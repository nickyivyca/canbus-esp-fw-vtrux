#!/usr/bin/env python3
"""Test b6_session1_desk.py on synthetic records.

    py -3.14 -B gen-inhibit/tools/bench/test_b6_session1_desk.py [--mutants]

Gen-inhibit tester, 2026-10-20.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAILS = []


def check(name, fn, detail=""):
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %s" % (str(detail)[:200],)))
    if not ok:
        FAILS.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def quiet(fn, *a):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = fn(*a)
    return r, out.getvalue()


def unit_checks(S):
    con = [(10.0, "JAMMER ACK PULSES n=2"), (10.05, "JAMMER PULSE k=0 t_us=100 rel_us=1"),
           (11.05, "JAMMER PULSE k=1 t_us=1000100 rel_us=1"), (30.0, "JAMMER PULSE k=0 t_us=5 rel_us=1")]
    check("jam_pulses: k and t_us parsed, a line outside the window dropped",
          lambda: S.jam_pulses(con, 9.0, 12.0) == [(0, 100, 10.05), (1, 1000100, 11.05)])
    check("dev_host_offset: median of host - uptime",
          lambda: abs(S.dev_host_offset([(100.0, {"uptime_ms": 0}), (101.0, {"uptime_ms": 1000}),
                                         (102.5, {"uptime_ms": 2000})]) - 100.0) < 1e-9)
    pulses = [(0, 0, 10.0), (1, 0, 11.0)]
    a = S.assign([10.1, 10.2, 10.9, 12.0], pulses)
    check("assign: nearest pulse within tol; two times to one pulse kept; outside tol dropped",
          lambda: dict(a) == {0: [0, 1], 1: [2]}, dict(a))
    fa, fb, res = S.linfit([0.0, 1.0, 2.0], [5.0, 7.0, 9.0])
    check("linfit: exact line", lambda: abs(fa - 5) < 1e-9 and abs(fb - 2) < 1e-9 and max(map(abs, res)) < 1e-9)
    isr = [[100, 1], [200, 2], [300, 3]]
    ticks = [[90, 1, 0, 0, 1, 0], [150, 1, 1, 1, 0, 0], [260, 2, 1, 2, 0, 0]]
    pt = S.pair_ticks(isr, ticks)
    check("pair_ticks: pushed rows only, matched on bus_error_count; none for seq 3",
          lambda: pt == {1: [150, 1, 1, 1, 0, 0], 2: [260, 2, 1, 2, 0, 0], 3: None}, pt)
    isr5 = [[100, 1], [200, 2], [210, 3], [220, 4], [300, 5]]
    tk5 = [[150, 1, 1, 1, 0, 0], [250, 4, 3, 4, 0, 0], [350, 5, 1, 5, 0, 0]]
    pt5 = S.pair_ticks(isr5, tk5)
    check("pair_ticks: a ticks row that pushed 3 covers seqs 2-4",
          lambda: [(pt5.get(q) or [None])[0] for q in (1, 2, 3, 4, 5)] == [150, 250, 250, 250, 350], pt5)
    r100 = list(range(1, 101))
    check("pctl: nearest rank (p50, p95, p99 of 1..100; 1 and 3 values)",
          lambda: (S.pctl(r100, 50), S.pctl(r100, 95), S.pctl(r100, 99), S.pctl([5], 99), S.pctl([1, 2, 3], 50))
          == (50, 95, 99, 5, 2))
    fr = [(1.0, 0x051, "a"), (2.0, 0x7F1, "b"), (3.0, 0x051, "c")]
    tm = [f[0] for f in fr]
    check("next_frame: strictly after; WiCAN ids excluded when asked",
          lambda: S.next_frame(fr, tm, 1.0) == fr[1] and S.next_frame(fr, tm, 1.0, S.WICAN_TX) == fr[2]
          and S.next_frame(fr, tm, 3.0) is None)
    check("prev_frame: strictly before", lambda: S.prev_frame(fr, tm, 2.0) == fr[0] and S.prev_frame(fr, tm, 1.0) is None)
    rf = [(1.0, 0x051, "aa"), (1.0002, 0x051, "aa"), (2.0, 0x051, "bb"), (2.0005, 0x051, "bb"),
          (3.0, 0x051, "cc"), (3.0001, 0x051, "dd"), (4.0, 0x7F1, "ee"), (4.0001, 0x7F1, "ee")]
    check("close_repeats: same data under 400 us only, 0x051 only",
          lambda: S.close_repeats(rf) == [(rf[0], rf[1])], S.close_repeats(rf))
    check("build_of: _ecc, a commit, none",
          lambda: S.build_of({"power": "12 V; WiCAN _ecc 637e1af1"}) == "_ecc"
          and S.build_of({"power": "WiCAN e3d9437 _man"}) == "e3d9437" and S.build_of({"power": "USB only"}) is None)
    check("binom0: (1-p)^n", lambda: abs(S.binom0(0.1, 2) - 0.81) < 1e-12)
    e = 50.0
    arm = {"samples": [(e - 0.2, {"drv_bus_error": 4, "req_start": e - 0.25}), (e - 0.03, {"drv_bus_error": 5, "req_start": None}),
                       (e + 0.03, {"drv_bus_error": 5, "req_start": None}), (e + 0.2, {"drv_bus_error": 6, "req_start": e + 0.1})]}
    b, af, _ = S.page_readings(arm, e)
    check("page_readings: readings within 50 ms of the error are not used",
          lambda: b[1] == 4 and af[1] == 6, (b, af))


def synth():
    """One arm of 5 pulses: pulse 3 missing on the witness, pulse 4 missing on the device."""
    H = 1000.0
    con = [(H + k + 0.05, "JAMMER PULSE k=%d t_us=%d rel_us=1" % (k, 1000000 + k * 1000000)) for k in range(5)]
    errs = [H + k for k in (0, 1, 3, 4)]
    dev = [5000000 + k * 1000000 for k in (0, 1, 2, 4)]
    vals = {0: (8, 0, 32, 128)}
    isr = [[t, i + 1, 0, "bit", 0, 0] + list(vals.get(k, (0, 1, 44, 128))) for i, (k, t) in enumerate(zip((0, 1, 2, 4), dev))]
    lag = {0: 1500, 1: 2500, 2: 500, 4: 3000}
    ticks = [[dev[0] - 50, 1, 0, 0, 0, 0]]
    ticks += [[t + lag[k], i + 1, 1, i + 1, 0, 0] for i, (k, t) in enumerate(zip((0, 1, 2, 4), dev))]
    latch = ticks[2][0]                                   # the ticks row of pulse 2 latched
    samples = [(995.0 + u / 1000.0, {"uptime_ms": u, "drv_bus_error": 0}) for u in range(0, 12000, 100)]
    frames = []
    frames += [(H + 0 + 260e-6, 0x051, "00")]                                  # pulse 1: class
    frames += [(H + 1 + 100e-6, 0x7F1, "00"), (H + 1 + 2000e-6, 0x051, "01")]  # pulse 2: WiCAN id first
    frames += [(H + 2 + 220e-6, 0x051, "02")]                                  # pulse 3 (witness miss)
    frames += [(H + 3 + 5000e-6, 0x051, "03")]
    frames += [(H + 4 + 5000e-6, 0x051, "04")]
    frames.sort()
    arm = {"phases": {"arm_posted": H - 1, "mode0_returned": H + 6, "pulses_start": H}, "witness_error_times": errs,
           "ecc_page": {"isr": isr, "ticks": ticks, "isr_total": 4}, "ecc": {"latch_t_us": latch}, "samples": samples}
    rec = {"console": con, "args": {"n": 5, "lead_s": 1.5, "interval_ms": 1000}, "arms": [arm]}
    return rec, arm, frames


def arm_checks(S):
    rec, arm, frames = synth()
    times = [f[0] for f in frames]
    rows, fj, fd, off = S.arm_table(rec, arm, frames, times)
    by = {r["k"]: r for r in rows}
    check("arm_table: one row per jammer line; pulse 3 has no witness error, pulse 4 no isr row",
          lambda: len(rows) == 5 and not by[2]["errs"] and len(by[2]["isr"]) == 1 and not by[3]["isr"]
          and len(by[3]["errs"]) == 1, [(r["k"], len(r["errs"]), len(r["isr"])) for r in rows])
    check("arm_table: the missing pulse's instant predicted from the jammer, and its isr row put on the witness clock",
          lambda: abs(by[2]["pred"] - 1002.0) < 1e-6 and abs(by[2]["isr_w"] - 1002.0) < 1e-6, by[2])
    check("arm_table: the witness-miss row takes its error time from the device",
          lambda: by[2]["e_ref"] is not None and abs(by[2]["e_ref"] - 1002.0) < 1e-6
          and by[2]["e_src"].startswith("device"), by[2].get("e_src"))
    check("arm_table: the latching row is pre-latch, later rows post-latch",
          lambda: [by[k]["phase"] for k in (0, 1, 2, 4)] == ["pre-latch", "pre-latch", "post-latch", "post-latch"],
          [by[k]["phase"] for k in (0, 1, 2, 4)])
    check("arm_table: next received frame skips the WiCAN's own id; class from the next frame of any id",
          lambda: by[1]["next_rx"][1] == 0x051 and by[1]["next_any"][1] == 0x7F1 and by[0]["cls"] and not by[1]["cls"])
    arms = [("synth.json", rec, 0, arm, rows, fj, fd, frames, times)]
    (lags, worse), out4 = quiet(S.phase4, arms)
    check("phase4: lag is ticks minus isr", lambda: sorted(x[0] for x in lags) == [500, 1500, 2500, 3000], out4)
    check("phase4: only the lag past gap + margin is flagged (pulse 1: 1500 > 260 + 1000)",
          lambda: [(x[5], x[0]) for x in worse] == [(1, 1500)], out4)
    check("phase4: percentiles printed", lambda: "lag us: min 500 median 2000 p95 3000 p99 3000 max 3000" in out4, out4)
    (cats, by_next), out5 = quiet(S.phase5, arms)
    check("phase5: rows by category",
          lambda: dict(cats["pre-latch, class"]) == {(8, 0, 32, 128): 1} and dict(cats["witness miss"]) == {(0, 1, 44, 128): 1}
          and dict(cats["pre-latch"]) == {(0, 1, 44, 128): 1} and dict(cats["post-latch"]) == {(0, 1, 44, 128): 1},
          {k: dict(v) for k, v in cats.items()})
    check("phase5: rows by next frame 200-320 us (any id)",
          lambda: sum(by_next["next frame of any id 200-320 us after"].values()) == 2, {k: dict(v) for k, v in by_next.items()})
    _, out1 = quiet(S.phase1, arms)
    check("phase1: names the witness miss and the device miss, and the frame after the predicted instant",
          lambda: "arm 0 pulse 3: witness error frames 0, device isr rows 1" in out1
          and "arm 0 pulse 4: witness error frames 1, device isr rows 0" in out1 and "0x051 02 +220.0 us" in out1, out1)


def phase3_checks(S):
    tmp = tempfile.mkdtemp(prefix="gi-b6-desk-p3-", dir=gi_paths.scratch("tmp", run=False))
    recs = {"a/x2_jam_threshold_20261016T000001.json": "WiCAN e3d9437 _man",
            "b/x2_jam_threshold_20261016T000002_VOID_noreset.json": "WiCAN c65c45e _man",
            "c/x2_jam_threshold_20261020T000003.json": "12 V; WiCAN _ecc 637e1af1",
            "d/x2_jam_threshold_20261008T000004.json": "USB only (PC)"}
    E = 10.0
    cls = (E + 260e-6, 0x051, "00")
    oth = (E + 5e-3, 0x051, "00")
    rows = {"x2_jam_threshold_20261016T000001.json": [(1, cls), (11, cls), (1, oth), (None, oth), (0, oth)],
            "x2_jam_threshold_20261016T000002_VOID_noreset.json": [(10, oth), (1, oth)],
            "x2_jam_threshold_20261020T000003.json": [(1, cls), (1, oth), (1, oth)]}
    try:
        for rel, power in recs.items():
            os.makedirs(os.path.join(tmp, os.path.dirname(rel)), exist_ok=True)
            json.dump({"power": power}, open(os.path.join(tmp, rel), "w"))
        real = S.L.arm_rows

        def fake(p):
            b = os.path.basename(p)
            if b not in rows:
                return [("no witness file", p)]
            return [(p, 0, i, d, E, None, f, None) for i, (d, f) in enumerate(rows[b])]
        S.L.arm_rows = fake
        try:
            _, out = quiet(S.phase3, tmp)
        finally:
            S.L.arm_rows = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("phase3: per build placed, over-counts (delta > 1), class and class over-counts",
          lambda: "e3d9437  placed 4 over 1 class 2 class-over 1" in out and "c65c45e  placed 2 over 1 class 0 class-over 0" in out
          and "_ecc     placed 3 over 0 class 1 class-over 0" in out, out)
    check("phase3: VOID records listed apart, unplaced and no-witness records counted",
          lambda: "c65c45e VOID" in out and "'unplaced': 1" in out and "no witness file {'records': 1}" in out, out)
    check("phase3: binomial at each earlier rate, and pooled",
          lambda: "rate 1/4 = 0.25000 -> P(0 in 3) = 0.4219; class rate 1/2 = 0.5000 -> P(0 in 1) = 0.5000" in out
          and "earlier pooled rate 2/6 = 0.33333 -> P(0 in 3) = 0.2963" in out, out)


MUTANTS = [
    ("assign tolerance ignored", "if best is not None and abs(t - best[2]) <= tol:", "if best is not None:"),
    # Not "takes unpushed rows": a pushed-0 row covers an empty range either way (equivalent).
    ("pair_ticks drops single pushes", "        if t[2] >= 1:\n", "        if t[2] >= 2:\n"),
    ("pair_ticks ignores pushed > 1", "range(t[1] - t[2] + 1, t[1] + 1)", "range(t[1], t[1] + 1)"),
    ("pctl off by one", "-(-len(s) * q // 100) - 1", "-(-len(s) * q // 100)"),
    ("next_frame not strict", "i = bisect.bisect_right(times, t)", "i = bisect.bisect_left(times, t)"),
    ("next_frame exclude ignored", "if frames[i][1] not in exclude:", "if True:"),
    ("close_repeats ignores the data", "a[2] == b[2] and ", ""),
    ("build_of misses _ecc", r'"WiCAN (_ecc|[0-9a-f]{7})"', r'"WiCAN ([0-9a-f]{7})"'),
    ("over-count is delta >= 1", 'st[key]["over"] += d > 1', 'st[key]["over"] += d >= 1'),
    ("VOID not listed apart", '(" VOID" if "VOID" in os.path.basename(p) else "")', '""'),
    ("class over-count is any class pulse", 'st[key]["class over"] += c and d > 1', 'st[key]["class over"] += c'),
    ("binomial wrong", "return (1.0 - p) ** n", "return 1.0 - p * n"),
    ("margin dropped", "lag > gap + MARGIN_US", "lag > gap"),
    ("lag sign", "lag = tk[0] - row[0]", "lag = row[0] - tk[0]"),
    ("latching row post-latch", '"pre-latch" if r["ticks"][0][0] <= latch', '"pre-latch" if r["ticks"][0][0] < latch'),
    ("witness-miss row without a device time", 'else r.get("isr_w") if not r["errs"] else None', "else None"),
    ("page slack dropped", "before = [s for s in good if s[0] < e - 0.05]", "before = [s for s in good if s[0] < e]"),
]


def checks_from(folder):
    S = load(os.path.join(folder, "b6_session1_desk.py"), "desk_under_test")
    for group in (unit_checks, arm_checks, phase3_checks):
        try:
            group(S)
        except Exception as e:      # noqa: BLE001
            # A crash outside a check is a failed group, never a kill on its own.
            check("%s ran" % group.__name__, lambda: False, "CRASH %s: %s" % (type(e).__name__, e))


def main():
    if "--mutants" not in sys.argv[1:]:
        checks_from(HERE)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = errors = 0
    f = "b6_session1_desk.py"
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        tmp = tempfile.mkdtemp(prefix="gi-b6-desk-m%d-" % i, dir=gi_paths.scratch("tmp", run=False))
        try:
            for g in (f, "b6_class_count.py", "b6_multicount_landing.py", "gi_ecc.py", "x2_jammer.py"):
                shutil.copy(os.path.join(HERE, g), tmp)
            open(os.path.join(tmp, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--folder", tmp],
                               capture_output=True, text=True, timeout=120)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        real = [l for l in fails if "-- CRASH " not in l]
        dead = bool(real) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (%d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", i, name, len(fails),
                                            "" if len(real) == len(fails) else ", %d crashed" % (len(fails) - len(real)),
                                            ("; " + real[0][6:90]) if real else ""))
    print("%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--folder"]:
        checks_from(sys.argv[2])
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
