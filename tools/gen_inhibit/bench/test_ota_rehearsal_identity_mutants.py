#!/usr/bin/env python3
"""Mutants of ota_rollback_rehearsal.py's unit and image reads; test_ota_rehearsal_identity.py
must fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply is an
error. The unmutated copy runs first as the control.

    py -3.14 -B tools/gen_inhibit/bench/test_ota_rehearsal_identity_mutants.py

Each mutant is a copy of ota_rollback_rehearsal.py in the worktree's scratch/, one edit
applied, run through REHEARSAL_DIR. Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_ota_rehearsal_identity.py")
TARGET = "ota_rollback_rehearsal.py"
UNIT_ROW = '        ("the connected AP is the expected unit (BSSID, value-blind)",\n         state.get("unit_matches") is True),\n'
MUTANTS = [
    (None, None, None),
    ("device_state asks /check_status first, by a built path", '    gi = http_json("/gen_inhibit")\n',
     '    http_json("/check_" + "status")\n    gi = http_json("/gen_inhibit")\n'),
    ("unit_check compares 10 digits", 'if got == want else (False', 'if got[:10] == want[:10] else (False'),
    ("unit_check passes with no reading", '    if got is None:\n        return False, why\n',
     '    if got is None:\n        return True, why\n'),
    ("unit_check takes a short expectation", '    if len(want) != 12:\n', '    if not want:\n'),
    ("unit_check names both values", '(False, "the connected BSSID does not match")',
     '(False, "the connected BSSID %s does not match %s" % (got, want))'),
    ("connected_bssid takes the first of two", '    if len(found) != 1:\n', '    if not found:\n'),
    ("connected_bssid misses the Windows 11 form", r'r"^\s*(?:AP )?BSSID\s*:\s*(\S+)"', r'r"^\s*BSSID\s*:\s*(\S+)"'),
    ("ota_precondition ignores the unit", UNIT_ROW + '        ("mode is 0", state["mode"] == 0),\n        ("not live", state["inhibit_live"] is False),\n        ("bus not ours"',
     '        ("the connected AP is the expected unit (BSSID, value-blind)",\n         True),\n'
     '        ("mode is 0", state["mode"] == 0),\n        ("not live", state["inhibit_live"] is False),\n        ("bus not ours"'),
    ("phase_verify ignores the unit", UNIT_ROW + '        ("reading is >= 60 s',
     '        ("the connected AP is the expected unit (BSSID, value-blind)",\n         True),\n        ("reading is >= 60 s'),
    ("phase_verify does not compare the image",
     'bool(good.get("elf_sha256")) and state.get("elf_sha256") == good.get("elf_sha256")', 'True'),
    ("device_state records the expected id", '        "unit_why": why,\n',
     '        "unit_why": why,\n        "unit": expect_device_id,\n'),
    ("image_facts reads no elf_sha256", 'version, elf = desc["version"], desc["elf_sha256"]',
     'version, elf = desc["version"], None'),
]


def main():
    base = gi_paths.scratch("mutants-ota_rehearsal_identity")
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
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, REHEARSAL_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout or "the module loads" in r.stdout
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
    shutil.rmtree(base, ignore_errors=True)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
