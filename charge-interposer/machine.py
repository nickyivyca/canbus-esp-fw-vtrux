"""The interposer state machine. PURE -- no I/O, no floats, stdlib only.

This module is the thing that eventually runs on the micro on the interposer
board. Everything else in this folder exists to exercise it. Keep it that way:

  * no imports beyond the standard library (currently: none at all)
  * integer arithmetic only, in fixed units (mV, centi-amps, ms)
  * no allocation in the steady-state path beyond the small output list
  * no wall-clock reads -- time arrives as a parameter

Board topology
--------------
    A123 BMS ---+                                    +--- Bel charger
    VCU/HCU ----+--[ vehicle port ]== CORE ==[ charger port ]

The core sees every frame on both ports and returns the frames to forward. In
the normal case that is the input frame, unmodified, sent to the other side.

What it does
------------
Specified in projects/vtrux/notes/charge-interposer-spec.md, the source of
truth. In one line: when the VCU commands Low Power with the evap flag up and
the pack at or above the ceiling SoC, the core keeps the charge going under
the BMS's own permission, and goes transparent again once that permission
collapses -- the VCU's hold then reaches the charger exactly as it does at a
normal top of charge; every VCU stop and every BMS or charger fault is obeyed.
Section numbers in comments below refer to that spec. The core never
originates a frame on the charger side.
"""

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

CMD_ID = 0x18EFC000

BMS_STATUS = 0x410      # soc, ibat, epo, alarm, hvil, chg_done, mainc_stat
BMS_LIMITS = 0x420      # chg_max, dis_max, cell over/under-volt
BMS_DATA1 = 0x430       # cell vmax / vmin / tmax / tmin
BMS_DATA2 = 0x440       # mainc_stat (4-bit), balancing

# The BMS messages the core reads, in the order tick() tests them for
# staleness (spec 6, B-6). Both cores must walk them in this order so that a
# tick where several are stale at once picks the same one to report.
BMS_IDS = (BMS_STATUS, BMS_LIMITS, BMS_DATA1, BMS_DATA2)
VCM_EVAP = 0x649        # VCM evap-purge control: vcm_evap_active (spec 3)
CHG_STATUS = 0x18FFD4C0     # BINV_status, multiplexed
CHG_HV_STATUS = 0x18FFD7C0  # BELINV_hvBatteryAndCharger: pack V/I, AC V/I
CHG_INFO = 0x18FFD9C0       # BELINV_chargeInfo: maxAvailableChargingCurrent
CHG_PILOT = 0x18FFD8C0      # BELINV_chargePilotOnlineTime (spec 3, A3)

TO_VEHICLE = 0
TO_CHARGER = 1

# Diagnostics (spec 8; layout in notes/artifacts/interposer-firmware/
# interposer_diag_schema.md, decode DBC vtrux-interposer-diag.dbc). All of it
# goes to the VEHICLE side only.
DIAG_SA = 0xE1                 # J1939 source address for mirror frames; unused
                               # on this truck (in use: 00 20 40 47 B3 C0 FE)
DIAG_STATUS_ID = 0x7F4
DIAG_OBSERVED_ID = 0x7F5
DIAG_COUNTERS_ID = 0x7F6
DIAG_BUILD_ID = 0x7F7
# On-wire schema (spec 8.2). The decoder's guard: a DBC built for another
# version flags the mismatch rather than trusting the fields.
#   2  0x7F5 B7 = the VCU's mode in the low nibble, the evap flag in bit 7
#   3  0x7F5 B7 bit 6 = "charger silent while expected" (spec 2.2), and
#      0x7F6 B7 counts transmit failures toward the charger ONLY while the
#      VCU's flow bit is 1. The byte is in the same place and means a
#      different quantity, which is exactly what this guard is for.
DIAG_SCHEMA_VER = 3
# Firmware version (spec 8.2, B-2). The two cores must agree; check_port.py
# fails if they do not.
#   2  the section A behaviour, Python's value until 2026-10-03
#   3  0x7F7 B0-B3 changed meaning from fnv1a(GIT_SHA) -- which was always
#      fnv1a("unknown"), because -DGIT_SHA was never passed -- to the first
#      four bytes of the application ELF's SHA-256. The wire layout is
#      unchanged, so this byte is the only thing that tells the two apart.
#      C++ carried 3 while Python still read 2; that mismatch was B-2.
#   4  sections A and B: the per-message BMS staleness, no cell_undervolt
#      trip, 20 repeat frames from every path, the flow-gated charger
#      transmit-failure counter and the charger-silence flag.
DIAG_FW_VER = 4


def mirror_id(arb_id):
    """The 29-bit id with its source-address byte replaced by DIAG_SA."""
    return (arb_id & 0x1FFFFF00) | DIAG_SA

# states. 3 is reserved: it was RELEASING, the synthesised charger stop
# removed on 2026-09-19 (spec 5.2); the on-wire codes of the others are kept.
S_PASSTHROUGH = 0   # not charging (or not yet armed)
S_MONITOR = 1       # charging, watching for the VCU's Low Power command
S_OVERRIDE = 2      # VCU holds at the evap ceiling; we are driving the charge
S_TERMINATED = 4    # the VCU's hold reaches the charger; latched for the session
S_SAFE = 5          # a trip fired; transparent forwarding, latched

STATE_NAMES = {0: "PASSTHROUGH", 1: "MONITOR", 2: "OVERRIDE",
               4: "TERMINATED", 5: "SAFE"}

# page 00 cmd_Mode. CONFIRMED by two independent methods -- the EPRI app binary's
# literal pool and log correlation on this vehicle. Mode 3 is a LOW POWER mode,
# NOT a completion state: the charger caps itself at ~6 A in it and then
# follows the VCU's setpoint down to a ~2.4 A hold that lasts until the handle
# is pulled; 3 -> 1 restarts it at full current (spec 4, 7).
MODE_EXPORT = 0
MODE_CHARGER = 1
MODE_STANDBY = 2
MODE_LOW_POWER = 3
MODE_INVALID = 4

CHG_STATE_CHARGING = 12

# bcm_mainc_stat (0x440, 4-bit) values that mean the main contactors are closed
# and power is flowing. MEASURED, not assumed: across the whole of
# vtrux_charge_M1 and vtruxchargeafterexportchargetest-80pctcv the steady value
# while charging is 12 (8681 and 9180 points respectively), reached through the
# documented precharge ladder 0 -> 2 -> 5 -> 7 -> 8 -> 9 and leaving via 14 at
# shutdown. projects/vtrux/README.md documents 11 as "normal running", which is the DRIVING
# steady state; charging sits at 12. Both are accepted here so the same check
# works either way.
MAINC_CLOSED = (11, 12)


