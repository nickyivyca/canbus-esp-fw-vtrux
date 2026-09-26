/*
 * gen_inhibit_core -- the decision logic. See gen_inhibit_core.h for the
 * rules this file obeys and for why it exists at all.
 *
 * Nothing in here may include a platform header, read a clock, allocate, or
 * log. If a change needs one of those, it belongs in gen_inhibit.c.
 *
 * This file is a behaviour-preserving extraction from gen_inhibit.c as it
 * stood on 2026-09-19. Where a comment explains WHY a rule is the way it is,
 * it has been carried across verbatim in substance -- those justifications
 * are the record of what was measured on the truck, and losing them in a
 * refactor would be the expensive part.
 */
#include "gen_inhibit_core.h"

#include <string.h>

/* ---------------------------------------------------------------- config -- */

void gi_config_defaults(gi_config_t *c)
{
    memset(c, 0, sizeof(*c));

    /*
     * 21 %, deliberately a full percentage point above the truck's own 20.0 %
     * trigger, so we get out of the way BEFORE the VCM asks for the engine
     * rather than at the same instant. The cost is giving up inhibiting in
     * the 20-21 % band; the benefit is that there is no window in which we
     * are blocking a start the pack genuinely needs. Spec section 6.2:
     * isolating entries into charge-sustain across the whole corpus leaves
     * two events, firing at 20.02 % and 20.23 %, so the truck defends a
     * 20.0 % floor.
     */
    c->soc_min_raw = 21 * 100;

    /*
     * 0x411 reads 0 for the first samples at startup before it is valid. A
     * raw 0 is treated as "not yet valid", never as 0 %, and the
     * sub-threshold condition must persist this many valid samples before it
     * latches -- so a transient 0->real-value at boot cannot false-trip.
     */
    c->soc_debounce = 5;

    /*
     * 300 rpm sustained for 0.3 s, from the laptop tool's --start-abort-rpm.
     * The debounce is not optional: a momentary crank blip is exactly what a
     * WORKING inhibit produces, and aborting on it would end the run the
     * inhibit just won.
     */
    c->start_abort_rpm = 300;
    c->rpm_debounce_us = 300000;

    /*
     * Freshness, not last-known values: a silent bus otherwise answers every
     * question you ask it. 0.5 s matches the reference tool's gene_alive().
     */
    c->fresh_us = 500000;
    c->fault_fresh_us = 1000000;    /* 0x617 only; spec 7 "Freshness" */

    /*
     * Error frames are rate-based, never first-strike. Aborting on the first
     * one ended three consecutive armed runs. The running-generator regime
     * produces 0.07-0.21/s and bursts to 4 in any 10 s; a background measured
     * engine-off reads ~0/s and is useless for calibration. 10 in 10 s keeps
     * 2.5x headroom over the worst observed burst, while a genuine same-ID
     * collision at 100 Hz would blow past it inside a second.
     */
    c->err_window_us = 10000000LL;
    c->err_min_trip  = 10;

    c->diag_period_ms = 300;

    /*
     * A run of hard receive errors means the driver is gone underneath us --
     * for example another subsystem called can_disable(). Disarm rather than
     * keep retrying, so the device parks itself in a safe, answerable state
     * instead of needing a power cycle.
     */
    c->max_rx_errors = 20;

    c->fw_version = 1;
    c->git_hash   = 0;
    c->autoarm_configured = false;
}

/* ----------------------------------------------------------- reason names -- */

/*
 * LOAD-BEARING STRINGS. These are what /gen_inhibit reports as "arm_block"
 * and "abort_reason", so anything that greps them is coupled to this table.
 * See the warning in the header before changing one.
 */
const char *gi_block_name(gi_block_t b)
{
    switch (b)
    {
    case GI_BLOCK_NONE:                return "";
    case GI_BLOCK_NOT_ARMED:           return "not armed";
    case GI_BLOCK_GATE_NOT_EVALUATED:  return "gate not yet evaluated";
    case GI_BLOCK_NO_FRESH_CMD:        return "no fresh 0x051";
    case GI_BLOCK_VCM_FAULT:           return "VCM fault active";
    case GI_BLOCK_GEN_RUNNING:         return "generator running";
    case GI_BLOCK_VCM_REQ_ENGINE:      return "VCM requesting engine";
    case GI_BLOCK_VCM_TORQUE:          return "VCM commanding torque";
    case GI_BLOCK_SHUTDOWN_CMD:        return "VCM commanding 0x10";
    case GI_BLOCK_NO_SOC_VALID:        return "SoC not yet valid (contactors)";
    case GI_BLOCK_CONTACTORS_OPEN:     return "main contactors not closed";
    case GI_BLOCK_NO_FRESH_FAULT:      return "no fresh 0x617";
    case GI_BLOCK_NO_FRESH_SHIFT:      return "no fresh 0x639";
    case GI_BLOCK_DISABLED:            return "latched disable";
    case GI_BLOCK_ABORTED:             return "latched abort (key-on clears)";
    }
    return "?";
}

const char *gi_abort_name(gi_abort_t a)
{
    switch (a)
    {
    case GI_ABORT_NONE:            return "";
    case GI_ABORT_TX_FAILED:       return "transmit failed";
    case GI_ABORT_VCM_FAULT:       return "0x617 B7 = 0xCA (VCM fault active)";
    case GI_ABORT_BUS_LOST:        return "bus lost -- no 0x051 (latched; CAN link down)";
    case GI_ABORT_INVERTER_LOST:   return "0x471 stopped while 0x051 still live -- inverter lost";
    case GI_ABORT_ENGINE_TURNING:  return "engine turning while armed -- the inhibit did not hold";
    case GI_ABORT_ERROR_RATE:      return "error-frame rate exceeded";
    case GI_ABORT_STALE_SOC:       return "0x411 went stale while live -- BMS lost";
    case GI_ABORT_STALE_CONTACTOR: return "0x440 went stale while live -- BMS lost";
    case GI_ABORT_STALE_FAULT:     return "0x617 went stale while live -- VCM fault flag lost";
    case GI_ABORT_STALE_SHIFT:     return "0x639 went stale while live -- shift position lost";
    case GI_ABORT_STALE_RPM:       return "0x054 stopped while live -- generator speed lost";
    case GI_ABORT_SHORT_VCM_FRAME: return "0x051 arrived with DLC < 6 -- cannot build a reply";
    case GI_ABORT_TX_NOT_QUEUED:   return "inhibit frame could not be queued";
    case GI_ABORT_TX_LATE:         return "inhibit frame still unsent when the VCM's next 0x051 arrived";
    }
    return "?";
}

const char *gi_self_off_name(gi_self_off_t r)
{
    switch (r)
    {
    case GI_SELF_OFF_NONE:      return "";
    case GI_SELF_OFF_RX_ERRORS: return "receive errors -- driver gone (spec 7)";
    case GI_SELF_OFF_QUIESCE:   return "forced quiesce -- another task took the bus (spec 8)";
    }
    return "?";
}

const char *gi_disable_name(gi_disable_t d)
{
    switch (d)
    {
    case GI_DISABLE_NONE:     return "";
    case GI_DISABLE_M_MODE:   return "m_mode";
    case GI_DISABLE_LOW_SOC:  return "low_soc";
    }
    return "?";
}

const char *gi_event_name(gi_event_kind_t k)
{
    switch (k)
    {
    case GI_EV_NONE:                return "none";
    case GI_EV_DISABLED:            return "DISABLED";
    case GI_EV_SHUTDOWN_SUPPRESS:   return "SHUTDOWN_SUPPRESS";
    case GI_EV_INHIBIT_LIVE:        return "INHIBIT_LIVE";
    case GI_EV_ABORT:               return "ABORT";
    case GI_EV_TX_FAIL:             return "TX_FAIL";
    case GI_EV_RX_ERROR_DISARM:     return "RX_ERROR_DISARM";
    case GI_EV_MODE:                return "MODE";
    case GI_EV_KEY:                 return "KEY";
    case GI_EV_KEY_CLEAR:           return "KEY_CLEAR";
    case GI_EV_SOC_VALID:           return "SOC_VALID";
    }
    return "?";
}

/* -------------------------------------------------------------- plumbing -- */

static void emit(gi_state_t *st, gi_emit_t *out, const gi_frame_t *f)
{
    if (out == NULL)
    {
        return;
    }
    /*
     * THE CHOKE POINT. Every frame this core emits passes through here, so
     * this is where "PASSIVE transmits no 0x051" (spec 3.1) is made true
     * regardless of what any caller does. The dispatch already never builds
     * the frame outside INHIBIT; this is the second wall, and it is cheap.
     *
     * emit_refused must always read zero. A non-zero value means a code path
     * tried to transmit a real command from a mode that must not, which is a
     * bug in this file -- reported rather than silently corrected, because
     * "it never happened" and "it happened and we hid it" have to look
     * different in the JSON.
     */
    if (f->kind == GI_TX_INHIBIT && st->mode != GI_INHIBIT)
    {
        st->emit_refused++;
        return;
    }
    if (out->n >= GI_EMIT_MAX)
    {
        /* A bug in this file, not a condition to handle. See the header. */
        out->dropped++;
        return;
    }
    out->f[out->n++] = *f;
}

static void ev_add(gi_events_t *ev, int64_t t, gi_event_kind_t k,
                   int32_t a, int32_t b, int32_t c)
{
    if (ev == NULL)
    {
        return;
    }
    if (ev->n >= GI_EVENT_MAX)
    {
        ev->dropped++;
        return;
    }
    gi_event_t *e = &ev->e[ev->n++];
    e->t_us = t;
    e->kind = (uint8_t)k;
    e->a = a;
    e->b = b;
    e->c = c;
}

/* ------------------------------------------------------------ histograms -- */

const uint32_t gi_bucket_us[GI_NBUCKETS] = {
    50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 0xFFFFFFFFu
};

void gi_hist_reset(gi_hist_t *h)
{
    memset(h, 0, sizeof(*h));
    h->min_us = 0xFFFFFFFFu;
}

void gi_hist_add(gi_hist_t *h, uint32_t us)
{
    h->count++;
    h->sum_us += us;
    if (us < h->min_us) h->min_us = us;
    if (us > h->max_us) h->max_us = us;
    for (int i = 0; i < GI_NBUCKETS; i++)
    {
        if (us < gi_bucket_us[i])
        {
            h->buckets[i]++;
            return;
        }
    }
}

/* ------------------------------------------------------------ extractors -- */

int32_t gi_le16c(const uint8_t *d, int32_t zero)
{
    return (int32_t)((uint32_t)d[0] | ((uint32_t)d[1] << 8)) - zero;
}

