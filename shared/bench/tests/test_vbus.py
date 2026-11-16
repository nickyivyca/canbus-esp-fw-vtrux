"""Tests of the wall-clock slots, the per-check virtual buses and their
isolation monitor (vtrux_bench/run/vbus.py and the engine's rules
wallclock_slots, slot_concurrency, bus_alloc, bus_precheck,
isolation_void). Linux only (ss, /proc). No hardware.

    python -B shared/bench/tests/test_vbus.py            every test
    python -B shared/bench/tests/test_vbus.py --can-fail  the control first
        (every test unchanged, which must pass), then each test again with
        a rule it guards switched off (MUTANTS: every rule of vbus.RULES and
        the engine's five has one); each must then fail

The checks run a fake simulator (SIM, written to this run's folder) that
opens each segment of its VTRUX_BUS_ALLOC as python-can's udp_multicast
does (bound to the port on every address, SO_REUSEADDR and SO_REUSEPORT,
joined to the group, TTL 0) and sends on it for a while, from that socket
("bound", as python-can does) or from one of its own ("own"); it writes
when it started and ended. Foreign traffic comes from separate processes
without the check's token. Every socket that sends joins the group first,
so TTL 0 keeps every datagram on this host (AGENTS.md "A virtual CAN bus is
not automatically local"). The groups and ports are this run's own: groups
239.0.<200..>.x and ports from 45000 + 10 x (pid mod 400), so no bench of
another session is touched. Scratch is this run's own folder (_scratch.py).
"""

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                       # noqa: E402
from vtrux_bench.run import engine as E               # noqa: E402
from vtrux_bench.run import vbus as VB                # noqa: E402
from vtrux_bench.run.model import Check, Group, Suite  # noqa: E402

PY = sys.executable
SCRATCH = _scratch.run_dir("vbus")
PORT_BASE = 45000 + 10 * (os.getpid() % 400)
GROUP_THIRD = 200
SEGS = ["a", "b"]

SIM = os.path.join(SCRATCH, "_fake_sim.py")
with open(SIM, "w", encoding="utf-8", newline="\n") as _f:
    _f.write(r'''import json, os, socket, struct, sys, time
mode, dur, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
alloc = os.environ.get("VTRUX_BUS_ALLOC") or sys.argv[4]
segs = []
for part in alloc.split(","):
    seg, rest = part.split("=", 1)
    g, p = rest.rsplit(":", 1)
    segs.append((seg, g, int(p)))
t0 = time.time()
socks = []
for seg, g, p in segs:
    if mode in ("bound", "own", "hold"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("", p))
    else:                       # "foreign": a sender of its own, joined, on no allocated port
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.bind(("", 0))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(g), socket.inet_aton("0.0.0.0")))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    socks.append((s, g, p))
own = None
if mode == "own":
    own = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    own.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    own.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
end = time.time() + dur
n = 0
while time.time() < end:
    if mode != "hold":
        for s, g, p in socks:
            (own or s).sendto(b"frame", (g, p))
            n += 1
    time.sleep(0.02)
with open(os.path.join(out, "sim.json"), "w") as f:
    json.dump({"t0": t0, "t1": time.time(), "sent": n,
               "alloc": os.environ.get("VTRUX_BUS_ALLOC"),
               "token": bool(os.environ.get("VTRUX_CHECK_TOKEN"))}, f)
''')


def settings(env_root, slots=1, **bus):
    b = {"group_third": GROUP_THIRD, "port_base": PORT_BASE, "port_stride": 10,
         "listen_s": 0.5, "poll_s": 0.3}
    b.update(bus)
    return dict(runs_root=os.path.join(env_root, "runs"),
                bench_holder=os.path.join(env_root, "bench-holder.md"),
                hosts={"local": {"python": [PY, "-B"], "wallclock_slots": slots}},
                scratch=env_root, pause_poll_s=0.2, wallclock_poll_s=0.2, bus_alloc=b)


def root(name):
    """A fresh folder for one run of a test (each mutant runs it again)."""
    d = os.path.join(SCRATCH, "%s_%d" % (name, time.monotonic_ns()))
    os.makedirs(d)
    with open(os.path.join(d, "bench-holder.md"), "w", encoding="utf-8") as f:
        f.write("# Bench holder\n\n## Lock\n\n**Holder:** nobody\n")
    return d


