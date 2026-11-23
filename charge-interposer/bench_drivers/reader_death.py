"""A receive thread that dies is not a product result (the charge-interposer
reviewer's PCAN ruling 1, 2026-11-19): this harness's verdict on a bench
reader, over can-tools' canbench.reader_guard.

python-can's PcanBus.recv raises on most PCAN statuses (4.6.1: anything but
OK, an empty queue, BUSLIGHT, BUSHEAVY and ILLDATA; error passive, bus-off,
OVERRUN and QOVERRUN among them). A reader thread with no guard then ends
with a traceback on stderr, and its check went on to judge the short row
list it had read before, as if the board had sent nothing more. Every
bench reader that judges or counts a PCAN's rows runs its loop under
canbench.reader_guard (can-tools f19d04b; until 2026-11-23 this module's
own guard() and screen(), carried into canbench under the shared-first
rule): the first exception is recorded, with its type, text and host time,
and ends the loop. The check makes the boot or phase whose window the
reader covered VOID with that text, never judged on the rows (a FAIL from a
boot or phase whose reader lived still stands).

What stays here (the reviewer's reader_guard ruling, 2026-11-23: DIED and
the VOID lines stay ours):

    rg()          the pinned canbench.reader_guard, loaded once by file
                  (can_tools_pin.use)
    Death(name)   a reader_guard.ReaderRecord with this harness's verdict
                  on it: .lost (the first exception was MissedFrames) and
                  .void_line(tag), the VOID line its check prints
    DIED          the verdict of a boot or phase whose reader died: neither
                  True (PASS) nor False (FAIL), and distinct from None,
                  which some drivers already use for a VOID whose premise
                  was not met
    undrained(src)
                  each canbench reader call in a driver's source that does
                  not pass drain=False (the ruling: every reader passes it
                  explicitly, since no reader drained before and the
                  library's read_loop drains by default)
    RaisingBus, report_rows(), report_bus()
                  the drivers' self-test stand-ins (not in canbench)

    death = reader_death.Death("pcan")
    RG = reader_death.rg()
    RG.read_loop(bus, death, stop_event, on_row, read_s=0.05, drain=False)
    if death.error: print(death.void_line(tag))      # in the check

STATE ROWS AND MISSED FRAMES (the reviewer's PCAN item 2, 2026-11-20). The
PCAN readers open the PCAN with bus_states="report" (adapter_roles.
pcanfd_report): bus states and overruns come up as error frames in the
stream and are never raised. reader_guard.screen, which read_loop runs on
every read:
  - keeps a frame, or an error frame that counts as a bus error by can-tools'
    classifier (bench_layer.bus_error), as a row;
  - drops any other error frame (a controller-state report: error warning,
    error passive, back to active) as a state row, counted in the record's
    states;
  - raises MissedFrames on an error frame saying the reader missed frames
    (reader_guard.missed: an overrun or queue overrun; or a bus-off, or the
    restart after it, during which the reader read nothing), which the loop
    records like any raise. The reader stops, and its window is VOID with a
    line saying the reader lost frames. Its text reads "MissedFrames: ...
    reports <why>" (it read "LostFrames: ..." before 2026-11-23). Bus-off
    stays VOID, and can-tools' canbench_busoff_reset stays on so the reader
    recovers (the reviewer's ruling, 2026-11-20).
reader_guard.screen reads msg.is_error_frame directly and classifies the
bus it is given, not a wrapper's .inner: a stand-in message carries
is_error_frame, and bench_topology's drains, which read a WatchedBus, keep
their own screen step.

    python3.13 -B reader_death.py --self-test | --can-fail
"""
import ast
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.path.dirname(HERE) not in sys.path:
    sys.path.append(os.path.dirname(HERE))                 # adapter_roles, can_tools_pin

DIED = "reader died"
# canbench.reader_guard's readers, each of which takes drain= (read_loop's
# default drains)
READERS = ("read_loop", "GuardedReader", "ForkedReader")

_RG = None
_DEATH = None


def rg():
    """The pinned canbench.reader_guard (can_tools_pin.use: canbench loaded
    by file from the pinned commit's folder), loaded once per process."""
    global _RG
    if _RG is None:
        import can_tools_pin
        can_tools_pin.use()
        from canbench import reader_guard
        _RG = reader_guard
    return _RG


