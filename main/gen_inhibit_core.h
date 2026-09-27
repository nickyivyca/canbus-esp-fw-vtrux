/*
 * gen_inhibit_core -- the generator-inhibit decision logic, as a pure core.
 *
 * WHY THIS FILE EXISTS
 *
 * Three real bugs were found in this logic on 2026-09-19 by a human reading
 * it, not by any test. All three were state-sequence bugs -- reachable only
 * from a particular ORDER of received frames, and invisible to a compiler:
 *
 *   1. the arm gate required fresh 0x471/0x054, so the inhibit would have gone
 *      live ~28 s after key-on or never;
 *   2. the 0x471 runtime trip fired without ever having seen 0x471, so it
 *      aborted instantly on every normal key-on;
 *   3. inhibit_live survived an ordinary disarm, so telemetry claimed a live
 *      inhibit on a disarmed device.
 *
 * That is exactly the class a host harness replaying real captures catches.
 * This core is therefore written to be compiled and driven by a test program
 * on a PC, with no ESP32 present.
 *
 * WHAT THAT BUYS, PRECISELY (read before trusting a green run)
 *
 * The host harness compiles THIS SAME FILE that the target runs, so it is a
 * REGRESSION test, not a DIFFERENTIAL one. It pins behaviour against recorded
 * goldens and catches changes; it cannot catch a rule that is wrong here and
 * wrong in the golden too. Accepted deliberately by the user on 2026-09-19:
 * we have no independent reference implementation of this logic to diff
 * against (projects/vtrux/tools/gen_inhibit/inhibit.py is a different
 * architecture -- proactive/scheduled, not a reactive trail -- so it is not a
 * 1:1 oracle). The interposer's firmware/test/host_diff/ IS a true
 * differential because machine.py and machine.cpp are independent; do not
 * read this harness as the same kind of evidence.
 *
 * THE RULES THAT KEEP IT PURE (copied from the interposer core's machine.h,
 * which does this successfully)
 *
 *   - no dependency on ESP-IDF, FreeRTOS, or any platform header;
 *   - integer arithmetic only, no floating point;
 *   - no allocation -- the caller supplies every output buffer;
 *   - no wall-clock reads -- time arrives as a parameter;
 *   - no logging -- events are appended to a caller-supplied list as DATA,
 *     and the caller renders them.
 *
 * TIME, AND A DELIBERATE DEPARTURE FROM THE INTERPOSER CORE
 *
 * Time here is `int64_t` MICROSECONDS, monotonic from boot -- the native unit
 * of esp_timer_get_time(). The interposer core takes `uint32_t` milliseconds
 * from millis(), which wraps at 49.7 days, and so it must write every elapsed
 * test as `(uint32_t)(t - since) >= limit` and never `t >= since + limit`.
 *
 * THAT DISCIPLINE DOES NOT APPLY HERE AND MUST NOT BE COPIED IN. A signed
 * 64-bit microsecond counter does not wrap in any relevant lifetime, so the
 * plain comparisons below are correct, and rewriting them into unsigned
 * subtraction would be wrong -- unsigned arithmetic on a signed quantity
 * turns a negative interval into an enormous positive one. If the time base
 * is ever narrowed to 32 bits, that discipline becomes mandatory; until then
 * it is a hazard, not a safeguard.
 *
 * WHAT DOES CARRY OVER IS THE SENTINEL RULE, and for a different reason than
 * wraparound. "Never seen" is never encoded as a timestamp value. The old
 * code used `0` to mean never, which is safe against esp_timer_get_time() but
 * NOT safe against a host harness that rebases a recorded capture to t=0 --
 * there the very first frame is indistinguishable from "no frame ever". Each
 * timestamp therefore carries an explicit `have_*` boolean beside it.
 */
#ifndef GEN_INHIBIT_CORE_H
#define GEN_INHIBIT_CORE_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ IDs -- */

/* The VCM's generator command. Observed, and in INHIBIT also transmitted. */
#define GI_VCM_ID           0x051
/* Timing probe. Deliberately an ID nothing on the truck consumes. */
#define GI_PROBE_ID         0x7F0
#define GI_MMODE_ID         0x639   /* shift lever; pos 4 = manual generator */
#define GI_SOC_ID           0x411   /* BMS_SoC_HiRes */
#define GI_FB_ID            0x471   /* GENE inverter feedback; liveness only */
#define GI_RPM_ID           0x054   /* GENE_RotSpd */
#define GI_FAULT_ID         0x617   /* VCM fault flag in B7 */
/*
 * BMS contactor state (spec 6.2, 7 condition 2; review A1). bcm_mainc_stat is
 * a 4-bit enum at bit 2 of B0 -- confirmed against epri-pt-bus.dbc
 * (EPRI_BCM_Data2_0440) with cantools over 15,188 real frames, and the two
 * closed states are named there.
 */
