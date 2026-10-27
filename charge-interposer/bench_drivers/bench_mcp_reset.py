"""Does the X2's MCP2515 keep ACKing on the charger segment while the ESP32 is down?

Reviewer's measurement, 2026-10-16. The MCP2515 is reset over SPI by the
product's begin(), not by a reset line the ESP drives, so it may stay in
normal mode on the charger segment through an ESP reset until begin() runs:
the charger-side twin of the TWAI restart finding (the implementor's
report). Board image 756d6714, no image change.

The instrument:
  - the Innomaker, the charger segment's only other node, streams
    0x0CFF01C0 (extended, a 4-byte big-endian sequence number) at RATE fps.
    gs_usb returns an echo only for a frame that completed, i.e. was ACKed,
    and stamps it on the Innomaker's own clock. Echo stamps are mapped to
    the host clock by a least-squares fit plus the minimum-latency offset
    (USB can only delay an echo). Each send's host time and failure (a
    send() that times out because the head frame is not completing) is
    recorded too;
  - the PCAN, acknowledging on CAN1, records the board's bridged copies of
    the stream and its power-up round (0x7F4-0x7F7) on the host clock.

THE ESP-DOWN WINDOW IS TAKEN FROM CAN1, NOT FROM THE RESET MARK: from the
last bridged frame before the reset to the first power-up-round frame after
it. A reset that did not happen leaves no such window and is a TEST FAULT
rather than a pass. Each boot's 0x7F7 (cantools, the diag DBC)
must read build 0x756D6714.

Per boot: >= MIN_ACKED echoes inside the window -> ACKED WITH THE ESP DOWN;
0 echoes and one gap spanning it -> NOT ACKED WITH THE ESP DOWN; otherwise
UNCLEAR. Every echo gap longer than the threshold (from the baseline) near
the reset is listed with its send attempts and failures: a gap during which
sends were attempted and failed is the instrument showing no-ACK. A gap with
no send attempt is the sender pausing and counts for nothing.

Variants (run): `--resets N` run_scenario's SerialTap.reset_board(), the
esptool USB-Serial-JTAG hard reset (RTS 0.2 s; the port re-enumerates, so
this is a reset pulse, not a held EN); `--holds M --hold-s S` the ROM
download hold (esptool read-mem with --after no_reset, as gen-inhibit
x2_hold.py does: the app is not running for S s), then a hard reset into
the app.

SCOPE: an internal USB-JTAG chip reset drives no pin, so this covers resets
that leave the MCP2515's own reset line alone (software restart, panic,
watchdog, this USB reset). Not a physical EN reset or power-on, if the X2
ties the MCP2515's reset to EN.

canfail (a separate process: gs_usb cannot be reopened in-process on this
host): the Innomaker at --bitrate 250000 against the board's 500 kbit/s for
2 s. Nothing can ACK it (reviewer-approved; an error storm on the charger
segment only). It must get fewer echoes than MIN_ACKED and some failed
sends: the reviewer's ruling of 2026-10-16 replaced "0 echoes", which
ignored the adapter's queue and mailboxes (judge_canfail; tester,
2026-10-21, after a25e).

normal: the charger segment back to normal after canfail -- a 500 kbit/s
stream, every frame echoed, and the board's LOSS line showing no chg
bus-off and no chg err added during it.

    py -3.14 bench_mcp_reset.py --self-test
    py -3.14 bench_mcp_reset.py run --port COM3 --resets 5 --holds 3 --out X
    py -3.14 bench_mcp_reset.py canfail --out Y
    py -3.14 bench_mcp_reset.py normal --port COM3 --out Z

Tester, 2026-10-16.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never
# from the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

STREAM_ID = 0x0CFF01C0
RATE = 500.0
LAT_S = 0.020              # margin on each side of the ESP-down window
MIN_WINDOW_S = 0.050
MIN_ACKED = 5
GAP_MIN_S = 0.010
ROUND_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
BUILD_EXPECT = 0x756D6714
BASELINE_S = 3.0
SETTLE_S = 4.0
CANFAIL_S = 2.0
NORMAL_S = 6.0
NORMAL_RATE = 100.0
ESPTOOL = os.path.join(os.path.expanduser("~"), ".platformio", "packages",
                       "tool-esptoolpy", "esptool.py")
# 756d6714 prints chg(rx= tx= err= off= ring=a/b); the first version of
# this pattern wanted peak= (the veh field's form), matched nothing and
# read every block as clean (bench 2026-10-16 01:31)
CHG_RE = re.compile(r"chg\(rx=(\d+) tx=(\d+) err=(\d+) off=(\d+)")


# --- pure ------------------------------------------------------------------

def seq_of(data):
    if len(data) < 4:
        return None
    return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]


def unwrap_us(raw):
    import bench_innomaker_clock as IC
    return IC.unwrap_us(raw)


def dev_mapper(pairs):
    """pairs [(host_s, dev_s)] -> f(dev_s) -> host_s estimate, or None.
    Fit dev = a + b * host by least squares, then shift by the smallest
    (host - fitted host): the least-delayed echo is the truest pairing."""
    import bench_innomaker_clock as IC
    if len(pairs) < 20:
        return None
    b, a = IC._lsq([p[0] for p in pairs], [p[1] for p in pairs])
    if not b:
        return None
    shift = min(h - (d - a) / b for h, d in pairs)
    return lambda d: (d - a) / b + shift


def find_gaps(ts, thr):
    ts = sorted(ts)
    return [(x, y) for x, y in zip(ts, ts[1:]) if y - x > thr]


def gap_threshold(base_echo_t):
    gs = sorted(y - x for x, y in zip(base_echo_t, base_echo_t[1:]))
    if not gs:
        return None
    return max(GAP_MIN_S, 2.0 * gs[-1])


def boot_timeline(echo_t, sends, can1, mark, thr, release=None):
    """echo_t: host-mapped echo times; sends: [(host, ok)]; can1: PCAN rows
    [(host, id, err, data)]; mark: the reset (or hold) request's host time.
    -> dict with the window, counts, gaps and faults."""
    # The window is the BRIDGING GAP the reset made, closed by the boot's
    # power-up round. The board also sends 0x7F4-0x7F7 once a second while
    # charger frames flow, so "the first status frame after the mark" can
    # be a periodic one sent before the ESP went down (bench, 2026-10-16
    # 01:26: holds 2 and 3, where esptool took ~0.6 s to reset the chip).
    tl = dict(mark=mark, release=release, faults=[], t_round=None,
              t_last=None, t_resume=None, f7=None)
    br = sorted(r[0] for r in can1 if r[1] == STREAM_ID and not r[2])
    bg = [(x, y) for x, y in zip(br, br[1:])
          if y - x > MIN_WINDOW_S and x >= mark - 0.1]
    if not bg:
        tl["faults"].append("no gap in the bridged frames on CAN1 after the "
                            "reset request: no reset seen on CAN1")
        return tl
    t_last, t_res = bg[0]
    tl["t_last"], tl["t_resume"] = t_last, t_res
    if t_last > mark + 3.0:
        tl["faults"].append("bridging stopped %.1f s after the reset "
                            "request" % (t_last - mark))
    rnd = sorted((r for r in can1 if r[0] > t_last and r[1] in ROUND_IDS
                  and not r[2]), key=lambda r: r[0])
    t_round = rnd[0][0] if rnd else None
    tl["t_round"] = t_round
    f7 = [r for r in rnd if r[1] == 0x7F7]
    tl["f7"] = f7[0][3] if f7 else None
    if t_round is None or t_round > t_res + 0.05:
        tl["faults"].append("no power-up round at the end of the bridging "
                            "gap: no boot seen")
        return tl
    w0, w1 = t_last + LAT_S, t_round - LAT_S
    tl["w"] = (w0, w1)
    if w1 - w0 < MIN_WINDOW_S:
        tl["faults"].append("ESP-down window %.0f ms, under %.0f ms: no "
                            "reset seen on CAN1" % ((w1 - w0) * 1000,
                                                    MIN_WINDOW_S * 1000))
    tl["acked_w"] = sum(1 for t in echo_t if w0 <= t <= w1)
    tl["fails_w"] = sum(1 for t, ok in sends if w0 <= t <= w1 and not ok)
    tl["sends_w"] = sum(1 for t, _ok in sends if w0 <= t <= w1)
    lo = t_last - 0.05
    hi = (tl["t_resume"] or t_round) + 1.0
    near = [t for t in echo_t if lo <= t <= hi]
    gl = []
    for a, b in find_gaps(near, thr):
        att = [ok for t, ok in sends if a < t < b]
        gl.append(dict(a=a, b=b, sends=len(att),
                       fails=sum(1 for ok in att if not ok)))
    tl["gaps"] = gl
    tl["spans_w"] = any(g["a"] <= w0 and g["b"] >= w1 for g in gl)
    return tl


def build_of(f7):
    if f7 is None:
        return None
    import cantools
    import paths
    db = cantools.database.load_file(paths.diag_dbc())
    return db.get_message_by_frame_id(0x7F7).decode(bytes(f7))["intp_build_id"]


def judge_boot(tl, build=None):
    """-> (verdict, lines)."""
    lines = []
    rel = lambda t: "%+.1f ms" % ((t - tl["mark"]) * 1000.0)   # noqa: E731
    if build is not None and build != BUILD_EXPECT:
        tl["faults"].append("0x7F7 build 0x%08X, not 0x%08X"
                            % (build, BUILD_EXPECT))
    if tl["faults"]:
        return "TEST FAULT", ["TEST FAULT: " + f for f in tl["faults"]]
    w0, w1 = tl["w"]
    lines.append("ESP-down window (CAN1): last bridged frame %s, first "
                 "power-up-round frame %s; judged %s .. %s (%.0f ms)"
                 % (rel(tl["t_last"]), rel(tl["t_round"]), rel(w0), rel(w1),
                    (w1 - w0) * 1000))
    if tl.get("release") is not None:
        lines.append("release (hard reset into the app) requested %s"
                     % rel(tl["release"]))
    lines.append("in the window: %d echo(es) = ACKed frames, %d send "
                 "attempt(s), %d failed" % (tl["acked_w"], tl["sends_w"],
                                            tl["fails_w"]))
    if tl["t_resume"] is not None:
        lines.append("bridging resumed %s" % rel(tl["t_resume"]))
    for g in tl["gaps"]:
        lines.append("echo gap %s .. %s (%.1f ms): %d send attempt(s), %d "
                     "failed%s" % (rel(g["a"]), rel(g["b"]),
                                   (g["b"] - g["a"]) * 1000, g["sends"],
                                   g["fails"],
                                   " -- NO-ACK SHOWN" if g["fails"] else
                                   " -- not evidence (no failed send)"))
    if not tl["gaps"]:
        lines.append("no echo gap over the threshold near the reset")
    if tl["acked_w"] >= MIN_ACKED:
        return "ACKED WITH THE ESP DOWN", lines
    if tl["acked_w"] == 0 and tl["spans_w"]:
        return "NOT ACKED WITH THE ESP DOWN", lines
    return "UNCLEAR", lines


def judge_canfail(echoes, sends, fails):
    """The reviewer's ruling of 2026-10-16: the 0-echo criterion was wrong a
    priori (the adapter's queue and mailboxes); the resets stand on echoes
    against the measured unacknowledged ceiling (3, twice), the holds on
    failed sends. So the can-fail shows the two things the run's verdicts
    rest on: with nothing able to ACK, fewer echoes than MIN_ACKED (the
    reset judge's ACKED threshold), and sends that fail once the queue is
    full."""
    if sends == 0:
        return "TEST FAULT", "no frame was offered"
    if echoes >= MIN_ACKED:
        return "INSTRUMENT FAULT", ("%d echo(es) with nothing able to ACK: "
                                    "at or above MIN_ACKED (%d), so the "
                                    "reset judge cannot tell ACKed from not"
                                    % (echoes, MIN_ACKED))
    if fails == 0:
        return "INSTRUMENT FAULT", ("no send failed in %d attempt(s) with "
                                    "nothing able to ACK: a hold's 0 failed "
                                    "sends would mean nothing" % sends)
    return "CANFAIL-OK", ("%d echo(es) (below MIN_ACKED %d; the 2026-10-16 "
                          "ceiling was 3) and %d failed of %d send "
                          "attempt(s)" % (echoes, MIN_ACKED, fails, sends))


def chg_of(lines):
    """The last LOSS chg(...) fields in serial lines -> dict or None. No
    LOSS line means none dropped, no bus-off and no bus errors (the board
    prints LOSS only then), so None reads as clean."""
    last = None
    for ln in lines:
        m = CHG_RE.search(ln)
        if m:
            last = dict(zip(("rx", "tx", "err", "off"),
                            (int(x) for x in m.groups())))
    return last


def judge_normal(echoes, sent, before, after):
    lines = ["%d of %d frames echoed (ACKed); LOSS chg before %s, after %s"
             % (echoes, sent, before, after)]
    if sent == 0:
        return "TEST FAULT", lines + ["no frame offered"]
    bad = []
    if echoes != sent:
        bad.append("%d frame(s) not echoed" % (sent - echoes))
    b_err = before["err"] if before else 0
    a_err = after["err"] if after else 0
    if a_err > b_err:
        bad.append("chg err +%d during the stream" % (a_err - b_err))
    if after and after["off"] > (before["off"] if before else 0):
        bad.append("chg bus-off count rose")
    if bad:
        return "NOT NORMAL", lines + bad
    return "NORMAL", lines


# --- hardware --------------------------------------------------------------

def open_innomaker(bitrate):
    import can
    import libusb_package
    d = os.path.dirname(libusb_package.get_library_path())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    bus = can.Bus(interface="gs_usb", channel="can0", index=0,
                  bitrate=bitrate)
    from gs_usb.constants import GS_CAN_MODE_HW_TIMESTAMP as HW
    flags = getattr(getattr(bus, "gs_usb", None), "device_flags", None)
    return bus, bool(flags is not None and flags & HW)


class Echoes(object):
    """Our frames coming back: (host, raw device us, seq)."""

    def __init__(self, bus):
        self.bus, self.rows, self.other, self.err = bus, [], 0, 0
        self.stop_ev = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self.stop_ev.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except Exception:                              # noqa: BLE001
                continue
            host = time.perf_counter()
            if m is None:
                continue
            if m.is_error_frame:
                self.err += 1
            elif m.arbitration_id == STREAM_ID and not m.is_rx:
                self.rows.append((host, int(round(m.timestamp * 1e6)),
                                  seq_of(m.data)))
            else:
                self.other += 1

    def stop(self):
        self.stop_ev.set()
        self.t.join(3.0)


class Sender(object):
    """STREAM_ID at `rate`, the schedule kept (bench_pacer's rule: nxt +=
    interval, never reset), every attempt's host time and outcome kept."""

    def __init__(self, bus, rate):
        self.bus, self.interval = bus, 1.0 / rate
        self.log, self.max_lag = [], 0.0
        self.stop_ev = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        import can
        seq = 0
        nxt = time.perf_counter()
        while not self.stop_ev.is_set():
            now = time.perf_counter()
            self.max_lag = max(self.max_lag, now - nxt)
            msg = can.Message(arbitration_id=STREAM_ID, is_extended_id=True,
                              data=bytearray((seq >> 24 & 255, seq >> 16 & 255,
                                              seq >> 8 & 255, seq & 255,
                                              0, 0, 0, 0)))
            try:
                self.bus.send(msg, timeout=0.05)
                self.log.append((time.perf_counter(), seq, True))
            except Exception:                              # noqa: BLE001
                self.log.append((time.perf_counter(), seq, False))
            seq += 1
            nxt += self.interval
            while True:
                slack = nxt - time.perf_counter()
                if slack <= 0:
                    break
                if slack > 0.002:
                    time.sleep(slack - 0.001)

    def stop(self):
        self.stop_ev.set()
        self.t.join(3.0)


def esptool(port, before, after, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    cmd = [sys.executable, ESPTOOL, "--chip", "esp32s3", "--port", port,
           "--before", before, "--after", after] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, p.stdout + p.stderr


def run_bench(a):
    import bench_usb_host as U
    import run_scenario as R
    base = os.path.splitext(a.out)[0]
    print("run at %s; board image expected 0x%08X; stream 0x%08X at %.0f fps "
          "on the charger segment (Innomaker), PCAN acknowledging on CAN1; "
          "resets %d, holds %d x %.1f s"
          % (datetime.datetime.now().isoformat(), BUILD_EXPECT, STREAM_ID,
             RATE, a.resets, a.holds, a.hold_s))
    inno, hw = open_innomaker(500000)
    if not hw:
        print("REFUSED: the Innomaker is not in hardware-timestamp mode")
        inno.shutdown()
        return 2
    pc = U.Rx(U.open_pcan(True), "pcan")
    tap = R.SerialTap(a.port, 115200, base + "_serial.txt")
    tap.start()
    time.sleep(SETTLE_S)
    ech = Echoes(inno)
    snd = Sender(inno, RATE)
    marks = []
    t_base0 = time.perf_counter()
    time.sleep(BASELINE_S)
    t_base1 = time.perf_counter()
    try:
        for i in range(a.resets):
            prev = tap.reset_host_t
            tap.reset_board()
            end = time.perf_counter() + 5.0
            while tap.reset_host_t == prev and time.perf_counter() < end:
                time.sleep(0.01)
            if tap.reset_host_t == prev:
                print("TEST FAULT: reset %d was not sent" % (i + 1))
                continue
            marks.append(("reset %d" % (i + 1), tap.reset_host_t, None, ""))
            time.sleep(SETTLE_S)
        tap.stop()
        time.sleep(SETTLE_S)            # a COM3 close can reset the board
        for j in range(a.holds):
            m = time.perf_counter()
            rc, txt = esptool(a.port, "default_reset", "no_reset",
                              "read-mem", "0x6000403C")
            time.sleep(a.hold_s)
            rel = time.perf_counter()
            rc2, txt2 = esptool(a.port, "no_reset", "hard_reset",
                                "read-mem", "0x6000403C")
            note = "esptool hold rc %d, release rc %d" % (rc, rc2)
            if rc or rc2:
                note += " | " + " / ".join(
                    ln for ln in (txt + txt2).splitlines()
                    if "rror" in ln)[-300:]
            marks.append(("hold %d" % (j + 1), m, rel, note))
            time.sleep(SETTLE_S + 1.0)
        time.sleep(2.0)
    finally:
        snd.stop()
        time.sleep(1.5)
        ech.stop()
        rows = pc.take() if hasattr(pc, "take") else list(pc.rows)
        pc.stop_ev.set()
        pc.t.join(2.0)
        try:
            pc.bus.shutdown()
        except Exception:                                  # noqa: BLE001
            pass
        inno.shutdown()
    dev = unwrap_us([r[1] for r in ech.rows])
    f = dev_mapper([(r[0], d) for r, d in zip(ech.rows, dev)])
    if f is None:
        print("TEST FAULT: too few echoes to map the Innomaker's clock (%d)"
              % len(ech.rows))
        return 2
    echo_t = sorted(f(d) for d in dev)
    sends = [(t, ok) for t, _s, ok in snd.log]
    can1 = [(r[1], r[2], r[3], r[4]) for r in rows]
    thr = gap_threshold([t for t in echo_t if t_base0 <= t <= t_base1])
    with open(base + "_marks.txt", "w") as fh:
        for name, m, rel, note in marks:
            fh.write("%s\t%.6f\t%s\t%s\n" % (name, m, "" if rel is None
                                             else "%.6f" % rel, note))
    with open(base + "_echo.txt", "w") as fh:
        fh.write("# host_read raw_dev_us seq host_mapped\n")
        for r, d in zip(ech.rows, dev):
            fh.write("%.6f %d %s %.6f\n" % (r[0], r[1], r[2], f(d)))
    with open(base + "_sends.txt", "w") as fh:
        for t, s, ok in snd.log:
            fh.write("%.6f %d %d\n" % (t, s, 1 if ok else 0))
    with open(base + "_pcan.txt", "w") as fh:
        for r in rows:
            fh.write("%.6f %.6f %X %d %s\n" % (r[0], r[1], r[2], int(r[3]),
                                               r[4].hex()))
    print("baseline %.1f s: %d echoes of %d sends; echo-gap threshold %s; "
          "sender max lag %.1f ms; Innomaker error frames %d, other rx %d"
          % (BASELINE_S, sum(1 for t in echo_t if t_base0 <= t <= t_base1),
             sum(1 for t, _ok in sends if t_base0 <= t <= t_base1),
             "%.1f ms" % (thr * 1000) if thr else "NONE", snd.max_lag * 1000,
             ech.err, ech.other))
    if thr is None:
        print("TEST FAULT: no baseline echoes")
        return 2
    tally = {}
    noack_seen = False
    for name, m, rel, note in marks:
        tl = boot_timeline(echo_t, sends, can1, m, thr, rel)
        try:
            build = build_of(tl.get("f7"))
        except Exception as e:                             # noqa: BLE001
            build = None
            tl["faults"].append("0x7F7 not decoded (%s)" % e)
        v, lines = judge_boot(tl, build)
        tally[v] = tally.get(v, 0) + 1
        noack_seen = noack_seen or any(g["fails"] for g in tl.get("gaps", []))
        print("\n%s: %s%s%s" % (name, v, (" (0x7F7 build 0x%08X)" % build)
                                if build is not None else "",
                                (" [%s]" % note) if note else ""))
        for ln in lines:
            print("    " + ln)
    print("\nRESULT: %s; a no-ACK interval (echo gap with failed sends) "
          "%s in this run" % (", ".join("%s %d" % kv for kv in
                                        sorted(tally.items())),
                              "WAS SHOWN" if noack_seen else
                              "was NOT shown -- run canfail"))
    return 0


def first_fail(log):
    """log: [(host, seq, ok)] in send order -> (k, accepted, ms) for the
    first failed send: it was attempt k (1-based), the driver accepted
    `accepted` before it, ms after the first send; None if none failed.
    A figure, not judged (reviewer, 2026-10-22): with nothing able to ACK,
    the sends taken before the first failure are what the adapter's queue
    and mailboxes held."""
    for i, (t, _sq, ok) in enumerate(log):
        if not ok:
            return (i + 1, sum(1 for x in log[:i] if x[2]),
                    (t - log[0][0]) * 1000.0)
    return None


def run_canfail(a):
    print("canfail at %s: the Innomaker at %d bit/s against the board's "
          "500 kbit/s for %.0f s" % (datetime.datetime.now().isoformat(),
                                     a.bitrate, CANFAIL_S))
    inno, hw = open_innomaker(a.bitrate)
    ech = Echoes(inno)
    snd = Sender(inno, RATE)
    time.sleep(CANFAIL_S)
    snd.stop()
    time.sleep(1.0)
    ech.stop()
    inno.shutdown()
    fails = sum(1 for x in snd.log if not x[2])
    base = os.path.splitext(a.out)[0]
    with open(base + "_sends.txt", "w") as fh:
        for t, sq, ok in snd.log:
            fh.write("%.6f %d %d\n" % (t, sq, 1 if ok else 0))
    t0 = snd.log[0][0] if snd.log else 0.0
    print("%d send attempt(s): %d accepted by the driver, %d failed; %d "
          "echo(es)" % (len(snd.log), len(snd.log) - fails, fails,
                        len(ech.rows)))
    ff = first_fail(snd.log)
    print("FIGURE first failed send: %s" % (
        "attempt %d, %d accepted before it, +%.1f ms after the first send"
        % ff if ff else "none"))
    for host, raw, sq in ech.rows:
        print("  echo seq %s, read +%.1f ms after the first send, device "
              "stamp %d us" % (sq, (host - t0) * 1000.0, raw))
    v, line = judge_canfail(len(ech.rows), len(snd.log), fails)
    print("RESULT: %s -- %s; Innomaker error frames %d, other rx %d"
          % (v, line, ech.err, ech.other))
    return 0 if v == "CANFAIL-OK" else 1


def run_normal(a):
    import bench_usb_host as U
    import run_scenario as R
    base = os.path.splitext(a.out)[0]
    print("normal at %s: 500 kbit/s stream at %.0f fps for %.0f s, PCAN "
          "acknowledging on CAN1" % (datetime.datetime.now().isoformat(),
                                     NORMAL_RATE, NORMAL_S))
    inno, hw = open_innomaker(500000)
    pc = U.Rx(U.open_pcan(True), "pcan")
    tap = R.SerialTap(a.port, 115200, base + "_serial.txt")
    tap.start()
    time.sleep(SETTLE_S + 2.0)          # one status block before
    with open(base + "_serial.txt", encoding="utf-8", errors="replace") as fh:
        before = chg_of(fh.read().splitlines())
    ech = Echoes(inno)
    snd = Sender(inno, NORMAL_RATE)
    time.sleep(NORMAL_S)
    snd.stop()
    time.sleep(6.0)                     # one status block after
    ech.stop()
    with open(base + "_serial.txt", encoding="utf-8", errors="replace") as fh:
        after = chg_of(fh.read().splitlines())
    tap.stop()
    pc.stop_ev.set()
    pc.t.join(2.0)
    pc.bus.shutdown()
    inno.shutdown()
    sent = sum(1 for x in snd.log if x[2])
    v, lines = judge_normal(len(ech.rows), sent, before, after)
    for ln in lines:
        print("    " + ln)
    print("RESULT: %s (Innomaker error frames %d)" % (v, ech.err))
    return 0 if v == "NORMAL" else 1


# --- self-test -------------------------------------------------------------

def self_test():
    fails = []

    def check(c, what):
        print("  %-5s %s" % ("ok" if c else "FAIL", what))
        if not c:
            fails.append(what)

    def scene(echo_stop=None, echo_resume=None, bridge_stop=0.005,
              round_at=0.40, begin_gap=None, fail_in_gap=True, periodic=()):
        mark = 100.0
        echo, sends, can1 = [], [], []
        t = mark - 3.0
        while t < mark + max(3.0, (round_at or 0.0) + 1.0):
            sends_ok = True
            acked = True
            if echo_stop is not None and mark + echo_stop <= t < \
                    mark + echo_resume:
                acked = False
            if begin_gap and mark + begin_gap[0] <= t < mark + begin_gap[1]:
                acked = False
            if acked:
                echo.append(t + 0.0003)
            else:
                sends_ok = not fail_in_gap
            sends.append((t, sends_ok))
            if bridge_stop is None or t < mark + bridge_stop or \
                    (round_at is not None and t > mark + round_at + 0.05):
                can1.append((t + 0.0005, STREAM_ID, False, b"\0" * 8))
            t += 0.002
        if round_at is not None:
            for k, i in enumerate(ROUND_IDS):
                can1.append((mark + round_at + k * 0.0002, i, False,
                             bytes.fromhex("14676d7509000400")))
        for k in periodic:
            for i in ROUND_IDS:
                can1.append((mark + k, i, False,
                             bytes.fromhex("14676d7509000400")))
        return echo, sends, can1, mark

    e, s, c, m = scene()
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "ACKED WITH THE ESP DOWN", "echoes all through the ESP-down "
          "window -> ACKED -> %s" % v)
    e, s, c, m = scene(echo_stop=0.003, echo_resume=0.45)
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "NOT ACKED WITH THE ESP DOWN" and
          any("NO-ACK SHOWN" in x for x in ln),
          "can-fail: echoes stop at the reset until after the round -> NOT "
          "ACKED, the gap shown with failed sends -> %s" % v)
    e, s, c, m = scene(begin_gap=(0.30, 0.32))
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "ACKED WITH THE ESP DOWN" and
          any("NO-ACK SHOWN" in x and "+300" in x for x in ln),
          "a 20 ms no-ACK gap at begin() inside the window: ACKED, and the "
          "gap listed at +300 ms")
    e, s, c, m = scene(begin_gap=(0.30, 0.32), fail_in_gap=True)
    s2 = [(t, ok) for t, ok in s if not (m + 0.30 <= t < m + 0.32)]
    tl = boot_timeline(e, s2, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(any("not evidence" in x for x in ln),
          "an echo gap with no send attempt (the sender paused) is not "
          "evidence")
    e, s, c, m = scene(bridge_stop=None)
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "TEST FAULT", "can-fail: bridging never stopped (no reset "
          "seen on CAN1) -> TEST FAULT -> %s" % v)
    e, s, c, m = scene(bridge_stop=0.6, round_at=6.7,
                       periodic=(-0.8, 0.2, 7.7))
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "ACKED WITH THE ESP DOWN" and abs(tl["t_round"] - m - 6.7)
          < 0.01, "can-fail of the 01:26 run's defect: a periodic status "
          "frame between the hold request and the ESP going down is not "
          "taken for the boot round -> %s" % v)
    e, s, c, m = scene(round_at=None)
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v == "TEST FAULT", "no power-up round after the reset -> TEST "
          "FAULT")
    e, s, c, m = scene()
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, 0x12345678)
    check(v == "TEST FAULT", "a wrong 0x7F7 build -> TEST FAULT")
    e, s, c, m = scene(echo_stop=0.003, echo_resume=0.2)
    tl = boot_timeline(e, s, c, m, 0.010)
    v, ln = judge_boot(tl, BUILD_EXPECT)
    check(v in ("ACKED WITH THE ESP DOWN", "UNCLEAR"),
          "echoes return part-way through the window -> not NOT ACKED -> %s"
          % v)
    try:
        b = build_of(bytes.fromhex("14676d7509000400"))
        check(b == BUILD_EXPECT, "0x7F7 14676d7509000400 decodes (cantools) "
              "to 0x756D6714")
    except Exception as ex:                                # noqa: BLE001
        check(False, "0x7F7 decode: %s" % ex)
    pairs = [(10.0 + i * 0.01 + (0.004 if i % 3 else 0.0),
              5000.0 + i * 0.01 * (1 + 40e-6)) for i in range(500)]
    fm = dev_mapper(pairs)
    err = max(abs(fm(5000.0 + i * 0.01 * (1 + 40e-6)) - (10.0 + i * 0.01))
              for i in range(500))
    check(err < 0.0005, "clock map: 40 ppm and 4 ms USB delay on 2 of 3 "
          "echoes -> within 0.5 ms (%.3f ms)" % (err * 1000))
    check(dev_mapper(pairs[:5]) is None, "clock map refuses under 20 echoes")
    lg = [(1.0 + i * 0.001, i, ok) for i, ok in enumerate(
        [True] * 5 + [False] + [True] * 3 + [False])]
    ff = first_fail(lg)
    check(ff is not None and ff[:2] == (6, 5) and abs(ff[2] - 5.0) < 1e-6,
          "first failed send: attempt 6 after 5 accepted, +5 ms, not the "
          "later failure -> %s" % (ff,))
    lg2 = [(1.0, 0, True), (1.001, 1, False), (1.002, 2, False)]
    check(first_fail(lg2)[:2] == (2, 1), "first failed send: attempt 2, one "
          "accepted before it -> %s" % (first_fail(lg2),))
    check(first_fail([(1.0, 0, True)] * 4) is None,
          "first failed send: none failed -> None")
    check(judge_canfail(0, 900, 880)[0] == "CANFAIL-OK", "canfail: 0 echoes "
          "-> OK")
    check(judge_canfail(3, 261, 2)[0] == "CANFAIL-OK", "canfail: the "
          "2026-10-16 / a25e measurement (3 echoes, 2 of 261 failed) -> OK "
          "under the reviewer's ruling")
    check(judge_canfail(MIN_ACKED - 1, 261, 2)[0] == "CANFAIL-OK",
          "canfail: one below MIN_ACKED -> OK")
    check(judge_canfail(MIN_ACKED, 261, 2)[0] == "INSTRUMENT FAULT",
          "canfail: MIN_ACKED echoes -> INSTRUMENT FAULT (the reset judge "
          "could not separate)")
    check(judge_canfail(12, 900, 0)[0] == "INSTRUMENT FAULT", "canfail: "
          "echoes without an acknowledger -> INSTRUMENT FAULT")
    check(judge_canfail(3, 261, 0)[0] == "INSTRUMENT FAULT", "canfail: no "
          "failed send -> INSTRUMENT FAULT (the holds rest on failed sends)")
    check(judge_canfail(0, 0, 0)[0] == "TEST FAULT", "canfail: nothing "
          "offered -> TEST FAULT")
    lines = ["t=5 s", "  LOSS veh(rx=0 tx=0 err=0 off=0 peak=0) chg(rx=2 tx=0 "
             "err=12 off=0 ring=0/0) bridge=0 emit=0"]
    c0 = chg_of(lines)
    check(c0 is not None and c0["err"] == 12, "can-fail of the 01:31 run's "
          "defect: 756d6714's real LOSS line (chg ... ring=0/0) parses, err "
          "12")
    check(chg_of(["t=5 s", "MODE ..."]) is None, "no LOSS line -> None "
          "(clean)")
    check(judge_normal(600, 600, c0, c0)[0] == "NORMAL", "normal: all echoed, "
          "chg err unchanged -> NORMAL")
    c1 = dict(c0, err=40)
    check(judge_normal(600, 600, c0, c1)[0] == "NOT NORMAL", "can-fail: chg "
          "err rose during the stream -> NOT NORMAL")
    check(judge_normal(590, 600, None, None)[0] == "NOT NORMAL", "can-fail: "
          "frames not echoed -> NOT NORMAL")
    check(judge_normal(600, 600, None, dict(c0, off=1))[0] == "NOT NORMAL",
          "can-fail: chg bus-off rose -> NOT NORMAL")
    print("\nbench_mcp_reset self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the 0-echo criterion (pre-ruling)",
     "    if echoes >= MIN_ACKED:", "    if echoes:"),
    ("no echo bound",
     "    if echoes >= MIN_ACKED:", "    if False:"),
    ("bound one too high",
     "    if echoes >= MIN_ACKED:", "    if echoes > MIN_ACKED:"),
    ("failed sends not required",
     "    if fails == 0:\n        return \"INSTRUMENT FAULT\"",
     "    if False:\n        return \"INSTRUMENT FAULT\""),
    ("first failure: 0-based attempt",
     "            return (i + 1, sum(", "            return (i, sum("),
    ("first failure: the last one, not the first",
     "        if not ok:\n            return (i + 1,",
     "        if not ok and not any(not x[2] for x in log[i + 1:]):\n"
     "            return (i + 1,"),
    # 2026-10-24: this replaced "every attempt before it counted as
    # accepted" (sum(...) -> i), an equivalent mutant: first_fail returns
    # at the first failed send, so every send before it was accepted and the
    # two are always equal. It read as caught only because the DBC path,
    # relative to the reverse-it root, failed the 0x7F7 decode check under
    # every mutant (script_can_fail now runs a baseline first).
    ("first failure: ms from the send before it, not the first send",
     "(t - log[0][0]) * 1000.0", "(t - log[i - 1][0]) * 1000.0"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("cmd", nargs="?", choices=["run", "canfail", "normal"])
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--resets", type=int, default=5)
    ap.add_argument("--holds", type=int, default=3)
    ap.add_argument("--hold-s", type=float, default=5.0)
    ap.add_argument("--bitrate", type=int, default=250000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not a.cmd or not a.out:
        ap.error("a command and --out are required for a bench run")
    import bench_charger_witness as bcw
    tee = bcw.Tee(a.out)
    sys.stdout = tee
    try:
        rc = {"run": run_bench, "canfail": run_canfail,
              "normal": run_normal}[a.cmd](a)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("saved verbatim to %s" % a.out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
