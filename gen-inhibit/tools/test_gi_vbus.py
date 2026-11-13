#!/usr/bin/env python3
"""Proof by running that gi_vbus keeps the virtual bus on this host.

Gen-inhibit tester, 2026-10-08. Reading _confine_to_host is not evidence that
it works; this runs it.

  1. Refusals, on REAL python-can buses deliberately broken three ways:
     a forced non-zero TTL, a missing socket, a setsockopt that fails. Each
     must raise SystemExit naming firmware/esp-development.md. Plus a non-
     virtual transport, which gi_vbus must refuse rather than guess at.
  2. A clean open reads the TTL back as 0 from the socket itself.
  3. Each simulator bench (bench_vehicle.py, live_bench.py) is run REPS
     times, a few seconds each, as its own process, with an IDLE_S idle
     window on each side of each run. Each run's stderr must carry gi_vbus's
     "TTL read back 0" line for each bus the bench opens (BENCHES gives the
     count: one, and three), on the segments BENCHES lists and no other; a
     listener on each of those segments must count frames flowing in each
     run and none in any idle window; and the network counters must stay
     flat: see the flat judge below.
  4. Positive control for 3, so a flat counter means something: 300 datagrams
     sent deliberately with TTL 1 (to a group no bench uses), about 200 a
     second, must show up on those counters, above the background. Without
     it, counters that never move would pass 3. 300 small datagrams once is
     harmless; the 2026-09-01 incident was 66,450 per second for twenty
     minutes. (100 until 2026-11-12: with madhouse-debian's background at
     40-96 pps in the first evidence run, 100 passed by 60 against a need of
     50, too close to the background's own error.)

The counters (5, the judge):
- Linux: tx_packets in /sys/class/net/<nic>/statistics, for the NIC that
  `ip route get 239.0.2.1` names (AGENTS.md "Measure, do not assume"). It
  counts every packet the NIC sends, so the box's own traffic moves it: on
  madhouse-debian a few tens of pps (AGENTS.md), 22-112 pps in the
  2026-11-12 runs, in bursts.
- Windows: SentMulticastPackets per adapter (Get-NetAdapterStatistics),
  the worst adapter counting. The bench tooling is Linux only (user,
  2026-11-12); this branch is kept, not re-checked.

The flat judge (3, and --can-fail b) judges only what the bench could add
(gen-inhibit reviewer, 2026-11-12), so its margin doesn't depend on the
LAN's background. A leaking bench puts each frame on the wire, adding about
its frame count n to a run; a confined one adds about 0. Each run's added
count is its window's delta less the background the idle windows on both
sides of it give over the same length, so a background rising or falling
through the three windows cancels. Flat means the median added over the
runs is at most n/2 (n the median frames), midway: a burst of the LAN's own
in a run or two leaves the median alone, a leak is in every run. Judged
only with at least MIN_REPS runs, frames flowing, and the added values'
spread (median absolute deviation) at most n/4; a window not judged fails.
Until 2026-11-12 it was one run against a 5 s background just before it,
allowed 40 pps x the window, which a burst failed once in an evidence sweep
(excess 596 against 345) and passed three re-runs.

Several segments (gen-inhibit reviewer, 2026-11-12, after the judge above
merged): n is the smallest segment's frames, a listener on every segment the
bench sends on. A bench on several segments with only a small one leaking
adds only that segment's frames, which half of all its frames, or half of a
bigger segment's, would let through (the old 40 pps x the window caught it
only while the leak was above that allowance). Both benches send on one
segment today, so for them n is that segment's frames, as before.

Several buses on one segment are counted together, and that the judge cannot
split: live_bench.py's VCM and inhibitor each send about half its frames
(about 600 of 1214-1443 a run in the 2026-11-12 evidence), so one of them
leaking alone would add about 600 against a limit of about 650 and could
read flat. Judged by the smaller bus's frames instead (limit about 300,
spread up to about 150), the LAN's noise would leave it not judged too often
to use: live_bench.py's spread was 147 in that evidence. A single leaking bus
there rests on the per-bus TTL read-back instead: gi_vbus.open_bus refuses
any bus whose TTL does not read back 0, and 3 checks one such line for each
bus the bench opens.

The control (4, and --can-fail a) is judged as before: BASE_S seconds of
background just before give a rate, the window's excess is its delta less
that rate times its length, and it is seen at an excess of at least half of
what it sent, midway between kept on the host and sent; judged only if it
sent at least twice ALLOW_PPS times the window.

5 checks both judges with made-up readings: steady backgrounds low and
high, bursts, rising and falling ones are flat; a leak on top of any of
them is not; an unsteady background, too few runs or no frames is not
judged; three segments with only the small one leaking are not flat.
--mutants turns each choice of the flat judge (median, both sides, n/2, the
spread gate, the worst counter, the minimum runs, the smallest segment) the
wrong way and must see 5 fail every time.

  6. No .py under gen-inhibit/ (but firmware/, the upstream WiCAN tree) uses
     a raw UDP or multicast socket call (SOCK_DGRAM, sendto, IP_MULTICAST_*,
     IP_ADD_MEMBERSHIP) except gi_vbus.py and this test. TTL 0 confines a
     group only while a socket on this host has joined it (AGENTS.md "A
     virtual CAN bus is not automatically local", 2026-11-12); a python-can
     bus always joins its group, a send-only socket, replayer or probe need
     not (gen-inhibit reviewer, 2026-11-12: each must join, and be covered
     by the counter checks or shown to be none). The scan is shown able to
     fail on a planted send-only socket in a made-up tree.

--can-fail runs the network checks where they must fail, and passes only if
they do:
  a. the positive control sent through gi_vbus.open_bus (TTL 0, its group
     joined, as every bench sends) must not be seen on the NIC. Not a plain
     socket with TTL 0: on madhouse-debian (eno1, e1000e, 2026-11-12) 100
     TTL-0 datagrams to a group no socket on this host had joined raised
     tx_packets by about 100, the same as TTL 1, and stayed off it once any
     socket on the host had joined the group;
  b. a leaky stand-in bench, a python-can udp_multicast bus with the
     python-can default hop_limit=1 (the 2026-09-01 mistake), sending 600
     frames at 300 a second to the spare group in each of REPS runs, must
     fail "counters flat" under the same judge as the real benches;
  c. a stand-in bench on three segments (gen-inhibit reviewer, 2026-11-12):
     two through gi_vbus.open_bus, confined, THREE_N frames each, and the
     leaky bus of b on the spare group with a third as many, in each of REPS
     runs, a listener on each segment, must fail "counters flat".
  Each must fail by a judged window: one too short to show it fails too,
  but is reported NOT JUDGED and doesn't count. The real control is run
  first and must pass, so b and c are judged by counters that can move. b
  puts 3,000 small frames on the LAN, in five 2 s bursts (600 once until
  2026-11-12, when the flat judge took REPS runs); c 1,500, in five 3 s runs.

    python3.13 -B test_gi_vbus.py              # exit 0 only if every check passes
    python3.13 -B test_gi_vbus.py --can-fail   # exit 0 only if a, b and c fail as they must
    python3.13 -B test_gi_vbus.py --mutants    # exit 0 only if every flat-judge mutant is killed
"""
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import can            # noqa: E402
import gi_vbus        # noqa: E402

