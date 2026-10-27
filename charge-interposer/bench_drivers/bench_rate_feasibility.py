"""Can this host actually OFFER the rates spec 12 entry 2 asks for?

Spec 12 entry 2 wants a sequence-numbered identifier offered steadily at
a mean of 2,000 fps for at least 50,000 frames on the vehicle segment,
plus charger-side filler in bursts of 8 at a mean of 1,000 fps. Both come
from python-can on Windows.

THIS IS A PREREQUISITE, NOT AN ARM. An earlier replay on this bench
sustained about 452 fps, so 2,000 fps from a Python send loop is a claim
that needs measuring before a run is built on it. A run that quietly
offered 400 fps and passed would be the same defect as every other one on
this bench: the setup deciding the verdict.

CORRECTED 2026-10-05, AND THE CORRECTION IS THE POINT OF THE FILE.
The first version of this script reported that the PCAN could sustain
only 1,869 of a requested 2,000 fps, and that figure was within an hour
of going into the spec as the rate the bench can achieve. It was not the
adapter. It was this script's own pacing loop, which reset its schedule
to the present on every late pass and so forgave every overslept
time.sleep() -- at a 500 us interval, which is under the Windows sleep
granularity, that is every pass. The same loop with the send replaced by
a no-op, no CAN hardware present at all, reproduces 1,672-1,869 fps; the
spread between runs is itself the tell, because a hardware ceiling does
not move 200 fps between invocations.

The pacer now lives in bench_pacer.py, is shared with the arm so the two
cannot drift, and is SELF-TESTED WITH NO HARDWARE BEFORE ANY ADAPTER IS
OPENED. That ordering is deliberate: a rate the pacer cannot hold against
a no-op send was never the adapter's fault, and finding that out after
opening the adapters is how the wrong conclusion got drawn the first
time.

What it measures, per adapter, from the wire's point of view:
  - frames accepted by send() per second, and send errors with the first
    exception kept
  - the achieved mean against the requested one
  - whether the offer was STEADY, not merely right on average: max gap
    between sends against the nominal interval. Spec 12 entry 2 says
    "offered steadily", and a mean hit by alternating stalls with
    catch-up bursts does not satisfy it.
  - whether a requested burst SHAPE survives (bursts of 8)

It transmits. The board is the only other node and acknowledges, so there
is no unacknowledged-transmitter storm (spec 2.2).

    py -3.14 projects/vtrux/notes/artifacts/interposer-firmware/bench_rate_feasibility.py

Output is written to bench_rate_feasibility_<date>.txt beside this script
as well as to stdout, because the run that produced the 1,869 left no
artifact and the figure had nothing behind it.
"""

import argparse
import datetime
import os
import sys
import threading
import time

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_pacer                                            # noqa: E402
import can                                                    # noqa: E402

BITRATE = 500000

# THE TARGET IS EXTENDED, per spec 12 entry 2 as amended 2026-10-05:
# "One extended (29-bit) identifier that the core does not read, the
# format of the identifier the hold exists for".
#
# 0x18FFDEC0 and the evidence that the core ignores it. The core reads
# exactly ten identifiers, all of them named constants in machine.h and
# machine.py:
#   extended: CMD_ID 0x18EFC000, CHG_STATUS 0x18FFD4C0,
#             CHG_HV_STATUS 0x18FFD7C0, CHG_PILOT 0x18FFD8C0,
#             CHG_INFO 0x18FFD9C0
#   standard: BMS_STATUS 0x410, BMS_LIMITS 0x420, BMS_DATA1 0x430,
#             BMS_DATA2 0x440, VCM_EVAP 0x649
# and it transmits DIAG 0x7F4-0x7F7 plus mirrorId(CMD_ID) = 0x18EFC0E1.
# 0x18FFDEC0 is none of those, and there is no wildcard or masked
# comparison anywhere in either core -- every read is an equality test
# against one of those constants, so an identifier that matches none of
# them reaches no handler.
#
# It is shaped like real traffic rather than arbitrary: source address
# 0xC0 is the charger's, and PGN 0xFFDE sits in the proprietary range
# beside the 0xFFD4/D7/D8/D9 pages the charger does use. Nothing on the
# bench transmits it -- the Innomaker plays the foreign node and sends
# the filler.
#
# PRIORITY: arbitration compares the 11-bit base, which for 0x18FFDEC0
# is 0x63F. The filler at 0x100 is lower and therefore higher priority,
# which is what entry 2 requires. Comparing against the full 29-bit
# value instead would get this backwards and still produce numbers.
TARGET_ID = 0x18FFDEC0
TARGET_EXT = True
FILLER_ID = 0x100          # higher priority than the target, DLC 8