uint8_t gi_mainc_stat(const uint8_t *d)
{
    /*
     * bcm_mainc_stat: 4-bit unsigned at bit 2 of B0, Intel (epri-pt-bus.dbc,
     * EPRI_BCM_Data2_0440). Cross-checked against cantools over 15,188 real
     * frames by artifacts/gen-inhibit/contactor_state_check.py, which also
     * shows the whole close sequence the truck walks through:
     * 0 ALL_OPEN -> 2 CLOSE_MAINR -> 5 CLOSE_MAINN -> 7 WAIT_PRECHARGE ->
     * 8 CLOSE_MAINP -> 9 OPEN_MAINR -> 11 MAIN_PN_CLOSED_DRIVE, and
     * 13 SHUTDOWN_REQUEST -> 14 ALL_OPEN_SHUTDOWN on the way down.
     */
    return (uint8_t)((d[0] >> 2) & 0x0F);
}

uint32_t gi_soc_raw(const uint8_t *d)
{
    /* BMS_SoC_HiRes: 14-bit big-endian at bit 7, scale 0.01 %. */
    return ((uint32_t)d[0] << 6) | (uint32_t)(d[1] >> 2);
}

uint8_t gi_shift_pos(const uint8_t *d)
{
    return (uint8_t)((d[6] >> 4) & 0x07);
}

uint8_t gi_ctr(const uint8_t *d)
{
    return (uint8_t)(d[5] & 0x0F);
}

/*
 * A one-bit extractor looks too simple to be worth naming, and that is the
 * argument for naming it: it is a hand transcription of `SG_ IgnitionKeyState
 * : 4|1@1+`, and an off-by-one in a bit position is exactly the class of
 * error every other test in this suite would agree with. Exposed so
 * test_signals.py can put it against cantools like the other four.
 */
bool gi_key_bit(const uint8_t *d)
{
    return (d[0] & GI_KEY_ON_MASK) != 0;
}

bool gi_key_on(const gi_state_t *st, int64_t now)
{
    /*
     * Spec 7.1 rule 1, and note the order of the two terms is immaterial --
     * both must hold. Never-seen reads OFF, which is the conservative answer
     * and the literal spec: a device that has not heard 0x592 has not read
     * the key on, so it does not transmit. That is not a hypothetical on the
     * truck (0x592 was present in 1045 of 1045 powertrain epochs), but it IS
     * the state a partial bench bus produces, which is why every synthetic
     * scenario now carries a key train.
     */
    return st->key_on && gi_fresh(st, st->have_key, st->seen_key, now);
}

bool gi_fresh_w(const gi_state_t *st, bool have, int64_t stamp, int64_t now,
                int64_t window_us)
{
    (void)st;
    /*
     * Plain signed comparison, deliberately. See the time note in the header:
     * this is int64 microseconds and does not wrap, so the unsigned-
     * subtraction idiom the interposer core needs would be wrong here.
     */
    return have && (now - stamp) < window_us;
}

bool gi_fresh(const gi_state_t *st, bool have, int64_t stamp, int64_t now)
{
    return gi_fresh_w(st, have, stamp, now, (int64_t)st->cfg.fresh_us);
}

/* ------------------------------------------------------------ lifecycle -- */

void gi_reset_stats(gi_state_t *st)
{
    gi_hist_reset(&st->rx_gap);
    gi_hist_reset(&st->response);
    st->tx_ok = st->tx_fail = st->other_frames = 0;
    st->ctr_steps_ok = st->ctr_steps_bad = 0;
    st->have_last_ctr = false;
    st->rx_errors = 0;

    /*
     * Live bus-derived state, not a statistic, but it must not survive an arm
     * cycle: disarming mid-0x10 and re-arming would otherwise start
     * suppressed on a stale reading until the next 0x051. It is recomputed
     * from the first frame either way; this just makes the starting point
     * honest.
     */
    st->shutdown_suppressed = false;

    /*
     * Spec 7: a new arm starts from a clean gate. Signal freshness is
     * deliberately NOT cleared -- it is a property of the bus, not of this
     * run, and dropping it would make every arm wait a fresh round before the
     * interlocks could pass. The spec 6.2 SoC-valid marker is not cleared
     * here for the same reason: it says whether the BMS has published since
     * it last woke, which an arm cycle does not change. Contrast fb_ever
     * below, which IS per-arm-cycle by definition.
     */
    st->inhibit_live = false;
    st->arm_block    = GI_BLOCK_GATE_NOT_EVALUATED;
    st->abort_reason = GI_ABORT_NONE;
    /* A new arm answers the question; the reason described the previous OFF. */
    st->self_off     = GI_SELF_OFF_NONE;
    /*
     * Spec 7.1: an explicit re-arm clears the section 7 latch, exactly as it
     * always did -- a latched abort used to be expressed as mode OFF, and
     * arming out of OFF is what cleared it. The flag is what carries that now.
     * The section 6 `disabled` latch is still NOT cleared here.
     */
    st->abort_latched = false;
    st->rpm_over = false;
    st->rpm_over_since = 0;
    st->have_err_window = false;
    st->err_window = 0;
    st->fb_ever = false;
    st->rpm_ever = false;

    /*
     * Not in the pre-refactor reset, but it was reset in the worker's OFF
     * branch for exactly the same reason and with the same comment: a stale
     * previous-0x051 would make the first gap after the next arm the whole
     * idle interval, silently poisoning every run after the first. Doing it
     * here as well is harmless -- the value is only read when have_prev_051
     * is set -- and makes the histogram reset complete in one place.
     */
    st->have_prev_051 = false;
    st->prev_051 = 0;

    /*
     * Spec 7 trip 7 (review C1): no frame of ours is outstanding across an arm
     * cycle or a disarm. The previous run's frame either completed or the run
     * ended with it; carrying the flag would make the first 0x051 of the next
     * run abort on a transmit that was not this run's.
     */
    st->tx_pending = false;
    st->tx_pending_have_rx = false;
}

void gi_init(gi_state_t *st, const gi_config_t *cfg)
{
    memset(st, 0, sizeof(*st));
    if (cfg != NULL)
    {
        st->cfg = *cfg;
    }
    else
    {
        gi_config_defaults(&st->cfg);
    }

    st->mode = GI_OFF;
    st->offset_us = 500;
    st->last_shift_pos = 0xFF;      /* never seen */
    st->vcm_fault = 0xC8;           /* no fault */
    st->mainc_stat = GI_MAINC_NEVER_SEEN;
    st->disable_code = GI_DISABLE_NONE;
    gi_reset_stats(st);
    st->arm_block = GI_BLOCK_NOT_ARMED;
}

void gi_set_mode(gi_state_t *st, gi_mode_t mode, uint32_t offset_us,
                 int64_t now, gi_events_t *ev)
{
    st->offset_us = offset_us;
    /*
     * Reset only when arming. Clearing on disarm too meant a client that did
     * the obvious thing -- stop, then read the results -- got zeros back,
     * which is indistinguishable from "the device received nothing".
     * Statistics now survive the disarm and live until the next arm.
     */
    if (mode != GI_OFF)
    {
        gi_reset_stats(st);
    }
    st->mode = mode;
    ev_add(ev, now, GI_EV_MODE, (int32_t)mode, (int32_t)offset_us, 0);
}

void gi_notify_off(gi_state_t *st)
{
    /*
     * Clear the live flag on EVERY route into OFF, not just on the abort
     * path. gi_reset_stats() only runs when arming, so an ordinary disarm
     * would otherwise leave inhibit_live set: harmless for transmission,
     * since the dispatch also tests the mode, but it would make diag_flags
     * bit5 and the JSON claim a live inhibit on a disarmed device -- exactly
     * the kind of reassuring-but-wrong reading these flags exist to prevent.
     */
    if (st->inhibit_live)
    {
        st->inhibit_live = false;
        st->arm_block = GI_BLOCK_NOT_ARMED;
    }
    /*
     * Unconditionally, not just on release: a stale previous-0x051 would make
     * the first gap after the next arm the whole idle interval, silently
     * poisoning every run after the first.
     */
    st->have_prev_051 = false;
    st->prev_051 = 0;

    /*
     * Spec 7 trip 7 (review C1): no frame of ours is outstanding across an arm
     * cycle or a disarm. The previous run's frame either completed or the run
     * ended with it; carrying the flag would make the first 0x051 of the next
     * run abort on a transmit that was not this run's.
     */
    st->tx_pending = false;
    st->tx_pending_have_rx = false;
}

/* ------------------------------------------------------------- interlocks -- */

/*
 * A section 7 abort is a LATCHED STAND-DOWN. It does NOT set mode = GI_OFF,
 * and that is a change made for spec 7.1 (2026-09-20).
 *
 * WHY IT CHANGED. Rule 2 says a key-off -> key-on transition clears the
 * latch. That is unimplementable against a mode-OFF abort: the worker's OFF
 * branch in gen_inhibit.c does not call twai_receive() at all -- it parks,
 * delays 50 ms and loops -- and the host runner reproduces that by DROPPING
 * frames while OFF. A device that aborted would therefore be deaf to the key
 * coming back, forever, which is precisely the end-of-every-drive case that
 * finding 3 measured (100 of 100 key-offs would trip the section 7 inverter
 * trip as it stood).
 *
 * WHAT DID NOT CHANGE. Nothing is less latched than it was. The gate does not
 * run while abort_latched is set, inhibit_live is false, and the transmit
 * dispatch tests both -- so the device transmits nothing, which is the whole
 * content of the old mode-OFF. This is the same shape as the section 6
 * `disabled` latch it now sits beside, and it has the same two exits: an
 * explicit re-arm, or (new) a key-on.
 *
 * WHAT IT BUYS BESIDES. Diag keeps going out, because gi_tick() emits the
 * heartbeat whenever mode != OFF. A latched device now says so on the wire
 * for as long as it stays latched, instead of falling silent in a way that is
 * indistinguishable from having been switched off.
 */
