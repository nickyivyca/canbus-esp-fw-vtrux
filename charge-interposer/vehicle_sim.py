"""Vehicle-side simulator: the VCU/HCU plus the A123 BMS.

Emulates everything on the powertrain bus that the Bel charger talks to:

  0x18EFC000   VCU -> charger command (6-page cyclic rotation at ~20 Hz plus
               event bursts). See protocol.py for the page map.
  0x410/0x411  BMS status, SoC, current, pack voltage, chg_done
  0x420        BMS charge/discharge current permission  <- the signal that
               decides whether a stop is genuine
  0x430        cell vmax / vmin / tmax / tmin
  0x440        contactor state, balancing, HV bus voltage

Two modes:

  --mode model    closed-loop: a real LFP pack model (pack.py) integrates the
                  current the charger reports, the BMS derives its broadcasts
                  from it, and the VCU runs its cutoff logic against them. This
                  is the only way to exercise 80 %->100 % under override, which
                  no capture contains.

The pack is charged over the PHYSICS LINK (bus.py), not by a CAN frame: the
charger publishes the current it is really delivering and the pack publishes its
terminal voltage back, standing in for the HV cable the interposer does not sit
in. Reading the current out of the charger's CAN telemetry instead would let the
interposer charge the battery by rewriting a status message.

  --mode replay   replays the VCU and BMS frames out of a real capture. Nothing
                  is modelled; the bytes are the truck's own. Use this to prove
                  the interposer does the right thing against ground truth.
                  See replay.py for the offline (no-CAN) equivalent.

The VCU cutoff reproduces both observed behaviours:

  --evap        stop at SoC == 80 (vtruxchargeafterexportchargetest-80pctcv:
                the stop landed 1.2 s after the SoC byte reached 0x50, with
                bcm_chg_max still at 300 A)
  default       stop when bcm_chg_max collapses (vtrux_charge_M1 at 2.75 A,
                vtrux_partstruck_charge at 0 A)

...and what happens AFTER that cutoff. Commanding mode 3 does not end the
session: the charger stays in state 12 at ~2.4 A while the pack balances. The
VCU never ends the session on its own (spec 7, charge-termination-behaviour.md
4b): every captured hold ended at a handle pull and every direct stop was a
charger fault. What the VCU does is REACT to the charger leaving state 12 --
flow bit 0 after ~1 s, STAND_BY ~250 ms later -- and the BMS then asserts EPO
and opens the contactors. That reaction is modelled here with the measured
delays (REACT_*, STANDBY_GAP_*, EPO_*); the endings themselves come from
charger_sim (--unplug-at, --fault-at) or from the interposer's own charger
stop (spec 5.2), which the VCU reacts to the same way.
"""

import argparse
import gc
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import can

import bus as busmod
import pack as packmod
import protocol as P

log = logging.getLogger("veh")

ROTATION_PERIOD = 0.050          # 20 Hz aggregate -> each of 6 pages at 3.3 Hz
BURST_FRAMES = 20
BURST_PERIOD = 0.050

I_CC_CMD = 19.0                  # the VCU's own CC current command (observed 18-19.5 A)
I_FLOOR = 2.4                    # it never commands below this (observed hold 2.2-2.9 A)
MAX_CATCHUP = 8                  # bound on frames emitted per loop iteration
PHYS_PERIOD = 0.020              # physics-link update interval, simulated s

# Termination, per notes/charge-termination-behaviour.md section 4b (raw
# captures, 0.1 ms): the VCU never initiates one. Mode 3 CHARGER_LOW_POWER is a
# HOLD with no exit of its own -- every captured hold ended when the handle was
# pulled. What the VCU does is REACT to the charger leaving state 12, with these
# measured delays (artifacts/interposer-firmware/termination_ordering_raw.txt):
REACT_HOLD_S = 1.0        # charger out of 12 -> flow 0, from a mode-3 hold (1.02-1.13 s)
REACT_DIRECT_S = 0.5      # same, in CHARGER mode (charger faults: 0.23-0.58 s)
# Spec 9 (user, 2026-10-07): the reaction depends on the charger's
# shutdownSource, from section 6's handle-pull table -- not on the VCU's
# mode, which is what REACT_HOLD_S / REACT_DIRECT_S keyed on until
# 2026-10-08 (they are kept only for the comments that cite them). Each
# delay is a point inside the measured range:
REACT_BY_SOURCE = {
    11: 0.95,   # handle pull, once vehicleConnected has dropped (0.8-1.1 s)
    3: 0.45,    # charger stops first (0.2-0.7 s)
    14: 2.9,    # charger fault (2.9 s)
    4: 0.15,    # battery-led: the BMS's EPO leads the flow drop by ~0.15 s
}
# Spec 9 (user, 2026-10-08): source 11 with the plug still IN
# (vehicleConnected 1) -- the original Bel unit -- the VCU keeps flow on, as
# it did for 84 s in `chargingafterturningaroundandnotusingextension`, then
# ends the session itself with flow 0 and STAND_BY.
SOURCE11_PLUG_IN_S = 84.0
# Spec 9 (A5): how long after the handle goes back in the VCU starts the
# NEXT session. This is a BENCH NUMBER, not a measurement -- no capture in
# the corpus contains two sessions on one recording, which is precisely
# why the restart had to be built rather than replayed. It only has to be
# long enough that the charger is announcing itself before the VCU asks
# for current; the behaviour under test is the core's, not this delay's.
RESTART_AFTER_REPLUG = 2.0
STANDBY_GAP_HOLD = 0.25   # flow 0 -> STAND_BY out of the hold (0.248-0.261 s)
STANDBY_GAP_DIRECT = 0.65 # flow 0 -> STAND_BY in CHARGER mode (0.65-0.70 s)
EPO_AFTER_STANDBY_HOLD = 0.03    # STAND_BY -> bcm_epo (0.01-0.04 s)
EPO_AFTER_STANDBY_DIRECT = 0.48  # (0.43-0.48 s)
MAINC_AFTER_EPO = 0.04           # bcm_epo -> 0x440 contactor state 14 (-0.01..0.04 s)
HVIL_AFTER_FLOW_DROP = 0.03      # VCU flow 0 -> bcm_hvil_mon 0 (27 ms, chargeagain3;
                                 # ~200 ms before the STAND_BY in both long captures)
