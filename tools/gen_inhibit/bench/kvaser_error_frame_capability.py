"""Can the Kvaser witness report a CAN error frame AT ALL? Settle it by making some.

WHY THIS EXISTS, AND WHY IT IS A PRECONDITION RATHER THAN A RESULT.

Every arm recorded in `runs/` reports `error_frames=0` from the Kvaser witness, and
that figure has been quoted in support of "no error frames on the wire". It cannot
carry that weight until this script runs, because of a specific instrument gap:
python-can's Kvaser backend DOES decode `canMSG_ERROR_FRAME`
(`interfaces/kvaser/canlib.py:685`), but it opens the handle with only
EXCLUSIVE / OVERRIDE_EXCLUSIVE / ACCEPT_VIRTUAL / NO_INIT_ACCESS / CAN_FD and calls
`canIoCtl` only for SET_TIMER_SCALE, SET_LOCAL_TXECHO and SET_LOCAL_TXACK -- there is
no ioctl enabling error-frame reporting. So "0 error frames" and "this witness never
reports error frames" are, as things stand, THE SAME OBSERVATION. A number that reads
as evidence of a quiet bus may be evidence of nothing at all.

This is the AGENTS.md rule in its original form: a check whose failure mode is
indistinguishable from success is not a check. The fix is not to reason harder about
the backend -- it is to put real error frames on the wire and see whether the
instrument reports them.

ALSO REQUESTED BY THE INTERPOSER BOARD SESSION (2026-10-04), which has an open,
unexplained lower bound of ~225 bus-error events/s on this same vehicle segment from
the board's own TWAI counters. Whether a second, independent instrument can see error
frames at all is a precondition for their measurement too, so the device-side counters
below are collected for them as an outside view.

METHOD: three regimes, because one is not a measurement.

  baseline_before  Everything at 500 kbit. Clean frames, no errors expected.
                   This is the control for "does this instrument report zero when
                   zero is the truth".
  wrong_bitrate    The PCAN transmits at 250 kbit onto a segment whose other nodes
                   are at 500 kbit. Every frame is then misread by the receivers --
                   wrong bit boundaries produce stuff and form violations -- so REAL
                   error frames appear on the wire. This is the only regime in which
                   error frames are known to exist.
  baseline_after   Back to 500 kbit. Errors must stop. Without this the wrong-bitrate
                   reading cannot be told from a dongle that latched into a bad state
                   and keeps reporting.

WHAT EACH OUTCOME MEANS, decided in advance so the result is not read to taste:

  Kvaser reports errors ONLY in wrong_bitrate
      The witness can see error frames. Every historical `error_frames=0` becomes
      meaningful evidence of a quiet bus.
  Kvaser reports NOTHING anywhere, but the DEVICE's drv counters climb in
  wrong_bitrate
      The error frames were real and the Kvaser is blind to them. Every historical
      `error_frames=0` is uninformative and must be withdrawn as evidence.
  NEITHER sees anything in wrong_bitrate
      TEST FAULT. The method did not generate error frames, so nothing is settled.
      This MUST NOT be read as "no errors occurred" -- that is the exact
      misreading this script exists to prevent.
  Kvaser reports errors in a baseline regime too
      The reading is not attributable to the wrong bitrate; report and stop.

THE DEVICE IS A THIRD INSTRUMENT AND IT IS FREE. The WiCAN on this segment is in
mode 0 and will not transmit, but its TWAI driver is installed (`bus_on` is true in
mode 0), so `drv.bus_error`, `drv.rx_missed` and `rx_errors` count regardless. Note
the driver object is `heap_caps_calloc`'d at install, so these counters ZERO on every
arm/disarm cycle -- do not arm the device during this run or the baseline is reset
under you.

SAFETY AND COURTESY. This deliberately makes the segment unusable for the duration.
Agreed with the interposer board session before running. Nothing is flashed. The
PCAN is opened with auto_reset=True on purpose: at the wrong bitrate it WILL go
bus-off, and recovering is what keeps errors being generated instead of the
transmitter falling silent after the first burst.

    py -3.14 tools/gen_inhibit/bench/kvaser_error_frame_capability.py
"""

import json
import os
import sys
import time
import urllib.request


import can                                                    # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()
HOST = "192.168.80.1"

NOMINAL = 500000
WRONG = 250000
SECONDS = 6.0
SEND_PERIOD_S = 0.002        # 500 fps, brisk enough to make errors quickly
PROBE_ID = 0x123             # ours, not a truck id, not a diag id


def device_counters():
    """drv.* and rx_errors from the device, or None if it does not answer."""
    try:
        req = urllib.request.Request("http://%s/gen_inhibit" % HOST)
        with urllib.request.urlopen(req, timeout=5.0) as r:
            page, _ = json.JSONDecoder().raw_decode(
                r.read().decode("utf-8", "replace").lstrip())
    except Exception:
        return None
    drv = page.get("drv") or {}
    return {"bus_error": drv.get("bus_error"),
            "tx_failed": drv.get("tx_failed"),
            "arb_lost": drv.get("arb_lost"),
            "rx_missed": drv.get("rx_missed"),
            "rx_overrun": drv.get("rx_overrun"),
            "rx_errors": page.get("rx_errors"),
            "mode": page.get("mode"),
            "bus_on": page.get("bus_on")}


