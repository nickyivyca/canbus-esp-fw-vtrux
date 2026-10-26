#!/usr/bin/env python3
"""Spec 12.4 "a bus with no other node" on the bench: the WiCAN live, then every
other node silent, then back, then a key-on. Gen-inhibit tester, 2026-10-20; design
approved by the reviewer the same day.

THE RIG (bench-holder.md). On the WiCAN's segment (CAN1): the WiCAN, the Kvaser, the
PCAN and the X2's CAN1. The Innomaker is on the X2's CAN2 and is never opened.
  Kvaser  NORMAL, the replayer: it loops the interlock slice
          (lowid_flood_skip_bench.build_stimulus, key on). It ACKs whenever it is
          open (it has no silent mode), so it is the node that goes silent, by
          closing.
  PCAN    PASSIVE (listen-only), the witness for the whole run. It never ACKs.
  X2      held in ROM download mode, TX not driven, from before the first arm
          (x2_hold.py; checked with `x2_hold.py pins` at the start and the end).
--termination (required) records where the segment's terminators are, as read on
the bench by a person: closing the Kvaser must not change the wire as well.
THE BENCH LOCK is the operator's: this script does not take it. Hold it in
firmware/bench-holder.md for the whole run, message the other tester, and note the
window there as usual (reviewer, 2026-10-20).
THE REBOOT RULE (user, 2026-10-20) applies to this run too: wican_reboot.py --first
wican_man before the first arm (a FAIL refuses the run) and --want wican_man at the
end; both records are named in this run's record.

EACH ARM:
  1. mode 0; the replay starts; mode 3; wait for inhibit_live; 5 s live.
  2. The replay stops just after it sends a VCM 0x051 (the last VCM command), and
     the Kvaser closes ~5 ms later, so the device's answer to it normally
     completes on an ACKing bus. From then nothing can ACK.
  3. 3 s of silence. /gen_inhibit read at ~20 Hz throughout the arm.
  4. The Kvaser reopens and the replay resumes, key on throughout, for 3 s.
  5. A key-on: the slice's own 0x592 frames with IgnitionKeyState encoded 0 for
     0.5 s, then 1 (cantools against epri-pt-bus.dbc for the mapped bits; the
     unmapped bits as captured). The decoded key of every 0x592 sent is recorded.
  6. The replay stops while the WiCAN still ACKs; mode 0.
At least --arms (3) main arms. An arm whose answer to the last command did not
complete before the close (its copies retry) is the answer-stuck case: judged
for that and reported as such, and another arm is run for the main case.

JUDGED per arm (judge_arm, pure, tested by test_nonode_bench.py):
  void      the witness saw the device's frame repeat unacknowledged: at least 10
            identical copies (ID, DLC, payload) with consecutive gaps of at most 2
            frame times plus an error frame, in the silence; and nothing from any
            other node in the silence. Otherwise the arm is VOID.
  answer    the device's answer to the last VCM command: one copy before the close
            (ACKed), or the answer-stuck case.
  no051     no device 0x051 after its answer: after the last VCM frame on the wire,
            0x051 frames carry at most one payload, the answer (only the device can
            send 0x051 once the replay stops). Answer-stuck: no copy of the answer
            after the first latched read; a copy inside that read's bracket (the last
            unlatched request to the first latched response) is INCONCLUSIVE.
  latched   from the nodes' return to the key-on, the device sends no 0x051, two
            ways (reviewer, 2026-10-20): on the witness, by live_jam_arm's provenance
            rule (the first of two adjacent 0x051 with the same counter byte, B5;
            a VCM frame seen twice -- identical, within 0.3 ms -- excluded); and on
            the page, the inhibit tx count (tx_ok) does not rise from the first
            latched read to the last read before the key-on.
  latch     latched within 0.7 s of the last VCM command, on the host clock: the
            last VCM frame's send time on the Kvaser against the first latched
            read's response time (conservative at both ends). A bracket between the
            last unlatched read's request and the first latched read's response
            that straddles 0.7 s is INCONCLUSIVE, never a pass.
  back      with the nodes back and no key-on, latched on every read, and the
            device's diag pages resume on the witness (frames other than the stuck
            frame's copies).
  keyon     the key-on clears the latch.
RECORDED, not judged: the trip and when; TEC, REC and drv.state on every read;
bus-off; the PCAN's error and status reports in the silence and the attempt count
at which the copies start; the retry period; whether the device goes live again
after the key-on (7.1's design).

    py -3.14 -B tools/gen_inhibit/bench/nonode_bench.py \
        --termination "<where the CAN1 terminators are>" [--arms 3]
Writes nonode_bench_<stamp>.json in GI_RUNS_DIR (or runs/). Exit 0 only when every
main and answer-stuck arm passes every judged item (run_verdict: an item NOT AS
SPECIFIED in any of them is the run's verdict, an INCONCLUSIVE item makes the run
INCONCLUSIVE).

    py -3.14 -B tools/gen_inhibit/bench/nonode_bench.py --rejudge <nonode_bench_<stamp>.json>
judges a record's stored arms again with run_verdict and writes <stem>_rejudged.json
beside it, the record unchanged (for records from before 2026-10-25, when main arms that
disagreed on the trip were FLAKY).
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
X2_HOLD = os.path.join(HERE, "x2_hold.py")
DBC = gi_paths.vehicle_dbc("epri-pt-bus.dbc")
VCM_ID, KEY_ID = 0x051, 0x592
DEVICE_IDS = {0x051, 0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}   # vtrux-wican-diag.dbc and the inhibit
DIAG_IDS = DEVICE_IDS - {0x051}
BIT_US = 2.0                    # 500 kbit/s
LATCH_MAX_S = 0.7               # 12.4 row (corrected 2026-10-20, user)
COPIES_MIN = 10                 # reviewer, 2026-10-20
CLOSE_AFTER_S = 0.005
SILENCE_EDGE_S = 0.020          # the Kvaser's last frames may still be on the wire this long after the close
DUP_MAX_US = 300.0              # live_jam_arm.DUP_MAX_S: a VCM frame the witness saw twice


# ------------------------------------------------------------------ judging --

def frame_time_us(dlc):
    """A standard frame's nominal air time, without stuffing."""
    return (47 + 8 * dlc) * BIT_US


