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
    ("B11raw: an analysis row's error is ignored", 'return [result("B11raw", "ERROR" if err else', 'return [result("B11raw", "ERROR" if False else'),
    ("B13: an analysis row's error is ignored", 'return [result("B13", "ERROR" if err else', 'return [result("B13", "ERROR" if False else'),
    ("B1: no commands is a pass", 'if ok and s["commands"] > 0 and not not_run:', 'if ok and not not_run:'),
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
    ("B2: device count is the device's own word", 'and p["dev"] == p["dev_says"]', 'and True'),
    ("B3: the first answer's min is its max", 'figs["first_answer_ms_%s_min" % m] = fig(min(ms), "ms")',
     'figs["first_answer_ms_%s_min" % m] = fig(max(ms), "ms")'),
    ("B24: no device frames passes on any arm", 'bad = other + [n for n in none if "_respond_" not in n]', 'bad = other'),
    # an arm VOID in its own run (the witness clock, 2026-11-18)
    ("B4: a VOID arm reads FAIL", '"VOID" if void and not any(v == "FAIL" for _, v in rows) else "FAIL"', '"FAIL"'),
    ("B4: a VOID arm hides another's FAIL", '"VOID" if void and not any(v == "FAIL" for _, v in rows) else "FAIL"',
     '"VOID" if void else "FAIL"'),
    # an arm voided on its witness clock (2026-11-18): B11, B11raw
    ("B11: a void arm reads FAIL", '    if void and not bad and not err:\n        return [result("B11", "VOID"',
     '    if False:\n        return [result("B11", "VOID"'),
    ("B11: a void arm hides another's FAIL", '    bad = [r["run"] for r in rows if not r.get("void") and (',
     '    bad = [r["run"] for r in rows if not r.get("void") and False and ('),
    ("B11raw: a VOID post reads FAIL", '    vv = [v for v in verdicts if v.startswith("VOID: arm void")]\n',
     '    vv = []\n'),
    ("B11raw: a void arm's provenance reads FAIL", '    pbad = [r["run"] for r in prov if not r.get("void") and r.get("provenance") != "as specified"]\n',
     '    pbad = [r["run"] for r in prov if r.get("provenance") != "as specified"]\n'),
    ("B11raw: a VOID post hides another's FAIL", '    if (vv or pvoid) and not fails and not pbad and others == 0 and not err:\n',
     '    if vv or pvoid:\n'),
    # x2_jam_cases' run VOID on its witness clock (2026-11-18): B5-B8
    ("B5: a VOID run reads FAIL", '    if void and not bad:\n        figs["void_runs"] = fig(void)\n',
     '    if False:\n        figs["void_runs"] = fig(void)\n'),
    ("B5: a VOID run hides an arm's FAIL", '        if "FAIL" in arms or (not arms and not nv):\n',
     '        if not arms and not nv:\n'),
    ("B5: a VOID run with no arms is FAIL", '        if "FAIL" in arms or (not arms and not nv):\n',
     '        if "FAIL" in arms or not arms:\n'),
    ("B6: a VOID run reads FAIL", '    if any(void) and seen <= {"PASS", "INCONCLUSIVE"}:\n', '    if False:\n'),
    ("B6: a VOID run hides an arm's FAIL", '    if any(void) and seen <= {"PASS", "INCONCLUSIVE"}:\n', '    if any(void):\n'),
    ("B7: a VOID run reads FAIL", '    if jam_void(t) and set(arms) <= {"AS SPECIFIED", "INCONCLUSIVE"}:\n', '    if False:\n'),
    ("B7: a VOID run hides a NOT AS SPECIFIED arm", '    if jam_void(t) and set(arms) <= {"AS SPECIFIED", "INCONCLUSIVE"}:\n',
     '    if jam_void(t):\n'),
    ("B8: a VOID run reads FAIL", '    if jam_void(t) and "FAIL" not in train and "FAIL" not in control:\n', '    if False:\n'),
    ("B8: a VOID run hides an arm's FAIL", '    if jam_void(t) and "FAIL" not in train and "FAIL" not in control:\n',
     '    if jam_void(t):\n'),
    ("B2: a VOID arm is judged", '            if re.search(r"---- ARM VOID:', '            if False and re.search(r"---- ARM VOID:'),
    ("B2: any ARM VOID is VOID (not only the witness clock's)",
     '            if re.search(r"---- ARM VOID:.*\\n(?:\\s+- .*\\n)*?\\s+- witness clock .*: not judged on host time", t):',
     '            if "ARM VOID" in t:'),
    ("B2: VOID arms read PASS", '    if void and not bad:\n        figs["void_arms"] = fig(len(void))\n        return [result("B2", "VOID"',
     '    if void and not bad:\n        figs["void_arms"] = fig(len(void))\n        return [result("B2", "PASS"'),
    ("B3: a VOID arm reads FAIL", '    if void and not bad:\n        figs["void_arms"] = fig(len(set(void)))\n',
     '    if False:\n        figs["void_arms"] = fig(len(set(void)))\n'),
    ("B3: a VOID entry counted as an entry", '    entries = [e for e in entries if not e.get("void")]\n', ''),
    ("B24: a VOID arm reads FAIL", '    if void and not bad:\n        return [result("B24", "VOID"',
     '    if False:\n        return [result("B24", "VOID"'),
    ("B24: a VOID arm hides another's FAIL", '    if void and not bad:\n        return [result("B24", "VOID"',
     '    if void:\n        return [result("B24", "VOID"'),
    ("B5: an arm passes on A alone", 'return "PASS" if re.search(r"\\| A: PASS\\b.*\\| B: PASS\\b", v) else "FAIL"',
     'return "PASS" if re.search(r"\\| A: PASS\\b", v) else "FAIL"'),
    ("B5: inconclusive counts as a pass", 'if v.startswith("INCONCLUSIVE"):\n        return "INCONCLUSIVE"', 'if False:\n        pass'),
    ("B6: an INCONCLUSIVE arm fails B6, as before 2026-10-29", 'if not arms or not seen <= {"PASS", "INCONCLUSIVE"}:',
     'if not arms or not seen <= {"PASS"}:'),
    ("B6: an INCONCLUSIVE arm passes", 'verdict = "PASS" if seen == {"PASS"} else "INCONCLUSIVE"', 'verdict = "PASS"'),
    ("B6: any arm word but FAIL will do", 'if not arms or not seen <= {"PASS", "INCONCLUSIVE"}:',
     'if not arms or "FAIL" in seen:'),
    # B7's INCONCLUSIVE (2026-11-16, B7 on madhouse-debian)
    ("B7: an INCONCLUSIVE arm fails B7, as before 2026-11-16", 'return [result("B7", "PASS" if ok else "INCONCLUSIVE" if inc else "FAIL",',
     'return [result("B7", "PASS" if ok else "FAIL",'),
    ("B7: INCONCLUSIVE beside a NOT AS SPECIFIED arm", 'and set(arms) <= {"AS SPECIFIED", "INCONCLUSIVE"} and v',
     'and v'),
    ("B7: INCONCLUSIVE whatever the case's verdict", 'and v and v.group(1) == "INCONCLUSIVE")', 'and v)'),
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
    ("B11raw: strict FAIL passes", 'passing = [v for v in verdicts if v in ("PASS", "strict FAIL, PASS on ACK")]',
     'passing = [v for v in verdicts if v in ("PASS", "strict FAIL, PASS on ACK", "strict FAIL")]'),
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
    ("B14: the end mode is not checked", 'and v.group(2) == "0"', ''),
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
    ("B21: an RST is not counted", 'rst = sum(1 for _, e, _ in reps if e == "rst")', 'rst = 0'),
    # the car-data endpoints not registered (spec 12.4, user 2026-11-16)
    ("B21: the car-data sentence not required", "    elif B21_UNREGISTERED not in fg:\n", "    elif False:\n"),
    ("B21: any line naming the car-data endpoints will do",
     'B21_UNREGISTERED = "/store_car_data and /upload/car_data.json are not registered (404 in every mode)."',
     'B21_UNREGISTERED = "/store_car_data and /upload/car_data.json"'),
    ("B22: tx_fail is not checked", 'if not prov_ok(p) or int(d.group(2)) or',
     'if not prov_ok(p) or'),
    ("B22: a void arm is a missing line, as before 2026-10-29", '        if "---- ARM VOID:" in t and reasons:\n',
     '        if False:\n'),
    # the old trail-window count decides nothing (gen-inhibit reviewer, 2026-10-29)
    ("B22: the superseded count still voids, as before 2026-10-29",
     '                   if not x.startswith("attribution disagrees with the device:")]',
     '                   ]'),
    ("B22: the superseded count still decides, as before 2026-10-29",
     'if not prov_ok(p) or int(d.group(2)) or',
     'if not prov_ok(p) or "8983 seen" not in t or int(d.group(2)) or'),
    ("B22: a void arm passes", '        return [result("B22", "INCONCLUSIVE", figs, "; ".join(void))]',
     '        return [result("B22", "PASS", figs, "; ".join(void))]'),
    ("B22: a void arm's tx_fail is not checked", 'or any(int(d.group(i)) for i in (2, 3, 4)) or int(e.group(1)):',
     'or int(e.group(1)):'),
    ("B22: a void arm's error frames are not checked", 'or any(int(d.group(i)) for i in (2, 3, 4)) or int(e.group(1)):',
     'or any(int(d.group(i)) for i in (2, 3, 4)):'),
    ("B22: a void arm's provenance is not checked", 'if not (p and d and e) or not prov_ok(p) or any(',
     'if not (d and e) or any('),
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
