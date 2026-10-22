"""Harness test for provenance_classifier: synthetic replays with known answers.

    py -3.14 tools/gen_inhibit/bench/test_provenance_classifier.py
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance_classifier as P                                 # noqa: E402


def cmd(k):
    return bytes([0x08, 0x00, 0x80, 0xFF, 0x7F, k & 0x0F])


def scene(n=60, early_at=None, late_at=None, late_by=0.0015, drop_answer_at=None, jitter_us=40,
          offset=1000.0, drift=1.00002, seed=1, answers=True, swap_at=None):
    """A source window and the wire it produced. VCM commands every 10 ms (the one at
    early_at comes 2.4 ms after our previous answer), filler ids every 1 ms, our
    answer 0.42 ms after each command (late_at: lands late_by after the next
    command). swap_at: two replayed frames swapped on the wire (FIFO broken)."""
    rnd = random.Random(seed)
    src, wire = [], []
    times = [i * 0.010 for i in range(n)]
    if early_at is not None:
        times[early_at] = times[early_at - 1] + 0.00042 + 0.0024
    for t in [i * 0.001 + 0.0005 for i in range(int(n * 10))]:
        src.append((t, 0x308, bytes([1, 2])))
    for k, t in enumerate(times):
        src.append((t, P.CMD_ID, cmd(k)))
    src.sort(key=lambda x: x[0])

    def to_wire(t):
        return offset + drift * t + rnd.uniform(0, jitter_us) * 1e-6

    for t, i, d in src:
        wire.append((to_wire(t), i, d))
    sent = 0
    for k, t in enumerate(times):
        if not answers or k == drop_answer_at:
            continue
        ta = to_wire(t) + 0.00042
        if k == late_at:
            ta = to_wire(times[k + 1]) + late_by
        wire.append((ta, P.CMD_ID, cmd(k + 1)))
        sent += 1
    wire.sort(key=lambda x: x[0])
    if swap_at is not None:
        reps = [x for x in range(len(wire)) if wire[x][1] == 0x308]
        a_, b_ = reps[swap_at], reps[swap_at + 1]
        wire[a_], wire[b_] = (wire[b_][0], 0x308, bytes([9, 9])), (wire[a_][0], 0x308, bytes([1, 2]))
    return src, wire, sent


def main():
    s, w, n = scene()
    r = P.classify(s, w, n)
    s2, w2, n2 = scene(early_at=30)
    r2 = P.classify(s2, w2, n2)
    s3, w3, n3 = scene(late_at=20)
    r3 = P.classify(s3, w3, n3)
    s3b, w3b, n3b = scene(late_at=20, late_by=0.0003)
    r3b = P.classify(s3b, w3b, n3b)
    s5, w5, n5 = scene(drop_answer_at=12)
    r5 = P.classify(s5, w5, n5 + 1)
    sn, wn, _ = scene(answers=False)
    s6, w6, n6 = scene(swap_at=100)
    checks = [
        ("on-time answers: as specified, every replayed frame found, counts hold",
         r["verdict"] == "as specified" and r["on_time"] == n and r["assigned"] == 60
         and r["device_frames"] == n and r["replayed_found"] == r["replayed_frames"]),
        ("timing fit still reported: drift recovered, residuals present",
         abs(r["align"]["b"] - 1.00002) < 1e-5 and r["align"]["residual_us"]["max"] <= 45),
        ("a VCM command 2.4 ms after our answer is the VCM's, not a double",
         r2["verdict"] == "as specified" and r2["double"] == [] and r2["on_time"] == n2),
        ("a late answer landing after a later replayed frame is LATE",
         r3["verdict"] == "NOT as specified" and len(r3["late"]) == 1),
        ("a late answer right behind the next command (nothing replayed between) is "
         "AMBIGUOUS -> UNRESOLVED, never on time",
         r3b["verdict"] == "UNRESOLVED" and len(r3b["ambiguous"]) == 1),
        ("count invariant: device says one more than the wire shows -> UNRESOLVED",
         r5["verdict"] == "UNRESOLVED"
         and any(u.startswith("count:") for u in r5["unresolved"])),
        ("RESPOND-style: no device 0x051 expected and none present -> as specified",
         P.classify(sn, wn, 0)["verdict"] == "as specified"
         and P.classify(sn, wn, 0)["device_frames"] == 0),
        ("RESPOND-style: a device 0x051 appearing when none is expected -> UNRESOLVED",
         P.classify(s, w, 0)["verdict"] == "UNRESOLVED"),
        ("a replayed command missing from the wire -> UNRESOLVED by the count invariant",
         (lambda sw: (lambda r: r["verdict"] == "UNRESOLVED"
                      and any("replayed frames found" in u for u in r["unresolved"]))(
             P.classify(sw[0], [x for x in sw[1] if not (x[1] == P.CMD_ID and x[2] == cmd(7)
                                                         and 0.0695 < x[0] - sw[1][0][0] < 0.0705)],
                        sw[2])))(scene())),
        ("a replayed frame altered on the wire (order broken) -> UNRESOLVED, not a verdict",
         P.classify(s6, w6, n6)["verdict"] == "UNRESOLVED"),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
