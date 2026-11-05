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
    for t in BS.HARNESS_PY_CANFAILS:
        e = by[t[:-3].replace("_", "-") + "-canfail"]
        assert e["path"] == "charge-interposer/" + t and e["args"] == ["--can-fail"], e
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
                'run = ["charge-interposer/paths_test.py"]\n\n[[build]]\nid = "bridge"\n'
                'tree = "head"\ntarget = "idf-build"\noptions = { variant = "bridge" }\n' % head)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "list")
    got = L.load(clone, "%s:charge-interposer/acceptance/t.toml"
                 % git(clone, "rev-parse", "HEAD"), A)
    st = [c for c in got.checks if c.get("from_set") == "st"]
    assert len(st) == len(BS.SELF_TESTS), (len(st), len(BS.SELF_TESTS))
    assert any(c["id"] == "paths" for c in got.checks)
    # the build target and its option are the adapter's; the tree gets the
    # harness's shared pin from the pin file at its commit
    assert [b["id"] for b in got.builds] == ["bridge"], got.builds
    pins = json.loads(git(REPO, "show", "%s:charge-interposer/firmware/shared_pins.json" % head))
    assert got.trees["head"]["shared"] == {c: pins["shared/" + c]["commit"]
                                           for c in ("bench", "tools", "twai_port")}, \
        got.trees["head"]


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


def test_idf_shell(A, work):
    """A5, A15: the "idf" shell is the host's python running
    fw_harness/idf_build_shell.py with the machine settings' ESP-IDF
    export (idf_path, idf_tools_path, python_dir), local only; a setting
    missing is a refusal."""
    idf = {"idf_path": r"C:\idf", "idf_tools_path": r"C:\tools", "python_dir": r"C:\py311"}
    s = {"hosts": {"local": {"python": ["py", "-3.14"]},
                   "madhouse": {"python": ["python3"], "ssh": "m", "workroot": "/w"}},
         "idf": dict(idf)}
    want = ["py", "-3.14", "-B", A.IDF_SHELL, "--idf-path", idf["idf_path"],
            "--idf-tools-path", idf["idf_tools_path"], "--python-dir", idf["python_dir"], "--"]
    assert A.shell_argv("idf", s) == want, A.shell_argv("idf", s)
    assert "idf" in A.SHELLS
    assert os.path.normcase(A.IDF_SHELL).endswith(os.path.normcase(
        os.path.join("fw_harness", "idf_build_shell.py"))), A.IDF_SHELL
    assert os.path.isfile(os.path.join(HERE, "fw_harness", "idf_build_shell.py"))
    for k in idf:
        t = dict(s, idf={x: v for x, v in idf.items() if x != k})
        try:
            A.shell_argv("idf", t)
        except A.AdapterError:
            continue
        raise AssertionError("idf with no %s was taken" % k)
    try:
        A.shell_argv("idf", s, host="madhouse")
    except A.AdapterError:
        pass
    else:
        raise AssertionError("idf on madhouse was taken")


def _fake_build(work, variant, project=None, drop=(), idf="v5.4.2"):
    """A tree as the runner makes it for a build, with what an IDF build of
    variant leaves in firmware/build/<variant>/; its out folder."""
    t = os.path.join(work, "build-%s" % variant)
    bd = os.path.join(t, "repo", "charge-interposer", "firmware", "build", variant)
    for rel in ("interposer_%s.bin" % variant, "interposer_%s.elf" % variant,
                os.path.join("bootloader", "bootloader.bin"),
                os.path.join("partition_table", "partition-table.bin")):
        if rel in drop:
            continue
        os.makedirs(os.path.dirname(os.path.join(bd, rel)), exist_ok=True)
        with open(os.path.join(bd, rel), "wb") as f:
            f.write(rel.encode())
    desc = {"project_name": project or "interposer_%s" % variant,
            "app_bin": "interposer_%s.bin" % variant, "app_elf": "interposer_%s.elf" % variant,
            "git_revision": idf, "target": "esp32s3",
            "c_compiler": "C:/x/.espressif-5.4.2/tools/xtensa-esp-elf/esp-14.2.0_20241119/"
                          "xtensa-esp-elf/bin/xtensa-esp32s3-elf-gcc.exe"}
    with open(os.path.join(bd, "project_description.json"), "w", encoding="utf-8") as f:
        json.dump(desc, f)
    return os.path.join(t, "out"), bd


