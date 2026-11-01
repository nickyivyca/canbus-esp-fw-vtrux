#!/usr/bin/env python3
"""The standing negative control for E1: break the firmware, expect red.

Written by the reviewing session on 2026-09-25 and adopted here, because E1's
failure mode is silent greenness and this is the only thing that measures it.
Four of the seven mutations survived the first version of E1, INCLUDING the
exact defect it was written to catch.

  M1  RETIRED at port step 1b (see RETIRED below)
  M2  the core's diag deferral removed
  M3  RETIRED at port step 1b (see RETIRED below)
  M4  the quiesce self_off reason removed
  M5  the receive-error self_off reason removed
  M6  PASSIVE allowed to reach build_inhibit()
  M7  the pre-queue drain of a held TX_FAILED removed (dispatch_emits(), the
      inhibit branch). Written when the drain read the driver's alerts; since
      port step 1b it polls twai_port's alerts and drops the transmit failure
      the port holds (twai_port T5). CAUGHT since 2026-11-01, by shim case 61.
      Before 1b it survived, and was accepted, because the frame-completion
      path consumed a pending TX_FAILED immediately before dispatch_emits() in
      both worker paths, leaving no window for a stale alert. On 1b there is a
      window in the model: the shared fake raises TX_FAILED for a frame it
      fails, and twai_port holds it until taken, which is only at an
      inhibit's completion, so without the drain a failed diag page is
      reported as the next inhibit's failure and trips the device. Case 61
      fails a status page and expects no trip (reviewer, 2026-11-01). On the
      device the bit is raised for single-shot transmission only, and these
      frames go out with ss = 0; the call stays because it would matter if a
      frame ever went out single-shot.
  N1  completion off-by-one: dev_outstanding(), the scheduler's "is a frame of
      ours still in the controller", reads tx_pending != 0 -> > 1
  N2  RETIRED at the gi_sched switchover (see RETIRED below)
  N3  a port 0 bring-up failure ignored at arm. Written for a
      twai_reconfigure_alerts() failure; since port step 1b the alerts are one
      step of can_port_stock_enable(), whose failure at any step (the pins,
      the install, the start or the alerts) refuses the arm (spec 3.3, user
      2026-10-31), so this ignores that refusal. SURVIVES ON 1b, as an
      equivalent mutant: a failed bring-up leaves port 0 uninstalled, and
      apply_mode()'s next two guards, !can_is_enabled() and
      !controller_running(), refuse the arm with the same
      ESP_ERR_INVALID_STATE; only the log line differs. Before 1b the alerts
      were enabled on a bus already up, so this was their only guard. Kept as
      a tripwire on those inner guards, as V08 and V09 are. A
      twai_reconfigure_alerts() failure that did not fail the install would be
      twai_port's defect, for its unit mutants
  N4  OBSERVE not forced listen-only
  N5  the spec 8 quiesce handshake does not wait -- the use-after-free guard
  N6  the probe offset limit relaxed from 4000 to 40000 us
  N7  the core ignores the scheduler's skip trip (gi_on_inhibit_skip()). It was
      the TX_LATE test, which spec 7 trip 7 as amended replaced with "a third
      skipped inhibit inside a second trips"; this is that trip's core side
  K06 an explicit re-arm no longer clears the section 7 abort latch
  K08 the key-on clear no longer resets rpm_ever -- the BUG 2 class
  K17 the SoC threshold becomes <=, moving the 21.00 % boundary by one count
  K21 SoC recovering above the threshold un-latches the release -- UNKILLABLE,
      and not for want of a test: disable_monitor() returns early once a release
      has latched, so the branch K21 edits cannot run. Spec 6.2's
      "unconditional on SoC recovering" is enforced one level above it. Kept in
      the set as a tripwire on that early return, which is the thing that would
      have to move for K21 to become killable -- and dangerous.

ROUND 3 MUTATED THE CORE, and its most useful finding was not a survivor: all
15 of the mutations it DID catch were caught by the GOLDENS alone -- never by
passive_diff's invariants and never by E1, and several by a single golden. The
goldens are load-bearing in a way a regression harness is not supposed to be,
which is the argument for E2 stating properties instead.

K08 is the one to reread if any of this is ever simplified: with rpm_ever
surviving a key-on clear, the device goes live on the way back in (correctly --
the gate does not require the GENE family) and then aborts STALE_RPM
immediately, about 28 s before the inverter wakes, on a healthy truck, every
re-key. That is bug 2 of 2026-09-19 in a third place.

N1-N7 were added by the reviewing session in a SECOND round against bea8d51,
after M1-M6 were all being caught. Five of them survived: N2, N3, N4, N5 and
N6. That is the pattern worth noticing -- each round of "all mutations caught"
has been followed by a round that found more, so a green mutation run means
"nothing in THIS set survives", never "the suite is adequate".

N3, K21, V08 and V09 are EXPECTED to survive, each for the reason at its
entry (N3's measured on the 1b code, 2026-11-01). Every other mutation must
come back rc=1, M7 included since case 61 (2026-11-01), and a survivor that is
not one of those four is a finding.

RETIRED (gen-inhibit implementor, port step 1b, 2026-11-01; reviewer
2026-10-31: CAN-side mutants of can_port and twai_port belong in those
components' unit mutants). Each mutated a line that no longer exists, and
until 2026-10-31 each ran nothing without the run failing (see "A STALE
ANCHOR" below):
  M1  632af32's alert-based completion reinstated -- the shipped bug. The
      completion test it broke, poll_tx_completion(), went at the gi_sched
      switchover (spec 5.2): completion is now the scheduler's record of what
      it handed over, with `outstanding` read through dev_outstanding(). N1
      mutates that read here; gi_sched's own mutate.py mutates the scheduler's
      use of it.
  M3  the spec 3.2 refusal removed from the REAL main/can.c. can.c retired at
      port step 1b; the refusal is in vtrux/can_port's can_stock.c, which this
      shim compiles as shipped from the pinned checkout, outside the copy this
      file mutates. Mutating it is can_port's unit mutants' job.
  N2  `behind` forced false, so spec 5's tx_queued_behind never counts. Since
      the gi_sched switchover dispatch_emits() passes `behind` as a literal
      false (spec 5.2 item 3: the scheduler hands over one frame at a time), so
      the mutation is the identity. tx_queued_behind staying 0 is asserted by
      spec 5.2 item 10's full-replay case.

READ rc, NOT THE TEXT. The reviewer's original scraped lines containing "FAIL",
which also matches the harness's own "twai_receive failed: ESP_FAIL" log lines,
so a surviving mutation was reported as CAUGHT. rc is the only signal.

Usage, with BASE a copy of the project folder, gen-inhibit/, which holds
firmware/main/ and test/:
    BASE=~/gi_review python3 mutate.py
"""

