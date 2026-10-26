#!/usr/bin/env python3
"""B24 (baseline part B): spec 4 / 4.1, the inhibit frame's content, judged from the
device frames of every B2 and B3 arm. Gen-inhibit tester, 2026-10-16. No new rig.

Spec 4's table, for every device 0x051 against the VCM command in front of it:
  DLC 6; B0 0x08 (held, not mirrored: 4.1); B1 0x00 and B2 0x80 (zero torque);
  B3, B4 the VCM's (mirrored); B5 low nibble the VCM's counter + 1.
Which 0x051 are the device's is decided by provenance_classifier.classify (queue
order against the replay; its own count and ambiguity rules), unchanged. The
classifier returns device frame TIMES (keep_frames); the bytes are read back off
the witness at those times, and the command in front is the last replayed 0x051
on the wire before each.

WHAT IT CANNOT SHOW, and says so per arm: "held, not mirrored" is only exercised
by a VCM command whose B0 is not 0x08. If every command in front reads 0x08, a
device that mirrored B0 would pass too, so the verdict on B0 is then "consistent,
not discriminating".

    py -3.14 tools/gen_inhibit/bench/b24_frame_content_judge.py <run.json> [...]
Run from the reverse-it root. Tested by test_b24_frame_content_judge.py.
"""
import bisect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance_classifier as P     # noqa: E402

CMD = 0x051


def frame_faults(d, front):
    """Spec 4's table for one device frame d against the VCM command front
    (bytes). Returns the list of failed rows ([] if it conforms)."""
    bad = []
    if len(d) != 6:
        return ["DLC %d" % len(d)]
    if d[0] != 0x08:
        bad.append("B0 0x%02X" % d[0])
    if d[1] != 0x00 or d[2] != 0x80:
        bad.append("B1-B2 %02X %02X" % (d[1], d[2]))
    if front is None or len(front) < 6:
        bad.append("no VCM command in front")
        return bad
    if d[3] != front[3] or d[4] != front[4]:
        bad.append("B3-B4 %02X %02X, VCM %02X %02X" % (d[3], d[4], front[3], front[4]))
    if (d[5] & 0x0F) != ((front[5] + 1) & 0x0F):
        bad.append("counter %d after VCM %d" % (d[5] & 0x0F, front[5] & 0x0F))
    return bad


def judge_frames(wire, prov):
    """wire: [(t, id, data)]; prov: classify(..., keep_frames=True). Returns the
    per-arm result."""
    by_t = {}
    for t, i, d in wire:
        if i == CMD:
            by_t.setdefault(round(t, 6), []).append(d)
    vt = prov.get("vcm_times") or []
    vdata = [by_t.get(t, [None])[-1] for t in vt]
    faults, fronts_b0 = [], {}
    n = 0
    for t in prov.get("device_times") or []:
        cands = by_t.get(t) or []
        k = bisect.bisect_left(vt, t) - 1
        front = vdata[k] if k >= 0 else None
        if front is not None:
            fronts_b0["0x%02X" % front[0]] = fronts_b0.get("0x%02X" % front[0], 0) + 1
        # Two frames at one microsecond would be indistinguishable here; the
        # classifier's twin rule makes that arm UNRESOLVED already.
        d = cands[0] if cands else None
        n += 1
        if d is None:
            faults.append([t, "device frame not found on the wire at its time"])
            continue
        bad = frame_faults(d, front)
        if bad:
            faults.append([t, d.hex(), front.hex() if front else None, bad])
    discriminating = any(b != "0x08" for b in fronts_b0)
    if prov.get("unresolved"):
        verdict = "UNRESOLVED (provenance: %s)" % "; ".join(prov["unresolved"])
    elif n == 0:
        verdict = "NO DEVICE FRAMES"
    elif faults:
        verdict = "NOT AS SPECIFIED"
    else:
        verdict = "AS SPECIFIED"
    return {"device_frames": n, "faults": faults[:20], "faults_n": len(faults),
            "front_b0": fronts_b0, "b0_hold_discriminating": discriminating, "verdict": verdict}


def judge_run(run_path, arm="idle"):
    prov = P.rejudge(run_path, arm, keep_frames=True)
    d = json.load(open(run_path, encoding="utf-8"))
    a = d["arms"][arm]
    wit = a.get("witness_raw")
    wpath = os.path.join(os.path.dirname(run_path), os.path.basename(wit)) if wit else \
        run_path[:-5] + "_%s_witness.log.gz" % arm
    res = judge_frames(P.read_witness(wpath), prov)
    res["run"] = os.path.basename(run_path)
    return res


def main(paths):
    worst = 0
    for p in paths:
        r = judge_run(p)
        b0 = "B0 hold exercised" if r["b0_hold_discriminating"] else \
            "B0: every command in front was 0x08 -- consistent, not discriminating"
        print("%-60s %-18s device 0x051 %5d  faults %d  fronts %s  %s"
              % (r["run"][:60], r["verdict"], r["device_frames"], r["faults_n"], r["front_b0"], b0))
        for f in r["faults"][:3]:
            print("    %s" % (f,))
        worst = max(worst, 0 if r["verdict"] == "AS SPECIFIED" else 1)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
