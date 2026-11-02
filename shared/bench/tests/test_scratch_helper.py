"""Tests of vtrux_bench/scratch.py, the one shared scratch helper (ACCEPT-SPEC
A14; AGENTS.md "Removing scratch"). No hardware. Each test names the
requirement it checks.

    python -B shared/bench/tests/test_scratch_helper.py             every test
    python -B shared/bench/tests/test_scratch_helper.py --can-fail  every test
        on the unmutated module (the control, which must pass), then again
        under each mutant of scratch.py, each of which must make one FAIL

Native Windows is where it counts (the runner's machine, NICKY-XPS). On POSIX
it needs a native file system that enforces folder permissions, and not as
root; WSL's /mnt/c does not, and test_leftover_reported says so.

The module under test is loaded by its file path, so a mutant (an edited copy
of scratch.py in this run's folder) reaches the tests and the subprocesses
they start. An edit whose text is not found exactly once counts as a failure,
never a silent no-op. Every test works in this run's own folder under the
worktree's scratch/ (_scratch.py), removed at the end with the unmutated
helper; what it cannot remove is printed.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _scratch                                     # noqa: E402

PY = sys.executable
MODULE = os.path.join(os.path.dirname(HERE), "vtrux_bench", "scratch.py")
WINDOWS = os.name == "nt"


def load(path, name="scratch_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = load(MODULE, "scratch_good")


def lp(p):
    """p in the long form on Windows, made here without the module under test."""
    p = os.path.abspath(p)
    return "\\\\?\\" + p if WINDOWS and not p.startswith("\\\\?\\") else p


def child(mod_path, code, cwd):
    """code in a fresh interpreter with `S` the module at mod_path; its
    result (rc, last stdout line or None, stdout + stderr)."""
    pre = ("import importlib.util, sys\n"
           "sp = importlib.util.spec_from_file_location('S', %r)\n"
           "S = importlib.util.module_from_spec(sp); sp.loader.exec_module(S)\n" % mod_path)
    r = subprocess.run([PY, "-B", "-c", pre + code], capture_output=True, text=True,
                       cwd=cwd, timeout=120)
    lines = r.stdout.strip().splitlines()
    return r.returncode, (lines[-1] if lines else None), r.stdout + r.stderr


def plant_git_objects(d):
    """A folder like a clone's .git/objects: read-only files in read-only
    folders, as Git for Windows leaves them."""
    o = os.path.join(d, ".git", "objects", "ab")
    os.makedirs(o)
    for n in ("cdef0123", "4567"):
        with open(os.path.join(o, n), "wb") as f:
            f.write(b"x" * 64)
        os.chmod(os.path.join(o, n), 0o444)
    os.chmod(o, 0o555)
    os.chmod(os.path.dirname(o), 0o555)


# ---- the tests -------------------------------------------------------------
def test_remove_readonly(S, work):
    """A14: a folder holding read-only files in read-only folders (a clone's
    .git/objects) is removed, and remove() reports nothing left."""
    d = os.path.join(work, "ro")
    os.makedirs(d)
    plant_git_objects(d)
    left = S.remove(d, quiet=True)
    assert left == [], left
    assert not os.path.lexists(d), "still there: %s" % d


def test_remove_long_path(S, work):
    """A14: a file whose path is past 260 characters is removed (Windows: the
    \\\\?\\ form)."""
    d = os.path.join(work, "long")
    deep = d
    while len(deep) < 300:
        deep = os.path.join(deep, "a_fairly_long_folder_name_%02d" % (len(deep) % 97))
    os.makedirs(lp(deep))
    with open(lp(os.path.join(deep, "f.txt")), "w") as f:
        f.write("x")
    left = S.remove(d, quiet=True)
    assert left == [], left
    assert not os.path.lexists(lp(d)), "still there: %s" % d


def test_leftover_reported(S, work):
    """A14: what cannot be removed is never passed over: remove() returns it
    with the reason and prints a LEFTOVER line naming it (never
    ignore_errors). Made by holding a file open (Windows) or by a folder the
    remover may not write (POSIX, not root)."""
    d = os.path.join(work, "held")
    os.makedirs(os.path.join(d, "sub"))
    f = os.path.join(d, "sub", "busy.txt")
    with open(f, "w") as fh:
        fh.write("x")
    printed = []
    if WINDOWS:
        fh = open(f)                                  # no FILE_SHARE_DELETE
        try:
            left = S.remove(d, out=printed.append)
        finally:
            fh.close()
    else:
        if os.geteuid() == 0:
            raise AssertionError("cannot be tested as root")
        os.chmod(os.path.join(d, "sub"), 0o555)
        os.chmod(d, 0o555)
        try:
            open(os.path.join(d, "sub", "probe"), "w").close()
            os.chmod(d, 0o755)
            os.chmod(os.path.join(d, "sub"), 0o755)
            raise AssertionError("cannot be tested here: this file system does not "
                                 "enforce folder permissions (WSL's /mnt/c?); run from "
                                 "a native Linux file system")
        except PermissionError:
            pass
        real = S._writable
        S._writable = lambda p: None                  # the remover may not fix it
        try:
            left = S.remove(d, out=printed.append)
        finally:
            S._writable = real
            os.chmod(d, 0o755)
            os.chmod(os.path.join(d, "sub"), 0o755)
    try:
        assert left, "remove() reported nothing left, but %s is there: %s" % (
            d, os.path.lexists(d))
        assert any("busy.txt" in p for p, _why in left), left
        assert all(why for _p, why in left), left
        text = "\n".join(printed)
        assert "LEFTOVER" in text and "busy.txt" in text, printed
    finally:
        GOOD.remove(d, quiet=True)


def test_link_not_followed(S, work):
    """A14: a link (a junction on Windows) inside a folder, or the folder
    itself, is removed as a link: what it points at is untouched."""
    target = os.path.join(work, "target")
    os.makedirs(target)
    keep = os.path.join(target, "keep.txt")
    with open(keep, "w") as fh:
        fh.write("keep")
    d = os.path.join(work, "holder")
    os.makedirs(d)
    links = [os.path.join(d, "inner"), os.path.join(work, "outer")]
    for ln in links:
        if WINDOWS:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", ln, target],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
        else:
            os.symlink(target, ln)
    assert S.remove(d, quiet=True) == [], "inner"
    assert S.remove(links[1], quiet=True) == [], "outer"
    assert not os.path.lexists(d) and not os.path.lexists(links[1])
    assert os.path.isfile(keep), "the link's target was emptied"


def test_owner_beside(S, work):
    """A14 (and A4: a tree folder is a clone that must stay clean): make()
    writes the owner file beside the folder, <folder>.owner.json, with this
    process's pid and start, and nothing inside the folder."""
    d = S.make(os.path.join(work, "own"), "tree")
    assert os.path.isdir(d), d
    assert os.listdir(d) == [], os.listdir(d)
    with open(d + ".owner.json", encoding="utf-8") as fh:
        o = json.load(fh)
    assert o["pid"] == os.getpid(), o
    assert o["start"] == S.process_start(os.getpid()) and o["start"] is not None, o
    assert o["host"] == socket.gethostname(), o
    assert S.state(d) == "live", S.state(d)


