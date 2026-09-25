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
    ("soc_debounce", 5, "6.2",
     "the reading must persist across a debounce window (count not stated; 5 "
     "is the implemented value)"),
    ("start_abort_rpm", 300, "7 trip 4",
     "GENE_RotSpd >= 300 rpm"),
    ("rpm_debounce_us", 300000, "7 trip 4",
     "sustained for 0.3 s"),
    ("fresh_us", 500000, "7",
     "received within fresh_us (0.5 s) of now"),
    ("err_window_us", 10000000, "7",
     "10 errors in 10 s -- SEE BELOW, this was code-only until 2026-09-25"),
    ("err_min_trip", 10, "7",
     "as above"),
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

    bad = 0
    print("%-18s %12s %12s   spec" % ("field", "built", "spec says"))
    for field, want, section, why in EXPECTED:
        have = got.get(field)
        if have is None:
            print("%-18s %12s %12d   %-8s MISSING FROM THE BUILD"
                  % (field, "-", want, section))
            bad += 1
            continue
        mark = "" if have == want else "   <-- DISAGREES"
        print("%-18s %12d %12d   %-8s %s%s"
              % (field, have, want, section, why[:44], mark))
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
        print("every configured value matches the section it cites")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
