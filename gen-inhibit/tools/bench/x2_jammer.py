"""Host side of the X2 jammer (x2_jammer/, test equipment, NOT FOR VEHICLE).

Gen-inhibit tester, 2026-10-08. Talks to the jammer image on the
ESP32-CAN-X2's USB-Serial-JTAG console (COM3 on NICKY-XPS), watches the
WiCAN's segment with an independent witness (Kvaser, NORMAL mode -- a silent
Kvaser reports 0 error frames whatever happens), and judges what was
DELIVERED, never what was commanded.

Pure functions (tested by test_x2_jammer.py, no hardware):
  parse_line(text)          one console line -> dict, or None if not ours
  judge_stop(...)           did delivery stop by the deadline? (timeout checks)
  clusters(ts, gap_s)       error-frame times -> groups, one per pulse
  per_pulse(...)            witness error frames and WiCAN bus_error per pulse

Bench subcommands (each needs the bench lock; each writes
runs/x2_jammer_<sub>_<stamp>.json and never overwrites):
  id                 ask the board for its banner (no reset)
  boot-silence       reset the X2 N times; witness must see 0 error frames
                     from each reset to banner + 5 s
  pulses             PULSES w n interval_ms with the WiCAN in --wican-mode,
                     page polled ~10 Hz: port check (n=1) and calibration
  timeout            TRAIN w p ms; judge_stop against ms (and a negative
                     control against --control-ms, which must FAIL);
                     --host-quiet stops reading the console during the train
  noduration         "TRAIN w p" with no ms: must be refused, witness quiet
  can2-check         WiCAN in mode 2 + n pulses; the Kvaser (WiCAN segment)
                     must see pages and pulses, the Innomaker (X2 CAN2) neither

The console is opened with DTR and RTS pre-set low so opening and closing do
not reset the S3 (tools-reference.md); `boot-silence` resets it deliberately
with esptool's USB-JTAG ordering (console_capture.pulse_rts).

  py -3.14 gen-inhibit/tools/bench/x2_jammer.py <sub> [...]
"""
import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BITRATE = 500000
KINDS = ("ACK", "DONE", "PULSE", "ERR", "CAPPED", "STATUS")
LINE_RE = re.compile(r"^JAMMER (\S+)(.*)$")
KV_RE = re.compile(r"(\w+)=(\S+)")
INT_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------- pure parts

def parse_line(text):
    """'JAMMER <KIND> ...k=v...' -> {'kind', 'what', k: v...}; None if not ours.

    Banner lines (no KIND word) come back as kind 'INFO'. Integers are ints.
    """
    m = LINE_RE.match(text.strip())
    if not m:
        return None
    head, rest = m.group(1), m.group(2)
    if head in KINDS:
        d = {"kind": head}
        words = rest.split()
        d["what"] = words[0] if words and "=" not in words[0] else ""
        body = rest
    else:
        d = {"kind": "INFO", "what": ""}
        body = head + rest
    for k, v in KV_RE.findall(body):
        d[k] = int(v) if INT_RE.fullmatch(v) else v
    d["raw"] = text.strip()
    return d


