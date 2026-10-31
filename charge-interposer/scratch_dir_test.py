#!/usr/bin/env python3
"""Tests scratch_dir.py: harness run folders are removed, read-only git
objects and long paths included, what cannot be removed is reported, and
the backlog sweep reports before it removes and removes run folders only.

Charge-interposer tester, 2026-10-31 (the reviewer's scratch-removal topic;
AGENTS.md "Removing scratch"). Everything is made in this test's own run
folder (scratch_dir.run), inside a stand-in worktree there (a folder with a
.git file), so nothing outside it is touched.

  1. A real git repo with a commit, its .git/objects files read-only (as
     Git for Windows leaves them; on POSIX the object folders r-x too):
     a plain shutil.rmtree(ignore_errors=True) leaves it (the control: the
     fixture bites), remove() takes it all and reports nothing.
  2. A path past 300 characters, with a read-only file at the end, is
     removed. long_path() gives the \\\\?\\ form for a drive path and the
     \\\\?\\UNC\\ form for a share, and leaves one already in that form.
  3. A file that cannot be removed (os.unlink refuses it) is returned and
     printed as a LEFTOVER line naming it and the error; remove() does not
     raise, and once the file can go a second remove() takes it.
  4. make() writes the owner file: this pid and its start time. state() is
     live for it, finished for a pid that has exited and for this pid with
     another start time (a pid used again), no-owner without the file,
     other-host for another machine's and for another platform's (a
     WSL process on the same machine).
  9. In a copy with no git, the root is scratch/ beside the copied
     folder, not inside it.
  5. make() prunes the tool's finished folders only: a live and an unowned
     one stay. A tool name with a separator is refused.
  6. run() removes its folder when the body raises, and the exception still
     propagates; and when it does not.
  7. The sweep, on a stand-in worktree: the report lists a finished run
     folder, an old pin's folder and a dead partial install as candidates;
     the current pins, session-compact/, a live run folder and a
     scratchpad and a running partial install are not, and nothing is
     removed. --apply refuses when the
     wall-clock check says busy, removing nothing; when clear it removes
     exactly the candidates, read-only git trees included.
  8. The wall-clock check: the "No builds" line cleared ("-- ...") is
     clear and set is not; a Python process running charger_sim.py is
     seen.

    py -3.14 -B charge-interposer/scratch_dir_test.py [--can-fail]

--can-fail runs this test on copies of scratch_dir.py with one edit each
(SCRATCH_DIR_SRC names the copy), after the unmutated copy passes there,
and passes only if every edit is caught.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scratch_dir as REAL   # noqa: E402  (the test's own folder)


def _load():
    src = os.environ.get("SCRATCH_DIR_SRC")
    if not src:
        return REAL
    spec = importlib.util.spec_from_file_location("scratch_dir_under_test", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()
fails = []


def check(cond, what):
    print(("  ok  " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        fails.append(what)


def plant(d, name="fixture-repo"):
    """A real git repo at d/name with one commit; every file under
    .git/objects read-only, and on POSIX its object folders r-x, so a plain
    rmtree fails on both. Returns its path."""
    g = os.path.join(d, name)
    os.makedirs(g)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example")
    git = ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=",
           "-C", g]
    subprocess.run(git + ["init", "-q"], check=True, env=env)
    with open(os.path.join(g, "a.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("a\n")
    subprocess.run(git + ["add", "a.txt"], check=True, env=env)
    subprocess.run(git + ["commit", "-q", "-m", "x"], check=True, env=env)
    objects = os.path.join(g, ".git", "objects")
    for dp, _dirs, files in os.walk(objects):
        for f in files:
            os.chmod(os.path.join(dp, f), stat.S_IREAD)
    if os.name != "nt":
        for dp, _dirs, _files in os.walk(objects, topdown=False):
            os.chmod(dp, 0o555)
    return g


def _readonly_files(g):
    n = 0
    for dp, _dirs, files in os.walk(os.path.join(g, ".git", "objects")):
        for f in files:
            n += not os.access(os.path.join(dp, f), os.W_OK)
    return n


def fake_worktree(base, name="wt"):
    wt = os.path.join(base, name)
    os.makedirs(os.path.join(wt, "charge-interposer"))
    with open(os.path.join(wt, ".git"), "w") as f:
        f.write("gitdir: nowhere\n")
    return wt


def write_owner(d, pid, start, host=None):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, M.OWNER), "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "start": start,
                   "host": host or M.socket.gethostname()}, f)


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def quietly(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = fn(*a, **kw)
    return r, out.getvalue()


# ---- the cases ----------------------------------------------------------
def case_git(base):
    ctl = plant(os.path.join(base, "control"))
    check(_readonly_files(ctl) > 0, "1. the fixture's .git/objects files are "
          "read-only (%d)" % _readonly_files(ctl))
    shutil.rmtree(ctl, ignore_errors=True)
    check(os.path.isdir(ctl), "1. control: a plain rmtree(ignore_errors=True) "
          "leaves the fixture, so it can fail")
    REAL.remove(ctl)
    d = os.path.join(base, "git")
    plant(d)
    left, text = quietly(M.remove, d)
    check(left == [] and not os.path.exists(d) and "LEFTOVER" not in text,
          "1. remove() takes a read-only git tree, reporting nothing -> %s"
          % (left[:1],))


def case_long(base):
    d = os.path.join(base, "long")
    p = d
    while len(p) < 320:
        p = os.path.join(p, "segment-of-forty-characters-for-length")
    os.makedirs(M.long_path(p))
    f = os.path.join(p, "ro.txt")
    with open(M.long_path(f), "w") as fh:
        fh.write("x")
    os.chmod(M.long_path(f), stat.S_IREAD)
    check(len(f) > 300, "2. the long path is %d characters" % len(f))
    left, _t = quietly(M.remove, d)
    check(left == [] and not os.path.exists(M.long_path(d)),
          "2. remove() takes a %d-character path with a read-only file -> %s"
          % (len(f), left[:1]))
    check(M._long_nt("C:\\a\\b") == "\\\\?\\C:\\a\\b"
          and M._long_nt("\\\\srv\\share\\x") == "\\\\?\\UNC\\srv\\share\\x"
          and M._long_nt("\\\\?\\C:\\a") == "\\\\?\\C:\\a",
          "2. the \\\\?\\ form: drive, share (UNC), and one already in it")
    if os.name == "nt":
        check(M.long_path("C:\\a\\b") == "\\\\?\\C:\\a\\b",
              "2. long_path() uses that form on Windows")
    else:
        check(M.long_path("/a/b") == "/a/b", "2. long_path() leaves a POSIX path")


def case_leftover(base):
    d = os.path.join(base, "stuck")
    os.makedirs(d)
    stuck = os.path.join(d, "stuck.txt")
    open(stuck, "w").close()
    real_unlink = os.unlink

    def refuse(p, *a, **kw):
        if os.path.basename(str(p)) == "stuck.txt":
            raise PermissionError(13, "refused by the test", str(p))
        return real_unlink(p, *a, **kw)
    os.unlink = refuse
    try:
        left, text = quietly(M.remove, d)
    except Exception as e:     # noqa: BLE001
        left, text = None, "raised %r" % e
    finally:
        os.unlink = real_unlink
    check(left is not None and any(p.endswith("stuck.txt") and "PermissionError" in why
                                   for p, why in left),
          "3. remove() returns the file it could not remove, with the error -> %s"
          % (left[:2] if left else text[:120],))
    check("LEFTOVER" in text and "stuck.txt" in text and "PermissionError" in text,
          "3. and prints a LEFTOVER line naming it and the error")
    left, _t = quietly(M.remove, d)
    check(left == [] and not os.path.exists(d),
          "3. once it can go, a second remove() takes it")


def case_owner(base):
    wt = fake_worktree(base, "wt-owner")
    d = M.make("t", start=wt)
    o = M.owner(d) or {}
    check(os.path.dirname(os.path.dirname(d)) ==
          os.path.join(wt, "scratch", M.HARNESS),
          "4. the folder is <worktree>/scratch/%s/<tool>/..." % M.HARNESS)
    check(o.get("pid") == os.getpid() and o.get("start") is not None
          and o.get("start") == M.proc(os.getpid())[1],
          "4. the owner file holds this pid and its start time -> %s" % (o,))
    check(M.state(d) == "live", "4. state: live for this process")
    f = os.path.join(base, "fin")
    write_owner(f, dead_pid(), 1)
    check(M.state(f) == "finished", "4. state: finished for an exited pid")
    r = os.path.join(base, "reused")
    write_owner(r, os.getpid(), (o.get("start") or 0) + 1)
    check(M.state(r) == "finished",
          "4. state: finished for this pid with another start time (reused)")
    n = os.path.join(base, "noowner")
    os.makedirs(n)
    check(M.state(n) == "no-owner", "4. state: no-owner without the file")
    h = os.path.join(base, "otherhost")
    write_owner(h, os.getpid(), o.get("start"), host="some-other-host")
    check(M.state(h) == "other-host", "4. state: other-host for another machine's")
    w = os.path.join(base, "otherplatform")
    write_owner(w, os.getpid(), o.get("start"))
    with open(os.path.join(w, M.OWNER), encoding="utf-8") as f:
        rec = json.load(f)
    rec["platform"] = "linux" if sys.platform != "linux" else "win32"
    with open(os.path.join(w, M.OWNER), "w", encoding="utf-8") as f:
        json.dump(rec, f)
    check(M.state(w) == "other-host",
          "4. state: other-host for another platform's (WSL beside Windows)")
    check(o.get("platform") == sys.platform,
          "4. the owner file names this platform -> %s" % o.get("platform"))


def case_prune(base):
    wt = fake_worktree(base, "wt-prune")
    td = M.tool_dir("t", start=wt)
    fin = os.path.join(td, "t-fin")
    write_owner(fin, dead_pid(), 1)
    plant(fin)
    live = os.path.join(td, "t-live")
    write_owner(live, os.getpid(), M.proc(os.getpid())[1])
    unowned = os.path.join(td, "t-unowned")
    os.makedirs(unowned)
    new, text = quietly(M.make, "t", start=wt)
    check(not os.path.exists(fin) and "LEFTOVER" not in text,
          "5. make() prunes a finished folder, read-only git tree and all")
    check(os.path.isdir(live) and os.path.isdir(unowned) and os.path.isdir(new),
          "5. a live and an unowned folder stay")
    try:
        M.make("a/b", start=wt)
        refused = False
    except ValueError:
        refused = True
    check(refused, "5. a tool name with a separator is refused")


def case_run(base):
    wt = fake_worktree(base, "wt-run")
    seen = []
    try:
        with M.run("r", start=wt) as d:
            seen.append(d)
            plant(d, "run-repo")
            raise KeyError("boom")
    except KeyError:
        seen.append("raised")
    check(len(seen) == 2 and seen[1] == "raised" and not os.path.exists(seen[0]),
          "6. run(): the folder goes when the body raises, and it propagates")
    with M.run("r", start=wt) as d:
        pass
    check(not os.path.exists(d), "6. run(): and when it does not")


def _sweep_tree(base):
    wt = fake_worktree(base, "wt-sweep")
    cur_ct, cur_cr = "c" * 40, "d" * 40
    with open(os.path.join(wt, "charge-interposer", "pins.json"), "w") as f:
        json.dump({"can-tools": {"repo": "x", "commit": cur_ct},
                   "canre": {"repo": "y", "commit": cur_cr}}, f)
    s = os.path.join(wt, "scratch")
    t = {
        "cur_ct": os.path.join(s, "can-tools", cur_ct),
        "old_ct": os.path.join(s, "can-tools", "e" * 40),
        "partial": os.path.join(s, "can-tools", "%s.partial-%d" % (cur_ct, dead_pid())),
        "livepart": os.path.join(s, "canre", "%s.partial-%d" % (cur_cr, os.getpid())),
        "cur_cr": os.path.join(s, "canre", cur_cr),
        "compact": os.path.join(s, "session-compact"),
        "notes": os.path.join(s, "my-notes"),
        "fin": os.path.join(s, M.HARNESS, "t", "t-fin"),
        "live": os.path.join(s, M.HARNESS, "t", "t-live"),
    }
    for k in ("cur_ct", "partial", "livepart", "cur_cr", "compact", "notes"):
        os.makedirs(t[k])
        open(os.path.join(t[k], "f.txt"), "w").close()
    plant(t["old_ct"].rsplit(os.sep, 1)[0], os.path.basename(t["old_ct"]))
    write_owner(t["fin"], dead_pid(), 1)
    plant(t["fin"])
    write_owner(t["live"], os.getpid(), M.proc(os.getpid())[1])
    return wt, t


def case_sweep(base):
    wt, t = _sweep_tree(base)
    lines = []
    rc = M.sweep([wt], apply=False, out=lines.append)
    text = "\n".join(lines)
    verdict = {}
    for line in lines:
        parts = line.split()
        if len(parts) > 2 and parts[0] in ("candidate", "kept", "other"):
            verdict[os.path.normpath(os.path.join(wt, parts[1]))] = parts[0]
    want = {"old_ct": "candidate", "partial": "candidate", "fin": "candidate",
            "cur_ct": "kept", "cur_cr": "kept", "compact": "kept",
            "live": "kept", "livepart": "kept", "notes": "other"}
    got = {k: verdict.get(os.path.normpath(t[k])) for k in want}
    check(got == want, "7. report: candidates and the rest -> %s"
          % {k: v for k, v in got.items() if v != want[k]})
    check(rc == 0 and "REPORT ONLY" in text and all(os.path.isdir(p) for p in t.values()),
          "7. report only: nothing removed")
    fin = next((ln for ln in lines if ln.split()[1:2] == [os.path.relpath(
        t["fin"], wt)]), "")
    nf, nd = M._count(t["fin"])[:2]
    check(nf > 3 and nd > 3 and ("%d files" % nf) in fin
          and ("%d folders" % nd) in fin and " 20" in fin,
          "7. a row gives files (%d), folders (%d), bytes and date -> %s"
          % (nf, nd, fin.strip()[:120]))
    tc = next((ln for ln in lines if ln.startswith("TOTAL candidates")), "")
    ta = next((ln for ln in lines if ln.startswith("TOTAL everything")), "")
    want_n = sum(M._count(t[k])[0] for k in ("fin", "old_ct", "partial"))
    check(" 3 row(s)" in tc and (" %d files" % want_n) in tc and "folders" in tc
          and "folders" in ta,
          "7. totals for the candidates (3 rows, %d files) and for everything "
          "-> %s | %s" % (want_n, tc.strip(), ta.strip()))
    lines = []
    rc = M.sweep([wt], apply=True, guard=lambda: (False, ["busy (test)"]),
                 out=lines.append)
    check(rc == 2 and all(os.path.isdir(p) for p in t.values())
          and "REFUSED" in "\n".join(lines),
          "7. --apply refused when the wall-clock check says busy, nothing removed")
    lines = []
    rc, text = quietly(M.sweep, [wt], apply=True, guard=lambda: (True, ["clear (test)"]),
                       out=lines.append)
    gone = sorted(k for k, p in t.items() if not os.path.exists(p))
    check(rc == 0 and gone == ["fin", "old_ct", "partial"] and "LEFTOVER" not in text,
          "7. --apply when clear removes exactly the candidates -> gone %s, rc %d"
          % (gone, rc))


def case_wallclock(base):
    rid = os.path.join(base, "rid")
    os.makedirs(os.path.join(rid, "firmware"))
    hold = os.path.join(rid, "firmware", "bench-holder.md")
    keep = os.environ.get("REVERSE_IT_DIR")
    os.environ["REVERSE_IT_DIR"] = rid
    try:
        with open(hold, "w") as f:
            f.write("## No builds\n\n**No builds:** -- (cleared 10:00)\n")
        ok_clear = M._no_builds_clear()[0]
        with open(hold, "w") as f:
            f.write("## No builds\n\n**No builds:** set 10:00 by a tester\n")
        ok_set = M._no_builds_clear()[0]
    finally:
        if keep is None:
            os.environ.pop("REVERSE_IT_DIR", None)
        else:
            os.environ["REVERSE_IT_DIR"] = keep
    check(ok_clear is True and ok_set is False,
          "8. No builds: cleared is clear (%s), set is not (%s)" % (ok_clear, ok_set))
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                          "charger_sim.py"])
    try:
        time.sleep(0.5)
        ok, why = M._sims_running()
    finally:
        p.kill()
        p.wait()
    check(not ok and ("pid %d" % p.pid) in why,
          "8. a Python process running charger_sim.py is seen -> %s" % why)


def case_nogit(base):
    code = os.path.join(base, "shipped", "code")
    os.makedirs(code)
    keep = M.worktree
    M.worktree = lambda start=None: None
    try:
        r = M.root()
    finally:
        M.worktree = keep
    here = os.path.dirname(os.path.abspath(M.__file__))
    check(r == os.path.join(os.path.dirname(here), "scratch"),
          "9. no git: the root is scratch/ beside the code folder -> %s" % r)


CASES = [case_git, case_long, case_leftover, case_owner, case_prune,
         case_run, case_sweep, case_wallclock, case_nogit]


def test():
    with REAL.run("scratch-dir-test") as base:
        for c in CASES:
            d = os.path.join(base, c.__name__)
            os.makedirs(d)
            try:
                c(d)
            except Exception as e:     # noqa: BLE001
                check(False, "%s raised %s: %s" % (c.__name__, type(e).__name__, e))
    print("\nscratch_dir_test: %s" % ("all checks OK" if not fails
                                      else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# (label, old text, new text), each applied to a copy of scratch_dir.py
MUTANTS = [
    ("the read-only handler taken out",
     '    """Clear the read-only flag on p and make its folder writable."""\n',
     '    """Clear the read-only flag on p and make its folder writable."""\n'
     '    return\n'),
    ("ignore_errors=True instead of the handler",
     "                _rmtree(lp, _handler(failed))",
     "                shutil.rmtree(lp, ignore_errors=True)"),
    ("leftovers not printed",
     "    if left and not quiet:", "    if False:"),
    ("no \\\\?\\ form",
     '    return "\\\\\\\\?\\\\" + p\n', "    return p\n"),
    ("no UNC form",
     '        return "\\\\\\\\?\\\\UNC\\\\" + p[2:]', "        return p"),
    ("the start time not compared (a reused pid read as live)",
     '    return "live" if start == o["start"] else "finished"',
     '    return "live"'),
    ("another platform's owner judged here",
     "            o.get(\"platform\", sys.platform) != sys.platform:",
     "            False:"),
    ("no git: scratch/ inside the code folder",
     "    return os.path.join(wt if wt else os.path.dirname(HERE), \"scratch\")",
     "    return os.path.join(wt if wt else HERE, \"scratch\")"),
    ("no folder count",
     "        folders += len(dirs)\n", "\n"),
    ("no file count in the totals",
     "                tot[k][1] += n\n", "\n"),
    ("no owner file",
     "        json.dump(owner_record(), f, sort_keys=True)",
     "        pass"),
    ("prune takes every folder",
     '        if os.path.isdir(p) and state(p) == "finished":',
     "        if os.path.isdir(p):"),
    ("make() does not prune",
     "    prune(tool, start)\n", "\n"),
    ("run() without the finally",
     "    try:\n        yield p\n    finally:\n        remove(p)",
     "    yield p\n    remove(p)"),
    ("the sweep removes without --apply",
     "    if not apply:\n", "    if False:\n"),
    ("the sweep takes the current pin",
     "                elif n == cur:", "                elif False:"),
    ("the sweep takes a live partial",
     '"candidate" if alive is False else "kept",', '"candidate",'),
    ("the sweep takes scratchpads",
     'out.append((tp, "other", "not a run folder: left alone"))',
     'out.append((tp, "candidate", "x"))'),
    ("the sweep ignores the wall-clock check",
     "    if not ok:\n        out(\"REFUSED", "    if False:\n        out(\"REFUSED"),
    ("No builds always clear",
     '                    return v.startswith("--"), "No builds: %s" % v[:60]',
     '                    return True, "No builds: %s" % v[:60]'),
    ("simulators never seen",
     "        if pid.isdigit() and int(pid) != me and \\",
     "        if False and \\"),
]


def can_fail():
    src_path = os.path.join(HERE, "scratch_dir.py")
    with open(src_path, encoding="utf-8", newline="") as f:
        src = f.read()
    bad = 0
    with REAL.run("scratch-dir-test-mutants") as base:
        def run_copy(text, i):
            d = os.path.join(base, "m%02d" % i)
            os.makedirs(d)
            p = os.path.join(d, "scratch_dir.py")
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            env = dict(os.environ, SCRATCH_DIR_SRC=p, PYTHONDONTWRITEBYTECODE="1")
            return subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                                  capture_output=True, text=True, env=env,
                                  timeout=600)
        r = run_copy(src, 0)
        if r.returncode != 0:
            print("FAIL  baseline: the unmutated copy fails (rc %d): %s"
                  % (r.returncode, (r.stdout.strip().splitlines() or [""])[-1]))
            return 1
        print("ok    baseline: the unmutated copy passes")
        for i, (label, old, new) in enumerate(MUTANTS, 1):
            n = src.count(old)
            if n != 1:
                print("FAIL  %-58s NOT APPLIED (found %d times)" % (label, n))
                bad += 1
                continue
            r = run_copy(src.replace(old, new), i)
            caught = r.returncode != 0
            bad += not caught
            first = next((ln for ln in r.stdout.splitlines()
                          if ln.startswith("FAIL")), "")
            print("%s  %-58s %s" % ("ok  " if caught else "FAIL", label,
                                    ("caught: " + first[6:90]) if caught
                                    else "STILL PASSES"))
    print("\n%d of %d mutants caught" % (len(MUTANTS) - bad, len(MUTANTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(can_fail() if "--can-fail" in sys.argv[1:] else test())
