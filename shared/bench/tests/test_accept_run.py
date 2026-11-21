"""End-to-end tests of the acceptance runner (vtrux_bench/accept/run.py,
python -m vtrux_bench.accept run): ACCEPT-SPEC A1, with A11-A15 as they meet
in one run. No hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_run.py            every test
    python -B shared/bench/tests/test_accept_run.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (run.RULES; every rule has one)

Each test builds a small git repo holding a project ("proj"): a build, a
check, a project suite with a case that fails once and then passes, mutant
text, and the fake adapter itself (tests/fake_adapter.py, committed as
proj/accept_adapter.py), and runs a whole acceptance through the command
line's entry. Everything is in this run's own folder under the worktree's
scratch/ (tests/_scratch.py), removed at the end with vtrux_bench/scratch.py.
"""

import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import run as RUN            # noqa: E402

BASE = _scratch.run_dir("accept_run")
N = [0]
MAC = "02:00:00:00:00:01"                    # locally administered, no real board's

with open(os.path.join(HERE, "fake_adapter.py"), encoding="utf-8") as _f:
    ADAPTER = _f.read()

FILES = {
    "proj/accept_adapter.py": ADAPTER,
    "proj/build.py": (
        "import os, sys\n"
        "out, variant = sys.argv[1], sys.argv[2]\n"
        "os.makedirs(out, exist_ok=True)\n"
        "open(os.path.join(out, 'app.bin'), 'wb').write(('image ' + variant).encode())\n"
        "open(os.path.join(out, 'app.elf'), 'wb').write(('elf ' + variant).encode())\n"),
    "proj/calc.py": "def add(a, b):\n    return a + b\n",
    "proj/check_calc.py": (
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "import calc\n"
        "if calc.add(2, 2) != 4:\n"
        "    print('FAIL add')\n"
        "    sys.exit(1)\n"
        "print('ok add')\n"),
    "proj/check.py": "pass\n",
    "proj/check_subs.py": (
        "import sys\n"
        "print('SUB a PASS')\n"
        "print('SUB known FAIL')\n"
        "print('SUB built ERROR')\n"
        "sys.exit(1)\n"),
    "proj/suite.py": (
        "from vtrux_bench.run.model import Check, Group, KnownFail, Suite\n"
        "FLAKY = (\"import os, sys; p = os.path.join(r'{flake_dir}', 'seen'); \"\n"
        "         \"ok = os.path.exists(p); open(p, 'w').close(); sys.exit(0 if ok else 1)\")\n"
        "EMULATED = Suite(name='proj-emu', role='tester (test-suite)', groups=[\n"
        "    Group('a', [Check('ok', ['-c', 'pass'], python=True),\n"
        "                Check('flaky', ['-c', FLAKY], python=True),\n"
        "                Check('kf', ['-c', 'import sys; sys.exit(1)'], python=True)])],\n"
        "    known_fails=[KnownFail('a/kf', 'a recorded known FAIL')])\n"
        "BENCH = Suite(name='proj-bench', role='tester (gen-inhibit)', groups=[\n"
        "    Group('b', [Check('ok', ['-c', 'pass'], python=True)], needs_lock=True)])\n"),
    "proj/mutants.toml": ('[[mutant]]\nid = "sum"\nfile = "proj/calc.py"\n'
                          'old = "return a + b"\nnew = "return a - b"\n'),
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

[trees.base]
project = "{C1}"

[[build]]
id = "fw"
tree = "base"
target = "fw"
options = {{ variant = "man" }}

[[check]]
id = "calc"
tree = "base"
run = ["proj/check_calc.py"]

[[check]]
id = "calc2"
tree = "base"
run = ["proj/check_calc.py"]

[[suite_run]]
id = "emu"
tree = "base"
suite = "emulated"
rerun_flagged = true
{SKIPS}
[[compare]]
id = "same"
comparer = "transcript"
a = "calc"
b = "calc2"
expect = "identical"

[[canfail]]
id = "cf"
target = "calc"
mutants = {{ from = "proj/mutants.toml", ids = ["{MUTANT}"] }}
expect = "fails"

[[premise]]
id = "digest"
kind = "product_digest_same_as"
tree = "base"
commit = "{PREMISE}"
items = ["calc", "cf", "fw"]
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


class Run:
    """Builds the repo and runs one acceptance through run.main()."""

    def __init__(self, flaky_passes=True, mutant="sum", premise_later=False, extra="",
                 dirty_adapter=False, runner_dirty=False, dev=False, runner=None,
                 from_hold=None, machine=None,
                 setup_fails=False, skip_case=False, skips=""):
        N[0] += 1
        self.dir = os.path.join(BASE, "r%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel, text in FILES.items():
            write(self.repo, rel, text)
        write(self.repo, ".gitignore", "/scratch/\n")
        if skip_case:                     # a case the suite's catalogue skips (A12a)
            write(self.repo, "proj/suite.py", FILES["proj/suite.py"].replace(
                "                Check('kf',",
                "                Check('truck', ['-c', 'pass'], python=True,\n"
                "                      skip='needs the truck'),\n"
                "                Check('kf',"))
        if setup_fails:                   # the committed adapter names a setup that fails
            write(self.repo, "proj/accept_adapter.py", ADAPTER + (
                "\n\ndef tree_setup(tree):\n    return [['proj/setup.py']]\n"))
            write(self.repo, "proj/setup.py", "import sys\nprint('no pin')\nsys.exit(3)\n")
        c1 = self.commit("C1")
        write(self.repo, "proj/check.py", "pass  # changed\n")      # a product file
        c2 = self.commit("C2")
        git(self.repo, "checkout", "-q", c1, "--", "proj/check.py")
        c1b = self.commit("C1 product again")
        write(self.repo, "proj/acceptance/s1.toml", LIST.format(
            C1=c1b, MUTANT=mutant, PREMISE=c2 if premise_later else c1, EXTRA=extra,
            SKIPS=skips))
        self.c3 = self.commit("list")
        if dirty_adapter:                  # an uncommitted adapter that would refuse the list
            write(self.repo, "proj/accept_adapter.py", ADAPTER.replace(
                'PROJECT = "proj"', 'PROJECT = "not-proj"'))
        self.runs = os.path.join(self.dir, "runs")
        os.makedirs(self.runs)
        flake = os.path.join(self.dir, "flake")
        os.makedirs(flake)
        if flaky_passes:
            open(os.path.join(flake, "seen"), "w").close()
        self.settings = os.path.join(self.dir, "machine.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(dict(runs_root=self.runs, hosts={"local": {"python": [sys.executable, "-B"]}},
                           bench_mac=MAC, flake_dir=flake,
                           bench_holder=os.path.join(self.dir, "bench-holder.md"),
                           **(machine or {})), f)
        self.said = []
        args = ["run", "--repo", self.repo, "--list", "%s:proj/acceptance/s1.toml" % self.c3,
                "--adapter", "proj/accept_adapter.py", "--settings", self.settings] + (
                    ["--dev"] if dev else []) + (
                    ["--from-hold", str(from_hold)] if from_hold is not None else [])
        if runner:
            # a runner of its own (a clean checkout's shared/bench), in a
            # process of its own, without -B
            env = dict(os.environ, PYTHONPATH=runner, GIT_CEILING_DIRECTORIES=self.dir)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            r = subprocess.run([sys.executable, os.path.join(BASE, "runner_wrapper.py")] + args,
                               env=env, cwd=self.dir, capture_output=True, text=True,
                               timeout=1800)
            self.rc, self.out = r.returncode, r.stdout + r.stderr
            recs = [d for d in os.listdir(self.runs) if d.startswith("accept_")]
            self.rec = os.path.join(self.runs, recs[0]) if recs else None
            return
        R_ = __import__("vtrux_bench.accept.record", fromlist=["x"])
        real = R_.start
        R_.start = lambda runs, step, out=None, **kw: real(runs, step, out=out,
                                                           ceiling=self.dir, **kw)
        # The runner these tests run is the working tree under development,
        # so its identity is given here: a clean commit, or one with changes
        # (test_runner_identity tests runner_identity() itself).
        real_ident = RUN.runner_identity
        RUN.runner_identity = lambda path=None: {"commit": "f" * 40, "dirty": runner_dirty}
        try:
            self.rc = RUN.main(args)
        finally:
            R_.start = real
            RUN.runner_identity = real_ident
        recs = [d for d in os.listdir(self.runs) if d.startswith("accept_")]
        self.rec = os.path.join(self.runs, recs[0]) if recs else None

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def read(self, name):
        with open(os.path.join(self.rec, name), encoding="utf-8") as f:
            return f.read()

    def scratch_left(self):
        d = os.path.join(self.repo, "scratch", "vtrux-accept")
        return [n for n in os.listdir(d)] if os.path.isdir(d) else []


def sums_ok(rec):
    listed = {}
    with open(os.path.join(rec, "SHA256SUMS"), encoding="utf-8") as f:
        for ln in f.read().splitlines():
            h, _, p = ln.partition("  ")
            listed[p] = h
    have = {}
    for d, _dirs, files in os.walk(rec):
        for n in files:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, rec).replace("\\", "/")
            if rel != "SHA256SUMS":
                with open(p, "rb") as f:
                    have[rel] = hashlib.sha256(f.read()).hexdigest()
    return listed == have


