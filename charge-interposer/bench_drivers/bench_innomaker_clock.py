"""The Innomaker's own clock: device-timestamped echoes (step 4 prep).

The Innomaker (gs_usb / candleLight) is the charger segment's only adapter.
Its timestamps were never probed (plan, step 4 item 2), and every charger-
side figure so far was stamped on the HOST clock: bench_charger_witness's
EchoWitness records time.perf_counter() when an echo is read, which carries
USB batching, the artefact bench_gap_resolution's docstring warns about. So
its 0.307 / 5.852 ms burst figures are host-clock figures.

The reviewer ruled (2026-10-13): no cross-segment check, because the board's
bridge reshapes timing. Instead compare the Innomaker's device clock with
the host clock over a run of at least 60 s and record the rate error -- the
+/-5 % rate means of spec 12 entry 2 need only that -- and write the
device-timestamp echo mode. Three modes, all on device timestamps:

  clock      offer a steady low rate for >= 60 s; fit device time against
             host arrival time; report the rate error in ppm by two fits
             that must agree (least squares, and the minimum-latency point
             of each window, which USB batching can only delay, never
             advance).
  echo       offer bursts; report the burst shape (within-burst and
             between-burst gaps) and the mean rate from DEVICE stamps.
  rate-runs  the run-to-run rate collapse (bringup 2026-10-06: 30,000 ->
             3,656 -> 83 fps): K consecutive runs at a stated rate, with a
             stated action between them (an idle gap, a close-and-reopen
             of the device, or nothing), each run's delivered echoes,
             schedule lag and first send error recorded. The diagnosis
             order is the plan's: an idle gap first, then a device
             stop/reset at open, a physical replug only if both fail.

Requires the device to run in hardware-timestamp mode (GS_CAN_MODE_HW_
TIMESTAMP, which gs_usb's start() asks for only if the device advertises
it). If the device does not stamp, every mode REFUSES: a constant or zero
stamp would otherwise produce a perfect-looking 0 ppm.

The charger segment must have something ACKing (the X2 powered), or no
frame completes and no echo returns. Frames offered here are forwarded by
the board onto the vehicle segment.

    py -3.14 bench_innomaker_clock.py --self-test     # no hardware
    py -3.14 bench_innomaker_clock.py clock --seconds 120
    py -3.14 bench_innomaker_clock.py echo --rate 1000 --burst 8 --seconds 10
    py -3.14 bench_innomaker_clock.py rate-runs --rate 1000 --seconds 30 \\
        --runs 3 --between idle:60

Every run writes its output verbatim to --out (default: beside this script,
time-stamped). Tester, 2026-10-13.
"""

import argparse
import datetime
import os
import sys
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WRAP_US = 1 << 32                 # gs_usb's timestamp_us is 32 bits
MIN_CLOCK_S = 60.0                # the reviewer's floor for the clock run
CLOCK_ID = 0x101
FILLER_ID = 0x100


# --- analysis: pure functions, exercised by --self-test ------------------

def unwrap_us(raw):
    """32-bit microsecond stamps -> monotonic seconds, unwrapping each
    backwards step of more than half the range as one wrap (71.6 min)."""
    out, base, last = [], 0, None
    for r in raw:
        r = int(r) & (WRAP_US - 1)
        if last is not None and r < last and last - r > WRAP_US // 2:
            base += WRAP_US
        out.append((base + r) / 1e6)
        last = r
    return out


