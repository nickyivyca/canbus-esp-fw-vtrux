#!/usr/bin/env python3
"""Tests of fw_harness/refusal_pairs.py's judging, on synthetic builds: no
ESP-IDF, no build folders.

    py -3.14 -B charge-interposer/refusal_pairs_test.py [--can-fail]

Tester, 2026-11-03 (port step 2a). The builds are made of the outputs
shared/tools' collect() reads ({"image", "elf_symbols", "project_name",
"image_hash"}), from the project's real table (firmware/vtrux_checks.json):
each ON label's image carries what its switch puts there (the table's own
strings and symbols, the variant's project name), the OFF image none of it.
The rules are judged by the pinned shared/tools' own judge_rule, as on real
builds.

The control (every build right) must PASS, and each of these must FAIL on
its own check: a label left out; an unknown label; truck's image as
witness's ON; bridge's image as OFF; an ON build or the OFF build that
cannot be read; OFF failing the whole R20 check; trace's image under the
truck's name; a bridge release that came out truck; a new test-only switch
no label expects; a rule EXPECT names that the table does not have; a
test-only entry with no rules.

--can-fail runs the tests on mutants of refusal_pairs.py's text, each of
which must make a test fail, and on the unmutated text, which must pass.
"""

import copy
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "fw_harness", "refusal_pairs.py")


def load(text=None):
    if text is None:
        with open(SRC, encoding="utf-8") as f:
            text = f.read()
    m = types.ModuleType("refusal_pairs_under_test")
    m.__file__ = SRC
    exec(compile(text, SRC, "exec"), m.__dict__)
    return m


def table():
    with open(os.path.join(HERE, "firmware", "vtrux_checks.json"), encoding="utf-8") as f:
        return json.load(f)["build_switches"]


def _rule(t, sw, kind, value=None):
    for r in t[sw]["refusal"]:
        (k, v), = r.items()
        if k == kind and (value is None or v == value):
            return v
    raise KeyError((sw, kind, value))


def outputs(t, variant, serial=False, witness=False, trace=False):
    img = b"\0app\0"
    syms = {"app_main", "setup", "loop"}
    if serial:
        img += b"\0".join(s.encode() for s in _rule(t, "INTP_NO_SERIAL", "bin_strings_absent")) + b"\0"
    if witness:
        syms |= set(_rule(t, "INTP_ORDER_WITNESS", "elf_symbols_absent"))
    if trace:
        syms |= set(_rule(t, "INTP_TX_TRACE", "elf_symbols_absent"))
    return {"image": img, "elf_symbols": syms, "project_name": "interposer_" + variant,
            "image_hash": "0" * 64}


def builds(t):
    """(off, ons) of the right builds."""
    off = {"outputs": outputs(t, "truck"), "bad": []}
    ons = {
        "bridge": {"outputs": outputs(t, "bridge", serial=True)},
        "witness": {"outputs": outputs(t, "witness", serial=True, witness=True)},
        "selftest": {"outputs": outputs(t, "selftest")},
        "diag": {"outputs": outputs(t, "diag")},
        "trace": {"outputs": outputs(t, "bridge", serial=True, trace=True)},
        "bridge-release": {"outputs": outputs(t, "bridge", serial=True)},
    }
    return off, ons


def failed(res):
    return [n for n, ok, _d in res if not ok]


def fails_on(res, *parts):
    """some failing check's name holds every part"""
    return any(all(p in n for p in parts) for n in failed(res))


# ---- the tests --------------------------------------------------------------
def test_control(M, SW):
    t = table()
    off, ons = builds(t)
    res = M.checks(t, off, ons, SW.judge_rule)
    assert failed(res) == [], failed(res)
    names = [n for n, _ok, _d in res]
    # every test-only rule judged on OFF, and each expected ON failure judged
    for s, rs in M.test_only_rules(t).items():
        for rid, _r in rs:
            assert "OFF: %s rule %s passes" % (s, rid) in names, (s, rid)
    assert "ON bridge-release: INTP_NO_SERIAL rule project_name_token_absent bridge fails" in names
    assert "ON trace: INTP_TX_TRACE rule elf_symbols_absent fails" in names
    assert len(res) > 50, len(res)