GUIDE = "firmware/esp-development.md"
SPARE_GROUP, SPARE_PORT = "239.0.2.250", 43299   # used by no bench
# Each bench: its script, its arguments, every segment it sends on (a listener on each),
# and how many buses it opens through gi_vbus (one "TTL read back 0" line each). Both
# send on one segment: bench_vehicle.py one bus, live_bench.py three (VCM, inhibitor,
# GENE) on the same group and port (read from their open_bus calls, 2026-11-12).
BENCHES = [
    ("bench_vehicle.py", ["--duration", "6", "--no-start"], [("239.0.2.4", 43217)], 1),
    ("live_bench.py", ["--duration", "6", "--time-scale", "1"], [("239.0.2.3", 43216)], 3),
]
MIN_FRAMES = 300            # the listener on each segment must see at least this many
ROUTE_PROBE = "239.0.2.1"   # AGENTS.md "Measure, do not assume"
BASE_S = 5.0                # the control's background reading, just before it
ALLOW_PPS = 40.0            # the control's: a control is judged only if it sent
#                             at least twice this x its window (madhouse-debian's
#                             background is 13-35 pps, AGENTS.md; 22-112 pps
#                             read in the 2026-11-12 runs)
CONTROL_N = 300             # ~1.5 s at 5 ms: seen at an excess of 150
REPS = 5                    # runs of each bench for the flat judge
IDLE_S = 6.0                # idle window between runs (one on each side of each)
SETTLE_S = 1.0              # after a bench exits, before its window closes
MIN_REPS = 3                # fewer runs than this are not judged
LEAKY_N, LEAKY_FPS = 600, 300   # --can-fail b, per run: 2 s; judged up to a spread of
#                                 150 (400 at 200/s gave a spread of 90 against 100 in
#                                 the first trial, 2026-11-12, background 23-125 pps)
# --can-fail c, per run (3 s): two confined segments of THREE_N frames each at
# THREE_FPS, and the spare group, leaking, with a third of that. The judge takes the
# smallest segment's frames, 300: limit 150, judged up to a spread of 75. Counted on
# the first segment only (limit 450), or on the three summed (limit 1050), the leak of
# 300 would pass.
THREE_SEGS = [("239.0.2.252", 43297), ("239.0.2.253", 43296)]   # used by no bench
THREE_N, THREE_FPS = 900, 300

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print("  [%s] %s%s" % ({True: "PASS", False: "FAIL", None: "SKIP"}[ok], name,
                           (" -- " + detail) if detail else ""), flush=True)


def ns(transport="virtual", channel="239.0.2.251", port=43298):
    return types.SimpleNamespace(transport=transport, channel=channel,
                                 port=port, bitrate=500000)


def refuses(fn):
    """(refused?, message). A refusal is SystemExit naming GUIDE."""
    try:
        fn()
    except SystemExit as e:
        msg = str(e.code)
        return GUIDE in msg or "only the virtual transport" in msg, msg.splitlines()[0]
    return False, "did not refuse"


class SetIsNoop:
    """A real socket whose setsockopt does nothing, so its TTL stays as opened."""
    def __init__(self, sock):
        self._s = sock

    def setsockopt(self, *a):
        pass

    def __getattr__(self, k):
        return getattr(self._s, k)


class SetFails(SetIsNoop):
    def setsockopt(self, *a):
        raise OSError("injected")


def patched_open(breaker):
    """Run gi_vbus.open_bus end to end with its python-can Bus broken."""
    made = []

    def bus_factory(**kw):
        kw["hop_limit"] = 1                     # the python-can default
        b = can.interface.Bus(**kw)
        made.append(b)
        breaker(b)
        return b
    fake_can = types.SimpleNamespace(interface=types.SimpleNamespace(Bus=bus_factory),
                                     __version__=can.__version__)
    real = gi_vbus.can
    gi_vbus.can = fake_can
    try:
        return refuses(lambda: gi_vbus.open_bus(ns()))
    finally:
        gi_vbus.can = real
        for b in made:
            try:
                b.shutdown()
            except Exception:
                pass
        while DROPPED:
            try:
                DROPPED.pop()._socket.close()
            except Exception:
                pass