# Spec 9: the variant. In 5 of the 13 source-11 handle pulls in the corpus
# HVIL opened up to 20 ms BEFORE the VCU's flow drop, not after it
# (handle_pull_sequence.txt). Under the old teardown window that tripped
# "HVIL open" mid-override; under spec 6 the charger's plug-out report has
# already made the core transparent about a second earlier, so it must not
# trip. This is the case that proves the plug-out rule earns its keep.
HVIL_BEFORE_FLOW_DROP = 0.02
MAINC_CLOSED = (11, 12)   # 0x440 states with the main contactors closed (machine.py)
AUX_A_DEFAULT = 2.4       # HV-side draw of the 12 V system: charger reported
                          # -2.9 A in the hold while the pack saw +0.31-0.49 A



# A sender that stalls for longer than the interposer's staleness window
# (500 ms of SIMULATED time, i.e. 500/--time-scale ms of wall clock) trips the
# bench with "frames stale" through no fault of the state machine. Log every
# wall-clock stall over this threshold, with the GC collection counters, so a
# spurious trip can be traced to its cause instead of being blamed on load.
STALL_WARN_S = 0.040


def _gc_collections():
    return tuple(s["collections"] for s in gc.get_stats())


def report_stall(dt_wall, t_sim, prev_gc, phases=None):
    """Call once per loop with the wall dt; returns the current GC counters.

    `phases` is the previous iteration's per-phase wall time, so a stall can be
    attributed to the drain, the model, the sends or the sleep rather than
    just reported.
    """
    cur = _gc_collections()
    if dt_wall > STALL_WARN_S:
        delta = tuple(c - p for c, p in zip(cur, prev_gc))
        where = ""
        if phases:
            where = "  phases(ms): " + " ".join(
                "%s=%.1f" % (k, v * 1000.0) if isinstance(v, float) else "%s=%d" % (k, v)
                for k, v in phases.items())
        log.warning("STALL %.0f ms wall at sim t=%.0fs  gc collections during "
                    "it gen0/1/2=%s%s", dt_wall * 1000.0, t_sim, delta, where)
    return cur

