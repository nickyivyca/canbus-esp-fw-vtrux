"""Which boards are up, and which segment does each adapter see? Read off the wire.

Gen-inhibit tester, 2026-10-07. Run at the start of a bench session, before
flashing anything, so the board state recorded in firmware/bench-holder.md comes
from the wire rather than from a report.

Each adapter is opened ON ITS OWN, one after another, for --seconds, and
nothing is transmitted. Listen-only is applied by the mechanism each backend
actually honours (tools-reference.md: `listen_only=True` is swallowed):
  Kvaser  NORMAL, labelled ACKING. This Leaf Light v2 has NO silent mode
          (driver channel caps 0x000000C1; 2026-10-09): opened with
          driver_mode=False it still ACKed, so every census row before that
          date labelled "silent (driver_mode=False)" was in fact ACKING.
  PCAN    state=BusState.PASSIVE (python-can 4.6.1 has no LISTEN_ONLY member;
          PcanBus maps PASSIVE to the PCAN listen-only parameter)
  gs_usb  python-can 4.6 exposes no listen-only mode, so the Innomaker opens in
          NORMAL mode and ACKs. Its row is labelled ACKING, never passive.
A silent adapter's "0 error frames" says nothing about the segment
(tools-reference.md).

Per adapter it prints the ID census, the WiCAN diag pages decoded against
vtrux-wican-diag.dbc (schema, fw_ver, flags, git hash), and the raw 0x7F4-0x7F7
payloads so the interposer side can identify its own board with its own tool.

Usage (project root):
  py -3.14 tools/gen_inhibit/bench/bench_census.py [--seconds 8] [--only kvaser,pcan,gs_usb]
Writes runs/bench_census_<stamp>.json (never overwrites).
"""
import argparse
import collections
import json
import os
import sys
import time


import can                                                      # noqa: E402
import cantools                                                 # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
DBC = gi_paths.diag_dbc()
BITRATE = 500000
WICAN_IDS = (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
X2_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)


def fnv1a32(s):
    h = 0x811C9DC5
    for b in s.encode("ascii"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def open_adapter(name):
    if name == "kvaser":
        return can.Bus(interface="kvaser", channel=0, bitrate=BITRATE,
                       driver_mode=True), "ACKING (normal; this Leaf Light v2 has no silent mode)"
    if name == "pcan":
        return can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                       bitrate=BITRATE,
                       state=can.BusState.PASSIVE), \
            "listen-only (state=BusState.PASSIVE)"
    if name == "gs_usb":
        try:
            import libusb_package
            import usb.backend.libusb1 as lb
            lb.get_backend(find_library=libusb_package.find_library)
        except Exception:
            pass
        return can.Bus(interface="gs_usb", channel="can0", index=0,
                       bitrate=BITRATE), "ACKING (normal mode, no listen-only)"
    raise ValueError(name)


def census(name, seconds, db):
    out = {"adapter": name}
    try:
        bus, how = open_adapter(name)
    except Exception as e:
        out["error"] = "open failed: %r" % (e,)
        return out
    out["mode"] = how
    ids = collections.Counter()
    errs = 0
    last = {}
    first_t = last_t = None
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            m = bus.recv(timeout=0.1)
            if m is None:
                continue
            if m.is_error_frame:
                errs += 1
                continue
            ids[m.arbitration_id] += 1
            last[m.arbitration_id] = bytes(m.data)
            first_t = first_t if first_t is not None else m.timestamp
            last_t = m.timestamp
    finally:
        bus.shutdown()
    out["frames"] = sum(ids.values())
    out["error_frames"] = errs
    out["seconds"] = seconds
    out["ids"] = {"0x%03X" % k: v for k, v in sorted(ids.items())}
    wican = {}
    for fid in WICAN_IDS:
        if fid in last:
            try:
                d = db.decode_message(fid, last[fid], decode_choices=False)
                wican["0x%03X" % fid] = {k: (int(v) if float(v).is_integer()
                                             else float(v))
                                         for k, v in d.items()}
            except Exception as e:
                wican["0x%03X" % fid] = "decode failed: %r" % (e,)
    out["wican_last_decoded"] = wican
    out["x2_last_raw"] = {"0x%03X" % f: last[f].hex() for f in X2_IDS
                          if f in last}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--only", default="kvaser,pcan,gs_usb")
    a = ap.parse_args()
    db = cantools.database.load_file(DBC)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "dbc": DBC, "adapters": []}
    for name in a.only.split(","):
        print("--- %s, %.0f s" % (name, a.seconds))
        r = census(name, a.seconds, db)
        rec["adapters"].append(r)
        if "error" in r:
            print("  %s" % r["error"])
            continue
        print("  mode %s: %d frames, %d error frames, %d ids"
              % (r["mode"], r["frames"], r["error_frames"], len(r["ids"])))
        print("  ids: %s" % ", ".join("%s:%d" % kv for kv in r["ids"].items()))
        for fid, d in r["wican_last_decoded"].items():
            print("  wican %s %s" % (fid, d))
        b = r["wican_last_decoded"].get("0x7F3")
        if isinstance(b, dict) and "diag_git_hash" in b:
            print("  0x7F3 hash 0x%08X" % b["diag_git_hash"])
        for fid, raw in r["x2_last_raw"].items():
            print("  x2 %s raw %s" % (fid, raw))
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "bench_census_%s.json" % stamp)
    with open(path, "x", encoding="ascii") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("wrote %s" % path)
    for sha in ("3373f49", "6771edf", "2dafff9", "aaab89a"):
        print("  reference fnv1a32(%s) = 0x%08X" % (sha, fnv1a32(sha)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
