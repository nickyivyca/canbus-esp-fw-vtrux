"""Tests the HARNESS: build_check_in_clone.py (user ruling, 2026-10-19:
build-check runs in a real git checkout at exactly the run's commit, from a
clean tree whose source digest equals the shipped copy's).

Builds a throwaway repo with two commits and a stub build_check.py that
only prints that it ran, bundles it, and exports HEAD as the "shipped
copy". The right commit and copy must run the stub; a plain (non-git) copy
given as the bundle, an unknown commit, a short SHA, an existing
destination, an older commit and a shipped copy with other sources must
each be refused with nothing run.

    py -3.14 charge-interposer/firmware/test/build_check_in_clone_test.py
    ... --can-fail   each mutant of the script must make this test fail
"""

import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_check_in_clone as BCC                  # noqa: E402

WORK = os.path.join(tempfile.gettempdir(), "interposer-tester",
                    "build-check-in-clone")
STUB = ("import sys\n"
        "print('STUB BUILD_CHECK RAN', ' '.join(sys.argv[1:]))\n")


def _rmtree(p):
    def unlock(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if os.path.exists(p):
        shutil.rmtree(p, onerror=unlock)


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                           "user.email=t@t"] + list(args), check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def fixture():
    """-> dict of paths and the two commits."""
    _rmtree(WORK)
    repo = os.path.join(WORK, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    fw = os.path.join(repo, "charge-interposer", "firmware")
    _write(os.path.join(fw, "src", "a.cpp"), "int a = 1;\n")
    _write(os.path.join(fw, "platformio.ini"), "[env:x]\n")
    _write(os.path.join(fw, "test", "build_check.py"), STUB)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    old = _git(repo, "rev-parse", "HEAD")
    _write(os.path.join(fw, "src", "a.cpp"), "int a = 2;\n")
    _git(repo, "commit", "-q", "-am", "two")
    new = _git(repo, "rev-parse", "HEAD")
    bundle = os.path.join(WORK, "repo.bundle")
    _git(repo, "bundle", "create", bundle, "HEAD")
    shipped = os.path.join(WORK, "shipped")
    shutil.copytree(os.path.join(repo, "charge-interposer"), shipped)
    edited = os.path.join(WORK, "shipped-edited")
    shutil.copytree(shipped, edited)
    _write(os.path.join(edited, "firmware", "src", "a.cpp"), "int a = 3;\n")
    return dict(repo=repo, old=old, new=new, bundle=bundle, shipped=shipped,
                edited=edited)


def call(fx, name, bundle=None, commit=None, shipped=None, dest=None,
         extra=("--no-build",)):
    dest = dest or os.path.join(WORK, "dest-" + name)
    argv = ["--bundle", bundle or fx["bundle"],
            "--commit", commit or fx["new"],
            "--shipped", shipped or fx["shipped"], "--dest", dest,
            "--"] + list(extra)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = BCC.main(argv)
    return rc, out.getvalue()


def run():
    """-> list of failures (empty: the script behaves). The stub's own
    output goes to the subprocess's stdout, so 'it ran' is judged by its
    exit code 0 against the refusal's 2, and a refusal must say so."""
    fx = fixture()
    bad = []
    rc, out = call(fx, "good")
    if rc != 0:
        bad.append("the right commit and copy did not run build_check: rc "
                   "%s, %s" % (rc, out.strip()[-200:]))
    os.makedirs(os.path.join(WORK, "dest-exists"))
    refusals = [
        ("a plain copy as the bundle", dict(bundle=fx["shipped"])),
        ("an unknown commit", dict(commit="0" * 40)),
        ("a short SHA", dict(commit=fx["new"][:10])),
        ("an existing destination",
         dict(dest=os.path.join(WORK, "dest-exists"))),
        ("an older commit than the shipped copy", dict(commit=fx["old"])),
        ("a shipped copy with other sources", dict(shipped=fx["edited"])),
    ]
    for name, kw in refusals:
        rc, out = call(fx, name.replace(" ", "_"), **kw)
        if rc != BCC.REFUSED or "REFUSED" not in out:
            bad.append("%s: expected a refusal, got rc %s: %s"
                       % (name, rc, out.strip()[-200:]))
    return bad


def _plain_copy(bundle, commit, dest):
    """The e7d4 behaviour: build in a plain copy, no git."""
    src = bundle if os.path.isdir(bundle) else os.path.join(
        os.path.dirname(bundle), "shipped")
    shutil.copytree(src, os.path.join(dest, "charge-interposer"))
    return []


def _bundle_head(bundle, commit, dest):
    """Clone and keep the bundle's HEAD, whatever commit was asked for."""
    r = subprocess.run(["git", "clone", "-q", bundle, dest],
                       capture_output=True, text=True)
    return [] if r.returncode == 0 else ["clone failed"]


MUTANTS = [
    ("plain copy, no git checks",
     [("prepare_tree", _plain_copy), ("verify_tree", lambda d, c: [])]),
    ("bundle HEAD, unverified",
     [("prepare_tree", _bundle_head), ("verify_tree", lambda d, c: [])]),
    ("sources not compared", [("same_sources", lambda s, d: [])]),
]


def main(argv):
    if "--can-fail" not in argv:
        bad = run()
        for b in bad:
            print("FAIL  " + b)
        print("\n%s -- 1 run and 6 refusals" % ("FAIL" if bad else "OK"))
        return 1 if bad else 0
    missed = 0
    for label, patches in MUTANTS:
        saved = [(a, getattr(BCC, a)) for a, _f in patches]
        for a, f in patches:
            setattr(BCC, a, f)
        try:
            bad = run()
        finally:
            for a, v in saved:
                setattr(BCC, a, v)
        print("%s  %-28s %s" % ("ok  " if bad else "FAIL", label,
                                ("fails (" + bad[0][:90] + ")") if bad else
                                "STILL PASSES -- the test checks nothing"))
        missed += not bad
    print("\n%d of %d mutants caught" % (len(MUTANTS) - missed, len(MUTANTS)))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