def sim(cid, mode="bound", dur=2.0, **kw):
    return Check(cid, [SIM, mode, str(dur), "{outdir}", "{bus_alloc}"], python=True,
                 cls="wallclock", bus=list(SEGS), timeout_s=60, **kw)


def run(suite_groups, s):
    eng = E.Engine(Suite(name="t", role="tester (test-suite)", groups=suite_groups), s,
                   out=lambda *a: None)
    rec = eng.run()
    return eng, rec


def results(rec):
    return {r["id"]: r for r in rec["results"]}


def events(eng, kind):
    out = []
    with open(os.path.join(eng.run_dir, "progress.jsonl"), encoding="utf-8") as f:
        for ln in f:
            e = json.loads(ln)
            if e.get("type") == "event" and e.get("kind") == kind:
                out.append(e)
    return out


def sim_out(eng, group, cid):
    p = os.path.join(eng.run_dir, group, cid, "sim.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def foreign(mode, alloc, dur):
    """A process without the check's token: a sender of its own ("foreign")
    or a holder of the allocated ports ("hold")."""
    env = {k: v for k, v in os.environ.items() if k not in (VB.TOKEN_ENV, VB.ALLOC_ENV)}
    out = os.path.join(SCRATCH, "_foreign_%s_%d" % (mode, time.monotonic_ns()))
    os.makedirs(out)
    return subprocess.Popen([PY, "-B", SIM, mode, str(dur), out, VB.format_alloc(alloc)],
                            env=env)


def slot0():
    return VB.allocation(settings(SCRATCH), 0, SEGS)


def overlap(a, b):
    return min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])


# ---- the tests --------------------------------------------------------------
def test_slots_side_by_side():
    """With two slots, a group's three slotted checks run two at a time, each
    on its own slot's bus (its own groups and ports, given as {bus_alloc}
    and VTRUX_BUS_ALLOC, with a token), never three at once; each sends from
    the socket it receives on, as python-can does, and its monitor counts
    its segments and finds nothing foreign: all PASS, no VOID."""
    s = settings(root("side"), slots=2)
    eng, rec = run([Group("m", [sim("s1"), sim("s2"), sim("s3")])], s)
    r = results(rec)
    assert all(r["m/%s" % c]["verdict"] == "PASS" for c in ("s1", "s2", "s3")), \
        {k: (v["verdict"], v.get("detail")) for k, v in r.items()}
    o = {c: sim_out(eng, "m", c) for c in ("s1", "s2", "s3")}
    assert all(x["token"] for x in o.values()), o
    allocs = [x["alloc"] for x in o.values()]
    assert o["s1"]["alloc"] != o["s2"]["alloc"], allocs
    assert overlap(o["s1"], o["s2"]) > 1.0, "s1 and s2 did not run side by side"
    assert overlap(o["s1"], o["s3"]) <= 0 or overlap(o["s2"], o["s3"]) <= 0, \
        "three ran at once on two slots"
    iso = {e["check"]: e for e in events(eng, "isolation")}
    assert set(iso) == {"m/s1", "m/s2", "m/s3"}, iso
    for e in iso.values():
        assert e["void"] is None and e["foreign_datagrams"] == 0, e
        assert all(e["counts"][g] > 20 for g in SEGS) and all(e["fps"][g] > 5 for g in SEGS), e


def test_alloc_given():
    """Slot 0's bus is the settings' first: groups 239.0.<group_third>.1 and
    .2, ports port_base and port_base + 1, as {bus_alloc} and in the
    environment (rule bus_alloc)."""
    s = settings(root("alloc"), slots=1)
    eng, rec = run([Group("m", [sim("one", dur=0.5)])], s)
    o = sim_out(eng, "m", "one")
    want = "a=239.0.%d.1:%d,b=239.0.%d.2:%d" % (GROUP_THIRD, PORT_BASE, GROUP_THIRD,
                                                 PORT_BASE + 1)
    assert o["alloc"] == want and o["token"], o
    assert results(rec)["m/one"]["verdict"] == "PASS", rec["results"]


