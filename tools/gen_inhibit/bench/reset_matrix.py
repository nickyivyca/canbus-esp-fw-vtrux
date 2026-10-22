"""WiCAN reset matrix on the diag image: what resets the WiCAN, and how.

Gen-inhibit tester, 2026-10-08, the reviewer's GO on the reset matrix. With the
X2 jammer image on the X2, every WiCAN arm reset the WiCAN (evidence doc
section 7); with 7f197bfd on it, arms held. The diag image (b7c65a7, off
a71b90a, NOT FOR VEHICLE) prints, by esp_rom_printf so no log level silences
it:
    GI_DIAG: DIAG BUILD, NOT FOR VEHICLE. reset reason <NAME> (<n>)    ~280 ms
    GI_DIAG: up <n> s, uptime <ms> ms, last reset <NAME> (<n>)         1/s, 30 s
and on a panic halts with a register dump (PANIC_PRINT_HALT); after that, a
USB-JTAG reset reads USB (11), not PANIC, so the dump is the only record.

Cases (one session each, COM4 captured for the whole session):
  baseline  device idle in mode 0 for --seconds, page polled every 2 s. Start
            it BEFORE the OTA that boots the image, so that boot is captured.
  A         mode 3, no 0x051, NO node ACKs: the witness is the PCAN in
            listen-only (state=PASSIVE), the Kvaser is not opened.
  B         mode 2, otherwise as A.
  C         mode 3, Kvaser NORMAL (ACKs the WiCAN's frames).
  D         mode 3 armed into the PCAN replaying the interlock slice
            (lowid_flood_skip_bench stimulus); the Kvaser witnesses and ACKs.

THE KVASER CANNOT BE SILENT ON THIS BENCH (2026-10-09). The Leaf Light v2's
driver reports no silent-mode capability (channel caps 0x000000C1), and opened
with driver_mode=False -- with or without single_handle=True -- it still ACKs:
the WiCAN's TEC stayed 0 with it open and reached 128 with no adapter open
(runs/ack_source_probe_*). The first run of case A/B (reset_matrix_A_
20261009T042000, _B_20261009T042311) opened it "silent", so in those arms the
Kvaser ACKed. The PCAN in listen-only did not ACK (TEC 128), so it is the
silent witness. witness_plan() holds the choice; the test pins it.
  E         as A, with the X2's console (COM3) held open throughout.

A BOOT is read from the console only, by three independent signs, any of which
counts: the diag reason line, a ROM banner line ("ESP-ROM" / "rst:0x"), or the
uptime counter going backwards. A RESET while armed is read from the page polls
(reset_rate_arms.find_reset: mode 0 nobody posted, drv null, tx_ok falling).
Port drops are recorded and never counted: a USB-JTAG reset leaves COM4 up
(console_selftest_20261008T000900), and drops do not map 1:1 to boots
(implementor, 2026-10-08). A quiet console carries nothing either way.

A HALT is a panic dump line ("Guru Meditation", "abort()", "Backtrace",
"Core  0 register dump") not followed by a boot within --halt-wait seconds.
The harness then pulses RTS (console_capture.pulse_rts, the USB-JTAG reset)
and records that it did. Backtraces are decoded afterwards, by hand, with the
ELF; the harness keeps the raw lines.

The Wi-Fi: after a device reset the laptop drops off the SoftAP. If the page
has not answered for --rejoin-after s, the harness runs
`netsh wlan connect name=$GI_WICAN_SSID` (the SSID is not in this file; it is
in firmware/bench-holder.md).

  py -3.14 .../reset_matrix.py --case A --arms 3 --hold 20
  py -3.14 .../reset_matrix.py --case baseline --seconds 330
Writes runs/reset_matrix_<case>_<stamp>.json and ..._com4.txt (and ..._com3.txt
for E). Never overwrites. Always posts mode 0 at the end.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from console_capture import ConsoleCapture                      # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

RUNS = gi_paths.runs()
BITRATE = 500000
SSID_ENV = "GI_WICAN_SSID"

REASON_RE = re.compile(r"GI_DIAG: DIAG BUILD, NOT FOR VEHICLE\. reset reason "
                       r"(\w+) \((\d+)\)")
UPTIME_RE = re.compile(r"GI_DIAG: up (\d+) s, uptime (\d+) ms, last reset "
                       r"(\w+) \((\d+)\)")
ROM_RE = re.compile(r"ESP-ROM:|^rst:0x[0-9a-fA-F]+")
RST_RE = re.compile(r"rst:0x([0-9a-fA-F]+) \(([A-Z_]+)\)")
# An ESP-IDF log line's tick, "I (571) phy_init: ...", colour codes allowed.
TICK_RE = re.compile(r"^(?:\x1b\[[0-9;]*m)?[EWIDV] \((\d+)\) ")
TICK_ANY_RE = re.compile(r"[EWIDV] \((\d+)\) ")
TICK_BACK_MS = 100
DUMP_RE = re.compile(r"Guru Meditation|abort\(\) was called|Backtrace|"
                     r"register dump|Stack memory", re.I)
WICAN_PAGES = (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
X2_PAGES = (0x7F4, 0x7F5, 0x7F6, 0x7F7)


# ------------------------------------------------------------ console reading --
def boots(lines):
    """Boots seen on the console: [{t, how, reason, reason_n, rst, up_last}].

    lines: [(t, text)] in time order. A boot shows as up to three signs in
    order: ROM banner lines ("ESP-ROM:", "rst:0x.."), the diag reason line,
    then uptime lines counting 1, 2, ...; and every ESP-IDF log line carries
    a millisecond tick since boot. A NEW boot starts on:
      - a ROM line once the current boot is past its ROM stage (it has a
        reason, uptime or log-tick line), or a second "ESP-ROM:" line;
      - a reason line when the current boot already has one;
      - an uptime count lower than the previous one;
      - a log tick more than TICK_BACK_MS below the previous one.
    Any one sign alone is enough, so a boot whose ROM lines were lost (port
    down) or whose reason line was lost is still counted. The log-tick sign is
    the only one an image without the diag lines (a71b90a) gives when its ROM
    banner is lost: added 2026-10-09 after the baseline transcript
    reset_matrix_baseline_20261009T000120 showed boots seen only that way.
    """
    out = []
    cur = None
    for t, x in lines:
        rom = ROM_RE.search(x)
        rea = REASON_RE.search(x)
        up = UPTIME_RE.search(x)
        tk = TICK_RE.search(x)
        tick = int(tk.group(1)) if tk else None
        if not (rom or rea or up or tk):
            continue
        if cur is None and not (rom or rea) and (tk or int(up.group(1)) > 1):
            # Log lines, or an uptime line past "up 1 s", from an image already
            # running when the capture opened (or left in the console's buffer
            # from an earlier image: 2026-10-09 04:03 the first read held a
            # GI_DIAG "up 26 s" line on a71b90a, which prints none): follow it,
            # but that is not a boot we saw.
            cur = {"t": t, "how": [], "reason": None, "reason_n": None,
                   "rst": None, "up_last": None, "esp_rom": False,
                   "reason_line": False,
                   "tick_last": (max([int(v) for v in TICK_ANY_RE.findall(x)] or [tick])
                                 if tk else None)}
            if up:
                cur["up_last"] = int(up.group(1))
            continue
        if cur is None:
            new = True
        elif rom:
            new = (cur["reason_line"] or cur["up_last"] is not None or
                   cur["tick_last"] is not None or
                   (x.startswith("ESP-ROM") and cur["esp_rom"]))
        elif rea:
            new = cur["reason_line"]
        elif up:
            new = cur["up_last"] is not None and int(up.group(1)) < cur["up_last"]
        else:
            new = (cur["tick_last"] is not None and
                   tick < cur["tick_last"] - TICK_BACK_MS)
        if new:
            cur = {"t": t, "how": [], "reason": None, "reason_n": None,
                   "rst": None, "up_last": None, "esp_rom": False,
                   "reason_line": False, "tick_last": None}
            out.append(cur)
        if rom:
            if "rom" not in cur["how"]:
                cur["how"].append("rom")
            if x.startswith("ESP-ROM"):
                cur["esp_rom"] = True
            m = RST_RE.search(x)
            if m:
                cur["rst"] = "0x%s %s" % (m.group(1).lower(), m.group(2))
        if rea:
            cur["reason_line"] = True
            cur["reason"], cur["reason_n"] = rea.group(1), int(rea.group(2))
            cur["how"].append("reason")
        if up:
            if cur["up_last"] is None:
                cur["how"].append("uptime")
                if cur["reason"] is None:
                    cur["reason"], cur["reason_n"] = up.group(3), int(up.group(4))
            cur["up_last"] = int(up.group(1))
        if tk:
            if not cur["how"]:
                cur["how"].append("tick")
            # A line can hold two boots' text: the capture keeps a partial
            # line across a port drop, so the next boot's output is appended
            # to it. Every tick in the line moves tick_last on.
            ticks = [int(v) for v in TICK_ANY_RE.findall(x)] or [tick]
            cur["tick_last"] = max(ticks + [cur["tick_last"] or 0])
    for b in out:
        del b["esp_rom"], b["reason_line"]
    return out


def load_transcript(path):
    """[(t, text)] back from a ..._com4.txt this tool wrote."""
    out = []
    for raw in open(path, encoding="ascii", errors="replace"):
        raw = raw.rstrip("\n")
        head, _, text = raw.lstrip().partition(" ")
        try:
            out.append((float(head), text))
        except ValueError:
            pass
    return out


def dumps(lines):
    """Times of panic-dump lines."""
    return [t for t, x in lines if DUMP_RE.search(x)]


# ------------------------------------------------------------------- device --
def _lf():
    import lowid_flood_skip_bench as LF
    return LF


def snap(p):
    if p is None:
        return None
    drv = p.get("drv") or {}
    return {"mode": p.get("mode"), "bus_on": p.get("bus_on"),
            "inhibit_live": p.get("inhibit_live"), "arm_block": p.get("arm_block"),
            "tx_ok": p.get("tx_ok"), "tx_fail": p.get("tx_fail"),
            "rx_errors": p.get("rx_errors"), "abort_reason": p.get("abort_reason"),
            "drv_null": p.get("drv") is None, "drv_state": drv.get("state"),
            "drv_tec": drv.get("tec"), "drv_rec": drv.get("rec"),
            "drv_bus_error": drv.get("bus_error")}


def rejoin():
    ssid = os.environ.get(SSID_ENV)
    if not ssid:
        return "no %s set" % SSID_ENV
    r = subprocess.run(["netsh", "wlan", "connect", "name=%s" % ssid],
                       capture_output=True, text=True, timeout=10)
    return "netsh rc %d" % r.returncode


class Witness(threading.Thread):
    """Counts frames per id, error frames, and times of WiCAN diag pages."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.ids = {}
        self.errors = []
        self.wican = []

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                t = time.perf_counter()
                with self.lock:
                    if m.is_error_frame:
                        self.errors.append(t)
                    else:
                        i = m.arbitration_id
                        self.ids[i] = self.ids.get(i, 0) + 1
                        if i in WICAN_PAGES:
                            self.wican.append(t)
                m = self.bus.recv(timeout=0.0)

    def window(self, a, b):
        with self.lock:
            return {"error_frames": sum(1 for t in self.errors if a <= t <= b),
                    "wican_pages": sum(1 for t in self.wican if a <= t <= b),
                    "first_wican_page_rel_s": next(
                        (round(t - a, 3) for t in self.wican if a <= t <= b), None)}


