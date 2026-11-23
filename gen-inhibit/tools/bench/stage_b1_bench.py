#!/usr/bin/env python3
"""Stage (b) batch 1 on the bench: spec 9.4's regression set and the OTA requirements of
9.2 items 4-6 and shared/wican W15, on one image, in one sequence. Gen-inhibit tester,
2026-11-23.

Steps (--steps, default all, in this order):
  load     an end-to-end OTA of --image through the pinned core's flasher (gi_ota.py), even
           when the device already runs it, so the image boots fresh in PENDING_VERIFY.
  boot     a clean boot: the page answers within 90 s of the load, running --image, mode 0,
           reset_reason, worker and uptime recorded.
  gate     the health gate reaching its decision (spec 9.1, 9.4), seen from the client, with
           no console: the page is read every 5 s until uptime passes 75 s (the gate decides
           by 60 s at the latest); every read must show --image with uptime rising, so no
           rollback restart happened. Then wican_reboot.py --want: after a software restart
           the device is still on --image. An image left PENDING_VERIFY would have rolled back
           at that boot, so this shows the gate marked it valid.
  w15      wican_fw_version_check.py: the mDNS `fimrware` entry against the descriptor
           (the status-page half NOT RUN: /check_status barred).
  stall, restart, fin, rst, full
           ota_failpaths_bench.py, one kind each (W13, W12's bench consequence, W14 on every
           kind's ACKs, and `full` a second end-to-end OTA by a plain client).

The bench lock must be the gen-inhibit tester's (bench_held.py; vtrux_bench on PYTHONPATH);
refused otherwise. The WiCAN's SSID is read from bench-holder.md as the suite reads it, set
in the children's environment only, and masked as <WICAN_SSID> in every file written.
The settings are the chain runner's settings file (its ota block, for gi_wifi/gi_ota).

    python3.13 -B stage_b1_bench.py --settings <chain settings .json> --image <.bin>
        --run-dir <folder> [--steps load,boot,gate,...]
Writes <run-dir>/<step>.{txt,json} and summary.json. Exit 0 every step passed, 1 a step
failed, 2 refused. Run in bench.slice.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

STEPS = ("load", "boot", "gate", "w15", "stall", "restart", "fin", "rst", "full")
KINDS = ("stall", "restart", "fin", "rst", "full")


def watch_gate(page_fn, sha, limit=150.0, period=5.0, until_ms=75000, clock=time.time, sleep=time.sleep):
    """(ok, why, reads): the page read every `period` s until uptime passes until_ms. Every read
    must show `sha` with uptime rising; a read that fails is recorded and retried."""
    reads = []
    end = clock() + limit
    while clock() < end:
        try:
            pg = page_fn()
        except Exception as e:      # noqa: BLE001
            reads.append({"epoch": clock(), "error": str(e)})
            sleep(period)
            continue
        reads.append(dict(pg, epoch=clock()))
        if pg.get("elf_sha256") != sha:
            return False, "the device left the image (rollback?): %s" % str(pg.get("elf_sha256"))[:8], reads
        ups = [x["uptime_ms"] for x in reads if isinstance(x.get("uptime_ms"), int)]
        if len(ups) > 1 and ups[-1] < ups[-2]:
            return False, "uptime fell %s -> %s ms: a restart before the reboot" % (ups[-2], ups[-1]), reads
        if isinstance(pg.get("uptime_ms"), int) and pg["uptime_ms"] >= until_ms:
            return True, "", reads
        sleep(period)
    return False, "uptime never reached %d ms in %.0f s" % (until_ms, limit), reads


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--steps", default=",".join(STEPS))
    a = ap.parse_args(argv)
    steps = [s for s in a.steps.split(",") if s]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        print("REFUSED: unknown steps %s" % bad)
        return 2
    import bench_held
    ours, who = bench_held.held_by_us()
    if not ours:
        print("REFUSED: the bench lock is not the gen-inhibit tester's (%s)" % who)
        return 2
    import suite_gen_inhibit as SG
    import image_elf_sha as IMG
    import ota_failpaths_bench as FP
    settings = json.load(open(a.settings, encoding="utf-8"))
    root = os.environ.get("REVERSE_IT_DIR") or ""
    ssid = SG.wican_ssid(os.path.join(root, "firmware", "bench-holder.md"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GI_WICAN_SSID=ssid)
    env.update(SG.ota_tools_env(settings, ssid))
    sha = IMG.read_file(a.image)["elf_sha256"]
    os.makedirs(a.run_dir, exist_ok=True)
    mask = lambda t: t.replace(ssid, "<WICAN_SSID>") if ssid else t
    summary = {"image": os.path.basename(a.image), "elf_sha256": sha, "steps": {}, "epoch_start": time.time()}

    def tool(step, argv, timeout=900):
        t = time.time()
        p = subprocess.run([sys.executable, "-B"] + argv, capture_output=True, text=True, env=env, timeout=timeout)
        with open(os.path.join(a.run_dir, step + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(mask("$ %s\n[exit %d, %.1f s]\n%s\n%s" % (" ".join(os.path.basename(x) for x in argv[:1]) + " "
                         + " ".join(argv[1:]), p.returncode, time.time() - t, p.stdout, p.stderr)))
        return p.returncode

    def note(step, ok, detail, **kw):
        summary["steps"][step] = dict(kw, ok=ok, detail=detail, epoch=time.time())
        print("%-5s %-8s %s" % ("PASS" if ok else "FAIL", step, mask(str(detail))))

    for step in steps:
        if step == "load":
            rc = tool("load", [os.path.join(HERE, "gi_ota.py"), "--bin", a.image,
                               "--record", os.path.join(a.run_dir, "load.json")])
            summary["epoch_loaded"] = time.time()
            note("load", rc == 0, "gi_ota.py exit %d (0: TOOK and read back)" % rc)
            if rc != 0:
                break
        elif step == "boot":
            since = summary.get("epoch_loaded", time.time())
            r = FP.page_until(FP.HOST, since, 90)
            pg = r.get("page") or {}
            ok = r["ok"] and pg.get("elf_sha256") == sha and pg.get("mode") == 0
            note("boot", ok, "page in %s s: elf %s, mode %s, worker %s, reset %s, uptime %s ms"
                 % (r.get("delay"), str(pg.get("elf_sha256"))[:8], pg.get("mode"), pg.get("worker"),
                    pg.get("reset_reason"), pg.get("uptime_ms")), page=pg)
        elif step == "gate":
            ok, why, reads = watch_gate(lambda: FP.page(FP.HOST), sha)
            with open(os.path.join(a.run_dir, "gate_reads.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(reads, f, indent=1)
            if ok:
                rc = tool("gate", [os.path.join(HERE, "wican_reboot.py"), "--label", "stage-b1-gate",
                                   "--out", os.path.join(a.run_dir, "gate_reboot.json"),
                                   "--known", "b1=%s" % sha, "--want", "b1"])
                ok, why = rc == 0, ("past 75 s on the image with uptime rising (%d reads); after a software "
                                    "restart still on it: wican_reboot.py exit %d" % (len(reads), rc))
            note("gate", ok, why)
        elif step == "w15":
            rc = tool("w15", [os.path.join(HERE, "wican_fw_version_check.py"), "--image", a.image,
                              "--out", os.path.join(a.run_dir, "w15.json")])
            note("w15", rc == 0, "wican_fw_version_check.py exit %d (mDNS half; the page half NOT RUN)" % rc)
        elif step in KINDS:
            rc = tool(step, [os.path.join(HERE, "ota_failpaths_bench.py"), "--kind", step, "--image", a.image,
                             "--out", os.path.join(a.run_dir, step + ".json")], timeout=600)
            note(step, rc == 0, "ota_failpaths_bench.py --kind %s exit %d" % (step, rc))
            time.sleep(3)
    summary["epoch_end"] = time.time()
    with open(os.path.join(a.run_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(mask(json.dumps(summary, indent=1)))
    return 0 if all(s["ok"] for s in summary["steps"].values()) and len(summary["steps"]) == len(steps) else 1


if __name__ == "__main__":
    sys.exit(main())
