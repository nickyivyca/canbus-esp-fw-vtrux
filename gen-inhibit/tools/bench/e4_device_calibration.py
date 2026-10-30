#!/usr/bin/env python3
"""E4, the device half: a real full-bus truck replay into the WiCAN itself.

    py -3.11 e4_device_calibration.py                     # idle and loaded arms
    py -3.11 e4_device_calibration.py --seconds 30 --arm idle

Spec 12.4's `full-replay bus` row: "no dropped 0x051 answer and no inhibit frame
after the VCM's next, idle and under WiFi load, diag active". Spec 5 states why
it is needed -- every timing number on the device was taken on a near-idle bus
carrying a VCM-only 0x051 stream at ~100 frames/s, and the truck's powertrain bus
is an order of magnitude busier. The rig half (`e4_rig_calibration.py`) showed
this bench can emit that traffic faithfully; this drives the device with it.

THE WINDOW IS CHOSEN BY B0, NOT BY FRAME RATE, and that is the whole reason this
file names its own window rather than reusing the rig calibration's.
`truck_passive2.log` holds two epochs:

    0-90 s     ~2350 frames/s, 0x051 B0 = 0x0B throughout -- THE VCM IS
               REQUESTING THE GENERATOR. Spec 7's arm gate blocks on exactly
               that ("VCM requesting engine"), so the device can never go live
               here and every counter below would read zero. A first attempt
               used 20-50 s for its frame rate and would have measured nothing.
    100-240 s  ~1680 frames/s, B0 = 0x08 throughout -- engine off, which is the
               ONLY condition in which the inhibitor is ever live.

So ~1680 frames/s is the loaded bus this device actually faces, and spec 9.3's
~2250 figure is the generator-RUNNING rate. E4 models the busier of the two,
which is the conservative direction, and this run is what says so.

WHAT IS MEASURED, and why each is here rather than being inferred:

  dropped answers   replayed 0x051 count against the device's own tx_ok. A
                    dropped receive is an unanswered frame, and spec 5 calls it
                    out as the hazard that gets attributed to anything else.
  tx_queued_behind  must be 0 while live (spec 5.1 item 4). E4 measured 2 before
                    diag was scheduled around the inhibit.
  ctr_bad           must be 0. A missed counter step is a real defect where a
                    late frame is not (spec 12.1).
  response tail     the device's own RX-to-completion histogram at truck rate.
                    This is the quantity `WORST_PREEMPT_US = 2390` in
                    load_margin.py stands for, and it was measured at ~100
                    frames/s on an idle bus.
  error frames      two nodes transmitting 0x051 at overlapping times is the one
                    thing that can walk a real VCM to bus-off. Trail timing is
                    what avoids it and the witness is what proves it.

COUNT WITH THE KVASER. The rig calibration established that the IXXAT
timestamps correctly but loses frames under sustained load -- 300/300 in short
bursts, 4,860 of 70,936 in a 30 s arm -- while the Kvaser counted 70,936 of
70,936 at every rate. So the Kvaser is the witness here and the IXXAT is not
opened: a witness that silently drops frames would read as the device failing to
answer, which is precisely the finding this run exists to make.
"""

import argparse
import collections
import json
import io
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import can
import gzip

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from e4_rig_calibration import (          # noqa: E402
    BITRATE, CAPTURE, FOREIGN_IDS, Witness, pct,
)
import raw_http_probe as RHP              # noqa: E402
import subprocess                         # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HOST = "192.168.80.1"

# FROM THE START OF THE CAPTURE, and the reason is the only place the two
# conditions overlap.
#
# The armable condition -- key on, contactors closed, engine off -- and a loaded
# bus coincide for about five seconds in this capture and nowhere else: the first
# ~5 s is engine-off with HV up at ~2350 frames/s, then the VCM starts requesting
# the generator. The engine-off epoch later on (100-240 s) is the vehicle PARKED,
# key off with the BMS and shift modules silent, so the gate cannot pass there.
#
# So the window starts at 0, the device arms inside those first seconds, and the
# replay then runs on THROUGH the generator request. That is not a compromise --
# it is the more valuable test. A live device meeting a real crank request must
# hold B0 at 0x08 (spec 4.1) and keep transmitting, which is what
# wican_crank_request_bench.py proves at 100 frames/s and this does at truck
# rate. One continuous window also keeps the VCM's rolling counter intact; a
# looped or stitched stimulus would break it and the device would report ctr_bad
# for the harness's fault rather than its own.
WINDOW_START = 0.0

# How long the start of the window must be free of generator requests for the
# arm gate to pass. The gate needs the interlocks fresh, and the measured
# bus-up-to-live figure on this bench is a few hundred milliseconds.
ARM_HEADROOM_S = 3.0

CMD_ID = 0x051
PROBE_ID = 0x7F0              # RESPOND's probe, spec 10

# The two gen_inhibit modes this file drives. main() computed these as a bare
# mode_num; they are named here because run_arm has to know WHICH FRAMES tx_ok
# is counting, and a bare 2 at that comparison is how the modes got conflated.
MODE_RESPOND = 2
MODE_INHIBIT = 3
# PASSIVE (spec 3.1, added here 2026-10-10): INHIBIT's decision path, never a 0x051 from the
# device; tx_ok is not where it counts -- the would-transmit count is (diag_would_tx).
MODE_PASSIVE = 4

# ---- the saturation filler (spec 5.2 item 10 case 3) ----------------------
#
# 0x7E0: above every standard ID in the replay (max 0x6A0, counted from the
# capture) so it loses to all truck traffic and to 0x051, and below 0x7F0-0x7F8 so
# it BEATS our probe and diag pages -- which is required, because the criterion is
# that telemetry is delayed and counted.
FLOOD_ID = 0x7E0
# One 8-byte standard frame at 500 kbit/s: 47 bits of framing + 64 of payload + 3
# of intermission is 114 bits, ~228 us, and stuffing can push it to ~270 us. The
# filler is paced no faster than this so the adapter's queue holds about one, which
# bounds the delay it can impose on a replay frame to one frame time.
#
# PACED FROM THE MEASURED CAPACITY, and it took two corrections to get here.
#
# 270 us (to 2026-09-28): the adapter REFUSED 4242 frames, and 840 of that window's
# 8998 commands never reached the wire. Loud, and caught by comparing the offered
# rate with the carried one.
#
# 300 us (2026-09-29): refusals went to zero and the loss did NOT -- 384 of 8998
# commands (4.27 %) still never appeared on the bus, silently, because the adapter
# accepts a frame into its queue and then cannot fit it onto a wire already at
# 97.6 %. `refused 0` is not evidence the stimulus arrived; only the witness
# counted against the source shows it, and there is now a VOID check for exactly
# that.
#
# 550 us (2026-09-29): the stimulus came out PERFECT -- 8998 of 8998 commands on
# the wire, attribution exact at 8983 both sides, ctr_bad 0 -- and the bus was no
# longer saturated: 80.4 % carried. The filler managed only ~1237/s against the
# ~1818/s the interval allows, because THE GUARD BELOW IS THE BINDING CONSTRAINT,
# not this interval: the replay's mean gap at 2357 frames/s is ~424 us, so a
# FLOOD_GUARD_S of 400 us rejects most gaps outright. Raising this interval past
# roughly the replay's own gap therefore buys nothing and costs utilisation.
#
# 400 us (current): between the two measured points. 300 gave 2194/s of filler and
# 97.6 % carried with 384 commands lost; 550 gave 1237/s and 80.4 % with none lost.
# The target is a COMPLETE stimulus first and ~93-96 % carried second -- a degraded
# stimulus cannot support a verdict at all, whereas a few points of utilisation
# only weakens one. Whatever this lands on, the two checks that matter now report
# themselves: the VOID rule refuses a verdict if any command failed to reach the
# wire, and `witness_vs_source.py` prints the comparison.
#
FLOOD_WIRE_S = 0.00040
# Do not start a filler unless this much of the gap remains, so a filler cannot be
# queued immediately before a replay frame falls due.
FLOOD_GUARD_S = 0.00040

# ---- the separate-adapter filler (see SlcanFiller) -------------------------
#
# Offered rate for the filler when it comes from its own controller. This is NOT
# the careful wire-time pacing above and does not need to be: arbitration places
# these frames, so over-offering costs the replay nothing and the excess is simply
# dropped by the board's transmit queue, which reports it.
#
# ~380 us is ~2630 frames/s offered. The wire carries ~4450 frames/s here and the
# replay plus the device's answers take ~2453, so this deliberately offers MORE
# filler than the ~2000/s of headroom: the point is to leave no gap unfilled and
# let arbitration decide, rather than to hit a number.
SLCAN_FILLER_WIRE_S = 0.00038


# The offset the setter is given (spec 11: POST /gen_inhibit_set?mode=N&offset=N).
# It was a literal 500 in both calls until 2026-10-10, when the RESPOND offset sweep
# (reviewer) needed it varied; --offset-us sets it and the run record carries it.
OFFSET_US = 500
# --poll-hz: light polling of the status page during an idle arm (0 = none).
POLL_HZ = 0.0
# --arm-after: POST the mode this many seconds into the replay instead of before it
# (mode entry into live traffic, as on the truck; reviewer 2026-10-10). None = before.
ARM_AFTER_S = None
# --post-schedule "10:1,20:3": POST these modes at these times into the replay, after
# the arm's own initial mode (spec 8's quiesce under traffic; reviewer 2026-10-10).
POST_SCHEDULE = []
# --post-raw-timeout S: the scheduled POSTs go over a raw socket (raw_http_probe.py)
# with this timeout, recording every byte and how the connection ended, instead of
# through urllib (reviewer, 2026-10-14: two INHIBIT POSTs from OBSERVE on 10-12
# died on an exception urllib raised, and its type was lost). None = urllib as before.
POST_RAW_TIMEOUT = None
# --post-chain: a scheduled POST waits for the previous one to finish before it
# starts, so "20:0,20:3" is OFF then INHIBIT with the OFF dwell as short as the
# replies allow (reviewer, 2026-10-14). Without it, posters run concurrently.
POST_CHAIN = False


