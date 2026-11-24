"""The forced-order cases' premise (run_scenario.py, 0041572d): the flow
drop must lead the fault flag by FORCED_ORDER_MIN_GAP_S on the witness (a
board) or on the core's own trace (emulation), or the case is NOT
EXERCISED, whatever its results; and the phase option that moves the
charger's --fault-at through its page rotation.

Tester, 2026-11-08: the premise test of scenario_scope_test.py, which is
retired now that port step 2a is accepted (the charge-interposer reviewer,
2026-11-08). Its scope check against c15f8c05 had one job, 2a's re-run;
this part tests harness logic that stays.

    py -3.14 -B charge-interposer/forced_order_test.py [--can-fail]

--can-fail runs the test against four mutants (the premise always held;
the order alone, without the 150 ms gap; a premise that did not hold
ignored by the verdict; a third case given the premise) and requires each
to fail; exit 0 only if every mutant is caught.

The Kvaser premise (tester, 2026-11-13; the charge-interposer reviewer's
D6): vehicle_sim's "VOID: kvaser TX stall ..." or "VOID: kvaser link ..."
line (kvaser_link) makes a case NOT EXERCISED through evaluate(), its line
(the kernel's USB/CAN lines included) in the reason; three more mutants
(the line never read, a link VOID not read, the premise ignored by the
verdict).

The PCAN diag witness's premise (tester, 2026-11-19; the charge-interposer
reviewer's PCAN ruling 1): a witness whose reader died makes the case NOT
EXERCISED with its error (run_scenario.witness_outcome), where it was an
8.2 FAIL; four more mutants (the old FAIL, the dead witness's rows scored,
the premise dropped by evaluate, the premise ignored by the verdict).

The diag witness on can-tools' canbench.reader_guard (tester, 2026-11-23;
the reviewer's reader_guard ruling): DiagWitness reads with read_loop,
drain=False, its error text "MissedFrames: ..." on an overrun row; a
bus-error row with a BMS id is not logged; stop() returns at once with
frames still queued. Mutants: blind to an overrun row (on_missed="mark"),
the recv unguarded, draining after stop, and bus-error rows logged.
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bench_suite as B                                   # noqa: E402
import run_scenario as R                                  # noqa: E402

FORCED = ("charger-fault-stop-flow-first", "fault-during-override-flow-first")


def _witness(flag_s, drop_s):
    """PCAN witness rows (host, device s, id, data) as read_witness() gives
    them: the VCU's page 00 with flow 1, then the flagged mux-3 page (run
    0d0d's own bytes) and the page 00 with flow 0 at the given times."""
    import protocol as P
    t0 = min(flag_s, drop_s) - 1.0
    rows = [(t0, t0, 0x18EFC000, bytes(P.enc_master(1, 3))),
            (t0 + 0.1, t0 + 0.1, 0x18FFD4C0, bytes.fromhex("0301000000000000")),
            (flag_s, flag_s, 0x18FFD4C0, bytes.fromhex("0301010000000000")),
            (drop_s, drop_s, 0x18EFC000, bytes(P.enc_master(0, 3)))]
    return R.witness_fault_flow_order(rows)


