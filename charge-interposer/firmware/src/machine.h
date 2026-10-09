// The interposer state machine. PURE -- no I/O, no floats, no allocation.
//
// This is a direct port of machine.py from the parent folder. That file is the
// reference implementation and stays the source of truth for behaviour; this
// one is the source of truth for what runs on the board. They are kept in step
// by test/host_diff, which drives both over the same captures and diffs every
// emitted frame and state transition. Both implement
// notes/charge-interposer-spec.md; section numbers below refer to it.
//
// The same rules that govern machine.py govern this file:
//
//   * no dependency on Arduino, ESP-IDF, or any platform header
//   * integer arithmetic only, in fixed units (mV, centi-amps, ms, half-percent)
//   * no allocation -- callers supply the output buffer
//   * no wall-clock reads -- time arrives as a parameter
//
// Anything that needs a peripheral belongs in a CanPort implementation, not
// here. Keep it that way: it is the only reason the logic can be tested at all
// without hardware.
//
// Board topology
// --------------
//     A123 BMS ---+                                    +--- Bel charger
//     VCU/HCU ----+--[ vehicle port ]== CORE ==[ charger port ]
//
// Divergence note vs machine.py: Python uses floor division (//), C++ truncates
// toward zero. Every division in the ported logic has a non-negative numerator
// (checked at port time), so the two agree. Do not introduce a division here
// without confirming that still holds.

#pragma once

#include <stdint.h>
#include <stddef.h>

