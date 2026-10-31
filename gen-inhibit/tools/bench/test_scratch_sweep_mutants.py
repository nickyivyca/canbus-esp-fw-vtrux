#!/usr/bin/env python3
"""Mutants of scratch_sweep.py; test_scratch_sweep.py must fail on each BY A CHECK. A
crash is not a kill, and an edit that does not apply is an error. Each mutant is a copy
of scratch_sweep.py in this run's scratch/ folder with one edit, run through SWEEP_DIR.
Gen-inhibit tester, 2026-10-31.

    py -3.14 -B test_scratch_sweep_mutants.py
"""
import os
import subprocess
import sys

import gi_paths

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "scratch_sweep.py"
MUTANTS = [
    ("the protected folders walked", '            if rel == "." and e.name in PROTECTED:', "            if False:"),
    ("a run folder walked into, not judged", "                if is_run_folder(p, e.name):", "                if False:"),
    ("an owner file alone doesn't make a run folder",
     "    return bool(PID_NAME.search(name)) or os.path.exists(gi_paths.owner_path(path))",
     "    return bool(PID_NAME.search(name))"),
    ("the quiet hour ignored", "    if now - newest < quiet_s:", "    if False:"),
    ("a running pid in the name ignored", "    if gi_paths.process_start(int(m.group(1))) is not None:", "    if False:"),
    ("a running owner ignored", '    if state in ("running", "kept"):', '    if state == "kept":'),
    ("a kept owner ignored", '    if state in ("running", "kept"):', '    if state == "running":'),
    ("report only removes", "    if a.apply:", "    if True:"),
    ("--apply doesn't judge again", '                if judge(p, time.time(), quiet_s)[0] != "REMOVE":', "                if False:"),
    ("--apply leaves the owner file", "                    gi_paths._remove_owner(p)\n", ""),
    ("file counts leave out subfolders", "                            stack.append(e.path)", "                            pass"),
    ("--out not written", '([open(a.out, "w", encoding="utf-8")] if a.out else [])', "[]"),
    ("the report leaves out the protected folders",
     '"; protected, not walked: " + ", ".join(protected) if protected else ""', '""'),
    ("owner files counted as OTHER", "            elif not e.name.endswith(gi_paths.OWNER_SUFFIX):", "            else:"),
    ("a finished owner left", '        return "REMOVE", "owner finished"', '        return "LEAVE", "owner finished"'),
]


def run_test(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_scratch_sweep.py")], capture_output=True,
                       text=True, env=dict(os.environ, SWEEP_DIR=d), cwd=d)
    fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
    crashed = "Traceback" in r.stderr
    dead = bool(fails) and not crashed and not all(" CRASH " in f for f in fails)
    return dead, crashed, fails, r.returncode


def copy(base, name, text):
    d = os.path.join(base, name)
    os.makedirs(d)
    open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
    return d


def main():
    with gi_paths.run_dir("mutants-scratch_sweep") as base:
        return run(base)


def run(base):
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    dead, crashed, fails, rc = run_test(copy(base, "control", src))
    control_ok = rc == 0 and not fails
    print("%s control: the unmutated copy  (exit %d, %d FAIL)%s"
          % ("PASS    " if control_ok else "FAILED  ", rc, len(fails), "".join("\n           " + f[:130] for f in fails[:3])))
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        dead, crashed, fails, rc = run_test(copy(base, "m%d" % n, src.replace(old, new)))
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:130] for f in fails[:3])))
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
