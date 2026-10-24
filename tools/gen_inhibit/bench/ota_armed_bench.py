#!/usr/bin/env python3
"""Spec 8.1 row 8 and 9.3, bench half (reviewer, 2026-10-16, pre-port batch 3): an
OTA upload while armed in INHIBIT on a live replay. Gen-inhibit tester.

Runs e4_device_calibration.py (PCAN replay, Kvaser witness, --arm-after) and
full_page_recorder.py beside it, waits until the page reads mode 3 with tx_ok
rising (a live inhibit), lets it run --lead s more, then uploads the WHOLE running
image over a raw socket (ota_guard_raw_probe.raw_post, every byte and its time
kept). Records; batch3_bench_judge.py judges (403 with the armed text, still armed
in the same arm and transmitting throughout, image, uptime and violation count
unchanged).

SAFETY: refused unless the device is in mode 0 and runs the image given (its
page's elf_sha256), so an upload that was wrongly taken could only re-install the
running build.

    REVERSE_IT_DIR=<reverse-it root> GI_WICAN_SSID=... py -3.14 ota_armed_bench.py --image <running .bin> [--seconds 40] [--lead 3]
Writes runs/ota_armed_<stamp>.json (+ the e4 run and the page recording beside it).
Tested by test_batch3_bench_judge.py (its record handling).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import failed_upload_probe as U   # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
P, BF = U.P, U.BF

CAPTURE = gi_paths.runs("scottsvalley_armable_300s.log")
LIVE_TX = 30


def wait_live(timeout, read=None, now=time.time, sleep=time.sleep):
    """The first page reading mode 3 with tx_ok >= LIVE_TX, or None by `timeout` s."""
    read = read or (lambda: P.G.gi_status(P.HOST)[0])
    end = now() + timeout
    while now() < end:
        pg = read() or {}
        if pg.get("mode") == 3 and isinstance(pg.get("tx_ok"), int) and pg["tx_ok"] >= LIVE_TX:
            return pg
        sleep(0.25)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="the .bin the device is running")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--lead", type=float, default=3.0, help="s of live inhibit before the upload")
    ap.add_argument("--capture", default=CAPTURE)
    args = ap.parse_args()
    data = open(args.image, "rb").read()
    image = {"file": os.path.basename(args.image), "bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "elf_sha256": BF.image_elf_sha256(args.image)}
    page0, err = P.G.gi_status(P.HOST)
    why = U.refuse(page0 or {}, image["elf_sha256"], 1, len(data))
    if why:
        raise SystemExit(why if page0 else err)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    runs = gi_paths.runs()
    e4_out = os.path.join(runs, "ota_armed_e4_%s.json" % stamp)
    pages_out = os.path.join(runs, "ota_armed_pages_%s.jsonl" % stamp)
    py = sys.executable
    rec = subprocess.Popen([py, "-B", os.path.join(HERE, "full_page_recorder.py"), "record",
                            "--seconds", str(args.seconds + 15), "--hz", "4", "--out", pages_out])
    time.sleep(1.0)
    e4 = subprocess.Popen([py, "-B", os.path.join(HERE, "e4_device_calibration.py"), "--capture", args.capture,
                           "--window-start", "0", "--seconds", str(args.seconds), "--arm", "idle",
                           "--mode", "inhibit", "--arm-after", "1", "--out", e4_out])
    out = {"when": stamp, "image": image, "e4": os.path.basename(e4_out), "pages": os.path.basename(pages_out),
           "lead_s": args.lead, "seconds": args.seconds}
    live = wait_live(args.seconds)
    out["live_page"] = live
    if live is None:
        out["upload"] = None
        out["abandoned"] = "no live inhibit (mode 3, tx_ok >= %d) within %s s" % (LIVE_TX, args.seconds)
    else:
        time.sleep(args.lead)
        body, ctype = P.multipart_file(data)
        up = P.raw_post("/upload/ota.bin", body, ctype, timeout=120.0, grace=2.0)
        up.update(U.raw_post_epochs(up))
        out["upload"] = up
    e4.wait(args.seconds + 300)
    rec.wait(60)
    out["e4_exit"], out["recorder_exit"] = e4.returncode, rec.returncode
    path = os.path.join(runs, "ota_armed_%s.json" % stamp)
    json.dump(out, open(path, "w"), indent=1)
    up = out.get("upload") or {}
    print("ota while armed: status %s, ending %s; e4 exit %s; wrote %s"
          % (up.get("status_line"), up.get("ending"), out["e4_exit"], path))


if __name__ == "__main__":
    main()
