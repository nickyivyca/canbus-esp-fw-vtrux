#!/usr/bin/env python3
"""Mutants of wrong_bitrate_errors.py's per-burst count (2026-11-20, gen-inhibit reviewer's answer
to the run (C) report, item 4(ii): each burst counted over burst_measures.burst_window);
test_wrong_bitrate_errors.py must fail on each BY A CHECK. A crash is not a kill, and an edit that
does not apply is an error. The unmutated copy runs first as the control.

    python3.13 -B gen-inhibit/tools/bench/test_wrong_bitrate_errors_mutants.py

Each mutant is a copy of wrong_bitrate_errors.py in the worktree's scratch/, one edit applied,
run through WRONG_BITRATE_DIR. A second or two a mutant. Gen-inhibit tester, 2026-11-20.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_wrong_bitrate_errors.py")
TARGET = "wrong_bitrate_errors.py"
PB = ("    return [len(BM.in_window(errors, BM.burst_window(burst_starts, burst_closes, k)))\n"
      "            for k in range(len(burst_starts))]\n")
MUTANTS = [
    (None, None, None),
    ("the last burst's window 0.5 s from its start, as before",
     PB, "    return [sum(1 for e in errors if t <= e < (burst_starts[k + 1] if k + 1 < len(burst_starts)\n"
         "            else t + 0.5)) for k, t in enumerate(burst_starts)]\n"),
    ("every window runs to the next start, ignoring the close",
     PB, "    return [sum(1 for e in errors if t <= e < (burst_starts[k + 1] if k + 1 < len(burst_starts)\n"
         "            else burst_closes[k] + 0.5)) for k, t in enumerate(burst_starts)]\n"),
    ("the window closed at its end",
     PB, "    return [sum(1 for e in errors if BM.burst_window(burst_starts, burst_closes, k)[0] <= e\n"
         "            <= BM.burst_window(burst_starts, burst_closes, k)[1]) for k in range(len(burst_starts))]\n"),
    ("the window open at its start",
     PB, "    return [sum(1 for e in errors if BM.burst_window(burst_starts, burst_closes, k)[0] < e\n"
         "            < BM.burst_window(burst_starts, burst_closes, k)[1]) for k in range(len(burst_starts))]\n"),
    ("main counts against the starts as closes",
     "per_burst(st, [c_ for _s, _ms, c_ in starts], errs)", "per_burst(st, st, errs)"),
    ("the last burst dropped", "            for k in range(len(burst_starts))]\n",
     "            for k in range(len(burst_starts) - 1)] + [0] * bool(burst_starts)\n"),
]


def main():
    base = gi_paths.scratch("mutants-wrong_bitrate_errors")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate(MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, WRONG_BITRATE_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
