#!/usr/bin/env python3
"""Mutants of the firmware repo's test/build_checks/test_prod_build_image_real.py;
each must make the harness report FAIL BY A CHECK on a prod_build.py that is
correct. A crash is not a kill, and an edit that does not apply is an error.

    python test_prod_build_image_real_mutants.py --repo <clone at a fixed prod_build.py> [--harness PATH]

Run it with ESP-IDF exported, as the harness needs (prod_image_real_run.ps1 does
that: -Harness <this file>). Each mutant is a copy of the harness in the machine's
temp folder with one edit, run on one case against --repo; every case is a real
reconfigure and build. The control run on the parent of the fix shows the
verdict checks failing; these show the checks that guard the harness itself.
Afterwards, run the harness's plain case again: the mutants clear the clone's
record. Gen-inhibit tester, 2026-10-21.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

DEFAULT_HARNESS = os.path.join(gi_paths.repo_root(), "test", "build_checks", "test_prod_build_image_real.py")
MUTANTS = [
    ("the hook fires but makes no change", "no-desc",
     "        self.fired += 1\n", "        self.fired += 1\n        return\n"),
    ("the hook is never installed", "stray",
     "    mod.subprocess.run = hooked\n", "    mod.subprocess.run = real_run\n"),
    ("the case leaves a file in the tree", "no-key",
     "        self.moved = []\n\n",
     "        self.moved = []\n        open(os.path.join(os.path.dirname(self.bdir), LEFT), \"w\").close()\n\n"),
]
# The file mutant 3 leaves in the clone; removed after every mutant.
LEFT = "gi_mutant_left_in_tree.txt"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--harness", default=DEFAULT_HARNESS)
    a = ap.parse_args()
    src = open(a.harness, encoding="utf-8", newline="").read()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "prod_image_real")
    killed = errors = 0
    for n, (name, case, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)), flush=True)
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, os.path.basename(a.harness))
        open(path, "w", encoding="utf-8", newline="").write(
            src.replace(old, new).replace("STRAY = ", "LEFT = %r\nSTRAY = " % LEFT, 1))
        r = subprocess.run([sys.executable, "-u", path, "--repo", a.repo, "--cases", case],
                           capture_output=True, text=True)
        left = os.path.join(a.repo, LEFT)
        if os.path.exists(left):
            os.remove(left)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "Traceback" in r.stdout
        dead = bool(fails) and not crashed and r.returncode == 1
        killed += dead
        print("%s m%d %s, case %s  (exit %d, %d FAIL%s)%s" % (
            "KILLED  " if dead else "SURVIVED", n, name, case, r.returncode, len(fails),
            ", CRASHED" if crashed else "", "".join("\n           " + f for f in fails)), flush=True)
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
