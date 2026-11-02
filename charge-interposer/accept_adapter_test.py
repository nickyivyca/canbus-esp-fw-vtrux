#!/usr/bin/env python3
"""Tests of accept_adapter.py, the charge interposer's adapter for the
acceptance runner (shared/bench ACCEPT-SPEC A15). No hardware.

    ACCEPT_CORE=<a shared/bench with vtrux_bench.accept> \\
        py -3.14 -B charge-interposer/accept_adapter_test.py [--can-fail]

ACCEPT_CORE names the acceptance runner's core this adapter is tested
against: until the core is tagged, the shared/bench of the
shared/bench/accept-runner worktree; after, the pinned ../shared-<tag>/'s.
No default: unset is a refusal. --can-fail runs every test first unchanged
(the control, which must pass), then under each mutant of accept_adapter.py
(an edited copy in this run's folder; an edit not found exactly once counts
as a failure); every mutant must make a test fail.

Each test names what it checks. Throwaway folders come from scratch_dir.py
and are removed when the test ends.
"""

import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CORE = os.environ.get("ACCEPT_CORE", "").strip()
if not CORE or not os.path.isdir(os.path.join(CORE, "vtrux_bench", "accept")):
    sys.exit("REFUSED: ACCEPT_CORE must name a shared/bench holding vtrux_bench/accept "
             "(the acceptance runner's core this adapter is tested against)")
sys.path.insert(0, CORE)
sys.path.insert(0, HERE)

import scratch_dir                                   # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402

MODULE = os.path.join(HERE, "accept_adapter.py")


def load(path):
    spec = importlib.util.spec_from_file_location("accept_adapter_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


# ---- the tests --------------------------------------------------------------
def test_identity(A, work):
    """A15: the adapter is the charge interposer's: its suites are the three
    in bench_suite.py, committed at HEAD; its product files are the suite's
    own split (bench_suite.product_files), so the runner's digest and the
    chain runner's agree."""
    import bench_suite
    assert A.PROJECT == "charge-interposer" and A.ROLE == bench_suite.ROLE, (A.PROJECT, A.ROLE)
    assert A.SUITES == {n: "charge-interposer/bench_suite.py:%s" % n
                        for n in ("emulated", "bench", "harness")}, A.SUITES
    assert sorted(A.product_files(REPO)) == sorted(bench_suite.product_files(REPO))


def test_catalogue(A, work):
    """A8, A15: the harness-test catalogue is the suite's own lists (the
    Python tests, the bench drivers' self-tests and can-fails, the local
    harness checks), each entry a committed file in this tree, with its
    arguments, tags and the paths it covers; ids unique."""
    import bench_suite as BS
    cat = A.catalogue(REPO, git(REPO, "rev-parse", "HEAD"))
    ids = [e["id"] for e in cat]
    assert len(ids) == len(set(ids)), "duplicate ids"
    by = {e["id"]: e for e in cat}
    for s in BS.SELF_TESTS:
        e = by["st." + s.replace("_", "-")]
        assert e["path"] == "charge-interposer/bench_drivers/%s.py" % s and e["args"] == ["--self-test"], e
    for s in BS.CAN_FAILS:
        assert by["cf." + s.replace("_", "-")]["args"] == ["--can-fail"]
    for t in BS.HARNESS_PY_TESTS + BS.LOGIC_PY_TESTS:
        assert by[t[:-3].replace("_", "-")]["path"] == "charge-interposer/" + t
    for cid, _argv in BS.HARNESS_LOCAL:
        assert cid in by, cid
    for e in cat:
        assert git(REPO, "cat-file", "-t", "HEAD:" + e["path"]) == "blob", e["path"]
        assert e["tags"] and e["covers"] and e["shell"] in A.SHELLS, e


def test_list_loads(A, work):
    """A2, A3, A8 with this adapter: a list naming this tree, a catalogue
    set and one check is accepted, and the set takes exactly its tag's
    entries."""
    import bench_suite as BS
    clone = os.path.join(work, "clone")
    git(REPO, "worktree", "list")                      # git is there
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", REPO, clone], check=True)
    head = git(REPO, "rev-parse", "HEAD")
    git(clone, "checkout", "-q", "--detach", head)
    git(clone, "config", "user.name", "test")
    git(clone, "config", "user.email", "test@example.invalid")
    lst = os.path.join(clone, "charge-interposer", "acceptance", "t.toml")
    os.makedirs(os.path.dirname(lst))
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        f.write('[step]\nid = "t"\nproject = "charge-interposer"\nordered_by = "test"\n'
                'ordered_on = "2026-11-01"\nspec = ["9.1"]\nverification = "9.1"\n\n'
                '[trees.head]\nproject = "%s"\n\n[[set]]\nid = "st"\ntree = "head"\n'
                'select = ["tag:self-test"]\n\n[[check]]\nid = "paths"\ntree = "head"\n'
                'run = ["charge-interposer/paths_test.py"]\n' % head)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "list")
    got = L.load(clone, "%s:charge-interposer/acceptance/t.toml"
                 % git(clone, "rev-parse", "HEAD"), A)
    st = [c for c in got.checks if c.get("from_set") == "st"]
    assert len(st) == len(BS.SELF_TESTS), (len(st), len(BS.SELF_TESTS))
    assert any(c["id"] == "paths" for c in got.checks)


