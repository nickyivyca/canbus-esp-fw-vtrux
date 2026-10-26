#!/usr/bin/env python3
"""Mutants of suite_judges.py; test_suite_judges.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 test_suite_judges_mutants.py

Each mutant copies suite_judges.py and the test into a fresh folder in the worktree's
scratch/, applies one edit, and runs the test there. Gen-inhibit tester, 2026-10-18.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
SRC = "suite_judges.py"
TEST = "test_suite_judges.py"

MUTANTS = [
    ("B11: an analysis row's error is ignored", 'return [result("B11", "ERROR" if err else', 'return [result("B11", "ERROR" if False else'),
    ("B11raw: an analysis row's error is ignored", 'return [result("B11raw", "ERROR" if err else', 'return [result("B11raw", "ERROR" if False else'),
    ("B13: an analysis row's error is ignored", 'return [result("B13", "ERROR" if err else', 'return [result("B13", "ERROR" if False else'),
    ("B1: no commands is a pass", 'ok = s["commands"] > 0 and', 'ok = s["commands"] >= 0 and'),
    ("B2: X2 frames not counted", 'x2 = sum(int(v) for r in rows for v in r[1:])', 'x2 = 0'),
    ("B2: device count is the device's own word", 'and p["dev"] == p["dev_says"]', 'and True'),
    ("B3: the first answer's min is its max", 'figs["first_answer_ms_%s_min" % m] = fig(min(ms), "ms")',
     'figs["first_answer_ms_%s_min" % m] = fig(max(ms), "ms")'),
    ("B24: no device frames passes on any arm", 'bad = other + [n for n in none if "_respond_" not in n]', 'bad = other'),
    ("B5: an arm passes on A alone", 'return "PASS" if re.search(r"\\| A: PASS\\b.*\\| B: PASS\\b", v) else "FAIL"',
     'return "PASS" if re.search(r"\\| A: PASS\\b", v) else "FAIL"'),
    ("B5: inconclusive counts as a pass", 'if v.startswith("INCONCLUSIVE"):\n        return "INCONCLUSIVE"', 'if False:\n        pass'),
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
    ("B11raw: strict FAIL passes", 'passing = [v for v in verdicts if v in ("PASS", "strict FAIL, PASS on ACK")]',
     'passing = [v for v in verdicts if v in ("PASS", "strict FAIL, PASS on ACK", "strict FAIL")]'),
    ("B12: any reset kind will do", 'want = "SW" if restart == "reboot" else "PANIC"', 'want = m.group(1)'),
    ("B13: PASSIVE may answer", 'or (r["mode"] == "passive" and r["device_0x051"] != 0)', ''),
    ("B14: the end mode is not checked", 'and v.group(2) == "0"', ''),
    ("NONODE: NOT AS SPECIFIED is not a FAIL", '"FAIL" if v.startswith("NOT AS SPECIFIED") else', '"PASS" if v.startswith("NOT AS SPECIFIED") else'),
    ("NONODE: FLAKY passes", 'v.startswith(("INCONCLUSIVE", "FLAKY"))', 'v.startswith(("INCONCLUSIVE",))'),
    ("NONODE: the arms are not checked against a PASS", 'if v == "PASS" and (off or len(mains) < want):', 'if False:'),
    ("NONODE: the run's own --arms is ignored", 'want = (rec.get("args") or {}).get("arms", 3)', 'want = 3'),
    ("NONODE: no termination passes", '    if not rec.get("termination"):\n', '    if False:\n'),
    ("NONODE: the latch figure is the first arm's", 'fig(max(float(m.group(1)) for m in lat), "s")', 'fig(float(lat[0].group(1)), "s")'),
    ("B15: the repeat id from any adapter", 'if len(ids) == 1:', 'if True:'),
    ("B15host: a frame that does not decode passes", 'result("B15host", "PASS" if decodes and pages else "FAIL"',
     'result("B15host", "PASS" if pages else "FAIL"'),
    ("B17: the page need not name the image", 'ok = first == "AS SPECIFIED" and m and i and m.group(1) == i.group(1)',
     'ok = first == "AS SPECIFIED" and m and i'),
    ("B21: an RST is not counted", 'rst = sum(1 for _, e, _ in reps if e == "rst")', 'rst = 0'),
    ("B22: tx_fail is not checked", 'if not prov_ok(p) or w.group(1) != w.group(2) or int(d.group(2)) or',
     'if not prov_ok(p) or w.group(1) != w.group(2) or'),
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
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = gi_paths.scratch("mutants-suite_judges")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
