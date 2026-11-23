"""Frames offered at a stated rate for a stated time: every bench driver that
offers frames at a rate (bench_hold12, bench_switch_loss,
bench_innomaker_clock, bench_charger_witness) calls offer() here.

Since 2026-11-23 (the charge-interposer reviewer's pace ruling, can-tools
f19d04b) the schedule is can-tools' canbench.replay.pace, loaded by file
from the pinned commit (can_tools_pin.use). It replaces bench_pacer.py
(2026-10-05 to 2026-11-23), whose schedule pace carries under the
shared-first rule: the schedule is absolute and never reset, so a late
frame goes at once and the time lost is owed (bench_pacer's reason, in
short: a loop that reset its schedule on every late pass forgave every
overslept sleep and reported 1,869 fps as the PCAN's ceiling, a figure a
no-op send reproduced); pace's "hybrid" wait sleeps to 2 ms before a due
time and then spins, as bench_pacer's did (to 1.5 ms). This module keeps
what pace leaves to its caller: the end, the counts and what the callers
read.

THE END IS BY TIME, AS BENCH_PACER'S WAS. offer(send, rate, seconds) ends
at t0 + seconds: no pass (a group of `burst` frames) begins at or after it,
and the offer returns at the first pass due at or after it. pace's own
until_s is by schedule offset, so on its own a sender running late would
send every frame of the schedule and end later, moving bench_hold12's
judged window end (t1), bench_innomaker_clock rate-runs' count against
0.95 x rate x seconds and bench_switch_loss's cycle stops. So (the ruling)
a stop Event, which pace checks before each pass, is set by a timer at
t0 + seconds. Two things the timer alone would not keep, kept here:
  - pace checks its stop BEFORE it waits for a pass's due time, so a pass
    due after the end would still be sent; and the timer's thread can run
    late while pace spins holding the GIL. So the pass schedule ends when
    it is asked for a pass at or after the end (a late sender: no pass
    starts past the end, and pace records no lag or catch-up for one), and
    a pass that pace calls at or after the end (one due then, which it
    waited for) is not sent. The stop is set in both. With these, the
    timer's stop never ends an offer that would otherwise go on; it is
    the ruling's cut, kept so pace's own stop is set at the end.
  - pace counts sends that return or raise one by one; a pass here is one
    pace item of `burst` frames, so a burst is never cut part way (as
    bench_pacer's never was), and the frames' counts are kept here: sent,
    errs and the first error's repr, as bench_pacer kept them.

WHAT AN OFFER RETURNS (Offered), and where each figure comes from:
    sent, errs, first_err  this module's per-frame counts (a frame whose
                           send raised an Exception is counted in errs; the
                           first one's repr kept). Anything else raised, or
                           raised by seq_writer, propagates (pace's catch
                           is empty), as from bench_pacer.
    elapsed                pace's elapsed_s, from t0 to the end
    queued_fps             sent / elapsed: send() RETURNS, a queue-accept
                           rate and not a wire rate (bench_pacer's warning
                           stands: a witness says what the wire carried)
    max_lag_ms             pace's max_lag_s in ms (a pass's due time to its
                           call), 0.0 when nothing was sent, as
                           bench_pacer's began at 0
    catchup                pace's count: passes already due when the
                           previous pass returned (bench_pacer counted the
                           frames of each pass that began after its due
                           time, which, as it read the clock after its own
                           wait, was nearly every pass)
    sender_gaps_ms         measured here: from t0 to the end of the first
                           pass, then between the ends of consecutive
                           passes (bench_pacer's gaps, which pace does not
                           give; bench_hold12 records them, judged by no
                           check)
    ended                  pace's ended ("end": the time bound; "stop": the
                           timer's stop seen first)

seq_writer, if given, is called with the 0-based frame index (sent + errs
so far) before each send, so the caller stamps a sequence number into its
own payload. A caller's msg.data stays a bytearray: gs_usb's backend calls
.extend() on it (bench_pacer's note, 2026-10-05).

    python3.13 -B bench_offer.py --self-test | --can-fail
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.path.dirname(HERE) not in sys.path:
    sys.path.append(os.path.dirname(HERE))                 # can_tools_pin

# pace's "hybrid" wait: sleep to this long before a due time, then spin
SPIN_S = 0.002

_RP = None


def rp():
    """The pinned canbench.replay (can_tools_pin.use: canbench loaded by
    file from the pinned commit's folder), loaded once per process, at the
    first offer."""
    global _RP
    if _RP is None:
        import can_tools_pin
        can_tools_pin.use()
        from canbench import replay
        _RP = replay
    return _RP


class Offered(object):
    """What one offer did (the module's docstring says where each figure
    comes from). `pace` is canbench's PaceResult itself."""

    def __init__(self, res, sent, errs, first_err, gaps, rate, burst, seconds):
        self.pace = res
        self.sent = sent
        self.errs = errs
        self.first_err = first_err
        self.elapsed = res.elapsed_s
        self.queued_fps = sent / self.elapsed if self.elapsed else 0.0
        self.max_lag_ms = (res.max_lag_s or 0.0) * 1000.0
        self.catchup = res.catchup
        self.sender_gaps_ms = [g * 1000.0 for g in gaps]
        self.ended = res.ended
        self.rate = rate
        self.burst = burst
        self.seconds = seconds

    def __str__(self):
        return ("%d sent, %d send error(s), %.0f fps queued over %.3f s "
                "(%.0f fps in bursts of %d for %.1f s asked), max lag %.2f ms, "
                "catch-up %d, ended %s%s"
                % (self.sent, self.errs, self.queued_fps, self.elapsed,
                   self.rate, self.burst, self.seconds, self.max_lag_ms,
                   self.catchup, self.ended,
                   "; first error %s" % self.first_err if self.first_err else ""))


def offer(send, rate, seconds, burst=1, seq_writer=None,
          clock=time.perf_counter, sleep=time.sleep, timer=threading.Timer):
    """Call send() at `rate` frames/s mean, in groups of `burst`, for
    `seconds` (by time: see the module's docstring). `send` takes no
    arguments and raises on failure. Returns an Offered. clock, sleep and
    timer are for the self-test."""
    # canbench loaded before t0: the first load in a process (the pin's
    # check and the import) takes about 0.2 s, which, inside the offer's
    # time, would be a first window with no frame in it
    RP = rp()
    interval = float(burst) / rate
    stop = threading.Event()
    t0 = clock()
    end = t0 + seconds
    st = {"sent": 0, "errs": 0, "first_err": None, "prev": t0}
    gaps = []

    def passes():
        k = 0
        while True:
            # asked for a pass at or after the end (a sender running late):
            # the schedule ends, so no pass starts past it
            if clock() >= end:
                stop.set()
                return
            yield k * interval, k
            k += 1

    def one_pass(_k):
        # a pass pace waited for and calls at or after the end: not sent
        if clock() >= end:
            stop.set()
            return
        for _ in range(burst):
            if seq_writer is not None:
                seq_writer(st["sent"] + st["errs"])
            try:
                send()
                st["sent"] += 1
            except Exception as exc:                       # noqa: BLE001
                st["errs"] += 1
                if st["first_err"] is None:
                    st["first_err"] = repr(exc)
        t = clock()
        gaps.append(t - st["prev"])
        st["prev"] = t

    tm = timer(max(0.0, end - clock()), stop.set)
    tm.daemon = True
    tm.start()
    try:
        res = RP.pace(passes(), one_pass, t0=t0, wait="hybrid", spin_s=SPIN_S,
                        stop=stop, catch=(), clock=clock, sleep=sleep)
    finally:
        tm.cancel()
    return Offered(res, st["sent"], st["errs"], st["first_err"], gaps, rate,
                   burst, seconds)


# --- self-test ---------------------------------------------------------------

class FakeClock(object):
    """A clock that moves on each read by `tick` and on each sleep by its
    length; `cost(s)` is a send that takes s of it."""

    def __init__(self, tick=1e-7):
        self.now = 100.0
        self.tick = tick

    def __call__(self):
        self.now += self.tick
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)

    def cost(self, s, fail_at=(), sent=None):
        n = [0]

        def send():
            n[0] += 1
            self.now += s
            if sent is not None:
                sent.append(n[0])
            if n[0] in fail_at:
                raise IOError("send %d refused" % n[0])
        return send


