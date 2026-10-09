"""Generate a deterministic trace + golden output from machine.py.

The firmware's machine.cpp is a port of the parent folder's machine.py. This
script freezes what the Python core does over a real capture, so the C++ port
can be checked against it byte for byte.

Two files come out:

    <name>.trace   the input, one event per line, INCLUDING explicit tick lines
    <name>.golden  what machine.py emitted, in a canonical format

Ticks are written into the trace rather than being re-derived by each runner.
Without that, "tick at most once per simulated 10 ms" has to be reimplemented
identically on both sides, and a divergence there would look like a logic bug in
the port. Making the input fully explicit means any diff is a real difference in
the state machine.

Usage:
    py -3.12 make_golden.py <capture.log> [--name NAME] [--start S] [--end S]

Run from the repo root (the Bash tool's working directory).
"""

import argparse
import os
import sys

sys.path.insert(0, ".")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTERPOSER = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _INTERPOSER)

import machine as M          # noqa: E402
import replay as R           # noqa: E402


TICK_INTERVAL_MS = 10        # must match replay.replay_offline


def build(path, out_base, start_s=0.0, end_s=None, bus_filter=None):
    if bus_filter is None:
        bus_filter = R.detect_pt_bus(path)
        print("detected powertrain bus: %s" % bus_filter)

    known = R.VEHICLE_IDS | R.CHARGER_IDS
    core = M.InterposerCore()

    trace_lines = []
    golden_lines = []
    last_state = core.state
    last_tick_ms = -1
    n_in = 0

    def record(out):
        for side, oid, oext, odata in out:
            golden_lines.append("E %d %d %X %d %s"
                                % (t_ms, side, oid, 1 if oext else 0,
                                   odata.hex()))

    for t, arb, ext, data in R.iter_pt(path, bus_filter, known):
        if t < start_s:
            continue
        if end_s is not None and t > end_s:
            break
        t_ms = int(t * 1000)
        n_in += 1

        is_charger = arb in R.CHARGER_IDS
        trace_lines.append("%s %d %X %d %s"
                           % ("C" if is_charger else "V", t_ms, arb,
                              1 if ext else 0, data.hex()))

        if is_charger:
            out = core.on_charger_frame(arb, ext, data, t_ms)
        else:
            out = core.on_vehicle_frame(arb, ext, data, t_ms)
        record(out)
        if core.state != last_state:
            golden_lines.append("S %d %s" % (t_ms, M.STATE_NAMES[core.state]))
            last_state = core.state

        if t_ms - last_tick_ms >= TICK_INTERVAL_MS:
            last_tick_ms = t_ms
            trace_lines.append("T %d" % t_ms)
            record(core.tick(t_ms))
            if core.state != last_state:
                golden_lines.append("S %d %s"
                                    % (t_ms, M.STATE_NAMES[core.state]))
                last_state = core.state

    with open(out_base + ".trace", "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(trace_lines))
        f.write("\n")
    with open(out_base + ".golden", "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(golden_lines))
        f.write("\n")

    print("frames in     : %d" % n_in)
    print("trace lines   : %d" % len(trace_lines))
    print("golden lines  : %d" % len(golden_lines))
    print("final state   : %s" % M.STATE_NAMES[core.state])
    print("stats         : %s" % core.stats)
    print("events        : %d" % len(core.events))
    for ev in core.events:
        print("  t=%-9d %-12s %s" % ev)
    return core


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture")
    ap.add_argument("--name", default=None,
                    help="output base name (default: capture stem)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--bus", type=int, default=None,
                    help="powertrain bus number; auto-detected if omitted. "
                         "Must be an int -- the parser compares it to "
                         "CANFrame.bus, so a string silently matches nothing.")
    # WRITES WHERE THE GATE READS. This defaulted to golden/ beside this
    # script, which diff_all.sh has not read since 2026-09-20 -- it globs
    # notes/artifacts/interposer-firmware/. A regeneration therefore
    # updated a directory nothing looks at and the gate then passed on
    # stale files; make_synthetic.py had the identical defect and was
    # repointed on 2026-10-05, and golden/ was deleted the same day
    # (user's call). Leaving this default behind would recreate the dead
    # directory the first time anyone ran it without --outdir.
    ap.add_argument("--outdir", default=None,
                    help="default: notes/artifacts/interposer-firmware/ "
                         "under VTRUX_DATA, which is what diff_all.sh reads")
    a = ap.parse_args()
    if a.outdir is None:
        import paths
        a.outdir = paths.fixtures()

    outdir = a.outdir
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    name = a.name
    if name is None:
        name = os.path.splitext(os.path.basename(a.capture))[0]

    build(a.capture, os.path.join(outdir, name),
          start_s=a.start, end_s=a.end, bus_filter=a.bus)


if __name__ == "__main__":
    main()
