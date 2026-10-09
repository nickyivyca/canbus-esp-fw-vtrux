"""Unit tests for the interposer core's decisions. No CAN, no logs, no timing.

test_signals.py proves machine.py reads the bus correctly. This proves it
decides correctly, section by section of charge-interposer-spec.md. Both run
in seconds and neither needs hardware or captures, so they are the tests to
run after every edit; regress.py and run_scenario.py are the slow,
high-fidelity backstops.

Run:  py -3.12 projects/vtrux/tools/interposer/test_machine.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import machine as M
import protocol as P

FAILS = []


def ok(cond, name):
    if not cond:
        FAILS.append(name)


def f_649(evap):
    """0x649 with vcm_evap_active (B4 bit 7); the other bytes are noise the
    core must ignore."""
    d = bytearray((0x11, 0x22, 0x30, 0x44, 0x00, 0x66, 0x77, 0x88))
    if evap:
        d[4] |= 0x80
    return bytes(d)


def pilot(minutes):
    """0x18FFD8C0 with BELINV_chargePilotOnlineTime set (B5-B7, 24-bit LE)."""
    d = bytearray(8)
    d[5] = minutes & 0xFF
    d[6] = (minutes >> 8) & 0xFF
    d[7] = (minutes >> 16) & 0xFF
    return bytes(d)


class Bench(object):
    """Minimal driver: arms a core into a running charge, then pokes it."""

    def __init__(self, arm_delay_ms=0, **cfgkw):
        cfg = M.Config()
        cfg.arm_delay_ms = arm_delay_ms
        for k, v in cfgkw.items():
            setattr(cfg, k, v)
        self.core = M.InterposerCore(cfg, 0)
        self.t = 0
        self.out = []
        # Every step, in make_golden.py's trace format. These tests drive
        # machine.py only; recording what they drove lets the same sequence
        # be replayed through machine.cpp (host_diff/make_unit_traces.py), so
        # the C++ port is exercised on the paths they cover instead of only
        # on the paths some capture happens to reach.
        #
        # A Bench built with non-default config cannot be replayed -- the
        # trace carries no config and host_runner uses configDefaults() --
        # so make_unit_traces.py refuses those rather than emitting a trace
        # that would diverge for a reason that is not a port bug.
        self.trace = []
        self.cfg_overrides = dict(cfgkw)
        if arm_delay_ms != M.Config().arm_delay_ms:
            self.cfg_overrides["arm_delay_ms"] = arm_delay_ms

    def veh(self, fid, data):
        self.trace.append("V %d %X %d %s"
                          % (self.t, fid, 1 if fid > 0x7FF else 0,
                             bytes(data).hex()))
        self.out = self.core.on_vehicle_frame(fid, fid > 0x7FF, data, self.t)
        return self.out

    def chg(self, fid, data):
        self.trace.append("C %d %X 1 %s" % (self.t, fid, bytes(data).hex()))
        self.out = self.core.on_charger_frame(fid, True, data, self.t)
        return self.out

    def tick(self):
        self.trace.append("T %d" % self.t)
        self.out = self.core.tick(self.t)
        return self.out

    def charge_info(self, max_avail_a=16.0):
        return self.chg(0x18FFD9C0, P.encode(P.BEL, 0x18FFD9C0, {
            "BELINV_maxAvailableChargingCurrent": max_avail_a,
            "BELINV_vehicleState": 3}))

    def charger_status(self, state=12, shutdown_src=0, vehicle_connected=1,
                       evse_connected=1):
        # A real charger reports the plug as connected throughout a charge,
        # so the default says so. The handle-pull rule of spec 6 keys off
        # `shutdown_src` 11 or vehicle_connected going 1 -> 0.
        return self.chg(0x18FFD4C0, P.encode(
            P.BEL, 0x18FFD4C0, {"BELINV_state": state,
                                "BELINV_shutdownSource": shutdown_src,
                                "BELINV_vehicleConnected": vehicle_connected,
                                "BELINV_evseConnected": evse_connected},
            "BELINV_statusMultiplexer", 0))

    def charger_fault(self, **bits):
        vals = {"BELINV_inverterFault": 0, "BELINV_buckBoostFault": 0,
                "BELINV_hvBatteryOverVoltage": 0, "BELINV_overTemperature": 0}
        vals.update(bits)
        return self.chg(0x18FFD4C0, P.encode(
            P.BEL, 0x18FFD4C0, vals, "BELINV_statusMultiplexer", 3))

    def evap(self, flag=1):
        return self.veh(M.VCM_EVAP, f_649(flag))

    # The four BMS messages the core reads, one method each. Spec 6 (B-6)
    # times them separately, so a test has to be able to withhold exactly one
    # and keep the other three fresh; `bms()` sends all four, as the pack does.
    def bms_430(self, vmax=3.34, vmin=3.33, tmax=25.0):
        return self.veh(0x430, P.encode(P.EPRI, 0x430, {
            "bcm_cell_vmax": vmax, "bcm_cell_vmin": vmin,
            "bcm_cell_tmax": tmax, "bcm_cell_tmin": tmax - 2}))

    def bms_420(self, chg_max=300.0, overvolt=0, undervolt=0):
        return self.veh(0x420, P.encode(P.EPRI, 0x420, {
            "bcm_chg_max": chg_max, "bcm_dis_max": 612.0,
            "bcm_cell_overvolt": overvolt,
            "bcm_cell_undervolt": undervolt}))

    def bms_440(self, mainc=12):
        return self.veh(0x440, P.encode(P.EPRI, 0x440,
                                        {"bcm_mainc_stat": mainc}))

    def bms_410(self, soc=80.0, ibat=15.0, hvil=1, alarm=0, epo=0):
        return self.veh(0x410, P.encode(P.EPRI, 0x410, {
            "bcm_soc": soc, "bcm_ibat": ibat, "bcm_hvil_mon": hvil,
            "bcm_alarm": alarm, "bcm_epo": epo}))

    def bms(self, vmax=3.34, vmin=3.33, chg_max=300.0, soc=80.0, ibat=15.0,
            mainc=12, tmax=25.0, alarm=0, epo=0, hvil=1, overvolt=0,
            undervolt=0, skip=()):
        """All four BMS messages. `skip` withholds some by identifier."""
        if 0x430 not in skip:
            self.bms_430(vmax, vmin, tmax)
        if 0x420 not in skip:
            self.bms_420(chg_max, overvolt, undervolt)
        if 0x440 not in skip:
            self.bms_440(mainc)
        if 0x410 not in skip:
            self.bms_410(soc, ibat, hvil, alarm, epo)

    def arm(self, max_avail_a=16.0, evap=1, pilot_min=0, **kw):
        # The Bel unit sends 0x18FFD8C0 at 1 Hz alongside its 10 Hz status,
        # so a real session always has a pilot value before the charge is
        # observable for long. `pilot_min=None` omits it, for the tests that
        # are about the ordering itself.
        self.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))   # the VCU's own start
        if pilot_min is not None:
            self.chg(M.CHG_PILOT, pilot(pilot_min))
        self.charger_status(12)
        self.charge_info(max_avail_a)
        self.evap(evap)
        self.bms(**kw)
        return self.core.state

    def commanded_ilim(self, vcu_a=19.0):
        """Push a page-01 frame through and read back what we tell the charger."""
        sp = P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, int(round(vcu_a / P.UNIT)))
        out = self.veh(0x18EFC000, sp)
        data = [d for _s, f, _e, d in out if f == M.CMD_ID][0]
        return P.dec_setpoint(data)[1]

    def low_power(self):
        return self.veh(0x18EFC000, P.enc_master(1, P.MODE_LOW_POWER))

    def state(self):
        return M.STATE_NAMES[self.core.state]

    def hold_fresh(self, n, step_ms=100, resend_low_power=True, **bms):
        """Advance the clock n steps with both sides fresh, ticking each
        step; the VCU re-asserts Low Power each step unless told not to
        (the real VCU sends page 00 only on change). Returns the frames the
        core originated toward the charger from tick(): the repeat bursts of
        spec 4.1, as a list of payloads."""
        originated = []
        for _ in range(n):
            self.t += step_ms
            self.charger_status(12)
            self.bms(**bms)
            if resend_low_power:
                self.low_power()
            for o in self.tick():
                if o[0] == M.TO_CHARGER:
                    originated.append(o[3])
        return originated


def emitted_mode(out):
    for _side, fid, _ext, data in out:
        if fid == M.CMD_ID and data[0] == 0x00:
            return data[2]
    return None


# --- spec 3: arming and the override decision -------------------------------

def test_arms_on_state_12_and_contactors_not_on_current():
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))   # the Bel unit's 1 Hz pilot timer; the core will not arm before it
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    ok(b.state() == "PASSTHROUGH", "command alone must not arm")
    b.charge_info(16.0)
    b.charger_status(12)
    b.bms(mainc=0, ibat=0.0)
    ok(b.state() == "PASSTHROUGH", "open contactors must not arm")
    # the 83 % start: contactors closed, charger in 12, pack DRAINING
    b.bms(mainc=12, ibat=-2.7)
    ok(b.state() == "MONITOR", "state 12 + contactors closed arms without "
                               "a net charging current")


def test_overrides_the_evap_ceiling():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0, soc=80.0, evap=1)
    out = b.low_power()
    ok(b.state() == "OVERRIDE", "evap flag + soc 80 % is overridden")
    ok(emitted_mode(out) == P.MODE_CHARGER, "the Low Power reaches the charger as CHARGER")
    ok(any("LOW_POWER overridden" in e[2] for e in b.core.events), "logged as an override")


def test_evap_flag_clear_is_accepted_at_any_soc():
    for soc, vmax, chg_max, label in ((100.0, 3.594, 2.75, "genuine top"),
                                      (45.5, 3.282, 300.0, "transient at 45 %"),
                                      (85.5, 3.338, 285.5, "transient at 85 %")):
        b = Bench()
        b.arm(vmax=vmax, chg_max=chg_max, soc=soc, evap=0)
        out = b.low_power()
        ok(b.state() == "TERMINATED", "%s accepted" % label)
        ok(emitted_mode(out) == P.MODE_LOW_POWER, "%s forwarded unchanged" % label)
        ok(b.core.stats["modified"] == 0, "%s modifies nothing" % label)
        ok(any("evap flag clear" in e[2] for e in b.core.events),
           "%s: the reason is the flag" % label)


def test_no_evap_frame_ever_means_accept():
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))   # the Bel unit's 1 Hz pilot timer; the core will not arm before it
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    b.charger_status(12)
    b.charge_info(16.0)
    b.bms(soc=80.0)                    # armed, 0x649 never seen
    b.low_power()
    ok(b.state() == "TERMINATED", "without any 0x649 the flag reads 0: accept")


def test_evap_set_but_soc_below_gate_is_accepted():
    b = Bench()
    b.arm(soc=70.0, evap=1)
    b.low_power()
    ok(b.state() == "TERMINATED", "evap set at 70 % is not the ceiling")
    ok(any("below the" in e[2] for e in b.core.events), "reason names the SoC gate")


def test_soc_gate_edges():
    b = Bench()
    b.arm(soc=77.5, evap=1)
    b.low_power()
    ok(b.state() == "TERMINATED", "77.5 % is below the 78 % gate")
    b = Bench()
    b.arm(soc=78.0, evap=1)
    b.low_power()
    ok(b.state() == "OVERRIDE", "78.0 % is at the gate")
    b = Bench()
    b.arm(soc=83.0, evap=1)
    b.low_power()
    ok(b.state() == "OVERRIDE", "83.0 % (the above-ceiling start) is overridden")


def test_bms_numbers_play_no_part():
    """Spec 3: vmax and chg_max are not in the decision."""
    b = Bench()
    b.arm(vmax=3.594, chg_max=2.75, soc=100.0, evap=1)
    b.low_power()
    ok(b.state() == "OVERRIDE",
       "evap set at 100 % with the BMS tapered still overrides (release "
       "follows at once from chg_max, but the decision is the flag and SoC)")


def test_startup_transient_is_waited_out_not_touched():
    """Every observed transient: Low Power 2-3 s after CHARGER, back to
    CHARGER 63-72 s later. With the flag up and SoC above the gate it is
    indistinguishable from the ceiling, so the core waits out the 75 s delay
    and touches nothing meanwhile."""
    b = Bench(arm_delay_ms=75000)
    b.arm(vmax=3.324, chg_max=270.75, soc=83.0, evap=1)
    b.t = 3000
    out = b.low_power()
    ok(b.state() == "MONITOR", "a hold 3 s in is not decided yet")
    ok(emitted_mode(out) == P.MODE_LOW_POWER, "and passes through untouched")
    ok(any("waiting out the arm delay" in e[2] for e in b.core.events),
       "the wait is logged")
    n_before = len(b.core.events)
    for k in range(60):                     # 6 s of frames: no log spam
        b.t = 3100 + k * 100
        b.charger_status(12)
        b.bms(soc=83.0)
        b.low_power()
        b.tick()
    ok(len(b.core.events) == n_before, "the wait is logged once, not per frame")
    ok(b.core.stats["modified"] == 0, "nothing modified while waiting")
    # the transient ends: the VCU goes back to CHARGER
    b.t = 66000
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    ok(b.state() == "MONITOR", "CHARGER after the transient leaves us armed")
    ok(b.core.stats["modified"] == 0, "the transient was never touched")


def test_hold_past_the_delay_is_overridden_at_the_delay():
    """The 83 % start where the VCU keeps holding: overridden at 75 s, from
    a tick if no frame happens to land there."""
    b = Bench(arm_delay_ms=75000)
    b.arm(vmax=3.324, chg_max=270.75, soc=83.0, evap=1)
    b.t = 3000
    b.low_power()
    ok(b.state() == "MONITOR", "waiting")
    for k in range(1, 800):
        b.t = 3000 + k * 100
        b.charger_status(12)
        b.bms(soc=83.0)
        if k % 3 == 1:              # never on the 75 s boundary (k = 720)
            b.low_power()
        b.tick()
        if b.core.state == M.S_OVERRIDE:
            break
    ok(b.state() == "OVERRIDE", "overridden once the delay has elapsed")
    ok(74900 <= b.t <= 75300, "at the delay (%d ms), not later" % b.t)
    # spec 4.1: decided from a tick, so the VCU's standing Low Power is
    # repeated to the charger as CHARGER, 20 frames at 50 ms, each mirrored
    sent = [o for o in b.out if o[0] == M.TO_CHARGER]
    mirrored = [o for o in b.out if o[1] == M.mirror_id(M.CMD_ID)]
    ok(len(sent) >= 1 and sent[0][3] == P.enc_master(1, P.MODE_CHARGER),
       "the repeat starts on the deciding tick, flow 1 mode CHARGER")
    ok(len(mirrored) == len(sent), "each repeated frame is mirrored")
    frames = b.hold_fresh(12, step_ms=100, resend_low_power=False, soc=83.0)
    ok(len(sent) + len(frames) == b.core.cfg.burst_frames,
       "%d frames in the burst" % b.core.cfg.burst_frames)
    ok(all(f == P.enc_master(1, P.MODE_CHARGER) for f in frames),
       "every repeated frame is the VCU's command rewritten to CHARGER")
    ok(b.core.stats["synth"] == b.core.cfg.burst_frames, "counted as synth")
    out = b.low_power()
    ok(emitted_mode(out) == P.MODE_CHARGER, "a later hold frame is rewritten")
    # "the VCU's own page 00 ends any repeat" used to be asserted here, as
    # `_burst_left == 0`. It could not fail: hold_fresh() above has already
    # drained all 20 frames, so the burst was over before the frame that is
    # supposed to end it arrived. The real test is below.


def test_a_vcu_page_00_cuts_a_repeat_burst_short():
    """Spec 4.1: a page-00 frame from the VCU during a repeat ends it.

    The VCU's frame is the truth; our repeat exists only because there was
    no frame to rewrite. The burst has to be INTERRUPTED for this to mean
    anything -- asserting it after the burst has finished on its own is the
    vacuous form this replaced (C2).
    """
    b = Bench(arm_delay_ms=75000)
    b.arm(vmax=3.324, chg_max=270.75, soc=83.0, evap=1)
    b.t = 3000
    b.low_power()
    for k in range(1, 800):
        b.t = 3000 + k * 100
        b.charger_status(12)
        b.bms(soc=83.0)
        b.tick()
        if b.core.state == M.S_OVERRIDE:
            break
    ok(b.state() == "OVERRIDE", "overridden from a tick, so a repeat started")

    # Two ticks at 100 ms, two frames each: 1 on the deciding tick + 4.
    b.hold_fresh(2, step_ms=100, resend_low_power=False, soc=83.0)
    left = b.core._burst_left
    ok(0 < left < b.core.cfg.burst_frames,
       "the burst is still running (%d of %d frames left)"
       % (left, b.core.cfg.burst_frames))
    sent_before = b.core.stats["synth"]

    b.low_power()                       # the VCU speaks for itself
    ok(b.core._burst_left == 0,
       "the VCU's own page 00 ends the repeat (%d left)" % b.core._burst_left)

    after = b.hold_fresh(10, step_ms=100, resend_low_power=False, soc=83.0)
    ok(not after, "and nothing further is originated (%d frames)" % len(after))
    ok(b.core.stats["synth"] == sent_before,
       "`synth` stops too: %d, not %d"
       % (b.core.stats["synth"], b.core.cfg.burst_frames))
    ok(sent_before < b.core.cfg.burst_frames,
       "the burst really was cut short (%d of %d)"
       % (sent_before, b.core.cfg.burst_frames))


def test_decision_reached_when_low_power_precedes_the_observation():
    """Low Power arrives before the charger is in 12; arming comes after,
    and the standing command must still be decided (no edge to catch)."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))   # the Bel unit's 1 Hz pilot timer; the core will not arm before it
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    b.charge_info(16.0)
    b.evap(1)
    b.bms(soc=83.0, mainc=12, ibat=-2.0)   # contactors closed, charger not yet 12
    b.low_power()
    ok(b.state() == "PASSTHROUGH", "not armed yet")
    b.charger_status(12)
    b.bms(soc=83.0, mainc=12, ibat=-2.0)
    ok(b.state() == "MONITOR", "armed after the command")
    out = b.tick()
    ok(b.state() == "OVERRIDE", "the standing Low Power is decided from a tick")
    ok(any(o[0] == M.TO_CHARGER and o[3] == P.enc_master(1, P.MODE_CHARGER)
           for o in out), "and the charger is told by a repeat (spec 4.1)")