namespace interposer {

// --------------------------------------------------------------------------
// identifiers
// --------------------------------------------------------------------------

static const uint32_t CMD_ID = 0x18EFC000u;  // VCU -> charger command

static const uint32_t BMS_STATUS = 0x410u;  // soc, ibat, epo, alarm, hvil
static const uint32_t BMS_LIMITS = 0x420u;  // chg_max, cell over/under-volt
static const uint32_t BMS_DATA1 = 0x430u;   // cell vmax / vmin / tmax / tmin
static const uint32_t BMS_DATA2 = 0x440u;   // mainc_stat (4-bit), balancing

// The BMS messages the core reads, in the order tick() tests them for
// staleness (spec 6, B-6). machine.py's BMS_IDS walks the same order, so a
// tick where several are stale at once reports the same one in both cores.
enum BmsIdx {
  BMS_IDX_STATUS = 0,   // 0x410
  BMS_IDX_LIMITS,       // 0x420
  BMS_IDX_DATA1,        // 0x430
  BMS_IDX_DATA2,        // 0x440
  N_BMS_IDS
};
static const uint32_t VCM_EVAP = 0x649u;    // vcm_evap_active (spec 3)

static const uint32_t CHG_STATUS = 0x18FFD4C0u;     // BINV_status, multiplexed
static const uint32_t CHG_HV_STATUS = 0x18FFD7C0u;  // pack V/I, AC V/I
static const uint32_t CHG_INFO = 0x18FFD9C0u;       // maxAvailableChargingCurrent
static const uint32_t CHG_PILOT = 0x18FFD8C0u;      // chargePilotOnlineTime (spec 3)

enum PortId : uint8_t {
  TO_VEHICLE = 0,
  TO_CHARGER = 1,
};

// Diagnostics (spec 8; layout in notes/artifacts/interposer-firmware/
// interposer_diag_schema.md, decode DBC vtrux-interposer-diag.dbc). All of it
// goes to the VEHICLE side only.
static const uint8_t DIAG_SA = 0xE1;            // mirror-frame source address; unused on this truck
static const uint32_t DIAG_STATUS_ID = 0x7F4;
static const uint32_t DIAG_OBSERVED_ID = 0x7F5;
static const uint32_t DIAG_COUNTERS_ID = 0x7F6;
static const uint32_t DIAG_BUILD_ID = 0x7F7;
// On-wire schema (spec 8.2). The decoder's guard: a DBC built for another
// version flags the mismatch rather than trusting the fields.
//   2  0x7F5 B7 = the VCU's mode in the low nibble, the evap flag in bit 7
//   3  0x7F5 B7 bit 6 = "charger silent while expected" (spec 2.2), and
//      0x7F6 B7 counts transmit failures toward the charger ONLY while the
//      VCU's flow bit is 1. The byte is in the same place and means a
//      different quantity, which is exactly what this guard is for.
static const uint8_t DIAG_SCHEMA_VER = 3;
// Firmware version (spec 8.2, B-2). The two cores must agree; check_port.py
// fails if they do not.
//   2  the section A behaviour, Python's value until 2026-10-03
//   3  0x7F7 B0-B3 changed meaning from fnv1a(GIT_SHA) -- which was always
//      fnv1a("unknown"), because -DGIT_SHA was never passed -- to the first
//      four bytes of the application ELF's SHA-256. The wire layout is
//      unchanged, so this byte is the only thing that tells the two apart.
//      C++ carried 3 while Python still read 2; that mismatch was B-2.
//   4  sections A and B: the per-message BMS staleness, no cell_undervolt
//      trip, 20 repeat frames from every path, the flow-gated charger
//      transmit-failure counter and the charger-silence flag.
static const uint8_t DIAG_FW_VER = 4;

// The 29-bit id with its source-address byte replaced by DIAG_SA.
inline uint32_t mirrorId(uint32_t id) { return (id & 0x1FFFFF00u) | DIAG_SA; }

// 3 is reserved: it was RELEASING, the synthesised charger stop removed on
// 2026-09-19 (spec 5.2); the on-wire codes of the others are kept.
enum State : uint8_t {
  S_PASSTHROUGH = 0,  // not charging (or not yet armed)
  S_MONITOR = 1,      // charging, watching for the VCU's Low Power command
  S_OVERRIDE = 2,     // VCU holds at the evap ceiling; we are driving the charge
  S_TERMINATED = 4,   // the VCU's hold reaches the charger; latched for the session
  S_SAFE = 5,         // a trip fired; transparent forwarding, latched
};

const char* stateName(uint8_t s);

// page 00 cmd_Mode. CONFIRMED by two independent methods -- the EPRI app
// binary's literal pool and log correlation on this vehicle. Mode 3 is a LOW
// POWER mode, NOT a completion state: the charger caps itself at ~6 A in it
// and then follows the VCU's setpoint down to a ~2.4 A hold that lasts until
// the handle is pulled; 3 -> 1 restarts it at full current (spec 4, 7).
enum Mode : uint8_t {
  MODE_EXPORT = 0,
  MODE_CHARGER = 1,
  MODE_STANDBY = 2,
  MODE_LOW_POWER = 3,
  MODE_INVALID = 4,
};

static const uint8_t CHG_STATE_CHARGING = 12;

// bcm_mainc_stat (0x440, 4-bit) values meaning the main contactors are closed
// and power is flowing. MEASURED, not assumed: across the whole of
// vtrux_charge_M1 and vtruxchargeafterexportchargetest-80pctcv the steady value
// while charging is 12, reached through the precharge ladder 0 -> 2 -> 5 -> 7
// -> 8 -> 9 and leaving via 14 at shutdown. projects/vtrux/README.md documents
// 11 as "normal running", which is the DRIVING steady state; charging sits at
// 12. Both are accepted so the same check works either way.
inline bool maincClosed(uint8_t ms) { return ms == 11u || ms == 12u; }

// --------------------------------------------------------------------------
// config -- thresholds. Units: mV, centi-amps (cA = 0.01 A), ms, half-percent.
// --------------------------------------------------------------------------

struct Config {
  // --- where WE release, when we are the one driving the charge -----------
  // Spec 5.1: the BMS's permission collapsing. No vmax trigger.
  int32_t chgmax_full_ca;

  // --- the override gate (spec 3) -----------------------------------------
  // A Low Power command is the evap ceiling only with the VCM's evap flag set
  // AND bcm_soc at or above this (half-percent counts). Nothing about cell
  // voltage or the BMS's permission takes part.
  int32_t soc_arm_half;

  // --- hard safety ---------------------------------------------------------
  int32_t vmax_hard_mv;  // no debounce, trips immediately

  // A page-01 setpoint counts as the VCU's CC command (spec 4) only from
  // here up: real CC commands are 9.7-19.8 A, hold setpoints 0-2.9 A.
  int32_t learned_ilim_min_ca;

