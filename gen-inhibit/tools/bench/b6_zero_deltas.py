#!/usr/bin/env python3
"""B6 per-pulse bus_error deltas of 0, under the old poll stamp and the new one. Records only.

    py -3.14 -B gen-inhibit/tools/bench/b6_zero_deltas.py ROOT [ROOT ...]

Gen-inhibit tester, 2026-11-05, for the gen-inhibit reviewer: re-judge every earlier B6
landing figure after the poll-stamp fix (x2_jammer.per_pulse, 86dcfee6). A delta of 0 was
read as an error frame the device did not count; step 1d's two were stalled polls instead.

For every x2_jam_threshold_*.json under the roots that has its witness file (the records
b6_multicount_landing.py places), every arm, the device's bus_error delta per witness pulse
from x2_jammer.per_pulse twice, as b6_multicount_landing.py feeds it (every page sample):
  new  with each reading's req_start: "after" only if sent after the pulse's error frame,
       "before" only if returned before it (the rule from 86dcfee6);
  old  each reading taken as sent when it returned (the rule before).
Then one row for every pulse whose delta is 0 under either rule: both deltas, and the page
readings either side of the error frame under the new rule, each with its return and send
times and when the page was built, from the page's uptime_ms (the arm's median offset of
uptime against the return time, over readings answered within 30 ms, so "built" can be late
by up to that answer time), all in seconds from the error frame on the host clock. A reading built after the error frame that already holds
the count shows the pulse counted.

  artifact  old 0, new not 0: the old delta came from a poll stamped by its return
  stands    new 0: the readings either side bracket the error frame and the count did not move

Summary per root: placed pulses (new delta not None), and deltas of 0 under each rule.
"""
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x2_jammer as XJ   # noqa: E402

FAST_S = 0.03            # readings answered within this go into the uptime offset


def records(roots):
    out = set()
    for r in roots:
        for p in glob.glob(os.path.join(r, "**", "x2_jam_threshold_*.json"), recursive=True):
            if "_witness" not in p and os.path.exists(p[:-len(".json")] + "_witness.json.gz"):
                out.add(p)
    return sorted(out)


def uptime_offset(samples):
    """Median of uptime_s - returned over quickly answered readings, or None."""
    offs = []
    for t, s in samples:
        if s and s.get("uptime_ms") is not None and s.get("req_start") is not None and t - s["req_start"] <= FAST_S:
            offs.append(s["uptime_ms"] / 1000.0 - t)
    return statistics.median(offs) if offs else None


def reading(s, e, off):
    """'v N, returned +a, sent +b, built +c' relative to e."""
    if s is None:
        return "-"
    t, x = s
    sent = x.get("req_start")
    built = x["uptime_ms"] / 1000.0 - off if off is not None and x.get("uptime_ms") is not None else None
    f = lambda v: "?" if v is None else "%+.3f" % (v - e)   # noqa: E731
    return "%s, returned %s, sent %s, built %s" % (x.get("drv_bus_error"), f(t), f(sent), f(built))


def arm_rows(path):
    """(arm, pulse, err_time, new, old, before, after, offset) for each delta 0 under either rule,
    and the arm's placed count (new not None) and zero counts."""
    rec = json.load(open(path))
    n, iv = rec["args"]["n"], rec["args"]["interval_ms"] / 1000.0
    rows, stats = [], {"placed": 0, "zero_new": 0, "zero_old": 0}
    for k, arm in enumerate(rec.get("arms") or []):
        errs = sorted(arm.get("witness_error_times") or [])
        samples = [(s[0], s[1]) for s in arm.get("samples") or [] if s[1] is not None]
        new = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error"), x.get("req_start")) for t, x in samples])
        old = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error")) for t, x in samples])
        dn, do = new["bus_error_delta_per_pulse"], old["bus_error_delta_per_pulse"]
        cl = XJ.clusters(errs, iv / 2.0)
        off = uptime_offset(samples)
        stats["placed"] += sum(1 for d in dn if d is not None)
        stats["zero_new"] += sum(1 for d in dn if d == 0)
        stats["zero_old"] += sum(1 for d in do if d == 0)
        for i, (a, b) in enumerate(zip(dn, do)):
            if a != 0 and b != 0:
                continue
            e0, e1 = cl[i][0], cl[i][-1]
            good = [s for s in samples if s[1].get("drv_bus_error") is not None]
            bef = [s for s in good if s[0] < e0 - 0.05]
            aft = sorted((s for s in good if (s[1].get("req_start") or s[0]) > e1 + 0.05),
                         key=lambda s: s[1].get("req_start") or s[0])
            rows.append((k, i + 1, e0, a, b, bef[-1] if bef else None, aft[0] if aft else None, off))
    return rows, stats


def main(argv):
    roots = argv or []
    if not roots:
        print("usage: b6_zero_deltas.py ROOT [ROOT ...]")
        return 2
    total = {"placed": 0, "zero_new": 0, "zero_old": 0}
    for root in roots:
        paths = records([root])
        tot = {"placed": 0, "zero_new": 0, "zero_old": 0}
        print("root %s: %d threshold records with a witness file" % (os.path.basename(os.path.normpath(root)), len(paths)))
        for p in paths:
            rows, st = arm_rows(p)
            for key in tot:
                tot[key] += st[key]
            for k, i, e, a, b, bef, aft, off in rows:
                verdict = "stands" if a == 0 else "artifact"
                print("  %s %s arm %d pulse %d: new %s, old %s" % (verdict, os.path.relpath(p, root), k, i, a, b))
                print("    before: %s" % reading(bef, e, off))
                print("    after:  %s" % reading(aft, e, off))
        print("  placed %d; delta 0: new %d, old %d" % (tot["placed"], tot["zero_new"], tot["zero_old"]))
        for key in total:
            total[key] += tot[key]
    print("all roots: placed %d; delta 0: new %d, old %d" % (total["placed"], total["zero_new"], total["zero_old"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
