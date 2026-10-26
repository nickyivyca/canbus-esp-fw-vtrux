#!/usr/bin/env python3
"""B6 multi-count pulses: where each jammer pulse landed relative to the frames on the
wire, for the reviewer's request of 2026-10-20 (a). Gen-inhibit tester.

    py -3.14 -B gen-inhibit/tools/bench/b6_multicount_landing.py [ROOT ...]

Scans x2_jam_threshold_*.json under each ROOT (default: this folder's runs/), with the
witness file named in each record. Per arm, the device's bus_error delta per pulse comes
from x2_jammer.per_pulse (the judge's own grouping). Per pulse it prints the witness
error-frame time and the frames either side of it on the Kvaser (hardware clock, the
same clock as the error time): id, data length, and the gap to the error frame in us.
A pulse the device counted >= MULTI times is marked. Phase 1 prints every pulse; phase 2
a summary of the multi-count ones against the rest. Reads records only; no hardware.
"""
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x2_jammer as XJ  # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

MULTI = 5
BIT_US = 2.0          # 500 kbit/s


def frame_bits(dlc):
    # Standard 11-bit data frame without stuff bits: SOF..EOF 44 + 8*dlc, then 3 IFS.
    # Stuffing adds up to (34 + 8*dlc - 1) // 4 more; both bounds are printed.
    base = 44 + 8 * dlc
    return base, base + (34 + 8 * dlc - 1) // 4


def witness(rec, path):
    wf = rec.get("witness_file")
    cand = [os.path.join(os.path.dirname(path), os.path.basename(wf))] if wf else []
    cand.append(path.replace(".json", "_witness.json.gz").replace("_VOID_noreset_witness", "_witness"))
    for c in cand:
        if os.path.exists(c):
            return json.load(gzip.open(c))
    return None


def near(frames, t):
    """The last frame strictly before t and the first strictly after it. A frame stamped
    at t itself is neither (reviewer, 2026-10-21: "the next frame after the error frame"
    means strictly after; 7 pulses on record have a frame at the error frame's stamp)."""
    before = after = None
    for f in frames:
        if f[0] < t:
            before = f
        elif f[0] > t:
            after = f
            break
    return before, after


def arm_rows(path):
    rec = json.load(open(path))
    w = witness(rec, path)
    if w is None:
        return [("no witness file", path)]
    frames = sorted(w["frames"], key=lambda f: f[0])
    a_ = rec["args"]
    rows = []
    for k, arm in enumerate(rec["arms"]):
        errs = sorted(arm.get("witness_error_times") or [])
        samples = arm.get("samples") or []
        be = [(s[0], s[1].get("drv_bus_error")) for s in samples]
        iv = a_["interval_ms"] / 1000.0
        pp = XJ.per_pulse(errs, a_["n"], iv, be)
        deltas = pp.get("bus_error_delta_per_pulse") or []
        cl = XJ.clusters(errs, iv / 2.0)
        for i, d in enumerate(deltas):
            e = cl[i][0] if i < len(cl) else None
            if e is None:
                rows.append((path, k, i, d, None, None, None, None))
                continue
            b, f = near(frames, e)
            tec = [s[1].get("drv_tec") for s in samples if e - 0.2 <= s[0] <= e + 1.2 and s[1].get("drv_tec") is not None]
            rec_ = [s[1].get("drv_rec") for s in samples if e - 0.2 <= s[0] <= e + 1.2 and s[1].get("drv_rec") is not None]
            rows.append((path, k, i, d, e, b, f, (max(tec) if tec else None, max(rec_) if rec_ else None)))
    return rows


def fmt(f, e):
    if f is None:
        return "-"
    dlc = len(f[2]) // 2
    lo, hi = frame_bits(dlc)
    return "0x%03X dlc%d %+8.0f us (frame %d-%d us)" % (f[1], dlc, (f[0] - e) * 1e6, lo * BIT_US, hi * BIT_US)


def main():
    roots = sys.argv[1:] or [gi_paths.runs()]
    paths = sorted({p for r in roots for p in glob.glob(os.path.join(r, "**", "x2_jam_threshold_*.json"), recursive=True)
                    if "_witness" not in p})
    print("phase 1: %d threshold records" % len(paths))
    multi, single = [], []
    for p in paths:
        for row in arm_rows(p):
            if len(row) == 2:
                print("  %s: %s" % (row[0], os.path.relpath(row[1], HERE)))
                continue
            path, k, i, d, e, b, f, tr = row
            if d is None or e is None:
                continue
            (multi if d >= MULTI else single).append(row)
            if d >= MULTI:
                print("  MULTI %s arm %d pulse %d: delta %d | before %s | after %s | tec/rec max %s"
                      % (os.path.relpath(path, HERE), k, i + 1, d, fmt(b, e), fmt(f, e), tr))
    print("\nphase 2: %d pulses counted >= %d, %d others" % (len(multi), MULTI, len(single)))
    for name, rows in (("multi", multi), ("others", single)):
        gaps = sorted((e - b[0]) * 1e6 for (_, _, _, _, e, b, _, _) in rows if b)
        nxt = sorted((f[0] - e) * 1e6 for (_, _, _, _, e, _, f, _) in rows if f)
        if gaps:
            print("  %-6s prev-frame-to-error us: min %.0f median %.0f max %.0f" % (name, gaps[0], gaps[len(gaps) // 2], gaps[-1]))
        if nxt:
            print("  %-6s error-to-next-frame us: min %.0f median %.0f max %.0f" % (name, nxt[0], nxt[len(nxt) // 2], nxt[-1]))


if __name__ == "__main__":
    main()