def regime(label, tx_bitrate, seconds=SECONDS):
    """Run one regime. Kvaser always at NOMINAL; the PCAN at tx_bitrate."""
    print("\n== %s: PCAN at %d, Kvaser at %d ==" % (label, tx_bitrate, NOMINAL))
    before = device_counters()

    # PCAN FIRST, then Kvaser -- the documented open order on this bench.
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                 bitrate=tx_bitrate, auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=NOMINAL)
    # Settle and drain: a freshly opened handle delivers a backlog for about a
    # second, and counting from the open inflates every figure.
    time.sleep(2.0)
    while kv.recv(timeout=0.0) is not None:
        pass
    while tx.recv(timeout=0.0) is not None:
        pass

    sent = send_err = 0
    normal = errframes = 0
    err_examples = []
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            t_send = time.perf_counter()
            try:
                tx.send(can.Message(arbitration_id=PROBE_ID,
                                    data=bytes([0x55] * 8),
                                    is_extended_id=False), timeout=0.05)
                sent += 1
            except Exception:
                send_err += 1
            m = kv.recv(timeout=0.0)
            while m is not None:
                if m.is_error_frame:
                    errframes += 1
                    if len(err_examples) < 3:
                        err_examples.append(repr(m)[:110])
                else:
                    normal += 1
                m = kv.recv(timeout=0.0)
            slack = SEND_PERIOD_S - (time.perf_counter() - t_send)
            if slack > 0:
                time.sleep(slack)
    finally:
        for b in (tx, kv):
            try:
                b.shutdown()
            except Exception:
                pass

    after = device_counters()
    delta = {}
    if before and after:
        for k in ("bus_error", "tx_failed", "arb_lost", "rx_missed",
                  "rx_overrun", "rx_errors"):
            a, b = before.get(k), after.get(k)
            delta[k] = (b - a) if (isinstance(a, int) and isinstance(b, int)
                                   ) else None

    print("  PCAN       sent %d, send exceptions %d" % (sent, send_err))
    print("  Kvaser     normal frames %d, ERROR FRAMES %d"
          % (normal, errframes))
    for e in err_examples:
        print("             example: %s" % e)
    if delta:
        print("  device     " + "  ".join("%s %+d" % (k, v)
                                          for k, v in sorted(delta.items())
                                          if isinstance(v, int)))
    else:
        print("  device     did not answer, so its counters are unavailable")
    return {"regime": label, "tx_bitrate": tx_bitrate, "seconds": seconds,
            "pcan_sent": sent, "pcan_send_exceptions": send_err,
            "kvaser_normal": normal, "kvaser_error_frames": errframes,
            "kvaser_error_examples": err_examples,
            "device_before": before, "device_after": after,
            "device_delta": delta}


def set_mode(mode):
    """POST a mode. Returns True on HTTP 200."""
    try:
        req = urllib.request.Request(
            "http://%s/gen_inhibit_set?mode=%d" % (HOST, mode),
            data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=8.0) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    #
    # --device-observe INSTALLS THE DEVICE'S TWAI DRIVER SO ITS COUNTERS WORK.
    #
    # The first run of this script got the verdict from the Kvaser alone and
    # the device contributed nothing: bus_on was false and drv.* came back
    # null, because in mode OFF after a fresh boot the TWAI driver is not
    # installed (can.c installs on arm and uninstalls on disarm), so there is
    # nothing counting.
    #
    # OBSERVE (mode 1) is the right instrument rather than INHIBIT: spec 3
    # makes it LISTEN-ONLY in hardware, so the driver is installed and
    # counting while the device cannot put a frame on the wire -- which is
    # what makes it safe to leave attached during a deliberate error storm.
    # INHIBIT would be an armed inhibitor on a broken bus for no benefit.
    #
    # This exists because the interposer board session has an open, unexplained
    # lower bound of ~225 bus-error events/s on this segment from the board's
    # own TWAI counters, and has no outside view of it. A C3 TWAI's own
    # bus_error count under an error storm of KNOWN origin is exactly the
    # calibration that figure lacks.
    #
    observe = "--device-observe" in sys.argv
    restored = None
    pre = device_counters()
    if observe:
        if pre is None:
            print("--device-observe asked for but the device is not "
                  "answering; continuing without it.")
            observe = False
        elif pre.get("mode") not in (0, None):
            print("--device-observe: device already in mode %r; leaving it "
                  "alone and not restoring anything." % pre.get("mode"))
            observe = False
        else:
            print("putting the device in OBSERVE (mode 1, listen-only per "
                  "spec 3) so its TWAI driver is installed and counting")
            if set_mode(1):
                time.sleep(2.0)
                restored = 0
                pre = device_counters()
                print("  device now: mode %s, bus_on %s, drv.bus_error %s"
                      % (pre.get("mode"), pre.get("bus_on"),
                         pre.get("bus_error")) if pre else "  no answer")
            else:
                print("  POST failed; continuing without the device view")
                observe = False
    try:
        return _run(pre, observe)
    finally:
        if restored is not None:
            ok = set_mode(restored)
            print("\nrestored device to mode %d: %s"
                  % (restored, "ok" if ok else "FAILED -- check it by hand"))