#define GI_CONTACTOR_ID     0x440
#define GI_MAINC_CLOSED_DRIVE   11  /* MAIN_PN_CLOSED_DRIVE */
#define GI_MAINC_CLOSED_CHARGE  12  /* MAIN_P_CLOSED_CHARGE */
#define GI_MAINC_NEVER_SEEN   0xFF
/*
 * Spec 7.1: key state. EPRI_HCU_Sensor_0592, IgnitionKeyState, which the DBC
 * declares as `SG_ IgnitionKeyState : 4|1@1+` -- Intel start bit 4, one bit,
 * i.e. B0 bit 4. The VCM and the HCU are the same module under two names, so
 * this arrives from the SAME transmitter as 0x051: key state can never be
 * less available than the anchor the whole design already depends on.
 *
 * THE NAME IS INFERRED, NOT CONFIRMED. The DBC comment says "not in plist
 * sampling" -- it came from the decompile. What is measured is the behaviour
 * (spec 7.1's table). If the bit turns out to mean "powertrain awake" rather
 * than "key", the gate still works and the numbers still hold.
 */
#define GI_KEY_ID           0x592
#define GI_KEY_ON_MASK      0x10

#define GI_DIAG_ID_STATUS   0x7F1
#define GI_DIAG_ID_COUNTERS 0x7F2
#define GI_DIAG_ID_BUILD    0x7F3
/*
 * Schema 4 (review B1). A fourth page rather than a repacking of the first
 * three: 0x7F1 is full to B7 and diag_flags has no spare bit, and moving a
 * field would make every recorded log need its schema version consulted to be
 * read at all. Appending leaves 0x7F1-0x7F3 byte-identical, so a schema-3
 * decoder still reads everything it used to and simply does not see this page.
 *
 * 0x7F8, AND NOT 0x7F4, WHICH THIS FIRST SHIPPED AS. The standing allocation
 * on this truck is WiCAN 0x7F1-0x7F3, charge interposer 0x7F4-0x7F7, and both
 * devices transmit on the powertrain bus and may be fitted at once. 0x7F4 is
 * the interposer's INTP_Status: two transmitters on one ID with differing
 * data, which is arbitration with bit errors, error frames feeding this
 * component's own error-rate trip, and a log in which no frame can be
 * attributed to a device.
 *
 * Caught in review, 2026-09-25. The check that missed it was run against the
 * interposer's VIRTUAL BENCH physics ids (tools/interposer/bus.py) while the
 * authoritative list is vtrux-interposer-diag.dbc and
 * interposer_diag_schema.md -- and the agreement itself is written in
 * wican_diag_schema.md, the file being edited at the time. Checking the wrong
 * artifact produced a confident "checked rather than assumed".
 *
 * The WiCAN's range is now 0x7F1-0x7F3 plus 0x7F8, and the next page on either
 * side comes out of the allocation table in both schema docs.
 * artifacts/gen-inhibit/diag_id_allocation.py reports what every Vtrux DBC
 * declares and what the corpus actually carries.
 */
#define GI_DIAG_ID_STATUS2  0x7F8
#define GI_DIAG_PAGES       4

#define GI_MMODE_VALUE      4
#define GI_FAULT_ACTIVE     0xCA
#define GI_B0_ENGINE_OFF    0x08
#define GI_B0_SHUTDOWN      0x10

/*
 * GENE_RotSpd is the odd one out and the DBC says so explicitly:
 *   SG_ GENE_RotSpd : 0|16@1+ (1,-32767)
 *   CM_ "Zero at raw=32767 (0x7FFF). Note: GENE uses 32767, not 32768 like
 *        trac_rpm."
 * The truck-validated Python reference decodes it with its generic le16c(),
 * which subtracts 32768, so it reads this one signal 1 rpm low. Immaterial
 * against a 300 rpm threshold, but the DBC is the source of truth for
 * encoding, so this follows the DBC. gi_rpm_zero()/gi_cmd_zero() are exposed
 * so a host test can assert this distinction still holds.
 */
#define GI_RPM_ZERO         32767
#define GI_CMD_ZERO         32768

/*
 * 2: added diag_flags bit4 (shutdown suppression) and bit5 (inhibit live).
 * 3: added diag_flags bit6 (key on, fresh) and bit7 (latched by a section 7
 *    abort). No field moved, so a v2 decoder still reads everything else.
 * 4: PASSIVE (diag_mode 4) and the new 0x7F8 page -- abort reason, the spec
 *    6.2 SoC-valid marker, the would-transmit count and tx_queued_behind.
 *    Again no field moved; 0x7F1-0x7F3 are byte-identical to schema 3.
 *
 * artifacts/gen-inhibit/wican_diag_schema.md is the source of truth for the
 * layout and vtrux-wican-diag.dbc is the decoder; all three change together
 * or a log becomes unreadable.
 */
/*
 * Spec 5.1 item 4: how long after an inhibit completion a diag page may still
 * be queued. Longer than one pass round the worker loop (5 us mean, 38 us max
 * measured RX-to-TX turnaround) and far shorter than the VCM's 4.69 ms minimum
 * inter-frame gap, so a page queued inside it cannot still be in the
 * controller when the next 0x051 arrives.
 */
#define GI_DIAG_AFTER_TX_US 1000

#define GI_DIAG_SCHEMA_VER  4

/* ---------------------------------------------------------------- modes -- */

/*
 * Numerically identical to gen_inhibit_mode_t in gen_inhibit.h, which is the
 * public API the HTTP handler and config_server use. The shim static-asserts
 * that they agree rather than trusting the comment.
 */
