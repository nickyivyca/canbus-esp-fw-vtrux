#!/usr/bin/env python3
"""Trace EVERY 0x7F1 the device emits during a stimulus run, with its flags.

WHY. `wican_diag_check.py` samples one 0x7F1 -- whichever it saw last -- and
asserts on its flags. Against the spec 7.1 build that reported bit6 `key_on`
clear while the device's own JSON reported `tx_ok=399`, i.e. transmission had
plainly been permitted. One of the two is measuring the wrong instant, and a
single sample cannot say which.

So: capture them all, print each with its offset from the start of the
stimulus and its flags decoded, and poll the JSON alongside. A flags value
that changes over the run means the check is sampling too early; one that
never sets bit6 means the flag itself is wrong.

Bench only -- needs PCAN and Kvaser on the desk segment and the WiCAN at
192.168.80.1.
"""

import json
import sys
import time
import urllib.request

import can                                              # noqa: E402
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

HOST = "192.168.80.1"
BITRATE = 500000
KEY_ID = 0x592
KEY_ON = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])
STATUS = 0x7F1
SETTLE_S = 2.0

FLAG_NAMES = ["disabled", "bus_on", "bus_ours", "autoarm",
              "susp", "live", "key_on", "latched"]


def post(p):
    r = urllib.request.Request("http://%s%s" % (HOST, p), data=b"",
                               method="POST")
    urllib.request.urlopen(r, timeout=8).read()


def get_json():
    with urllib.request.urlopen("http://%s/gen_inhibit" % HOST, timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def decode(flags):
    return ",".join(n for i, n in enumerate(FLAG_NAMES) if flags & (1 << i))


def flag_rows(frames, dec):
    """[(t, diag_flags)] of the 0x7F1 pages in frames [(t, id, data)], every diag page
    fed to the gi_diag Decoder in order, so each is read on its schema (reading Q7)."""
    rows = []
    for t, fid, data in frames:
        pg = dec.feed(fid, data, t=t, decode_choices=False, allow_truncated=True)
        if fid == STATUS and pg and "diag_flags" in pg:
            rows.append((t, int(pg["diag_flags"])))
    return rows


def main():
    # The decoder first: a working-tree DBC that is not HEAD's is refused before the bus opens.
    schemas = gi_diag.SchemaSet.from_git()
    IDENT = gi_diag.run_identity(schemas=schemas)
    DEC = gi_diag.Decoder(schemas, IDENT)
    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser"]):
        if "Virtual" not in cfg.get("device_name", ""):
            found.setdefault(cfg["interface"], cfg)
    pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"],
                   bitrate=BITRATE)
    kvaser = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"],
                     bitrate=BITRATE)

    # Interposer session, 2026-09-22: both dongles flush a backlog in the
    # first second after opening. Sleep, then drain.
    time.sleep(SETTLE_S)
    n = 0
    for b in (pcan, kvaser):
        while b.recv(timeout=0.0) is not None:
            n += 1
    print("settled, dropped %d backlog frames" % n)

    rows = []
    frames = []
    polls = []
    try:
        post("/gen_inhibit_set?mode=3")
        t0 = time.time()
        i = 0
        next_poll = t0 + 1.0
        while time.time() - t0 < 6.0:
            pcan.send(can.Message(arbitration_id=0x051,
                                  data=bytes([0x08, 0x00, 0x80, 0xFF, 0x7F,
                                              i & 0x0F]),
                                  is_extended_id=False))
            if i % 10 == 0:
                pcan.send(can.Message(arbitration_id=KEY_ID, data=KEY_ON,
                                      is_extended_id=False))
            i += 1
            m = kvaser.recv(timeout=0.005)
            while m is not None:
                if m.arbitration_id in DEC.ids:
                    frames.append((time.time() - t0, m.arbitration_id, bytes(m.data)))
                m = kvaser.recv(timeout=0.0)
            if time.time() >= next_poll:
                next_poll += 1.0
                d = get_json()
                polls.append((time.time() - t0, d.get("key_on"),
                              d.get("key_fresh"), d.get("inhibit_live"),
                              d.get("tx_ok")))
        post("/gen_inhibit_set?mode=0")
    finally:
        for b in (pcan, kvaser):
            try:
                b.shutdown()
            except Exception:
                pass

    # diag_flags by name, every diag page decoded in order by gi_diag against HEAD's DBC
    # (reading Q7, 2026-10-24; byte 3 by hand before). Another schema is refused.
    rows = flag_rows(frames, DEC)
    print("decoder: %s %s" % (IDENT, DEC.summary()))
    print("\n0x051 sent: %d, key frames: %d" % (i, (i + 9) // 10))
    print("\n%d x 0x7F1 captured:" % len(rows))
    for t, f in rows:
        print("  t=%6.3f  flags=0x%02X  %s" % (t, f, decode(f) or "-"))

    print("\nJSON polls (t, key_on, key_fresh, live, tx_ok):")
    for p in polls:
        print("  t=%6.3f  key_on=%-5s key_fresh=%-5s live=%-5s tx_ok=%s" % p)

    if rows:
        any_key = any(f & 0x40 for _, f in rows)
        print("\nbit6 key_on set in %d of %d status frames"
              % (sum(1 for _, f in rows if f & 0x40), len(rows)))
        print("VERDICT: %s" % ("the flag works; wican_diag_check.py sampled "
                               "the wrong instant" if any_key else
                               "bit6 never sets -- the flag itself is wrong"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
