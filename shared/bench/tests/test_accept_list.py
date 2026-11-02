"""Tests of the acceptance list (vtrux_bench/accept/listfile.py): ACCEPT-SPEC
A2, A3, A4 (exact commits) and A8. No hardware. Each test names the
requirements it checks.

    python -B shared/bench/tests/test_accept_list.py            every test
    python -B shared/bench/tests/test_accept_list.py --can-fail  every test
        unchanged first (the control, which must pass), then each test again
        with a rule it guards switched off (listfile.RULES; every rule has
        one); each must then FAIL -- a test that still passes checks nothing
    python -B shared/bench/tests/test_accept_list.py NAME...     some tests

Each test builds a small git repo in this run's own folder under the
worktree's scratch/ (tests/_scratch.py), with a project folder "proj" and a
shared component "comp", and loads lists committed there with the fake
adapter (tests/fake_adapter.py). The folder is removed at the end with
vtrux_bench/scratch.py; what cannot be removed is printed.
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

BASE = _scratch.run_dir("accept_list")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Repo:
    """proj/ and shared/comp/ at C1; shared/comp/newtool.py added at C2,
    tagged comp-v2; a branch "topic" at C2."""
    n = 0

    def __init__(self):
        Repo.n += 1
        self.path = os.path.join(BASE, "repo%d" % Repo.n)
        os.makedirs(self.path)
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "user.name", "test")
        git(self.path, "config", "user.email", "test@example.invalid")
        git(self.path, "config", "core.autocrlf", "false")
        for rel in ("proj/check.py", "proj/suite.py", "proj/test/a_test.py",
                    "proj/test/b_test.py", "proj/test/c_sim.sh", "shared/comp/tool.py"):
            write(self.path, rel, "# %s\n" % rel)
        write(self.path, "proj/mutate.txt", "m1: old -> new\n")
        self.c1 = self.commit("C1")
        write(self.path, "shared/comp/newtool.py", "# new\n")
        self.c2 = self.commit("C2")
        git(self.path, "tag", "comp-v2")
        git(self.path, "branch", "topic")
        FA.PINS.clear()
        FA.PINS["comp"] = self.c1

    def commit(self, msg):
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", msg)
        return git(self.path, "rev-parse", "HEAD")

    def list_at(self, text, name="s"):
        """Commit the list as proj/acceptance/<name>.toml; its spec."""
        write(self.path, "proj/acceptance/%s.toml" % name, text.format(C1=self.c1, C2=self.c2))
        c = self.commit("list " + name)
        return "%s:proj/acceptance/%s.toml" % (c, name), c


GOOD = '''
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

[[build]]
id = "fw"
tree = "base"
target = "fw"
options = {{ variant = "man" }}

[[check]]
id = "chk"
trees = ["base", "new"]
run = ["proj/check.py", "--x", "{{run_dir}}"]

[[check]]
id = "tool"
tree = "new"
run = ["shared/comp/newtool.py"]
after = ["chk@base"]

[[set]]
id = "unit"
tree = "base"
select = ["tag:unit"]

[[suite_run]]
id = "emu"
tree = "base"
suite = "emulated"

[baseline]
emu = "proj-emulated_20261020T075920_d5cd"

[[compare]]
id = "same"
comparer = "transcript"
a = "chk@base"
b = "chk@new"
expect = "identical"

[[canfail]]
id = "cf"
target = "chk@new"
control = "chk@base"
mutants = {{ from = "proj/mutate.txt", ids = ["m1"] }}
expect = "new-fail-line"

[[premise]]
id = "digest"
kind = "product_digest_same_as"
tree = "base"
commit = "{C1}"
'''


def refused(repo, text, *words, name="bad"):
    """The list must be refused, and the refusal must name every word."""
    spec, _c = repo.list_at(text, name)
    try:
        L.load(repo.path, spec, FA)
    except L.ListError as e:
        msg = str(e)
        for w in words:
            assert w in msg, "refusal does not name %r: %s" % (w, msg)
        return msg
    raise AssertionError("not refused (should name %s)" % ", ".join(words))


def edit(text, old, new):
    assert text.count(old) == 1, ("fixture edit not unique", old)
    return text.replace(old, new)


# ---- the tests --------------------------------------------------------------
def test_reads_committed():
    """A2: the list is read from git at the commit named, never from the
    working tree; the result names the list's commit (in full) and the
    sha256 of its bytes there. A1/A3: the step's fields come through."""
    r = Repo()
    spec, c = r.list_at(GOOD)
    want = hashlib.sha256(subprocess.run(
        ["git", "-C", r.path, "show", "%s:proj/acceptance/s.toml" % c],
        capture_output=True, check=True).stdout).hexdigest()
    write(r.path, "proj/acceptance/s.toml", "this working copy is not the list\n")
    got = L.load(r.path, spec, FA)
    assert got.commit == c and got.path == "proj/acceptance/s.toml", (got.commit, got.path)
    assert got.sha256 == want, (got.sha256, want)
    assert got.step["id"] == "s1" and got.step["verification"] == "9.1 Emulation", got.step
    short = "%s:proj/acceptance/s.toml" % c[:10]
    assert L.load(r.path, short, FA).commit == c, "a short commit is not resolved in full"


