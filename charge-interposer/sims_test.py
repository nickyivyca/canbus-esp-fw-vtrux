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
    the 12 V load (W, since P3a 2026-10-10) is the base until chg_max
    first reaches 0 A, each step applies
    at its delay after THAT moment (not after the start, not after any
    later 0 A), once, in delay order, and each is logged. `sched` replaces
    the class, for the can-fail."""
    import vehicle_sim as VS
    cls = sched or VS.AuxSchedule
    probs = []
    s = cls(266.0, [(120.0, 60.0), (60.0, 136.0)])
    a, lines = s.update(0.0, 300.0)
    if a != 266.0 or lines:
        probs.append("before chg_max 0: %.1f W, %r" % (a, lines))
    a, _l = s.update(500.0, 50.0)
    if a != 266.0:
        probs.append("a step applied before chg_max reached 0 A (%.1f)" % a)
    a, lines = s.update(1000.0, 0.0)
    if a != 266.0 or not any("reached 0 A at t=1000.0" in x for x in lines):
        probs.append("at chg_max 0: %.1f W, %r" % (a, lines))
    a, _l = s.update(1059.9, 0.0)
    if a != 266.0:
        probs.append("the 60 s step applied early (%.1f at +59.9 s)" % a)
    a, lines = s.update(1060.0, 0.25)       # chg_max moving off 0 is ignored
    if a != 136.0 or not any("266.0 -> 136.0 W" in x for x in lines):
        probs.append("the 60 s step not applied at +60 s: %.1f %r"
                     % (a, lines))
    a, _l = s.update(1100.0, 0.0)           # a later 0 A is not a new anchor
    if a != 136.0:
        probs.append("a second chg_max 0 moved the schedule (%.1f)" % a)
    a, lines = s.update(1120.0, 0.0)
    if a != 60.0 or not any("136.0 -> 60.0 W" in x for x in lines):
        probs.append("the 120 s step not applied at +120 s: %.1f %r"
                     % (a, lines))
    a, lines = s.update(5000.0, 0.0)
    if a != 60.0 or lines:
        probs.append("a step applied twice, or something logged late: %.1f "
                     "%r" % (a, lines))
    try:
        VS.parse_aux_step("10:-1")
        probs.append("parse_aux_step accepted a negative load")
    except ValueError:
        pass
    if VS.parse_aux_step("90:136") != (90.0, 136.0):
        probs.append("parse_aux_step('90:136') != (90.0, 136.0)")
    # the HV-side draw the load causes (spec 9, P3a): 0.559 + 1.512 P12/V
    d = VS.hv_draw_a(266.0, 416.8)
    if abs(d - (0.559 + 1.512 * 266.0 / 416.8)) > 1e-9:
        probs.append("hv_draw_a(266 W, 416.8 V) = %.4f A" % d)
    if not VS.hv_draw_a(136.0, 416.8) < d:
        probs.append("a lower 12 V load does not lower the HV draw")
    if AuxCheckLine.search(lines_of(cls)) is None:
        probs.append("the step line does not match run_scenario.AUX_STEP")
    return probs


def lines_of(cls):
    s = cls(266.0, [(10.0, 136.0)])
    s.update(0.0, 0.0)
    _a, lines = s.update(10.0, 0.0)
    return "\n".join(lines)


import run_scenario as _RS                                  # noqa: E402
AuxCheckLine = _RS.AUX_STEP


def bms_problems(rule=None, bms_cls=None):
    """bcm_chg_max follows the BMS's SoC (P3, user via the reviewer,
    2026-10-10), checked against the landmarks of the four genuine tops of
    charge, not against pack.py's own constants: about 30 A per % (the
    captures' 27-30), a step to 10-11 A at SoC 98.2-98.4 %, 3.0 A reached at
    99.46-99.64 %, 0 A at 99.95-100.00 %. And the BMS sim broadcasts that
    rule of its pack's reported SoC, whatever the leader cell is doing."""
    import pack as PK
    rule = rule or PK.bms_chg_max_soc
    bms_cls = bms_cls or VS.BMS
    probs = []
    socs = [s / 100.0 for s in range(9000, 10001)]      # 90.00 .. 100.00
    vals = [rule(s) for s in socs]
    slope = (rule(92.0) - rule(97.0)) / 5.0
    if not 27.0 <= slope <= 33.0:
        probs.append("ramp %.1f A/%% between SoC 92 and 97, not 27-33" % slope)
    drops = [(vals[i] - vals[i + 1], i) for i in range(len(vals) - 1)]
    big, i = max(drops)
    if big < 20.0:
        probs.append("no step: the largest drop per 0.01 %% SoC is %.1f A"
                     % big)
    elif not (98.15 <= socs[i + 1] <= 98.4 and 10.0 <= vals[i + 1] <= 11.0):
        probs.append("the step lands at %.2f A at SoC %.2f, not 10-11 A at "
                     "98.2-98.4" % (vals[i + 1], socs[i + 1]))
    at3 = next((s for s, v in zip(socs, vals) if v <= 3.0), None)
    if at3 is None or not 99.46 <= at3 <= 99.64:
        probs.append("3.0 A first reached at SoC %s, not 99.46-99.64" % at3)
    at0 = next((s for s, v in zip(socs, vals) if v <= 0.0), None)
    if at0 is None or not 99.95 <= at0 <= 100.0:
        probs.append("0 A first reached at SoC %s, not 99.95-100.00" % at0)
    if rule(100.0) != 0.0:
        probs.append("not 0 A at SoC 100 (%.2f)" % rule(100.0))
    # the BMS sim: the same rule of the pack's SoC, the leader irrelevant.
    # The imbalanced packs put the leader well up the knee at SoC 99.5.
    for soc in (95.0, 99.0, 99.5):
        pk = PK.Pack(soc_pct=soc, imbalance_pct=1.0, outlier_cell=7,
                     outlier_pct=2.5)
        got = bms_cls(pk).chg_max
        want = rule(pk.soc_pct)
        if abs(got - want) > 1e-9:
            probs.append("BMS sim at SoC %.2f (leader %.3f V) broadcasts "
                         "%.2f A, the rule says %.2f" % (pk.soc_pct, pk.vmax,
                                                         got, want))
    return probs