def max_retry_gap_us(dlc):
    """Two frame times plus a (passive) error frame and intermission: ~25 bits."""
    return 2 * frame_time_us(dlc) + 25 * BIT_US


def longest_retry_run(frames):
    """frames: [(t_us, id, dlc, hex)] in wire order. The longest run of identical
    frames whose consecutive gaps are retry-sized: (count, first index, frame key,
    median gap us)."""
    best = (0, -1, None, None)
    i = 0
    while i < len(frames):
        j = i
        key = frames[i][1:]
        gaps = []
        while (j + 1 < len(frames) and frames[j + 1][1:] == key
               and frames[j + 1][0] - frames[j][0] <= max_retry_gap_us(key[1])):
            gaps.append(frames[j + 1][0] - frames[j][0])
            j += 1
        n = j - i + 1
        if n > best[0]:
            best = (n, i, key, statistics.median(gaps) if gaps else None)
        i = j + 1
    return best


def device_051_by_provenance(seq):
    """seq: [(t_us, payload bytes)] of the 0x051 frames on the witness, in wire order.
    The device's answer carries the VCM's next counter, so the device's frames are the
    first of each adjacent pair with equal B5 (live_jam_arm.equal_b5_pairs); a pair
    with identical payloads within DUP_MAX_US is one VCM frame seen twice. Returns
    (device frame times, duplicates excluded)."""
    pairs = [(t0, t1, d0, d1) for (t0, d0), (t1, d1) in zip(seq, seq[1:])
             if len(d0) >= 6 and len(d1) >= 6 and d0[5] == d1[5]]
    dups = [p for p in pairs if p[2] == p[3] and p[1] - p[0] <= DUP_MAX_US]
    return [p[0] for p in pairs if p not in dups], len(dups)


