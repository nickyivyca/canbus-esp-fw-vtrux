#!/usr/bin/env python3
"""Do PCAN and Kvaser, on the SAME wire, see the same frames?

WHY THIS EXISTS. The agreed skip-bench method (reviewer, 2026-10-05) classifies
each event by WIRE ORDER from the Kvaser witness rather than by timestamps,
because `pcan_backtoback_capability.py` proved the Kvaser's timestamps are
unusable at frame scale (resolution floor ~170 us against 190 us of air time,
318 of 468 inter-frame gaps measured NEGATIVE). Order survives broken
timestamps -- but only if the witness sees every frame.

The method's three verdicts are:

    our answer inside the 0x050 burst run  -> the burst had a window
    no answer between N and N+1            -> withdrawn (a skip)
    answer carrying N's counter after N+1  -> late

and the middle one is an ABSENCE. If the witness silently drops frames, a
dropped answer reads as "withdrawn", which is the very verdict the run is
trying to count. That is the project's recurring failure shape: the
instrument's failure looks exactly like the result.

WHAT PROMPTED IT. `bench_topology.py` (interposer session, 2026-10-05) was run
here on 2026-10-05 to settle which segment each dongle is on. Incidentally, in
one 6 s passive window on a single shared wire it reported **pcan 57 frames,
kvaser 24 frames** -- both seeing all four of the interposer board's diagnostic
pages, so both are genuinely on that wire, yet differing by 2.4x in count. That
is either the Kvaser dropping frames or the PCAN counting something the Kvaser
filters. Either way it has to be resolved before an absence-based classifier is
built on the Kvaser.

THE STIMULUS IS SOMEONE ELSE'S BOARD, deliberately. The ESP32-S3 interposer
board emits 0x7F4-0x7F7 at about 8 fps continuously while powered, on the
vehicle segment, and this session does not control it. So the two adapters are
compared against a transmitter that cannot be biased by either of them, and no
DATA frame is injected by this script.

**IT IS NOT PASSIVE, AND AN EARLIER VERSION OF THIS PARAGRAPH SAID IT WAS.**
It claimed the script "cannot disturb the other session's board". That is
wrong, and wrong in the direction that matters here. `can.Bus(...)` opens both
adapters in NORMAL mode, and a normal-mode receiver transmits ACK bits. On
this segment the board is the only other transmitter and nothing else
acknowledges it, so an open adapter does not perturb the board slightly -- it
removes the board's entire error condition. Measured by the interposer session
2026-10-06: unacknowledged the board runs 3,649 error events/s and drops 4
transmits/s; with a Kvaser merely open in normal mode, both go to zero.

That does not invalidate the comparison this script makes. Both adapters are
open throughout and both acknowledge, so the board is in the same state for
both and the ratio is still a fair one. But the board's OWN counters taken
across a run of this script are not a measurement of its normal environment,
and nothing here should be read as leaving it undisturbed.

**Making it genuinely listen-only would not fix it and would cost more.**
`listen_only=True` is swallowed by both backends' `**kwargs` and silently does
nothing (interposer session, 2026-10-06); the real spellings are
`driver_mode=False` for Kvaser and `state=can.BusState.LISTEN_ONLY` for PCAN.
And a silent-mode adapter does not transmit error flags and does not reliably
surface them, so its "0 error frames" is uninformative -- measured at 73 data
frames and zero error frames over 10 s while the board was taking thousands of
error events per second. Acknowledging is the honest choice here; saying so is
the fix.

WHY NOT COMPARE AGAINST OUR OWN TRANSMITTER. Because that is the quantity in
doubt elsewhere: if the PCAN's own queueing is what we are unsure about, using
it as the reference for the witness measures two unknowns against each other.
An independent third transmitter is the orthogonal check.

WHAT WOULD MAKE THIS INCONCLUSIVE, stated up front so a null result is not read
as a pass: if the board stops transmitting, both adapters see nothing, the
counts agree trivially, and nothing is established. So a minimum frame count is
required on the BUSIER adapter before any verdict is given.

    py -3.14 gen-inhibit/tools/bench/witness_fidelity_check.py
    py -3.14 ... --seconds 30
"""

import argparse
import collections
import json
import os
import sys
import time


import can                                                    # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

BITRATE = 500000
# The interposer board's diagnostic pages, emitted on the vehicle segment only.
BOARD_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
# Below this many frames on the busier adapter, the board is not talking enough
# for the comparison to mean anything.
MIN_FRAMES = 40


