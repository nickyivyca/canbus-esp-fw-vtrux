#!/usr/bin/env python3
"""Judge the trip 6 scenarios against the SPEC, not against their goldens.

Gen-inhibit tester, 2026-10-08. A golden records what the core did when it was
blessed; for a rule the core may not meet, blessing would pin the failure as
correct (README: "--bless is not a way to make a failing test pass"). These
scenarios are checked here instead, and have no goldens.

THE WINDOW, IN ONE PLACE. Spec 7: "trip at 10 or more error frames within a
10 s window". Whether the window slides or tumbles, and whether a tenth frame
exactly 10.0 s after the first counts, is with the user (2026-10-08).
READINGS below lists the candidates; for every scenario the checker reads the
error times back out of the .scn (the cumulative count on its "bus"
directives, only from go-live on) and prints when each reading requires trip 6.

VERDICTS. Each row says what the spec, as ruled so far, requires:
  latch    trip 6 must latch at the error that completes the first 10-in-10 s
           (sliding-strict), not before it and within TICK_SLACK_US after.
           Used only where the reviewer has ruled the outcome reading-
           independent (10 in 9 s, a single +12, two separate batches).
  nolatch  trip 6 must never latch.
  pending  the outcome depends on the unruled readings: PENDING, the readings'
           requirements and what the core did are shown, nothing fails.
Only TRIP 6 is judged; another trip is a note, or INCONCLUSIVE if it came
before trip 6 was due. A row is INCONCLUSIVE unless the inhibit was live before
the first error. err-rate-trip / err-rate-under are the controls (the checker
sees trip 6 and its absence). err-rate-spread-9 can not fail on a core whose
window misses spread bursts, so it pins only the threshold; the edge pair pins
the window once ruled.

    make && python3 make_scenarios.py && python3 spec_expect_check.py
    python3 spec_expect_check.py --selftest     # the reading model, no runner
Exit status 0 unless a row FAILs, is INCONCLUSIVE or errors. PENDING does not fail.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
TRIP6 = "error-frame rate exceeded"

THRESHOLD = 10              # spec 7
WINDOW_US = 10 * 1000000    # spec 7
TICK_SLACK_US = 100000      # a latch may trail its due error by one poll
# A tumbling window's origin is "go-live", known here only to about one tick
# (the trace's INHIBIT_LIVE time; the first tick after it is ~20 ms later). An
# error this close to a tumble edge makes the row unable to tell the readings
# apart: the first err-rate-two-spans put its tenth error 0-20 ms from one
# (reviewer, 2026-10-08). A row is flagged EDGE when shifting the origin by
# EDGE_US changes what the tumbling reading requires.
EDGE_US = 50000
READINGS = ("sliding-strict", "sliding-inclusive", "tumbling-from-live")

ROWS = [
    ("err-rate-trip", "latch", "control: 12 errors in 1.2 s"),
    ("err-rate-under", "nolatch", "control: 9 errors in 0.9 s"),
    ("err-rate-spread-9", "nolatch", "9 errors at 1.1 s (threshold only, not a window control)"),
    ("err-rate-spread-10", "latch", "10 at 1/s from 5 s"),
    ("err-rate-spread-10-at-1p5", "latch", "10 at 1/s from 1.5 s"),
    ("err-rate-spread-10-at-2p5", "latch", "10 at 1/s from 2.5 s"),
    ("err-rate-spread-10-at-9p0", "latch", "10 at 1/s from 9.0 s"),
    ("err-rate-spread-10-at-11p5", "latch", "10 at 1/s from 11.5 s"),
    ("err-rate-edge-9p5", "pending", "10 over 9.5 s from 5 s"),
    ("err-rate-edge-10p5", "pending", "10 over 10.5 s from 5 s"),
    ("err-rate-two-spans", "latch", "9 early, 10 at 1/s from 33.5 s"),
    ("err-rate-sustained-4", "pending", "4 in every 10 s for 60 s"),
    ("err-rate-jump-12", "latch", "+12 in one reading at 5 s"),
    ("err-rate-rearm-clears", "pending",
     "9, disarm, re-arm, 1: new rule with the user; a re-arm that starts the window "
     "fresh requires never"),
]
STATE_RE = re.compile(r"^(\d+) (?:STATE|FINAL) .* abort=(.*?) latched=(\d)")
LIVE_RE = re.compile(r"^(\d+) EV INHIBIT_LIVE ")
BUS_RE = re.compile(r"^bus (\d+) (\d+) (\d+) (\d+) (\d+)\s*$")


# ------------------------------------------------------- the reading model

def errors_from_scn(text):
    """Error times from cumulative 'bus' counts: a rise of k gives k errors."""
    out, last = [], 0
    for ln in text.splitlines():
        m = BUS_RE.match(ln.strip())
        if not m:
            continue
        t, c = int(m.group(1)), int(m.group(5))
        if c > last:
            out += [t] * (c - last)
        last = c
    return sorted(out)


def due(errs, reading, live_us):
    """Time of the error at which `reading` first requires trip 6, or None."""
    errs = sorted(t for t in errs if live_us is not None and t >= live_us)
    if reading == "tumbling-from-live":
        counts = {}
        for t in errs:
            k = (t - live_us) // WINDOW_US
            counts[k] = counts.get(k, 0) + 1
            if counts[k] >= THRESHOLD:
                return t
        return None
    inclusive = reading == "sliding-inclusive"
    j = 0
    for i, t in enumerate(errs):
        while (t - errs[j] > WINDOW_US) if inclusive else (t - errs[j] >= WINDOW_US):
            j += 1
        if i - j + 1 >= THRESHOLD:
            return t
    return None


def edge_sensitive(errs, live_us):
    """True if moving the tumble origin by +/- EDGE_US changes what the
    tumbling reading requires. An error merely sitting near an edge is not
    enough: the first version of this flag fired on rows whose outcome no
    origin shift could change."""
    if live_us is None:
        return False
    outs = {due(errs, "tumbling-from-live", live_us + d) for d in (-EDGE_US, 0, EDGE_US)}
    return len(outs) > 1


def selftest():
    ten_9s = [k * 1000000 for k in range(10)]
    ten_10s = [k * 1000000 * 10 // 9 for k in range(10)]   # last exactly 10.0 s after first
    straddle = [5000000 + k * 1000000 for k in range(10)]  # 5..14 s, tumbling from 0 splits 5/5
    cases = [
        ("10 in 9 s, strict", ten_9s, "sliding-strict", 0, 9000000),
        ("10 in exactly 10.0 s, strict -> none", ten_10s, "sliding-strict", 0, None),
        ("10 in exactly 10.0 s, inclusive", ten_10s, "sliding-inclusive", 0, 10000000),
        ("5..14 s, tumbling from 0 -> none", straddle, "tumbling-from-live", 0, None),
        ("5..14 s, sliding strict", straddle, "sliding-strict", 0, 14000000),
        ("+12 at once", [5000000] * 12, "tumbling-from-live", 0, 5000000),
        ("errors before go-live do not count", ten_9s, "sliding-strict", 500000, None),
        ("scn parse: cumulative +12", errors_from_scn("bus 5000000 1 1 1 12\n"), "sliding-strict",
         0, 5000000),
    ]
    ten_32 = [32000000 + k * 1000000 for k in range(10)]      # the old two-spans batch
    ten_33p5 = [33500000 + k * 1000000 for k in range(10)]    # the new one
    on_edge_harmless = [5000000 + k * 1000000 for k in range(10)]  # 11.0 s on an edge
    edge_cases = [
        ("old two-spans (32..41 s), origin 1.0 s: sensitive", ten_32, 1000000, True),
        ("new two-spans (33.5..42.5 s): not sensitive", ten_33p5, 1000000, False),
        ("5..14 s: an error on an edge, outcome unchanged", on_edge_harmless, 1000000, False),
    ]
    bad = 0
    for label, errs, rd, live, want in cases:
        got = due(errs, rd, live)
        ok = got == want
        bad += not ok
        print("  %-4s %-44s %s" % ("ok" if ok else "FAIL", label, got))
    for label, errs, live, want in edge_cases:
        got = edge_sensitive(errs, live)
        ok = got == want
        bad += not ok
        print("  %-4s %-44s %s" % ("ok" if ok else "FAIL", label, got))
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL"))
    return 1 if bad else 0


# ------------------------------------------------------------- the check

def first_latch(trace):
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


def judge(expect, lat, live_us, errs):
    """(verdict, detail, {reading: due})."""
    dues = {r: due(errs, r, live_us) for r in READINGS}
    if not errs or live_us is None or live_us >= errs[0]:
        return "INCONCLUSIVE", "not live before the first error (live at %s)" % live_us, dues
    t6 = lat is not None and TRIP6 in lat[1]
    other = lat is not None and not t6
    seen = ("trip 6 at %d us" % lat[0]) if t6 else (
        "no trip 6" + (" (note: %r at %d us)" % (lat[1], lat[0]) if other else ""))
    if expect == "pending":
        return "PENDING", seen, dues
    if expect == "nolatch":
        return ("FAIL" if t6 else "PASS"), seen, dues
    want = dues["sliding-strict"]
    if want is None:
        return "ERROR", "row says latch but no reading requires it", dues
    if other and lat[0] <= want:
        return "INCONCLUSIVE", "another trip first: " + seen, dues
    if not t6:
        return "FAIL", seen + "; due at %d us" % want, dues
    if lat[0] < want:
        return "FAIL", seen + ", BEFORE the error that completes ten (%d us)" % want, dues
    if lat[0] > want + TICK_SLACK_US:
        return "FAIL", seen + ", later than %d us + %d" % (want, TICK_SLACK_US), dues
    return "PASS", seen + " (due %d us)" % want, dues


def run(name):
    path = os.path.join(SCN, name + ".scn")
    with open(path, "rb") as fh:
        text = fh.read().decode("ascii", "replace")
    with open(path, "rb") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=300)
    return r.returncode, r.stdout.decode("ascii", "replace"), text


def main():
    if "--selftest" in sys.argv:
        return selftest()
    bad = pending = 0
    for name, expect, why in ROWS:
        rc, out, text = run(name)
        if rc != 0:
            print("[ERROR       ] %-26s host_runner rc %d" % (name, rc))
            bad += 1
            continue
        v, detail, dues = judge(expect, first_latch(out), first_live(out),
                                errors_from_scn(text))
        bad += v not in ("PASS", "PENDING")
        pending += v == "PENDING"
        print("[%-12s] %-26s %s | %s" % (v, name, detail, why))
        errs = errors_from_scn(text)
        edge = edge_sensitive(errs, first_live(out))
        print("               %-26s due under: %s%s" % ("", ", ".join(
            "%s %s" % (r, "never" if d is None else "%.2f s" % (d / 1e6)) for r, d in dues.items()),
            ("  EDGE: a %d ms shift of the tumble origin changes the tumbling outcome"
             % (EDGE_US // 1000)) if edge else ""))
    print("\n%d of %d rows pass, %d pending a ruling, %d fail or inconclusive"
          % (len(ROWS) - bad - pending, len(ROWS), pending, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
