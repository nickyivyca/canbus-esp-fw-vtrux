"""One trip test per fault source in spec 6, from MONITOR and from OVERRIDE,
plus the thresholds and the non-trips.

Review item C1: of the twelve trip codes, five had no test at any level --
unit, differential or scenario -- and the staleness one had a test that could
not say WHICH side went quiet. A trip is the core's whole safety story, so
every source gets a test, and every test states which source it is about.

Two things are checked at each source, because they are different claims:

  * from MONITOR -- armed and watching, rewriting nothing -- the core latches
    SAFE and **nothing changes on the bus**: every frame handed to it comes
    out once and byte-identical, and nothing is originated. A trip from here
    is supposed to be invisible to the truck;
  * from OVERRIDE the core stops rewriting, repeats the VCU's standing
    command to the charger once (spec 4.1, 20 frames, the VCU's own mode),
    and latches SAFE.

**A test of one source must not be able to pass on another.** That is the
failure this file was written against: `test_staleness_trips` in
test_machine.py starves BOTH sides at once and asserts `trip_reason is not
None`, so it passes whichever one fires and would pass if the BMS timer were
deleted. Here each case drives exactly one source and holds every other
input healthy for the whole run, and the assertion names the reason.

Rev 2 (charge-interposer tester, 2026-10-07). Rev 1 tested that each source
trips but pinned none of the numbers: the staleness cases allowed 1.2 s, the
debounce case had no lower bound, the 6 h cap was only exercised through a
2 s Config override, and 3610 mV only at 3615. It judged "nothing changed on
the bus" from the core's own counters, and took the burst length from the
core's Config. Now:

  * the thresholds are tested at their edges -- 3609 / 3610 mV, a 4.8 s dip
    against a 5.0-5.15 s trip, a message refreshed every 450 ms against one
    tripping 500-550 ms after its last frame, and a default-Config override
    that trips 6 h after the OVERRIDE began, not 6 h after arming;
  * the spec's non-trips are tested from both states: TYPE2, undervolt,
    temperature, and chg_max = 0 (a release from OVERRIDE, B-5);
  * "nothing changed on the bus" compares every output with its input;
  * the burst is the spec's literal 20;
  * test_thresholds_can_fail runs each threshold test against a core built
    with a wrong threshold, and requires it to fail.

The Bench driver is a copy of test_machine.py's, kept here because that file
belongs to the implementor: a harness that imported it could change under
the tester without notice.

Rev 3 (tester, 2026-10-08, plan 1b), for spec 6 text of 2026-10-07 (it
closed tracker F items 4 and 5, which rev 2 left untested):
  * "contactors open" is bcm_mainc_stat anything but 11 or 12: every value
    of the 4-bit field, alone, from MONITOR and from OVERRIDE;
  * "charger fault flags" are exactly inverterFault, buckBoostFault,
    hvBatteryOverVoltage and overTemperature in 0x18FFD4C0 mux 3: each trips
    alone, and every other mux-3 flag, read from the DBC, does not;
  * MONITOR past 6 h does not trip ("override longer than 6 hours").
Each has a can-fail test: the definition check against a wrong definition,
and the 6 h MONITOR check with a fault injected after 6 h.

Spec 6.1 text of 2026-10-08 (tester, in the repo): the charger-silence
boundary forgets the last pilot-timer reading, so a lower timer after the
silence keeps SAFE; SAFE then clears on STAND_BY / EXPORT or a later
step-back (test_silence_forgets_the_pilot_reading, with a can-fail test).

Spec 5.2 HOLD (spec eb9b9d135939af17, tester, 2026-10-09):
  * every fault source (all but the 6 h cap) trips from HOLD too: SAFE,
    the trip's reason, NO repeat toward the charger, page 01 untouched
    after (test_every_fault_source_trips_from_hold; can-fail
    test_hold_trips_can_fail, three wrapped wrong cores);
  * the hold has no time limit: a 2 s cap held 3x from the hold's entry
    does not trip, while the same span in OVERRIDE does (the control), and
    a hold past 6 h on the default Config does not trip;
  * the non-trips hold from HOLD as well, and chg_max 0 from OVERRIDE now
    ends in HOLD (the release), not TERMINATED.

Run:  py -3.14 projects/vtrux/tools/interposer/test_trips.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import machine as M
import protocol as P

FAILS = []

BURST = 20                  # spec 4.1, the literal
STALE_MS = 500              # spec 6
DEBOUNCE_MS = 5000          # spec 6 (A8a)
VMAX_HARD_V = 3.610         # spec 6
CAP_MS = 6 * 3600 * 1000    # spec 6


def ok(cond, name):
    if not cond:
        FAILS.append(name)


# --- the driver ------------------------------------------------------------

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
    """Arms a core into a running charge, then pokes it. Copied from
    test_machine.py (see the module docstring), plus `log`: every step with
    what went in and what came out, for the byte-level checks."""

    def __init__(self, arm_delay_ms=0, **cfgkw):
        cfg = M.Config()
        cfg.arm_delay_ms = arm_delay_ms
        for k, v in cfgkw.items():
            setattr(cfg, k, v)
        self.core = M.InterposerCore(cfg, 0)
        self.t = 0
        self.out = []
        self.log = []
        # Every step, in make_golden.py's trace format, so the same sequence
        # can be replayed through machine.cpp (host_diff/make_unit_traces.py).
        self.trace = []
        self.cfg_overrides = dict(cfgkw)
        if arm_delay_ms != M.Config().arm_delay_ms:
            self.cfg_overrides["arm_delay_ms"] = arm_delay_ms

    def veh(self, fid, data):
        self.trace.append("V %d %X %d %s"
                          % (self.t, fid, 1 if fid > 0x7FF else 0,
                             bytes(data).hex()))
        self.out = self.core.on_vehicle_frame(fid, fid > 0x7FF, data, self.t)
        self.log.append(("V", fid, bytes(data), list(self.out)))
        return self.out

    def chg(self, fid, data):
        self.trace.append("C %d %X 1 %s" % (self.t, fid, bytes(data).hex()))
        self.out = self.core.on_charger_frame(fid, True, data, self.t)
        self.log.append(("C", fid, bytes(data), list(self.out)))
        return self.out

    def tick(self):
        self.trace.append("T %d" % self.t)
        self.out = self.core.tick(self.t)
        self.log.append(("T", None, None, list(self.out)))
        return self.out

    def charge_info(self, max_avail_a=16.0):
        return self.chg(0x18FFD9C0, P.encode(P.BEL, 0x18FFD9C0, {
            "BELINV_maxAvailableChargingCurrent": max_avail_a,
            "BELINV_vehicleState": 3}))

    def charger_status(self, state=12, shutdown_src=0, vehicle_connected=1,
                       evse_connected=1):
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
        self.veh(0x18EFC000, P.enc_master(1, P.MODE_CHARGER))
        if pilot_min is not None:
            self.chg(M.CHG_PILOT, pilot(pilot_min))
        self.charger_status(12)
        self.charge_info(max_avail_a)
        self.evap(evap)
        self.bms(**kw)
        return self.core.state

    def low_power(self):
        return self.veh(0x18EFC000, P.enc_master(1, P.MODE_LOW_POWER))

    def state(self):
        return M.STATE_NAMES[self.core.state]


# --- injectors -------------------------------------------------------------
#
# Each takes a Bench already armed (MONITOR) or overriding, and makes exactly
# ONE fault source fire. `hold` is called every step with the sources this
# case is not testing, so nothing else can expire underneath it; a case that
# needs something to go quiet simply leaves it out of its own `hold`.

def _hold_all(b):
    """Everything healthy: both sides fresh, charger in 12."""
    b.charger_status(12)
    b.bms(vmax=3.340, chg_max=300.0)


def _hold_bms_only(b):
    b.bms(vmax=3.340, chg_max=300.0)


def _hold_except(bms_id):
    def hold(b):
        b.charger_status(12)
        b.bms(vmax=3.340, chg_max=300.0, skip=(bms_id,))
    return hold


# (label, reason fragment, fire(bench), hold(bench), steps, override_only)
# host_diff/make_unit_traces.py records one trace per entry; keep the shape.
CASES = [
    ("HVIL open", "HVIL open",
     lambda b: b.bms_410(hvil=0), _hold_all, 1, False),

    ("contactors open", "contactor state",
     lambda b: b.bms_440(mainc=0), _hold_all, 1, False),

    ("alarm TYPE3", "BMS alarm TYPE3",
     lambda b: b.bms_410(alarm=3), _hold_all, 1, False),

    ("BMS EPO", "BMS EPO",
     lambda b: b.bms_410(epo=1), _hold_all, 1, False),

    ("cell_overvolt", "BMS cell_overvolt",
     lambda b: b.bms_420(overvolt=1), _hold_all, 1, False),

    ("3610 mV hard ceiling", "over hard ceiling",
     lambda b: b.bms_430(vmax=3.615), _hold_all, 1, False),

    ("charger fault flags", "charger fault",
     lambda b: b.charger_fault(BELINV_inverterFault=1), _hold_all, 1, False),

    ("charger out of state 12 past the debounce", "charger out of state 12",
     lambda b: b.charger_status(15),
     lambda b: (b.charger_status(15), b.bms(vmax=3.340, chg_max=300.0)),
     60, False),

    # Charger silence with the BMS kept fresh -- spec 9's "the stale-charger
    # test keeps the BMS fresh".
    ("charger status stale on its own", "charger frames stale",
     lambda b: None, _hold_bms_only, 12, False),

    # ...and the mirror image, one BMS message at a time (B-6).
    ("0x410 stale on its own", "BMS frames stale: 0x410",
     lambda b: None, _hold_except(0x410), 12, False),
    ("0x420 stale on its own", "BMS frames stale: 0x420",
     lambda b: None, _hold_except(0x420), 12, False),
    ("0x430 stale on its own", "BMS frames stale: 0x430",
     lambda b: None, _hold_except(0x430), 12, False),
    ("0x440 stale on its own", "BMS frames stale: 0x440",
     lambda b: None, _hold_except(0x440), 12, False),

    # From OVERRIDE only: spec 6 defines it as "override longer than 6
    # hours". Its MONITOR side is with the reviewer (item 4). This case uses
    # a 2 s cap so the trace stays short; test_override_cap_is_six_hours
    # covers the real value with the default Config.
    ("override session cap", "override exceeded session cap",
     lambda b: None, _hold_all, 30, True),
]


def _originated(out):
    """The page-00 frames an injector's own return carries toward the
    charger -- the first frame of a repeat raised on a frame."""
    if not out:
        return []
    return [d for side, fid, _ext, d in out
            if side == M.TO_CHARGER and fid == M.CMD_ID]


def _drain(b, hold, steps, step_ms=100):
    """Advance the clock, calling `hold` each step. Returns the payloads the
    core originated toward the charger from tick() (the spec 4.1 repeat)."""
    sent = []
    for _ in range(steps):
        b.t += step_ms
        hold(b)
        for side, fid, _ext, data in b.tick():
            if side == M.TO_CHARGER and fid == M.CMD_ID:
                sent.append(data)
    return sent


def _armed(cap_ms=None, **cfg):
    kw = dict(cfg)
    if cap_ms is not None:
        kw["override_max_ms"] = cap_ms
    b = Bench(**kw)
    b.arm(vmax=3.340, chg_max=300.0)
    return b


def _bus_unchanged(entries):
    """Spec 2 transparency over a stretch of the log: every frame came out
    once, to the other port, byte-identical; ticks emitted nothing. Returns
    the first violation, or None."""
    for kind, fid, data, out in entries:
        if kind == "T":
            if out:
                return "a tick emitted %s" % [(s, hex(f), bytes(d).hex())
                                              for s, f, _e, d in out]
            continue
        port = M.TO_CHARGER if kind == "V" else M.TO_VEHICLE
        if len(out) != 1:
            return "0x%X came out %d times" % (fid, len(out))
        side, ofid, _e, odata = out[0]
        if side != port or ofid != fid or bytes(odata) != data:
            return ("0x%X %s came out as port %d 0x%X %s"
                    % (fid, data.hex(), side, ofid, bytes(odata).hex()))
    return None


def check_from_monitor(label, reason, fire, hold, steps):
    b = _armed()
    ok(b.state() == "MONITOR", "%s: armed into MONITOR (was %s)"
       % (label, b.state()))
    mark = len(b.log)

    fire(b)
    _drain(b, hold, steps)

    got = b.core.trip_reason or ""
    ok(reason in got,
       "%s: trips from MONITOR with reason %r (got %r)" % (label, reason, got))
    ok(b.state() == "SAFE", "%s: SAFE latched from MONITOR (was %s)"
       % (label, b.state()))
    # A trip from MONITOR must be invisible to the truck: we were a wire and
    # we stay one. Judged on the bytes, not on the core's counters -- the
    # fault frame itself must reach the vehicle (spec 5.3: never masks a
    # BMS frame).
    bad = _bus_unchanged(b.log[mark:])
    ok(bad is None, "%s: the bus is unchanged through a MONITOR trip (%s)"
       % (label, bad))
    ok(any(k != "T" for k, _f, _d, _o in b.log[mark:]),
       "%s: and frames were forwarded through it" % label)

    # SAFE is latched for the session: a healthy bus must not un-trip it.
    _drain(b, _hold_all, 20)
    ok(b.state() == "SAFE", "%s: SAFE survives the fault clearing (was %s)"
       % (label, b.state()))


def check_from_override(label, reason, fire, hold, steps, cap_ms=None):
    b = _armed(cap_ms=cap_ms)
    b.low_power()
    ok(b.state() == "OVERRIDE", "%s: overriding (was %s)" % (label, b.state()))
    rewrote = [o for o in b.out if o[0] == M.TO_CHARGER and o[1] == M.CMD_ID]
    ok(rewrote and P.dec_master(rewrote[0][3])[1] == P.MODE_CHARGER,
       "%s: the override really was rewriting Low Power to CHARGER" % label)

    # _trip() emits the burst's FIRST frame into the return of whatever
    # called it. For a trip raised on a frame that is the frame handler, so
    # the injector's own return is counted; for sources that fire from a
    # tick the injector sends nothing.
    sent = _originated(fire(b))
    sent += _drain(b, hold, steps)

    got = b.core.trip_reason or ""
    ok(reason in got,
       "%s: trips from OVERRIDE with reason %r (got %r)"
       % (label, reason, got))
    ok(b.state() == "SAFE", "%s: SAFE latched from OVERRIDE (was %s)"
       % (label, b.state()))

    # Spec 4.1: the charger only ever heard our rewrite of the VCU's command,
    # so the trip hands it the real one -- 20 frames, the VCU's own mode.
    sent += _drain(b, hold, 25)
    ok(len(sent) == BURST, "%s: %d repeat frames, not %d"
       % (label, len(sent), BURST))
    want = P.enc_master(1, P.MODE_LOW_POWER)
    ok(all(bytes(f) == want for f in sent),
       "%s: every repeated frame is the VCU's own Low Power" % label)
    ok(b.core.stats["synth"] == len(sent),
       "%s: the synthesised counter agrees with what was sent (%d vs %d)"
       % (label, b.core.stats["synth"], len(sent)))

    # And the rewriting has stopped: later VCU frames pass byte-identical.
    mark = len(b.log)
    b.low_power()
    b.veh(M.CMD_ID, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 380))
    bad = _bus_unchanged(b.log[mark:])
    ok(bad is None, "%s: nothing rewritten after the trip (%s)" % (label, bad))


def _to_hold(b, limit=60):
    """OVERRIDE -> HOLD by spec 5.1 / 5.2: chg_max at or below 3.0 A for the
    debounce releases into the hold. Then lets the release's own Low Power
    repeat (4.1, 20 x 50 ms) run out, so nothing a later step sends toward
    the charger is that burst. -> True if the core is in HOLD."""
    for _ in range(limit):
        if b.state() == "HOLD":
            break
        b.t += 100
        b.charger_status(12)
        b.bms(vmax=3.340, chg_max=2.0)
        b.tick()
    if b.state() != "HOLD":
        return False
    _drain(b, lambda bb: (bb.charger_status(12),
                          bb.bms(vmax=3.340, chg_max=2.0)), 25)
    return b.state() == "HOLD"


def _hold_rewrites_page01(b):
    """True if a VCU page 01 sent now reaches the charger changed -- the
    hold's page-01 rewrite (5.2) that a trip must stop."""
    out = b.veh(M.CMD_ID, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 380))
    fw = [d for side, fid, _e, d in out
          if side == M.TO_CHARGER and fid == M.CMD_ID]
    return len(fw) == 1 and bytes(fw[0]) != P.enc_setpoint(
        P.VLIM_DEFAULT_COUNTS, 380)


