#!/usr/bin/env python3
"""Does canmain hold a burst for its length and close when asked? Measured on the PCAN's stamps.

Gen-inhibit tester, 2026-11-18: the gen-inhibit reviewer's condition 5 and its ruling on the
measures and bounds the same day. B9's and B10's wrong-bit-rate bursts (wrong_bitrate_errors.burst,
250 kbit) and NONODE's replayer (500 kbit, closed 5 ms after its last frame) moved from the Kvaser
to canmain, CAN1's Innomaker, opened through gs_usb over libusb on the role held once for the run
(kvaser_bus.innomaker_held). This check shows, before those runs count, that gs_usb does what they
assume. It judges with burst_measures, the functions B9, B10 and NONODE use on their own runs. It
runs first in the short bench check, under the bench lock.

THE BENCH, READ BACK AND RECORDED before anything is sent (REFUSED, exit 2, if not):
  WiCAN   mode 0, its driver not running (POST mode 0, then the page): it does not ACK.
  X2      held in ROM, CAN1 TX not driven (`x2_hold.py pins`, nonode_bench.x2_held).
  Kvaser  link down (kvaser_bus.link_down, read back): an up kvaser link ACKs with no socket.
So only canmain and the PCAN can ACK on CAN1.
  PCAN    500 kbit, NORMAL (it ACKs canmain's frames), the witness: every message with its own
          stamp, mapped onto perf_counter by witness_clock.device_fit.

(b) THE 5 ms CLOSE, at 500 kbit (first, so its frames give the fit its points):
  canmain sends REPLAY_ID every --replay-period-ms for --replay-s, each frame with its own count,
  then waits --close-after-ms (5, NONODE's CLOSE_AFTER_S) and closes; the close is the shutdown
  call. From the close + SILENCE_EDGE_S (20 ms) the PCAN sends PROBE_ID and listens for PROBE_S;
  then canmain is opened again for ACK_S, which ACKs the PCAN's retries and ends them.
  close_measure: the last REPLAY_ID frame (mapped before the close + 20 ms) is 5 ms +/- 3 ms before
  the close; no REPLAY_ID frame from the close + 20 ms to the re-open; the probe not ACKed (an
  error frame on the witness after it, before the re-open).
(a) BURST LENGTH, at 250 kbit: for each --bursts-ms (1, 5, 20: every length B9's and B10's
  schedules use), --repeats times, --gap-s apart, wrong_bitrate_errors.burst (open canmain, one
  send of its SOURCE_ID, hold, close). burst_measure on its window: at least one error frame; the
  last at most 5 ms after the close; the span at most the asked length + 5 ms and, from 5 ms up, at
  least the asked length - 2 ms.

VERDICT: VOID if the fit is VOID (as everywhere: never PASS on host time); FAIL if any measure
breached; PASS otherwise. Exit 0 PASS, 1 FAIL or VOID, 2 REFUSED.

CAN-FAILS (each must FAIL by its verdict, not by an error):
  --hold-extra-ms 20   every burst held 20 ms past its asked length (over-hold);
  --cut-half           every burst held half its asked length (cut short);
  --close-after-ms 30  the close 30 ms after the last frame.

    python3.13 -B canmain_burst_check.py
    python3.13 -B canmain_burst_check.py --hold-extra-ms 20
Writes runs/canmain_burst_check_<stamp>.json (GI_RUNS_DIR if set).
"""
import argparse
import json
import os
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burst_measures as BM  # noqa: E402
import witness_clock as WC  # noqa: E402
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
ROLE = "canmain"
REPLAY_ID = 0x6A8
PROBE_ID = 0x6A9
REPLAY_BITRATE = 500000
PROBE_S = 0.2
ACK_S = 0.1