typedef enum
{
    GI_OFF = 0,
    GI_OBSERVE,
    GI_RESPOND,
    GI_INHIBIT,
    /*
     * Spec 3.1 (review B1). The complete INHIBIT decision path -- arm gate,
     * section 6 releases, 6.3 suppression, section 7 trips, 7.1 key rules --
     * on the live bus, transmitting no 0x051 and counting a would-transmit
     * where INHIBIT would transmit. Diag still goes out, reporting the state
     * the device WOULD be in, so a log of a PASSIVE drive reads as a dry run
     * of an INHIBIT drive.
     *
     * Appended rather than inserted: the numbering is on the wire (diag_mode)
     * and in the HTTP setter, and renumbering OBSERVE/RESPOND/INHIBIT would
     * silently change what every recorded log means.
     */
    GI_PASSIVE,
} gi_mode_t;

/*
 * The two modes that run the decision path. Everything the gate and the trips
 * do is identical in both; the ONE difference is what happens at the moment a
 * frame would go out, and that lives in exactly one place (gi_on_frame()).
 */
static inline bool gi_mode_decides(gi_mode_t m)
{
    return m == GI_INHIBIT || m == GI_PASSIVE;
}

/* --------------------------------------------------------------- config -- */

/*
 * Everything tunable, in one struct passed at init.
 *
 * This is deliberately a struct rather than the #defines it replaces. The
 * session that owns the interposer tree reports that compile-time-only config
 * has already cost them two scenarios that simply cannot run against the
 * board, because the scenarios pass values the hardware build cannot take.
 * Having the values here means a host scenario can vary them, and means the
 * shim can later accept them over the wire without touching this file.
 */
typedef struct
{
    uint32_t soc_min_raw;       /* percent * 100; below this, latch disabled */
    uint32_t soc_debounce;      /* consecutive valid sub-threshold samples */
    int32_t  start_abort_rpm;   /* GENE rpm that counts as "engine turning" */
    int64_t  rpm_debounce_us;   /* how long it must hold before it trips */
    int64_t  fresh_us;          /* a signal older than this is not evidence */

    /*
     * 0x617's OWN FRESHNESS WINDOW: 1.0 s, where every other signal uses
     * fresh_us (0.5 s). Spec 7's "Freshness" paragraph, decided by the user
     * 2026-09-25.
     *
     * WHY ONE SIGNAL GETS ITS OWN. With a single window the SLOWEST signal
     * sets the whole device's tolerance of a brief bus dropout. 0x617 runs at
     * 4 Hz with a worst measured gap of 308 ms, so after a dropout it is
     * absent for the dropout plus up to 250 ms more -- meaning any dropout
     * over roughly 0.25-0.5 s, depending on nothing but 0x617's phase,
     * latched the inhibit off until the next key cycle AND reported it as
     * "0x617 stale" rather than as the link going down.
     *
     * At 1.0 s -- four periods, more than three times the worst measured gap
     * -- a whole-bus dropout of up to ~0.5 s rides through, bounded by
     * 0x051's own window, and anything longer is reported as trip 2 because
     * 0x051 now expires first. The cost is up to 0.5 s longer before a VCM
     * that has genuinely gone silent is noticed through 0x617; in that case
     * the truck stops anyway, and 0x051's window catches a silent VCM first.
     *
     * This does NOT replace the evidence rule. The rule still guards every
     * other signal whose window can expire before 0x051's -- 0x471, 0x054,
     * 0x639, 0x411, 0x440 -- and spec 7 says so explicitly.
     */
    int64_t  fault_fresh_us;   /* 0x617 only -- spec 7 "Freshness" */
    int64_t  err_window_us;     /* sliding window for the error-frame rate */
    uint32_t err_min_trip;      /* errors within that window that trip it */
    uint32_t diag_period_ms;    /* per-page diag cadence, round-robin over 3 */
    uint32_t max_rx_errors;     /* consecutive hard RX errors -> self-disarm */
    uint16_t fw_version;        /* reported in diag */
    uint32_t git_hash;          /* reported in diag; FNV-1a of GIT_SHA */
    bool     autoarm_configured;/* diag flags bit3 -- build auto-arms */
} gi_config_t;

/* The shipped values. Every field's justification is in gen_inhibit_core.c. */
void gi_config_defaults(gi_config_t *c);

/* ---------------------------------------------------------------- output -- */

typedef enum
{
    GI_TX_PROBE = 0,    /* 0x7F0 timing probe, honours due_us */
    GI_TX_INHIBIT,      /* 0x051 zero-torque command */
    GI_TX_DIAG,         /* 0x7F1-0x7F3 self-telemetry */
} gi_tx_kind_t;

typedef struct
{
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    uint8_t  kind;      /* gi_tx_kind_t */
    /*
     * Send at or after this time. Only GI_TX_PROBE sets it non-zero; the
     * busy-wait that honours it is the caller's, because spinning is a
     * platform behaviour and a host harness must not do it. `have_due` rather
     * than due_us != 0, per the sentinel rule above.
     */
    bool     have_due;
    int64_t  due_us;
    /*
     * The receive that caused this frame, so the caller can report the
     * RX-to-TX latency back without having to remember it. Zero for diag.
     */
    bool     have_t_rx;
    int64_t  t_rx;
} gi_frame_t;

/*
 * Bound: every entry point emits AT MOST ONE frame. gi_tick() emits a diag
 * page or nothing; gi_on_frame() emits a probe, an inhibit, or nothing. Four
 * is pure headroom.
 *
 * `dropped` non-zero means GI_EMIT_MAX was too small, which is A BUG IN THIS
 * FILE, not a runtime condition to handle. Stating that is what lets the core
 * have no error path: the bound is statically knowable, so overflow cannot
 * happen without someone having added an emit site and not read this comment.
 */
