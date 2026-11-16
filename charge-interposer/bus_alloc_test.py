#!/usr/bin/env python3
"""Harness test: run_scenario's use of the chain runner's bus
(VTRUX_BUS_ALLOC, shared/bench job B, 2026-11-16): bus_alloc() and
sim_bus_args(), and that the sims then really use the groups and ports they
are given. Linux only (IP_MULTICAST_ALL). No hardware.

    python3.13 -B charge-interposer/bus_alloc_test.py [--can-fail]

--can-fail runs every test first on run_scenario.py as it is (the control,
which must pass), then on an edited copy per mutant (an edit not found
exactly once counts as a failure); every mutant must make a test fail.

The end-to-end test starts vehicle_sim and charger_sim with the options
sim_bus_args gives for a test allocation (groups 239.0.201.x, ports from
46000 + 10 x (pid mod 400), never a bench's), and listens on each allocated
segment with a socket that hears only the group it joined
(IP_MULTICAST_ALL off): frames on every segment show each sim sent to its
allocated group and port. The listener joins each group before the sims
start, and the sims send with TTL 0 (bus.py), so nothing leaves the host.
Throwaway folders come from scratch_dir.py.
"""

import importlib.util
import os
import select
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scratch_dir                                   # noqa: E402

MODULE = os.path.join(HERE, "run_scenario.py")
PY = sys.executable
IP_MULTICAST_ALL = 49                                # linux/in.h
PORT_BASE = 46000 + 10 * (os.getpid() % 400)
ALLOC = {"vehicle": ("239.0.201.1", PORT_BASE), "charger": ("239.0.201.2", PORT_BASE + 1),
         "physics": ("239.0.201.3", PORT_BASE + 2)}
ALLOC_TEXT = ",".join("%s=%s:%d" % (s, g, p) for s, (g, p) in ALLOC.items())


