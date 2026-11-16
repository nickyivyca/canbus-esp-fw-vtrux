"""Tests of a required suite's earlier run (vtrux_bench/accept/reuse.py, and
its use in run.py, close.py, listfile.py and the chain runner's
settings.requires_run): ACCEPT-SPEC A17. No hardware. Each test names what
it checks; each refusal reason has a test, and each is a can-fail of the
rule that gives it.

    python -B shared/bench/tests/test_accept_reuse.py            every test
    python -B shared/bench/tests/test_accept_reuse.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with the rule it guards switched off (reuse.RULES; every rule has
        one)

A small git repo holds a project ("proj") with the fake adapter committed
as proj/accept_adapter.py: an emulated suite whose harness is
proj/harness/sim.py, the pins file proj/pins.json and a shared component's
file shared/comp/tool.py, and a bench suite that requires the emulated
one. Four earlier acceptances are run once and closed: "good" (the whole
emulated run, its comparison held), "nohold" (its comparison did not
hold), "nocomp" (no comparison names it) and "subset" (part of the suite).
Each test then runs a second acceptance, on a commit of its own, that names
one of them in a [[reuse]]. Everything is in this run's own folder under
the worktree's scratch/ (tests/_scratch.py), removed at the end with
vtrux_bench/scratch.py.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import record as R_          # noqa: E402
from vtrux_bench.accept import reuse as RU           # noqa: E402
from vtrux_bench.accept import run as RUN            # noqa: E402

BASE = _scratch.run_dir("accept_reuse")
MAC = "02:00:00:00:00:01"                    # locally administered, no real board's

with open(os.path.join(HERE, "fake_adapter.py"), encoding="utf-8") as _f:
    ADAPTER = _f.read()

SUITE = '''import os, subprocess
from vtrux_bench.run.model import Check, Group, Suite

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _code(ctx):
    c = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True,
                       text=True).stdout.strip()
    return {"repo": ROOT, "commit": c, "dirty": False}


def _product(root):
    return ["proj/check.py", "proj/suite.py"]


EMULATED = Suite(name="proj-emu", role="tester (test-suite)", groups=[
    Group("a", [Check("sim", [os.path.join(HERE, "harness", "sim.py")], python=True),
                Check("two", ["-c", "pass"], python=True),
                Check("later", ["-"], skip="by ruling")])],
    code_info=_code, product_files=_product)
BENCH = Suite(name="proj-bench", role="tester (test-suite)", groups=[
    Group("b", [Check("y", ["-c", "pass"], python=True)])],
    requires="proj-emu", code_info=_code, product_files=_product)
'''

FILES = {
    "proj/accept_adapter.py": ADAPTER,
    "proj/build.py": (
        "import os, sys\n"
        "out, variant = sys.argv[1], sys.argv[2]\n"
        "os.makedirs(out, exist_ok=True)\n"
        "open(os.path.join(out, 'app.bin'), 'wb').write(('image ' + variant).encode())\n"
        "open(os.path.join(out, 'app.elf'), 'wb').write(('elf ' + variant).encode())\n"),
    "proj/check.py": "pass\n",
    "proj/suite.py": SUITE,
    "proj/harness/sim.py": "print('sim ok')\n",
    "proj/pins.json": '{"comp": "v1"}\n',
    "proj/notes.md": "notes\n",
    "shared/comp/tool.py": "VERSION = 1\n",
    ".gitignore": "/scratch/\n",
}

HEAD = '''
[step]
id = "{STEP}"
project = "proj"
ordered_by = "reviewer"
ordered_on = "2026-11-16"
spec = ["9.1"]
verification = "9.1 Emulation"

[trees.base]
project = "{C}"
shared = {{ comp = "{COMP}" }}
'''

EARLIER = HEAD + '''
[[check]]
id = "chk"
tree = "base"
run = ["proj/check.py"]

[[suite_run]]
id = "emu"
tree = "base"
suite = "emulated"
{SUBSET}
{COMPARE}'''

COMPARE = '''
[[compare]]
id = "c-emu"
comparer = "fake-count"
a = "emu"
b = "{B}"
expect = "identical"
'''

LATER = HEAD + '''
[[build]]
id = "fw"
tree = "base"
target = "fw"
options = {{ variant = "man" }}

[[reuse]]
id = "emulated"
tree = "base"
suite = "emulated"
record = "{REC}"
run = "emu"
{RUNS}
[[compare]]
id = "c-reused"
comparer = "fake-count"
a = "emulated"
b = "emulated"
expect = "identical"
'''

BENCH_RUN = '''
[[suite_run]]
id = "bench"
tree = "base"
suite = "bench"
after = ["emulated"]
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


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def tree_hashes(folder):
    out = {}
    for d, _dirs, files in os.walk(folder):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


def resum(folder):
    rows = []
    for rel, h in tree_hashes(folder).items():
        rel = rel.replace("\\", "/")
        if rel != "SHA256SUMS":
            rows.append((rel, h))
    with open(os.path.join(folder, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("%s  %s\n" % (h, rel) for rel, h in sorted(rows)))


class Base:
    """The repo and the four earlier records, made once."""
    made = None

    def __init__(self):
        self.dir = os.path.join(BASE, "base")
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel, text in FILES.items():
            write(self.repo, rel, text)
        self.c1 = commit(self.repo, "C1")
        write(self.repo, "shared/comp/tool.py", "VERSION = 2\n")
        self.cs = commit(self.repo, "shared comp v2")
        write(self.repo, "shared/comp/tool.py", "VERSION = 1\n")
        self.cr = commit(self.repo, "shared comp v1 again")
        self.runs = os.path.join(self.dir, "runs")
        os.makedirs(self.runs)
        self.settings = os.path.join(self.dir, "machine.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(dict(runs_root=self.runs, hosts={"local": {"python": [sys.executable]}},
                           bench_mac=MAC,
                           bench_holder=os.path.join(self.dir, "bench-holder.md")), f)
        self.n = 0
        self.rec = {}
        for name, subset, compare in (
                ("good", "", COMPARE.format(B="emu")),
                ("nohold", "", COMPARE.format(B="chk")),
                ("nocomp", "", ""),
                ("subset", 'checks = ["a/sim"]', COMPARE.format(B="emu"))):
            git(self.repo, "checkout", "-q", "-B", "e-" + name, self.cr)
            write(self.repo, "proj/acceptance/%s.toml" % name, EARLIER.format(
                STEP="e" + name, C=self.c1, COMP=self.c1, SUBSET=subset, COMPARE=compare))
            c = commit(self.repo, "list " + name)
            rc, out, rec = self.accept(c, name)
            if rc not in (0, 1) or rec is None:
                raise AssertionError("earlier acceptance %s: rc %s\n%s" % (name, rc, out[-2000:]))
            self.rec[name] = os.path.basename(rec)

    def accept(self, c, name):
        """One acceptance of proj/acceptance/<name>.toml at commit c. (rc,
        console text, record folder or None)."""
        before = set(os.listdir(self.runs))
        real = R_.start
        R_.start = lambda runs, step, out=None, **kw: real(runs, step, out=out,
                                                           ceiling=self.dir, **kw)
        real_ident = RUN.runner_identity
        RUN.runner_identity = lambda path=None: {"commit": "f" * 40, "dirty": False}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = RUN.main(["run", "--repo", self.repo, "--list",
                               "%s:proj/acceptance/%s.toml" % (c, name),
                               "--adapter", "proj/accept_adapter.py",
                               "--settings", self.settings])
        finally:
            R_.start = real
            RUN.runner_identity = real_ident
        new = sorted(n for n in set(os.listdir(self.runs)) - before if n.startswith("accept_"))
        return rc, buf.getvalue(), os.path.join(self.runs, new[0]) if new else None

    def later(self, rec="good", change=None, comp=None, runs=BENCH_RUN):
        """A second acceptance naming the earlier record rec, on a commit of
        its own from the earlier tree's state with change {path: text or
        None} applied. (rc, console, record folder)."""
        self.n += 1
        name = "later%d" % self.n
        git(self.repo, "checkout", "-q", "-B", name, self.cr)
        for rel, text in (change or {}).items():
            if text is None:
                os.remove(os.path.join(self.repo, rel))
            else:
                write(self.repo, rel, text)
        c = commit(self.repo, "change " + name) if change else self.cr
        write(self.repo, "proj/acceptance/%s.toml" % name, LATER.format(
            STEP="l%d" % self.n, C=c, COMP=comp or self.c1, REC=self.rec.get(rec, rec),
            RUNS=runs))
        lc = commit(self.repo, "list " + name)
        return self.accept(lc, name)

    def clone_record(self, src, dst, edit=None, sums="resum"):
        """A copy of record src as dst (its run folders renamed), with
        edit(record.json of its emu run) applied; SHA256SUMS then
        recomputed ("resum"), left as it was ("keep") or removed ("drop")."""
        s, d = os.path.join(self.runs, self.rec[src]), os.path.join(self.runs, dst)
        if os.path.exists(d):
            S.remove(d)
        shutil.copytree(s, d)
        rd = os.path.join(d, "runs")
        for n in os.listdir(rd):
            os.rename(os.path.join(rd, n), os.path.join(rd, n.replace(self.rec[src], dst)))
        if edit:
            p = os.path.join(rd, "%s-emu" % dst, "record.json")
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            edit(r)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(r, f, indent=1)
        sums_p = os.path.join(d, "SHA256SUMS")
        if sums == "keep":
            with open(sums_p, encoding="utf-8") as f:
                text = f.read().replace(self.rec[src], dst)
            with open(sums_p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        elif sums == "drop":
            os.remove(sums_p)
        else:
            resum(d)
        return dst


def base():
    if Base.made is None:
        Base.made = Base()
    return Base.made


def refused(rc, out, rec, *words):
    """rc 2, REFUSED naming every word, and nothing built or run (A17: at
    the start, before any lock or build)."""
    assert rc == 2, (rc, out[-1500:])
    assert "REFUSED" in out and all(w in out for w in words), (words, out[-1500:])
    assert rec is not None and not os.path.exists(os.path.join(rec, "builds")) and \
        not os.path.exists(os.path.join(rec, "chain")), sorted(os.listdir(rec or BASE))


# ---- the tests --------------------------------------------------------------
def test_reuse_pass():
    """A17: the earlier record's whole emulated run, its comparison held, on
    an equal product digest and an equal harness digest (a commit that
    changes neither, here a note), stands as this acceptance's emulated
    run: ACCEPTANCE PASS; the bench suite's requirement is met by that run,
    read where it is (its record names it); the comparison naming the
    reuse reads it; the record names the earlier record, the run and both
    pairs of digests; nothing is copied into this record and the earlier
    record is unchanged."""
    b = base()
    early = os.path.join(b.runs, b.rec["good"])
    snap = tree_hashes(early)
    rc, out, rec = b.later(change={"proj/notes.md": "notes, changed\n"})
    assert rc == 0, (rc, out[-2000:])
    with open(os.path.join(rec, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    assert "ACCEPTANCE PASS" in readme, readme[:1500]
    with open(os.path.join(early, "runs", "%s-emu" % b.rec["good"], "record.json"),
              encoding="utf-8") as f:
        p = json.load(f)["code"]["product_digest"]
    for w in ("reused suite run emulated (suite emulated): acceptance record %s, its run emu"
              % b.rec["good"], "its comparison held there: c-emu",
              "product digest: earlier %s, this tree %s" % (p, p), "harness digest (3 files)",
              "| reuse | emulated | accepted | accepted |",
              "- compare c-reused (identical): held"):
        assert w in readme, (w, readme[:3000])
    with open(os.path.join(rec, "verification_skeleton.md"), encoding="utf-8") as f:
        assert "acceptance record %s, its run emu" % b.rec["good"] in f.read()
    runs = os.listdir(os.path.join(rec, "runs"))
    assert runs == ["%s-bench" % os.path.basename(rec)], runs
    with open(os.path.join(rec, "runs", runs[0], "record.json"), encoding="utf-8") as f:
        met = json.load(f).get("requires_met") or ""
    assert "%s-emu" % b.rec["good"] in met, met
    assert tree_hashes(early) == snap, "the earlier record changed"


def test_harness_changed():
    """A17 can-fail: the required suite's harness changed (one of its own
    files): refused at the start, naming the file and both digests."""
    rc, out, rec = base().later(change={"proj/harness/sim.py": "print('sim ok, v2')\n"})
    refused(rc, out, rec, "harness of suite emulated changed", "proj/harness/sim.py")


def test_shared_pin_changed():
    """A17 can-fail (the gen-inhibit reviewer's tightening (b)): the shared
    pins file, which the required suite runs through, changed: refused."""
    rc, out, rec = base().later(change={"proj/pins.json": '{"comp": "v2"}\n'})
    refused(rc, out, rec, "harness of suite emulated changed", "proj/pins.json")


def test_shared_at_tree():
    """A17: a shared component's file in the harness list is read at each
    tree's commit of that component: the list's override moves this tree's
    component to a commit where the file differs, with the project's own
    copy unchanged: refused."""
    b = base()
    rc, out, rec = b.later(comp=b.cs)
    refused(rc, out, rec, "harness of suite emulated changed", "shared/comp/tool.py")


def test_missing_differs():
    """A17 (the gen-inhibit reviewer's tightening (a)): this tree's adapter
    names a harness file missing from a tree, here from both: the harness
    differs, refused, though the digests over what is there agree."""
    rc, out, rec = base().later(change={"proj/harness/extra.txt": "proj/harness/nowhere.dat\n"})
    refused(rc, out, rec, "missing from a tree", "proj/harness/nowhere.dat")


def test_product_changed():
    """A17 can-fail: the product changed (a product file): refused."""
    rc, out, rec = base().later(change={"proj/check.py": "pass  # changed\n"})
    refused(rc, out, rec, "the product changed")


def test_subset():
    """A17 can-fail: the earlier run was part of the suite (a subset run):
    refused."""
    rc, out, rec = base().later(rec="subset")
    refused(rc, out, rec, "subset run", "the whole suite must have run")


def test_compare_not_held():
    """A17 can-fail: the earlier run's comparison did not hold in its
    record: refused, naming it."""
    rc, out, rec = base().later(rec="nohold")
    refused(rc, out, rec, "its comparison did not hold", "c-emu DID NOT HOLD")


def test_no_compare():
    """A17: no comparison of the earlier list has the run as a side, so
    none held for it: refused."""
    rc, out, rec = base().later(rec="nocomp")
    refused(rc, out, rec, "no comparison in the earlier list")


def test_not_closed():
    """A17: a record with no SHA256SUMS is not a closed record: refused."""
    b = base()
    rc, out, rec = b.later(rec=b.clone_record("good", "open1", sums="drop"))
    refused(rc, out, rec, "not a closed acceptance record")


def test_record_changed():
    """A17: the run's record.json no longer matches the closed record's
    SHA256SUMS: refused."""
    b = base()

    def edit(r):
        r["results"][0]["detail"] = "edited after the close"
    rc, out, rec = b.later(rec=b.clone_record("good", "edited1", edit=edit, sums="keep"))
    refused(rc, out, rec, "does not match the record's SHA256SUMS")


def test_not_finished():
    """A17: the run did not finish (its record has no end): refused."""
    b = base()
    rc, out, rec = b.later(rec=b.clone_record("good", "unended1",
                                              edit=lambda r: r.pop("ended", None)))
    refused(rc, out, rec, "did not finish")


def test_not_required():
    """A17: an earlier run stands only for a suite a suite run of the list
    requires: a list with no bench suite run is refused."""
    rc, out, rec = base().later(runs="")
    refused(rc, out, rec, "no suite run of the list is of a suite that requires")


def test_not_alone():
    """A17: a list that both reuses the emulated suite and runs it is
    refused (the requirement would have two runs to read)."""
    rc, out, rec = base().later(runs=BENCH_RUN + '''
[[suite_run]]
id = "emu-again"
tree = "base"
suite = "emulated"
''')
    refused(rc, out, rec, "runs suite emulated too")


def test_parse_readme():
    """parse_readme reads close.readme's lines: the list, each tree's
    project, product digest and shared components (and stops at the next
    top-level line), and each comparison's outcome."""
    h = "a" * 40
    text = "\n".join([
        "- list: `proj/acceptance/x.toml` at %s, sha256 %s" % (h, "b" * 64),
        "- runner (shared/bench): %s" % h,
        "- tree base: project %s (tree %s), product digest pd1" % ("1" * 40, "2" * 40),
        "  - shared bench: %s (tree %s)" % ("3" * 40, "4" * 40),
        "- build fw: PASS, variant man, toolchain t, ELF sha256 e",
        "  - shared not: %s (tree %s)" % ("5" * 40, "6" * 40),
        "- compare c1 (listed): held", "- compare c2 (same-verdicts): DID NOT HOLD"])
    p = RU.parse_readme(text)
    assert p["list"] == ("proj/acceptance/x.toml", h), p
    assert p["trees"] == {"base": {"project": "1" * 40, "product_digest": "pd1",
                                   "shared": {"bench": "3" * 40}}}, p
    assert p["compares"] == {"c1": True, "c2": False}, p


TESTS = [test_reuse_pass, test_harness_changed, test_shared_pin_changed, test_shared_at_tree,
         test_missing_differs, test_product_changed, test_subset, test_compare_not_held,
         test_no_compare, test_not_closed, test_record_changed, test_not_finished,
         test_not_required, test_not_alone, test_parse_readme]

MUTANTS = [
    ("required_by", test_not_required),
    ("alone", test_not_alone),
    ("closed", test_not_closed),
    ("sums", test_record_changed),
    ("finished", test_not_finished),
    ("whole", test_subset),
    ("compare_named", test_no_compare),
    ("compare_held", test_compare_not_held),
    ("product", test_product_changed),
    ("harness", test_harness_changed),
    ("harness", test_shared_pin_changed),
    ("missing_differs", test_missing_differs),
    ("shared_at_tree", test_shared_at_tree),
    ("in_place", test_reuse_pass),
    ("named", test_reuse_pass),
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
        missing = sorted(set(RU.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            RU.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                RU.RULES[k] = True
            print("%s  %-16s off: %-24s %s" % (
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
