/*
 * E1 -- the shim's own tests, against a fake TWAI driver.
 *
 * Every case here is a defect that shipped or nearly shipped in the four days
 * to 2026-09-25, and not one of them was visible to ../gen_inhibit_host, which
 * compiles the pure core and none of gen_inhibit.c. That is the argument for
 * this file existing: the host suite's blind spot is not a gap in coverage, it
 * is a whole component.
 *
 * Determinism: virtual time, one runnable thread at a time. See fake_twai.c.
 */
#include "fake_twai.h"
#include "gen_inhibit.h"
#include "gen_inhibit_core.h"

#include "driver/twai.h"
#include "esp_log.h"

#include "can.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int g_fail;

/*
 * A WALL-CLOCK BUDGET PER CASE, so a hang is a failure rather than a silence.
 *
 * Every other guard here is about virtual time. This one is not: it exists
 * because a case that never finishes produces no output at all, and the only
 * evidence is a process to kill. The budget is absurdly generous -- the whole
 * suite runs in well under a second -- so it cannot fire on a slow machine
 * without something being genuinely stuck.
 */
#define CASE_BUDGET_S 20

/*
 * A case may DECLARE a larger budget, for one reason only: it really does that much
 * work. The default is NOT raised, because it is the hang detector for every other
 * case and one long case is not a reason to blind the other thirty-two. Reset per
 * case, so a declaration cannot leak into the next one.
 */
static int g_case_budget_s = CASE_BUDGET_S;

static void case_budget(int seconds, const char *why)
{
    g_case_budget_s = seconds;
    printf("           budget: %d s -- %s\n", seconds, why);
}

static time_t g_case_start;
static const char *g_case_name = "";

static void case_begin(const char *name)
{
    g_case_name = name;
    g_case_start = time(NULL);
    printf("  %s\n", name);
}

/*
 * SPEC 12.4 (2026-10-03): the case declares what transmit timing it ran with. Printed
 * at the END, because a knob set mid-case has to appear even if the case cleared it
 * again -- ft_timing_config() keeps the high-water record for exactly that reason.
 */
static void print_timing_config(void)
{
    char cfg[256];
    ft_timing_config(cfg, (int)sizeof(cfg));
    printf("           timing: %s\n", cfg);
}

static void case_end(void)
{
    print_timing_config();
    const double took = difftime(time(NULL), g_case_start);
    /*
     * SAY WHAT IT COST, for anything that cost anything. The budget check below only
     * speaks when it is exceeded, so until now the cost of every case was invisible
     * and a stale figure in the spec ("case 33 costs roughly 55 s of host time",
     * against a measured 13.3 s for the WHOLE suite) could not be noticed by anyone
     * reading the output.
     */
    if (took >= 1.0)
    {
        printf("           took %.0f s of wall clock (budget %d s)\n",
               took, g_case_budget_s);
    }
    if (took > g_case_budget_s)
    {
        printf("    FAIL: %s took %.0f s of wall clock (budget %d s) -- "
               "something is spinning, not merely slow\n",
               g_case_name, took, g_case_budget_s);
        g_fail++;
    }
    g_case_budget_s = CASE_BUDGET_S;
}

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        if (!(cond)) {                                                        \
            printf("    FAIL: ");                                             \
            printf(__VA_ARGS__);                                              \
            printf("\n          (%s, line %d)\n", #cond, __LINE__);           \
            g_fail++;                                                         \
        }                                                                     \
    } while (0)

/* The JSON is the shim's own report, and several checks are about it. */
static const char *stats(void)
{
    /*
     * BIGGER THAN THE PAGE, deliberately -- 1761 bytes as of the scheduler
     * switchover. At 1600 this window cut the scheduler block off, and every
     * json_u32() of a key inside it returned "absent" while looking exactly like
     * a zero-or-more answer. This is the TEST's view and is not what the device
     * passes; case 26 is what checks the handler's buffer.
     */
    static char buf[4096];
    gen_inhibit_get_stats_json(buf, sizeof(buf));
    return buf;
}

static bool json_has(const char *needle)
{
    return strstr(stats(), needle) != NULL;
}

static uint32_t json_u32(const char *key)
{
    const char *p = strstr(stats(), key);
    if (!p) return 0xFFFFFFFFu;
    p += strlen(key);
    return (uint32_t)strtoul(p, NULL, 10);
}

/*
 * A counter that MUST be on the page. json_u32() returns 0xFFFFFFFF for a key it
 * cannot find, which satisfies every `>=` assertion ever written against it -- so
 * a missing counter reads as a large one and the test passes. Asserting presence
 * separately, with its own message, is the difference between a check and a
 * decoration.
 */
static uint32_t json_need(const char *key)
{
    const uint32_t v = json_u32(key);
    CHECK(v != 0xFFFFFFFFu,
          "the status page has no %s, so every threshold test on it would pass "
          "for the wrong reason", key);
    return v;
}

/* ---------------------------------------------------------- the fixture -- */

static const uint8_t VCM[6] = { 0x08, 0x00, 0x80, 0xFF, 0x7F, 0x00 };

/* The diag page IDs, for cases that need to know one is in flight. */
static const uint32_t DIAG[] = { 0x7F1, 0x7F2, 0x7F3, 0x7F8 };

static void feed(uint32_t id, const uint8_t *d, uint8_t dlc, int64_t budget)
{
    ft_deliver(id, d, dlc);
    ft_run(budget);
}

/*
 * Keep the whole interlock set alive for `us` of virtual time.
 *
 * WITHOUT THIS A CASE SILENTLY STOPS TESTING. The first version of case 1 fed
 * one 0x051 and then ran 1.6 s of silence to let diag frames go out. The
 * device aborted on bus-loss at 1.015 s, stopped transmitting, and the
 * assertion "tx_ok did not move" passed -- for a reason that had nothing to do
 * with what the case was about. A check that cannot fail is not a check.
 */
static void keep_alive(int64_t us, uint8_t *ctr)
{
    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };
    const int64_t end = ft_now() + us;
    uint8_t cmd[6];
    while (ft_now() < end)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)((*ctr)++ & 0x0F);
        feed(0x051, cmd, 6, 10000);
        feed(0x592, KEY, 8, 10000);
        feed(0x440, CONT, 8, 10000);
        feed(0x411, SOC, 8, 10000);
        feed(0x617, FLT, 8, 10000);
        feed(0x639, SHF, 8, 10000);
    }
}

/* Bring the device live: the whole interlock set, then a few VCM frames. */
static void go_live(void)
{
    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };      /* 50.00 % */
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };
    uint8_t cmd[6];

    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    for (int i = 0; i < 12; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(i & 0x0F);
        feed(0x051, cmd, 6, 5000);
        feed(0x592, KEY, 8, 5000);
        feed(0x440, CONT, 8, 5000);
        feed(0x411, SOC, 8, 5000);
        feed(0x617, FLT, 8, 5000);
        feed(0x639, SHF, 8, 5000);
    }
}

/*
 * THE WIRE INVARIANT, drained rather than polled.
 *
 * The fake checks every frame handed to twai_transmit() and appends a sentence
 * for each violation; this reports the ones not yet reported and counts them as
 * failures. Draining rather than comparing a total means each violation is
 * named once, at the step where it happened, with the virtual time -- and a
 * case that ends early still has its violations reported by teardown().
 */
static int g_wv_reported;

static void drain_wire_violations(void)
{
    while (g_wv_reported < ft_wire_violations())
    {
        printf("    FAIL: at %lld us the driver was handed a frame that must "
               "never reach the wire:\n           %s\n",
               (long long)ft_now(), ft_wire_violation(g_wv_reported));
        g_fail++;
        g_wv_reported++;
    }
}

/*
 * The invariant as a STEP HOOK rather than an end-of-case assertion. See
 * ft_set_step_hook(): 632af32's bug opens a window that closes again, so a
 * check that only runs at the end sees nothing wrong.
 */
/* Shared with check_tx_ok_invariant(): the notice is printed once,
 * not once per worker iteration. */
static bool g_txok_unbounded_warned;

static void invariant_hook(void)
{
    drain_wire_violations();

    /*
     * THE COUNT CEILING IS BOUNDED BY THE FRAME LOGS and the wire ORDER is not, so
     * only the comparison below is skipped once they saturate. See
     * check_tx_ok_invariant(): past SENTMAX the ceiling freezes while tx_ok keeps
     * rising, and this hook then reported a violation on every iteration of the long
     * arm -- 10209 against a frozen 3994. drain_wire_violations() above still runs.
     */
    if (ft_logs_saturated())
    {
        if (!g_txok_unbounded_warned)
        {
            g_txok_unbounded_warned = true;
            printf("    NOTE: the frame logs are full (%d entries), so the tx_ok "
                   "ceiling is a lower bound and that invariant is no longer "
                   "evaluated from here. Counter-based assertions still hold.\n",
                   ft_wire_count());
        }
        return;
    }

    const uint32_t claimed = json_u32("\"tx_ok\":");

    /*
     * tx_ok COUNTS TWO DIFFERENT THINGS, deliberately, and the invariant has to
     * know it. Spec 7 trip 7 made the inhibit frame count only on COMPLETION,
     * because "handed to the driver" is not "on the wire". The RESPOND probe is
     * still counted at QUEUE time: it is a measurement, not a command, so a late
     * one costs a data point rather than a safety property, and keeping it there
     * leaves every timing figure recorded before that change comparable.
     *
     * So the ceiling is "inhibit frames completed, plus probes queued". Using
     * only the first reported every RESPOND probe as a violation, which is how
     * case 14 failed when it was written -- the invariant was right about
     * INHIBIT and simply did not describe RESPOND.
     */
    const int ceiling = ft_wire_count_id(0x051) + ft_sent_count_id(0x7F0);
    if (claimed != 0xFFFFFFFFu && claimed > (uint32_t)ceiling)
    {
        printf("    FAIL: at %lld us tx_ok=%u exceeds the %d frames that can "
               "have been counted (%d inhibit completions + %d probes queued)\n",
               (long long)ft_now(), claimed, ceiling,
               ft_wire_count_id(0x051), ft_sent_count_id(0x7F0));
        g_fail++;
    }
}

static void setup(void)
{
    ft_reset();
    g_wv_reported = 0;
    ft_set_step_hook(invariant_hook);
    ml_clear();
    gen_inhibit_init();
    ft_start_worker();
    ft_run(1000);
}

/*
 * THE INVARIANT EVERY CASE IS CHECKED AGAINST, whatever else it is about.
 *
 * tx_ok means "frames that completed on the wire" (spec 7 trip 7). So it can
 * never exceed the number of 0x051 frames the driver actually completed. Every
 * mis-crediting bug this component has had -- a diag frame's latched
 * TWAI_ALERT_TX_SUCCESS credited to an inhibit, a late completion carried to
 * the next frame, a queue-time credit -- violates exactly this, whatever the
 * mechanism.
 *
 * Stating it this way rather than replaying the historical code is deliberate.
 * A case that reproduces one defect's exact shape passes the moment the shape
 * changes; this one fails for any future way of getting the same thing wrong.
 */
/*
 * THE SUITE'S ALWAYS-PROPERTY: tx_ok can never exceed the frames that actually went.
 *
 * IT IS BOUNDED BY THE FRAME LOGS, and past SENTMAX those stop recording silently --
 * so the ceiling freezes while tx_ok keeps rising and the check reports a violation
 * that did not happen. The long-arm case (32) hit exactly that: tx_ok 10209 against a
 * frozen ceiling of 3994, reported from the step hook on every iteration. A confident
 * false failure is the most expensive kind here, because the natural response is to go
 * looking in the firmware.
 *
 * So it stops asserting once the logs saturate, and SAYS SO ONCE. It does not go
 * quiet: a check that can no longer be evaluated has to announce that, or its silence
 * reads as evidence that it held.
 */
static void check_tx_ok_invariant(const char *where)
{
    if (ft_logs_saturated())
    {
        if (!g_txok_unbounded_warned)
        {
            g_txok_unbounded_warned = true;
            printf("    NOTE: %s: the frame logs are full (%d entries), so the "
                   "tx_ok ceiling is a lower bound and this invariant is no longer "
                   "evaluated. Counter-based assertions still hold.\n",
                   where, ft_wire_count());
        }
        return;
    }
    const uint32_t claimed = json_u32("\"tx_ok\":");
    const int on_wire = ft_wire_count_id(0x051) + ft_sent_count_id(0x7F0);
    CHECK(claimed <= (uint32_t)on_wire,
          "%s: tx_ok=%u but only %d frames can have been counted",
          where, claimed, on_wire);
}

static void teardown(void)
{
    /*
     * BEFORE the mode change, so that a violation belonging to the case is
     * reported against the case rather than against the shutdown.
     */
    drain_wire_violations();

    /*
     * NO MORE RESPONSES WENT OUT THAN COMMANDS CAME IN, checked in every case.
     *
     * The inhibit frame is reactive: spec 4 emits one in answer to a received
     * 0x051 and never on its own schedule, so the count queued can never exceed
     * the count delivered. Stating it as a count is what catches a
     * self-reception loop BY ITS BEHAVIOUR rather than by the flag that caused
     * it -- with self = 1 the worker answers its own frame, which it then
     * receives and answers again, and this ratio runs away whatever set the
     * bit. ft_delivered_count_id() counts only what the test delivered, so the
     * loop cannot raise its own ceiling.
     */
    const int out = ft_sent_count_id(0x051);
    const int in  = ft_delivered_count_id(0x051);
    CHECK(out <= in,
          "%d inhibit frames were queued in answer to %d commands -- the "
          "inhibit is reactive, so something is generating frames of its own",
          out, in);
    CHECK(ft_self_received() == 0,
          "the controller delivered %d of our own frames back to us: on this "
          "bus our 0x051 is indistinguishable from the VCM's, so receiving it "
          "means answering it", ft_self_received());

    /*
     * Spec 3: OFF is off. Nothing may reach the bus after this, diag pages
     * included -- the device is not merely not-inhibiting, it is not talking.
     * Declared here rather than in a case of its own so that EVERY case ends
     * by asserting it, which is the only way it covers the paths no case walks.
     */
    gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
    ft_allow_ids(NULL, -1, "OFF puts nothing at all on the bus (spec 3)");
    ft_run(100000);
    drain_wire_violations();
    /*
     * SPEC 8, CHECKED IN EVERY CASE. The driver must never be torn down while a
     * thread is inside twai_receive() -- on the device that frees the memory the
     * blocked call is using, and gen_inhibit_quiesce() exists to make it
     * impossible. The model cannot crash, so the fake counts the violations
     * instead; see ft_unsafe_teardowns(). Removing the quiesce wait left this
     * suite green until the fake learned to watch for it.
     */
    CHECK(ft_unsafe_teardowns() == 0,
          "%d teardown(s) happened while a thread was inside twai_receive() -- "
          "the spec 8 handshake did not hold", ft_unsafe_teardowns());
    ft_stop_worker();
}

/* ---------------------------------------------------------------- cases -- */

/*
 * CASE 1 -- a diag frame completes while an inhibit is in flight.
 *
 * THE SHIPPED BUG (dc571cb). Alerts are latched bits shared by every frame.
 * The shim read them only after queueing an inhibit and credited whatever was
 * pending, so a diag frame's TWAI_ALERT_TX_SUCCESS completed an inhibit that
 * had not been sent -- masking exactly the "inhibit queued behind a diag
 * frame" case spec 7 trip 7 exists to catch.
 */
static void case_diag_completion_not_credited(void)
{
    case_begin("case 1: a diag frame's completion must not credit an inhibit");
    setup();
    go_live();

    const uint32_t tx_before = json_u32("\"tx_ok\":");
    const int wire_before = ft_wire_count_id(0x051);

    /*
     * The next INHIBIT frame never completes -- stalled by ID, because
     * ft_stall_next() marks whichever frame is queued next and gi_tick() may
     * emit a diag page first. The first version of this case stalled the diag
     * by accident and proved nothing.
     */
    ft_stall_id(0x051, 1);

    /* The bus stays alive, so the device stays live and keeps deciding. */
    uint8_t ctr = 0x0C;
    keep_alive(1500000, &ctr);

    /*
     * The guard first: if the stall did not take, everything below is true for
     * the wrong reason. Note what CANNOT be asserted here -- that a diag frame
     * completed during the window. The controller has ONE transmit buffer and
     * the driver queues FIFO, so a stalled frame at the head blocks everything
     * behind it including the diag. An earlier version of this case demanded a
     * diag completion and failed, correctly: it was describing something the
     * hardware cannot do.
     */
    /*
     * RE-POINTED 2026-09-27 with the transmit scheduler, and the case's PURPOSE is
     * unchanged: a diag frame's completion must not credit an inhibit.
     *
     * What changed is what a stall does. It used to wedge the transmit path -- the
     * stalled frame sat at the head of the driver's FIFO for ever and nothing behind
     * it could go out -- so the case could assert that NOTHING reached the wire and
     * tx_ok never moved. Spec 5.2 item 5 replaces that: the stalled frame is
     * WITHDRAWN at the VCM's next 0x051, counted as a skip, and the next command is
     * answered normally.
     *
     * So the stall is now a skip, and the property that matters is stated directly
     * instead of via a frozen counter: tx_ok moves by EXACTLY the number of inhibit
     * frames that reached the wire. The stalled one did not reach it and must not be
     * credited, whatever completed in the controller -- which is the item 5 hazard
     * the 2026-09-27 measurement found on hardware, where an aborted frame is
     * reported identically to a transmitted one.
     *
     * WHAT THIS CASE STOPPED COVERING: "a stall wedges the transmit path". It cannot,
     * by design. The withdraw-and-skip behaviour is covered here and in
     * test/gen_inhibit_sched's "D1: a queued inhibit is purged at the deadline" and
     * "R8: a withdrawn HELD inhibit never comes back".
     */
    CHECK(json_u32("\"skipped\":") >= 1,
          "the stalled inhibit was not withdrawn and counted as a skip: %s",
          stats());

    const uint32_t tx_after = json_u32("\"tx_ok\":");
    const int wire_delta = ft_wire_count_id(0x051) - wire_before;
    CHECK((int)(tx_after - tx_before) == wire_delta,
          "tx_ok moved by %d while %d inhibit frames reached the wire -- a frame "
          "that never went out has been credited, which is the spec 5.2 item 5 "
          "hazard: %s",
          (int)(tx_after - tx_before), wire_delta, stats());
    check_tx_ok_invariant("case 1");
    teardown();
    case_end();
}

/*
 * CASE 2 -- a late completion must not credit the NEXT inhibit.
 *
 * Left unfixed this is self-sustaining: every frame is credited by its
 * predecessor's latched alert, TX_LATE can never fire again, and the response
 * histogram silently reverts to measuring queue time.
 */
