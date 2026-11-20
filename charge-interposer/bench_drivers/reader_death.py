"""A receive thread that dies is not a product result (the charge-interposer
reviewer's PCAN ruling 1, 2026-11-19).

python-can's PcanBus.recv raises on most PCAN statuses (4.6.1: anything but
OK, an empty queue, BUSLIGHT, BUSHEAVY and ILLDATA; error passive, bus-off,
OVERRUN and QOVERRUN among them). A reader thread with no guard then ends
with a traceback on stderr, and its check went on to judge the short row
list it had read before, as if the board had sent nothing more. Every
bench reader that judged or counted a PCAN's rows that way now runs its
loop under guard(): the first exception is recorded in a Death, with its
type, text and host time, and ends the loop. The check makes the boot or
phase whose window the reader covered VOID with that text, never judged on
the rows (a FAIL from a boot or phase whose reader lived still stands).

    death = Death("pcan")
    guard(death, stop_event, lambda: one_recv())     # in the thread
    if death.error: print(death.void_line(tag))      # in the check

DIED is the verdict a boot or phase gets when its reader died: neither True
(PASS) nor False (FAIL), and distinct from None, which some drivers already
use for a VOID whose premise was not met, so a case can tell a dead reader
from that and exit VOID even when its other boots passed.

STATE ROWS AND LOST FRAMES (the reviewer's PCAN item 2, 2026-11-20). The
PCAN readers open the PCAN with bus_states="report" (adapter_roles.
pcanfd_report): bus states and overruns come up as error frames in the
stream and are never raised. Each such reader passes what it reads through
screen(bus, msg) in its step:
  - a frame, or an error frame that counts as a bus error by can-tools'
    classifier (adapter_roles.bus_error), is returned and kept as a row;
  - any other error frame (a controller-state report: error warning,
    error passive, back to active) is a state row, not an error row: None,
    kept out of the rows, counted in the Death's states;
  - an error frame saying the reader missed frames (adapter_roles.missed:
    can-tools' errors.lost_frames, an overrun or queue overrun; or a
    bus-off, or the restart after it, during which the reader read
    nothing) raises LostFrames, which guard() records like any raise. The
    reader stops, and its window is VOID with a line saying the reader lost
    frames: a window a reader missed frames in is not judged. Bus-off was a
    raise before report, so a dead reader; it stays VOID, and can-tools'
    canbench_busoff_reset stays on so the reader recovers (the reviewer's
    ruling, 2026-11-20).

    python3.13 -B reader_death.py --self-test | --can-fail
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.path.dirname(HERE) not in sys.path:
    sys.path.append(os.path.dirname(HERE))                 # adapter_roles

DIED = "reader died"


def text_of(exc):
    """'<Type>: <text>' for an exception."""
    return "%s: %s" % (type(exc).__name__, exc)


class LostFrames(Exception):
    """A reader read an error frame that reports frames lost (screen)."""


class Death(object):
    """What a reader thread recorded when its receive raised: error is None
    until then, then '<Type>: <text>' of the first exception, at host time
    t (time.time()); lost is True when that was LostFrames. states counts
    the state rows screen() kept out of the rows."""

    def __init__(self, name):
        self.name = name
        self.error = None
        self.t = None
        self.lost = False
        self.states = 0

    def record(self, exc):
        if self.error is None:
            self.error = text_of(exc)
            self.t = time.time()
            self.lost = isinstance(exc, LostFrames)

    def void_line(self, tag=None):
        """The VOID line for the boot or phase this reader covered."""
        if self.lost:
            return ("VOID%s: the %s reader lost frames at host %.3f (%s); its "
                    "rows are not judged: a window a reader missed frames in "
                    "is not a product result"
                    % ((" " + tag) if tag else "", self.name, self.t or 0.0,
                       self.error))
        return ("VOID%s: the %s reader died at host %.3f (%s); its rows are "
                "not judged: a reader that dies is not a product result"
                % ((" " + tag) if tag else "", self.name, self.t or 0.0,
                   self.error))


def screen(bus, msg, death=None, roles=None):
    """A message a reader read from bus -> msg to keep as a row (a frame,
    or an error frame that is a bus error), or None (nothing read, or a
    state row: counted in death.states). Raises LostFrames on an error
    frame that says the reader missed frames (adapter_roles.missed: frames
    lost, bus-off, a restart after bus-off). roles: adapter_roles, or a
    stand-in (self-tests)."""
    if msg is None or not getattr(msg, "is_error_frame", False):
        return msg
    if roles is None:
        import adapter_roles as roles
    why = roles.missed(msg)
    if why:
        raise LostFrames("error frame 0x%08X data %s reports %s%s"
                         % (msg.arbitration_id, bytes(msg.data).hex(), why,
                            (" (%s)" % msg.raw_note) if getattr(msg, "raw_note", None)
                            else ""))
    if roles.bus_error(bus, msg):
        return msg
    if death is not None:
        death.states += 1
    return None


def guard(death, stop_ev, step):
    """Run step() until stop_ev is set. The first exception it raises is
    recorded in death and ends the loop (the thread then returns)."""
    while not stop_ev.is_set():
        try:
            step()
        except Exception as exc:                           # noqa: BLE001
            death.record(exc)
            return


def first_dead(deaths):
    """The first Death in deaths that recorded an error, or None."""
    for d in deaths:
        if d is not None and d.error is not None:
            return d
    return None


# --- self-test: no hardware --------------------------------------------------

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


def screen_checks(check):
    """screen() with the real adapter_roles over the pinned canbench, on the
    rows can-tools' CanbenchPcanBus makes from PCAN-Basic's statuses
    (errors.from_pcan_status) and a bus error (errors.error_frame): the
    classifier and lost_frames are can-tools', not decoded here."""
    R = report_rows()
    bus = report_bus([])
    frame, passive, warning, ack = R["frame"], R["passive"], R["warning"], R["ack"]
    d = Death("pcan")
    check(screen(bus, frame, d) is frame and screen(bus, None, d) is None,
          "screen: a frame is kept, nothing read is nothing")
    check(screen(bus, ack, d) is ack, "screen: a bus error (ACK) is kept as an error row")
    check(screen(bus, passive, d) is None and screen(bus, warning, d) is None and d.states == 2,
          "screen: error passive and error warning are state rows, not error rows, "
          "counted: %d" % d.states)
    # frames lost, bus-off and the restart after it each raise, saying which
    # (the reviewer's ruling, 2026-11-20: bus-off VOIDs like an overrun)
    for name, says in (("overrun", "reports frames lost"), ("qoverrun", "reports frames lost"),
                       ("busoff", "reports bus-off"),
                       ("restarted", "reports a restart after bus-off")):
        try:
            screen(bus, R[name], d)
            check(False, "screen: a %s row raises LostFrames" % name)
        except LostFrames as e:
            check(says in str(e), "screen: a %s row raises LostFrames, '%s': %s"
                  % (name, says, e))
    check(d.states == 2, "screen: no missed-frames row counted as a state row: %d" % d.states)
    # off canbench's PCAN bus (a stand-in that is no SocketCAN bus either)
    # every error frame is a bus error, as can-tools classifies it
    d0 = Death("x")
    check(screen(RaisingBus([]), passive, d0) is passive and d0.states == 0,
          "screen: off CanbenchPcanBus and SocketCAN, a state frame is an error row")
    # in a reader's loop under guard: the overrun ends it, the frame after
    # is not read, the window's VOID line says frames were lost
    src = [frame, passive, ack, R["qoverrun"], frame]
    rows, d2, stop = [], Death("pcan witness"), threading.Event()

    def step():
        if not src:
            stop.set()
            return
        m = screen(bus, src.pop(0), d2)
        if m is not None:
            rows.append(m)
    guard(d2, stop, step)
    ln = d2.void_line("noack_01")
    check(rows == [frame, ack] and len(src) == 1 and d2.states == 1,
          "screen in guard: rows are the frame and the bus error, the state row "
          "counted, the reader stopped at the overrun (%d rows, %d left)"
          % (len(rows), len(src)))
    check(d2.lost and d2.error.startswith("LostFrames:"),
          "the loss is recorded as LostFrames: %r" % d2.error)
    check(ln.startswith("VOID noack_01: the pcan witness reader lost frames at host ")
          and "reports frames lost" in ln and "FAIL" not in ln,
          "its VOID line says the reader lost frames: %s" % ln)
    # a bus-off in the loop: the reader stops there, its window VOID
    src4, rows4, d4, stop4 = [frame, R["busoff"], R["restarted"], frame], [], \
        Death("pcan witness"), threading.Event()

    def step4():
        if not src4:
            stop4.set()
            return
        m = screen(bus, src4.pop(0), d4)
        if m is not None:
            rows4.append(m)
    guard(d4, stop4, step4)
    ln4 = d4.void_line("noack_02")
    check(rows4 == [frame] and len(src4) == 2 and d4.lost
          and ln4.startswith("VOID noack_02: the pcan witness reader lost frames at host ")
          and "reports bus-off" in ln4,
          "screen in guard: a bus-off stops the reader, its window VOID: %s" % ln4)
    d3 = Death("pcan")
    d3.record(RuntimeError("x"))
    check(not d3.lost and "died" in d3.void_line(), "a plain raise still reads 'died'")
    # report_bus, the drivers' stand-in: its frames, then nothing or a raise
    b = report_bus([frame])
    check(b.recv(0.0) is frame and b.recv(0.0) is None and b.left == [],
          "report_bus returns its frames, then nothing")
    try:
        report_bus([], then=RuntimeError("gone")).recv(0.0)
        check(False, "report_bus raises its then")
    except RuntimeError:
        check(True, "report_bus raises its then once its frames are read")

