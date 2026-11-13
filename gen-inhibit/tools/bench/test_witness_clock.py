#!/usr/bin/env python3
"""Harness test for witness_clock.py and the three Kvaser witnesses that use it
(interlock_bench.py, x2_jam_cases.py, x2_jammer.py): a check is never judged on the
witness's receipt time. It is VOID, with the clock note, and its data is still recorded.

    python3.13 -B gen-inhibit/tools/bench/test_witness_clock.py [--can-fail]

The gen-inhibit reviewer's A2 ruling (b), 2026-11-11: the latent fallback to receipt time
when the Kvaser's hardware stamps are not on the epoch clock must refuse or VOID the check,
with the note, and never judge. Gen-inhibit tester, 2026-11-11.

No hardware: each witness is built without its bus, and the bench around a case is stubbed
(interlock_bench.run_case's reboot, PCAN, poller, sender and judge; x2_jam_cases'
case_threshold arm; x2_jammer's console). The canre pin must be installed in the worktree
(canre_pin.py install) and REVERSE_IT_DIR must be the real one, read only:
lowid_flood_skip_bench and interlock_judge need them at import. Witness and run files go
to this run's scratch folder.

With --can-fail the unmutated files run first, as the control (if a check fails or
crashes, no mutant is judged and the run exits 1); then each mutant, a copy of one file
with one edit in this run's scratch folder, put first on the path in a fresh interpreter,
must fail a check. A check that raises is a crash, not a kill, and an edit that does not
apply is an error.
"""
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time as _time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MUT_ENV = "WITNESS_CLOCK_MUTANT_DIR"
if os.environ.get(MUT_ENV):
    sys.path.insert(0, os.environ[MUT_ENV])
if HERE not in sys.path:
    sys.path.insert(1, HERE)
import gi_paths  # noqa: E402  scratch/ folders

EPOCH = 1.7e9          # receipt times on the host's epoch clock
HW0 = 5000.0           # a Kvaser hardware clock that is not on it

MUTANTS = [
    ("witness_clock.py", "judgeable takes any string",
     'return isinstance(note, str) and (note == NONE or note.startswith(HW + " "))',
     "return isinstance(note, str)"),
    ("witness_clock.py", "require does not raise",
     "        raise ReceiptClock(note)\n", "        pass\n"),
    ("witness_clock.py", "the void verdict is not VOID",
     'return "VOID: witness clock', 'return "AS SPECIFIED: witness clock'),
    ("interlock_bench.py", "run_case judges on receipt time",
     "    if not WC.judgeable(clock):\n        # never judged on receipt time",
     "    if False:\n        # never judged on receipt time"),
    ("interlock_bench.py", "rejudge judges a receipt-time witness file",
     '    if not WC.judgeable(w.get("clock")):', "    if False:"),
    ("interlock_bench.py", "the witness takes any lag as hardware time",
     "if lags and abs(statistics.median(lags)) < WC.MAX_LAG_S:",
     "if lags and abs(statistics.median(lags)) < 1e12:"),
    ("x2_jam_cases.py", "clocked() does not refuse receipt time",
     "        fr, er, note = self.recorded()\n        WC.require(note)\n",
     "        fr, er, note = self.recorded()\n"),
    ("x2_jam_cases.py", "run_guarded does not void",
     "        WC.void(rec, e.note)\n        print(rec[\"verdict\"])\n", "        pass\n"),
    ("x2_jam_cases.py", "the witness takes any lag as hardware time",
     "if lags and abs(statistics.median(lags)) < WC.MAX_LAG_S:",
     "if lags and abs(statistics.median(lags)) < 1e12:"),
    ("x2_jam_cases.py", "nothing heard reads as receipt time",
     "note = WC.RECEIPT if lags else WC.NONE", "note = WC.RECEIPT"),
    ("x2_jammer.py", "times() does not refuse receipt time",
     "        ts, note = self.recorded()\n        WC.require(note)\n",
     "        ts, note = self.recorded()\n"),
    ("x2_jammer.py", "run_guarded does not void",
     "        WC.void(rec, e.note)\n        print(rec[\"verdict\"])\n", "        pass\n"),
    ("x2_jammer.py", "the witness takes any offset as hardware time",
     "if abs(off) < WC.MAX_LAG_S:", "if abs(off) < 1e12:"),
    # A2 (a), 2026-11-11: the PCAN's own stamps
    ("witness_clock.py", "stamps on the epoch clock are not VOID",
     "    if abs(epoch_lag) < EPOCH_LAG_S:\n", "    if False:\n"),
    ("witness_clock.py", "too few points are not VOID",
     "    elif len(points) < MIN_POINTS:\n", "    elif False:\n"),
    ("witness_clock.py", "the slope bound is not applied",
     "    elif slope_ppm is None or abs(slope_ppm) > SLOPE_MAX_PPM:\n", "    elif False:\n"),
    ("witness_clock.py", "the residual bound is not applied",
     '    elif fit["residual_p99_s"] > RESIDUAL_P99_MAX_S:\n', "    elif False:\n"),
    ("witness_clock.py", "no offset: the stamps taken as they are",
     "    off = _median(p - ts for p, _e, ts in points)\n", "    off = 0.0\n"),
    ("witness_clock.py", "any two gaps agree",
     "    return a is not None and b is not None and abs(a - b) <= RESIDUAL_P99_MAX_S\n",
     "    return True\n"),
    ("wrong_bitrate_errors.py", "B9 judges the receipt times",
     "    return [WC.mapped(fit, ts) for ts in wit.error_ts], fit,", "    return list(wit.errors), fit,"),
    ("wrong_bitrate_errors.py", "B9 judges a VOID fit's receipt times as mapped",
     '    if fit["void"]:\n        return list(wit.errors), fit,', '    if False:\n        return list(wit.errors), fit,'),
    ("trip6_rearm_check.py", "B10 counts errors since the arm on receipt time",
     "    wire = [t for t, d in det if d[1] != 0]\n", "    wire = [d[0] for t, d in det if d[1] != 0]\n"),
    ("trip6_rearm_check.py", "B10 counts the device's 0x051 on receipt time",
     "    d051 = [WC.mapped(fit, s) for s in wit.dev051_ts]\n", "    d051 = list(wit.dev051)\n"),
    ("trip6_rearm_check.py", "B10's gap from the receipt time",
     '            gap = round(arm["armed_at"] - (lm - t0), 3)\n',
     '            gap = round(arm["armed_at"] - (last[0] - t0), 3)\n'),
    ("trip6_rearm_check.py", "B10's history gap from the receipt time",
     '            hgap = round(pre["t"] - (lm - t0), 3) if (pre and "t" in pre) else None\n',
     '            hgap = round(pre["t"] - (last[0] - t0), 3) if (pre and "t" in pre) else None\n'),
    ("trip6_rearm_check.py", "B10 judges a gap the receipt time disagrees with",
     "        if last is not None and not WC.gap_agrees(gap, gap_r):\n", "        if False:\n"),
    ("trip6_rearm_check.py", "B10 judges on a VOID fit",
     '    if fit["void"]:\n        v = WC.pcan_void_verdict', '    if False:\n        v = WC.pcan_void_verdict'),
    ("trip6_rearm_check.py", "B10 prints no VOID line",
     '        print("witness clock VOID: %s" % fit["void"])\n', "        pass\n"),
    ("trip6_rearm_check.py", "B10 judges an arm live again",
     '        judged.append((poll_arm("A3 recent", a.arm_s), "recent", last))\n',
     '        judged.append((poll_arm("A3 recent", a.arm_s), "recent", last))\n'
     '        rec["arms"][-1]["verdict"] = judge_recent(rec["arms"][-1]["reads"], 1.0, 0)\n'),
]