def wrap(cls):
    def f(b):
        b._multicast._socket = cls(b._multicast._socket)
    return f


DROPPED = []


def drop_socket(b):
    DROPPED.append(b._multicast)      # kept so its socket can still be closed
    b._multicast = None


# ------------------------------------------------------------ 1 and 2

def part_refusals():
    print("1. refusals on real buses")
    ok, msg = patched_open(wrap(SetIsNoop))
    check("forced non-zero TTL (hop_limit=1, set ignored) refuses", ok, msg)
    ok, msg = patched_open(drop_socket)
    check("missing socket refuses (fails closed)", ok, msg)
    ok, msg = patched_open(wrap(SetFails))
    check("setsockopt failure refuses", ok, msg)
    ok, msg = refuses(lambda: gi_vbus.open_bus(ns(transport="pcan")))
    check("non-virtual transport refuses", ok, msg)

    print("2. clean open")
    b = gi_vbus.open_bus(ns())
    try:
        ttl = b._multicast._socket.getsockopt(socket.IPPROTO_IP,
                                              socket.IP_MULTICAST_TTL)
        check("TTL read back from the socket is 0", ttl == 0, "ttl=%d" % ttl)
    finally:
        b.shutdown()


# ------------------------------------------------------------ 3, 4 and 5

SYS_NET = "/sys/class/net"


def route_nic(group=ROUTE_PROBE):
    """(nic, "") for the NIC `ip route get <group>` names, or (None, why). Only
    the dev name is kept: the route's other fields name this network."""
    try:
        out = subprocess.run(["ip", "route", "get", group], capture_output=True,
                             text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "ip route get %s: %s" % (group, e)
    if "dev" not in out or out.index("dev") + 1 >= len(out):
        return None, "ip route get %s names no dev" % group
    return out[out.index("dev") + 1], ""


def counters():
    """{name: count}: on Linux the routed NIC's tx_packets, on Windows each
    adapter's SentMulticastPackets. Raises RuntimeError if they can't be read."""
    if os.name == "nt":
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapterStatistics | ForEach-Object { $_.Name + '|' + "
             "$_.SentMulticastPackets }"],
            capture_output=True, text=True, timeout=60).stdout
        d = {}
        for ln in out.splitlines():
            if "|" in ln:
                k, v = ln.rsplit("|", 1)
                if v.strip().isdigit():
                    d[k.strip()] = int(v)
        if not d:
            raise RuntimeError("Get-NetAdapterStatistics gave no counters")
        return d
    nic, why = route_nic()
    if nic is None:
        raise RuntimeError(why)
    path = os.path.join(SYS_NET, nic, "statistics", "tx_packets")
    try:
        with open(path) as f:
            return {nic: int(f.read())}
    except (OSError, ValueError) as e:
        raise RuntimeError("%s: %s" % (path, e))


def reading():
    return time.monotonic(), counters()


def background():
    """A reading just before a window, and the rate per counter over the BASE_S
    before it: (t, counts, {name: per second})."""
    t0, c0 = reading()
    time.sleep(BASE_S)
    t1, c1 = reading()
    return t1, c1, {k: (c1[k] - c0[k]) / (t1 - t0) for k in c1 if k in c0}


def excess(rate, start, end):
    """({name: delta less background}, window s) between two readings."""
    (t0, c0), (t1, c1) = start, end
    w = t1 - t0
    return {k: c1[k] - c0[k] - rate.get(k, 0.0) * w for k in c1 if k in c0}, w


def median(xs):
    return statistics.median(list(xs))


def run_added(run):
    """(added, ok) for one bench run: what the window counted beyond the
    background that the idle windows on both sides of it give over the same
    length, the worst counter's. A background that rises or falls steadily
    through the three windows cancels out. (None, False) if no counter is read
    in all three.

    run: {"w": window s, "delta": {counter: count in the window},
          "before": {counter: pps}, "after": {counter: pps},
          "frames": {segment: frames its listener counted}}"""
    adds = [d - run["w"] * (run["before"][k] + run["after"][k]) / 2.0
            for k, d in run["delta"].items() if k in run["before"] and k in run["after"]]
    return (max(adds), True) if adds else (None, False)


def run_frames(run):
    """The frames a run is judged by: its smallest segment's (gen-inhibit reviewer,
    2026-11-12). A bench that sends on several segments, one of them leaking, adds
    about that segment's frames, which may be far fewer than the others'; half the
    smallest segment's frames is the limit that still sees it. 0 with no segment."""
    return min(run["frames"].values()) if run["frames"] else 0


