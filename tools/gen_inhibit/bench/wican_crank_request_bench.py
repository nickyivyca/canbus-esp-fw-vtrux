"""Does the WiCAN hold B0 at 0x08 when the VCM asks for the generator?

Written 2026-09-20. Spec 4.1 changed B0 from mirrored to **held at 0x08**, and
says plainly what that change does not have behind it: the truck test that
produced INHIBIT HELD mirrored B0, so holding is a change to the one
configuration ever proven on the vehicle. It names two risks. One of them --
a fault raised by commanding the engine-off state mid-crank-request -- needs a
real VCM and cannot be tested here. The other, and the mechanics of the hold
itself, can.

`wican_inhibit_bench.py` does not cover this: the stream it replays is real
VCM traffic in which B0 never leaves 0x08, so the device is never asked to
hold against a generator request. This synthesises one.

Four phases, replayed at 100 Hz on the PCAN with the Kvaser witnessing:

  A  B0 = 0x08   engine off              -> transmits, B0 0x08, zero torque
  B  B0 = 0x0B   GENERATOR REQUESTED     -> STILL transmits, STILL B0 0x08
  C  B0 = 0x10   shutdown commanded      -> transmits NOTHING (spec 6.3)
  D  B0 = 0x08   back to engine off      -> resumes; 6.3 must not latch

Assertions, all of which are spec text rather than opinion:

  1. every WiCAN frame carries B0 = 0x08 -- never 0x0B, and never 0x10, which
     spec 4.1 says must never be transmitted at all;
  2. every WiCAN frame is zero torque;
  3. every WiCAN frame steals the next counter;
  4. phase C produces no WiCAN frames;
  5. phase D resumes -- a 6.3 suppression is not a latch;
  6. the device does not report itself disabled at any point, and ends with
     tx_fail = 0 and ctr_bad = 0.

Payloads are real 0x051 frames from a capture with B0 and the counter
overwritten, so everything except the byte under test is traffic the truck
actually produced.

Counting discipline: the bench Kvaser adds frames on burst-shaped traffic
(measured 2026-09-20, see diag_duplicate_crosscheck.py and the interposer
session's dual_listener_duplicates.py). At the 10 ms spacing used here it has
never been observed to do so, but counts are de-duplicated on
(id, payload, within 5 ms) anyway and the script reports whether that changed
anything -- a check reported only when it fires is not a check.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

# Reviews A1 and B3 added 0x440, 0x411, 0x617 and 0x639 to the arm gate, on top
# of spec 7.1's 0x592. The whole set lives in one place so the next addition
# does not have to be remembered five times; see bench_interlocks.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_interlocks import send_interlocks, prime_interlocks   # noqa: E402


import can
import cantools
from canre.parsers import parse_file


# --- dongle backlog ------------------------------------------------------
#
# Reported by the interposer session and measured on BOTH dongles
# (2026-09-22): after the bus has been idle, the first second of a freshly
# opened handle delivers a backlog -- 37 frames against a true rate of 4/s --
# and it is then exact. Counting from the instant of the open therefore
# inflates a witness count, which reads as a better pass rather than a
# failure. A non-blocking drain at t=0 does not fix it: the backlog arrives
# over about a second, so there is nothing to drain yet.
#
# Sleep, then drain. Two seconds costs nothing next to a bench run.
SETTLE_S = 2.0


def settle(*buses, seconds=SETTLE_S):
    """Let any backlog arrive, then throw it away. Returns what was dropped."""
    time.sleep(seconds)
    dropped = 0
    for b in buses:
        while b.recv(timeout=0.0) is not None:
            dropped += 1
    return dropped


HOST = "192.168.80.1"
BITRATE = 500000
VCM_ID = 0x051
# Spec 7.1 key state: 0x592 EPRI_HCU_Sensor_0592, IgnitionKeyState = B0 bit 4.
KEY_ID = 0x592
KEY_ON = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])
KEY_EVERY = 10          # one per ten 0x051 -> 10 Hz against a 100 Hz replay
REPLAY_LOG = gi_paths.capture("generator-on-then-off.log")
DBC = gi_paths.vehicle_dbc("vtrux-powertrain-experimental.dbc")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()

MODE_OFF, MODE_INHIBIT = 0, 3
TRAIL_MAX_US = 3000
DUP_WINDOW_S = 0.005

B0_ENGINE_OFF = 0x08
B0_GEN_REQ = 0x0B
B0_SHUTDOWN = 0x10

PHASES = [
    ("A_engine_off", B0_ENGINE_OFF, True),
    ("B_gen_requested", B0_GEN_REQ, True),
    ("C_shutdown_cmd", B0_SHUTDOWN, False),
    ("D_back_to_off", B0_ENGINE_OFF, True),
]


def dev_get(path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dev_post(path, timeout=8):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_payloads(path, limit):
    out = []
    for f in parse_file(path):
        if f.arbitration_id == VCM_ID:
            d = bytes(f.data)
            if len(d) >= 6:
                out.append(bytearray(d[:6]))
                if len(out) >= limit:
                    break
    return out


def run_phase(pcan, kvaser, payloads, b0, seconds, ctr0, period=0.010):
    """Replay `seconds` of 0x051 with B0 forced to `b0`.

    `ctr0` continues the rolling counter from the previous phase. Restarting
    it at 0 each phase puts a non-+1 step on every phase boundary, which the
    device correctly counts as ctr_bad -- an artifact of the stimulus, not a
    fault, and one that cost a confusing first run.
    """
    n = int(seconds / period)
    captured = []
    while kvaser.recv(timeout=0.0) is not None:
        pass
    t0 = time.perf_counter()
    for i in range(n):
        src = payloads[i % len(payloads)]
        d = bytearray(src)
        d[0] = b0
        d[5] = (ctr0 + i) & 0x0F   # continuous across phases
        target = t0 + i * period
        while time.perf_counter() < target:
            pass
        pcan.send(can.Message(arbitration_id=VCM_ID, data=bytes(d),
                              is_extended_id=False), timeout=0.5)
        # Spec 7.1: no 0x592 means the device reads the key OFF and transmits
        # nothing at all, which would make every phase below read as a pass
        # for the wrong reason. 10 Hz, matching the truck.
        send_interlocks(pcan, i)
        m = kvaser.recv(timeout=0.0)
        while m is not None:
            captured.append((m.timestamp, m.arbitration_id, bytes(m.data),
                             m.is_error_frame))
            m = kvaser.recv(timeout=0.0)
    deadline = time.time() + 0.3
    while time.time() < deadline:
        m = kvaser.recv(timeout=0.05)
        if m is None:
            break
        captured.append((m.timestamp, m.arbitration_id, bytes(m.data),
                         m.is_error_frame))
    return captured, n


def dedup(frames):
    """Drop a repeat of the same (id, payload) within DUP_WINDOW_S."""
    out = []
    last = {}
    for t, a, d, e in frames:
        k = (a, d)
        if k in last and (t - last[k]) < DUP_WINDOW_S:
            continue
        last[k] = t
        out.append((t, a, d, e))
    return out


def split_trail(frames):
    """Separate replayed VCM frames from WiCAN trail frames by gap."""
    o51 = sorted([(t, d) for t, a, d, e in frames
                  if a == VCM_ID and not e and len(d) >= 6])
    vcm, trail = [], []
    prev = None
    for t, d in o51:
        if prev is not None and (t - prev[0]) * 1e6 < TRAIL_MAX_US:
            trail.append((t, d, prev[1]))     # (t, wican_payload, trigger)
        else:
            vcm.append((t, d))
        prev = (t, d)
    return vcm, trail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0,
                    help="seconds per phase")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "crank_request_bench.json"))
    args = ap.parse_args()

    db = cantools.database.load_file(DBC)
    msg = db.get_message_by_frame_id(VCM_ID)
    payloads = load_payloads(REPLAY_LOG, 2000)
    print("loaded %d real 0x051 payloads" % len(payloads))

    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser"]):
        if "Virtual" in cfg.get("device_name", ""):
            continue
        found.setdefault(cfg["interface"], cfg)
    if "pcan" not in found or "kvaser" not in found:
        print("FAIL: need both dongles; found %s" % sorted(found))
        return 1

    # OPEN ORDER IS LOAD-BEARING: PCAN FIRST, THEN KVASER.
    # Resolved by the interposer session 2026-09-22. `can.Bus()` on the PCAN
    # takes seconds to return, and a bus already open while another is still
    # opening receives and buffers that whole time, then delivers the backlog in
    # a burst when reading starts. Open the Kvaser first and it accumulates ~33
    # frames through the PCAN's slow open; open the PCAN first and nothing waits
    # on anything. Measured over the same 20 s window, varying only the order:
    # kvaser-then-pcan gave 113 vs 80 frames and 21 "duplicates"; pcan-then-kvaser
    # gave 80 vs 80 and zero. There is no receiver duplication -- that conclusion
    # is withdrawn, and this was its whole cause.
    # Swapping these two lines looks harmless and is not. The settle() below is
    # the belt to this braces.
    pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"], bitrate=BITRATE)
    kvaser = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"], bitrate=BITRATE)
    print("settling dongles (%.1fs) ... dropped %d backlog frames"
          % (SETTLE_S, settle(pcan, kvaser)))

    results = []
    stats_end = {}
    try:
        # The image from /gen_inhibit's elf_sha256 (spec 11), never /check_status, which answers
        # with the device's config, credentials included (reviewer, 2026-10-20). Until
        # 2026-10-25 this printed device_id (the unit's MAC), git_version and can_mode from it.
        pre = json.loads(dev_get("/gen_inhibit"))
        print("device elf_sha256 %s" % pre.get("elf_sha256"))
        if pre.get("disabled"):
            print("FAIL: device is already latched disabled (%s). A disable "
                  "latch survives until reboot, so this must run on a fresh "
                  "boot -- POST /system_reboot (a GET returns 200 and does "
                  "nothing) and check tx_ok is 0." % pre.get("disable_reason"))
            return 1

        dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
        time.sleep(0.5)

        ctr = 0
        for name, b0, expect_tx in PHASES:
            raw, sent = run_phase(pcan, kvaser, payloads, b0, args.seconds, ctr)
            ctr = (ctr + sent) & 0x0F
            clean = dedup(raw)
            removed = len(raw) - len(clean)
            vcm, trail = split_trail(clean)
            mid = json.loads(dev_get("/gen_inhibit"))
            results.append({"phase": name, "b0_sent": b0,
                            "expect_tx": expect_tx,
                            "vcm": len(vcm), "wican": len(trail),
                            "dups_removed": removed,
                            "disabled": mid.get("disabled"),
                            "disable_reason": mid.get("disable_reason"),
                            "trail": trail})
            print("  %-16s B0=0x%02X sent  VCM %4d  WiCAN %4d  "
                  "(dups removed %d)  disabled=%s"
                  % (name, b0, len(vcm), len(trail), removed,
                     mid.get("disabled")))

        stats_end = json.loads(dev_get("/gen_inhibit"))
        dev_post("/gen_inhibit_set?mode=%d" % MODE_OFF)
    finally:
        for b in (pcan, kvaser):
            try:
                b.shutdown()
            except Exception:
                pass

    print()
    fails = []
    all_dups = sum(r["dups_removed"] for r in results)

    for r in results:
        bad_b0 = []
        bad_tq = []
        bad_ctr = []
        for t, d, trig in r["trail"]:
            if d[0] != B0_ENGINE_OFF:
                bad_b0.append("0x%02X" % d[0])
            if msg.decode(bytes(d))["gen_torque_cmd"] != 0:
                bad_tq.append(t)
            if d[5] != ((trig[5] + 1) & 0x0F):
                bad_ctr.append((trig[5], d[5]))
        r["bad_b0"] = bad_b0[:10]
        r["bad_torque"] = len(bad_tq)
        r["bad_ctr"] = len(bad_ctr)

        if r["expect_tx"]:
            if r["wican"] == 0:
                fails.append("%s: expected transmission, saw none" % r["phase"])
        else:
            if r["wican"] != 0:
                fails.append("%s: expected silence, saw %d WiCAN frames"
                             % (r["phase"], r["wican"]))
        if bad_b0:
            fails.append("%s: %d WiCAN frames with B0 != 0x08 (%s)"
                         % (r["phase"], len(bad_b0), ",".join(sorted(set(bad_b0)))))
        if bad_tq:
            fails.append("%s: %d WiCAN frames with nonzero torque"
                         % (r["phase"], len(bad_tq)))
        if bad_ctr:
            fails.append("%s: %d WiCAN frames did not steal the next counter"
                         % (r["phase"], len(bad_ctr)))
        if r["disabled"]:
            fails.append("%s: device latched disabled (%s) -- 6.3 must not latch"
                         % (r["phase"], r["disable_reason"]))

    tx_fail = stats_end.get("tx_fail", -1)
    ctr_bad = stats_end.get("ctr_bad", -1)
    if tx_fail:
        fails.append("device tx_fail=%s" % tx_fail)
    if ctr_bad:
        fails.append("device ctr_bad=%s" % ctr_bad)

    print("de-duplication removed %d frame(s) in total -- %s"
          % (all_dups,
             "it changed nothing, as expected at 10 ms spacing" if not all_dups
             else "NOTE: it changed the counts, so the raw numbers were inflated"))
    print("device at end: tx_ok=%s tx_fail=%s ctr_ok=%s ctr_bad=%s disabled=%s"
          % (stats_end.get("tx_ok"), tx_fail, stats_end.get("ctr_ok"),
             ctr_bad, stats_end.get("disabled")))
    print()
    if fails:
        print("=== FAIL ===")
        for f in fails:
            print("  " + f)
    else:
        print("=== PASS -- B0 held at 0x08 through a generator request, "
              "0x10 suppressed without latching, 0x10 never transmitted ===")

    if not os.path.isdir(os.path.dirname(args.out)):
        os.makedirs(os.path.dirname(args.out))
    for r in results:
        r.pop("trail", None)
    with open(args.out, "w") as fh:
        json.dump({"phases": results, "device_end": stats_end,
                   "fails": fails}, fh, indent=1)
    print("wrote %s" % args.out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
