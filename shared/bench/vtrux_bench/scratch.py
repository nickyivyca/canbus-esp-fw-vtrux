"""The one shared scratch helper: folders a tool makes under a worktree's
scratch/, owned, and removed so that nothing is left behind, on Windows too.

ACCEPT-SPEC A14 (user, 2026-11-01), merged from the charge-interposer's
`scratch_dir.py` and gen-inhibit's `gi_paths.py` (AGENTS.md "Removing
scratch"): Git for Windows marks a repo's .git/objects files read-only,
shutil.rmtree cannot delete a read-only file on Windows, and with
ignore_errors=True it failed without a word (2026-10-31: 1,085 run folders
and 472K files left on NICKY-XPS, which stalled Windows Search). The
acceptance runner takes every folder it makes from here; each project moves
its own harness onto it as its own harness work.

    <parent>/<name>-<pid>-<hex6>/          the folder
    <parent>/<name>-<pid>-<hex6>.owner.json  its owner, BESIDE it

The owner file is beside the folder, not in it, so a folder that holds a
git clone stays clean (A4: a tree must have no changes). It records the pid,
the process's start time (so a pid used again by another process is not
taken for the owner), the host and the platform.

  scratch_root(start)  <the git worktree holding start>/scratch; refused
                       (ScratchError) outside any worktree, never the system
                       temp folder.
  make(parent, name)   removes the finished folders in parent (prune), then
                       makes a new one with its owner file.
  run(parent, name)    make() as a context manager, removed in a finally.
  Made()               the folders a run made: remove_all() is the finally's
                       half and an exit handler the other, so every way out
                       but a hard kill removes them; the next make() beside
                       a killed run's folder removes it.
  remove(path)         the folder (or file, or link) and its owner file. A
                       read-only entry is made writable (on POSIX its folder
                       too) and tried again, then once more after 0.2 s; a
                       \\\\?\\ path on Windows, so a path past 260 characters
                       is no obstacle; the whole folder is tried again after
                       waits of 0.05 s doubling to 1.6 s (about 3 s), for a
                       Windows hold on a fresh folder; a link or junction is removed as a
                       link, never followed. Never ignore_errors: what it
                       cannot remove is returned as [(path, reason)] and
                       printed as LEFTOVER lines.
  keep(path, why)      marks a folder kept (a failed run kept for
                       inspection): never pruned, never removed by Made;
                       taking it out is a person's call (gen-inhibit's
                       reviewer, 2026-11-01).
  state(path)          kept | live | finished | no-owner | other-host;
                       unsure is live. Only a finished folder is ever pruned.

Tests: tests/test_scratch_helper.py (with --can-fail).
"""

import atexit
import contextlib
import datetime
import json
import os
import secrets
import shutil
import socket
import stat
import sys
import time

OWNER_SUFFIX = ".owner.json"
# remove() tries a folder's whole tree again after each of these waits, about
# 3 s in all, as tests/_scratch.py does (gen-inhibit tester, 2026-11-01): a
# Windows hold on a fresh folder (WinError 32) can outlast the handler's own
# 0.2 s retry
BACKOFF = tuple(0.05 * 2 ** i for i in range(6))


class ScratchError(RuntimeError):
    pass


def worktree_of(path):
    """The git worktree holding path: the nearest folder upwards with a .git
    (a folder in a clone, a file in a worktree)."""
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise ScratchError("%s is in no git worktree, so there is no "
                               "scratch/ for it" % path)
        d = up


def scratch_root(start):
    return os.path.join(worktree_of(start), "scratch")


