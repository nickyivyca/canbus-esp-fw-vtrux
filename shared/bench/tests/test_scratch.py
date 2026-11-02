"""Tests of _scratch.py, where shared/bench's tests put their files. No hardware.

    python -B shared/bench/tests/test_scratch.py             every test
    python -B shared/bench/tests/test_scratch.py --can-fail  every test again
        under each mutant of _scratch.py, each of which must make one FAIL

The rule (AGENTS.md "Files and folders"; reviewer, 2026-10-24): each run gets
<worktree>/scratch/vtrux-bench-tests/<name>/<time>_<pid>, the worktree found by
walking up to the nearest .git; no worktree is a refusal, never a fall-back to
the system temp folder. The folder is removed when the process that made it
exits, pass or fail, read-only files and long paths included, and one that
will not go is printed (AGENTS.md "Removing scratch", 2026-11-01). Every test
runs in a subprocess with the _scratch.py under test first on its path, so a
mutant reaches the three tests' own imports. A mutant is an edited copy of
_scratch.py in this run's folder; an edit whose text is not found exactly once
counts as a failure, never a silent no-op. Two of the removal mutants fail
only on Windows, where a read-only file stops a plain rmtree. What a mutant
leaves (its run folders, or a folder written where it should not be) is
removed after it.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _scratch                                     # noqa: E402

PY = sys.executable
WORKTREE = _scratch.worktree_of(HERE)
ROOT = os.path.join(WORKTREE, "scratch", "vtrux-bench-tests")


def run_py(code, mdir):
    """code in a fresh interpreter with mdir (the _scratch.py under test) first
    on its path, then the tests folder; its last stdout line parsed as JSON. It runs
    in mdir, since -c puts the working folder ahead of PYTHONPATH."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([mdir, HERE, os.path.dirname(HERE)]))
    r = subprocess.run([PY, "-B", "-c", code], capture_output=True, text=True, env=env,
                       cwd=mdir, timeout=120)
    if r.returncode != 0:
        raise AssertionError("the probe failed: %s" % (r.stdout + r.stderr)[-400:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def under(p, root):
    p, root = os.path.normcase(os.path.abspath(p)), os.path.normcase(os.path.abspath(root))
    return p.startswith(root + os.sep)


def test_per_run(mdir):
    """Two runs started at once get two folders, each in the worktree's
    scratch/vtrux-bench-tests/<name>/, none in the system temp folder."""
    code = ("import json, os, _scratch; d = _scratch.run_dir('probe'); "
            "print(json.dumps([d, os.path.isdir(d)]))")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([mdir, HERE]))
    ps = [subprocess.Popen([PY, "-B", "-c", code], stdout=subprocess.PIPE, text=True, env=env,
                           cwd=mdir) for _ in range(2)]
    got = [json.loads(p.communicate(timeout=120)[0].strip().splitlines()[-1]) for p in ps]
    assert got[0][0] != got[1][0], got
    for (g, made), p in zip(got, ps):
        assert under(g, os.path.join(ROOT, "probe")), g
        assert not under(g, tempfile.gettempdir()), g
        assert made, g                          # there while the run was
        assert os.path.basename(g).endswith("_%d" % p.pid), (g, p.pid)


def test_removed_at_exit(mdir):
    """The run's folder goes when its process exits, with a read-only file and,
    on Windows, a path past 260 characters in it (AGENTS.md "Removing scratch")."""
    code = ("import json, os, stat, _scratch\n"
            "d = _scratch.run_dir('gone')\n"
            "f = os.path.join(d, 'ro.txt')\n"
            "open(f, 'w').write('x')\n"
            "os.chmod(f, stat.S_IREAD)\n"
            "deep = os.path.join(d, *(['x' * 50] * 5))\n"
            "lp = ('\\\\\\\\?\\\\' + deep) if os.name == 'nt' else deep\n"
            "os.makedirs(lp)\n"
            "open(os.path.join(lp, 'f.txt'), 'w').write('x')\n"
            "print(json.dumps([d, len(deep), os.path.isfile(f)]))\n")
    d, n, made = run_py(code, mdir)
    try:
        assert made and n > 260, (d, n)
        assert under(d, os.path.join(ROOT, "gone")), d
        assert not os.path.lexists(_scratch.long_path(d)), "left behind: %s" % d
    finally:
        _scratch.remove_tree(d)                 # what a mutant left; this file's own _scratch


def test_unremovable_printed(mdir):
    """A folder that will not go is printed with its path, not passed over."""
    code = ("import json, _scratch\n"
            "d = _scratch.run_dir('stuck')\n"
            "_scratch.shutil.rmtree = lambda *a, **k: None     # nothing can be removed\n"
            "print(json.dumps(d))\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([mdir, HERE, os.path.dirname(HERE)]))
    r = subprocess.run([PY, "-B", "-c", code], capture_output=True, text=True, env=env,
                       cwd=mdir, timeout=120)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    try:
        assert r.returncode == 0, r.stderr[-400:]
        assert "could not remove %s" % d in r.stderr, r.stderr[-400:]
    finally:
        _scratch.remove_tree(d)                 # this file's own _scratch, never a mutant


def test_walks_up(mdir):
    """The worktree is found by walking up to .git: from a folder deep inside
    scratch/ (where a pinned export sits) it is the same worktree."""
    deep = os.path.join(tempfile.mkdtemp(dir=_scratch.run_dir("walk")), "a", "b", "c")   # unique per call
    os.makedirs(deep)
    got = run_py("import json, _scratch; print(json.dumps([_scratch.worktree_of(%r), "
                 "_scratch.worktree_of(%r), _scratch.run_dir('walk', start=%r)]))"
                 % (HERE, deep, deep), mdir)
    assert os.path.normcase(got[0]) == os.path.normcase(WORKTREE), got
    assert os.path.normcase(got[1]) == os.path.normcase(WORKTREE), got
    assert under(got[2], os.path.join(ROOT, "walk")), got


def test_refuses_outside(mdir):
    """With no worktree above, run_dir refuses; it does not fall back."""
    outside = tempfile.mkdtemp(prefix="vtrux-scratch-outside-")   # in no worktree, removed below
    start = os.path.join(outside, "a", "b", "c", "d")   # deep, so a mutant that counts up stays inside
    os.makedirs(start)
    try:
        got = run_py("import json, _scratch\ntry:\n    r = _scratch.run_dir('x', start=%r)\n"
                     "except _scratch.ScratchError as e:\n    r = 'refused: ' + str(e)\n"
                     "print(json.dumps(r))" % start, mdir)
    finally:
        _scratch.remove_tree(outside)
    assert got.startswith("refused: ") and "no git worktree" in got, got


def test_three_tests(mdir):
    """test_bench_lock, test_decode and test_run_core each put their files in
    a run folder of their own name."""
    got = run_py("import json, test_bench_lock as a, test_decode as b, test_run_core as c; "
                 "print(json.dumps([a.SCRATCH, b.SCRATCH, c.SCRATCH]))", mdir)
    for p, name in zip(got, ("bench_lock", "decode", "run_core")):
        assert under(p, os.path.join(ROOT, name)), (name, p)
        assert not under(p, tempfile.gettempdir()), p


TESTS = [test_per_run, test_walks_up, test_refuses_outside, test_three_tests,
         test_removed_at_exit, test_unremovable_printed]

# The temp-folder mutant writes to this folder of its own there, removed after it,
# so no mutant leaves anything outside this run's scratch folder.
MUTANT_TEMP = "vtrux-scratch-mutant"

MUTANTS = [
    ("no per-run part",
     '"%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))', '"run")'),
    ("the system temp folder",
     'd = os.path.join(worktree_of(start or __file__), "scratch", "vtrux-bench-tests", name,',
     'import tempfile\n    d = os.path.join(tempfile.gettempdir(), "vtrux-scratch-mutant", name,'),
    ("counts up instead of finding .git",
     '        if os.path.exists(os.path.join(d, ".git")):\n            return d\n',
     '        return os.path.dirname(os.path.dirname(os.path.dirname(d)))\n'),
    ("falls back instead of refusing",
     '            raise ScratchError(', '            return os.path.abspath(path)\n            raise ScratchError('),
    ("not removed at exit",
     "    atexit.register(remove_tree, d)\n", ""),
    ("rmtree with ignore_errors (Windows only)",
     "    if sys.version_info >= (3, 12):\n        shutil.rmtree(lp, onexc=again)\n    else:\n"
     "        shutil.rmtree(lp, onerror=lambda func, p, ei: again(func, p, ei[1]))\n",
     "    shutil.rmtree(lp, ignore_errors=True)\n"),
    ("read-only not retried (Windows only)",
     "os.chmod(p, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(p) else 0))\n"
     "            func(p)",
     "raise OSError('not retried')"),
    # No mutant drops long_path's \\?\ form: on a Windows with LongPathsEnabled
    # (NICKY-XPS, 2026-11-01) a plain rmtree removes a path past 260 characters
    # too, so nothing here could fail on it. The test still puts one in the folder.
    ("a folder left is not printed",
     'print("could not remove %s: %s (%s)" % (path, why[1], why[0]), file=sys.stderr)', "pass"),
]


def run_all(mdir):
    bad = []
    for t in TESTS:
        try:
            t(mdir)
            print("ok    %s" % t.__name__)
        except Exception as e:
            print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:200]))
            bad.append(t.__name__)
    return bad