def judge_stop(err_ts, t_start, t_deadline, obs_start, obs_end,
               margin_s=0.3, quiet_s=5.0, pre_s=2.0, seen_s=0.5):
    """Did the witness's error frames stop by the deadline?

    err_ts: witness error-frame times, same clock as the other arguments.
    PASS needs: the bus observed quiet for pre_s before the start; at least one
      error frame between start and deadline (else there was nothing to stop);
      none after deadline + margin, observed for quiet_s beyond that.
    FAIL: any error frame after deadline + margin.
    INCONCLUSIVE: not observed long enough, the bus not quiet before,
      nothing delivered, or the last frame seen more than seen_s before the
      deadline (the witness may have stopped seeing -- the Kvaser reported
      nothing for 1.5 s mid-train on 2026-10-08 01:24 -- so "none after" would
      be vacuous).
    """
    pre = [t for t in err_ts if t_start - pre_s <= t < t_start - margin_s]
    inside = [t for t in err_ts if t_start - margin_s <= t <= t_deadline + margin_s]
    after = [t for t in err_ts if t > t_deadline + margin_s]
    out = {"n_pre": len(pre), "n_in": len(inside), "n_after": len(after),
           "margin_s": margin_s,
           "last_in_rel_deadline_s": (round(max(inside) - t_deadline, 4)
                                      if inside else None),
           "first_after_rel_deadline_s": (round(min(after) - t_deadline, 4)
                                          if after else None)}
    if after:
        out["verdict"], out["why"] = "FAIL", "error frames after the deadline"
    elif obs_start > t_start - pre_s or obs_end < t_deadline + margin_s + quiet_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "not observed long enough"
    elif pre:
        out["verdict"], out["why"] = "INCONCLUSIVE", "bus not quiet before start"
    elif not inside:
        out["verdict"], out["why"] = "INCONCLUSIVE", "nothing delivered"
    elif max(inside) < t_deadline - seen_s:
        out["verdict"], out["why"] = ("INCONCLUSIVE",
                                      "witness stopped seeing before the deadline")
    else:
        out["verdict"], out["why"] = "PASS", ""
    return out


def judge_counter_stop(samples, t_start, t_deadline, margin_s=0.3, quiet_s=5.0,
                       max_gap_s=1.0, seen_s=0.5):
    """Second witness of the stop: a cumulative error counter read off a page.

    samples: [(t, value or None)], same clock. An increase is placed in the
    interval (t_prev, t]. FAIL if one starts at or after deadline + margin.
    INCONCLUSIVE if the counter never rose during the train, the readings do
    not reach deadline + margin + quiet_s, or any gap after the start exceeds
    max_gap_s (a rise could hide in it), or the last rise ended more than
    seen_s before the deadline (e.g. the device went bus-off and stopped
    counting, so "no late rise" would be vacuous). PASS otherwise.
    """
    good = [(t, v) for t, v in samples if v is not None]
    rises = [(a[0], b[0], b[1] - a[1]) for a, b in zip(good, good[1:])
             if b[1] > a[1]]
    late = [r for r in rises if r[0] >= t_deadline + margin_s]
    during = [r for r in rises if r[1] >= t_start and r[0] < t_deadline + margin_s]
    after_start = [t for t, _ in good if t >= t_start]
    gaps = [b - a for a, b in zip(after_start, after_start[1:])]
    out = {"n_readings": len(good), "rises_during": sum(r[2] for r in during),
           "rises_late": sum(r[2] for r in late),
           "last_rise_end_rel_deadline_s": (round(max(r[1] for r in during) - t_deadline, 3)
                                            if during else None),
           "max_gap_s": round(max(gaps), 3) if gaps else None}
    if late:
        out["verdict"], out["why"] = "FAIL", "counter rose after the deadline"
    elif not during:
        out["verdict"], out["why"] = "INCONCLUSIVE", "counter never rose"
    elif not good or good[-1][0] < t_deadline + margin_s + quiet_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "not observed long enough"
    elif gaps and max(gaps) > max_gap_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "gap in readings"
    elif max(r[1] for r in during) < t_deadline - seen_s:
        out["verdict"], out["why"] = ("INCONCLUSIVE",
                                      "counter stopped rising before the deadline")
    else:
        out["verdict"], out["why"] = "PASS", ""
    return out


def combine(mains, controls):
    """Overall timeout verdict from per-witness verdicts.

    mains / controls: {witness: verdict}. A witness's negative control must
    FAIL only where that witness's own verdict is PASS: a witness that went
    blind (INCONCLUSIVE) cannot fail its control either, and demanding it did
    turned the 2026-10-08 01:25 run into a FAIL although the Kvaser witness
    passed and its control failed. FAIL if any witness FAILs or a passing
    witness's control does not FAIL; PASS if at least one witness PASSes;
    otherwise INCONCLUSIVE.
    """
    bad_controls = [w for w, v in mains.items()
                    if v == "PASS" and w in controls and controls[w] != "FAIL"]
    if "FAIL" in mains.values():
        return "FAIL", "a witness saw delivery after the deadline"
    if bad_controls:
        return "FAIL", "negative control did not fail: %s" % ",".join(bad_controls)
    if "PASS" in mains.values():
        return "PASS", ""
    return "INCONCLUSIVE", "no witness could show the stop"


