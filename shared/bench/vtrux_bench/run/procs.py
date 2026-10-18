"""Running commands: timeouts that kill the whole tree, the build throttle,
and the process-list scan for wall-clock tests.

The throttle is the standing rule for the shared machines (esp-development
.md, 2026-10-16): while a wall-clock test of either project runs on a host,
every build there runs at idle priority with one job. A wall-clock test is
running on a host when
  - this runner has a "wallclock" check active there, or
  - (local host) bench-holder.md names any holder, or
  - a process whose command line matches one of the settings'
    "wallclock_patterns" is running there.
"""

import os
import re
import shlex
import signal
import subprocess
import sys
import time

WIN = sys.platform == "win32"
IDLE_PRIORITY_CLASS = 0x00000040
CREATE_NEW_PROCESS_GROUP = 0x00000200

THROTTLE_ENV = {"PLATFORMIO_RUN_JOBS": "1", "MAKEFLAGS": "-j1",
                "CMAKE_BUILD_PARALLEL_LEVEL": "1"}


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_local(argv, cwd, env, timeout_s, log_path, throttled=False,
              stop_check=None):
    """Run argv; stdout and stderr to log_path. Returns (rc, timed_out).
    rc is None if killed. stop_check() returning True kills it early."""
    kw = {}
    if WIN:
        flags = CREATE_NEW_PROCESS_GROUP
        if throttled:
            flags |= IDLE_PRIORITY_CLASS
        kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
        if throttled:
            kw["preexec_fn"] = lambda: os.nice(19)
    full_env = dict(os.environ)
    full_env.update(env or {})
    if throttled:
        full_env.update(THROTTLE_ENV)
    with open(log_path, "ab") as log:
        log.write(("$ %s\n# cwd=%s throttled=%s\n" % (
            " ".join(shlex.quote(a) for a in argv), cwd, throttled)).encode())
        log.flush()
        proc = subprocess.Popen(argv, cwd=cwd, env=full_env, stdout=log,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kw)
        t_end = time.monotonic() + timeout_s
        while True:
            try:
                rc = proc.wait(timeout=1.0)
                return rc, False
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > t_end:
                kill_tree(proc)
                log.write(b"\n# runner: TIMEOUT, process tree killed\n")
                return None, True
            if stop_check is not None and stop_check():
                kill_tree(proc)
                log.write(b"\n# runner: STOPPED, process tree killed\n")
                return None, False


def remote_command(argv, cwd, env, timeout_s, throttled):
    """The shell line run on a remote host over ssh. coreutils `timeout`
    kills the remote side even if the ssh link drops."""
    parts = []
    if cwd:
        parts.append("cd %s &&" % shlex.quote(cwd))
    e = dict(env or {})
    if throttled:
        e.update(THROTTLE_ENV)
    if e:
        parts.append("env " + " ".join("%s=%s" % (k, shlex.quote(str(v)))
                                       for k, v in sorted(e.items())))
    parts.append("timeout --kill-after=30 %d" % int(max(1, timeout_s)))
    if throttled:
        parts.append("nice -n 19")
    parts.append(" ".join(shlex.quote(a) for a in argv))
    return " ".join(parts)


def run_remote(ssh_host, argv, cwd, env, timeout_s, log_path, throttled=False,
               stop_check=None, ssh=("ssh", "-o", "BatchMode=yes")):
    line = remote_command(argv, cwd, env, timeout_s, throttled)
    return run_local(list(ssh) + [ssh_host, line], None, None,
                     timeout_s + 90, log_path, False, stop_check)


def process_lines(ssh_host=None, ssh=("ssh", "-o", "BatchMode=yes")):
    """Command lines of every process on the host (local or over ssh)."""
    if ssh_host:
        argv = list(ssh) + [ssh_host, "ps -eo args"]
    elif WIN:
        argv = ["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                "ForEach-Object { $_.CommandLine }"]
    else:
        argv = ["ps", "-eo", "args"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("process list failed on %s: %s"
                           % (ssh_host or "local", r.stderr[-300:]))
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not lines:
        # An empty list is not "nothing running" -- it is a broken probe.
        raise RuntimeError("process list on %s came back empty"
                           % (ssh_host or "local"))
    return lines


def matching(lines, patterns, own_pid=None):
    out = []
    regs = [re.compile(p) for p in patterns]
    for ln in lines:
        if any(r.search(ln) for r in regs):
            out.append(ln)
    return out