def test_targets(A, work):
    """A5, A15: the build target "idf-build" (option variant) runs the
    product's own charge-interposer/vtrux_tools.py build <variant> through
    the idf shell; what it made is read in the build's tree, from
    firmware/build/<variant>/ (reviewer, 2026-11-04): the app, bootloader
    and partition-table images, the ELF, the variant and the toolchain; a
    description of another variant, or a file missing, is refused."""
    t = A.TARGETS["idf-build"]
    assert t["options"] == ["variant"] and t["shell"] == "idf", t
    assert t["run"] == "charge-interposer/vtrux_tools.py" and git(
        REPO, "cat-file", "-t", "HEAD:" + t["run"]) == "blob", t["run"]
    assert t["args"]({"variant": "bridge"}, "x") == ["build", "bridge"]
    out, bd = _fake_build(work, "bridge")
    got = A.TARGETS["idf-build"]["outputs"](out, {"variant": "bridge"})
    assert got["images"] == {
        "app": os.path.join(bd, "interposer_bridge.bin"),
        "bootloader": os.path.join(bd, "bootloader", "bootloader.bin"),
        "partition-table": os.path.join(bd, "partition_table", "partition-table.bin")}, got
    assert got["elf"] == os.path.join(bd, "interposer_bridge.elf") and got["variant"] == "bridge"
    assert got["toolchain"] == {"esp_idf": "v5.4.2", "target": "esp32s3",
                                "compiler": "esp-14.2.0_20241119"}, got["toolchain"]
    for name, kw in (("another variant's description", {"project": "interposer_witness"}),
                     ("no bootloader", {"drop": (os.path.join("bootloader", "bootloader.bin"),)}),
                     ("no ELF", {"drop": ("interposer_diag.elf",)}),
                     ("another ESP-IDF", {"idf": "v5.4.1"})):
        v = "diag" if "ELF" in name else "w%d" % len(name)
        o, _ = _fake_build(os.path.join(work, name.replace(" ", "-").replace("'", "")), v, **kw)
        try:
            A.TARGETS["idf-build"]["outputs"](o, {"variant": v})
        except A.AdapterError:
            continue
        raise AssertionError("outputs took a build with %s" % name)


def test_settings_keys(A, work):
    """A6: the settings a list's checks name come from the build records:
    for each build that made its images, build_<id> (its build folder),
    image_<id> and elf_<id>; none for a failed build."""
    bd = os.path.join(work, "b", "build", "bridge")
    builds = {"bridge": {"images": {"app": {"path": os.path.join(bd, "interposer_bridge.bin")},
                                    "bootloader": {"path": os.path.join(bd, "bl.bin")}},
                         "elf": {"path": os.path.join(bd, "interposer_bridge.elf")}},
              "witness": {"verdict": "FAIL"}}
    assert A.settings_keys(builds, {}) == {
        "build_bridge": bd, "image_bridge": os.path.join(bd, "interposer_bridge.bin"),
        "elf_bridge": os.path.join(bd, "interposer_bridge.elf")}, A.settings_keys(builds, {})


def test_suite_settings(A, work):
    """A9 (the charge-interposer reviewer, 2026-11-04): a suite run's
    images come from its images_from builds' records, as the core wrote
    them: each variant's build folder, its id and the recorded ELF and app
    sha256. A bench suite run missing a variant the bench suite flashes,
    or naming a build that is not an idf-build, is refused."""
    def rec(v, elf="e", app="a", target="idf-build"):
        bd = os.path.join(work, "s", "build", v)
        return {"id": "b-" + v, "target": target, "variant": v,
                "images": {"app": {"path": os.path.join(bd, "interposer_%s.bin" % v),
                                   "sha256": app + v},
                           "bootloader": {"path": os.path.join(bd, "bl.bin"), "sha256": "x"}},
                "elf": {"path": os.path.join(bd, "interposer_%s.elf" % v), "sha256": elf + v}}
    recs = [rec(v) for v in A.BENCH_VARIANTS]
    got = A.suite_settings({"id": "b", "suite": "bench"}, recs)
    assert got == {"interposer_builds": {v: {
        "dir": os.path.join(work, "s", "build", v), "build": "b-" + v,
        "elf_sha256": "e" + v, "app_sha256": "a" + v} for v in A.BENCH_VARIANTS}}, got
    # another suite may take what it names
    assert set(A.suite_settings({"id": "e", "suite": "emulated"}, recs[:1])
               ["interposer_builds"]) == {"bridge"}
    for why, run, rs in (("a bench run without the truck", {"id": "b", "suite": "bench"},
                          recs[:2]),
                         ("a build that is not idf-build", {"id": "e", "suite": "emulated"},
                          [rec("bridge", target="other")])):
        try:
            A.suite_settings(run, rs)
        except A.AdapterError:
            continue
        raise AssertionError("not refused: %s" % why)


