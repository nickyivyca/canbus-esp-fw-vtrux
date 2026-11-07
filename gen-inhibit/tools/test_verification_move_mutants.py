#!/usr/bin/env python3
"""Mutants of verification_move.py; test_verification_move.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/test_verification_move_mutants.py

Each mutant is a copy of the script in the worktree's scratch/, one edit applied, run by the test
through VMOVE_SCRIPT. The unmutated copy runs first, as the control: if it fails, no mutant is
judged and the run exits 1. Gen-inhibit tester, 2026-11-07.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders

SRC = "verification_move.py"
TEST = "test_verification_move.py"
MUTANTS = [
    ("CR in the target allowed",
     '    if "\\r" in target:\n', '    if False:\n'),
    ("no final newline allowed",
     '    if target and not target.endswith("\\n"):\n', '    if False:\n'),
    ("a target stopping inside a section allowed",
     '        elif not src[len(tgt)].startswith("## "):\n', '        elif False:\n'),
    ("--complete ignored",
     '        if complete:\n', '        if False:\n'),
    ("a longer target allowed",
     '    if len(tgt) > len(src):\n', '    if False:\n'),
    ("lines not compared",
     '    diff = [i + 1 for i in range(min(len(tgt), len(src))) if tgt[i] != src[i]]\n',
     '    diff = []\n'),
    ("a pattern may match any number of times",
     '        if found != s["count"]:\n', '        if found == 0:\n'),
    ("a pattern that matches nothing allowed",
     '        if found != s["count"]:\n', '        if found > s["count"]:\n'),
    ("substitutions not applied",
     '        src[s["line"] - 1] = after\n', '        pass\n'),
    ("a substitution that changes nothing allowed",
     '        if after == before:\n', '        if False:\n'),
    ("the new text read as a template (group references expanded)",
     '        after = s["rx"].sub(lambda m: s["new"], before, count=s["count"])\n',
     '        after = s["rx"].sub(s["new"], before, count=s["count"])\n'),
    ("an empty target allowed",
     '    if not tgt:\n        problems.append("the target is empty")\n    elif',
     '    if False:\n        pass\n    elif'),
    ("why not required",
     '        if not isinstance(why, str) or not why.strip():\n', '        if False:\n'),
    ("unknown keys allowed",
     '        if extra:\n', '        if False:\n'),
    ("line range not checked",
     '        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= n_source:\n',
     '        if not isinstance(line, int):\n'),
    ("a two-line new allowed",
     '        if not isinstance(new, str) or "\\n" in new or "\\r" in new:\n',
     '        if not isinstance(new, str):\n'),
    ("count 0 allowed",
     '        if not isinstance(count, int) or isinstance(count, bool) or count < 1:\n',
     '        if not isinstance(count, int):\n'),
    ("a problem does not change the exit code",
     '    return 1 if problems else 0\n', '    return 0\n'),
]


def main():
    base = gi_paths.scratch("mutants-verification_move")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, VMOVE_SCRIPT=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
