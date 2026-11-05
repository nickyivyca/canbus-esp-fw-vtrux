#!/usr/bin/env python3
"""The acceptance runner's "idf" shell for the interposer's builds: one
command run in the interposer's exported ESP-IDF environment, with the
tree checked afterwards.

Charge-interposer tester, 2026-11-04 (the charge-interposer reviewer's 2a
acceptance list: a build target over charge-interposer/vtrux_tools.py build
<variant>, the shell from the machine's settings, and a build that rewrote
dependencies.lock refused). accept_adapter.py's shell_argv("idf") puts
this file, from the list's commit, in front of the target's own file (from
the build's tree, at the product commit):

    py -3.14 -B idf_build_shell.py --idf-path P --idf-tools-path T
        --python-dir D -- <script.py> [args...]

1. The export: with MSYSTEM taken out of the environment (ESP-IDF refuses
   MSYS, and a runner started from Git Bash passes it down), a PowerShell
   sets IDF_TOOLS_PATH=T and IDF_PATH=P, puts D
   (the Python the export itself calls as "python", local-paths.md) first
   on PATH, dot-sources P/export.ps1 and hands back the environment it
   leaves. Refused (exit 2), with the command not run, if the export fails
   or leaves no IDF_PYTHON_ENV_PATH.
2. The command: [this Python, -B, script] + args, in that environment and
   in the current folder (the runner's cwd: the tree's checkout). Its exit
   code is passed on.
3. The tree: the current folder must be a git checkout with no tracked
   change before the command (else exit 2: nothing could vouch for it
   after). Any tracked file the command changed
   is a FAIL line naming it, and the exit code is 3 if the command's own
   was 0. dependencies.lock above all: under another IDF a configure
   rewrites it and exits 0 (shared/tools R33; the reviewer, 2026-11-04).
   Untracked files that git does not ignore are listed, not failed.

Windows only: the export is ESP-IDF's PowerShell one. Elsewhere refused.
Tests: idf_build_shell_test.py (with --can-fail).
"""

import argparse
import json
import os
import subprocess
import sys

# the export, and the environment it leaves as JSON on the last line. Not
# under $ErrorActionPreference = 'Stop': in Windows PowerShell 5.1 the
# export's own python, writing "Activating ESP-IDF 5.4" to stderr, would
# then be a terminating error (the build smoke of 2026-11-04). A throw in
# the export still ends the command, with no IBS-ENV line.
EXPORT = (
    "$env:IDF_TOOLS_PATH = $env:_IBS_TOOLS; "
    "$env:IDF_PATH = $env:_IBS_IDF; "
    "$env:PATH = $env:_IBS_PY + ';' + $env:PATH; "
    ". (Join-Path $env:IDF_PATH 'export.ps1') *> $null; "
    "$h = @{}; "
    "foreach ($e in Get-ChildItem env:) { $h[$e.Name] = $e.Value }; "
    "Write-Output ('IBS-ENV ' + ($h | ConvertTo-Json -Compress))"
)


def export(idf_path, tools_path, python_dir):
    """The environment the export leaves; SystemExit(2) if it fails."""
    env = dict(os.environ, _IBS_TOOLS=tools_path, _IBS_IDF=idf_path, _IBS_PY=python_dir)
    # ESP-IDF refuses to run under MSYS ("MSys/Mingw is not supported"), and
    # a runner started from Git Bash passes MSYSTEM down: not to the export,
    # and so not to the command either
    env.pop("MSYSTEM", None)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                        "Bypass", "-Command", EXPORT],
                       capture_output=True, text=True, env=env, timeout=600)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("IBS-ENV ")),
                None)
    if r.returncode != 0 or line is None:
        print("idf_build_shell: REFUSED: the ESP-IDF export failed (exit %d): %s"
              % (r.returncode, (r.stderr or r.stdout).strip()[-600:]), flush=True)
        raise SystemExit(2)
    got = json.loads(line[len("IBS-ENV "):])
    for k in ("_IBS_TOOLS", "_IBS_IDF", "_IBS_PY"):
        got.pop(k, None)
    if not got.get("IDF_PYTHON_ENV_PATH"):
        print("idf_build_shell: REFUSED: the export left no IDF_PYTHON_ENV_PATH", flush=True)
        raise SystemExit(2)
    return got


def tracked_changes(folder):
    """([tracked file changed], [untracked file not ignored]) in the git
    checkout at folder; SystemExit(2) if it is not one."""
    r = subprocess.run(["git", "-C", folder, "status", "--porcelain", "--untracked-files=all"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("idf_build_shell: REFUSED: %s is not a git checkout, so nothing can say the "
              "build left it unchanged: %s" % (folder, r.stderr.strip()), flush=True)
        raise SystemExit(2)
    changed, untracked = [], []
    for ln in r.stdout.splitlines():
        if not ln.strip():
            continue
        (untracked if ln.startswith("??") else changed).append(ln[3:])
    return changed, untracked


def main(argv):
    if os.name != "nt":
        print("idf_build_shell: REFUSED: the ESP-IDF export is PowerShell's; Windows only",
              flush=True)
        return 2
    if "--" not in argv:
        print("idf_build_shell: REFUSED: no -- before the command", flush=True)
        return 2
    i = argv.index("--")
    ap = argparse.ArgumentParser(prog="idf_build_shell.py")
    ap.add_argument("--idf-path", required=True)
    ap.add_argument("--idf-tools-path", required=True)
    ap.add_argument("--python-dir", required=True)
    a = ap.parse_args(argv[:i])
    cmd = argv[i + 1:]
    if not cmd:
        print("idf_build_shell: REFUSED: no command after --", flush=True)
        return 2
    here = os.getcwd()
    before, _ = tracked_changes(here)
    if before:
        print("idf_build_shell: REFUSED: the checkout is not clean before the build: %s"
              % ", ".join(before[:5]), flush=True)
        return 2
    env = export(a.idf_path, a.idf_tools_path, a.python_dir)
    print("idf_build_shell: IDF_PATH %s, IDF_TOOLS_PATH %s" % (env.get("IDF_PATH"),
                                                              env.get("IDF_TOOLS_PATH")),
          flush=True)
    rc = subprocess.run([sys.executable, "-B"] + cmd, env=env, cwd=here).returncode
    changed, untracked = tracked_changes(here)
    for f in changed:
        print("FAIL  idf_build_shell: the build changed the tracked file %s" % f, flush=True)
    for f in untracked:
        print("note  idf_build_shell: untracked, not ignored: %s" % f, flush=True)
    if rc == 0 and changed:
        rc = 3
    print("idf_build_shell: exit %d" % rc, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