# ---- processes --------------------------------------------------------------
def process_start(pid):
    """When process pid started, as a number fixed for its life; None if no
    process pid is running; "?" if one may be but its start can't be read."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            # 87, ERROR_INVALID_PARAMETER: no such process; anything else
            # (5, access denied) means it may be there
            return None if ctypes.get_last_error() == 87 else "?"
        try:
            code = wintypes.DWORD()
            if k.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:
                return None                          # 259: STILL_ACTIVE
            t = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
                return "?"
            return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
        finally:
            k.CloseHandle(h)
    if os.path.isdir("/proc/self"):
        try:
            with open("/proc/%d/stat" % pid, encoding="ascii", errors="replace") as f:
                s = f.read()
        except FileNotFoundError:
            return None
        except OSError:
            return "?"
        # comm may hold spaces and brackets: the fields start after the last ')'
        fields = s[s.rfind(")") + 2:].split()
        if fields[:1] == ["Z"]:                      # exited, not yet reaped
            return None
        try:
            return int(fields[19])                   # field 22, starttime
        except (IndexError, ValueError):
            return "?"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError:
        pass
    return "?"


def owner_path(d):
    return d.rstrip("/\\") + OWNER_SUFFIX


def owner(d):
    try:
        with open(owner_path(d), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_owner(d):
    """Exclusive create: False if that owner file is already there."""
    rec = {"pid": os.getpid(), "start": process_start(os.getpid()),
           "host": socket.gethostname(), "platform": sys.platform,
           "made": datetime.datetime.now().isoformat(timespec="seconds"),
           "argv": [os.path.basename(a) for a in sys.argv[:1]] + sys.argv[1:]}
    try:
        with open(owner_path(d), "x", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, sort_keys=True)
            f.write("\n")
    except FileExistsError:
        return False
    return True


def keep(d, why):
    """Mark d kept (a failed run kept for inspection, say): its owner file
    says so, with why, and nothing in this module removes it again. Taking
    it out is a person's call: remove() by hand."""
    o = owner(d)
    if not isinstance(o, dict):
        raise ScratchError("%s has no owner file, so it is not this helper's to "
                           "keep" % d)
    o.update(keep=True, kept_why=why,
             kept_at=datetime.datetime.now().isoformat(timespec="seconds"))
    with open(owner_path(d), "w", encoding="utf-8", newline="\n") as f:
        json.dump(o, f, sort_keys=True)
        f.write("\n")


def state(d):
    """kept | live | finished | no-owner | other-host. Unsure counts as
    live."""
    o = owner(d)
    if not isinstance(o, dict) or not isinstance(o.get("pid"), int):
        return "no-owner"
    if o.get("keep"):
        return "kept"
    if o.get("host") != socket.gethostname() or \
            o.get("platform", sys.platform) != sys.platform:
        return "other-host"
    now = process_start(o["pid"])
    if now is None:
        return "finished"
    if now == "?" or o.get("start") in (None, "?"):
        return "live"
    return "live" if now == o.get("start") else "finished"


# ---- removal ----------------------------------------------------------------
def long_path(path):
    """path made absolute and, on Windows, in the \\\\?\\ form (\\\\?\\UNC\\ for a
    share), which has no 260-character limit."""
    p = os.path.abspath(path)
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _short(p):
    p = str(p)
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    return p[4:] if p.startswith("\\\\?\\") else p


def _is_link(p):
    return os.path.islink(p) or getattr(os.path, "isjunction", lambda _p: False)(p)


def _writable(p):
    """Clear the read-only flag on p and, on POSIX, make its folder writable."""
    try:
        mode = stat.S_IREAD | stat.S_IWRITE
        if os.path.isdir(p) and not _is_link(p):
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
        if fn in (os.open, os.scandir, os.lstat, os.stat, os.path.islink):
            failed.append((p, exc))
            return
        _writable(p)
        for wait in (0, 0.2):
            time.sleep(wait)
            try:
                fn(p)
                return
            except FileNotFoundError:
                return
            except OSError as e:
                exc = e
        failed.append((p, exc))
    return again


def _rmtree(path, again):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=again)
    else:
        shutil.rmtree(path, onerror=again)