def judge_can2(kv_pages, kv_groups, n_pulses, in_frames, in_err):
    """Does the jammer reach ONLY the WiCAN's segment (reviewer, 2026-10-08)?

    The Kvaser on the WiCAN's segment is the positive control: it must see the
    WiCAN's pages and one error-frame group per pulse. The Innomaker, on the
    X2's CAN2 (user), ACKing in normal mode, must see neither frames nor error
    frames. With the jammer image there is no bridge, so any WiCAN page on the
    Innomaker means it shares the segment.
    Note the limit: whether gs_usb reports error frames at all is not shown
    here, so "0 error frames" on it rests on the frame half of the check.
    """
    if kv_pages < 1 or kv_groups != n_pulses:
        return "INCONCLUSIVE", "positive control failed on the Kvaser"
    if in_frames or in_err:
        return "FAIL", "the Innomaker saw traffic from the WiCAN's segment"
    return "PASS", ""


def clusters(ts, gap_s):
    """Sorted times split wherever the gap exceeds gap_s."""
    out = []
    for t in sorted(ts):
        if out and t - out[-1][-1] <= gap_s:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def per_pulse(err_ts, n_cmd, interval_s, samples=None, slack_s=0.05):
    """Witness error frames per pulse, and the WiCAN's bus_error delta per pulse.

    Clock-free attribution: error frames are grouped by gaps > interval/2, one
    group per delivered pulse. samples: [(t, bus_error or None)] from the
    WiCAN page, same clock as err_ts; a pulse's delta is the first reading
    after its group minus the last reading before it, and is None if either
    reading is missing or another group falls between them.
    slack_s: readings within slack_s of a group are not used. The page is
    stamped on the host clock and error frames on the Kvaser's; on
    2026-10-08 01:45 a reading 1.4 ms BEFORE a pulse's stamped error frame
    already counted it, and the delta came out 0 with no slack.
    """
    cl = clusters(err_ts, interval_s / 2.0)
    counts = [len(c) for c in cl]
    deltas = []
    good = [(t, v) for t, v in (samples or []) if v is not None]
    for i, c in enumerate(cl):
        before = [s for s in good if s[0] < c[0] - slack_s]
        after = [s for s in good if s[0] > c[-1] + slack_s]
        if not before or not after:
            deltas.append(None)
            continue
        b, a = before[-1], after[0]
        lo = cl[i - 1][-1] if i > 0 else float("-inf")
        hi = cl[i + 1][0] if i + 1 < len(cl) else float("inf")
        if b[0] <= lo or a[0] >= hi:
            deltas.append(None)
            continue
        deltas.append(a[1] - b[1])
    return {"commanded": n_cmd, "groups": len(cl), "per_pulse": counts,
            "total": sum(counts), "bus_error_delta_per_pulse": deltas,
            "exactly_one_each": len(cl) == n_cmd and all(c == 1 for c in counts)}


# ------------------------------------------------------------- bench parts