def test_tree_names(A, work):
    """A15: a check in a tree is told that tree: INTERPOSER_CODE (which
    paths.code_dir() checks) and a suite run's interposer_code setting are
    the tree's own charge-interposer/."""
    t = {"project_dir": os.path.join(work, "tree-x", "repo"), "shared_dirs": {}}
    want = os.path.join(t["project_dir"], "charge-interposer")
    assert A.tree_env(t) == {"INTERPOSER_CODE": want}, A.tree_env(t)
    assert A.tree_settings(t) == {"interposer_code": want}, A.tree_settings(t)


def test_shells(A, work):
    """A15: a check's shell: python is the host's python from the settings;
    git-bash is the settings' bash by its full path (a bare "bash" on
    Windows is WSL's), refused when the settings name none."""
    s = {"hosts": {"local": {"python": ["py", "-3.14"]}}, "bash": r"C:\Program Files\Git\bin\bash.exe"}
    assert A.shell_argv("python", s) == ["py", "-3.14", "-B"], A.shell_argv("python", s)
    assert A.shell_argv("git-bash", s) == [s["bash"]]
    try:
        A.shell_argv("git-bash", {"hosts": s["hosts"]})
    except A.AdapterError:
        pass
    else:
        raise AssertionError("git-bash with no bash setting was taken")
    assert A.default_shell("x/run.sh") == "git-bash" and A.default_shell("x/t.py") == "python"


def test_identifier_scan(A, work):
    """A13: the identifier scan is reverse-it's prepush scan (its patterns
    and private value file) over every file of a record: each file's hits
    by category and count, never the text that matched."""
    d = os.path.join(work, "rec")
    os.makedirs(os.path.join(d, "chain"))
    planted = "10.20.30.40"
    with open(os.path.join(d, "chain", "check.log"), "w", encoding="utf-8") as f:
        f.write("ok\nreached %s twice: %s\n" % (planted, planted))
    with open(os.path.join(d, "clean.txt"), "w", encoding="utf-8") as f:
        f.write("nothing here\n")
    hits = A.identifier_scan(d)
    assert any(h.startswith("chain/check.log:") and "ipv4 x2" in h for h in hits), hits
    assert not any("clean.txt" in h for h in hits), hits
    assert not any(planted in h for h in hits), "a hit shows the text it matched"
    # a value-file value (the charge-interposer reviewer, 2026-11-01): a fake
    # value file, a fake MAC planted in a record, written bare as a log has it
    fake = "02:00:00:00:12:34"                       # locally administered, no board's
    vf = os.path.join(work, "fake_values.txt")
    with open(vf, "w", encoding="utf-8") as f:
        f.write("# a fake value file\nmac %s\n" % fake)
    with open(os.path.join(d, "board.txt"), "w", encoding="utf-8") as f:
        f.write("chip mac %s\n" % fake.replace(":", ""))
    hits = A.identifier_scan(d, values=vf)
    bare = fake.replace(":", "")
    assert any(h.startswith("board.txt:") and "value:mac" in h and "x1" in h for h in hits), hits
    for h in hits:
        assert fake not in h and bare not in h and bare.upper() not in h, \
            "a hit shows the value it matched: %s" % h