HTTP_PORT = 80          # the device's; a test points HOST/HTTP_PORT at a fake server


def parse_netstat(txt, host):
    """Rows of `netstat -n -p TCP` whose remote end is host, as [local port, remote, state]."""
    rows = []
    for ln in txt.splitlines():
        f = ln.split()
        if len(f) >= 4 and f[0] == "TCP" and f[2].rsplit(":", 1)[0] == host:
            rows.append([f[1].rsplit(":", 1)[-1], f[2], f[3]])
    return rows


def device_sockets():
    """The host's TCP connections to the device, from netstat.

    The host's view only: a socket the device holds after the host forgot it does
    not show here, which is why the batch also runs a packet capture."""
    try:
        txt = subprocess.run(["netstat", "-n", "-p", "TCP"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return "netstat failed: %s" % e
    return parse_netstat(txt, HOST)


def schedule_due(sched, si, offset_s):
    """Whether scheduled POST si starts at this replay offset: its time has come
    and, with --post-chain, the previous POST has finished."""
    return (si < len(sched) and offset_s >= sched[si]["at_s"]
            and not (POST_CHAIN and si > 0 and sched[si - 1]["poster"].is_alive()))


def reply_failed(rec):
    """True when a raw reply is not a clean, complete 200 with a JSON body."""
    if rec is None:
        return True
    st = rec.get("status_line") or ""
    return (rec.get("ending") not in ("fin", "complete_open")
            or (rec.get("http_client") or {}).get("result") != "ok"
            or " 200 " not in st + " "
            or rec.get("cl_matches") is False
            or rec.get("body_json_ok") is not True)


def parse_schedule(text):
    """'10:1,20:3' -> [(10.0, 1), (20.0, 3)], in time order; modes 0-4 only."""
    out = []
    for part in (text or "").split(","):
        if not part.strip():
            continue
        t, m = part.split(":")
        m = int(m)
        if m not in (0, 1, 2, 3, 4):
            raise ValueError("mode %d is not 0-4" % m)
        out.append((float(t), m))
    return sorted(out)


def set_mode_path(mode, offset_us):
    """The setter path for a mode and offset."""
    return "/gen_inhibit_set?mode=%d&offset=%d" % (int(mode), int(offset_us))


def vcm_commands_on_wire(mode, cmd_on_wire, tx_ok):
    """How many of the 0x051 frames on the wire were the VCM's (the replayed stimulus).

    In INHIBIT each of the device's tx_ok is a 0x051 answer, so the VCM's are the
    rest. In RESPOND the device transmits only 0x7F0 probes and never a 0x051, so
    every 0x051 on the wire is the VCM's. Subtracting tx_ok there (the stimulus
    check as written 2026-09-29) voided a good RESPOND arm on 2026-10-10 with
    "2993 of 2999 commands never reached the wire" while the witness had all 2999:
    the same mode conflation the attribution split fixed on 2026-09-28."""
    # Only INHIBIT puts the device's own 0x051 on the wire (PASSIVE counts would-transmits).
    return cmd_on_wire - ((tx_ok or 0) if mode == MODE_INHIBIT else 0)


def attribution_check(mode, ours_on_wire, tx_ok, counted_as):
    """The witness's count of our frames against the device's tx_ok: (line, void reason
    or None).

    In INHIBIT `ours` comes from the old trail-window classifier, which provenance
    replaced as the live classifier on 2026-10-10 (reviewer, tracker row "E4 answer
    classifier", its figures kept, superseded, "for one cycle"). Since 2026-10-29
    (gen-inhibit reviewer, from step 1a's acceptance, where it voided 2 of 6 B22 arms
    on 8982 against 8983 while provenance found 8983 of 8983) its count is printed,
    labelled, and voids nothing: the provenance check decides. In RESPOND the count is
    of the 0x7F0 probes, by id, not the trail classifier, and a disagreement still voids
    the arm."""
    if ours_on_wire == tx_ok:
        return ("  wire agrees with the device: %d transmitted, %d seen (%s)"
                % (tx_ok, ours_on_wire, counted_as)), None
    if mode != MODE_RESPOND:
        return ("  superseded trail-window count (voids nothing; provenance decides): the "
                "device reports tx_ok=%d, the old classifier attributes %d %s frames to us"
                % (tx_ok, ours_on_wire, counted_as)), None
    #
    # The two instruments disagreeing means the attribution is unsound, and every
    # criterion below is computed from that attribution. Reporting them anyway is
    # reporting the classifier's opinion as the device's behaviour.
    #
    return ("  MISMATCH: the device reports tx_ok=%d but the witness attributes %d %s "
            "frames to us. One of the two is wrong -- the filter, or a frame that never "
            "reached the wire." % (tx_ok, ours_on_wire, counted_as),
            "attribution disagrees with the device: tx_ok=%d, witness attributes %d"
            % (tx_ok, ours_on_wire))


def bus_utilisation(frames, bitrate=BITRATE):
    """Carried load on the wire, as a LOWER BOUND, from the witness's own frames.

    Bits per frame are reconstructed from the ID width and DLC: 47 bits of framing
    for an 11-bit identifier (or 67 for 29-bit), 8 per data byte, plus 3 bits of
    intermission. STUFF BITS ARE NOT RECOVERABLE from a logged frame, so the true
    figure is higher -- which is why this is reported as a bound and not as "the"
    utilisation. Returns (fraction, longest idle gap in seconds).
    """
    if len(frames) < 2:
        return 0.0, 0.0
    bits = 0
    for f in frames:
        dlc = len(f[2]) if f[2] is not None else 0
        bits += (67 if f[4] else 47) + 8 * dlc + 3
    span = frames[-1][0] - frames[0][0]
    if span <= 0:
        return 0.0, 0.0
    worst = 0.0
    for i in range(len(frames) - 1):
        g = frames[i + 1][0] - frames[i][0]
        if g > worst:
            worst = g
    return bits / (span * float(bitrate)), worst

# The busiest sustained stretch of the capture. Usable only by RESPOND, which
# does not go through the arm gate, so B0 is irrelevant here.
RESPOND_WINDOW_START = 20.0
# A 0x051 closer than this to its predecessor is ours, not the VCM's. The VCM
# runs at ~10 ms and our trail offset is 500 us, so anything up to a few ms is
# unambiguous; 3 ms leaves room for scheduling delay without reaching half the
# VCM's period.
TRAIL_MAX_US = 3000.0

ZERO_TORQUE = 0x8000          # raw B1B2 little-endian: bytes 00 80.
# WAS 0x0080, WHICH MATCHED NOTHING. The firmware writes data[1]=0x00,
# data[2]=0x80 and its own comment reads "raw 0x8000, 16-bit LE, offset
# -32768" (main/gen_inhibit.c). 0x0080 is what the bytes SPELL read
# left to right, which is the big-endian value, so every device frame was
# classified as the VCM's: ours_zero_torque read 0 in all three arms of
# 2026-09-26 while the device reported tx_ok 670/670/2999, and the
# INHIBIT arms' wire checks silently never ran. Caught by the reviewer
# noticing cmd_on_wire 4170 = vcm_cmd 4170 with tx_ok 670 -- the totals
# disagreed with the split, which is the orthogonal quantity.


def gi(path, method="GET", timeout=6.0):
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


def device_identity():
    """(elf_sha256, None) for the running image, or (None, why): one GET of /gen_inhibit,
    whose elf_sha256 names the image file (spec 11, "The page names the binary it runs").

    NEVER /check_status (reviewer, 2026-10-20): it answers with the device's config,
    credentials included, and B11raw's Wi-Fi capture of port 80 kept that answer in the
    clear. Until 2026-10-25 this file read git_version, fw_version and wifi_mode from it
    as its first request.
    """
    st, txt = gi("/gen_inhibit")
    if st != 200:
        return None, "HTTP %s: %s" % (st, txt[:120])
    try:
        page = json.loads(txt)
    except ValueError:
        return None, "HTTP 200 but the page does not parse (%d bytes)" % len(txt)
    sha = page.get("elf_sha256") if isinstance(page, dict) else None
    if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
        return None, "the page names no elf_sha256 (%r)" % (str(sha)[:70],)
    return sha, None


def status(timeout=6.0, tries=1):
    """The status page, or None.

    RETRIES EXIST BECAUSE LOSING THIS READ VOIDS A 90 s ARM. Two consecutive
    92.6 %-load arms on 2026-09-29 ended with no device measurement at all, while
    the same page answered 1545 times in 40 s with the bus saturated by the filler
    alone and nothing else running (http_under_load.py) -- so the device's HTTP
    path is not what fails. What differs in an arm is the HOST: the replay thread
    and the witness are still holding the GIL when this read is issued, and a 6 s
    timeout can expire in host time without the device ever being slow.

    The end-of-arm read is not timing-critical -- it happens after the wire has
    been measured quiet -- so waiting longer for it costs nothing and saves the
    arm. Kept at one 6 s try by default so no existing caller's timing changes.
    """
    for i in range(max(1, tries)):
        st, txt = gi("/gen_inhibit", timeout=timeout)
        if st == 200:
            try:
                return json.loads(txt)
            except ValueError:
                #
                # 200 WITH A BODY THAT WILL NOT PARSE IS ITS OWN FAILURE, and
                # returning None for it made a truncated page indistinguishable
                # from an unreachable device -- which cost two 90 s arms on
                # 2026-09-29 before the retry logic showed no retries had run.
                # The length is the diagnosis: the firmware's page buffer is
                # GI_STATUS_PAGE_CAP, and a body at that length is a cut page,
                # not a transport fault.
                #
                print("  status: HTTP 200 but %d bytes of UNPARSEABLE body "
                      "(tail: %r)" % (len(txt), txt[-60:]))
                _p = gi_paths.runs("unparseable_status.txt")
                try:
                    io.open(_p, "w", encoding="utf-8").write(txt)
                    print("  saved to %s" % _p)
                except OSError:
                    pass
                return None
        if i + 1 < tries:
            print("  status read failed (%s); retrying" % st)
            time.sleep(1.0)
    return None


def load_window(seconds):
    """Offsets, ids, payloads and the extended flag, from the capture."""
    out = []
    t0 = None
    for m in can.LogReader(CAPTURE):
        if t0 is None:
            t0 = m.timestamp
        off = m.timestamp - t0
        if off < WINDOW_START:
            continue
        if off >= WINDOW_START + seconds:
            break
        out.append((off - WINDOW_START, m.arbitration_id, bytes(m.data),
                    bool(m.is_extended_id)))
    return out


class SlcanFiller(threading.Thread):
    """Saturation filler from a SECOND adapter, where arbitration does the work.

    WHY A SEPARATE ADAPTER CHANGES THE PROBLEM. Frames queued in one adapter do not
    arbitrate against each other -- they leave in queue order -- so filler queued
    ahead of a 0x051 delays that 0x051 no matter how low its CAN priority is. That
    is the entire reason the single-adapter path below has to pace by wire time,
    and pacing was still not enough: at 97.6 % carried the PCAN accepted every
    frame and 384 of 8998 commands never reached the wire (2026-09-29).

    From its own controller, 0x7E0 loses arbitration to every truck id and to
    0x051 and wins against the diag pages on 0x7F1-0x7F8. So:

      * it can never delay a command -- arbitration places it, as on the truck;
      * OVER-OFFERING IS NOW HARMLESS. Excess frames lose arbitration or are
        dropped by the board's own transmit queue, and neither touches the replay.
        So this thread does not need the careful pacing the inline filler needs,
        and runs in a thread precisely so it cannot interfere with the replay's
        busy-wait schedule on the main thread.

    It is still paced, loosely, for one reason only: an unpaced Python loop writing
    to a CDC port spends the CPU the replay's `while perf() < due` needs.
    """

    def __init__(self, bus, wire_s):
        threading.Thread.__init__(self, daemon=True)
        self.bus = bus
        self.wire_s = wire_s
        self.msg = can.Message(arbitration_id=FLOOD_ID,
                               data=bytes([0xAA] * 8), is_extended_id=False)
        #
        # WRITTEN AS RAW SLCAN, IN BATCHES, rather than through bus.send().
        # python-can's slcan send() does one serial write PLUS A FLUSH per frame,
        # which measured ~1.5 ms on this host: the first separate-adapter arm
        # offered only 675 frames/s against the 2622/s the pacing allowed, and the
        # bus came out at 66.7 % carried. Neither the board nor the wire was the
        # limit -- the host's serial path was, and a filler that cannot fill is
        # indistinguishable in the result from a bus that is already full.
        #
        # One write of N identical frames costs barely more than one frame, and the
        # firmware parses line by line so a bulk write is just several commands.
        # 32 frames is 704 bytes, comfortably inside a CDC transfer.
        #
        self.port = getattr(bus, "serialPortOrig", None)
        self.frame = (b"t%03X8" % FLOOD_ID) + (b"AA" * 8) + b"\r"
        self.chunk = 32
        self.sent = 0
        self.failed = 0
        # NAMED `stop`, MATCHING Hammer BELOW, AND NOT `_stop`: threading.Thread
        # has its own private _stop() method, and an Event attribute of that name
        # shadows it, so Thread.join() calls the Event and raises
        # "TypeError: 'Event' object is not callable". It fails in teardown, AFTER
        # the arm has run, so the cost is a whole 90 s replay thrown away.
        self.stop = threading.Event()

    # Wake this often and send a small batch, rather than spinning per frame.
    WAKE_S = 0.002

    def run(self):
        # IT SLEEPS, AND THAT IS THE WHOLE POINT. The replay on the main thread
        # paces itself with `while perf() < due: pass` -- a busy-wait that holds
        # the GIL. A filler thread that ALSO busy-waits contends with it: the two
        # then alternate on the interpreter's switch interval, which makes the
        # filler bursty and, far worse, can delay the replay. Delaying a command is
        # the exact fault this separate adapter exists to remove, so reintroducing
        # it in the host would be self-defeating.
        #
        # Measured: a per-frame spin managed ~1040 frames/s against the ~2630/s its
        # interval allowed, and that was with the main thread merely sleeping.
        #
        # So it sends a batch and then sleeps, which releases the GIL. Batching is
        # free here for the same reason over-offering is: arbitration places these
        # frames, so their spacing within a couple of milliseconds does not matter.
        # time.perf_counter is bound directly because `perf` is a local of
        # run_arm -- referencing it here would be a NameError only at run time.
        # THE BATCH IS SIZED FROM ELAPSED TIME, not from the wake interval, because
        # this thread does not get to choose when it runs. The replay's busy-wait
        # holds the GIL and releases it only on the interpreter's switch interval,
        # so a thread asking to wake every 2 ms is actually scheduled every ~5 ms.
        # A FIXED batch then silently under-delivers -- measured 1191 frames/s
        # against the 2632/s the interval allowed, which is exactly the kind of
        # shortfall that reads as "the bus was full" when it means "the host was
        # late". Owing frames against real elapsed time is self-correcting whatever
        # the scheduler does.
        clock = time.perf_counter
        # Cap a single burst at the board's transmit queue depth: more than that
        # cannot be absorbed anyway, and it bounds the catch-up after a long stall.
        cap = 64
        last = clock()
        credit = 0.0
        while not self.stop.is_set():
            now = clock()
            credit += (now - last) / self.wire_s
            last = now
            n = int(credit)
            if n > cap:
                n = cap
                credit = 0.0
            else:
                credit -= n
            while n > 0:
                k = self.chunk if n > self.chunk else n
                try:
                    if self.port is not None:
                        self.port.write(self.frame * k)
                        self.sent += k
                    else:
                        for _ in range(k):
                            self.bus.send(self.msg, timeout=0.5)
                            self.sent += 1
                except Exception:
                    # A refusal costs the measurement nothing -- the filler is not
                    # the stimulus -- so it is counted, not raised.
                    self.failed += k
                n -= k
            time.sleep(self.WAKE_S)


def slcan_report(bus):
    """Ask the filler board for its own counters.

    `?` is not SLCAN. It exists because the protocol has no way to ask "did every
    frame I handed you actually reach the wire", and the answer decides whether a
    filler shortfall is the board's queue or the bus being genuinely full.
    python-can leaves the port reachable, which is how it is sent.
    """
    try:
        port = bus.serialPortOrig
        #
        # LET IT DRAIN, THEN RESYNCHRONISE THE LINE. At filler rates the board's
        # receive buffer overflows and loses bytes mid-line, so a `?` sent straight
        # afterwards arrives glued to the tail of a truncated frame and is rejected
        # (observed: a bare 0x07 back and no counters). A bare CR closes whatever
        # partial line is sitting there; the quiet moment first is what lets the
        # backlog clear so the CR is not itself lost.
        #
        time.sleep(0.5)
        port.write(b"\r")
        port.flush()
        time.sleep(0.3)
        port.reset_input_buffer()
        port.write(b"?\r")
        port.flush()
        #
        # POLL, DO NOT SLEEP ONCE. This is read straight after the filler stops,
        # when the board may still be chewing through a backlog of queued frames --
        # measured: a single 300 ms wait returned EMPTY after ~455 kB had been
        # written, and an empty counter line is indistinguishable from a board that
        # has nothing to report. Waits up to ~4 s for the line that starts with the
        # prefix the firmware sends, and says so plainly if it never arrives.
        #
        deadline = time.time() + 4.0
        buf = b""
        while time.time() < deadline:
            n = port.in_waiting
            if n:
                buf += port.read(n)
                # A complete line is all that is needed; the firmware terminates
                # the counter line with CRLF and prefixes it with "slcan1".
                for line in buf.split(b"\n"):
                    if line.startswith(b"slcan1"):
                        return line.decode("ascii", "replace").strip()
            time.sleep(0.05)
        if buf:
            return ("no counter line in %r (board still draining?)"
                    % buf[:120].decode("ascii", "replace"))
        return "NO REPLY within 4 s -- counters not read"
    except Exception as e:
        return "unavailable (%s)" % e


class Hammer(threading.Thread):
    """An adversarial WiFi load: spec 12.1 wants it alongside the idle arm.

    It exists to bound the tail, not to represent normal operation -- nothing
    hammers this device's HTTP server in the truck.
    """

    def __init__(self, period=0.0):
        threading.Thread.__init__(self, daemon=True)
        self.stop = threading.Event()
        self.n = 0
        self.errors = 0
        # period 0 is the flood; a period > 0 is light polling (--poll-hz, 2026-10-10).
        # Either way every answered page's driver RX queue depth and loss counter are
        # kept, so a drop can be placed against the queue depth around it (reviewer).
        self.period = period
        self.series = []          # [host perf time, msgs_to_rx, rx_missed, mode, self_off]

    def run(self):
        while not self.stop.is_set():
            t0 = time.perf_counter()
            st, txt = gi("/gen_inhibit", timeout=4.0)
            if st == 200:
                self.n += 1
                self.series.append([round(t0, 4)] + page_queue_fields(txt) + page_state_fields(txt))
            else:
                self.errors += 1
            if self.period > 0:
                self.stop.wait(max(0.0, self.period - (time.perf_counter() - t0)))


def arm_steps(arm_after_s, post_raw_timeout):
    """How run_arm arms: ("now" or "deferred", whether the pre-replay page read runs).
    The two are independent. 2026-10-16: the pre-read block had been inserted between
    the arm's if and its else (2026-10-14), so every run without --post-raw-timeout
    took the deferred branch with no --arm-after and crashed after arming, and a run
    with both would never have POSTed its deferred arm (test_e4_rawpost.py)."""
    return ("now" if arm_after_s is None else "deferred"), post_raw_timeout is not None


def arm_post_fields(poster, post_index):
    """The --arm-after POST's fields in the arm record. Since 2026-10-16 they include
    its raw record (post_raw) and any follow-up GET and exception, as each scheduled
    POST's do: without post_raw a judge could only time the reply from post_s, which
    with --post-raw-timeout also waits for the connection to end (the device holds it
    open), so the arm POST read ~2 s late (mode_change_judge, 887c000)."""
    if poster is None:
        return {"post_index": None, "post_s": None, "post_http": None,
                "post_raw": None, "post_followup": None, "post_exception": None}
    return {"post_index": post_index, "post_s": [poster.t_start, poster.t_end],
            "post_http": poster.status, "post_raw": poster.raw,
            "post_followup": poster.followup, "post_exception": poster.exc}


class ModePoster(threading.Thread):
    """--arm-after: POSTs the mode from its own thread once the replay reaches
    ARM_AFTER_S, and records when it started and ended on the replay's clock (s
    from the replay's base). It shares the GIL with the replay's busy-wait, so the
    replay's timing around the POST is measured afterwards, not assumed."""

    def __init__(self, mode):
        threading.Thread.__init__(self, daemon=True)
        self.mode = mode
        self.base = None
        self.status = None
        self.t_start = None
        self.t_end = None
        self.raw = None             # raw_http_probe record (--post-raw-timeout)
        self.followup = None        # a fresh GET /gen_inhibit after a failed reply
        self.sockets_before = None
        self.sockets_after = None
        self.exc = None             # an exception this thread raised, as text

    def run(self):
        try:
            if POST_RAW_TIMEOUT is None:
                self.t_start = round(time.perf_counter() - self.base, 4)
                self.status, _ = gi(set_mode_path(self.mode, OFFSET_US), method="POST")
                self.t_end = round(time.perf_counter() - self.base, 4)
                return
            self.sockets_before = device_sockets()
            self.t_start = round(time.perf_counter() - self.base, 4)
            self.raw = RHP.raw_request(HOST, set_mode_path(self.mode, OFFSET_US),
                                       timeout=POST_RAW_TIMEOUT, method="POST",
                                       port=HTTP_PORT, base=self.base)
            self.t_end = self.raw["t_end"]
            st = (self.raw.get("status_line") or "").split()
            self.status = int(st[1]) if len(st) > 1 and st[1].isdigit() else None
            if reply_failed(self.raw):
                # (c), reviewer 2026-10-14: is the server still answering, and when?
                self.followup = RHP.raw_request(HOST, "/gen_inhibit", timeout=POST_RAW_TIMEOUT,
                                                method="GET", port=HTTP_PORT, base=self.base)
            self.sockets_after = device_sockets()
        except Exception as e:
            # RECORDED, NOT RAISED: on 2026-10-12 this thread died on an exception
            # whose type only the console saw, and the batch log's grep dropped it.
            import traceback
            self.exc = traceback.format_exc()
            print("  POSTER EXCEPTION (recorded): %s" % self.exc.strip().splitlines()[-1])


def page_state_fields(txt):
    """[mode, self_off] from a status page body (the quiesce reason shows in self_off)."""
    try:
        d, _ = json.JSONDecoder().raw_decode(txt.lstrip())
        return [d.get("mode"), d.get("self_off")]
    except Exception:
        return [None, None]


def page_queue_fields(txt):
    """[msgs_to_rx, rx_missed] from a status page body; None for what is absent."""
    try:
        d, _ = json.JSONDecoder().raw_decode(txt.lstrip())
        drv = d.get("drv") or {}
        return [drv.get("msgs_to_rx"), drv.get("rx_missed")]
    except Exception:
        return [None, None]


def wait_for_wire_quiet(w, cmd_id=CMD_ID, quiet_s=0.2, cap_s=6.0,
                        clock=None, sleep=None):
    """Block until no 0x051 has been witnessed for `quiet_s`, or `cap_s` elapses.

    Returns (quiet, last_frame_ts, last_frame_host, t_started).

    WHY THIS EXISTS. The device's counters used to be read after a fixed 0.5 s sleep,
    which asserted the wire was quiet without checking. `tx.send()` returns at
    QUEUEING, so a backlogged adapter can still be delivering the replay when the
    loop ends -- measured on two arms, whose last command reached the wire ~0.5 s
    late and which each read one answer short of what the witness saw.

    QUIET IS MEASURED ON THE HOST CLOCK. An earlier version compared the newest
    witnessed frame's timestamp against the newest 0x051's, which cannot detect quiet
    at all: on a silent bus nothing new is appended, the difference stops growing, and
    the wait runs to its cap and reports possible mid-traffic on an idle wire. Its
    failure looked like a warning rather than like a pass. The host clock keeps
    running when the bus does not.

    `clock` and `sleep` are injectable so this is testable without hardware; the two
    cases that matter are a bus that falls silent and a bus that does not.
    """
    clock = clock or time.perf_counter
    sleep = sleep or time.sleep

    t0 = clock()
    quiet = False
    last_cmd_ts = None
    last_cmd_host = t0
    last_any_ts = None
    last_any_host = t0

    while clock() - t0 < cap_s:
        # Scan only the tail: w.frames reaches several hundred thousand entries.
        snap = w.frames
        if snap:
            if snap[-1][0] != last_any_ts:
                last_any_ts = snap[-1][0]
                last_any_host = clock()
            seen = None
            for e in reversed(snap[-4000:]):
                if e[1] == cmd_id:
                    seen = e[0]
                    break
            if seen is not None and seen != last_cmd_ts:
                last_cmd_ts = seen
                last_cmd_host = clock()
        if last_cmd_ts is not None and (clock() - last_cmd_host) >= quiet_s:
            quiet = True
            break
        sleep(0.05)

    return quiet, last_any_ts, last_any_host, t0


def run_arm(name, frames, hammer_on, out, mode=3, flood=False,
            flood_slcan=None):
    print("\n== arm %s ==" % name)

    # PCAN FIRST, then the Kvaser -- the 2026-09-22 open-order rule.
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    time.sleep(2.0)
    for b in (tx, kv):
        while b.recv(timeout=0.0) is not None:
            pass

    msgs = [can.Message(arbitration_id=i, is_extended_id=x, data=d)
            for _, i, d, x in frames]
    offs = [f[0] for f in frames]

    # Arm it, then confirm it actually went live BEFORE counting anything.
    poster = None
    pre = None
    arm_when, pre_read = arm_steps(ARM_AFTER_S, POST_RAW_TIMEOUT)
    if arm_when == "now":
        st, txt = gi(set_mode_path(mode, OFFSET_US), method="POST")
        print("  arm: HTTP %s (mode %d)" % (st, mode))
        pre = {"initial_post_http": st, "initial_post_body_len": len(txt or "")}
    else:
        poster = ModePoster(mode)
        print("  arm: deferred -- mode %d will be POSTed %.1f s into the replay, into live "
              "traffic" % (mode, ARM_AFTER_S))
    if pre_read:
        # Uptime and page size before the replay; the run's end read gives them after.
        pre = pre or {}
        t_pre = time.time()
        pst, ptxt = gi("/gen_inhibit", timeout=6.0)
        pre.update(epoch=t_pre, http=pst, page_len=len(ptxt or ""),
                   device_sockets=device_sockets())
        try:
            pd = json.loads(ptxt)
            pre.update(uptime_ms=pd.get("uptime_ms"), mode=pd.get("mode"),
                       inhibit_live=pd.get("inhibit_live"), reset_reason=pd.get("reset_reason"))
        except (TypeError, ValueError):
            pre.update(uptime_ms=None)
        print("  pre: HTTP %s, %d bytes, uptime_ms %s, mode %s, device sockets %s"
              % (pst, pre["page_len"], pre.get("uptime_ms"), pre.get("mode"), pre["device_sockets"]))
    w = Witness("kvaser", kv)
    w.start()

    ham = None
    if hammer_on or POLL_HZ > 0:
        ham = Hammer(period=0.0 if hammer_on else 1.0 / POLL_HZ)
        ham.start()

    perf = time.perf_counter
    base = perf()
    # The replay's clock against the Kvaser's (epoch): placing a POST against the
    # device's first frame on the wire needs both (reviewer, 2026-10-14).
    base_epoch = time.time() - (perf() - base)
    fails = 0
    #
    # THE FILLER, PACED BY WIRE TIME AND NOT BY send() RETURNING. See the module
    # note on FLOOD_ID: send() returns at QUEUEING, and filler queued ahead of a
    # replay frame delays it regardless of CAN priority, because frames from one
    # adapter do not arbitrate against each other. So at most one filler goes out
    # per FLOOD_WIRE_S, and only while the gap to the next replay frame still has
    # FLOOD_GUARD_S of room.
    #
    #
    # THE SEPARATE-ADAPTER FILLER. Opened AFTER the PCAN and Kvaser, keeping the
    # 2026-09-22 open-order rule, and it is a third transmitter on the segment --
    # so it is opened only when asked for explicitly.
    #
    sl = None
    sl_filler = None
    sl_before = None
    if flood_slcan:
        sl = can.Bus(interface="slcan", channel=flood_slcan, bitrate=BITRATE)
        time.sleep(0.5)
        sl_before = slcan_report(sl)
        print("  FLOOD ON (SEPARATE ADAPTER %s): filler id 0x%03X, offered at "
              "<= 1 per %.0f us; arbitration places it, not pacing"
              % (flood_slcan, FLOOD_ID, SLCAN_FILLER_WIRE_S * 1e6))
        print("    board before: %s" % sl_before)

    flood_msg = None
    flood_sent = 0
    flood_fail = 0
    next_flood = 0.0
    if flood:
        flood_msg = can.Message(arbitration_id=FLOOD_ID,
                                data=bytes([0xAA] * 8), is_extended_id=False)
        print("  FLOOD ON: filler id 0x%03X, paced at <= 1 per %.0f us"
              % (FLOOD_ID, FLOOD_WIRE_S * 1e6))

    # Started as late as possible -- with the replay about to begin -- so the bus
    # is not being filled during the arm's settle and drain.
    if sl is not None:
        sl_filler = SlcanFiller(sl, SLCAN_FILLER_WIRE_S)
        sl_filler.start()

    post_index = None
    sched = [{"at_s": t, "mode": m, "poster": ModePoster(m), "index": None}
             for t, m in POST_SCHEDULE]
    si = 0
    for k in range(len(msgs)):
        due = base + offs[k]
        if schedule_due(sched, si, offs[k]):
            sched[si]["index"] = k
            sched[si]["poster"].base = base
            sched[si]["poster"].start()
            si += 1
        if poster is not None and post_index is None and offs[k] >= ARM_AFTER_S:
            # Frames 0..k-1 have been handed to the adapter; the POST goes out now,
            # from its own thread so the replay keeps its schedule.
            post_index = k
            poster.base = base
            poster.start()
        if flood_msg is not None:
            while True:
                now = perf()
                if (due - now) <= FLOOD_GUARD_S:
                    break
                if now >= next_flood:
                    try:
                        tx.send(flood_msg, timeout=0.5)
                        flood_sent += 1
                    except can.CanError:
                        flood_fail += 1
                        break
                    next_flood = now + FLOOD_WIRE_S
        while perf() < due:
            pass
        try:
            tx.send(msgs[k], timeout=0.5)
        except can.CanError:
            fails += 1
    elapsed = perf() - base
    for e in sched:
        if e["index"] is not None:
            e["poster"].join(timeout=10.0 if POST_RAW_TIMEOUT is None
                             else 2 * POST_RAW_TIMEOUT + 15.0)
            print("  schedule: mode %d POSTed at replay frame %d, %.3f-%.3f s, HTTP %s"
                  % (e["mode"], e["index"], e["poster"].t_start or -1, e["poster"].t_end or -1,
                     e["poster"].status))
            if e["poster"].raw is not None:
                r = e["poster"].raw
                print("    raw: ending %s, first byte %s, complete %s, %d bytes, CL %s, json %s, "
                      "http.client %s%s"
                      % (r["ending"], r["t_first_byte"], r["t_complete"], r["raw_len"],
                         r["content_length"], r["body_json_ok"], r["http_client"]["result"],
                         "  REPLY FAILED" if reply_failed(r) else ""))
                if e["poster"].followup is not None:
                    f = e["poster"].followup
                    print("    follow-up GET: ending %s, first byte %s, %d bytes, http.client %s"
                          % (f["ending"], f["t_first_byte"], f["raw_len"], f["http_client"]["result"]))
    if poster is not None:
        poster.join(timeout=10.0)
        print("  arm: HTTP %s (mode %d), POSTed at replay frame %s, %.3f-%.3f s into the replay"
              % (poster.status, mode, post_index, poster.t_start or -1, poster.t_end or -1))
    if flood_msg is not None:
        print("  filler queued %d frames (%d send failures)"
              % (flood_sent, flood_fail))

    sl_after = None
    if sl_filler is not None:
        sl_filler.stop.set()
        sl_filler.join(timeout=3.0)
        # The board's own account, read AFTER the thread has stopped so the
        # counters are not moving while they are read. `queue_full` here is the
        # board's transmit queue, not the bus: on a separate adapter an
        # over-offered filler is supposed to be dropped somewhere, and this says
        # where, which is the difference between a full bus and a lost stimulus.
        sl_after = slcan_report(sl)
        print("  filler (separate adapter) offered %d, write failures %d"
              % (sl_filler.sent, sl_filler.failed))
        print("    board after:  %s" % sl_after)

    #
    # WAIT FOR THE WIRE, DO NOT ASSUME IT. See the module note: a fixed sleep read
    # tx_ok while a backlogged adapter was still delivering the replay, and the
    # answer that went out afterwards made the device look like it had undercounted.
    # The witness is the only thing that knows whether the bus is finished, so ask
    # it: quiet means no 0x051 for QUIET_S. Bounded, because a wire that never goes
    # quiet must not hang the arm -- and if the bound is hit, that is recorded rather
    # than passed off as quiet.
    #
    _quiet, _last_any_ts, _last_any_host, _t_wait = wait_for_wire_quiet(w)
    if not _quiet:
        print("  NOTE: 0x051 was still arriving after the quiet wait expired; the "
              "counter read below may land mid-traffic")

    # Three tries at 20 s: see status(). An arm without this read is VOID,
    # and the wire is already measured quiet, so there is nothing to lose.
    st_end = status(timeout=20.0, tries=3)
    _read_host = perf()
    #
    # TWO DIFFERENT QUANTITIES, and conflating them is what the rename fixes.
    #
    #   last_witness_frame_before_read_s -- exactly what it says, and a LOWER bound
    #       on the read. On a quiet bus it can sit hundreds of ms before it.
    #   read_at_witness_s_est -- the read mapped into witness time through the last
    #       frame actually observed, using the host clock for the offset. An estimate,
    #       named as one.
    #
    # The comparison these exist for is "was every answer on the wire before the
    # read?", and answering it with a lower bound can make a race look impossible
    # when it was not.
    #
    _snap = w.frames
    arm_last_frame_before_read = None
    arm_read_at_est = None
    if _snap and _last_any_ts is not None:
        _t0 = _snap[0][0]
        arm_last_frame_before_read = round(_last_any_ts - _t0, 4)
        arm_read_at_est = round((_last_any_ts - _t0) + (_read_host - _last_any_host),
                                4)
    arm_quiet_before_read = _quiet
    arm_wait_s = round(_read_host - _t_wait, 3)
    if ham:
        ham.stop.set()
        ham.join(timeout=3.0)
    w.stop.set()
    w.join(timeout=3.0)

    gi(set_mode_path(0, OFFSET_US), method="POST")
    closing = [(tx, "pcan"), (kv, "kvaser")]
    if sl is not None:
        closing.append((sl, "slcan filler"))
    for b, n in closing:
        try:
            b.shutdown()
        except Exception as e:
            print("  warning: closing %s raised %s" % (n, e))

    # ---------------------------------------------------- save the evidence --
    #
    # RAW AND UNFILTERED, before any analysis touches it. A rare event -- one late
    # frame in 26,507 -- cannot be investigated from summaries, and the analysis
    # below discards our own diag pages, which are the leading suspect for
    # exactly that event.
    #
    # WHY AN ARM CAN BE VOID. Every criterion below is judged against the witness
    # log and the device's own counters, so when the evidence itself is damaged the
    # honest output is "no verdict", not a verdict with a warning above it. The
    # warning form was tried and failed in the obvious way: a run whose witness log
    # could not be written printed the full set of favourable conclusions anyway,
    # and only a stray path in the WARNING line showed the capture was gone.
    # Reasons are collected here and attached to the arm, and anything in this list
    # suppresses the verdict.
    #
    void_reasons = []

    raw_path = None
    if out.get("_raw_dir"):
        raw_path = os.path.join(out["_raw_dir"],
                                "%s_%s_witness.log.gz"
                                % (out["_raw_stem"], name))
        try:
            with gzip.open(raw_path, "wt") as fh:
                for ts, ident, data, is_err, is_ext in w.frames:
                    if is_err:
                        fh.write("(%.6f) can0 ERRORFRAME\n" % ts)
                        continue
                    hexid = ("%08X" % ident) if is_ext else ("%03X" % ident)
                    fh.write("(%.6f) can0 %s#%s\n"
                             % (ts, hexid,
                                "".join("%02X" % b for b in data)))
            print("  witness raw log -> %s (%d frames)"
                  % (os.path.basename(raw_path), len(w.frames)))
        except OSError as e:
            print("  VOID: could not write the witness log: %s" % e)
            void_reasons.append("witness log could not be written: %s" % e)
            raw_path = None
    else:
        void_reasons.append("no witness log directory was configured")

    # ------------------------------------------------------------ analysis --
    #
    # CARRIED LOAD, FROM THE WITNESS. Reported for every arm, because "saturated"
    # is a property of the wire and not of what the adapter was asked to send.
    #
    util, worst_idle = bus_utilisation([f for f in w.frames if not f[3]])
    print("  carried load >= %.1f%% of %d bit/s (lower bound: stuff bits are not "
          "recoverable); longest idle gap %.0f us"
          % (util * 100.0, BITRATE, worst_idle * 1e6))

    got = [f for f in w.frames if not f[3] and not (f[1] in FOREIGN_IDS and not f[4])]
    errf = [f for f in w.frames if f[3]]
    cmd = [f for f in got if f[1] == CMD_ID and not f[4]]

    # OURS AGAINST THE VCM'S, TOLD APART BY TRAIL TIMING. The VCM's 0x051 runs
    # at ~10 ms; ours is queued a programmed offset after the frame it answers,
    # so it arrives within a few hundred microseconds of a VCM frame and never
    # mid-gap. Content is corroboration only: zero torque does NOT identify us,
    # because the VCM commands zero torque too wherever the engine is off -- which
    # is most of the armable part of this window. Booking "zero torque" to us
    # over-counted by 581 frames on 2026-09-26.
    # BOTH CONDITIONS, because each alone over-counts and in a different
    # direction. Zero torque over-counts: the VCM commands zero torque wherever
    # the engine is off, which is most of the armable window. Trailing
    # over-counts too: the replay compresses some gaps -- rx_gap min reached
    # 2978 us against this 3000 us window -- so a VCM frame can follow another
    # VCM frame inside it. Trailing alone read 671-672 against tx_ok 670 in 6 of
    # 10 arms on 2026-09-26. Spec 4 requires both of every frame we send, so
    # their intersection is what identifies one.
    cmd.sort(key=lambda f: f[0])

    #
    # THE WINDOW COMES FROM THE DEVICE, capped by TRAIL_MAX_US. A fixed 3 ms is
    # wider than the device can possibly be: the replay compresses gaps to ~2.56 ms,
    # which is inside 3 ms, so a VCM frame can trail another VCM frame and be booked
    # as ours -- measured as attribution running 1-2 frames above tx_ok on every
    # arm. The device reports how long it actually takes, so that plus a margin is
    # the honest bound. min() also protects against a nonsense max_hold_us: one arm
    # reported 310 s, and a derived window must not inherit that.
    #
    trail_us = TRAIL_MAX_US
    _cls = ((st_end or {}).get("sched") or {}).get("cls") or {}
    _k = "probe" if mode == MODE_RESPOND else "inhibit"
    _c = _cls.get(_k) or {}
    if _c.get("max_hold_us") is not None and _c.get("max_queue_us") is not None:
        _derived = float(_c["max_hold_us"]) + float(_c["max_queue_us"]) + 300.0
        if 0.0 < _derived < trail_us:
            trail_us = _derived
    arm_trail_us = trail_us

    ours, vcm, prev, trailing_only = [], [], None, 0
    ours_idx = set()           # indices in `cmd` that were attributed to us
    for i, f in enumerate(cmd):
        trails = prev is not None and (f[0] - prev) * 1e6 <= trail_us
        zero = (len(f[2]) >= 3
                and int.from_bytes(f[2][1:3], "little") == ZERO_TORQUE)
        if trails and zero:
            ours.append(f)
            ours_idx.add(i)
        else:
            if trails:
                trailing_only += 1      # a VCM frame inside the trail window
            vcm.append(f)
            prev = f[0]
    zt = ours                       # by construction, every one is zero-torque

    arm = {
        "seconds": round(elapsed, 3),
        "queued": len(msgs) - fails,
        "refused": fails,
        "send_fps": round((len(msgs) - fails) / elapsed, 1),
        "witness_frames": len(got),
        "witness_fps": round(len(got) / elapsed, 1),
        "error_frames": len(errf),
        "cmd_on_wire": len(cmd),
        "vcm_cmd": len(vcm),
        "ours_trailing": len(ours),
        "ours_also_zero_torque": len(zt),
        "trailing_but_torqued": trailing_only,
        "hammer_requests": ham.n if ham else 0,
        "arm_after_s": ARM_AFTER_S,
        "post_schedule": [{"at_s": e["at_s"], "mode": e["mode"], "index": e["index"],
                           "post_s": [e["poster"].t_start, e["poster"].t_end],
                           "http": e["poster"].status,
                           "raw": e["poster"].raw,
                           "reply_failed": (reply_failed(e["poster"].raw)
                                            if e["poster"].raw is not None else None),
                           "followup": e["poster"].followup,
                           "sockets_before": e["poster"].sockets_before,
                           "sockets_after": e["poster"].sockets_after,
                           "exception": e["poster"].exc} for e in sched],
        "post_raw_timeout_s": POST_RAW_TIMEOUT,
        "post_chain": POST_CHAIN,
        "base_epoch": base_epoch,
        "pre": pre,
        **arm_post_fields(poster, post_index),
        "poll_hz": 0.0 if (ham is None or ham.period == 0) else round(1.0 / ham.period, 3),
        "queue_series": ham.series if ham else [],
        "hammer_errors": ham.errors if ham else 0,
        "device": st_end,
        "witness_raw": os.path.basename(raw_path) if raw_path else None,
        # WHEN the counters were read, in witness time, and whether the wire had
        # actually gone quiet first. Both exist because a status read landing
        # mid-traffic is indistinguishable, in the figures alone, from the device
        # undercounting -- which is how it was misdiagnosed.
        "last_witness_frame_before_read_s": arm_last_frame_before_read,
        "read_at_witness_s_est": arm_read_at_est,
        "wire_quiet_before_read": arm_quiet_before_read,
        "quiet_wait_s": arm_wait_s,
        # The SAME list object that is appended to below, so reasons discovered
        # during the analysis land in the JSON without being re-attached.
        "void_reasons": void_reasons,
        # CARRIED LOAD ON THE WIRE, not the adapter's offered rate -- the lesson of
        # the abort bench, where those two were not the same number. A lower bound:
        # stuff bits cannot be recovered from a logged frame. The longest idle gap
        # sits beside it because a high mean with a hole in it is not sustained.
        # These were printed but NOT persisted on the first saturation arm
        # (2026-09-28): one edit of seven silently failed to apply, so the figures
        # existed only in the run's stdout. Re-derivable from the witness log, but
        # a number a reviewer must recompute is a number they cannot check against.
        "bus_utilisation_lower_bound": round(util, 4),
        "longest_idle_gap_us": round(worst_idle * 1e6, 1),
        "flood": ({"id": "0x%03X" % FLOOD_ID, "queued": flood_sent,
                   "send_failures": flood_fail} if flood else None),
        # The separate-adapter filler, with the board's OWN counters either side.
        # Kept distinct from "flood" above because the two are not the same
        # experiment: one shares the replay's queue and one does not.
        "flood_slcan": ({"port": flood_slcan, "id": "0x%03X" % FLOOD_ID,
                         "wire_s": SLCAN_FILLER_WIRE_S,
                         "offered": sl_filler.sent if sl_filler else 0,
                         "write_failures": sl_filler.failed if sl_filler else 0,
                         "board_before": sl_before, "board_after": sl_after}
                        if flood_slcan else None),
    }
    out["arms"][name] = arm

    print("  replayed %d frames in %.2f s -> %.1f frames/s (refused %d)"
          % (arm["queued"], elapsed, arm["send_fps"], fails))
    #
    # A REFUSED REPLAY FRAME IS A DEGRADED STIMULUS, not a device result. The
    # saturation arm of 2026-09-28 lost 840 of 8998 VCM commands this way, and the
    # counter breaks that followed were read as device behaviour until the offered
    # and carried rates were compared. The device cannot answer a command that
    # never reached the wire, so an arm that dropped stimulus cannot support a
    # verdict about the device.
    #
    if fails:
        void_reasons.append(
            "%d replay frames were refused by the adapter, so the stimulus is "
            "incomplete" % fails)
    print("  witness saw %d frames (%.1f/s), %d error frames"
          % (arm["witness_frames"], arm["witness_fps"], arm["error_frames"]))
    print("  0x051 on the wire %d = VCM %d + ours %d (%d of ours zero-torque)"
          % (arm["cmd_on_wire"], arm["vcm_cmd"], arm["ours_trailing"],
             arm["ours_also_zero_torque"]))

    # THE SPLIT MUST AGREE WITH THE DEVICE'S OWN COUNT. Two independent
    # instruments: the device says how many frames it transmitted, the witness
    # says how many it saw from us. A classifier that silently mis-attributes is
    # exactly what happened here twice in one day, and neither failure changed
    # any number that was being read -- only this comparison would have.
    #
    # WHICH FRAMES tx_ok COUNTS DEPENDS ON THE MODE. In INHIBIT the device
    # transmits zero-torque 0x051 and tx_ok counts those, so `ours` is the right
    # comparison. In RESPOND it transmits ONLY the 0x7F0 probe and never a
    # 0x051, so `ours` is 0 by construction while tx_ok is ~2999 -- and this
    # check reported "2999 never arrived" on a run where every probe was on the
    # wire. Fixed 2026-09-28; the probe filter matches the one used below.
    #
    if mode == MODE_RESPOND:
        ours_counted = [f for f in got if f[1] == PROBE_ID and not f[4]]
        counted_as = "probe 0x7F0"
    else:
        ours_counted = ours
        counted_as = "zero-torque 0x051"

    tx_ok = (st_end or {}).get("tx_ok")
    if tx_ok is None:
        print("  WARNING: the device reported no tx_ok, so the split is unchecked")
    else:
        arm["wire_vs_device"] = {"ours_on_wire": len(ours_counted),
                                 "device_tx_ok": tx_ok,
                                 "counted_as": counted_as}
        line, reason = attribution_check(mode, len(ours_counted), tx_ok, counted_as)
        print(line)
        if reason:
            void_reasons.append(reason)
        #
        # DID THE STIMULUS ACTUALLY REACH THE WIRE? `refused` counts send()
        # exceptions, and on 2026-09-29 a re-paced arm reported refused 0 while 384
        # of 8998 commands (4.27 %) never appeared on the bus: the adapter took them
        # into its queue and could not fit them onto a wire already carrying 97.6 %,
        # silently. The previous arm's loss was loud; this one was invisible until
        # the witness was counted against the source, so that comparison belongs
        # here rather than in a reader's good intentions.
        #
        # The split avoids the trail classifier deliberately -- it is the thing
        # under suspicion. Every 0x051 on the wire is either the VCM's or one of our
        # answers, and the device transmits at most one answer per command, so
        # `total 0x051 - tx_ok` is the VCM count without appealing to timing. The
        # device's own `ctr_ok + ctr_bad` is the second instrument: when it agrees
        # with this and both fall short of the source, the frames really were never
        # on the wire.
        #
        src_cmd = sum(1 for _, i, _, _ in frames if i == CMD_ID)
        vcm_on_wire = vcm_commands_on_wire(mode, len(cmd), tx_ok)
        recv = (st_end.get("ctr_ok") or 0) + (st_end.get("ctr_bad") or 0)
        arm["stimulus"] = {"source_cmd": src_cmd,
                           "vcm_on_wire": vcm_on_wire,
                           "device_received": recv,
                           "never_reached_wire": src_cmd - vcm_on_wire}
        if src_cmd - vcm_on_wire > 0:
            print("  STIMULUS SHORT: %d of %d commands never reached the wire "
                  "(%.2f %%); the device received %d and the witness saw %d"
                  % (src_cmd - vcm_on_wire, src_cmd,
                     100.0 * (src_cmd - vcm_on_wire) / max(1, src_cmd),
                     recv, vcm_on_wire))
            void_reasons.append(
                "%d of %d commands (%.2f %%) never reached the wire despite "
                "refused=%d" % (src_cmd - vcm_on_wire, src_cmd,
                                100.0 * (src_cmd - vcm_on_wire) / max(1, src_cmd),
                                fails))

        if trailing_only:
            print("  NOTE: %d frames trailed another 0x051 within %.0f us but "
                  "commanded torque, so they are the VCM's -- the replay "
                  "compresses some gaps. This is why both conditions are "
                  "required." % (trailing_only, TRAIL_MAX_US))

        # LATENESS AT THE WIRE -- spec 12.4's full-replay row, "no inhibit frame
        # after the VCM's next" -- DECIDED BY THE ROLLING COUNTER.
        #
        # Neither of my first two attempts could fail. Iterating over `ours` and
        # asking whether each precedes the next VCM frame is vacuous, because our
        # frames are DEFINED as those within TRAIL_MAX_US of a VCM frame. So is
        # comparing counts: a frame answering command N that lands after N+1
        # still trails a VCM frame and still carries zero torque, so it is booked
        # as an on-time answer to N+1, the device answers N+1 as well, and the
        # totals agree over a late frame.
        #
        # The counter is the mechanism and therefore the discriminator. We steal
        # the counter the VCM is about to use, so our B5 is gi_ctr(answered) + 1.
        # If the VCM frame immediately before us is the one we answered, that
        # holds. If it is the NEXT command, its counter already equals ours and
        # the test fails. gi_ctr is d[5] & 0x0F (gen_inhibit_core.c).
        #
        # AGAINST THE FRAME IMMEDIATELY IN FRONT -- whatever it was classified as.
        # Comparing against the last frame CLASSED as VCM is what manufactured the
        # "5 late" of 2026-09-28: a misattributed VCM between our answer and the
        # next reads +1 itself and pushes the genuine answer behind it to +2.
        #
        late, doubled, unchecked, missing_cmd, misattr = 0, 0, 0, 0, 0
        for i in sorted(ours_idx):
            f = cmd[i]
            front = cmd[i - 1] if i > 0 else None
            if front is None or len(f[2]) < 6 or len(front[2]) < 6:
                unchecked += 1
                continue
            if (i - 1) in ours_idx:
                # Two of ours in a row: a different fault with the same symptom.
                doubled += 1
            d = (f[2][5] - front[2][5]) & 0x0F
            if d == 1:
                continue                      # on time
            if d == 0:
                #
                # Late ONLY IF NOTHING ANSWERS IT. The device sends at most one
                # answer per command, so a delta-0 frame followed inside the trail
                # window by a +1 frame is a misattributed VCM command, not our late
                # answer -- the +1 behind it IS the answer to it.
                #
                nxt = cmd[i + 1] if i + 1 < len(cmd) else None
                answered = (nxt is not None and len(nxt[2]) >= 6
                            and ((nxt[2][5] - f[2][5]) & 0x0F) == 1
                            and (nxt[0] - f[0]) * 1e6 <= trail_us)
                if answered:
                    misattr += 1
                else:
                    late += 1
            else:
                # delta >= 2: a command is missing in front of us. A STIMULUS gap,
                # not lateness, and conflating the two is how the harness's own
                # dropped frames were read as device faults.
                missing_cmd += 1
        arm["late_at_wire"] = late
        arm["double_answers"] = doubled
        arm["counter_unchecked"] = unchecked
        arm["missing_command_in_front"] = missing_cmd
        arm["misattributed_vcm"] = misattr
        arm["trail_window_us"] = round(arm_trail_us, 1)
        if misattr or missing_cmd:
            print("  classifier: %d frames were misattributed VCM commands "
                  "(answered within %.0f us), %d had a command missing in front"
                  % (misattr, arm_trail_us, missing_cmd))

        # A DIFFERENT QUANTITY, kept and renamed: frames the device says it sent
        # that never reached the wire at all.
        arm["absent_from_wire"] = tx_ok - len(ours_counted)

        if late:
            print("  LATE AT THE WIRE: %d of %d frames carry a counter that "
                  "does not follow the VCM frame in front of them, so they "
                  "landed after the VCM's next 0x051 (spec 12.4)"
                  % (late, len(ours_idx)))
        else:
            print("  none of our %d frames landed after the VCM's next 0x051 "
                  "(counter follows the frame immediately in front in every case)"
                  % len(ours_idx))
        if doubled:
            print("  %d of our frames were a SECOND answer between two VCM "
                  "commands" % doubled)
        if unchecked:
            print("  %d frames could not be counter-checked (short DLC or no "
                  "preceding VCM frame)" % unchecked)
        if arm["absent_from_wire"]:
            print("  ABSENT: the device says it sent %d frames, %d are on the "
                  "wire (%s) -- %d never arrived"
                  % (tx_ok, len(ours_counted), counted_as,
                     arm["absent_from_wire"]))

    # WIRE-LEVEL PROBE LATENCY, the independent half of spec 12.1's "the device
    # reports its own figures and the witness reports what reached the wire, and
    # the way they agree is the check". Each probe is paired with the most recent
    # 0x051 the witness saw, which is the frame it is answering.
    probes = [f for f in got if f[1] == PROBE_ID and not f[4]]
    lat = []
    last_cmd = None
    for f in sorted(got, key=lambda x: x[0]):
        if f[1] == CMD_ID and not f[4]:
            last_cmd = f[0]
        elif f[1] == PROBE_ID and not f[4] and last_cmd is not None:
            lat.append((f[0] - last_cmd) * 1e6)
    # A probe is queued at a set offset after the RX, so a negative or absurd
    # delta means the pairing broke rather than that the device was fast.
    lat = [v for v in lat if 0.0 <= v < 100000.0]
    arm["probes_on_wire"] = len(probes)
    if lat:
        arm["probe_wire_us"] = {
            "count": len(lat),
            "min": round(min(lat), 1), "p50": round(pct(lat, 50), 1),
            "p95": round(pct(lat, 95), 1), "p99": round(pct(lat, 99), 1),
            "max": round(max(lat), 1),
        }
        w = arm["probe_wire_us"]
        print("  probes on the wire %d; latency from the 0x051 they answer:"
              % len(probes))
        print("    min %.0f  p50 %.0f  p95 %.0f  p99 %.0f  max %.0f us"
              % (w["min"], w["p50"], w["p95"], w["p99"], w["max"]))
    if ham:
        print("  WiFi load: %d requests, %d errors" % (ham.n, ham.errors))

    if st_end is None:
        print("  DEVICE DID NOT ANSWER at the end of the arm -- nothing below")
        print("  can be read as a device measurement.")
        # This return skips the void banner below, so record it here too: the JSON
        # has to carry the same verdict-suppressing flag as every other path.
        void_reasons.append("the device did not answer at the end of the arm")
        arm["void"] = True
        return arm

    # The precondition. Everything else is meaningless without it, and this is
    # the shape that has produced vacuous passes repeatedly in this component.
    live = st_end.get("inhibit_live")
    print("  device: live=%s mode=%s arm_block=%r abort=%r"
          % (live, st_end.get("mode"), st_end.get("arm_block"),
             st_end.get("abort_reason")))
    print("  device: tx_ok=%s tx_fail=%s ctr_ok=%s ctr_bad=%s "
          "tx_queued_behind=%s"
          % (st_end.get("tx_ok"), st_end.get("tx_fail"), st_end.get("ctr_ok"),
             st_end.get("ctr_bad"), st_end.get("tx_queued_behind")))
    r = st_end.get("response") or {}
    print("  device response us: count=%s min=%s mean=%s max=%s"
          % (r.get("count"), r.get("min_us"), r.get("mean_us"), r.get("max_us")))
    g = st_end.get("rx_gap") or {}
    print("  device rx_gap us:   count=%s min=%s mean=%s max=%s"
          % (g.get("count"), g.get("min_us"), g.get("mean_us"), g.get("max_us")))

    #
    # LAST, so it is the line left on the screen. An arm can be void for reasons
    # discovered at three different points -- the witness write, the send loop and
    # the attribution check -- and a reader who scrolled past the first two would
    # otherwise take the measurements above as results. The figures are still
    # printed and still written to the JSON, because they are what diagnoses the
    # degradation; what is withheld is their standing as a verdict.
    #
    arm["void"] = bool(void_reasons)
    if void_reasons:
        print("  ---- ARM VOID: the figures above do not support a verdict ----")
        for r_ in void_reasons:
            print("       - %s" % r_)
    return arm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--capture", default=None,
                    help="replay this capture instead of truck_passive2.log. "
                         "Use with a slice cut by cut_replay_window.py, whose "
                         "window is armable from its first frame.")
    ap.add_argument("--window-start", type=float, default=None,
                    help="override the window start (a pre-cut slice wants 0)")
    ap.add_argument("--arm", default="both", choices=("idle", "loaded", "both"))
    ap.add_argument("--flood", action="store_true",
                    help="saturate the bus with low-priority filler on 0x%03X "
                         "while the replay runs (spec 5.2 item 10 case 3)"
                         % FLOOD_ID)
    # CROSS-SESSION HAZARD, 2026-10-04. COM3 is the S3 interposer board and
    # belongs to another session. Opening it takes their hardware, and CLOSING
    # it RESETS the board, because DTR/RTS is its reset line -- they lost a
    # whole measurement condition to exactly that, since a freshly reset board
    # has dropped nothing and so reports clean loss counters. There is no
    # default here and there must not be one: ask that session first, and
    # confirm the MAC rather than trusting a remembered port number.
    ap.add_argument("--flood-slcan", default=None, metavar="PORT",
                    help="saturate from a SECOND adapter instead (e.g. COM3, the "
                         "interposer board running the `slcan` env -- WHICH "
                         "BELONGS TO ANOTHER SESSION, and closing its port "
                         "RESETS it; ask first). Frames from "
                         "one adapter do not arbitrate against each other, so "
                         "--flood has to pace by wire time and still lost 384 of "
                         "8998 commands at 97.6%% carried; from its own controller "
                         "arbitration places the filler and it cannot delay a "
                         "command. Mutually exclusive with --flood.")
    ap.add_argument("--mode", default="inhibit", choices=("inhibit", "respond", "passive", "observe"),
                    help="inhibit measures RX-to-completion; respond measures "
                         "RX-to-queueing, which is the scheduling delay without "
                         "air time (spec 12.1)")
    ap.add_argument("--arm-after", type=float, default=None, metavar="S",
                    help="POST the mode S seconds into the replay (into live traffic) "
                         "instead of before it starts")
    ap.add_argument("--post-schedule", default="", metavar="T:MODE,...",
                    help="POST these modes at these times (s) into the replay, after the "
                         "initial --mode (e.g. 10:1,20:3)")
    ap.add_argument("--post-raw-timeout", type=float, default=None, metavar="S",
                    help="send the --post-schedule POSTs over a raw socket with this "
                         "timeout, recording every byte and the connection's end "
                         "(raw_http_probe.py); a failed reply is followed by a fresh GET")
    ap.add_argument("--post-chain", action="store_true",
                    help="each scheduled POST waits for the previous one to finish")
    ap.add_argument("--poll-hz", type=float, default=0.0,
                    help="poll the status page at this rate during an idle arm, recording "
                         "drv.msgs_to_rx each time (0: no polling, the default)")
    ap.add_argument("--offset-us", type=int, default=500,
                    help="the offset given to the setter with the mode (recorded)")
    ap.add_argument("--out", default=gi_paths.runs("e4_device_calibration.json"))
    args = ap.parse_args()

    if args.flood and args.flood_slcan:
        print("--flood and --flood-slcan are mutually exclusive: two fillers "
              "would make the carried load unattributable to either.")
        return 2

    #
    # `--out runs/foo.json` used to be taken relative to the CWD, which put the
    # JSON in whatever directory the tool happened to be launched from -- and
    # created a `runs/` there to hold it. The default is absolute, so this only
    # bit when a path was passed explicitly, which is every real run. It also
    # took the witness log down with it: the raw-log path is derived from this
    # one, so it pointed at a directory that did not exist and the capture was
    # lost with a warning while the arm still printed a full verdict. `--capture`
    # already resolves against the script's directory; this now matches it.
    #
    if (not os.path.isabs(args.out)
            and os.path.dirname(args.out) in ("", "runs")):
        args.out = gi_paths.runs(os.path.basename(args.out))

    print("E4 device calibration -- full-bus truck replay into the WiCAN")

    device_sha, why = device_identity()
    if device_sha is None:
        print("device did not answer /gen_inhibit with its elf_sha256: %s" % why)
        print("Join its AP first: bench_flash.py --status, in the firmware repo's "
              "gen-inhibit/tools/ (set GI_WICAN_SSID; see tools-reference.md)")
        return 2
    print("device elf_sha256 %s (from /gen_inhibit)" % device_sha)

    global WINDOW_START, CAPTURE
    if args.capture:
        CAPTURE = gi_paths.verify(args.capture) if os.path.isabs(args.capture)             else gi_paths.verify(gi_paths.data_dir(args.capture))
        # A cut slice begins AT the armable point, so the default 0.0 is right
        # and the B0 precondition below still guards it.
        WINDOW_START = 0.0
    if args.window_start is not None:
        WINDOW_START = args.window_start
    mode_num = {"passive": MODE_PASSIVE, "observe": 1}.get(args.mode, 3)
    if args.mode == "respond":
        # RESPOND does not go through the arm gate, so the window is chosen for
        # traffic alone and the B0 precondition below does not apply.
        mode_num = 2
        WINDOW_START = RESPOND_WINDOW_START

    frames = load_window(args.seconds)
    if not frames:
        print("no frames in the window")
        return 2
    span = frames[-1][0] - frames[0][0]

    # THE WINDOW'S OWN PRECONDITION. If any 0x051 in it requests the generator,
    # the arm gate blocks and every device counter reads zero -- which is how a
    # first attempt at this file would have "passed" while measuring nothing.
    b0 = collections.Counter(f[2][0] for f in frames
                             if f[1] == CMD_ID and not f[3] and f[2])
    print("window %.0f-%.0f s: %d frames, %.1f frames/s, 0x051 B0 %s"
          % (WINDOW_START, WINDOW_START + args.seconds, len(frames),
             len(frames) / span, {hex(k): v for k, v in b0.items()}))
    # THE PRECONDITION IS ABOUT THE START, not the whole window. The device has
    # to be able to ARM -- spec 7 blocks arming while the VCM is requesting the
    # generator -- and after that a live device is expected to hold B0 at 0x08
    # through a request rather than to stop.
    head = [f for f in frames
            if f[1] == CMD_ID and not f[3] and f[2] and f[0] < ARM_HEADROOM_S]
    head_b0 = collections.Counter(f[2][0] for f in head)
    print("  first %.0f s of 0x051: %s"
          % (ARM_HEADROOM_S, {hex(k): v for k, v in head_b0.items()}))
    if args.mode == "respond":
        print("  RESPOND: the arm gate is not involved, so B0 does not matter "
              "and this window is chosen for traffic")
    if args.mode != "respond" and (not head or set(head_b0) != {0x08}):
        print("REFUSING TO RUN: the VCM is already requesting the generator in")
        print("the first %.0f s, so spec 7's arm gate will never pass and every"
              % ARM_HEADROOM_S)
        print("device counter would read zero -- a run that proves nothing.")
        return 2

    global OFFSET_US, POLL_HZ, ARM_AFTER_S, POST_SCHEDULE, POST_RAW_TIMEOUT, POST_CHAIN
    OFFSET_US = args.offset_us
    POST_RAW_TIMEOUT = args.post_raw_timeout
    POST_CHAIN = args.post_chain
    POLL_HZ = args.poll_hz
    ARM_AFTER_S = args.arm_after
    POST_SCHEDULE = parse_schedule(args.post_schedule)
    out = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "mode": args.mode,
           "offset_us": OFFSET_US,
           "poll_hz": POLL_HZ,
           "arm_after_s": ARM_AFTER_S,
           "post_schedule": POST_SCHEDULE,
           "capture_used": os.path.basename(CAPTURE),
           # Where each arm's raw witness log goes; consumed by run_arm().
           "_raw_dir": os.path.dirname(os.path.abspath(args.out)),
           "_raw_stem": os.path.splitext(os.path.basename(args.out))[0],
           "device": device_sha,      # elf_sha256 since 2026-10-25; /check_status git_version before
           "capture": os.path.basename(CAPTURE),
           "window_start": WINDOW_START, "seconds": args.seconds,
           "intended_fps": round(len(frames) / span, 1),
           "arms": {}}

    arms = ["idle", "loaded"] if args.arm == "both" else [args.arm]
    for a in arms:
        run_arm(a, frames, a == "loaded", out, mode=mode_num,
                flood=args.flood, flood_slcan=args.flood_slcan)
        time.sleep(2.0)

    # The raw-log bookkeeping is internal; it does not belong in the record.
    for k in ("_raw_dir", "_raw_stem"):
        out.pop(k, None)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)
    attach_provenance(args.out, out)
    print("\nwrote %s" % args.out)
    return 0