class Tee(object):
    """stdout to the console and to the artifact, with no ceremony."""

    def __init__(self, path):
        self.f = open(path, "w", encoding="ascii", errors="replace")
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        self.f.write(s)

    def flush(self):
        self.out.flush()
        self.f.flush()


def open_gs_usb():
    import libusb_package
    d = os.path.dirname(libusb_package.get_library_path())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    return can.Bus(interface="gs_usb", channel="can0", index=0,
                   bitrate=BITRATE)


OPENERS = {
    "pcan": lambda: can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                            bitrate=BITRATE, auto_reset=True),
    "innomaker": open_gs_usb,
}

# The witness. It is on the VEHICLE segment alongside the PCAN (proven by
# bench_topology.py: both see 4/4 of the board's diagnostic pages, while
# the Innomaker sees none and takes the forwarded probe), so it can see
# the target identifier the PCAN transmits. It never transmits here.
WITNESS_NAME = "kvaser"


def open_witness():
    return can.Bus(interface="kvaser", channel=0, bitrate=BITRATE,
                   receive_own_messages=False)


def drain(bus, stop, counter):
    """Mandatory for gs_usb -- an undrained transmitter runs out of echo
    slots and starts failing -- and harmless everywhere else.

    ON ITS OWN THREAD, ALWAYS, and that is load-bearing rather than
    tidiness. The generator-inhibit session measured this on this bench
    on 2026-10-05: a single-threaded loop that interleaves send() with
    recv() paces itself at about 1 ms per iteration, giving 970 fps and
    22 % occupancy; moving the drain to its own thread took the same
    adapter to 4,424 fps and 98 % occupancy. Every sparse stimulus in
    that chain -- 23 %, 24 %, a "0.5 ms period" that came out at
    1.91 ms -- had this one cause, and sleep() granularity was a
    contributing factor that got the blame.
    """
    while not stop.is_set():
        try:
            m = bus.recv(timeout=0.05)
        except Exception:
            return
        if m is not None:
            counter[0] += 1


