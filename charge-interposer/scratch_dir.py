#!/usr/bin/env python3
"""Scratch run folders for the harness: made under scratch/, owned, and
removed so that nothing is left behind, on Windows too.

Charge-interposer tester, 2026-10-31: the reviewer's scratch-removal topic,
under AGENTS.md "Removing scratch" (methodology 24e32e0). Git for Windows
marks a repo's .git/objects files read-only, and shutil.rmtree cannot
delete a read-only file on Windows; with ignore_errors=True it failed
without a word and the folder stayed (1,085 run folders and 472K files on
NICKY-XPS, which stalled Windows Search). Every harness file that makes a
throwaway folder now takes it from here:

    <root>/charge-interposer-harness/<tool>/<tool>-<pid>-<random>/
        .scratch-owner.json     {"pid", "start", "host", "made", "argv"}

<root> is <worktree>/scratch, the worktree found by walking up to its .git
(as can_tools_pin.worktree() does). In a copy with no git (the code the
chain runner ships to madhouse) it is scratch/ beside the copied
charge-interposer/ folder, where a worktree's would be: never inside
it, where the harness's walks over charge-interposer/ would meet it.

  make(tool)    prunes the tool's finished folders (below), then makes a
                new one and writes its owner file: this process's pid and
                its start time, so a pid used again by another process is
                not taken for the owner.
  run(tool)     make() as a context manager: the folder is removed in a
                finally, pass or fail.
  remove(path)  shutil.rmtree with an error handler that clears the
                read-only flag (and, on POSIX, makes the parent writable)
                and tries again, on a \\\\?\\ path on Windows so a path past
                260 characters is no obstacle. Never ignore_errors: what it
                cannot remove is printed, one LEFTOVER line each with the
                reason, and returned.
  state(path)   live (the owner is running), finished (it is not, or the
                pid is now another process's), no-owner, or other-host
                (another machine's, or another platform's: a WSL
                process's pid means nothing to Windows).
                Only a finished folder is ever pruned or swept.

THE SWEEP of the backlog left before this module (report first; the user
sees the report before any --apply, reviewer 2026-10-31):

    py -3.14 -B charge-interposer/scratch_dir.py sweep [--apply] [--worktree DIR ...]

Each row gives the folder's files, folders, bytes and newest write, and
the report ends with totals for the candidates and for everything listed:
Windows Search chokes on the number of files, not the bytes (user, via
methodology and the reviewer, 2026-10-31).

It looks only in each worktree's scratch/ (default: the interposer-reviewer,
-implementor and -tester worktrees beside this one). Candidates are run
folders only: a finished folder under charge-interposer-harness/, and a
can-tools/ or canre/ folder for a commit that worktree's pins.json no longer
pins (or a .partial-<pid> whose pid is not running). Never touched: the
current pins' folders, session-compact/, a live or unowned run folder, and
anything else in scratch/ (people's scratchpads), which is listed with its
file count as not a run folder. --apply refuses while the bench lock is
held, the bench-holder "No builds" line is set or a simulator or bench
script is running (AGENTS.md: never during a wall-clock run), and when it
cannot tell.

Test: scratch_dir_test.py (with --can-fail). Each harness file that uses
this module is run with a read-only git tree planted in every folder it
makes, and with the read-only handler taken out, by scratch_dir_check.py.
"""

import argparse
import contextlib
import datetime
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = "charge-interposer-harness"
OWNER = ".scratch-owner.json"
NEVER = ("session-compact",)
PIN_DIRS = {"can-tools": "can-tools", "canre": "canre"}   # scratch dir -> pins.json key
WORKTREES = ("interposer-reviewer", "interposer-implementor", "interposer-tester")
SIM_SCRIPTS = ("charger_sim.py", "vehicle_sim.py", "interposer_sim.py",
               "run_scenario.py", "vtrux_bench.run", "vtrux_bench/run")


def worktree(start=None):
    """The git worktree holding start (default this file), by walking up to
    its .git; None in a copy with no git."""
    d = os.path.abspath(start or HERE)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def root(start=None):
    """<worktree>/scratch, or scratch/ beside this folder in a copy with
    no git."""
    wt = worktree(start)
    return os.path.join(wt if wt else os.path.dirname(HERE), "scratch")


def tool_dir(tool, start=None):
    return os.path.join(root(start), HARNESS, tool)