# --------------------------------------------------------------------- runs --
def poll_until(t_end, samples, period, state):
    """Poll the page until t_end; rejoin the SoftAP after a long silence."""
    LF = _lf()
    while time.perf_counter() < t_end:
        s = snap(LF.page())
        now = time.perf_counter()
        samples.append((now, s))
        if s is None:
            if state["silent_since"] is None:
                state["silent_since"] = now
            elif now - state["silent_since"] > state["rejoin_after"] and \
                    now - state["last_rejoin"] > 8.0:
                state["rejoins"].append((now, rejoin()))
                state["last_rejoin"] = now
        else:
            state["silent_since"] = None
        time.sleep(period)


def halt_check(cc, t_from, halt_wait, rec_list):
    """A dump with no boot after it within halt_wait s -> pulse RTS once."""
    with cc.lock:
        lines = [(t, x) for t, x in cc.lines if t >= t_from]
    d = dumps(lines)
    if not d:
        return False
    if time.perf_counter() - d[0] < halt_wait:
        return False
    if any(b["t"] > d[0] for b in boots(lines)):
        return False
    rec_list.append({"t": time.perf_counter(), "dump_first_t": d[0],
                     "pulsed": cc.pulse_rts(0.2)})
    return True


def wait_ready(t_max, state, samples):
    """Until the page answers mode 0 (after a reset or our own post)."""
    LF = _lf()
    t_end = time.perf_counter() + t_max
    while time.perf_counter() < t_end:
        s = snap(LF.page())
        samples.append((time.perf_counter(), s))
        if s is not None and s.get("mode") == 0:
            return True
        if s is None and time.perf_counter() - state["last_rejoin"] > 8.0:
            state["rejoins"].append((time.perf_counter(), rejoin()))
            state["last_rejoin"] = time.perf_counter()
        time.sleep(0.5)
    return False