def test_label_left_out(M, SW):
    t = table()
    for lb in sorted(M.EXPECT):
        off, ons = builds(t)
        del ons[lb]
        res = M.checks(t, off, ons, SW.judge_rule)
        assert fails_on(res, "ON build %s given" % lb), (lb, failed(res))


def test_unknown_label(M, SW):
    t = table()
    off, ons = builds(t)
    ons["truck"] = {"outputs": outputs(t, "truck")}
    assert fails_on(M.checks(t, off, ons, SW.judge_rule), "ON label truck is known")


def test_truck_as_witness(M, SW):
    t = table()
    off, ons = builds(t)
    ons["witness"] = {"outputs": outputs(t, "truck")}
    res = M.checks(t, off, ons, SW.judge_rule)
    for rid in ("INTP_ORDER_WITNESS rule elf_symbols_absent",
                "INTP_NO_SERIAL rule bin_strings_absent",
                "INTP_NO_SERIAL rule project_name_token_absent witness"):
        assert fails_on(res, "ON witness: " + rid + " fails"), (rid, failed(res))
    assert fails_on(res, "ON witness is interposer_witness")


def test_bridge_as_off(M, SW):
    t = table()
    off, ons = builds(t)
    off = {"outputs": outputs(t, "bridge", serial=True), "bad": []}
    res = M.checks(t, off, ons, SW.judge_rule)
    assert fails_on(res, "OFF is interposer_truck"), failed(res)
    assert fails_on(res, "OFF: INTP_NO_SERIAL rule bin_strings_absent passes"), failed(res)
    assert fails_on(res, "OFF: INTP_NO_SERIAL rule project_name_token_absent bridge passes")


def test_witness_symbols_in_off(M, SW):
    """the truck's name and no strings, but the witness's symbols"""
    t = table()
    off, ons = builds(t)
    off["outputs"] = outputs(t, "truck", witness=True)
    assert fails_on(M.checks(t, off, ons, SW.judge_rule),
                    "OFF: INTP_ORDER_WITNESS rule elf_symbols_absent passes")


def test_unreadable(M, SW):
    t = table()
    off, ons = builds(t)
    ons["trace"] = {"error": "no ELF"}
    assert fails_on(M.checks(t, off, ons, SW.judge_rule), "ON trace read")
    off, ons = builds(t)
    assert fails_on(M.checks(t, {"error": "no ELF"}, ons, SW.judge_rule), "OFF build read")


def test_off_whole_check(M, SW):
    t = table()
    off, ons = builds(t)
    off["bad"] = [("the project", "INTP_NEW", "unlisted", "main/main.cpp")]
    assert fails_on(M.checks(t, off, ons, SW.judge_rule), "OFF passes the whole R20 check")
    off, ons = builds(t)
    del off["bad"]
    assert fails_on(M.checks(t, off, ons, SW.judge_rule), "OFF passes the whole R20 check")


def test_trace_named_truck(M, SW):
    t = table()
    off, ons = builds(t)
    ons["trace"] = {"outputs": outputs(t, "truck", serial=True, trace=True)}
    assert fails_on(M.checks(t, off, ons, SW.judge_rule), "ON trace is interposer_bridge")


def test_release_came_out_truck(M, SW):
    """the guard (release route (B)): a bridge-configured release that came
    out interposer_truck, serial strings and all"""
    t = table()
    off, ons = builds(t)
    ons["bridge-release"] = {"outputs": outputs(t, "truck", serial=True)}
    res = M.checks(t, off, ons, SW.judge_rule)
    assert fails_on(res, "ON bridge-release: INTP_NO_SERIAL rule project_name_token_absent bridge fails")
    assert fails_on(res, "ON bridge-release is interposer_bridge")