def test_bound_refused():
    """A socket already bound to a port of the check's bus: the check is
    NOT-RUN "refused" before it starts (AGENTS.md "One simulator bench per
    set of ports")."""
    s = settings(root("bound"), slots=1)
    g, p = slot0()["a"]
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    blocker.bind(("", p))
    try:
        eng, rec = run([Group("m", [sim("one", dur=0.5)])], s)
    finally:
        blocker.close()
    r = results(rec)["m/one"]
    assert r["verdict"] == "NOT-RUN" and "already bound" in r["detail"], r
    assert not os.path.exists(os.path.join(eng.run_dir, "m", "one", "sim.json"))


def test_listen_refused():
    """A datagram heard on the check's segments before it starts (here a
    sender of its own, holding none of its ports): NOT-RUN "refused"."""
    s = settings(root("listen"), slots=1, listen_s=1.0)
    noise = foreign("foreign", slot0(), 8.0)
    try:
        time.sleep(0.5)
        eng, rec = run([Group("m", [sim("one", dur=0.5)])], s)
    finally:
        noise.kill()
        noise.wait()
    r = results(rec)["m/one"]
    assert r["verdict"] == "NOT-RUN" and "heard on its bus" in r["detail"], r


def test_same_allocation_void():
    """The monitor's can-fail (job B): two checks given the same bus (the
    slots' groups and ports made equal) run at once, with the pre-check off
    so the second is not refused: each sees the other's datagrams and
    sockets on its ports, and both come out VOID with the count."""
    VB.RULES["distinct_slots"] = False
    saved = E.RULES["bus_precheck"]
    E.RULES["bus_precheck"] = False
    try:
        s = settings(root("same"), slots=2)
        eng, rec = run([Group("m", [sim("x1", dur=3.0), sim("x2", dur=3.0)])], s)
    finally:
        VB.RULES["distinct_slots"] = True
        E.RULES["bus_precheck"] = saved
    r = results(rec)
    for c in ("m/x1", "m/x2"):
        assert r[c]["verdict"] == "VOID" and "isolation:" in r[c]["detail"], r[c]
        assert r[c].get("judged") == "PASS", r[c]
    iso = {e["check"]: e for e in events(eng, "isolation")}
    assert all(iso[c]["foreign_datagrams"] > 0 for c in ("m/x1", "m/x2")), iso


def test_foreign_sender_void():
    """A process without the check's token sends into its bus from a port of
    its own while the check runs, holding none of the check's ports: the
    check is VOID, the sender's port and count named."""
    s = settings(root("fsend"), slots=1)
    holder = {}

    def start_noise():
        time.sleep(1.2)                      # after the listen, while it runs
        holder["p"] = foreign("foreign", slot0(), 1.5)
    t = threading.Thread(target=start_noise)
    t.start()
    try:
        eng, rec = run([Group("m", [sim("one", mode="own", dur=4.0)])], s)
    finally:
        t.join()
        holder["p"].wait()
    r = results(rec)["m/one"]
    assert r["verdict"] == "VOID" and "not this check's" in r["detail"], r
    e = events(eng, "isolation")[0]
    assert e["foreign"] and not e["foreign_holders"], e


def test_foreign_holder_void():
    """A process without the check's token holds a socket on a port of the
    check's bus while it runs, sending nothing (the check sends from ports
    of its own, so no datagram is foreign): VOID, the holder named."""
    s = settings(root("fhold"), slots=1)
    holder = {}

    def start_hold():
        time.sleep(1.2)
        holder["p"] = foreign("hold", slot0(), 2.0)
    t = threading.Thread(target=start_hold)
    t.start()
    try:
        eng, rec = run([Group("m", [sim("one", mode="own", dur=4.0)])], s)
    finally:
        t.join()
        holder["p"].wait()
    r = results(rec)["m/one"]
    assert r["verdict"] == "VOID" and "held an allocated port" in r["detail"], r
    e = events(eng, "isolation")[0]
    assert e["foreign_holders"] and not e["foreign"], e


