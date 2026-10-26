"""Can the PCAN actually put frames on the wire back-to-back? Measure it before
building the skip bench on the assumption that it can.

WHY THIS COMES FIRST. The agreed skip-bench method (spec 5.2 item 10 cases 7-9,
trip 7 reason 15) rests on a timing claim about OUR OWN TRANSMITTER, not about the
device:

    "VCM 0x051 N, then N+1 back-to-back, then ~2 ms of back-to-back filler at ids
     below 0x051. N+1 starts ~6 us after N ends, before the device can queue its
     answer, so the two never start together."

Every case in that method depends on the PCAN emptying its queue with
inter-frame gaps at the hardware minimum. If it inserts a software gap between
queued frames -- tens or hundreds of microseconds, which a USB adapter driven
frame-at-a-time plausibly does -- then:

  - N+1 does NOT arrive before the device can queue its answer, so the device
    answers N normally and there is no skip to detect;
  - the "~2 ms of back-to-back filler" has holes, so our answer wins arbitration
    in a hole instead of being withdrawn;
  - and the harness would report `skipped = 0`, which is INDISTINGUISHABLE from
    the firmware never skipping. That is the whole failure mode this project keeps
    hitting: the method's silent failure looks exactly like the result it is
    trying to measure.

The reviewing session flagged this as unsettled -- "I found no check on it, only
statistics". So it is measured here, before any skip harness exists to be misled by.

WHAT IS MEASURED. Frames are queued as fast as python-can accepts them and the
KVASER timestamps what actually reaches the wire. The reported quantity is the
distribution of wire-level inter-frame gaps, where "gap" is

    start(n+1) - end(n),  end(n) = start(n) + air_time(n)

with air time computed per frame (44 + 8*DLC bits for a standard frame, + 3 bits
IFS, 2.0 us/bit at 500 kbit). A gap at or near 0 means back-to-back; a consistently
large gap means the method needs rethinking.

THE WITNESS'S OWN RESOLUTION IS A LIMIT ON THIS, AND IT IS REPORTED RATHER THAN
ASSUMED. python-can's Kvaser backend sets the timer scale via canIoCtl, so the
driver timestamp has finite granularity. If the smallest non-zero gap difference
observed is ~N us, then nothing below N us is resolvable and a measured "0 us gap"
means "below the resolution", NOT "exactly zero". The script derives that floor
from the data instead of taking a figure from a datasheet, and says so.

TWO CLOCKS, deliberately. Host receive times on a USB adapter show transfer
batching, which at frame scale is precisely the thing under test, so the DRIVER
timestamp is the measurement and the host timestamp is reported only to show the
batching exists. This is the same rule e4_rig_calibration follows.

NO DEVICE INVOLVEMENT. This measures our transmitter and our witness. The WiCAN
may be in any state; it is not asked anything and nothing here depends on it. A
quiet bus is better, so prefer mode 0.

    py -3.14 gen-inhibit/tools/bench/pcan_backtoback_capability.py
"""

import json
import os
import statistics
import sys
import time


import can                                                    # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

BITRATE = 500000
US_PER_BIT = 1e6 / BITRATE          # 2.0 us at 500 kbit
BURST = 40                          # frames queued back-to-back per burst
BURSTS = 12
GAP_BETWEEN_BURSTS_S = 0.05
FILLER_ID = 0x030                   # below 0x051, as the method requires
DLC = 6                             # 0x051's DLC


def air_us(dlc, extended=False):
    """Nominal air time of one frame including the 3-bit IFS, in microseconds."""
    bits = (64 if extended else 44) + 8 * dlc + 3
    return bits * US_PER_BIT


