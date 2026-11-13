#!/usr/bin/env python3
"""The interposer's release stand-in, R19 pair and R20 refusal pairs on real
builds, from one product commit (port step 2a; the charge-interposer
reviewer's acceptance of the plan and its conditions, 2026-11-03).

    py -3.14 -B charge-interposer/fw_harness/refusal_pairs_runs.py \
        --commit <product commit> --out <new folder> [--jobs N]
        [--scratch-from DIR]

Run from this worktree's root, in a PowerShell exported for the interposer's
ESP-IDF (local-paths.md), and only when no wall-clock test of either project
is running on any machine whose builds it would disturb (AGENTS.md "Builds
during wall-clock tests": until the interposer's IDF build passes the trial,
interposer builds wait for every wall-clock test). It refuses while the bench
lock is held, "No builds" is set, or a simulator runs here
(scratch_dir.wallclock_clear); a run on madhouse is the caller's to check.

The harness is this worktree's, which must be clean: its commit is recorded
as the harness commit. Every build is of --commit, in worktrees made for this
run beside this one, detached, and removed with `git worktree remove` (and
prune) when the run ends, pass or fail; a leftover is reported:

  interposer-tester-standin    the release stand-in: vtrux_tools.py release
                               (interposer_truck, the CMake default), its
                               record written under <out>/rid-standin, never
                               the real firmware/builds/; then shared/tools'
                               switches, pins, board, options and scope
                               --release on its build folder, each to pass
  interposer-tester-r19        the same release again (R19): its image hash
                               must equal the stand-in's
  interposer-tester-relbridge  the FIXTURE: --commit plus one line, the CMake
                               default variant bridge for truck, committed
                               there by this script (never by hand, never on
                               a branch, never pushed), then released; its
                               diff against --commit must be exactly that one
                               line, or nothing is built
  interposer-tester-pairsdev   the dev builds (vtrux_tools.py build): bridge,
                               witness, selftest, diag; and trace,
                               bridge with -D CMAKE_CXX_FLAGS=-DINTP_TX_TRACE=1
                               and -D CMAKE_EXE_LINKER_FLAGS=-Wl,--noinhibit-exec,
                               whose link must report "overflowed by" (DRAM:
                               no runnable image, but an ELF and a .bin)

Dev builds have a worktree of their own because a release builds in
firmware/build/ itself, and shared/tools' checks read every CMake build
folder under it.

With --scratch-from DIR the worktrees go in a folder of the scratch/ of the
worktree holding DIR instead (scratch_dir), with a read-only checkout
shared-<tag> beside them for each tag the product commit's
shared_pins.json pins, which their vtrux_tools.py needs. An acceptance list
passes its {clone}: beside a runner's tree the build folders are too deep
for ESP-IDF on Windows. Either way a build folder over MAX_BUILD_DIR
characters is refused before anything is made (the charge-interposer
reviewer, 2026-11-04). The worktrees, the checkouts and the folder are
removed at the end.

Then refusal_pairs.py (this harness's) on them: the positive run (OFF the
stand-in, every ON label) must PASS, and each negative must FAIL on its own
check:
  the bridge build as OFF          -> "OFF is interposer_truck"
  the stand-in as witness's ON     -> witness's ELF-symbol rule
  diag left out                    -> "ON build diag given"
  the bridge build without its ELF -> "ON bridge read"

<out> gets record.txt (the product and harness commits, the fixture's sha
and its diff, each build's exit and image hash, each check's result, the
summary) and logs/, one file per step. The stand-in's release record and its
.bin/.elf stay in <out>/rid-standin/firmware/builds/. Exit 0 only if
everything held.

The fixture's diff check and its edit are refusal_pairs_test.py's to test.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

CMAKE_REL = "charge-interposer/firmware/CMakeLists.txt"
FIXTURE_OLD = 'set(INTP_VARIANT "truck" CACHE STRING'
FIXTURE_NEW = 'set(INTP_VARIANT "bridge" CACHE STRING'
WORKTREES = ("standin", "r19", "relbridge", "pairsdev")
DEV = ("bridge", "witness", "selftest", "diag")
CHECKS = (("switches", []), ("pins", []), ("board", []), ("options", []),
          ("scope", ["--release"]))
OVERFLOW = "overflowed by"
PINS_REL = "charge-interposer/firmware/shared_pins.json"
# The deepest build folder ESP-IDF is let run in (no_led.MAX_BUILD_DIR's
# reason: deeper, on Windows, idf.py's cmake fails with WinError 267, build
# smoke 5, 2026-11-04)
MAX_BUILD_DIR = 200


# ---- where the worktrees go (pure; refusal_pairs_test.py tests these) ------
def worktree_paths(top, base=None):
    """The run's worktrees: beside this worktree (top), named as the
    project's extra worktrees are (interposer-tester-<purpose>), or in base
    by their bare names, which keeps the build folders shallow."""
    if base:
        return {k: os.path.join(base, k) for k in WORKTREES}
    return {k: os.path.join(os.path.dirname(top), "interposer-tester-" + k) for k in WORKTREES}


def build_dirs(paths):
    """Every build folder the run makes: each release's firmware/build, and
    the dev builds' and the trace build's firmware/build/<variant>."""
    out = [os.path.join(paths[k], "charge-interposer", "firmware", "build")
           for k in ("standin", "r19", "relbridge")]
    out += [os.path.join(paths["pairsdev"], "charge-interposer", "firmware", "build", v)
            for v in DEV + ("trace",)]
    return out


def too_deep(paths, limit=MAX_BUILD_DIR):
    """The build folders longer than limit characters."""
    return [p for p in build_dirs(paths) if len(os.path.abspath(p)) > limit]


def shared_tags(pins_text):
    """The distinct tags a shared_pins.json pins, sorted: one read-only
    checkout shared-<tag> each, which a worktree's vtrux_tools.py looks for
    beside it."""
    import json
    return sorted({v["tag"] for v in json.loads(pins_text).values()})


# ---- the fixture (pure; refusal_pairs_test.py tests these) ----------------
def fixture_text(cmake_text):
    """The fixture's CMakeLists.txt: the default line, truck -> bridge.
    ValueError unless that line is there exactly once."""
    n = cmake_text.count(FIXTURE_OLD)
    if n != 1:
        raise ValueError("%r occurs %d times in %s, not once"
                         % (FIXTURE_OLD, n, CMAKE_REL))
    return cmake_text.replace(FIXTURE_OLD, FIXTURE_NEW)


def fixture_diff_problems(diff):
    """[] when `git diff --unified=0` of the product commit against the
    fixture is exactly the one default line in CMAKE_REL; otherwise what is
    wrong."""
    files = re.findall(r"^diff --git a/(\S+) b/(\S+)$", diff, re.M)
    out = []
    if files != [(CMAKE_REL, CMAKE_REL)]:
        out.append("the diff names %s, not %s alone" % (files, CMAKE_REL))
    body = [ln for ln in diff.splitlines()
            if ln[:1] in "+-" and not ln.startswith(("+++ ", "--- "))]
    if body != ["-" + FIXTURE_OLD, "+" + FIXTURE_NEW]:
        out.append("the changed lines are %r, not the default line alone"
                   % body[:6])
    if "new file mode" in diff or "deleted file mode" in diff or \
            re.search(r"^old mode|^new mode", diff, re.M):
        out.append("the diff changes a file's mode or adds or removes one")
    return out


# ---- running ---------------------------------------------------------------
class Run:
    def __init__(self, out):
        self.out = out
        self.logs = os.path.join(out, "logs")
        os.makedirs(self.logs)
        self.lines = []

    def note(self, text):
        print(text, flush=True)
        self.lines.append(text)

    def sh(self, name, argv, cwd=None, env=None, timeout=4 * 3600):
        """(rc, text) of argv, its output in logs/<name>.log."""
        t0 = time.time()
        try:
            r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                               text=True, errors="replace", timeout=timeout)
            rc, text = r.returncode, r.stdout + r.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, text = None, "%s: %s" % (type(e).__name__, e)
        with open(os.path.join(self.logs, name + ".log"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("$ %s\n(cwd %s)\n\n%s\n[exit %s, %.0f s]\n"
                    % (" ".join(argv), cwd, text, rc, time.time() - t0))
        return rc, text

    def write(self):
        with open(os.path.join(self.out, "record.txt"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("\n".join(self.lines) + "\n")


def git(*args, cwd=None):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip() + (("\n" + r.stderr.strip())
                                             if r.returncode else "")


def remove_worktrees(top, paths, note):
    left = []
    for k, p in paths.items():
        if os.path.exists(p):
            rc, out = git("worktree", "remove", "--force", p, cwd=top)
            if rc != 0 or os.path.exists(p):
                left.append("%s (%s)" % (p, out.splitlines()[-1] if out else "rc %s" % rc))
    git("worktree", "prune", cwd=top)
    for p in left:
        note("LEFTOVER worktree %s" % p)
    return left


def release(run, name, wt, rid, jobs):
    """(ok, image hash, build folder) of vtrux_tools.py release in wt, its
    record under rid."""
    os.makedirs(os.path.join(rid, "firmware"))
    env = dict(os.environ, REVERSE_IT_DIR=rid)
    argv = [sys.executable, "-B", os.path.join(wt, "charge-interposer", "vtrux_tools.py"),
            "release"] + (["--jobs", str(jobs)] if jobs else [])
    rc, text = run.sh("release-" + name, argv, cwd=wt, env=env)
    m = re.search(r"image hash ([0-9a-f]{64})", text)
    rec = re.search(r"RELEASE BUILD RECORDED: (.+)", text)
    ok = rc == 0 and m is not None and rec is not None
    run.note("release %-9s exit %s, image hash %s%s" % (
        name, rc, m.group(1) if m else "(none)",
        "" if ok else "  FAIL (logs/release-%s.log)" % name))
    return ok, (m.group(1) if m else None), os.path.join(wt, "charge-interposer", "firmware", "build")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--scratch-from", metavar="DIR",
                    help="make the worktrees, and the shared-<tag> checkouts they need, "
                    "in the scratch/ of the worktree holding DIR, not beside this one")
    a = ap.parse_args(argv)

    import vtrux_tools
    import scratch_dir
    vtrux_tools.check_idf_env()
    clear, why = scratch_dir.wallclock_clear()
    if not clear:
        raise SystemExit("refusal_pairs_runs: not now: %s" % "; ".join(why))
    rc, top = git("rev-parse", "--show-toplevel", cwd=CODE)
    rc2, harness = git("rev-parse", "HEAD", cwd=top)
    rc3, dirty = git("status", "--porcelain", cwd=top)
    if rc or rc2 or rc3 or dirty:
        raise SystemExit("refusal_pairs_runs: this worktree is not a clean git "
                         "worktree; the harness must be committed:\n%s" % dirty)
    rc, commit = git("rev-parse", "--verify", a.commit + "^{commit}", cwd=top)
    if rc:
        raise SystemExit("refusal_pairs_runs: no commit %r" % a.commit)
    if os.path.exists(a.out):
        raise SystemExit("refusal_pairs_runs: %s exists" % a.out)
    work, shared = None, {}
    if a.scratch_from:
        rc, pins = git("show", "%s:%s" % (commit, PINS_REL), cwd=top)
        if rc:
            raise SystemExit("refusal_pairs_runs: no %s at %s" % (PINS_REL, commit[:12]))
        work = scratch_dir.make("refusal-pairs", a.scratch_from)
        paths = worktree_paths(top, work)
        shared = {"shared-" + t: os.path.join(work, "shared-" + t) for t in shared_tags(pins)}
    else:
        paths = worktree_paths(top)
    deep = too_deep(paths)
    if deep:
        if work:
            scratch_dir.remove(work)
        raise SystemExit("refusal_pairs_runs: a build folder would be %d characters long "
                         "(%s), over %d: ESP-IDF cannot run a build that deep on Windows. "
                         "Give --scratch-from a shallower worktree (a list's {clone})"
                         % (len(os.path.abspath(deep[0])), deep[0], MAX_BUILD_DIR))
    there = [p for p in paths.values() if os.path.exists(p)]
    if there:
        raise SystemExit("refusal_pairs_runs: already there: %s" % there)
    os.makedirs(a.out)
    run = Run(a.out)
    run.note("refusal_pairs_runs %s" % time.strftime("%Y-%m-%d %H:%M:%S %z"))
    run.note("product commit %s" % commit)
    run.note("harness commit %s (this worktree, clean)" % harness)
    run.note("jobs %s" % (a.jobs or "(the build's default)"))
    if work:
        run.note("worktrees in %s, with the product commit's shared checkouts %s"
                 % (work, ", ".join(sorted(shared))))
    bad = []
    try:
        for name, p in sorted(shared.items()):
            tag = name[len("shared-"):]
            rc, out = git("worktree", "add", "--detach", p, tag, cwd=top)
            if rc:
                raise RuntimeError("git worktree add %s %s: %s" % (p, tag, out))
        for k, p in paths.items():
            rc, out = git("worktree", "add", "--detach", p, commit, cwd=top)
            if rc:
                raise RuntimeError("git worktree add %s: %s" % (p, out))
        # the fixture, made here every run, checked before anything builds
        rb = paths["relbridge"]
        cm = os.path.join(rb, *CMAKE_REL.split("/"))
        with open(cm, encoding="utf-8", newline="") as f:
            text = f.read()
        with open(cm, "w", encoding="utf-8", newline="") as f:
            f.write(fixture_text(text))
        rc, out = git("commit", "-q", "-a", "-m",
                      "fixture (refusal_pairs_runs, never on a branch): "
                      "INTP_VARIANT default bridge, for the R20 name-token "
                      "refusal of a release that is not truck", cwd=rb)
        if rc:
            raise RuntimeError("the fixture commit: %s" % out)
        _rc, fixture = git("rev-parse", "HEAD", cwd=rb)
        _rc, diff = git("diff", "--unified=0", "--no-color", "--no-ext-diff",
                        commit, fixture, cwd=rb)
        probs = fixture_diff_problems(diff)
        run.note("fixture %s (derived: %s + one line)" % (fixture, commit[:12]))
        run.note("fixture diff:\n" + "\n".join("    " + ln for ln in diff.splitlines()))
        if probs:
            raise RuntimeError("the fixture's diff is not the one line: %s" % probs)

        # releases
        ok_s, h_s, b_s = release(run, "standin", paths["standin"],
                                 os.path.join(a.out, "rid-standin"), a.jobs)
        ok_r, h_r, _b = release(run, "r19", paths["r19"],
                                os.path.join(a.out, "rid-r19"), a.jobs)
        ok_b, _h, b_b = release(run, "relbridge", paths["relbridge"],
                                os.path.join(a.out, "rid-relbridge"), a.jobs)
        for name, ok in (("standin", ok_s), ("r19", ok_r), ("relbridge", ok_b)):
            if not ok:
                bad.append("release " + name)
        r19 = ok_s and ok_r and h_s == h_r
        run.note("R19 %s: stand-in %s, second %s" % ("PASS" if r19 else "FAIL", h_s, h_r))
        if not r19:
            bad.append("R19")

        # shared/tools' checks on the stand-in
        tools = vtrux_tools.tools_dir()
        env = dict(os.environ, PYTHONPATH=tools)
        if ok_s:
            for cmd, extra in CHECKS:
                rc, _t = run.sh("check-" + cmd, [sys.executable, "-B", "-m", "vtrux_checks",
                                                 cmd, "--build", b_s] + extra, env=env)
                run.note("check %-8s on the stand-in: exit %s%s" % (cmd, rc, "" if rc == 0 else "  FAIL"))
                if rc != 0:
                    bad.append("check " + cmd)

        # dev builds
        pd = paths["pairsdev"]
        fw = os.path.join(pd, "charge-interposer", "firmware")
        dev = {}
        for v in DEV:
            rc, _t = run.sh("build-" + v, [sys.executable, "-B", os.path.join(
                pd, "charge-interposer", "vtrux_tools.py"), "build", v]
                + (["--jobs", str(a.jobs)] if a.jobs else []), cwd=pd)
            run.note("dev build %-9s exit %s%s" % (v, rc, "" if rc == 0 else "  FAIL"))
            if rc != 0:
                bad.append("build " + v)
            dev[v] = os.path.join(fw, "build", v)
        py = os.path.join(os.environ["IDF_PYTHON_ENV_PATH"],
                          "Scripts" if os.name == "nt" else "bin",
                          "python.exe" if os.name == "nt" else "python")
        idf_py = os.path.join(os.environ["IDF_PATH"], "tools", "idf.py")
        tb = os.path.join(fw, "build", "trace")
        rc, _t = run.sh("build-trace-reconfigure", [
            py, idf_py, "-C", fw, "-B", tb, "-D", "INTP_VARIANT=bridge",
            "-D", "CMAKE_CXX_FLAGS=-DINTP_TX_TRACE=1",
            "-D", "CMAKE_EXE_LINKER_FLAGS=-Wl,--noinhibit-exec", "reconfigure"], cwd=pd)
        rc2, t2 = run.sh("build-trace", [shutil.which("ninja") or "ninja", "-C", tb]
                         + (["-j", str(a.jobs)] if a.jobs else []) + ["all"], cwd=pd)
        over = OVERFLOW in t2
        run.note("dev build trace     reconfigure exit %s, ninja exit %s; %r in the link: %s"
                 % (rc, rc2, OVERFLOW, "yes" if over else "NO  FAIL"))
        if rc != 0 or not over:
            bad.append("build trace")
        dev["trace"] = tb

        # the judge: positive, then each negative on its own check
        judge = os.path.join(HERE, "refusal_pairs.py")

        def pairs(name, off, ons):
            argv = [sys.executable, "-B", judge, "--off", off]
            for lb, p in sorted(ons.items()):
                argv += ["--on", "%s=%s" % (lb, p)]
            return run.sh("pairs-" + name, argv)

        ons = dict(dev, **{"bridge-release": b_b})
        rc, text = pairs("positive", b_s, ons)
        pos = rc == 0 and text.rstrip().endswith("PASS")
        run.note("refusal pairs, positive: %s" % ("PASS" if pos else "FAIL  (logs/pairs-positive.log)"))
        if not pos:
            bad.append("pairs positive")
        elf = None
        negatives = [
            ("bridge-as-off", "the bridge build as OFF", dev["bridge"], ons,
             "OFF is interposer_truck"),
            ("truck-as-witness", "the stand-in as witness's ON", b_s,
             dict(ons, witness=b_s), "ON witness: INTP_ORDER_WITNESS rule elf_symbols_absent fails"),
            ("diag-left-out", "diag left out", b_s,
             {k: v for k, v in ons.items() if k != "diag"}, "ON build diag given"),
            ("bridge-no-elf", "the bridge build without its ELF", b_s, ons, "ON bridge read"),
        ]
        for key, what, off, o, want in negatives:
            moved = None
            if key == "bridge-no-elf":
                elf = os.path.join(dev["bridge"], "interposer_bridge.elf")
                moved = elf + ".refusal-pairs-away"
                os.replace(elf, moved)
            try:
                rc, text = pairs(key, off, o)
            finally:
                if moved:
                    os.replace(moved, elf)
            hit = any(ln.strip().startswith("FAIL") and want in ln for ln in text.splitlines())
            caught = rc != 0 and hit
            run.note("refusal pairs, negative %s: %s on %r" % (
                what, "CAUGHT" if caught else "NOT CAUGHT  FAIL", want))
            if not caught:
                bad.append("negative " + key)
    except Exception as e:                           # noqa: BLE001
        run.note("STOPPED: %s: %s" % (type(e).__name__, e))
        bad.append("stopped")
    finally:
        left = remove_worktrees(top, dict(paths, **shared), run.note)
        if work:
            try:
                scratch_dir.remove(work)
            except Exception as e:                   # noqa: BLE001
                left.append(work)
                run.note("LEFTOVER %s: %s" % (work, e))
        if left:
            bad.append("leftover worktrees")
        run.note("SUMMARY: %s" % ("PASS" if not bad else "FAIL: " + ", ".join(bad)))
        run.write()
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
