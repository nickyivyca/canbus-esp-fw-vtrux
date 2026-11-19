#!/usr/bin/env python3
"""Test canmain_burst_check.py and burst_measures.py (condition 5, the gen-inhibit reviewer, 2026-11-18).

  measures  burst_measures on made-up times: each bound at its edge and past it (the last error
            frame after the close, the span over and under, no error frame, the window), the
            close (5 ms +/- 3 ms, late frames, the probe) and the breach line.
  check     canmain_burst_check.main() against fakes in sys.modules: can, kvaser_bus (link_down,
            innomaker_held, pcan_bus, fixes), lowid_flood_skip_bench (the WiCAN page), nonode_bench
            (x2_pins, x2_held). A fake wire stamps every frame on the PCAN's device clock (RAW: an
            uptime base; or COOKED: the host's epoch clock). canmain at 500 kbit puts its frames
            on the wire while open and ACKs the PCAN; at 250 kbit its close puts error frames on
            the wire from its open to its close (+ --stop-late in the fake: a gs_usb that does not
            stop). The control PASSES; each can-fail (--hold-extra-ms 20, --cut-half,
            --close-after-ms 30) FAILs by its verdict; a canmain that keeps ACKing, a late
            replay frame or a late stop FAILs; COOKED stamps are VOID; a WiCAN not in mode 0, an
            X2 not held or a Kvaser not down is REFUSED before anything opens.

    python3.13 -B test_canmain_burst_check.py [--mutants]
--mutants applies each edit in MUTANTS to copies of canmain_burst_check.py and burst_measures.py
in the worktree's scratch/ (CANMAIN_BURST_DIR) and requires a FAIL line; a crash is not a kill.
Gen-inhibit tester, 2026-11-18.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("CANMAIN_BURST_DIR") or HERE
sys.path.insert(0, HERE)
if SRC != HERE:
    sys.path.insert(0, SRC)       # the mutated burst_measures before the real one
import gi_paths  # noqa: E402

FAILS = []
OKS = [0]


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if ok:
        OKS[0] += 1
    else:
        FAILS.append(name)


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", os.path.join(SRC, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ the measures

def measures(BM):
    b = BM.burst_measure
    m = b(5.0, 10.005, [10.000, 10.002, 10.004])
    check("burst: 3 error frames over 4 ms, the last before the close -> no breach",
          m["breaches"] == [] and m["n"] == 3 and abs(m["span_ms"] - 4.0) < 1e-6 and abs(m["last_after_close_ms"] + 1.0) < 1e-6, m)
    check("burst: none -> a breach", b(5.0, 10.0, [])["breaches"] != [] and "no witness error frame" in b(5.0, 10.0, [])["breaches"][0])
    check("burst: the last 5 ms after the close -> no breach", b(1.0, 10.0, [10.0, 10.005])["breaches"] == [])
    check("burst: the last 5.1 ms after the close -> a breach", any("after the close" in x for x in b(1.0, 10.0, [10.0, 10.0051])["breaches"]))
    check("burst: span asked + 5 ms -> no breach", b(5.0, 20.0, [10.0, 10.010])["breaches"] == [])
    check("burst: span asked + 5.1 ms -> a breach", any("over" in x for x in b(5.0, 20.0, [10.0, 10.0101])["breaches"]))
    check("burst: 5 ms asked, span 3 ms -> no breach", b(5.0, 10.005, [10.0, 10.003])["breaches"] == [])
    check("burst: 5 ms asked, span 2.9 ms -> a breach (cut short)", any("under" in x for x in b(5.0, 10.005, [10.0, 10.0029])["breaches"]))
    check("burst: 20 ms asked, span 17.9 ms -> a breach", any("under" in x for x in b(20.0, 10.02, [10.0, 10.0179])["breaches"]))
    check("burst: 4.9 ms asked, a single frame -> no lower bound", b(4.9, 10.005, [10.004])["breaches"] == [])
    check("burst: 1 ms asked, a single frame -> no breach", b(1.0, 10.001, [10.0005])["breaches"] == [])
    check("window: to the next start when it comes before the close + 0.5 s",
          BM.burst_window([1.0, 1.2], [1.01, 1.21], 0) == (1.0, 1.2))
    check("window: to the close + 0.5 s when the next start is later",
          BM.burst_window([1.0, 3.0], [1.01, 3.01], 0) == (1.0, 1.51) and BM.burst_window([1.0, 3.0], [1.01, 3.01], 1) == (3.0, 3.51))
    check("in_window: half-open", BM.in_window([0.9, 1.0, 1.5, 1.51], (1.0, 1.51)) == [1.0, 1.5])
    c = BM.close_measure
    check("close: 5 ms -> no breach, probe not ACKed", c(10.0, 10.005, [], False)["breaches"] == []
          and c(10.0, 10.005, [], False)["probe"] == "not ACKed")
    check("close: 8 ms and 2 ms -> no breach", c(10.0, 10.008, [])["breaches"] == [] and c(10.0, 10.002, [])["breaches"] == [])
    check("close: 8.1 ms and 1.9 ms -> a breach", c(10.0, 10.0081, [])["breaches"] != [] and c(10.0, 10.0019, [])["breaches"] != [])
    check("close: a late frame -> a breach", any("canmain frames" in x for x in c(10.0, 10.005, [10.03])["breaches"]))
    check("close: the probe ACKed -> a breach", any("ACKed" in x for x in c(10.0, 10.005, [], True)["breaches"]))
    check("close: no probe -> 'not sent', no breach", c(10.0, 10.005, [], None)["probe"] == "not sent"
          and c(10.0, 10.005, [], None)["breaches"] == [])
    check("close: no last frame -> a breach", c(None, 10.005, [])["breaches"] != [])
    check("breach line: empty with no breach, the prefix and every breach otherwise",
          BM.breach_line([c(10.0, 10.005, [])]) == "" and BM.breach_line([c(None, 1.0, []), b(1.0, 1.0, [])]).startswith(
              "burst timing VOID: close: no last") and "no witness error frame" in BM.breach_line([b(1.0, 1.0, [])]))


# ------------------------------------------------------------------ judge(), on made-up messages

def judging(C):
    """canmain_burst_check.judge on set messages (perf, epoch, stamp, id, is_error, data): 200 replay
    frames 2 ms apart to 10.398 s, the close at 10.403 s, the probe at 10.428 s and its 5 error frames,
    the re-open 10.63-10.73 s, one 5 ms burst at 11.0 s. The PCAN's clock 5000 s behind perf."""
    EPOCH = 1.79e9

    def msg(wire, fid, err=False, data="", lat=0.0):
        return (wire + lat, EPOCH + wire + lat, wire - 5000.0, fid, err, data)

    def msgs(last_lat=0.0, extra=(), probe_errs=True):
        out = [msg(10.0 + k * 0.002, C.REPLAY_ID, data="%08x" % k, lat=last_lat if k == 199 else 0.0) for k in range(200)]
        if probe_errs:
            out += [msg(10.429 + k * 0.0004, 0x20, err=True) for k in range(5)]
        out += [msg(11.0002 + k * 0.0005, 0x4, err=True) for k in range(11)]
        out += list(extra)
        return sorted(out)
    times = {"last_send": 10.398, "close": 10.403, "probe": 10.428, "ack_open": 10.63, "ack_close": 10.73}
    starts = [(11.0, 5.0, 5.0, 11.0052)]
    r = C.judge(msgs(), times, "%08x" % 199, starts)
    check("judge: the set messages PASS (close 5 ms after the last frame, probe not ACKed, the burst in bounds)",
          r["verdict"] == "PASS" and abs(r["close"]["gap_ms"] - 5.0) < 0.01 and r["bursts"][0]["n"] == 11, r.get("verdict"))
    r = C.judge(msgs(last_lat=0.006), times, "%08x" % 199, starts)
    check("judge: the last frame received 6 ms late is placed by its stamp: still 5 ms, PASS",
          r["verdict"] == "PASS" and abs(r["close"]["gap_ms"] - 5.0) < 0.01, (r.get("verdict"), r.get("close")))
    r = C.judge(msgs(extra=[msg(10.410, C.REPLAY_ID, data="ffffffff")]), times, "%08x" % 199, starts)
    check("judge: a replay frame of another payload 7 ms after the close is not the last frame: PASS",
          r["verdict"] == "PASS", (r.get("verdict"), r.get("close")))
    r = C.judge(msgs(probe_errs=False), times, "%08x" % 199, starts)
    check("judge: no error frame after the probe -> ACKed -> FAIL", r["verdict"].startswith("FAIL")
          and "ACKed" in r["verdict"], r.get("verdict"))
    r = C.judge(msgs(), times, "%08x" % 199, [(11.0, 5.0, 5.0, 10.9902)])
    check("judge: the burst's errors 15 ms past its close -> FAIL", r["verdict"].startswith("FAIL")
          and "after the close" in r["verdict"], r.get("verdict"))


