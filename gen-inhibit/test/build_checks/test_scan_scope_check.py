#!/usr/bin/env python3
"""Tests scan_scope_check.py's judgements (no configure, no ESP-IDF).

    python3 test_scan_scope_check.py

The real-configure half is the run itself: the planted option must fail the
scan, and the same name set first must pass it.

check-env first (port step 1d, 2026-11-03): steps() runs against fake worktrees in
scratch/, each with a fake gen-inhibit/vtrux_tools.py (a child process that records what
it was run with), and the configure replaced by one that records its calls. A launcher
that refuses (exit 1), crashes or is missing stops the run before any configure, and the
verdict is REFUSED; one that passes runs before the first configure, given check-env
and the caller's environment.
"""

import contextlib
import io
import json
import os
import sys


def _bench_dir():
    """gen-inhibit/tools/bench in this file's worktree, found by walking up to its .git (a
    copy in scratch/ finds the same one); in a copy with no git, two folders up."""
    d = here = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            return os.path.normpath(os.path.join(here, "..", "..", "tools", "bench"))
        d = os.path.dirname(d)
    return os.path.join(d, "gen-inhibit", "tools", "bench")


sys.path.append(_bench_dir())
import gi_paths  # noqa: E402  scratch and remove_tree (AGENTS.md "Removing scratch")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_scope_check as S                                       # noqa: E402
import build_switches as BS                                        # noqa: E402
REPO = S.git(["rev-parse", "--show-toplevel"], HERE).stdout.strip()

FAKE_LAUNCHER = r'''
import json, os, sys
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ran.json"), "w") as f:
    json.dump({"argv": sys.argv[1:], "plant": os.environ.get("SSC_PLANT")}, f)
MODE = %r
if MODE == "crash":
    raise KeyError("IDF_TOOLS_PATH")
sys.exit(1 if MODE == "refuse" else 0)
'''


class Stop(Exception):
    pass


