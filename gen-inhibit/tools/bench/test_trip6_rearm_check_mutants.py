#!/usr/bin/env python3
"""Mutants of trip6_rearm_check.py's expired-arm judge (judge_expired, went_live) and its arm line
(the gen-inhibit reviewer's rulings on the run (C) fixes, 2026-11-20: live then aborted other than
by trip 6 is as specified, its abort in the arm line; never live and live then trip 6 are NOT);
test_trip6_rearm_check.py must fail on each BY A CHECK. A crash is not a kill, and an edit that
does not apply is an error. The unmutated copy runs first as the control.

    python3.13 -B gen-inhibit/tools/bench/test_trip6_rearm_check_mutants.py

Each mutant is a copy of trip6_rearm_check.py in the worktree's scratch/, one edit applied, run
through TRIP6_DIR. A few seconds a mutant. Gen-inhibit tester, 2026-11-20.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_trip6_rearm_check.py")
TARGET = "trip6_rearm_check.py"
MUTANTS = [
    (None, None, None),
    ("a new error frame since the arm does not void", "    if errors_since_arm:\n        return \"VOID",
     "    if False:\n        return \"VOID"),
    ("trip 6 on an expired burst passes",
     "    if any(r.get(\"abort\") == TRIP6 for r in reads):\n        return \"NOT as specified: trip 6",
     "    if False:\n        return \"NOT as specified: trip 6"),
    ("never live passes", "    if not went_live(reads, device_0x051):\n", "    if False:\n"),
    ("any abort is NOT, as before the ruling",
     "        return \"NOT as specified: never live\"\n    return \"as specified\"\n",
     "        return \"NOT as specified: never live\"\n"
     "    if any(r.get(\"abort\") for r in reads):\n        return \"NOT as specified: aborted\"\n"
     "    return \"as specified\"\n"),
    ("live only from a read live", "    return (any(r.get(\"live\") for r in reads) or bool(device_0x051)\n"
     "            or any(r.get(\"mode\") == 3",
     "    return (any(r.get(\"live\") for r in reads)\n            or False and any(r.get(\"mode\") == 3"),
    ("the witness's 0x051 not counted as live", " or bool(device_0x051)\n", "\n"),
    ("tx_ok in any mode counts as live", "any(r.get(\"mode\") == 3 and isinstance(r.get(\"tx_ok\"), int)",
     "any(isinstance(r.get(\"tx_ok\"), int)"),
    ("tx_ok in mode 3 not counted as live", "any(r.get(\"mode\") == 3 and isinstance(r.get(\"tx_ok\"), int)",
     "any(False and isinstance(r.get(\"tx_ok\"), int)"),
    ("the run voids on every burst breach, as before the ruling",
     "    burst_line = BM.run_void_line([arm[\"burst_measure\"]", "    burst_line = BM.breach_line([arm[\"burst_measure\"]"),
    ("the burst's source not read", "W.burst(a.burst_ms, inno, read_source=True)",
     "W.burst(a.burst_ms, inno) + ({},)"),
    ("the arm line drops the aborts", "                 (round(lives[0] - arm[\"armed_at\"], 3) if lives else None), aborts,",
     "                 (round(lives[0] - arm[\"armed_at\"], 3) if lives else None), [],"),
]


def main():
    base = gi_paths.scratch("mutants-trip6_rearm_check")
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
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, TRIP6_DIR=d),
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