def test_no_evse_limit_waits_then_overrides():
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))   # the Bel unit's 1 Hz pilot timer; the core will not arm before it
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    b.charger_status(12)
    b.evap(1)
    b.bms(soc=80.0)                       # armed, charge_info never seen
    b.low_power()
    ok(b.state() == "MONITOR", "no EVSE limit -> not driving, still armed")
    ok(any("no EVSE current limit" in e[2] for e in b.core.events), "and says why")
    b.charge_info(16.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "the cap arriving completes the decision")


# --- spec 4: during the override --------------------------------------------

def test_command_is_the_bms_permission_capped_by_the_evse():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.t = 5000
    b.charger_status(12)
    ok(abs(b.commanded_ilim() - 16.0) < 0.06,
       "with the BMS at 300 A we command the 16 A EVSE cap")
    b.bms(vmax=3.560, chg_max=5.0)
    ok(abs(b.commanded_ilim() - 5.0) < 0.06,
       "with the BMS at 5 A we command 5 A -- no taper of our own")
    b.bms(vmax=3.578, chg_max=3.3)
    ok(abs(b.commanded_ilim() - 3.3) < 0.06,
       "the 2026-09-17 stall point: 3.3 A permitted, 3.3 A commanded")
    b.bms(vmax=3.590, chg_max=2.0)
    ok(abs(b.commanded_ilim() - 2.0) < 0.06,
       "no floor: 2.0 A permitted, 2.0 A commanded")


