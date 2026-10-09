"""Tests the simulators against spec 9's behaviour (tester, 2026-10-08).

Driven directly -- no bus, no processes -- so each behaviour is checked in
isolation and against the spec's MEASURED RANGE, not the sim's own constant:

vehicle_sim (the VCU's reaction to the charger leaving state 12, by
shutdownSource; user, 2026-10-07 / 2026-10-08):
  - 11 with vehicleConnected dropped: flow 0 after 0.8-1.1 s;
  - 3: 0.2-0.7 s; 14: 2.9 s; 4: ~0.15 s (the BMS's EPO leads);
  - 11 with the plug still in: flow kept on for 84 s, then flow 0 and
    STAND_BY of its own; and if the plug comes out first, the handle-pull
    reaction runs from that edge;
  - 0 and 1 are ridden through; any other source is a simulator error, not
    reacted to;
  - STAND_BY follows the flow drop out of the hold by ~250 ms.
charger_sim (BELINV_chargePilotOnlineTime; user, 2026-10-08):
  - the timer holds at the handle pull and returns to 0 0.2-1.3 s after the
    VCU's STAND_BY; with no STAND_BY it holds; a replug restarts it at 0;
  - --stop11-at: 15 with shutdownSource 11 and vehicleConnected still 1,
    delivering nothing.

Each problems function takes an override that builds a wrong sim, and
test_*_can_fail requires it to report a problem.

Run:  python charge-interposer/sims_test.py
"""

import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import protocol as P               # noqa: E402
import vehicle_sim as VS           # noqa: E402
import charger_sim as CS           # noqa: E402

logging.disable(logging.CRITICAL)
failures = 0

STEP = 0.01                         # 10 ms of sim time per update


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


# --- vehicle_sim -------------------------------------------------------------

def vcu_reaction(src, veh_conn, unplug_after=None, horizon=120.0):
    """A VCU in its mode-3 hold sees the charger leave 12 at t=10 s with
    `src`; -> (flow-drop delay or None, STAND_BY gap or None)."""
    v = VS.VCU()
    v.start(0.0)
    v.on_charger(0.5, P.CHG_STATE_CHARGING, 0, 1)
    v._complete(1.0)                        # into the hold
    t0 = 10.0
    t = t0
    conn = veh_conn
    while t < t0 + horizon:
        if unplug_after is not None and t >= t0 + unplug_after:
            conn = 0
        v.on_charger(t, P.CHG_STATE_FAULT, src, conn)
        v.update(t, STEP, 80.0, 100.0)
        if v.standby_t is not None:
            break
        t += STEP
    drop = None if v.flow_off_t is None else v.flow_off_t - t0
    gap = (None if v.standby_t is None or v.flow_off_t is None
           else v.standby_t - v.flow_off_t)
    return drop, gap


def vcu_problems(table=None, plug_in_s=None):
    saved = (dict(VS.REACT_BY_SOURCE), VS.SOURCE11_PLUG_IN_S)
    if table is not None:
        VS.REACT_BY_SOURCE.clear()
        VS.REACT_BY_SOURCE.update(table)
    if plug_in_s is not None:
        VS.SOURCE11_PLUG_IN_S = plug_in_s
    probs = []
    try:
        tol = STEP * 1.5
        for src, lo, hi in ((11, 0.8, 1.1), (3, 0.2, 0.7), (14, 2.9, 2.9),
                            (4, 0.1, 0.2)):
            drop, gap = vcu_reaction(src, 0)
            if drop is None or not lo - tol <= drop <= hi + tol:
                probs.append("source %d: flow 0 after %s s, spec %.2f-%.2f s"
                             % (src, drop, lo, hi))
            if gap is None or abs(gap - 0.25) > 0.02:
                probs.append("source %d: STAND_BY %s s after the flow drop "
                             "out of the hold, spec ~0.25 s" % (src, gap))
        drop, gap = vcu_reaction(11, 1, horizon=120.0)
        if drop is None or abs(drop - 84.0) > tol:
            probs.append("source 11, plug in: flow 0 after %s s, spec 84 s"
                         % drop)
        if gap is None:
            probs.append("source 11, plug in: no STAND_BY of its own")
        drop, _gap = vcu_reaction(11, 1, unplug_after=30.0)
        if drop is None or not 30.8 - tol <= drop <= 31.1 + tol:
            probs.append("source 11, plug in, then the plug out at +30 s: "
                         "flow 0 after %s s, spec 30.8-31.1 s" % drop)
        for src in (0, 1, 7, None):
            drop, _gap = vcu_reaction(src, 1, horizon=20.0)
            if drop is not None:
                probs.append("source %r: flow dropped after %.2f s; spec 9 "
                             "rides 0 and 1 through and treats any other "
                             "source as a simulator error" % (src, drop))
    finally:
        VS.REACT_BY_SOURCE.clear()
        VS.REACT_BY_SOURCE.update(saved[0])
        VS.SOURCE11_PLUG_IN_S = saved[1]
    return probs


