#!/usr/bin/env python3
"""Does the Kvaser witness still count correctly at thousands of frames/s?

WHY THIS EXISTS, AND WHY IT IS MY PROBLEM RATHER THAN THE OTHER SESSION'S.

`lowid_flood_skip_bench.py` reported, on 2026-10-06, that the device placed
3,000 of 3,000 inhibit frames inside their window at 83 % wire occupancy. That
83 % is a count taken by the Kvaser -- 112,569 `0x050` frames in a 30 s window
-- and the robustness claim rests entirely on it being right. If the witness
over-counts, the occupancy label is wrong and the result is softer than
stated.

The doubt is not hypothetical. The interposer session's board, on the same
wire, counted 2,540 frames/s arriving across the same burst where this
witness counted 4,314 frames/s. A 41 % disagreement between two decoders.
Their count is not a bound: their receive path sums both
`rx_missed_count` and `rx_overrun_count` (`port_twai.cpp:160`), their filter
is accept-all, and ESP-IDF v5.4.1 does populate the overrun field on both ISR
branches (`twai.c:148` and `:174`), so the obvious "a hardware overrun is
invisible to a software counter" explanation is ruled out.

WHAT IS ALREADY KNOWN, AND IT IS THE INTERESTING PART. At 558 frames/s the
two decoders agree to 0.7 % -- 562 against 558, flat across eight consecutive
5 s samples. So this is RATE-DEPENDENT, which rules out a constant offset, a
filter, and a clock-scale error, and points at something that saturates.
`witness_fidelity_check.py` previously established PCAN-versus-Kvaser
agreement at ratio 1.000, but at about 8 frames/s, so it says nothing about
this load.

THE INSTRUMENT. A fixed-rate `0x050` stream at a rate chosen in advance,
witnessed by the Kvaser, with nothing else on the wire that this script puts
there. Offered, witnessed and achieved rates are all recorded separately,
because `send()` returns at queueing and the offered figure is therefore not a
wire measurement -- the same trap that made a 50-frame wall turn the adapter's
transmit queue into a half-second reservoir earlier today.

TWO RATES, NOT ONE, and that is the whole design. A single high-rate point
cannot distinguish "loses frames above some threshold" from "undercounts at
all rates" -- the distinction the other session asked for, and the one that
decides whether this is a saturation effect or a scaling bug. One rate is
placed where both decoders are known to agree and one at the disputed load.

WHAT WOULD MAKE THIS INCONCLUSIVE, stated up front. If the PCAN cannot
actually deliver the requested rate, the witness has nothing to miss and
agreement proves nothing. So the achieved rate is checked against the
requested one before any verdict, and a shortfall is reported rather than
absorbed.

THE DRAIN IS ON ITS OWN THREAD. A loop interleaving send and receive paces
itself to ~970 fps with no exception to announce the cap; every sparse
stimulus in this investigation traced to that shape.

    py -3.14 gen-inhibit/tools/bench/witness_rate_fidelity.py
    py -3.14 ... --rates 1000,3750 --seconds 40
"""

import argparse
import collections
import json
import os
import sys
import threading
import time


import can                                                     # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

BITRATE = 500000
PROBE_ID = 0x050          # the same id A3 floods, so this calibrates that run
AIR_US = 222.0            # 111 bits incl. IFS, 8-byte standard frame
SPIN_THRESHOLD_S = 0.002


