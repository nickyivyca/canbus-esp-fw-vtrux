"""One pacer for every bench run that offers frames at a stated rate.

WHY THIS EXISTS, 2026-10-05. The pacing loop inside
bench_rate_feasibility.py silently decided a measurement. On a late pass
it did:

    nxt = time.perf_counter()

which forgives every overslept time.sleep() and never makes the lost time
up. At a 2,000 fps target the interval is 500 us, well under the Windows
sleep granularity, so the loop overslept on essentially every pass, reset
its own schedule each time, and settled at whatever rate the sleep
granularity happened to give. That rate was 1,869 fps, which was then
reported as the PCAN adapter's ceiling and nearly went into the spec as
the rate the bench "can achieve".

It is not the adapter's ceiling. The same loop with the send replaced by
a no-op -- no CAN hardware involved at all -- produces 1,832 fps and
2,518 late passes, reproducing the "measured" figure to within run-to-run
noise. The number described the pacer, not the bus.

The lesson is the one this bench keeps re-teaching in new clothes: the
loop could not report that it had failed to keep up. `late` was counted
but treated as information rather than as a failure, and the achieved
rate it produced looked like a measurement of the hardware.

So this pacer makes falling behind VISIBLE AND SEPARATE from the mean:

  - it keeps the schedule. `nxt += interval` always; the schedule is
    never reset to the present, so lost time is owed and repaid.
  - it sleeps coarse and spins fine. time.sleep() only while more than
    SLEEP_FLOOR_S remains, and short by SLEEP_MARGIN_S so the overshoot
    lands inside the spin rather than past the deadline.
  - it reports max_lag_ms and catchup_frames. A mean rate hit by
    alternating long gaps with catch-up bursts is NOT a steady offer,
    and spec 12 entry 2 says "offered steadily". Those two numbers are
    how a reader tells the difference; the mean alone cannot.
  - it keeps the first send exception. A count of errors with no reason
    cost two runs to an `AttributeError` that read as a hardware fault.

A note on msg.data, which is a separate trap from the same afternoon:
can.Message.__init__ converts `data` to a bytearray. Assigning
`msg.data = bytes(...)` afterwards bypasses that, and the gs_usb backend
then calls .extend() on it and raises. The pcan backend tolerates it. So
the payload here stays a bytearray throughout.
"""

import time

# Spin below this much remaining rather than sleeping. Windows timer
# granularity is the reason: a sleep of less than about a millisecond
# routinely overshoots by more than its own duration.
SLEEP_FLOOR_S = 0.002
# Ask sleep() for less than the slack so its overshoot lands in the spin.
SLEEP_MARGIN_S = 0.0015
# The host's own scheduling jitter, below which a gap says nothing about
# the offer's shape. A pure spin loop on this machine still sees gaps of
# about a millisecond when the OS takes the core away, so a steadiness
# bound tighter than this is a check that fails on the scheduler rather
# than on the thing being measured -- at 4,000 fps the nominal interval
# is 250 us and three times it is 750 us, which is under the jitter and
# so can never pass however good the adapter is. Measured, not guessed:
# self_test() prints the gaps a no-op send produces.
JITTER_FLOOR_MS = 1.5


