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
    # a tree the list gives a twai_port checkout is also told the shared TWAI
    # fake in it, which host_bridge's run.sh builds against under
    # TWAI_FAKE=shared (port step 1b's acceptance E, by the runner)
    sd = os.path.join(work, "tree-x", "s-twai_port")
    t["shared_dirs"] = {"twai_port": sd}
    assert A.tree_env(t) == {"INTERPOSER_CODE": want, "TWAI_FAKE_DIR": os.path.join(
        sd, "shared", "twai_port", "test")}, A.tree_env(t)
    r = {"project_dir": "/w/accept-x/tree-m/p", "remote": True,
         "shared_dirs": {"twai_port": "/w/accept-x/tree-m/s-twai_port"}}
    assert A.tree_env(r) == {"INTERPOSER_CODE": "/w/accept-x/tree-m/p/charge-interposer",
                             "TWAI_FAKE_DIR": "/w/accept-x/tree-m/s-twai_port/shared/twai_port/test"}, \
        A.tree_env(r)


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
    # on a remote host (ACCEPT-SPEC A4, A7): its own python, -B once; its own
    # bash, which on a Linux host is plain bash
    s["hosts"]["madhouse"] = {"python": ["python3", "-B"], "ssh": "m", "workroot": "/w"}
    assert A.shell_argv("python", s, host="madhouse") == ["python3", "-B"], \
        A.shell_argv("python", s, host="madhouse")
    assert A.shell_argv("git-bash", s, host="madhouse") == ["bash"]
    try:
        A.shell_argv("powershell", s, host="madhouse")
    except A.AdapterError:
        pass
    else:
        raise AssertionError("powershell on madhouse was taken")


def test_madhouse_entries(A, work):
    """A8, A15: the checks bench_suite runs on madhouse only are catalogue
    entries tagged "madhouse", each a committed file in this tree with the
    suite's arguments, judge and L3_STIMULUS: the host bridge, host unit and
    host diff, check-port, regress, the madhouse mutants and every sim."""
    import bench_suite as BS
    by = {e["id"]: e for e in A.catalogue(REPO, git(REPO, "rev-parse", "HEAD"))}
    mad = {i: e for i, e in by.items() if "madhouse" in e["tags"]}
    for i in ("host-bridge", "host-unit", "host-diff", "check-port", "regress-all"):
        assert i in mad, i
    assert mad["host-bridge"]["judge"] == "host-bridge" and mad["host-bridge"]["shell"] == "git-bash"
    assert "TWAI_FAKE" not in mad["host-bridge"]["env"], mad["host-bridge"]
    # the same suite on the shared TWAI fake: the same file and judge, with
    # TWAI_FAKE=shared (its fake folder comes from tree_env)
    sf = mad["host-bridge-shared-fake"]
    assert sf["path"] == mad["host-bridge"]["path"] and sf["args"] == [] \
        and sf["judge"] == "host-bridge" and sf["shell"] == "git-bash", sf
    assert sf["env"].get("TWAI_FAKE") == "shared", sf
    assert mad["regress-all"]["args"] == ["--all"], mad["regress-all"]
    for cid, _argv in BS.MADHOUSE_MUTANTS:
        assert cid in mad, cid
    assert mad["monitor-6h-diff-corrupt-pin"]["judge"] == "corrupt-pin"
    for s in BS.SIM_SCENARIOS:
        e = mad["sim." + s]
        assert e["args"][:1] == [s] and "--time-scale" in e["args"] and e["judge"] == "scenario" \
            and e.get("cls") == "wallclock", e
    for e in mad.values():
        assert e["env"].get("L3_STIMULUS") == "{madhouse_artifacts}/l3_stimulus.txt", e
        assert git(REPO, "cat-file", "-t", "HEAD:" + e["path"]) == "blob", e["path"]
        assert e["judge"] in A.JUDGES, e


