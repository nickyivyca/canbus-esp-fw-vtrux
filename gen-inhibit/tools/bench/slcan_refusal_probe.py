"""Spec 3.2, no other transmitter: SLCAN over TCP and the web UI websocket must not
open, close, reconfigure or transmit, in OFF and while gen_inhibit owns the bus.

Gen-inhibit tester, 2026-10-10 (reviewer-approved plan). Spec 3.2:
  - "The SLCAN command parser is disabled on every channel. No client can transmit
    a frame, open or close the bus, change silent/loopback mode or the bitrate
    through it."
  - "While gen_inhibit owns the bus (any non-OFF mode), no path other than
    gen_inhibit may transmit -- can_send() refuses -- and no client path may change
    bus state."
  - "A refused attempt is counted, never silently dropped: the status page carries
    refused_slcan and refused_can_send, both since boot" (changed 2026-10-12, user;
    it said "logged", and the console is muted after boot). judge_counted(): the two
    SLCAN command, OFF included, must raise refused_slcan by exactly 1 and leave
    refused_can_send alone (reviewer's reading, 2026-10-12); a page without them is
    UNRESOLVED. Until 2026-10-12 this recorded only whether anything observable
    recorded a refusal (none did: the 2026-10-10 finding).
Channels: the TCP port when the device's protocol is slcan (only `protocol`,
`port_type` and `port` are read from /load_config -- it carries credentials, so
nothing else is kept or printed), and the websocket at /ws whatever the protocol.
UART needs USB and is parked.
Commands, in order: O (open), Y (loopback), S6 (500 kbit), t (frame 0x6A6, which
nothing on the bench sends), C (close). Conditions: OFF (mode 0), and armed (mode 3,
live on the interlock slice the PCAN loops, skip_method_bench.Player). Kvaser
witness. Around each command: the status page before and after, the channel's
reply, and the witness frames in between.
A command FAILS if: any bus-state field on the page changes (mode, bus_on, drv,
inhibit_live, self_off, disabled, arm_block, abort_reason); 0x6A6 appears on the
wire; in OFF, any WiCAN frame appears; armed, the device stops answering (tx_ok
does not rise).

    GI_WICAN_SSID=... py -3.14 slcan_refusal_probe.py --power "..." [--channels tcp,ws]
Writes runs/slcan_refusal_<stamp>.json.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()

INJECT_ID = 0x6A6
COMMANDS = [("O", "O\r"), ("Y", "Y\r"), ("S6", "S6\r"), ("t", "t6A64DEADBEEF\r"), ("C", "C\r")]
STATE_KEYS = ("mode", "bus_on", "drv_state", "inhibit_live", "self_off", "disabled",
              "arm_block", "abort_reason")
# The WiCAN's own frames: its probe and diag pages. NOT 0x7F4-0x7F7 (the X2's pages, on
# the same segment) and NOT 0x051 (the replayed VCM's, by id indistinguishable from an
# answer; in OFF the driver is not installed -- drv null -- so the device cannot send
# one). The first run counted both and failed every OFF command on frames that were
# not the WiCAN's (2026-10-10).
WICAN_IDS = {0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}
# Spec 3.2 / 11 (user, 2026-10-12): "A refused attempt is counted, never silently
# dropped: the status page carries refused_slcan and refused_can_send, both since boot."
REFUSED_KEYS = ("refused_slcan", "refused_can_send")


def judge_counted(pre, post):
    """Every command this probe sends, `t` included, is refused at the SLCAN parser (it is
    disabled on every channel, OFF included), so each books exactly +1 in refused_slcan and
    nothing in refused_can_send: "an attempt counts once, in the counter for the path that
    refused it", with no rate limit (reviewer's reading, 2026-10-12)."""
    if pre is None or post is None:
        return {"counted": "UNREAD"}
    if any(k not in pre or k not in post for k in REFUSED_KEYS):
        return {"counted": "UNRESOLVED: the page carries no %s" % "/".join(REFUSED_KEYS)}
    d = {k: post[k] - pre[k] for k in REFUSED_KEYS}
    if any(v < 0 for v in d.values()):
        verdict = "FAIL: a since-boot counter went down (a reboot?) %r" % d
    elif d == {"refused_slcan": 1, "refused_can_send": 0}:
        verdict = "PASS"
    elif sum(d.values()) == 0:
        verdict = "FAIL: refused and not counted"
    else:
        verdict = "FAIL: expected refused_slcan +1 and refused_can_send +0, got %r" % d
    return {"counted": verdict, "refused_delta": d}


def state(page):
    if page is None:
        return None
    s = {k: page.get(k) for k in STATE_KEYS if k != "drv_state"}
    drv = page.get("drv")
    s["drv_state"] = None if drv is None else drv.get("state")
    s["tx_ok"] = page.get("tx_ok")
    for k in REFUSED_KEYS:              # absent stays absent (builds before 2026-10-12)
        if k in page:
            s[k] = page[k]
    s["keys"] = sorted(page.keys())
    return s