/*
 * Spec 7's EVIDENCE RULE for trips 3 and 5 (decided 2026-09-25).
 *
 * A signal going stale counts as that signal being lost only if an 0x051 has
 * arrived AFTER its freshness window expired -- i.e. the bus was demonstrably
 * alive at the moment the missing signal should have been there.
 *
 * WITHOUT IT, A WHOLE-BUS LOSS IS ALWAYS MISREPORTED. One 0.5 s window across
 * signals of different cadence means the SLOWEST expires first when everything
 * stops together: 0x617 runs at 4 Hz, so its last frame precedes 0x051's by up
 * to 250 ms and its window closes that much earlier. Measured on the host
 * suite before this rule: a trailing silence aborted at 8.380 s naming 0x617,
 * where trip 2 would have fired at 8.480 s naming the link. A pulled connector
 * would have reported "VCM fault flag lost" and sent someone after the VCM.
 *
 * Note what this is NOT. It is not a safety change -- every trip ends the
 * inhibit and latches identically, and the device stands down either way. It
 * is about the reason, which goes on the wire in section 10 and is what a log
 * gets read for.
 *
 * `now` is not used: the test is between two arrival times, not against the
 * present. A signal that expired long ago and a bus that has been quiet ever
 * since still fails this test, which is the intended answer.
 *
 * `have_x` IS CURRENTLY REDUNDANT, and that is deliberate rather than
 * overlooked. Dropping it survives every mutation test (K01, 2026-09-25):
 * every call site is already behind !gi_fresh(), the arm gate requires 0x617,
 * 0x639, 0x440 and 0x411 to have been seen before the inhibit can go live, and
 * 0x054/0x471 are behind fb_ever/rpm_ever. So no caller can reach here with a
 * never-seen signal today. It stays because that is a conjunction of four
 * other rules, any one of which could be relaxed for a good reason -- and the
 * failure if one were would be a stale timestamp of 0 comparing as an ancient
 * arrival, which is precisely the sentinel trap this file's header exists to
 * warn about.
 */
static bool bus_alive_since_w(const gi_state_t *st, bool have_x,
                             int64_t seen_x, int64_t window_us)
{
    return st->have_cmd && have_x
        && st->seen_cmd > seen_x + window_us;
}

static bool bus_alive_since(const gi_state_t *st, bool have_x, int64_t seen_x)
{
    return bus_alive_since_w(st, have_x, seen_x, (int64_t)st->cfg.fresh_us);
}

static void inhibit_abort(gi_state_t *st, gi_abort_t why, int64_t now,
                          gi_events_t *ev)
{
    st->abort_reason  = why;
    st->inhibit_live  = false;
    st->abort_latched = true;
    st->arm_block     = GI_BLOCK_ABORTED;
    ev_add(ev, now, GI_EV_ABORT, (int32_t)why, 0, 0);
}

/*
 * Spec 7: may the inhibit go live right now? Every check is against FRESHNESS
 * as well as value -- a silent bus otherwise answers every question you ask
 * it, and answers them all reassuringly.
 */
/*
 * Spec 7's arm gate. Every condition must hold before the inhibit goes live.
 *
 * The checks below are NOT in the spec's numbered order, and that is on
 * purpose: the order decides which reason gets reported when several conditions
 * fail at once, so reordering it would rewrite arm_block in a pile of goldens
 * without changing whether anything goes live. The mapping is:
 *
 *   spec 1 (0x051 fresh)          first check
 *   spec 3 (0x617 fresh, no fault) second and third
 *   spec 4 (0x639 fresh)           fourth
 *   spec 5 (generator stopped)     fifth
 *   spec 6 (VCM not asking)        sixth and seventh
 *   spec 7 (not 0x10-suppressed)   eighth
 *   spec 2 (contactors + 0x411)    last
 *   spec 8 (no latch)              not here -- gi_tick() does not call this
 *                                  function at all while a latch is set
 */
static bool arm_gate_ok(gi_state_t *st, int64_t now)
{
    if (!gi_fresh(st, st->have_cmd, st->seen_cmd, now))
    {
        st->arm_block = GI_BLOCK_NO_FRESH_CMD;
        return false;
    }
    /*
     * 0x471 and 0x054 are deliberately NOT required here, and that is a
     * correction (2026-09-19) rather than an omission.
     *
     * Measured across bus wake events in the corpus: after the bus comes
     * alive, 0x051 appears within ~0.17 s, but the GENE family (0x471, 0x054)
     * appears +28 s later, or not at all within 60 s -- the inverter powers
     * up long after the VCM, and in sessions where the generator is never
     * used it never appears. Requiring them to arm would mean the inhibit
     * went live ~28 s after key-on at best and never at worst, while the
     * evap-driven start it exists to prevent follows key-on almost
     * immediately.
     *
     * It is also self-defeating: requiring fresh 0x471 means arming only once
     * the inverter is powered, which is the neighbourhood of the state spec 7
     * explicitly refuses to arm into. Their absence is positive evidence the
     * generator is NOT running.
     *
     * Spec 7 lists 0x471 only as a DISARM condition -- "disarm if the
     * inverter goes quiet (0x471 stops)" -- and "stops" presupposes it was
     * going. The runtime trip below honours that by firing only once it has
     * been seen.
     */
    /*
     * Spec 7 condition 3 (review B2/B3). Freshness FIRST, then the value: a
     * never-seen 0x617 used to read as "no fault" because vcm_fault is
     * initialised to 0xC8, which is a silent bus answering a safety question
     * in the reassuring direction. 0x617 is the tightest signal here against
     * the 0.5 s window -- 4 Hz, worst measured gap 308 ms -- so this is the
     * condition most likely to block on a marginal bus, which is the intended
     * direction.
     */
    /* 0x617 on its own 1.0 s window -- spec 7 "Freshness". */
    if (!gi_fresh_w(st, st->have_fault, st->seen_fault, now,
                    (int64_t)st->cfg.fault_fresh_us))
    {
        st->arm_block = GI_BLOCK_NO_FRESH_FAULT;
        return false;
    }
    if (st->vcm_fault == GI_FAULT_ACTIVE)
    {
        st->arm_block = GI_BLOCK_VCM_FAULT;
        return false;
    }
    /*
     * Spec 7 condition 4: 0x639 fresh, so the section 6.1 M-mode release is
     * evaluating a real reading rather than last_shift_pos's 0xFF sentinel.
     */
    if (!gi_fresh(st, st->have_shift, st->seen_shift, now))
    {
        st->arm_block = GI_BLOCK_NO_FRESH_SHIFT;
        return false;
    }
    /*
     * Refuse while the generator is already running or the VCM is asking for
     * torque. Taking over a loaded generator and commanding zero sheds the
     * engine's whole load in one frame -- a load dump on a running engine,
     * and the wrong way to find out whether the mechanism works. The only
     * transition this arms into is "engine off, VCM tries to start it, we
     * stop it".
     *
     * Only meaningful if 0x054 is actually arriving. A stale reading cannot
     * say the generator is running -- but nor can it say it is stopped, so
     * this leans on the GENE family's silence being evidence of an unpowered
     * inverter rather than trusting a last-known value.
     */
    if (gi_fresh(st, st->have_rpm, st->seen_rpm, now)
        && st->gene_rpm >= st->cfg.start_abort_rpm)
    {
        st->arm_block = GI_BLOCK_GEN_RUNNING;
        return false;
    }
    if (st->vcm_rpm_ref >= 0)
    {
        st->arm_block = GI_BLOCK_VCM_REQ_ENGINE;
        return false;
    }
    if (st->vcm_torque != 0)
    {
        st->arm_block = GI_BLOCK_VCM_TORQUE;
        return false;
    }
    if (st->shutdown_suppressed)
    {
        st->arm_block = GI_BLOCK_SHUTDOWN_CMD;
        return false;
    }
    /*
     * Spec 7 condition 2, spec 6.2 (review A1). The main contactors must
     * report closed and an 0x411 must have arrived since, so the inhibit never
     * goes live on a SoC the BMS has not published since waking.
     *
     * This costs nothing in coverage: the engine is cranked by the HV
     * generator inverter, which cannot run with the contactors open, so there
     * is no start to inhibit before the close. What it buys is that the
     * low-SoC release -- which latches for the whole drive -- is never decided
     * by a wake reading. Measured on vtrux_20260513_174225_T4: 0x411 comes
     * back at 202.289 s reading 18.77 % against a real 24.41 %, and
     * bcm_mainc_stat does not reach 11 until 203.459 s, so all 24 false
     * readings fall outside the marker.
     *
     * The staleness half of the marker is maintained in interlock_runtime();
     * the reason this is only a gate check is that the marker must also stop
     * SoC from voting, which it does in disable_monitor().
     */
    if (!st->soc_valid || !st->soc_since_valid)
    {
        st->arm_block = GI_BLOCK_NO_SOC_VALID;
        return false;
    }
    /*
     * ...and the contactors must read closed NOW, not merely have done
     * (user, 2026-09-25, resolving spec 6.2 against spec 7 condition 2).
     *
     * The marker deliberately survives the contactors opening while the BMS
     * stays awake -- no wake has happened, so the readings are still good, and
     * that is spec 6.2's rule for the VALIDITY of a reading. But validity is
     * not permission: with the contactors open there is no HV, the inverter
     * cannot crank, and an arm evaluated in that window (a POST during a
     * key-off with the BMS still talking) would go live with nothing to
     * inhibit. Spec 7 is the stricter of the two and it wins.
     *
     * Freshness comes free: soc_marker_tick() clears the marker on a stale
     * 0x440 and runs earlier in the same gi_tick(), so reaching here with
     * soc_valid set means 0x440 was fresh this tick. The reading is what still
     * has to be tested.
     */
    if (st->mainc_stat != GI_MAINC_CLOSED_DRIVE
        && st->mainc_stat != GI_MAINC_CLOSED_CHARGE)
    {
        st->arm_block = GI_BLOCK_CONTACTORS_OPEN;
        return false;
    }
    st->arm_block = GI_BLOCK_NONE;
    return true;
}

/*
 * Spec 7: the trips that end a live run. Called every loop, not only on an
 * 0x051, so a bus that goes quiet is caught by the receive timeout.
 */