def test_forced_order_premise():
    """The premise on the witness (a board) and on the trace (emulation),
    and verdict_of: a premise that did not hold is NOT EXERCISED."""
    p = R.forced_order("flow")
    assert p == ("forced_order", {"first": "flow", "min_gap_s": 0.15}), p
    # run 0d0d's own figures: the flag first by 152.8 ms -> not held
    ok, why = R._forced_order(_witness(388010.659, 388010.812), "flow", 0.15)
    assert not ok and "the flag first by 153.0 ms" in why, why
    # 6019's: the flow drop first by 226.6 ms -> held
    ok, why = R._forced_order(_witness(1714012.006, 1714011.780), "flow", 0.15)
    assert ok and "the flow drop first by 226.0 ms" in why, why
    # flow first, but by less than the 150 ms the case forces -> not held
    ok, why = R._forced_order(_witness(10.100, 10.000), "flow", 0.15)
    assert not ok and "by at least 150 ms" in why, why
    # the core's trace in emulation, (t_flag, t_drop) ms
    assert R._forced_order((5300.0, 5000.0), "flow", 0.15)[0]
    assert not R._forced_order((5000.0, 5300.0), "flow", 0.15)[0]
    assert R._forced_order((5000.0, 5300.0), "trip", 0.15)[0]
    # nothing to read the order from, or an input missing
    assert not R._forced_order(None, "flow", 0.15)[0]
    assert not R._forced_order((None, 5000.0), "flow", 0.15)[0]
    assert not R._forced_order(dict(t_flag=None, t_drop=1.0, source="w"), "flow", 0.15)[0]
    # the verdict: a premise that did not hold is NOT EXERCISED, whatever
    # the results; without premises, PASS / FAIL as before
    bad, good = [(False, "premise, x")], [(True, "premise, x")]
    assert R.verdict_of(dict(results=[(True, "a")], premises=bad))[0] == "NOT EXERCISED"
    assert R.verdict_of(dict(results=[(False, "a")], premises=bad))[0] == "NOT EXERCISED"
    assert R.verdict_of(dict(results=[(True, "a")], premises=good)) == ("PASS", "")
    assert R.verdict_of(dict(results=[(False, "a")], premises=good)) == ("FAIL", "")
    assert R.verdict_of(dict(results=[(True, "a")])) == ("PASS", "")
    assert R.verdict_of(dict(results=[(False, "a")], premises=[])) == ("FAIL", "")
    # the two forced cases carry the premise first, and no other case does
    for s in R.SCENARIOS:
        has = [k for k, _v in s.expect if k == "forced_order"]
        if s.name in FORCED:
            assert s.expect[0] == R.forced_order("flow"), (s.name, s.expect[0])
        else:
            assert not has, s.name
    # the phase option moves --fault-at only, and refuses a case without one
    sc = R.BY_NAME[FORCED[1]]
    a = R.charger_argv(sc, 0.13)
    assert a[a.index("--fault-at") + 1] == "2500.130" and len(a) == len(sc.charger), a
    assert R.charger_argv(sc, 0.0) == list(sc.charger)
    try:
        R.charger_argv(R.BY_NAME["normal-full"], 0.13)
        raise AssertionError("normal-full has no --fault-at, and was not refused")
    except ValueError:
        pass
    # the bench suite runs both forced cases at offset 0 and at the two
    # other phases, through this option
    assert tuple(B.FORCED_ORDER_SCENARIOS) == FORCED
    assert B.PHASE_OFFSETS_S == (0.13, 0.27)


def test_kvaser_premise():
    """vehicle_sim's Kvaser VOID line (kvaser_link: a TX stall or a link
    not set) makes the case NOT EXERCISED, its line in the reason; a log
    without one, or with an unrelated VOID, leaves the verdict alone."""
    from pathlib import Path
    import scratch_dir
    stall = ("12:00:00 veh VOID: kvaser TX stall: no own echo 120 ms after send returned "
             "at 12:00:01.000; kernel USB/CAN lines 1 in the window 12:00:00..12:00:02: "
             "2026-11-13T12:00:01.5 usb 1-3.2.1: error -71\n")
    link = "12:00:00 veh VOID: kvaser link: the kvaser link reads down in sysfs\n"
    with scratch_dir.run("forced-order-test") as d:
        d = Path(d)
        logs = [d / "v.log", d / "c.log", d / "i.log"]
        for p in logs[1:]:
            p.write_text("")
        for text, want in ((stall, "kvaser TX stall"), (link, "kvaser link")):
            logs[0].write_text("12:00:00 veh started\n" + text)
            res = R.evaluate(R.BY_NAME["normal-full"], *logs, False)
            v, why = R.verdict_of(res)
            assert v == "NOT EXERCISED" and want in why, (v, why)
            if want == "kvaser TX stall":
                assert "error -71" in why, why
        for text in ("12:00:00 veh started\n", "12:00:00 veh VOID: something else\n"):
            logs[0].write_text(text)
            res = R.evaluate(R.BY_NAME["normal-full"], *logs, False)
            assert R.verdict_of(res)[0] != "NOT EXERCISED", res["premises"]
    assert R.kvaser_void("x VOID: kvaser TX stall: y") == "kvaser TX stall: y"
    assert R.kvaser_void("x VOID: kvaser linked") is None