# ------------------------------------------------------------------ helpers

class Box:
    pass


def fast_time():
    """A time module for the code under test: real clocks, sleeps of at most 1 ms."""
    return types.SimpleNamespace(time=_time.time, perf_counter=_time.perf_counter,
                                 strftime=_time.strftime,
                                 sleep=lambda s: _time.sleep(min(max(s, 0), 0.001)))


def raises(exc, fn):
    try:
        fn()
    except exc as e:
        return e
    return None


def bare(cls, **attrs):
    """An instance of a witness class without its bus."""
    w = object.__new__(cls)
    w.lock = threading.Lock()
    for k, v in attrs.items():
        setattr(w, k, v)
    return w


# ------------------------------------------------------- interlock_bench stubs

class JudgeSpy:
    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return {"verdict": "AS SPECIFIED", "preconditions": [], "checks": [],
                "facts": {"vcm_0x051": 0}}


class FakeBus:
    def shutdown(self):
        pass


class FakePoller:
    def __init__(self, LF, every=0.5):
        self.samples = []
        self.stop_flag = threading.Event()

    def start(self):
        pass

    def join(self, timeout=None):
        pass


class FakeLF:
    @staticmethod
    def build_id():
        return {"stub": True}

    @staticmethod
    def page():
        return {"mode": 0}

    @staticmethod
    def dev_post(path, timeout=None):
        return True


def ib_witness(IB, frames, errs):
    real = IB.REAL_WITNESS

    class W:
        def __init__(self, can):
            self.inner = bare(real, frames=list(frames), errs=list(errs))

        def start(self):
            pass

        def recorded(self):
            return self.inner.recorded()

        def clocked(self):          # what a judging caller of the old name would get
            return self.inner.recorded()

        def close(self):
            pass
    return W


def ib_frames(lag, epoch_rx):
    """Two frames, hardware stamps from HW0; receipt either lag after them (on the same
    clock) or on the epoch clock."""
    out = []
    for k in range(2):
        hw = HW0 + k * 0.01
        rx = (EPOCH + k * 0.01 + 0.001) if epoch_rx else hw + lag
        out.append((hw, rx, 0x051, "00" * 8))
    return out