# ------------------------------------------------------------------ the check, on fakes

ECHO_CAP = 64      # the fake gs_usb stalls with this many echoes unread (canmain: 256; the replay here is short)


def real_kb(log):
    """The REAL kvaser_bus (by file, from beside this test): its drain_stop, its record into the log, and through
    its layer() the pinned canbench, whose open_bench(drain=True) reader (_start_drain) the fake held open starts
    on the fake gs_usb (can-tools 7a27cc9; the gen-inhibit reviewer, 2026-11-19)."""
    spec = importlib.util.spec_from_file_location("kvaser_bus_echo_cbc", os.path.join(HERE, "kvaser_bus.py"))
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)
    kb.record = lambda ev: log.append((ev.get("event"), ev))      # "before_send" and "echoes" events
    return kb


def fakes(log, stamps="raw", acks_after_close=False, late_frame=False, stop_late=0.0, wican_mode=0,
          x2_held=True, kvaser_up=False, drop_echoes=False, slow_recv=0.0, leftover=None, drop_one=False,
          no_snapshot=False):
    DEV0 = 5000.0
    EPOCH = time.time() - time.perf_counter()

    def stamp(t):
        return EPOCH + t if stamps == "epoch" else t - DEV0

    can = types.ModuleType("can")

    class CanError(Exception):
        pass

    class Message:
        def __init__(self, arbitration_id=0, is_extended_id=False, data=b"", is_error_frame=False, timestamp=0.0,
                     is_rx=True):
            self.arbitration_id, self.data = arbitration_id, bytes(data)
            self.is_error_frame, self.timestamp, self.is_rx = is_error_frame, timestamp, is_rx
    can.Message, can.CanError = Message, CanError
    wire = {"q": [], "open": None, "ghost": False, "lock": threading.Lock()}

    def put(t, fid, err=False, data=b""):
        with wire["lock"]:
            wire["q"].append(Message(arbitration_id=fid, data=data, is_error_frame=err, timestamp=stamp(t)))

    class Gs:
        def __init__(self, bitrate):
            self.bitrate, self.t_open = bitrate, time.perf_counter()
            wire["open"] = self
            log.append(("gs open", bitrate))
            self.t_close, self.th = None, None
            self.echoes, self.sent, self.shut = [], 0, False     # its echoes, queued until read (2026-11-19)
            self.elock = threading.Lock()     # its own: the drain does not contend with the PCAN's reads
            if bitrate == 250000:       # at the wrong rate: error frames on the wire from bus-on to the stop
                self.th = threading.Thread(target=self.err, daemon=True)
                self.th.start()

        def err(self):
            while self.t_close is None or time.perf_counter() <= self.t_close + stop_late:
                put(time.perf_counter(), 0x4, err=True)
                time.sleep(0.0005)

        def send(self, msg, timeout=None):
            if self.bitrate == 500000:
                with self.elock:
                    if len(self.echoes) >= ECHO_CAP:     # as canmain's gs_usb with its echoes unread
                        log.append(("gs stall", self.sent))
                        raise CanError("The message could not be sent")
                put(time.perf_counter(), msg.arbitration_id, data=msg.data)
                if self.sent == 0:
                    log.append(("gs first send", self.bitrate))
                if not (drop_echoes and self.sent % 10 == 9) and not (drop_one and self.sent == 7):
                    with self.elock:
                        self.echoes.append(Message(msg.arbitration_id, data=msg.data, is_rx=False))
                self.sent += 1

        def recv(self, timeout=None):
            # slow_recv: an empty read lasts that long whatever the timeout (a drain whose stop is slow)
            end = time.perf_counter() + max(timeout or 0.0, 0.001, slow_recv)
            while True:
                if self.shut:
                    raise CanError("recv after shutdown")
                with self.elock:
                    if self.echoes:
                        return self.echoes.pop(0)
                if time.perf_counter() >= end:
                    return None
                time.sleep(0.0002)

        def shutdown(self):
            t_c = time.perf_counter()
            d = getattr(self, "bench_drain", None)     # canbench's drain (open_bench drain=True)
            log.append(("gs shutdown", self.bitrate, bool(d is not None and d.get("running"))))
            self.shut = True
            wire["open"] = None
            if self.th is not None:
                self.t_close = t_c
                self.th.join(2)
            elif late_frame:            # on the wire 30 ms after the close, received then
                threading.Timer(0.030, lambda: put(time.perf_counter(), 0x6A8, data=b"\xff\xff\xff\xff")).start()
            if acks_after_close:
                wire["ghost"] = True

    class Pcan:
        def recv(self, timeout=None):
            if wire.pop("state", False):    # as python-can's pcan raised error passive on the bench (2026-11-19)
                raise CanError("Bus error: The CAN controller is error passive")
            end = time.perf_counter() + (timeout or 0.0)
            while True:
                with wire["lock"]:
                    if wire["q"]:
                        return wire["q"].pop(0)
                if time.perf_counter() >= end:
                    return None
                time.sleep(0.0003)

        def send(self, msg, timeout=None):
            log.append(("pcan send", msg.arbitration_id))
            if wire["open"] is None and not wire["ghost"]:
                t = time.perf_counter()
                for k in range(5):
                    put(t + k * 0.0004, 0x20, err=True)       # unACKed: ACK error frames
                wire["state"] = True                          # and the PCAN goes error passive

        def status_string(self):
            return "OK"

        def shutdown(self):
            log.append(("pcan shutdown",))

    KB = types.ModuleType("kvaser_bus")
    KB.fixes = lambda: {"kvaser": False, "gs_usb": True}
    kb = real_kb(log)
    KB.drain_stop = kb.drain_stop
    KB.drain_before_send = (lambda bus, what: None) if no_snapshot else kb.drain_before_send

    def link_down():
        log.append(("kvaser down",))
        return {"up": kvaser_up, "bitrate": 500000, "state": "STOPPED", "restart_ms": 100, "listen_only": False}
    KB.link_down = link_down

    def pcan_bus(bitrate=500000, **kw):
        log.append(("pcan open", bitrate))
        return Pcan()
    KB.pcan_bus = pcan_bus

    class Held:
        def __init__(self, role):
            self.role = role

        def __enter__(self):
            log.append(("held enter", self.role))
            return self

        def open(self, bitrate=500000, drain=False, **kw):
            g = Gs(bitrate)
            if drain:       # as open_bench(drain=True): the pinned canbench's reader on the bus
                if leftover:    # a frame the device hands up at the open (its previous session's)
                    g.echoes.append(Message(0x6A9, data=b"\x01", is_rx=(leftover == "rx")))
                g.vtrux_role = self.role
                kb.layer()._start_drain(g, self.role)
                g.vtrux_opened_at = time.perf_counter()     # as kvaser_bus._innomaker_open sets it
            log.append(("held open", bitrate, bool(drain)))
            return g

        def __exit__(self, *exc):
            log.append(("held exit", self.role))
            return False
    KB.innomaker_held = Held
    LF = types.ModuleType("lowid_flood_skip_bench")
    LF.dev_post = lambda path, timeout=8.0: log.append(("post", path)) or True
    LF.page = lambda: {"mode": wican_mode, "drv": {"state": "running" if wican_mode == 3 else "installing"}}
    NN = types.ModuleType("nonode_bench")
    NN.x2_pins = lambda logdir, tool=None: "pins: TX (GPIO7) %s -- x" % ("recessive" if x2_held else "driven")
    NN.x2_held = lambda line: "recessive" in line
    return {"can": can, "kvaser_bus": KB, "lowid_flood_skip_bench": LF, "nonode_bench": NN}


