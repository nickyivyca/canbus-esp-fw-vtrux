"""Spec 12.4 "test-only features are inert in the build that will fly" -- tester, 2026-10-13.

For every test-only build switch, its trigger is sent to the image on the device
exactly as a test run sends it, and the effect the switch exists to produce is looked
for on the bus and on the device. The triggers come from ONE table, the firmware
repo's vtrux_checks.json ("inert_triggers", beside the switch table since
2026-10-24), which restart_error_frames.py --trigger also reads.

The same code runs in both directions:
  --expect absent    on the flight image: every effect must be absent, AND the device
                     must have kept running across every probe -- uptime continuous,
                     same reset reason, same build (reviewer's condition, 2026-10-13: a
                     probe that crashed the device would otherwise read as inert);
  --expect present   on an image built with that switch on: the effect must be there
                     (the positive control, so a probe that reaches nothing can't pass).

Probes, by the table's "observe":
  reset          GI_TEST_RESTART_QUIESCE's POST: runs restart_error_frames.py --trigger
                 --restart crash (live INHIBIT under replay, the request at 20 s), the
                 test run's own rig, and reads whether the device reset;
  http_status    each GET/POST in the table: the HTTP status (404 = no such endpoint);
  tagged_frames  GI_INSTRUMENT_TXABORT: in mode 0 with the PCAN open as the ACKing node
                 and the Kvaser witnessing, POST the request and count frames whose
                 bytes 2-3 are AB 7E or AB 7F (the probe's trial tag) in the 3 s after;
  page_key:<k>   under a replay, arm mode 3 at 5 s, read /gen_inhibit at 12 s while
                 live, and look for key <k> at any depth (page_has_key); disarm at 14 s;
  rollback       OTA_HEALTH_FAULT_INJECT: the image must be OTA'd first (bench_flash.py);
                 then 70 s with no request, and the build and uptime are read again.
  probe_json:<n> GI_INSTRUMENT_ECC (2026-10-20): GET each path in the table and read the
                 answer as JSON; the effect is there when its "probe" is <n>. A status
                 alone is not the observable: an answer of 200 to an unknown path would
                 read as the instrument, so the body has to name it.

The build is read from /gen_inhibit's elf_sha256, never /check_status (2026-10-20: it
carries credentials until the WiCAN's reset).

    GI_WICAN_SSID=... py -3.14 gen-inhibit/tools/bench/test_only_inert.py \
        --expect absent|present --switch NAME [--switch NAME ...] --power "..."
Writes runs/test_only_inert_<expect>_<stamp>.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
TAGS = (b"\xab\x7e", b"\xab\x7f")
UPTIME_SLACK_MS = 1500        # host read jitter allowed when checking uptime continuity
ROLLBACK_WAIT_S = 70.0


def load_triggers(repo=None):
    """{switch: {"probes": ((method, path), ...), "state", "effect", "observe": (...)}} from the
    firmware repo's vtrux_checks.json, key "inert_triggers"."""
    repo = repo or os.environ.get("GI_FW_REPO") or gi_paths.firmware_root()   # the folder holding vtrux_checks.json
    with open(os.path.join(repo, "vtrux_checks.json"), encoding="utf-8") as fh:
        t = json.load(fh)["inert_triggers"]
    return {s: {"probes": tuple(tuple(x) for x in e["probes"]), "state": e.get("state"),
                "effect": e.get("effect"), "observe": tuple(e.get("observe", ()))} for s, e in t.items()}


# ------------------------------------------------------------------ judgements --

def kept_running(before, after):
    """before/after: {"t": host epoch, "uptime_ms", "reset_reason", "build"}. True only
    when uptime advanced by the elapsed time (within UPTIME_SLACK_MS), the reset reason
    and the build are the same. A missing reading is not continuity."""
    if not before or not after or before.get("uptime_ms") is None or after.get("uptime_ms") is None:
        return False
    elapsed_ms = (after["t"] - before["t"]) * 1000.0
    adv = after["uptime_ms"] - before["uptime_ms"]
    return (adv >= elapsed_ms - UPTIME_SLACK_MS and adv <= elapsed_ms + UPTIME_SLACK_MS
            and after.get("reset_reason") == before.get("reset_reason")
            and after.get("build") == before.get("build"))


