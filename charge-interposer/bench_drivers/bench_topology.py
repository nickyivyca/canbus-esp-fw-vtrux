"""Which interposer segment is each dongle on? Separated, not guessed.

Written 2026-10-05 for the spec 12 entry 2 arm, because that run needs a
stimulus on the VEHICLE side and filler on the CHARGER side and gets the
wrong answer silently if either is misplaced. `bench_channel_identify.py`
does this for the Ixxat/PCAN/Kvaser bench; the Innomaker has since replaced
the Ixxat, and gs_usb needs its own open path and echo draining.

THE DISCRIMINATOR, and why it is built this way. AGENTS.md: a bus
discriminator must be shown to SEPARATE, and the winning side must be
non-empty on its own evidence rather than merely last standing.

  - 0x7F4-0x7F7 are emitted by main.cpp on the VEHICLE port only. An
    adapter that sees them is on the vehicle segment. That is positive
    evidence with a threshold: all four pages, not one frame.

  - An adapter that does NOT see them is not thereby on the charger
    segment -- it might be unplugged, bus-off, or on a dead wire. So a
    probe frame is injected from a KNOWN vehicle-side adapter and must
    arrive, forwarded by the board, before that adapter is called
    charger-side. Both halves must hold.

  - A single-adapter run is refused outright. "There was only one, so
    that's the bus" is the form of this error that reads as trivially
    correct, and it is the one worth refusing.

THE CHARGER SIDE FIRST (tester, 2026-10-21, reviewer-approved after the a25e
chain run). Since spec 2.3 the vehicle port is listen-only while the charger
is silent, and "in listen-only the board sends no diagnostic frames" (spec
line 248), so on every image since then a quiet phase 1 hears nothing.
Phase 0 therefore listens quietly once; if no adapter hears the four pages,
each adapter in turn talks as the charger (CHARGER_ID, the charger status
frame: an extended frame from source address 0xC0, the frame 2.3 switches
on) while the others listen. An adapter whose charger frames bring the four
pages up on others is on the CHARGER segment, and those that hear them are
on the VEHICLE segment: positive evidence for both sides. After each talker
the port must fall back to listen-only (2 s with no charger frame) and a
quiet control window must hear no pages, or nothing can be attributed.
classify_charger_up() decides; phase 2's forwarded probe then confirms.

THE KVASER ON KVASER_USB (tester, 2026-11-13; the charge-interposer
reviewer's A, D6 and the kernel-log addendum). The adapters acknowledge, so
the Kvaser's link is brought up and read back before anything opens
(topology_link; VOID with the kernel's lines if it does not come up). It is
netdev kvaser, opened with its echoes kept (a kvaser_link.WatchedBus until
2026-11-23; since then Kvaser: the bus and a kvaser_link.EchoWatch over
can-tools' canbench.echo_watch with read_after_send=False, the reviewer's
echo_watch ruling): the sends go through the watch, which reads nothing,
and the Kvaser's drain, the one thread reading it, hands each frame to
watch.saw() first, so its own echoes are consumed by the stall rule and
never counted as frames heard, and calls watch.check() after every read,
so the 100 ms bound is judged there as WatchedBus.recv() judged it. A
kvaser TX stall, on a send or in its drain, ends the run VOID with the
kernel's lines, never a topology answer, and the Kvaser is recovered. The
sends here are slow (10 per second, 40 probes 10 ms apart), so the drains
stay threads; nothing waits out the last echoes (no finish()), as before.

THE PCAN (the reviewer's PCAN item 2, 2026-11-20): opened with bus states
reported (adapter_roles.pcanfd_report). Each drain reads under
reader_death's guard; a raise other than a kvaser TX stall, or a row
saying the reader missed frames (adapter_roles.missed: frames lost, a
bus-off, a restart after bus-off; the reviewer's ruling on bus-off,
2026-11-20), ends it, and the run is
VOID with its line (void_in), never a topology answer. Since 2026-11-23
(the reviewer's reader_guard ruling) the guard is can-tools'
canbench.reader_guard.guard and a missed-frames row raises its
MissedFrames (the VOID line's text reads "MissedFrames: ...", it read
"LostFrames: ..."); the screen stays the drain's own step, since
reader_guard.screen does not classify a WatchedBus on its .inner (the
Kvaser's drain reads the bus itself since 2026-11-23, but its frames pass
saw() first, so its screen stays the drain's too).

    py -3.14 charge-interposer/bench_drivers/bench_topology.py
    py -3.14 .../bench_topology.py --self-test | --can-fail
"""

import argparse
import collections
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
import kvaser_link                 # noqa: E402  (the Kvaser on kvaser_usb)
import reader_death                # noqa: E402  (a drain that dies or loses frames is VOID)

import can                                                   # noqa: E402

BITRATE = 500000
DIAG_IDS = {0x7F4, 0x7F5, 0x7F6, 0x7F7}
PROBE_ID = 0x6A2            # clear of the truck, the WiCAN and the interposer
MIN_DIAG_PAGES = 4          # all four, so one stray frame cannot decide
CHARGER_ID = 0x18FFD4C0     # the charger status frame (spec 2.1 / 2.3)
CHARGER_PERIOD_S = 0.1
RELISTEN_S = 3.0            # past 2.3's 2 s fallback to listen-only
CONTROL_S = 2.0


