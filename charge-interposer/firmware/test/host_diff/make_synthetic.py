"""Synthetic scenarios for the differential test.

The capture-driven goldens (make_golden.py) cover the bulk forwarding path and
the override decision, but no real capture reaches a release, because in
reality the charger shut down at the ceiling and the capture ended. So the
release, the trip paths, the arm-delay wait and the above-ceiling start went
untested by them.

(This said "every trip path" until 2026-09-20, when it was checked and was
not true: no trace here drove vmax past the 3610 mV hard ceiling -- the
highest was 3593 mV -- so the backstop was the one branch the differential
never touched. scen_hard_ceiling_trip closes it.)

That is exactly the code where a port bug would be most expensive and least
visible, so these scenarios drive it deliberately: build frames by hand, walk
the pack up to the top of charge, and make the core release (spec 5.2:
transparency plus one repeat of the VCU's standing command, spec 4.1 --
the only frames the core ever originates).

Each scenario writes the same trace/golden pair format as make_golden.py, so
host_runner.cpp consumes them unchanged.

Usage (from the repo root):
    py -3.12 .../make_synthetic.py --outdir <artifacts dir>
"""

import argparse
import os
import sys

sys.path.insert(0, ".")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTERPOSER = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _INTERPOSER)

import machine as M          # noqa: E402


TICK_MS = 10
FRAME_MS = 100


# --- frame builders: the inverse of machine.py's extractors -----------------

def f_430(vmax_mv, vmin_mv=3300, tmax_c=25.0, tmin_c=20.0):
    d = bytearray(8)
    d[0] = (vmax_mv >> 8) & 0x1F
    d[1] = vmax_mv & 0xFF
    d[2] = (vmin_mv >> 8) & 0x1F
    d[3] = vmin_mv & 0xFF
    d[4] = int(round((tmax_c * 10 + 400) / 5))
    d[5] = int(round((tmin_c * 10 + 400) / 5))
    return bytes(d)


def f_420(chg_max_ca, overvolt=0, undervolt=0):
    counts = chg_max_ca // 25
    d = bytearray(8)
    d[1] = (counts >> 8) & 0x0F
    d[2] = counts & 0xFF
    d[6] = (overvolt << 7) | (undervolt << 6)
    return bytes(d)


def f_410(soc_half, ibat_ca, epo=0, alarm=0, hvil=1, chg_done=0):
    raw = (ibat_ca + 100000) * 10 // 25
    d = bytearray(8)
    d[0] = (epo << 1) | (hvil << 3)
    d[1] = (alarm & 3) << 6
    d[3] = soc_half & 0xFF
    d[4] = (raw >> 8) & 0xFF
    d[5] = raw & 0xFF
    d[6] = (chg_done & 1) << 4
    return bytes(d)


def f_440(mainc_stat=12):
    d = bytearray(8)
    d[0] = (mainc_stat & 0x0F) << 2
    return bytes(d)


def f_649(evap):
    """0x649 vcm_evap_active at B4 bit 7 (spec 3); other bytes arbitrary."""
    d = bytearray((0x00, 0x00, 0x30, 0x00, 0x80 if evap else 0x00, 0, 0, 0))
    return bytes(d)


def f_chg_status(state=12, mux=0, shutdown_src=0, vehicle_connected=1,
                 evse_connected=1):
    # BELINV_evseConnected 11|1 -> d[1] bit 3, BELINV_vehicleConnected 12|1
    # -> d[1] bit 4, BELINV_shutdownSource 48|8 -> d[6]. A real charger
    # reports the plug connected throughout a charge, so that is the
    # default; the spec 6 handle-pull rule keys off source 11 or
    # vehicleConnected going 1 -> 0.
    d = bytearray(8)
    d[0] = mux
    d[1] = (evse_connected << 3) | (vehicle_connected << 4)
    d[5] = state
    d[6] = shutdown_src
    return bytes(d)


def f_chg_fault(inverter=0, buckboost=0, hv_ov=0, overtemp=0):
    d = bytearray(8)
    d[0] = 3
    d[1] = (hv_ov << 4) | (overtemp << 6)
    d[2] = inverter | (buckboost << 1)
    return bytes(d)


def f_chg_info(max_avail_ca):
    counts = max_avail_ca // 5
    d = bytearray(8)
    d[0] = counts & 0xFF
    d[1] = (counts >> 8) & 0xFF
    return bytes(d)


def f_chg_pilot(minutes):
    """0x18FFD8C0 BELINV_chargePilotOnlineTime: B5-B7, 24-bit LE, MINUTES."""
    d = bytearray(8)
    d[5] = minutes & 0xFF
    d[6] = (minutes >> 8) & 0xFF
    d[7] = (minutes >> 16) & 0xFF
    return bytes(d)


def f_cmd_master(flow, mode):
    return bytes((0x00, flow, mode, 0, 0, 0, 0, 0))