def ib_setup(IB, work):
    IB.REAL_WITNESS = IB.Witness          # run_case's Witness is replaced per run below
    IB.time = fast_time()
    IB.RUNS = os.path.join(work, "ib_runs")
    os.makedirs(IB.RUNS, exist_ok=True)
    IB.reboot = lambda LF, timeout=60.0: {"ok": True}
    IB.Poller = FakePoller
    IB.play = lambda can, bus, parsed, t0p, sent: None
    IB.poster = lambda LF, modes, t0p, posts: None
    IB.J.gi_diag.run_identity = lambda **k: {"stub": True}
    IB.J.schemas = lambda: None
    spy = JudgeSpy()
    IB.J.judge = spy
    return spy


def ib_run(IB, spy, frames):
    IB.Witness = ib_witness(IB, frames, [])
    can = types.SimpleNamespace(Bus=lambda **k: FakeBus())
    name = IB.CASES[0][0]
    info = {"case": name, "judge": IB.CASES[0][2]}
    parsed = {"frames": [], "modes": [], "end": 1000}
    before = spy.calls
    rec = IB.run_case(can, FakeLF, info, parsed, "stub", _time.strftime("%Y%m%dT%H%M%S"))
    return rec, spy.calls - before


def ib_saved(IB, work, clock, k):
    """A saved run and its witness file, as run_case writes them."""
    d = os.path.join(work, "ib_saved_%d" % k)
    os.makedirs(d, exist_ok=True)
    wname = "interlock_x_%d_witness.json.gz" % k
    with gzip.open(os.path.join(d, wname), "wt", encoding="ascii") as fh:
        json.dump({"clock": clock, "frames": [[HW0, 0x051, "00" * 8]], "errs": [], "sent": []}, fh)
    rec = {"case": IB.CASES[0][0], "stimulus": {"case": IB.CASES[0][0], "judge": IB.CASES[0][2]},
           "t0_epoch": EPOCH, "witness_file": wname, "verdict": "AS SPECIFIED",
           "judged": {"preconditions": [], "checks": [], "facts": {}}}
    path = os.path.join(d, "interlock_x_%d.json" % k)
    with open(path, "w", encoding="ascii") as fh:
        json.dump(rec, fh)
    return path


# ---------------------------------------------------------- x2_jam_cases stubs

def xc_case(XC, wit, n=10):
    """case_threshold, one arm, with the bench stubbed (as test_x2_jam_cases_b6ecc does)."""
    import argparse
    a = argparse.Namespace(n=n, judge_first=None, ecc=False, reset_wican=False, interval_ms=1000,
                           lead_s=5.0, w=20, arms=1, want_sha=None)
    end = 110.0 + n + 12.0
    ph = {"replay_start": 95.0, "arm_posted": 100.0, "pulses_start": 110.0,
          "pulses_end": 110.0 + n, "mode0_posted": end}
    samples = []
    t = 100.5
    while t < end:
        k = sum(1 for i in range(n) if 110.001 + i < t)
        samples.append((round(t, 3), {"mode": 3, "inhibit_live": True, "abort_latched": False,
                                      "abort_reason": "", "tx_ok": int(t * 10), "drv_bus_error": k}))
        t += 0.1
    XC.time = fast_time()
    XC.arm_live = lambda a_, con, w, k, body: {"n": k, "phases": dict(ph), "samples": samples}
    XC.replay_rate_device_off = lambda frames, ph_: 100.0
    rec = {}
    XC.run_guarded(XC.case_threshold, a, rec, None, wit)
    return rec


def xc_errs(n, epoch_rx):
    """n error frames, one per second from 110.001 on the hardware clock; receipt 1 ms
    later on the same clock, or on the epoch clock."""
    out = []
    for k in range(n):
        hw = 110.001 + k
        out.append((hw, (EPOCH + k) if epoch_rx else hw + 0.001))
    return out


# ----------------------------------------------- A2 (a): the PCAN's own stamps

DEV0 = 5000.0          # the PCAN's device clock: perf_counter less this (an uptime base)
LAT = 0.0005           # host delivery latency


def pcan_points(n=200, jitter=0.0, ppm=0.0, epoch=False, start=100.0, step=0.01):
    """[(perf_rx, epoch_rx, stamp)]: a message on the wire every step s from perf start,
    stamped on the PCAN's device clock (drifting ppm against perf_counter) or, epoch=True,
    on the host's epoch clock (COOKED); received LAT later, plus jitter on every other one."""
    out = []
    for k in range(n):
        wire = start + k * step
        rx = wire + LAT + (jitter if k % 2 else 0.0)
        ts = (EPOCH + wire - start) if epoch else (wire - DEV0) / (1 + ppm * 1e-6)
        out.append((rx, EPOCH + (rx - start), ts))
    return out


def stamp(wire):
    return wire - DEV0


def t6_witness(epoch=False):
    """A B10 witness after a run: messages 90-150 s; floods at 100 s and 130 s; an error frame
    stamped 114.990 s but received 115.020 s (just after A1's arm at 115 s); a device 0x051
    stamped 131.990 s, received 132.010 s (just after A3's arm at 132 s)."""
    w = Box()
    w.points = pcan_points(n=1200, epoch=epoch, start=90.0, step=0.05)
    det = []
    for base in (100.0, 130.0):
        for k in range(5):
            wire = base + k * 0.001
            det.append((wire + LAT, 2, "00", stamp(wire)))
    det.append((115.020, 2, "00", stamp(114.990)))
    w.error_detail = det
    w.dev051 = [132.010]
    w.dev051_ts = [stamp(131.990)]
    if epoch:                   # COOKED: every stamp on the epoch clock
        w.error_detail = [(d[0], d[1], d[2], EPOCH + d[0]) for d in det]
        w.dev051_ts = [EPOCH + 132.010]
    return w


