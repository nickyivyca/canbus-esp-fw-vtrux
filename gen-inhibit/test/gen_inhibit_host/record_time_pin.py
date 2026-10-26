#!/usr/bin/env python3
"""Pin host_runner to recording each error at its directive's own instant.

spec_expect_check.py judges trip 6 from the error times in the .scn stimulus, not from
the trace. That stands in for "the device's recorded times" (spec 7, 2026-10-20) only
because host_runner wakes for every directive and runs gi_tick() at that same instant,
so the core records each error at exactly its .scn time. The reviewer accepted that
reading on this condition (2026-10-20): a test that fails if host_runner ever adds a
lag, instead of the judge drifting silently inside its tick tolerance.

The check: on the scenarios below, each live before its burst and tripping at its 10th
error, the trace's first trip 6 (`EV ABORT a=6`) is at exactly the .scn time of the 10th
error, with no tolerance. A lag of any size in recording moves it.

    make && python3 make_scenarios.py && python3 record_time_pin.py

Exit status 0 only if every row holds. Gen-inhibit tester.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
ROWS = ("err-rate-edge-10p0", "err-rate-edge-9p5", "err-rate-spread-10")
TRIP6_CODE = 6
ABORT_RE = re.compile(r"^(\d+) EV ABORT a=(\d+) ")


def scn_errors(text):
    """Error times from the .scn's `bus <t> <en> <ours> <errvalid> <count>` directives:
    a rise of k in the cumulative count is k errors at t."""
    out, last = [], 0
    for ln in text.splitlines():
        w = ln.split()
        if len(w) == 6 and w[0] == "bus":
            t, valid, count = int(w[1]), int(w[4]), int(w[5])
            if valid and count > last:
                out += [t] * (count - last)
            if valid:
                last = count
    return out


def first_trip6(trace):
    for ln in trace.splitlines():
        m = ABORT_RE.match(ln)
        if m and int(m.group(2)) == TRIP6_CODE:
            return int(m.group(1))
    return None


def row(name):
    path = os.path.join(SCN, name + ".scn")
    text = open(path, encoding="ascii", errors="replace").read()
    with open(path, "rb") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=300)
    if r.returncode != 0:
        return False, "host_runner rc %d" % r.returncode
    errs = scn_errors(text)
    if len(errs) < 10:
        return False, "only %d errors in the .scn" % len(errs)
    t = first_trip6(r.stdout.decode("ascii", "replace"))
    if t is None:
        return False, "no trip 6 in the trace (10th error at %d us)" % errs[9]
    return t == errs[9], "trip 6 at %d us, the 10th error at %d us (%+d us)" % (t, errs[9], t - errs[9])


def main():
    bad = 0
    for name in ROWS:
        ok, why = row(name)
        bad += not ok
        print("  %-4s %-26s %s" % ("ok" if ok else "FAIL", name, why))
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