class Config(object):
    """Thresholds. Units: mV, centi-amps (cA = 0.01 A), ms, half-percent."""

    __slots__ = ("chgmax_full_ca", "soc_arm_half", "vmax_hard_mv",
                 "learned_ilim_min_ca", "burst_frames", "burst_period_ms",
                 "arm_delay_ms", "full_debounce_ms", "chg_state_debounce_ms",
                 "bms_stale_ms", "chg_stale_ms", "chg_silence_ms",
                 "override_max_ms",
                 "revert_aux_pages", "mask_charger_telemetry", "diag_mirror")

    def __init__(self):
        # --- where WE release, when we are the one driving the charge -------
        # Spec 5.1: release when the BMS's permission has collapsed -- the
        # VCU's own top-of-charge criterion (it commanded Low Power at 2.75 A
        # on vtrux_charge_M1 and at 0 A on vtrux_partstruck_charge). Modelled
        # at 3.0 A from those two captures. vmax is NOT a release trigger:
        # under real imbalance the BMS pins the leaders at 3593 mV while
        # still permitting 2.8 A, and a vmax threshold never fires
        # (evap-override-imbalanced, 2026-09-14).
        self.chgmax_full_ca = 300            # 3.00 A

        # --- the override gate (spec 3) --------------------------------------
        # A Low Power command is the evap ceiling only with the VCM's evap
        # flag set AND the BMS SoC at or above this (78 %, in bcm_soc's
        # half-percent counts). Observed ceilings: SoC 80.0, 80.0, 83.0 %.
        # Nothing about cell voltage or the BMS's permission takes part.
        self.soc_arm_half = 156

        # --- hard safety ---------------------------------------------------
        self.vmax_hard_mv = 3610             # no debounce, trips immediately

        # --- the VCU's learned CC command (spec 4) --------------------------
        # A page-01 setpoint counts as the VCU's CC command only from here
        # up: its real CC commands are 9.7-19.8 A, its hold setpoints are
        # 0-2.9 A (the 0/2.9 A dither, or 0 A at an above-ceiling start).
        # Without this, a 0.25 A hold setpoint was learned as "the CC
        # command" and capped the override at 0.2 A (bench, 2026-09-19).
        self.learned_ilim_min_ca = 500       # 5.00 A

        # --- repeating the VCU's standing page-00 command (spec 4.1) --------
        # The VCU sends page 00 only on change, in bursts of ~20 frames at
        # 50 ms. When the core changes its rewriting state between bursts it
        # repeats the VCU's standing command in the same shape.
        self.burst_frames = 20
        self.burst_period_ms = 50

        # The VCU's startup transient: in every session that had one, Low
        # Power arrived 2-3 s after the first CHARGER command and lasted
        # 63-72 s before the VCU went back to CHARGER by itself
        # (evap_gate_evidence.txt). Never override before the charge has been
        # established this long, so the transient is never touched; a hold
        # that is still there afterwards is decided then (spec 3).
        self.arm_delay_ms = 75000

        self.full_debounce_ms = 1000

        # The charger dips out of state 12 for a single frame during normal
        # operation -- measured on vtruxchargeafterexportchargetest-80pctcv,
        # where BELINV_state shows 12 -> 15 -> 12 blips of 0.00 s duration, one
        # of them at the 80 % cutover itself, inside two runs of state 12
        # lasting 7336 s and 1868 s. A real fault latches in 15; these do not.
        # Trip only once the charger has been out of state 12 this long.
        # Raised 2000 -> 5000 on 2026-09-30 (spec 6, A8a): across 71 sessions
        # the charger left 12 and came back 61 times with the charge going
        # on, never for longer than 1.2 s, while every departure over 2 s was
        # a session ending or a restart minutes later.
        self.chg_state_debounce_ms = 5000

        self.bms_stale_ms = 500
        self.chg_stale_ms = 500

        # Spec 6.1: no charger status frame for this long is a session
        # boundary -- the Bel unit has gone offline. It resets everything
        # EXCEPT SAFE, because the 500 ms staleness row above has already
        # tripped on the same silence and the latch silence set must not be
        # undone by that silence continuing.
        #
        # 20 s rests on an asymmetry, not on a measured maximum: a larger
        # value only delays recognising a session that has already ended,
        # while a smaller one risks resetting inside a live one. In fully
        # captured charges the 100 ms status frame never went quiet for 2 s
        # (artifacts/interposer-firmware/charger_silence_gaps.txt).
        self.chg_silence_ms = 20000
        self.override_max_ms = 6 * 3600 * 1000   # spec 6: 6 hours

        # Spec 4: hold pages 03.02 / 03.07 at their charging-time values
        # while overriding, so the charger sees a self-consistent picture.
        #
        # Decided, not assumed (review A6, 2026-09-30). 03.00 is forwarded
        # untouched -- it goes to 1 about 3 s after a session's first Low
        # Power and stays there until the handle pull, and the truck itself
        # has run mode CHARGER at full current with 03.00 = 1 twice: after
        # the startup transient of `charging45`, and through the whole
        # 6,582 s charge of `vtrux_partstruck_charge`. Holding 03.02 / 03.07
        # while forwarding 03.00 therefore hands the charger a picture it has
        # already seen in real charges at full current.
        #
        # One difference remains and is accepted: at a real top of charge
        # 03.02 / 03.07 flip to their "complete" values minutes to hours
        # after the Low Power (+256 s on `vtrux_charge_M1`, +5,705 s on
        # `vtrux_partstruck_charge`, +3 s at the evap ceiling), whereas at
        # our release the charger receives them together with it. The trigger
        # for the flip is not identified, so the delay is not reproduced.
        # Whether the charger reads these pages at all is still unknown
        # (spec 10); the point is that what it gets is a state the truck
        # itself produces.
        self.revert_aux_pages = True
        # STEALTH: additionally rewrite charger->vehicle current/state so the
        # VCU believes the charge stopped. Off by default -- only enable if the
        # bench shows the VCU misbehaves when it sees current after its cutoff.
        self.mask_charger_telemetry = False
        # Spec 8.1: rebroadcast every frame we modify on the vehicle side
        # under DIAG_SA, so a log shows what the charger got.
        self.diag_mirror = True


# ---------------------------------------------------------------------------
# frame field extraction -- hand-rolled to match the DBC bit layouts exactly.
# Cross-checked against cantools over random payloads by test_signals.py; that
# test is the reason it is safe to hand-roll here instead of decoding.
# ---------------------------------------------------------------------------

def vmax_mv(d):
    """0x430 bcm_cell_vmax: 4|13@0+, 1 mV/count."""
    return ((d[0] & 0x1F) << 8) | d[1]


def vmin_mv(d):
    """0x430 bcm_cell_vmin: 20|13@0+, 1 mV/count."""
    return ((d[2] & 0x1F) << 8) | d[3]


def tmax_ddegc(d):
    """0x430 bcm_cell_tmax: 32|8@1+, 0.5 degC/count, offset -40."""
    return d[4] * 5 - 400


def tmin_ddegc(d):
    """0x430 bcm_cell_tmin: 40|8@1+, 0.5 degC/count, offset -40."""
    return d[5] * 5 - 400


def chg_max_ca(d):
    """0x420 bcm_chg_max: 11|12@0+, 0.25 A/count -> centi-amps."""
    return (((d[1] & 0x0F) << 8) | d[2]) * 25


def cell_overvolt(d):
    """0x420 bcm_cell_overvolt: bit 55."""
    return (d[6] >> 7) & 1


def cell_undervolt(d):
    """0x420 bcm_cell_undervolt: bit 54."""
    return (d[6] >> 6) & 1


def soc_half_pct(d):
    """0x410 bcm_soc: 24|8@1+, 0.5 %/count. Returned in half-percent counts."""
    return d[3]


def ibat_ca(d):
    """0x410 bcm_ibat: 39|16@0+, 0.025 A/count, offset -1000 -> centi-amps.

    Positive while charging.
    """
    return ((d[4] << 8) | d[5]) * 25 // 10 - 100000


def bms_epo(d):
    return (d[0] >> 1) & 1


def bms_hvil_mon(d):
    return (d[0] >> 3) & 1


def bms_alarm(d):
    """0x410 bcm_alarm: 14|2@1+ -> 0 NONE, 1 TYPE1, 2 TYPE2, 3 TYPE3."""
    return (d[1] >> 6) & 3