#define GI_EMIT_MAX 4
typedef struct
{
    gi_frame_t f[GI_EMIT_MAX];
    uint8_t    n;
    uint8_t    dropped;
} gi_emit_t;

/*
 * Events: diagnostics as DATA. The core cannot log -- it has no printf and no
 * allocation -- so it appends fixed records and the caller renders them.
 * Payload slots a/b/c are per-kind and documented at gi_event_name().
 */
/*
 * Which kind of skip (spec 5.2 item 5). Reported as GI_EV_SKIP's `a` argument, so a
 * log says what happened to the frame rather than only that something did.
 */
typedef enum
{
    GI_SKIP_WITHDRAWN = 0,  /* removed at the deadline; never on the wire */
    GI_SKIP_MAYBE_LATE,     /* still in the buffer at the deadline: the device
                             * cannot tell a late completion from a successful
                             * abort, because both report TX_SUCCESS */
    GI_SKIP_SEEN_LATE,      /* its completion was observed only after the VCM's
                             * next 0x051 had been dequeued */
} gi_skip_kind_t;

typedef enum
{
    GI_EV_NONE = 0,
    GI_EV_DISABLED,             /* a = gi_disable_t              (latched)   */
    GI_EV_SHUTDOWN_SUPPRESS,    /* a = 1 on / 0 off, b = 0x051 B0            */
    GI_EV_INHIBIT_LIVE,         /* interlocks passed; now transmitting       */
    GI_EV_ABORT,                /* a = gi_abort_t                            */
    GI_EV_TX_FAIL,              /* a = kind                                  */
    GI_EV_SKIP,                 /* a = gi_skip_kind_t. Spec 5.2 item 5: an
                                 * inhibit frame the scheduler skipped.
                                 * DELIBERATELY NOT GI_EV_TX_FAIL, which
                                 * trips at once and increments tx_fail --
                                 * a skip does neither, so sharing the name
                                 * gave two severities one label.         */
    GI_EV_RX_ERROR_DISARM,      /* a = consecutive rx errors                 */
    GI_EV_MODE,                 /* a = new mode, b = offset_us               */
    GI_EV_KEY,                  /* a = 1 on / 0 off (spec 7.1)               */
    GI_EV_KEY_CLEAR,            /* a = gi_abort_t, b = gi_disable_t cleared  */
    GI_EV_SOC_VALID,            /* a = 1 set / 0 cleared, b = bcm_mainc_stat  */
} gi_event_kind_t;

#define GI_EVENT_MAX 16
typedef struct
{
    int64_t t_us;
    uint8_t kind;               /* gi_event_kind_t */
    int32_t a, b, c;
} gi_event_t;

typedef struct
{
    gi_event_t e[GI_EVENT_MAX];
    uint8_t    n;
    uint8_t    dropped;
} gi_events_t;

/* ----------------------------------------------------------- reason codes -- */

/*
 * Why the inhibit is not live, and why a live run ended.
 *
 * These were `const char *` assigned at each site. They are enums now because
 * an enum is comparable in a test; the strings are recovered by the pure
 * lookups below.
 *
 * LOAD-BEARING: gi_block_name() and gi_abort_name() return the EXACT strings
 * that appear in the /gen_inhibit JSON as "arm_block" and "abort_reason".
 * Any bench harness or log analysis that matches on those strings is coupled
 * to this table, and renaming one breaks that matching with no compiler
 * error. Change a string only together with whatever reads it.
 */
typedef enum
{
    GI_BLOCK_NONE = 0,          /* "" -- the gate is satisfied */
    GI_BLOCK_NOT_ARMED,
    GI_BLOCK_GATE_NOT_EVALUATED,
    GI_BLOCK_NO_FRESH_CMD,
    GI_BLOCK_VCM_FAULT,
    GI_BLOCK_GEN_RUNNING,
    GI_BLOCK_VCM_REQ_ENGINE,
    GI_BLOCK_VCM_TORQUE,
    GI_BLOCK_SHUTDOWN_CMD,
    GI_BLOCK_NO_SOC_VALID,      /* no closed reading yet, or no 0x411 since */
    GI_BLOCK_CONTACTORS_OPEN,   /* marker set, but 0x440 reads open now */
    GI_BLOCK_NO_FRESH_FAULT,    /* 0x617 stale (spec 7 condition 3) */
    GI_BLOCK_NO_FRESH_SHIFT,    /* 0x639 stale (spec 7 condition 4) */
    GI_BLOCK_DISABLED,          /* a section 6 release has latched */
    GI_BLOCK_ABORTED,           /* a section 7 abort has latched (spec 7.1) */
} gi_block_t;