def test_table_vs_expect(M, SW):
    t = table()
    off, ons = builds(t)
    t2 = copy.deepcopy(t)
    t2["INTP_NEW_PROBE"] = {"class": "test-only", "reason": "x",
                            "refusal": [{"elf_symbols_absent": ["probe_"]}]}
    assert fails_on(M.checks(t2, off, ons, SW.judge_rule),
                    "INTP_NEW_PROBE rule elf_symbols_absent is expected to fail")
    t3 = copy.deepcopy(t)
    t3["INTP_NO_SERIAL"]["refusal"] = [r for r in t3["INTP_NO_SERIAL"]["refusal"]
                                       if r != {"project_name_token_absent": "diag"}]
    assert fails_on(M.checks(t3, off, ons, SW.judge_rule),
                    "diag: INTP_NO_SERIAL rule project_name_token_absent diag is in the table")
    t4 = copy.deepcopy(t)
    t4["INTP_TX_TRACE"]["refusal"] = []
    assert fails_on(M.checks(t4, off, ons, SW.judge_rule), "INTP_TX_TRACE has refusal rules")


TESTS = [test_control, test_label_left_out, test_unknown_label, test_truck_as_witness,
         test_bridge_as_off, test_witness_symbols_in_off, test_unreadable,
         test_off_whole_check, test_trace_named_truck, test_release_came_out_truck,
         test_table_vs_expect]


# ---- the runner's fixture (refusal_pairs_runs.py) --------------------------
RUNS = os.path.join(HERE, "fw_harness", "refusal_pairs_runs.py")
CMAKE_REL = "charge-interposer/firmware/CMakeLists.txt"


def load_runs(text=None):
    if text is None:
        with open(RUNS, encoding="utf-8") as f:
            text = f.read()
    m = types.ModuleType("refusal_pairs_runs_under_test")
    m.__file__ = RUNS
    exec(compile(text, RUNS, "exec"), m.__dict__)
    return m


def git_diff(minus, plus, path=CMAKE_REL, extra=""):
    """`git diff --unified=0`'s shape for one hunk of one file"""
    return ("diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n--- a/{p}\n+++ b/{p}\n"
            "@@ -22 +22 @@ set(INTP_VARIANTS bridge witness truck selftest diag)\n"
            "{m}{pl}{x}").format(p=path, m="".join("-%s\n" % ln for ln in minus),
                                 pl="".join("+%s\n" % ln for ln in plus), x=extra)


def test_fixture_text(R):
    """the fixture changes the default line alone, and only if it is there
    once: on this tree's CMakeLists.txt, and on texts without it or with
    it twice"""
    with open(os.path.join(HERE, "firmware", "CMakeLists.txt"), encoding="utf-8", newline="") as f:
        text = f.read()
    new = R.fixture_text(text)
    a, b = text.splitlines(), new.splitlines()
    assert len(a) == len(b)
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    assert changed == [('set(INTP_VARIANT "truck" CACHE STRING', 'set(INTP_VARIANT "bridge" CACHE STRING')], changed
    for bad in (text.replace(R.FIXTURE_OLD, 'set(INTP_VARIANT "bridge" CACHE STRING'),
                text + "\n" + R.FIXTURE_OLD + "\n"):
        try:
            R.fixture_text(bad)
        except ValueError:
            continue
        raise AssertionError("not refused")


def test_fixture_diff(R):
    """the one-line diff passes; anything more or else is a problem"""
    old, new = 'set(INTP_VARIANT "truck" CACHE STRING', 'set(INTP_VARIANT "bridge" CACHE STRING'
    assert R.fixture_diff_problems(git_diff([old], [new])) == []
    two = git_diff([old], [new]) + git_diff(["x"], ["y"], path="charge-interposer/firmware/main/main.cpp")
    for why, d in (
            ("another value", git_diff([old], ['set(INTP_VARIANT "witness" CACHE STRING'])),
            ("a second line", git_diff([old, "a"], [new, "b"])),
            ("a line added", git_diff([old], [new, "set(X 1)"])),
            ("another file too", two),
            ("another file only", git_diff([old], [new], path="charge-interposer/firmware/main/CMakeLists.txt")),
            ("nothing changed", ""),
            ("a mode change", git_diff([old], [new]).replace("index 1111111..2222222 100644",
                                                            "old mode 100644\nnew mode 100755\nindex 1111111..2222222"))):
        assert R.fixture_diff_problems(d), why


