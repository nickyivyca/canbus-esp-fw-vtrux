"""Exercise wican_trip_bench.judge() without hardware, against real recorded runs.

WHY THIS EXISTS. wican_trip_bench.py's reason check was, until 2026-10-04, "a
reason was given" rather than "the right reason was given" -- so a case that
tripped for the wrong reason passed. The fix is only worth as much as the
evidence that it fires, and a bench run costs a reboot per case, so the check
had to become testable off the bench. judge() was extracted for that.

TWO THINGS IT ASSERTS, and they are different claims:

  1. THE RECORDED RUNS STILL PASS. runs/trip_bench.json (2026-09-20) and
     runs/trip_bench_eea8f75.json (2026-09-22) must produce zero fails. This
     is what establishes that tightening the check costs no result already
     recorded -- the reasons in those files were right all along, they were
     simply never asserted. If this ever fails, either the expectation table is
     wrong or a recorded run was not the pass it reads as.

  2. EACH MUTATION IS CAUGHT. One wrong thing per case, applied to a real
     recorded run rather than to a hand-built fixture, so the mutation is the
     only difference from a known-good input.

IT IMPORTS judge() RATHER THAN REIMPLEMENTING IT. On 2026-10-04 this project's
autoarm_bit_offline_test.py was written against a COPY of the logic it meant to
test; it passed while testing nothing that ships. The import is the point.

A MUTATION THAT CRASHES IS NOT A MUTATION THAT WAS CAUGHT, and the two must not
be scored alike -- the earlier autoarm mutation harness reported a crash as a
GAP, which turned its strongest result into a failure. Each case below is
classified three ways: caught (a fail naming the right thing), MISSED (no fail,
or a fail about something else), or CRASH (judge raised).

    py -3.14 tools/gen_inhibit/bench/trip_reason_offline_test.py
"""

import copy
import json
import os
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RUNS = gi_paths.runs()

from wican_trip_bench import judge, EXPECT_BLOCK, EXPECT_ABORT

RECORDED = ["trip_bench.json", "trip_bench_eea8f75.json"]


def load(fn):
    with open(os.path.join(RUNS, fn), encoding="utf-8") as fh:
        return json.load(fh)["results"]


def find(results, name):
    for r in results:
        if r["name"] == name:
            return r
    raise AssertionError("recorded run has no case %r" % name)


# --- mutations ----------------------------------------------------------
#
# Each takes a deep copy of a recorded run and breaks ONE thing. `want` is a
# fragment that must appear in some fail message: matching the fragment rather
# than just counting fails is what stops a mutation from being scored as caught
# by an unrelated complaint.
#

def m_live_wrong_reason(rs):
    """vcm_fault trips, but reports bus-lost. THE defect this all exists for:
    a replay that falters trips bus_lost, and that used to read as a pass."""
    find(rs, "vcm_fault")["abort_reason"] = EXPECT_ABORT["bus_lost"]
    return rs, "vcm_fault", "wrong reason"


def m_live_no_reason(rs):
    """inverter_lost stops the inhibit and names nothing. The ONLY case the
    old check caught -- included to prove the fix did not lose it."""
    find(rs, "inverter_lost")["abort_reason"] = ""
    return rs, "inverter_lost", "no reason"


def m_live_stale_instead(rs):
    """bus_lost reports a stale 0x617 instead. This is exactly what spec
    12.4's evidence rule forbids: a whole-bus stop reporting trip 5, not
    trip 2."""
    find(rs, "bus_lost")["abort_reason"] = \
        "0x617 went stale while live -- VCM fault flag lost"
    return rs, "bus_lost", "wrong reason"


def m_gate_latched_abort(rs):
    """gen_running went LIVE and then aborted on engine-turning, instead of
    the gate refusing. The old fallback (abort_reason or arm_block) could not
    see this at all: arm_block carries the abort text once a trip latches, so
    it read as a reason and passed."""
    r = find(rs, "gen_running")
    r["abort_reason"] = EXPECT_ABORT["engine_turns"]
    r["arm_block"] = EXPECT_ABORT["engine_turns"]
    return rs, "gen_running", "LATCHED"


