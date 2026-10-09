"""
Phase-locked synchroniser for the VCM's 0x051 transmit slot.

Pure: no imports, integer-free float arithmetic only, time passed in as a
parameter. This is the part that would port to a micro.

The job is to answer, at any moment, two questions about the VCM's *next*
0x051 transmission:

    when will it be sent, and what rolling-counter value will it carry?

The counter is what makes this tractable. A plain timing PLL that misses a
few frames cannot tell "the VCM skipped a slot" from "my estimate of the
period is wrong" -- it silently slips a cycle and its phase estimate is then
wrong by a multiple of the period forever. 0x051's B5 counter increments by
exactly one per transmission (measured: 6422 of 6422 steps in
generator-on-then-off.log), so the counter delta *is* the slot count. The PLL
uses it to resolve how many slots elapsed and only uses elapsed time to
disambiguate the 16-slot (160 ms) wrap.

Everything here is an estimate of what *we observed*, which is the VCM's
transmission plus our own controller's receive latency. Constant receive
latency is absorbed into the phase and is harmless -- it shifts our idea of
"now" and the VCM's slot by the same amount. Latency *jitter* is not
absorbed, and is exactly what `phase_err_p99` measures and what the guard
band must cover.
"""

COUNTER_MOD = 16


class SlotSync:
    """Tracks the VCM's 0x051 slot in time and in counter space."""

    def __init__(
        self,
        nominal_period=0.010,      # s, 100 Hz
        period_tolerance=0.02,     # fractional clamp on the period estimate
        alpha=0.20,                # phase correction gain
        beta=0.004,                # period correction gain
        lock_threshold=0.0015,     # s, |phase error| considered "in lock"
        lock_count=25,             # consecutive good observations to declare lock
        unlock_count=5,            # consecutive bad observations to drop lock
        err_window=200,            # observations retained for the error percentile
    ):
        self.nominal_period = nominal_period
        self.period_min = nominal_period * (1.0 - period_tolerance)
        self.period_max = nominal_period * (1.0 + period_tolerance)
        self.alpha = alpha
        self.beta = beta
        self.lock_threshold = lock_threshold
        self.lock_count = lock_count
        self.unlock_count = unlock_count
        self.err_window = err_window

        self.period = nominal_period
        self.t_ref = None            # modelled time of the last observed slot
        self.last_counter = None
        self.last_seen = None        # wall time of the last observation
        self.slots_seen = 0

        self.locked = False
        self._good = 0
        self._bad = 0
        self._errs = []

        # diagnostics
        self.cycle_slips = 0         # counter delta disagreed with elapsed time
        self.counter_repeats = 0     # same counter twice: never seen from the VCM
        self.gaps = 0                # more than one slot elapsed

    # -- observation ------------------------------------------------------

    def observe(self, t, counter):
        """Feed one genuine VCM 0x051 arrival. Returns the phase error, or None."""
        if self.t_ref is None:
            self.t_ref = t
            self.last_counter = counter % COUNTER_MOD
            self.last_seen = t
            self.slots_seen = 1
            return None

        n = self._slots_elapsed(t, counter)
        if n <= 0:
            # A repeat of the counter we already hold. The VCM does not do this,
            # so it is either our own injected frame fed back (echo not
            # suppressed) or a genuinely anomalous bus. Do not steer on it.
            self.counter_repeats += 1
            return None
        if n > 1:
            self.gaps += 1

        predicted = self.t_ref + n * self.period
        err = t - predicted

        # PI update. The period correction is divided by the slot count so a
        # long gap does not apply an outsized period step.
        self.t_ref = predicted + self.alpha * err
        self.period += self.beta * err / n
        if self.period < self.period_min:
            self.period = self.period_min
        elif self.period > self.period_max:
            self.period = self.period_max

        self.last_counter = counter % COUNTER_MOD
        self.last_seen = t
        self.slots_seen += n

        self._errs.append(err if err >= 0.0 else -err)
        if len(self._errs) > self.err_window:
            del self._errs[0]

        if (err if err >= 0.0 else -err) <= self.lock_threshold:
            self._good += 1
            self._bad = 0
            if self._good >= self.lock_count:
                self.locked = True
        else:
            self._bad += 1
            self._good = 0
            if self._bad >= self.unlock_count:
                self.locked = False
        return err

    def _slots_elapsed(self, t, counter):
        """How many transmit slots passed since the last observation.

        The counter fixes this modulo 16; elapsed time picks the wrap.
        """
        c = counter % COUNTER_MOD
        by_counter = (c - self.last_counter) % COUNTER_MOD
        by_time = (t - self.last_seen) / self.period
        # nearest slot count congruent to by_counter (mod 16)
        k = round((by_time - by_counter) / COUNTER_MOD)
        n = by_counter + k * COUNTER_MOD
        if n <= 0:
            # by_counter == 0 and no wrap resolved -> a true repeat
            if by_counter == 0 and by_time < COUNTER_MOD * 0.5:
                return 0
            n = by_counter + (k + 1) * COUNTER_MOD
        # did time and counter tell the same story?
        if abs(by_time - n) > COUNTER_MOD * 0.5:
            self.cycle_slips += 1
        return n

    # -- prediction -------------------------------------------------------

    def predict_next(self):
        """(time, counter) of the VCM's next 0x051, or None if not yet tracking."""
        if self.t_ref is None:
            return None
        return (self.t_ref + self.period,
                (self.last_counter + 1) % COUNTER_MOD)

    def predict_slot(self, k):
        """(time, counter) of the slot k transmissions ahead (k >= 1)."""
        if self.t_ref is None:
            return None
        return (self.t_ref + k * self.period,
                (self.last_counter + k) % COUNTER_MOD)

    # -- how much do we trust the prediction? -----------------------------

    def phase_err_p99(self):
        """99th-percentile absolute phase error over the recent window.

        This is the number the guard band is sized from. Returns None until
        there is enough history to mean anything.
        """
        n = len(self._errs)
        if n < 20:
            return None
        s = sorted(self._errs)
        i = int(0.99 * (n - 1))
        return s[i]

    def phase_err_max(self):
        return max(self._errs) if self._errs else None

    @property
    def tracking(self):
        """Weaker than `locked`: we know the period and the counter, but make
        no claim about predicting a future transmit time to sub-millisecond.

        A trailing frame only needs this. It is placed relative to a frame
        already received, so what it requires is a period estimate good enough
        to know the next slot is roughly a period away -- not a phase estimate.
        Gating it on `locked` meant that transmitting, which degrades our own
        receive timestamps, dropped the lock and stopped us transmitting.
        """
        return (self.t_ref is not None
                and self.last_counter is not None
                and self.slots_seen >= 10)

    def stale(self, now, max_slots=5.0):
        """True if we have not heard the VCM recently enough to trust anything.

        Expressed in transmit slots, not seconds. An absolute constant here was
        a bug: at a stretched bench period every scheduled lead frame -- which
        by construction is due most of a period after the last reception --
        looked stale and was thrown away, so the lead half of the bracket
        strategy silently never fired.
        """
        if self.last_seen is None:
            return True
        return (now - self.last_seen) > max_slots * self.period