def Death(name):
    """A canbench reader_guard.ReaderRecord for the reader `name` (error,
    exc_type, t, missed, states, ...), with this harness's verdict on it:
    lost is True when the first exception was MissedFrames, and
    void_line(tag) is the VOID line for the boot or phase it covered."""
    global _DEATH
    if _DEATH is None:
        RG = rg()

        class _Death(RG.ReaderRecord):
            @property
            def lost(self):
                return self.exc_type == RG.MissedFrames.__name__

            def void_line(self, tag=None):
                """The VOID line for the boot or phase this reader covered."""
                head = "VOID%s: the %s reader" % ((" " + tag) if tag else "", self.name)
                at = (" at host %.3f (%s); its rows are not judged: "
                      % (self.t or 0.0, self.error))
                if self.lost:
                    return (head + " lost frames" + at
                            + "a window a reader missed frames in is not a product result")
                return head + " died" + at + "a reader that dies is not a product result"
        _DEATH = _Death
    return _DEATH(name)


def undrained(src):
    """-> ["line N: <reader>"] for each call of a canbench reader (READERS)
    in src that does not pass drain=False as a keyword (the reviewer's
    reader_guard ruling, 2026-11-23). A driver's self-test runs it on its
    own source."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name not in READERS:
            continue
        if not any(k.arg == "drain" and isinstance(k.value, ast.Constant)
                   and k.value.value is False for k in node.keywords):
            bad.append("line %d: %s" % (node.lineno, name))
    return bad


def drain_check(path, check):
    """For a driver's self-test: check() that the source at path calls a
    canbench reader and that every such call passes drain=False."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    bad = undrained(src)
    n = sum(src.count(r + "(") for r in READERS)
    check(n >= 1 and not bad,
          "every canbench reader in %s passes drain=False (no reader drains): "
          "%d call(s), without it: %s" % (os.path.basename(path), n, bad))


# --- self-test stand-ins: no hardware -----------------------------------------

def report_rows():
    """For the self-tests: the rows can-tools' CanbenchPcanBus hands up,
    made by can-tools itself (pinned canbench.errors): "passive" and
    "warning" (PCAN-Basic statuses as controller-state frames: state rows),
    "overrun" and "qoverrun" (lost frames), "busoff" and "restarted" (the
    controller off the bus, and the row CanbenchPcanBus hands up when it
    has reset it: missed frames), "ack" (a bus error), and "frame" (a data
    frame, 0x7F4)."""
    import can
    import adapter_roles
    E = adapter_roles.errors()
    return {"passive": E.from_pcan_status(E.PCAN_ERROR_BUSPASSIVE),
            "warning": E.from_pcan_status(E.PCAN_ERROR_BUSHEAVY),
            "busoff": E.from_pcan_status(E.PCAN_ERROR_BUSOFF),
            "restarted": E.error_frame(E.CAN_ERR_RESTARTED, b""),
            "overrun": E.from_pcan_status(E.PCAN_ERROR_OVERRUN),
            "qoverrun": E.from_pcan_status(E.PCAN_ERROR_QOVERRUN),
            "ack": E.error_frame(E.CAN_ERR_ACK, bytes(8)),
            "frame": can.Message(arbitration_id=0x7F4, data=bytes(8), is_extended_id=False)}


def report_bus(frames, then=None, delay=0.0):
    """For the self-tests: a stand-in for the PCAN opened with
    bus_states="report" (adapter_roles.pcanfd_report). It is a can-tools
    CanbenchPcanBus never opened (its class named by string, so bench_scan
    does not take it for an open), so the classifier reads it as that bus.
    recv() returns each of frames in turn (after delay), then raises then if
    given, else returns None."""
    import can_tools_pin
    can_tools_pin.use()                                 # the pinned canbench, by file
    from canbench.live import pcan_bus
    b = object.__new__(getattr(pcan_bus, "Canbench" "PcanBus"))
    src = list(frames)

    def recv(timeout=None):
        if delay:
            time.sleep(delay)
        if src:
            return src.pop(0)
        if then is not None:
            raise then
        time.sleep(min(timeout or 0, 0.01))
        return None
    b.recv, b.send, b.shutdown = recv, (lambda *a, **k: None), (lambda: None)
    b._is_shutdown = True                               # nothing for BusABC.__del__ to close
    b.left = src
    return b