static void case_late_completion_not_carried(void)
{
    case_begin("case 2: a late completion must not credit the next inhibit");
    setup();
    go_live();

    /* A very slow controller: completion takes longer than a VCM gap. */
    ft_set_air_time(30000);
    uint8_t cmd[6];
    for (int i = 0; i < 3; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(0x0C + i);
        feed(0x051, cmd, 6, 20000);
    }
    ft_run(200000);

    /*
     * Whatever the outcome, tx_ok may never exceed the number of frames that
     * actually completed on the wire. That is the invariant the latched-alert
     * bug broke, and it holds however the timing falls.
     */
    int completed = 0;
    for (int i = 0; i < ft_wire_count(); i++)
    {
        if (ft_wire(i)->id == 0x051) completed++;
    }
    CHECK(json_u32("\"tx_ok\":") <= (uint32_t)completed,
          "tx_ok=%u but only %d inhibit frames completed on the wire",
          json_u32("\"tx_ok\":"), completed);
    check_tx_ok_invariant("case 2");
    teardown();
    case_end();
}

/*
 * CASE 3 -- a completion later than any bounded read must still be seen.
 *
 * dc571cb waited 1 ms for the alert. A completion at 1.2 ms on a loaded bus
 * went unobserved, leaving the frame pending and producing a FALSE TX_LATE on
 * a frame that had finished in good time. The fix polls every loop iteration
 * instead, so there is no window to fall outside.
 */
static void case_slow_completion_is_not_late(void)
{
    case_begin("case 3: a completion at 1.2 ms is observed, not called late");
    setup();
    go_live();

    ft_set_air_time(1200);          /* later than the old 1 ms read */
    uint8_t cmd[6];
    for (int i = 0; i < 4; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(0x0C + i);
        feed(0x051, cmd, 6, 4690);  /* the VCM's tightest observed gap */
    }
    ft_run(50000);

    CHECK(!json_has("still unsent when the VCM's next 0x051 arrived"),
          "a frame completing at 1.2 ms was reported as having lost the "
          "counter race");
    check_tx_ok_invariant("case 3");
    teardown();
    case_end();
}

/*
 * CASE 4 -- a diag frame already in the buffer when the inhibit is queued.
 *
 * The diag-first ordering, which 632af32's pre-queue drain could not see
 * because the diag had not completed yet. It is the likelier order on the
 * truck: the diag IDs are the lowest priority on the bus, so one waits for an
 * idle gap while the inhibit queues behind it. This is what forced the move to
 * msgs_to_tx == 0.
 */
static void case_inhibit_behind_diag(void)
{
    case_begin("case 4: an inhibit queued behind an unfinished foreign frame");
    setup();
    go_live();

    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };

    /*
     * THE ORDERING HAS TO BE BUILT, NOT HOPED FOR, and the first version of
     * this case hoped. It set an 8 ms air time, fed one 0x051 and trusted that
     * a diag page happened to be in the buffer -- so when the reviewing session
     * reinstated 632af32's alert-based completion, this case stayed green. The
     * core's diag deferral means a diag is queued only while NOTHING of ours is
     * outstanding, so the diag-first order has to be constructed deliberately:
     *
     *   1. a long air time, so whatever is queued stays in flight;
     *   2. hold the interlocks up WITHOUT 0x051 until a diag page is queued;
     *   3. only then feed the 0x051, which queues the inhibit behind it.
     *
     * When the diag completes it latches TWAI_ALERT_TX_SUCCESS, and that is the
     * bit 632af32 credited to the inhibit -- which is still in flight.
     */
    /*
     * NO TIMING TO TUNE. Earlier versions of this case tried to arrange for the
     * diag to complete inside a window while the inhibit was in flight, and it
     * took four attempts -- each one a plausible-looking arrangement that let
     * the alert-based mutation survive, and one of which broke again the moment
     * the fake's receive model was made faithful. A case whose sensitivity
     * depends on three durations lining up is a case that will silently stop
     * testing.
     *
     * So: the inhibit is STALLED -- it never completes, whatever the clock does
     * -- while the diag ahead of it completes normally. The diag's
     * TWAI_ALERT_TX_SUCCESS is then latched with our frame demonstrably
     * unsent, which is the whole condition, and it holds for any air time.
     */
    ft_set_air_time(200000);     /* the diag stays in flight for 200 ms */
    ft_stall_id(0x051, 1);       /* ours never completes, whatever the clock does */

    /*
     * ONE FRAME PER STEP, checking after each, so the 0x051 goes in within a
     * single step of the diag being queued.
     *
     * The previous version fed five interlock signals per pass and only then
     * looked, so up to 100 ms of virtual time elapsed between the diag being
     * queued and the 0x051 arriving -- longer than the diag's own 30 ms air
     * time. The diag had therefore already completed and the per-iteration
     * poll had consumed its alert, leaving nothing to mis-credit. That is the
     * third reason the alert-based mutation survived this case, and each time
     * the failure looked like the firmware being right.
     */
    (void)KEY; (void)CONT; (void)SOC; (void)FLT; (void)SHF;

    /*
     * THE VEHICLE CHANGED ON 2026-09-26, and the reason is that the firmware
     * got better.
     *
     * This case used to hold the interlocks up without 0x051 until a diag page
     * happened to be queued, then feed the 0x051 so the inhibit went in behind
     * it. Spec 5.1 item 4 makes that ordering IMPOSSIBLE while live: a diag page
     * is now queued only in the millisecond after an inhibit completes, which is
     * when the VCM's next 0x051 is at least 4.69 ms away. The old construction
     * ran for 900 ms, never saw a diag, and aborted on bus-loss -- correctly,
     * and while proving nothing.
     *
     * A FOREIGN FRAME IS THE BETTER VEHICLE ANYWAY, and it is the purer form of
     * the same hazard. TWAI_ALERT_TX_SUCCESS is a latched bit shared by every
     * frame the controller sends, including frames this component never emitted
     * -- an SLCAN or MQTT path, which is what spec 3.2 is about. If a foreign
     * frame's completion can credit our outstanding inhibit, the attribution is
     * broken in the most general way there is, and a diag page was only ever a
     * convenient instance of it.
     *
     * So: a foreign frame occupies the single TX buffer, our inhibit is queued
     * behind it and stalled so it cannot complete, and the foreign frame's
     * alert latches with ours demonstrably unsent. Same condition, no dependence
     * on diag scheduling, and it keeps tx_queued_behind measurable -- which
     * mutation N2 exists to check.
     */
    static const uint8_t FOREIGN[8] = { 0xDE, 0xAD, 0xBE, 0xEF, 0, 0, 0, 0 };
    CHECK(ft_foreign_transmit(0x123, FOREIGN, 8),
          "the foreign frame was not accepted, so nothing occupies the TX "
          "buffer and this case proves nothing");
    CHECK(ft_wire_count_id(0x123) == 0,
          "the foreign frame completed before the 0x051 was fed, so its alert "
          "was consumed while nothing of ours was outstanding");

    const uint32_t before = json_u32("\"tx_ok\":");
    const int wire_before = ft_wire_count_id(0x051);

    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = 0x0C;
    feed(0x051, cmd, 6, 1000);      /* queues BEHIND the in-flight foreign frame */

    /*
     * Let the foreign frame complete -- latching its alert -- with our frame
     * stalled behind it. The step hook watches the invariant throughout; these
     * check the same thing where it matters.
     */
    ft_run(500000);
    CHECK(ft_wire_count_id(0x051) == wire_before,
          "the stall did not take: our frame completed, so the diag's "
          "completion was not observed while ours was outstanding");
    CHECK(json_u32("\"tx_ok\":") == before,
          "tx_ok moved %u -> %u on a FOREIGN frame's completion while our frame "
          "was still queued behind it", before, json_u32("\"tx_ok\":"));
    /*
     * INVERTED 2026-09-27 with the transmit scheduler, and the inversion is the point
     * rather than a concession.
     *
     * This used to require tx_queued_behind > 0. Spec 5's hazard was an inhibit going
     * into the controller BEHIND something of ours that had not finished, and a silent
     * zero would have read as "hazard absent" -- the worst way for a measurement to
     * fail. Spec 5.2 item 3 makes that state unreachable: at most one frame is handed
     * to the driver at a time. So the counter is now STRUCTURALLY ZERO, and a non-zero
     * value would mean the scheduler had handed over a second frame.
     *
     * The same stimulus therefore pins the opposite fact, and item 10's full-replay
     * case checks the same property on hardware.
     *
     * WHAT THIS CASE STOPPED COVERING: that the hazard is measurable. It is no longer
     * producible, which is better than measured. That the inhibit really does go
     * first is covered by test/gen_inhibit_sched's "preempt: telemetry awaiting,
     * inhibit wins".
     */
    CHECK(json_u32("\"tx_queued_behind\":") == 0,
          "tx_queued_behind is %u: the scheduler handed over a second frame while "
          "one was still in the controller, which spec 5.2 item 3 forbids",
          json_u32("\"tx_queued_behind\":"));

    ft_run(600000);
    check_tx_ok_invariant("case 4");
    teardown();
    case_end();
}

/*
 * CASE 5 -- another task must not be able to queue behind the inhibit.
 *
 * Spec 3.2 (A3), and a correctness requirement for trip 7: msgs_to_tx == 0
 * proves OUR frame completed only because nothing else can be queued behind
 * it. can_send() must refuse while the inhibitor owns the bus, and must say so
 * rather than dropping the frame silently.
 */
static void case_foreign_transmit_refused(void)
{
    case_begin("case 5: can_send() refuses while the inhibitor owns the bus");
    setup();
    go_live();

    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = 0x051;
    m.data_length_code = 6;
    m.data[1] = 0x34;               /* a NON-ZERO torque, which is the point */
    m.data[2] = 0x92;

    const int sent_before = ft_sent_count();
    const esp_err_t err = can_send(&m, 0);

    CHECK(err != ESP_OK, "can_send() accepted a frame while the inhibitor "
                         "owned the bus");
    CHECK(ft_sent_count() == sent_before,
          "a foreign frame reached the driver: %d -> %d",
          sent_before, ft_sent_count());
    CHECK(ml_count("can_send refused") > 0,
          "the refusal was not logged (spec 3.2 requires it)");
    teardown();
    case_end();
}

/*
 * CASE 6 -- a self-imposed OFF names itself (spec 7, 8, 11).
 *
 * In OFF the worker stops receiving, so the state cannot clear itself and no
 * diag goes out: without a reason it is indistinguishable from a disarm
 * somebody asked for, which is the wrong thing for a human deciding whether to
 * re-arm or go and look at the wiring.
 */
static void case_self_off_names_itself(void)
{
    case_begin("case 6: a self-imposed OFF says why");
    setup();
    go_live();

    CHECK(json_has("\"self_off\":\"\""),
          "self_off was already set on a healthy device: %s", stats());

    /* The driver goes out from under the worker. */
    ft_rx_error_next(40);
    for (int i = 0; i < 60; i++) ft_run(200000);

    CHECK(json_has("receive errors"),
          "the receive-error self-disarm did not name itself: %s", stats());

    /* Arming again answers the question, so the reason must clear. */
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    ft_run(10000);
    CHECK(json_has("\"self_off\":\"\""),
          "self_off survived a re-arm and now describes a previous OFF: %s",
          stats());
    teardown();
    case_end();
}

/*
 * CASE 7 -- PASSIVE puts no 0x051 on the wire, through the real transmit path.
 *
 * passive_diff.py proves this about the CORE's emit list. This proves it about
 * the driver: whatever the core emitted, nothing with the VCM's ID reached
 * twai_transmit().
 */
static void case_passive_emits_nothing(void)
{
    case_begin("case 7: PASSIVE reaches the driver with no 0x051");
    setup();

    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };
    uint8_t cmd[6];

    /*
     * Spec 3.1: PASSIVE decides everything and transmits none of it. The diag
     * pages still go out -- that is what makes it useful as a shadow mode --
     * so the allowed set is the diag IDs and nothing else.
     *
     * The loop below already counts 0x051 leaks, which is the property this
     * case is named for. Declaring the set as well covers what the loop does
     * not look at: a frame on some OTHER id, emitted from a path this
     * scenario happens not to walk, at a moment nothing is counting.
     */
    static const uint32_t passive_ok[] = { 0x7F1, 0x7F2, 0x7F3, 0x7F8 };
    ft_allow_ids(passive_ok, (int)(sizeof(passive_ok) / sizeof(passive_ok[0])),
                 "PASSIVE decides everything and transmits none of it "
                 "(spec 3.1) -- only the diag pages may go out");

    gen_inhibit_set_mode(GEN_INHIBIT_PASSIVE, 500);
    for (int i = 0; i < 20; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(i & 0x0F);
        feed(0x051, cmd, 6, 5000);
        feed(0x592, KEY, 8, 5000);
        feed(0x440, CONT, 8, 5000);
        feed(0x411, SOC, 8, 5000);
        feed(0x617, FLT, 8, 5000);
        feed(0x639, SHF, 8, 5000);
    }

    int leaked = 0;
    for (int i = 0; i < ft_sent_count(); i++)
    {
        if (ft_sent(i)->id == 0x051) leaked++;
    }
    CHECK(leaked == 0, "PASSIVE queued %d 0x051 frames into the driver", leaked);
    CHECK(json_u32("\"would_tx\":") > 0,
          "PASSIVE counted no would-transmits, so it never went live and this "
          "case proved nothing: %s", stats());
    CHECK(json_u32("\"emit_refused\":") == 0,
          "the emit() tripwire fired: a code path tried to transmit a real "
          "0x051 from PASSIVE");
    check_tx_ok_invariant("case 7");
    teardown();
    case_end();
}


/*
 * CASE 8 -- the next 0x051 lands while the inhibit is still behind the diag.
 *
 * Case 4 proves the inhibit is not credited early. This proves the other half:
 * what the early credit MASKS. If the inhibit is credited when the diag
 * completes, the frame is no longer outstanding and TX_LATE cannot fire -- so
 * the device believes it inhibited a slot in which the VCM's torque is what the
 * inverter acted on. The reviewing session noted that case 4 never let a second
 * 0x051 arrive, so it never exercised the consequence.
 */
static void case_late_behind_diag_counted(void)
{
    case_begin("case 8: the VCM's next 0x051 arrives with ours still queued "
               "-- counted, NOT a trip (spec 7 trip 7, amended)");
    setup();
    go_live();

    /*
     * TWO 0x051 FRAMES 1 ms APART, with 50 ms of air time. keep_alive() is no
     * use here: it interleaves the other interlock signals, each advancing
     * virtual time, so 100 ms passes between consecutive 0x051 frames and any
     * plausible air time completes in the gap. Two attempts failed that way --
     * 9 ms and then 45 ms of air time -- each reporting a firmware defect while
     * the firmware was right. The condition has to be built, not hoped for.
     */
    ft_set_air_time(50000);
    uint8_t cmd[6];
    for (int i = 0; i < 2; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(0x0C + i);
        feed(0x051, cmd, 6, 1000);      /* 1 ms apart, 50 ms to complete */
    }
    ft_run(5000);

    /*
     * AMENDED 2026-09-27 with spec 7 trip 7. This used to require a TX_LATE trip
     * here. It must NOT trip now: the scheduler withdraws such a frame and reports
     * a skip, and only three within one second end the inhibit. This shim drives
     * gen_inhibit.c, which is not yet wired to the scheduler, so nothing here
     * produces a skip -- and the correct behaviour of the code as it stands is no
     * trip at all.
     *
     * What the shim CAN still see of the hazard is tx_queued_behind, the count
     * spec 5 keeps for exactly this condition, and it is asserted instead of the
     * trip. The consequence this case used to cover -- an early credit making the
     * frame look complete -- now lives in the scheduler's cases and returns here
     * as a 5.2 item 10 preemption case once the scheduler is wired in.
     */
    CHECK(!json_has("still unsent when the VCM's next 0x051 arrived"),
          "the retired TX_LATE trip fired; a late frame is a skip now "
          "(spec 7 trip 7, amended): %s", stats());
    CHECK(!json_has("\"abort_latched\":true"),
          "a frame outstanding at the next 0x051 must not latch an abort on its "
          "own: %s", stats());
    /*
     * AMENDED AGAIN 2026-09-27, after the switchover. The first amendment asserted
     * tx_queued_behind == 1, which was right while dispatch_emits() still handed
     * frames to the driver itself. The scheduler makes that impossible (item 3), so
     * the honest assertion is that the frame was WITHDRAWN and counted as a skip --
     * the behaviour spec 5.2 item 5 specifies for exactly this stimulus.
     */
    CHECK(json_u32("\"tx_queued_behind\":") == 0,
          "tx_queued_behind is %u; item 3 permits one frame in the controller: %s",
          json_u32("\"tx_queued_behind\":"), stats());
    CHECK(json_u32("\"skipped\":") >= 1,
          "the outstanding inhibit was not withdrawn and counted as a skip at the "
          "VCM's next 0x051: %s", stats());
    check_tx_ok_invariant("case 8");
    teardown();
    case_end();
}

/*
 * CASE 9 -- the diag page waits while an inhibit is outstanding.
 *
 * Nothing asserted this before, so removing the core's deferral survived as a
 * mutation. It matters twice: spec 10 (the lowest-priority diag frames must not
 * delay the inhibit) and, since completion is msgs_to_tx == 0, a diag queued
 * BEHIND the inhibit would hold the count above zero after ours had gone and
 * produce a false TX_LATE on a slow bus.
 */
static void case_diag_defers_to_pending(void)
{
    case_begin("case 9: no diag frame is queued while an inhibit is outstanding");
    setup();
    go_live();

    /*
     * Stall our frame at the head, then hold the OTHER interlocks up without
     * any further 0x051, so the frame stays outstanding while diag falls due
     * at 300 ms.
     *
     * NO MORE 0x051, and that is the whole shape of the case. The first version
     * used keep_alive(), which kept feeding 0x051 -- the next one tripped
     * TX_LATE, that cleared tx_pending, and the five diag frames it then
     * counted were all queued AFTER the abort, when deferring was neither
     * required nor happening. The window has to be bounded by the abort, not
     * span it.
     *
     * 400 ms: past the 300 ms diag period and inside the 500 ms freshness
     * window, so the bus-loss trip has not fired either.
     */
    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };

    /*
     * A LONG AIR TIME, NOT ft_stall_id(), and the difference is the whole case.
     *
     * This used to call ft_stall_id(0x051, 1) and feed one frame, and the
     * precondition check added below showed the result: 11 queued, 11 completed,
     * nothing ever outstanding. The case passed for four weeks while exercising
     * nothing, which is why the reviewing session found M2 -- the core's
     * deferral deleted -- surviving the entire suite.
     *
     * Case 8 already recorded the mechanism that works and the reason: the
     * interlock feeding below advances virtual time by 100 ms per round, so any
     * short air time completes in the gap. 600 ms exceeds the 400 ms window, so
     * the frame is still at the head when the window ends.
     */
    ft_set_air_time(600000);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = 0x0C;
    feed(0x051, cmd, 6, 5000);

    /*
     * GIVE THE WORKER A TICK TO DISPATCH before sampling anything. The feed
     * above advances 5 ms, which does not always get the worker as far as
     * twai_transmit() -- the frame is accepted by the core and counted in its
     * `response` histogram while no queue attempt has been made yet, so a
     * precondition sampled here reads "nothing outstanding" for a frame that is
     * about to be queued. With the 600 ms air time above it stays outstanding
     * once it is queued, so waiting costs nothing.
     */
    ft_run(20000);

    const int q_before = ft_sent_count();

    /*
     * THE PRECONDITION, CHECKED RATHER THAN DESCRIBED. Everything above is
     * arranged so an inhibit frame is sitting at the head of the queue,
     * uncompleted, while diag falls due -- and until 2026-09-26 nothing here
     * confirmed that it was. If the frame is not outstanding, "no diag was
     * queued" is true for an uninteresting reason and the case passes without
     * exercising the rule. That is the shape the reviewing session found M2
     * surviving through.
     */
    CHECK(ft_sent_count_id(0x051) > ft_wire_count_id(0x051),
          "no inhibit is outstanding (%d queued, %d completed), so the "
          "deferral has nothing to defer to and this case proves nothing",
          ft_sent_count_id(0x051), ft_wire_count_id(0x051));

    const int64_t end = ft_now() + 400000;
    while (ft_now() < end)
    {
        feed(0x592, KEY, 8, 20000);
        feed(0x440, CONT, 8, 20000);
        feed(0x411, SOC, 8, 20000);
        feed(0x617, FLT, 8, 20000);
        feed(0x639, SHF, 8, 20000);
    }

    int diag_queued = 0;
    for (int i = q_before; i < ft_sent_count(); i++)
    {
        const uint32_t id = ft_sent(i)->id;
        if (id == 0x7F1 || id == 0x7F2 || id == 0x7F3 || id == 0x7F8) diag_queued++;
    }
    /* The guard: if nothing was outstanding there was nothing to defer to. */
    CHECK(json_has("\"abort_reason\":\"\""),
          "the device aborted during the window, so the deferral was not what "
          "was being tested: %s", stats());
    CHECK(diag_queued == 0,
          "%d diag frames were queued while an inhibit was outstanding -- spec "
          "10, and they would hold msgs_to_tx above zero after ours had gone",
          diag_queued);
    teardown();
    case_end();
}

