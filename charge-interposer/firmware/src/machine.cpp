#include "machine.h"

namespace interposer {

// --------------------------------------------------------------------------
// names -- diagnostics only
// --------------------------------------------------------------------------

const char* stateName(uint8_t s) {
  switch (s) {
    case S_PASSTHROUGH: return "PASSTHROUGH";
    case S_MONITOR: return "MONITOR";
    case S_OVERRIDE: return "OVERRIDE";
    case S_TERMINATED: return "TERMINATED";
    case S_SAFE: return "SAFE";
    case S_HOLD: return "HOLD";
    default: return "?";
  }
}

const char* tripName(uint8_t r) {
  switch (r) {
    case TRIP_NONE: return "none";
    case TRIP_VMAX_HARD: return "vmax over hard ceiling";
    case TRIP_CELL_OVERVOLT: return "BMS cell_overvolt";
    case TRIP_CELL_UNDERVOLT_RETIRED: return "BMS cell_undervolt (retired)";
    case TRIP_BMS_EPO: return "BMS EPO";
    case TRIP_BMS_ALARM_TYPE3: return "BMS alarm TYPE3";
    case TRIP_HVIL_OPEN: return "HVIL open";
    case TRIP_CONTACTOR: return "contactor not in a closed state";
    case TRIP_CHARGER_FAULT: return "charger fault";
    case TRIP_CHG_STATE: return "charger out of state 12";
    case TRIP_BMS_STALE: return "BMS frames stale";
    case TRIP_CHG_STALE: return "charger frames stale";
    case TRIP_OVERRIDE_CAP: return "override exceeded session cap";
    default: return "?";
  }
}

const char* eventName(uint8_t c) {
  switch (c) {
    case EV_ARMED: return "charge established";
    case EV_VCU_CHARGE_AGAIN: return "VCU commanded CHARGE again";
    case EV_HOLD_ENDED_BY_VCU:
      return "VCU commanded CHARGE during the hold: hold ended, transparent";
    case EV_OVERRIDE_ENDED_BY_VCU:
      return "VCU commanded CHARGE during the override: override ended, transparent";
    case EV_SESSION_OVER: return "session over";
    case EV_WAIT_NO_BMS: return "LOW_POWER seen, no BMS data yet -- waiting";
    case EV_ACCEPT_EVAP_CLEAR: return "LOW_POWER accepted: evap flag clear";
    case EV_ACCEPT_SOC_BELOW_GATE:
      return "LOW_POWER accepted: evap flag set but soc below the gate";
    case EV_WAIT_ARM_DELAY:
      return "LOW_POWER seen with the evap flag set -- waiting out the arm delay";
    case EV_WAIT_NO_EVSE_LIMIT:
      return "LOW_POWER seen with the evap flag set -- no EVSE current limit known yet, not driving";
    case EV_OVERRIDE: return "LOW_POWER overridden";
    case EV_TOP_OF_CHARGE: return "top of charge reached";
    case EV_RELEASE:
      // Spec 5.2 (2026-10-08): no longer transparent. run_scenario.py
      // matches on the "RELEASE" substring, which is preserved.
      return "RELEASE: the VCU's Low Power now reaches the charger; the setpoint is ours";
    case EV_TRIP: return "TRIP";
    case EV_REPEAT: return "REPEAT: the VCU's standing page-00 command to the charger";
    case EV_REPEAT_END: return "REPEAT ended";
    case EV_FLOW_DROP:
      return "VCU dropped the flow bit: override ended, transparent";
    case EV_BOOT_LOCKED:
      return "booted into a session already running: PASSTHROUGH until the next session boundary";
    case EV_PILOT_BACK:
      return "pilot timer stepped back: new session";
    case EV_CHG_SILENCE:
      return "no charger status frame: Bel unit offline";
    case EV_HANDLE_PULL:
      return "charger reports the plug out (vehicleConnected 1 -> 0): transparent";
    default: return "?";
  }
}

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------

void EmitList::push(uint8_t port, uint32_t id, bool ext, const uint8_t* d,
                    uint8_t len) {
  if (n >= EMIT_MAX) {
    if (dropped < 255) dropped++;
    return;
  }
  Emit& e = items[n++];
  e.port = port;
  e.id = id;
  e.ext = ext;
  e.len = len > 8 ? 8 : len;
  for (uint8_t i = 0; i < e.len; i++) e.data[i] = d[i];
  for (uint8_t i = e.len; i < 8; i++) e.data[i] = 0;
}

void configDefaults(Config& c) {
  // Spec 5.1: release when the BMS's permission has collapsed (3.00 A).
  c.chgmax_full_ca = 300;  // 3.00 A

  // Spec 3: the override gate. 78 % in bcm_soc's half-percent counts.
  c.soc_arm_half = 156;

  c.vmax_hard_mv = 3610;
  c.learned_ilim_min_ca = 500;  // 5.00 A
  c.burst_frames = 20;
  c.burst_period_ms = 50;

  c.arm_delay_ms = 75000;
  c.full_debounce_ms = 1000;
  // Raised 2000 -> 5000 on 2026-09-30 (spec 6, A8a).
  c.chg_state_debounce_ms = 5000;
  c.bms_stale_ms = 500;
  c.chg_stale_ms = 500;
  c.chg_silence_ms = 20000;  // spec 6.1
  c.override_max_ms = 6u * 3600u * 1000u;

  // Spec 5.2: the hold after the release.
  c.hold_step_ca = 10;        // 0.1 A per step
  c.hold_band_ca = 25;        // hold while within 0.25 A below
  c.hold_step_min_ms = 300;   // at most one step per 0.3 s

  c.revert_aux_pages = true;
  c.mask_charger_telemetry = false;
  c.diag_mirror = true;
}

static bool same8(const uint8_t* a, const uint8_t* b) {
  for (uint8_t i = 0; i < 8; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

void encMaster(uint8_t flow, uint8_t mode, uint8_t out[8]) {
  out[0] = 0x00;
  out[1] = flow;
  out[2] = mode;
  out[3] = 0;
  out[4] = 0;
  out[5] = 0;
  out[6] = 0;
  out[7] = 0;
}

void encSetpointIlim(const uint8_t* d, int32_t ilim_counts, uint8_t out[8]) {
  int32_t i = ilim_counts;
  if (i < 0) i = 0;
  if (i > 0xFFFF) i = 0xFFFF;
  out[0] = d[0];
  out[1] = d[1];
  out[2] = d[2];
  out[3] = (uint8_t)(i & 0xFF);
  out[4] = (uint8_t)((i >> 8) & 0xFF);
  out[5] = d[5];
  out[6] = d[6];
  out[7] = d[7];
}

static const uint8_t CONST_03_02_CHARGING[8] = {0x03, 0x02, 0x00, 0x00,
                                                0x00, 0x00, 0x00, 0x00};
static const uint8_t CONST_03_07_CHARGING[8] = {0x03, 0x07, 0x02, 0x64,
                                                0x04, 0x32, 0x00, 0x00};

// --------------------------------------------------------------------------
// construction
// --------------------------------------------------------------------------

InterposerCore::InterposerCore(const Config& cfg, uint32_t now_ms)
    : cfg_(cfg),
      state_(S_PASSTHROUGH),
      trip_reason_(TRIP_NONE),
      vmax_(0),
      vmin_(0),
      tmax_(0),
      tmin_(0),
      chg_max_(0),
      ibat_(0),
      max_avail_(-1),
      learned_ilim_(0),
      soc_half_(0),
      chg_state_(0),
      vcu_mode_(MODE_EXPORT),
      vcu_flow_(0),
      evap_(0),
      mainc_closed_(false),
      have_bms_(false),
      chg_seen_charging_(false),

      have_t_chg_(false),
      have_chg_bad_(false),
      have_t_full_(false),
      hold_valid_(false),
      have_t_hold_step_(false),

      t_chg_(0),
      chg_bad_since_(0),
      t_full_since_(0),
      hold_ca_(0),
      t_hold_step_(0),
      t_enter_(now_ms),
      wait_reason_(0),
      burst_left_(0),
      burst_sent_(0),
      burst_next_ms_(0),
      pilot_min_(0),
      pilot_min_valid_(false),
      pilot_seen_(false),
      boot_locked_(false),
      chg_veh_conn_(-1),
      fwd_v2c_(0),
      fwd_c2v_(0),
      modified_(0),
      synth_(0),
      event_n_(0) {
  for (uint8_t i = 0; i < 8; i++) burst_frame_[i] = 0;
  // B-6: one staleness timer per BMS message, all "never seen" at boot.
  for (uint8_t i = 0; i < N_BMS_IDS; i++) {
    have_t_bms_[i] = false;
    t_bms_[i] = 0;
  }
}

void InterposerCore::log(uint32_t t_ms, uint8_t code, int32_t a, int32_t b,
                         int32_t c) {
  if (event_n_ >= MAX_EVENTS) return;
  Event& e = events_[event_n_++];
  e.t_ms = t_ms;
  e.state = state_;
  e.code = code;
  e.a = a;
  e.b = b;
  e.c = c;
}

void InterposerCore::gotoState(uint32_t t_ms, uint8_t st, uint8_t code,
                               int32_t a, int32_t b, int32_t c) {
  if (st == state_) return;
  state_ = st;
  t_enter_ = t_ms;
  wait_reason_ = 0;
  log(t_ms, code, a, b, c);
}

// --------------------------------------------------------------------------
// decisions
// --------------------------------------------------------------------------

// Are WE done charging? Spec 5.1: the BMS's permission has collapsed. Only
// chg_max -- a vmax threshold is unreachable under real imbalance and the
// truck never keys on one; the 3610 mV hard trip is the backstop.
bool InterposerCore::isGenuinelyFull() const {
  return chg_max_ <= cfg_.chgmax_full_ca;
}

// The EVSE current ceiling we must not command past, in cA.
//
// J1772 compliance matters here and it is not automatic. Measured across two
// captures of the same truck on different EVSE settings:
//
//   pilot 26 % (16 A EVSE): VCU commands  9.70 A, charger maxAvail  8 A
//   pilot 52 % (32 A EVSE): VCU commands 18.80 A, charger maxAvail 16 A
//
// BOTH ends are pilot-aware. The charger is the binding constraint, but relying
// on that alone would make our override only accidentally compliant. The
// charger's value is used INSTANTANEOUSLY, not latched at its maximum: EVSEs
// with load management genuinely reduce the pilot mid-session, and following it
// down is the whole point. A learned VCU command of 0 is "none", not a cap: a
// charge that starts above the ceiling carries a 0 A setpoint (spec 7).
//
// Returns -1 if we have no EVSE-derived limit at all, in which case we must not
// drive the charge.
int32_t InterposerCore::evseCapCa() const {
  int32_t cap = -1;
  if (max_avail_ >= 0) cap = max_avail_;
  if (learned_ilim_) {
    if (cap < 0 || learned_ilim_ < cap) cap = learned_ilim_;
  }
  return cap;
}

// Our current command while overriding, in page-01 counts (0.05 A each).
// Spec 4: the BMS's permission, capped by the EVSE. No floor, no taper.
int32_t InterposerCore::ourIlimCounts() const {
  const int32_t cap = evseCapCa();
  if (cap < 0) return 0;
  int32_t ca = chg_max_;
  if (cap < ca) ca = cap;
  if (ca < 0) ca = 0;
  return ca / 5;  // centi-amps -> 0.05 A counts
}

// What we are telling the charger right now, in page-01 counts.
//
// Spec 8.2 says the status frame carries "our commanded current", and since
// 2026-10-08 that is the HOLD setpoint while in HOLD -- the number actually
// going out on page 01. ourIlimCounts() is the OVERRIDE command, the BMS
// permission capped by the EVSE, and in HOLD it is not what the charger is
// being told: the hold tracks the permission in 0.1 A steps rather than
// following it exactly.
int32_t InterposerCore::commandedIlimCounts() const {
  if (state_ == S_HOLD && hold_valid_) return hold_ca_ / 5;
  return ourIlimCounts();
}

// Spec 5.2: move the hold setpoint one step toward the permission.
//
// Called on each page-01 frame from the VCU, which is what the hold
// rewrites; the cadence of the loop is therefore the VCU's own frame
// cadence, limited to one step per hold_step_min_ms.
//
// bcm_ibat is the PACK current, which nets out every other load on the bus
// -- that is why the loop is closed on it rather than on our own setpoint.
// The truck's own hold does not do this: with bcm_chg_max at 0 A it puts
// +0.85 to +2.55 A into the pack for the first hour.
//
// Above the permission it steps down; more than hold_band_ca below it, up;
// inside the band it holds. The result sits at or just under the BMS's
// permission, and is above it only for the steps a sudden load drop takes to
// remove -- about 2 s for 0.7 A.
void InterposerCore::stepHold(uint32_t t_ms) {
  if (!hold_valid_) return;
  if (have_t_hold_step_ &&
      (uint32_t)(t_ms - t_hold_step_) < cfg_.hold_step_min_ms) {
    return;
  }
  int32_t want = hold_ca_;
  if (ibat_ > chg_max_) {
    want = hold_ca_ - cfg_.hold_step_ca;
  } else if (ibat_ < chg_max_ - cfg_.hold_band_ca) {
    want = hold_ca_ + cfg_.hold_step_ca;
  }
  // Spec 5.2: "It stays between 0 A and the EVSE cap of section 4." The cap
  // is clamped even when the step did not move, because the cap itself can
  // fall: max_avail is learned from the charger and a lower one must bring
  // the hold down with it.
  const int32_t cap = evseCapCa();
  if (cap >= 0 && want > cap) want = cap;
  if (want < 0) want = 0;
  if (want != hold_ca_) {
    hold_ca_ = want;
    t_hold_step_ = t_ms;
    have_t_hold_step_ = true;
  }
}

// --------------------------------------------------------------------------
// trips and arming
// --------------------------------------------------------------------------

// Bail to transparent forwarding and latch SAFE (spec 6). A trip is the same
// action as a release: we stop rewriting, and if we were overriding we repeat
// the VCU's standing command to the charger (spec 4.1) -- its Low Power hold.
// Out of HOLD nothing is repeated, and that is spec 6 in as many words: "the
// page-01 rewrite stopped at once if we were holding (5.2; page 00 is already
// the VCU's, so nothing is repeated)". The charger is already in Low Power
// because the release put it there.
//
// Only meaningful while armed. Outside MONITOR/OVERRIDE/HOLD we are a wire,
// and half of these conditions are NORMAL off-charge traffic -- EPO and the
// alarm bits fire during every ordinary charge shutdown, and HVIL is open
// before precharge. Tripping on those merely fills the log and latches SAFE
// before the session even starts. Callers gate on active(); this is the
// backstop.
//
// Teardown is not a fault (spec 6): once the VCU has dropped the flow bit the
// BMS's HVIL, EPO and contactor signals are the close-out.
void InterposerCore::trip(uint32_t t_ms, uint8_t reason, EmitList& out,
                          int32_t b) {
  // No teardown window (spec 6, changed 2026-09-30): a flow drop ends the
  // override, so the handle-pull signals that follow arrive in TERMINATED
  // where active() is already false. Nothing needs suppressing.
  if (!active()) return;
  trip_reason_ = reason;
  const bool was_override = (state_ == S_OVERRIDE);
  gotoState(t_ms, S_SAFE, EV_TRIP, reason, b);
  if (was_override) {
    repeatMaster(t_ms, vcu_mode_);
    emitRepeat(t_ms, out);
  }
}

// Spec 4.1: repeat the VCU's standing page-00 command toward the charger,
// rewritten as the new state requires, in the VCU's own burst shape. tick()
// emits it; the flow bit is the VCU's own.
void InterposerCore::repeatMaster(uint32_t t_ms, uint8_t mode) {
  // SPEC 4.1, 2026-10-05: "A repeat that starts while another is still
  // being sent replaces it: the earlier one stops there, the frames it
  // already sent stand, and only the new command continues."
  //
  // Logged BEFORE the new EV_REPEAT and before burst_sent_ is cleared,
  // so the pair reads in the order the events happened and carries the
  // count that actually went out. Without this line the two REPEATs
  // appeared with only one end between them, and the replaced burst's
  // frames looked like they belonged to the new command.
  if (burst_left_ > 0) {
    log(t_ms, EV_REPEAT_END, burst_sent_, cfg_.burst_frames, REPEAT_REPLACED);
  }
  encMaster(vcu_flow_, mode, burst_frame_);
  burst_left_ = cfg_.burst_frames;
  burst_sent_ = 0;
  burst_next_ms_ = t_ms;
  log(t_ms, EV_REPEAT, vcu_flow_, mode, cfg_.burst_frames);
}

void InterposerCore::emitRepeat(uint32_t t_ms, EmitList& out) {
  while (burst_left_ > 0 && (int32_t)(t_ms - burst_next_ms_) >= 0) {
    out.push(TO_CHARGER, CMD_ID, true, burst_frame_, 8);
    if (cfg_.diag_mirror) out.push(TO_VEHICLE, mirrorId(CMD_ID), true, burst_frame_, 8);
    synth_++;
    burst_sent_++;
    burst_left_--;
    burst_next_ms_ += cfg_.burst_period_ms;
  }
  // Report what went, not what was intended. emitRepeat() is only reached
  // with burst_left_ > 0, so this fires exactly once per completed burst.
  if (burst_left_ == 0) {
    log(t_ms, EV_REPEAT_END, burst_sent_, cfg_.burst_frames, REPEAT_COMPLETE);
  }
}

// Arm on an OBSERVED charge, never on the VCU's command alone.
//
// Two reasons this is keyed to observation rather than to `00 01 01`:
//
//  * The command leads reality by a long way. In
//    vtruxchargeafterexportchargetest-80pctcv the VCU commands CHARGE at
//    t=79.6 s while the contactors are still open (bcm_mainc_stat = 0) and
//    precharge has not run. Arming there means immediately tripping on a
//    contactor state that is perfectly normal for that moment.
//  * The command may never be seen at all. vtrux_charge_M1's capture starts at
//    76 % SoC, already charging, and its first page-00 frame is the low-power
//    command at t=3077 s. A board powered up mid-session is in that position.
//
// Observed means the charger in state 12 with the contactors closed. NOT pack
// current: a charge that starts above the ceiling never shows a net charging
// current (the 83 % start drained the pack through its whole hold, spec 7).
void InterposerCore::maybeArm(uint32_t t_ms) {
  // Spec 3 (A3): do not arm before the first pilot value has been read.
  // 0x18FFD4C0 is 10 Hz and 0x18FFD8C0 is 1 Hz, so the charge is routinely
  // observable first; arming before the pilot value arrives left the core in
  // MONITOR and a later value of 5 set the flag without changing anything.
  // A charger that never sends 0x18FFD8C0 is therefore never overridden,
  // which errs toward the truck's normal charging.
  if (state_ == S_PASSTHROUGH && chg_seen_charging_ && mainc_closed_ &&
      pilot_seen_ && !boot_locked_) {
    gotoState(t_ms, S_MONITOR, EV_ARMED, ibat_);
  }
}

// Still MONITOR; log the reason once per change of reason.
void InterposerCore::wait(uint32_t t_ms, uint8_t key, uint8_t code, int32_t a,
                          int32_t b) {
  if (key != wait_reason_) {
    wait_reason_ = key;
    log(t_ms, code, a, b);
  }
}

// The VCU is commanding Low Power and we are armed. The evap ceiling, or
// something to accept? (spec 3) Evaluated on every Low Power frame and on
// every tick while it lasts, never only on the edge into Low Power. An
// override decided from a tick has no frame to rewrite, so the VCU's standing
// command is repeated as CHARGER (spec 4.1); one decided on a frame rewrites
// that frame.
void InterposerCore::sessionBoundary(uint32_t t_ms, uint8_t ev, int32_t a,
                                     int32_t b, bool clear_safe,
                                     bool forget_pilot) {
  // Spec 6.1: ONE reset path for all three boundaries. The defect this
  // replaced was the STAND_BY path clearing only part of the session state,
  // while 11 of the 49 terminations in the survey do not end on STAND_BY --
  // so SAFE stayed latched into the next session and chg_seen_charging
  // carried over, letting the next session arm before its own charger
  // reached 12.
  if (clear_safe) trip_reason_ = TRIP_NONE;
  // A boundary that does not move the state must still SAY so. The earlier
  // form logged only through gotoState, so a clear_safe boundary arriving in
  // PASSTHROUGH -- the ordinary case for the pilot timer, because the handle
  // pull has already ended the session by the time the timer returns to 0 --
  // reset chg_seen_charging_, boot_locked_, max_avail_ and learned_ilim_ and
  // left no trace of having done it.
  if (state_ != S_PASSTHROUGH && (clear_safe || state_ != S_SAFE)) {
    gotoState(t_ms, S_PASSTHROUGH, ev, a, b);
  } else if (!(event_n_ > 0 && events_[event_n_ - 1].code == ev
               && events_[event_n_ - 1].a == a
               && events_[event_n_ - 1].b == b)) {
    // Collapse a contiguous run of the same boundary to one line. The reset
    // below stays level-triggered and fires on every frame, because
    // maybeArm() does not look at the VCU's mode: it is the repeated
    // clearing of chg_seen_charging_ and mainc_closed_ that stops the core
    // arming while the VCU is commanding STAND_BY. Only the LOG is an edge.
    // The VCU sends its page-00 changes as a 20-frame burst, so without this
    // one session end reads as twenty.
    log(t_ms, ev, a, b);
  }
  have_t_full_ = false;
  learned_ilim_ = 0;
  // Spec 6.1: a boundary resets "the HOLD or TERMINATED state" and
  // everything the session learned, so the hold setpoint goes too -- it is
  // the most session-specific number the core holds, and a second charge
  // inheriting the first one's setpoint would command the previous pack
  // state.
  hold_valid_ = false;
  have_t_hold_step_ = false;
  chg_seen_charging_ = false;
  mainc_closed_ = false;
  have_chg_bad_ = false;
  max_avail_ = -1;
  wait_reason_ = 0;
  if (burst_left_ > 0) {
    log(t_ms, EV_REPEAT_END, burst_sent_, cfg_.burst_frames, REPEAT_RESET);
  }
  burst_left_ = 0;
  boot_locked_ = false;
  if (forget_pilot) {
    // Spec 6.1: "The charger-silence boundary also forgets the last
    // pilot-timer reading. The first reading after the silence is not
    // compared with the last one before it, so a timer that comes
    // back lower is not a step-back and does not clear SAFE."
    //
    // Without this the core trips on the silence, the Bel unit
    // restarts with the handle still in, its timer resumes from a
    // lower minute count, and that reads as a replug and clears the
    // latch the silence set -- the exact outcome the 20 s row exists
    // to prevent.
    //
    // pilot_seen_ is deliberately NOT reset: it gates arming (spec 3,
    // A3), not the comparison.
    pilot_min_valid_ = false;
  }
}

void InterposerCore::evaluateHold(uint32_t t_ms, bool from_frame) {
  if (state_ != S_MONITOR) return;
  // Spec 3 (A5): decided only while the flow bit is 1, on BOTH paths. The
  // tick path always checked it; the frame path did not, so a first Low
  // Power arriving with flow 0 was overridden mid-teardown (review P7). The
  // check lives here so the two paths cannot drift apart again.
  if (!vcu_flow_) return;
  if (!have_bms_) {
    wait(t_ms, 1, EV_WAIT_NO_BMS, 0, 0);
    return;
  }
  // Spec 3 (A7): WAITING COMES FIRST. Nothing latches inside the arm delay,
  // not an override and not an accept. This used to sit below the two accept
  // branches, so a Low Power inside the delay could latch TERMINATED for the
  // session on an SoC the BMS can report 10-20 % off for ~60 s after start.
  if ((uint32_t)(t_ms - t_enter_) < cfg_.arm_delay_ms) {
    wait(t_ms, 2, EV_WAIT_ARM_DELAY, soc_half_, (int32_t)(t_ms - t_enter_));
    return;
  }
  if (!evap_) {
    gotoState(t_ms, S_TERMINATED, EV_ACCEPT_EVAP_CLEAR, soc_half_, vmax_,
              chg_max_);
    return;
  }
  if ((int32_t)soc_half_ < cfg_.soc_arm_half) {
    gotoState(t_ms, S_TERMINATED, EV_ACCEPT_SOC_BELOW_GATE, soc_half_);
    return;
  }
  if (evseCapCa() < 0) {
    wait(t_ms, 3, EV_WAIT_NO_EVSE_LIMIT, soc_half_, 0);
    return;
  }
  gotoState(t_ms, S_OVERRIDE, EV_OVERRIDE, vmax_, chg_max_, soc_half_);
  if (!from_frame) repeatMaster(t_ms, MODE_CHARGER);
}

// --------------------------------------------------------------------------
// ingress -- vehicle side
// --------------------------------------------------------------------------

void InterposerCore::onVehicleFrame(uint32_t id, bool ext, const uint8_t* d,
                                    uint8_t len, uint32_t t_ms, EmitList& out) {
  if (id == BMS_DATA1 && len >= 6) {
    vmax_ = vmaxMv(d);
    vmin_ = vminMv(d);
    tmax_ = tmaxDdegC(d);
    tmin_ = tminDdegC(d);
    t_bms_[BMS_IDX_DATA1] = t_ms;
    have_t_bms_[BMS_IDX_DATA1] = true;
    have_bms_ = true;
    // Cell temperature is deliberately NOT a trip (spec 6): the BMS's own
    // response -- a lower permission, or a fault -- is what we obey.
    if (active() && vmax_ >= cfg_.vmax_hard_mv) {
      trip(t_ms, TRIP_VMAX_HARD, out);
    }

  } else if (id == BMS_LIMITS && len >= 7) {
    chg_max_ = chgMaxCa(d);
    t_bms_[BMS_IDX_LIMITS] = t_ms;
    have_t_bms_[BMS_IDX_LIMITS] = true;
    if (!active()) {
      // not armed: observe only
    } else if (cellOvervolt(d)) {
      trip(t_ms, TRIP_CELL_OVERVOLT, out);
    }
    // bcm_cell_undervolt is deliberately NOT a trip (spec 6, B-7a,
    // 2026-10-03). The core has no low-cell limit of its own: if a cell is
    // dangerously low the interposer must not get in the way of the charger
    // charging it. The BMS declares a pack too low to charge through its
    // permission and its faults, and those are obeyed. cellUndervolt() stays
    // -- check_port.py verifies the decoder against machine.py.

  } else if (id == BMS_STATUS && len >= 7) {
    soc_half_ = socHalfPct(d);
    ibat_ = ibatCa(d);
    t_bms_[BMS_IDX_STATUS] = t_ms;
    have_t_bms_[BMS_IDX_STATUS] = true;
    maybeArm(t_ms);
    if (!active()) {
      // not armed: observe only
    } else if (bmsEpo(d)) {
      trip(t_ms, TRIP_BMS_EPO, out);
    } else if (bmsAlarm(d) >= 3) {
      // TYPE3 only. TYPE2 is a persistent latched state this pack sits in while
      // charging and driving perfectly normally -- see
      // notes/specific-trucks/bcm-alarm-type2-investigation.md. Tripping on
      // TYPE2 would disable the board permanently on a truck that has it
      // latched, which is this truck.
      trip(t_ms, TRIP_BMS_ALARM_TYPE3, out);
    } else if (!bmsHvilMon(d)) {
      trip(t_ms, TRIP_HVIL_OPEN, out);
    }

  } else if (id == BMS_DATA2 && len >= 8) {
    t_bms_[BMS_IDX_DATA2] = t_ms;
    have_t_bms_[BMS_IDX_DATA2] = true;
    const uint8_t ms = maincStat4(d);
    mainc_closed_ = maincClosed(ms);
    maybeArm(t_ms);
    if (active() && !mainc_closed_) {
      trip(t_ms, TRIP_CONTACTOR, out);
    }

  } else if (id == VCM_EVAP && len >= 5) {
    evap_ = evapActive(d);

  } else if (id == CMD_ID && len >= 8) {
    onCommand(d, t_ms, out);
    return;
  }

  // everything that is not the command frame forwards verbatim
  out.push(TO_CHARGER, id, ext, d, len);
  fwd_v2c_++;
}

// Handle a 0x18EFC000 frame -- the only frame we ever modify.
void InterposerCore::onCommand(const uint8_t* d, uint32_t t_ms, EmitList& out) {
  const PageKey key = pageKey(d);
  uint8_t emit[8];
  for (uint8_t i = 0; i < 8; i++) emit[i] = d[i];

  const bool is_page_00 = (!key.sub && key.a == 0x00u);
  const bool is_page_01 = (!key.sub && key.a == 0x01u);

  if (is_page_00) {
    const uint8_t prev_flow = vcu_flow_;
    vcu_flow_ = masterFlow(d);
    const uint8_t mode = masterMode(d);
    vcu_mode_ = mode;
    // spec 4.1: the VCU's own page 00 supersedes any repeat of it
    if (burst_left_ > 0) {
      log(t_ms, EV_REPEAT_END, burst_sent_, cfg_.burst_frames,
          REPEAT_SUPERSEDED);
    }
    burst_left_ = 0;
    if (prev_flow && !vcu_flow_ && active()) {
      // Spec 6 (A5, changed 2026-09-30): a flow drop ENDS the override. The
      // core is transparent from here, so HVIL, EPO, the contactors and the
      // charger leaving 12 all arrive in TERMINATED and cannot trip. This
      // frame reaches the charger unmodified, so no repeat is needed.
      gotoState(t_ms, S_TERMINATED, EV_FLOW_DROP);
    }

    // The flow bit is required below. A handle pull drops flow on a frame
    // that still reads mode CHARGER, and without it the core went TERMINATED
    // on the flow drop and straight back to PASSTHROUGH on the same frame,
    // then re-armed and tripped on the HVIL of the teardown.
    if (mode == MODE_CHARGER && state_ == S_TERMINATED && vcu_flow_) {
      // Become eligible to arm again: the VCU really does go low power ->
      // charger mode after its startup transient (vtrux_partstruck_charge,
      // t=31 s then t=94 s). Actual arming still waits for an observed charge
      // in maybeArm(). SAFE is deliberately NOT re-armable -- a trip latches
      // for the session.
      have_t_full_ = false;
      gotoState(t_ms, S_PASSTHROUGH, EV_VCU_CHARGE_AGAIN);
    } else if (mode == MODE_CHARGER && state_ == S_HOLD && vcu_flow_) {
      // Spec 6 (user, 2026-10-09): the hold ends at once and the core goes
      // STRAIGHT to PASSTHROUGH -- re-armable as after a TERMINATED, with
      // arming still waiting for an observed charge. Not via TERMINATED:
      // the arm above would not catch it on this frame (the chain tests the
      // state as it was on entry, which is HOLD), but the VCU sends a
      // page-00 change as a ~20-frame burst, so frame 2 would hit that arm
      // ~50 ms later and reach PASSTHROUGH anyway. Routing through
      // TERMINATED would look latched without being it.
      //
      // THE FLOW BIT IS REQUIRED, and with it off this branch must not
      // fire: that is the first frame of a handle pull (00 00 01), which
      // the flow-drop row covers. It cannot fire, for two reasons -- the
      // flow-drop check above runs BEFORE this chain and has already taken
      // the core to TERMINATED on that frame, so state_ == S_HOLD is false
      // here; and the vcu_flow_ test fails independently.
      //
      // Defined, not expected: the VCU has never left its hold except at a
      // handle pull.
      have_t_full_ = false;
      hold_valid_ = false;
      have_t_hold_step_ = false;
      gotoState(t_ms, S_PASSTHROUGH, EV_HOLD_ENDED_BY_VCU);
    } else if (mode == MODE_CHARGER && state_ == S_OVERRIDE) {
      // Spec 6: defined, not expected -- the VCU has never left a hold except
      // at a handle pull. The override ends at once and the next Low Power is
      // decided afresh, behind the arm delay.
      have_t_full_ = false;
      gotoState(t_ms, S_MONITOR, EV_OVERRIDE_ENDED_BY_VCU);
    } else if (mode == MODE_STANDBY || mode == MODE_EXPORT) {
      // Spec 6.1: a session boundary, obeyed from any state. Clears SAFE --
      // this is the VCU's own session end.
      sessionBoundary(t_ms, EV_SESSION_OVER, mode, 0, true);
    } else if (mode == MODE_LOW_POWER && state_ == S_MONITOR) {
      // spec 3: decided on every Low Power frame while armed
      evaluateHold(t_ms, true);
      if (state_ == S_OVERRIDE) {
        encMaster(vcu_flow_, MODE_CHARGER, emit);
        modified_++;
      }
    } else if (mode == MODE_LOW_POWER && state_ == S_OVERRIDE) {
      // the VCU keeps re-asserting low power; keep telling the charger otherwise
      encMaster(vcu_flow_, MODE_CHARGER, emit);
      modified_++;
    }

  } else if (is_page_01 && state_ == S_HOLD) {
    // Spec 5.2: in HOLD every page-01 setpoint from the VCU is replaced
    // with ours. Nothing else is touched -- page 00 and with it Low Power
    // mode, and every other frame, reach the charger as the VCU sent them,
    // which is why this branch sits before the 03.02 / 03.07 one and that
    // one stays gated on S_OVERRIDE.
    stepHold(t_ms);
    encSetpointIlim(d, hold_ca_ / 5, emit);
    modified_++;

  } else if (is_page_01 && (state_ == S_MONITOR || state_ == S_OVERRIDE)) {
    if (state_ == S_MONITOR) {
      // learn the VCU's own CC command while it is still charging
      // (hold-sized setpoints are not one: learned_ilim_min_ca)
      const int32_t ca = setpointIlimCounts(d) * 5;
      if (ca >= cfg_.learned_ilim_min_ca && ca > learned_ilim_) learned_ilim_ = ca;
    } else {
      encSetpointIlim(d, ourIlimCounts(), emit);
      modified_++;
    }

  } else if (state_ == S_OVERRIDE && cfg_.revert_aux_pages) {
    if (key.sub && key.a == 0x03u && key.b == 0x02u) {
      for (uint8_t i = 0; i < 8; i++) emit[i] = CONST_03_02_CHARGING[i];
      modified_++;
    } else if (key.sub && key.a == 0x03u && key.b == 0x07u) {
      for (uint8_t i = 0; i < 8; i++) emit[i] = CONST_03_07_CHARGING[i];
      modified_++;
    }
  }

  fwd_v2c_++;
  out.push(TO_CHARGER, CMD_ID, true, emit, 8);
  if (cfg_.diag_mirror && !same8(emit, d)) {
    // spec 8.1: the frame as the charger received it
    out.push(TO_VEHICLE, mirrorId(CMD_ID), true, emit, 8);
  }
}

// --------------------------------------------------------------------------
// ingress -- charger side
// --------------------------------------------------------------------------

void InterposerCore::onChargerFrame(uint32_t id, bool ext, const uint8_t* d,
                                    uint8_t len, uint32_t t_ms, EmitList& out) {
  if (id == CHG_PILOT && len >= 8) {
    const uint32_t mins = pilotOnlineMin(d);
    if (!pilot_seen_) {
      // Spec 3 (A3): the FIRST value after power-up decides. Page 00 is sent
      // only on change, so a core that boots mid-session never hears the
      // VCU's standing command and cannot know what it is.
      pilot_seen_ = true;
      if (mins >= 2u) {
        boot_locked_ = true;
        log(t_ms, EV_BOOT_LOCKED, (int32_t)mins);
      }
    } else if (pilot_min_valid_ && mins < pilot_min_) {
      // Spec 6.1: it returns to 0 at every handle pull, so a step backwards
      // is a new plug-in. Clears SAFE.
      sessionBoundary(t_ms, EV_PILOT_BACK, (int32_t)pilot_min_, (int32_t)mins,
                      true);
    }
    pilot_min_ = mins;
    pilot_min_valid_ = true;
  } else if (id == CHG_INFO && len >= 8) {
    max_avail_ = maxAvailCa(d);

  } else if (id == CHG_STATUS && len >= 8) {
    t_chg_ = t_ms;
    have_t_chg_ = true;
    const uint8_t mux = chgMux(d);
    if (mux == 0) {
      chg_state_ = chgState(d);
      // Spec 6 (A5, 2026-10-03): at a handle pull the CHARGER reports first,
      // 0.8-1.1 s before the VCU drops the flow bit, on all 13 source-11
      // pulls in the corpus. Treated exactly like a flow drop: transparent
      // from this frame, no repeat.
      //
      // It matters because HVIL opens AT the flow drop, and in 5 of those 13
      // up to 20 ms before it, which tripped "HVIL open" mid-override.
      //
      // vehicleConnected is an observed 1 -> 0 EDGE, not a level: a level
      // test fires on any mux-0 frame whose byte 1 is zero, which is what a
      // frame built without that signal looks like.
      // shutdownSource 11 is NOT a trigger (decided 2026-10-03). It is a
      // latched "last stop" reason: on the original Bel unit it stayed at 11
      // with the plug still in for 84-162 s, and in charging45 that swallowed
      // a charger fault 83 s later.
      const uint8_t vc = chgVehicleConnected(d);
      const bool unplugged = (chg_veh_conn_ == 1 && vc == 0);
      chg_veh_conn_ = (int8_t)vc;
      //
      // An else-if chain, NOT an early return: the frame carrying the
      // plug-out must still reach the vehicle like every other (spec 2).
      // Returning here swallowed it and the VCU learned of the plug-out
      // only from the next mux-0 frame ~0.4 s later. The differential was
      // blind to it because both cores had the same bug (review probe Q4).
      if (active() && unplugged) {
        gotoState(t_ms, S_TERMINATED, EV_HANDLE_PULL, vc);
        have_chg_bad_ = false;
      } else if (chg_state_ == CHG_STATE_CHARGING) {
        chg_seen_charging_ = true;
        have_chg_bad_ = false;
      } else if (chg_seen_charging_ && active()) {
        // Only a departure FROM charging counts, and only a SUSTAINED one --
        // see chg_state_debounce_ms. The trip itself is raised in tick().
        if (!have_chg_bad_) {
          have_chg_bad_ = true;
          chg_bad_since_ = t_ms;
        }
      } else {
        have_chg_bad_ = false;
      }
    } else if (mux == 3) {
      if (chgFaultInverter(d) || chgFaultBuckBoost(d) ||
          chgFaultHvOverVolt(d) || chgFaultOverTemp(d)) {
        trip(t_ms, TRIP_CHARGER_FAULT, out);
      }
    }
  }

  uint8_t emit[8];
  const uint8_t n = len > 8 ? 8 : len;
  for (uint8_t i = 0; i < n; i++) emit[i] = d[i];

  if (cfg_.mask_charger_telemetry && state_ == S_OVERRIDE &&
      id == CHG_HV_STATUS && len >= 8) {
    // STEALTH. Zero the two current fields of BELINV_hvBatteryAndCharger so the
    // VCU does not see current flowing after it commanded a stop. Voltages are
    // left alone. UNVERIFIED and off by default -- we do not know that the VCU
    // objects, and lying to it is a bigger intervention than the one we already
    // make.
    emit[2] = 0;
    emit[3] = 0;
    emit[6] = 0;
    emit[7] = 0;
    modified_++;
  }

  out.push(TO_VEHICLE, id, ext, emit, n);
  fwd_c2v_++;
  if (cfg_.diag_mirror && n == 8 && !same8(emit, d)) {
    // spec 8.1: the frame as received from the charger, since the
    // vehicle bus only sees the modified one
    out.push(TO_VEHICLE, mirrorId(id), ext, d, n);
  }
}

// --------------------------------------------------------------------------
// periodic
// --------------------------------------------------------------------------

// The BMS identifiers in BmsIdx order, for the staleness report (B-6). At
// file scope rather than function-local: a function-local static would carry
// a guard variable on the target for no gain.
static const uint32_t kBmsIds[N_BMS_IDS] = {BMS_STATUS, BMS_LIMITS,
                                            BMS_DATA1, BMS_DATA2};

void InterposerCore::tick(uint32_t t_ms, EmitList& out) {
  if (burst_left_ > 0) emitRepeat(t_ms, out);

  // Spec 6.1: the charger-silence boundary applies FROM ANY STATE, so it is
  // checked before the armed-only return below. It has to be: the 500 ms
  // staleness row trips the core to SAFE long before 20 s elapses, and from
  // SAFE that return would skip this entirely.
  if (have_t_chg_ && (uint32_t)(t_ms - t_chg_) >= cfg_.chg_silence_ms) {
    sessionBoundary(t_ms, EV_CHG_SILENCE, (int32_t)(t_ms - t_chg_), 0,
                    false, true);
    have_t_chg_ = false;  // one boundary per silence, not one a tick
  }

  // Spec 5.2: "The trips of section 6 apply throughout, including 0x410
  // staleness, since the hold reads bcm_ibat from it." The staleness and
  // charger-state trips are raised HERE, on the clock, so a gate naming
  // MONITOR and OVERRIDE alone left the hold with no timers at all -- the
  // one state that reads a BMS value continuously would have been the only
  // armed state that could not notice the value going stale. active() is
  // the same set every trip caller gates on, so the two cannot drift.
  if (!active()) return;

  if (have_chg_bad_ &&
      (uint32_t)(t_ms - chg_bad_since_) >= cfg_.chg_state_debounce_ms) {
    trip(t_ms, TRIP_CHG_STATE, out);
    return;
  }
  for (uint8_t i = 0; i < N_BMS_IDS; i++) {
    if (!have_t_bms_[i] ||
        (uint32_t)(t_ms - t_bms_[i]) > cfg_.bms_stale_ms) {
      // The identifier rides in the event's `b` field, not in a new trip
      // code: the on-wire TripReason enum is unchanged and still reports 10.
      trip(t_ms, TRIP_BMS_STALE, out, (int32_t)kBmsIds[i]);
      return;
    }
  }
  if (!have_t_chg_ || (uint32_t)(t_ms - t_chg_) > cfg_.chg_stale_ms) {
    trip(t_ms, TRIP_CHG_STALE, out);
    return;
  }

  if (state_ == S_MONITOR) {
    if (vcu_mode_ == MODE_LOW_POWER && vcu_flow_) {
      evaluateHold(t_ms, false);
      if (burst_left_ > 0) emitRepeat(t_ms, out);
    }
    return;
  }

  if (state_ == S_HOLD) {
    // Spec 5.2: the hold steps on the VCU's page-01 frames, not on the
    // clock, so once the section 6 trips above have been checked there is
    // nothing periodic left to do. The release has already happened and
    // HOLD is not re-evaluated.
    //
    // THE 6 H CAP BELOW IS THE OVERRIDE'S, and the hold has no time limit
    // at all (user, 2026-10-09). Section 6's row now says so in as many
    // words -- "override longer than 6 hours (OVERRIDE only; the hold has
    // no time limit, 5.2)" -- and 5.2 repeats it. Tripping the hold on a
    // timer would hand the pack back to the VCU's own hold, which is the
    // thing 5.2 exists to replace.
    return;
  }

  // OVERRIDE
  if ((uint32_t)(t_ms - t_enter_) > cfg_.override_max_ms) {
    trip(t_ms, TRIP_OVERRIDE_CAP, out);
    return;
  }
  if (isGenuinelyFull()) {
    if (!have_t_full_) {
      have_t_full_ = true;
      t_full_since_ = t_ms;
    } else if ((uint32_t)(t_ms - t_full_since_) >= cfg_.full_debounce_ms) {
      // c = vmin, so the release can be judged on the pack's spread
      // at the moment it happened (spec 9, C2).
      log(t_ms, EV_TOP_OF_CHARGE, vmax_, chg_max_, vmin_);
      // Spec 5.2 (user, 2026-10-08): the release stops rewriting page 00
      // and repeats the VCU's standing Low Power command once, so the
      // charger -- which only ever heard our rewrite of it -- drops into
      // Low Power, state 12. THE HOLD AFTER IT IS OURS: the VCU holds at 0
      // or 2.9 A, and on the parts truck, whose loads need ~2.9-3.0 A, that
      // drains the pack. So the core enters HOLD and replaces the page-01
      // setpoint from here on, changing nothing else.
      //
      // It starts from our last override setpoint, which is what the
      // charger already has, so the entry is not a step.
      hold_ca_ = ourIlimCounts() * 5;
      hold_valid_ = true;
      t_hold_step_ = t_ms;
      have_t_hold_step_ = true;
      gotoState(t_ms, S_HOLD, EV_RELEASE, hold_ca_);
      repeatMaster(t_ms, vcu_mode_);
      emitRepeat(t_ms, out);
    }
  } else {
    have_t_full_ = false;
  }
}

// --------------------------------------------------------------------------
// diagnostics (spec 8.2) -- byte-for-byte what machine.py diag_frames() packs
// --------------------------------------------------------------------------

static uint8_t clamp8(int32_t v) {
  return (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));
}
static uint16_t clamp16(int32_t v) {
  return (uint16_t)(v < 0 ? 0 : (v > 65535 ? 65535 : v));
}
static void put16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xFF);
  p[1] = (uint8_t)((v >> 8) & 0xFF);
}
static void put32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xFF);
  p[1] = (uint8_t)((v >> 8) & 0xFF);
  p[2] = (uint8_t)((v >> 16) & 0xFF);
  p[3] = (uint8_t)((v >> 24) & 0xFF);
}

