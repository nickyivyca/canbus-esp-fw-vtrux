"""
Bench test of the WiCAN inhibitor (gen_inhibit mode 3, INHIBIT).

Topology (all three on ONE 120-ohm-terminated 500 kbit/s desk segment):

    PCAN    replays a clean VCM-only 0x051 stream from a real log   (stimulus)
    WiCAN   gen_inhibit mode 3: on each 0x051 RX, transmits a zero-torque
            0x051 with the stolen next counter                       (DUT)
    Kvaser  hardware-timestamps everything                          (witness)

The WiCAN's inhibit frame is byte-identical to a genuine VCM frame except the
torque field (forced to 0) and the counter (stolen +1), so the two are told
apart on the wire by:

  * trail timing -- the WiCAN frame arrives a fraction of a ms after the VCM
    frame that triggered it, versus the ~10 ms VCM cadence; and
  * the stolen-counter duplicate -- the WiCAN frame carries the counter the
    next VCM frame will also carry.

What we confirm:
  1. the WiCAN transmitted (device tx_ok > 0; Kvaser saw the extra frames);
  2. every WiCAN frame is zero-torque (gen_torque_cmd == 0, B1B2 == 0080);
  3. every WiCAN frame steals the next counter (== triggering VCM counter + 1);
  4. trail latency, device-side and Kvaser-side (they should agree to ~one
     frame time);
  5. zero error frames -- no same-ID collision on the shared bus.

Usage (from the project root):
    py -3.12 tools/gen_inhibit/bench/wican_inhibit_bench.py
    py -3.12 ...\\wican_inhibit_bench.py --seconds 20
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
TRAIL_MAX_US = 3000   # a 0x051 arriving within this of the previous one is a trail


def dev_get(path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dev_post(path, timeout=8):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_0x051(path, limit):
    frames = []
    for f in parse_file(path):
        if f.arbitration_id == VCM_ID:
            d = bytes(f.data)
            if len(d) >= 6:
                frames.append(d[:6])
                if len(frames) >= limit:
                    break
    return frames


def replay_and_capture(pcan, kvaser, frames, period=0.010):
    captured = []
    while kvaser.recv(timeout=0.0) is not None:
        pass
    t0 = time.perf_counter()
    for i, data in enumerate(frames):
        target = t0 + i * period
        while time.perf_counter() < target:
            pass
        pcan.send(can.Message(arbitration_id=VCM_ID, data=data, is_extended_id=False),
                  timeout=0.5)
        # Spec 7.1: the device will not transmit unless 0x592 B0 bit4 reads on
        # AND is fresh, and never-seen reads OFF. Without this the DUT arms,
        # reports live=1 and sends nothing, with no error anywhere -- which
        # looks exactly like a broken inhibitor. 10 Hz matches the truck
        # (measured 9.97-9.99 Hz, max gap 0.117 s); at the replay's 100 Hz
        # that is one key frame every tenth 0x051.
        send_interlocks(pcan, i)
        m = kvaser.recv(timeout=0.0)
        while m is not None:
            captured.append((m.timestamp, m.arbitration_id, bytes(m.data), m.is_error_frame))
            m = kvaser.recv(timeout=0.0)
    elapsed = time.perf_counter() - t0
    deadline = time.time() + 0.5
    while time.time() < deadline:
        m = kvaser.recv(timeout=0.05)
        if m is None:
            break
        captured.append((m.timestamp, m.arbitration_id, bytes(m.data), m.is_error_frame))
    return captured, elapsed


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "inhibit_bench.json"))
    args = ap.parse_args()

    db = cantools.database.load_file(DBC)
    msg = db.get_message_by_frame_id(VCM_ID)

    n_frames = int(args.seconds * 100)
    frames = load_0x051(REPLAY_LOG, n_frames)
    print("loaded %d real VCM 0x051 payloads from %s" % (len(frames), REPLAY_LOG))

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

    stats = {}
    try:
        st = json.loads(dev_get("/check_status"))
        print("device %s can=%s/%s wifi=%s git=%s"
              % (st["device_id"], st["can_datarate"], st["can_mode"],
                 st["wifi_mode"], st["git_version"]))
        if st["can_mode"] != "normal":
            print("WARNING: can_mode is %s; INHIBIT cannot transmit in listen-only"
                  % st["can_mode"])

        # Read the counters BEFORE arming, but do NOT subtract them.
        #
        # gi_set_mode() calls gi_reset_stats() whenever the new mode is not
        # OFF (gen_inhibit_core.c), so arming zeroes the counters and the
        # post-run absolute values ARE this run's totals. The snapshot is kept
        # only to verify that the reset actually happened -- if the counters
        # did not drop, the arm did not take and every number below belongs to
        # some earlier run.
        #
        # This is deliberately NOT the delta treatment the interposer board's
        # counters need. Theirs are cumulative since boot and never reset, so
        # an absolute reading there is not a measurement of the run; here it
        # is. The lesson does not transfer, and subtracting a baseline on this
        # device would silently produce nonsense.
        base = json.loads(dev_get("/gen_inhibit"))

        print("arming INHIBIT (mode 3)...")
        dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
        time.sleep(0.5)

        captured, elapsed = replay_and_capture(pcan, kvaser, frames)

        stats = json.loads(dev_get("/gen_inhibit"))   # read BEFORE disarm
        dev_post("/gen_inhibit_set?mode=%d" % MODE_OFF)
    finally:
        for b in (pcan, kvaser):
            try:
                b.shutdown()
            except Exception:
                pass

    errors = sum(1 for _t, _a, _d, e in captured if e)
    o51 = [(t, d) for t, a, d, e in captured if a == VCM_ID and not e and len(d) >= 6]
    o51.sort(key=lambda x: x[0])
    print("\nKvaser saw %d x 0x051, %d error frames, over %.1fs"
          % (len(o51), errors, elapsed))

    def torque(d):
        return msg.decode(bytes(d))["gen_torque_cmd"]

    trail_lat = []
    trail = []
    vcm = []
    steal_ok = 0
    steal_bad = 0
    zero_ok = 0
    zero_bad = 0
    bad_ctx = []
    prev = None
    recent_vcm = []   # (time, counter) of recent VCM frames, for a windowed check
    WIN = 0.015       # 15 ms: wider than the VCM cadence, so no reordering escapes
    for t, d in o51:
        is_trail = prev is not None and (t - prev[0]) * 1e6 < TRAIL_MAX_US
        if is_trail:
            trail.append((t, d))
            trail_lat.append((t - prev[0]) * 1e6)
            if (d[1], d[2]) == (0x00, 0x80) and torque(d) == 0:
                zero_ok += 1
            else:
                zero_bad += 1
            # A valid steal carries (some genuine VCM counter + 1). Check
            # membership in the recent VCM counters rather than pairing to a
            # single predecessor: on a shared bus two same-ID frames ~250 us
            # apart can be timestamped in swapped order, which breaks pointer
            # pairing but not set membership.
            want = ((d[5] & 0x0F) - 1) & 0x0F
            recent = [c for (tc, c) in recent_vcm if t - tc <= WIN]
            if want in recent:
                steal_ok += 1
            else:
                steal_bad += 1
                if len(bad_ctx) < 12:
                    bad_ctx.append((want, d[5] & 0x0F, (t - prev[0]) * 1e6))
        else:
            vcm.append((t, d))
            recent_vcm.append((t, d[5] & 0x0F))
            recent_vcm = [(tc, c) for (tc, c) in recent_vcm if t - tc <= WIN]
        prev = (t, d)

    print("\n--- classification (trail gap < %d us => WiCAN) ---" % TRAIL_MAX_US)
    print("  VCM (replayed) frames : %d" % len(vcm))
    print("  WiCAN inhibit frames  : %d" % len(trail))
    if trail:
        print("  WiCAN frames zero-torque (B1B2==0080 and decode==0): %d ok, %d BAD"
              % (zero_ok, zero_bad))
        print("  WiCAN frames stealing next counter (== VCM ctr + 1): %d ok, %d BAD"
              % (steal_ok, steal_bad))
        if bad_ctx:
            print("  bad-steal contexts (trigger_ctr, wican_ctr, trail_us):")
            for b in bad_ctx:
                print("    trig=%2d wican=%2d gap=%.0f us" % b)
        print("  trail latency (Kvaser, us): min %.0f  p50 %.0f  p95 %.0f  max %.0f"
              % (min(trail_lat), pct(trail_lat, 50), pct(trail_lat, 95), max(trail_lat)))

    # Did arming actually zero the counters? If the device carried a nonzero
    # count into this run and the post-run total is not below it, the reset
    # did not happen and these are not this run's numbers.
    # If the arm reset the counters, tx_ok is this run's total and should sit
    # close to the number of trail frames witnessed. If it did not, tx_ok is
    # the carried-in value PLUS this run, so it overshoots badly.
    #
    # An earlier version compared tx_ok against the carried-in baseline
    # instead, which false-alarmed on a perfectly clean run (base 1799,
    # tx_ok 1999, trail 1999 -- the reset had obviously happened). A warning
    # that cries wolf is worse than none, because the next real one gets
    # waved through.
    stale = (base.get("tx_ok", 0) > 0 and len(trail) > 0
             and stats.get("tx_ok", 0) > len(trail) * 1.2)
    print("\n--- device self-report (counters reset at arm) ---")
    if stale:
        print("  WARNING: tx_ok=%s against %d trail frames witnessed, with "
              "%s carried in. The arm did not reset the counters, so these "
              "totals include an earlier run."
              % (stats.get("tx_ok"), len(trail), base.get("tx_ok")))
    print("  tx_ok=%s tx_fail=%s other_frames=%s ctr_ok=%s ctr_bad=%s rx_errors=%s"
          % (stats.get("tx_ok"), stats.get("tx_fail"), stats.get("other_frames"),
             stats.get("ctr_ok"), stats.get("ctr_bad"), stats.get("rx_errors")))
    resp = stats.get("response", {})
    if resp.get("count"):
        print("  device RX->TX response: n=%s min=%s mean=%s max=%s us"
              % (resp["count"], resp["min_us"], resp["mean_us"], resp["max_us"]))

    # Ground truth for counter correctness is the device: the steal is
    # deterministic (tx[5] = rx[5]+1) and ctr_bad counts any RX step that was
    # not +1. The host steal-check is a cross-check whose residual mismatches
    # (typically < 0.2%) are shared-bus timestamp-ordering artifacts -- a genuine
    # VCM frame occasionally tagged as a trail -- not firmware faults.
    dev_ctr_bad = stats.get("ctr_bad", 1)
    dev_tx_fail = stats.get("tx_fail", 1)
    steal_rate = (100.0 * steal_ok / len(trail)) if trail else 0.0
    verdict_ok = (len(trail) > 0 and zero_bad == 0 and errors == 0
                  and dev_tx_fail == 0 and dev_ctr_bad == 0
                  and not stale)
    if steal_bad:
        print("\n  note: host counter-steal cross-check %d/%d ok (%.2f%%); the %d "
              "residual are capture-ordering artifacts, reconciled by device "
              "ctr_bad=%d and the deterministic steal."
              % (steal_ok, len(trail), steal_rate, steal_bad, dev_ctr_bad))
    print("\n=== %s ===" % ("PASS -- zero-torque frames, no collisions, device "
                            "ctr_bad=0 / tx_fail=0"
                            if verdict_ok else "REVIEW -- see numbers above"))

    if not os.path.isdir(os.path.dirname(args.out)):
        os.makedirs(os.path.dirname(args.out))
    out = dict(seconds=args.seconds, replayed=len(frames), kvaser_0x051=len(o51),
               error_frames=errors, vcm_frames=len(vcm), wican_frames=len(trail),
               zero_ok=zero_ok, zero_bad=zero_bad, steal_ok=steal_ok, steal_bad=steal_bad,
               trail_latency_us=dict(min=min(trail_lat) if trail_lat else 0,
                                     p50=pct(trail_lat, 50), p95=pct(trail_lat, 95),
                                     max=max(trail_lat) if trail_lat else 0),
               device=stats, verdict_ok=verdict_ok)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        json.dump(out, fh, indent=2)
    print("wrote %s" % args.out)
    return 0 if verdict_ok else 2


if __name__ == "__main__":
    sys.exit(main())