import subprocess, shutil, sys, os, re
BASE = os.path.expanduser(os.environ.get("BASE", "~/gi_review"))
# THE MUTANTS' COPIES are in this run's own folder in the worktree's scratch/,
# removed when the run ends, pass or fail (AGENTS.md "Removing scratch",
# 2026-10-31; gi_paths.scratch()), and each mutant's copy is removed before the
# next is made. They were in ~/gi_mut, outside the worktree, and cleared with
# shutil.rmtree's ignore_errors, which leaves what it cannot remove in silence.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "tools", "bench"))
import gi_paths
# The four lines dispatch_emits() uses to copy a core frame into a driver
# frame. The round-6 mutations mostly APPEND to it rather than replace it, so
# it is named once here instead of being retyped in each entry and drifting
# from the firmware -- a mutation whose anchor no longer matches does not fail,
# it silently does nothing, and a control that silently does nothing is the
# failure this whole file exists to prevent.
COPY = """    twai_message_t tx = { 0 };
    (void)ctx;
    tx.identifier = f->id;
    tx.data_length_code = f->dlc;
    memcpy(tx.data, f->data, 8);"""

PREFIX = "\n    "

MUT = {
 "M2_no_diag_deferral": ("firmware/main/gen_inhibit_core.c", [(
  "    if (st->tx_pending)\n    {\n        /* fall through to the interlocks; the page goes out next tick */\n    }\n    else if (",
  "    if (0)\n    {\n    }\n    else if (")]),
 "M4_quiesce_self_off_removed": ("firmware/main/gen_inhibit.c", [(
  "    s_core.self_off = GI_SELF_OFF_QUIESCE;", "    ")]),
 "M5_rx_error_self_off_removed": ("firmware/main/gen_inhibit_core.c", [(
  "        st->self_off = GI_SELF_OFF_RX_ERRORS;", "        ")]),
 "M6_passive_builds_frame": ("firmware/main/gen_inhibit_core.c", [(
  "            st->would_tx++;\n            return;", "            st->would_tx++;")]),
 "M7_no_predrain": ("firmware/main/gen_inhibit.c", [(
  "            (void)twai_port_poll_alerts();\n            (void)twai_port_take_tx_failed();",
  "            ")]),
 "N1_completion_off_by_one": ("firmware/main/gen_inhibit.c", [(
  "    return info.tx_pending != 0;", "    return info.tx_pending > 1;")]),
 "N3_bringup_failure_ignored": ("firmware/main/gen_inhibit.c", [(
  "            const esp_err_t up = can_port_stock_enable();\n            if (up != ESP_OK)",
  "            const esp_err_t up = can_port_stock_enable();\n            if (0 && up != ESP_OK)")]),
 "N4_observe_not_listen_only": ("firmware/main/gen_inhibit.c", [(
  "        const bool want_listen_only = (mode == GEN_INHIBIT_OBSERVE);",
  "        const bool want_listen_only = false;")]),
 "N5_quiesce_does_not_wait": ("firmware/main/gen_inhibit.c", [(
  "        if (served)",
  "        if (1)")]),
 "N6_offset_limit_relaxed": ("firmware/main/gen_inhibit.c", [(
  "    if (offset_us > 4000)", "    if (offset_us > 40000)")]),
 "N7_skip_trip_ignored": ("firmware/main/gen_inhibit_core.c", [(
  "    if (trip && st->inhibit_live)", "    if (0)")]),
 # Round 3 (reviewing session, 2026-09-25) mutated the CORE. These four were
 # real survivors and now have scenarios; K01 and K12 were judged equivalent and
 # are not here -- K01's reasoning is recorded in bus_alive_since()'s comment.
 "K06_rearm_keeps_abort_latch": ("firmware/main/gen_inhibit_core.c", [(
  "    st->abort_latched = false;\n    st->rpm_over = false;",
  "    st->rpm_over = false;")]),
 "K08_keyon_keeps_rpm_ever": ("firmware/main/gen_inhibit_core.c", [(
  "    st->rpm_ever      = false;\n}", "}")]),
 "K17_soc_threshold_le": ("firmware/main/gen_inhibit_core.c", [(
  "            if (raw < st->cfg.soc_min_raw)",
  "            if (raw <= st->cfg.soc_min_raw)")]),
 "K21_soc_recovery_unlatches": ("firmware/main/gen_inhibit_core.c", [(
  "            else\n            {\n                st->soc_low_count = 0;\n            }",
  "            else\n            {\n                st->soc_low_count = 0;\n                st->disabled = false;\n            }")]),
 # ---- ROUND 6: the shim -> driver copy boundary ---------------------------
 #
 # Every one of these lives in dispatch_emits(), in the four lines that copy a
 # gi_frame_t into a twai_message_t. Five of them survived EVERY suite on
 # 2026-09-25 -- goldens, passive_diff, invariants and E1 alike -- because all
 # four watch the CORE's emit list, which is upstream of the copy and was
 # correct in every case. Nothing anywhere asserted what the driver was handed.
 #
 # V04 and V05 are the two that would matter on the truck. V04 sends the
 # inhibit on a 29-bit identifier: the GENE inverter filters on the 11-bit
 # 0x051 and never sees it, so the VCM's torque stands while tx_ok counts up
 # and every diag page reports a healthy inhibit. V05 makes the controller
 # deliver our own frame back to us; the worker cannot tell it from the VCM's
 # and answers it, which is received, and answered.
 #
 # They are killed by the wire invariant in shim_harness.c, which checks every
 # frame handed to twai_transmit(), and -- for V05 -- by the reactive-ratio
 # check in shim_test.c's teardown, which catches the loop by its behaviour
 # rather than by the flag that caused it.

 "V01_rtr_set": ("firmware/main/gen_inhibit.c", [(
  COPY, COPY + PREFIX + "tx.rtr = 1;")]),

 "V02_dlc_8_not_6": ("firmware/main/gen_inhibit.c", [(
  "    tx.data_length_code = f->dlc;",
  "    tx.data_length_code = 8;")]),

 "V03_memcpy_6_of_8": ("firmware/main/gen_inhibit.c", [(
  "    memcpy(tx.data, f->data, 8);",
  "    memcpy(tx.data, f->data, 6);")]),

 "V04_extd_set": ("firmware/main/gen_inhibit.c", [(
  COPY, COPY + PREFIX + "tx.extd = 1;")]),

 "V05_self_set": ("firmware/main/gen_inhibit.c", [(
  COPY, COPY + PREFIX + "tx.self = 1;")]),

 "V06_probe_ignores_offset": ("firmware/main/gen_inhibit.c", [(
  "        if (f->have_due)", "        if (0)")]),

 "V07_single_shot": ("firmware/main/gen_inhibit.c", [(
  COPY, COPY + PREFIX + "tx.ss = 1;")]),
 # These three are the reviewing session's own round-6 mutations, carried over
 # rather than paraphrased. They reach past the copy boundary into what the
 # diag and probe frames SAY, and into which modes may emit at all.
 #
 # V08 AND V09 BOTH SURVIVE, and both are equivalent mutants rather than
 # coverage gaps. They have the same shape: each edits an OUTER guard that sits
 # in front of a standing INNER one, so removing it changes nothing.
 #
 #   V08  dispatch_emits() skips gi_on_tx_result() for a diag frame. Remove
 #        that and gi_on_tx_result() STILL returns early for GI_TX_DIAG, in
 #        gen_inhibit_core.c. Nothing downstream can tell.
 #   V09  gi_tick() returns immediately in OFF. Remove that and the worker
 #        still never calls gi_tick() in OFF -- it `continue`s in its
 #        s_core.mode == GI_OFF branch, after parking and releasing the bus.
 #
 # Kept in the set for the reason K21 is: each is a tripwire on the inner
 # guard. The day the inner one moves, the outer one stops being redundant and
 # these two start failing, which is exactly when someone needs to know.
 #
 # ONE REAL GAP IS VISIBLE IN V09 and is worth stating rather than hiding
 # behind the equivalence: gi_tick()'s OFF guard is unreachable from the
 # DRIVER, but the host harness in ../gen_inhibit_host calls the pure core
 # directly and could reach it. No scenario there ticks in OFF, so nothing
 # tests what the core does if asked to. That is a host-suite gap, not a shim
 # one, and it is small -- but it is a gap and not an equivalence.

 # The `continue` that keeps a diag frame's result away from the core. Without
 # it a diag completion is reported as an inhibit's -- the dc571cb
 # mis-crediting class, arriving by a different route.
 # Since the gi_sched switchover the same `continue` also skips the probe,
 # which is reported at handover instead, so only the diag half is removed.
 "V08_diag_fed_to_core": ("firmware/main/gen_inhibit.c", [(
  "        if (f->kind == GI_TX_DIAG || f->kind == GI_TX_PROBE)",
  "        if (f->kind == GI_TX_PROBE)")]),

 # Diag pages emitted in OFF. The device is meant to be silent there, not
 # merely not-inhibiting; caught by the empty allowed set every teardown
 # declares after the mode change.
 "V09_diag_in_off_mode": ("firmware/main/gen_inhibit_core.c", [(
  "    if (st->mode == GI_OFF)", "    if (0 && st->mode == GI_OFF)")]),

 # The probe's offset field zeroed. It still waits the right length of time --
 # only its report of that is wrong, which no timing assertion can see.
 "V10_probe_offset_byte": ("firmware/main/gen_inhibit_core.c", [(
  "    f->data[4] = (uint8_t)(st->offset_us);", "    f->data[4] = 0;")]),
 # ---- THE 2026-09-25 RULINGS ----------------------------------------------
 #
 # Each ruling changed behaviour, so each needs a mutation showing that a test
 # would notice it going away. W01 and W02 are the two that produced golden
 # changes; W03 and W04 guard the numbers rather than the shape.

 # Ruling 1: 0x617 back on the common 0.5 s window. This is the state the
 # firmware was in yesterday, and the one that latched the inhibit off for a
 # whole key cycle on a 300 ms bus glitch while blaming 0x617 for it.
 # Caught by bus-dropout-450-fault-worst-phase, which is written to sit 70 ms
 # inside the 1.0 s window at 0x617's worst phase.
 "W01_fault_window_back_to_fresh_us": ("firmware/main/gen_inhibit_core.c", [(
  'c->fault_fresh_us = 1000000;    /* 0x617 only; spec 7 "Freshness" */',
  'c->fault_fresh_us = 500000;')]),

 # Ruling 1, the subtler half: stale judged on 1.0 s but the EVIDENCE test
 # still asking for an 0x051 after 0.5 s. That is a different and much weaker
 # rule than spec 7 states, and it is the mistake the call site's comment
 # exists to warn against -- so it needs a mutation rather than a comment.
 "W02_evidence_window_mismatch": ("firmware/main/gen_inhibit_core.c", [(
  """        && bus_alive_since_w(st, st->have_fault, st->seen_fault,
                             (int64_t)st->cfg.fault_fresh_us))""",
  """        && bus_alive_since_w(st, st->have_fault, st->seen_fault,
                             (int64_t)st->cfg.fresh_us))""")]),

 # Ruling 2: the GENE family ordering put back the way it was, by making the
 # inverter trip fire only when 0x054 is ALSO still fresh -- which is the
 # behaviour the old ordering produced whenever both went quiet together.
 # Caught by gene-family-quiet-together; gene-rpm-stale-while-fb-fresh proves
 # the other direction is not broken by the fix.
 "W03_gene_order_reverted": ("firmware/main/gen_inhibit_core.c", [(
  "    if (st->fb_ever && !gi_fresh(st, st->have_fb, st->seen_fb, now)",
  """    if (st->fb_ever && gi_fresh(st, st->have_rpm, st->seen_rpm, now)
        && !gi_fresh(st, st->have_fb, st->seen_fb, now)""")]),

 # Ruling 3: the debounce count. The behaviour was already pinned by
 # low-soc-debounce; what changed today is that spec 6.2 states the number, so
 # config_vs_spec can trace it. Both should object -- the scenario because four
 # readings now release, the build check because 4 != 5.
 "W04_soc_debounce_4": ("firmware/main/gen_inhibit_core.c", [(
  "c->soc_debounce = 5;", "c->soc_debounce = 4;")]),
}
# A GREEN MUTATION RUN MEANS NOTHING IF THE BASELINE IS RED: every mutant is
# then reported caught by a failure that was already there. That happened once,
# with all fourteen "caught" and two baseline assertions failing.
_t = os.path.join(BASE, "test/gen_inhibit_shim")
_b = subprocess.run("make -s && ./shim_test", shell=True, cwd=_t,
                    capture_output=True, text=True)