/*
 * CASE 10 -- a forced quiesce names itself (spec 8, 11).
 *
 * Case 6 drives only the receive-error path, so removing the quiesce reason
 * survived as a mutation. can_disable() from another task is the realistic
 * trigger after A3 -- the inhibitor's own HTTP setter switching modes with the
 * bus up -- and it forces mode OFF, in which the worker stops receiving and
 * nothing more goes out on the wire.
 */
static void case_quiesce_names_itself(void)
{
    case_begin("case 10: a forced quiesce says why");
    setup();
    go_live();

    CHECK(json_has("\"self_off\":\"\""),
          "self_off was set on a healthy armed device: %s", stats());

    /* Another task takes the bus down. can_disable() quiesces us first. */
    can_disable();
    ft_run(10000);

    CHECK(json_has("forced quiesce"),
          "a forced quiesce did not name itself: %s", stats());
    teardown();
    case_end();
}


/*
 * CASE 11 -- OBSERVE must be hardware listen-only (spec 3, spec 13 item 1).
 *
 * The TWAI API exposes no read-back of the controller's mode, so what
 * gen_inhibit.c checks is the value handed to twai_driver_install() -- and the
 * fake records exactly that. The hardware test (12.4: the WiCAN must be the
 * only possible ACKer) is still needed and this does not replace it; what this
 * catches is the forcing LOGIC being wrong, which is host-testable and was not
 * tested.
 */
static void case_observe_is_listen_only(void)
{
    case_begin("case 11: OBSERVE installs the driver listen-only");
    setup();

    /*
     * Spec 3: OBSERVE is hardware listen-only, so the allowed set is EMPTY --
     * not "no 0x051", nothing at all, diag pages included. The fake enforces
     * it on every frame, which covers the paths this case does not walk.
     */
    ft_allow_ids(NULL, -1, "OBSERVE is hardware listen-only and may queue "
                           "nothing at all");

    gen_inhibit_set_mode(GEN_INHIBIT_OBSERVE, 500);
    ft_run(50000);
    CHECK(ft_installed_mode() == (int)TWAI_MODE_LISTEN_ONLY,
          "OBSERVE installed the driver in mode %d, not listen-only (%d)",
          ft_installed_mode(), (int)TWAI_MODE_LISTEN_ONLY);

    /* And nothing may be queued: in listen-only even diag cannot go out. */
    const int n = ft_sent_count();
    uint8_t cmd[6];
    for (int i = 0; i < 10; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)i;
        feed(0x051, cmd, 6, 20000);
    }
    CHECK(ft_sent_count() == n,
          "OBSERVE queued %d frames into the driver", ft_sent_count() - n);

    /* Leaving OBSERVE must restore a transmitting mode -- and may transmit. */
    ft_allow_ids(NULL, 0, "");
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    ft_run(50000);
    CHECK(ft_installed_mode() == (int)TWAI_MODE_NORMAL,
          "leaving OBSERVE left the driver in mode %d, not normal",
          ft_installed_mode());
    teardown();
    case_end();
}

/*
 * CASE 12 -- the probe offset limit (spec 3, spec 12.4).
 *
 * A probe scheduled beyond the VCM's shortest observed inter-frame gap
 * (4.69 ms) would land after the next genuine frame and make the measurement
 * meaningless, so anything above 4000 us is refused. Listed in 12.4 as a host
 * mock-HAL check; it had none.
 */
static void case_offset_limit(void)
{
    case_begin("case 12: an offset above 4000 us is refused");
    setup();

    CHECK(gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, 4000) == ESP_OK,
          "4000 us was refused, but it is the limit and must be accepted");
    CHECK(gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, 4001) != ESP_OK,
          "4001 us was accepted: a probe beyond the VCM's 4.69 ms gap would "
          "land after the next genuine frame");
    CHECK(gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, 40000) != ESP_OK,
          "40000 us was accepted");
    teardown();
    case_end();
}

/*
 * CASE 13 -- a driver that will not enable the TX alerts must not be armed.
 *
 * can.c installs with TWAI_ALERT_NONE, so arming turns on TX_SUCCESS/TX_FAILED
 * itself. If that fails and the arm proceeds anyway, nothing ever observes a
 * completion: every frame looks outstanding and the first VCM frame after it
 * aborts TX_LATE. On the truck that is an inhibitor that stands down
 * immediately, every time, for a reason no log would explain.
 */
static void case_arm_refused_without_alerts(void)
{
    case_begin("case 13: no alerts, no arm");
    setup();

    ft_fail_alerts_config(true);
    const esp_err_t err = gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    ft_run(20000);

    CHECK(err != ESP_OK,
          "the arm succeeded although the TX alerts could not be enabled");
    ft_fail_alerts_config(false);
    teardown();
    case_end();
}


/*
 * CASE 14 -- RESPOND, which nothing here exercised and which HUNG the suite.
 *
 * gen_inhibit.c's probe path spins on esp_timer_get_time() until the frame is
 * due. Against a clock that only moved when the test moved it, that loop could
 * never end: the worker held the CPU and nothing advanced time. No case emitted
 * a probe, so the baseline was fine -- until a round-5 mutation made OBSERVE
 * emit one and shim_test ran at 100 % CPU for 11 minutes before being killed.
 *
 * A HANG REPORTS NOTHING. The clock now advances on every read, which is also
 * the truthful model (that spin really does burn time at priority 18, which is
 * why spec 3 caps the offset at 4000 us), and every case carries a wall-clock
 * budget so a future spin FAILS instead of stopping the world.
 *
 * EXPECT: a probe on 0x7F0 for each 0x051, never on 0x051, and the case
 * finishes.
 */
static void case_respond_probe(void)
{
    case_begin("case 14: RESPOND probes without hanging");
    setup();

    /*
     * Spec 3: RESPOND measures. It may emit the probe and the diag pages and
     * nothing else -- above all not 0x051, which is what makes an accidental
     * real command structurally impossible from a measuring mode.
     */
    static const uint32_t respond_ok[] = { 0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8 };
    ft_allow_ids(respond_ok, (int)(sizeof(respond_ok) / sizeof(respond_ok[0])),
                 "RESPOND may only emit the probe and the diag pages");

    gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, 500);
    ft_run(20000);

    const int before = ft_sent_count();
    uint8_t cmd[6];
    for (int i = 0; i < 10; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)i;
        feed(0x051, cmd, 6, 20000);
    }

    int probes = 0, inhibits = 0;
    for (int i = before; i < ft_sent_count(); i++)
    {
        if (ft_sent(i)->id == 0x7F0) probes++;
        if (ft_sent(i)->id == 0x051) inhibits++;
    }
    CHECK(probes > 0, "RESPOND emitted no probe at all, so this case proved "
                      "nothing about the path that hung");
    CHECK(inhibits == 0, "RESPOND queued %d frames on 0x051 -- it must only "
                         "ever emit the probe ID", inhibits);
    teardown();
    case_end();
}

/*
 * CASE 15 -- the probe really waits for its offset (spec 3, spec 12.4).
 *
 * THE POINT OF THE PROBE is placement. It goes out `offset_us` after the VCM's
 * frame so the measurement says something about where in the gap a frame of
 * ours would land; a probe queued immediately measures nothing, and case 12
 * does not catch that -- it checks only that an offset ABOVE 4000 us is
 * refused, which a firmware that ignores the offset entirely passes happily.
 *
 * Round 6 proved the gap with a mutation: deleting the `if (f->have_due)` spin
 * in dispatch_emits() left every suite green. The offset was configured, was
 * reported in the diag JSON, was range-checked here -- and was not obeyed, and
 * nothing anywhere would have said so.
 *
 * THE BOUND IS ONE-SIDED AND SOUND. ft_last_rx_time() is when the driver handed
 * the frame over; gen_inhibit.c reads the clock just after, so the core's t_rx
 * is that or a shade later and the probe cannot honestly be queued before
 * t_rx + offset. The upper bound is deliberately loose -- it is there to catch
 * an offset applied twice or in the wrong units, not to measure jitter, which
 * is the bench's job and not a model's.
 */
static void case_probe_waits_for_its_offset(void)
{
    case_begin("case 15: the RESPOND probe waits out its offset");
    setup();

    const uint32_t offset = 3000;
    static const uint32_t respond_ok[] = { 0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8 };
    ft_allow_ids(respond_ok, (int)(sizeof(respond_ok) / sizeof(respond_ok[0])),
                 "RESPOND may only emit the probe and the diag pages");

    CHECK(gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, offset) == ESP_OK,
          "RESPOND refused a %u us offset, which is inside the 4000 us limit",
          (unsigned)offset);
    ft_run(20000);

    int checked = 0;
    uint8_t cmd[6];
    for (int i = 0; i < 6; i++)
    {
        const int before = ft_sent_count();
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)i;
        feed(0x051, cmd, 6, 20000);

        const int64_t t_rx = ft_last_rx_time();
        for (int j = before; j < ft_sent_count(); j++)
        {
            const ft_frame_t *f = ft_sent(j);
            if (f->id != 0x7F0) continue;

            CHECK(f->t_queued >= t_rx + (int64_t)offset,
                  "the probe was queued at %lld us, %lld us after the frame "
                  "arrived at %lld -- the offset is %u us, so it did not wait",
                  (long long)f->t_queued, (long long)(f->t_queued - t_rx),
                  (long long)t_rx, (unsigned)offset);

            /* Loose: catches a doubled or mis-scaled offset, not jitter. */
            CHECK(f->t_queued <= t_rx + (int64_t)offset * 2,
                  "the probe was queued %lld us after the frame arrived, more "
                  "than twice its %u us offset",
                  (long long)(f->t_queued - t_rx), (unsigned)offset);

            /*
             * AND THE PROBE SAYS WHAT IT DID, which is a separate claim from
             * having done it. Bytes 4-5 carry the offset so a capture can be
             * read without knowing how the device was configured; a probe that
             * waited correctly and then reported zero is useless for exactly
             * the analysis it exists to support, and the timing assertions
             * above pass happily while it does.
             */
            const uint32_t said =
                (uint32_t)f->data[4] | ((uint32_t)f->data[5] << 8);
            CHECK(said == offset,
                  "the probe reports an offset of %u us in bytes 4-5 while it "
                  "was configured for %u", said, (unsigned)offset);

            checked++;
        }
    }

    /*
     * Without this the case passes by emitting no probe at all -- which is how
     * four earlier cases in this file came to assert nothing. A bound checked
     * zero times is not a bound.
     */
    CHECK(checked > 0,
          "no probe was queued, so this case checked the offset zero times");

    teardown();
    case_end();
}

/*
 * CASE 16 -- the diag page on the wire says what the diag JSON says.
 *
 * WHY A SECOND REPORT IS NEEDED. The wire invariant in fake_twai.c checks the
 * bytes the spec fixes -- B0, the torque pair, the probe's sentinel, the
 * schema version. It cannot check the bytes that carry measurements, because
 * every value they can hold is legal. A status page whose soc_x100 was
 * truncated to zero is a well-formed frame reporting a flat pack.
 *
 * Round 6 proved that with one character: `memcpy(tx.data, f->data, 6)` in
 * dispatch_emits(). At DLC 6 the inhibit frame loses nothing, so the goldens,
 * passive_diff, invariants.py and every E1 case stayed green -- while the
 * status page went out with its SoC bytes zeroed and the probe lost its
 * sentinel. A truck diagnosed from that capture would show a healthy inhibitor
 * on an empty battery.
 *
 * So this compares the two paths that report the same state: the CAN frame and
 * the JSON. They are built by different code from the same fields, which is
 * exactly what makes the comparison worth anything -- a defect in the copy to
 * the driver moves one and not the other.
 *
 * THE STATE IS HELD STILL on purpose. Both readings have to describe the same
 * instant, so the interlock set is fed steadily and nothing is changed between
 * the last diag frame and the JSON read.
 */
static void case_diag_wire_matches_json(void)
{
    case_begin("case 16: the diag page on the wire agrees with the JSON");
    setup();

    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };
    uint8_t cmd[6];

    /*
     * LONG ENOUGH FOR A STATUS PAGE TO FOLLOW THE SoC. Diag round-robins four
     * pages at 300 ms, so 0x7F1 comes round about every 1.2 s. The first
     * version ran 12 iterations -- 360 ms of virtual time -- and caught
     * exactly one status page, emitted before the interlocks established a
     * SoC at 65 ms. It reported soc_x100 = 0 against the JSON's 5000 and read
     * exactly like the truncation defect this case exists to catch. It was
     * the case being too short to observe the thing it compares.
     */
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    for (int i = 0; i < 140; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(i & 0x0F);
        feed(0x051, cmd, 6, 5000);
        feed(0x592, KEY, 8, 5000);
        feed(0x440, CONT, 8, 5000);
        feed(0x411, SOC, 8, 5000);
        feed(0x617, FLT, 8, 5000);
        feed(0x639, SHF, 8, 5000);
    }

    /* The last status page to reach the driver, which is the one the JSON
     * read below describes. */
    const ft_frame_t *page = NULL;
    for (int i = ft_sent_count() - 1; i >= 0; i--)
    {
        if (ft_sent(i)->id == GI_DIAG_ID_STATUS) { page = ft_sent(i); break; }
    }
    CHECK(page != NULL,
          "no status page was queued at all, so this case compared nothing");

    if (page != NULL)
    {
        const uint32_t json_soc  = json_u32("\"soc_x100\":");
        const uint32_t json_mode = json_u32("\"mode\":");

        /* Spec 10: soc_x100 in bytes 6-7, little-endian. */
        const uint32_t wire_soc =
            (uint32_t)page->data[6] | ((uint32_t)page->data[7] << 8);

        CHECK(wire_soc == json_soc,
              "the status page on the wire reports soc_x100 = %u while the "
              "JSON reports %u -- the two are built from the same field, so "
              "they cannot legitimately differ",
              wire_soc, json_soc);

        CHECK((uint32_t)page->data[2] == json_mode,
              "the status page on the wire reports mode %u while the JSON "
              "reports %u", (unsigned)page->data[2], json_mode);

        CHECK(page->dlc == 8,
              "the status page went out with DLC %u", (unsigned)page->dlc);

        /*
         * A state that is all zeroes would satisfy the comparison above
         * without proving anything, which is how several earlier cases in this
         * file came to assert nothing.
         */
        CHECK(wire_soc > 0,
              "soc_x100 is zero on both paths: the scenario never established "
              "a SoC, so an agreement between them is not evidence");
    }

    teardown();
    case_end();
}

/*
 * CASE 17 -- arming forces accept-all, whatever the filter was set to
 * (spec 5.1 item 2).
 *
 * THE MUTATION THAT MADE THIS NECESSARY SURVIVED BECAUSE NOTHING NARROWED THE
 * FILTER. The rule was implemented and had no test at all -- review 2026-09-26
 * found F1 (the forcing disabled) passing every suite -- and then found the
 * implementation could not have worked on this path anyway:
 * gen_inhibit_set_mode() called can_enable() before gi_set_mode() recorded the
 * mode, so gen_inhibit_owns_bus() was false inside twai_driver_install().
 *
 * Why it matters rather than being tidiness: every condition in the section 7
 * arm gate and every trip reads an ID, and an acceptance mask that drops one of
 * them FAILS SILENTLY -- a device that never goes live, or one that trips stale,
 * for a reason no log would explain.
 *
 * The check is on the value handed to twai_driver_install(), because the TWAI
 * driver exposes no read-back of the installed filter. That is the same ground
 * case 11 checks the listen-only mode on.
 */
static void case_arm_forces_accept_all(void)
{
    case_begin("case 17: arming forces accept-all however the filter was set");
    setup();

    /*
     * THE BUS IS ALREADY UP after setup(), and that silently defeated the first
     * version of this case: can_set_filter() and can_set_mask() return early
     * while can_cfg.bus_state is ON_BUS, so the filter never narrowed and the
     * case checked that an already-accept-all filter was still accept-all.
     */
    can_disable();

    /*
     * Narrow it the way slcan.c would, while the bus is down -- can_set_filter()
     * and can_set_mask() refuse while ON_BUS, which is itself part of why the
     * fix has to tear a live driver down rather than just rewrite the config.
     * 0x051 with an 11-bit mask is the worst realistic case: it keeps the one ID
     * an inhibit answers and drops every signal the gate needs.
     */
    can_set_filter(0x051);
    can_set_mask(0x7FF);
    CHECK(can_filter_narrowed(),
          "the filter did not narrow, so this case cannot show it being "
          "widened again and proves nothing");

    go_live();

    CHECK(ft_installed_acc_mask() == 0xFFFFFFFFu && ft_installed_acc_code() == 0,
          "the driver was installed with code 0x%08X mask 0x%08X -- spec 5.1 "
          "item 2 requires accept-all (code 0, mask 0xFFFFFFFF) while the "
          "inhibitor owns the bus",
          (unsigned)ft_installed_acc_code(), (unsigned)ft_installed_acc_mask());
    CHECK(!can_filter_narrowed(),
          "the configuration is still narrowed after arming, so the next "
          "install would narrow the controller again");

    teardown();
    case_end();
}

