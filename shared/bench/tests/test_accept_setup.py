"""Tests of the adapter's tree_setup (vtrux_bench/accept/trees.py): the
commands an adapter names to make a fresh tree usable -- the interposer's
installs of its pinned canre and can-tools -- run in every tree the runner
makes (ACCEPT-SPEC A4, A14, A15; the charge-interposer reviewer's
conditions of 2026-11-02, each tested here). No hardware.

    python -B shared/bench/tests/test_accept_setup.py            every test
    python -B shared/bench/tests/test_accept_setup.py --can-fail  the
        control first, then each test with a rule it guards switched off

The conditions: (1) each command is a committed file in that tree, named by
the adapter, nothing from the list or the operator; (2) it runs after the
tree check, and the tree is checked again after it, so setup changed
nothing git tracks; (3) the tree's record holds each command, its exit
code, its console and what it installed; (4) a failure stops the run
before anything is judged: exit 2, the record left open; (5) what it
installs is in the tree's own scratch/, removed with the tree.

Each test builds a small git repo in this run's own folder under the
worktree's scratch/ (tests/_scratch.py), removed at the end with
vtrux_bench/scratch.py.
"""

import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402

BASE = _scratch.run_dir("accept_setup")
N = [0]
PY = sys.executable

SETUP = {
    "ok": ("import os\n"
           "d = os.path.join('scratch', 'pin')\n"
           "os.makedirs(d, exist_ok=True)\n"
           "open(os.path.join(d, 'installed.txt'), 'w').write('pin abc123')\n"
           "print('installed pin abc123')\n"),
    "fails": "import sys\nprint('cannot reach the pin')\nsys.exit(4)\n",
    "edits": "open('proj/check.py', 'a').write('# edited by setup\\n')\nprint('edited')\n",
}

LIST = '''
[step]
id = "s1"
project = "proj"
ordered_by = "reviewer"
ordered_on = "2026-11-02"
spec = ["9.1"]
verification = "9.1 Emulation"

[trees.base]
project = "{C1}"
'''


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


def adapter(cmds):
    """The fake adapter with a tree_setup naming cmds, and a
    tree_installed reading what the setup said it installed."""
    A = types.ModuleType("setup_adapter")
    A.__dict__.update({k: v for k, v in vars(FA).items() if not k.startswith("__")})
    A.tree_setup = lambda tree: [list(c) for c in cmds]
    A.tree_installed = lambda tree, results: {
        "pin": next((r["console"].split("installed pin ", 1)[1].split()[0]
                     for r in results if "installed pin " in r["console"]), None)}
    return A


class Fixture:
    def __init__(self, setup="ok", cmds=(("proj/setup.py",),)):
        N[0] += 1
        self.dir = os.path.join(BASE, "f%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel, text in (("proj/check.py", "pass\n"), ("proj/suite.py", "pass\n"),
                          ("proj/setup.py", SETUP[setup]), (".gitignore", "/scratch/\n"),
                          ("shared/comp/tool.py", "pass\n")):
            write(self.repo, rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "C1")
        c1 = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "proj/uncommitted.py", "print('not committed')\n")
        FA.PINS.clear()
        write(self.repo, "proj/acceptance/s.toml", LIST.format(C1=c1))
        git(self.repo, "add", "proj/acceptance/s.toml")
        git(self.repo, "commit", "-q", "-m", "list")
        self.A = adapter(cmds)
        self.alist = L.load(self.repo, "%s:proj/acceptance/s.toml"
                            % git(self.repo, "rev-parse", "HEAD"), self.A)
        self.made = S.Made()
        self.settings = {"hosts": {"local": {"python": [PY]}}}

    def trees(self):
        return T.make_trees(self.repo, self.alist, self.A, os.path.join(self.dir, "work"),
                            self.made, say=lambda s: None,
                            remote={"settings": self.settings, "record_id": "accept_s1_x"})


def refused(f, *words):
    try:
        f.trees()
    except T.TreeError as e:
        for w in words:
            assert w in str(e), (w, str(e))
        return str(e)
    raise AssertionError("not refused: %s" % (words,))


# ---- the tests --------------------------------------------------------------
def test_setup_recorded():
    """Conditions 3 and 5: the setup runs in the tree, and the tree's record
    holds each command, its exit code, its console and what it installed;
    the install is in the tree's own scratch/ and goes with the tree."""
    f = Fixture()
    t = f.trees()["base"]
    s = t["record"]["setup"]
    assert s["commands"] == [{"argv": ["proj/setup.py"], "rc": 0,
                              "console": s["commands"][0]["console"]}], s
    assert "installed pin abc123" in s["commands"][0]["console"], s
    assert s["installed"] == {"pin": "abc123"}, s
    inst = os.path.join(t["project_dir"], "scratch", "pin", "installed.txt")
    assert os.path.isfile(inst), inst
    assert f.made.remove_all(out=lambda s: None) == [] and not os.path.exists(inst)


def test_setup_committed_only():
    """Condition 1: a setup command is a committed file in that tree: a
    file only in the repo's working copy, a bare program or a file outside
    the tree is refused before anything runs."""
    for cmd, word in ((("proj/uncommitted.py",), "proj/uncommitted.py"),
                      (("python", "-c", "1"), "python"),
                      (("../outside.py",), "../outside.py")):
        f = Fixture(cmds=(cmd,))
        refused(f, "setup", word, "committed")


def test_setup_changes_nothing_tracked():
    """Condition 2: the tree is checked again after its setup, so a setup
    that changes a tracked file is refused."""
    f = Fixture(setup="edits")
    refused(f, "proj/check.py", "setup")


def test_setup_failure_stops():
    """Condition 4: a setup command that fails stops the run: the refusal
    names the command, its exit code and its console."""
    f = Fixture(setup="fails")
    refused(f, "proj/setup.py", "4", "cannot reach the pin")


TESTS = [test_setup_recorded, test_setup_committed_only, test_setup_changes_nothing_tracked,
         test_setup_failure_stops]

MUTANTS = [
    ("setup_runs", test_setup_recorded),
    ("setup_recorded", test_setup_recorded),
    ("setup_committed", test_setup_committed_only),
    ("setup_recheck", test_setup_changes_nothing_tracked),
    ("setup_stops", test_setup_failure_stops),
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
                print("%s  %-36s %s" % ("ok  " if out is None else "FAIL", t.__name__,
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
        for k, t in MUTANTS:
            T.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                T.RULES[k] = True
            print("%s  %-16s off: %-36s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:100] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
