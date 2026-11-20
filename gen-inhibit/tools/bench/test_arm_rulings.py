#!/usr/bin/env python3
"""Harness test for arm_rulings.py and the records it reads: the gen-inhibit reviewer's
rulings on the arm reasons on no spec 12.1 entry (answer to ba92acf0, 2026-11-19).

  1. The reset rule: a new boot is an uptime_ms fall (spec 11), its reason is recorded,
     and a panic, a watchdog or an uncommanded software reset is the device's, a brownout
     or a power-on the bench's, anything else (or no new boot) the reviewer's.
  2. The gate on the wire: spec 7's arm gate conditions 1-6, from frames encoded with
     the vehicle DBCs; each condition taken away on its own leaves the gate never held,
     freshness and the 0x411-since-close rule hold at their edges, and the held time is
     judged against GATE_HELD_S.
  3. Each check's reasons map as ruled (B5, B6, B7, B14).
  4. The records: x2_jam_cases' threshold reset reason, the gate line of a "never went
     live" arm in livetrain, threshold and busoff-rearm (the cases run with the bench
     stubbed), and the busoff-rearm answered-readings count.

    python3.13 -B gen-inhibit/tools/bench/test_arm_rulings.py [--mutants]

--mutants applies each edit in MUTANTS to copies of arm_rulings.py, x2_jam_cases.py and
live_jam_arm.py in the worktree's scratch/ (ARM_RULINGS_DIR) and requires a FAIL line
from this test on each (a crash is not a kill). Gen-inhibit tester, 2026-11-19.
"""
import argparse
import itertools
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MUT = os.environ.get("ARM_RULINGS_DIR")
sys.path.insert(0, HERE)
if MUT:
    sys.path.insert(0, MUT)
import gi_paths  # noqa: E402
import arm_rulings as AR  # noqa: E402
import interlock_judge as IJ  # noqa: E402

FAILS = []


def check(name, fn, info=None):
    try:
        ok = fn() if callable(fn) else fn
    except Exception as e:      # noqa: BLE001  a check that raises is a failed check, said so
        ok, info = False, "%s: %s" % (type(e).__name__, e)
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (info,)))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- 1. the reset rule

def page(t, up, reason="SW", live=True):
    return (t, {"uptime_ms": up, "reset_reason": reason, "inhibit_live": live})


def reset_checks():
    ss = [page(10.0, 5000), page(11.0, 6000), (11.5, None), page(12.0, 300, "PANIC"), page(13.0, 1300, "PANIC")]
    check("new_boots: an uptime fall is a new boot, with its reason", AR.new_boots(ss, 0, 99) == [(12.0, "PANIC")])
    check("new_boots: a fall outside the window is not counted", AR.new_boots(ss, 12.5, 99) == [])
    check("new_boots: compared with the reading before the window", AR.new_boots(ss, 11.9, 99) == [(12.0, "PANIC")])
    check("new_boots: rising uptime, no boot", AR.new_boots([page(1, 10), page(2, 20)], 0, 9) == [])
    check("new_boots: readings with no uptime are skipped, not a boot",
          AR.new_boots([page(1, 10), (2, {"reset_reason": "SW"}), page(3, 20)], 0, 9) == [])
    check("new_boots: two boots, both reasons",
          AR.new_boots([page(1, 900), page(2, 5, "POWERON"), page(3, 900), page(4, 5, "SW")], 0, 9)
          == [(2, "POWERON"), (4, "SW")])
    check("reset_text: the reasons, or none", AR.reset_text([(1, "PANIC"), (2, "SW")]) == "reset reason PANIC,SW"
          and AR.reset_text([]) == "reset reason none")
    for reason, kind in (("PANIC", AR.DEVICE), ("INT_WDT", AR.DEVICE), ("TASK_WDT", AR.DEVICE), ("WDT", AR.DEVICE),
                         ("SW", AR.DEVICE), ("BROWNOUT", AR.BENCH), ("POWERON", AR.BENCH),
                         ("POWERON,SW", AR.DEVICE), ("POWERON,BROWNOUT", AR.BENCH), ("POWERON,EXT", AR.UNCLEAR),
                         ("EXT", AR.UNCLEAR), ("USB", AR.UNCLEAR), ("17", AR.UNCLEAR), ("none", AR.UNCLEAR)):
        check("reset_kind: %s is the %s's" % (reason, kind),
              AR.reset_kind("device reset while armed (reset reason %s)" % reason) == kind)
    check("reset_kind: a line with no reason recorded (an older record) is the reviewer's",
          AR.reset_kind("device reset while armed") == AR.UNCLEAR)


