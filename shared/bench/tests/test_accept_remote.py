"""Tests of the acceptance runner's remote trees (vtrux_bench/accept/trees.py,
compile.py, scratch.py's remote folders): ACCEPT-SPEC A4 ("a tree is local
or on a remote host the adapter knows"), A7 and A14. No hardware. Each test
names what it checks.

    python -B shared/bench/tests/test_accept_remote.py            every test
    python -B shared/bench/tests/test_accept_remote.py --can-fail  the
        control first, then each test with a rule it guards switched off

The remote host is this machine through tests/loop_ssh.py, which runs each
ssh line with bash here (Git Bash on Windows): the host's workroot is a
scratch folder, named the way bash names it, and what the runner does there
-- ship a bundle, clone, check, run, remove -- really happens. Everything is
in this run's own folder under the worktree's scratch/ (tests/_scratch.py),
removed at the end with vtrux_bench/scratch.py.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import builds as B           # noqa: E402
from vtrux_bench.accept import canfail as CF         # noqa: E402
from vtrux_bench.accept import compile as C          # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402
from vtrux_bench.run import engine as E              # noqa: E402
from vtrux_bench.run import procs as P               # noqa: E402

BASE = _scratch.run_dir("accept_remote")
N = [0]
PY = sys.executable
LOOP = (PY, os.path.join(HERE, "loop_ssh.py"), "-o", "BatchMode=yes")
WINDOWS = os.name == "nt"


def bash_path(p):
    """p as bash names it (C:\\x -> /c/x on Windows)."""
    p = os.path.abspath(p)
    if WINDOWS:
        return "/%s%s" % (p[0].lower(), p[2:].replace("\\", "/"))
    return p


def local_path(bp):
    """The other way."""
    if WINDOWS and bp.startswith("/") and len(bp) > 2 and bp[2] == "/":
        return "%s:%s" % (bp[1].upper(), bp[2:].replace("/", "\\"))
    return bp


FILES = {
    "proj/envcheck.py": (
        "import os, sys\n"
        "here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "t = os.environ.get('FAKE_TREE', '')\n"
        "print('here', here, 'FAKE_TREE', t)\n"
        "sys.exit(0 if t and os.path.samefile(t, here) else 1)\n"),
    "proj/build.py": "pass\n",
    "proj/check.py": "pass\n",
    "proj/suite.py": "pass\n",
    "proj/mutants.toml": '[[mutant]]\nid = "m"\nfile = "proj/check.py"\nold = "pass"\nnew = "x"\n',
    "shared/comp/tool.py": "pass\n",
}

LIST = '''
[step]
id = "s1"
project = "proj"
ordered_by = "reviewer"
ordered_on = "2026-11-01"
spec = ["9.1"]
verification = "9.1 Emulation"

[trees.far]
project = "{C1}"
host = "far"

[[check]]
id = "remote"
tree = "far"
run = ["proj/envcheck.py"]
{EXTRA}'''


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Fixture:
    def __init__(self, extra=""):
        N[0] += 1
        self.dir = os.path.join(BASE, "f%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel, text in FILES.items():
            write(self.repo, rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "C1")
        self.c1 = git(self.repo, "rev-parse", "HEAD")
        FA.PINS.clear()
        write(self.repo, "proj/acceptance/s.toml", LIST.format(C1=self.c1, EXTRA=extra))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "list")
        self.alist = L.load(self.repo, "%s:proj/acceptance/s.toml"
                            % git(self.repo, "rev-parse", "HEAD"), FA)
        runs = os.path.join(self.dir, "runs")
        os.makedirs(runs)
        self.rec = R.start(runs, "s1", out=lambda s: None, ceiling=self.dir)
        self.made = S.Made()
        self.work = os.path.join(self.dir, "work")
        self.far = os.path.join(self.dir, "far")
        os.makedirs(self.far)
        self.settings = {"runs_root": os.path.join(self.dir, "unused"),
                         "hosts": {"local": {"python": [PY]},
                                   "far": {"ssh": "farhost", "workroot": bash_path(self.far),
                                           "python": [PY.replace("\\", "/")]}}}

    def trees(self):
        return T.make_trees(self.repo, self.alist, FA, self.work, self.made, say=self.rec.say,
                            remote={"settings": self.settings, "record_id": self.rec.id})


class Loop:
    """ssh is loop_ssh.py while the block runs."""

    def __enter__(self):
        self.saved = E.SSH, P.run_remote
        real = P.run_remote
        E.SSH = LOOP
        P.run_remote = lambda *a, **k: real(*a, **dict(k, ssh=LOOP))
        return self

    def __exit__(self, *exc):
        E.SSH, P.run_remote = self.saved


# ---- the tests --------------------------------------------------------------
def test_remote_tree():
    """A4: a tree on a remote host is a clone there at exactly its commit,
    made from a bundle of git objects, checked there (HEAD and status), in
    the host's workroot under the acceptance record's id; its record is
    the tree's as made locally."""
    with Loop():
        f = Fixture()
        t = f.trees()["far"]
    assert t["remote"] and t["host"] == "far", t
    rdir = local_path(t["project_dir"])
    assert rdir.startswith(f.far) and f.rec.id in rdir, rdir
    assert git(rdir, "rev-parse", "HEAD") == f.c1
    assert git(rdir, "status", "--porcelain", "--untracked-files=all") == ""
    with open(os.path.join(rdir, "proj", "envcheck.py")) as fh:
        assert "FAKE_TREE" in fh.read()
    assert t["record"]["project"]["commit"] == f.c1 and t["record"]["remote"]["host"] == "far"


