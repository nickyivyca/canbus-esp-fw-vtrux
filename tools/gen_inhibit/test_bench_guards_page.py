#!/usr/bin/env python3
"""Tests the two guard benches' reads of the device: bench_ota_guard.py and
bench_flash_guard.py name the image in their record from /gen_inhibit, and the
flash guard's liveness probe asks /gen_inhibit too. Neither asks for /check_status.

Gen-inhibit tester, 2026-10-21. /check_status answers with the device's config,
credentials included (reviewer, 2026-10-20), and both guards read it until
2026-10-21: the record's "device" version in both, and the flash guard's
liveness probe after a closed connection in its accepted-when-OFF half.

The device and the network are faked. The fake /check_status answers 200 with a
made-up value, so a request for it shows in the paths asked for and in the
record. The AP-mode precondition is not judged here: it waits on the user's
ruling (2026-10-21), and the fake /load_config answers only so the OTA guard
gets past it.

    py -3.14 test_bench_guards_page.py     # exit 0 only if every check passes
Its mutants: test_bench_guards_page_mutants.py. GUARDS_DIR runs another copy of
the two guards (the mutants use it).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("GUARDS_DIR") or HERE)
import bench_ota_guard as G       # noqa: E402
import bench_flash_guard as F     # noqa: E402

FAILS = []
MADE_UP = "made-up-value-not-a-credential"
SHA = "ab" * 32
PAGE_ID = {"elf_sha256": SHA, "git_version": "1234567", "uptime_ms": 4321}


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not ok:
        FAILS.append(label)


class Device:
    """A WiCAN on the inhibitor build. Writers and OTA are refused 403 in any mode
    but OFF; when OFF a writer drops the connection (status None), as the three
    probed handlers do for an empty body. With die_after_off_probe, /gen_inhibit
    stops answering after the first OFF writer probe. Records every path."""

    def __init__(self, die_after_off_probe=False, page_up=True):
        self.mode, self.paths = 0, []
        self.die, self.page_up = die_after_off_probe, page_up

    def http(self, host, path, method="GET", body=None, ctype=None, **_kw):
        self.paths.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return 200, json.dumps({"git_version": "check-status-version", "sta_pass": MADE_UP})
        if path.startswith("/load_config"):
            return 200, '{"wifi_mode":"AP"}'
        if path.startswith("/gen_inhibit_set"):
            q = urllib.parse.parse_qs(path.split("?", 1)[1])
            self.mode = int(q["mode"][0])
            return 200, "{}"
        if path.startswith("/gen_inhibit"):
            if not self.page_up:
                return None, "timed out"
            return 200, json.dumps(dict(PAGE_ID, mode=self.mode, wifi_mode="AP"))
        if self.mode != 0:
            return 403, "refused: gen_inhibit owns the bus"
        if path.startswith("/upload/ota.bin"):
            return 400, "invalid image"
        if self.die:
            self.page_up = False
        return None, "RemoteDisconnected: closed"


def run(mod, argv, dev):
    """mod.main() with the device and network faked; (rc, stdout). The flash
    guard keeps its rows in module lists, emptied here so each run is its own."""
    del F.records[:], F.fails[:]
    saved = {}
    for m in (G, F):
        for name, new in (("http", dev.http), ("have_external_route", lambda: (True, "198.51.100.7"))):
            saved[(m, name)] = getattr(m, name)
            setattr(m, name, new)
    out, old_argv = io.StringIO(), sys.argv
    sys.argv = ["x"] + argv
    try:
        with contextlib.redirect_stdout(out):
            try:
                rc = mod.main()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = old_argv
        for (m, name), v in saved.items():
            setattr(m, name, v)
    return rc, out.getvalue()


def record(d, name):
    with open(os.path.join(d, name), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    work = os.path.join(tempfile.gettempdir(), "gen-inhibit-guards-page")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    os.environ[G.RUNS_ENV] = work

    print("=== bench_ota_guard.py ===")
    dev = Device()
    rc, out = run(G, ["--host", "192.0.2.1"], dev)
    check("ota: the run passes on a device that refuses", lambda: rc == 0, "rc %r; %s" % (rc, out[-120:]))
    check("ota: /check_status never asked for", lambda: "/check_status" not in dev.paths, sorted(set(dev.paths)))
    rec = record(work, "bench_ota_guard.json")
    check("ota: the record names the image from /gen_inhibit", lambda: rec.get("device") == PAGE_ID, rec.get("device"))
    check("ota: no made-up value in the record", lambda: MADE_UP not in json.dumps(rec) and
          "check-status-version" not in json.dumps(rec))
    down = Device(page_up=False)

    def ident_down():
        saved, G.http = G.http, down.http
        try:
            return G.device_identity("192.0.2.1")
        finally:
            G.http = saved
    check("ota: device_identity is None when the page does not answer", lambda: ident_down() is None)
    check("ota: ... and does not fall back to /check_status", lambda: "/check_status" not in down.paths, down.paths)

    print("\n=== bench_flash_guard.py ===")
    dev = Device()
    out_path = os.path.join(work, "bench_flash_guard.json")
    rc, out = run(F, ["--host", "192.0.2.1", "--out", out_path, "--check-accepted"], dev)
    check("flash: the run passes on a device that refuses", lambda: rc == 0, "rc %r; %s" % (rc, out[-160:]))
    check("flash: /check_status never asked for", lambda: "/check_status" not in dev.paths, sorted(set(dev.paths)))
    rec = record(work, "bench_flash_guard.json")
    check("flash: the record names the image from /gen_inhibit", lambda: rec.get("device") == PAGE_ID, rec.get("device"))
    check("flash: no made-up value in the record", lambda: MADE_UP not in json.dumps(rec) and
          "check-status-version" not in json.dumps(rec))
    off = [r for r in rec.get("rows", []) if "reached the handler when OFF" in r.get("check", "")]
    check("flash: three OFF rows, each reached with the device alive",
          lambda: len(off) == 3 and all(r["ok"] and r.get("device_alive") is True for r in off),
          [(r.get("endpoint"), r["ok"], r.get("device_alive")) for r in off])

    dev = Device(die_after_off_probe=True)
    os.remove(out_path)
    rc, out = run(F, ["--host", "192.0.2.1", "--out", out_path, "--check-accepted"], dev)
    rec = record(work, "bench_flash_guard.json")
    off = [r for r in rec.get("rows", []) if "reached the handler when OFF" in r.get("check", "")]
    check("flash: a device that stops answering fails the OFF rows",
          lambda: rc == 1 and len(off) == 3 and all(not r["ok"] and r.get("device_alive") is False for r in off),
          "rc %r; %s" % (rc, [(r.get("endpoint"), r["ok"], r.get("device_alive")) for r in off]))
    check("flash: ... asked of /gen_inhibit, not /check_status", lambda: "/check_status" not in dev.paths,
          sorted(set(dev.paths)))

    shutil.rmtree(work, ignore_errors=True)
    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