def high_cell_problems(fmt=None, outlier_pct=-5.0):
    """Spec 9's high-cell scenarios (2026-10-10): a negative --outlier-pct
    makes that cell the pack's leader, and vehicle_sim's bus-SoC-100 line is
    the one run_scenario.SOC100 reads. The line is logged when bcm_soc on the
    bus first reads 100, which is from a pack mean of 99.75 % (0.5 % LSB)."""
    import pack as PK
    probs = []
    pk = PK.Pack(soc_pct=95.0, imbalance_pct=0.4, outlier_cell=7,
                 outlier_pct=outlier_pct)
    v = pk.cell_voltages()
    if max(range(len(v)), key=lambda i: v[i]) != 7:
        probs.append("--outlier-pct %.1f: cell 7 is not the leader (%.3f V "
                     "vs vmax %.3f V)" % (outlier_pct, v[7], max(v)))
    line = (fmt or VS.SOC100_FMT) % 1234.5
    m = _RS.SOC100.search(line)
    if m is None or float(m.group(1)) != 1234.5:
        probs.append("vehicle_sim's SoC-100 line %r is not what "
                     "run_scenario.SOC100 reads" % line)
    for soc, want in ((99.74, False), (99.76, True)):
        got = P.decode(P.EPRI, 0x410, P.encode(P.EPRI, 0x410, {
            "bcm_soc": soc}))["bcm_soc"] >= 100.0
        if got != want:
            probs.append("bcm_soc %.2f reads %s100 on the bus"
                         % (soc, "" if got else "below "))
    return probs


def vport_problems(port_cls=None, src=None):
    """interposer_sim's PORT side of spec 2.3 (d9f49ea6 / d8c9e87d, user
    2026-10-13). The mode is the core's (`listen_only`); the sim applies
    it: not normal before anything is applied; apply() reports a switch
    only when the core's mode differs from the port's. And in
    interposer_sim's loop, read from its source in order: the power-up
    round goes out BEFORE the first drain and is followed by the first
    apply; each core call is applied before its frames are emitted; and a
    frame toward the vehicle is dropped unless the port is normal (bar the
    power-up round)."""
    import interposer_sim as IS
    probs = []
    pm = (port_cls or IS.PortMode)()

    class Core(object):
        listen_only = True
    c = Core()
    if pm.normal:
        probs.append("the port is normal before anything is applied")
    if pm.apply(c) is not None:
        probs.append("apply() reported a switch with the core listen-only")
    c.listen_only = False
    if pm.apply(c) != "normal" or not pm.normal:
        probs.append("apply() did not switch to normal with the core normal")
    if pm.apply(c) is not None:
        probs.append("apply() reported a second switch with no change")
    c.listen_only = True
    if pm.apply(c) != "listen-only" or pm.normal:
        probs.append("apply() did not fall back with the core listen-only")
    if src is None:
        src = open(os.path.join(HERE, "interposer_sim.py"),
                   encoding="utf-8").read()
    loop = src[src.index("while running[0]:"):]

    def order(*needles):
        """each needle found AFTER the previous one, the first anywhere;
        the next occurrence of the first needle must not come before the
        last one (so the three belong to one call site)"""
        at = loop.find(needles[0])
        if at < 0:
            return False
        for n in needles[1:]:
            nxt = loop.find(n, at + 1)
            if nxt < 0:
                return False
            at = nxt
        again = loop.find(needles[0], loop.find(needles[0]) + 1)
        return again < 0 or again > at
    if not order('core.diag_frames(now_ms, 0, 0, now_ms, True',
                 'apply_mode("power-up round")', "veh.recv(timeout=0.0)"):
        probs.append("the power-up round and its first apply do not come "
                     "before the first drain")
    if not order("core.on_charger_frame(", 'apply_mode("charger frame',
                 "for out in outs:"):
        probs.append("a charger frame's call is not applied before its "
                     "frames are emitted")
    if not order("core.tick(now_ms)", 'apply_mode("tick")', "for out in outs:"):
        probs.append("a tick is not applied before its frames are emitted")
    if "if not (args.bypass or port.normal or sending_boot[0]):" not in src:
        probs.append("emit() no longer drops frames toward the vehicle in "
                     "listen-only")
    return probs


