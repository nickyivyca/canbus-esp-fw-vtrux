#!/usr/bin/env python3
"""Test of respond_entry_profile.py (F4: RESPOND's receive loss over time from the mode POST, into
live traffic; 2026-11-27), on synthetic e4 records: the reads put on the replay's clock by perf_base
and timed from the POST's send; the baseline the last read sent before the POST, a null rx_missed
there counted from 0 and said so; each rise of rx_missed or ctr_bad after the POST, ctr_bad alone
too; the counts added by each mark, "no read" for a span with no read; an unpolled record giving the
page after the arm; a record that is not a RESPOND --arm-after arm, is void, lacks perf_base, or has
no read before or after the POST, counted out and named, never read as zero; exit 1 then.

    python3.13 -B test_respond_entry_profile.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ and runs this test on it: each must fail a
check (test_respond_entry_profile_mutants.py runs it so). Gen-inhibit tester, 2026-11-27.
"""
import contextlib
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("ENTRY_PROFILE_DIR") or HERE)
import respond_entry_profile as P  # noqa: E402

fails = []
oks = 0
BASE = 100.0      # perf_base: the replay's base on the perf clock
POST = 5.0        # the POST's send on the replay's clock


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def row(t, rx, ctr, mode=2):
    """A Hammer row sent `t` s after the POST: [perf time, msgs_to_rx, rx_missed, mode, self_off, ctr_bad]."""
    return [BASE + POST + t, 0, rx, mode, None, ctr]


def rec(series, mode="respond", arm_after=5.0, post_s=(POST, POST + 0.03), perf_base=BASE, void=False,
        device=None):
    a = {"arm_after_s": arm_after, "post_s": list(post_s) if post_s else None, "post_http": 200,
         "poll_hz": 2.0 if series else 0.0, "queue_series": series, "perf_base": perf_base,
         "void": void, "void_reasons": ["x"] if void else [],
         "device": device if device is not None else {"ctr_bad": 1, "drv": {"rx_missed": 16}}}
    return {"mode": mode, "arms": {"idle": a}}


def prof(r):
    """P.profile(r) with every key a check reads, so a mutant that counts a record out fails a
    check instead of crashing the test."""
    p = {"by": {}, "rises": [], "notes": [], "reads_before": None, "baseline_t": None, "last_t": None,
         "last": None, "polled": None, "end": None}
    p.update(P.profile(r))
    return p