def bms_chg_done(d):
    return (d[6] >> 4) & 1


def mainc_stat_4(d):
    """0x440 bcm_mainc_stat: 2|4@1+. 11 = normal running."""
    return (d[0] >> 2) & 0x0F


def evap_active(d):
    """0x649 vcm_evap_active: 39|1@1+ (B4 bit 7). 1 = purge pending or purging.

    Broadcast and set for the whole of every 80 % ceiling charge, 0 in every
    other charge session (evap_gate_evidence.txt, 19 Low Power commands).
    """
    return (d[4] >> 7) & 1


def max_avail_ca(d):
    """0x18FFD9C0 BELINV_maxAvailableChargingCurrent: 0|16@1-, 0.05 A/count.

    The charger's own pilot-derived DC current ceiling. The charger owns the
    J1772 inlet and measures the pilot itself (it broadcasts pilotDuty on
    0x18FFD8C0), so this is the authoritative EVSE limit on the bus.
    """
    v = (d[1] << 8) | d[0]
    if v & 0x8000:
        v -= 1 << 16
    return v * 5


def pilot_online_min(d):
    """0x18FFD8C0 BELINV_chargePilotOnlineTime: 40|24@1+, MINUTES (spec 3).

    Reads 0 when the Bel unit's frames begin, counts minutes of pilot, and
    returns to 0 at every handle pull. Layout is single-source
    (`BelInverter-v2.dbc`); the 72-session behavioural evidence was decoded
    through that layout, so it is consistent with it rather than an
    independent confirmation of it.
    """
    return d[5] | (d[6] << 8) | (d[7] << 16)


def chg_mux(d):
    return d[0]


def chg_state(d):
    """0x18FFD4C0 mux 0: BELINV_state at 40|8@1+."""
    return d[5]


def chg_vehicle_connected(d):
    """0x18FFD4C0 mux 0: BELINV_vehicleConnected at 12|1@1+ -> d[1] bit 4."""
    return (d[1] >> 4) & 1


def chg_fault_bits(d):
    """0x18FFD4C0 mux 3 -> (inverterFault, buckBoostFault, hvOverVolt, overTemp)."""
    return (d[2] & 1, (d[2] >> 1) & 1, (d[1] >> 4) & 1, (d[1] >> 6) & 1)


# --- 0x18EFC000 pages ------------------------------------------------------

def page_key(d):
    return (0x03, d[1]) if d[0] == 0x03 else (d[0],)


def master_mode(d):
    return d[2]


def master_flow(d):
    return d[1]


def enc_master(flow, mode):
    return bytes((0x00, flow & 0xFF, mode & 0xFF, 0, 0, 0, 0, 0))


def setpoint_ilim_counts(d):
    return (d[4] << 8) | d[3]


def enc_setpoint_ilim(d, ilim_counts):
    """Rewrite page 01's Ilim, leaving Vlim exactly as the VCU sent it."""
    i = ilim_counts
    if i < 0:
        i = 0
    elif i > 0xFFFF:
        i = 0xFFFF
    return bytes((d[0], d[1], d[2], i & 0xFF, (i >> 8) & 0xFF, d[5], d[6], d[7]))


CONST_03_02_CHARGING = bytes((0x03, 0x02, 0x00, 0x00, 0, 0, 0, 0))
CONST_03_07_CHARGING = bytes((0x03, 0x07, 0x02, 0x64, 0x04, 0x32, 0, 0))


# ---------------------------------------------------------------------------
# the core
# ---------------------------------------------------------------------------

