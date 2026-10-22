"""
WiCAN timing characterization on the PCAN/Kvaser bench.

Answers the question the whole inhibitor design turns on: how long does the
ESP32-C3 take to get from "0x051 arrived on the wire" to "our frame is on the
wire", and how much does WiFi activity move that number.

Topology (all on one 120-ohm-terminated 500 kbit/s segment):

    PCAN    replays real Vtrux 0x051 payloads at 100 Hz  (stimulus)
    WiCAN   gen_inhibit component, OBSERVE or RESPOND    (device under test)
    Kvaser  listens to everything, hardware timestamps   (independent witness)

Two independent measurements of the same quantity:

  * device-side  -- gen_inhibit's own esp_timer_get_time() deltas, read back
                    as histograms from GET /gen_inhibit. Sub-microsecond
                    resolution but blind to anything between the transceiver
                    and the TWAI ISR.
  * host-side    -- Kvaser hardware timestamps of 0x051 followed by the 0x7F0
                    probe. Includes the whole path, but carries the dongle's
                    own timestamping error.

They should agree to within the probe offset. If they disagree materially,
believe the Kvaser: the device cannot see its own latency to the ISR.

RESPOND transmits 0x7F0, never 0x051 -- a timing probe, not a command.

Usage (from the project root):
    py -3.12 tools/gen_inhibit/bench/wican_timing_bench.py
    py -3.12 ...\\wican_timing_bench.py --phases 0,1 --seconds 10
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

import can

HOST = "192.168.80.1"
BITRATE = 500000
VCM_ID = 0x051
PROBE_ID = 0x7F0
REPLAY_LOG = gi_paths.capture("generator-on-then-off.log")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()

MODE_OFF, MODE_OBSERVE, MODE_RESPOND = 0, 1, 2


# --------------------------------------------------------------------------
# device control
# --------------------------------------------------------------------------

def dev_get(path, timeout=8):
    url = "http://%s%s" % (HOST, path)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dev_post(path, timeout=8):
    url = "http://%s%s" % (HOST, path)
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def set_mode(mode, offset_us=500):
    dev_post("/gen_inhibit_set?mode=%d&offset=%d" % (mode, offset_us))


def read_stats():
    return json.loads(dev_get("/gen_inhibit"))


def slcan_bus_up(pcan, kvaser):
    """Install and start the TWAI driver via the SLCAN socket, and prove it.

    Why this is needed at all: wican-fw's can_init() records the bitrate but
    does NOT install the TWAI driver -- can_enable() does, and with
    protocol=slcan that only runs when a client opens the socket and sends
    'O'. On an idle WiCAN the driver is therefore absent, twai_receive()
    returns ESP_ERR_INVALID_STATE immediately, and the gen_inhibit worker
    busy-waits on that error at priority 18, starving httpd. The device then
    pings but answers nothing, and only a power cycle clears it.

    Newer firmware arms the bus itself. The build currently on the device does
    not, so we do it from here -- and we VERIFY it before arming, because
    arming against a dead driver is the soft-lock.

    Verification has to happen while gen_inhibit is OFF: once armed, its
    gen_inhibit_owns_bus() makes can_rx_task stand down, so nothing would
    reach this socket even on a healthy bus.

    Returns the open socket; the caller must keep it open for the run.
    """
    import socket
    s = socket.create_connection((HOST, 3333), timeout=8)
    s.sendall(b"C\r")
    time.sleep(0.2)
    s.sendall(b"S6\r")      # 500 kbit/s, matching the bench segment
    time.sleep(0.2)
    s.sendall(b"O\r")       # open -> can_enable() -> twai_driver_install + start
    time.sleep(0.8)

    # Prove the driver is really running by putting traffic on the wire and
    # seeing it arrive. A silent socket here means do NOT arm.
    s.settimeout(2.0)
    try:
        while True:
            if not s.recv(4096):
                break
    except Exception:
        pass

    s.settimeout(3.0)
    for i in range(20):
        pcan.send(can.Message(arbitration_id=0x123, data=bytes([i] * 8),
                              is_extended_id=False), timeout=0.5)
        time.sleep(0.01)
    try:
        seen = s.recv(4096)
    except Exception:
        seen = b""

    if not seen:
        s.close()
        raise RuntimeError(
            "SLCAN socket saw no traffic: the TWAI driver did not come up. "
            "Refusing to arm gen_inhibit -- that would soft-lock the device.")
    print("  bus up: SLCAN socket returned %d bytes (driver running)" % len(seen))
    return s


class HttpLoad:
    """Background HTTP hammering, to make the WiFi stack and httpd busy.

    This is the 'client busy' arm of the sweep. It is deliberately the same
    channel we use for control, so it competes for exactly the resources the
    inhibitor would have to survive in the field.
    """

    def __init__(self, threads=4):
        self.threads = threads
        self._stop = threading.Event()
        self._workers = []
        self.requests = 0
        self.errors = 0
        self._lock = threading.Lock()

    def _worker(self):
        while not self._stop.is_set():
            try:
                dev_get("/check_status", timeout=5)
                with self._lock:
                    self.requests += 1
            except Exception:
                with self._lock:
                    self.errors += 1

    def __enter__(self):
        for _ in range(self.threads):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._workers.append(t)
        return self

    def __exit__(self, *exc):
        self._stop.set()
        for t in self._workers:
            t.join(timeout=3)


# --------------------------------------------------------------------------
# stimulus and capture
# --------------------------------------------------------------------------

def load_0x051(path, limit):
    """Real VCM payloads, in order, so the B5 rolling counter stays coherent.

    Coherence matters: gen_inhibit checks that B5 steps by exactly +1 each
    frame, which is how we detect the device silently dropping frames rather
    than merely being late.
    """
    from canre.parsers import parse_file
    frames = []
    for f in parse_file(path):
        if f.arbitration_id == VCM_ID:
            frames.append(bytes(f.data))
            if len(frames) >= limit:
                break
    return frames


def rearm_after(delay, mode, offset_us, sink):
    """Re-issue the arm command mid-stream, from a thread.

    gen_inhibit's t_prev_051 is static and is not cleared when the mode
    changes, so the first inter-arrival recorded after arming is the gap since
    the *previous* run -- tens of seconds, landing in the top bucket and
    dragging the mean with it (a 10 ms period reported a 70 ms mean because of
    one 60 s sample).

    Re-arming resets the histograms while frames are still flowing, so
    t_prev_051 is only milliseconds old and the first sample is honest. Doing
    it from a thread is what keeps the stimulus running across the HTTP round
    trip; re-arming between two synchronous replays just shrinks the artifact
    to the size of that round trip instead of removing it.
    """
    time.sleep(delay)
    try:
        set_mode(mode, offset_us)
        sink["t"] = time.perf_counter()
    except Exception as exc:
        sink["error"] = str(exc)


def replay_and_capture(pcan, kvaser, frames, period=0.010):
    """Send `frames` at 1/period Hz on PCAN; capture everything on Kvaser.

    Capture is interleaved with transmission rather than threaded: python-can's
    recv is cheap when the queue is empty, and a separate thread would add
    scheduling noise to the very interval being measured.
    """
    captured = []
    while kvaser.recv(timeout=0.0) is not None:
        pass

    t0 = time.perf_counter()
    for i, data in enumerate(frames):
        target = t0 + i * period
        while time.perf_counter() < target:
            pass
        pcan.send(can.Message(arbitration_id=VCM_ID, data=data,
                              is_extended_id=False), timeout=0.5)
        m = kvaser.recv(timeout=0.0)
        while m is not None:
            if not m.is_error_frame:
                captured.append((m.timestamp, m.arbitration_id, bytes(m.data)))
            m = kvaser.recv(timeout=0.0)
    elapsed = time.perf_counter() - t0

    deadline = time.time() + 0.5
    while time.time() < deadline:
        m = kvaser.recv(timeout=0.05)
        if m is None:
            break
        if not m.is_error_frame:
            captured.append((m.timestamp, m.arbitration_id, bytes(m.data)))

    return captured, elapsed


def pair_response_times(captured):
    """Kvaser-side RX-to-probe delta, in microseconds.

    Pairs each 0x051 with the first 0x7F0 that follows it and precedes the
    next 0x051. A probe that lands after the next genuine frame is dropped
    rather than mis-paired -- it is counted separately as a straggler.
    """
    deltas = []
    stragglers = 0
    pending = None
    for ts, arb, _data in captured:
        if arb == VCM_ID:
            if pending is not None:
                stragglers += 1
            pending = ts
        elif arb == PROBE_ID:
            if pending is not None:
                deltas.append((ts - pending) * 1e6)
                pending = None
            else:
                stragglers += 1
    return deltas, stragglers


def save_raw_capture(name, captured, primed):
    """Write the Kvaser's raw frames for one arm, so the run can be recounted.

    Added 2026-09-27 for the e011b01 bench runs, where the agreed conditions
    require the raw witness logs to be kept rather than only the percentiles
    derived from them -- a summary cannot be re-examined, and every percentile
    in this file is computed from exactly these rows.

    THE FULL CAPTURE IS WRITTEN, PRIMING WINDOW INCLUDED, and the number of
    priming frames is recorded in the header so the trim `run_arm` performs is
    reproducible rather than baked in. Called after the measurement window has
    closed and the device has been read, so it cannot perturb what it records.
    """
    path = os.path.join(OUT_DIR, "%s_kvaser_raw.csv" % name)
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    with open(path, "w", encoding="ascii", errors="replace", newline="") as fh:
        fh.write("# kvaser raw capture, arm=%s, primed_0x051=%d\n"
                 % (name, primed))
        fh.write("# the measured window starts after the %dth 0x051; "
                 "everything before it is priming and is discarded\n" % primed)
        fh.write("# timestamps are Kvaser hardware timestamps, seconds\n")
        fh.write("timestamp,arb_id,dlc,data_hex\n")
        for ts, arb, data in captured:
            fh.write("%.6f,0x%03X,%d,%s\n" % (ts, arb, len(data), data.hex()))
    print("  raw kvaser capture -> %s (%d frames)" % (path, len(captured)))
    return path


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def describe(vals, label):
    if not vals:
        print("    %-14s (no samples)" % label)
        return {}
    s = sorted(vals)
    d = dict(n=len(s), min=s[0], p50=pct(s, 50), p95=pct(s, 95),
             p99=pct(s, 99), max=s[-1], mean=sum(s) / len(s))
    print("    %-14s n=%-5d min %8.1f  p50 %8.1f  p95 %8.1f  p99 %8.1f  max %8.1f us"
          % (label, d["n"], d["min"], d["p50"], d["p95"], d["p99"], d["max"]))
    return d


def print_hist(h, edges, label):
    if not h or h.get("count", 0) == 0:
        print("    %-14s (device reported no samples)" % label)
        return
    print("    %-14s n=%-5d min %d  mean %d  max %d us"
          % (label, h["count"], h["min_us"], h["mean_us"], h["max_us"]))
    lo = 0
    for i, b in enumerate(h["buckets"]):
        if b == 0:
            continue
        hi = edges[i]
        span = ("<%s" % hi) if i < len(edges) - 1 else (">=%s" % lo)
        bar = "#" * min(40, int(40.0 * b / max(h["buckets"])))
        print("      %-12s %6d  %s" % (span, b, bar))
        lo = hi


# --------------------------------------------------------------------------
# phases
# --------------------------------------------------------------------------

def phase0_preflight(pcan, kvaser):
    print("\n=== Phase 0: preflight ===")
    st = json.loads(dev_get("/check_status"))
    print("  device   %s  can=%s/%s  wifi=%s  git=%s"
          % (st["device_id"], st["can_datarate"], st["can_mode"],
             st["wifi_mode"], st["git_version"]))
    ok = st["can_datarate"] == "500K" and st["can_mode"] == "normal"
    if not ok:
        print("  FAIL: device CAN must be 500K/normal to see the bench")
        return False

    set_mode(MODE_OFF)
    print("  gen_inhibit reachable, mode set OFF")

    while kvaser.recv(timeout=0.0) is not None:
        pass
    for i in range(10):
        pcan.send(can.Message(arbitration_id=0x123, data=bytes([i] * 8),
                              is_extended_id=False), timeout=0.5)
        time.sleep(0.002)
    got = 0
    deadline = time.time() + 0.5
    while time.time() < deadline and got < 10:
        m = kvaser.recv(timeout=0.1)
        if m is not None and not m.is_error_frame:
            got += 1
    print("  link check PCAN->Kvaser: %d/10 frames" % got)
    if got < 10:
        print("  FAIL: bench segment is not carrying traffic")
        return False

    # Firmware that reports bus_on arms the TWAI driver itself (it calls
    # can_enable() on arm and refuses to arm if the bus will not come up), so
    # the SLCAN workaround is neither needed nor wanted -- using it would mean
    # measuring a path we do not actually ship.
    if "bus_on" in read_stats():
        print("  firmware self-arms the bus (bus_on reported); skipping SLCAN")
        return True

    try:
        return slcan_bus_up(pcan, kvaser)
    except Exception as exc:
        print("  FAIL: %s" % exc)
        return False


def run_arm(name, mode, frames, pcan, kvaser, offset_us, busy):
    """One cell of the sweep: a mode crossed with a WiFi load condition."""
    print("\n--- %s ---" % name)
    set_mode(mode, offset_us)
    time.sleep(0.3)

    load = HttpLoad() if busy else None
    if load:
        load.__enter__()
        time.sleep(1.0)

    # 1 s of priming frames, then a mid-stream re-arm that zeroes the
    # histograms with traffic already flowing. Everything before the re-arm is
    # discarded on both sides.
    prime = frames[:100]
    rearm = {}
    t_rearm = threading.Thread(target=rearm_after,
                               args=(1.0, mode, offset_us, rearm), daemon=True)
    t_rearm.start()
    try:
        captured, elapsed = replay_and_capture(pcan, kvaser, prime + frames)
    finally:
        t_rearm.join(timeout=5)
        if load:
            load.__exit__()
    if rearm.get("error"):
        print("  WARNING: mid-stream re-arm failed (%s); rx_gap carries a "
              "stale first sample" % rearm["error"])

    # Read BEFORE disarming. gen_inhibit_set_mode() calls
    # gen_inhibit_reset_stats() on every mode change, disarm included, so
    # set_mode(OFF) wipes the histograms. Reading afterwards returns zeros and
    # looks exactly like "the device received nothing".
    stats = read_stats()
    set_mode(MODE_OFF)

    raw_path = save_raw_capture(name, captured, len(prime))

    # Discard the priming window on the host side too, so both measurements
    # cover the same interval.
    seen = 0
    for i, (_t, a, _d) in enumerate(captured):
        if a == VCM_ID:
            seen += 1
            if seen > len(prime):
                captured = captured[i:]
                break

    n_051 = sum(1 for _t, a, _d in captured if a == VCM_ID)
    n_probe = sum(1 for _t, a, _d in captured if a == PROBE_ID)
    n_sent = len(prime) + len(frames)
    print("  replayed %d frames (%d primed) in %.1fs (%.1f Hz); "
          "Kvaser saw %d x 0x051, %d x 0x7F0 in the measured window"
          % (n_sent, len(prime), elapsed, n_sent / elapsed, n_051, n_probe))
    if load:
        print("  http load: %d requests, %d errors during the window"
              % (load.requests, load.errors))

    edges = stats["bucket_edges_us"]
    print("  device-side:")
    print_hist(stats["rx_gap"], edges, "rx_gap")
    if mode == MODE_RESPOND:
        print_hist(stats["response"], edges, "response")
    print("  device counters: other_frames=%d ctr_ok=%d ctr_bad=%d tx_ok=%d tx_fail=%d"
          % (stats["other_frames"], stats["ctr_ok"], stats["ctr_bad"],
             stats["tx_ok"], stats["tx_fail"]))

    result = dict(name=name, mode=mode, offset_us=offset_us, busy=busy,
                  sent=n_sent, primed=len(prime), elapsed=elapsed, kvaser_051=n_051,
                  kvaser_probe=n_probe, device=stats, kvaser_raw=raw_path)

    if mode == MODE_RESPOND:
        deltas, stragglers = pair_response_times(captured)
        print("  host-side (Kvaser hardware timestamps):")
        result["host"] = describe(deltas, "0x051->0x7F0")
        result["host_stragglers"] = stragglers
        if stragglers:
            print("    %d unpaired frames (probe landed outside its own gap)"
                  % stragglers)
    if load:
        result["http_requests"] = load.requests
        result["http_errors"] = load.errors
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="0,1,2,3,4")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--offset", type=int, default=500)
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "timing_bench.json"))
    args = ap.parse_args()
    phases = set(int(p) for p in args.phases.split(","))

    n_frames = int(args.seconds * 100)
    frames = load_0x051(REPLAY_LOG, n_frames)
    print("loaded %d real 0x051 payloads from %s" % (len(frames), REPLAY_LOG))
    if len(frames) < n_frames:
        print("  (log holds fewer than requested; using what it has)")

    configs = can.detect_available_configs(["pcan", "kvaser"])
    found = {}
    for cfg in configs:
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
    pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"],
                   bitrate=BITRATE)
    kvaser = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"],
                     bitrate=BITRATE)
    results = []
    slcan_sock = None
    try:
        slcan_sock = phase0_preflight(pcan, kvaser)
        if not slcan_sock:
            return 1

        if 1 in phases:
            print("\n=== Phase 1: OBSERVE, WiFi idle ===")
            results.append(run_arm("observe_idle", MODE_OBSERVE, frames,
                                   pcan, kvaser, args.offset, busy=False))
        if 2 in phases:
            print("\n=== Phase 2: OBSERVE, WiFi busy ===")
            results.append(run_arm("observe_busy", MODE_OBSERVE, frames,
                                   pcan, kvaser, args.offset, busy=True))
        if 3 in phases:
            print("\n=== Phase 3: RESPOND, WiFi idle ===")
            results.append(run_arm("respond_idle", MODE_RESPOND, frames,
                                   pcan, kvaser, args.offset, busy=False))
        if 4 in phases:
            print("\n=== Phase 4: RESPOND, WiFi busy ===")
            results.append(run_arm("respond_busy", MODE_RESPOND, frames,
                                   pcan, kvaser, args.offset, busy=True))
    finally:
        try:
            set_mode(MODE_OFF)
        except Exception:
            pass
        if slcan_sock is not None and slcan_sock is not True:
            try:
                slcan_sock.close()
            except Exception:
                pass
        for b in (pcan, kvaser):
            try:
                b.shutdown()
            except Exception:
                pass

    if not os.path.isdir(os.path.dirname(args.out)):
        os.makedirs(os.path.dirname(args.out))
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        json.dump(results, fh, indent=2)
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
