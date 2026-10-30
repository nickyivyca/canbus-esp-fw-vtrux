#!/usr/bin/env python3
"""Mutants of ota_rollback_rehearsal.py's log-level-none changes (port step 1a2; gen-inhibit
reviewer, 2026-10-30); test_ota_rehearsal_none.py must fail on each BY A CHECK. A crash is
not a kill, and an edit that does not apply is an error. The unmutated copy runs first, as
the control: if it fails, no mutant is judged and the run exits 1.

    py -3.14 -B gen-inhibit/tools/bench/test_ota_rehearsal_none_mutants.py

Each mutant is a copy of ota_rollback_rehearsal.py in the worktree's scratch/, one edit
applied, loaded by the test through REHEARSAL_DIR. A second or two each. Gen-inhibit
tester, 2026-10-30.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_ota_rehearsal_none.py")
TARGET = "ota_rollback_rehearsal.py"
BOOT_START = 'if k == "rom" or (k == "reset" and not (cur and cur["banner"] == ["rom"])):'
MUTANTS = [
    ("boots keyed on the bootloader's Loaded line again", BOOT_START, 'if k == "boot_load":'),
    ("every rst: line starts a boot", BOOT_START, 'if k == "rom" or k == "reset":'),
    ("an rst: line never starts a boot", BOOT_START, 'if k == "rom":'),
    ("the ESP-ROM: line is not an event", "        if RE_ROM.search(text):\n", "        if False:\n"),
    ("bootloader lines not counted", '        elif k == "bootloader_log":\n            cur["bootloader_lines"] += 1\n',
     '        elif k == "bootloader_log":\n            pass\n'),
    ("control: the reboot's verdict not required", 'rb.get("verdict") == "PASS"),', "True),"),
    ("control: marked valid read from the page before the reboot",
     'bool(good) and rb.get("found_sha") == good),', 'bool(good) and after.get("elf_sha256") == good),'),
    ("control: up time not required", '(after.get("uptime_ms") or 0) >= GATE_SETTLED_MS),', "True),"),
    ("control: the change row never asked", "    if require_change:\n", "    if False:\n"),
    ("fault: back on the good image not required",
     '("back on the good image afterwards (page elf_sha256)",\n         bool(good) and after.get("elf_sha256") == good),',
     '("back on the good image afterwards (page elf_sha256)",\n         bool(good)),'),
    ("fault: the good image before not required", 'bool(good) and before.get("elf_sha256") == good),', "True),"),
    ("fault: the faulty and good images not compared", "bool(good) and bool(faulty) and good != faulty),", "True),"),
    ("fault: any gap up to 75 s", "gap is not None and 55.0 <= gap <= 75.0),", "gap is not None and gap <= 75.0),"),
    ("_do_ota never reboots", "    if reboot and after is not None:\n", "    if False:\n"),
    ("run_ota_phase reboots no phase", "reboot=judger is judge_control)", "reboot=False)"),
    ("run_ota_phase judges restore as control", 'rows = judger(rec, require_change=label == "control")',
     "rows = judger(rec)"),
    ("reboot_check wants no image", '"--known", "good=%s" % good_sha, "--want", "good"]',
     '"--known", "good=%s" % good_sha, "--first", "good"]'),
    ("reboot_check asks with no hash", "    if not (isinstance(good_sha, str) and len(good_sha) == 64):\n",
     "    if False:\n"),
    ("the bootloader's level is its least verbose marker", 'found[0] if found else "none of the markers"',
     'found[-1] if found else "none of the markers"'),
    ("the appended hash left out of the length", "    if data[23] == 1:\n        off += 32\n",
     "    if False:\n        off += 32\n"),
    ("no checksum alignment", "    off += 15 - off % 16 + 1\n", "    off += 1\n"),
    ("phase_bootloader records no device bootloader", '        rec["device_bootloader"] = dev\n',
     "        pass\n"),
]


def main():
    base = gi_paths.scratch("mutants-ota_rehearsal_none")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
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
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