def load(path):
    spec = importlib.util.spec_from_file_location("run_scenario_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Args:
    vehicle_port, charger_port, phys_port = 43213, 43214, 43215
    external_interposer = False
    vehicle_transport = charger_transport = None


def refused(fn, *a):
    try:
        fn(*a)
    except SystemExit:
        return True
    return False


# ---- the tests --------------------------------------------------------------
def test_parse(RS, work):
    """bus_alloc: unset or empty is None; the three segments read back; a
    missing segment, a malformed string or a group outside 239.0.x.x is
    refused."""
    assert RS.bus_alloc({}) is None and RS.bus_alloc({RS.BUS_ALLOC_ENV: ""}) is None
    assert RS.bus_alloc({RS.BUS_ALLOC_ENV: ALLOC_TEXT}) == ALLOC
    for bad in ("vehicle=239.0.201.1:46000,charger=239.0.201.2:46001",
                "vehicle=239.0.201.1",
                ALLOC_TEXT.replace("239.0.201.3", "239.1.0.9")):
        assert refused(RS.bus_alloc, {RS.BUS_ALLOC_ENV: bad}), bad


def test_sim_args(RS, work):
    """sim_bus_args: no allocation gives the command line's ports and no
    group (bus.py's); an allocation gives each sim its segments' ports and
    groups; with a board or a dongle an allocation is refused."""
    a = RS.sim_bus_args(Args(), None)
    assert a["vehicle"] == ["--port", "43213", "--phys-port", "43215"], a
    assert a["interposer"] == ["--vehicle-port", "43213", "--charger-port", "43214"], a
    assert not any("--channel" in x or "-channel" in " ".join(x) for x in a.values()), a
    b = RS.sim_bus_args(Args(), ALLOC)
    v, c, p = (str(ALLOC[s][1]) for s in ("vehicle", "charger", "physics"))
    assert b["vehicle"] == ["--port", v, "--phys-port", p, "--channel", "239.0.201.1",
                            "--phys-channel", "239.0.201.3"], b
    assert b["charger"] == ["--port", c, "--phys-port", p, "--channel", "239.0.201.2",
                            "--phys-channel", "239.0.201.3"], b
    assert b["interposer"] == ["--vehicle-port", v, "--charger-port", c,
                               "--vehicle-channel", "239.0.201.1",
                               "--charger-channel", "239.0.201.2"], b
    for k, val in (("external_interposer", True), ("vehicle_transport", "kvaser"),
                   ("charger_transport", "gs_usb")):
        x = Args()
        setattr(x, k, val)
        assert refused(RS.sim_bus_args, x, ALLOC), k


def test_suite_names(RS, work):
    """bench_suite's SIM_BUS, the segments its sims name as Check.bus, are
    run_scenario's BUS_SEGMENTS."""
    import bench_suite as BS
    assert tuple(BS.SIM_BUS) == tuple(RS.BUS_SEGMENTS), (BS.SIM_BUS, RS.BUS_SEGMENTS)


def test_main_reads_env(RS, work, path=None):
    """run_scenario's main reads VTRUX_BUS_ALLOC: a good one is named on the
    console before anything runs; a bad one stops it."""
    path = path or RS.__file__
    env = dict(os.environ, PYTHONPATH=HERE, **{RS.BUS_ALLOC_ENV: ALLOC_TEXT})
    r = subprocess.run([PY, "-B", path, "--list"], capture_output=True, text=True, env=env,
                       cwd=HERE, timeout=120)
    assert r.returncode == 0 and ("bus: %s=%s" % (RS.BUS_ALLOC_ENV, ALLOC_TEXT)) in r.stdout, \
        (r.returncode, r.stdout[-500:], r.stderr[-500:])
    env[RS.BUS_ALLOC_ENV] = "vehicle=239.0.201.1:1"
    r = subprocess.run([PY, "-B", path, "--list"], capture_output=True, text=True, env=env,
                       cwd=HERE, timeout=120)
    assert r.returncode != 0 and RS.BUS_ALLOC_ENV in (r.stdout + r.stderr), \
        (r.returncode, r.stdout[-500:])


def _listener(group, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.setsockopt(socket.IPPROTO_IP, IP_MULTICAST_ALL, 0)
    s.bind(("", port))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0")))
    s.setblocking(False)
    return s


def test_sims_use_it(RS, work):
    """End to end: vehicle_sim and charger_sim, started with sim_bus_args'
    options for the test allocation, send on each allocated segment (its
    group and port); a listener that hears only those groups counts frames
    on all three."""
    seg = RS.sim_bus_args(Args(), ALLOC)
    socks = {s: _listener(g, p) for s, (g, p) in ALLOC.items()}
    procs = []
    try:
        for name in ("charger", "vehicle"):
            log = open(os.path.join(work, name + ".log"), "w")
            procs.append(subprocess.Popen([PY, "-B", os.path.join(HERE, name + "_sim.py")]
                                          + seg[name] + ["--time-scale", "1"],
                                          stdout=log, stderr=subprocess.STDOUT, cwd=HERE))
        counts = {s: 0 for s in ALLOC}
        end = time.monotonic() + 6
        while time.monotonic() < end and not all(n >= 5 for n in counts.values()):
            r, _, _ = select.select(list(socks.values()), [], [], 0.2)
            for sk in r:
                name = next(k for k, v in socks.items() if v is sk)
                try:
                    while True:
                        sk.recvfrom(65535)
                        counts[name] += 1
                except BlockingIOError:
                    pass
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        for sk in socks.values():
            sk.close()
    logs = {n: open(os.path.join(work, n + ".log")).read()[-400:] for n in ("charger", "vehicle")}
    assert all(n >= 5 for n in counts.values()), (counts, logs)


TESTS = [test_parse, test_sim_args, test_suite_names, test_main_reads_env, test_sims_use_it]

MUTANTS = [
    ("groups not passed", "    if groups:\n        out[\"charger\"]",
     "    if False:\n        out[\"charger\"]"),
    ("the command line's ports kept",
     "        v, c, p = (alloc[s][1] for s in BUS_SEGMENTS)\n",
     "        v, c, p = args.vehicle_port, args.charger_port, args.phys_port\n"),
    ("physics group not passed to the vehicle",
     '        out["vehicle"] += ["--channel", groups["vehicle"], "--phys-channel", groups["physics"]]',
     '        out["vehicle"] += ["--channel", groups["vehicle"]]'),
    ("a missing segment taken", "    if sorted(out) != sorted(BUS_SEGMENTS):", "    if False:"),
    ("any group taken", "    if bad:\n        raise SystemExit(\"%s: group(s)",
     "    if False:\n        raise SystemExit(\"%s: group(s)"),
    ("an allocation with a board taken",
     "        if (getattr(args, \"external_interposer\", False)",
     "        if False and (getattr(args, \"external_interposer\", False)"),
    ("the environment not read", "    args.bus_alloc = bus_alloc()\n", "    args.bus_alloc = None\n"),
]


def run_all(path, base):
    RS = load(path)
    bad = []
    for t in TESTS:
        work = os.path.join(base, t.__name__)
        os.makedirs(work)
        try:
            if t is test_main_reads_env:
                t(RS, work, path=path)
            else:
                t(RS, work)
            print("ok    %s" % t.__name__, flush=True)
        except Exception as e:
            print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:240]), flush=True)
            bad.append(t.__name__)
    return bad


def main(argv):
    if not sys.platform.startswith("linux"):
        print("REFUSED: Linux only (IP_MULTICAST_ALL)")
        return 2
    with scratch_dir.run("bus-alloc-test") as base:
        if "--can-fail" not in argv:
            bad = run_all(MODULE, os.path.join(base, "tests"))
            print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
            return 1 if bad else 0
        print("-- control", flush=True)
        if run_all(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        src = open(MODULE, encoding="utf-8").read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (matched %d times)" % (n, name, src.count(old)))
                continue
            mdir = os.path.join(base, "m%d" % n)
            os.makedirs(mdir)
            mp = os.path.join(mdir, "run_scenario.py")
            with open(mp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_all(mp, os.path.join(mdir, "tests"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad else
                                      "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