SHORT = ["--repeats", "1", "--gap-s", "0.05", "--replay-s", "0.3"]


def run(C, argv, **kw):
    log = []
    names = ("can", "kvaser_bus", "lowid_flood_skip_bench", "nonode_bench", "wrong_bitrate_errors")
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules.update(fakes(log, **kw))
    sys.modules.pop("wrong_bitrate_errors", None)
    runs = gi_paths.scratch("canmain-burst-check-runs")
    gi_paths.remove_tree(runs)
    os.makedirs(runs)
    C.RUNS = runs
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                rc = C.main(SHORT + argv)
            except Exception as e:      # noqa: BLE001  the check raising is a FAIL of "ran", not a crash here
                rc = "raised %s: %s" % (type(e).__name__, e)
        recs = [f for f in os.listdir(runs) if f.endswith(".json")]
        rec = json.load(open(os.path.join(runs, recs[0]), encoding="utf-8")) if len(recs) == 1 else {}
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        gi_paths.remove_tree(runs)
    return rc, rec, log, buf.getvalue()


def checks(C):
    rc, rec, log, out = run(C, [])
    check("control: PASS, exit 0", rc == 0 and rec.get("verdict") == "PASS", (rc, rec.get("verdict"), out[-400:]))
    check("control: the bench read back and recorded (WiCAN mode 0 and its driver stopped, the X2 held, the Kvaser down)",
          rec.get("bench", {}).get("wican") == {"mode": 0, "driver": "installing"} and rec["bench"]["x2"]["held"] is True
          and rec["bench"]["kvaser"].get("up") is False, rec.get("bench"))
    c = rec.get("close") or {}
    check("control: the close 5 ms (+/- 3) after the last frame, no late frame, the probe not ACKed",
          c.get("breaches") == [] and c.get("gap_ms") is not None and 2.0 <= c["gap_ms"] <= 8.0 and c.get("late") == 0
          and c.get("probe") == "not ACKed" and c.get("probe_error_frames", 0) >= 1, c)
    bs = rec.get("bursts") or []
    check("control: one measure per asked length (1, 5, 20 ms), each with error frames and no breach",
          [m["asked_ms"] for m in bs] == [1.0, 5.0, 20.0] and all(m["n"] > 0 and m["breaches"] == [] for m in bs), bs)
    check("control: the probe's error frames are not counted in the bursts",
          all(m["n"] < 200 for m in bs) and bs and bs[0]["n"] <= 6, [m["n"] for m in bs])
    check("control: the fit and the bounds recorded, the fit not VOID",
          (rec.get("fit") or {}).get("void") is None and rec.get("bounds", {}).get("close_tol_s") == 0.003, rec.get("fit"))
    ts = (rec.get("replay") or {}).get("times_s") or {}
    check("control: the probe sent from the close + 20 ms, the re-open after it",
          ts.get("probe") is not None and ts["probe"] >= 0.020 and ts["ack_open"] > ts["probe"], ts)
    order = [e[0] for e in log]
    check("control: the read-backs before the PCAN opens; canmain held after it; held left before the PCAN is shut",
          order.index("kvaser down") < order.index("pcan open") < order.index("held enter")
          < order.index("held exit") < order.index("pcan shutdown"), order)
    check("control: prints the fit line, the close line, a line per burst and the verdict",
          "pcan clock: n " in out and "\nclose: " in out and out.count("\nburst ") == 3 and "verdict: PASS" in out, out[-500:])
    rp = rec.get("replay") or {}
    ed = rp.get("echo_drain") or {}
    check("control: the replay is longer than the fake's echo cap and canmain never stalled (its echoes read)",
          (rp.get("frames") or 0) > ECHO_CAP and not any(e[0] == "gs stall" for e in log), (rp.get("frames"), log[-5:]))
    check("control: the echo drain's counts recorded with the replay (echoes == frames sent) and judged",
          ed.get("event") == "echoes" and ed.get("sent") == rp.get("frames") == ed.get("echoes")
          and ed.get("echoes_equal_sent") is True and (rec.get("echoes") or {}).get("breaches") == []
          and "\nechoes: %s for %s frames sent, drain stopped in " % (rp.get("frames"), rp.get("frames")) in out,
          (ed, rec.get("echoes")))
    check("control: the drain's stop time recorded and judged (canbench's stop_ms, within the 3 ms lead)",
          isinstance(ed.get("stop_ms"), (int, float)) and ed["stop_ms"] <= C.DRAIN_LEAD_S * 1000
          and (rec.get("echoes") or {}).get("stop_ms") == ed["stop_ms"], ed)
    opens = [e for e in log if e[0] == "held open"]
    check("control: the replay's canmain opened with canbench's drain (drain=True); the ACK re-open and the bursts "
          "without", opens and opens[0] == ("held open", 500000, True) and all(not e[2] for e in opens[1:]), opens)
    shuts = [e for e in log if e[0] == "gs shutdown"]
    k = [e[0] for e in log]
    check("control: the drain stopped (drain_stop, recorded once) before the replay's close, not by the close",
          shuts and shuts[0][1:] == (500000, False) and sum(1 for e in log if e[0] == "echoes") == 1
          and k.index("echoes") < k.index("gs shutdown"), (shuts, log[-8:]))
    bsend = [e[1] for e in log if e[0] == "before_send"]
    check("control: the drain's counts taken once, before the replay's first send and at least the settle after "
          "its open, nothing yet (kind none, no id), kept with the replay, in the measure and printed",
          len(bsend) == 1 and "gs first send" in k and k.index("before_send") < k.index("gs first send")
          and (bsend[0].get("echoes"), bsend[0].get("rx"), bsend[0].get("errors"), bsend[0].get("kind"),
               bsend[0].get("id")) == (0, 0, 0, [], None)
          and bsend[0]["since_open_ms"] >= bsend[0]["settle_s"] * 1000 - 0.01
          and ed.get("before_send") == bsend[0] and (rec.get("echoes") or {}).get("before_send") == bsend[0]
          and ed.get("drained", {}).get("echoes") == rp.get("frames")
          and "\nbefore the first send: 0 echoes, 0 rx, 0 error frames (kind none, id -), " in out, (bsend, ed))
    ws = rec.get("witness_states") or []
    check("control: the PCAN's error-passive state raised from recv after the probe is recorded, and the witness "
          "read on (the bursts above were witnessed)", len(ws) == 1 and "error passive" in ws[0][1] and ws[0][0] > 0.020,
          ws)
    rc, rec, log, out = run(C, [], drop_echoes=True)
    check("one frame in ten giving no echo: FAIL by its verdict naming the echo count, exit 1",
          rc == 1 and "echoes for" in str(rec.get("verdict")) and "verdict: FAIL" in out, (rc, rec.get("verdict")))
    rc, rec, log, out = run(C, [], slow_recv=0.02)
    check("a drain whose stop takes ~18 ms (each empty read 20 ms): FAIL by its verdict naming the stop past its "
          "lead, exit 1", rc == 1 and "echo drain's stop took" in str(rec.get("verdict")) and "verdict: FAIL" in out,
          (rc, rec.get("verdict")))

    # LEFTOVER FRAMES (the gen-inhibit reviewer's Q1 (i), 2026-11-19): a frame the device hands up at the open
    for kind in ("echo", "rx"):
        rc, rec, log, out = run(C, [], leftover=kind)
        ed = (rec.get("replay") or {}).get("echo_drain") or {}
        bs = ed.get("before_send") or {}
        n = (rec.get("replay") or {}).get("frames")
        check("a leftover %s at the open: PASS, exit 0; counted before the first send with its kind (the id not "
              "told: canbench's drain keeps counts), the echoes judged after it, the drain's whole count kept" % kind,
              rc == 0 and rec.get("verdict") == "PASS" and bs.get("kind") == [kind]
              and bs.get("echoes" if kind == "echo" else "rx") == 1 and "counts only" in str(bs.get("id"))
              and ed.get("echoes") == n
              and (ed.get("drained") or {}).get("echoes") == n + (1 if kind == "echo" else 0)
              and "(kind %s, id not told" % kind in out, (rc, rec.get("verdict"), ed))
    rc, rec, log, out = run(C, [], leftover="echo", drop_one=True)
    n = (rec.get("replay") or {}).get("frames")
    check("CAN-FAIL: a leftover echo and one frame giving no echo: FAIL by its verdict naming one echo short "
          "(the whole count, %s, would have matched), exit 1" % n,
          rc == 1 and n and "replay: %d echoes for %d frames sent" % (n - 1, n) in str(rec.get("verdict"))
          and ((rec.get("replay") or {}).get("echo_drain") or {}).get("drained", {}).get("echoes") == n,
          (rc, rec.get("verdict")))
    rc, rec, log, out = run(C, [], drop_one=True)
    check("one frame giving no echo, no leftover: FAIL naming one echo short, exit 1",
          rc == 1 and "echoes for" in str(rec.get("verdict")), (rc, rec.get("verdict")))
    rc, rec, log, out = run(C, [], no_snapshot=True)
    check("no before_send snapshot taken: FAIL by its verdict naming the missing snapshot, exit 1",
          rc == 1 and "no before_send snapshot" in str(rec.get("verdict")), (rc, rec.get("verdict")))

    for label, argv, kw, want in (
            ("--hold-extra-ms 20 (can-fail)", ["--hold-extra-ms", "20"], {}, "over"),
            ("--cut-half (can-fail)", ["--cut-half"], {}, "under"),
            ("--close-after-ms 30 (can-fail)", ["--close-after-ms", "30"], {}, "not 5 +/- 3 ms"),
            ("canmain still ACKing after its close", [], {"acks_after_close": True}, "probe after the close was ACKed"),
            ("a replay frame 30 ms after the close", [], {"late_frame": True}, "canmain frames from 20 ms"),
            ("gs_usb erring 30 ms past its close", [], {"stop_late": 0.030}, "after the close, over 5 ms")):
        rc, rec, log, out = run(C, argv, **kw)
        check("%s: FAIL by its verdict, naming the breach, exit 1" % label,
              rc == 1 and str(rec.get("verdict", "")).startswith("FAIL: ") and want in rec["verdict"]
              and "verdict: FAIL" in out, (rc, rec.get("verdict"), out[-300:]))

    rc, rec, log, out = run(C, [], stamps="epoch")
    check("COOKED stamps: VOID naming the epoch clock, nothing measured, exit 1",
          rc == 1 and str(rec.get("verdict", "")).startswith("VOID: PCAN clock") and "epoch" in rec["verdict"]
          and "close" not in rec and "bursts" not in rec, (rc, rec.get("verdict")))
    rc, rec, log, out = run(C, ["--hold-extra-ms", "20"], stamps="epoch")
    check("COOKED stamps with a can-fail: VOID, not FAIL", str(rec.get("verdict", "")).startswith("VOID"), rec.get("verdict"))

    for label, kw, want in (("the WiCAN in mode 3", {"wican_mode": 3}, "WiCAN"),
                            ("the X2 not held", {"x2_held": False}, "X2"),
                            ("the Kvaser link up", {"kvaser_up": True}, "Kvaser")):
        rc, rec, log, out = run(C, [], **kw)
        check("%s: REFUSED naming it, exit 2, nothing opened" % label,
              rc == 2 and str(rec.get("verdict", "")).startswith("REFUSED") and want in rec["verdict"]
              and not any(e[0] in ("pcan open", "held enter", "gs open") for e in log), (rc, rec.get("verdict"), log))


