#!/usr/bin/env python3
"""The charge interposer's adapter for the acceptance runner (shared/bench
ACCEPT-SPEC A15): what differs by project, beside the project's suite
(bench_suite.py). The runner loads it from the list's commit, so a list and
this adapter always move together.

Tester, 2026-11-01 (the charge-interposer reviewer's assignment: our adapter
first, for port step 2a). This first cut gives:
  PROJECT, ROLE      the project's folder and the bench role
  SUITES             the three suites in bench_suite.py, as nested suite runs
  product_files      bench_suite.product_files: the same split, so the
                     runner's product digest and the chain runner's agree
  catalogue          the harness-test catalogue (A8): the suite's own lists
                     -- the Python tests, the bench drivers' self-tests and
                     can-fails, the local harness checks, and tagged
                     "madhouse" what the suite runs on madhouse only (the
                     host bridge, unit and diff suites, check-port, regress,
                     the madhouse mutants, every sim) -- each a committed
                     file with its arguments, shell, judge, tags and the
                     paths it covers
  tree_env,          what names a tree to the code run in it: INTERPOSER_CODE
  tree_settings      (paths.code_dir() refuses any other) and a suite run's
                     interposer_code; and TWAI_FAKE_DIR, the tree's checkout
                     of the shared TWAI fake when the list gives the tree a
                     twai_port, which host_bridge builds against in the
                     catalogue's host-bridge-shared-fake (TWAI_FAKE=shared:
                     port step 1b's acceptance E by the runner; its can-fail
                     is run.sh's own --can-fail, reviewer 2026-11-02)
  SHELLS, shell_argv python (the host's python from the settings), git-bash
                     (the settings' bash by its full path: a bare "bash" on
                     Windows is WSL's), powershell
  JUDGES, judge      bench_suite's judges by name
  identifier_scan    reverse-it's prepush scan (its patterns and private
                     value file, firmware/prepush-scan/) over a record: each
                     file's hits by category and count, never the text
  artifacts_row      the record's row at the top of the runs/ rows of the
                     artifacts README beside the runs folder
                     (projects/vtrux/notes/artifacts/interposer-firmware/)
  COMPARERS          port step 0's comparison (reverse-it's one-off
                     port0_compare.py, 2026-10-26), committed (A10):
                     masked-log  two check logs line by line, with paths,
                                 run ids, clock times, durations and
                                 timestamps masked and the runner's echo
                                 lines dropped
                     sim-judged  a sim's judged and state lines aligned by
                                 shape: the same line with other numbers is
                                 a figure difference with both sides'
                                 numbers, a line on one side only a text one
                     run-logs    two runs case by case: verdicts, cases in
                                 one run only, sims (m-sims/) by sim-judged,
                                 the rest by masked-log; a carried result's
                                 log is read from the run it came from
                     vport-windows  two runs' b-vport/vport-bitrate reports
                                 window by window (port step 2a (f)): the
                                 check's and each arm's verdict, the final
                                 line, a window on one side only and any
                                 TEST FAULT count; figures and FLAG lines
                                 are shown side by side, never judged; a
                                 side with no report is refused
THE MADHOUSE HOST. A madhouse entry runs from a tree on madhouse (a list's
[trees.<name>] with host = "madhouse"), which the runner makes under the
host's workroot in the settings and removes with everything in it there
that matches what it makes. So acceptance runs use settings of their own
(accept_settings_<host>.json beside the bench settings in reverse-it's
interposer-firmware/), whose madhouse workroot is a folder the acceptance
runner alone uses -- never the bench settings' one, where the chain
runner's bench-suite runs leave their folders, nor one holding run folders
kept by hand (the charge-interposer reviewer, 2026-11-02).

Both workroots are in the tester's scratch folder on madhouse, not /tmp:
~/scratch-tester/charge-interposer-harness/accept for acceptance and
.../runner for the bench settings, which give each as a full path (the
charge-interposer reviewer, 2026-11-02). That folder already holds the tester's PlatformIO core
(PLATFORMIO_CORE_DIR). madhouse has no interposer worktree whose scratch/
AGENTS.md "Files and folders" could mean, and the anchor is nobody's
working tree. The runner makes the parent folders when it needs them and
removes only what it makes there (A14).

  TARGETS            "idf-build" (option variant): the product's own
                     charge-interposer/vtrux_tools.py build <variant>, in the
                     build's own tree, through the "idf" shell. Its outputs
                     are read inside that tree, from firmware/build/<variant>/
                     (the launcher's own folder; the charge-interposer
                     reviewer, 2026-11-04): the app, bootloader and
                     partition-table images, the ELF, the variant and the
                     toolchain from project_description.json, which must name
                     interposer_<variant>
  shell "idf"        fw_harness/idf_build_shell.py from the list's commit:
                     exports the interposer's ESP-IDF from the machine's
                     settings ("idf": idf_path, idf_tools_path, python_dir),
                     runs the command, then fails the build if it changed a
                     tracked file of the tree (dependencies.lock: shared/tools
                     R33); local only
  shared_pins,       the shared pins, shared/bench, shared/tools and
  tree_paths         shared/twai_port, from charge-interposer/firmware/
                     shared_pins.json at the tree's commit; each pinned tag's
                     checkout is named shared-<tag>, the folder shared_pin.py
                     and vtrux_tools.py look for beside a worktree's root (a
                     second component at the same tag gets shared-<component>)

  settings_keys      for each build that made its images: build_<id> (its
                     build folder), image_<id> (the app) and elf_<id>, for a
                     list's checks to name ("{build_bridge}")
  suite_settings     a suite run's images_from (A9): interposer_builds, each
                     variant's build folder, build id and the core's recorded
                     ELF and app sha256, which bench_suite flashes and checks
                     instead of images_dir; a bench suite run must name all
                     three (BENCH_VARIANTS)

  tree_setup,        what each fresh tree needs before its checks: the
  tree_installed     pinned canre installed and checked (canre_pin.py), and
                     locally the pinned can-tools canbench/ (can_tools_pin.py),
                     into the tree's own scratch/; what was installed, as the
                     pin tools' checks verified it

Tests: accept_adapter_test.py (with --can-fail), run against the core named
by ACCEPT_CORE.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PROJECT = "charge-interposer"
ROLE = "tester (charge-interposer)"
SUITES = {n: "charge-interposer/bench_suite.py:%s" % n
          for n in ("emulated", "bench", "harness")}
SHELLS = ("python", "git-bash", "powershell", "idf")
IDF_SHELL = os.path.join(HERE, "fw_harness", "idf_build_shell.py")
IDF_VERSION = "v5.4.2"     # spec 8.2: the interposer builds on ESP-IDF v5.4.2 (user, 2026-11-03)
# the machine variables a check may see (AGENTS.md, local-paths.md)
MACHINE_VARS = ("REVERSE_IT_DIR", "CAN_TOOLS_DIR", "VTRUX_PUBLIC_REPO")
JUDGES = ("script", "recorded", "c12", "host-bridge", "corrupt-pin", "scenario")
COMPARERS = ("masked-log", "sim-judged", "run-logs", "vport-windows")
# the folder names the runner's scratch helper gives trees, builds and mutants
MASKS = {"tree-root": r"(?:tree|build|mutant)-[\w.-]+?-\d+-[0-9a-f]{6}"}


class AdapterError(Exception):
    pass


# ---- the project ------------------------------------------------------------
def product_files(root):
    import bench_suite
    return bench_suite.product_files(root)


PIN_FILE = "charge-interposer/firmware/shared_pins.json"
# the shared components a tree checks out, each at its pin: the harness's
# shared/bench (shared_pin.use_bench) and shared/twai_port (host_bridge's
# TWAI_FAKE=shared fake), and the build tooling's shared/tools
# (vtrux_tools.py); each pinned tag's checkout is shared-<tag>/ (tree_paths)
TREE_SHARED = {"bench": "shared/bench", "tools": "shared/tools",
               "twai_port": "shared/twai_port"}
PROJECT_DIR = "repo"


def _pins(repo, commit):
    """The pin file at commit; None at a commit from before it (port step
    2a), whose tree carries its own shared/."""
    import json
    import subprocess
    spec = "%s:%s" % (commit, PIN_FILE)
    if subprocess.run(["git", "-C", repo, "cat-file", "-e", spec],
                      capture_output=True).returncode != 0:
        return None
    r = subprocess.run(["git", "-C", repo, "show", spec], capture_output=True, text=True)
    if r.returncode != 0:
        raise AdapterError("%s at %s could not be read: %s" % (PIN_FILE, commit[:12],
                                                            r.stderr.strip()))
    return json.loads(r.stdout)


def shared_pins(repo, commit):
    """{component: commit} for the trees, from the pin file at commit;
    none at a commit from before the pin file."""
    pins = _pins(repo, commit)
    if pins is None:
        return {}
    out = {}
    for comp, key in TREE_SHARED.items():
        if key not in pins:
            raise AdapterError("%s at %s has no %s pin" % (PIN_FILE, commit[:12], key))
        out[comp] = pins[key]["commit"]
    return out


def tree_paths(name, tree):
    """The project in repo/; a shared component at its pinned commit in
    shared-<tag>/ beside it (the folder shared_pin.py and vtrux_tools.py
    look for beside a worktree's root), else shared-<component>/. Pins may
    name different tags (shared/bench moves alone); a second component at
    a tag already named gets its own shared-<component>/, the first
    checkout of the tag's commit serving both."""
    out = {"project": PROJECT_DIR}
    pins = {}
    if tree.get("shared"):
        pins = _pins(os.path.dirname(HERE), tree["project"]) or {}
    taken = set()
    for c, commit in sorted(tree["shared"].items()):
        p = pins.get(TREE_SHARED.get(c, ""), {})
        d = ("shared-%s" % p["tag"]) if p.get("commit") == commit else ("shared-%s" % c)
        if d in taken:
            d = "shared-%s" % c
        taken.add(d)
        out[c] = d
    return out


def _code(tree):
    if tree.get("remote"):                       # a posix host: madhouse
        import posixpath
        return posixpath.join(tree["project_dir"], "charge-interposer")
    return os.path.join(tree["project_dir"], "charge-interposer")


def _shared_fake(tree):
    """The tree's checkout of the shared TWAI fake (shared/twai_port/test),
    if the list gives the tree a twai_port; else None."""
    d = (tree.get("shared_dirs") or {}).get("twai_port")
    if not d:
        return None
    if tree.get("remote"):
        import posixpath
        return posixpath.join(d, "shared", "twai_port", "test")
    return os.path.join(d, "shared", "twai_port", "test")


def tree_env(tree):
    """INTERPOSER_CODE, and TWAI_FAKE_DIR when the tree has a twai_port
    checkout: the fake host_bridge's run.sh builds against under
    TWAI_FAKE=shared (only an entry that sets that uses it)."""
    env = {"INTERPOSER_CODE": _code(tree)}
    fake = _shared_fake(tree)
    if fake:
        env["TWAI_FAKE_DIR"] = fake
    return env


def tree_settings(tree):
    return {"interposer_code": _code(tree)}


def settings_keys(builds, trees):
    """For each build that made its images: build_<id>, its build folder
    (where variant_diff.py reads its configuration), image_<id>, its app
    image, and elf_<id>, its ELF; nothing for a build that failed or did
    not run."""
    out = {}
    for bid, b in sorted(builds.items()):
        app = ((b.get("images") or {}).get("app") or {}).get("path")
        if not app:
            continue
        out["build_%s" % bid] = os.path.dirname(app)
        out["image_%s" % bid] = app
        out["elf_%s" % bid] = b["elf"]["path"]
    return out


# The interposer images the bench suite flashes (bench_suite.bench): a bench
# suite run with images_from must get all three from the list's builds.
BENCH_VARIANTS = ("bridge", "witness", "truck")


def suite_settings(run, records):
    """A suite run's images (ACCEPT-SPEC A9; the core's suite_images): from
    the records of its images_from builds, as the core wrote them,
    interposer_builds = {variant: {"dir": its build folder, "build": its
    id, "elf_sha256", "app_sha256"}}, the sha256s the core recorded, never
    read again from the folders (the charge-interposer reviewer,
    2026-11-04). bench_suite._interposer_image checks the bytes against
    them. Refused: a build that is not this adapter's idf-build, and a
    bench suite run missing one of the variants it flashes, so the run
    stops before anything is flashed or the lock taken."""
    out = {}
    for r in records:
        if r.get("target") != "idf-build":
            raise AdapterError("suite run %s: images_from build %s is a %r build, not "
                               "idf-build" % (run.get("id"), r.get("id"), r.get("target")))
        app = r["images"]["app"]
        out[r["variant"]] = {"dir": os.path.dirname(app["path"]), "build": r["id"],
                             "elf_sha256": r["elf"]["sha256"], "app_sha256": app["sha256"]}
    if run.get("suite") == "bench":
        absent = [v for v in BENCH_VARIANTS if v not in out]
        if absent:
            raise AdapterError("suite run %s: the bench suite flashes %s, and its "
                               "images_from gives no build of %s"
                               % (run.get("id"), ", ".join(BENCH_VARIANTS),
                                  ", ".join(absent)))
    return {"interposer_builds": out}


# ---- tree setup (the core's tree_setup) -------------------------------------
INSTALLED = (("canre", re.compile(r"^canre ([0-9a-f]{40}) checked at (.+)$", re.M)),
             ("can-tools", re.compile(r"^can-tools ([0-9a-f]{40}) canbench/ checked at (.+)$",
                                      re.M)))


def tree_setup(tree):
    """What a fresh tree needs before its checks run: the pinned canre,
    installed into the tree's own scratch/ from the public repo at the pin
    and then checked; on a local tree also the pinned can-tools canbench/,
    written from the CAN_TOOLS_DIR clone and checked (madhouse has no
    can-tools clone and opens no dongle). Committed files of the tree."""
    cmds = [["charge-interposer/canre_pin.py", "install"],
            ["charge-interposer/canre_pin.py", "check"]]
    if not tree.get("remote"):
        cmds += [["charge-interposer/can_tools_pin.py", "install"],
                 ["charge-interposer/can_tools_pin.py", "check"]]
    return cmds


def tree_installed(tree, results):
    """What the setup installed, as the pin tools' checks verified it:
    {"canre": {"commit", "folder"}, "can-tools": {...}}."""
    out = {}
    for r in results:
        for name, rx in INSTALLED:
            m = rx.search(r["console"])         # only a check's line: "<commit> checked at"
            if m:
                out[name] = {"commit": m.group(1), "folder": m.group(2).strip()}
    return out


# ---- shells and judges ------------------------------------------------------
def default_shell(path):
    return {".py": "python", ".sh": "git-bash", ".ps1": "powershell"}.get(
        os.path.splitext(path)[1])


def shell_argv(shell, settings, host="local"):
    """The command a check's file runs with, on its host: the host's python
    from the settings (-B once); bash (Git Bash by its full path locally, a
    bare bash on Windows being WSL's; plain bash on a Linux host);
    PowerShell locally only."""
    if shell == "python":
        py = list(settings["hosts"][host]["python"])
        return py if "-B" in py else py + ["-B"]
    if shell == "git-bash":
        if host != "local":
            return ["bash"]
        if not settings.get("bash"):
            raise AdapterError("the settings name no bash: give Git Bash by its full "
                               "path (a bare bash on Windows is WSL's)")
        return [settings["bash"]]
    if shell == "powershell" and host == "local":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    if shell == "idf" and host == "local":
        idf = settings.get("idf") or {}
        missing = [k for k in ("idf_path", "idf_tools_path", "python_dir") if not idf.get(k)]
        if missing:
            raise AdapterError("the settings' \"idf\" names no %s: the interposer's ESP-IDF "
                               "export comes from the machine's settings (local-paths.md)"
                               % ", ".join(missing))
        return shell_argv("python", settings, host) + [
            IDF_SHELL, "--idf-path", idf["idf_path"], "--idf-tools-path", idf["idf_tools_path"],
            "--python-dir", idf["python_dir"], "--"]
    raise AdapterError("no shell %r on host %s" % (shell, host))


# ---- build targets (A5) -----------------------------------------------------
def _build_args(opts, out):
    return ["build", opts["variant"]]


def _build_outputs(out, opts):
    """What a build made, read in its tree (out's sibling repo/):
    firmware/build/<variant>/, which project_description.json must name
    interposer_<variant>."""
    import json
    v = opts["variant"]
    bd = os.path.join(os.path.dirname(out), PROJECT_DIR, "charge-interposer", "firmware",
                      "build", v)
    try:
        with open(os.path.join(bd, "project_description.json"), encoding="utf-8") as f:
            desc = json.load(f)
    except (OSError, ValueError) as e:
        raise AdapterError("build %s: no project description in %s (%s)" % (v, bd, e))
    if desc.get("project_name") != "interposer_%s" % v:
        raise AdapterError("build %s: %s describes %r, not interposer_%s"
                           % (v, bd, desc.get("project_name"), v))
    if desc.get("git_revision") != IDF_VERSION:
        raise AdapterError("build %s: built with ESP-IDF %r, not %s (spec 8.2)"
                           % (v, desc.get("git_revision"), IDF_VERSION))
    images = {"app": os.path.join(bd, desc["app_bin"]),
              "bootloader": os.path.join(bd, "bootloader", "bootloader.bin"),
              "partition-table": os.path.join(bd, "partition_table", "partition-table.bin")}
    elf = os.path.join(bd, desc["app_elf"])
    for p in list(images.values()) + [elf]:
        if not os.path.isfile(p):
            raise AdapterError("build %s: %s is missing" % (v, p))
    cc = desc.get("c_compiler", "").replace("\\", "/").split("/")
    i = next((k for k, part in enumerate(cc) if part == "xtensa-esp-elf"), None)
    return {"images": images, "elf": elf, "variant": v,
            "toolchain": {"esp_idf": desc.get("git_revision"), "target": desc.get("target"),
                          "compiler": cc[i + 1] if i is not None and i + 1 < len(cc) else None}}


TARGETS = {
    "idf-build": {"options": ["variant"], "run": "charge-interposer/vtrux_tools.py",
                  "shell": "idf", "args": _build_args, "outputs": _build_outputs,
                  "timeout_s": 3 * 3600},
}


def judge(name):
    import bench_suite as BS
    return {"script": BS.script_judge, "recorded": BS.recorded_judge,
            "c12": BS.c12_judge, "host-bridge": BS.host_bridge_judge,
            "corrupt-pin": BS.corrupt_pin_judge, "scenario": BS.scenario_judge}[name]


# ---- the catalogue (A8) -----------------------------------------------------
def _entry(cid, path, args, tags, timeout_s=3600):
    return {"id": cid, "path": path, "args": list(args), "env": {}, "shell": "python",
            "timeout_s": timeout_s, "tags": list(tags), "covers": [path]}


def catalogue(repo, commit):
    """The harness tests of bench_suite.py's lists, as the runner's
    catalogue; run locally (the madhouse lane waits for remote trees)."""
    import bench_suite as BS
    out = []
    for t in BS.LOGIC_PY_TESTS:
        out.append(_entry(t[:-3].replace("_", "-"), "charge-interposer/" + t, [],
                          ["logic"]))
    for t in BS.HARNESS_PY_TESTS:
        out.append(_entry(t[:-3].replace("_", "-"), "charge-interposer/" + t, [],
                          ["harness"]))
    for t in BS.HARNESS_PY_CANFAILS:
        out.append(_entry(t[:-3].replace("_", "-") + "-canfail", "charge-interposer/" + t,
                          ["--can-fail"], ["harness", "can-fail"]))
    for s in BS.SELF_TESTS:
        out.append(_entry("st." + s.replace("_", "-"),
                          "charge-interposer/bench_drivers/%s.py" % s, ["--self-test"],
                          ["harness", "self-test"], 1800))
    for s in BS.CAN_FAILS:
        out.append(_entry("cf." + s.replace("_", "-"),
                          "charge-interposer/bench_drivers/%s.py" % s, ["--can-fail"],
                          ["harness", "can-fail"]))
    for cid, argv in BS.HARNESS_LOCAL:
        path = argv[0].replace("{interposer_code}/", "charge-interposer/")
        out.append(_entry(cid, path, argv[1:], ["harness", "harness-local"]))
    # what bench_suite runs on madhouse only (Linux: bash scripts, the sims)
    out.append(_mad("host-bridge", "charge-interposer/fw_harness/host_bridge/run.sh", [],
                    shell="git-bash", judge="host-bridge"))
    # the same suite on the shared TWAI fake (port step 1b's acceptance E,
    # re-run by the runner): its tree needs a twai_port checkout, which
    # tree_env names in TWAI_FAKE_DIR; its can-fail is run.sh's own
    # --can-fail mode (harness_checks/shared_fake_canfail.py), which runs
    # the suite five times, under this entry's timeout
    e = _mad("host-bridge-shared-fake", "charge-interposer/fw_harness/host_bridge/run.sh",
             [], shell="git-bash", judge="host-bridge", timeout_s=4 * 3600)
    e["env"]["TWAI_FAKE"] = "shared"
    e["covers"] += ["charge-interposer/harness_checks/shared_fake_canfail.py",
                    "shared/twai_port/test/twai_fake.c",
                    "shared/twai_port/test/cpp/twai_fake.cpp"]
    out.append(e)
    out.append(_mad("host-unit", "charge-interposer/host_unit/run.sh", [],
                    shell="git-bash", timeout_s=1800))
    out.append(_mad("host-diff", "charge-interposer/fw_harness/host_diff/diff_all.sh", [],
                    shell="git-bash", timeout_s=7200))
    out.append(_mad("check-port", "charge-interposer/fw_harness/host_diff/check_port.py", []))
    out.append(_mad("regress-all", "charge-interposer/regress.py", ["--all"], timeout_s=7200))
    for cid, argv in BS.MADHOUSE_MUTANTS:
        shell = "python" if argv[0] == "@python" else "git-bash"
        judge = {"corrupt_pin_judge": "corrupt-pin"}.get(BS.MUTANT_JUDGES.get(cid), "script")
        out.append(_mad(cid, "charge-interposer/" + argv[1], argv[2:], shell=shell,
                        judge=judge, timeout_s=7200))
    for s in BS.SIM_SCENARIOS:
        out.append(_mad("sim." + s, "charge-interposer/run_scenario.py",
                        [s, "--time-scale", "1", "--keep-traces", "--outdir", "{outdir}"],
                        timeout_s=4 * 3600,
                        judge="scenario", cls="wallclock"))
    return out


def _mad(cid, path, args, shell="python", judge="script", cls="build", timeout_s=3600):
    """A madhouse entry, with the environment bench_suite gives every
    madhouse check (INTERPOSER_CODE comes from tree_env; canre from the
    tree's own install)."""
    env = {"L3_STIMULUS": "{madhouse_artifacts}/l3_stimulus.txt"}
    e = _entry(cid, path, args, ["madhouse"], timeout_s)
    e.update(env=env, shell=shell, judge=judge, cls=cls)
    return e


# ---- comparers (A10) --------------------------------------------------------
JUDGED = re.compile(r"^\s*\[(PASS|FAIL|KNOWN-FAIL|WARN|SKIP|NOT-RUN)\]\s")
EVENT = re.compile(r"^\s*\[([A-Z_]+)\]\s")
NUM = re.compile(r"-?\d+(?:\.\d+)?")
# timestamps before clock times, so a timestamp is masked whole
NOISE = [
    (re.compile(r"\d{4}-\d\d-\d\dT[\d:.+-]+"), "<ts>"),
    (re.compile(r"/tmp/\S+"), "<tmp>"),
    (re.compile(r"charge-interposer-(?:emulated|bench|harness)_\d{8}T\d{6}_[0-9a-f]{4}"), "<run>"),
    (re.compile(r"\b\d\d:\d\d:\d\d\b"), "<hh:mm:ss>"),
]
# in fragments, so this file holds no machine-absolute path of its own
# (paths_test.py's scan, which the smoke run of 2026-11-02 caught)
FULL_NOISE = NOISE + [
    (re.compile(r"/home/\S+|[A-Za-z]:[/\\]\S+|\S*seadrive" + r"_root\S*"), "<path>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|ms|sec|seconds)\b"), "<dur>"),
]