PROVENANCE_FIELDS = ("verdict", "replayed_frames", "replayed_found", "replayed_commands",
                     "assigned", "device_frames", "device_cmd_tx", "on_time", "late",
                     "double", "unexplained", "ambiguous", "unresolved", "align", "method")


def attach_provenance(path, out):
    """The live classifier since 2026-10-10 (reviewer): provenance_classifier.py, by the
    replay's queue order, judged from this run's own file and witness log. The trail-
    window classifier's figures above it (late_at_wire, double_answers,
    misattributed_vcm, trail_window_us, and wire_vs_device's ours_on_wire in INHIBIT)
    stay in the record, marked superseded: its window came from the device's
    max_hold_us, which is stamped when the worker credits a frame, not when it is on the
    wire. Since 2026-10-29 (gen-inhibit reviewer) they void no arm and decide no
    verdict; see attribution_check()."""
    import provenance_classifier as P
    for name, arm in out["arms"].items():
        try:
            res = P.rejudge(path, name)
            arm["provenance"] = {k: res.get(k) for k in PROVENANCE_FIELDS}
        except Exception as e:                      # recorded, never silently skipped
            arm["provenance"] = {"verdict": "NOT RUN", "error": "%s: %s" % (type(e).__name__, e)}
        arm["superseded_classifier"] = ("late_at_wire, double_answers, misattributed_vcm and "
                                        "trail_window_us are the old trail-window classifier, "
                                        "printed and labelled; since 2026-10-29 they void no "
                                        "arm, and the verdict is arm['provenance']")
        p = arm["provenance"]
        print("  [%s] provenance (live): %s -- replayed %s/%s found, device 0x051 %s (device "
              "says %s), late %s, double %s, ambiguous %s%s"
              % (name, p.get("verdict"), p.get("replayed_found"), p.get("replayed_frames"),
                 p.get("device_frames"), p.get("device_cmd_tx"), len(p.get("late") or []),
                 len(p.get("double") or []), len(p.get("ambiguous") or []),
                 "; " + "; ".join(p.get("unresolved") or []) if p.get("unresolved") else
                 (" ERROR " + p["error"] if "error" in p else "")))
        print("  [%s] old trail-window classifier (SUPERSEDED): late %s, double %s, "
              "misattributed %s, window %s us"
              % (name, arm.get("late_at_wire"), arm.get("double_answers"),
                 arm.get("misattributed_vcm"), arm.get("trail_window_us")))
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    sys.exit(main())