class Witness(object):
    """What an adapter actually SAW of one identifier, with its own span.

    Exists because send() returning is not transmission. Counting send()
    returns gave 292 % and 1,390 % wire occupancy on this bench -- a
    queue-accept rate wearing a wire rate's units. Only another adapter
    on the same segment can say what the wire carried.

    The span comes from the FIRST AND LAST WITNESSED FRAME, never from
    the send window: a drain thread keeps counting while the driver's
    queue flushes, and dividing by the send duration gave 163 %
    occupancy in the same investigation.

    PER-FRAME GAPS COME FROM `m.timestamp`, NOT FROM THE HOST CLOCK
    (2026-10-07, for spec 12 entry 2's "measured on the wire"). This
    class recorded `perf_counter()` at `recv()` and supplied only
    (count, span), so the steadiness verdict had nothing but sender
    gaps to score. Two reasons the host clock cannot do this job:

      - USB BATCHING. Frames arrive in driver batches, so arrival-time
        gaps inside a batch collapse toward zero and the gap before the
        batch carries all of them. The distribution that comes out is
        bimodal and belongs to the USB poll interval. It is not a wire
        measurement and it looks like one.
      - It is the same clock the sender uses, so a sender/wire
        comparison made with it cannot disagree in the one way that
        would be informative.

    The driver stamps were cleared for this by `bench_gap_resolution.py`
    (2026-10-06): both the Kvaser and the PCAN tick at <= 1 us against
    the 0.025 ms the 5 % band allows, 30,000 frames each, no missing or
    duplicate sequence numbers. The two clocks differ by about 0.024 ms
    per paired gap -- inside the band, and an open accuracy question
    with the reviewer, which is why `stamp_note()` keeps it attached to
    every verdict rather than leaving it in a note file.

    A DROPPED FRAME MUST NOT BECOME A LONG GAP. The payload carries a
    24-bit sequence number, so a hole is visible; gaps that span one are
    excluded from the distribution and counted separately. Without that
    a single lost frame contributes a 2x gap that inflates p99, and at
    the wrong moment it would fail a good run -- or, read the other way,
    be mistaken for a stall the wire never had.
    """

    def __init__(self, bus, want_id):
        self.bus = bus
        self.want_id = want_id
        self.first = None
        self.last = None
        self.count = 0
        self.other = 0
        # (driver timestamp, sequence number or None), arrival order.
        self.stamps = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run)
        self._t.daemon = True

    def _run(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except Exception:
                return
            if m is None:
                continue
            if getattr(m, "is_error_frame", False):
                continue
            if m.arbitration_id != self.want_id:
                self.other += 1
                continue
            now = time.perf_counter()
            if self.first is None:
                self.first = now
            self.last = now
            self.count += 1
            seq = None
            d = m.data
            if d is not None and len(d) >= 3:
                seq = (d[0] << 16) | (d[1] << 8) | d[2]
            self.stamps.append((m.timestamp, seq))

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2.0)

    def reset(self):
        self.first = self.last = None
        self.count = 0
        self.other = 0
        del self.stamps[:]

    def wire_gaps_ms(self):
        """(gaps_ms, note) from the DRIVER's stamps. Never the host's.

        Gaps are taken between frames whose sequence numbers are
        consecutive, so a hole contributes no gap. Returns an empty list
        with a note saying why when the stamps cannot carry the
        measurement -- an unusable clock has to refuse, not degrade,
        because a degraded gap series is indistinguishable from a real
        one once it is a number in a table.
        """
        s = list(self.stamps)
        if len(s) < 2:
            return ([], "fewer than 2 frames witnessed")
        zeros = sum(1 for t, _ in s if not t)
        if zeros:
            return ([], "%d of %d driver timestamps were 0 or absent, so "
                        "this backend does not stamp frames and the gaps "
                        "cannot be taken from the wire" % (zeros, len(s)))
        gaps, holes, backwards, nonseq = [], 0, 0, 0
        for (t0, q0), (t1, q1) in zip(s, s[1:]):
            dt = (t1 - t0) * 1000.0
            if dt < 0:
                backwards += 1
                continue
            if q0 is None or q1 is None:
                nonseq += 1
                gaps.append(dt)
                continue
            if ((q0 + 1) & 0xFFFFFF) != q1:
                holes += 1
                continue
            gaps.append(dt)
        if not gaps:
            return ([], "no gap survived filtering (%d backwards, %d across "
                        "a sequence hole)" % (backwards, holes))
        tick = min([g for g in sorted(set(gaps)) if g > 0] or [0.0])
        note = ("driver m.timestamp, %d gap(s) from %d frame(s); "
                "smallest nonzero step %.4f ms" % (len(gaps), len(s), tick))
        if holes:
            note += ("; %d gap(s) EXCLUDED as spanning a sequence hole"
                     % holes)
        if backwards:
            note += "; %d backwards stamp(s) dropped" % backwards
        if nonseq:
            note += ("; %d gap(s) kept with no sequence number (DLC < 3)"
                     % nonseq)
        return (gaps, note)

    def stamp_span_s(self):
        """First-to-last span on the DRIVER's clock, or None."""
        s = [t for t, _ in self.stamps if t]
        if len(s) < 2:
            return None
        return max(s) - min(s)

    def seen(self):
        """Every non-error frame this witness has counted, any id."""
        return self.count + self.other

    def result(self):
        """(count, span_s). Span is first-to-last witnessed, not wall time."""
        if self.count < 2 or self.first is None:
            return (self.count, 0.0)
        return (self.count, self.last - self.first)