def witness_plan(case, kvaser_acks=False):
    """Which adapter witnesses, whether any node ACKs, and who replays.

    The Kvaser is only ever opened in NORMAL mode (it cannot be silent here,
    see the module docstring); a case that needs nobody to ACK uses the PCAN
    in listen-only and leaves the Kvaser closed.

    kvaser_acks (A/B only): the Kvaser witnesses and ACKs instead -- the
    condition the first A/B run (reset_matrix_A_20261009T042000,
    _B_20261009T042311) actually had, so that run can be repeated as it was."""
    if kvaser_acks and case in ("A", "B"):
        return {"witness": "kvaser", "witness_mode": "normal", "acker": "kvaser",
                "replay": None}
    if kvaser_acks:
        raise ValueError("--kvaser-acks applies to cases A and B only")
    if case in ("A", "B", "E"):
        return {"witness": "pcan", "witness_mode": "listen-only", "acker": None,
                "replay": None}
    if case == "C":
        return {"witness": "kvaser", "witness_mode": "normal", "acker": "kvaser",
                "replay": None}
    if case == "D":
        return {"witness": "kvaser", "witness_mode": "normal",
                "acker": "kvaser and pcan", "replay": "pcan"}
    raise ValueError(case)


def rel(t, t0):
    return None if t is None else round(t - t0, 3)


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--power", required=True,
                    help="how the WiCAN is powered for this run, recorded in the "
                         "run file (e.g. 'USB only (PC)', '12 V on OBD pin 16 + "
                         "USB connected'); required, since the rig's supply changes")
    ap.add_argument("--kvaser-acks", action="store_true",
                    help="cases A/B: the Kvaser (normal mode, ACKing) witnesses "
                         "instead of the listen-only PCAN, as in the first A/B run")
    ap.add_argument("--case", required=True,
                    choices=("baseline", "A", "B", "C", "D", "E"))
    ap.add_argument("--arms", type=int, default=3)
    ap.add_argument("--hold", type=float, default=20.0,
                    help="seconds each arm is held, polling every 200 ms")
    ap.add_argument("--seconds", type=float, default=330.0,
                    help="baseline: how long to stay idle")
    ap.add_argument("--settle", type=float, default=35.0,
                    help="seconds between arms, after the device reads mode 0")
    ap.add_argument("--start-reset", action="store_true",
                    help="baseline: 3 s in, one USB-JTAG reset (pulse_rts), so the "
                         "run starts from a boot it captured")
    ap.add_argument("--halt-wait", type=float, default=10.0)
    ap.add_argument("--rejoin-after", type=float, default=4.0)
    ap.add_argument("--expect-git", default=None,
                    help="refuse unless /check_status git_version is this")
    return ap


