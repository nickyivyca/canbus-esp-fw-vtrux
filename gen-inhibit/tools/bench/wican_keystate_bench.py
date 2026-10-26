#!/usr/bin/env python3
"""Drive spec 7.1's key-state rules on hardware.

WHY THIS EXISTS. The existing benches cover section 6 and section 7 as they
stood before 2026-09-20. None of them turns the key: `wican_trip_bench.py`
holds it on throughout, which is right for what it tests and means the two
rules 7.1 actually adds have no hardware coverage at all. The host suite
covers them against an emulated worker loop; section 13 clears on bench proof.

FOUR PHASES, each on its own fresh boot -- every latch here survives until a
reboot or a key-on, so sharing a boot contaminates the next phase.

  gate        Rule 1. Live and transmitting, then the key reads OFF with
              everything else still flowing. Transmission must STOP, with no
              abort and no latch -- the device stays armed and silent. The key
              returns and it resumes.

  clear       Rule 2, the end-of-drive case finding 3 measured. Key off AND
              0x471 stops, which is what a real key-off looks like: the
              inverter sleeps while the VCM keeps talking. GI_ABORT_INVERTER_-
              LOST must latch. Then the key returns and the latch must clear,
              the gate re-run, and transmission resume. Also checks the device
              stayed in mode 3 through the abort rather than dropping to OFF,
              which is the structural change 7.1 required.

  stale       The distinction that keeps the bus-loss latch meaningful. M mode
              latches a section 6 release, then 0x592 simply STOPS while the
              key's last value was ON, and comes back still ON. Transmission
              stops on the staleness, but the latch must SURVIVE -- only an
              actual 0 followed by an actual 1 clears anything.

  clear6      The approved reading of "clears any latch": a key cycle clears
              the section 6 M-mode release too, and the device re-arms.

The device's own /gen_inhibit JSON is the authority for whether it
transmitted; `tx_ok` freezing is the measurement, the same way
`wican_disable_bench.py` does it. No Kvaser witness is needed -- nothing here
is about frame content or timing.

Bench only: PCAN on the desk segment, WiCAN at 192.168.80.1.
"""

import argparse
import json
import os
import sys
import time
import urllib.request


# Reviews A1 and B3 added 0x440, 0x411, 0x617 and 0x639 to the arm gate, on
# top of spec 7.1's 0x592. One definition for every harness.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_interlocks import send_interlocks   # noqa: E402

import can                                              # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HOST = "192.168.80.1"
BITRATE = 500000
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()

VCM_ID, INV_ID, KEY_ID, SOC_ID, SHIFT_ID = 0x051, 0x471, 0x592, 0x411, 0x639
KEY_ON = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])
KEY_OFF = bytes([0x00, 0, 0, 0, 0, 0, 0, 0])

# Interposer session, 2026-09-22: both dongles deliver a backlog in the first
# second after opening. Nothing here counts witness frames, but the settle
# costs two seconds and removes the question.
SETTLE_S = 2.0


def post(p):
    r = urllib.request.Request("http://%s%s" % (HOST, p), data=b"",
                               method="POST")
    urllib.request.urlopen(r, timeout=8).read()