def offer_on(bus, can_id, dlc, rate, seconds, burst, ext):
    """Offer `rate` fps mean of `can_id` through `bus`, via the shared pacer.

    The payload carries a 24-bit sequence number when the DLC allows it,
    which is what makes the charger-side capture readable without the
    buffer number the dongle cannot see.

    `ext` IS A PARAMETER AND NOT A CONSTANT, fixed 2026-10-05. This
    function hardcoded `is_extended_id=False` while the module defined
    `TARGET_EXT = True` just above it, so after entry 2 was amended to
    require an extended identifier the constant said extended and the
    transmitter still sent standard. I had reported the script as updated;
    the constant was, the frame construction was not.
    It produced no error -- `errs=0`, `queued 2000` -- and the only reason
    it surfaced is that the wire witness looked for 0x18FFDEC0 and saw 0
    of them beside 25,068 other frames. A queue-accept count would have
    called that a clean 2,000 fps and the arm would have run on the wrong
    frame format, which is the exact defect the review session caught in
    the host model a few hours earlier.

    msg.data STAYS A BYTEARRAY. can.Message.__init__ converts `data` to
    one; assigning bytes() afterwards bypasses that and the gs_usb
    backend then calls .extend() on it and raises. The pcan backend
    tolerates it, which is why only the Innomaker appeared broken and why
    it read as a hardware fault for two runs.
    """
    payload = bytearray([0] * dlc)
    msg = can.Message(arbitration_id=can_id, is_extended_id=ext,
                      data=bytearray(payload))

    def seq_writer(n):
        if dlc >= 3:
            payload[0] = (n >> 16) & 0xFF
            payload[1] = (n >> 8) & 0xFF
            payload[2] = n & 0xFF
            msg.data = bytearray(payload)

    def send():
        bus.send(msg, timeout=0.05)

    return bench_pacer.offer(send, rate, seconds, burst,
                             seq_writer=seq_writer if dlc >= 3 else None)


def settle_wire(w, label, idle_fps=50.0, limit=25.0):
    """Wait until THE WITNESS sees the vehicle segment go quiet.

    Added 2026-10-05, and it replaces settling on the transmitting
    adapter's own receive counter for vehicle-segment plans, because that
    measured the wrong wire. The first witnessed run reported the
    2,000 fps steady plan as `pcan idle at 0 fps after 0.3 s` and then the
    Kvaser counted 15,068 FOREIGN frames during the measurement window,
    against about 24 in every clean plan. The board was still forwarding
    the preceding charger-side filler plan's backlog -- 10,000 frames at
    roughly 4,000 fps is about 2.5 s of draining -- straight into the
    window being measured. The target's 10,000 frames then arrived
    compressed into 3.62 s of a 5 s offer, reading as 2,760 fps with a
    5.22 ms maximum gap, and the plan failed the steadiness check for a
    reason that had nothing to do with the pacer or the adapter.

    The witness is on the segment under test, so it is the only thing
    that can say the segment is quiet. Settling on the transmitter's
    counter asks a device about a backlog it is not carrying.
    """
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit:
        a0 = w.seen()
        time.sleep(0.25)
        fps = (w.seen() - a0) / 0.25
        if fps < idle_fps:
            waited = time.perf_counter() - t0
            print("    %s: wire idle at %.0f fps after %.1f s"
                  % (label, fps, waited))
            return waited
    print("    %s: WIRE STILL BUSY after %.0f s -- the figures below are "
          "under contention from the previous plan" % (label, limit))
    return limit


