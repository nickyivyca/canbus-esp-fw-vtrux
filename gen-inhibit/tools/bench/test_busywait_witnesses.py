#!/usr/bin/env python3
"""Harness test for the busy-wait tools' witnesses (gen-inhibit reviewer's answer (5) to message 43ffe9bb,
and its ruling (i) on message f8e5f9aa, 2026-11-19).

    python3.13 -B gen-inhibit/tools/bench/test_busywait_witnesses.py [--case B4,B12,void,...] [--mutants]

No hardware. Run cb08 showed a thread witness starved by a busy-wait pacer in its own process (e4: 1.3-30 %
of its replay kept); proc_witness.ProcWitness took e4's witness out. Of the other tools the suite runs, each
was measured with its own witness class, under its own pacer, at its own rate:

  B12  restart_error_frames.kvaser_witness  its loop's `while perf() < due: pass`           2355 frames/s
  B4   skip_method_bench.Witness       skip_method_bench.Player (sleep to 2 ms, then spin)   454 frames/s
  B1   slcan_refusal_probe.Witness     skip_method_bench.Player                             454 frames/s
  B9   wrong_bitrate_errors.PcanWitness  skip_method_bench.Player                           454 frames/s
  B10  trip6_rearm_check.Witness       skip_method_bench.Player                             454 frames/s
  B20  interlock_bench.Witness         interlock_bench.play (sleep to 2 ms, then spin)      454 frames/s
  B5-B8  x2_jam_cases.Witness          lowid_flood_skip_bench.play (the same)               454 frames/s

Only B12's continuous spin starved a thread witness; the six sleep-to-2-ms pacers did not (the thread
read every frame, also with the kernel's default receive buffer and at feeds of 1000 and 2400 frames/s).
The ruling: B12's witness reads in a forked child (proc_witness.ProcThread), the six keep theirs in a
thread (proc_witness.ThreadWitness), and a witness whose loop dies, child or thread, VOIDs.

The rates: the interlock slice the Player, play() and the slice cases replay, 5445 frames in 12 s (the
stimulus cache, lowid_flood_stimulus.json: 0x051, 0x054, 0x639, 0x471 every 10 ms, 0x440 and 0x411 every
50 ms, 0x592 every 100 ms, 0x617 every 250 ms), here built with the same ids and periods; B12's, the e4
window it replays, 70637 frames in 30 s (e4_device_calibration.load_window). B20's host scenarios run at
97-114 frames/s, under the slice's rate.

The bus is a datagram socketpair: a feeder process (the wire) sends RATE frames per s for SECONDS s,
non-blocking, so a frame the reader does not take in time is lost as a CAN raw socket loses it; the
witness reads the other end through a bus object with recv(timeout).

  B12 every frame   kvaser_witness, in its child, reads every frame the feeder sent; the child ends with
                    exit code 0 and no error.
  B12 can-fail      the same loop in a thread of this process (proc_witness.thread_twin) does NOT read
                    every frame. If it ever does, the case no longer tells them apart and FAILs.
  six regression    each of the six tools' thread witness reads every frame under its own pacer at its
                    own rate. What it guards (measured 2026-11-19): a thread under a continuous spin read
                    about 1000 frames/s (B12's can-fail: 4068 of 9420 in 4 s), and with each of the three
                    pacers turned into a continuous spin at 454 frames/s the thread still read every frame;
                    so it fails when a rate rises past what a thread keeps up with, not on a change of
                    pacer at the slice's rate.

Then each tool's VOID path for a witness whose loop died (its bus raised): void_reason and void_line name
a dead thread and a dead child; B4's run_arm marks the arm and both judges VOID it; B1's witness dies
and a command VOIDs where it would PASS, a FAIL standing, and the suite's B1 judge VOIDs; B10's and B12's
witnesses die and give the "witness VOID" line, which the suite's B9, B10 and B12 judges read first (B12:
not a PASS on 0 error frames, a FAIL standing); B20's run_case VOID, nothing judged; x2_jam_cases'
clocked() raises and run_guarded VOIDs the run, which the suite's jam judge reads. B9's main is run
with a dying witness in test_wrong_bitrate_sequence.py.

--mutants applies each edit in MUTANTS to a copy of its module in the worktree's scratch/ (loaded in that
module's place) and requires a FAIL line on each. Runs in build.slice (AGENTS.md); about 1 min, the
mutants about 10 min.
"""
import importlib.util
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import gi_paths  # noqa: E402  scratch/ folders