# --- charger_sim -------------------------------------------------------------

def pilot_min(c):
    return P.decode(P.BEL, 0x18FFD8C0,
                    c.slow_frames()[0x18FFD8C0])["BELINV_chargePilotOnlineTime"]


def charger_run(standby_at=None, replug_at=None, until=130.0, delay=None):
    """30 min into a session, the handle pulled at 100 s -> [(t, minutes)]."""
    c = CS.Charger()
    if delay is not None:
        c.pilot_reset_after_standby_s = delay
    c.on_command(P.enc_master(1, P.MODE_LOW_POWER), 0.0)
    c.pilot_online_s = 1800.0
    t = 99.0
    seen = []
    while t < until:
        if abs(t - 100.0) < STEP / 2:
            c.unplug(t)
        if standby_at is not None and abs(t - standby_at) < STEP / 2:
            c.on_command(P.enc_master(0, P.MODE_STANDBY), t)
        if replug_at is not None and abs(t - replug_at) < STEP / 2:
            c.replug(t)
        c.update(STEP, t)
        seen.append((round(t, 3), pilot_min(c)))
        t += STEP
    return seen


def first_zero(seen):
    for t, m in seen:
        if m == 0:
            return t
    return None


def charger_problems(delay=None):
    probs = []
    seen = charger_run(standby_at=101.0, delay=delay)
    at_pull = [m for t, m in seen if 100.0 <= t < 101.0]
    if not at_pull or min(at_pull) != 30:
        probs.append("the timer did not hold at 30 min between the pull and "
                     "the STAND_BY (%s)" % sorted(set(at_pull)))
    z = first_zero(seen)
    if z is None or not 101.2 - STEP <= z <= 102.3 + STEP:
        probs.append("first 0 at %s s; spec: 0.2-1.3 s after the STAND_BY "
                     "at 101.0 s" % z)
    seen = charger_run(standby_at=None, until=140.0, delay=delay)
    if first_zero(seen) is not None:
        probs.append("with no STAND_BY the timer still went to 0 at %s s"
                     % first_zero(seen))
    seen = charger_run(standby_at=None, replug_at=120.0, delay=delay)
    z = first_zero(seen)
    if z is None or abs(z - 120.0) > 2 * STEP:
        probs.append("a replug at 120 s did not restart the timer at 0 "
                     "(first 0 at %s)" % z)
    return probs


def stop11_problems():
    c = CS.Charger()
    c.on_command(P.enc_master(1, P.MODE_CHARGER), 0.0)
    c.pack_v = 400.0
    t = 0.0
    while t < 5.0:
        c.update(STEP, t)
        t += STEP
    c.stop11_t = t
    while t < 8.0:
        c.update(STEP, t)
        t += STEP
    probs = []
    if (c.state, c.shutdown_source) != (P.CHG_STATE_FAULT, 11):
        probs.append("--stop11-at: state %d source %d, spec 15 / 11"
                     % (c.state, c.shutdown_source))
    if c.vehicle_connected != 1 or c.evse_connected != 1:
        probs.append("--stop11-at: connected flags dropped (%d/%d); the plug "
                     "is still in" % (c.vehicle_connected, c.evse_connected))
    if abs(c.current) > 0.05:
        probs.append("--stop11-at: still delivering %.2f A" % c.current)
    return probs