def test_worktree_layout(R):
    """the worktrees go beside this worktree, or in --scratch-from's folder;
    every build folder is checked against the 200-character limit, the
    three releases' and every dev and trace build's; one shared-<tag>
    checkout per distinct tag of the pins. The stand-in paths start at this
    system's own root (C:\\ on Windows, / on Linux), so they are absolute
    there and the length checks do not depend on the folder the test runs
    in (the reviewer's B2, 2026-11-12: with C:\\ on Linux they were
    relative, joined to the acceptance runner's deep tree by abspath, and
    record accept_3_20261111T234853_563d refused them as too deep)"""
    root = os.path.abspath(os.sep)
    top = os.path.join(root, "w", "esp", "interposer-tester")
    beside = R.worktree_paths(top)
    assert beside["standin"] == os.path.join(root, "w", "esp", "interposer-tester-standin"), beside
    work = os.path.join(root, "w", "esp", "interposer-tester", "scratch", "rp")
    inw = R.worktree_paths(top, work)
    assert inw == {k: os.path.join(work, k) for k in R.WORKTREES}, inw
    # absolute as given: abspath (which too_deep measures) leaves them alone
    assert all(os.path.abspath(p) == p for p in [top, work] + list(inw.values())), inw
    dirs = R.build_dirs(inw)
    assert len(dirs) == 3 + len(R.DEV) + 1, dirs
    want = [os.path.join(inw[k], "charge-interposer", "firmware", "build")
            for k in ("standin", "r19", "relbridge")] + \
        [os.path.join(inw["pairsdev"], "charge-interposer", "firmware", "build", v)
         for v in R.DEV + ("trace",)]
    assert dirs == want, dirs
    assert R.too_deep(inw) == [], R.too_deep(inw)
    # a base that puts the longest build folder at exactly the limit passes,
    # one character more is refused
    longest = max(len(os.path.abspath(p)) for p in dirs)
    edge = work + "x" * (R.MAX_BUILD_DIR - longest)
    assert R.too_deep(R.worktree_paths(top, edge)) == []
    deep = R.too_deep(R.worktree_paths(top, edge + "y"))
    assert len(deep) == 1 and deep[0].endswith("selftest"), deep
    pins = {"shared/tools": {"tag": "2026.11.03"}, "shared/bench": {"tag": "2026.11.04.4"},
            "shared/twai_port": {"tag": "2026.11.03"}}
    assert R.shared_tags(json.dumps(pins)) == ["2026.11.03", "2026.11.04.4"]


def test_scratch_from_wiring(R):
    """main() with --scratch-from: the worktrees, and a shared-<tag> checkout
    of each tag the product commit pins, are added in the scratch folder of
    DIR's worktree, and removed with it at the end; a DIR too deep is
    refused before any worktree is added. git, vtrux_tools and scratch_dir
    are fakes: nothing is built."""
    sys.path.insert(0, HERE)
    import scratch_dir as real_scratch               # the test's own folder, in scratch/
    calls, made, removed = [], [], []
    pins = json.dumps({"shared/tools": {"tag": "2026.11.03"},
                       "shared/bench": {"tag": "2026.11.04.4"}})

    def fake_git(*args, cwd=None):
        calls.append(args)
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return 0, "TOP"
        if args[:1] == ("show",):
            return 0, pins
        if args[:1] == ("status",):
            return 0, ""
        return 0, "c" * 40
    fake_tools = types.ModuleType("vtrux_tools")
    fake_tools.check_idf_env = lambda: None
    fake_scratch = types.ModuleType("scratch_dir")
    fake_scratch.wallclock_clear = lambda: (True, [])

    def make(tool, start):
        p = os.path.join(start, "scratch", tool)
        made.append(p)
        return p
    fake_scratch.make = make
    fake_scratch.remove = removed.append
    saved = {k: sys.modules.get(k) for k in ("vtrux_tools", "scratch_dir")}
    R.git = fake_git
    with real_scratch.run("refusal-pairs-test") as tmp:
        sys.modules.update(vtrux_tools=fake_tools, scratch_dir=fake_scratch)
        try:
            # a short DIR (the fake make() creates nothing there), the --out real
            dir_ = os.path.abspath(os.sep + "c")
            try:
                rc = R.main(["--commit", "abc", "--out", os.path.join(tmp, "out"),
                             "--scratch-from", dir_])
            except SystemExit as e:
                raise AssertionError("refused: %s" % e)
            assert rc == 1, rc                      # stopped at the fixture: no CMakeLists
            work = os.path.join(dir_, "scratch", "refusal-pairs")
            adds = [c for c in calls if c[:2] == ("worktree", "add")]
            assert [(os.path.dirname(c[3]), os.path.basename(c[3]), c[4]) for c in adds] == \
                [(work, "shared-2026.11.03", "2026.11.03"),
                 (work, "shared-2026.11.04.4", "2026.11.04.4")] + \
                [(work, k, "c" * 40) for k in R.WORKTREES], adds
            assert removed == [work], removed
            calls.clear()
            removed.clear()
            deep = os.path.join(tmp, "d" * 150)
            try:
                R.main(["--commit", "abc", "--out", os.path.join(tmp, "out2"),
                        "--scratch-from", deep])
                raise AssertionError("a folder too deep was not refused")
            except SystemExit as e:
                assert "over 200" in str(e), e
            assert not [c for c in calls if c[:2] == ("worktree", "add")], calls
            assert removed == [os.path.join(deep, "scratch", "refusal-pairs")], removed
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