def _lsq(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return None, None
    b = sxy / sxx
    return b, my - b * mx


def fit_rate(host_s, dev_s, windows=10):
    """-> dict: the device clock's rate against the host's, as ppm (device
    seconds per host second, minus one, times 1e6), by least squares over
    every echo and by the minimum-latency echo of each of `windows` equal
    host-time windows. USB delivery can only make an echo arrive LATER than
    its device stamp, so each window's least-delayed echo is its truest
    pairing. Also the spread of (host - device) after the fit, which is
    the delivery latency's jitter."""
    if len(host_s) < 2 * windows or len(host_s) != len(dev_s):
        return dict(ok=False, why="%d pairs, need >= %d"
                    % (len(host_s), 2 * windows))
    span = host_s[-1] - host_s[0]
    b, a = _lsq(host_s, dev_s)
    if b is None:
        return dict(ok=False, why="host time does not advance")
    t0 = host_s[0]
    picks = []
    for w in range(windows):
        lo, hi = t0 + span * w / windows, t0 + span * (w + 1) / windows
        idx = [i for i, h in enumerate(host_s) if lo <= h <= hi]
        if idx:
            i = min(idx, key=lambda k: host_s[k] - dev_s[k])
            picks.append((host_s[i], dev_s[i]))
    b2, _a2 = _lsq([p[0] for p in picks], [p[1] for p in picks])
    resid = sorted(h - (d - a) / b for h, d in zip(host_s, dev_s))
    resid0 = resid[0]
    lat = [(r - resid0) * 1000.0 for r in resid]
    return dict(ok=b2 is not None, span_s=span, n=len(host_s),
                ppm_lsq=(b - 1.0) * 1e6,
                ppm_minlat=None if b2 is None else (b2 - 1.0) * 1e6,
                lat_p50_ms=lat[len(lat) // 2],
                lat_p99_ms=lat[min(len(lat) - 1, int(len(lat) * 0.99))],
                lat_max_ms=lat[-1])


def stamps_usable(raw):
    """-> (ok, why): refuse stamps that cannot be a running device clock."""
    if len(raw) < 2:
        return False, "fewer than 2 stamps"
    if all(r == 0 for r in raw):
        return False, "every stamp is 0 -- the device is not stamping"
    if len(set(raw)) == 1:
        return False, "every stamp is the same value"
    dev = unwrap_us(raw)
    back = sum(1 for x, y in zip(dev, dev[1:]) if y < x)
    if back:
        return False, "%d backwards step(s) after unwrapping" % back
    return True, "%d stamps, %.3f s of device time" % (len(raw),
                                                       dev[-1] - dev[0])


def burst_shape(dev_s, burst):
    """-> (within_ms, between_ms, mean_fps) from device stamps: a burst of
    `burst` frames shows burst-1 short gaps then one long one."""
    if len(dev_s) < burst * 3:
        return None, None, None
    gaps = sorted((y - x) * 1000.0 for x, y in zip(dev_s, dev_s[1:]))
    n_long = max(1, len(gaps) // burst)
    shorts, longs = gaps[:-n_long] or [0.0], gaps[-n_long:]
    span = dev_s[-1] - dev_s[0]
    return (sum(shorts) / len(shorts), sum(longs) / len(longs),
            (len(dev_s) - 1) / span if span else None)


def parse_between(s):
    """'idle:60' -> ('idle', 60.0); 'reopen' -> ('reopen', 0); 'none'."""
    if s in ("none", "reopen"):
        return s, 0.0
    if s.startswith("idle:"):
        return "idle", float(s.split(":", 1)[1])
    raise ValueError("--between is none, reopen or idle:<seconds>")


# --- the device -----------------------------------------------------------

class EchoLog(object):
    """Our own frames coming back: (host perf_counter at read, raw device
    timestamp_us) for each echo of `want_id`. Reads continuously, which
    also keeps gs_usb's TX from stalling (bringup, 2026-10-06)."""

    def __init__(self, bus, want_id):
        self.bus, self.want_id = bus, want_id
        self.pairs, self.other = [], 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except Exception:                              # noqa: BLE001
                return
            host = time.perf_counter()
            if m is None or getattr(m, "is_error_frame", False):
                continue
            if m.arbitration_id == self.want_id and not m.is_rx:
                self.pairs.append((host, int(round(m.timestamp * 1e6))))
            else:
                self.other += 1

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=3.0)


def open_device():
    import bench_charger_witness as bcw
    bus = bcw.open_gs_usb()
    dev = getattr(bus, "gs_usb", None)
    cap = getattr(dev, "device_capability", None)
    flags = getattr(dev, "device_flags", None)
    from gs_usb.constants import GS_CAN_MODE_HW_TIMESTAMP as HW
    info = dict(fclk_can=getattr(cap, "fclk_can", None),
                feature=getattr(cap, "feature", None), flags=flags,
                hw_timestamp=bool(flags is not None and flags & HW))
    return bus, info


def open_or_void():
    """open_device(), or (None, None) with a VOID line printed when the
    Innomaker does not open (2026-11-23, the reviewer's sweep ruling: a
    premise failure prints a VOID line rather than a bare non-zero exit;
    before, a traceback, read FAIL)."""
    try:
        return open_device()
    except Exception as e:                                 # noqa: BLE001
        print(premise_line("the Innomaker (role canchg) did not open (%s: %s)"
                           % (type(e).__name__, e)))
        return None, None


def premise_line(why):
    """A premise of the mode not met (the Innomaker not opening, not in
    hardware-timestamp mode, or its stamps not usable) as a VOID line, which
    script_judge reads (2026-11-23, the reviewer's sweep ruling)."""
    return "VOID: %s: not measured" % why


NOT_HW = ("the Innomaker is not in hardware-timestamp mode, so its stamps are "
          "not a device clock")


def offer(bus, rate, seconds, burst, want_id):
    """`want_id` offered on `bus` at `rate` in bursts of `burst` for
    `seconds`: bench_offer's offer (can-tools' pace, ended by time at
    seconds, since 2026-11-23: the reviewer's pace ruling; bench_pacer's
    before), so a run whose sends fall behind offers what its time allows,
    as before, never every frame of its schedule over a longer time:
    rate-runs' >= 95 % is held against rate x seconds frames in seconds.
    Its result prints as its figures (bench_pacer's printed as an object)."""
    import bench_offer
    import can
    msg = can.Message(arbitration_id=want_id, is_extended_id=False,
                      data=bytearray([0x5A] * 8))
    return bench_offer.offer(lambda: bus.send(msg, timeout=0.05), rate,
                             seconds, burst)


def describe(info):
    return ("fclk_can=%s feature=0x%s flags=0x%s hw_timestamp=%s"
            % (info["fclk_can"],
               "%x" % info["feature"] if info["feature"] is not None else "?",
               "%x" % info["flags"] if info["flags"] is not None else "?",
               info["hw_timestamp"]))


def run_clock(args):
    if args.seconds < MIN_CLOCK_S:
        print("REFUSED: --seconds %.0f is under the %.0f s the reviewer set "
              "for the clock comparison" % (args.seconds, MIN_CLOCK_S))
        return 2
    bus, info = open_or_void()
    if bus is None:
        return 1
    print("device: %s" % describe(info))
    if not info["hw_timestamp"]:
        print("REFUSED: the device is not in hardware-timestamp mode, so its "
              "stamps are not a device clock")
        print(premise_line(NOT_HW))
        bus.shutdown()
        return 1
    log = EchoLog(bus, CLOCK_ID).start()
    try:
        o = offer(bus, args.rate, args.seconds, 1, CLOCK_ID)
        time.sleep(1.5)
    finally:
        log.stop()
        bus.shutdown()
    print("offered %.0f fps for %.0f s: %s" % (args.rate, args.seconds, o))
    raw = [r for _h, r in log.pairs]
    ok, why = stamps_usable(raw)
    print("stamps: %s" % why)
    if not ok:
        print("REFUSED: %s" % why)
        print(premise_line("the Innomaker's stamps are not usable (%s)" % why))
        return 1
    host = [h for h, _r in log.pairs]
    f = fit_rate(host, unwrap_us(raw))
    if not f["ok"]:
        print("NO FIT: %s" % f.get("why"))
        return 1
    print("rate error over %.1f s, %d echoes:" % (f["span_s"], f["n"]))
    print("  least squares          %+9.1f ppm" % f["ppm_lsq"])
    print("  minimum-latency points %+9.1f ppm" % f["ppm_minlat"])
    print("  delivery latency above the least: p50 %.3f ms, p99 %.3f ms, "
          "max %.3f ms" % (f["lat_p50_ms"], f["lat_p99_ms"], f["lat_max_ms"]))
    agree = abs(f["ppm_lsq"] - f["ppm_minlat"])
    print("  the two fits differ by %.1f ppm%s" % (
        agree, "" if agree <= args.agree_ppm else
        " -- MORE THAN %.0f: report both, trust neither" % args.agree_ppm))
    print("  as a rate error: %.4f %% (spec 12 entry 2's means are +/-5 %%)"
          % (abs(f["ppm_minlat"]) / 1e4))
    print("  echoes of other ids seen: %d" % log.other)
    return 0 if agree <= args.agree_ppm else 1


def run_echo(args):
    bus, info = open_or_void()
    if bus is None:
        return 1
    print("device: %s" % describe(info))
    if not info["hw_timestamp"]:
        print("REFUSED: not in hardware-timestamp mode")
        print(premise_line(NOT_HW))
        bus.shutdown()
        return 1
    log = EchoLog(bus, FILLER_ID).start()
    try:
        o = offer(bus, args.rate, args.seconds, args.burst, FILLER_ID)
        time.sleep(1.5)
    finally:
        log.stop()
        bus.shutdown()
    print("offered %.0f fps in bursts of %d for %.0f s: %s"
          % (args.rate, args.burst, args.seconds, o))
    raw = [r for _h, r in log.pairs]
    ok, why = stamps_usable(raw)
    print("stamps: %s" % why)
    if not ok:
        print("REFUSED: %s" % why)
        print(premise_line("the Innomaker's stamps are not usable (%s)" % why))
        return 1
    w, b, fps = burst_shape(unwrap_us(raw), args.burst)
    print("DEVICE-CLOCK burst shape: within %.3f ms, between %.3f ms, mean "
          "%.1f fps (%d echoes of %d offered)"
          % (w, b, fps, len(raw), o.sent if hasattr(o, "sent") else -1))
    host = [h for h, _r in log.pairs]
    hw, hb, hfps = burst_shape(host, args.burst)
    print("host-clock shape of the same echoes, for comparison: within "
          "%.3f ms, between %.3f ms, mean %.1f fps" % (hw, hb, hfps))
    return 0


def run_rate_runs(args):
    kind, idle_s = parse_between(args.between)
    bus, info = open_or_void()
    if bus is None:
        return 1
    print("device: %s" % describe(info))
    rows = []
    try:
        for k in range(args.runs):
            if k:
                if kind == "idle":
                    print("  idle %.0f s" % idle_s)
                    time.sleep(idle_s)
                elif kind == "reopen":
                    bus.shutdown()
                    time.sleep(1.0)
                    bus, info = open_or_void()
                    if bus is None:
                        return 1
                    print("  reopened: %s" % describe(info))
            log = EchoLog(bus, FILLER_ID).start()
            o = offer(bus, args.rate, args.seconds, 1, FILLER_ID)
            time.sleep(1.5)
            log.stop()
            raw = [r for _h, r in log.pairs]
            dev = unwrap_us(raw) if raw else []
            fps = ((len(dev) - 1) / (dev[-1] - dev[0])
                   if len(dev) > 1 and dev[-1] > dev[0] else 0.0)
            rows.append((k + 1, len(raw), fps, o))
            print("run %d: %d echoes (device-clock %.1f fps) of %.0f fps "
                  "offered for %.0f s; %s" % (k + 1, len(raw), fps,
                                              args.rate, args.seconds, o))
    finally:
        if bus is not None:
            bus.shutdown()
    want = args.rate * args.seconds
    held = all(n >= 0.95 * want for _k, n, _f, _o in rows)
    print("between runs: %s. %s" % (args.between, "every run delivered >= 95 "
          "%% of the offer" if held else "the rate did NOT hold across runs"))
    return 0 if held else 1


# --- self-test: the analysis can fail -------------------------------------

def offer_and_premise_checks(check):
    """2026-11-23: offer() on bench_offer (the reviewer's pace ruling), on a
    stand-in bus, on time and running late; and the premise VOID lines (the
    reviewer's sweep ruling), each mode run with a stand-in open_device."""
    import contextlib
    import inspect
    import io
    import types
    print("offer, on bench_offer (ended by time)")

    class TxStandIn(object):
        def __init__(self, cost=0.0):
            self.cost, self.ids, self.shut = cost, [], False

        def send(self, msg, timeout=None):
            t = time.perf_counter() + self.cost
            while time.perf_counter() < t:
                pass
            self.ids.append(msg.arbitration_id)

        def shutdown(self):
            self.shut = True
    b = TxStandIn()
    o = offer(b, 1000, 0.2, 1, FILLER_ID)
    check(abs(o.sent - 200) <= 1 and o.errs == 0 and set(b.ids) == {FILLER_ID},
          "1,000 fps of 0x%X for 0.2 s: %d sent" % (FILLER_ID, o.sent))
    b = TxStandIn(0.002)
    o = offer(b, 1000, 0.2, 1, FILLER_ID)
    check(o.elapsed < 0.205 and o.sent < 110,
          "running late (2 ms a send): the offer ends at its 0.2 s (%.4f s, %d "
          "sent of 200), not after every frame, so rate-runs' count stays one of "
          "what its time offered" % (o.elapsed, o.sent))
    check("sent" in str(o) and "max lag" in str(o),
          "the offer prints as its figures: %s" % o)
    print("premise lines")
    g = globals()
    saved = g["open_device"]
    a = types.SimpleNamespace(seconds=120.0, rate=100.0, agree_ppm=20.0, burst=8,
                              runs=2, between="none")

    def run_mode(fn):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(a)
        return rc, buf.getvalue().splitlines()
    try:
        def boom():
            raise OSError("no device")
        g["open_device"] = boom
        for fn in (run_clock, run_echo, run_rate_runs):
            rc, lines = run_mode(fn)
            check(rc == 1 and lines[:1] == [
                "VOID: the Innomaker (role canchg) did not open (OSError: no device): "
                "not measured"],
                "%s: the Innomaker not opening -> a VOID line, exit 1 (%s, %s)"
                % (fn.__name__, rc, lines[:1]))
        bus = TxStandIn()
        g["open_device"] = lambda: (bus, dict(fclk_can=48000000, feature=0, flags=0,
                                              hw_timestamp=False))
        for fn in (run_clock, run_echo):
            rc, lines = run_mode(fn)
            check(rc == 1 and premise_line(NOT_HW) in lines and bus.shut,
                  "%s: not in hardware-timestamp mode -> a VOID line, exit 1 (%s)"
                  % (fn.__name__, [x for x in lines if x.startswith("VOID")]))
    finally:
        g["open_device"] = saved
    for fn in (run_clock, run_echo):
        check("        print(premise_line(\"the Innomaker's stamps are not usable (%s)\" "
              "% why))\n        return 1\n" in inspect.getsource(fn),
              "%s: stamps not usable -> a VOID line" % fn.__name__)
    src = inspect.getsource(run_rate_runs)
    check(src.count("open_or_void()") == 2 and "open_device()" not in src,
          "run_rate_runs: the first open and each reopen through open_or_void")


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    import random
    rnd = random.Random(7)
    print("unwrap")
    raw = [WRAP_US - 300, WRAP_US - 100, 50, 250]
    d = unwrap_us(raw)
    check(all(y > x for x, y in zip(d, d[1:])) and
          abs((d[2] - d[1]) - 150e-6) < 1e-9,
          "a wrap between two stamps is one step forward of 150 us")
    check(unwrap_us([100, 90]) == [100e-6, 90e-6],
          "a small backwards step is NOT a wrap (stamps_usable refuses it)")
    print("stamps_usable")
    check(not stamps_usable([0] * 50)[0], "all-zero stamps are refused")
    check(not stamps_usable([1234] * 50)[0], "constant stamps are refused")
    check(not stamps_usable([100, 200, 150, 300])[0],
          "a backwards step is refused")
    check(stamps_usable([100, 200, 300])[0], "rising stamps are usable")
    print("fit_rate, on a device clock 37 ppm fast with USB-batch delays")
    host, raw = [], []
    true_ppm = 37.0
    t = 0.0
    while t < 120.0:
        dev_t = 5.0 + t * (1 + true_ppm / 1e6)
        delay = rnd.choice([0.0002, 0.0005, 0.001, 0.004]) + rnd.random() * 1e-4
        host.append(t + delay)
        raw.append(int(round(dev_t * 1e6)) % WRAP_US)
        t += 0.01
    f = fit_rate(host, unwrap_us(raw))
    check(f["ok"] and abs(f["ppm_minlat"] - true_ppm) < 2.0,
          "minimum-latency fit recovers 37 ppm -> %+.2f" % f["ppm_minlat"])
    check(abs(f["ppm_lsq"] - true_ppm) < 5.0,
          "least squares lands near it -> %+.2f" % f["ppm_lsq"])
    check(f["lat_max_ms"] > 3.0, "latency spread shows the 4 ms batches -> "
          "max %.2f ms" % f["lat_max_ms"])
    host2 = [h + (0.03 if i > len(host) // 2 else 0.0)
             for i, h in enumerate(host)]
    f2 = fit_rate(host2, unwrap_us(raw))
    check(abs(f2["ppm_minlat"] - true_ppm) > 50.0,
          "a 30 ms host stall half way moves the fit (the fit can fail) -> "
          "%+.1f ppm" % f2["ppm_minlat"])
    check(not fit_rate(host[:5], unwrap_us(raw)[:5])["ok"],
          "too few pairs give no fit")
    print("burst_shape")
    dev, tt = [], 0.0
    for _b in range(200):
        for _i in range(8):
            dev.append(tt)
            tt += 0.000222
        tt += 0.008 - 8 * 0.000222       # one burst every 8 ms
    w, b, fps = burst_shape(dev, 8)
    check(abs(w - 0.222) < 0.001 and abs(b - (8 - 7 * 0.222)) < 0.01,
          "bursts of 8: within %.3f ms, between %.3f ms" % (w, b))
    check(abs(fps - 1000.0) < 5.0, "mean %.1f fps for 1,000 offered" % fps)
    flat = [i * 0.001 for i in range(1600)]
    w2, b2, _ = burst_shape(flat, 8)
    check(abs(w2 - b2) < 1e-6, "an evenly spaced train shows no burst shape "
          "(within == between)")
    print("parse_between")
    check(parse_between("idle:60") == ("idle", 60.0)
          and parse_between("reopen") == ("reopen", 0.0),
          "idle:60 and reopen parse")
    try:
        parse_between("replug")
        check(False, "an unknown action is refused")
    except ValueError:
        check(True, "an unknown action is refused")
    offer_and_premise_checks(check)
    print("\nbench_innomaker_clock self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py, since 2026-11-23): each edit must make
# --self-test fail
MUTANTS = [
    ("pace: the offer by schedule alone (until_s), not by time",
     "    return bench_offer.offer(lambda: bus.send(msg, timeout=0.05), rate,\n"
     "                             seconds, burst)",
     "    n = int(rate * seconds)\n"
     "    return bench_offer.Offered(bench_offer.rp().pace(\n"
     "        ((k // burst * burst / float(rate), k) for k in range(n)),\n"
     "        lambda k: bus.send(msg, timeout=0.05), until_s=seconds), n, 0, None, [],\n"
     "        rate, burst, seconds)"),
    ("pace: the offer for twice its time",
     "    return bench_offer.offer(lambda: bus.send(msg, timeout=0.05), rate,\n"
     "                             seconds, burst)",
     "    return bench_offer.offer(lambda: bus.send(msg, timeout=0.05), rate,\n"
     "                             2 * seconds, burst)"),
    ("premise: the Innomaker not opening, no VOID line",
     "        print(premise_line(\"the Innomaker (role canchg) did not open (%s: %s)\"\n"
     "                           % (type(e).__name__, e)))\n", "        pass\n"),
    ("premise: the clock run's hardware-timestamp refusal, no VOID line",
     "              \"stamps are not a device clock\")\n        print(premise_line(NOT_HW))\n",
     "              \"stamps are not a device clock\")\n"),
    ("premise: the echo run's hardware-timestamp refusal, no VOID line",
     "        print(\"REFUSED: not in hardware-timestamp mode\")\n"
     "        print(premise_line(NOT_HW))\n",
     "        print(\"REFUSED: not in hardware-timestamp mode\")\n"),
    ("premise: the clock run's stamps refusal, no VOID line",
     "        print(premise_line(\"the Innomaker's stamps are not usable (%s)\" % why))\n"
     "        return 1\n    host = [h for h, _r in log.pairs]\n    f = fit_rate(",
     "        return 1\n    host = [h for h, _r in log.pairs]\n    f = fit_rate("),
    ("premise: the echo run's stamps refusal, no VOID line",
     "        print(premise_line(\"the Innomaker's stamps are not usable (%s)\" % why))\n"
     "        return 1\n    w, b, fps = burst_shape(",
     "        return 1\n    w, b, fps = burst_shape("),
    ("premise: rate-runs opened with a traceback",
     "    bus, info = open_or_void()\n    if bus is None:\n        return 1\n"
     "    print(\"device: %s\" % describe(info))\n    rows = []",
     "    bus, info = open_device()\n    print(\"device: %s\" % describe(info))\n"
     "    rows = []"),
    ("premise: a reopen with a traceback",
     "                    bus, info = open_or_void()\n                    if bus is None:\n"
     "                        return 1\n",
     "                    bus, info = open_device()\n"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run --self-test against each MUTANTS edit")
    ap.add_argument("--out", default=None)
    sub = ap.add_subparsers(dest="mode")
    c = sub.add_parser("clock")
    c.add_argument("--seconds", type=float, default=120.0)
    c.add_argument("--rate", type=float, default=100.0)
    c.add_argument("--agree-ppm", type=float, default=20.0,
                   help="the two fits must agree within this")
    e = sub.add_parser("echo")
    e.add_argument("--seconds", type=float, default=10.0)
    e.add_argument("--rate", type=float, default=1000.0)
    e.add_argument("--burst", type=int, default=8)
    r = sub.add_parser("rate-runs")
    r.add_argument("--seconds", type=float, default=30.0)
    r.add_argument("--rate", type=float, default=1000.0)
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--between", default="none",
                   help="none, reopen, or idle:<seconds>")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if args.mode is None:
        ap.error("a mode (clock, echo, rate-runs) or --self-test")
    # with no --out, the default output goes where it went before the
    # move: SeaDrive's interposer-firmware/ folder, never this tree
    import paths
    here = None if args.out else paths.fixtures()
    out = args.out or os.path.join(here, "bench_innomaker_clock_%s_%s.txt" % (
        args.mode, datetime.datetime.now().strftime("%Y%m%dT%H%M%S")))
    import bench_charger_witness as bcw
    tee = bcw.Tee(out)
    sys.stdout = tee
    try:
        print("run at %s, mode %s, args %s"
              % (datetime.datetime.now().isoformat(), args.mode, vars(args)))
        rc = {"clock": run_clock, "echo": run_echo,
              "rate-runs": run_rate_runs}[args.mode](args)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("saved verbatim to %s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