typedef enum
{
    GI_ABORT_NONE = 0,
    GI_ABORT_TX_FAILED,
    GI_ABORT_VCM_FAULT,
    GI_ABORT_BUS_LOST,
    GI_ABORT_INVERTER_LOST,
    GI_ABORT_ENGINE_TURNING,
    GI_ABORT_ERROR_RATE,
    /*
     * Spec 7 trip 5 (review B3, decided 2026-09-24 as option c): an interlock
     * signal going stale while the inhibit is LIVE is a latched abort, not a
     * non-latching gate on transmission.
     *
     * ONE REASON PER SIGNAL, deliberately. Spec 7 requires every trip to name
     * itself in the JSON and on the wire, and "a signal went stale" would send
     * a reader to the logs to find out which -- the BMS dropping off while the
     * VCM keeps talking (which the LV-connector disconnect test shows happens)
     * is a different event from the VCM going quiet, and the whole point of
     * option (c) over (b) is that these are worth stopping the drive for.
     */
    GI_ABORT_STALE_SOC,         /* 0x411 */
    GI_ABORT_STALE_CONTACTOR,   /* 0x440 */
    GI_ABORT_STALE_FAULT,       /* 0x617 */
    GI_ABORT_STALE_SHIFT,       /* 0x639 */
    GI_ABORT_STALE_RPM,         /* 0x054, once heard this arm cycle */
    GI_ABORT_SHORT_VCM_FRAME,   /* spec 7 trip 8: 0x051 with DLC < 6 */
    /*
     * Spec 7 trip 7 (review C1). "Failed transmit" has three forms, worth
     * telling apart in a log because they point at different things:
     *   NOT_QUEUED -- twai_transmit() refused it. Our software.
     *   TX_FAILED  -- the controller reported the attempt failed (arbitration
     *                 lost repeatedly, no ACK, bus-off). The wire.
     *   TX_LATE    -- RETIRED 2026-09-27 as an immediate trip. It is kept in
     *                 the enum so an older log or golden naming it still
     *                 decodes, and nothing sets it any more. What it used to
     *                 catch -- still outstanding when the VCM's next 0x051
     *                 arrived -- is now a SKIP, detected by the transmit
     *                 scheduler (spec 5.2 item 5) and tripped only when three
     *                 fall within one second.
     *   SKIPS      -- three skipped inhibit frames within 1 s (spec 7 trip 7 as
     *                 amended). The single skip is not a trip because the
     *                 inverter has already acted on the VCM's command carrying
     *                 that counter and ignores ours, so one skip costs one
     *                 slot; a stuck transmit path costs every slot, and this is
     *                 what tells them apart.
     */
    GI_ABORT_TX_NOT_QUEUED,
    GI_ABORT_TX_LATE,
    GI_ABORT_SKIPS,
} gi_abort_t;

/*
 * Why the device put ITSELF in OFF (spec 7's receive-error self-disarm, spec
 * 8's forced quiesce), reported in the JSON per spec 11.
 *
 * WHY THIS EXISTS AT ALL. Both paths set mode OFF and, in OFF, the worker no
 * longer receives -- so no key-on can clear it, no diag goes out, and the
 * device is indistinguishable from one somebody disarmed on purpose. Recovery
 * is a re-arm or a reboot, and a human deciding which needs to know which of
 * the three it is looking at. Cleared when the device is next armed, so it
 * describes the CURRENT OFF and not a previous one.
 */
typedef enum
{
    GI_SELF_OFF_NONE = 0,       /* not off, or off because someone asked */
    GI_SELF_OFF_RX_ERRORS,      /* spec 7: the driver went out from under us */
    GI_SELF_OFF_QUIESCE,        /* spec 8: another task is tearing the bus down */
} gi_self_off_t;

const char *gi_self_off_name(gi_self_off_t r);

typedef enum
{
    GI_DISABLE_NONE = 0,
    GI_DISABLE_M_MODE = 1,      /* wire values: diag byte 4, schema v2 */
    GI_DISABLE_LOW_SOC = 2,
} gi_disable_t;

/* Pure lookups. No formatting, no state. */
const char *gi_block_name(gi_block_t b);
const char *gi_abort_name(gi_abort_t a);
const char *gi_disable_name(gi_disable_t d);
const char *gi_event_name(gi_event_kind_t k);

/* ------------------------------------------------------------ histograms -- */

#define GI_NBUCKETS 10
typedef struct
{
    uint32_t count;
    uint32_t min_us;
    uint32_t max_us;
    uint64_t sum_us;
    uint32_t buckets[GI_NBUCKETS];
} gi_hist_t;

/* Upper edges, microseconds; the last is the catch-all. */
extern const uint32_t gi_bucket_us[GI_NBUCKETS];

/* ----------------------------------------------------------------- state -- */

/*
 * Everything the decision logic remembers. One struct, no statics, so a host
 * harness can run several independent instances -- and so that "reset" is a
 * visible operation on a visible object rather than a scatter of assignments.
 */