def judge_flat(runs):
    """(ok, judged, detail) over the runs of one bench.

    It judges only what the bench could add: a leaking segment puts each of
    its frames on the wire, adding about its frame count to a run, and a
    confined one adds about 0. Flat means the median added is at most n/2,
    midway, n the median over the runs of the smallest segment's frames
    (run_frames), so a leak on the smallest segment alone still reads not
    flat. The LAN's own level does not enter: the idle windows on both sides
    take it out of each run, and the median takes out a burst in a run or
    two. A leak is in every run, so it moves the median; a burst is not.

    Judged only with at least MIN_REPS runs, frames flowing on every segment,
    and the spread of the added values (their median absolute deviation) at
    most n/4, so a leak and no leak each sit at least twice the spread from
    the midway. A background too unsteady for that is not judged; not judged
    is never ok."""
    pairs = [run_added(r) for r in runs]
    if not runs or not all(ok for _, ok in pairs):
        return False, False, "no counters to compare"
    added = [a for a, _ in pairs]
    a = median(added)
    spread = median(abs(x - a) for x in added)
    n = median(run_frames(r) for r in runs)
    segs = sorted(set(s for r in runs for s in r["frames"]))
    per = ", ".join("%s %.0f" % (s, median(r["frames"].get(s, 0) for r in runs)) for s in segs)
    detail = ("median added %.0f over the idle windows on both sides, limit %.0f (half the "
              "median %.0f frames of the smallest segment; median frames per segment: %s), "
              "spread %.0f (judged up to %.0f), %d runs; added per run %s"
              % (a, n / 2.0, n, per, spread, n / 4.0, len(runs), [int(round(x)) for x in added]))
    if len(runs) < MIN_REPS:
        return False, False, detail + ": too few runs to take a median"
    if n <= 0:
        return False, False, detail + ": no frames, so nothing a leak would add"
    if spread > n / 4.0:
        return False, False, detail + ": the background moved too much to tell a leak"
    return a <= n / 2.0, True, detail


def judge_seen(ex, w, sent):
    """(ok, judged, detail): the best excess is at least half of what was sent,
    midway between kept on this host (about 0) and sent on the wire (about
    sent). Judged only if sent >= twice the allowance, so that half is beyond
    the background's allowance; not judged is never ok."""
    allow = ALLOW_PPS * w
    if not ex:
        return False, False, "no counters to compare"
    best = max(ex.values())
    detail = "best excess %.0f over the background, needs %.0f (%d sent, allowance %.0f, %.1f s)" % (
        best, sent / 2.0, sent, allow, w)
    if sent < 2 * allow:
        return False, False, detail + ": too few sent to stand out"
    return best >= sent / 2.0, True, detail


W_MADE_UP = 8.6     # s, a bench window as the real ones run


def made_up(during, before, after, extra=0, frames=1300, w=W_MADE_UP):
    """One made-up bench run on one counter: the background in pps during the
    window and in the idle windows before and after it, plus `extra` packets
    in the window (a leak, or a burst of the LAN's own). frames: one segment's
    count, or {segment: count} for several."""
    return {"w": w, "delta": {"nic": during * w + extra}, "before": {"nic": before},
            "after": {"nic": after}, "frames": frames if isinstance(frames, dict) else {"seg": frames}}


THREE = {"big1": 900, "big2": 900, "small": 300}     # --can-fail c's segments, per run