def help_problems():
    """Each sim's argument parser builds: `--help` exits 0. A broken help
    string (argparse formats it lazily) otherwise only shows when a
    scenario starts the sim, as a crash in its log (2026-10-10)."""
    import subprocess
    probs = []
    for sim in ("vehicle_sim.py", "charger_sim.py", "interposer_sim.py"):
        r = subprocess.run([sys.executable, os.path.join(HERE, sim),
                            "--help"], capture_output=True, text=True)
        if r.returncode != 0:
            probs.append("%s --help exited %d: %s"
                         % (sim, r.returncode, r.stderr.strip()[-200:]))
    return probs


def main():
    hp = help_problems()
    for p in hp:
        check(False, p)
    check(not hp, "every sim's --help exits 0 (its argument parser builds)")

    for p in bms_problems():
        check(False, "vehicle_sim BMS: " + p)
    check(not bms_problems(), "vehicle_sim's bcm_chg_max follows the BMS's "
          "SoC: ~30 A/%, a step to 10-11 A at 98.2-98.4 %, 3.0 A at "
          "99.46-99.64 %, 0 A at 99.95-100 % (the four tops of charge)")
    import pack as _PK

    class LeaderBMS(VS.BMS):
        @property
        def chg_max(self):
            return _PK.bms_chg_max(self.pack.vmax)
    check(bool(bms_problems(bms_cls=LeaderBMS)),
          "can fail: the leader-voltage chg_max of before 2026-10-10 is "
          "caught")
    check(bool(bms_problems(rule=lambda s: max(0.0, min(300.0, 6.0 * (100.0 - s)))
                            )),
          "can fail: a rule with no ramp and no step (6 x (100 - SoC) "
          "throughout) is caught")
    check(bool(bms_problems(rule=lambda s: (
        0.0 if s >= 99.5 else _PK.bms_chg_max_soc(s)))),
          "can fail: a rule that reaches 0 A at SoC 99.5 is caught")
    check(bool(bms_problems(rule=lambda s: (
        _PK.bms_chg_max_soc(s) if s < 98.2 else 8.0 * (100.0 - s)))),
          "can fail: a tail of 8 x (100 - SoC) (3.0 A at 99.63, but the step "
          "lands at 14 A) is caught")
    for p in high_cell_problems():
        check(False, "vehicle_sim: " + p)
    check(not high_cell_problems(), "vehicle_sim: a negative --outlier-pct "
          "is the pack's leader, and its bus-SoC-100 line is the form "
          "run_scenario reads")
    check(bool(high_cell_problems(outlier_pct=5.0)),
          "can fail: a positive (low) outlier is not taken for the leader")
    check(bool(high_cell_problems(fmt="BMS: SoC 100 at t=%.1fs")),
          "can fail: a reworded SoC-100 line is caught")
    for p in aux_problems():
        check(False, "vehicle_sim: " + p)
    check(not aux_problems(), "vehicle_sim --aux-step: 12 V load steps "
          "timed from the first chg_max 0 A, each once, in order, logged in "
          "the form run_scenario reads; hv_draw_a is the P3a line")

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

    for p in vport_problems():
        check(False, "interposer_sim: " + p)
    check(not vport_problems(), "interposer_sim: the spec 2.3 port side "
          "(apply the core's mode after each call and before its frames; "
          "the power-up round first; nothing toward the vehicle in "
          "listen-only)")
    import interposer_sim as _IS

    class StartsNormal(_IS.PortMode):
        def __init__(self):
            _IS.PortMode.__init__(self)
            self.normal = True

    class Inverted(_IS.PortMode):
        def apply(self, core):
            class Flip(object):
                listen_only = not core.listen_only
            return _IS.PortMode.apply(self, Flip())

    class Sticky(_IS.PortMode):
        def apply(self, core):
            if self.normal:
                return None
            return _IS.PortMode.apply(self, core)
    for cls, what in ((StartsNormal, "a port normal before anything is "
                                     "applied"),
                      (Inverted, "a port applying the opposite mode"),
                      (Sticky, "a port that never falls back")):
        check(bool(vport_problems(cls)), "can fail: %s is caught" % what)
    real = open(os.path.join(HERE, "interposer_sim.py"),
                encoding="utf-8").read()
    for needle, what in (('apply_mode("charger frame', "the charger call's "
                          "apply"),
                         ('apply_mode("tick")', "the tick's apply"),
                         ('apply_mode("power-up round")', "the first apply "
                          "after the power-up round")):
        check(bool(vport_problems(src=real.replace(needle, "pass  # ", 1))),
              "can fail: interposer_sim without %s is caught" % what)
    moved = real.replace("if not (args.bypass or port.normal or "
                         "sending_boot[0]):", "if False:", 1)
    check(bool(vport_problems(src=moved)),
          "can fail: interposer_sim without the listen-only drop is caught")

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
