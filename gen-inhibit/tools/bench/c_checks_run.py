#!/usr/bin/env python3
"""Baseline part C's scan-scope check (baseline_run_list.md; suite check C2scan): the
firmware clone's test/build_checks/scan_scope_check.py, run under ESP-IDF exported from
IDF_PATH, with the exported environment's Python. The Python port of c_checks_run.ps1
(Windows PowerShell, which madhouse-debian does not have), with the same arguments, file
names and record (gen-inhibit tester, 2026-11-16, the gen-inhibit reviewer's item 1: port
it, with its test; no declared Linux skip).

    python3.13 -B c_checks_run.py --clone <tester worktree>/gen-inhibit --commit <sha> \\
        --components <verified managed_components> --outdir <runs dir>

The environment it needs is the one the .ps1 needed, in Linux form: IDF_PATH (the ESP-IDF
tree) and IDF_TOOLS_PATH (its tools folder; local-paths.md: set before the export). Like
the .ps1, it exports ESP-IDF itself (`. "$IDF_PATH/export.sh"`, in a bash child), so the
chain runner's settings carry IDF_PATH and IDF_TOOLS_PATH in hosts.local.env rather than the
runner being started from an exported shell. scan_scope_check.py then runs as `python -B`
from the exported PATH, as the .ps1 ran it, in the clone's test/build_checks/, with
PYTHONDONTWRITEBYTECODE=1 (charge-interposer tester, 2026-11-16). It runs idf.py itself:
a build-class check (build.slice on madhouse-debian).

Writes <outdir>/scan_scope_check_<commit>.txt, the .ps1's record:
    start <time> scan_scope_check --commit <commit> (clone HEAD <short>)
    <the check's stdout and stderr>
    end <time> exit <code>
and scan_scope_check writes its own <outdir>/scan_scope_check_<commit>.json. The export's
own output, which the .ps1 discarded, goes to scan_scope_check_<commit>_export.txt beside
it, so it can never be read as one of the check's step lines. On stdout: `scan_scope_check
exit <code>`, then `c checks done`.

-Components (here --components) must be a tree whose prod build verified it against
CHECKSUMS.json and the lock (12.3 item 3); this script does not check that.

Exit: 0 when the check ran, whatever its verdict (the verdict is in the .txt, for the
suite's judge; the .ps1 exited 0 here too); 3 when the export failed (the .txt says so, the
check never ran); 2 refused before anything ran (no IDF_PATH, no IDF_TOOLS_PATH, no
export.sh in IDF_PATH, no test/build_checks/scan_scope_check.py in --clone, or not POSIX:
export.sh is a POSIX shell script), with nothing written.
"""
import argparse
import os
import subprocess
import sys
import time

CHECKS = ("scan_scope_check",)      # the .ps1's list: C5 and C6 retired at port step 0c (user, 2026-10-25)
BASH = "/bin/bash"
# the export's output goes to its own file; then the check, as `python -B`, from the exported PATH
EXPORT_THEN_RUN = ('. "$IDF_PATH/export.sh" > "$GI_EXPORT_LOG" 2>&1 || '
                   '{ rc=$?; echo "export.sh failed (exit $rc): the check did not run; its output is in '
                   '$(basename "$GI_EXPORT_LOG")"; exit 199; }; exec python -B "$@"')
EXPORT_FAILED = 199

REFUSED = 2
EXPORT_FAIL = 3


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def refusal(a, env):
    """Why nothing may run, or None."""
    if os.name != "posix":
        return "export.sh is a POSIX shell script; run on madhouse-debian (c_checks_run.ps1 is retired)"
    idf = env.get("IDF_PATH")
    if not idf:
        return "IDF_PATH is not set: the ESP-IDF tree (local-paths.md, the settings' hosts.local.env)"
    if not env.get("IDF_TOOLS_PATH"):
        return "IDF_TOOLS_PATH is not set: the ESP-IDF install's tools folder, set before the export (local-paths.md)"
    if not os.path.isfile(os.path.join(idf, "export.sh")):
        return "no export.sh in IDF_PATH (%s)" % idf
    for c in CHECKS:
        if not os.path.isfile(os.path.join(a.clone, "test", "build_checks", c + ".py")):
            return "no test/build_checks/%s.py in --clone (%s): it is the project folder, <worktree>/gen-inhibit" % (c, a.clone)
    return None


def run_check(c, a, outdir, env, say):
    """One check; its exit code (EXPORT_FAILED when the export failed)."""
    txt = os.path.join(outdir, "%s_%s.txt" % (c, a.commit))
    jsn = os.path.join(outdir, "%s_%s.json" % (c, a.commit))
    head = subprocess.run(["git", "-C", a.clone, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    checks = os.path.join(a.clone, "test", "build_checks")
    cenv = dict(env, PYTHONDONTWRITEBYTECODE="1",
                GI_EXPORT_LOG=os.path.join(outdir, "%s_%s_export.txt" % (c, a.commit)))
    argv = [BASH, "-c", EXPORT_THEN_RUN, "c_checks_run",
            c + ".py", "--commit", a.commit, "--components", a.components, "--out", jsn]
    with open(txt, "w", encoding="utf-8", newline="\n") as f:
        f.write("start %s %s --commit %s (clone HEAD %s)\n" % (stamp(), c, a.commit, head))
        f.flush()
        try:
            code = subprocess.run(argv, cwd=checks, env=cenv, stdout=f, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL).returncode
        except OSError as e:
            f.write("could not start %s: %s\n" % (BASH, e))
            code = EXPORT_FAILED
        f.flush()
        f.write("end %s exit %s\n" % (stamp(), code))
    say("%s exit %s" % (c, code))
    return code


def main(argv=None, say=print, env=None):
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser(description="baseline part C's scan-scope check (C2scan), Linux")
    ap.add_argument("--clone", required=True, help="the project folder: <tester worktree>/gen-inhibit")
    ap.add_argument("--commit", required=True, help="the commit under test (names the files)")
    ap.add_argument("--components", required=True, help="a managed_components tree its prod build verified")
    ap.add_argument("--outdir", required=True, help="the run folder (created when missing)")
    a = ap.parse_args(argv)
    a.clone = os.path.abspath(a.clone)
    a.components = os.path.abspath(a.components)       # the check runs in the clone's folder
    why = refusal(a, env)
    if why:
        say("refused: %s; nothing run" % why)
        return REFUSED
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)
    codes = [run_check(c, a, outdir, env, say) for c in CHECKS]
    say("c checks done")
    return EXPORT_FAIL if EXPORT_FAILED in codes else 0


if __name__ == "__main__":
    sys.exit(main())
