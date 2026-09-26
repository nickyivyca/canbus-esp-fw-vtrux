#!/usr/bin/env python3
"""E3: gi_config_defaults() against the numbers the SPEC states.

    make -C . config_probe && python3 config_vs_spec.py

WHY THIS IS NOT "WRITING THE CONSTANT TWICE". I left the thresholds out of
invariants.py on the grounds that asserting a constant against a copy of itself
proves nothing, and that was right about a copy kept in the test. It was wrong
about this: the SPEC is already a second, independent statement of these
numbers, written for a human and reviewed by one. A check that compares the
built firmware against the spec is traceability -- it fails when the code and
the document disagree, which is the failure that actually happens.

The reviewing session made this argument on 2026-09-25 after a round-5 mutation
moved fresh_us to 0.6 s and was caught by 22 goldens but by no stated rule.

EACH ROW CITES ITS SECTION. If a value here has no section to cite, it is not
traceable and the entry says so rather than inventing one -- which is how the
error-rate numbers were found to exist only in the code.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "config_probe")

# field, expected, spec section, what the spec says
EXPECTED = [
    ("soc_min_raw", 2100, "6.2",
     "released the moment bms_soc_hires goes below 21.00 %"),
    # NOW TRACEABLE. Spec 6.2 states the count as of 2026-09-25 -- "the reading
    # must persist for 5 consecutive valid 0x411 readings" -- so this row goes
    # back to comparing against a number the document actually gives. It spent
    # a day as CODE-ONLY, which was the honest state to be in: the check said
    # the gap was in the spec rather than in the firmware, and the spec is where
    # it got closed.
    ("soc_debounce", 5, "6.2",
     "must persist for 5 consecutive valid 0x411 readings"),
    ("start_abort_rpm", 300, "7 trip 4",
     "GENE_RotSpd >= 300 rpm"),
    ("rpm_debounce_us", 300000, "7 trip 4",
     "sustained for 0.3 s"),
    ("fresh_us", 500000, "7",
     "received within fresh_us (0.5 s) of now"),
    ("fault_fresh_us", 1000000, "7 (Freshness)",
     "0x617 gets 1.0 s; every other signal stays on 0.5 s"),
    ("err_window_us", 10000000, "7 error-frame para",
     "10 errors in 10 s"),
    ("err_min_trip", 10, "7 error-frame para",
     "10 errors in 10 s"),
    ("diag_period_ms", 300, "10",
     "round-robin at ~1 Hz each over four pages"),
    ("max_rx_errors", 20, "7",
     "a run of 20 consecutive hard receive errors"),
]


def main():
    if not os.path.exists(PROBE):
        print("build the probe first:  make config_probe")
        return 2
    out = subprocess.run([PROBE], capture_output=True, text=True).stdout
    got = {}
    for line in out.splitlines():
        m = re.match(r"^(\w+)\s+(-?\d+)$", line.strip())
        if m:
            got[m.group(1)] = int(m.group(2))

    if not got:
        print("the probe printed nothing parseable -- this run proved nothing")
        return 2

    bad = untraced = 0
    print("%-18s %12s %12s   %-22s %s"
          % ("field", "built", "spec says", "section", "what it says"))
    for field, want, section, why in EXPECTED:
        have = got.get(field)
        if have is None:
            print("%-18s %12s %12s   %-22s MISSING FROM THE BUILD"
                  % (field, "-", want if want is not None else "-", section))
            bad += 1
            continue
        if want is None:
            # No number in the spec: report the built value and say so. Never
            # print a "spec says" column for a value the spec does not state.
            untraced += 1
            print("%-18s %12d %12s   %-22s %s"
                  % (field, have, "CODE-ONLY", section, why[:40]))
            continue
        mark = "" if have == want else "   <-- DISAGREES"
        print("%-18s %12d %12d   %-22s %s%s"
              % (field, have, want, section, why[:40], mark))
        if have != want:
            bad += 1

    extra = sorted(set(got) - {f for f, _, _, _ in EXPECTED})
    if extra:
        print("\nIn the build but not in this table, so NOT traceable to the "
              "spec: %s" % ", ".join(extra))

    print()
    if bad:
        print("%d value(s) disagree with the spec. Either the code changed "
              "without the spec, or the spec changed without the code -- both "
              "are the bug this check exists for." % bad)
    else:
        print("every value that HAS a spec number matches the section it cites")
    if untraced:
        print("%d value(s) are code-only: the spec requires the behaviour but "
              "states no number, so nothing here can check them. That is a gap "
              "in the spec, not in this script." % untraced)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