class Offer(object):
    """The result of one paced offer. Read `ok` LAST, not first.

    queued_fps     send() RETURNS ACCEPTED divided by elapsed. This is a
                   QUEUE-ACCEPT RATE AND NOT A WIRE RATE -- see below.
    witnessed_fps  frames actually seen on the wire by another adapter,
                   or None if nobody watched
    max_lag_ms     furthest the loop ever fell behind its own schedule
    catchup_frames frames sent while already behind schedule
    max_gap_ms     longest interval between consecutive sends

    QUEUEING IS NOT TRANSMISSION, corrected 2026-10-05 after the
    generator-inhibit session measured it on this bench. `bus.send()`
    returns when the frame is accepted by the driver's queue, not when it
    has been transmitted, so counting returns measures how fast a queue
    will take frames. They measured 12,683 fps at 500 kbit (292 %
    occupancy) and 30,225 fps at 250 kbit (1,390 % occupancy) that way,
    with 161,759 queue-full exceptions alongside -- numbers that are
    impossible on the wire and that nothing in the counting flagged.

    So `queued_fps` can certify only that the HOST kept its schedule. It
    cannot certify that the wire carried the frames, and a spec clause
    that says "offered at a mean of N fps" is about the wire. Hence
    `verdict()` REFUSES to pass on queueing alone when a witness was
    expected: attach `witness()` or ask for `require_witness=False` and
    say why.

    Their second error is worth the same guard: they then divided a
    witness count by the SEND window while the drain thread kept counting
    through a 3 s queue flush, giving 163 % occupancy. The span has to
    come from the witnessed frames themselves, which is what
    `witness(count, span_s)` takes.
    """

    def __init__(self, sent, errs, elapsed, max_lag, catchup, max_gap,
                 first_err, rate, burst, gaps=None):
        self.sent = sent
        self.errs = errs
        self.elapsed = elapsed
        self.max_lag_ms = max_lag * 1000.0
        self.catchup_frames = catchup
        self.max_gap_ms = max_gap * 1000.0
        self.first_err = first_err
        self.rate = rate
        self.burst = burst
        self.queued_fps = sent / elapsed if elapsed else 0.0
        self.witnessed_fps = None
        self.witnessed_count = None
        self.witnessed_span = None
        # THE GAP DISTRIBUTION, not just its worst case. A maximum over
        # 10,000 samples is one outlier and says nothing about the shape;
        # on the bench the 2,000 fps steady plan delivered exactly 2,000
        # fps on the wire and failed a steadiness bound on a single
        # 1.54 ms gap. Reporting p99 alongside it is what lets "steadily"
        # be judged on the distribution rather than on one sample --
        # without quietly loosening the bound, which would be the setup
        # deciding the verdict again.
        #
        # THESE ARE SENDER-SIDE GAPS AND THE NAMES NOW SAY SO. `offer()`
        # measures between `send()` RETURNS, and send() returns at
        # queueing -- the exact thing spec 12 entry 2 excludes when it
        # says the gaps are "measured on the wire, not counted at the
        # sender". They were scored as the steadiness verdict until
        # 2026-10-07, which is why the 2026-10-05 README table published
        # wire RATES beside SENDER gaps under one "MEASURED ON THE WIRE"
        # heading. Kept, because a sender/wire disagreement is itself
        # diagnostic (it localises a shortfall to the host or the wire),
        # but no verdict reads them: see `steady_spec()`.
        self._sender_gaps_ms = [x * 1000.0 for x in (gaps or [])]
        g = sorted(gaps or [])
        self.sender_gap_p50_ms = (g[len(g) // 2] * 1000.0) if g else 0.0
        self.sender_gap_p99_ms = (g[min(len(g) - 1, int(len(g) * 0.99))]
                                  * 1000.0 if g else 0.0)
        self.sender_gap_n = len(g)
        self.gaps_over_bound = None
        # Wire gaps, from the witness's per-frame driver timestamps.
        # Empty until `witness()` is given them, and `steady_spec()`
        # refuses rather than falling back.
        self._wire_gaps_ms = []
        self.wire_gap_p50_ms = None
        self.wire_gap_p99_ms = None
        self.wire_gap_max_ms = None
        self.wire_gap_n = 0
        self.wire_stamp_note = None

    @property
    def achieved_fps(self):
        """Deprecated: ambiguous between queueing and the wire.

        Kept so nothing breaks, but it returns the WITNESSED rate when
        one exists, because that is what "achieved" has to mean. Read
        `queued_fps` or `witnessed_fps` explicitly in new code.
        """
        return (self.witnessed_fps if self.witnessed_fps is not None
                else self.queued_fps)

    def witness(self, count, span_s, wire_gaps_ms=None, stamp_note=None):
        """Attach what another adapter actually SAW on the wire.

        `span_s` must be measured from the witnessed frames themselves --
        first to last -- and not from the send window. A drain thread
        keeps counting while the driver's queue flushes, so dividing a
        witness count by the send duration inflates the rate; that is
        how 163 % occupancy got reported on this bench.

        `wire_gaps_ms` is the per-frame gap series the witness computed
        from the DRIVER'S OWN timestamps (`m.timestamp`), in milliseconds.
        Supplying it is what lets `steady_spec()` render a verdict at all.

        IT MUST NOT BE perf_counter() AT recv(). The host sees frames in
        USB batches, so arrival-time gaps within a batch collapse toward
        zero and the gap between batches carries the whole batch -- a
        bimodal distribution owned by the USB poll interval, not the wire.
        The resolution probe (`bench_gap_resolution.py`, 2026-10-06)
        settled that the driver stamps are usable instead: both adapters
        tick at <= 1 us, finer than the 0.025 ms the 5 % band allows, over
        30,000 frames with no missing or duplicate sequence numbers.

        `stamp_note` carries whatever the witness wants said about the
        provenance of those stamps, and it is printed with the verdict.
        The two clocks still disagree by about 0.024 ms per gap, which is
        inside the band but is an accuracy question the reviewer has, so
        the note is the place that stays visible.
        """
        self.witnessed_count = count
        self.witnessed_span = span_s
        self.witnessed_fps = (count / span_s) if span_s else 0.0
        self.wire_stamp_note = stamp_note
        if wire_gaps_ms:
            self._wire_gaps_ms = list(wire_gaps_ms)
            w = sorted(self._wire_gaps_ms)
            self.wire_gap_p50_ms = w[len(w) // 2]
            self.wire_gap_p99_ms = w[min(len(w) - 1, int(len(w) * 0.99))]
            self.wire_gap_max_ms = w[-1]
            self.wire_gap_n = len(w)
        return self

    # SPEC 12 ENTRY 2's DEFINITION OF "STEADILY", as amended 2026-10-06:
    #
    #   "the median gap between the identifier's frames is within 5 % of
    #    the nominal interval (0.5 ms) and 99 % of the gaps are under
    #    twice it; the largest gap is recorded but not limited."
    #
    # This REPLACES the max-gap bound this file used to apply. That bound
    # was mine, not the spec's, and it rejected a run whose wire-measured
    # distribution was p50 0.50 / p99 0.89 ms on five outliers out of ten
    # thousand -- the max is exactly the wrong statistic for the question,
    # which is why the spec records it without limiting it.
    #
    # MEDIAN_TOL_FRAC IS NOW THE SPEC'S OWN FIGURE, not this file's. The
    # clause first read "equals the nominal interval", which cannot be
    # exact against a float median taken from a timer with its own
    # resolution, so the equality needed a width and 5 % was mine. The
    # review session put that to the user and the clause was amended to
    # carry the 5 % itself (reported 2026-10-06), so the constant below
    # cites entry 2 rather than flagging a local choice. The width still
    # has to do two jobs and does: timer granularity cannot fail a good
    # run, and the burst shapes still fail on the median, which is the
    # distinction the clause exists to draw -- bursts of 2 at a 2,000 fps
    # mean give a median of 1.00 ms against a 0.50 ms nominal, 100 % out.
    # The measured median is always printed regardless.
    MEDIAN_TOL_FRAC = 0.05                     # spec 12 entry 2

    def steady_spec(self, sender_side_unscored=False):
        """(ok, reason) under spec 12 entry 2's definition, ON WIRE GAPS.

        THE GAPS MUST COME FROM THE WIRE. Entry 2: the gaps are "recorded
        as measured on the wire, not counted at the sender". This method
        scored `offer()`'s sender-side gaps until 2026-10-07 and the
        numbers it produced looked exactly like wire numbers -- same
        units, same shape, plausible values -- which is the whole reason
        the clause exists. `send()` returns at queueing; a host that
        keeps a perfect schedule into a driver queue reports a perfect
        median while the wire does something else entirely.

        SO IT REFUSES RATHER THAN FALLING BACK. With no wire gaps
        attached it returns False with a reason that says what is
        missing. It does not quietly score the sender series, because a
        verdict that cannot tell which clock it read is the recurring
        defect on this bench in its purest form: a check that produces
        numbers either way and so cannot fail honestly.

        `sender_side_unscored=True` is for the NO-HARDWARE self-test and
        nothing else. There is no wire in that run, so the sender series
        is all there is; the reason string is prefixed to say so, and
        the result is not a spec verdict.

        THE NOMINAL IS THE PER-FRAME INTERVAL, NOT THE PER-BURST ONE.
        The spec says "the median gap between the identifier's FRAMES is
        within 5 % of the nominal interval (0.5 ms)", and 0.5 ms is
        1/2,000 --
        so it does not change when sends are grouped. Dividing by the
        burst instead made a burst of 2 compare its 1.00 ms median
        against a 1.00 ms "nominal" and pass, when failing it is the
        whole point of the clause: the review session's own worked
        example is "it fails bursts of 2 (p50 1.00 against 0.50)".

        ON BURSTS, THE WIRE SERIES IS BETTER THAN THE SENDER ONE and the
        old caveat here no longer applies to it. `offer()` records one
        gap per send PASS, so its burst-plan gaps were inter-burst gaps
        wearing a frame-gap label. The witness stamps every frame, so for
        burst > 1 the wire series is a true frame-gap distribution: the
        intra-burst gaps appear at the wire's own back-to-back spacing
        (about 0.222 ms for a standard 8-byte frame at 500 kbit) rather
        than being absent. A burst plan still fails the median, which is
        the verdict entry 2 wants, but now it fails on the distribution
        the clause names.
        """
        nominal = 1000.0 / self.rate
        if sender_side_unscored:
            if not self._sender_gaps_ms:
                return (False, "no gaps measured")
            p50, p99 = self.sender_gap_p50_ms, self.sender_gap_p99_ms
            gmax, n = self.max_gap_ms, self.sender_gap_n
            where = "SENDER-SIDE, NOT A SPEC VERDICT: "
        else:
            if not self._wire_gaps_ms:
                return (False,
                        "NOT MEASURED ON THE WIRE: no per-frame witness "
                        "timestamps were attached, so the gap distribution "
                        "entry 2 asks for does not exist for this run. "
                        "%d sender-side gaps were recorded and are NOT "
                        "scored: send() returns at queueing. Attach "
                        "witness(..., wire_gaps_ms=...) from the driver's "
                        "own m.timestamp."
                        % self.sender_gap_n)
            p50, p99 = self.wire_gap_p50_ms, self.wire_gap_p99_ms
            gmax, n = self.wire_gap_max_ms, self.wire_gap_n
            where = ""
        med_ok = abs(p50 - nominal) <= nominal * self.MEDIAN_TOL_FRAC
        p99_ok = p99 < 2.0 * nominal
        why = (where
               + "median %.3f ms against a nominal %.3f ms (%+.1f %%), "
                 "p99 %.3f ms against a limit of %.3f ms, max %.3f ms "
                 "recorded and not limited, over %d gap(s)"
               % (p50, nominal,
                  100.0 * (p50 - nominal) / nominal if nominal else 0.0,
                  p99, 2.0 * nominal, gmax, n))
        if self.wire_stamp_note and not sender_side_unscored:
            why += " [%s]" % self.wire_stamp_note
        if not med_ok:
            return (False, "median gap is not within %.0f %% of the "
                           "nominal interval: "
                           % (100.0 * self.MEDIAN_TOL_FRAC) + why)
        if not p99_ok:
            return (False, "99th percentile gap is not under twice the "
                           "nominal interval: " + why)
        return (True, why)

    def sender_vs_wire(self):
        """How far the host's own schedule differs from the wire's.

        Not a verdict and deliberately not one. It localises a shortfall:
        a sender median at nominal with a wire median above it puts the
        loss past `send()`, and both out together puts it in the pacer.
        Returns None when there is nothing to compare.
        """
        if not self._wire_gaps_ms or not self._sender_gaps_ms:
            return None
        return ("sender p50 %.3f ms vs wire p50 %.3f ms (%+.3f ms); "
                "sender gaps %d, wire gaps %d"
                % (self.sender_gap_p50_ms, self.wire_gap_p50_ms,
                   self.wire_gap_p50_ms - self.sender_gap_p50_ms,
                   self.sender_gap_n, self.wire_gap_n))

    def steady(self, tol_gaps=3.0):
        """Was the offer STEADY, not merely right on average?

        tol_gaps is in multiples of the nominal interval. A pacer that
        meets its mean by stalling for ten intervals and then sending ten
        frames back to back has the right mean and the wrong shape, and
        the arm's premise is the shape.
        """
        return self.max_gap_ms <= self.steady_bound_ms(tol_gaps)

    def steady_bound_ms(self, tol_gaps=3.0):
        """The gap bound actually applied, and why it is not just 3x."""
        nominal_ms = 1000.0 * self.burst / self.rate
        return max(tol_gaps * nominal_ms, JITTER_FLOOR_MS)

    def verdict(self, tol_rate=0.995, require_witness=True):
        """(ok, reason). Conditions stated separately.

        `require_witness=False` is for runs with no wire at all -- the
        no-hardware self-test -- and for nothing else. On the bench, a
        rate claim without a witness is a claim about a queue. It also
        selects the sender-side steadiness series, labelled as such,
        since a run with no wire has no wire gaps to score.
        """
        if self.errs:
            return False, ("%d send error(s); first: %s"
                           % (self.errs, self.first_err))
        if require_witness and self.witnessed_fps is None:
            return False, ("NOT MEASURED: %.0f fps were accepted by send(), "
                           "which is a queue-accept rate, not a wire rate. "
                           "Nothing witnessed the wire, so this cannot be "
                           "quoted as an offered rate."
                           % self.queued_fps)
        rate_seen = (self.witnessed_fps if self.witnessed_fps is not None
                     else self.queued_fps)
        if rate_seen < self.rate * tol_rate:
            return False, ("rate not achieved: %.0f of %d fps%s"
                           % (rate_seen, self.rate,
                              " on the wire" if self.witnessed_fps is not None
                              else " accepted by send()"))
        ok_steady, steady_why = self.steady_spec(
            sender_side_unscored=not require_witness)
        if not ok_steady:
            return False, "mean reached but NOT steady: " + steady_why
        where = ("on the wire" if self.witnessed_fps is not None
                 else "accepted by send() -- NOT a wire rate")
        return True, ("%.0f fps %s, %s"
                      % (self.achieved_fps, where, steady_why))

    def line(self, require_witness=True):
        ok, why = self.verdict(require_witness=require_witness)
        wit = ("wire %7.0f" % self.witnessed_fps
               if self.witnessed_fps is not None else "wire       -")
        # `maxgap` IS THE SENDER'S and the column says so. It was headed
        # "maxgap" beside a wire rate, which is how a sender figure got
        # published under a wire heading; entry 2's max is the wire one,
        # printed with the verdict.
        return ("%8d want  queued %8.0f  %s  errs %4d  sndmaxgap %6.2f ms  "
                "maxlag %7.2f ms  catchup %6d  %s"
                % (self.rate, self.queued_fps, wit, self.errs,
                   self.max_gap_ms, self.max_lag_ms, self.catchup_frames,
                   "OK" if ok else "FAIL: " + why))


def offer(send, rate, seconds, burst=1, seq_writer=None):
    """Call `send()` at `rate` frames/s mean, in groups of `burst`.

    `send` takes no arguments and raises on failure. `seq_writer`, if
    given, is called with the 0-based frame index before each send so the
    caller can stamp a sequence number into its own payload.

    Returns an Offer. Nothing here touches CAN, which is deliberate: the
    pacer is testable with no hardware, and had that been true in the
    first place the 1,869 would have been caught in seconds.
    """
    interval = float(burst) / rate
    sent = errs = catchup = 0
    first_err = None
    max_lag = 0.0
    max_gap = 0.0
    gaps = []
    t0 = time.perf_counter()
    nxt = t0
    prev = t0
    end = t0 + seconds

    while True:
        now = time.perf_counter()
        if now >= end:
            break
        # Behind schedule? Record it; do NOT forgive it by moving nxt.
        lag = now - nxt
        if lag > 0:
            if lag > max_lag:
                max_lag = lag
            catchup += burst
        for _ in range(burst):
            if seq_writer is not None:
                seq_writer(sent + errs)
            try:
                send()
                sent += 1
            except Exception as exc:
                errs += 1
                if first_err is None:
                    first_err = repr(exc)
        t = time.perf_counter()
        gap = t - prev
        if gap > max_gap:
            max_gap = gap
        gaps.append(gap)
        prev = t
        # THE SCHEDULE IS NEVER RESET. This is the whole fix.
        nxt += interval
        while True:
            slack = nxt - time.perf_counter()
            if slack <= 0:
                break
            if slack > SLEEP_FLOOR_S:
                time.sleep(slack - SLEEP_MARGIN_S)
            # else: fall through and spin, re-reading the clock.

    return Offer(sent, errs, time.perf_counter() - t0, max_lag, catchup,
                 max_gap, first_err, rate, burst, gaps)


def self_test(seconds=3.0):
    """Prove the pacer with no CAN present, and prove the OLD one wrong.

    Run this before trusting any bench rate figure. If the no-op case
    cannot hold the rate, the adapters were never the limit.
    """
    print("pacer self-test, NO CAN HARDWARE -- a rate that fails here was")
    print("never the adapter's fault.\n")

    def noop():
        pass

    def cost(seconds):
        def fn():
            t = time.perf_counter() + seconds
            while time.perf_counter() < t:
                pass
        return fn

    # THE SELF-TEST CANNOT SEE WIRE LENGTH, and it is worth being
    # explicit about that rather than letting a passing row imply more
    # than it shows. An extended DLC-3 frame is about 101 bits against a
    # standard one's 79, so roughly 202 us against 158 us of wire time at
    # 500 kbit/s -- but python-can's send() returns once the frame is
    # queued to the driver, not once it has been transmitted, so the wire
    # length is not the host-side cost. What the self-test can do is
    # bracket a range of send costs and show whether the SCHEDULE
    # survives them. 0.2 ms brackets above a plausible per-send cost for
    # the longer frame; only the bench run measures the real one.
    print("%-34s %s" % ("case", "result"))
    for label, fn in (("no-op send", noop),
                      ("0.1 ms send", cost(0.0001)),
                      ("0.2 ms send (extended bracket)", cost(0.0002))):
        for rate, burst in ((2000, 1), (4000, 1), (2000, 2)):
            o = offer(fn, rate, seconds, burst)
            # No wire exists in the self-test, so a witness cannot and
            # need not exist. This is the ONLY place that flag is right.
            ok, why = o.verdict(require_witness=False)
            print("%-34s %s"
                  % ("%s, %d fps, burst %d" % (label, rate, burst),
                     ("OK   " if ok else "FAIL ") + why))

    print("\nthe OLD loop, for comparison -- same no-op send, schedule reset")
    print("on every late pass, which is what produced 1,869 fps:")
    for rate in (2000,):
        iv = 1.0 / rate
        t0 = time.perf_counter()
        nxt = t0
        end = t0 + seconds
        n = late = 0
        while time.perf_counter() < end:
            n += 1
            nxt += iv
            slack = nxt - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            else:
                late += 1
                nxt = time.perf_counter()
        el = time.perf_counter() - t0
        print("  old loop, %d fps, no-op send: %.0f fps, %d late passes"
              % (rate, n / el, late))


if __name__ == "__main__":
    self_test()
