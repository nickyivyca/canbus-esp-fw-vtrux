"""
Test the inhibit-disable conditions (gen_inhibit mode 3), on the shared bench bus.

PCAN injects the stimulus; the WiCAN's /gen_inhibit self-report (tx_ok climbing,
`disabled`, `disable_reason`) is the truth. Two phases (run each on a FRESH boot,
since the disable latches until reboot):

  --phase soc
    1. arm, replay 0x051 -> tx_ok climbing (inhibiting)
    2. inject 0x411 with raw SoC = 0 (the startup sentinel) x10 -> must NOT
       disable, tx_ok still climbing
    3. inject 0x411 with SoC = 15% (below the 21.00% spec 6.2 threshold) x8
       -> disables (reason low_soc), tx_ok stops

  --phase mmode
    1. arm, replay 0x051 -> tx_ok climbing
    2. inject 0x639 shift_lever_pos = 4 (M) -> disables (reason m_mode), tx stops
    3. inject 0x639 shift_lever_pos = 3 (Drive) -> STILL disabled (latched)

Usage:
    py -3.12 gen-inhibit/tools/bench/wican_disable_bench.py --phase soc
    py -3.12 ...\\wican_disable_bench.py --phase mmode
"""

import argparse
import json
import os
import sys
import time
import urllib.request


# Reviews A1 and B3 added 0x440, 0x411, 0x617 and 0x639 to the arm gate, on
# top of spec 7.1's 0x592. The whole set lives in one place so the next
# addition does not have to be remembered five times.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_interlocks import send_interlocks, prime_interlocks   # noqa: E402

import can

HOST = "192.168.80.1"
BITRATE = 500000


def dev_get(path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=timeout) as r:
        return json.loads(r.read().decode())


def dev_post(path, timeout=8):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def soc_frame(pct_x100):
    """0x411 BMS_SoC_HiRes: raw=(B0<<6)|(B1>>2), percent=raw/100."""
    raw = pct_x100
    b0 = (raw >> 6) & 0xFF
    b1 = (raw & 0x3F) << 2
    return bytes([b0, b1, 0, 0, 0, 0, 0, 0])


def shift_frame(pos):
    """0x639: shift_lever_pos = (B6 >> 4) & 0x7."""
    return bytes([0, 0, 0, 0, 0, 0, (pos & 0x7) << 4, 0])


def open_pcan():
    for cfg in can.detect_available_configs(["pcan"]):
        if "Virtual" not in cfg.get("device_name", ""):
            return can.Bus(interface="pcan", channel=cfg["channel"], bitrate=BITRATE)
    raise RuntimeError("no PCAN")


# Spec 7.1 (2026-09-20): from DIAG_FW_VERSION 3 the device will not transmit
# unless 0x592 B0 bit 4 reads on AND is fresh, and never-seen reads OFF. Every
# "tx stops" check below would otherwise pass for the wrong reason -- on a bus
# with no key frame, transmission never starts. 0x592 EPRI_HCU_Sensor_0592,
# IgnitionKeyState = B0 bit 4.
KEY_ID = 0x592
KEY_ON = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])


def burst_051(bus, n, hz=100):
    period = 1.0 / hz
    for i in range(n):
        t = time.perf_counter()
        bus.send(can.Message(arbitration_id=0x051,
                             data=bytes([0x08, 0x00, 0x80, 0xFF, 0x7F, i & 0x0F]),
                             is_extended_id=False))
        # 0x411 withheld: this harness injects SoC itself, and a healthy
        # 20 Hz train would reset the low-SoC debounce every 50 ms, so the
        # release could never latch.
        send_interlocks(bus, i, skip=(0x411,))
        while time.perf_counter() < t + period:
            pass


def inject_with_051(bus, arb, data, count, betw_051=10):
    """Send `count` copies of one aux frame, each preceded by some 0x051 so the
    inhibitor stays active and the worker is processing frames."""
    for _ in range(count):
        burst_051(bus, betw_051)
        bus.send(can.Message(arbitration_id=arb, data=data, is_extended_id=False))
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=("soc", "mmode"))
    args = ap.parse_args()

    pcan = open_pcan()
    ok = True
    try:
        st = dev_get("/gen_inhibit")
        if st.get("disabled"):
            print("FAIL: device already disabled (reason %s) -- reboot before testing"
                  % st.get("disable_reason"))
            return 1

        dev_post("/gen_inhibit_set?mode=3")
        time.sleep(0.4)
        burst_051(pcan, 200)
        a = dev_get("/gen_inhibit")
        print("armed: tx_ok=%s disabled=%s" % (a["tx_ok"], a["disabled"]))

        if args.phase == "soc":
            print("\n[1] inject SoC=0 sentinel x10 (must NOT disable)")
            inject_with_051(pcan, 0x411, soc_frame(0), 10)
            s = dev_get("/gen_inhibit")
            tx_before = s["tx_ok"]
            print("    disabled=%s reason=%s soc_x100=%s tx_ok=%s"
                  % (s["disabled"], s["disable_reason"], s["soc_x100"], s["tx_ok"]))
            sentinel_ok = (not s["disabled"])

            print("\n[2] inject SoC=15%% (raw 1500) x8 (must disable, reason low_soc)")
            inject_with_051(pcan, 0x411, soc_frame(1500), 8)
            burst_051(pcan, 100)
            s = dev_get("/gen_inhibit")
            print("    disabled=%s reason=%s soc_x100=%s tx_ok=%s"
                  % (s["disabled"], s["disable_reason"], s["soc_x100"], s["tx_ok"]))
            tx_after = s["tx_ok"]
            burst_051(pcan, 200)
            s2 = dev_get("/gen_inhibit")
            latched = (s2["tx_ok"] == s["tx_ok"])   # no more tx after disable
            ok = (sentinel_ok and s["disabled"] and s["disable_reason"] == "low_soc"
                  and latched)
            print("\n  sentinel rejected: %s | latched low_soc: %s | tx frozen after: %s"
                  % (sentinel_ok, s["disabled"] and s["disable_reason"] == "low_soc",
                     latched))

        else:  # mmode
            print("\n[1] inject shift_lever_pos=4 (M) (must disable, reason m_mode)")
            inject_with_051(pcan, 0x639, shift_frame(4), 3)
            burst_051(pcan, 100)
            s = dev_get("/gen_inhibit")
            print("    disabled=%s reason=%s tx_ok=%s"
                  % (s["disabled"], s["disable_reason"], s["tx_ok"]))
            tx_at_disable = s["tx_ok"]

            print("\n[2] inject shift_lever_pos=3 (Drive) (must STAY disabled -- latch)")
            inject_with_051(pcan, 0x639, shift_frame(3), 3)
            burst_051(pcan, 200)
            s2 = dev_get("/gen_inhibit")
            print("    disabled=%s reason=%s tx_ok=%s"
                  % (s2["disabled"], s2["disable_reason"], s2["tx_ok"]))
            latched = s2["disabled"] and (s2["tx_ok"] == tx_at_disable)
            ok = (s["disabled"] and s["disable_reason"] == "m_mode" and latched)
            print("\n  latched through Drive: %s | tx frozen: %s"
                  % (s2["disabled"], s2["tx_ok"] == tx_at_disable))

        dev_post("/gen_inhibit_set?mode=0")
    finally:
        try:
            pcan.shutdown()
        except Exception:
            pass

    print("\n=== %s ===" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
