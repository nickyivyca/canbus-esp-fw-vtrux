"""The forced-order range's scope (tester, 2026-11-07; the charge-interposer
reviewer's condition for port step 2a's re-run): against BASE, the
harness commit run 2a 0d0d's bench results stand on for every other case,

  - every run_scenario scenario but the two forced-order cases has the same
    name, description, sim arguments, timeout and expectations as at BASE;
  - the two differ only in their description, their --fault-flag-lead-s
    (-0.5 at BASE, run_scenario.FLOW_FIRST_LEAD_S now) and one
    forced_order("flow") premise put first in their expectations;
  - bench_suite.scenario_judge gives the same verdict as BASE's on every
    output form BASE's could judge, and VOID only on "-> NOT EXERCISED",
    which no BASE scenario printed.

BASE's files are read from git (git show), so this runs in a clone with
history: the local harness lane.

FOR PORT STEP 2a'S RE-RUN ONLY. It names c15f8c05, the harness 0d0d's
other bench results stand on, so once 2a is accepted it leaves the harness
suite (bench_suite.HARNESS_LOCAL) or is re-based on the commit the next
acceptance stands on (the charge-interposer reviewer, 2026-11-07).

    py -3.14 -B charge-interposer/scenario_scope_test.py [--can-fail]

--can-fail runs each test against a mutant (another scenario changed; a
third case given the premise; scenario_judge changed on an old form) and
requires it to fail; exit 0 only if every mutant is caught.
"""

import copy
import os
import subprocess
import sys
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bench_suite as B                                   # noqa: E402
import run_scenario as R                                  # noqa: E402
from vtrux_bench.run import Result, Verdict               # noqa: E402
from vtrux_bench.run.model import CheckRun                # noqa: E402

BASE = "c15f8c0527d4211bb819046518d010fa627fec88"
FORCED = ("charger-fault-stop-flow-first", "fault-during-override-flow-first")