def part_judge():
    """5 on made-up readings: no network."""
    print("5. the judge, on made-up readings")
    steady = [made_up(30, 30, 30) for _ in range(REPS)]
    ok, judged, d = judge_flat(steady)
    check("a steady background of 30 pps is flat", ok and judged, d)
    ok, judged, d = judge_flat([made_up(110, 110, 110) for _ in range(REPS)])
    check("a steady background of 110 pps is flat too: the LAN's level doesn't enter",
          ok and judged, d)
    sweep = [made_up(30, 30, 30, extra=596 if i == 2 else 0) for i in range(REPS)]
    ok, judged, d = judge_flat(sweep)
    check("a burst of 596 in one run (the 2026-11-12 sweep's) is flat", ok and judged, d)
    check("the old judge (background 5 s before, 40 pps x the window) failed that run",
          596 > ALLOW_PPS * W_MADE_UP, "596 against %.0f" % (ALLOW_PPS * W_MADE_UP))
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=4000 if i == 1 else 0)
                                for i in range(REPS)])
    check("a burst of 4000 in one run (a download, a backup) is flat", ok and judged, d)
    ok, judged, d = judge_flat([made_up(120, 20, 220) for _ in range(REPS)])
    check("a background rising 20 -> 220 pps through each run is flat", ok and judged, d)
    ok, judged, d = judge_flat([made_up(120, 220, 20) for _ in range(REPS)])
    check("a background falling 220 -> 20 pps through each run is flat", ok and judged, d)

    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=1300) for _ in range(REPS)])
    check("every frame leaked, on a 30 pps background, is not flat", judged and not ok, d)
    ok, judged, d = judge_flat([made_up(110, 110, 110, extra=1300) for _ in range(REPS)])
    check("every frame leaked, on a 110 pps background, is not flat", judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=1300 + (4000 if i == 3 else 0))
                                for i in range(REPS)])
    check("a leak with a burst in one run is still not flat", judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=300, frames=300)
                                for _ in range(REPS)])
    check("every frame of a 300-frame bench leaked is not flat (inside the old allowance)",
          judged and not ok, d)
    two = [{"w": W_MADE_UP, "delta": {"a": 30 * W_MADE_UP, "b": 30 * W_MADE_UP + 1300},
            "before": {"a": 30, "b": 30}, "after": {"a": 30, "b": 30}, "frames": {"seg": 1300}}
           for _ in range(REPS)]
    ok, judged, d = judge_flat(two)
    check("a leak on one of two counters is not flat (the worst counter counts)",
          judged and not ok, d)
    # several segments (gen-inhibit reviewer, 2026-11-12): the smallest one's frames set n
    ok, judged, d = judge_flat([made_up(30, 30, 30, frames=dict(THREE), w=4.0) for _ in range(REPS)])
    check("three segments (900, 900, 300 frames), none leaking, are flat", ok and judged, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=300, frames=dict(THREE), w=4.0) for _ in range(REPS)])
    check("three segments, only the small one (300 frames) leaking, are not flat",
          judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=300, frames={"small": 300, "big1": 900, "big2": 900},
                                        w=4.0) for _ in range(REPS)])
    check("the same, the small segment listed first, is not flat either", judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, extra=x, frames=dict(THREE), w=4.0)
                                for x in (-100, 120, -60, 140, 20)])
    check("three segments with a spread beyond a quarter of the small one's frames is not judged",
          not judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, frames={"a": 1300, "b": 0}) for _ in range(REPS)])
    check("a segment with no frames is not judged, and not flat", not judged and not ok, d)

    noisy = [made_up(30, 30, 30, extra=x) for x in (-700, 500, -300, 900, 100)]
    ok, judged, d = judge_flat(noisy)
    check("a background too unsteady to tell a leak is not judged, and not flat",
          not judged and not ok, d)
    ok, judged, d = judge_flat(steady[:MIN_REPS - 1])
    check("fewer than %d runs is not judged, and not flat" % MIN_REPS,
          not judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, frames=0) for _ in range(REPS)])
    check("runs with no frames are not judged, and not flat", not judged and not ok, d)
    ok, judged, d = judge_flat([made_up(30, 30, 30, frames={}) for _ in range(REPS)])
    check("runs with no segment counted are not judged, and not flat", not judged and not ok, d)
    nocount = [dict(made_up(30, 30, 30), before={}) for _ in range(REPS)]
    ok, judged, d = judge_flat(nocount)
    check("runs with no counter read on both sides are not judged, and not flat",
          not judged and not ok, d)

    # the per-bus TTL read-back (3): one line per bus, TTL 0, only on the counted segments
    line = "gi_vbus: %s multicast TTL read back %d -- confined to this host\n"
    seg = "239.0.2.3:43216"
    three = "noise\n" + line % (seg, 0) * 3
    for label, errs, want in (
            ("three buses, three lines of TTL 0 on the segment counted, each run", [three] * REPS, (True, True)),
            ("one run with a line missing", [three] * 4 + ["noise\n" + line % (seg, 0) * 2], (False, True)),
            ("one run with a line too many", [three] * 4 + [three + line % (seg, 0)], (False, True)),
            ("a line reading TTL 1", [three] * 4 + [line % (seg, 0) * 2 + line % (seg, 1)], (False, True)),
            ("a bus on a segment not counted", [three] * 4 + [line % (seg, 0) * 2 + line % ("239.0.2.9:43299", 0)],
             (True, False)),
            ("no line at all", ["noise\n"] * REPS, (False, False))):
        c_ok, s_ok, td, sd = judge_ttl(errs, 3, {seg})
        check("TTL read-back: %s -> count %s, segments %s" % (label, "ok" if want[0] else "fails",
                                                              "ok" if want[1] else "fail"),
              (c_ok, s_ok) == want, "%s; %s" % (td, sd))

    bg = {"nic": 30.0}                       # pps, the control's background
    ex, _ = excess(bg, (0.0, {"nic": 0}), (0.8, {"nic": int(30 * 0.8) + 100}))
    ok, judged, d = judge_seen(ex, 0.8, 100)
    check("100 control datagrams on top of the background are seen", ok and judged, d)
    ex, _ = excess(bg, (0.0, {"nic": 0}), (0.8, {"nic": int(30 * 0.8)}))
    ok, judged, d = judge_seen(ex, 0.8, 100)
    check("the background alone is not taken for the control", judged and not ok, d)
    ok, judged, d = judge_seen({"nic": 1000.0}, 9.0, 100)
    check("too few sent to stand out is not judged, and not seen",
          not judged and not ok, d)


class Listener(threading.Thread):
    def __init__(self, group, port):
        super().__init__(daemon=True)
        self.bus = gi_vbus.open_bus(ns(channel=group, port=port))
        self.n = 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            if self.bus.recv(timeout=0.2) is not None:
                self.n += 1

    def close(self):
        self.stop.set()
        self.join(2)
        self.bus.shutdown()


def where():
    """Check the counters can be read and say which; False if they can't."""
    if os.name != "nt":
        nic, why = route_nic()
        check("`ip route get %s` names a NIC" % ROUTE_PROBE, nic is not None,
              nic or why)
        if nic is None:
            return False
        spare, why = route_nic(SPARE_GROUP)
        check("the control's group %s leaves by the same NIC" % SPARE_GROUP,
              spare == nic, spare or why)
        if spare != nic:
            return False
    try:
        c = counters()
    except RuntimeError as e:
        check("network counters readable", False, str(e))
        return False
    check("network counters readable", True, "%s (%s)" % (
        ", ".join(sorted(c)), "SentMulticastPackets" if os.name == "nt" else "tx_packets"))
    return True