def settle(bus, rx, label, idle_fps=50.0, limit=20.0):
    """Wait until `bus` sees fewer than `idle_fps` frames per second.

    NOT silence. The vehicle segment carries the board's 0x7F4-0x7F7
    diagnostics for as long as the board is powered, so it never goes
    quiet -- the first version of this waited for zero traffic and
    reported "STILL BUSY" for its full 20 s limit on every vehicle-side
    plan, which is a check that cannot pass.

    A fixed sleep is the other wrong answer: a vehicle-side plan at
    thousands of fps leaves the board's charger-side queue draining for
    seconds, and 0.3 s guessed that away.
    """
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit:
        a = rx[0]
        time.sleep(0.25)
        fps = (rx[0] - a) / 0.25
        if fps < idle_fps:
            waited = time.perf_counter() - t0
            print("    %s idle at %.0f fps after %.1f s" % (label, fps, waited))
            return waited
    print("    %s STILL BUSY after %.0f s -- the figures below are under "
          "contention from the previous plan" % (label, limit))
    return limit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default=None,
                    help="artifact path; default is beside this script")
    ap.add_argument("--selftest-only", action="store_true",
                    help="run the no-hardware pacer check and stop")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    # A SELF-TEST RUN MUST NOT CLAIM THE BENCH RUN'S FILENAME. The
    # self-test opens no adapter and measures no hardware, so a file
    # named bench_rate_feasibility_<date>.txt containing only self-test
    # rows would be read later as the bench measurement -- an artifact
    # whose name asserts more than its contents, which is how the 1,869
    # travelled in the first place.
    # A SELF-TEST ARTIFACT GETS A UNIQUE NAME, added 2026-10-05.
    #
    # The date alone was not enough: the review session cited lines 13
    # and 16 of `..._selftest.txt` with two steadiness failures, and by
    # the time I looked the file had been overwritten by a later run of
    # the same day and those lines said something else. A citation by
    # line number into a file that is silently replaced is unverifiable,
    # and the reviewer had no way to know.
    #
    # It matters more here than for the bench run because the self-test's
    # steadiness verdict is NOT reproducible between runs -- it is a
    # maximum over a few thousand samples on a machine whose scheduler
    # takes the core away unpredictably, so the same case passes and
    # fails across invocations. Each run therefore needs to stay readable
    # as its own evidence.
    suffix = ("_selftest_%s" % datetime.datetime.now().strftime("%H%M%S")
              if args.selftest_only else "")
    out = args.out or os.path.join(
        here, "bench_rate_feasibility_%s%s.txt"
        % (datetime.date.today().isoformat(), suffix))
    tee = Tee(out)
    sys.stdout = tee
    try:
        return run(args, out)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("\nsaved verbatim to %s" % out)