# --- charger_sim: the Bel's status-page cadence and fault-flag timing -------------

def cadence_problems(n=12):
    """0x18FFD4C0 goes out one page per 100 ms frame, rotating mux 0-3, so
    each page every 0.4 s with mux 3 just before mux 0 (measured on the Bel,
    charger_fault_flag_timing.txt); none of it in the slow frames."""
    c = CS.Charger()
    seq = []
    for _ in range(n):
        d = c.frames().get(0x18FFD4C0)
        seq.append(None if d is None else
                   P.decode(P.BEL, 0x18FFD4C0, d)["BELINV_statusMultiplexer"])
    probs = []
    if seq != [i % 4 for i in range(n)]:
        probs.append("0x18FFD4C0 pages %s, not 0,1,2,3 repeating" % seq)
    if 0x18FFD4C0 in c.slow_frames():
        probs.append("0x18FFD4C0 still in the slow frames")
    return probs


def fault_timing(lead):
    """-> (t flag raised, t state left 12) for a fault injected at 10 s."""
    c = CS.Charger()
    c.fault_flag_lead_s = lead
    c.on_command(P.enc_master(1, P.MODE_CHARGER), 0.0)
    c.pack_v = 400.0
    t = 0.0
    t_flag = t_edge = None
    injected = False
    while t < 13.0:
        if not injected and t >= 10.0:
            c.inject_fault("inverter", t)
            injected = True
        c.update(STEP, t)
        if t_flag is None and c.fault_flags["inverter"]:
            t_flag = t
        if (t_edge is None and injected and c.state == P.CHG_STATE_FAULT
                and c.shutdown_source == 3):
            t_edge = t
        t += STEP
    return t_flag, t_edge


def fault_flag_problems(lead=None, want_lo=0.1, want_hi=0.5):
    """Default: the flag leads the state edge by 0.1-0.5 s (16 of 19 source-3
    endings). want_lo/hi negative express the minority (flag after edge)."""
    t_flag, t_edge = fault_timing(0.3 if lead is None else lead)
    if t_flag is None or t_edge is None:
        return ["flag at %s, edge at %s" % (t_flag, t_edge)]
    d = t_edge - t_flag
    if not want_lo - STEP <= d <= want_hi + STEP:
        return ["flag leads the edge by %.2f s, wanted %.2f-%.2f s"
                % (d, want_lo, want_hi)]
    return []


# --- bus.py's multicast guard ----------------------------------------------------

def guard_problems():
    """bus._confine_to_host must FAIL CLOSED (2026-10-08): no reachable
    socket, or a TTL it cannot get to 0, is a SystemExit, never a silent
    return. A real UDP socket (never sent on) must pass and read TTL 0."""
    import socket
    import types
    import bus as B
    probs = []

    def refuses(b, fragment):
        try:
            B._confine_to_host(b)
        except SystemExit as e:
            return fragment in str(e), str(e)
        return False, "returned without raising"

    ns = types.SimpleNamespace
    for label, b in (("no _multicast", ns()),
                     ("_multicast without _socket", ns(_multicast=ns()))):
        okk, why = refuses(b, "cannot reach the multicast socket")
        if not okk:
            probs.append("a bus with %s: %s" % (label, why))

    class StuckTTL(object):
        def setsockopt(self, *a):
            pass

        def getsockopt(self, *a):
            return 1
    okk, why = refuses(ns(_multicast=ns(_socket=StuckTTL())),
                       "firmware/esp-development.md")
    if not okk or "TTL is 1" not in why:
        probs.append("a socket stuck at TTL 1: %s" % why)

    real = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            B._confine_to_host(ns(_multicast=ns(_socket=real)))
        except SystemExit as e:
            probs.append("a real UDP socket was refused: %s" % e)
        if real.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL) != 0:
            probs.append("a real UDP socket did not end at TTL 0")
    finally:
        real.close()
    return probs


