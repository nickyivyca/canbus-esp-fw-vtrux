"""Evaluate wican_diag_check.py's rules with no bus, against encoded frames.

WHY. On 2026-10-04 three rows added to that check would have raised TypeError
on their first hardware run -- `int()` on a cantools NamedSignalValue -- ending
the run in a traceback rather than a verdict and wasting a bench slot. I had
called the change "verified offline" on the strength of two things that do not
evaluate an assertion: the five pages decoded, and the file compiled. A decode
proves the INPUTS exist; a compile proves the SYNTAX parses. Between them they
feel like coverage while leaving every expression in the rule table unrun.

So this builds real frames with cantools `encode_message` and runs the REAL
`build_checks` over them. Encoding rather than hand-packing bytes matters: the
bug was in how a DECODED signal is handled, so the test has to go through the
same decode path the device's frames take.

FOUR SCENARIOS, chosen so every row is exercised in both directions somewhere,
including the cross-page invariant's agree and disagree cases:

  clean        everything as a good run looks -> every row passes
  aborted      abort_reason set AND bit7 set  -> the two "none" rows fail, but
                                                 the INVARIANT HOLDS (this is
                                                 the case that would have gone
                                                 undetected if the invariant
                                                 were written as a bare "both
                                                 are zero" check)
  disagree_a   abort_reason set, bit7 CLEAR   -> invariant fails
  disagree_b   abort_reason none, bit7 SET    -> invariant fails

    py -3.14 tools/gen_inhibit/bench/diag_check_rules_test.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from diag_check_rules import build_checks, raw                # noqa: E402
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

SCHEMAS = gi_diag.SchemaSet.from_git()
db = SCHEMAS.head().db
DBC_SCHEMA = SCHEMAS.head().version

# A good run: bus_on (0x02), bus_ours (0x04), inhibit_live (0x20), key_on
# (0x40) -- 0x66, which is what diag_flags_trace.py measured in six of seven
# frames on 2026-09-22. abort_latched (0x80) clear.
FLAGS_GOOD = 0x66
FLAGS_ABORTED = FLAGS_GOOD | 0x80


def frames(flags=FLAGS_GOOD, abort=0, arm_block=0):
    """Encode with HEAD's DBC, then decode through gi_diag in page order, so the rules
    see what the bench would hand them."""
    st = {"diag_schema_ver": DBC_SCHEMA, "diag_fw_ver": 3, "diag_mode": 3,
          "diag_flags": flags, "diag_disable_reason": 0,
          "diag_shift_pos": 0, "diag_soc_x100": 77.0}
    bd = {"diag_git_hash": 0x5A4AEA1, "diag_fw_ver16": 3,
          "diag_schema_ver_build": DBC_SCHEMA}
    s2 = {"diag_abort_reason": abort, "diag_flags2": 0,
          "diag_arm_block": arm_block, "diag_would_tx": 0,
          "diag_tx_queued_behind": 0}
    sk = {"diag_skips_withdrawn": 0, "diag_skips_late": 0}
    out = []
    dec = gi_diag.Decoder(SCHEMAS)
    for fid, payload in ((0x7F1, st), (0x7F3, bd), (0x7F8, s2), (0x7F9, sk)):
        data = db.encode_message(fid, payload)
        out.append(dec.feed(fid, data))
    return out


SCENARIOS = [
    ("clean", dict(), set()),
    # abort 3 is bus_lost. Both "none" rows must fail; the invariant must HOLD.
    ("aborted", dict(flags=FLAGS_ABORTED, abort=3, arm_block=14),
     {"abort_latched clear (bit7)", "0x7F8 abort_reason none",
      "0x7F8 arm_block none"}),
    ("disagree_a_reason_without_bit7", dict(flags=FLAGS_GOOD, abort=3),
     {"0x7F8 abort_reason none", "0x7F8 abort_reason agrees with bit7"}),
    ("disagree_b_bit7_without_reason", dict(flags=FLAGS_ABORTED, abort=0),
     {"abort_latched clear (bit7)", "0x7F8 abort_reason agrees with bit7"}),
]


def main():
    bad = 0

    print("raw() on both shapes cantools returns:")
    st, _bd, s2, _sk = frames()
    print("  choice signal   diag_abort_reason %-22r -> raw() %d"
          % (s2["diag_abort_reason"], raw(s2["diag_abort_reason"])))
    print("  plain signal    diag_flags        %-22r -> raw() %d"
          % (st["diag_flags"], raw(st["diag_flags"])))
    print("  (int() on the first raises TypeError -- that was the bug.)\n")

    for name, kwargs, want_fail in SCENARIOS:
        st, bd, s2, sk = frames(**kwargs)
        try:
            checks = build_checks(st, bd, s2, sk, DBC_SCHEMA)
        except Exception as exc:
            print("  CRASH %-32s build_checks raised %s: %s"
                  % (name, type(exc).__name__, exc))
            print("        This is the original defect's shape: the run ends "
                  "in a traceback instead of a verdict.")
            bad += 1
            continue

        nonbool = {k: type(v).__name__ for k, v in checks.items()
                   if not isinstance(v, bool)}
        if nonbool:
            print("  FAIL  %-32s non-bool rows %s -- a row must not be able "
                  "to pass by being a truthy object" % (name, nonbool))
            bad += 1

        got_fail = {k for k, v in checks.items() if not v}
        if got_fail == want_fail:
            print("  ok    %-32s %d rows, %d failing as expected%s"
                  % (name, len(checks), len(got_fail),
                     (": " + ", ".join(sorted(got_fail))) if got_fail else ""))
        else:
            print("  WRONG %-32s expected these to fail: %s"
                  % (name, sorted(want_fail) or "none"))
            print("        got: %s" % (sorted(got_fail) or "none"))
            bad += 1

    # The invariant must be the thing that distinguishes aborted from
    # disagree_*, and that is worth asserting rather than reading off the
    # output above: if it were written as "abort_reason is zero AND bit7 is
    # clear" it would also fail on `aborted`, where nothing is wrong, and
    # nobody would notice because two other rows fail there too.
    print()
    _st, _bd, s2a, _sk = frames(flags=FLAGS_ABORTED, abort=3, arm_block=14)
    sta, bda, _s2, ska = frames(flags=FLAGS_ABORTED, abort=3, arm_block=14)
    inv = build_checks(sta, bda, s2a, ska,
                       DBC_SCHEMA)["0x7F8 abort_reason agrees with bit7"]
    if inv:
        print("  ok    the invariant HOLDS on a legitimately aborted device, "
              "so it is testing agreement and not merely 'both are zero'")
    else:
        print("  WRONG the invariant fails on a legitimately aborted device. "
              "It has been written as 'both are zero', which would call a "
              "correct abort a packing bug.")
        bad += 1

    print()
    if bad:
        print("=== %d PROBLEM(S) ===" % bad)
    else:
        print("=== PASS -- every rule evaluated, in both directions, with no "
              "bus ===")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