def judge_arm(arm):
    """Verdicts for one arm record (see the module docstring). Times: host seconds
    (perf counter) except the witness's, which are its own microseconds."""
    v = {}
    sil = [f for f in arm["witness_silence"]]           # (t_us, id, dlc, hex), non-error frames in the silence
    others = sorted({"0x%03X" % f[1] for f in sil if f[1] not in DEVICE_IDS})
    run = longest_retry_run(sil)
    v["void"] = ("VOID: a frame from another node in the silence: %s" % ", ".join(others) if others else
                 "VOID: the longest run of identical retry-spaced copies is %d (< %d)" % (run[0], COPIES_MIN)
                 if run[0] < COPIES_MIN else
                 "PASS: %d identical copies of 0x%03X retry-spaced (median gap %.0f us)" % (run[0], run[2][0], run[3] or 0))
    ans = arm["answer_copies_before_close"]
    stuck = arm["answer_copies_total"] > 1
    v["answer"] = ("ANSWER-STUCK: %d copies of the answer" % arm["answer_copies_total"] if stuck else
                   "ACKED: one copy before the close" if ans == 1 else
                   "VOID: no answer to the last VCM command seen")
    payloads = arm["device_051_payloads_after_last_vcm"]
    t_last, reads = arm["t_last_vcm"], arm["reads"]
    first = next((r for r in reads if r.get("latched") and r["t_send"] > t_last), None)
    before = [r for r in reads if r["t_send"] > t_last and not r.get("latched") and (first is None or r["t_send"] < first["t_send"])]
    if stuck:
        times = arm["answer_copy_times"]
        if first is None:
            v["no051"] = "NOT AS SPECIFIED: the answer stuck, and no latched read to judge its copies against"
        else:
            lo = before[-1]["t_send"] if before else t_last
            hi = first["t_recv"]
            late = sum(1 for t in times if t > hi)
            inside = sum(1 for t in times if lo < t <= hi)
            v["no051"] = ("NOT AS SPECIFIED: %d copies of the stuck answer after the first latched read" % late if late else
                          "INCONCLUSIVE: %d copies of the stuck answer inside the latched read's bracket (%.3f s)"
                          % (inside, hi - lo) if inside else
                          "PASS: no copy of the stuck answer after the first latched read's bracket (%.3f s)" % (hi - lo))
    else:
        v["no051"] = ("PASS: one 0x051 payload after the last VCM frame, the answer" if len(payloads) <= 1 else
                      "NOT AS SPECIFIED: %d distinct device 0x051 payloads after the last VCM frame" % len(payloads))
    if first is None:
        v["latch"] = "NOT AS SPECIFIED: never latched after the last VCM command"
    else:
        hi = first["t_recv"] - t_last
        lo = (before[-1]["t_send"] - t_last) if before else 0.0
        v["latch"] = ("PASS: latched by %.3f s (bracket %.3f-%.3f s)" % (hi, lo, hi) if hi <= LATCH_MAX_S else
                      "NOT AS SPECIFIED: still unlatched at %.3f s (bracket %.3f-%.3f s)" % (lo, lo, hi) if lo >= LATCH_MAX_S else
                      "INCONCLUSIVE: the bracket %.3f-%.3f s straddles %.1f s" % (lo, hi, LATCH_MAX_S))
    back = [r for r in reads if arm["t_reopen"] <= r["t_send"] and r["t_recv"] <= arm["t_keyoff"]]
    unl = [r for r in back if r.get("page") and not r.get("latched")]
    resumed = arm["diag_after_reopen_not_stuck"]
    v["back"] = ("VOID: no read while the nodes were back" if not back else
                 "NOT AS SPECIFIED: %d of %d reads unlatched with the nodes back and no key-on" % (len(unl), len(back)) if unl else
                 "NOT AS SPECIFIED: no diag page on the witness after the nodes came back" if resumed < 1 else
                 "PASS: latched on all %d reads; %d diag frames after the return" % (len(back), resumed))
    after = [r for r in reads if r["t_send"] >= arm["t_keyon"] and r.get("page")]
    v["keyon"] = ("VOID: no read after the key-on" if not after else
                  "PASS: the latch cleared after the key-on" if any(not r.get("latched") for r in after) else
                  "NOT AS SPECIFIED: still latched %.1f s after the key-on" % (after[-1]["t_recv"] - arm["t_keyon"]))
    n_wire = len(arm["device_051_latched_phase"])
    v["latched_wire"] = ("PASS: no device 0x051 on the witness from the return to the key-on (%d VCM duplicates "
                         "excluded)" % arm.get("vcm_duplicates_latched_phase", 0) if n_wire == 0 else
                         "NOT AS SPECIFIED: %d device 0x051 on the witness from the return to the key-on" % n_wire)
    tx = [r.get("tx_ok") for r in reads if first is not None and r["t_send"] >= first["t_send"]
          and r["t_recv"] < arm["t_keyon"] and r.get("tx_ok") is not None]
    v["latched_tx"] = ("VOID: no tx_ok read between the latch and the key-on" if len(tx) < 2 else
                       "PASS: tx_ok %d on every read from the first latched one to the key-on" % tx[0]
                       if max(tx) == tx[0] else
                       "NOT AS SPECIFIED: tx_ok rose %d -> %d between the first latched read and the key-on"
                       % (tx[0], max(tx)))
    keys = arm.get("key_decoded", {})
    if keys.get("off_phase") != [0] or keys.get("on_phase") != [1]:
        v["keyon"] = "VOID: the key frames sent decode as %s" % keys
    return v