def test_witness_premise():
    """The PCAN diag witness's reader died (the charge-interposer reviewer's
    PCAN ruling 1, 2026-11-19): the case is NOT EXERCISED with its error,
    never an 8.2 FAIL (it was "8.2: the witness failed", a FAIL); a live
    witness is scored as before. DiagWitness keeps the error's type."""
    import inspect
    import time
    import types
    from pathlib import Path
    import can
    import adapter_roles
    import diag_cadence
    import scratch_dir
    sys.path.insert(0, os.path.join(HERE, "bench_drivers"))
    import reader_death
    called = []

    def score():
        called.append(1)
        return (True, "8.2: planted")
    err = "RuntimeError: A PCAN status 0x40000 BUSPASSIVE"
    assert R.witness_outcome(None, score) == (None, None) and not called
    d82, void = R.witness_outcome(types.SimpleNamespace(error=err), score)
    assert d82 is None and void and err in void and "not judged" in void, (d82, void)
    assert not called, "a dead witness's rows were scored"
    assert R.witness_outcome(types.SimpleNamespace(error=None), score) == (
        (True, "8.2: planted"), None) and called
    src = inspect.getsource(R.run_one)
    assert "diag82, witness_void = witness_outcome(witness, score82)" in src
    assert "witness_void=witness_void," in src, "run_one does not pass it on"
    # schema 5 (2026-11-24): misfits= follows it in the same call
    assert "board_start=board_start," in src, "run_one does not pass the start premise on"
    assert "the witness failed" not in src, "run_one still FAILS 8.2 on it"
    with scratch_dir.run("forced-order-witness") as d:
        d = Path(d)
        logs = [d / "v.log", d / "c.log", d / "i.log"]
        for p in logs:
            p.write_text("")
        res = R.evaluate(R.BY_NAME["normal-full"], *logs, False, witness_void=void)
        v, why = R.verdict_of(res)
        assert v == "NOT EXERCISED" and err in why, (v, why)
        assert not any(w.startswith("spec 8.2") for _ok, w in res["results"])
        res = R.evaluate(R.BY_NAME["normal-full"], *logs, False)
        assert R.verdict_of(res)[0] != "NOT EXERCISED", res["premises"]
        # the old form, for contrast: an 8.2 FAIL line is a FAIL of the case
        res = R.evaluate(R.BY_NAME["normal-full"], *logs, False,
                         diag82=(False, "8.2: the witness failed: %s" % err))
        assert R.verdict_of(res)[0] == "FAIL"
        # DiagWitness on a stand-in bus that raises: the error with its type
        saved = adapter_roles.pcanfd_report
        try:
            adapter_roles.pcanfd_report = lambda *a, **k: reader_death.RaisingBus(
                [], RuntimeError("A PCAN status 0x40000 BUSPASSIVE"))
            w = diag_cadence.DiagWitness(str(d / "w.txt")).start()
            w._t.join(2.0)
            w.stop()
            # the reviewer's PCAN item 2 (2026-11-20): the PCAN opened with bus
            # states reported; a state row is not logged and an overrun row
            # ends the reader with MissedFrames (canbench.reader_guard since
            # 2026-11-23; LostFrames before): NOT EXERCISED, never scored
            Rw = reader_death.report_rows()
            adapter_roles.pcanfd_report = lambda *a, **k: reader_death.report_bus(
                [Rw["passive"], Rw["ack"], Rw["overrun"]], delay=0.05)
            w2 = diag_cadence.DiagWitness(str(d / "w2.txt")).start()
            w2._t.join(2.0)
            w2.stop()
            # 2026-11-23, the reviewer's reader_guard ruling: a bus-error row
            # is not logged, whatever its id (reader_guard.screen keeps bus
            # errors as rows; the witness drops them, as its own loop did):
            # an error frame with a BMS id, a bus error off CanbenchPcanBus
            ef = can.Message(arbitration_id=0x420, is_error_frame=True,
                             is_extended_id=False, data=bytes(8))
            adapter_roles.pcanfd_report = lambda *a, **k: reader_death.RaisingBus(
                [ef], RuntimeError("A PCAN status 0x40000 BUSPASSIVE"))
            w4 = diag_cadence.DiagWitness(str(d / "w4.txt")).start()
            w4._t.join(2.0)
            w4.stop()
            # ... and drain=False: the witness stops at the first read after
            # stop, frames still queued then left unread (it never drained)
            many = reader_death.report_bus([Rw["frame"]] * 3000, delay=0.002)
            adapter_roles.pcanfd_report = lambda *a, **k: many
            w3 = diag_cadence.DiagWitness(str(d / "w3.txt")).start()
            time.sleep(0.1)
            t_stop = time.monotonic()
            w3.stop()
            took = time.monotonic() - t_stop
        finally:
            adapter_roles.pcanfd_report = saved
        assert w.error == err, w.error
        assert w2.error and w2.error.startswith("MissedFrames:") and w2.n == 0, (w2.error, w2.n)
        assert "reports frames lost" in w2.error, w2.error
        d82, void2 = R.witness_outcome(w2, score)
        assert d82 is None and "MissedFrames" in void2 and "lost frames" in void2, void2
        assert w4.n == 0 and w4.error and "BUSPASSIVE" in w4.error, (w4.n, w4.error)
        assert took < 1.5 and w3.error is None and w3.n > 0 and len(many.left) > 2000, \
            (took, w3.error, w3.n, len(many.left))