def part_control(via_vbus=False, label=None):
    """Send CONTROL_N datagrams to the spare group, (ok, judged): ok if the
    counters saw them above the background. By default from a plain socket
    with TTL 1; via_vbus, through gi_vbus.open_bus (TTL 0, the group joined,
    as every bench sends), for --can-fail a."""
    label = label or "counters see %d deliberate TTL-1 datagrams" % CONTROL_N
    print("4. positive control: %s" % ("gi_vbus's own sends must not move the counters"
                                       if via_vbus else "TTL-1 multicast must move the counters"))
    t, c, rate = background()
    if via_vbus:
        b = gi_vbus.open_bus(ns(channel=SPARE_GROUP, port=SPARE_PORT))
        try:
            for i in range(CONTROL_N):
                b.send(can.Message(arbitration_id=0x7FF, data=[i & 0xFF] * 8,
                                   is_extended_id=False))
                time.sleep(0.005)
        finally:
            b.shutdown()
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        for _ in range(CONTROL_N):
            s.sendto(b"gi_vbus control", (SPARE_GROUP, SPARE_PORT))
            time.sleep(0.005)
        s.close()
    time.sleep(0.3)
    ex, w = excess(rate, (t, c), reading())
    ok, judged, d = judge_seen(ex, w, CONTROL_N)
    check(label, ok, d + "; background %s pps" % {k: round(v, 1) for k, v in rate.items()})
    return ok, judged


def leaky_sender():
    """--leaky-sender: the 2026-09-01 mistake on purpose, for --can-fail b. A
    python-can udp_multicast bus with python-can's default hop_limit=1 (not
    through gi_vbus), LEAKY_N frames at LEAKY_FPS to the spare group."""
    bus = can.interface.Bus(interface="udp_multicast", channel=SPARE_GROUP,
                            port=SPARE_PORT, hop_limit=1)
    try:
        for i in range(LEAKY_N):
            bus.send(can.Message(arbitration_id=0x7FF, data=[i & 0xFF] * 8,
                                 is_extended_id=False))
            time.sleep(1.0 / LEAKY_FPS)
    finally:
        bus.shutdown()
    return 0


def three_segment_sender():
    """--three-segment-sender: a stand-in bench on three segments, for --can-fail c.
    Two through gi_vbus.open_bus (TTL 0, confined), THREE_N frames each at THREE_FPS;
    the third the leaky python-can bus of b on the spare group, a third as many."""
    confined = [gi_vbus.open_bus(ns(channel=g, port=p)) for g, p in THREE_SEGS]
    leak = can.interface.Bus(interface="udp_multicast", channel=SPARE_GROUP,
                             port=SPARE_PORT, hop_limit=1)
    try:
        for i in range(THREE_N):
            m = can.Message(arbitration_id=0x7FE, data=[i & 0xFF] * 8, is_extended_id=False)
            for b in confined:
                b.send(m)
            if i % 3 == 0:
                leak.send(m)
            time.sleep(1.0 / THREE_FPS)
    finally:
        for b in confined + [leak]:
            b.shutdown()
    return 0


def idle_rate():
    """{counter: pps} over an IDLE_S window, timed by the readings themselves."""
    t0, c0 = reading()
    time.sleep(IDLE_S)
    t1, c1 = reading()
    return {k: (c1[k] - c0[k]) / (t1 - t0) for k in c1 if k in c0}


TTL_LINE = re.compile(r"^gi_vbus: (\S+):(\d+) multicast TTL read back (\d+) ")


def seg_name(group, port):
    return "%s:%d" % (group, port)


def judge_ttl(errs, buses, segs):
    """(count_ok, segs_ok, count detail, segs detail) over each run's stderr: every
    gi_vbus line reads TTL 0 and there are `buses` of them in each run; and every
    segment they name is one of `segs` (seg_name strings), and there is one."""
    lines = [[ln for ln in err.splitlines() if ln.startswith("gi_vbus:")] for err in errs]
    parsed = [[TTL_LINE.match(ln) for ln in t] for t in lines]
    count_ok = bool(errs) and all(len(t) == buses and all(m and m.group(3) == "0" for m in t) for t in parsed)
    opened = sorted(set(seg_name(m.group(1), int(m.group(2))) for t in parsed for m in t if m))
    segs_ok = bool(opened) and set(opened) <= set(segs)
    return (count_ok, segs_ok, "%s bus(es) per run: %s" % ([len(t) for t in lines], lines[0][:1] if lines else ""),
            "opened on %s" % opened)


def run_bench(label, argv, segments, control_ok, own_checks=True, reps=REPS, buses=None):
    """Run one simulator bench `reps` times, each as its own process, with an
    idle window on each side of each run (one between two runs serves both),
    and judge it (3). A listener on each segment it sends on counts that
    segment's frames in each run and in each idle window. With own_checks, each
    run's gi_vbus lines must read TTL 0, name only those segments, and number
    `buses`, one per bus the bench opens."""
    lis = {seg_name(g, p): Listener(g, p) for g, p in segments}
    for x in lis.values():
        x.start()

    def counts():
        return {s: x.n for s, x in lis.items()}
    runs, procs = [], []
    rates = []
    try:
        before = idle_rate()
        rates.append(before)
        first_idle = sum(counts().values())
        for _ in range(reps):
            n0 = counts()
            start = reading()
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
            killed = False
            try:
                _, err = p.communicate(timeout=90)
            except subprocess.TimeoutExpired:
                p.kill()
                _, err = p.communicate()
                killed = True
            time.sleep(SETTLE_S)
            end = reading()
            n1 = counts()
            after = idle_rate()
            rates.append(after)
            runs.append({"w": end[0] - start[0],
                         "delta": {k: end[1][k] - start[1][k] for k in end[1] if k in start[1]},
                         "before": before, "after": after,
                         "frames": {s: n1[s] - n0[s] for s in lis},
                         "idle_frames": sum(counts().values()) - sum(n1.values())})
            procs.append((p.returncode, err, killed))
            before = after
    finally:
        for x in lis.values():
            x.close()
    if own_checks:
        bad = [(rc, err.strip().splitlines()[-1] if rc and err.strip() else "")
               for rc, err, killed in procs if rc != 0 or killed]
        check(label + " exits 0 in each of %d runs" % reps, not bad,
              "rc %s" % bad if bad else "")
        count_ok, segs_ok, td, sd = judge_ttl([err for _, err, _ in procs], buses, set(lis))
        check(label + " reports TTL read back 0 for each of its %s bus(es) in each run" % buses, count_ok, td)
        check(label + " opens buses only on the segments counted (%s)" % ", ".join(sorted(lis)), segs_ok, sd)
        fr = [r["frames"] for r in runs]
        check(label + " frames flowed on each of its segments in each run",
              bool(fr) and all(len(f) == len(lis) and min(f.values()) >= MIN_FRAMES for f in fr),
              "frames per run %s" % [list(f.values()) if len(f) == 1 else f for f in fr])
        idle = [first_idle] + [r["idle_frames"] for r in runs]
        check(label + " no frames on its segments in any idle window", not any(idle),
              "frames per idle window %s (the first is before any run)" % idle)
    ok, judged, d = judge_flat(runs)
    lo = min(min(r.values()) for r in rates if r) if any(rates) else 0
    hi = max(max(r.values()) for r in rates if r) if any(rates) else 0
    check(label + " network counters flat", control_ok and ok,
          d + "; background %.0f-%.0f pps over %d idle windows%s" % (
              lo, hi, len(rates), "" if control_ok else " (control failed, so no proof)"))
    return control_ok and ok, judged