# ---- processes ----------------------------------------------------------
def _win_proc(pid):
    """(alive, creation time in 100 ns ticks) on Windows; (None, None) if it
    cannot be asked."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        err = ctypes.get_last_error()
        # 87 = ERROR_INVALID_PARAMETER: no such process
        return (False, None) if err == 87 else (None, None)
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return None, None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
            return None, None
        c = times[0]
        return code.value == 259, (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        k.CloseHandle(h)


def _posix_proc(pid):
    try:
        with open("/proc/%d/stat" % pid, encoding="ascii",
                  errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return False, None
    except OSError:
        return None, None
    # comm may hold spaces and brackets: the fields start after the last ')'
    fields = text[text.rfind(")") + 2:].split()
    if fields and fields[0] == "Z":
        return False, None
    return True, int(fields[19])               # field 22, starttime


def proc(pid):
    """(alive, start) for pid: alive True/False/None (cannot tell), start an
    opaque number fixed for the process's life, or None."""
    if not isinstance(pid, int) or pid <= 0:
        return False, None
    if os.name == "nt":
        return _win_proc(pid)
    if os.path.isdir("/proc"):
        return _posix_proc(pid)
    return None, None


def owner_record():
    return {"pid": os.getpid(), "start": proc(os.getpid())[1],
            "host": socket.gethostname(), "platform": sys.platform,
            "made": datetime.datetime.now().isoformat(timespec="seconds"),
            "argv": [os.path.basename(a) for a in sys.argv[:1]] + sys.argv[1:]}


def owner(path):
    try:
        with open(os.path.join(path, OWNER), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def state(path):
    """live | finished | no-owner | other-host. Unsure counts as live."""
    o = owner(path)
    if not isinstance(o, dict) or not isinstance(o.get("pid"), int):
        return "no-owner"
    if o.get("host") != socket.gethostname() or \
            o.get("platform", sys.platform) != sys.platform:
        return "other-host"
    alive, start = proc(o["pid"])
    if alive is None:
        return "live"
    if not alive:
        return "finished"
    if o.get("start") is None or start is None:
        return "live"
    return "live" if start == o["start"] else "finished"


# ---- removal ------------------------------------------------------------
def long_path(p):
    """On Windows, the \\\\?\\ form of p's absolute path; elsewhere p."""
    if os.name != "nt":
        return p
    return _long_nt(os.path.abspath(p))


def _long_nt(p):
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _short(p):
    p = str(p)
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    return p[4:] if p.startswith("\\\\?\\") else p


def _writable(p):
    """Clear the read-only flag on p and make its folder writable."""
    try:
        mode = stat.S_IREAD | stat.S_IWRITE
        if os.path.isdir(p) and not os.path.islink(p):
            mode |= stat.S_IEXEC
        os.chmod(p, mode)
    except OSError:
        pass
    parent = os.path.dirname(p)
    if parent and os.name != "nt":
        try:
            os.chmod(parent, stat.S_IRWXU)
        except OSError:
            pass


def _handler(failed):
    def again(fn, p, exc):
        exc = exc[1] if isinstance(exc, tuple) else exc
        if fn in (os.open, os.scandir, os.lstat, os.stat):
            failed.append((p, exc))
            return
        _writable(p)
        try:
            fn(p)
        except FileNotFoundError:
            pass
        except OSError as e:
            failed.append((p, e))
    return again


def _rmtree(path, again):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=again)
    else:
        shutil.rmtree(path, onerror=again)


def remove(path, quiet=False):
    """Remove the folder at path, read-only files and long paths included.
    Returns [(path, reason)] for what is left (empty when it is all gone)
    and prints each as a LEFTOVER line unless quiet. A path that is not
    there is nothing to do."""
    lp = long_path(path)
    if not os.path.lexists(lp):
        return []
    failed = []
    if os.path.isdir(lp) and not os.path.islink(lp):
        # a second pass, after making writable what the first could not
        # list or remove (a POSIX folder with no read permission)
        for _ in range(2):
            failed = []
            try:
                _rmtree(lp, _handler(failed))
            except OSError as e:
                failed.append((lp, e))
            if not os.path.lexists(lp) or not failed:
                break
            for p, _e in failed:
                _writable(p)
    else:
        _writable(lp)
        try:
            os.unlink(lp)
        except OSError as e:
            failed.append((lp, e))
    left = []
    if os.path.lexists(lp):
        seen = set()
        for p, e in failed:
            if p not in seen:
                seen.add(p)
                left.append((_short(p), "%s: %s" % (type(e).__name__,
                                                     getattr(e, "strerror", None) or e)))
        if not left:
            left.append((_short(lp), "still there, and no error was reported"))
    if left and not quiet:
        for p, why in left[:20]:
            print("scratch_dir: LEFTOVER %s -- %s" % (p, why), flush=True)
        if len(left) > 20:
            print("scratch_dir: LEFTOVER ... and %d more under %s"
                  % (len(left) - 20, _short(lp)), flush=True)
    return left