def main():
    a = build_parser().parse_args()
    LF = _lf()
    import reset_rate_arms as RR

    stamp = time.strftime("%Y%m%dT%H%M%S")
    base = os.path.join(RUNS, "reset_matrix_%s_%s" % (a.case, stamp))
    rec = {"when": stamp, "case": a.case, "power": a.power,
           "args": vars(a), "arms": [], "halts": []}
    if a.case != "baseline":
        p = LF.page()
        if p is None or p.get("mode") != 0:
            print("FAIL: device must answer in mode 0 to start")
            return 1
        git = LF.build_id()
        rec["build"] = git
        if a.expect_git and a.expect_git not in json.dumps(git):
            print("FAIL: device reports %r, expected %s" % (git, a.expect_git))
            return 1
        print("build %s" % git)

    state = {"silent_since": None, "rejoin_after": a.rejoin_after,
             "last_rejoin": 0.0, "rejoins": []}
    cc = ConsoleCapture("COM4")
    cc.start()
    cc3 = None
    if a.case == "E":
        cc3 = ConsoleCapture("COM3")
        cc3.start()
    kv = pc = wit = None
    replay = None
    samples = []
    t_sess = time.perf_counter()
    try:
        if a.case != "baseline":
            import can
            plan = witness_plan(a.case, a.kvaser_acks)
            rec["witness_plan"] = plan
            if plan["witness"] == "pcan":
                kv = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                             bitrate=BITRATE, state=can.BusState.PASSIVE)
            else:
                kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE,
                             driver_mode=True)
            wit = Witness(kv)
            wit.start()
            if plan["replay"] == "pcan":
                pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                             bitrate=BITRATE)
                stim = LF.build_stimulus()
                rec["replay_stats"] = {}
                total = a.arms * (a.hold + a.settle + 40) + 30
                replay = threading.Thread(target=LF.play, args=(pc, stim, total),
                                          kwargs={"stats": rec["replay_stats"]},
                                          daemon=True)
                replay.start()
        time.sleep(1.5)
        if a.case == "baseline":
            if a.start_reset:
                time.sleep(1.5)
                t_r = time.perf_counter()
                rec["start_reset"] = {"t_rel_session": rel(t_r, t_sess),
                                      "pulsed": cc.pulse_rts(0.2)}
            t_end = t_sess + a.seconds
            while time.perf_counter() < t_end:
                poll_until(min(t_end, time.perf_counter() + 10.0), samples, 2.0,
                           state)
                halt_check(cc, t_sess, a.halt_wait, rec["halts"])
        else:
            mode = 2 if a.case == "B" else 3
            for k in range(a.arms):
                arm = {"n": k, "mode": mode}
                rec["arms"].append(arm)
                arm_samples = []
                t_arm = time.perf_counter()
                arm["t_rel_session"] = rel(t_arm, t_sess)
                try:
                    arm["post_ok"] = LF.dev_post("/gen_inhibit_set?mode=%d" % mode)
                except Exception as e:
                    arm["post_ok"] = type(e).__name__
                t_end = t_arm + a.hold
                while time.perf_counter() < t_end:
                    poll_until(min(t_end, time.perf_counter() + 1.0), arm_samples,
                               0.2, state)
                    if halt_check(cc, t_arm, a.halt_wait, rec["halts"]):
                        arm["rts_pulsed"] = True
                t_off = time.perf_counter()
                try:
                    LF.dev_post("/gen_inhibit_set?mode=0")
                    arm["posted_mode0_rel_s"] = rel(t_off, t_arm)
                except Exception as e:
                    arm["posted_mode0_rel_s"] = None
                    arm["mode0_post_error"] = type(e).__name__
                r = RR.find_reset([(t, s) for t, s in arm_samples])
                arm["page_reset"] = None if r is None else {
                    "rel_s": rel(r[0], t_arm), "why": r[1]}
                arm["last_armed_page_rel_s"] = next(
                    (rel(t, t_arm) for t, s in reversed(arm_samples)
                     if s and s.get("mode") == mode and (r is None or t < r[0])),
                    None)
                arm["unanswered"] = sum(1 for _, s in arm_samples if s is None)
                arm["polls"] = [(rel(t, t_arm), s) for t, s in arm_samples]
                if wit is not None:
                    arm["witness"] = wit.window(t_arm, t_off)
                arm["ready_again"] = wait_ready(60.0, state, samples)
                time.sleep(a.settle)
                arm["t_end_rel_session"] = rel(time.perf_counter(), t_sess)
                arm["_t_arm"] = t_arm
                arm["_t_next"] = time.perf_counter()
                print("arm %d (mode %d): page reset %s, unanswered %d, witness %s"
                      % (k, mode, arm["page_reset"], arm["unanswered"],
                         arm.get("witness")))
    finally:
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        time.sleep(0.5)
        for c in (cc, cc3):
            if c is not None:
                c.stop_flag.set()
                c.join(timeout=3.0)
        if wit is not None:
            wit.stop_flag.set()
            wit.join(timeout=3.0)
            with wit.lock:
                rec["witness_ids"] = {"0x%03X" % i: n for i, n in sorted(wit.ids.items())}
                rec["witness_error_frames"] = len(wit.errors)
        for b in (pc, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        try:
            rec["post"] = snap(LF.page())
        except Exception:
            rec["post"] = None

    lines = list(cc.lines)
    bs = boots(lines)
    rec["boots"] = [dict({k: v for k, v in b.items() if k != "t"},
                         t_rel_session=rel(b["t"], t_sess)) for b in bs]
    rec["dumps_rel_session"] = [rel(t, t_sess) for t in dumps(lines)]
    rec["com4_events"] = [(rel(t, t_sess), e) for t, e in cc.events]
    rec["rejoins"] = [(rel(t, t_sess), w) for t, w in state["rejoins"]]
    rec["halts"] = [{k: (rel(v, t_sess) if k.endswith("t") else v)
                     for k, v in h.items()} for h in rec["halts"]]
    if a.case == "baseline":
        rec["polls"] = [(rel(t, t_sess), s) for t, s in samples]
        rec["unanswered"] = sum(1 for _, s in samples if s is None)
    for arm in rec["arms"]:
        t_arm, t_next = arm.pop("_t_arm"), arm.pop("_t_next")
        inside = [b for b in bs if t_arm <= b["t"] < t_next]
        arm["boots"] = [{"rel_s": rel(b["t"], t_arm), "how": b["how"],
                         "reason": b["reason"], "reason_n": b["reason_n"],
                         "rst": b["rst"]} for b in inside]
        prior = [b for b in bs if b["t"] < t_arm]
        arm["prev_boot_rel_s"] = rel(prior[-1]["t"], t_arm) if prior else None
        arm["dumps_rel_s"] = [rel(t, t_arm) for t in dumps(lines)
                              if t_arm <= t < t_next]
        arm["com4_events"] = [(rel(t, t_arm), e) for t, e in cc.events
                              if t_arm <= t < t_next]
    with open(base + "_com4.txt", "x", encoding="ascii", errors="replace") as fh:
        for t, x in lines:
            fh.write("%9.3f %s\n" % (t - t_sess, x))
    if cc3 is not None:
        with open(base + "_com3.txt", "x", encoding="ascii", errors="replace") as fh:
            for t, x in cc3.lines:
                fh.write("%9.3f %s\n" % (t - t_sess, x))
        rec["com3_events"] = [(rel(t, t_sess), e) for t, e in cc3.events]
    with open(base + ".json", "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
    print("boots on COM4: %d" % len(bs))
    for b in rec["boots"]:
        print("  %8.3f s  %-22s reason %s (%s) rst %s up_last %s"
              % (b["t_rel_session"], "+".join(b["how"]), b["reason"],
                 b["reason_n"], b["rst"], b["up_last"]))
    print("dumps %s; halts %s; com4 events %s" % (rec["dumps_rel_session"],
                                                 rec["halts"], rec["com4_events"][:12]))
    print("wrote %s.json" % base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