typedef struct
{
    gi_config_t cfg;

    gi_mode_t mode;
    uint32_t  offset_us;

    /*
     * --- latched disable (section 6) ---
     * Survives arm cycles and does not survive a reboot. Since spec 7.1 it is
     * also cleared by a key-off -> key-on transition (key_monitor()), which is
     * the only thing besides a power cycle that clears it -- SoC recovering or
     * M mode being deselected does not.
     */
    bool         disabled;
    gi_disable_t disable_code;
    uint32_t     soc_raw;           /* last valid SoC, percent * 100 */
    uint32_t     soc_low_count;
    uint8_t      last_shift_pos;    /* 0xFF = never seen */

    /*
     * --- the SoC-valid marker (spec 6.2, review A1) ---
     *
     * WHY IT EXISTS. 0x411 is wrong for about a second every time the BMS
     * wakes: 113 of 114 corpus wakes open on a false value, 112 of them LOW by
     * a median 13.8 %, correcting 0.32-1.15 s later. A release that latches
     * for the rest of the drive cannot be allowed to run on that. The spec
     * used to call 0x411 "valid during startup"; that is withdrawn.
     *
     * WHY THE CONTACTORS ARE THE GATE and not a timer: in all 103 scanned
     * wakes where the contactors closed, SoC had already corrected BEFORE the
     * first closed state -- 0 after, smallest margin 0.07 s -- and the
     * correction lands at ~1.03 s even where they never close, so the two are
     * independent. Gating on the close makes a wake reading unusable by
     * construction rather than by a timing window, and costs nothing: the
     * engine is cranked by the HV generator inverter, so no start is possible
     * before the contactors close and there is nothing to inhibit until then.
     *
     * soc_valid       -- set by a closed bcm_mainc_stat, cleared when 0x411 or
     *                    0x440 stops being fresh (the BMS went away, so its
     *                    next reading is a wake reading), on every key-on
     *                    latch clear, and at init.
     * soc_since_valid -- an 0x411 has arrived since it was set. The arm gate
     *                    needs this as well, so the inhibit never goes live on
     *                    a close with no reading behind it.
     */
    bool         soc_valid;
    bool         soc_since_valid;

    /* --- non-latching shutdown suppression (section 6.3) --- */
    bool shutdown_suppressed;

    /* --- interlocks (section 7) --- */
    bool       inhibit_live;
    gi_block_t arm_block;
    gi_abort_t abort_reason;
    /*
     * Spec 7.1. A section 7 abort used to set mode = GI_OFF, and that made
     * rule 2 unimplementable: the worker's OFF branch does not receive, so a
     * device that had aborted could never see the key come back. The abort is
     * a LATCHED STAND-DOWN now, the same shape as the section 6 `disabled`
     * latch beside it -- the mode is left alone, the worker keeps receiving,
     * and nothing transmits because inhibit_live is false and the gate will
     * not run while this is set. What is latched has not weakened; what has
     * changed is that the device stays awake to hear the key.
     */
    bool       abort_latched;

    /* --- key state (section 7.1) --- */
    bool    have_key;   int64_t seen_key;
    bool    key_on;                 /* last reading of 0x592 B0 bit 4 */
    /*
     * The edge detector, and it deliberately runs on READINGS, not on
     * freshness. Staleness is not a key-off: if the bus drops while the key
     * is on and returns with it still on, no transition happened and the
     * section 7 latch must survive -- which is the whole point of the
     * bus-loss trip latching in the first place. Only an actual 0 followed
     * by an actual 1 clears anything.
     */
    bool    key_seen_off;

    /*
     * Freshness. Each stamp carries its own have_* flag; see the sentinel
     * note at the top of this file for why the timestamp alone will not do.
     */
    bool    have_cmd;   int64_t seen_cmd;
    bool    have_fb;    int64_t seen_fb;
    bool    fb_ever;    /* 0x471 heard at least once THIS arm cycle */
    bool    have_rpm;   int64_t seen_rpm;
    /*
     * Spec 7 trip 5. 0x054 gets the same once-heard qualification as 0x471:
     * the GENE family is legitimately absent until the inverter wakes (~28 s
     * after key-on, or never on a drive that does not use the generator), so
     * a never-seen-is-stale rule would reintroduce bug 2 of 2026-09-19 through
     * a second door. Per arm cycle, like fb_ever.
     */
    bool    rpm_ever;
    bool    have_soc;   int64_t seen_soc;
    bool    have_cont;  int64_t seen_cont;
    /*
     * 0x617 and 0x639 were read as last-known values until 2026-09-24, which
     * is what review B2/B3 objected to: a silent bus otherwise answers every
     * question you ask it, and a never-seen 0x617 read as "no fault".
     * 0x617 is the tightest of all of them against the 0.5 s window -- 4 Hz,
     * worst measured gap 308 ms.
     */
    bool    have_fault; int64_t seen_fault;
    bool    have_shift; int64_t seen_shift;

    int32_t vcm_torque;             /* 0x051 B1-B2, centred */
    int32_t vcm_rpm_ref;            /* 0x051 B3-B4, centred; -1 = engine off */
    int32_t gene_rpm;               /* 0x054 B0-B1, centred per the DBC */
    uint8_t vcm_fault;              /* 0x617 B7; 0xC8 = no fault */
    uint8_t mainc_stat;             /* 0x440 bcm_mainc_stat; 0xFF = never seen */

    bool    rpm_over;   int64_t rpm_over_since;
    bool    have_err_window; int64_t err_window; uint32_t err_base;

    /*
     * Spec 7 trip 7 (review C1). One inhibit frame may be outstanding at a
     * time -- the controller has a single TX buffer -- so a flag and the
     * frame's own timestamps are enough. tx_ok is incremented on COMPLETION,
     * not on queueing: "accepted into the driver's queue" is what the previous
     * implementation counted as a transmit.
     */
    bool     tx_pending;
    int64_t  tx_pending_since;      /* when it was queued */
    /*
     * Spec 5 (review A4): how many inhibit frames were queued while something
     * of ours was still in the controller's single TX buffer. The diag pages
     * are the lowest-priority IDs on the bus, so one waiting for an idle gap
     * on a loaded bus delays the inhibit behind it. This is that hazard
     * happening, counted rather than assumed, and it is the number the
     * full-replay bench run exists to put a value on.
     */
    uint32_t tx_queued_behind;

    /*
     * Spec 3.1: what PASSIVE counts where INHIBIT would transmit. Deliberately
     * NOT tx_ok -- a reader of a log has to be able to tell a would-transmit
     * from a frame that really went on the wire, and conflating them would make
     * a PASSIVE drive indistinguishable from an INHIBIT drive in exactly the
     * dimension the mode exists to report on.
     */
    uint32_t would_tx;

    /*
     * A tripwire, not a statistic. emit() increments it when a code path tries
     * to transmit a real 0x051 from a mode that must not, which cannot happen
     * and therefore has to be visible if it ever does. Reported in the JSON;
     * any non-zero reading is a bug in gen_inhibit_core.c, not a bus event.
     */
    uint32_t emit_refused;

    /* Spec 11: why the device is in OFF, when it was not asked to be. */
    gi_self_off_t self_off;
    int64_t  tx_pending_t_rx;       /* the 0x051 it answers */
    bool     tx_pending_have_rx;

    /* --- statistics --- */
    uint32_t tx_ok, tx_fail, other_frames;
    uint32_t ctr_steps_ok, ctr_steps_bad;
    bool     have_last_ctr; uint8_t last_ctr;
    uint32_t rx_errors;
    gi_hist_t rx_gap;               /* 0x051 inter-arrival */
    gi_hist_t response;             /* RX of 0x051 -> our frame queued */
    bool     have_prev_051; int64_t prev_051;

    /* --- diag scheduling --- */
    bool    have_last_diag; int64_t last_diag;

    /*
     * SPEC 5.1 ITEM 4: diag is scheduled around the inhibit.
     *
     * `diag_after_tx` opens when an inhibit frame is observed COMPLETE, and
     * closes as soon as a page uses it -- so at most one page per completion,
     * queued at the moment the VCM's next 0x051 is furthest away (>= 4.69 ms).
     * That gives the lowest-priority frame on the bus several milliseconds to
     * find an idle slot at ~56 % occupancy, instead of landing wherever the
     * wall clock happened to put it.
     *
     * `last_tx_done` is when that last happened, and `have_tx_done` says
     * whether it has happened AT ALL IN THIS TRANSMITTING EPISODE. Both are
     * cleared the moment the device stops being able to transmit, so a device
     * that has just gone live has no completion on record and takes the normal
     * cadence until its first one -- a device that has stopped transmitting must
     * still report, or the diagnostics go quiet exactly when something has gone
     * wrong.
     *
     * The clearing is the whole of that guarantee as of 2026-09-26. It used to
     * be a 1 s bound on `last_tx_done` instead, which is withdrawn: see the long
     * note at the clearing site in gen_inhibit_core.c, and spec 5.1 item 4.
     */
    bool    diag_after_tx;
    bool    have_tx_done; int64_t last_tx_done;
    uint8_t diag_page;
} gi_state_t;