def t6_run(T6, w, a4_receipt_late=0.3, a1_receipt_late=0.02):
    """judge_arms on three judged arms (t0 = 0): A1 expired at 115 s after the 100 s flood, whose
    last error frame was received a1_receipt_late after its stamp; A3 recent at 132 s after the
    130 s flood; A4 expired at 145 s, its last error received a4_receipt_late after its stamp."""
    T = T6.TRIP6
    last0 = (100.004 + LAT + a1_receipt_late, 2, "00", stamp(100.004))
    last2 = (130.004 + LAT, 2, "00", stamp(130.004))
    last2_late = (130.004 + LAT + a4_receipt_late, 2, "00", stamp(130.004))

    def arm(name, at, pre, reads):
        return {"arm": name, "armed_at": at, "_ta": at, "_t_end": at + 12.0, "pre_arm_read": pre,
                "reads": reads}
    a1 = arm("A1 expired", 115.0, {"t": 114.9, "err_n": 0, "err_age_ms": 14896, "err_burst": False},
             [{"t": 116.0, "live": True, "abort": ""}])
    a3 = arm("A3 recent", 132.0, {"t": 131.9, "err_n": 12, "err_age_ms": 1896, "err_burst": True},
             [{"t": 132.2, "live": False, "abort": T}])
    a4 = arm("A4 expired", 145.0, {"t": 144.9, "err_n": 0, "err_age_ms": 14896, "err_burst": False},
             [{"t": 146.0, "live": True, "abort": ""}])
    fit = T6.judge_arms([a1, a3, a4], [(a1, "expired", last0), (a3, "recent", last2), (a4, "expired", last2_late)],
                        w, 0.0)
    return fit, a1, a3, a4


