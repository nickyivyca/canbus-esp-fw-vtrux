#!/usr/bin/env python3
"""Test of b11_queue_quiet.py (B11raw's queue bound from quiet data, topic (E), 2026-11-19), on
synthetic e4 records: the reads in the arm's own mode counted, others and unanswered ones not; an arm
with a post schedule (not quiet), no series, or no read in its mode counted out and named, never read as
zero; the summary's median, p99 and maximum; the bound the largest read.

    python3.13 -B test_b11_queue_quiet.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ and runs this test on it: each must fail a
check. Gen-inhibit tester, 2026-11-19.
"""
import io
import contextlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("QUEUE_QUIET_DIR") or HERE)
import b11_queue_quiet as Q  # noqa: E402

fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def rec(mode="inhibit", series=None, sched=None):
    m = Q.MODE[mode]
    s = series if series is not None else [[0.1 * k, k % 5, 0, m, ""] for k in range(100)]
    return {"mode": mode, "arms": {"idle": {"queue_series": s, "post_schedule": sched or []}}}


def main():
    _a, mode, q, n, why = Q.arm_reads(rec())
    check("a quiet arm: every read in its mode counted", why is None and n == 100 and len(q) == 100 and max(q) == 4,
          (n, len(q), why))
    s = [[0.1, 3, 0, 3, ""], [0.2, 9, 0, 1, ""], [0.3, None, 0, 3, ""], [0.4, 2, 0, 3, ""]]
    _a, _m, q, n, why = Q.arm_reads(rec(series=s))
    check("reads in another mode, or with no msgs_to_rx, not counted", q == [3, 2] and n == 4, q)
    why = Q.arm_reads(rec(sched=[{"at_s": 10.0, "mode": 1}]))[4]
    check("an arm with a post schedule counted out: not quiet", why is not None and "not quiet" in why, why)
    why = Q.arm_reads(rec(series=[]))[4]
    check("an arm with no queue series counted out, not read as zero", why == "no queue series", why)
    why = Q.arm_reads(rec(series=[[0.1, 3, 0, 1, ""]]))[4]
    check("an arm with no read in its own mode counted out", why == "no read in the arm's own mode", why)
    check("rank: median and p99 of 1..100", Q.rank(list(range(1, 101)), 0.5) == 50 and Q.rank(list(range(1, 101)), 0.99) == 99)
    import gi_paths
    d = gi_paths.scratch("test-b11-queue-quiet")
    gi_paths.remove_tree(d)
    os.makedirs(d)
    try:
        paths = []
        for k, r in enumerate([rec("inhibit"), rec("passive", series=[[0.1, 7, 0, 4, ""], [0.2, 1, 0, 4, ""]]),
                               rec("passive", series=[])]):
            paths.append(os.path.join(d, "r%d.json" % k))
            with open(paths[-1], "w", encoding="ascii") as f:
                json.dump(r, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = Q.main(paths)
        lines = out.getvalue().splitlines()
        check("main: the bound the largest read over the quiet arms; an arm counted out named, exit 1",
              rc == 1 and "COUNTED OUT: no queue series" in lines[2]
              and lines[-1] == "bound the data supports (the largest msgs_to_rx read): 7"
              and lines[-2].startswith("all arms: n 102,"), lines)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = Q.main(paths[:2])
        check("main: every record gave reads -> exit 0", rc == 0, out.getvalue()[-200:])
    finally:
        gi_paths.remove_tree(d)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


MUTANTS = [
    ("reads in any mode counted", "row[3] == want and row[1] is not None", "row[1] is not None"),
    ("an unanswered read counted as zero", "row[3] == want and row[1] is not None]", "row[3] == want]"),
    ("a post schedule taken as quiet", '    if a.get("post_schedule"):\n', "    if False:\n"),
    ("the bound the median", '(max(every) if every else None)', '(rank(every, 0.5) if every else None)'),
    ("exit 0 with an arm counted out", "    return 0 if every and not bad else 1", "    return 0 if every else 1"),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "b11_queue_quiet.py"), encoding="utf-8", newline="").read()
    if subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True).returncode != 0:
        print("ERROR    control: the unmutated tool fails this test")
        return 1
    base = gi_paths.scratch("mutants-b11_queue_quiet")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "b11_queue_quiet.py"), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, QUEUE_QUIET_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s" % ("KILLED  " if dead else "SURVIVED", n, name))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