# ---------------------------------------------------------------- 2. the gate on the wire

def enc(arb, **sig):
    if arb == 0x051:
        return IJ.db(IJ.PTX).encode_message(0x051, dict({"gen_torque_cmd": 0, "gen_rpm_ref": -1}, **sig)).hex()
    m = IJ.db(IJ.PT).get_message_by_frame_id(arb)
    full = {s.name: 0 for s in m.signals}
    if arb == 0x054:
        full["GENE_RotSpd"] = 0
    full.update(sig)
    return m.encode(full, strict=False).hex()


def wire(t0=0.0, t1=10.0, skip=(), stat=11, b7=0xC8, rpm=None, torque=0, soc_from=None, every=None, gap=None):
    """The arm gate's frames from t0 to t1 at the replay's rates (0x051 100 Hz, 0x440, 0x411,
    0x639 20 Hz, 0x617 4 Hz, 0x054 100 Hz when rpm is given): every condition holds unless
    changed. every: {id: period}; gap: (id, from, to) with no frames of id."""
    per = {0x051: 0.01, 0x440: 0.05, 0x411: 0.05, 0x639: 0.05, 0x617: 0.25, 0x054: 0.01}
    per.update(every or {})
    out = []
    for arb, p in per.items():
        if arb in skip or (arb == 0x054 and rpm is None):
            continue
        n = 0
        while t0 + n * p <= t1:
            t = round(t0 + n * p + {0x051: 0, 0x440: 0.001, 0x411: 0.002, 0x639: 0.003, 0x617: 0.004,
                                     0x054: 0.005}[arb], 6)
            n += 1
            if gap and gap[0] == arb and gap[1] <= t < gap[2]:
                continue
            if arb == 0x411 and soc_from is not None and t < soc_from:
                continue
            h = {0x051: lambda: enc(0x051, gen_torque_cmd=torque), 0x440: lambda: enc(0x440, bcm_mainc_stat=stat),
                 0x411: lambda: "4e20000000000000", 0x639: lambda: "0000000000002000",
                 0x617: lambda: "00000000000000%02x" % b7, 0x054: lambda: enc(0x054, GENE_RotSpd=rpm)}[arb]()
            out.append((t, arb, h))
    return out


def held(frames, t0=2.0, t1=10.0):
    sp = AR.gate_spans(frames, t0, t1, AR.decoder())
    return round(max([b - a for a, b in sp] or [-1]), 2)


