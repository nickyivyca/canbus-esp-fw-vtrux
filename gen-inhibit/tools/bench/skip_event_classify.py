"""Classify a skip event from FRAME ORDER alone. No timing, deliberately.

WHY ORDER AND NOT TIMING. The agreed skip-bench method (spec 5.2 item 10 cases
7-9, trip 7 reason 15) was first written around a 6 us gap between the VCM's
0x051 N and N+1. `pcan_backtoback_capability.py` then showed the Kvaser's driver
timestamps CANNOT resolve inter-frame gaps at all -- 318 of 468 measured gaps
were negative, which is physically impossible, and its smallest non-zero delta
(170.0 us) sits below the 190.0 us a frame physically occupies. I next proposed
reading the device's own `rx_gap` histogram instead, and that was wrong twice
over (reviewing session): its stamp is taken at TASK level after twai_receive,
so it measures worker processing rather than the wire, and two back-to-back
frames are ~190 us apart anyway and so could never land in the sub-50 us bucket
the proposal pointed at.

The way out, from the reviewing session, is that **the question was never about
time**. "Did our answer to N reach the wire between N and N+1?" is ORDINAL. A
single receiver's log gives frame order reliably even when its timestamps are
unusable, which is exactly this witness's situation.

HOW OUR 0x051 IS TOLD FROM THE VCM'S, and it is not by payload. The device holds
B0 at 0x08 and commands zero torque, but so does the VCM wherever the engine is
off -- which is most of an armable window. Booking "zero torque" to us
over-counted 670 frames as 1251 on 2026-09-26. The reliable discriminator is the
COUNTER: the device STEALS the counter of the 0x051 it answers (spec 4), so a
second 0x051 carrying a counter already seen is ours. The replay's own counters
increment mod 16, so a repeat inside a short window cannot be the replay's.

THE OUTCOME THAT IS NOT A RESULT. If the stimulus failed to produce the race --
and whether the PCAN can emit back-to-back frames is UNRESOLVED, because no
instrument here can measure it -- then every event classifies as
`answered_normally`. That is NOT evidence the firmware does not skip. It is
evidence the bench did not ask it to. `summarise()` reports that case as
STIMULUS_PRODUCED_NO_RACE and refuses to call it a pass, because the two are
indistinguishable in the counts alone and the whole method dies if they are
conflated.

    (imported by the skip bench harness; see skip_event_classify_test.py)
"""

CMD_ID = 0x051

# Verdicts, in the order they are reported.
WITHDRAWN = "withdrawn"            # our answer to N never appeared at all
LATE = "late"                      # our answer to N appeared AFTER N+1
ANSWERED = "answered_normally"     # our answer to N landed before N+1
FILLER_HOLE = "filler_hole"        # our frame appeared inside the filler run
INDETERMINATE = "indeterminate"    # the sequence cannot support a verdict


def counter_of(data):
    """The VCM rolling counter, 0x051 B5 (spec 4: B5 = counter 0x00-0x0F)."""
    return data[5] & 0x0F if len(data) >= 6 else None


def classify_event(seq, n_index, filler_ids):
    """Classify one event.

    `seq` is the witness's frames IN ORDER, each a (arb_id, data) pair. No
    timestamps are taken and none are needed. `n_index` indexes the VCM's
    0x051 "N" within `seq`. `filler_ids` is the set of ids used as filler.

    Returns (verdict, detail). Every branch that cannot be decided returns
    INDETERMINATE with a reason, rather than guessing -- a wrong label on a
    skip event is the failure this whole harness exists to avoid, and an
    honest "cannot tell" is worth more than a confident coin-flip.
    """
    if not (0 <= n_index < len(seq)):
        return INDETERMINATE, {"why": "n_index out of range"}
    n_id, n_data = seq[n_index]
    if n_id != CMD_ID:
        return INDETERMINATE, {"why": "frame at n_index is 0x%03X, not the "
                                      "command id" % n_id}
    n_ctr = counter_of(n_data)
    if n_ctr is None:
        return INDETERMINATE, {"why": "N has DLC < 6, so it carries no counter"}

    # Find the NEXT command frame after N that is not ours. "Not ours" means a
    # counter different from N's: the device can only steal N's.
    next_vcm = None
    ours_before_next = None
    for i in range(n_index + 1, len(seq)):
        fid, data = seq[i]
        if fid != CMD_ID:
            continue
        ctr = counter_of(data)
        if ctr == n_ctr:
            # A repeat of N's counter: ours.
            if next_vcm is None:
                ours_before_next = i
            else:
                return LATE, {"n_index": n_index, "n_counter": n_ctr,
                              "next_vcm_index": next_vcm, "ours_index": i,
                              "why": "our answer carries N's counter and "
                                     "appears AFTER the VCM's next command, "
                                     "so it lost the counter race"}
        else:
            if next_vcm is None:
                next_vcm = i
                if ours_before_next is not None:
                    # Our answer landed between N and N+1: the normal case.
                    return ANSWERED, {"n_index": n_index, "n_counter": n_ctr,
                                      "ours_index": ours_before_next,
                                      "next_vcm_index": next_vcm}
            # A third distinct-counter command; keep scanning for a late ours.

    if ours_before_next is not None and next_vcm is None:
        return INDETERMINATE, {
            "n_index": n_index, "n_counter": n_ctr,
            "ours_index": ours_before_next,
            "why": "our answer appeared but the VCM's next command never did, "
                   "so there is no deadline to judge it against. The event "
                   "ran off the end of the capture."}
    if next_vcm is None:
        return INDETERMINATE, {
            "n_index": n_index, "n_counter": n_ctr,
            "why": "no following VCM command in the sequence, so neither a "
                   "skip nor an on-time answer can be established"}

    # No frame carrying N's counter anywhere after N. Our answer never reached
    # the wire: a WITHDRAWN skip. Check the filler ran continuously, because a
    # hole in it is the alternative explanation for us not winning arbitration.
    hole = _filler_hole(seq, n_index, next_vcm, filler_ids)
    if hole is not None:
        return FILLER_HOLE, {"n_index": n_index, "n_counter": n_ctr,
                             "next_vcm_index": next_vcm, "hole_at": hole,
                             "why": "a non-filler, non-command frame appeared "
                                    "inside the filler run, so the filler did "
                                    "not occupy the wire continuously and the "
                                    "event does not test what it should"}
    return WITHDRAWN, {"n_index": n_index, "n_counter": n_ctr,
                       "next_vcm_index": next_vcm,
                       "why": "no frame carrying N's counter ever appeared, so "
                              "our answer to N was withdrawn rather than sent "
                              "late"}


