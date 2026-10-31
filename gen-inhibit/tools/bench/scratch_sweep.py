#!/usr/bin/env python3
"""One-time sweep of the run folders left in the worktrees' scratch/ (AGENTS.md
"Removing scratch", 2026-10-31: 1,085 run folders and 472K files left on NICKY-XPS).
Gen-inhibit tester, 2026-10-31, for the gen-inhibit reviewer.

By default it only reports: it removes nothing, and the report goes to the reviewer,
who puts it to the user. Only then is it run with --apply. Run it outside any wall-clock
test: it reads every file's times.

Which scratch/ folders: each worktree of the anchor clone (`git worktree list`), or the
worktrees given with --worktree. Never touched, and not walked: scratch/can-tools/,
scratch/canre/ (the pinned installs) and scratch/session-compact/.

What it judges, walking down from each scratch/: a RUN FOLDER is one with an owner file
beside it (gi_paths.scratch(), since 2026-10-31) or a name ending _<pid>. It does not
walk into a run folder. For each:
  REMOVE   its owner file names a finished process (no such pid, or the pid reused);
           or it has no owner file, no process with the pid in its name is running, and
           nothing in it changed in the last hour (--quiet-hours).
  LEAVE    its owner process runs, or it is kept; or, with no owner file, the pid in its
           name runs, or something in it changed within the hour.
Every other folder is walked into; what is left in folders with no pid in their name
(fixed per-test folders, mkdtemp folders) is counted as OTHER, per top folder, and
left: not this sweep's to judge.

    py -3.14 -B scratch_sweep.py                  # report only
    py -3.14 -B scratch_sweep.py --out FILE       # the report also to FILE
    py -3.14 -B scratch_sweep.py --apply          # remove what the report calls REMOVE,
                                                  # each judged again just before
"""
import argparse
import os
import re
import subprocess
import sys
import time

import gi_paths

PROTECTED = ("can-tools", "canre", "session-compact")
PID_NAME = re.compile(r"_(\d+)$")