def _git_show(rel):
    top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return subprocess.run(["git", "-C", top, "show", "%s:%s" % (BASE, rel)],
                          capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def _base_module(rel, name):
    """BASE's copy of a module of this folder, executed beside the current
    ones (its imports are this folder's)."""
    m = types.ModuleType(name)
    m.__file__ = os.path.join(HERE, os.path.basename(rel))
    sys.modules[name] = m
    exec(compile(_git_show(rel), "%s@%s" % (rel, BASE[:8]), "exec"), m.__dict__)
    return m


def _shape(sc):
    return dict(name=sc.name, why=sc.why, vehicle=list(sc.vehicle),
                charger=list(sc.charger), interposer=list(sc.interposer),
                timeout=sc.timeout, board_reset_at_s=sc.board_reset_at_s,
                expect=repr(list(sc.expect)))


_BASE_RS = None


def _base_rs():
    global _BASE_RS
    if _BASE_RS is None:
        _BASE_RS = _base_module("charge-interposer/run_scenario.py", "_rs_base")
    return _BASE_RS


def test_scenarios():
    old = {s.name: s for s in _base_rs().SCENARIOS}
    new = {s.name: s for s in R.SCENARIOS}
    assert set(old) == set(new), "scenarios added or removed: %s" % sorted(
        set(old) ^ set(new))
    for n in sorted(new):
        a, b = _shape(old[n]), _shape(new[n])
        if n not in FORCED:
            assert a == b, "%s changed: %s" % (
                n, [k for k in a if a[k] != b[k]])
            assert not any(k == "forced_order" for k, _v in new[n].expect), n
            continue
        # the forced cases: the lead, the premise first, the description
        oc, nc = list(old[n].charger), list(new[n].charger)
        i = oc.index("--fault-flag-lead-s")
        assert oc[i + 1] == "-0.5", (n, oc)
        assert nc[:i + 1] == oc[:i + 1] and nc[i + 2:] == oc[i + 2:], (n, nc)
        assert float(nc[i + 1]) == R.FLOW_FIRST_LEAD_S, (n, nc)
        assert new[n].expect[0] == R.forced_order("flow"), (n, new[n].expect[0])
        assert repr(list(new[n].expect[1:])) == a["expect"], \
            "%s: expectations other than the premise changed" % n
        for k in ("vehicle", "interposer", "timeout", "board_reset_at_s"):
            assert a[k] == b[k], "%s: %s changed" % (n, k)


class _C:
    id = "c"


def _run(log, rc, tmp):
    p = os.path.join(tmp, "check_%d.log" % _run.n)
    _run.n += 1
    with open(p, "w", encoding="utf-8") as f:
        f.write(log)
    return CheckRun(check=_C(), full_id="g/c", rc=rc, timed_out=False,
                    log_path=p, outdir=tmp, duration_s=1.0, ctx={})


_run.n = 0

# the output forms run_scenario printed before the range, and the new one
OLD_FORMS = [
    ("   -> PASS  (12 s wall, logs in x)\n\nALL SCENARIOS PASSED\n", 0),
    ("   -> PASS  (12 s wall, logs in x)\n", 1),
    ("   [FAIL] y\n   -> FAIL  (12 s wall, logs in x)\n\nSOME SCENARIOS FAILED\n", 1),
    ("   [FAIL] P3a, the simulation ...\n   -> FAIL  (the simulation does not "
     "match the truck's holds, so the core is not judged)\n", 1),
    ("   -> FAIL  (a)\n   -> PASS  (b)\n", 0),
    ("   -> PASS  (a)\n   -> FAIL  (b)\n", 1),
    ("FAIL  x\nVOID  y\n   -> FAIL  (z)\n", 1),
    ("Traceback (most recent call last):\n", 1),
    ("", 0),
]
NEW_FORM = ("   [NOT EXERCISED] premise, the forced order: ...\n   -> NOT EXERCISED  "
            "(premise, the forced order: the case forces the flow drop first by at "
            "least 150 ms, but ...; 2543 s wall, logs in x)\n\nSOME SCENARIOS FAILED\n", 1)


def _base_judge():
    """BASE's scenario_judge, from BASE's bench_suite.py source, run with
    this bench_suite's helpers (the same _lines, Result, Verdict)."""
    src = _git_show("charge-interposer/bench_suite.py")
    start = src.index("def scenario_judge(run):")
    end = src.index("\n\n\n", start)
    ns = {"_lines": B._lines, "Result": Result, "Verdict": Verdict}
    exec(compile(src[start:end], "scenario_judge@%s" % BASE[:8], "exec"), ns)
    return ns["scenario_judge"]


def test_scenario_judge_scope():
    import scratch_dir
    old = _base_judge()
    with scratch_dir.run("scenario-scope-test") as tmp:
        for log, rc in OLD_FORMS:
            a, b = old(_run(log, rc, tmp)), B.scenario_judge(_run(log, rc, tmp))
            assert a.verdict == b.verdict, (log[:60], rc, a.verdict, b.verdict)
        r = B.scenario_judge(_run(NEW_FORM[0], NEW_FORM[1], tmp))
        assert r.verdict == Verdict.VOID and r.detail.startswith(
            "NOT EXERCISED: premise, the forced order: the case forces"), (
                r.verdict, r.detail)
        assert old(_run(NEW_FORM[0], NEW_FORM[1], tmp)).verdict == Verdict.ERROR, \
            "BASE's judge had no form for it"
    # no BASE scenario could print it: none had a forced_order premise
    assert not any(k == "forced_order" for s in _base_rs().SCENARIOS
                   for k, _v in s.expect)


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


TESTS = [test_scenarios, test_scenario_judge_scope, test_forced_order_premise]


# ---- can-fail -------------------------------------------------------------
def _other_changed():
    scs = copy.deepcopy(R.SCENARIOS)
    s = next(x for x in scs if x.name == "charger-fault-stop")
    s.charger = list(s.charger) + ["--fault-flag-lead-s", "-0.2"]
    return scs


def _third_premise():
    scs = copy.deepcopy(R.SCENARIOS)
    s = next(x for x in scs if x.name == "fault-during-override")
    s.expect = [R.forced_order("trip")] + list(s.expect)
    return scs


def _forced_more_changed():
    scs = copy.deepcopy(R.SCENARIOS)
    s = next(x for x in scs if x.name == FORCED[1])
    s.timeout = s.timeout + 60
    return scs


def _judge_rc_ignored(run):
    r = _ORIG_JUDGE(run)
    if r.verdict == Verdict.FAIL and "-> PASS" in run.log_text():
        r = Result(Verdict.PASS, detail=r.detail)
    return r


def _judge_not_exercised_fails(run):
    r = _ORIG_JUDGE(run)
    return Result(Verdict.FAIL, detail=r.detail) if r.verdict == Verdict.VOID else r


def _premise_always(order, first, min_gap_s):
    return (True, "held")


def _premise_no_gap(order, first, min_gap_s):
    return _ORIG_PREMISE(order, first, 0.0)


def _verdict_no_premises(res):
    return ("PASS" if all(r[0] for r in res["results"]) else "FAIL", "")


_ORIG_SCEN = R.SCENARIOS
_ORIG_JUDGE = B.scenario_judge
_ORIG_PREMISE = R._forced_order
_ORIG_VERDICT = R.verdict_of
MUTANTS = {
    "test_scenarios": [
        ("R.SCENARIOS", _other_changed, "charger-fault-stop given a lead"),
        ("R.SCENARIOS", _third_premise, "fault-during-override given a premise"),
        ("R.SCENARIOS", _forced_more_changed, "a forced case's timeout changed")],
    "test_scenario_judge_scope": [
        ("B.scenario_judge", lambda: _judge_rc_ignored, "-> PASS with exit 1 passes"),
        ("B.scenario_judge", lambda: _judge_not_exercised_fails,
         "NOT EXERCISED judged FAIL")],
    "test_forced_order_premise": [
        ("R._forced_order", lambda: _premise_always, "the premise always held"),
        ("R._forced_order", lambda: _premise_no_gap,
         "the order alone, no 150 ms gap"),
        ("R.verdict_of", lambda: _verdict_no_premises,
         "a premise that did not hold ignored by the verdict")],
}


def _set(attr, value):
    mod, name = attr.split(".", 1)
    setattr({"R": R, "B": B}[mod], name, value)


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
        for attr, make, what in MUTANTS[t.__name__]:
            n += 1
            _set(attr, make())
            try:
                t()
                bad += 1
                print("FAIL  %s survived: %s" % (t.__name__, what))
            except Exception as e:
                print("ok    %s caught: %s (%s: %s)" % (
                    t.__name__, what, type(e).__name__, str(e)[:100]))
            finally:
                _set("R.SCENARIOS", _ORIG_SCEN)
                _set("B.scenario_judge", _ORIG_JUDGE)
                _set("R._forced_order", _ORIG_PREMISE)
                _set("R.verdict_of", _ORIG_VERDICT)
    print("\n%d of %d %s" % (n - bad, n, "mutants caught" if can_fail else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
