#!/usr/bin/env python3
"""Test x2_jam_cases.py's B6-investigation additions (2026-10-20): judged_errs,
threshold_refusal, identity (from /gen_inhibit, never /check_status), ecc_entry, and
case_threshold run end to end with stubs for the bench (arm_live, the reset, the
witness, /gi_ecc). Synthetic, no hardware.

    py -3.14 -B gen-inhibit/tools/bench/test_x2_jam_cases_b6ecc.py
    py -3.14 -B gen-inhibit/tools/bench/test_x2_jam_cases_b6ecc.py --mutants

The reviewer's plan: 29 pulses at 1000 ms in one PULSES command (the jammer's limit
is n x interval + 100 ms <= 30 s), trip 6 judged on the first 10 as B6 does, every
pulse recorded, /gi_ecc read after each arm with a reset before each. Gen-inhibit tester.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_gi_ecc as TG                                        # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

T6 = "error-frame rate exceeded"
FAILS = []
SHA = "ab" * 32


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def load(path):
    spec = importlib.util.spec_from_file_location("x2_jam_cases_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ns(**k):
    d = dict(n=29, judge_first=10, ecc=True, reset_wican=True, interval_ms=1000, lead_s=5.0,
             w=20, arms=1, want_sha=None)
    d.update(k)
    return argparse.Namespace(**d)


def wit_errs(n, per=None, start=110.001):
    out = []
    for k in range(n):
        out += [start + k + j * 0.0003 for j in range((per or {}).get(k + 1, 1))]
    return out


def page_samples(n, latch_at, end):
    out, t, tx = [], 100.5, 100
    while t < end:
        k = sum(1 for i in range(n) if 110.001 + i < t)
        lat = latch_at is not None and t >= latch_at
        tx += 10
        out.append((round(t, 3), {"mode": 3, "inhibit_live": not lat, "abort_latched": lat,
                                  "abort_reason": T6 if lat else "", "tx_ok": tx, "drv_bus_error": k}))
        t += 0.1
    return out


class FakeWit:
    def __init__(self, errs):
        self.errs = errs

    def clocked(self):
        return [], list(self.errs), {"stub": True}


def run_case(C, a, errs, latch_at, ecc_page, get_raises=False):
    """case_threshold with the bench stubbed; returns (rec, arm, gets)."""
    end = 110.0 + a.n + 12.0
    ph = {"replay_start": 95.0, "arm_posted": 100.0, "pulses_start": 110.0,
          "pulses_end": 110.0 + a.n, "mode0_posted": end}
    gets = []

    def get(path):
        gets.append(path)
        if get_raises:
            raise OSError("timed out")
        if isinstance(ecc_page, list):          # one page per arm
            return json.dumps(ecc_page[len(gets) - 1])
        return json.dumps(ecc_page)
    C.reset_wican_to_off = lambda: {"ok": True, "why": "stub"}
    C.arm_live = lambda a_, con, wit, k, body: {"n": k, "phases": dict(ph), "samples": page_samples(a.n, latch_at, end)}
    C.replay_rate_device_off = lambda frames, ph_: 100.0
    C.LF.dev_get = get
    C.time.sleep = lambda s: None
    rec = {}
    C.case_threshold(a, rec, None, FakeWit(errs))
    return rec, rec["arms"][0], gets


class FakeCC:
    made = 0

    def __init__(self):
        FakeCC.made += 1
        self.started = self.stopped = self.joined = False
        self.events, self.lines = [(1.0, "open")], [(1.0, "x"), (2.0, "y")]

        class Flag:
            def set(flag):
                self.stopped = True
        self.stop_flag = Flag()

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined = True

    def banner_lines(self):
        return []


def tried(fn):
    """fn's value, or the exception's name: a crash in the code under test reads as a
    value no check accepts, so it FAILs the check rather than the run."""
    try:
        return fn()
    except Exception as e:      # noqa: BLE001
        return "raised %s" % type(e).__name__


def console_checks(C):
    """--no-wican-console (reviewer, 2026-10-20: no WiCAN console captures until the
    factory reset)."""
    p = tried(lambda: C.build_parser().parse_args(["threshold", "--power", "x", "--no-wican-console"]))
    check("the parser takes --no-wican-console", getattr(p, "no_wican_console", None) is True, p)
    p = tried(lambda: C.build_parser().parse_args(["threshold", "--power", "x"]))
    check("the console is on unless asked off", getattr(p, "no_wican_console", None) is False, p)
    FakeCC.made = 0
    cc = tried(lambda: C.start_wican_console(ns(no_wican_console=True), FakeCC))
    check("with --no-wican-console no capture is made and none returned", cc is None and FakeCC.made == 0,
          (cc, FakeCC.made))
    cc = tried(lambda: C.start_wican_console(ns(no_wican_console=False), FakeCC))
    check("without it the capture is made and started", isinstance(cc, FakeCC) and cc.started, cc)
    got = tried(lambda: C.console_totals(None))
    check("with no capture the record says the console was off",
          isinstance(got, tuple) and got[0] == {"wican_console": "off (--no-wican-console)"}
          and "off" in got[1], got)
    cc = FakeCC()
    got = tried(lambda: C.console_totals(cc))
    check("with a capture it is stopped and joined, and its counts recorded",
          isinstance(got, tuple) and cc.stopped and cc.joined and got[0].get("wican_console_line_count") == 2
          and got[0].get("wican_console_events") == [(1.0, "open")], got)
    src = open(C.__file__, encoding="utf-8").read()
    check("main makes the capture only through start_wican_console",
          "ConsoleCapture()" not in src and "CC = start_wican_console(a, ConsoleCapture)" in src)
    out = tried(lambda: C.reset_to_off(None, iter([{"uptime_ms": 900, "mode": 0}, {"uptime_ms": 5, "mode": 0}]).__next__,
                                      lambda path: (200, ""), iter(range(1000)).__next__, lambda s: None))
    check("with no capture the reset is POST /system_reboot", isinstance(out, dict) and
          out.get("how") == "POST /system_reboot (no COM4)" and out.get("ok") is True, out)


def checks(C):
    console_checks(C)
    # ---- judged_errs
    e = [105.0] + wit_errs(29)
    j = C.judged_errs(e, 110.0, 1.0, 10)
    check("judged_errs keeps the first 10 pulses and what came before them",
          j == [105.0] + wit_errs(10), (len(j), j[-1:]))
    check("judged_errs cuts half an interval before pulse 11",
          C.judged_errs([119.49, 119.51], 110.0, 1.0, 10) == [119.49], C.judged_errs([119.49, 119.51], 110.0, 1.0, 10))
    check("judged_errs with no judge_first keeps everything", C.judged_errs(e, 110.0, 1.0, None) == e)

    # ---- threshold_refusal
    check("29 x 1000 ms is inside the jammer's limit", C.threshold_refusal(ns()) is None, C.threshold_refusal(ns()))
    r = C.threshold_refusal(ns(n=30))
    check("30 x 1000 ms is refused before the bench", r is not None and "30000" in r, r)
    check("10 x 1000 ms (plain B6) runs without --judge-first or --ecc",
          C.threshold_refusal(ns(n=10, judge_first=None, ecc=False, reset_wican=False)) is None)
    for jf in (0, 30):
        r = C.threshold_refusal(ns(judge_first=jf))
        check("--judge-first %d is refused" % jf, r is not None and "--judge-first" in r, r)
    r = C.threshold_refusal(ns(reset_wican=False))
    check("--ecc without --reset-wican is refused", r is not None and "--reset-wican" in r, r)

    # ---- identity
    b, why = C.identity({"elf_sha256": SHA.upper()}, SHA)
    check("identity: the page's elf_sha256 matches, whatever its case", why is None and b["elf_sha256"] == SHA, (b, why))
    b, why = C.identity({"elf_sha256": "cd" * 32}, SHA)
    check("identity: another image is refused, naming both", why is not None and "cd" * 32 in why and SHA in why, why)
    b, why = C.identity({}, SHA)
    check("identity: no elf_sha256 on the page is refused", why is not None, why)
    b, why = C.identity({"elf_sha256": SHA}, None)
    check("identity: with no --want-sha it records and does not refuse",
          why is None and b == {"elf_sha256": SHA, "from": "/gen_inhibit"}, (b, why))
    src = open(C.__file__, encoding="utf-8").read()
    check("main takes the build from the page, and nothing calls build_id or /check_status",
          "build_id(" not in src and '"/check_status"' not in src and '"build": build' in src)

    # ---- ecc_entry
    pg, s = C.ecc_entry(lambda p: (_ for _ in ()).throw(OSError("timed out")), [], 1.0, 10)
    check("ecc_entry: a failed read is a problem, not an empty page",
          pg is None and s["problems"] and "timed out" in s["problems"][0], s)

    # ---- the case, end to end
    rec, arm, gets = run_case(C, ns(), wit_errs(29), 119.15, TG.page())
    check("29 pulses, trip 6 after the 10th: the arm is judged on the first 10 and PASSes",
          arm["judge"]["verdict"] == "PASS" and arm.get("judged_first") == 10 and rec["verdict"] == "PASS",
          arm["judge"])
    ap = arm.get("all_pulses") or {}
    check("every pulse is still recorded: 29 groups, one error frame each",
          ap.get("groups") == 29 and ap.get("exactly_one_each") is True, ap)
    check("/gi_ecc is read once, after the arm", gets == ["/gi_ecc"], gets)
    e = arm.get("ecc") or {}
    check("its summary aligns and finds no over-count", e.get("aligned") is True and e.get("overcounts") == [], e.get("why"))
    check("the raw page is kept beside it", (arm.get("ecc_page") or {}).get("probe") == "gi_ecc")
    check("pulses 11-29 are marked not judged",
          [k["judged"] for k in e.get("pulses", [])] == [True] * 10 + [False] * 19)

    rec, arm, _ = run_case(C, ns(), wit_errs(29), 119.15, TG.page(over={15: 11}))
    e = arm.get("ecc") or {}
    check("a post-latch over-count leaves the judged arm PASS and is reported as pulse 15, post-latch",
          arm["judge"]["verdict"] == "PASS" and e.get("overcounts") == [15] and
          e["pulses"][14]["phase"] == "post-latch", (arm["judge"]["verdict"], e.get("overcounts")))

    rec, arm, _ = run_case(C, ns(), wit_errs(29), 111.3, TG.page(over={2: 11}, latch_pulse=2))
    e = arm.get("ecc") or {}
    check("a pre-latch over-count with an early latch: the arm FAILs as B6 does, pulse 2 reported",
          arm["judge"]["verdict"] == "FAIL" and "before the 10th" in arm["judge"].get("why", "") and
          e.get("overcounts") == [2], (arm["judge"], e.get("overcounts")))

    rec, arm, gets = run_case(C, ns(ecc=False), wit_errs(29), 119.15, TG.page())
    check("without --ecc nothing is read and no ecc entry is written", gets == [] and "ecc" not in arm, gets)
    rec, arm, gets = run_case(C, ns(n=10, judge_first=None, ecc=False), wit_errs(10), 119.15, TG.page(n=10))
    check("plain B6 (n 10, no --judge-first) is judged as before, with no all_pulses",
          arm["judge"]["verdict"] == "PASS" and "all_pulses" not in arm and "judged_first" not in arm, arm["judge"])
    rec, arm, _ = run_case(C, ns(), wit_errs(29), 119.15, TG.page(), get_raises=True)
    check("a failed /gi_ecc read is recorded as a problem in the arm",
          (arm.get("ecc") or {}).get("problems") and arm.get("ecc_page") is None, arm.get("ecc"))
    gate_checks(C)
    band_checks(C)


def band_checks(C):
    """band_refusal (reviewer, 2026-10-20; spec 7): judged pulses whose 10-pulse span is
    within 0.25 s of 10 s cannot be judged from the bus, and are refused before the bench."""
    for iv, refused in ((1000, False), (1083, False), (1084, True), (1111, True), (1138, True), (1139, False),
                        (1100, True)):
        r = C.band_refusal(10, iv)
        check("10 pulses at %d ms (span %.3f s): %s" % (iv, 9 * iv / 1000.0, "refused" if refused else "judged"),
              (r is not None) == refused and (not refused or "spec 7" in r), r)
    check("exactly 0.25 s from 10 s is judged (span 9.75 s)", C.band_refusal(10, 9750 / 9.0) is None,
          C.band_refusal(10, 9750 / 9.0))
    check("9 judged pulses are never refused (no window fills)", C.band_refusal(9, 1111) is None)
    r = C.threshold_refusal(ns(n=10, judge_first=None, ecc=False, reset_wican=False, interval_ms=1100))
    check("threshold refuses n 10 at 1100 ms before the bench", r is not None and "9.900 s" in r, r)
    r = C.threshold_refusal(ns(n=20, judge_first=10, interval_ms=1100))
    check("n 20 judged on the first 10 at 1100 ms is refused (the judged 10 span 9.9 s)",
          r is not None and "9.900 s" in r, r)
    r = C.threshold_refusal(ns(n=20, judge_first=9, interval_ms=1111))
    check("--judge-first 9 at 1111 ms is judged (9 pulses)", r is None, r)
    check("the B6 arms in use are judged: n 10 at 1000 ms, n 9 at 1100 ms, n 29 judged on 10 at 1000 ms",
          C.threshold_refusal(ns(n=10, judge_first=None, ecc=False, reset_wican=False)) is None and
          C.threshold_refusal(ns(n=9, judge_first=None, ecc=False, reset_wican=False, interval_ms=1100)) is None and
          C.threshold_refusal(ns()) is None)


S1 = gi_paths.verify(gi_paths.runs("b6_ecc_20261020T214017", "x2_jam_threshold_20261020T214653.json"))


def gate_checks(C):
    """--ecc-gate (reviewer, 2026-10-20): stop after the first arm when its /gi_ecc page
    fails gi_ecc.liveness; exit 3."""
    p = tried(lambda: C.build_parser().parse_args(["threshold", "--power", "x", "--ecc", "--reset-wican", "--ecc-gate"]))
    check("the parser takes --ecc-gate", getattr(p, "ecc_gate", None) is True, p)
    r = C.threshold_refusal(ns(ecc=False, reset_wican=False, ecc_gate=True))
    check("--ecc-gate without --ecc is refused", r is not None and "--ecc-gate needs --ecc" in r, r)
    check("--ecc-gate with --ecc runs", C.threshold_refusal(ns(ecc_gate=True)) is None)
    check("ecc_gate_stop: a page that was not read stops",
          "not usable" in str(tried(lambda: C.ecc_gate_stop({"problems": ["/gi_ecc not read: x"]}))))
    check("ecc_gate_stop: no liveness counts stops", "no liveness" in str(tried(lambda: C.ecc_gate_stop({"problems": []}))))
    check("ecc_gate_stop: a live page does not stop",
          tried(lambda: C.ecc_gate_stop({"problems": [], "liveness": {"stop": False}})) is None)
    s1 = json.load(open(S1))["arms"][0]
    w = s1["witness_error_times"]
    why = tried(lambda: C.ecc_gate_stop(C.GE.summary(s1["ecc_page"], w, 1.0, 10)))
    check("session 1's first arm (positive control) stops the gate on its undefined segments",
          "liveness gate:" in str(why) and "29 of 29 isr rows have a segment the TRM does not define" in str(why)
          and "every one of" not in str(why), why)
    dead, live = TG.seg_page([0] * 29), TG.page()
    one = TG.page(ecc_alt=False)              # one live code on every row (session 2's 0xa2 arms)
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=2, ecc_gate=True), wit_errs(29), 119.15, [one, live])) or ({}, {}, [])
    check("one live code on every row of the first page does not stop the gate",
          len(rec.get("arms", [])) == 2 and rec.get("stopped") is None, (len(rec.get("arms", [])), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=2, ecc_gate=True), wit_errs(29), 119.15, [dead, live])) or ({}, {}, [])
    check("a dead first page with --ecc-gate: one arm, STOPPED, the reason recorded",
          len(rec.get("arms", [])) == 1 and rec.get("verdict") == "STOPPED" and gets == ["/gi_ecc"]
          and "after arm 0: liveness gate: 29 of 29 isr rows have a segment the TRM does not define"
          in str(rec.get("stopped")),
          (len(rec.get("arms", [])), rec.get("verdict"), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=2, ecc_gate=True), wit_errs(29), 119.15, [live, dead])) or ({}, {}, [])
    check("a live first page: both arms run; a dead second page does not stop (the gate is on the first arm)",
          len(rec.get("arms", [])) == 2 and rec.get("stopped") is None and rec.get("verdict") == "PASS",
          (len(rec.get("arms", [])), rec.get("verdict"), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=2), wit_errs(29), 119.15, [dead, dead])) or ({}, {}, [])
    check("without --ecc-gate a dead page stops nothing", len(rec.get("arms", [])) == 2 and rec.get("stopped") is None,
          len(rec.get("arms", [])))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=2, ecc_gate=True), wit_errs(29), 119.15, live, get_raises=True)) or ({}, {}, [])
    check("an unread first page with --ecc-gate stops", len(rec.get("arms", [])) == 1 and "not usable" in str(rec.get("stopped")),
          rec.get("stopped"))
    src = open(C.__file__, encoding="utf-8").read()
    check("main exits 4 on an over-count stop, 3 on the gate, else 0",
          'return 4 if rec.get("stopped_by") == "over-count" else 3 if rec.get("stopped") else 0' in src)
    over_checks(C)


def over_checks(C):
    """--stop-over-counts (reviewer, 2026-10-21): B6 session 3 stops at the end of the
    arm where the 3rd over-count lands; sessions 1-2 had 2, so session 3 passes 1."""
    p = tried(lambda: C.build_parser().parse_known_args(["threshold", "--power", "x", "--ecc", "--reset-wican",
                                                         "--stop-over-counts", "1"])[0])
    check("the parser takes --stop-over-counts N", getattr(p, "stop_over_counts", None) == 1, p)
    p = tried(lambda: C.build_parser().parse_args(["threshold", "--power", "x"]))
    check("--stop-over-counts defaults to off", getattr(p, "stop_over_counts", "absent") is None, p)
    r = C.threshold_refusal(ns(ecc=False, reset_wican=False, stop_over_counts=1))
    check("--stop-over-counts without --ecc is refused", r is not None and "--stop-over-counts needs --ecc" in r, r)
    r = C.threshold_refusal(ns(stop_over_counts=0))
    check("--stop-over-counts 0 is refused", r is not None and "below 1" in r, r)
    check("--stop-over-counts 1 with --ecc runs", C.threshold_refusal(ns(stop_over_counts=1)) is None)
    st = lambda arms, n: tried(lambda: C.over_count_stop(arms, n))
    a0, a15 = {"ecc": {"overcounts": []}}, {"ecc": {"overcounts": [15]}}
    check("over_count_stop: no over-count, no stop", st([a0, a0], 1) is None, st([a0, a0], 1))
    why = st([a0, a15], 1)
    check("over_count_stop: the 1st of 1 in arm 1 stops after arm 1, naming the pulse",
          "after arm 1" in str(why) and "arm 1 pulse 15" in str(why), why)
    check("over_count_stop: 1 of 2 does not stop", st([a0, a15], 2) is None, st([a0, a15], 2))
    check("over_count_stop: an unread page adds none", st([{"ecc": {"problems": ["x"]}}, {}], 1) is None)
    check("over_count_stop: two pulses in one arm reach 2", "after arm 0" in str(st([{"ecc": {"overcounts": [5, 9]}}], 2)))
    live, o15, two = TG.page(), TG.page(over={15: 11}), TG.page(over={5: 3, 9: 3})
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3, stop_over_counts=1), wit_errs(29), 119.15,
                                            [live, o15, live])) or ({}, {}, [])
    check("an over-count in arm 1 of 3 with --stop-over-counts 1: stops after arm 1, STOPPED, by over-count",
          len(rec.get("arms", [])) == 2 and rec.get("verdict") == "STOPPED" and rec.get("stopped_by") == "over-count"
          and "after arm 1" in str(rec.get("stopped")) and "arm 1 pulse 15" in str(rec.get("stopped"))
          and rec.get("over_count_pulses") == 1 and gets == ["/gi_ecc"] * 2,
          (len(rec.get("arms", [])), rec.get("verdict"), rec.get("stopped_by"), rec.get("stopped"), rec.get("over_count_pulses")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3, stop_over_counts=1), wit_errs(29), 119.15,
                                            [o15, live, live])) or ({}, {}, [])
    check("an over-count in arm 0: one arm", len(rec.get("arms", [])) == 1 and rec.get("stopped_by") == "over-count",
          (len(rec.get("arms", [])), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=4, stop_over_counts=2), wit_errs(29), 119.15,
                                            [o15, live, o15, live])) or ({}, {}, [])
    check("--stop-over-counts 2 with over-counts in arms 0 and 2: stops after arm 2",
          len(rec.get("arms", [])) == 3 and "after arm 2" in str(rec.get("stopped")) and rec.get("over_count_pulses") == 2,
          (len(rec.get("arms", [])), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3, stop_over_counts=2), wit_errs(29), 119.15,
                                            [live, two, live])) or ({}, {}, [])
    check("--stop-over-counts 2 with two over-count pulses in arm 1: stops after arm 1",
          len(rec.get("arms", [])) == 2 and "after arm 1" in str(rec.get("stopped")), (len(rec.get("arms", [])), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3, stop_over_counts=1), wit_errs(29), 119.15,
                                            [live, live, live])) or ({}, {}, [])
    check("no over-count: all 3 arms, no stop, the count recorded as 0",
          len(rec.get("arms", [])) == 3 and rec.get("stopped") is None and rec.get("over_count_pulses") == 0
          and rec.get("verdict") == "PASS", (len(rec.get("arms", [])), rec.get("stopped"), rec.get("over_count_pulses")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3), wit_errs(29), 119.15, [live, o15, live])) or ({}, {}, [])
    check("without --stop-over-counts an over-count stops nothing and no count is recorded",
          len(rec.get("arms", [])) == 3 and rec.get("stopped") is None and "over_count_pulses" not in rec,
          (len(rec.get("arms", [])), rec.get("stopped")))
    rec, arm, gets = tried(lambda: run_case(C, ns(arms=3, ecc_gate=True, stop_over_counts=1), wit_errs(29), 119.15,
                                            [TG.seg_page([0] * 29), live, live])) or ({}, {}, [])
    check("a dead first page with both flags: stopped by the gate",
          len(rec.get("arms", [])) == 1 and rec.get("stopped_by") == "gate", (rec.get("stopped_by"), rec.get("stopped")))


MUTANTS = [
    ("judged_errs keeps pulse 11", "cut = p0 + (judge_first - 0.5) * interval_s", "cut = p0 + (judge_first + 0.6) * interval_s"),
    ("judged_errs ignores judge_first", "    if not judge_first:\n        return list(errs)\n", "    return list(errs)\n"),
    ("the case judges every pulse", "judge_threshold(judged_errs(errs, ph[\"pulses_start\"], iv, a.judge_first),",
     "judge_threshold(list(errs),"),
    ("the case judges with n_cmd = n", "        jf = a.judge_first or a.n\n", "        jf = a.n\n"),
    ("the jammer limit is not checked", "    if a.n * a.interval_ms + 100 > JAMMER_MAX_MS:\n", "    if False:\n"),
    ("the jammer limit is off by the 100 ms", "    if a.n * a.interval_ms + 100 > JAMMER_MAX_MS:\n",
     "    if a.n * a.interval_ms > JAMMER_MAX_MS:\n"),
    ("--judge-first is not range-checked", "    if a.judge_first is not None and not 1 <= a.judge_first <= a.n:\n",
     "    if False:\n"),
    ("--ecc runs without the reset", "    if a.ecc and not a.reset_wican:\n", "    if False:\n"),
    ("identity is case-sensitive", 'sha = ((p or {}).get("elf_sha256") or "").lower() or None',
     'sha = ((p or {}).get("elf_sha256") or "") or None'),
    ("identity never refuses", "    if want_sha and sha != want_sha.lower():\n", "    if False:\n"),
    ("main still asks /check_status", '"build": build, "power": a.power,', '"build": LF.build_id(), "power": a.power,'),
    ("/gi_ecc is never read", "            if a.ecc:\n                arm[\"ecc_page\"], arm[\"ecc\"]",
     "            if False:\n                arm[\"ecc_page\"], arm[\"ecc\"]"),
    ("the ecc summary is not told judge_first", 'ecc_entry(LF.dev_get, pe, iv, jf)', 'ecc_entry(LF.dev_get, pe, iv, None)'),
    ("all pulses are not recorded", '                arm["all_pulses"] = XJ.per_pulse(pe, a.n, iv, be)\n', '\n'),
    ("--no-wican-console is ignored", '    if getattr(a, "no_wican_console", False):\n        return None\n', ""),
    ("the flag is not in the parser", '    ap.add_argument("--no-wican-console", action="store_true",',
     '    ap.add_argument("--no-wican-console-x", action="store_true",'),
    ("main makes its own capture", "    CC = start_wican_console(a, ConsoleCapture)",
     "    CC = ConsoleCapture()\n    CC.start()"),
    ("the totals read a capture that is not there", '    if cc is None:\n        return {"wican_console": "off (--no-wican-console)"}, "COM4 console off"\n', ""),
    ("the capture is not stopped", "    cc.stop_flag.set()\n    cc.join(timeout=3.0)\n", ""),
    ("the gate is never applied", '        if getattr(a, "ecc_gate", False) and k == 0:\n', "        if False:\n"),
    ("the gate is on every arm", '        if getattr(a, "ecc_gate", False) and k == 0:\n', '        if getattr(a, "ecc_gate", False):\n'),
    ("--ecc-gate runs without --ecc", '    if getattr(a, "ecc_gate", False) and not a.ecc:\n', "    if False:\n"),
    ("a stopped run keeps its verdict", '        rec["verdict"] = "STOPPED"\n', "        pass\n"),
    ("the gate does not break", 'print("STOPPED %s" % rec["stopped"])\n                break\n', 'print("STOPPED %s" % rec["stopped"])\n'),
    ("an unread page passes the gate", '    if e.get("problems"):\n        return "/gi_ecc not usable', '    if False:\n        return "/gi_ecc not usable'),
    ("no liveness passes the gate", '    if g is None:\n        return "/gi_ecc gave no liveness counts"\n', ""),
    ("main exits 0 when stopped", 'else 3 if rec.get("stopped") else 0', "else 0"),
    ("main exits 3 on an over-count stop", 'return 4 if rec.get("stopped_by") == "over-count"',
     'return 3 if rec.get("stopped_by") == "over-count"'),
    ("the over-count stop is never applied",
     '        if getattr(a, "stop_over_counts", None) is not None:\n            why = over_count_stop',
     '        if False:\n            why = over_count_stop'),
    ("the over-count stop does not break", 'print("STOPPED %s" % why)\n                break\n', 'print("STOPPED %s" % why)\n'),
    ("the over-count stop is one early", "    if len(seen) < n:\n", "    if len(seen) < n - 1:\n"),
    ("the over-count stop is one late", "    if len(seen) < n:\n", "    if len(seen) <= n:\n"),
    ("over-count arms counted, not pulses",
     '    seen = [(k, p) for k, x in enumerate(arms) for p in ((x.get("ecc") or {}).get("overcounts") or [])]',
     '    seen = [(k, x["ecc"]["overcounts"][0]) for k, x in enumerate(arms) if (x.get("ecc") or {}).get("overcounts")]'),
    ("--stop-over-counts runs without --ecc", "    if so is not None and not a.ecc:\n", "    if False:\n"),
    ("--stop-over-counts 0 runs", "    if so is not None and so < 1:\n", "    if False:\n"),
    ("the over-count stop is not marked", '                rec["stopped_by"] = "over-count"\n', ""),
    ("the call's over-count total is not recorded",
     '        rec["over_count_pulses"] = sum(', '        rec["over_count_pulses"] = 0 * sum('),
    ("the over-count flag is not in the parser", 'ap.add_argument("--stop-over-counts", type=int',
     'ap.add_argument("--stop-over-count", type=int'),
    ("the band is never refused", "    why = band_refusal(min(a.n, a.judge_first or a.n), a.interval_ms)\n    if why:\n        return why\n", ""),
    ("the band takes n, not the judged pulses", "band_refusal(min(a.n, a.judge_first or a.n), a.interval_ms)",
     "band_refusal(a.n, a.interval_ms)"),
    ("the band includes its edge", "    if abs(span - window_s) < TRIP6_BAND_S:\n", "    if abs(span - window_s) <= TRIP6_BAND_S:\n"),
    ("9 judged pulses refused", "    if n_judged < 10:\n        return None\n", ""),
    ("the band is 0.1 s", "TRIP6_BAND_S = 0.25\n", "TRIP6_BAND_S = 0.1\n"),
    ("ecc_entry hides a failed read", '        return None, {"problems": ["/gi_ecc not read: %s" % txt], "caveats": []}',
     '        return None, {"problems": [], "caveats": []}'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(load(os.path.join(HERE, "x2_jam_cases.py")))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    src = open(os.path.join(HERE, "x2_jam_cases.py"), encoding="utf-8", newline="").read()
    crlf = "\r\n" in src
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if crlf:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            continue
        with tempfile.TemporaryDirectory(prefix="gi-jam-b6ecc-m%d-" % i, dir=gi_paths.scratch("tmp", run=False)) as tmp:
            mp = os.path.join(tmp, "x2_jam_cases.py")
            open(mp, "w", encoding="utf-8", newline="").write(src.replace(old, new))
            env = dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.environ.get("PYTHONPATH", ""))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--module", mp],
                               capture_output=True, text=True, timeout=300, env=env)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
        if fails and not crashed:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:90]))
        else:
            print("%s m%d %s  (rc %d)%s" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode,
                                            ("  " + r.stderr.strip().splitlines()[-1]) if crashed and r.stderr.strip() else ""))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--module"]:
        checks(load(sys.argv[2]))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
