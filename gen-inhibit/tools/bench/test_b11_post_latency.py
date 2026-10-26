"""Harness test for b11_post_latency.py's per-POST rows (no records, no device).

    py -3.14 gen-inhibit/tools/bench/test_b11_post_latency.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import b11_post_latency as B                                       # noqa: E402


def _live_wire(gd):
    v = gd.schemas_for().head_version
    return [(0.0, 0x7F1, bytes([v, 1, 3, 0x26, 0, 0, 0, 0])), (1.0, 0x7F1, bytes([v, 1, 3, 0x06, 0, 0, 0, 0])),
            (1.5, 0x7F2, bytes(8)), (2.0, 0x7F1, bytes([v, 1, 3, 0x66, 0, 0, 0, 0])), (2.5, 0x051, bytes(6))]


def _raises_cls(fn, cls):
    try:
        fn()
    except cls:
        return True
    except Exception:       # noqa: BLE001
        return False
    return False


def main():
    # INHIBIT at 100 Hz (10 ms) to 10.2 s, OBSERVE POST sent at 10.0 s (reply 0.15 s
    # later), the last 0x051 at 10.2, a diag frame at 10.25, back to INHIBIT at 20.0 s
    # (reply 0.05 s), the first 0x051 at 20.03.
    # Before 9.0 s a slower 50 ms train, so only the gaps just before the send read 10 ms.
    cmd = ([round(-1.0 + 0.05 * k, 3) for k in range(200)] + [round(9.0 + 0.01 * k, 3) for k in range(121)]
           + [20.03, 20.04])
    anyf = sorted(cmd + [10.25])
    obs = {"at_s": 10.0, "mode": 1, "http": 200, "post_s": [10.0, 10.15]}
    back = {"at_s": 20.0, "mode": 3, "http": 200, "post_s": [20.0, 20.05]}
    rows = B.post_rows([(10.0, 1, obs), (20.0, 3, back)], cmd, anyf, [], 30.0)
    o, b = rows
    # a raw POST (887c000 shape): reply = first byte, 13 ms after the send
    raw = {"at_s": 7.0, "mode": 3, "http": 200, "post_s": [7.03, 9.06],
           "raw": {"t_start": 7.03, "t_connected": 7.04, "t_sent": 7.041, "t_first_byte": 7.054,
                   "t_complete": 7.06, "raw": 'HTTP/1.1 200 OK\r\n\r\n{"mode":3,"uptime_ms":5000,"inhibit_live":false,'
                                             '"arm_block":"receive queue not drained"}'}}
    rr = B.post_rows([(7.0, 3, raw)], [7.1], [7.1], [], 9.0)[0]
    LIVE_WIRE = _live_wire(B.gi_diag)
    checks = [
        # live pages via gi_diag (toolkit item 10, reading Q7, 2026-10-24)
        ("live pages: 0x7F1 with diag_flags bit5 set, decoded on its schema",
         B.live_page_times(LIVE_WIRE)[0] == [0.0, 2.0]),
        ("live pages: a 0x7F1 on a schema no DBC states raises, never read by hand",
         _raises_cls(lambda: B.live_page_times(LIVE_WIRE + [(3.0, 0x7F1, bytes([0xEE, 1, 3, 0x26, 0, 0, 0, 0]))]),
                     B.gi_diag.UnknownSchema)),
        ("OBSERVE: send -> reply is the POST's duration", o["send_to_reply_ms"] == 150.0),
        ("OBSERVE: the last 0x051 after the send, not before it", o["last_0x051_after_send_ms"] == 200.0),
        ("OBSERVE: the last frame of any device id (the diag frame after the last 0x051)",
         o["last_frame_after_send_ms"] == 250.0),
        ("OBSERVE: reply -> last frame is send -> last minus the reply", o["reply_to_last_frame_ms"] == 100.0),
        ("OBSERVE: the span ends at the next POST (the back-to-INHIBIT frames are not counted)",
         o["n_0x051_after_send"] == 21),
        ("OBSERVE: the 0x051 period just before the send (its last 20 gaps), not the whole arm's",
         o["period_0x051_before_ms"] == 10.0),
        ("back to INHIBIT: the first 0x051 after the send", b["first_0x051_after_send_ms"] == 30.0),
        ("back to INHIBIT: reply -> first 0x051", b["reply_to_first_0x051_ms"] == -20.0),
        ("raw POST: reply is the first byte, not post_s's end",
         rr["raw"]["send_to_first_byte_ms"] == 13.0 and rr["reply_to_first_0x051_ms"] == 100.0 - 13.0),
        ("raw POST: connect and the reply page are read",
         rr["raw"]["connect_ms"] == 10.0 and rr["raw"]["reply_page"]["uptime_ms"] == 5000
         and rr["raw"]["reply_page"]["arm_block"] == "receive queue not drained"),
        ("a POST with nothing after it reads None, not 0",
         B.post_rows([(10.0, 1, obs)], [1.0, 2.0], [1.0, 2.0], [], 30.0)[0]["last_frame_after_send_ms"] is None),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
