#!/usr/bin/env python3
"""Tests gi_paths.py's scratch removal (AGENTS.md "Removing scratch", methodology
24e32e0, 2026-10-31): remove_tree, process_start, the owner files, finish, run_dir and
scratch()'s removal at exit. Gen-inhibit tester, 2026-10-31.

On a fake worktree in this run's own scratch/ folder:

  1. remove_tree removes a tree holding read-only files and a read-only folder (git for
     Windows' .git/objects), a lone file, a folder with no permissions at all (mode 000)
     holding another, and, on Windows, a path past MAX_PATH; a path that isn't there is
     already removed. An entry it can't remove is printed with its path and the reason,
     the call returns False without raising, and the rest goes. That entry is a file
     held open on Windows. Elsewhere a user can't make its own file in its own folder
     unremovable without root, so os.unlink and os.remove refuse it while the check runs,
     as Windows refuses a held file: a stand-in injected by this test (held()).
     Off Windows also (2026-11-12, the Linux fix): a folder the walk can't open even once
     writable (os.open refused by the test) is printed, False, no exception; a link in a
     read-only folder is removed without its target being chmod-ed; a folder whose parent
     isn't writable is not removed, and the parent, outside the path, keeps its mode.
  2. process_start: this process gives a number, the same each time; a child gives
     another, and None once it has exited, waited for or not (a zombie on Linux); pid 0
     and -1 give None.
  3. Owner files: scratch() writes one beside the run folder (nothing inside it), with
     this pid and start time; owner_state says running for it, finished for a pid that
     isn't running or one whose start differs (a reused pid), kept for a kept one, and
     None for no file or an unreadable one.
  4. remove_finished_siblings removes finished folders with their owner files, and
     leaves running, kept and ownerless ones; one it can't remove is printed and kept
     with its owner file.
  5. scratch() in a child process: its folder and owner file are gone after a normal
     end, sys.exit(3) and an uncaught exception; GI_KEEP_SCRATCH keeps them, says so and
     marks the owner file kept; a run killed outright (os._exit) leaves them, and the
     next run of the same name removes them; another name's folders are not touched.
  6. run_dir removes its folder at the end of the block and on an exception, keep=True
     keeps it; finish from another pid (a forked child) leaves the folder.

    python3.13 -B test_gi_scratch.py        # exit 0 only if every check passes
                                            # (py -3.14 on Windows)

Its mutants: test_gi_scratch_mutants.py. GI_PATHS_DIR runs another copy of gi_paths.py.
"""
import contextlib
import errno
import io
import json
import os
import re
import stat
import subprocess
import sys
import time
import gi_paths as real_gi_paths  # noqa: E402  this test's own scratch/ folder

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("GI_PATHS_DIR") or HERE
FAILS = []


def check(label, fn, detail=""):
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001  a crash is this check's FAIL
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def load():
    """SRC_DIR's gi_paths, imported fresh."""
    sys.modules.pop("gi_paths", None)
    sys.path.insert(0, SRC_DIR)
    try:
        import gi_paths
    finally:
        sys.path.remove(SRC_DIR)
    sys.modules["gi_paths"] = real_gi_paths
    return gi_paths


def child(code, env=None):
    """Run code in a fresh interpreter with SRC_DIR's gi_paths importable."""
    pre = "import os, sys; sys.path.insert(0, %r); import gi_paths; TOOL = %r\n" % (SRC_DIR, TOOL)
    e = {k: v for k, v in os.environ.items() if k != "GI_KEEP_SCRATCH"}
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", "-c", pre + code], capture_output=True, text=True, env=e)


def tree(base, ro_dir=True):
    """A small tree like a git clone's: read-only object files, a read-only folder."""
    obj = os.path.join(base, ".git", "objects", "ab")
    os.makedirs(obj)
    for n in ("cdef", "0123"):
        p = os.path.join(obj, n)
        with open(p, "wb") as fh:
            fh.write(b"blob")
        os.chmod(p, stat.S_IREAD)
    with open(os.path.join(base, "plain.txt"), "w") as fh:
        fh.write("x")
    if ro_dir:
        os.chmod(obj, stat.S_IREAD | stat.S_IEXEC)
    return base