# --- tests ---------------------------------------------------------------------

def measured():
    """The observed values the checks below judge, printed so a run file
    shows observed against the spec and not only ok."""
    out = []
    for src, lo, hi in ((11, 0.8, 1.1), (3, 0.2, 0.7), (14, 2.9, 2.9),
                        (4, 0.1, 0.2)):
        drop, gap = vcu_reaction(src, 0)
        out.append("vehicle_sim source %d, plug out: flow 0 after %s s (spec "
                   "%.2f-%.2f s), STAND_BY %s s after it"
                   % (src, _f(drop), lo, hi, _f(gap)))
    drop, gap = vcu_reaction(11, 1, horizon=120.0)
    out.append("vehicle_sim source 11, plug in: flow 0 after %s s (spec 84 s), "
               "then its own STAND_BY %s s after it" % (_f(drop), _f(gap)))
    seen = charger_run(standby_at=101.0)
    held = sorted(set(m for t, m in seen if 100.0 <= t < 101.0))
    z = first_zero(seen)
    out.append("charger_sim pilot timer: %s min between the pull (100.0 s) "
               "and the STAND_BY (101.0 s); 0 at %s s, %s s after the "
               "STAND_BY (spec 0.2-1.3 s)"
               % (held, _f(z), _f(None if z is None else z - 101.0)))
    return out


def _f(x):
    return "-" if x is None else "%.2f" % x


def aux_problems(sched=None):
    """vehicle_sim.AuxSchedule (2026-10-09, spec 9's injected load steps):
    the draw is the base until chg_max first reaches 0 A, each step applies
    at its delay after THAT moment (not after the start, not after any
    later 0 A), once, in delay order, and each is logged. `sched` replaces
    the class, for the can-fail."""
    import vehicle_sim as VS
    cls = sched or VS.AuxSchedule
    probs = []
    s = cls(2.9, [(120.0, 1.0), (60.0, 2.2)])
    a, lines = s.update(0.0, 300.0)
    if a != 2.9 or lines:
        probs.append("before chg_max 0: %.2f A, %r" % (a, lines))
    a, _l = s.update(500.0, 50.0)
    if a != 2.9:
        probs.append("a step applied before chg_max reached 0 A (%.2f)" % a)
    a, lines = s.update(1000.0, 0.0)
    if a != 2.9 or not any("reached 0 A at t=1000.0" in x for x in lines):
        probs.append("at chg_max 0: %.2f A, %r" % (a, lines))
    a, _l = s.update(1059.9, 0.0)
    if a != 2.9:
        probs.append("the 60 s step applied early (%.2f at +59.9 s)" % a)
    a, lines = s.update(1060.0, 0.25)       # chg_max moving off 0 is ignored
    if a != 2.2 or not any("2.90 -> 2.20" in x for x in lines):
        probs.append("the 60 s step not applied at +60 s: %.2f %r"
                     % (a, lines))
    a, _l = s.update(1100.0, 0.0)           # a later 0 A is not a new anchor
    if a != 2.2:
        probs.append("a second chg_max 0 moved the schedule (%.2f)" % a)
    a, lines = s.update(1120.0, 0.0)
    if a != 1.0 or not any("2.20 -> 1.00" in x for x in lines):
        probs.append("the 120 s step not applied at +120 s: %.2f %r"
                     % (a, lines))
    a, lines = s.update(5000.0, 0.0)
    if a != 1.0 or lines:
        probs.append("a step applied twice, or something logged late: %.2f "
                     "%r" % (a, lines))
    try:
        VS.parse_aux_step("10:-1")
        probs.append("parse_aux_step accepted a negative draw")
    except ValueError:
        pass
    if VS.parse_aux_step("90:1.5") != (90.0, 1.5):
        probs.append("parse_aux_step('90:1.5') != (90.0, 1.5)")
    return probs