def test_artifacts_row(A, work):
    """A13: the record's row goes into the artifacts README beside the runs
    folder, at the top of its runs/ rows; a README with no runs/ rows is a
    failure the close reports, never a row put anywhere."""
    art = os.path.join(work, "art")
    runs = os.path.join(art, "runs")
    os.makedirs(os.path.join(runs, "accept_s_1"))
    readme = os.path.join(art, "README.md")
    with open(readme, "w", encoding="utf-8", newline="\n") as f:
        f.write("# x\n\n| Path | What |\n|---|---|\n| `runs/old_1/` | an old run |\n")
    summary = {"step": {"id": "s", "verification": "9.1"},
               "verdict": {"lines": ["ACCEPTANCE PASS"]}}
    row = A.artifacts_row(os.path.join(runs, "accept_s_1"), summary)
    with open(readme, encoding="utf-8") as f:
        lines = f.read().splitlines()
    i = lines.index(row)
    assert lines[i + 1].startswith("| `runs/old_1/`") and "accept_s_1" in row \
        and "ACCEPTANCE PASS" in row, lines
    with open(readme, "w", encoding="utf-8", newline="\n") as f:
        f.write("# x\n\nno table\n")
    try:
        A.artifacts_row(os.path.join(runs, "accept_s_1"), summary)
    except A.AdapterError:
        return
    raise AssertionError("a README with no runs/ rows took the row")


TESTS = [test_identity, test_catalogue, test_list_loads, test_tree_names, test_shells,
         test_identifier_scan, test_artifacts_row]

MUTANTS = [
    ("suites from another file", '"charge-interposer/bench_suite.py:%s"', '"charge-interposer/suite.py:%s"'),
    ("product files not the suite's", "    return bench_suite.product_files(root)\n",
     "    return bench_suite.product_files(root)[1:]\n"),
    ("self-tests without their flag", '["--self-test"]', "[]"),
    ("a harness check dropped", "    for cid, argv in BS.HARNESS_LOCAL:\n",
     "    for cid, argv in BS.HARNESS_LOCAL[1:]:\n"),
    ("INTERPOSER_CODE not the tree's", 'return {"INTERPOSER_CODE": _code(tree)}',
     'return {"INTERPOSER_CODE": HERE}'),
    ("interposer_code not the tree's", 'return {"interposer_code": _code(tree)}',
     'return {"interposer_code": HERE}'),
    ("a bare bash", '        raise AdapterError("the settings name no bash',
     '        return ["bash"]\n        raise AdapterError("the settings name no bash'),
    ("the hit's text shown", "                        key = cat\n",
     "                        key = line[s:e]\n"),
    ("counts not kept", '"%s: %s x%d" % (rel, cat, k)', '"%s: %s" % (rel, cat)'),
    ("the row anywhere", '        raise AdapterError("%s has no runs/ rows',
     '        lines.append(""); first = len(lines)\n        if 0: raise AdapterError("%s has no runs/ rows'),
]


def run_all(mod_path, base):
    A = load(mod_path)
    bad = []
    for t in TESTS:
        work = os.path.join(base, t.__name__)
        os.makedirs(work)
        try:
            t(A, work)
            print("ok    %s" % t.__name__, flush=True)
        except Exception as e:
            print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:240]), flush=True)
            bad.append(t.__name__)
    return bad


def main(argv):
    with scratch_dir.run("accept-adapter-test") as base:
        if "--can-fail" not in argv:
            bad = run_all(MODULE, os.path.join(base, "tests"))
            print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
            return 1 if bad else 0
        print("-- control: the unmutated adapter", flush=True)
        bad = run_all(MODULE, os.path.join(base, "control"))
        if bad:
            print("\nREFUSED: the control fails (%s)" % ", ".join(bad))
            return 1
        src = open(MODULE, encoding="utf-8").read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)" % (n, name, src.count(old)))
                continue
            mdir = os.path.join(base, "m%d" % n)
            os.makedirs(mdir)
            mp = os.path.join(mdir, "accept_adapter.py")
            with open(mp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_all(mp, os.path.join(mdir, "tests"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad else
                                      "STILL PASSES -- nothing checks it"))
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