def test_unattributed_void():
    """A sender no process could be found for (gone before the monitor
    looked; here an ss that lists nothing): its datagrams count as foreign,
    and the monitor's report says VOID, naming them unattributed."""
    fake_ss = os.path.join(SCRATCH, "_fake_ss.py")
    with open(fake_ss, "w", encoding="utf-8", newline="\n") as f:
        f.write("pass\n")
    a = slot0()
    m = VB.Monitor(a, "no-such-token", poll_s=0.2, ss=(PY, "-B", fake_ss)).open()
    m.start()
    p = foreign("foreign", a, 0.6)
    p.wait()
    rep = m.stop()
    assert sum(rep["counts"].values()) > 5, rep
    assert rep["unattributed"] and rep["void"] and "unattributed" in rep["void"], rep


def test_parse_alloc():
    """format_alloc and parse_alloc are inverse; a bad string is refused;
    slots past the ports or groups the settings give are refused."""
    a = VB.allocation(settings(SCRATCH), 3, ["v", "c", "p"])
    assert a == {"v": ("239.0.%d.1" % (GROUP_THIRD + 3), PORT_BASE + 30),
                 "c": ("239.0.%d.2" % (GROUP_THIRD + 3), PORT_BASE + 31),
                 "p": ("239.0.%d.3" % (GROUP_THIRD + 3), PORT_BASE + 32)}, a
    assert VB.parse_alloc(VB.format_alloc(a)) == a
    for bad in ("nonsense", "v=1.2.3.4"):
        try:
            VB.parse_alloc(bad)
        except VB.BusError:
            continue
        raise AssertionError("parsed %r" % bad)
    try:
        VB.allocation(settings(SCRATCH), 60, ["v"])
    except VB.BusError:
        pass
    else:
        raise AssertionError("a slot past the groups was given")


def test_slots_need_alloc():
    """More than one slot without settings.bus_alloc is refused (each slot
    needs a bus of its own)."""
    s = settings(root("noalloc"), slots=2)
    del s["bus_alloc"]
    try:
        run([Group("m", [sim("one", dur=0.2)])], s)
    except E.SuiteError as e:
        assert "bus_alloc" in str(e), e
        return
    raise AssertionError("two slots without bus_alloc were taken")


TESTS = [test_slots_side_by_side, test_alloc_given, test_bound_refused, test_listen_refused,
         test_same_allocation_void, test_foreign_sender_void, test_foreign_holder_void,
         test_unattributed_void, test_parse_alloc, test_slots_need_alloc]


def _vb(name):
    return (lambda: VB.RULES.__setitem__(name, False),
            lambda: VB.RULES.__setitem__(name, True), name)


def _e(name):
    return (lambda: E.RULES.__setitem__(name, False),
            lambda: E.RULES.__setitem__(name, True), "engine " + name)


MUTANTS = [
    (_e("wallclock_slots"), test_slots_side_by_side),
    (_e("slot_concurrency"), test_slots_side_by_side),
    (_e("bus_alloc"), test_alloc_given),
    (_e("bus_precheck"), test_listen_refused),
    (_e("isolation_void"), test_same_allocation_void),
    (_vb("distinct_slots"), test_slots_side_by_side),
    (_vb("bound_check"), test_bound_refused),
    (_vb("listen_first"), test_listen_refused),
    (_vb("foreign_sender"), test_foreign_sender_void),
    (_vb("foreign_holder"), test_foreign_holder_void),
    (_vb("unattributed_foreign"), test_unattributed_void),
    (_vb("token_owner"), test_foreign_holder_void),
    (_vb("runner_excluded"), test_slots_side_by_side),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    if not sys.platform.startswith("linux"):
        print("REFUSED: these tests need Linux (ss, /proc)")
        return 2
    can_fail = "--can-fail" in argv
    bad = []
    for t in TESTS:
        o = outcome_of(t)
        print("%s  %-26s %s" % ("ok  " if o is None else "FAIL", t.__name__, o or ""),
              flush=True)
        if o:
            bad.append(t.__name__)
    if not can_fail:
        print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
        return 1 if bad else 0
    if bad:
        print("\nREFUSED: the control fails (%s); no mutant is counted" % ", ".join(bad))
        return 1
    caught = 0
    for (on, off, name), t in MUTANTS:
        on()
        try:
            o = outcome_of(t)
        finally:
            off()
        caught += o is not None
        print("%s  %-24s off: %-26s %s" % ("ok  " if o else "FAIL", name, t.__name__,
                                           ("fails (%s)" % o[:100]) if o else
                                           "STILL PASSES -- nothing checks it"), flush=True)
    print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
