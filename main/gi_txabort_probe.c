/*
 * gi_txabort_probe -- MEASUREMENT-ONLY. See gi_txabort_probe.h for why this is
 * a separate source that is not in the shipping build at all.
 *
 * Spec 5.2 item 9: before the transmit scheduler is built on the controller's
 * abort command, measure what the abort actually does -- to the frame, to the
 * TX alert bits, to msgs_to_tx, and to the time the buffer takes to come free.
 *
 * WHAT THE IDF SOURCE ALREADY PREDICTS, so that this run confirms or refutes a
 * stated claim rather than fishing. From
 * components/hal/esp32c3/include/hal/twai_ll.h, twai_ll_set_cmd_abort_tx():
 *
 *     "Frames awaiting TX will be aborted. Frames already being TX are not
 *      aborted. Transmission Complete Status bit is automatically set to 1.
 *      Similar to setting TX command, but the TWAI controller will not
 *      automatically retry transmission upon an error."
 *
 * Two predictions follow, and both are things this file exists to test:
 *
 *   - the driver's ISR decides TX_SUCCESS versus TX_FAILED from exactly that TCS
 *     bit (twai_ll_is_last_tx_successful() returns status_reg.tcs), so an abort
 *     is expected to be reported as a SUCCESSFUL transmission of a frame that
 *     never reached the wire. If that holds, the scheduler can never take a
 *     completion at face value, which is what spec 5.2 item 5 is about;
 *   - "will not automatically retry" means an abort latched while the frame is
 *     in its ARBITRATION field, where the frame then loses, should drop it with
 *     no retry. That makes "the controller was transmitting" and "the frame
 *     never reached the wire" simultaneously true, which is a legitimate fourth
 *     outcome rather than the instrument disagreeing with the witness. Raised by
 *     the reviewing session 2026-09-27, and it is why the status word is
 *     reported whole instead of collapsed into a label here.
 *
 * THE FOUR STATUS BITS THAT CLASSIFY A TRIAL, read in the same critical section
 * as the abort:
 *
 *     TBS = 1              the buffer is free; there is nothing to abort
 *     TBS = 0, TS = 0      a frame is in the buffer, not being transmitted --
 *                          awaiting arbitration (or awaiting an idle bus)
 *     TBS = 0, TS = 1      the controller is transmitting it, which INCLUDES
 *                          the arbitration field -- so this alone does not mean
 *                          the frame is guaranteed to reach the wire
 *     RS  = 1              the controller is RECEIVING a frame
 *
 * RS is what makes the "awaiting arbitration" case deterministic instead of a
 * timing lottery. With `gate=rs` the trial waits for the RS 0 -> 1 EDGE -- the
 * start of another node's frame -- and submits into it: our frame cannot begin
 * before that frame's EOF and the intermission that follows it, so an abort a few
 * microseconds later is guaranteed to land while ours is still awaiting.
 *
 * The edge, not the level, and the difference is not pedantry: RS = 1 is true
 * throughout the frame including its last few bits, so a level test can pass at a
 * point where submit-plus-15 us lands AFTER that frame's EOF, our frame has
 * already started, and the trial is filed as the gated case when it is not one.
 * `edge_us` records edge-to-abort per trial so "well inside the frame" is a
 * printed number, and every gated row is expected to show TS = 0 in `st_before`
 * -- a TS = 1 row in a gated phase means the gate failed and must be read as
 * such, not as a finding about the abort.
 *
 * The alternative -- a flood dense enough to saturate the bus -- would need about
 * 3700-4300 DLC-8 frames a second at 500 kbit/s. No measured rate on this bench
 * supports that (2364.6 fps is the rate of a PACED replay, not the adapter's
 * ceiling, so saturation is simply unestablished in either direction), which is
 * the reason the gate is the mechanism and bus load is not. Every trial is still
 * classified by its own measured status word: the gate makes the condition
 * likely, the status word makes it certain.
 *
 * HOW THE REGISTER IS REACHED, AND WHAT IS MASKED. The abort is a write to the
 * command register through TWAI_LL_GET_HW(0), with no driver lock held -- there
 * is no public way to take the driver's lock, and hiding the call inside one
 * would measure something the scheduler cannot do. The status read, the abort
 * and the status read after it are wrapped in ONE critical section, which on the
 * single-core C3 disables the TWAI interrupt for the duration. Two reasons, and
 * only the second is about the measurement:
 *
 *   - a status label read at a different instant from the abort is not a label
 *     for that abort. Without the critical section the ISR can complete the
 *     frame between the two, and the row would say "was transmitting" about a
 *     frame that had already finished;
 *   - the scheduler will need the same atomicity for the same reason, so
 *     measuring it this way measures what the scheduler will do.
 *
 * The race against the ISR is therefore narrowed to the register write itself
 * and is NOT eliminated -- the ISR can still run immediately after the section
 * ends, which is what everything after the section observes. The section is
 * three register accesses long, far inside CONFIG_ESP_INT_WDT_TIMEOUT_MS (300).
 *
 * The interrupt register is never read here. twai_ll_get_and_clear_intrs()
 * clears interrupts as a side effect of reading them, so touching it would steal
 * interrupts from the driver and change the behaviour being measured. Alerts are
 * read only through the public twai_read_alerts().
 */