static void interlock_runtime(gi_state_t *st, int64_t now, const gi_bus_t *bus,
                              gi_events_t *ev)
{
    if (st->vcm_fault == GI_FAULT_ACTIVE)
    {
        inhibit_abort(st, GI_ABORT_VCM_FAULT, now, ev);
        return;
    }

    /*
     * THE BUS ITSELF HAS GONE. 0x051 runs at ~100 Hz whenever the link is up,
     * so its absence is the anchor test for "we are no longer connected to
     * the powertrain bus" -- a pulled connector, a dropped transceiver,
     * key-off. Framed as link loss deliberately, rather than as individual
     * signals going offline: the realistic failure is the CAN connection
     * dropping entirely, in which case every signal stops together and 0x051
     * is the one to notice it by.
     *
     * THIS LATCHES. Decided 2026-09-19. The tempting alternative is to stand
     * down and re-arm when the bus returns, on the grounds that link loss is
     * an absence rather than a fault and a momentary glitch should not cost a
     * drive. That is rejected: if the link is INTERMITTENT, resuming is the
     * wrong response. A bus that drops and returns is an unreliable
     * environment, and this device does not merely observe it -- it steals
     * the VCM's rolling counter and transmits a real 0x051 onto a live
     * powertrain bus. Coming back automatically would mean doing that
     * repeatedly across a link we already have evidence is unsound, and each
     * resume would re-enter through a gate whose freshness checks a flapping
     * bus can satisfy. Latching turns an intermittent connection into one
     * clean stand-down instead of an oscillation.
     *
     * Latched like the section 6 releases, with the same two exits: an
     * explicit re-arm, or a key-off -> key-on transition (spec 7.1 rule 2,
     * key_monitor() below). Before 7.1 the only exit was a reboot, and a
     * re-key quick enough that the relay never opened left the inhibit
     * latched off for that second drive; that was recorded as an intended
     * reading of "do not resume" and the user overruled it on 2026-09-20.
     * What the latch still buys is that the bus coming back on its own
     * clears nothing -- only the key does, and only on a real 0 -> 1
     * reading, so a flapping link cannot clear the latch it caused.
     *
     * THIS REPLACES THE DEAD-MAN TIMER, which is removed (spec 7,
     * 2026-09-19). Every hazard the timer was still covering reduces to "the
     * signals that would release us stopped arriving", which is now split
     * between this trip and trip 5 above.
     *
     * The original text here said losing 0x051 and 0x411 together "has no
     * plausible cause on a healthy link". Review B3 withdrew that on
     * 2026-09-24: they are different transmitters, and the BMS LV-connector
     * disconnect test (projects/vtrux/README.md, "PT Bus Disconnect Test
     * Evidence", T2) shows 0x411 stopping while the VCM keeps broadcasting.
     * That case is trip 5, not this one.
     */
    if (!gi_fresh(st, st->have_cmd, st->seen_cmd, now))
    {
        inhibit_abort(st, GI_ABORT_BUS_LOST, now, ev);
        return;
    }
    /*
     * Spec 7 trip 5 (review B3, option c). A stale interlock signal while live
     * is a LATCHED abort.
     *
     * The user's reasoning for that severity: if the VCM or the BMS genuinely
     * goes offline the truck stops, or the inhibitor has lost the truck --
     * either way there is nothing left to protect by continuing to transmit a
     * real 0x051 built from readings we can no longer see.
     *
     * ORDER MATTERS HERE, and not for correctness but for what a log says.
     * This runs AFTER the 0x617-fault and 0x051-link tests above, so a whole
     * bus going away is still reported as link loss rather than as whichever
     * signal this function happens to check first. At the end of a drive the
     * BMS stops ~0.26 s after the key reads 0, so on a healthy truck this trip
     * is usually beaten to it by the inverter-lost trip (measured on
     * vtrux_20260513_174225_T4: inverter lost at 168.071 s, 0x440 stale at
     * 168.18 s) -- whichever fires, the next key-on clears it.
     *
     * 0x054 carries the once-heard qualification; the other four cannot be
     * never-seen while live, because the arm gate requires all of them.
     */
    if (!gi_fresh(st, st->have_soc, st->seen_soc, now)
        && bus_alive_since(st, st->have_soc, st->seen_soc))
    {
        inhibit_abort(st, GI_ABORT_STALE_SOC, now, ev);
        return;
    }
    if (!gi_fresh(st, st->have_cont, st->seen_cont, now)
        && bus_alive_since(st, st->have_cont, st->seen_cont))
    {
        inhibit_abort(st, GI_ABORT_STALE_CONTACTOR, now, ev);
        return;
    }
    /*
     * 0x617 on its own 1.0 s window, BOTH here and in the evidence test. The
     * rule is "an 0x051 arrived after this signal's window expired", so the
     * window used to judge staleness and the window used to judge the
     * evidence have to be the same one -- mixing them would ask for an 0x051
     * after 0.5 s while calling the signal stale at 1.0 s, which is a
     * different and much weaker rule than the spec states.
     */
    if (!gi_fresh_w(st, st->have_fault, st->seen_fault, now,
                    (int64_t)st->cfg.fault_fresh_us)
        && bus_alive_since_w(st, st->have_fault, st->seen_fault,
                             (int64_t)st->cfg.fault_fresh_us))
    {
        inhibit_abort(st, GI_ABORT_STALE_FAULT, now, ev);
        return;
    }
    if (!gi_fresh(st, st->have_shift, st->seen_shift, now)
        && bus_alive_since(st, st->have_shift, st->seen_shift))
    {
        inhibit_abort(st, GI_ABORT_STALE_SHIFT, now, ev);
        return;
    }
    /*
     * "Disarm if the inverter goes QUIET (0x471 STOPS)" -- so this can only
     * fire once 0x471 has actually been heard. Without fb_ever the trip would
     * fire the instant we went live on a bus whose inverter has not powered
     * up yet, which measurement says is the normal case for the first ~28 s
     * after key-on and is permanent in sessions where the generator is never
     * used. Never-seen is not the same as stopped.
     *
     * 0x051 is known fresh by this point, AND bus_alive_since() requires an
     * 0x051 to have arrived after 0x471's window closed -- so this is
     * unambiguously the inverter specifically dropping off a live bus, the
     * alarming case and the distinction the laptop tool could not make.
     *
     * The freshness test alone was not enough, and that is not theoretical
     * even here where both signals run at ~100 Hz: if 0x471's last frame
     * straddles a tick boundary before 0x051's, a whole-bus loss reports the
     * inverter. Rarer than the 0x617 case that prompted the rule, same shape.
     */
    if (st->fb_ever && !gi_fresh(st, st->have_fb, st->seen_fb, now)
        && bus_alive_since(st, st->have_fb, st->seen_fb))
    {
        inhibit_abort(st, GI_ABORT_INVERTER_LOST, now, ev);
        return;
    }

    /*
     * 0x054 IS CHECKED AFTER 0x471, AND THE ORDER IS THE RULE.
     *
     * Spec 7, trip 5, decided by the user 2026-09-25: when the GENE family
     * goes quiet TOGETHER -- 0x054 and 0x471 both stale -- it is reported as
     * trip 3, inverter lost, and not as a stale 0x054. A stale 0x054 is trip 5
     * only while 0x471 is still fresh.
     *
     * Checking 0x471 first is the whole implementation. Both frames run at
     * ~100 Hz off the same module, so when the inverter drops they expire
     * within a tick of each other and WHICHEVER TEST RAN FIRST WON -- which
     * made the reported reason depend on nothing but which frame happened to
     * arrive last. The two identical end-of-drive events in replay-rekey-long
     * reported one each, from the same capture, for the same physical event.
     *
     * Trip 3 is the better of the two answers: 0x471 stopping IS the inverter
     * going away, whereas 0x054 stopping is a symptom of it. A reader who sees
     * "generator speed lost" has to work out that the inverter went with it.
     */
    if (st->rpm_ever && !gi_fresh(st, st->have_rpm, st->seen_rpm, now)
        && bus_alive_since(st, st->have_rpm, st->seen_rpm))
    {
        inhibit_abort(st, GI_ABORT_STALE_RPM, now, ev);
        return;
    }



    /*
     * Note this reads gene_rpm WITHOUT a freshness test, where the arm gate
     * above tests freshness. That asymmetry is pre-refactor behaviour and is
     * preserved deliberately rather than tidied: once live, the last known
     * "the engine is turning" is the conservative reading, and whole-bus loss
     * has already been handled above. Flagged in the verification table as
     * something to confirm on the bench rather than assume.
     */
    if (st->gene_rpm >= st->cfg.start_abort_rpm)
    {
        if (!st->rpm_over)
        {
            st->rpm_over = true;
            st->rpm_over_since = now;
        }
        else if (now - st->rpm_over_since > st->cfg.rpm_debounce_us)
        {
            inhibit_abort(st, GI_ABORT_ENGINE_TURNING, now, ev);
            return;
        }
    }
    else
    {
        st->rpm_over = false;
        st->rpm_over_since = 0;
    }

    /*
     * Error frames, rate-based over a sliding window. Note this counts the
     * controller's own bus_error_count rather than anything payload-derived,
     * so it is distinct from the failed-transmit trip, which is unambiguously
     * about a frame of ours.
     */
    if (bus != NULL && bus->err_valid)
    {
        if (!st->have_err_window
            || (now - st->err_window) > st->cfg.err_window_us)
        {
            st->have_err_window = true;
            st->err_window = now;
            st->err_base = bus->bus_error_count;
        }
        else if ((uint32_t)(bus->bus_error_count - st->err_base)
                 >= st->cfg.err_min_trip)
        {
            inhibit_abort(st, GI_ABORT_ERROR_RATE, now, ev);
            return;
        }
    }
}

/* ------------------------------------------------------------------ diag -- */