def part_benches(control_ok):
    print("3. each simulator bench, run for real")
    for script, extra, segments, buses in BENCHES:
        run_bench(script, [sys.executable, os.path.join(HERE, script)] + extra,
                  segments, control_ok, buses=buses)


# ------------------------------------------------------------ 6

GI = os.path.dirname(HERE)
RAW_UDP = ("SOCK_DGRAM", "sendto(", "IP_MULTICAST", "IP_ADD_MEMBERSHIP")
RAW_UDP_ALLOWED = {
    "tools/gi_vbus.py": "sets and reads back the TTL on python-can's own socket",
    "tools/test_gi_vbus.py": "the deliberate TTL-1 control, and this scan",
}


def raw_udp_users(root):
    """{path relative to root, '/' separated: [patterns]} for every .py under root
    (not firmware/, the upstream WiCAN tree) that uses a raw UDP or multicast
    socket call."""
    found = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not (d == root and x == "firmware")
                   and x not in ("__pycache__", ".git")]
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(d, f)
                try:
                    text = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                hits = [s for s in RAW_UDP if s in text]
                if hits:
                    found[os.path.relpath(p, root).replace(os.sep, "/")] = hits
    return found


def part_scan():
    """6: no harness tool sends to a group except through a python-can bus.
    TTL 0 confines a group only while a socket on the host has joined it
    (AGENTS.md "A virtual CAN bus is not automatically local", 2026-11-12), and
    a python-can bus always joins its group; a raw socket need not."""
    print("6. no raw UDP or multicast sockets outside gi_vbus and this test")
    found = raw_udp_users(GI)
    extra = {k: v for k, v in found.items() if k not in RAW_UDP_ALLOWED}
    check("no other .py under gen-inhibit/ uses %s" % "/".join(RAW_UDP), not extra,
          "found: %s" % extra if extra else "%d allowed: %s" % (
              len(found), ", ".join(sorted(found))))
    gone = [k for k in RAW_UDP_ALLOWED if k not in found]
    check("each allowed file is still found by the scan", not gone,
          "not found: %s" % gone if gone else "")
    # A made-up tree in this run's scratch folder, removed at exit, pass or fail.
    sys.path.insert(0, os.path.join(HERE, "bench"))
    import gi_paths
    t = gi_paths.scratch("gi_vbus", start=__file__)
    os.makedirs(os.path.join(t, "tools", "bench"))
    os.makedirs(os.path.join(t, "firmware"))
    with open(os.path.join(t, "tools", "bench", "probe.py"), "w") as f:
        f.write("import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.sendto(b'x', ('239.0.2.4', 43217))\n")
    with open(os.path.join(t, "firmware", "upstream.py"), "w") as f:
        f.write("s.sendto(b'x', addr)\n")
    got = raw_udp_users(t)
    check("scan can-fail: a planted send-only socket is found",
          "tools/bench/probe.py" in got, "found: %s" % got)
    check("scan: firmware/ (upstream WiCAN) is not scanned",
          "firmware/upstream.py" not in got, "found: %s" % got)


# ------------------------------------------------------------ --mutants

def _variant(runs, center="median", worst=max, bracket="both", limit="half",
             gate=True, min_reps=True, not_judged_ok=False, segment="smallest"):
    """judge_flat rewritten with each choice as a knob, for --mutants. With the
    defaults it must agree with judge_flat on every made-up case (the
    runner's control); each mutant turns one knob."""
    added = []
    for r in runs:
        adds = []
        for k, dlt in r["delta"].items():
            if k not in r["before"] or k not in r["after"]:
                continue
            bg = {"both": (r["before"][k] + r["after"][k]) / 2.0,
                  "before": r["before"][k], "after": r["after"][k]}[bracket]
            adds.append(dlt - r["w"] * bg)
        if not adds:
            return not_judged_ok, False, ""
        added.append(worst(adds))
    if not runs:
        return not_judged_ok, False, ""
    a = {"median": median, "mean": statistics.mean, "max": max}[center](added)
    spread = median(abs(x - a) for x in added)
    per_run = {"smallest": lambda f: min(f.values()) if f else 0,
               "first": lambda f: next(iter(f.values()), 0),
               "sum": lambda f: sum(f.values())}[segment]
    n = median(per_run(r["frames"]) for r in runs)
    lim = {"half": n / 2.0, "n": float(n),
           "allowance": ALLOW_PPS * median(r["w"] for r in runs)}[limit]
    if (min_reps and len(runs) < MIN_REPS) or n <= 0 or (gate and spread > n / 4.0):
        return not_judged_ok, False, ""
    return a <= lim, True, ""