#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/twai.h"
#include "hal/twai_ll.h"

#include "can.h"
#include "gen_inhibit.h"
#include "gi_txabort_probe.h"

static const char *TAG = "gi_txabort";

/*
 * The trial frame's ID. Lowest-priority region, clear of every ID this
 * firmware or the truck uses: the diag pages are 0x7F1-0x7F3 and 0x7F8, the
 * RESPOND probe is 0x7F0, and nothing in the powertrain capture reaches 0x7FE.
 */
#define TXAB_ID             0x7FE
#define TXAB_DLC            8

/* data[2..3] tags the frame so a witness can tell the two kinds apart. */
#define TXAB_TAG_TRIAL_HI   0xAB
#define TXAB_TAG_TRIAL_LO   0x7E
#define TXAB_TAG_FOLLOW_LO  0x7F

/*
 * 128 rather than 200 because the timing fields below are `int` and not
 * int16_t. The largest phase this bench runs is 78 trials, and a width that
 * cannot wrap is worth more than 72 rows nothing uses.
 */
#define TXAB_MAX_TRIALS     128
#define TXAB_POLL_US        20000   /* 20 ms: far past one frame at 500 kbit/s */
#define TXAB_TRAIL_US       2000    /* keep watching this long after the first
                                     * observation, so a LATE alert is caught
                                     * rather than missed by an early exit */
#define TXAB_RS_WAIT_US     50000   /* how long to wait for each RS transition */

/*
 * The alert bits an abort could plausibly raise, kept apart from the rest.
 *
 * WHY THIS SPLIT IS NOT COSMETIC. Alerts are enabled as TWAI_ALERT_ALL, because
 * "which alert does an abort raise" is the question and narrowing the set would
 * answer it by construction. But that includes TWAI_ALERT_RX_DATA, which fires on
 * every received frame -- thousands a second while another node floods the bus.
 * Without this mask, "time to the first alert" would measure the time to the next
 * RECEIVED FRAME, and the check that matters most -- was there NO alert at all,
 * one of the two answers that would change the emulator's model -- could never
 * fail, because RX_DATA guarantees the word is non-empty. A check whose failure
 * mode is indistinguishable from success is not a check.
 */
#define TXAB_TX_ALERTS  (TWAI_ALERT_TX_IDLE | TWAI_ALERT_TX_SUCCESS \
                         | TWAI_ALERT_TX_FAILED | TWAI_ALERT_ARB_LOST \
                         | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_TX_RETRIED)

enum
{
    TXAB_PHASE_ABORT = 0,   /* submit, then abort at the swept delay */
    TXAB_PHASE_CONTROL,     /* abort with nothing submitted */
    TXAB_PHASE_NOABORT,     /* submit and let it complete */
};

enum
{
    TXAB_GATE_OFF = 0,
    TXAB_GATE_RS,           /* submit only while the controller is receiving */
};

enum
{
    TXAB_SKIP_NONE = 0,
    TXAB_SKIP_BUSY_MTX,     /* driver still had a frame outstanding */
    TXAB_SKIP_BUSY_TBS,     /* controller buffer was not free */
    TXAB_SKIP_TX_REFUSED,   /* twai_transmit() would not take it */
    TXAB_SKIP_NO_RS,        /* no RS 0->1 edge appeared -- the bus was quiet */
    TXAB_SKIP_RS_STUCK,     /* RS never fell, so no edge could be seen */
};

typedef struct
{
    uint16_t trial;
    uint16_t req_us;        /* requested abort delay after twai_transmit() */
    int      act_us;        /* achieved delay, -1 when no abort was issued */
    int      gate_us;       /* how long the RS gate waited, -1 if not gated */
    int      edge_us;       /* RS 0->1 edge to the abort; -1 if not gated. This
                             * is the number that says the abort landed inside
                             * the frame our own was queued behind. */
    uint16_t st_pre;        /* status register before twai_transmit() */
    uint16_t st_before;     /* ... immediately before the abort */
    uint16_t st_after;      /* ... immediately after, same critical section */
    uint8_t  mtx_before;    /* msgs_to_tx before the abort */
    uint8_t  mtx_after;     /* ... right after */
    int      free_us;       /* until TBS set, from the abort; -1 = never */
    int      mtx0_us;       /* until msgs_to_tx == 0; -1 = never */
    int      alert_us;      /* until the first alert of ANY kind; -1 = none */
    int      txalert_us;    /* until the first TXAB_TX_ALERTS bit; -1 = none.
                             * THIS is the one the report reads -- alert_us is
                             * dominated by RX_DATA whenever the bus is busy. */
    uint32_t alerts;        /* every alert bit seen during the poll */
    uint32_t txalerts;      /* ... masked to TXAB_TX_ALERTS */
    uint8_t  d_tx_failed;   /* driver counter deltas over the trial */
    uint8_t  d_bus_err;
    uint8_t  d_arb_lost;
    uint8_t  tx_err;        /* 0 = ESP_OK */
    uint8_t  next_tx_err;   /* the follow-up transmit: does the buffer work? */
    int      next_done_us;  /* until the follow-up completed; -1 = never */
    uint8_t  skip;
} txab_rec_t;

