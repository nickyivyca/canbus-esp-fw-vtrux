#!/usr/bin/env python3
"""Mutants of capture_window.py; test_capture_window.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_capture_window_mutants.py

Not run as a mutant: dropping the child's own process group. CTRL_BREAK would then go
to the console group this runner shares and stop the runner itself, so it cannot be
judged by the test. Gen-inhibit tester, 2026-10-20.
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
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, CAPTURE_WINDOW=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = any("ran without raising" not in f for f in fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fails), ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