def main():
    for p in aux_problems():
        check(False, "vehicle_sim: " + p)
    check(not aux_problems(), "vehicle_sim --aux-step: steps timed from the "
          "first chg_max 0 A, each once, in order, logged")

    import vehicle_sim as _VS

    class FromStart(_VS.AuxSchedule):
        def update(self, t, chg_max):
            if self.t_chg0 is None:
                self.t_chg0 = 0.0           # anchored at the start instead
            return _VS.AuxSchedule.update(self, t, chg_max)

    class ReAnchors(_VS.AuxSchedule):
        def update(self, t, chg_max):
            if chg_max is not None and chg_max <= 0.0 and \
                    self.t_chg0 is not None and t - self.t_chg0 > 50:
                self.t_chg0 = t             # every 0 A restarts the clock
            return _VS.AuxSchedule.update(self, t, chg_max)
    check(bool(aux_problems(FromStart)),
          "can fail: a schedule timed from the start, not from chg_max 0 A, "
          "is caught")
    check(bool(aux_problems(ReAnchors)),
          "can fail: a schedule that re-anchors on a later chg_max 0 A is "
          "caught")

    for line in measured():
        print("  --  " + line)
    for p in vcu_problems():
        check(False, "vehicle_sim: " + p)
    check(not vcu_problems(), "vehicle_sim reacts per shutdownSource as spec 9 "
          "says (11, 3, 14, 4, 11 with the plug in, 0/1 ridden through, any "
          "other source not reacted to)")
    check(bool(vcu_problems(table={11: 1.0, 3: 0.5, 14: 0.5, 4: 0.5})),
          "can fail: a VCU that reacts by mode, not by source, is caught")
    check(bool(vcu_problems(plug_in_s=0.95)),
          "can fail: a VCU that treats source 11 with the plug in as a pull "
          "is caught")

    for p in charger_problems():
        check(False, "charger_sim: " + p)
    check(not charger_problems(), "charger_sim's pilot timer holds at the "
          "pull, returns to 0 after the STAND_BY, holds with no STAND_BY, "
          "restarts at a replug")
    check(bool(charger_problems(delay=2.0)),
          "can fail: a reset 2.0 s after the STAND_BY is caught")
    check(bool(charger_problems(delay=0.0)),
          "can fail: a reset at the STAND_BY itself is caught")

    for p in stop11_problems():
        check(False, "charger_sim: " + p)
    check(not stop11_problems(), "charger_sim --stop11-at stops with source "
          "11, plug in, delivering nothing")

    for p in cadence_problems() + fault_flag_problems():
        check(False, "charger_sim: " + p)
    check(not cadence_problems(), "charger_sim rotates 0x18FFD4C0 mux 0-3, "
          "one page per 100 ms frame, none in the slow frames")
    check(not fault_flag_problems(), "charger_sim raises the fault flag 0.1-"
          "0.5 s before the state edge by default")
    check(not fault_flag_problems(lead=-0.2, want_lo=-0.25, want_hi=-0.15),
          "--fault-flag-lead-s -0.2 gives the minority order (flag after the "
          "edge)")
    check(bool(fault_flag_problems(lead=0.0)),
          "can fail: a flag raised with the state edge is caught")
    check(bool(fault_flag_problems(lead=-0.2)),
          "can fail: the minority order is not mistaken for the default")

    for p in guard_problems():
        check(False, "bus.py guard: " + p)
    check(not guard_problems(), "bus.py's multicast guard fails closed: no "
          "socket or a TTL stuck at 1 refuses to start, a real socket passes "
          "at TTL 0")

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nsims_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
