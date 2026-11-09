#!/usr/bin/env python3
"""Tests scratch_run.sh, the bash scripts' run folders, and every bash
script of the harness that takes its folder from it.

Charge-interposer tester, 2026-11-02 (the charge-interposer reviewer's item
(B): the harness's bash scripts wrote to the system temp folder, two of
them to one fixed folder that two runs at once shared; AGENTS.md "Files and
folders"). Everything runs in a copy of the code under this test's own
scratch_dir run folder, in a stand-in worktree (a folder with a .git
file), so nothing outside it is touched.

The helper, on a fixture script that sources it:
  1. The run folder is <worktree>/scratch/cih/<tool>/<pid>-XXXXXX (the
     short names, 2026-11-05), with an owner file scratch_dir reads: its argv,
     host, platform and pid as Python names them, and on Linux the start
     time scratch_dir.proc() gives (null on Windows); scratch_dir.state()
     is live while the script runs. When it exits the folder goes, and the
     tool folder with it; the exit code is the script's own, from inside
     the folder too; the system temp folder stays empty.
  2. A killed script's folder stays, finished, and the sweep lists it.
  3. Read-only files and folders are removed.
  4. A removal that fails twice is tried again after 0.1 s and 0.4 s and
     succeeds; one that keeps failing leaves the folder, printed as a
     LEFTOVER line, with the exit code unchanged.
  5. The tool folder taken by another run's cleanup between making it and
     the run folder (twice) costs two more tries, after 0.1 s and 0.4 s,
     printing nothing; lost three times it is a refusal (rc 2) with the
     last try's reason printed, and nothing is made.
  6. A tool folder holding another live run's folder stays.
  7. A tool name that is not one plain folder name, and a second run
     folder in one script, are refused.
  8. The root: the nearest .git above the helper, else scratch/ beside
     the code folder.
 12. The length guard (the charge-interposer reviewer, 2026-11-05):
     SCRATCH_RUN_LIMIT and SCRATCH_RUN_DEPTH are scratch_dir.py's LIMIT and
     DEPTH. With the limit moved to put the run folder's full path plus
     the depth exactly on it, the folder is made; one character less and
     scratch_run refuses (rc 2), naming the full path and its length, with
     nothing made.
 13. The git ceiling (the charge-interposer reviewer, 2026-11-05): in a
     stand-in worktree that is a real git repo, git run in a subfolder of
     the run folder says "not a git repository"; without
     GIT_CEILING_DIRECTORIES (the control) it finds that repo.

The writers (WRITERS below):
  9. No .sh under charge-interposer/ names TMPDIR or /tmp outside a
     comment, and only scratch_run.sh and host_bridge/run.sh (its own
     three tries, tested by twai_fake_option_test.py) call mktemp; every
     writer sources scratch_run.sh and takes a run folder under its own
     tool name. The scan is shown to find a planted line.
 10. Each writer is run, with stand-in g++ and python3 (no hardware, no
     real build; gcc too, and a stand-in TWAI_FAKE_DIR for the host_bridge
     builders since port step 2b) and an empty system temp folder: one run
     folder made, for its tool, every g++ and gcc output inside it, nothing
     in the temp folder,
     nothing left once it exits. stop_all.sh runs (--self-test) only on
     Linux; elsewhere it refuses before making anything, which is checked.

The test itself:
 11. When every case has run, no process whose command line names a path
     under the test's folder is left running; any that is, is killed. The
     scan is shown to see a planted loop first. (Added 2026-11-04: a
     case_killed fixture, never told to go when the case failed before its
     try (under the "no owner file" edit), looped on madhouse-debian for
     up to a day: four of them, 2026-11-02/03. The fixture's wait is now
     bounded too, at 120 s.)

    py -3.14 -B charge-interposer/scratch_run_test.py [--can-fail]

--can-fail runs the test on copies of the code with one edit each
(SCRATCH_RUN_CODE names the copy's charge-interposer/), after the unedited
copy passes there, and passes only if every edit is caught. An edit marked
for one platform is run only there: its line does nothing on the other.
After each copy's run it also looks, from outside, for a process left
under that run's own folder, kills it, and counts the edit as a failure
(the same scan as check 11). Its control: the unedited copy's run leaves a
bounded loop naming its own run folder (scratch_dir's naming), which that
scan must see.

On Windows bash is Git Bash by its full path (a bare "bash" there is WSL's);
elsewhere bash. Git Bash puts its own folders first on PATH, so the
stand-ins for mktemp, sleep and rm are functions read from BASH_ENV; g++
and python3 are files first on PATH, as in twai_fake_option_test.py.
"""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                    # noqa: E402

CODE = os.environ.get("SCRATCH_RUN_CODE") or HERE
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
WIN = os.name == "nt"

# the harness's bash scripts that take their folder from scratch_run.sh:
# path under charge-interposer/ -> (tool, args)
WRITERS = {
    "harness_checks/busoff_mutations.sh": ("busoff-mutations", []),
    "harness_checks/conformance_mutations.sh": ("conformance-mutations", []),
    "harness_checks/hold_cadence_gate_mutation.sh": ("hold-cadence-gate-mutation", []),
    "harness_checks/hold_mutations.sh": ("hold-mutations", []),
    "harness_checks/l2_mutations.sh": ("l2-mutations", []),
    "harness_checks/rx_hook_mutations.sh": ("rx-hook-mutations", []),
    "harness_checks/rx_order_prefix_control.sh": ("rx-order-prefix-control", []),
    "harness_checks/tx_fail_banking_mutation.sh": ("tx-fail-banking-mutation", []),
    "host_unit/run.sh": ("host-unit", []),
    "fw_harness/host_diff/diff_all.sh": ("host-diff", []),
    "fw_harness/host_diff/diff_one.sh": ("host-diff-one", ["{trace}"]),
    "stop_all.sh": ("stop-all", ["--self-test"]),
}
# the only .sh files that may call mktemp
MKTEMP_OK = {"scratch_run.sh", "fw_harness/host_bridge/run.sh"}
SKIP_DIRS = {"scratch", "__pycache__", ".pio", "build"}