static txab_rec_t s_rec[TXAB_MAX_TRIALS];
static int        s_n;

/*
 * Our own spinlock. Deliberately not the driver's -- that one is private, and
 * taking it is not something the scheduler could do either.
 */
static portMUX_TYPE s_abort_lock = portMUX_INITIALIZER_UNLOCKED;

/*
 * A bounded copy that REPORTS truncation, so the caller can refuse rather than
 * proceed with a shortened argument.
 *
 * snprintf(dst, sizeof dst, "%s", src) with src larger than dst is
 * -Werror=format-truncation under this build's flags, and the warning is right,
 * but silently truncating is not the fix: it is how "gate=rs" becomes something
 * else and an ungated phase runs with nothing saying so. Today's arguments happen
 * to be validated downstream -- gate against off/rs, phase against three names --
 * so a truncated one would 400 anyway; `label` would not, and the next argument
 * added here would inherit the trap instead of the check. Returns false if it did
 * not fit. Raised by the reviewing session, 2026-09-27.
 */
static bool copy_arg(char *dst, size_t dn, const char *src)
{
    size_t i = 0;
    while (i + 1 < dn && src[i] != '\0')
    {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
    return src[i] == '\0';
}

static void txab_fill(twai_message_t *m, uint16_t trial, uint8_t tag_lo,
                      uint16_t req_us)
{
    memset(m, 0, sizeof(*m));
    m->identifier = TXAB_ID;
    m->data_length_code = TXAB_DLC;
    m->data[0] = (uint8_t)(trial & 0xFF);
    m->data[1] = (uint8_t)(trial >> 8);
    m->data[2] = TXAB_TAG_TRIAL_HI;
    m->data[3] = tag_lo;
    m->data[4] = (uint8_t)(req_us & 0xFF);
    m->data[5] = (uint8_t)(req_us >> 8);
    m->data[6] = 0;
    m->data[7] = 0;
}

/*
 * One trial. Everything that decides a number is in here so the sequence is
 * legible in one place; the phases differ only in what happens between the
 * submit and the poll.
 */
static void txab_trial(int phase, int gate, uint16_t trial, uint16_t req_us,
                       txab_rec_t *r)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_status_info_t s0, s1;
    uint32_t junk = 0;
    twai_message_t m;

    memset(r, 0, sizeof(*r));
    r->trial = trial;
    r->req_us = req_us;
    r->act_us = -1;
    r->gate_us = -1;
    r->edge_us = -1;
    r->free_us = -1;
    r->mtx0_us = -1;
    r->alert_us = -1;
    r->txalert_us = -1;
    r->next_done_us = -1;

    /*
     * Start from a known state. The RX queue is drained because nothing else is
     * draining it in this build -- gen_inhibit is OFF and its worker is not
     * running -- and a full RX queue raises alerts that would land in this
     * trial's alert word and be read as something the abort did.
     */
    can_flush_rx();
    (void)twai_read_alerts(&junk, 0);

    if (twai_get_status_info(&s0) != ESP_OK || s0.msgs_to_tx != 0)
    {
        r->skip = TXAB_SKIP_BUSY_MTX;
        return;
    }
    r->st_pre = (uint16_t)twai_ll_get_status(hw);
    if (phase != TXAB_PHASE_CONTROL && !(r->st_pre & TWAI_LL_STATUS_TBS))
    {
        r->skip = TXAB_SKIP_BUSY_TBS;
        return;
    }

    int64_t t_ref;
    int64_t t_edge = 0;

    if (phase == TXAB_PHASE_CONTROL)
    {
        /*
         * THE CONTROL: abort an empty buffer. No frame is submitted, so every
         * number below describes what the abort command does on its own. This
         * is the row every other row is read against -- without it, "the abort
         * raised TX_SUCCESS" cannot be told from "the abort command always
         * raises TX_SUCCESS".
         */
        t_ref = esp_timer_get_time();
        taskENTER_CRITICAL(&s_abort_lock);
        r->st_before = (uint16_t)twai_ll_get_status(hw);
        twai_ll_set_cmd_abort_tx(hw);
        r->st_after = (uint16_t)twai_ll_get_status(hw);
        taskEXIT_CRITICAL(&s_abort_lock);
        r->act_us = 0;
    }
    else
    {
        /*
         * THE RS GATE, ON THE EDGE AND NOT THE LEVEL. Wait for the controller to
         * START receiving another node's frame, then submit into it. Our frame
         * cannot begin before that frame's EOF and the intermission after it, so
         * a short abort delay is guaranteed to land while ours is still awaiting
         * -- without needing the bus saturated.
         *
         * WHY THE EDGE MATTERS AND THE LEVEL DOES NOT DO. "Spin until RS is set"
         * returns immediately if RS is already 1, which can be anywhere in the
         * frame in progress INCLUDING ITS LAST FEW BITS. Submit plus a 15 us
         * abort then lands after that frame's EOF, our frame may already have
         * started, and the trial gets filed as the gated case when it is not one.
         * So: wait for RS = 0, then for RS = 1, then submit. Caught by the
         * reviewing session, 2026-09-27.
         *
         * The two waits have separate skip codes on purpose. A quiet bus (no
         * edge) and a bus so busy that RS never falls are different facts, and an
         * API where both came back as one code would hide the second entirely.
         */
        if (gate == TXAB_GATE_RS)
        {
            const int64_t g0 = esp_timer_get_time();
            while (twai_ll_get_status(hw) & TWAI_LL_STATUS_RS)
            {
                if (esp_timer_get_time() - g0 > TXAB_RS_WAIT_US)
                {
                    r->skip = TXAB_SKIP_RS_STUCK;
                    return;
                }
            }
            /*
             * ITS OWN DEADLINE, not g0's. Sharing one meant that a long wait for
             * RS to fall left the rising-edge wait almost no budget, and it would
             * have skipped as "bus quiet" on a bus that was continuously busy --
             * the two conditions this gate exists to tell apart.
             */
            const int64_t g1 = esp_timer_get_time();
            for (;;)
            {
                if (twai_ll_get_status(hw) & TWAI_LL_STATUS_RS)
                {
                    break;
                }
                if (esp_timer_get_time() - g1 > TXAB_RS_WAIT_US)
                {
                    r->skip = TXAB_SKIP_NO_RS;
                    return;
                }
            }
            t_edge = esp_timer_get_time();
            r->gate_us = (int)(t_edge - g0);
            r->st_pre = (uint16_t)twai_ll_get_status(hw);
        }

        txab_fill(&m, trial, TXAB_TAG_TRIAL_LO, req_us);
        const esp_err_t terr = twai_transmit(&m, 0);
        t_ref = esp_timer_get_time();
        r->tx_err = (terr == ESP_OK) ? 0 : 1;
        if (terr != ESP_OK)
        {
            r->skip = TXAB_SKIP_TX_REFUSED;
            return;
        }

        if (phase == TXAB_PHASE_NOABORT)
        {
            /*
             * No abort at all. This measures a GENUINE completion with the same
             * instrument, in the same run, so the comparison item 5 needs is
             * between two rows of one table rather than between a row and a
             * memory of another session's numbers.
             */
            r->mtx_before = 1;
            r->st_before = (uint16_t)twai_ll_get_status(hw);
            r->st_after = r->st_before;
            r->mtx_after = 1;
        }
        else
        {
            /*
             * Spin to the requested delay and record what was ACHIEVED. The
             * achieved value is what the row is classified by: a trial that got
             * preempted lands at a different delay and is still a valid data
             * point there, whereas classifying by the request would silently
             * file it under a delay it never had.
             */
            while (esp_timer_get_time() - t_ref < (int64_t)req_us)
            {
                /* spin */
            }

            twai_status_info_t sb;
            r->mtx_before = (twai_get_status_info(&sb) == ESP_OK)
                                ? (uint8_t)sb.msgs_to_tx : 0xFF;

            taskENTER_CRITICAL(&s_abort_lock);
            const int64_t t_abort = esp_timer_get_time();
            r->st_before = (uint16_t)twai_ll_get_status(hw);
            twai_ll_set_cmd_abort_tx(hw);
            r->st_after = (uint16_t)twai_ll_get_status(hw);
            taskEXIT_CRITICAL(&s_abort_lock);

            r->act_us = (int)(t_abort - t_ref);
            if (gate == TXAB_GATE_RS)
            {
                r->edge_us = (int)(t_abort - t_edge);
            }
            t_ref = t_abort;

            twai_status_info_t sa;
            r->mtx_after = (twai_get_status_info(&sa) == ESP_OK)
                               ? (uint8_t)sa.msgs_to_tx : 0xFF;
        }
    }

    /*
     * Watch for the consequences. Everything is timed from t_ref -- the abort
     * for the abort phases, the submit for noabort -- and the loop keeps going
     * TXAB_TRAIL_US past the first thing it sees, because an early exit is how
     * a late alert becomes "no alert", which is one of the two answers that
     * would change the model.
     */
    const int64_t deadline = t_ref + TXAB_POLL_US;
    int64_t settled = 0;
    for (;;)
    {
        const int64_t now = esp_timer_get_time();
        const uint32_t st = twai_ll_get_status(hw);

        if (r->free_us < 0 && (st & TWAI_LL_STATUS_TBS))
        {
            r->free_us = (int)(now - t_ref);
        }

        twai_status_info_t sp;
        if (r->mtx0_us < 0 && twai_get_status_info(&sp) == ESP_OK
            && sp.msgs_to_tx == 0)
        {
            r->mtx0_us = (int)(now - t_ref);
        }

        uint32_t a = 0;
        if (twai_read_alerts(&a, 0) == ESP_OK && a != 0)
        {
            r->alerts |= a;
            if (r->alert_us < 0)
            {
                r->alert_us = (int)(now - t_ref);
            }
            if (a & TXAB_TX_ALERTS)
            {
                r->txalerts |= (a & TXAB_TX_ALERTS);
                if (r->txalert_us < 0)
                {
                    r->txalert_us = (int)(now - t_ref);
                }
            }
        }

        /*
         * Settled means the TX-relevant alert has been seen, not just any alert.
         * Keyed on alert_us this loop would have exited on the first received
         * frame under a flood, before the abort's own alert had a chance to
         * appear -- manufacturing the "no alert" answer it is here to test for.
         */
        if (settled == 0 && r->free_us >= 0 && r->mtx0_us >= 0
            && r->txalert_us >= 0)
        {
            settled = now;
        }
        if (settled != 0 && now - settled >= TXAB_TRAIL_US)
        {
            break;
        }
        if (now >= deadline)
        {
            break;
        }
    }

    if (twai_get_status_info(&s1) == ESP_OK)
    {
        uint32_t d;
        d = s1.tx_failed_count - s0.tx_failed_count;
        r->d_tx_failed = (d > 255) ? 255 : (uint8_t)d;
        d = s1.bus_error_count - s0.bus_error_count;
        r->d_bus_err = (d > 255) ? 255 : (uint8_t)d;
        d = s1.arb_lost_count - s0.arb_lost_count;
        r->d_arb_lost = (d > 255) ? 255 : (uint8_t)d;
    }

    /*
     * Does the controller still transmit normally afterwards? An abort that
     * leaves the peripheral in a state where the next frame does not go out
     * would make the whole preemption design unusable, and nothing above would
     * have noticed.
     *
     * With a flood running, this frame may legitimately not complete inside the
     * window -- next_done_us = -1 there says the bus was busy, not that the
     * controller is broken. next_tx_err is the part that means the same thing in
     * every phase.
     */
    txab_fill(&m, trial, TXAB_TAG_FOLLOW_LO, req_us);
    const int64_t t_next = esp_timer_get_time();
    const esp_err_t nerr = twai_transmit(&m, pdMS_TO_TICKS(10));
    r->next_tx_err = (nerr == ESP_OK) ? 0 : 1;
    if (nerr == ESP_OK)
    {
        const int64_t nd = t_next + TXAB_POLL_US;
        for (;;)
        {
            const int64_t now = esp_timer_get_time();
            twai_status_info_t sp;
            if (twai_get_status_info(&sp) == ESP_OK && sp.msgs_to_tx == 0)
            {
                r->next_done_us = (int)(now - t_next);
                break;
            }
            if (now >= nd)
            {
                break;
            }
        }
        (void)twai_read_alerts(&junk, 0);
    }
}