def hold_trip_problems(label, reason, fire, hold, steps, wrong=None):
    """Spec 5.2: "The trips of section 6 apply throughout" the hold, and "a
    trip in HOLD goes to SAFE". Spec 6: a trip stops "the page-01 rewrite at
    once if we were holding (5.2; page 00 is already the VCU's, so nothing
    is repeated)". So: SAFE, the trip's reason, NO frame originated toward
    the charger, and later page 01 passes byte-identical.

    `wrong(b)`, for test_hold_trips_can_fail only, turns the Bench's core
    into a wrong one after it is built."""
    probs = []
    b = _armed()
    if wrong is not None:
        wrong(b)
    b.low_power()
    if not _to_hold(b):
        return ["%s: did not release into HOLD (%s)" % (label, b.state())]
    if not _hold_rewrites_page01(b):
        probs.append("%s: the hold was not rewriting page 01" % label)
    sent = _originated(fire(b))
    sent += _drain(b, hold, steps)
    got = b.core.trip_reason or ""
    if reason not in got:
        probs.append("%s: no trip from HOLD with reason %r (got %r)"
                     % (label, reason, got))
    if b.state() != "SAFE":
        probs.append("%s: not SAFE after a trip in HOLD (%s)"
                     % (label, b.state()))
    sent += _drain(b, hold, 25)
    if sent:
        probs.append("%s: a trip in HOLD repeated %d frame(s) toward the "
                     "charger; spec 6 repeats nothing" % (label, len(sent)))
    mark = len(b.log)
    b.veh(M.CMD_ID, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 380))
    bad = _bus_unchanged(b.log[mark:])
    if bad is not None:
        probs.append("%s: page 01 changed after the trip in HOLD (%s)"
                     % (label, bad))
    return probs