def test_zero_setpoint_start_is_capped_by_the_pilot_alone():
    """The 83 % start: the VCU's setpoint is 0 A from the first frame, so
    there is no learned CC command; the pilot cap applies on its own."""
    b = Bench()
    b.arm(max_avail_a=16.0, soc=83.0)
    b.commanded_ilim(0.0)                 # the VCU's 0 A setpoint, in MONITOR
    b.low_power()
    ok(b.state() == "OVERRIDE", "overridden")
    i = b.commanded_ilim(0.0)
    ok(abs(i - 16.0) < 0.06, "commands the 16 A pilot cap, not 0 A (%.2f)" % i)


def test_hold_setpoints_are_not_learned_as_the_cc_command():
    """Spec 4: a 0.25 A (dithered hold) setpoint seen in MONITOR must not
    become the cap -- it capped an override at 0.2 A on the bench."""
    b = Bench()
    b.arm(max_avail_a=16.0, soc=83.0)
    b.commanded_ilim(0.25)
    b.commanded_ilim(2.9)
    b.low_power()
    i = b.commanded_ilim(0.25)
    ok(abs(i - 16.0) < 0.06, "hold-sized setpoints ignored: commands %.2f A" % i)
    b = Bench()
    b.arm(max_avail_a=16.0)
    b.commanded_ilim(9.7)
    b.low_power()
    i = b.commanded_ilim(9.7)
    ok(abs(i - 9.7) < 0.06, "a real 9.7 A CC command is learned (%.2f A)" % i)


def test_respects_j1772_limit_16a_evse():
    """Measured: a 26 % pilot (16 A EVSE) gives maxAvail 8 A and the VCU asks
    for 9.7 A. Our override must never command past the EVSE's ceiling."""
    b = Bench()
    b.arm(max_avail_a=8.0)
    b.commanded_ilim(9.7)
    b.low_power()
    ok(b.state() == "OVERRIDE", "override engaged on the 16 A EVSE")
    i = b.commanded_ilim(9.7)
    ok(i <= 8.0 + 1e-6, "commanded %.2f A must not exceed maxAvail 8.00 A" % i)
    ok(i > 5.0, "but still commands usefully more than the 2.4 A hold (%.2f A)" % i)


def test_respects_j1772_limit_32a_evse():
    b = Bench()
    b.arm(max_avail_a=16.0)
    b.commanded_ilim(18.8)
    b.low_power()
    i = b.commanded_ilim(18.8)
    ok(i <= 16.0 + 1e-6, "commanded %.2f A must not exceed maxAvail 16.00 A" % i)
    ok(i > 12.0, "uses most of the 32 A EVSE (%.2f A)" % i)


def test_follows_evse_load_management_down():
    b = Bench()
    b.arm(max_avail_a=16.0)
    b.commanded_ilim(18.8)
    b.low_power()
    hi = b.commanded_ilim(18.8)
    b.charge_info(6.0)                    # EVSE derates mid-charge
    lo = b.commanded_ilim(18.8)
    ok(hi > 12.0, "was commanding the full rate (%.2f A)" % hi)
    ok(lo <= 6.0 + 1e-6, "follows the derate down to %.2f A" % lo)


def test_ilim_rewrite_preserves_vlim():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.commanded_ilim(19.0)
    b.low_power()
    out = b.veh(0x18EFC000, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 48))
    data = [d for _s, f, _e, d in out if f == M.CMD_ID][0]
    v, i = P.dec_setpoint(data)
    ok(abs(v - 429.20) < 0.01, "Vlim is forwarded verbatim (%.2f)" % v)
    ok(i > 2.4, "Ilim is raised above the VCU's hold value (%.2f A)" % i)


# --- spec 5: release ---------------------------------------------------------

def test_release_hands_page_00_back_and_takes_the_setpoint():
    """Spec 5.2 (changed 2026-10-08): the release stops rewriting page 00 and
    repeats the VCU's standing Low Power once, so the charger drops into Low
    Power -- and the core enters HOLD, where the page-01 setpoint is OURS.

    It used to enter TERMINATED and hand the whole command back. That is the
    behaviour this test asserted until 2026-10-08, and it is what 5.2
    replaced: the VCU holds at 0 or 2.9 A, which on the parts truck drains
    the pack.
    """
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "override engaged")
    originated = b.hold_fresh(30, vmax=3.593, chg_max=2.75, ibat=0.3,
                              resend_low_power=False)
    ok(b.state() == "HOLD",
       "chg_max 2.75 A with vmax pinned at 3593 mV releases into HOLD")
    # spec 4.1 / 5.2: the only frames originated are the repeat of the
    # VCU's standing Low Power command, so the charger enters the hold
    ok(len(originated) == b.core.cfg.burst_frames,
       "the release repeats the VCU's command %d times (%d)"
       % (b.core.cfg.burst_frames, len(originated)))
    ok(all(f == P.enc_master(1, P.MODE_LOW_POWER) for f in originated),
       "every repeated frame is the VCU's own 00 01 03")
    ok(b.core.stats["synth"] == b.core.cfg.burst_frames, "counted as synth")
    ok(any("RELEASE" in e[2] for e in b.core.events), "the release is logged")
    ok(any("top of charge reached" in e[2] for e in b.core.events),
       "the top of charge is logged")
    # spec 5.2: page 00 -- and with it Low Power mode -- is the VCU's again
    out = b.low_power()
    ok(emitted_mode(out) == P.MODE_LOW_POWER,
       "after the release the VCU's 00 01 03 reaches the charger as-is")
    ok(b.out[0][3] == P.enc_master(1, P.MODE_LOW_POWER),
       "byte-exact, not merely the same mode")
    # spec 5.2: the page-01 setpoint is not
    n_mod = b.core.stats["modified"]
    i = b.commanded_ilim(19.0)
    ok(b.core.stats["modified"] == n_mod + 1,
       "every page-01 setpoint IS rewritten -- the hold is ours")
    ok(abs(i - b.core.hold_ca / 100.0) < 1e-6,
       "and what the charger is told is our hold setpoint, not the VCU's "
       "19 A (%.2f A)" % i)
    # spec 6: the VCU's own STAND_BY is obeyed and ends everything
    out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(b.state() == "PASSTHROUGH" and emitted_mode(out) == P.MODE_STANDBY,
       "the VCU's STAND_BY drops us to PASSTHROUGH, forwarded untouched")
    ok(b.core.hold_ca is None,
       "and the boundary forgets the hold setpoint (6.1)")


def test_no_release_while_bms_still_permits():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.hold_fresh(30, vmax=3.599, chg_max=20.0, ibat=5.0)
    ok(b.state() == "OVERRIDE",
       "vmax 3599 mV with the BMS still granting 20 A does NOT release")


def test_release_needs_the_debounce():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.hold_fresh(5, vmax=3.590, chg_max=2.0)     # 500 ms below 3 A
    ok(b.state() == "OVERRIDE", "500 ms below the threshold is not enough")
    b.hold_fresh(3, vmax=3.590, chg_max=20.0)    # permission back up
    b.hold_fresh(8, vmax=3.590, chg_max=2.0)
    ok(b.state() == "OVERRIDE", "the debounce restarts when permission returns")
    b.hold_fresh(5, vmax=3.590, chg_max=2.0)
    ok(b.state() == "HOLD", "1 s continuously below 3 A releases")


# --- spec 5.2: the hold after the release ------------------------------------

def _released(ibat=0.3, chg_max=2.75, max_avail_a=16.0):
    """A core in HOLD, entered the way 5.1 says: the BMS's permission at or
    below 3 A for the full debounce, with the leader cell pinned at 3593 mV
    so the old vmax trigger would never have fired."""
    b = Bench()
    b.arm(max_avail_a=max_avail_a, vmax=3.340, chg_max=300.0)
    b.low_power()
    b.hold_fresh(34, vmax=3.593, chg_max=chg_max, ibat=ibat,
                 resend_low_power=False)
    # Long enough for the release's own repeat burst (4.1) to finish: 20
    # frames at 50 ms, started ~1.1 s in. A test that read `synth` with the
    # burst still in flight was reading the release and calling it the trip.
    assert b.core._burst_left == 0, "the release burst is still in flight"
    assert b.core.state == M.S_HOLD, "the bench did not reach HOLD"
    return b


