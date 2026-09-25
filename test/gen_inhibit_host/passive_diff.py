#!/usr/bin/env python3
"""Replay every scenario in INHIBIT and in PASSIVE and prove they decided alike.

Spec 3.1 (review B1) requires PASSIVE to run "exactly the INHIBIT decision
path ... producing the same state, the same latches and the same events. The
only permitted difference is at the moment of transmission."

WHY THIS IS NOT ANOTHER GOLDEN. The host suite is a REGRESSION harness: it
pins each trace against a recorded copy of itself, so a rule that is wrong in
the code is wrong in the golden too and stays green for ever. Three separate
defects this month were found by someone reading a diff, not by the suite --
a scenario whose header and golden disagreed, four scenarios that had silently
stopped going live, and a directive that sorted itself to the front of the
file and delayed arming.

This check has no golden. It compares two runs of the SAME build against each
other and asserts a property the spec states, so it cannot be blessed into
agreement. That is review D9/E2's shape, and this is the first instance of it.

WHAT IS COMPARED, and what is deliberately not:

  compared      every STATE line's decision fields, every EV line, and the
                FINAL decision fields. These must be byte-identical.
  transmission  INHIBIT's `TX INHIBIT` lines must correspond one-for-one with
                PASSIVE's would-transmits -- the counts must match exactly, and
                PASSIVE must emit NO 0x051 at all.
  not compared  diag payloads and cadence: diag_mode differs by construction
                (3 against 4) and the mode is IN the payload, which is the
                point of it. And the FINAL fields that COUNT transmission --
                tx_ok, would_tx, tx_fail, resp_n -- since those are precisely
                "the moment of transmission" that spec 3.1 exempts, and they
                are checked separately and exactly below.

THE ONE PLACE THE PATHS MAY GENUINELY PART. Spec 3.1: "The trips that are about
our own transmission have no PASSIVE counterpart and are simply never reached:
a failed transmit cannot happen when nothing is sent." So a scenario that
drives spec 7 trip 7 -- tx-fail-aborts, tx-late-loses-counter-race,
tx-controller-reports-failed -- ends the INHIBIT run in a latched abort that
PASSIVE cannot have, and runs on in PASSIVE. Those are compared only up to the
instant INHIBIT's trip 7 fires, which is everything the two modes are required
to share.

That exemption is detected from the trace (INHIBIT emitted an `EV TX_FAIL`),
not from a list of scenario names, so a new trip-7 scenario is covered without
anyone remembering to add it -- and a scenario that starts failing transmits
when it should not still shows up, because the divergence then appears before
the TX_FAIL rather than after it.

Usage, from test/gen_inhibit_host:
    python3 passive_diff.py                 # every scenario
    python3 passive_diff.py --only keyon
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCN = os.path.join(HERE, "scenarios")
RUNNER = os.path.join(HERE, "host_runner")

# Every scenario opens with its mode directive. Rewriting 3 -> 4 there is what
# makes the two runs differ, and nothing else about the input changes.
MODE_RE = re.compile(r"^mode (\d+) 3 (\d+)$")


def to_passive(text):
    out, changed = [], 0
    for ln in text.splitlines():
        m = MODE_RE.match(ln)
        if m:
            out.append("mode %s 4 %s" % (m.group(1), m.group(2)))
            changed += 1
        else:
            out.append(ln)
    return "\n".join(out) + "\n", changed


def run(text):
    r = subprocess.run([RUNNER], input=text, capture_output=True, text=True,
                       cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError("runner exited %d: %s" % (r.returncode, r.stderr))
    return r.stdout


def decisions(trace, mode_from, mode_to):
    """The decision sequence, with the mode number normalised away.

    `mode=N` appears in STATE and FINAL and differs by construction, so it is
    rewritten rather than dropped -- dropping it would also hide a mode change
    the scenario asked for, which is a decision.
    """
    keep = []
    for ln in trace.splitlines():
        if " TX " in ln or ln.startswith("#"):
            continue
        ln = ln.replace("mode=%d" % mode_from, "mode=%d" % mode_to)
        # EV MODE carries the mode number as its `a` field, and that is the
        # one thing the two runs are SUPPOSED to differ in. Rewrite only the
        # value under test, so a genuine disarm (a=0) or a switch to OBSERVE
        # still shows up as a difference if one run has it and the other does
        # not.
        ln = ln.replace("EV MODE a=%d " % mode_from, "EV MODE a=ARMED ")
        if " FINAL " in ln:
            ln = strip_tx_counts(ln)
        keep.append(ln)
    return keep


def before(lines, t_limit):
    """Decision lines strictly before `t_limit`. FINAL is always dropped."""
    if t_limit is None:
        return lines
    out = []
    for ln in lines:
        if " FINAL " in ln:
            continue
        try:
            t = int(ln.split()[0])
        except (ValueError, IndexError):
            continue
        if t < t_limit:
            out.append(ln)
    return out


# The FINAL fields that count transmission rather than describe a decision.
FINAL_TX_FIELDS = ("tx_ok", "would_tx", "tx_fail", "resp_n")


def strip_tx_counts(line):
    out = line
    for f in FINAL_TX_FIELDS:
        out = re.sub(re.escape(f) + r"=\d+", f + "=-", out)
    return out


def first_tx_fail(trace):
    """Timestamp of the first EV TX_FAIL, or None. See the header."""
    for ln in trace.splitlines():
        if " EV TX_FAIL " in ln:
            return int(ln.split()[0])
    return None


def tx_inhibit_times(trace):
    return [ln.split()[0] for ln in trace.splitlines() if " TX INHIBIT " in ln]


def final_field(trace, name):
    for ln in trace.splitlines():
        if " FINAL " in ln:
            m = re.search(re.escape(name) + r"=(\d+)", ln)
            if m:
                return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    names = sorted(n[:-4] for n in os.listdir(SCN) if n.endswith(".scn"))
    if args.only:
        names = [n for n in names if args.only in n]

    ok = bad = skipped = 0
    for name in names:
        with open(os.path.join(SCN, name + ".scn")) as fh:
            text = fh.read()
        passive_text, changed = to_passive(text)
        if changed == 0:
            # OBSERVE/RESPOND scenarios never enter INHIBIT, so there is no
            # INHIBIT decision path for PASSIVE to be compared against.
            skipped += 1
            if args.verbose:
                print("[skip  ] %-32s never arms INHIBIT" % name)
            continue

        a = run(text)
        b = run(passive_text)

        problems = []

        da, db = decisions(a, 3, 0), decisions(b, 4, 0)

        # Spec 3.1's one permitted parting of the ways: trip 7 is unreachable
        # in PASSIVE, so a scenario that drives it is compared only up to the
        # instant INHIBIT's trip fires.
        t_fail = first_tx_fail(a)
        if t_fail is not None:
            da, db = before(da, t_fail), before(db, t_fail)

        if da != db:
            first = next((i for i, (x, y) in enumerate(zip(da, db)) if x != y),
                         min(len(da), len(db)))
            problems.append("decision paths diverge at line %d:\n"
                            "    INHIBIT: %s\n    PASSIVE: %s"
                            % (first,
                               da[first] if first < len(da) else "<end>",
                               db[first] if first < len(db) else "<end>"))

        # Spec 3.1 rule 2, the structural half: nothing on the real ID.
        leaked = tx_inhibit_times(b)
        if leaked:
            problems.append("PASSIVE emitted %d 0x051 frames (first at %s)"
                            % (len(leaked), leaked[0]))

        # ...and the accounting half: one would-transmit per real transmit.
        n_tx = len(tx_inhibit_times(a))
        n_would = final_field(b, "would_tx")
        if n_would is None:
            problems.append("PASSIVE trace has no would_tx in FINAL")
        elif t_fail is not None:
            # INHIBIT stopped transmitting at its trip 7 and PASSIVE ran on, so
            # the totals cannot match. Compare what they shared instead: every
            # transmit INHIBIT made before the trip must have a would-transmit.
            n_before = len([t for t in tx_inhibit_times(a) if int(t) < t_fail])
            if n_would < n_before:
                problems.append("would_tx=%d but INHIBIT transmitted %d before "
                                "its trip 7 at %d" % (n_would, n_before, t_fail))
        elif n_would != n_tx:
            problems.append("would_tx=%d but INHIBIT transmitted %d"
                            % (n_would, n_tx))

        if final_field(b, "tx_ok"):
            problems.append("PASSIVE reported tx_ok=%d" % final_field(b, "tx_ok"))
        for tr, who in ((a, "INHIBIT"), (b, "PASSIVE")):
            if final_field(tr, "emit_refused"):
                problems.append("%s tripped emit_refused" % who)

        if problems:
            bad += 1
            print("[DIFFER] %-32s" % name)
            for p in problems:
                print("    " + p)
        else:
            ok += 1
            if args.verbose:
                note = "" if t_fail is None else "  (compared to trip 7 at %d)" % t_fail
                print("[ok    ] %-32s %d transmits, %d would-transmits%s"
                      % (name, n_tx, n_would, note))

    print()
    print("%d identical, %d differing, %d skipped (never arm INHIBIT)"
          % (ok, bad, skipped))
    if bad:
        print("Spec 3.1: PASSIVE must run exactly the INHIBIT decision path,")
        print("differing only at the moment of transmission.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
