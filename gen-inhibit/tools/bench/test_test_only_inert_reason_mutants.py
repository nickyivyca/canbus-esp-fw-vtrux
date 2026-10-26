#!/usr/bin/env python3
"""Mutants of test_only_inert's page-key record and spacing (page_key_result,
REASON_FIELDS, wait_before; reviewer, 2026-10-17); test_test_only_inert.py must
fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply
is an error.

    py -3.14 tools/gen_inhibit/bench/test_test_only_inert_reason_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "test_only_inert.py"
TEST = "test_test_only_inert.py"

MUTANTS = [
    ("the reasons are not recorded", 'out["reasons"] = {f: p.get(f) for f in REASON_FIELDS}', 'out["reasons"] = {}'),
    ("the reasons are not read from the page", 'out["reasons"] = {f: p.get(f) for f in REASON_FIELDS}',
     'out["reasons"] = {f: None for f in REASON_FIELDS}'),
    ("arm_block is not a reason field", 'REASON_FIELDS = ("arm_block", ', 'REASON_FIELDS = ('),
    ("abort_reason is not a reason field", '"abort_reason", "abort_latched"', '"abort_latched"'),
    ("the live flag is read from the mode", 'out = {"http_status": st, "live": bool(p.get("inhibit_live")),',
     'out = {"http_status": st, "live": bool(p.get("mode")),'),
    ("a non-object page is used as one", "    if not isinstance(p, dict):\n        p = {}\n", ""),
    ("no spacing at all", "return gap_s if index > 0 else 0.0", "return 0.0"),
    ("spacing before the first switch too", "return gap_s if index > 0 else 0.0", "return gap_s"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-inert_reason_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
