"""
Generator-inhibit core.

Pure: no imports. Time in, frames out. This is the part that would port to a
micro; everything else in this folder exists to exercise it.

WHAT IT DOES
------------
It transmits 0x051 frames carrying zero generator torque, timed against the
VCM's own 0x051 slot, so that the GENE MCU's most recent (or only accepted)
torque command is always zero and the engine is never commanded to crank.

You cannot erase a frame from a CAN bus by transmitting one. The genuine VCM
frame is always received by the GENE MCU. Inhibition therefore has to work at
the layer above: either by being the frame the GENE MCU *acts on*, or by
making the genuine frame the one it *discards*. Which of those applies depends
on how the GENE MCU consumes 0x051, which is not known -- see models.py.
Three polarities are provided because they win under different receiver
models:

  lead    Transmit in the gap just BEFORE the VCM's predicted slot, carrying
          the counter value the VCM is about to use. If the GENE MCU validates
          the rolling counter, the VCM's genuine frame then looks like a
          repeat and is discarded. This is the only polarity under which the
          GENE MCU can see *zero* genuine torque commands. It needs an
          accurate forward prediction of the VCM's transmit time, which is the
          risky part.

  trail   Transmit a short fixed delay AFTER each genuine frame, carrying the
          next counter. Purely reactive -- no forward prediction at all -- and
          it still steals the next counter value, so it beats a
          counter-validating receiver as well as a last-value-wins one. What
          it cannot do is stop the genuine frame being briefly the freshest.

  bracket Both. Costs ~4.5% extra bus load and covers every receiver model
          considered.

THE THING THAT CAN BREAK THE TRUCK
----------------------------------
Two nodes transmitting the SAME CAN ID at overlapping times do not arbitrate
-- the identifier fields are identical, so both survive arbitration and run on
into the data field, where the first differing bit makes one of them detect a
bit error. That destroys the frame, raises both nodes' transmit error counters
and, repeated at 100 Hz, walks the VCM toward error-passive and then bus-off.
A VCM that has gone bus-off is a truck that has stopped.

So every transmission here is gated on a guard band: the core refuses to
transmit unless it can place the frame wholly inside a window that is clear of
the VCM's predicted slot by more than the synchroniser's own recent worst-case
phase error. If it is not locked, if the prediction has gone loose, or if the
VCM has gone quiet, it emits nothing at all. Silence is the safe state: the
truck then behaves exactly as it does today.
"""

TORQUE_ZERO = 0x8000          # centred encoding: raw 0x8000 == 0 Nm
IDLE_PAYLOAD = (0x08, 0x00, 0x80, 0xFF, 0x7F)   # B0..B4 of a genuine engine-off frame

# A 6-byte standard data frame at 500 kbit/s: 95 bits + worst-case stuffing,
# plus the 3-bit interframe space. ~0.23 ms. Rounded up for headroom.
FRAME_TIME = 0.00030

STRATEGIES = ("lead", "trail", "bracket")
PAYLOAD_MODES = ("mirror", "idle")


