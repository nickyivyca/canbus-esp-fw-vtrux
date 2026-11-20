#!/usr/bin/env python3
"""Mutants of suite_judges.py; test_suite_judges.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 test_suite_judges_mutants.py

Each mutant copies suite_judges.py and the test into a fresh folder in the worktree's
scratch/, applies one edit, and runs the test there. Gen-inhibit tester, 2026-10-18.

The unmutated copy runs first, as the control (2026-10-29): if it fails, no mutant is
judged and the run exits 1. Until then a crashed mutant was also counted as killed.
"""
import os
import shutil
import subprocess
import sys
# no bytecode anywhere below this process: a child importing the pinned core must not write
# __pycache__ into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
SRC = "suite_judges.py"
TEST = "test_suite_judges.py"

MUTANTS = [
    ("C7: a short commit is exactly 7 characters, as before port step 1d", 'C7 = r"[0-9a-f]{7,40}"', 'C7 = r"[0-9a-f]{7}"'),
    ("C7: any run of hex digits is a short commit", 'C7 = r"[0-9a-f]{7,40}"', 'C7 = r"[0-9a-f]+"'),
    ("B11: an analysis row's error is ignored", 'return [result("B11", "ERROR" if err else', 'return [result("B11", "ERROR" if False else'),
    ("B11raw: an analysis row's error is ignored", '    arms = vs + ["FAIL"] * len(pbad) + ["VOID"] * len(pvoid) + (["ERROR"] if err or bad_words else [])\n',
     '    arms = vs + ["FAIL"] * len(pbad) + ["VOID"] * len(pvoid) + (["ERROR"] if bad_words else [])\n'),
    ("B13: an analysis row's error is ignored", 'return [result("B13", "ERROR" if err else', 'return [result("B13", "ERROR" if False else'),
    ("B1: no commands is a pass", 'if ok and s["commands"] > 0 and not not_run:', 'if ok and not not_run:'),
    ("B4: fewer case lines can PASS", "    ok = len(rows) == B4_CASES and passed == len(rows)",
     "    ok = rows and passed == len(rows)"),
    ("B4: an INCONCLUSIVE case reads FAIL, as before",
     '    unjudged = void + sum(1 for _, v in rows if v == "INCONCLUSIVE")', "    unjudged = void"),
    ("B21: the hotspot gate's NOT-RUN ignored", "    if nr:\n        return [result(\"B21\", \"NOT-RUN\"",
     "    if False:\n        return [result(\"B21\", \"NOT-RUN\""),
    ("B22: an arm not run for its reboot reads as a missing line (FAIL)",
     "        if rb:\n            void.append(", "        if False:\n            void.append("),
    ("B22: an arm not run for its reboot FAILs, not VOID (the reviewer's answer to ba92acf0)",
     '            void.append("%s: not run (%s)" % (arm, rb.group(1)))', '            bad.append("%s: not run (%s)" % (arm, rb.group(1)))'),
    # B20 never PASSes with a requested scenario unjudged (run cb08; reviewer, 2026-11-19)
    ("B20: an unjudged case passes, as in run cb08", "    if unjudged:\n        return [result(\"B20\", \"VOID\"",
     "    if False:\n        return [result(\"B20\", \"VOID\""),
    ("B20: only the cases with a verdict line count",
     '    requested = re.findall(r"^(\\S+) +\\d+ frames +[\\d.]+ s +sha ", t, re.M)',
     '    requested = re.findall(r"^(\\S+) +(?:NOT )?AS SPECIFIED +\\(interlock_", t, re.M)'),
    ("B20: a NOT AS SPECIFIED beside a VOID reads VOID", "    if notspec:\n        return [result(\"B20\", \"FAIL\"",
     "    if notspec and not unjudged:\n        return [result(\"B20\", \"FAIL\""),
    ("B20: a verdict for a case not asked for is ignored", "    if not requested or extra:", "    if not requested:"),
    # B3 and B11 read VOID on void arms or a window the witness left short, and never raise (2026-11-19)
    ("B3: a void arm counted twice, by label and by run, as before",
     '    void = {r[0] for r in rows if r[4].startswith("VOID: arm void")}',
     '    void = {r[1] + " arm (stale check)" for r in rows if r[4].startswith("VOID: arm void")}'),
    ("B3: an entry the analysis could not make is not ERROR", "    if err:\n        return [result(\"B3\", \"ERROR\"",
     "    if False:\n        return [result(\"B3\", \"ERROR\""),
    ("B3: an entry with no answer reaches min()", "        if None in ms:\n", "        if False:\n"),
    ("B3: a None device count reaches int()", '           and (r[2] == "None" or r[4] != "as specified"',
     '           and (r[4] != "as specified"'),
    ("B11: an analysis with too few rows is not ERROR", "    if not err and len(rows) != 6:", "    if False:"),
    # B1's ERROR and NOT-RUN (run cb08's ws arm; gen-inhibit reviewer, 2026-11-19)
    ("B1: an ERROR line is not counted", 'errors = sum(1 for a, b in lines if "ERROR" in (a, b))', 'errors = 0'),
    ("B1: a line with a reason is not read", r'''lines = re.findall(r"-> (\w+)[^|\n]*\| counted (\w+)", t)''',
     r'''lines = re.findall(r"-> (\w+) +\| counted (\w+)", t)'''),
    ("B1: the summary's error count is ignored", '    if errors or s.get("error"):', '    if errors:'),
    ("B1: an arm not run reads PASS", '    if ok and s["commands"] > 0 and not not_run:', '    if ok and s["commands"] > 0:'),
    ("B1: the NOT-RUN lines need not match the summary",
     '    if len(not_run) != len(s.get("not_run") or []):', '    if False:'),
    ("B2: X2 frames not counted", 'x2 = sum(int(v) for r in rows for v in r[1:])', 'x2 = 0'),
    # "B2: device count is the device's own word" (prov_ok's dev == dev_says dropped) is equivalent since
    # spec 12.1 (c5849685): e4_attribution voids that disagreement before prov_ok reads it (B2's INHIBIT
    # arms, B22), and B2's RESPOND arms check dev != 0 themselves; its kill is "B2: an INHIBIT arm's
    # provenance count against tx_ok is not attribution" below
    ("B3: the first answer's min is its max", 'figs["first_answer_ms_%s_min" % m] = fig(min(ms), "ms")',
     'figs["first_answer_ms_%s_min" % m] = fig(max(ms), "ms")'),
    ("B24: no device frames passes on any arm", 'bad = other + [n for n in none if "_respond_" not in n]', 'bad = other'),
    # an arm VOID in its own run (the witness clock, 2026-11-18)
    ("B4: a VOID arm reads FAIL", '    elif unjudged:\n        verdict = "VOID"\n', '    elif unjudged:\n        verdict = "FAIL"\n'),
    ("B4: a VOID arm hides another's FAIL", '    elif any(v == "FAIL" for _, v in rows):\n        verdict = "FAIL"\n',
     '    elif any(v == "FAIL" for _, v in rows) and not unjudged:\n        verdict = "FAIL"\n'),
    # an arm voided on its witness clock (2026-11-18): B11, B11raw
    ("B11: a void arm reads FAIL", '    if void and not bad and not err:\n        return [result("B11", "VOID"',
     '    if False:\n        return [result("B11", "VOID"'),
    ("B11: a void arm hides another's FAIL",
     'if not r.get("void") and not r.get("error") and (\n           r.get("provenance") != "as specified" or r.get("late")',
     'if not r.get("void") and not r.get("error") and False and (\n           r.get("provenance") != "as specified" or r.get("late")'),
    ("B11raw: an arm void post read as FAIL", '        vs.append("VOID" if v.startswith("VOID") else "ERROR" if v.startswith("ERROR") else v)\n',
     '        vs.append("FAIL" if v.startswith("VOID:") else "VOID" if v == "VOID" else "ERROR" if v.startswith("ERROR") else v)\n'),
    ("B11raw: a void arm's provenance reads FAIL", '    pbad = [r["run"] for r in prov if not r.get("void") and r.get("provenance") != "as specified"]\n',
     '    pbad = [r["run"] for r in prov if r.get("provenance") != "as specified"]\n'),
    # the request-to-reply window (topic (E), 2026-11-19)
    ("B11raw: an unknown verdict word passes", '    bad_words = [v for v in vs if v not in ("PASS", "FAIL", "ERROR", "VOID")]\n',
     '    bad_words = []\n'),
    ("B11raw: a POST missing passes", "    if len(posts) != B11RAW_POSTS:\n        arms.append(\"ERROR\")\n", ""),
    ("B11raw: a void arm in the analysis ignored", '["VOID"] * len(pvoid)', '[]'),
    ("B11raw: the old file read", 'text(F.one(r"B11raw_window_judge\\.txt"))', 'text(F.one(r"B11raw_rawpost_judge\\.txt"))'),
    # x2_jam_cases' run VOID on its witness clock (2026-11-18): B5-B8
    ("B5: a VOID run reads FAIL", '    if void and not bad and "ERROR" not in arms:\n        figs["void_runs"] = fig(void)\n',
     '    if False:\n        figs["void_runs"] = fig(void)\n'),
    ("B5: an ERROR arm hidden by a VOID run", '    if void and not bad and "ERROR" not in arms:\n',
     '    if void and not bad:\n'),
    ("B5: a VOID run hides an arm's FAIL", '        if "FAIL" in arms or (not arms and not nv):\n',
     '        if not arms and not nv:\n'),
    ("B5: a VOID run with no arms is FAIL", '        if "FAIL" in arms or (not arms and not nv):\n',
     '        if "FAIL" in arms or not arms:\n'),
    ("B6: a VOID run reads FAIL", '    if any(void) and not {"FAIL", "ERROR"} & set(arms):\n', '    if False:\n'),
    ("B6: a VOID run hides an arm's FAIL", '    if any(void) and not {"FAIL", "ERROR"} & set(arms):\n', '    if any(void):\n'),
    ("B6: a VOID run hides an ERROR arm", '    if any(void) and not {"FAIL", "ERROR"} & set(arms):\n',
     '    if any(void) and "FAIL" not in arms:\n'),
    ("B7: a VOID run reads FAIL", '    if jam_void(t) and not {"FAIL", "ERROR"} & set(arms):\n', '    if False:\n'),
    ("B7: a VOID run hides a NOT AS SPECIFIED arm", '    if jam_void(t) and not {"FAIL", "ERROR"} & set(arms):\n',
     '    if jam_void(t):\n'),
    ("B7: a VOID run hides an ERROR arm", '    if jam_void(t) and not {"FAIL", "ERROR"} & set(arms):\n',
     '    if jam_void(t) and "FAIL" not in arms:\n'),
    ("B8: a VOID run reads FAIL", '    if jam_void(t) and "FAIL" not in train and "FAIL" not in control:\n', '    if False:\n'),
    ("B8: a VOID run hides an arm's FAIL", '    if jam_void(t) and "FAIL" not in train and "FAIL" not in control:\n',
     '    if jam_void(t):\n'),
    ("e4_void: a reason on no 12.1 entry is mapped to VOID", '            (listed if AV.kind(r) else unlisted).append(r)',
     '            listed.append(r)'),
    ("B2: VOID arms read PASS", '    if void and not bad:\n        figs["void_arms"] = fig(len(void))\n        return [result("B2", "VOID"',
     '    if void and not bad:\n        figs["void_arms"] = fig(len(void))\n        return [result("B2", "PASS"'),
    ("B3: a VOID arm reads FAIL",
     '    if void and not bad:\n        figs["void_arms"] = fig(len(void))\n        return [result("B3", "VOID"',
     '    if False:\n        figs["void_arms"] = fig(len(void))\n        return [result("B3", "VOID"'),
    ("B3: a VOID entry counted as an entry",
     '    entries = [e for e in entries if not e.get("void") and not e.get("error") and e.get("run") not in void]\n',
     '    entries = [e for e in entries if not e.get("error")]\n'),
    ("B24: a VOID arm reads FAIL", '    if void and not bad:\n        return [result("B24", "VOID"',
     '    if False:\n        return [result("B24", "VOID"'),
    ("B24: a VOID arm hides another's FAIL", '    if void and not bad:\n        return [result("B24", "VOID"',
     '    if void:\n        return [result("B24", "VOID"'),
    ("B5: an arm passes on A alone", 'return "PASS" if re.search(r"\\| A: PASS\\b.*\\| B: PASS\\b", v) else "FAIL"',
     'return "PASS" if re.search(r"\\| A: PASS\\b", v) else "FAIL"'),
    # the gen-inhibit reviewer's rulings on reasons on no 12.1 entry (answer to ba92acf0, 2026-11-19)
    ("B5: an INCONCLUSIVE arm is not read by its reason (FAIL)",
     'if v.startswith("INCONCLUSIVE"):\n        return AR.VERDICT[AR.b5_arm(v)]', 'if False:\n        pass'),
    ("B5: an INCONCLUSIVE arm reads INCONCLUSIVE, as before the rulings",
     '        return AR.VERDICT[AR.b5_arm(v)]', '        return "INCONCLUSIVE"'),
    ("B5: the ruled arms left out of the verdict", "        all_arms += arms\n",
     '        all_arms += [a for a in arms if a in ("PASS", "FAIL")]\n'),
    ("B5: a void arm passes", '    verdict = "FAIL" if bad else ruled_verdict(arms)', '    verdict = "FAIL" if bad else "PASS"'),
    # a run's re-judgement from its record stands in for its lines (answer to 0cfd6689, 2026-11-19)
    ("B5: a re-judgement read beside the run's lines, not in place of them",
     '    return [f for f in files if f.endswith("_rejudged.txt") or base(f)[:-4] + "_rejudged.txt" not in names]\n',
     "    return list(files)\n"),
    ("B5: a re-judgement not read", '                      ("500", r"B5_livetrain_p2000_run\\d(?:_rejudged)?\\.txt")):',
     '                      ("500", r"B5_livetrain_p2000_run\\d\\.txt")):'),
    ("jam_arms: an arm with no replay rate, or none at all, is not read",
     r'''re.findall(r"^arm (\d+)(?: \(replay [^)]*\))?: (.+)$", t, re.M)''',
     r'''re.findall(r"^arm (\d+) \(replay [\d.]+/s\): (.+)$", t, re.M)'''),
    ("B6: delivery other than one per pulse is VOID (12.1 widened)",
     '            a = "INCONCLUSIVE" if k == "inconclusive" else AR.VERDICT[k]',
     '            a = "VOID" if k == "inconclusive" else AR.VERDICT[k]'),
    ("B6: an INCONCLUSIVE arm reads INCONCLUSIVE, as before the rulings",
     '            a = "INCONCLUSIVE" if k == "inconclusive" else AR.VERDICT[k]', '            a = "INCONCLUSIVE"'),
    ("B6: any arm word but FAIL will do", '            a = "PASS" if word == "PASS" else "FAIL"',
     '            a = "FAIL" if word == "FAIL" else "PASS"'),
    ("B6: a run's own verdict ignored", '    if not runs <= {"PASS", "INCONCLUSIVE"}:\n', '    if False:\n'),
    ("B7: an INCONCLUSIVE arm reads INCONCLUSIVE, as before the rulings",
     '            a = AR.VERDICT[AR.b7_arm(x)]', '            a = "INCONCLUSIVE"'),
    ("B7: any arm word but NOT AS SPECIFIED will do", '            a = "PASS" if w == "AS SPECIFIED" else "FAIL"',
     '            a = "FAIL" if w.startswith("NOT") else "PASS"'),
    ("B7: the case's verdict ignored", '    if jam_void(t):\n        pass\n    elif not v', '    if True:\n        pass\n    elif not v'),
    ("ruled_verdict: VOID before ERROR", '    for v in ("FAIL", "ERROR", "VOID", "INCONCLUSIVE"):',
     '    for v in ("FAIL", "VOID", "ERROR", "INCONCLUSIVE"):'),
    ("ruled_verdict: INCONCLUSIVE before VOID", '    for v in ("FAIL", "ERROR", "VOID", "INCONCLUSIVE"):',
     '    for v in ("FAIL", "ERROR", "INCONCLUSIVE", "VOID"):'),
    ("B8: the control arm is not looked at", 'and all(c != "FAIL" for c in control)', ''),
    ("B9: trip 6 need not latch", 'ok = latched and min(bursts) > 0', 'ok = min(bursts) > 0'),
    ("B9: live_before is not looked at", '    if rec.get("live_before") is not True:\n', '    if False:\n'),
    ("B9: a latch before the first burst is not named", '    elif first is not None and first["t"] < 0:\n',
     '    elif False:\n'),
    ("B9: a sample not live before the latch is not looked at",
     '    elif any(x.get("live") is not True for x in before):\n', '    elif False:\n'),
    ("B9: the latch need not be placed after the first burst",
     '    elif not any(-B9_PRE_S <= x["t"] < 0 for x in before):\n', '    elif False:\n'),
    ("B9: any clean sample before the burst places the latch",
     '    elif not any(-B9_PRE_S <= x["t"] < 0 for x in before):\n', '    elif not any(x["t"] < 0 for x in before):\n'),
    ("B9: trip 6 read from the summary's aborts, as before 2026-10-25",
     'latched = bool(first) and first["abort"] == "error-frame rate exceeded"',
     'latched = "error-frame rate exceeded" in s["aborts"]'),
    ("B9: a record with no pre-burst witness count passes",
     '    elif not isinstance(rec.get("pre_burst_window"), dict) or rec["pre_burst_window"].get("errors") is None:\n',
     '    elif False:\n'),
    ("B9: witness error frames before the first burst are not looked at",
     '    elif rec["pre_burst_window"]["errors"] != 0:\n', '    elif False:\n'),
    ("B9: no page samples is not an error", '    if not series:\n        return [result("B9", "ERROR"',
     '    if False:\n        return [result("B9", "ERROR"'),
    ("B10: any judged arm counts", 'spec = sum(1 for *_, v in judged if v == "as specified")', 'spec = len(judged)'),
    # condition 5 (2026-11-18): the bursts' timing line
    ("B9: the burst timing VOID line is not read",
     '        return [result("B9", "VOID", detail="the bursts\' timing: %s" % bv.group(1)[:300])]\n', '        pass\n'),
    ("B10: the burst timing VOID line is not read",
     '        return [result("B10", "VOID", detail="the bursts\' timing: %s" % bv.group(1)[:300])]\n', '        pass\n'),
    ("B9/B10: the burst timing line matched anywhere", 'BURST_VOID_RE = re.compile(r"^burst timing VOID: (.*)$", re.M)',
     'BURST_VOID_RE = re.compile(r"burst timing VOID(.*)$", re.M)'),
    ("B12: any reset kind will do", 'want = "SW" if restart == "reboot" else "PANIC"', 'want = m.group(1)'),
    # the restart record (follow-up (iii), reviewer, 2026-11-03)
    ("B12: the restart record not judged", "        why = b12_record(f)\n", "        why = None\n"),
    ("B12: a record's failure does not fail B12", 'result("B12", "FAIL" if bad or rec_bad else', 'result("B12", "FAIL" if bad else'),
    ("B12: timed_out not judged", '("did_stdby", True), ("timed_out", False))', '("did_stdby", True))'),
    ("B12: did_twai not judged", 'B12_LAST_RESTART = (("did_twai", True), ', 'B12_LAST_RESTART = ('),
    ("B12: a truthy field will do", "for k, v in B12_LAST_RESTART if lr.get(k) is not v]",
     "for k, v in B12_LAST_RESTART if bool(lr.get(k)) != v]"),
    ("B12: a record not read passes", "    if not isinstance(lr, dict):\n        return", "    if not isinstance(lr, dict):\n        return None\n        return"),
    ("B12: a record not beside the file passes", "    if not os.path.isfile(p):\n        return", "    if not os.path.isfile(p):\n        return None\n        return"),
    ("B13: PASSIVE may answer", 'or (r["mode"] == "passive" and r["device_0x051"] != 0)', ''),
    ("B14: the end mode is not checked", '    if v.group(2) != "0":\n        arms.append("FAIL")\n', ''),
    ("B14: an INVALID row read as VOID whatever its POST", '            arms.append(AR.VERDICT[k])\n', '            arms.append("VOID")\n'),
    ("B14: a row NOT as specified FAILs beside a VOID control",
     '            arms.append("FAIL" if not v.group(1).startswith("VOID") else "VOID")',
     '            arms.append("FAIL")'),
    ("B14: a row NOT as specified is not read", '        elif m != "as specified":\n            arms.append(',
     '        elif False:\n            arms.append('),
    ("B14: the verdict's ruling not read", '    if kv is not None:\n        arms.append(AR.VERDICT[kv])\n',
     '    if kv is not None:\n        pass\n'),
    ("B14: NOT as specified with no row to explain it passes", '        if not arms:\n            arms.append("FAIL")\n',
     '        pass\n'),
    ("B14: any other verdict passes", '    elif v.group(1) != "as specified":\n        arms.append("FAIL")\n', ''),
    ("NONODE: NOT AS SPECIFIED is not a FAIL", '"FAIL" if v.startswith("NOT AS SPECIFIED") else', '"PASS" if v.startswith("NOT AS SPECIFIED") else'),
    ("NONODE: FLAKY passes", 'v.startswith(("INCONCLUSIVE", "FLAKY"))', 'v.startswith(("INCONCLUSIVE",))'),
    ("NONODE: the arms are not checked against a PASS", 'if v == "PASS" and (off or len(mains) < want):', 'if False:'),
    ("NONODE: the run's own --arms is ignored", 'want = (rec.get("args") or {}).get("arms", 3)', 'want = 3'),
    ("NONODE: no termination passes", '    if not rec.get("termination"):\n', '    if False:\n'),
    ("NONODE: the latch figure is the first arm's", 'fig(max(float(m.group(1)) for m in lat), "s")', 'fig(float(lat[0].group(1)), "s")'),
    ("B15: the repeat id from any adapter", 'if len(ids) == 1:', 'if True:'),
    ("B15: an adapter that did not open passes (2026-11-18)", '    if unread:\n        return [result("B15", "ERROR"',
     '    if False:\n        return [result("B15", "ERROR"'),
    ("B15host: a frame that does not decode passes", 'result("B15host", "PASS" if decodes and pages else "FAIL"',
     'result("B15host", "PASS" if pages else "FAIL"'),
    ("B17: the page need not name the image", 'ok = first == "AS SPECIFIED" and m and i and m.group(1) == i.group(1)',
     'ok = first == "AS SPECIFIED" and m and i'),
    # B21 at the client (topic (E), the reviewer's readings (s)-(w), 2026-11-19)
    ("B21: the client verdict not read", "        worst = ruled_verdict([worst, v])\n", ""),
    ("B21: no client verdict line read as PASS", '            v, n, whole = "ERROR", 0, 0\n',
     '            v, n, whole = "PASS", 0, 0\n'),
    ("B21: the whole-403 figure from the VOID count", "v, n, whole = m.group(1), int(m.group(2)), int(m.group(7))",
     "v, n, whole = m.group(1), int(m.group(2)), int(m.group(6))"),
    ("B21: the guards' FAIL lost under a client verdict", '    v = "FAIL" if bad else worst\n', "    v = worst\n"),
    # B21's spec 3 findings lifted into the summary line (the reviewer, answer to 48b1100e)
    ("B21: the spec 3 finding count not lifted", "                spec3.append((kind, int(f3.group(1)), base(cf)))\n",
     "                pass\n"),
    ("B21: a zero spec 3 finding count lifted too", "            if f3 and int(f3.group(1)) > 0:\n",
     "            if f3:\n"),
    ("B21: the spec 3 findings summed as one per file", "fig(sum(k for _b, k, _f in spec3))",
     "fig(len(spec3))"),
    ("B21: the spec 3 findings after the guards and notes", "lead + bad + notes",
     "bad + notes + lead"),
    ("B21: the spec 3 finding's file not named", '"%s %d (%s)" % (b, k, f)', '"%s %d" % (b, k)'),
    # the car-data endpoints not registered (spec 12.4, user 2026-11-16)
    ("B21: the car-data sentence not required", "    elif B21_UNREGISTERED not in fg:\n", "    elif False:\n"),
    ("B21: any line naming the car-data endpoints will do",
     'B21_UNREGISTERED = "/store_car_data and /upload/car_data.json are not registered (404 in every mode)."',
     'B21_UNREGISTERED = "/store_car_data and /upload/car_data.json"'),
    ("B22: tx_fail is not checked", 'if not prov_ok(p) or int(d.group(2)) or',
     'if not prov_ok(p) or'),
    ("B22: a void arm is judged as a normal arm", '        if listed:\n            void.append(arm + ": void ("',
     '        if False:\n            void.append(arm + ": void ("'),
    # the old trail-window count decides nothing (gen-inhibit reviewer, 2026-10-29)
    ("e4_void: the superseded whole-arm count still voids, as before 2026-10-29",
     '                if old_attribution_counts:\n                    listed.append(r)',
     '                if True:\n                    listed.append(r)'),
    ("B22: the superseded count still decides, as before 2026-10-29",
     'if not prov_ok(p) or int(d.group(2)) or',
     'if not prov_ok(p) or "8983 seen" not in t or int(d.group(2)) or'),
    # spec 12.1 (c5849685): a void arm makes B22 VOID, whatever it shows; attribution is a void reason
    ("B22: a void arm passes", '        return [result("B22", "VOID", figs, "; ".join(void))]',
     '        return [result("B22", "PASS", figs, "; ".join(void))]'),
    ("B22: a void arm is INCONCLUSIVE, as before c5849685",
     '        return [result("B22", "VOID", figs, "; ".join(void))]',
     '        return [result("B22", "INCONCLUSIVE", figs, "; ".join(void))]'),
    ("B22: a void arm's failing figures still FAIL, as before c5849685",
     '        if listed:\n            void.append(arm + ": void ("',
     '        if listed and "tx_fail=0" in t and "), 0 error frames" in t and "late 0" in t and "provenance (live)" in t:\n'
     '            void.append(arm + ": void ("'),
    ("B22: provenance's count against tx_ok is not attribution (FAIL, as before c5849685)",
     '        listed += e4_attribution(p)\n        if unlisted:\n            err.append("%s: a void',
     '        if unlisted:\n            err.append("%s: a void'),
    ("B22: a reason on no 12.1 entry is not an error",
     '        if unlisted:\n            err.append("%s: a void reason on no spec 12.1 entry: %s" % (arm, "; ".join(unlisted)))\n'
     '            continue\n        if listed:\n            void.append(arm + ": void ("',
     '        if listed:\n            void.append(arm + ": void ("'),
    ("B2: a void arm is judged as a normal arm", '            if listed:\n                void.append("%s %s (%s)" % (arm, x2,',
     '            if False:\n                void.append("%s %s (%s)" % (arm, x2,'),
    ("e4_void: only the witness clock's reason voids, as before c5849685",
     '            (listed if AV.kind(r) else unlisted).append(r)',
     '            (listed if AV.kind(r) == "clock" else unlisted).append(r)'),
    ("B2: an INHIBIT arm's provenance count against tx_ok is not attribution",
     '            if arm != "respond":\n                listed += e4_attribution(p)\n', ''),
    ("B2: a RESPOND arm's whole-arm probe count is not attribution",
     'listed, unlisted = e4_void(t, old_attribution_counts=arm == "respond")',
     'listed, unlisted = e4_void(t, old_attribution_counts=False)'),
    ("B2: a reason on no 12.1 entry is not an error", '    if err and not bad:\n        return [result("B2", "ERROR"',
     '    if False:\n        return [result("B2", "ERROR"'),
    ("B24: a reason on no 12.1 entry is not an error", '    if err and not bad:\n        return [result("B24", "ERROR"',
     '    if False:\n        return [result("B24", "ERROR"'),
    ("B13: a void arm passes", '    if void and not err and not bad and book and not bfail:',
     '    if False:'),
    ("B13: a void arm is judged as a normal arm",
     'if not r.get("void") and not r.get("error") and (\n           r.get("provenance") != "as specified"\n           or (r["mode"]',
     'if not r.get("error") and (\n           r.get("provenance") != "as specified"\n           or (r["mode"]'),
    ("B26: a void arm is judged as a normal arm", '        if listed:\n            void.append("%s (%s)" % (arm,',
     '        if False:\n            void.append("%s (%s)" % (arm,'),
    ("B26: a reason on no 12.1 entry is not an error",
     '        if unlisted:\n            err.append("%s: a void reason on no spec 12.1 entry: %s" % (arm, "; ".join(unlisted)))\n'
     '            continue\n        if listed:\n            void.append("%s (%s)" % (arm,',
     '        if listed:\n            void.append("%s (%s)" % (arm,'),
    ("B23: the retry loop need not free every buffer", 'ok = free.group(1) == free.group(2) and', 'ok = True and'),
    ("B26: >= 2 read at >= 4", 'figs["ge2_" + arm] = fig(dict(zip(rxq["ge_edges"], rxq["ge"])).get(2))',
     'figs["ge2_" + arm] = fig(dict(zip(rxq["ge_edges"], rxq["ge"])).get(4))'),
    ("H: a step's exit is not checked", 'nonzero = [s for s, c in exits.items() if c]', 'nonzero = []'),
    ("C1C7: one image is enough", 'if images != ["man", "prod"]:', 'if not images:'),
    ("C3: a negative may be missed", 'ok = m and m.group(1) == "PASS" and m.group(2) == m.group(3)', 'ok = m and m.group(1) == "PASS"'),
    ("a judge returning nothing passes", 'return out or [result(cid, "ERROR", detail="the judge returned nothing")]',
     'return out or [result(cid, "PASS")]'),
    ("the first of several files is taken", '        if len(got) != 1:\n            raise Missing',
     '        if not got:\n            raise Missing'),
    ("a missing file is a pass", '        return [result(cid, "ERROR", detail="files: %s" % e)]', '        return [result(cid, "PASS")]'),
    # A2 (a), 2026-11-11: the PCAN's clock VOID line, and B10's VOID arms
    ("B9: the clock's VOID line is ignored",
     '        return [result("B9", "VOID", detail="the PCAN\'s clock: %s" % cv.group(1))]', '        pass'),
    ("B10: the clock's VOID line is ignored",
     '        return [result("B10", "VOID", detail="the PCAN\'s clock: %s" % cv.group(1))]', '        pass'),
    ("B10: a VOID arm is FAIL, as before", "    if voided and not notspec:", "    if False:"),
    ("B10: a VOID arm hides a NOT as specified", "    if voided and not notspec:", "    if voided:"),
    # the host mode line, H and B15host (host_target.sh, 2026-11-12)
    ("host mode: any line is read, not only the first", '    first = t.split("\\n", 1)[0]  ',
     '    first = next((l for l in t.splitlines() if HOST_MODE_RX.fullmatch(l)), "")  '),
    ("host mode: remote reads as local", 'return ("local" if m.group(1) == "local mode" else "remote"), first[2:]',
     'return "local", first[2:]'),
    ("host mode: a mode word with nothing after it counts", 'HOST_MODE_RX = re.compile(r"# (local mode|remote): .+")',
     'HOST_MODE_RX = re.compile(r"# (local mode|remote): .*")'),
    ("host mode: any word before the colon counts", 'HOST_MODE_RX = re.compile(r"# (local mode|remote): .+")',
     'HOST_MODE_RX = re.compile(r"# ([\\w ]+): .+")'),
    ("H: no host_mode figure", '    figs["host_mode"] = fig(mode)\n', ''),
    ("H: the mode line not in the detail", '"%d steps exit 0" % len(exits), line))]', '"%d steps exit 0" % len(exits), ""))]'),
    ("B15host: no host_mode figure", '{"modes_with_pages": fig(len(pages)), "host_mode": fig(mode)}',
     '{"modes_with_pages": fig(len(pages))}'),
    ("B15host: the mode line not in the detail", 'else "a diag frame does not decode", line))]',
     'else "a diag frame does not decode", ""))]'),
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = gi_paths.scratch("mutants-suite_judges")
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-29): where the copied test cannot
    # pass, every mutant would read killed; and a crash is not a kill
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:90]) if fails else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
