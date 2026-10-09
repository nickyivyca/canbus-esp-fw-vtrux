"""Offline test for the spec 4.1 repeat check: real logs, then mutations.

WHY THIS EXISTS. Rev 1 demanded that `synth` equal exactly 20 per REPEAT.
On `fault-during-override` that is unreachable on a CORRECT core, because
spec 4.1 says "A page-00 frame from the VCU during a repeat ends the
repeat; the VCU's frame is the truth" -- and in that scenario the VCU's
close-out lands inside the burst:

    burst_frames 20 x burst_period_ms 50          = exactly 1,000 ms
    REACT_HOLD_S (VCU flow drop from a mode-3 hold) = exactly 1.0 s

A dead heat by construction, so the achieved count is scheduling jitter.
Measured on the same code: 13 at 1x, 19 at 5x, 7 on the board -- and
`restart-after-standby`, which runs the identical race, won it and sent
all 20. Rev 1 was a check that could only fail there; any threshold
inside 7..20 would have been a coin toss dressed up as a limit.

WHAT IS PROVEN HERE. Passing the real logs mostly shows the check still
says yes to runs it already accepted. The mutations are the half that can
fail, and the two that matter are the ones that must STILL fail: a short
burst with no superseding frame, and a truncated burst in a scenario where
the VCU said nothing. Without those, "accept short bursts" would be
indistinguishable from not checking the count at all.

    py -3.14 projects/vtrux/tools/interposer/test_repeat_bursts.py
    python3.13 projects/vtrux/tools/interposer/test_repeat_bursts.py [run-dir]
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_scenario as R                                    # noqa: E402

DEFAULT_RUN = (HERE.parents[1] / "notes" / "artifacts" / "interposer-runs"
               / "acceptance-1x-2026-10-04")

# Every scenario that declares repeat_bursts_of_20().
REAL = ["evap-override", "evap-override-above-ceiling",
        "evap-override-early-bms", "evap-override-imbalanced",
        "evap-override-16a", "fault-during-override",
        "hard-ceiling-failsafe"]

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def refuses(intp, veh, fragment, what, chg=""):
    ok, why = R._repeat_bursts(intp, veh, chg_text=chg)
    if ok:
        check(False, "%s -- but the check PASSED it: %s" % (what, why))
    else:
        check(fragment in why,
              "%s -> %s" % (what, why if fragment in why
                            else "WRONG REASON: " + why))


def main():
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RUN
    print("--- spec 4.1 repeat check, burst = %d x %d ms = %d ms ---"
          % (R.BURST_FRAMES, R.BURST_MS // R.BURST_FRAMES, R.BURST_MS))
    print("run: %s\n" % run)

    missing = [n for n in REAL
               if not (run / ("%s.interposer.log" % n)).exists()]
    if missing:
        print("TEST FAULT: no saved logs for %s under %s. This test asserts "
              "nothing without them, so it reports a fault rather than a "
              "pass." % (", ".join(missing), run))
        return 2

    def logs(name):
        return ((run / ("%s.interposer.log" % name)).read_text(errors="replace"),
                (run / ("%s.vehicle.log" % name)).read_text(errors="replace"))

    print("real logs -- every scenario that declares the check:")
    for name in REAL:
        intp, veh = logs(name)
        ok, why = R._repeat_bursts(intp, veh)
        check(ok, "%-30s %s" % (name, why))

    # The regression, stated as its own assertion.
    intp, veh = logs("fault-during-override")
    ok, why = R._repeat_bursts(intp, veh)
    check(ok and "synth 13 of 20" in why,
          "fault-during-override passes AND the achieved count 13 is in the "
          "why-string, so a drift to 3 would still be visible")
    check(ok and "superseded at" in why,
          "and it names the superseding frame, so the excuse is evidenced "
          "rather than assumed")

    # A complete run must not be reported as superseded.
    intp2, veh2 = logs("restart-after-standby") if (
        run / "restart-after-standby.interposer.log").exists() else (None, None)
    if intp2 is not None:
        ok2, why2 = R._repeat_bursts(intp2, veh2)
        check(ok2 and "every one complete" in why2,
              "restart-after-standby runs the same 1 s race, won it, and is "
              "reported as complete -- not as excused")

    print("\nmutations -- each must be rejected, for its own reason:")

    # THE ONE THAT MATTERS. Same short burst, no cause in the vehicle log.
    veh_nocause = "\n".join(
        l for l in veh.splitlines()
        if "VCU: commanding" not in l and "VCU: dropping energy flow" not in l)
    refuses(intp, veh_nocause, "superseded the repeat",
            "13 of 20 with the VCU's close-out removed, so nothing "
            "superseded it")

    # A normal release burst, truncated, in a scenario the VCU was silent in.
    intp_ov, veh_ov = logs("evap-override")
    import re as _re
    intp_cut = _re.sub(r"synth=20\b", "synth=6", intp_ov)
    assert "synth=6" in intp_cut, "fixture did not mutate"
    refuses(intp_cut, veh_ov, "superseded the repeat",
            "a release burst cut to 6 of 20 with no VCU page 00 anywhere "
            "near it")

    # More sent than announced.
    refuses(_re.sub(r"synth=20\b", "synth=33", intp_ov), veh_ov,
            "exceeds the", "synth 33 against one announced burst of 20")

    # A REPEAT that announces the wrong shape.
    refuses(intp_ov.replace(", 20 frames", ", 5 frames"), veh_ov,
            "not 20", "a REPEAT announcing 5 frames instead of 20")

    # No tally at all is not a pass.
    refuses(_re.sub(r"final state=\w+.*", "final state=PASSTHROUGH", intp_ov),
            veh_ov, "no final synth counter",
            "the core printed no synth tally")

    # No REPEAT at all is not a pass either.
    refuses("12:00:00 intp nothing here\n", veh_ov, "nothing here to be "
            "right about", "a log with no REPEAT event")

    # ---- per-burst scoring, on logs that CARRY the end lines -------------
    #
    # The saved acceptance logs predate machine.py's REPEAT-ended line, so
    # everything above exercises the AGGREGATE path. The aggregate cannot
    # attribute a shortfall: the review session passed a real two-burst log
    # with synth 21 of 40 and one superseding frame inside burst 1's window,
    # which fits equally well with burst 2 sending 1 frame for no reason.
    # These cases are synthetic because no saved log has the end lines yet.
    print("\nper-burst scoring (logs that carry the end lines):")

    def log2(e1="20 of 20, complete", e2="20 of 20, complete", synth=40,
             drop_second_end=False):
        rows = [
            "14:18:27 intp [OVERRIDE] REPEAT: the VCU's standing page-00 "
            "command to the charger, flow 1 mode 1, 20 frames",
            "14:18:28 intp REPEAT ended: " + e1,
            "15:12:47 intp [TERMINATED] REPEAT: the VCU's standing page-00 "
            "command to the charger, flow 1 mode 3, 20 frames",
        ]
        if not drop_second_end:
            rows.append("15:12:48 intp REPEAT ended: " + e2)
        rows.append("15:30:00 intp final state=PASSTHROUGH  fwd v->c=1 "
                    "c->v=1 modified=0 synth=%d" % synth)
        return "\n".join(rows) + "\n"

    # A vehicle log with a VCU page 00 inside BURST 1's window only.
    veh_b1 = ("14:18:27 veh VCU: commanding CHARGER (page 00)\n"
              "16:00:00 veh VCU: commanding STAND_BY (session over)\n")
    # ... and one inside BURST 2's window only.
    veh_b2 = ("15:12:47 veh VCU: dropping energy flow, mode left at "
              "CHARGER_LOW_POWER\n")

    ok, why = R._repeat_bursts(log2(), veh_b1, )
    check(ok and why.startswith("per-burst:"),
          "two complete bursts summing to synth 40 pass, and are scored "
          "per-burst rather than in aggregate -> %s" % why)

    ok, why = R._repeat_bursts(
        log2(e2="9 of 20, superseded by the VCU's page 00", synth=29), veh_b2)
    check(ok and "burst 2" in why and "superseded" in why,
          "burst 2 short WITH a VCU page 00 in ITS OWN window passes -> %s"
          % why)

    # THE EXACT HOLE. Burst 2 claims it was superseded, but the only VCU
    # frame is in burst 1's window. The aggregate passed this; per-burst
    # must not.
    refuses(log2(e2="1 of 20, superseded by the VCU's page 00", synth=21),
            veh_b1, "burst 2 at 15:12:47 reports SUPERSEDED",
            "burst 2 claims superseded but the VCU frame is in BURST 1's "
            "window (the aggregate hole)")

    refuses(log2(e2="1 of 20, superseded by the VCU's page 00", synth=21),
            "14:00:00 veh nothing relevant\n",
            "does not get to excuse it",
            "a SUPERSEDED end with no VCU page 00 anywhere")

    refuses(log2(synth=37), veh_b1, "sum to 40 but the core's final synth",
            "per-burst counts summing to 40 against a final synth of 37")

    refuses(log2(drop_second_end=True, synth=40), veh_b1,
            "never reported how it ended",
            "two REPEATs but only one end line")

    refuses(log2(e1="13 of 20, complete", synth=33), veh_b1,
            "reports COMPLETE but sent 13",
            "an end claiming COMPLETE while short")

    refuses(log2(e1="21 of 20, complete", synth=41), veh_b1,
            "more than it announced",
            "a burst sending 21 of an announced 20")

    # ABANDONED (spec 4.1, 2026-10-07): legal only when a spec 6.1 session
    # boundary is witnessed inside the burst's window -- the VCU's STAND_BY
    # or EXPORT, the pilot timer going back to 0 (charger_sim's HANDLE
    # PULLED / REPLUGGED), or the charger silent for 20 s. Until 2026-10-08
    # every ABANDONED end failed, pending that text (tracker F item 1).
    ab = log2(e2="3 of 20, abandoned on session reset", synth=23)
    board_ab = (log2(e2="3 of 20, X", synth=23)
                .replace("REPEAT ended: 20 of 20, complete",
                         "REPEAT ended a=20 b=20 c=0")
                .replace("REPEAT ended: 3 of 20, X",
                         "REPEAT ended a=3 b=20 c=2"))
    veh_sb2 = veh_b1 + "15:12:48 veh VCU: commanding STAND_BY (session over)\n"
    chg_pull = "15:12:48 chg charger: HANDLE PULLED at t=900s (shutdownSource 11)\n"
    chg_silent = "15:12:27 chg charger: GOING SILENT at t=880s -- no further\n"

    refuses(ab, veh_b1, "neither log shows",
            "a burst ended 'abandoned on session reset' with no boundary "
            "anywhere near it (the only STAND_BY is at 16:00:00)")
    refuses(board_ab, veh_b1, "neither log shows",
            "the board's code 2 (abandoned) with no witness")
    for intp, form in ((ab, "prose"), (board_ab, "board code 2")):
        ok, why = R._repeat_bursts(intp, veh_sb2)
        check(ok and "abandoned, boundary VCU STAND_BY" in why,
              "%s: ABANDONED at 3 of 20 passes with the VCU's STAND_BY at "
              "15:12:48 inside the window -> %s" % (form, why))
    ok, why = R._repeat_bursts(ab, veh_b1, chg_text=chg_pull)
    check(ok and "pilot timer back to 0" in why,
          "ABANDONED passes with a handle pull (pilot timer to 0) in the "
          "charger log inside the window -> %s" % why)
    refuses(ab, veh_b1, "neither log shows",
            "the same log with the charger log withheld -- the witness has "
            "to come from the charger log, not appear by accident")
    ok, why = R._repeat_bursts(ab, veh_b1, chg_text=chg_silent)
    check(ok and "charger silent since 15:12:27" in why,
          "ABANDONED passes when the 20 s charger-silence boundary "
          "(GOING SILENT at 15:12:27) falls inside the window -> %s" % why)
    refuses(ab, veh_b1, "neither log shows",
            "charger silent from 15:12:00 -- its boundary at 15:12:20 is "
            "before the burst, so it cannot have cut it",
            chg="15:12:00 chg charger: GOING SILENT at t=850s\n")
    refuses(ab, veh_b1 + "15:12:48 veh VCU: commanding CHARGER_LOW_POWER "
            "(ceiling)\n", "neither log shows",
            "a VCU page 00 that is not STAND_BY or EXPORT is no session "
            "boundary, so it does not witness ABANDONED")
    refuses(log2(e2="20 of 20, abandoned on session reset", synth=40),
            veh_sb2, "nothing left to send",
            "ABANDONED claiming all 20 sent, witness or not")
    refuses(log2(e2="3 of 20, X", synth=23)
            .replace("REPEAT ended: 20 of 20, complete",
                     "REPEAT ended a=20 b=20 c=0")
            .replace("REPEAT ended: 3 of 20, X",
                     "REPEAT ended a=3 b=20 c=7"),
            veh_sb2, "does not account for",
            "an end code the harness does not know (c=7), even with a "
            "boundary in the window")
    import inspect
    check("_repeat_bursts(intp, veh, chg_text=chg)"
          in inspect.getsource(R.evaluate),
          "evaluate() hands the charger log to the repeat scorer, so the "
          "pilot and silence witnesses can be found in a real run")

    # The board writes the same event as a code plus numbers.
    board = log2().replace("REPEAT ended: 20 of 20, complete",
                           "REPEAT ended a=20 b=20 c=0")
    ok, why = R._repeat_bursts(board, veh_b1)
    check(ok and why.startswith("per-burst:"),
          "the BOARD form of the end line is scored per-burst too, so a "
          "hardware log is not silently demoted to the aggregate -> %s" % why)

    # And the saved logs must still take the aggregate path, labelled.
    intp_fd, veh_fd = logs("fault-during-override")
    ok, why = R._repeat_bursts(intp_fd, veh_fd)
    check(ok and why.startswith("aggregate (no end lines)"),
          "a log recorded before the end lines existed is scored in "
          "aggregate AND says so, so nobody reads it as per-burst")

    # ------------------------------------------------------------------
    # SPEC 4.1, 2026-10-05: "A repeat that starts while another is still
    # being sent replaces it: the earlier one stops there, the frames it
    # already sent stand, and only the new command continues."
    #
    # The witness for a REPLACED end is the NEXT REPEAT, and it has to be
    # inside the replaced burst's own window -- the same shape as the
    # SUPERSEDED rule, where the witness is the vehicle log. Without the
    # window the label would excuse any short burst that happens to be
    # followed by another repeat later in the run, which is most of them.
    print("\nspec 4.1 replaced (a repeat starting over one in flight):")

    def log_repl(gap_s=0, sent=9, e2="20 of 20, complete", synth=None,
                 drop_next_repeat=False):
        """Burst 1 replaced `gap_s` seconds after it opened at 14:18:27."""
        if synth is None:
            synth = sent + 20
        t2 = "14:18:%02d" % (27 + gap_s)
        rows = [
            "14:18:27 intp [OVERRIDE] REPEAT: the VCU's standing page-00 "
            "command to the charger, flow 1 mode 1, 20 frames",
            "%s intp REPEAT ended: %d of 20, replaced by a new repeat"
            % (t2, sent),
        ]
        if not drop_next_repeat:
            rows.append("%s intp [SAFE] REPEAT: the VCU's standing page-00 "
                        "command to the charger, flow 1 mode 3, 20 frames"
                        % t2)
            rows.append("%s intp REPEAT ended: %s" % (t2, e2))
        rows.append("15:30:00 intp final state=SAFE  fwd v->c=1 "
                    "c->v=1 modified=0 synth=%d" % synth)
        return "\n".join(rows) + "\n"

    # No vehicle page 00 anywhere: a REPLACED end must stand on the next
    # REPEAT alone, and must not be quietly rescued by the SUPERSEDED rule.
    veh_none = "14:00:00 veh nothing relevant\n"

    ok, why = R._repeat_bursts(log_repl(gap_s=0), veh_none)
    check(ok and "replaced" in why and "burst 1" in why,
          "burst 1 replaced in the same second, next REPEAT inside its "
          "window, passes with no VCU frame involved -> %s" % why)

    # THE +100..+950 ms CASES. Both logs are stamped to the second, so
    # every sub-second offset inside one burst collapses onto gap 0, and
    # the 1,000 ms boundary lands on gap 1. These are the runs that were
    # CORRECT and that both the old and the new scoring failed before the
    # end line existed -- two REPEATs with one end between them.
    for sent in (2, 9, 19):
        ok, why = R._repeat_bursts(log_repl(gap_s=0, sent=sent), veh_none)
        check(ok, "a replacement %d frames in is a PASS now that the end "
                  "line exists (was: 2 REPEATs, 1 end) -> %s" % (sent, why))
    ok, why = R._repeat_bursts(log_repl(gap_s=1), veh_none)
    check(ok, "and at the 1,000 ms boundary, one second later, still "
              "inside the window -> %s" % why)

    # MUST FAIL 1: nothing replaced it.
    refuses(log_repl(drop_next_repeat=True, synth=9), veh_none,
            "no later REPEAT exists",
            "a REPLACED end with no following REPEAT at all")

    # MUST FAIL 2: the next REPEAT is outside the replaced burst's window,
    # so burst 1 had already finished or been abandoned and REPLACED is
    # the wrong reason. gap 3 s against a 1,000 ms burst plus the one
    # second of log resolution.
    refuses(log_repl(gap_s=3), veh_none,
            "outside its window",
            "a REPLACED end whose next REPEAT starts after the window")

    # MUST FAIL 3: a burst with nothing left to send cannot be replaced --
    # the hook only fires with frames still owed, so this contradicts the
    # code that writes the line.
    refuses(log_repl(gap_s=0, sent=20), veh_none,
            "cannot be replaced",
            "a REPLACED end claiming it sent all 20 of 20")

    # MUST FAIL 4: the replaced frames count toward synth, so a final
    # synth that omits them is a run whose own numbers disagree.
    refuses(log_repl(gap_s=0, sent=9, synth=20), veh_none,
            "final synth",
            "a final synth of 20 that omits the 9 replaced frames")

    print("\nthe window:")
    hits = R._page00_in_burst(veh, "02:13:37")
    # Two page-00 changes share that second -- the flow drop and the
    # STAND_BY command -- so the window returns both. Asserting a
    # single-element list here failed on the real log, which was the
    # assertion being wrong rather than the window.
    check(set(hits) == {"02:13:38"} and len(hits) == 2,
          "both of the VCU's page-00 changes at 02:13:38 fall inside the "
          "burst opened at 02:13:37 (got %s)" % hits)
    check(R._page00_in_burst(veh, "02:10:00") == [],
          "and a burst three minutes earlier finds nothing, so the window "
          "is a window and not a whole-log search")

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ntest_repeat_bursts: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
