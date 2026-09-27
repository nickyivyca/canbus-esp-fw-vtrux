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

static time_t g_case_start;
static const char *g_case_name = "";

static void case_begin(const char *name)
{
    g_case_name = name;
    g_case_start = time(NULL);
    printf("  %s\n", name);
}

static void case_end(void)
{
    const double took = difftime(time(NULL), g_case_start);
    if (took > CASE_BUDGET_S)
    {
        printf("    FAIL: %s took %.0f s of wall clock (budget %d s) -- "
               "something is spinning, not merely slow\n",
               g_case_name, took, CASE_BUDGET_S);
        g_fail++;
    }
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
static void invariant_hook(void)
{
    drain_wire_violations();

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
static void check_tx_ok_invariant(const char *where)
{
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
            const char *nul = memchr(area, ' ', (size_t)cap);
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
 * This is the alert read that moved into sched_pump() when poll_tx_completion()
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

int main(void)
{
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

    printf("\n%s\n", g_fail ? "FAILURES" : "all shim cases pass");
    return g_fail ? 1 : 0;
}