def classify_charger_up(windows, min_pages=MIN_DIAG_PAGES):
    """windows: {talker: {"sent": n, "heard": {adapter: pages},
    "control": {adapter: pages}}}, one per adapter that talked as the
    charger; "heard" leaves the talker out. -> (verdict, vehicle, charger,
    lines), verdict OK, TEST FAULT or UNRESOLVED."""
    lines, vehicle, charger = [], set(), set()
    for x in sorted(windows):
        w = windows[x]
        up = sorted(n for n, p in w["heard"].items()
                    if n != x and p >= min_pages)
        late = sorted(n for n, p in w["control"].items() if p >= min_pages)
        lines.append("  %-10s talked (%d sent): pages on %s; control after: %s"
                     % (x, w["sent"], ", ".join(up) or "none",
                        ", ".join(late) or "quiet"))
        if late:
            return ("TEST FAULT", [], [], lines + [
                "TEST FAULT: after %s stopped, %s still heard the pages in "
                "the control window: the port did not fall back to "
                "listen-only, so nothing can be attributed" % (x, ", ".join(late))])
        if up:
            charger.add(x)
            vehicle.update(up)
    both = sorted(charger & vehicle)
    if both:
        return ("UNRESOLVED", [], [], lines + [
            "UNRESOLVED: %s both brought the port up and heard it brought up "
            "by another: no consistent segment map" % ", ".join(both)])
    if not charger:
        mute = sorted(x for x, w in windows.items() if not w["sent"])
        if mute:
            return ("TEST FAULT", [], [], lines + [
                "TEST FAULT: no talker brought the port up, and %s sent "
                "nothing, so its silence proves nothing" % ", ".join(mute)])
        return ("UNRESOLVED", [], [], lines + [
            "UNRESOLVED: no adapter's charger frames brought the four pages "
            "up anywhere: no adapter is shown on the charger segment"])
    return "OK", sorted(vehicle), sorted(charger), lines


def open_gs_usb():
    """The Innomaker, role canchg in libusb mode (adapter_roles.canchg;
    shared/bench SPEC section 8, 2026-11-18: never an index, a serial or
    the first found)."""
    return adapter_roles.canchg(BITRATE)


class Kvaser(object):
    """Role kvaser's bus (its echoes kept) with the stall rule: .watch, a
    kvaser_link.EchoWatch over can-tools' echo_watch with
    read_after_send=False (the reviewer's echo_watch ruling, 2026-11-23),
    since the Kvaser's drain thread reads the bus while sends come from the
    main thread. send() goes through the watch; the drain reads .bus and
    hands each frame to watch.saw() and calls watch.check() (Drain, by
    drain_of)."""

    def __init__(self, bus):
        self.bus = bus
        self.watch = kvaser_link.EchoWatch(bus, read_after_send=False)

    def send(self, msg, timeout=None):
        self.watch.send(msg, timeout=0.05 if timeout is None else timeout)

    def shutdown(self):
        self.bus.shutdown()


OPENERS = {
    # auto_reset on the PCAN is not optional: phase 2 transmits, and a
    # python-can PCAN bus without it latches BUS-OFF silently, turning a
    # reachability test into one that cannot fail honestly. Role pcanfd
    # (2026-11-18), bus states reported (the reviewer's PCAN item 2,
    # 2026-11-20): its states come up as error frames, never raised
    "pcan": lambda: adapter_roles.pcanfd_report(BITRATE, auto_reset=True),
    # the Kvaser on kvaser_usb, role kvaser (2026-11-13): its echoes kept
    # and consumed by the stall rule, never counted as heard (Kvaser; a
    # WatchedBus until 2026-11-23)
    "kvaser": lambda: Kvaser(kvaser_link.open_bus(receive_own_messages=True)),
    "innomaker": open_gs_usb,
}


def stall_in(drains, exc=None):
    """The kvaser TX stall that ended a drain or a send, or None."""
    if isinstance(exc, kvaser_link.Stall):
        return exc
    for d in drains.values():
        if isinstance(d.exc, kvaser_link.Stall):
            return d.exc
    return None


def void_in(drains):
    """The VOID line of a drain that died other than on a kvaser TX stall,
    or read a row reporting frames lost (reader_death; the reviewer's PCAN
    item 2, 2026-11-20), or None: its counts are not a topology answer."""
    for d in drains.values():
        if d.death.error and not isinstance(d.exc, kvaser_link.Stall):
            return d.death.void_line()
    return None


class Drain(object):
    """Reads one bus on a thread for the whole window.

    Not optional for the Innomaker: gs_usb returns a TX echo per frame
    sent and an in-flight transmit holds an echo slot until the host
    reads it back, so a transmitter nobody reads from runs out of slots
    and starts failing. It also keeps every adapter's receive buffer
    empty, which is the defect behind four separate false results on
    this bench already.
    """

    def __init__(self, name, bus, watch=None):
        self.name = name
        self.bus = bus
        # the Kvaser's stall rule (drain_of; the reviewer's echo_watch
        # ruling, 2026-11-23): every frame read goes to watch.saw() first,
        # and watch.check() follows every read
        self.watch = watch
        self.ids = collections.Counter()
        # error frames counted as bus errors by can-tools' classifier
        # (adapter_roles.bus_error; the reviewer's bench-layer item,
        # 2026-11-19): on SocketCAN, not a controller-state change or the
        # counters alone, which are not_bus; elsewhere every error frame
        self.errors = 0
        self.not_bus = 0
        self.total = 0
        self.exc = None          # what ended the drain early (a kvaser TX stall)
        # under a guard (the reviewer's PCAN item 2, 2026-11-20): a raise or
        # a row reporting frames lost ends the drain, kept in death, and the
        # run is VOID (void_in). Since 2026-11-23 the guard is can-tools'
        # canbench.reader_guard.guard (the reviewer's reader_guard ruling);
        # the screen stays this step's own, since reader_guard.screen
        # classifies the bus it is given, not a WatchedBus's .inner
        self.death = reader_death.Death(name)
        self.rg = reader_death.rg()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run)
        self._t.daemon = True

    def _run(self):
        self.rg.guard(self.death, self._stop, self._step)

    def _step(self):
        try:
            m = self.bus.recv(timeout=0.05)
            if self.watch is not None:
                if m is not None and self.watch.saw(m):
                    m = None             # an own echo: the stall rule's, never heard
                self.watch.check()
        except Exception as e:
            self.exc = e
            raise
        if m is None:
            return
        if getattr(m, "is_error_frame", False):
            why = adapter_roles.missed(m)
            if why:
                raise self.rg.MissedFrames(why, "error frame 0x%08X data %s reports %s"
                                           % (m.arbitration_id, bytes(m.data).hex(), why))
            if adapter_roles.bus_error(self.bus, m):
                self.errors += 1
            else:
                self.not_bus += 1
            return
        self.total += 1
        self.ids[m.arbitration_id] += 1

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2.0)

    def reset(self):
        self.ids.clear()
        self.errors = 0
        self.not_bus = 0
        self.total = 0


