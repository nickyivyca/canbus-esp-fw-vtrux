#!/usr/bin/env python3
"""Mutants of batt_voltage_log.py's park; test_batt_voltage_log.py must fail on each BY A
CHECK. An edit that does not apply is an error. The unmutated copy runs first as the control.

    py -3.14 -B tools/gen_inhibit/bench/test_batt_voltage_log_mutants.py

Each mutant is a copy of batt_voltage_log.py in the worktree's scratch/, one edit applied,
imported by the test from there (BATT_DIR). Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_batt_voltage_log.py")
TARGET = "batt_voltage_log.py"
REFUSE = ('    print("REFUSED: batt_voltage_log is parked: %s. Nothing was requested or written." % PARKED)\n'
          '    return 2\n')
MUTANTS = [
    (None, None, None),
    ("main reads and logs as before", REFUSE, "    return read_and_log()\n"),
    ("main refuses but exits 0", REFUSE,
     '    print("REFUSED: batt_voltage_log is parked: %s. Nothing was requested or written." % PARKED)\n    return 0\n'),
    ("the refusal gives no reason", REFUSE, '    print("REFUSED.")\n    return 2\n'),
    ("main asks the page before refusing", REFUSE,
     '    import lowid_flood_skip_bench as LF\n    LF.dev_get("/check_status")\n' + REFUSE),
]


def main():
    base = gi_paths.scratch("mutants-batt_voltage_log")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate(MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, BATT_DIR=d),
                           capture_output=True, text=True, timeout=120)
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
    shutil.rmtree(base, ignore_errors=True)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