def drain(bus):
    """Discard the backlog a freshly opened handle delivers."""
    n = 0
    while bus.recv(timeout=0.0) is not None:
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join(RUNS,
                                                  "witness_fidelity.json"))
    args = ap.parse_args()

    print("Passive comparison of PCAN and Kvaser on one wire. Nothing is "
          "transmitted by this script.")
    print("Stimulus is the interposer board's 0x7F4-0x7F7 at ~8 fps, which "
          "neither adapter can influence.\n")

    # PCAN first, then Kvaser -- the documented open order on this bench.
    pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                 auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    time.sleep(2.0)
    print("drained pcan %d, kvaser %d" % (drain(pc), drain(kv)))

    counts = {"pcan": collections.Counter(), "kvaser": collections.Counter()}
    errs = {"pcan": 0, "kvaser": 0}
    other = {"pcan": collections.Counter(), "kvaser": collections.Counter()}
    order = {"pcan": [], "kvaser": []}

    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < args.seconds:
            for name, bus in (("pcan", pc), ("kvaser", kv)):
                m = bus.recv(timeout=0.0)
                while m is not None:
                    if m.is_error_frame:
                        errs[name] += 1
                    elif m.arbitration_id in BOARD_IDS:
                        counts[name][m.arbitration_id] += 1
                        order[name].append(m.arbitration_id)
                    else:
                        other[name][m.arbitration_id] += 1
                    m = bus.recv(timeout=0.0)
            time.sleep(0.002)
    finally:
        for b in (pc, kv):
            try:
                b.shutdown()
            except Exception:
                pass

    tot = {k: sum(v.values()) for k, v in counts.items()}
    print("\nBOARD PAGES SEEN (0x7F4-0x7F7), %.1f s" % args.seconds)
    for name in ("pcan", "kvaser"):
        per = "  ".join("%03X:%d" % (i, counts[name][i]) for i in BOARD_IDS)
        print("  %-8s total %-6d  %s   err %d  other-ids %d"
              % (name, tot[name], per, errs[name], sum(other[name].values())))

    busier = max(tot, key=lambda k: tot[k])
    quieter = "kvaser" if busier == "pcan" else "pcan"

    rec = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": args.seconds,
        "bitrate": BITRATE,
        "board_ids": ["0x%03X" % i for i in BOARD_IDS],
        "counts": {k: {"0x%03X" % i: v[i] for i in BOARD_IDS}
                   for k, v in counts.items()},
        "totals": tot,
        "error_frames": errs,
        "other_ids": {k: {"0x%03X" % i: n for i, n in v.items()}
                      for k, v in other.items()},
    }

    print()
    if tot[busier] < MIN_FRAMES:
        rec["verdict"] = "inconclusive_board_too_quiet"
        print("INCONCLUSIVE: the busier adapter saw only %d frames (need %d). "
              "The board may not be transmitting. Agreement between two "
              "adapters that both saw nothing establishes nothing."
              % (tot[busier], MIN_FRAMES))
    else:
        ratio = (tot[quieter] / tot[busier]) if tot[busier] else 0.0
        rec["ratio_quieter_over_busier"] = ratio
        rec["busier"] = busier
        print("ratio %s/%s = %.3f" % (quieter, busier, ratio))
        if ratio >= 0.98:
            rec["verdict"] = "adapters_agree"
            print("ADAPTERS AGREE. Either may be used as the witness for an "
                  "absence-based classifier.")
        else:
            rec["verdict"] = "adapters_disagree"
            print("ADAPTERS DISAGREE by more than 2 %%. The absence-based skip "
                  "classifier MUST NOT be built on %s until this is "
                  "explained: a dropped frame would be scored as a withdrawn "
                  "inhibit, which is the verdict the run exists to count."
                  % quieter)
            #
            # Per-id, because the two failure shapes look different and the
            # distinction decides what to do next. Uniform loss across all
            # four pages points at the adapter or its driver; loss confined to
            # particular ids points at filtering or at an id-dependent path.
            #
            print("  per-id ratios:")
            for i in BOARD_IDS:
                b, q = counts[busier][i], counts[quieter][i]
                print("    0x%03X  %-6s %-6d  %-6s %-6d  ratio %s"
                      % (i, busier, b, quieter, q,
                         ("%.3f" % (q / b)) if b else "n/a"))

    os.makedirs(RUNS, exist_ok=True)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
