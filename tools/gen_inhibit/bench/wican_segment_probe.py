"""Which adapters share the WiCAN's segment, and what build does it name on the wire?

Gen-inhibit tester, 2026-10-07. Sets mode 3 (INHIBIT) with no 0x051 on the bus,
so the arm gate stays blocked and the device transmits only its diag pages
(the same trick as diag_build_page_check.py), then runs bench_census over each
adapter with listen-only applied by mechanism, then restores mode 0 in a
finally. An adapter that sees 0x7F1-0x7F3 shares the WiCAN's segment.
Writes runs/wican_segment_probe_<stamp>.json via bench_census.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_census as BC                                       # noqa: E402
import cantools                                                 # noqa: E402

HOST = "192.168.80.1"


def post_mode(m):
    req = urllib.request.Request("http://%s/gen_inhibit_set?mode=%d&offset=500"
                                 % (HOST, m), data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status


def page():
    with urllib.request.urlopen("http://%s/gen_inhibit" % HOST, timeout=8) as r:
        return json.load(r)


def main():
    db = cantools.database.load_file(BC.DBC)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "adapters": []}
    try:
        rec["post_mode3"] = post_mode(3)
        time.sleep(2.0)
        p = page()
        rec["page_mode3"] = {k: p.get(k) for k in
                             ("mode", "bus_on", "bus_ours", "drv", "inhibit_live",
                              "arm_block", "abort_reason")}
        print("page in mode 3:", json.dumps(rec["page_mode3"])[:400])
        for name in ("kvaser", "pcan", "gs_usb"):
            r = BC.census(name, 6.0, db)
            rec["adapters"].append(r)
            print("--- %s %s: %s frames, ids %s" % (name, r.get("mode", r.get("error")),
                  r.get("frames"), r.get("ids")))
            for fid, d in (r.get("wican_last_decoded") or {}).items():
                print("   ", fid, d)
    finally:
        rec["post_mode0"] = post_mode(0)
        time.sleep(1.0)
        p = page()
        rec["page_after"] = {k: p.get(k) for k in ("mode", "bus_on", "drv")}
        print("restored:", rec["page_after"])
    path = os.path.join(BC.RUNS, "wican_segment_probe_%s.json" % stamp)
    with open(path, "x", encoding="ascii") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("wrote", path)
    print("expected 0x7F3 hash for aaab89a: 0x%08X" % BC.fnv1a32("aaab89a"))


if __name__ == "__main__":
    sys.exit(main())
