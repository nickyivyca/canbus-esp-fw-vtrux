#!/usr/bin/env python3
"""B6 session 1 desk points (reviewer, 2026-10-20), from the records only. Gen-inhibit tester.

    py -3.14 -B gen-inhibit/tools/bench/b6_session1_desk.py SESSION_DIR RUNS_ROOT

Per arm of the session it builds one row per commanded pulse: the jammer's own PULSE line
(k, t_us), the witness error frames and the device's /gi_ecc isr rows assigned to it, and
the ticks row that pushed each isr row (pushed 1, bus_error_count equal to the row's seq).
Witness errors are assigned on the host clock (the jammer line's receipt time); isr rows
through the device uptime on the page samples. Then:

  phase 1  pulses missing on the witness or the device: the jammer's line, the predicted
           witness instant (fit of witness error time on jammer t_us), the frames around it
  phase 2  pulses whose bus_error delta is 0 or None, and the per-arm row counts
  phase 3  over-counts per build over every record with a witness file, and binomial odds
  phase 4  recording lag (tick t_us - isr t_us) against the gap from the error frame to the
           next frame the device received (witness clock; WiCAN's own ids excluded)
  phase 5  TEC, REC, status and events per isr row, by pulse category

Times printed in us are differences on one clock each: lag on the device clock, gaps on
the Kvaser's. Reads records only; no hardware.
"""
import bisect
import collections
import glob
import gzip
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b6_class_count as C          # noqa: E402
import b6_multicount_landing as L   # noqa: E402
import x2_jammer as XJ              # noqa: E402

# Ids the WiCAN transmits itself (vtrux-wican-diag.dbc): 0x7F0 probe, 0x7F1-0x7F3, 0x7F8-0x7F9.
WICAN_TX = {0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}
ASSIGN_TOL_S = 0.4
MARGIN_US = 1000.0
WINDOW_US = 1500.0
PULSE_RE = re.compile(r"JAMMER PULSE k=(\d+) t_us=(\d+)")


def jam_pulses(console, lo, hi):
    """[(k, t_us, host_t)] from the jammer's PULSE lines received in [lo, hi]."""
    out = []
    for t, line in console:
        m = PULSE_RE.search(line)
        if m and lo <= t <= hi:
            out.append((int(m.group(1)), int(m.group(2)), t))
    return out


def dev_host_offset(samples):
    """Device uptime -> host clock: median(host_t - uptime_ms / 1000) over the page samples."""
    d = [t - s["uptime_ms"] / 1000.0 for t, s in samples if s.get("uptime_ms") is not None]
    return statistics.median(d) if d else None


def assign(ts, pulses, tol=ASSIGN_TOL_S):
    """{k: [index into ts]}: each time to the pulse whose host time is nearest, within tol."""
    out = collections.defaultdict(list)
    for i, t in enumerate(ts):
        best = min(pulses, key=lambda p: abs(t - p[2]), default=None)
        if best is not None and abs(t - best[2]) <= tol:
            out[best[0]].append(i)
    return out


def linfit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    return a, b, [y - (a + b * x) for x, y in zip(xs, ys)]


def pair_ticks(isr, ticks):
    """{isr seq: the ticks row that pushed it}. A ticks row with pushed p > 0 and
    bus_error_count c pushed seqs c-p+1..c (p is 1 except over a multi-error pulse;
    session 2, 2026-10-20). Rows with pushed 0 push nothing."""
    by = {}
    for t in ticks:
        if t[2] >= 1:
            for q in range(t[1] - t[2] + 1, t[1] + 1):
                by.setdefault(q, t)
    return {r[1]: by.get(r[1]) for r in isr}