def tagged(frames):
    """frames: [(t, id, data bytes)] -> those carrying the TX-abort probe's trial tag."""
    return [f for f in frames if len(f[2]) >= 4 and bytes(f[2][2:4]) in TAGS]


def page_has_key(body, key):
    """Whether the page carries key <key> at ANY depth: the exact token "<key>": in the
    body text. A top-level-only look missed "abortwin", which lives inside the scheduler
    object -- the ABORTWIN positive control failed on that, 2026-10-13, and the flight
    image's first "absent" for it was vacuous. A key name that merely starts with <key>
    ("rxq_max") does not match."""
    return ('"%s":' % key) in (body or "")


def effect_present(observe, result):
    """One observable's verdict from a probe result: True (effect there), False
    (absent), None (the probe could not tell -- never a pass in either direction)."""
    if observe == "http_status":
        st = result.get("http_status")
        if not st:
            return None
        return any(s != 404 for s in st.values()) if isinstance(st, dict) else st != 404
    if observe == "reset":
        r = result.get("reset")
        return None if r is None else bool(r)
    if observe == "tagged_frames":
        n = result.get("tagged_frames")
        return None if n is None or not result.get("witness_ok") else n > 0
    if observe.startswith("page_key:"):
        if not result.get("live"):
            return None
        return bool(result.get("key_present"))
    if observe.startswith("probe_json:"):
        got = result.get("probe_json")
        if not got or any(v is None for v in got.values()):
            return None
        return any(v == observe.split(":", 1)[1] for v in got.values())
    if observe == "rollback":
        b0, b1 = result.get("build_before"), result.get("build_after")
        if b0 is None or b1 is None:
            return None
        return b0 != b1 or not result.get("uptime_continuous")
    return None


def judge(expect, observes, result):
    """(ok, why). absent: every observable False and the device kept running.
    present: at least one observable True. Any None fails."""
    v = {o: effect_present(o, result) for o in observes}
    if any(x is None for x in v.values()):
        return False, "undetermined: %s" % {o: x for o, x in v.items() if x is None}
    if expect == "absent":
        if any(v.values()):
            return False, "effect present: %s" % [o for o, x in v.items() if x]
        if not result.get("kept_running"):
            return False, "the device did not keep running across the probe"
        return True, "absent, and the device kept running"
    if not any(v.values()):
        return False, "effect absent on the switch-on image: the probe reaches nothing"
    return True, "present: %s" % [o for o, x in v.items() if x]


# ------------------------------------------------------------------ the bench --

def lf():
    import lowid_flood_skip_bench as LF
    return LF


def http(method, path, timeout=60.0):
    LF = lf()
    req = urllib.request.Request("http://%s%s" % (LF.HOST, path), data=b"" if method == "POST" else None,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                       # the device may reset before answering
        return None, repr(e)[:80]


def alive():
    LF = lf()
    p = LF.page() or {}
    return {"t": time.time(), "uptime_ms": p.get("uptime_ms"), "reset_reason": p.get("reset_reason"),
            "build": p.get("elf_sha256"), "mode": p.get("mode")}


def probe_answer(st, body):
    """One GET's "probe" field: the value, "" when the answer is not a JSON object
    naming one, None when there was no answer (undetermined, never a pass)."""
    if st is None:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode((body or "").lstrip())
    except ValueError:
        return ""
    return d.get("probe", "") if isinstance(d, dict) else ""


def probe_json(probes):
    out = {"http_status": {}, "probe_json": {}}
    for method, path in probes:
        if method != "GET":
            continue
        st, body = http("GET", path)
        out["http_status"][path] = st
        out["probe_json"][path] = probe_answer(st, body)
    return out


def replay_thread(seconds, stop):
    import can
    import e4_device_calibration as E
    E.CAPTURE = gi_paths.runs("scottsvalley_armable_300s.log")
    E.WINDOW_START = 0.0
    frames = E.load_window(seconds)
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000, auto_reset=True)
    msgs = [can.Message(arbitration_id=i, data=d, is_extended_id=x) for _t, i, d, x in frames]
    base = time.perf_counter() + 0.2

    def run():
        try:
            for (t, _i, _d, _x), m in zip(frames, msgs):
                if stop.is_set():
                    break
                while time.perf_counter() < base + t:
                    pass
                try:
                    tx.send(m, timeout=0.5)
                except can.CanError:
                    pass
        finally:
            tx.shutdown()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, base