def worktrees(anchor):
    """The worktree folders `git worktree list` gives for anchor's clone."""
    r = subprocess.run(["git", "-C", anchor, "worktree", "list", "--porcelain"], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("git worktree list failed in %s: %s" % (anchor, r.stderr.strip()))
    return [ln[len("worktree "):].strip() for ln in r.stdout.splitlines() if ln.startswith("worktree ")]


def tree_stats(d):
    """(files, bytes, newest mtime) over d and everything in it; unreadable entries are
    counted in errors, not raised."""
    files = size = errors = 0
    newest = 0.0
    stack = [gi_paths.long_path(d)]
    while stack:
        p = stack.pop()
        try:
            st = os.stat(p, follow_symlinks=False)
            newest = max(newest, st.st_mtime)
            with os.scandir(p) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            s = e.stat(follow_symlinks=False)
                            files += 1
                            size += s.st_size
                            newest = max(newest, s.st_mtime)
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return files, size, newest, errors


def judge(d, now, quiet_s):
    """(verdict, reason) for run folder d: verdict REMOVE or LEAVE."""
    state = gi_paths.owner_state(d)
    if state == "finished":
        return "REMOVE", "owner finished"
    if state in ("running", "kept"):
        return "LEAVE", "owner " + state
    m = PID_NAME.search(os.path.basename(d))
    if not m:
        return "LEAVE", "owner file unreadable"
    if gi_paths.process_start(int(m.group(1))) is not None:
        return "LEAVE", "pid in name running"
    newest = tree_stats(d)[2]
    if now - newest < quiet_s:
        return "LEAVE", "changed within %g h" % (quiet_s / 3600.0)
    return "REMOVE", "no owner, pid not running, quiet"


def is_run_folder(path, name):
    return bool(PID_NAME.search(name)) or os.path.exists(gi_paths.owner_path(path))


def sweep(scratch, now, quiet_s):
    """[(rel, verdict, reason, files, bytes)] for the run folders under scratch, and
    {top folder: [files, bytes]} for what else is there (OTHER), and the protected
    folders found."""
    runs, other, protected = [], {}, []
    stack = [scratch]
    while stack:
        d = stack.pop()
        rel = os.path.relpath(d, scratch).replace("\\", "/")
        try:
            entries = sorted(os.scandir(gi_paths.long_path(d)), key=lambda e: e.name)
        except OSError:
            continue
        top = rel.split("/")[0] if rel != "." else "."
        for e in entries:
            p = os.path.join(d, e.name)
            erel = e.name if rel == "." else rel + "/" + e.name
            if rel == "." and e.name in PROTECTED:
                protected.append(e.name)
                continue
            if e.is_dir(follow_symlinks=False):
                if is_run_folder(p, e.name):
                    verdict, reason = judge(p, now, quiet_s)
                    f, b, _, _ = tree_stats(p)
                    runs.append((erel, verdict, reason, f, b))
                else:
                    stack.append(p)
            elif not e.name.endswith(gi_paths.OWNER_SUFFIX):
                try:
                    s = e.stat(follow_symlinks=False).st_size
                except OSError:
                    s = 0
                o = other.setdefault(top if rel != "." else e.name, [0, 0])
                o[0] += 1
                o[1] += s
    return runs, other, protected


def mb(b):
    return "%.1f MB" % (b / 1e6)


def report(results, quiet_s, out):
    w = lambda s="": print(s, file=out)   # noqa: E731
    w("scratch sweep, %s, report only unless --apply; REMOVE needs a finished owner, or no owner, the pid in"
      " the name not running and nothing changed in %g h" % (time.strftime("%Y-%m-%d %H:%M:%S"), quiet_s / 3600.0))
    tot = {"REMOVE": [0, 0, 0], "LEAVE": [0, 0, 0]}
    for wt, (runs, other, protected) in results:
        w()
        w("== %s  (scratch/%s)" % (os.path.basename(wt.rstrip("/\\")),
                                   "; protected, not walked: " + ", ".join(protected) if protected else ""))
        if runs is None:
            w("   no scratch/ folder")
            continue
        groups = {}
        for rel, verdict, reason, f, b in runs:
            g = groups.setdefault((os.path.dirname(rel) or ".", verdict, reason), [0, 0, 0])
            g[0] += 1
            g[1] += f
            g[2] += b
            t = tot[verdict]
            t[0] += 1
            t[1] += f
            t[2] += b
        for (parent, verdict, reason), (n, f, b) in sorted(groups.items()):
            w("   %-6s %5d run folder%s %8d files %10s  %s  [%s]" % (verdict, n, " " if n == 1 else "s", f, mb(b),
                                                                  parent, reason))
        for top, (f, b) in sorted(other.items()):
            w("   OTHER         %8d files %10s  %s  [no pid in the name: left]" % (f, mb(b), top))
    w()
    for v in ("REMOVE", "LEAVE"):
        n, f, b = tot[v]
        w("TOTAL %-6s %d run folders, %d files, %s" % (v, n, f, mb(b)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--worktree", action="append", help="a worktree to sweep (default: every worktree of the anchor)")
    ap.add_argument("--anchor", help="the clone whose worktrees to sweep (default: this file's worktree's clone)")
    ap.add_argument("--quiet-hours", type=float, default=1.0)
    ap.add_argument("--out", help="also write the report here")
    ap.add_argument("--apply", action="store_true", help="remove what the report calls REMOVE")
    a = ap.parse_args(argv)
    wts = a.worktree or worktrees(a.anchor or gi_paths.worktree_of(os.path.abspath(__file__)))
    now = time.time()
    quiet_s = a.quiet_hours * 3600.0
    results = []
    for wt in wts:
        s = os.path.join(wt, "scratch")
        results.append((wt, sweep(s, now, quiet_s) if os.path.isdir(s) else (None, {}, [])))
    outs = [sys.stdout] + ([open(a.out, "w", encoding="utf-8")] if a.out else [])
    for o in outs:
        report(results, quiet_s, o)
    if a.apply:
        removed = failed = changed = 0
        for wt, (runs, _, _) in results:
            for rel, verdict, _, _, _ in runs or []:
                if verdict != "REMOVE":
                    continue
                p = os.path.normpath(os.path.join(wt, "scratch", rel))
                if judge(p, time.time(), quiet_s)[0] != "REMOVE":
                    changed += 1
                    continue
                if gi_paths.remove_tree(p):
                    gi_paths._remove_owner(p)
                    removed += 1
                else:
                    failed += 1
        for o in outs:
            print("\napplied: %d removed, %d could not be removed (above), %d judged again and left"
                  % (removed, failed, changed), file=o)
    for o in outs[1:]:
        o.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