def test_every_fault_source_trips_from_hold():
    for label, reason, fire, hold, steps, override_only in CASES:
        if override_only:
            continue                    # the 6 h cap: OVERRIDE only (5.2)
        for p in hold_trip_problems(label, reason, fire, hold, steps):
            FAILS.append(p)


def test_hold_trips_can_fail():
    """Three wrong cores, each made by wrapping the real one (machine.py is
    untouched); each must be caught on its own check, using the first fault
    source (any would do: the wrongs are in what follows the trip)."""
    label, reason, fire, hold, steps, _o = [c for c in CASES if not c[5]][0]

    def repeats_on_trip(b):
        tick = b.core.tick

        def t(now):
            out = list(tick(now))
            if b.core.trip_reason:
                out.append((M.TO_CHARGER, M.CMD_ID, True,
                            P.enc_master(1, P.MODE_LOW_POWER)))
            return out
        b.core.tick = t

    def keeps_rewriting(b):
        veh = b.core.on_vehicle_frame

        def v(fid, ext, data, now):
            out = list(veh(fid, ext, data, now))
            if (b.core.trip_reason and fid == M.CMD_ID and data[0] == 0x01):
                out = [(s, f, e, P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, 40))
                       if f == M.CMD_ID else (s, f, e, d)
                       for s, f, e, d in out]
            return out
        b.core.on_vehicle_frame = v

    def ignores_trips_in_hold(b):
        if not hasattr(b.core, "_trip"):
            raise SystemExit("test_hold_trips_can_fail: the core has no "
                             "_trip() to wrap -- rebuild this wrong core")
        trip = b.core._trip

        def tr(*a, **kw):
            if M.STATE_NAMES[b.core.state] == "HOLD":
                return []                   # the trip's frames: none
            return trip(*a, **kw)
        b.core._trip = tr

    for name, wrong, want in (
            ("repeats Low Power after a trip in HOLD", repeats_on_trip,
             "repeated"),
            ("keeps rewriting page 01 after the trip", keeps_rewriting,
             "page 01 changed after the trip"),
            ("ignores trips in HOLD", ignores_trips_in_hold,
             "no trip from HOLD")):
        probs = hold_trip_problems(label, reason, fire, hold, steps,
                                   wrong=wrong)
        ok(any(want in p for p in probs),
           "the from-HOLD trip check catches a core that %s (%s)"
           % (name, probs[:2]))
    ok(not hold_trip_problems(label, reason, fire, hold, steps),
       "control: the same check passes the unwrapped core")