def _filler_hole(seq, n_index, next_vcm, filler_ids):
    """Index of the first frame between N+1 and the next command that is
    neither filler nor a command, or None. That is the ordering form of
    "the filler had a gap" -- see the module docstring."""
    for i in range(next_vcm + 1, len(seq)):
        fid, _ = seq[i]
        if fid == CMD_ID:
            return None
        if fid not in filler_ids:
            return i
    return None


def order_preserved(queued, witnessed):
    """Did the wire carry what we queued, in the order we queued it?

    The reviewing session's check, and it is what licenses reading order off a
    single receiver at all: compare the PCAN's queued sequence against the
    witness's, counting only the ids we sent. Our own transmissions and the
    device's appear in the witness and not in the queue, so they are excluded
    by id-and-counter rather than by position.

    Returns (ok, detail).

    IMPLEMENTED AS A GREEDY SUBSEQUENCE MATCH, and the first version was not --
    it filtered the witness to frames whose (id, counter) appeared in the queue
    and then compared position by position. That is broken by the very
    mechanism the classifier depends on: the device STEALS the counter, so our
    answer and the VCM's command share (id, counter) exactly. A witness
    containing both copies therefore had MORE "queued" frames than were queued,
    and the check failed on a correct run. Caught by this module's own test.

    So the property tested is: every frame we queued appears in the witness, in
    the order we queued it, with anything else (our transmissions, the device's,
    diag pages) interleaved freely. A reordering is still caught, because the
    walk requires each queued frame to appear AFTER the previous one.
    """
    q = list(queued)
    k = 0
    for fid, ctr in witnessed:
        if k < len(q) and (fid, ctr) == q[k]:
            k += 1
    if k != len(q):
        return False, {"why": "matched only %d of the %d frames we queued, in "
                              "order; first unmatched is 0x%03X/ctr%s"
                              % (k, len(q), q[k][0], q[k][1]),
                       "queued": len(q), "matched": k,
                       "first_unmatched_index": k}
    return True, {"frames": len(q)}


def summarise(verdicts):
    """Turn per-event verdicts into a run verdict.

    THE IMPORTANT BRANCH IS THE ONE THAT REFUSES TO PASS. If no event produced
    a skip, the firmware has not been shown to skip correctly -- it has not been
    asked to. Reporting that as a pass would make a stimulus failure
    indistinguishable from a working device, which is the single way this
    harness could mislead.
    """
    n = {}
    for v in verdicts:
        n[v] = n.get(v, 0) + 1
    skips = n.get(WITHDRAWN, 0) + n.get(LATE, 0)
    bad = n.get(FILLER_HOLE, 0) + n.get(INDETERMINATE, 0)

    if not verdicts:
        return "NO_EVENTS", n, ("No events were classified at all. Nothing is "
                                "established.")
    if skips == 0:
        return ("STIMULUS_PRODUCED_NO_RACE", n,
                "Every event was answered on time, so THE BENCH NEVER CREATED "
                "THE CONDITION. This is NOT evidence the firmware does not "
                "skip -- whether the PCAN can emit the required back-to-back "
                "pair is unresolved, because no instrument here can measure "
                "it. Fix the stimulus; do not record a pass.")
    if bad:
        return ("MIXED", n,
                "%d event(s) produced a skip, but %d could not be classified "
                "cleanly (filler holes or indeterminate). Read those before "
                "using the skip counts." % (skips, bad))
    return ("SKIPS_PRODUCED", n,
            "%d event(s) produced a skip, every event classified cleanly. The "
            "device's own withdrawn/late counters, 0x7F9 and the balance "
            "identity must now be checked against these." % skips)