/*
 * CASE 18 -- the driver is installed with a receive queue at least 32 deep
 * (spec 5.1 item 1, and the E1 half of the spec 12.4 row that asks for exactly
 * this: "E1 reads the depth the driver was installed with").
 *
 * Q1 (the override line deleted) and Q2 (32 put back to 5) both survived every
 * suite until 2026-09-26, because the depth was not observable anywhere: the
 * fake driver took the general config and kept only the mode.
 *
 * The depth is the remedy that makes the receive margin positive rather than
 * merely smaller. WiFi/lwIP preemption blocks the worker task, not the TWAI
 * interrupt, so the ISR keeps moving frames into this queue and its depth is
 * what has to cover preemption x frame rate. E4 measures the margin that buys
 * -- first loss at 20 ms against a 2.39 ms worst preemption -- and this is what
 * ties that model to the number the firmware actually installs.
 */
static void case_rx_queue_depth(void)
{
    case_begin("case 18: the driver is installed with an RX queue >= 64");
    setup();

    /*
     * Bring the bus down so that arming has to install the driver. Without this
     * no install happens inside the case at all, and the accessor returns
     * ft_reset()'s zero -- which the first version of this case reported as a
     * firmware failure to set the depth. The install count below is what makes
     * the difference visible rather than guessable.
     */
    can_disable();
    go_live();

    CHECK(ft_install_count() > 0,
          "the driver was never installed during this case, so the recorded "
          "queue depth is the reset value and says nothing about the firmware");
    CHECK(ft_installed_rx_queue_len() >= 64,
          "the driver was installed with rx_queue_len = %u; spec 5.1 item 1 "
          "requires at least 64, about 27 ms of truck traffic at the measured "
          "2364 frames/s",
          (unsigned)ft_installed_rx_queue_len());

    teardown();
    case_end();
}

/*
 * CASE 19 -- an outstanding inhibit still defers diag after the key goes off
 * (spec 10, and the case that makes the core's deferral load-bearing).
 *
 * CASE 9 STOPPED COVERING THIS on 2026-09-26. It holds the interlocks up with
 * the key ON, and spec 5.1 item 4 now keeps diag off the bus in that situation
 * for its own reason -- the device is "sending", so a due page waits for a
 * completion. So case 9 passes whether or not the core's separate
 * "no diag while an inhibit is outstanding" rule exists, and M2 (that rule
 * deleted) survived the whole suite including 200 randomised sequences.
 *
 * Key OFF is what separates them. It makes `sending` false, so item 4's
 * scheduling steps aside and the normal cadence applies -- while the frame
 * already handed to the controller is still outstanding, because a key-off does
 * not clear tx_pending. The section 10 rule is then the only thing standing
 * between a diag page and the single TX buffer, which is exactly the condition
 * the rule exists for: a page queued behind our frame holds msgs_to_tx above
 * zero after ours has gone, and the shim would read that as our frame never
 * completing.
 */
static void case_diag_defers_after_keyoff(void)
{
    case_begin("case 19: an outstanding inhibit defers diag with the key off");
    setup();
    go_live();

    static const uint8_t KEYOFF[8] = { 0x00 };     /* IgnitionKeyState clear */
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };

    /* Case 8's mechanism, for case 9's reason -- see the note there. */
    ft_set_air_time(600000);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = 0x0C;
    feed(0x051, cmd, 6, 5000);

    /*
     * GIVE THE WORKER A TICK TO DISPATCH before sampling anything. The feed
     * above advances 5 ms, which does not always get the worker as far as
     * twai_transmit() -- the frame is accepted by the core and counted in its
     * `response` histogram while no queue attempt has been made yet, so a
     * precondition sampled here reads "nothing outstanding" for a frame that is
     * about to be queued. With the 600 ms air time above it stays outstanding
     * once it is queued, so waiting costs nothing.
     */
    ft_run(20000);

    /*
     * THE GUARD, and it has to be taken here rather than asserted at the end.
     * If our frame is not actually outstanding when the key goes off there is
     * nothing for the section 10 rule to defer to, and "no diag was queued"
     * would then be true for an uninteresting reason.
     */
    const int sent_051 = ft_sent_count_id(0x051);
    const int wire_051 = ft_wire_count_id(0x051);

    const int q_before = ft_sent_count();

    /*
     * No further 0x051, for case 9's reason: the next one would trip TX_LATE,
     * clear tx_pending, and every diag page counted after that would have been
     * queued when deferring was neither required nor happening. 400 ms is past
     * the 300 ms diag period and inside the 500 ms freshness window, so the
     * bus-loss trip has not fired either.
     */
    const int64_t end = ft_now() + 400000;
    while (ft_now() < end)
    {
        feed(0x592, KEYOFF, 8, 20000);
        feed(0x440, CONT, 8, 20000);
        feed(0x411, SOC, 8, 20000);
        feed(0x617, FLT, 8, 20000);
        feed(0x639, SHF, 8, 20000);
    }

    int diag_queued = 0;
    for (int i = q_before; i < ft_sent_count(); i++)
    {
        const uint32_t id = ft_sent(i)->id;
        if (id == 0x7F1 || id == 0x7F2 || id == 0x7F3 || id == 0x7F8) diag_queued++;
    }

    CHECK(sent_051 > wire_051,
          "no inhibit was outstanding when the key went off (%d queued, %d on "
          "the wire), so there was nothing for the section 10 rule to defer "
          "to: %s", sent_051, wire_051, stats());
    CHECK(json_has("\"abort_reason\":\"\""),
          "the device aborted during the window, so the deferral was not what "
          "was being tested: %s", stats());
    CHECK(diag_queued == 0,
          "%d diag frames were queued while an inhibit was outstanding and the "
          "key was off -- spec 10. With the key off, spec 5.1 item 4's "
          "scheduling does not apply, so this rule is the only guard",
          diag_queued);

    teardown();
    case_end();
}

static void case_status_json_bounds(void)
{
    case_begin("case 20: the status page truncates instead of overrunning");
    setup();

    /*
     * A populated page, so the sizes below are crossing a REAL length rather
     * than a nearly empty object. go_live() gives it histograms with content and
     * a mode, which is most of the page's bulk.
     */
    go_live();
    ft_run(20000);

    /*
     * Sizes chosen to straddle the page: far too small, around the truncation
     * boundary, and comfortably larger. 1400 is what config_server.c actually
     * passes.
     */
    static const int sizes[] = { 1, 2, 8, 40, 200, 600, 1100, 1219, 1220,
                                 1221, 1400, 1600 };

    for (unsigned i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++)
    {
        const int cap = sizes[i];
        /*
         * A CANARY AFTER THE BUFFER, because the bug writes past it and a
         * returned length cannot show that. Checked byte by byte so a partial
         * overrun is caught, not only a large one.
         */
        enum { PAD = 64 };
        char *area = malloc((size_t)cap + PAD);
        CHECK(area != NULL, "out of memory building the %d-byte case", cap);
        if (area == NULL) { continue; }
        memset(area + cap, 0x5A, PAD);

        const int n = gen_inhibit_get_stats_json(area, cap);

        int clean = 1;
        for (int k = 0; k < PAD; k++)
        {
            if ((unsigned char)area[cap + k] != 0x5A) { clean = 0; break; }
        }
        CHECK(clean,
              "gen_inhibit_get_stats_json wrote PAST a %d-byte buffer. snprintf "
              "returns the length it WOULD have written, so one truncated "
              "append leaves the offset past the end and the next append is "
              "handed a negative size that converts to a huge size_t",
              cap);

        CHECK(n >= 0 && n < cap,
              "with a %d-byte buffer the returned length was %d; it must be "
              "less than the capacity, because config_server.c hands it "
              "straight to httpd_resp_send() and would otherwise read the "
              "terminator or past it",
              cap, n);

        /*
         * And the bytes it claims must be inside what it wrote: a NUL at or
         * before n proves the return is not counting phantom content.
         */
        if (n >= 0 && n < cap)
        {
            /* memchr, not strnlen: strnlen needs a POSIX feature macro the
             * shim does not define, and the failure is a link-time surprise. */
            const char *nul = memchr(area, '\0', (size_t)cap);
            CHECK(nul != NULL && (nul - area) >= n,
                  "the %d-byte case returned %d but the string ends at %ld, so "
                  "the length does not describe the content",
                  cap, n, nul ? (long)(nul - area) : -1L);
        }

        free(area);
    }

    teardown();
    case_end();
}


/*
 * CASE 21 -- three skips inside one second trip; one does not.
 *
 * Mutation I2 survived without this: the scheduler's 3-in-1 s verdict never
 * reached the core and nothing noticed, because E1 had no case with three skips
 * inside a second at all. Both halves matter. Asserting only that three trip
 * would also pass if EVERY skip tripped, which is the behaviour the user's
 * amendment of 2026-09-27 replaced.
 *
 * The skips come from stalling 0x051, not from refusing it: a refusal trips at
 * once through trip 7's not-queued form (case 23) and would mask the skip path
 * entirely.
 */
static void case_skip_window_trips_at_three(void)
{
    case_begin("case 21: three skips within 1 s trip, one does not");
    setup();
    go_live();

    uint8_t ctr = 0x20;

    /* ONE skip. The stall is spent on the first inhibit; the rest complete. */
    ft_stall_id(0x051, 1);
    keep_alive(200000, &ctr);

    CHECK(json_need("\"skipped\":") == 1,
          "expected exactly one skip from one stalled inhibit, page says %u: %s",
          json_u32("\"skipped\":"), stats());
    CHECK(!json_has("\"abort_latched\":true"),
          "a SINGLE skip latched an abort. Spec 7 trip 7 as amended (user, "
          "2026-09-27) says three within 1 s trip and fewer do not: %s",
          stats());
    CHECK(json_has("\"inhibit_live\":true"),
          "the device stopped inhibiting after one tolerated skip: %s", stats());

    /* Now enough of them, close enough together. */
    ft_stall_id(0x051, 8);
    keep_alive(400000, &ctr);

    CHECK(json_need("\"skipped\":") >= 3,
          "fewer than three skips after 0.4 s of stalled inhibits, so the trip "
          "below would not be the one being tested: %s", stats());
    CHECK(json_has("\"abort_latched\":true"),
          "three skips inside a second did not trip: %s", stats());
    CHECK(json_has("3 inhibit frames skipped within 1 s"),
          "the trip did not name the skip window, so it was a different trip: "
          "%s", stats());
    teardown();
    case_end();
}

/*
 * CASE 22 -- the controller reporting a FAILED transmit trips at once.
 *
 * Mutations I3 and I8 both survived without this. I3 stops the TWAI_ALERT_TX_FAILED
 * latch being set; I8 credits the completion regardless of it. Either way an
 * inhibit the controller could not get out is counted as sent.
 *
 * This is the alert read that moved into sched_advance() when poll_tx_completion()
 * was deleted. The compiler's unused-function warning is what stopped it being
 * lost then, and a warning is not a test.
 */
static void case_controller_failed_trips(void)
{
    case_begin("case 22: the controller reporting a failed transmit trips");
    setup();
    go_live();

    uint8_t ctr = 0x30;
    ft_fail_next();                 /* the next completing frame raises it */
    keep_alive(200000, &ctr);

    CHECK(json_has("\"abort_latched\":true"),
          "a controller-reported transmit failure did not latch an abort: %s",
          stats());
    CHECK(json_has("transmit failed"),
          "an abort latched but did not name the transmit failure: %s", stats());
    /*
     * AND IT WAS THIS TRIP, not a skip that happened along the way. Without
     * this the case would also pass on a build that tripped for the wrong
     * reason, which is the failure it exists to distinguish.
     */
    CHECK(json_need("\"skipped\":") == 0,
          "the trip came with %u skips, so it may have been the skip window "
          "rather than the reported failure: %s",
          json_u32("\"skipped\":"), stats());
    teardown();
    case_end();
}

/*
 * CASE 23 -- a REFUSED inhibit trips at once, and nothing follows it onto the
 * wire.
 *
 * Mutation I7 survived without the first half. The second half is defect D7,
 * found by the reviewing session by reading the switchover: a refused submit
 * counts `refused` and returns WITH THE FRAME STILL QUEUED, so the core latched
 * off on the refusal and the scheduler's retry on the next tick could put an
 * inhibit frame on the wire AFTER the trip that was supposed to stop us.
 *
 * ft_refuse_id(), not ft_refuse_next(): the refusal has to land on the inhibit
 * rather than on whichever frame the tick happened to queue first.
 */
static void case_refusal_trips_and_nothing_follows(void)
{
    case_begin("case 23: a refused inhibit trips, and is not sent afterwards");
    setup();
    go_live();

    uint8_t ctr = 0x40;
    ft_refuse_id(0x051, 1);
    keep_alive(120000, &ctr);

    CHECK(json_has("\"abort_latched\":true"),
          "a refused inhibit did not latch an abort: %s", stats());
    CHECK(json_has("could not be queued"),
          "the abort did not name the refusal: %s", stats());

    /* The driver accepts frames again from here, so a frame still sitting in the
     * scheduler's queue would be retried and WOULD go out. */
    keep_alive(500000, &ctr);

    /*
     * D7, STATED AS WHAT HAPPENED TO THE FRAME rather than as when it went out.
     *
     * Two earlier versions of this check could not fail. One compared wire counts
     * across a 120 ms window, by which time the retry had already happened; one
     * moved that into a step hook, and the trip and the retry turned out to fall
     * inside a single worker iteration, so the hook's baseline already contained
     * the frame it was watching for. Measuring both builds settled it:
     *
     *                     correct   D7 removed
     *   inhibit queued      12         12
     *   inhibit sent        11         12
     *   0x051 on the wire   11         12
     *   skipped_withdrawn    1          0
     *
     * Neither of the two checks below needs to know WHEN the trip landed, which
     * is the part the harness cannot resolve. They come from two independent
     * places -- the fake's wire log and the scheduler's own counters -- so a
     * defect has to fool both to pass.
     */
    const uint32_t queued = json_need("\"inhibit\":{\"queued\":");
    CHECK((uint32_t)ft_wire_count_id(0x051) < queued,
          "all %u queued inhibit frames reached the wire, so the one the driver "
          "refused was retried after the trip instead of being withdrawn "
          "(defect D7): %s", queued, stats());
    CHECK(json_need("\"skipped_withdrawn\":") >= 1,
          "the refused inhibit was never counted as withdrawn, so it stayed in "
          "the scheduler's queue for a retry -- and a retry after the trip puts "
          "an inhibit frame on the wire after the thing that stopped us "
          "(defect D7): %s", stats());
    teardown();
    case_end();
}

/*
 * CASE 24 -- the KIND of a skip is reported, and it is the right kind.
 *
 * Mutation I9 reports every skip as maybe-late. It survived for a reason worth
 * recording: before defect D9 was fixed the kind was visible NOWHERE. The core
 * raised GI_EV_SKIP carrying it, report_events() had no case for that event, and
 * the `default: break;` swallowed it without a -Wswitch warning. So the mutation
 * corrupted a value that nothing read, and no test could have caught it --
 * the observer had to exist first.
 *
 * The distinction is not cosmetic. WITHDRAWN means the frame never reached the
 * wire; MAYBE_LATE means it may have arrived after the VCM's next command, which
 * is a different thing to see in a truck log.
 */
static void case_skip_kind_is_reported(void)
{
    case_begin("case 24: a withdrawn skip is reported as withdrawn");
    setup();
    go_live();

    uint8_t ctr = 0x50;
    /*
     * Cleared right before the action, not at setup(): the log holds 512 lines
     * and go_live() plus a stretch of keep_alive() can fill it, at which point
     * ml_note() silently drops the line this case is looking for.
     */
    ml_clear();
    ft_stall_id(0x051, 1);
    keep_alive(200000, &ctr);

    CHECK(ml_count("withdrawn before the wire") >= 1,
          "a stalled inhibit was withdrawn at the deadline and nothing said so. "
          "The kind reaches the log through GI_EV_SKIP in report_events()");
    CHECK(ml_count("may have gone out late") == 0,
          "%d skip(s) were reported as possibly late, but the frame was still "
          "AWAITING arbitration and the abort removed it -- it cannot have "
          "reached the wire",
          ml_count("may have gone out late"));

    /*
     * And the scheduler's own breakdown adds up. These are two independent
     * statements: the log says what the CORE was told, this says the SCHEDULER's
     * counters agree with their own total.
     */
    CHECK(json_need("\"skipped\":")
              == json_need("\"skipped_withdrawn\":")
               + json_need("\"skipped_late\":"),
          "the skip total and its per-kind breakdown disagree, so at least one "
          "of them is wrong: %s", stats());
    CHECK(json_need("\"skipped_withdrawn\":") >= 1,
          "the withdrawn skip was not counted as withdrawn: %s", stats());
    teardown();
    case_end();
}

/*
 * CASE 25 -- no abort command is issued while the buffer is TRANSMITTING.
 *
 * Spec 5.2 item 4 waits for TS to fall instead, because the measurement said the
 * command does nothing there: 7 of 8 contended trials, artifacts/gen-inhibit/
 * runs/txabort_full.json. Mutation I6 makes the HAL command an abort whatever the
 * buffer state, and ft_ll_aborts_while_tx() is the only thing that can see it --
 * the scheduler's own statistics cannot tell a command it never sent from one
 * that had no effect.
 *
 * THE AIR TIME GOES ON 0x051, NOT ON THE DIAG IDS. An earlier draft of this case
 * put it on the diag pages and relied on preemption reaching the abort, but
 * preemption is guarded by `buf_state() == AWAITING` and never enters the abort
 * state while transmitting -- so the assertion held no matter what the HAL did.
 * Making the INHIBIT slow puts a genuinely mid-transmission frame in front of the
 * deadline's withdrawal, which is the one path that calls the HAL there.
 */
static void case_no_abort_while_transmitting(void)
{
    case_begin("case 25: no abort command while the buffer is transmitting");
    setup();
    go_live();

    /* Longer than the VCM's period, so the frame is still on the air at the
     * deadline that withdraws it. */
    ft_set_air_time_id(0x051, 150000);

    uint8_t ctr = 0x60;
    keep_alive(600000, &ctr);

    /*
     * THE TWO GUARDS COME FIRST. Without them the assertion below passes on any
     * build where no abort was ever attempted, or where the deadline never found
     * a transmitting frame -- and both of those look exactly like success.
     */
    CHECK(json_need("\"late_on_wire\":") >= 1,
          "no deadline found the inhibit still in the buffer, so the "
          "transmitting branch was never reached and the check below is "
          "vacuous: %s", stats());
    CHECK(json_need("\"aborts\":") >= 1,
          "no abort was started at all, so the check below cannot fail: %s",
          stats());

    CHECK(ft_ll_aborts_while_tx() == 0,
          "%d abort command(s) were issued while the buffer was TRANSMITTING. "
          "Spec 5.2 item 4 waits for TS to fall, because the measurement showed "
          "the command has no effect there (7 of 8 contended trials): %s",
          ft_ll_aborts_while_tx(), stats());
    teardown();
    case_end();
}