def _hold_frame(b, chg_max, ibat, dt_ms=400, vcu_a=19.0):
    """One page-01 frame dt_ms later, with the BMS refreshed first. Returns
    what the charger is told, in amps."""
    b.t += dt_ms
    b.bms(vmax=3.593, chg_max=chg_max, ibat=ibat)
    return b.commanded_ilim(vcu_a)


def test_the_hold_starts_from_our_own_last_setpoint():
    """Spec 5.2: "It starts from our last override setpoint, which is what
    the charger already has" -- so the entry into HOLD is not a step."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    before = b.commanded_ilim(19.0)
    b.hold_fresh(34, vmax=3.593, chg_max=2.75, ibat=0.3,
                 resend_low_power=False)
    ok(b.core.chg_max == 275,
       "the BMS field cannot express 2.80 A; 275 cA is what it sends (%d)"
       % b.core.chg_max)
    ok(b.state() == "HOLD", "released into HOLD")
    ok(before > 15.0,
       "we were commanding the full 16 A EVSE rate (%.2f A)" % before)
    ok(abs(b.core.hold_ca / 100.0 - 2.75) < 1e-6,
       "and the hold starts at the permission we were last commanding, "
       "2.75 A, not at that 16 A (%.2f A)" % (b.core.hold_ca / 100.0))


def test_the_hold_steps_down_above_the_permission():
    """Spec 5.2: 0.1 A down while bcm_ibat is above bcm_chg_max. ibat is the
    PACK current, which nets out the other loads, so this is the pack being
    pushed harder than the BMS permits."""
    b = _released(ibat=0.3, chg_max=2.75)
    start = b.core.hold_ca
    seen = [_hold_frame(b, 2.75, 5.0) for _ in range(6)]
    ok(abs(seen[0] - (start - 10) / 100.0) < 1e-6,
       "the first frame steps down 0.1 A (%.2f A)" % seen[0])
    ok(abs(seen[-1] - (start - 60) / 100.0) < 1e-6,
       "six frames, six steps, no faster and no larger (%.2f A)" % seen[-1])


def test_the_hold_steps_up_toward_the_permission():
    """Spec 5.2: 0.1 A up while bcm_ibat is more than 0.25 A below it. The
    setpoint can end up ABOVE the permission and that is the point -- the
    parts truck's loads take ~2.9-3.0 A before any of it reaches the pack."""
    b = _released(ibat=0.3, chg_max=2.75)
    start = b.core.hold_ca
    for _ in range(4):
        i = _hold_frame(b, 2.75, 0.3)
    ok(abs(i - (start + 40) / 100.0) < 1e-6,
       "0.3 A of pack current against a 2.75 A permission steps up (%.2f A)"
       % i)
    ok(b.core.hold_ca > 275,
       "past the permission itself, which is where the loads live")


def test_the_hold_rests_inside_the_band():
    """Spec 5.2: inside the 0.25 A band below the permission it holds.

    THIS IS WHAT STOPS IT OSCILLATING: ibat is never exactly chg_max, so a
    loop that stepped down above and up below with nothing between would
    step on every frame for the whole hold.
    """
    b = _released(ibat=0.3, chg_max=2.75)
    b.core.hold_ca = 275
    for _ in range(5):
        _hold_frame(b, 2.75, 2.7)
    ok(b.core.hold_ca == 275,
       "2.70 A of pack current against a 2.75 A permission does not step "
       "(%d cA)" % b.core.hold_ca)
    _hold_frame(b, 2.75, 2.5)
    ok(b.core.hold_ca == 275,
       "nor does exactly 0.25 A below it -- the band edge is inclusive "
       "(%d cA)" % b.core.hold_ca)
    _hold_frame(b, 2.75, 2.4)
    ok(b.core.hold_ca == 285,
       "0.35 A below it, past the edge, does (%d cA)" % b.core.hold_ca)


def test_the_hold_steps_no_faster_than_the_truck():
    """Spec 5.2: at most one step per 0.3 s, the truck's own fastest step
    cadence. The VCU's page-01 cadence is faster than that, so the limit is
    load-bearing rather than decorative."""
    b = _released(ibat=5.0, chg_max=2.75)
    _hold_frame(b, 2.75, 5.0)
    first = b.core.hold_ca
    for _ in range(2):
        _hold_frame(b, 2.75, 5.0, dt_ms=100)
    ok(b.core.hold_ca == first,
       "two more frames 0.1 s apart make no further step (%d cA)"
       % b.core.hold_ca)
    _hold_frame(b, 2.75, 5.0, dt_ms=100)
    ok(b.core.hold_ca == first - 10,
       "and the next step comes once 0.3 s has passed (%d cA)"
       % b.core.hold_ca)


def test_the_hold_stays_inside_the_evse_cap():
    """Spec 5.2: "It stays between 0 A and the EVSE cap of section 4", and
    the cap is instantaneous -- an EVSE with load management genuinely
    reduces the pilot mid-session."""
    b = _released(ibat=0.3, chg_max=2.75, max_avail_a=16.0)
    for _ in range(160):
        i = _hold_frame(b, 300.0, 0.3)
    ok(abs(i - 16.0) < 1e-6,
       "the hold climbs no further than maxAvail 16 A (%.2f A)" % i)
    b.t += 400
    b.charge_info(6.0)                    # the EVSE derates mid-hold
    i = _hold_frame(b, 300.0, 0.3)
    ok(abs(i - 6.0) < 1e-6,
       "and a derate brings the hold straight down with it, not one step "
       "per 0.3 s (%.2f A)" % i)


def test_the_hold_follows_the_permission_to_zero():
    """Spec 5.2: between 0 A and the cap -- "following it down to 0 A if it
    has not reached 0 A by the release"."""
    b = _released(ibat=5.0, chg_max=2.75)
    for _ in range(60):
        i = _hold_frame(b, 0.0, 5.0)
    ok(abs(i) < 1e-6, "the hold reaches 0 A (%.2f A)" % i)
    ok(b.core.hold_ca == 0,
       "and stops there rather than going negative (%d cA)" % b.core.hold_ca)


def test_the_hold_touches_only_the_page_01_setpoint():
    """Spec 5.2: it "changes nothing else: page 00 -- and with it Low Power
    mode -- and every other frame reach the charger as the VCU sends them".

    The 03.02 / 03.07 reverts of section 4 are part of the override and stop
    at the release with everything else.
    """
    b = _released()
    for data in (P.enc_master(1, P.MODE_LOW_POWER),
                 bytes((0x03, 0x02, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66)),
                 bytes((0x03, 0x07, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66)),
                 bytes((0x02, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06))):
        out = b.veh(0x18EFC000, data)
        sent = [d for s, f, _e, d in out
                if s == M.TO_CHARGER and f == M.CMD_ID]
        ok(sent == [data],
           "page %02X %02X reaches the charger byte-exact in HOLD"
           % (data[0], data[1]))
    ok(b.core.hold_ca is not None, "and the hold is still ours throughout")


def test_the_hold_rewrites_the_setpoint_and_mirrors_it():
    """Spec 5.2: "The interposer's mirror traffic (section 8) continues for
    the hold's page-01 frames and ends when the core is transparent"."""
    b = _released()
    asked = P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 380)
    out = b.veh(0x18EFC000, asked)
    mirrored = [d for _s, f, _e, d in out if f == M.mirror_id(M.CMD_ID)]
    sent = [d for s, f, _e, d in out
            if s == M.TO_CHARGER and f == M.CMD_ID]
    ok(len(mirrored) == 1 and mirrored == sent,
       "the page-01 frame is mirrored as the charger received it")
    ok(abs(P.dec_setpoint(sent[0])[0] - P.dec_setpoint(asked)[0]) < 1e-6,
       "and Vlim is forwarded verbatim -- only Ilim is ours")


def test_the_hold_ends_at_a_flow_drop():
    """Spec 6: the flow-drop row names HOLD. From TERMINATED the core is a
    wire and the handle-pull signals that follow cannot trip anything."""
    b = _released()
    out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER))
    ok(b.state() == "TERMINATED", "the flow drop ends the hold")
    ok(out[0][3] == P.enc_master(0, P.MODE_LOW_POWER),
       "and that frame itself reaches the charger unmodified (A5)")
    n_mod = b.core.stats["modified"]
    b.commanded_ilim(2.9)
    ok(b.core.stats["modified"] == n_mod,
       "the page-01 rewrite has stopped: the core is transparent")


def test_the_hold_ends_at_the_chargers_plug_out_report():
    """Spec 6: `vehicleConnected` 1 -> 0 while holding does what a flow drop
    does. The handle is out; the VCU's own flow drop follows ~1.4 s later."""
    b = _released()
    b.charger_status(12, vehicle_connected=0)
    ok(b.state() == "TERMINATED", "the plug-out report ends the hold")
    n_mod = b.core.stats["modified"]
    b.commanded_ilim(2.9)
    ok(b.core.stats["modified"] == n_mod,
       "and the setpoint is the VCU's again")


