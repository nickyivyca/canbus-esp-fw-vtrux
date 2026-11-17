#!/usr/bin/env python3
"""Part C's and the bench suite's images on Linux: clean builds of one commit, one build
directory each, in a tree nobody else uses. The Linux port of baseline_builds.ps1 (Windows
PowerShell, which madhouse-debian does not have), with the same names, defines, steps,
logs and exit codes (gen-inhibit tester, 2026-11-16, the gen-inhibit reviewer's item 2: a
Linux build route for the bench variants, built by the tester).

Run from a shell exported for ESP-IDF v5.4.2 (local-paths.md: IDF_TOOLS_PATH first, then
export.sh), in build.slice on madhouse-debian (AGENTS.md "Parallel work on the shared
machine"):

    export IDF_TOOLS_PATH="$HOME/.espressif-5.4.2"; . ~/esp/esp-idf-v5.4.2/export.sh
    systemd-run --user --scope --quiet --slice=build.slice -- \\
        python3.13 -B gen-inhibit/tools/bench/baseline_builds.py \\
        --tree <WiCAN tree: a worktree's gen-inhibit/firmware> --out <log dir> \\
        (--builds man,rt,rxq | --bench)

--bench is the bench suite's set: the build-tester-<name> folders of
suite_gen_inhibit.WICAN_BUILDS (man, rt, txabort, abortwin, rxq). The suite's sixth WiCAN
image, wican_prod, is the shared tooling's release build (gen-inhibit/vtrux_tools.py
release), not this.

Each build, as the .ps1 does it, logged to <out>/build_<name>.txt:
  - a build directory that already exists is refused rather than reused (each must be a
    clean build);
  - git status and HEAD of the tree before;
  - check-env (shared/tools R32; user, 2026-11-03: every gen-inhibit build passes the
    check), `<this python> -B gen-inhibit/vtrux_tools.py check-env`, the tree's own
    launcher (beside --tree, so the commit under build's), run from that worktree's root
    with this shell's environment as it is; its output and exit. If it exits non-zero, or
    the launcher is not there, the build is refused: no build directory, idf.py never run;
  - the idf.py command (`idf.py -B <tree>/build-tester-<name> <defines> build`, run in the
    tree, idf.py from the exported shell's PATH), its full output, start and end times and
    seconds, and its exit;
  - (added here) git status of the tree after the build.

Exit: 0 every build passed; 3 a check-env refusal (a later failure does not hide it); else
2 an existing build directory refused; else 1 a failed build. Unknown names, an empty list,
a --tree that is not a gen-inhibit/firmware folder, or a shell not exported for ESP-IDF
(IDF_PATH unset) are refused before anything runs, exit 2. (The .ps1 refused an unknown
name in the loop and went on; here nothing is built when any name is unknown.)
"""
import argparse
import os
import subprocess
import sys
import time

# name -> idf.py defines; the .ps1's $defs table, the same names and switches
DEFS = {
    "man": [],
    "man2": [],
    # rt and txabort: vtrux/restart_guard's and vtrux/twai_port's switches since port step 1c
    "rt": ["-DRESTART_GUARD_TEST=1"],
    "rxq": ["-DGI_INSTRUMENT_RXQ=1"],
    "txabort": ["-DTWAI_PORT_TXABORT_PROBE=1"],
    "abortwin": ["-DGI_INSTRUMENT_ABORTWIN=1"],
    "flt": ["-DOTA_HEALTH_FAULT_INJECT=1"],
    # Spec 3.2 / 12.3 item 3: the stock features, off in this product, each built ON for its
    # refusal row's failing half. AutoPID needs ELM327 and MQTT, so its pair is fautopid vs fmqttelm.
    "fmqtt": ["-DWICAN_FEATURE_MQTT=ON"],
    "felm": ["-DWICAN_FEATURE_ELM327=ON"],
    "fmqttelm": ["-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON"],
    "fautopid": ["-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON", "-DWICAN_FEATURE_AUTOPID=ON"],
    "frealdash": ["-DWICAN_FEATURE_REALDASH=ON"],
    "fall": ["-DWICAN_FEATURE_MQTT=ON", "-DWICAN_FEATURE_ELM327=ON", "-DWICAN_FEATURE_AUTOPID=ON",
             "-DWICAN_FEATURE_REALDASH=ON"],
    # vtrux/wican's fifth stock switch, the SLCAN command parser (shared/wican W6; known anomaly 8)
    "fslcan": ["-DWICAN_SLCAN_COMMANDS=ON"],
}
# the bench suite's WiCAN images (suite_gen_inhibit.WICAN_BUILDS' folders); its test checks
# the two agree
BENCH = ("man", "rt", "txabort", "abortwin", "rxq")
IDF = "idf.py"