/*
 * CASE 26 -- the status page fits the buffer the HTTP handlers give it.
 *
 * DEFECT D10, and it was live. gs_json() appends the scheduler block to the END
 * of the page, which took it to 1761 bytes while both handlers in
 * config_server.c passed 1400. The page was therefore cut mid-token --
 *
 *     ..."cls":{"inhibit":{"queued":11,"sent":11,"aborted":0,"handed":1
 *
 * -- so the whole per-class block was missing AND the body was not valid JSON,
 * returned with a 200. Spec 11 requires those counters on this page.
 *
 * Case 20 already proves the truncation is memory-SAFE. Safe and silently wrong
 * is what let this sit there, so this case asserts the page is WHOLE: it ends
 * where it says it ends, and its braces balance. Counting braces rather than
 * checking the last byte is what catches a cut in the middle of a number, which
 * is what actually happened.
 */
/*
 * The longest value a text field's source table can put on the page, found by
 * ASKING THE TABLE over its whole index range rather than copying its strings
 * here. A case that hard-codes 60 for the trip-7 abort text is a second copy of
 * a table that will not be updated alongside the first, and the bound would
 * silently stop being a bound.
 *
 * The range is walked well past each enum's last member on purpose: an added
 * name is then inside the bound from the moment it exists, and an out-of-range
 * index simply takes the switch's default. Nothing here depends on the enum
 * counts, which these headers do not publish.
 */
#define NAME_PROBE_RANGE 64

static size_t longest_from(const char *(*name)(int))
{
    size_t m = 0;
    for (int i = 0; i < NAME_PROBE_RANGE; i++)
    {
        const char *s = name(i);
        const size_t n = (s != NULL) ? strlen(s) : 0;
        if (n > m) { m = n; }
    }
    return m;
}

static const char *name_abort(int i)   { return gi_abort_name((gi_abort_t)i); }
static const char *name_block(int i)   { return gi_block_name((gi_block_t)i); }
static const char *name_selfoff(int i) { return gi_self_off_name((gi_self_off_t)i); }
static const char *name_disable(int i) { return gi_disable_name((gi_disable_t)i); }
static const char *name_drvstate(int i){ return drv_state_name((twai_state_t)i); }

/*
 * How many bytes `"<key>":"<value>"` would grow by if the value were the
 * longest its table can return. Returns 0 if the key is absent, which the
 * caller checks separately -- a missing key here would silently remove a term
 * from the bound.
 */
static int text_field_slack(const char *page, const char *key, size_t longest,
                            bool *found)
{
    char needle[48];
    snprintf(needle, sizeof(needle), "\"%s\":\"", key);
    const char *p = strstr(page, needle);
    *found = (p != NULL);
    if (p == NULL) { return 0; }
    p += strlen(needle);
    const char *end = strchr(p, '"');
    if (end == NULL) { return 0; }
    const size_t cur = (size_t)(end - p);
    return (longest > cur) ? (int)(longest - cur) : 0;
}

static void case_status_page_fits_the_handler_buffer(void)
{
    case_begin("case 26: the status page fits the buffer the handler passes");
    setup();
    go_live();
    ft_run(20000);

    static char page[GI_STATUS_PAGE_CAP];
    const int n = gen_inhibit_get_stats_json(page, sizeof(page));

    CHECK(n > 0 && n < (int)sizeof(page) - 1,
          "the status page needs more than the %d bytes config_server.c gives "
          "it (returned %d). Raise GI_STATUS_PAGE_CAP -- until then the tail of "
          "the page is cut off and the body is not valid JSON",
          (int)sizeof(page), n);

    /*
     * THE WORST CASE, NOT THIS CASE. The assertion above is about the page this
     * shim happens to render, whose counters are in the tens -- and it passed
     * happily while the real page was being cut on the bench, because a 90 s arm
     * puts five-figure numbers in the same fields and the widest u32 is ten
     * digits. A check that can only fail on values the harness never produces is
     * the trap this project's notes name: its failure looks like its success.
     *
     * So: rewrite every integer in the rendered page to the full width of ITS
     * OWN type and require THAT to fit. It is an over-estimate by construction
     * (no arm makes every counter 4294967295 at once), which is the right
     * direction for a buffer bound.
     *
     * THE WIDTH IS PER TYPE, NOT A FLAT TEN. Every integer on the page is a u32
     * except spec 11's `uptime_ms`, which is a u64 and so runs to twenty digits
     * -- ten more than this used to allow it. Widening EVERYTHING to twenty
     * instead would be simpler and wrong in the expensive direction: there are
     * on the order of seventy-five integer fields here, so it would claim the
     * page needs about 3250 bytes, fail against the 3072-byte cap, and demand a
     * bigger buffer to satisfy a bound no device can reach. A bound has to be
     * loose enough to never fire wrongly and tight enough that firing means
     * something.
     */
    int widened = 0;
    int arr_ints = 0;
    bool in_edges = false;
    for (int i = 0; i < n; i++)
    {
        /*
         * `bucket_edges_us` is built from compile-time constants, so its
         * elements are the one group of integers on this page that CANNOT
         * grow. Every other array holds counters.
         */
        if (page[i] == '[')
        {
            in_edges = (i >= 18
                        && memcmp(page + i - 18,
                                  "\"bucket_edges_us\":[", 19) == 0);
        }
        else if (page[i] == ']')
        {
            in_edges = false;
        }

        const char c = page[i];
        const bool starts = (c >= '0' && c <= '9')
                            || (c == '-' && i + 1 < n
                                && page[i + 1] >= '0' && page[i + 1] <= '9');
        if (!starts) { continue; }

        /*
         * A number begins only after ':' (a field), '[' (first element) or ','
         * (a later element). Anything else means these digits are INSIDE a
         * string -- "0x051" in an abort text, the probe id -- or mid-run, and
         * widening those would inflate the bound with characters no counter
         * controls. The ':' form was all the first version matched, which is
         * why the histogram buckets went unwidened: array elements never
         * follow a colon.
         */
        const char prev = (i > 0) ? page[i - 1] : '\0';
        if (prev != ':' && prev != '[' && prev != ',') { continue; }

        int j = (c == '-') ? i + 1 : i;
        while (j < n && page[j] >= '0' && page[j] <= '9') { j++; }
        const int have = j - i;             /* the sign counts as a character */

        int want;
        if (c == '-')
        {
            want = 11;                      /* "-2147483648" */
        }
        else if (prev == ':')
        {
            /* `"uptime_ms":` is 12 characters and ends at i-1. */
            const int is_u64 = (i >= 12
                                && memcmp(page + i - 12,
                                          "\"uptime_ms\":", 12) == 0);
            want = is_u64 ? 20 : 10;
        }
        else
        {
            if (in_edges) { i = j - 1; continue; }
            arr_ints++;
            want = 10;
        }
        widened += (have < want) ? (want - have) : 0;
        i = j - 1;
    }

    /*
     * TEXT FIELDS AND BOOLEANS, which no amount of integer widening reaches.
     *
     * The page this shim renders happens to carry empty or short strings in
     * every text field -- no abort latched, no self-imposed OFF -- and the
     * longest value some of those tables can return is 60 characters. So the
     * integer bound above passed at 2,822 bytes while the real worst case is
     * over the 3,072 cap, and nothing said so (gen-inhibit tester, 2026-10-10,
     * runs/case26_worst_case_ddddfea_20261010.txt: 3,197 against 3,072).
     *
     * Each term is (longest the table can return) - (what is on the page now),
     * and the longest comes from the table itself. `false` is one byte longer
     * than `true`, so every boolean reading true can still grow by one.
     */
    bool found[6];
    int text = 0;
    text += text_field_slack(page, "abort_reason",
                             longest_from(name_abort), &found[0]);
    /*
     * arm_block carries the ABORT name while an abort is latched and the block
     * name otherwise -- see the `block` ternary in gen_inhibit_get_stats_json
     * -- so its worst case is the longer of the two tables, not its own.
     */
    text += text_field_slack(page, "arm_block",
                             longest_from(name_block) > longest_from(name_abort)
                                 ? longest_from(name_block)
                                 : longest_from(name_abort), &found[1]);
    text += text_field_slack(page, "self_off",
                             longest_from(name_selfoff), &found[2]);
    text += text_field_slack(page, "disable_reason",
                             longest_from(name_disable), &found[3]);
    text += text_field_slack(page, "state",
                             longest_from(name_drvstate), &found[4]);
    text += text_field_slack(page, "reset_reason",
                             GI_RESET_REASON_LEN - 1, &found[5]);
    for (int k = 0; k < 6; k++)
    {
        CHECK(found[k],
              "case 26 could not find text field %d on the page, so its term "
              "is missing from the bound and the bound is too small by however "
              "long that field can get", k);
    }

    int bools = 0;
    for (int i = 0; i + 5 <= n; i++)
    {
        if (page[i] == ':' && memcmp(page + i + 1, "true", 4) == 0) { bools++; }
    }

    const int worst = n + widened + text + bools;
    printf("           case 26 bound: n %d, +%d integers (%d in arrays), "
           "+%d text, +%d bool -> %d against cap %d\n",
           n, widened, arr_ints, text, bools, worst, (int)sizeof(page));
    CHECK(worst < (int)sizeof(page) - 1,
          "the page is %d bytes now, but %d at its worst case -- every integer "
          "at its type's full width, every text field at the longest its table "
          "can return, every boolean false -- against a %d-byte buffer. A long "
          "arm with an abort latched will cut it mid-token, the device will "
          "answer HTTP 200 with unparseable JSON, and the harness will report "
          "that as an unreachable device. Raise GI_STATUS_PAGE_CAP",
          n, worst, (int)sizeof(page));

    int depth = 0, lowest = 0;
    for (int i = 0; i < n; i++)
    {
        if (page[i] == '{') { depth++; }
        else if (page[i] == '}') { depth--; if (depth < lowest) lowest = depth; }
    }
    CHECK(depth == 0 && lowest == 0,
          "the status page's braces do not balance (ends at depth %d, lowest "
          "%d), so it was truncated part-way through and no consumer can parse "
          "it: %s", depth, lowest, page);
    teardown();
    case_end();
}


/*
 * CASE 28 -- spec section 11 and 5.2 item 6: every status reading balances.
 *
 * The page is read at EVERY worker step boundary and each reading is checked for
 *
 *     queued + carried_in == sent + dropped + depth + held
 *
 * per class. An always-property, checked always, for the reason fake_twai.h's
 * step-hook comment gives: a window that opens and closes is invisible to a check
 * that only runs at the end, and this is precisely such a window -- a frame is in
 * the controller for a few hundred microseconds out of every cycle.
 *
 * WHAT IT DOES NOT PROVE. Not that the critical section excludes anything.
 * mock/freertos/FreeRTOS.h's macros are empty and its header says why: the worker
 * here advances only when the test advances virtual time, so nothing runs between
 * two statements of the page builder and there is no race to lose. What covers
 * the exclusion is the unicore #error in gen_inhibit.c and the bench, where the
 * worker really does preempt the web server -- sched_identity_check.py applies
 * this same balance to every recorded arm's end-of-arm page.
 */
static int g_bal_readings;
static int g_bal_failures;
/*
 * READINGS THAT ACTUALLY EXERCISED THE TERM, not readings taken.
 *
 * `held` and `depth` are the two balance terms that are 0 on an idle device,
 * so a run with no frame ever in flight balances trivially and reads exactly
 * like a clean pass. That is how mutation H1 -- held always reported 0 on the
 * page -- survived once case 28's virtual-time span shrank: 799 readings, none
 * of them with held set, and a population check that counted the 799.
 */
static int g_bal_held_seen;
static int g_bal_depth_seen;
static char g_bal_first[GI_STATUS_PAGE_CAP];

/* Pull one unsigned field out of a JSON object fragment, or -1 if absent. */
static long jnum(const char *obj, const char *key)
{
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char *p = strstr(obj, pat);
    if (p == NULL) { return -1; }
    p += strlen(pat);
    long v = 0;
    if (*p < '0' || *p > '9') { return -1; }
    while (*p >= '0' && *p <= '9') { v = v * 10 + (*p - '0'); p++; }
    return v;
}

static void balance_one_class(const char *page, const char *cls)
{
    char pat[48];
    snprintf(pat, sizeof(pat), "\"%s\":{", cls);
    const char *p = strstr(page, pat);
    if (p == NULL) { return; }
    const char *end = strchr(p, '}');
    if (end == NULL) { return; }

    static char obj[512];
    size_t len = (size_t)(end - p) + 1;
    if (len >= sizeof(obj)) { len = sizeof(obj) - 1; }
    memcpy(obj, p, len);
    obj[len] = 0;

    const long q  = jnum(obj, "queued");
    const long se = jnum(obj, "sent");
    const long dr = jnum(obj, "dropped");
    const long de = jnum(obj, "depth");
    const long ci = jnum(obj, "carried_in");
    const long wd = jnum(obj, "withdrawn");
    const long he = jnum(obj, "held");
    if (q < 0 || se < 0 || dr < 0 || de < 0 || ci < 0 || wd < 0 || he < 0)
    {
        if (g_bal_failures++ == 0)
        {
            snprintf(g_bal_first, sizeof(g_bal_first),
                     "%s is missing a balance field: %s", cls, obj);
        }
        return;
    }
    if (he > 0) { g_bal_held_seen++; }
    if (de > 0) { g_bal_depth_seen++; }

    if (q + ci != se + dr + wd + de + he)
    {
        if (g_bal_failures++ == 0)
        {
            snprintf(g_bal_first, sizeof(g_bal_first),
                     "%s does not balance: queued %ld + carried_in %ld != sent "
                     "%ld + dropped %ld + withdrawn %ld + depth %ld + held %ld "
                     " (%s)",
                     cls, q, ci, se, dr, wd, de, he, obj);
        }
    }
}

static void balance_step_hook(void)
{
    static char page[GI_STATUS_PAGE_CAP];
    const int n = gen_inhibit_get_stats_json(page, sizeof(page));
    if (n <= 0) { return; }
    g_bal_readings++;
    /*
     * The inhibit class is checked too, including `withdrawn` -- so a reading
     * taken after a skip balances as well. test/gen_inhibit_sched's
     * case_identity_withdrawn_inhibit_balances drives that term directly.
     */
    balance_one_class(page, "inhibit");
    balance_one_class(page, "probe");
    balance_one_class(page, "telemetry");
}

static void case_status_page_always_balances(void)
{
    case_begin("case 28: every status reading balances (5.2 item 6, sec 11)");
    setup();
    go_live();

    g_bal_readings = 0;
    g_bal_failures = 0;
    g_bal_held_seen = 0;
    g_bal_depth_seen = 0;
    g_bal_first[0] = 0;
    /*
     * MANY SHORT STEPS, NOT ONE LONG ONE. ft_run() returns as soon as the worker
     * blocks in twai_receive(), so a single ft_run(400000) fires the hook ONCE --
     * which the population check below caught, and which would otherwise have
     * read as a clean pass over one reading.
     *
     * AND IT MUST BE REAL TRAFFIC, not bare turns on an idle bus. This case used to
     * be 400 x ft_run(1000) and nothing else. That covered 40 s of virtual time only
     * because every step rode the blocked receive's 200 ms deadline jump; when that
     * jump was bounded to the turn's grant (g_turn_grant in fake_twai.c) the same
     * loop covered 0.81 s, and the number of readings with `held` set went from 395
     * to ZERO. The case still passed, and mutation H1 -- `held` always reported 0 on
     * the page -- went from caught to surviving. Nothing in the suite said so,
     * because the loop's own cost was what had been providing the coverage.
     *
     * So: feed the interlocks and real VCM commands, which puts inhibit frames in
     * flight and makes `held` and `depth` non-zero at some of the readings, and
     * ASSERT that below rather than assuming it.
     */
    ft_set_step_hook(balance_step_hook);
    uint8_t bal_ctr = 0x40;
    keep_alive(2000000, &bal_ctr);

    /*
     * AND THE `depth` TERM, which real traffic alone does not reach: at this rate a
     * frame is handed to the controller immediately and the queue never holds one, so
     * every reading above has depth 0.
     *
     * AND THE ABORT-AND-REQUEUE ROUTE DOES NOT EXIST IN THIS HARNESS, which cost two
     * wrong attempts and is a fact about the mock worth stating. fake_twai's
     * controller is a FIFO with an air time, NOT a single TX buffer: an inhibit is
     * handed over and completes regardless of a telemetry frame already in flight.
     * Measured -- with a page held for 20 ms, the inhibit's max_queue_us stayed at
     * 1 us and `aborted` stayed 0. So telemetry cannot block an inhibit here and no
     * abort can be provoked. Single-buffer contention is fake_buf's model, over in
     * test/gen_inhibit_sched, and that is where the abort cases live.
     *
     * What reaches `depth` here is two TELEMETRY frames outstanding at once, so the
     * air time has to exceed diag_period_ms and let the next page come due while the
     * current one is still going. Measured at 400 ms: depth 4, held 1, and 1083 of
     * 1303 readings with depth set.
     *
     * 400 ms IS A STRESS VALUE, labelled per spec 12.2: a real 8-byte frame is about
     * 200 us at 500 kbit. It is a knob for reaching a state the identity has to
     * survive, not a rate anything on the truck does, and print_timing_config()
     * prints it with the case so it cannot be quoted as one.
     */
    static const uint32_t DIAG_IDS[] = { 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9 };
    for (size_t k = 0; k < sizeof(DIAG_IDS) / sizeof(DIAG_IDS[0]); k++)
    {
        ft_set_air_time_id(DIAG_IDS[k], 400000);
    }
    keep_alive(3000000, &bal_ctr);
    for (size_t k = 0; k < sizeof(DIAG_IDS) / sizeof(DIAG_IDS[0]); k++)
    {
        ft_set_air_time_id(DIAG_IDS[k], 0);
    }

    for (int i = 0; i < 400; i++)
    {
        ft_run(1000);
    }
    ft_set_step_hook(NULL);

    /*
     * THE POPULATION FIRST. A balance check over zero readings passes, and that
     * is indistinguishable from a clean run -- the empty-filter trap this
     * project's notes open with.
     */
    CHECK(g_bal_readings > 20,
          "only %d status readings were taken, so this case proved nothing; the "
          "step hook is not firing", g_bal_readings);
    /*
     * AND THE POPULATION HAS TO EXERCISE THE TERMS, which counting readings does not
     * establish. `held` and `depth` are 0 on an idle device, so a balance over
     * readings that never saw a frame in flight is arithmetic on zeroes. These two
     * are what make H1 fail here instead of passing.
     */
    CHECK(g_bal_held_seen > 0,
          "not one of %d readings reported held > 0, so the identity's `held` term was "
          "never exercised and a page that always reports held 0 would pass this case; "
          "the case needs frames in flight, not just readings", g_bal_readings);
    CHECK(g_bal_depth_seen > 0,
          "not one of %d readings reported depth > 0, so the `depth` term was never "
          "exercised either", g_bal_readings);
    CHECK(g_bal_failures == 0,
          "%d of %d status readings did not balance. First: %s",
          g_bal_failures, g_bal_readings, g_bal_first);

    /* And the section 11 requirement that the copy be short is a MEASUREMENT on
     * the page, not a claim in a comment -- so the field has to be there. */
    static char page[GI_STATUS_PAGE_CAP];
    gen_inhibit_get_stats_json(page, sizeof(page));
    CHECK(strstr(page, "\"snapshot_us\":") != NULL,
          "the page does not report snapshot_us, so section 11's "
          "\"short enough not to move any 5.2 timing bound\" cannot be checked");

    teardown();
    case_end();
}