def check_env_checks():
    """[(label, ok)] for the check-env step, on fake worktrees in scratch/."""
    d = gi_paths.scratch("test-scan-scope-check")
    out = []
    saved = S.configure
    os.environ["SSC_PLANT"] = "planted"
    try:
        for mode in ("refuse", "crash", "missing", "pass"):
            wt = os.path.join(d, mode)
            os.makedirs(os.path.join(wt, "gen-inhibit"))
            ran = os.path.join(wt, "gen-inhibit", "ran.json")
            if mode != "missing":
                with open(os.path.join(wt, "gen-inhibit", "vtrux_tools.py"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(FAKE_LAUNCHER % mode)
            calls = []

            def fake_configure(tree, log, defines=(), _ran=ran, _calls=calls):
                _calls.append(os.path.exists(_ran))    # did check-env run before it?
                raise Stop()

            S.configure = fake_configure
            log = os.path.join(wt, "run.log")
            record = {"steps": []}
            try:
                with contextlib.redirect_stdout(io.StringIO()):   # its own "FAIL check-env" lines
                    S.steps(os.path.join(wt, "gen-inhibit", "firmware"), wt, log, record)
            except Stop:
                pass
            seen = json.load(open(ran)) if os.path.exists(ran) else None
            logged = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
            if mode == "pass":
                out += [("check-env passes: the configure runs once, after it", calls == [True]),
                        ("check-env passes: recorded ok", record.get("check_env") == {"rc": 0, "ok": True}),
                        ("check-env passes: the launcher was given check-env and nothing else",
                         seen is not None and seen["argv"] == ["check-env"]),
                        ("check-env passes: the launcher saw the caller's environment",
                         seen is not None and seen["plant"] == "planted"),
                        ("check-env passes: its rc is in the log", "===== check-env (rc 0)" in logged)]
            else:
                rc = (record.get("check_env") or {}).get("rc")
                out += [("check-env %s: no configure" % mode, calls == []),
                        ("check-env %s: recorded not ok, non-zero rc" % mode,
                         (record.get("check_env") or {}).get("ok") is False and rc not in (0, None)),
                        ("check-env %s: verdict REFUSED, no step" % mode,
                         S.verdict(record) == "REFUSED" and record["steps"] == []),
                        ("check-env %s: its rc is in the log" % mode, "===== check-env (rc %s)" % rc in logged)]
    finally:
        S.configure = saved
        os.environ.pop("SSC_PLANT", None)
        gi_paths.remove_tree(d)
    wt = os.path.join("W")
    out.append(("the launcher is the worktree's gen-inhibit/vtrux_tools.py, run by this Python with check-env",
                S.launcher_cmd(wt) == [sys.executable, "-B", os.path.join(wt, "gen-inhibit", "vtrux_tools.py"),
                                       "check-env"]))
    ok3 = [{"ok": True}] * 3
    for label, rec, want in (("check-env ok, three steps ok", {"check_env": {"ok": True}, "steps": ok3}, "PASS"),
                             ("check-env ok, two steps", {"check_env": {"ok": True}, "steps": ok3[:2]}, "FAIL"),
                             ("check-env ok, a step not ok",
                              {"check_env": {"ok": True}, "steps": ok3[:2] + [{"ok": False}]}, "FAIL"),
                             ("check-env refused, three steps ok", {"check_env": {"ok": False}, "steps": ok3}, "REFUSED"),
                             ("no check-env, three steps ok", {"steps": ok3}, "REFUSED")):
        out.append(("verdict: %s -> %s" % (label, want), S.verdict(rec) == want))
    return out

REC = ["CMakeLists.txt", S.PLANTED, "main/CMakeLists.txt"]
ON = "-- " + S.SAYS_ON + "\n"
HIT = {S.NAME: [S.PLANTED + ":2"]}


def planted(rc=0, log=ON, scanned=REC, found=HIT, unlisted=(S.NAME,)):
    return S.judge_planted(rc, log, scanned, found, list(unlisted))[0]


def set_first(rc=0, log="", scanned=REC, found=None, unlisted=(), stale=(), bad=()):
    return S.judge_set_first(rc, log, scanned, found or {}, list(unlisted), list(stale), list(bad))[0]


def main():
    checks = [
        ("planted: the -D reached the test, the record names the file, the scan reports it there",
         planted() is True),
        ("planted: a failed configure fails", planted(rc=1) is False),
        ("planted: no record fails", planted(scanned=None) is False),
        ("planted: the planted file missing from the record fails", planted(scanned=REC[:1]) is False),
        ("planted: the -D not reaching the test fails (it would not be shown to be an option)",
         planted(log="") is False),
        ("planted: the scan not reporting it fails", planted(found={}, unlisted=()) is False),
        ("planted: reported as unlisted, but only from another file, fails",
         planted(found={S.NAME: ["main/CMakeLists.txt:9"]}) is False),
        ("planted: found but listed (not unlisted) fails", planted(unlisted=()) is False),
        ("set-first: the -D ignored, the file in the record, the name not reported, the scan clean",
         set_first() is True),
        ("set-first: a failed configure fails", set_first(rc=2) is False),
        ("set-first: no record fails", set_first(scanned=None) is False),
        ("set-first: the planted file missing from the record fails (the scan never read it)",
         set_first(scanned=REC[:1]) is False),
        ("set-first: the test still true fails", set_first(log=ON) is False),
        ("set-first: the name still reported fails", set_first(found=HIT) is False),
        ("set-first: any other finding fails the passing half",
         set_first(unlisted=["X"]) is False and set_first(stale=["Y"]) is False
         and set_first(bad=["Z: no reason"]) is False),
        ("the planted file, scanned for real, reports the name at line 2",
         [s for s, _ in BS.cmake_symbols(S.PLANTED_TEXT)] == [S.NAME]
         and BS.cmake_symbols(S.PLANTED_TEXT)[0][1] == 2),
        ("the set-first file, scanned for real, reports nothing",
         BS.cmake_symbols(S.SET_FIRST_TEXT) == []),
        ("the planted file's message is the marker the judgements look for",
         S.SAYS_ON in S.PLANTED_TEXT and S.SAYS_ON in S.SET_FIRST_TEXT),
        ("the include names the planted file", S.PLANTED in S.INCLUDE),
        # the WiCAN tree per commit (port step 0d, 2026-10-26): e7263bb is before the move,
        # 43f0ae2 (the implementor's half of it) has gen-inhibit/firmware/CMakeLists.txt
        ("a commit before the move configures in the worktree itself",
         S.wican_tree("W", "e7263bb", REPO) == "W"),
        ("a commit from the move on configures in gen-inhibit/firmware/",
         S.wican_tree("W", "43f0ae2", REPO) == os.path.join("W", "gen-inhibit", "firmware")),
    ] + check_env_checks()
    fails = 0
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
