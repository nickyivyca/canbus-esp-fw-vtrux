#!/usr/bin/env python3
"""Cut a replayable slice out of a big capture, on the machine that holds it.

    python3 cut_replay_window.py --capture projects/vtrux/logs/vtruxscottsvalleydrive.log \\
                                --start 1868.34 --seconds 300 \\
                                --out .../runs/scottsvalley_armable_300s.log

RUN THIS ON `madhouse-debian`. The source is 1.2 GB and lives in the SeaDrive
library; slicing there and copying back a ~30 MB result beats hydrating the whole
file onto a Windows box (`data-sources.md`).

WHY. The armable window scan found `vtruxscottsvalleydrive.log` holding key on,
contactors closed and engine off for 2128 s at 2367.7 frames/s -- an EV drive, so
the generator never runs. The bench's longest live arm to date is ~6.7 s, which is
what caps every tail measurement at ~3000 samples (spec 5). A few hundred seconds
of this is the stimulus that lifts it.

OUTPUT FORMAT IS canutils, matching `truck_passive2.log`, because the bench reads
its capture with `can.LogReader` and that is what the existing replay already
consumes. So `e4_device_calibration.py` takes this file unchanged.

TWO THINGS THIS GETS RIGHT ON PURPOSE:

  ONE BUS, chosen by content. Channel numbers vary per capture with dongle
  insertion order, so the powertrain bus is identified as the one carrying 0x051
  and every other channel is dropped rather than merged. Merging would fake both
  the frame rate and the signal states, silently.

  EXTENDED IDS KEPT EXTENDED. canutils marks a 29-bit id with 8 hex digits and an
  11-bit id with 3, and `can.LogReader` infers the flag from that width. An
  earlier loader in this project dropped `is_extended_id` and re-sent twelve
  J1939 ids as 11-bit; the frames looked plausible and the bus state was wrong.
  Writing the wrong width here would reintroduce exactly that.
"""

import argparse
import os
import sys
import time

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

CMD = 0x051


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--start", type=float, required=True,
                    help="seconds into the capture (as the window scan reports)")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", type=float, default=1788744351.0,
                    help="epoch base for the written timestamps; only the "
                         "differences matter to the replay")
    args = ap.parse_args()

    from canre.parsers import parse_file

    t0 = time.time()
    lo, hi = args.start, args.start + args.seconds

    # Two passes rather than holding a 1.2 GB capture in memory: the first finds
    # the powertrain channel from the slice itself, the second writes it.
    first = None
    chans = {}
    for f in parse_file(args.capture):
        if first is None:
            first = f.timestamp
        off = f.timestamp - first
        if off < lo:
            continue
        if off >= hi:
            break
        if f.arbitration_id == CMD:
            chans[f.bus] = chans.get(f.bus, 0) + 1
    if not chans:
        print("no 0x051 in %.1f-%.1f s -- wrong window, or the wrong capture"
              % (lo, hi))
        return 2
    pt = max(chans, key=chans.get)
    print("powertrain bus is channel %r (%d x 0x051 in the slice); other "
          "channels dropped" % (pt, chans[pt]))

    n, first2, kept_ids = 0, None, set()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as out:
        for f in parse_file(args.capture):
            if first2 is None:
                first2 = f.timestamp
            off = f.timestamp - first2
            if off < lo:
                continue
            if off >= hi:
                break
            if f.bus != pt:
                continue
            # 8 hex digits for a 29-bit id, 3 for an 11-bit one: that width is
            # how can.LogReader decides, and getting it wrong re-sends J1939
            # frames as standard ones.
            ident = ("%08X" % f.arbitration_id) if f.is_extended \
                else ("%03X" % f.arbitration_id)
            data = "".join("%02X" % b for b in f.data)
            out.write("(%.6f) can0 %s#%s\n"
                      % (args.base + (off - lo), ident, data))
            n += 1
            kept_ids.add((f.arbitration_id, f.is_extended))

    dur = args.seconds
    ext = sum(1 for _, e in kept_ids if e)
    print("wrote %s" % args.out)
    print("  %d frames over %.1f s = %.1f frames/s" % (n, dur, n / dur))
    print("  %d distinct ids, %d of them extended" % (len(kept_ids), ext))
    print("  %.0f s" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