/*
 * CASE 29 -- the 0x7F9 skip counts are SINCE ARMING (spec section 10).
 *
 * The reset lives in gi_reset_stats(), which only runs when arming, so gi_init()'s
 * memset hides it on the first arm and only a RE-ARM exercises it. Deleting the
 * reset left every suite green, which is how this case came to exist.
 *
 * It matters for a reader of a truck log rather than for the device: 0x7F9 is
 * differenced across time, and a count that survived a re-arm would make the first
 * page of a new drive carry the previous drive's skips.
 */
static void case_skips_are_since_arming(void)
{
    case_begin("case 29: 0x7F9 skip counts reset on a re-arm (since arming)");
    setup();
    go_live();

    /* Stall an inhibit so it is withdrawn at the VCM's next command -- the same
     * mechanism case 1 uses, and the only way to make a skip here. */
    ft_stall_id(0x051, 1);
    uint8_t ctr = 0x20;
    keep_alive(1500000, &ctr);

    const uint32_t core_w = json_u32("\"skips_withdrawn\":");
    const uint32_t core_l = json_u32("\"skips_late\":");
    CHECK(core_w + core_l >= 1,
          "no skip was produced, so this case proves nothing about the reset: %s",
          stats());

    /*
     * THE PAGE, not just the counter. Find the last 0x7F9 on the wire and check it
     * carries a non-zero count, so the reset below is tested against what a log
     * actually shows rather than against an internal.
     */
    int last_7f9 = -1;
    for (int i = 0; i < ft_wire_count(); i++)
    {
        if (ft_wire(i)->id == 0x7F9) { last_7f9 = i; }
    }
    CHECK(last_7f9 >= 0, "no 0x7F9 page reached the wire at all");
    const uint8_t *d = ft_wire(last_7f9)->data;
    const uint32_t page_w = (uint32_t)d[0] | ((uint32_t)d[1] << 8)
                          | ((uint32_t)d[2] << 16) | ((uint32_t)d[3] << 24);
    const uint32_t page_l = (uint32_t)d[4] | ((uint32_t)d[5] << 8)
                          | ((uint32_t)d[6] << 16) | ((uint32_t)d[7] << 24);
    CHECK(page_w + page_l >= 1,
          "0x7F9 reads 0/0 while the status page reports %u/%u, so the page is not "
          "carrying the counts at all", core_w, core_l);

    /* THE RE-ARM. */
    const int wire_before_rearm = ft_wire_count();
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    ft_run(10000);

    CHECK(json_u32("\"skips_withdrawn\":") == 0,
          "skips_withdrawn survived the re-arm, so 0x7F9 is not 'since arming' and "
          "a new drive's first page carries the previous drive's skips: %s",
          stats());
    CHECK(json_u32("\"skips_late\":") == 0,
          "skips_late survived the re-arm: %s", stats());

    /* And the next page on the wire says so too. */
    go_live();
    uint8_t ctr2 = 0x40;
    keep_alive(1600000, &ctr2);

    int found_after = -1;
    for (int i = wire_before_rearm; i < ft_wire_count(); i++)
    {
        if (ft_wire(i)->id == 0x7F9) { found_after = i; }
    }
    CHECK(found_after >= 0,
          "no 0x7F9 page was emitted after the re-arm, so the page-level half of "
          "this case did not run");
    const uint8_t *e = ft_wire(found_after)->data;
    uint32_t after = 0;
    for (int i = 0; i < 8; i++) { after |= e[i]; }
    CHECK(after == 0,
          "the 0x7F9 page after a re-arm is not zero: %02X%02X%02X%02X%02X%02X"
          "%02X%02X", e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7]);

    teardown();
    case_end();
}

/*
 * CASE 30 -- the RESPOND figure is consistent with its offset (spec 12.1).
 *
 * Nothing asserted the VALUE of the `response` histogram anywhere before this: the
 * three earlier mentions are all about it being empty. So a figure that is nonsense
 * relative to the configured offset -- in the one mode whose entire purpose is to
 * measure -- had no cover at all.
 *
 * WHAT THIS CASE DOES NOT COVER, AND CANNOT. It does NOT pin that the probe is
 * stamped at the HANDOVER rather than at the worker iteration entry (422293a). That
 * case was owed and this is not it. MEASURED: with the handover stamp the smallest
 * reported figure is 4003 us against a 4000 us offset; with the iteration-entry
 * stamp it is 4002 us. One microsecond -- one read of the mock clock, which is
 * `g_now++` per call -- and the mutation passes every assertion below.
 *
 * The reasoning that suggested otherwise was wrong in a way worth recording: the
 * iteration entry does NOT precede the due-time wait. gen_inhibit.c spins to the due
 * time in the REACTIVE path, before the probe is queued, so by the time any later
 * iteration reads its clock the due time is already past. Both stamps land after the
 * wait and differ only by the reads between them.
 *
 * An assertion of `>= 4003` would discriminate and would be worse than no test: it
 * would be pinned to the number of clock reads in the mock rather than to anything
 * the firmware guarantees, it would break on any unrelated edit that adds a read,
 * and it would be believed while it did. THE PROPERTY IS TESTABLE ON THE BENCH, not
 * here: the Kvaser hardware-timestamps the probe on the wire, and
 * wican_timing_bench.py already saves its raw frames per arm so the device's figure
 * can be checked against a clock that is not the device's.
 */
static void case_respond_figure_matches_its_offset(void)
{
    case_begin("case 30: the RESPOND figure is consistent with its offset");
    setup();

    const uint32_t offset = 4000;    /* well above the per-read clock granularity */
    gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, offset);
    ft_run(20000);

    uint8_t cmd[6];
    for (int i = 0; i < 12; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)i;
        feed(0x051, cmd, 6, 20000);
    }

    /*
     * Read inside the `response` object. json_u32() keys on the first match and
     * "min_us" appears in rx_gap as well, so scoping matters -- an unscoped read
     * would silently answer about the wrong histogram.
     */
    static char page[GI_STATUS_PAGE_CAP];
    gen_inhibit_get_stats_json(page, sizeof(page));
    const char *resp = strstr(page, "\"response\":{");
    CHECK(resp != NULL, "the status page has no response histogram: %s", page);
    if (resp == NULL) { teardown(); case_end(); return; }

    unsigned long count = 0, min_us = 0, max_us = 0;
    const char *p;
    if ((p = strstr(resp, "\"count\":")) != NULL)  { count = strtoul(p + 8, NULL, 10); }
    if ((p = strstr(resp, "\"min_us\":")) != NULL) { min_us = strtoul(p + 9, NULL, 10); }
    if ((p = strstr(resp, "\"max_us\":")) != NULL) { max_us = strtoul(p + 9, NULL, 10); }

    /*
     * THE POPULATION FIRST. An empty histogram satisfies any inequality below, and
     * that is indistinguishable from a pass -- the empty-filter trap this project
     * opens with.
     */
    CHECK(count >= 5,
          "only %lu probe measurements were recorded, so the inequality below "
          "proves nothing", count);

    CHECK(min_us >= offset,
          "the smallest reported RESPOND figure is %lu us against an offset of "
          "%u us. The probe is not queued until its due time has passed, so no "
          "measurement can be shorter than the offset -- a figure below it means "
          "the figure and the offset are not measuring the same interval. %s",
          min_us, offset, stats());

    /*
     * And an upper bound, so the case also fails if the stamp drifts far past the
     * handover (a later iteration, say). One VCM period of slack over the offset is
     * generous on a virtual clock and still an order of magnitude tighter than a
     * whole-iteration error.
     */
    CHECK(max_us < offset + 20000,
          "the largest reported RESPOND figure is %lu us against an offset of "
          "%u us -- too late to be the handover instant: %s",
          max_us, offset, stats());

    teardown();
    case_end();
}

/*
 * CASE 31 -- the RESPOND figure INCLUDES the wait for the transmit buffer
 * (spec 12.1, "timed at driver handover"). Design from the reviewing session.
 *
 * A diag page is given a long air time and is therefore transmitting when a probe
 * falls due. Item 4 is explicit that a frame already transmitting cannot be aborted
 * and the waiting frame waits for it, so the probe's handover is milliseconds after
 * its due time -- and a figure timed at the handover has to show that, while a
 * figure timed when the probe was QUEUED cannot.
 *
 * WHICH REGRESSIONS THIS CATCHES, measured by mutation rather than assumed:
 *
 *   stamp at the instant the probe is QUEUED (the reactive path, before the wait)
 *       CAUGHT -- the figure collapses to about the offset.
 *   stamp at the entry of the iteration that HANDS OVER (the pre-422293a code,
 *   `gi_on_tx_result(..., now, ...)`)
 *       NOT caught, and nothing in emulation catches it. The submit happens inside
 *       the same gs_tick() that advances `handed`, so that iteration's entry and the
 *       submit instant are separated only by the clock reads between them -- 1 us on
 *       a mock whose clock is `g_now++` per call. Delaying the handover moves BOTH
 *       stamps together, which is why this design does not separate them. See
 *       case 30 for the measurement.
 *
 * So this case pins the guarantee, and the 422293a stamp itself remains unpinned in
 * emulation. Both facts are recorded where someone will read them.
 */
static void case_respond_figure_includes_the_buffer_wait(void)
{
    case_begin("case 31: the RESPOND figure includes the wait for the buffer");
    setup();

    /*
     * A diag page that occupies the controller for 20 ms. The pages are the only
     * other frames RESPOND emits, so they are the only thing that can hold the
     * buffer against a probe.
     */
    const int64_t air = 20000;
    ft_set_air_time_id(0x7F1, air);

    const uint32_t offset = 1000;
    gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, offset);
    ft_run(20000);

    uint8_t cmd[6];
    for (int i = 0; i < 90; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(i & 0x0F);
        feed(0x051, cmd, 6, 20000);
    }

    static char page[GI_STATUS_PAGE_CAP];
    gen_inhibit_get_stats_json(page, sizeof(page));
    const char *resp = strstr(page, "\"response\":{");
    CHECK(resp != NULL, "the status page has no response histogram");
    if (resp == NULL) { teardown(); case_end(); return; }

    unsigned long count = 0, max_us = 0;
    const char *p;
    if ((p = strstr(resp, "\"count\":")) != NULL)  { count = strtoul(p + 8, NULL, 10); }
    if ((p = strstr(resp, "\"max_us\":")) != NULL) { max_us = strtoul(p + 9, NULL, 10); }

    /*
     * THE POPULATION, AND THE PRECONDITION. Measurements alone are not enough: the
     * case means nothing unless a probe actually waited behind a page, so the
     * figure itself is the evidence that the contention happened. A run where no
     * probe ever coincided with a page would satisfy any floor at the offset.
     */
    CHECK(count >= 10,
          "only %lu probe measurements, so this case proves nothing", count);

    CHECK(max_us > offset + air / 2,
          "the largest RESPOND figure is %lu us, with an offset of %u us and a "
          "page occupying the controller for %lld us. A probe that waited for the "
          "buffer must report that wait -- a figure near the offset means the "
          "measurement was timed when the probe was QUEUED, not when it was handed "
          "to the driver, which is not what spec 12.1 defines. %s",
          max_us, offset, (long long)air, stats());

    CHECK(max_us < offset + air * 2,
          "the largest RESPOND figure is %lu us, more than twice the page's air "
          "time past the offset -- too late to be a handover delayed by one "
          "frame: %s", max_us, stats());

    ft_set_air_time_id(0x7F1, 0);
    teardown();
    case_end();
}

/*
 * CASE 32 -- item 10 catalogue bullet 10, EMULATION HALF: the long arm.
 *
 * "No inhibit after the VCM's next command, tx_queued_behind 0, telemetry present
 * throughout." The bench half is runs/e4_long_arm_422293a.json and the 300 s
 * saturated arms; this is duration with the whole shipped stack in the loop.
 *
 * WHY HERE AND NOT IN THE REPLAY HARNESS. test/gen_inhibit_host's host_runner links
 * gen_inhibit_core.c and nothing else, so the replay scenarios -- the real captures,
 * the 300 s ones included -- never exercise the transmit scheduler. Only this harness
 * compiles gi_sched.c and gen_inhibit.c together with a wire model. "Full-replay"
 * names the bench half; the emulation half can have the duration and the full stack
 * but not the captured traffic, and saying so is better than implying this replays
 * anything.
 *
 * "THROUGHOUT" IS PER WINDOW. A total count of diag pages would pass if every page
 * came out in the first second and none afterwards -- which is the failure the
 * criterion exists for, and precisely what a starved telemetry class looks like. So
 * the arm is divided and every window must carry at least one page.
 *
 * The wire log stops recording silently at 4096 entries, so the long-run assertions
 * use the device's own counters and the wire is read only in a guarded prefix.
 */
static void case_long_arm(void)
{
    case_begin("case 32: the long arm (item 10 bullet 10, emulation half)");
    setup();
    go_live();

    static const uint8_t KEY[8]  = { 0x10 };
    static const uint8_t CONT[8] = { 11 << 2 };
    static const uint8_t SOC[8]  = { 0x4E, 0x20 };
    static const uint8_t FLT[8]  = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    static const uint8_t SHF[8]  = { 0, 0, 0, 0, 0, 0, 2 << 4 };

    /* --- a guarded prefix where the wire log is still evidence ------------- */
    const uint32_t tx_at_prefix_start = json_u32("\"tx_ok\":");
    const int wire_at_prefix_start = ft_wire_count_id(0x051);
    uint8_t ctr = 0;
    uint8_t cmd[6];
    for (int i = 0; i < 200; i++)
    {
        memcpy(cmd, VCM, sizeof(cmd));
        cmd[5] = (uint8_t)(ctr++ & 0x0F);
        feed(0x051, cmd, 6, 10000);
        if ((i % 20) == 0)
        {
            feed(0x592, KEY, 8, 200);
            feed(0x440, CONT, 8, 200);
            feed(0x411, SOC, 8, 200);
            feed(0x617, FLT, 8, 200);
            feed(0x639, SHF, 8, 200);
        }
    }
    CHECK(ft_wire_count() < 4096,
          "the wire log is already full in the prefix, so its counts below prove "
          "nothing");
    const uint32_t tx_prefix = json_u32("\"tx_ok\":") - tx_at_prefix_start;
    const int wire_prefix = ft_wire_count_id(0x051) - wire_at_prefix_start;
    /*
     * AN INEQUALITY, NOT EQUALITY, AND THE DIRECTION IS THE WHOLE POINT. tx_ok may
     * LAG the wire by one frame: the scheduler credits a departure when it observes
     * it, on a worker iteration, so a frame that has left the controller but not yet
     * been observed is on the wire and not yet counted. That is the same lag `held`
     * accounts for in spec 5.2 item 6, and the first version of this assertion
     * demanded equality and failed on it at 198 against 199.
     *
     * What must never happen is the other direction: tx_ok ahead of the wire means a
     * frame that never went out has been credited, which is the item 5 hazard.
     */
    CHECK((int)tx_prefix <= wire_prefix,
          "tx_ok moved by %u while only %d inhibit frames reached the wire: a frame "
          "that never went out has been credited", tx_prefix, wire_prefix);
    CHECK(wire_prefix - (int)tx_prefix <= 1,
          "the wire is %d frames ahead of tx_ok; at most one frame can be awaiting "
          "its completion observation, so a larger gap means departures are going "
          "uncredited", wire_prefix - (int)tx_prefix);

    /* --- the long part, on the unbounded counters -------------------------- */
    const int WINDOWS = 20;
    const int CMD_PER_WINDOW = 500;         /* 5 s of virtual truck time each */
    int quiet_windows = 0;
    uint32_t telem_prev = 0;
    {
        const char *p = strstr(stats(), "\"telemetry\":{");
        if (p != NULL) { p = strstr(p, "\"sent\":"); }
        if (p != NULL) { telem_prev = (uint32_t)strtoul(p + 7, NULL, 10); }
    }

    for (int w = 0; w < WINDOWS; w++)
    {
        for (int i = 0; i < CMD_PER_WINDOW; i++)
        {
            memcpy(cmd, VCM, sizeof(cmd));
            cmd[5] = (uint8_t)(ctr++ & 0x0F);
            feed(0x051, cmd, 6, 10000);
            if ((i % 20) == 0)
            {
                feed(0x592, KEY, 8, 200);
                feed(0x440, CONT, 8, 200);
                feed(0x411, SOC, 8, 200);
                feed(0x617, FLT, 8, 200);
                feed(0x639, SHF, 8, 200);
            }
        }
        uint32_t telem_now = telem_prev;
        const char *p = strstr(stats(), "\"telemetry\":{");
        if (p != NULL) { p = strstr(p, "\"sent\":"); }
        if (p != NULL) { telem_now = (uint32_t)strtoul(p + 7, NULL, 10); }
        if (telem_now == telem_prev) { quiet_windows++; }
        telem_prev = telem_now;
    }

    /* 3. TELEMETRY PRESENT THROUGHOUT. */
    CHECK(quiet_windows == 0,
          "%d of %d five-second windows carried NO diag page. A total would have "
          "hidden this: telemetry present at the start and absent later is exactly "
          "what the criterion is about. %s", quiet_windows, WINDOWS, stats());

    /* 1. NO INHIBIT AFTER THE VCM'S NEXT COMMAND. */
    CHECK(json_u32("\"skipped\":") == 0,
          "the long arm skipped %u inhibit frames: %s",
          json_u32("\"skipped\":"), stats());
    CHECK(json_u32("\"late_on_wire\":") == 0,
          "the long arm reported %u late frames: %s",
          json_u32("\"late_on_wire\":"), stats());
    CHECK(json_u32("\"order_violations\":") == 0,
          "the deadline did not run before every answer: %s", stats());

    /* 2. tx_queued_behind STRUCTURALLY ZERO (spec 5.2 item 3). */
    CHECK(json_u32("\"tx_queued_behind\":") == 0,
          "tx_queued_behind is %u over the long arm: the scheduler handed a second "
          "frame to the driver while one was still there: %s",
          json_u32("\"tx_queued_behind\":"), stats());

    /*
     * AND THE POPULATION, last so the figures above are not a pass over nothing.
     * 10,200 commands at 10 ms is 102 s of virtual truck time.
     */
    const uint32_t tx_total = json_u32("\"tx_ok\":");
    CHECK(tx_total > 9000,
          "only %u inhibit frames went out over the whole arm, so this case did not "
          "run long enough to be a long arm: %s", tx_total, stats());
    /*
     * check_tx_ok_invariant() is deliberately NOT called here: it is bounded by the
     * frame logs and this arm runs past them. The counter-based assertions above are
     * what hold over a long arm, and the guarded prefix is where the wire was
     * compared against tx_ok while that comparison still meant something.
     */

    teardown();
    case_end();
}


/* ------------------------------------------------ replaying a real capture --- */

