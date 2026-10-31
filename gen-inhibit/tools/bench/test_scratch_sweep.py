#!/usr/bin/env python3
"""Tests scratch_sweep.py, the one-time sweep of left run folders. Gen-inhibit tester,
2026-10-31.

On a fake worktree in this run's own scratch/ folder, whose scratch/ holds: the three
protected folders, each with a dead-pid run folder inside; run folders with an owner file
(finished, running, kept); run folders with none (pid not running and quiet for two hours;
pid not running but a file changed a minute ago; pid running); a run folder nested in a
fixed folder, with a mutant copy inside it that holds its own run folder; a fixed folder
with no pid in its name; a loose file.

  1. Report only: removes and changes nothing (every path and time the same after).
  2. It judges each REMOVE or LEAVE as the docstring says, with the reason, and doesn't
     judge or walk into a run folder's insides, nor into the protected folders.
  3. --quiet-hours moves the line: with 0, the recently changed one is REMOVE too.
  4. --apply removes the REMOVE ones (and their owner files) and nothing else; one
     changed between the report and --apply is judged again and left.
  5. The report names each verdict, reason and count, the OTHER folders, the protected
     ones, and the totals; --out writes the same report.

    py -3.14 -B test_scratch_sweep.py          # exit 0 only if every check passes

Its mutants: test_scratch_sweep_mutants.py. SWEEP_DIR runs another copy of scratch_sweep.py.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time

import gi_paths

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("SWEEP_DIR") or HERE
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
    sys.modules.pop("scratch_sweep", None)
    sys.path.insert(0, SRC_DIR)
    try:
        import scratch_sweep
    finally:
        sys.path.remove(SRC_DIR)
    return scratch_sweep


def mk(path, age_s=7200, files=2):
    """A folder with files, every time age_s seconds ago."""
    os.makedirs(path, exist_ok=True)
    t = time.time() - age_s
    for i in range(files):
        f = os.path.join(path, "f%d.txt" % i)
        with open(f, "w") as fh:
            fh.write("x" * 10)
        os.utime(f, (t, t))
    os.utime(path, (t, t))
    return path


def owner(d, pid, start, keep=False):
    with open(gi_paths.owner_path(d), "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "start": start, "made": "x", "keep": keep}, fh)


def snapshot(top):
    out = {}
    for d, dirs, files in os.walk(top):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.stat(p)
            out[os.path.relpath(p, top)] = (st.st_mtime_ns, st.st_size)
    return out


def run_main(sw, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.main(argv)
    return rc, buf.getvalue()


def build(wt, dead, me):
    S = os.path.join(wt, "scratch")
    T = os.path.join(S, "gen-inhibit-tests")
    for p in ("can-tools/abc1234", "canre/def5678", "session-compact"):
        mk(os.path.join(S, p, "20260101T000000_%d" % dead))
    paths = {
        "fin": mk(os.path.join(T, "a", "20261031T010000_%d" % dead)),
        "run": mk(os.path.join(T, "a", "20261031T010001_%d" % me)),
        "kept": mk(os.path.join(T, "a", "20261031T010002_%d" % dead)),
        "old": mk(os.path.join(T, "b", "20261030T010000_%d" % dead)),
        "recent": mk(os.path.join(T, "b", "20261031T010000_%d" % dead), age_s=60),
        "live": mk(os.path.join(T, "b", "20261031T010001_%d" % me)),
        "nested": mk(os.path.join(S, "build-checks", "deep", "20261031T030000_%d" % dead)),
        "mutcopy": mk(os.path.join(S, "build-checks", "deep", "interlock_judge_%d" % dead)),
        "toolchain": mk(os.path.join(S, "fake_idf", "esp-14.2.0_20241119")),
        "fixed": mk(os.path.join(S, "build-checks", "test_source_rows"), files=3),
        "named": mk(os.path.join(T, "c", "no-pid-in-this-name")),
    }
    owner(paths["fin"], dead, 1)
    owner(paths["run"], me, gi_paths.process_start(me))
    owner(paths["kept"], dead, 1, keep=True)
    owner(paths["named"], dead, 1)     # a run folder by its owner file alone
    # a mutant copy inside the dead run folder, with its own (live-named) run folder: not judged
    paths["inner"] = mk(os.path.join(paths["old"], "m1", "20261031T020000_%d" % me))
    t = time.time() - 7200
    for d in (os.path.join(paths["old"], "m1"), paths["old"]):     # making inner touched them
        os.utime(d, (t, t))
    with open(os.path.join(S, "loose.txt"), "w") as fh:
        fh.write("y")
    return paths


def main():
    with gi_paths.run_dir("scratch-sweep") as work:
        return run(work)


def run(work):
    sw = load()
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    dead, me = p.pid, os.getpid()
    wt = os.path.join(work, "wt")
    S = os.path.join(wt, "scratch")
    paths = build(wt, dead, me)
    rel = lambda k: os.path.relpath(paths[k], S).replace("\\", "/")   # noqa: E731

    print("=== report only ===")
    before = snapshot(wt)
    out = os.path.join(work, "report.txt")
    rc, text = run_main(sw, ["--worktree", wt, "--out", out])
    check("exit 0", lambda: rc == 0, rc)
    check("removes and changes nothing", lambda: snapshot(wt) == before)
    runs, other, protected = sw.sweep(S, time.time(), 3600.0)
    v = {r[0]: (r[1], r[2]) for r in runs}
    want = {"fin": ("REMOVE", "owner finished"), "run": ("LEAVE", "owner running"), "kept": ("LEAVE", "owner kept"),
            "old": ("REMOVE", "no owner, pid not running, quiet"), "recent": ("LEAVE", "changed within 1 h"),
            "live": ("LEAVE", "pid in name running"), "nested": ("REMOVE", "no owner, pid not running, quiet"),
            "named": ("REMOVE", "owner finished")}
    for k, w in want.items():
        check("%s: %s, %s" % (k, w[0], w[1]), lambda: v.get(rel(k)) == w, v.get(rel(k)))
    check("only those eight judged: not inside a run folder, not the fixed folder",
          lambda: sorted(v) == sorted(rel(k) for k in want), sorted(v))
    check("the protected folders found and not walked", lambda: sorted(protected) == ["can-tools", "canre", "session-compact"]
          and not any(r.startswith(("can-tools", "canre", "session-compact")) for r in v), protected)
    check("OTHER: the fixed folder's files (and the mutant copy's) under their top folder, and the loose file",
          lambda: other.get("build-checks", [0])[0] == 5 and other.get("loose.txt", [0])[0] == 1, other)
    check("a name merely ending _<n> (a mutant copy, a toolchain version) is not a run folder: OTHER",
          lambda: rel("mutcopy") not in v and rel("toolchain") not in v and other.get("fake_idf", [0])[0] == 2, other)
    check("owner files are not OTHER", lambda: "gen-inhibit-tests" not in other, other)
    check("a run folder's file count includes its insides", lambda: [r[3] for r in runs if r[0] == rel("old")] == [4])
    for word in ("REMOVE", "LEAVE", "OTHER", "owner finished", "pid in name running", "changed within 1 h",
                 "protected, not walked: can-tools, canre, session-compact", "TOTAL REMOVE 4 run folders",
                 "TOTAL LEAVE  4 run folders"):
        check("the report says %r" % word, lambda: word in text)
    check("--out holds the same report", lambda: os.path.isfile(out) and open(out, encoding="utf-8").read() == text)

    print("\n=== --quiet-hours ===")
    runs0 = {r[0]: r[1] for r in sw.sweep(S, time.time(), 0.0)[0]}
    check("with 0 the recently changed one is REMOVE", lambda: runs0.get(rel("recent")) == "REMOVE", runs0.get(rel("recent")))
    check("and the running ones are still LEAVE", lambda: runs0.get(rel("live")) == "LEAVE" and runs0.get(rel("run")) == "LEAVE")

    print("\n=== --apply ===")
    # between the report and --apply, the old one is touched: judged again, it is left
    t = os.path.join(paths["old"], "touched.txt")
    orig_judge = sw.judge
    calls = []

    def judge(d, now, q):
        d = os.path.normpath(d)
        calls.append(d)
        if d == os.path.normpath(paths["old"]) and calls.count(d) == 2:
            open(t, "w").close()
        return orig_judge(d, now, q)
    sw.judge = judge
    rc, text = run_main(sw, ["--worktree", wt, "--apply"])
    sw.judge = orig_judge
    check("fin, nested and named removed, their owner files with them",
          lambda: not os.path.exists(paths["fin"]) and not os.path.exists(gi_paths.owner_path(paths["fin"]))
          and not os.path.exists(paths["nested"]) and not os.path.exists(paths["named"])
          and not os.path.exists(gi_paths.owner_path(paths["named"])))
    check("the old one, changed before --apply reached it, judged again and left",
          lambda: os.path.isdir(paths["old"]) and "1 judged again and left" in text, text[-200:])
    check("everything else left", lambda: all(os.path.isdir(paths[k]) for k in ("run", "kept", "recent", "live", "fixed"))
          and os.path.isdir(os.path.join(S, "can-tools", "abc1234", "20260101T000000_%d" % dead))
          and os.path.isdir(os.path.join(S, "session-compact", "20260101T000000_%d" % dead))
          and os.path.exists(gi_paths.owner_path(paths["run"])) and os.path.exists(gi_paths.owner_path(paths["kept"])))
    check("the count says 3 removed", lambda: "applied: 3 removed, 0 could not be removed" in text, text[-200:])

    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