class Console(threading.Thread):
    """COM3 reader that survives a reset (reopens) and can be paused."""

    def __init__(self, port="COM3"):
        super().__init__(daemon=True)
        self.port = port
        self.stop_flag = threading.Event()
        self.paused = threading.Event()
        self.lines = []      # (epoch, text, parsed)
        self.events = []     # (epoch, what)
        self.lock = threading.Lock()
        self.ser = None

    def _open(self):
        import serial
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = self.port, 115200, 0.05
        s.dtr = False
        s.rts = False
        s.open()
        s.dtr = False
        s.rts = False
        return s

    def run(self):
        pending, first = b"", True
        while not self.stop_flag.is_set():
            if self.paused.is_set():
                time.sleep(0.05)
                continue
            if self.ser is None:
                try:
                    self.ser = self._open()
                    self.events.append((time.time(), "open" if first else "reopened"))
                    first = False
                except Exception:
                    time.sleep(0.05)
                    continue
            try:
                chunk = self.ser.read(4096)
            except Exception as e:
                self.events.append((time.time(), "lost: %s" % type(e).__name__))
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                continue
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                txt = raw.decode("latin-1").rstrip("\r")
                with self.lock:
                    self.lines.append((time.time(), txt, parse_line(txt)))
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass

    def send(self, line):
        self.ser.write((line + "\n").encode("ascii"))
        self.events.append((time.time(), "sent: %s" % line))

    def wait(self, pred, timeout, since):
        """First parsed line after epoch `since` satisfying pred, or None."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for t, _txt, p in self.lines:
                    if t >= since and p is not None and pred(p):
                        return t, p
            time.sleep(0.02)
        return None

    def pulse_reset(self):
        """esptool's USB-JTAG hard reset (see console_capture.pulse_rts)."""
        s = self.ser
        s.dtr = False
        s.rts = True
        s.dtr = False
        s.rts = True
        time.sleep(0.2)
        s.rts = False
        s.dtr = False
        s.rts = False
        self.events.append((time.time(), "reset pulsed"))


class Witness(threading.Thread):
    """Kvaser in NORMAL mode: records every error frame and the 0x051 count."""

    def __init__(self):
        super().__init__(daemon=True)
        import can
        self.bus = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
        self.stop_flag = threading.Event()
        self.err = []        # (hw_ts, receipt_epoch)
        self.frames = 0
        self.ids = {}
        self.t_open = time.time()
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                now = time.time()
                with self.lock:
                    if m.is_error_frame:
                        self.err.append((m.timestamp, now))
                    else:
                        self.frames += 1
                        k = "0x%03X" % m.arbitration_id
                        self.ids[k] = self.ids.get(k, 0) + 1
                m = self.bus.recv(timeout=0.0)

    def close(self):
        self.stop_flag.set()
        self.join(timeout=2.0)
        self.bus.shutdown()

    def times(self):
        """Error-frame times on the epoch clock, and which clock was used."""
        with self.lock:
            e = list(self.err)
        if not e:
            return [], "none"
        off = statistics.median(r - h for h, r in e)
        if abs(off) < 2.0:
            return [h for h, _ in e], "kvaser_hw (median receipt lag %.3f s)" % off
        return [r for _, r in e], "receipt (hw ts not epoch: offset %.1f s)" % off


def wican_snap():
    sys.path.insert(0, HERE)
    import lowid_flood_skip_bench as LF
    p = LF.page()
    if p is None:
        return None
    d = p.get("drv") or {}
    return {"t": time.time(), "mode": p.get("mode"), "drv_null": p.get("drv") is None,
            "bus_error": d.get("bus_error"), "tec": d.get("tec"), "rec": d.get("rec"),
            "state": d.get("state"), "bus_on": p.get("bus_on"),
            "tx_ok": p.get("tx_ok")}


def wican_mode(n):
    import lowid_flood_skip_bench as LF
    LF.dev_post("/gen_inhibit_set?mode=%d" % n)


