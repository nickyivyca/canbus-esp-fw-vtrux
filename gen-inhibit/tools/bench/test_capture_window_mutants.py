#!/usr/bin/env python3
"""Mutants of capture_window.py; test_capture_window.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    python3.13 -B gen-inhibit/tools/bench/test_capture_window_mutants.py   (py -3.14 on Windows)

Not run as a mutant on Windows: dropping the child's own process group. CTRL_BREAK would
then go to the console group this runner shares and stop the runner itself, so it cannot
be judged by the test. Gen-inhibit tester, 2026-10-20.

A mutant of one platform's branch (a 4th field, "nt" or "posix") is printed N/A on the
other and not counted, since that branch never runs there (2026-11-12, the reviewer's
Linux leftovers item 3). Off Windows the CTRL_BREAK line is N/A, and its POSIX
counterparts run instead: no SIGINT to the child's group, and no session of its own
(then os.killpg finds no group, and the child is killed after the grace).

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
    ("no break: the child is only killed", "            p.send_signal(signal.CTRL_BREAK_EVENT)\n", "            pass\n", "nt"),
    ("no SIGINT: the child is only killed", "            os.killpg(p.pid, signal.SIGINT)\n", "            pass\n", "posix"),
    ("the child in this runner's session (no group of its own)", '{"start_new_session": True}', "{}", "posix"),
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
    killed = errors = na = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, old, new, *where) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        if where and where[0] != os.name:       # the edit still has to apply (above)
            print("N/A      m%d %s  (%s only; this run is on %s)"
                  % (n, name, "Windows" if where[0] == "nt" else "POSIX", sys.platform))
            na += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
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
                gi_paths.remove_tree(base)
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
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors, %d not applicable on %s; the control passes"
          % (killed, len(MUTANTS) - na, errors, na, sys.platform))
    return 0 if killed == len(MUTANTS) - na and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
