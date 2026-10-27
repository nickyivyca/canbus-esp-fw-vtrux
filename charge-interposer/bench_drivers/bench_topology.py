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

    py -3.14 projects/vtrux/notes/artifacts/interposer-firmware/bench_topology.py
    py -3.14 .../bench_topology.py --self-test | --can-fail
"""

import argparse
import collections
import os
import sys
import threading
import time

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    """The Innomaker. Addressed by INDEX: can.detect_available_configs
    returns [] for gs_usb on this machine even with the DLL bootstrap
    applied, so detection must never be the gate (python-executables-
    reference.md, 2026-10-04)."""
    import libusb_package
    d = os.path.dirname(libusb_package.get_library_path())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    return can.Bus(interface="gs_usb", channel="can0", index=0,
                   bitrate=BITRATE)


OPENERS = {
    # auto_reset on the PCAN is not optional: phase 2 transmits, and a
    # python-can PCAN bus without it latches BUS-OFF silently, turning a
    # reachability test into one that cannot fail honestly.
    "pcan": lambda: can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                            bitrate=BITRATE, auto_reset=True),
    "kvaser": lambda: can.Bus(interface="kvaser", channel=0,
                              bitrate=BITRATE),
    "innomaker": open_gs_usb,
}


class Drain(object):
    """Reads one bus on a thread for the whole window.

    Not optional for the Innomaker: gs_usb returns a TX echo per frame
    sent and an in-flight transmit holds an echo slot until the host
    reads it back, so a transmitter nobody reads from runs out of slots
    and starts failing. It also keeps every adapter's receive buffer
    empty, which is the defect behind four separate false results on
    this bench already.
    """

    def __init__(self, name, bus):
        self.name = name
        self.bus = bus
        self.ids = collections.Counter()
        self.errors = 0
        self.total = 0
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
                self.errors += 1
                continue
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
        self.total = 0


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

    buses, drains = {}, {}
    for name, opener in OPENERS.items():
        try:
            buses[name] = opener()
            print("opened  %-10s" % name)
        except Exception as exc:
            print("FAILED  %-10s %s" % (name, exc))

    if len(buses) < 2:
        print("\nTEST FAULT: %d adapter(s) opened. A topology claim needs at "
              "least two, because with one the answer is always 'that one' "
              "and the check cannot fail." % len(buses))
        for b in buses.values():
            b.shutdown()
        return 2

    try:
        for n, b in buses.items():
            drains[n] = Drain(n, b).start()

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
            windows = {x: talk_as_charger(x, buses, drains, args.seconds)
                       for x in sorted(buses)}
            verdict, vehicle, known_charger, lines = classify_charger_up(windows)
            for ln in lines:
                print(ln)
            if verdict == "TEST FAULT":
                print("Not a topology result.")
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
            except Exception:
                errs += 1
            time.sleep(0.01)
        time.sleep(0.5)

        print("  offered %d, send errors %d" % (sent, errs))
        if sent == 0:
            print("\nTEST FAULT: nothing was transmitted, so a silent "
                  "adapter below proves nothing about where it is.")
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
        if len(quiet) != len(charger):
            un = [n for n in quiet if n not in charger]
            print("  unplaced        : %s" % ", ".join(un))
        return 0
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
]


if __name__ == "__main__":
    sys.exit(main())
