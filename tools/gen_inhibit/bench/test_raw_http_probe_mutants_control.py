#!/usr/bin/env python3
"""The test of test_raw_http_probe_mutants.py's control, on its image_elf_sha suite (about 30 s).

    py -3.14 -B tools/gen_inhibit/bench/test_raw_http_probe_mutants_control.py

1. The runner as it is: the control passes, 6 of 6 killed, exit 0.
2. Two broken layouts, each of which must make the run report the control FAILED, count 0
   kills and exit 1:
   - OLD LAYOUT: test_image_elf_sha.py back in TAKES_SCRATCH, the layout that crashed its
     unmodified copy (no FAIL line, exit 1);
   - PRINTS FAIL: the unmodified test_image_elf_sha.py printing a FAIL line and still exiting 0
     (edited in the copy only).
3. Mutants of the control logic must each break that expectation under at least one layout.
Each variant is a copy of this folder in the worktree's scratch/. Gen-inhibit tester, 2026-10-25.
"""
import os
import re
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

BENCH = os.path.dirname(os.path.abspath(__file__))
RUNNER = "test_raw_http_probe_mutants.py"
BASE = gi_paths.scratch("raw-http-probe-mutants-control")
NL = chr(10)
BS = chr(92)
src = open(os.path.join(BENCH, RUNNER), encoding="utf-8", newline="").read()

OLD_LAYOUT = ('TAKES_SCRATCH = {"test_reply_golive_report.py",',
              'TAKES_SCRATCH = {"test_image_elf_sha.py", "test_reply_golive_report.py",')
SUMMARY = '    print("' + BS + 'n%d failure(s)" % len(FAILS))' + NL
PRINTS_FAIL = (SUMMARY, SUMMARY + '    print("FAIL planted by the meta-test")' + NL)
MUTANTS = [
    ("control's verdict ignored", "    control_ok = rc == 0 and not fails" + NL, "    control_ok = True" + NL),
    ("control's FAIL lines ignored", "    control_ok = rc == 0 and not fails" + NL, "    control_ok = rc == 0" + NL),
    ("control's exit ignored", "    control_ok = rc == 0 and not fails" + NL, "    control_ok = not fails" + NL),
    ("kills counted when the control fails", "        if dead and control_ok:" + NL, "        if dead:" + NL),
    ("a failed control does not fail the run", "    return 1 if survived or bad_controls else 0",
     "    return 1 if survived else 0"),
]


def run(label, text, test_edit=None):
    """The runner, as text, in a scratch copy of bench/, on the image_elf_sha suite."""
    d = os.path.join(BASE, re.sub(r"\W+", "_", label))
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(BENCH, d, ignore=shutil.ignore_patterns("__pycache__"))
    open(os.path.join(d, RUNNER), "w", encoding="utf-8", newline="").write(text)
    if test_edit:
        t = os.path.join(d, "test_image_elf_sha.py")
        tt = open(t, encoding="utf-8", newline="").read()
        assert tt.count(test_edit[0]) == 1, "the test's summary line moved"
        open(t, "w", encoding="utf-8", newline="").write(tt.replace(*test_edit))
    r = subprocess.run([sys.executable, "-B", os.path.join(d, RUNNER), os.path.join(d, "mut"), "image_elf_sha.py"],
                       capture_output=True, text=True, cwd=BENCH, env=dict(os.environ, PYTHONPATH=d))
    last = (r.stdout.strip().splitlines() or [""])[-1]
    shutil.rmtree(d, ignore_errors=True)
    return r.returncode, last, r.stdout


def broken_layout_caught(rc, last):
    return rc == 1 and last.startswith("0 of 6 killed") and "1 control(s) FAIL" in last


LAYOUTS = (("old layout", lambda t: t.replace(*OLD_LAYOUT), None), ("FAIL with exit 0", lambda t: t, PRINTS_FAIL))
ok = True
assert src.count(OLD_LAYOUT[0]) == 1
rc, last, out = run("as is", src)
good = rc == 0 and last == "6 of 6 killed; every control passes"
print("%s the runner as it is: exit %d, %s" % ("ok  " if good else "FAIL", rc, last))
ok &= good
for name, lay, edit in LAYOUTS:
    rc, last, out = run(name, lay(src), edit)
    good = broken_layout_caught(rc, last) and "CONTROL FAILED: image_elf_sha.py" in out
    print("%s %s: exit %d, %s" % ("ok  " if good else "FAIL", name, rc, last))
    ok &= good
for label, a, b in MUTANTS:
    assert src.count(a) == 1, label
    seen = []
    for name, lay, edit in LAYOUTS:
        rc, last, _ = run(label + " " + name, lay(src.replace(a, b)), edit)
        seen.append((name, rc, last, not broken_layout_caught(rc, last)))
    caught = any(x[3] for x in seen)
    print("%s mutant %s: %s" % ("caught" if caught else "MISSED", label,
                                "; ".join("%s: exit %d, %s" % x[:3] for x in seen)))
    ok &= caught
print(NL + "runner meta-test: %s" % ("ok" if ok else "FAILED"))
sys.exit(0 if ok else 1)
