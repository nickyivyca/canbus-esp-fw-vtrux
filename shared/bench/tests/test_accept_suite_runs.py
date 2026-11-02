"""Tests of the acceptance runner's suite runs (vtrux_bench/accept/nested.py
and its group in compile.py): ACCEPT-SPEC A7, A6 for their records, and the
first-run rule of A12 (the charge-interposer reviewer, 2026-11-01). No
hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_suite_runs.py            every test
    python -B shared/bench/tests/test_accept_suite_runs.py --can-fail  the
        control first (every test unchanged, which must pass), then each test
        again with a rule it guards switched off (nested.RULES; every rule has
        one). The rules are switched off in this process; the nested run is a
        subprocess, so a switched-off rule reaches it through VTRUX_TEST_OFF,
        which only this test's own wrapper (nested_off.py) reads.

A small git repo holds a project suite (proj/suite.py) with one passing case
and one that fails the first time it runs and passes after (a marker file),
so a re-run of flagged cases has something to re-run. Everything is in this
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
from vtrux_bench.accept import compile as C          # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import nested as NS          # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402
from vtrux_bench.accept import settings as ST        # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402

BASE = _scratch.run_dir("accept_suite_runs")
N = [0]
MAC = "02:00:00:00:00:01"                    # locally administered, no real board's
OFF_ENV = "VTRUX_TEST_OFF"

SUITE = '''import os, sys
from vtrux_bench.run.model import Check, Group, Suite

FLAKY = ("import os, sys; p = os.path.join(r'{flake_dir}', 'seen'); "
         "ok = os.path.exists(p); open(p, 'w').close(); sys.exit(0 if ok else 1)")
TREE = ("import os, sys; sys.exit(0 if os.path.samefile(r'{fake_tree}', os.getcwd()) "
        "else 1)")
EMULATED = Suite(name="proj-emu", role="tester (test-suite)", groups=[
    Group("a", [Check("ok", ["-c", "pass"], python=True),
                Check("flaky", ["-c", FLAKY], python=True),
                Check("tree", ["-c", TREE], python=True)])])
'''

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

[[suite_run]]
id = "emu"
tree = "base"
suite = "emulated"
rerun_flagged = {RERUN}
'''

# A wrapper for the nested run that switches off, in the subprocess, the
# rules named in VTRUX_TEST_OFF; it is used only by these tests.
WRAPPER = '''import os, sys
from vtrux_bench.accept import nested
for k in filter(None, os.environ.get("%s", "").split(",")):
    nested.RULES[k] = False
sys.exit(nested.main())
''' % OFF_ENV


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
    """Runs the list's suite run to the end; holds what it made."""

    def __init__(self, rerun=True):
        N[0] += 1
        self.dir = os.path.join(BASE, "f%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        write(self.repo, "proj/suite.py", SUITE)
        write(self.repo, "proj/check.py", "pass\n")
        write(self.repo, "shared/comp/tool.py", "pass\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "C1")
        c1 = git(self.repo, "rev-parse", "HEAD")
        FA.PINS.clear()
        write(self.repo, "proj/acceptance/s.toml",
              LIST.format(C1=c1, RERUN="true" if rerun else "false"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "list")
        alist = L.load(self.repo, "HEAD:proj/acceptance/s.toml".replace(
            "HEAD", git(self.repo, "rev-parse", "HEAD")), FA)
        self.rec = R.start(os.path.join(self.dir, "runs"), "s1", out=lambda s: None,
                           ceiling=BASE)
        self.made = S.Made()
        work = os.path.join(self.dir, "work")
        trees = T.make_trees(self.repo, alist, FA, work, self.made, say=self.rec.say)
        flake = os.path.join(self.dir, "flake")
        os.makedirs(flake)
        machine = os.path.join(self.dir, "machine.json")
        with open(machine, "w", encoding="utf-8") as f:
            json.dump(dict(hosts={"local": {"python": [sys.executable]}}, bench_mac=MAC,
                           flake_dir=flake, runs_root=os.path.join(self.dir, "elsewhere"),
                           bench_holder=os.path.join(self.dir, "bench-holder.md")), f)
        path, s = ST.write_settings(machine, FA, {}, trees, self.rec, self.made, work)
        base = C.machine_env(FA)
        off = ",".join(k for k, v in NS.RULES.items() if not v)
        if off:
            base[OFF_ENV] = off
        per_run = {"emu": ST.write_tree_settings(s, FA, "emu", trees["base"], self.rec,
                                                 self.made, work)}
        suite = C.compile_suite(alist, trees, FA, s, base, paths={
            "settings": path, "masked": os.path.join(self.rec.path, "settings.masked.json"),
            "record_id": self.rec.id, "suite_runs": per_run})
        chk = suite.groups[0].checks[0]
        wrapper = os.path.join(self.dir, "nested_off.py")
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write(WRAPPER)
        i = chk.argv.index("-m")
        chk.argv[i:i + 2] = [wrapper]                  # same arguments, through the wrapper
        self.chain = C.run_suite(suite, s, self.rec, adapter=FA)
        self.rid = "%s-emu" % self.rec.id
        self.runs = os.path.join(self.rec.path, "runs")
        with open(os.path.join(self.chain["_dir"], "emu", "run", "nested.json"),
                  encoding="utf-8") as f:
            self.nested = json.load(f)

    def record(self, rid):
        with open(os.path.join(self.runs, rid, "record.json"), encoding="utf-8") as f:
            text = f.read()
        return text, json.loads(text)

    def verdict(self):
        return {r["id"]: r["verdict"] for r in self.chain["results"]}["emu/run"]


# ---- the tests --------------------------------------------------------------
def test_nested_record():
    """A7: a suite run is a nested chain run with its own record inside the
    acceptance record (runs/<record id>-<suite run id>/), of the whole
    suite, with settings of its own naming its tree (the adapter's
    tree_settings: case a/tree passes only then); A6: that record holds
    the masked settings, and so does the record's copy of them."""
    f = Fixture(rerun=False)
    text, rec = f.record(f.rid)
    assert MAC not in text and "<MAC>" in text, "the nested record holds the real settings"
    assert rec["subset"] is False, rec["subset"]
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"a/ok": "PASS", "a/flaky": "FAIL", "a/tree": "PASS"}, v
    with open(os.path.join(f.rec.path, "settings.emu.masked.json"), encoding="utf-8") as fh:
        m = fh.read()
    assert "fake_tree" in m and MAC not in m, m
    assert sorted(os.listdir(f.runs)) == [f.rid], os.listdir(f.runs)


def test_rerun_new_record():
    """A7: a re-run of the flagged cases is a new nested record selecting
    only them; the first record is left as it closed."""
    f = Fixture(rerun=True)
    _t, first = f.record(f.rid)
    ids = sorted(r["id"] for r in first["results"])
    assert ids == ["a/flaky", "a/ok", "a/tree"], "the first record changed: %s" % ids
    _t2, second = f.record(f.rid + "-rerun")
    assert second["subset"] is True and second["subset_cases"] == ["a/flaky"], (
        second["subset"], second["subset_cases"])
    assert {r["id"]: r["verdict"] for r in second["results"]} == {"a/flaky": "PASS"}


def test_first_run_decides():
    """A12 (the reviewer's ruling (a), 2026-11-01): the first, whole run
    decides: a case that failed and passed on its re-run leaves the suite
    run FAIL. Each flagged case is listed with its first and re-run
    verdicts side by side, in nested.json and on the console."""
    f = Fixture(rerun=True)
    assert f.verdict() == "FAIL", f.verdict()
    assert f.nested["flagged"] == [{"check": "a/flaky", "first": "FAIL", "rerun": "PASS"}], \
        f.nested["flagged"]
    with open(os.path.join(f.chain["_dir"], "emu", "run", "check.log"), encoding="utf-8") as fh:
        log = fh.read()
    assert any("a/flaky" in ln and "first FAIL" in ln and "re-run PASS" in ln
               for ln in log.splitlines()), log[-800:]


TESTS = [test_nested_record, test_rerun_new_record, test_first_run_decides]

MUTANTS = [
    (NS, "nested_masked", test_nested_record),
    (NS, "rerun_new_record", test_rerun_new_record),
    (NS, "rerun_only_flagged", test_rerun_new_record),
    (NS, "first_decides", test_first_run_decides),
    (ST, "tree_settings", test_nested_record),
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
        missing = sorted(set(NS.RULES) - {k for m, k, _t in MUTANTS if m is NS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for mod, k, t in MUTANTS:
            mod.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                mod.RULES[k] = True
            print("%s  %-18s off: %-26s %s" % (
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