TESTS = [test_forced_order_premise, test_kvaser_premise, test_witness_premise]


# ---- can-fail -------------------------------------------------------------
def _premise_always(order, first, min_gap_s):
    return (True, "held")


def _premise_no_gap(order, first, min_gap_s):
    return _ORIG_PREMISE(order, first, 0.0)


def _verdict_no_premises(res):
    return ("PASS" if all(r[0] for r in res["results"]) else "FAIL", "")


def _third_premise():
    import copy
    scs = copy.deepcopy(_ORIG_SCEN)
    s = next(x for x in scs if x.name == "fault-during-override")
    s.expect = [R.forced_order("trip")] + list(s.expect)
    return scs


_ORIG_PREMISE = R._forced_order
_ORIG_VERDICT = R.verdict_of
_ORIG_SCEN = R.SCENARIOS
_ORIG_KV = R.kvaser_void
_ORIG_WO = R.witness_outcome
_ORIG_EVAL = R.evaluate


def _outcome_as_before(witness, score):
    """witness_outcome as it was before 2026-11-19: a dead witness an 8.2 FAIL."""
    if witness is None:
        return None, None
    if witness.error:
        return (False, "8.2: the witness failed: %s" % witness.error), None
    return score(), None


def _outcome_scores_dead(witness, score):
    """A dead witness's rows scored as well as voided."""
    d82, void = _ORIG_WO(witness, score)
    return (score() if witness is not None else d82), void


def _blind_witness():
    """DiagWitness that never stops on frames lost: reader_guard's screen
    marks a missed-frames row instead of raising (until 2026-11-23 this
    mutant blinded the witness's own adapter_roles.missed)."""
    class Blind(_ORIG_DW):
        def _run(self):
            self.rg.read_loop(self.bus, self.rec, self._stop, self._row,
                              read_s=0.2, on_missed="mark", drain=False)
    return Blind


def _unguarded_witness():
    """DiagWitness whose loop keeps no raise: screened, but a recv that
    raises ends the thread unrecorded (its hand copy kept it from
    2026-11-19; reader_guard's loop since 2026-11-23)."""
    class Unguarded(_ORIG_DW):
        def _run(self):
            while not self._stop.is_set():
                m = self.rg.screen(self.bus, self.bus.recv(0.2), self.rec)
                if m is not None:
                    self._row(m)
    return Unguarded


