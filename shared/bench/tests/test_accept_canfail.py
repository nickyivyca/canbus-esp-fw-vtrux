"""Tests of the acceptance runner's can-fails (vtrux_bench/accept/canfail.py
and their groups in compile.py): ACCEPT-SPEC A10. No hardware. Each test
names what it checks.

    python -B shared/bench/tests/test_accept_canfail.py            every test
    python -B shared/bench/tests/test_accept_canfail.py --can-fail  the
        control first (every test unchanged, which must pass), then each test
        again with a rule it guards switched off (canfail.RULES; every rule
        has one)

A small git repo holds a function (proj/calc.py), a check of it
(proj/check_calc.py), a harness with its own can-fail mode (proj/harness.py)
and committed mutant text (proj/mutants.toml). Each test loads a list with
one [[canfail]], makes the trees and mutant trees, runs the compiled suite
through the real chain runner and judges the can-fail. Everything is in this
run's own folder under the worktree's scratch/ (tests/_scratch.py), removed
at the end with vtrux_bench/scratch.py.
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
from vtrux_bench.accept import canfail as CF         # noqa: E402
from vtrux_bench.accept import compile as C          # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402
from vtrux_bench.accept import settings as ST        # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402

BASE = _scratch.run_dir("accept_canfail")
N = [0]

FILES = {
    "proj/calc.py": "def add(a, b):\n    return a + b  # the sum\n",
    "proj/check_calc.py": (
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "t = os.environ.get('FAKE_TREE', '')\n"
        "if not (t and os.path.samefile(t, here)):\n"
        "    print('FAIL FAKE_TREE does not name my own tree')\n"
        "    sys.exit(1)\n"
        "import calc\n"
        "got = calc.add(2, 2)\n"
        "if got != 4:\n"
        "    print('FAIL add(2, 2) = %d' % got)\n"
        "    sys.exit(1)\n"
        "print('ok add(2, 2) = 4, as calc.add(2, 2) should')\n"),
    "proj/harness.py": (
        "import sys\n"
        "sys.exit(0 if '--can-fail' in sys.argv else 1 if '--broken' in sys.argv else 0)\n"),
    "proj/fail.py": "import sys\nprint('FAIL always')\nsys.exit(1)\n",
    "proj/check.py": "pass\n",
    "proj/suite.py": "pass\n",
    "shared/comp/tool.py": "pass\n",
    "proj/mutants.toml": '''
[[mutant]]
id = "sum"
file = "proj/calc.py"
old = "return a + b"
new = "return a - b"

[[mutant]]
id = "comment"
file = "proj/calc.py"
old = "# the sum"
new = "# a sum"

[[mutant]]
id = "crash"
file = "proj/check_calc.py"
old = "import calc\\n"
new = "import calcx\\n"

[[mutant]]
id = "twice"
file = "proj/check_calc.py"
old = "calc.add(2, 2)"
new = "calc.add(2, 3)"

[[mutant]]
id = "absent"
file = "proj/calc.py"
old = "no such text"
new = "x"
''',
}

LIST = '''
[step]
id = "s1"
project = "proj"
ordered_by = "reviewer"
ordered_on = "2026-11-01"
spec = ["9.1"]
verification = "9.1 Emulation"

[trees.base]
project = "{C1}"

[[check]]
id = "calc"
tree = "base"
run = ["proj/check_calc.py"]

[[check]]
id = "harness"
tree = "base"
run = ["proj/harness.py"]

[[check]]
id = "always"
tree = "base"
run = ["proj/fail.py"]
expect = "FAIL"

[[canfail]]
id = "cf"
{CANFAIL}
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


class Fixture:
    """Loads the list with the [[canfail]] body given, makes the trees and
    prepares the mutants; run() runs and judges."""

    def __init__(self, canfail):
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
        c1 = git(self.repo, "rev-parse", "HEAD")
        FA.PINS.clear()
        write(self.repo, "proj/acceptance/s.toml", LIST.format(C1=c1, CANFAIL=canfail))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "list")
        self.alist = L.load(self.repo, "%s:proj/acceptance/s.toml"
                            % git(self.repo, "rev-parse", "HEAD"), FA)
        self.rec = R.start(os.path.join(self.dir, "runs"), "s1", out=lambda s: None,
                           ceiling=BASE)
        self.made = S.Made()
        self.work = os.path.join(self.dir, "work")
        self.trees = T.make_trees(self.repo, self.alist, FA, self.work, self.made,
                                  say=self.rec.say)

    def prepare(self):
        self.entries = CF.prepare(self.repo, self.alist, self.trees, FA, self.work,
                                  self.made, self.rec.say)
        return self.entries

    def run(self):
        self.prepare()
        machine = os.path.join(self.dir, "machine.json")
        with open(machine, "w", encoding="utf-8") as f:
            json.dump(dict(hosts={"local": {"python": [sys.executable]}}), f)
        path, s = ST.write_settings(machine, FA, {}, self.trees, self.rec, self.made, self.work)
        suite = C.compile_suite(self.alist, self.trees, FA, s, C.machine_env(FA),
                                mutants=self.entries)
        self.chain = C.run_suite(suite, s, self.rec, adapter=FA)
        items = CF.items(self.alist, self.entries, self.chain)
        assert len(items) == 1, items
        return items[0]


def refused(canfail, *words):
    f = Fixture(canfail)
    try:
        f.prepare()
    except CF.CanfailError as e:
        for w in words:
            assert w in str(e), (w, str(e))
        assert not os.path.exists(os.path.join(f.rec.path, "chain")), "something ran"
        return
    raise AssertionError("not refused: %s" % (words,))


# ---- the tests --------------------------------------------------------------
def test_copy_not_target():
    """A10: a mutant is applied to a copy of the tree (a fresh clone), never
    the target's own tree, which stays clean at its commit."""
    f = Fixture('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["sum"] }\n'
                'expect = "fails"')
    (e,) = f.prepare()
    base = f.trees["base"]
    assert os.path.normcase(e["tree"]["dir"]) != os.path.normcase(base["dir"]), e["tree"]["dir"]
    with open(os.path.join(e["tree"]["project_dir"], "proj", "calc.py")) as fh:
        assert "a - b" in fh.read(), "the mutant is not in its tree"
    with open(os.path.join(base["project_dir"], "proj", "calc.py")) as fh:
        assert "a + b" in fh.read(), "the target's tree was edited"
    T.check_tree(base["project_dir"], base["record"]["project"]["commit"])


