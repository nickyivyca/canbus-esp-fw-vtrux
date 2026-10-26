#!/usr/bin/env python3
"""Tests bench_flash.py's preflight read of the device: it waits on /gen_inhibit
for a page with elf_sha256 and uptime_ms, and never asks for /check_status.

Gen-inhibit tester, 2026-10-20 (reviewer's ruling): /check_status answers with the
device's config, credentials included, and that answer reached Wi-Fi captures and
console logs. The preflight drops it, and with it the wifi_mode check and the
git/fw/hw_version line. The device, the network and the clock are faked; the fake
/check_status answers with a made-up value so that a request for it shows.

    py -3.14 test_bench_flash_preflight.py     # exit 0 only if every check passes
Its mutants: test_bench_flash_preflight_mutants.py.
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_flash as BF          # noqa: E402
import bench_ota_guard as G       # noqa: E402

FAILS = []
MADE_UP = "made-up-value-not-a-credential"
SHA = "ab" * 32


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
    """Answers /gen_inhibit from a list of (status, body), the last one repeating,
    and /check_status with a config-shaped page. Records every path asked for."""

    def __init__(self, pages):
        self.pages, self.paths = list(pages), []

    def http(self, host, path, method="GET", body=None, timeout=10.0):
        self.paths.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return 200, '{"wifi_mode":"AP","sta_pass":"%s","git_version":"v9"}' % MADE_UP
        if path.startswith("/gen_inhibit"):
            return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return 404, ""


def page(**kw):
    d = {"mode": 0, "elf_sha256": SHA, "uptime_ms": 4321, "reset_reason": "SW", "git_version": "v9"}
    d.update(kw)
    return 200, "{%s}" % ",".join('"%s":%s' % (k, "null" if v is None else
                                              ('"%s"' % v if isinstance(v, str) else v))
                                  for k, v in d.items())


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps += 1
        self.t += s


class FakeTime:
    """For main(): bench_flash's own clock, so a wait that runs out costs no real time."""

    def __init__(self):
        self.clk = Clock()
        self.time, self.sleep = self.clk.now, self.clk.sleep

    @staticmethod
    def strftime(fmt):
        return "20261020T000000"


@contextlib.contextmanager
def faked(dev):
    saved = (G.http, BF.http, BF.have_external_route, BF.wlan_state, BF.join_ap, BF.time)
    G.http = BF.http = dev.http
    BF.time = FakeTime()
    BF.have_external_route = lambda: (True, "198.51.100.7")
    BF.wlan_state = lambda: ("home", "connected")
    BF.join_ap = lambda ssid, timeout=40.0: (True, "associated")
    try:
        yield
    finally:
        G.http, BF.http, BF.have_external_route, BF.wlan_state, BF.join_ap, BF.time = saved


def wait(pages, timeout=10.0):
    dev, clk = Device(pages), Clock()
    with faked(dev):
        got, err = BF.wait_for_device("192.0.2.1", timeout=timeout, now=clk.now, sleep=clk.sleep)
    return got, err, dev, clk


def run_status(pages):
    """main() --status on the faked device: (exit code, stdout, paths asked)."""
    dev = Device(pages)
    argv, out = sys.argv, io.StringIO()
    sys.argv = ["bench_flash.py", "--status", "--ssid", "fake-ssid", "--host", "192.0.2.1"]
    try:
        with faked(dev), contextlib.redirect_stdout(out):
            rc = BF.main()
    except Exception as e:      # noqa: BLE001
        return None, "  CRASH %s: %s" % (type(e).__name__, e), dev.paths
    finally:
        sys.argv = argv
    return rc, out.getvalue(), dev.paths


def main():
    got, err, dev, clk = wait([page()])
    check("a page with elf_sha256 and uptime_ms is the device", lambda: got and got["elf_sha256"] == SHA
          and got["uptime_ms"] == 4321 and err is None, (got, err))
    check("waiting asks only for /gen_inhibit", lambda: dev.paths and set(dev.paths) == {"/gen_inhibit"}, dev.paths)

    got, err, dev, clk = wait([(503, ""), (200, "not json"), page()])
    check("a 503 and a non-JSON answer are waited through", lambda: got and got["elf_sha256"] == SHA
          and clk.sleeps == 2, (got, err, clk.sleeps))

    st, body = page()
    got, err, dev, clk = wait([(st, body + " junk")])
    check("a page with trailing bytes is read leniently (the image about to be replaced)",
          lambda: got and got["elf_sha256"] == SHA and "_trailing" in got, (got, err))

    got, err, dev, clk = wait([page(elf_sha256=None)])
    check("a page without elf_sha256 is not the device, and the error names it",
          lambda: got is None and "elf_sha256" in (err or "") and "/check_status" not in dev.paths, err)
    got, err, dev, clk = wait([page(uptime_ms=None)])
    check("a page without uptime_ms is not the device, and the error names it",
          lambda: got is None and "uptime_ms" in (err or ""), err)
    check("it gives up at the timeout", lambda: clk.t - 1000.0 <= 12.0 and clk.sleeps >= 4, (clk.t, clk.sleeps))

    rc, out, paths = run_status([page()])
    check("--status on an OFF device passes with no config read", lambda: rc == 0, out[-300:])
    check("--status never asks for /check_status", lambda: "/check_status" not in paths and paths, paths)
    check("nothing from the config reaches the output",
          lambda: MADE_UP not in out and "fw_version" not in out and "hw=" not in out, out[-300:])
    check("the output names the image and the boot from /gen_inhibit",
          lambda: SHA[:16] in out and "4321" in out, out[-300:])
    rc, out, paths = run_status([page(mode=1)])
    check("an armed device still fails the preflight (spec 9.3)",
          lambda: rc == 1 and "device is OFF" in out, out[-300:])
    rc, out, paths = run_status([page(elf_sha256=None)])
    check("--status fails when /gen_inhibit names no image", lambda: rc == 1 and "device answers" in out
          and "/check_status" not in paths, out[-300:])

    print("\n%s" % ("%d FAIL" % len(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