def _unlink_link(p):
    """A link or junction, removed as itself; a Windows link to a folder (a
    junction included) goes with rmdir, which never touches its target."""
    if os.name == "nt" and os.path.isdir(p):
        os.rmdir(p)
    else:
        os.unlink(p)


def remove(path, quiet=False, out=None):
    """Remove path and its owner file. Returns [(path, reason)] for what is
    left, empty when it is all gone, and prints each as a LEFTOVER line
    (to out, a callable, default print) unless quiet. A path that is not
    there is nothing to do."""
    say = out or (lambda s: print(s, flush=True))
    lp = long_path(path)
    failed = []
    if not os.path.lexists(lp):
        pass
    elif _is_link(lp):
        try:
            _unlink_link(lp)
        except OSError as e:
            failed.append((lp, e))
    elif os.path.isdir(lp):
        # the whole tree again after each wait, for a hold that outlasts the
        # handler's own retry (Windows Search or Defender on a fresh folder)
        for wait in (0,) + BACKOFF:
            time.sleep(wait)
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
            if not os.path.lexists(lp):
                break
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
    else:
        own = long_path(owner_path(path))
        if os.path.lexists(own):
            try:
                os.unlink(own)
            except OSError as e:
                left.append((_short(own), "%s: %s" % (type(e).__name__, e)))
    if left and not quiet:
        for p, why in left[:20]:
            say("scratch: LEFTOVER %s -- %s" % (p, why))
        if len(left) > 20:
            say("scratch: LEFTOVER ... and %d more under %s" % (len(left) - 20, _short(lp)))
    return left


# ---- folders ----------------------------------------------------------------
def prune(parent, out=None):
    """Remove the finished folders in parent, with their owner files, and
    owner files left with no folder. Returns the paths removed."""
    gone = []
    try:
        names = sorted(os.listdir(parent))
    except FileNotFoundError:
        return gone
    for n in names:
        p = os.path.join(parent, n)
        if n.endswith(OWNER_SUFFIX):
            d = p[:-len(OWNER_SUFFIX)]
            if not os.path.lexists(d) and state(d) == "finished":
                remove(d, out=out)
            continue
        if not os.path.isdir(p):
            continue
        if state(p) == "finished":
            if not remove(p, out=out):
                gone.append(p)
    return gone


def make(parent, name, out=None):
    """A new folder <parent>/<name>-<pid>-<hex6>, with its owner file beside
    it (written first, so the folder never lacks one), after removing the
    finished folders in parent."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("name must be one plain folder name: %r" % (name,))
    prune(parent, out=out)
    os.makedirs(parent, exist_ok=True)
    while True:
        d = os.path.join(parent, "%s-%d-%s" % (name, os.getpid(), secrets.token_hex(3)))
        if os.path.lexists(d) or not _write_owner(d):
            continue
        os.mkdir(d)
        return d


@contextlib.contextmanager
def run(parent, name):
    """with run(parent, name) as d: ...  removed in a finally."""
    p = make(parent, name)
    try:
        yield p
    finally:
        remove(p)


class Made:
    """The folders one run made. remove_all() in the run's finally; the exit
    handler calls it again on any other way out (sys.exit, an uncaught
    exception), in the process that made it only. A folder marked with
    keep() is left, and listed in .kept."""

    def __init__(self):
        self.paths = []
        self.kept = []
        self._pid = os.getpid()
        atexit.register(self.remove_all)

    def add(self, path):
        self.paths.append(path)
        return path

    def remove_all(self, out=None):
        """Remove every folder but the kept ones, the newest first. Returns
        the LEFTOVER (path, reason) pairs; what is left stays listed."""
        if os.getpid() != self._pid:
            return []
        left = []
        for p in reversed(self.paths):
            if state(p) == "kept":
                if p not in self.kept:
                    self.kept.append(p)
                continue
            left += remove(p, out=out)
        self.paths = [p for p in self.paths
                      if p not in self.kept and os.path.lexists(long_path(p))]
        return left