/*
 * SPEC 5.2 ITEM 10 (2026-10-03): "Emulation replays recorded traffic for its stimulus,
 * not for bus load." This is the reader. It gives real command timing and jitter, real
 * arrival ordering, real arm and key transitions, and the receive-side pressure of
 * dense traffic. IT GIVES NO BUS LOAD: controller_advance() completes our frames from
 * our own queue head plus air_time_for(id), and a delivered frame enters that
 * calculation nowhere. A load-dependent criterion needs the bench.
 *
 * THE PIN (spec 12.4, widened 2026-10-03): the scenario is rebuilt from a capture
 * through the project's parser and is not in git, so the case records a checksum of
 * what it ran on. FNV-1a 64 rather than sha256 -- it runs here in C, the spec says
 * "checksum", and FNV-1a is already what this firmware hashes GIT_SHA with for 0x7F3.
 * Stimulus lines FAIL on a mismatch; comment lines are a notice, exactly as
 * run_tests.py's pin behaves.
 */
/*
 * The shim's own stimulus first, then the host harness's. Two directories because
 * run_tests.py enumerates everything in ITS scenarios/ -- a file there with no golden
 * is reported as missing one, and the all-IDs long-arm scenario made host_runner exit
 * 2 on the length of its per-ID comment line. The fallback keeps the eight existing
 * replays usable from here without copying them.
 */
#define SCN_DIR  "scenarios/"
#define SCN_DIR2 "../gen_inhibit_host/scenarios/"
#define PIN_FILE "stimulus.fnv"

static uint64_t fnv1a_init(void) { return 1469598103934665603ULL; }

static uint64_t fnv1a_add(uint64_t h, const char *s)
{
    while (*s)
    {
        h ^= (unsigned char)*s++;
        h *= 1099511628211ULL;
    }
    return h;
}

typedef struct
{
    long     frames;            /* `f` lines delivered */
    /*
     * HOW FAR THE REPLAY FELL BEHIND, in microseconds, not how often the clock had
     * moved past a target. The capture delivers many frames at the same instant, so
     * "already passed" fires for all but the first of each burst -- 463,307 of 706,283
     * steps, nearly all of them behind by a microsecond or two. The maximum is what
     * says whether recorded timing is being reproduced.
     */
    int64_t  max_late_us;
    int64_t  max_late_at_us;    /* capture offset where the worst one happened */
    long     late_over_1ms;     /* how many excursions exceeded a millisecond */
    long     max_late_frame;    /* frame index of the worst one */
    /*
     * WHAT THE WORKER'S TURNS COST, summed, with the count beside it. Measured rather
     * than inferred: the per-turn figure is these two divided, not a maximum reread as
     * an average, which is the mistake that sent the whole clock investigation wrong.
     */
    int64_t  drain_cost_us;
    long     drain_turns;
    int64_t  span_us;           /* last frame's offset */
    uint64_t stim_fnv;
    uint64_t head_fnv;
} scn_result_t;

static uint8_t hexnib(char c)
{
    if (c >= '0' && c <= '9') { return (uint8_t)(c - '0'); }
    if (c >= 'a' && c <= 'f') { return (uint8_t)(c - 'a' + 10); }
    if (c >= 'A' && c <= 'F') { return (uint8_t)(c - 'A' + 10); }
    return 0;
}

/*
 * Replay `name`.scn. Returns false if the file is missing, which the caller must treat
 * as a failure rather than an empty run -- a replay case over no frames would satisfy
 * every criterion it checks.
 */
static bool replay_scn(const char *name, scn_result_t *out,
                       int64_t window_us, void (*on_window)(int w))
{
    char path[512];
    snprintf(path, sizeof(path), "%s%s.scn", SCN_DIR, name);
    FILE *fh = fopen(path, "r");
    if (fh == NULL)
    {
        snprintf(path, sizeof(path), "%s%s.scn", SCN_DIR2, name);
        fh = fopen(path, "r");
    }
    if (fh == NULL) { return false; }

    memset(out, 0, sizeof(*out));
    out->stim_fnv = fnv1a_init();
    out->head_fnv = fnv1a_init();

    const int64_t base = ft_now();
    int next_window = 1;
    static char line[512];
    while (fgets(line, (int)sizeof(line), fh) != NULL)
    {
        if (line[0] == '#')
        {
            out->head_fnv = fnv1a_add(out->head_fnv, line);
            continue;
        }
        if (line[0] == 'm')                 /* mode <at_us> <mode> <offset_us> */
        {
            long at = 0, mode = 0, off = 0;
            if (sscanf(line, "mode %ld %ld %ld", &at, &mode, &off) == 3)
            {
                out->stim_fnv = fnv1a_add(out->stim_fnv, line);
                const int64_t target = base + at;
                if (target > ft_now()) { ft_run(target - ft_now()); }
                gen_inhibit_set_mode((gen_inhibit_mode_t)mode, (uint32_t)off);
            }
            continue;
        }
        if (line[0] != 'f') { continue; }

        long us = 0, dlc = 0;
        char idbuf[16], hexbuf[33];
        if (sscanf(line, "f %ld %15s %ld %32s", &us, idbuf, &dlc, hexbuf) != 4)
        {
            continue;
        }
        out->stim_fnv = fnv1a_add(out->stim_fnv, line);

        const uint32_t id = (uint32_t)strtoul(idbuf, NULL, 16);
        uint8_t data[8] = { 0 };
        const size_t hl = strlen(hexbuf);
        for (size_t i = 0; i + 1 < hl && i / 2 < 8; i += 2)
        {
            data[i / 2] = (uint8_t)((hexnib(hexbuf[i]) << 4) | hexnib(hexbuf[i + 1]));
        }

        const int64_t target = base + us;
        if (target > ft_now())
        {
            ft_run(target - ft_now());
        }
        else
        {
            const int64_t behind = ft_now() - target;
            if (behind > 1000) { out->late_over_1ms++; }
            if (behind > out->max_late_us)
            {
                out->max_late_us = behind;
                out->max_late_at_us = us;
                out->max_late_frame = out->frames;
            }
        }
        ft_deliver(id, data, (uint8_t)(dlc > 8 ? 8 : dlc));
        out->frames++;
        out->span_us = us;

        /*
         * DRAIN BEFORE TIME MOVES ON. The worker consumes one frame per turn, and the
         * capture delivers 706,283 frames at 242,977 distinct timestamps -- bursts of
         * up to 12. Advancing after one turn left the rest of each burst in the queue
         * to be overwritten, which is why the core saw 242,974 frames: exactly one per
         * timestamp.
         *
         * The condition matters as much as the loop. A turn on a NON-EMPTY queue
         * returns a frame immediately and costs nothing; a turn on an empty one makes
         * the worker block in twai_receive and burn a full receive timeout of virtual
         * time. An unconditional turn per frame reported 353,141 diag pages and
         * 140,959 s of drift over a 300 s capture.
         */
        /*
         * DRAINS TO EMPTY, which it could not safely do before the mock was fixed.
         *
         * It used to leave ONE frame pending on purpose: draining to empty let the
         * worker re-enter a blocking twai_receive, and fake_twai then advanced g_now
         * to THAT call's deadline -- GEN_INHIBIT_RX_TIMEOUT_MS, 200 ms -- "if nothing
         * else does". The clock jumped 200 ms, the next ~470 frames of capture had
         * targets already in the past and arrived as one clump of ~20 commands inside
         * 50 us, and the device tripped 7 on four phantom skips. The rx_gap histogram
         * showed it: 28,495 gaps under 50 us against 1,496 over 12.8 ms, with a
         * correct mean of 10,003 us.
         *
         * THAT COST A CRITERION. `ontime` is credited only when the receive queue is
         * empty at the completion, so a frame deliberately left pending held
         * rx_backlog >= 1 at most completions BY CONSTRUCTION and the verified-on-time
         * proportion sat at 7,011 of 29,977 against the bench's 8,976 of 8,983.
         *
         * A blocked receive now advances the clock no further than the turn the test
         * granted it (see g_turn_grant in fake_twai.c), so emptying the queue is safe
         * and the device is asked the same question the bench asks it.
         */
        while (ft_rx_pending() > 0)
        {
            const int64_t before = ft_now();
            ft_run(0);
            out->drain_cost_us += ft_now() - before;
            out->drain_turns++;
        }

        /* Window boundaries by REPLAY time, not by frame count: the criterion is
         * about telemetry over the arm's duration, and the frame rate varies. */
        if (on_window != NULL && window_us > 0
            && us >= (int64_t)next_window * window_us)
        {
            on_window(next_window - 1);
            next_window++;
        }
    }
    fclose(fh);
    return true;
}

/*
 * Check the replayed stimulus against the pin, or write it. The pin lives beside the
 * harness because a replay-driven case has no golden to hang a checksum on -- spec 12.4
 * as widened on 2026-10-03.
 */
static bool g_pin_write_mode;

static void pin_stimulus(const char *name, const scn_result_t *r)
{
    char want_stim[32] = { 0 }, want_head[32] = { 0 };
    char nm[128];
    bool found = false;
    FILE *fh = fopen(PIN_FILE, "r");
    if (fh != NULL)
    {
        static char line[256];
        while (fgets(line, (int)sizeof(line), fh) != NULL)
        {
            if (line[0] == '#') { continue; }
            if (sscanf(line, "%31s %31s %127s", want_stim, want_head, nm) == 3
                && strcmp(nm, name) == 0)
            {
                found = true;
                break;
            }
        }
        fclose(fh);
    }

    char have_stim[32], have_head[32];
    snprintf(have_stim, sizeof(have_stim), "%016llx",
             (unsigned long long)r->stim_fnv);
    snprintf(have_head, sizeof(have_head), "%016llx",
             (unsigned long long)r->head_fnv);

    if (g_pin_write_mode)
    {
        FILE *w = fopen(PIN_FILE, "a");
        if (w != NULL)
        {
            fprintf(w, "%s  %s  %s\n", have_stim, have_head, name);
            fclose(w);
        }
        printf("           pinned %s stimulus=%s header=%s\n",
               name, have_stim, have_head);
        return;
    }

    CHECK(found,
          "%s is not pinned. A replay-driven case records a checksum of the stimulus "
          "it ran on (spec 12.4): run `./shim_test --pin-stimulus` once, after "
          "checking the scenario is the one you mean", name);
    if (!found) { return; }
    CHECK(strcmp(have_stim, want_stim) == 0,
          "%s STIMULUS does not match the pin: ran %s, pinned %s. The scenario was "
          "regenerated and the frames differ, so this case is not measuring what it "
          "was written against. replay_stimulus_delta.py shows what moved",
          name, have_stim, want_stim);
    if (strcmp(have_head, want_head) != 0)
    {
        printf("           NOTE: %s header text differs from the pin (frames "
               "identical) -- a coverage note may have been reworded or lost\n",
               name);
    }
}

/*
 * CASE 33 -- item 10 bullet 10's EMULATION HALF, as a replay of real traffic
 * (spec 5.2 item 10, ruled 2026-10-03).
 *
 * 300 s of scottsvalley_armable_300s.log -- the capture the bench long arm replays --
 * through core + gi_sched + the device HAL, at the recorded timing, reproduced to
 * within 6 us. Case 32 is the synthetic equivalent and stays; this one exists because
 * a synthetic run misses real command timing and jitter, real arrival ordering, and
 * the arm and key transitions the truck actually produced.
 *
 * IT REPLAYS ALL 706,283 FRAMES, every ID, and reproduces the recorded timing to
 * within 63 us with no excursion over a millisecond. Getting there took three wrong
 * diagnoses of mine, and the one that matters is recorded in the drain loop below: the
 * worker consumes ONE frame per turn, and draining to EMPTY let it re-enter a blocking
 * receive that advances the clock 200 ms. The mock clock itself is cheap -- 3.5 us per
 * turn, measured by summing the turns rather than reading a maximum as an average,
 * which is how I got "1235 us per frame" wrong and sent a spec requirement after a
 * clock that was fine.
 *
 * THE SPEC'S FIVE ACCEPTANCE CRITERIA for the emulation half, in its order (:1035):
 * every ID; mock loss 0; verified-on-time comparable to the bench; every existing case
 * passes; the latest mutation rounds still catch what they caught. The first four hold.
 * The fifth is the reviewing session's to score, and it is NOT a number this file can
 * claim -- H1 went from caught to surviving once this case's clock behaviour changed,
 * which is exactly what that criterion exists to notice.
 *
 * THE VERIFIED-ON-TIME ONE -- the THIRD, not the fifth, which an earlier version of this
 * comment got wrong -- took a mock fix, and it is worth knowing which way it now leans. `ontime`
 * is credited only when the receive queue is empty at the completion. The drain used
 * to leave one frame pending on purpose -- the only way to stop the 200 ms clock jump
 * -- so rx_backlog >= 1 at most completions BY CONSTRUCTION and the proportion sat at
 * 7,011 of 29,977 against the bench's 8,976 of 8,983. Since a blocked receive stops at
 * the turn's grant (g_turn_grant in fake_twai.c) the drain empties the queue and the
 * proportion is 29,977 of 29,977.
 *
 * READ THAT NUMBER WITH CARE: IT IS NOW CONSTRUCTED THE OPPOSITE WAY. The drain empties
 * the queue before each completion is judged, so rx_backlog is 0 by construction and
 * this case can no longer produce an unverified completion at all. The bench produces 7
 * of them, and the device's unverified accounting -- the count and its largest backlog
 * -- therefore has NO cover HERE any more, where it previously had 22,966 samples.
 * (Those counters are status-page telemetry and feed no trip: rx_backlog's only effect
 * is the ontime / ontime_unverified / unverified_max_backlog split in
 * note_left_controller. An earlier version of this comment said trip 7 read them, which
 * is wrong -- trip 7 reads skips, TX_FAILED and refusal.) 100 % verified is not a better
 * result than the bench's 99.92 %; it is a different question being asked. Case 34
 * builds the non-empty-backlog condition this one cannot.
 *
 * WHAT IT DOES NOT TEST: bus load. Spec item 10 is explicit -- a replay gives stimulus
 * and no load, because controller_advance() completes our frames from our own queue
 * head plus air_time_for(id) and a delivered frame enters that nowhere. The timing
 * line printed with this case shows the defaults, which is the honest declaration: no
 * contention was configured. A load-dependent criterion needs the bench.
 */
static int g_la_quiet_windows;
static uint32_t g_la_telem_prev;
static int g_la_windows_seen;

static uint32_t telem_sent_now(void)
{
    const char *p = strstr(stats(), "\"telemetry\":{");
    if (p != NULL) { p = strstr(p, "\"sent\":"); }
    return (p != NULL) ? (uint32_t)strtoul(p + 7, NULL, 10) : 0;
}

static void la_window(int w)
{
    (void)w;
    const uint32_t now = telem_sent_now();
    if (now == g_la_telem_prev) { g_la_quiet_windows++; }
    g_la_telem_prev = now;
    g_la_windows_seen++;
}

static void case_long_arm_replay(void)
{
    case_begin("case 33: the long arm, replaying real traffic (bullet 10)");
    case_budget(240, "replays 706,283 frames of a real 300 s capture");
    setup();

    g_la_quiet_windows = 0;
    g_la_windows_seen = 0;
    g_la_telem_prev = 0;

    scn_result_t r;
    const bool ok = replay_scn("replay-longarm-300s-all", &r, 15000000, la_window);
    CHECK(ok,
          "test/gen_inhibit_shim/scenarios/replay-longarm-300s-all.scn is "
          "missing. It is generated, not in git -- see this harness's README "
          "for the from_capture.py line that builds it, and pass --all-ids");
    if (!ok) { teardown(); case_end(); return; }

    /*
     * THE POPULATION AND THE PIN, before any criterion. A replay over no frames
     * satisfies everything below, and a replay over the WRONG frames satisfies it
     * just as well while measuring something else.
     */
    CHECK(r.frames > 700000,
          "only %ld frames were replayed; the capture has 706,283, so this did not "
          "run the arm it claims to", r.frames);
    CHECK(r.span_us > 295000000,
          "the replay spans only %lld us; the capture is 300 s, so the arm is short",
          (long long)r.span_us);
    pin_stimulus("replay-longarm-300s-all", &r);

    /*
     * The clock advances on every read, so a frame's instant can already have passed
     * by the time we reach it. A few is noise; many would mean the replay is
     * compressing its own timeline and the "real timing" claim is false.
     */
    CHECK(r.max_late_us < 20000,
          "the replay fell up to %lld us behind the recorded timing, which is more "
          "than a VCM period -- the stimulus is being compressed rather than "
          "reproduced", (long long)r.max_late_us);

    /* 3. TELEMETRY PRESENT THROUGHOUT, per 15 s window. */
    CHECK(g_la_windows_seen >= 15,
          "only %d windows were sampled over a 300 s arm", g_la_windows_seen);
    CHECK(g_la_quiet_windows == 0,
          "%d of %d windows carried no diag page: telemetry present at the start and "
          "absent later is what this criterion is about, and a total would hide it",
          g_la_quiet_windows, g_la_windows_seen);

    /* 1. NO INHIBIT AFTER THE VCM'S NEXT COMMAND. */
    CHECK(json_u32("\"late_on_wire\":") == 0,
          "%u inhibit frames went out late over the replay: %s",
          json_u32("\"late_on_wire\":"), stats());
    CHECK(json_u32("\"order_violations\":") == 0,
          "the deadline did not run before every answer: %s", stats());

    /* 2. tx_queued_behind STRUCTURALLY ZERO (spec 5.2 item 3). */
    CHECK(json_u32("\"tx_queued_behind\":") == 0,
          "tx_queued_behind is %u: the scheduler handed a second frame to the driver "
          "while one was still there: %s",
          json_u32("\"tx_queued_behind\":"), stats());

    /*
     * 4. THE VERIFIED-ON-TIME FIGURE, ASSERTED RATHER THAN PRINTED.
     *
     * This is the spec's THIRD acceptance criterion for the emulation half (~:1037),
     * not the fifth -- the fifth is the mutation rounds. It was only ever printed
     * here, which is why two mutations survived while the suite stayed green: the
     * drain reverted to leaving one frame pending (ontime 7,011 + 22,966), and both
     * backlog call sites forced to 1 (ontime 0 + unverified 29,977). A figure nobody
     * asserts is not a criterion, it is a log line.
     *
     * The construction makes it exactly checkable: the reader drains the receive
     * queue to EMPTY before each completion is judged, so every completion must be
     * verifiable and `unverified` must be 0. If that ever stops holding, either the
     * drain changed or the backlog stopped reaching the scheduler -- both of which
     * are things this case should fail for.
     */
    const uint32_t la_ontime = json_u32("\"ontime\":");
    const uint32_t la_unver  = json_u32("\"ontime_unverified\":");
    const uint32_t la_sent   = json_u32("\"tx_ok\":");
    CHECK(la_unver == 0,
          "%u of %u completions could not be verified on time, but the reader drains "
          "the receive queue empty before judging one, so the backlog should be 0 at "
          "every completion: either the drain no longer empties it or msgs_to_rx is "
          "not reaching the scheduler: %s",
          la_unver, la_ontime + la_unver, stats());
    CHECK(la_ontime == la_sent,
          "%u completions were credited on time against %u frames transmitted; under "
          "an empty-queue drain every transmitted inhibit must be verifiable: %s",
          la_ontime, la_sent, stats());

    /*
     * COMMAND LOSS IS BOUNDED, NOT IGNORED. The bench receives every command with
     * ctr_bad 0; here about 8 % of the counter steps are bad because the mock worker
     * cannot always keep up and the receive queue overflows in bursts. That does not
     * invalidate the criteria above -- they are about what WE transmitted, and a
     * missed command simply means one fewer answer -- but it is a fidelity gap that
     * must not be allowed to grow quietly into a case that replays almost nothing.
     */
    const uint32_t ctr_bad = json_u32("\"ctr_bad\":");
    const uint32_t ctr_ok = json_u32("\"ctr_ok\":");
    CHECK(ctr_bad < (ctr_ok + ctr_bad) / 5,
          "%u of %u counter steps were bad -- the harness is losing more than a fifth "
          "of the commands, so this replay is no longer reproducing the capture",
          ctr_bad, ctr_ok + ctr_bad);

    /*
     * AND THE DEVICE ACTUALLY TRANSMITTED. Real traffic drives the gate, so a capture
     * whose window never satisfies the interlocks would run all 706,283 frames and
     * assert nothing -- every criterion above is vacuous at tx_ok 0.
     */
    const uint32_t tx = json_u32("\"tx_ok\":");
    CHECK(tx > 25000,
          "only %u inhibit frames went out over 300 s of real traffic (the capture "
          "carries 29,992 commands on 0x051), so the gate did not stay open and the "
          "criteria above were not exercised: %s", tx, stats());

    /*
     * REPORT WHAT IT RAN, not just that it passed. A case this large whose only
     * output is a pass tells a reader nothing about whether the arm was healthy,
     * and these figures are the first thing anyone comparing it against the bench
     * long arm will want.
     */
    printf("           replayed %ld frames over %lld s, max %lld us behind "
           "(at capture offset %lld us, frame %ld; %ld excursions over 1 ms)\n",
           r.frames, (long long)(r.span_us / 1000000),
           (long long)r.max_late_us, (long long)r.max_late_at_us,
           r.max_late_frame, r.late_over_1ms);
    printf("           worker turns %ld costing %lld us total, %.1f us per turn\n",
           r.drain_turns, (long long)r.drain_cost_us,
           r.drain_turns ? (double)r.drain_cost_us / (double)r.drain_turns : 0.0);
    printf("           tx_ok %u, ctr_ok %u, ctr_bad %u, ontime %u + unverified "
           "%u, diag %u\n",
           json_u32("\"tx_ok\":"), json_u32("\"ctr_ok\":"),
           json_u32("\"ctr_bad\":"), json_u32("\"ontime\":"),
           json_u32("\"ontime_unverified\":"), telem_sent_now());

    teardown();
    case_end();
}

