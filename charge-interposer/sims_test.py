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

def main():
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
