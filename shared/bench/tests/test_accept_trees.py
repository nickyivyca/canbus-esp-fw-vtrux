"""Tests of the acceptance runner's trees (vtrux_bench/accept/trees.py):
ACCEPT-SPEC A4, and A14 for their removal. No hardware. Each test names the
requirements it checks.

    python -B shared/bench/tests/test_accept_trees.py            every test
    python -B shared/bench/tests/test_accept_trees.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (trees.RULES; every rule has one)

Each test builds a small git repo (proj/ and shared/comp/) in this run's own
folder under the worktree's scratch/ (tests/_scratch.py), loads a list
committed there with tests/fake_adapter.py and makes its trees; the folder
is removed at the end with vtrux_bench/scratch.py.
"""

import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402
from vtrux_bench.run.engine import product_digest    # noqa: E402

BASE = _scratch.run_dir("accept_trees")
N = [0]

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

[trees.new]
project = "{C1}"
shared = {{ comp = "comp-v2" }}
'''


def git(repo, *args, binary=False):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=not binary)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout if binary else r.stdout.strip()


def write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Fixture:
    """A repo: proj/ and shared/comp/ at C1; shared/comp/tool.py changed at
    C2 (tagged comp-v2); the list at C3; then the working copy of
    proj/check.py edited and not committed. The list loaded, and a Made."""

    def __init__(self):
        N[0] += 1
        self.dir = os.path.join(BASE, "f%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel in ("proj/check.py", "proj/suite.py", "proj/notes.md", "shared/comp/tool.py"):
            write(self.repo, rel, "# %s at C1\n" % rel)
        self.c1 = self.commit("C1")
        write(self.repo, "shared/comp/tool.py", "# shared/comp/tool.py at C2\n")
        self.c2 = self.commit("C2")
        git(self.repo, "tag", "comp-v2")
        FA.PINS.clear()
        FA.PINS["comp"] = self.c1
        write(self.repo, "proj/acceptance/s.toml", LIST.format(C1=self.c1))
        self.c3 = self.commit("list")
        write(self.repo, "proj/check.py", "# an uncommitted edit\n")
        self.alist = L.load(self.repo, "%s:proj/acceptance/s.toml" % self.c3, FA)
        self.made = S.Made()
        self.parent = os.path.join(self.dir, "trees")

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def make(self):
        return T.make_trees(self.repo, self.alist, FA, self.parent, self.made,
                            say=lambda s: None)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---- the tests --------------------------------------------------------------
def test_exact_and_clean():
    """A4: each tree is a checkout at exactly its commit with no changes:
    the project at its commit, a shared component at the list's override
    (tree new) or the project's pin (tree base), each with HEAD at that
    commit and a clean status."""
    f = Fixture()
    trees = f.make()
    for name, want_comp in (("base", f.c1), ("new", f.c2)):
        t = trees[name]
        assert git(t["project_dir"], "rev-parse", "HEAD") == f.c1, name
        assert git(t["project_dir"], "status", "--porcelain", "--untracked-files=all") == "", name
        cd = t["shared_dirs"]["comp"]
        assert git(cd, "rev-parse", "HEAD") == want_comp, (name, "comp")
        assert git(cd, "status", "--porcelain", "--untracked-files=all") == "", name
    with open(os.path.join(trees["new"]["shared_dirs"]["comp"], "shared", "comp", "tool.py")) as fh:
        assert "at C2" in fh.read(), "tree new's comp is not at its override"


def test_from_git_objects():
    """A4: a tree is made from git objects, never from a working copy: the
    repo's uncommitted edit of proj/check.py is not in it, and the repo
    itself is left as it was."""
    f = Fixture()
    trees = f.make()
    with open(os.path.join(trees["base"]["project_dir"], "proj", "check.py")) as fh:
        text = fh.read()
    assert "at C1" in text and "uncommitted" not in text, text
    with open(os.path.join(f.repo, "proj", "check.py")) as fh:
        assert "uncommitted" in fh.read(), "the runner touched the repo's working copy"


def test_refuses_dirty_or_moved():
    """A4: the runner refuses a tree whose HEAD is not exactly its commit,
    or that has any change: a modified file, an untracked file."""
    f = Fixture()
    trees = f.make()
    d, c = trees["base"]["project_dir"], f.c1
    T.check_tree(d, c)                                       # as made: clean
    cases = []
    write(d, "proj/notes.md", "changed\n")
    cases.append("modified")
    try:
        T.check_tree(d, c)
        raise AssertionError("a modified file was taken")
    except T.TreeError as e:
        assert "notes.md" in str(e), e
    git(d, "checkout", "--", "proj/notes.md")
    write(d, "proj/stray.txt", "x\n")
    try:
        T.check_tree(d, c)
        raise AssertionError("an untracked file was taken")
    except T.TreeError as e:
        assert "stray.txt" in str(e), e
    os.remove(os.path.join(d, "proj", "stray.txt"))
    try:
        T.check_tree(d, f.c2)
        raise AssertionError("a tree at another commit was taken")
    except T.TreeError as e:
        assert f.c2[:12] in str(e), e


def test_record():
    """A4: each tree gets a record: its commits (project and every shared
    component), tree ids, the product digest, and the sha256 of every file
    in each checkout, as git has it at that commit."""
    f = Fixture()
    trees = f.make()
    rec = trees["new"]["record"]
    assert rec["project"]["commit"] == f.c1, rec
    assert rec["project"]["tree_id"] == git(f.repo, "rev-parse", f.c1 + "^{tree}"), rec
    assert rec["shared"]["comp"]["commit"] == f.c2, rec
    assert rec["shared"]["comp"]["tree_id"] == git(f.repo, "rev-parse", f.c2 + "^{tree}"), rec
    want = {}
    for rel in git(f.repo, "ls-tree", "-r", "--name-only", f.c1).splitlines():
        want[rel] = sha(git(f.repo, "show", "%s:%s" % (f.c1, rel), binary=True))
    assert rec["project"]["files"] == want, (rec["project"]["files"], want)
    want_c = {rel: sha(git(f.repo, "show", "%s:%s" % (f.c2, rel), binary=True))
              for rel in git(f.repo, "ls-tree", "-r", "--name-only", f.c2).splitlines()}
    assert rec["shared"]["comp"]["files"] == want_c, rec["shared"]["comp"]["files"]
    # the product digest, worked out here from git objects
    from_git = os.path.join(f.dir, "digest")
    for rel in FA.product_files(None):
        p = os.path.join(from_git, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(git(f.repo, "show", "%s:%s" % (f.c1, rel), binary=True))
    assert rec["product_digest"] == product_digest(from_git, FA.product_files(None)), rec


def test_layout():
    """A4, A15: the checkouts go where the adapter's tree_paths puts them,
    inside the tree's own folder."""
    f = Fixture()
    trees = f.make()
    t = trees["new"]
    assert os.path.normcase(t["project_dir"]) == os.path.normcase(os.path.join(t["dir"], "project")), t
    assert os.path.normcase(t["shared_dirs"]["comp"]) == os.path.normcase(
        os.path.join(t["dir"], "shared-comp")), t
    assert os.path.normcase(os.path.dirname(t["dir"])) == os.path.normcase(f.parent), t


def test_removed():
    """A14: every tree folder is in the run's Made, so its finally (and exit
    handler) removes them all, read-only git objects included; nothing is
    left under the trees' parent."""
    f = Fixture()
    trees = f.make()
    dirs = [t["dir"] for t in trees.values()]
    assert all(os.path.isdir(d) for d in dirs)
    left = f.made.remove_all(out=lambda s: None)
    assert left == [], left
    assert not any(os.path.lexists(d) for d in dirs), dirs
    assert os.listdir(f.parent) == [], os.listdir(f.parent)


TESTS = [test_exact_and_clean, test_from_git_objects, test_refuses_dirty_or_moved,
         test_record, test_layout, test_removed]

MUTANTS = [
    ("exact_checkout", test_exact_and_clean),
    ("shared_checkout", test_exact_and_clean),
    ("from_objects", test_from_git_objects),
    ("head_check", test_refuses_dirty_or_moved),
    ("clean_check", test_refuses_dirty_or_moved),
    ("untracked_check", test_refuses_dirty_or_moved),
    ("record_tree_id", test_record),
    ("record_files", test_record),
    ("record_digest", test_record),
    ("layout", test_layout),
    ("made_tracked", test_removed),
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
                print("%s  %-28s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(T.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            T.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                T.RULES[k] = True
            print("%s  %-16s off: %-28s %s" % (
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