def drain_of(name, b):
    """The Drain for an opened adapter: a Kvaser's reads its bus with its
    watch (saw() and check() in the drain's own loop), any other's reads
    the bus as it is."""
    if isinstance(b, Kvaser):
        return Drain(name, b.bus, b.watch)
    return Drain(name, b)


def open_void_lines(failed):
    """An adapter that did not open leaves the topology unresolved over the
    whole bench: a VOID line, which script_judge reads (2026-11-21, the
    reviewer's sweep after mcp-reset: it printed only "FAILED  <adapter>",
    which is not a FAIL line, and RESOLVED still exited 0)."""
    if not failed:
        return []
    return ["VOID: %d adapter(s) did not open (%s): the topology is not "
            "resolved over the whole bench" % (len(failed), ", ".join(failed))]


def open_rc(failed):
    """RESOLVED's exit code: 3 (VOID) when an adapter did not open."""
    return 3 if failed else 0


# Every adapter OPENERS opens, by its canctl role: each is a bench role, so
# one left unplaced voids RESOLVED (the reviewer's sweep ruling 5,
# 2026-11-21; a device that is not a bench role would stay reported only,
# and this script opens none).
ADAPTER_ROLE = {"pcan": "pcanfd", "kvaser": "kvaser", "innomaker": "canchg"}


def unplaced_lines(quiet, charger):
    """RESOLVED's adapters that heard neither the diagnostics nor the probe
    -> (lines, rc): the unplaced line, and for any bench role a VOID line
    with exit 3; a non-role device only the line, exit 0."""
    un = [n for n in quiet if n not in charger]
    if not un:
        return [], 0
    lines = ["  unplaced        : %s" % ", ".join(un)]
    roles = [n for n in un if n in ADAPTER_ROLE]
    if not roles:
        return lines, 0
    return lines + ["VOID: %d bench-role adapter(s) unplaced (%s): the "
                    "topology is not resolved over the whole bench"
                    % (len(roles), ", ".join("%s, role %s" % (n, ADAPTER_ROLE[n])
                                             for n in roles))], 3


def fault_line(text):
    """A TEST FAULT (the check's premise not met: too few adapters opened,
    nothing sent, the port not falling back) as a VOID line, which
    script_judge reads as VOID (the reviewer's sweep, lower priority,
    2026-11-21, at this driver's next change: before, a bare exit 2)."""
    return "VOID: TEST FAULT: " + text


def topology_link(link):
    """-> (ok, line): the Kvaser's link up and read back; not ok is a VOID
    line with the kernel's USB/CAN lines."""
    t0 = time.time()
    try:
        return True, link.set_link(True)
    except link.LinkError as e:
        return False, link.link_void(e, t0, time.time())


def talk_as_charger(x, buses, drains, seconds):
    """x sends the charger status frame for `seconds` while every drain
    counts; then the port's fall-back, then a quiet control window."""
    for d in drains.values():
        d.reset()
    msg = can.Message(arbitration_id=CHARGER_ID, is_extended_id=True,
                      data=bytes(8))
    sent = errs = 0
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        try:
            buses[x].send(msg, timeout=0.2)
            sent += 1
        except kvaser_link.Stall:
            raise
        except Exception:
            errs += 1
        time.sleep(CHARGER_PERIOD_S)
    heard = {n: len(DIAG_IDS & set(d.ids)) for n, d in drains.items() if n != x}
    time.sleep(RELISTEN_S)
    for d in drains.values():
        d.reset()
    time.sleep(CONTROL_S)
    control = {n: len(DIAG_IDS & set(d.ids)) for n, d in drains.items()}
    print("  %-10s sent %d charger frame(s), %d send error(s)"
          % (x, sent, errs))
    return {"sent": sent, "heard": heard, "control": control}


class _Frame(object):
    """A stand-in python-can message (saw() reads is_error_frame)."""

    def __init__(self, aid, data, is_rx=True):
        self.arbitration_id, self.data, self.is_rx = aid, bytes(data), is_rx
        self.is_extended_id, self.is_error_frame = False, False


class _KvaserBus(object):
    """A stand-in Kvaser socket with its echoes kept: each send echoes at
    once (is_rx False) unless `lose`; offer() queues a received frame."""

    def __init__(self, lose=False):
        self.q, self.lose, self.lock = collections.deque(), lose, threading.Lock()

    def send(self, msg, timeout=None):
        if not self.lose:
            with self.lock:
                self.q.append(_Frame(msg.arbitration_id, msg.data, is_rx=False))

    def offer(self, m):
        with self.lock:
            self.q.append(m)

    def recv(self, timeout=None):
        with self.lock:
            if self.q:
                return self.q.popleft()
        time.sleep(min(timeout or 0.0, 0.005))
        return None

    def shutdown(self):
        pass