def test_a_trip_in_the_hold_latches_safe_and_repeats_nothing():
    """Spec 6: "the page-01 rewrite stopped at once if we were holding (5.2;
    page 00 is already the VCU's, so nothing is repeated)"."""
    b = _released()
    synth_before = b.core.stats["synth"]
    out = b.charger_fault(BELINV_inverterFault=1)
    ok(b.state() == "SAFE", "a charger fault in HOLD trips to SAFE")
    ok("inv=1" in (b.core.trip_reason or ""), "the reason is recorded")
    ok([o for o in out if o[0] == M.TO_CHARGER] == [],
       "and NOTHING is repeated toward the charger")
    b.hold_fresh(25, vmax=3.593, chg_max=2.75, ibat=0.3,
                 resend_low_power=False)
    ok(b.core.stats["synth"] == synth_before,
       "not on the following ticks either (%d frames)"
       % (b.core.stats["synth"] - synth_before))
    n_mod = b.core.stats["modified"]
    b.commanded_ilim(2.9)
    ok(b.core.stats["modified"] == n_mod, "the rewrite stopped at the trip")


def test_the_hold_still_watches_for_stale_bms_frames():
    """Spec 5.2: "The trips of section 6 apply throughout, including 0x410
    staleness, since the hold reads bcm_ibat from it."

    0x410 is the message that carries ibat, so this is the hold reading a
    number that has stopped arriving -- the case 5.2 calls out by name. The
    trips are raised on the clock, in tick(), which until 2026-10-08 returned
    early for every state but MONITOR and OVERRIDE.
    """
    b = _released()
    for _ in range(4):
        b.t += 100
        b.charger_status(12)
        b.bms(vmax=3.593, chg_max=2.75, ibat=0.3, skip=(0x410,))
        b.tick()
    ok(b.state() == "HOLD", "400 ms without 0x410 is inside the threshold")
    b.t += 200
    b.charger_status(12)
    b.bms(vmax=3.593, chg_max=2.75, ibat=0.3, skip=(0x410,))
    b.tick()
    ok(b.state() == "SAFE", "past 500 ms it trips")
    ok("0x410" in (b.core.trip_reason or ""),
       "naming the message that went stale (%s)" % b.core.trip_reason)


def test_the_hold_still_watches_the_hard_ceiling():
    """Spec 6: the 3610 mV backstop has no debounce and no state condition.
    It is the last defence while we are the one commanding current."""
    b = _released()
    b.t += 100
    b.bms_430(vmax=3.611)
    ok(b.state() == "SAFE", "3611 mV in HOLD trips at once")
    ok("hard ceiling" in (b.core.trip_reason or ""),
       "with the ceiling named (%s)" % b.core.trip_reason)


def test_the_hold_reports_its_own_setpoint_in_the_diagnostics():
    """Spec 8.2: the status frame carries OUR COMMANDED CURRENT. In HOLD that
    is the hold setpoint, not the BMS permission capped by the EVSE -- the
    two differ by up to a step, and by much more just after the entry."""
    b = _released()
    b.core.hold_ca = 150
    status = dict(b.core.diag_frames(b.t, 0, 0))[M.DIAG_STATUS_ID]
    ok(status[2] == M.S_HOLD and M.STATE_NAMES[status[2]] == "HOLD",
       "the status frame reports state %d, HOLD" % status[2])
    ok(status[5] == 15,
       "and 1.50 A as 15 in the 0.1 A field (%d)" % status[5])
    ok((status[0], status[1]) == (4, 5),
       "under diag schema 4 / fw 5, which is what the new state code and "
       "this field's new meaning are published as (%d/%d)"
       % (status[0], status[1]))
    line = b.core.diag_line(b.t, 0, 0)
    ok("HOLD" in line, "the human diag line says HOLD too")
    ok("1.5" in line, "and carries the same setpoint (%s)" % line)


# --- spec 6: trips and the vehicle's commands --------------------------------

def test_trip_during_override_goes_transparent_and_latches_safe():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.t = 5000
    b.charger_status(12)
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_fault(BELINV_inverterFault=1)
    ok(b.state() == "SAFE", "a trip during override goes straight to SAFE")
    ok("inv=1" in (b.core.trip_reason or ""), "trip reason records the fault")
    first = [o for o in b.out if o[0] == M.TO_CHARGER]
    ok(len(first) == 1 and first[0][3] == P.enc_master(1, P.MODE_LOW_POWER),
       "the trip repeats the VCU's standing Low Power at once")
    originated = b.hold_fresh(20, vmax=3.340, chg_max=300.0,
                              resend_low_power=False)
    ok(len(first) + len(originated) == b.core.cfg.burst_frames
       and all(f == P.enc_master(1, P.MODE_LOW_POWER) for f in originated),
       "spec 6: a trip during an override repeats like a release")
    out = b.low_power()
    ok(emitted_mode(out) == P.MODE_LOW_POWER, "the VCU's hold passes through in SAFE")
    ok(b.state() == "SAFE", "SAFE is latched")
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    ok(b.state() == "SAFE", "SAFE is not re-armable on CHARGER")
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(b.state() == "PASSTHROUGH", "only the VCU's own STAND_BY clears SAFE")


def test_source_11_with_the_plug_in_does_not_end_the_override():
    """Spec 6 (decided 2026-10-03): shutdownSource 11 is NOT a trigger.

    It is a latched "last stop" reason, not a live report. On the original
    Bel unit (replaced 2026-05-03) it stayed at 11 with the plug still in
    for 84-162 s, three times in the corpus. `charging45` has 209 such
    frames over 83 s, every one with vehicleConnected = 1, and triggering
    on them left the core transparent for the rest of the session and
    swallowed the charger fault that followed at t=178138.

    So: source 11 with the flag up changes nothing, and a charger fault
    after it must still trip."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 5000
    b.charger_status(state=12, shutdown_src=11, vehicle_connected=1)
    ok(b.state() == "OVERRIDE", "source 11 with the plug in is not a plug-out")
    b.t = 6000
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_status(state=12, shutdown_src=11, vehicle_connected=1)
    ok(b.state() == "OVERRIDE", "still overriding while it stays latched")
    # the charger fault that charging45 lost
    b.t = 7000
    b.charger_fault(BELINV_inverterFault=1)
    ok(b.state() == "SAFE", "and a charger fault after it still trips")
    ok(b.core.trip_reason is not None and "fault" in b.core.trip_reason,
       "the trip names the charger fault")


def test_charger_handle_pull_vehicle_connected_ends_the_override():
    """Spec 6: vehicleConnected going 1 -> 0 alone ends it, with the source
    still reading 0. In 12 of the 13 corpus pulls both flags drop in the
    same frame as the source-11 report, but either is sufficient."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 5000
    b.charger_status(state=12, shutdown_src=0, vehicle_connected=0)
    ok(b.state() == "TERMINATED",
       "vehicleConnected 1 -> 0 ends the override at once, on the flag alone")
    ok(b.core.trip_reason is None, "and it is not a trip")
    ok(b.core.stats["synth"] == 0, "no repeat burst")


def test_hvil_before_the_flow_drop_does_not_trip():
    """Spec 6: the whole point of the rule.

    HVIL opens AT the flow drop, and in 5 of the 13 source-11 pulls up to
    20 ms BEFORE it. That used to trip "HVIL open" mid-override. With the
    charger's report ending the override ~1 s earlier, the HVIL arrives in
    TERMINATED, where nothing can trip."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 5000
    b.charger_status(state=15, shutdown_src=11, vehicle_connected=0)
    ok(b.state() == "TERMINATED",
       "the flag dropping ended it (source 11 rides along, as on a real pull)")
    # HVIL opens 20 ms BEFORE the VCU's flow drop, ~1 s after the report
    b.t = 5980
    b.bms(vmax=3.340, chg_max=300.0, hvil=0)
    ok(b.core.trip_reason is None, "HVIL before the flow drop does not trip")
    b.t = 6000
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER))
    b.t = 6250
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(b.state() == "PASSTHROUGH" and b.core.trip_reason is None,
       "and the session closes out clean, with no fault in the log")


def test_charger_stop_source_3_is_not_a_handle_pull():
    """Spec 6: only source 11. Source 3 is the charger stopping itself, 24
    endings in the corpus, with the connected flags staying up. It must
    reach the normal trip path -- the 5 s state debounce -- not be mistaken
    for an unplug."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.t = 1000
    b.charger_status(state=15, shutdown_src=3, vehicle_connected=1)
    ok(b.state() == "OVERRIDE",
       "source 3 does not end the override as a handle pull")
    b.t = 3000
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_status(state=15, shutdown_src=3, vehicle_connected=1)
    b.tick()
    ok(b.core.trip_reason is None, "still inside the 5 s debounce")
    b.t = 6500
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_status(state=15, shutdown_src=3, vehicle_connected=1)
    b.tick()
    ok(b.core.trip_reason is not None and "state 12" in b.core.trip_reason,
       "and trips on the state debounce, as a charger-first stop should")