class RaisingBus(object):
    """A stand-in bus: recv() returns each of frames in turn, then raises
    exc (a PCAN status, as python-can raises it) on every later call. Used
    by the drivers' self-tests too."""

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


def self_test():
    fails = []

    def check(ok, what):
        print("  %s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    class Boom(Exception):
        pass

    # a loop that raises on its third step: recorded, and the loop ends
    d, stop, n = Death("pcan"), threading.Event(), [0]

    def step():
        n[0] += 1
        if n[0] == 3:
            raise Boom("A PCAN status 0x40000 BUSPASSIVE")
    t0 = time.time()
    th = threading.Thread(target=guard, args=(d, stop, step), daemon=True)
    th.start()
    th.join(2.0)
    check(not th.is_alive(), "a raise ends the reader's loop")
    check(n[0] == 3, "... at the step that raised, not later: %d steps" % n[0])
    check(d.error == "Boom: A PCAN status 0x40000 BUSPASSIVE",
          "the exception is recorded with its type and text: %r" % d.error)
    check(d.t is not None and t0 <= d.t <= time.time(),
          "... with its host time")
    ln = d.void_line("noack_01")
    check(ln.startswith("VOID noack_01: the pcan reader died at host ")
          and "Boom: A PCAN status 0x40000 BUSPASSIVE" in ln
          and "not judged" in ln and "FAIL" not in ln,
          "its VOID line names the boot, the reader and the error, and no "
          "FAIL: %s" % ln)
    check(d.void_line().startswith("VOID: the pcan reader died"),
          "with no tag the line reads 'VOID: ...' (switch_pending_judge's "
          "form)")
    # only the first error is kept
    d.record(ValueError("later"))
    check(d.error.startswith("Boom:"), "a later error does not replace the first")

    # a loop that never raises: nothing recorded; stop ends it
    d2, stop2 = Death("kvaser"), threading.Event()
    th2 = threading.Thread(target=guard, args=(d2, stop2, lambda: time.sleep(0.01)),
                           daemon=True)
    th2.start()
    time.sleep(0.1)
    check(th2.is_alive() and d2.error is None,
          "a reader that does not raise keeps reading, nothing recorded")
    stop2.set()
    th2.join(2.0)
    check(not th2.is_alive() and d2.error is None, "the stop event ends it")
    check(first_dead([None, d2, d]) is d and first_dead([d2, None]) is None,
          "first_dead finds the reader that died, None when none did")
    check(DIED not in (True, False, None), "DIED is not PASS, FAIL or None")

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
    print("\nreader_death self-test: %s" % ("all checks OK" if not fails
                                           else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the exception swallowed, not recorded",
     "            death.record(exc)\n            return",
     "            return"),
    ("the loop goes on after a raise",
     "            death.record(exc)\n            return",
     "            death.record(exc)"),
    ("the text without its type",
     "    return \"%s: %s\" % (type(exc).__name__, exc)",
     "    return str(exc)"),
    ("a later error replaces the first",
     "        if self.error is None:\n            self.error",
     "        if True:\n            self.error"),
    ("no host time", "            self.t = time.time()", "            self.t = None"),
    ("the line without the error",
     "\n                   self.error))", "\n                   \"\"))"),
    ("the line a FAIL",
     "        return (\"VOID%s: the %s reader died", "        return (\"FAIL%s: the %s reader died"),
    ("the lost-frames line a FAIL",
     "            return (\"VOID%s: the %s reader lost", "            return (\"FAIL%s: the %s reader lost"),
    ("first_dead takes a live reader",
     "        if d is not None and d.error is not None:",
     "        if d is not None:"),
    ("a lost-frames row not raised", "    why = roles.missed(msg)\n    if why:\n",
     "    why = roles.missed(msg)\n    if False:\n"),
    ("only an overrun raised (bus-off a state row)", "    why = roles.missed(msg)\n",
     "    why = roles.missed(msg) == \"frames lost\" and \"frames lost\"\n"),
    ("the raise does not say what was missed",
     "% (msg.arbitration_id, bytes(msg.data).hex(), why,",
     "% (msg.arbitration_id, bytes(msg.data).hex(), \"frames lost\","),
    ("state rows kept as error rows", "        death.states += 1\n    return None",
     "        death.states += 1\n    return msg"),
    ("bus errors dropped", "    if roles.bus_error(bus, msg):\n        return msg",
     "    if roles.bus_error(bus, msg):\n        return None"),
    ("state rows not counted", "        death.states += 1\n", "        pass\n"),
    ("a loss not marked", "            self.lost = isinstance(exc, LostFrames)",
     "            self.lost = False"),
    ("a loss reads as a death", "        if self.lost:\n            return (",
     "        if False:\n            return ("),
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