def test_tree_setup(A, work):
    """A15, the core's tree_setup (the charge-interposer reviewer,
    2026-11-02): every tree installs and then checks the pinned canre; a
    local tree also the pinned can-tools canbench (madhouse has no can-tools
    clone); each command a committed file here. tree_installed reads back
    the commits and folders as the pin tools' checks verified them."""
    local = A.tree_setup({"project_dir": "x", "shared_dirs": {}})
    remote = A.tree_setup({"project_dir": "/w/x", "shared_dirs": {}, "remote": True,
                           "host": "madhouse"})
    assert local == [["charge-interposer/canre_pin.py", "install"],
                     ["charge-interposer/canre_pin.py", "check"],
                     ["charge-interposer/can_tools_pin.py", "install"],
                     ["charge-interposer/can_tools_pin.py", "check"]], local
    assert remote == local[:2], remote
    for argv in local:
        assert git(REPO, "cat-file", "-t", "HEAD:" + argv[0]) == "blob", argv
    c1, c2 = "1" * 40, "2" * 40
    results = [
        {"argv": local[0], "rc": 0, "console": "canre %s installed and checked at /a\n" % c1},
        {"argv": local[1], "rc": 0, "console": "canre %s checked at /w/x/scratch/canre/%s\n"
                                               % (c1, c1)},
        {"argv": local[2], "rc": 0, "console": "can-tools %s canbench/ written and checked at /b\n"
                                               % c2},
        {"argv": local[3], "rc": 0, "console": "can-tools %s canbench/ checked at "
                                               "/w/x/scratch/can-tools/%s\n" % (c2, c2)}]
    got = A.tree_installed({}, results)
    assert got == {"canre": {"commit": c1, "folder": "/w/x/scratch/canre/" + c1},
                   "can-tools": {"commit": c2, "folder": "/w/x/scratch/can-tools/" + c2}}, got
    assert A.tree_installed({}, results[:2]) == {"canre": got["canre"]}
    # an install's own line is not a check: nothing is recorded as verified
    assert A.tree_installed({}, results[:1]) == {}, A.tree_installed({}, results[:1])


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
    # a --dev run is never evidence: no row (the charge-interposer reviewer,
    # 2026-11-02), and the README is left as it was
    with open(readme, encoding="utf-8") as f:
        before = f.read()
    assert A.artifacts_row(os.path.join(runs, "accept_s_1"), dict(summary, dev=True)) is None
    with open(readme, encoding="utf-8") as f:
        assert f.read() == before, "a dev run wrote a row"
    with open(readme, "w", encoding="utf-8", newline="\n") as f:
        f.write("# x\n\nno table\n")
    try:
        A.artifacts_row(os.path.join(runs, "accept_s_1"), summary)
    except A.AdapterError:
        return
    raise AssertionError("a README with no runs/ rows took the row")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def test_masked_log(A, work):
    """A10 (port step 0's comparison, committed): two check logs compare
    line by line with run ids, temp and Windows paths, clock times,
    durations and timestamps masked and the runner's echo lines dropped;
    a real change is one difference."""
    a = os.path.join(work, "a.log")
    b = os.path.join(work, "b.log")
    _write(a, "$ py x.py\n# cwd=C:/one throttled=False\nrun charge-interposer-emulated_20261020T075920_d5cd\n"
              "wrote /tmp/abc123/x.bin at 07:59:20 in 12.5 s, 2026-10-20T07:59:20+01:00\nok 4 cases\n")
    _write(b, "$ py y.py\n# cwd=C:/two throttled=True\nrun charge-interposer-emulated_20261101T120000_beef\n"
              "wrote /tmp/zz9/x.bin at 12:00:01 in 9.1 s, 2026-11-01T12:00:01+00:00\nok 4 cases\n")
    f = A.comparer("masked-log")
    assert f({"log": a}, {"log": b}, {}, A) == [], f({"log": a}, {"log": b}, {}, A)
    _write(b, open(b).read().replace("ok 4 cases", "ok 3 cases"))
    d = f({"log": a}, {"log": b}, {}, A)
    assert len(d) == 2 and all(x["kind"] == "text" for x in d), d       # -old, +new