def test_remote_check_refuses():
    """A4: the remote checkout is refused unless its HEAD is the commit and
    it has no change."""
    with Loop():
        f = Fixture()
        t = f.trees()["far"]
        rdir = local_path(t["project_dir"])
        write(rdir, "proj/check.py", "changed\n")
        try:
            T.remote_check_tree("farhost", t["project_dir"], f.c1)
        except T.TreeError as e:
            assert "check.py" in str(e), e
        else:
            raise AssertionError("a remote checkout with a change was taken")


def test_remote_check_runs():
    """A7: a check on a remote tree runs on that host, in its checkout,
    told its own (remote) tree by the adapter's tree_env, through the chain
    runner's remote lane."""
    with Loop():
        f = Fixture()
        trees = f.trees()
        suite = C.compile_suite(f.alist, trees, FA, f.settings, C.machine_env(FA))
        g = suite.groups[0]
        assert g.host == "far" and g.checks[0].cwd == trees["far"]["project_dir"], (g.host, g.checks[0].cwd)
        assert g.checks[0].argv[0] == f.settings["hosts"]["far"]["python"][0], g.checks[0].argv
        # the host is posix: its paths have no backslash (Git Bash would
        # forgive one; madhouse would not)
        assert "\\" not in g.checks[0].argv[-1] and "\\" not in g.checks[0].cwd, g.checks[0].argv
        rec = C.run_suite(suite, f.settings, f.rec, adapter=FA, made=f.made)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    log = open(os.path.join(rec["_dir"], "remote", "run", "check.log"), encoding="utf-8").read()
    assert v == {"remote/run": "PASS"}, (v, log[-600:])


def test_remote_removed():
    """A14: what the run made on a remote host -- its trees and the chain
    runner's own work folder there -- goes with the run's Made; a remote
    folder that will not go is a LEFTOVER line naming it."""
    with Loop():
        f = Fixture()
        trees = f.trees()
        suite = C.compile_suite(f.alist, trees, FA, f.settings, C.machine_env(FA))
        rec = C.run_suite(suite, f.settings, f.rec, adapter=FA, made=f.made)
        tree_dir = os.path.dirname(local_path(trees["far"]["project_dir"]))
        chain_work = os.path.join(f.far, rec["run_id"])
        assert os.path.isdir(tree_dir) and os.path.isdir(chain_work), (tree_dir, chain_work)
        held = None
        if WINDOWS:
            held = open(os.path.join(local_path(trees["far"]["project_dir"]), "proj", "check.py"))
        try:
            said = []
            left = f.made.remove_all(out=said.append)
        finally:
            if held:
                held.close()
        if WINDOWS:
            assert left and any("far" in p for p, _w in left), left
            assert any("LEFTOVER" in s for s in said), said
            left = f.made.remove_all(out=lambda s: None)
        assert left == [], left
        assert not os.path.exists(tree_dir) and not os.path.exists(chain_work), \
            os.listdir(f.far)


def test_remote_not_yet():
    """A4, A5, A10: what is not built for remote trees yet is refused before
    anything runs, saying so: a build, a mutant from committed text, and a
    suite run on a remote tree."""
    for extra, call, word in (
            ('[[build]]\nid = "fw"\ntree = "far"\ntarget = "fw"\n',
             lambda f, t: B.build_suite(f.repo, f.alist, FA, f.settings, f.made, f.work,
                                        {}, f.rec.say, trees=t), "build"),
            ('[[canfail]]\nid = "cf"\ntarget = "remote"\nmutants = { from = '
             '"proj/mutants.toml" }\nexpect = "fails"\n',
             lambda f, t: CF.prepare(f.repo, f.alist, t, FA, f.work, f.made, f.rec.say),
             "canfail"),
            ('[[suite_run]]\nid = "emu"\ntree = "far"\nsuite = "emulated"\n',
             lambda f, t: C.compile_suite(f.alist, t, FA, f.settings, {}, paths={
                 "settings": "s", "masked": "m", "record_id": "r"}), "suite run")):
        with Loop():
            f = Fixture(extra)
            t = f.trees()
            try:
                call(f, t)
            except (T.TreeError, CF.CanfailError, ValueError) as e:
                assert "remote" in str(e) and "not built yet" in str(e), (word, e)
                continue
        raise AssertionError("a %s on a remote tree was taken" % word)