def _refuse(path, fns, why):
    """While the block runs, each os function named in fns refuses a path whose last part
    is path's last part, raising PermissionError(EPERM) as the OS would."""
    real = {n: getattr(os, n) for n in fns}
    name = os.path.basename(path)

    def wrap(fn):
        def refusing(p, *a, **kw):
            if not isinstance(p, int) and os.path.basename(os.fsdecode(p)) == name:
                raise PermissionError(errno.EPERM, "Operation not permitted (%s)" % why, p)
            return fn(p, *a, **kw)
        return refusing

    @contextlib.contextmanager
    def cm():
        for n, fn in real.items():
            setattr(os, n, wrap(fn))
        try:
            yield
        finally:
            for n, fn in real.items():
                setattr(os, n, fn)
    return cm()


@contextlib.contextmanager
def held(path):
    """path can't be removed while the block runs: held open on Windows (no
    FILE_SHARE_DELETE); elsewhere os.unlink and os.remove refuse it (the docstring)."""
    if os.name == "nt":
        fh = open(path)
        try:
            yield
        finally:
            fh.close()
        return
    with _refuse(path, ("unlink", "remove"), "refused by the test, standing in for a held file"):
        yield


def unopenable(path):
    """Off Windows: os.open refuses folder path while the block runs, so the walk can't
    open it even once it is writable."""
    return _refuse(path, ("open",), "refused by the test: a folder the walk can't open")