# Why an arm did not go live, recorded beside the live flag (reviewer, 2026-10-17:
# on e3d9437 prod the RXQ arm read mode 3, not live, twice, and the record could not
# say why). Status page fields, read as the page gives them; absent keys read None.
REASON_FIELDS = ("arm_block", "abort_reason", "abort_latched", "disable_reason", "err_n", "err_age_ms", "worker")


def page_key_result(st, body, key):
    """The page-key probe's record from one GET of /gen_inhibit: the live flag, the
    mode, whether <key> is on the page, and REASON_FIELDS."""
    try:
        p, _ = json.JSONDecoder().raw_decode((body or "").lstrip())
    except Exception:
        p = {}
    if not isinstance(p, dict):
        p = {}
    out = {"http_status": st, "live": bool(p.get("inhibit_live")), "mode": p.get("mode"),
           "key_present": page_has_key(body, key), "key_top_level": key in p, "page_keys": sorted(p)}
    out["reasons"] = {f: p.get(f) for f in REASON_FIELDS}
    return out


def wait_before(index, gap_s):
    """Seconds to wait before switch <index> (0-based): none before the first, --gap-s
    before each later one (reviewer, 2026-10-17: spacing after the QUIESCE probe)."""
    return gap_s if index > 0 else 0.0


def probe_page_key(key):
    LF = lf()
    stop = threading.Event()
    th, base = replay_thread(20.0, stop)
    out = {}
    try:
        while time.perf_counter() < base + 5.0:
            time.sleep(0.05)
        LF.dev_post("/gen_inhibit_set?mode=3")
        while time.perf_counter() < base + 12.0:
            time.sleep(0.05)
        st, body = http("GET", "/gen_inhibit")
        out = page_key_result(st, body, key)
        while time.perf_counter() < base + 14.0:
            time.sleep(0.05)
        LF.dev_post("/gen_inhibit_set?mode=0")
    finally:
        stop.set()
        th.join(25)
    out.pop("http_status", None)          # a GET of the normal page is not the observable
    return out


# The witness is shown listening by two marker frames the PCAN sends, one before
# the probe's requests and one after (2026-10-16). The first rule, "any frame",
# leaned on the X2's 1 Hz diag; the X2 on 756d6714 is silent on CAN1, so on
# c65c45e prod a quiet bus read as a deaf witness and the probe was undetermined.
# 0x7A5 is in none of the Vtrux DBCs, and its bytes 2-3 are not a trial tag.
MARKER_ID = 0x7A5
MARKERS = (bytes.fromhex("5aa5000000000000"), bytes.fromhex("5aa5000000000001"))


def witness_listening(seen):
    """Both markers seen by the witness: it listened across the probe."""
    got = {d for _t, i, d in seen if i == MARKER_ID}
    return all(m in got for m in MARKERS)


def probe_txabort(probes):
    import can
    out = {"http_status": {}}
    if (lf().page() or {}).get("mode") != 0:
        return {"error": "the device must be in mode 0 for this probe"}
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)    # PCAN first, always
    kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
    seen, stop = [], threading.Event()

    def wit():
        while not stop.is_set():
            m = kv.recv(timeout=0.1)
            if m is not None and not m.is_error_frame:
                seen.append((m.timestamp, m.arbitration_id, bytes(m.data)))
    w = threading.Thread(target=wit, daemon=True)
    w.start()
    try:
        time.sleep(1.0)
        tx.send(can.Message(arbitration_id=MARKER_ID, data=MARKERS[0], is_extended_id=False))
        time.sleep(0.3)
        for method, path in probes:
            st, body = http(method, path, timeout=120.0)
            out["http_status"][path] = st
            out["body_len_%s" % path] = len(body or "")
        time.sleep(3.0)
        tx.send(can.Message(arbitration_id=MARKER_ID, data=MARKERS[1], is_extended_id=False))
        time.sleep(0.5)
    finally:
        stop.set()
        w.join(3)
        kv.shutdown()
        tx.shutdown()
    out["witness_ok"] = witness_listening(seen)
    out["witnessed_frames"] = len(seen)
    out["marker_frames"] = len([1 for _t, i, _d in seen if i == MARKER_ID])
    out["tagged_frames"] = len(tagged(seen))
    return out


