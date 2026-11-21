#!/usr/bin/env python3
"""Test wrong_bitrate_errors.py's arming order and its pre-burst witness window (reviewer,
2026-10-25).

Every B9 record from 2026-10-10 to 2026-10-25 had latched before its first burst and was
never live. The tool started the PCAN's slice 2 s before arming, with nothing on the
segment to ACK it. trip6_rearm_check.py's 2026-10-10 note names that as an error source.
Now:
  order    the WiCAN is armed (POST mode 3) before the slice starts; the slice starts only
           after a page shows the driver running (or 3 s, recorded as not running); at the
           end the slice stops before the disarm (POST mode 0)
  window   pre_burst_window counts the witness's error frames from the last live page
           sample without an abort to the first burst, with times: none in the gap -> 0;
           frames in the gap -> counted, timed; frames before that sample or from the burst on
           are not; a latched or not-live last sample is passed over for the clean one before;
           no clean sample -> None
  record   main() writes pre_burst_window, witness_errors_before_first_burst and
           driver_running_before_slice
  open     (2026-10-29, step 1a's acceptance) a burst starts when the source's channel opens,
           not when the open returns: the fake source puts an error frame on the witness as it
           opens, which must be the first burst's and not before it; each burst records the
           open's time, open_s
  canmain  (condition 5, 2026-11-18) the source is canmain at 250 kbit, opened on the role held
           once for the run (kvaser_bus.innomaker_held, entered before the PCAN opens and left
           after it is shut), with the Kvaser brought down and read back first; each burst's
           measures (burst_measures) recorded; a source that keeps erring 30 ms after its close
           made the run VOID with the "burst timing VOID" line until 2026-11-20, when the gen-inhibit
           reviewer ruled a run's burst lengths and closes recorded, not judged (item 4(ii)): only a
           burst with no witness error frame VOIDs (burst_measures.run_void_line); each burst's
           source state, read during its hold, is recorded and printed

main() runs against fakes put in sys.modules: can (PCAN, Kvaser), lowid_flood_skip_bench
(the device: a log of POSTs and page reads) and skip_method_bench (the Player). About 10 s.

    py -3.14 -B gen-inhibit/tools/bench/test_wrong_bitrate_sequence.py
Mutants: test_wrong_bitrate_sequence_mutants.py (WRONG_BITRATE_DIR runs another copy).
Gen-inhibit tester, 2026-10-25.
"""
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("WRONG_BITRATE_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths      # noqa: E402

SCRATCH = gi_paths.scratch("wrong-bitrate-sequence")
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def load():
    spec = importlib.util.spec_from_file_location("wbe_under_test", os.path.join(SRC, "wrong_bitrate_errors.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def window_cases(W):
    s = lambda t, live=True, abort="": {"t": t, "live": live, "abort": abort}
    series = [s(9.0), s(9.2), s(9.4), s(9.6), s(9.8)]
    for label, ser, errs, want in [
            ("no error frame in the gap", series, [8.5, 10.05], {"from_s": -0.2, "errors": 0, "error_times_s": []}),
            ("two in the gap, timed", series, [9.85, 9.9], {"from_s": -0.2, "errors": 2, "error_times_s": [-0.15, -0.1]}),
            ("one at the clean sample counts, one at the burst does not", series, [9.8, 10.0],
             {"from_s": -0.2, "errors": 1, "error_times_s": [-0.2]}),
            ("frames before the clean sample are not counted", series, [9.0, 9.5, 9.7],
             {"from_s": -0.2, "errors": 0, "error_times_s": []}),
            ("a latched last sample is passed over", series[:-1] + [s(9.8, live=False, abort="x")], [9.7],
             {"from_s": -0.4, "errors": 1, "error_times_s": [-0.3]}),
            ("a not-live last sample is passed over", series[:-1] + [s(9.8, live=False)], [9.7],
             {"from_s": -0.4, "errors": 1, "error_times_s": [-0.3]}),
            ("samples after the burst are not the clean sample", series + [s(10.2)], [9.9],
             {"from_s": -0.2, "errors": 1, "error_times_s": [-0.1]}),
            ("no clean sample before the burst", [s(9.8, live=False)], [9.9],
             {"from_s": None, "errors": None, "error_times_s": []})]:
        got = W.pre_burst_window(ser, errs, 10.0)
        check("pre_burst_window: %s" % label, got == want, got)


class Log:
    def __init__(self):
        self.ev, self.lock, self.t0 = [], threading.Lock(), time.perf_counter()

    def add(self, *e):
        with self.lock:
            self.ev.append(e)

    def index(self, pred):
        return next((i for i, e in enumerate(self.ev) if pred(e)), None)


def fakes(log, running_after, stamps="device", late=False, silent=False):
    """running_after: page reads after the mode 3 POST before the driver reports running
    (None: never). late: the burst source's close leaves an error frame 30 ms after it.
    silent: the burst source's open draws no error frame on the witness (2026-11-20)."""
    can = types.ModuleType("can")

    class CanError(Exception):
        pass

    # The PCAN's stamp, taken when a frame is on the wire (2026-11-11, A2 (a)): its device clock
    # in RAW mode (perf_counter less DEV0: an uptime base, not the epoch), or with
    # stamps="epoch" the host's epoch clock, as libpcanbasic gives in COOKED mode.
    DEV0 = 5000.0

    def stamp():
        return time.time() if stamps == "epoch" else time.perf_counter() - DEV0

    class Message:
        def __init__(self, arbitration_id=0, is_extended_id=False, data=b""):
            self.arbitration_id, self.data, self.is_error_frame = arbitration_id, data, False
            self.timestamp = stamp()

    wire = []       # error frames the witness (the PCAN) has yet to read
    last_data = [0.0]

    class Bus:
        def __init__(self, interface=None, channel=None, bitrate=None):
            self.interface = interface
            log.add("bus open", interface)
            if interface == "pcan":     # kvaser_bus.pcan_bus's record of bus states read past (F6, 2026-11-19)
                self.vtrux_states = [(12.5, "PcanCanOperationError: Bus error: The CAN controller is error passive")]
            if interface == "gs_usb":
                # bus-on at the wrong rate: the witness sees an error frame while the open
                # is still returning (step 1a's acceptance, 2026-10-29)
                if not silent:
                    e = Message()
                    e.is_error_frame = True
                    wire.append(e)
                time.sleep(0.02)

        def recv(self, timeout=None):
            end = time.perf_counter() + (timeout or 0.01)
            while time.perf_counter() < end:
                if self.interface == "pcan" and wire:
                    return wire.pop(0)
                # while the slice plays, the device's frames reach the PCAN every 5 ms: the
                # messages its clock is fitted on (A2 (a))
                if self.interface == "pcan" and state["slice"] and time.perf_counter() - last_data[0] >= 0.005:
                    last_data[0] = time.perf_counter()
                    return Message(arbitration_id=0x051, data=bytes(8))
                time.sleep(0.0005)
            return None

        def send(self, msg, timeout=None):
            pass

        def status_string(self):
            return "OK"

        def shutdown(self):
            log.add("bus shutdown", self.interface)
            if self.interface == "gs_usb" and late:
                e = Message()
                e.is_error_frame = True
                e.timestamp += 0.030
                wire.append(e)

    can.Bus, can.Message, can.CanError = Bus, Message, CanError

    state = {"mode": 0, "reads_since_arm": 0, "slice": False}
    LF = types.ModuleType("lowid_flood_skip_bench")

    def dev_post(path, timeout=8.0):
        log.add("post", path)
        state["mode"] = 3 if "mode=3" in path else 0
        state["reads_since_arm"] = 0
        return True

    def page():
        if state["mode"] == 3:
            state["reads_since_arm"] += 1
        run = (state["mode"] == 3 and running_after is not None and state["reads_since_arm"] > running_after)
        live = run and state["slice"]
        log.add("page", "running" if run else "not running")
        return {"mode": state["mode"], "inhibit_live": live, "abort_reason": "",
                "drv": {"state": "running" if run else "installing", "tec": 0, "rec": 0, "bus_error": 0}}

    LF.dev_post, LF.page = dev_post, page
    LF.build_stimulus = lambda: [(0.0, 0x051, b"\x00" * 8)]
    LF.build_id = lambda: {"elf_sha256": "c0" * 32}

    S = types.ModuleType("skip_method_bench")

    class Player:
        def __init__(self, bus, stim):
            self.stop_flag = threading.Event()

        def start(self):
            state["slice"] = True
            log.add("slice start")

        def join(self, timeout=None):
            state["slice"] = False
            log.add("slice stop")

    S.Player = Player

    # kvaser_bus (2026-11-11): the tool opens the Kvaser through it. The fake's fixes take
    # 0.5 s, so fixes loaded inside the open's time show in open_s.
    KB = types.ModuleType("kvaser_bus")

    def fixes():
        log.add("kvaser fixes")
        time.sleep(0.5)
        return {"kvaser": False, "gs_usb": False}

    KB.fixes = fixes

    def link_down():
        log.add("kvaser down")
        return {"up": False, "bitrate": 500000, "state": "STOPPED", "restart_ms": 100, "listen_only": False}
    KB.link_down = link_down
    fake_gs = Bus

    class Held:
        def __init__(self, role):
            self.role = role

        def __enter__(self):
            log.add("held enter", self.role)
            return self

        def open(self, bitrate=500000, **kw):
            log.add("held open", self.role, bitrate)
            return fake_gs(interface="gs_usb", bitrate=bitrate, **kw)

        def __exit__(self, *exc):
            log.add("held exit", self.role)
            return False
    KB.innomaker_held = Held
    KB.kvaser_bus = lambda **kw: fake_gs(interface="kvaser", **kw)     # the old source: must not be opened
    fake_pcan = Bus      # not called Bus here: test_kvaser_bus's scan reads Bus(...) as an open
    KB.pcan_bus = lambda **kw: fake_pcan(interface="pcan", **kw)     # the PCAN by its canctl role (2026-11-18)
    return {"can": can, "lowid_flood_skip_bench": LF, "skip_method_bench": S, "kvaser_bus": KB}


OUT = {}       # the tool's stdout from the last run_main


def run_main(W, running_after, stamps="device", late=False, silent=False):
    log = Log()
    saved = {k: sys.modules.get(k) for k in ("can", "lowid_flood_skip_bench", "skip_method_bench", "kvaser_bus")}
    sys.modules.update(fakes(log, running_after, stamps, late, silent))
    runs = os.path.join(SCRATCH, "runs")
    gi_paths.remove_tree(runs)
    os.makedirs(runs)
    W.RUNS = runs
    argv = sys.argv
    sys.argv = ["wrong_bitrate_errors.py", "calibrate", "--x2", "held", "--power", "test", "--bursts-ms", "1",
                "--repeats", "1", "--tail-s", "0.2", "--poll-hz", "20"]
    try:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = W.main()
            except Exception as e:      # noqa: BLE001  the tool raising is a FAIL of "ran", not a crash here
                rc = "raised %s: %s" % (type(e).__name__, e)
        OUT["text"] = buf.getvalue()
    finally:
        sys.argv = argv
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    recs = [f for f in os.listdir(runs) if f.endswith(".json")]
    rec = json.load(open(os.path.join(runs, recs[0]), encoding="utf-8")) if len(recs) == 1 else {}
    return rc, log, rec


def order_cases(W):
    rc, log, rec = run_main(W, running_after=2)
    arm = log.index(lambda e: e == ("post", "/gen_inhibit_set?mode=3"))
    start = log.index(lambda e: e == ("slice start",))
    run_seen = log.index(lambda e: e == ("page", "running"))
    stop = log.index(lambda e: e == ("slice stop",))
    disarm = log.index(lambda e: e == ("post", "/gen_inhibit_set?mode=0"))
    check("main: ran and wrote one record", rc == 0 and rec, (rc, sorted(rec)[:5]))
    check("main: armed before the slice starts", arm is not None and start is not None and arm < start, log.ev[:12])
    check("main: the slice starts only after a page shows the driver running",
          run_seen is not None and start is not None and run_seen < start, log.ev[:12])
    check("main: no slice before the arm, not even a started one",
          [e for e in log.ev[:arm or 0] if e[0].startswith("slice")] == [], log.ev[:12])
    check("main: the slice stops before the disarm", stop is not None and disarm is not None and stop < disarm,
          log.ev[-8:])
    check("main: driver_running_before_slice recorded true", rec.get("driver_running_before_slice") is True,
          rec.get("driver_running_before_slice"))
    check("main: the bus states the witness PCAN read past are recorded (F6, 2026-11-19)",
          rec.get("pcan_states") == [[12.5, "PcanCanOperationError: Bus error: The CAN controller is error passive"]],
          rec.get("pcan_states"))
    w = rec.get("pre_burst_window") or {}
    check("main: pre_burst_window recorded, 0 frames from a clean live sample",
          w.get("errors") == 0 and isinstance(w.get("from_s"), float) and -1.0 < w["from_s"] < 0, w)
    check("main: witness_errors_before_first_burst recorded", rec.get("witness_errors_before_first_burst") == 0,
          rec.get("witness_errors_before_first_burst"))
    b = rec.get("bursts") or [{}]
    check("main: the error frame of the source's bus-on is the burst's, not before it (2026-10-29)",
          b[0].get("witness_error_frames") == 1 and rec.get("witness_error_frames") == 1, (b, rec.get("witness_error_frames")))
    check("main: each burst records how long the open took", isinstance(b[0].get("open_s"), float)
          and 0.015 <= b[0]["open_s"] < 1.0, b)
    check("main: the source opens through kvaser_bus, its fixes applied before the open and outside open_s",
          None not in (log.index(lambda e: e == ("kvaser fixes",)), log.index(lambda e: e == ("bus open", "gs_usb")))
          and log.index(lambda e: e == ("kvaser fixes",)) < log.index(lambda e: e == ("bus open", "gs_usb"))
          and isinstance(b[0].get("open_s"), float) and b[0]["open_s"] < 0.4, (b, log.ev[:12]))
    # condition 5 (2026-11-18): canmain, held once; the Kvaser down first
    down = log.index(lambda e: e == ("kvaser down",))
    enter = log.index(lambda e: e == ("held enter", "canmain"))
    pcan = log.index(lambda e: e == ("bus open", "pcan"))
    hopen = log.index(lambda e: e == ("held open", "canmain", 250000))
    pshut = log.index(lambda e: e == ("bus shutdown", "pcan"))
    hexit = log.index(lambda e: e == ("held exit", "canmain"))
    check("main: the Kvaser brought down (read back, recorded) before canmain is held",
          down is not None and enter is not None and down < enter
          and (rec.get("kvaser_link_start") or {}).get("up") is False, (log.ev[:6], rec.get("kvaser_link_start")))
    check("main: canmain held before the PCAN opens, each burst opened on it at 250 kbit, held left after the "
          "PCAN is shut", None not in (enter, pcan, hopen, pshut, hexit) and enter < pcan < hopen < pshut < hexit
          and [e for e in log.ev if e[0] == "bus open" and e[1] == "kvaser"] == [], log.ev)
    check("main: the source recorded as canmain at 250 kbit", "canmain" in rec.get("source", "")
          and "250000" in rec.get("source", ""), rec.get("source"))
    bm = rec.get("burst_measures") or [{}]
    check("main: each burst's measures recorded, within the bounds, no burst-timing VOID",
          len(bm) == 1 and bm[0].get("n") == 1 and bm[0].get("breaches") == [] and isinstance(b[0].get("close_s"), float)
          and "burst_timing_void" not in rec and "burst timing VOID" not in OUT.get("text", ""), (bm, b))
    check("main: live_before true with the slice playing on a running driver", rec.get("live_before") is True,
          rec.get("live_before"))
    # A2 (a), 2026-11-11: the error frames are judged on the PCAN's own stamps, mapped
    fit = rec.get("witness_clock") or {}
    check("main: the PCAN's device stamps fit within the bounds, not VOID",
          fit.get("void") is None and fit.get("n", 0) >= 20 and "witness_clock_void" not in rec
          and "verdict" not in rec, (fit.get("void"), fit.get("n"), rec.get("verdict")))
    check("main: the figures are on the mapped stamps, and say so",
          rec.get("witness_clock_figures", "").startswith("PCAN device stamps"), rec.get("witness_clock_figures"))
    check("main: the fit and its bounds are recorded beside each other",
          (fit.get("bounds") or {}).get("residual_p99_max_s") == 0.05 and isinstance(fit.get("residual_p99_s"), float)
          and isinstance(fit.get("slope_ppm"), float), fit)
    text = OUT.get("text", "")
    check("main: prints the clock fit line, and no VOID line",
          "pcan clock: n " in text and "witness clock VOID" not in text, text[-400:])

    rc, log, rec = run_main(W, running_after=None)
    start = log.index(lambda e: e == ("slice start",))
    arm = log.index(lambda e: e == ("post", "/gen_inhibit_set?mode=3"))
    check("main, driver never running: the slice still starts after the arm", start is not None and arm < start,
          log.ev[:8])
    check("main, driver never running: recorded false", rec.get("driver_running_before_slice") is False,
          rec.get("driver_running_before_slice"))
    check("main, driver never running: no clean sample, so no count", (rec.get("pre_burst_window") or {}).get("errors")
          is None, rec.get("pre_burst_window"))

    # COOKED: the PCAN's stamps on the host's epoch clock are host receipt time -> VOID (A2 (a))
    rc, log, rec = run_main(W, running_after=2, stamps="epoch")
    text = OUT.get("text", "")
    check("main, stamps on the epoch clock: ran and wrote one record", rc == 0 and rec, (rc, sorted(rec)[:5]))
    check("main, stamps on the epoch clock: VOID with the reason",
          "epoch clock" in str(rec.get("witness_clock_void")) and str(rec.get("verdict", "")).startswith("VOID"),
          (rec.get("witness_clock_void"), rec.get("verdict")))
    check("main, stamps on the epoch clock: prints the VOID line the suite reads",
          "\nwitness clock VOID: " in "\n" + text, text[-400:])
    check("main, stamps on the epoch clock: the figures are labelled receipt, not judged",
          "not judged" in rec.get("witness_clock_figures", ""), rec.get("witness_clock_figures"))
    check("main, stamps on the epoch clock: no burst measured on them", "burst_measures" not in rec, rec.get("burst_measures"))

    # an error frame 30 ms after the close: until 2026-11-20 (condition 5) VOID; since the gen-inhibit reviewer's
    # ruling of that day (item 4(ii)) a run's burst lengths and closes are recorded, not judged
    rc, log, rec = run_main(W, running_after=2, late=True)
    text = OUT.get("text", "")
    bm = rec.get("burst_measures") or [{}]
    check("main, the source errs 30 ms after its close: the breach recorded",
          any("after the close" in x for x in bm[0].get("breaches", [])), bm)
    check("main, the source errs 30 ms after its close: recorded, not judged: no VOID, no burst timing VOID line",
          not str(rec.get("verdict", "")).startswith("VOID") and "burst_timing_void" not in rec
          and "burst timing VOID" not in text and bm[0].get("void") == [], (rec.get("verdict"), text[-300:]))
    check("main: each burst's measure and its source's state printed under it, recorded not judged",
          "    measure: span " in text and "recorded, not judged | source: bus_off False" in text, text[-600:])
    so = ((rec.get("bursts") or [{}])[0]).get("source") or {}
    check("main: each burst's source state recorded (read during the hold)",
          so.get("error_frames") == 0 and so.get("bus_off") is False and so.get("restarts") == 0
          and so.get("read", "").startswith("during the hold"), so)
    import inspect
    msrc = inspect.getsource(W.main)
    check("main: the run VOIDs only on run_void_line (a burst with no witness error frame), not breach_line",
          "burst_line = BM.run_void_line(rec[\"burst_measures\"])" in msrc and "BM.breach_line" not in msrc, "")
    check("main: each burst read with its source", "burst(ms, inno, read_source=True)" in msrc, "")
    # a burst that drew no witness error frame still VOIDs the run, with the line the suite reads
    rc, log, rec = run_main(W, running_after=2, silent=True)
    text = OUT.get("text", "")
    check("main, a burst with no witness error frame: VOID verdict and the burst timing VOID line",
          str(rec.get("verdict", "")).startswith("VOID: burst timing VOID: burst 1 ms: no witness error frame")
          and "\nburst timing VOID: burst 1 ms: no witness error frame" in "\n" + text,
          (rec.get("verdict"), text[-300:]))
    check("main, the source errs 30 ms after its close: canmain still left (held exit)",
          log.index(lambda e: e == ("held exit", "canmain")) is not None, log.ev[-5:])


def dead_witness_cases(W):
    """(2026-11-19, the gen-inhibit reviewer's ruling (i)) a witness loop that dies VOIDs the run: the
    record's verdict and the "witness VOID" line the suite's B9 judge reads first, never a count of
    error frames from a witness that stopped reading."""
    real = W.PcanWitness

    class Dies(real):
        def run(self):
            raise OSError("the PCAN went away (test)")
    W.PcanWitness = Dies
    try:
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):      # the dead thread's traceback, expected
            rc, _log, rec = run_main(W, running_after=2)
    finally:
        W.PcanWitness = real
    t = OUT.get("text", "")
    check("main, the witness loop dies: ran and wrote one record", rc == 0 and rec, rc)
    check("main, the witness loop dies: the record VOID, naming the dead thread",
          (rec.get("verdict") or "").startswith("VOID: the witness thread failed") and
          "went away" in (rec.get("witness_proc_void") or ""), (rec.get("verdict"), rec.get("witness_proc_void")))
    check("main, the witness loop dies: prints the witness VOID line",
          any(ln.startswith("witness VOID: the witness thread failed") for ln in t.splitlines()), t[-300:])
    rc, _log, rec = run_main(W, running_after=2)
    check("main, the witness whole: no witness VOID (the control)",
          "witness_proc_void" not in rec and "witness VOID:" not in OUT.get("text", ""), rec.get("verdict"))


def main():
    gi_paths.remove_tree(SCRATCH)
    W = load()
    for part in (window_cases, order_cases, dead_witness_cases):
        try:
            part(W)
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    gi_paths.remove_tree(SCRATCH)
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