MUTANTS_ECHO = [
    # the echo drain (the gen-inhibit reviewer's ruling (C), 2026-11-19)
    ("canmain_burst_check.py", "the replay's canmain opened without canbench's drain",
     "    b = inno.open(bitrate=REPLAY_BITRATE, drain=True)\n", "    b = inno.open(bitrate=REPLAY_BITRATE)\n"),
    ("canmain_burst_check.py", "the replay's drain not stopped before the close (left to the close)",
     '        echo = KB.drain_stop(b, n, "canmain_burst_check replay")\n        time.sleep(max(0.0, target - time.perf_counter()))\n'
     '    finally:\n        if echo is None:\n            echo = KB.drain_stop(b, n, "canmain_burst_check replay")\n'
     "        t_close = time.perf_counter()\n        b.shutdown()\n",
     "        time.sleep(max(0.0, target - time.perf_counter()))\n    finally:\n        t_close = time.perf_counter()\n"
     '        b.shutdown()\n        echo = KB.drain_stop(b, n, "canmain_burst_check replay")\n'),
    ("canmain_burst_check.py", "the drain's stop time not judged",
     '    if echo.get("stop_ms") is not None and echo["stop_ms"] > DRAIN_LEAD_S * 1000:\n', "    if False:\n"),
    ("canmain_burst_check.py", "the drain's stop time not kept in the measure",
     '"echoes": echo.get("echoes"), "stop_ms": echo.get("stop_ms"),', '"echoes": echo.get("echoes"),'),
    ("canmain_burst_check.py", "the echo count not checked",
     '    elif echo.get("echoes") != echo.get("sent"):\n', "    elif False:\n"),
    ("canmain_burst_check.py", "the echo measure left out of the verdict",
     '([out["echoes"]] if "echoes" in out else [])', "[]"),
    # leftover frames at the open (the gen-inhibit reviewer's Q1 (i), 2026-11-19)
    ("canmain_burst_check.py", "no before_send snapshot before the replay's first send",
     '        KB.drain_before_send(b, "canmain_burst_check replay")', "        pass"),
    ("canmain_burst_check.py", "the snapshot taken after the replay's sends",
     '        KB.drain_before_send(b, "canmain_burst_check replay")     # what the open handed up, apart (LEFTOVER '
     'FRAMES)\n        t0 = time.perf_counter()\n',
     "        t0 = time.perf_counter()\n"
     '        threading.Timer(0.05, KB.drain_before_send, (b, "canmain_burst_check replay")).start()\n'),
    ("canmain_burst_check.py", "the snapshot not kept in the measure",
     '         "before_send": echo.get("before_send"), ', "         "),
    ("canmain_burst_check.py", "the snapshot not printed",
     '        print("before the first send: ', '        0 and print("before the first send: '),
    # the witness reads on past a bus state (2026-11-19)
    ("canmain_burst_check.py", "the witness stops at a bus state raised from recv",
     "                time.sleep(0.001)\n                continue\n            if m is None:",
     "                return\n            if m is None:"),
    ("canmain_burst_check.py", "the witness's bus states not recorded",
     '    rec["witness_states"] = [(round(t - t_c, 6), s) for t, s in wit.states]', '    rec["witness_states"] = []'),
]