def f_cmd_setpoint(ilim_counts, vlim_counts=8584):
    return bytes((0x01, vlim_counts & 0xFF, (vlim_counts >> 8) & 0xFF,
                  ilim_counts & 0xFF, (ilim_counts >> 8) & 0xFF, 0, 0, 0))


# --- scenario construction --------------------------------------------------

class Scenario(object):
    """Accumulates (t_ms, side, id, data) events; ticks are added on write."""

    def __init__(self, name):
        self.name = name
        self.events = []

    def veh(self, t_ms, arb, data):
        self.events.append((t_ms, "V", arb, data))

    def chg(self, t_ms, arb, data):
        self.events.append((t_ms, "C", arb, data))

    def steady(self, t0, t1, vmax, chg_max, soc_half, ibat=1520,
               chg_state=12, max_avail=1600, mainc=12, ilim=380,
               overvolt=0, undervolt=0, alarm=0, hvil=1, tmax_c=25.0,
               evap=0, master=None, pilot_min=0, shutdown_src=0,
               vehicle_connected=1):
        """Fill [t0, t1) with the normal charging frame set at FRAME_MS.
        `evap` is the 0x649 flag; `master` = (flow, mode) would re-assert
        the VCU's page 00 once a second -- unused, because the truck's VCU
        sends page 00 only on change (spec 4.1) and a re-assert would end
        the core's repeat burst early.

        `pilot_min` is the Bel unit's 0x18FFD8C0 value, emitted at 1 Hz as
        the truck does (measured 10:1 against the 10 Hz status frame in
        every session). The core will not arm until it has read one, so a
        scenario that omits it stays in PASSTHROUGH throughout -- pass
        `pilot_min=None` only when that is the point."""
        for t in range(t0, t1, FRAME_MS):
            self.veh(t, 0x430, f_430(vmax, tmax_c=tmax_c))
            self.veh(t, 0x420, f_420(chg_max, overvolt, undervolt))
            self.veh(t, 0x410, f_410(soc_half, ibat, alarm=alarm, hvil=hvil))
            self.veh(t, 0x440, f_440(mainc))
            self.veh(t, M.VCM_EVAP, f_649(evap))
            self.chg(t, M.CHG_STATUS,
                     f_chg_status(chg_state, shutdown_src=shutdown_src,
                                  vehicle_connected=vehicle_connected))
            self.chg(t, 0x18FFD9C0, f_chg_info(max_avail))
            self.veh(t, M.CMD_ID, f_cmd_setpoint(ilim))
            if pilot_min is not None and (t - t0) % 1000 == 0:
                self.chg(t, M.CHG_PILOT, f_chg_pilot(pilot_min))
            if master is not None and (t - t0) % 1000 == 0:
                self.veh(t, M.CMD_ID, f_cmd_master(master[0], master[1]))


def scen_override_release():
    """arm -> override -> the BMS tapers -> its permission collapses ->
    release = transparency -> TERMINATED, the VCU's hold passes untouched,
    then its own STAND_BY.

    The one that exercises the release (spec 5). vmax is walked from the
    evap ceiling (3340 mV) up into the taper band and chg_max is walked down
    as the BMS would; the core must release on chg_max <= 3.0 A while vmax is
    still 3593 mV (the parts-truck pin), repeat the VCU's 00 01 03 to the
    charger 20 times (spec 4.1) and from then on rewrite NOTHING: the VCU's
    setpoint reaches the charger as sent. Then the VCU closes out with
    STAND_BY: the core goes PASSTHROUGH and a later 00 01 03 passes untouched.
    """
    s = Scenario("syn_override_release")
    # 0-90 s: ordinary charging at 79 % with the evap flag up. Arms almost at
    # once (charger in state 12, contactors closed) and then has to sit for
    # arm_delay_ms = 75 s before an override is permitted.
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=158, evap=1)
    # 90 s: the VCU calls the evap ceiling at 80 % with the BMS still granting
    # 300 A -> overridden at once (gate: flag set, soc >= 78 %, delay done).
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    # 90-140 s: we are driving now. Walk vmax up through the taper band while
    # the BMS walks its permission down; stop at the pin, 3593 mV / 2.8 A.
    t = 90100
    vmax, chg_max = 3340, 30000
    while t < 140000:
        s.steady(t, t + 1000, vmax=vmax, chg_max=chg_max, soc_half=160,
                 evap=1)
        vmax = min(vmax + 6, 3593)
        chg_max = max(chg_max - 600, 280)
        t += 1000
    # 140-150 s: pinned. chg_max 2.8 A < 3.0 A held past full_debounce_ms ->
    # the release; vmax never reaches 3595. The VCU keeps re-asserting its
    # hold and its (now 2.9 A) setpoint: untouched after the release.
    s.steady(140000, 150000, vmax=3593, chg_max=280, soc_half=160, evap=1,
             ilim=58)
    # 150-155 s: the charger is in the VCU's hold (state 12, 2.4 A).
    s.steady(150000, 155000, vmax=3590, chg_max=280, soc_half=160, evap=1,
             ilim=58, ibat=30)
    # 155 s: handle pull -> the VCU closes out -> PASSTHROUGH; its next hold
    # command passes untouched.
    s.veh(155000, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(155100, 158000, vmax=3590, chg_max=280, soc_half=160, ibat=0,
             chg_state=11, evap=1)
    s.veh(158000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(158100, 160000, vmax=3590, chg_max=280, soc_half=160, ibat=0,
             chg_state=11, evap=1)
    return s


def scen_trip_during_override():
    """Override, then a BMS cell_overvolt. Straight to SAFE, transparent, the
    VCU's standing Low Power repeated to the charger (spec 4.1, 6)."""
    s = Scenario("syn_trip_during_override")
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=158, evap=1)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 100000, vmax=3340, chg_max=30000, soc_half=160, evap=1)
    # cell overvolt asserted -> trip -> SAFE; the VCU's hold passes untouched
    s.steady(100000, 110000, vmax=3340, chg_max=30000, soc_half=160,
             overvolt=1, evap=1)
    return s


