"""Charger-side simulator: the Bel Fuse bidirectional charger/inverter.

Consumes the VCU command frame `0x18EFC000` and broadcasts the charger's own
`0x18FFD?C0` family. Reproduces the behaviour the captures show:

  * page 00 is what actually starts and stops the current -- NOT page 01's
    setpoint. In vtruxchargeafterexportchargetest-80pctcv the delivered current
    collapsed from -15 A to -0.7 A within 3.1 s of `00 01 03`, while page 01's
    Ilim was still commanding 19.5 A and stayed there for another 155 s.
  * page 00 carries TWO fields and they do different jobs. The mode selects the
    regime; the ENERGY-FLOW bit is what ends the session. Mode 3
    CHARGER_LOW_POWER does not stop the charger at all -- it stays in state 12
    for 7.7 h on the parts truck. Delivery stops when flow goes to 0, which 39
    of 49 surveyed sessions contain and 38 of which then end on STAND_BY.
    See notes/charge-termination-behaviour.md.
  * mode 3 is TWO steps (spec 4, 7; raw 80 % capture, 2026-09-19): the mode
    command alone caps the charger at ~6 A DC at once (15 -> 6 A with the
    setpoint untouched at 19.5 A, via a reset 12 -> 15 -> 12); below that cap
    it follows the setpoint, which the VCU ramps to 0 and then dithers 0/2.9 A
    for the ~2.4 A hold. With the setpoint held at 0 A (the 83 % start) it
    delivers ~1.4 A: LOW_POWER_FLOOR_A.
  * mode 3 -> 1 restarts it. vtrux_partstruck_charge sent `00 01 03` at t=31 s
    (a startup transient) and `00 01 01` at t=94 s, and the current went
    straight back to -15 A. This is the behaviour the interposer relies on.
  * delivered current is min(page-01 Ilim, maxAvailableChargingCurrent, CV
    loop). During CC the VCU commanded ~19 A, maxAvail read 16 A and the pack
    took 14.8-15.5 A -- so maxAvail, not Ilim, was binding.
  * maxAvailableChargingCurrent is the charger's own pilot-derived DC ceiling,
    and it is what actually binds during CC. Measured on the same truck at two
    EVSE settings:

        pilot 26 % (16 A EVSE): maxAvail  8 A, VCU asks  9.70 A, delivered 5.85 A
        pilot 52 % (32 A EVSE): maxAvail 16 A, VCU asks 18.80 A, delivered 14.7 A

    so maxAvail_DC ~= pilotDuty% x 0.31, and the VCU's page-01 request sits
    ~1.2x above it (both ends are pilot-aware; the charger is the binding one).
    It drops to ~6 A in the complete/hold state.

Pack voltage and delivered current travel over the PHYSICS LINK (bus.py), not
over CAN. That link stands in for the HV cable: the charger and the pack are
connected by copper the interposer does not sit in. Modelling it explicitly
matters -- if the pack were charged by a CAN telemetry frame instead, the
interposer could "charge the battery" by rewriting the charger's reported
current, and --stealth (which zeroes exactly that telemetry) would appear to
stop the charge dead.
"""

import argparse
import gc
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import can

import bus as busmod
import protocol as P

log = logging.getLogger("chg")

R_PACK = 116 * 0.0006            # series resistance seen by the CV loop

# Until 2026-09-19 mode 3 was modelled as a float loop (2 mV/cell of headroom
# above the pack voltage at entry) so the hold current decayed the way the
# parts truck's balancing tail did. The raw 80 % capture shows the real
# charger does no such thing: it follows the VCU's setpoint under a ~6 A cap,
# and the ~2.4 A hold is the VCU commanding it (spec 4). What keeps the pack
# from running away in the hold is that the hold current mostly feeds the
# aux load (vehicle_sim --aux-a) and the BMS bleeds the leaders.
N_CELLS = 116
# What mode 3 delivers with a 0 A setpoint: 1.4-1.45 A DC, dead steady, in
# vtrux_partstruck_chargeagain3 (above_ceiling_start_timeline.txt).
LOW_POWER_FLOOR_A = 1.4
MAX_CATCHUP = 8
PHYS_PERIOD = 0.020              # physics-link update interval, simulated s




