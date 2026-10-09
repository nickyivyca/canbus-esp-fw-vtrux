"""
Offline bench for the generator inhibitor.

Real command content, modelled bus timing.

The content is taken byte-for-byte from a capture, so the sequence of torque
commands, state bytes, RPM references and rolling-counter values is exactly
what the truck really sent, including the crank command that starts the engine.

The *timing* is modelled, and has to be. BUSMASTER timestamps these captures
host-side in batches -- up to 96 frames share one 100 us tick in
generator-on-then-off.log, which is physically impossible at 500 kbit/s -- so
the recorded inter-frame times are the logger's, not the bus's. Using them as
if they were real would produce a confident and meaningless answer. Instead
the VCM's slot is modelled as a 100 Hz clock with a stated jitter, and the
jitter is swept, so the output is not "it works" but "it works while the VCM's
transmit jitter stays below X". Deciding whether the real bus is inside that
bound needs a measurement on the truck, not another look at these logs.

  --timing ideal   perfect 100 Hz
  --timing jitter  100 Hz plus uniform +/- --jitter ms  (the useful one)
  --timing log     the raw capture timestamps, batching artefacts and all.
                   Not a fair test of the concept -- it is a test of the
                   failsafe, and the inhibitor should mostly refuse to fire.

Usage, from the reverse-it root (or with REVERSE_IT_ROOT set to it):
  python3.13 <this folder>/replay_inhibit.py --run
  python3.13 <this folder>/replay_inhibit.py --sweep

The log parser (canre) and the capture live in the reverse-it repo, not in
the firmware repo this file is published in, so their root is a setting:
$REVERSE_IT_ROOT, default the current directory.
"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
REVERSE_IT_ROOT = os.environ.get("REVERSE_IT_ROOT", ".")
sys.path.insert(0, HERE)
sys.path.insert(0, REVERSE_IT_ROOT)

import argparse
import random

try:
    from canre.parsers import parse_file
except ImportError as e:
    raise SystemExit("replay_inhibit: cannot import canre from %r (%s). Run "
                     "from the reverse-it root or set REVERSE_IT_ROOT to it."
                     % (os.path.abspath(REVERSE_IT_ROOT), e))
from sync import SlotSync
from inhibit import Inhibitor, FRAME_TIME
import models as M

TARGET = 0x051
DEFAULT_LOG = os.path.join(REVERSE_IT_ROOT, "projects", "vtrux", "logs",
                           "generator-on-then-off.log")
POLL_DT = 0.00005          # 50 us super-loop / timer tick


def load_payloads(path, limit=None):
    """The real 0x051 content sequence, in order, from the bus that carries it."""
    by_bus = {}
    for f in parse_file(path):
        if f.arbitration_id == TARGET:
            by_bus.setdefault(f.bus, []).append((f.timestamp, tuple(f.data)))
    if not by_bus:
        raise SystemExit("no 0x051 in %s" % path)
    bus = max(by_bus, key=lambda b: len(by_bus[b]))
    seq = by_bus[bus]
    if limit:
        seq = seq[:limit]
    return bus, seq


def window_around_crank(seq, before=2.0, after=4.0, period=0.010):
    """Trim to the interesting part: the run-up to the first crank command."""
    idx = next((i for i, (_, d) in enumerate(seq)
                if abs(M.torque_of(d)) > 50), None)
    if idx is None:
        return seq, None
    lo = max(0, idx - int(before / period))
    hi = min(len(seq), idx + int(after / period))
    return seq[lo:hi], idx - lo


def build_schedule(seq, timing, jitter_ms, period, seed):
    """Wire times for the VCM's transmissions."""
    rnd = random.Random(seed)
    if timing == "log":
        t0 = seq[0][0]
        return [t - t0 for t, _ in seq]
    out = []
    j = jitter_ms / 1000.0
    for k in range(len(seq)):
        t = k * period
        if timing == "jitter" and j > 0:
            t += rnd.uniform(-j, j)
        out.append(t)
    return out


