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
     this pid, start time and host; owner_state says running for it, finished for a pid
     that isn't running or one whose start differs (a reused pid), kept for a kept one,
     and None for no file, an unreadable one or another host's.
  4. remove_finished_siblings removes finished folders with their owner files, and
     owner files whose folder is gone; it leaves running, kept, ownerless and other
     hosts' ones; one it can't remove is printed and kept with its owner file.
  5. scratch() in a child process: its folder and owner file are gone after a normal
     end, sys.exit(3) and an uncaught exception; GI_KEEP_SCRATCH keeps them, says so and
     marks the owner file kept; a run killed outright (os._exit) leaves them, and the
     next run of the same name removes them; another name's folders are not touched.
     A run folder's name is <time>-<pid>-<hex6>.
  6. run_dir removes its folder at the end of the block and on an exception, keep=True
     keeps it; finish from another pid (a forked child) leaves the folder.
  7. Where the shared helper comes from (port step 4 item 3; the gen-inhibit reviewer's
     ruling of 2026-11-26): in this worktree, the pinned checkout's
     vtrux_bench/scratch.py; a copy in a worktree with a staged file and a valid record
     beside it still takes the pinned one, and when gi_core_pin refuses it is refused
     and the staged file is not used. A copy with no git (stood in for: load_copy) is
     refused with nothing staged, and with a staged file but no record; stage_core() stages the pinned file byte for byte with
     a record of the pin and the sha256, and the copy then loads it and removes through
     it; a staged file changed after staging, or a record with a bad sha256, is refused.
     stage_core at a commit stages that commit's pin from git, and refuses a commit with
     no shared/bench pin.

Since port step 4 item 3 the removal itself is the shared helper's, and its own tests
(shared/bench tests/test_scratch_helper.py, with --can-fail) carry its mechanics; these
checks are gen-inhibit's rules for it (AGENTS.md "Removing scratch").

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
import shutil
import socket
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


def owner(gp, d, pid, start, keep=False, host=None):
    """An owner file in the shared helper's form (pid, start, host, platform)."""
    os.makedirs(os.path.dirname(gp.owner_path(d)), exist_ok=True)
    with open(gp.owner_path(d), "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "start": start, "made": "x", "keep": keep,
                   "host": host or socket.gethostname(), "platform": sys.platform}, fh)


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



COPIES = []