def main_body(path, name="main"):
    """The source lines of function `name` in the file at path."""
    import ast
    src = open(path, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return src.splitlines()[node.lineno - 1:node.end_lineno]
    return []


def judge_calls_outside(path, names, inside):
    """Lines calling any of names from anywhere but function `inside` (or their own defs)."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == inside]
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names:
            if not any(a <= n.lineno <= b for a, b in spans):
                out.append((n.lineno, n.func.id))
    return out


# ------------------------------------------------------------- x2_jammer stubs

class FakeJamCon:
    """Replies ERR to TRAIN with no ms, as the jammer does."""

    def send(self, line):
        pass

    def wait(self, pred, timeout, t0):
        p = {"kind": "ERR", "raw": "JAMMER ERR no_duration"}
        return (_time.time(), p) if pred(p) else None


def xj_closed(XJ, err):
    """x2_jammer's Witness without its bus or thread, as after close(): no failed read and
    the stop flag set, so down() is None (2026-11-13, WitnessDown)."""
    stop = threading.Event()
    stop.set()
    return bare(XJ.Witness, err=list(err), failed=None, stop_flag=stop)


def adv_time():
    """A time module whose sleeps advance its own clock (from now), for windows with an end."""
    clock = [_time.time()]

    def sleep(s):
        clock[0] += max(s, 0)
    return types.SimpleNamespace(time=lambda: clock[0], perf_counter=_time.perf_counter,
                                 strftime=_time.strftime, sleep=sleep)


def xj_noduration(XJ, err):
    """noduration's window ends at its closing pulse since 2026-11-13, so the clock advances
    with its sleeps (the send at +2 s, the closing pulse at +5 s)."""
    import argparse
    XJ.time = adv_time()
    wit = xj_closed(XJ, err)
    rec = {}
    XJ.run_guarded(XJ.sub_noduration, argparse.Namespace(w=20, p=200), rec, FakeJamCon(), wit)
    return rec


# ------------------------------------------------------------------- checks

CALL_RE = re.compile(r"\b(\w+)\.recorded\(\)")


def recorded_calls(path):
    """Lines that call <object>.recorded(): not its definition, not the clocked()/times()
    wrapper (self.recorded()), not a docstring's Witness.recorded()."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if any(m.group(1) not in ("self", "Witness") for m in CALL_RE.finditer(line)):
                out.append(n)
    return out


def under_test():
    """The four modules, a mutant's file first: each tool puts its own folder at the front
    of sys.path when imported, so the mutant folder goes back in front before each import,
    and the leaves come first (x2_jam_cases imports x2_jammer; all import witness_clock),
    so sys.modules holds the mutant when a later tool imports it. A mutant run refuses if
    its file is not the module in use."""
    import importlib
    mut = os.environ.get(MUT_ENV)
    mods = []
    for name in ("witness_clock", "x2_jammer", "x2_jam_cases", "interlock_bench", "wrong_bitrate_errors",
                 "trip6_rearm_check"):
        if mut:
            sys.path.insert(0, mut)
        mods.append(importlib.import_module(name))
    if mut:
        for m in mods:
            if os.path.isfile(os.path.join(mut, os.path.basename(m.__file__))) and \
                    os.path.dirname(os.path.abspath(m.__file__)) != os.path.abspath(mut):
                raise RuntimeError("the mutant %s is not the module in use (%s)" % (m.__name__, m.__file__))
    return mods


def checks(work):
    WC, XJ, XC, IB, WB, T6 = under_test()
    spy = ib_setup(IB, work)
    B = Box()
    out = []

    def add(label, fn):
        out.append((label, fn))

    hw = WC.hw_note(0.0012)
    # witness_clock
    add("judgeable: a hardware-stamp note", lambda: (WC.judgeable(hw), hw))
    add("judgeable: nothing heard (NONE)", lambda: (WC.judgeable(WC.NONE), WC.NONE))
    add("not judgeable: receipt time", lambda: (not WC.judgeable(WC.RECEIPT), WC.RECEIPT))
    add("not judgeable: x2_jammer's receipt note with its offset",
        lambda: (not WC.judgeable(WC.RECEIPT + ": offset 3.2 s"), None))
    add("not judgeable: None, a dict, a look-alike prefix",
        lambda: (not any(WC.judgeable(x) for x in (None, {"stub": True}, "kvaser_hwx (1 s)")), None))
    add("require: receipt time raises ReceiptClock with the note",
        lambda: (lambda e: (e is not None and e.note == WC.RECEIPT, e))(
            raises(WC.ReceiptClock, lambda: WC.require(WC.RECEIPT))))
    add("require: a hardware note passes", lambda: (WC.require(hw) is None, None))
    add("void: VOID verdict naming the note, and the note kept",
        lambda: (lambda r: (r["verdict"].startswith("VOID") and WC.RECEIPT in r["verdict"]
                            and r["witness_clock_void"] == WC.RECEIPT, r))(WC.void({}, WC.RECEIPT)))

    # interlock_bench: its witness
    def ib_rec(lag, epoch_rx):
        return bare(IB.REAL_WITNESS, frames=ib_frames(lag, epoch_rx), errs=[]).recorded()
    add("interlock witness: 1 ms lag -> hardware stamps, judgeable",
        lambda: (lambda r: (WC.judgeable(r[2]) and r[0][0][0] == HW0, r[2]))(ib_rec(0.001, False)))
    add("interlock witness: hardware clock not on the epoch -> receipt times, the receipt note",
        lambda: (lambda r: (r[2] == WC.RECEIPT and r[0][0][0] > EPOCH, r[2]))(ib_rec(0, True)))
    add("interlock witness: a lag of -2.5 s -> receipt",
        lambda: (lambda r: (r[2] == WC.RECEIPT, r[2]))(ib_rec(-2.5, False)))

    # interlock_bench: run_case
    def ib_receipt():
        B.ib_r, B.ib_n = ib_run(IB, spy, ib_frames(0, True))
        return True
    add("interlock run_case on receipt time: runs", lambda: (ib_receipt(), None))
    add("interlock run_case on receipt time: the judge is not called",
        lambda: (B.ib_n == 0, B.ib_n))
    add("interlock run_case on receipt time: VOID, nothing judged",
        lambda: (B.ib_r["verdict"].startswith("VOID") and B.ib_r.get("judged") is None,
                 (B.ib_r.get("verdict"), B.ib_r.get("judged"))))
    add("interlock run_case on receipt time: the witness file is kept, labelled",
        lambda: (lambda w: (w["clock"] == WC.RECEIPT and len(w["frames"]) == 2, w["clock"]))(
            json.load(gzip.open(os.path.join(IB.RUNS, B.ib_r["witness_file"]), "rt"))))
    add("interlock run_case on hardware time: judged once, the judge's verdict",
        lambda: (lambda r: (r[1] == 1 and r[0]["verdict"] == "AS SPECIFIED", (r[1], r[0]["verdict"])))(
            ib_run(IB, spy, ib_frames(0.001, False))))

    # interlock_bench: rejudge
    def ib_rejudge(clock, k):
        before = spy.calls
        rec2, out_ = IB.rejudge_file(ib_saved(IB, work, clock, k))
        return rec2, out_, spy.calls - before
    add("interlock rejudge of a receipt-time witness file: VOID, not judged, written",
        lambda: (lambda r: (r[2] == 0 and r[0]["verdict"].startswith("VOID") and r[0]["judged"] is None
                            and r[0]["previous_verdict"] == "AS SPECIFIED" and os.path.isfile(r[1]),
                            (r[2], r[0]["verdict"][:40])))(ib_rejudge(WC.RECEIPT, 1)))
    add("interlock rejudge of a hardware-time witness file: judged",
        lambda: (lambda r: (r[2] == 1 and r[0]["verdict"] == "AS SPECIFIED", (r[2], r[0]["verdict"])))(
            ib_rejudge(hw, 2)))
    add("interlock main rejudge prints a VOID file without crashing",
        lambda: (IB.main(["rejudge", ib_saved(IB, work, WC.RECEIPT, 3)]) == 0, None))

    # x2_jam_cases: its witness
    def xc_wit(errs):
        return bare(XC.Witness, frames=[], errs=list(errs))
    add("x2_jam_cases witness: 1 ms lag -> hardware stamps, clocked() gives them",
        lambda: (lambda r: (WC.judgeable(r[2]) and r[1][0] == 110.001, r[2]))(
            xc_wit(xc_errs(3, False)).clocked()))
    add("x2_jam_cases witness: epoch receipt -> recorded() gives receipt times, labelled",
        lambda: (lambda r: (r[2] == WC.RECEIPT and r[1][0] >= EPOCH, r[2]))(
            xc_wit(xc_errs(3, True)).recorded()))
    add("x2_jam_cases witness: epoch receipt -> clocked() raises ReceiptClock",
        lambda: (lambda e: (e is not None and e.note == WC.RECEIPT, e))(
            raises(WC.ReceiptClock, xc_wit(xc_errs(3, True)).clocked)))
    add("x2_jam_cases witness: nothing heard -> clocked() gives NONE, no raise",
        lambda: (lambda e: (e is None and xc_wit([]).clocked() == ([], [], WC.NONE), e))(
            raises(WC.ReceiptClock, xc_wit([]).clocked)))

    # x2_jam_cases: case_threshold through run_guarded
    def xc_receipt():
        B.xc_r = xc_case(XC, xc_wit(xc_errs(10, True)))
        return True
    add("x2_jam_cases threshold on receipt time: runs", lambda: (xc_receipt(), None))
    add("x2_jam_cases threshold on receipt time: VOID with the note",
        lambda: (str(B.xc_r.get("verdict", "")).startswith("VOID")
                 and B.xc_r.get("witness_clock_void") == WC.RECEIPT, B.xc_r.get("verdict")))
    add("x2_jam_cases threshold on receipt time: no arm judged",
        lambda: (bool(B.xc_r.get("arms")) and not any("judge" in x for x in B.xc_r["arms"]),
                 [sorted(x) for x in B.xc_r.get("arms") or []]))
    add("x2_jam_cases threshold on hardware time: the arm judged, not VOID",
        lambda: (lambda r: ("judge" in r["arms"][0] and "witness_clock_void" not in r
                            and not str(r.get("verdict", "")).startswith("VOID"),
                            (r["arms"][0].get("judge", {}).get("verdict"), r.get("verdict"))))(
            xc_case(XC, xc_wit(xc_errs(10, False)))))

    # x2_jammer: its witness and noduration through run_guarded
    def xj_wit(err):
        return xj_closed(XJ, err)
    add("x2_jammer witness: 1 ms lag -> hardware stamps, times() gives them",
        lambda: (lambda r: (WC.judgeable(r[1]) and r[0][0] == 110.001, r[1]))(
            xj_wit(xc_errs(2, False)).times()))
    add("x2_jammer witness: epoch receipt -> recorded() labelled receipt",
        lambda: (lambda r: (not WC.judgeable(r[1]) and r[1].startswith(WC.RECEIPT), r[1]))(
            xj_wit(xc_errs(2, True)).recorded()))
    add("x2_jammer witness: epoch receipt -> times() raises ReceiptClock",
        lambda: (raises(WC.ReceiptClock, xj_wit(xc_errs(2, True)).times) is not None, None))
    add("x2_jammer noduration on receipt time: VOID, not PASS or FAIL",
        lambda: (lambda r: (str(r.get("verdict", "")).startswith("VOID")
                            and r.get("witness_clock_void", "").startswith(WC.RECEIPT), r.get("verdict")))(
            xj_noduration(XJ, [(HW0, _time.time())])))
    add("x2_jammer noduration with nothing heard: judged, not VOID (INCONCLUSIVE: closing pulse unheard)",
        lambda: (lambda r: (r.get("verdict") == "INCONCLUSIVE" and "closing" in r.get("why", ""),
                            r.get("verdict")))(xj_noduration(XJ, [])))
    add("x2_jammer noduration with an error frame on hardware time, 1 s after the send: FAIL",
        lambda: (lambda r: (r.get("verdict") == "FAIL", r.get("verdict")))(
            xj_noduration(XJ, [(_time.time() + 3.0, _time.time() + 3.001)])))

    # A2 (a): the PCAN's device stamps, mapped onto perf_counter
    def fit_of(**k):
        return WC.device_fit(pcan_points(**k))
    add("device_fit: RAW device stamps fit, not VOID, offset DEV0 + latency, residual ~0, slope ~0",
        lambda: (lambda f: (f["void"] is None and f["n"] == 200 and abs(f["offset_s"] - (DEV0 + LAT)) < 1e-6
                            and f["residual_p99_s"] < 1e-6 and abs(f["slope_ppm"]) < 1.0, f))(fit_of()))
    add("device_fit: the bounds are recorded beside the figures",
        lambda: (lambda f: (f["bounds"] == {"residual_p99_max_s": 0.05, "slope_max_ppm": 100.0, "min_points": 20,
                                            "epoch_lag_min_s": 2.0}, f["bounds"]))(fit_of()))
    add("device_fit: stamps on the host's epoch clock (COOKED) -> VOID",
        lambda: (lambda f: ("epoch clock" in str(f["void"]), f["void"]))(fit_of(epoch=True)))
    add("device_fit: 19 messages -> VOID; 20 -> fit",
        lambda: ("fewer than 20" in str(fit_of(n=19)["void"]) and fit_of(n=20)["void"] is None,
                 (fit_of(n=19)["void"], fit_of(n=20)["void"])))
    add("device_fit: a 200 ppm drift -> VOID; 50 ppm -> fit",
        lambda: ("slope" in str(fit_of(ppm=200)["void"]) and fit_of(ppm=50)["void"] is None,
                 (fit_of(ppm=200)["void"], fit_of(ppm=50).get("slope_ppm"))))
    # over 200 s, so the jitter does not also tilt the fitted slope past its own bound
    add("device_fit: residual p99 0.1 s -> VOID; 0.01 s -> fit",
        lambda: ("residual p99" in str(fit_of(jitter=0.2, step=1.0)["void"])
                 and fit_of(jitter=0.02, step=1.0)["void"] is None,
                 (fit_of(jitter=0.2, step=1.0)["void"], fit_of(jitter=0.02, step=1.0).get("residual_p99_s"))))
    add("device_fit: no message -> VOID", lambda: (WC.device_fit([])["void"] is not None, None))
    add("mapped: a stamp plus the offset",
        lambda: (lambda f: (abs(WC.mapped(f, stamp(120.0)) - (120.0 + LAT)) < 1e-6, None))(fit_of()))
    add("gap_agrees: 40 ms apart agree; 60 ms do not; None never",
        lambda: (WC.gap_agrees(10.0, 10.04) and not WC.gap_agrees(10.0, 10.06) and not WC.gap_agrees(None, 1.0),
                 None))
    add("fit_line: the bounds beside the figures",
        lambda: (lambda s: ("(bound 0.05)" in s and "(bound 100)" in s and "residual" in s, s))(WC.fit_line(fit_of())))
    add("pcan_void_verdict: VOID naming the reason",
        lambda: (WC.pcan_void_verdict("x y").startswith("VOID: PCAN clock x y"), None))

    # B9: judged_errors
    def wb(epoch):
        w = Box()
        w.points = pcan_points(epoch=epoch)
        w.error_ts = [EPOCH + 0.5] if epoch else [stamp(100.5)]
        w.errors = [100.8]                  # received 0.3 s after the wire
        return WB.judged_errors(w)
    add("B9 judged_errors: the error frame at its mapped stamp, not its receipt",
        lambda: (lambda r: (abs(r[0][0] - (100.5 + LAT)) < 1e-6 and r[1]["void"] is None
                            and r[2].startswith("PCAN device stamps"), (r[0], r[2])))(wb(False)))
    add("B9 judged_errors: COOKED stamps -> the receipt times, labelled not judged, the fit VOID",
        lambda: (lambda r: (r[0] == [100.8] and r[1]["void"] and "not judged" in r[2], (r[0], r[2])))(wb(True)))

    # B10: judge_arms
    def t6(**k):
        B.t6 = t6_run(T6, t6_witness(), **k)
        return True
    add("B10 judge_arms on device stamps: runs", lambda: (t6(), None))
    add("B10 judge_arms: the fit not VOID", lambda: (B.t6[0]["void"] is None, B.t6[0]["void"]))
    add("B10 judge_arms: A1 expired as specified, its history and prediction too",
        lambda: ((B.t6[1]["verdict"], B.t6[1]["history_verdict"], B.t6[1]["prediction_verdict"])
                 == ("as specified",) * 3, (B.t6[1]["verdict"], B.t6[1]["history_verdict"])))
    add("B10 judge_arms: an error frame received after A1's arm but stamped before it is not since the arm",
        lambda: (B.t6[1]["errors_since_arm"] == 0, B.t6[1]["errors_since_arm"]))
    add("B10 judge_arms: A1's gaps from the mapped stamp, the receipt gap beside them",
        lambda: (abs(B.t6[1]["armed_after_last_error_s"] - 14.996) < 0.0015
                 and abs(B.t6[1]["armed_after_last_error_receipt_s"] - 14.976) < 0.0015
                 and abs(B.t6[1]["pre_arm_after_last_error_s"] - 14.896) < 0.0015,
                 (B.t6[1]["armed_after_last_error_s"], B.t6[1]["armed_after_last_error_receipt_s"],
                  B.t6[1]["pre_arm_after_last_error_s"])))
    add("B10 judge_arms: A3 recent as specified (the 0x051 stamped before its arm is not since it)",
        lambda: ((B.t6[2]["verdict"], B.t6[2]["history_verdict"], B.t6[2]["device_0x051_since_arm"])
                 == ("as specified", "as specified", 0),
                 (B.t6[2]["verdict"], B.t6[2]["history_verdict"], B.t6[2]["device_0x051_since_arm"])))
    add("B10 judge_arms: A4's receipt 0.3 s after its stamp -> VOID, gaps disagree",
        lambda: (B.t6[3]["verdict"].startswith("VOID") and "differ" in B.t6[3]["verdict"]
                 and B.t6[3]["history_verdict"] == B.t6[3]["verdict"], B.t6[3]["verdict"]))
    add("B10 judge_arms: A4 with its receipt 0.03 s late is judged (inside the bound)",
        lambda: (lambda r: (r[3]["verdict"] == "as specified", r[3]["verdict"]))(
            t6_run(T6, t6_witness(), a4_receipt_late=0.03)))

    def t6_cooked():
        B.t6c = t6_run(T6, t6_witness(epoch=True))
        return True
    add("B10 judge_arms on COOKED stamps: runs", lambda: (t6_cooked(), None))
    add("B10 judge_arms on COOKED stamps: the fit VOID, every arm VOID, prediction still judged",
        lambda: (B.t6c[0]["void"] and all(x["verdict"].startswith("VOID: PCAN clock")
                                          and x["history_verdict"] == x["verdict"]
                                          and x["prediction_verdict"] == "as specified" for x in B.t6c[1:]),
                 [(x["verdict"][:30], x["prediction_verdict"]) for x in B.t6c[1:]]))
    # B10 main: nothing judged live; the fit and its VOID line printed
    add("scan: B10 judges arms only in judge_arms, after the run",
        lambda: (lambda o: (o == [], o))(judge_calls_outside(
            T6.__file__, {"judge_expired", "judge_recent", "judge_history", "judge_prediction"}, "judge_arms")))
    add("scan: B10 main calls judge_arms and prints the fit line and the VOID line",
        lambda: (lambda b: (any("judge_arms(" in x for x in b) and any("WC.fit_line(fit)" in x for x in b)
                            and any('print("witness clock VOID: %s" % fit["void"])' in x for x in b), len(b)))(
            main_body(T6.__file__)))

    # scan: recorded() only where the times are kept, never judged
    add("scan: interlock_bench calls recorded() once (run_case's record, before the gate)",
        lambda: (len(recorded_calls(IB.__file__)) == 1, recorded_calls(IB.__file__)))
    add("scan: x2_jam_cases calls recorded() once (main's record of the witness file)",
        lambda: (len(recorded_calls(XC.__file__)) == 1, recorded_calls(XC.__file__)))
    add("scan: x2_jammer calls recorded() only inside times()",
        lambda: (recorded_calls(XJ.__file__) == [], recorded_calls(XJ.__file__)))
    return out


def run_checks(work, show):
    failed, crashed = [], []
    for label, fn in checks(work):
        try:
            ok, got = fn()
        except Exception as e:
            ok, got = None, "%s: %s" % (type(e).__name__, e)
        if show or not ok:
            print("%s  %s%s" % ({True: "ok  ", False: "FAIL", None: "CRASH"}[ok], label,
                                "" if ok else "  -- %r" % (got,)))
        if ok is False:
            failed.append(label)
        elif ok is None:
            crashed.append(label)
    return failed, crashed


def child(mut_dir, work):
    """The checks in a fresh interpreter, mut_dir first on the path (None: the control)."""
    env = dict(os.environ)
    env.pop(MUT_ENV, None)
    if mut_dir:
        env[MUT_ENV] = mut_dir
    p = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--child", work],
                       capture_output=True, text=True, env=env, timeout=300)
    last = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not last:
        return [], ["no result (exit %d): %s" % (p.returncode, (p.stderr or p.stdout)[-300:])], p.stdout
    r = json.loads(last[-1][7:])
    return r["failed"], r["crashed"], p.stdout


