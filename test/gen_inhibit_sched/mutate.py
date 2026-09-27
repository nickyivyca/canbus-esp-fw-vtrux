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
MAIN = os.path.normpath(os.path.join(HERE, "..", "..", "main"))

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

    ("drop the !aborting guard on the completion path",
     "gi_sched.c",
     """    if (s->held && !s->aborting && !s->hal->outstanding(s->hal->ctx))""",
     """    if (s->held && !s->hal->outstanding(s->hal->ctx))""",
     "item 5: an abort is not credited as a send"),

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

    for name, fname, old, new, expect_case in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="gsmut_")
        try:
            # test/gen_inhibit_sched, so that the Makefile's ../../main
            # resolves INSIDE the temp tree. One level too shallow and every
            # mutant fails to build for a reason that has nothing to do with the
            # mutation.
            wd = os.path.join(tmp, "test", "gen_inhibit_sched")
            shutil.copytree(HERE, wd,
                            ignore=shutil.ignore_patterns("sched_test", "*.o",
                                                          "__pycache__"))
            m = os.path.join(tmp, "main")
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
            if body.count(old) != 1:
                print("[SKIP] %-52s (pattern found %d times)"
                      % (name, body.count(old)))
                continue
            with open(target, "w") as fh:
                fh.write(body.replace(old, new))

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
            shutil.rmtree(tmp, ignore_errors=True)

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
    if survived or build_failed:
        return 1
    print("every mutation with a matching case was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