class VCU(object):
    """The Via VCU's charge-control half.

    Two things it does on its own: command the charge, and call the cutoff
    (the evap 80 % ceiling, or the BMS's permission collapsing). Ending the
    session is NOT one of them -- it has never been observed to (spec 7). It
    REACTS to the charger leaving state 12, for a handle pull or a charger
    fault, by dropping the flow bit and then commanding STAND_BY with the
    measured delays. That reaction is also what the interposer's own charger
    stop relies on (spec 5.3).
    """

    def __init__(self, evap=False, cutoff_ilim_delay=155.0, seed=7,
                 hold_max_s=0.0, start_soc=50.0):
        self.evap = evap
        self.cutoff_ilim_delay = cutoff_ilim_delay
        # A charge that starts above the ceiling with evap pending: the truck
        # commanded CHARGER with a 0.0 A setpoint from the first frame and
        # never raised it before the handle pull at 58 s (spec 7, the 83 %
        # start). Nothing past that point has been observed (spec 10 q4);
        # here the setpoint stays 0 until the hold walk brings it to the
        # I_FLOOR hold value after cutoff_ilim_delay, like the 80 % hold.
        # That continuation is an ASSUMPTION of the bench, not a measurement.
        self.above_ceiling_start = bool(evap and start_soc >= 80.0)
        self.rng = random.Random(seed)
        self.mode = P.MODE_EXPORT
        self.flow = 0
        self.complete_t = None       # sim time the low-power burst was sent
        self.hold_max_s = hold_max_s
        self.no_standby = False      # spec 9 bench variant, see _standby
        # Spec 9 (A5) bench variant: start a NEW session when the handle goes
        # back in. Off by default -- the modelled VCU otherwise ends exactly
        # once, which is what every single-session capture shows.
        self.restart_on_replug = False
        self.chg_veh_conn = None     # last vehicleConnected the charger showed
        self.restart_due = None      # sim time the next session starts
        self._restarted = False      # one-shot, consumed by the main loop
        self.flow_off_t = None       # sim time the flow bit was dropped
        self.flow_off_mode = None    # mode the flow was dropped in
        self.standby_t = None        # sim time STAND_BY was commanded
        self.chg_seen_12 = False     # charger has been seen delivering
        self.chg_out_since = None    # sim time the charger last left state 12
        self.closeout_due = None     # sim time the flow drop is due
        self.closeout_why = None
        self.aux_complete = False
        self.ilim = 0.0 if self.above_ceiling_start else I_CC_CMD
        self._rot = 0
        self._soc_80_since = None
        self._chgmax_low_since = None
        self.pending = []            # event frames still to burst out
        self.last_view = ""          # pack summary for the close-out log line

    def start(self, t):
        self.flow = 1
        self.mode = P.MODE_CHARGER
        self._burst(P.enc_master(1, P.MODE_CHARGER))

    def _burst(self, payload):
        # A new master-page announcement REPLACES whatever burst is still
        # going out: the VCU announces its current state, and an obsolete one
        # must not delay it. Queuing them (the earlier behaviour) put the
        # STAND_BY burst a full second behind the flow-0 burst, so the BMS's
        # EPO reached the bus before STAND_BY -- the opposite of the measured
        # order (flow 0, STAND_BY +250 ms, EPO +30 ms), and enough to make the
        # interposer trip on EPO during a handle pull it should simply obey.
        self.pending = [payload] * BURST_FRAMES

    def on_charger(self, t, state, shutdown_src=None, veh_conn=None):
        """What the VCU sees of the charger: its status frames, forwarded by
        whatever sits in between. A departure from state 12 during a charge
        starts the reaction clock; a return to 12 before it elapses (the
        charger's own single-frame blips, or a fault retry) cancels it.
        """
        # The replug, watched before anything else: a session that has
        # already closed out is not interested in the charger's state, but it
        # is very interested in the handle going back in.
        prev_conn = self.chg_veh_conn
        if veh_conn is not None:
            self.chg_veh_conn = veh_conn
            if (self.restart_on_replug and prev_conn == 0 and veh_conn == 1
                    and self.standby_t is not None
                    and self.restart_due is None):
                self.restart_due = t + RESTART_AFTER_REPLUG
                log.info("VCU: handle back in at t=%.1fs, starting a new "
                         "session in %.1f s", t, RESTART_AFTER_REPLUG)

        if state == P.CHG_STATE_CHARGING:
            self.chg_seen_12 = True
            if self.chg_out_since is not None and self.flow_off_t is None:
                log.info("VCU: charger back in state 12 after %.2f s; "
                         "reaction cancelled", t - self.chg_out_since)
                self.closeout_due = None
            self.chg_out_since = None
            return
        if not self.chg_seen_12 or self.flow != 1 or self.flow_off_t is not None:
            return
        # The real VCU does NOT end a session on the charger's own benign
        # dips, and it cannot be modelled by the reaction delay alone.
        #
        # Every session in the corpus has a start-up dip ~0.4 s after the
        # charger first reaches 12, lasting ~1.2 s with shutdownSource 0, and
        # the charge goes on (71 sessions, charger_startup_dips.txt). A Low
        # Power / CHARGER change resets the charger for 0.8-1.2 s with
        # source 1, and the charge goes on through that too. Both are longer
        # than REACT_DIRECT_S (0.5 s, measured on charger FAULTS), so a model
        # that keys only off "left state 12" ends the session on its own
        # start-up dip -- which is exactly what happened when the dips were
        # added to charger_sim on 2026-10-03.
        #
        # What separates them is the reason: source 0 and 1 are the charger
        # restarting itself, 3/4/11/14 are a real ending. Raised with the
        # review session; the spec 9 text says "reacts to the charger leaving
        # state 12 exactly as measured", and these two measurements can only
        # both hold if the VCU discriminates.
        if shutdown_src in (0, 1):
            return
        if shutdown_src not in REACT_BY_SOURCE:
            # Spec 9: "a departure with no source is a simulator error, not a
            # case". Said loudly, once per departure, and not reacted to.
            if self.chg_out_since is None:
                self.chg_out_since = t
                log.error("VCU: SIMULATOR ERROR -- charger left state 12 "
                          "(now %d) with shutdownSource %r, which no spec 9 "
                          "reaction covers; not reacting", state, shutdown_src)
            return
        if shutdown_src == 11 and self.chg_veh_conn == 1:
            due = t + SOURCE11_PLUG_IN_S
            why = ("charger stopped with source 11 and the plug still in; "
                   "VCU ending the session itself")
        else:
            due = t + REACT_BY_SOURCE[shutdown_src]
            why = ("charger left state 12, now %d, shutdownSource %d"
                   % (state, shutdown_src))
        if self.chg_out_since is None:
            self.chg_out_since = t
            self.closeout_due = due
            self.closeout_why = why
            log.info("VCU: charger left state 12 (now %d, shutdownSource %d) "
                     "at t=%.1fs; dropping energy flow in %.2f s",
                     state, shutdown_src, t, due - t)
        elif self.closeout_due is not None and due < self.closeout_due:
            # e.g. source 11 with the plug in, and then the plug comes out:
            # the handle-pull reaction runs from that edge
            self.closeout_due = due
            self.closeout_why = why
            log.info("VCU: %s at t=%.1fs; dropping energy flow in %.2f s",
                     why, t, due - t)

    def update(self, t, dt, soc_pct, chg_max, spread_mv=None, vmin=None):
        """Run the cutoff logic, the reaction to the charger, and the Ilim
        regulator."""
        self.last_view = ("soc=%.1f spread=%s mV vmin=%s V"
                          % (soc_pct,
                             "n/a" if spread_mv is None else "%.1f" % spread_mv,
                             "n/a" if vmin is None else "%.3f" % vmin))
        if self.mode == P.MODE_CHARGER and self.flow_off_t is None:
            if self.evap:
                if int(soc_pct) >= 80:
                    if self._soc_80_since is None:
                        self._soc_80_since = t
                    elif t - self._soc_80_since >= 1.2:
                        self._complete(t)
                else:
                    self._soc_80_since = None
            else:
                if chg_max <= 3.0:
                    if self._chgmax_low_since is None:
                        self._chgmax_low_since = t
                    elif t - self._chgmax_low_since >= 2.0:
                        self._complete(t)
                else:
                    self._chgmax_low_since = None

        if self.restart_due is not None and t >= self.restart_due:
            self._restart(t)

        # The reaction (spec 7, termination note 4b): flow 0 after the measured
        # delay, STAND_BY after the measured gap. Nothing else ends a session.
        if (self.closeout_due is not None and self.flow_off_t is None
                and t >= self.closeout_due):
            self._drop_flow(t, self.mode, self.closeout_why)
        if self.flow_off_t is not None and self.standby_t is None:
            gap = (STANDBY_GAP_HOLD if self.flow_off_mode == P.MODE_LOW_POWER
                   else STANDBY_GAP_DIRECT)
            if t - self.flow_off_t >= gap:
                self._standby(t)

        # A bench bound, not a model of the truck: end a hold after hold_max_s
        # so a run cannot sit in it forever. Uses the same reaction path.
        if (self.hold_max_s and self.mode == P.MODE_LOW_POWER
                and self.flow_off_t is None and self.closeout_due is None
                and self.complete_t is not None
                and t - self.complete_t >= self.hold_max_s):
            log.info("VCU: hold cap (--hold-max-s %.0f) reached -- bench "
                     "bound, not the truck", self.hold_max_s)
            self.closeout_due = t
            self.closeout_why = "hold cap"

        # aux pages flip a few seconds after the master burst
        if (self.complete_t is not None and not self.aux_complete
                and t - self.complete_t >= 3.0):
            self.aux_complete = True

        # Ilim regulator. During CC it sits at ~19 A (above what the charger can
        # actually deliver, so it is not binding); once bcm_chg_max falls below
        # that it tracks it down, with a floor it never goes under.
        if self.mode == P.MODE_STANDBY:
            target = 0.0
        elif self.mode == P.MODE_LOW_POWER:
            if t - self.complete_t < self.cutoff_ilim_delay:
                target = self.ilim                      # held, then walked down
            else:
                # the measured ramp: 19.8 -> 0 A over ~21 s starting 155 s
                # after the mode command, then the 0/2.9 A dither whose
                # delivered average is the I_FLOOR hold (spec 4, 7)
                walk = (t - self.complete_t - self.cutoff_ilim_delay) / 20.0
                target = max(I_FLOOR, min(self.ilim, I_CC_CMD * (1.0 - walk)))
        elif self.above_ceiling_start:
            target = 0.0                                # never learned a CC command
        else:
            target = max(I_FLOOR, min(I_CC_CMD, chg_max))
        self.ilim += (target - self.ilim) * min(1.0, dt * 2.0)
        self.ilim = max(0.0, self.ilim)

    def _complete(self, t):
        """The cutoff: mode 3, the hold. The charger keeps delivering ~2.4 A
        and the session does not end here (spec 7)."""
        why = "evap 80%% ceiling" if self.evap else "BMS chg_max collapsed"
        self.mode = P.MODE_LOW_POWER
        self.complete_t = t
        self._burst(P.enc_master(self.flow, P.MODE_LOW_POWER))
        log.info("VCU: commanding CHARGER_LOW_POWER (%s)", why)

    def _drop_flow(self, t, mode, why):
        """chgcmd_enable -> 0 with the mode left as it was. Always the VCU's
        first stop action, always a reaction (termination note 4b). The pack
        view is logged here because this is the instant that fixes the
        termination point for the scenario checks."""
        self.flow = 0
        self.flow_off_t = t
        self.flow_off_mode = mode
        self.mode = mode
        self._burst(P.enc_master(0, mode))
        log.info("VCU: dropping energy flow, mode left at %s (%s) %s",
                 P.MODE_NAMES.get(mode, mode), why, self.last_view)

    def _restart(self, t):
        """A new session on the same bench run (spec 9, A5).

        Every field the first session latched goes back to where `start`
        found it. The pack does NOT reset -- the truck keeps the charge it
        took -- so the second session begins at whatever SoC the first one
        reached, which is the realistic case: a charge that failed at one
        handle and is finished at another."""
        self.restart_due = None
        self.flow_off_t = None
        self.flow_off_mode = None
        self.standby_t = None
        self.chg_seen_12 = False
        self.chg_out_since = None
        self.closeout_due = None
        self.closeout_why = None
        self.complete_t = None
        self.aux_complete = False
        self._soc_80_since = None
        self._chgmax_low_since = None
        self.ilim = I_CC_CMD
        self._restarted = True
        log.warning("VCU: NEW SESSION at t=%.0fs (handle back in)", t)
        self.start(t)

    def consume_restart(self):
        """True once per restart, so the BMS can be reset with it."""
        r = self._restarted
        self._restarted = False
        return r

    def _standby(self, t):
        """STAND_BY, the measured gap after the flow drop: ~250 ms out of the
        hold, ~650 ms in CHARGER mode. (An earlier reading from InfluxDB's 1 s
        timestamps had the hold ending in a single step; the raw captures show
        two.)"""
        if self.standby_t is not None:
            return
        if self.no_standby:
            # Spec 9 (A5): a bench variant, not a model of the truck. 11 of
            # the 49 terminations in the survey do not end on a STAND_BY
            # (possibly captures cut short), and the core must still start a
            # new session cleanly on the pilot timer alone. Suppressing it
            # here is the only way to exercise that boundary in isolation.
            self.standby_t = t
            log.info("VCU: STAND_BY suppressed (--no-standby-on-unplug)")
            return
        self.standby_t = t
        self.flow = 0
        self.mode = P.MODE_STANDBY
        self._burst(P.enc_master(0, P.MODE_STANDBY))
        log.info("VCU: commanding STAND_BY (session over)")

    def evap_frame(self):
        """0x649 VCM_EvapControl as the truck broadcasts it (~20 Hz): B4 bit 7
        is vcm_evap_active, up for the whole session when a purge is pending
        (spec 3, 7). The other bytes are not modelled (the DBC says their
        roles are unresolved in charge mode); the counter stays 0."""
        return bytes((0, 0, 0, 0, 0x80 if self.evap else 0, 0, 0, 0))

    def next_frame(self, soc_pct):
        """One frame of the 20 Hz stream: pending event frames first."""
        if self.pending:
            return self.pending.pop(0)
        key = P.CYCLIC_ROTATION[self._rot % len(P.CYCLIC_ROTATION)]
        self._rot += 1
        if key == P.PAGE_SETPOINT:
            dither = self.rng.uniform(-0.25, 0.25)
            counts = int(round(max(0.0, self.ilim + dither) / P.UNIT))
            return P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, counts)
        if key == P.PAGE_SOC:
            return P.enc_soc(int(soc_pct))
        if key == P.PAGE_03_02:
            return P.enc_03_02(self.aux_complete)
        if key == P.PAGE_03_07:
            return P.enc_03_07(self.aux_complete)
        if key == P.PAGE_03_04:
            return P.CONST_03_04
        return P.CONST_03_06