/* ------------------------------------------------------------------- API -- */

/*
 * Platform facts the core needs but must not go and read for itself. Supplied
 * fresh on each tick.
 */
typedef struct
{
    bool     can_enabled;       /* driver installed and on-bus */
    bool     bus_ours;          /* we were the ones who enabled it */
    bool     err_valid;         /* bus_error_count below is meaningful */
    uint32_t bus_error_count;   /* controller's own counter, free-running */
} gi_bus_t;

/* Full initialisation. `cfg` is copied; pass NULL for gi_config_defaults(). */
void gi_init(gi_state_t *st, const gi_config_t *cfg);

/*
 * Arm-cycle reset: statistics, and the section 7 gate.
 *
 * Deliberately does NOT clear signal freshness -- that is a property of the
 * bus, not of this run, and dropping it would make every arm wait a fresh
 * round before the interlocks could pass. Deliberately does NOT clear the
 * section 6 latched disable: that one is cleared by a reboot or by a key-on
 * (spec 7.1), never by re-arming. It DOES clear the section 7 abort latch,
 * which is what arming out of OFF used to do when an abort was expressed as
 * mode OFF.
 */
void gi_reset_stats(gi_state_t *st);

/* Mode change, decision side only. The shim does the driver work around it. */
void gi_set_mode(gi_state_t *st, gi_mode_t mode, uint32_t offset_us,
                 int64_t now, gi_events_t *ev);

/*
 * Entering the OFF state from the worker loop. Separate from gi_set_mode()
 * because the worker also reaches OFF without a mode request -- the RX-error
 * ceiling of gi_on_bus_error() forces it from inside the core. A section 7
 * abort no longer comes through here at all: since spec 7.1 it is a latched
 * stand-down that leaves the mode alone (see abort_latched).
 */
void gi_notify_off(gi_state_t *st);

/*
 * The periodic half: diag heartbeat and the section 7 interlocks. Call every
 * loop BEFORE the blocking receive, so both still run on a silent bus -- a
 * trip that only ran on frame arrival could not detect the absence of frames.
 */
void gi_tick(gi_state_t *st, int64_t now, const gi_bus_t *bus,
             gi_emit_t *out, gi_events_t *ev);

/* One received frame. */
void gi_on_frame(gi_state_t *st, uint32_t id, uint8_t dlc, const uint8_t *data,
                 int64_t now, gi_emit_t *out, gi_events_t *ev);

