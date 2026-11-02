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
THE MADHOUSE HOST. A madhouse entry runs from a tree on madhouse (a list's
[trees.<name>] with host = "madhouse"), which the runner makes under the
host's workroot in the settings and removes with everything in it there
that matches what it makes. So acceptance runs use settings of their own
(accept_settings_<host>.json beside the bench settings in reverse-it's
interposer-firmware/), whose madhouse workroot is a folder the acceptance
runner alone uses -- never /tmp/interposer-tester/runner, where the chain
runner's bench-suite runs leave their folders, nor one holding run folders
kept by hand (the charge-interposer reviewer, 2026-11-02).

Not yet: build targets (ESP-IDF from port step 2a, whose build commands the
2a build message gives; the reviewer, 2026-11-01: wait for it), settings
keys from builds, shared pins (this branch carries its own shared/ until
its first re-pin).

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
SHELLS = ("python", "git-bash", "powershell")
# the machine variables a check may see (AGENTS.md, local-paths.md)
MACHINE_VARS = ("REVERSE_IT_DIR", "CAN_TOOLS_DIR", "VTRUX_PUBLIC_REPO")
JUDGES = ("script", "recorded", "c12", "host-bridge", "corrupt-pin", "scenario")
TARGETS = {}
COMPARERS = ("masked-log", "sim-judged", "run-logs")
# the folder names the runner's scratch helper gives trees, builds and mutants
MASKS = {"tree-root": r"(?:tree|build|mutant)-[\w.-]+?-\d+-[0-9a-f]{6}"}


class AdapterError(Exception):
    pass


# ---- the project ------------------------------------------------------------
def product_files(root):
    import bench_suite
    return bench_suite.product_files(root)


def shared_pins(repo, commit):
    """None yet: this branch carries its own shared/ until its first re-pin
    (README, "Shared code")."""
    return {}


def tree_paths(name, tree):
    out = {"project": "repo"}
    out.update({c: "shared-%s" % c for c in tree["shared"]})
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
    return {}


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
    raise AdapterError("no shell %r on host %s" % (shell, host))


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
    for t in ("canre_pin_test.py", "can_tools_pin_test.py", "bus_gs_usb_test.py",
              "paths_test.py", "twai_fake_option_test.py"):
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
    out.append(_mad("host-bridge", "charge-interposer/firmware/test/host_bridge/run.sh", [],
                    shell="git-bash", judge="host-bridge"))
    # the same suite on the shared TWAI fake (port step 1b's acceptance E,
    # re-run by the runner): its tree needs a twai_port checkout, which
    # tree_env names in TWAI_FAKE_DIR; its can-fail is run.sh's own
    # --can-fail mode (harness_checks/shared_fake_canfail.py), which runs
    # the suite five times, under this entry's timeout
    e = _mad("host-bridge-shared-fake", "charge-interposer/firmware/test/host_bridge/run.sh",
             [], shell="git-bash", judge="host-bridge", timeout_s=4 * 3600)
    e["env"]["TWAI_FAKE"] = "shared"
    e["covers"] += ["charge-interposer/harness_checks/shared_fake_canfail.py",
                    "shared/twai_port/test/twai_fake.c",
                    "shared/twai_port/test/cpp/twai_fake.cpp"]
    out.append(e)
    out.append(_mad("host-unit", "charge-interposer/firmware/test/host_unit/run.sh", [],
                    shell="git-bash", timeout_s=1800))
    out.append(_mad("host-diff", "charge-interposer/firmware/test/host_diff/diff_all.sh", [],
                    shell="git-bash", timeout_s=7200))
    out.append(_mad("check-port", "charge-interposer/firmware/test/host_diff/check_port.py", []))
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


def comparer(name):
    return {"masked-log": lambda a, b, spec, ad: _masked_log_diffs(a["log"], b["log"]),
            "sim-judged": lambda a, b, spec, ad: _sim_diffs(a["log"], b["log"]),
            "run-logs": _run_logs}[name]


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