class FakeTimer(object):
    """threading.Timer's shape, recording what offer() does with it; with
    fire=True it runs its function at start."""
    made = []

    def __init__(self, interval, fn, fire=False):
        self.interval, self.fn, self.fire = interval, fn, fire
        self.daemon = False
        self.started = self.cancelled = False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True
        if self.fire:
            self.fn()

    def cancel(self):
        self.cancelled = True


def self_test():
    fails = []

    def check(ok, what):
        print("  %s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    def run(rate, seconds, send_cost=0.0, burst=1, seq=None, fail_at=(),
            fire=False, seq_writer=None):
        c = FakeClock()
        FakeTimer.made[:] = []
        o = offer(c.cost(send_cost, fail_at), rate, seconds, burst=burst,
                  seq_writer=seq_writer or (seq.append if seq is not None else None),
                  clock=c, sleep=c.sleep,
                  timer=lambda iv, fn: FakeTimer(iv, fn, fire))
        return o, FakeTimer.made[-1]

    print("the end is by time (a fake clock)")
    # on time: 1,000 fps for 10.5 ms -> passes due at 0..10 ms, 11 sent; the
    # pass due at 11 ms (after the end) is waited for and not sent
    o, tm = run(1000, 0.0105)
    check(o.sent == 11 and o.errs == 0,
          "on time: the 11 passes due before the end sent, none after (%d)" % o.sent)
    check(0.011 <= o.elapsed < 0.0112,
          "... and the offer ends at the first pass due after the end, 11 ms "
          "(%.4f s), as bench_pacer's did" % o.elapsed)
    check(o.catchup == 0 and o.max_lag_ms < 0.05,
          "... no catch-up, lag under 0.05 ms (%d, %.4f ms)" % (o.catchup, o.max_lag_ms))
    check(abs(o.queued_fps - 1000.0) < 2.0,
          "... queued_fps = sent / elapsed, 11 in 11 ms (%.1f)" % o.queued_fps)
    # late: each send takes 2 ms at 1,000 fps for 100 ms -> passes start at
    # 0, 2, .. 98 ms: 50 sent, not the schedule's 100
    o, tm = run(1000, 0.100, send_cost=0.002)
    check(o.sent == 50,
          "late (2 ms a send at 1 ms a frame): 50 sent in the 100 ms, not the "
          "schedule's 100 (%d)" % o.sent)
    check(0.100 <= o.elapsed < 0.1002,
          "... the offer ends at 100 ms, not after every scheduled frame "
          "(%.4f s)" % o.elapsed)
    check(abs(o.max_lag_ms - 49.0) < 0.2 and o.catchup == 49,
          "... max lag the last pass sent's, 49 ms, and 49 catch-ups: none "
          "counted for a pass past the end (%.3f ms, %d)" % (o.max_lag_ms, o.catchup))
    check(abs(o.queued_fps - 500.0) < 2.0,
          "... queued_fps = sent / elapsed (%.1f)" % o.queued_fps)
    print("bursts, sequence numbers, sender gaps")
    seq = []
    o, tm = run(1000, 0.010, burst=4, seq=seq)
    check(o.sent == 12 and seq == list(range(12)),
          "bursts of 4 every 4 ms for 10 ms: 3 passes, 12 frames, seq_writer "
          "given 0..11 (%d, %s)" % (o.sent, seq))
    check(len(o.sender_gaps_ms) == 3
          and all(abs(g - 4.0) < 0.05 for g in o.sender_gaps_ms[1:])
          and o.sender_gaps_ms[0] < 0.05,
          "... one sender gap a pass in ms, the first from t0 (%s)"
          % ["%.3f" % g for g in o.sender_gaps_ms])
    o, tm = run(1000, 0.0105, burst=4, send_cost=0.0003)
    check(o.sent == 12 and o.sent % 4 == 0,
          "a burst is never cut part way (%d frames)" % o.sent)
    o, tm = run(1000, 0.020, burst=4, send_cost=0.002)
    check(o.sent == 12 and o.catchup == 2,
          "late bursts (8 ms a pass every 4 ms, 20 ms): passes at 0, 8, 16 ms, "
          "catch-up pace's count of passes, 2 (%d frames, %d)" % (o.sent, o.catchup))
    print("send errors")
    seq = []
    o, tm = run(1000, 0.0055, fail_at=(3, 5), seq=seq)
    check(o.sent == 4 and o.errs == 2 and seq == list(range(6)),
          "a send that raises is counted in errs and the next frame keeps its "
          "index (%d sent, %d errs, %s)" % (o.sent, o.errs, seq))
    check(o.first_err == repr(IOError("send 3 refused")),
          "... the first error's repr kept, as bench_pacer kept it (%s)" % o.first_err)

    def bad_seq(n):
        raise RuntimeError("seq_writer broke")
    try:
        run(1000, 0.005, seq_writer=bad_seq)
        check(False, "an exception from seq_writer propagates (it did not)")
    except RuntimeError:
        check(True, "an exception from seq_writer propagates, as from bench_pacer")
    print("the timer's stop")
    o, tm = run(1000, 0.010)
    check(tm.started and tm.cancelled and tm.daemon
          and abs(tm.interval - 0.010) < 1e-5 and getattr(tm.fn, "__name__", "") == "set",
          "a daemon timer started at t0 for the offer's seconds (%.6f s) to set "
          "the stop, cancelled at the end" % tm.interval)
    o, tm = run(1000, 0.010, fire=True)
    check(o.sent == 0 and o.ended == "stop" and o.max_lag_ms == 0.0,
          "a stop the timer has set ends the offer before its next pass (%d "
          "sent, ended %s, lag %.1f)" % (o.sent, o.ended, o.max_lag_ms))
    o, tm = run(1000, 0.0)
    check(o.sent == 0 and o.max_lag_ms == 0.0 and o.queued_fps == 0.0,
          "no time: nothing sent, lag 0.0 (%d, %.1f)" % (o.sent, o.max_lag_ms))
    # canbench's first load in a process takes about 0.2 s: before t0, so the
    # first offer is not short by it (found on bench_switch_loss's self-test:
    # its first on window sent nothing)
    g = globals()
    real_rp = g["rp"]
    c = FakeClock()

    def slow_rp():
        c.sleep(0.2)
        return real_rp()
    g["rp"] = slow_rp
    try:
        o = offer(c.cost(0.0), 1000, 0.0105, clock=c, sleep=c.sleep,
                  timer=lambda iv, fn: FakeTimer(iv, fn))
    finally:
        g["rp"] = real_rp
    check(o.sent == 11, "canbench loaded before t0: a 0.2 s load costs the offer "
          "no frame (%d of 11)" % o.sent)
    print("the real clock, canbench's pace")
    n = [0]

    def noop():
        n[0] += 1
    o = offer(noop, 2000, 0.3)
    check(isinstance(o.pace, rp().PaceResult) and abs(o.sent - 600) <= 2,
          "a no-op send at 2,000 fps for 0.3 s: %d sent (600 +/- 2), canbench's "
          "PaceResult underneath" % o.sent)
    g = sorted(o.sender_gaps_ms[1:])
    med = g[len(g) // 2] if g else 0.0
    check(abs(med - 0.5) <= 0.025, "... median sender gap %.3f ms (0.5 ms +/- 5 %%)" % med)

    def slow():
        t = time.perf_counter() + 0.002
        while time.perf_counter() < t:
            pass
    o = offer(slow, 2000, 0.3)
    check(0.3 <= o.elapsed < 0.306 and o.sent < 160,
          "a 2 ms send at 2,000 fps for 0.3 s ends by 0.3 s + one send (%.4f s, "
          "%d sent of the schedule's 600)" % (o.elapsed, o.sent))
    check("sent" in str(o) and "max lag" in str(o),
          "str() names the figures: %s" % o)
    print("\nbench_offer self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the schedule asked past the end: a late pass still starts",
     "            if clock() >= end:\n                stop.set()\n                return\n"
     "            yield k * interval, k",
     "            yield k * interval, k"),
    ("a pass called at or after the end is sent",
     "        if clock() >= end:\n            stop.set()\n            return\n"
     "        for _ in range(burst):",
     "        for _ in range(burst):"),
    ("canbench loaded after t0, inside the offer's time",
     "    RP = rp()\n    interval = float(burst) / rate\n    stop = threading.Event()\n"
     "    t0 = clock()\n",
     "    interval = float(burst) / rate\n    stop = threading.Event()\n"
     "    t0 = clock()\n    RP = rp()\n"),
    ("the stop not given to pace",
     "                        stop=stop, catch=(), clock=clock, sleep=sleep)",
     "                        stop=None, catch=(), clock=clock, sleep=sleep)"),
    ("the timer for twice the time", "    tm = timer(max(0.0, end - clock()), stop.set)",
     "    tm = timer(2 * seconds, stop.set)"),
    ("the timer never started", "    tm.daemon = True\n    tm.start()\n",
     "    tm.daemon = True\n"),
    ("the timer not cancelled", "    finally:\n        tm.cancel()\n",
     "    finally:\n        pass\n"),
    ("pace counts what escapes (seq_writer's raise swallowed)",
     "stop=stop, catch=(), clock", "stop=stop, catch=(Exception,), clock"),
    ("each frame's send unguarded",
     "            try:\n                send()\n                st[\"sent\"] += 1\n"
     "            except Exception as exc:                       # noqa: BLE001\n"
     "                st[\"errs\"] += 1\n",
     "            if True:\n                send()\n                st[\"sent\"] += 1\n"
     "            else:\n                st[\"errs\"] += 1\n"),
    ("the first error's text, not its repr", "                    st[\"first_err\"] = repr(exc)",
     "                    st[\"first_err\"] = str(exc)"),
    ("seq_writer given the sent count only", "                seq_writer(st[\"sent\"] + st[\"errs\"])",
     "                seq_writer(st[\"sent\"])"),
    ("the sender gaps from t0 each time", "        st[\"prev\"] = t\n", ""),
    ("the sender gaps in seconds", "        self.sender_gaps_ms = [g * 1000.0 for g in gaps]",
     "        self.sender_gaps_ms = list(gaps)"),
    ("max lag in seconds", "        self.max_lag_ms = (res.max_lag_s or 0.0) * 1000.0",
     "        self.max_lag_ms = (res.max_lag_s or 0.0)"),
    ("max lag None when nothing was sent", "        self.max_lag_ms = (res.max_lag_s or 0.0) * 1000.0",
     "        self.max_lag_ms = res.max_lag_s and res.max_lag_s * 1000.0"),
    ("catch-up in frames", "        self.catchup = res.catchup",
     "        self.catchup = res.catchup * burst"),
    ("one frame a pass", "        for _ in range(burst):", "        for _ in range(1):"),
    ("queued_fps against the time asked", "        self.queued_fps = sent / self.elapsed if self.elapsed else 0.0",
     "        self.queued_fps = sent / seconds if seconds else 0.0"),
]


if __name__ == "__main__":
    if "--can-fail" in sys.argv[1:]:
        import script_can_fail
        sys.exit(script_can_fail.run(__file__, MUTANTS))
    if "--self-test" in sys.argv[1:]:
        sys.exit(self_test())
    print(__doc__)
    sys.exit(2)
