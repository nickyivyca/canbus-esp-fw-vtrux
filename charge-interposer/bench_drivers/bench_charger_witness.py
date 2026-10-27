"""Is there a WIRE witness for the charger-side filler, and can it be trusted?

Spec 12 entry 2 records the filler's burst shape and mean rate. Those are
properties of the charger segment's wire, and `bench_rate_feasibility.py`
could only report them as queue-accept figures, labelled UNWITNESSED --
because the Innomaker is the only adapter on that segment and it is the
transmitter.

The review session named the candidates and set the bar: gs_usb echoes may
serve, but **show that they track the wire before relying on them**. It also
ruled out the obvious shortcut, correctly: the Kvaser seeing the bridged
`0x100` on the vehicle side is not a charger-side witness, because the board's
forwarding sits in between and reshapes the timing.

WHY NOT THE BOARD'S OWN COUNTERS. The board IS a second device on the charger
segment, and its serial status line carries `c2v` -- frames received on the
charger port and forwarded. That is a genuine independent witness for the
MEAN. It cannot give the burst shape: the line prints every 5 s, and bursts of
8 at a 1,000 fps mean need sub-millisecond resolution. The diagnostic pages do
not help either -- `0x7F6` carries `intp_modified`, `intp_synth`,
`intp_rx_overflow` and `intp_tx_fail_expected`, and no charger-port receive
total. So the board is used here as a cross-check on the count, not as the
shape witness.

THE DISCRIMINATOR FOR ECHOES, and it is one that can fail. If an echo means
"this frame has been transmitted", then the echo rate cannot exceed the wire's
own limit however fast frames are offered. An 8-byte standard frame at
500 kbit is about 111 bits plus interframe space, so the ceiling is near
4,500 fps. So:

  - offer far above the ceiling (10,000 fps) and the echo rate must PLATEAU
    near it. If echoes instead track the offer past the ceiling, they are
    reporting queue acceptance and are useless for this, exactly as counting
    send() returns was.
  - the echo count should also agree with what the board received.

Both halves are required. A plateau alone could be the USB path saturating,
and a matching count alone says nothing about timing.

    py -3.14 bench_charger_witness.py            # validate, then measure
    py -3.14 bench_charger_witness.py --validate-only
"""

import argparse
import datetime
import os
import sys
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_pacer                                            # noqa: E402
import can                                                    # noqa: E402

class Tee(object):
    """stdout to the console and to the artifact.

    Added 2026-10-06 at the review session's request, and it is the same
    rule the rate run already followed: the plateau validation and the
    filler shape existed only inside a message, so a figure quoted from
    them had nothing on disk behind it.
    """

    def __init__(self, path):
        self.f = open(path, "w", encoding="ascii", errors="replace")
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        self.f.write(s)

    def flush(self):
        self.out.flush()
        self.f.flush()


BITRATE = 500000
FILLER_ID = 0x100
DLC = 8

# An 8-byte standard data frame: 1 SOF + 11 id + RTR + IDE + r0 + 4 DLC
# + 64 data + 15 CRC + delim + ACK slot + delim + 7 EOF = 108 bits, plus
# 3 bits of interframe space, before stuffing. Stuffing can add up to
# about 19 bits worst case; the all-0x5A payload used here stuffs little.
FRAME_BITS = 111
WIRE_CEILING = BITRATE / float(FRAME_BITS)        # ~4,504 fps


class EchoWitness(object):
    """Arrival times of our own frames coming back from the adapter.

    For gs_usb/candleLight the device returns an echo once the frame has
    been handed to the CAN core, so arrival order and spacing may reflect
    the wire. `validate()` is what decides whether they actually do --
    this class does not assume it.
    """

    def __init__(self, bus, want_id):
        self.bus = bus
        self.want_id = want_id
        self.stamps = []
        self.other = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run)
        self._t.daemon = True

    def _run(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except Exception:
                return
            if m is None or getattr(m, "is_error_frame", False):
                continue
            if m.arbitration_id == self.want_id:
                self.stamps.append(time.perf_counter())
            else:
                self.other += 1

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=3.0)

    def reset(self):
        self.stamps = []
        self.other = 0

    def rate(self):
        """(count, span_s, fps). Span from the echoes themselves."""
        s = self.stamps
        if len(s) < 2:
            return (len(s), 0.0, 0.0)
        span = s[-1] - s[0]
        return (len(s), span, (len(s) / span) if span else 0.0)

    def shape(self, burst):
        """Did the offered burst SHAPE survive to the echoes?

        Returns (within_ms, between_ms, bursts_seen). A burst of 8 should
        show 7 short gaps then one long one; if every gap is the same the
        shape did not survive, whatever the mean says.
        """
        s = self.stamps
        if len(s) < burst * 3:
            return (None, None, 0)
        gaps = [(s[i + 1] - s[i]) * 1000.0 for i in range(len(s) - 1)]
        gaps_sorted = sorted(gaps)
        # The long gaps are the inter-burst ones: one per burst, so the
        # top (len/burst) of the distribution.
        n_long = max(1, len(gaps) // burst)
        longs = gaps_sorted[-n_long:]
        shorts = gaps_sorted[:-n_long] or [0.0]
        return (sum(shorts) / len(shorts), sum(longs) / len(longs),
                n_long)


def open_gs_usb():
    import libusb_package
    d = os.path.dirname(libusb_package.get_library_path())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    return can.Bus(interface="gs_usb", channel="can0", index=0,
                   bitrate=BITRATE)


def offer(bus, rate, seconds, burst, w):
    payload = bytearray([0x5A] * DLC)
    msg = can.Message(arbitration_id=FILLER_ID, is_extended_id=False,
                      data=bytearray(payload))

    def send():
        bus.send(msg, timeout=0.05)

    w.reset()
    o = bench_pacer.offer(send, rate, seconds, burst)
    time.sleep(1.5)                     # let the queue and echoes flush
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--out", default=None,
                    help="artifact path; default is beside this script, "
                         "stamped with the time so runs cannot overwrite "
                         "each other")
    args = ap.parse_args()

    # with no --out, the default output goes where it went before the
    # move: SeaDrive's interposer-firmware/ folder, never this tree
    import paths
    here = None if args.out else paths.fixtures()
    out = args.out or os.path.join(
        here, "bench_charger_witness_%s_%s.txt"
        % (datetime.date.today().isoformat(),
           datetime.datetime.now().strftime("%H%M%S")))
    tee = Tee(out)
    sys.stdout = tee
    try:
        rc = _run(args)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("\nsaved verbatim to %s" % out)
    return rc


