"""Exercise skip_event_classify against synthetic witness sequences. No hardware.

WHY SYNTHETIC IS RIGHT HERE, unlike the other offline tests in this folder.
trip_reason_offline_test.py and diag_check_rules_test.py run against REAL
recorded runs, because real data was available and a hand-built fixture would
have tested my idea of the data rather than the data. There is no recorded skip
event anywhere -- no hardware reading has ever had `skipped > 0`, which is the
entire reason the skip bench is being built. So the fixtures are constructed,
and that limitation is stated rather than hidden: these cases prove the
CLASSIFIER behaves as specified on sequences of the shape the method predicts.
They cannot prove the method's shape is what hardware will produce.

The classifier is imported, not reimplemented.

    py -3.14 tools/gen_inhibit/bench/skip_event_classify_test.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skip_event_classify import (                              # noqa: E402
    CMD_ID, WITHDRAWN, LATE, ANSWERED, FILLER_HOLE, INDETERMINATE,
    classify_event, order_preserved, summarise)

FILLER = {0x030}
OTHER = 0x7F1          # a diag page: neither filler nor command


def cmd(ctr, b0=0x08):
    """A 0x051 with the given rolling counter in B5."""
    return (CMD_ID, bytes([b0, 0x00, 0x80, 0xFF, 0x7F, ctr & 0x0F]))


def filler(n=3):
    return [(0x030, bytes([0x5A] * 8))] * n


CASES = [
    # name, sequence, n_index, expected verdict
    ("answered_on_time",
     [cmd(1), cmd(1)] + filler() + [cmd(2)], 0, ANSWERED),

    # The withdrawn skip the method is built to produce: N, N+1, filler, then
    # our answer to N+1 only. Nothing ever carries N's counter.
    ("withdrawn",
     [cmd(1), cmd(2)] + filler() + [cmd(2), cmd(3)], 0, WITHDRAWN),

    # The late skip: our answer to N appears AFTER the VCM's next command.
    ("late_after_next",
     [cmd(1), cmd(2), cmd(1), cmd(3)], 0, LATE),

    # A hole in the filler: a non-filler, non-command frame inside the run.
    ("filler_hole",
     [cmd(1), cmd(2)] + filler(2) + [(OTHER, bytes(8))] + filler(2)
     + [cmd(3)], 0, FILLER_HOLE),

    # No following VCM command: no deadline, so no verdict.
    ("no_next_command",
     [cmd(1)] + filler(), 0, INDETERMINATE),

    # Our answer appeared but the capture ended before N+1.
    ("ours_but_no_deadline",
     [cmd(1), cmd(1)] + filler(), 0, INDETERMINATE),

    # Short DLC carries no counter, so the event cannot be judged.
    ("short_dlc",
     [(CMD_ID, bytes([0x08, 0x00, 0x80])), cmd(2)], 0, INDETERMINATE),

    # n_index does not point at a command frame.
    ("not_a_command",
     [(0x030, bytes(8)), cmd(1)], 0, INDETERMINATE),

    # Counter wrap: N = 15, next = 0. Must not be read as a repeat.
    ("counter_wrap_not_ours",
     [cmd(15), cmd(0)] + filler() + [cmd(1)], 0, WITHDRAWN),

    # Counter wrap WITH our answer on time.
    ("counter_wrap_answered",
     [cmd(15), cmd(15), cmd(0)], 0, ANSWERED),
]


def main():
    bad = 0
    print("CLASSIFIER CASES")
    for name, seq, idx, want in CASES:
        try:
            got, detail = classify_event(seq, idx, FILLER)
        except Exception as exc:
            print("  CRASH %-24s %s: %s" % (name, type(exc).__name__, exc))
            bad += 1
            continue
        if got == want:
            print("  ok    %-24s %-14s %s"
                  % (name, got, detail.get("why", "")[:58]))
        else:
            print("  WRONG %-24s expected %s, got %s  %r"
                  % (name, want, got, detail))
            bad += 1

    print("\nORDER CHECK")
    q = [(CMD_ID, 1), (0x030, 0), (CMD_ID, 2)]
    ok, d = order_preserved(q, [(CMD_ID, 1), (0x030, 0), (CMD_ID, 2)])
    print("  %-5s identical sequence            %r" % ("ok" if ok else "WRONG", d))
    bad += 0 if ok else 1

    # Our own and the device's frames appear in the witness and not the queue;
    # they must not count as a divergence.
    ok, d = order_preserved(q, [(CMD_ID, 1), (CMD_ID, 1), (0x030, 0),
                                (0x7F1, 0), (CMD_ID, 2)])
    print("  %-5s extra frames ignored          %r" % ("ok" if ok else "WRONG", d))
    bad += 0 if ok else 1

    ok, d = order_preserved(q, [(0x030, 0), (CMD_ID, 1), (CMD_ID, 2)])
    print("  %-5s reordered is caught           %r"
          % ("ok" if not ok else "WRONG", d))
    bad += 0 if not ok else 1

    ok, d = order_preserved(q, [(CMD_ID, 1), (CMD_ID, 2)])
    print("  %-5s dropped frame is caught       %r"
          % ("ok" if not ok else "WRONG", d))
    bad += 0 if not ok else 1

    print("\nRUN SUMMARY BRANCHES")
    for verdicts, want in (
            ([], "NO_EVENTS"),
            ([ANSWERED] * 5, "STIMULUS_PRODUCED_NO_RACE"),
            ([WITHDRAWN, WITHDRAWN, LATE], "SKIPS_PRODUCED"),
            ([WITHDRAWN, FILLER_HOLE], "MIXED"),
            ([WITHDRAWN, INDETERMINATE], "MIXED")):
        got, counts, note = summarise(verdicts)
        if got == want:
            print("  ok    %-28s %s" % (got, note[:62]))
        else:
            print("  WRONG expected %s, got %s" % (want, got))
            bad += 1

    # The branch that matters most: all-answered must NOT read as a pass.
    got, _c, note = summarise([ANSWERED] * 9)
    if got == "STIMULUS_PRODUCED_NO_RACE" and "NOT evidence" in note:
        print("\n  ok    an all-on-time run refuses to be a pass, and says why")
    else:
        print("\n  WRONG an all-on-time run must refuse to pass: %r" % (got,))
        bad += 1

    print()
    if bad:
        print("=== %d PROBLEM(S) ===" % bad)
    else:
        print("=== PASS -- %d classifier cases, 4 order checks, 5 summary "
              "branches ===" % len(CASES))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