def probe_crash(name, power):
    cmd = [sys.executable, os.path.join(HERE, "restart_error_frames.py"), "--condition", "live",
           "--restart", "crash", "--trigger", name, "--cycles", "1", "--power", power,
           "--record-get", "/gi_quiesce"]
    before = set(os.listdir(RUNS))
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    new = sorted(f for f in set(os.listdir(RUNS)) - before
                 if f.startswith("restart_error_frames_") and f.endswith(".json"))
    if not new:
        return {"error": "restart_error_frames wrote no run file", "rc": r.returncode,
                "stderr": r.stderr[-300:]}
    rec = json.load(open(os.path.join(RUNS, new[-1]), encoding="utf-8"))
    lf().dev_post("/gen_inhibit_set?mode=0")      # a device that did not reset is still armed
    http_post = rec["events"].get("reboot_http")
    return {"run": new[-1], "reset": rec.get("reads_after_boot", 0) > 0,
            "reset_reasons_after": rec.get("reset_reasons_after"),
            "post_answer": http_post, "error_frames": rec.get("witness_error_frames_total"),
            "live_at_post": rec.get("live_at_post")}


def post_status(answer):
    """restart_error_frames records the trigger POST as "ok" (HTTP 200) or the repr of
    the exception. -> 200, the HTTP error code it names, or None (no answer: the device
    may have reset first, which says nothing about the endpoint)."""
    if answer == "ok":
        return 200
    m = re.search(r"HTTP Error (\d{3})|HTTPError (\d{3})", str(answer or ""))
    return int(m.group(1) or m.group(2)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", required=True, choices=("absent", "present"))
    ap.add_argument("--switch", action="append", required=True)
    ap.add_argument("--power", required=True)
    ap.add_argument("--gap-s", type=float, default=0.0,
                    help="wait this long between one switch's probe and the next (default 0)")
    a = ap.parse_args()
    triggers = load_triggers()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    run = {"when": stamp, "expect": a.expect, "power": a.power, "gap_s": a.gap_s,
           "build_at_start": {"elf_sha256": (lf().page() or {}).get("elf_sha256"), "from": "/gen_inhibit"},
           "results": []}
    rc = 0
    prev_end = None
    for k, name in enumerate(a.switch):
        trig = triggers[name]
        observes = trig["observe"]
        time.sleep(wait_before(k, a.gap_s))
        before = alive()
        result = {}
        if "reset" in observes:
            result.update(probe_crash(name, a.power))
            st, _b = http("GET", [p for m, p in trig["probes"] if m == "GET"][0])
            result["http_status"] = {"GET": st, "POST": post_status(result.get("post_answer"))}
        elif "tagged_frames" in observes:
            result.update(probe_txabort(trig["probes"]))
        elif any(o.startswith("page_key:") for o in observes):
            key = [o for o in observes if o.startswith("page_key:")][0].split(":", 1)[1]
            result.update(probe_page_key(key))
        elif any(o.startswith("probe_json:") for o in observes):
            result.update(probe_json(trig["probes"]))
        elif "rollback" in observes:
            b0 = alive()
            time.sleep(ROLLBACK_WAIT_S)
            b1 = alive()
            result.update({"build_before": b0["build"], "build_after": b1["build"],
                           "uptime_continuous": kept_running(b0, b1), "waited_s": ROLLBACK_WAIT_S})
        time.sleep(2.0)
        after = alive()
        result["alive_before"], result["alive_after"] = before, after
        # the spacing actually had: from the last switch's closing read to this one's first
        result["gap_from_previous_s"] = (round(before["t"] - prev_end, 2)
                                         if prev_end is not None and before and before.get("t") else None)
        prev_end = (after or {}).get("t")
        result["kept_running"] = kept_running(before, after)
        ok, why = judge(a.expect, observes, result)
        rc |= 0 if ok else 1
        run["results"].append({"switch": name, "trigger": trig, "result": result, "ok": ok, "why": why})
        print("%-26s %s  %s" % (name, "PASS" if ok else "FAIL", why))
        if "reasons" in result:
            print("    live %s mode %s, %s" % (result.get("live"), result.get("mode"),
                                            ", ".join("%s=%r" % kv for kv in result["reasons"].items())))
    path = os.path.join(RUNS, "test_only_inert_%s_%s.json" % (a.expect, stamp))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=1, default=str)
    print("wrote", os.path.basename(path))
    return rc


if __name__ == "__main__":
    sys.exit(main())