def _masked(s, noise):
    for rx, rep in noise:
        s = rx.sub(rep, s)
    return s


def _full_lines(path):
    """Every line of a check log but the runner's echo lines, masked; None
    if there is no log."""
    if not path or not os.path.exists(path):
        return None
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(("$ ", "# cwd=")):
                continue
            out.append(_masked(line.rstrip(), FULL_NOISE))
    return out


def _judged(path):
    """[(shape, numbers, raw)] of a log's judged and state lines."""
    if not path or not os.path.exists(path):
        return None
    lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not (JUDGED.match(line) or EVENT.match(line)):
                continue
            if "bench premise" in line or "process priority" in line:
                continue
            lines.append((_masked(line.strip(), NOISE), line.strip()))
    return [(NUM.sub("#", s), NUM.findall(s), raw) for s, raw in lines]


def _masked_log_diffs(pa, pb, prefix=""):
    import difflib
    a, b = _full_lines(pa), _full_lines(pb)
    if a is None or b is None:
        return [{"kind": "text", "line": "%slog missing in %s" % (prefix, "a" if a is None else "b")}]
    return [{"kind": "text", "line": prefix + x}
            for x in difflib.unified_diff(a, b, lineterm="", n=0)
            if x[:1] in "+-" and not x.startswith(("+++", "---"))]