def run(seq, *, strategy="bracket", payload_mode="mirror", timing="jitter",
        jitter_ms=0.2, period=0.010, rx_latency=0.00005, rx_jitter_ms=0.02,
        tx_latency=0.0002, seed=1, enabled=True, lead=0.0020,
        trail_delay=0.0008, verbose=False, probe_tick=None, probe_phase=0.0):
    """One bench run. Returns (scores, stats)."""
    rnd = random.Random(seed + 977)
    wire = build_schedule(seq, timing, jitter_ms, period, seed)

    sync = SlotSync(nominal_period=period)
    inh = Inhibitor(sync, strategy=strategy, payload_mode=payload_mode,
                    lead=lead, trail_delay=trail_delay, tx_latency=tx_latency)

    delivered = []      # (wire_completion_time, payload, mine)
    my_frames = []      # (start, end)
    collisions = []

    # Interleave the VCM's transmissions with our poll loop.
    t = 0.0
    k = 0
    t_end = wire[-1] + period
    while t < t_end:
        # VCM frames that complete in this tick
        while k < len(seq) and wire[k] <= t:
            payload = seq[k][1]
            delivered.append((wire[k] + FRAME_TIME, payload, False))
            rx = wire[k] + FRAME_TIME + rx_latency + \
                rnd.uniform(0, rx_jitter_ms / 1000.0)
            if enabled:
                inh.on_genuine(rx, payload)
            k += 1
        if enabled:
            for _id, payload in inh.poll(t):
                start = t + tx_latency
                end = start + FRAME_TIME
                my_frames.append((start, end))
                delivered.append((end, payload, True))
        t += POLL_DT

    # Same-ID collision check: our frame overlapping a VCM frame on the wire.
    for s, e in my_frames:
        for wt in wire:
            if wt < e and s < wt + FRAME_TIME:
                collisions.append((s, wt))
            elif wt > e:
                break

    delivered.sort(key=lambda x: x[0])
    t_start = delivered[0][0] if delivered else 0.0
    t_last = delivered[-1][0] if delivered else 0.0

    scores = []
    if probe_tick is not None:
        model_list = [M.LastWins(tick=probe_tick, phase=probe_phase * probe_tick)]
    else:
        model_list = M.all_models(tick=period)
    for m in model_list:
        for ts, payload, mine in delivered:
            m.deliver(ts, payload, mine)
        m.finish(t_last)
        sc = M.score(m, t_start, t_last)
        if isinstance(m, M.CounterValidating):
            sc["genuine_rejected"] = m.rejected_genuine
            sc["genuine_accepted"] = m.accepted_genuine
        scores.append(sc)

    stats = inh.status()
    stats["collisions"] = len(collisions)
    stats["my_frames"] = len(my_frames)
    stats["vcm_frames"] = len(seq)
    stats["window_s"] = t_last - t_start
    stats["cycle_slips"] = sync.cycle_slips
    stats["counter_repeats"] = sync.counter_repeats
    return scores, stats


def fmt_scores(scores, baseline=None):
    lines = ["  %-22s %8s %12s %8s %10s %8s" %
             ("receiver model", "zero %", "worst leak s", "peak",
              "mean |tq|", "vs none")]
    for i, s in enumerate(scores):
        extra = ""
        if "genuine_rejected" in s:
            extra = "   genuine %d rej / %d acc" % (
                s["genuine_rejected"], s["genuine_accepted"])
        rel = ""
        if baseline is not None and baseline[i]["mean_abs"] > 0:
            rel = "%7.2f%%" % (100 * s["mean_abs"] / baseline[i]["mean_abs"])
        lines.append("  %-22s %7.2f%% %12.4f %8d %10.1f %8s%s" %
                     (s["model"], 100 * s["zero_fraction"],
                      s["longest_nonzero_s"], s["peak_abs"], s["mean_abs"],
                      rel, extra))
    return "\n".join(lines)