static void build_diag(const gi_state_t *st, const gi_bus_t *bus, int64_t now,
                       uint8_t which, gi_frame_t *f)
{
    memset(f, 0, sizeof(*f));
    f->dlc = 8;
    f->kind = GI_TX_DIAG;

    if (which == 0)     /* STATUS -> 0x7F1 */
    {
        uint8_t flags = (uint8_t)
              ((st->disabled                 ? 0x01 : 0)
             | ((bus && bus->can_enabled)    ? 0x02 : 0)
             | ((bus && bus->bus_ours)       ? 0x04 : 0)
             | (st->cfg.autoarm_configured   ? 0x08 : 0)
             /*
              * bit4: live, non-latching. Spec 6.4 puts this in the flags
              * rather than disable_reason precisely because it comes and goes
              * -- a reason code that appeared and vanished as the truck
              * parked and woke could not be told apart from a past one when
              * reading a log.
              */
             | (st->shutdown_suppressed      ? 0x10 : 0)
             /*
              * bit5: spec 7 interlocks satisfied and transmitting. Distinct
              * from mode 3, which is only what was asked for -- the two
              * differ for the whole time the gate is waiting on the bus, and
              * that gap is the interesting thing to see in a log.
              */
             | (st->inhibit_live             ? 0x20 : 0)
             /*
              * bit6: spec 7.1 rule 1 -- the key reads on AND 0x592 is fresh,
              * i.e. transmission is permitted. Reads 0 both for a key-off and
              * for a bus where 0x592 never arrived, which is the same thing
              * as far as this gate is concerned; bit1/bit2 separate them.
              *
              * Computed with the diag frame's own timestamp rather than
              * stored, so it cannot go stale in the way it is reporting on.
              */
             | (gi_key_on(st, now)           ? 0x40 : 0)
             /*
              * bit7: spec 7.1 -- a section 7 abort has latched. Before 7.1
              * this state was reported by diag going silent, because the
              * abort set mode OFF; the device now stays armed and says so.
              */
             | (st->abort_latched            ? 0x80 : 0));

        f->id = GI_DIAG_ID_STATUS;
        f->data[0] = GI_DIAG_SCHEMA_VER;
        f->data[1] = (uint8_t)st->cfg.fw_version;
        f->data[2] = (uint8_t)st->mode;
        f->data[3] = flags;
        f->data[4] = (uint8_t)st->disable_code;
        f->data[5] = st->last_shift_pos;
        f->data[6] = (uint8_t)st->soc_raw;          /* soc_x100, LE */
        f->data[7] = (uint8_t)(st->soc_raw >> 8);
    }
    else if (which == 3)    /* STATUS2 -> 0x7F8 (schema 4) */
    {
        f->id = GI_DIAG_ID_STATUS2;
        /*
         * Spec 10: the abort reason on the wire. diag_flags bit7 says THAT a
         * section 7 trip latched; with the trips now numbering eight, a log
         * has to say WHICH, and the PASSIVE dry run depends on it -- a dry run
         * that reports "something tripped" is not a dry run of anything.
         */
        f->data[0] = (uint8_t)st->abort_reason;
        /*
         * Spec 6.2's SoC-valid marker, so a log shows when SoC started
         * counting, plus the arm block so a reader can see WHY it was not
         * live rather than only that it was not.
         */
        f->data[1] = (uint8_t)((st->soc_valid       ? 0x01 : 0)
                             | (st->soc_since_valid ? 0x02 : 0)
                             | (st->tx_pending      ? 0x04 : 0)
                             | (st->emit_refused    ? 0x08 : 0));
        f->data[2] = (uint8_t)st->arm_block;
        /*
         * The would-transmit count (spec 3.1), as its OWN field rather than
         * tx_ok reinterpreted by mode. Spec 10 allowed either; a separate
         * field is chosen because a decoder that ignores diag_mode then cannot
         * silently read a dry run as a drive in which the device transmitted
         * 18,000 times. The two counters are mutually exclusive by mode, and
         * that is a property worth being able to CHECK rather than one a
         * reader has to apply.
         */
        f->data[3] = (uint8_t)st->would_tx;
        f->data[4] = (uint8_t)(st->would_tx >> 8);
        f->data[5] = (uint8_t)(st->would_tx >> 16);
        f->data[6] = (uint8_t)(st->would_tx >> 24);
        /* Spec 5's hazard, saturating: any non-zero value is the point. */
        f->data[7] = (uint8_t)(st->tx_queued_behind > 255 ? 255
                                                          : st->tx_queued_behind);
    }
    else if (which == 1)    /* COUNTERS -> 0x7F2 */
    {
        uint32_t v = st->tx_ok;
        f->id = GI_DIAG_ID_COUNTERS;
        f->data[0] = (uint8_t)v;
        f->data[1] = (uint8_t)(v >> 8);
        f->data[2] = (uint8_t)(v >> 16);
        f->data[3] = (uint8_t)(v >> 24);
        f->data[4] = st->tx_fail       > 255 ? 255 : (uint8_t)st->tx_fail;
        f->data[5] = st->ctr_steps_bad > 255 ? 255 : (uint8_t)st->ctr_steps_bad;
        f->data[6] = st->rx_errors     > 255 ? 255 : (uint8_t)st->rx_errors;
    }
    else                    /* BUILD -> 0x7F3 */
    {
        uint32_t h = st->cfg.git_hash;
        f->id = GI_DIAG_ID_BUILD;
        f->data[0] = (uint8_t)h;
        f->data[1] = (uint8_t)(h >> 8);
        f->data[2] = (uint8_t)(h >> 16);
        f->data[3] = (uint8_t)(h >> 24);
        f->data[4] = (uint8_t)(st->cfg.fw_version);
        f->data[5] = (uint8_t)(st->cfg.fw_version >> 8);
        f->data[6] = GI_DIAG_SCHEMA_VER;
    }
}

/* ------------------------------------------------------------------ tick -- */

/*
 * The staleness half of the spec 6.2 SoC-valid marker.
 *
 * Why here and not in interlock_runtime(): this is not a trip. It runs in every
 * non-OFF mode and whether or not the inhibit is live, because the marker's
 * whole job is to say whether the next 0x411 can be believed -- a question that
 * does not wait for the gate to pass. Trip 5 (spec 7, review B3) is the
 * separate matter of a stale interlock ENDING a live inhibit.
 *
 * Only a signal that has been seen can stop being fresh. Spec 6.2 says the
 * marker clears when 0x411 or 0x440 "stops being fresh", and never-seen never
 * started: reading it the other way makes the marker set and clear in the same
 * 15 ms at boot, which the replay in artifacts/gen-inhibit/
 * contactor_state_check.py showed before this was settled.
 */
static void soc_marker_tick(gi_state_t *st, int64_t now, gi_events_t *ev)
{
    if (!st->soc_valid)
    {
        return;
    }
    const bool soc_gone  = st->have_soc
                        && !gi_fresh(st, st->have_soc, st->seen_soc, now);
    const bool cont_gone = st->have_cont
                        && !gi_fresh(st, st->have_cont, st->seen_cont, now);
    if (!soc_gone && !cont_gone)
    {
        return;
    }
    /*
     * The BMS has gone away, so whatever it says next is a wake reading. On a
     * key-off this is the ordinary case: measured on
     * vtrux_20260513_174225_T4, 0x440 stops 0.45 s after the key reads 0 and
     * 0x411 0.46 s after, and the marker clears 1.0 s later.
     */
    st->soc_valid = false;
    st->soc_since_valid = false;
    st->soc_low_count = 0;
    ev_add(ev, now, GI_EV_SOC_VALID, 0, (int32_t)(soc_gone ? GI_SOC_ID
                                                          : GI_CONTACTOR_ID), 0);
}

void gi_tick(gi_state_t *st, int64_t now, const gi_bus_t *bus,
             gi_emit_t *out, gi_events_t *ev)
{
    if (st->mode == GI_OFF)
    {
        return;
    }

    /*
     * Self-telemetry heartbeat, round-robin across the three diag IDs. Runs
     * before the blocking receive so it still fires on a quiet bus. Armed
     * only.
     */
    /*
     * Spec 10 and spec 7 trip 7: the diag page waits while an inhibit frame is
     * outstanding. Two reasons, and both are load-bearing.
     *
     * TIMING (spec 10, review A4): the diag frames are the lowest-priority IDs
     * on the bus and share the controller's single TX buffer with the inhibit
     * frame, so a diag page queued behind one delays it -- and the whole
     * design is a reactive trail inside the VCM's inter-frame gap.
     *
     * ATTRIBUTION (review C1, 2026-09-25): the driver's TX alerts are latched
     * bits shared by every frame, and TWAI_ALERT_TX_SUCCESS does not say which
     * frame completed. If a diag frame can complete while an inhibit is in
     * flight, the shim cannot tell whose completion it is holding, and the
     * inhibit gets credited for the diag frame's success -- masking the exact
     * case trip 7 exists to catch. Keeping at most one of our frames in flight
     * is what makes the alert attributable at all.
     *
     * Deferring costs nothing: diag is a ~1 Hz heartbeat per page and the
     * outstanding frame resolves within one VCM gap or aborts.
     */
    const bool diag_due = !st->have_last_diag
        || (now - st->last_diag) >= (int64_t)st->cfg.diag_period_ms * 1000;

    /*
     * SPEC 5.1 ITEM 4: WHERE in the gap the page goes, not just whether it is
     * due.
     *
     * While inhibit frames are actually being transmitted, a due page waits for
     * the moment just after an inhibit completes -- which is when the VCM's next
     * 0x051 is furthest away. Queued anywhere else, the lowest-priority frame on
     * the bus can still be in the controller's single TX buffer when the next
     * 0x051 arrives, and the inhibit answering it goes in behind. E4 measured
     * that as tx_behind = 2 over a 5 s run before this existed.
     *
     * "Being transmitted" is live AND key on, because those are the two things
     * that make an inhibit frame happen at all. In every other case -- not live,
     * key off, PASSIVE, RESPOND -- no inhibit is competing for the buffer, so
     * the normal cadence applies and nothing is gained by waiting.
     *
     * AND IT MUST NOT BE ABLE TO GO QUIET. If no completion has been observed
     * for a second, the normal cadence resumes regardless: a device that has
     * stopped transmitting is exactly when the diagnostics are worth having, and
     * a rule that silenced them then would be worse than the timing it fixes.
     */
    /*
     * "IMMEDIATELY after" IS THE OPERATIVE WORD, and the first version of this
     * dropped it.
     *
     * A flag that stays open until a page uses it is not a window: a page that
     * falls due ten milliseconds after a completion still fires at once, and by
     * then the VCM's next 0x051 may be 179 us away. That is exactly what
     * happened -- load-diag-delays-inhibit still reported tx_behind = 2, with
     * the diag at 1060211 us and an inhibit queued behind it at 1060390 us.
     *
     * So the window is BOUNDED IN TIME. 1 ms is comfortably more than one pass
     * round the worker loop (the measured RX-to-TX turnaround is 5 us mean,
     * 38 us max) and comfortably less than the VCM's 4.69 ms minimum gap, which
     * is the whole point: a page queued inside it has ~3.7 ms of room to find an
     * idle slot at ~56 % occupancy. A page that misses the window waits for the
     * next completion, which is at most one VCM gap away.
     */
    /*
     * PASSIVE IS NOT SENDING, and the first version of this got it wrong in a
     * way the comment above already contradicted.
     *
     * `inhibit_live` is set in PASSIVE as much as in INHIBIT -- gi_mode_decides()
     * is the whole point of spec 3.1, the two modes run one decision path and
     * differ only at the moment of transmission. So a test of inhibit_live and
     * the key says "this device has decided to inhibit", not "this device is
     * putting frames on the wire", and PASSIVE puts none there.
     *
     * Without the mode term, switching INHIBIT -> PASSIVE within a second of a
     * completion held diag for up to that second, waiting for a completion that
     * by construction could never arrive. Spec 5.1 item 4 names PASSIVE in its
     * list of cases that keep the normal cadence, so this was a conformance
     * defect and not a judgement call. Found by review, 2026-09-26.
     */
    const bool suppressed = st->shutdown_suppressed;
    const bool sending = st->mode == GI_INHIBIT
        && st->inhibit_live && gi_key_on(st, now) && !suppressed;

    /*
     * THE COMPLETION RECORD BELONGS TO ONE TRANSMITTING EPISODE, and until
     * 2026-09-26 it belonged to the device's whole life: `have_tx_done` was set
     * in gi_on_tx_done() and cleared NOWHERE -- not on a disarm, not on a
     * section 7 abort, not on a mode change. A device that tripped and then came
     * back on a key cycle went live again carrying a timestamp from before the
     * trip.
     *
     * Clearing it here, on the one condition that matters, rather than at each
     * transition that produces it. `sending` false means no inhibit frame can be
     * going out, so any completion on record is from a previous episode and must
     * not gate the next one. Doing it this way needs no enumeration of the ways
     * a device can stop transmitting -- disarm, abort, key off, PASSIVE, 6.3
     * suppression all arrive here identically -- and cannot be left stale by a
     * route somebody forgets to add.
     *
     * `last_tx_done` is deliberately not cleared: it is only ever read behind
     * `have_tx_done`, so zeroing it would add a write that no reader can observe
     * and invite the reader to think otherwise.
     *
     * Spec 5.1 item 4 was amended for this, and the 1 s fallback it used to
     * carry is withdrawn in the same change (user, 2026-09-26). That fallback
     * read `(now - last_tx_done) < 1000000` here. What it was really covering
     * was the PASSIVE defect above -- with no mode term, PASSIVE waited for a
     * completion that could never come, and only the 1 s bound broke the wait.
     * Naming the mode fixes that at the cause, and clearing the record fixes the
     * staleness directly, which leaves the bound redundant: removing it changed
     * none of the 74 host goldens and none of 200 randomised sequences. The
     * worst case it now guards against is a diag hold from going live to the
     * first completion -- one 0x051 gap, 4.69-16.4 ms -- not silence.
     */
    if (!sending)
    {
        st->have_tx_done  = false;
        st->diag_after_tx = false;
    }

    const bool in_window = st->have_tx_done
        && (now - st->last_tx_done) <= GI_DIAG_AFTER_TX_US;

    bool diag_ok = true;
    if (diag_due && sending && st->have_tx_done)
    {
        diag_ok = st->diag_after_tx && in_window;
    }

    if (st->tx_pending)
    {
        /* fall through to the interlocks; the page goes out next tick */
    }
    else if (diag_due && diag_ok)
    {
        gi_frame_t f;
        st->have_last_diag = true;
        st->last_diag = now;
        st->diag_after_tx = false;      /* one page per completion */
        build_diag(st, bus, now, st->diag_page, &f);
        emit(st, out, &f);
        st->diag_page = (uint8_t)((st->diag_page + 1) % GI_DIAG_PAGES);
    }

    soc_marker_tick(st, now, ev);

    /*
     * LOAD-BEARING INVARIANT: interlock_runtime() runs ONLY once the inhibit
     * is live, never on the path to going live. The bus-loss trip inside it
     * latches, and before key-on there is legitimately no 0x051 at all
     * (measured: the PT bus is silent until the key turns, and the GENE
     * family for ~28 s after that). Running the trips on a not-yet-live
     * inhibit would therefore latch the device off during the ordinary
     * pre-key-on wait -- on an auto-arm build, before it had ever inhibited
     * anything. Absence of the bus BEFORE going live is "not ready yet",
     * handled by arm_gate_ok(); absence AFTER going live is "the link we were
     * using has dropped". Do not merge these paths.
     */
    if (gi_mode_decides(st->mode) && (st->disabled || st->abort_latched))
    {
        /*
         * Latched off. latch_disable() and inhibit_abort() both already
         * cleared the live flag at the instant of the latch; this covers the
         * other route to the same state -- re-arming while a release is still
         * latched, where gi_reset_stats() would otherwise leave arm_block
         * reading "gate not yet evaluated" and hide the reason the gate will
         * never run. Belt and braces on the flag too, since it is
         * load-bearing.
         *
         * A section 6 disable outranks a section 7 abort in the reporting
         * when both are set: it is the one that a key-on used to be unable to
         * clear, and it is the one a human has to act on.
         */
        st->inhibit_live = false;
        st->arm_block = st->disabled ? GI_BLOCK_DISABLED : GI_BLOCK_ABORTED;
    }
    else if (gi_mode_decides(st->mode))
    {
        if (st->inhibit_live)
        {
            interlock_runtime(st, now, bus, ev);
        }
        else if (arm_gate_ok(st, now))
        {
            st->inhibit_live = true;
            st->rpm_over = false;
            st->rpm_over_since = 0;
            st->have_err_window = false;
            st->err_window = 0;
            st->abort_reason = GI_ABORT_NONE;
            ev_add(ev, now, GI_EV_INHIBIT_LIVE, 0, 0, 0);
        }
    }
}