def test_shared_pins(A, work):
    """A4, A15: a tree's shared component is the harness's pin,
    shared/twai_port, from charge-interposer/firmware/shared_pins.json at
    its commit; its checkout is shared-<tag>/ beside repo/ (where
    shared_pin.py and vtrux_tools.py look), and one at another commit is
    named for its component."""
    head = git(REPO, "rev-parse", "HEAD")
    comps = ("bench", "tools", "twai_port")

    def pins_at(c):
        return json.loads(git(REPO, "show", "%s:charge-interposer/firmware/shared_pins.json" % c))

    def want_paths(pins):
        """each pinned tag's first component (by name) in shared-<tag>, a
        second at a taken tag in shared-<component>"""
        out, taken = {"project": "repo"}, set()
        for c in comps:
            d = "shared-%s" % pins["shared/" + c]["tag"]
            out[c] = d if d not in taken else "shared-%s" % c
            taken.add(out[c])
        return out

    pins = pins_at(head)
    tp = pins["shared/twai_port"]
    got = A.shared_pins(REPO, head)
    assert got == {c: pins["shared/" + c]["commit"] for c in comps}, got
    assert A.tree_paths("x", {"project": head, "shared": got}) == want_paths(pins), \
        A.tree_paths("x", {"project": head, "shared": got})
    assert A.tree_paths("x", {"project": head, "shared": {"twai_port": head}}) == {
        "project": "repo", "twai_port": "shared-twai_port"}
    # the 2a product commit: every pin at one tag, so one shared-<tag> and
    # the other two named for themselves; at HEAD shared/bench is at another
    # tag (2026.11.04.2) than the rest
    prod = "cf1b911326d62fc7a7a743be2050ff51bf37f0d6"
    pp = pins_at(prod)
    assert len({pp["shared/" + c]["tag"] for c in comps}) == 1, pp
    assert A.tree_paths("x", {"project": prod, "shared": A.shared_pins(REPO, prod)}) == {
        "project": "repo", "bench": "shared-%s" % pp["shared/bench"]["tag"],
        "tools": "shared-tools", "twai_port": "shared-twai_port"}
    assert A.tree_paths("x", {"project": head, "shared": {}}) == {"project": "repo"}
    # a commit from before the pin file (port step 2a) carries its own
    # shared/: no pins, and a component the list gives is named for itself
    old = git(REPO, "log", "-1", "--format=%H", "--diff-filter=A", "--",
              "charge-interposer/firmware/shared_pins.json") + "^"
    old = git(REPO, "rev-parse", old)
    assert A.shared_pins(REPO, old) == {}, A.shared_pins(REPO, old)
    assert A.tree_paths("x", {"project": old, "shared": {"twai_port": tp["commit"]}}) == {
        "project": "repo", "twai_port": "shared-twai_port"}


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


VP_IDLE = ("  250k, idle: 352088 error frame(s) in 69 burst(s) over 60 s: per burst min 1 / "
           "p50 2 / max 351940, 69.0 bursts per minute; node frames that got through: 1521 "
           "of the 6021 it tried to send in the window (25.3 %; 1521 queued, 4500 refused "
           "for a full queue); 0 frame(s) from earlier windows' backlog also seen, not counted")
VP_FLOOD = ("  1M, flooding 7F4: 575234 error frame(s) in 8 burst(s) over 60 s: per burst min "
            "690 / p50 22670 / max 356454, 8.0 bursts per minute; node frames that got "
            "through: 1556 of the 6028 it tried to send in the window (25.8 %; 1556 queued, "
            "4472 refused for a full queue); 538 frame(s) from earlier windows' backlog also "
            "seen, not counted")


def _vp_report(windows=(VP_IDLE, VP_FLOOD), arms=(("dominant/witness", "PASS"),),
               flag="    FLAG: 4 backward stamp step(s) -- the burst count may be off by up to 4"):
    lines = ["run at 2026-11-04T00:00:00; vehicle port only", "",
             "== 2 wrong bit rate (normal mode, AUTORECOVER on)",
             "    board> @STATUS installed=1 state=running tec=0 rec=128"]
    for w in windows:
        lines += [w, "    error-frame gaps within 2 ms of the 10 ms burst threshold: 142"]
    if flag:
        lines.append(flag)
    lines += ["", "== verdicts"] + ["  %-28s %s" % a for a in arms]
    return "\n".join(lines) + "\n"