  // Spec 4.1: the repeat of the VCU's standing page-00 command, in the
  // VCU's own burst shape (~20 frames at 50 ms).
  uint16_t burst_frames;
  uint32_t burst_period_ms;

  // The VCU's startup transient: Low Power 2-3 s after the first CHARGER
  // command, back to CHARGER 63-72 s later, in every session that had one.
  // Never override before the charge has been established this long; a hold
  // still there afterwards is decided then (spec 3).
  uint32_t arm_delay_ms;

  uint32_t full_debounce_ms;

  // The charger dips out of state 12 for a single frame during normal
  // operation -- measured on vtruxchargeafterexportchargetest-80pctcv, where
  // BELINV_state shows 12 -> 15 -> 12 blips of 0.00 s duration, one of them at
  // the 80 % cutover itself, inside two runs of state 12 lasting 7336 s and
  // 1868 s. A real fault latches in 15; these do not.
  uint32_t chg_state_debounce_ms;

  uint32_t bms_stale_ms;
  uint32_t chg_stale_ms;
  // Spec 6.1: no charger status frame for this long is a session boundary.
  // It resets everything EXCEPT SAFE -- the 500 ms staleness row has already
  // tripped on the same silence, and silence continuing must not undo the
  // latch that silence set.
  uint32_t chg_silence_ms;
  uint32_t override_max_ms;  // spec 6: 6 hours

  // Spec 4: hold pages 03.02 / 03.07 at their charging-time values while
  // overriding, so the charger sees a self-consistent picture.
  //
  // Decided, not assumed (review A6, 2026-09-30). 03.00 is forwarded
  // untouched, and the truck itself has run mode CHARGER at full current
  // with 03.00 = 1 twice, so holding 03.02 / 03.07 alongside it hands the
  // charger a picture it has already seen in real charges. The one accepted
  // difference is timing: at a real top of charge those pages flip to their
  // "complete" values minutes to hours after the Low Power, while at our
  // release they arrive with it. The trigger is not identified, so the delay
  // is not reproduced. See machine.py's Config for the captures.
  bool revert_aux_pages;

  // STEALTH: additionally rewrite charger->vehicle current/state so the VCU
  // believes the charge stopped. Off by default -- only enable if the bench
  // shows the VCU misbehaves when it sees current after its cutoff.
  bool mask_charger_telemetry;
  // Spec 8.1: rebroadcast every frame we modify on the vehicle side under
  // DIAG_SA, so a log shows what the charger got.
  bool diag_mirror;
};

// Fills cfg with the defaults from machine.py's Config.__init__.
void configDefaults(Config& cfg);

// --------------------------------------------------------------------------
// frames
// --------------------------------------------------------------------------

struct Emit {
  uint32_t id;
  uint8_t data[8];
  uint8_t len;
  uint8_t port;  // PortId
  bool ext;
};

// tick() can emit a whole repeat burst in one call when it has fallen behind:
// cfg.burst_frames frames plus a mirror copy each (spec 4.1, 8.1) -- 2 x 20
// with the defaults. 64 leaves headroom.
static const uint8_t EMIT_MAX = 64;

struct EmitList {
  Emit items[EMIT_MAX];
  uint8_t n;
  uint8_t dropped;  // non-zero means EMIT_MAX was too small -- a bug, not a
                    // condition to handle at runtime