def write_rec(sub, rec):
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "x2_jammer_%s_%s.json" % (sub, rec["when"]))
    with open(path, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
    print("wrote", path)


def _console_dump(con, since=0.0):
    with con.lock:
        return [(round(t, 4), x) for t, x, _ in con.lines if t >= since]


def sub_id(a, rec, con, wit):
    # An empty line first. The jammer runs a line only on LF and drops CR, so input an
    # earlier probe left unterminated (bench_identify's slcan "V\r" "N\r", 2026-10-19)
    # would join the ID line and come back unknown_command. The empty line runs whatever
    # is pending (an empty buffer is ignored) before ID is sent.
    con.send("")
    time.sleep(0.3)
    t = time.time()
    con.send("ID")
    got = con.wait(lambda p: p["kind"] == "INFO" and "mcp2515" in p["raw"], 3.0, t)
    rec["banner_ok"] = got is not None
    time.sleep(0.3)


def sub_boot_silence(a, rec, con, wit):
    rec["resets"] = []
    for k in range(a.resets):
        time.sleep(1.0)
        t0 = time.time()
        con.pulse_reset()
        got = con.wait(lambda p: p["kind"] == "INFO" and "mcp2515" in p["raw"],
                       15.0, t0)
        t_banner = got[0] if got else None
        time.sleep(5.0)
        ts, clock = wit.times()
        end = (t_banner or t0) + 5.0
        n = sum(1 for t in ts if t0 - 0.3 <= t <= end)
        rec["resets"].append({"n": k, "banner_after_s": (round(t_banner - t0, 3)
                                                         if t_banner else None),
                              "canstat": got[1].get("canstat") if got else None,
                              "config_mode": got[1].get("config_mode") if got else None,
                              "witness_err_frames": n, "clock": clock})
        print("reset %d: banner after %s s, err frames %d, canstat %s"
              % (k, rec["resets"][-1]["banner_after_s"], n,
                 rec["resets"][-1]["canstat"]))
    ok = all(r["banner_after_s"] is not None and r["witness_err_frames"] == 0
             and r["config_mode"] == "yes" for r in rec["resets"])
    rec["verdict"] = "PASS" if ok else "FAIL"


def sub_pulses(a, rec, con, wit):
    rec["wican_mode"] = a.wican_mode
    wican_mode(a.wican_mode)
    time.sleep(2.0)
    samples, stop = [], threading.Event()

    def poll():
        while not stop.is_set():
            s = wican_snap()
            samples.append(s if s else {"t": time.time(), "unanswered": True})
            time.sleep(0.1)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    time.sleep(2.0)
    t0 = time.time()
    con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
    ack = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t0)
    done = con.wait(lambda p: p["kind"] == "DONE", a.n * a.interval_ms / 1000.0 + 5.0, t0)
    time.sleep(2.0)
    stop.set()
    th.join(timeout=2.0)
    wican_mode(0)
    ts, clock = wit.times()
    win = [t for t in ts if t0 - 0.3 <= t <= (done[0] if done else time.time()) + 1.0]
    pre = [t for t in ts if t0 - 4.0 <= t < t0 - 0.3]
    be = [(s["t"], s.get("bus_error")) for s in samples if not s.get("unanswered")]
    rec.update({"ack": ack[1] if ack else None, "done": done[1] if done else None,
                "clock": clock, "witness_err_frames_pre_4s": len(pre),
                "per_pulse": per_pulse(win, a.n, a.interval_ms / 1000.0, be),
                "jammer_pulses": [p for t, _x, p in con.lines
                                  if p and p["kind"] == "PULSE" and t >= t0],
                "wican_samples": samples, "err_ts_rel_t0": [round(t - t0, 4) for t in win]})
    pp = rec["per_pulse"]
    print("commanded %d, groups %d, per pulse %s, bus_error deltas %s, pre %d"
          % (a.n, pp["groups"], pp["per_pulse"], pp["bus_error_delta_per_pulse"],
             len(pre)))


def _poller(samples, stop):
    while not stop.is_set():
        x = wican_snap()
        samples.append(x if x else {"t": time.time(), "unanswered": True})
        time.sleep(0.1)


