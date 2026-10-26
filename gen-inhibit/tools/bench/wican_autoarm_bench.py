#!/usr/bin/env python3
"""Spec 12.4's auto-arm row: power-on straight into the gate, and the boot figure.

    py -3.14 wican_autoarm_bench.py                  # all four phases
    py -3.14 wican_autoarm_bench.py --phase gate
    py -3.14 wican_autoarm_bench.py --phase reset-engine

Run from the PROJECT ROOT, not this directory.

REQUIRES AN AUTO-ARM BUILD and refuses to run without one:

    idf.py -B build-autoarm -DGEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT build

WHAT THE SPEC ASKS FOR (12.4, section 7, review D8): "a test that runs only when
`GEN_INHIBIT_AUTOARM_MODE` is built in, covering power-on straight into the gate,
waiting for the contactors, going live, and the ~0.6 s boot figure". That figure
is load-bearing rather than decorative: section 7's condition 5 argument accepts
that every latch lives in RAM and any reset clears them, on the grounds that the
auto-arm build re-arms in ~0.6 s AND that the gate will not take over an engine
already running. This measures the first half.

FOUR PHASES, because they fail for different reasons and a single pass/fail
would hide which. `reset_engine_running` was added 2026-10-04 (review gap 4).

  gate   No bus traffic at all. The device must come up ARMED with nobody having
         armed it -- that is the whole point of the build -- and must NOT be
         live, because the interlocks cannot possibly be satisfied on a silent
         bus. A build that went live here would be ignoring the gate.

  live   Start the truck replay. The device must then go live and transmit, which
         shows the gate is waiting for the interlocks rather than being broken.

  boot   The figure. With the replay already running, reboot and measure from the
         reset to the first frame we transmit. One boot per invocation, so a
         single run records the figure but never ESTABLISHES it -- see the
         `established` field.

  reset_engine_running
         Gap 4, and the half of condition 5's argument the other phases do not
         touch. Reboot into a stimulus with 0x054 forced over the 300 rpm
         threshold: the device must come up ARMED and must NOT go live, because
         the spec accepts RAM-only latches only on the grounds that the gate
         will not take over an engine already turning. Runs last, since it
         leaves the device in a state the other phases do not expect.

HOW THE BOOT FIGURE IS TIMED, because the obvious way is wrong twice over.

  NOT by subtracting the reboot timer. `/system_reboot` starts a 2000 ms
  FreeRTOS timer and only then resets, so measuring from the HTTP request and
  subtracting 2.0 s means deriving a ~600 ms answer as the difference of two
  much larger numbers -- every error in that constant lands whole on the result.
  Instead the reset is observed: HTTP is polled hard, and the reset is bracketed
  between the last response and the first failure.

  NOT with the witness's own timestamps. e4_rig_calibration.Witness keeps the
  driver's timestamp and deliberately never a host one, because host receive
  times on a USB adapter show transfer batching -- at frame scale that batching
  would be mistaken for the jitter under test. Here the question is a
  600 ms interval measured against an HTTP event on THIS host, so a host clock is
  what makes the two comparable and millisecond-scale batching is negligible
  against it. So this records both, and reports the driver-timebase figure beside
  the host one as a cross-check.

OUR FRAMES ARE IDENTIFIED BY TRAIL TIMING, not by content. Zero torque does not
identify us: the VCM commands zero torque wherever the engine is off, which is
most of the armable window, and booking "zero torque" to us over-counted 670
frames as 1251 on 2026-09-26. The VCM runs at ~10 ms and our trail offset is
500 us, so a 0x051 close behind another one is ours.

AFTERWARDS: REFLASH THE SHIPPING IMAGE. An auto-arm build transmits on the bus as
soon as it boots with no human in the loop, which is correct for the deployed
unit and is not what should be sitting on a bench device between sessions. This
script says so on exit; it cannot do it for you.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import can

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from e4_rig_calibration import BITRATE, CAPTURE, FOREIGN_IDS, Witness  # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HOST = "192.168.80.1"
CMD_ID = 0x051

# A 0x051 closer than this to its predecessor is ours, not the VCM's (the VCM
# runs at ~10 ms; our trail offset is 500 us).
TRAIL_MAX_US = 3000.0

# The stretch of the capture that can actually satisfy the gate: key on,
# contactors closed, engine off. It is only about five seconds long, which is why
# the replay loops it rather than playing on into the generator start.
GATE_WINDOW_START = 0.0
GATE_WINDOW_S = 5.0

POLL_S = 0.02          # how hard HTTP is polled to bracket the reset
REBOOT_WAIT_S = 30.0
# How long gap 4 watches AFTER the reset. Generous on purpose: the failure it
# looks for is the gate letting go late, and a short watch that sees "not
# live" twice would report a pass the device had not earned. The auto-arm
# build re-arms in well under a second, so 20 s is ~30 re-arm intervals.
WATCH_AFTER_RESET_S = 20.0

# A gap in OUR transmissions longer than this is the reset, not cadence: we trail
# the VCM's 0x051 and the looped window emits one every ~10 ms. The seam where the
# loop restarts is the only other gap, and it is milliseconds, not tenths.
SILENCE_S = 0.15


def gi(path, method="GET", timeout=4.0):
    req = urllib.request.Request("http://%s%s" % (HOST, path),
                                 data=b"" if method == "POST" else None,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def build_id():
    """The running image: /gen_inhibit's elf_sha256 since 2026-10-25 (the comment after this
    docstring). Until then the running build, from /check_status -- NOT from /gen_inhibit.

    THIS IS WHY "device" WAS NULL IN EVERY 2026-09-26 RECORD, and the cause is
    not what either session assumed. main() read `page.get("git_version")` off
    the /gen_inhibit page, and **/gen_inhibit has no git_version field at all**
    -- it never did. The reviewing session and I both reasoned that the
    23d390c build must have failed to report it; the field was simply being
    read from the wrong endpoint, so it would have been null on any build.

    Worth stating plainly because the wrong explanation was the more
    interesting one and we both reached for it: a null where a value is
    expected invites a story about the device, when the cheaper possibility is
    that nothing ever asked it. Checked 2026-10-04 against a live f363674:
    /check_status returns git_version 'f363674', /gen_inhibit returns no such
    key.
    """
    # SINCE 2026-10-25: the image from /gen_inhibit's elf_sha256 (spec 11, "The page names the
    # binary it runs"), never /check_status, which answers with the device's config,
    # credentials included (reviewer, 2026-10-20). git_version above is history.
    page = status()
    sha = page.get("elf_sha256") if isinstance(page, dict) else None
    if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None


def status():
    """The status page, tolerating a trailing fragment (see bench_ota_guard)."""
    st, txt = gi("/gen_inhibit")
    if st != 200:
        return None
    try:
        page, _ = json.JSONDecoder().raw_decode(txt.lstrip())
    except ValueError:
        return None
    return page


class StampedWitness(Witness):
    """Witness, plus a host clock.

    The base class keeps only the driver's timestamp, and for frame-scale jitter
    that is the right choice. This measurement is a ~600 ms interval against an
    HTTP event on this host, so it needs both timebases: the host one to compare
    with the poll, the driver one as an independent check on the answer.
    """

    def __init__(self, name, bus):
        Witness.__init__(self, name, bus)
        self.stamped = []

    def run(self):
        append = self.stamped.append
        recv = self.bus.recv
        perf = time.perf_counter
        while not self.stop.is_set():
            m = recv(timeout=0.2)
            if m is not None:
                append((perf(), m.timestamp, m.arbitration_id, bytes(m.data),
                        bool(m.is_error_frame), bool(m.is_extended_id)))


class Replay(threading.Thread):
    """Loop the armable window at its recorded timing until told to stop.

    LOOPED, not played through, and the reason is the gate. The only stretch of
    this capture where the interlocks can be satisfied is about five seconds long;
    after it the VCM starts requesting the generator, which section 7 refuses to
    arm into, and later the engine turns and a live inhibit correctly aborts. A
    boot-to-live measurement needs the armable condition to persist, so the window
    is repeated.

    The cost is honest and worth stating: looping restarts the VCM's rolling
    counter, so the device will report `ctr_bad` at each seam. That is the
    harness's doing and not a device fault, and it is why this script does not
    judge ctr_bad.
    """

    def __init__(self, bus, frames):
        threading.Thread.__init__(self, daemon=True)
        self.bus = bus
        self.frames = frames
        self.stop = threading.Event()
        self.sent = 0
        self.sent_cmd = 0
        self.errors = 0

    def run(self):
        perf = time.perf_counter
        span = self.frames[-1][0] if self.frames else 0.0
        base = perf()
        while not self.stop.is_set():
            for off, ident, data, ext in self.frames:
                if self.stop.is_set():
                    return
                due = base + off
                # A spin, because Windows' time.sleep rounds to the system timer
                # and would smear the replay's timing.
                while perf() < due:
                    pass
                try:
                    self.bus.send(can.Message(arbitration_id=ident, data=data,
                                              is_extended_id=ext))
                    self.sent += 1
                    # 0x051 counted separately so the witness's 0x051 count
                    # can be differenced against what WE put on the wire. The
                    # excess is the device's, and that is the only wire-side
                    # measure of "did it transmit" that does not depend on
                    # trail timing. See the gap-4 phase.
                    if ident == CMD_ID:
                        self.sent_cmd += 1
                except can.CanError:
                    self.errors += 1
            base += span


def load_window(start, seconds):
    out, t0 = [], None
    for m in can.LogReader(CAPTURE):
        if t0 is None:
            t0 = m.timestamp
        off = m.timestamp - t0
        if off < start:
            continue
        if off >= start + seconds:
            break
        out.append((off - start, m.arbitration_id, bytes(m.data),
                    bool(m.is_extended_id)))
    return out


def ours_from(stamped, since_perf, clock=0):
    """Times of frames we transmitted, after `since_perf`, on ONE clock.

    `clock` selects the timebase and is used for the ordering, the trail test AND
    the returned value -- all three. The first version sorted by the driver
    timestamp and returned host timestamps, so the gap sequence was computed on one
    clock over an order set by another; USB transfer batching guarantees the two
    disagree somewhere, and the largest gap is precisely what the boot figure
    reads.

    clock=0 is the host's perf_counter, which is what an HTTP poll can be compared
    against. clock=1 is the driver's, for the independent check.
    """
    k = 0 if clock == 0 else 1
    cmd = sorted((s for s in stamped if s[2] == CMD_ID and not s[5]),
                 key=lambda s: s[k])
    out, prev = [], None
    for s in cmd:
        zero = (len(s[3]) >= 3
                and int.from_bytes(s[3][1:3], "little") == 0x8000)
        if prev is not None and (s[k] - prev) * 1e6 <= TRAIL_MAX_US and zero:
            if s[0] >= since_perf:
                out.append(s)
        else:
            prev = s[k]
    return [(f[k], f) for f in out]


def open_buses():
    # The SAME pair e4_device_calibration.py uses: PCAN transmits, Kvaser
    # witnesses. The rig calibration established that the IXXAT times correctly
    # but drops frames under sustained load (4,860 of 70,936 in a 30 s arm),
    # while the Kvaser counted every frame at every rate -- and a witness that
    # silently drops frames would read here as the device failing to transmit.
    #
    # auto_reset=True IS LOAD-BEARING IN THIS HARNESS, and its absence was
    # silently destroying runs (found 2026-10-04).
    #
    # Every phase here except `gate` REBOOTS THE DEVICE WHILE REPLAYING. While
    # it is down, the WiCAN is the only other node on the segment, so nothing
    # ACKs the PCAN's frames: every transmission errors, the error counter
    # climbs, and the controller goes BUS-OFF. Without auto_reset it stays
    # there, so the stimulus stops for the rest of the phase -- and the device,
    # once back, correctly reports 'no fresh 0x051'. The run then looks like a
    # device that would not go live.
    #
    # Measured on two consecutive runs of the same phase: one with the device
    # up throughout gave sent 68,314 / errors 0; the one whose reboot landed
    # mid-replay gave sent 12,053 / errors 57,219, other_frames moving only
    # 24 -> 92, cmd_fresh False and gene_rpm 0 for the whole 20 s watch.
    #
    # THIS IS A STRONG CANDIDATE FOR THE BOOT FIGURE THAT "READ 824 ms ONCE
    # AND DID NOT REPRODUCE" (README, 2026-09-26): a silence in our
    # transmissions after a reset is exactly what this produces, and it would
    # be timed as a boot interval. Not asserted -- the 09-26 run records no
    # replay error count, so it cannot be confirmed after the fact. It is a
    # hypothesis with a mechanism, and the fix below makes future runs able to
    # tell the two apart.
    #
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                 auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    return tx, kv


def phase_gate(out):
    """Armed on boot with nobody arming it, and NOT live on a silent bus."""
    print("\n== phase gate: up armed, and held by the interlocks ==")
    page = status()
    if page is None:
        print("  FAIL: the device did not answer")
        out["gate"] = {"ok": False, "why": "no answer"}
        return False

    mode = page.get("mode")
    live = bool(page.get("inhibit_live"))
    block = page.get("arm_block", "")
    ok = True

    if mode in (None, 0):
        print("  FAIL: mode is %r. This is not an auto-arm build -- build it "
              "with -DGEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT" % (mode,))
        ok = False
    else:
        print("  ok: came up in mode %d with nobody having armed it" % mode)

    if live:
        print("  FAIL: inhibit_live is true on a silent bus. The interlocks "
              "cannot be satisfied with no traffic, so the gate is not holding")
        ok = False
    else:
        print("  ok: not live, blocked by %r" % block)

    out["gate"] = {"ok": ok, "mode": mode, "live": live, "arm_block": block}
    return ok


def phase_live(out, tx, kv, frames):
    """With traffic, it must go live and transmit."""
    print("\n== phase live: the gate passes once the interlocks arrive ==")
    w = StampedWitness("kvaser", kv)
    w.start()
    r = Replay(tx, frames)
    t_start = time.perf_counter()
    r.start()

    live_at, deadline = None, t_start + 20.0
    while time.perf_counter() < deadline:
        page = status()
        if page and page.get("inhibit_live"):
            live_at = time.perf_counter()
            break
        time.sleep(0.05)

    time.sleep(2.0)
    r.stop.set(); r.join(timeout=5.0)
    w.stop.set(); w.join(timeout=3.0)

    mine = [t for t, _f in ours_from(w.stamped, t_start)]
    page = status() or {}
    #
    # WHY THE WITNESS COUNT SITS BELOW tx_ok, AND WHY THAT IS THE EXPECTED
    # DIRECTION (reviewing session, 2026-10-04: witness 195 against tx_ok 204).
    # They measure different intervals and different things:
    #
    #   tx_ok  is the DEVICE'S LIFETIME counter since its driver was installed.
    #          It therefore includes anything it sent before this phase opened
    #          its witness and after the witness stopped, and it is not reset
    #          per phase.
    #   mine   is a WINDOWED count, from t_start to the moment the witness
    #          thread was stopped, and it is additionally subject to USB
    #          dongle loss under load.
    #
    # So witness <= tx_ok is normal and witness > tx_ok would be the
    # surprising direction. Both numbers are recorded with the window so the
    # difference is attributable instead of being left as an unexplained gap.
    #
    out["live"] = {
        "went_live": live_at is not None,
        "seconds_to_live": round(live_at - t_start, 3) if live_at else None,
        "frames_we_sent": len(mine),
        "device_tx_ok": page.get("tx_ok"),
        "replayed": r.sent,
        "witness_window_s": round(time.perf_counter() - t_start, 3),
        "counts_note":
            "frames_we_sent is windowed (t_start to witness stop) and subject "
            "to dongle loss; device_tx_ok is the device's lifetime counter "
            "since driver install and is not phase-scoped. witness <= tx_ok "
            "is the expected direction; witness > tx_ok would not be.",
    }
    if live_at is None:
        print("  FAIL: never went live within 20 s (block %r)"
              % page.get("arm_block"))
        return False
    print("  ok: live %.2f s after traffic started" % (live_at - t_start))
    print("  we transmitted %d frames on the wire; device reports tx_ok=%s"
          % (len(mine), page.get("tx_ok")))
    return len(mine) > 0


def phase_boot(out, tx, kv, frames):
    """The figure: reset -> first frame we transmit, with traffic already there."""
    print("\n== phase boot: reset to first transmitted frame ==")
    w = StampedWitness("kvaser", kv)
    w.start()
    r = Replay(tx, frames)
    r.start()
    time.sleep(3.0)          # let it be live before the reset

    # CLEAR A LATCHED ABORT FIRST, with traffic already flowing. Between runs the
    # bus is quiet, so section 7's link-loss trip fires and latches -- and a latch
    # clears only on a key-on transition (7.1) or an explicit re-arm. Re-arming
    # here does not weaken what this phase measures: phase gate is what proves
    # arming on boot, and this phase measures how quickly a RESET comes back,
    # which is what section 7's condition 5 argument rests on.
    page = status() or {}
    if not page.get("inhibit_live"):
        print("  clearing a latched abort (%r) and re-arming"
              % page.get("arm_block"))
        gi("/gen_inhibit_set?mode=3&offset=500", method="POST")
        for _ in range(30):
            time.sleep(0.5)
            page = status() or {}
            if page.get("inhibit_live"):
                break

    page = status() or {}
    if not page.get("inhibit_live"):
        print("  FAIL: not live before the reset, so this would measure the "
              "wrong thing (block %r)" % page.get("arm_block"))
        r.stop.set(); w.stop.set()
        out["boot"] = {"ok": False, "why": "not live before reset"}
        return False

    st, txt = gi("/system_reboot", method="POST", timeout=8.0)
    print("  POST /system_reboot -> HTTP %s %r" % (st, (txt or "")[:40]))

    # BRACKET THE RESET rather than trusting the 2000 ms reboot timer: the last
    # response is before it and the first failure is after it.
    last_ok, first_fail = time.perf_counter(), None
    deadline = time.perf_counter() + REBOOT_WAIT_S
    while time.perf_counter() < deadline:
        st, _ = gi("/gen_inhibit", timeout=2.0)
        now = time.perf_counter()
        if st == 200:
            last_ok = now
        else:
            # Two in a row, because one slow reply under truck-rate load is not
            # a reset -- that misreading ended this loop early on 2026-09-26.
            st2, _ = gi("/gen_inhibit", timeout=2.0)
            if st2 != 200:
                first_fail = now
                break
            last_ok = time.perf_counter()
        time.sleep(POLL_S)

    if first_fail is None:
        print("  FAIL: the device never stopped answering, so no reset was seen")
        r.stop.set(); w.stop.set()
        out["boot"] = {"ok": False, "why": "no reset observed"}
        return False

    print("  reset bracketed to a %.0f ms window" % ((first_fail - last_ok) * 1e3))

    # Let it boot and re-arm, with traffic running throughout. Long enough to
    # show a settled cadence after the edge, not merely to find the edge.
    time.sleep(25.0)
    r.stop.set(); r.join(timeout=5.0)
    w.stop.set(); w.join(timeout=3.0)

    # THE SILENCE IS THE MEASUREMENT, on the host clock, which is the one the
    # reset bracket is on. The driver clock is computed the same way below as an
    # independent check rather than as a second opinion to choose between.
    mine = [t for t, _f in ours_from(w.stamped, 0.0, clock=0)]
    mine_drv = [t for t, _f in ours_from(w.stamped, 0.0, clock=1)]
    if len(mine) < 2:
        print("  FAIL: %d frames from us in the whole phase -- nothing to "
              "measure" % len(mine))
        out["boot"] = {"ok": False, "why": "too few transmissions"}
        return False

    gaps = [(mine[i + 1] - mine[i], mine[i], mine[i + 1])
            for i in range(len(mine) - 1)]
    longest, before, after = max(gaps)
    if longest < SILENCE_S:
        print("  FAIL: the longest gap in our transmissions was %.0f ms, which "
              "is cadence rather than a reset -- the device did not stop, so "
              "the reboot did not take" % (longest * 1e3))
        out["boot"] = {"ok": False, "why": "no silence found",
                       "longest_gap_ms": round(longest * 1e3, 1)}
        return False

    # SANITY: the reset must fall inside the silence we found. If the HTTP
    # bracket sits outside it, the gap is something else and the figure is not
    # the boot time.
    inside = (before - 0.5) <= last_ok and first_fail <= (after + 0.5)
    #
    # "ok" MEANS THE MEASUREMENT WAS TAKEN CLEANLY, NOT THAT THE FIGURE IS
    # ESTABLISHED, and the two were conflated here until 2026-10-04 (reviewing
    # session). The 2026-09-26 record stored ok:true against a single reading
    # of 824 ms that never reproduced -- later attempts showed HTTP stopping at
    # the timer while our transmissions never paused -- and the README has said
    # "not established; spec's ~0.6 s is not contradicted" ever since. So the
    # stored verdict read STRONGER than the prose written from it, which is the
    # wrong way round for a record someone will later quote without the prose.
    #
    # One boot per invocation is all this phase does, so a single run can never
    # establish the figure on its own. `established` is therefore always False
    # here; reproducing it means comparing the per-phase files from separate
    # runs, which is now possible because they no longer overwrite each other.
    #
    out["boot"] = {
        "ok": inside,
        "ok_means": "the reset was observed and the silence brackets it; "
                    "NOT that the boot figure is established",
        "established": False,
        "established_note": "a single reading. The 2026-09-26 824 ms figure "
                            "did not reproduce. Compare boot_to_live_s across "
                            "the per-phase files from separate runs before "
                            "quoting it.",
        "reset_bracket_ms": round((first_fail - last_ok) * 1e3, 1),
        "boot_to_live_s": round(longest, 3),
        "transmissions": len(mine),
        "after_silence": sum(1 for t in mine if t >= after),
        "http_bracket_inside_silence": inside,
    }
    print("  silence in our transmissions: %.0f ms" % (longest * 1e3))

    # THE SAME FIGURE ON THE DRIVER'S CLOCK. If these disagree materially the
    # batching matters at this scale and neither number should be quoted.
    if len(mine_drv) >= 2:
        dg = max(b - a for a, b in zip(mine_drv, mine_drv[1:]))
        out["boot"]["boot_to_live_s_driver_clock"] = round(dg, 3)
        skew = abs(dg - longest) * 1e3
        print("  the same silence on the driver's clock: %.0f ms (%.0f ms apart)"
              % (dg * 1e3, skew))
        if skew > 50.0:
            print("  WARNING: the two clocks disagree by more than 50 ms, so "
                  "transfer batching matters at this scale and neither figure "
                  "should be quoted as the boot time")
    if not inside:
        print("  FAIL: the HTTP reset bracket does not fall inside that "
              "silence, so the gap is not the reset")
        return False
    print("  BOOT TO LIVE: %.2f s (reset -> first frame transmitted again)"
          % longest)
    print("  %d transmissions in the phase, %d of them after the silence"
          % (len(mine), out["boot"]["after_silence"]))
    return True


RPM_ID = 0x054
RUN_RPM_RAW = 32767 + 800        # the figure wican_trip_bench.py uses


def _engine_running(frames):
    """The same window with 0x054 GENE_RotSpd forced over the 300 rpm threshold.

    Returns (frames, n_mutated). The COUNT is not decoration -- see the phase
    below for why a zero there invalidates the whole result.
    """
    out, n = [], 0
    for t, arb, data, ext in frames:
        if arb == RPM_ID and len(data) >= 2:
            d = bytearray(data)
            d[0] = RUN_RPM_RAW & 0xFF
            d[1] = (RUN_RPM_RAW >> 8) & 0xFF
            data = bytes(d)
            n += 1
        out.append((t, arb, data, ext))
    return out, n


def phase_reset_engine_running(out, tx, kv, frames):
    """Gap 4: a reset while the engine is TURNING must come up armed and NOT live.

    WHY THIS IS THE PHASE THAT MATTERS MOST. Spec 7's condition 5 argument is
    what makes the auto-arm build acceptable at all: every latch lives in RAM,
    so any reset -- panic, brownout, config save, a connector carrying both
    power and CAN -- clears them, and the device re-arms in ~0.6 s with nobody
    in the loop. The spec accepts that ONLY because "the gate will not take
    over an engine that is already running". The other phases measure how fast
    it comes back. This one measures that coming back is safe, which is the
    half the argument actually rests on.

    TWO WAYS THIS PASSES WITHOUT TESTING ANYTHING, both guarded:

      THE DEVICE NEVER ARMED. "not live" is satisfied trivially by a device
      sitting in mode 0 -- and that is indistinguishable in the result from a
      gate correctly refusing. So mode is asserted non-OFF as well, and the
      block reason is required to be the generator-running one rather than
      merely non-empty. A device blocked on "no fresh 0x051" has told us
      nothing about condition 5.

      THE WINDOW HAS NO 0x054. Forcing rpm on a window that carries no 0x054
      mutates nothing, the device never hears a running engine, and it
      correctly does not go live -- for entirely the wrong reason. The
      mutation count is checked and the phase FAILS if it is zero, rather
      than reporting a pass it has not earned.
    """
    print("\n== phase reset_engine_running (gap 4): reset with the engine "
          "turning ==")
    hot, n_mut = _engine_running(frames)
    print("  forced 0x054 over the threshold in %d of %d frames"
          % (n_mut, len(frames)))
    if n_mut == 0:
        print("  FAIL: the replay window carries no 0x054, so nothing told "
              "the device the engine was running.")
        print("  'Did not go live' would pass here for the wrong reason, so "
              "this is a failure of the test, not a result.")
        out["reset_engine_running"] = {"ok": False, "why": "no 0x054 in window",
                                       "mutated": 0}
        return False

    w = StampedWitness("kvaser", kv)
    w.start()
    r = Replay(tx, hot)
    r.start()
    time.sleep(3.0)

    st, txt = gi("/system_reboot", method="POST", timeout=8.0)
    print("  POST /system_reboot -> HTTP %s %r" % (st, (txt or "")[:40]))

    last_ok, first_fail = time.perf_counter(), None
    deadline = time.perf_counter() + REBOOT_WAIT_S
    while time.perf_counter() < deadline:
        s, _ = gi("/gen_inhibit", timeout=2.0)
        now = time.perf_counter()
        if s == 200:
            last_ok = now
        else:
            s2, _ = gi("/gen_inhibit", timeout=2.0)
            if s2 != 200:
                first_fail = now
                break
            last_ok = time.perf_counter()
        time.sleep(POLL_S)

    if first_fail is None:
        print("  FAIL: the device never stopped answering, so no reset "
              "happened and nothing was tested")
        r.stop.set(); w.stop.set()
        out["reset_engine_running"] = {"ok": False, "why": "no reset observed",
                                       "mutated": n_mut}
        return False
    print("  reset bracketed to a %.0f ms window"
          % ((first_fail - last_ok) * 1e3))

    # Watch for long enough that a late arm would be caught, not just the
    # first moment after boot. The replay keeps the engine "running" the whole
    # time, so there is no window in which going live would be correct.
    seen = []
    recv = []
    # Snapshot both counters at the window edges so the comparison is over the
    # SAME interval on both instruments -- see the reconciliation below.
    cmd_sent_at_start = r.sent_cmd
    stamped_at_start = len(w.stamped)
    t_watch = time.perf_counter()
    while time.perf_counter() - t_watch < WATCH_AFTER_RESET_S:
        page = status()
        if page is not None:
            seen.append((round(time.perf_counter() - t_watch, 2),
                         page.get("mode"), bool(page.get("inhibit_live")),
                         page.get("arm_block", "")))
            # ESTABLISH THAT THE DEVICE IS HEARING US, which is the quantity
            # that distinguishes "the gate held" from "there was nothing to
            # hold against". A block of 'no fresh 0x051' with other_frames
            # flat means the stimulus never arrived, and then "not live" says
            # nothing at all.
            recv.append((page.get("other_frames"), page.get("cmd_fresh"),
                         page.get("rpm_fresh"), page.get("gene_rpm"),
                         page.get("tx_ok"), page.get("would_tx")))
        time.sleep(0.5)

    if recv:
        first, last = recv[0], recv[-1]
        print("  device receive: other_frames %s -> %s, cmd_fresh %s -> %s, "
              "rpm_fresh %s -> %s, gene_rpm %s -> %s"
              % (first[0], last[0], first[1], last[1],
                 first[2], last[2], first[3], last[3]))
    print("  replay thread: sent %d, errors %d, alive %s"
          % (r.sent, r.errors, r.is_alive()))

    r.stop.set(); r.join(timeout=5.0)
    w.stop.set(); w.join(timeout=3.0)

    if not seen:
        print("  FAIL: the device never came back, so nothing was observed")
        out["reset_engine_running"] = {"ok": False, "why": "never came back",
                                       "mutated": n_mut}
        return False

    ever_live = [s for s in seen if s[2]]
    armed = [s for s in seen if s[1] not in (None, 0)]
    blocks = sorted({s[3] for s in seen})
    mine = [t for t, _f in ours_from(w.stamped, 0.0, clock=0)]

    ok = True
    if ever_live:
        print("  FAIL: inhibit_live went TRUE %d times while the engine was "
              "turning, first at t+%.2f s. This is the case spec 7 condition "
              "5 exists to prevent." % (len(ever_live), ever_live[0][0]))
        ok = False
    else:
        print("  ok: never live across %d readings over %.0f s"
              % (len(seen), WATCH_AFTER_RESET_S))

    if not armed:
        print("  FAIL: the device never armed (mode stayed %r), so 'not live' "
              "says nothing about the gate -- this is the vacuous pass, not a "
              "pass." % (seen[0][1],))
        ok = False
    else:
        print("  ok: came up ARMED (mode %r) with nobody arming it, and was "
              "held by the gate" % (armed[0][1],))

    #
    # THE STIMULUS IS A PRECONDITION, NOT A RESULT. If the replay died -- the
    # bus-off case above -- the device hears nothing and every verdict below
    # is about an absent stimulus rather than about the gate. Check it FIRST
    # and say so, so a harness fault is never reported as a device verdict.
    #
    heard = (recv and recv[-1][0] is not None and recv[0][0] is not None
             and (recv[-1][0] - recv[0][0]) > 100)
    if not heard or r.errors > r.sent:
        print("  TEST FAULT, not a device result: the stimulus did not reach "
              "the device.")
        print("      replay sent %d with %d errors; other_frames moved %s -> "
              "%s; cmd_fresh %s, rpm_fresh %s"
              % (r.sent, r.errors, recv[0][0] if recv else "?",
                 recv[-1][0] if recv else "?",
                 recv[-1][1] if recv else "?", recv[-1][2] if recv else "?"))
        print("      Nothing below says anything about condition 5. Re-run.")
        out["reset_engine_running"] = {
            "ok": False, "why": "stimulus did not reach the device",
            "test_fault": True, "replay_sent": r.sent,
            "replay_errors": r.errors,
            "other_frames": [recv[0][0], recv[-1][0]] if recv else None}
        return False

    want = "generator running"
    if not any(want in (s[3] or "") for s in seen):
        print("  FAIL: never blocked on %r. Blocks seen: %s. Being held for "
              "another reason leaves condition 5 untested." % (want, blocks))
        ok = False
    else:
        print("  ok: blocked on %r, which is condition 5 itself" % want)

    #
    # DID THE DEVICE TRANSMIT? TWO INSTRUMENTS, AND THEY CAN DISAGREE HERE.
    #
    # `mine` comes from ours_from(), which attributes a 0x051 to us by TRAIL
    # TIMING -- a frame arriving within the trail offset behind another 0x051.
    # That heuristic is sound while the device is live on a clean stimulus, and
    # it is NOT sound here: this phase loops a 5 s slice of a real, heavily
    # loaded capture (11,745 frames, ~2,350 fps), so every loop seam butts the
    # window's last frames against its first, and any naturally close-spaced
    # pair in the truck's own traffic reads as a trail. The first run of this
    # phase attributed 411 frames to us while the device reported not live.
    #
    # `tx_ok` is the device's own count of frames it completed on the wire, and
    # for "did IT transmit" it is the authority -- it cannot be confused by the
    # replay's timing. So tx_ok decides, and the witness figure is reported
    # beside it rather than being silently dropped: if tx_ok is flat while the
    # witness attributes frames, that is a classification artefact of the
    # looped stimulus and is stated as one; if they agree, both say so.
    #
    #
    # THE ORTHOGONAL WIRE COUNT (reviewing session, 2026-10-04). The three
    # criteria above all come from the device's own HTTP page, and tx_ok being
    # flat is the device vouching for itself. This is the independent wire
    # measure, and it is the E4 reconciliation rather than a trail heuristic:
    #
    #   every 0x051 the witness saw  -  every 0x051 the PCAN sent  =  the
    #   device's 0x051
    #
    # It needs no attribution rule at all, which is the point: ours_from()'s
    # trail timing is what the looped real-traffic stimulus defeats, and this
    # replaces it with subtraction over the same interval.
    #
    # A NEGATIVE excess is expected and benign -- it means the witness dropped
    # frames, which a USB dongle does under load -- so the test is "excess <= 0
    # within the witness's loss", not "excess == 0". A POSITIVE excess beyond
    # that is the device transmitting, and there is no innocent reading of it.
    #
    cmd_sent_window = r.sent_cmd - cmd_sent_at_start
    cmd_seen_window = sum(
        1 for s in w.stamped[stamped_at_start:]
        if s[2] == CMD_ID and not s[4] and t_watch <= s[0])
    excess = cmd_seen_window - cmd_sent_window
    print("  wire reconciliation over the watch window:")
    print("      PCAN sent    %6d x 0x051" % cmd_sent_window)
    print("      witness saw  %6d x 0x051" % cmd_seen_window)
    print("      excess       %6d  (witness minus ours; the device's 0x051)"
          % excess)
    if excess > 0:
        print("  FAIL: %d 0x051 on the wire that we did not send. The device "
              "transmitted while the gate claimed to hold -- this is a wire "
              "measurement and does not depend on trail timing." % excess)
        ok = False
    else:
        print("  ok: no 0x051 on the wire that we did not send (%d short, "
              "which is witness loss and is the expected direction)"
              % -excess)

    tx_first = next((r[4] for r in recv if r[4] is not None), None)
    tx_last = next((r[4] for r in reversed(recv) if r[4] is not None), None)
    tx_delta = (tx_last - tx_first) if (tx_first is not None
                                        and tx_last is not None) else None
    out.setdefault("_tmp", None)
    if tx_delta is None:
        print("  FAIL: the device never reported tx_ok, so whether it "
              "transmitted cannot be established either way")
        ok = False
    elif tx_delta > 0:
        print("  FAIL: the device transmitted -- tx_ok rose by %d while the "
              "gate claimed to be holding. The witness attributed %d frames "
              "to us. This is the real failure: a gate that reports blocked "
              "and transmits anyway." % (tx_delta, len(mine)))
        ok = False
    elif mine:
        print("  ok: tx_ok did not move (%d), so the device transmitted "
              "NOTHING." % tx_first)
        print("      The witness attributed %d frames to us, which is a "
              "known artefact of this phase's LOOPED real-traffic stimulus, "
              "not a transmission -- see the comment above. Reported, not "
              "counted against the device." % len(mine))
    else:
        print("  ok: tx_ok did not move and the witness attributed no frames "
              "to us -- both instruments agree the device was silent")

    out["reset_engine_running"] = {
        "ok": ok, "mutated": n_mut, "readings": len(seen),
        "watch_s": WATCH_AFTER_RESET_S,
        "ever_live": [list(s) for s in ever_live],
        "blocks_seen": blocks, "armed_mode": armed[0][1] if armed else None,
        "witness_attributed_frames": len(mine),
        "witness_attribution_note":
            "ours_from() uses trail timing, which this phase's looped "
            "real-traffic stimulus defeats at every seam. tx_ok is the "
            "authority for whether the device transmitted.",
        "tx_ok_first": tx_first, "tx_ok_last": tx_last,
        "tx_ok_delta": tx_delta,
        "wire": {"cmd_sent_by_us": cmd_sent_window,
                 "cmd_seen_by_witness": cmd_seen_window,
                 "excess_attributable_to_device": excess,
                 "note": "witness minus ours over the same interval. Needs no "
                         "attribution rule; negative is witness loss. A "
                         "positive excess is the device transmitting."},
        "device_received": {"other_frames_first": recv[0][0],
                            "other_frames_last": recv[-1][0],
                            "gene_rpm_last": recv[-1][3],
                            "rpm_fresh_last": recv[-1][2]} if recv else None,
        "reset_bracket_ms": round((first_fail - last_ok) * 1e3, 1)}
    out["reset_engine_running"].pop("_tmp", None)
    out.pop("_tmp", None)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="all",
                    choices=("all", "gate", "live", "boot", "reset-engine"))
    ap.add_argument("--out", default=gi_paths.runs("autoarm_bench.json"))
    ap.add_argument("--expect-autoarm", type=int, default=1, choices=(0, 1),
                    help="the arm state this device's build must have. "
                         "Defaults to 1 because this harness has no meaning "
                         "on a shipping build.")
    ap.add_argument("--allow-unknown-build", action="store_true",
                    help="proceed even if the device will not name its build. "
                         "Only for diagnosing the build-reporting path itself; "
                         "the run cannot be tied to an image afterwards.")
    args = ap.parse_args()

    page = status()
    if page is None:
        print("device did not answer at %s -- join its AP first" % HOST)
        return 2
    build = build_id()          # /gen_inhibit's elf_sha256 since 2026-10-25 -- see above
    print("device %s, mode %s, live %s"
          % (build or "?", page.get("mode"), page.get("inhibit_live")))

    #
    # REFUSE A RUN THAT CANNOT BE TIED TO AN IMAGE (2026-10-04, reviewing
    # session). "device" was null in all three 2026-09-26 records, because
    # git_version came back empty from that build -- so even the boot record
    # that survived does not say what it ran against. The image's file
    # timestamp was consistent with 23d390c, which is circumstantial and not
    # provenance.
    #
    # This is the gap that makes a result unusable rather than wrong: a figure
    # with no build attached cannot be carried forward under spec 13's
    # code-identity rule, cannot be compared with a later run, and cannot be
    # matched to an md5 anyone verified. Better to stop and fix the reporting
    # than to collect another unattributable number.
    #
    if not build:
        if not args.allow_unknown_build:
            print("\nREFUSING TO RUN: the device names no elf_sha256 on /gen_inhibit, "
                  "so nothing measured here could be tied to an image.")
            print("  That is exactly how the 2026-09-26 gate/live figures "
                  "became unusable -- device was null in every record.")
            print("  Fix the build reporting, or pass --allow-unknown-build "
                  "if the reporting path is itself what you are debugging.")
            return 2
        print("  WARNING: build unknown and --allow-unknown-build given. "
              "Every figure below is UNATTRIBUTABLE to an image.")

    out = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "device": build,
           "build_known": bool(build),
           "expect_autoarm": args.expect_autoarm,
           "capture": os.path.basename(CAPTURE),
           "window": [GATE_WINDOW_START, GATE_WINDOW_S]}

    #
    # REBOOT BEFORE JUDGING THE ARM STATE (2026-10-04). "Came up armed with
    # nobody having armed it" is a claim about a POWER-ON, and reading it from
    # whatever state the device happens to be in reads leftover state instead.
    # Concretely: diag_build_page_check.py posts mode=0 when it finishes, so
    # running it first leaves even an auto-arm build sitting disarmed -- and
    # this harness then reported "mode 0, not an auto-arm build" about an image
    # that auto-arms perfectly well. The bus is silent here (the replay buses
    # are not opened until the phases below), which is exactly the condition
    # phase gate wants.
    #
    print("\nrebooting so the gate is judged from a power-on, not from "
          "whatever the last tool left behind")
    st, _ = gi("/system_reboot", method="POST", timeout=8.0)
    print("  POST /system_reboot -> HTTP %s" % st)
    #
    # WAIT FOR IT TO GO DOWN FIRST, THEN COME BACK. `/system_reboot` arms a
    # 2000 ms FreeRTOS timer and only then resets -- phase_boot documents this
    # and the first version of this block ignored it, polling until status()
    # merely ANSWERED. It answered at once, because the device had not reset
    # yet, so the loop broke on the pre-reboot page and then fell back to that
    # same stale page. The result was a confident "came up in mode 0" about a
    # reading taken BEFORE the reboot, which is how an auto-arm build got
    # reported as not being one.
    #
    # Observe the reset instead of trusting the timer: poll until HTTP fails,
    # then until it answers again.
    #
    went_down = False
    deadline = time.perf_counter() + REBOOT_WAIT_S
    while time.perf_counter() < deadline:
        if status() is None:
            went_down = True
            break
        time.sleep(0.2)
    if not went_down:
        print("  FAIL: the device never stopped answering, so no reset "
              "happened and anything read below is pre-reboot state")
        out["reboot_before_gate"] = {"ok": False, "why": "no reset observed"}
        _save(out, args.out)
        return 2
    print("  reset observed (HTTP went away)")

    back = None
    deadline = time.perf_counter() + REBOOT_WAIT_S
    while time.perf_counter() < deadline:
        time.sleep(0.5)
        p = status()
        if p is not None:
            back = p
            break
    if back is None:
        print("  FAIL: the device did not come back within %.0f s"
              % REBOOT_WAIT_S)
        out["reboot_before_gate"] = {"ok": False, "why": "did not come back"}
        _save(out, args.out)
        return 2
    # Let auto-arm actually happen: the build arms itself in well under a
    # second, but reading the instant it answers HTTP can catch it before.
    time.sleep(3.0)
    page = status() or back
    out["reboot_before_gate"] = {"ok": True}
    print("  back up: mode %s, live %s, block %r"
          % (page.get("mode"), page.get("inhibit_live"),
             page.get("arm_block")))

    #
    # THE ARM STATE IS ASSERTED, not inferred from the run going well. The
    # authoritative marker is 0x7F1 diag_flags bit3 (autoarm_build) and the
    # project_name in the image, both checked by diag_build_page_check.py
    # --expect-autoarm; this is the HTTP-side gate so that a shipping build
    # cannot get as far as phase gate and produce a confusing failure that
    # looks like the gate misbehaving.
    #
    mode_now = page.get("mode")
    looks_autoarm = mode_now not in (None, 0)
    if looks_autoarm != bool(args.expect_autoarm):
        print("\nARM STATE MISMATCH: --expect-autoarm %d, but the device came "
              "up in mode %r." % (args.expect_autoarm, mode_now))
        print("  An auto-arm build is armed with nobody having armed it; a "
              "shipping build sits in mode 0 until asked.")
        print("  Run diag_build_page_check.py --expect-autoarm %d for the "
              "authoritative answer (0x7F1 bit3)." % args.expect_autoarm)
        out["arm_state_mismatch"] = {"expected": args.expect_autoarm,
                                     "mode": mode_now}
        _save(out, args.out)
        return 1

    ok = True
    if args.phase in ("all", "gate"):
        ok = phase_gate(out) and ok
        if not ok:
            print("\nstopping: without an auto-arm build the rest measures "
                  "nothing")
            _save(out, args.out)
            return 1

    if args.phase in ("all", "live", "boot", "reset-engine"):
        frames = load_window(GATE_WINDOW_START, GATE_WINDOW_S)
        print("\nreplay window %.1f-%.1f s: %d frames"
              % (GATE_WINDOW_START, GATE_WINDOW_START + GATE_WINDOW_S,
                 len(frames)))
        tx, kv = open_buses()
        try:
            if args.phase in ("all", "live"):
                ok = phase_live(out, tx, kv, frames) and ok
            if args.phase in ("all", "boot"):
                ok = phase_boot(out, tx, kv, frames) and ok
            # Gap 4 runs LAST of the replay phases: it reboots the device into
            # a forced engine-running stimulus, so it leaves the device in a
            # different state from the one `live` and `boot` expect to find.
            if args.phase in ("all", "reset-engine"):
                ok = phase_reset_engine_running(out, tx, kv, frames) and ok
        finally:
            for b, n in ((tx, "ixxat"), (kv, "kvaser")):
                try:
                    b.shutdown()
                except Exception as e:
                    print("  warning: closing %s raised %s" % (n, e))

    _save(out, args.out)
    print("\nREMINDER: this device is running an AUTO-ARM build, which "
          "transmits on boot with no human in the loop. Reflash the shipping "
          "image before leaving the bench.")
    return 0 if ok else 1


def _save(out, path):
    """MERGE into the named file, and also write one immutable file per phase.

    THE BUG THIS FIXES COST THE 2026-09-26 RESULT (found by the reviewing
    session, 2026-10-04). `--out` defaulted to this one path for EVERY
    `--phase`, and this function opened it `"w"`. So the all-phases run wrote
    gate + live + boot, and the later `--phase boot` invocation overwrote the
    file with a boot key alone. The gate and live pass -- "live 0.30 s after
    traffic started, 209 frames against tx_ok 209" -- survived only as prose in
    two notes files, with no run record and no build recorded. Nothing in the
    output said anything had been lost.

    Two changes, because they protect against different things:

      MERGE, so a single-phase re-run adds to the record instead of replacing
      it. Read, update, write. A phase re-run still overwrites ITS OWN key,
      which is wanted -- the newest attempt at a phase is the one that counts.

      ONE FILE PER PHASE, named with the timestamp, which no later run can
      touch at all. The merge above is still a read-modify-write and will lose
      data if two runs interleave or a merge is interrupted mid-write; these
      are the copies that cannot be lost that way.
    """
    merged = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                merged = json.load(f)
        except (ValueError, OSError) as e:
            # Do NOT silently start a fresh dict: that is the same data loss
            # by another route. Keep the old file under a .corrupt name so
            # whatever was in it is still recoverable.
            bad = path + ".corrupt"
            print("\n  WARNING: %s could not be read (%s); preserving it as %s"
                  % (path, e, os.path.basename(bad)))
            try:
                os.replace(path, bad)
            except OSError:
                pass
            merged = {}

    #
    # MERGE PHASES ONLY, AND STAMP EACH WITH THE RUN THAT PRODUCED IT.
    #
    # The first version of this did `merged.update(out)`, which fixed the data
    # LOSS and introduced data CONTAMINATION in its place -- arguably worse,
    # because a lost phase is visibly absent while a stale one is not. Caught
    # by the reviewing session: a failed run had written the top-level key
    # `arm_state_mismatch {expected: 1, mode: 0}`, and a later successful run
    # carried it forward under its OWN "when" and "device". Anyone reading the
    # merged file saw a mismatch that no current run had, attributed to the
    # current run.
    #
    # So: top-level metadata is ALWAYS the current run's and nothing else
    # survives from a previous one, while each phase keeps the when/device of
    # the run that actually produced it. A phase re-run replaces its own entry.
    # The per-phase files remain the authoritative record; this file is a
    # convenience view and now says so.
    #
    PHASE_KEYS = ("gate", "live", "boot", "reset_engine_running")
    phases = [k for k in PHASE_KEYS if k in out]
    stamp_now = {"when": out.get("when"), "device": out.get("device"),
                 "expect_autoarm": out.get("expect_autoarm")}

    kept = {}
    for k in PHASE_KEYS:
        if k in out:
            kept[k] = dict(out[k])
            kept[k]["_run"] = stamp_now
        elif k in merged and isinstance(merged.get(k), dict):
            prev = dict(merged[k])
            prev.setdefault("_run", merged.get("_run_legacy")
                            or {"when": merged.get("when"),
                                "device": merged.get("device")})
            kept[k] = prev

    doc = dict((k, v) for k, v in out.items() if k not in PHASE_KEYS)
    doc["_note"] = ("Convenience view. Each phase carries the _run that "
                    "produced it; top-level fields are the LAST run's only. "
                    "The per-phase autoarm_<phase>_<stamp>.json files are the "
                    "record -- nothing can rewrite those.")
    doc.update(kept)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1)
    runs_in = sorted({(v.get("_run") or {}).get("when")
                      for v in kept.values()} - {None})
    print("\nwrote %s (phases: %s; from %d run(s): %s)"
          % (path, ", ".join(k for k in PHASE_KEYS if k in kept) or "none",
             len(runs_in), ", ".join(runs_in) or "-"))

    stamp = out.get("when", time.strftime("%Y-%m-%dT%H:%M:%S"))
    stamp = stamp.replace(":", "").replace("-", "")
    for ph in phases:
        one = dict((k, v) for k, v in out.items() if k not in
                   ("gate", "live", "boot", "reset_engine_running"))
        one[ph] = out[ph]
        p = os.path.join(os.path.dirname(path),
                         "autoarm_%s_%s.json" % (ph, stamp))
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(one, f, indent=1)
        print("  per-phase record: %s" % os.path.basename(p))


if __name__ == "__main__":
    sys.exit(main())