/*
 * The QUEUE attempt for a frame the core emitted: `queued` is whether
 * twai_transmit() accepted it, and `t_tx` is when. Feeding this back is what
 * keeps the response histogram and the failed-transmit trip in the core
 * rather than in the driver shim.
 *
 * ACCEPTING A FRAME IS NOT SENDING IT (spec 7 trip 7, review C1). The
 * controller has one TX buffer and the driver queues FIFO, so an inhibit
 * frame can sit behind a diag frame waiting for an idle bus. The verdict
 * comes separately, from gi_on_tx_done().
 */
void gi_on_tx_result(gi_state_t *st, const gi_frame_t *f, bool queued,
                     bool behind, int64_t t_tx, gi_events_t *ev);

/*
 * The CONTROLLER'S VERDICT on the inhibit frame it was given. This is what
 * makes tx_ok mean "on the wire" rather than "handed to the driver".
 *
 * The caller decides HOW it knows. The shim does not use the TX_SUCCESS alert
 * for this, because that alert is a latched bit shared by every frame and
 * cannot say which frame it belongs to -- see poll_tx_completion() in
 * gen_inhibit.c.
 *
 * A caller with no way to know -- a host harness with no driver -- calls it
 * with ok=true immediately, which models a controller that always completes
 * and should be documented as the model it is.
 */
void gi_on_tx_done(gi_state_t *st, bool ok, int64_t t_done, gi_events_t *ev);

/*
 * The transmit scheduler skipped an inhibit frame (spec 5.2 item 5, trip 7 as
 * amended). `trip` is the scheduler's "3 within 1 s" verdict: it owns the sliding
 * window, the core owns the abort. A single skip is counted and reported and does
 * NOT end the inhibit.
 */
/*
 * The name of a skip kind. In the core because the core owns the enum, like
 * gi_abort_name() and gi_disable_name() -- and because a renderer that has to
 * spell the kinds itself is a second table that can disagree with this one.
 */
const char *gi_skip_name(gi_skip_kind_t k);

void gi_on_inhibit_skip(gi_state_t *st, gi_skip_kind_t kind, bool trip,
                        int64_t now, gi_events_t *ev);

/* A hard (non-timeout) receive error. Returns true if it self-disarmed. */
bool gi_on_rx_error(gi_state_t *st, int64_t now, gi_events_t *ev);

/* Reset the consecutive-error counter after a good receive. */
void gi_on_rx_ok(gi_state_t *st);

/* ------------------------------------------------------- pure extractors -- */

/*
 * Hand-rolled bit extraction, matching the DBC layouts.
 *
 * AGENTS.md's standing rule is to decode with cantools and never hand-roll.
 * The exemption applies only because these are exposed here for a host test
 * to cross-check against cantools over randomised frames -- the interposer
 * tree's test_signals.py does exactly that for its extractors. Purity makes
 * them testable; it does NOT make them right, and a transcription error here
 * would be invisible to every other test in the suite. We already have one
 * live instance of that failure mode in this project (the reference Python
 * decodes GENE_RotSpd against 32768 where the DBC says 32767), which is the
 * reason the cross-check is not optional.
 */
int32_t gi_le16c(const uint8_t *d, int32_t zero);   /* LE 16-bit, centred */
uint32_t gi_soc_raw(const uint8_t *d);              /* 0x411, 14-bit BE @ bit7 */
uint8_t  gi_mainc_stat(const uint8_t *d);           /* 0x440 B0 bits 5:2 */
uint8_t  gi_shift_pos(const uint8_t *d);            /* 0x639 B6 bits 6:4 */
uint8_t  gi_ctr(const uint8_t *d);                  /* 0x051 B5 low nibble */
bool     gi_key_bit(const uint8_t *d);              /* 0x592 B0 bit 4 */

/* Freshness test, exposed so a test can drive it directly. */
bool gi_fresh(const gi_state_t *st, bool have, int64_t stamp, int64_t now);

/*
 * Freshness against an EXPLICIT window, for the signals that do not use
 * fresh_us. Today that is 0x617 alone (cfg.fault_fresh_us).
 *
 * The window is passed rather than looked up from the signal, so that every
 * call site states which window it means. A lookup table would put that choice
 * somewhere other than where the decision is made, and a signal silently
 * falling through to the default is exactly the failure this split exists to
 * fix.
 */
bool gi_fresh_w(const gi_state_t *st, bool have, int64_t stamp, int64_t now,
                int64_t window_us);

/*
 * Spec 7.1 rule 1: the key reads ON right now -- value AND freshness, never a
 * last-known value. This gates TRANSMISSION only. Arming is deliberately not
 * gated: the device arms during a key-off and sits armed but silent, because
 * every arm-gate condition is satisfied there (0x051 outlives the key by a
 * median 77 s, the generator is stopped, gen_rpm_ref reads its engine-off
 * null and torque is zero) and because being already armed before the VCU
 * asserts is the point.
 */
bool gi_key_on(const gi_state_t *st, int64_t now);

/* Histogram helpers, pure. */
void gi_hist_reset(gi_hist_t *h);
void gi_hist_add(gi_hist_t *h, uint32_t us);

#ifdef __cplusplus
}
#endif

#endif /* GEN_INHIBIT_CORE_H */
