"""Offline test for released_into_hold: real logs, then mutations of them.

WHY THIS EXISTS. Rev 1 of the check latched the FIRST entry into mode 3
anywhere in the charger log. Above the evap ceiling (spec 7, the 83 %
start) the VCU commands Low Power during the 75 s arm delay, and spec 3
requires that command to reach the charger untouched -- so a CORRECT run
enters mode 3 twice, and rev 1 read the override's return to CHARGER as
the charger leaving the hold. It failed `evap-override-above-ceiling` on
2026-10-04 while every other assertion in that scenario passed.

It was invisible in the other eight scenarios of that run because they
start at 79 %, where Low Power only arrives at the ceiling with the
override already in place: one entry, so the single-entry assumption
held. A check that is wrong in exactly one configuration and right in
eight is the shape this project keeps meeting.

Rev 2 anchors to the core's own RELEASE. Entries before it are legal and
are REPORTED in the why-string rather than skipped, so they stay visible.

WHAT IS PROVEN HERE. Passing the real logs only shows the check still
says yes to runs it already said yes to -- which rev 1 also did for four
of the five. The mutations are the half that can fail: each one breaks
exactly one clause and must be rejected FOR THAT CLAUSE, so a check that
collapsed every refusal into one message would not pass this file.

    py -3.14 projects/vtrux/tools/interposer/test_released_into_hold.py
    python3.13 projects/vtrux/tools/interposer/test_released_into_hold.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_scenario as R                                    # noqa: E402

RUN = (HERE.parents[1] / "notes" / "artifacts" / "interposer-runs"
       / "acceptance-1x-2026-10-04")

# The scenarios that declare released_into_hold() and have finished.
REAL = ["evap-override", "evap-override-above-ceiling",
        "evap-override-early-bms", "evap-override-imbalanced",
        "evap-override-16a"]

# The release in the above-ceiling log, and the line that marks teardown.
REL_LINE = ("15:12:47 chg charger: mode CHARGER -> CHARGER_LOW_POWER "
            "(setpoint 3.00 A)")
PULL = "15:22:47 chg charger: HANDLE PULLED"

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def logs(name):
    c = (RUN / ("%s.charger.log" % name)).read_text(errors="replace")
    i = (RUN / ("%s.interposer.log" % name)).read_text(errors="replace")
    return c, i


def refuses(chg, intp, fragment, what):
    """The check must say no, and say no FOR THE STATED REASON."""
    ok, why = R._released_into_hold(chg, intp, 1.0, 4.0)
    if ok:
        check(False, "%s -- but the check PASSED it: %s" % (what, why))
    else:
        check(fragment in why,
              "%s -> %s" % (what, why if fragment in why
                            else "WRONG REASON: " + why))


def main():
    print("--- released_into_hold rev %d ---" % R.RELEASED_INTO_HOLD_REV)

    # A missing corpus must refuse, not quietly pass with zero cases.
    missing = [n for n in REAL
               if not (RUN / ("%s.charger.log" % n)).exists()]
    if missing:
        print("TEST FAULT: no saved logs for %s under %s. This test asserts "
              "nothing without them, so it reports a fault rather than a "
              "pass." % (", ".join(missing), RUN))
        return 2

    print("\nreal logs -- every scenario that declares the check:")
    for name in REAL:
        chg, intp = logs(name)
        ok, why = R._released_into_hold(chg, intp, 1.0, 4.0)
        check(ok, "%-30s %s" % (name, why))

    # The regression itself, stated as its own assertion.
    chg, intp = logs("evap-override-above-ceiling")
    ok, why = R._released_into_hold(chg, intp, 1.0, 4.0)
    check(ok and "earlier entry into mode 3" in why,
          "above-ceiling passes AND the pre-release entry is reported, not "
          "skipped -- it is visible in the why-string")
    ok2, why2 = R._released_into_hold(*logs("evap-override"), 1.0, 4.0)
    check(ok2 and "earlier entry" not in why2,
          "a 79 % start reports NO earlier entry, so the clause is not "
          "boilerplate that appears either way")

    print("\nmutations of the above-ceiling log -- each must be rejected, "
          "for its own reason:")

    assert REL_LINE in chg and PULL in chg, "fixture lines moved"

    refuses(chg.replace(REL_LINE + "\n", ""), intp,
            "never entered mode 3 after the release",
            "the release entry deleted (the spec 4.1 defect)")

    exit_after = chg.replace(
        PULL, "15:15:00 chg charger: mode CHARGER_LOW_POWER -> CHARGER "
              "(setpoint 0.20 A)\n" + PULL)
    refuses(exit_after, intp, "left mode 3 again",
            "an exit from the hold after the release")

    refuses(chg.replace(REL_LINE,
                        REL_LINE.replace("mode CHARGER ->",
                                         "mode EXPORT_POWER ->")),
            intp, "not CHARGER",
            "the release entered mode 3 from EXPORT_POWER")

    refuses(chg.replace(REL_LINE,
                        "15:12:47 chg charger: state 12 -> 15 (mode=CHARGER "
                        "flow=1 faults=none)\n" + REL_LINE),
            intp, "state 15, not 12",
            "the charger is in state 15 at the release entry")

    refuses(chg.replace(REL_LINE,
                        REL_LINE.replace("3.00 A", "9.00 A")),
            intp, "outside the hold band",
            "the hold setpoint is 9.00 A, above the band")

    refuses(chg, "\n".join(l for l in intp.splitlines()
                           if "[TERMINATED] RELEASE" not in l),
            "never logged a RELEASE",
            "no RELEASE in the interposer log")

    # ... and that last one must NOT be reported as a charger fault.
    _ok, why = R._released_into_hold(
        chg, "\n".join(l for l in intp.splitlines()
                       if "[TERMINATED] RELEASE" not in l), 1.0, 4.0)
    check("never entered mode 3" not in why,
          "a missing RELEASE is its own refusal, NOT 'the charger never "
          "entered mode 3' -- those send you to different files")

    print("\nmidnight:")
    wrapped = R._stamped("23:59:59 a\n00:00:01 b\n")
    check(wrapped[1][0] - wrapped[0][0] == 2,
          "a log crossing midnight unwraps to +2 s, not -86398 s")
    jitter = R._stamped("12:00:05 a\n12:00:04 b\n")
    check(jitter[1][0] - jitter[0][0] == -1,
          "two lines a second out of order stay a second apart, and do NOT "
          "trigger a day rollover")

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ntest_released_into_hold: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