def test_vport_windows(A, work):
    """A10 (the charge-interposer reviewer, 2026-11-04: port step 2a (f),
    the vport x3 runs, each against vp1 and against 6019): vport-windows
    counts the check's verdict, each arm's verdict, a window on one side
    only and every TEST FAULT on either side; the windows' figures and the
    FLAG lines are shown with both sides' values, never counted; a run
    with no report is refused."""
    runs = os.path.join(work, "vp")
    n = [0]

    def run(report, verdict="PASS"):
        n[0] += 1
        d = os.path.join(runs, "r%d" % n[0])
        res = [{"id": A.VPORT_CHECK, "check": A.VPORT_CHECK, "verdict": verdict}]
        if report is not None:
            _write(os.path.join(d, "b-vport", "vport-bitrate", "vport-bitrate.txt"), report)
        _write(os.path.join(d, "b-vport", "vport-bitrate", "check.log"), "log\n")
        _write(os.path.join(d, "record.json"), json.dumps({"results": res}))
        return {"kind": "run", "dir": d, "record": {"results": res}}

    def counted(a, b):
        d = A.comparer("vport-windows")(a, b, {}, A)
        return [x["line"] for x in d if x["kind"] in ("verdict", "missing", "new")], d
    base = run(_vp_report())
    c, d = counted(base, run(_vp_report()))
    assert c == [], c
    assert [x["kind"] for x in d] == ["flag", "flag"] and "burst count" in d[0]["line"], d
    # figures only: shown side by side, nothing counted
    c, d = counted(base, run(_vp_report(windows=(VP_IDLE.replace("352088", "494441"),
                                                 VP_FLOOD))))
    assert c == [], c
    figs = [x for x in d if x["kind"] == "figure"]
    assert [x["line"] for x in figs] == [
        "figure 250k/idle error frames, bursts, seconds: a 352088 69 60 | b 494441 69 60"], figs
    for why, b, word in (
            ("an arm's verdict changed", run(_vp_report(arms=(("dominant/witness", "FAIL"),))),
             "VERDICT arm dominant/witness: PASS -> FAIL"),
            ("the check's verdict changed", run(_vp_report(), verdict="VOID"),
             "VERDICT %s: PASS -> VOID" % A.VPORT_CHECK),
            ("a TEST FAULT added", run(_vp_report(windows=(
                VP_IDLE, "  1M, flooding 7F4: TEST FAULT: 1M flooding 7F4: the witness's "
                         "receive failed 12 time(s) in the window"))),
             "TEST FAULT in b: 1M, flooding 7F4: TEST FAULT"),
            ("a window gone", run(_vp_report(windows=(VP_IDLE,))),
             "MISSING in b: window 1M/flooding 7F4")):
        c, _d = counted(base, b)
        assert any(word in x for x in c), (why, c)
    # the same TEST FAULT on both sides still counts
    faulty = _vp_report(windows=(VP_IDLE, "  1M, idle: TEST FAULT: 1M idle: x"))
    c, _d = counted(run(faulty), run(faulty))
    assert len([x for x in c if x.startswith("TEST FAULT in ")]) == 2, c
    for why, b in (("no report", run(None)),
                   ("no vport-bitrate result", dict(base, record={"results": []}))):
        try:
            counted(base, b)
        except ValueError:
            continue
        raise AssertionError("not refused: %s" % why)


TESTS = [test_identity, test_catalogue, test_list_loads, test_tree_names, test_shells,
         test_idf_shell, test_targets, test_settings_keys, test_suite_settings,
         test_shared_pins,
         test_madhouse_entries, test_tree_setup, test_identifier_scan, test_artifacts_row,
         test_masked_log,
         test_sim_judged, test_run_logs, test_vport_windows]