/*
 * CASE 27 -- D8's wiring: a mode change while a frame is really in the
 * controller.
 *
 * sched_test pins gs_rearm() itself. This pins that gen_inhibit.c CALLS it: the
 * reviewing session's round 13 swapped gs_rearm() for gs_init() at the mode
 * change and every suite stayed green, here included.
 *
 * A diag page with a long air time is in the controller when the mode changes. If
 * the scheduler forgets it, the next frame is handed to twai_transmit() while the
 * old one is still there -- which is the FIFO inversion item 3 exists to prevent
 * -- and the departure of the old frame is credited to the new one.
 *
 * ft_ll_removed() and the wire log are what can see this; tx_ok alone cannot,
 * because a mis-credited completion still looks like a completion.
 */
static void case_mode_change_keeps_the_held_frame(void)
{
    case_begin("case 27: a mode change does not forget a frame in the driver");
    setup();
    go_live();

    /*
     * Long enough that the page is unambiguously still in the driver when the
     * mode changes below: the diag IDs are the only frames whose air time a test
     * can stretch without touching the inhibit path.
     */
    ft_set_air_time_id(0x7F1, 120000);
    ft_set_air_time_id(0x7F2, 120000);
    ft_set_air_time_id(0x7F3, 120000);
    ft_set_air_time_id(0x7F8, 120000);

    /*
     * DRIVEN TO THE CONDITION, NOT TIMED INTO IT. The mode change has to land
     * while a page is genuinely in the driver, and "queued but not yet on the
     * wire" is that condition exactly. A fixed keep_alive would make this case
     * pass or fail on scheduling luck.
     */
    uint8_t ctr = 0x70;
    bool in_driver = false;
    for (int i = 0; i < 60 && !in_driver; i++)
    {
        keep_alive(10000, &ctr);
        for (unsigned k = 0; k < sizeof(DIAG) / sizeof(DIAG[0]); k++)
        {
            if (ft_sent_count_id(DIAG[k]) > ft_wire_count_id(DIAG[k]))
            {
                in_driver = true;
                break;
            }
        }
    }
    CHECK(in_driver,
          "no diag page was ever in the driver un-completed, so the mode change "
          "below cannot land on one and the case is vacuous: %s", stats());

    /*
     * THE MODE CHANGE, with the page still in the controller. Back into INHIBIT,
     * which is the INHIBIT->INHIBIT re-arm gi_sched.h names: the counters and
     * queues are per-arm, what the hardware holds is not.
     */
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    keep_alive(400000, &ctr);

    /*
     * ITEM 3, AND IT IS THE WHOLE CASE. fake_twai models the driver's real FIFO,
     * so a frame handed over while the controller still holds one is ACCEPTED and
     * queues behind it -- no refusal, no lost frame, no wire violation, and the
     * inhibit delayed by the page's air time. The first version of this case
     * asserted all the things that stay true through that, and D8a survived it.
     */
    CHECK(ft_max_inflight() <= 1,
          "%d of our frames were in the driver at once. A mode change that "
          "forgets the frame the controller is holding hands the next one over "
          "immediately, and twai_transmit queues it BEHIND the old one -- the "
          "priority inversion spec 5.2 item 3 exists to prevent (defect D8): %s",
          ft_max_inflight(), stats());
    CHECK(ft_wire_violations() == 0,
          "the driver was handed a frame that must never reach the wire");
    teardown();
    case_end();
}

/*
 * CASE 34 -- A KNOWN RECEIVE BACKLOG IS COUNTED, AND AT BOTH CALL SITES.
 *
 * Spec 5.2's on-time counts are split: `ontime` is a completion the device could
 * verify because its receive queue was empty at that instant, `ontime_unverified` one
 * it could not, with `unverified_max_backlog` the worst backlog behind one. The split
 * is the only thing rx_backlog does (gi_sched.c, note_left_controller) -- it feeds no
 * trip and no abort. It is status-page telemetry, and its value is that a reader can
 * tell "we answered in time" from "we think we answered in time".
 *
 * WHY THIS CASE EXISTS. The reviewing session's round 15 showed that zeroing the
 * backlog at EITHER call site left the entire shim suite green:
 *   U1  gs_command_received(..., 0)  -- survived
 *   U2  gs_tick(..., 0)              -- survived
 * Nothing checked that can_msgs_to_rx() reaches the scheduler at all. Case 33 cannot
 * see it: it drains the queue empty before judging a completion, so a backlog forced
 * to zero is indistinguishable from the truth there. This case builds the opposite
 * condition on purpose.
 *
 * AN ALWAYS-ZERO BACKLOG IS THE DANGEROUS DIRECTION. It makes every completion read as
 * verified, which is the flattering answer: a log would claim the device confirmed
 * every answer against an empty queue when it had frames waiting and could confirm
 * nothing.
 *
 * TWO PHASES, one per call site. note_left_controller() is reached from gs_tick() on
 * the periodic path and from gs_command_received() when the next command is dequeued,
 * and one assertion cannot tell which credited a completion -- so each phase is
 * arranged to be noticed by one of them and asserted on its own.
 */
static void case_backlog_reaches_the_scheduler(void)
{
    case_begin("case 34: a known receive backlog is counted (both call sites)");
    setup();
    go_live();

    static const uint8_t FLT[8] = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
    uint8_t cmd[6];
    uint8_t ctr = 0x60;

    const uint32_t unver_start = json_u32("\"ontime_unverified\":");

    /*
     * PHASE A -- the completion is noticed on the PERIODIC path (gs_tick).
     *
     * Queue a command with filler BEHIND it, so the worker takes the 0x051 first and
     * frames are still waiting when our answer completes. The answer's air time is
     * the default 200 us, far shorter than the gap to any next command, so the tick
     * is what finds it.
     */
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    for (int i = 0; i < 6; i++) { ft_deliver(0x617, FLT, 8); }

    /*
     * Turns, not time: each one lets the worker take exactly one frame, so the
     * backlog falls by one per turn and is still non-zero when the answer completes.
     */
    for (int i = 0; i < 8; i++) { ft_run(500); }

    const uint32_t unver_a = json_u32("\"ontime_unverified\":");
    const uint32_t backlog_a = json_u32("\"unverified_max_backlog\":");
    CHECK(unver_a > unver_start,
          "a command was answered with %d frames still queued behind it and the "
          "device counted the completion as verified on time; msgs_to_rx is not "
          "reaching gs_tick: %s", 6, stats());
    /*
     * THE DEPTH, EXACTLY, not just "non-zero". A mutation flattening the backlog to 1
     * at both call sites passed an earlier `>= 1` here: it kept the unverified verdict
     * while discarding the depth, which is the only thing unverified_max_backlog is
     * for -- how far behind the dequeue actually was.
     *
     * 5 = the 6 filler frames delivered behind the command, less the one consumed by
     * the turn that carries the credit. If the harness's turn accounting changes, this
     * is to be re-derived from the new accounting and re-stated, NOT relaxed to an
     * inequality; the inequality is what let the mutation through.
     */
    CHECK(backlog_a == 5,
          "the largest unverified backlog reads %u, expected 5 (6 frames delivered "
          "behind the command, less the one the crediting turn consumed); the depth is "
          "not being carried faithfully: %s", backlog_a, stats());

    /*
     * PHASE B -- a completion credited at the step where the NEXT COMMAND is dequeued,
     * with frames queued behind it.
     *
     * WHAT THIS PHASE DOES NOT DO, stated because an earlier version of this comment
     * claimed it did: it does not isolate gs_command_received's call site, and it
     * cannot. THE ORDER IS sched_advance() AT gen_inhibit.c:1234 FIRST, THEN
     * gs_command_received() AT :1257 -- so the tick inside sched_advance() sees every
     * completion before the command path does. I originally had that the other way
     * round and built this phase on it, expecting a frame completing while the worker
     * was parked to be seen first by gs_command_received.
     *
     * Measured, and the trace is IDENTICAL on a clean build and with
     * gs_command_received's backlog forced to 0:
     *
     *   t=414262  unver=12  pending=1   command delivered
     *   t=414771  unver=12  pending=0   our answer handed to the controller
     *   t=415783  unver=12  pending=0   worker parked; the 200 us air time elapses
     *   t=415789  unver=12  pending=7   next command + 6 filler delivered
     *   t=416298  unver=13  pending=6   the completion is credited HERE
     *
     * THE CREDIT LANDS AT THE DEQUEUE STEP, AND gs_tick IS WHAT TAKES IT -- because of
     * the call ORDER, which I first had backwards. gen_inhibit.c:1234 runs
     * sched_advance(t_rx), and that tick credits the completion, BEFORE
     * gs_command_received at :1257; the comment at :1228 states it outright ("collect a
     * completion before anything judges the frame"). fake_twai's controller moves only
     * at turn boundaries, so by the time :1257 runs, `held` is already false and its
     * completion branch is never entered at all.
     *
     * So there is no mystery and nothing "frees the frame in the gap" -- there is no
     * gap. An earlier version of this comment said the frame became free between the
     * two calls and that what freed it was not established. That was the reversed
     * order, and it is wrong.
     *
     * CONSEQUENCE: zeroing the backlog at gs_command_received's call site is a
     * DOCUMENTED SURVIVOR BY CALL ORDER, not an open gap -- that branch is unreachable
     * from E1, and on the device it is entered only when the controller finishes a
     * frame in the microseconds between the two status reads. Zeroing it at gs_tick's
     * call site IS caught, here and in phase A.
     *
     * A hook completing the head on the Nth twai_get_status_info would reach it, and is
     * deliberately NOT added: it would exist only to reach a branch no test can
     * otherwise reach, which shapes the model to the test rather than to the device.
     */
    const uint32_t unver_b_start = json_u32("\"ontime_unverified\":");

    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    ft_run(500);        /* dequeued; our answer is handed to the controller */
    ft_run(1000);       /* that tick saw it outstanding; the worker then parks in the
                         * receive and the 200 us air time elapses while it is there */

    /*
     * The next command, with filler behind it so the backlog is non-zero. The parked
     * receive returns this inside the iteration whose tick has already run, so
     * gs_command_received is the first to see the completion.
     */
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    for (int i = 0; i < 6; i++) { ft_deliver(0x617, FLT, 8); }
    ft_run(500);

    CHECK(json_u32("\"ontime_unverified\":") > unver_b_start,
          "an answer that completed while the worker was parked, and was credited at "
          "the step that dequeued the next command, was booked verified on time with "
          "6 frames still queued behind it; the backlog is not reaching the scheduler "
          "on that path: %s",
          stats());

    /*
     * AND THE PAGE STILL BALANCES through all of it -- the terms above are only
     * meaningful if the identity they sit in holds.
     */
    g_bal_readings = 0;
    g_bal_failures = 0;
    g_bal_first[0] = 0;
    balance_step_hook();
    CHECK(g_bal_failures == 0,
          "the status page does not balance after a backlogged arm: %s", g_bal_first);

    teardown();
    case_end();
}


/*
 * CASES 35 AND 36 -- spec section 11, "Uptime and reset reason are reported"
 * (user, 2026-10-09).
 *
 * WHY A SHIM CASE AT ALL, when the tester checks both fields on hardware. The
 * bench can see that uptime rises and that a forced reset reads as a drop; what
 * it cannot practically reach is the NAMING, because producing a BROWNOUT or a
 * CPU_LOCKUP on demand means abusing the supply or crashing the chip. So the
 * device proves the field moves and these two prove it is spelled right, which
 * is the half a bench run would quietly skip.
 *
 * The unknown-value case is the one that earns its keep. `reset_reason_name()`
 * has no `default:` that maps to a name, deliberately -- a reason ESP-IDF adds
 * later must surface as a number rather than be filed under "UNKNOWN" -- and
 * that is a rule about code nobody has written yet, so nothing on the bench can
 * ever exercise it. If someone adds a tidy-looking `default: "UNKNOWN"`, this is
 * what objects.
 */
static void case_uptime_rises_and_the_reason_is_named(void)
{
    case_begin("case 35: uptime rises and the reset reason is named (spec 11)");
    ft_set_reset_reason(ESP_RST_BROWNOUT);
    setup();
    go_live();
    ft_run(20000);

    CHECK(json_has("\"reset_reason\":\"BROWNOUT\""),
          "the page does not name this boot's reset reason BROWNOUT. Without "
          "the name, an uptime drop says a reboot happened and nothing says "
          "whether the supply or the firmware caused it -- which is the "
          "question spec 11 added the field for");

    const uint32_t first = json_need("\"uptime_ms\":");
    ft_run(50000);
    const uint32_t second = json_u32("\"uptime_ms\":");
    CHECK(second > first,
          "uptime_ms did not rise across 50 ms of virtual time (%lu, then "
          "%lu). A field that does not rise cannot make a DROP mean a reboot, "
          "and a frozen counter reads exactly like a device that never "
          "rebooted", (unsigned long)first, (unsigned long)second);

    teardown();
    case_end();
    ft_set_reset_reason(ESP_RST_POWERON);
}

static void case_unknown_reset_reason_is_a_number(void)
{
    case_begin("case 36: an unrecognised reset reason reports its number");
    ft_set_reset_reason((esp_reset_reason_t)99);
    setup();
    go_live();
    ft_run(20000);

    CHECK(json_has("\"reset_reason\":\"99\""),
          "a reset reason this firmware does not know must be reported as its "
          "NUMBER (spec 11). Folding it into \"UNKNOWN\" would file a new cause "
          "under an existing one, so the page would read 'the chip could not "
          "determine the reason' at the one moment the chip determined it "
          "exactly");

    teardown();
    case_end();
    ft_set_reset_reason(ESP_RST_POWERON);
}

int main(int argc, char **argv)
{
    /*
     * THE MOCK'S RECEIVE QUEUE MUST BE THE DEVICE'S. fake_twai's RXMAX was 10 against
     * the firmware's 64 until 2026-10-03, with a comment claiming they matched, and
     * the only thing that revealed it was replaying a real capture -- synthetic cases
     * never queue enough to notice. Checked here rather than commented, because a
     * comment is what was wrong.
     */
    if (ft_rx_queue_depth() != (int)can_rx_queue_len())
    {
        printf("FAIL: the mock receive queue is %d deep and the firmware's is %d. "
               "A shallower model drops frames the device would have buffered, which "
               "makes every replay case measure the wrong thing.\n",
               ft_rx_queue_depth(), (int)can_rx_queue_len());
        return 1;
    }
    for (int i = 1; i < argc; i++)
    {
        if (strcmp(argv[i], "--pin-stimulus") == 0)
        {
            g_pin_write_mode = true;
        }
    }
    setvbuf(stdout, NULL, _IONBF, 0);   /* a hang must still show its trace */
    printf("E1 -- shim tests against a fake TWAI driver\n\n");
    case_diag_completion_not_credited();
    case_late_completion_not_carried();
    case_slow_completion_is_not_late();
    case_inhibit_behind_diag();
    case_foreign_transmit_refused();
    case_self_off_names_itself();
    case_passive_emits_nothing();
    case_late_behind_diag_counted();
    case_diag_defers_to_pending();
    case_quiesce_names_itself();
    case_observe_is_listen_only();
    case_offset_limit();
    case_arm_refused_without_alerts();
    case_respond_probe();
    case_probe_waits_for_its_offset();
    case_diag_wire_matches_json();
    case_arm_forces_accept_all();
    case_rx_queue_depth();
    case_diag_defers_after_keyoff();
    case_status_json_bounds();
    case_skip_window_trips_at_three();
    case_controller_failed_trips();
    case_refusal_trips_and_nothing_follows();
    case_skip_kind_is_reported();
    case_no_abort_while_transmitting();
    case_status_page_fits_the_handler_buffer();
    case_mode_change_keeps_the_held_frame();
    case_status_page_always_balances();
    case_skips_are_since_arming();
    case_respond_figure_matches_its_offset();
    case_respond_figure_includes_the_buffer_wait();
    case_long_arm();
    case_long_arm_replay();
    case_backlog_reaches_the_scheduler();
    case_uptime_rises_and_the_reason_is_named();
    case_unknown_reset_reason_is_a_number();

    printf("\n%s\n", g_fail ? "FAILURES" : "all shim cases pass");
    return g_fail ? 1 : 0;
}