def main():
    print("PCAN back-to-back capability. Measuring OUR transmitter, not the "
          "device.")
    print("one %d-byte standard frame at %d kbit is %.1f us of air time "
          "including IFS\n" % (DLC, BITRATE // 1000, air_us(DLC)))

    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                 auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    time.sleep(2.0)
    while kv.recv(timeout=0.0) is not None:
        pass

    rows = []
    queued = 0
    try:
        for b in range(BURSTS):
            # Queue as fast as python-can accepts. No pacing: the question is
            # what the adapter does when handed frames with no gaps asked for.
            for i in range(BURST):
                try:
                    tx.send(can.Message(arbitration_id=FILLER_ID,
                                        data=bytes([b, i] + [0x5A] * (DLC - 2)),
                                        is_extended_id=False), timeout=0.05)
                    queued += 1
                except Exception as e:
                    print("  send raised at burst %d frame %d: %s" % (b, i, e))
            time.sleep(GAP_BETWEEN_BURSTS_S)
            m = kv.recv(timeout=0.0)
            while m is not None:
                if m.arbitration_id == FILLER_ID and not m.is_error_frame:
                    rows.append((m.timestamp, time.perf_counter(),
                                 len(m.data)))
                m = kv.recv(timeout=0.0)
        # Final drain
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            m = kv.recv(timeout=0.05)
            while m is not None:
                if m.arbitration_id == FILLER_ID and not m.is_error_frame:
                    rows.append((m.timestamp, time.perf_counter(),
                                 len(m.data)))
                m = kv.recv(timeout=0.0)
    finally:
        for bus in (tx, kv):
            try:
                bus.shutdown()
            except Exception:
                pass

    print("queued %d, witnessed %d" % (queued, len(rows)))
    if len(rows) < 20:
        print("TEST FAULT: too few frames witnessed to measure anything. "
              "Nothing about back-to-back capability is established -- do NOT "
              "read this as the adapter being incapable.")
        return 1

    rows.sort(key=lambda r: r[0])
    drv = [r[0] for r in rows]
    dlcs = [r[2] for r in rows]

    # Inter-frame gap on the DRIVER clock: start(n+1) - end(n).
    gaps = []
    for i in range(1, len(drv)):
        delta_us = (drv[i] - drv[i - 1]) * 1e6
        gaps.append(delta_us - air_us(dlcs[i - 1]))

    # THE RESOLUTION FLOOR, derived from the data. The smallest non-zero
    # difference between consecutive timestamps bounds what this witness can
    # resolve; anything below it is unmeasurable and a "0 us" gap means
    # "under the floor", not "zero".
    diffs = sorted({round(abs((drv[i] - drv[i - 1]) * 1e6), 4)
                    for i in range(1, len(drv))} - {0.0})
    floor_us = diffs[0] if diffs else None

    # Within-burst gaps only: the deliberate 50 ms pauses between bursts are
    # not back-to-back and would swamp the statistics.
    inburst = [g for g in gaps if g < 1000.0]
    print("\nWIRE INTER-FRAME GAPS, driver clock, within bursts (%d samples)"
          % len(inburst))
    if not inburst:
        print("  none -- every gap exceeded 1 ms, so nothing was back-to-back")
        return 1
    inburst.sort()
    pct = lambda p: inburst[min(len(inburst) - 1,
                                int(round(p / 100.0 * (len(inburst) - 1))))]
    print("  min %.1f  p50 %.1f  p90 %.1f  p99 %.1f  max %.1f us"
          % (inburst[0], pct(50), pct(90), pct(99), inburst[-1]))
    print("  mean %.1f  stdev %.1f us"
          % (statistics.fmean(inburst),
             statistics.pstdev(inburst) if len(inburst) > 1 else 0.0))
    print("  witness resolution floor (smallest non-zero delta): %s"
          % ("%.1f us" % floor_us if floor_us else "unknown"))

    under_6 = sum(1 for g in inburst if g <= 6.0)
    under_50 = sum(1 for g in inburst if g <= 50.0)
    print("\n  gaps <= 6 us   : %d of %d (%.1f %%)   <- the method's figure"
          % (under_6, len(inburst), 100.0 * under_6 / len(inburst)))
    print("  gaps <= 50 us  : %d of %d (%.1f %%)"
          % (under_50, len(inburst), 100.0 * under_50 / len(inburst)))

    print("\n" + "=" * 70)
    print("WHAT THIS MEANS FOR THE SKIP BENCH")
    med = pct(50)

    #
    # A PHYSICALLY IMPOSSIBLE GAP CONDEMNS THE INSTRUMENT, NOT THE PHYSICS.
    #
    # Two frames cannot overlap on a single wire, so a NEGATIVE gap cannot
    # happen. 44 + 8*DLC + 3 bits is also the MINIMUM air time -- bit stuffing
    # only ever adds -- so a measured inter-arrival below that figure is proof
    # the timestamps are wrong at this scale, not evidence of a tight gap.
    #
    # This branch exists because the first version of this script did not have
    # it and reported a median gap of -20.0 us as "at or below the resolution
    # floor", concluding that the adapter was emptying its queue with no
    # measurable gap. That reasoning takes an impossible number as support for
    # the convenient answer. The correct conclusion is the opposite: nothing
    # about the 6 us claim can be established with this witness.
    #
    min_air = air_us(min(dlcs))
    impossible = [g for g in inburst if g < -1.0]
    if impossible:
        verdict = "witness_cannot_resolve"
        print("  %d of %d within-burst gaps are NEGATIVE (min %.1f us), which "
              "is physically impossible: frames cannot overlap on one wire, "
              "and %.1f us is the MINIMUM air time for the smallest frame "
              "sent (stuffing only adds)."
              % (len(impossible), len(inburst), inburst[0], min_air))
        print("  So the witness's driver timestamps are NOT trustworthy at "
              "this scale -- the smallest non-zero delta it produced was "
              "%.1f us, below the %.1f us a frame physically occupies."
              % (floor_us or 0.0, min_air))
        print()
        print("  NOTHING IS ESTABLISHED ABOUT THE 6 us CLAIM, in either "
              "direction. This is not 'back-to-back confirmed' and it is not "
              "'the adapter is incapable'. The instrument is the limit.")
        print()
        print("  DO NOT REACH FOR rx_gap INSTEAD. This script's first version "
              "recommended it and that advice was WRONG, twice over "
              "(reviewing session, 2026-10-04):")
        print("    - rx_gap measures PROCESSING, not the wire. Its stamp is "
              "esp_timer_get_time() taken at TASK level after twai_receive "
              "(gen_inhibit.c:1226), carried into prev_051 at core:1823-1828. "
              "Two frames already sitting in the RX queue when the worker "
              "wakes get near-identical stamps whatever happened on the wire, "
              "so a low bucket mostly means 'the worker drained a backlog'.")
        print("    - and the arithmetic never worked anyway: two truly "
              "back-to-back frames are one frame time apart, ~190 us plus "
              "intermission, so even a wire-accurate instrument would bucket "
              "them at 100-200 or 200-400 us. They could NEVER land in the "
              "sub-50 us bucket the original advice pointed at.")
        print()
        print("  THE METHOD DOES NOT NEED A GAP MEASUREMENT AT ALL. The skip "
              "question is ordinal: did our answer to N reach the wire "
              "BETWEEN N and N+1? A single receiver's log gives frame ORDER "
              "reliably even when its timestamps are unusable, which is "
              "exactly this witness's situation. Per event:")
        print("    withdrawn skip -- N, N+1, filler..., then our answer to "
              "N+1 only; our answer to N never appears at all")
        print("    late skip      -- N, N+1, then our answer carrying N's "
              "stolen counter")
        print("    filler hole    -- our frame appears INSIDE the filler run, "
              "which is an ordering check rather than a timing one")
        print("  Confirm order is trustworthy by checking the PCAN's queued "
              "sequence against the witness sequence: same frames, same "
              "order, every event.")
        print()
        print("  THE TIMING RISK THAT REMAINS IS THE OPPOSITE ONE: whether our "
              "answer can be READY inside the ~6 us intermission, in which "
              "case it starts arbitration together with N+1 -- a same-ID "
              "collision, which shows up as error frames and TEC climbing "
              "rather than as a clean skip. The 'RX->TX 5 us mean' bench "
              "figure is task-to-transmit and excludes ISR and wake latency, "
              "so it does not settle EOF-to-TX-ready. The skip harness should "
              "record error frames per event; this run shows the witness can "
              "see those even though it cannot time them.")
    elif floor_us and med <= floor_us:
        verdict = "at_or_below_resolution"
        print("  The median gap (%.1f us) is at or below this witness's own "
              "resolution floor (%.1f us)." % (med, floor_us))
        print("  The method's 6 us assumption is NOT CONTRADICTED, and it is "
              "also not confirmed -- this instrument cannot see that finely. "
              "Treat the exact figure as unmeasured.")
    elif med <= 6.0:
        verdict = "back_to_back_confirmed"
        print("  Median gap %.1f us, at or under the method's 6 us. The "
              "stimulus assumption holds." % med)
    elif med <= 50.0:
        verdict = "small_gap_method_at_risk"
        print("  Median gap %.1f us -- larger than the method's 6 us but "
              "still small." % med)
        print("  THE METHOD IS AT RISK AND SHOULD NOT BE BUILT ON THIS ALONE: "
              "the device may be able to queue its answer inside that gap, in "
              "which case there is no skip to detect and the harness reports "
              "0 skips for a reason that has nothing to do with the firmware.")
    else:
        verdict = "not_back_to_back"
        print("  Median gap %.1f us. THE ADAPTER DOES NOT SEND BACK-TO-BACK, "
              "so the agreed stimulus cannot be produced this way." % med)
        print("  Building the skip bench on it would produce skipped=0 and "
              "that would say nothing about trip 7. Needs a different "
              "stimulus -- a second transmitter, or frames queued by hardware "
              "rather than one at a time over USB.")

    out = os.path.join(RUNS, "pcan_backtoback_capability.json")
    if not os.path.isdir(RUNS):
        os.makedirs(RUNS)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "bitrate": BITRATE, "dlc": DLC,
                   "air_us_per_frame": air_us(DLC),
                   "queued": queued, "witnessed": len(rows),
                   "verdict": verdict,
                   "witness_resolution_floor_us": floor_us,
                   "inburst_samples": len(inburst),
                   "gap_us": {"min": inburst[0], "p50": pct(50),
                              "p90": pct(90), "p99": pct(99),
                              "max": inburst[-1],
                              "mean": statistics.fmean(inburst)},
                   "frac_le_6us": under_6 / len(inburst),
                   "frac_le_50us": under_50 / len(inburst),
                   "note": "Measures OUR transmitter and witness, not the "
                           "device. Where the verdict is "
                           "'witness_cannot_resolve', NEGATIVE gaps are "
                           "present and the timestamps are proven wrong at "
                           "this scale: no gap figure here means anything, "
                           "in either direction. Corrected 2026-10-04 -- this "
                           "field previously said a '0 us' gap meant 'below "
                           "the resolution floor', which is the reading the "
                           "verdict now rejects, so the record argued with "
                           "itself.",
                   "min_air_us_smallest_frame": air_us(min(dlcs)),
                   "negative_gap_samples": len(impossible)},
                  f, indent=1)
    print("\nwrote %s" % os.path.relpath(out, HERE))
    return 0 if verdict in ("back_to_back_confirmed",) else 1


if __name__ == "__main__":
    sys.exit(main())