def test_state(S, work):
    """A14: state() is live only for the very process that made the folder:
    an exited pid, a pid now another process's (another start time) and a
    folder with no owner are told apart; another host's is never finished."""
    parent = os.path.join(work, "st")
    os.makedirs(parent)
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()

    def mk(name, **o):
        d = os.path.join(parent, name)
        os.makedirs(d)
        rec = dict(pid=os.getpid(), start=S.process_start(os.getpid()),
                   host=socket.gethostname(), platform=sys.platform)
        rec.update(o)
        if o.get("_none"):
            return d
        with open(d + ".owner.json", "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        return d
    me = S.process_start(os.getpid())
    other = me + 1 if isinstance(me, int) else 1
    cases = {
        mk("live"): "live",
        mk("exited", pid=p.pid, start=12345): "finished",
        mk("reused", start=other): "finished",
        mk("elsewhere", pid=p.pid, host="not-" + socket.gethostname()): "other-host",
        mk("none", _none=True): "no-owner",
    }
    got = {os.path.basename(d): S.state(d) for d in cases}
    want = {os.path.basename(d): v for d, v in cases.items()}
    assert got == want, (got, want)


def test_prune(S, work):
    """A14: make() first removes the finished folders beside it, with their
    owner files, and an owner file left with no folder by a finished run;
    it leaves live and unowned folders, and a live run's owner file, alone.
    No owner file is left but the live ones'."""
    parent = os.path.join(work, "pr")
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    old = S.make(parent, "x")
    plant_git_objects(old)
    with open(old + ".owner.json", "w", encoding="utf-8") as fh:
        json.dump(dict(pid=p.pid, start=1, host=socket.gethostname(),
                       platform=sys.platform), fh)
    live = S.make(parent, "x")
    bare = os.path.join(parent, "x-bare")
    os.makedirs(bare)
    # owner files whose folder is gone (a killed removal): a finished one's
    # goes, a live one's stays
    orphan = os.path.join(parent, "x-orphan")
    with open(orphan + ".owner.json", "w", encoding="utf-8") as fh:
        json.dump(dict(pid=p.pid, start=1, host=socket.gethostname(),
                       platform=sys.platform), fh)
    orphan_live = os.path.join(parent, "x-orphan-live")
    with open(orphan_live + ".owner.json", "w", encoding="utf-8") as fh:
        json.dump(dict(pid=os.getpid(), start=S.process_start(os.getpid()),
                       host=socket.gethostname(), platform=sys.platform), fh)
    new = S.make(parent, "x")
    assert not os.path.lexists(old), "a finished folder was kept"
    assert not os.path.lexists(old + ".owner.json"), "its owner file was kept"
    assert not os.path.lexists(orphan + ".owner.json"), "a finished orphan owner file was kept"
    assert os.path.isfile(orphan_live + ".owner.json"), "a live orphan owner file was removed"
    for d in (live, bare, new):
        assert os.path.isdir(d), "removed: %s" % d
    left = sorted(n for n in os.listdir(parent) if n.endswith(".owner.json"))
    assert left == sorted(os.path.basename(x) + ".owner.json"
                          for x in (live, new, orphan_live)), left


def test_keep(S, work):
    """A14, and gen-inhibit's reviewer's watch point (2026-11-01): a folder
    a tester keeps for inspection is never removed by the helper. keep()
    marks its owner file; state() says "kept"; make() beside it never
    prunes it, even once its run has finished; Made.remove_all() leaves it,
    lists it as kept and does not count it as a LEFTOVER."""
    parent = os.path.join(work, "kp")
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    m = S.Made()
    kept = m.add(S.make(parent, "k"))
    gone = m.add(S.make(parent, "k"))
    S.keep(kept, "a failed run, for inspection")
    with open(kept + ".owner.json", encoding="utf-8") as fh:
        o = json.load(fh)
    assert o.get("keep") is True and "inspection" in o.get("kept_why", ""), o
    assert S.state(kept) == "kept", S.state(kept)
    o["pid"], o["start"] = p.pid, 1                   # its run has finished
    with open(kept + ".owner.json", "w", encoding="utf-8") as fh:
        json.dump(o, fh)
    assert S.state(kept) == "kept", S.state(kept)
    S.make(parent, "k")
    assert os.path.isdir(kept), "make() pruned a kept folder"
    left = m.remove_all(out=lambda s: None)
    assert left == [], left
    assert os.path.isdir(kept) and os.path.isfile(kept + ".owner.json"), "remove_all took a kept folder"
    assert not os.path.lexists(gone), "remove_all left a folder that was not kept"
    assert m.kept == [kept], m.kept
    GOOD.remove(kept, quiet=True)


def test_run_finally(S, work):
    """A14: with run(...) removes its folder and owner file when the block
    raises, as when it ends."""
    seen = []
    try:
        with S.run(os.path.join(work, "rf"), "t") as d:
            seen.append(d)
            plant_git_objects(d)
            raise KeyError("boom")
    except KeyError:
        pass
    assert seen and not os.path.lexists(seen[0]), seen
    assert not os.path.lexists(seen[0] + ".owner.json")


def test_exit_handler(S, work, mod_path):
    """A14: folders a Made() holds are removed by its exit handler on every
    other way out: sys.exit, an uncaught exception. remove_all() is the
    finally's half and says what is left. A hard kill leaves the folder, and
    the next make() beside it removes it."""
    parent = os.path.join(work, "ex")
    for how in ("sys.exit(3)", "raise SystemError('x')"):
        rc, line, out = child(mod_path, (
            "m = S.Made()\n"
            "d = m.add(S.make(%r, 'e'))\n"
            "open(d + '/f', 'w').write('x')\n"
            "print(d, flush=True)\n"
            "%s\n") % (parent, how), work)
        assert line and os.path.isabs(line), out
        assert not os.path.lexists(line), "%s left %s: %s" % (how, line, out)
    rc, line, out = child(mod_path, (
        "import os\n"
        "m = S.Made()\n"
        "d = m.add(S.make(%r, 'e'))\n"
        "print(d, flush=True)\n"
        "os._exit(0)\n") % parent, work)
    assert line and os.path.isdir(line), "a hard kill should leave it: %s" % out
    d2 = S.make(parent, "e")
    assert not os.path.lexists(line), "the next make() kept a finished one"
    m = S.Made()
    m.add(d2)
    assert m.remove_all() == [] and not os.path.lexists(d2)


def test_names(S, work):
    """A14: make() takes one plain folder name, and refuses a scratch root
    outside any git worktree rather than fall back to the system temp
    folder."""
    for bad in ("a/b", "a\\b", "..", ".x", ""):
        try:
            S.make(os.path.join(work, "nm"), bad)
        except ValueError:
            continue
        raise AssertionError("make() took %r" % bad)
    assert os.path.normcase(S.scratch_root(HERE)) == os.path.normcase(
        os.path.join(_scratch.worktree_of(HERE), "scratch")), S.scratch_root(HERE)
    drive = os.path.splitdrive(HERE)[0] + os.sep if WINDOWS else "/"
    try:
        r = S.scratch_root(drive)
    except S.ScratchError:
        return
    raise AssertionError("scratch_root(%r) gave %r" % (drive, r))


TESTS = [test_remove_readonly, test_remove_long_path, test_leftover_reported,
         test_link_not_followed, test_owner_beside, test_state, test_prune,
         test_keep, test_run_finally, test_exit_handler, test_names]

MUTANTS = [
    ("no read-only handling",
     '    """Clear the read-only flag on p and, on POSIX, make its folder writable."""\n',
     '    """Clear the read-only flag on p and, on POSIX, make its folder writable."""\n'
     '    return\n'),
    ("no long-path form", '    if os.name != "nt" or p.startswith("\\\\\\\\?\\\\"):\n        return p\n',
     "    return p\n"),
    ("ignore_errors", "            _rmtree(lp, _handler(failed))",
     "            shutil.rmtree(lp, ignore_errors=True)"),
    ("leftovers not returned", "    return left\n\n\n# ---- folders",
     "    return []\n\n\n# ---- folders"),
    ("follows a link at the top", "    elif _is_link(lp):", "    elif False:"),
    ("owner inside the folder", 'return d.rstrip("/\\\\") + OWNER_SUFFIX',
     'return os.path.join(d, "owner" + OWNER_SUFFIX)'),
    ("start time not compared", '    return "live" if now == o.get("start") else "finished"',
     '    return "live"'),
    ("other host not told apart", "    if o.get(\"host\") != socket.gethostname()",
     "    if False"),
    ("prune takes unowned folders", '        if state(p) == "finished":',
     '        if state(p) in ("finished", "no-owner"):'),
    ("make does not prune", "    prune(parent, out=out)\n", ""),
    ("orphan owner files kept",
     '            if not os.path.lexists(d) and state(d) == "finished":',
     '            if False:'),
    ("a kept folder reads as its run's state", '    if o.get("keep"):\n        return "kept"\n', ""),
    ("remove_all takes kept folders", '            if state(p) == "kept":', '            if False:'),
    ("remove leaves the owner file", "        own = long_path(owner_path(path))\n",
     "        own = long_path(owner_path(path)) + '.not'\n"),
    ("run without finally", "    try:\n        yield p\n    finally:\n        remove(p)\n",
     "    yield p\n    remove(p)\n"),
    ("no exit handler", "        atexit.register(self.remove_all)\n", ""),
    ("bad names taken", "        raise ValueError(", "        if 0: raise ValueError("),
    ("falls back without a worktree", "            raise ScratchError(",
     "            return os.path.abspath(path)\n            raise ScratchError("),
]


def long_paths_native():
    """Why the long-path mutant changes nothing on this machine, or None.
    Off Windows there is no 260-character limit; on Windows with
    LongPathsEnabled = 1 a long path works without the \\\\?\\ form."""
    if not WINDOWS:
        return "not Windows"
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\FileSystem")
        v, _t = winreg.QueryValueEx(k, "LongPathsEnabled")
    except OSError:
        return None
    return "LongPathsEnabled = 1 on this machine" if v == 1 else None


# Mutants that change nothing where the function gives a reason: listed as
# not applied, with the reason, never counted as caught.
NOT_HERE = {"no long-path form": long_paths_native}


def run_all(mod_path, base):
    S = load(mod_path)
    bad = []
    for t in TESTS:
        work = os.path.join(base, t.__name__)
        os.makedirs(work)
        try:
            if t is test_exit_handler:
                t(S, work, mod_path)
            else:
                t(S, work)
            print("ok    %s" % t.__name__, flush=True)
        except Exception as e:
            print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:240]),
                  flush=True)
            bad.append(t.__name__)
    return bad


