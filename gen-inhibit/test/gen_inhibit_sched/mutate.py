#!/usr/bin/env python3
"""Mutate the scheduler's guards and check the cases NOTICE. Run on the host.

    python3 mutate.py

A case nobody has seen fail is not a case. Each mutation below removes exactly one
guard that a defect found in review put there, rebuilds, and expects a FAILURE. A
mutation that SURVIVES is reported loudly: it means the suite does not actually
cover the thing the guard exists for.

The mutations are applied to a COPY of the sources, never to the tree, so an
interrupted run cannot leave a mutant behind. That is deliberate: the first thing
a mutation harness must not do is silently modify the code under test.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.normpath(os.path.join(HERE, "..", "..", "firmware", "main"))
# AGENTS.md "Files and folders" and "Removing scratch": the run's folder is
# gi_paths.scratch()'s, in this worktree's scratch/, removed when the run ends,
# pass or fail; each mutant gets its own folder in it, removed after the mutant.
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "tools", "bench")))
import gi_paths  # noqa: E402

# (name, file, old, new, the case that must fail)
MUTATIONS = [
    # THE ANCHOR STOPS AT THE LOOP HEAD, deliberately. Spelling the whole body
    # meant spelling the end-of-line comment on the note_skip() call too, and a
    # reworded comment then disarms the mutation with no sign that it did -- which
    # is how this one came to be skipped for two commits. Killing the loop by its
    # condition breaks the same property with nothing prose-shaped in the anchor.
    ("D1: drop the deadline purge of queued inhibits",
     "gi_sched.c",
     """    uint32_t purged = 0;
    while (s->q[GS_CLASS_INHIBIT].n > 0)""",
     """    uint32_t purged = 0;
    while (0)""",
     "D1: a queued inhibit is purged at the deadline"),

    ("D2: make the skip window fixed instead of sliding",
     "gi_sched.c",
     """    if (s->skip_ts_n < GS_SKIP_TRIP_N)
    {
        return false;
    }""",
     """    if (s->skip_ts_n < GS_SKIP_TRIP_N)
    {
        return false;
    }
    return false;   /* MUTANT: never trips */""",
     "D2: 3 skips in 0.15 s across the old window reset"),

    ("preemption: abort even while TRANSMITTING",
     "gi_sched.c",
     """        && s->q[GS_CLASS_INHIBIT].n > 0
        && s->hal->buf_state(s->hal->ctx) == GS_BUF_AWAITING)""",
     """        && s->q[GS_CLASS_INHIBIT].n > 0)""",
     "item 4: no abort command while TRANSMITTING"),

    # THE GUARD IS IN TWO PLACES, so each anchor carries the line above it.
    # ae7b3cb added the same `!s->aborting` guard to gs_command_received, and
    # from then on the bare `if` matched twice and this mutation was skipped:
    # neither site was mutated. gs_tick's sits under its comment block, the
    # command path's under the GI_INSTRUMENT_ABORTWIN `#endif`; neither anchor
    # holds prose.
    ("drop the !aborting guard on gs_tick's completion path",
     "gi_sched.c",
     """     */
    if (s->held && !s->aborting && !credit_held(s)
        && !s->hal->outstanding(s->hal->ctx))""",
     """     */
    if (s->held && !credit_held(s)
        && !s->hal->outstanding(s->hal->ctx))""",
     "item 5: an abort is not credited as a send"),

    ("drop the !aborting guard on gs_command_received's completion path",
     "gi_sched.c",
     """    else if (s->held && !s->aborting && !late_hold && !waited""",
     """    else if (s->held && !late_hold && !waited""",
     "abort: a departure seen by gs_command_received is not credited as sent"),

    # ---- spec 7 trip 7's held credit (user, 2026-11-23; B4 l3s) ------------
    # A completion seen only after a command that was already waiting was
    # dequeued is a late skip. Each site that honours the hold is broken alone.
    ("trip 7: gs_tick credits a held inhibit",
     "gi_sched.c",
     """    if (s->held && !s->aborting && !credit_held(s)""",
     """    if (s->held && !s->aborting""",
     "trip 7: completion seen after a waiting command is late"),

    ("trip 7: the deadline credits a held inhibit",
     "gi_sched.c",
     """    else if (s->held && !s->aborting && !late_hold && !waited""",
     """    else if (s->held && !s->aborting && (late_hold || !late_hold) && !waited""",
     "trip 7: completion seen after a waiting command is late"),

    ("trip 7: the deadline does not end the hold",
     "gi_sched.c",
     """    }
    end_verdict_wait(s);

    /*
     * THE DEADLINE HAS NOW PASSED""",
     """    }

    /*
     * THE DEADLINE HAS NOW PASSED""",
     "trip 7: completion seen after a waiting command is late"),

    # ---- spec 7 trip 7, the decision with frames waiting (user, 2026-11-23) --
    # The count is taken after the completion read, and the verdict waits for
    # exactly the counted frames. The first two are the reviewer's: the count
    # order and the dequeue limit.
    # The count read moved before the completion read, at both sites that see
    # a departure: the value taken first is the one see_departure() uses.
    ("trip 7: the count is read before the completion read",
     "gi_sched.c",
     [("""static void see_departure(gs_t *s, int64_t now, bool at_deadline)
{""",
       """static uint32_t g_mut_before;
static void see_departure(gs_t *s, int64_t now, bool at_deadline)
{"""),
      ("""        waiting = s->hal->rx_waiting(s->hal->ctx);""",
       """        waiting = g_mut_before;"""),
      ("""     */
    if (s->held && !s->aborting && !credit_held(s)
        && !s->hal->outstanding(s->hal->ctx))""",
       """     */
    g_mut_before = s->held ? s->hal->rx_waiting(s->hal->ctx) : 0;
    if (s->held && !s->aborting && !credit_held(s)
        && !s->hal->outstanding(s->hal->ctx))"""),
      ("""    const bool late_hold = credit_held(s) && s->credit_hold;""",
       """    g_mut_before = s->held ? s->hal->rx_waiting(s->hal->ctx) : 0;
    const bool late_hold = credit_held(s) && s->credit_hold;""")],
     None,
     "trip 7: a frame arriving between the completion read and the count"),

    ("trip 7: the verdict waits for one frame more than counted",
     "gi_sched.c",
     """            s->verdict_left = waiting;""",
     """            s->verdict_left = waiting + 1;""",
     "trip 7: a command behind the counted frames does not make it late"),

    ("trip 7: the verdict never waits for counted frames",
     "gi_sched.c",
     """        if (waiting > 0 && !at_deadline)""",
     """        if (0 && at_deadline)""",
     "trip 7: the command is among the counted frames: late skip"),

    ("trip 7: a counted command does not make it late",
     "gi_sched.c",
     """            s->verdict_left = 0;
            s->credit_hold = true;""",
     """            s->verdict_left = 0;""",
     "trip 7: the command is among the counted frames: late skip"),

    ("trip 7: the deadline does not credit a verdict that waits",
     "gi_sched.c",
     """        credit_after_wait(s);
    }
    else if""",
     """        (void)0;
    }
    else if""",
     "trip 7: frames behind the dequeued command: on time, waiting"),

    ("trip 7: a stand-down does not end the wait",
     "gi_sched.c",
     """        end_verdict_wait(s);
        if (!s->held_booked)""",
     """        if (!s->held_booked)""",
     "trip 7: a stand-down while the verdict waits"),

    ("trip 7: the hold runs to the last dequeue, not to the completion",
     "gi_sched.c",
     """    note_left_controller(s, s->seen_at, backlog);""",
     """    note_left_controller(s, s->seen_at + 5000, backlog);""",
     "trip 7: frames waiting, no command among them: on time, waiting"),

    # ---- spec 11: the probe takes the inhibit's path (user, 2026-11-23) -----
    # Each site that keys the wait on the arm's timed class is put back on the
    # inhibit class alone.
    ("spec 11: the held verdict keys on the inhibit, not the timed class",
     "gi_sched.c",
     """    return s->held && s->held_class == s->timed_class
        && (s->credit_hold || s->verdict_left > 0);""",
     """    return s->held && s->held_class == GS_CLASS_INHIBIT
        && (s->credit_hold || s->verdict_left > 0);""",
     "spec 11: probe, the command among the counted frames"),

    ("spec 11: the count at the completion is the inhibit's only",
     "gi_sched.c",
     """    if (s->held_class == s->timed_class && s->held_deadline_n == s->deadline_n)
    {
        waiting""",
     """    if (s->held_class == GS_CLASS_INHIBIT && s->held_deadline_n == s->deadline_n)
    {
        waiting""",
     "spec 11: probe, frames waiting, no command among them"),

    ("spec 11: a dequeue changes the inhibit's verdict only",
     "gi_sched.c",
     """    if (!s->held || s->held_class != s->timed_class || s->aborting)""",
     """    if (!s->held || s->held_class != GS_CLASS_INHIBIT || s->aborting)""",
     "spec 11: probe, frames waiting, no command among them"),

    ("D3: an inhibit queue-full is not counted as a skip",
     "gi_sched.c",
     """        if (cls == GS_CLASS_INHIBIT)
        {
            note_skip(s, now, false);   /* never went out */
        }""",
     """        if (cls == GS_CLASS_INHIBIT)
        {
            (void)now;
        }""",
     "D3: an inhibit that cannot be queued is a skip"),

    # ---- review round 12's survivors, each now with a case ------------------
    ("R8: a withdrawn HELD inhibit gets requeued",
     "gi_sched.c",
     """            if (c != GS_CLASS_INHIBIT)
            {
                gs_slot_t sl;""",
     """            if (1)
            {
                gs_slot_t sl;""",
     "R8: a withdrawn HELD inhibit never comes back"),

    # THE ANCHOR HAS TO NAME ITS BRANCH. `late_on_wire++` is raised twice now
    # -- once for TRANSMITTING and once for BUF_EMPTY, which is also a frame that
    # went out late -- so the bare two lines match in two places and the mutation
    # is skipped as ambiguous rather than applied. Carrying the `if` in makes it
    # unique and says which branch is being broken.
    ("R5: TRANSMITTING at the deadline reported as a skip",
     "gi_sched.c",
     """    if (b == GS_BUF_TRANSMITTING)
    {
        /*
         * It may reach the wire late, and the device cannot tell that from the
         * abort succeeding: both end as TX_SUCCESS with msgs_to_tx 0.
         */
        s->st.late_on_wire++;
        verdict = GS_DEADLINE_MAYBE_LATE;""",
     """    if (b == GS_BUF_TRANSMITTING)
    {
        verdict = GS_DEADLINE_SKIPPED;""",
     "R5: transmitting at the deadline is MAYBE_LATE"),

    ("R4: requeue an aborted page at the TAIL",
     "gi_sched.c",
     """    q->head = (uint8_t)((q->head + q->cap - 1) % q->cap);
    q->q[q->head] = *sl;
    q->n++;
    return true;""",
     """    q->q[(q->head + q->n) % q->cap] = *sl;
    q->n++;
    return true;""",
     "R4: an aborted page requeues at the HEAD"),

    ("R1: the abort bound 1 ms -> 100 ms",
     "gi_sched.c",
     """#define GS_ABORT_BOUND_US   1000""",
     """#define GS_ABORT_BOUND_US   100000""",
     "R1: the 1 ms abort bound is reached and counted"),

    ("R9: a probe is never preempted",
     "gi_sched.c",
     """    if (!s->aborting && s->held && s->held_class != GS_CLASS_INHIBIT""",
     """    if (!s->aborting && s->held && s->held_class == GS_CLASS_TELEMETRY""",
     "R9: a probe in the buffer is preempted"),

    ("R11: the 1 s window becomes inclusive",
     "gi_sched.c",
     """        if (newest - s->skip_ts[i] < GS_SKIP_WINDOW_US) { k++; }""",
     """        if (newest - s->skip_ts[i] <= GS_SKIP_WINDOW_US) { k++; }""",
     "R11: exactly 1.000 s apart does NOT trip"),

    ("the ordering check reverts to the weak inhibit_outstanding test",
     "gi_sched.c",
     """        if (!s->deadline_token)
        {
            s->st.order_violations++;
        }
        s->deadline_token = false;""",
     """        if (s->inhibit_outstanding)
        {
            s->st.order_violations++;
        }
        s->deadline_token = false;""",
     "order: SWAPPED, previous complete (the missed case)"),

    ("the deadline stops setting its token",
     "gi_sched.c",
     """    s->deadline_token = true;""",
     """    s->deadline_token = s->deadline_token;""",
     "order: correct, previous complete"),

    # F5 (spec 5.2 item 5, user 2026-11-26): a withdrawn inhibit held past
    # the 1 ms bound is aborted again on every later pass.
    ("F5: the loop ends at the bound for a withdrawn inhibit too",
     "gi_sched.c",
     """            if (s->held_class == GS_CLASS_INHIBIT)
            {
                s->aborting = true;""",
     """            if (s->held_class == GS_CLASS_INHIBIT)
            {
                s->aborting = false;""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5: the bound is counted on every pass past it",
     "gi_sched.c",
     """    if (!s->abort_past_bound && now - s->abort_since >= GS_ABORT_BOUND_US)""",
     """    if (now - s->abort_since >= GS_ABORT_BOUND_US)""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5: a new abort keeps the last one's past-bound state",
     "gi_sched.c",
     """    s->abort_cmds_this = 0;
    s->abort_past_bound = false;""",
     """    s->abort_cmds_this = 0;""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5: a later deadline books the held skip again",
     "gi_sched.c",
     """    if (!s->inhibit_outstanding || s->held_booked)""",
     """    if (!s->inhibit_outstanding)""",
     "F5: a deadline skip held past the bound is aborted, booked once"),

    # F7 (spec 5.2 item 6, user 2026-11-26): the bound ends the loop only on
    # a read that issued no command.
    ("F7: the bound ends the loop on the read that issued the command",
     "gi_sched.c",
     """         * result instead: EMPTY books it aborted and requeued (step 3).
         */
        return false;""",
     """         * result instead: EMPTY books it aborted and requeued (step 3).
         */""",
     "F7: a page aborted on the bound's read is requeued, not sent"),

    # F5 option A (spec 5.2 item 5, user 2026-11-27): each pass past the bound
    # re-reads about every 2 us, for up to 1 ms, and issues the abort at the
    # first read with TS = 0.
    ("F5A: no re-read past the bound",
     "gi_sched.c",
     """        b = reread_past_bound(s);""",
     """        (void)reread_past_bound;""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5A: the re-read for every class, not only a withdrawn inhibit",
     "gi_sched.c",
     """    if (s->abort_past_bound && b == GS_BUF_TRANSMITTING)""",
     """    if (b == GS_BUF_TRANSMITTING)""",
     "R1: the 1 ms abort bound is reached and counted"),

    ("F5A: the re-read before the bound too",
     "gi_sched.c",
     """    if (s->abort_past_bound && b == GS_BUF_TRANSMITTING)""",
     """    if ((s->abort_past_bound || s->held_class == GS_CLASS_INHIBIT)
        && b == GS_BUF_TRANSMITTING)""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5A: the re-read spans 2 ms",
     "gi_sched.c",
     """#define GS_REREAD_SPAN_US   1000""",
     """#define GS_REREAD_SPAN_US   2000""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5A: the re-read spans 0.5 ms",
     "gi_sched.c",
     """#define GS_REREAD_SPAN_US   1000""",
     """#define GS_REREAD_SPAN_US   500""",
     "F5: the re-read stops at 1 ms, and meets a window inside it"),

    ("F5A: the reads 8 us apart",
     "gi_sched.c",
     """#define GS_REREAD_GAP_US    2""",
     """#define GS_REREAD_GAP_US    8""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5A: the re-read goes on after the read that issued the command",
     "gi_sched.c",
     """    while (b == GS_BUF_TRANSMITTING
           && h->now_us(h->ctx) - t0 < GS_REREAD_SPAN_US)""",
     """    while (b != GS_BUF_EMPTY
           && h->now_us(h->ctx) - t0 < GS_REREAD_SPAN_US)""",
     "F5: a stand-down held past the bound is aborted on every pass"),

    ("F5A: two waits between reads (4 us apart)",
     "gi_sched.c",
     """        h->spin_us(h->ctx, GS_REREAD_GAP_US);
        b = h->abort_if_awaiting(h->ctx);""",
     """        h->spin_us(h->ctx, GS_REREAD_GAP_US);
        h->spin_us(h->ctx, GS_REREAD_GAP_US);
        b = h->abort_if_awaiting(h->ctx);""",
     "F5: a stand-down held past the bound is aborted on every pass"),
]


def build_and_run(workdir):
    """(returncode, output). returncode None means THE BUILD FAILED.

    A build failure is NOT a caught mutation and must never be scored as one.
    The first version of this harness printed "the mutant does not compile --
    counts as caught" for all five mutations, and every one of them was actually
    a broken temp-directory layout: `-I../../main` resolved outside the temp tree
    because the copy was one directory too shallow. Five mutants reported as
    caught, zero actually exercised, and the run printed a clean summary. That is
    the shape this project keeps recording -- a check whose failure is
    indistinguishable from its success -- reproduced inside the harness meant to
    catch it.
    """
    r = subprocess.run(["make", "-s", "-C", workdir], capture_output=True,
                       text=True)
    if r.returncode != 0:
        return None, (r.stdout + r.stderr)[-800:]
    r = subprocess.run([os.path.join(workdir, "sched_test")],
                       capture_output=True, text=True, cwd=workdir)
    return r.returncode, r.stdout


def main():
    survived = []
    uncovered = []
    build_failed = []
    skipped = []

    # BASELINE FIRST. Without it, a suite that fails for an unrelated reason
    # reports every mutation as caught -- the same "failure looks like success"
    # shape this harness already had once, one level up. The reviewing session
    # runs its own baseline for exactly this reason.
    print("[baseline] the unmutated suite must PASS")
    rc = subprocess.run(["make", "-s", "-C", HERE], capture_output=True,
                        text=True)
    if rc.returncode != 0:
        print("  the baseline does not even build; nothing below means anything")
        return 2
    rc = subprocess.run([os.path.join(HERE, "sched_test")], capture_output=True,
                        text=True, cwd=HERE)
    if rc.returncode != 0:
        print("  THE BASELINE FAILS. Every 'caught' below would be meaningless.")
        for line in rc.stdout.splitlines()[-10:]:
            print("  %s" % line)
        return 2
    print("  baseline passes\n")

    run = gi_paths.scratch("gen-inhibit-sched-mutants")
    for name, fname, old, new, expect_case in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="gsmut_", dir=run)
        try:
            # test/gen_inhibit_sched, so that the Makefile's
            # ../../firmware/main resolves INSIDE the temp tree. One level too
            # shallow and every mutant fails to build for a reason that has
            # nothing to do with the mutation.
            wd = os.path.join(tmp, "test", "gen_inhibit_sched")
            shutil.copytree(HERE, wd,
                            ignore=shutil.ignore_patterns("sched_test", "*.o",
                                                          "__pycache__"))
            m = os.path.join(tmp, "firmware", "main")
            os.makedirs(m)
            for f in ("gi_sched.c", "gi_sched.h", "gen_inhibit_core.h"):
                src = os.path.join(MAIN, f)
                if os.path.exists(src):
                    shutil.copy(src, m)

            # The source machine's clock can be ahead of this one (measured:
            # ~2 minutes), and make then warns about files from the future and
            # may not rebuild. Stamp everything to now.
            for root, _d, files in os.walk(tmp):
                for f in files:
                    os.utime(os.path.join(root, f), None)

            target = os.path.join(m, fname)
            with open(target) as fh:
                body = fh.read()
            # A mutation is one (old, new) pair, or, with `new` None, a list of
            # pairs applied in order, each of which must match exactly once.
            pairs = [(old, new)] if new is not None else old
            bad = [(a, body.count(a)) for a, _b in pairs if body.count(a) != 1]
            if bad:
                print("[SKIP] %-52s (pattern found %d times)"
                      % (name, bad[0][1]))
                skipped.append("%s (pattern found %d times)"
                               % (name, bad[0][1]))
                continue
            for a, b in pairs:
                body = body.replace(a, b)
            with open(target, "w") as fh:
                fh.write(body)

            rc, out = build_and_run(wd)
            if rc is None:
                print("[ERROR] %-52s THE MUTANT DID NOT BUILD -- inconclusive, "
                      "not caught" % name)
                for line in out.splitlines()[-12:]:
                    print("        %s" % line)
                build_failed.append(name)
                continue
            if rc == 0:
                print("[SURVIVED] %s" % name)
                if expect_case is None:
                    uncovered.append(name)
                else:
                    survived.append(name)
            else:
                hit = (expect_case is not None and expect_case in out)
                print("[caught]   %-52s%s" % (name,
                      "" if hit or expect_case is None
                      else "  (but NOT by the expected case!)"))
                if expect_case is not None and not hit:
                    survived.append(name + " (wrong case caught it)")
        finally:
            gi_paths.remove_tree(tmp)

    print()
    if uncovered:
        print("NOT COVERED BY ANY CASE -- these mutants pass the suite, which is")
        print("a gap in the suite and not a defect in the code:")
        for u in uncovered:
            print("  - %s" % u)
    if build_failed:
        print("MUTANTS THAT DID NOT BUILD -- these are INCONCLUSIVE and the run")
        print("proves nothing about them:")
        for b in build_failed:
            print("  - %s" % b)
    if survived:
        print("MUTANTS THAT SURVIVED AND SHOULD NOT HAVE:")
        for x in survived:
            print("  - %s" % x)
    # A SKIP FAILS THE RUN. A mutation whose anchor no longer matches exactly
    # once was never applied, so its property is unpinned -- and the line below
    # stayed true while one was skipped, which is how two such runs read as
    # clean (README, "Three mutations were skipped").
    if skipped:
        print("MUTATIONS THAT WERE SKIPPED -- never applied, so the run proves")
        print("nothing about them; fix the anchor:")
        for k in skipped:
            print("  - %s" % k)
    if survived or build_failed or skipped:
        return 1
    print("every mutation with a matching case was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
