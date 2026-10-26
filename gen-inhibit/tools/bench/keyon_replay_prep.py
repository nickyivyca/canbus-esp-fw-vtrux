"""Cut a replay that starts AT a key-on edge, for the 12.4 "restart timed to a key-on" row.

Gen-inhibit tester, 2026-10-10. The row (user): "a replayed capture starting at its
key-on edge, with the bus silent before it ... Use a capture from the 7.7 s cluster of
the deadline sweep". This finds the edge in the source capture and cuts from it with
cut_replay_window.py (one bus, canutils, extended ids kept), then CHECKS the cut:

  1. the cut's channel (cut_replay_window picks the channel with the most 0x051) is the
     channel the deadline sweep identified as the PT bus by content -- two different
     discriminators agreeing, or the cut is refused;
  2. the cut's first frame is the edge: a 0x592 whose IgnitionKeyState reads 1
     (cantools, epri-pt-bus.dbc), and the source's previous 0x592 on that channel read 0;
  3. the engine request (gen_torque_cmd != 0 or gen_rpm_ref >= 0, cantools) falls at the
     sweep's figure from the edge, to within 5 ms.

Times are offsets from the source's first frame in FILE order, as cut_replay_window
computes them, so the edge is found on the same scale the cut uses.

Run on madhouse-debian (the source is in the library there), from the project root:
  python3.13 tools/gen_inhibit/bench/keyon_replay_prep.py \\
      --capture projects/vtrux/logs/vtruxdrivetrainoverheattest.log --channel 3 --expect-req 7.741 \\
      --out projects/vtrux/notes/artifacts/gen-inhibit/runs/keyon_drivetrainoverheat_70s.log
"""
import argparse
import os
import subprocess
import sys

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True)
    ap.add_argument("--channel", type=int, required=True, help="the PT channel per the deadline sweep")
    ap.add_argument("--expect-req", type=float, required=True, help="the sweep's request time from key-on")
    ap.add_argument("--seconds", type=float, default=70.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import cantools
    from canre.parsers import parse_file
    epri = cantools.database.load_file(gi_paths.vehicle_dbc("epri-pt-bus.dbc"), strict=False)
    pt = cantools.database.load_file(gi_paths.vehicle_dbc("vtrux-powertrain-experimental.dbc"), strict=False)
    first = None
    key_prev = None
    edge = None
    for f in parse_file(a.capture):
        if first is None:
            first = f.timestamp
        if f.bus != a.channel or f.arbitration_id != 0x592:
            continue
        k = int(epri.decode_message(0x592, bytes(f.data), decode_choices=False)["IgnitionKeyState"])
        if key_prev == 0 and k == 1:
            edge = f.timestamp - first
            break
        key_prev = k
    if edge is None:
        print("REFUSED: no 0x592 0 -> 1 edge on channel %d" % a.channel)
        return 2
    print("edge at %.6f s from the source's first frame (channel %d)" % (edge, a.channel))
    # start a hair before the edge frame so float rounding cannot drop it; check 2 below
    # proves nothing earlier came along
    r = subprocess.run([sys.executable, os.path.join(HERE, "cut_replay_window.py"), "--capture", a.capture,
                        "--start", "%.6f" % (edge - 0.0000005), "--seconds", str(a.seconds), "--out", a.out],
                       capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode:
        print(r.stderr[-500:])
        return r.returncode
    chosen = [ln for ln in r.stdout.splitlines() if ln.startswith("powertrain bus is channel")]
    ok_ch = bool(chosen) and chosen[0].split()[4] == repr(a.channel)
    import can
    msgs = list(can.LogReader(a.out))
    m0 = msgs[0]
    key0 = (int(epri.decode_message(0x592, bytes(m0.data), decode_choices=False)["IgnitionKeyState"])
            if m0.arbitration_id == 0x592 else None)
    req = None
    for m in msgs:
        if m.arbitration_id == 0x051:
            d = pt.decode_message(0x051, bytes(m.data), decode_choices=False)
            if d["gen_rpm_ref"] >= 0 or d["gen_torque_cmd"] != 0:
                req = m.timestamp - m0.timestamp
                break
    mono = all(y.timestamp >= x.timestamp for x, y in zip(msgs, msgs[1:]))
    checks = [("cut channel = the sweep's PT channel %d" % a.channel, ok_ch),
              ("first frame is the edge: 0x592 with the key on", m0.arbitration_id == 0x592 and key0 == 1),
              ("engine request at %.3f s from the edge (sweep %.3f, within 5 ms)" % (req or -1, a.expect_req),
               req is not None and abs(req - a.expect_req) <= 0.005),
              ("timestamps monotonic in the cut (%d frames)" % len(msgs), mono)]
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
    if not all(ok for _l, ok in checks):
        print("REFUSED: the cut does not start at the key-on edge as required")
        return 1
    print("OK -- %s starts at the key-on edge" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