def main(argv):
    base = _scratch.run_dir("scratch_helper")
    try:
        if "--can-fail" not in argv:
            bad = run_all(MODULE, os.path.join(base, "tests"))
            print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
            return 1 if bad else 0
        print("-- control: the unmutated module", flush=True)
        bad = run_all(MODULE, os.path.join(base, "control"))
        if bad:
            print("\nREFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(bad))
            return 1
        src = open(MODULE, encoding="utf-8").read()
        caught = 0
        skipped = {m[0]: NOT_HERE[m[0]]() for m in MUTANTS if m[0] in NOT_HERE}
        skipped = {k: v for k, v in skipped.items() if v}
        mutants = [(n, m) for n, m in enumerate(MUTANTS, 1) if m[0] not in skipped]
        for n, (name, old, new) in mutants:
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)))
                continue
            mdir = os.path.join(base, "m%d" % n)
            os.makedirs(mdir)
            mp = os.path.join(mdir, "scratch.py")
            with open(mp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_all(mp, os.path.join(mdir, "tests"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"))
        print("\n%d of %d mutants caught" % (caught, len(mutants)))
        for k, why in sorted(skipped.items()):
            print("NOT APPLIED here: %s (it changes nothing: %s)" % (k, why))
        return 0 if caught == len(mutants) else 1
    finally:
        left = GOOD.remove(base)
        if left:
            print("this run's folder was not all removed (%d LEFTOVER line(s) above)"
                  % len(left))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