uint8_t InterposerCore::ourIlimX10() const {
  if (state_ != S_OVERRIDE && state_ != S_HOLD) return 255;
  const int32_t x = (commandedIlimCounts() * 5 + 5) / 10;
  return x > 254 ? 254 : clamp8(x);
}

void InterposerCore::diagFrames(uint32_t t_ms, uint8_t rx_overflow,
                                uint8_t tx_fail, uint32_t uptime_ms,
                                bool bridge_ok, bool serial, uint32_t build_id,
                                uint8_t frames[4][8]) const {
  const int32_t cap = evseCapCa();
  uint8_t flags = 0;
  if (have_bms_) flags |= 1;
  if (chg_seen_charging_) flags |= 2;
  if (mainc_closed_) flags |= 4;
  if (cap >= 0) flags |= 8;
  if (vcu_flow_) flags |= 16;
  // Spec 8.2 / schema 4 (user, 2026-10-09): bit 5 is 1 "while the core is
  // rewriting the VCU's commands: OVERRIDE, pages 00/01/03, and HOLD, page
  // 01". It read 0 in HOLD, where the core is rewriting page 01, which is
  // the one thing the bit is for.
  if (state_ == S_OVERRIDE || state_ == S_HOLD) flags |= 32;
  if (bridge_ok) flags |= 64;
  if (serial) flags |= 128;

  uint8_t* s = frames[0];
  s[0] = DIAG_SCHEMA_VER;
  s[1] = DIAG_FW_VER;
  s[2] = state_;
  s[3] = trip_reason_;
  s[4] = flags;
  s[5] = ourIlimX10();
  put16(s + 6, clamp16((int32_t)((uint32_t)(t_ms - t_enter_) / 1000u)));

  uint8_t* o = frames[1];
  put16(o, clamp16(vmax_));
  put16(o + 2, clamp16(chg_max_));
  o[4] = soc_half_;
  o[5] = chg_state_;
  if (cap < 0) {
    o[6] = 255;
  } else {
    const int32_t x = (cap + 5) / 10;
    o[6] = x > 254 ? 254 : clamp8(x);
  }
  // B7: low nibble = the VCU's mode, bit 7 = the evap flag (schema 2);
  // bit 6 = "charger silent while expected" (schema 3, spec 2.2). Bits 4
  // and 5 are reserved and sent as 0.
  o[7] = (uint8_t)((vcu_mode_ & 0x0Fu) | (evap_ ? 0x80u : 0u) |
                   (chargerSilentWhileExpected(t_ms) ? 0x40u : 0u));

  uint8_t* k = frames[2];
  put32(k, modified_);
  put16(k + 4, synth_ > 65535u ? 65535u : (uint16_t)synth_);
  k[6] = rx_overflow;
  k[7] = tx_fail;

  uint8_t* b = frames[3];
  put32(b, build_id);
  put16(b + 4, DIAG_FW_VER);
  b[6] = DIAG_SCHEMA_VER;
  const uint32_t mins = uptime_ms / 60000u;
  b[7] = mins > 255u ? 255u : (uint8_t)mins;
}

}  // namespace interposer