/* ------------------------------------------------------------- reporting -- */

static const char *skip_name(uint8_t s)
{
    switch (s)
    {
    case TXAB_SKIP_NONE:        return "";
    case TXAB_SKIP_BUSY_MTX:    return "driver busy (msgs_to_tx != 0)";
    case TXAB_SKIP_BUSY_TBS:    return "buffer occupied (TBS clear)";
    case TXAB_SKIP_TX_REFUSED:  return "twai_transmit refused";
    case TXAB_SKIP_NO_RS:       return "no RS 0->1 edge (bus quiet)";
    case TXAB_SKIP_RS_STUCK:    return "RS never fell (bus continuously busy)";
    default:                    return "?";
    }
}

static esp_err_t send_rec(httpd_req_t *req, const txab_rec_t *r, bool first)
{
    char b[480];
    int n = snprintf(b, sizeof(b),
        "%s{\"t\":%u,\"req\":%u,\"act\":%d,\"gate_us\":%d,"
        "\"edge_us\":%d,"
        "\"st_pre\":%u,\"st_before\":%u,\"st_after\":%u,"
        "\"tbs_b\":%d,\"ts_b\":%d,\"rs_b\":%d,\"tcs_b\":%d,\"es_b\":%d,"
        "\"bs_b\":%d,"
        "\"tbs_a\":%d,\"ts_a\":%d,\"rs_a\":%d,\"tcs_a\":%d,"
        "\"mtx_b\":%u,\"mtx_a\":%u,"
        "\"free_us\":%d,\"mtx0_us\":%d,"
        "\"alert_us\":%d,\"alerts\":%lu,"
        "\"txalert_us\":%d,\"txalerts\":%lu,"
        "\"d_tx_failed\":%u,\"d_bus_err\":%u,\"d_arb_lost\":%u,"
        "\"tx_err\":%u,\"next_tx_err\":%u,\"next_done_us\":%d,"
        "\"skip\":\"%s\"}",
        first ? "" : ",",
        (unsigned)r->trial, (unsigned)r->req_us, (int)r->act_us,
        (int)r->gate_us, (int)r->edge_us,
        (unsigned)r->st_pre, (unsigned)r->st_before, (unsigned)r->st_after,
        (r->st_before & TWAI_LL_STATUS_TBS) ? 1 : 0,
        (r->st_before & TWAI_LL_STATUS_TS) ? 1 : 0,
        (r->st_before & TWAI_LL_STATUS_RS) ? 1 : 0,
        (r->st_before & TWAI_LL_STATUS_TCS) ? 1 : 0,
        (r->st_before & TWAI_LL_STATUS_ES) ? 1 : 0,
        (r->st_before & TWAI_LL_STATUS_BS) ? 1 : 0,
        (r->st_after & TWAI_LL_STATUS_TBS) ? 1 : 0,
        (r->st_after & TWAI_LL_STATUS_TS) ? 1 : 0,
        (r->st_after & TWAI_LL_STATUS_RS) ? 1 : 0,
        (r->st_after & TWAI_LL_STATUS_TCS) ? 1 : 0,
        (unsigned)r->mtx_before, (unsigned)r->mtx_after,
        (int)r->free_us, (int)r->mtx0_us,
        (int)r->alert_us, (unsigned long)r->alerts,
        (int)r->txalert_us, (unsigned long)r->txalerts,
        (unsigned)r->d_tx_failed, (unsigned)r->d_bus_err,
        (unsigned)r->d_arb_lost,
        (unsigned)r->tx_err, (unsigned)r->next_tx_err, (int)r->next_done_us,
        skip_name(r->skip));
    if (n < 0 || n >= (int)sizeof(b))
    {
        return ESP_FAIL;
    }
    return httpd_resp_send_chunk(req, b, n);
}