def test_refuses_uncommitted():
    """A2: a list that is not committed at the commit named is refused, and
    so is a list named without a commit."""
    r = Repo()
    r.list_at(GOOD)
    write(r.path, "proj/acceptance/new.toml", GOOD.format(C1=r.c1, C2=r.c2))
    for spec in ("HEAD:proj/acceptance/new.toml", "proj/acceptance/new.toml",
                 os.path.join(r.path, "proj", "acceptance", "new.toml")):
        try:
            L.load(r.path, spec, FA)
        except L.ListError:
            continue
        raise AssertionError("%s was not refused" % spec)


def test_unknown_keys():
    """A3: a key the runner does not know is refused, at every level, and
    the refusal names it; so a list can carry no patch or script text."""
    r = Repo()
    cases = [
        ("\n[step]", 'patch = "--- a/x\\n+++ b/x"\n\n[step]', "patch"),
        ('verification = "9.1 Emulation"', 'verification = "9.1 Emulation"\nowner = "me"', "owner"),
        ('trees = ["base", "new"]', 'trees = ["base", "new"]\nscript = "print(1)"', "script"),
        ('[trees.base]\nproject = "{C1}"', '[trees.base]\nproject = "{C1}"\nedit = "x"', "edit"),
        ('expect = "identical"', 'expect = "identical"\nfixup = 1', "fixup"),
    ]
    for i, (old, new, word) in enumerate(cases):
        refused(r, edit(GOOD, old, new), word, name="unk%d" % i)


def test_required():
    """A3: what a list must hold is required: the step's fields, a check's
    command, a tree's commit."""
    r = Repo()
    refused(r, edit(GOOD, 'verification = "9.1 Emulation"\n', ""), "verification", name="r1")
    refused(r, edit(GOOD, 'run = ["shared/comp/newtool.py"]\n', ""), "tool", "run", name="r2")
    refused(r, edit(GOOD, '[trees.base]\nproject = "{C1}"', '[trees.base]\nhost = "local"'),
            "base", "project", name="r3")


def test_committed_command():
    """A3: a command is a committed file in its tree: a bare program, a file
    only in the working tree, or a command given as one string is refused.
    A shared file is looked for at the tree's commit of that component: the
    list's override, else the project's pin. A can-fail's mutant text is a
    committed file too (A10)."""
    r = Repo()
    write(r.path, "proj/notcommitted.py", "# no\n")
    refused(r, edit(GOOD, 'run = ["proj/check.py", "--x", "{{run_dir}}"]', 'run = ["python", "-c", "1"]'),
            "chk", "python", name="c1")
    refused(r, edit(GOOD, 'run = ["proj/check.py", "--x", "{{run_dir}}"]', 'run = ["proj/notcommitted.py"]'),
            "proj/notcommitted.py", name="c2")
    refused(r, edit(GOOD, 'run = ["proj/check.py", "--x", "{{run_dir}}"]', 'run = "proj/check.py --x"'),
            "chk", "run", name="c3")
    # newtool.py exists only from C2: at tree base the pin is C1
    refused(r, edit(GOOD, 'id = "tool"\ntree = "new"', 'id = "tool"\ntree = "base"'),
            "shared/comp/newtool.py", name="c4")
    refused(r, edit(GOOD, 'from = "proj/mutate.txt"', 'from = "proj/mutate2.txt"'),
            "proj/mutate2.txt", name="c5")
    L.load(r.path, r.list_at(GOOD, "ok")[0], FA)


def test_exact_commits():
    """A4 (and A3): each tree is at an exact commit: a branch name is
    refused for the project and for a shared component, an unknown commit
    is refused, a tag of a shared component is taken; the trees come back
    with full commits."""
    r = Repo()
    got = L.load(r.path, r.list_at(GOOD, "ok")[0], FA)
    assert got.trees["base"]["project"] == r.c1, got.trees
    assert got.trees["new"]["shared"] == {"comp": r.c2}, got.trees
    assert got.trees["base"]["shared"] == {"comp": r.c1}, ("the pin, in full", got.trees)
    refused(r, edit(GOOD, '[trees.base]\nproject = "{C1}"', '[trees.base]\nproject = "main"'),
            "base", "main", name="e1")
    refused(r, edit(GOOD, 'comp = "comp-v2"', 'comp = "topic"'), "new", "topic", name="e2")
    refused(r, edit(GOOD, '[trees.base]\nproject = "{C1}"',
                    '[trees.base]\nproject = "0123456789abcdef0123456789abcdef01234567"'),
            "base", "0123456789", name="e3")


