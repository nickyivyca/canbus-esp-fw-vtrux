#!/usr/bin/env python3
"""Tests that mutate.py removes its scratch folder when it ends, pass or fail
(AGENTS.md, "Removing scratch"), and refuses a folder it did not make. No
compiler needed: the builds are stood in for.

    python3 shared/twai_port/test/test_mutate.py [--module DIR]
    python3 shared/twai_port/test/test_mutate.py --mutants

--module tests the mutate.py in DIR instead of the one beside this file.
--mutants breaks mutate.py's removal one way at a time, in copies under the
worktree's scratch/, and passes only if this test fails each by a check. Some
of them only fail on Windows (a read-only file, a long path), and are reported
as not judged elsewhere.

Prints "ok ..." or "FAIL ..." per check and PASS or FAIL at the end; exit 0
only on PASS.
"""
import argparse
import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
WIN = os.name == "nt"
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def lp(p):
    p = os.path.abspath(p)
    return "\\\\?\\" + p if WIN and not p.startswith("\\\\?\\") else p


def cleanup(path):
    """This test's own removal, independent of the mutate.py under test, so a mutant
    leaves nothing behind. A folder that will not go is printed with the reason."""
    def again(func, p, exc):
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        func(p)
    if os.path.lexists(lp(path)):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(lp(path), onexc=again)
            else:
                shutil.rmtree(lp(path), onerror=lambda f, p, ei: again(f, p, ei[1]))
        except OSError as e:
            print("could not remove %s: %s" % (path, e), file=sys.stderr)