/*
 * Fetch one query argument, or say why not.
 *
 * Returns 1 found, 0 absent, -1 present but unusable. The -1 case is the whole
 * point: httpd_query_key_value() returns ESP_ERR_HTTPD_RESULT_TRUNC rather than
 * ESP_OK when the value does not fit, so testing `== ESP_OK` treats an over-long
 * value exactly like an absent one and runs the DEFAULT. That is a guard whose
 * bypass path is indistinguishable from its ordinary path.
 */
static int arg_get(const char *query, const char *key, char *val, size_t vn)
{
    const esp_err_t e = httpd_query_key_value(query, key, val, vn);
    if (e == ESP_OK)              { return 1; }
    if (e == ESP_ERR_NOT_FOUND)   { return 0; }
    return -1;
}

/*
 * strtol with the end pointer checked, because atoi("abc") is 0 -- so `d0=abc`
 * would run as d0 = 0, which is the same silent-default failure in a different
 * coat. Returns false if the text is not a whole number in [lo, hi].
 */
static bool arg_int(const char *val, long lo, long hi, int *out)
{
    char *end = NULL;
    const long v = strtol(val, &end, 10);
    if (end == val || (end != NULL && *end != '\0'))
    {
        return false;
    }
    if (v < lo || v > hi)
    {
        return false;
    }
    *out = (int)v;
    return true;
}

