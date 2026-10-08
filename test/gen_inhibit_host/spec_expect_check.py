#!/usr/bin/env python3
"""Judge selected scenarios against the SPEC, not against their goldens.

Gen-inhibit tester, 2026-10-08. A golden records what the core did when it was
blessed; for a scenario whose spec outcome the core may not meet, blessing would
pin the failure as correct (README: "--bless is not a way to make a failing test
pass"). These scenarios are therefore checked here against an expectation
written from the spec, and are left without goldens until the core meets it.

Each row: scenario, the abort reason it must latch (or None for "no abort"),
and the latest trace time by which it must have latched. The existing
err-rate-trip / err-rate-under rows are the CONTROLS: they show the checker can
see a trip and can see its absence, so a PASS on the new rows is not a check
that cannot fail.

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

# (scenario, required abort or None, latch no later than (us), why)
ROWS = [
    ("err-rate-trip", TRIP6, 3500000, "control: 12 errors in 1.2 s (spec 7 trip 6)"),
    ("err-rate-under", None, None, "control: 9 errors, never 10 (spec 7 trip 6)"),
    ("err-rate-spread-10", TRIP6, 14000000,
     "10 errors at 1/s from 5 s, the 10th at 14 s: within a 10 s window (spec 7 trip 6)"),
    ("err-rate-spread-9", None, None, "9 errors at 1.1 s from 5 s: never 10 (spec 7 trip 6)"),
]
STATE_RE = re.compile(r"^(\d+) (?:STATE|FINAL) .* abort=(.*?) latched=(\d)")


def first_latch(trace):
    """(time_us, reason) of the first STATE/FINAL line with latched=1, or None."""
    for ln in trace.splitlines():
        m = STATE_RE.match(ln)
        if m and m.group(3) == "1":
            return int(m.group(1)), m.group(2)
    return None


def run(name):
    with open(os.path.join(SCN, name + ".scn"), "rb") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("ascii", "replace")


def judge(lat, want, by_us):
    if want is None:
        return ("PASS", "no abort") if lat is None else ("FAIL", "latched %r at %d us" % (lat[1], lat[0]))
    if lat is None:
        return "FAIL", "no abort latched"
    if want not in lat[1]:
        return "FAIL", "latched %r, not %r" % (lat[1], want)
    if by_us is not None and lat[0] > by_us:
        return "FAIL", "latched at %d us, after %d us" % (lat[0], by_us)
    return "PASS", "latched %r at %d us" % (lat[1], lat[0])


def main():
    bad = 0
    for name, want, by_us, why in ROWS:
        rc, out = run(name)
        if rc != 0:
            print("[ERROR ] %-22s host_runner rc %d" % (name, rc))
            bad += 1
            continue
        v, detail = judge(first_latch(out), want, by_us)
        bad += v != "PASS"
        print("[%-6s] %-22s %s | %s" % (v, name, detail, why))
    print("\n%d of %d rows pass" % (len(ROWS) - bad, len(ROWS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
