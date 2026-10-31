#!/usr/bin/env python3
"""Mutants of the arms scripts' exit codes; test_arms_exit.py must fail on each BY A
CHECK. A crash or a time-out is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_arms_exit_mutants.py

Each mutant is a copy of the three scripts (with bench_images.sh and capture_window.py,
which they call from their own folder) in the worktree's scratch/, one edit applied, run
through BATCH_DIR. About 2 minutes a mutant. Gen-inhibit tester, 2026-10-25.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_arms_exit.py")
NOX2, B3, HAND = "baseline_b_noX2.sh", "baseline_b3_golive.sh", "baseline_b_hand.sh"
FILES = (NOX2, B3, HAND, "bench_images.sh", "capture_window.py")
COUNT = "[ $r -eq 0 ] || ARMS_FAILED=$((ARMS_FAILED + 1))"
MUTANTS = [
    ("noX2: an e4 arm is not counted", NOX2, '  echo "  exit $r"\n  ' + COUNT, '  echo "  exit $r"\n  true'),
    ("noX2: a capture is not counted", NOX2, 'echo "  capture exit $r"\n        ' + COUNT, 'echo "  capture exit $r"\n        true'),
    ("noX2: no exit at the end", NOX2,
     '[ "$ARMS_FAILED" -eq 0 ] || { echo "$ARMS_FAILED arm(s) or capture(s) exited non-zero"; exit 1; }', ""),
    ("noX2: stops at the first failed arm", NOX2, '  echo "  exit $r"\n  ' + COUNT,
     '  echo "  exit $r"\n  [ $r -eq 0 ] || { echo "exited non-zero"; exit 1; }'),
    ("noX2: B4 counted as an arm", NOX2, '      echo "B4 exit $?"',
     '      r=$?; echo "B4 exit $r"; [ $r -eq 0 ] || ARMS_FAILED=1'),
    ("B3: an arm is not counted", B3, '    echo "  exit $r"\n    ' + COUNT, '    echo "  exit $r"\n    true'),
    ("B3: no exit at the end", B3, '[ "$ARMS_FAILED" -eq 0 ] || { echo "$ARMS_FAILED arm(s) exited non-zero"; exit 1; }', ""),
    ("B3: stops at the first failed arm", B3, '    echo "  exit $r"\n    ' + COUNT,
     '    echo "  exit $r"\n    [ $r -eq 0 ] || { echo "exited non-zero"; exit 1; }'),
    ("hand: a B2 arm is not counted", HAND, '  echo "$n exit $r"\n  ' + COUNT, '  echo "$n exit $r"\n  true'),
    ("hand: no exit at the end", HAND,
     '[ "$ARMS_FAILED" -eq 0 ] || { echo "$ARMS_FAILED B2 arm(s) exited non-zero"; exit 1; }', ""),
    ("hand: stops at the first failed arm", HAND, '  echo "$n exit $r"\n  ' + COUNT,
     '  echo "$n exit $r"\n  [ $r -eq 0 ] || { echo "exited non-zero"; exit 1; }'),
]


def main():
    base = gi_paths.scratch("mutants-arms_exit")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, target, old, new) in enumerate([("the control: the unmutated copy", None, None, None)] + MUTANTS):
        if old is not None and srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f, s in srcs.items():
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(s.replace(old, new) if f == target else s)
        try:
            r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, BATCH_DIR=d),
                               capture_output=True, text=True, timeout=900)
            out, err, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            out, err, rc = "", "TIMED OUT", None
        fails = [l.strip() for l in out.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in err or "TIMED OUT" in err
        if old is None:
            ok = rc == 0 and not fails and not crashed
            print("%s m0 %s  (exit %s, %d FAIL%s%s)" % (
                "PASS    " if ok else "FAILED  ", name, rc, len(fails),
                ", CRASHED: " + err.strip()[-120:] if crashed else "", ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            sys.stdout.flush()
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %s, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
            ", CRASHED: " + err.strip()[-120:] if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