def test_battery_led_stop_source_4_still_trips():
    """Spec 6: source 4 is battery-led -- the BMS's EPO comes about 0.15 s
    first -- and it is a real stop that must TRIP, not a handle pull."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 1000
    # the BMS's EPO leads the charger's report
    b.bms(vmax=3.340, chg_max=300.0, epo=1)
    ok(b.state() == "SAFE", "the EPO trips")
    ok(b.core.trip_reason is not None and "EPO" in b.core.trip_reason.upper(),
       "and the trip names the EPO")
    b.t = 1150
    b.charger_status(state=15, shutdown_src=4, vehicle_connected=1)
    ok(b.state() == "SAFE",
       "the charger's source-4 report does not undo the latch")


def test_every_charger_frame_reaches_the_vehicle():
    """Spec 2: every frame is forwarded, in every state.

    The general form of the bug review probe Q4 found: the plug-out trigger
    did `return out` before the forwarding code, so the one frame carrying
    `vehicleConnected` = 0 was swallowed and the VCU learned of the plug-out
    only from the next mux-0 frame, about 0.4 s later. The differential
    could not see it -- both cores had the same early return -- and no test
    asserted that a triggering frame is still forwarded.

    So this asserts the invariant rather than the instance: drive the core
    through every state it has, and for each charger frame in, require
    exactly one vehicle-side copy out carrying that id."""
    b = Bench()

    def one_copy(label, fid, data):
        out = b.chg(fid, data)
        copies = [d for s, f, _e, d in out if s == M.TO_VEHICLE and f == fid]
        ok(len(copies) == 1,
           "%s in %s: exactly one vehicle-side copy (got %d)"
           % (label, b.state(), len(copies)))
        ok(not copies or copies[0] == data,
           "%s in %s: forwarded unmodified" % (label, b.state()))

    # PASSTHROUGH, before anything is known
    one_copy("status", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 8, "BELINV_vehicleConnected": 1},
        "BELINV_statusMultiplexer", 0))
    b.chg(M.CHG_PILOT, pilot(0))
    one_copy("pilot", M.CHG_PILOT, pilot(0))

    # MONITOR
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    ok(b.state() == "MONITOR", "armed")
    one_copy("status", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 12, "BELINV_vehicleConnected": 1},
        "BELINV_statusMultiplexer", 0))

    # OVERRIDE
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    one_copy("info", 0x18FFD9C0, P.encode(P.BEL, 0x18FFD9C0, {
        "BELINV_maxAvailableChargingCurrent": 16.0, "BELINV_vehicleState": 3}))

    # the plug-out frame itself -- the instance that started this
    b.t = 5000
    one_copy("plug-out", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 15, "BELINV_vehicleConnected": 0},
        "BELINV_statusMultiplexer", 0))
    ok(b.state() == "TERMINATED", "and it still ended the override")

    # TERMINATED
    one_copy("status", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 15, "BELINV_vehicleConnected": 0},
        "BELINV_statusMultiplexer", 0))

    # SAFE
    b2 = Bench()
    b2.arm(vmax=3.340, chg_max=300.0)
    b2.low_power()
    b2.bms(vmax=3.700, chg_max=300.0)
    ok(b2.state() == "SAFE", "tripped")
    b = b2
    one_copy("status", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 12, "BELINV_vehicleConnected": 1},
        "BELINV_statusMultiplexer", 0))
    one_copy("fault", 0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_inverterFault": 1, "BELINV_buckBoostFault": 0,
                            "BELINV_hvBatteryOverVoltage": 0,
                            "BELINV_overTemperature": 0},
        "BELINV_statusMultiplexer", 3))


def test_flow_drop_ends_the_override():
    """Spec 6 (changed 2026-09-30): a flow drop ends the override and the core
    goes transparent (TERMINATED). There is no teardown window: the
    handle-pull signals that follow arrive in a state where nothing can trip,
    so nothing has to be suppressed. Measured: flow 0, HVIL open +30 ms,
    STAND_BY +250 ms, EPO."""
    for start_override in (False, True):
        b = Bench()
        b.arm(vmax=3.340, chg_max=300.0)
        if start_override:
            b.low_power()
            ok(b.state() == "OVERRIDE", "overriding")
        b.t = 5000
        out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER if start_override
                                             else P.MODE_CHARGER))
        ok(b.state() == "TERMINATED",
           "the flow drop ends the override (override=%s)" % start_override)
        ok(emitted_mode(out) == (P.MODE_LOW_POWER if start_override
                                 else P.MODE_CHARGER),
           "the flow-drop frame reaches the charger unmodified")
        b.t = 5030
        b.charger_status(15)
        b.bms(vmax=3.340, chg_max=300.0, hvil=0)
        ok(b.core.trip_reason is None, "HVIL open after the flow drop is not a trip")
        b.t = 5280
        out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
        ok(b.state() == "PASSTHROUGH" and emitted_mode(out) == P.MODE_STANDBY,
           "STAND_BY ends the session, forwarded untouched")
        b.bms(vmax=3.340, chg_max=300.0, hvil=0, epo=1, mainc=14)
        ok(b.core.trip_reason is None and b.state() == "PASSTHROUGH",
           "EPO and the contactors after STAND_BY are not trips either")
        ok(b.core.stats["synth"] == 0,
           "no repeat on a flow drop (override=%s)" % start_override)


def test_no_longer_rewriting_after_a_flow_drop():
    """The teardown window this replaced suppressed EVERY trip, including the
    3610 mV ceiling and staleness, and left the core in OVERRIDE still
    rewriting 60 of 60 page-01 frames (review P2). The override now ends at
    the flow drop, so the point is that the rewriting stops."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 5000
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER))
    b.t = 5100
    out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER))
    ok(emitted_mode(out) == P.MODE_LOW_POWER,
       "no longer rewriting the mode after the flow drop")
    b.bms(vmax=3.700, chg_max=300.0)
    ok(b.state() == "TERMINATED",
       "transparent, and 3700 mV arms nothing from here")


def test_arm_delay_latches_nothing():
    """Spec 3 (A7): inside the arm delay the core waits. It must not accept,
    which the old order did -- the evap and SoC accept branches ran BEFORE
    the delay check, so a Low Power inside the delay latched TERMINATED for
    the whole session on an SoC the BMS can report 10-20 % off for ~60 s
    after it starts."""
    for evap_flag, soc in ((0, 80.0), (1, 60.0)):
        b = Bench(arm_delay_ms=75000)
        b.arm(vmax=3.340, chg_max=300.0, soc=soc, evap=evap_flag)
        b.t = 10000
        out = b.low_power()
        ok(b.state() == "MONITOR",
           "still MONITOR inside the delay (evap=%d soc=%.0f)" % (evap_flag, soc))
        ok(emitted_mode(out) == P.MODE_LOW_POWER,
           "the Low Power reaches the charger untouched inside the delay")
        ok(b.core.stats["modified"] == 0, "nothing rewritten inside the delay")


def test_arm_delay_then_decides_on_the_standing_command():
    """Spec 3: when the delay ends the core decides on the VCU's standing
    command from the tick path, and repeats it (4.1) because there is no
    frame to rewrite."""
    b = Bench(arm_delay_ms=75000)
    b.arm(vmax=3.340, chg_max=300.0)
    b.t = 10000
    b.low_power()
    ok(b.state() == "MONITOR", "waiting out the delay")
    bursts = b.hold_fresh(800, step_ms=100, resend_low_power=False)
    ok(b.state() == "OVERRIDE", "overrides once the delay has elapsed")
    ok(len(bursts) > 0, "and repeats the standing command to the charger")


def test_frame_path_requires_the_flow_bit():
    """Spec 3 (A5, review P7): a first Low Power arriving with flow 0 was
    overridden on the frame path, entering OVERRIDE during a teardown. The
    tick path always checked the flow bit; the frame path did not."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    out = b.veh(0x18EFC000, P.enc_master(0, P.MODE_LOW_POWER))
    ok(b.state() != "OVERRIDE", "a Low Power with flow 0 does not override")
    ok(emitted_mode(out) == P.MODE_LOW_POWER, "and is forwarded untouched")


def test_boot_into_a_running_session_stays_out():
    """Spec 3 (A3): the first pilot-timer value after power-up decides. 2 or
    more minutes means the session is already running, and a core that never
    heard the VCU standing page 00 must not join it."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(5))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    ok(b.state() == "PASSTHROUGH", "does not arm into a session already running")
    b.low_power()
    ok(b.state() == "PASSTHROUGH", "and does not override in it")


def test_boot_lock_holds_when_the_charge_is_observed_first():
    """Spec 3 (A3): the boot fallback must not depend on frame ORDER.

    This is the case the first version of these tests hid by sending the
    pilot frame before arming. On the truck the order is usually the other
    way round: 0x18FFD4C0 is 10 Hz and 0x18FFD8C0 is 1 Hz (measured 10:1 in
    every session -- 3000/300, 1648/165, 482/48, 517/52), so the charge is
    routinely observable before the first pilot value arrives.

    With `boot_locked` as the only guard the core armed first, the later
    pilot value of 5 set the flag but changed nothing, and the next Low
    Power was overridden -- the exact defect the fallback exists to prevent.
    Found by the review session's batch1_probes.py Q1."""
    b = Bench()
    # the charge is fully observable, but no pilot value has been read yet
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    b.charger_status(12)
    b.charge_info(16.0)
    b.evap(1)
    b.bms(vmax=3.340, chg_max=300.0, soc=83.0)
    ok(b.state() == "PASSTHROUGH",
       "does not arm before the first pilot value has been read")
    # only now does the Bel unit's 1 Hz frame arrive, and it says 5 minutes
    b.chg(M.CHG_PILOT, pilot(5))
    ok(b.state() == "PASSTHROUGH", "a late value of 5 still keeps us out")
    b.bms(vmax=3.340, chg_max=300.0, soc=83.0)
    ok(b.state() == "PASSTHROUGH", "and the charge does not arm it afterwards")
    b.low_power()
    ok(b.state() == "PASSTHROUGH", "so the Low Power is never overridden")
    ok(b.core.stats["modified"] == 0, "nothing rewritten")


