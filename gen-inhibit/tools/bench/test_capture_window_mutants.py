#!/usr/bin/env python3
"""Mutants of capture_window.py; test_capture_window.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_capture_window_mutants.py

Not run as a mutant: dropping the child's own process group. CTRL_BREAK would then go
to the console group this runner shares and stop the runner itself, so it cannot be
judged by the test. Gen-inhibit tester, 2026-10-20.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. A FAIL line the test writes for a crash it caught ("ran
without raising") kills nothing; since 2026-10-30 nor does any FAIL line beside a
traceback.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "capture_window.py"
TEST = "test_capture_window.py"
MUTANTS = [
    ("the stop file is not looked at", "        if os.path.exists(stopfile):\n", "        if False:\n"),
    ("no break: the child is only killed", "            p.send_signal(signal.CTRL_BREAK_EVENT)\n", "            pass\n"),
    ("a capture stopped by the max bound exits 0",
     'return 0 if rec["how"] == "stopfile" and', 'return 0 if rec["how"] in ("stopfile", "max") and'),
    ("a stop file left over is accepted",
     "    if os.path.exists(a.stopfile):\n        ap.error", "    if False:\n        ap.error"),
    ("a killed child counts as clean", "        p.kill()\n        p.wait()\n        return False\n",
     "        p.kill()\n        p.wait()\n        return True\n"),
    ("the start is taken after the stop request is possible",
     '    rec = {"argv": argv, "t_start": time.time(),', '    rec = {"argv": argv, "t_start": time.time() + 5,'),
]


def main():
    base = gi_paths.scratch("mutants-capture_window")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, CAPTURE_WINDOW=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            sys.stdout.flush()
            continue
        dead = any("ran without raising" not in f for f in fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                    len(fails), ", CRASHED" if crashed else "",
                                                    ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