MUTANTS = [
    ("suites from another file", '"charge-interposer/bench_suite.py:%s"', '"charge-interposer/suite.py:%s"'),
    ("product files not the suite's", "    return bench_suite.product_files(root)\n",
     "    return bench_suite.product_files(root)[1:]\n"),
    ("self-tests without their flag", '["--self-test"]', "[]"),
    ("a harness check dropped", "    for cid, argv in BS.HARNESS_LOCAL:\n",
     "    for cid, argv in BS.HARNESS_LOCAL[1:]:\n"),
    ("a harness test's can-fail dropped", "    for t in BS.HARNESS_PY_CANFAILS:\n",
     "    for t in BS.HARNESS_PY_CANFAILS[:-1]:\n"),
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
    ("the variant not passed", '    return ["build", opts["variant"]]', '    return ["build"]'),
    ("another variant's build taken",
     '    if desc.get("project_name") != "interposer_%s" % v:', "    if False:"),
    ("the bootloader image dropped",
     '              "bootloader": os.path.join(bd, "bootloader", "bootloader.bin"),\n', ""),
    ("a missing file taken", "        if not os.path.isfile(p):\n", "        if False:\n"),
    ("no compiler recorded", "cc[i + 1] if i is not None", "None if i is not None"),
    ("the idf shell without its tools path",
     ' "--idf-tools-path", idf["idf_tools_path"],\n', "\n"),
    ("the idf shell on madhouse", '    if shell == "idf" and host == "local":',
     '    if shell == "idf":'),
    ("an idf setting missing, taken", "        if missing:\n", "        if False:\n"),
    ("no shared pin for the trees", '        out[comp] = pins[key]["commit"]\n', "        pass\n"),
    ("the pinned checkout not named by its tag", '("shared-%s" % p["tag"]) if',
     '("shared-%s" % c) if'),
    ("a checkout at another commit named by the tag",
     'if p.get("commit") == commit else', 'if p else'),
    ("only the TWAI fake checked out",
     'TREE_SHARED = {"bench": "shared/bench", "tools": "shared/tools",\n'
     '               "twai_port": "shared/twai_port"}',
     'TREE_SHARED = {"twai_port": "shared/twai_port"}'),
    ("two components at one tag in one folder", "        if d in taken:\n", "        if False:\n"),
    ("another ESP-IDF taken", '    if desc.get("git_revision") != IDF_VERSION:', "    if False:"),
    ("a failed build's keys given", "        if not app:\n            continue\n",
     '        if not app:\n            app = "none"\n'),
    ("the build folder not the image's", '        out["build_%s" % bid] = os.path.dirname(app)',
     '        out["build_%s" % bid] = app'),
    ("no ELF key", '        out["elf_%s" % bid] = b["elf"]["path"]\n', ""),
    ("a commit from before the pin file refused", "    if pins is None:\n        return {}\n",
     '    if pins is None:\n        raise AdapterError("no pin file")\n'),
    ("the ELF sha256 not the record's",
     '"elf_sha256": r["elf"]["sha256"], "app_sha256"', '"elf_sha256": "", "app_sha256"'),
    ("the app sha256 not the record's", '"app_sha256": app["sha256"]}', '"app_sha256": ""}'),
    ("a bench run missing a variant taken", "        if absent:\n", "        if False:\n"),
    ("any target's build taken", '        if r.get("target") != "idf-build":',
     "        if False:"),
    ("the build folder not the app's", '{"dir": os.path.dirname(app["path"]), "build"',
     '{"dir": app["path"], "build"'),
    ("vport: an arm's verdict not counted", '        if x != y:\n            counted("VERDICT arm',
     '        if False:\n            counted("VERDICT arm'),
    ("vport: the check's verdict not counted", "    if va != vb:\n", "    if False:\n"),
    ("vport: a TEST FAULT not counted",
     '            counted("TEST FAULT in %s: %s" % (side, ln))', "            pass"),
    ("vport: a missing report taken", "    if not os.path.isfile(path):\n",
     '    if not os.path.isfile(path):\n        return rs[0]["verdict"], ""\n'),
    ("vport: figures counted", '{"kind": "figure", "name": "%s %s" % (w, name)',
     '{"kind": "verdict", "name": "%s %s" % (w, name)'),
    ("vport: a window on one side only not counted",
     '"kind": "missing", "line": "MISSING in b: window', '"kind": "flag", "line": "MISSING in b: window'),
    ("vport: FLAG lines not shown", '        out += [{"kind": "flag"', '        out += [] and [{"kind": "flag"'),
    ("vport: the same TEST FAULT on both sides counted once",
     '    for side, p in (("a", pa), ("b", pb)):\n        for ln in p["faults"]:',
     '    for side, p in (("a", pa),):\n        for ln in p["faults"]:'),
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