def sub_timeout(a, rec, con, wit):
    rec["wican_mode"] = a.wican_mode
    samples, stop = [], threading.Event()
    th = None
    if a.wican_mode:
        wican_mode(a.wican_mode)
        time.sleep(2.0)
        th = threading.Thread(target=_poller, args=(samples, stop), daemon=True)
        th.start()
    time.sleep(2.5)  # quiet observation before the start (judge pre_s = 2 s)
    t_send = time.time()
    con.send("TRAIN %d %d %d" % (a.w, a.p, a.ms))
    ack = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t_send)
    capped = con.wait(lambda p: p["kind"] == "CAPPED", 0.5, t_send)
    if ack is None or ack[1]["kind"] != "ACK":
        rec["verdict"] = "INCONCLUSIVE"
        rec["why"] = "no ACK: %s" % (ack[1] if ack else None)
        stop.set()
        return
    t_start = ack[0]
    eff_ms = ack[1].get("ms", a.ms)
    if a.host_quiet:
        con.paused.set()
        rec["host_quiet_from"] = round(time.time() - t_start, 3)
    t_deadline = t_start + (a.expect_ms or eff_ms) / 1000.0
    # Watch past deadline + margin + quiet (judge_stop) with 2 s to spare
    # for the witness's delivery lag. The 01:24 run stopped ~0.5 s short.
    end = t_start + eff_ms / 1000.0 + 0.3 + 5.0 + 2.0
    while time.time() < end:
        time.sleep(0.1)
    con.paused.clear()
    stop.set()
    if th is not None:
        th.join(timeout=2.0)
        wican_mode(0)
    done = con.wait(lambda p: p["kind"] == "DONE", 3.0, t_start)
    ts, clock = wit.times()
    obs_end = time.time() - 1.0
    j = judge_stop(ts, t_start, t_deadline, wit.t_open, obs_end)
    be = [(x["t"], x.get("bus_error")) for x in samples if not x.get("unanswered")]
    rec.update({"ack": ack[1], "capped": capped[1] if capped else None,
                "effective_ms": eff_ms, "done": done[1] if done else None,
                "clock": clock, "judge": j,
                "err_ts_rel_start": [round(t - t_start, 4) for t in ts
                                     if t >= t_start - 2.5],
                "wican_samples": samples})
    if a.wican_mode:
        rec["judge_wican_bus_error"] = judge_counter_stop(be, t_start, t_deadline)
    if a.control_ms:
        c = judge_stop(ts, t_start, t_start + a.control_ms / 1000.0, wit.t_open,
                       obs_end)
        rec["negative_control"] = {"expect_ms": a.control_ms, "judge": c,
                                   "ok": c["verdict"] == "FAIL"}
        if a.wican_mode:
            cw = judge_counter_stop(be, t_start, t_start + a.control_ms / 1000.0)
            rec["negative_control"]["judge_wican_bus_error"] = cw
            rec["negative_control"]["ok"] = (rec["negative_control"]["ok"]
                                             and cw["verdict"] == "FAIL")
    mains = {"kvaser": j["verdict"]}
    controls = {}
    if a.wican_mode:
        mains["wican_bus_error"] = rec["judge_wican_bus_error"]["verdict"]
    if a.control_ms:
        controls["kvaser"] = rec["negative_control"]["judge"]["verdict"]
        if a.wican_mode:
            controls["wican_bus_error"] = (
                rec["negative_control"]["judge_wican_bus_error"]["verdict"])
        rec["negative_control"]["ok"] = all(
            controls[w] == "FAIL" for w, v in mains.items()
            if v == "PASS" and w in controls)
    rec["verdict"], rec["why"] = combine(mains, controls)
    print("verdict %s | kvaser %s n_in %d n_after %d last_in %s | wican %s | control ok %s | %s"
          % (rec["verdict"], j["verdict"], j["n_in"], j["n_after"],
             j["last_in_rel_deadline_s"],
             rec.get("judge_wican_bus_error"),
             rec.get("negative_control", {}).get("ok"),
             (done[1]["raw"] if done else None)))


def sub_noduration(a, rec, con, wit):
    time.sleep(2.0)
    t0 = time.time()
    con.send("TRAIN %d %d" % (a.w, a.p))
    got = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t0)
    time.sleep(3.0)
    ts, clock = wit.times()
    n = sum(1 for t in ts if t >= t0 - 0.3)
    rec.update({"reply": got[1] if got else None, "witness_err_frames": n,
                "clock": clock})
    rec["verdict"] = ("PASS" if got and got[1]["kind"] == "ERR" and n == 0
                      else "FAIL")
    print("reply %s, err frames %d -> %s" % (got[1]["raw"] if got else None, n,
                                            rec["verdict"]))