  void clear() {
    n = 0;
    dropped = 0;
  }
  void push(uint8_t port, uint32_t id, bool ext, const uint8_t* d, uint8_t len);
};

// --------------------------------------------------------------------------
// events -- diagnostics only. Codes rather than formatted strings so the core
// stays allocation-free and printf-free; the host harness renders them.
// eventName() strings carry the substrings run_scenario.py matches on
// ("LOW_POWER overridden", "top of charge reached", "RELEASE", "TRIP", ...).
// --------------------------------------------------------------------------

enum EventCode : uint8_t {
  EV_NONE = 0,
  EV_ARMED,                     // a=ibat_ca
  EV_VCU_CHARGE_AGAIN,
  EV_OVERRIDE_ENDED_BY_VCU,     // CHARGER during an override (spec 6)
  EV_SESSION_OVER,              // a=mode
  EV_WAIT_NO_BMS,
  EV_ACCEPT_EVAP_CLEAR,         // a=soc_half, b=vmax, c=chg_max
  EV_ACCEPT_SOC_BELOW_GATE,     // a=soc_half
  EV_WAIT_ARM_DELAY,            // a=soc_half, b=ms into the charge
  EV_WAIT_NO_EVSE_LIMIT,        // a=soc_half
  EV_OVERRIDE,                  // a=vmax, b=chg_max, c=soc_half
  EV_TOP_OF_CHARGE,             // a=vmax, b=chg_max, c=vmin
  EV_RELEASE,
  EV_TRIP,                      // a=TripReason
  EV_REPEAT,                    // a=flow, b=mode, c=frames (spec 4.1)
  EV_FLOW_DROP,                 // flow bit dropped: the override ends (spec 6)
  EV_BOOT_LOCKED,               // booted into a running session (spec 3, A3)
  EV_PILOT_BACK,                // pilot timer stepped back (spec 6.1)
  EV_CHG_SILENCE,               // 20 s with no charger status frame (spec 6.1)
  EV_HANDLE_PULL,               // charger reports the plug out (spec 6)
  // Spec 4.1, added 2026-10-05. a=frames SENT, b=frames announced,
  // c=RepeatEnd reason. EV_REPEAT announces an intention before any
  // frame goes out; this reports what actually went and why it
  // stopped. Appended at the end of the enum on purpose: the codes
  // are not on the wire, but renumbering them would still invalidate
  // every serial log anyone has already captured.
  EV_REPEAT_END,
};

// Why a spec 4.1 repeat stopped, reported as EV_REPEAT_END's `c`.
enum RepeatEnd : uint8_t {
  REPEAT_COMPLETE = 0,          // all announced frames went out
  REPEAT_SUPERSEDED,            // spec 4.1: the VCU's own page 00 ended it
  REPEAT_RESET,                 // the session was torn down mid-burst
  // Appended, never inserted: these codes appear in captured logs, so
  // renumbering the existing ones would silently re-read every saved
  // trace. Spec 4.1, added 2026-10-05: a repeat that starts while
  // another is still being sent replaces it.
  REPEAT_REPLACED,              // a newer repeat took over mid-burst
};

// Schema 2: the cell-temperature codes were removed (spec 6). Order matches
// machine.py's TRIP_KEYS so both report the same code on the wire.
enum TripReason : uint8_t {
  TRIP_NONE = 0,
  TRIP_VMAX_HARD,
  TRIP_CELL_OVERVOLT,
  // Retired 2026-10-03 (B-7a): the cell_undervolt trip is gone, but the slot
  // stays so every other trip code keeps the value it has on the wire today.
  // Never emitted; a decoder meeting it is reading a pre-B-7a log.
  TRIP_CELL_UNDERVOLT_RETIRED,
  TRIP_BMS_EPO,
  TRIP_BMS_ALARM_TYPE3,
  TRIP_HVIL_OPEN,
  TRIP_CONTACTOR,
  TRIP_CHARGER_FAULT,
  TRIP_CHG_STATE,
  TRIP_BMS_STALE,
  TRIP_CHG_STALE,
  TRIP_OVERRIDE_CAP,
};

const char* tripName(uint8_t r);
const char* eventName(uint8_t c);

struct Event {
  uint32_t t_ms;
  int32_t a;
  int32_t b;
  int32_t c;
  uint8_t state;
  uint8_t code;
};

// --------------------------------------------------------------------------
// signal extraction -- hand-rolled to match the DBC bit layouts exactly.
// Cross-checked against cantools by the Python side's test_signals.py; that
// test is the reason it is safe to hand-roll rather than decode.
// --------------------------------------------------------------------------

inline int32_t vmaxMv(const uint8_t* d) {  // 0x430 bcm_cell_vmax 4|13@0+
  return (int32_t)(((uint32_t)(d[0] & 0x1Fu) << 8) | d[1]);
}
inline int32_t vminMv(const uint8_t* d) {  // 0x430 bcm_cell_vmin 20|13@0+
  return (int32_t)(((uint32_t)(d[2] & 0x1Fu) << 8) | d[3]);
}
inline int32_t tmaxDdegC(const uint8_t* d) {  // 0x430 32|8@1+ 0.5 C, off -40
  return (int32_t)d[4] * 5 - 400;
}
inline int32_t tminDdegC(const uint8_t* d) {  // 0x430 40|8@1+ 0.5 C, off -40
  return (int32_t)d[5] * 5 - 400;
}
inline int32_t chgMaxCa(const uint8_t* d) {  // 0x420 11|12@0+ 0.25 A -> cA
  return (int32_t)((((uint32_t)(d[1] & 0x0Fu) << 8) | d[2]) * 25u);
}
inline uint8_t cellOvervolt(const uint8_t* d) { return (d[6] >> 7) & 1u; }
inline uint8_t cellUndervolt(const uint8_t* d) { return (d[6] >> 6) & 1u; }
inline uint8_t socHalfPct(const uint8_t* d) { return d[3]; }  // 0x410 0.5 %

// 0x410 bcm_ibat 39|16@0+ 0.025 A/count, offset -1000 -> centi-amps.
// Positive while charging. Numerator is non-negative so / matches Python's //.
inline int32_t ibatCa(const uint8_t* d) {
  return (int32_t)((((uint32_t)d[4] << 8) | d[5]) * 25u / 10u) - 100000;
}

inline uint8_t bmsEpo(const uint8_t* d) { return (d[0] >> 1) & 1u; }
inline uint8_t bmsHvilMon(const uint8_t* d) { return (d[0] >> 3) & 1u; }
inline uint8_t bmsAlarm(const uint8_t* d) {  // 0 NONE 1 TYPE1 2 TYPE2 3 TYPE3
  return (d[1] >> 6) & 3u;
}
inline uint8_t bmsChgDone(const uint8_t* d) { return (d[6] >> 4) & 1u; }
inline uint8_t maincStat4(const uint8_t* d) {  // 0x440 2|4@1+
  return (d[0] >> 2) & 0x0Fu;
}
// 0x649 vcm_evap_active 39|1@1+ (B4 bit 7): 1 = purge pending or purging.
inline uint8_t evapActive(const uint8_t* d) { return (d[4] >> 7) & 1u; }

// 0x18FFD9C0 BELINV_maxAvailableChargingCurrent 0|16@1-, 0.05 A/count.
// The charger owns the J1772 inlet and measures the pilot itself, so this is
// the authoritative EVSE limit on the bus.
inline int32_t maxAvailCa(const uint8_t* d) {
  int32_t v = (int32_t)(((uint32_t)d[1] << 8) | d[0]);
  if (v & 0x8000) v -= 0x10000;
  return v * 5;
}

// 0x18FFD8C0 BELINV_chargePilotOnlineTime: 40|24@1+, MINUTES (spec 3, A3).
inline uint32_t pilotOnlineMin(const uint8_t* d) {
  return (uint32_t)d[5] | ((uint32_t)d[6] << 8) | ((uint32_t)d[7] << 16);
}

inline uint8_t chgMux(const uint8_t* d) { return d[0]; }

// 0x18FFD4C0 mux 0: BELINV_vehicleConnected at 12|1@1+ -> d[1] bit 4.
inline uint8_t chgVehicleConnected(const uint8_t* d) {
  return (uint8_t)((d[1] >> 4) & 1u);
}
inline uint8_t chgState(const uint8_t* d) { return d[5]; }  // mux 0, 40|8@1+

// 0x18FFD4C0 mux 3 fault bits
inline uint8_t chgFaultInverter(const uint8_t* d) { return d[2] & 1u; }
inline uint8_t chgFaultBuckBoost(const uint8_t* d) { return (d[2] >> 1) & 1u; }
inline uint8_t chgFaultHvOverVolt(const uint8_t* d) { return (d[1] >> 4) & 1u; }
inline uint8_t chgFaultOverTemp(const uint8_t* d) { return (d[1] >> 6) & 1u; }

// --- 0x18EFC000 pages -----------------------------------------------------

// Page identity. Page 03 is sub-paged by byte 1; everything else by byte 0.
struct PageKey {
  uint8_t a;
  uint8_t b;
  bool sub;
  bool operator==(const PageKey& o) const {
    return a == o.a && sub == o.sub && (!sub || b == o.b);
  }
};
inline PageKey pageKey(const uint8_t* d) {
  PageKey k;
  if (d[0] == 0x03u) {
    k.a = 0x03u;
    k.b = d[1];
    k.sub = true;
  } else {
    k.a = d[0];
    k.b = 0;
    k.sub = false;
  }
  return k;
}

inline uint8_t masterMode(const uint8_t* d) { return d[2]; }
inline uint8_t masterFlow(const uint8_t* d) { return d[1]; }
inline int32_t setpointIlimCounts(const uint8_t* d) {
  return (int32_t)(((uint32_t)d[4] << 8) | d[3]);
}

void encMaster(uint8_t flow, uint8_t mode, uint8_t out[8]);
// Rewrite page 01's Ilim, leaving Vlim exactly as the VCU sent it.
void encSetpointIlim(const uint8_t* d, int32_t ilim_counts, uint8_t out[8]);

// --------------------------------------------------------------------------
// the core
// --------------------------------------------------------------------------

class InterposerCore {
 public:
  InterposerCore(const Config& cfg, uint32_t now_ms);

