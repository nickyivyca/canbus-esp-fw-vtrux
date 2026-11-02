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
                     can-fails, the local harness checks -- each a committed
                     file with its arguments, shell, tags and the paths it
                     covers
  tree_env,          what names a tree to the code run in it: INTERPOSER_CODE
  tree_settings      (paths.code_dir() refuses any other) and a suite run's
                     interposer_code
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
Not yet: build targets (the PlatformIO envs now; ESP-IDF from port step 2a,
whose build commands the 2a build message gives), comparers (port step 0's
masked check logs and judged sim lines), settings keys from builds, shared
pins (this branch carries its own shared/ until its first re-pin), and the
madhouse lane (remote trees in the core).

Tests: accept_adapter_test.py (with --can-fail), run against the core named
by ACCEPT_CORE.
"""

import os
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
COMPARERS = ()
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
    return os.path.join(tree["project_dir"], "charge-interposer")


def tree_env(tree):
    return {"INTERPOSER_CODE": _code(tree)}


def tree_settings(tree):
    return {"interposer_code": _code(tree)}


def settings_keys(builds, trees):
    return {}


# ---- shells and judges ------------------------------------------------------
def default_shell(path):
    return {".py": "python", ".sh": "git-bash", ".ps1": "powershell"}.get(
        os.path.splitext(path)[1])


def shell_argv(shell, settings):
    if shell == "python":
        return list(settings["hosts"]["local"]["python"]) + ["-B"]
    if shell == "git-bash":
        if not settings.get("bash"):
            raise AdapterError("the settings name no bash: give Git Bash by its full "
                               "path (a bare bash on Windows is WSL's)")
        return [settings["bash"]]
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    raise AdapterError("no shell %r" % (shell,))


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
              "paths_test.py"):
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
    return out


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


def identifier_scan(folder):
    """Each file's identifier hits in the record, as "<file>: <category>
    x<count>", with reverse-it's prepush scan's patterns and private value
    file; never the text that matched."""
    P = _prepush()
    vals = P.load_values(P.DEFAULT_VALUES)
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
    README beside the runs folder. Returns the row."""
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