fails = []


def check(cond, what):
    print(("  ok  " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        fails.append(what)


def bash():
    if WIN:
        if not os.path.exists(GIT_BASH):
            raise SystemExit("REFUSED: no Git Bash at %s" % GIT_BASH)
        return GIT_BASH
    return "bash"


def fwd(p):
    return p.replace("\\", "/")


def bash_form(p):
    """p as bash prints it: on Windows Git Bash's /c/... form."""
    p = fwd(p)
    if WIN and len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


# ---- the code under test ----------------------------------------------------
def copy_code(src, dst):
    """What the writers need of charge-interposer/, from src to dst."""
    ign = shutil.ignore_patterns("__pycache__")
    os.makedirs(dst)
    for f in ("scratch_run.sh", "stop_all.sh", "machine.py"):
        shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
    shutil.copytree(os.path.join(src, "harness_checks"), os.path.join(dst, "harness_checks"),
                    ignore=ign)
    shutil.copytree(os.path.join(src, "firmware", "main"), os.path.join(dst, "firmware", "main"),
                    ignore=ign)
    shutil.copytree(os.path.join(src, "host_unit"), os.path.join(dst, "host_unit"),
                    ignore=ign)
    for sub in ("host_bridge", "host_diff"):
        shutil.copytree(os.path.join(src, "fw_harness", sub),
                        os.path.join(dst, "fw_harness", sub), ignore=ign)


def tree(base, name="tree"):
    """A stand-in worktree holding a copy of the code under test; its
    charge-interposer/."""
    t = os.path.join(base, name)
    os.makedirs(t)
    with open(os.path.join(t, ".git"), "w") as f:
        f.write("gitdir: nowhere\n")
    ci = os.path.join(t, "charge-interposer")
    copy_code(CODE, ci)
    return ci


def harness(ci):
    return os.path.join(os.path.dirname(ci), "scratch", scratch_dir.HARNESS)


# ---- stand-ins --------------------------------------------------------------
GPP = r"""#!/bin/sh
# stand-in g++: logs where its output goes and writes there an output that
# runs, prints nothing and fails
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
printf 'GPP\t%s\n' "$out" >> "$STUB_LOG"
[ -n "$out" ] || exit 1
printf '#!/bin/sh\nexit 1\n' > "$out"
chmod +x "$out"
"""
PY3 = r"""#!/bin/sh
{ printf 'PY'; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
exit 0
"""
# read by bash before the script (BASH_ENV); each logs to STUB_LOG.
# STUB_MKTEMP_RACE=N: the first N mktemp -d calls into a tool folder find it
# removed just before, as another run's cleanup would. STUB_RM_FAIL=N: the
# first N rm -rf calls fail and remove nothing. Counts are in files (both
# can run in a subshell).
BASH_ENV = r"""mktemp() {
  case "${2:-}" in
    */cih/*)
      printf 'MKTEMP\t%s\n' "$2" >> "$STUB_LOG"
      if [ -n "${STUB_MKTEMP_RACE:-}" ]; then
        n="$(cat "$STUB_COUNT.mktemp" 2>/dev/null || echo 0)"
        if [ "$n" -lt "$STUB_MKTEMP_RACE" ]; then
          echo $((n + 1)) > "$STUB_COUNT.mktemp"
          command rmdir "$(dirname "$2")"
        fi
      fi ;;
  esac
  command mktemp "$@"
}
sleep() {
  printf 'SLEEP\t%s\n' "$*" >> "$STUB_LOG"
  command sleep "$@"
}
rm() {
  if [ -n "${STUB_RM_FAIL:-}" ] && [ "${1:-}" = "-rf" ]; then
    n="$(cat "$STUB_COUNT.rm" 2>/dev/null || echo 0)"
    if [ "$n" -lt "$STUB_RM_FAIL" ]; then
      echo $((n + 1)) > "$STUB_COUNT.rm"
      echo "rm: cannot remove '$2': refused by the test" >&2
      return 1
    fi
  fi
  command rm "$@"
}
"""
# sources the helper and takes a run folder; FX_* say what else to do
FIXTURE = r"""set -u
. "$FX_SR" || exit 2
if [ -n "${FX_SLACK:-}" ]; then
  # the length guard's boundary: the limit put FX_SLACK past the run
  # folder's full path, as the layout says it is, plus the depth
  _sr_owner
  full="$(scratch_root)/cih/${FX_TOOL-fixture}/$_SR_PID-XXXXXX"
  echo "FULL $full"
  SCRATCH_RUN_LIMIT=$(( ${#full} + SCRATCH_RUN_DEPTH + FX_SLACK ))
fi
scratch_run "${FX_TOOL-fixture}" || exit $?
echo "RUN $SCRATCH_RUN"
if [ -n "${FX_SECOND:-}" ]; then scratch_run again; echo "SECOND $?"; fi
if [ -n "${FX_GIT:-}" ]; then
  # the git ceiling: a subfolder with no .git of its own, with the variable
  # as scratch_run left it, then without it (the control)
  mkdir -p "$SCRATCH_RUN/sub" || exit 9
  git -C "$SCRATCH_RUN/sub" rev-parse --show-toplevel 2>&1; echo "GITRC $?"
  env -u GIT_CEILING_DIRECTORIES git -C "$SCRATCH_RUN/sub" rev-parse --show-toplevel 2>&1
  echo "CTLRC $?"
fi
if [ -n "${FX_CD:-}" ]; then cd "$SCRATCH_RUN" || exit 9; fi
if [ -n "${FX_RO:-}" ]; then
  mkdir -p "$SCRATCH_RUN/ro/sub" && echo x > "$SCRATCH_RUN/ro/sub/f" &&
    chmod a-w "$SCRATCH_RUN/ro/sub/f" "$SCRATCH_RUN/ro/sub" "$SCRATCH_RUN/ro" || exit 9
fi
if [ -n "${FX_WAIT:-}" ]; then
  echo "$SCRATCH_RUN" > "$FX_WAIT.ready"
  # bounded: a test that never says go must not leave this looping (it did,
  # for a day, on madhouse-debian: 2026-11-03)
  n=0
  while [ ! -e "$FX_WAIT.go" ]; do
    n=$((n + 1)); [ "$n" -le 2400 ] || exit 98
    command sleep 0.05
  done
fi
exit "${FX_RC:-0}"
"""
# prints scratch_root() for a helper home of FX_HOME
ROOT_FIXTURE = r"""set -u
. "$FX_SR" || exit 2
_SR_HOME="$FX_HOME"
scratch_root
"""


class Run:
    """A work folder for one bash run: stand-ins, an empty TMPDIR, a log."""

    def __init__(self, work):
        self.work = work
        os.makedirs(work)
        self.tmp = os.path.join(work, "tmp")
        os.makedirs(self.tmp)
        self.log = os.path.join(work, "stub.log")
        self.bindir = os.path.join(work, "bin")
        os.makedirs(self.bindir)
        # gcc too: since port step 2b the host_bridge builds compile C
        # sources (fw_harness/host_bridge/l2_build.sh), logged as g++'s are
        for name, text in (("g++", GPP), ("gcc", GPP), ("python3", PY3)):
            p = os.path.join(self.bindir, name)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        self.benv = os.path.join(work, "bash_env.sh")
        with open(self.benv, "w", encoding="utf-8", newline="\n") as f:
            f.write(BASH_ENV)

    def env(self, extra, stubs=True):
        e = dict(os.environ)
        for k in list(e):
            if k.startswith(("STUB_", "FX_")) or k in ("INTERPOSER_CODE", "SCRATCH_RUN_SH"):
                e.pop(k)
        e.update(STUB_LOG=fwd(self.log), STUB_COUNT=fwd(os.path.join(self.work, "count")),
                 TMPDIR=fwd(self.tmp), BASH_ENV=fwd(self.benv))
        if stubs:
            e["PATH"] = self.bindir + os.pathsep + e.get("PATH", "")
        e.update(extra)
        return e

    def lines(self, kind):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            out = []
            for ln in f.read().splitlines():
                k, _, v = ln.partition("\t")
                if kind is None or k == kind:
                    out.append(ln if kind is None else v)
            return out

    def order(self):
        """the MKTEMP and SLEEP lines, in order, as 'mktemp' / 'sleep <s>'"""
        return [("mktemp" if ln.startswith("MKTEMP") else "sleep " + ln.split("\t")[1])
                for ln in self.lines(None) if ln.startswith(("MKTEMP", "SLEEP"))]


def fixture(r, ci, extra=None, wait=False, text=FIXTURE):
    """run the fixture in r with ci's helper; (rc, output), or the Popen
    when wait (the fixture then waits for FX_WAIT.go)"""
    fx = os.path.join(r.work, "fx.sh")
    with open(fx, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    env = r.env(dict({"FX_SR": fwd(os.path.join(ci, "scratch_run.sh"))}, **(extra or {})))
    if wait:
        env["FX_WAIT"] = fwd(os.path.join(r.work, "w"))
        return subprocess.Popen([bash(), fwd(fx)], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    p = subprocess.run([bash(), fwd(fx)], env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=120)
    return p.returncode, p.stdout


def wait_ready(r, timeout=60):
    p = os.path.join(r.work, "w.ready")
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = f.read().strip()
            if d:
                return d
        time.sleep(0.05)
    raise AssertionError("the fixture never got its run folder")


def go(r):
    open(os.path.join(r.work, "w.go"), "w").close()


def native(p):
    """a path bash printed, as Python opens it"""
    if WIN and len(p) > 2 and p[0] == "/" and p[2] == "/":
        return p[1].upper() + ":" + p[2:]
    return p


def owner_of(d):
    with open(os.path.join(native(d), scratch_dir.OWNER), encoding="utf-8") as f:
        return json.load(f)


def gone(ci, tool="fixture"):
    return not os.path.exists(os.path.join(harness(ci), tool))


# ---- the helper -------------------------------------------------------------
def case_made(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    p = fixture(r, ci, {"FX_RC": "3", "FX_CD": "1"}, wait=True)
    try:
        d = wait_ready(r)
        want = bash_form(os.path.join(harness(ci), "fixture")) + "/"
        check(scratch_dir.HARNESS == "cih" and d.startswith(want)
              and "fixture-" not in d[len(want):],
              "1. the run folder is <worktree>/scratch/%s/<tool>/<pid>-... -> %s"
              % (scratch_dir.HARNESS, d))
        try:
            o = owner_of(d)
        except (OSError, ValueError) as e:
            o = {"error": repr(e)}
        start_ok = (o.get("start") is None) if WIN else (
            o.get("start") is not None and o.get("start") == scratch_dir.proc(o.get("pid"))[1])
        check(isinstance(o.get("pid"), int) and o.get("host") == socket.gethostname()
              and o.get("platform") == sys.platform and o.get("argv") == ["fx.sh"]
              and isinstance(o.get("made"), str) and start_ok
              and d[len(want):].startswith("%s-" % o.get("pid")),
              "1. its owner file: pid (in the name too), host, platform, argv, made, "
              "start (%s) -> %s" % ("null on Windows" if WIN else "Linux's", o))
        st = scratch_dir.state(native(d))
        check(st == "live", "1. scratch_dir.state() is live while the script runs -> %s" % st)
    finally:
        go(r)
        out = p.communicate(timeout=120)[0]
    check(p.returncode == 3, "1. the script's exit code is its own, from inside its folder "
          "too -> %d %s" % (p.returncode, out[-200:]))
    check(not os.path.exists(native(d)) and gone(ci) and "LEFTOVER" not in out,
          "1. the folder is removed when it exits, and the tool folder with it")
    check(os.listdir(r.tmp) == [], "1. nothing in the system temp folder -> %s"
          % os.listdir(r.tmp))
    check(r.order() == ["mktemp"], "1. one try, no wait -> %s" % r.order())


def case_killed(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    p = fixture(r, ci, wait=True)
    # everything after the fixture starts is inside the try, so a failure
    # here (no owner file, say) still tells the fixture to go
    try:
        d = wait_ready(r)
        pid = owner_of(d).get("pid")
        os.kill(pid, signal.SIGTERM if WIN else signal.SIGKILL)
        try:
            p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # Git Bash: the kill took the script's shell and not every
            # process it had made (1 run in 6 here, 2026-11-04: its launcher
            # never returned and a bash of fx.sh stayed). Kill the rest of
            # the script's processes, as a kill of its whole group would;
            # the case judges the folder a killed script leaves
            rest = leftovers(r.work)
            print("note  case_killed: the script's launcher was still running 15 s after its "
                  "shell was killed; killing %d more of its processes" % len(rest), flush=True)
            kill_all(rest)
            p.communicate(timeout=60)
    finally:
        go(r)
        if p.poll() is None:
            p.kill()
            p.communicate()
    st = scratch_dir.state(native(d))
    rows = {os.path.normpath(pth): v for pth, v, _w in scratch_dir.survey(os.path.dirname(ci))}
    check(os.path.isdir(native(d)) and st == "finished"
          and rows.get(os.path.normpath(native(d))) == "candidate",
          "2. a killed script's folder stays, finished, and the sweep lists it -> %s, %s"
          % (st, rows.get(os.path.normpath(native(d)))))
    scratch_dir.remove(native(d))


def case_readonly(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    rc, out = fixture(r, ci, {"FX_RO": "1"})
    check(rc == 0 and gone(ci) and "LEFTOVER" not in out,
          "3. read-only files and folders are removed -> rc %d %s" % (rc, out[-200:]))


def case_removal(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    rc, out = fixture(r, ci, {"STUB_RM_FAIL": "2"})
    check(rc == 0 and gone(ci) and "LEFTOVER" not in out
          and r.order() == ["mktemp", "sleep 0.1", "sleep 0.4"],
          "4. a removal that fails twice is tried again after 0.1 s and 0.4 s -> "
          "rc %d, %s" % (rc, r.order()))
    r = Run(os.path.join(base, "r2"))
    rc, out = fixture(r, ci, {"STUB_RM_FAIL": "9", "FX_RC": "4"})
    run = next((ln[4:] for ln in out.splitlines() if ln.startswith("RUN ")), "")
    check(rc == 4 and run and ("scratch_run: LEFTOVER %s" % run) in out
          and os.path.isdir(native(run)),
          "4. one that keeps failing leaves the folder, printed as a LEFTOVER line, "
          "the exit code unchanged -> rc %d %s" % (rc, out[-200:]))
    if run:
        scratch_dir.remove(native(run))


def case_race(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    rc, out = fixture(r, ci, {"STUB_MKTEMP_RACE": "2"})
    three = ["mktemp", "sleep 0.1", "mktemp", "sleep 0.4", "mktemp"]
    check(rc == 0 and r.order() == three and "mktemp" not in out
          and "No such file" not in out and gone(ci),
          "5. the tool folder taken twice: two more tries, after 0.1 s and 0.4 s, "
          "printing nothing -> rc %d, %s %s" % (rc, r.order(), out[-200:]))
    r = Run(os.path.join(base, "r2"))
    rc, out = fixture(r, ci, {"STUB_MKTEMP_RACE": "9"})
    check(rc == 2 and r.order() == three and "mktemp" in out and "RUN " not in out
          and gone(ci),
          "5. lost three times: refused, rc 2, the last try's reason printed, "
          "nothing made -> rc %d, %s %s" % (rc, r.order(), out[-200:]))


def case_shared_tool(base):
    ci = tree(base)
    ra = Run(os.path.join(base, "a"))
    pa = fixture(ra, ci, wait=True)
    try:
        da = wait_ready(ra)
        rb = Run(os.path.join(base, "b"))
        rc, out = fixture(rb, ci)
        check(rc == 0 and os.path.isdir(native(da)),
              "6. a tool folder holding another live run's folder stays -> rc %d" % rc)
    finally:
        go(ra)
        pa.communicate(timeout=120)
    check(gone(ci), "6. and goes with that run")


def case_refused(base):
    ci = tree(base)
    for i, name in enumerate(("", ".hidden", "a/b", "a\\b")):
        r = Run(os.path.join(base, "r%d" % i))
        rc, out = fixture(r, ci, {"FX_TOOL": name})
        check(rc == 2 and "one plain folder name" in out and "RUN " not in out
              and r.order() == [],
              "7. the tool name %r is refused -> rc %d" % (name, rc))
    r = Run(os.path.join(base, "second"))
    rc, out = fixture(r, ci, {"FX_SECOND": "1"})
    check(rc == 0 and "SECOND 2" in out and "already has a run folder" in out
          and r.order() == ["mktemp"] and gone(ci),
          "7. a second run folder in one script is refused, and the first still goes "
          "-> %s" % out[-200:])


def case_root(base):
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    # a stand-in worktree of its own, so the case holds in a copy of the
    # code with no git above it (the harness suite's form on madhouse);
    # inside a real worktree it is the nearer of the two
    wt = os.path.join(base, "wt")
    os.makedirs(wt)
    with open(os.path.join(wt, ".git"), "w") as f:
        f.write("gitdir: nowhere\n")
    deep = os.path.join(wt, "no-git-here", "x", "charge-interposer")
    rc, out = fixture(r, ci, {"FX_HOME": bash_form(deep)}, text=ROOT_FIXTURE)
    want = bash_form(os.path.join(wt, "scratch"))
    check(rc == 0 and out.strip() == want,
          "8. the root is the nearest worktree's scratch/ above the helper -> %s"
          % out.strip())
    r = Run(os.path.join(base, "r2"))
    rc, out = fixture(r, ci, {"FX_HOME": "/no-such-sr-root/code"}, text=ROOT_FIXTURE)
    check(rc == 0 and out.strip() == "/no-such-sr-root/scratch",
          "8. with no git above it, scratch/ beside the code folder -> %s" % out.strip())


def case_length(base):
    import re
    with open(os.path.join(CODE, "scratch_run.sh"), encoding="utf-8") as f:
        text = f.read()
    lim = re.search(r"^SCRATCH_RUN_LIMIT=(\d+)$", text, re.M)
    dep = re.search(r"^SCRATCH_RUN_DEPTH=(\d+)$", text, re.M)
    got = (int(lim.group(1)) if lim else None, int(dep.group(1)) if dep else None)
    check(got == (scratch_dir.LIMIT, scratch_dir.DEPTH),
          "12. the limit and depth are scratch_dir's (%d, %d) -> %s"
          % (scratch_dir.LIMIT, scratch_dir.DEPTH, got))
    ci = tree(base)
    r = Run(os.path.join(base, "r"))
    rc, out = fixture(r, ci, {"FX_SLACK": "0"})
    check(rc == 0 and "RUN " in out and gone(ci),
          "12. full path plus depth exactly at the limit: made -> rc %d %s" % (rc, out[-200:]))
    ci = tree(base, "tree2")        # a fresh one: the first run's cih/ stays
    r = Run(os.path.join(base, "r2"))
    rc, out = fixture(r, ci, {"FX_SLACK": "-1"})
    full = next((ln[5:] for ln in out.splitlines() if ln.startswith("FULL ")), "")
    check(rc == 2 and "REFUSED" in out and "RUN " not in out and r.order() == []
          and not os.path.exists(harness(ci)),
          "12. one character past it: refused (rc 2), nothing made -> rc %d %s"
          % (rc, out[-200:]))
    check(full and full in out.replace("FULL " + full, "")
          and ("%d characters" % len(full)) in out,
          "12. the refusal names the full path and its length (%d) -> %s"
          % (len(full), out[-240:]))


def case_ceiling(base):
    ci = tree(base, "treeg")
    t = os.path.dirname(ci)
    os.remove(os.path.join(t, ".git"))
    subprocess.run(["git", "init", "-q", t], check=True, capture_output=True)
    r = Run(os.path.join(base, "r"))
    rc, out = fixture(r, ci, {"FX_GIT": "1"})
    lines = out.splitlines()

    def after(tag):
        i = next((n for n, ln in enumerate(lines) if ln.startswith(tag)), None)
        return lines[i].split()[1] if i is not None else None, \
            lines[i - 1] if i else ""
    git_rc, git_out = after("GITRC ")
    ctl_rc, ctl_out = after("CTLRC ")
    check(ctl_rc == "0" and os.path.normcase(native(ctl_out.strip()).replace("/", os.sep))
          == os.path.normcase(t),
          "13. control: without the variable git finds the enclosing repo -> %s %s"
          % (ctl_rc, ctl_out[-100:]))
    check(rc == 0 and git_rc not in (None, "0") and "not a git repository" in git_out,
          "13. with it, as scratch_run left it: not a git repository -> rc %d, %s %s"
          % (rc, git_rc, git_out[-100:]))


# ---- the writers ------------------------------------------------------------
def code_lines(text):
    """the lines of a bash script with comments taken out"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        i = ln.find(" #")
        out.append(ln if i < 0 else ln[:i])
    return out


def scan(ci):
    """[(rel, problem)] for every .sh under ci"""
    probs = []
    seen_tools = {}
    for d, dirs, files in os.walk(ci):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if not f.endswith(".sh"):
                continue
            p = os.path.join(d, f)
            rel = fwd(os.path.relpath(p, ci))
            with open(p, encoding="utf-8", errors="replace") as fh:
                lines = code_lines(fh.read())
            for ln in lines:
                if "TMPDIR" in ln or "/tmp" in ln:
                    probs.append((rel, "the system temp folder: " + ln.strip()))
                if "mktemp" in ln and rel not in MKTEMP_OK:
                    probs.append((rel, "mktemp: " + ln.strip()))
            if rel in WRITERS:
                tool = WRITERS[rel][0]
                if not any(ln.lstrip().startswith(". ") and "scratch_run.sh" in ln
                           for ln in lines):
                    probs.append((rel, "does not source scratch_run.sh"))
                calls = [ln.split()[1] for ln in lines
                         if ln.lstrip().startswith("scratch_run ") and len(ln.split()) > 1]
                if calls != [tool]:
                    probs.append((rel, "takes %s, not its own tool %s" % (calls, tool)))
                for t in calls:
                    if t in seen_tools and seen_tools[t] != rel:
                        probs.append((rel, "shares the tool %s with %s" % (t, seen_tools[t])))
                    seen_tools[t] = rel
    for rel in WRITERS:
        if not os.path.isfile(os.path.join(ci, rel)):
            probs.append((rel, "no such writer"))
    return probs


def case_scan(base):
    ci = tree(base)
    probs = scan(ci)
    check(probs == [], "9. no .sh names the system temp folder, only scratch_run.sh and "
          "host_bridge/run.sh call mktemp, and every writer takes its own tool's run "
          "folder -> %s" % probs[:4])
    p = os.path.join(ci, "harness_checks", "planted.sh")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write('# a comment naming /tmp and TMPDIR is fine\n'
                'X="${TMPDIR:-/tmp}/x"  # but this line is not\n'
                'Y=$(mktemp -d)\n')
    planted = [w for rel, w in scan(ci) if rel == "harness_checks/planted.sh"]
    check(len(planted) == 2 and "TMPDIR" in planted[0] and "mktemp" in planted[1],
          "9. the scan finds a planted temp-folder line and a planted mktemp, not the "
          "comment -> %s" % planted)


def case_writers(base):
    ci = tree(base)
    rid = os.path.join(base, "rid")
    os.makedirs(os.path.join(rid, "projects", "vtrux", "notes", "artifacts",
                             "interposer-firmware"))
    trace = os.path.join(base, "empty.trace")
    open(trace, "w").close()
    # the shared folders the host_bridge builders find by TWAI_FAKE_DIR
    # (port step 2b, l2_build.sh): stand-ins of the files it checks for
    fake = os.path.join(base, "co", "shared", "twai_port", "test")
    for rel in ("twai_fake.c", "cpp/twai_fake.cpp", "../twai_port.c",
                "../twai_port_ll_unsupported.c", "../include/twai_port.h",
                "../../can_port/can_port.c", "../../can_port/include/can_port.h"):
        p = os.path.normpath(os.path.join(fake, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    for n, (rel, (tool, args)) in enumerate(sorted(WRITERS.items())):
        r = Run(os.path.join(base, "w%02d" % n))
        linux_only = rel == "stop_all.sh"
        env = r.env({"REVERSE_IT_DIR": fwd(rid), "L3_STIMULUS": "unused",
                     "TWAI_FAKE_DIR": fwd(fake)},
                    stubs=not linux_only)
        argv = [bash(), fwd(os.path.join(ci, rel))] + [
            fwd(trace) if a == "{trace}" else a for a in args]
        try:
            p = subprocess.run(argv, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=600)
            rc, out = p.returncode, p.stdout
        except subprocess.TimeoutExpired:
            rc, out = None, "timed out"
        made = r.lines("MKTEMP")
        want = bash_form(os.path.join(harness(ci), tool)) + "/"
        left = os.path.join(harness(ci), tool)
        if linux_only and sys.platform != "linux":
            check(rc == 2 and "REFUSED" in out and not made and not os.path.exists(left)
                  and os.listdir(r.tmp) == [],
                  "10. %s refuses off Linux before making anything (its run is checked "
                  "on Linux) -> rc %s" % (rel, rc))
            continue
        outs = r.lines("GPP")
        check(len(made) == 1 and made[0].startswith(want),
              "10. %s: one run folder, for %s -> %s" % (rel, tool, made))
        check(linux_only or (outs and all(bash_form(o).startswith(want) for o in outs)),
              "10. %s: every g++ output in it (%d) -> %s"
              % (rel, len(outs), [o for o in outs if not bash_form(o).startswith(want)][:2]))
        check(os.listdir(r.tmp) == [] and not os.path.exists(left) and "LEFTOVER" not in out
              and rc is not None,
              "10. %s: nothing in the temp folder, nothing left (rc %s) -> %s"
              % (rel, rc, (os.listdir(r.tmp), os.path.exists(left), out[-160:])))


CASES = [case_made, case_killed, case_readonly, case_removal, case_race,
         case_shared_tool, case_refused, case_root, case_scan, case_writers,
         case_length, case_ceiling]


# ---- processes left behind --------------------------------------------------
def _cmdlines():
    """[(pid, command line)] of every process but this one"""
    me = os.getpid()
    out = []
    if WIN:
        ps = ("Get-CimInstance Win32_Process | ForEach-Object { "
              "\"$($_.ProcessId)`t$($_.CommandLine)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=120)
        for ln in r.stdout.splitlines():
            pid, _, cmd = ln.partition("\t")
            if pid.strip().isdigit() and int(pid) != me:
                out.append((int(pid), cmd))
        return out
    for e in os.listdir("/proc"):
        if not e.isdigit() or int(e) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % e, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        out.append((int(e), cmd))
    return out


def leftovers(folder):
    """[(pid, command line)] of the processes whose command line names a
    path under folder, in either spelling bash or Python gives it"""
    forms = {fwd(folder).lower().rstrip("/") + "/", bash_form(folder).lower().rstrip("/") + "/"}
    return [(pid, cmd) for pid, cmd in _cmdlines()
            if any(f in fwd(cmd).lower() for f in forms)]


def kill_all(procs):
    for pid, _cmd in procs:
        try:
            os.kill(pid, signal.SIGTERM if WIN else signal.SIGKILL)
        except OSError:
            pass


PLANTED = "while :; do command sleep 0.2; done\n"
PLANT_ENV = "SCRATCH_RUN_TEST_PLANT_OUTSIDE"


def check_left(base):
    """11. a planted loop is seen and killed; then nothing of the test's own
    is left running under its folder, and anything that is gets killed"""
    d = os.path.join(base, "planted")
    os.makedirs(d)
    sh = os.path.join(d, "planted.sh")
    with open(sh, "w", encoding="utf-8", newline="\n") as f:
        f.write(PLANTED)
    p = subprocess.Popen([bash(), fwd(sh)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        seen = []
        end = time.time() + 30
        while not seen and time.time() < end:
            seen = leftovers(d)
            if not seen:
                time.sleep(0.2)
        check(bool(seen), "11. the scan sees a planted loop under the folder -> %s"
              % [c[:100] for _p, c in seen])
        kill_all(seen)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=60)
    end = time.time() + 10
    left = leftovers(base)
    while left and time.time() < end:
        time.sleep(0.2)
        left = leftovers(base)
    check(left == [], "11. no process of the test is left running under its folder -> %s"
          % [c[:160] for _p, c in left])
    kill_all(left)


def test():
    # its deepest git is case_shared_tool\tree\.git\config, 33 below the run
    # folder and its separator, 34 in all (measured 2026-11-05), past
    # scratch_dir.DEPTH
    with scratch_dir.run("scratch-run-test", depth=34) as base:
        for c in CASES:
            d = os.path.join(base, c.__name__)
            os.makedirs(d)
            try:
                c(d)
            except Exception as e:     # noqa: BLE001
                check(False, "%s raised %s: %s" % (c.__name__, type(e).__name__, e))
        try:
            check_left(base)
        except Exception as e:         # noqa: BLE001
            check(False, "check_left raised %s: %s" % (type(e).__name__, e))
        if os.environ.get(PLANT_ENV):
            # --can-fail's control for its outside scan: a loop, bounded
            # (60 s), that names this run's own folder (scratch_dir's
            # naming) and outlives this process
            subprocess.Popen([bash(), "-c", "n=0; while [ $n -lt 300 ]; do command sleep "
                              "0.2; n=$((n + 1)); done", fwd(os.path.join(base, "outside"))],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("\nscratch_run_test: %s" % ("all checks OK" if not fails
                                      else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# (label, file under charge-interposer/, old, new, platform or None), each
# applied to a copy of the code
SR = "scratch_run.sh"
MUTANTS = [
    ("no EXIT trap", SR, "  trap scratch_run_cleanup EXIT\n", "", None),
    ("the run folder in the system temp folder", SR,
     'td="$(scratch_root)/cih/$tool"',
     'td="${TMPDIR:-/tmp}/cih/$tool"', None),
    ("the old long root", SR, 'td="$(scratch_root)/cih/$tool"',
     'td="$(scratch_root)/charge-interposer-harness/$tool"', None),
    ("the run folder named <tool>-<pid>- again", SR,
     'p="$(mktemp -d "$td/$_SR_PID-XXXXXX" 2>/dev/null)" && break',
     'p="$(mktemp -d "$td/$tool-$_SR_PID-XXXXXX" 2>/dev/null)" && break', None),
    ("no length guard", SR, '  if [ "$n" -gt "$SCRATCH_RUN_LIMIT" ]; then',
     "  if false; then", None),
    ("the limit 260", SR, "SCRATCH_RUN_LIMIT=240\n", "SCRATCH_RUN_LIMIT=260\n", None),
    ("the guard leaves the depth out", SR, "  n=$(( ${#full} + SCRATCH_RUN_DEPTH ))\n",
     "  n=${#full}\n", None),
    ("the guard leaves the run folder's own name out", SR,
     '  local full="$td/$_SR_PID-XXXXXX" n\n', '  local full="$td" n\n', None),
    ("the guard refuses at the limit itself", SR,
     '  if [ "$n" -gt "$SCRATCH_RUN_LIMIT" ]; then', '  if [ "$n" -ge "$SCRATCH_RUN_LIMIT" ]; then',
     None),
    ("the refusal without the path", SR, "REFUSED the run folder $full: it is",
     "REFUSED the run folder: it is", None),
    ("the refusal without the length", SR, "it is ${#full} characters, and",
     "it is too many characters, and", None),
    ("no git ceiling", SR,
     '  export GIT_CEILING_DIRECTORIES="$ceil${GIT_CEILING_DIRECTORIES:+$sep$GIT_CEILING_DIRECTORIES}"\n',
     "", None),
    ("the ceiling in bash's /c/ form on Windows", SR,
     '    ceil="$(cd "$p" && pwd -W)" || return 2\n', "", "nt"),
    ("no owner file", SR,
     "  printf '%s\\n' \"$_SR_OWNER\" > \"$p/.scratch-owner.json\" || return 2\n", "", None),
    ("bash's own pid, not Windows'", SR,
     '    _SR_PID="$(cat "/proc/$$/winpid")"\n', "", "nt"),
    ("the wrong start time", SR, 'start="${f[19]:-null}"', 'start="${f[18]:-null}"',
     "posix"),
    ("the platform misnamed (Windows)", SR, '    platform="win32"\n',
     '    platform="cygwin"\n', "nt"),
    ("the platform misnamed (Linux)", SR, '      Linux) platform="linux" ;;',
     '      Linux) platform="Linux" ;;', "posix"),
    ("the host misnamed", SR, '"$(_sr_json "${HOSTNAME:-$(hostname)}")"',
     '"$(_sr_json "some-host")"', None),
    ("no wait before the second try", SR,
     '    [ "$try" = 1 ] || sleep "${SCRATCH_RUN_WAITS[0]}"\n', "", None),
    ("no wait before the last try", SR,
     '    sleep "${SCRATCH_RUN_WAITS[1]}"\n    mkdir -p "$td" || return 2\n',
     '    mkdir -p "$td" || return 2\n', None),
    ("one try only", SR, "  for try in 1 2; do\n", "  for try in ; do\n", None),
    ("tries without end", SR, "  for try in 1 2; do\n",
     "  for try in 1 2 3 4 5 6 7 8 9 10 11; do\n", None),
    ("the failed tries printed", SR,
     'p="$(mktemp -d "$td/$_SR_PID-XXXXXX" 2>/dev/null)" && break',
     'p="$(mktemp -d "$td/$_SR_PID-XXXXXX")" && break', None),
    ("the last try's reason not printed", SR,
     '    p="$(mktemp -d "$td/$_SR_PID-XXXXXX")" || return 2\n',
     '    p="$(mktemp -d "$td/$_SR_PID-XXXXXX" 2>/dev/null)" || return 2\n', None),
    ("the removal not tried again", SR, '  for w in "${SCRATCH_RUN_WAITS[@]}"; do\n',
     "  for w in ; do\n", None),
    ("not made writable before trying again", SR,
     '    chmod -R u+w "$p" 2>/dev/null\n', "", "posix"),
    ("a leftover not reported", SR,
     '    echo "scratch_run: LEFTOVER $p -- rm -rf could not remove it (its reason is above)" >&2\n',
     "    :\n", None),
    ("the tool folder left", SR, '    rmdir "$SCRATCH_TOOL_DIR" 2>/dev/null\n', "    :\n",
     None),
    ("the tool folder removed with another run's in it", SR,
     '    rmdir "$SCRATCH_TOOL_DIR" 2>/dev/null\n',
     '    command rm -rf "$SCRATCH_TOOL_DIR"\n', None),
    ("the exit code replaced", SR, '  SCRATCH_RUN=""\n  return 0\n}',
     '  SCRATCH_RUN=""\n  exit 0\n}', None),
    ("a bad tool name taken", SR, '    ""|.*|*/*|*\\\\*)', '    "")', None),
    ("a second run folder taken", SR, '  if [ -n "$SCRATCH_RUN" ]; then', "  if false; then",
     None),
    ("no git: scratch/ inside the code folder", SR,
     '  echo "$(dirname "$_SR_HOME")/scratch"', '  echo "$_SR_HOME/scratch"', None),
    ("the worktree not looked for", SR,
     '    if [ -e "$d/.git" ]; then echo "$d/scratch"; return 0; fi\n', "", None),
    # in the test's TMPDIR, not a fixed /tmp folder: the writers' case runs
    # the mutant, and a fixed /tmp/busoff_mut would be made for real (it
    # was, on both machines, 2026-11-02); the scan sees /tmp either way
    ("busoff back in the system temp folder", "harness_checks/busoff_mutations.sh",
     '. "$CODE/scratch_run.sh" || exit 2\nscratch_run busoff-mutations || exit 2\n'
     'WORK="$SCRATCH_RUN"', 'WORK="${TMPDIR:-/tmp}/busoff_mut"\nrm -rf "$WORK"\n'
     'mkdir -p "$WORK"', None),
    ("host_unit's binaries in TMPDIR", "host_unit/run.sh",
     'bin="$SCRATCH_RUN/$name"', 'bin="${TMPDIR:-/tmp}/${name}_$$"', None),
    ("diff_all's runner beside the script", "fw_harness/host_diff/diff_all.sh",
     'BIN="$SCRATCH_RUN/hr"', 'BIN="$HERE/hr"', None),
    ("diff_one's runner in TMPDIR", "fw_harness/host_diff/diff_one.sh",
     'BIN="$SCRATCH_RUN/hr_l1"', 'BIN="${TMPDIR:-/tmp}/hr_l1"', None),
    ("stop_all's mutants in TMPDIR", "stop_all.sh",
     '    tmp="$SCRATCH_RUN/mutant_$i.sh"',
     '    tmp=$(mktemp "${TMPDIR:-/tmp}/stop_all_mutant.XXXXXX")', None),
    ("tx_fail under another tool's name", "harness_checks/tx_fail_banking_mutation.sh",
     "scratch_run tx-fail-banking-mutation || exit 2",
     "scratch_run busoff-mutations || exit 2", None),
]


def can_fail():
    plat = "nt" if WIN else "posix"
    bad = total = 0
    with scratch_dir.run("scratch-run-test-mutants") as base:
        def run_copy(i, edit=None):
            ci = os.path.join(base, "m%02d" % i, "charge-interposer")
            copy_code(HERE, ci)
            if edit:
                f, old, new = edit
                p = os.path.join(ci, f)
                with open(p, encoding="utf-8", newline="") as fh:
                    src = fh.read()
                if src.count(old) != 1:
                    return None, src.count(old)
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(src.replace(old, new))
            env = dict(os.environ, SCRATCH_RUN_CODE=ci, PYTHONDONTWRITEBYTECODE="1")
            env.pop(PLANT_ENV, None)
            if i == 0:
                env[PLANT_ENV] = "1"           # the outside scan's control
            p = subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=env)
            try:
                out, err = p.communicate(timeout=3600)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
            r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
            # the copy's own test run folder: a mutant's run must leave no
            # process behind either (check 11 runs inside it too; this is
            # the outside view, after it has exited)
            mine = os.path.join(scratch_dir.tool_dir("scratch-run-test"),
                                "%d-" % p.pid)
            left = [(q, c) for q, c in leftovers(os.path.dirname(mine))
                    if fwd(mine).lower() in fwd(c).lower()
                    or bash_form(mine).lower() in fwd(c).lower()]
            kill_all(left)
            return r, (1 if not left else -len(left))
        r, n = run_copy(0)
        if n >= 0:
            print("FAIL  baseline: the outside scan did not see the loop the copy's run "
                  "left under its own run folder (its control)")
            return 1
        print("ok    the outside scan sees the loop the baseline run left under its own "
              "run folder (%d process(es), killed)" % -n, flush=True)
        if r.returncode != 0:
            print("FAIL  baseline: the unedited copy fails (rc %d): %s"
                  % (r.returncode, [ln for ln in r.stdout.splitlines()
                                    if ln.startswith("FAIL")][:3]))
            return 1
        print("ok    baseline: the unedited copy passes", flush=True)
        for i, (label, f, old, new, only) in enumerate(MUTANTS, 1):
            if only and only != plat:
                print("--    %-58s not run here (%s only)" % (label, only), flush=True)
                continue
            total += 1
            r, n = run_copy(i, (f, old, new))
            if r is None:
                print("FAIL  %-58s NOT APPLIED (found %d times)" % (label, n), flush=True)
                bad += 1
                continue
            if n < 0:
                print("FAIL  %-58s LEFT %d PROCESS(ES) RUNNING (killed)" % (label, -n),
                      flush=True)
                bad += 1
                continue
            caught = r.returncode != 0
            bad += not caught
            first = next((ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")), "")
            print("%s  %-58s %s" % ("ok  " if caught else "FAIL", label,
                                    ("caught: " + first[6:100]) if caught
                                    else "STILL PASSES"), flush=True)
    print("\n%d of %d mutants caught" % (total - bad, total))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(can_fail() if "--can-fail" in sys.argv[1:] else test())