# ---- run folders --------------------------------------------------------
def prune(tool, start=None, quiet=False):
    """Remove the tool's finished run folders. Returns their paths."""
    d = tool_dir(tool, start)
    gone = []
    try:
        names = sorted(os.listdir(d))
    except FileNotFoundError:
        return gone
    for n in names:
        p = os.path.join(d, n)
        if os.path.isdir(p) and state(p) == "finished":
            if not remove(p, quiet=quiet):
                gone.append(p)
    return gone


def make(tool, start=None):
    """A new run folder for tool, with its owner file, after pruning the
    tool's finished ones."""
    if not tool or os.sep in tool or "/" in tool or tool.startswith("."):
        raise ValueError("tool must be one plain folder name: %r" % (tool,))
    prune(tool, start)
    d = tool_dir(tool, start)
    os.makedirs(d, exist_ok=True)
    p = tempfile.mkdtemp(prefix="%s-%d-" % (tool, os.getpid()), dir=d)
    with open(os.path.join(p, OWNER), "w", encoding="utf-8", newline="\n") as f:
        json.dump(owner_record(), f, sort_keys=True)
        f.write("\n")
    return p


@contextlib.contextmanager
def run(tool, start=None):
    """with run(tool) as d: ...  The folder is removed in a finally."""
    p = make(tool, start)
    try:
        yield p
    finally:
        remove(p)


# ---- the sweep ----------------------------------------------------------
def _count(path):
    """(files, folders, bytes, newest mtime) under path, path itself
    not counted as a folder; a file is (1, 0, its size, its mtime)."""
    lp = long_path(path)
    try:
        st = os.lstat(lp)
    except OSError:
        return 0, 0, 0, 0.0
    if not os.path.isdir(lp):
        return 1, 0, st.st_size, st.st_mtime
    n = folders = size = 0
    newest = st.st_mtime
    for d, dirs, files in os.walk(lp):
        folders += len(dirs)
        for x in dirs + files:
            try:
                s = os.lstat(os.path.join(d, x))
            except OSError:
                continue
            newest = max(newest, s.st_mtime)
            if x in files:
                n += 1
                size += s.st_size
    return n, folders, size, newest