def gate_checks():
    check("the DBC encodes the engine-off 0x051 the replay carries (B3 0xFF B4 0x7F, torque 0x8000)",
          enc(0x051)[2:10] == "0080ff7f", enc(0x051))
    check("gate: every condition on the wire -> held from the arm to the end", lambda: 7.9 <= held(wire()) <= 8.0,
          held(wire()))
    for label, w in (("no 0x051 (1)", wire(skip=(0x051,))),
                     ("0x051 asks for torque (6)", wire(torque=50)),
                     ("no 0x440 (2)", wire(skip=(0x440,))),
                     ("contactors open, bcm_mainc_stat 0 (2)", wire(stat=0)),
                     ("contactors in a state other than 11 or 12 (2)", wire(stat=10)),
                     ("no 0x411 since the close (2)", wire(skip=(0x411,))),
                     ("0x617 B7 0xCA, VCM fault active (3)", wire(b7=0xCA)),
                     ("no 0x617 (3)", wire(skip=(0x617,))),
                     ("no 0x639 (4)", wire(skip=(0x639,))),
                     ("0x054 fresh at 500 rpm (5)", wire(rpm=500))):
        check("gate: %s -> never held" % label, lambda w=w: held(w) == -1, held(w))
    check("gate: contactors closed charging, 12 (2), holds", lambda: held(wire(stat=12)) >= 7.9)
    check("gate: 0x054 fresh below 300 rpm (5) holds", lambda: held(wire(rpm=299)) >= 7.9)
    check("gate: 0x054 at 300 rpm is not below it (5)", lambda: held(wire(rpm=300)) == -1)
    check("gate: 0x054 absent does not block (5 applies only when fresh)", lambda: held(wire()) >= 7.9)
    check("gate: 0x411 only from 6 s, after the close: held from then",
          lambda: 3.9 <= held(wire(soc_from=6.0)) <= 4.0, held(wire(soc_from=6.0)))
    check("gate: 0x617 every 0.9 s is fresh (1.0 s window)", lambda: held(wire(every={0x617: 0.9})) >= 7.9)
    check("gate: 0x617 every 1.2 s is not", lambda: held(wire(every={0x617: 1.2})) <= 1.0,
          held(wire(every={0x617: 1.2})))
    check("gate: 0x051 gone for 0.6 s splits the hold (0.5 s window)",
          lambda: held(wire(gap=(0x051, 5.0, 5.6))) < 5.0, held(wire(gap=(0x051, 5.0, 5.6))))
    check("gate: 0x051 gone for 0.4 s does not", lambda: held(wire(gap=(0x051, 5.0, 5.4))) >= 7.9)
    check("gate: frames before the arm count for freshness and the close",
          lambda: held(wire(t0=0.0, t1=10.0), t0=2.0) >= 7.9 and held(wire(t0=0.0, t1=10.0), t0=0.0) >= 9.9)
    check("gate: frames after the end of the wait are not judged", lambda: 2.9 <= held(wire(), 2.0, 5.0) <= 3.0)
    check("gate: a frame that does not decode is no condition", lambda: held(
        [(t, a, "00" if a == 0x440 else h) for t, a, h in wire()]) == -1)
    check("gate: the other IDs are ignored", lambda: held(wire() + [(3.0, 0x7F1, "0801030600028813")]) >= 7.9)
    short = wire(gap=(0x617, 2.0, 99.0))      # 0x617 stops at the arm: held to its last frame (1.754 s) + 1.0 s
    check("gate_text: never held", AR.gate_text([]) == "gate on the wire: never held")
    check("gate_text: the longest hold", AR.gate_text([(1.0, 2.0), (3.0, 7.25)]) == "gate on the wire: held 4.2 s at most"
          or AR.gate_text([(1.0, 2.0), (3.0, 7.25)]) == "gate on the wire: held 4.3 s at most")
    check("gate_kind: never held is the bench's", AR.gate_kind("never went live; gate on the wire: never held") == AR.BENCH)
    check("gate_kind: held 1.0 s is the device's",
          AR.gate_kind("never went live; gate on the wire: held 1.0 s at most") == AR.DEVICE)
    check("gate_kind: held 0.9 s is the reviewer's",
          AR.gate_kind("never went live; gate on the wire: held 0.9 s at most") == AR.UNCLEAR)
    check("gate_kind: no gate line (an older record) is the reviewer's", AR.gate_kind("never went live") == AR.UNCLEAR)
    check("gate: a short hold reads as the reviewer's",
          lambda: AR.gate_kind(AR.gate_text(AR.gate_spans(short, 2.0, 10.0, AR.decoder()))) == AR.UNCLEAR,
          AR.gate_text(AR.gate_spans(short, 2.0, 10.0, AR.decoder())))