/* ----------------------------------------------------------------- frames -- */

/*
 * Conditions that DISABLE the inhibit, latched. Both mean the
 * engine/generator is legitimately wanted, so we stop transmitting and stay
 * stopped. Two exits, neither of them SoC or M mode going away again: an
 * explicit re-arm, or a key-off -> key-on transition (spec 7.1 rule 2). A
 * power cycle also clears them, the relay dropping at truck sleep being the
 * ordinary case, but it is no longer the only way out.
 *
 *   M mode  -- driver manually commanding the engine (0x639 shift_lever_pos
 *              == 4, "Manual_generator_mode", confirmed in the powertrain
 *              DBC).
 *   Low SoC -- pack needs the generator to charge (0x411 BMS_SoC_HiRes).
 */
/*
 * Latch a section 6 release.
 *
 * Spec 6.4 (2026-09-19): a latched release is a STAND-DOWN, so the section 7
 * live flag clears with it. This is not cosmetic. `inhibit_live` is one of the
 * terms that authorises a transmit in gi_on_frame(), so leaving it set means a
 * control flag reads "authorised" while transmission is forbidden, and the
 * only thing preventing a transmit is the `!disabled` term sitting beside it
 * in the same && chain. That is a one-term margin that depends on the order of
 * a boolean expression, and it is exactly the kind of thing a later edit
 * breaks silently.
 *
 * Cleared here, at the instant of the latch, rather than on the next tick, so
 * that the dispatch later in this same gi_on_frame() call already sees it
 * false and does not rely on `!disabled` at all.
 *
 * Found by the host suite. The pre-refactor code left the flag frozen true for
 * the rest of the run, because the whole interlock block is guarded by
 * `mode == INHIBIT && !disabled` and the worker never reaches its OFF branch
 * while the mode is still INHIBIT.
 */
static void latch_disable(gi_state_t *st, gi_disable_t why, int32_t detail,
                          int64_t now, gi_events_t *ev)
{
    st->disabled = true;
    st->disable_code = why;
    st->inhibit_live = false;
    st->arm_block = GI_BLOCK_DISABLED;
    ev_add(ev, now, GI_EV_DISABLED, (int32_t)why, detail, 0);
}

static void disable_monitor(gi_state_t *st, uint32_t id, uint8_t dlc,
                            const uint8_t *data, int64_t now, gi_events_t *ev)
{
    if (st->disabled)
    {
        /*
         * Latched; nothing more to evaluate until it is cleared.
         *
         * THIS EARLY RETURN IS WHAT MAKES SPEC 6.2'S "UNCONDITIONAL ON SoC
         * RECOVERING" STRUCTURAL rather than a rule someone has to remember.
         * The recovery branch below cannot run once a release has latched, so
         * even a mutation that explicitly clears `disabled` there is dead code
         * -- confirmed by mutation on 2026-09-25 (K21: unkillable, because
         * unreachable, not because untested). Move this guard and that stops
         * being true.
         */
        return;
    }

    if (id == GI_MMODE_ID && dlc >= 7)
    {
        uint8_t pos = gi_shift_pos(data);
        st->last_shift_pos = pos;
        if (pos == GI_MMODE_VALUE)
        {
            latch_disable(st, GI_DISABLE_M_MODE, 0, now, ev);
        }
    }
    else if (id == GI_SOC_ID && dlc >= 2)
    {
        uint32_t raw = gi_soc_raw(data);
        /*
         * Spec 6.2 (review A1): a reading only counts while the SoC-valid
         * marker is set -- i.e. the main contactors have reported closed and
         * the BMS has not slept since. Readings taken before that are BMS wake
         * readings, wrong by a median 13.8 % and low in 112 of 113 corpus
         * wakes, and this release latches for the rest of the drive.
         *
         * The reading is still recorded as soc_raw for telemetry: hiding what
         * the BMS said would make a log unreadable. It just does not vote.
         */
        st->soc_raw = raw ? raw : st->soc_raw;
        if (!st->soc_valid)
        {
            return;
        }
        if (raw == 0)
        {
            /*
             * Startup sentinel, not 0 % -- treat as not-yet-valid. Kept even
             * though the A1 scan found no raw 0 at any of 114 wakes: a reading
             * of exactly 0 is not evidence of an empty pack whatever produced
             * it, and the marker now covers the case this was guessing at.
             */
            st->soc_low_count = 0;
        }
        else
        {
            if (raw < st->cfg.soc_min_raw)
            {
                if (++st->soc_low_count >= st->cfg.soc_debounce)
                {
                    latch_disable(st, GI_DISABLE_LOW_SOC, (int32_t)raw,
                                  now, ev);
                }
            }
            else
            {
                st->soc_low_count = 0;
            }
        }
    }
}

/*
 * Spec 7.1 rule 2: a key-off -> key-on transition clears every latch and the
 * device re-enters the normal arm gate.
 *
 * WHAT IS CLEARED, AND WHY IT IS "EVERY". The user's ruling was literal --
 * "clears any latch. it does re enter the normal arm gate" -- and the reason
 * it is safe to read it that way is that a key cycle is morally the reboot
 * that these latches were always documented to wait for -- section 6 and the
 * bus-loss trip both used to say they cleared only by a reboot, which the
 * relay dropping at truck sleep provides. A re-key quick enough that the
 * relay never opens is the same event with the power never interrupted, and
 * leaving the device latched across it was recorded as a known consequence,
 * not as a wish. Those comments now name this function as the second exit.
 *
 * Neither section 6 release survives on evidence, either: M mode has to be
 * re-selected by the driver on the new drive before disable_monitor() can see
 * it again, and a low SoC re-latches after cfg.soc_debounce valid samples of
 * 0x411 -- about five frames. Clearing them is a re-evaluation, not an
 * override.
 *
 * fb_ever IS reset, and that is not optional. The inverter powers up ~28 s
 * after the bus does, so re-entering the gate with fb_ever still true would
 * abort on GI_ABORT_INVERTER_LOST the instant the inhibit went live -- which
 * is bug 2 from 2026-09-19 reintroduced through a different door.
 *
 * Statistics are deliberately NOT reset: tx_ok and the histograms span the
 * device's whole powered life, and a re-key is not a new measurement.
 */
