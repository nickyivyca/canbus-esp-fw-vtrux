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
    python3.13 -B bench_charger_witness.py --self-test | --can-fail

OPENER AND OFFER. open_gs_usb (role canchg) is the Innomaker opener the
other drivers import (bench_rate_feasibility's copy, retired 2026-11-23,
was the same opener). Since 2026-11-23 (the charge-interposer reviewer's
pace ruling) offer() offers through bench_offer (can-tools' pace, ended by
time at the seconds asked; bench_pacer's before), and an Innomaker that
does not open prints a VOID line (the reviewer's sweep ruling on premise
failures; before, a FAILED line and exit 2). --self-test and --can-fail
cover those two (2026-11-23); the measurement itself is hand-run.
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
import adapter_roles               # noqa: E402  (every adapter by its bench role)

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
    # role canchg in libusb mode (shared/bench SPEC section 8, 2026-11-18)
    return adapter_roles.canchg(BITRATE)


def offer(bus, rate, seconds, burst, w, flush_s=1.5):
    """The filler offered through bench_offer (ended by time; bench_pacer's
    until 2026-11-23), the witness reset first, then flush_s for the queue
    and the echoes to flush. Its queued_fps is bench_pacer's: sends that
    returned over the offer's elapsed time."""
    import bench_offer
    payload = bytearray([0x5A] * DLC)
    msg = can.Message(arbitration_id=FILLER_ID, is_extended_id=False,
                      data=bytearray(payload))

    def send():
        bus.send(msg, timeout=0.05)

    bench_offer.rp()                    # canbench loaded before the witness's reset
    w.reset()
    o = bench_offer.offer(send, rate, seconds, burst)
    time.sleep(flush_s)                 # let the queue and echoes flush
    return o


def premise_line(what, e):
    """A premise not met (the Innomaker that did not open) as a VOID line
    (2026-11-23, the reviewer's sweep ruling: a premise failure prints a VOID
    line rather than a bare non-zero exit)."""
    return "VOID: %s did not open (%s: %s): nothing measured" % (what, type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--out", default=None,
                    help="artifact path; default is beside this script, "
                         "stamped with the time so runs cannot overwrite "
                         "each other")
    ap.add_argument("--self-test", action="store_true",
                    help="offer() and the premise line, no hardware (2026-11-23)")
    ap.add_argument("--can-fail", action="store_true",
                    help="run --self-test against each MUTANTS edit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)

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
        print(premise_line("the Innomaker (role canchg)", exc))
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


def self_test():
    """No hardware (2026-11-23): offer() on bench_offer with a stand-in bus
    and witness (the witness reset before the first send, the filler frame,
    the count by time on time and running late, bursts whole, queued_fps),
    and _run() with an Innomaker that does not open (a VOID line, exit 2)."""
    import contextlib
    import io
    import types
    fails = []

    def check(ok, what):
        print("  %s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)
    events = []

    class TxStandIn(object):
        def __init__(self, cost=0.0):
            self.cost, self.msgs = cost, []

        def send(self, msg, timeout=None):
            t = time.perf_counter() + self.cost
            while time.perf_counter() < t:
                pass
            events.append("send")
            self.msgs.append((msg.arbitration_id, msg.is_extended_id, bytes(msg.data)))

    class W(object):
        def reset(self):
            events.append("reset")
    b = TxStandIn()
    o = offer(b, 200, 0.5, 1, W(), flush_s=0.0)
    check(events[:2] == ["reset", "send"] and events.count("reset") == 1,
          "the witness reset once, before the first send")
    check(abs(o.sent - 100) <= 1 and set(b.msgs) == {(FILLER_ID, False, bytes([0x5A] * DLC))},
          "200 fps for 0.5 s: %d of 0x%X, DLC 8, 0x5A" % (o.sent, FILLER_ID))
    check(abs(o.queued_fps - 200.0) < 5.0,
          "queued_fps: sends returned over the offer's time (%.1f)" % o.queued_fps)
    o = offer(TxStandIn(), 1000, 0.205, 8, W(), flush_s=0.0)
    check(o.sent == 208,
          "bursts of 8 at 1,000 fps for 0.205 s: 26 whole bursts, 208 (%d)" % o.sent)
    o = offer(TxStandIn(0.002), 1000, 0.2, 1, W(), flush_s=0.0)
    check(o.elapsed < 0.205 and o.sent < 110,
          "running late (2 ms a send): ended by time, %.4f s, %d sent of 200"
          % (o.elapsed, o.sent))
    g = globals()
    saved = g["open_gs_usb"]

    def boom():
        raise OSError("no device")
    try:
        g["open_gs_usb"] = boom
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _run(types.SimpleNamespace(seconds=1.0, validate_only=True))
        voids = [x for x in buf.getvalue().splitlines() if x.startswith("VOID")]
    finally:
        g["open_gs_usb"] = saved
    check(rc == 2 and voids == ["VOID: the Innomaker (role canchg) did not open "
                                "(OSError: no device): nothing measured"],
          "an Innomaker that does not open: a VOID line, exit 2 (%s, %s)" % (rc, voids))
    print("\nbench_charger_witness self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py, since 2026-11-23): each edit must make
# --self-test fail
MUTANTS = [
    ("the witness reset after the offer",
     "    w.reset()\n    o = bench_offer.offer(send, rate, seconds, burst)\n",
     "    o = bench_offer.offer(send, rate, seconds, burst)\n    w.reset()\n"),
    ("pace: the offer by schedule alone (until_s), not by time",
     "    o = bench_offer.offer(send, rate, seconds, burst)\n",
     "    n = int(rate * seconds)\n"
     "    o = bench_offer.Offered(bench_offer.rp().pace(\n"
     "        ((k // burst * burst / float(rate), k) for k in range(n)),\n"
     "        lambda k: send(), until_s=seconds), n, 0, None, [], rate, burst, seconds)\n"),
    ("pace: the bursts ignored", "    o = bench_offer.offer(send, rate, seconds, burst)\n",
     "    o = bench_offer.offer(send, rate, seconds)\n"),
    ("the filler's payload", "    payload = bytearray([0x5A] * DLC)\n",
     "    payload = bytearray([0x00] * DLC)\n"),
    ("premise: no VOID line",
     "        print(premise_line(\"the Innomaker (role canchg)\", exc))\n", ""),
]


if __name__ == "__main__":
    sys.exit(main())