# ---------------------------------------------------------------- 3. each check's reasons

def mapping_checks():
    for line, kind in (
            ("INCONCLUSIVE | the device was not live at the start of the jam", AR.BENCH),
            ("INCONCLUSIVE | the device was not live at the start of the jam; device reset seen at [-1.0] s rel jam; "
             "reset reason PANIC", AR.DEVICE),
            ("INCONCLUSIVE | the device was not live at the start of the jam; device reset seen at [-1.0] s rel jam; "
             "reset reason POWERON", AR.BENCH),
            ("INCONCLUSIVE | the device was not live at the start of the jam; device reset seen at [-1.0] s rel jam",
             AR.UNCLEAR),
            ("INCONCLUSIVE | never went live; gate on the wire: never held", AR.BENCH),
            ("INCONCLUSIVE | never went live; gate on the wire: held 6.0 s at most", AR.DEVICE),
            ("INCONCLUSIVE | never went live", AR.UNCLEAR),
            ("INCONCLUSIVE | something else", AR.UNCLEAR)):
        check("B5: %s -> %s" % (line[15:90], kind), AR.b5_arm(line) == kind)
    for why, kind in (
            ("device reset while armed (reset reason TASK_WDT)", AR.DEVICE),
            ("device reset while armed (reset reason BROWNOUT)", AR.BENCH),
            ("device reset while armed", AR.UNCLEAR),
            ("not live at the start of the pulses", AR.BENCH),
            ("abort already latched before the pulses", AR.BENCH),
            ("3 error frames between the arm and the pulses", AR.BENCH),
            ("not armed: the reset before it was not shown (no page)", AR.BENCH),
            ("delivery was not one error frame per pulse", "inconclusive"),
            ("never went live; gate on the wire: never held", AR.BENCH),
            ("never went live; gate on the wire: held 3.0 s at most", AR.DEVICE),
            ("never went live", AR.UNCLEAR),
            ("the moon", AR.UNCLEAR)):
        check("B6: %s -> %s" % (why[:70], kind), AR.b6_arm(why) == kind)
    for v, kind in (
            ("INCONCLUSIVE: too few answered readings (3 no-arm, 30 after)", AR.BENCH),
            ("INCONCLUSIVE: the replay was not running after the re-arm (2.0 VCM 0x051/s)", AR.BENCH),
            ("INCONCLUSIVE: the page never read bus_off after the jam (12 answered readings from the jam to the re-arm)",
             AR.DEVICE),
            ("INCONCLUSIVE: the page never read bus_off after the jam (0 answered readings from the jam to the re-arm)",
             AR.BENCH),
            ("INCONCLUSIVE: the page never read bus_off after the jam", AR.UNCLEAR),
            ("INCONCLUSIVE: never went live before the jam; gate on the wire: never held", AR.BENCH),
            ("INCONCLUSIVE: never went live before the jam; gate on the wire: held 2.5 s at most", AR.DEVICE),
            ("INCONCLUSIVE: never went live before the jam", AR.UNCLEAR),
            ("INCONCLUSIVE: the moon", AR.UNCLEAR)):
        check("B7: %s -> %s" % (v[14:90], kind), AR.b7_arm(v) == kind)
    check("B14: a row read fairly is no reason", AR.b14_row("as specified") is None
          and AR.b14_row("NOT as specified") is None)
    check("B14: INVALID is the device's", AR.b14_row("INVALID (page read mode 0)") == AR.DEVICE
          and AR.b14_row("INVALID (page read mode 0); the mode POST was answered 400") == AR.DEVICE)
    check("B14: INVALID with no answer on the bench path is the bench's",
          AR.b14_row("INVALID (page read mode 0); the mode POST got no answer on the bench path (URLError: x)") == AR.BENCH)
    check("B14: both VOID reasons are the bench's",
          AR.b14_verdict("VOID: no valid mode-0 control") == AR.BENCH
          and AR.b14_verdict("VOID: the mode-0 control was ACKed, so the WiCAN is not the only possible ACKer") == AR.BENCH)
    check("B14: INCOMPLETE cut short is the bench's, otherwise the reviewer's",
          AR.b14_verdict("INCOMPLETE: modes tested [1], need OBSERVE and RESPOND; the probe was cut short after 2 of 5 "
                         "arms (OSError: x)") == AR.BENCH
          and AR.b14_verdict("INCOMPLETE: modes tested [1], need OBSERVE and RESPOND") == AR.UNCLEAR)
    check("B14: a verdict read is no reason", AR.b14_verdict("as specified") is None
          and AR.b14_verdict("NOT as specified") is None)
    check("VERDICT: bench VOID, device FAIL, unclear ERROR",
          AR.VERDICT == {AR.BENCH: "VOID", AR.DEVICE: "FAIL", AR.UNCLEAR: "ERROR"})