# a mutant run loads its module from a copy: BUSYWAIT_MUTANT="module=path"
if os.environ.get("BUSYWAIT_MUTANT"):
    _name, _path = os.environ["BUSYWAIT_MUTANT"].split("=", 1)
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)

import proc_witness as PW  # noqa: E402

SECONDS = 4.0
SLICE_RATE = 454.0
E4_RATE = 2355.0
SLICE_IDS = [(0x051, 0.010), (0x054, 0.010), (0x639, 0.010), (0x471, 0.010), (0x440, 0.050),
             (0x411, 0.050), (0x592, 0.100), (0x617, 0.250)]

fails, oks = [], 0


def check(name, ok, detail=""):
    global oks
    if ok:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[:400]))
    sys.stdout.flush()


def slice_stim(seconds=12.0):
    """The interlock slice's ids and periods: [(t, id, data)], 454 frames/s."""
    out = []
    for ident, period in SLICE_IDS:
        n = int(round(seconds / period))
        out += [(round(k * period, 6), ident, bytes([k & 0xFF] * 8)) for k in range(n)]
    return sorted(out)


class Msg(object):
    def __init__(self, ident, k, t):
        self.arbitration_id = ident
        self.data = struct.pack("<II", k, k ^ 0x5A5A5A5A)
        self.is_error_frame, self.is_extended_id = False, False
        self.timestamp = t
        self.host_timestamp = t


class SockBus(object):
    """One end of the socketpair, read as python-can's recv(timeout); ids cycle through ids."""

    def __init__(self, sock, ids):
        self.sock, self.ids, self.n = sock, ids, 0

    def recv(self, timeout=None):
        try:
            if timeout:
                self.sock.settimeout(timeout)
            else:
                self.sock.setblocking(False)
            d = self.sock.recv(64)
        except (socket.timeout, BlockingIOError):
            return None
        self.n += 1
        k = struct.unpack("<I", d[:4])[0]
        return Msg(self.ids[k % len(self.ids)], k, time.time())

    def shutdown(self):
        pass


class RaisingBus(object):
    """A bus whose read raises, as an adapter that went away."""

    def recv(self, timeout=None):
        time.sleep(0.01)
        raise OSError("the adapter went away (test)")

    def shutdown(self):
        pass


class FakeTx(object):
    """The replay's adapter: takes every frame, sends nothing."""

    def __init__(self):
        self.n = 0

    def send(self, msg, timeout=None):
        self.n += 1


def feeder(sock, n, rate, sent_w):
    sock.setblocking(False)
    took = 0
    t0 = time.perf_counter()
    for k in range(n):
        due = t0 + k / rate
        while time.perf_counter() < due:
            time.sleep(0)
        try:
            sock.send(struct.pack("<II", k, 0) + b"\0" * 8)
            took += 1
        except BlockingIOError:
            pass
    os.write(sent_w, struct.pack("<I", took))
    os._exit(0)


def fake_kvaser_bus(bus):
    kb = types.ModuleType("kvaser_bus")
    kb.kvaser_bus = lambda **k: bus
    kb.bus_error = lambda b, m: True
    return kb


def with_module(name, mod, fn):
    saved = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        return fn()
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


# ---------------------------------------------------------------------- the pacers, each the tool's own

