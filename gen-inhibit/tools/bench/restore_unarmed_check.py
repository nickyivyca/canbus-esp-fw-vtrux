"""After an AUTO-ARM image comes off the WiCAN: confirm on the wire that the restored
manual image is unarmed and silent.

Gen-inhibit tester, 2026-10-12. The reviewer's condition for an auto-arm image on the
bench: restore a manual image before releasing the lock and confirm it ON THE WIRE,
not from the page alone. (2026-10-10 did this inline: runs/restore_unarmed_check_
20261010T193803.json.)

The PCAN replays the first --seconds of a capture with real 0x051 traffic; the Kvaser
witnesses. judge() requires:
  - the page before and after: mode 0 and the driver not installed (drv null), and the
    expected elf_sha256 (--expect, 8 or more hex digits of it; /gen_inhibit names the image,
    spec 11; until 2026-10-25 this was git_version, from /check_status, which is never
    read: reviewer, 2026-10-20);
  - no WiCAN frame on the wire (0x7F0-0x7F3, 0x7F8, 0x7F9: probe and diag pages);
  - no 0x051 the PCAN did not send: the witness's 0x051 payloads, as a multiset, inside
    the sent ones. An answer steals the next counter, so its payload is one the replay
    sends later; with the witness seeing every copy, the count of that payload exceeds
    the replay's. This cannot fire spuriously -- a witness cannot see a payload more
    often than it was sent unless something else sent it. (A per-frame order check was
    tried and dropped the same day: it caught nothing the multiset did not, and could
    misfire when the witness missed 8 frames in a row.)
  - the witness saw >= WITNESS_MIN of the sent 0x051, else VOID: a deaf witness would
    pass every other test.

    GI_WICAN_SSID=... py -3.14 restore_unarmed_check.py --expect 3639f8b2 [--seconds 15]
Writes runs/restore_unarmed_check_<stamp>.json.
"""
import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RUNS = gi_paths.runs()
WICAN_IDS = {0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}
WITNESS_MIN = 0.95


def judge(sent, seen, before, after, expect):
    why = []
    for name, p in (("before", before), ("after", after)):
        if p is None:
            return "UNREAD", ["page %s not read" % name]
        if p.get("mode") != 0:
            why.append("mode %s %r" % (name, p.get("mode")))
        if p.get("drv") is not None:
            why.append("driver installed %s (%r)" % (name, p.get("drv")))
        sha = p.get("elf_sha256") or ""
        if not (len(expect) >= 8 and sha.startswith(expect.lower())):
            why.append("elf_sha256 %s %r, expected %r..." % (name, sha[:16], expect))
    wican = sorted({hex(i) for _t, i, _d in seen if i in WICAN_IDS})
    if wican:
        why.append("WiCAN frames on the wire: %s" % wican)
    n_sent = sum(1 for _t, i, _d in sent if i == 0x051)
    n_seen = sum(1 for _t, i, _d in seen if i == 0x051)
    if not Counter(d for _t, i, d in seen if i == 0x051) <= Counter(d for _t, i, d in sent if i == 0x051):
        why.append("a 0x051 payload on the wire more often than the replay sent it")
    if why:
        return "FAIL", why
    if not n_sent or n_seen < WITNESS_MIN * n_sent:
        return "VOID", ["witness saw %d of %d sent 0x051" % (n_seen, n_sent)]
    return "PASS", ["%d of %d sent 0x051 seen, none extra, no WiCAN frame" % (n_seen, n_sent)]


class Witness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.frames, self.stop = bus, [], threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is not None and not m.is_error_frame:
                self.frames.append((time.perf_counter(), m.arbitration_id, bytes(m.data)))


def page_state():
    import lowid_flood_skip_bench as LF
    p = LF.page()
    if p is None:
        return None
    return {"mode": p.get("mode"), "drv": p.get("drv"), "elf_sha256": p.get("elf_sha256"),
            "uptime_ms": p.get("uptime_ms"), "reset_reason": p.get("reset_reason")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", required=True, help="the restored image's elf_sha256, or 8+ hex digits of it")
    ap.add_argument("--capture", default="runs/scottsvalley_armable_300s.log")
    ap.add_argument("--seconds", type=float, default=15.0)
    a = ap.parse_args()
    import can
    cap = gi_paths.verify(a.capture if os.path.isabs(a.capture) else gi_paths.data_dir(a.capture))
    frames, t0 = [], None
    for m in can.LogReader(cap):
        t0 = m.timestamp if t0 is None else t0
        if m.timestamp - t0 >= a.seconds:
            break
        frames.append((m.timestamp - t0, m.arbitration_id, bytes(m.data), bool(m.is_extended_id)))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    before = page_state()
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
    wit = Witness(kv)
    sent = []
    try:
        wit.start()
        time.sleep(1.0)
        start = time.perf_counter()
        for off, cid, data, ext in frames:
            while time.perf_counter() - start < off:
                pass
            try:
                tx.send(can.Message(arbitration_id=cid, data=data, is_extended_id=ext), timeout=0.05)
                sent.append((time.perf_counter(), cid, data))
            except can.CanError:
                pass
        time.sleep(1.0)
    finally:
        wit.stop.set()
        wit.join(3)
        tx.shutdown()
        kv.shutdown()
    after = page_state()
    verdict, why = judge(sent, wit.frames, before, after, a.expect)
    rec = {"when": stamp, "expect": a.expect, "capture": os.path.basename(cap), "seconds": a.seconds,
           "before": before, "after": after, "sent": len(sent),
           "sent_0x051": sum(1 for _t, i, _d in sent if i == 0x051),
           "seen": len(wit.frames), "seen_0x051": sum(1 for _t, i, _d in wit.frames if i == 0x051),
           "seen_ids": sorted({hex(i) for _t, i, _d in wit.frames}), "verdict": verdict, "why": why}
    path = os.path.join(RUNS, "restore_unarmed_check_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    print("%s: %s" % (verdict, "; ".join(why)))
    print("wrote", path)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