# ---- the tests --------------------------------------------------------------
def test_pass():
    """A1, A12-A14: a whole acceptance that passes: exit 0, ACCEPTANCE PASS,
    a closed record (README, skeleton, SHA256SUMS that verify, the build
    record, the chain and nested records), the artifacts README row, and
    nothing left in scratch."""
    r = Run()
    assert r.rc == 0 and r.rec, (r.rc, r.rec)
    assert "ACCEPTANCE PASS" in r.read("README.md"), r.read("README.md")[-1500:]
    assert sums_ok(r.rec), "SHA256SUMS does not verify"
    for p in ("builds/fw.json", "verification_skeleton.md", "settings.masked.json"):
        assert os.path.exists(os.path.join(r.rec, p)), p
    assert os.listdir(os.path.join(r.rec, "chain")) and os.listdir(os.path.join(r.rec, "runs"))
    with open(os.path.join(r.runs, "README.md"), encoding="utf-8") as f:
        assert os.path.basename(r.rec) in f.read(), "no artifacts README row"
    assert r.scratch_left() == [], r.scratch_left()


def test_adapter_committed():
    """A15, A2: the adapter comes from the list's commit, never the working
    copy: an uncommitted adapter that would refuse the list is not used."""
    r = Run(dirty_adapter=True)
    assert r.rc == 0, (r.rc, r.rec and r.read("console.txt")[-1200:])