class BMS(object):
    """The A123 BMS half: derives its broadcasts from the pack model."""

    def __init__(self, pk, stuck_chg_max=None, taper_shift_v=0.0,
                 fault_at=0.0, fault_kind=None, hvil_early=False):
        self.pack = pk
        self.stuck_chg_max = stuck_chg_max
        self.taper_shift_v = taper_shift_v
        self.chg_done = 0
        self.mainc_stat = 11
        self.epo = 0
        self.alarm = 0
        self.hvil = 1
        self.overvolt = 0
        # fault injection (spec 9): one BMS fault class at a chosen sim time
        self.fault_at = fault_at
        self.fault_kind = fault_kind
        self.fault_done = False
        # spec 9: open HVIL 20 ms BEFORE the VCU's flow drop instead of 30 ms
        # after it (HVIL_BEFORE_FLOW_DROP)
        self.hvil_early = hvil_early
        # the close-out after the VCU's STAND_BY (termination note 4b)
        self.epo_due = None
        self.mainc_due = None

    def restart(self):
        """Spec 9 (A5): the close-out undone for a new session.

        The teardown latches -- EPO, the open contactors, the HVIL read --
        all belong to the session that ended. Leaving any of them set would
        stop the core arming in the next session for a reason that has
        nothing to do with what the scenario is testing."""
        self.chg_done = 0
        self.mainc_stat = 11
        self.epo = 0
        self.hvil = 1
        self.epo_due = None
        self.mainc_due = None
        log.info("BMS: contactors re-closed, EPO and HVIL cleared "
                 "(new session)")

    @property
    def contactors_closed(self):
        return self.mainc_stat in MAINC_CLOSED

    def update(self, t, vcu):
        # chg_done follows the VCU's command, not the pack: it latched at
        # 80.5 % SoC with 300 A still granted in the evap capture (spec 7).
        if vcu.mode == P.MODE_LOW_POWER or (vcu.flow == 0 and vcu.complete_t):
            self.chg_done = 1

        # Teardown (spec 6, 7): HVIL reads open ~30 ms after the VCU's flow
        # drop -- before its STAND_BY -- and the interposer must not trip on
        # it; then EPO and the contactors after the STAND_BY.
        if (vcu.flow_off_t is not None and self.hvil
                and t >= vcu.flow_off_t + HVIL_AFTER_FLOW_DROP):
            self.hvil = 0
            log.info("BMS: HVIL monitor open (teardown)")
        # The variant: HVIL leads the flow drop. The VCU has already
        # scheduled the drop in `closeout_due`, so the BMS can be made to
        # open just before it -- which is the ordering the corpus shows in
        # 5 of 13 handle pulls.
        elif (self.hvil_early and self.hvil and vcu.closeout_due is not None
              and vcu.flow_off_t is None
              and t >= vcu.closeout_due - HVIL_BEFORE_FLOW_DROP):
            self.hvil = 0
            log.info("BMS: HVIL monitor open %.0f ms BEFORE the flow drop "
                     "(spec 9 variant)", HVIL_BEFORE_FLOW_DROP * 1000)
        # Close-out: EPO then the contactors, the measured delays after the
        # VCU's STAND_BY (hold path ~30 ms, direct path ~480 ms; then ~40 ms).
        if vcu.standby_t is not None and self.epo_due is None:
            hold = vcu.flow_off_mode == P.MODE_LOW_POWER
            self.epo_due = vcu.standby_t + (EPO_AFTER_STANDBY_HOLD if hold
                                            else EPO_AFTER_STANDBY_DIRECT)
            self.mainc_due = self.epo_due + MAINC_AFTER_EPO
        if self.epo_due is not None and t >= self.epo_due and not self.epo:
            self.epo = 1
            log.info("BMS: EPO asserted (session close-out)")
        if (self.mainc_due is not None and t >= self.mainc_due
                and self.contactors_closed):
            self.mainc_stat = 14
            log.info("BMS: contactors opening (0x440 state 14)")

        if (self.fault_at and self.fault_kind and not self.fault_done
                and t >= self.fault_at):
            self.fault_done = True
            if self.fault_kind == "overvolt":
                self.overvolt = 1
            elif self.fault_kind == "alarm":
                self.alarm = 3
            elif self.fault_kind == "epo":
                self.epo = 1
            elif self.fault_kind == "contactor":
                self.mainc_stat = 14
            elif self.fault_kind == "hvil":
                self.hvil = 0
            log.warning("BMS: INJECTED FAULT %s at t=%.0fs", self.fault_kind, t)

    @property
    def chg_max(self):
        """Normally derived from max cell voltage.

        `stuck_chg_max` pins it instead, simulating a BMS whose charge-current
        permission fails to taper at the top of charge. That is the only way to
        reach the interposer's own hard cell-voltage ceiling: while the BMS
        behaves, bcm_chg_max going to zero already clamps our taper to zero, so
        the ceiling is unreachable -- which is itself worth knowing.
        """
        if self.stuck_chg_max is not None:
            return self.stuck_chg_max
        # `taper_shift_v` evaluates the permission curve at a higher cell
        # voltage than the pack is really at, so the BMS calls the pack full
        # EARLIER than the interposer's own thresholds expect -- as a BMS with
        # one high cell, or a different calibration, would. The interposer must
        # then terminate at the BMS's point, not its own. Verification round 2,
        # step 1.
        return packmod.bms_chg_max(self.pack.vmax + self.taper_shift_v)

    def frames(self):
        pk = self.pack
        out = {}
        out[0x410] = P.encode(P.EPRI, 0x410, {
            "bcm_ready": 1, "bcm_epo": self.epo, "bcm_on_plug": 1,
            "bcm_hvil_mon": self.hvil, "bcm_alarm": self.alarm,
            "bcm_mainc_stat": min(3, self.mainc_stat),
            "bcm_chgc_stat": 1, "bcm_cpwr_cmd": 1,
            "bcm_soc": min(100.0, pk.soc_pct), "bcm_ibat": pk.current_a,
            "bcm_vbat": min(1023.0, pk.pack_v), "bcm_chg_done": self.chg_done,
            "bcm_gfd": 1000.0})
        out[0x420] = P.encode(P.EPRI, 0x420, {
            "bcm_chg_max": min(1023.0, self.chg_max), "bcm_dis_max": 612.0,
            "bcm_cell_overvolt": 1 if (pk.vmax > 3.75 or self.overvolt) else 0,
            "bcm_cell_undervolt": 1 if pk.vmin < 2.0 else 0})
        out[0x430] = P.encode(P.EPRI, 0x430, {
            "bcm_chga_ena": 1, "bcm_mod_ena": 1, "bcm_veh_mon": 1,
            "bcm_chga_mon": 1,
            "bcm_cell_vmax": min(3.799, pk.vmax),
            "bcm_cell_vmin": min(3.799, pk.vmin),
            "bcm_cell_tmax": pk.temp_c, "bcm_cell_tmin": pk.temp_c - 2,
            "bcm_lvbat": 13.4})
        out[0x440] = P.encode(P.EPRI, 0x440, {
            "bcm_mainc_stat": self.mainc_stat,
            "bcm_balancing": 1 if pk.balancing_cnt else 0,
            "bcm_balancing_cnt": min(253, pk.balancing_cnt),
            "bcm_vbus_pos": min(1023.0, pk.pack_v),
            "bcm_vbus_neg": 0.0, "bcm_fgd": 0.0})
        return out