def owner(gp, d, pid, start, keep=False):
    os.makedirs(os.path.dirname(gp.owner_path(d)), exist_ok=True)
    with open(gp.owner_path(d), "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "start": start, "made": "x", "keep": keep}, fh)


def call(fn):
    """(fn(), None), or (None, the exception): a raise is judged by a check, not a crash."""
    try:
        return fn(), None
    except Exception as e:      # noqa: BLE001
        return None, e


def gone(gp, d):
    return not os.path.lexists(d) and not os.path.lexists(gp.owner_path(d))


TOOL = None


def run_dir_checks(gp):
    buf = io.StringIO()
    with gp.run_dir("rd", start=TOOL) as rd:
        open(os.path.join(rd, "f"), "w").close()
        inside = os.path.isdir(rd) and os.path.exists(gp.owner_path(rd))
    check("run_dir: there in the block, gone after it", lambda: inside and gone(gp, rd), rd)
    try:
        with gp.run_dir("rd", start=TOOL) as rd2:
            raise KeyError("x")
    except KeyError:
        pass
    check("run_dir: gone after an exception in the block", lambda: gone(gp, rd2), rd2)
    buf = io.StringIO()
    sys_err, sys.stderr = sys.stderr, buf
    try:
        with gp.run_dir("rd", start=TOOL, keep=True) as rd3:
            pass
    finally:
        sys.stderr = sys_err
    check("run_dir keep=True: kept, and said", lambda: os.path.isdir(rd3) and gp.owner_state(rd3) == "kept"
          and "kept" in buf.getvalue(), buf.getvalue())
    d4 = gp.scratch("t", start=TOOL, at_exit=False)
    check("finish from another pid (a forked child) leaves the folder",
          lambda: gp.finish(d4, pid=os.getpid() + 1) is True and os.path.isdir(d4))
    check("finish: removed with its owner file", lambda: gp.finish(d4, out=buf) is True and gone(gp, d4))



def main():
    global TOOL
    with real_gi_paths.run_dir("gi-scratch") as work:
        return run(work)


def run(work):
    global TOOL
    gp = load()
    wt = os.path.join(work, "wt")
    TOOL = os.path.join(wt, "gen-inhibit", "tools", "bench", "tool.py")
    os.makedirs(os.path.dirname(TOOL))
    with open(os.path.join(wt, ".git"), "w") as fh:
        fh.write("gitdir: elsewhere\n")
    parent = os.path.join(wt, "scratch", "gen-inhibit-tests", "t")

    print("=== remove_tree ===")
    t = tree(os.path.join(work, "rm1"))
    buf = io.StringIO()
    check("a tree with read-only files and a read-only folder is removed, True, nothing printed",
          lambda: gp.remove_tree(t, out=buf) is True and not os.path.lexists(t) and buf.getvalue() == "",
          buf.getvalue())
    check("a path that isn't there is already removed", lambda: gp.remove_tree(t, out=buf) is True)
    f = os.path.join(work, "lone.txt")
    with open(f, "w") as fh:
        fh.write("x")
    os.chmod(f, stat.S_IREAD)
    check("a lone read-only file is removed", lambda: gp.remove_tree(f, out=buf) and not os.path.lexists(f))
    z = os.path.join(work, "rm000")
    deeper = os.path.join(z, "none", "deeper")
    os.makedirs(deeper)
    for p in (os.path.join(z, "none", "f.txt"), os.path.join(deeper, "g.txt"), os.path.join(z, "plain.txt")):
        with open(p, "w") as fh:
            fh.write("x")
    os.chmod(deeper, 0)
    os.chmod(os.path.dirname(deeper), 0)
    buf = io.StringIO()
    res, err = call(lambda: gp.remove_tree(z, out=buf))
    check("a folder with no permissions at all (mode 000) holding files and another such folder is removed, "
          "True, nothing printed", lambda: err is None and res is True and not os.path.lexists(z) and buf.getvalue() == "",
          (res, err, buf.getvalue()))
    if os.path.lexists(z):
        for p in (os.path.dirname(deeper), deeper):
            try:
                os.chmod(p, stat.S_IRWXU)
            except OSError:
                pass
        real_gi_paths.remove_tree(z, out=buf)
    if os.name != "nt":
        u = os.path.join(work, "rm-open")
        locked = os.path.join(u, "locked")
        os.makedirs(locked)
        for p in (os.path.join(locked, "f.txt"), os.path.join(u, "plain.txt")):
            with open(p, "w") as fh:
                fh.write("x")
        buf = io.StringIO()
        with unopenable(locked):
            res, err = call(lambda: gp.remove_tree(u, out=buf))
        check("a folder the walk can't open even once writable: False, no exception, printed with its path",
              lambda: err is None and res is False and locked in buf.getvalue()
              and not os.path.lexists(os.path.join(u, "plain.txt")), (res, err, buf.getvalue()))
        check("... and once it opens, it goes", lambda: gp.remove_tree(u, out=buf) and not os.path.lexists(u))
        target = os.path.join(work, "link-target.txt")
        with open(target, "w") as fh:
            fh.write("x")
        os.chmod(target, stat.S_IREAD)
        lt = tree(os.path.join(work, "rm-link"), ro_dir=False)
        obj = os.path.join(lt, ".git", "objects", "ab")
        os.symlink(target, os.path.join(obj, "link"))
        os.chmod(obj, stat.S_IREAD | stat.S_IEXEC)
        buf = io.StringIO()
        res, err = call(lambda: gp.remove_tree(lt, out=buf))
        check("a link in a read-only folder is removed, and its target outside keeps its mode (never chmod-ed)",
              lambda: err is None and res is True and not os.path.lexists(lt) and os.path.exists(target)
              and stat.S_IMODE(os.stat(target).st_mode) == stat.S_IREAD,
              (res, err, oct(stat.S_IMODE(os.stat(target).st_mode)), buf.getvalue()))
        ro_parent = os.path.join(work, "ro-parent")
        inner = tree(os.path.join(ro_parent, "inner"))
        os.chmod(ro_parent, stat.S_IREAD | stat.S_IEXEC)
        buf = io.StringIO()
        try:
            res, err = call(lambda: gp.remove_tree(inner, out=buf))
            mode = stat.S_IMODE(os.stat(ro_parent).st_mode)
        finally:
            os.chmod(ro_parent, stat.S_IRWXU)
        check("a folder whose parent isn't writable: not removed, False, printed, no exception",
              lambda: err is None and res is False and os.path.isdir(inner) and buf.getvalue().startswith(
                  "could not remove %s" % inner), (res, err, buf.getvalue()))
        check("... and the parent, outside the path, keeps its mode (nothing outside it is chmod-ed)",
              lambda: mode == stat.S_IREAD | stat.S_IEXEC, oct(mode))
        real_gi_paths.remove_tree(ro_parent, out=buf)
    else:
        print("n/a   the walk's open refused, a link's target, an unwritable parent: off Windows only "
              "(Windows' rmtree walks by path, and a folder's read-only flag doesn't stop removal)")
    # Python before 3.12 (WSL's 3.10, where the C suites' runners run) has no onexc, only onerror
    real_rmtree = gp.shutil.rmtree

    def old_rmtree(path, ignore_errors=False, onerror=None, **kw):
        if kw:
            raise TypeError("rmtree() got an unexpected keyword argument '%s'" % next(iter(kw)))
        return real_rmtree(path, ignore_errors, onerror)
    gp.shutil.rmtree = old_rmtree
    try:
        t = tree(os.path.join(work, "rm-old"))
        buf = io.StringIO()
        res, err = call(lambda: gp.remove_tree(t, out=buf))
        check("an rmtree with onerror only (Python < 3.12): the read-only tree is removed, True, nothing printed",
              lambda: err is None and res is True and not os.path.lexists(t) and buf.getvalue() == "", (res, err))
        t2 = tree(os.path.join(work, "rm-old2"), ro_dir=False)
        held2 = os.path.join(t2, "held.txt")
        open(held2, "w").close()
        buf = io.StringIO()
        with held(held2):
            res, err = call(lambda: gp.remove_tree(t2, out=buf))
        check("... and what it can't remove is printed with the reason, False, no exception",
              lambda: err is None and res is False and "held.txt" in buf.getvalue(), (res, err, buf.getvalue()))
    finally:
        gp.shutil.rmtree = real_rmtree
    gp.remove_tree(os.path.join(work, "rm-old2"), out=buf)
    if os.name == "nt":
        deep = os.path.join(work, "deep")
        p = deep
        while len(p) < 300:
            p = os.path.join(p, "d" * 40)
        os.makedirs(gp.long_path(p))
        with open(gp.long_path(os.path.join(p, "f.txt")), "w") as fh:
            fh.write("x")
        check("a path past MAX_PATH is removed (\\\\?\\)",
              lambda: gp.remove_tree(deep, out=buf) and not os.path.lexists(gp.long_path(deep)), len(p))
        check("long_path: \\\\?\\ for a drive path, \\\\?\\UNC\\ for a share, unchanged if already long",
              lambda: gp.long_path("C:\\a") == "\\\\?\\C:\\a" and gp.long_path("\\\\srv\\sh\\a") == "\\\\?\\UNC\\srv\\sh\\a"
              and gp.long_path("\\\\?\\C:\\a") == "\\\\?\\C:\\a")
    stuck = os.path.join(work, "stuck")
    tree(stuck, ro_dir=False)
    held_f = os.path.join(stuck, "sub", "held.txt")
    os.makedirs(os.path.dirname(held_f))
    with open(held_f, "w") as fh:
        fh.write("x")
    buf = io.StringIO()
    with held(held_f):
        res, err = call(lambda: gp.remove_tree(stuck, out=buf))
    msg = buf.getvalue()
    check("an entry it can't remove: False, no exception", lambda: err is None and res is False, (res, err))
    check("... printed as 'could not remove <folder>' with the entry's path and reason",
          lambda: msg.startswith("could not remove %s" % stuck) and "held.txt" in msg and "Error" in msg, msg)
    check("... and the rest is removed", lambda: os.path.exists(held_f)
          and not os.path.exists(os.path.join(stuck, ".git")) and not os.path.exists(os.path.join(stuck, "plain.txt")))
    check("... and once free, it goes", lambda: gp.remove_tree(stuck, out=buf) and not os.path.lexists(stuck))

    print("\n=== process_start ===")
    me = gp.process_start(os.getpid())
    check("this process: a number, the same each time", lambda: isinstance(me, int) and gp.process_start(os.getpid()) == me, me)
    p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    kid = gp.process_start(p.pid)
    p.stdin.close()
    p.wait()
    check("a running child: another number", lambda: isinstance(kid, int) and kid != me, kid)
    check("the child once it has exited: None", lambda: gp.process_start(p.pid) is None)
    check("pid 0 and -1: None", lambda: gp.process_start(0) is None and gp.process_start(-1) is None)
    # exited but not yet waited for (2026-11-12): on Linux a zombie, whose /proc entry stays
    z = subprocess.Popen([sys.executable, "-c", "pass"])
    for _ in range(100):
        if os.name == "nt":
            time.sleep(1.0)
            break
        try:
            with open("/proc/%d/stat" % z.pid) as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
        except OSError:
            break
        time.sleep(0.05)
    zs = gp.process_start(z.pid)
    z.wait()
    check("a child that has exited but not been waited for (a zombie on Linux): None", lambda: zs is None, zs)

    print("\n=== owner files ===")
    d, err = call(lambda: gp.scratch("t", start=TOOL, at_exit=False))
    check("scratch() makes a run folder", lambda: err is None and os.path.isdir(d), err)
    if err is not None:                     # the rest still runs, on a folder of its own
        d = os.path.join(parent, "made-by-the-test")
        os.makedirs(d, exist_ok=True)
    o = json.load(open(gp.owner_path(d), encoding="utf-8")) if os.path.exists(gp.owner_path(d)) else {}
    check("scratch() writes <run folder>.owner.json beside the folder",
          lambda: os.path.isfile(os.path.join(parent, os.path.basename(d) + ".owner.json")), d)
    check("... nothing inside the folder", lambda: os.listdir(d) == [], os.listdir(d))
    check("... with this pid and its start time", lambda: o.get("pid") == os.getpid() and o.get("start") == me
          and o.get("keep") is False, o)
    check("owner_state: running for this process", lambda: gp.owner_state(d) == "running", gp.owner_state(d))
    x = os.path.join(work, "own", "x")
    os.makedirs(os.path.dirname(x))
    owner(gp, x, p.pid, kid)
    check("finished: a pid that isn't running", lambda: gp.owner_state(x) == "finished", gp.owner_state(x))
    owner(gp, x, os.getpid(), me + 1)
    check("finished: this pid with another start (the pid reused)", lambda: gp.owner_state(x) == "finished")
    owner(gp, x, p.pid, kid, keep=True)
    check("kept: a kept one, even finished", lambda: gp.owner_state(x) == "kept")
    owner(gp, x, os.getpid(), None)
    check("running: a live pid whose start wasn't read", lambda: gp.owner_state(x) == "running")
    with open(gp.owner_path(x), "w") as fh:
        fh.write('{"pid": ')
    check("None: an unreadable owner file", lambda: gp.owner_state(x) is None)
    os.remove(gp.owner_path(x))
    check("None: no owner file", lambda: gp.owner_state(x) is None)
    gp.finish(d, keep=False, out=buf)

    print("\n=== remove_finished_siblings ===")
    sib = os.path.join(work, "sib")
    mk = {}
    for name, (pid, start, keep) in {"done": (p.pid, kid, False), "reused": (os.getpid(), me + 1, False),
                                     "live": (os.getpid(), me, False), "kept": (p.pid, kid, True)}.items():
        mk[name] = tree(os.path.join(sib, name))
        owner(gp, mk[name], pid, start, keep)
    mk["noowner"] = tree(os.path.join(sib, "noowner"))
    mk["stuck"] = os.path.join(sib, "stuck")
    os.makedirs(os.path.join(mk["stuck"], "sub"))
    held_f = os.path.join(mk["stuck"], "sub", "held.txt")
    open(held_f, "w").close()
    owner(gp, mk["stuck"], p.pid, kid)
    owner(gp, os.path.join(sib, "orphan"), p.pid, kid)       # an owner file whose folder is gone
    buf = io.StringIO()
    with held(held_f):
        removed, err = call(lambda: gp.remove_finished_siblings(sib, out=buf))
    names = sorted(os.path.basename(r) for r in removed or [])
    check("remove_finished_siblings raises nothing", lambda: err is None, err)
    check("finished ones removed with their owner files (and an owner file with no folder)",
          lambda: names == ["done", "orphan", "reused"] and gone(gp, mk["done"]) and gone(gp, mk["reused"])
          and not os.path.lexists(os.path.join(sib, "orphan.owner.json")), names)
    check("running, kept and ownerless ones left", lambda: all(os.path.isdir(mk[k]) for k in ("live", "kept", "noowner"))
          and os.path.exists(gp.owner_path(mk["live"])) and os.path.exists(gp.owner_path(mk["kept"])))
    check("one it can't remove: printed, kept with its owner file",
          lambda: "could not remove" in buf.getvalue() and os.path.exists(gp.owner_path(mk["stuck"])), buf.getvalue())
    check("a parent that isn't there: nothing", lambda: gp.remove_finished_siblings(os.path.join(work, "none")) == [])

    print("\n=== scratch() removal at exit ===")
    show = "d = gi_paths.scratch('t', start=TOOL); open(os.path.join(d, 'f'), 'w').write('x'); print(d, flush=True)\n"
    for label, tail, rc in (("a normal end", "", 0), ("sys.exit(3)", "sys.exit(3)\n", 3),
                            ("an uncaught exception", "raise RuntimeError('boom')\n", 1)):
        r = child(show + tail)
        d = r.stdout.strip()
        check("after %s: the folder and its owner file are gone" % label,
              lambda: r.returncode == rc and d and gone(gp, d) and d.startswith(parent), (r.returncode, d, r.stderr[-200:]))
    r = child(show, {"GI_KEEP_SCRATCH": "1"})
    d = r.stdout.strip()
    kept = json.load(open(gp.owner_path(d), encoding="utf-8")) if d and os.path.exists(gp.owner_path(d)) else {}
    check("GI_KEEP_SCRATCH keeps both, says so, marks the owner file kept",
          lambda: os.path.isdir(d) and kept.get("keep") is True and ("kept %s" % d) in r.stderr, (d, r.stderr[-200:]))
    r = child(show + "os._exit(9)\n")
    killed = r.stdout.strip()
    other = child("d = gi_paths.scratch('u', start=TOOL); print(d, flush=True); os._exit(9)\n").stdout.strip()
    check("killed outright: the folder and owner file are left",
          lambda: os.path.isdir(killed) and os.path.exists(gp.owner_path(killed)), killed)
    r = child(show)
    check("the next run of the same name removes the killed run's folder, and its own at exit",
          lambda: r.returncode == 0 and gone(gp, killed) and gone(gp, r.stdout.strip()), r.stderr[-200:])
    check("... and leaves the kept one, and another name's", lambda: os.path.isdir(d) and os.path.isdir(other))
    check("a run folder's name is <time>_<pid>", lambda: re.fullmatch(r"\d{8}T\d{6}_\d+", os.path.basename(killed)))

    print("\n=== run_dir and finish ===")
    _, err = call(lambda: run_dir_checks(gp))
    check("the run_dir and finish checks ran to the end, raising nothing", lambda: err is None, err)

    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