class Inhibitor:
    def __init__(
        self,
        sync,
        strategy="bracket",
        payload_mode="mirror",
        lead=0.0020,             # s before the predicted VCM slot
        trail_delay=0.0005,      # s after an observed genuine frame
        min_guard=0.0005,        # s, absolute floor on the clearance
        guard_factor=3.0,        # multiples of the recent p99 phase error
        max_guard=0.0035,        # s, beyond this the slot is too uncertain to use
        trail_guard=0.0015,      # s, clearance a trailing frame keeps from the
                                 # NEXT VCM slot. This one number is where our
                                 # ignorance of the VCM's own transmit jitter
                                 # lives -- see can_trail().
        tx_latency=0.0002,       # s, our own controller's queue-to-wire budget
        arm_mode="continuous",   # "continuous" | "on_demand"
        frame_time=FRAME_TIME,
        target_torque=0,         # counts we hold once engaged; 0 = inhibit
        max_tx_per_slot=3.0,     # hard ceiling on our own transmit rate
    ):
        if strategy not in STRATEGIES:
            raise ValueError(strategy)
        if payload_mode not in PAYLOAD_MODES:
            raise ValueError(payload_mode)

        self.sync = sync
        self.strategy = strategy
        self.payload_mode = payload_mode
        self.lead = lead
        self.trail_delay = trail_delay
        self.min_guard = min_guard
        self.guard_factor = guard_factor
        self.max_guard = max_guard
        self.trail_guard = trail_guard
        self.tx_latency = tx_latency
        self.frame_time = frame_time
        self.arm_mode = arm_mode
        self.max_tx_per_slot = max_tx_per_slot
        self._tx_times = []
        self.rate_tripped = False
        self.suppressed_rate = 0

        # What we hold once engaged. Zero is the inhibit case and the default.
        # The mechanism does not care what this number is: holding +342 to
        # backdrive the engine for a smog readiness run is the same transaction
        # as holding 0 to stop it cranking. The difference is in the
        # consequences of a frame leaking through -- see README.
        self.target_torque = target_torque
        # "off" -> nothing on the wire at all;  "holding" -> transmitting
        self.state = "holding" if arm_mode == "continuous" else "off"
        self._last_genuine = None      # B0..B4 of the most recent genuine frame
        self._pending = []             # [(due_time, counter, kind)]

        # counters, for reporting
        self.sent = 0
        self.sent_lead = 0
        self.sent_trail = 0
        self.suppressed_unlocked = 0
        self.suppressed_guard = 0
        self.suppressed_late = 0
        self.suppressed_stale = 0
        self.last_reason = "init"

    # -- external control -------------------------------------------------

    def engine_running(self):
        """Is the engine turning, according to the VCM's own last 0x051?

        B3-B4 of the same frame we are already synchronised to is the engine
        RPM reference, and it reads -1 (raw 0x7FFF) with the engine off. So
        this needs no second message and no second sensor -- it is the command
        stream itself saying whether there is anything running.

        Returns None if we have not seen a frame yet.
        """
        g = self._last_genuine
        if g is None:
            return None
        return (((g[4] << 8) | g[3]) - 32768) >= 0

    def arm(self, allow_running=False):
        """Begin transmitting. Returns (ok, reason).

        Refuses if the generator is already running. Taking over a loaded
        generator and commanding zero torque sheds the engine's entire load in
        one frame, which is a load dump on a running engine -- the wrong way to
        find out whether the mechanism works. The point of the exercise is to
        stop a start that has not happened yet, and that is the only state it
        will arm in. `allow_running` exists for deliberate bench work and is
        not something to pass on the truck without a reason.
        """
        if self.state != "off":
            return True, "already armed"
        running = self.engine_running()
        if running is None:
            return False, "no VCM frame seen yet"
        if running and not allow_running:
            return False, "generator already running -- refusing to engage"
        if self._vcm_torque() != 0 and not allow_running:
            return False, ("VCM is commanding %d counts -- refusing to engage"
                           % self._vcm_torque())
        self.state = "holding"
        return True, "armed"

    def disarm(self):
        """Stop transmitting. The VCM's command takes effect on its next frame."""
        self.state = "off"
        self._pending = []

    abort = disarm

    @property
    def armed(self):
        return self.state != "off"

    def _vcm_torque(self):
        g = self._last_genuine
        if g is None:
            return 0
        return ((g[2] << 8) | g[1]) - 32768

    # -- guard band -------------------------------------------------------

    def guard(self):
        """Required clearance between our frame and the VCM's predicted slot.

        Sized from the synchroniser's own recent phase error, not from a
        constant, so that a bus which turns out to be jittery widens the guard
        automatically -- and, once the guard cannot fit, stops us transmitting
        rather than risking a same-ID collision.
        """
        p99 = self.sync.phase_err_p99()
        if p99 is None:
            return None
        g = self.guard_factor * p99
        return g if g > self.min_guard else self.min_guard

    def can_lead(self):
        """Is there room to place a frame wholly before the VCM's slot?"""
        g = self.guard()
        if g is None:
            return False, "no error history"
        if g > self.max_guard:
            return False, "phase error too large"
        need = self.frame_time + self.tx_latency + g
        if self.lead < need:
            return False, "lead %.4f ms < needed %.4f ms" % (self.lead * 1e3, need * 1e3)
        if self.lead > self.sync.period - need:
            return False, "lead does not fit inside the period"
        return True, "ok"

    def can_trail(self):
        """A trailing frame is NOT gated on the phase-prediction error.

        That was a bug, and an expensive one: measured on a CPython host, the
        act of transmitting delayed our own receive timestamps enough to
        inflate the phase-error estimate, which widened the guard, which
        suppressed the next transmission -- a feedback loop that shut the
        inhibitor down to about 1.5 % duty while reporting a healthy lock.

        A trailing frame is anchored to a frame we have already RECEIVED, not
        to a predicted future one, so the phase error is the wrong quantity
        entirely. What it must clear is the VCM's *next* slot, which is one
        period away from an event we observed. The uncertainty in that is the
        VCM's own transmit jitter -- which these captures cannot measure -- so
        it is carried explicitly in `trail_guard` rather than smuggled in via a
        number that happens to be large.
        """
        end = self.trail_delay + self.tx_latency + self.frame_time
        if end > self.sync.period - self.trail_guard:
            return False, ("trail %.2f ms + latency does not clear the next slot"
                           % (self.trail_delay * 1e3))
        return True, "ok"

    # -- the two entry points ---------------------------------------------

    def on_genuine(self, t, data):
        """A genuine VCM 0x051 was received at time t with payload `data`."""
        if len(data) < 6:
            return
        self.sync.observe(t, data[5] & 0x0F)
        self._last_genuine = tuple(data[:5])

        if self.state == "off":
            return

        nxt = self.sync.predict_next()
        if nxt is None:
            return
        t_next, c_next = nxt

        # Each polarity is gated on the quality it actually depends on. A
        # trailing frame answers a frame we have already received and needs
        # only the period and the counter; a leading frame is aimed at a slot
        # that has not happened yet and needs a phase estimate we can defend.
        if self.strategy in ("trail", "bracket"):
            if not self.sync.tracking:
                self.suppressed_unlocked += 1
                self.last_reason = "trail: not tracking yet"
            else:
                ok, why = self.can_trail()
                if ok:
                    self._pending.append((t + self.trail_delay, c_next, "trail"))
                else:
                    self.suppressed_guard += 1
                    self.last_reason = "trail: " + why

        if self.strategy in ("lead", "bracket"):
            if not self.sync.locked:
                self.suppressed_unlocked += 1
                self.last_reason = "lead: not locked"
            else:
                ok, why = self.can_lead()
                if ok:
                    self._pending.append((t_next - self.lead, c_next, "lead"))
                else:
                    self.suppressed_guard += 1
                    self.last_reason = "lead: " + why

    def poll(self, now):
        """Return the frames due to be transmitted at `now`.

        Each is (arbitration_id, payload tuple of 6 ints). The caller puts them
        on the wire. Anything whose slot has already passed is dropped rather
        than sent late -- a late frame is a frame heading for the VCM's slot.
        """
        if not self._pending:
            return []
        out = []
        keep = []
        for due, counter, kind in self._pending:
            if now < due:
                keep.append((due, counter, kind))
                continue
            if self.sync.stale(now):
                self.suppressed_stale += 1
                self.last_reason = "VCM stale"
                continue
            # How late are we? Anything that would land inside the guard of the
            # VCM's slot is abandoned.
            if not self._slot_is_clear(now, kind):
                self.suppressed_late += 1
                self.last_reason = "%s: too late, slot not clear" % kind
                continue
            if not self._rate_ok(now):
                # A runaway transmitter on a live powertrain bus is far worse
                # than anything this tool is trying to prevent, so the ceiling
                # lives in the pure core where no I/O bug can bypass it, and
                # tripping it latches us off rather than merely throttling.
                # It has fired for real: on a transport that echoes our own
                # frames back, the reactive trail path fed itself and reached
                # 24,000 frames a second.
                self.suppressed_rate += 1
                if not self.rate_tripped:
                    self.rate_tripped = True
                    self.last_reason = ("TX RATE CEILING TRIPPED (%.0f/s) -- "
                                        "latched off" % self._rate(now))
                    self.abort()
                return out
            out.append((0x051, self._payload(counter)))
            self.sent += 1
            if kind == "lead":
                self.sent_lead += 1
            else:
                self.sent_trail += 1
            self.last_reason = "sent " + kind
        self._pending = keep
        return out

    def _rate(self, now):
        return len([t for t in self._tx_times if t > now - 1.0])

    def _rate_ok(self, now):
        """Sliding one-second ceiling on our own transmissions."""
        if self.rate_tripped:
            return False
        cutoff = now - 1.0
        if len(self._tx_times) > 2000:
            self._tx_times = [t for t in self._tx_times if t > cutoff]
        n = 0
        for t in self._tx_times:
            if t > cutoff:
                n += 1
        limit = self.max_tx_per_slot / self.sync.period
        if n >= limit:
            return False
        self._tx_times.append(now)
        return True

    def _slot_is_clear(self, now, kind):
        """Would a frame started at `now` finish clear of the VCM's slot?

        Only the FORWARD edge needs guarding. Scheduling is triggered by
        reception, and a CAN frame is timestamped when it completes, so the
        previous VCM frame is provably off the wire before we are ever asked
        to transmit. What is uncertain is the *next* one, and the guard is
        sized from the synchroniser's own recent phase error so that a bus
        which turns out to be jittery pushes us off the air rather than into
        a same-ID collision.
        """
        if self.sync.last_seen is None or now < self.sync.last_seen:
            return False
        finish = now + self.tx_latency + self.frame_time

        if kind == "trail":
            # Measured from the frame we actually received, one period ago.
            return finish <= self.sync.last_seen + self.sync.period - self.trail_guard

        g = self.guard()
        if g is None:
            return False
        nxt = self.sync.predict_next()
        if nxt is None:
            return False
        return finish <= nxt[0] - g

    # -- payload ----------------------------------------------------------

    def _payload(self, counter):
        """Build the inhibit frame.

        "mirror" copies the VCM's most recent frame and overwrites only the
        torque field. That changes exactly one thing on the bus: everything
        else the GENE MCU cross-checks -- the state byte, the engine RPM
        reference it uses as a plausibility check against measured RPM -- stays
        consistent with what the VCM is really saying.

        "idle" sends the byte-exact engine-off command instead. It is more
        emphatically "off", but it also null-references the RPM the engine may
        actually be turning at, which the GENE inverter is documented as
        cross-checking.
        """
        if self.payload_mode == "idle" or self._last_genuine is None:
            b = IDLE_PAYLOAD
        else:
            b = self._last_genuine
        raw = (self.target_torque + TORQUE_ZERO) & 0xFFFF
        return (b[0],
                raw & 0xFF,
                (raw >> 8) & 0xFF,
                b[3], b[4],
                counter & 0x0F)

    # -- reporting --------------------------------------------------------

    def status(self):
        g = self.guard()
        return {
            "armed": self.armed,
            "state": self.state,
            "target": self.target_torque,
            "locked": self.sync.locked,
            "period_ms": self.sync.period * 1e3,
            "phase_err_p99_ms": (self.sync.phase_err_p99() or 0) * 1e3,
            "guard_ms": (g * 1e3) if g is not None else None,
            "sent": self.sent,
            "sent_lead": self.sent_lead,
            "sent_trail": self.sent_trail,
            "suppressed_unlocked": self.suppressed_unlocked,
            "suppressed_guard": self.suppressed_guard,
            "suppressed_late": self.suppressed_late,
            "suppressed_stale": self.suppressed_stale,
            "suppressed_rate": self.suppressed_rate,
            "rate_tripped": self.rate_tripped,
            "counter_repeats": self.sync.counter_repeats,
            "last_reason": self.last_reason,
        }