# True peaks, tracked every model step. The periodic status rows are for a
# human reading the log; THESE are what the scenario harness asserts on. A
# maximum over samples taken every --report-every simulated seconds is a
# sampling artefact, and on a session shorter than one report period it is
# just the starting value -- which is how `evap-bypass`, the control run for
# the whole exercise, came to report "SoC stayed <= 82.5 % (peak 79.0)" on a
# run that reached 80.3 %.
#
# Module-level so main()'s `finally` can report it on every exit path,
# including the CTRL_BREAK the scenario harness sends (which
# bus.install_break_handler turns into a KeyboardInterrupt).
PEAK = {"i": None, "vmax": None, "soc": None, "n": 0, "done": False}


def report_peaks():
    """One line, once, with the true maxima over the whole run."""
    if PEAK["n"] == 0 or PEAK["done"]:
        return
    PEAK["done"] = True
    log.info("peaks over %d model steps: I=%.2fA vmax=%.3fV soc=%.1f%%",
             PEAK["n"], PEAK["i"], PEAK["vmax"], PEAK["soc"])


def run_model(args, b, phys):
    pk = packmod.Pack(soc_pct=args.soc, imbalance_pct=args.imbalance,
                      outlier_cell=args.outlier_cell,
                      outlier_pct=args.outlier_pct,
                      balance_model=args.balance_model, seed=args.seed)
    vcu = VCU(evap=args.evap, cutoff_ilim_delay=args.cutoff_ilim_delay,
              hold_max_s=args.hold_max_s, start_soc=args.soc)
    vcu.no_standby = args.no_standby_on_unplug
    vcu.restart_on_replug = args.restart_on_replug
    bms = BMS(pk, stuck_chg_max=args.bms_stuck_chg_max,
              taper_shift_v=args.bms_taper_shift_mv / 1000.0,
              fault_at=args.bms_fault_at, fault_kind=args.bms_fault_kind,
              hvil_early=args.hvil_before_flow_drop)

    charger_i = 0.0             # last current the charger reported (A, + = charging)
    t = 0.0                     # simulated seconds
    wall0 = time.time()
    next_cmd = 0.0
    next_bms = 0.0
    next_phys = 0.0
    next_report = 0.0
    vcu.start(t)
    log.info("model start: soc=%.1f%% imbalance=%.2f%% evap=%s time-scale=%g%s",
             pk.soc_pct, args.imbalance, args.evap, args.time_scale,
             "  [above-ceiling start: 0 A setpoint]"
             if vcu.above_ceiling_start else "")

    last_wall = time.time()
    gc_prev = _gc_collections()
    ph = {}
    # Spec 8.2 diagnostics seen on the vehicle segment, for run_scenario.py to
    # decode (C2). Keyed by id: the simulated time each was last written.
    diag_last_t = {}
    diag_last_state = None
    peak = PEAK
    DIAG_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
    DIAG_LOG_PERIOD_S = 5.0
    while True:
        now_wall = time.time()
        dt_wall = now_wall - last_wall
        last_wall = now_wall
        gc_prev = report_stall(dt_wall, t, gc_prev, ph)
        ph = {}
        _pt = time.perf_counter()
        dt = busmod.unfreeze(dt_wall) * args.time_scale
        t += dt

        # The pack is charged over the physics link, not by CAN telemetry.
        n_b = 0
        while True:
            msg = b.recv(timeout=0.0)
            if msg is None:
                break
            n_b += 1
            if msg.arbitration_id in DIAG_IDS and len(msg.data) >= 8:
                # The interposer's own telemetry (spec 8.2). The VCU ignores
                # it; we record it so the scenario can judge the diagnostics
                # from the wire rather than from the bench's serial text.
                fid = msg.arbitration_id
                body = bytes(msg.data)
                changed = (fid == 0x7F4 and body[2] != diag_last_state)
                if changed or t - diag_last_t.get(fid, -1e9) >= DIAG_LOG_PERIOD_S:
                    diag_last_t[fid] = t
                    if fid == 0x7F4:
                        diag_last_state = body[2]
                    log.info("diagframe t=%.1f id=0x%03X data=%s",
                             t, fid, body.hex())
            # The VCU watches the charger's status page (forwarded by whatever
            # sits between): that is what it reacts to (spec 5.3, 7).
            if (msg.arbitration_id == 0x18FFD4C0 and len(msg.data) >= 8
                    and msg.data[0] == 0):
                dec = P.decode(P.BEL, 0x18FFD4C0, bytes(msg.data))
                st = dec.get("BELINV_state")
                src = dec.get("BELINV_shutdownSource")
                vc = dec.get("BELINV_vehicleConnected")
                if st is not None:
                    vcu.on_charger(t, int(st),
                                   None if src is None else int(src),
                                   None if vc is None else int(vc))
        n_p = 0
        while True:
            msg = phys.recv(timeout=0.0)
            if msg is None:
                break
            n_p += 1
            if msg.arbitration_id == busmod.PHYS_CURRENT_ID:
                charger_i = busmod.decode_phys(bytes(msg.data))
        ph["recv"] = time.perf_counter() - _pt
        ph["n_bus"] = n_b
        ph["n_phys"] = n_p
        _pt = time.perf_counter()

        # The pack sees the charger's current less the 12 V system's HV-side
        # draw (spec 9), and nothing at all once the contactors are open.
        i_pack = (charger_i - args.aux_a) if bms.contactors_closed else 0.0
        pk.step(dt, i_pack)
        peak["n"] += 1
        for key, v in (("i", charger_i), ("vmax", pk.vmax),
                       ("soc", pk.soc_pct)):
            if peak[key] is None or v > peak[key]:
                peak[key] = v
        bms.update(t, vcu)
        vcu.update(t, dt, pk.soc_pct, bms.chg_max, pk.spread_mv, pk.vmin)
        if vcu.consume_restart():
            bms.restart()
        ph["model"] = time.perf_counter() - _pt
        _pt = time.perf_counter()

        # Bus timing is expressed in SIMULATED time, so every timing
        # relationship on the wire is preserved exactly at any --time-scale;
        # accelerating just means more frames per wall second. Catch-up is
        # bounded so a scheduling hiccup cannot spiral.
        n = 0
        while t >= next_cmd and n < MAX_CATCHUP:
            b.send(can.Message(arbitration_id=P.CMD_ID, is_extended_id=True,
                               data=vcu.next_frame(pk.soc_pct)))
            b.send(can.Message(arbitration_id=0x649, is_extended_id=False,
                               data=vcu.evap_frame()))
            next_cmd += ROTATION_PERIOD
            n += 1
        if n >= MAX_CATCHUP:
            next_cmd = t + ROTATION_PERIOD
        n = 0
        while t >= next_bms and n < MAX_CATCHUP:
            for fid, data in bms.frames().items():
                b.send(can.Message(arbitration_id=fid, is_extended_id=False,
                                   data=data))
            next_bms += 0.100
            n += 1
        if n >= MAX_CATCHUP:
            next_bms = t + 0.100

        if t >= next_phys:
            phys.send(can.Message(arbitration_id=busmod.PHYS_VOLTAGE_ID,
                                  is_extended_id=False,
                                  data=busmod.encode_phys(pk.pack_v)))
            next_phys = t + PHYS_PERIOD
        ph["send"] = time.perf_counter() - _pt
        _pt = time.perf_counter()

        if t >= next_report:
            log.info("t=%7.0fs soc=%5.1f vmax=%5.3f vmin=%5.3f spread=%5.1fmV "
                     "bal=%3d chg_max=%6.1fA ilim=%5.2fA I=%6.2fA V=%6.1f mode=%d",
                     t, pk.soc_pct, pk.vmax, pk.vmin, pk.spread_mv,
                     pk.balancing_cnt, bms.chg_max, vcu.ilim, charger_i,
                     pk.pack_v, vcu.mode)
            next_report = t + args.report_every

        if args.max_sim_s and t >= args.max_sim_s:
            log.info("reached --max-sim-s, stopping")
            return
        if (args.stop_on_done and vcu.standby_t is not None
                and t - vcu.standby_t >= args.post_standby_s):
            log.info("VCU commanded STAND_BY, session over, stopping "
                     "(sim %.0f s, wall %.1f s)", t, time.time() - wall0)
            return
        ph["tail"] = time.perf_counter() - _pt
        _pt = time.perf_counter()
        time.sleep(0.0005)
        ph["sleep"] = time.perf_counter() - _pt