def hold_no_time_limit_problems(state, cap_ms=2000, span_ms=None):
    """Spec 5.2 / 6 (user, 2026-10-09): "The hold has no time limit: the
    6 h cap of section 6 is the override's". The span is counted from the
    entry into `state` and is three times the cap, so a cap counted from the
    hold's entry, or from the session start, both show. Run with
    state="OVERRIDE" as the control: the same span there MUST trip -- which
    is what makes a HOLD that does not trip mean anything."""
    probs = []
    b = _armed(cap_ms=cap_ms)
    b.low_power()
    if state == "HOLD" and not _to_hold(b):
        return ["did not reach HOLD (%s)" % b.state()]
    if b.state() != state:
        return ["did not reach %s (%s)" % (state, b.state())]
    span = span_ms if span_ms is not None else 3 * cap_ms
    step = 400 if span > 60000 else 100      # BMS fresh within 500 ms
    hold = (_hold_all if state == "OVERRIDE" else
            (lambda bb: (bb.charger_status(12),
                         bb.bms(vmax=3.340, chg_max=2.0))))
    _drain(b, hold, span // step + 1, step_ms=step)
    if b.core.trip_reason is not None or b.state() != state:
        probs.append("%s held %d ms past entry (cap %s ms): %s (%r)"
                     % (state, span, cap_ms, b.state(), b.core.trip_reason))
    return probs


def test_hold_has_no_time_limit():
    p = hold_no_time_limit_problems("HOLD")
    ok(not p, "spec 5.2: a hold with a 2 s cap configured, held 6 s from its "
       "entry, does not trip (%s)" % p)
    ctl = hold_no_time_limit_problems("OVERRIDE")
    ok(ctl and "session cap" in ctl[0],
       "control: the same span in OVERRIDE trips on the cap (%s)" % ctl)


def test_hold_past_six_hours():
    """The same with the default Config: a hold held 6 h 5 min from its
    entry, with no trip (P5, 2026-10-09)."""
    six_h = 6 * 3600 * 1000
    p = hold_no_time_limit_problems("HOLD", cap_ms=None,
                                    span_ms=six_h + 5 * 60 * 1000)
    ok(not p, "spec 5.2: a hold past 6 h (default Config) does not trip "
       "(%s)" % p)


def test_every_fault_source_trips_from_monitor():
    for label, reason, fire, hold, steps, override_only in CASES:
        if override_only:
            continue
        check_from_monitor(label, reason, fire, hold, steps)


def test_every_fault_source_trips_from_override():
    for label, reason, fire, hold, steps, override_only in CASES:
        cap = 2000 if override_only else None
        check_from_override(label, reason, fire, hold, steps, cap_ms=cap)


def test_no_source_trips_a_healthy_core():
    """The control. Every assertion above is of the form "this made it trip";
    none of them can tell the difference between a working trip and a core
    that trips on anything. This one holds every input healthy for the same
    span and requires silence."""
    for state, setup in (("MONITOR", lambda b: None),
                         ("OVERRIDE", lambda b: b.low_power())):
        b = _armed()
        setup(b)
        ok(b.state() == state, "control: reached %s (was %s)"
           % (state, b.state()))
        sent = _drain(b, _hold_all, 80)
        ok(b.core.trip_reason is None,
           "control (%s): a healthy bus does not trip (%r)"
           % (state, b.core.trip_reason))
        ok(not sent,
           "control (%s): and nothing is originated (%d frames)"
           % (state, len(sent)))


# --- thresholds ------------------------------------------------------------
#
# Each returns its problems instead of appending to FAILS, so that
# test_thresholds_can_fail can run it against a deliberately wrong core.

def _entry(state, **cfg):
    b = _armed(**cfg)
    if state in ("OVERRIDE", "HOLD"):
        b.low_power()
    if state == "HOLD":
        _to_hold(b)
    return b


def hard_ceiling_problems(**cfg):
    """3610 mV inclusive, no debounce: 3609 never trips, one 3610 frame
    does."""
    probs = []
    for state in ("MONITOR", "OVERRIDE"):
        b = _entry(state, **cfg)
        for _ in range(30):
            b.t += 100
            b.charger_status(12)
            b.bms(vmax=VMAX_HARD_V - 0.001, chg_max=300.0)
            b.tick()
        if b.core.trip_reason is not None:
            probs.append("%s: 3.609 V tripped (%r)" % (state,
                                                        b.core.trip_reason))
        b.t += 100
        b.bms_430(vmax=VMAX_HARD_V)
        if "over hard ceiling" not in (b.core.trip_reason or ""):
            probs.append("%s: one 3.610 V frame did not trip at once (%r)"
                         % (state, b.core.trip_reason))
    return probs


def debounce_problems(**cfg):
    """A 4.8 s departure from 12 is ridden through; a continuing one trips
    5.0-5.15 s after the first non-12 frame."""
    probs = []
    for state in ("MONITOR", "OVERRIDE"):
        b = _entry(state, **cfg)
        for i in range(49):                    # 15 at t0 .. t0 + 4.8 s
            b.t += 100
            b.charger_status(15)
            b.bms(vmax=3.340, chg_max=300.0)
            b.tick()
        _drain(b, _hold_all, 30)
        if b.core.trip_reason is not None:
            probs.append("%s: a 4.8 s departure from 12 tripped (%r)"
                         % (state, b.core.trip_reason))
        if b.state() != state:
            probs.append("%s: a 4.8 s departure left the core in %s"
                         % (state, b.state()))

        b = _entry(state, **cfg)
        t0 = b.t + 50
        tripped = None
        for _ in range(140):
            b.t += 50
            b.charger_status(15)
            b.bms(vmax=3.340, chg_max=300.0)
            b.tick()
            if b.core.trip_reason is not None:
                tripped = b.t - t0
                break
        if tripped is None or "charger out of state 12" not in \
                b.core.trip_reason:
            probs.append("%s: a continuing departure never tripped on it (%r)"
                         % (state, b.core.trip_reason))
        elif not (DEBOUNCE_MS <= tripped <= DEBOUNCE_MS + 150):
            probs.append("%s: tripped %d ms into the departure, not at 5 s"
                         % (state, tripped))
    return probs


def staleness_problems(**cfg):
    """Each of the five timed messages, alone: refreshed every 450 ms it
    never trips; withheld, it trips 500-550 ms after its last frame."""
    probs = []

    def sends(b, which):
        if which == "charger":
            b.charger_status(12)
        else:
            b.bms(vmax=3.340, chg_max=300.0,
                  skip=tuple(i for i in M.BMS_IDS if i != which))

    def others(b, which):
        if which != "charger":
            b.charger_status(12)
        b.bms(vmax=3.340, chg_max=300.0,
              skip=(which,) if which != "charger" else ())

    for which in ("charger",) + tuple(M.BMS_IDS):
        name = which if which == "charger" else "0x%X" % which
        b = _entry("MONITOR", **cfg)
        for i in range(400):                  # 20 s at 50 ms
            b.t += 50
            others(b, which)
            if i % 9 == 0:                    # every 450 ms
                sends(b, which)
            b.tick()
        if b.core.trip_reason is not None:
            probs.append("%s refreshed every 450 ms tripped (%r)"
                         % (name, b.core.trip_reason))

        b = _entry("MONITOR", **cfg)
        b.t += 50
        others(b, which)
        sends(b, which)
        last = b.t
        tripped = None
        for _ in range(40):
            b.t += 50
            others(b, which)
            b.tick()
            if b.core.trip_reason is not None:
                tripped = b.t - last
                break
        want = ("charger frames stale" if which == "charger"
                else "BMS frames stale: 0x%X" % which)
        if tripped is None or want not in b.core.trip_reason:
            probs.append("%s withheld: no trip on it (%r)"
                         % (name, b.core.trip_reason))
        elif not (STALE_MS <= tripped <= STALE_MS + 50):
            probs.append("%s withheld: tripped %d ms after its last frame, "
                         "not at 500" % (name, tripped))
    return probs


def cap_problems(**cfg):
    """Default 6 h, counted from the start of the OVERRIDE: two hours of
    MONITOR first, then no trip at 6 h - 1 s, a trip by 6 h + 1 s."""
    probs = []
    b = _armed(**cfg)
    step = 250

    def hold(n):
        for _ in range(n):
            b.t += step
            b.charger_status(12)
            b.bms(vmax=3.340, chg_max=300.0)
            b.tick()

    hold(2 * 3600 * 1000 // step)
    if b.state() != "MONITOR":
        probs.append("two hours of MONITOR ended in %s (%r)"
                     % (b.state(), b.core.trip_reason))
        return probs
    b.low_power()
    t_ov = b.t
    if b.state() != "OVERRIDE":
        probs.append("Low Power did not override (%s)" % b.state())
        return probs
    hold((CAP_MS - 1000) // step)
    if b.core.trip_reason is not None:
        probs.append("tripped %.1f s into the override, before 6 h (%r)"
                     % ((b.t - t_ov) / 1000.0, b.core.trip_reason))
        return probs
    hold(2000 // step)
    if "session cap" not in (b.core.trip_reason or ""):
        probs.append("no cap trip by %.1f s into the override (%r, %s)"
                     % ((b.t - t_ov) / 1000.0, b.core.trip_reason,
                        b.state()))
    return probs


def test_hard_ceiling_is_inclusive_at_3610():
    for p in hard_ceiling_problems():
        FAILS.append("hard ceiling: " + p)


def test_charger_debounce_is_five_seconds():
    for p in debounce_problems():
        FAILS.append("debounce: " + p)


def test_staleness_is_500_ms_per_message():
    for p in staleness_problems():
        FAILS.append("staleness: " + p)


def test_override_cap_is_six_hours_from_override_start():
    for p in cap_problems():
        FAILS.append("6 h cap: " + p)


def test_thresholds_can_fail():
    """Each threshold test above, against a core built with a wrong
    threshold, must report a problem. Config is the product's; it is used
    here only to build the wrong core, never to decide what is right."""
    for name, fn, wrong in (
            ("hard ceiling at 3611 mV", hard_ceiling_problems,
             {"vmax_hard_mv": 3611}),
            ("hard ceiling at 3609 mV", hard_ceiling_problems,
             {"vmax_hard_mv": 3609}),
            ("debounce 2 s", debounce_problems,
             {"chg_state_debounce_ms": 2000}),
            ("debounce 8 s", debounce_problems,
             {"chg_state_debounce_ms": 8000}),
            ("BMS staleness 1000 ms", staleness_problems,
             {"bms_stale_ms": 1000}),
            ("BMS staleness 300 ms", staleness_problems,
             {"bms_stale_ms": 300}),
            ("charger staleness 1000 ms", staleness_problems,
             {"chg_stale_ms": 1000}),
            ("override cap 7 h", cap_problems,
             {"override_max_ms": 7 * 3600 * 1000}),
            ("override cap 5 h", cap_problems,
             {"override_max_ms": 5 * 3600 * 1000})):
        ok(fn(**wrong), "threshold test catches a core with %s" % name)


# --- spec 6 definitions (user, 2026-10-07) ---------------------------------

FAULT_BITS = ("BELINV_inverterFault", "BELINV_buckBoostFault",
              "BELINV_hvBatteryOverVoltage", "BELINV_overTemperature")
CONTACTORS_CLOSED = (11, 12)     # MAIN_PN_CLOSED_DRIVE, MAIN_P_CLOSED_CHARGE


def _spec_mainc_trips(v):
    return v not in CONTACTORS_CLOSED


def _spec_flag_trips(name):
    return name in FAULT_BITS


def _mux3_flags():
    """Every one-bit flag on 0x18FFD4C0 mux 3, from the DBC."""
    msg = P.BEL.get_message_by_frame_id(0x18FFD4C0)
    return sorted(sg.name for sg in msg.signals
                  if sg.multiplexer_ids == [3] and sg.length == 1)


def run_definition_case(state, kind, value):
    """One spec 6 definition input held for 1 s from `state` -> the Bench.

    kind "mainc": bcm_mainc_stat = value. kind "flag": the mux-3 flag named
    `value` set alone. host_diff/make_unit_traces.py records each case as a
    unit trace so the same inputs reach machine.cpp."""
    b = _entry(state)
    for _ in range(10):
        b.t += 100
        b.charger_status(12)
        if kind == "flag":
            b.chg(0x18FFD4C0, P.encode(P.BEL, 0x18FFD4C0, {value: 1},
                                       "BELINV_statusMultiplexer", 3))
            b.bms(vmax=3.340, chg_max=300.0)
        else:
            b.bms(vmax=3.340, chg_max=300.0, mainc=value)
        b.tick()
    return b


def definition_cases():
    """-> [(state, kind, value, reason fragment, spec says it trips)]."""
    out = []
    for state in ("MONITOR", "OVERRIDE"):
        for v in range(16):                     # 4-bit field
            out.append((state, "mainc", v, "contactor", _spec_mainc_trips(v)))
        for name in _mux3_flags():
            out.append((state, "flag", name, "charger fault",
                        _spec_flag_trips(name)))
    return out


def definition_problems(mainc_trips=_spec_mainc_trips,
                        flag_trips=_spec_flag_trips):
    """The core against a definition of the two spec 6 rows, one input at a
    time. Called with the spec's definition it must return nothing; called
    with a wrong one it must not (test_definitions_can_fail)."""
    probs = []
    flags = _mux3_flags()
    if len(flags) < 13 or not set(FAULT_BITS) <= set(flags):
        return ["mux 3 flags from the DBC: %s -- not the page this test "
                "assumes" % flags]
    for state, kind, value, frag, _spec in definition_cases():
        want = mainc_trips(value) if kind == "mainc" else flag_trips(value)
        b = run_definition_case(state, kind, value)
        got = b.core.trip_reason or ""
        what = ("mainc %d" % value) if kind == "mainc" else (value + " alone")
        if want and not (frag in got and b.state() == "SAFE"):
            probs.append("%s from %s: no %s trip (%r, %s)"
                         % (what, state, frag, got, b.state()))
        if not want and got:
            probs.append("%s from %s: tripped (%r)" % (what, state, got))
    return probs


def test_spec6_contactor_and_fault_definitions():
    for p in definition_problems():
        FAILS.append("spec 6 definitions: " + p)
    flags = _mux3_flags()
    ok(len([f for f in flags if f not in FAULT_BITS]) >= 9,
       "the non-fault mux-3 flags were judged too (%s)"
       % [f for f in flags if f not in FAULT_BITS])


def test_definitions_can_fail():
    for name, kw in (
            ("mainc 11 counted as open",
             dict(mainc_trips=lambda v: v != 12)),
            ("mainc 13 counted as closed",
             dict(mainc_trips=lambda v: v not in (11, 12, 13))),
            ("overTemperature not a fault",
             dict(flag_trips=lambda n: n in FAULT_BITS[:3])),
            ("acOverVoltage a fault",
             dict(flag_trips=lambda n: n in FAULT_BITS
                  or n == "BELINV_acOverVoltage"))):
        ok(definition_problems(**kw),
           "the definition test catches a core built to: %s" % name)


def monitor_cap_problems(inject_after_cap=False):
    """Spec 6: the cap is "override longer than 6 hours", so 6 h 5 min of
    MONITOR does not trip and stays MONITOR. inject_after_cap opens the
    contactors at 6 h 1 min, so test_monitor_cap_can_fail can show that a
    trip in this window is seen."""
    probs = []
    b = _armed()
    step = 250
    for i in range((CAP_MS + 5 * 60 * 1000) // step):
        b.t += step
        b.charger_status(12)
        bad = inject_after_cap and b.t >= CAP_MS + 60 * 1000
        b.bms(vmax=3.340, chg_max=300.0, mainc=0 if bad else 12)
        b.tick()
    if b.core.trip_reason is not None or b.state() != "MONITOR":
        probs.append("6 h 5 min of MONITOR ended in %s (%r)"
                     % (b.state(), b.core.trip_reason))
    return probs


def test_monitor_past_six_hours_does_not_trip():
    for p in monitor_cap_problems():
        FAILS.append("6 h cap, MONITOR: " + p)


def test_monitor_cap_can_fail():
    ok(monitor_cap_problems(inject_after_cap=True),
       "the 6 h MONITOR test sees a trip after 6 h when there is one")


# --- spec 6.1 session boundaries and SAFE -----------------------------------

def _boundary_standby(b):
    b.veh(M.CMD_ID, P.enc_master(0, P.MODE_STANDBY))


def _boundary_export(b):
    b.veh(M.CMD_ID, P.enc_master(0, P.MODE_EXPORT))


def _boundary_pilot_back(b):
    b.chg(M.CHG_PILOT, pilot(0))          # from 30 min, set after arming


def _boundary_silence(b):
    # 20.5 s with no charger frame at all, the BMS kept fresh
    _drain(b, _hold_bms_only, 205)


# (label, boundary, spec 6.1 "clears SAFE")
BOUNDARIES = (
    ("VCU STAND_BY", _boundary_standby, True),
    ("VCU EXPORT", _boundary_export, True),
    ("pilot timer steps back", _boundary_pilot_back, True),
    ("charger silent 20 s", _boundary_silence, False),
)


def boundary_problems(clears=None):
    """Spec 6.1 from SAFE: a boundary that clears SAFE leaves the core in
    PASSTHROUGH and re-armable; charger silence resets the session but keeps
    SAFE, so a charger that comes back cannot re-arm it. `clears` overrides
    the spec's column, for test_boundaries_can_fail."""
    probs = []
    for label, boundary, spec_clears in BOUNDARIES:
        want = spec_clears if clears is None else clears[label]
        for state in ("MONITOR", "OVERRIDE"):
            b = Bench()
            # Armed at 0 min and moved on to 30 afterwards: a core whose
            # first pilot reading is 2 min or more booted mid-session and
            # does not arm (spec 9), which the first version of this test
            # walked into.
            b.arm(vmax=3.340, chg_max=300.0, pilot_min=0)
            b.chg(M.CHG_PILOT, pilot(30))
            if state == "OVERRIDE":
                b.low_power()
            if b.state() != state:
                probs.append("%s: did not reach %s (%s)"
                             % (label, state, b.state()))
                continue
            b.bms_410(epo=1)                   # any trip will do
            _drain(b, _hold_all, 30)           # and the repeat, if any
            if b.state() != "SAFE":
                probs.append("%s/%s: the EPO did not latch SAFE (%s)"
                             % (label, state, b.state()))
                continue
            boundary(b)
            after = b.state()
            # whatever the boundary, the charger is (back) on and healthy,
            # and the core is offered a fresh session to arm on -- with the
            # pilot timer NOT moved. Re-arming at 0 min was itself a step
            # back from 30, a second boundary that clears SAFE, so it
            # masked what the boundary under test had done. (Whether a
            # charger returning from silence with a lower timer is a step
            # back is with the reviewer, 2026-10-08.)
            b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
            rearmed = b.state()
            if want:
                if after != "PASSTHROUGH":
                    probs.append("%s from SAFE (%s): %s, not PASSTHROUGH"
                                 % (label, state, after))
                if rearmed != "MONITOR":
                    probs.append("%s from SAFE (%s): a fresh session did not "
                                 "re-arm (%s)" % (label, state, rearmed))
            else:
                if after != "SAFE" or rearmed != "SAFE":
                    probs.append("%s from SAFE (%s): SAFE not kept -- %s at "
                                 "the boundary, %s when offered a session"
                                 % (label, state, after, rearmed))
    return probs


def test_session_boundaries_clear_or_keep_safe():
    for p in boundary_problems():
        FAILS.append("spec 6.1: " + p)


def test_boundaries_can_fail():
    for label, _b, spec_clears in BOUNDARIES:
        wrong = dict((l, c) for l, _x, c in BOUNDARIES)
        wrong[label] = not spec_clears
        ok(boundary_problems(clears=wrong),
           "the boundary test catches a core where '%s' %s SAFE"
           % (label, "keeps" if spec_clears else "clears"))


def _charger_back(b, pilot_min, ms=1000):
    """The charger answering again after a silence: status, a pilot reading
    and the BMS, for `ms`."""
    b.chg(M.CHG_PILOT, pilot(pilot_min))
    for _ in range(ms // 100):
        b.t += 100
        b.charger_status(12)
        b.bms(vmax=3.340, chg_max=300.0)
        b.tick()


SILENCE_CASES = [(st, cb) for st in ("MONITOR", "OVERRIDE")
                 for cb in ("later step-back", "STAND_BY")]


def run_silence_case(state, clear_by):
    """One spec 6.1 silence sequence, run to the end whatever the core does
    -> (bench, setup problem or None, state after the silence and the lower
    timer, state after `clear_by`). host_diff/make_unit_traces.py records
    each as a unit trace so the same inputs reach machine.cpp."""
    b = Bench()
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=0)
    b.chg(M.CHG_PILOT, pilot(30))
    if state == "OVERRIDE":
        b.low_power()
    if b.state() != state:
        return b, "did not reach %s (%s)" % (state, b.state()), None, None
    b.bms_410(epo=1)
    _drain(b, _hold_all, 30)
    if b.state() != "SAFE":
        return b, "the EPO did not latch SAFE (%s)" % b.state(), None, None
    _boundary_silence(b)                         # 20.5 s, BMS fresh
    _charger_back(b, 0)                          # 30 -> 0 across the silence
    b.arm(vmax=3.340, chg_max=300.0, pilot_min=None)
    after = b.state()
    if clear_by == "STAND_BY":
        _boundary_standby(b)
    else:
        _charger_back(b, 5)                      # counting on after it ...
        b.chg(M.CHG_PILOT, pilot(0))             # ... then a real step-back
    return b, None, after, b.state()


def silence_forgets_pilot_problems(kept=True):
    """Spec 6.1 (user, 2026-10-08): the charger-silence boundary forgets the
    last pilot-timer reading, so a timer that comes back LOWER after the
    silence is not a step-back and does not clear SAFE. SAFE then clears on
    the VCU's STAND_BY or EXPORT, or on a later step-back measured from the
    readings after the silence.

    `kept=False` inverts the expectation for the comparison, for
    test_silence_forgets_pilot_can_fail."""
    probs = []
    for state, clear_by in SILENCE_CASES:
        tag = "%s, then %s" % (state, clear_by)
        _b, setup, after, final = run_silence_case(state, clear_by)
        if setup:
            probs.append("%s: %s" % (tag, setup))
            continue
        if (after == "SAFE") != kept:
            probs.append("%s: a pilot timer back at 0 min after 20 s of "
                         "silence (30 min before it) left the core %s -- "
                         "spec 6.1: the silence forgets the last reading, "
                         "so it is not a step-back and SAFE stays"
                         % (tag, after))
            continue
        if kept and final != "PASSTHROUGH":
            probs.append("%s: still %s -- after the silence, SAFE clears "
                         "on %s (spec 6.1)" % (tag, final, clear_by))
    return probs


def test_silence_forgets_the_pilot_reading():
    for p in silence_forgets_pilot_problems():
        FAILS.append("spec 6.1 (2026-10-08): " + p)


def test_silence_forgets_pilot_can_fail():
    """The check discriminates: run with the spec's expectation and with the
    opposite one, exactly one of the two reports a problem -- whichever way
    the core under test behaves. (Asking only that the inverted run fail
    would itself fail on a core that gets 6.1 wrong, which is not the
    harness's fault.)"""
    right = silence_forgets_pilot_problems(kept=True)
    wrong = silence_forgets_pilot_problems(kept=False)
    ok(bool(right) != bool(wrong),
       "the silence-forgets-pilot check separates the two behaviours "
       "(spec expectation: %d problem(s); inverted: %d)"
       % (len(right), len(wrong)))


# --- the non-trips ----------------------------------------------------------

def test_spec_non_trips():
    """Spec 6 (B-5, B-7a, B-7b) and "cell temperature is not a trip": none
    of these trips from MONITOR, OVERRIDE or HOLD, and the core stays in its
    state -- except chg_max = 0 from OVERRIDE, which is the release (5.1)
    into HOLD (5.2, spec eb9b9d13; it was TERMINATED before HOLD existed).
    In HOLD chg_max 0 is what the hold follows down, not a trip (5.2)."""
    cases = (
        ("alarm TYPE2", dict(alarm=2)),
        ("cell_undervolt", dict(undervolt=1)),
        ("hot pack 60 degC", dict(tmax=60.0)),
        ("cold pack -30 degC", dict(tmax=-30.0)),
        ("chg_max 0", dict(chg_max=0.0)),
    )
    for label, kw in cases:
        for state in ("MONITOR", "OVERRIDE", "HOLD"):
            b = _entry(state)
            ok(b.state() == state, "%s: reached %s (was %s)"
               % (label, state, b.state()))
            for _ in range(100):               # 10 s
                b.t += 100
                b.charger_status(12)
                bk = dict(vmax=3.340, chg_max=300.0)
                bk.update(kw)
                b.bms(**bk)
                b.tick()
            ok(b.core.trip_reason is None,
               "%s from %s: no trip (%r)" % (label, state,
                                             b.core.trip_reason))
            want = state
            if label == "chg_max 0" and state == "OVERRIDE":
                want = "HOLD"
            ok(b.state() == want, "%s from %s: ends in %s (was %s)"
               % (label, state, want, b.state()))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        before = len(FAILS)
        try:
            t()
        except Exception as e:                            # noqa: BLE001
            FAILS.append("%s raised %r" % (t.__name__, e))
        print(("  ok  " if len(FAILS) == before else "FAIL  ") + t.__name__)
    if FAILS:
        print("\n%d failure(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\ntest_trips: %d fault sources, %d tests OK"
          % (len(CASES), len(tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