class Witness(threading.Thread):
    """The PCAN: every message as (perf, epoch, stamp, id, is_error, data hex)."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.msgs = bus, []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is None:
                continue
            self.msgs.append((time.perf_counter(), time.time(), m.timestamp, m.arbitration_id,
                              bool(m.is_error_frame), bytes(m.data).hex()))


def bench_state(LF, NN, KB):
    """The read-backs: {wican, x2, kvaser} and the refusal reason or None."""
    st = {}
    try:
        LF.dev_post("/gen_inhibit_set?mode=0")
        time.sleep(0.5)
        p = LF.page() or {}
        st["wican"] = {"mode": p.get("mode"), "driver": (p.get("drv") or {}).get("state")}
    except Exception as e:      # noqa: BLE001  recorded, refuses below
        st["wican"] = {"error": "%s: %s" % (type(e).__name__, e)}
    line = NN.x2_pins(RUNS)
    st["x2"] = {"pins": line, "held": NN.x2_held(line)}
    try:
        st["kvaser"] = KB.link_down()
    except Exception as e:      # noqa: BLE001
        st["kvaser"] = {"error": "%s: %s" % (type(e).__name__, e)}
    why = []
    if st["wican"].get("mode") != 0 or st["wican"].get("driver") == "running":
        why.append("the WiCAN is not in mode 0 with its driver stopped: %s" % st["wican"])
    if not st["x2"]["held"]:
        why.append("the X2 is not shown held: %s" % line)
    if st["kvaser"].get("up") is not False:
        why.append("the Kvaser link is not read back down: %s" % st["kvaser"])
    return st, "; ".join(why) or None


def replay_and_close(inno, pc, a, can):
    """(b): returns the host times {last_send, close, probe, ack_open, ack_close} and the count."""
    b = inno.open(bitrate=REPLAY_BITRATE)
    period = a.replay_period_ms / 1000.0
    n, t0 = 0, time.perf_counter()
    last_send = None
    try:
        while time.perf_counter() - t0 < a.replay_s:
            b.send(can.Message(arbitration_id=REPLAY_ID, is_extended_id=False, data=n.to_bytes(4, "big")),
                   timeout=0.05)
            last_send = time.perf_counter()
            n += 1
            time.sleep(max(0.0, t0 + n * period - time.perf_counter()))
        time.sleep(a.close_after_ms / 1000.0)
    finally:
        t_close = time.perf_counter()
        b.shutdown()
    while time.perf_counter() < t_close + BM.SILENCE_EDGE_S + 0.005:
        time.sleep(0.001)
    t_probe = time.perf_counter()
    try:
        pc.send(can.Message(arbitration_id=PROBE_ID, is_extended_id=False, data=b"\x01"), timeout=0.05)
    except can.CanError:
        pass
    time.sleep(PROBE_S)
    t_ack = time.perf_counter()
    b2 = inno.open(bitrate=REPLAY_BITRATE)     # ACKs the PCAN's retries, which ends them
    time.sleep(ACK_S)
    b2.shutdown()
    return {"last_send": last_send, "close": t_close, "probe": t_probe, "ack_open": t_ack,
            "ack_close": time.perf_counter()}, n, "%08x" % (n - 1)


def judge(msgs, times, last_payload, starts):
    """The fit, the close measure and the burst measures, and the verdict."""
    fit = WC.device_fit([(p, e, ts) for p, e, ts, *_ in msgs])
    out = {"fit": fit}
    if fit["void"]:
        out["verdict"] = WC.pcan_void_verdict(fit["void"])
        return out
    mapped = [(WC.mapped(fit, ts), fid, err, data) for _p, _e, ts, fid, err, data in msgs]
    t_close = times["close"]
    last = max((t for t, fid, err, data in mapped if fid == REPLAY_ID and not err and data == last_payload
                and t < t_close + BM.SILENCE_EDGE_S), default=None)
    late = [t for t, fid, err, _d in mapped if fid == REPLAY_ID and not err
            and t_close + BM.SILENCE_EDGE_S <= t < times["ack_open"]]
    probe_errs = [t for t, _f, err, _d in mapped if err and times["probe"] <= t < times["ack_open"]]
    out["close"] = BM.close_measure(last, t_close, late, probe_acked=not probe_errs)
    out["close"]["probe_error_frames"] = len(probe_errs)
    errs = [t for t, _f, err, _d in mapped if err]      # each burst's window starts after the probe's re-open
    st = [s for s, _ms, _h, _c in starts]
    cl = [c for _s, _ms, _h, c in starts]
    out["bursts"] = []
    for k, (s, ms, hold, c) in enumerate(starts):
        m = BM.burst_measure(ms, c, BM.in_window(errs, BM.burst_window(st, cl, k)))
        m["held_ms"] = hold
        for key in ("first_s", "last_s"):
            m[key] = None if m[key] is None else round(m[key] - s, 6)
        out["bursts"].append(m)
    line = BM.breach_line([out["close"]] + out["bursts"])
    out["verdict"] = "FAIL: %s" % line[len(BM.BURST_VOID_PREFIX):] if line else "PASS"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts-ms", default="1,5,20")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--gap-s", type=float, default=1.0)
    ap.add_argument("--hold-extra-ms", type=float, default=0.0, help="can-fail: hold each burst this much longer")
    ap.add_argument("--cut-half", action="store_true", help="can-fail: hold each burst half its length")
    ap.add_argument("--close-after-ms", type=float, default=BM.CLOSE_AFTER_S * 1000,
                    help="the close this long after the last frame (can-fail: 30)")
    ap.add_argument("--replay-s", type=float, default=1.0)
    ap.add_argument("--replay-period-ms", type=float, default=2.0)
    a = ap.parse_args(argv)
    import can
    import kvaser_bus as KB
    import lowid_flood_skip_bench as LF
    import nonode_bench as NN
    import wrong_bitrate_errors as W
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "args": vars(a), "role": ROLE,
           "bounds": {"close_edge_s": BM.CLOSE_EDGE_S, "span_over_s": BM.SPAN_OVER_S, "span_under_s": BM.SPAN_UNDER_S,
                      "span_under_from_s": BM.SPAN_UNDER_FROM_S, "tail_s": BM.TAIL_S,
                      "close_after_s": BM.CLOSE_AFTER_S, "close_tol_s": BM.CLOSE_TOL_S,
                      "silence_edge_s": BM.SILENCE_EDGE_S}}
    path = os.path.join(RUNS, "canmain_burst_check_%s.json" % stamp)
    rec["bench"], refused = bench_state(LF, NN, KB)
    if refused:
        rec["verdict"] = "REFUSED: %s" % refused
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        print("verdict: %s" % rec["verdict"])
        print("wrote", path)
        return 2
    asked = [float(x) for x in a.bursts_ms.split(",")]
    pc = wit = None
    starts = []
    try:
        pc = KB.pcan_bus(bitrate=500000)
        wit = Witness(pc)
        wit.start()
        with KB.innomaker_held(ROLE) as inno:
            times, n, last_payload = replay_and_close(inno, pc, a, can)
            time.sleep(0.5)
            for ms in asked:
                for _ in range(a.repeats):
                    hold = ms * (0.5 if a.cut_half else 1.0) + a.hold_extra_ms
                    t_s, _opened, t_c = W.burst(hold, inno)
                    starts.append((t_s, ms, hold, t_c))
                    time.sleep(a.gap_s)
    finally:
        if wit is not None:
            wit.stop.set()
            wit.join(3)
        if pc is not None:
            try:
                rec["pcan_status_end"] = pc.status_string()
            except Exception:       # noqa: BLE001
                pass
            pc.shutdown()
    rec["replay"] = {"frames": n, "last_payload": last_payload,
                     "times_s": {k: (None if v is None else round(v - times["close"], 6)) for k, v in times.items()}}
    rec.update(judge(wit.msgs, times, last_payload, starts))
    rec["witness_messages"] = len(wit.msgs)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    print(WC.fit_line(rec["fit"]))
    if "close" in rec:
        c = rec["close"]
        print("close: %s ms after the last frame, %d late frames, probe %s (%d error frames)"
              % (c["gap_ms"], c["late"], c["probe"], c["probe_error_frames"]))
        for m in rec["bursts"]:
            print("burst %g ms (held %g): %d error frames, span %s ms, last %s ms after the close"
                  % (m["asked_ms"], m["held_ms"], m["n"], m["span_ms"], m["last_after_close_ms"]))
    print("verdict: %s" % rec["verdict"])
    print("wrote", path)
    return 0 if rec["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
