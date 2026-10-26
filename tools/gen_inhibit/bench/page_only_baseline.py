"""Idle baseline of the WiCAN with NO console: page polls and the WiFi link only.

Gen-inhibit tester, 2026-10-09, for the run with the WiCAN's USB unplugged
(12 V on OBD pin 16 only; reviewer's request). With USB out there is no COM4,
so no boot banner, no reset reason and no USB-JTAG reset. Neither the
/gen_inhibit page nor /check_status carries an uptime, so a reboot cannot be
read as "uptime went down". What a reboot does that this CAN see: the device
stops answering HTTP and its SoftAP goes away, so the laptop's WiFi drops off
it (a reset of a71b90a took ~6 s to rejoin). So every poll records:

  - the page (answered or not, mode, drv null),
  - the laptop's WLAN state and SSID (netsh wlan show interfaces).

OUTAGE = a maximal run of polls where the page went unanswered, OR the WLAN was
not connected to the WiCAN's SSID, OR the poll STALLED (took longer than
--stall-s to answer). The stall is the sign a reboot actually gives here: the
positive control (one USB-JTAG reset, 2026-10-09 11:26,
runs/page_only_baseline_20261009T112608) showed NO unanswered page and NO WLAN
drop -- the request in flight blocked 7.4 s and then answered -- so the first
version of this tool, without the stall sign, saw no outage at a real reboot. Each outage is a candidate reboot. Without a
console this cannot tell a reboot from a SoftAP/WiFi outage, so outages are
reported as outages; 0 outages means no reboot by this test. A mode that is
not 0 is reported too (nothing posts during the run). Rejoins with netsh after
--rejoin-after s of WLAN loss.

--watch-port COM4: records on every poll whether that COM port is present, so
the moment the user pulls the USB cable is stamped in the run (`port_gone_at`).
Outages are then split into before/at the unplug (within --unplug-window s) and
after it; the idle window is counted from the unplug.

--pulse-rts-at S (validation only, USB connected): opens COM4 at S seconds and
gives one USB-JTAG reset, so the detector can be checked against a reboot that
is known to happen. COM4 is never read for counting.

UPTIME (spec 11, "Uptime and reset reason are reported", user 2026-10-09; first
on cc34c80). Every poll also records the page's `uptime_ms` and `reset_reason`,
and `uptime_check` reads reboots from them directly, with no stall threshold:
  - REBOOT: a reading lower than the previous one (spec: "a reading lower than
    the previous one is a reboot").
  - BEHIND / AHEAD: uptime moved by more or less than the wall clock between the
    two readings, beyond what the two requests' durations allow (0.25 s plus half
    of each request's duration, since a reading is taken somewhere inside its
    request). BEHIND is also what a reboot between polls looks like when the old
    uptime was smaller than the gap; either way it is reported, never dropped.
  - per boot: its reset_reason, whether it changed within the boot (it is read
    once at boot, so it must not), and the uptime rate against the wall clock
    (end-to-end over the boot; spec: "about 1", reviewer).
  - the fields' form: uptime_ms a JSON integer >= 0, reset_reason a JSON string
    that is an ESP-IDF v5.4.1 `esp_reset_reason_t` name without `ESP_RST_`, or a
    number written as a string for an unrecognised value (reviewer, 2026-10-09).
    A page without the fields is reported as MISSING.
`agree` then compares the two detectors: a reboot no outage covers is a reboot
the stall detector missed; an outage with no reboot in it is a stall-detector
outage that was not a reboot.
--build-check: reads /gen_inhibit's `elf_sha256` (lowid_flood_skip_bench.build_id) at
the start and at the end of the run, for bring-up 1.5b after an OTA. Never /check_status
(reviewer, 2026-10-20); until 2026-10-25 this read its `git_version`.

    GI_WICAN_SSID=... py -3.14 tools/gen_inhibit/bench/page_only_baseline.py
        --power "..." [--seconds 360] [--period 1.0] [--pulse-rts-at 30]
Writes runs/page_only_baseline_<stamp>.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
SSID_ENV = "GI_WICAN_SSID"


def parse_wlan(text, ssid):
    """(connected_to_ssid, state) from `netsh wlan show interfaces` output."""
    state = None
    cur_ssid = None
    for line in text.splitlines():
        m = re.match(r"\s*State\s*:\s*(.+?)\s*$", line)
        if m:
            state = m.group(1)
        m = re.match(r"\s*SSID\s*:\s*(.+?)\s*$", line)
        if m:
            cur_ssid = m.group(1)
    return (state == "connected" and ssid is not None and cur_ssid == ssid), state


def outages(samples, stall_s=3.0):
    """Maximal runs of bad polls. samples: [{t, answered, wlan_ok, mode, dur}] in time order.

    A poll is bad if unanswered, off the WLAN, or slower than stall_s (dur)."""
    return _outages(samples, stall_s)


def _outages(samples, stall_s):
    """See outages().

    Returns [{start, end, polls, unanswered, wlan_down}]; start/end are the times of
    the first and last bad poll."""
    out = []
    cur = None
    for s in samples:
        stalled = (s.get("dur") or 0.0) > stall_s
        bad = (not s["answered"]) or (not s["wlan_ok"]) or stalled
        if bad:
            if cur is None:
                cur = {"start": s["t"], "end": s["t"], "polls": 0, "unanswered": 0,
                       "wlan_down": 0, "stalled": 0, "max_dur": 0.0}
                out.append(cur)
            cur["end"] = s["t"]
            cur["polls"] += 1
            cur["unanswered"] += 0 if s["answered"] else 1
            cur["wlan_down"] += 0 if s["wlan_ok"] else 1
            cur["stalled"] += 1 if stalled else 0
            cur["end"] = round(s["t"] + (s.get("dur") or 0.0), 3)
            cur["max_dur"] = max(cur["max_dur"], s.get("dur") or 0.0)
        else:
            cur = None
    return out


def port_gone_at(samples):
    """Time of the first poll at which the watched port is absent after being present."""
    seen = False
    for s in samples:
        if s.get("port") is True:
            seen = True
        elif s.get("port") is False and seen:
            return s["t"]
    return None


RESET_NAMES = ("UNKNOWN", "POWERON", "EXT", "SW", "PANIC", "INT_WDT", "TASK_WDT", "WDT",
               "DEEPSLEEP", "BROWNOUT", "SDIO", "USB", "JTAG", "EFUSE", "PWR_GLITCH",
               "CPU_LOCKUP")


def reason_ok(v):
    """True if v is a reset_reason in the reported form: a known name, or digits."""
    return isinstance(v, str) and (v in RESET_NAMES or (v.isdigit() and v != ""))


def uptime_ok(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _wall(s):
    """Wall time of a reading: the middle of its request."""
    return s.get("tq", s["t"]) + (s.get("dur") or 0.0) / 2.0


def uptime_check(samples, base_tol=0.25, precise_s=0.5):
    """Reboots and anomalies read from uptime_ms / reset_reason. See the module doc."""
    res = {"reboots": [], "behind": [], "ahead": [], "missing": [], "bad_uptime": [],
           "bad_reason": [], "reason_changed": [], "boots": []}
    prev = None
    boot = None
    for s in samples:
        if not s["answered"]:
            continue
        if not s.get("has_uptime") or not s.get("has_reason"):
            res["missing"].append(s["t"])
            continue
        u, r = s.get("uptime_ms"), s.get("reset_reason")
        if not reason_ok(r):
            res["bad_reason"].append([s["t"], r])
        if not uptime_ok(u):
            res["bad_uptime"].append([s["t"], u])
            continue
        new_boot = boot is None
        if prev is not None:
            wall = _wall(s) - _wall(prev)
            tol = base_tol + ((prev.get("dur") or 0.0) + (s.get("dur") or 0.0)) / 2.0
            du = (u - prev["uptime_ms"]) / 1000.0
            if u < prev["uptime_ms"]:
                res["reboots"].append({"t": s["t"], "prev_ms": prev["uptime_ms"], "ms": u,
                                       "reason": r, "prev_reason": prev.get("reset_reason")})
                new_boot = True
            elif du < wall - tol:
                res["behind"].append({"t": s["t"], "du": round(du, 3), "wall": round(wall, 3)})
            elif du > wall + tol:
                res["ahead"].append({"t": s["t"], "du": round(du, 3), "wall": round(wall, 3)})
        if new_boot:
            boot = {"first_t": s["t"], "first_ms": u, "reason": r,
                    "last_t": s["t"], "last_ms": u, "polls": 0, "_precise": []}
            res["boots"].append(boot)
        elif r != boot["reason"]:
            res["reason_changed"].append([s["t"], boot["reason"], r])
        boot["last_t"], boot["last_ms"] = s["t"], u
        boot["polls"] += 1
        if (s.get("dur") or 0.0) <= precise_s:
            boot["_precise"].append((_wall(s), u))
        prev = s
    for b in res["boots"]:
        # The rate uses only readings whose request was short: a reading is dated to the
        # middle of its request, and a 7 s stalled request (the first reading after a
        # reset) was answered at its END, which put a USB boot's rate at 0.952 when it
        # was 1.000 (2026-10-10 forced-reset run).
        pr = b.pop("_precise")
        b["rate_readings"] = len(pr)
        span = pr[-1][0] - pr[0][0] if len(pr) >= 2 else 0.0
        b["rate"] = round((pr[-1][1] - pr[0][1]) / 1000.0 / span, 5) if span > 0 else None
    res["readings"] = sum(b["polls"] for b in res["boots"])
    # No reading is no evidence: with nothing answered the lists are empty, and that
    # must not read as "no reboot" (2026-10-10, a run with the laptop off the SoftAP).
    res["clean"] = res["readings"] > 0 and not any(
        res[k] for k in ("reboots", "behind", "ahead", "missing", "bad_uptime", "bad_reason",
                         "reason_changed"))
    return res


def agree(outs, uptime, slack=2.0):
    """Stall-detector outages vs uptime reboots (a reboot is stamped at the first poll
    that shows it). Returns the reboots no outage covers and the outages with no reboot."""
    def covers(o, t):
        return o["start"] - slack <= t <= o["end"] + slack
    reboots = [x["t"] for x in uptime["reboots"]] + [x["t"] for x in uptime["behind"]]
    missed = [t for t in reboots if not any(covers(o, t) for o in outs)]
    not_reboot = [o for o in outs if not any(covers(o, t) for t in reboots)]
    return {"reboots_stall_missed": missed, "outages_not_reboot": not_reboot,
            "agree": not missed and not not_reboot}


def judge(samples, stall_s=3.0, unplug_window=15.0):
    o = outages(samples, stall_s)
    odd_mode = [s["t"] for s in samples if s["answered"] and s.get("mode") != 0]
    gone = port_gone_at(samples)
    split = None
    if gone is not None:
        at = [x for x in o if x["end"] >= gone - unplug_window and x["start"] <= gone + unplug_window]
        after = [x for x in o if x["start"] > gone + unplug_window]
        before = [x for x in o if x["end"] < gone - unplug_window]
        idle = [s for s in samples if s["t"] > gone + unplug_window]
        split = {"port_gone_at": gone, "outages_before": before, "outages_at_unplug": at,
                 "outages_after": after,
                 "idle_after_s": round(idle[-1]["t"] - idle[0]["t"], 1) if idle else 0.0,
                 "idle_after_polls": len(idle)}
    up = uptime_check(samples)
    return {"polls": len(samples), "unplug": split, "uptime": up, "agree": agree(o, up),
            "uptime_verdict": "no reboot, fields well-formed" if up["clean"] else
                              "NO UPTIME READINGS, so no verdict" if up["readings"] == 0
                              and not up["missing"] and not up["bad_uptime"] else
                              "%d reboot(s), %d behind, %d ahead, %d missing, %d bad uptime, "
                              "%d bad reason, %d reason changed"
                              % tuple(len(up[k]) for k in ("reboots", "behind", "ahead", "missing",
                                                            "bad_uptime", "bad_reason",
                                                            "reason_changed")),
            "stalled_polls": sum(1 for s in samples if (s.get("dur") or 0.0) > stall_s),
            "unanswered": sum(1 for s in samples if not s["answered"]),
            "wlan_down_polls": sum(1 for s in samples if not s["wlan_ok"]),
            "outages": o, "not_mode0_at": odd_mode,
            "verdict": "no outage" if not o and not odd_mode else
                       "%d outage(s), %d poll(s) not in mode 0" % (len(o), len(odd_mode))}


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--power", required=True, help="how the WiCAN is powered; recorded")
    ap.add_argument("--seconds", type=float, default=360.0)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--rejoin-after", type=float, default=4.0)
    ap.add_argument("--stall-s", type=float, default=3.0,
                    help="a poll slower than this is an outage sign (a reboot stalls ~7 s)")
    ap.add_argument("--watch-port", default=None,
                    help="stamp when this COM port disappears (the USB unplug)")
    ap.add_argument("--unplug-window", type=float, default=15.0)
    ap.add_argument("--pulse-rts-at", type=float, default=None,
                    help="validation only (USB connected): one USB-JTAG reset at this time")
    ap.add_argument("--build-check", action="store_true",
                    help="read /gen_inhibit's elf_sha256 at the start and the end (1.5b)")
    return ap


def main():
    a = build_parser().parse_args()
    ssid = os.environ.get(SSID_ENV)
    if not ssid:
        print("FAIL: set %s" % SSID_ENV)
        return 2
    import lowid_flood_skip_bench as LF
    cc = None
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "power": a.power, "args": vars(a), "events": [], "samples": []}
    if a.build_check:
        rec["build_start"] = {"wall": stamp, "elf_sha256": (LF.build_id() or {}).get("elf_sha256")}
    t0 = time.perf_counter()
    lost_since = None
    try:
        while time.perf_counter() - t0 < a.seconds:
            t = time.perf_counter() - t0
            if a.pulse_rts_at is not None and cc is None and t >= a.pulse_rts_at:
                from console_capture import ConsoleCapture
                cc = ConsoleCapture("COM4")
                cc.start()
                for _ in range(40):
                    if cc.ser is not None:
                        break
                    time.sleep(0.05)
                rec["events"].append([round(time.perf_counter() - t0, 3),
                                      "rts pulsed: %s" % cc.pulse_rts(0.2)])
            r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, text=True, timeout=10)
            wlan_ok, state = parse_wlan(r.stdout, ssid)
            port = None
            if a.watch_port:
                import serial.tools.list_ports as LP
                port = any(x.device == a.watch_port for x in LP.comports())
            tq = time.perf_counter()
            p = LF.page() if wlan_ok else None
            s = {"t": round(t, 3), "tq": round(tq - t0, 3), "wlan_ok": wlan_ok,
                 "wlan_state": state,
                 "dur": round(time.perf_counter() - tq, 3), "port": port,
                 "answered": p is not None,
                 "mode": None if p is None else p.get("mode"),
                 "drv_null": None if p is None else p.get("drv") is None,
                 "has_uptime": None if p is None else "uptime_ms" in p,
                 "has_reason": None if p is None else "reset_reason" in p,
                 "uptime_ms": None if p is None else p.get("uptime_ms"),
                 "reset_reason": None if p is None else p.get("reset_reason")}
            rec["samples"].append(s)
            if not wlan_ok:
                lost_since = t if lost_since is None else lost_since
                if t - lost_since >= a.rejoin_after:
                    r2 = subprocess.run(["netsh", "wlan", "connect", "name=%s" % ssid],
                                        capture_output=True, text=True, timeout=10)
                    rec["events"].append([round(t, 3), "rejoin netsh rc %d" % r2.returncode])
                    lost_since = t
            else:
                lost_since = None
            nxt = t0 + t + a.period
            time.sleep(max(0.0, nxt - time.perf_counter()))
    finally:
        if cc is not None:
            cc.stop_flag.set()
            try:
                cc.join(2)
                if cc.ser is not None:
                    cc.ser.close()
            except Exception:
                pass
    if a.build_check:
        rec["build_end"] = {"t": round(time.perf_counter() - t0, 3),
                            "elf_sha256": (LF.build_id() or {}).get("elf_sha256")}
    rec["judge"] = judge(rec["samples"], a.stall_s, a.unplug_window)
    path = os.path.join(RUNS, "page_only_baseline_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    j = rec["judge"]
    print("polls %d, unanswered %d, wlan-down polls %d, stalled polls %d, outages %d -> %s"
          % (j["polls"], j["unanswered"], j["wlan_down_polls"], j["stalled_polls"],
             len(j["outages"]), j["verdict"]))
    for o in j["outages"]:
        print("  outage %.1f-%.1f s, %d poll(s), max poll %.1f s"
              % (o["start"], o["end"], o["polls"], o["max_dur"]))
    if j["unplug"]:
        u = j["unplug"]
        print("  %s gone at %.1f s; outages before %d, at unplug %d, after %d; idle after %.1f s (%d polls)"
              % (a.watch_port, u["port_gone_at"], len(u["outages_before"]),
                 len(u["outages_at_unplug"]), len(u["outages_after"]),
                 u["idle_after_s"], u["idle_after_polls"]))
    elif a.watch_port:
        print("  %s never disappeared" % a.watch_port)
    up = j["uptime"]
    print("uptime: %s" % j["uptime_verdict"])
    for x in up["reboots"]:
        print("  reboot at %.1f s: uptime %d -> %d ms, reset_reason %s (was %s)"
              % (x["t"], x["prev_ms"], x["ms"], x["reason"], x["prev_reason"]))
    for k in ("behind", "ahead", "missing", "bad_uptime", "bad_reason", "reason_changed"):
        if up[k]:
            print("  %s: %s" % (k, up[k][:5]))
    for b in up["boots"]:
        print("  boot seen %.1f-%.1f s, reset_reason %s, uptime %d -> %d ms, %d polls, rate %s"
              % (b["first_t"], b["last_t"], b["reason"], b["first_ms"], b["last_ms"],
                 b["polls"], b["rate"]))
    g = j["agree"]
    print("stall vs uptime: %s; reboots the stall detector missed %s; outages not a reboot %d"
          % ("agree" if g["agree"] else "DISAGREE", g["reboots_stall_missed"],
             len(g["outages_not_reboot"])))
    if a.build_check:
        print("build: start %s, end %s at %.1f s" % (rec["build_start"]["elf_sha256"],
                                                    rec["build_end"]["elf_sha256"],
                                                    rec["build_end"]["t"]))
    print("events", rec["events"])
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