if _b.returncode:
    print("BASELINE IS RED (rc=%d) -- fix it before reading anything below"
          % _b.returncode)
    for _l in _b.stdout.splitlines():
        if _l.strip().startswith("FAIL:"):
            print("   ", _l.strip()[:140])
    sys.exit(2)
print("baseline green")
RUN = gi_paths.scratch("gen-inhibit-shim-mutants")

# A STALE ANCHOR IS A FAILURE OF THIS FILE, not a skip: a mutation whose
# pattern no longer matches runs nothing, and until 2026-10-31 thirteen of the
# thirty-two had gone that way without the run failing. They are listed at the
# end and the run exits 3.
stale = []
for name, (f, reps) in MUT.items():
    d = os.path.join(RUN, "gi")
    if not gi_paths.remove_tree(d): sys.exit("the last mutant's copy is still there (above)")
    shutil.copytree(BASE, d)
    p = os.path.join(d, f); s = open(p).read()
    ok = True
    for a, b in reps:
        if s.count(a) != 1: ok = False; print(name, "PATTERN NOT FOUND", s.count(a)); break
        s = s.replace(a, b)
    if not ok:
        stale.append(name)
        continue
    open(p, "w").write(s)
    t = os.path.join(d, "test/gen_inhibit_shim")
    b = subprocess.run("make -s", shell=True, cwd=t, capture_output=True, text=True)
    if b.returncode: print(name, "BUILD FAIL", b.stderr[-300:]); continue
    r = subprocess.run("./shim_test", shell=True, cwd=t, capture_output=True,
                       text=True, timeout=300)
    out = r.stdout + r.stderr
    rc = r.returncode

    # THE CORE IS WHERE MOST OF THE LOGIC LIVES, so a mutation control that
    # only runs E1 scores the wrong thing. Round 3 mutated the core and found
    # that 15 of 15 caught mutations were caught by the GOLDENS alone -- never
    # by an invariant and never by E1. Scoring all three suites together is the
    # only honest total.
    h = os.path.join(d, "test/gen_inhibit_host")
    if os.path.isdir(os.path.join(h, "scenarios")):
        for cmd in ("python3 run_tests.py", "python3 passive_diff.py",
                    "python3 invariants.py"):
            hr = subprocess.run(cmd, shell=True, cwd=h, capture_output=True,
                                text=True, timeout=900)
            if hr.returncode:
                rc = rc or hr.returncode
                out += "\n[%s] %s" % (cmd.split()[-1], hr.stdout[-400:])
    else:
        out += "\n[note] no scenarios/ in this copy: host suite NOT scored"
    # rc, not text: "FAIL" also matches the harness's own ESP_FAIL log lines,
    # which reported surviving mutations as caught.
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL:")][:3]
    verdict = ("CAUGHT" if rc else "*** SURVIVED ***")
    print("%-32s rc=%d  %-16s %s" % (name, rc, verdict, " | ".join(fails)))

if stale:
    print("STALE ANCHORS (%d, nothing run for them): %s" % (len(stale), ", ".join(stale)))
    sys.exit(3)