def run_folders():
    """Every <name>/<entry> under ROOT now."""
    got = set()
    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if os.path.isdir(os.path.join(ROOT, name)):
                got.update(os.path.join(ROOT, name, e) for e in os.listdir(os.path.join(ROOT, name)))
    return got


def main(argv):
    if "--can-fail" not in argv:
        bad = run_all(HERE)
        print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
        return 1 if bad else 0
    src = open(os.path.join(HERE, "_scratch.py"), encoding="utf-8").read()
    base = _scratch.run_dir("scratch_mutants")
    caught = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)" % (n, name, src.count(old)))
            continue
        mdir = os.path.join(base, "m%d" % n)
        os.makedirs(mdir)
        with open(os.path.join(mdir, "_scratch.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src.replace(old, new))
        print("-- m%d %s" % (n, name))
        before = run_folders()
        try:
            bad = run_all(mdir)
        finally:                                 # what the temp-folder mutant wrote
            _scratch.remove_tree(os.path.join(tempfile.gettempdir(), MUTANT_TEMP))
            for left in sorted(run_folders() - before):   # what a mutant left in ROOT
                _scratch.remove_tree(left)
        caught += bool(bad)
        print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                  ("fails " + ", ".join(bad)) if bad else "STILL PASSES -- nothing checks it"))
    print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