static int phase_from(const char *s)
{
    if (strcmp(s, "abort") == 0)    { return TXAB_PHASE_ABORT; }
    if (strcmp(s, "control") == 0)  { return TXAB_PHASE_CONTROL; }
    if (strcmp(s, "noabort") == 0)  { return TXAB_PHASE_NOABORT; }
    return -1;
}

static esp_err_t txab_handler(httpd_req_t *req)
{
    char query[256];
    char val[40];
    char phase_s[16] = "abort";
    char gate_s[8] = "off";
    char label[32] = "";
    const char *bad = NULL;
    int reps = 3;
    int d0 = 0, d1 = 0, step = 20, settle_ms = 2;

    /*
     * THE QUERY STRING ITSELF FIRST. A query over sizeof(query) returns
     * RESULT_TRUNC, and the old `== ESP_OK` test then skipped ALL parsing and ran
     * the whole measurement on defaults -- the worst version of this failure,
     * because every argument is wrong at once and nothing says so.
     */
    bool have_query = false;
    {
        const esp_err_t qe = httpd_req_get_url_query_str(req, query,
                                                         sizeof(query));
        if (qe == ESP_OK)
        {
            have_query = true;
        }
        else if (qe != ESP_ERR_NOT_FOUND)
        {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                "the query string did not fit and would have "
                                "been truncated -- refused, because every "
                                "argument would silently have taken its default");
            ESP_LOGE(TAG, "refused: query string too long (%d)", (int)qe);
            return ESP_FAIL;
        }
    }

    if (have_query)
    {
        /*
         * Each argument: 1 found, 0 absent, -1 present but unusable. Only 0 falls
         * through to the default. `bad` names the first argument that was
         * supplied and could not be honoured, and nothing runs until it is NULL.
         */
        int g;

        g = arg_get(query, "phase", val, sizeof(val));
        if (g < 0 || (g > 0 && !copy_arg(phase_s, sizeof(phase_s), val)))
        {
            bad = "phase";
        }
        g = arg_get(query, "gate", val, sizeof(val));
        if (g < 0 || (g > 0 && !copy_arg(gate_s, sizeof(gate_s), val)))
        {
            bad = "gate";
        }
        g = arg_get(query, "label", val, sizeof(val));
        if (g < 0 || (g > 0 && !copy_arg(label, sizeof(label), val)))
        {
            bad = "label";
        }

        /*
         * The numeric bounds are not tidiness. req_us is uint16_t and the timing
         * fields are int, so d0 = 70000 used to truncate to 4464 and a sweep
         * would have run at a delay nobody asked for, labelled with the one they
         * did. 20000 us leaves room for a preemption outlier on top without any
         * result field wrapping.
         */
        struct { const char *key; int *dst; long lo, hi; } nums[] = {
            { "reps",   &reps,      1, TXAB_MAX_TRIALS },
            { "d0",     &d0,        0, 20000 },
            { "d1",     &d1,        0, 20000 },
            { "step",   &step,      1, 20000 },
            { "settle", &settle_ms, 1, 1000 },
        };
        for (unsigned i = 0; i < sizeof(nums) / sizeof(nums[0]); i++)
        {
            g = arg_get(query, nums[i].key, val, sizeof(val));
            if (g < 0
                || (g > 0 && !arg_int(val, nums[i].lo, nums[i].hi,
                                      nums[i].dst)))
            {
                bad = nums[i].key;
            }
        }
    }

    if (bad != NULL)
    {
        /*
         * Refuse rather than run something adjacent to what was asked for. A
         * measurement whose configuration was quietly altered is worse than no
         * measurement, because the results look like an answer.
         */
        /*
         * 256, because the fixed text alone is 155 bytes and `bad` is a key name
         * on top -- at 160 this was a -Werror=format-truncation error, and the
         * compiler was right: a refusal that silently loses its own explanation
         * is the same class of defect as the truncation it is refusing.
         */
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "query argument '%s' was supplied but is unusable (too long, "
                 "not a whole number, or out of range) -- refused rather than "
                 "run with a different configuration than was asked for", bad);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, msg);
        ESP_LOGE(TAG, "refused: argument '%s' unusable", bad);
        return ESP_FAIL;
    }

    const int phase = phase_from(phase_s);
    const int gate = (strcmp(gate_s, "rs") == 0) ? TXAB_GATE_RS : TXAB_GATE_OFF;
    if (phase < 0)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "phase must be abort|control|noabort");
        return ESP_FAIL;
    }
    if (strcmp(gate_s, "rs") != 0 && strcmp(gate_s, "off") != 0)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "gate must be off|rs");
        return ESP_FAIL;
    }
    if (d1 < d0)
    {
        d1 = d0;
    }
    /*
     * settle >= 1 ms IS NOT TIDINESS. Each trial busy-polls for up to
     * TXAB_POLL_US + the follow-up's window and never blocks, and this runs at
     * priority 18. With no delay the IDLE task never runs, and the task
     * watchdog -- 5 s, CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0=y, no panic --
     * starts logging in the middle of the measurement. Caught by the reviewing
     * session, 2026-09-27.
     */
    /*
     * Ranges are enforced by the table above; what is left is the relation
     * between d0 and d1, and the trial budget. settle >= 1 is in the table with
     * the reason recorded here: each trial busy-polls and never blocks, at
     * priority 18, so with no delay between trials the IDLE task never runs and
     * the task watchdog (5 s, CHECK_IDLE_TASK_CPU0=y, no panic) starts logging in
     * the middle of the measurement.
     */
    if ((long)(((d1 - d0) / step) + 1) * reps > TXAB_MAX_TRIALS)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "steps x reps exceeds the record budget -- refused "
                            "rather than silently truncating the sweep");
        return ESP_FAIL;
    }

    /*
     * Refuse to run while gen_inhibit owns the bus. Two frames of ours in the
     * controller at once is the one condition under which none of these numbers
     * mean anything, and the inhibitor's own transmits would also be aborted.
     */
    if (gen_inhibit_owns_bus())
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "gen_inhibit owns the bus; POST "
                            "/gen_inhibit_set?mode=0 first");
        return ESP_FAIL;
    }

    /*
     * Bring the bus up ourselves if nothing else has. can_init() only records
     * the bitrate; can_enable() installs the driver and starts it. Listen-only
     * would make every transmit here a silent no-op, so it is cleared first --
     * and if the bus still will not come up, say so rather than producing a
     * table of zeros.
     */
    if (!can_is_enabled())
    {
        can_set_silent(0);
        can_set_bitrate(CAN_500K);
        can_enable();
    }
    if (!can_is_enabled())
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "CAN bus would not come up");
        return ESP_FAIL;
    }
    if (can_is_silent())
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "bus is listen-only; nothing can be transmitted");
        return ESP_FAIL;
    }

    /*
     * Every alert, not just the two the shipping build reads. Which alert an
     * abort raises is the question, so narrowing the set would answer it by
     * construction.
     */
    if (twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "alerts would not enable");
        return ESP_FAIL;
    }

    const int steps = ((d1 - d0) / step) + 1;
    const int want = steps * reps;

    const int64_t t_run0 = esp_timer_get_time();

    /*
     * Run at the worker's priority. The spin that sets the abort delay is only
     * as good as the task it runs on, and the httpd task's normal priority puts
     * it below lwIP -- which is how a 20 us step becomes a 2 ms outlier. lwIP is
     * at 18 too, so this does not remove preemption; it bounds it, and `act_us`
     * records what actually happened either way.
     */
    TaskHandle_t self = xTaskGetCurrentTaskHandle();
    const UBaseType_t prio0 = uxTaskPriorityGet(self);
    vTaskPrioritySet(self, 18);

    s_n = 0;
    for (int i = 0; i < want; i++)
    {
        const uint16_t req_us = (uint16_t)(d0 + (i / reps) * step);
        txab_trial(phase, gate, (uint16_t)i, req_us, &s_rec[s_n]);
        s_n++;
        vTaskDelay(pdMS_TO_TICKS(settle_ms));
    }

    vTaskPrioritySet(self, prio0);

    twai_status_info_t send_s;
    const bool have_s = (twai_get_status_info(&send_s) == ESP_OK);

    /*
     * The acceptance filter and the queue depth go in the header because this
     * probe does NOT go through the arming path, so spec 5.1 item 2's refusal of
     * a narrowed filter never runs here. A narrowed filter would not corrupt the
     * abort measurement -- the witness decides what reached the wire -- but it
     * would make RS fire on fewer frames, so the RS gate would slow down or skip
     * for a reason nothing in the results explained. Reported rather than
     * assumed.
     */

    char head[640];
    int n = snprintf(head, sizeof(head),
        "{\"probe\":\"gi_txabort\",\"git\":\"%s\",\"phase\":\"%s\","
        "\"gate\":\"%s\",\"label\":\"%s\","
        "\"id\":\"0x%03X\",\"dlc\":%d,\"tag\":\"AB7E trial, AB7F follow-up\","
        "\"reps\":%d,\"d0\":%d,\"d1\":%d,\"step\":%d,\"steps\":%d,"
        "\"settle_ms\":%d,\"query_given\":%s,"
        "\"n\":%d,\"run_us\":%lld,\"uptime_us\":%lld,"
        "\"silent\":%s,\"alerts_enabled\":\"TWAI_ALERT_ALL\","
        "\"rx_queue_len\":%lu,\"filter_narrowed\":%s,"
        "\"end\":{\"state\":%d,\"tx_failed\":%lu,\"bus_error\":%lu,"
        "\"arb_lost\":%lu,\"rx_missed\":%lu,\"rx_overrun\":%lu,"
        "\"tec\":%lu,\"rec\":%lu},"
        "\"trials\":[",
        GIT_SHA, phase_s, gate_s, label, TXAB_ID, TXAB_DLC,
        reps, d0, d1, step, steps, settle_ms,
        have_query ? "true" : "false",
        s_n, (long long)(esp_timer_get_time() - t_run0),
        (long long)esp_timer_get_time(),
        can_is_silent() ? "true" : "false",
        (unsigned long)can_rx_queue_len(),
        can_filter_narrowed() ? "true" : "false",
        have_s ? (int)send_s.state : -1,
        (unsigned long)(have_s ? send_s.tx_failed_count : 0),
        (unsigned long)(have_s ? send_s.bus_error_count : 0),
        (unsigned long)(have_s ? send_s.arb_lost_count : 0),
        (unsigned long)(have_s ? send_s.rx_missed_count : 0),
        (unsigned long)(have_s ? send_s.rx_overrun_count : 0),
        (unsigned long)(have_s ? send_s.tx_error_counter : 0),
        (unsigned long)(have_s ? send_s.rx_error_counter : 0));

    if (n < 0 || n >= (int)sizeof(head))
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "header did not fit");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    if (httpd_resp_send_chunk(req, head, n) != ESP_OK)
    {
        return ESP_FAIL;
    }
    for (int i = 0; i < s_n; i++)
    {
        if (send_rec(req, &s_rec[i], i == 0) != ESP_OK)
        {
            return ESP_FAIL;
        }
    }
    if (httpd_resp_send_chunk(req, "]}", 2) != ESP_OK)
    {
        return ESP_FAIL;
    }
    ESP_LOGW(TAG, "measurement run: phase=%s gate=%s label=%s trials=%d -- "
                  "THIS IS NOT A SHIPPING BUILD", phase_s, gate_s, label, s_n);
    return httpd_resp_send_chunk(req, NULL, 0);
}

static const httpd_uri_t txab_uri = {
    .uri = "/gi_txabort", .method = HTTP_POST,
    .handler = txab_handler, .user_ctx = NULL
};

esp_err_t gi_txabort_register(httpd_handle_t server)
{
    ESP_LOGW(TAG, "MEASUREMENT BUILD: /gi_txabort is registered. This image "
                  "writes the TWAI abort command register directly and must "
                  "never be flashed to the truck.");
    return httpd_register_uri_handler(server, &txab_uri);
}
