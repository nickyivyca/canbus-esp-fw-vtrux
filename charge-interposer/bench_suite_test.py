"""Tests the HARNESS: bench_suite.py's judges and the shape of its three
suites (tester, 2026-10-18). No hardware, no runner, no ssh.

  judges   each judge on synthetic output in the format its script prints
           (taken from the 2026-10-18 trial runs), driven to every verdict
           it can give
  suites   emulated / bench / harness validate; bench requires emulated;
           no hardware or lock outside bench; no harness test (mutation,
           can-fail, self-test) in emulated or bench; no wall-clock check
           in harness; no check id in two suites; every known FAIL names a
           check of its own suite; every skip names its spec item or ruling
  pycache  no __pycache__ in charge-interposer/ or shared/bench/ makes the
           clone dirty (a dirty clone cannot be resumed)
  stage    the build-check bundle is made in a scratch_dir run folder of
           this process under runner-stage/, once per commit, and
           registered for removal at exit; a killed run's folder is
           pruned; never the system temp folder (reviewer, 2026-10-31)
  record   the bench run records the can-tools pin (repo, commit), and the
           folder and clone once the folder passes the pin's checks, or the
           refusal (a hand-edited folder, none installed, CAN_TOOLS_DIR
           unset) with the pin still named; the emulated and harness
           suites, which open no dongle, do not
  m-lanes  settings m_lanes_host: unset (the Windows settings) keeps the
           m-* groups on madhouse over ssh from the shipped copy, exactly
           as before; "local" (madhouse-debian, user 2026-11-11) puts them
           on host local from the worktree, nothing shipped and no {work}
           path left, with the same checks, argv, class and judge; any
           other value refused
  test images  an X2 found on one of shared/x2_test_images' images: the
           jammer is read as no image identify knows, slcan_spi by its
           identity (name, version, ELF hash; V and N must agree, the name
           end _NOT_FOR_VEHICLE), slcan_can1 as its protocol, none as the
           product or any bench image and none raising; the pinned core's
           Engine, with the bench suite's identify hook on a fake board,
           flashes the group's image before its first check, and runs none
           of its checks if the flash does not take (the charge-interposer
           reviewer's item 4, 2026-11-13; slcan_spi's identity, its queue
           after the slcan_can1 move, 2026-11-13)

    py -3.14 charge-interposer/bench_suite_test.py [--can-fail]      (Windows)
    python3.13 charge-interposer/bench_suite_test.py [--can-fail]    (madhouse)

--can-fail runs each test against a mutant of what it tests (a judge that
passes everything, a suite with its requires dropped, ...) and requires the
test to fail; exit 0 only if every mutant is caught.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scratch_dir                                        # noqa: E402
import bench_suite as B                                   # noqa: E402
from vtrux_bench.run import Result, Verdict               # noqa: E402
from vtrux_bench.run.model import CheckRun, validate      # noqa: E402

# this run's scratch_dir folder, made by main() and removed in its finally
# (2026-10-31: before, a fixed folder in the machine temp folder)
TMP = None

# No test here runs the real canctl or reads the real sysfs (canchg_mode.py,
# the bench suite's X2 hooks on Linux): its canctl is a command that changes
# nothing and exits 9, its sysfs roots a folder that does not exist, and the
# tests that drive the hooks stub canchg_mode.start and .end (_canchg_stubs).
B.canchg_mode.CANCTL = [sys.executable, "-B", "-c", "import sys; sys.exit(9)"]
B.canchg_mode.SYSFS_USB = B.canchg_mode.SYSFS_NET = os.path.join(HERE, "no-such-sysfs")
B.canchg_mode.BIND_WAIT_S = 0
# Nor canx2_slcan's (the X2's start hook detaches canx2): the same canctl
# that exits 9, sysfs and /proc roots that do not exist, and the tests that
# drive the hooks stub canx2_slcan.start (inside _canchg_stubs).
B.canx2_slcan.CANCTL = list(B.canchg_mode.CANCTL)
B.canx2_slcan.SYSFS_NET = B.canx2_slcan.PROC = os.path.join(HERE, "no-such-sysfs")
B.canx2_slcan.DETACH_WAIT_S = 0


def _canchg_stubs(calls):
    """canchg_mode.start / .end and canx2_slcan.start replaced by stubs
    recording (step, environ) in calls (canx2's as ("canx2", {})) and
    returning ok; -> the originals, to put back with _canchg_restore."""
    M = B.canchg_mode
    keep = (M.start, M.end, B.canx2_slcan.start)

    def stub(step):
        def fn(environ, logdir, platform=None):
            calls.append((step, dict(environ)))
            return dict(ok=True, detail="canchg %s stub" % step, found={})
        return fn

    def canx2(logdir, platform=None):
        calls.append(("canx2", {}))
        return dict(ok=True, detail="canx2 stub", found={})
    M.start, M.end = stub("start"), stub("end")
    B.canx2_slcan.start = canx2
    return keep


def _canchg_restore(keep):
    B.canchg_mode.start, B.canchg_mode.end, B.canx2_slcan.start = keep


class _Chk:
    def __init__(self, cid):
        self.id = cid


def _run(log, rc=0, cid="c", files=None, ctx=None):
    """A CheckRun with `log` as its output and `files` in its outdir."""
    d = os.path.join(TMP, "run_%d" % _run.n)
    _run.n += 1
    scratch_dir.remove(d)
    os.makedirs(d)
    lp = os.path.join(d, "check.log")
    with open(lp, "w", encoding="utf-8") as f:
        f.write(log)
    for name, text in (files or {}).items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
    return CheckRun(check=_Chk(cid), full_id="g/" + cid, rc=rc,
                    timed_out=False, log_path=lp, outdir=d, duration_s=1.0,
                    ctx=ctx or {})


_run.n = 0


def _v(r):
    rs = r if isinstance(r, list) else [r]
    return [x.verdict for x in rs]


def _by(rs):
    return {x.sub: x.verdict for x in rs}


# ---- judges -------------------------------------------------------------
def test_script_judge():
    j = B.script_judge
    assert _v(j(_run("all ok\n", 0))) == [Verdict.PASS]
    assert _v(j(_run("FAIL  the thing\n", 1))) == [Verdict.FAIL]
    assert _v(j(_run("VOID  premise not met\n", 1))) == [Verdict.VOID]
    assert _v(j(_run("VOID  a\nFAIL  b\n", 1))) == [Verdict.FAIL], \
        "a FAIL beside a VOID is a FAIL"
    assert _v(j(_run("", 3))) == [Verdict.FAIL], "rc 3, no lines: FAIL"
    assert _v(j(_run("", None))) == [Verdict.ERROR], "killed: ERROR"
    noread = ("noread_ack_01: premise NOT met\n"
              "  VOID noread_ack_01: VOID: no hole in the board's t= sequence\n"
              "  VOID noread_ack_02: VOID: no hole in the board's t= sequence\n")
    assert _v(j(_run(noread, 0))) == [Verdict.VOID], \
        "rc 0 with every verdict VOID is VOID, not PASS (a25e noread-boots)"
    assert _v(j(_run(noread, 3))) == [Verdict.VOID], \
        "bench_usb_host's rc 3 for an all-VOID case is VOID"
    assert _v(j(_run(noread + "  PASS noread_ack_03: boot window 12209 us\n",
                     0))) == [Verdict.PASS], "a VOID beside a PASS stays PASS"
    # bench_vport's TEST FAULT rule (reviewer, 2026-10-23, c40b): a run whose
    # only bad arm faulted ends on a VOID line, and that arm's scorer lines
    # are marked "not judged", so the suite reads it VOID; a valid FAIL
    # beside the fault ends on a FAIL line and stays FAIL
    vport = ("  1M, idle: TEST FAULT: 1M idle: the witness's receive failed "
             "1249 time(s) in the window (queue overflow)\n"
             "    not judged (the window faulted): FAIL: 66 error frame(s)\n"
             "== verdicts\n"
             "  bitrate/1M/idle              VOID (TEST FAULT)\n"
             "VOID: TEST FAULT on bitrate/1M/idle, and no arm FAILs on a "
             "valid measurement\n")
    assert _v(j(_run(vport, 1))) == [Verdict.VOID], \
        "bench_vport's fault-only run is VOID"
    assert _v(j(_run(vport.replace(
        "VOID: TEST FAULT on bitrate/1M/idle, and no arm FAILs on a valid "
        "measurement", "FAIL: flood/7F4; TEST FAULT, not measured: "
        "bitrate/1M/idle"), 1))) == [Verdict.FAIL], \
        "bench_vport's fault beside a valid FAIL is FAIL"


def test_recorded_judge():
    j = B.recorded_judge
    r = j(_run("measured 12\n", 0))
    assert _v(r) == [Verdict.PASS] and "recorded" in r.detail
    assert _v(j(_run("FAIL  x\n", 1))) == [Verdict.FAIL]


def test_scenario_judge():
    j = B.scenario_judge
    assert _v(j(_run("...\n  -> PASS\n", 0))) == [Verdict.PASS]
    assert _v(j(_run("  -> PASS\n", 1))) == [Verdict.FAIL], \
        "-> PASS with a non-zero exit is not a pass"
    assert _v(j(_run("FAIL  x\n  -> FAIL\n", 1))) == [Verdict.FAIL]
    assert _v(j(_run("  -> FAIL\n  -> PASS\n", 0))) == [Verdict.PASS], \
        "the LAST verdict line counts"
    assert _v(j(_run("  -> PASS\n  -> FAIL\n", 0))) == [Verdict.FAIL]
    assert _v(j(_run("no verdict\n", 0))) == [Verdict.ERROR]
    # a forced_order premise that did not hold: NOT EXERCISED, VOID
    ne = j(_run("   [NOT EXERCISED] premise, the forced order: x\n"
                "   -> NOT EXERCISED  (premise: x; 9 s wall, logs in y)\n", 1))
    assert _v(ne) == [Verdict.VOID] and ne.detail.startswith(
        "NOT EXERCISED: premise: x"), (ne.verdict, ne.detail)
    assert _v(j(_run("   -> NOT EXERCISED  (premise: x)\n   -> PASS  (y)\n", 0))) \
        == [Verdict.PASS], "the LAST verdict line counts"


PIN_OK = (
    "unit_monitor_6h05: 525609 steps\n"
    "  FAIL  trace and golden do NOT match their pins\n"
    "unit_override_6h05: 525610 steps\n"
    "  FAIL  trace and golden do NOT match their pins\n"
    "  ok    unit_monitor_6h05 on machine.cpp: IDENTICAL\n"
    "  ok    unit_override_6h05 on machine.cpp: IDENTICAL\n\n2 FAILED\n")


def test_corrupt_pin_judge():
    j = B.corrupt_pin_judge
    assert _v(j(_run(PIN_OK, 1))) == [Verdict.PASS]
    assert _v(j(_run(PIN_OK.replace("  FAIL  trace and golden do NOT match",
                                    "  ok    trace and golden match", 1), 1))) \
        == [Verdict.FAIL], "one corrupted pin not caught"
    assert _v(j(_run(PIN_OK.replace("FAIL  trace and golden do NOT match",
                                    "ok    trace and golden match"), 0))) \
        == [Verdict.FAIL], "neither caught, exit 0"
    assert _v(j(_run(PIN_OK.replace("on machine.cpp: IDENTICAL\n\n",
                                    "on machine.cpp: IDENTICAL\n"
                                    "  FAIL  unit_override_6h05 on machine.cpp:"
                                    " DIFFERS\n\n"), 1))) == [Verdict.FAIL], \
        "a replay that differs as well"
    assert _v(j(_run(PIN_OK, None))) == [Verdict.ERROR]


C12_HEAD = ("C1-reset  CANSTAT  0x80\n\n"
            "--- against the fake (mcp2515_conformance_fake.txt): %d line(s) "
            "differ ---\n")
C12_LINES = [
    "  C4-txflags-on-request    TXB0CTRL.dirtied         fake 0x70       "
    "silicon 0x00       ASSUMED",
    "  C9-no-ack                TEC.at-2ms               fake 64         "
    "silicon (absent)   DEFERRED",
    "  C9-no-ack                wire.delivered           fake (none)     "
    "silicon (absent)   DATASHEET  <-- a DATASHEET line",
]
C12_BAD = ("  C3-loopback              wire.delivered           fake 1          "
           "silicon 0          DATASHEET  <-- a DATASHEET line")


def test_c12_judge():
    j = B.c12_judge
    ok = C12_HEAD % 3 + "\n".join(C12_LINES) + "\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": ok}))
    assert _v(r) == [Verdict.PASS], r
    assert r.figures["lines_differ"].value == 3
    assert r.figures["datasheet_differ_outside_c9"].value == 0
    bad = C12_HEAD % 4 + "\n".join(C12_LINES + [C12_BAD]) + "\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": bad}))
    assert _v(r) == [Verdict.FAIL] and "C3-loopback" in r.detail, r
    # a DATASHEET line after the block's blank line is not in the block
    tail = ok + "\nC3-x  <-- a DATASHEET line\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": tail}))
    assert _v(r) == [Verdict.PASS], r
    r = j(_run("", 1, cid="c12-all"))
    assert _v(r) == [Verdict.ERROR], "no transcript: ERROR"
    r = j(_run("FAIL  x\n", 1, cid="c12-all",
               files={"c12-all.txt": "no block here\n"}))
    assert _v(r) == [Verdict.FAIL], "no differ block: the exit code decides"


def _sp_text(noack=32, ack=0, deltas=None, result=True):
    """bench_switch_pending's out file, as run_bench and phase() write it."""
    d = {"NOACK": {"c2v": 118, "veh_tx": 85, "bridge": 57, "suppressed": 0,
                   "drained": 0, "switches": 2, "veh_err": 24600},
         "ACK": {"c2v": 120, "veh_tx": 0, "bridge": 0, "suppressed": 0,
                 "drained": 0, "switches": 2, "veh_err": 0}}
    for ph, ch in (deltas or {}).items():
        d[ph] = dict(d[ph], **ch) if ch is not None else None
    out = ["run at 2026-11-09T00:00:00; charger frames 0x18FF50C0 at 20 fps"]
    for ph in ("NOACK", "ACK"):
        out.append("phase %s: waiting for a status block (baseline)" % ph)
        out.append("phase %s: 120 charger frames sent; PCAN (passive) saw 1 of them "
                   "complete on the vehicle wire and 0 error frames" % ph)
        out.append("  board counters before {'c2v': 1}")
        out.append("  board counters after  {'c2v': 2}")
        if d[ph] is not None:
            out.append("  deltas %r" % (d[ph],))
        out.append("  UNACCOUNTED (c2v - delivered - veh tx - suppressed - drained; "
                   "bridge= is a subset of tx=): %d" % (noack if ph == "NOACK" else ack))
    if result:
        out.append("RESULT: unaccounted NOACK %d, ACK %d" % (noack, ack))
    return "\n".join(out) + "\n"


def test_switch_pending_judge():
    """switch_pending_judge (port step 2b; the charge-interposer reviewer,
    2026-11-08): PASS with ACK 0 and NOACK in 0..33, each phase's bridge=
    within its veh tx=; FAIL for each of: no RESULT line, ACK not 0, NOACK
    above 33 or below 0, bridge= above veh tx= in a phase, a phase's deltas
    missing; ERROR with no out file or no exit code. The figures carry both
    counts and each phase's four deltas."""
    j = B.switch_pending_judge

    def run(text, rc=0):
        return j(_run("", rc, cid="switch-pending",
                      files={"switch-pending.txt": text} if text is not None else None))
    for noack in (0, 32, 33):
        r = run(_sp_text(noack=noack))
        assert _v(r) == [Verdict.PASS], (noack, r.detail)
    r = run(_sp_text(noack=17))
    assert r.figures["unaccounted_noack"].value == 17
    assert r.figures["unaccounted_ack"].value == 0
    assert r.figures["noack_veh_tx"].value == 85 and r.figures["noack_bridge"].value == 57
    assert r.figures["ack_switches"].value == 2 and r.figures["noack_drained"].value == 0
    for label, text, has in (
            ("no RESULT line", _sp_text(result=False), "RESULT"),
            ("ACK not 0", _sp_text(ack=1), "ACK: 1"),
            ("NOACK above 33", _sp_text(noack=34), "outside 0..33"),
            ("NOACK below 0", _sp_text(noack=-1), "outside 0..33"),
            ("bridge above veh tx in NOACK",
             _sp_text(deltas={"NOACK": {"bridge": 86}}), "NOACK: bridge="),
            ("bridge above veh tx in ACK",
             _sp_text(deltas={"ACK": {"bridge": 1}}), "ACK: bridge="),
            ("a phase's deltas missing", _sp_text(deltas={"ACK": None}),
             "ACK: no deltas")):
        r = run(text)
        assert _v(r) == [Verdict.FAIL] and has in r.detail, (label, r.detail)
    r = run(_sp_text() + "RESULT: unaccounted NOACK 1, ACK 0\n")
    assert _v(r) == [Verdict.FAIL], "two RESULT lines: FAIL"
    assert _v(run(None)) == [Verdict.ERROR], "no out file: ERROR"
    assert _v(run(_sp_text(), rc=None)) == [Verdict.ERROR], "killed: ERROR"
    b = B.bench(_settings())
    sp = [c for g in b.groups for c in g.checks if c.id == "switch-pending"]
    assert len(sp) == 1 and sp[0].judge is B.switch_pending_judge, sp


def _sp_no_result(run):
    """A missing RESULT line taken as 0 and 0."""
    out = os.path.join(run.outdir, "%s.txt" % run.check.id)
    if os.path.exists(out) and "RESULT:" not in open(out, encoding="utf-8").read():
        with open(out, "a", encoding="utf-8") as f:
            f.write("RESULT: unaccounted NOACK 0, ACK 0\n")
    return _ORIG["switch_pending_judge"](run)


def _sp_flip(cond):
    """The judge with one FAIL condition dropped (its detail's marker)."""
    def judge(run):
        r = _ORIG["switch_pending_judge"](run)
        if r.verdict == Verdict.FAIL and all(cond in part for part in r.detail.split("; ")):
            r.verdict = Verdict.PASS
        return r
    return judge


def _hb_listline(names, n=None):
    """run.sh's test-list line for `names` (n: the count it prints)."""
    return "host_bridge tests (%d): %s" % (len(names) if n is None else n,
                                           " ".join(names))


def _hb_close(t, rc):
    """run.sh's closing line for test t, after a blank line as it prints it."""
    return "\nrun.sh: %s exit %d" % (t, rc)


def test_host_bridge_judge():
    """host_bridge_judge on run.sh's own output (follow-up (B), tester,
    2026-11-08): the tests are the list run.sh printed; each listed test
    needs its closing line; the strict reading is ERROR for a missing,
    empty, doubled or miscounted list, an unlisted result and a missing
    result."""
    j = B.host_bridge_judge
    four = ["test_canport_contract", "test_conformance", "test_event_layout",
            "test_l2_bridge"]
    l2 = ["--- l2: c%d ---\ntest_l2_bridge[c%d]: all checks OK" % (i, i)
          for i in range(33)]
    log = "\n".join([
        _hb_listline(four),
        "capture pin: l3_stimulus.txt matches its pin",
        "\ntest_canport_contract: all checks OK", _hb_close("test_canport_contract", 0),
        "\ntest_conformance: all checks OK", _hb_close("test_conformance", 0),
        "test_event_layout DID NOT BUILD"] + l2 + [
        "--- l2: bad ---",
        "  ok  setup",
        "FAIL  it clears bridge_ok (spec 8.2)",
        "1 failure(s) in bad",
        "--- l2: hung ---",
        "  ok  setup", _hb_close("test_l2_bridge", 1)]) + "\n"
    # no settings key at all, as a [[check]] judge gets it (0d0d's
    # KeyError): the list is the run's own, so none is needed
    out = j(_run(log, 1, ctx={}))
    rs = _by(out)
    tl = [x for x in out if x.sub == "test-list"]
    assert len(tl) == 1 and tl[0].verdict == Verdict.PASS, tl
    assert tl[0].detail == ("4 test(s) listed by run.sh: test_canport_contract "
                            "test_conformance test_event_layout test_l2_bridge"), tl[0].detail
    assert rs["capture-pin"] == Verdict.PASS
    assert rs["l2.c0"] == rs["l2.c32"] == Verdict.PASS
    assert rs["l2.bad"] == Verdict.FAIL
    assert rs["l2.hung"] == Verdict.ERROR, "opened, never reported: ERROR"
    assert rs["test_conformance"] == Verdict.PASS
    assert rs["test_canport_contract"] == Verdict.PASS
    assert rs["test_event_layout"] == Verdict.ERROR
    assert "test_l2_bridge" not in rs, "its cases speak for it"
    assert "l2.population" not in rs, "35 cases opened: no population FAIL"
    assert set(rs) == {"test-list", "capture-pin", "test_conformance",
                       "test_canport_contract", "test_event_layout", "l2.bad",
                       "l2.hung"} | {"l2.c%d" % i for i in range(33)}, sorted(rs)
    # a settings key naming another tree changes nothing
    assert _by(j(_run(log, 1, ctx={"interposer_code": "elsewhere"}))) == rs
    # each listed test's closing line decides; a listed test with none is
    # ERROR (a failing test prints no line of its own name, so without the
    # closing line it cannot be told from one that never ran)
    six = ["test_l2_bridge", "test_conformance", "test_new", "test_bad",
           "test_quiet", "test_twice"]
    log2 = "\n".join([
        _hb_listline(six),
        "\ntest_conformance: all checks OK", _hb_close("test_conformance", 0),
        "\n3 failure(s)", _hb_close("test_bad", 1),
        "", _hb_close("test_quiet", 0),
        "\ntest_twice: all checks OK", _hb_close("test_twice", 0),
        _hb_close("test_twice", 0)] + l2[:5] + [_hb_close("test_l2_bridge", 1)]) + "\n"
    out = j(_run(log2, 1))
    rs, det = _by(out), {x.sub: x.detail for x in out}
    assert rs["test_new"] == Verdict.ERROR and det["test_new"].startswith("no result"), out
    assert rs["test_bad"] == Verdict.FAIL and det["test_bad"] == \
        "exit 1, no 'all checks OK' line", det["test_bad"]
    assert rs["test_quiet"] == Verdict.FAIL, "exit 0 without its OK line: FAIL"
    assert rs["test_twice"] == Verdict.ERROR and "2 closing lines" in det["test_twice"]
    assert rs["test_l2_bridge"] == Verdict.FAIL, "exit 1 with every case OK: FAIL"
    assert rs["test_conformance"] == Verdict.PASS
    assert rs["capture-pin"] == Verdict.FAIL
    assert rs["l2.population"] == Verdict.FAIL
    assert rs["test-list"] == Verdict.PASS
    # exit non-zero with its OK line: FAIL
    log2b = "\n".join([_hb_listline(["test_conformance"]),
                       "test_conformance: all checks OK",
                       _hb_close("test_conformance", 2)]) + "\n"
    out = j(_run(log2b, 1))
    assert _by(out)["test_conformance"] == Verdict.FAIL and \
        [x for x in out if x.sub == "test_conformance"][0].detail == \
        "exit 2, its 'all checks OK' line too", out
    # a result line for a test the list does not name: ERROR, each form
    log3 = "\n".join([_hb_listline(["test_conformance"]),
                      "capture pin: l3_stimulus.txt matches its pin",
                      "test_conformance: all checks OK", _hb_close("test_conformance", 0),
                      "test_gone: all checks OK", "test_older DID NOT BUILD",
                      _hb_close("test_other", 0),
                      "--- l2: c0 ---", "test_l2_bridge[c0]: all checks OK"]) + "\n"
    out = j(_run(log3, 0))
    rs = _by(out)
    for t in ("test_gone", "test_older", "test_other", "test_l2_bridge"):
        assert rs.get(t) == Verdict.ERROR, (t, rs)
        assert [x for x in out if x.sub == t][0].detail.startswith("not listed: "), t
    assert rs["test_conformance"] == Verdict.PASS and "l2.population" not in rs, rs
    # the list itself: missing, twice, empty, a name twice, miscounted, not
    # a test name -- each an ERROR, and every result then unlisted
    body = "\n".join(["test_conformance: all checks OK",
                      _hb_close("test_conformance", 0)]) + "\n"
    bad_lists = [
        ("", "no test list: "),
        (_hb_listline(["test_conformance"]) + "\n" + _hb_listline(["test_conformance"]),
         "2 test lists"),
        (_hb_listline([]), "an empty test list"),
        (_hb_listline(["test_conformance", "test_conformance"]), "listed twice: "
         "test_conformance"),
        (_hb_listline(["test_conformance"], n=2), "the list counts 2 but names 1"),
        (_hb_listline(["test_conformance", "*.cpp"]), "not a test name"),
    ]
    for head, why in bad_lists:
        out = j(_run((head + "\n" if head else "") + body, 0))
        rs = _by(out)
        tl = [x for x in out if x.sub == "test-list"][0]
        assert tl.verdict == Verdict.ERROR and tl.detail.startswith(why), (head, tl)
        assert rs["test_conformance"] == Verdict.ERROR, (head, rs)
    # run.sh's own form, as it prints it with no test: the list empty
    assert B._hb_list("host_bridge tests (0): \n")[1].startswith("an empty test list")
    # and run.sh prints the two forms this judge reads (what it prints is
    # twai_fake_option_test's test_list_and_closing, on madhouse)
    with open(os.path.join(HERE, "fw_harness", "host_bridge", "run.sh"),
              encoding="utf-8") as f:
        src = f.read()
    assert src.count('echo "host_bridge tests (${#tests[@]}): ${tests[*]+${tests[*]}}"') == 1
    assert src.count("printf '\\nrun.sh: %s exit %s\\n' \"$name\" \"$rc\"") == 1
    assert B.HB_LIST_RE.match("host_bridge tests (2): test_a test_b") and \
        B.HB_CLOSE_RE.match("run.sh: test_a exit 0")


# ---- suites -------------------------------------------------------------
IMG_SRC = "abc1234"          # the stand-in images' version (git describe)


def _app_bytes(project, sha, version=IMG_SRC, idf_ver="v5.4.2"):
    """An application image head with esp_app_desc_t where ESP-IDF puts it
    (after the 24-byte image header and 8-byte segment header)."""
    h = bytearray(24 + 8 + 256)
    a = 32
    h[a:a + 4] = (0xABCD5432).to_bytes(4, "little")
    for at, s in ((a + 16, version), (a + 48, project), (a + 112, idf_ver)):
        h[at:at + len(s)] = s.encode("ascii")
    h[a + 144:a + 176] = bytes.fromhex(sha)
    return bytes(h)


def _idf_build(images, variant, sha, project=None, version=IMG_SRC,
               idf_ver="v5.4.2", flash_files=None):
    """A stand-in of what goes in images/<src>/<variant>/: the build's
    flasher_args.json and the files it names, at their paths."""
    d = os.path.join(images, IMG_SRC, variant)
    app = "interposer_%s.bin" % variant
    ff = flash_files or {"0x0": "bootloader/bootloader.bin",
                         "0x10000": app,
                         "0x8000": "partition_table/partition-table.bin",
                         "0xe000": "ota_data_initial.bin"}
    for rel in ff.values():
        p = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(_app_bytes(project or "interposer_" + variant, sha,
                               version, idf_ver)
                    if rel == app else ("%s %s" % (variant, rel)).encode())
    with open(os.path.join(d, "flasher_args.json"), "w") as f:
        json.dump({"flash_files": ff,
                   "app": {"offset": "0x10000", "file": app}}, f)
    return d


IMG_SHA = {"bridge": "c7c98adc" + "1" * 56, "witness": "a538710a" + "2" * 56,
           "truck": "5e1f00d0" + "3" * 56}


def _settings():
    """Enough settings to build all three suites, from this clone: stand-in
    ESP-IDF build folders for the interposer's images (their bytes are
    checked only at flash time) and stand-in ELFs for the test images."""
    d = os.path.join(TMP, "settings")
    scratch_dir.remove(d)
    for name in ("c12_bench", "vport_bench"):
        os.makedirs(os.path.join(d, "images", "idf", name))
        with open(os.path.join(d, "images", "idf", name, name + ".elf"),
                  "wb") as f:
            f.write(name.encode())
    for v, sha in IMG_SHA.items():
        _idf_build(os.path.join(d, "images"), v, sha)
    # the WiCAN's image folder, bench_wican_test's (made-up files, so the
    # committed pin's load refuses); test_wican_board adds its USB settings
    import bench_wican_test as WT
    ws, _root = WT.fixture("for-bench-suite-test", TMP)
    return {"interposer_code": HERE,
            "images_dir": os.path.join(d, "images"),
            "images_src": IMG_SRC,
            "x2_mac": "02:00:00:00:00:01",
            "wican_image_dir": ws["wican_image_dir"],
            "hosts": {"local": {}, "madhouse": {}}}


def _wican_usb(s, name):
    """s with the WiCAN's USB settings from bench_wican_test's made-up
    boards and fake esptool (the WiCAN on the pinned image) -> (s, root);
    run what identifies or resets the WiCAN under WT.Linux(root)."""
    import bench_wican_test as WT
    ws, root = WT.fixture(name, TMP)
    s = dict(s, wican_esptool_device=ws["wican_esptool_device"],
             wican_mac=ws["wican_mac"], esptool=ws["esptool"])
    return s, root


def _all():
    s = _settings()
    return s, {"emulated": B.emulated(s), "bench": B.bench(s),
               "harness": B.harness(s)}


M_GROUPS = {"emulated": ("m-host", "m-sims"), "harness": ("m-harness", "m-mutants")}


def _check_text(c):
    return " ".join([str(c.cwd)] + [str(a) for a in c.argv]
                    + ["%s=%s" % kv for kv in sorted((c.env or {}).items())])


def test_m_lanes_host():
    s = _settings()
    s_loc = dict(s, m_lanes_host="local")
    for k, make in (("emulated", B.emulated), ("harness", B.harness)):
        win, loc = make(s), make(s_loc)
        validate(loc, s_loc, builtin_flashers=("esptool",))
        wg = {g.name: g for g in win.groups}
        lg = {g.name: g for g in loc.groups}
        assert set(wg) == set(lg), (k, sorted(wg), sorted(lg))
        for n in M_GROUPS[k]:
            w, l = wg[n], lg[n]
            # the Windows settings (no m_lanes_host): ssh and the shipped copy
            assert w.host == "madhouse", (n, w.host)
            assert w.ship and set(w.ship) == {"code", "canre", "shared"}, (n, w.ship)
            live = [c for c in w.checks if not c.skip]
            assert live, n
            for c in live:
                assert c.cwd == "{work}/code", (n, c.id, c.cwd)
                assert (c.env.get("INTERPOSER_CODE"), c.env.get("CANRE_DIR"),
                        c.env.get("TWAI_FAKE_DIR")) == (
                    "{work}/code", "{work}/canre", "{work}/shared/twai_port/test"), (n, c.id)
            # local: the worktree, nothing shipped, no {work} anywhere
            assert l.host == "local" and not l.ship, (n, l.host, l.ship)
            for c in l.checks:
                if c.skip:
                    continue
                assert "{work}" not in _check_text(c), (n, c.id, _check_text(c))
                assert c.cwd == "{interposer_code}", (n, c.id, c.cwd)
                assert c.env.get("INTERPOSER_CODE") == "{interposer_code}", (n, c.id)
                assert "CANRE_DIR" not in c.env and "TWAI_FAKE_DIR" not in c.env, (n, c.id)
                assert c.env.get("L3_STIMULUS") == "{madhouse_artifacts}/l3_stimulus.txt"
            # the same checks either way
            assert [(c.id, c.argv, c.cls, c.judge, c.skip, c.python, c.timeout_s)
                    for c in w.checks] == \
                   [(c.id, c.argv, c.cls, c.judge, c.skip, c.python, c.timeout_s)
                    for c in l.checks], n
        for n in set(wg) - set(M_GROUPS[k]):
            assert (wg[n].host, [_check_text(c) for c in wg[n].checks]) == \
                   (lg[n].host, [_check_text(c) for c in lg[n].checks]), n
    for bad in ("ssh", "", "Local", "madhouse-debian"):
        try:
            B.emulated(dict(s, m_lanes_host=bad))
        except ValueError:
            continue
        raise AssertionError("m_lanes_host %r taken" % bad)


HARNESS_MARKS = ("mutation", "--can-fail", "--self-test", "canfail",
                 "--corrupt-pin")
# A harness check that needs the board cannot run without the lock, so it
# stays in the bench suite, named here.
BENCH_HARNESS = {"b-bridge/mcp-reset-canfail"}


def _argv(c):
    return " ".join(str(a) for a in c.argv)


def test_suites():
    s, su = _all()
    for k, x in su.items():
        validate(x, s, builtin_flashers=("esptool",))
    assert su["emulated"].name == "charge-interposer-emulated"
    assert su["bench"].requires == su["emulated"].name, \
        "the bench must require the emulated suite"
    assert su["emulated"].requires is None and su["harness"].requires is None
    for k in ("emulated", "harness"):
        assert not su[k].images, "%s flashes nothing" % k
        assert not any(g.needs_lock for g in su[k].groups), \
            "%s takes no lock" % k
    for g in su["bench"].groups:
        live = [c for c in g.checks if not c.skip]
        assert g.needs_lock or not live, \
            "bench group %s runs checks without the lock" % g.name
    for k in ("emulated", "bench"):
        for g in su[k].groups:
            for c in g.checks:
                if c.skip:
                    continue
                hit = [m for m in HARNESS_MARKS if m in _argv(c)]
                assert not hit or (k == "bench" and "%s/%s" % (
                    g.name, c.id) in BENCH_HARNESS), \
                    "%s/%s/%s is a harness test (%s)" % (k, g.name, c.id, hit)
    for g in su["harness"].groups:
        for c in g.checks:
            assert c.cls != "wallclock", \
                "harness check %s/%s is wall-clock" % (g.name, c.id)
    seen = {}
    for k, x in su.items():
        for g in x.groups:
            for c in g.checks:
                key = _argv(c) if not c.skip else "skip:" + c.id
                assert key not in seen, "%s in %s and %s" % (key, seen.get(key), k)
                seen[key] = k
        ids = {"%s/%s" % (g.name, c.id) for g in x.groups for c in g.checks}
        for kf in x.known_fails:
            top = "/".join(kf.id.split("/")[:2])
            assert top in ids, "%s: known FAIL %s names no check" % (k, kf.id)
        for g in x.groups:
            for c in g.checks:
                if c.skip:
                    assert any(w in c.skip for w in ("spec", "reviewer",
                                                     "plan", "ruling")), \
                        "skip %s/%s names no spec item or ruling" % (g.name, c.id)
    # 171: vport-listen-wrong-rate split out of vport-listen (2026-10-21)
    # 177: st.bench-topology and the five cf.* can-fails (2026-10-22)
    # 178: cf.bench-powerup-round, its --strict-port mutants (2026-10-22)
    # 179: cf.bench-c12, its TAGS reply cap mutants (2026-10-22)
    # 183: sb.test-decode, sb.test-run-core, each plain and -canfail (2026-10-22)
    # 185: sb.test-bench-lock and its -canfail, with the bench-lock merge
    # 186: cf.bench-hold12, its fresh-board gate mutants (2026-10-23)
    # 188: bench-wican-test and its -canfail, with the WiCAN board
    # 189: script-can-fail-test, script_can_fail's baseline run (2026-10-24)
    # 191: canre-pin-test and its -canfail, with the canre pin (2026-10-26)
    # 194: can-tools-pin-test, its -canfail and the local can-tools-pin-check,
    #      with the can-tools pin (2026-10-30)
    # 194: digest-recheck-check-canfail added and cutover-verify-guard-test
    #      retired (reviewer, 2026-10-31)
    # 198: bus-gs-usb-test-canfail, paths-test-canfail,
    #      st.bench-canbench-smoke and cf.bench-canbench-smoke, with the
    #      can-tools pin move to 1cd3942 (2026-11-01)
    # 200: twai-fake-option-test and its -canfail, with host_bridge's
    #      TWAI_FAKE option (reviewer, 2026-11-02)
    # 204: scratch-dir-test, scratch-run-test and each one's -canfail, with
    #      the run folders in scratch/ (reviewer, 2026-11-02)
    # 232: the pinned shared/bench's 13 further tests (the acceptance runner
    #      and scratch helpers) and each one's -canfail, and shared-pin-test
    #      and its -canfail, with the re-pin (port step 2a)
    # 234: hw-mechanics-test and its -canfail, with hw_mechanics.py's map
    #      on the IDF project (port step 2a)
    # 236: refusal-pairs-test and its -canfail, with shared/tools R20's
    #      refusal pairs for the interposer (port step 2a)
    # 238: idf-build-shell-test and its -canfail, with the acceptance
    #      adapter's build target (port step 2a acceptance list)
    # 239: variant-diff-self-test, with 2a acceptance (h)
    # 240: no-led-self-test, with 2a acceptance (j)
    # 242: b-bridge/idf-log and b-witness/idf-log, spec 8.3 on the board
    #      (2a acceptance (j), reviewer 2026-11-04)
    # 248: the two forced-order cases at phases 130 and 270 ms on the board
    #      (4), and scenario-scope-test with its -canfail (run 2a 0d0d's
    #      item 4, reviewer 2026-11-07); since 2026-11-08 forced-order-test
    #      and its -canfail in their place (2a accepted)
    # 247: twai-mutations retired with port_twai.cpp and test_twai_port
    #      (port step 2b; the vehicle port is the shared twai_port)
    # 259: sb.* for shared/bench 2026.11.11.2's six new test files
    #      (test_canmain, test_ota_outcome/policy/preflight/wire, test_wifi),
    #      each plain and -canfail (re-pin, reviewer 2026-11-11)
    # 261: serial_open-test and its -canfail (the X2 port's no-reset open on
    #      Linux, adapter characterisation step 7; reviewer 2026-11-11)
    # 267: sb.* for shared/bench 2026.11.11.6's three new test files (pin
    #      since 2026.11.11.7, no new test file)
    #      (test_canfail_control, test_wican_http, test_serial_dev), each
    #      plain and -canfail (re-pin, reviewer 2026-11-11)
    # 269: adapter-fixes-test and its -canfail (the can-tools pin's Linux
    #      fixes before every Kvaser and gs_usb open; reviewer 2026-11-11)
    # 271: wican-pin-test and its -canfail (the WiCAN's pinned image, the
    #      madhouse rebuild of gen-inhibit 7f2d5032 _man; reviewer 2026-11-11)
    # 273: cf.bench-charger-silent and cf.bench-rx-order (the gs_usb stamp's
    #      32-bit wrap unwrapped, the reviewer's E3, 2026-11-12)
    # 275: canchg-mode-test and its -canfail (the bench run sets canchg's
    #      mode through canctl; the reviewer's canchg-mode batch, 2026-11-12)
    # 277: canx2-slcan-test and its -canfail (the bench run detaches canx2
    #      from SLCAN before the X2 is identified or flashed; the reviewer's
    #      run-start batch, 2026-11-13)
    # 281: kvaser-link-test and its -canfail, st.keepalive-probe and
    #      cf.keepalive-probe (the Kvaser on kvaser_usb: link state per
    #      check, the stall rule; the reviewer's rulings, 2026-11-13)
    assert len(seen) == 281, "checks across the suites: %d" % len(seen)
    phased = sorted(c.id for g in su["bench"].groups for c in g.checks
                    if ".phase-" in c.id)
    assert phased == ["board.%s.phase-%dms" % (s, ms) for s in
                      ("charger-fault-stop-flow-first",
                       "fault-during-override-flow-first") for ms in (130, 270)] \
        and phased == sorted(phased), phased
    # madhouse gets the pinned canre beside the code, named by CANRE_DIR
    mad = 0
    for name in ("emulated", "harness"):
        for g in su[name].groups:
            if g.host == "madhouse":
                mad += 1
                assert g.ship.get("canre") and os.path.isdir(g.ship["canre"]),                     "%s/%s ships no canre: %s" % (name, g.name, g.ship)
                # and the pinned twai_port's checkout's shared/, its fake
                # named by TWAI_FAKE_DIR (port step 2b: host_bridge builds on
                # the shared fake, twai_port and can_port)
                sh = g.ship.get("shared")
                assert sh and os.path.normcase(sh) == os.path.normcase(
                    os.path.dirname(B.shared_pin.folder("shared/twai_port"))) and \
                    os.path.isdir(os.path.join(sh, "can_port")), \
                    "%s/%s ships no pinned shared/: %s" % (name, g.name, g.ship)
                for c in g.checks:
                    if c.skip:
                        continue        # never runs (the 2a retirements)
                    assert c.env.get("CANRE_DIR") == "{work}/canre",                         "%s/%s has CANRE_DIR %r" % (g.name, c.id, c.env.get("CANRE_DIR"))
                    assert c.env.get("TWAI_FAKE_DIR") == "{work}/shared/twai_port/test", \
                        "%s/%s has TWAI_FAKE_DIR %r" % (g.name, c.id, c.env.get("TWAI_FAKE_DIR"))
    # m-build is no longer a madhouse group: its checks retired at 2a
    assert mad == 4, "madhouse groups seen: %d" % mad
    # retired at port step 2a (Part B): listed with the reason, never run,
    # so a baseline comparison lists them and counts nothing (2a acceptance
    # (c)); the same ids as before, so the comparison can pair them
    mb = [g for g in su["emulated"].groups if g.name == "m-build"][0]
    assert [c.id for c in mb.checks] == [
        "build-check", "manifest-test", "manifest-offline-test"], mb.checks
    mh = [g for g in su["harness"].groups if g.name == "m-harness"][0]
    retired = [c for c in mb.checks + mh.checks if c.skip == B.RETIRED_2A]
    assert len(retired) == 11, [c.id for c in retired]
    assert all(c.id.startswith(("build-check", "manifest-", "digest-recheck"))
               for c in retired), [c.id for c in retired]
    # the product digest (user 2026-10-20): emulated and bench use the
    # component table's split; the harness suite keeps the commit
    assert su["emulated"].product_files is B.product_files
    assert su["bench"].product_files is B.product_files
    assert su["harness"].product_files is None
    repo = os.path.dirname(HERE)
    pf = set(B.product_files(repo))
    for must in ("charge-interposer/machine.py",
                 "charge-interposer/firmware/CMakeLists.txt",
                 "charge-interposer/firmware/sdkconfig",
                 "charge-interposer/firmware/dependencies.lock",
                 "charge-interposer/firmware/main/CMakeLists.txt",
                 "charge-interposer/firmware/main/idf_component.yml",
                 "charge-interposer/firmware/main/main.cpp",
                 "charge-interposer/firmware/main/machine.h",
                 "charge-interposer/firmware/main/board_ports.h"):
        assert must in pf, must
    for never in ("run_scenario.py", "vehicle_sim.py", "charger_sim.py",
                  "interposer_sim.py", "pack.py", "protocol.py", "bus.py",
                  "replay.py", "regress.py", "bench_suite.py"):
        assert "charge-interposer/" + never not in pf, never
    assert not [p for p in pf if "/test/" in p or p.endswith(".md")], pf
    # every file of main/ but its notes, the root's build files, machine.py
    fw = os.path.join(repo, "charge-interposer", "firmware")
    n_main = sum(1 for d, _s, fs in os.walk(os.path.join(fw, "main"))
                 for f in fs if not f.endswith(".md"))
    n_root = sum(1 for x in ("CMakeLists.txt", "sdkconfig", "dependencies.lock")
                 if os.path.exists(os.path.join(fw, x)))
    assert n_root == 3, n_root
    assert len(pf) == n_main + n_root + 1, (len(pf), n_main, n_root)
    # nothing the build makes or downloads, nor the menuconfig backup
    assert not [p for p in pf if "/build/" in p or "managed_components" in p
                or p.endswith("sdkconfig.old")], pf
    # every X2 image names the X2's chip, so a port that reaches another
    # board writes nothing (methodology, 2026-10-20)
    for n, im in su["bench"].images.items():
        if n == B.bench_wican.IMAGE:
            continue        # its chip is set at the load: bench_wican_test
        assert im.board == "x2" and (im.params or {}).get("chip") == {
            "type": "ESP32-S3", "mac": s["x2_mac"]}, (n, im.params)
    # the public repo holds no MAC: none in the suite's source
    src = open(os.path.join(HERE, "bench_suite.py"), encoding="utf-8").read()
    macs = re.findall(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b", src)
    assert not macs, "MAC literal(s) in bench_suite.py: %s" % macs
    e_sims = [c for g in su["emulated"].groups for c in g.checks
              if c.id.startswith("sim.")]
    assert len(e_sims) == 27 and all("--time-scale 1 " in _argv(c) + " "
                                     for c in e_sims), "27 sims at 1x"


def test_interposer_images():
    """Port step 2a: the bridge, witness and truck images are ESP-IDF
    builds, flashed as their flasher_args.json gives them -- bootloader,
    partition table, the build's own otadata and the app -- each file's md5
    pinned, and identified by the app's own descriptor: expect is its ELF
    hash's first four bytes (spec 8.2), and a folder whose app is not
    interposer_<variant> at images_src on ESP-IDF v5.4.2, or whose
    flasher_args.json is not the X2's four files, is refused when the suite
    is built."""
    import hashlib
    s, su = _all()
    for v, sha in IMG_SHA.items():
        im = su["bench"].images[v]
        d = os.path.join(s["images_dir"], IMG_SRC, v)
        assert im.expect == sha[:8], (v, im.expect)
        files = im.params["files"]
        assert [o for o, _p in files] == ["0x0", "0x8000", "0xe000",
                                          "0x10000"], files
        assert dict(files)["0xe000"] == os.path.join(d, "ota_data_initial.bin")
        assert dict(files)["0x10000"] == os.path.join(
            d, "interposer_%s.bin" % v), files
        for _o, p in files:
            with open(p, "rb") as f:
                assert im.params["md5"][p] == hashlib.md5(f.read()).hexdigest()
        assert "interposer_%s" % v in im.note and "v5.4.2" in im.note, im.note
    images = os.path.join(TMP, "img-refusals")
    scratch_dir.remove(images)
    t = dict(s, images_dir=images)
    sha = IMG_SHA["bridge"]
    no_ota = {"0x0": "bootloader/bootloader.bin",
              "0x10000": "interposer_bridge.bin",
              "0x8000": "partition_table/partition-table.bin"}
    swapped = {"0x0": "interposer_bridge.bin",
               "0x10000": "bootloader/bootloader.bin",
               "0x8000": "partition_table/partition-table.bin",
               "0xe000": "ota_data_initial.bin"}
    for why, kw in (("the witness in the bridge's folder",
                     {"project": "interposer_witness"}),
                    ("another commit's image", {"version": "def5678"}),
                    ("a dirty tree's image", {"version": IMG_SRC + "-dirty"}),
                    ("built with ESP-IDF v5.4.1", {"idf_ver": "v5.4.1"}),
                    ("no otadata", {"flash_files": no_ota}),
                    ("the app not at 0x10000", {"flash_files": swapped})):
        scratch_dir.remove(images)
        _idf_build(images, "bridge", sha, **kw)
        try:
            B._interposer_image("bridge", t)
        except (ValueError, KeyError) as e:
            continue
        raise AssertionError("not refused: %s" % why)
    # an app with no descriptor at all
    scratch_dir.remove(images)
    d = _idf_build(images, "bridge", sha)
    with open(os.path.join(d, "interposer_bridge.bin"), "wb") as f:
        f.write(b"\0" * 512)
    try:
        B._interposer_image("bridge", t)
        raise AssertionError("an app with no descriptor not refused")
    except ValueError:
        pass
    scratch_dir.remove(images)


LIST_VERSION = "v0.0-12-gabc1234"     # a list build's own version (git describe)


def _list_builds(s, root, **kw):
    """Stand-in build folders of an acceptance list's three builds, and the
    interposer_builds the adapter would hand the suite from their records:
    the record's app sha256 and ELF sha256 (kw: changes to one variant's
    pin, {variant: {key: value}}, or a folder's version, version_<v>)."""
    pins = {}
    for v, sha in IMG_SHA.items():
        d = _idf_build(os.path.join(root, "b-" + v), v, sha,
                       version=kw.get("version_" + v, LIST_VERSION))
        with open(os.path.join(d, "interposer_%s.bin" % v), "rb") as f:
            app_sha = hashlib.sha256(f.read()).hexdigest()
        pins[v] = {"dir": d, "build": "build-" + v, "elf_sha256": sha,
                   "app_sha256": app_sha}
        pins[v].update((kw.get("pins") or {}).get(v) or {})
    # images_src names no folder: the interposer images must not come from
    # images_dir (the two test images still do, from images_dir/idf/)
    return dict(s, interposer_builds=pins, images_src="no-such-src")


def test_list_build_images():
    """ACCEPT-SPEC A9 (the charge-interposer reviewer, 2026-11-04): in an
    acceptance run the bench suite flashes the list's own builds, from
    interposer_builds: each image from its build's folder, expect its ELF
    hash's first four bytes, the note and the run's code info naming the
    build and its ELF sha256, nothing read from images_dir. Refused when
    the suite is built, before the lock: a variant no listed build gives,
    an app whose bytes are not the record's, a descriptor ELF hash not the
    record's, a dirty tree's version."""
    s = _settings()
    root = os.path.join(TMP, "list-builds")
    scratch_dir.remove(root)
    t = _list_builds(s, root)
    su = B.bench(t)
    for v, sha in IMG_SHA.items():
        im = su.images[v]
        d = t["interposer_builds"][v]["dir"]
        assert im.expect == sha[:8], (v, im.expect)
        assert dict(im.params["files"])["0x10000"] == os.path.join(
            d, "interposer_%s.bin" % v), im.params["files"]
        assert all(p.startswith(d) for _o, p in im.params["files"]), im.params["files"]
        assert "list build build-%s, ELF sha256 %s" % (v, sha) in im.note, im.note
        assert LIST_VERSION in im.note, im.note
    info = su.code_info({})
    assert info.get("interposer_builds") == {
        v: {"build": "build-" + v, "elf_sha256": sha} for v, sha in IMG_SHA.items()} \
        and "images_src" not in info, info
    other = "f" * 64
    for why, kw in (
            ("no build of the truck", {"drop": "truck"}),
            ("the app's bytes not the record's", {"pins": {"bridge": {"app_sha256": other}}}),
            ("the descriptor's ELF hash not the record's",
             {"pins": {"witness": {"elf_sha256": other}}}),
            ("a dirty tree's version", {"version_truck": LIST_VERSION + "-dirty"})):
        scratch_dir.remove(root)
        u = _list_builds(s, root, **kw)
        if kw.get("drop"):
            del u["interposer_builds"][kw["drop"]]
        try:
            B.bench(u)
        except (ValueError, KeyError) as e:
            if kw.get("drop"):
                assert "truck" in str(e) and "acceptance list" in str(e), e
            continue
        raise AssertionError("not refused: %s" % why)
    scratch_dir.remove(root)


def _image_folder_pins(variant, settings):
    """The pins recomputed from the build folders (what the reviewer ruled
    out): the record's sha256 never reaches the check."""
    import build_lookup
    s = dict(settings)
    if s.get("interposer_builds"):
        pins = {}
        for v, p in s["interposer_builds"].items():
            app = os.path.join(p["dir"], "interposer_%s.bin" % v)
            with open(app, "rb") as f:
                app_sha = hashlib.sha256(f.read()).hexdigest()
            pins[v] = dict(p, app_sha256=app_sha,
                           elf_sha256=build_lookup.app_desc_of_bin(app)["elf_sha256"])
        s["interposer_builds"] = pins
    return _ORIG["_interposer_image"](variant, s)


def _image_any_build(variant, settings):
    """A variant no listed build gives taken from another build's folder."""
    b = settings.get("interposer_builds")
    if b and variant not in b:
        settings = dict(settings, interposer_builds=dict(b, **{variant: next(iter(b.values()))}))
    try:
        return _ORIG["_interposer_image"](variant, settings)
    except ValueError:
        return B.Image(variant, "esptool", board="x2", expect="00000000",
                       params={"files": [], "md5": {}, "chip": B._x2_chip(settings)},
                       note="list build ?")


def _image_images_dir(variant, settings):
    """interposer_builds ignored: the images from images_dir as before."""
    s = dict(settings)
    s.pop("interposer_builds", None)
    return _ORIG["_interposer_image"](variant, s)


def test_idf_log_checks():
    """Spec 8.3 from port step 2a (the charge-interposer reviewer,
    2026-11-04, 2a acceptance (j)): b-bridge and b-witness each run
    bench_flight_silence.py --idf-log on their image (no --pcan: the image's
    own lines pass, only framework log forms fail); b-truck keeps the
    stronger flight-silence, no application byte at all, without --idf-log."""
    _s, su = _all()
    g = {x.name: x for x in su["bench"].groups}

    def argv(group, cid):
        cs = [c for c in g[group].checks if c.id == cid]
        assert len(cs) == 1 and not cs[0].skip, (group, cid, [c.id for c in g[group].checks])
        return [str(a) for a in cs[0].argv]
    for group in ("b-bridge", "b-witness"):
        a = argv(group, "idf-log")
        assert a[0].endswith("bench_drivers/bench_flight_silence.py") and "--idf-log" in a \
            and a[a.index("--port") + 1] == "{serial_port}" and "--pcan" not in a \
            and a[a.index("--out") + 1].startswith("{outdir}"), (group, a)
    t = argv("b-truck", "flight-silence")
    assert "--idf-log" not in t and "--pcan" in t, t


def _drop_witness_idf_log(s):
    for g in s.groups:
        if g.name == "b-witness":
            g.checks = [c for c in g.checks if c.id != "idf-log"]


def _idf_log_flag_dropped(s):
    for g in s.groups:
        for c in g.checks:
            if c.id == "idf-log":
                c.argv = [a for a in c.argv if a != "--idf-log"]


def _truck_idf_log(s):
    for g in s.groups:
        for c in g.checks:
            if g.name == "b-truck" and c.id == "flight-silence":
                c.argv = list(c.argv) + ["--idf-log"]


def _image_never_refuses(variant, settings):
    try:
        return _ORIG["_interposer_image"](variant, settings)
    except (ValueError, KeyError):
        return B.Image(variant, "esptool", board="x2", expect="00000000",
                       params={"files": [], "md5": {},
                               "chip": B._x2_chip(settings)})


def _image_name_expect(variant, settings):
    """expect taken from the file name, not the bytes."""
    im = _ORIG["_interposer_image"](variant, settings)
    im.expect = variant[:8]
    return im


def _branch_shared_bench(s):
    """The shared/bench tests pointed at a copy beside this clone's
    charge-interposer/ (the pre-2a layout), not the pinned checkout."""
    for g in s.groups:
        for c in g.checks:
            a = [str(x) for x in c.argv]
            if a and "/shared/bench/tests/" in a[0].replace("\\", "/"):
                c.argv = ["{interposer_code}/../shared/bench/tests/" +
                          os.path.basename(a[0])] + a[1:]


def _unretire_build_check(s):
    """build-check given back an argv after its 2a retirement."""
    for g in s.groups:
        for c in g.checks:
            if c.id == "build-check":
                c.skip = None
                c.argv = ["firmware/test/build_check.py"]


def test_wican_board():
    """The bench suite carries the WiCAN (bench_wican.py; reviewer,
    2026-10-23, for the bench-lock cutover): every bench group that holds
    the lock names wican_man beside its X2 image; wican_man is idle-safe
    and the WiCAN has no idle-safe image of its own (the runner's end pass
    never loads one), its identity is the committed pin's image hash, its
    flasher is bench_wican's USB load (2026-11-12), which checks the pinned
    files first and so runs nothing on the fixture's made-up files, its
    hooks are bench_wican's USB hard reset (the X2 gets no step), and
    identify sends the WiCAN to bench_wican's USB read, not to the X2's
    identify (bench_wican_test's made-up boards and fake esptool)."""
    import bench_wican_test as WT
    s, wroot = _wican_usb(_settings(), "wican-board")
    b = B.bench(s)
    W = B.bench_wican
    locked = [g for g in b.groups if g.needs_lock]
    assert len(locked) == 5, [g.name for g in locked]
    for g in locked:
        il = g.image_list
        assert len(il) == 2 and [b.images[x].board for x in il] == [
            "x2", W.BOARD] and il[1] == W.IMAGE, (g.name, il)
    assert b.images[W.IMAGE].idle_safe and W.BOARD not in b.idle_safe_images, \
        (b.images[W.IMAGE].idle_safe, b.idle_safe_images)
    im = b.images[W.IMAGE]
    assert (im.board, im.flasher, im.expect) == (W.BOARD, W.FLASHER,
                                                 WT.MAN_SHA), im
    res = b.flashers[W.FLASHER](im, {}, s, TMP)
    assert res["ok"] is False and res["phase"] == "pin" and \
        "nothing run" in res["detail"], res
    d = os.path.join(TMP, "wican_hooks")
    os.makedirs(d, exist_ok=True)
    with WT.Linux(wroot):
        assert b.identify(im, None, d) == WT.MAN_SHA
    keep = _canchg_stubs([])            # the X2's canchg step: test_canchg_mode_hooks
    try:
        x2 = b.on_start("x2", b.images["bridge"], None, d)
    finally:
        _canchg_restore(keep)
    assert x2["ok"] is True and "no start step" in x2["detail"], x2
    port = WT.root_ports(wroot)["wican"]
    for hook in (b.on_start, b.on_end):
        n = len(WT.esptool_calls(wroot))
        with WT.Linux(wroot):
            out = hook(W.BOARD, im, None, d)
        a = WT.esptool_calls(wroot)[n:]
        assert out["ok"] is True and a == [["--port", port, "--after", "hard_reset",
                                            "chip_id"]], (out, a)


def test_canchg_mode_hooks():
    """The bench suite's hooks set canchg's mode (canchg_mode.py; tester,
    2026-11-12, the reviewer's canchg-mode batch): on the X2, on_start runs
    canchg_mode.start and on_end canchg_mode.end, once each, with the
    settings' canchg_device as VTRUX_CANCHG_DEVICE; on the WiCAN neither
    runs (bench_wican's hooks alone). The emulated and harness suites have
    no hooks. canchg_mode_test.py tests the steps themselves, and the end
    step running whatever the WiCAN's hook returned or raised."""
    s = _settings()
    s["canchg_device"] = dict(CHG_DEV)
    want = _ORIG["canchg_env"](s)
    assert want, want
    b = B.bench(s)
    W = B.bench_wican
    d = os.path.join(TMP, "canchg_hooks")
    os.makedirs(d, exist_ok=True)
    calls = []
    keep = _canchg_stubs(calls)
    try:
        out = b.on_start("x2", b.images["bridge"], None, d)
        chg = [c for c in calls if c[0] != "canx2"]     # canx2's: test_canx2_slcan_hooks
        assert out["ok"] is True and chg == [("start", want)], (out, calls)
        out = b.on_end("x2", b.images["bridge"], None, d)
        chg = [c for c in calls if c[0] != "canx2"]
        assert out["ok"] is True and chg == [("start", want), ("end", want)], (out, calls)
        im = b.images[W.IMAGE]
        for hook in (b.on_start, b.on_end):
            hook(W.BOARD, im, None, d)
        assert len([c for c in calls if c[0] != "canx2"]) == 2, calls
    finally:
        _canchg_restore(keep)
    for su in (B.emulated(s), B.harness(s)):
        assert su.on_start is None and su.on_end is None, (su.name, su.on_start, su.on_end)


def test_canx2_slcan_hooks():
    """The bench suite's X2 start hook detaches canx2 from the kernel's
    SLCAN first (canx2_slcan.py; tester, 2026-11-13, the reviewer's
    run-start batch): on the X2, on_start runs canx2_slcan.start once,
    before canchg's start step (and so before bench_wican's hook given and
    before the runner identifies or flashes the X2); its failure makes the
    X2's start hook fail at once, with nothing more of it run (no canchg
    step), which stops the bench; on_end runs no canx2 step
    (nothing put back); on the WiCAN neither hook runs it.
    canx2_slcan_test.py tests the step itself."""
    s = _settings()
    s["canchg_device"] = dict(CHG_DEV)
    b = B.bench(s)
    W = B.bench_wican
    d = os.path.join(TMP, "canx2_hooks")
    os.makedirs(d, exist_ok=True)
    calls = []
    keep = _canchg_stubs(calls)
    try:
        out = b.on_start("x2", b.images["bridge"], None, d)
        steps = [c[0] for c in calls]
        assert out["ok"] is True and steps == ["canx2", "start"], (out, steps)
        assert "canx2 stub" in out["detail"], out
        b.on_end("x2", b.images["bridge"], None, d)
        assert [c[0] for c in calls] == ["canx2", "start", "end"], calls
        im = b.images[W.IMAGE]
        for hook in (b.on_start, b.on_end):
            hook(W.BOARD, im, None, d)
        assert [c[0] for c in calls].count("canx2") == 1, calls

        def failed(logdir, platform=None):
            calls.append(("canx2", {}))
            return dict(ok=False, detail="canx2 failed stub", found={})
        B.canx2_slcan.start = failed
        n = len(calls)
        out = b.on_start("x2", b.images["bridge"], None, d)
        assert out["ok"] is False and "canx2 failed stub" in out["detail"] and \
            "was not run" in out["detail"], out
        # nothing more of the X2's start hook runs (the reviewer, 2026-11-13)
        assert [c[0] for c in calls[n:]] == ["canx2"], calls[n:]
    finally:
        _canchg_restore(keep)


# a made-up Innomaker serial (24 hex digits, the real ones' shape)
CHG_DEV = {"vid": "1D50", "pid": "606f", "serial": "00CC00DD5555666677778888"}


def test_canchg_env():
    """The reviewer's E2 (tester, 2026-11-12): the settings' canchg_device
    reaches every check of every bench group as VTRUX_CANCHG_DEVICE
    "<vid>:<pid>:<serial>", vid and pid lower-cased, and survives the
    runner's formatting; without the setting no bench check carries it; a
    malformed entry is refused (ValueError); the emulated and harness
    suites never carry it."""
    s = _settings()
    s["canchg_device"] = dict(CHG_DEV)
    want = "1d50:606f:" + CHG_DEV["serial"]
    b = B.bench(s)
    checks = [c for g in b.groups for c in g.checks]
    assert len(checks) > 30, len(checks)
    off = [c.id for c in checks if c.env.get(B.CANCHG_ENV) != want]
    assert not off, off
    from vtrux_bench.run.engine import fmt
    assert fmt(want, {}) == want
    for k, f in (("emulated", B.emulated), ("harness", B.harness)):
        assert not [c.id for g in f(s).groups for c in g.checks
                    if B.CANCHG_ENV in (c.env or {})], k
    s2 = _settings()
    assert not [c.id for g in B.bench(s2).groups for c in g.checks
                if B.CANCHG_ENV in c.env]
    for bad in ("1d50:606f:" + CHG_DEV["serial"], dict(CHG_DEV, vid="1d5"),
                dict(CHG_DEV, pid="60zf"), dict(CHG_DEV, serial=""),
                dict(CHG_DEV, serial="00CC 00DD"), dict(CHG_DEV, serial="{x}")):
        s2["canchg_device"] = bad
        try:
            B.canchg_env(s2)
        except ValueError:
            continue
        raise AssertionError("canchg_device %r not refused" % (bad,))


def test_esptool_env():
    """bench_mcp_reset's esptool is the core flasher's (tester, 2026-11-12;
    the reviewer, 2026-11-11): the settings' esptool.argv reaches every
    bench check as VTRUX_ESPTOOL_ARGV, the JSON of the list, which survives
    the runner's formatting and the driver reads back as the same list;
    without it no check carries it; a malformed argv is refused."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bench_mcp_reset", os.path.join(HERE, "bench_drivers", "bench_mcp_reset.py"))
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    argv = ["/opt/idf/python_env/bin/python", "-m", "esptool"]
    s = _settings()
    s["esptool"] = {"argv": list(argv), "baud": 921600}
    b = B.bench(s)
    vals = {c.env.get(B.ESPTOOL_ENV) for g in b.groups for c in g.checks}
    assert len(vals) == 1, vals
    from vtrux_bench.run.engine import fmt
    v = fmt(vals.pop(), {})
    assert mr.esptool_argv({mr.ESPTOOL_ENV: v}, "posix") == argv, v
    assert B.ESPTOOL_ENV == mr.ESPTOOL_ENV
    s2 = _settings()
    assert not [c.id for g in B.bench(s2).groups for c in g.checks
                if B.ESPTOOL_ENV in c.env]
    for bad in ("python -m esptool", [], ["python", ""], ["python", 3], ["{x}/python"]):
        s2["esptool"] = {"argv": bad}
        try:
            B.esptool_env(s2)
        except ValueError:
            continue
        raise AssertionError("esptool.argv %r not refused" % (bad,))


def test_idle_safe():
    """shared/bench 2026.11.11.2 (user, 2026-11-11: no end restore): the
    bench suite names no standing image; exactly witness, c12_bench and
    vport_bench are marked not safe to leave idle (X2_IDLE_UNSAFE), the
    bridge, truck and WiCAN images are idle-safe, and the X2's idle-safe
    image is the bridge (spec 2.3; X2_IDLE_SAFE). The emulated and harness
    suites have no images to mark."""
    s, su = _all()
    b = su["bench"]
    assert b.standing_image is None and b.standing_images is None, \
        (b.standing_image, b.standing_images)
    unsafe = {n for n, im in b.images.items() if not im.idle_safe}
    assert unsafe == {"witness", "c12_bench", "vport_bench"} == set(B.X2_IDLE_UNSAFE), unsafe
    assert b.idle_safe_images == {"x2": "bridge"}, b.idle_safe_images
    for n in ("bridge", "truck", B.bench_wican.IMAGE):
        assert b.images[n].idle_safe, n
    validate(b, s, builtin_flashers=("esptool",))
    for k in ("emulated", "harness"):
        assert not su[k].images and not su[k].idle_safe_images, k


def test_mutant_wiring():
    """The corrupt-pin check uses its judge; every other mutant script
    the exit-code judge."""
    s, su = _all()
    mut = {c.id: c for g in su["harness"].groups if g.name == "m-mutants"
           for c in g.checks}
    assert mut["monitor-6h-diff-corrupt-pin"].judge is B.corrupt_pin_judge
    assert all(c.judge is B.script_judge for k, c in mut.items()
               if k != "monitor-6h-diff-corrupt-pin")


# The two HELLO? replies the bridge image gave in the 00:30 trial
# (runs/charge-interposer_20261018T003009_7077/_bench/b-bridge/).
REPLY_DIAG = ("=0 ovf=0 txf=0 silent=0 fw=9 schema=4\ndiag st=PASSTHROUGH "
              "trip=0 flags=0xc0 ilim=- vmax=0 chgmax=0 soc=0.0 evap=0 chg=0 "
              "mod=0 synth=0 ovf=0 txf=0 silent=0 fw=9 schema=4\n")
REPLY_LOSS = (" boot=61522us switch=300/300us(last/max) drained=0\n  LOSS "
              "veh(rx=0 tx=0 err=184 off=0 peak=0) chg(rx=0 tx=0 err=0 off=0 "
              "ring=0/0) bridge=0 emit=0\n       bridged frames that missed "
              "the vehicle: 0 (clears bridge_ok for that interval)\n")


def test_identify():
    """bench_identify: the product's serial lines are recognised on any
    line, and a product image is never sent the SLCAN probe."""
    I = B.bench_identify
    for name, text in (("diag reply", REPLY_DIAG), ("loss reply", REPLY_LOSS),
                       ("diag line not first", "x\ndiag st=MONITOR trip=0\n")):
        assert I.PRODUCT_RE.search(text), "%s not seen as product" % name
    for text in ("@HELLO c12_bench version=1 elf_sha256=" + "a" * 64 + "\n",
                 "V0101\rNF001\r", "JAMMER ERR unknown_command\n",
                 "INFO mcp2515 ready\n"):
        assert not I.PRODUCT_RE.search(text), "%r seen as product" % text
    calls = []

    def fake(r7, rh, rs):
        def f7(settings, logdir, tag):
            calls.append("7f7")
            if isinstance(r7, Exception):
                raise r7
            return r7

        def fh(settings, logdir, tag):
            calls.append("hello")
            return rh if rh is not None else (None, False)

        def fs(settings, logdir, tag):
            calls.append("slcan")
            return rs
        return f7, fh, fs

    saved = (I.read_7f7, I.read_hello, I.read_slcan)
    try:
        cases = [
            (("756d6714", False), None, None, "756d6714", ["7f7"]),
            ((None, True), None, None, "product-without-0x7F7", ["7f7"]),
            ((None, False), (None, True), None, "product-without-0x7F7",
             ["7f7", "hello"]),
            ((None, False), ("c12_bench:x", False), None, "c12_bench:x",
             ["7f7", "hello"]),
            ((None, False), (None, False), "slcan:V0101:NF001",
             "slcan:V0101:NF001", ["7f7", "hello", "slcan"]),
        ]
        img = B.Image("x", "esptool")
        for r7, rh, rs, want, seq in cases:
            del calls[:]
            I.read_7f7, I.read_hello, I.read_slcan = fake(r7, rh, rs)
            got = I.identify(img, {}, TMP, {})
            assert got == want and calls == seq, (r7, rh, got, calls)
        del calls[:]
        I.read_7f7, I.read_hello, I.read_slcan = fake(
            I.IdentifyError("no capture"), None, None)
        try:
            I.identify(img, {}, TMP, {})
        except I.IdentifyError:
            pass
        else:
            raise AssertionError("a failed boot capture did not raise")
        assert calls == ["7f7"], calls
        # interposer_dbc is retired (2026-10-24): refused before any read
        del calls[:]
        I.read_7f7, I.read_hello, I.read_slcan = fake(
            ("756d6714", False), None, None)
        try:
            I.identify(img, {}, TMP, {"interposer_dbc": "x.dbc"})
        except I.IdentifyError as e:
            assert "retired" in str(e), e
        else:
            raise AssertionError("settings with interposer_dbc not refused")
        assert calls == [], calls
    finally:
        I.read_7f7, I.read_hello, I.read_slcan = saved


# ---- the X2's test images (tester, 2026-11-13, the charge-interposer
# reviewer's item 4) ---------------------------------------------------------
# Hand takes and the Android session leave the X2 on one of
# shared/x2_test_images' images (the jammer, the SLCANs), and under the bench
# lock as a mutex nothing puts it back (shared/bench SPEC section 8). The
# bench suite must never take such a board for one of its own images:
# identify reads it as no image of the suite, without raising (a raise
# stops the bench, test_identify), and the runner flashes the suite's image
# before the group's first check. What each image answers is its README's,
# at shared/main tag 2026.11.13.2: jammer/README.md ("Commands": lines end
# at LF, CR dropped, anything malformed answered "JAMMER ERR <reason>"; the
# five-line banner at boot), slcan_spi/README.md ("The protocol": lines end
# at CR, V / N answer "V0100 <identity>" / "N<identity>", anything else BEL;
# "Console lines": the one boot line) and slcan_can1's, the interposer's
# slcan variant until the move (V0101 / NF001, bench_identify's docstring).
X2_JAMMER_BANNER = [
    "JAMMER vtrux_jammer_x2_NOT_FOR_VEHICLE -- TEST EQUIPMENT, NEVER ON THE TRUCK",
    "JAMMER build version=2026.11.13 elf=" + "4" * 64 + " idf=v5.4.2",
    "JAMMER tx=GPIO7(CAN1/TWAI) twai=never_started stops=a+b reset_reason=21 "
    "uptime_us=300120",
    "JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes",
    "JAMMER limits w_us=12..50 p_us>=4w..20000 ms<=30000 n<=1000 interval_ms=1..10000"]
X2_SLCAN_SPI_ID = "slcan_spi_NOT_FOR_VEHICLE 2026.11.13 elf=" + "5" * 64
# what identify reads off each test image: the jammer, no image at all; for
# slcan_can1 the protocol (bench_identify's docstring: SLCAN reports no
# build id); slcan_spi by its identity, which names its build (SPEC T12):
# "<name>:<version>:<elf sha256>" (tester, 2026-11-13, the charge-interposer
# reviewer's queue after the slcan_can1 move)
X2_TEST_READS = {"jammer": None,
                 "slcan_spi": "slcan_spi_NOT_FOR_VEHICLE:2026.11.13:" + "5" * 64,
                 "slcan_can1": "slcan:V0101:NF001"}


class _FakeX2:
    """The X2 as bench_identify's three reads see it, on one image: the
    ACKed boot (bench_powerup_round, run as a subprocess: its log, with the
    board's serial lines as "serial>" lines, and its witness file) and the
    USB serial port (HELLO?, then CR, V and N). image: "jammer",
    "slcan_spi", "slcan_can1", ("app", build id) for an interposer image
    (0x7F7 and the product's serial lines) or ("hello", name, ELF sha256)
    for a bench test image. jam_end and jam_err shape the jammer's replies,
    which its README does not pin down beyond "JAMMER ERR <reason>".
    spi_v and spi_n are the identities slcan_spi answers V and N with."""

    def __init__(self, image, jam_end=b"\n", jam_err=b"unknown_command",
                 spi_v=X2_SLCAN_SPI_ID, spi_n=X2_SLCAN_SPI_ID):
        self.image, self.jam_end, self.jam_err = image, jam_end, jam_err
        self.spi_v, self.spi_n = spi_v, spi_n
        self.boots = self.opens = 0
        self.inbuf, self.out = b"", b""

    def boot_lines(self):
        im = self.image
        if im == "jammer":
            return list(X2_JAMMER_BANNER)
        if im == "slcan_spi":
            return ["# NOT FOR VEHICLE slcan_spi"]
        if im[0] == "app":
            return REPLY_DIAG.strip().splitlines()
        return []

    def run(self, cmd, stdout=None, **kw):
        """bench_powerup_round --mode ack --boots 1 --out OUT."""
        self.boots += 1
        out = cmd[cmd.index("--out") + 1]
        os.makedirs(out, exist_ok=True)
        rows = []
        if self.image[0] == "app":
            data = int(self.image[1], 16).to_bytes(4, "little") + bytes(4)
            rows.append("0.100000 0.100000 7F7 0 %s" % data.hex())
        with open(os.path.join(out, "witness_ack_01.txt"), "w") as f:
            f.write("# t_device t_host id err data -- fake\n")
            f.write("".join(r + "\n" for r in rows))
        stdout.write("boot ack_01: fake; segment: fake; WiCAN fake; 0 frame(s)\n")
        for ln in self.boot_lines():
            stdout.write("  serial> %s\n" % ln)
        stdout.write("\n== verdicts\n  ack                 1  PASS\n")
        return types.SimpleNamespace(returncode=0)

    def open_quiet(self, port, baud, timeout):
        self.opens += 1
        self.inbuf = self.out = b""
        return _FakeX2Port(self)

    def feed(self, data):
        im = self.image
        for b in data:
            ch = bytes([b])
            if im == "jammer":
                if ch == b"\r":
                    continue
                if ch == b"\n":
                    self.out += b"JAMMER ERR " + self.jam_err + self.jam_end
                    self.inbuf = b""
                    continue
            elif im in ("slcan_spi", "slcan_can1"):
                if ch == b"\r":
                    line, self.inbuf = self.inbuf.strip(b"\n"), b""
                    if im == "slcan_spi" and line == b"V":
                        self.out += b"V0100 " + self.spi_v.encode() + b"\r"
                    elif im == "slcan_spi" and line == b"N":
                        self.out += b"N" + self.spi_n.encode() + b"\r"
                    elif im == "slcan_can1" and line in (b"V", b"N"):
                        self.out += b"V0101\r" if line == b"V" else b"NF001\r"
                    else:
                        self.out += b"\x07"
                    continue
            elif im[0] == "hello":
                if ch == b"\n" and self.inbuf.strip() == b"HELLO?":
                    self.out += ("@HELLO %s version=1 elf_sha256=%s\n"
                                 % (im[1], im[2])).encode()
                if ch == b"\n":
                    self.inbuf = b""
                    continue
            self.inbuf += ch


class _FakeX2Port:
    def __init__(self, board):
        self.board = board

    def write(self, data):
        self.board.feed(data)

    def read(self, n):
        got, self.board.out = self.board.out[:n], self.board.out[n:]
        return got

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.board.out = b""

    def close(self):
        pass


class _FakeClock:
    """bench_identify's time: no real waits (HELLO? waits 5 s for nothing on
    a test image)."""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        self.t += 0.25
        return self.t

    def sleep(self, s):
        self.t += s


def _on_board(board):
    """bench_identify's subprocess, serial port and clock pointed at board;
    returns what to hand to _off_board."""
    I = B.bench_identify
    import serial_open
    keep = (I.subprocess, I.time, serial_open.open_quiet)
    I.subprocess = types.SimpleNamespace(run=board.run, STDOUT=subprocess.STDOUT)
    clock = _FakeClock()
    I.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    serial_open.open_quiet = board.open_quiet
    return keep


def _off_board(keep):
    I = B.bench_identify
    import serial_open
    I.subprocess, I.time, serial_open.open_quiet = keep


def _x2_test_boards():
    """[(label, board, what identify must read)]: the jammer with its
    replies ended LF or CRLF, a bare reason or one that echoes the line,
    and the two SLCANs."""
    out = []
    for end in (b"\n", b"\r\n"):
        for err in (b"unknown_command", b"unknown_command HELLO?"):
            out.append(("jammer %r %r" % (end, err),
                        _FakeX2("jammer", end, err), X2_TEST_READS["jammer"]))
    for im in ("slcan_spi", "slcan_can1"):
        out.append((im, _FakeX2(im), X2_TEST_READS[im]))
    return out


def test_x2_test_images_identify():
    """(a) identify on a board left on an X2 test image: the jammer, silent
    on CAN and answering no read with an identity, is read as None (no
    image identify knows); slcan_spi by its identity (name, version, ELF
    hash); slcan_can1 as its protocol,
    slcan:V0101:NF001. None of them raises, none is read as the product
    (product-without-0x7F7) and none as any image of the bench suite; the
    boot capture runs once and the port is opened for HELLO? and SLCAN.
    Controls on the same fakes: an interposer image is read by its 0x7F7
    build id with the port never opened, a bench test image by its @HELLO."""
    I = B.bench_identify
    s, su = _all()
    expects = {im.expect for im in su["bench"].images.values()}
    img = B.Image("bridge", "esptool")
    st = {"serial_port": "fake-x2"}
    for label, board, want in _x2_test_boards():
        keep = _on_board(board)
        try:
            got = I.identify(img, {}, TMP, st)
        finally:
            _off_board(keep)
        assert got == want, "%s: read %r, not %r" % (label, got, want)
        assert got not in expects, "%s read as a bench image: %r" % (label, got)
        assert got != "product-without-0x7F7", "%s read as the product" % label
        assert (board.boots, board.opens) == (1, 2), \
            "%s: %d boot(s), port opened %d time(s)" % (label, board.boots,
                                                        board.opens)
    for board, want, opens in (
            (_FakeX2(("app", "c7c98adc")), "c7c98adc", 0),
            (_FakeX2(("hello", "c12_bench", "6" * 64)),
             "c12_bench:" + "6" * 64, 1)):
        keep = _on_board(board)
        try:
            got = I.identify(img, {}, TMP, st)
        finally:
            _off_board(keep)
        assert got == want and board.opens == opens, (board.image, got,
                                                      board.opens)


def test_slcan_spi_identity():
    """(c) slcan_spi's identity (slcan_spi/README.md, "The protocol" and
    "Variants", at shared/main tag 2026.11.13.3): identify names the build
    it reads, "<name>:<version>:<elf sha256>", for the dev image and both
    test variants, so the run record and the release note name the board's
    image. Read as no image identify knows: V and N giving different
    identities, a name not ending _NOT_FOR_VEHICLE, an ELF hash not 64
    digits, an identity not the whole answer. None is read as the product
    or as an image of the bench suite, and none raises."""
    I = B.bench_identify
    s, su = _all()
    expects = {im.expect for im in su["bench"].images.values()}
    img = B.Image("bridge", "esptool")
    st = {"serial_port": "fake-x2"}
    elf, other = "a" * 64, "b" * 64
    cases = []
    for name in ("slcan_spi_NOT_FOR_VEHICLE", "slcan_spi_a_NOT_FOR_VEHICLE",
                 "slcan_spi_b_NOT_FOR_VEHICLE"):
        for ver in ("2026.11.13.3", "2026.11.13.3-2-g4dc0bd00-dirty"):
            ident = "%s %s elf=%s" % (name, ver, elf)
            cases.append(("%s %s" % (name, ver), ident, ident,
                          "%s:%s:%s" % (name, ver, elf)))
    good = "slcan_spi_NOT_FOR_VEHICLE 2026.11.13.3 elf=" + elf
    cases += [
        ("V and N elf differ", good,
         "slcan_spi_NOT_FOR_VEHICLE 2026.11.13.3 elf=" + other, None),
        ("V and N version differ", good,
         "slcan_spi_NOT_FOR_VEHICLE 2026.11.13.2 elf=" + elf, None),
        ("V and N name differ", good,
         "slcan_spi_a_NOT_FOR_VEHICLE 2026.11.13.3 elf=" + elf, None),
        ("no NOT_FOR_VEHICLE", "slcan_spi 2026.11.13.3 elf=" + elf,
         "slcan_spi 2026.11.13.3 elf=" + elf, None),
        ("elf 63 digits", good[:-1], good[:-1], None),
        ("elf 65 digits", good + "a", good + "a", None),
        ("an identity inside a longer answer", "x " + good, "x " + good, None)]
    for label, v, n, want in cases:
        board = _FakeX2("slcan_spi", spi_v=v, spi_n=n)
        keep = _on_board(board)
        try:
            got = I.identify(img, {}, TMP, st)
        finally:
            _off_board(keep)
        assert got == want, "%s: read %r, not %r" % (label, got, want)
        assert got not in expects, "%s read as a bench image: %r" % (label, got)
        assert got != "product-without-0x7F7", "%s read as the product" % label


def test_x2_test_image_flash_first():
    """(b) a bench group whose X2 is found on a test image: the runner
    (the pinned core's Engine, with the bench suite's own identify hook and
    bench_identify reading the fake board) flashes the group's image first
    and runs its checks only once identify reads that image back, for every
    X2 image of the bench suite and each test image; a flash that leaves
    the board on the test image runs none of the group's checks, all
    NOT-RUN. The flasher is a fake that puts the image on the fake board;
    the checks and their placement are recorded, never run."""
    from vtrux_bench.run.engine import Engine
    from vtrux_bench.run.record import Recorder
    import bench_wican_test as WT
    s, su = _all()
    s["serial_port"] = "fake-x2"
    # the groups carry the WiCAN too: identified over the fake USB
    s, wroot = _wican_usb(s, "flash-first-wican")
    b = B.bench(s)
    x2_images = sorted(n for n, im in b.images.items() if im.board == "x2")
    assert len(x2_images) == 5, x2_images
    # this call's own run folders (--can-fail runs the test once per mutant)
    runs = os.path.join(TMP, "flash-first")
    scratch_dir.remove(runs)

    def engine(tag, board, take=True):
        eng = Engine(b, s, runs_root=runs)
        eng.run_id = tag
        eng.run_dir = os.path.join(runs, tag)
        os.makedirs(eng.run_dir)
        eng.rec = Recorder(eng.run_dir, tag)
        seq = []

        def flasher(image, params, settings, logdir):
            seq.append(("flash", image.name, board.image))
            if take:
                e = image.expect
                board.image = ("hello",) + tuple(e.split(":", 1)) \
                    if ":" in e else ("app", e)
            return dict(ok=True, phase="done", detail="fake flash")

        def run_check(g, c, carried):
            seq.append(("check", c.id, board.image))
            return True
        eng.flashers["esptool"] = flasher
        eng.run_check = run_check
        eng.place_group = lambda g, items: None
        eng.case_gate = lambda: None
        return eng, seq

    n = 0
    for label, board, read in _x2_test_boards():
        start = board.image
        for name in x2_images:
            n += 1
            board.image = start
            eng, seq = engine("t%03d" % n, board)
            logdir = os.path.join(eng.run_dir, "_bench")
            os.makedirs(logdir)
            keep = _on_board(board)
            try:
                ok, why = eng.ensure_image(name, logdir)
            finally:
                _off_board(keep)
                eng.rec.close()
            assert ok and why.startswith("flashed x2 to %s" % name), \
                (label, name, ok, why)
            assert [x[:2] for x in seq] == [("flash", name)], (label, name, seq)
            fs = [e for e in eng.rec.events if e["kind"] == "flash-start"]
            assert len(fs) == 1 and fs[0]["read_before"] == read, \
                (label, name, fs)
    # at the group level: flash first, then the checks, on the right image;
    # a flash that does not take runs none of them
    g = next(x for x in b.groups if x.name == "b-bridge")
    want = ("app", b.images["bridge"].expect)
    runnable = [c.id for c in g.checks if not c.skip]
    assert runnable, g.checks
    for label, board, read in _x2_test_boards():
        start = board.image
        for take in (True, False):
            n += 1
            board.image = start
            eng, seq = engine("g%03d" % n, board, take=take)
            keep = _on_board(board)
            try:
                with WT.Linux(wroot):
                    eng.run_group(g, list(g.checks), {})
            finally:
                _off_board(keep)
                eng.rec.close()
            flashes = [x for x in seq if x[0] == "flash"]
            assert [x[:2] for x in flashes] == [("flash", "bridge")] and \
                seq[0] == flashes[0], (label, take, seq)
            checks = [x for x in seq if x[0] == "check"]
            res = [r for r in eng.rec.results if r["group"] == g.name]
            if take:
                assert [x[1] for x in checks] == runnable and \
                    all(x[2] == want for x in checks), (label, seq)
                continue
            assert not checks, (label, seq)
            assert len(res) == len(g.checks) and all(
                r["verdict"] == Verdict.NOT_RUN and
                "flashed bridge but identify read" in r["detail"]
                for r in res), (label, res)


# The trees a run imports Python from. Bytecode written there must not make
# the clone dirty, or --resume refuses it (2026-10-20: 20d3's first resume,
# refused for shared/bench/vtrux_bench/__pycache__). From port step 2a
# shared/bench is the pinned checkout ../shared-<tag>/, not this clone's, and
# bytecode there changes its R7 digest (which counts every file but .md and
# .gitignore), so shared_pin refuses the checkout; only this clone's own
# tree is checked here.
PYCACHE_TREES = ("charge-interposer",)
_PYCACHE_DROP = ()      # .gitignore files the check leaves out (mutant hook)


def _pycache_unignored():
    """(paths git would report, folders checked): one __pycache__/*.pyc per
    folder holding a .py file in PYCACHE_TREES, asked of a scratch repo
    holding only this repo's .gitignore files on those paths."""
    repo = os.path.dirname(HERE)
    probe = os.path.join(TMP, "pyc")     # short: Windows' path limit (scratch_dir)
    scratch_dir.remove(probe)
    os.makedirs(probe)
    subprocess.run(["git", "init", "-q", probe], check=True)
    ignores, paths = {".gitignore"}, []
    for tree in PYCACHE_TREES:
        parts = tree.split("/")
        for i in range(1, len(parts)):
            ignores.add("/".join(parts[:i] + [".gitignore"]))
        for d, dirs, files in os.walk(os.path.join(repo, *parts)):
            dirs[:] = [x for x in dirs
                       if x not in (".pio", "__pycache__", ".git")]
            rel = os.path.relpath(d, repo).replace(os.sep, "/")
            if ".gitignore" in files:
                ignores.add(rel + "/.gitignore")
            if any(f.endswith(".py") for f in files):
                paths.append(rel + "/__pycache__/m.cpython-314.pyc")
    for g in sorted(ignores):
        src = os.path.join(repo, *g.split("/"))
        if g in _PYCACHE_DROP or not os.path.isfile(src):
            continue
        dst = os.path.join(probe, *g.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    # NUL-separated: in text mode Windows would turn "\n" into "\r\n" and
    # no path would match.
    r = subprocess.run(["git", "-C", probe, "check-ignore", "--stdin", "-z"],
                       input="\0".join(paths) + "\0", capture_output=True,
                       text=True)
    if r.returncode not in (0, 1):
        raise AssertionError("git check-ignore failed: %s" % r.stderr)
    ignored = set(x for x in r.stdout.split("\0") if x)
    scratch_dir.remove(probe)
    return [p for p in paths if p not in ignored], len(paths)


def test_pycache_ignored():
    missing, n = _pycache_unignored()
    assert n >= 5, "only %d Python folders found: the walk is wrong" % n
    assert not missing, "bytecode git would report: %s" % missing[:4]


# Runs a script up to its argument parsing and no further: parse_args is
# replaced by one that parses, prints ARGV-OK and exits; a bad line exits 2
# with argparse's error. Serial, CAN and USB opens raise, so no line of the
# script can reach the bench even if it does work before parsing.
_ARGV_PROBE = r"""
import argparse, runpy, sys
def _forbidden(*a, **k):
    raise SystemExit("ARGV-PROBE: hardware opened before parse_args")
for mod, names in (("serial", ("Serial", "serial_for_url")),
                   ("can", ("Bus",)), ("can.interface", ("Bus",)),
                   ("usb.core", ("find",))):
    try:
        m = __import__(mod, fromlist=["x"])
    except Exception:
        continue
    for n in names:
        setattr(m, n, _forbidden)
_orig = argparse.ArgumentParser.parse_args
_orig_known = argparse.ArgumentParser.parse_known_args
def _done(r):
    print("ARGV-OK")
    sys.stdout.flush()
    raise SystemExit(0)
_inside = []
def _pa(self, args=None, namespace=None):
    _inside.append(1)
    _done(_orig(self, args, namespace))
def _pk(self, args=None, namespace=None):
    if _inside:
        return _orig_known(self, args, namespace)
    _done(_orig_known(self, args, namespace))
argparse.ArgumentParser.parse_args = _pa
argparse.ArgumentParser.parse_known_args = _pk
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
# Scripts the probe cannot check, because they take no arguments through
# argparse (it would run them whole): each is run with no arguments by its
# check, which test_bench_argv also asserts, and every name here must be
# one the walk met without argparse (no stale entries). reset_check.py
# took argparse with its --serial-log in the move into git (2026-10-26).
NO_ARGPARSE = set()     # keepalive_probe.py has argparse since 2026-11-13


def _drivers():
    """bench_drivers/ in this tree: the bench scripts since port step 0
    (2026-10-26); reverse-it's interposer-firmware/ folder before."""
    d = os.path.join(HERE, "bench_drivers")
    assert os.path.isfile(os.path.join(d, "bench_flight_silence.py")), d
    return d


def _fill(a, s, outdir):
    vals = dict(s, serial_port="COM99", pcan_channel="PCAN_USBBUS1",
                outdir=outdir, wican_state="argv probe")
    return re.sub(r"\{(\w+)\}", lambda m: str(vals.get(m.group(1), "X")), a)


def _bench_argv_problems():
    """[(check id, problem)] for every python check of the bench suite whose
    argument line its own script's parser refuses, the number probed, and
    the script names met without argparse."""
    s = _settings()
    outdir = os.path.join(TMP, "argv-out")
    os.makedirs(outdir, exist_ok=True)
    bad, n, plain = [], 0, set()
    for g in B.bench(s).groups:
        for c in g.checks:
            if c.skip or not c.python or not c.argv:
                continue
            argv = [_fill(str(a), s, outdir) for a in c.argv]
            script = argv[0]
            name = os.path.basename(script)
            if not os.path.isfile(script):
                bad.append((c.id, "no script %s" % script))
                continue
            with open(script, encoding="utf-8", errors="replace") as f:
                src = f.read()
            if "parse_args" not in src and "parse_known_args" not in src:
                plain.add(name)
                if name not in NO_ARGPARSE:
                    bad.append((c.id, "%s has no argparse and is not listed"
                                % name))
                elif len(argv) > 1:
                    bad.append((c.id, "%s takes no arguments, given %s"
                                % (name, argv[1:])))
                continue
            n += 1
            err = _probe(argv)
            if err:
                bad.append((c.id, err))
    return bad, n, plain


def _probe(argv):
    """None if argv[0]'s parser accepts argv[1:], else the error line."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-B", "-c", _ARGV_PROBE] + argv,
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=os.path.dirname(argv[0]))
    if r.returncode == 0 and "ARGV-OK" in r.stdout:
        return None
    lines = (r.stderr + r.stdout).strip().splitlines() or ["rc %d" % r.returncode]
    return lines[-1][:160]


def _probe_controls():
    """The probe must refuse the a25e flight-silence line, accept the fixed
    one, and stop a script that opens a serial port before parsing."""
    fs = os.path.join(_drivers(), "bench_flight_silence.py")
    bad = _probe([fs, "--port", "COM99", "--pcan", "PCAN_USBBUS1"])
    assert bad and "unrecognized arguments" in bad, \
        "the probe accepted the a25e line: %s" % bad
    good = _probe([fs, "--port", "COM99", "--pcan", "--out", "x.txt"])
    assert good is None, "the probe refused the fixed line: %s" % good
    d = os.path.join(TMP, "argv-control")
    os.makedirs(d, exist_ok=True)
    early = os.path.join(d, "opens_first.py")
    with open(early, "w") as f:
        f.write("import argparse, serial\nserial.Serial('COM99')\n"
                "argparse.ArgumentParser().parse_args()\n")
    hw = _probe([early])
    assert hw and "hardware opened before parse_args" in hw, \
        "the probe let a serial open through: %s" % hw


USB_BASELINE = {"noread-boots": "baseline-boots", "close-boots": "baseline-boots",
                "bridge": "bridge"}


def _opt(argv, name):
    argv = [str(a) for a in argv]
    return argv[argv.index(name) + 1] if name in argv else None


def test_usb_baselines():
    """Every bench_usb_host case that scores against a baseline names, by
    --baseline-dir, the folder of an EARLIER check of its group that writes
    that baseline: baseline-boots with the same --ack / --noack for the
    boot cases, bridge --com3 absent for cycles (a25e: none did)."""
    s, su = _all()
    n = 0
    for g in su["bench"].groups:
        seen = {}
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if not argv or not argv[0].endswith("bench_usb_host.py"):
                continue
            case = _opt(argv, "--case")
            mode = "--ack" if "--ack" in argv else "--noack" \
                if "--noack" in argv else _opt(argv, "--com3")
            seen[c.id] = (case, mode)
            needs = case in ("noread-boots", "close-boots") or \
                (case == "bridge" and mode == "cycles")
            bdir = _opt(argv, "--baseline-dir")
            if not needs:
                assert bdir is None, "%s needs no baseline dir" % c.id
                continue
            n += 1
            assert bdir, "%s has no --baseline-dir" % c.id
            m = re.match(r"^\{outdir\}/\.\./([\w.-]+)$", bdir)
            assert m, "%s: --baseline-dir %s is not {outdir}/../<check>" \
                % (c.id, bdir)
            src = m.group(1)
            assert src in seen, "%s reads %s, not an earlier check of %s" \
                % (c.id, src, g.name)
            want = ("baseline-boots", mode) if case != "bridge" \
                else ("bridge", "absent")
            assert seen[src] == want, "%s reads %s, which is %s, not %s" \
                % (c.id, src, seen[src], want)
    assert n == 4, "%d dependent usb-host cases found, 4 expected" % n


# The stop each bench_usb_host case passes. Bridge cycles 600 s, as in the
# 2026-10-14 runs that met the premise (reverse-it interposer-firmware
# runs/bench_usbhost_control_20261014T0528/CODE.txt). Not-reading boots
# 720 s (reviewer ruling 2026-10-22): the host side's capacity is not
# stable (12.6 kB on 2026-10-14, 98.6 kB in 700f), the premise check makes
# a longer stop safe, and 700f's noread-ack was VOID at 480 s with ~539 s
# needed at its rate (runs/noread_rate_compare_20261022.txt). Only these
# cases stop reading.
USB_STOPS = {("noread-boots", "--ack"): "720", ("noread-boots", "--noack"): "720",
             ("bridge", "cycles"): "600"}


def test_usb_stops():
    """Each bench_usb_host case that stops reading passes its ruled stop
    (4cdb: both noread cases VOID at the script's 240 s floor; 700f:
    noread-ack VOID at 480 s), and no other case passes one."""
    s, su = _all()
    found = set()
    for g in su["bench"].groups:
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if not argv or not argv[0].endswith("bench_usb_host.py"):
                continue
            case = _opt(argv, "--case")
            mode = "--ack" if "--ack" in argv else "--noack" \
                if "--noack" in argv else _opt(argv, "--com3")
            want = USB_STOPS.get((case, mode))
            got = _opt(argv, "--stop-s")
            assert got == want, "%s: --stop-s %s, want %s" % (c.id, got, want)
            if want:
                found.add((case, mode))
    assert found == set(USB_STOPS), "stop cases missing: %s" % sorted(
        set(USB_STOPS) - found)


def test_script_coverage():
    """Every bench driver in bench_drivers/ with a --self-test is in
    SELF_TESTS, every script whose --can-fail runs script_can_fail is in
    CAN_FAILS, and the harness suite runs each (4cdb: bench_topology's
    self-test and all five can-fails were run by hand only)."""
    art = _drivers()
    st, cf = set(), set()
    for n in sorted(os.listdir(art)):
        if not n.endswith(".py"):
            continue
        src = open(os.path.join(art, n), encoding="utf-8",
                   errors="replace").read()
        # keepalive_probe has one since 2026-11-13 (kvaser_link)
        if (n.startswith("bench_") or n == "keepalive_probe.py") \
                and '"--self-test"' in src:
            st.add(n[:-3])
        if "script_can_fail.run(" in src and n != "script_can_fail.py":
            cf.add(n[:-3])
    assert st == set(B.SELF_TESTS), "self-tests: found %s, listed %s" % (
        sorted(st ^ set(B.SELF_TESTS)), "SELF_TESTS")
    assert cf == set(B.CAN_FAILS), "can-fails: found %s, listed %s" % (
        sorted(cf ^ set(B.CAN_FAILS)), "CAN_FAILS")
    s, su = _all()
    runs = set()
    for g in su["harness"].groups:
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if c.skip or not argv or not argv[0].endswith(".py"):
                continue
            name = os.path.basename(argv[0])[:-3]
            runs |= {(name, m) for m in ("--self-test", "--can-fail")
                     if m in argv[1:]}
    for n in st:
        assert (n, "--self-test") in runs, "harness never runs %s --self-test" % n
    for n in cf:
        assert (n, "--can-fail") in runs, "harness never runs %s --can-fail" % n


def test_shared_bench_tests():
    """Every shared/bench/tests/test_*.py of the pinned shared/bench is in
    SHARED_BENCH_TESTS, and the harness suite runs each one plain and with
    --can-fail, from the pinned checkout (reviewer, 2026-10-22: until then
    no suite ran them; port step 2a: the pin, not a copy on this branch)."""
    import shared_pin
    pinned = shared_pin.folder("shared/bench")
    assert B.SHARED_BENCH == pinned, (B.SHARED_BENCH, pinned)
    tests = os.path.join(pinned, "tests")
    found = {n for n in os.listdir(tests)
             if n.startswith("test_") and n.endswith(".py")}
    assert found == set(B.SHARED_BENCH_TESTS), \
        "shared/bench tests: found %s, listed %s" % (
            sorted(found), sorted(B.SHARED_BENCH_TESTS))
    s, su = _all()
    runs = set()
    for g in su["harness"].groups:
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if c.skip or not argv or "/shared/bench/tests/" not in \
                    argv[0].replace("\\", "/"):
                continue
            assert os.path.normcase(os.path.dirname(argv[0])) == \
                os.path.normcase(tests.replace(os.sep, "/")), \
                "%s runs from %s, not the pinned %s" % (c.id, argv[0], tests)
            runs.add((os.path.basename(argv[0]), tuple(argv[1:])))
    for n in found:
        for extra in ((), ("--can-fail",)):
            assert (n, extra) in runs, "harness never runs %s %s" % (
                n, " ".join(extra))


def test_harness_py_canfails():
    """Every test in HARNESS_PY_TESTS that takes --can-fail is in
    HARNESS_PY_CANFAILS, every one listed there takes it, and the harness
    suite runs each with it (reviewer, 2026-11-02: scratch_dir_test and
    scratch_run_test had can-fails no suite ran)."""
    takes = set()
    for t in B.HARNESS_PY_TESTS:
        src = open(os.path.join(HERE, t), encoding="utf-8").read()
        if '"--can-fail"' in src:
            takes.add(t)
    assert takes == set(B.HARNESS_PY_CANFAILS), \
        "can-fails: found %s, listed %s" % (
            sorted(takes ^ set(B.HARNESS_PY_CANFAILS)), "HARNESS_PY_CANFAILS")
    s, su = _all()
    runs = {tuple(str(a) for a in c.argv) for g in su["harness"].groups
            for c in g.checks if not c.skip}
    for t in takes:
        assert (t, "--can-fail") in runs, "harness never runs %s --can-fail" % t


def test_reopen_skip():
    """innomaker-rate-runs-reopen stays listed with its argument line, and is
    a skip naming the 2026-10-14 harness defect, so it shows NOT-RUN with
    that reference (reviewer, 2026-10-22; a25e and 4cdb crashed on it)."""
    s, su = _all()
    c = next(c for g in su["bench"].groups for c in g.checks
             if c.id == "innomaker-rate-runs-reopen")
    argv = [str(a) for a in c.argv]
    assert argv[0].endswith("bench_innomaker_clock.py") and \
        _opt(argv, "--between") == "reopen", "the reopen line is gone"
    assert c.skip and "Step 4 no-hands sheet item 1" in c.skip and \
        "Access denied" in c.skip, "not a skip naming the defect: %r" % c.skip


def test_strict_port_off():
    """No suite line passes bench_powerup_round's --strict-port, the ported
    image's no-ACKer rule (u must be 0, port requirement (f)): the current
    product is judged by the default, u <= 1 and t <= 1 independently (user
    ruling 2026-10-22, relayed by the reviewer). bench_usb_host's no-ACKer
    boots score through bench_powerup_round too, with its own options, so
    it must have no such switch either."""
    art = _drivers()
    src = open(os.path.join(art, "bench_powerup_round.py"),
               encoding="utf-8").read()
    assert 'add_argument("--strict-port"' in src, \
        "bench_powerup_round has no --strict-port: this test pins nothing"
    usb = open(os.path.join(art, "bench_usb_host.py"),
               encoding="utf-8").read()
    assert "strict" not in usb, "bench_usb_host can set the strict mode"
    s, su = _all()
    noack = []
    for name, suite in su.items():
        for g in suite.groups:
            for c in g.checks:
                argv = [str(a) for a in c.argv]
                assert not any("strict-port" in a for a in argv), \
                    "%s/%s passes --strict-port" % (name, c.id)
                if argv and argv[0].endswith("bench_powerup_round.py") and \
                        _opt(argv, "--mode") == "noack":
                    noack.append(c.id)
    assert noack == ["powerup-round-noack"], \
        "the no-ACKer power-up check: %s" % noack


def _add_arg(cid, arg):
    def fn(s):
        _check(s, cid).argv.append(arg)
    return fn


def _set_mode(cid, mode):
    def fn(s):
        c = _check(s, cid)
        c.argv[c.argv.index("--mode") + 1] = mode
    return fn


def test_vport_split():
    """vport-listen's two wrong-rate arms run as their own check, which is a
    registered KNOWN FAIL (reviewer, 2026-10-21); the rest stay judged in
    vport-listen; between them every listen arm runs exactly once."""
    sys.path.insert(0, _drivers())
    import bench_vport as V
    s, su = _all()
    checks = {c.id: c for g in su["bench"].groups for c in g.checks}
    arms = {}
    for cid in ("vport-listen", "vport-listen-wrong-rate"):
        c = checks[cid]
        assert _opt(c.argv, "--steps") == "listen", cid
        arms[cid] = V.listen_arm_keys(_opt(c.argv, "--listen-arms") or "all")
    assert arms["vport-listen-wrong-rate"] == ["wrong-250k", "wrong-1m"]
    assert not set(arms["vport-listen"]) & {"wrong-250k", "wrong-1m"}, \
        "a known-fail arm left in the judged check"
    allarms = arms["vport-listen"] + arms["vport-listen-wrong-rate"]
    assert sorted(allarms) == sorted(V.LISTEN_ARMS), \
        "listen arms lost or doubled: %s" % allarms
    kf = {k.id for k in su["bench"].known_fails}
    assert "b-vport/vport-listen-wrong-rate" in kf
    assert "b-vport/vport-listen" not in kf


def test_bench_argv():
    _probe_controls()
    bad, n, plain = _bench_argv_problems()
    assert n >= 40, "only %d checks probed: the walk is wrong" % n
    assert plain == NO_ARGPARSE, "scripts without argparse: met %s, listed %s" \
        % (sorted(plain), sorted(NO_ARGPARSE))
    assert not bad, "argument lines their scripts refuse: %s" % bad[:4]


# What a check may not name since the scripts moved into git (port step 0,
# 2026-10-26): the reverse-it folder they ran from, and its staged copy.
OUT_OF_TREE = ("{interposer_artifacts}", "{reverse_it}", "{work}/art",
               "interposer-firmware")


def _script_of(c):
    """The script a check runs, or None (a bare command, a skip)."""
    argv = [str(a) for a in c.argv]
    if c.skip or not argv or argv == ["-"]:
        return None
    if argv[0] == "bash":
        return argv[1]
    if c.python or argv[0].endswith((".py", ".sh")):
        return argv[0]
    return None


def test_scripts_in_tree():
    """Every script a check of any suite runs is a file in this tree: local
    checks by {interposer_code}/..., madhouse checks relative to the shipped
    code ({work}/code); none names reverse-it's interposer-firmware/ folder,
    in its argv or its cwd, and no group ships the old "art" staging."""
    s, su = _all()
    n = 0
    for name, suite in su.items():
        for g in suite.groups:
            assert "art" not in (g.ship or {}), "%s/%s ships art" % (name, g.name)
            for c in g.checks:
                text = " ".join(str(a) for a in c.argv) + " " + str(c.cwd or "")
                hit = [m for m in OUT_OF_TREE if m in text]
                assert not hit, "%s/%s names %s: %s" % (name, c.id, hit, text[:120])
                script = _script_of(c)
                if script is None:
                    continue
                if script.startswith("{interposer_code}/"):
                    path = os.path.join(HERE, script[len("{interposer_code}/"):])
                elif g.host == "madhouse" and not script.startswith("{"):
                    assert c.cwd == "{work}/code", "%s/%s cwd %s" % (
                        name, c.id, c.cwd)
                    path = os.path.join(HERE, script)
                elif g.host != "madhouse" and os.path.normcase(
                        os.path.dirname(os.path.normpath(script))) == \
                        os.path.normcase(os.path.join(B.SHARED_BENCH, "tests")):
                    # the pinned shared/bench's own tests (port step 2a),
                    # from its read-only checkout, checked by the pin
                    path = script
                else:
                    raise AssertionError("%s/%s runs %s, not from this tree"
                                         % (name, c.id, script))
                assert os.path.isfile(path), "%s/%s: no %s" % (name, c.id, path)
                n += 1
    # the walk saw the moved scripts, not only the ones already in git
    assert n >= 150, "only %d scripts checked: the walk is wrong" % n


def test_can_tools_record():
    """canbench is the pinned can-tools commit's (can_tools_pin.py; user
    ruling 2026-10-30, via the reviewer), so the bench run names the pin,
    the folder it was written to and the clone, and records a refusal
    with the pin still named (a stand-in clone,
    can_tools_pin_test.fixture)."""
    import can_tools_pin_test as CT
    base = os.path.join(TMP, "ctp")      # short: Windows' path limit (scratch_dir)
    scratch_dir.remove(base)
    try:
        fix = CT.fixture(base)
        with CT.pointed(fix) as ctp:
            ctp.install()
            s, su = _all()
            ct = su["bench"].code_info(None).get("can_tools")
            assert ct == {"repo": CT.REPO, "commit": fix["commit"],
                          "folder": fix["folder"],
                          "clone": os.path.normpath(fix["clone"])}, ct
            with open(os.path.join(fix["folder"], "canbench",
                                   "__init__.py"), "a") as fh:
                fh.write("# by hand\n")
            ct = su["bench"].code_info(None).get("can_tools") or {}
            assert ct.get("commit") == fix["commit"] and "folder" not in ct \
                and "is not can-tools" in ct.get("error", ""), ct
            scratch_dir.remove(fix["folder"])
            ct = su["bench"].code_info(None).get("can_tools") or {}
            assert ct.get("commit") == fix["commit"] \
                and "not installed" in ct.get("error", ""), ct
            for k in ("emulated", "harness"):
                assert "can_tools" not in su[k].code_info(None), k
            os.environ.pop("CAN_TOOLS_DIR")
            ct = su["bench"].code_info(None).get("can_tools") or {}
            assert "CAN_TOOLS_DIR is not set" in ct.get("error", "") \
                and ct.get("commit") == fix["commit"] \
                and "folder" not in ct, ct
    finally:
        scratch_dir.remove(base)


def _under(path, top):
    a, b = (os.path.normcase(os.path.realpath(x)) for x in (path, top))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def test_runner_stage():
    td = scratch_dir.tool_dir("runner-stage")
    os.makedirs(td, exist_ok=True)
    # a run killed before its exit handler: this pid with another start
    killed = tempfile.mkdtemp(prefix="runner-stage-killed-", dir=td)
    with open(os.path.join(killed, scratch_dir.OWNER), "w",
              encoding="utf-8") as f:
        json.dump(dict(scratch_dir.owner_record(), start=-1), f)
    assert scratch_dir.state(killed) == "finished", killed
    head = subprocess.run(["git", "-C", os.path.dirname(HERE), "rev-parse",
                           "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    B._STAGED.pop(head, None)
    exits = []
    keep = B.atexit
    B.atexit = types.SimpleNamespace(
        register=lambda fn, *a: exits.append((fn, a)))
    run = None
    try:
        d, commit = B._stage_git_bundle(HERE)
        d2, _c = B._stage_git_bundle(HERE)
        run = os.path.dirname(d)
        assert commit == head, (commit, head)
        assert not _under(d, tempfile.gettempdir()), \
            "the bundle is in the system temp folder: %s" % d
        assert os.path.basename(d) == "gitb" and _under(run, td) \
            and os.path.dirname(os.path.realpath(run)) == os.path.realpath(td), \
            "the bundle is not in gitb/ of a runner-stage run folder: %s" % d
        o = scratch_dir.owner(run) or {}
        assert scratch_dir.state(run) == "live" and o.get("pid") == os.getpid(), \
            "the run folder is not this process's: %s %s" % (run, o)
        r = subprocess.run(["git", "-C", os.path.dirname(HERE), "bundle",
                            "list-heads", os.path.join(d, "repo.bundle")],
                           capture_output=True, text=True)
        assert r.returncode == 0 and head in r.stdout, (r.stdout, r.stderr)
        assert d2 == d, "a second bundle for the same commit: %s, %s" % (d, d2)
        assert not os.path.isdir(killed), \
            "a killed run's folder was not pruned: %s" % killed
        assert exits == [(scratch_dir.remove, (run,))], \
            "removal at exit not registered once for %s: %s" % (run, exits)
        exits[0][0](*exits[0][1])
        assert not os.path.isdir(run), "the exit handler left %s" % run
    finally:
        B.atexit = keep
        B._STAGED.pop(head, None)
        for x in [killed, run] + [a[0] for _f, a in exits]:
            if x and os.path.isdir(x):
                scratch_dir.remove(x)


TESTS = [test_script_judge, test_recorded_judge, test_scenario_judge,
         test_corrupt_pin_judge, test_c12_judge, test_switch_pending_judge,
         test_host_bridge_judge,
         test_suites, test_interposer_images, test_list_build_images, test_idf_log_checks,
         test_mutant_wiring,
         test_identify, test_pycache_ignored,
         test_usb_baselines, test_usb_stops, test_reopen_skip,
         test_script_coverage, test_shared_bench_tests,
         test_harness_py_canfails, test_strict_port_off,
         test_vport_split, test_bench_argv, test_wican_board,
         test_scripts_in_tree, test_can_tools_record, test_runner_stage,
         test_m_lanes_host, test_idle_safe, test_canchg_env, test_esptool_env,
         test_canchg_mode_hooks, test_canx2_slcan_hooks,
         test_x2_test_images_identify, test_slcan_spi_identity,
         test_x2_test_image_flash_first]


# ---- can-fail -----------------------------------------------------------
def _pass_all(run):
    return Result(Verdict.PASS, detail="mutant")


def _hb_unreported_pass(run):
    out = _ORIG["host_bridge_judge"](run)
    return [Result(Verdict.PASS, x.sub, detail=x.detail)
            if x.verdict == Verdict.ERROR else x for x in out]


def _hb_list_taken(run):
    """Any test-list ERROR taken as a list."""
    return [Result(Verdict.PASS, x.sub, detail=x.detail) if x.sub == "test-list" else x
            for x in _ORIG["host_bridge_judge"](run)]


def _hb_unlisted_ignored(run):
    return [x for x in _ORIG["host_bridge_judge"](run)
            if not x.detail.startswith("not listed: ")]


def _hb_missing_as_fail(run):
    """The reading before (B): a listed test with no result line is FAIL."""
    return [Result(Verdict.FAIL, x.sub, detail=x.detail)
            if x.detail.startswith("no result") else x
            for x in _ORIG["host_bridge_judge"](run)]


def _hb_exit_ignored(run):
    """The closing line's exit ignored: the OK line alone passes."""
    return [Result(Verdict.PASS, x.sub, detail=x.detail)
            if x.detail.endswith("its 'all checks OK' line too") else x
            for x in _ORIG["host_bridge_judge"](run)]


def _hb_list_from_results(text):
    """No list printed: the tests taken from the result lines (a list that
    can only agree with the run)."""
    names, why = _ORIG["_hb_list"](text)
    if why and why.startswith("no test list"):
        return sorted(set(re.findall(r"^(test_\w+): all checks OK", text, re.M))), None
    return names, why


def _hb_list_deduped(text):
    found = B.HB_LIST_RE.findall(text)
    if len(found) == 1:
        names = list(dict.fromkeys(found[0][1].split()))
        if names and len(names) != len(found[0][1].split()):
            return names, None
    return _ORIG["_hb_list"](text)


def _hb_list_first(text):
    """Two lists: the first taken."""
    found = B.HB_LIST_RE.findall(text)
    if len(found) > 1:
        return _ORIG["_hb_list"](text.split("\n", 1)[0] + "\n")
    return _ORIG["_hb_list"](text)


def _hb_list_count_ignored(text):
    found = B.HB_LIST_RE.findall(text)
    if len(found) == 1 and found[0][1].split():
        return _ORIG["_hb_list"]("host_bridge tests (%d): %s\n" % (
            len(found[0][1].split()), found[0][1]))
    return _ORIG["_hb_list"](text)


def _scen_not_exercised_fail(run):
    r = _ORIG["scenario_judge"](run)
    return Result(Verdict.FAIL, detail=r.detail) if r.verdict == Verdict.VOID else r


def _scen_ignores_rc(run):
    r = _ORIG["scenario_judge"](run)
    if r.verdict == Verdict.FAIL and "-> PASS" in r.detail:
        r.verdict = Verdict.PASS
    return r


def _c12_counts_c9(run):
    import re
    out = os.path.join(run.outdir, "%s.txt" % run.check.id)
    if not os.path.exists(out):
        return Result(Verdict.ERROR)
    text = open(out, encoding="utf-8").read()
    if re.search(r"<-- a DATASHEET line", text):
        return Result(Verdict.FAIL, detail="any DATASHEET line")
    return Result(Verdict.PASS)


def _wrap_suite(name, fn):
    def factory(settings):
        s = _ORIG[name](settings)
        fn(s)
        return s
    return factory


def _drop_requires(s):
    s.requires = None


def _no_shared_ship(s):
    for g in s.groups:
        if g.ship:
            g.ship = {k: v for k, v in g.ship.items() if k != "shared"}


def _no_fake_dir(s):
    for g in s.groups:
        for c in g.checks:
            if c.id == "host-bridge":
                c.env = {k: v for k, v in c.env.items() if k != "TWAI_FAKE_DIR"}


def _harness_files(root):
    return B.product_files(root) + ["charge-interposer/run_scenario.py"]


def _no_machine(root):
    return [p for p in B.product_files(root) if not p.endswith("machine.py")]


def _product_in_harness(s):
    s.product_files = B.product_files


def _no_chip(s):
    for im in s.images.values():
        im.params.pop("chip", None)


def _sim_into_harness(s):
    s.groups[0].checks.append(B.Check("sim.extra", ["run_scenario.py", "x"],
                                      python=True, cls="wallclock"))


def _mutant_into_emulated(s):
    s.groups[0].checks.append(B.Check("x-mutations", ["bash", "x_mutations.sh"]))


def _bare_skip(s):
    s.groups[-1].checks.append(B.Check("later", ["-"], skip="later"))


def _plain_judges(s):
    for g in s.groups:
        for c in g.checks:
            if c.judge is _ORIG["corrupt_pin_judge"]:
                c.judge = B.script_judge


def _identify_ignores_product(image, ctx, logdir, settings):
    I = B.bench_identify
    got, _p = I.read_7f7(settings, logdir, "t")
    if got is not None:
        return got
    got, _p = I.read_hello(settings, logdir, "t")
    if got is not None:
        return got
    return I.read_slcan(settings, logdir, "t")


# mutants of the X2 test-image tests (item 4)
def _slcan_raises(settings, logdir, tag):
    got = _ORIG["bench_identify.read_slcan"](settings, logdir, tag)
    if got is None:
        raise B.bench_identify.IdentifyError("nothing answered V or N")
    return got


def _slcan_unanchored(settings, logdir, tag):
    """read_slcan with V and N matched anywhere in the reply, not as the
    whole answer before its CR."""
    I = B.bench_identify
    s = I._serial(settings)
    got = {}
    try:
        s.write(b"\r")
        s.flush()
        I.time.sleep(0.3)
        s.read(4096)
        for cmd in (b"V", b"N"):
            s.write(cmd + b"\r")
            s.flush()
            I.time.sleep(0.4)
            got[cmd] = s.read(4096)
    finally:
        s.close()
    v = re.search(rb"V(\d{4})", got[b"V"])
    n = re.search(rb"N(\w{4})", got[b"N"])
    if v and n:
        return "slcan:V%s:N%s" % (v.group(1).decode(), n.group(1).decode())
    return None


# mutants of slcan_spi's identity read (test_slcan_spi_identity)
def _spi_read(v_re, n_re, compare=True, form=lambda g: b":".join(g)):
    """read_slcan with slcan_spi's identity parsed by v_re and n_re,
    compared or not, and formed by form; the V0101 read unchanged."""
    def read(settings, logdir, tag):
        I = B.bench_identify
        s = I._serial(settings)
        got = {}
        try:
            s.write(b"\r")
            s.flush()
            I.time.sleep(0.3)
            s.read(4096)
            for cmd in (b"V", b"N"):
                s.write(cmd + b"\r")
                s.flush()
                I.time.sleep(0.4)
                got[cmd] = s.read(4096)
        finally:
            s.close()
        v = re.search(v_re, got[b"V"])
        n = re.search(n_re, got[b"N"])
        if v and n and (not compare or v.groups() == n.groups()):
            return form(v.groups()).decode()
        v = re.search(rb"V(\d{4})\r", got[b"V"])
        n = re.search(rb"N(\w{4})\r", got[b"N"])
        if v and n:
            return "slcan:V%s:N%s" % (v.group(1).decode(), n.group(1).decode())
        return None
    return read


_SPI_OK = rb"(slcan_spi\w*_NOT_FOR_VEHICLE) (\S+) elf=([0-9a-f]{64})\r"
_SPI_A = rb"(?:^|[\r\n\x07])"
_SPI_V_OK, _SPI_N_OK = _SPI_A + rb"V0100 " + _SPI_OK, _SPI_A + rb"N" + _SPI_OK
_spi_v_only = _spi_read(_SPI_V_OK, _SPI_N_OK, compare=False)
_spi_any_name = _spi_read(
    _SPI_A + rb"V0100 (slcan_spi\w*) (\S+) elf=([0-9a-f]{64})\r",
    _SPI_A + rb"N(slcan_spi\w*) (\S+) elf=([0-9a-f]{64})\r")
_spi_any_elf = _spi_read(
    _SPI_A + rb"V0100 (slcan_spi\w*_NOT_FOR_VEHICLE) (\S+) elf=([0-9a-f]+)\r",
    _SPI_A + rb"N(slcan_spi\w*_NOT_FOR_VEHICLE) (\S+) elf=([0-9a-f]+)\r")
_spi_unanchored = _spi_read(_SPI_OK, _SPI_OK)
_spi_name_only = _spi_read(_SPI_V_OK, _SPI_N_OK, form=lambda g: g[0])


def _identify_unplaced_expected(image, ctx, logdir, settings):
    got = _ORIG["bench_identify.identify"](image, ctx, logdir, settings)
    return image.expect if got is None else got


def _identify_unplaced_raises(image, ctx, logdir, settings):
    got = _ORIG["bench_identify.identify"](image, ctx, logdir, settings)
    if got is None:
        raise B.bench_identify.IdentifyError("no image identify knows")
    return got


def _x2_trusts_expect(s):
    hook = s.identify

    def identify(image, ctx, logdir):
        if image.board == "x2":
            return image.expect
        return hook(image, ctx, logdir)
    s.identify = identify


def _x2_trusts_flash(s):
    """The X2 read off the board once; every later identify takes the
    image it is asked about as read (a flash taken as read back)."""
    hook, seen = s.identify, set()

    def identify(image, ctx, logdir):
        if image.board == "x2" and ctx.get("run_id") in seen:
            return image.expect
        seen.add(ctx.get("run_id"))
        return hook(image, ctx, logdir)
    s.identify = identify


import re as _re                                          # noqa: E402
_OLD_PRODUCT_RE = _re.compile(r"serial>.*\b(MODE|spec 2\.3)|^diag st=|"
                              r"\bfw=\d+ schema=\d+")
_ORIG_SCRIPT = B.script_judge
def _target(attr):
    """(module, name) for a mutant attribute, "mod.name" or a name in
    bench_suite."""
    if "." in attr:
        mod, name = attr.split(".", 1)
        if mod == "self":
            return sys.modules[__name__], name
        return getattr(B, mod), name
    return B, attr


_ORIG = {n: getattr(B, n) for n in ("script_judge", "recorded_judge",
                                    "scenario_judge", "c12_judge",
                                    "switch_pending_judge", "corrupt_pin_judge",
                                    "host_bridge_judge", "emulated", "bench",
                                    "harness", "product_files")}
_ORIG["_hb_list"] = B._hb_list
_ORIG["_Build.mgroup"] = B._Build.mgroup
_ORIG["_Build.mpy"] = B._Build.mpy
_ORIG["M_LANES_HOSTS"] = B.M_LANES_HOSTS
_ORIG["canchg_env"] = B.canchg_env
_ORIG["esptool_env"] = B.esptool_env
_ORIG["canchg_mode.hooks"] = B.canchg_mode.hooks
_ORIG["canx2_slcan.hooks"] = B.canx2_slcan.hooks


def _x2_unwired(on_start, on_end, **kw):
    return on_start, on_end


def _x2_after_hook(on_start, on_end, **kw):
    X = B.canx2_slcan

    def s(b, img, ctx, logdir):
        if b != X.X2_BOARD:
            return on_start(b, img, ctx, logdir)
        theirs = X._call(on_start, b, img, ctx, logdir)
        return X._both(X.start(logdir), theirs)
    return s, on_end


def _x2_on_wican(on_start, on_end, **kw):
    return _ORIG["canx2_slcan.hooks"](on_start, on_end, board=B.bench_wican.BOARD, **kw)


def _x2_also_at_end(on_start, on_end, **kw):
    s, _e = _ORIG["canx2_slcan.hooks"](on_start, on_end, **kw)
    return s, _ORIG["canx2_slcan.hooks"](on_end, on_end, **kw)[0]


def _x2_failure_ignored(on_start, on_end, **kw):
    s, e = _ORIG["canx2_slcan.hooks"](on_start, on_end, **kw)

    def s2(b, img, ctx, logdir):
        out = s(b, img, ctx, logdir)
        out["ok"] = True
        return out
    return s2, e


def _x2_on_start_anyway(on_start, on_end, **kw):
    """The hook given run after a failed detach too (before the reviewer's
    fix of e284dd75)."""
    X = B.canx2_slcan

    def s(b, img, ctx, logdir):
        if b != X.X2_BOARD:
            return on_start(b, img, ctx, logdir)
        mine = X.start(logdir)
        return X._both(mine, X._call(on_start, b, img, ctx, logdir))
    return s, on_end


def _hooks_start_only(on_start, on_end, environ, **kw):
    return _ORIG["canchg_mode.hooks"](on_start, on_end, environ, **kw)[0], on_end


def _hooks_end_only(on_start, on_end, environ, **kw):
    return on_start, _ORIG["canchg_mode.hooks"](on_start, on_end, environ, **kw)[1]


def _hooks_no_device(on_start, on_end, environ, **kw):
    return _ORIG["canchg_mode.hooks"](on_start, on_end, {}, **kw)


def _hooks_on_wican(on_start, on_end, environ, **kw):
    return _ORIG["canchg_mode.hooks"](on_start, on_end, environ,
                                      board=B.bench_wican.BOARD, **kw)


def _esptool_unchecked(settings):
    argv = (settings.get("esptool") or {}).get("argv")
    return {} if argv is None else {B.ESPTOOL_ENV: json.dumps(argv)}


def _esptool_space_joined(settings):
    out = _ORIG["esptool_env"](settings)
    return out and {B.ESPTOOL_ENV: " ".join(settings["esptool"]["argv"])}


def _canchg_unchecked(settings):
    d = settings.get("canchg_device")
    return {} if d is None else {B.CANCHG_ENV: "%s:%s:%s" % (
        d["vid"].lower(), d["pid"].lower(), d["serial"])}


def _canchg_case_kept(settings):
    d = settings.get("canchg_device")
    out = _ORIG["canchg_env"](settings)
    return out and {B.CANCHG_ENV: "%s:%s:%s" % (d["vid"], d["pid"], d["serial"])}


def _canchg_dropped_from(group, key=None):
    def fn(s):
        for g in s.groups:
            if g.name == group:
                for c in g.checks:
                    c.env.pop(key or B.CANCHG_ENV, None)
    return fn


def _mgroup_always_madhouse(self, name, checks):
    return B.Group(name, host="madhouse", ship=self.ship, checks=checks)


def _mgroup_always_local(self, name, checks):
    return B.Group(name, host="local", ship=None, checks=checks)


def _mgroup_ships_locally(self, name, checks):
    return B.Group(name, host=self.m_host, ship=self.ship, checks=checks)


def _mpy_work_cwd(self, cid, argv, **kw):
    kw.setdefault("cwd", "{work}/code")
    return _ORIG["_Build.mpy"](self, cid, argv, **kw)
_ORIG["bench_identify.PRODUCT_RE"] = B.bench_identify.PRODUCT_RE
_ORIG["bench_identify.identify"] = B.bench_identify.identify
_ORIG["bench_identify.read_slcan"] = B.bench_identify.read_slcan
_ORIG["bench_identify.SPI_V_RE"] = B.bench_identify.SPI_V_RE
_ORIG["self._PYCACHE_DROP"] = _PYCACHE_DROP
_ORIG["self.NO_ARGPARSE"] = NO_ARGPARSE
_ORIG["self._ARGV_PROBE"] = _ARGV_PROBE
_ORIG["SELF_TESTS"] = B.SELF_TESTS
_ORIG["CAN_FAILS"] = B.CAN_FAILS
_ORIG["SHARED_BENCH_TESTS"] = B.SHARED_BENCH_TESTS
_ORIG["SHARED_BENCH"] = B.SHARED_BENCH
_ORIG["HARNESS_PY_CANFAILS"] = B.HARNESS_PY_CANFAILS
_ORIG["can_tools_info"] = B.can_tools_info
_ORIG["_stage_git_bundle"] = B._stage_git_bundle
_ORIG["_remove_at_exit"] = B._remove_at_exit
_ORIG["_STAGED"] = B._STAGED
_ORIG["scratch_dir.make"] = scratch_dir.make
_ORIG["_interposer_image"] = B._interposer_image
_ORIG["INTERPOSER_IDF"] = B.INTERPOSER_IDF
_ORIG["_md5"] = B._md5
_ORIG["_sha256"] = B._sha256


def _ct_unchecked():
    """The pin's folder recorded without the pin's checks."""
    p = B.can_tools_pin.pin()
    return {"repo": p["repo"], "commit": p["commit"],
            "folder": B.can_tools_pin.folder(p),
            "clone": os.path.normpath(os.environ.get("CAN_TOOLS_DIR", ""))}


def _ct_no_pin():
    r = _ORIG["can_tools_info"]()
    r.pop("commit", None)
    return r


def _ct_no_refusal():
    r = _ORIG["can_tools_info"]()
    r.pop("error", None)
    return r


def _bench_without_can_tools(s):
    info = s.code_info

    def no_ct(ctx):
        out = info(ctx)
        out.pop("can_tools", None)
        return out
    s.code_info = no_ct


def _drop_harness_check(cid):
    def fn(s):
        for g in s.groups:
            g.checks = [c for c in g.checks if c.id != cid]
    return fn


def _rc0_pass(run):
    """The pre-a25e judge: exit code 0 is PASS whatever was printed."""
    if run.rc == 0:
        return Result(Verdict.PASS, detail="rc=0")
    return _ORIG["script_judge"](run)


def _any_fail_text(run):
    """A FAIL anywhere in the log beats a VOID line, as if a faulted arm's
    scorer lines were read (bench_vport before its "not judged" marking)."""
    if run.rc not in (0, None) and "FAIL" in run.log_text():
        return Result(Verdict.FAIL, detail="FAIL text")
    return _ORIG["script_judge"](run)


def _check(s, cid):
    return next(c for g in s.groups for c in g.checks if c.id == cid)


def _drop_baseline_dir(cid):
    def fn(s):
        c = _check(s, cid)
        i = c.argv.index("--baseline-dir")
        del c.argv[i:i + 2]
    return fn


def _baseline_from(cid, src):
    def fn(s):
        c = _check(s, cid)
        c.argv[c.argv.index("--baseline-dir") + 1] = "{outdir}/../" + src
    return fn


def _set_stop(cid, value):
    """value None drops --stop-s; otherwise sets it (adding it if absent)."""
    def fn(s):
        c = _check(s, cid)
        if "--stop-s" in c.argv:
            i = c.argv.index("--stop-s")
            del c.argv[i:i + 2]
        if value is not None:
            c.argv[1:1] = ["--stop-s", value]
    return fn


def _unskip(cid):
    def fn(s):
        _check(s, cid).skip = None
    return fn


def _skip_reason(cid, reason):
    def fn(s):
        _check(s, cid).skip = reason
    return fn


def _wrong_rate_back(s):
    c = _check(s, "vport-listen")
    c.argv[c.argv.index("--listen-arms") + 1] = "all"


def _arm_lost(s):
    c = _check(s, "vport-listen")
    c.argv[c.argv.index("--listen-arms") + 1] = "no-ack,idle,flood-7F4,flood-649"


def _known_fail_dropped(s):
    s.known_fails = [k for k in s.known_fails
                     if k.id != "b-vport/vport-listen-wrong-rate"]


def _flight_silence_a25e(s):
    """The a25e line: a value after the --pcan flag."""
    for g in s.groups:
        for c in g.checks:
            if c.id == "flight-silence":
                i = c.argv.index("--pcan")
                c.argv.insert(i + 1, "{pcan_channel}")

def _artifacts_script(s):
    """A bench check run from reverse-it's folder again (before port step 0)."""
    _check(s, "hold12").argv[0] = "{interposer_artifacts}/bench_hold12.py"


def _reverse_it_cwd(s):
    _check(s, "st.bench-vport").cwd = "{reverse_it}"


def _staged_art(s):
    for g in s.groups:
        if g.name == "m-mutants":
            g.ship = dict(g.ship, art="art")


def _missing_script(s):
    _check(s, "l2-mutations").argv[1] = "harness_checks/no_such_mutations.sh"


def _stage_kept(code):
    """The bundle as before 2026-10-31: a plain folder per commit, kept,
    no owner (here under TMP, not the system temp folder)."""
    repo = os.path.dirname(code)
    commit = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True,
                            check=True).stdout.strip()
    d = os.path.join(TMP, "runner-stage", "gitb-" + commit[:12])
    os.makedirs(d, exist_ok=True)
    subprocess.run(["git", "-C", repo, "bundle", "create",
                    os.path.join(d, "repo.bundle"), "HEAD"],
                   capture_output=True, check=True)
    return d, commit


class _NoStore(dict):
    def __setitem__(self, k, v):
        pass


def _make_no_prune(tool, start=None):
    d = scratch_dir.tool_dir(tool, start)
    os.makedirs(d, exist_ok=True)
    p = tempfile.mkdtemp(prefix="%d-" % os.getpid(), dir=d)
    with open(os.path.join(p, scratch_dir.OWNER), "w", encoding="utf-8") as f:
        json.dump(scratch_dir.owner_record(), f)
    return p


# test -> [(module attribute, replacement, what the mutant does)]
def _no_wican_in(group):
    def fn(s):
        g = [x for x in s.groups if x.name == group][0]
        g.image = [x for x in g.image_list if x != B.bench_wican.IMAGE][0]
    return fn


def _wican_marked_unsafe(s):
    s.images[B.bench_wican.IMAGE].idle_safe = False


def _vport_left_safe(s):
    s.images["vport_bench"].idle_safe = True


def _bridge_marked_unsafe(s):
    s.images["bridge"].idle_safe = False


def _no_idle_safe_x2(s):
    s.idle_safe_images = {}


def _witness_idle_safe_image(s):
    s.idle_safe_images = {"x2": "witness"}


def _standing_back(s):
    s.standing_images = {"x2": "bridge"}


def _no_release_hook(s):
    s.on_end = None


def _wican_flasher_not_the_load(s):
    s.flashers[B.bench_wican.FLASHER] = lambda *a, **k: dict(
        ok=False, phase="refused", recoverable=False, detail="never flashes")


def _wican_identify_off(s):
    x2_identify = s.identify
    s.identify = lambda image, ctx, logdir: (
        None if image.board == B.bench_wican.BOARD
        else x2_identify(image, ctx, logdir))


MUTANTS = {
    "test_esptool_env": [
        ("esptool_env", lambda settings: {}, "esptool.argv never passed on"),
        ("esptool_env", _esptool_unchecked, "a malformed esptool.argv passed on"),
        ("esptool_env", _esptool_space_joined,
         "the argv passed space-joined, not as JSON"),
        ("bench", _wrap_suite("bench", _canchg_dropped_from("b-c12", B.ESPTOOL_ENV)),
         "one bench group's checks without it")],
    "test_canchg_env": [
        ("canchg_env", lambda settings: {}, "canchg_device never passed on"),
        ("canchg_env", _canchg_unchecked, "a malformed canchg_device passed on"),
        ("canchg_env", _canchg_case_kept, "vid and pid passed on in the settings' case"),
        ("bench", _wrap_suite("bench", _canchg_dropped_from("b-c12")),
         "one bench group's checks without it")],
    "test_idle_safe": [
        ("bench", _wrap_suite("bench", _vport_left_safe),
         "vport_bench left idle-safe"),
        ("bench", _wrap_suite("bench", _bridge_marked_unsafe),
         "the bridge marked not idle-safe"),
        ("bench", _wrap_suite("bench", _no_idle_safe_x2),
         "no idle-safe image for the X2"),
        ("bench", _wrap_suite("bench", _witness_idle_safe_image),
         "the witness named the X2's idle-safe image"),
        ("bench", _wrap_suite("bench", _standing_back),
         "a standing image named again")],
    "test_m_lanes_host": [
        ("_Build.mgroup", _mgroup_always_madhouse,
         "m_lanes_host local ignored: the m-lanes still over ssh"),
        ("_Build.mgroup", _mgroup_always_local,
         "the Windows settings' m-lanes moved off the ssh route"),
        ("_Build.mgroup", _mgroup_ships_locally,
         "a local m-lane still ships the copy"),
        ("_Build.mpy", _mpy_work_cwd,
         "a local m-check left in {work}/code"),
        ("M_LANES_HOSTS", ("madhouse", "local", "ssh", "", "Local", "madhouse-debian"),
         "any m_lanes_host taken")],
    "test_wican_board": [
        ("bench", _wrap_suite("bench", _no_wican_in("b-vport")),
         "a bench group without the WiCAN"),
        ("bench", _wrap_suite("bench", _wican_marked_unsafe),
         "the WiCAN image marked not idle-safe"),
        ("bench", _wrap_suite("bench", _no_release_hook),
         "no release hook (the WiCAN unread at every release)"),
        ("bench", _wrap_suite("bench", _wican_identify_off),
         "identify never reads the WiCAN"),
        ("bench", _wrap_suite("bench", _wican_flasher_not_the_load),
         "the WiCAN's flasher is not bench_wican's load")],
    "test_canchg_mode_hooks": [
        ("canchg_mode.hooks", _hooks_start_only,
         "the canchg end step left out of the bench suite's end hook"),
        ("canchg_mode.hooks", _hooks_end_only,
         "the canchg start step left out of the bench suite's start hook"),
        ("canchg_mode.hooks", _hooks_no_device,
         "the hooks not given the settings' canchg_device"),
        ("canchg_mode.hooks", _hooks_on_wican,
         "the canchg step on the WiCAN, not the X2")],
    "test_canx2_slcan_hooks": [
        ("canx2_slcan.hooks", _x2_unwired,
         "the canx2 detach left out of the bench suite's start hook"),
        ("canx2_slcan.hooks", _x2_after_hook,
         "the canx2 detach after canchg's step and the WiCAN's hook"),
        ("canx2_slcan.hooks", _x2_on_wican,
         "the canx2 detach on the WiCAN, not the X2"),
        ("canx2_slcan.hooks", _x2_also_at_end,
         "a canx2 step in the end hook too (nothing is put back)"),
        ("canx2_slcan.hooks", _x2_failure_ignored,
         "a failed canx2 detach not failing the X2's start hook"),
        ("canx2_slcan.hooks", _x2_on_start_anyway,
         "the rest of the X2's start hook run after a failed canx2 detach")],
    "test_script_judge": [
        ("script_judge", _pass_all, "passes everything"),
        ("script_judge", _rc0_pass,
         "rc 0 is PASS whatever it printed (a25e noread-boots)"),
        ("script_judge", _any_fail_text,
         "any FAIL text beats a VOID line (bench_vport's TEST FAULT, c40b)")],
    "test_usb_baselines": [
        ("bench", _wrap_suite("bench", _drop_baseline_dir("usb-host-close-ack")),
         "close-ack without --baseline-dir (a25e)"),
        ("bench", _wrap_suite("bench",
                              _drop_baseline_dir("usb-host-bridge-cycles")),
         "bridge-cycles without --baseline-dir (a25e)"),
        ("bench", _wrap_suite("bench", _baseline_from(
            "usb-host-noread-noack", "usb-host-baseline-ack")),
         "noread-noack reads the ACKed baseline"),
        ("bench", _wrap_suite("bench", _baseline_from(
            "usb-host-noread-ack", "usb-host-close-ack")),
         "noread-ack reads a later check's folder")],
    "test_usb_stops": [
        ("bench", _wrap_suite("bench", _set_stop("usb-host-noread-ack", None)),
         "noread-ack at the script's 240 s floor (4cdb)"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-noread-noack", "240")),
         "noread-noack stops 240 s"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-noread-ack", "480")),
         "noread-ack stops 480 s (700f VOID)"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-noread-noack", "480")),
         "noread-noack stops 480 s"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-bridge-cycles", "480")),
         "bridge-cycles stops 480 s"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-close-ack", "480")),
         "a stop on a case that never stops reading")],
    "test_script_coverage": [
        ("SELF_TESTS", [n for n in B.SELF_TESTS if n != "bench_topology"],
         "bench_topology's self-test unlisted (4cdb)"),
        ("CAN_FAILS", [n for n in B.CAN_FAILS if n != "bench_switch_loss"],
         "bench_switch_loss's can-fail unlisted"),
        ("CAN_FAILS", B.CAN_FAILS + ["bench_switch_errors"],
         "a listed can-fail the script does not have"),
        ("harness", _wrap_suite("harness", _drop_harness_check(
            "cf.bench-usb-host")), "listed, but the harness never runs it")],
    "test_shared_bench_tests": [
        ("SHARED_BENCH_TESTS", ["test_decode.py"], "test_run_core unlisted"),
        ("harness", _wrap_suite("harness", _drop_harness_check(
            "sb.test-decode-canfail")),
         "the harness drops test_decode --can-fail"),
        ("harness", _wrap_suite("harness", _drop_harness_check(
            "sb.test-run-core")), "the harness drops test_run_core"),
        ("harness", _wrap_suite("harness", _branch_shared_bench),
         "the shared/bench tests run from this clone's copy, not the pin"),
        ("SHARED_BENCH", os.path.join(os.path.dirname(HERE), "shared",
                                      "bench"),
         "the suite's shared/bench is this clone's copy, not the pin")],
    "test_harness_py_canfails": [
        ("HARNESS_PY_CANFAILS",
         [t for t in B.HARNESS_PY_CANFAILS if t != "scratch_run_test.py"],
         "scratch_run_test's can-fail unlisted"),
        ("HARNESS_PY_CANFAILS", B.HARNESS_PY_CANFAILS + ["sims_test.py"],
         "a listed can-fail the test does not have"),
        ("harness", _wrap_suite("harness", _drop_harness_check(
            "scratch-dir-test-canfail")),
         "listed, but the harness never runs it")],
    "test_reopen_skip": [
        ("bench", _wrap_suite("bench", _unskip("innomaker-rate-runs-reopen")),
         "the reopen case runs again (a25e, 4cdb)"),
        ("bench", _wrap_suite("bench", _skip_reason(
            "innomaker-rate-runs-reopen", "reviewer: later")),
         "a skip that does not name the 2026-10-14 defect")],
    "test_strict_port_off": [
        ("bench", _wrap_suite("bench", _add_arg("powerup-round-noack",
                                                "--strict-port")),
         "the no-ACKer power-up check under the ported image's rule"),
        ("bench", _wrap_suite("bench", _add_arg("usb-host-baseline-noack",
                                                "--strict-port")),
         "a bench_usb_host line passes --strict-port"),
        ("bench", _wrap_suite("bench", _set_mode("powerup-round-noack",
                                                 "ack")),
         "no no-ACKer power-up check left to pin")],
    "test_vport_split": [
        ("bench", _wrap_suite("bench", _wrong_rate_back),
         "the wrong-rate arms back in vport-listen"),
        ("bench", _wrap_suite("bench", _arm_lost),
         "vport-listen loses its stall arm"),
        ("bench", _wrap_suite("bench", _known_fail_dropped),
         "the wrong-rate check not a known fail")],
    "test_recorded_judge": [("recorded_judge", _pass_all, "passes everything")],
    "test_scenario_judge": [
        ("scenario_judge", _pass_all, "passes everything"),
        ("scenario_judge", _scen_ignores_rc, "ignores the exit code"),
        ("scenario_judge", _scen_not_exercised_fail, "NOT EXERCISED judged FAIL")],
    "test_corrupt_pin_judge": [
        ("corrupt_pin_judge", _pass_all, "passes everything"),
        ("corrupt_pin_judge", _ORIG_SCRIPT, "reads exit 1 as FAIL")],
    "test_mutant_wiring": [
        ("harness", _wrap_suite("harness", _plain_judges),
         "every mutant on the exit-code judge"),
        ],
    "test_identify": [
        ("bench_identify.PRODUCT_RE", _OLD_PRODUCT_RE,
         "PRODUCT_RE without re.M (the 00:30 form)"),
        ("bench_identify.identify", _identify_ignores_product,
         "identify goes on to SLCAN after a product reply")],
    "test_x2_test_images_identify": [
        ("bench_identify.read_slcan", _slcan_raises,
         "a board that answers no SLCAN read raises (the bench stops)"),
        ("bench_identify.read_slcan", _slcan_unanchored,
         "V and N matched anywhere in the reply (slcan_spi read as an SLCAN)"),
        ("bench_identify.PRODUCT_RE", _re.compile(r"serial> \S"),
         "any boot serial line taken as the product's (the jammer's banner)"),
        ("bench_identify.SPI_V_RE", _re.compile(rb"(?!)"),
         "slcan_spi's identity not read (the read before this change)")],
    "test_slcan_spi_identity": [
        ("bench_identify.SPI_V_RE", _re.compile(rb"(?!)"),
         "slcan_spi's identity not read"),
        ("bench_identify.read_slcan", _spi_v_only,
         "the identity taken from V alone, N not compared"),
        ("bench_identify.read_slcan", _spi_any_name,
         "a name without _NOT_FOR_VEHICLE taken"),
        ("bench_identify.read_slcan", _spi_any_elf,
         "an ELF hash of any length taken"),
        ("bench_identify.read_slcan", _spi_unanchored,
         "the identity matched anywhere in the answer"),
        ("bench_identify.read_slcan", _spi_name_only,
         "the image named without its version and ELF hash")],
    "test_x2_test_image_flash_first": [
        ("bench_identify.identify", _identify_unplaced_expected,
         "a board identify cannot place taken as the expected image"),
        ("bench_identify.identify", _identify_unplaced_raises,
         "a board identify cannot place stops the bench, not flashed"),
        ("bench", _wrap_suite("bench", _x2_trusts_expect),
         "the X2 taken to be on the group's image, not read"),
        ("bench", _wrap_suite("bench", _x2_trusts_flash),
         "a flash taken as read back (checks run on a flash that did not take)")],
    "test_c12_judge": [
        ("c12_judge", _pass_all, "passes everything"),
        ("c12_judge", _c12_counts_c9, "fails on C9's DATASHEET line")],
    "test_switch_pending_judge": [
        ("switch_pending_judge", _pass_all, "passes everything"),
        ("switch_pending_judge", _sp_no_result, "a missing RESULT line read as 0 and 0"),
        ("switch_pending_judge", _sp_flip("unaccounted, not 0"),
         "ACK's unaccounted count not judged"),
        ("switch_pending_judge", _sp_flip("outside 0.."), "NOACK's range not judged"),
        ("switch_pending_judge", _sp_flip("bridge= rose"),
         "bridge= above veh tx= not judged"),
        ("switch_pending_judge", _sp_flip("no deltas line"),
         "a phase's missing deltas not judged")],
    "test_host_bridge_judge": [
        ("host_bridge_judge", _hb_unreported_pass,
         "an unreported L2 case passes"),
        ("host_bridge_judge", _hb_list_taken, "a bad test list taken"),
        ("host_bridge_judge", _hb_unlisted_ignored,
         "a result line for an unlisted test ignored"),
        ("host_bridge_judge", _hb_missing_as_fail,
         "a listed test with no result FAIL, not ERROR"),
        ("host_bridge_judge", _hb_exit_ignored,
         "a closing exit 2 ignored when the OK line is there"),
        ("_hb_list", _hb_list_from_results,
         "no list: the tests taken from the result lines"),
        ("_hb_list", _hb_list_deduped, "a name listed twice taken once"),
        ("_hb_list", _hb_list_first, "two lists: the first taken"),
        ("_hb_list", _hb_list_count_ignored, "a miscounted list taken")],
    "test_suites": [
        ("bench", _wrap_suite("bench", _drop_requires), "bench drops requires"),
        ("harness", _wrap_suite("harness", _sim_into_harness),
         "a wall-clock sim in harness"),
        ("emulated", _wrap_suite("emulated", _mutant_into_emulated),
         "a mutation script in emulated"),
        ("bench", _wrap_suite("bench", _bare_skip),
         "a skip with no spec item or ruling"),
        ("emulated", _wrap_suite("emulated", _unretire_build_check),
         "build-check run again after its 2a retirement"),
        ("bench", _wrap_suite("bench", _no_chip),
         "X2 images name no chip"),
        ("product_files", _harness_files, "run_scenario.py counted as product"),
        ("product_files", _no_machine, "machine.py left out of the product"),
        ("harness", _wrap_suite("harness", _product_in_harness),
         "the harness suite gated on the product digest"),
        ("emulated", _wrap_suite("emulated", _no_shared_ship),
         "madhouse gets no shared/ (port step 2b)"),
        ("emulated", _wrap_suite("emulated", _no_fake_dir),
         "host-bridge without TWAI_FAKE_DIR (port step 2b)")],
    "test_idf_log_checks": [
        ("bench", _wrap_suite("bench", _drop_witness_idf_log), "no idf-log on the witness"),
        ("bench", _wrap_suite("bench", _idf_log_flag_dropped),
         "idf-log run without --idf-log (total silence on a printing image)"),
        ("bench", _wrap_suite("bench", _truck_idf_log),
         "the truck's flight-silence weakened to --idf-log")],
    "test_list_build_images": [
        ("_interposer_image", _image_folder_pins,
         "the pins recomputed from the build folders, not the records'"),
        ("_interposer_image", _image_any_build,
         "a variant no listed build gives taken from another"),
        ("_interposer_image", _image_images_dir,
         "interposer_builds ignored, images from images_dir"),
        ("_sha256", lambda p: "0" * 64, "the app's sha256 not of its bytes")],
    "test_interposer_images": [
        ("_interposer_image", _image_never_refuses,
         "a folder that does not name its image is flashed anyway"),
        ("_interposer_image", _image_name_expect,
         "expect from the name, not the app's ELF hash"),
        ("INTERPOSER_IDF", "v5.4.1",
         "the interposer's images expected on ESP-IDF v5.4.1"),
        ("_md5", lambda p: "0" * 32, "md5 not of the file")],
    "test_pycache_ignored": [
        ("self._PYCACHE_DROP", ("charge-interposer/.gitignore",),
         "charge-interposer without its .gitignore")],
    "test_can_tools_record": [
        ("can_tools_info", lambda: {}, "nothing recorded"),
        ("can_tools_info", _ct_no_pin, "the pin's commit not recorded"),
        ("can_tools_info", _ct_unchecked,
         "the folder recorded without the pin's checks"),
        ("can_tools_info", _ct_no_refusal,
         "a refusal not recorded"),
        ("bench", _wrap_suite("bench", _bench_without_can_tools),
         "the bench record without the pin")],
    "test_scripts_in_tree": [
        ("bench", _wrap_suite("bench", _artifacts_script),
         "a bench driver run from {interposer_artifacts}"),
        ("harness", _wrap_suite("harness", _reverse_it_cwd),
         "a self-test with cwd {reverse_it}"),
        ("harness", _wrap_suite("harness", _staged_art),
         "the madhouse mutants shipping an art staging"),
        ("harness", _wrap_suite("harness", _missing_script),
         "a madhouse mutant naming a script the tree lacks")],
    "test_runner_stage": [
        ("_stage_git_bundle", _stage_kept,
         "the bundle in a kept per-commit folder outside runner-stage"),
        ("_remove_at_exit", lambda path: None,
         "the run folder never removed at exit"),
        ("_STAGED", _NoStore(),
         "a new bundle at every call for the same commit"),
        ("scratch_dir.make", _make_no_prune,
         "a killed run's folder never pruned")],
    "test_bench_argv": [
        ("bench", _wrap_suite("bench", _flight_silence_a25e),
         "flight-silence gives --pcan a value (the a25e line)"),
        ("self.NO_ARGPARSE", {"keepalive_probe.py", "bench_topology.py"},
         "a stale NO_ARGPARSE entry"),
        ("self.NO_ARGPARSE", set(),
         "a script without argparse left unlisted"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "_done(_orig(self, args, namespace))", "_done(None)"),
         "the probe never parses"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "        setattr(m, n, _forbidden)", "        pass"),
         "the probe leaves serial, CAN and USB open"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "    if _inside:\n        return _orig_known(self, args, namespace)\n",
            ""),
         "parse_args's inner parse_known_args ends the probe (extras unchecked)")],
}


def main():
    global TMP
    # depth: its deepest git repo, ctp\ct (7 below the run folder), + 12
    # for .git\config (measured 2026-11-05 as can-tools-pin\can-tools, then
    # shortened with pycache-probe to pyc; scratch_dir)
    TMP = scratch_dir.make("bench-suite-test", depth=19)
    try:
        _main()
    finally:
        scratch_dir.remove(TMP)


def _main():
    can_fail = "--can-fail" in sys.argv[1:]
    bad = 0
    for t in TESTS:
        if not can_fail:
            try:
                t()
                print("ok    %s" % t.__name__)
            except Exception:
                bad += 1
                print("FAIL  %s\n%s" % (t.__name__, traceback.format_exc()[-800:]))
            continue
        for attr, repl, what in MUTANTS[t.__name__]:
            mod, name = _target(attr)
            setattr(mod, name, repl)
            try:
                t()
                bad += 1
                print("FAIL  %s survived: %s" % (t.__name__, what))
            except Exception as e:
                print("ok    %s caught: %s (%s: %s)" % (
                    t.__name__, what, type(e).__name__, str(e)[:80]))
            finally:
                setattr(mod, name, _ORIG[attr])
    n = sum(len(v) for v in MUTANTS.values()) if can_fail else len(TESTS)
    print("\n%d of %d %s" % (n - bad, n, "mutants caught" if can_fail
                              else "passed"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