FLAT_MUTANTS = [
    ("mean instead of the median of the runs", dict(center="mean")),
    ("the worst run instead of the median", dict(center="max")),
    ("background from the idle window before only", dict(bracket="before")),
    ("background from the idle window after only", dict(bracket="after")),
    ("limit is the old allowance, 40 pps x the window", dict(limit="allowance")),
    ("limit is every frame, not half", dict(limit="n")),
    ("no spread gate: an unsteady background is judged", dict(gate=False)),
    ("the best counter instead of the worst", dict(worst=min)),
    ("no minimum number of runs", dict(min_reps=False)),
    ("not judged passes", dict(not_judged_ok=True)),
    ("frames counted on the first segment only", dict(segment="first")),
    ("frames summed over the segments", dict(segment="sum")),
]


def run_flat_mutants():
    """Each mutant of the flat judge must make part 5 fail somewhere."""
    import contextlib
    import io
    global judge_flat
    real = judge_flat

    def part5(fn):
        global judge_flat
        del results[:]
        judge_flat = fn
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                part_judge()
        finally:
            judge_flat = real
        return [n for n, ok in results if ok is False]

    print("flat judge mutants, run against part 5's made-up readings")
    control = part5(real)
    print("control (the real judge): %s" % ("passes" if not control else "FAILS %s" % control))
    same = []

    def agree(runs):
        v, j = _variant(runs), real(runs)
        if v[:2] != j[:2]:
            same.append("%s vs %s" % (v[:2], j[:2]))
        return j
    part5(agree)
    print("control (the knob copy with no knob turned): %s" % (
        "agrees with the real judge on every case" if not same else "DIFFERS: %s" % same))
    killed = 0
    for name, kw in FLAT_MUTANTS:
        failed = part5(lambda runs, kw=kw: _variant(runs, **kw))
        killed += bool(failed)
        print("  %-8s %s%s" % ("killed" if failed else "SURVIVED", name,
                               " -- by: %s" % failed[0] if failed else ""))
    del results[:]
    print("\n%d of %d killed" % (killed, len(FLAT_MUTANTS)))
    return 0 if not control and not same and killed == len(FLAT_MUTANTS) else 1


def tally():
    bad = [n for n, ok in results if ok is False]
    print("\n=== %s === %d pass, %d fail" % ("FAIL" if bad else "PASS",
                                           sum(1 for _, ok in results if ok), len(bad)))
    return 1 if bad else 0


def can_fail():
    """a and b must fail, each by a judged window (a window too short to judge
    fails too, but proves nothing, so it doesn't count); the real control
    first must pass."""
    print("gi_vbus can-fail, python-can %s, %s" % (can.__version__, sys.executable))
    if not where():
        print("\nthe counters can't be read: nothing is judged")
        return 1
    control_ok, _ = part_control()
    if not control_ok:
        print("\nthe real control FAILS: nothing is judged")
        return 1
    out = []
    ok, judged = part_control(via_vbus=True,
                              label="a. control sent through gi_vbus seen on the NIC (must FAIL)")
    out.append(("a. control sent through gi_vbus seen", ok, judged))
    print("3. b. a leaky stand-in bench (hop_limit=1)")
    ok, judged = run_bench("b. leaky stand-in bench",
                           [sys.executable, "-B", os.path.abspath(__file__), "--leaky-sender"],
                           [(SPARE_GROUP, SPARE_PORT)], control_ok, own_checks=False)
    out.append(("b. leaky stand-in bench's counters flat", ok, judged))
    print("3. c. a stand-in bench on three segments, two confined, the small one leaking")
    ok, judged = run_bench("c. three-segment stand-in bench",
                           [sys.executable, "-B", os.path.abspath(__file__), "--three-segment-sender"],
                           THREE_SEGS + [(SPARE_GROUP, SPARE_PORT)], control_ok, own_checks=False)
    out.append(("c. three-segment stand-in bench's counters flat", ok, judged))
    print()
    caught = 0
    for name, ok, judged in out:
        if not ok and judged:
            caught += 1
            print("FAILED as it must:  %s" % name)
        elif not ok:
            print("NOT JUDGED:         %s (the window could not show it)" % name)
        else:
            print("DID NOT FAIL:       %s" % name)
    print("\n%d of %d failed as they must, by a judged window; the real control passes"
          % (caught, len(out)))
    return 0 if caught == len(out) else 1


def main():
    if sys.argv[1:] == ["--leaky-sender"]:
        return leaky_sender()
    if sys.argv[1:] == ["--three-segment-sender"]:
        return three_segment_sender()
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    if sys.argv[1:] == ["--mutants"]:
        return run_flat_mutants()
    if sys.argv[1:]:
        print("usage: test_gi_vbus.py [--can-fail | --mutants]", file=sys.stderr)
        return 2
    print("gi_vbus proof, python-can %s, %s" % (can.__version__, sys.executable))
    part_refusals()
    part_judge()
    part_scan()
    control_ok = where() and part_control()[0]
    part_benches(control_ok)
    return tally()


if __name__ == "__main__":
    sys.exit(main())
