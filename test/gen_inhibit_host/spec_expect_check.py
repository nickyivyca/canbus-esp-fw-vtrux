#!/usr/bin/env python3
"""Judge selected scenarios against the SPEC, not against their goldens.

Gen-inhibit tester, 2026-10-08. A golden records what the core did when it was
blessed; for a scenario whose spec outcome the core may not meet, blessing would
pin the failure as correct (README: "--bless is not a way to make a failing test
pass"). These scenarios are therefore checked here against an expectation
written from the spec, and are left without goldens until the core meets it.

Only spec 7 TRIP 6 is judged. Each row: scenario, whether trip 6 must latch,
the latest trace time by which it must have, the scenario's first bus error,
and why. The existing err-rate-trip / err-rate-under rows are the CONTROLS:
they show the checker can see trip 6 and can see its absence, so a verdict on
the new rows is not a check that cannot fail.

Another trip is a note, not a verdict: err-rate-under's command train ends at
8 s, before the scenario does, so trip 2 ("bus lost") latches at 8.58 s -- its
golden records it -- and the first version of this checker failed that
control for it, which says nothing about trip 6. A row is INCONCLUSIVE when
the inhibit was not live before its first error, or when another trip latched
before trip 6 was due.

    make && python3 make_scenarios.py && python3 spec_expect_check.py
Exit status 0 only if every row passes.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
TRIP6 = "error-frame rate exceeded"

# (scenario, trip 6 required?, latch no later than (us), first error (us), why)
ROWS = [
    ("err-rate-trip", True, 3500000, 2100000, "control: 12 errors in 1.2 s"),
    ("err-rate-under", False, None, 2100000, "control: 9 errors, never 10"),
    ("err-rate-spread-10", True, 14000000, 5000000,
     "10 errors at 1/s from 5 s, the 10th at 14 s: within a 10 s window"),
    ("err-rate-spread-9", False, None, 5000000, "9 errors at 1.1 s from 5 s: never 10"),
]
STATE_RE = re.compile(r"^(\d+) (?:STATE|FINAL) .* abort=(.*?) latched=(\d)")
LIVE_RE = re.compile(r"^(\d+) EV INHIBIT_LIVE ")


def first_latch(trace):
    """(time_us, reason) of the first STATE/FINAL line with latched=1, or None."""
    for ln in trace.splitlines():
        m = STATE_RE.match(ln)
        if m and m.group(3) == "1":
            return int(m.group(1)), m.group(2)
    return None


def first_live(trace):
    for ln in trace.splitlines():
        m = LIVE_RE.match(ln)
        if m:
            return int(m.group(1))
    return None


def run(name):
    with open(os.path.join(SCN, name + ".scn"), "rb") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("ascii", "replace")


def judge(lat, live_us, want_trip6, by_us, first_err_us):
    if live_us is None or live_us >= first_err_us:
        return "INCONCLUSIVE", "not live before the first error (live at %s)" % live_us
    t6 = lat is not None and TRIP6 in lat[1]
    other = lat is not None and not t6
    if want_trip6:
        if t6:
            if by_us is not None and lat[0] > by_us:
                return "FAIL", "trip 6 at %d us, after %d us" % (lat[0], by_us)
            return "PASS", "trip 6 latched at %d us" % lat[0]
        if other and (by_us is None or lat[0] <= by_us):
            return "INCONCLUSIVE", "another trip first: %r at %d us" % (lat[1], lat[0])
        return "FAIL", "no trip 6" + (" (later: %r at %d us)" % (lat[1], lat[0])
                                      if other else "")
    if t6:
        return "FAIL", "trip 6 latched at %d us" % lat[0]
    return "PASS", "no trip 6" + (" (note: %r at %d us)" % (lat[1], lat[0]) if other else "")


def main():
    bad = 0
    for name, want, by_us, first_err, why in ROWS:
        rc, out = run(name)
        if rc != 0:
            print("[ERROR       ] %-20s host_runner rc %d" % (name, rc))
            bad += 1
            continue
        v, detail = judge(first_latch(out), first_live(out), want, by_us, first_err)
        bad += v != "PASS"
        print("[%-12s] %-20s %s | live from %s us | %s"
              % (v, name, detail, first_live(out), why))
    print("\n%d of %d rows pass" % (len(ROWS) - bad, len(ROWS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