def _run(args):
    print("run at %s" % datetime.datetime.now().isoformat())

    print("charger-side wire witness for the spec 12 entry 2 filler")
    print("theoretical ceiling for an 8-byte standard frame at %d bit/s:"
          % BITRATE)
    print("  %d bits incl. interframe space -> %.0f fps\n"
          % (FRAME_BITS, WIRE_CEILING))

    try:
        bus = open_gs_usb()
    except Exception as exc:
        print("FAILED to open the Innomaker: %s" % exc)
        return 2
    w = EchoWitness(bus, FILLER_ID).start()
    try:
        # --- does anything come back at all? ---------------------------
        o = offer(bus, 200, 2.0, 1, w)
        n, span, fps = w.rate()
        print("probe, 200 fps offered for 2 s: queued %.0f fps, echoes %d"
              % (o.queued_fps, n))
        if n == 0:
            print("\nNO ECHOES. This adapter returns nothing for its own")
            print("transmissions, so it cannot witness its own wire and the")
            print("filler's shape has no charger-side witness on this bench.")
            print("Report that rather than substituting the vehicle-side")
            print("view of the bridged frames, which measures the board's")
            print("forwarding and not the charger segment.")
            return 1
        print("  echoes do arrive (%d over %.2f s, %.0f fps)" % (n, span, fps))

        # --- THE DISCRIMINATOR: offer far past the wire ceiling --------
        print("\nvalidation -- offer 10,000 fps, which the wire cannot carry:")
        o = offer(bus, 10000, args.seconds, 1, w)
        n, span, fps = w.rate()
        print("  queued %.0f fps (send() returns), echoes %.0f fps over "
              "%.2f s" % (o.queued_fps, fps, span))
        print("  wire ceiling %.0f fps" % WIRE_CEILING)
        ratio = fps / WIRE_CEILING if WIRE_CEILING else 0.0
        if ratio > 1.15:
            print("  ECHOES EXCEED THE WIRE by %.0f%% -- they are reporting"
                  % ((ratio - 1) * 100))
            print("  queue acceptance, not transmission, and cannot witness")
            print("  the wire. Same defect as counting send() returns.")
            return 1
        if ratio < 0.5:
            print("  echoes are far BELOW the ceiling (%.0f%%), so something"
                  % (ratio * 100))
            print("  else is limiting them -- most likely the USB path. A")
            print("  witness slower than the wire undercounts the filler and")
            print("  cannot be used either.")
            return 1
        print("  PLATEAU at %.0f%% of the ceiling: consistent with echoes"
              % (ratio * 100))
        print("  being emitted per transmission rather than per queueing.")
        if args.validate_only:
            return 0

        # --- the spec plan, as seen by the echoes ----------------------
        print("\nthe spec filler: 1,000 fps mean in bursts of 8, %.0f s"
              % args.seconds)
        o = offer(bus, 1000, args.seconds, 8, w)
        n, span, fps = w.rate()
        within, between, nb = w.shape(8)
        print("  queued      %.0f fps" % o.queued_fps)
        print("  ON THE WIRE %.0f fps (%d echoes over %.2f s)"
              % (fps, n, span))
        if within is not None:
            print("  burst shape: %.3f ms within a burst, %.3f ms between "
                  "bursts, %d bursts" % (within, between, nb))
            print("  one 8-byte frame occupies %.3f ms of wire, and a burst"
                  % (1000.0 * FRAME_BITS / BITRATE))
            print("  of 8 at a 1,000 fps mean repeats every %.1f ms"
                  % (8 * 1000.0 / 1000))
            if between > within * 3:
                print("  the BURST SHAPE SURVIVED to the wire: inter-burst "
                      "gaps are %.1fx the within-burst gaps" %
                      (between / within if within else 0))
            else:
                print("  the burst shape did NOT survive: gaps are "
                      "indistinguishable, so this is a steady stream at the "
                      "same mean however it was offered")
        else:
            print("  too few echoes to judge the shape")
        return 0
    finally:
        w.stop()
        try:
            bus.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
