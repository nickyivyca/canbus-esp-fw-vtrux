#!/usr/bin/env python3
"""Test that the tests in this folder write only under the worktree's scratch/.

Five of them made their throwaway trees beside themselves (_test_*_tmp/), in the source
tree, where a crash left them behind: a _test_sources_row_tmp holding a .git was found
there (port step 0d finding 6, 2026-10-26). AGENTS.md "Files and folders" puts such work
in the worktree's gitignored scratch/.

Each test here is run in its own interpreter with an audit hook (sys.addaudithook) that
records every path it opens for writing, makes, removes, renames, copies to or rmtree's.
Then:

  1. Every test exits 0 under the hook.
  2. Every path it wrote is under <worktree>/scratch/ (the worktree of the folder under
     test: the folder's parent's parent's parent), or the null device.
  3. Each test that makes a folder (the five moved, test_image_rows, test_prod_record)
     has at least one write seen under scratch/, so the hook saw it at all.
  4. The folder's tree (every file and folder in it, __pycache__ aside) is the same
     after the runs as before, which also covers what child processes leave behind.
  5. Controls, planted scripts against a planted scratch/: a file written, a folder
     made and an rmtree outside it are each flagged; writes inside it are seen and not
     flagged; a script's exit 1 is reported.

Not run: test_refusal_pairs.py (a command line over real image pairs, exit 1 without
them) and test_reference_paths_mutants.py (a mutant runner whose test runs in child
processes; its folder is under scratch/ already). Child processes are not audited:
check 4 is what covers them.

    py -3.14 -B gen-inhibit/test/build_checks/test_scratch_only.py
    py -3.14 -B gen-inhibit/test/build_checks/test_scratch_only.py --mutants

--mutants copies this folder (and firmware/vtrux_checks.json) into a scratch tree once
per moved test, puts that test's folder back beside it, and runs this test on the copy
(BUILD_CHECKS_DIR, --only). Gen-inhibit tester, 2026-10-26.
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_output import WORKTREE                                   # noqa: E402

BC = os.path.abspath(os.environ.get("BUILD_CHECKS_DIR") or HERE)    # the folder under test
ALLOWED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(BC))), "scratch")
SKIP = {"test_scratch_only.py", "test_refusal_pairs.py", "test_reference_paths_mutants.py"}
MUST_WRITE = {"test_build_switches.py", "test_flt_row.py", "test_fw_version_row.py", "test_partition_row.py",
              "test_switch_rows.py", "test_image_rows.py", "test_prod_record.py", "test_source_rows.py",
              "test_refusal_tables.py"}
FAILS = []

# Run in the child: install the hook, run the target as __main__, write what was seen.
BOOT = r'''
import os, runpy, sys
target, log = sys.argv[1], sys.argv[2]
seen, busy = [], [False]
W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
def hook(ev, args):
    if busy[0]:
        return
    p = None
    if ev == "open":
        path, mode, flags = args
        if (mode and any(c in mode for c in "wax+")) or (mode is None and flags & W):
            p = path
    elif ev in ("os.mkdir", "os.remove", "os.rmdir", "shutil.rmtree"):
        p = args[0]
    elif ev in ("os.rename", "shutil.copyfile", "shutil.copytree", "shutil.move"):
        p = args[1]
    if p is None or isinstance(p, int):
        return
    seen.append((ev, os.path.abspath(os.fsdecode(p))))
sys.addaudithook(hook)
sys.argv = [target]
sys.path.insert(0, os.path.dirname(target))
code = 0
try:
    runpy.run_path(target, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
busy[0] = True
with open(log, "w", encoding="utf-8") as fh:
    fh.writelines("%s\t%s\n" % s for s in seen)
sys.exit(code)
'''


def check(name, ok, detail=""):
    print("  %-4s %-72s %s" % ("ok" if ok else "FAIL", name, "" if ok else repr(detail)[:240]))
    if not ok:
        FAILS.append(name)


def run_audited(target, log, env=None):
    """(exit, [(event, absolute path)]) of target run as __main__ under the hook."""
    r = subprocess.run([sys.executable, "-B", "-c", BOOT, target, log], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, cwd=os.path.dirname(target), env=env, timeout=600)
    seen = []
    if os.path.exists(log):
        seen = [tuple(l.rstrip("\n").split("\t", 1)) for l in open(log, encoding="utf-8") if "\t" in l]
    return r.returncode, seen


def under(p, root):
    p, root = os.path.normcase(os.path.abspath(p)), os.path.normcase(os.path.abspath(root))
    return p.startswith(root + os.sep)


def outside(seen, root):
    null = os.path.normcase(os.path.abspath(os.devnull))
    return [(e, p) for e, p in seen if not under(p, root) and os.path.normcase(p) != null]


def tree(d):
    """Every file and folder under d, relative, __pycache__ left out."""
    out = set()
    for dp, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x != "__pycache__"]
        out |= {os.path.relpath(os.path.join(dp, x), d) for x in dns + fns}
    return out


def own_scratch():
    d = os.path.join(WORKTREE, "scratch", "build-checks", "test_scratch_only",
                     "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d)
    return d


PLANTED = {
    "file": 'open(os.path.join(OUT, "f.txt"), "w").close()',
    "folder": 'os.makedirs(os.path.join(OUT, "d"), exist_ok=True)',
    "rmtree": 'shutil.rmtree(os.path.join(OUT, "gone"), ignore_errors=True)',
}


def controls(base):
    inside, out = os.path.join(base, "planted", "scratch"), os.path.join(base, "planted", "outside")
    os.makedirs(inside)
    os.makedirs(out)
    env = dict(os.environ, OUT=out, IN=inside)
    head = "import os, shutil, subprocess, sys\nOUT, IN = os.environ['OUT'], os.environ['IN']\n"
    for name, line in sorted(PLANTED.items()):
        p = os.path.join(base, "planted", "p_%s.py" % name)
        open(p, "w").write(head + line + "\n")
        rc, seen = run_audited(p, os.path.join(base, "planted_%s.log" % name), env)
        check("5 control: %s outside the planted scratch/ is flagged" % name, rc == 0 and outside(seen, inside),
              (rc, seen))
    p = os.path.join(base, "planted", "p_inside.py")
    open(p, "w").write(head + 'os.makedirs(os.path.join(IN, "d"))\nopen(os.path.join(IN, "d", "f"), "w").close()\n'
                              'shutil.rmtree(os.path.join(IN, "d"))\n')
    rc, seen = run_audited(p, os.path.join(base, "planted_inside.log"), env)
    check("5 control: writes inside the planted scratch/ are seen and not flagged",
          rc == 0 and len(seen) >= 3 and not outside(seen, inside), (rc, seen))
    p = os.path.join(base, "planted", "p_exit1.py")
    open(p, "w").write("import sys\nsys.exit(1)\n")
    rc, _ = run_audited(p, os.path.join(base, "planted_exit1.log"))
    check("5 control: a script's exit 1 is reported", rc == 1, rc)


def checks(only):
    targets = sorted(f for f in os.listdir(BC) if f.startswith("test_") and f.endswith(".py") and f not in SKIP)
    if only:
        targets = [t for t in targets if t in only]
    check("the folder's tests are found (%d)" % len(targets), len(targets) >= (len(only) if only else 9), targets)
    base = own_scratch()
    try:
        before = tree(BC)
        for t in targets:
            rc, seen = run_audited(os.path.join(BC, t), os.path.join(base, t + ".log"))
            check("1 %s exits 0 under the hook" % t, rc == 0, rc)
            check("2 %s writes only under scratch/" % t, not outside(seen, ALLOWED), outside(seen, ALLOWED)[:3])
            if t in MUST_WRITE:
                n = sum(1 for _, p in seen if under(p, ALLOWED))
                check("3 %s: its writes under scratch/ are seen (%d)" % (t, n), n >= 1, n)
        after = tree(BC)
        check("4 the folder's tree is the same after the runs", after == before,
              (sorted(after - before)[:5], sorted(before - after)[:5]))
        if not only:
            controls(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return FAILS


MOVED = [
    ("test_build_switches.py", 'TMP = os.path.join(WORKTREE, "scratch", "build-checks", "test_build_switches")',
     'TMP = os.path.join(HERE, "_test_build_switches_tmp")'),
    ("test_flt_row.py", 'TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_flt_row")',
     'TMP = os.path.join(HERE, "_test_flt_row_tmp")'),
    ("test_fw_version_row.py", 'TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_fw_version_row")',
     'TMP = os.path.join(HERE, "_test_fw_version_tmp")'),
    ("test_partition_row.py", 'TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_partition_row")',
     'TMP = os.path.join(HERE, "_test_partition_tmp")'),
    ("test_switch_rows.py", 'TMP = os.path.join(BO.WORKTREE, "scratch", "build-checks", "test_switch_rows")',
     'TMP = os.path.join(HERE, "_test_switch_rows_tmp")'),
]


def mutants():
    base = own_scratch()
    killed = errors = 0
    try:
        for n, (fname, old, new) in enumerate(MOVED, 1):
            src = open(os.path.join(BC, fname), encoding="utf-8", newline="").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, fname, src.count(old)))
                errors += 1
                continue
            proj = os.path.join(base, "m%d" % n, "gen-inhibit")
            d = os.path.join(proj, "test", "build_checks")
            shutil.copytree(BC, d, ignore=shutil.ignore_patterns("__pycache__"))
            os.makedirs(os.path.join(proj, "firmware"))
            shutil.copy(os.path.join(os.path.dirname(os.path.dirname(BC)), "firmware", "vtrux_checks.json"),
                        os.path.join(proj, "firmware"))
            open(os.path.join(d, fname), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--only", fname], capture_output=True,
                               text=True, env=dict(os.environ, BUILD_CHECKS_DIR=d))
            fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            dead = any("2 %s writes only under scratch/" % fname in f for f in fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s's folder beside it again%s" % ("KILLED  " if dead else "SURVIVED", n, fname,
                                                            "".join("\n           " + f[:150] for f in fails[:2])))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MOVED), errors))
    return 0 if killed == len(MOVED) and not errors else 1


def main():
    args = sys.argv[1:]
    if "--mutants" in args:
        return mutants()
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else set()
    got = checks(only)
    print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
    return 1 if got else 0


if __name__ == "__main__":
    sys.exit(main())