def test_no_pilot_frame_means_no_override():
    """The cost of the ordering fix, stated as a test: a charger that never
    sends 0x18FFD8C0 is never overridden.

    That errs toward the truck's own charging, which is the right direction,
    and no session in the corpus has charger status frames without pilot
    frames. If that ever changes this test is where it will show."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0, soc=83.0, evap=1, pilot_min=None)
    ok(b.state() == "PASSTHROUGH", "never arms without a pilot frame")
    b.low_power()
    ok(b.state() == "PASSTHROUGH", "and never overrides")


def test_boot_early_in_a_session_joins_it():
    """Spec 3 (A3): 0 or 1 minutes means we may join. The 1-minute resolution
    makes that window unavoidable."""
    for mins in (0, 1):
        b = Bench()
        b.chg(M.CHG_PILOT, pilot(mins))
        b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
        ok(b.state() == "MONITOR", "joins a session %d min old" % mins)


def test_pilot_timer_stepping_back_is_a_session_boundary():
    """Spec 6.1: the timer returns to 0 at every handle pull, so a backwards
    step is a new plug-in. It clears SAFE -- the case that matters is a
    charge that failed at one handle and was moved to another."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    b.low_power()
    b.bms(vmax=3.700, chg_max=300.0)
    ok(b.state() == "SAFE", "tripped and latched")
    b.chg(M.CHG_PILOT, pilot(7))
    ok(b.state() == "SAFE", "still latched while the timer runs forward")
    b.chg(M.CHG_PILOT, pilot(0))
    ok(b.state() == "PASSTHROUGH", "the backwards step clears SAFE")
    ok(b.core.trip_reason is None, "and clears the trip reason")
    ok(not b.core.chg_seen_charging,
       "and the charger-reached-12 observation, so the next session must "
       "re-arm on its own charger")


def test_a_boundary_that_changes_no_state_is_still_logged():
    """Spec 6.1: the boundary must be visible even when it moves nothing.

    `_session_boundary` used to log only through `_goto`, so a clear_safe
    boundary arriving while the core was already in PASSTHROUGH reset
    chg_seen_charging, boot_locked, max_avail and learned_ilim in silence.
    That is the ORDINARY case for the pilot timer: the charger reports the
    handle out, which ends the session, and only then does the timer return
    to 0 -- so in a real plug-out the one boundary spec 6.1 cares most about
    left no record at all. Found when restart-after-standby failed on an
    event the core had in fact performed."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    b.chg(M.CHG_PILOT, pilot(4))
    ok(b.core.chg_seen_charging, "the charger was seen delivering")

    # the plug-out ends the session first, exactly as the charger reports it
    b.t += 1000
    b.chg(0x18FFD4C0, P.encode(
        P.BEL, 0x18FFD4C0, {"BELINV_state": 15, "BELINV_vehicleConnected": 0},
        "BELINV_statusMultiplexer", 0))
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(b.state() == "PASSTHROUGH", "session over before the timer resets")

    n_before = len(b.core.events)
    b.t += 1000
    b.chg(M.CHG_PILOT, pilot(0))
    ok(not b.core.chg_seen_charging,
       "the boundary cleared the charger-reached-12 observation")
    ok(len(b.core.events) > n_before,
       "and SAID so -- a boundary that resets the session must be in the log "
       "whether or not it also changes state")
    ok(any("pilot timer stepped back" in e[2]
           for e in b.core.events[n_before:]),
       "named as the pilot timer stepping back")


def test_a_session_end_burst_logs_once_but_resets_every_frame():
    """Spec 6.1: one boundary, one line -- and the reset still level-triggered.

    The VCU sends every page-00 change as a 20-frame burst, so once the
    boundary started logging in PASSTHROUGH a single session end read as
    twenty identical lines. The event ring is bounded, so that is not merely
    untidy: it evicts real history.

    The reset behind the log deliberately stays per-frame. `_maybe_arm` does
    not look at the VCU's mode, so it is the repeated clearing of
    chg_seen_charging that stops the core arming while the VCU is commanding
    STAND_BY -- making the whole boundary edge-triggered would change
    behaviour, not just output."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    ok(b.state() == "MONITOR", "armed")

    b.t += 1000
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(b.state() == "PASSTHROUGH", "the first frame of the burst ends it")
    n_after_first = len(b.core.events)

    for i in range(19):
        b.t += 10
        b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))

    ok(len(b.core.events) == n_after_first,
       "the other 19 frames add no further lines (got %d extra)"
       % (len(b.core.events) - n_after_first))

    # ...and the reset behind the collapsed log is still per-frame. Set the
    # observation the boundary exists to clear, then send one more frame of
    # the same burst: it must be cleared again, silently.
    b.core.chg_seen_charging = True
    n_before = len(b.core.events)
    b.t += 10
    b.veh(0x18EFC000, P.enc_master(0, P.MODE_STANDBY))
    ok(not b.core.chg_seen_charging,
       "a later frame of the burst still clears chg_seen_charging -- the "
       "reset is level-triggered, only the log is an edge")
    ok(len(b.core.events) == n_before, "and still logs nothing")


def test_charger_silence_boundary_does_not_clear_safe():
    """Spec 6.1: 20 s of charger silence resets the session but NOT SAFE.
    Silence trips the core at 500 ms, and the same silence continuing must
    not undo the latch it set."""
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding")
    b.t = 1000
    b.tick()
    ok(b.state() == "SAFE" and "stale" in (b.core.trip_reason or ""),
       "500 ms of silence trips")
    b.t = 30000
    b.tick()
    ok(b.state() == "SAFE", "20 s of continuing silence does NOT clear SAFE")
    ok(not b.core.chg_seen_charging,
       "but it does reset the rest of the session state")


def test_charger_silence_forgets_the_pilot_timer():
    """Spec 6.1: "The charger-silence boundary also forgets the last
    pilot-timer reading. The first reading after the silence is not compared
    with the last one before it, so a timer that comes back lower is not a
    step-back and does not clear SAFE."

    A Bel unit that restarts with the handle still in resumes its timer from
    a lower minute count, which on the wire is indistinguishable from a
    replug. Without the forgetting, that reading would clear the very latch
    the unit's own silence had set.
    """
    b = Bench()
    b.chg(M.CHG_PILOT, pilot(0))
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    b.chg(M.CHG_PILOT, pilot(9))
    b.low_power()
    ok(b.state() == "OVERRIDE", "overriding, pilot timer at 9 min")
    ok(b.core.pilot_min == 9, "the core is holding 9 as the last reading")

    b.t = 1000
    b.tick()
    ok(b.state() == "SAFE", "500 ms of silence trips")
    b.t = 30000
    b.tick()
    ok(b.state() == "SAFE", "20 s of silence does not clear SAFE")
    ok(b.core.pilot_min is None,
       "and the boundary forgot the last pilot reading")

    b.chg(M.CHG_PILOT, pilot(1))
    ok(b.state() == "SAFE",
       "1 min after a 9 min reading is NOT a step-back across the silence")
    ok(b.core.trip_reason is not None, "so the latch is still intact")

    # The forgetting is scoped to the first reading after the silence. Without
    # this the test would also pass if step-back detection had been deleted
    # outright, which is the same shape of mistake as a check that cannot
    # fail: 1 -> 0 is a genuine step-back and must still end the session.
    b.chg(M.CHG_PILOT, pilot(0))
    ok(b.state() == "PASSTHROUGH",
       "but a step back from the POST-silence reading still clears SAFE")


def test_charger_state_debounce_is_five_seconds():
    """Spec 6 (A8a): raised from 2 s. Measured non-terminal dips out of state
    12 reach 1.2 s, and a Low Power / CHARGER change -- which the core own
    override causes -- resets the charger for 0.8-1.2 s."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.t = 1000
    b.charger_status(15)
    b.t = 3000
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_status(15)
    b.tick()
    ok(b.core.trip_reason is None, "a 2 s dip out of 12 does not trip")
    b.t = 6500
    b.bms(vmax=3.340, chg_max=300.0)
    b.charger_status(15)
    b.tick()
    ok(b.core.trip_reason is not None and "state 12" in b.core.trip_reason,
       "but a dip past 5 s does")


def test_hard_ceiling_trips_immediately():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.bms(vmax=3.615, chg_max=300.0)
    ok(b.core.trip_reason is not None, "vmax over the hard ceiling trips")
    ok("hard ceiling" in b.core.trip_reason, "trip names the hard ceiling")
    ok(b.state() == "SAFE", "and latches SAFE")


def test_cell_temperature_is_not_a_trip():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.bms(vmax=3.340, chg_max=300.0, tmax=52.0)
    ok(b.state() == "OVERRIDE" and b.core.trip_reason is None,
       "52 C does not trip: the BMS's own response is what we obey")
    b.bms(vmax=3.340, chg_max=300.0, tmax=-5.0)
    ok(b.state() == "OVERRIDE", "-5 C does not trip either")
    b.bms(vmax=3.340, chg_max=0.0, tmax=52.0)
    ok(abs(b.commanded_ilim() - 0.0) < 0.06,
       "...through the permission we command (BMS says 0 A -> 0 A)")


def test_type2_alarm_does_not_trip():
    """TYPE2 is a latched state this truck charges under. TYPE3 is a fault."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0, alarm=2)
    b.low_power()
    ok(b.state() == "OVERRIDE", "TYPE2 must not disarm us")
    b.bms(vmax=3.340, chg_max=300.0, alarm=3)
    ok(b.core.trip_reason is not None, "TYPE3 trips")


def test_staleness_trips():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.t = 5000                      # no frames for 5 s of vehicle time
    b.tick()
    ok(b.core.trip_reason is not None, "stale BMS/charger frames trip")