# A sender that stalls for longer than the interposer's staleness window
# (500 ms of SIMULATED time, i.e. 500/--time-scale ms of wall clock) trips the
# bench with "frames stale" through no fault of the state machine. Log every
# wall-clock stall over this threshold, with the GC collection counters, so a
# spurious trip can be traced to its cause instead of being blamed on load.
STALL_WARN_S = 0.040


def _gc_collections():
    return tuple(s["collections"] for s in gc.get_stats())


def report_stall(dt_wall, t_sim, prev_gc):
    """Call once per loop with the wall dt; returns the current GC counters."""
    cur = _gc_collections()
    if dt_wall > STALL_WARN_S:
        delta = tuple(c - p for c, p in zip(cur, prev_gc))
        log.warning("STALL %.0f ms wall at sim t=%.0fs  gc collections during "
                    "it gen0/1/2=%s", dt_wall * 1000.0, t_sim, delta)
    return cur

class Charger(object):

    def __init__(self, evse_a=16.0, hold_a=6.0, ac_v=240.0, ramp_s=1.5,
                 pilot_duty=52.0):
        self.state = P.CHG_STATE_OFF
        self.veh_state = P.VEH_STATE_B
        self.mode = P.MODE_EXPORT
        self.flow = 0
        self.vlim = P.VLIM_DEFAULT_COUNTS * P.UNIT
        self.ilim = 0.0
        self.evse_a = evse_a
        self.hold_a = hold_a
        self.ac_v = ac_v
        self.pilot_duty = pilot_duty
        self.ramp_s = ramp_s
        self.pack_v = 0.0
        self.current = 0.0           # + = charging into the pack
        self.faults = dict(inverter=0, buckboost=0, hv_over=0, over_temp=0)
        self.temp_c = 24.0
        # What the real charger reports about its own shutdowns (termination
        # note 4b): shutdownSource 11 = handle pull, 3 = internal fault (then
        # 14 once the vehicle has closed out), with stateBeforeLastShutdown.
        self.shutdown_source = 0
        self.state_before = 0
        self.evse_connected = 1
        self.vehicle_connected = 1
        self.unplugged_t = None
        self.fault_t = None
        self.low_power_since = None  # sim time mode 3 was last entered

        # Spec 9 (A8a): the two dips out of state 12 that the real charger
        # makes, so the 5 s debounce is exercised rather than assumed.
        #
        #  - the START-UP dip: ~0.4 s after first reaching 12, state 15 with
        #    shutdownSource 0, for ~1.2 s. Every session in the corpus has
        #    one (71 sessions, charger_startup_dips.txt).
        #  - the MODE-CHANGE reset: a Low Power <-> CHARGER change resets the
        #    charger for 0.8-1.2 s with shutdownSource 1. The core's own
        #    override causes exactly this, so the bench must produce it or
        #    the debounce is never tested against the thing it exists for.
        #
        # Both are shorter than the 5 s debounce and must NOT trip the core.
        self.first_charging_t = None   # when state 12 was first reached
        self.dip_until = None          # sim time the current dip ends
        self.dip_source = 0
        self.startup_dip_done = False
        self.prev_mode = None

        # Spec 3/6.1 (A3): BELINV_chargePilotOnlineTime, in MINUTES. Counts
        # from 0 while the plug is in and returns to 0 at the handle pull.
        # The core will not arm until it has read one of these, so a bench
        # without it never arms at all.
        self.pilot_online_s = 0.0
        # Spec 9 (A3): start the pilot timer part-way in, so the bench can
        # produce "the board booted into a session already running" without
        # waiting out the real minutes.
        self.pilot_start_min = 0
        # Spec 9 (6.1): stop transmitting entirely from this sim time, so the
        # charger-silence boundary can be exercised. None = never.
        self.silent_at = None

    def unplug(self, t):
        """The handle is pulled: proximity gone, state 15 with source 11, both
        connected flags cleared in the same frame, delivery stops at once."""
        if self.unplugged_t is not None:
            return
        self.unplugged_t = t
        self.state_before = self.state
        self.shutdown_source = 11
        self.evse_connected = 0
        self.vehicle_connected = 0
        # spec 3: the pilot timer returns to 0 at every handle pull, which is
        # the backwards step the core treats as a session boundary (6.1).
        # --pilot-start-min goes with it: that offset exists only to place
        # the BOOT part-way into a session, and a handle pull ends the
        # session it was pretending to be in. Leaving it set made the bench
        # report "34 -> 30 min" at a pull, a value no charger can produce.
        self.pilot_online_s = 0.0
        self.pilot_start_min = 0
        log.warning("charger: HANDLE PULLED at t=%.0fs (shutdownSource 11)", t)

    def replug(self, t):
        """The handle goes back in: a NEW session on the same bench run.

        Spec 9 (A5): the realistic case is a charge that fails at one handle
        and is moved to another. The pilot timer restarts from 0, which is
        the backwards step the core treats as a session boundary (6.1) even
        when no VCU STAND_BY was seen."""
        if self.unplugged_t is None:
            return
        self.unplugged_t = None
        self.fault_t = None
        self.faults = dict(inverter=0, buckboost=0, hv_over=0, over_temp=0)
        self.shutdown_source = 0
        self.evse_connected = 1
        self.vehicle_connected = 1
        self.pilot_online_s = 0.0
        self.pilot_start_min = 0
        self.first_charging_t = None
        self.startup_dip_done = False
        self.dip_until = None
        self.state = P.CHG_STATE_READY
        self.veh_state = P.VEH_STATE_B
        log.warning("charger: HANDLE REPLUGGED at t=%.0fs, pilot timer back "
                    "to 0 (new session)", t)

    @property
    def max_avail(self):
        return self.hold_a if self.mode == P.MODE_LOW_POWER else self.evse_a

    def on_command(self, data, t=0.0):
        key = P.page_key(data)
        if key == P.PAGE_MASTER:
            flow, mode = P.dec_master(data)
            if mode != self.mode:
                log.info("charger: mode %s -> %s (setpoint %.2f A)",
                         P.MODE_NAMES.get(self.mode),
                         P.MODE_NAMES.get(mode, mode), self.ilim)
                if mode == P.MODE_LOW_POWER:
                    self.low_power_since = t
                else:
                    self.low_power_since = None
            self.flow, self.mode = flow, mode
        elif key == P.PAGE_SETPOINT:
            self.vlim, self.ilim = P.dec_setpoint(data)

    def on_physics(self, arb_id, data):
        if arb_id == busmod.PHYS_VOLTAGE_ID:
            self.pack_v = busmod.decode_phys(data)

    def update(self, dt, t=0.0):
        prev_state = self.state

        # The pilot timer runs whenever the plug is in, counted in seconds
        # here and reported in whole minutes.
        if self.vehicle_connected:
            self.pilot_online_s += dt

        # A mode change between Low Power and CHARGER resets the charger for
        # ~1 s with shutdownSource 1 (spec 7). Detected here rather than in
        # on_command so it fires for the core's rewrite as well as the VCU's
        # own command -- from the charger's side they are the same frame.
        if (self.prev_mode is not None and self.mode != self.prev_mode
                and {self.mode, self.prev_mode} ==
                {P.MODE_LOW_POWER, P.MODE_CHARGER}
                and self.unplugged_t is None and not any(self.faults.values())):
            self.dip_until = t + 1.0
            self.dip_source = 1
            log.info("charger: mode change %s -> %s, resetting for 1.0 s "
                     "(shutdownSource 1)",
                     P.MODE_NAMES.get(self.prev_mode, self.prev_mode),
                     P.MODE_NAMES.get(self.mode, self.mode))
        self.prev_mode = self.mode
        if self.unplugged_t is not None:
            # 15 for a beat, then OFF; TERMINATED once STAND_BY arrives
            if self.mode == P.MODE_STANDBY:
                self.state = P.CHG_STATE_TERMINATED
                self.shutdown_source = 14
            elif t - self.unplugged_t < 0.1:
                self.state = P.CHG_STATE_FAULT
            else:
                self.state = P.CHG_STATE_OFF
            self.veh_state = P.VEH_STATE_A
        elif self.mode == P.MODE_CHARGER and self.flow:
            self.state = P.CHG_STATE_CHARGING
            self.veh_state = P.VEH_STATE_C
        elif self.mode == P.MODE_LOW_POWER and self.flow:
            self.state = P.CHG_STATE_CHARGING     # stays in 12 through the hold
            self.veh_state = P.VEH_STATE_C
        elif self.mode == P.MODE_STANDBY:
            self.state = P.CHG_STATE_FAULT
            self.veh_state = P.VEH_STATE_A
        elif self.mode == P.MODE_EXPORT:
            self.state = P.CHG_STATE_READY
            self.veh_state = P.VEH_STATE_B
        elif not self.flow:
            # The flow bit is the terminator, in whatever mode it is dropped.
            # Previously flow was consulted only in MODE_CHARGER, so a flow -> 0
            # during the mode-3 hold left the charger delivering -- the one
            # thing the survey says it must not do. STAND_BY and EXPORT keep
            # their own mappings above; this catches the flow -> 0 that arrives
            # BEFORE the mode changes, which is the ordering 27 of 49 sessions
            # show.
            self.state = P.CHG_STATE_READY
            self.veh_state = P.VEH_STATE_B

        # The start-up dip: ~0.4 s after the charger first reaches 12 it
        # drops to 15 for ~1.2 s with shutdownSource 0.
        if (self.state == P.CHG_STATE_CHARGING and self.unplugged_t is None
                and not any(self.faults.values())):
            if self.first_charging_t is None:
                self.first_charging_t = t
            if (not self.startup_dip_done
                    and t - self.first_charging_t >= 0.4):
                self.startup_dip_done = True
                self.dip_until = t + 1.2
                self.dip_source = 0
                log.info("charger: start-up dip at t=%.1fs, 1.2 s out of 12 "
                         "(shutdownSource 0)", t)

        if self.dip_until is not None:
            if t < self.dip_until and self.unplugged_t is None:
                self.state = P.CHG_STATE_FAULT      # 15
                self.shutdown_source = self.dip_source
            else:
                self.dip_until = None

        if any(self.faults.values()):
            # Internal fault: 15 with shutdownSource 3, then 14 once the
            # vehicle has closed the session out (the truck's own sequence).
            if self.fault_t is None:
                self.fault_t = t
                self.state_before = prev_state
                self.shutdown_source = 3
            elif self.mode == P.MODE_STANDBY:
                self.shutdown_source = 14
            self.state = P.CHG_STATE_FAULT
            target = 0.0
        elif self.unplugged_t is not None:
            target = 0.0
        elif self.state != P.CHG_STATE_CHARGING or self.pack_v <= 0:
            target = 0.0   # covers the dips: out of 12 means not delivering
        elif self.mode == P.MODE_LOW_POWER:
            # Two-step hold (spec 4): capped at hold_a by the mode alone,
            # following the setpoint below that, never under the floor the
            # real charger delivers at a 0 A setpoint. The CV limit against
            # the commanded Vlim still applies, as in CHARGER mode.
            cv = max(0.0, (self.vlim - self.pack_v) / R_PACK)
            target = max(LOW_POWER_FLOOR_A,
                         min(self.ilim, self.max_avail, cv))
        else:
            # CC, with a CV limit against the commanded Vlim (429.2 V -- never
            # actually reached on this pack, but modelled so the loop is real)
            cv = max(0.0, (self.vlim - self.pack_v) / R_PACK)
            target = max(0.0, min(self.ilim, self.max_avail, cv))

        if self.state != prev_state:
            log.info("charger: state %d -> %d (mode=%s flow=%d faults=%s)",
                     prev_state, self.state,
                     P.MODE_NAMES.get(self.mode, self.mode), self.flow,
                     {k: v for k, v in self.faults.items() if v} or "none")

        k = min(1.0, dt / self.ramp_s) if self.ramp_s > 0 else 1.0
        self.current += (target - self.current) * k

    def frames(self):
        ac_i = abs(self.current) * self.pack_v / max(1.0, self.ac_v) / 0.92
        out = {}
        out[0x18FFD4C0] = P.encode(P.BEL, 0x18FFD4C0, {
            "BELINV_keySwitch": 1, "BELINV_pilotControlSignal": 1,
            "BELINV_proxEnabled": self.vehicle_connected,
            "BELINV_evseConnected": self.evse_connected,
            "BELINV_vehicleConnected": self.vehicle_connected,
            "BELINV_auxPowerSupply": 1,
            "BELINV_chargerEnergyFlowEnable": 1 if self.flow else 0,
            "BELINV_s2Switch": 1,
            "BELINV_chargingInProgress": 1 if self.state == 12 else 0,
            "BELINV_inverterAlive": 1, "BELINV_inverterReady": 1,
            "BELINV_buckBoostAlive": 1, "BELINV_buckBoostReady": 1,
            "BELINV_gridTied": 1, "BELINV_unitReady": 1,
            "BELINV_state": self.state,
            "BELINV_shutdownSource": self.shutdown_source,
            "BELINV_stateBeforeLastShutdown": self.state_before,
        }, "BELINV_statusMultiplexer", 0)
        out[0x18FFD7C0] = P.encode(P.BEL, 0x18FFD7C0, {
            "BELINV_hvBatteryVoltage": max(0.0, self.pack_v),
            # NEGATIVE while charging -- see the DBC comment on this message
            "BELINV_hvBatteryCurrent": -self.current,
            "BELINV_chargerInputVoltage": self.ac_v if self.state == 12 else 0.0,
            "BELINV_chargerInputCurrent": ac_i if self.state == 12 else 0.0})
        out[0x18FFD9C0] = P.encode(P.BEL, 0x18FFD9C0, {
            "BELINV_maxAvailableChargingCurrent": self.max_avail,
            "BELINV_vehicleState": self.veh_state})
        return out

    def slow_frames(self):
        out = {}
        out[0x18FFD5C0] = P.encode(P.BEL, 0x18FFD5C0, {
            "BELINV_overAllInverterTemperature": self.temp_c,
            "BELINV_buckBoostTemperature": self.temp_c,
            "BELINV_ambientTemperature": self.temp_c - 2,
            "BELINV_overallTemperaturePercent": 0})
        out[0x18FFD8C0] = P.encode(P.BEL, 0x18FFD8C0, {
            "BELINV_system12vBatteryVoltage": 13.4,
            "BELINV_pilotDuty": int(round(self.pilot_duty)),
            "BELINV_pilotFrequency": 1000,
            # spec 3 (A3): whole minutes, from 0, back to 0 at the handle pull
            "BELINV_chargePilotOnlineTime":
                self.pilot_start_min + int(self.pilot_online_s // 60)})
        # mux 3 carries the fault register
        out[0x18FFD4C0] = P.encode(P.BEL, 0x18FFD4C0, {
            "BELINV_acOk": 1,
            "BELINV_inverterFault": self.faults["inverter"],
            "BELINV_buckBoostFault": self.faults["buckboost"],
            "BELINV_hvBatteryOverVoltage": self.faults["hv_over"],
            "BELINV_overTemperature": self.faults["over_temp"],
        }, "BELINV_statusMultiplexer", 3)
        return out


def main():
    busmod.install_break_handler()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    busmod.add_bus_args(ap, default_channel=busmod.CHARGER_SEGMENT,
                        help_role="the charger segment")
    busmod.add_bus_args(ap, default_channel=busmod.PHYSICS_SEGMENT,
                        prefix="phys-", help_role="the HV cable (physics link)")
    ap.add_argument("--pilot-duty", type=float, default=52.0,
                    help="J1772 control-pilot duty cycle %%. J1772 AC amps = "
                         "duty x 0.6, and the charger's DC ceiling works out at "
                         "duty x 8/26 (measured: 26 %% -> 8 A, 52 %% -> 16 A). "
                         "Sets --evse-a unless that is given explicitly.")
    ap.add_argument("--evse-a", type=float, default=None,
                    help="override the DC ceiling directly instead of deriving "
                         "it from --pilot-duty")
    ap.add_argument("--hold-a", type=float, default=6.0,
                    help="maxAvailableChargingCurrent in the complete/hold state")
    ap.add_argument("--time-scale", type=float, default=20.0)
    ap.add_argument("--fault-at", type=float, default=0.0,
                    help="inject an inverterFault this many simulated seconds "
                         "after charging starts (0 = never)")
    ap.add_argument("--fault-kind", default="inverter",
                    choices=("inverter", "buckboost", "hv_over", "over_temp"))
    ap.add_argument("--unplug-at", type=float, default=0.0,
                    help="pull the handle this many simulated seconds after "
                         "charging starts (0 = never): state 15 with "
                         "shutdownSource 11 and both connected flags cleared, "
                         "the way every captured hold actually ended")
    ap.add_argument("--replug-at", type=float, default=0.0,
                    help="spec 9 (A5): sim time to put the handle back in "
                         "after an unplug. A NEW session on the same run: the "
                         "pilot timer restarts from 0, which is the session "
                         "boundary the core sees when no VCU STAND_BY was "
                         "observed.")
    ap.add_argument("--pilot-start-min", type=int, default=0,
                    help="spec 9 (A3): start BELINV_chargePilotOnlineTime at "
                         "this many minutes, so the bench can produce 'the "
                         "board booted into a session already running' "
                         "without waiting out the real minutes. 2 or more "
                         "must keep the core in PASSTHROUGH.")
    ap.add_argument("--silent-at", type=float, default=0.0,
                    help="spec 9 (6.1): stop transmitting CAN frames at this "
                         "sim time. Past 20 s of silence the core takes a "
                         "session boundary that resets its working state but "
                         "must NOT clear SAFE.")
    ap.add_argument("--unplug-after-hold-s", type=float, default=0.0,
                    help="pull the handle this many simulated seconds after "
                         "the charger last entered mode 3 (0 = never). The "
                         "charger sees mode 3 only when nothing is rewriting "
                         "it, so in an override scenario this is 'N s after "
                         "the interposer released' -- the truck's own ending "
                         "of the hold the release leaves behind (spec 5, 9).")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s chg %(message)s", datefmt="%H:%M:%S")

    b = busmod.open_bus(args, default_channel=busmod.CHARGER_SEGMENT, logger=log)
    phys = busmod.open_bus(args, prefix="phys-",
                           default_channel=busmod.PHYSICS_SEGMENT, logger=log)
    # DC ceiling per unit pilot duty, fitted to the two measured points:
    # duty 26 % -> maxAvail 8 A, duty 52 % -> maxAvail 16 A (8/26 == 16/52).
    evse_a = (args.evse_a if args.evse_a is not None
              else round(args.pilot_duty * (8.0 / 26.0), 2))
    c = Charger(evse_a=evse_a, hold_a=args.hold_a, pilot_duty=args.pilot_duty)
    c.pilot_start_min = args.pilot_start_min

    t = 0.0
    charging_since = None
    silent_logged = [False]
    next_fast = 0.0
    next_slow = 0.0
    next_phys = 0.0
    last_wall = time.time()
    gc_prev = _gc_collections()
    log.info("charger sim up (pilot %.0f%% -> %.1f A DC, hold=%.1f A, "
             "time-scale=%g)", args.pilot_duty, evse_a, args.hold_a,
             args.time_scale)
    fired = {"fault": False, "unplug": False}
    try:
        while True:
            now = time.time()
            gc_prev = report_stall(now - last_wall, t, gc_prev)
            dt = busmod.unfreeze(now - last_wall) * args.time_scale
            last_wall = now
            t += dt

            while True:
                msg = b.recv(timeout=0.0)
                if msg is None:
                    break
                if msg.arbitration_id == P.CMD_ID and len(msg.data) >= 8:
                    c.on_command(bytes(msg.data), t)
            while True:
                msg = phys.recv(timeout=0.0)
                if msg is None:
                    break
                c.on_physics(msg.arbitration_id, bytes(msg.data))

            c.update(dt, t)

            if c.state == P.CHG_STATE_CHARGING and charging_since is None:
                charging_since = t
            # ONE-SHOT, per run and not per session. Both triggers are
            # keyed off `charging_since`, which --replug-at resets, so
            # without the latch the second session repeats the first
            # session's fault and unplug at the same offset -- which is
            # exactly the opposite of the A5 case (a charge that fails at
            # one handle and succeeds at another).
            if (args.fault_at and charging_since is not None
                    and not fired["fault"]
                    and t - charging_since >= args.fault_at
                    and not c.faults[args.fault_kind]):
                fired["fault"] = True
                c.faults[args.fault_kind] = 1
                log.warning("charger: INJECTED FAULT %s at t=%.0fs",
                            args.fault_kind, t)
            if (args.unplug_at and charging_since is not None
                    and not fired["unplug"]
                    and t - charging_since >= args.unplug_at):
                fired["unplug"] = True
                c.unplug(t)
            if (args.unplug_after_hold_s and c.low_power_since is not None
                    and t - c.low_power_since >= args.unplug_after_hold_s):
                c.unplug(t)
            if (args.replug_at and c.unplugged_t is not None
                    and t >= args.replug_at):
                c.replug(t)
                charging_since = None

            if t >= next_phys:
                phys.send(can.Message(arbitration_id=busmod.PHYS_CURRENT_ID,
                                      is_extended_id=False,
                                      data=busmod.encode_phys(c.current)))
                next_phys = t + PHYS_PERIOD

            # Spec 9 (6.1): --silent-at stops the charger transmitting
            # altogether, which is how the 20 s charger-silence boundary is
            # exercised. The sim keeps running so the physics link and the
            # vehicle side carry on; only the CAN frames stop.
            silent = args.silent_at and t >= args.silent_at
            if silent and not silent_logged[0]:
                silent_logged[0] = True
                log.warning("charger: GOING SILENT at t=%.0fs -- no further "
                            "CAN frames", t)

            n = 0
            while t >= next_fast and n < MAX_CATCHUP:
                if silent:
                    next_fast += 0.100
                    n += 1
                    continue
                for fid, data in c.frames().items():
                    b.send(can.Message(arbitration_id=fid, is_extended_id=True,
                                       data=data))
                next_fast += 0.100
                n += 1
            if n >= MAX_CATCHUP:
                next_fast = t + 0.100
            n = 0
            while t >= next_slow and n < 2:
                if silent:
                    pass
                for fid, data in ({} if silent else c.slow_frames()).items():
                    b.send(can.Message(arbitration_id=fid, is_extended_id=True,
                                       data=data))
                next_slow += 0.900
                n += 1
            if n >= 2:
                next_slow = t + 0.900

            time.sleep(0.0005)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        b.shutdown()
        phys.shutdown()


if __name__ == "__main__":
    main()