def test_rerun_never_passes():
    """A12 (the reviewer's ruling (a); gen-inhibit's reviewer, 2026-11-01:
    keep a test that proves it): a case that fails in the whole run and
    passes on its re-run leaves the acceptance FAIL, and the README and the
    skeleton show it side by side."""
    r = Run(flaky_passes=False)
    assert r.rc == 1, r.rc
    text = r.read("README.md")
    assert "ACCEPTANCE FAIL" in text and "suite_run emu" in text, text[-1500:]
    assert "| emu | a/flaky | first FAIL | re-run PASS |" in text, text
    assert "first FAIL, re-run PASS" in r.read("verification_skeleton.md")


def test_suite_run_not_passed():
    """A13 (the charge-interposer reviewer, 2026-11-02): a suite run's list
    of results that are not a pass holds its nested record's own, where the
    suite's known fails sit as KNOWN-FAIL, not only the outer chain's one
    result for the nested run: here a/flaky FAIL (the first, whole run's,
    not the re-run's) and a/kf KNOWN-FAIL; a/ok, a PASS, is not listed."""
    r = Run(flaky_passes=False)
    assert r.rc == 1, r.rc
    text = r.read("README.md")
    sec = text.split("## Results that are not a pass", 1)[-1].split("\n## ", 1)[0]
    lines = [ln for ln in sec.splitlines() if ln.startswith(("- ", "  - "))]
    at = [n for n, ln in enumerate(lines) if ln.startswith("- suite_run emu: ")]
    assert at, sec
    own = []
    for ln in lines[at[0] + 1:]:
        if not ln.startswith("  - "):
            break
        own.append(ln[4:])
    assert "nested/a/flaky FAIL" in own and "nested/a/kf KNOWN-FAIL" in own, sec
    assert not any("a/ok" in ln for ln in own), sec
    assert lines[at[0]] == "- suite_run emu: %d" % len(own), sec


