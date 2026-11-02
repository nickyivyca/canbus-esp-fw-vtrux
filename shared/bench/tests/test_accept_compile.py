"""Tests of the acceptance runner's builds, run settings and chain suite
(vtrux_bench/accept/builds.py, settings.py, compile.py): ACCEPT-SPEC A5, A6,
A7, and A14 for what they leave. No hardware. Each test names the
requirements it checks.

    python -B shared/bench/tests/test_accept_compile.py            every test
    python -B shared/bench/tests/test_accept_compile.py --can-fail  the
        control first (every test unchanged, which must pass), then each test
        again with a rule it guards switched off (the RULES of the three
        modules; every rule has one)

Each test builds a small git repo in this run's own folder under the
worktree's scratch/ (tests/_scratch.py), with a fake build (proj/build.py)
and fake checks, and runs them through the real chain runner with
tests/fake_adapter.py. The folder is removed at the end with
vtrux_bench/scratch.py.
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
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import builds as B           # noqa: E402
from vtrux_bench.accept import compile as C          # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402
from vtrux_bench.accept import settings as ST        # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402

BASE = _scratch.run_dir("accept_compile")
N = [0]
MAC = "02:00:00:00:00:01"                    # locally administered, no real board's

FILES = {
    "proj/build.py": (
        "import os, sys\n"
        "out, variant = sys.argv[1], sys.argv[2]\n"
        "if variant == 'broken':\n    sys.exit(2)\n"
        "os.makedirs(out, exist_ok=True)\n"
        "open(os.path.join(out, 'app.bin'), 'wb').write(('image ' + variant).encode())\n"
        "open(os.path.join(out, 'app.elf'), 'wb').write(('elf ' + variant).encode())\n"),
    "proj/envcheck.py": (
        "import os, sys\n"
        "bad = []\n"
        "if 'VTRUX_OPERATOR_ONLY' in os.environ: bad.append('operator var seen')\n"
        "if os.environ.get('LIST_ENV') != 'x': bad.append('LIST_ENV missing')\n"
        "if os.environ.get('FAKE_MACHINE_DIR') != 'm': bad.append('machine var missing')\n"
        "if not os.environ.get('PATH'): bad.append('PATH missing')\n"
        "here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "t = os.environ.get('FAKE_TREE', '')\n"
        "if not (t and os.path.samefile(t, here)): bad.append('FAKE_TREE is not my tree: %r' % t)\n"
        "print('envcheck:', bad or 'ok')\n"
        "sys.exit(1 if bad else 0)\n"),
    "proj/fail.py": "import sys\nsys.exit(1)\n",
    "proj/check.py": "print('check')\n",
    "proj/suite.py": "# suite\n",
    "shared/comp/tool.py": (
        "import os, sys\n"
        "print('tool at', os.path.abspath(__file__))\n"),
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

[trees.new]
project = "{C1}"
shared = {{ comp = "comp-v2" }}

[[build]]
id = "fw"
tree = "base"
target = "fw"
options = {{ variant = "{VARIANT}" }}

[[check]]
id = "env"
tree = "base"
run = ["proj/envcheck.py"]
env = {{ LIST_ENV = "x" }}

[[check]]
id = "tool"
tree = "new"
run = ["shared/comp/tool.py", "{{run_dir}}"]
after = ["env"]

[[check]]
id = "bad"
tree = "base"
run = ["proj/fail.py"]
expect = "FAIL"
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
    """The repo (C1; shared/comp changed at C2, tagged comp-v2; the list at
    C3), the record, the trees and the machine settings."""

    def __init__(self, variant="man", machine=None):
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
        self.c1 = self.commit("C1")
        write(self.repo, "shared/comp/tool.py", FILES["shared/comp/tool.py"] + "# C2\n")
        self.c2 = self.commit("C2")
        git(self.repo, "tag", "comp-v2")
        FA.PINS.clear()
        FA.PINS["comp"] = self.c1
        write(self.repo, "proj/acceptance/s.toml", LIST.format(C1=self.c1, VARIANT=variant))
        c3 = self.commit("list")
        self.alist = L.load(self.repo, "%s:proj/acceptance/s.toml" % c3, FA)
        self.runs = os.path.join(self.dir, "runs")
        self.said = []
        self.rec = R.start(self.runs, "s1", out=self.said.append, ceiling=BASE)
        self.made = S.Made()
        self.parent = os.path.join(self.dir, "work")
        self.trees = T.make_trees(self.repo, self.alist, FA, self.parent, self.made,
                                  say=self.rec.say)
        self.machine_path = os.path.join(self.dir, "machine.json")
        m = dict(runs_root=os.path.join(self.dir, "not-used"),
                 hosts={"local": {"python": [sys.executable]}}, bench_mac=MAC,
                 bench_holder=os.path.join(self.dir, "bench-holder.md"))
        m.update(machine or {})
        with open(self.machine_path, "w", encoding="utf-8") as f:
            json.dump(m, f)
        self.machine = m

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def base_env(self):
        return C.machine_env(FA, dict(os.environ, FAKE_MACHINE_DIR="m"))

    def builds(self):
        # the check trees are passed, as the runner passes them: a build must
        # still make a tree of its own
        return B.run_builds(self.repo, self.alist, FA, self.machine, self.rec, self.made,
                            self.parent, self.base_env(), trees=self.trees)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ---- the tests --------------------------------------------------------------
def test_build_record():
    """A5: a build runs in a clean tree of its own (not a check's), through
    the adapter's target and shell, and writes a build record: its images
    with their sha256, the ELF's sha256, the variant and the toolchain, in
    the record folder's builds/<id>.json. The check trees stay clean."""
    f = Fixture()
    builds = f.builds()
    b = builds["fw"]
    assert b["verdict"] == "PASS", b
    tree_dirs = {os.path.normcase(t["dir"]) for t in f.trees.values()}
    assert os.path.normcase(b["tree_dir"]) not in tree_dirs, "the build used a check's tree"
    app = b["images"]["app"]["path"]
    assert os.path.isfile(app) and b["images"]["app"]["sha256"] == sha(app), b
    assert b["elf"]["sha256"] == sha(b["elf"]["path"]), b
    assert b["variant"] == "man" and b["toolchain"] == "fake-cc 1.0", b
    with open(app, "rb") as fh:
        assert fh.read() == b"image man", "the build did not get its options"
    with open(os.path.join(f.rec.path, "builds", "fw.json"), encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["images"]["app"]["sha256"] == b["images"]["app"]["sha256"], on_disk
    for t in f.trees.values():
        T.check_tree(t["project_dir"], t["record"]["project"]["commit"])


def test_build_is_build_work():
    """A5: builds are build work under AGENTS.md "Builds during wall-clock
    tests": each runs as a "build" check of the chain runner, so its
    throttle applies, and its chain record is inside the acceptance
    record, holding the masked settings (A6)."""
    f = Fixture()
    suite, _ctx = B.build_suite(f.repo, f.alist, FA, f.machine, f.made, f.parent,
                                f.base_env(), f.rec.say)
    cls = {c.cls for g in suite.groups for c in g.checks}
    assert cls == {"build"}, cls
    f.builds()
    chain = os.path.join(f.rec.path, "chain")
    runs = [n for n in os.listdir(chain) if n.startswith("accept-builds")]
    assert runs, os.listdir(chain)
    with open(os.path.join(chain, runs[0], "record.json"), encoding="utf-8") as fh:
        text = fh.read()
    assert MAC not in text and "<MAC>" in text, "the builds' chain record holds the real settings (A6)"


def test_build_failure():
    """A5: a build that fails is recorded as failed, with no image."""
    f = Fixture(variant="broken")
    b = f.builds()["fw"]
    assert b["verdict"] == "FAIL" and not b.get("images"), b


def test_settings_from_builds():
    """A6: the run's settings are the machine's settings, with the keys that
    depend on a build filled from the build records by the adapter's key
    map, never by hand; A7: their runs_root is the record's runs/."""
    f = Fixture()
    builds = f.builds()
    path, s = ST.write_settings(f.machine_path, FA, builds, f.trees, f.rec, f.made, f.parent)
    with open(path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk == s, (on_disk, s)
    assert s["fw_image"] == builds["fw"]["images"]["app"]["path"], s
    assert s["fw_elf_sha256"] == builds["fw"]["elf"]["sha256"], s
    assert s["bench_mac"] == MAC and s["hosts"] == f.machine["hosts"], s
    # A7: nested suite runs make their records in the acceptance record
    assert s["runs_root"] == os.path.join(f.rec.path, "runs"), s["runs_root"]


def test_settings_masked_and_removed():
    """A6, A14: a masked copy of the run's settings goes in the record (no
    MAC in it); the unmasked file is in a scratch folder of the run's,
    never in the record, and goes with the run's Made."""
    f = Fixture()
    path, s = ST.write_settings(f.machine_path, FA, f.builds(), f.trees, f.rec, f.made, f.parent)
    masked = os.path.join(f.rec.path, "settings.masked.json")
    with open(masked, encoding="utf-8") as fh:
        text = fh.read()
    assert MAC not in text and "<MAC>" in text, text
    assert "fw_image" in text, "the masked copy lacks the build keys"
    assert not os.path.normcase(os.path.abspath(path)).startswith(
        os.path.normcase(os.path.abspath(f.rec.path))), "the unmasked settings are in the record"
    assert f.made.remove_all(out=lambda s: None) == []
    assert not os.path.exists(path), "the unmasked settings outlived the run"


def test_settings_no_passwords():
    """A6 (AGENTS.md: passwords are never read): a machine settings file
    with a password key is refused, naming the key and never its value."""
    f = Fixture(machine={"wifi_password": "hunter2-secret"})
    try:
        ST.write_settings(f.machine_path, FA, f.builds(), f.trees, f.rec, f.made, f.parent)
    except ST.SettingsError as e:
        assert "wifi_password" in str(e) and "hunter2" not in str(e), e
        assert all("hunter2" not in s for s in f.said), f.said
        return
    raise AssertionError("a password key was taken")


def test_env():
    """A7: a check's environment comes from the list (and the catalogue)
    and the machine variables, never from the operator's shell: a variable
    only in the runner's own environment does not reach it. A15: the
    adapter's tree_env(tree) names the check's own tree (the interposer's
    INTERPOSER_CODE, say)."""
    f = Fixture()
    os.environ["VTRUX_OPERATOR_ONLY"] = "1"
    try:
        base = f.base_env()
        assert "VTRUX_OPERATOR_ONLY" not in base and base.get("FAKE_MACHINE_DIR") == "m", base
        suite = C.compile_suite(f.alist, f.trees, FA, f.machine, base)
        rec = C.run_suite(suite, f.machine, f.rec)
    finally:
        del os.environ["VTRUX_OPERATOR_ONLY"]
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v.get("env/run") == "PASS", (v, open(os.path.join(
        rec["_dir"], "env", "run", "check.log")).read())


def test_compile_shape():
    """A7: one group per check, in the list's order; after becomes the
    group's after; the command is the shell's then the file in its tree
    (a shared file in that tree's checkout of the component), run from the
    tree's project checkout."""
    f = Fixture()
    suite = C.compile_suite(f.alist, f.trees, FA, f.machine, f.base_env())
    names = [g.name for g in suite.groups]
    assert names == ["env", "tool", "bad"], names
    g = {x.name: x for x in suite.groups}
    assert g["tool"].after == ["env"], g["tool"].after
    tool = g["tool"].checks[0]
    want = os.path.join(f.trees["new"]["shared_dirs"]["comp"], "shared", "comp", "tool.py")
    assert os.path.normcase(tool.argv[2]) == os.path.normcase(want), tool.argv
    assert tool.argv[:2] == [sys.executable, "-B"] and tool.argv[3] == "{run_dir}", tool.argv
    assert os.path.normcase(tool.cwd) == os.path.normcase(f.trees["new"]["project_dir"]), tool.cwd
    env = g["env"].checks[0]
    assert os.path.normcase(env.argv[2]) == os.path.normcase(
        os.path.join(f.trees["base"]["project_dir"], "proj", "envcheck.py")), env.argv


def test_chain_run():
    """A7: the compiled suite runs as an ordinary chain run whose record is
    inside the acceptance record (chain/), every check judged by the chain
    runner; the record holds the masked settings (A6)."""
    f = Fixture()
    suite = C.compile_suite(f.alist, f.trees, FA, f.machine, f.base_env())
    rec = C.run_suite(suite, f.machine, f.rec)
    assert os.path.normcase(os.path.dirname(rec["_dir"])) == os.path.normcase(
        os.path.join(f.rec.path, "chain")), rec["_dir"]
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"env/run": "PASS", "tool/run": "PASS", "bad/run": "FAIL"}, v
    with open(os.path.join(rec["_dir"], "record.json"), encoding="utf-8") as fh:
        text = fh.read()
    assert MAC not in text and "<MAC>" in text, "the chain record holds the real settings (A6)"
    with open(os.path.join(rec["_dir"], "tool", "run", "check.log"), encoding="utf-8") as fh:
        log = fh.read()
    where = os.path.normcase(f.trees["new"]["shared_dirs"]["comp"])
    assert any(ln.startswith("tool at ") and os.path.normcase(ln[8:]).startswith(where)
               for ln in log.splitlines()), log


TESTS = [test_build_record, test_build_is_build_work, test_build_failure,
         test_settings_from_builds, test_settings_masked_and_removed,
         test_settings_no_passwords, test_env, test_compile_shape, test_chain_run]

MUTANTS = [
    (B, "own_tree", test_build_record),
    (B, "options", test_build_record),
    (B, "image_sha", test_build_record),
    (B, "record_file", test_build_record),
    (B, "build_cls", test_build_is_build_work),
    (B, "chain_in_record", test_build_is_build_work),
    (B, "fail_recorded", test_build_failure),
    (B, "masked_record", test_build_is_build_work),
    (ST, "keymap", test_settings_from_builds),
    (ST, "runs_in_record", test_settings_from_builds),
    (ST, "masked", test_settings_masked_and_removed),
    (ST, "scratch_only", test_settings_masked_and_removed),
    (ST, "no_passwords", test_settings_no_passwords),
    (C, "base_env", test_env),
    (C, "list_env", test_env),
    (C, "machine_vars", test_env),
    (C, "tree_env", test_env),
    (C, "after", test_compile_shape),
    (C, "shared_path", test_compile_shape),
    (C, "cwd_tree", test_compile_shape),
    (C, "chain_in_record", test_chain_run),
    (C, "masked_record", test_chain_run),
]


# rules whose mutant is in another test file, which exercises them
ELSEWHERE = {ST: {"tree_settings"},           # test_accept_suite_runs.py
             B: {"remote_refused"},           # test_accept_remote.py, these
             C: {"remote_paths", "remote_shell", "chain_work_removed",
                 "remote_suite_refused"}}


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
                print("%s  %-32s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(tests) - bad, len(tests)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        for mod in (B, ST, C):
            missing = sorted(set(mod.RULES) - {k for m, k, _t in MUTANTS if m is mod}
                             - ELSEWHERE.get(mod, set()))
            if missing:
                print("FAIL  %s rules with no mutant: %s" % (mod.__name__, ", ".join(missing)))
                bad += 1
        caught = 0
        for mod, k, t in MUTANTS:
            mod.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                mod.RULES[k] = True
            print("%s  %-9s %-16s off: %-32s %s" % (
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
