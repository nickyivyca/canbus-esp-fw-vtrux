#!/usr/bin/env python3
"""Mutants of the 2026-10-20 batch in the baseline scripts; test_baseline_capture_stop.py
must fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply is
an error.

    py -3.14 -B gen-inhibit/tools/bench/test_baseline_capture_stop_mutants.py

Run from the reverse-it root. Each mutant is a copy of this folder in the worktree's
scratch/, with the five scripts from BATCH_SRC (default: this folder) and one edit, run
through the test's BATCH_DIR. The whole folder, since the scripts run tools beside them
($A, capture_window.py among them): a copy of the five alone failed the same five checks
with no edit at all. The unmutated copy runs first, as the control: if it fails a check,
no kill counts. About 25 minutes. Gen-inhibit tester, 2026-10-20.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("BATCH_SRC") or HERE
FILES = ["baseline_b21.sh", "baseline_b_noX2.sh", "baseline_b_jam.sh", "x2_release_identify.sh",
         "baseline_b_otagroup.sh"]
TEST = "test_baseline_capture_stop.py"
MUTANTS = [
    ("B21: the stop file is never written", "baseline_b21.sh", '  : > "$stop"\n', ""),
    ("B21: no tail after the probe", "baseline_b21.sh", '  sleep $TAIL\n  : > "$stop"\n', '  : > "$stop"\n'),
    ("B21: the stop file is left behind", "baseline_b21.sh", '  rm -f "$stop"\n', ""),
    ("B21: dumpcap loses its own bound", "baseline_b21.sh", '-a duration:$CAPMAX -w "$cap"', '-w "$cap"'),
    ("B21: the probe window is not recorded", "baseline_b21.sh",
     '  echo "{\\"probe_start\\": $t0, \\"probe_end\\": $t1, \\"tail_s\\": $TAIL}" > "$D/B21_probe_window_${body}_$stamp.json"\n', ""),
    ("B11raw: no tail after the arm", "baseline_b_noX2.sh",
     '        sleep $TAIL\n        : > "$D/$n.stop"\n', '        : > "$D/$n.stop"\n'),
    ("B11raw: the stop file is left behind", "baseline_b_noX2.sh", '        rm -f "$D/$n.stop"\n', ""),
    ("B11raw: the capture record named like an e4 record", "baseline_b_noX2.sh",
     '--record "$D/capture_$n.json"', '--record "$D/$n.capture.json"'),
    ("jam: the dump record's ls unquoted again", "baseline_b_jam.sh",
     'rec=$(ls -t "${GI_RUNS_DIR:-$DATA/runs}"/x2_flash_custody_dump_*.json', 'rec=$(ls -t ${GI_RUNS_DIR:-$DATA/runs}/x2_flash_custody_dump_*.json'),
    ("jam: no stop on a missing dump record", "baseline_b_jam.sh",
     '    [ -n "$rec" ] || { echo "no dump record in ${GI_RUNS_DIR:-$DATA/runs}: stop"; exit 1; }\n', ""),
    ("jam: the hold record's mv unquoted again", "baseline_b_jam.sh",
     'mv "${GI_RUNS_DIR:-$DATA/runs}"/x2_hold_hold_*.json', 'mv ${GI_RUNS_DIR:-$DATA/runs}/x2_hold_hold_*.json'),
    ("release: the release record's mv unquoted again", "x2_release_identify.sh",
     'mv "${GI_RUNS_DIR:-$DATA/runs}"/x2_hold_release_*.json', 'mv ${GI_RUNS_DIR:-$DATA/runs}/x2_hold_release_*.json'),
    ("otagroup: rt_self back", "baseline_b_otagroup.sh",
     '      inert rt present --switch GI_TEST_RESTART_QUIESCE\n',
     '      inert rt present --switch GI_TEST_RESTART_QUIESCE\n      ota rt_self "$(img rt)"\n'),
    ("otagroup: no page read at the end of rt", "baseline_b_otagroup.sh",
     '      py -3.14 -B $A/page_state.py --label "B12 rt end" --out "$D/B12_rt_end_page.json"; echo "rt end page exit $?"\n', ""),
    ("otagroup: the page read before test_only_inert", "baseline_b_otagroup.sh",
     '      inert rt present --switch GI_TEST_RESTART_QUIESCE\n',
     '      py -3.14 -B $A/page_state.py --label "B12 rt early" --out "$D/B12_rt_end_page.json"\n'
     '      inert rt present --switch GI_TEST_RESTART_QUIESCE\n'),
]


def main():
    base = gi_paths.scratch("mutants-capture_stop")
    srcs = {}
    for f in FILES:
        raw = open(os.path.join(SRC_DIR, f), encoding="utf-8", newline="").read()
        srcs[f] = (raw.replace("\r\n", "\n"), "\r\n" if "\r\n" in raw else "\n")
    killed = errors = 0

    def run(label, target=None, old=None, new=None):
        # The whole of bench/, since the scripts run tools from their own folder ($A), and
        # the fake py runs capture_window.py from there for real; then the five scripts.
        d = os.path.join(base, label)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__"))
        for f, (t, nl) in srcs.items():
            if f == target:
                t = t.replace(old, new)
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, BATCH_DIR=d.replace("\\", "/")), timeout=1800)
        return r, [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]

    # The unmutated copy first: if it fails a check, every mutant would be killed by it.
    r, fails = run("control")
    control_ok = r.returncode == 0 and not fails
    print("%s control: the unmutated copy  (exit %d, %d FAIL)%s" % (
        "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails),
        "".join("\n           " + f[:130] for f in fails[:3])))
    sys.stdout.flush()
    for n, (name, target, old, new) in enumerate(MUTANTS, 1):
        if srcs[target][0].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, srcs[target][0].count(old)))
            errors += 1
            continue
        r, fails = run("m%d" % n, target, old, new)
        dead = any("ran without raising" not in f for f in fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fails), ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