def pace_player(seconds):
    import skip_method_bench as S
    p = S.Player(FakeTx(), slice_stim())
    p.start()
    time.sleep(seconds)
    p.stop_flag.set()
    p.join(15)


def pace_lfplay(seconds):
    import lowid_flood_skip_bench as LF
    th = threading.Thread(target=LF.play, args=(FakeTx(), slice_stim(), seconds), daemon=True)
    th.start()
    th.join(seconds + 15)


def pace_ilplay(seconds):
    import can
    import interlock_bench as I
    stim = [x for x in slice_stim() if x[0] < seconds]
    parsed = {"frames": [(int(t * 1e6), a, d.hex()) for t, a, d in stim]}
    I.play(can, FakeTx(), parsed, time.perf_counter(), [])


def pace_spin(seconds):
    """restart_error_frames' replay loop: a busy-wait to each frame's due time, then the send."""
    tx, perf = FakeTx(), time.perf_counter
    base = perf()
    n = int(E4_RATE * seconds)
    for k in range(n):
        due = base + k / E4_RATE
        while perf() < due:
            pass
        tx.send(None, timeout=0.5)


# ---------------------------------------------------------------------- the witnesses, each the tool's own

def make_b12(bus, twin=False):
    import restart_error_frames as R
    got, errs, not_bus = [], [], []
    stop = PW.event()
    saved = R.PW
    if twin:
        R.PW = types.SimpleNamespace(ProcThread=PW.thread_twin(PW.ProcThread))
    try:
        w = R.kvaser_witness(bus, got, errs, not_bus, stop, lambda kv, m: True)
    finally:
        R.PW = saved
    w.stop_ev = stop
    w.count = lambda: len(got) + len(errs) + len(not_bus)
    return w


def make_class(module, cls_name, count, ctor="bus", stop="stop"):
    def make(bus, twin=False):
        mod = __import__(module)
        cls = getattr(mod, cls_name)
        if twin:
            cls = PW.thread_twin(cls)
        if ctor == "bus":
            w = cls(bus)
        elif ctor == "can":
            w = with_module("kvaser_bus", fake_kvaser_bus(bus), lambda: cls(None))
        else:
            w = with_module("kvaser_bus", fake_kvaser_bus(bus), lambda: cls())
        w.stop_ev = getattr(w, stop)
        w.count = lambda: count(w)
        return w
    return make


KEEP_B4 = [0x050, 0x051, 0x6A5, 0x7F1, 0x7F2, 0x7F8, 0x7F9]
KEEP_X2J = [0x051, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9]
ANY_IDS = [i for i, _p in SLICE_IDS]

B4_W = make_class("skip_method_bench", "Witness", lambda w: len(w.frames) + len(w.errors), stop="stop_flag")
B1_W = make_class("slcan_refusal_probe", "Witness", lambda w: len(w.ids) + w.errors)
B9_W = make_class("wrong_bitrate_errors", "PcanWitness", lambda w: len(w.points))
B10_W = make_class("trip6_rearm_check", "Witness", lambda w: len(w.points))
B20_W = make_class("interlock_bench", "Witness", lambda w: len(w.frames) + len(w.errs), ctor="can", stop="stop_flag")
X2J_W = make_class("x2_jam_cases", "Witness", lambda w: len(w.frames) + len(w.errs) + w.not_bus_errors,
                   ctor="none", stop="stop_flag")

# (case, what, make, ids, rate, pacer); the six keep their thread witness (ruling (i))
THREAD_CASES = [
    ("B4", "skip_method_bench.Witness", B4_W, KEEP_B4, SLICE_RATE, pace_player),
    ("B1", "slcan_refusal_probe.Witness", B1_W, ANY_IDS, SLICE_RATE, pace_player),
    ("B9", "wrong_bitrate_errors.PcanWitness", B9_W, ANY_IDS, SLICE_RATE, pace_player),
    ("B10", "trip6_rearm_check.Witness", B10_W, ANY_IDS, SLICE_RATE, pace_player),
    ("B20", "interlock_bench.Witness", B20_W, ANY_IDS, SLICE_RATE, pace_ilplay),
    ("B5-B8", "x2_jam_cases.Witness", X2J_W, KEEP_X2J, SLICE_RATE, pace_lfplay),
]


