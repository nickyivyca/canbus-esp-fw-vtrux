"""
Bench link check: PCAN <-> Kvaser on a shared 120-ohm-terminated segment.

Verifies, before any WiCAN work, that the two-dongle bench is electrically and
logically sound:

  Phase 1  enumerate  - both dongles present and openable at 500 kbit/s
  Phase 2  P->K       - PCAN transmits, Kvaser receives; check IDs/payloads
  Phase 3  K->P       - reverse direction
  Phase 4  replay     - replay real 0x051 frames from a Vtrux capture across the
                        link at 100 Hz and confirm every frame arrives intact

Run from the project root:
    py -3.12 tools/gen_inhibit/bench/bench_link_check.py
"""

import sys
import time

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

import can

BITRATE = 500000
PCAN = dict(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
KVASER = dict(interface="kvaser", channel=0, bitrate=BITRATE)

FAILURES = []


def report(phase, ok, detail):
    tag = "PASS" if ok else "FAIL"
    print("[%s] %s: %s" % (tag, phase, detail))
    if not ok:
        FAILURES.append(phase)


def phase1_enumerate():
    print("\n=== Phase 1: enumerate ===")
    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser"]):
        name = cfg.get("device_name", "")
        if "Virtual" in name:
            continue
        found[cfg["interface"]] = cfg
        print("  %-8s ch=%-14s %s" % (cfg["interface"], cfg["channel"], name))
    report("enumerate", "pcan" in found and "kvaser" in found,
           "pcan=%s kvaser=%s" % ("pcan" in found, "kvaser" in found))
    return found


def open_bus(cfg, label):
    bus = can.Bus(**cfg)
    print("  opened %s" % label)
    return bus


def drain(bus, seconds=0.3):
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        if bus.recv(timeout=0.01) is not None:
            n += 1
    return n


def directional_test(tx_bus, rx_bus, label, base_id, count=50):
    """Send `count` frames with incrementing ids/payloads, verify all arrive."""
    drain(rx_bus)
    sent = {}
    for i in range(count):
        arb = base_id + (i % 8)
        data = bytes([i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0x55, i & 0x0F, 0xDE, 0xAD, 0xBE])
        msg = can.Message(arbitration_id=arb, data=data, is_extended_id=False)
        try:
            tx_bus.send(msg, timeout=0.5)
        except can.CanError as exc:
            report(label, False, "send failed at frame %d: %s" % (i, exc))
            return
        sent[(arb, data)] = sent.get((arb, data), 0) + 1
        time.sleep(0.002)

    got = {}
    deadline = time.time() + 1.0
    while time.time() < deadline and sum(got.values()) < count:
        msg = rx_bus.recv(timeout=0.2)
        if msg is None:
            continue
        if msg.is_error_frame:
            continue
        key = (msg.arbitration_id, bytes(msg.data))
        got[key] = got.get(key, 0) + 1

    n_got = sum(got.values())
    corrupt = [k for k in got if k not in sent]
    ok = n_got == count and not corrupt
    report(label, ok, "sent %d, received %d, unmatched %d" % (count, n_got, len(corrupt)))
    if corrupt:
        for k in corrupt[:3]:
            print("      unexpected: id=0x%03X data=%s" % (k[0], k[1].hex()))


def load_0x051(path, limit=600):
    """Pull real 0x051 payloads out of a BUSMASTER-format Vtrux capture."""
    from canre.parsers import parse_file
    frames = []
    for f in parse_file(path):
        if f.arbitration_id == 0x051:
            frames.append(bytes(f.data))
            if len(frames) >= limit:
                break
    return frames


def phase4_replay(tx_bus, rx_bus, frames):
    print("\n=== Phase 4: replay real 0x051 at 100 Hz ===")
    if not frames:
        report("replay", False, "no 0x051 frames loaded")
        return
    drain(rx_bus)

    received = []
    period = 0.010
    t0 = time.perf_counter()
    for i, data in enumerate(frames):
        target = t0 + i * period
        while time.perf_counter() < target:
            pass
        tx_bus.send(can.Message(arbitration_id=0x051, data=data,
                                is_extended_id=False), timeout=0.5)
        msg = rx_bus.recv(timeout=0.0)
        while msg is not None:
            if not msg.is_error_frame:
                received.append(bytes(msg.data))
            msg = rx_bus.recv(timeout=0.0)
    elapsed = time.perf_counter() - t0

    deadline = time.time() + 0.5
    while time.time() < deadline:
        msg = rx_bus.recv(timeout=0.05)
        if msg is None:
            break
        if not msg.is_error_frame:
            received.append(bytes(msg.data))

    n = min(len(frames), len(received))
    mismatches = sum(1 for i in range(n) if frames[i] != received[i])
    ok = len(received) == len(frames) and mismatches == 0
    report("replay", ok,
           "sent %d in %.2fs (%.1f Hz), received %d, payload mismatches %d"
           % (len(frames), elapsed, len(frames) / elapsed, len(received), mismatches))
    if mismatches:
        for i in range(n):
            if frames[i] != received[i]:
                print("      first mismatch @%d: sent %s got %s"
                      % (i, frames[i].hex(), received[i].hex()))
                break


def main():
    found = phase1_enumerate()
    if FAILURES:
        print("\nCannot continue: both dongles must be present.")
        return 1

    kv_cfg = dict(KVASER)
    kv_cfg["channel"] = found["kvaser"]["channel"]
    pc_cfg = dict(PCAN)
    pc_cfg["channel"] = found["pcan"]["channel"]

    pcan = kvaser = None
    try:
        print("\n=== opening buses at %d bit/s ===" % BITRATE)
        pcan = open_bus(pc_cfg, "PCAN")
        kvaser = open_bus(kv_cfg, "Kvaser")

        print("\n=== Phase 2: PCAN -> Kvaser ===")
        directional_test(pcan, kvaser, "pcan->kvaser", 0x100)

        print("\n=== Phase 3: Kvaser -> PCAN ===")
        directional_test(kvaser, pcan, "kvaser->pcan", 0x200)

        log = gi_paths.capture("generator-on-then-off.log")
        frames = load_0x051(log)
        print("\nloaded %d real 0x051 payloads from %s" % (len(frames), log))
        phase4_replay(pcan, kvaser, frames)

    finally:
        for b in (pcan, kvaser):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass

    print("\n" + "=" * 52)
    if FAILURES:
        print("BENCH NOT READY - failed: %s" % ", ".join(FAILURES))
        return 1
    print("BENCH OK - PCAN and Kvaser talk both ways on the terminated segment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
