"""
Live three-node bench for the generator inhibitor.

  vcm_node  --.                                     .-- gene_node
               >-- one virtual CAN segment (239.0.2.3:43216) --<
  inhibit_node                                      '

Three python-can buses on one segment, in one process, one thread each. One
process means there is nothing to orphan: no spawned children, no `pkill`, no
simulators surviving a failed cleanup and transmitting for the next twenty
minutes. The bus comes from gi_vbus.py (this folder), which forces
`hop_limit=0` and refuses to start if the socket's multicast TTL is not zero,
so nothing reaches the LAN. See the "Simulators, Virtual CAN Buses" section of
firmware/esp-development.md for why that guard exists.

WHAT THIS BENCH DOES AND DOES NOT SHOW
--------------------------------------
It shows the mechanism end to end on real sockets, real clocks and real
concurrency: the synchroniser locking onto another process's transmissions,
the counter prediction being right, the guard band sizing itself from measured
error, the frames landing where they were scheduled, and a receiver model
downstream showing what the GENE MCU would have obeyed.

It does not show that the timing budget closes at 100 Hz. CPython scheduling
jitter on a general-purpose kernel is milliseconds, which is the whole guard
band at a 10 ms period -- run it at --time-scale 1 and the inhibitor correctly
refuses to transmit at all. So the bench runs decelerated by default: every
interval in the core is stretched by --time-scale, which keeps the ratio of
scheduling jitter to slot period in the range a microcontroller would see at
100 Hz. The real timing budget is settled by measurement on the truck, not
here.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import argparse
import threading
import time

import can
import gi_vbus as busmod
from sync import SlotSync
from inhibit import Inhibitor
import models as M

# -- clock ------------------------------------------------------------------
# time.monotonic() is GetTickCount64() on Windows: 15.625 ms resolution, which
# is coarser than the 10 ms slot this tool has to hit. Every interval measured
# here then quantises to a tick, which shows up as a phase error of half a tick
# (~8 ms) and a reply latency that reads as exactly 0.000 ms. perf_counter() is
# QueryPerformanceCounter() on Windows and clock_gettime(MONOTONIC) on Linux --
# monotonic on both, sub-microsecond on both -- so it is the correct clock for
# every interval in this file. Measured on the Windows laptop, 2026-09-06.
_now = time.perf_counter


SEGMENT = "239.0.2.3"
PORT = 43216
TARGET = 0x051

# The command the VCM issues, as observed in generator-on-then-off.log:
# a long idle, a crank at +328, a ramp to +1815, then settled generation.
PROFILE = [
    (0.00, (0x08, 0x00, 0x80, 0xFF, 0x7F), 0),
    (3.00, (0x0B, 0x48, 0x81, 0x1F, 0x83), 328),
    (3.80, (0x0B, 0x00, 0x00, 0x1F, 0x83), 1815),
    (4.60, (0x0B, 0x00, 0x00, 0x63, 0x84), -1200),
    (7.00, (0x08, 0x00, 0x80, 0xFF, 0x7F), 0),
]


def profile_at(t):
    cur = PROFILE[0]
    for entry in PROFILE:
        if t >= entry[0]:
            cur = entry
    base, tq = cur[1], cur[2]
    raw = (tq + 32768) & 0xFFFF
    return (base[0], raw & 0xFF, (raw >> 8) & 0xFF, base[3], base[4])


class Bench:
    def __init__(self, args):
        self.args = args
        self.ts = args.time_scale
        self.period = 0.010 * self.ts
        self.duration = args.duration * self.ts
        self.stop = threading.Event()
        self.t0 = None
        self.vcm_sent = []       # (t, payload)
        self.inh_sent = []       # (t, payload)
        self.gene_rx = []        # (t, payload)
        self.inh_rx_own = 0
        self.errors = []

    def _open(self, name, own=False):
        a = argparse.Namespace(transport="virtual", channel=SEGMENT,
                               port=PORT, bitrate=500000)
        return busmod.open_bus(a, receive_own_messages=own)

    # -- nodes ------------------------------------------------------------

    def vcm_node(self):
        b = self._open("vcm")
        try:
            counter = 0
            k = 0
            while not self.stop.is_set():
                due = self.t0 + k * self.period
                delay = due - _now()
                if delay > 0:
                    time.sleep(delay)
                t = _now()
                if t - self.t0 > self.duration:
                    break
                payload = profile_at((t - self.t0) / self.ts) + (counter,)
                b.send(can.Message(arbitration_id=TARGET, is_extended_id=False,
                                   data=bytes(payload), dlc=6))
                self.vcm_sent.append((t, payload))
                counter = (counter + 1) & 0x0F
                k += 1
        except Exception as e:                       # noqa: BLE001
            self.errors.append("vcm: %r" % (e,))
        finally:
            self.stop.set()
            b.shutdown()

    def inhibit_node(self):
        b = self._open("inhibit")
        sync = SlotSync(nominal_period=self.period,
                        lock_threshold=0.0015 * self.ts)
        inh = Inhibitor(
            sync,
            strategy=self.args.strategy,
            payload_mode=self.args.payload,
            lead=0.0020 * self.ts,
            trail_delay=0.0008 * self.ts,
            min_guard=0.0005 * self.ts,
            max_guard=0.0035 * self.ts,
            tx_latency=0.0002 * self.ts,
            frame_time=0.0003 * self.ts,
        )
        self.inh = inh
        mine = set()
        try:
            while not self.stop.is_set():
                msg = b.recv(timeout=0.001)
                now = _now()
                if msg is not None and msg.arbitration_id == TARGET:
                    payload = tuple(msg.data)
                    if payload in mine:
                        # Our own frame, looped back. On a real controller this
                        # is suppressed in hardware; here it must never be fed
                        # to the synchroniser or it will lock onto itself.
                        self.inh_rx_own += 1
                        mine.discard(payload)
                    else:
                        inh.on_genuine(now, payload)
                for _id, payload in inh.poll(_now()):
                    mine.add(payload)
                    b.send(can.Message(arbitration_id=TARGET,
                                       is_extended_id=False,
                                       data=bytes(payload), dlc=6))
                    self.inh_sent.append((_now(), payload))
        except Exception as e:                       # noqa: BLE001
            self.errors.append("inhibit: %r" % (e,))
        finally:
            b.shutdown()

    def gene_node(self):
        b = self._open("gene")
        try:
            while not self.stop.is_set():
                msg = b.recv(timeout=0.05)
                if msg is not None and msg.arbitration_id == TARGET:
                    self.gene_rx.append((_now(), tuple(msg.data)))
        except Exception as e:                       # noqa: BLE001
            self.errors.append("gene: %r" % (e,))
        finally:
            b.shutdown()

    # -- run --------------------------------------------------------------

    def run(self):
        self.t0 = _now() + 0.3
        threads = [threading.Thread(target=f, daemon=True)
                   for f in (self.gene_node, self.inhibit_node, self.vcm_node)]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                time.sleep(0.05)
        except KeyboardInterrupt:
            self.stop.set()
        self.stop.set()
        for t in threads:
            t.join(timeout=2.0)


def report(bench):
    ts = bench.ts
    print("\n=== live bench result ===")
    if bench.errors:
        for e in bench.errors:
            print("  ERROR %s" % e)
    print("  time scale x%g  (VCM slot = %.1f ms, %.1f Hz)"
          % (ts, bench.period * 1e3, 1.0 / bench.period))
    print("  VCM transmitted   : %d" % len(bench.vcm_sent))
    print("  inhibitor sent    : %d  (lead %d, trail %d)"
          % (len(bench.inh_sent), bench.inh.sent_lead, bench.inh.sent_trail))
    print("  GENE received     : %d" % len(bench.gene_rx))
    print("  own frames looped back to the inhibitor and discarded: %d"
          % bench.inh_rx_own)

    st = bench.inh.status()
    print("  locked=%s  period=%.3f ms  phase_err_p99=%.3f ms  guard=%s"
          % (st["locked"], st["period_ms"], st["phase_err_p99_ms"],
             ("%.3f ms" % st["guard_ms"]) if st["guard_ms"] else "n/a"))
    print("  suppressed: unlocked=%d guard=%d late=%d stale=%d"
          % (st["suppressed_unlocked"], st["suppressed_guard"],
             st["suppressed_late"], st["suppressed_stale"]))
    print("  sync: cycle slips=%d counter repeats=%d"
          % (bench.inh.sync.cycle_slips, bench.inh.sync.counter_repeats))

    # measured VCM slot jitter, scaled back to a 100 Hz equivalent
    v = [t for t, _ in bench.vcm_sent]
    if len(v) > 10:
        d = [(v[i] - v[i - 1]) for i in range(1, len(v))]
        d.sort()
        span = (d[-1] - d[0]) * 1e3
        print("  measured VCM slot spread on this host: %.3f ms "
              "(= %.3f ms at 100 Hz)" % (span, span / ts))

    # what would each receiver model have obeyed?
    inh_set = set()
    for _t, p in bench.inh_sent:
        inh_set.add(p)
    delivered = [(t, p, p in inh_set) for t, p in bench.gene_rx]
    if not delivered:
        print("  no frames reached the GENE node")
        return
    t_start, t_end = delivered[0][0], delivered[-1][0]
    print("\n  %-22s %8s %12s %10s" %
          ("receiver model", "zero %", "worst leak s", "mean |tq|"))
    for m in M.all_models(tick=bench.period):
        for t, p, mine in delivered:
            m.deliver(t, p, mine)
        m.finish(t_end)
        sc = M.score(m, t_start, t_end)
        extra = ""
        if isinstance(m, M.CounterValidating):
            extra = "   genuine %d rej / %d acc" % (m.rejected_genuine,
                                                    m.accepted_genuine)
        print("  %-22s %7.2f%% %12.4f %10.1f%s"
              % (sc["model"], 100 * sc["zero_fraction"],
                 sc["longest_nonzero_s"] / ts, sc["mean_abs"], extra))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="bracket")
    ap.add_argument("--payload", default="mirror")
    ap.add_argument("--duration", type=float, default=9.0,
                    help="seconds of SIMULATED vehicle time")
    ap.add_argument("--time-scale", type=float, default=10.0,
                    help="stretch every interval by this factor; 1 = real 100 Hz")
    a = ap.parse_args()

    print("segment %s:%d  strategy=%s payload=%s time-scale=x%g"
          % (SEGMENT, PORT, a.strategy, a.payload, a.time_scale))
    b = Bench(a)
    b.run()
    report(b)


if __name__ == "__main__":
    main()
