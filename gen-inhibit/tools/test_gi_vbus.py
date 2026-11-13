#!/usr/bin/env python3
"""Proof by running that gi_vbus keeps the virtual bus on this host.

Gen-inhibit tester, 2026-10-08. Reading _confine_to_host is not evidence that
it works; this runs it.

  1. Refusals, on REAL python-can buses deliberately broken three ways:
     a forced non-zero TTL, a missing socket, a setsockopt that fails. Each
     must raise SystemExit naming firmware/esp-development.md. Plus a non-
     virtual transport, which gi_vbus must refuse rather than guess at.
  2. A clean open reads the TTL back as 0 from the socket itself.
  3. Each simulator bench (bench_vehicle.py, live_bench.py) is run for a few
     seconds as its own process. Its stderr must carry gi_vbus's "TTL read
     back 0" line, a listener on its segment must count frames flowing, and
     the network counters must stay flat meanwhile: no more than the
     background the same counters showed just before.
  4. Positive control for 3, so a flat counter means something: 100 datagrams
     sent deliberately with TTL 1 (to a group no bench uses) must show up on
     those counters, above the background. Without it, counters that never
     move would pass 3. 100 small datagrams once is harmless; the 2026-09-01
     incident was 66,450 per second for twenty minutes.

The counters (5, the judge):
- Linux: tx_packets in /sys/class/net/<nic>/statistics, for the NIC that
  `ip route get 239.0.2.1` names (AGENTS.md "Measure, do not assume"). It
  counts every packet the NIC sends, so the box's own traffic moves it: on
  madhouse-debian a few tens of pps (AGENTS.md). Each window is judged
  against a reading taken just before it, never against zero: BASE_S
  seconds of background give a rate, and the window's excess is its delta
  less that rate times its length. Flat means an excess of at most
  ALLOW_PPS times the length; the control is seen at an excess of at least
  half of what it sent, midway between kept on the host and sent. A window
  is judged only if what it looks for is at least twice that allowance, so
  a leak could not hide in it; a window not judged fails.
- Windows: SentMulticastPackets per adapter (Get-NetAdapterStatistics),
  judged the same way, the worst adapter counting. The bench tooling is
  Linux only (user, 2026-11-12); this branch is kept, not re-checked.

5 is also checked with made-up readings: background alone is flat, a leak
on top of it is not, and a judge that read from zero would have failed the
background.

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
     frames at 200 a second to the spare group, must fail "counters flat".
  Each must fail by a judged window: one too short to show it fails too,
  but is reported NOT JUDGED and doesn't count. The real control is run
  first and must pass, so b is judged by counters that can move. b puts
  600 small frames on the LAN once.

    python3.13 -B test_gi_vbus.py              # exit 0 only if every check passes
    python3.13 -B test_gi_vbus.py --can-fail   # exit 0 only if a and b fail as they must
"""
import os
import socket
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
RIGS = [
    ("bench_vehicle.py", ["--duration", "6", "--no-start"], "239.0.2.4", 43217),
    ("live_bench.py", ["--duration", "6", "--time-scale", "1"], "239.0.2.3", 43216),
]
MIN_FRAMES = 300            # the listener must see at least this many
ROUTE_PROBE = "239.0.2.1"   # AGENTS.md "Measure, do not assume"
BASE_S = 5.0                # background reading just before each window
ALLOW_PPS = 40.0            # excess over that background still called flat:
#                             madhouse-debian's background is 13-35 pps
#                             (AGENTS.md; 15-35 pps read 2026-11-12)
CONTROL_N = 100
LEAKY_N, LEAKY_FPS = 600, 200   # --can-fail b: ~3 s, so 600 > twice the allowance

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