# ---------------------------------------------------------------- 4. the records

def tried(fn):
    try:
        return fn()
    except Exception as e:      # noqa: BLE001
        return "raised %s: %s" % (type(e).__name__, e)


class FakeWit:
    def __init__(self, frames):
        import threading
        self.frames, self.lock, self.status, self.status_error = frames, threading.Lock(), [], None

    def clocked(self):
        return list(self.frames), [], "kvaser_hw (stub)"


class FakePoller:
    def __init__(self, every=0.1):
        self.samples = [(101.0 + 0.1 * k, {"mode": 3, "inhibit_live": False, "tx_ok": 0, "uptime_ms": 5000 + 100 * k})
                        for k in range(20)]

        class Flag:
            def set(flag):
                pass
        self.stop_flag = Flag()

    def start(self):
        pass

    def join(self, timeout=None):
        pass


def stub_bench(C):
    C.LF.dev_post = lambda path: None
    C.LF.page = lambda: {}
    C.Poller = FakePoller
    C.sleep_until = lambda t: None
    C.attach_console = lambda arm, t0, t1: None
    C.replay_rate_device_off = lambda frames, ph: 100.0
    C.time.sleep = lambda s: None
    C.wait_live = lambda timeout=15.0: None

    class St:
        changes, reopens = [], 0

    class Th:
        def is_alive(self):
            return False
    C.replay_start = lambda seconds=90.0, auto_reset=False: (None, Th(), {}, St(), None)
    C.replay_stop = lambda pc, th, st, tx: None
    clock = itertools.count(100.0, 1.0)
    C.time.time = lambda: next(clock)