def _kvaser_drain(lose, sends, linger_s):
    """A Kvaser over the stand-in, drained by drain_of as main() does, with
    `sends` probe frames sent from this thread as phase 2 does, then
    linger_s. -> (kvaser, drain, drain still running)."""
    k = Kvaser(_KvaserBus(lose))
    d = drain_of("kvaser", k).start()
    k.bus.offer(_Frame(0x7F4, bytes(8)))
    msg = can.Message(arbitration_id=PROBE_ID, is_extended_id=False, data=bytes([0xA5] * 8))
    for _ in range(sends):
        k.send(msg, timeout=0.2)
        time.sleep(0.01)
    time.sleep(linger_s)
    alive = d._t.is_alive()
    d.stop()
    return k, d, alive


def echo_watch_cases(check):
    """The reviewer's echo_watch ruling (2026-11-23): the Kvaser's drain
    hands each frame to watch.saw() and calls watch.check() itself."""
    k, d, alive = _kvaser_drain(False, 5, 0.3)
    check(type(k.watch.w).__module__.endswith("echo_watch") and k.watch.w.read_after_send is False,
          "the Kvaser's stall rule is canbench's echo_watch, read_after_send=False")
    check(alive and d.exc is None and k.watch.echoed == 5 and not k.watch.pending
          and d.total == 1 and dict(d.ids) == {0x7F4: 1},
          "CAN-FAIL saw: five probes sent while the Kvaser's drain reads: each own echo "
          "taken by saw() (echoed %d, pending %d), none counted as heard (%d frame(s), "
          "ids %s), no stall 300 ms on (%r)" % (k.watch.echoed, len(k.watch.pending), d.total,
                                                sorted("0x%X" % i for i in d.ids), d.exc))
    k, d, alive = _kvaser_drain(True, 1, 0.4)
    st = stall_in({"kvaser": d})
    check(not alive and st is d.exc and str(st).startswith("kvaser TX stall: no own echo")
          and void_in({"kvaser": d}) is None,
          "CAN-FAIL check: an echo that never comes is a kvaser TX stall from the drain's "
          "check(), which ends the drain, and the stall's, not void_in's -> %r" % d.exc)


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    def w(sent, heard, control=None):
        return {"sent": sent, "heard": heard,
                "control": control or {"pcan": 0, "kvaser": 0, "innomaker": 0}}

    bench = {"pcan": w(60, {"kvaser": 0, "innomaker": 0}),
             "kvaser": w(60, {"pcan": 0, "innomaker": 0}),
             "innomaker": w(60, {"pcan": 4, "kvaser": 4})}
    v, veh, chg, _ = classify_charger_up(bench)
    check((v, veh, chg) == ("OK", ["kvaser", "pcan"], ["innomaker"]),
          "the bench: the Innomaker brings the pages up on PCAN and Kvaser "
          "(%s %s %s)" % (v, veh, chg))
    three = dict(bench, innomaker=w(60, {"pcan": 3, "kvaser": 3}))
    check(classify_charger_up(three)[0] == "UNRESOLVED",
          "three pages of four are not the pages")
    late = dict(bench, innomaker=w(60, {"pcan": 4, "kvaser": 4},
                                   {"pcan": 4, "kvaser": 0, "innomaker": 0}))
    check(classify_charger_up(late)[0] == "TEST FAULT",
          "pages still heard in the control window -> TEST FAULT")
    none = {k: w(60, {n: 0 for n in bench if n != k}) for k in bench}
    check(classify_charger_up(none)[0] == "UNRESOLVED",
          "no talker brings the port up -> UNRESOLVED, not a map")
    mute = dict(none, innomaker=w(0, {"pcan": 0, "kvaser": 0}))
    check(classify_charger_up(mute)[0] == "TEST FAULT",
          "no talker brings it up and one sent nothing -> TEST FAULT")
    cross = {"pcan": w(60, {"kvaser": 4, "innomaker": 0}),
             "kvaser": w(60, {"pcan": 4, "innomaker": 0}),
             "innomaker": w(60, {"pcan": 0, "kvaser": 0})}
    check(classify_charger_up(cross)[0] == "UNRESOLVED",
          "two adapters each bring the other up -> UNRESOLVED")
    own = dict(bench, innomaker=w(60, {"pcan": 4, "kvaser": 4,
                                       "innomaker": 4}))
    check(classify_charger_up(own)[:3] == ("OK", ["kvaser", "pcan"],
                                           ["innomaker"]),
          "the talker's own reception is left out (it is not a vehicle-side "
          "witness of itself)")
    # 2026-11-13, the reviewer's Kvaser rulings: the link up first, a kvaser
    # TX stall never counted as a send error or a quiet drain
    class Link(object):
        LinkError = kvaser_link.LinkError
        calls, fail = [], None

        @classmethod
        def set_link(cls, up):
            cls.calls.append(up)
            if cls.fail:
                raise kvaser_link.LinkError(cls.fail)
            return "kvaser link up"

        @staticmethod
        def link_void(e, t0, t1):
            return kvaser_link.link_void(e, t0, t1, lambda s, u: "")
    ok, ln = topology_link(Link)
    check(ok and Link.calls == [True], "link: brought up (the adapters acknowledge)")
    Link.fail = "the kvaser link reads down in sysfs, and this check needs it up"
    ok, ln = topology_link(Link)
    check(not ok and ln.startswith("VOID: kvaser link:"), "link: not up -> a VOID line")

    class Stalls(object):
        def send(self, msg, timeout=None):
            raise kvaser_link.Stall("kvaser TX stall: planted", 1.0)
    global RELISTEN_S, CONTROL_S
    saved = RELISTEN_S, CONTROL_S
    RELISTEN_S = CONTROL_S = 0.0
    try:
        talk_as_charger("kvaser", {"kvaser": Stalls()}, {}, 0.05)
        check(False, "talk: a stall on the talker counted as a send error")
    except kvaser_link.Stall:
        check(True, "talk: a stall on the talker is raised, not counted as a send error")
    finally:
        RELISTEN_S, CONTROL_S = saved
    d = Drain("kvaser", None)
    d.exc = kvaser_link.Stall("kvaser TX stall: planted", 1.0)
    check(stall_in({"kvaser": d, "pcan": Drain("pcan", None)}) is d.exc,
          "a drain ended by a stall is found")
    check(stall_in({"pcan": Drain("pcan", None)}) is None, "no stall, none found")

    # error frames classified by can-tools' bus_error (the reviewer's
    # bench-layer item, 2026-11-19): a controller-state frame (CRTL alone)
    # is no bus error on SocketCAN, a protocol + bus-error one is; off
    # SocketCAN every error frame counts
    class Err(object):
        is_error_frame, is_rx = True, True
        data = bytes(8)             # no overflow bit: not lost frames

        def __init__(self, cls):
            self.arbitration_id = 0x20000000 | cls

    class Data(object):
        is_error_frame, is_rx, arbitration_id = False, True, 0x512

    def drained(bus, wrap=None):
        frames = [Err(0x004), Err(0x008 | 0x080), Data()]

        class B(object):
            def recv(self, timeout=None):
                if frames:
                    return frames.pop(0)
                time.sleep(timeout or 0)
                return None
        b = B()
        if bus is not None:
            b = bus(b)
        if wrap is not None:
            b = wrap(b)
        d = drain_of("x", b).start()
        t_end = time.monotonic() + 5
        while frames and time.monotonic() < t_end:
            time.sleep(0.01)
        time.sleep(0.1)
        d.stop()
        return d.errors, d.not_bus, d.total

    def socketcan(b):
        import types
        b.bench_role = types.SimpleNamespace()
        b.bench_role.interface = "socketcan"
        return b
    got = drained(socketcan)
    check(got == (1, 1, 1), "SocketCAN: 1 bus error, 1 not, 1 data frame: %s" % (got,))
    got = drained(socketcan, Kvaser)
    check(got == (1, 1, 1), "the Kvaser's drain classified on its SocketCAN bus, its frames "
          "past saw(): %s" % (got,))
    got = drained(None)
    check(got == (2, 0, 1), "not SocketCAN: both error frames counted: %s" % (got,))
    # the reviewer's PCAN item 2 (2026-11-20): the PCAN opened with bus states
    # reported. A state row is not a bus error; an overrun row ends the drain
    # as lost frames and the run is VOID (void_in); a stall stays a stall
    R = reader_death.report_rows()
    rb = reader_death.report_bus([R["frame"], R["passive"], R["ack"], R["overrun"], R["frame"]])
    d = Drain("pcan", rb).start()
    d._t.join(2.0)
    v = void_in({"pcan": d})
    check(not d._t.is_alive() and d.death.lost and (d.errors, d.not_bus, d.total) == (1, 1, 1)
          and len(rb.left) == 1 and v and v.startswith("VOID: the pcan reader lost frames"),
          "CAN-FAIL: an overrun row ends the drain (1 bus error, 1 state row, 1 frame "
          "counted before it) and VOIDs the run: %s %s" % ((d.errors, d.not_bus, d.total), v))
    # 2026-11-23, the reviewer's reader_guard ruling: canbench's guard keeps
    # it as canbench's MissedFrames, saying what was missed
    check(d.death.exc_type == "MissedFrames" and d.death.missed == "frames lost"
          and "(MissedFrames: error frame 0x" in v and "reports frames lost)" in v,
          "the overrun is kept as reader_guard.MissedFrames, its reason recorded: %s"
          % d.death.error)
    d2 = Drain("pcan", reader_death.RaisingBus([], RuntimeError("A PCAN status BUSOFF"))).start()
    d2._t.join(2.0)
    check((void_in({"pcan": d2}) or "").startswith("VOID: the pcan reader died"),
          "a drain whose recv raises: VOID, the error kept")
    d3 = Drain("kvaser", reader_death.RaisingBus(
        [], kvaser_link.Stall("kvaser TX stall: planted", 1.0))).start()
    d3._t.join(2.0)
    check(stall_in({"kvaser": d3}) is d3.exc and void_in({"kvaser": d3}) is None,
          "a kvaser TX stall in a drain is still the stall's, not void_in's")
    echo_watch_cases(check)
    import inspect
    check(inspect.getsource(main).count("if void_in(drains):\n") == 3,
          "main: each phase's counts are taken only if no drain died or lost frames")
    check("drains[n] = drain_of(n, b).start()" in inspect.getsource(main),
          "main: each adapter drained by drain_of (the Kvaser's with its watch)")
    # 2026-11-21: an adapter that did not open is a VOID, not RESOLVED rc 0
    vl = open_void_lines(["canchg"])
    check(len(vl) == 1 and vl[0].startswith("VOID: 1 adapter(s) did not open "
                                            "(canchg)"),
          "an adapter that did not open -> a VOID line script_judge reads -> %s" % vl)
    check(open_void_lines([]) == [] and open_rc([]) == 0 and open_rc(["x"]) == 3,
          "all opened: no line, RESOLVED exits 0; one not: exits 3")
    msrc = inspect.getsource(main)
    check("failed.append(name)" in msrc and "for ln in open_void_lines(failed):" in msrc
          and "return max(open_rc(failed), urc)" in msrc
          and msrc.find("for ln in open_void_lines(failed):") < msrc.find("if len(buses) < 2:"),
          "main: each failed open is collected, its VOID line printed before "
          "any exit, and RESOLVED exits by open_rc")
    # 2026-11-23, the reviewer's sweep ruling 5: a bench-role adapter left
    # unplaced voids RESOLVED; a non-role device is only reported
    ul, urc = unplaced_lines(["innomaker", "kvaser"], ["kvaser"])
    check(urc == 3 and ul == ["  unplaced        : innomaker",
                              "VOID: 1 bench-role adapter(s) unplaced "
                              "(innomaker, role canchg): the topology is not "
                              "resolved over the whole bench"],
          "CAN-FAIL unplaced: a bench-role adapter unplaced -> a VOID line, "
          "exit 3 -> %s" % ul)
    ul, urc = unplaced_lines(["innomaker"], ["innomaker"])
    check(urc == 0 and ul == [], "unplaced: every quiet adapter placed -> "
                                 "nothing, exit 0")
    ul, urc = unplaced_lines(["other"], [])
    check(urc == 0 and ul == ["  unplaced        : other"],
          "unplaced: a device that is no bench role is reported, not VOID")
    check(sorted(ADAPTER_ROLE) == sorted(OPENERS),
          "CAN-FAIL unplaced: every adapter opened has its bench role named")
    check("ul, urc = unplaced_lines(quiet, charger)" in msrc and
          "for ln in ul:\n            print(ln)" in msrc,
          "main: RESOLVED prints the unplaced lines and exits by them")
    # premise failures (sweep, lower priority, 2026-11-23): VOID lines
    check(fault_line("x") == "VOID: TEST FAULT: x",
          "a TEST FAULT is a VOID line script_judge reads")
    check(msrc.count("fault_line(") == 3 and "TEST FAULT:" not in msrc.replace(
              "(the TEST FAULT \"\n", ""),
          "CAN-FAIL main: each of its three TEST FAULT exits prints a VOID "
          "line, none a bare TEST FAULT line")
    print("\nbench_topology self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--probe-count", type=int, default=40)
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to drain each handle's open backlog "
                         "before counting; --settle 0 reproduces the "
                         "cross-adapter count disagreement deliberately, "
                         "as dual_listener_duplicates.py does")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)

    # the adapters acknowledge (an ACK check; 2026-11-13, the reviewer's A):
    # the Kvaser's link up and read back before anything opens
    t_ep0 = time.time()
    ok, line = topology_link(kvaser_link)
    print(line)
    if not ok:
        return 2
    buses, drains = {}, {}
    failed = []
    for name, opener in OPENERS.items():
        try:
            buses[name] = opener()
            print("opened  %-10s" % name)
        except Exception as exc:
            print("FAILED  %-10s %s" % (name, exc))
            failed.append(name)
    for ln in open_void_lines(failed):
        print(ln)

    if len(buses) < 2:
        print("\n" + fault_line(
            "%d adapter(s) opened. A topology claim needs at "
            "least two, because with one the answer is always 'that one' "
            "and the check cannot fail." % len(buses)))
        for b in buses.values():
            b.shutdown()
        return 2

    try:
        for n, b in buses.items():
            drains[n] = drain_of(n, b).start()

        # --- SETTLE AND DRAIN BEFORE COUNTING ANYTHING -------------------
        #
        # Added 2026-10-05 after the generator-inhibit session took this
        # script's frame counts seriously and nearly lost a day to them.
        # On the same wire in the same 6 s window it reported `pcan 57`
        # against `kvaser 24` -- a 2.4x disagreement that reads as one
        # adapter dropping frames, which for a method that classifies
        # events by whether a witness SAW a frame is the worst possible
        # artefact: a dropped frame scores as a withdrawn inhibit, the
        # very thing the run exists to count.
        #
        # The adapters are fine. Measured passively over 30 s with the
        # board as the stimulus: pcan 120, kvaser 120, exactly 30 of each
        # page, ratio 1.000. The disagreement is the BACKLOG EACH HANDLE
        # HELD AT OPEN -- 41 frames drained from the PCAN against 8 from
        # the Kvaser -- which a count started at the open inherits whole.
        #
        # THE LESSON WAS ALREADY ON THIS BENCH AND THIS SCRIPT MISSED IT.
        # `dual_listener_duplicates.py` takes `--settle`, defaulting to
        # 2 s, for exactly this reason, and its `--settle 0` reproduces
        # the open-order artefact deliberately. Writing a second listener
        # without it reintroduced a known defect.
        #
        # The verdict logic never depended on the counts -- it keys on
        # all four diagnostic pages being present, and a backlog cannot
        # invent a page that was not transmitted. But a column printed
        # beside a verdict gets read as evidence for it, so it has to
        # mean something.
        if args.settle > 0:
            print("\nsettling %.1f s, then discarding what each handle "
                  "held at open" % args.settle)
            time.sleep(args.settle)
            for n, d in drains.items():
                print("  %-10s discarded %d frame(s) of open backlog"
                      % (n, d.total))
                d.reset()

        # --- phase 1: who hears the board's vehicle-only diagnostics? -----
        # Sends nothing, but the handles are normal-mode and ACKNOWLEDGE.
        # On this bench that is a change to the board's error state, not a
        # neutral observation: 3,649 bus errors/s and 4 txDropped/s with
        # nothing acknowledging, zero of both with a handle merely open
        # (measured 2026-10-06). Harmless for this script, whose verdict
        # keys on which diagnostic pages appear, but the label was wrong.
        print("\nphase 1: listening (no data frames; adapters DO ack), "
              "%.0f s" % args.seconds)
        time.sleep(args.seconds)
        if void_in(drains):
            print(void_in(drains))
            return 2
        vehicle, quiet = [], []
        for n, d in drains.items():
            pages = DIAG_IDS & set(d.ids)
            print("  %-10s %5d frames, %d err, diag pages %d/4  %s"
                  % (n, d.total, d.errors, len(pages),
                     " ".join("%03X" % i for i in sorted(d.ids)[:8])))
            if len(pages) >= MIN_DIAG_PAGES:
                vehicle.append(n)
            else:
                quiet.append(n)

        known_charger = []
        if not vehicle:
            # spec 2.3: listen-only while the charger is silent, and no
            # diagnostic frames in listen-only -- bring the charger side up
            print("\n  no adapter heard the four pages with the charger "
                  "silent (the port is listen-only, spec 2.3)")
            print("\nphase 1b: each adapter in turn talks as the charger "
                  "(0x%08X every %.1f s for %.0f s), then a %.0f s fall-back "
                  "and a %.0f s quiet control"
                  % (CHARGER_ID, CHARGER_PERIOD_S, args.seconds, RELISTEN_S,
                     CONTROL_S))
            windows = {}
            for x in sorted(buses):
                windows[x] = talk_as_charger(x, buses, drains, args.seconds)
                if stall_in(drains):
                    raise stall_in(drains)
                if void_in(drains):
                    print(void_in(drains))
                    return 2
            verdict, vehicle, known_charger, lines = classify_charger_up(windows)
            for ln in lines:
                print(ln)
            if verdict == "TEST FAULT":
                print(fault_line("not a topology result (the TEST FAULT "
                                 "line above)"))
                return 2
            if verdict == "UNRESOLVED":
                return 1
            quiet = [n for n in sorted(drains) if n not in vehicle]
            print("  CHARGER segment (brought the port up): %s"
                  % ", ".join(known_charger))
        print("  VEHICLE segment: %s" % ", ".join(vehicle))

        if not quiet:
            print("\nEvery adapter is on the vehicle segment. That is a "
                  "usable answer, but it means there is NO charger-side "
                  "adapter, and the spec 12 entry 2 arm needs one.")
            return 1

        # --- phase 2: do the quiet ones hear a forwarded probe? -----------
        print("\nphase 2: %d probe frames of 0x%03X from %s"
              % (args.probe_count, PROBE_ID, vehicle[0]))
        for d in drains.values():
            d.reset()
        src = buses[vehicle[0]]
        sent = errs = 0
        msg = can.Message(arbitration_id=PROBE_ID, is_extended_id=False,
                          data=bytes([0xA5] * 8))
        for _ in range(args.probe_count):
            try:
                src.send(msg, timeout=0.2)
                sent += 1
            except kvaser_link.Stall:
                raise
            except Exception:
                errs += 1
            time.sleep(0.01)
        time.sleep(0.5)
        if stall_in(drains):
            raise stall_in(drains)
        if void_in(drains):
            print(void_in(drains))
            return 2

        print("  offered %d, send errors %d" % (sent, errs))
        if sent == 0:
            print("\n" + fault_line("nothing was transmitted, so a silent "
                                    "adapter below proves nothing about where "
                                    "it is."))
            return 2

        charger = []
        for n in quiet:
            got = drains[n].ids.get(PROBE_ID, 0)
            print("  %-10s saw %d/%d of the probe" % (n, got, sent))
            if got > 0:
                charger.append(n)

        print()
        missed = [n for n in known_charger if n not in charger]
        if missed:
            print("UNRESOLVED: %s brought the port up from the charger side "
                  "but did not receive the probe forwarded from %s: the two "
                  "phases disagree" % (", ".join(missed), vehicle[0]))
            return 1
        if not charger:
            print("UNRESOLVED: %s heard the board's diagnostics, but %s "
                  "heard neither those nor a probe sent from the vehicle "
                  "side. They are not on a segment the board bridges -- "
                  "unplugged, bus-off, or on another wire entirely. Saying "
                  "'charger' here would be a label with no evidence under "
                  "it." % (", ".join(vehicle), ", ".join(quiet)))
            return 1

        print("RESOLVED")
        print("  vehicle segment : %s   (all four diag pages)" % ", ".join(vehicle))
        print("  charger segment : %s   (no diag pages, but received a probe "
              "the board forwarded)" % ", ".join(charger))
        ul, urc = unplaced_lines(quiet, charger)
        for ln in ul:
            print(ln)
        return max(open_rc(failed), urc)
    except kvaser_link.Stall as st:
        # not a topology result: VOID with the kernel's lines, the Kvaser
        # recovered (its socket closed first) before the next case
        for d in drains.values():
            d.stop()
        kv = buses.pop("kvaser", None)
        for ln in kvaser_link.stall_lines(st, t_ep0, close=kv.shutdown if kv else None):
            print(ln)
        return 2
    finally:
        for d in drains.values():
            d.stop()
        for b in buses.values():
            try:
                b.shutdown()
            except Exception:
                pass


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    # 2026-11-23, the reviewer's echo_watch ruling
    ("echo_watch: the saw() call dropped",
     "                if m is not None and self.watch.saw(m):\n", "                if False:\n"),
    ("echo_watch: the check() dropped", "                self.watch.check()\n",
     "                pass\n"),
    ("echo_watch: an own echo counted as heard",
     "                    m = None             # an own echo: the stall rule's, never heard\n",
     "                    pass\n"),
    ("echo_watch: the Kvaser's watch reads after each send (two readers)",
     "        self.watch = kvaser_link.EchoWatch(bus, read_after_send=False)",
     "        self.watch = kvaser_link.EchoWatch(bus)"),
    ("echo_watch: the Kvaser drained without its watch",
     "        return Drain(name, b.bus, b.watch)", "        return Drain(name, b.bus)"),
    ("echo_watch: main drains without drain_of", "            drains[n] = drain_of(n, b).start()",
     "            drains[n] = Drain(n, getattr(b, \"bus\", b)).start()"),
    # 2026-11-21, the reviewer's sweep: a failed open exited 0 on RESOLVED
    ("failed open: RESOLVED exits 0", "    return 3 if failed else 0\n",
     "    return 0\n"),
    ("failed open: no VOID line", '    return ["VOID: %d adapter(s) did not open',
     '    return ["FAILED: %d adapter(s) did not open'),
    ("failed open: not collected", "            failed.append(name)\n",
     "            pass\n"),
    ("failed open: RESOLVED returns 0 in main",
     "        return max(open_rc(failed), urc)\n", "        return urc\n"),
    # 2026-11-23, the reviewer's sweep ruling 5 and the premise rule
    ("unplaced: a bench role only reported",
     "    if not roles:\n        return lines, 0", "    if True:\n        return lines, 0"),
    ("unplaced: RESOLVED exits 0 with one unplaced",
     "        return max(open_rc(failed), urc)\n", "        return open_rc(failed)\n"),
    ("unplaced: the VOID line not printed",
     "        for ln in ul:\n            print(ln)\n", "        print(ul[:1])\n"),
    ("unplaced: an adapter without its role",
     "ADAPTER_ROLE = {\"pcan\": \"pcanfd\", \"kvaser\": \"kvaser\", \"innomaker\": \"canchg\"}",
     "ADAPTER_ROLE = {\"pcan\": \"pcanfd\", \"kvaser\": \"kvaser\"}"),
    ("premise: a TEST FAULT a bare line again",
     "    return \"VOID: TEST FAULT: \" + text", "    return \"TEST FAULT: \" + text"),
    ("premise: one TEST FAULT exit without its VOID line",
     "            print(\"\\n\" + fault_line(\"nothing was transmitted",
     "            print(\"\\n\" + (\"nothing was transmitted"),
    # 2026-11-20, the reviewer's PCAN item 2
    ("PCAN item 2: a lost-frames row counted as a state row",
     "            why = adapter_roles.missed(m)\n            if why:\n",
     "            why = adapter_roles.missed(m)\n            if False:\n"),
    ("PCAN item 2: a dead drain's counts used",
     "        if d.death.error and not isinstance(d.exc, kvaser_link.Stall):",
     "        if False:"),
    ("PCAN item 2: the drain unguarded",
     "        self.rg.guard(self.death, self._stop, self._step)",
     "        while not self._stop.is_set():\n            self._step()"),
    # 2026-11-23, the reviewer's reader_guard ruling
    ("reader_guard: a missed-frames row raised as a plain error",
     "                raise self.rg.MissedFrames(why, \"error frame",
     "                raise (lambda why, t: RuntimeError(t))(why, \"error frame"),
    ("reader_guard: a missed-frames row raised without its reason",
     "                raise self.rg.MissedFrames(why, \"error frame",
     "                raise self.rg.MissedFrames(\"\", \"error frame"),
    ("one page is enough",
     "def classify_charger_up(windows, min_pages=MIN_DIAG_PAGES):",
     "def classify_charger_up(windows, min_pages=1):"),
    ("control window ignored",
     "        if late:\n            return (\"TEST FAULT\"",
     "        if False:\n            return (\"TEST FAULT\""),
    ("contradiction accepted",
     "    if both:\n        return (\"UNRESOLVED\"",
     "    if False:\n        return (\"UNRESOLVED\""),
    ("a mute talker's silence trusted",
     "        if mute:\n            return (\"TEST FAULT\"",
     "        if False:\n            return (\"TEST FAULT\""),
    ("the talker's own reception counted",
     "        up = sorted(n for n, p in w[\"heard\"].items()\n                    if n != x and p >= min_pages)",
     "        up = sorted(n for n, p in w[\"heard\"].items()\n                    if p >= min_pages)"),
    ("no talker still a map",
     "    if not charger:\n        mute",
     "    if False:\n        mute"),
    # 2026-11-13, the reviewer's Kvaser rulings
    ("link: never brought up", "        return True, link.set_link(True)",
     "        return True, \"not set\""),
    ("link: a failure not VOID", "        return False, link.link_void(e, t0, time.time())",
     "        return True, str(e)"),
    ("talk: a stall counted as a send error",
     "            sent += 1\n        except kvaser_link.Stall:\n            raise\n",
     "            sent += 1\n"),
    ("a drain's stall not found", "        if isinstance(d.exc, kvaser_link.Stall):",
     "        if False:"),
    ("drain: every error frame a bus error",
     "            if adapter_roles.bus_error(self.bus, m):", "            if True:"),
    ("drain: no error frame a bus error",
     "            if adapter_roles.bus_error(self.bus, m):", "            if False:"),
    ("drain: not-bus errors not counted", "                self.not_bus += 1\n",
     "                pass\n"),
]


if __name__ == "__main__":
    sys.exit(main())