def run(args, out):
    print("bench rate feasibility, %s" % datetime.datetime.now().isoformat())
    print("spec 12 entry 2 wants 2,000 fps steady (DLC 3) on the vehicle")
    print("side and 1,000 fps in bursts of 8 (DLC 8) on the charger side.")
    print()

    # STEP 1, BEFORE ANY ADAPTER IS OPENED. If the pacer cannot hold a
    # rate against a no-op send, nothing measured afterwards is about the
    # hardware. Getting this ordering wrong is exactly how 1,869 fps was
    # attributed to the PCAN.
    print("=" * 72)
    bench_pacer.self_test(seconds=min(3.0, args.seconds))
    print("=" * 72)
    print()
    if args.selftest_only:
        return 0

    plans = [
        # CHARGER SIDE FIRST, deliberately. Run after the vehicle-side
        # plans, the Innomaker was transmitting into a segment still
        # draining the board's forwarded backlog, and reported 0 sent /
        # 5,000 errors -- which reads as a broken adapter and was a
        # saturated wire.
        ("innomaker", FILLER_ID, 8, 1000, 8, False, "charger filler, SPEC shape"),
        ("innomaker", FILLER_ID, 8, 2000, 8, False, "charger filler, 2x probe"),
        # THE SPEC PLAN, and the one the arm will use: DLC 3, steady.
        ("pcan", TARGET_ID, 3, 2000, 1, TARGET_EXT,
         "vehicle target, SPEC: 2000 steady"),
        ("pcan", TARGET_ID, 3, 4000, 1, TARGET_EXT,
         "vehicle target, 4000 headroom probe"),
        ("pcan", TARGET_ID, 3, 1000, 1, TARGET_EXT,
         "vehicle target, 1000 reference"),
        # Burst shapes are kept only as a comparison. They are NOT a
        # fallback for a steady offer: the pacer's own self-test shows a
        # burst of 2 failing the steadiness check at a nominal 1 ms, so
        # the earlier reading that bursts "reached 2,000 where steady
        # could not" was the overshoot hiding inside a longer interval.
        ("pcan", TARGET_ID, 3, 2000, 2, TARGET_EXT,
         "vehicle target, burst 2 (comparison)"),
        ("pcan", TARGET_ID, 3, 2000, 4, TARGET_EXT,
         "vehicle target, burst 4 (comparison)"),
    ]

    buses, stops, threads, rx = {}, {}, {}, {}
    for name in sorted(set(p[0] for p in plans)):
        try:
            buses[name] = OPENERS[name]()
            rx[name] = [0]
            stops[name] = threading.Event()
            t = threading.Thread(target=drain,
                                 args=(buses[name], stops[name], rx[name]))
            t.daemon = True
            t.start()
            threads[name] = t
            print("opened %s" % name)
        except Exception as exc:
            print("FAILED to open %s: %s" % (name, exc))

    # THE WIRE WITNESS. Opened separately from the transmitters because it
    # is an instrument, not a plan. Without it, every figure below is a
    # queue-accept rate and the script says so rather than implying a wire
    # measurement it did not make.
    witness_bus = None
    try:
        witness_bus = open_witness()
        print("opened %s as the wire witness (vehicle segment, never "
              "transmits)" % WITNESS_NAME)
    except Exception as exc:
        print("NO WITNESS: %s would not open (%s). Every rate below will be "
              "reported as a queue-accept rate and NONE of them may be "
              "quoted as an offered rate." % (WITNESS_NAME, exc))

    try:
        print()
        results = []
        for name, cid, dlc, rate, burst, ext, why in plans:
            if name not in buses:
                continue
            # Witness only what is on the witness's own segment. The
            # Innomaker's filler is on the charger segment, where the
            # Kvaser is not, so a zero there would mean "wrong segment"
            # and not "nothing transmitted" -- a null reading that looks
            # like a failure.
            w = None
            if witness_bus is not None and name == "pcan":
                w = Witness(witness_bus, cid).start()
                settle_wire(w, name)
                w.reset()          # discard the settling window
            else:
                settle(buses[name], rx[name], name)
            o = offer_on(buses[name], cid, dlc, rate, args.seconds,
                         burst, ext)
            if w is not None:
                # Let the driver's queue flush before reading, then take
                # the span from the frames themselves.
                time.sleep(1.0)
                w.stop()
                count, span = w.result()
                gaps, note = w.wire_gaps_ms()
                # THE RATE AND THE GAPS COME OFF THE SAME CLOCK when the
                # driver stamps are usable. `result()`'s span is the host
                # clock at recv(), which is fine over tens of seconds but
                # is a different instrument; quoting a rate from one and
                # gaps from the other under one heading is the 2026-10-05
                # mistake in miniature. Both are printed, so a
                # disagreement between them is visible instead of being
                # resolved by whichever was reported.
                sspan = w.stamp_span_s()
                if sspan:
                    note += ("; span %.3f s driver vs %.3f s host (%+.1f ms)"
                             % (sspan, span, (sspan - span) * 1000.0))
                o.witness(count, sspan or span, wire_gaps_ms=gaps,
                          stamp_note=note)
                print("    witnessed %d frame(s) of 0x%X %s over %.2f s "
                      "(%.0f fps on the wire), %d other frame(s)"
                      % (count, cid, "ext" if ext else "std",
                         sspan or span, o.witnessed_fps or 0.0, w.other))
                print("    wire gaps: %s" % note)
                if not gaps:
                    print("    NO WIRE GAPS: the steadiness verdict below "
                          "will REFUSE rather than score sender gaps.")
                sv = o.sender_vs_wire()
                if sv:
                    print("    %s" % sv)
            results.append((name, why, o, w is not None))
            print("%-10s %-38s %s"
                  % (name, why, o.line(require_witness=(w is not None))))
            if o.first_err:
                print("    first send error: %s" % o.first_err)

        print("\nverdict:")
        for name, why, o, had_witness in results:
            ok, reason = o.verdict(require_witness=had_witness)
            tag = "ACHIEVED " if ok else "NOT achieved -- "
            if not had_witness:
                tag = "UNWITNESSED, queue-accept only -- "
                ok = False
            print("  %-10s %-38s %s" % (name, why, tag + reason))

        print("\nA rate that is not achieved here cannot be achieved by the")
        print("arm either, and an arm that offers less than the spec says is")
        print("not the specified run however it is labelled. A rate that is")
        print("achieved on the mean but fails the steadiness check is also")
        print("not the specified run: entry 2 says OFFERED STEADILY.")
        return 0
    finally:
        for name in buses:
            stops[name].set()
        for t in threads.values():
            t.join(timeout=2.0)
        for b in buses.values():
            try:
                b.shutdown()
            except Exception:
                pass
        if witness_bus is not None:
            try:
                witness_bus.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