def record_checks():
    import io
    import contextlib
    import x2_jam_cases as C
    import live_jam_arm as LJ
    check("x2_jam_cases keeps the gate's IDs from the witness", set(AR.GATE_IDS) <= C.KEEP_IDS)
    check("the tools read arm_rulings from the copy under test", C.AR is AR and LJ.AR is AR)
    # the threshold judge's reset carries the new boot's reason
    ph = {"arm_posted": 100.0, "pulses_start": 110.0, "pulses_end": 120.0, "mode0_posted": 135.0}
    ss = []
    for k in range(345):
        t = round(100.5 + 0.1 * k, 3)
        boot = t >= 113.0
        ss.append((t, {"mode": 3, "inhibit_live": not boot, "abort_latched": False, "tx_ok": 0 if boot else
                       int(t * 10), "uptime_ms": int(round((t - 113.0 if boot else t) * 1000)),
                       "reset_reason": "INT_WDT" if boot else "SW"}))
    j = C.judge_threshold([110.001 + k for k in range(10)], ss, ph, 10, 1.0)
    check("threshold: a reset while armed records its reason in why",
          j.get("why") == "device reset while armed (reset reason INT_WDT)", j.get("why"))
    check("threshold: and the new boot in the record", j.get("new_boots_rel_pulses_s") == [(3.0, "INT_WDT")],
          j.get("new_boots_rel_pulses_s"))
    noboot = [(x, dict(s, uptime_ms=None)) for x, s in ss]
    check("threshold: a reset with no new boot on the page says reason none",
          C.judge_threshold([110.001 + k for k in range(10)], noboot, ph, 10, 1.0).get("why")
          == "device reset while armed (reset reason none)")
    ph2 = {"arm_posted": 2.0, "mode0_posted": 10.0}
    check("gate_line: from the arm to the mode-0 post", lambda: C.gate_line(wire(), ph2).startswith(
        "gate on the wire: held 8.0 s"), tried(lambda: C.gate_line(wire(), ph2)))
    check("gate_line: ends at the mode-0 post, not at the record's end",
          lambda: C.gate_line(wire(t1=20.0), {"arm_posted": 2.0, "mode0_posted": 5.0}).startswith(
              "gate on the wire: held 3.0 s"), tried(lambda: C.gate_line(wire(t1=20.0), {"arm_posted": 2.0,
                                                                                         "mode0_posted": 5.0})))
    check("gate_line: never held",lambda: C.gate_line(wire(skip=(0x440,)), ph2) == "gate on the wire: never held")
    # judge_busoff_rearm: the page never reading bus_off records how many readings were answered
    php = {"jam_start": 100.0, "jam_stop": 105.0, "rearm_posted": 135.0, "rearm_end": 165.0}
    rs = [(100.0 + k, {"drv_state": "running", "bus_on": True, "inhibit_live": False, "uptime_ms": 1000 * k})
          for k in range(65)]
    v = C.judge_busoff_rearm(rs, [], php, 200, "reinstalls")["verdict"]
    check("busoff-rearm: never bus_off records the answered readings from the jam to the re-arm",
          v == "INCONCLUSIVE: the page never read bus_off after the jam (35 answered readings from the jam to the re-arm)",
          v)
    v0 = C.judge_busoff_rearm([(x, None) for x, _ in rs], [], php, 200, "reinstalls")["verdict"]
    check("busoff-rearm: none answered records 0", "(0 answered readings from the jam to the re-arm)" in v0, v0)
    check("busoff-rearm: and reads as the bench's (device silent) beside the device's with readings",
          AR.b7_arm(v0) == AR.BENCH and AR.b7_arm(v) == AR.DEVICE)
    # the cases: a "never went live" arm carries the gate line, in its record and its printed line
    stub_bench(C)
    for name, case, args, key in (
            ("livetrain", C.case_livetrain, argparse.Namespace(w=20, p=2000, ms=5000, arms=1), "summary"),
            ("threshold", C.case_threshold, argparse.Namespace(w=20, n=10, interval_ms=1000, arms=1, lead_s=5.0,
                                                               reset_wican=False, judge_first=None, ecc=False), "why"),
            ("busoff-rearm", C.case_busoff_rearm, argparse.Namespace(w=20, p=2000, ms=5000, arms=1,
                                                                     expect="reinstalls"), "verdict")):
        for wname, fr, want in (("held", wire(t0=90.0, t1=140.0), "gate on the wire: held"),
                                ("never held", wire(t0=90.0, t1=140.0, skip=(0x617,)), "gate on the wire: never held")):
            rec, out = {}, io.StringIO()
            with contextlib.redirect_stdout(out):
                r = tried(lambda: case(args, rec, None, FakeWit(fr)))
            arm = (rec.get("arms") or [{}])[0]
            got = (arm.get("judge") or {}).get(key, "")
            check("%s: a never-live arm's record carries the gate (%s)" % (name, wname),
                  not isinstance(r, str) and "never went live" in got and want in got, r if isinstance(r, str) else got)
            check("%s: ... and its printed line (%s)" % (name, wname), want in out.getvalue(), out.getvalue()[-300:])
    check("live_jam_arm: the not-live summary with no reset carries no reason",
          "reset reason" not in LJ.judge({"phases": {"replay_start": 0.0, "arm_posted": 3.0, "jam_start": 10.0,
                                                     "jam_stop": 15.0},
                                          "samples": [{"pt": 9.5, "snap": {"inhibit_live": False}}], "wire": [],
                                          "witness_error_pts": []})["summary"])