def test_session_cap_trips():
    b = Bench(override_max_ms=2000)
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    b.hold_fresh(25, vmax=3.400, chg_max=300.0)
    ok("session cap" in (b.core.trip_reason or ""), "the override cap trips")


def test_charger_command_during_override_ends_it():
    """Spec 6: defined, not expected."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "override engaged")
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    ok(b.state() == "MONITOR", "CHARGER during the override ends it, still armed")
    n_mod = b.core.stats["modified"]
    b.commanded_ilim(19.0)
    ok(b.core.stats["modified"] == n_mod, "the VCU's own setpoint passes untouched")
    b.t = 200000
    b.low_power()
    ok(b.state() == "OVERRIDE", "a later Low Power is decided afresh")


def test_accepted_stop_is_not_rewritten():
    b = Bench()
    b.arm(vmax=3.594, chg_max=2.75, soc=100.0, evap=0)
    out = b.low_power()
    ok(b.state() == "TERMINATED" and emitted_mode(out) == P.MODE_LOW_POWER,
       "genuine stop accepted and forwarded")
    out = b.low_power()
    ok(emitted_mode(out) == P.MODE_LOW_POWER,
       "the VCU's hold keeps passing through after an accepted stop")
    b.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
    ok(b.state() == "PASSTHROUGH", "CHARGER after TERMINATED is re-armable")


def test_passthrough_is_byte_exact_when_idle():
    b = Bench()
    frames = [(0x410, P.encode(P.EPRI, 0x410, {"bcm_soc": 50.0})),
              (0x18EFC000, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 380)),
              (0x18EFC000, P.CONST_03_06),
              (M.VCM_EVAP, f_649(1))]
    for fid, data in frames:
        for _s, f, _e, d in b.veh(fid, data):
            ok(f == fid and d == data, "idle forwarding is byte-exact")


# --- spec 8: diagnostics -----------------------------------------------------

def test_mirror_frames_only_for_modified_frames():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    out = b.low_power()
    ids = [f for _s, f, _e, _d in out]
    ok(M.mirror_id(M.CMD_ID) in ids, "a rewritten page 00 is mirrored")
    mirror = [d for _s, f, _e, d in out if f == M.mirror_id(M.CMD_ID)][0]
    sent = [d for _s, f, _e, d in out if f == M.CMD_ID][0]
    ok(mirror == sent, "the mirror is the frame as sent to the charger")
    out = b.veh(0x410, P.encode(P.EPRI, 0x410, {"bcm_soc": 80.0}))
    ok(all(f != M.mirror_id(0x410) for _s, f, _e, _d in out),
       "an unmodified frame is not mirrored")


def test_diag_frames_carry_state_and_evap():
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0, soc=80.0)
    frames = dict(b.core.diag_frames(1000))
    st = frames[M.DIAG_STATUS_ID]
    ok(st[0] == M.DIAG_SCHEMA_VER and st[1] == M.DIAG_FW_VER, "versions on the wire")
    ok(st[2] == M.S_MONITOR, "state on the wire")
    obs = frames[M.DIAG_OBSERVED_ID]
    ok(obs[4] == 160, "soc half-percent on the wire")
    ok(obs[7] & 0x80, "evap flag on the wire (schema 2)")
    ok((obs[7] & 0x0F) == b.core.vcu_mode, "VCU mode in the low nibble")
    b.evap(0)
    obs = dict(b.core.diag_frames(1000))[M.DIAG_OBSERVED_ID]
    ok(not (obs[7] & 0x80), "evap clear on the wire")
    line = b.core.diag_line(1000)
    ok("evap=0" in line and "schema=%d" % M.DIAG_SCHEMA_VER in line,
       "serial diag line carries the flag and the schema")
    cnt = frames[M.DIAG_COUNTERS_ID]
    ok(cnt[4] == 0 and cnt[5] == 0, "synth counter reads 0 before any repeat")


def test_trip_codes_match_the_board_enum():
    for text, code in (("vmax 3615 mV over hard ceiling", 1),
                       ("BMS cell_overvolt", 2),
                       ("BMS EPO", 4),
                       ("charger fault inv=1 bb=0 ov=0 ot=0", 8),
                       ("override exceeded session cap", 12)):
        b = Bench()
        b.core.trip_reason = text
        ok(b.core.trip_reason_code() == code, "%r -> %d" % (text, code))


# --- spec 4.1 / 6: the repeat burst and the staleness timers (B-1, B-6, B-7a)

def test_a_tick_raised_trip_sends_all_twenty_repeat_frames():
    """Spec 4.1: every repeat is 20 frames, whichever path raised it (B-1).

    The trips reachable only from tick() -- BMS or charger stale, the charger
    out of state 12 past the debounce, the 6 h cap -- went through a _trip()
    whose return value tick() discarded. _trip() emits the burst's FIRST
    frame itself and decrements the counter for it, so that frame was built,
    counted in `synth`, and dropped on the floor: 19 frames on the wire
    reported as 20. machine.cpp, which passes `out` by reference into trip(),
    sent 20, and the two known-divergence pairs were this and nothing else.

    Counting `synth` here would not catch it. The count has to come off the
    emitted frames.
    """
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "override engaged")

    sent = []
    # Let the BMS go quiet with the charger still fresh: the trip comes from
    # tick(), and SAFE does not stop the burst -- _emit_repeat runs at the top
    # of tick(), above the armed-only return.
    for _ in range(40):
        b.t += 100
        b.charger_status(12)
        for side, fid, _ext, data in b.tick():
            if side == M.TO_CHARGER and fid == M.CMD_ID:
                sent.append(data)
    ok("BMS frames stale" in (b.core.trip_reason or ""),
       "the BMS went stale and tripped from tick()")
    ok(len(sent) == 20,
       "20 repeat frames reach the charger, not 19 (got %d)" % len(sent))
    ok(b.core.stats["synth"] == len(sent),
       "`synth` counts what was actually sent (synth=%d sent=%d)"
       % (b.core.stats["synth"], len(sent)))


def test_each_bms_message_is_timed_on_its_own():
    """Spec 6 (B-6): a separate 500 ms staleness timer per BMS message.

    One timer refreshed by any BMS frame is the defect: with 0x420 silent the
    core kept commanding on a permission a minute old while 0x410 and 0x440
    kept the timer alive, and the 3610 mV backstop -- which reads 0x430 --
    was blind with it (review probe P5). Each message is withheld in turn
    while the other three and the charger stay fresh, so a single shared
    timer cannot trip at all here and every one of these fails against it.
    """
    for stale_id in (0x410, 0x420, 0x430, 0x440):
        b = Bench()
        b.arm(vmax=3.340, chg_max=300.0)
        b.low_power()
        ok(b.state() == "OVERRIDE", "0x%03X: override engaged" % stale_id)
        for _ in range(10):                      # 1 s, twice the threshold
            b.t += 100
            b.charger_status(12)
            b.bms(vmax=3.340, chg_max=300.0, skip=(stale_id,))
            b.tick()
        reason = b.core.trip_reason or ""
        ok("BMS frames stale" in reason,
           "0x%03X going quiet alone trips (reason %r)" % (stale_id, reason))
        # Naming the message is the half that proves the timers are separate
        # rather than one timer that happened to expire.
        ok(("0x%03X" % stale_id) in reason,
           "the trip names 0x%03X, not another message (reason %r)"
           % (stale_id, reason))
        ok(b.core.trip_reason_code() == 10,
           "0x%03X: still trip code 10 on the wire" % stale_id)


def test_the_other_three_bms_messages_hold_the_core_up():
    """The companion to the test above: it must be possible to fail.

    If `skip` did not actually withhold anything, or if any of the four
    messages were absent from the bench's `bms()`, every assertion above
    would pass for the wrong reason. All four fresh must NOT trip.
    """
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    for _ in range(10):
        b.t += 100
        b.charger_status(12)
        b.bms(vmax=3.340, chg_max=300.0)
        b.tick()
    ok(b.core.trip_reason is None,
       "all four BMS messages fresh: no staleness trip (%r)"
       % b.core.trip_reason)
    ok(b.state() == "OVERRIDE", "and the override is still running")


def test_a_low_cell_is_the_bms_s_business_not_ours():
    """Spec 6 (B-7a): bcm_cell_undervolt is not a trip.

    The core has no low-cell limit of its own. If a cell is dangerously low
    the interposer must not get in the way of the charger charging it; the
    BMS declares a pack too low to charge through its permission and its
    faults, and those are obeyed. The decoder stays -- test_signals.py and
    check_port.py check it against the DBC -- only the trip is gone.
    """
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0)
    b.low_power()
    ok(b.state() == "OVERRIDE", "override engaged")
    b.bms_420(chg_max=300.0, undervolt=1)
    ok(b.core.trip_reason is None,
       "cell_undervolt does not trip (%r)" % b.core.trip_reason)
    ok(b.state() == "OVERRIDE", "and the override carries on")
    # The overvolt bit in the same byte still does, so this is not just a
    # dead 0x420 handler.
    b.bms_420(chg_max=300.0, overvolt=1)
    ok("cell_overvolt" in (b.core.trip_reason or ""),
       "cell_overvolt in the same message still trips (%r)"
       % b.core.trip_reason)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        before = len(FAILS)
        try:
            t()
        except Exception as e:
            FAILS.append("%s raised %r" % (t.__name__, e))
        if len(FAILS) == before:
            print("  ok  %s" % t.__name__)
        else:
            print("FAIL  %s" % t.__name__)
    if FAILS:
        print("\n%d failure(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\ntest_machine: %d tests OK" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