def main():
    busmod.install_break_handler()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    busmod.add_bus_args(ap, default_channel=busmod.VEHICLE_SEGMENT,
                        help_role="the vehicle segment")
    busmod.add_bus_args(ap, default_channel=busmod.PHYSICS_SEGMENT,
                        prefix="phys-", help_role="the HV cable (physics link)")
    ap.add_argument("--mode", choices=("model", "replay"), default="model")
    ap.add_argument("--log", help="replay mode: capture file (BUSMASTER or candump)")
    ap.add_argument("--evap", action="store_true",
                    help="model the evap-purge 80 %% SoC ceiling")
    ap.add_argument("--soc", type=float, default=50.0, help="starting SoC %%")
    ap.add_argument("--imbalance", type=float, default=0.4,
                    help="cell-to-cell charge spread, %% of capacity "
                         "(parts truck was ~1.0 with a 2.5 %% outlier)")
    ap.add_argument("--outlier-cell", type=int, default=None)
    ap.add_argument("--outlier-pct", type=float, default=0.0)
    ap.add_argument("--balance-model", choices=("hybrid", "absolute"),
                    default="hybrid")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bms-stuck-chg-max", type=float, default=None,
                    metavar="AMPS",
                    help="fault injection: pin bcm_chg_max at this value "
                         "instead of tapering it with cell voltage, "
                         "simulating a BMS whose charge-current permission "
                         "fails at the top of charge")
    ap.add_argument("--bms-taper-shift-mv", type=float, default=0.0,
                    help="evaluate the BMS chg_max taper at vmax plus this "
                         "many mV, so the BMS withdraws permission EARLIER "
                         "than the interposer's own release point. +50 makes "
                         "it call the pack full at a ~3.55 V leader cell. "
                         "The interposer must follow chg_max down and stop "
                         "there. 0 (default) = the fitted curve as observed.")
    ap.add_argument("--time-scale", type=float, default=20.0,
                    help="simulated seconds per wall second (default 60)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay mode: playback speed multiplier, 0 = as fast "
                         "as possible")
    ap.add_argument("--bus-filter", type=int, default=None,
                    help="replay mode: only replay this log bus/channel")
    ap.add_argument("--cutoff-ilim-delay", type=float, default=155.0,
                    help="seconds the VCU holds Ilim after the low-power command before "
                         "walking it down (observed 155 s)")
    ap.add_argument("--hold-max-s", type=float, default=0.0,
                    help="end a mode-3 hold after at most this many simulated "
                         "seconds, through the VCU's normal reaction path. 0 "
                         "(default) means never: the truck's VCU has no hold "
                         "exit of its own (spec 7). A bench bound only. The "
                         "truck's real endings are charger_sim's --unplug-at "
                         "and --fault-at, which this VCU reacts to.")
    ap.add_argument("--bms-fault-at", type=float, default=0.0,
                    help="fault injection: assert one BMS fault class at this "
                         "simulated time (0 = never); see --bms-fault-kind")
    ap.add_argument("--bms-fault-kind", default=None,
                    choices=("overvolt", "alarm", "epo", "contactor", "hvil"),
                    help="which BMS fault --bms-fault-at asserts: "
                         "bcm_cell_overvolt, bcm_alarm TYPE3, bcm_epo, the "
                         "0x440 contactor state leaving 12, or HVIL open")
    ap.add_argument("--no-standby-on-unplug", action="store_true",
                    help="spec 9 (A5) bench variant: the VCU never sends "
                         "STAND_BY, so the only session boundary left is the "
                         "pilot timer stepping back. Exercises the 6.1 "
                         "boundary that 11 of 49 corpus terminations would "
                         "need.")
    ap.add_argument("--hvil-before-flow-drop", action="store_true",
                    help="spec 9 variant: the BMS opens HVIL 20 ms BEFORE the "
                         "VCU drops the flow bit, instead of 30 ms after it. "
                         "The ordering seen in 5 of the 13 source-11 handle "
                         "pulls in the corpus. Pair it with charger_sim "
                         "--unplug-at: the charger's plug-out report makes the "
                         "core transparent ~1 s earlier, so the early HVIL "
                         "must not trip.")
    ap.add_argument("--restart-on-replug", action="store_true",
                    help="spec 9 (A5) bench variant: after the session has "
                         "closed out, start a NEW one when the charger "
                         "reports the handle back in (vehicleConnected 0 -> "
                         "1). The modelled VCU otherwise ends exactly once. "
                         "Pair with charger_sim --replug-at, and do NOT pass "
                         "--stop-on-done, which ends the run at the FIRST "
                         "STAND_BY and so cuts the second session off before "
                         "it begins.")
    ap.add_argument("--aux-a", type=float, default=AUX_A_DEFAULT,
                    help="HV-side draw of the 12 V system, subtracted from the "
                         "charger's current before it reaches the pack "
                         "(default %(default)s A, from the hold-current "
                         "measurement). 0 disables it.")
    ap.add_argument("--post-standby-s", type=float, default=30.0,
                    help="simulated seconds to keep broadcasting after "
                         "STAND_BY before --stop-on-done ends the run, so the "
                         "interposer sees the close-out before the frames stop")
    ap.add_argument("--report-every", type=float, default=300.0,
                    help="simulated seconds between status lines")
    ap.add_argument("--max-sim-s", type=float, default=0.0)
    ap.add_argument("--stop-on-done", action="store_true",
                    help="end the run --post-standby-s after the VCU commands "
                         "STAND_BY, i.e. when the session is genuinely over. "
                         "Before 2026-09-12 this fired on chg_done + mode 3, "
                         "which cut the run off at the START of the balance "
                         "hold and so never exercised the close-out at all. "
                         "NOT suitable for override scenarios: there the VCU "
                         "sits in low power for the whole override. Use "
                         "--max-sim-s there.")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s veh %(message)s", datefmt="%H:%M:%S")

    b = busmod.open_bus(args, default_channel=busmod.VEHICLE_SEGMENT, logger=log)
    phys = busmod.open_bus(args, prefix="phys-",
                           default_channel=busmod.PHYSICS_SEGMENT, logger=log)
    try:
        if args.mode == "model":
            run_model(args, b, phys)
        else:
            import replay
            if not args.log:
                ap.error("--mode replay needs --log")
            replay.replay_to_bus(args.log, b, replay.VEHICLE_IDS,
                                 speed=args.speed, bus_filter=args.bus_filter,
                                 logger=log)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        report_peaks()
        b.shutdown()
        phys.shutdown()


if __name__ == "__main__":
    main()