def run(make, ids, rate, pace, twin=False):
    """Feeds rate x SECONDS frames while this process runs the tool's pacer; returns (frames the
    socket took, frames sent, the witness after join)."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    w = make(SockBus(a, ids), twin)
    w.start()
    time.sleep(0.3)
    r, wfd = os.pipe()
    n = int(rate * SECONDS)
    pid = os.fork()
    if pid == 0:
        a.close()
        os.close(r)
        feeder(b, n, rate, wfd)
    b.close()
    os.close(wfd)
    pace(SECONDS + 0.3)
    os.waitpid(pid, 0)
    took = struct.unpack("<I", os.read(r, 4))[0]
    os.close(r)
    time.sleep(0.5)
    w.stop_ev.set()
    w.join(10.0)
    a.close()
    return took, n, w


def wire_cases(only):
    if not only or "B12" in only:
        took, n, w = run(make_b12, ANY_IDS, E4_RATE, pace_spin)
        got = w.count()
        check("B12 every frame: restart_error_frames.kvaser_witness in its child read every frame on the wire, "
              "under its own busy-wait at %d/s" % E4_RATE, took == n and got == n,
              "read %d, the socket took %d, of %d sent" % (got, took, n))
        check("B12 every frame: the witness reads in a child process, which ended with exit code 0, no error",
              isinstance(w.proc_info["pid"], int) and not w.is_alive() and w.proc_info["exitcode"] == 0
              and w.proc_info["error"] is None, w.proc_info)
        took_t, n_t, wt = run(make_b12, ANY_IDS, E4_RATE, pace_spin, twin=True)
        got_t = wt.count()
        check("B12 can-fail: the same loop in a thread of the pacer's process misses frames on the wire",
              got_t < n_t, "the thread read %d of %d: this case no longer tells them apart" % (got_t, n_t))
        print("     B12: thread %d of %d (%.1f %%); child %d of %d" % (got_t, n_t, 100.0 * got_t / n_t, got, n))
    for cid, what, make, ids, rate, pace in THREAD_CASES:
        if only and cid not in only:
            continue
        took, n, w = run(make, ids, rate, pace)
        got = w.count()
        check("%s regression: %s, a thread witness, read every frame under its own pacer at %d/s"
              % (cid, what, rate), took == n and got == n, "read %d, the socket took %d, of %d sent" % (got, took, n))
        check("%s regression: its thread ended with no error recorded" % cid,
              not w.is_alive() and w.proc_info["error"] is None and PW.void_reason(w, running=False) is None,
              w.proc_info)


# ---------------------------------------------------------------------- the VOID paths

def dead(make_witness):
    """A witness started on a bus that raises, its loop given time to die."""
    w = make_witness(RaisingBus())
    w.start()
    time.sleep(0.6)
    return w


def void_cases():
    import skip_method_bench as S
    import slcan_refusal_probe as P
    import suite_judges as SJ
    import x2_jam_cases as X
    import witness_clock as WC

    # void_reason / void_line, for a thread and a child
    for name, mk, kind in (("B4 (thread)", B4_W, "thread"), ("B10 (thread)", B10_W, "thread"),
                           ("B12 (child)", make_b12, "child")):
        w = dead(mk)
        why = PW.void_reason(w)
        reason, line = (w.stop_ev.set(), w.join(3.0), PW.void_line(w))[2]
        check("void_reason %s: a witness whose bus raised is named, with the error" % name,
              (why or "").startswith("the witness %s failed" % kind) and "went away" in why, why)
        check("void_line %s: the witness VOID line, which the suite reads" % name,
              line == "witness VOID: %s" % reason and bool(SJ.WITNESS_VOID_RE.search(line or "")), line)
    w = B4_W(SockBus(socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)[0], KEEP_B4))
    w.start()
    w.stop_ev.set()
    w.join(3.0)
    check("void_line: a witness that ended whole gives no line (the control)", PW.void_line(w) == (None, None),
          PW.void_line(w))

    # B4: run_arm with the device and Player stubbed, the witness's loop dead
    w = dead(B4_W)

    class LF(object):
        @staticmethod
        def dev_post(path, timeout=None):
            return True

        @staticmethod
        def page():
            return {"mode": 3, "inhibit_live": True, "skips_withdrawn": 0, "skips_late": 0}

    class Player(object):
        done = []

        def request(self, *a):
            pass
    saved = (S.LF, S.time)
    S.LF, S.time = LF, types.SimpleNamespace(sleep=lambda s: None, perf_counter=time.perf_counter)
    try:
        arm = {"name": "w3"}
        S.run_arm("w3", Player(), w, arm)
    finally:
        S.LF, S.time = saved
    w.join(3.0)
    check("B4 run_arm: an arm read by a dead witness is marked witness_proc_void",
          "went away" in (arm.get("witness_proc_void") or ""), arm.get("witness_proc_void"))
    for name, fn in (("judge_arm", S.judge_arm), ("judge_arm_v2", S.judge_arm_v2)):
        v = fn(dict(arm, went_live=True))["verdict"]
        check("B4 %s: such an arm is VOID, naming the dead witness" % name,
              v.startswith("VOID: the witness thread failed"), v)

    # B1: its witness dies; a command VOID where it would pass, a FAIL standing; the suite's judge
    w1 = dead(B1_W)
    why1 = PW.void_reason(w1)
    check("B1: a dead witness is named", (why1 or "").startswith("the witness thread failed"), why1)
    pre = {"mode": 0, "tx_ok": 1, "refused_slcan": 0, "refused_can_send": 0, "keys": []}
    post = dict(pre, refused_slcan=1)
    r = P.command_result("off", "tcp", "O", True, "no reply", pre, post, [], 0, why1)
    check("B1 command_result: with the witness dead, a command that would pass is VOID",
          r["verdict"] == "VOID" and r["why"] == [why1], r)
    r2 = P.command_result("off", "tcp", "t", True, "no reply", pre, post, [P.INJECT_ID], 0, why1)
    check("B1 command_result: a FAIL seen by the short witness stands", r2["verdict"] == "FAIL", r2)
    r3 = P.command_result("off", "tcp", "O", True, "no reply", pre, post, [], 0, None)
    check("B1 command_result: the witness whole, the command PASSes", r3["verdict"] == "PASS", r3)
    s = P.summary([r, r3])
    check("B1 summary: counts the VOID command", s.get("void") == 1, s)
    work = gi_paths.scratch("busywait-witnesses-judges")
    gi_paths.remove_tree(work)
    os.makedirs(work)
    try:
        def judged(cid, body):
            d = os.path.join(work, cid)
            gi_paths.remove_tree(d)
            os.makedirs(d)
            for n, t in body:
                with open(os.path.join(d, n), "w", encoding="utf-8") as fh:
                    fh.write(t)
            return SJ.JUDGES[cid][1](SJ.Files(d))[0]
        b1 = [("B1_slcan_refusal_stdout.txt", "\n".join([P.line(r), P.line(r3), "summary %r" % (s,)]) + "\n")]
        res = judged("B1", b1)
        check("suite B1: a VOID command and no FAIL -> B1 VOID", res["verdict"] == "VOID", res)
        s2 = P.summary([r, r2])
        res = judged("B1", [("B1_slcan_refusal_stdout.txt",
                             "\n".join([P.line(r), P.line(r2), "summary %r" % (s2,)]) + "\n")])
        check("suite B1: a VOID command beside a FAIL -> B1 FAIL", res["verdict"] == "FAIL", res)

        # B9 / B10: the witness VOID line, read first
        _r, line = PW.void_line(w1)
        for cid, name in (("B9", "B9_wrong_bitrate_calibrate.txt"), ("B10", "B10_trip6_rearm_check.txt")):
            res = judged(cid, [(name, line + "\nwitness clock VOID: y\n")])
            check("suite %s: the witness VOID line -> VOID, naming the dead witness" % cid,
                  res["verdict"] == "VOID" and "witness thread failed" in res.get("detail", ""), res)

        # B12: a cycle with 0 error frames read by a dead witness is not a PASS
        cyc = ("cycle 0 {tag}: hook None, reset ['{rr}'], build x, live_at_post None, boot 1.200 s after POST, "
               "errors {e}, burst x\n")
        files = []
        for k, (cond, rs) in enumerate((("live", "reboot"), ("live", "crash"), ("live", "crash"), ("live", "reboot"),
                                        ("live", "reboot"), ("live", "crash"), ("off", "reboot")), 1):
            body = cyc.format(tag=cond, rr="SW" if rs == "reboot" else "PANIC", e=0)
            if k == 3:
                body += "witness VOID: the witness child failed: OSError: x\n"
            files.append(("B12_%d_%s_%s.txt" % (k, cond, rs), body))
        saved_rec = SJ.b12_record
        SJ.b12_record = lambda f: None
        try:
            res = judged("B12", files)
            check("suite B12: a cycle's witness died, 0 error frames -> VOID, not PASS", res["verdict"] == "VOID", res)
            files[0] = (files[0][0], cyc.format(tag="live", rr="SW", e=5))
            res = judged("B12", files)
            check("suite B12: beside a cycle with error frames -> FAIL stands", res["verdict"] == "FAIL", res)
            files = [(n, b.replace("witness VOID: the witness child failed: OSError: x\n", "")) for n, b in files[1:]]
            files.insert(0, ("B12_1_live_reboot.txt", cyc.format(tag="live", rr="SW", e=0)))
            res = judged("B12", files)
            check("suite B12: every witness whole, 0 error frames -> PASS (the control)", res["verdict"] == "PASS", res)
        finally:
            SJ.b12_record = saved_rec
    finally:
        gi_paths.remove_tree(work)
    w1.join(3.0)

    # x2_jam_cases: clocked() raises on a dead witness; run_guarded VOIDs; the suite's jam judge reads it
    xw = dead(X2J_W)
    try:
        xw.clocked()
        raised = None
    except PW.WitnessFailed as e:
        raised = e.reason
    except WC.ReceiptClock as e:
        raised = "ReceiptClock: %s" % e
    xw.close()
    check("x2_jam_cases clocked(): a dead witness raises WitnessFailed",
          (raised or "").startswith("the witness thread failed"), raised)
    rec = {}

    def case(a, rec, con, wit):
        wit.clocked()
    X.run_guarded(case, None, rec, None, xw)
    check("x2_jam_cases run_guarded: the run VOID, naming the dead witness",
          (rec.get("verdict") or "").startswith("VOID: the witness thread failed"), rec)
    check("suite jam judge: reads that run's verdict line as VOID", SJ.jam_void("verdict %s\n" % rec.get("verdict")),
          rec.get("verdict"))
    check("suite jam judge: a run's ordinary verdict line is not VOID (the control)",
          not SJ.jam_void("verdict None \n"), "")

    # B20: run_case VOID, nothing judged (interlock_bench's run_case with its bench stubbed as
    # test_witness_clock stubs it)
    spec = importlib.util.spec_from_file_location("twc_for_busywait", os.path.join(HERE, "test_witness_clock.py"))
    TWC = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(TWC)
    ib_path = os.path.join(HERE, "interlock_bench.py")
    if os.environ.get("BUSYWAIT_MUTANT", "").startswith("interlock_bench="):
        ib_path = os.environ["BUSYWAIT_MUTANT"].split("=", 1)[1]
    spec = importlib.util.spec_from_file_location("ib_for_busywait", ib_path)
    IB = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(IB)
    ibw = gi_paths.scratch("busywait-witnesses-ib")
    gi_paths.remove_tree(ibw)
    os.makedirs(ibw)
    try:
        spy = TWC.ib_setup(IB, ibw)
        real = IB.REAL_WITNESS

        def on(bus, fail):
            class W(real):
                def __init__(self, can):
                    PW.ThreadWitness.__init__(self, daemon=True)
                    self.bus = bus
                    self.stop_flag = self.event()
                    self.frames, self.errs = [], []
                    self.lock = threading.Lock()

                if fail:        # dies at once: run_case's sleeps are stubbed, so its stop may come first
                    def run(self):
                        raise OSError("the adapter went away (test)")
            return W

        def ib(bus, fail=False):
            IB.Witness = on(bus, fail)
            can = types.SimpleNamespace(Bus=lambda **k: TWC.FakeBus())
            kb = types.ModuleType("kvaser_bus")
            kb.pcan_bus = lambda **k: TWC.FakeBus()
            before = spy.calls
            rec = with_module("kvaser_bus", kb, lambda: IB.run_case(
                can, TWC.FakeLF, {"case": IB.CASES[0][0], "judge": IB.CASES[0][2]},
                {"frames": [], "modes": [], "end": 1000}, "stub", time.strftime("%Y%m%dT%H%M%S")))
            return rec, spy.calls - before
        rec, n = ib(RaisingBus(), fail=True)
        check("B20 run_case: a dead witness -> VOID naming it, nothing judged",
              (rec.get("verdict") or "").startswith("VOID: the witness thread failed") and n == 0
              and rec.get("judged") is None, (rec.get("verdict"), n))

        class Quiet(object):
            def recv(self, timeout=None):
                time.sleep(min(timeout or 0, 0.05))
                return None

            def shutdown(self):
                pass
        rec, n = ib(Quiet())
        check("B20 run_case: the witness whole -> no dead-witness VOID (the control)",
              "the witness thread" not in (rec.get("verdict") or ""), rec.get("verdict"))
    finally:
        gi_paths.remove_tree(ibw)


def main(argv):
    only = None
    if "--case" in argv:
        only = set(argv[argv.index("--case") + 1].split(","))
    for part, run_it, arg in ((void_cases, not only or "void" in only, ()),
                              (wire_cases, not only or bool(only - {"void"}), (only - {"void"} if only else None,))):
        if not run_it:
            continue
        try:
            part(*arg)
        except Exception as e:      # noqa: BLE001  a part that raises is its FAIL, and the rest still runs
            import traceback
            traceback.print_exc()
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


# (module, the case to run, name, old, new)
MUTANTS = [
    ("restart_error_frames", "B12", "B12's witness back in a thread",
     "    return PW.ProcThread(target=wit,", "    return PW.thread_twin(PW.ProcThread)(target=wit,"),
    ("proc_witness", "void", "a dead thread's error not kept",
     "            self.proc_info[\"error\"] = \"%s: %s\" % (type(e).__name__, e)\n", "            pass\n"),
    ("proc_witness", "void", "void_reason ignores a dead thread",
     "        if err:\n            return \"the witness thread failed: %s\" % err\n", ""),
    ("proc_witness", "void", "void_reason ignores a dead child",
     "    if err:\n        return \"the witness child failed: %s\" % err\n", ""),
    ("proc_witness", "void", "void_line gives no line",
     "    return reason, (\"witness VOID: %s\" % reason if reason else None)\n", "    return reason, None\n"),
    ("skip_method_bench", "void", "B4 run_arm does not mark a dead witness",
     "        rec_arm[\"witness_proc_void\"] = why\n", "        pass\n"),
    ("skip_method_bench", "void", "B4 judge_arm_v2 ignores witness_proc_void",
     "           \"expected_trip7\": want_trip}\n    if arm.get(\"witness_proc_void\"):",
     "           \"expected_trip7\": want_trip}\n    if False:"),
    ("skip_method_bench", "void", "B4 judge_arm ignores witness_proc_void",
     "    out = {\"expected_class\": want_cls, \"expected_trip7\": want_trip}\n    if arm.get(\"witness_proc_void\"):",
     "    out = {\"expected_class\": want_cls, \"expected_trip7\": want_trip}\n    if False:"),
    ("slcan_refusal_probe", "void", "B1 a VOID command passes",
     "        if witness_void and j[\"verdict\"] == \"PASS\":\n            j.update(verdict=\"VOID\", why=[witness_void])\n",
     ""),
    ("slcan_refusal_probe", "void", "B1 VOID overrides a FAIL",
     "        if witness_void and j[\"verdict\"] == \"PASS\":", "        if witness_void:"),
    ("slcan_refusal_probe", "void", "B1 summary does not count VOID",
     "            \"void\": sum(1 for r in results if r.get(\"verdict\") == \"VOID\"),\n", ""),
    ("interlock_bench", "void", "B20 run_case judges a dead witness",
     "    proc_void = PW.void_reason(wit, running=False)\n    if proc_void:",
     "    proc_void = PW.void_reason(wit, running=False)\n    if False:"),
    ("x2_jam_cases", "void", "x2_jam_cases clocked() does not check the witness",
     "        PW.require(self)\n", ""),
    ("x2_jam_cases", "void", "x2_jam_cases run_guarded lets WitnessFailed through as a pass",
     "        rec[\"verdict\"] = PW.void_verdict(e.reason)\n", "        pass\n"),
    ("suite_judges", "void", "suite B12 reads a dead witness's 0 error frames as PASS",
     "    if voids and not (bad or rec_bad):\n", "    if False:\n"),
    ("suite_judges", "void", "suite B1 ignores VOID commands",
     "    if (voids or s.get(\"void\")) and s[\"fail\"] == 0:\n", "    if False:\n"),
    ("suite_judges", "void", "suite B9 ignores the witness VOID line",
     "    t = text(F.one(r\"B9_wrong_bitrate_calibrate\\.txt\"))\n    pv = WITNESS_VOID_RE.search(t)\n",
     "    t = text(F.one(r\"B9_wrong_bitrate_calibrate\\.txt\"))\n    pv = None\n"),
    ("suite_judges", "void", "suite B10 ignores the witness VOID line",
     "    t = text(F.one(r\"B10_trip6_rearm_check\\.txt\"))\n    pv = WITNESS_VOID_RE.search(t)\n",
     "    t = text(F.one(r\"B10_trip6_rearm_check\\.txt\"))\n    pv = None\n"),
    ("suite_judges", "void", "suite jam judge misses a dead witness",
     "(witness clock |the witness (child|thread) )", "(witness clock )"),
]


def mutants():
    base = gi_paths.scratch("mutants-busywait-witnesses")
    killed = errors = 0
    for n, (mod, case, name, old, new) in enumerate(MUTANTS, 1):
        src_path = os.path.join(os.path.dirname(HERE) if mod == "suite_judges" else HERE, mod + ".py")
        src = open(src_path, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, mod + ".py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--case", case],
                           capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, BUSYWAIT_MUTANT="%s=%s" % (mod, path)))
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        # killed: a check FAILed and the test ran to its summary (a crash is not a kill; the dead
        # witnesses' own tracebacks on stderr, from the raising bus, are expected)
        dead_ = bool(fl) and bool(re.search(r"^FAIL, \d+ ok, \d+ FAIL$", r.stdout, re.M))
        killed += dead_
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead_ else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else (r.stderr.strip()[-200:] if r.stderr else "")))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main(sys.argv))