static void key_monitor(gi_state_t *st, uint32_t id, uint8_t dlc,
                        const uint8_t *data, int64_t now, gi_events_t *ev)
{
    if (id != GI_KEY_ID || dlc < 1)
    {
        return;
    }

    bool on = gi_key_bit(data);

    st->have_key = true;
    st->seen_key = now;

    if (on != st->key_on)
    {
        st->key_on = on;
        ev_add(ev, now, GI_EV_KEY, on ? 1 : 0, 0, 0);
    }

    if (!on)
    {
        st->key_seen_off = true;
        return;
    }
    if (!st->key_seen_off)
    {
        return;                 /* no transition -- nothing to clear */
    }
    st->key_seen_off = false;

    if (!st->abort_latched && !st->disabled)
    {
        return;                 /* nothing was latched */
    }

    ev_add(ev, now, GI_EV_KEY_CLEAR, (int32_t)st->abort_reason,
           (int32_t)st->disable_code, 0);

    st->abort_latched = false;
    st->abort_reason  = GI_ABORT_NONE;
    st->disabled      = false;
    st->disable_code  = GI_DISABLE_NONE;
    st->soc_low_count = 0;

    /*
     * Spec 6.2: the SoC-valid marker is cleared on every key-on latch clear.
     *
     * Deliberately inside the "something was latched" branch, which is the
     * literal rule and also the right behaviour. A re-key where the BMS
     * actually slept has already cleared the marker through staleness
     * (soc_marker_tick()); a re-key quick enough that it did not sleep leaves
     * genuinely valid readings, and spec 6.2 says the contactors merely
     * opening does not invalidate them. What this covers is the case where a
     * latch is being lifted: the device is about to re-enter the gate, and it
     * must not do so on a reading it accepted before the key cycle.
     *
     * The cost when the contactors are still closed is about 100 ms -- the
     * marker re-sets on the next 0x440 (~20 Hz) and soc_since_valid on the
     * next 0x411 (~20 Hz).
     */
    st->soc_valid       = false;
    st->soc_since_valid = false;

    st->inhibit_live  = false;
    st->arm_block     = GI_BLOCK_GATE_NOT_EVALUATED;
    st->fb_ever       = false;
    st->rpm_over      = false;
    st->rpm_over_since = 0;
    st->have_err_window = false;
    st->err_window      = 0;
    /*
     * rpm_ever goes with fb_ever, and for the same reason: the key-on clear
     * re-enters the arm gate, and the inverter powers up ~28 s after the bus.
     * Carrying either flag across the clear would abort the instant the
     * inhibit went live -- bug 2 of 2026-09-19 through a third door, this one
     * via trip 5.
     */
    st->rpm_ever      = false;
}

/*
 * Record every interlock signal's value AND the time it was last seen. Runs
 * on every frame whatever the mode, so the gate has history the instant a
 * mode change asks for it, rather than having to wait for a fresh round after
 * the request.
 */
static void interlock_monitor(gi_state_t *st, uint32_t id, uint8_t dlc,
                              const uint8_t *data, int64_t now,
                              gi_events_t *ev)
{
    switch (id)
    {
    case GI_VCM_ID:
        st->have_cmd = true;
        st->seen_cmd = now;
        if (dlc >= 5)
        {
            st->vcm_torque  = gi_le16c(&data[1], GI_CMD_ZERO);
            st->vcm_rpm_ref = gi_le16c(&data[3], GI_CMD_ZERO);
        }
        break;
    case GI_FB_ID:
        /* Liveness only. The value is not used; that it arrives is the test. */
        st->have_fb = true;
        st->seen_fb = now;
        st->fb_ever = true;
        break;
    case GI_RPM_ID:
        st->have_rpm = true;
        st->seen_rpm = now;
        st->rpm_ever = true;
        if (dlc >= 2)
        {
            st->gene_rpm = gi_le16c(&data[0], GI_RPM_ZERO);
        }
        break;
    case GI_FAULT_ID:
        /*
         * Stamp freshness on ARRIVAL, not on a long-enough frame. A 0x617 that
         * arrives too short to carry B7 still proves the VCM is talking, which
         * is what the freshness test is asking; treating it as absence would
         * abort a live inhibit over a truncated frame.
         */
        st->have_fault = true;
        st->seen_fault = now;
        if (dlc >= 8)
        {
            st->vcm_fault = data[7];
        }
        break;
    case GI_MMODE_ID:
        /* Freshness only; the value is disable_monitor()'s business. */
        st->have_shift = true;
        st->seen_shift = now;
        break;
    case GI_SOC_ID:
        /*
         * Freshness only; the value is disable_monitor()'s business. Kept here
         * so that "we have heard the BMS recently" is answerable without
         * reference to whether a release has latched -- disable_monitor()
         * returns early once one has, and the spec 6.2 marker and spec 7
         * trip 5 both need the freshness regardless.
         */
        st->have_soc = true;
        st->seen_soc = now;
        if (st->soc_valid)
        {
            st->soc_since_valid = true;
        }
        break;
    case GI_CONTACTOR_ID:
        st->have_cont = true;
        st->seen_cont = now;
        if (dlc >= 1)
        {
            st->mainc_stat = gi_mainc_stat(data);
            /*
             * Spec 6.2: the marker is SET by a closed reading. Only SET here
             * -- clearing is staleness, which is a property of time and so
             * belongs in the tick, and the key-on clear, which is 7.1's.
             *
             * soc_since_valid is armed false on the SET so the arm gate cannot
             * pass on a close with no reading behind it. The first 0x411 after
             * the close is what sets it, in the GI_SOC_ID case above -- not in
             * disable_monitor(), which returns early once a release has
             * latched.
             */
            if ((st->mainc_stat == GI_MAINC_CLOSED_DRIVE
                 || st->mainc_stat == GI_MAINC_CLOSED_CHARGE)
                && !st->soc_valid)
            {
                st->soc_valid = true;
                st->soc_since_valid = false;
                st->soc_low_count = 0;
                ev_add(ev, now, GI_EV_SOC_VALID, 1, (int32_t)st->mainc_stat, 0);
            }
        }
        break;
    default:
        break;
    }
}

static void build_probe(const gi_state_t *st, int64_t t_rx, gi_frame_t *f)
{
    memset(f, 0, sizeof(*f));
    f->id = GI_PROBE_ID;
    f->dlc = 8;
    f->kind = GI_TX_PROBE;

    /* Echo the RX timestamp so a capture can be aligned without guessing. */
    uint32_t lo = (uint32_t)t_rx;
    f->data[0] = (uint8_t)(lo);
    f->data[1] = (uint8_t)(lo >> 8);
    f->data[2] = (uint8_t)(lo >> 16);
    f->data[3] = (uint8_t)(lo >> 24);
    f->data[4] = (uint8_t)(st->offset_us);
    f->data[5] = (uint8_t)(st->offset_us >> 8);
    f->data[6] = st->last_ctr;
    f->data[7] = 0x5A;

    f->have_due = true;
    f->due_us   = t_rx + (int64_t)st->offset_us;
    f->have_t_rx = true;
    f->t_rx = t_rx;
}

/*
 * The truck-validated inhibit frame (DBC + inhibit.py + 18k decoded frames).
 * Rebuilt from the VCM frame we just received:
 *   B0     HELD     0x08, the engine-off state (spec 4.1; was mirrored)
 *   B1,B2  00 80    gen_torque_cmd = 0 (raw 0x8000, 16-bit LE, offset -32768)
 *   B3,B4  mirror   gen_rpm_ref
 *   B5     ctr+1    steal the counter the VCM is about to use (hi nibble 0)
 * DLC 6. B3/B4 and the stolen counter still make this byte-identical to the
 * VCM's next genuine frame whenever the VCM is also in 0x08, which is what
 * makes a counter-validating GENE MCU reject the VCM's real command as a
 * duplicate and accept only our zero.
 *
 * B0 is HELD at the engine-off state, not mirrored (spec 4.1, changed
 * 2026-09-19). Mirroring meant that when the VCM asked for a start it sent
 * 0x0B, we echoed 0x0B with zero torque, and the inverter drew ~10x its
 * engine-off power for the whole inhibit: gen_power median -0.11 kW with
 * excursions to -2.89 kW, against -0.01 kW engine-off. The VCM itself never
 * sustains (0x0B, zero torque) -- the longest such run in the corpus is
 * 0.19 s, a transition it passes through -- so the only sustained instance of
 * that combination anywhere was our own inhibit.
 *
 * Never transmit 0x10: that value is the VCM commanding generator shutdown,
 * and asserting it is the opposite of what this does. Holding a constant 0x08
 * makes that structurally impossible here.
 */
static void build_inhibit(const uint8_t *rx, int64_t t_rx, gi_frame_t *f)
{
    memset(f, 0, sizeof(*f));
    f->id = GI_VCM_ID;      /* 0x051 -- the real command ID */
    f->dlc = 6;
    f->kind = GI_TX_INHIBIT;

    f->data[0] = GI_B0_ENGINE_OFF;
    f->data[1] = 0x00;
    f->data[2] = 0x80;
    f->data[3] = rx[3];
    f->data[4] = rx[4];
    f->data[5] = (uint8_t)(((rx[5] & 0x0F) + 1) & 0x0F);

    /* Reactive trail: transmit immediately, no offset wait -- no due_us. */
    f->have_t_rx = true;
    f->t_rx = t_rx;
}