def main(argv):
    if argv[:1] == ["--child"]:
        work = argv[1]
        os.makedirs(work, exist_ok=True)
        failed, crashed = run_checks(work, False)
        print("RESULT " + json.dumps({"failed": failed, "crashed": crashed}))
        return 0
    can_fail = "--can-fail" in argv
    work = gi_paths.scratch("witness_clock")
    if not can_fail:
        failed, crashed = run_checks(os.path.join(work, "plain"), True)
        bad = failed + crashed
        print("\n%s" % ("PASS" if not bad else "FAIL: %d" % len(bad)))
        return 1 if bad else 0
    print("-- control: the unmutated files, in a fresh interpreter")
    failed, crashed, _ = child(None, os.path.join(work, "control"))
    for x in failed + crashed:
        print("    %s" % x)
    if failed or crashed:
        print("\nREFUSED: the control fails, so no mutant is judged")
        return 1
    print("control passes")
    print("-- mutants")
    killed = errors = 0
    for n, (fname, name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, fname), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s %s: edit matched %d times" % (n, fname, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(work, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, fname), "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new))
        failed, crashed, _ = child(d, os.path.join(d, "work"))
        dead = bool(failed)
        killed += dead
        print("%s m%d %s: %s  (%d FAIL, %d CRASH%s)" % (
            "KILLED  " if dead else "SURVIVED", n, fname, name, len(failed), len(crashed),
            ("; " + failed[0][:70]) if failed else ("; " + crashed[0][:90]) if crashed else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