def _when(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") \
        if t else "-"


def _pins(wt):
    try:
        with open(os.path.join(wt, "charge-interposer", "pins.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    out = {}
    for d, key in PIN_DIRS.items():
        e = data.get(key)
        if isinstance(e, dict) and isinstance(e.get("commit"), str):
            out[d] = e["commit"]
    return out


def _partial_pid(name):
    head, sep, tail = name.rpartition(".partial-")
    return int(tail) if sep and tail.isdigit() else None


def survey(wt):
    """[(path, verdict, why)] for one worktree's scratch/: verdict is
    'candidate', 'kept' or 'other' (not a run folder)."""
    s = os.path.join(wt, "scratch")
    out = []
    if not os.path.isdir(s):
        return out
    pins = _pins(wt)
    for top in sorted(os.listdir(s)):
        tp = os.path.join(s, top)
        if not os.path.isdir(tp):
            out.append((tp, "other", "a file, not a run folder"))
        elif top in NEVER:
            out.append((tp, "kept", "never swept"))
        elif top == HARNESS:
            for tool in sorted(os.listdir(tp)):
                td = os.path.join(tp, tool)
                if not os.path.isdir(td):
                    out.append((td, "other", "a file, not a run folder"))
                    continue
                for n in sorted(os.listdir(td)):
                    p = os.path.join(td, n)
                    st = state(p) if os.path.isdir(p) else "a file"
                    out.append((p, "candidate" if st == "finished" else "kept",
                                st))
        elif top in PIN_DIRS:
            cur = (pins or {}).get(top)
            for n in sorted(os.listdir(tp)):
                p = os.path.join(tp, n)
                pid = _partial_pid(n)
                if pins is None:
                    out.append((p, "kept", "pins.json unreadable: current pin unknown"))
                elif pid is not None:
                    alive = proc(pid)[0]
                    out.append((p, "candidate" if alive is False else "kept",
                                "a partial install, pid %d %s" % (
                                    pid, {False: "not running", True: "running",
                                          None: "unknown"}[alive])))
                elif n == cur:
                    out.append((p, "kept", "the current pin"))
                else:
                    out.append((p, "candidate", "not the current pin %s"
                                % (cur or "(none)")[:12]))
        else:
            out.append((tp, "other", "not a run folder: left alone"))
    return out


def default_worktrees():
    wt = worktree()
    base = os.path.dirname(wt) if wt else None
    if not base:
        return []
    return [os.path.join(base, w) for w in WORKTREES
            if os.path.isdir(os.path.join(base, w))]


def _bench_lock_free():
    wt = worktree()
    if not wt:
        return False, "no worktree, so no shared/bench to ask the lock"
    env = dict(os.environ, PYTHONPATH=os.path.join(wt, "shared", "bench"),
               PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([sys.executable, "-B", "-m", "vtrux_bench.bench_lock",
                            "status"], capture_output=True, text=True, env=env,
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "bench_lock status failed: %s" % e
    for line in r.stdout.splitlines():
        if line.startswith("holder:"):
            free = line.strip() == "holder: the lock is free"
            return free, line.strip()
    return False, "bench_lock status gave no holder line (rc %d)" % r.returncode


def _no_builds_clear():
    rid = os.environ.get("REVERSE_IT_DIR")
    if not rid:
        return False, "REVERSE_IT_DIR is not set: cannot read bench-holder.md"
    try:
        with open(os.path.join(rid, "firmware", "bench-holder.md"),
                  encoding="utf-8") as f:
            for line in f:
                if line.startswith("**No builds:**"):
                    v = line[len("**No builds:**"):].strip()
                    return v.startswith("--"), "No builds: %s" % v[:60]
    except OSError as e:
        return False, "bench-holder.md unreadable: %s" % e
    return False, "bench-holder.md has no No builds line"


def _sims_running():
    """(none running, why). Python processes running a simulator or the
    chain runner, this one excluded."""
    me = os.getpid()
    lines = []
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
              "Name='py.exe' or Name='pythonw.exe'\" | ForEach-Object { "
              "'{0} {1}' -f $_.ProcessId, $_.CommandLine }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return False, "cannot list processes: %s" % e
        if r.returncode:
            return False, "cannot list processes (rc %d)" % r.returncode
        lines = r.stdout.splitlines()
    elif os.path.isdir("/proc"):
        for n in os.listdir("/proc"):
            if not n.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % n) as f:
                    if not f.read().startswith("python"):
                        continue
                with open("/proc/%s/cmdline" % n, "rb") as f:
                    lines.append("%s %s" % (n, f.read().replace(b"\0", b" ")
                                            .decode("utf-8", "replace")))
            except OSError:
                pass
    else:
        return False, "cannot list processes on this system"
    hits = []
    for line in lines:
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != me and \
                any(s in cmd.replace("\\", "/") for s in SIM_SCRIPTS):
            hits.append("pid %s" % pid)
    return not hits, ("running: " + ", ".join(hits)) if hits else "none running"


def wallclock_clear():
    """(clear, [reasons]). Clear only when every check says so."""
    checks = [_bench_lock_free(), _no_builds_clear(), _sims_running()]
    return all(ok for ok, _ in checks), [why for _, why in checks]


def sweep(worktrees, apply=False, guard=wallclock_clear, out=print):
    """Report (and with apply, remove) the candidates. Returns the exit code:
    0 done, 1 something left, 2 refused."""
    tot = {"candidate": [0, 0, 0, 0], "all": [0, 0, 0, 0]}
    cands = []
    for wt in worktrees:
        out("== %s" % wt)
        rows = survey(wt)
        if not rows:
            out("   (scratch/ empty or missing)")
        for p, verdict, why in rows:
            n, f, b, t = _count(p)
            rel = os.path.relpath(p, wt)
            out("   %-9s %-60s %8d files %7d folders %12d bytes  %s  %s"
                % (verdict, rel, n, f, b, _when(t), why))
            for k in (("candidate", "all") if verdict == "candidate" else ("all",)):
                tot[k][0] += 1
                tot[k][1] += n
                tot[k][2] += f
                tot[k][3] += b
            if verdict == "candidate":
                cands.append(p)
    out("")
    for k, label in (("candidate", "candidates"), ("all", "everything listed")):
        out("TOTAL %-18s %4d row(s) %9d files %8d folders %13d bytes"
            % (label, *tot[k]))
    if not apply:
        out("REPORT ONLY: nothing removed. --apply removes the candidates.")
        return 0
    ok, why = guard()
    for w in why:
        out("   wall-clock check: %s" % w)
    if not ok:
        out("REFUSED: --apply is not run during a wall-clock run, or when that "
            "cannot be told")
        return 2
    left = 0
    for p in cands:
        if remove(p):
            left += 1
    out("removed %d of %d candidate folder(s)" % (len(cands) - left, len(cands)))
    return 1 if left else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scratch_dir.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("sweep", help="report (default) or remove the backlog")
    sw.add_argument("--apply", action="store_true")
    sw.add_argument("--worktree", action="append", default=[])
    a = ap.parse_args(argv)
    wts = [os.path.abspath(w) for w in a.worktree] or default_worktrees()
    if not wts:
        print("no worktree found: name one with --worktree")
        return 2
    return sweep(wts, apply=a.apply)


if __name__ == "__main__":
    sys.exit(main())
