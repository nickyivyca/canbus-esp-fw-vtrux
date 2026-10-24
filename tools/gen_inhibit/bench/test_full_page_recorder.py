#!/usr/bin/env python3
"""Tests full_page_recorder.field() and around(); no device. Its mutants are at
the bottom (--mutants): each must make a check fail.

    py -3.14 tools/gen_inhibit/bench/test_full_page_recorder.py [--mutants]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import full_page_recorder as R      # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:120]))
    if not ok:
        FAILS.append(label)


def main():
    page = {"tx_ok": 5, "sched": {"aborts": 2, "cls": {"inhibit": {"withdrawn": 1}}}}
    check("field: a nested value", R.field(page, "sched.cls.inhibit.withdrawn") == 1)
    check("field: a top-level value", R.field(page, "tx_ok") == 5)
    check("field: a missing step is None, not an error",
          R.field(page, "sched.cls.probe.withdrawn") is None and R.field(page, "nope") is None)
    rows = [{"t_req": 9.0, "t_resp": 9.1, "page": {"n": 1}},
            {"t_req": 9.8, "t_resp": 10.05, "page": {"n": 2}},     # straddles the instant
            {"t_req": 10.1, "t_resp": 10.2, "page": {"n": 3}},     # inside the settle
            {"t_req": 10.4, "t_resp": 10.5, "error": "timeout"},   # no page
            {"t_req": 10.6, "t_resp": 10.7, "page": {"n": 4}}]
    b, a = R.around(rows, 10.0, settle=0.3)
    check("around: before is the last read that ENDED before the instant", b and b["page"]["n"] == 1, b)
    check("around: after is the first page read wholly after instant + settle, skipping an error",
          ((a or {}).get("page") or {}).get("n") == 4, a)
    b, a = R.around(rows, 20.0)
    check("around: nothing after the recording is None", a is None and b["page"]["n"] == 4)
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("before by request time", 'before = [r for r in pages if r["t_resp"] < instant]',
     'before = [r for r in pages if r["t_req"] < instant]'),
    ("no settle", 'after = [r for r in pages if r["t_req"] >= instant + settle]',
     'after = [r for r in pages if r["t_req"] >= instant]'),
    ("errors kept", 'pages = [r for r in rows if isinstance(r.get("page"), dict)]', "pages = rows"),
    ("field never descends", '        o = o[k]\n', '        o = o\n'),
]


def mutants():
    import shutil
    import gi_paths   # here, not at the top: the mutant copies hold only this test and its target
    src = open(os.path.join(HERE, "full_page_recorder.py"), encoding="utf-8").read()
    base = gi_paths.scratch("mutants-full_page_recorder_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "full_page_recorder.py"), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:80]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