def test_sim_judged(A, work):
    """A10: a sim compares its judged and state lines by shape: the same
    line with other numbers is a figure difference carrying both sides'
    numbers; a line on one side only is a text difference naming its side;
    other lines do not count."""
    a = os.path.join(work, "a.log")
    b = os.path.join(work, "b.log")
    _write(a, "noise 1\n[PASS] trip within 120 ms (limit 200)\n[STATE] HOLD a=1 b=2\n"
              "[PASS] evap released\n")
    _write(b, "noise 2\n[PASS] trip within 131 ms (limit 200)\n[STATE] HOLD a=1 b=2\n"
              "[FAIL] evap still held\n")
    d = A.comparer("sim-judged")({"log": a}, {"log": b}, {}, A)
    figs = [x for x in d if x["kind"] == "figure"]
    text = [x for x in d if x["kind"] == "text"]
    assert len(figs) == 1 and "120" in figs[0]["line"] and "131" in figs[0]["line"], d
    assert len(text) == 2 and any("a only" in x["line"] and "evap released" in x["line"]
                                  for x in text), d


def test_run_logs(A, work):
    """A10: two whole runs compare case by case: verdicts, cases in one run
    only, a sim (m-sims/) by its judged lines, any other case by its masked
    log; a result carried from an earlier run has its log read there."""
    runs = os.path.join(work, "runs")

    def run(name, results, logs):
        d = os.path.join(runs, name)
        for rel, text in logs.items():
            _write(os.path.join(d, rel), text)
        _write(os.path.join(d, "record.json"), json.dumps({"results": results}))
        return {"kind": "run", "dir": d, "record": {"results": results}}
    r = lambda i, v, **k: dict({"id": i, "check": i, "verdict": v}, **k)  # noqa: E731
    run("early", [], {"m-host/host-unit/check.log": "ok all\n"})
    a = run("a", [r("m-sims/sim.x", "PASS"), r("m-host/host-unit", "PASS"),
                  r("m-host/gone", "PASS")],
            {"m-sims/sim.x/check.log": "[PASS] trip in 120 ms\n",
             "m-host/host-unit/check.log": "ok all\n", "m-host/gone/check.log": "x\n"})
    b = run("b", [r("m-sims/sim.x", "PASS"), r("m-host/host-unit", "PASS", carried_from="early"),
                  r("m-host/new", "FAIL")],
            {"m-sims/sim.x/check.log": "[PASS] trip in 125 ms\n"})
    d = A.comparer("run-logs")(a, b, {}, A)
    kinds = sorted((x["kind"], x["line"].split(":")[0]) for x in d)
    assert ("missing", "MISSING in b") in kinds and ("new", "NEW in b") in kinds, kinds
    assert any(x["kind"] == "figure" and "m-sims/sim.x" in x["line"] for x in d), d
    assert not any("host-unit" in x["line"] for x in d), "the carried log was not read from its run"
    b2 = run("b2", [r("m-sims/sim.x", "FAIL")], {"m-sims/sim.x/check.log": "[FAIL] trip\n"})
    d2 = A.comparer("run-logs")(a, b2, {}, A)
    assert any(x["kind"] == "verdict" and "m-sims/sim.x" in x["line"] for x in d2), d2


TESTS = [test_identity, test_catalogue, test_list_loads, test_tree_names, test_shells,
         test_madhouse_entries, test_tree_setup, test_identifier_scan, test_artifacts_row,
         test_masked_log,
         test_sim_judged, test_run_logs]