def test_references():
    """A3: every name a list uses is defined in it: trees, checks (an
    id@tree of a check on several trees), builds and suite runs."""
    r = Repo()
    cases = [
        ('after = ["chk@base"]', 'after = ["chk@nowhere"]', "chk@nowhere"),
        ('b = "chk@new"', 'b = "chk"', '"chk"'),
        ('target = "chk@new"', 'target = "nope"', "nope"),
        ('emu = "proj-emulated', 'emx = "proj-emulated', "emx"),
        ('id = "fw"\ntree = "base"', 'id = "fw"\ntree = "basis"', "basis"),
        ('id = "digest"\nkind = "product_digest_same_as"\ntree = "base"',
         'id = "digest"\nkind = "product_digest_same_as"\ntree = "bass"', "bass"),
    ]
    for i, (old, new, word) in enumerate(cases):
        refused(r, edit(GOOD, old, new), word, name="ref%d" % i)


def test_adapter_names():
    """A3, A15: build targets and their options, shells, judges, comparers
    and suites are the adapter's; any other name is refused."""
    r = Repo()
    cases = [
        ('target = "fw"', 'target = "fx"', "fx"),
        ('options = {{ variant = "man" }}', 'options = {{ flavour = "man" }}', "flavour"),
        ('run = ["shared/comp/newtool.py"]', 'run = ["shared/comp/newtool.py"]\nshell = "zsh"', "zsh"),
        ('run = ["shared/comp/newtool.py"]', 'run = ["shared/comp/newtool.py"]\njudge = "eyeball"', "eyeball"),
        ('comparer = "transcript"', 'comparer = "diffy"', "diffy"),
        ('suite = "emulated"', 'suite = "emulatd"', "emulatd"),
    ]
    for i, (old, new, word) in enumerate(cases):
        refused(r, edit(GOOD, old, new), word, name="ad%d" % i)


def test_project():
    """A3: the step's project is the adapter's."""
    r = Repo()
    refused(r, edit(GOOD, 'project = "proj"', 'project = "other"'), "other", "proj")


def test_selection():
    """A8: a set takes exactly the catalogue entries its selector picks,
    each with the entry's own path, arguments, environment, shell and
    timeout; the runner adds and drops none. A selector that picks nothing
    is refused."""
    r = Repo()
    got = L.load(r.path, r.list_at(GOOD, "ok")[0], FA)
    ids = sorted(c["id"] for c in got.checks)
    assert ids == ["chk@base", "chk@new", "tool", "unit.unit-a", "unit.unit-b"], ids
    b = next(c for c in got.checks if c["id"] == "unit.unit-b")
    assert b["run"] == ["proj/test/b_test.py", "--quick"] and b["env"] == {"B": "1"}, b
    assert b["shell"] == "python" and b["timeout_s"] == 60 and b["tree"] == "base", b
    got2 = L.load(r.path, r.list_at(edit(GOOD, 'select = ["tag:unit"]',
                                         'select = ["id:sim-*", "tag:fast"]'), "ok2")[0], FA)
    ids2 = sorted(c["id"] for c in got2.checks if c["id"].startswith("unit."))
    assert ids2 == ["unit.sim-c", "unit.unit-a"], ids2
    refused(r, edit(GOOD, 'select = ["tag:unit"]', 'select = ["tag:none"]'), "unit", "tag:none",
            name="sel")


def test_ids():
    """A3: item ids are unique across the list and safe in paths."""
    r = Repo()
    refused(r, edit(GOOD, 'id = "tool"', 'id = "chk@base"'), "chk@base", name="i1")
    refused(r, edit(GOOD, 'id = "same"', 'id = "cf"'), '"cf"', name="i2")
    refused(r, edit(GOOD, 'id = "tool"', 'id = "to ol"'), "to ol", name="i3")


TESTS = [test_reads_committed, test_refuses_uncommitted, test_unknown_keys, test_required,
         test_committed_command, test_exact_commits, test_references, test_adapter_names,
         test_project, test_selection, test_ids]

# Each mutant: one listfile.RULES entry switched off, and the test that must
# then fail. Every rule has one.
MUTANTS = [
    ("full_commit", test_reads_committed),
    ("list_sha256", test_reads_committed),
    ("committed_only", test_refuses_uncommitted),
    ("unknown_keys", test_unknown_keys),
    ("required", test_required),
    ("committed_files", test_committed_command),
    ("shared_at_tree", test_committed_command),
    ("exact_commit", test_exact_commits),
    ("refs", test_references),
    ("adapter_names", test_adapter_names),
    ("project", test_project),
    ("selector_only", test_selection),
    ("empty_selector", test_selection),
    ("unique_ids", test_ids),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    bad = 0
    try:
        if not can_fail:
            tests = [t for t in TESTS if not names or t.__name__ in names]
            for t in tests:
                out = outcome_of(t)
                print("%s  %-26s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(tests) - bad, len(tests)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(L.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        muts = [(k, t) for k, t in MUTANTS if not names or t.__name__ in names]
        caught = 0
        for k, t in muts:
            L.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                L.RULES[k] = True
            print("%s  %-16s off: %-26s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(muts)))
        return 1 if bad or caught < len(muts) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
