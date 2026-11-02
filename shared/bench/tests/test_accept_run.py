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
    "proj/suite.py": (
        "from vtrux_bench.run.model import Check, Group, Suite\n"
        "FLAKY = (\"import os, sys; p = os.path.join(r'{flake_dir}', 'seen'); \"\n"
        "         \"ok = os.path.exists(p); open(p, 'w').close(); sys.exit(0 if ok else 1)\")\n"
        "EMULATED = Suite(name='proj-emu', role='tester (test-suite)', groups=[\n"
        "    Group('a', [Check('ok', ['-c', 'pass'], python=True),\n"
        "                Check('flaky', ['-c', FLAKY], python=True)])])\n"),
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
                 dirty_adapter=False):
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
        c1 = self.commit("C1")
        write(self.repo, "proj/check.py", "pass  # changed\n")      # a product file
        c2 = self.commit("C2")
        git(self.repo, "checkout", "-q", c1, "--", "proj/check.py")
        c1b = self.commit("C1 product again")
        write(self.repo, "proj/acceptance/s1.toml", LIST.format(
            C1=c1b, MUTANT=mutant, PREMISE=c2 if premise_later else c1, EXTRA=extra))
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
            json.dump(dict(runs_root=self.runs, hosts={"local": {"python": [sys.executable]}},
                           bench_mac=MAC, flake_dir=flake,
                           bench_holder=os.path.join(self.dir, "bench-holder.md")), f)
        self.said = []
        R_ = __import__("vtrux_bench.accept.record", fromlist=["x"])
        real = R_.start
        R_.start = lambda runs, step, out=None, **kw: real(runs, step, out=out,
                                                           ceiling=self.dir, **kw)
        try:
            self.rc = RUN.main(["run", "--repo", self.repo, "--list",
                                "%s:proj/acceptance/s1.toml" % self.c3,
                                "--adapter", "proj/accept_adapter.py",
                                "--settings", self.settings])
        finally:
            R_.start = real
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


TESTS = [test_pass, test_adapter_committed, test_rerun_never_passes, test_premise_blocks,
         test_stopped]

MUTANTS = [
    ("adapter_from_commit", test_adapter_committed),
    ("premise_skip", test_premise_blocks),
    ("premise_skip_builds", test_premise_blocks),
    ("cleanup_finally", test_stopped),
    ("record_first", test_stopped),
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
        missing = sorted(set(RUN.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            RUN.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
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