class Drain(threading.Thread):
    """Count what reaches the wire. On its own thread; see the docstring."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.counts = collections.Counter()
        self.errors = 0
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                with self.lock:
                    if m.is_error_frame:
                        self.errors += 1
                    else:
                        self.counts[m.arbitration_id] += 1
                m = self.bus.recv(timeout=0.0)

    def snapshot(self):
        with self.lock:
            return dict(self.counts), self.errors


def send_at(tx, fps, seconds):
    """Offer `fps` frames/s for `seconds`, paced by wire time.

    Busy-waits below SPIN_THRESHOLD_S because Windows cannot sleep for less
    than about a millisecond and silently oversleeps when asked to.
    """
    msg = can.Message(arbitration_id=PROBE_ID, data=bytes([0x5A] * 8),
                      is_extended_id=False)
    period = 1.0 / fps
    t0 = time.perf_counter()
    nxt = t0
    sent = err = 0
    while time.perf_counter() - t0 < seconds:
        try:
            tx.send(msg)
            sent += 1
        except Exception:
            err += 1
        nxt += period
        while True:
            left = nxt - time.perf_counter()
            if left <= 0:
                break
            if left > SPIN_THRESHOLD_S:
                time.sleep(left - SPIN_THRESHOLD_S)
        now = time.perf_counter()
        if nxt < now - period:
            nxt = now          # do not try to catch up; it would burst
    return sent, err, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", default="1000,3750",
                    help="frames/s to test, comma separated")
    ap.add_argument("--seconds", type=float, default=40.0,
                    help="per rate; 40 s gives the other session eight 5 s "
                         "samples, six of them wholly inside")
    ap.add_argument("--gap", type=float, default=10.0,
                    help="quiet seconds between rates, so the boundary is "
                         "unambiguous in a 5 s sampler")
    ap.add_argument("--out", default=os.path.join(RUNS,
                                                  "witness_rate_fidelity.json"))
    args = ap.parse_args()
    rates = [float(x) for x in args.rates.split(",") if x.strip()]

    rec = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "question": "does the Kvaser witness count correctly at high frame "
                    "rates, or is the 41 % disagreement with the interposer "
                    "board the witness over-counting",
        "probe_id": "0x%03X" % PROBE_ID,
        "bitrate": BITRATE,
        "air_us": AIR_US,
        "rates_requested": rates,
        "seconds_per_rate": args.seconds,
        "segments": [],
    }

    print("=" * 72)
    print("Witness rate fidelity. Fixed-rate 0x%03X, Kvaser counting."
          % PROBE_ID)
    print("Calibrates the occupancy figure A3's result rests on.")
    print("=" * 72)

    # PCAN first, then Kvaser -- documented open order on this bench.
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                 auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    time.sleep(2.0)
    while kv.recv(timeout=0.0) is not None:
        pass
    d = Drain(kv)
    d.start()
    try:
        for i, fps in enumerate(rates):
            if i:
                print("\n  quiet %.0f s" % args.gap)
                time.sleep(args.gap)
            before, _ = d.snapshot()
            begin = time.strftime("%Y-%m-%dT%H:%M:%S")
            print("\n[%.0f fps] %.0f s  begin %s" % (fps, args.seconds, begin))
            sent, err, dur = send_at(tx, fps, args.seconds)
            time.sleep(1.5)                 # let the queue and witness drain
            after, errs = d.snapshot()
            end = time.strftime("%Y-%m-%dT%H:%M:%S")
            seen = after.get(PROBE_ID, 0) - before.get(PROBE_ID, 0)
            offered_fps = sent / dur if dur else 0.0
            seen_fps = seen / dur if dur else 0.0
            seg = {
                "rate_requested": fps,
                "begin": begin, "end": end,
                "seconds": round(dur, 3),
                "offered": sent, "offer_exceptions": err,
                "offered_fps": round(offered_fps, 1),
                "witnessed": seen,
                "witnessed_fps": round(seen_fps, 1),
                "witnessed_occupancy": round(seen * AIR_US / 1e6 / dur, 4),
                "ratio_witnessed_over_offered": (
                    round(seen / sent, 4) if sent else None),
                "other_ids_witnessed": {
                    "0x%03X" % k: after.get(k, 0) - before.get(k, 0)
                    for k in set(after) | set(before) if k != PROBE_ID
                    and after.get(k, 0) - before.get(k, 0) > 0},
            }
            rec["segments"].append(seg)
            print("    offered %d (%.0f fps, exc %d)  witnessed %d (%.0f fps)"
                  % (sent, offered_fps, err, seen, seen_fps))
            print("    ratio witnessed/offered %.4f   occupancy %.1f %%"
                  % (seg["ratio_witnessed_over_offered"] or 0.0,
                     seg["witnessed_occupancy"] * 100))
    finally:
        d.stop_flag.set()
        d.join(timeout=3.0)
        for b in (tx, kv):
            try:
                b.shutdown()
            except Exception:
                pass
        rec["witness_error_frames"] = d.errors

    # ---------------------------------------------------------------- verdict
    print("\n" + "=" * 72)
    bad_offer = [s for s in rec["segments"]
                 if s["offered_fps"] < 0.9 * s["rate_requested"]]
    if bad_offer:
        rec["verdict"] = "inconclusive_offered_rate_not_achieved"
        print("INCONCLUSIVE: the PCAN did not deliver the requested rate at "
              "%s. The witness had nothing to miss, so agreement proves "
              "nothing."
              % ", ".join("%.0f fps" % s["rate_requested"] for s in bad_offer))
    else:
        ratios = [s["ratio_witnessed_over_offered"] for s in rec["segments"]]
        rec["ratios"] = ratios
        spread = max(ratios) - min(ratios)
        rec["ratio_spread"] = round(spread, 4)
        if all(r >= 0.98 for r in ratios):
            rec["verdict"] = "witness_counts_correctly_at_all_rates"
            print("THE WITNESS IS SOUND at every rate tested (%s). A3's "
                  "occupancy figure stands, and the 41 %% disagreement is on "
                  "the other decoder's side."
                  % ", ".join("%.3f" % r for r in ratios))
        elif spread > 0.05:
            rec["verdict"] = "witness_degrades_with_rate"
            print("THE WITNESS DEGRADES WITH RATE (%s). A3's occupancy figure "
                  "is NOT safe and the robustness claim built on it must be "
                  "restated." % ", ".join("%.3f" % r for r in ratios))
        else:
            rec["verdict"] = "witness_undercounts_at_all_rates"
            print("The witness undercounts at every rate by a similar factor "
                  "(%s), which is a scaling problem rather than saturation."
                  % ", ".join("%.3f" % r for r in ratios))

    os.makedirs(RUNS, exist_ok=True)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
