"""
Hypotheses about how the GENE MCU consumes 0x051, and how to score an attempt
to inhibit it.

We do not know which of these the GENE MCU implements, and no capture can tell
us -- the receiver's behaviour is not observable on the bus. So the inhibitor
is scored against all of them, and a strategy is only worth taking to the
truck if it wins under every model we cannot rule out.

Each model consumes the ordered stream of frames that actually reach the GENE
MCU -- genuine and injected alike, because the bus does not distinguish them --
and produces the torque command the MCU would be acting on over time.
"""

COUNTER_MOD = 16


def torque_of(payload):
    return ((payload[2] << 8) | payload[1]) - 32768


class _Base:
    name = "base"

    def __init__(self):
        self.timeline = []      # [(t, torque)] whenever the effective command changes

    def _emit(self, t, tq):
        if not self.timeline or self.timeline[-1][1] != tq:
            self.timeline.append((t, tq))


class PerFrame(_Base):
    """Acts on every frame it receives, immediately. No filtering at all.

    The most permissive receiver, and the least forgiving to inhibit: the
    genuine command is always acted on, if only until the next injected frame.
    """
    name = "per_frame"

    def deliver(self, t, payload, mine):
        self._emit(t, torque_of(payload))

    def finish(self, t_end):
        pass


class LastWins(_Base):
    """Holds the newest frame in a mailbox and reads it on its own control tick.

    The classic AUTOSAR-style receive path. Whoever transmitted most recently
    before the tick is the one that gets obeyed.
    """
    name = "last_wins"

    def __init__(self, tick=0.010, phase=0.0):
        super().__init__()
        self.tick = tick
        self.phase = phase
        self.mailbox = None
        self.next_tick = None

    def deliver(self, t, payload, mine):
        if self.next_tick is None:
            self.next_tick = t + self.phase
        self._run_to(t)
        self.mailbox = payload

    def _run_to(self, t):
        while self.next_tick is not None and self.next_tick <= t:
            if self.mailbox is not None:
                self._emit(self.next_tick, torque_of(self.mailbox))
            self.next_tick += self.tick

    def finish(self, t_end):
        self._run_to(t_end)


class CounterValidating(_Base):
    """Requires B5 to increment by exactly one; discards anything else.

    This is the model under which pre-empting the VCM's counter value works
    outright: our frame consumes counter N+1, and the VCM's genuine frame,
    arriving afterwards with that same N+1, is thrown away without ever being
    acted on.

    `tolerant` relaxes "exactly +1" to "anything but a repeat", which is what a
    receiver that only cares about staleness would do. The outcome for us is
    the same; it is included so the claim does not rest on the stricter reading.
    """
    name = "counter_validating"

    def __init__(self, tolerant=False):
        super().__init__()
        self.tolerant = tolerant
        self.last_counter = None
        self.rejected = 0
        self.rejected_genuine = 0
        self.accepted_genuine = 0
        self.accepted_mine = 0
        if tolerant:
            self.name = "counter_tolerant"

    def deliver(self, t, payload, mine):
        c = payload[5] & 0x0F
        if self.last_counter is not None:
            step = (c - self.last_counter) % COUNTER_MOD
            ok = (step != 0) if self.tolerant else (step == 1)
            if not ok:
                self.rejected += 1
                if not mine:
                    self.rejected_genuine += 1
                return
        self.last_counter = c
        if mine:
            self.accepted_mine += 1
        else:
            self.accepted_genuine += 1
        self._emit(t, torque_of(payload))

    def finish(self, t_end):
        pass


class CounterValidatingLastWins(CounterValidating):
    """Counter validation in the receive filter, mailbox read on a control tick.

    The combination most likely to be true of a real production ECU.
    """
    name = "counter_then_tick"

    def __init__(self, tick=0.010, phase=0.0, tolerant=False):
        super().__init__(tolerant=tolerant)
        self.tick = tick
        self.phase = phase
        self.mailbox = None
        self.next_tick = None
        self.timeline = []

    def deliver(self, t, payload, mine):
        c = payload[5] & 0x0F
        if self.last_counter is not None:
            step = (c - self.last_counter) % COUNTER_MOD
            ok = (step != 0) if self.tolerant else (step == 1)
            if not ok:
                self.rejected += 1
                if not mine:
                    self.rejected_genuine += 1
                return
        self.last_counter = c
        if mine:
            self.accepted_mine += 1
        else:
            self.accepted_genuine += 1
        if self.next_tick is None:
            self.next_tick = t + self.phase
        self._run_to(t)
        self.mailbox = payload

    def _run_to(self, t):
        while self.next_tick is not None and self.next_tick <= t:
            if self.mailbox is not None:
                self._emit(self.next_tick, torque_of(self.mailbox))
            self.next_tick += self.tick

    def finish(self, t_end):
        self._run_to(t_end)


def all_models(tick=0.010):
    """One of each, plus a spread of control-tick phases.

    The tick phase matters: a mailbox model read at a moment that happens to
    fall just after the VCM's frame behaves quite differently from one read
    just before it, and we have no way to choose the phase. So sweep it.
    """
    out = [PerFrame(), CounterValidating(), CounterValidating(tolerant=True)]
    for ph in (0.0, 0.25, 0.5, 0.75):
        out.append(LastWins(tick=tick, phase=ph * tick))
        out.append(CounterValidatingLastWins(tick=tick, phase=ph * tick))
    return out


# -- scoring ---------------------------------------------------------------

def score(model, t_start, t_end, threshold=50):
    """How well was the generator torque command inhibited?

    `threshold` is in raw counts. The observed first crank command was +328
    counts, so 50 is comfortably below anything meaningful.

    Returns the fraction of the window during which the effective command was
    within +/- threshold of zero, and -- the number that actually matters for
    stopping an engine start -- the longest unbroken stretch during which it
    was not. Cranking is not an instant: a few hundred microseconds of
    commanded torque leaking through does nothing, a few hundred milliseconds
    starts the engine.
    """
    tl = [x for x in model.timeline if x[0] <= t_end]
    if not tl:
        return {"model": model.name, "zero_fraction": 1.0,
                "longest_nonzero_s": 0.0, "peak_abs": 0, "n_changes": 0}

    zero_time = 0.0
    longest = 0.0
    run = 0.0
    peak = 0
    integral = 0.0
    prev_t = max(t_start, tl[0][0])
    prev_v = tl[0][1]
    for t, v in tl[1:] + [(t_end, tl[-1][1])]:
        if t <= t_start:
            prev_t, prev_v = t, v
            continue
        dur = t - prev_t
        if dur > 0:
            integral += abs(prev_v) * dur
            if abs(prev_v) <= threshold:
                zero_time += dur
                if run > longest:
                    longest = run
                run = 0.0
            else:
                run += dur
                if abs(prev_v) > peak:
                    peak = abs(prev_v)
        prev_t, prev_v = t, v
    if run > longest:
        longest = run

    span = t_end - t_start
    return {
        "model": model.name,
        "zero_fraction": zero_time / span if span > 0 else 1.0,
        "longest_nonzero_s": longest,
        "peak_abs": peak,
        # Time-averaged magnitude of the command the GENE MCU was acting on.
        # This is the one that decides whether an engine turns over: a torque
        # command chopped to a few per cent duty cycle at 100 Hz does not crank
        # anything, however high its peaks are.
        "mean_abs": integral / span if span > 0 else 0.0,
        "n_changes": len(tl),
    }