REFUSED_ENV = 3
REFUSED = 2
FAILED = 1


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def git(tree, *args):
    p = subprocess.run(["git", "-C", tree] + list(args), capture_output=True, text=True)
    return p.stdout.strip()


def run_logged(argv, cwd, log):
    """argv in cwd, its stdout and stderr appended to the open log; its exit code, or None
    when it could not be started (the reason logged)."""
    log.flush()
    try:
        return subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL).returncode
    except OSError as e:
        log.write("could not start %s: %s\n" % (argv[0], e))
        return None


def build_one(name, tree, out, launcher, root, say):
    """One build; its outcome: 0, REFUSED_ENV, REFUSED or FAILED."""
    d = os.path.join(tree, "build-tester-" + name)
    logp = os.path.join(out, "build_%s.txt" % name)
    if os.path.exists(d):
        say("%s : %s exists, refused" % (name, d))
        return REFUSED
    with open(logp, "w", encoding="utf-8", newline="\n") as log:
        st = "; ".join(git(tree, "status", "--porcelain").splitlines())
        log.write("git status before: [%s] HEAD %s\n" % (st, git(tree, "rev-parse", "HEAD")))
        env_cmd = [sys.executable, "-B", launcher, "check-env"]
        log.write("check-env: %s (in %s)\n" % (" ".join(env_cmd), root))
        if not os.path.isfile(launcher):
            log.write("check-env: no launcher at %s; refused, nothing built\n" % launcher)
            say("%s : no launcher at %s, check-env refused, nothing built" % (name, launcher))
            return REFUSED_ENV
        envrc = run_logged(env_cmd, root, log)
        log.write("check-env exit: %s\n" % envrc)
        if envrc != 0:
            log.write("check-env refused; nothing built\n")
            say("%s : check-env refused (exit %s), nothing built" % (name, envrc))
            return REFUSED_ENV
        cmd = [IDF, "-B", d] + DEFS[name] + ["build"]
        log.write("command: %s\n" % " ".join(cmd))
        t0 = time.time()
        log.write("start: %s\n" % stamp())
        code = run_logged(cmd, tree, log)
        secs = int(time.time() - t0)
        log.write("end: %s  seconds %d\n" % (stamp(), secs))
        log.write("idf exit: %s\n" % code)
        st = "; ".join(git(tree, "status", "--porcelain").splitlines())
        log.write("git status after: [%s]\n" % st)
    say("%s: exit %s, %d s" % (name, code, secs))
    return 0 if code == 0 else FAILED


def main(argv=None, say=print):
    ap = argparse.ArgumentParser(description="clean builds of named variants of one tree (Linux)")
    ap.add_argument("--tree", required=True, help="the WiCAN tree: a worktree's gen-inhibit/firmware")
    ap.add_argument("--out", required=True, help="the log folder (build_<name>.txt)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--builds", help="names, comma-separated: " + ",".join(DEFS))
    g.add_argument("--bench", action="store_true", help="the bench suite's set: " + ",".join(BENCH))
    a = ap.parse_args(argv)
    names = list(BENCH) if a.bench else [n for n in a.builds.split(",") if n]
    bad = [n for n in names if n not in DEFS]
    if not names or bad:
        say("refused: %s; nothing built (names: %s)" % (
            "unknown build %s" % ", ".join(bad) if bad else "no build named", ", ".join(DEFS)))
        return REFUSED
    if not os.environ.get("IDF_PATH"):
        say("refused: IDF_PATH is not set; run from a shell exported for ESP-IDF v5.4.2 "
            "(local-paths.md); nothing built")
        return REFUSED
    tree = os.path.abspath(a.tree)
    if os.path.basename(tree) != "firmware" or os.path.basename(os.path.dirname(tree)) != "gen-inhibit" \
            or not os.path.isfile(os.path.join(tree, "CMakeLists.txt")):
        say("refused: %s is not a worktree's gen-inhibit/firmware; nothing built" % tree)
        return REFUSED
    launcher = os.path.join(os.path.dirname(tree), "vtrux_tools.py")
    root = os.path.dirname(os.path.dirname(tree))
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    got = [build_one(n, tree, out, launcher, root, say) for n in names]
    for rc in (REFUSED_ENV, REFUSED, FAILED):
        if rc in got:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