MUTANTS = [
    ("arm_rulings.py", "a SW reset read as the bench's", 'RESET_DEVICE = ("PANIC", "INT_WDT", "TASK_WDT", "WDT", "SW")',
     'RESET_DEVICE = ("PANIC", "INT_WDT", "TASK_WDT", "WDT")'),
    ("arm_rulings.py", "a watchdog read as unclear", '"TASK_WDT", "WDT", "SW")', '"WDT", "SW")'),
    ("arm_rulings.py", "a power-on read as unclear", 'RESET_BENCH = ("BROWNOUT", "POWERON")', 'RESET_BENCH = ("BROWNOUT",)'),
    ("arm_rulings.py", "a mixed bench and unknown reason read as the bench's",
     "    if all(r in RESET_BENCH for r in rs):\n", "    if any(r in RESET_BENCH for r in rs):\n"),
    ("arm_rulings.py", "no reason recorded read as the bench's", '    if not m or m.group(1) == "none":\n        return UNCLEAR\n',
     '    if not m or m.group(1) == "none":\n        return BENCH\n'),
    ("arm_rulings.py", "a rise read as a boot", "        if last is not None and u < last and t0 <= t < t1:\n",
     "        if last is not None and u != last and t0 <= t < t1:\n"),
    ("arm_rulings.py", "a boot outside the window counted", "        if last is not None and u < last and t0 <= t < t1:\n",
     "        if last is not None and u < last:\n"),
    ("arm_rulings.py", "the reset reason not kept", '            out.append((t, s.get("reset_reason")))\n',
     '            out.append((t, "SW"))\n'),
    ("arm_rulings.py", "0x617 fresh for 0.5 s", "0x617: 1.0, 0x639: 0.5}", "0x617: 0.5, 0x639: 0.5}"),
    ("arm_rulings.py", "0x051 fresh for 1 s", "FRESH_S = {0x051: 0.5,", "FRESH_S = {0x051: 1.0,"),
    ("arm_rulings.py", "contactors 12 not closed", "CLOSED = (11, 12)", "CLOSED = (11,)"),
    ("arm_rulings.py", "the 0x411-since-close rule dropped", " and soc_t is not None and soc_t >= closed_since", ""),
    ("arm_rulings.py", "the VCM fault not read", "last[0x617][1][\"b7\"] != FAULT_ACTIVE", "True"),
    ("arm_rulings.py", "the generator speed not read", 'last[0x054][1].get("GENE_RotSpd", 300) < 300', "True"),
    ("arm_rulings.py", "0x054 required present", "(not fresh(0x054) or ", "(fresh(0x054) and "),
    ("arm_rulings.py", "the torque command not read", '              and last[0x051][1].get("gen_torque_cmd", 1) == 0\n', ""),
    ("arm_rulings.py", "0x639 not required", "              and fresh(0x639)\n", ""),
    ("arm_rulings.py", "frames after the wait judged", "        if t < t0 or t > t1:\n", "        if t < t0:\n"),
    ("arm_rulings.py", "a hold ends one frame late", "            spans.append((start, prev))\n            start = None\n",
     "            spans.append((start, t))\n            start = None\n"),
    ("arm_rulings.py", "the held bound 0.5 s", "GATE_HELD_S = 1.0", "GATE_HELD_S = 0.5"),
    ("arm_rulings.py", "a short hold read as the device's", "    return DEVICE if float(m.group(2)) >= GATE_HELD_S else UNCLEAR\n",
     "    return DEVICE\n"),
    ("arm_rulings.py", "no gate line read as the bench's", "    if not m:\n        return UNCLEAR\n    if m.group(1):\n",
     "    if not m:\n        return BENCH\n    if m.group(1):\n"),
    ("arm_rulings.py", "B5 not-live with a reset read as the bench's",
     '        return reset_kind(line) if "device reset seen" in line else BENCH\n', "        return BENCH\n"),
    ("arm_rulings.py", "B6 delivery over read as void", '    if B6_SHORT in why:\n        return "inconclusive"\n', ""),
    ("arm_rulings.py", "B6 latched before read as unclear", '"abort already latched before the pulses",\n            ', ""),
    ("arm_rulings.py", "B7 bus_off with no count read as the device's", "        if not m:\n            return UNCLEAR\n",
     "        if not m:\n            return DEVICE\n"),
    ("arm_rulings.py", "B7 silent page read as the device's", "        return BENCH if int(m.group(1)) == 0 else DEVICE\n",
     "        return DEVICE\n"),
    ("arm_rulings.py", "B14 INCOMPLETE always the bench's", "        return BENCH if B14_CUT_SHORT in verdict else UNCLEAR\n",
     "        return BENCH\n"),
    ("arm_rulings.py", "B14 INVALID always the device's", "    return BENCH if B14_BENCH_POST in result else DEVICE\n",
     "    return DEVICE\n"),
    ("x2_jam_cases.py", "the witness keeps no gate IDs", " | set(AR.GATE_IDS)\n", "\n"),
    ("x2_jam_cases.py", "the threshold reset reason not recorded",
     'why="device reset while armed (%s)" % AR.reset_text(boots),', 'why="device reset while armed",'),
    ("x2_jam_cases.py", "the gate judged to the end of the record, not the wait",
     'ph["arm_posted"], ph["mode0_posted"], AR.decoder()', 'ph["arm_posted"], 1e12, AR.decoder()'),
    ("x2_jam_cases.py", "livetrain's never-live arm has no gate line",
     '"summary": "never went live; " + gate_line(frames, ph)}', '"summary": "never went live"}'),
    ("x2_jam_cases.py", "threshold's never-live arm has no gate line",
     '"why": "never went live; " + gate_line(frames, ph)}', '"why": "never went live"}'),
    ("x2_jam_cases.py", "busoff-rearm's never-live arm has no gate line",
     '"INCONCLUSIVE: never went live before the jam; " + gate_line(frames, ph)}',
     '"INCONCLUSIVE: never went live before the jam"}'),
    ("x2_jam_cases.py", "the answered readings counted over the whole arm",
     '% len(window_states(samples, ph["jam_start"], ph["rearm_posted"])))',
     '% len(window_states(samples, 0, 1e12)))'),
    ("live_jam_arm.py", "the not-live summary carries a reason without a reset",
     '                             if resets else ""))', '                             if resets else "; reset reason none"))'),
]


def mutants():
    base = gi_paths.scratch("mutants-arm_rulings")
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("ERROR    control: the unmutated files fail this test (exit %d)" % r.returncode)
        return 1
    killed = errors = 0
    for n, (fname, name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, fname), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, src.count(old), fname))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        for f in ("arm_rulings.py", "x2_jam_cases.py", "live_jam_arm.py"):
            shutil.copyfile(os.path.join(HERE, f), os.path.join(d, f))
        open(os.path.join(d, fname), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, ARM_RULINGS_DIR=d, PYTHONDONTWRITEBYTECODE="1"), timeout=300)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s: %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, fname, name, r.returncode,
                                                      len(fl), ("; " + fl[0][:100]) if fl else
                                                      (r.stderr.strip()[-160:] if r.stderr else "")))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    reset_checks()
    gate_checks()
    mapping_checks()
    record_checks()
    print("PASS" if not FAILS else "FAIL: %d" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