MUTANTS = [
    ("suites from another file", '"charge-interposer/bench_suite.py:%s"', '"charge-interposer/suite.py:%s"'),
    ("product files not the suite's", "    return bench_suite.product_files(root)\n",
     "    return bench_suite.product_files(root)[1:]\n"),
    ("self-tests without their flag", '["--self-test"]', "[]"),
    ("a harness check dropped", "    for cid, argv in BS.HARNESS_LOCAL:\n",
     "    for cid, argv in BS.HARNESS_LOCAL[1:]:\n"),
    ("INTERPOSER_CODE not the tree's", 'env = {"INTERPOSER_CODE": _code(tree)}',
     'env = {"INTERPOSER_CODE": HERE}'),
    ("no TWAI_FAKE_DIR", '        env["TWAI_FAKE_DIR"] = fake\n', "        pass\n"),
    ("the shared fake's folder on a remote tree in local form",
     '        return posixpath.join(d, "shared", "twai_port", "test")',
     '        return os.path.join(d, "shared", "twai_port", "test")'),
    ("the shared-fake entry on our own fake", '    e["env"]["TWAI_FAKE"] = "shared"\n', ""),
    ("interposer_code not the tree's", 'return {"interposer_code": _code(tree)}',
     'return {"interposer_code": HERE}'),
    ("a bare bash", '        raise AdapterError("the settings name no bash',
     '        return ["bash"]\n        raise AdapterError("the settings name no bash'),
    ("the hit's text shown", "                        key = cat\n",
     "                        key = line[s:e]\n"),
    ("counts not kept", '"%s: %s x%d" % (rel, cat, k)', '"%s: %s" % (rel, cat)'),
    ("the row anywhere", '        raise AdapterError("%s has no runs/ rows',
     '        lines.append(""); first = len(lines)\n        if 0: raise AdapterError("%s has no runs/ rows'),
    ("run ids not masked", '    (re.compile(r"charge-interposer-(?:emulated|bench|harness)_',
     '    (re.compile(r"NO-charge-interposer-(?:emulated|bench|harness)_'),
    ("echo lines kept", '            if line.startswith(("$ ", "# cwd=")):\n                continue\n', ""),
    ("numbers not masked by shape", '    return [(NUM.sub("#", s), NUM.findall(s), raw)',
     '    return [(s, NUM.findall(s), raw)'),
    ("one-side lines dropped", '                    out.append({"kind": "text", "line": "%s%s only: %s"',
     '                    0 and out.append({"kind": "text", "line": "%s%s only: %s"'),
    ("carried logs from this run", '    src = r.get("carried_from") or os.path.basename(side["dir"])',
     '    src = os.path.basename(side["dir"])'),
    ("sims by whole log", '        if cid.startswith("m-sims/"):', '        if False:'),
    ("the local python on a host", 'list(settings["hosts"][host]["python"])',
     'list(settings["hosts"]["local"]["python"])'),
    ("a sim's judge dropped", '                        judge="scenario", cls="wallclock")',
     '                        cls="wallclock")'),
    ("no L3_STIMULUS on madhouse", '    env = {"L3_STIMULUS": "{madhouse_artifacts}/l3_stimulus.txt"}',
     '    env = {}'),
    ("a madhouse mutant dropped", "    for cid, argv in BS.MADHOUSE_MUTANTS:\n",
     "    for cid, argv in BS.MADHOUSE_MUTANTS[1:]:\n"),
    ("can-tools on madhouse too", "    if not tree.get(\"remote\"):\n        cmds +=",
     "    if True:\n        cmds +="),
    ("no check after the install", '["charge-interposer/canre_pin.py", "check"]',
     '["charge-interposer/canre_pin.py", "where"]'),
    ("a dev run gets a row", '    if summary.get("dev"):\n        return None\n', ""),
    ("installed read from the install's line", r'r"^canre ([0-9a-f]{40}) checked at (.+)$"',
     r'r"^canre ([0-9a-f]{40}) .*at (.+)$"'),
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
