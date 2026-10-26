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

main() runs against fakes put in sys.modules: can (PCAN, Kvaser), lowid_flood_skip_bench
(the device: a log of POSTs and page reads) and skip_method_bench (the Player). About 10 s.

    py -3.14 -B tools/gen_inhibit/bench/test_wrong_bitrate_sequence.py
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


def fakes(log, running_after):
    """running_after: page reads after the mode 3 POST before the driver reports running
    (None: never)."""
    can = types.ModuleType("can")

    class CanError(Exception):
        pass

    class Message:
        def __init__(self, arbitration_id=0, is_extended_id=False, data=b""):
            self.arbitration_id, self.data, self.is_error_frame = arbitration_id, data, False

    class Bus:
        def __init__(self, interface=None, channel=None, bitrate=None):
            self.interface = interface
            log.add("bus open", interface)

        def recv(self, timeout=None):
            time.sleep(timeout or 0.01)
            return None

        def send(self, msg, timeout=None):
            pass

        def status_string(self):
            return "OK"

        def shutdown(self):
            log.add("bus shutdown", self.interface)

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
    return {"can": can, "lowid_flood_skip_bench": LF, "skip_method_bench": S}


def run_main(W, running_after):
    log = Log()
    saved = {k: sys.modules.get(k) for k in ("can", "lowid_flood_skip_bench", "skip_method_bench")}
    sys.modules.update(fakes(log, running_after))
    runs = os.path.join(SCRATCH, "runs")
    shutil.rmtree(runs, ignore_errors=True)
    os.makedirs(runs)
    W.RUNS = runs
    argv = sys.argv
    sys.argv = ["wrong_bitrate_errors.py", "calibrate", "--x2", "held", "--power", "test", "--bursts-ms", "1",
                "--repeats", "1", "--tail-s", "0.2", "--poll-hz", "20"]
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                rc = W.main()
            except Exception as e:      # noqa: BLE001  the tool raising is a FAIL of "ran", not a crash here
                rc = "raised %s: %s" % (type(e).__name__, e)
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
    w = rec.get("pre_burst_window") or {}
    check("main: pre_burst_window recorded, 0 frames from a clean live sample",
          w.get("errors") == 0 and isinstance(w.get("from_s"), float) and -1.0 < w["from_s"] < 0, w)
    check("main: witness_errors_before_first_burst recorded", rec.get("witness_errors_before_first_burst") == 0,
          rec.get("witness_errors_before_first_burst"))
    check("main: live_before true with the slice playing on a running driver", rec.get("live_before") is True,
          rec.get("live_before"))

    rc, log, rec = run_main(W, running_after=None)
    start = log.index(lambda e: e == ("slice start",))
    arm = log.index(lambda e: e == ("post", "/gen_inhibit_set?mode=3"))
    check("main, driver never running: the slice still starts after the arm", start is not None and arm < start,
          log.ev[:8])
    check("main, driver never running: recorded false", rec.get("driver_running_before_slice") is False,
          rec.get("driver_running_before_slice"))
    check("main, driver never running: no clean sample, so no count", (rec.get("pre_burst_window") or {}).get("errors")
          is None, rec.get("pre_burst_window"))


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    W = load()
    for part in (window_cases, order_cases):
        try:
            part(W)
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