def test_nested_wrong_shape():
    """A13 (gen-inhibit's note, through the charge-interposer reviewer,
    2026-11-02): a suite run's nested.json or nested record of the wrong
    shape -- nested.json's "first" or its "dir" null, a record that is not
    an object, or whose "results" is null or holds a result that is not an
    object -- gives the list an UNREADABLE line after the outer chain's own
    results, never an exception that stops the run. A record of the right
    shape is still listed, as nested/<id>."""
    N[0] += 1
    d = os.path.join(BASE, "shape%d" % N[0])
    os.makedirs(d)
    chain = {"results": [{"group": "emu", "id": "emu/run", "verdict": "FAIL"}]}

    def listed(nested, record=None):
        if record is not None:
            with open(os.path.join(d, "record.json"), "w", encoding="utf-8") as f:
                json.dump(record, f)
        return RUN._suite_not_passed(chain, "emu", nested)

    good = {"first": {"dir": d}}
    got = listed(good, {"results": [{"group": "a", "id": "a/kf", "verdict": "KNOWN-FAIL"}]})
    assert got == [("run", "FAIL"), ("nested/a/kf", "KNOWN-FAIL")], got
    for name, nested, record in (("first null", {"first": None}, None),
                                 ("dir null", {"first": {"dir": None}}, None),
                                 ("record a list", good, []),
                                 ("results null", good, {"results": None}),
                                 ("a result null", good, {"results": [None]})):
        got = listed(nested, record)
        assert len(got) == 2 and got[0] == ("run", "FAIL") \
            and got[1][0] == "nested record" and got[1][1].startswith("UNREADABLE: "), \
            (name, got)


def test_premise_blocks():
    """A11: a premise that fails makes its items NOT EXERCISED, never PASS
    or FAIL; they are not run -- builds included (the charge-interposer
    reviewer, 2026-11-01: a build is the expensive part) -- and the
    acceptance does not pass."""
    r = Run(premise_later=True)
    assert r.rc == 1, r.rc
    text = r.read("README.md")
    for want in ("NOT EXERCISED check calc", "NOT EXERCISED canfail cf",
                 "NOT EXERCISED build fw"):
        assert want in text, (want, text[-2000:])
    chain = os.path.join(r.rec, "chain")
    runs = [d for d in os.listdir(chain) if d.startswith("accept-s1")]
    with open(os.path.join(chain, runs[0], "record.json"), encoding="utf-8") as f:
        v = {x["id"]: x["verdict"] for x in json.load(f)["results"]}
    assert v.get("calc/run") == "NOT-RUN" and v.get("calc2/run") == "PASS", v
    assert not [d for d in os.listdir(chain) if d.startswith("accept-builds")], \
        "a blocked build ran: %s" % os.listdir(chain)
    with open(os.path.join(r.rec, "builds", "fw.json"), encoding="utf-8") as f:
        b = json.load(f)
    assert b["verdict"] == "NOT-RUN" and "premise" in b.get("skipped", "") \
        and not b.get("images"), b


def test_stopped():
    """A13, A14: a run refused after its record is named (a mutant whose
    text does not apply) says why in the record's console, leaves the
    record open (no SHA256SUMS), exits 2, and leaves nothing in scratch."""
    r = Run(mutant="nope")
    assert r.rc == 2, r.rc
    assert r.rec and "STOPPED before the close" in r.read("console.txt"), r.rec
    assert not os.path.exists(os.path.join(r.rec, "SHA256SUMS")), "a stopped record was closed"
    assert r.scratch_left() == [], r.scratch_left()


def test_setup_failure_stops_run():
    """A15's tree_setup (the charge-interposer reviewer's condition 4,
    2026-11-02): a setup command that fails stops the whole run before
    anything is judged: exit 2, the record left open (no SHA256SUMS), its
    console saying why, and nothing left in scratch."""
    r = Run(setup_fails=True)
    assert r.rc == 2, r.rc
    console = r.read("console.txt")
    assert "STOPPED before the close" in console and "proj/setup.py exited 3" in console, \
        console[-800:]
    assert not os.path.exists(os.path.join(r.rec, "SHA256SUMS")), "a stopped record was closed"
    assert not os.path.exists(os.path.join(r.rec, "chain")), "something was judged"
    assert r.scratch_left() == [], r.scratch_left()