  // Ingress. Each appends whatever should be emitted to `out`; `out` is NOT
  // cleared, so a caller may batch. In the normal case the output is the input
  // frame, unmodified, addressed to the other side.
  void onVehicleFrame(uint32_t id, bool ext, const uint8_t* d, uint8_t len,
                      uint32_t t_ms, EmitList& out);
  void onChargerFrame(uint32_t id, bool ext, const uint8_t* d, uint8_t len,
                      uint32_t t_ms, EmitList& out);

  // Periodic. Watchdogs, the release, the hold decision between frames, and
  // the repeat burst of spec 4.1 -- the only frames the core transmits that
  // the VCU did not just send. Must be called often enough to service the
  // burst at cfg.burst_period_ms and to catch staleness within
  // cfg.bms_stale_ms.
  void tick(uint32_t t_ms, EmitList& out);

  uint8_t state() const { return state_; }
  uint8_t tripReason() const { return trip_reason_; }

  // Spec 8.2: the four status frames (0x7F4-0x7F7), 8 bytes each. Pure
  // packing; the I/O layer supplies what only it knows and sends them on
  // the vehicle side at ~1 Hz plus one STATUS on every state change.
  void diagFrames(uint32_t t_ms, uint8_t rx_overflow, uint8_t tx_fail,
                  uint32_t uptime_ms, bool bridge_ok, bool serial,
                  uint32_t build_id, uint8_t frames[4][8]) const;
  // Our commanded current while overriding, in 0.1 A (255 = not commanding).
  uint8_t ourIlimX10() const;
  const Config& config() const { return cfg_; }