def load_copy(folder, in_worktree=None):
    """folder/gi_paths.py, loaded as a module of its own; in_worktree, if given, stands
    in for its finding of a .git upwards (a copy with no git can't be made inside this
    worktree's scratch/, and nowhere else is ours to write)."""
    import importlib.util
    name = "gi_paths_copy_%d" % len(COPIES)
    spec = importlib.util.spec_from_file_location(name, os.path.join(folder, "gi_paths.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.IN_WORKTREE = in_worktree
    COPIES.append(mod)
    return mod


FAKE_CORE = b"MARK = 'the staged file'\nOWNER_SUFFIX = '.owner.json'\n"


def stage_fake(folder, gp, data=FAKE_CORE, sha=None):
    """A staged helper and a record that matches it (or gives sha)."""
    with open(os.path.join(folder, gp.CORE_STAGED), "wb") as fh:
        fh.write(data)
    with open(os.path.join(folder, gp.CORE_RECORD), "w", encoding="utf-8") as fh:
        json.dump({"sha256": sha or __import__("hashlib").sha256(data).hexdigest()}, fh)


def source_checks(gp, work):
    """Where the shared helper comes from (port step 4 item 3; gen-inhibit reviewer's
    ruling, 2026-11-26)."""
    import hashlib
    GCP = gp.core_pin()
    pin = GCP.check()
    pinned = os.path.join(pin["folder"], *gp.CORE_FILE.split("/"))
    with open(pinned, "rb") as fh:
        pinned_bytes = fh.read()
    S = gp.scratch_core()
    check("in this worktree: the helper is the pinned checkout's vtrux_bench/scratch.py",
          lambda: os.path.samefile(S.__file__, pinned) and "shared-%s" % pin["tag"] in S.__file__, S.__file__)

    # a copy in this worktree with a staged file and a valid record beside it: still the pin
    cp = os.path.join(work, "copy-wt")
    os.makedirs(cp)
    for f in ("gi_paths.py", "gi_core_pin.py"):
        shutil.copyfile(os.path.join(SRC_DIR if f == "gi_paths.py" else HERE, f), os.path.join(cp, f))
    stage_fake(cp, gp)
    c1 = load_copy(cp)
    S1, err = call(c1.scratch_core)
    check("in a worktree with a staged file and a valid record beside it: the pinned helper, not the staged one",
          lambda: err is None and not hasattr(S1, "MARK") and os.path.samefile(S1.__file__, pinned), (err, S1))

    # the same copy when gi_core_pin refuses: refused, and the staged file is still not used
    class Refusing:
        class Refused(Exception):
            pass

        def use_bench(self, start=None):
            raise self.Refused("refused by the test: the pin check fails")
    saved = sys.modules.get("gi_core_pin")
    sys.modules["gi_core_pin"] = Refusing()
    try:
        c2 = load_copy(cp)
        S2, err = call(c2.scratch_core)
    finally:
        if saved is None:
            sys.modules.pop("gi_core_pin", None)
        else:
            sys.modules["gi_core_pin"] = saved
    check("in a worktree whose pin check refuses: a PathsError naming the refusal, the staged file not used",
          lambda: S2 is None and isinstance(err, c2.PathsError) and "refused by the test" in str(err), (S2, err))

    # copies with no git
    nogit = os.path.join(work, "copy-nogit")
    os.makedirs(nogit)
    shutil.copyfile(os.path.join(SRC_DIR, "gi_paths.py"), os.path.join(nogit, "gi_paths.py"))
    c3 = load_copy(nogit, in_worktree=False)
    S3, err = call(c3.scratch_core)
    check("no git, nothing staged: a PathsError naming the record", lambda: S3 is None
          and isinstance(err, c3.PathsError) and c3.CORE_RECORD in str(err), (S3, err))
    with open(os.path.join(nogit, gp.CORE_STAGED), "wb") as fh:
        fh.write(FAKE_CORE)
    c3b = load_copy(nogit, in_worktree=False)
    S3b, err = call(c3b.scratch_core)
    check("no git, a staged file with no record beside it: a PathsError naming the record, the file not loaded",
          lambda: S3b is None and isinstance(err, c3b.PathsError) and c3b.CORE_RECORD in str(err), (S3b, err))
    rec, err = call(lambda: gp.stage_core(nogit))
    check("stage_core(dest): the pinned checkout's scratch.py, byte for byte, beside its record",
          lambda: err is None and open(os.path.join(nogit, gp.CORE_STAGED), "rb").read() == pinned_bytes, err)
    check("... the record names shared/bench, the pin's tag and commit, the file, and the bytes' sha256",
          lambda: rec == {"component": "shared/bench", "tag": pin["tag"], "commit": pin["commit"],
                          "file": "vtrux_bench/scratch.py", "sha256": hashlib.sha256(pinned_bytes).hexdigest()}
          and json.load(open(os.path.join(nogit, gp.CORE_RECORD), encoding="utf-8")) == rec, rec)
    c4 = load_copy(nogit, in_worktree=False)
    S4, err = call(c4.scratch_core)
    check("no git, staged with a matching record: the staged file is the helper",
          lambda: err is None and os.path.samefile(S4.__file__, os.path.join(nogit, gp.CORE_STAGED)), (err, S4))
    t = tree(os.path.join(work, "rm-staged"))
    buf = io.StringIO()
    check("... and remove_tree works through it", lambda: c4.remove_tree(t, out=buf) is True and not os.path.lexists(t),
          buf.getvalue())
    with open(os.path.join(nogit, gp.CORE_STAGED), "ab") as fh:
        fh.write(b"\n# one more line\n")
    c5 = load_copy(nogit, in_worktree=False)
    S5, err = call(c5.scratch_core)
    check("no git, the staged file changed after staging: a PathsError naming both sha256s",
          lambda: S5 is None and isinstance(err, c5.PathsError) and rec["sha256"] in str(err), (S5, err))
    stage_fake(nogit, gp, sha="ABC")
    c6 = load_copy(nogit, in_worktree=False)
    S6, err = call(c6.scratch_core)
    check("no git, a record whose sha256 is not 64 hex digits: a PathsError", lambda: S6 is None
          and isinstance(err, c6.PathsError), (S6, err))
    stage_fake(nogit, gp)
    c7 = load_copy(nogit, in_worktree=False)
    S7, err = call(c7.scratch_core)
    check("no git, a staged file with a matching record: that file is loaded (the check is the sha256, "
          "nothing else)", lambda: err is None and getattr(S7, "MARK", None) == "the staged file", (err, S7))

    # stage_core at a commit (H's): the shared/bench pin that commit gives, from git
    at = os.path.join(work, "staged-at")
    rec, err = call(lambda: gp.stage_core(at, STAGE_AT))
    want = os.path.join(gp.worktree_of(HERE), os.pardir, "shared-%s" % STAGE_AT_TAG, *"shared/bench/vtrux_bench/scratch.py".split("/"))
    check("stage_core(dest, %s): that commit's pin (%s), its scratch.py from git, byte for byte" % (STAGE_AT[:8], STAGE_AT_TAG),
          lambda: err is None and rec["tag"] == STAGE_AT_TAG and rec["commit"] == STAGE_AT_COMMIT
          and open(os.path.join(at, gp.CORE_STAGED), "rb").read() == open(want, "rb").read(), (err, rec))
    rec, err = call(lambda: gp.stage_core(os.path.join(work, "staged-none"), NO_PIN_AT))
    check("stage_core(dest, %s), a commit with no shared/bench pin: a PathsError, nothing staged" % NO_PIN_AT[:8],
          lambda: rec is None and isinstance(err, gp.PathsError)
          and not os.path.lexists(os.path.join(work, "staged-none")), (rec, err))


# A commit whose shared/bench pin is 2026.11.26 (item 3's pin move), and one from before
# gen-inhibit pinned shared/bench at all (port step 4 item 1's base).
STAGE_AT, STAGE_AT_TAG = "bbedbdbb", "2026.11.26"
STAGE_AT_COMMIT = "2986c5e0edb26041c57fa20cd83d991525c92ee1"
NO_PIN_AT = "4c3afb4d"


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
    # No check of an rmtree with onerror only (Python < 3.12) since port step 4 item 3: the
    # shared helper chooses onexc or onerror by the Python version, not by a TypeError, and
    # the bench tooling runs on python3.13 only (user, 2026-11-12).
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
    check("... with this pid, its start time and this host, not kept", lambda: o.get("pid") == os.getpid()
          and o.get("start") == me and o.get("host") == socket.gethostname() and not o.get("keep"), o)
    check("gi_paths' OWNER_SUFFIX is the shared helper's",
          lambda: gp.OWNER_SUFFIX == gp.scratch_core().OWNER_SUFFIX == ".owner.json")
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
    owner(gp, x, p.pid, kid, host="another-host")
    check("None: another host's owner file, even with a finished pid (not ours to judge)",
          lambda: gp.owner_state(x) is None, gp.owner_state(x))
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
    mk["otherhost"] = tree(os.path.join(sib, "otherhost"))
    owner(gp, mk["otherhost"], p.pid, kid, host="another-host")
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
          lambda: names == ["done", "reused"] and gone(gp, mk["done"]) and gone(gp, mk["reused"])
          and not os.path.lexists(os.path.join(sib, "orphan.owner.json")), names)
    check("running, kept, ownerless and another host's ones left",
          lambda: all(os.path.isdir(mk[k]) for k in ("live", "kept", "noowner", "otherhost"))
          and all(os.path.exists(gp.owner_path(mk[k])) for k in ("live", "kept", "otherhost")))
    check("one it can't remove: printed with the entry's path, kept with its owner file",
          lambda: held_f in buf.getvalue() and os.path.isdir(mk["stuck"]) and os.path.exists(gp.owner_path(mk["stuck"])),
          buf.getvalue())
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
    check("a run folder's name is <time>-<pid>-<hex6> (the shared helper's make())",
          lambda: re.fullmatch(r"\d{8}T\d{6}-\d+-[0-9a-f]{6}", os.path.basename(killed)), killed)

    print("\n=== run_dir and finish ===")
    _, err = call(lambda: run_dir_checks(gp))
    check("the run_dir and finish checks ran to the end, raising nothing", lambda: err is None, err)

    print("\n=== where the shared helper comes from ===")
    _, err = call(lambda: source_checks(gp, work))
    check("the source checks ran to the end, raising nothing", lambda: err is None, err)

    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