def arm_verdict(v):
    """main / answer-stuck / void, and whether every judged item passed."""
    if any(x.startswith("VOID") for x in v.values()):
        return "VOID", False
    judged = ["no051", "latch", "back", "keyon", "latched_wire", "latched_tx"]
    ok = all(v[k].startswith("PASS") for k in judged)
    return ("answer-stuck" if v["answer"].startswith("ANSWER-STUCK") else "main"), ok


def run_verdict(arms, want):
    """The run's verdict from its arms (kind, passed, verdicts), main and answer-stuck alike
    (2026-10-20): NOT AS SPECIFIED if any judged arm has such an item; VOID with fewer than
    `want` main arms; INCONCLUSIVE if any judged arm has such an item; PASS if every judged
    arm passed. Arms that disagree on a judged item resolve through that order. Which trip
    latched is recorded per arm, never compared: spec 12.4's row says "Recorded, not judged:
    which trip latched (2 or 6) and when" (reviewer, 2026-10-25; until then main arms that
    disagreed on the trip were FLAKY)."""
    judged = [x for x in arms if x["kind"] in ("main", "answer-stuck")]
    mains = [x for x in judged if x["kind"] == "main"]
    items = lambda word: ["arm %d %s" % (x.get("n", i + 1), k) for i, x in enumerate(judged)
                          for k, v in x["verdicts"].items() if v.startswith(word)]
    bad, unsure = items("NOT AS SPECIFIED"), items("INCONCLUSIVE")
    if bad:
        return "NOT AS SPECIFIED: %s" % ", ".join(bad)
    if len(mains) < want:
        return "VOID: %d main arms of %d" % (len(mains), want)
    if unsure:
        return "INCONCLUSIVE: %s" % ", ".join(unsure)
    if all(x["passed"] for x in judged):
        return "PASS"
    return "ERROR: an arm did not pass, with no item NOT AS SPECIFIED or INCONCLUSIVE"


# -------------------------------------------------------------------- the rig --

class KeyFrames:
    """0x592 with IgnitionKeyState set by cantools; the unmapped bits as captured."""

    def __init__(self):
        import cantools
        self.db = cantools.database.load_file(DBC, strict=False)
        self.msg = self.db.get_message_by_frame_id(KEY_ID)
        mask = 0
        for s in self.msg.signals:
            if s.byte_order != "little_endian":
                raise ValueError("0x592 %s is not little-endian; the bit mask below would be wrong" % s.name)
            mask |= ((1 << s.length) - 1) << s.start
        self.mask = mask    # the bits the DBC maps

    def with_key(self, data, key):
        sig = self.msg.decode(data, decode_choices=False, scaling=False)
        sig["IgnitionKeyState"] = key
        enc = int.from_bytes(self.msg.encode(sig, scaling=False, strict=False), "little")
        orig = int.from_bytes(data, "little")
        out = ((orig & ~self.mask) | (enc & self.mask)).to_bytes(len(data), "little")
        return out

    def key_of(self, data):
        return self.msg.decode(data, decode_choices=False).get("IgnitionKeyState")