def scen_repeat_superseded():
    """Spec 4.1: the VCU's own page 00 lands INSIDE the repeat and ends it.

    Added 2026-10-05. Every other trace in this corpus lets the repeat run
    to its full 20 frames, so the whole superseding branch -- the clause
    spec 4.1 is written around -- had no trace at all. On the real bench
    `fault-during-override` hits it every time and nothing here did.

    Why it is a dead heat on the bench and deliberate here: a burst is
    burst_frames x burst_period_ms = 1,000 ms, and the VCU drops energy flow
    REACT_HOLD_S = 1.0 s after the charger leaves state 12, so the two land
    together and the achieved count is scheduling jitter (13 at 1x, 19 at
    5x, 7 on the board). This trace puts the frame at a FIXED 500 ms into
    the burst instead, so the count is deterministic and a golden can hold
    it.
    """
    s = Scenario("syn_repeat_superseded")
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=158, evap=1)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 100000, vmax=3340, chg_max=30000, soc_half=160, evap=1)
    # cell overvolt -> trip -> SAFE, and the repeat of the VCU's hold starts
    s.steady(100000, 100500, vmax=3340, chg_max=30000, soc_half=160,
             overvolt=1, evap=1)
    # ... and 500 ms in, the VCU closes the session out itself. Spec 4.1:
    # "A page-00 frame from the VCU during a repeat ends the repeat; the
    # VCU's frame is the truth."
    s.veh(100500, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(100600, 110000, vmax=3340, chg_max=30000, soc_half=160,
             overvolt=1, evap=1)
    return s


def scen_repeat_replaced():
    """Spec 4.1: a repeat starting over one still in flight REPLACES it.

    Added 2026-10-05 for the paragraph the user approved that day: "A
    repeat that starts while another is still being sent replaces it: the
    earlier one stops there, the frames it already sent stand, and only
    the new command continues."

    The base is scen_above_ceiling_start, because that override is decided
    FROM A TICK -- the 75 s arm delay elapsing with no page-00 frame on the
    boundary -- which is the only way to open a CHARGER repeat that a later
    event can then interrupt. My first attempt at reproducing this branch
    used scen_charger_fault as the base and it did not reproduce, because
    that override is decided by an arriving frame and opens no burst; the
    review session had named the tick-decided case and the miss was my
    base, not their mechanism.

    The timeline:
      0.1 s    the charge is established. THE ARM DELAY RUNS FROM HERE,
               not from the VCU's command -- my first attempt timed the
               fault off the Low Power at 3 s and the override had already
               fired and completed 2.4 s earlier, giving two COMPLETE
               repeats and no replacement at all.
      3 s      the VCU commands Low Power and keeps holding (83 % start)
      75.1 s   the 75 s arm delay elapses. Tick-decided override -> the
               VCU's standing Low Power is repeated as CHARGER. Burst opens.
      75.6 s   the charger raises an inverter fault. Spec 6: a trip during
               an override repeats the VCU's CURRENT mode, Low Power. That
               second repeat starts while the first is still sending.

    500 ms IN, NOT ON THE BOUNDARY, and that is the whole point of the
    timing. A burst is burst_frames x burst_period_ms = 1,000 ms, so a
    fault at exactly 1,000 ms is a dead heat with the burst's own end and
    the achieved count becomes scheduling jitter -- the same photo finish
    that made `fault-during-override` report 13, 19 and 7 frames on three
    runs of identical code. At a fixed 500 ms the count is deterministic
    and a golden can hold it, and the end reason is unambiguously REPLACED
    rather than COMPLETE.
    """
    s = Scenario("syn_repeat_replaced")
    s.steady(0, 3000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(3000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    # Hold past the 75 s arm delay so the override is tick-decided. The
    # delay is measured from charge establishment at t=100, so it elapses
    # at 75100 -- not 78000, which is what timing it off the VCU's 3 s
    # command would give.
    s.steady(3100, 75600, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    # 500 ms into the CHARGER repeat: the inverter fault.
    s.chg(75600, M.CHG_STATUS, f_chg_fault(inverter=1))
    s.steady(75700, 90000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(90100, 93000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=0, ilim=0, chg_state=11)
    return s


def scen_charger_fault():
    """Charger raises an inverter fault while we are overriding."""
    s = Scenario("syn_charger_fault")
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=158, evap=1)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3340, chg_max=30000, soc_half=160, evap=1)
    s.chg(95000, M.CHG_STATUS, f_chg_fault(inverter=1))
    s.steady(95100, 105000, vmax=3340, chg_max=30000, soc_half=160, evap=1)
    return s


def scen_stale_bms():
    """BMS goes quiet mid-charge. Staleness must trip, and tick must raise it."""
    s = Scenario("syn_stale_bms")
    s.steady(0, 60000, vmax=3340, chg_max=30000, soc_half=150)
    # BMS silent from 60 s; only charger frames continue.
    for t in range(60000, 70000, FRAME_MS):
        s.chg(t, M.CHG_STATUS, f_chg_status(12))
        s.chg(t, 0x18FFD9C0, f_chg_info(1600))
    return s


def scen_evap_clear_accept():
    """Low Power with the evap flag clear, at 90 % with the BMS mid-taper.

    Spec 3: the flag decides. Not the ceiling -> TERMINATED, nothing touched.
    """
    s = Scenario("syn_evap_clear_accept")
    s.steady(0, 90000, vmax=3480, chg_max=1000, soc_half=180, evap=0)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3480, chg_max=1000, soc_half=180, evap=0)
    return s


def scen_soc_below_gate():
    """Low Power with the evap flag SET but SoC 70 %: below the 78 % gate,
    so not the ceiling -> accepted."""
    s = Scenario("syn_soc_below_gate")
    s.steady(0, 90000, vmax=3300, chg_max=30000, soc_half=140, evap=1)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3300, chg_max=30000, soc_half=140, evap=1)
    return s


def scen_startup_transient():
    """The startup transient with the evap flag up at 83 %: Low Power 3 s
    into the charge, CHARGER again at 66 s. Indistinguishable from the
    ceiling while it lasts, so the core waits out the arm delay, touches
    nothing, and is still armed when the VCU goes back to CHARGER."""
    s = Scenario("syn_startup_transient")
    s.steady(0, 3000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(3000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(3100, 66000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(66000, M.CMD_ID, f_cmd_master(0x01, M.MODE_CHARGER))
    s.steady(66100, 80000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=1500, ilim=380)
    return s


def scen_above_ceiling_start():
    """The 83 % start where the VCU keeps holding (spec 3, 7): Low Power 3 s
    in, pack draining, 0 A setpoint. Overridden at the 75 s arm delay --
    from a tick, since no page-00 frame lands on the boundary -- so the VCU's
    standing Low Power is repeated as CHARGER (spec 4.1); then the setpoint
    is rewritten to the pilot cap (the learned CC command is 0 = none), and a
    STAND_BY at 100 s ends it."""
    s = Scenario("syn_above_ceiling_start")
    s.steady(0, 3000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(3000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(3100, 100000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=-270, ilim=0)
    s.veh(100000, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(100100, 103000, vmax=3324, chg_max=27075, soc_half=166, evap=1,
             ibat=0, ilim=0, chg_state=11)
    return s


def scen_balance_hold():
    """A genuine top of charge, the balance hold, and the close-out.

    The sequence the 49-session survey establishes (see
    notes/charge-termination-behaviour.md), which nothing on the bench could
    produce before 2026-09-12:

        CHARGER/1  -> LOW_POWER/1   accepted as genuine: the BMS has already
                                    tapered chg_max, so this is not the evap
                                    ceiling
        ...                         the HOLD. The charger stays in state 12 at
                                    ~2.4 A while the pack balances -- 7.7 h on
                                    the parts truck, 60 s here
        LOW_POWER/0                 the energy-flow bit. THIS is what stops the
                                    charger
        STAND_BY/0                  close-out

    None of it is ours to touch. Two things are being checked: that a long
    stretch of mode 3 is not re-read as an evap ceiling, and that the two frames
    which actually end the session are forwarded byte for byte.

    After the flow bit drops the charger reports state 11. Measured across six
    sessions (2026-09-12): 12 -> 11 within a second of the drop, then a 12/15
    flutter from about +9 s that this trace does not bother to reproduce -- by
    then the STAND_BY has already put the core in PASSTHROUGH.
    """
    s = Scenario("syn_balance_hold")
    # Ordinary charging near the top, BMS permission already tapering, evap
    # flag clear: the Low Power is genuine (spec 3).
    s.steady(0, 90000, vmax=3560, chg_max=600, soc_half=200, ibat=400,
             max_avail=800)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    # The hold: current at the VCU's ~2.4 A hold value, charger still delivering.
    s.steady(90100, 150000, vmax=3587, chg_max=300, soc_half=200, ibat=240,
             max_avail=600, ilim=96)
    # Out of the hold the close-out is ONE step: flow 0 and STAND_BY together.
    # All 3 sessions whose flow bit dropped in LOW_POWER did it this way
    # (standby_gap.py, gap 0.0 s) -- there is no intermediate LOW_POWER/0.
    s.veh(150000, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(150100, 156000, vmax=3587, chg_max=300, soc_half=200, ibat=0,
             chg_state=11, max_avail=600, ilim=0)
    return s


def scen_direct_stop():
    """The majority ending: flow drops with the mode left at CHARGER.

        CHARGER/1 -> CHARGER/0 -> STAND_BY

    27 of 49 surveyed sessions end exactly this way and mode 3 never appears, so
    the interposer never gets a LOW_POWER to judge -- it must simply forward. A
    port that keyed the end of a session off the mode rather than the flow bit
    would sail straight past this.
    """
    s = Scenario("syn_direct_stop")
    s.steady(0, 90000, vmax=3560, chg_max=600, soc_half=200, ibat=400,
             max_avail=800)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x00, M.MODE_CHARGER))
    # STAND_BY 1.0 s later: the measured median (standby_gap.py), and inside
    # chg_state_debounce_ms, so the charger dropping out of state 12 must NOT
    # produce a trip here.
    s.steady(90100, 91000, vmax=3570, chg_max=300, soc_half=200, ibat=0,
             chg_state=11, max_avail=800, ilim=0)
    s.veh(91000, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(91100, 96000, vmax=3570, chg_max=300, soc_half=200, ibat=0,
             chg_state=11, max_avail=800, ilim=0)
    return s


def scen_hard_ceiling_trip():
    """arm -> override -> the BMS never tapers -> vmax crosses 3610 mV ->
    the hard ceiling trips, immediately and with no debounce.

    The backstop of spec 5, and the one path nothing else in this set
    reaches. The highest vmax anywhere else here is 3593 mV, in
    scen_override_release -- seventeen millivolts short of the ceiling. So
    until this scenario existed the C++ port's ceiling branch had never been
    compared against the Python reference at all: machine.py had a unit test
    (test_hard_ceiling_trips_immediately) and machine.cpp had nothing, while
    this file's own docstring claimed every trip path was covered. Added
    2026-09-20 after that was measured rather than assumed.

    Reaching the ceiling means holding off both of the normal stops. chg_max
    is pinned at 300 A so the BMS never withdraws permission and the release
    on chg_max <= 3.0 A cannot fire, which leaves vmax climbing with nothing
    but the 3610 mV ceiling in front of it. That is the same shape as the
    `hard-ceiling-failsafe` bench scenario, which cannot run on hardware
    because it needs an interposer_sim flag the board has no way to accept.

    The trip is transparency rather than a command: the core stops rewriting,
    the VCU's own hold reaches the charger untouched, and the handle pull
    clears it back to PASSTHROUGH.
    """
    s = Scenario("syn_hard_ceiling_trip")
    # 0-90 s: ordinary charging at 79 % with the evap flag up. Arms at once,
    # then sits out arm_delay_ms = 75 s.
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=158, evap=1)
    # 90 s: the VCU calls the evap ceiling -> overridden.
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    # 90-135 s: we are driving. vmax walks up 6 mV/s while the BMS keeps
    # granting 300 A, so nothing releases. 3340 + 45*6 = 3610 exactly, which
    # is >= vmax_hard_mv and must trip on that tick.
    t = 90100
    vmax = 3340
    while vmax < 3616:
        s.steady(t, t + 1000, vmax=vmax, chg_max=30000, soc_half=160, evap=1)
        vmax = min(vmax + 6, 3616)
        t += 1000
    # Past the ceiling: tripped and transparent. The VCU's setpoint must now
    # reach the charger byte for byte -- nothing rewritten after a trip.
    s.steady(t, t + 5000, vmax=3616, chg_max=30000, soc_half=160, evap=1,
             ilim=58)
    t += 5000
    # Handle pull -> the VCU closes out -> SAFE clears to PASSTHROUGH.
    s.veh(t, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(t + 100, t + 3000, vmax=3610, chg_max=30000, soc_half=160,
             ibat=0, chg_state=11, evap=1)
    return s


def scen_flow_drop_ends_override():
    """Spec 6 (A5): a flow drop ends the override; the core is transparent from
    then on and the handle-pull signals that follow cannot trip.

    The teardown window this replaced suppressed every trip, including the
    3610 mV ceiling, and left the core in OVERRIDE still rewriting (review
    P2). Here vmax goes to 3700 and HVIL opens AFTER the drop: the golden
    must show TERMINATED at the drop, no TRIP, and no further rewriting."""
    s = Scenario("syn_flow_drop_ends_override")
    s.steady(0, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 90000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    # the handle pull: flow drops while the mode still reads Low Power
    s.veh(90000, M.CMD_ID, f_cmd_master(0x00, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3700, chg_max=30000, soc_half=166, evap=1,
             hvil=0, chg_state=15)
    return s


def scen_arm_delay_no_latch():
    """Spec 3 (A7): nothing latches inside the arm delay.

    A Low Power arrives 10 s into the charge with the evap flag CLEAR, which
    before 2026-09-30 accepted immediately and latched TERMINATED for the
    session -- on an SoC the BMS can report 10-20 % off for ~60 s after it
    starts. The core must stay MONITOR until the delay has elapsed, then
    decide on the standing command."""
    s = Scenario("syn_arm_delay_no_latch")
    s.steady(0, 10000, vmax=3300, chg_max=30000, soc_half=170, evap=0)
    s.veh(10000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(10100, 95000, vmax=3300, chg_max=30000, soc_half=170, evap=0)
    return s


def scen_boot_mid_session():
    """Spec 3 (A3): the first pilot-timer value after power-up decides.

    5 minutes means the session is already running, and a core that never
    heard the VCU's standing page 00 must not join it: PASSTHROUGH
    throughout, even though the charge is observable and a Low Power with
    the evap flag set arrives later."""
    s = Scenario("syn_boot_mid_session")
    s.steady(0, 90000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=5)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=5)
    return s


def scen_pilot_restart_clears_safe():
    """Spec 6.1: the pilot timer stepping backwards is a session boundary and
    it clears SAFE.

    The case that matters: a charge fails at one handle, the truck is moved
    to another, and the second session must arm afresh rather than stay
    latched from the first. Here the first session trips on the hard ceiling,
    then the timer returns to 0 and a second charge runs normally."""
    s = Scenario("syn_pilot_restart_clears_safe")
    # the timer counts minutes of pilot through the first session
    s.steady(0, 60000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    s.steady(60000, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1)
    # trip: over the hard ceiling, SAFE latched
    s.steady(85000, 90000, vmax=3700, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1)
    # handle pulled and replugged: the timer steps BACK to 0
    s.chg(90000, M.CHG_PILOT, f_chg_pilot(0))
    s.veh(90050, M.CMD_ID, f_cmd_master(0x01, M.MODE_CHARGER))
    s.steady(90100, 180000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    s.veh(180000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(180100, 185000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    return s


def scen_charger_silence_keeps_safe():
    """Spec 6.1: 20 s of charger silence resets the session but NOT SAFE.

    Silence trips the core at 500 ms (the staleness row), and the same
    silence continuing must not undo the latch it set. The golden must show
    SAFE at the trip and still SAFE after the boundary fires."""
    s = Scenario("syn_charger_silence_keeps_safe")
    s.steady(0, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    # the Bel unit goes off the bus; the vehicle side keeps talking
    for tt in range(85000, 115000, FRAME_MS):
        s.veh(tt, 0x430, f_430(3340))
        s.veh(tt, 0x420, f_420(30000))
        s.veh(tt, 0x410, f_410(166, 1520))
        s.veh(tt, 0x440, f_440(12))
        s.veh(tt, M.VCM_EVAP, f_649(1))
    return s


def scen_boot_lock_arm_first():
    """Spec 3 (A3): the boot fallback must not depend on frame ORDER.

    The charge is fully observable for 3 s before the first pilot frame
    arrives, which is the usual order on the truck (0x18FFD4C0 at 10 Hz,
    0x18FFD8C0 at 1 Hz). When the value does arrive it reads 5, so the core
    must stay in PASSTHROUGH and never override.

    With `boot_locked` as the only guard the core armed during those 3 s and
    the later value changed nothing (review batch1_probes.py Q1)."""
    s = Scenario("syn_boot_lock_arm_first")
    s.steady(0, 3000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=None)
    s.steady(3000, 90000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=5)
    s.veh(90000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(90100, 95000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=5)
    return s


def scen_charger_handle_pull():
    """Spec 6 (A5, 2026-10-03): the charger's handle-pull report ends the
    override, about a second before the VCU's flow drop, so the HVIL that
    opens at the drop cannot trip.

    The real sequence, from all 13 source-11 pulls in the corpus
    (`artifacts/interposer-review-2026-09-22/handle_pull_sequence.txt`):
    the charger leaves 12 with source 11 and drops both connected flags
    0.8-1.1 s before the flow drop, then HVIL opens at the drop -- in 5 of
    the 13 up to 20 ms BEFORE it. Here HVIL opens 20 ms early, which under
    the old rule tripped "HVIL open" mid-override. The golden must show
    TERMINATED at the charger's report, no TRIP, and synth 0."""
    s = Scenario("syn_charger_handle_pull")
    s.steady(0, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    # the charger reports the pull and drops both flags, 1 s early
    s.steady(85000, 85980, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             chg_state=15, shutdown_src=11, vehicle_connected=0)
    # HVIL opens 20 ms BEFORE the VCU's flow drop
    s.steady(85980, 86000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             chg_state=15, shutdown_src=11, vehicle_connected=0, hvil=0)
    s.veh(86000, M.CMD_ID, f_cmd_master(0x00, M.MODE_LOW_POWER))
    s.steady(86020, 86250, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             chg_state=15, shutdown_src=11, vehicle_connected=0, hvil=0)
    s.veh(86250, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(86300, 87000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             chg_state=15, shutdown_src=11, vehicle_connected=0, hvil=0,
             mainc=14)
    return s


def scen_source_11_plug_in():
    """Spec 9 (2026-10-03): the original Bel unit latches shutdownSource 11
    with the plug still in, and the core must keep watching.

    charging45 has 209 such frames over 83 s, every one with
    vehicleConnected = 1. Triggering on source 11 left the core transparent
    for the rest of the session and swallowed the charger fault that
    followed. Here the same shape: source 11 latched with the flag up,
    then an inverter fault -- which must still trip."""
    s = Scenario("syn_source_11_plug_in")
    s.steady(0, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    # the latched source-11 reason, plug still in, charge continuing
    s.steady(85000, 95000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             shutdown_src=11, vehicle_connected=1)
    # the charger fault that charging45 lost
    s.chg(95000, M.CHG_STATUS, f_chg_fault(inverter=1))
    # run past the 20-frame / 950 ms repeat burst so synth reads 20; a
    # scenario that stops inside the burst reports 19 and reads like the
    # B1 divergence when it is only a short scenario.
    s.steady(95100, 97000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             shutdown_src=11, vehicle_connected=1)
    return s


def scen_charger_stop_source_3():
    """Spec 6: only source 11 is a handle pull.

    Source 3 is the charger stopping itself -- 24 endings in the corpus,
    connected flags staying up. It must reach the 5 s state debounce and
    trip there, not be mistaken for an unplug."""
    s = Scenario("syn_charger_stop_source_3")
    s.steady(0, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1)
    s.steady(85000, 95000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             chg_state=15, shutdown_src=3, vehicle_connected=1)
    return s


def scen_plug_out_then_pilot_reset():
    """Spec 6.1: the boundary that arrives when the session is ALREADY over.

    The real ordering at a handle pull, and the one no other trace covers:
    the charger reports the plug out (vehicleConnected 1 -> 0) and the VCU
    follows with STAND_BY, so the core is in PASSTHROUGH; the pilot timer
    only returns to 0 afterwards. `syn_pilot_restart_clears_safe` reaches
    the same boundary from SAFE, which takes the state-changing path --
    so until this trace existed, the differential never ran a clear_safe
    boundary that changes no state, and both cores could have logged
    nothing without the diff noticing.

    The golden must show the pilot boundary logged in PASSTHROUGH, and the
    second session arming on its own charger afterwards."""
    s = Scenario("syn_plug_out_then_pilot_reset")
    s.steady(0, 60000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    s.steady(60000, 80000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1)
    s.veh(80000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(80100, 85000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1)
    # the charger reports the plug out; the timer is still reading 1
    s.steady(85000, 86000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1, chg_state=15, shutdown_src=11, vehicle_connected=0)
    # the VCU closes out: the core is in PASSTHROUGH from here
    s.veh(86000, M.CMD_ID, f_cmd_master(0x00, M.MODE_LOW_POWER))
    # the VCU's real 20-frame page-00 burst, not one frame: the session end
    # is ONE boundary and must read as one line, while the reset behind it
    # still runs on every frame (it is what stops the core arming here).
    for i in range(20):
        s.veh(86250 + i * 10, M.CMD_ID, f_cmd_master(0x00, M.MODE_STANDBY))
    s.steady(86500, 90000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=1, chg_state=15, shutdown_src=11, vehicle_connected=0,
             mainc=14)
    # and ONLY NOW does the timer return to 0 -- the boundary under test
    s.chg(90000, M.CHG_PILOT, f_chg_pilot(0))
    s.steady(90100, 95000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0, chg_state=15, shutdown_src=11, vehicle_connected=0,
             mainc=14)
    # the second session, on its own charger
    s.veh(95000, M.CMD_ID, f_cmd_master(0x01, M.MODE_CHARGER))
    s.steady(95100, 180000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    s.veh(180000, M.CMD_ID, f_cmd_master(0x01, M.MODE_LOW_POWER))
    s.steady(180100, 185000, vmax=3340, chg_max=30000, soc_half=166, evap=1,
             pilot_min=0)
    return s


SCENARIOS = [scen_override_release, scen_trip_during_override,
             scen_repeat_superseded, scen_repeat_replaced,
             scen_charger_fault, scen_stale_bms, scen_evap_clear_accept,
             scen_soc_below_gate, scen_startup_transient,
             scen_above_ceiling_start, scen_balance_hold, scen_direct_stop,
             scen_hard_ceiling_trip,
             scen_flow_drop_ends_override, scen_arm_delay_no_latch,
             scen_boot_mid_session, scen_pilot_restart_clears_safe,
             scen_charger_silence_keeps_safe, scen_boot_lock_arm_first,
             scen_charger_handle_pull, scen_charger_stop_source_3,
             scen_source_11_plug_in, scen_plug_out_then_pilot_reset]


# --- run --------------------------------------------------------------------

def write(s, outdir):
    core = M.InterposerCore()
    trace, golden = [], []
    last_state = core.state
    last_tick = -1

    def record(out, t_ms):
        for side, oid, oext, odata in out:
            golden.append("E %d %d %X %d %s"
                          % (t_ms, side, oid, 1 if oext else 0, odata.hex()))

    for t_ms, side, arb, data in sorted(s.events, key=lambda e: e[0]):
        ext = arb > 0x7FF
        trace.append("%s %d %X %d %s" % (side, t_ms, arb, 1 if ext else 0,
                                         data.hex()))
        if side == "C":
            out = core.on_charger_frame(arb, ext, data, t_ms)
        else:
            out = core.on_vehicle_frame(arb, ext, data, t_ms)
        record(out, t_ms)
        if core.state != last_state:
            golden.append("S %d %s" % (t_ms, M.STATE_NAMES[core.state]))
            last_state = core.state

        if t_ms - last_tick >= TICK_MS:
            last_tick = t_ms
            trace.append("T %d" % t_ms)
            record(core.tick(t_ms), t_ms)
            if core.state != last_state:
                golden.append("S %d %s" % (t_ms, M.STATE_NAMES[core.state]))
                last_state = core.state

    base = os.path.join(outdir, s.name)
    # Report NEW / CHANGED / UNCHANGED per file before overwriting it.
    # Regenerating cannot mask a difference BETWEEN the cores, since the C++
    # side is never regenerated -- but it can mask a change in machine.py's
    # own behaviour, and that is invisible if the write is silent. On
    # 2026-10-05 this was done by hashing the corpus by hand before and
    # after; doing it here means nobody has to remember.
    verdicts = []
    for ext, body in ((".trace", trace), (".golden", golden)):
        path = base + ext
        new = "\n".join(body) + "\n"
        if not os.path.exists(path):
            verdicts.append("NEW " + ext[1:])
        else:
            with open(path, "r", encoding="ascii", newline="\n") as f:
                verdicts.append(("UNCHANGED " if f.read() == new
                                 else "CHANGED ") + ext[1:])
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write(new)

    print("%-28s %6d trace  %6d golden  final=%-11s synth=%d modified=%d"
          % (s.name, len(trace), len(golden), M.STATE_NAMES[core.state],
             core.stats["synth"], core.stats["modified"]))
    print("      %s" % ", ".join(verdicts))
    for ev in core.events:
        print("      t=%-8d %-12s %s" % ev)
    return core


# Pairs the C++ port does NOT reproduce, for a reason recorded in
# `known-divergence/README.md` (review section B1: the port emits 20 repeat
# frames where the reference emits 19). `diff_all.sh` globs the artifacts
# folder, so a pair left loose in it fails the differential on a divergence
# that is already understood and already filed. Writing them into the
# subfolder is not bookkeeping -- regenerating with --outdir pointed at the
# artifacts folder silently put them back, and the differential went red,
# on 2026-10-03.
#
# EMPTY since 2026-10-03: B-1 is fixed (machine.py's tick() now collects
# _trip()'s output), so `syn_charger_silence_keeps_safe` and
# `syn_charger_stop_source_3` -- the two pairs that reproduced it -- are back
# in the maintained set and agree. The mechanism stays for the next one.
KNOWN_DIVERGENCE = ()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    # THE CORPUS diff_all.sh ACTUALLY READS. This used to default to
    # `<here>/golden`, which nothing reads -- diff_all.sh takes its traces
    # from notes/artifacts/interposer-firmware, as make_unit_traces.py
    # already does. Anyone regenerating "the goldens" with the obvious
    # command updated a directory the gate never looks at and got a clean
    # run from stale files. Changed 2026-10-05 after exactly that.
    ap.add_argument("--outdir", default=os.path.normpath(
        os.path.join(_HERE, "..", "..", "..", "..", "..",
                     "notes", "artifacts", "interposer-firmware")))
    a = ap.parse_args()
    if not os.path.isdir(a.outdir):
        os.makedirs(a.outdir)
    kd = os.path.join(a.outdir, "known-divergence")
    for fn in SCENARIOS:
        s = fn()
        out = a.outdir
        if s.name in KNOWN_DIVERGENCE:
            out = kd
            if not os.path.isdir(out):
                os.makedirs(out)
        write(s, out)


if __name__ == "__main__":
    main()