RUNS_TESTS = [test_fixture_text, test_fixture_diff, test_worktree_layout,
              test_scratch_from_wiring]


def run_all(SW, text=None, quiet=False, runs_text=None):
    failed_tests = []
    for t in RUNS_TESTS:
        try:
            t(load_runs(runs_text))
            if not quiet:
                print("ok    " + t.__name__)
        except Exception as e:                       # noqa: BLE001
            failed_tests.append(t.__name__)
            if not quiet:
                print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:300]))
    for t in TESTS:
        M = load(text)
        try:
            t(M, SW)
            if not quiet:
                print("ok    " + t.__name__)
        except Exception as e:                       # noqa: BLE001
            failed_tests.append(t.__name__)
            if not quiet:
                print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:300]))
    return failed_tests


MUTANTS = [
    ("an ON rule that passes accepted", "                add(\"ON %s: %s rule %s fails\" % (lb, s, rid), ok is False,",
     "                add(\"ON %s: %s rule %s fails\" % (lb, s, rid), True,"),
    ("an OFF rule that fails accepted", "                add(\"OFF: %s rule %s passes\" % (s, rid), ok is True, detail)",
     "                add(\"OFF: %s rule %s passes\" % (s, rid), True, detail)"),
    ("OFF's whole R20 result not read", "        add(\"OFF passes the whole R20 check\", bad == [],",
     "        add(\"OFF passes the whole R20 check\", True,"),
    ("a missing label not reported", "        add(\"ON build %s given\" % lb, lb in ons, \"no --on %s=...\" % lb)",
     "        pass"),
    ("an unreadable ON build skipped", "            add(\"ON %s read\" % lb, False, b.get(\"error\", \"no outputs\"))\n",
     ""),
    ("an unreadable OFF build passes", "        add(\"OFF build read\", False, off.get(\"error\", \"no outputs\"))",
     "        add(\"OFF build read\", True, off.get(\"error\", \"no outputs\"))"),
    ("ON project names not checked", "        add(\"ON %s is %s\" % (lb, name), o.get(\"project_name\") == name,",
     "        add(\"ON %s is %s\" % (lb, name), True,"),
    ("OFF's project name not checked", "        add(\"OFF is %s\" % name, o.get(\"project_name\") == name,",
     "        add(\"OFF is %s\" % name, True,"),
    ("a rule no label expects passes", "            add(\"%s rule %s is expected to fail on an ON build\" % (s, rid),\n                where,",
     "            add(\"%s rule %s is expected to fail on an ON build\" % (s, rid),\n                True,"),
    ("an EXPECT rule not in the table passes", "                    rid in known, \"not a test-only rule of %s\" % s)",
     "                    True, \"not a test-only rule of %s\" % s)"),
    ("the bridge release's name-token rule not expected",
     '    "bridge-release": ("bridge", {"INTP_NO_SERIAL": [TOKEN + " bridge",\n                                                     STRINGS]}),',
     '    "bridge-release": ("bridge", {"INTP_NO_SERIAL": [STRINGS]}),'),
    ("the witness's symbols not expected on witness",
     '                            "INTP_ORDER_WITNESS": [SYMBOLS]}),',
     '                            }),'),
    ("tokens compared with their underscores", '    return "%s %s" % (k, v.strip("_")) if k == TOKEN else k',
     '    return "%s %s" % (k, "_" + v) if k == TOKEN else k'),
]