def judge_flat(ex, w, frames):
    """(ok, judged, detail): the worst excess is within ALLOW_PPS x w. Judged
    only if the frames sent would stand out, frames >= twice the allowance;
    not judged is never ok."""
    allow = ALLOW_PPS * w
    if not ex:
        return False, False, "no counters to compare"
    worst = max(ex.values())
    detail = "worst excess %.0f over the background, allowance %.0f, %d frames, %.1f s" % (
        worst, allow, frames, w)
    if frames < 2 * allow:
        return False, False, detail + ": a leak of these frames would not stand out"
    return worst <= allow, True, detail


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


def part_judge():
    """5 on made-up readings: no network."""
    print("5. the judge, on made-up readings")
    bg = {"nic": 30.0}                       # pps, the background just before
    w = 9.0
    quiet = ({"nic": 1000}, {"nic": 1000 + int(30 * w)})
    ex, _ = excess(bg, (0.0, quiet[0]), (w, quiet[1]))
    ok, judged, d = judge_flat(ex, w, 1200)
    check("background alone over the window is flat", ok and judged, d)
    raw = quiet[1]["nic"] - quiet[0]["nic"]
    check("a judge from zero (the old 40-packet allowance) would have failed it",
          raw > 40, "delta %d" % raw)
    ex, _ = excess(bg, (0.0, {"nic": 1000}), (w, {"nic": 1000 + int(30 * w) + 1200}))
    ok, judged, d = judge_flat(ex, w, 1200)
    check("1200 leaked frames on top of the background are not flat",
          judged and not ok, d)
    ok, judged, d = judge_flat({"nic": 0.0}, w, 100)
    check("too few frames to show a leak is not judged, and not flat",
          not judged and not ok, d)
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


def run_bench(label, argv, group, port, control_ok, own_checks=True):
    """Run one simulator bench as its own process and judge it (3)."""
    lis = Listener(group, port)
    lis.start()
    t, c, rate = background()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        check(label + " finished", False, "killed after 90 s")
    time.sleep(1.0)
    n_during = lis.n
    time.sleep(1.0)
    n_after = lis.n - n_during
    lis.close()
    ex, w = excess(rate, (t, c), reading())
    if own_checks:
        ttl_lines = [ln for ln in err.splitlines() if ln.startswith("gi_vbus:")]
        check(label + " exits 0", p.returncode == 0, "rc=%s %s" % (
            p.returncode, err.strip().splitlines()[-1] if p.returncode and err.strip() else ""))
        check(label + " reports TTL read back 0",
              bool(ttl_lines) and all("read back 0 " in ln for ln in ttl_lines),
              "%d bus(es): %s" % (len(ttl_lines), ttl_lines[:1]))
        check(label + " frames flowed on %s:%d" % (group, port),
              n_during >= MIN_FRAMES, "%d frames seen" % n_during)
        check(label + " traffic stopped when it exited", n_after == 0,
              "%d frames in the 1 s after" % n_after)
    ok, judged, d = judge_flat(ex, w, n_during)
    check(label + " network counters flat", control_ok and ok,
          d + "; background %s pps%s" % ({k: round(v, 1) for k, v in rate.items()},
                                         "" if control_ok else " (control failed, so no proof)"))
    return control_ok and ok, judged


def part_rigs(control_ok):
    print("3. each simulator bench, run for real")
    for script, extra, group, port in RIGS:
        run_bench(script, [sys.executable, os.path.join(HERE, script)] + extra,
                  group, port, control_ok)


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
                           SPARE_GROUP, SPARE_PORT, control_ok, own_checks=False)
    out.append(("b. leaky stand-in bench's counters flat", ok, judged))
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
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    if sys.argv[1:]:
        print("usage: test_gi_vbus.py [--can-fail]", file=sys.stderr)
        return 2
    print("gi_vbus proof, python-can %s, %s" % (can.__version__, sys.executable))
    part_refusals()
    part_judge()
    control_ok = where() and part_control()[0]
    part_rigs(control_ok)
    return tally()


if __name__ == "__main__":
    sys.exit(main())