class InterposerCore(object):

    def __init__(self, cfg=None, now_ms=0):
        self.cfg = cfg or Config()
        self.state = S_PASSTHROUGH
        self.trip_reason = None
        self.events = []              # (t_ms, state, text) -- bench diagnostics

        # latest BMS / charger / VCM view
        self.vmax = 0
        self.vmin = 0
        self.tmax = 0
        self.tmin = 0
        self.chg_max = 0
        self.soc_half = 0
        self.chg_state = 0
        self.ibat = 0
        self.mainc_closed = False
        self.chg_bad_since = None
        self.max_avail = -1          # charger's pilot-derived cap, cA (-1 = unseen)
        self.have_bms = False
        self.chg_seen_charging = False   # charger has reached state 12 this session
        self.evap = 0                # vcm_evap_active, 0 until 0x649 is seen

        # freshness. Spec 6 (B-6): each BMS message the core reads is timed
        # SEPARATELY. A single timer refreshed by any BMS frame let the core
        # keep commanding 16 A on a permission frame 60 s old, with the
        # 3610 mV backstop blind, because 0x410 and 0x440 kept arriving
        # (review probe P5). Keyed by arbitration id; absent = never seen.
        self.t_bms = {}
        self.t_chg = None
        self.t_enter = now_ms
        self.t_full_since = None

        # learned from the VCU while it was charging normally
        self.learned_ilim = 0
        self.vcu_mode = MODE_EXPORT
        self.vcu_flow = 0

        # spec 3: the decision is re-evaluated on every Low Power frame while
        # armed; this remembers the last reason we were still waiting so the
        # log carries it once, not at 3 Hz.
        self._wait_reason = None

        # spec 4.1: the repeat of the VCU's standing page-00 command, emitted
        # by tick() at burst_period_ms; any page-00 frame from the VCU ends it.
        self._burst_left = 0
        self._burst_sent = 0
        self._burst_next_ms = 0
        self._burst_frame = None

        # spec 3 (A3): the Bel unit's pilot timer, in minutes. The FIRST
        # value read after power-up decides whether we may join the session
        # at all; a backwards step afterwards is a session boundary (6.1).
        self.pilot_min = None
        self.pilot_seen = False
        self.boot_locked = False

        # spec 6: BELINV_vehicleConnected, for the 1 -> 0 edge at a handle
        # pull. -1 = no mux-0 frame seen yet, so the first one cannot be an
        # edge however its byte 1 happens to read.
        self.chg_veh_conn = -1

        self.stats = {"fwd_v2c": 0, "fwd_c2v": 0, "modified": 0, "synth": 0}

    # -- helpers ------------------------------------------------------------
    MAX_EVENTS = 500

    def _log(self, t_ms, text):
        if len(self.events) < self.MAX_EVENTS:
            self.events.append((t_ms, STATE_NAMES[self.state], text))

    def _goto(self, t_ms, state, why):
        if state == self.state:
            return
        self.state = state
        self.t_enter = t_ms
        self._wait_reason = None
        self._log(t_ms, why)

    def _active(self):
        return self.state in (S_MONITOR, S_OVERRIDE)

    def _trip(self, t_ms, reason):
        """Bail to transparent forwarding and latch SAFE (spec 6).

        A trip is the same action as a release: we stop rewriting, and if we
        were overriding we repeat the VCU's standing command to the charger
        (spec 4.1) -- its Low Power hold, the truck's normal state at full.

        Only meaningful while we are armed. Outside MONITOR/OVERRIDE we are
        already a wire, and half of these conditions are NORMAL off-charge
        traffic -- EPO and the alarm bits fire during every ordinary charge
        shutdown, and HVIL is open before precharge. Tripping on those merely
        fills the log and latches SAFE before the session even starts. Callers
        must gate on _active(); this is the backstop.

        There is no teardown window (spec 6, changed 2026-09-30). A flow drop
        ends the override and leaves the core transparent, so the handle-pull
        signals that follow it are seen from TERMINATED, where _active() is
        already false and nothing can trip. Suppression is unnecessary once
        the state is right, and a suppression window that swallowed the
        3610 mV ceiling and staleness alongside the close-out signals was the
        defect this replaced (review P2).
        """
        out = []
        if not self._active():
            return out
        self.trip_reason = reason
        was_override = self.state == S_OVERRIDE
        self._goto(t_ms, S_SAFE, "TRIP: %s" % reason)
        if was_override:
            self._repeat_master(t_ms, self.vcu_mode)
            self._emit_repeat(t_ms, out)
        return out

    def _session_boundary(self, t_ms, why, clear_safe, forget_pilot=False):
        """Spec 6.1: reset everything this session learned.

        ONE path, used by all three boundaries, because the defect this
        replaced was the STAND_BY path clearing only some of it. The core
        cleared its session state only on STAND_BY/EXPORT, and 11 of the 49
        terminations in the survey do not end on STAND_BY -- so SAFE stayed
        latched into the next session and `chg_seen_charging` carried over,
        letting the next session arm before its own charger reached 12. The
        realistic case is a charge that fails at one handle and is moved to
        another: the second must start afresh.

        `clear_safe` is False for the charger-silence boundary alone: silence
        trips the core at 500 ms, and the same silence continuing must not
        undo the latch it set.

        `forget_pilot` is True for that same boundary and no other, and it
        is a SEPARATE flag rather than `not clear_safe` on purpose. The
        two say different things -- one is about the latch, one about the
        pilot timer -- and they coincide today only because the
        charger-silence boundary is the only one needing either. A fourth
        boundary wanting one and not the other would otherwise inherit the
        wrong behaviour silently.
        """
        if clear_safe:
            self.trip_reason = None
        # A boundary that does not move the state must still SAY so. The
        # earlier form logged only through _goto, so a clear_safe boundary
        # arriving in PASSTHROUGH -- which is the ordinary case for the pilot
        # timer, because the handle pull has already ended the session by the
        # time the timer returns to 0 -- reset chg_seen_charging, boot_locked,
        # max_avail and learned_ilim and left no trace of having done it.
        # Spec 6.1's most consequential boundary was the one you could not see.
        if self.state != S_PASSTHROUGH and (clear_safe
                                            or self.state != S_SAFE):
            self._goto(t_ms, S_PASSTHROUGH, why)
        elif not (self.events and self.events[-1][2] == why):
            # Collapse a contiguous run of the same boundary to one line.
            # The reset below stays level-triggered and fires on every frame,
            # because `_maybe_arm` does not look at the VCU's mode: it is the
            # repeated clearing of chg_seen_charging and mainc_closed that
            # stops the core arming while the VCU is commanding STAND_BY.
            # Only the LOG is an edge. The VCU sends its page-00 changes as a
            # 20-frame burst, so without this one session end reads as twenty.
            self._log(t_ms, why)

        self.t_full_since = None
        self.learned_ilim = 0
        self.chg_seen_charging = False
        self.mainc_closed = False
        self.chg_bad_since = None
        self.max_avail = -1
        self._wait_reason = None
        if self._burst_left > 0:
            self._end_repeat(t_ms, "abandoned on session reset")
        self._burst_left = 0
        self.boot_locked = False
        if forget_pilot:
            # Spec 6.1: "The charger-silence boundary also forgets the
            # last pilot-timer reading. The first reading after the
            # silence is not compared with the last one before it, so a
            # timer that comes back lower is not a step-back and does not
            # clear SAFE."
            #
            # Without this the core trips on the silence, the Bel unit
            # restarts with the handle still in, its timer resumes from a
            # lower minute count, and that reads as a replug and clears
            # the latch the silence set -- the exact outcome the 20 s row
            # above exists to prevent.
            #
            # `pilot_seen` is deliberately NOT reset. It gates arming
            # (spec 3, A3), not the comparison; clearing it would stop the
            # core re-arming until a new pilot frame arrived, which 6.1
            # does not ask for.
            self.pilot_min = None

    def _repeat_master(self, t_ms, mode):
        """Spec 4.1: repeat the VCU's standing page-00 command toward the
        charger, rewritten as the new state requires, in the VCU's own burst
        shape. tick() emits it; the flow bit is the VCU's own.

        Spec 4.1, 2026-10-05: "A repeat that starts while another is still
        being sent replaces it: the earlier one stops there, the frames it
        already sent stand, and only the new command continues." The end
        is logged BEFORE the new REPEAT line and before _burst_sent is
        cleared, so the pair reads in event order and carries the count
        that actually went. Mirrors machine.cpp's REPEAT_REPLACED.
        """
        if self._burst_left > 0:
            self._end_repeat(t_ms, "replaced by a new repeat")
        self._burst_frame = enc_master(self.vcu_flow, mode)
        self._burst_left = self.cfg.burst_frames
        self._burst_sent = 0
        self._burst_next_ms = t_ms
        self._log(t_ms, "REPEAT: the VCU's standing page-00 command to the "
                        "charger, flow %d mode %d, %d frames"
                  % (self.vcu_flow, mode, self.cfg.burst_frames))

    def _emit_repeat(self, t_ms, out):
        cfg = self.cfg
        while self._burst_left > 0 and t_ms >= self._burst_next_ms:
            out.append((TO_CHARGER, CMD_ID, True, self._burst_frame))
            if cfg.diag_mirror:
                out.append((TO_VEHICLE, mirror_id(CMD_ID), True, self._burst_frame))
            self.stats["synth"] += 1
            self._burst_sent += 1
            self._burst_left -= 1
            self._burst_next_ms += cfg.burst_period_ms
        # Report what WENT, not what was intended. EV_REPEAT announces the
        # burst before a frame has left; this says how it actually ended.
        # _emit_repeat is only called with _burst_left > 0, so this fires
        # exactly once per completed burst. Mirrors machine.cpp's
        # EV_REPEAT_END / REPEAT_COMPLETE.
        if self._burst_left == 0:
            self._end_repeat(t_ms, "complete")

    def _end_repeat(self, t_ms, why):
        """Spec 4.1: how the repeat ended, with the count that actually went.

        Added 2026-10-05. The REPEAT line states an INTENTION -- it is
        logged before any frame leaves -- and on `fault-during-override` it
        announced 20 while 13, 19 and 7 went out on three different runs of
        the same code. A log line that is read as an accomplishment has to
        report one.
        """
        self._log(t_ms, "REPEAT ended: %d of %d, %s"
                  % (self._burst_sent, self.cfg.burst_frames, why))

    def _maybe_arm(self, t_ms):
        """Arm on an OBSERVED charge, never on the VCU's command alone.

        Two reasons this is keyed to observation rather than to `00 01 01`:

        * The command leads reality by a long way. In
          vtruxchargeafterexportchargetest-80pctcv the VCU commands CHARGE at
          t=79.6 s while the contactors are still open (bcm_mainc_stat = 0) and
          precharge has not run. Arming there means immediately tripping on a
          contactor state that is perfectly normal for that moment.
        * The command may never be seen at all. vtrux_charge_M1's capture starts
          at 76 % SoC, already charging, and its first page-00 frame is the
          low-power command at t=3077 s. A board powered up mid-session is in the
          position.

        Observed means the charger in state 12 with the contactors closed.
        NOT pack current: a charge that starts above the ceiling never shows
        a net charging current -- the 83 % start (spec 7) drained the pack at
        2.7 A through its whole hold because the Low Power output did not
        cover the aux load -- and it must still arm.
        """
        # Spec 3 (A3): do not arm before the first pilot value has been read.
        #
        # `boot_locked` alone was not enough and the ordering is the whole
        # point. 0x18FFD4C0 is 10 Hz and 0x18FFD8C0 is 1 Hz (measured 10:1 in
        # every session: 3000/300, 1648/165, 482/48, 517/52), so on the truck
        # the charge is routinely observable before the first pilot frame
        # arrives. Arming first left the core in MONITOR, and a later pilot
        # value of 5 set the flag but changed nothing -- the next Low Power
        # was overridden anyway. Waiting for `pilot_seen` makes the first
        # value decide before there is anything to decide about.
        #
        # The cost is that a charger which never sends 0x18FFD8C0 is never
        # overridden. That errs toward the truck's normal charging, and no
        # session in the corpus has status frames without pilot frames.
        if (self.state == S_PASSTHROUGH and self.chg_seen_charging
                and self.mainc_closed and self.pilot_seen
                and not self.boot_locked):
            self._goto(t_ms, S_MONITOR,
                       "charge established (charger in state 12, contactors "
                       "closed, ibat %.1f A)" % (self.ibat / 100.0))

    def _is_genuinely_full(self):
        """Are WE done charging? Spec 5.1: the BMS's permission has collapsed.

        Only chg_max. A vmax threshold is unreachable under real imbalance
        (the BMS pins the leaders below it while still permitting current)
        and the truck never keys on one; the 3610 mV hard trip is the backstop.
        """
        return self.chg_max <= self.cfg.chgmax_full_ca

    def _evse_cap_ca(self):
        """The EVSE current ceiling we must not command past, in cA.

        J1772 compliance matters here and it is not automatic. Measured across
        two captures of the same truck on different EVSE settings:

            pilot 26 % (16 A EVSE): VCU commands  9.70 A, charger maxAvail  8 A,
                                    delivered 5.85 A
            pilot 52 % (32 A EVSE): VCU commands 18.80 A, charger maxAvail 16 A,
                                    delivered 14.70 A

        So BOTH ends are pilot-aware: the VCU scales its page-01 request with
        the EVSE rating (~1.2x maxAvail), and the charger clamps to its own
        measured pilot. The charger is the binding constraint, but relying on
        that alone would mean our override was only accidentally compliant.

        We therefore cap by the charger's own broadcast, which is the
        authoritative pilot-derived number, and by whatever the VCU was itself
        commanding -- when it commanded anything: a charge that starts above
        the ceiling carries a 0 A setpoint from the first frame (spec 7), so a
        learned command of 0 is "none", not a cap. The charger's value is used
        INSTANTANEOUSLY, not latched at its maximum: EVSEs with load
        management genuinely reduce the pilot mid-session, and following it
        down is the whole point.

        Returns -1 if we have no EVSE-derived limit at all, in which case we
        must not drive the charge.
        """
        caps = []
        if self.max_avail >= 0:
            caps.append(self.max_avail)
        if self.learned_ilim:
            caps.append(self.learned_ilim)
        return min(caps) if caps else -1

    def _our_ilim_counts(self):
        """Our current command while overriding, in page-01 counts (0.05 A).

        Spec 4: the BMS's permission, capped by the EVSE. No floor and no
        taper of our own: a voltage taper starved the last millivolts on the
        bench (2026-09-17), the BMS already tapers on the leader cell, and the
        2.4 A "aux floor" written as "never above the permission" was a no-op
        (2026-09-19).
        """
        cap = self._evse_cap_ca()
        if cap < 0:
            return 0
        ca = self.chg_max
        if cap < ca:
            ca = cap
        if ca < 0:
            ca = 0
        return ca // 5          # centi-amps -> 0.05 A counts

    # -- ingress ------------------------------------------------------------
    def on_vehicle_frame(self, arb_id, ext, data, t_ms):
        """A frame heard on the vehicle port. Returns frames to emit."""
        out = []
        d = data

        if arb_id == BMS_DATA1 and len(d) >= 6:
            self.vmax = vmax_mv(d)
            self.vmin = vmin_mv(d)
            self.tmax = tmax_ddegc(d)
            self.tmin = tmin_ddegc(d)
            self.t_bms[BMS_DATA1] = t_ms
            self.have_bms = True
            # Cell temperature is deliberately NOT a trip (spec 6): the BMS's
            # own response to a hot or cold pack -- a lower permission, or a
            # fault -- is what we obey, through the permission we command and
            # the fault rows below.
            if self._active() and self.vmax >= self.cfg.vmax_hard_mv:
                out.extend(self._trip(t_ms, "vmax %d mV over hard ceiling" % self.vmax))

        elif arb_id == BMS_LIMITS and len(d) >= 7:
            self.chg_max = chg_max_ca(d)
            self.t_bms[BMS_LIMITS] = t_ms
            if not self._active():
                pass
            elif cell_overvolt(d):
                out.extend(self._trip(t_ms, "BMS cell_overvolt"))
            # bcm_cell_undervolt is deliberately NOT a trip (spec 6, B-7a,
            # 2026-10-03). The core has no low-cell limit of its own: if a
            # cell is dangerously low the interposer must not get in the way
            # of the charger charging it. If the BMS judges the pack too low
            # to charge it says so through its permission and its faults, and
            # those are obeyed. The DECODER stays -- check_port.py and
            # test_signals.py verify it against the DBC -- only the trip goes.

        elif arb_id == BMS_STATUS and len(d) >= 7:
            self.soc_half = soc_half_pct(d)
            self.ibat = ibat_ca(d)
            self.t_bms[BMS_STATUS] = t_ms
            self._maybe_arm(t_ms)
            if not self._active():
                pass
            elif bms_epo(d):
                out.extend(self._trip(t_ms, "BMS EPO"))
            elif bms_alarm(d) >= 3:
                # TYPE3 only. TYPE2 is a persistent latched state this pack sits
                # in while charging and driving perfectly normally -- see
                # notes/specific-trucks/bcm-alarm-type2-investigation.md.
                # Tripping on TYPE2 would disable the board permanently on a
                # truck that has it latched, which is this truck.
                out.extend(self._trip(t_ms, "BMS alarm TYPE%d" % bms_alarm(d)))
            elif not bms_hvil_mon(d):
                out.extend(self._trip(t_ms, "HVIL open"))

        elif arb_id == BMS_DATA2 and len(d) >= 8:
            self.t_bms[BMS_DATA2] = t_ms
            ms = mainc_stat_4(d)
            self.mainc_closed = ms in MAINC_CLOSED
            self._maybe_arm(t_ms)
            if self._active() and not self.mainc_closed:
                out.extend(self._trip(
                    t_ms, "contactor state %d (not a closed state)" % ms))

        elif arb_id == VCM_EVAP and len(d) >= 5:
            self.evap = evap_active(d)

        elif arb_id == CMD_ID and len(d) >= 8:
            out.extend(self._on_command(d, t_ms))
            return out

        # everything that is not the command frame forwards verbatim
        out.append((TO_CHARGER, arb_id, ext, bytes(d)))
        self.stats["fwd_v2c"] += 1
        return out

    def _on_command(self, d, t_ms):
        """Handle a 0x18EFC000 frame -- the only frame we ever modify."""
        cfg = self.cfg
        key = page_key(d)
        emit = bytes(d)

        if key == (0x00,):
            prev_flow = self.vcu_flow
            self.vcu_flow = master_flow(d)
            mode = master_mode(d)
            self.vcu_mode = mode
            # spec 4.1: the VCU's own page 00 supersedes any repeat of it
            if self._burst_left > 0:
                self._end_repeat(t_ms, "superseded by the VCU's page 00")
            self._burst_left = 0
            if prev_flow and not self.vcu_flow and self._active():
                # Spec 6 (A5, changed 2026-09-30): a flow drop ENDS the
                # override. The core is transparent from here, so the
                # handle-pull signals that follow -- HVIL, EPO, contactors,
                # the charger leaving 12 -- arrive in TERMINATED and cannot
                # trip anything. This frame itself goes to the charger
                # unmodified (it falls through every rewrite branch below,
                # none of which match TERMINATED), so no repeat is needed.
                self._goto(t_ms, S_TERMINATED,
                           "VCU dropped the flow bit: override ended, "
                           "transparent")

            if (mode == MODE_CHARGER and self.state == S_TERMINATED
                    and self.vcu_flow):
                # Become eligible to arm again: the VCU really does go
                # low power -> charger mode after its startup transient
                # (vtrux_partstruck_charge, t=31 s then t=94 s). Actual arming
                # still waits for an observed charge in _maybe_arm. SAFE is
                # deliberately NOT re-armable -- a trip latches for the session.
                #
                # The flow bit is required. A handle pull drops flow on a
                # frame that still reads mode CHARGER, and without this the
                # core went TERMINATED on the flow drop and straight back to
                # PASSTHROUGH on the same frame -- then re-armed on the next
                # BMS frame and tripped on the HVIL of the teardown it had
                # just been told about.
                self.t_full_since = None
                self._goto(t_ms, S_PASSTHROUGH, "VCU commanded CHARGE again")
            elif mode == MODE_CHARGER and self.state == S_OVERRIDE:
                # Spec 6: defined, not expected -- the VCU has never left a
                # hold except at a handle pull. The override ends at once and
                # the next Low Power is decided afresh, behind the arm delay.
                self.t_full_since = None
                self._goto(t_ms, S_MONITOR,
                           "VCU commanded CHARGE during the override: "
                           "override ended, transparent")
            elif mode in (MODE_STANDBY, MODE_EXPORT):
                # Spec 6.1: a session boundary, obeyed from any state. Clears
                # SAFE -- this is the VCU's own session end.
                self._session_boundary(t_ms, "VCU mode %d, session over" % mode,
                                       clear_safe=True)
            elif mode == MODE_LOW_POWER and self.state == S_MONITOR:
                # spec 3: decided on every Low Power frame while armed
                self._evaluate_hold(t_ms, from_frame=True)
                if self.state == S_OVERRIDE:
                    emit = enc_master(self.vcu_flow, MODE_CHARGER)
                    self.stats["modified"] += 1
            elif mode == MODE_LOW_POWER and self.state == S_OVERRIDE:
                # the VCU keeps re-asserting low power; keep telling the charger otherwise
                emit = enc_master(self.vcu_flow, MODE_CHARGER)
                self.stats["modified"] += 1

        elif key == (0x01,) and self.state in (S_MONITOR, S_OVERRIDE):
            if self.state == S_MONITOR:
                # learn the VCU's own CC command while it is still charging
                # (hold-sized setpoints are not one: learned_ilim_min_ca)
                ca = setpoint_ilim_counts(d) * 5
                if ca >= cfg.learned_ilim_min_ca and ca > self.learned_ilim:
                    self.learned_ilim = ca
            else:
                emit = enc_setpoint_ilim(d, self._our_ilim_counts())
                self.stats["modified"] += 1

        elif self.state == S_OVERRIDE and cfg.revert_aux_pages:
            if key == (0x03, 0x02):
                emit = CONST_03_02_CHARGING
                self.stats["modified"] += 1
            elif key == (0x03, 0x07):
                emit = CONST_03_07_CHARGING
                self.stats["modified"] += 1

        self.stats["fwd_v2c"] += 1
        out = [(TO_CHARGER, CMD_ID, True, emit)]
        if self.cfg.diag_mirror and emit != bytes(d):
            # spec 8.1: the frame as the charger received it
            out.append((TO_VEHICLE, mirror_id(CMD_ID), True, emit))
        return out

    def _wait(self, t_ms, key, text):
        """Still MONITOR; log the reason once per change of reason (the text
        may carry the time, the key must not)."""
        if key != self._wait_reason:
            self._wait_reason = key
            self._log(t_ms, text)

    def _evaluate_hold(self, t_ms, from_frame=False):
        """The VCU is commanding Low Power and we are armed. The evap ceiling,
        or something to accept? (spec 3)

        Evaluated on every Low Power frame and on every tick while it lasts,
        never only on the edge into Low Power: the command may arrive before
        the charge is observed (the 83 % start: Low Power at 12 s), or before
        the board is powered. An override decided from a tick has no frame
        to rewrite, so the VCU's standing command is repeated as CHARGER
        (spec 4.1); one decided on a frame rewrites that frame.
        """
        c = self.cfg
        if self.state != S_MONITOR:
            return
        if not self.vcu_flow:
            # Spec 3 (A5): the decision is evaluated only while the flow bit
            # is 1, on BOTH paths. The tick path always checked this; the
            # frame path did not, so a first Low Power arriving with flow 0
            # was overridden and entered OVERRIDE mid-teardown (review P7).
            # The check lives here so the two paths cannot drift again.
            return
        if not self.have_bms:
            self._wait(t_ms, "bms", "LOW_POWER seen, no BMS data yet -- waiting")
            return
        if t_ms - self.t_enter < c.arm_delay_ms:
            # Spec 3 (A7): WAITING COMES FIRST. Nothing latches inside the
            # arm delay -- not an override, and not an accept either. This
            # check used to sit below the two accept branches, so a Low Power
            # inside the delay could latch TERMINATED for the whole session
            # on an SoC that the BMS can report 10-20 % off for ~60 s after
            # it starts. The command reaches the charger untouched; when the
            # delay ends the tick path decides on the standing command and
            # repeats it (4.1) if it overrides.
            self._wait(t_ms, "delay",
                       "LOW_POWER seen at soc=%.1f%% -- waiting out the arm "
                       "delay (%.1f s into the charge)"
                       % (self.soc_half / 2.0, (t_ms - self.t_enter) / 1000.0))
            return
        if not self.evap:
            self._goto(t_ms, S_TERMINATED,
                       "LOW_POWER accepted: evap flag clear (soc=%.1f%% "
                       "vmax=%d mV chg_max=%.2f A)"
                       % (self.soc_half / 2.0, self.vmax, self.chg_max / 100.0))
            return
        if self.soc_half < c.soc_arm_half:
            self._goto(t_ms, S_TERMINATED,
                       "LOW_POWER accepted: evap flag set but soc=%.1f%% is "
                       "below the %.1f%% gate" % (self.soc_half / 2.0,
                                                 c.soc_arm_half / 2.0))
            return
        if self._evse_cap_ca() < 0:
            self._wait(t_ms, "cap",
                       "LOW_POWER seen with the evap flag set at soc=%.1f%% -- "
                       "no EVSE current limit known yet, not driving"
                       % (self.soc_half / 2.0))
            return
        self._goto(t_ms, S_OVERRIDE,
                   "LOW_POWER overridden: evap flag set, soc=%.1f%% vmax=%d mV "
                   "chg_max=%.2f A"
                   % (self.soc_half / 2.0, self.vmax, self.chg_max / 100.0))
        if not from_frame:
            self._repeat_master(t_ms, MODE_CHARGER)

    def on_charger_frame(self, arb_id, ext, data, t_ms):
        out = []
        d = data
        if arb_id == CHG_INFO and len(d) >= 8:
            self.max_avail = max_avail_ca(d)
        elif arb_id == CHG_PILOT and len(d) >= 8:
            mins = pilot_online_min(d)
            if not self.pilot_seen:
                # Spec 3 (A3): the FIRST value after power-up decides. Page
                # 00 is sent only on change, so a core that boots mid-session
                # never hears the VCU's standing command and cannot know what
                # it is. 2 or more minutes means the session is already
                # running and we stay out of it.
                self.pilot_seen = True
                if mins >= 2:
                    self.boot_locked = True
                    self._log(t_ms,
                              "booted into a session already %d min old: "
                              "PASSTHROUGH until the next session boundary"
                              % mins)
            elif self.pilot_min is not None and mins < self.pilot_min:
                # Spec 6.1: it returns to 0 at every handle pull, so a step
                # backwards is a new plug-in. Clears SAFE.
                self._session_boundary(
                    t_ms, "pilot timer stepped back %d -> %d min: new session"
                    % (self.pilot_min, mins), clear_safe=True)
            self.pilot_min = mins
        elif arb_id == CHG_STATUS and len(d) >= 8:
            self.t_chg = t_ms
            if chg_mux(d) == 0:
                self.chg_state = chg_state(d)
                # Spec 6 (A5, 2026-10-03): the charger reports a handle pull
                # ~1 s BEFORE the VCU drops the flow bit, so this is what
                # actually ends a session at the plug. Treated exactly like a
                # flow drop: transparent from this frame, no repeat.
                #
                # It matters because HVIL opens AT the flow drop, and in 5 of
                # the 13 source-11 pulls in the corpus up to 20 ms before it
                # -- which tripped "HVIL open" during an override. Ending on
                # the charger's report puts the core in TERMINATED about a
                # second earlier, so the HVIL arrives where nothing can trip.
                #
                # `vehicleConnected` is an observed 1 -> 0 EDGE, not a
                # level, matching the spec's "going to 0" and the same
                # treatment the flow bit gets. A level test fires on any
                # mux-0 frame whose byte 1 happens to be zero, which is what
                # a frame built without that signal looks like -- it took
                # out most of the unit suite the moment it was tried. An
                # edge cannot fire on a frame that never carried the signal.
                #
                # `shutdownSource` 11 is NOT a trigger (decided
                # 2026-10-03). It is a latched "last stop" reason, not a
                # live report: on the original Bel unit it stayed at 11 with
                # the plug still in for 84-162 s, three times in the corpus
                # -- 209 frames of it in `charging45`, every one with the
                # flag up. Triggering on it there left the core transparent
                # for the rest of the session and swallowed the charger
                # fault at t=178138. Source 3, 4 and 14 were never triggers
                # and still reach the normal trip paths.
                vc = chg_vehicle_connected(d)
                unplugged = self.chg_veh_conn == 1 and vc == 0
                self.chg_veh_conn = vc
                #
                # This is an `elif` chain, NOT an early return. The frame that
                # carries the plug-out must still reach the vehicle like every
                # other (spec 2); returning here swallowed it, and the VCU
                # only learned of the plug-out from the next mux-0 frame ~0.4 s
                # later. The differential could not see it because both cores
                # had the same bug -- found by review probe Q4.
                if self._active() and unplugged:
                    self._goto(t_ms, S_TERMINATED,
                               "charger reports the plug out "
                               "(vehicleConnected 1 -> 0): transparent")
                    self.chg_bad_since = None
                elif self.chg_state == CHG_STATE_CHARGING:
                    self.chg_seen_charging = True
                    self.chg_bad_since = None
                elif self.chg_seen_charging and self._active():
                    # Only a departure FROM charging counts, and only a
                    # SUSTAINED one -- see chg_state_debounce_ms. The trip
                    # itself is raised in tick().
                    if self.chg_bad_since is None:
                        self.chg_bad_since = t_ms
                else:
                    self.chg_bad_since = None
            elif chg_mux(d) == 3:
                inv, bb, ov, ot = chg_fault_bits(d)
                if inv or bb or ov or ot:
                    out.extend(self._trip(
                        t_ms, "charger fault inv=%d bb=%d ov=%d ot=%d"
                        % (inv, bb, ov, ot)))
        emit = bytes(d)
        if (self.cfg.mask_charger_telemetry and self.state == S_OVERRIDE
                and arb_id == CHG_HV_STATUS and len(d) >= 8):
            # STEALTH. Zero the two current fields of BELINV_hvBatteryAndCharger
            # so the VCU does not see current flowing after it commanded a stop.
            # Voltages are left alone. UNVERIFIED and off by default -- we do
            # not know that the VCU objects, and lying to it is a bigger
            # intervention than the one we already make.
            emit = bytes((d[0], d[1], 0, 0, d[4], d[5], 0, 0))
            self.stats["modified"] += 1
        out.append((TO_VEHICLE, arb_id, ext, emit))
        self.stats["fwd_c2v"] += 1
        if self.cfg.diag_mirror and emit != bytes(d):
            # spec 8.1: the frame as received from the charger, since the
            # vehicle bus only sees the modified one
            out.append((TO_VEHICLE, mirror_id(arb_id), ext, bytes(d)))
        return out

    # -- periodic -----------------------------------------------------------
    def tick(self, t_ms):
        """Watchdogs, the release, the hold decision between frames, and the
        repeat burst of spec 4.1 -- the only frames the core transmits that
        the VCU did not just send.
        """
        out = []
        cfg = self.cfg

        if self._burst_left > 0:
            self._emit_repeat(t_ms, out)

        # Spec 6.1: the charger-silence boundary applies FROM ANY STATE, so
        # it is checked before the armed-only return below. It has to be:
        # the 500 ms staleness row trips the core to SAFE long before 20 s
        # elapses, and from SAFE the early return would skip this entirely.
        if (self.t_chg is not None
                and t_ms - self.t_chg >= cfg.chg_silence_ms):
            self._session_boundary(
                t_ms, "no charger status frame for %d ms: Bel unit offline"
                % (t_ms - self.t_chg), clear_safe=False,
                forget_pilot=True)
            self.t_chg = None      # one boundary per silence, not one a tick

        if self.state not in (S_MONITOR, S_OVERRIDE):
            return out

        # Every _trip() below must have its output COLLECTED (B-1). _trip()
        # emits the first frame of the spec 4.1 repeat itself and decrements
        # the burst counter for it, so discarding the return value loses that
        # frame while `synth` still reads 20: a tick-raised trip during an
        # override sent 19 frames where machine.cpp, which passes `out` by
        # reference into trip(), sent 20.
        if (self.chg_bad_since is not None
                and t_ms - self.chg_bad_since >= cfg.chg_state_debounce_ms):
            out.extend(self._trip(
                t_ms, "charger out of state 12 for %d ms (now %d)"
                % (t_ms - self.chg_bad_since, self.chg_state)))
            return out
        for bid in BMS_IDS:
            last = self.t_bms.get(bid)
            if last is None or t_ms - last > cfg.bms_stale_ms:
                # The id goes in the text, not in a new trip code: "BMS
                # frames stale" stays a substring so trip_reason_code() keeps
                # reporting 10 and the on-wire enum is unchanged.
                out.extend(self._trip(
                    t_ms, "BMS frames stale: 0x%03X" % bid))
                return out
        if self.t_chg is None or t_ms - self.t_chg > cfg.chg_stale_ms:
            out.extend(self._trip(t_ms, "charger frames stale"))
            return out

        if self.state == S_MONITOR:
            if self.vcu_mode == MODE_LOW_POWER and self.vcu_flow:
                self._evaluate_hold(t_ms, from_frame=False)
                if self._burst_left > 0:
                    self._emit_repeat(t_ms, out)
            return out

        # OVERRIDE
        if t_ms - self.t_enter > cfg.override_max_ms:
            out.extend(self._trip(t_ms, "override exceeded session cap"))
            return out
        if self._is_genuinely_full():
            if self.t_full_since is None:
                self.t_full_since = t_ms
            elif t_ms - self.t_full_since >= cfg.full_debounce_ms:
                # vmin rides along so the release can be judged on the
                # pack's SPREAD at the moment it happened (spec 9, C2). The
                # truck reports one clamped SoC, so "terminated below full"
                # is only visible as the spread and the weakest cell.
                self._log(t_ms, "top of charge reached: vmax=%d mV "
                                "vmin=%d mV chg_max=%.2f A"
                          % (self.vmax, self.vmin, self.chg_max / 100.0))
                # spec 5.2: release is transparency, plus one repeat of the
                # VCU's standing Low Power command so the charger, which
                # only ever heard our rewrite of it, enters the hold.
                self._goto(t_ms, S_TERMINATED,
                           "RELEASE: transparent -- the VCU's Low Power hold "
                           "now reaches the charger")
                self._repeat_master(t_ms, self.vcu_mode)
                self._emit_repeat(t_ms, out)
        else:
            self.t_full_since = None
        return out

    # -- diagnostics (spec 8.2) ----------------------------------------------
    def chg_silent_while_expected(self, t_ms):
        """Spec 2.2: the VCU's flow bit is 1 and no charger status frame
        (0x18FFD4C0) has arrived for the charger-staleness threshold.

        The flow bit is what makes this mean something. It is the VCU's own
        command, set only while a charge or an export is running (all 52 flow
        drops in the corpus were session endings), so it does not depend on
        the charger being judged -- which is the whole point when the thing
        being judged is whether the charger is there at all. While driving,
        the Bel unit is silent for hours and this reads 0.

        A board that boots mid-session does not know the flow bit until the
        VCU's next page 00, and `vcu_flow` is 0 until then, so it reports
        nothing rather than guessing. Never having heard the charger counts
        as silence, the same convention the staleness trip uses.
        """
        if not self.vcu_flow:
            return False
        return (self.t_chg is None
                or t_ms - self.t_chg > self.cfg.chg_stale_ms)

    def diag_frames(self, t_ms, rx_overflow=0, tx_fail=0, uptime_ms=0,
                    bridge_ok=True, serial=False, build_id=0):
        """The four status frames, as (arb_id, data). Pure packing; the I/O
        layer supplies what only it knows (overflow, tx failures, uptime,
        bridge health, serial attached, build hash) and sends them on the
        vehicle side at ~1 Hz plus one STATUS on every state change. Layout:
        interposer_diag_schema.md, kept in lockstep via DIAG_SCHEMA_VER."""
        def u8(v):
            return 0 if v < 0 else (255 if v > 255 else int(v))

        def u16(v):
            return 0 if v < 0 else (65535 if v > 65535 else int(v))

        def le16(v):
            v = u16(v)
            return (v & 0xFF, (v >> 8) & 0xFF)

        def le32(v):
            v = 0 if v < 0 else (0xFFFFFFFF if v > 0xFFFFFFFF else int(v))
            return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF)

        cap = self._evse_cap_ca()
        flags = ((1 if self.have_bms else 0)
                 | (2 if self.chg_seen_charging else 0)
                 | (4 if self.mainc_closed else 0)
                 | (8 if cap >= 0 else 0)
                 | (16 if self.vcu_flow else 0)
                 | (32 if self.state == S_OVERRIDE else 0)
                 | (64 if bridge_ok else 0)
                 | (128 if serial else 0))
        if self.state == S_OVERRIDE:
            ilim_x10 = u8((self._our_ilim_counts() * 5 + 5) // 10)
            if ilim_x10 > 254:
                ilim_x10 = 254
        else:
            ilim_x10 = 255
        t_in = le16((t_ms - self.t_enter) // 1000)
        status = bytes((DIAG_SCHEMA_VER, DIAG_FW_VER, self.state,
                        u8(self.trip_reason_code()), flags, ilim_x10,
                        t_in[0], t_in[1]))

        vmax = le16(self.vmax)
        cm = le16(self.chg_max)
        cap_x10 = 255 if cap < 0 else min(254, u8((cap + 5) // 10))
        # B7: low nibble = the VCU's mode, bit 7 = the evap flag (schema 2);
        # bit 6 = "charger silent while expected" (schema 3, spec 2.2).
        # Bits 4 and 5 are reserved and sent as 0.
        mode_evap = ((self.vcu_mode & 0x0F)
                     | (0x80 if self.evap else 0)
                     | (0x40 if self.chg_silent_while_expected(t_ms) else 0))
        observed = bytes((vmax[0], vmax[1], cm[0], cm[1], u8(self.soc_half),
                          u8(self.chg_state), cap_x10, mode_evap))

        mod = le32(self.stats["modified"])
        syn = le16(self.stats["synth"])
        counters = bytes((mod[0], mod[1], mod[2], mod[3], syn[0], syn[1],
                          u8(rx_overflow), u8(tx_fail)))

        gh = le32(build_id)
        fw = le16(DIAG_FW_VER)
        build = bytes((gh[0], gh[1], gh[2], gh[3], fw[0], fw[1],
                       DIAG_SCHEMA_VER, u8(uptime_ms // 60000)))
        return [(DIAG_STATUS_ID, status), (DIAG_OBSERVED_ID, observed),
                (DIAG_COUNTERS_ID, counters), (DIAG_BUILD_ID, build)]

    def trip_reason_code(self):
        """The trip reason as the small integer the board reports (its
        TripReason enum); the Python core keeps the text, so map it."""
        r = self.trip_reason or ""
        if not r:
            return 0
        for code, key in enumerate(TRIP_KEYS, 1):
            if key in r:
                return code
        return 255

    def diag_line(self, t_ms, rx_overflow=0, tx_fail=0):
        """The serial `diag` line (spec 8.3), same content as the frames."""
        cap = self._evse_cap_ca()
        return ("diag st=%s trip=%d flags=0x%02x ilim=%s vmax=%d chgmax=%d "
                "soc=%.1f evap=%d chg=%d mod=%d synth=%d ovf=%d txf=%d "
                "silent=%d fw=%d schema=%d"
                % (STATE_NAMES[self.state], self.trip_reason_code(),
                   self.diag_frames(t_ms, rx_overflow, tx_fail)[0][1][4],
                   ("%.1f" % (self._our_ilim_counts() * 0.05)
                    if self.state == S_OVERRIDE else "-"),
                   self.vmax, self.chg_max, self.soc_half / 2.0, self.evap,
                   self.chg_state, self.stats["modified"], self.stats["synth"],
                   rx_overflow, tx_fail,
                   1 if self.chg_silent_while_expected(t_ms) else 0,
                   DIAG_FW_VER, DIAG_SCHEMA_VER))


# The board's TripReason enum (machine.h), in order, matched against the
# Python core's trip text so both report the same code on the wire.
# Schema 2: the cell-temperature codes were removed (spec 6).
TRIP_KEYS = ("over hard ceiling", "cell_overvolt", "cell_undervolt",
             "BMS EPO", "BMS alarm", "HVIL open", "contactor state",
             "charger fault", "charger out of state 12", "BMS frames stale",
             "charger frames stale", "override exceeded session cap")