class Player(threading.Thread):
    """Loops the interlock slice on the Kvaser. key: None (as captured), 0 or 1.
    stop_after_vcm(): stop right after the next VCM 0x051 and return its send time."""

    def __init__(self, bus, stim, kf):
        super().__init__(daemon=True)
        self.bus, self.stim, self.kf = bus, stim, kf
        self.key = None
        self.stop_flag = threading.Event()
        self.stop_at_vcm = threading.Event()
        self.last_vcm = None            # (perf after send, payload hex)
        self.sent = self.fail = 0
        self.keys_sent = []             # (perf, decoded key)
        self.vcm_payloads = set()

    def _send(self, arb, data):
        import can
        try:
            self.bus.send(can.Message(arbitration_id=arb, data=data, is_extended_id=False))
            self.sent += 1
            return True
        except Exception:
            self.fail += 1
            return False

    def run(self):
        t0 = time.perf_counter()
        base = 0.0
        span = self.stim[-1][0] if self.stim else 1.0
        while not self.stop_flag.is_set():
            for rel, arb, data in self.stim:
                if self.stop_flag.is_set():
                    return
                target = t0 + base + rel
                while True:
                    left = target - time.perf_counter()
                    if left <= 0:
                        break
                    time.sleep(min(left, 0.002))
                if arb == KEY_ID and self.key is not None:
                    data = self.kf.with_key(data, self.key)
                if arb == KEY_ID:
                    self.keys_sent.append((time.perf_counter(), self.kf.key_of(data)))
                ok = self._send(arb, data)
                if arb == VCM_ID and ok:
                    self.vcm_payloads.add(bytes(data).hex())
                    if self.stop_at_vcm.is_set():
                        self.last_vcm = (time.perf_counter(), bytes(data).hex())
                        self.stop_flag.set()
                        return
            base += span

    def stop_after_vcm(self, timeout=2.0):
        self.stop_at_vcm.set()
        self.join(timeout)
        return self.last_vcm