def worst(scores):
    """The model that lets the most torque through -- the one that decides.

    Ranked on time-averaged commanded torque, not on the longest single gap:
    a stray 10 ms control tick obeying the real command does not turn an
    engine over, and ranking on the max would let one unlucky tick outweigh a
    strategy that suppresses 95 per cent of the command.
    """
    return max(scores, key=lambda s: s["mean_abs"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--strategy", default="bracket")
    ap.add_argument("--payload", default="mirror")
    ap.add_argument("--timing", default="jitter")
    ap.add_argument("--jitter", type=float, default=0.2, help="ms, +/-")
    ap.add_argument("--lead", type=float, default=2.0, help="ms")
    ap.add_argument("--trail", type=float, default=0.8, help="ms")
    a = ap.parse_args()

    bus, seq_full = load_payloads(a.log)
    seq, crank_i = window_around_crank(seq_full)
    print("log: %s" % a.log)
    print("0x051 on bus %d, %d frames total; window %d frames "
          "(crank command at index %s)\n" % (bus, len(seq_full), len(seq), crank_i))

    if a.run or not a.sweep:
        print("=== CONTROL: inhibitor disabled ===")
        base, st = run(seq, enabled=False, timing=a.timing, jitter_ms=a.jitter)
        print(fmt_scores(base))
        print("\n=== inhibitor: strategy=%s payload=%s timing=%s jitter=+/-%.2f ms ==="
              % (a.strategy, a.payload, a.timing, a.jitter))
        sc, st = run(seq, strategy=a.strategy, payload_mode=a.payload,
                     timing=a.timing, jitter_ms=a.jitter,
                     lead=a.lead / 1000.0, trail_delay=a.trail / 1000.0)
        print(fmt_scores(sc, baseline=base))
        print("\n  transmitted %d frames (%d lead, %d trail) against %d VCM frames"
              % (st["my_frames"], st["sent_lead"], st["sent_trail"], st["vcm_frames"]))
        print("  same-ID collisions: %d          <-- must be 0" % st["collisions"])
        print("  locked=%s period=%.4f ms phase_err_p99=%.4f ms guard=%s"
              % (st["locked"], st["period_ms"], st["phase_err_p99_ms"],
                 ("%.4f ms" % st["guard_ms"]) if st["guard_ms"] else "n/a"))
        print("  suppressed: unlocked=%d guard=%d late=%d stale=%d  (%s)"
              % (st["suppressed_unlocked"], st["suppressed_guard"],
                 st["suppressed_late"], st["suppressed_stale"], st["last_reason"]))
        print("  cycle slips=%d counter repeats=%d"
              % (st["cycle_slips"], st["counter_repeats"]))

    if a.sweep:
        print("\n=== sweep 1: VCM transmit jitter ===")
        print("cell = torque still getting through, as a percentage of the")
        print("no-inhibitor case, under the WORST of all receiver models")
        print("     / same-ID collisions / frames we actually transmitted.")
        print("A leak that returns to the no-inhibitor value WITH a transmit count")
        print("near zero is the guard band shutting us off, which is the intended")
        print("behaviour, not a failure of nerve.\n")
        jitters = [0.0, 0.05, 0.1, 0.2, 0.5, 0.75, 1.0, 2.0, 3.0]
        bmean = worst(run(seq, enabled=False, timing="jitter", jitter_ms=0.0)[0])["mean_abs"]
        print("  %-9s" % "strategy" +
              "".join("%14s" % ("+/-%.2fms" % j) for j in jitters))
        for strat in ("lead", "trail", "bracket"):
            cells = []
            for j in jitters:
                sc, st = run(seq, strategy=strat, timing="jitter", jitter_ms=j,
                             lead=a.lead / 1000.0, trail_delay=a.trail / 1000.0)
                w = worst(sc)
                cells.append("%5.1f%%/%d/%d" % (100 * w["mean_abs"] / bmean,
                                                st["collisions"], st["my_frames"]))
            print("  %-9s" % strat + "".join("%14s" % c for c in cells))
        print("  %-9s" % "none" + "%14s" % "100.0%/-/0")

        print("\n=== sweep 2: how fast must we answer a received frame? ===")
        print("The one window a trailing frame cannot cover is between the VCM's")
        print("frame completing and ours landing. Under a receiver that simply")
        print("obeys the newest frame, that window is where genuine torque gets")
        print("through, so it is bounded by our own interrupt-to-wire latency.\n")
        trails = [0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 3.0]
        print("  %-9s" % "jitter" + "".join("%12s" % ("%.1f ms" % x) for x in trails))
        for j in (0.0, 0.1, 0.2, 0.5):
            cells = []
            for tr in trails:
                sc, st = run(seq, strategy="bracket", timing="jitter", jitter_ms=j,
                             lead=a.lead / 1000.0, trail_delay=tr / 1000.0)
                w = worst(sc)
                cells.append("%5.1f%%/%d" % (100 * w["mean_abs"] / bmean,
                                             st["collisions"]))
            print("  +/-%-6.2f" % j + "".join("%12s" % c for c in cells))

        print("\n=== sweep 4: the one case that beats it ===")
        print("A receiver that reads a mailbox on its own control tick loses")
        print("nothing IF that tick falls anywhere except the short window")
        print("between the VCM's frame landing and our reply landing. Sweeping")
        print("the tick phase across a whole slot shows how wide that window is")
        print("and therefore how likely it is to matter.\n")
        for tick_ms in (2.0, 5.0, 10.0):
            bad = []
            vals = []
            for i in range(50):
                ph = i / 50.0
                sc, st = run(seq, strategy="bracket", timing="jitter",
                             jitter_ms=0.1, lead=a.lead / 1000.0,
                             trail_delay=a.trail / 1000.0,
                             probe_tick=tick_ms / 1000.0, probe_phase=ph)
                v = 100 * sc[0]["mean_abs"] / bmean
                vals.append(v)
                if v > 25.0:
                    bad.append(ph)
            print("  GENE control tick %4.1f ms: %d of 50 tick phases leak >25%%"
                  "  (worst %.1f%%, median %.1f%%)"
                  % (tick_ms, len(bad), max(vals), sorted(vals)[25]))
            if bad:
                print("      vulnerable phases: %s of a slot"
                      % ", ".join("%.2f" % x for x in bad))

        print("\n=== sweep 3: the capture's own timestamps (failsafe test) ===")
        sc, st = run(seq, strategy="bracket", timing="log",
                     lead=a.lead / 1000.0, trail_delay=a.trail / 1000.0)
        print("  Feeding the batched logger timestamps in as if they were bus")
        print("  timing gives an apparent jitter of several ms. Expected result:")
        print("  the inhibitor mostly refuses to transmit.")
        print("  frames transmitted: %d of a possible ~%d" % (st["my_frames"], 2 * len(seq)))
        print("  collisions: %d   locked=%s   phase_err_p99=%.3f ms   guard=%s"
              % (st["collisions"], st["locked"], st["phase_err_p99_ms"],
                 ("%.3f ms" % st["guard_ms"]) if st["guard_ms"] else "n/a"))
        print("  suppressed: unlocked=%d guard=%d late=%d  (%s)"
              % (st["suppressed_unlocked"], st["suppressed_guard"],
                 st["suppressed_late"], st["last_reason"]))


if __name__ == "__main__":
    main()