def load(d):
    spec = importlib.util.spec_from_file_location("mutate_under_test", os.path.join(d, "mutate.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def plant(d):
    """What a run might leave in its folder: a read-only file, and a file under a path
    longer than Windows' 260 characters."""
    ro = os.path.join(d, "readonly.o")
    with open(ro, "w") as f:
        f.write("x")
    os.chmod(ro, stat.S_IREAD)
    deep = os.path.join(d, *(["d" * 40] * 7))
    os.makedirs(lp(deep))
    with open(lp(os.path.join(deep, "deep.c")), "w") as f:
        f.write("x")
    return len(os.path.join(deep, "deep.c"))


def call(M, argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = M.main(argv)
    except Exception as e:
        rc = e
    return rc, out.getvalue(), err.getvalue()


def tests(M, base):
    # remove_tree on its own
    t = os.path.join(base, "tree")
    os.makedirs(t)
    n = plant(t)
    ok = M.remove_tree(t)
    check("remove_tree: a tree with a read-only file and a %d-character path is removed" % n,
          ok is True and not os.path.lexists(lp(t)), (ok, os.path.lexists(lp(t))))
    check("remove_tree: a path that is not there is not an error", M.remove_tree(t) is True)
    if WIN:
        # needed only where Windows' long paths are off (they are on, on NICKY-XPS), so
        # checked directly too
        x = M.long_path("x")
        check("long_path: absolute, in the \\\\?\\ form, and not prefixed twice",
              x == "\\\\?\\" + os.path.abspath("x") and M.long_path(x) == x, x)

    # a folder that cannot be removed is printed with the reason, not passed over
    t = os.path.join(base, "stuck")
    os.makedirs(os.path.join(t, "sub"))
    held = os.path.join(t, "sub", "held.txt")
    with open(held, "w") as f:
        f.write("x")
    judged, why = True, ""
    if WIN:
        h = open(held)   # Windows will not delete an open file
        undo = h.close
    else:
        sub = os.path.join(t, "sub")
        os.chmod(sub, stat.S_IREAD | stat.S_IEXEC)

        def undo():
            if os.path.isdir(sub):
                os.chmod(sub, stat.S_IRWXU)
        try:   # root, or a mount that ignores modes (WSL's /mnt/c), can still write there
            open(os.path.join(sub, "probe"), "w").close()
            judged, why = False, "a folder's mode does not stop writes here (root, or a mount that ignores modes)"
        except OSError:
            pass
    if judged:
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ok = M.remove_tree(t)
        finally:
            undo()
        check("remove_tree: a folder it cannot remove: False, and the folder and a reason printed",
              ok is False and os.path.lexists(t) and ("could not remove %s" % t) in err.getvalue()
              and len(err.getvalue().split(": ", 1)[-1].strip()) > 0, (ok, err.getvalue()))
    else:
        undo()
        print("not judged: remove_tree's printed failure (%s)" % why)
    cleanup(t)

    # main's folder: made by the run, removed at its end, pass or fail
    real = M.build_and_run
    try:
        out = os.path.join(base, "run_fail")
        seen = {}

        def leaves(cc, src, d, name):
            if name == "clean":
                seen["n"] = plant(d)
                seen["made"] = os.path.isdir(d)
            return "did not build"
        M.build_and_run = leaves
        rc, so, se = call(M, ["--out", out, "--cc", "cc"])
        check("main: a failing run (the unmutated build did not build) exits 1",
              rc == 1 and "unmutated: did not build" in so, (rc, so[-300:], se))
        check("main: the run made its folder, and it is gone after the run, read-only and long paths included",
              seen.get("made") is True and not os.path.lexists(lp(out)), (seen, os.path.lexists(lp(out))))
        cleanup(out)

        out = os.path.join(base, "run_pass")
        M.build_and_run = lambda cc, src, d, name: "pass" if name == "clean" else "fail"
        rc, so, se = call(M, ["--out", out])
        check("main: a passing run exits 0 and its folder is gone",
              rc == 0 and "all killed" in so and not os.path.lexists(lp(out)), (rc, so[-200:], se))
        cleanup(out)

        out = os.path.join(base, "run_raise")

        def raises(cc, src, d, name):
            plant(d)
            raise FileNotFoundError("no compiler named %s" % cc)
        M.build_and_run = raises
        rc, so, se = call(M, ["--out", out])
        check("main: a run that raises still removes its folder, and the error is not swallowed",
              isinstance(rc, FileNotFoundError) and not os.path.lexists(lp(out)), (rc, os.path.lexists(lp(out))))
        cleanup(out)

        out = os.path.join(base, "theirs")
        os.makedirs(out)
        keep = os.path.join(out, "keep.txt")
        with open(keep, "w") as f:
            f.write("someone else's")
        M.build_and_run = lambda *a: (_ for _ in ()).throw(AssertionError("ran in an existing folder"))
        rc, so, se = call(M, ["--out", out])
        check("main: a --out that exists is refused (exit 2), named, and left as it was",
              rc == 2 and out in se and open(keep).read() == "someone else's", (rc, se))
        cleanup(out)
    finally:
        M.build_and_run = real


# (name, text in mutate.py, its replacement, Windows only)
MUTANTS = [
    ("removed only when the run returns",
     "    try:\n        return run_mutants(a)\n    finally:\n        remove_tree(a.out)",
     "    rc = run_mutants(a)\n    remove_tree(a.out)\n    return rc", False),
    ("never removed", "    finally:\n        remove_tree(a.out)", "    finally:\n        pass", False),
    ("ignore_errors=True", "shutil.rmtree(lp, onexc=again)", "shutil.rmtree(lp, ignore_errors=True)", True),
    ("no retry after clearing read-only",
     "            os.chmod(p, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(p) else 0))\n            func(p)",
     "            raise OSError(\"not retried\")", True),
    ("no long-path form", 'if os.name == "nt" and not p.startswith', 'if False and not p.startswith', True),
    ("a failed removal is silent",
     '        print("could not remove %s: %s (%s)" % (path, why[1], why[0]), file=sys.stderr)\n', "", False),
    ("a failed removal reports success", "        return False\n    return True", "        return True\n    return True",
     False),
    ("an existing --out is used", "    if os.path.lexists(a.out):\n        print(", "    if False:\n        print(", False),
    ("an existing --out is emptied first", "    if os.path.lexists(a.out):\n        print(",
     "    if os.path.lexists(a.out) and remove_tree(a.out) and False:\n        print(", False),
]


def run_mutants(base):
    src = open(os.path.join(HERE, "mutate.py"), encoding="utf-8").read()
    if sys.version_info < (3, 12):
        print("the mutants need Python 3.12 or later (onexc)")
        return 1
    killed = judged = errors = 0
    for i, (name, old, new, win_only) in enumerate([("the control: the unmutated copy", None, None, False)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: its text occurs %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % i)
        os.makedirs(d)
        with open(os.path.join(d, "mutate.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(src if old is None else src.replace(old, new))
        shutil.copy(os.path.join(HERE, "twai_fake.c"), d)   # mutate.py reads it from beside itself
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--module", d],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        nfail = sum(1 for l in r.stdout.splitlines() if l.startswith("FAIL "))
        crashed = "Traceback" in r.stdout
        cleanup(d)
        if old is None:
            if r.returncode != 0 or nfail or crashed:
                print("FAILED   m0 %s (exit %d, %d FAIL)\n%s\nthe control fails: no mutant is judged"
                      % (name, r.returncode, nfail, r.stdout[-800:]))
                return 1
            print("PASS     m0 %s" % name)
            continue
        if win_only and not WIN:
            print("NOT JUDGED m%d %s (fails only on Windows)" % (i, name))
            continue
        judged += 1
        dead = nfail > 0 and not crashed
        killed += dead
        print("%s m%d %s (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", i, name, r.returncode, nfail,
                                                 ", CRASHED" if crashed else ""))
    print("\n%d of %d judged killed by a check, %d errors; the control passes" % (killed, judged, errors))
    return 0 if killed == judged and not errors else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--module", default=HERE)
    ap.add_argument("--mutants", action="store_true")
    a = ap.parse_args()
    base = os.path.join(ROOT, "scratch", "twai_port-test_mutate%s" % ("-mutants" if a.mutants else "-%d" % os.getpid()))
    if os.path.lexists(base):
        print("%s already exists (left by an earlier run?): remove it first" % base)
        return 2
    os.makedirs(base)
    try:
        if a.mutants:
            return run_mutants(base)
        try:
            tests(load(a.module), base)
        except Exception as e:
            check("the tests ran to the end", False, repr(e))
            raise
    finally:
        cleanup(base)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
