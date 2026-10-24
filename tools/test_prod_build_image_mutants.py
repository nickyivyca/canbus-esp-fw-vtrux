#!/usr/bin/env python3
"""Mutants of prod_build.py's image pick; test_prod_build_image.py must fail
each BY A CHECK. A crash (an ERROR line) is not a kill, and an edit that does
not apply is an error.

    python tools/test_prod_build_image_mutants.py

Each mutant copies prod_build.py into a fresh folder in this run's folder
under scratch/gen-inhibit-prodbuild/, applies one edit, and runs the test
against the copy with --script.
Implementor self-test, not evidence.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "prod_build.py")
TEST = os.path.join(HERE, "test_prod_build_image.py")
# AGENTS.md "Files and folders": this worktree's scratch/, never the system
# temp folder. One folder per run, so two runs at once never share one.
SCRATCH = os.path.join(os.path.dirname(HERE), "scratch", "gen-inhibit-prodbuild")

# (name, old, new, the case that must FAIL)
MUTANTS = [
    ("back to the newest .bin by mtime",
     "    return built_image()\n",
     "    bdir = os.path.join(REPO, \"build\")\n"
     "    cands = [os.path.join(bdir, f) for f in os.listdir(bdir)\n"
     "             if f.startswith(\"wican-fw_obd_\") and f.endswith(\".bin\")]\n"
     "    return max(cands, key=os.path.getmtime) if cands else None\n",
     "stray"),
]


def main():
    src = open(SRC, encoding="utf-8", newline="").read()
    os.makedirs(SCRATCH, exist_ok=True)
    run = tempfile.mkdtemp(prefix="image-mutants-", dir=SCRATCH)
    killed = 0
    for name, old, new, case in MUTANTS:
        if src.count(old) != 1:
            print("[ERROR] %s: the edit does not apply (%d matches)"
                  % (name, src.count(old)))
            shutil.rmtree(run, ignore_errors=True)
            return 2
        work = tempfile.mkdtemp(dir=run)
        mutant = os.path.join(work, "prod_build.py")
        with open(mutant, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.replace(old, new))
        r = subprocess.run([sys.executable, TEST, "--script", mutant],
                           capture_output=True, text=True)
        lines = r.stdout.splitlines()
        by_check = any(ln.startswith("[FAIL] " + case) for ln in lines)
        crashed = any(ln.startswith("[ERROR]") for ln in lines)
        ok = r.returncode == 1 and by_check and not crashed
        killed += ok
        print("[%s] %s (case %s%s)" % ("KILLED" if ok else "SURVIVED", name,
                                       case, ", crashed" if crashed else ""))
        if not ok:
            print("\n".join("         " + ln for ln in lines))
    shutil.rmtree(run, ignore_errors=True)
    print("%d of %d mutants killed" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