def test_dirty_runner_refused():
    """A10, AGENTS.md (evidence is committed harness run from a named
    commit; gen-inhibit's reviewer, 2026-11-01): a runner whose own tree has
    uncommitted changes is refused, before any record is made."""
    r = Run(runner_dirty=True)
    assert r.rc == 2 and r.rec is None, (r.rc, r.rec)


def test_dev_run():
    """The same (gen-inhibit's reviewer, 2026-11-01): with --dev a runner
    with changes runs, and its record's verdict reads "DEV RUN, not
    evidence", never ACCEPTANCE PASS, in the README and the skeleton,
    though every item is as expected; it exits 3, never 0."""
    r = Run(runner_dirty=True, dev=True)
    assert r.rc == 3, r.rc
    text = r.read("README.md")
    assert "DEV RUN, not evidence" in text and "ACCEPTANCE PASS" not in text, text[:1200]
    sk = r.read("verification_skeleton.md")
    assert sk.startswith("DEV RUN, not evidence") and "ACCEPTANCE PASS" not in sk, sk[:400]


def test_runner_identity():
    """runner_identity(path): the commit of the runner's tree, and whether
    it has changes there -- a modified file or an untracked one; no git is
    no commit, which is refused like changes."""
    N[0] += 1
    d = os.path.join(BASE, "ident%d" % N[0])
    os.makedirs(os.path.join(d, "pkg"))
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.name", "test")
    git(d, "config", "user.email", "test@example.invalid")
    write(d, "pkg/a.py", "x = 1\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "c")
    pkg = os.path.join(d, "pkg")
    i = RUN.runner_identity(pkg)
    assert i == {"commit": git(d, "rev-parse", "HEAD"), "dirty": False}, i
    write(d, "pkg/a.py", "x = 2\n")
    assert RUN.runner_identity(pkg)["dirty"] is True, "a modified file is not a change"
    git(d, "checkout", "--", "pkg/a.py")
    write(d, "pkg/new.py", "y = 1\n")
    assert RUN.runner_identity(pkg)["dirty"] is True, "an untracked file is not a change"
    root = os.path.abspath(os.sep)                 # the drive's root: in no git repo
    assert RUN.runner_identity(root)["commit"] is None, RUN.runner_identity(root)


# The runner a test starts in a process of its own: run.main(), with the
# rules VTRUX_TEST_OFF names switched off, and, for the stray-file mutant
# only (VTRUX_TEST_STRAY), a file left in the runner's tree.
WRAPPER = '''import os, sys
from vtrux_bench.accept import run
for k in filter(None, os.environ.get("VTRUX_TEST_OFF", "").split(",")):
    run.RULES[k] = False
rc = run.main(sys.argv[1:])
if os.environ.get("VTRUX_TEST_STRAY"):
    open(os.path.join(os.path.dirname(run.__file__), "stray.txt"), "w").close()
sys.exit(rc)
'''


def test_runner_stays_clean():
    """gen-inhibit's reviewer (2026-11-01): a clean checkout of the runner
    stays clean -- runner_identity() reports no changes -- after one whole
    acceptance run in a process of its own without -B, so nothing the run
    writes lands in the runner's tree (shared/bench/.gitignore covers its
    __pycache__/, which is written)."""
    wt = _scratch.worktree_of(HERE)
    clone = os.path.join(BASE, "runner%d" % (N[0] + 1))
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", wt, clone], check=True)
    git(clone, "checkout", "-q", "--detach", git(wt, "rev-parse", "HEAD"))
    runner = os.path.join(clone, "shared", "bench")
    with open(os.path.join(BASE, "runner_wrapper.py"), "w", encoding="utf-8") as f:
        f.write(WRAPPER)
    assert RUN.runner_identity(runner)["dirty"] is False, "the clone is not clean to start with"
    r = Run(runner=runner)
    assert r.rc == 0, (r.rc, r.out[-1500:])
    pyc = [d for d, _dirs, _files in os.walk(runner) if os.path.basename(d) == "__pycache__"]
    assert pyc, "no __pycache__: the run was not without -B"
    ident = RUN.runner_identity(runner)
    assert ident["dirty"] is False, "the run left changes in the runner's tree: %s" % git(
        clone, "status", "--porcelain")


def test_check_every_result():
    """A12 (the charge-interposer reviewer, 2026-11-02): a check's item is
    judged on all its results, whatever their order: a check whose judge
    gives a FAIL and then an ERROR, with expect FAIL, is not as expected
    (got ERROR), and the README's line names the ERROR's result. The
    README's list of results that are not a pass has the check's every one,
    from the chain run's own results (the user, 2026-11-02, through the
    charge-interposer reviewer)."""
    r = Run(extra='\n[[check]]\nid = "subs"\ntree = "base"\nrun = ["proj/check_subs.py"]\n'
                  'judge = "sub-lines"\nexpect = "FAIL"\n')
    assert r.rc == 1, (r.rc, r.rec and r.read("console.txt")[-1500:])
    text = r.read("README.md")
    assert "check subs: expected FAIL, got ERROR" in text, text[-2000:]
    assert "run/built ERROR" in text, text[-2000:]
    sec = text.split("## Results that are not a pass", 1)[-1].split("\n## ", 1)[0]
    want = ["- check subs: 2", "  - run/known FAIL", "  - run/built ERROR"]
    lines = [ln for ln in sec.splitlines() if ln.startswith(("- ", "  - "))]
    assert want[0] in lines and lines[lines.index(want[0]):][:3] == want, sec


def test_images_from():
    """A9: a suite run is given the images of the builds it names
    (images_from), from their records: here emu-fw, whose settings in the
    record carry fw's ELF sha256 as builds/fw.json holds it. A suite run
    naming a build that made no images (hb, a target with none) is not
    started, saying why, and the rest of the run goes on (emu-fw runs)."""
    r = Run(extra='\n[[build]]\nid = "hb"\ntree = "base"\ntarget = "host"\n'
                  '\n[[suite_run]]\nid = "emu-hb"\ntree = "base"\nsuite = "emulated"\n'
                  'images_from = ["hb"]\n'
                  '\n[[suite_run]]\nid = "emu-fw"\ntree = "base"\nsuite = "emulated"\n'
                  'images_from = ["fw"]\n')
    assert r.rc == 1, (r.rc, r.rec and r.read("console.txt")[-1500:])
    console = r.read("console.txt")
    assert "suite run emu-hb: not started: its images_from build(s) made no images: " \
        "hb (PASS)" in console, console[-2000:]
    chain = os.path.join(r.rec, "chain")
    runs = [d for d in os.listdir(chain) if d.startswith("accept-s1")]
    with open(os.path.join(chain, runs[0], "record.json"), encoding="utf-8") as f:
        v = {x["id"]: x["verdict"] for x in json.load(f)["results"]}
    assert v.get("emu-hb/run") == "NOT-RUN" and v.get("emu-fw/run") == "PASS", v
    with open(os.path.join(r.rec, "builds", "fw.json"), encoding="utf-8") as f:
        fw = json.load(f)
    s = json.loads(r.read("settings.emu-fw.masked.json"))
    assert s.get("fake_images", {}).get("man", {}).get("elf_sha256") == fw["elf"]["sha256"], s
    assert "fake_images" not in json.loads(r.read("settings.emu.masked.json"))


def test_from_hold_passed():
    """A1 with shared/bench SPEC section 6's --from-hold (user, 2026-11-16;
    the charge-interposer reviewer: the acceptance takes the lock through the
    chain runner, so it must pass --from-hold through): `run --from-hold PID`
    reaches every suite run's nested run, whose nested.json records it (here
    the emulated suite, which needs no bench, so it is not given it and the
    acceptance passes as without it). The nested run's own use of it is
    test_accept_suite_runs.py's test_nested_from_hold."""
    pid = 424242
    r = Run(from_hold=pid)
    assert r.rc == 0, (r.rc, r.rec and r.read("console.txt")[-1500:])
    chain = os.path.join(r.rec, "chain")
    found = []
    for d, _dirs, files in os.walk(chain):
        if "nested.json" in files:
            with open(os.path.join(d, "nested.json"), encoding="utf-8") as f:
                found.append(json.load(f).get("from_hold"))
    assert found and all(x == {"pid": pid, "given": False} for x in found), found


def test_keep_lock_chain():
    """A1 with two bench suite runs under --from-hold (the gen-inhibit
    reviewer, 2026-11-16: the hold passes from run to run and is never
    free during the acceptance). The list runs emu, bench1, bench2 (with
    rerun_flagged, nothing flagged) and emu2; a session holds the bench
    by hand and runs the acceptance with --from-hold, while another role
    waits for the bench (bench_lock wait, polling every 0.05 s). Asked
    before the chain, bench1 and bench2 need the bench, emu and emu2 do
    not; bench1 alone is given --keep-lock, bench2 being the last that
    needs it. The acceptance passes, and the log shows bench1's handed
    release and take, its end release with the take under the session's
    PID in one step, bench2's handed release and take, its end release,
    and only then the waiter's withdraw and take. The nested runs are
    subprocesses, so the canmain check is probe "absent" for real, on an
    interface name and serial that no machine has."""
    import threading
    import time
    from vtrux_bench import bench_lock as BL
    role, other_role = "tester (gen-inhibit)", "tester (charge-interposer)"
    N[0] += 1
    d = os.path.join(BASE, "klc%d" % N[0])
    os.makedirs(d)
    log = os.path.join(d, "bench-log.jsonl")
    BL.init(log, role)
    serial = os.path.join(d, "canmain_serial.md")
    with open(serial, "w", encoding="utf-8") as f:
        f.write("- canmain serial: `TESTNOSUCHCANMAIN01`\n")
    machine = dict(bench_lock_log=log, canmain_check={
        "probe": "absent", "reason": "made-up interface and serial, on no machine",
        "iface": "cmnosuch0", "serial_file": serial})
    extra = ('\n[[suite_run]]\nid = "bench1"\ntree = "base"\nsuite = "bench"\n'
             '\n[[suite_run]]\nid = "bench2"\ntree = "base"\nsuite = "bench"\n'
             'rerun_flagged = true\n'
             '\n[[suite_run]]\nid = "emu2"\ntree = "base"\nsuite = "emulated"\n')
    sleep = [sys.executable, "-B", "-c", "import time; time.sleep(900)"]
    sess, other = subprocess.Popen(sleep), subprocess.Popen(sleep)
    stop, got = threading.Event(), {}
    waiter = threading.Thread(target=lambda: got.update(r=BL.wait(
        log, other_role, other.pid, "after the acceptance", poll=0.05, timeout=900,
        me=os.getpid(), stopped=stop.is_set, out=lambda s: None)))
    try:
        BL.take(log, role, sess.pid)
        waiter.start()
        end = time.monotonic() + 10
        while not [q for q in BL.read_state(log)["queue"] if q["who"] == other_role]:
            assert time.monotonic() < end, "the waiter's request was not queued"
            time.sleep(0.02)
        with open(log, encoding="utf-8") as f:
            n = len(f.read().splitlines())
        r = Run(from_hold=sess.pid, extra=extra, machine=machine)
        assert r.rc == 0, (r.rc, r.rec and r.read("console.txt")[-2500:])
        console = r.read("console.txt")
        P = sess.pid
        for line in ("suite run emu: does not need the bench (asked)",
                     "suite run bench1: needs the bench (asked)",
                     "suite run bench2: needs the bench (asked)",
                     "suite run emu2: does not need the bench (asked)",
                     "suite run bench1: --from-hold %d --keep-lock %d" % (P, P),
                     "suite run bench2: --from-hold %d (the last" % P):
            assert line in console, (line, console[-2500:])
        waiter.join(60)
        assert got.get("r", (None,))[0] == 0, "the waiter did not take: %r" % (got,)
        with open(log, encoding="utf-8") as f:
            ev = [json.loads(x) for x in f.read().splitlines()[n:]]
        rows = [(e["event"], e.get("who"), e.get("pid") if e["event"] == "take" else None,
                 e.get("run") is not None) for e in ev]
        assert rows[-2:] == [("withdraw", other_role, None, False),
                             ("take", other_role, other.pid, False)], rows
        runs = [e["run"] for e in ev if e["event"] == "take" and e.get("run")]
        assert len(runs) == 2, runs
        pids = {e["pid"] for e in ev if e["event"] == "take" and e.get("run")}
        assert len(pids) == 2 and P not in pids, pids        # two nested runners
        assert [(a, b, c if c == P else None, x) for a, b, c, x in rows[:-2]] == [
            ("release", role, None, False), ("take", role, None, True),
            ("release", role, None, False), ("take", role, P, False),
            ("release", role, None, False), ("take", role, None, True),
            ("release", role, None, False)], rows
        assert "-bench1" in runs[0] and "-bench2" in runs[1], runs
    finally:
        stop.set()
        if waiter.ident is not None:
            waiter.join(10)
        for p in (sess, other):
            p.kill()
            p.wait()


def test_declared_skips():
    """A12a (user, 2026-11-20), end to end: the suite's catalogue skips
    a/truck ("needs the truck"). Listed with that reason, its NOT-RUN does
    not fail the suite run: ACCEPTANCE PASS, though the nested run's own
    exit code is not 0 (its exit rule is unchanged), and the record lists
    the declared skip with its reason; nested.json holds the catalogue's
    skips. Unlisted, the NOT-RUN fails the suite run as before. Listed with
    another reason, the suite run fails, naming it."""
    listed = '\n[[suite_run.declared_skips]]\nid = "a/truck"\nreason = "%s"\n'
    r = Run(skip_case=True, skips=listed % "needs the truck")
    text = r.read("README.md")
    assert r.rc == 0 and "ACCEPTANCE PASS" in text, (r.rc, text[-2000:])
    assert "- suite run emu: a/truck -- needs the truck" in text, text
    found = [os.path.join(d, "nested.json") for d, _s, fs in os.walk(r.rec)
             if "nested.json" in fs]
    assert len(found) == 1, found
    with open(found[0], encoding="utf-8") as f:
        n = json.load(f)
    assert n["catalogue_skips"] == {"a/truck": "needs the truck"} and n["first"]["exit"], n
    r = Run(skip_case=True)
    text = r.read("README.md")
    assert r.rc == 1 and "| suite_run | emu | PASS | NOT-RUN |" in text, (r.rc, text[-2000:])
    r = Run(skip_case=True, skips=listed % "needs hands")
    text = r.read("README.md")
    assert r.rc == 1 and "| suite_run | emu | PASS | FAIL |" in text and \
        "declared skip a/truck: the listed reason 'needs hands'" in text, (r.rc, text[-2000:])


TESTS = [test_pass, test_adapter_committed, test_rerun_never_passes, test_premise_blocks,
         test_stopped, test_setup_failure_stops_run, test_dirty_runner_refused, test_dev_run,
         test_runner_identity, test_runner_stays_clean, test_check_every_result,
         test_suite_run_not_passed, test_nested_wrong_shape, test_images_from,
         test_from_hold_passed, test_keep_lock_chain, test_declared_skips]

MUTANTS = [
    ("adapter_from_commit", test_adapter_committed),
    ("premise_skip", test_premise_blocks),
    ("premise_skip_builds", test_premise_blocks),
    ("cleanup_finally", test_stopped),
    ("record_first", test_stopped),
    ("refuse_dirty", test_dirty_runner_refused),
    ("dev_never_pass", test_dev_run),
    ("identity_untracked", test_runner_identity),
    ("check_verdict", test_check_every_result),
    ("not_passed_listed", test_check_every_result),
    ("nested_not_passed", test_suite_run_not_passed),
    ("nested_wrong_shape", test_nested_wrong_shape),
    ("images_not_started", test_images_from),
    ("from_hold_passed", test_from_hold_passed),
    ("keep_lock_chain", test_keep_lock_chain),
    ("keep_not_last", test_keep_lock_chain),
    ("keep_bench_only", test_keep_lock_chain),
    ("suite_from_nested", test_declared_skips),
    ("_stray", test_runner_stays_clean),        # VTRUX_TEST_STRAY: a stray file
]

# run.RULES of the acceptance's own hold (ACCEPT-SPEC A18): their mutants
# are in tests/test_accept_hold.py, which checks that each has one
HOLD_RULES = ("hold_queued", "from_hold_no_queue", "hold_recorded")


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
        missing = sorted(set(RUN.RULES) - {k for k, _t in MUTANTS} - set(HOLD_RULES))
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            if k == "_stray":                  # not a rule: the wrapper leaves a file
                os.environ["VTRUX_TEST_STRAY"] = "1"
            else:
                RUN.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                os.environ.pop("VTRUX_TEST_STRAY", None)
                if k != "_stray":
                    RUN.RULES[k] = True
            print("%s  %-20s off: %-26s %s" % (
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