MUTANTS = [
    (None, None, None, None),
    # burst_measures
    ("burst_measures.py", "no lower bound", "    if asked >= SPAN_UNDER_FROM_S - EPS and span < asked - SPAN_UNDER_S - EPS:\n",
     "    if False:\n"),
    ("burst_measures.py", "no upper bound on the span", "    if span > asked + SPAN_OVER_S + EPS:\n", "    if False:\n"),
    ("burst_measures.py", "the last error frame not bounded", "    if after > CLOSE_EDGE_S + EPS:\n", "    if False:\n"),
    ("burst_measures.py", "a burst with no error frame passes",
     '        m["breaches"].append("burst %g ms: no witness error frame" % asked_ms)\n', ""),
    ("burst_measures.py", "the close edge is 10 ms", "CLOSE_EDGE_S = 0.005 ", "CLOSE_EDGE_S = 0.010 "),
    ("burst_measures.py", "the span margin is 10 ms", "SPAN_OVER_S = 0.005 ", "SPAN_OVER_S = 0.010 "),
    ("burst_measures.py", "the lower bound from 1 ms", "SPAN_UNDER_FROM_S = 0.005 ", "SPAN_UNDER_FROM_S = 0.001 "),
    ("burst_measures.py", "the close tolerance is 4 ms", "CLOSE_TOL_S = 0.003 ", "CLOSE_TOL_S = 0.004 "),
    ("burst_measures.py", "the close gap not judged",
     "        if abs(gap - CLOSE_AFTER_S) > CLOSE_TOL_S + EPS:\n", "        if False:\n"),
    ("burst_measures.py", "late frames pass", "    if late:\n        m[\"late_first", "    if False:\n        m[\"late_first"),
    ("burst_measures.py", "an ACKed probe passes", "    if probe_acked:\n", "    if False:\n"),
    ("burst_measures.py", "no last frame passes", '        m["breaches"].append("close: no last canmain frame on the witness")\n',
     "        pass\n"),
    ("burst_measures.py", "the window runs to the next start", "    end = closes[k] + TAIL_S\n", "    end = closes[k] + 99.0\n"),
    ("burst_measures.py", "the window closed at its end", "    return [t for t in times if lo <= t < hi]", "    return [t for t in times if lo <= t <= hi]"),
    # the check
    ("canmain_burst_check.py", "a VOID fit is measured anyway",
     '    if fit["void"]:\n        out["verdict"] = WC.pcan_void_verdict(fit["void"])\n        return out\n', ""),
    ("canmain_burst_check.py", "a breach is a PASS",
     '    out["verdict"] = "FAIL: %s" % line[len(BM.BURST_VOID_PREFIX):] if line else "PASS"',
     '    out["verdict"] = "PASS"'),
    ("canmain_burst_check.py", "--cut-half ignored", "    hold = ms * (0.5 if a.cut_half else 1.0) + a.hold_extra_ms",
     "    hold = ms + a.hold_extra_ms"),
    ("canmain_burst_check.py", "the bursts measured against the held length", "        m = BM.burst_measure(ms, c,",
     "        m = BM.burst_measure(hold, c,"),
    ("canmain_burst_check.py", "the probe sent at once after the close",
     "    while time.perf_counter() < t_close + BM.SILENCE_EDGE_S + 0.005:\n", "    while False:\n"),
    ("canmain_burst_check.py", "the probe's errors counted as no ACK whatever",
     "    out[\"close\"] = BM.close_measure(last, t_close, late, probe_acked=not probe_errs)",
     "    out[\"close\"] = BM.close_measure(last, t_close, late, probe_acked=False)"),
    ("canmain_burst_check.py", "late frames looked for only to the probe",
     "            and t_close + BM.SILENCE_EDGE_S <= t < times[\"ack_open\"]]",
     "            and t_close + BM.SILENCE_EDGE_S <= t < times[\"probe\"]]"),
    ("canmain_burst_check.py", "receipt time, not the mapped stamp",
     "    mapped = [(WC.mapped(fit, ts), fid, err, data) for _p, _e, ts, fid, err, data in msgs]",
     "    mapped = [(p, fid, err, data) for p, _e, ts, fid, err, data in msgs]"),
    ("canmain_burst_check.py", "the WiCAN not read back", '    if st["wican"].get("mode") != 0 or st["wican"].get("driver") == "running":\n',
     "    if False:\n"),
    ("canmain_burst_check.py", "the X2 not read back", '    if not st["x2"]["held"]:\n', "    if False:\n"),
    ("canmain_burst_check.py", "the Kvaser not read back", '    if st["kvaser"].get("up") is not False:\n', "    if False:\n"),
    ("canmain_burst_check.py", "a refusal runs anyway", "    if refused:\n        rec[\"verdict\"]", "    if False:\n        rec[\"verdict\"]"),
    ("canmain_burst_check.py", "the close at 500 kbit is at 250", "REPLAY_BITRATE = 500000\n", "REPLAY_BITRATE = 250000\n"),
    ("canmain_burst_check.py", "the last frame of any payload",
     "if fid == REPLAY_ID and not err and data == last_payload\n", "if fid == REPLAY_ID and not err\n"),
    ("canmain_burst_check.py", "exit 0 on a FAIL", '    return 0 if rec["verdict"] == "PASS" else 1', "    return 0"),
    ("canmain_burst_check.py", "the close timed from the next period slot",
     "        target = last_send + a.close_after_ms / 1000.0\n",
     "        target = max(time.perf_counter(), t0 + n * period) + 0.005 + a.close_after_ms / 1000.0\n"),
]