class Witness(threading.Thread):
    """The PCAN, passive. Every message with its own timestamp (us) and the host's."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.msgs = []      # (perf, hw_us, id, dlc, hex, is_error)
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                with self.lock:
                    self.msgs.append((time.perf_counter(), m.timestamp * 1e6, m.arbitration_id, m.dlc,
                                      bytes(m.data).hex(), bool(m.is_error_frame)))
                m = self.bus.recv(timeout=0.0)

    def mark(self):
        with self.lock:
            return len(self.msgs)

    def since(self, i):
        with self.lock:
            return list(self.msgs[i:])


def clock_offset(msgs):
    """host perf (s) minus witness time (s), by the median over received messages,
    and its spread: maps witness times onto the host clock (receipt lags the wire)."""
    d = [p - hw / 1e6 for p, hw, *_ in msgs]
    if not d:
        return None, None
    d.sort()
    return statistics.median(d), d[len(d) * 9 // 10] - d[len(d) // 10]


def read_page(LF, t0):
    t_send = time.perf_counter()
    p = LF.page()
    t_recv = time.perf_counter()
    r = {"t_send": t_send, "t_recv": t_recv, "page": p is not None}
    if p is not None:
        drv = p.get("drv") or {}
        sch = p.get("sched") or {}
        r.update(mode=p.get("mode"), live=p.get("inhibit_live"), latched=p.get("abort_latched"), tx_ok=p.get("tx_ok"),
                 reason=p.get("abort_reason"), key_on=p.get("key_on"), err_n=p.get("err_n"),
                 state=drv.get("state"), tec=drv.get("tec"), rec=drv.get("rec"), bus_error=drv.get("bus_error"),
                 msgs_to_tx=drv.get("msgs_to_tx"), standdown=sch.get("standdown"), skipped=sch.get("skipped"),
                 uptime_ms=p.get("uptime_ms"))
    return r


class Poller(threading.Thread):
    def __init__(self, LF, hz):
        super().__init__(daemon=True)
        self.LF, self.period = LF, 1.0 / hz
        self.reads = []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            t = time.perf_counter()
            self.reads.append(read_page(self.LF, 0))
            time.sleep(max(0.0, self.period - (time.perf_counter() - t)))


def x2_pins(logdir, tool=None):
    """`x2_hold.py pins` (the tool beside this one): whether the X2's CAN1 TX is driven. The tool's verdict line."""
    p = subprocess.run([sys.executable, "-B", tool or X2_HOLD, "pins"], capture_output=True,
                       text=True, timeout=120, cwd=os.getcwd())
    line = next((l for l in p.stdout.splitlines() if l.startswith("pins:")), "no pins line (rc %s)" % p.returncode)
    return line


def x2_held(line):
    """Whether a `x2_hold.py pins` line shows CAN1 TX not driven: the verdict is "recessive" or "not driven, level
    unreadable" (the ROM leaves the input disabled). "dominant" (not driven but reads 0), "driven", "unknown" or
    no pins line is not held."""
    m = re.match(r"pins: TX \(GPIO\d+\) (.*?) -- ", line)
    return bool(m) and m.group(1) in ("recessive", "not driven, level unreadable")


def start_refusal(rec):
    """Why the run must not start, as (verdict, message), or None: the start reboot did not confirm, or the X2
    is not shown held."""
    if rec["reboot_start"]["rc"] != 0:
        return ("start reboot not confirmed", "the start reboot did not confirm: %s" % rec["reboot_start"]["line"])
    if not x2_held(rec["x2_pins_start"]):
        return ("X2 not held", "the X2 is not shown held (x2_hold.py pins: %s); hold it before the run"
                % rec["x2_pins_start"])
    return None


def reboot(step, a, stamp):
    """wican_reboot.py, --first wican_man (start) or --want wican_man (end): its verdict line and record."""
    out = os.path.join(RUNS, "nonode_bench_%s_reboot_%s.json" % (stamp, step))
    want = ["--first", "wican_man"] if step == "start" else ["--want", "wican_man"]
    p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "wican_reboot.py"), "--label", "nonode %s" % step,
                        "--out", out, "--known", "wican_man=%s" % a.man_sha] + want, capture_output=True, text=True,
                       timeout=300)
    return {"rc": p.returncode, "line": (p.stdout.strip().splitlines() or [""])[-1], "record": os.path.basename(out)}


def run_arm(n, kv_open, pc_wit, LF, stim, kf, a):
    arm = {"n": n}
    poll = Poller(LF, a.poll_hz)
    t_start = time.perf_counter()
    # THE SLICE PLAYS ONLY WHILE THE WICAN IS ARMED (trip6_rearm_check.py, 2026-10-10): with
    # the X2 held and the PCAN passive, nothing but the WiCAN's running driver ACKs the
    # Kvaser, and unacknowledged replay frames are error frames the device would count the
    # moment it arms. So: arm on a quiet bus, start the slice once the driver runs; at the
    # end, stop the slice while the WiCAN still ACKs, then disarm.
    LF.dev_post("/gen_inhibit_set?mode=0")
    time.sleep(0.5)
    kv = kv_open()
    poll.start()
    LF.dev_post("/gen_inhibit_set?mode=3")
    t_arm = time.perf_counter()
    while time.perf_counter() - t_arm < 5 and not (poll.reads and poll.reads[-1].get("state") == "running"):
        time.sleep(0.02)
    arm["driver_running_after_arm_s"] = round(time.perf_counter() - t_arm, 3)
    pl = Player(kv, stim, kf)
    pl.start()
    while time.perf_counter() - t_arm < 15 and not (poll.reads and poll.reads[-1].get("live")):
        time.sleep(0.05)
    arm["live_after_arm_s"] = (round(time.perf_counter() - t_arm, 3)
                               if poll.reads and poll.reads[-1].get("live") else None)
    time.sleep(a.live_s)
    w0 = pc_wit.mark()
    last = pl.stop_after_vcm()
    time.sleep(CLOSE_AFTER_S)
    kv.shutdown()
    t_close = time.perf_counter()
    arm["vcm_payloads"] = sorted(pl.vcm_payloads)
    time.sleep(a.silence_s)
    t_reopen = time.perf_counter()
    kv = kv_open()
    pl2 = Player(kv, stim, kf)
    pl2.start()
    time.sleep(a.back_s)
    t_keyoff = time.perf_counter()
    pl2.key = 0
    time.sleep(a.keyoff_s)
    t_keyon = time.perf_counter()
    pl2.key = 1
    time.sleep(a.keyon_s)
    pl2.stop_flag.set()
    pl2.join(3)
    time.sleep(0.2)
    LF.dev_post("/gen_inhibit_set?mode=0")
    time.sleep(0.3)
    poll.stop.set()
    poll.join(10)
    kv.shutdown()
    msgs = pc_wit.since(w0)

    off, spread = clock_offset([m for m in msgs if not m[5]])
    arm.update(t_start=t_start, t_arm=t_arm, t_last_vcm=last[0] if last else None, t_close=t_close, t_reopen=t_reopen,
               t_keyoff=t_keyoff, t_keyon=t_keyon, last_vcm_payload=last[1] if last else None,
               clock={"host": "time.perf_counter (s)", "witness": "PCAN message timestamps (us)",
                      "offset_host_minus_witness_s": off, "offset_spread_p10_p90_s": spread},
               reads=poll.reads)

    def host(m):
        return m[1] / 1e6 + off if off is not None else m[0]

    data = [m for m in msgs if not m[5]]
    errs = [m for m in msgs if m[5]]
    # the last VCM frame on the wire: the last 0x051 with the payload the Player last sent
    i_last = max((i for i, m in enumerate(data) if m[2] == VCM_ID and m[4] == arm["last_vcm_payload"]
                  and host(m) <= t_close + SILENCE_EDGE_S), default=None)
    after = data[i_last + 1:] if i_last is not None else []
    dev051 = [m for m in after if m[2] == VCM_ID and host(m) < t_reopen]
    answer = dev051[0][4] if dev051 else None
    arm.update(
        answer_payload=answer,
        answer_copies_total=sum(1 for m in dev051 if m[4] == answer),
        answer_copies_before_close=sum(1 for m in dev051 if m[4] == answer and host(m) <= t_close),
        answer_copy_times=[host(m) for m in dev051 if m[4] == answer],
        device_051_payloads_after_last_vcm=sorted({m[4] for m in dev051}),
        witness_silence=[(m[1], m[2], m[3], m[4]) for m in data
                         if t_close + SILENCE_EDGE_S <= host(m) <= t_reopen],
        witness_error_reports_silence=[(round(host(m) - t_close, 6), m[2], m[4]) for m in errs
                                       if t_close <= host(m) <= t_reopen][:2000],
        witness_frames_total=len(data), witness_error_reports_total=len(errs))
    # from the nodes' return to the key-on: the device's 0x051 by provenance (live_jam_arm):
    # the first of two adjacent 0x051 with equal B5; a VCM frame seen twice (identical,
    # within DUP_MAX_US on the witness clock) is a duplicate, not the device
    seq = [(m[1], bytes.fromhex(m[4])) for m in data if m[2] == VCM_ID and t_reopen <= host(m) < t_keyon]
    dev, ndup = device_051_by_provenance(seq)
    arm["device_051_latched_phase"] = [round((t - seq[0][0]) / 1e6, 6) for t in dev]
    arm["vcm_duplicates_latched_phase"] = ndup
    run = longest_retry_run(arm["witness_silence"])
    stuck_key = run[2]
    arm["retry_run"] = {"copies": run[0], "frame": ("0x%03X" % stuck_key[0], stuck_key[1], stuck_key[2]) if stuck_key else None,
                        "median_gap_us": run[3],
                        "error_reports_before_first_copy": (
                            sum(1 for m in errs if t_close <= host(m) and run[1] >= 0
                                and m[1] < arm["witness_silence"][run[1]][0]) if run[1] >= 0 else None)}
    arm["diag_after_reopen_not_stuck"] = sum(1 for m in data if host(m) >= t_reopen and m[2] in DIAG_IDS
                                             and (stuck_key is None or (m[2], m[3], m[4]) != stuck_key))
    ks = pl2.keys_sent
    arm["key_decoded"] = {"off_phase": sorted({k for t, k in ks if t_keyoff + 0.05 <= t < t_keyon}),
                          "on_phase": sorted({k for t, k in ks if t >= t_keyon + 0.05}),
                          "back_phase": sorted({k for t, k in ks if t < t_keyoff})}
    arm["live_again_after_keyon"] = any(r.get("live") for r in poll.reads if r["t_send"] >= t_keyon)
    lr = [r for r in poll.reads if r.get("page")]
    arm["recorded"] = {
        "trip": next((r.get("reason") for r in lr if r.get("latched")), None),
        "tec": [r.get("tec") for r in lr if t_close <= r["t_send"] <= t_reopen],
        "rec": [r.get("rec") for r in lr if t_close <= r["t_send"] <= t_reopen],
        "states": sorted({str(r.get("state")) for r in lr}),
        "bus_off_seen": any(str(r.get("state")).lower().replace("-", "_") == "bus_off" for r in lr),
        "bus_error_max": max([r.get("bus_error") or 0 for r in lr], default=None),
        "err_n_max": max([r.get("err_n") or 0 for r in lr], default=None)}
    for r in poll.reads:          # times relative to the arm start, for reading
        r["t_send_rel"] = round(r["t_send"] - t_start, 4)
    return arm


def rejudge(path):
    """--rejudge <nonode_bench_<stamp>.json>: run_verdict again over the record's stored arms,
    with the run's own --arms, and write <stem>_rejudged.json beside it. The record itself is
    not changed, and the arms' own verdicts (judge_arm) are taken as stored. For a record made
    before the trip stopped being compared (reviewer, 2026-10-25)."""
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)
    want = (rec.get("args") or {}).get("arms", 3)
    out = {"rejudged_from": os.path.basename(path), "rejudged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "rule": "run_verdict without the trip comparison (spec 12.4: the trip is recorded, not judged)",
           "want_main_arms": want, "verdict_before": rec.get("verdict"),
           "verdict": run_verdict(rec["arms"], want),
           "arms": [{"n": x.get("n"), "kind": x["kind"], "passed": x["passed"],
                     "trip": (x.get("recorded") or {}).get("trip"), "latch": x["verdicts"].get("latch")}
                    for x in rec["arms"]]}
    dst = os.path.splitext(path)[0] + "_rejudged.json"
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("verdict: %s (was %s); wrote %s" % (out["verdict"], out["verdict_before"], dst))
    return 0 if out["verdict"] == "PASS" else 1


def main():
    if sys.argv[1:2] == ["--rejudge"]:
        return rejudge(sys.argv[2])
    ap = argparse.ArgumentParser()
    ap.add_argument("--termination", required=True, help="where the CAN1 terminators are, as read on the bench")
    ap.add_argument("--man-sha", required=True, help="wican_man's elf_sha256 (64 hex), for wican_reboot.py")
    ap.add_argument("--arms", type=int, default=3)
    ap.add_argument("--max-arms", type=int, default=6)
    ap.add_argument("--live-s", type=float, default=5.0)
    ap.add_argument("--silence-s", type=float, default=3.0)
    ap.add_argument("--back-s", type=float, default=3.0)
    ap.add_argument("--keyoff-s", type=float, default=0.5)
    ap.add_argument("--keyon-s", type=float, default=2.5)
    ap.add_argument("--poll-hz", type=float, default=20.0)
    a = ap.parse_args()
    import can
    import lowid_flood_skip_bench as LF
    stamp = time.strftime("%Y%m%dT%H%M%S")
    os.makedirs(RUNS, exist_ok=True)
    kf = KeyFrames()
    stim = LF.build_stimulus()
    rec = {"when": stamp, "args": vars(a), "termination": a.termination,
           "nodes": {"WiCAN": "on CAN1 (the device)", "Kvaser": "CAN1, NORMAL, the replayer; closed in the silence",
                     "PCAN": "CAN1, PASSIVE (listen-only), the witness", "X2": "CAN1, held in ROM (see x2_pins)",
                     "Innomaker": "on the X2's CAN2, not CAN1 (bench-holder.md); never opened"},
           "reboot_start": reboot("start", a, stamp), "x2_pins_start": x2_pins(RUNS),
           "page_start": read_page(LF, 0), "arms": []}
    refused = start_refusal(rec)
    if refused:
        print("REFUSED: %s" % refused[1])
        rec["verdict"] = "REFUSED: %s" % refused[0]
        with open(os.path.join(RUNS, "nonode_bench_%s.json" % stamp), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1, default=str)
        return 2
    pc = None
    try:
        pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000, state=can.BusState.PASSIVE)
        wit = Witness(pc)
        wit.start()

        def kv_open():
            return can.Bus(interface="kvaser", channel=0, bitrate=500000)
        main_done = 0
        for n in range(1, a.max_arms + 1):
            arm = run_arm(n, kv_open, wit, LF, stim, kf, a)
            arm["verdicts"] = judge_arm(arm)
            kind, ok = arm_verdict(arm["verdicts"])
            arm["kind"], arm["passed"] = kind, ok
            rec["arms"].append(arm)
            print("arm %d: %s %s  %s" % (n, kind, "PASS" if ok else "--", arm["verdicts"]))
            sys.stdout.flush()
            main_done += kind == "main"
            if main_done >= a.arms:
                break
            time.sleep(2.0)
        wit.stop.set()
        wit.join(3)
    finally:
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        if pc is not None:
            try:
                rec["pcan_status_end"] = pc.status_string()
            except Exception:
                pass
            pc.shutdown()
    rec["x2_pins_end"] = x2_pins(RUNS)
    rec["reboot_end"] = reboot("release", a, stamp)
    if rec["reboot_end"]["rc"] != 0:
        rec["release_not_confirmed"] = rec["reboot_end"]["line"]
    rec["page_end"] = read_page(LF, 0)
    rec["verdict"] = run_verdict(rec["arms"], a.arms)
    path = os.path.join(RUNS, "nonode_bench_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, default=str)
    print("verdict: %s; wrote %s" % (rec["verdict"], path))
    return 0 if rec["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