def test_exact_once():
    """A10: the runner refuses a mutant whose text does not match exactly
    once (twice, or not at all), before anything runs; and a mutant id the
    file does not hold."""
    refused('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["twice"] }\n'
            'expect = "fails"', "twice", "2")
    refused('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["absent"] }\n'
            'expect = "fails"', "absent", "0")
    refused('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["nope"] }\n'
            'expect = "fails"', "nope")


def test_fails():
    """A10: "fails": every mutant must make the target check not PASS; a
    mutant under which it still passes is named. A15: a mutant run is
    named by its own tree (tree_env), never the target's: the check refuses
    to pass otherwise, which would hide the surviving mutant."""
    f = Fixture('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["sum"] }\n'
                'expect = "fails"')
    it = f.run()
    assert it["got"] == "as stated", it
    f = Fixture('target = "calc"\nmutants = { from = "proj/mutants.toml", '
                'ids = ["sum", "comment"] }\nexpect = "fails"')
    it = f.run()
    assert it["got"] != "as stated" and "comment" in it["detail"], it


def test_flips():
    """A10: "verdict-flips": the control (or the target unmutated) PASSes
    and every mutant FAILs; a control that does not pass is no flip."""
    f = Fixture('target = "calc"\ncontrol = "calc"\nmutants = { from = "proj/mutants.toml", '
                'ids = ["sum"] }\nexpect = "verdict-flips"')
    assert f.run()["got"] == "as stated"
    f = Fixture('target = "calc"\ncontrol = "always"\nmutants = { from = "proj/mutants.toml", '
                'ids = ["sum"] }\nexpect = "verdict-flips"')
    it = f.run()
    assert it["got"] != "as stated" and "control" in it["detail"], it


def test_new_fail_line():
    """A10: "new-fail-line": every mutant's log has a FAIL line that the
    control's does not; a mutant that only crashes does not count."""
    f = Fixture('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["sum"] }\n'
                'expect = "new-fail-line"')
    assert f.run()["got"] == "as stated"
    f = Fixture('target = "calc"\nmutants = { from = "proj/mutants.toml", ids = ["crash"] }\n'
                'expect = "new-fail-line"')
    it = f.run()
    assert it["got"] != "as stated" and "crash" in it["detail"], it


def test_mode():
    """A10: the harness's own can-fail mode: the target's command with the
    mode's argument, which must pass."""
    f = Fixture('target = "harness"\nmutants = { mode = "--can-fail" }\nexpect = "mode-passes"')
    assert f.run()["got"] == "as stated"
    f = Fixture('target = "harness"\nmutants = { mode = "--broken" }\nexpect = "mode-passes"')
    assert f.run()["got"] != "as stated"


TESTS = [test_copy_not_target, test_exact_once, test_fails, test_flips, test_new_fail_line,
         test_mode]

MUTANTS = [
    ("fresh_tree", test_copy_not_target),
    ("exact_once", test_exact_once),
    ("known_ids", test_exact_once),
    ("fails_all", test_fails),
    ("tree_env", test_fails),
    ("flip_control", test_flips),
    ("new_line", test_new_fail_line),
    ("mode_arg", test_mode),
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
                print("%s  %-24s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        # remote_refused's mutant is in test_accept_remote.py
        missing = sorted(set(CF.RULES) - {k for k, _t in MUTANTS} - {"remote_refused"})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            CF.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                CF.RULES[k] = True
            print("%s  %-14s off: %-24s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