def _draining_witness():
    """DiagWitness that drains after stop (reader_guard's default), not
    drain=False (the reviewer's reader_guard ruling, 2026-11-23)."""
    class Draining(_ORIG_DW):
        def _run(self):
            self.rg.read_loop(self.bus, self.rec, self._stop, self._row,
                              read_s=0.2, drain=True)
    return Draining


def _logs_bus_errors():
    """DiagWitness that logs a bus-error row screen keeps (its own loop
    dropped every error frame)."""
    import types

    class Logs(_ORIG_DW):
        def _row(self, m):
            m = types.SimpleNamespace(is_error_frame=False, arbitration_id=m.arbitration_id,
                                      is_extended_id=m.is_extended_id,
                                      timestamp=m.timestamp, data=m.data)
            _ORIG_DW._row(self, m)
    return Logs


import diag_cadence as _DC                                  # noqa: E402
_ORIG_DW = _DC.DiagWitness


MUTANTS = {
    "test_forced_order_premise": [
        ("_forced_order", lambda: _premise_always, "the premise always held"),
        ("_forced_order", lambda: _premise_no_gap, "the order alone, no 150 ms gap"),
        ("verdict_of", lambda: _verdict_no_premises,
         "a premise that did not hold ignored by the verdict"),
        ("SCENARIOS", _third_premise, "fault-during-override given a premise")],
    "test_kvaser_premise": [
        ("kvaser_void", lambda: (lambda text: None), "the Kvaser's VOID line never read"),
        ("kvaser_void", lambda: (lambda text: _ORIG_KV(text) if text and "stall" in text
                                 else None), "a link VOID not read"),
        ("verdict_of", lambda: _verdict_no_premises,
         "a Kvaser premise ignored by the verdict")],
    "test_witness_premise": [
        ("witness_outcome", lambda: _outcome_as_before,
         "a dead witness an 8.2 FAIL, as before 2026-11-19"),
        ("witness_outcome", lambda: _outcome_scores_dead,
         "a dead witness's rows scored"),
        ("evaluate", lambda: (lambda *a, **k: _ORIG_EVAL(*a, **dict(k, witness_void=None))),
         "the witness premise dropped by evaluate"),
        ("verdict_of", lambda: _verdict_no_premises,
         "a witness premise ignored by the verdict"),
        ("D:DiagWitness", _blind_witness,
         "the diag witness blind to an overrun row (PCAN item 2)"),
        # 2026-11-23, the reviewer's reader_guard ruling
        ("D:DiagWitness", _unguarded_witness,
         "the diag witness's recv unguarded (a raise not kept)"),
        ("D:DiagWitness", _draining_witness,
         "the diag witness drains after stop (drain=False not passed)"),
        ("D:DiagWitness", _logs_bus_errors,
         "the diag witness logs a bus-error row reader_guard.screen keeps")],
}


def main():
    can_fail = "--can-fail" in sys.argv[1:]
    bad = n = 0
    for t in TESTS:
        if not can_fail:
            n += 1
            try:
                t()
                print("ok    %s" % t.__name__)
            except Exception:
                bad += 1
                print("FAIL  %s\n%s" % (t.__name__, traceback.format_exc()[-900:]))
            continue
        for name, make, what in MUTANTS[t.__name__]:
            n += 1
            if name.startswith("D:"):
                setattr(_DC, name[2:], make())         # a diag_cadence name
            else:
                setattr(R, name, make())
            try:
                t()
                bad += 1
                print("FAIL  %s survived: %s" % (t.__name__, what))
            except Exception as e:
                print("ok    %s caught: %s (%s: %s)" % (
                    t.__name__, what, type(e).__name__, str(e)[:100]))
            finally:
                R._forced_order = _ORIG_PREMISE
                R.verdict_of = _ORIG_VERDICT
                R.SCENARIOS = _ORIG_SCEN
                R.kvaser_void = _ORIG_KV
                R.witness_outcome = _ORIG_WO
                R.evaluate = _ORIG_EVAL
                _DC.DiagWitness = _ORIG_DW
    print("\n%d of %d %s" % (n - bad, n, "mutants caught" if can_fail else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
