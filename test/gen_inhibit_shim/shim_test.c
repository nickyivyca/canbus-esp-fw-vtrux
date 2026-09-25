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
    static char buf[1600];
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

/* ---------------------------------------------------------- the fixture -- */

static const uint8_t VCM[6] = { 0x08, 0x00, 0x80, 0xFF, 0x7F, 0x00 };

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
 * The invariant as a STEP HOOK rather than an end-of-case assertion. See
 * ft_set_step_hook(): 632af32's bug opens a window that closes again, so a
 * check that only runs at the end sees nothing wrong.
 */
static void invariant_hook(void)
{
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
    gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
    ft_run(100000);
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
    CHECK(ft_wire_count_id(0x051) == wire_before,
          "the stall did not take: %d inhibit frames completed anyway",
          ft_wire_count_id(0x051) - wire_before);

    const uint32_t tx_after = json_u32("\"tx_ok\":");
    CHECK(tx_after == tx_before,
          "tx_ok moved %u -> %u while the inhibit at the head of the queue "
          "never completed", tx_before, tx_after);
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
    case_begin("case 4: an inhibit queued behind an unfinished diag frame");
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
    static const uint8_t *const CYCLE[5] = { KEY, CONT, SOC, FLT, SHF };
    static const uint32_t CYCLE_ID[5] = { 0x592, 0x440, 0x411, 0x617, 0x639 };

    int diag_at = -1;
    const int64_t give_up = ft_now() + 900000;
    for (int k = 0; diag_at < 0 && ft_now() < give_up; k++)
    {
        const int n = ft_sent_count();
        feed(CYCLE_ID[k % 5], CYCLE[k % 5], 8, 10000);
        for (int i = n; i < ft_sent_count(); i++)
        {
            const uint32_t id = ft_sent(i)->id;
            if (id == 0x7F1 || id == 0x7F2 || id == 0x7F3 || id == 0x7F8)
            {
                diag_at = i;
                break;
            }
        }
    }
    CHECK(diag_at >= 0,
          "no diag page was queued within 900 ms, so the inhibit could not be "
          "queued behind one and this case proved nothing");
    CHECK(ft_wire_count_id(ft_sent(diag_at >= 0 ? diag_at : 0)->id) == 0
          || diag_at < 0,
          "the diag page had already completed before the 0x051 was fed, so "
          "its alert was consumed while nothing was outstanding");

    const uint32_t before = json_u32("\"tx_ok\":");
    const int wire_before = ft_wire_count_id(0x051);

    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = 0x0C;
    feed(0x051, cmd, 6, 1000);      /* queues BEHIND the in-flight diag */

    /*
     * Let the diag complete -- latching its alert -- with our frame stalled
     * behind it. The step hook watches the invariant throughout; these check
     * the same thing where it matters.
     */
    ft_run(500000);
    CHECK(ft_wire_count_id(0x051) == wire_before,
          "the stall did not take: our frame completed, so the diag's "
          "completion was not observed while ours was outstanding");
    CHECK(json_u32("\"tx_ok\":") == before,
          "tx_ok moved %u -> %u on the DIAG frame's completion while our frame "
          "was still queued behind it", before, json_u32("\"tx_ok\":"));
    /*
     * Spec 5's hazard is happening RIGHT HERE -- our frame went into the
     * controller behind an unfinished one -- so the counter that exists to
     * measure it on the full-replay bench must have moved. A silent zero there
     * would read as "hazard absent", which is the worst possible way for a
     * measurement to fail.
     */
    CHECK(json_u32("\"tx_queued_behind\":") > 0,
          "tx_queued_behind is still 0 although this case queued an inhibit "
          "behind an in-flight diag frame: the spec 5 reading is dead");

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
static void case_late_behind_diag_trips(void)
{
    case_begin("case 8: the VCM's next 0x051 arrives with ours still queued");
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

    CHECK(json_has("still unsent when the VCM's next 0x051 arrived"),
          "a frame that was still queued when the next 0x051 arrived did not "
          "trip: %s", stats());
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

    ft_stall_id(0x051, 1);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = 0x0C;
    feed(0x051, cmd, 6, 5000);          /* this one stalls at the head */

    const int q_before = ft_sent_count();
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

    /* Leaving OBSERVE must restore a transmitting mode. */
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
    case_late_behind_diag_trips();
    case_diag_defers_to_pending();
    case_quiesce_names_itself();
    case_observe_is_listen_only();
    case_offset_limit();
    case_arm_refused_without_alerts();
    case_respond_probe();

    printf("\n%s\n", g_fail ? "FAILURES" : "all shim cases pass");
    return g_fail ? 1 : 0;
}