def main():
    s = [row(-1.0, None, 0, 0), row(-0.5, None, 0, 0), row(0.0, 0, 0), row(0.02, 16, 1),
         row(0.5, 16, 1), row(3.0, 16, 1), row(12.0, 16, 2)]
    p = prof(rec(s))
    check("the baseline is the last read sent before the POST", p["baseline_t"] == -0.5 and p["reads_before"] == 2,
          p)
    check("a null rx_missed before the POST counts from 0, and the note says so",
          any("counted from 0" in n for n in p["notes"]) and p["by"].get(1.0) == (16, 1), p["notes"])
    r0 = (p["rises"] or [{}])[0]
    check("a read sent at the POST's send is after it, timed from the send, not the reply",
          r0.get("t") == 0.02 and r0.get("rx_missed") == 16 and r0.get("ctr_bad") == 1, p["rises"])
    check("a rise of ctr_bad alone is a rise", [(r["t"], r["rx_missed"], r["ctr_bad"]) for r in p["rises"]]
          == [(0.02, 16, 1), (12.0, 16, 2)], p["rises"])
    check("added by each mark; a span with no read is 'no read'",
          p["by"] == {1.0: (16, 1), 5.0: (16, 1), 15.0: (16, 2), 30.0: None, 60.0: None}, p["by"])
    check("the last read's time and counts", p["last_t"] == 12.0 and p["last"] == (16, 2), (p["last_t"], p["last"]))
    p = prof(rec([row(-0.3, 5, 2), row(0.4, 7, 2), row(1.5, 7, 2)]))
    check("a baseline with counts is subtracted", p["by"].get(1.0) == (2, 0) and p["by"].get(5.0) == (2, 0)
          and p["notes"] == [] and "why" not in p, p)
    p = prof(rec([[BASE + POST - 0.2, 0, 3, 2, None], [BASE + POST + 0.2, 0, 4, 2, None]]))
    check("rows with no ctr_bad (before 2026-11-27): ctr_bad not counted, the note says so",
          p["by"].get(1.0) == (1, None) and any("ctr_bad absent" in n for n in p["notes"]), p)
    p = prof(rec([], device={"ctr_bad": 0, "drv": {"rx_missed": 13}}))
    check("an unpolled record gives the page after the arm", "why" not in p and p["polled"] is False
          and p["end"] == (13, 0), p)
    outs = [("an INHIBIT record", rec(s, mode="inhibit"), "not respond"),
            ("a record with no --arm-after POST", rec(s, arm_after=None), "no --arm-after"),
            ("a void arm", rec(s, void=True), "arm void"),
            ("a polled record with no perf_base", rec(s, perf_base=None), "no perf_base"),
            ("a polled record with no POST time", rec(s, post_s=None), "no POST time"),
            ("no read before the POST", rec([row(0.1, 0, 0)]), "no read before"),
            ("no read after the POST", rec([row(-0.1, 0, 0)]), "no read after")]
    for name, r, want in outs:
        why = P.profile(r).get("why")
        check("counted out, named: %s" % name, why is not None and want in why, why)
    import gi_paths
    d = gi_paths.scratch("test-respond-entry-profile")
    gi_paths.remove_tree(d)
    os.makedirs(d)
    try:
        paths = []
        for k, r in enumerate([rec(s), rec([]), rec(s, mode="inhibit")]):
            paths.append(os.path.join(d, "r%d.json" % k))
            with open(paths[-1], "w", encoding="ascii") as f:
                json.dump(r, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = P.main(paths)
        text = out.getvalue()
        check("main: a record counted out is named and makes exit 1",
              rc == 1 and "r2.json: COUNTED OUT: mode 'inhibit', not respond" in text
              and text.splitlines()[-1] == "3 record(s), 1 counted out", text[-300:])
        check("main: the polled records' figures per mark, the unpolled one's page",
              "  1 s: 16/1\n" in text and "  30 s: no read\n" in text
              and "r1.json: RESPOND, POST HTTP 200, unpolled; page after the arm: rx_missed 16, ctr_bad 1" in text,
              text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = P.main(paths[:2])
        check("main: every record gave figures -> exit 0", rc == 0, out.getvalue()[-200:])
    finally:
        gi_paths.remove_tree(d)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


MUTANTS = [
    ("the baseline the first read before the POST", "    b = before[-1]\n", "    b = before[0]\n"),
    ("no note for a null baseline", '        notes.append("rx_missed null before the POST (no drv): counted from 0")\n',
     "        pass\n"),
    ("time zero the POST's reply", '    post = (a.get("post_s") or [None])[0]\n',
     '    post = (a.get("post_s") or [None])[-1]\n'),
    ("perf_base ignored", 'rows.append({"t": round(r[0] - base - post, 4)', 'rows.append({"t": round(r[0] - post, 4)'),
    ("a read at the POST's send taken as before it", '    before = [r for r in rows if r["t"] < 0]\n',
     '    before = [r for r in rows if r["t"] <= 0]\n'),
    ("a rise of ctr_bad alone missed", "or (ctr is not None and ctr != last[1])", "or False"),
    ("a span with no read given the last value", "        by[s] = added(upto[-1]) if span else None\n",
     "        by[s] = added(upto[-1]) if upto else None\n"),
    ("a void arm read", '    if a.get("void"):\n', "    if False:\n"),
    ("an unpolled record counted out", '        out["polled"] = False\n        return out\n',
     '        return {"why": "no series"}\n'),
    ("exit 0 with a record counted out", "    return 0 if not bad else 1\n", "    return 0\n"),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "respond_entry_profile.py"), encoding="utf-8", newline="").read()
    if subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True).returncode != 0:
        print("ERROR    control: the unmutated tool fails this test")
        return 1
    print("PASS     control: the unmutated tool passes this test")
    base = gi_paths.scratch("mutants-respond_entry_profile")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "respond_entry_profile.py"), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, ENTRY_PROFILE_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "  (%s)" % failed[0] if failed else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