def _run(pre, observe):
    if pre is None:
        print("NOTE: the device is not answering on %s. The run can proceed "
              "on the Kvaser alone, but the second instrument -- the one that "
              "makes a Kvaser null interpretable -- will be missing." % HOST)
    else:
        print("device mode %s, bus_on %s, drv.bus_error %s"
              % (pre.get("mode"), pre.get("bus_on"), pre.get("bus_error")))
        if pre.get("mode") not in (0, 1, None):
            print("REFUSING: the device is in mode %r, which can TRANSMIT. "
                  "Only OFF (0) or OBSERVE (1, listen-only per spec 3) are "
                  "acceptable here -- an armed inhibitor on a deliberately "
                  "broken bus adds nothing and transmits into the run."
                  % pre.get("mode"))
            return 2
        if not pre.get("bus_on"):
            print("NOTE: bus_on is false, so the device's TWAI driver may not "
                  "be installed and its counters may stay flat for reasons "
                  "unrelated to the bus.")

    rows = [regime("baseline_before", NOMINAL),
            regime("wrong_bitrate", WRONG),
            regime("baseline_after", NOMINAL)]

    by = {r["regime"]: r for r in rows}
    wrong = by["wrong_bitrate"]
    base = (by["baseline_before"]["kvaser_error_frames"]
            + by["baseline_after"]["kvaser_error_frames"])
    dev_wrong = (wrong["device_delta"] or {}).get("bus_error")
    dev_base = ((by["baseline_before"]["device_delta"] or {}).get("bus_error")
                or 0) + ((by["baseline_after"]["device_delta"] or {}
                          ).get("bus_error") or 0)

    print("\n" + "=" * 72)
    print("VERDICT")
    print("  Kvaser error frames:  baselines %d, wrong_bitrate %d"
          % (base, wrong["kvaser_error_frames"]))
    print("  device drv.bus_error: baselines %s, wrong_bitrate %s"
          % (dev_base, dev_wrong))
    print()

    kv_saw = wrong["kvaser_error_frames"] > 0
    dev_saw = isinstance(dev_wrong, int) and dev_wrong > 0

    if base > 0:
        print("  INCONCLUSIVE -- the Kvaser reported error frames in a "
              "BASELINE regime too, so the wrong-bitrate reading is not "
              "attributable to the wrong bitrate. Investigate the bench "
              "before drawing anything from this.")
        verdict = "errors_in_baseline"
    elif kv_saw:
        print("  THE KVASER CAN SEE ERROR FRAMES: %d in wrong_bitrate, 0 in "
              "both baselines." % wrong["kvaser_error_frames"])
        print("  So every historical 'error_frames=0' from this witness is "
              "MEANINGFUL EVIDENCE of a quiet bus, and the python-can ioctl "
              "concern does not apply in practice on this adapter.")
        verdict = "kvaser_reports_error_frames"
    elif dev_saw:
        print("  THE KVASER IS BLIND TO ERROR FRAMES. It reported 0 while the "
              "device's own TWAI counted %d bus errors over the same window, "
              "so the frames were real." % dev_wrong)
        print("  Every historical 'error_frames=0' from this witness is "
              "UNINFORMATIVE and must be withdrawn as evidence of a quiet "
              "bus. It says only that the instrument does not report them.")
        verdict = "kvaser_blind_device_saw"
    else:
        print("  TEST FAULT -- NOTHING IS SETTLED. Neither instrument saw an "
              "error in wrong_bitrate, which means the method did not "
              "generate error frames rather than that none occurred.")
        print("  DO NOT read this as a quiet bus. Likely causes: the PCAN "
              "refused to transmit at all at the wrong bitrate (check "
              "pcan_sent above), or no node was in a state to signal an "
              "error. Fix the stimulus and re-run.")
        verdict = "test_fault_no_errors_generated"

    out = os.path.join(RUNS, "kvaser_error_frame_capability.json")
    if not os.path.isdir(RUNS):
        os.makedirs(RUNS)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "nominal_bitrate": NOMINAL, "wrong_bitrate": WRONG,
                   "verdict": verdict, "regimes": rows}, f, indent=1)
    print("\nwrote %s" % os.path.relpath(out, HERE))
    return 0 if verdict in ("kvaser_reports_error_frames",
                            "kvaser_blind_device_saw") else 1


if __name__ == "__main__":
    sys.exit(main())