def _sim_diffs(pa, pb, prefix=""):
    from difflib import SequenceMatcher
    a, b = _judged(pa), _judged(pb)
    if a is None or b is None:
        return [{"kind": "text", "line": "%slog missing in %s" % (prefix, "a" if a is None else "b")}]
    out = []
    ops = SequenceMatcher(None, [x[0] for x in a], [x[0] for x in b], autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                xa, xb = a[i1 + k], b[j1 + k]
                if xa[1] != xb[1]:
                    out.append({"kind": "figure", "name": xa[0],
                                "line": "%s~ %s: a %s | b %s" % (prefix, xa[0], " ".join(xa[1]),
                                                                 " ".join(xb[1]))})
        else:
            for side, xs in (("a", a[i1:i2]), ("b", b[j1:j2])):
                for x in xs:
                    out.append({"kind": "text", "line": "%s%s only: %s" % (prefix, side, x[2])})
    return out


def _case_log(side, r):
    """A result's check log: in the run it was carried from, if it was."""
    src = r.get("carried_from") or os.path.basename(side["dir"])
    rel = r.get("log") or os.path.join(*(r["id"].split("/") + ["check.log"]))
    return os.path.join(os.path.dirname(side["dir"]), src, rel)


def _run_logs(a, b, spec, adapter):
    ra = {r["id"]: r for r in a["record"]["results"]}
    rb = {r["id"]: r for r in b["record"]["results"]}
    out = []
    for cid in sorted(set(ra) | set(rb)):
        if cid not in rb:
            out.append({"kind": "missing", "line": "MISSING in b: %s (was %s)" % (cid, ra[cid]["verdict"])})
            continue
        if cid not in ra:
            out.append({"kind": "new", "line": "NEW in b: %s %s" % (cid, rb[cid]["verdict"])})
            continue
        if ra[cid]["verdict"] != rb[cid]["verdict"]:
            out.append({"kind": "verdict", "line": "VERDICT %s: %s -> %s"
                        % (cid, ra[cid]["verdict"], rb[cid]["verdict"])})
        pa, pb = _case_log(a, ra[cid]), _case_log(b, rb[cid])
        if cid.startswith("m-sims/"):
            out += _sim_diffs(pa, pb, cid + ": ")
        else:
            out += _masked_log_diffs(pa, pb, cid + ": ")
    return out


# ---- vport-windows: the bench's vport-bitrate, run against run ----------------
# The charge-interposer reviewer, 2026-11-04 (port step 2a (f), the judged
# vport-bitrate run three times): two runs' vport-bitrate reports compared
# window by window. What counts, as the core's "verdict" kind: the check's
# verdict, each arm's verdict in the report's "== verdicts" section and its
# final FAIL:/VOID: line, a window or arm on one side only, and every TEST
# FAULT on either side. The windows' figures and the FLAG lines are printed
# side by side and never judged (no figure tolerance). A side with no report
# is refused (raises): nothing to compare is not "the same".
VPORT_CHECK = "b-vport/vport-bitrate"
VPORT_REPORT = "vport-bitrate.txt"
_VP_WINDOW = re.compile(r"^  (\d+[kM]), ([^:]+): (.*)$")
_VP_ARM = re.compile(r"^  (\S+)\s+(PASS|FAIL|VOID \(TEST FAULT\))\s*$")
_VP_FIGURES = (
    ("error frames, bursts, seconds",
     re.compile(r"(\d+) error frame\(s\) in (\d+) burst\(s\) over (\d+) s")),
    ("per burst min, p50, max", re.compile(r"per burst min (\d+) / p50 (\d+) / max (\d+)")),
    ("bursts per minute", re.compile(r"([\d.]+) bursts per minute")),
    ("node frames through, tried", re.compile(r"got through: (\d+) of the (\d+)")),
    ("queued, refused", re.compile(r"(\d+) queued, (\d+) refused")),
    ("backlog frames", re.compile(r"(\d+) frame\(s\) from earlier windows' backlog")))


def _vport_report(side):
    """(the check's verdict, the report's text) of a run side."""
    rs = [r for r in (side.get("record") or {}).get("results") or [] if r["id"] == VPORT_CHECK]
    if not rs:
        raise ValueError("%s has no %s result" % (side.get("dir"), VPORT_CHECK))
    path = os.path.join(os.path.dirname(_case_log(side, rs[0])), VPORT_REPORT)
    if not os.path.isfile(path):
        raise ValueError("%s: no %s report at %s" % (VPORT_CHECK, VPORT_REPORT, path))
    with open(path, encoding="utf-8", errors="replace") as f:
        return rs[0]["verdict"], f.read()


def _vport_parse(text):
    """{"windows": {"<rate>/<state>": {figure: [numbers]} or {"fault": line}},
    "arms": {arm: verdict}, "final": line or None, "faults": [lines],
    "flags": [lines]}; board> lines are not read."""
    out = {"windows": {}, "arms": {}, "final": None, "faults": [], "flags": []}
    in_verdicts = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("board>"):
            continue
        if ln.startswith("== "):
            in_verdicts = ln.strip() == "== verdicts"
            continue
        if "TEST FAULT" in ln:
            out["faults"].append(ln.strip())
        if ln.strip().startswith("FLAG:"):
            out["flags"].append(ln.strip())
        if ln.startswith(("FAIL:", "VOID:")):
            out["final"] = ln.strip()
        m = _VP_ARM.match(ln)
        if in_verdicts and m:
            out["arms"][m.group(1)] = m.group(2)
            continue
        m = _VP_WINDOW.match(ln)
        if m:
            w = "%s/%s" % (m.group(1), m.group(2))
            body = m.group(3)
            if body.startswith("TEST FAULT"):
                out["windows"][w] = {"fault": body}
                continue
            figs = {}
            for name, rx in _VP_FIGURES:
                f = rx.search(body)
                if f:
                    figs[name] = [float(x) for x in f.groups()]
            out["windows"][w] = figs
    return out


def _vport_windows(a, b, spec, adapter):
    va, ta = _vport_report(a)
    vb, tb = _vport_report(b)
    pa, pb = _vport_parse(ta), _vport_parse(tb)
    out = []

    def counted(line):
        out.append({"kind": "verdict", "line": line})
    if va != vb:
        counted("VERDICT %s: %s -> %s" % (VPORT_CHECK, va, vb))
    for side, p in (("a", pa), ("b", pb)):
        for ln in p["faults"]:
            counted("TEST FAULT in %s: %s" % (side, ln))
    for arm in sorted(set(pa["arms"]) | set(pb["arms"])):
        x, y = pa["arms"].get(arm), pb["arms"].get(arm)
        if x != y:
            counted("VERDICT arm %s: %s -> %s" % (arm, x or "(none)", y or "(none)"))
    if pa["final"] != pb["final"]:
        counted("FINAL LINE: %s -> %s" % (pa["final"] or "(none)", pb["final"] or "(none)"))
    for w in sorted(set(pa["windows"]) | set(pb["windows"])):
        if w not in pb["windows"]:
            out.append({"kind": "missing", "line": "MISSING in b: window %s" % w})
            continue
        if w not in pa["windows"]:
            out.append({"kind": "new", "line": "NEW in b: window %s" % w})
            continue
        fa, fb = pa["windows"][w], pb["windows"][w]
        for name in [n for n, _rx in _VP_FIGURES]:
            x, y = fa.get(name), fb.get(name)
            if x != y:
                fmt = lambda v: "-" if v is None else " ".join(  # noqa: E731
                    "%d" % n if float(n).is_integer() else "%g" % n for n in v)
                out.append({"kind": "figure", "name": "%s %s" % (w, name),
                            "a": (x or [None])[0], "b": (y or [None])[0],
                            "line": "figure %s %s: a %s | b %s" % (w, name, fmt(x), fmt(y))})
    for side, p in (("a", pa), ("b", pb)):
        out += [{"kind": "flag", "line": "FLAG in %s: %s" % (side, ln[len("FLAG:"):].strip())}
                for ln in p["flags"]]
    return out


def comparer(name):
    return {"masked-log": lambda a, b, spec, ad: _masked_log_diffs(a["log"], b["log"]),
            "sim-judged": lambda a, b, spec, ad: _sim_diffs(a["log"], b["log"]),
            "run-logs": _run_logs, "vport-windows": _vport_windows}[name]


# ---- the record (A13) -------------------------------------------------------
def _prepush():
    import importlib.util
    rid = os.environ.get("REVERSE_IT_DIR", "").strip()
    if not rid:
        raise AdapterError("REVERSE_IT_DIR is not set: the identifier scan is reverse-it's "
                           "prepush scan")
    p = os.path.join(rid, "firmware", "prepush-scan", "prepush_scan.py")
    spec = importlib.util.spec_from_file_location("_prepush_scan", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def identifier_scan(folder, values=None):
    """Each file's identifier hits in the record, as "<file>: <category>
    x<count>", with reverse-it's prepush scan's own functions, patterns and
    private value file (values: another value file, for the test); never
    the text that matched, nor anything of the value file but a category
    and the line of the value file it names."""
    P = _prepush()
    vals = P.load_values(values or P.DEFAULT_VALUES)
    out = []
    for d, dirs, files in os.walk(folder):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, folder).replace("\\", "/")
            counts = {}
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    for cat, s, e in P.find_hits(line, vals):
                        key = cat
                        counts[key] = counts.get(key, 0) + 1
            for cat, k in sorted(counts.items()):
                out.append("%s: %s x%d" % (rel, cat, k))
    return out


def artifacts_row(folder, summary):
    """The record's row, put at the top of the runs/ rows of the artifacts
    README beside the runs folder. Returns the row; None for a --dev run,
    which is never evidence and gets no row (its records go to a dev-runs
    folder of their own: the charge-interposer reviewer, 2026-11-02)."""
    if summary.get("dev"):
        return None
    readme = os.path.join(os.path.dirname(os.path.dirname(folder)), "README.md")
    with open(readme, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.startswith("| `runs/")), None)
    if first is None:
        raise AdapterError("%s has no runs/ rows to put the record's row beside" % readme)
    row = ("| `runs/%s/` | Acceptance record of step %s (VERIFICATION.md %s): %s. Its "
           "README.md, verification_skeleton.md and SHA256SUMS are inside (the "
           "acceptance runner, shared/bench ACCEPT-SPEC A13) |"
           % (os.path.basename(folder), summary["step"]["id"],
              summary["step"]["verification"], summary["verdict"]["lines"][0]))
    lines.insert(first, row)
    with open(readme, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return row