void gi_on_frame(gi_state_t *st, uint32_t id, uint8_t dlc, const uint8_t *data,
                 int64_t now, gi_emit_t *out, gi_events_t *ev)
{
    /*
     * Spec 7.1 rule 2 first, so a key-on clears the section 6 latch before
     * disable_monitor() gets its early return on it. Safe in the other
     * direction too: the frame that clears is 0x592, which is not a frame
     * disable_monitor() can re-latch from.
     */
    key_monitor(st, id, dlc, data, now, ev);

    /* Watch for M mode / low SoC on every frame -- latches the disable. */
    disable_monitor(st, id, dlc, data, now, ev);

    /*
     * Spec 7: freshness and values for the interlock signals. Must run before
     * the 0x051 filter below -- 0x471, 0x054 and 0x617 are all "other frames"
     * as far as the inhibit is concerned, and they are exactly the ones the
     * safety trips depend on.
     */
    interlock_monitor(st, id, dlc, data, now, ev);

    if (id != GI_VCM_ID)
    {
        st->other_frames++;
        return;
    }

    /*
     * Spec 7 trip 7, third form (review C1): our previous inhibit frame is
     * still outstanding and the VCM's next 0x051 has arrived.
     *
     * This is the case that matters most and the one "could not queue" never
     * caught. The frame may still go out -- and a frame landing AFTER the
     * VCM's next one is worse than one that never lands, because it loses the
     * counter race: the inverter accepted the VCM's torque for that slot while
     * we believed we were inhibiting it.
     *
     * Checked before the counter and gap bookkeeping below, so the abort is
     * attributed to this frame's arrival rather than to the next one.
     */
    if (st->tx_pending && st->inhibit_live)
    {
        st->tx_fail++;
        st->tx_pending = false;
        ev_add(ev, now, GI_EV_TX_FAIL, (int32_t)GI_TX_INHIBIT, 0, 0);
        inhibit_abort(st, GI_ABORT_TX_LATE, now, ev);
        return;
    }

    if (st->have_prev_051)
    {
        gi_hist_add(&st->rx_gap, (uint32_t)(now - st->prev_051));
    }
    st->have_prev_051 = true;
    st->prev_051 = now;

    /*
     * Spec 6.3: track the VCM's shutdown command live, on every 0x051.
     * Recomputed from the current frame rather than latched, so leaving 0x10
     * resumes transmission by itself with no clearing step. Reported on the
     * edge only -- an episode can last 561 s and a per-frame event at 100 Hz
     * would bury everything else.
     */
    if (dlc >= 1)
    {
        bool now_shutdown = (data[0] == GI_B0_SHUTDOWN);
        if (now_shutdown != st->shutdown_suppressed)
        {
            st->shutdown_suppressed = now_shutdown;
            ev_add(ev, now, GI_EV_SHUTDOWN_SUPPRESS,
                   now_shutdown ? 1 : 0, (int32_t)data[0], 0);
        }
    }

    /*
     * B5 is the VCM's rolling counter -- 6422 of 6422 steps were exactly +1
     * in the reference capture. Tracking it here is a cheap check that we are
     * seeing every frame rather than silently dropping some.
     */
    if (dlc >= 6)
    {
        uint8_t ctr = gi_ctr(data);
        if (st->have_last_ctr)
        {
            if (((st->last_ctr + 1) & 0x0F) == ctr) st->ctr_steps_ok++;
            else                                    st->ctr_steps_bad++;
        }
        st->last_ctr = ctr;
        st->have_last_ctr = true;
    }

    if (st->mode == GI_RESPOND)
    {
        gi_frame_t f;
        build_probe(st, now, &f);
        emit(st, out, &f);
    }
    /*
     * Spec 7.1 rule 1 adds gi_key_on() to this chain, and abort_latched
     * beside `disabled` for the same reason that term is there: inhibit_live
     * is already false in both cases, but a one-term margin that depends on
     * the order of a boolean expression is exactly what a later edit breaks
     * silently.
     *
     * The key gates ONLY this -- the real 0x051 onto a live powertrain bus.
     * It does not gate the 0x7F0 timing probe or the 0x7F1-0x7F3 diag pages:
     * those are IDs nothing on the truck consumes, and silencing the diag
     * heartbeat at key-off would take away the telemetry precisely when the
     * end-of-drive behaviour this rule exists for is happening.
     */
    else if (gi_mode_decides(st->mode) && !st->disabled && !st->abort_latched
             && !st->shutdown_suppressed && st->inhibit_live
             && gi_key_on(st, now))
    {
        gi_frame_t f;
        /*
         * Spec 7 trip 8 (review C2b). This is ON RECEIVE: the VCM's frame
         * arrived too short to carry the rolling counter, so there is nothing
         * to steal and no reply can be built.
         *
         * It used to increment tx_fail and return -- no abort, no event, and a
         * counter whose name says the failure was ours. It is not: we never
         * attempted a transmit. A VCM emitting short 0x051 on a bus we are
         * inhibiting is a state nobody has seen and nobody can explain, and
         * quietly skipping frames while continuing to inhibit is the wrong
         * response to not understanding what the truck is doing.
         *
         * Aborting before emit() matters: the inhibit frame is built from THIS
         * frame, so standing down here is what guarantees no malformed reply
         * reaches the wire.
         */
        if (dlc < 6)
        {
            /*
             * Trip 8 fires in PASSIVE too. It is about the frame the VCM SENT,
             * not about a transmit of ours -- spec 3.1 excuses PASSIVE only
             * from the trips that concern our own transmission, which is trip
             * 7 alone.
             */
            st->tx_fail++;
            inhibit_abort(st, GI_ABORT_SHORT_VCM_FRAME, now, ev);
            return;
        }

        /*
         * SPEC 3.1: THE ONE PLACE THE TWO MODES DIFFER, and the only place a
         * 0x051 of ours is ever constructed.
         *
         * PASSIVE does not build a frame and then decline to send it -- it
         * never builds one. That is the same shape as goal 2, where commanding
         * a non-zero torque is impossible because no code path writes anything
         * but 00 80 into B1-B2: the guarantee is the absence of a code path,
         * not a check somebody has to remember to keep. emit() enforces the
         * same thing again at the choke point, so even a future edit that
         * reached here in PASSIVE could not put the frame on the wire.
         */
        if (st->mode == GI_PASSIVE)
        {
            st->would_tx++;
            return;
        }
        build_inhibit(data, now, &f);
        emit(st, out, &f);
    }
}

void gi_on_tx_result(gi_state_t *st, const gi_frame_t *f, bool queued,
                     bool behind, int64_t t_tx, gi_events_t *ev)
{
    if (behind && f->kind == GI_TX_INHIBIT)
    {
        /*
         * Spec 5: this frame went into the queue behind something of ours that
         * had not finished. Counted, not aborted -- it is a timing hazard, not
         * a failure, and whether it actually cost anything shows up as tx_ok
         * not rising or as a TX_LATE abort.
         */
        st->tx_queued_behind++;
    }
    if (f->kind == GI_TX_DIAG)
    {
        /* Best-effort; fails silently in listen-only, and is not counted. */
        return;
    }

    if (queued)
    {
        /*
         * Queued, not sent. Spec 7 trip 7: an inhibit frame counts only once
         * the controller says it completed, which arrives later via
         * gi_on_tx_done(). Until then it is outstanding, and if the VCM's next
         * 0x051 turns up first it has lost the counter race (gi_on_frame()).
         */
        if (f->kind == GI_TX_INHIBIT)
        {
            st->tx_pending = true;
            st->tx_pending_since = t_tx;
            st->tx_pending_t_rx = f->t_rx;
            st->tx_pending_have_rx = f->have_t_rx;
        }
        else
        {
            /*
             * The probe is a measurement, not a command: a late or lost one
             * costs a data point, not a safety property. Counted at the queue
             * as it always was, so the RESPOND timing sweeps stay comparable
             * with every figure recorded before this change.
             */
            st->tx_ok++;
            if (f->have_t_rx)
            {
                gi_hist_add(&st->response, (uint32_t)(t_tx - f->t_rx));
            }
        }
        return;
    }

    st->tx_fail++;
    ev_add(ev, t_tx, GI_EV_TX_FAIL, (int32_t)f->kind, 0, 0);

    /*
     * Spec 7: abort immediately on a failed transmit. Unlike an error frame,
     * which is a property of the bus and is therefore judged on a rate, a
     * failed transmit is unambiguously OURS -- our frame did not go out, so
     * the VCM's command stands and we are not inhibiting anything. There is
     * nothing to average over.
     *
     * Only for the inhibit frame: a dropped timing probe costs a measurement,
     * not a safety property, and the pre-refactor code did not abort on it.
     */
    if (f->kind == GI_TX_INHIBIT)
    {
        inhibit_abort(st, GI_ABORT_TX_NOT_QUEUED, t_tx, ev);
    }
}

void gi_on_tx_done(gi_state_t *st, bool ok, int64_t t_done, gi_events_t *ev)
{
    if (!st->tx_pending)
    {
        /*
         * A verdict for a frame we are not waiting on. The alert watcher can
         * deliver one for a diag frame, which is best-effort and uncounted, so
         * this is not an error -- but it must not credit or fail an inhibit
         * frame that is not outstanding.
         */
        return;
    }
    st->tx_pending = false;

    /*
     * SPEC 5.1 ITEM 4: the window for a diag page opens here and nowhere else.
     *
     * This is the moment an inhibit frame has left the controller, which is the
     * moment the VCM's next 0x051 is furthest away. gi_tick() spends it on a
     * page if one is due. Recording the TIME as well as the flag is what lets
     * the normal cadence resume after a second of silence, so a device that has
     * stopped transmitting still reports -- which is exactly when a reader
     * needs it to.
     */
    st->diag_after_tx = true;
    st->have_tx_done = true;
    st->last_tx_done = t_done;

    if (ok)
    {
        st->tx_ok++;
        if (st->tx_pending_have_rx)
        {
            /*
             * RX to COMPLETION, where this used to be RX to queueing. The
             * histogram therefore now includes the frame's time on the wire
             * and reads higher than every figure recorded before this change
             * by about one frame's air time -- which is exactly the constant
             * offset section 12.1 says the independent witness should show
             * against the device's own number. Comparing a post-C1 sweep with
             * a pre-C1 one without allowing for that will look like a
             * regression and is not one.
             */
            gi_hist_add(&st->response, (uint32_t)(t_done - st->tx_pending_t_rx));
        }
        return;
    }

    st->tx_fail++;
    ev_add(ev, t_done, GI_EV_TX_FAIL, (int32_t)GI_TX_INHIBIT, 0, 0);
    inhibit_abort(st, GI_ABORT_TX_FAILED, t_done, ev);
}

bool gi_on_rx_error(gi_state_t *st, int64_t now, gi_events_t *ev)
{
    st->rx_errors++;
    if (st->rx_errors >= st->cfg.max_rx_errors)
    {
        ev_add(ev, now, GI_EV_RX_ERROR_DISARM, (int32_t)st->rx_errors, 0, 0);
        /*
         * Spec 11: say why. In OFF the worker stops receiving, so this cannot
         * clear itself and nothing further goes out on the wire -- which makes
         * it look exactly like a deliberate disarm to anyone reading the JSON
         * afterwards.
         */
        st->self_off = GI_SELF_OFF_RX_ERRORS;
        st->mode = GI_OFF;
        st->rx_errors = 0;
        return true;
    }
    return false;
}

void gi_on_rx_ok(gi_state_t *st)
{
    st->rx_errors = 0;
}