MUTANTS += MUTANTS_ECHO


def mutants():
    base = gi_paths.scratch("mutants-canmain-burst-check")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
            for f in ("canmain_burst_check.py", "burst_measures.py")}
    killed = errors = 0
    control_ok = False
    try:
        for n, (target, name, old, new) in enumerate(MUTANTS):
            if old is not None and srcs[target].count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, srcs[target].count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            gi_paths.remove_tree(d)
            os.makedirs(d)
            for f, s in srcs.items():
                open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(
                    s.replace(old, new) if f == target else s)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], env=dict(os.environ, CANMAIN_BURST_DIR=d),
                               capture_output=True, text=True, timeout=600)
            fails = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
            if old is None:
                control_ok = r.returncode == 0 and not fails and not crashed
                print("%s control: the unmutated copies  (exit %d, %d FAIL)" % (
                    "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
                continue
            dead = bool(fails) and not crashed
            killed += dead
            print("%s m%d %s: %s  (exit %d, %d FAIL%s%s)" % (
                "KILLED  " if dead else "SURVIVED", n, target, name, r.returncode, len(fails),
                ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
            sys.stdout.flush()
    finally:
        gi_paths.remove_tree(base)
    total = len(MUTANTS) - 1
    print("\n%d of %d mutants killed, %d errors, control %s" % (killed, total, errors, "passing" if control_ok else "FAILING"))
    return 0 if killed == total and not errors and control_ok else 1


def main():
    if "--mutants" in sys.argv[1:]:
        return mutants()
    for part, mod in ((measures, "burst_measures"), (judging, "canmain_burst_check"), (checks, "canmain_burst_check")):
        try:
            part(load(mod))
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    print("\n%d ok, %d FAIL" % (OKS[0], len(FAILS)))
    print("PASS" if not FAILS else "FAIL")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