def m_gate_wrong_block(rs):
    """vcm_torque is refused, but for the generator-running condition."""
    find(rs, "vcm_torque")["arm_block"] = EXPECT_BLOCK["gen_running"]
    return rs, "vcm_torque", "wrong reason"


def m_unknown_kind(rs):
    """A case whose kind is neither arm_gate nor live_trip must not slip
    through unchecked -- that would be a silent pass by omission."""
    find(rs, "stale_bus")["kind"] = "something_else"
    return rs, "stale_bus", "unknown kind"


def m_renamed_case(rs):
    """A case renamed without updating the expectation table. Must complain
    about the MISSING EXPECTATION rather than quietly checking nothing."""
    find(rs, "engine_turns")["name"] = "engine_turning"
    return rs, "engine_turning", "no expected abort_reason"


def m_latch_broken(rs):
    """bus_lost resumed transmitting once the bus came back. Pre-existing
    check; included so the refactor is shown not to have dropped it."""
    find(rs, "bus_lost")["resumed_tx"] = 17
    return rs, "bus_lost", "must LATCH"


def m_still_live(rs):
    """The condition was applied and the inhibit kept transmitting."""
    find(rs, "vcm_fault")["inhibit_live"] = True
    return rs, "vcm_fault", "still live"


MUTATIONS = [m_live_wrong_reason, m_live_no_reason, m_live_stale_instead,
             m_gate_latched_abort, m_gate_wrong_block, m_unknown_kind,
             m_renamed_case, m_latch_broken, m_still_live]


def main():
    bad = 0

    print("PHASE 1 -- the recorded runs must still pass.")
    base = None
    for fn in RECORDED:
        path = gi_paths.verify(os.path.join(RUNS, fn))
        if not os.path.exists(path):
            print("  NO REFERENCE: %s is missing -- this phase did not run, "
                  "and that is not a pass" % fn)
            bad += 1
            continue
        rs = load(fn)
        rows, fails = judge(rs)
        if fails:
            print("  FAIL %s -- %d case(s) the tightened check rejects:"
                  % (fn, len(fails)))
            for f in fails:
                print("       " + f)
            print("       Either the expectation table is wrong, or this "
                  "recorded run is not the pass it reads as. Resolve before "
                  "trusting either.")
            bad += 1
        else:
            print("  ok   %s -- %d cases, 0 fails" % (fn, len(rs)))
        if base is None:
            base = rs

    if base is None:
        print("\nNo recorded run available, so the mutation phase has no "
              "known-good input. Stopping rather than inventing one.")
        return 1

    print("\nPHASE 2 -- every mutation must be caught, by name.")
    for fn_mut in MUTATIONS:
        rs = copy.deepcopy(base)
        label = fn_mut.__name__
        try:
            mutated, case_name, want = fn_mut(rs)
            _rows, fails = judge(mutated)
        except Exception as exc:
            # Distinct from MISSED on purpose: a crash means the mutation was
            # detected in the harshest way available, not that it slipped by.
            print("  CRASH %-22s judge() raised %s: %s"
                  % (label, type(exc).__name__, exc))
            print("        Counted as a GAP anyway -- a crash gives no usable "
                  "message, so fix it to report instead.")
            bad += 1
            continue
        hit = [f for f in fails if f.startswith(case_name + ":") and want in f]
        if hit:
            print("  ok    %-22s caught: %s" % (label, hit[0][:96]))
        else:
            print("  GAP   %-22s NOT caught. Wanted a fail on %r containing "
                  "%r." % (label, case_name, want))
            if fails:
                print("        judge() did complain, but about something "
                      "else -- which would send a reader to the wrong place:")
                for f in fails:
                    print("          " + f)
            else:
                print("        judge() returned NO fails at all: this "
                      "mutation would pass the bench.")
            bad += 1

    print()
    if bad:
        print("=== %d PROBLEM(S) ===" % bad)
    else:
        print("=== PASS -- the recorded runs survive the tightened check, "
              "and all %d mutations are caught ===" % len(MUTATIONS))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