def sub_can2_check(a, rec, con, wit):
    import can
    try:
        import libusb_package
        import usb.backend.libusb1 as lb
        lb.get_backend(find_library=libusb_package.find_library)
    except Exception:
        pass
    inno = can.Bus(interface="gs_usb", channel="can0", index=0, bitrate=BITRATE)
    # Every Innomaker frame is logged with host and adapter time, so a frame
    # left in its buffer from before the run can be told from a live one: the
    # 01:28 run logged one 0x051 with nothing else to place it (2026-10-08).
    log = []           # (phase, epoch, adapter_ts, id, err, data)
    phase = ["drain"]
    stop = threading.Event()

    def rd():
        while not stop.is_set():
            m = inno.recv(timeout=0.05)
            if m is None:
                continue
            log.append((phase[0], round(time.time(), 4), m.timestamp,
                        "0x%03X" % m.arbitration_id, bool(m.is_error_frame),
                        bytes(m.data).hex()))
    th = threading.Thread(target=rd, daemon=True)
    th.start()
    try:
        time.sleep(2.0)  # drain: anything already buffered lands in phase "drain"
        phase[0] = "test"
        f0 = wit.frames
        ids0 = dict(wit.ids)
        wican_mode(2)
        time.sleep(3.0)
        t0 = time.time()
        con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
        done = con.wait(lambda p: p["kind"] == "DONE",
                        a.n * a.interval_ms / 1000.0 + 5.0, t0)
        time.sleep(2.0)
        wican_mode(0)
        time.sleep(1.0)
    finally:
        stop.set()
        th.join(timeout=2.0)
        inno.shutdown()
    ts, clock = wit.times()
    groups = clusters([t for t in ts if t >= t0 - 0.3], a.interval_ms / 2000.0)
    kv_pages = wit.frames - f0
    test = [x for x in log if x[0] == "test"]
    in_frames = sum(1 for x in test if not x[4])
    in_err = sum(1 for x in test if x[4])
    v, why = judge_can2(kv_pages, len(groups), a.n, in_frames, in_err)
    kv_ids = {k: wit.ids.get(k, 0) - ids0.get(k, 0) for k in wit.ids
              if wit.ids.get(k, 0) - ids0.get(k, 0)}
    rec.update({"done": done[1] if done else None, "clock": clock,
                "kvaser_frames_during": kv_pages, "kvaser_ids_during": kv_ids,
                "kvaser_err_groups": [len(g) for g in groups],
                "innomaker_log": log, "innomaker_mode": "ACKING (normal mode)",
                "verdict": v, "why": why})
    print("kvaser frames %d %s, err groups %s | innomaker drain %s | test %s -> %s %s"
          % (kv_pages, kv_ids, [len(g) for g in groups],
             [x[1:] for x in log if x[0] == "drain"], [x[1:] for x in test], v, why))


SUBS = {"id": sub_id, "boot-silence": sub_boot_silence, "pulses": sub_pulses,
        "timeout": sub_timeout, "noduration": sub_noduration,
        "can2-check": sub_can2_check}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=sorted(SUBS))
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--resets", type=int, default=3)
    ap.add_argument("--w", type=int, default=20)
    ap.add_argument("--p", type=int, default=200)
    ap.add_argument("--ms", type=int, default=2000)
    ap.add_argument("--expect-ms", type=int, default=0,
                    help="deadline to judge against (default: the ACKed ms)")
    ap.add_argument("--control-ms", type=int, default=0,
                    help="negative control: judge again against this; must FAIL")
    ap.add_argument("--host-quiet", action="store_true")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--interval-ms", type=int, default=1000)
    ap.add_argument("--wican-mode", type=int, default=0)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "sub": a.sub, "args": vars(a),
           "wican_before": wican_snap()}
    con = Console(a.port)
    con.start()
    wit = None
    try:
        wit = Witness()
        wit.start()
        time.sleep(1.0)
        SUBS[a.sub](a, rec, con, wit)
    finally:
        try:
            if con.ser is not None:
                con.send("STOP")
                time.sleep(0.3)
        except Exception:
            pass
        rec["console"] = _console_dump(con)
        rec["console_events"] = [(round(t, 4), e) for t, e in con.events]
        con.stop_flag.set()
        con.join(timeout=2.0)
        if wit is not None:
            rec["witness_frames"] = wit.frames
            rec["witness_err_total"] = len(wit.err)
            wit.close()
        rec["wican_after"] = wican_snap()
        write_rec(a.sub, rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
