#!/usr/bin/env python3
"""Mutants of x2_hold.py's esptool use (gen-inhibit reviewer, 2026-11-11);
test_x2_hold.py must fail on each BY A CHECK. A crash of the test is not a kill, and an
edit that does not apply is an error.

    python3.13 -B gen-inhibit/tools/bench/test_x2_hold_mutants.py

Each mutant is a copy of x2_hold.py with one edit, in this run's scratch folder, beside
copies of the modules it loads from its own folder (gi_paths, gi_esptool,
x2_hold_inputs); the test runs it through X2_HOLD_DIR. The unmutated copy runs first, as
the control: if it fails, no mutant is judged and the run exits 1. Gen-inhibit tester,
2026-11-11.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders

SRC = "x2_hold.py"
TEST = "test_x2_hold.py"
BESIDE = ("gi_paths.py", "gi_esptool.py", "x2_hold_inputs.py")
MUTANTS = [
    ("pins and hold: the dashed read-mem", '"read_mem", "0x%08x" % addr)', '"read-mem", "0x%08x" % addr)'),
    ("release: the dashed read-mem", '"hard_reset", "read_mem"', '"hard_reset", "read-mem"'),
    ("the version is not checked", "        _ET.update(GE.checked())\n",
     '        _ET.update({"argv": GE.from_env(), "version": None})\n'),
    ("the record has no esptool argv or version",
     '           "esptool_argv": info["argv"], "esptool_version": info["version"]}', "           }"),
    ("a refusal goes on to the board",
     '        print("REFUSED: %s (nothing run, no record)" % e)\n        return 2\n',
     '        info = {"argv": None, "version": None}\n'),
    ("enable-inputs runs under x2_hold's python", 'cmd = [info["argv"][0], "-B", INPUTS,',
     'cmd = [sys.executable, "-B", INPUTS,'),
    ("enable-inputs is handed another pin", '"--pin", GE.PINNED]', '"--pin", "4.12.0"]'),
]


def main():
    base = gi_paths.scratch("mutants-x2_hold")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        for f in BESIDE:
            shutil.copyfile(os.path.join(HERE, f), os.path.join(d, f))
        with open(os.path.join(d, SRC), "w", encoding="utf-8", newline="") as f:
            f.write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, X2_HOLD_DIR=d), timeout=600)
        fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL ")]
        if old is None:
            ok = r.returncode == 0 and not fails and "Traceback" not in r.stderr
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ("; " + fails[0][:90]) if fails else ""))
            if not ok:
                if r.stderr.strip():
                    print("         stderr: %s" % r.stderr.strip().splitlines()[-1][:160])
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fails), ("; " + fails[0][:90]) if fails else ""))
        if not dead and r.stderr.strip():
            print("         stderr: %s" % r.stderr.strip().splitlines()[-1][:160])
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