  // Diagnostics.
  uint32_t fwdV2C() const { return fwd_v2c_; }
  uint32_t fwdC2V() const { return fwd_c2v_; }
  uint32_t modified() const { return modified_; }
  uint32_t synth() const { return synth_; }

  static const uint8_t MAX_EVENTS = 64;
  uint8_t eventCount() const { return event_n_; }
  const Event& event(uint8_t i) const { return events_[i]; }
  void clearEvents() { event_n_ = 0; }

  // Live view, for a status frame or a serial dump.
  int32_t vmax() const { return vmax_; }
  int32_t chgMax() const { return chg_max_; }
  int32_t ibat() const { return ibat_; }
  uint8_t socHalf() const { return soc_half_; }
  int32_t maxAvail() const { return max_avail_; }
  uint8_t chargerState() const { return chg_state_; }
  // main.cpp gates the spec 2.2 transmit-failure counter on this.
  uint8_t vcuFlow() const { return vcu_flow_; }
  // Spec 2.2: the VCU's flow bit is 1 and no charger status frame has
  // arrived for the charger-staleness threshold. See machine.py's
  // chg_silent_while_expected() for why the flow bit is the gate.
  bool chargerSilentWhileExpected(uint32_t t_ms) const {
    if (!vcu_flow_) return false;
    return !have_t_chg_ || (uint32_t)(t_ms - t_chg_) > cfg_.chg_stale_ms;
  }
  uint8_t evap() const { return evap_; }