RUNS_MUTANTS = [
    ("the fixture's changed lines not checked",
     '    if body != ["-" + FIXTURE_OLD, "+" + FIXTURE_NEW]:\n', "    if False:\n"),
    ("the fixture's files not checked",
     "    if files != [(CMAKE_REL, CMAKE_REL)]:\n", "    if False:\n"),
    ("a mode change in the fixture passes",
     '    if "new file mode" in diff or "deleted file mode" in diff or \\\n',
     '    if False and "new file mode" in diff or "deleted file mode" in diff and \\\n'),
    ("the default line replaced however often it occurs", "    if n != 1:\n", "    if n < 1:\n"),
    ("the worktrees beside this one whatever the base", "    if base:\n        return {k: os.path.join(base, k)",
     "    if False:\n        return {k: os.path.join(base, k)"),
    ("the releases' build folders not checked",
     '           for k in ("standin", "r19", "relbridge")]\n', "           for k in ()]\n"),
    ("the trace build not checked", '            for v in DEV + ("trace",)]\n',
     "            for v in DEV]\n"),
    ("the limit loosened", "len(os.path.abspath(p)) > limit]", "len(os.path.abspath(p)) > limit + 1]"),
    ("one checkout per component, not per tag",
     '    return sorted({v["tag"] for v in json.loads(pins_text).values()})',
     '    return sorted([v["tag"] for v in json.loads(pins_text).values()])'),
    ("--scratch-from not used for the worktrees", "        paths = worktree_paths(top, work)\n",
     "        paths = worktree_paths(top)\n"),
    ("no shared checkouts made", "    for name, p in sorted(shared.items()):\n            tag",
     "    for name, p in sorted({}.items()):\n            tag"),
    ("a folder too deep not refused", "    if deep:\n        if work:", "    if False:\n        if work:"),
    ("the scratch folder not removed", "        if work:\n            try:\n                scratch_dir.remove(work)",
     "        if False:\n            try:\n                scratch_dir.remove(work)"),
]


def can_fail(SW):
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    with open(RUNS, encoding="utf-8") as f:
        runs_text = f.read()
    bad = 0
    f0 = run_all(SW, text, quiet=True)
    print(("ok    " if not f0 else "FAIL  ") + "control: the unmutated text passes" + ("" if not f0 else " (%s)" % f0))
    bad += bool(f0)
    caught = 0
    every = [(n, o, nw, "refusal_pairs.py") for n, o, nw in MUTANTS] + \
            [(n, o, nw, "refusal_pairs_runs.py") for n, o, nw in RUNS_MUTANTS]
    for name, old, new, where in every:
        src = text if where == "refusal_pairs.py" else runs_text
        if src.count(old) != 1:
            print("FAIL  %s: its text occurs %d times in %s, not once" % (name, src.count(old), where))
            bad += 1
            continue
        try:
            if where == "refusal_pairs.py":
                fl = run_all(SW, text.replace(old, new), quiet=True)
            else:
                fl = run_all(SW, text, quiet=True, runs_text=runs_text.replace(old, new))
        except SyntaxError as e:
            print("FAIL  %s: the mutant does not compile (%s)" % (name, e))
            bad += 1
            continue
        if fl:
            caught += 1
            print("ok    %s: fails %s" % (name, ", ".join(fl)))
        else:
            bad += 1
            print("FAIL  %s: no test failed" % name)
    print("\n%d of %d mutants caught" % (caught, len(every)))
    return 1 if bad else 0


def main():
    SW = load().load_switches()
    if "--can-fail" in sys.argv[1:]:
        return can_fail(SW)
    fl = run_all(SW)
    n = len(TESTS) + len(RUNS_TESTS)
    print("\n%d of %d passed" % (n - len(fl), n))
    return 1 if fl else 0


if __name__ == "__main__":
    sys.exit(main())