def test_remove_guard():
    """A14 (the charge-interposer reviewer, 2026-11-02): remove_remote never
    runs rm -rf on a path it was not made for. It refuses -- running
    nothing, a LEFTOVER with the reason -- unless the path is absolute,
    normalised, with no ".." or "~"; strictly under the host's workroot,
    which itself is absolute and not "/"; and its first part below the
    workroot is one the runner makes (accept-<record id>, or a chain run's
    work folder <suite>_<YYYYmmddTHHMMSS>_<hex4>)."""
    N[0] += 1
    mark = os.path.join(BASE, "ssh_ran%d" % N[0])        # this call's own, never a stale one
    ssh = (PY, "-c", "open(r'%s', 'w').close()" % mark)
    wr = "/w/runner"
    rec_dir = wr + "/accept-accept_s1_20261102T101500_ab12"
    cases = [
        ("relative", "w/runner/accept-accept_s1_20261102T101500_ab12", wr, "absolute"),
        ("home", "~/accept-accept_s1_20261102T101500_ab12", wr, "absolute"),
        ("dotdot", rec_dir + "/../../etc", wr, "normal"),
        ("not normalised", wr + "//accept-accept_s1_20261102T101500_ab12", wr, "normal"),
        ("the workroot", wr, wr, "under"),
        ("outside", "/other/accept-accept_s1_20261102T101500_ab12", wr, "under"),
        ("empty workroot", rec_dir, "", "workroot"),
        ("root workroot", "/accept-accept_s1_20261102T101500_ab12", "/", "workroot"),
        ("not the runner's", wr + "/home", wr, "runner"),
        ("near miss", wr + "/accept-x", wr, "runner"),
    ]
    for name, path, root, word in cases:
        said = []
        left = S.remove_remote(ssh, "farhost", path, root, out=said.append)
        assert not os.path.exists(mark), "%s: ssh ran for %r" % (name, path)
        assert left and word in left[0][1] and "refused" in left[0][1], (name, left)
        assert said and "LEFTOVER" in said[0], (name, said)
    # the runner's own folders are taken (ssh runs; here it only marks)
    for path in (rec_dir + "/tree-far", wr + "/accept-s1_20261102T101501_cd34"):
        left = S.remove_remote(ssh, "farhost", path, wr, quiet=True)
        assert os.path.exists(mark), "a runner's folder was refused: %s %s" % (path, left)
        os.remove(mark)


TESTS = [test_remote_tree, test_remote_check_refuses, test_remote_check_runs,
         test_remote_removed, test_remote_not_yet, test_remove_guard]

MUTANTS = [
    (T, "remote_exact", test_remote_tree),
    (T, "remote_check", test_remote_check_refuses),
    (T, "remote_made", test_remote_removed),
    (C, "remote_paths", test_remote_check_runs),
    (C, "remote_shell", test_remote_check_runs),
    (C, "chain_work_removed", test_remote_removed),
    (S, "remote_leftover", test_remote_removed),
    (S, "guard_absolute", test_remove_guard),
    (S, "guard_normal", test_remove_guard),
    (S, "guard_under", test_remove_guard),
    (S, "guard_workroot", test_remove_guard),
    (S, "guard_runner", test_remove_guard),
    (B, "remote_refused", test_remote_not_yet),
    (CF, "remote_refused", test_remote_not_yet),
    (C, "remote_suite_refused", test_remote_not_yet),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    bad = 0
    try:
        if not can_fail:
            for t in TESTS:
                out = outcome_of(t)
                print("%s  %-26s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        caught = 0
        for mod, k, t in MUTANTS:
            rules = getattr(mod, "RULES", None)
            if rules is None:                    # scratch.py: a module flag
                setattr(mod, k.upper(), False)
            else:
                rules[k] = False
            try:
                out = outcome_of(t)
            finally:
                if rules is None:
                    setattr(mod, k.upper(), True)
                else:
                    rules[k] = True
            print("%s  %-9s %-20s off: %-26s %s" % (
                "ok  " if out else "FAIL", mod.__name__.rsplit(".", 1)[-1], k, t.__name__,
                ("fails (" + out[:100] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