 private:
  void log(uint32_t t_ms, uint8_t code, int32_t a = 0, int32_t b = 0,
           int32_t c = 0);
  void gotoState(uint32_t t_ms, uint8_t st, uint8_t code, int32_t a = 0,
                 int32_t b = 0, int32_t c = 0);
  bool active() const { return state_ == S_MONITOR || state_ == S_OVERRIDE; }
  // `b` rides into the TRIP event's second field: the stale message's
  // identifier for TRIP_BMS_STALE (B-6), 0 for every other reason.
  void trip(uint32_t t_ms, uint8_t reason, EmitList& out, int32_t b = 0);
  void maybeArm(uint32_t t_ms);
  void wait(uint32_t t_ms, uint8_t key, uint8_t code, int32_t a, int32_t b);
  void evaluateHold(uint32_t t_ms, bool from_frame);
  void sessionBoundary(uint32_t t_ms, uint8_t ev, int32_t a, int32_t b,
                       bool clear_safe);
  void repeatMaster(uint32_t t_ms, uint8_t mode);
  void emitRepeat(uint32_t t_ms, EmitList& out);

  bool isGenuinelyFull() const;
  int32_t evseCapCa() const;
  int32_t ourIlimCounts() const;

  void onCommand(const uint8_t* d, uint32_t t_ms, EmitList& out);

  Config cfg_;
  uint8_t state_;
  uint8_t trip_reason_;

  // latest BMS / charger / VCM view
  int32_t vmax_;
  int32_t vmin_;
  int32_t tmax_;
  int32_t tmin_;
  int32_t chg_max_;
  int32_t ibat_;
  int32_t max_avail_;  // charger's pilot-derived cap, cA (-1 = unseen)
  int32_t learned_ilim_;
  uint8_t soc_half_;
  uint8_t chg_state_;
  uint8_t vcu_mode_;
  uint8_t vcu_flow_;
  uint8_t evap_;  // vcm_evap_active, 0 until 0x649 is seen
  bool mainc_closed_;
  bool have_bms_;
  bool chg_seen_charging_;

  // freshness. Python uses None for "never seen"; explicit flags here rather
  // than a sentinel, so uint32 wraparound cannot be mistaken for a timestamp.
  // Spec 6 (B-6): each BMS message is timed SEPARATELY. One timer refreshed
  // by any BMS frame let the core keep commanding on a 60 s old permission
  // while the other messages stayed fresh (review probe P5). Indexed by
  // BMS_IDX_*, in the order tick() tests them -- the same order machine.py's
  // BMS_IDS walks, so a tick with several stale at once reports the same one.
  bool have_t_bms_[N_BMS_IDS];
  uint32_t t_bms_[N_BMS_IDS];
  bool have_t_chg_;
  bool have_chg_bad_;
  bool have_t_full_;
  uint32_t t_chg_;
  uint32_t chg_bad_since_;
  uint32_t t_full_since_;
  uint32_t t_enter_;

  // spec 3: the last reason the hold decision was still waiting (0 = none),
  // so the log carries it once, not per frame.
  uint8_t wait_reason_;

  // spec 4.1: the repeat of the VCU's standing page-00 command, emitted by
  // tick() at burst_period_ms; any page-00 frame from the VCU ends it.
  uint16_t burst_left_;
  uint16_t burst_sent_;           // frames of THIS burst already emitted
  uint32_t burst_next_ms_;
  uint8_t burst_frame_[8];

  // spec 6: the VCU dropped the flow bit while we were armed (an observed
  // 1 -> 0 edge): teardown underway, BMS/charger signals are not faults.
  // spec 3 (A3): the Bel unit's pilot timer, in minutes. The FIRST value
  // read after power-up decides whether we may join the session at all; a
  // backwards step afterwards is a session boundary (6.1).
  uint32_t pilot_min_;
  bool pilot_seen_;
  bool boot_locked_;

  // spec 6: BELINV_vehicleConnected, for the 1 -> 0 edge at a handle pull.
  // -1 = no mux-0 frame seen yet, so the first one cannot be an edge
  // however its byte 1 happens to read.
  int8_t chg_veh_conn_;

  uint32_t fwd_v2c_;
  uint32_t fwd_c2v_;
  uint32_t modified_;
  uint32_t synth_;  // repeated page-00 frames (spec 4.1)

  Event events_[MAX_EVENTS];
  uint8_t event_n_;
};

}  // namespace interposer