def pctl(xs, q):
    """Nearest-rank percentile of a list (q in 0-100)."""
    s = sorted(xs)
    if not s:
        return None
    i = max(0, min(len(s) - 1, -(-len(s) * q // 100) - 1))
    return s[int(i)]


def next_frame(frames, times, t, exclude=()):
    """First frame strictly after t whose id is not in exclude."""
    i = bisect.bisect_right(times, t)
    while i < len(frames):
        if frames[i][1] not in exclude:
            return frames[i]
        i += 1
    return None


def prev_frame(frames, times, t):
    i = bisect.bisect_left(times, t)
    return frames[i - 1] if i > 0 else None


def close_repeats(frames, gap_us=400.0, can_id=0x051):
    """Consecutive frames of can_id with the same data less than gap_us apart: [(first, second)]."""
    fr = [f for f in frames if f[1] == can_id]
    return [(a, b) for a, b in zip(fr, fr[1:]) if a[2] == b[2] and (b[0] - a[0]) * 1e6 < gap_us]


def build_of(rec):
    m = re.search(r"WiCAN (_ecc|[0-9a-f]{7})", rec.get("power") or "")
    return m.group(1) if m else None


def binom0(p, n):
    """Chance of 0 events in n trials at rate p."""
    return (1.0 - p) ** n


def witness_frames(rec, path):
    w = L.witness(rec, path)
    if w is None:
        return None, None
    fr = sorted(w["frames"], key=lambda f: f[0])
    return fr, [f[0] for f in fr]


def arm_table(rec, arm, frames, times):
    """One dict per commanded pulse of the arm, plus the arm's fits."""
    ph = arm["phases"]
    pulses = jam_pulses(rec.get("console") or [], ph["arm_posted"] - 1.0, ph["mode0_returned"] + 1.0)
    errs = sorted(arm.get("witness_error_times") or [])
    page = arm.get("ecc_page") or {}
    isr = page.get("isr") or []
    ticks = page.get("ticks") or []
    off = dev_host_offset(arm.get("samples") or [])
    wk = assign(errs, pulses)
    dk = assign([r[0] / 1e6 + off for r in isr], pulses) if off is not None else {}
    tick_of = pair_ticks(isr, ticks)
    latch = (arm.get("ecc") or {}).get("latch_t_us")
    rows = []
    for k, t_us, host in pulses:
        e = [errs[i] for i in wk.get(k, [])]
        d = [isr[i] for i in dk.get(k, [])]
        rows.append({"k": k, "jam_t_us": t_us, "host": host, "errs": e, "isr": d,
                     "ticks": [tick_of.get(r[1]) for r in d]})
    base = pulses[0][1] if pulses else 0
    one = [r for r in rows if len(r["errs"]) == 1]
    fit_j = linfit([(r["jam_t_us"] - base) / 1e6 for r in one], [r["errs"][0] for r in one]) if len(one) > 2 else None
    both = [r for r in rows if len(r["errs"]) == 1 and len(r["isr"]) == 1]
    fit_d = linfit([r["isr"][0][0] / 1e6 for r in both], [r["errs"][0] for r in both]) if len(both) > 2 else None
    for r in rows:
        if fit_j:
            r["pred"] = fit_j[0] + fit_j[1] * (r["jam_t_us"] - base) / 1e6
        if fit_d and len(r["isr"]) == 1:
            r["isr_w"] = fit_d[0] + fit_d[1] * r["isr"][0][0] / 1e6
        r["phase"] = None
        if latch is not None and r["ticks"] and r["ticks"][0]:
            r["phase"] = "pre-latch" if r["ticks"][0][0] <= latch else "post-latch"
        e = r["errs"][0] if len(r["errs"]) == 1 else r.get("isr_w") if not r["errs"] else None
        r["e_ref"] = e
        r["e_src"] = "witness" if len(r["errs"]) == 1 else "device isr on the witness clock" if e is not None else None
        if e is not None:
            r["next_any"] = next_frame(frames, times, e)
            r["next_rx"] = next_frame(frames, times, e, WICAN_TX)
            r["prev"] = prev_frame(frames, times, e)
            r["cls"] = C.in_class(e, r["next_any"]) if r["errs"] else None
    return rows, fit_j, fit_d, off


def us(x):
    return "%+.1f us" % (x * 1e6)


def fr_s(f, t):
    return "-" if f is None else "0x%03X %s %s" % (f[1], f[2], us(f[0] - t))


def session_arms(session):
    paths = sorted(p for p in glob.glob(os.path.join(session, "x2_jam_threshold_*[0-9].json")))
    for p in paths:
        rec = json.load(open(p))
        frames, times = witness_frames(rec, p)
        for k, arm in enumerate(rec["arms"]):
            rows, fj, fd, off = arm_table(rec, arm, frames, times)
            yield p, rec, k, arm, rows, fj, fd, frames, times


def phase1(arms):
    print("phase 1: pulses missing on the witness or on the device")
    deliv = []
    miss = []
    for p, rec, k, arm, rows, fj, fd, frames, times in arms:
        for r in rows:
            if len(r["errs"]) == 1:
                e = r["errs"][0]
                b, f = prev_frame(frames, times, e), next_frame(frames, times, e)
                deliv.append(((e - b[0]) * 1e6 if b else None, (f[0] - e) * 1e6 if f else None))
        n_cmd = rec["args"]["n"]
        ks = {r["k"] for r in rows}
        for kk in sorted(set(range(n_cmd)) - ks):
            print("  %s lead %s arm %d pulse %d: NO jammer PULSE line" % (os.path.basename(p), rec["args"]["lead_s"], k, kk + 1))
        for r in rows:
            if r["errs"] and r["isr"]:
                continue
            miss.append(r)
            print("  %s lead %s arm %d pulse %d: witness error frames %d, device isr rows %d"
                  % (os.path.basename(p), rec["args"]["lead_s"], k, r["k"] + 1, len(r["errs"]), len(r["isr"])))
            print("    jammer: PULSE k=%d t_us=%d (its interval from the previous line %s)"
                  % (r["k"], r["jam_t_us"], next(("%d us" % (r["jam_t_us"] - q["jam_t_us"]) for q in rows if q["k"] == r["k"] - 1), "-")))
            print("    jammer DONE: %s" % arm.get("done"))
            if fj:
                print("    witness fit on jammer t_us: slope-1 %.1f ppm, residuals %.1f to %.1f us over %d pulses"
                      % ((fj[1] - 1) * 1e6, min(fj[2]) * 1e6, max(fj[2]) * 1e6, len(fj[2])))
            if r.get("pred") is not None:
                t = r["pred"]
                print("    frames within %.0f us of the predicted witness instant:" % WINDOW_US)
                for f in frames[bisect.bisect_left(times, t - WINDOW_US / 1e6):bisect.bisect_right(times, t + WINDOW_US / 1e6)]:
                    print("      %s" % fr_s(f, t))
                print("    last frame before it %s; first after it %s" % (fr_s(prev_frame(frames, times, t), t),
                                                                          fr_s(next_frame(frames, times, t), t)))
            if r.get("isr_w") is not None:
                print("    device isr row %s on the witness clock: %s from the predicted instant (device fit residuals %.1f to %.1f us)"
                      % (r["isr"][0][:2], us(r["isr_w"] - r["pred"]) if r.get("pred") is not None else "-",
                         min(fd[2]) * 1e6, max(fd[2]) * 1e6))
            print("    pcan status changes in the arm: %s" % arm.get("pcan_status_changes"))
    seen = set()
    reps = []
    for p, rec, k, arm, rows, fj, fd, frames, times in arms:
        if p not in seen and frames:
            seen.add(p)
            reps += [(p, a, b) for a, b in close_repeats(frames)]
    print("  0x051 frames repeated with the same data less than 400 us apart, over the session's witness files: %d"
          % len(reps))
    for p, a, b in reps:
        m = min(miss, key=lambda r: abs(r.get("pred", 0) - a[0]), default=None)
        print("    %s: %s then again %.1f us later; nearest missing pulse's predicted instant %s"
              % (os.path.basename(p), a[2], (b[0] - a[0]) * 1e6,
                 "-" if m is None or m.get("pred") is None else "pulse %d, %s from the first" % (m["k"] + 1, us(m["pred"] - a[0]))))
    pg = sorted(x[0] for x in deliv if x[0] is not None)
    ng = sorted(x[1] for x in deliv if x[1] is not None)
    print("  delivered pulses %d: error frame minus the last frame before, us: min %.0f median %.0f max %.0f; "
          "next frame minus error frame, us: min %.0f median %.0f max %.0f"
          % (len(deliv), pg[0], pctl(pg, 50), pg[-1], ng[0], pctl(ng, 50), ng[-1]))
    return miss, deliv


def page_readings(arm, e):
    good = [(t, s["drv_bus_error"], s.get("req_start")) for t, s in arm.get("samples") or [] if s.get("drv_bus_error") is not None]
    before = [s for s in good if s[0] < e - 0.05]
    after = [s for s in good if s[0] > e + 0.05]
    return (before[-1] if before else None), (after[0] if after else None), good


def rd(s, e):
    if s is None:
        return None
    return "%+.3f s, req_start %s, %d" % (s[0] - e, "-" if s[2] is None else "%+.3f s" % (s[2] - e), s[1])


def phase2(arms):
    print("\nphase 2: bus_error delta 0 or None, and the per-arm row counts")
    tot = collections.Counter()
    for p, rec, k, arm, rows, fj, fd, frames, times in arms:
        errs = sorted(arm.get("witness_error_times") or [])
        be = [(s[0], s[1].get("drv_bus_error")) for s in arm.get("samples") or []]
        pp = XJ.per_pulse(errs, rec["args"]["n"], rec["args"]["interval_ms"] / 1000.0, be)
        deltas = pp["bus_error_delta_per_pulse"]
        cl = XJ.clusters(errs, rec["args"]["interval_ms"] / 2000.0)
        page = arm.get("ecc_page") or {}
        n_w = sum(1 for r in rows if r["errs"])
        n_d = len(page.get("isr") or [])
        n_t = sum(1 for t in page.get("ticks") or [] if t[2] == 1)
        tot["witness pulses"] += n_w
        tot["isr rows"] += n_d
        tot["pushed ticks"] += n_t
        tot["placed (delta not None)"] += sum(1 for d in deltas if d is not None)
        print("  %s arm %d: witness pulses %d, isr rows %d, pushed ticks %d, isr_total %s, deltas placed %d"
              % (os.path.basename(p), k, n_w, n_d, n_t, page.get("isr_total"), sum(1 for d in deltas if d is not None)))
        off = dev_host_offset(arm.get("samples") or [])
        for i, d in enumerate(deltas):
            if d is not None and d >= 1:
                continue
            e = cl[i][0]
            r = next((r for r in rows if r["errs"] and r["errs"][0] == e), None)
            b, a, good = page_readings(arm, e)
            print("    group %d delta %s: witness error at %.4f s into the arm; pulse %s; isr row %s; ticks row %s"
                  % (i + 1, d, e - arm["phases"]["pulses_start"], r["k"] + 1 if r else "?",
                     r["isr"] if r else "?", r["ticks"] if r else "?"))
            if r and r["ticks"] and r["ticks"][0] and off is not None:
                print("      ticks row on the host clock (uptime fit): %+.3f s from the witness error frame"
                      % (r["ticks"][0][0] / 1e6 + off - e))
            print("      page reading before (host t, req_start, bus_error): %s" % rd(b, e))
            print("      page reading after: %s" % rd(a, e))
            nxt = [s for s in good if s[0] > e + 0.05 and s[1] > (a[1] if a else -1)]
            if nxt:
                print("      first reading above that: %+.3f s, %d" % (nxt[0][0] - e, nxt[0][1]))
    print("  totals: %s" % dict(tot))


def phase3(root, session_build="_ecc"):
    print("\nphase 3: over-counts per build, every record with a witness file")
    paths = sorted({q for q in glob.glob(os.path.join(root, "**", "x2_jam_threshold_*.json"), recursive=True)
                    if "_witness" not in q})
    st = collections.defaultdict(collections.Counter)
    fold = collections.defaultdict(collections.Counter)
    for p in paths:
        rec = json.load(open(p))
        b = build_of(rec)
        rows = L.arm_rows(p)
        if rows and len(rows[0]) == 2:
            st["no witness file"]["records"] += 1
            continue
        key = b + (" VOID" if "VOID" in os.path.basename(p) else "")
        st[key]["records"] += 1
        for _p, _k, _i, d, e, _b, f, _tr in rows:
            if d is None or e is None:
                st[key]["unplaced"] += 1
                continue
            c = C.in_class(e, f)
            st[key]["placed"] += 1
            st[key]["over"] += d > 1
            st[key]["zero"] += d == 0
            st[key]["class"] += c
            st[key]["class over"] += c and d > 1
            fk = (key, "/".join(os.path.relpath(os.path.dirname(p), root).split(os.sep)[:2]))
            fold[fk]["placed"] += 1
            fold[fk]["class"] += c
    for key in sorted(st):
        print("  %-14s %s" % (key, dict(st[key])))
    print("  placed and class pulses per build and folder:")
    for (key, f) in sorted(fold):
        print("    %-14s %-50s %d %d" % (key, f, fold[(key, f)]["placed"], fold[(key, f)]["class"]))
    groups = collections.OrderedDict()
    for key in st:
        if key == "no witness file":
            continue
        groups.setdefault(key.split()[0], collections.Counter()).update(st[key])
    s1 = groups.get(session_build, collections.Counter())
    print("  by build (VOID records included):")
    for b, c in groups.items():
        print("    %-8s placed %d over %d class %d class-over %d" % (b, c["placed"], c["over"], c["class"], c["class over"]))
    earlier = collections.Counter()
    for b, c in groups.items():
        if b != session_build:
            earlier.update(c)
    groups["earlier pooled"] = earlier
    print("  chance of the session's %d over-counts in %d placed, and %d in %d class pulses, at each earlier rate:"
          % (s1["over"], s1["placed"], s1["class over"], s1["class"]))
    for b, c in groups.items():
        if b == session_build or not c["placed"]:
            continue
        p = c["over"] / c["placed"]
        pc = c["class over"] / c["class"] if c["class"] else None
        print("    %-14s rate %d/%d = %.5f -> P(0 in %d) = %.4f; class rate %s -> P(0 in %d) = %s"
              % (b, c["over"], c["placed"], p, s1["placed"], binom0(p, s1["placed"]),
                 "-" if pc is None else "%d/%d = %.4f" % (c["class over"], c["class"], pc), s1["class"],
                 "-" if pc is None else "%.4f" % binom0(pc, s1["class"])))
    return groups


def phase4(arms):
    print("\nphase 4: recording lag (ticks row t_us - isr row t_us) and the next received frame")
    lags, worse = [], []
    unpaired = 0
    for p, rec, k, arm, rows, fj, fd, frames, times in arms:
        for r in rows:
            for row, tk in zip(r["isr"], r["ticks"]):
                if tk is None:
                    unpaired += 1
                    print("  UNPAIRED %s arm %d pulse %d isr seq %d" % (os.path.basename(p), k, r["k"] + 1, row[1]))
                    continue
                lag = tk[0] - row[0]
                rx = r.get("next_rx")
                gap = (rx[0] - r["e_ref"]) * 1e6 if rx is not None and r["e_ref"] is not None else None
                anyf = r.get("next_any")
                gap_any = (anyf[0] - r["e_ref"]) * 1e6 if anyf is not None and r["e_ref"] is not None else None
                lags.append((lag, gap, gap_any, os.path.basename(p), k, r["k"] + 1, r["e_src"]))
                if gap is None or lag > gap + MARGIN_US:
                    worse.append(lags[-1])
    L_ = [x[0] for x in lags]
    print("  rows %d (unpaired %d)" % (len(lags), unpaired))
    print("  lag us: min %d median %.0f p95 %d p99 %d max %d" % (min(L_), statistics.median(L_), pctl(L_, 95), pctl(L_, 99), max(L_)))
    G = [x[1] for x in lags if x[1] is not None]
    print("  gap from the error frame to the next frame the device received (not a WiCAN id), us: "
          "min %.0f median %.0f p95 %.0f p99 %.0f max %.0f" % (min(G), statistics.median(G), pctl(G, 95), pctl(G, 99), max(G)))
    D = [x[0] - x[1] for x in lags if x[1] is not None]
    print("  lag minus that gap, us: min %.0f median %.0f p95 %.0f p99 %.0f max %.0f"
          % (min(D), statistics.median(D), pctl(D, 95), pctl(D, 99), max(D)))
    for m in (0, 250, 500, 1000, 2000):
        print("    rows with lag > gap + %d us: %d" % (m, sum(1 for d in D if d > m)))
    print("  rows with lag > gap + %.0f us (the stated margin), or no gap: %d" % (MARGIN_US, len(worse)))
    for x in worse:
        print("    %s arm %d pulse %d: lag %d us, gap %s us (any id %s us), error time from %s"
              % (x[3], x[4], x[5], x[0], "-" if x[1] is None else "%.0f" % x[1], "-" if x[2] is None else "%.0f" % x[2], x[6]))
    big = sorted(lags, key=lambda x: -x[0])[:8]
    print("  the 8 largest lags:")
    for x in big:
        print("    %s arm %d pulse %d: lag %d us, gap %s us (any id %s us)"
              % (x[3], x[4], x[5], x[0], "-" if x[1] is None else "%.0f" % x[1], "-" if x[2] is None else "%.0f" % x[2]))
    return lags, worse


def category(r):
    if not r["errs"]:
        return "witness miss"
    return "%s%s" % (r["phase"] or "phase unknown", ", class" if r.get("cls") else "")


def phase5(arms):
    print("\nphase 5: TEC, REC, status, events per isr row, by pulse category")
    by = collections.defaultdict(collections.Counter)
    by_next = collections.defaultdict(collections.Counter)
    allv = collections.Counter()
    rows_ = []
    for p, rec, k, arm, rows, fj, fd, frames, times in arms:
        for r in rows:
            for row in r["isr"]:
                v = tuple(row[6:10])
                by[category(r)][v] += 1
                allv[v] += 1
                nx = r.get("next_any")
                g = "-" if nx is None or r["e_ref"] is None else "0x%03X %.0f us" % (nx[1], (nx[0] - r["e_ref"]) * 1e6)
                near = nx is not None and r["e_ref"] is not None and                     C.LO_US <= round((nx[0] - r["e_ref"]) * 1e6, 3) <= C.HI_US
                by_next["next frame of any id 200-320 us after" if near else "next frame later, or none"][v] += 1
                rows_.append((os.path.basename(p), rec["args"]["lead_s"], k, r["k"] + 1, category(r), v, g))
    print("  (tec, rec, status, events) over all rows: %s" % allv.most_common())
    for c in sorted(by):
        print("  %-26s %s" % (c, by[c].most_common()))
    for c in sorted(by_next):
        print("  %-42s %s" % (c, by_next[c].most_common()))
    common = allv.most_common(1)[0][0] if allv else None
    print("  rows not %s:" % (common,))
    for x in rows_:
        if x[5] != common:
            print("    %s lead %s arm %d pulse %d: %s %s, next frame after the error %s" % x)
    return by, by_next


def main(argv):
    session, root = argv[0], argv[1]
    arms = list(session_arms(session))
    phase1(arms)
    phase2(arms)
    phase3(root)
    phase4(arms)
    phase5(arms)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