def judge(cond, pre, post, wire_ids):
    """One command's verdict. wire_ids: ids the witness saw between pre and post."""
    why = []
    if pre is None or post is None:
        return {"verdict": "UNREAD", "why": ["status page not read"]}
    for k in STATE_KEYS:
        if pre.get(k) != post.get(k):
            why.append("%s changed %r -> %r" % (k, pre.get(k), post.get(k)))
    if INJECT_ID in wire_ids:
        why.append("0x%03X reached the wire" % INJECT_ID)
    if cond == "off" and any(i in WICAN_IDS for i in wire_ids):
        why.append("a WiCAN frame on the wire in OFF")
    if cond == "armed" and not ((post.get("tx_ok") or 0) > (pre.get("tx_ok") or 0)):
        why.append("the device stopped answering (tx_ok %r -> %r)" % (pre.get("tx_ok"), post.get("tx_ok")))
    new_keys = sorted(set(post.get("keys", [])) - set(pre.get("keys", [])))
    return {"verdict": "FAIL" if why else "PASS", "why": why, "new_page_keys": new_keys}


class Witness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.ids, self.errors = bus, [], 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is None:
                continue
            if m.is_error_frame:
                self.errors += 1
            else:
                self.ids.append(m.arbitration_id)


def channel_send(kind, host, port, text, timeout=0.6):
    """Send one SLCAN command; return the reply (repr) or the error."""
    try:
        if kind == "tcp":
            with socket.create_connection((host, port), timeout=3) as s:
                s.sendall(text.encode())
                s.settimeout(timeout)
                try:
                    return repr(s.recv(256))
                except socket.timeout:
                    return "no reply"
        from websockets.sync.client import connect
        with connect("ws://%s/ws" % host, open_timeout=3) as ws:
            ws.send(text)
            try:
                return repr(ws.recv(timeout=timeout))
            except TimeoutError:
                return "no reply"
    except Exception as e:
        return "ERROR %s: %s" % (type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--power", required=True)
    ap.add_argument("--channels", default="tcp,ws")
    ap.add_argument("--conditions", default="off,armed")
    a = ap.parse_args()
    import can
    import lowid_flood_skip_bench as LF
    import skip_method_bench as S
    cfg = {}
    try:
        full = json.loads(LF.dev_get("/load_config"))
        cfg = {k: full.get(k) for k in ("protocol", "port_type", "port")}
        del full
    except Exception as e:
        cfg = {"error": "%s" % type(e).__name__}
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "power": a.power, "build": LF.build_id(), "config": cfg,
           "inject_id": hex(INJECT_ID), "results": []}
    print("build %s, config %s" % (rec["build"], cfg))
    kv = pc = player = wit = None
    try:
        kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
        wit = Witness(kv)
        wit.start()
        pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        player = S.Player(pc, LF.build_stimulus())
        player.start()
        time.sleep(2.0)
        for cond in a.conditions.split(","):
            LF.dev_post("/gen_inhibit_set?mode=%d" % (3 if cond == "armed" else 0))
            time.sleep(4.0)
            st0 = state(LF.page())
            print("== %s: %s" % (cond, {k: st0.get(k) for k in ("mode", "inhibit_live", "bus_on", "drv_state")}))
            for ch in a.channels.split(","):
                if ch == "tcp" and (cfg.get("protocol") != "slcan" or cfg.get("port_type") != "tcp"):
                    rec["results"].append({"cond": cond, "channel": ch,
                                           "skipped": "protocol/port_type is %s/%s" % (cfg.get("protocol"), cfg.get("port_type"))})
                    continue
                for name, text in COMMANDS:
                    pre = state(LF.page())
                    i0, e0 = len(wit.ids), wit.errors
                    reply = channel_send(ch, LF.HOST, int(cfg.get("port") or 3333), text)
                    time.sleep(0.6)
                    post = state(LF.page())
                    ids = sorted(set(wit.ids[i0:]))
                    j = judge(cond, pre, post, set(ids))
                    j.update(judge_counted(pre, post))
                    r = {"cond": cond, "channel": ch, "cmd": name, "reply": reply,
                         "witness_ids_between": [hex(x) for x in ids],
                         "witness_error_frames": wit.errors - e0,
                         "pre": {k: v for k, v in (pre or {}).items() if k != "keys"},
                         "post": {k: v for k, v in (post or {}).items() if k != "keys"}, **j}
                    rec["results"].append(r)
                    print("  %-5s %-3s %-4s reply %-14s -> %s %s | counted %s %s" % (
                        cond, ch, name, reply[:14], j["verdict"], "; ".join(j["why"]),
                        j["counted"], j.get("refused_delta", "")))
    finally:
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        if player is not None:
            player.stop_flag.set()
            player.join(timeout=15.0)
        if wit is not None:
            wit.stop.set()
            wit.join(timeout=3.0)
        for b in (pc, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        rec["after"] = state(LF.page())
    fails = [r for r in rec["results"] if r.get("verdict") == "FAIL"]
    rec["summary"] = {"commands": sum(1 for r in rec["results"] if "verdict" in r), "fail": len(fails),
                      "counted": sorted({r.get("counted") for r in rec["results"] if "counted" in r}),
                      "any_new_page_key": sorted({k for r in rec["results"] for k in r.get("new_page_keys", [])}),
                      "replies": sorted({r.get("reply") for r in rec["results"] if "reply" in r})}
    path = os.path.join(RUNS, "slcan_refusal_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    print("summary", rec["summary"])
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