class RaisingBus(object):
    """A stand-in bus: recv() returns each of frames in turn, then raises
    exc (a PCAN status, as python-can raises it) on every later call. Used
    by the drivers' self-tests too. A frame it hands to reader_guard.screen
    must carry is_error_frame, which screen reads directly."""

    def __init__(self, frames, exc=None, delay=0.0):
        self.frames = list(frames)
        self.exc = exc or RuntimeError("A PCAN status 0x40000 BUSPASSIVE")
        self.delay = delay
        self.calls = 0
        self.shut = False

    def recv(self, timeout=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.frames:
            return self.frames.pop(0)
        raise self.exc

    def send(self, msg, timeout=None):
        pass

    def shutdown(self):
        self.shut = True


# --- self-test: no hardware ----------------------------------------------------

def screen_checks(check):
    """reader_guard.screen with a Death, over the pinned canbench, on the
    rows can-tools' CanbenchPcanBus makes from PCAN-Basic's statuses
    (errors.from_pcan_status) and a bus error (errors.error_frame): every
    verdict as this module's own screen gave it before 2026-11-23."""
    RG = rg()
    R = report_rows()
    bus = report_bus([])
    frame, passive, warning, ack = R["frame"], R["passive"], R["warning"], R["ack"]
    d = Death("pcan")
    check(RG.screen(bus, frame, d) is frame and RG.screen(bus, None, d) is None,
          "screen: a frame is kept, nothing read is nothing")
    check(RG.screen(bus, ack, d) is ack, "screen: a bus error (ACK) is kept as an error row")
    check(RG.screen(bus, passive, d) is None and RG.screen(bus, warning, d) is None
          and d.states == 2,
          "screen: error passive and error warning are state rows, not error rows, "
          "counted in the Death: %d" % d.states)
    # frames lost, bus-off and the restart after it each raise, saying which
    # (the reviewer's ruling, 2026-11-20: bus-off VOIDs like an overrun)
    for name, says in (("overrun", "reports frames lost"), ("qoverrun", "reports frames lost"),
                       ("busoff", "reports bus-off"),
                       ("restarted", "reports a restart after bus-off")):
        try:
            RG.screen(bus, R[name], d)
            check(False, "screen: a %s row raises MissedFrames" % name)
        except RG.MissedFrames as e:
            check(says in str(e), "screen: a %s row raises MissedFrames, '%s': %s"
                  % (name, says, e))
    check(d.states == 2, "screen: no missed-frames row counted as a state row: %d" % d.states)
    # off canbench's PCAN bus (a stand-in that is no SocketCAN bus either)
    # every error frame is a bus error, as can-tools classifies it
    d0 = Death("x")
    check(RG.screen(RaisingBus([]), passive, d0) is passive and d0.states == 0,
          "screen: off CanbenchPcanBus and SocketCAN, a state frame is an error row")
    # in a reader's loop: the overrun ends it, the frame after is not read,
    # the window's VOID line says frames were lost
    src = [frame, passive, ack, R["qoverrun"], frame]
    rows, d2, stop = [], Death("pcan witness"), threading.Event()

    def step():
        if not src:
            stop.set()
            return
        m = RG.screen(bus, src.pop(0), d2)
        if m is not None:
            rows.append(m)
    RG.guard(d2, stop, step)
    ln = d2.void_line("noack_01")
    check(rows == [frame, ack] and len(src) == 1 and d2.states == 1,
          "screen in guard: rows are the frame and the bus error, the state row "
          "counted, the reader stopped at the overrun (%d rows, %d left)"
          % (len(rows), len(src)))
    check(d2.lost and d2.error.startswith("MissedFrames:"),
          "the loss is recorded as MissedFrames: %r" % d2.error)
    check(ln.startswith("VOID noack_01: the pcan witness reader lost frames at host ")
          and "reports frames lost" in ln and "FAIL" not in ln
          and "a window a reader missed frames in is not a product result" in ln,
          "its VOID line says the reader lost frames: %s" % ln)
    # a bus-off in read_loop: the reader stops there, its window VOID
    b4 = report_bus([frame, R["busoff"], R["restarted"], frame])
    rows4, d4 = [], Death("pcan witness")
    RG.read_loop(b4, d4, threading.Event(), rows4.append, read_s=0.0, drain=False)
    ln4 = d4.void_line("noack_02")
    check(rows4 == [frame] and len(b4.left) == 2 and d4.lost
          and ln4.startswith("VOID noack_02: the pcan witness reader lost frames at host ")
          and "reports bus-off" in ln4,
          "read_loop: a bus-off stops the reader, its window VOID: %s" % ln4)
    d3 = Death("pcan")
    d3.record(RuntimeError("x"))
    check(not d3.lost and "reader died" in d3.void_line(), "a plain raise still reads 'died'")
    # drain=False: the reader stops at the first read after stop, frames
    # queued then are left (no reader drained before); with the library's
    # default (drain) the same loop reads them, so the option is what decides
    for drain in (False, True):
        b5, got5, st5 = report_bus([frame, frame, frame]), [], threading.Event()

        def on_row(m, _got=got5, _st=st5):
            _got.append(m)
            _st.set()
        if drain:
            RG.read_loop(b5, Death("d"), st5, on_row, read_s=0.0)
        else:
            RG.read_loop(b5, Death("d"), st5, on_row, read_s=0.0, drain=False)
        want = (3, 0) if drain else (1, 2)
        check((len(got5), len(b5.left)) == want,
              "read_loop %s: %d read, %d left at stop (want %s)"
              % ("with the library's drain" if drain else "drain=False",
                 len(got5), len(b5.left), want))
    # report_bus, the drivers' stand-in: its frames, then nothing or a raise
    b = report_bus([frame])
    check(b.recv(0.0) is frame and b.recv(0.0) is None and b.left == [],
          "report_bus returns its frames, then nothing")
    try:
        report_bus([], then=RuntimeError("gone")).recv(0.0)
        check(False, "report_bus raises its then")
    except RuntimeError:
        check(True, "report_bus raises its then once its frames are read")


def undrained_checks(check):
    """undrained() on planted sources: drain=False passes; none, drain=True
    or drain given by a name each fail, for each of the three readers."""
    ok = ("RG.read_loop(bus, d, stop, f, read_s=0.05, drain=False)\n"
          "x = RG.GuardedReader('n', bus, drain=False)\n"
          "read_loop(bus, d, stop, f, drain=False)\n")
    check(undrained(ok) == [], "undrained: drain=False on each reader passes")
    for planted in ("RG.read_loop(bus, d, stop, f, read_s=0.05)\n",
                    "RG.read_loop(bus, d, stop, f, drain=True)\n",
                    "DRAIN = False\nRG.read_loop(bus, d, stop, f, drain=DRAIN)\n",
                    "x = RG.GuardedReader('n', bus)\n",
                    "x = RG.ForkedReader('n', bus, drain=True)\n",
                    "read_loop(bus, d, stop, f)\n"):
        check(len(undrained(planted)) == 1,
              "undrained: caught %r -> %s" % (planted.strip(), undrained(planted)))
    check(undrained("RG.guard(d, stop, step)\nRG.screen(bus, m, d)\n") == [],
          "undrained: guard and screen take no drain")


def self_test():
    fails = []

    def check(ok, what):
        print("  %s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    RG = rg()

    class Boom(Exception):
        pass

    # a loop that raises on its third step: recorded, and the loop ends
    d, stop, n = Death("pcan"), threading.Event(), [0]

    def step():
        n[0] += 1
        if n[0] == 3:
            raise Boom("A PCAN status 0x40000 BUSPASSIVE")
    t0 = time.time()
    th = threading.Thread(target=RG.guard, args=(d, stop, step), daemon=True)
    th.start()
    th.join(2.0)
    check(isinstance(d, RG.ReaderRecord), "a Death is canbench's ReaderRecord")
    check(not th.is_alive(), "a raise ends the reader's loop")
    check(n[0] == 3, "... at the step that raised, not later: %d steps" % n[0])
    check(d.error == "Boom: A PCAN status 0x40000 BUSPASSIVE",
          "the exception is recorded with its type and text: %r" % d.error)
    check(d.t is not None and t0 <= d.t <= time.time(),
          "... with its host time")
    ln = d.void_line("noack_01")
    check(ln.startswith("VOID noack_01: the pcan reader died at host %.3f " % d.t)
          and "(Boom: A PCAN status 0x40000 BUSPASSIVE)" in ln
          and "not judged" in ln and "a reader that dies is not a product result" in ln
          and "FAIL" not in ln,
          "its VOID line names the boot, the reader, its host time and the error, "
          "and no FAIL: %s" % ln)
    check(d.void_line().startswith("VOID: the pcan reader died"),
          "with no tag the line reads 'VOID: ...' (switch_pending_judge's "
          "form)")
    check(not d.lost, "a plain raise is not a loss")
    # only the first error is kept
    d.record(ValueError("later"))
    check(d.error.startswith("Boom:"), "a later error does not replace the first")

    # a loop that never raises: nothing recorded; stop ends it
    d2, stop2 = Death("kvaser"), threading.Event()
    th2 = threading.Thread(target=RG.guard, args=(d2, stop2, lambda: time.sleep(0.01)),
                           daemon=True)
    th2.start()
    time.sleep(0.1)
    check(th2.is_alive() and d2.error is None,
          "a reader that does not raise keeps reading, nothing recorded")
    stop2.set()
    th2.join(2.0)
    check(not th2.is_alive() and d2.error is None, "the stop event ends it")
    check(DIED not in (True, False, None), "DIED is not PASS, FAIL or None")
    check(rg() is RG, "rg() loads canbench.reader_guard once")

    # the stand-in bus the drivers' self-tests use
    b = RaisingBus(["a", "b"])
    got = [b.recv(), b.recv()]
    try:
        b.recv()
        check(False, "RaisingBus raises after its frames")
    except RuntimeError as e:
        check(got == ["a", "b"] and "BUSPASSIVE" in str(e),
              "RaisingBus returns its frames, then raises")
    screen_checks(check)
    undrained_checks(check)
    print("\nreader_death self-test: %s" % ("all checks OK" if not fails
                                           else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail.
# Until 2026-11-23 it also mutated this module's guard, screen, text_of,
# Death.record and first_dead; the first four are canbench.reader_guard's
# now (can-tools' own tests), still driven by screen_checks and self_test
# through it, and first_dead's only user was bench_rate_feasibility
MUTANTS = [
    ("the line without the error",
     "                      % (self.t or 0.0, self.error))",
     "                      % (self.t or 0.0, \"\"))"),
    ("the line without its host time",
     "                      % (self.t or 0.0, self.error))",
     "                      % (0.0, self.error))"),
    ("the line a FAIL",
     "                head = \"VOID%s: the %s reader\"",
     "                head = \"FAIL%s: the %s reader\""),
    ("the lost-frames line a FAIL",
     "                    return (head + \" lost frames\" + at",
     "                    return (head.replace(\"VOID\", \"FAIL\") + \" lost frames\" + at"),
    ("a loss not marked",
     "                return self.exc_type == RG.MissedFrames.__name__",
     "                return False"),
    ("a loss reads as a death", "                if self.lost:\n",
     "                if False:\n"),
    ("a plain raise reads as a loss",
     "                return self.exc_type == RG.MissedFrames.__name__",
     "                return self.error is not None"),
    ("Death not canbench's record", "        class _Death(RG.ReaderRecord):",
     "        class _Death(object):\n            name = error = t = exc_type = None"),
    ("undrained: a missing drain passes",
     "        if not any(k.arg == \"drain\" and isinstance(k.value, ast.Constant)",
     "        if node.keywords and not any(k.arg == \"drain\" and isinstance(k.value, ast.Constant)"),
    ("undrained: drain=True passes",
     "                   and k.value.value is False for k in node.keywords):",
     "                   for k in node.keywords):"),
    ("undrained: GuardedReader not looked at",
     "READERS = (\"read_loop\", \"GuardedReader\", \"ForkedReader\")",
     "READERS = (\"read_loop\", \"ForkedReader\")"),
    ("report_bus never raises its then",
     "        if then is not None:\n            raise then",
     "        if False:\n            raise then"),
]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if "--can-fail" in argv:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