def state():
    with urllib.request.urlopen("http://%s/gen_inhibit" % HOST, timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def reboot():
    try:
        post("/system_reboot")
    except Exception:
        pass            # the device drops the connection as it goes down
    time.sleep(14)
    for _ in range(10):
        try:
            state()
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("device did not come back after reboot")


def soc_frame(raw):
    """0x411 BMS_SoC_HiRes: 14-bit BE at bit 7."""
    return bytes([(raw >> 6) & 0xFF, (raw & 0x3F) << 2, 0, 0, 0, 0, 0, 0])


def shift_frame(pos):
    d = bytearray(8)
    d[6] = (pos & 0x07) << 4
    return bytes(d)


def stream(bus, seconds, key=None, inverter=True, shift=None, ctr=0, hz=100):
    """Hold the bus for `seconds`, returning the next counter value.

    key=None sends no 0x592 at all -- that is how the `stale` phase stops the
    key without changing its value. Otherwise KEY_ON / KEY_OFF at 10 Hz, the
    truck's measured rate.

    Everything else the arm gate needs -- 0x440 closed, 0x411, 0x617, 0x639 --
    comes from bench_interlocks at its measured rate. This function used to
    send 0x411 every 50th frame, i.e. at exactly 2 Hz: a 500 ms period against
    a 500 ms freshness window, which review B3's trip 5 would abort on, and
    which was already sitting on the boundary before B3 gave it teeth.
    """
    period = 1.0 / hz
    n = int(seconds * hz)
    # The key is this harness's whole subject, so it keeps it. 0x639 is handed
    # over only when a phase drives it.
    skip = [KEY_ID] + ([SHIFT_ID] if shift is not None else [])
    for i in range(n):
        t = time.perf_counter()
        bus.send(can.Message(
            arbitration_id=VCM_ID,
            data=bytes([0x08, 0x00, 0x80, 0xFF, 0x7F, (ctr + i) & 0x0F]),
            is_extended_id=False))
        send_interlocks(bus, i, skip=tuple(skip))
        if inverter and i % 10 == 0:
            bus.send(can.Message(arbitration_id=INV_ID, data=bytes(8),
                                 is_extended_id=False))
        if key is not None and i % 10 == 0:
            bus.send(can.Message(arbitration_id=KEY_ID, data=key,
                                 is_extended_id=False))
        if shift is not None and i % 20 == 0:
            bus.send(can.Message(arbitration_id=SHIFT_ID,
                                 data=shift_frame(shift), is_extended_id=False))
        while time.perf_counter() < t + period:
            pass
    return (ctr + n) & 0x0F


def open_pcan():
    for cfg in can.detect_available_configs(["pcan"]):
        if "Virtual" not in cfg.get("device_name", ""):
            bus = can.Bus(interface="pcan", channel=cfg["channel"],
                          bitrate=BITRATE)
            time.sleep(SETTLE_S)
            while bus.recv(timeout=0.0) is not None:
                pass
            return bus
    raise RuntimeError("no PCAN")


def show(tag, st):
    print("    %-10s live=%-5s mode=%s key_on=%-5s key_fresh=%-5s "
          "latched=%-5s disabled=%-5s tx_ok=%s"
          % (tag, st["inhibit_live"], st["mode"], st["key_on"],
             st["key_fresh"], st["abort_latched"], st["disabled"],
             st["tx_ok"]))
    return st


# ------------------------------------------------------------------ phases --

def phase_gate(bus):
    """Rule 1: the key gates transmission and nothing else."""
    post("/gen_inhibit_set?mode=3")
    c = stream(bus, 3.0, key=KEY_ON)
    a = show("key on", state())

    c = stream(bus, 3.0, key=KEY_OFF, ctr=c)
    b = show("key off", state())

    c = stream(bus, 3.0, key=KEY_ON, ctr=c)
    d = show("key back", state())

    # ONE frame is allowed through the key-off, and the allowance is not
    # slack. The inhibit is a reactive trail on 0x051, and stream() sends the
    # 0x051 for an iteration before the 0x592 -- so the first command of the
    # key-off window arrives while the key still genuinely reads ON, and
    # answering it is correct. Everything after it must be gated. Measured
    # 2026-09-22: exactly 1, against the 299 an ungated 3 s window produces.
    leaked = b["tx_ok"] - a["tx_ok"]
    ungated = d["tx_ok"] - b["tx_ok"]
    checks = {
        "transmitted while keyed on": a["tx_ok"] > 0,
        "transmission stopped within one frame of the key-off": leaked <= 1,
        "the key-off window is gated, not merely slower":
            leaked * 20 < ungated,
        "still armed and live through the key-off":
            b["inhibit_live"] and b["mode"] == 3,
        "no abort, no latch at key-off":
            not b["abort_latched"] and not b["abort_reason"],
        "resumed when the key came back": ungated > 0,
    }
    print("    gated window let %d frame(s) through; the same span keyed on"
          " transmitted %d" % (leaked, ungated))
    return checks, {"on": a, "off": b, "back": d}


def phase_clear(bus):
    """Rule 2 against the end-of-drive shape: key off, inverter sleeps."""
    post("/gen_inhibit_set?mode=3")
    c = stream(bus, 3.0, key=KEY_ON)
    a = show("live", state())

    # The real thing: key off, and 0x471 stops with it. 0x051 keeps running,
    # because on the truck the VCM outlives the key by a median 77 s.
    c = stream(bus, 3.0, key=KEY_OFF, inverter=False, ctr=c)
    b = show("latched", state())

    c = stream(bus, 4.0, key=KEY_ON, inverter=True, ctr=c)
    d = show("re-keyed", state())

    checks = {
        "transmitted before the key-off": a["tx_ok"] > 0,
        "inverter-lost LATCHED":
            b["abort_latched"] and "inverter lost" in b["abort_reason"],
        "stayed in mode 3 through the abort (7.1 structural change)":
            b["mode"] == 3,
        "key-on CLEARED the latch":
            not d["abort_latched"] and not d["abort_reason"],
        "re-armed and went live again": d["inhibit_live"],
        "transmitted again after the key-on": d["tx_ok"] > b["tx_ok"],
    }
    return checks, {"live": a, "latched": b, "rekeyed": d}


def phase_stale(bus):
    """A stale key gates transmission but must clear no latch."""
    post("/gen_inhibit_set?mode=3")
    c = stream(bus, 2.0, key=KEY_ON, shift=0)
    a = show("live", state())

    c = stream(bus, 2.0, key=KEY_ON, shift=4, ctr=c)     # M mode latches
    b = show("m_mode", state())

    # 0x592 simply stops. The key's last VALUE was on, so this is staleness,
    # not a key-off, and nothing may be cleared when it returns.
    c = stream(bus, 3.0, key=None, shift=0, ctr=c)
    m = show("key stale", state())

    c = stream(bus, 3.0, key=KEY_ON, shift=0, ctr=c)
    d = show("key back", state())

    checks = {
        "transmitted before M mode": a["tx_ok"] > 0,
        "M mode latched the section 6 release":
            b["disabled"] and b["disable_reason"] == "m_mode",
        "key went stale while its value stayed on":
            not m["key_fresh"] and m["key_on"],
        "latch SURVIVED the key returning": d["disabled"],
        "still not transmitting": d["tx_ok"] == b["tx_ok"],
    }
    return checks, {"live": a, "m_mode": b, "stale": m, "back": d}


def phase_clear6(bus):
    """The approved reading: a key cycle clears the section 6 latch too."""
    post("/gen_inhibit_set?mode=3")
    c = stream(bus, 2.0, key=KEY_ON, shift=0)
    a = show("live", state())

    c = stream(bus, 2.0, key=KEY_ON, shift=4, ctr=c)
    b = show("m_mode", state())

    # A real key cycle, with the lever back in Drive so nothing re-latches.
    c = stream(bus, 2.0, key=KEY_OFF, shift=3, ctr=c)
    o = show("key off", state())

    c = stream(bus, 3.0, key=KEY_ON, shift=3, ctr=c)
    d = show("key back", state())

    checks = {
        "M mode latched": b["disabled"] and b["disable_reason"] == "m_mode",
        "key-on CLEARED the section 6 latch": not d["disabled"],
        "re-armed and went live again": d["inhibit_live"],
        "transmitted again": d["tx_ok"] > o["tx_ok"],
    }
    return checks, {"live": a, "m_mode": b, "off": o, "back": d}


PHASES = [("gate", phase_gate), ("clear", phase_clear),
          ("stale", phase_stale), ("clear6", phase_clear6)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default=os.path.join(OUT_DIR,
                                                  "keystate_bench.json"))
    a = ap.parse_args()

    todo = [(n, f) for n, f in PHASES if not a.only or a.only in n]
    if not todo:
        print("no phase matched %r" % a.only)
        return 2

    # The image from /gen_inhibit's elf_sha256 (spec 11), never /check_status, which answers
    # with the device's config, credentials included (reviewer, 2026-10-20). Until 2026-10-25
    # this printed sta_mac (a MAC of the unit) and git from it.
    try:
        info = json.loads(urllib.request.urlopen(
            "http://%s/gen_inhibit" % HOST, timeout=8).read().decode())
        print("device elf_sha256 %s" % info.get("elf_sha256", "?"))
    except Exception as exc:                            # noqa: BLE001
        print("could not read /gen_inhibit: %s" % exc)
    results = {}
    ok_all = True

    for name, fn in todo:
        print("\n--- %s ---" % name)
        reboot()
        bus = open_pcan()
        try:
            checks, snaps = fn(bus)
        finally:
            try:
                post("/gen_inhibit_set?mode=0")
            except Exception:
                pass
            bus.shutdown()
        for k, v in checks.items():
            print("  [%s] %s" % ("PASS" if v else "FAIL", k))
        ok = all(checks.values())
        ok_all = ok_all and ok
        results[name] = {"ok": ok, "checks": checks, "snapshots": snaps}

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    with open(a.out, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
    print("\nwrote %s" % a.out)
    print("=== %s ===" % ("PASS -- spec 7.1 key-state rules hold on hardware"
                          if ok_all else "FAIL"))
    return 0 if ok_all else 2


if __name__ == "__main__":
    sys.exit(main())
