"""
On-vehicle runner for the generator inhibitor. One dongle, one connection to
the powertrain bus, no cutting into the harness.

    python3.13 tools/gen_inhibit/vehicle_runner.py            # observe only
    python3.13 tools/gen_inhibit/vehicle_runner.py --live     # will transmit

READ THIS BEFORE THE FIRST LIVE RUN
-----------------------------------
This is NOT the same thing as `../../CANsmog/generator_runner.py`. That script
cuts the bus and sits in it, so it rewrites every 0x051 on its way past and
nothing it does can collide with anything. This one is plugged in alongside the
VCM and has to place its frames in the gaps between the VCM's, because two
nodes transmitting the same CAN ID at overlapping times do not arbitrate --
they bit-error each other, and repeated at 100 Hz that walks the VCM toward
bus-off. Which is a truck that has stopped.

So the default mode transmits NOTHING. It locks onto the VCM's 0x051 slot,
measures how well it can predict it on *this* hardware, and tells you whether
the timing budget closes before you ever let it near the bus. Expect the
`lead` half to be refused: CPython on a general-purpose kernel does not
schedule a transmission a fixed 2 ms ahead of a predicted event to better than
a millisecond, and the core will not transmit if it cannot prove the frame
lands clear. `trail` is reactive -- receive, immediately reply -- and is the
half that has a real chance from userspace Python.

The engage interlock refuses to arm while the generator is running, so the
tested transition is always "engine off, VCM tries to start it, we stop it",
never "take over a loaded generator".

SIGNALS WATCHED (all confirmed, see notes/powertrain-messages.md)
----------------------------------------------------------------
  0x051 B1-B2  VCM generator torque command   LE, -32768
  0x051 B3-B4  engine RPM reference           LE, -32768; -1 = engine off
  0x051 B5     rolling counter
  0x471 B0-B1  GENE inverter torque FEEDBACK  LE, -32768
  0x054 B0-B1  generator/engine RPM           LE, -32768; -1 = engine off
  0x644 B6     operating mode  0xE0 init / 0xF0 normal / 0x90 backdriving
  0x053 B0-B1  generator/system mode status
  0x617 B7     VCM fault flag  0xC8 normal / 0xC9 secondary / 0xCA FAULT ACTIVE
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _repo_root():
    """Walk up to the directory that holds `canre`.

    Counting `.parents[N]` is how this breaks -- it is off by one the moment
    the file moves, and AGENTS.md flags it. Searching for the marker cannot be.
    """
    for d in _HERE.parents:
        if (d / "canre").is_dir():
            return d
    return _HERE.parents[-1]


REPO_ROOT = _repo_root()
sys.path.insert(0, str(_HERE))

import argparse
import csv
import subprocess
import threading
import time
from collections import Counter, deque

import can

from sync import SlotSync
from inhibit import Inhibitor

# -- clock ------------------------------------------------------------------
# time.monotonic() is GetTickCount64() on Windows: 15.625 ms resolution, which
# is coarser than the 10 ms slot this tool has to hit. Every interval measured
# here then quantises to a tick, which shows up as a phase error of half a tick
# (~8 ms) and a reply latency that reads as exactly 0.000 ms. perf_counter() is
# QueryPerformanceCounter() on Windows and clock_gettime(MONOTONIC) on Linux --
# monotonic on both, sub-microsecond on both -- so it is the correct clock for
# every interval in this file. Measured on the Windows laptop, 2026-09-06.
_now = time.perf_counter


CMD = 0x051          # VCM -> GENE composite command (our target)
FB = 0x471           # GENE torque feedback
RPM = 0x054          # generator/engine RPM
MODE = 0x644         # B6 operating mode
SYS = 0x053          # generator/system mode status
FAULT = 0x617        # B7 VCM fault flag

# How soon after our own bus.send() a matching payload must return for it
# to be our echo rather than the VCM. Our echo comes back within the send
# latency -- well under a millisecond. The VCM cannot produce that payload
# sooner than its next slot, and the minimum inter-arrival measured on the
# truck (2026-09-06, Kvaser hardware timestamps) is 5.2 ms. 2 ms sits an
# order of magnitude clear of both.
ECHO_WINDOW = 0.002  # s

# Sliding window over which the error-frame rate is judged, and how far above
# the measured background the rate must go before the transmitter is cut. The
# multiplier is deliberately blunt: the point is to ignore a noise floor that
# was measured, not to be clever about its distribution.
ERR_WINDOW = 10.0    # s; 5 s was too twitchy -- against this truck's measured
                     # 0.209/s background a 5 s window false-trips in ~33% of
                     # 90 s runs, a 10 s window in ~5%. A genuine collision at
                     # 100 Hz produces error frames far faster than the trip
                     # rate either way, so the longer window costs no real
                     # sensitivity.
ERR_FACTOR = 3.0     # times the measured background
# Never trip below this, whatever warmup measured. Warmup runs with the engine
# OFF and the bus quiet, so it measures ~0/s -- but the regime that matters only
# exists once the generator is running, which is after warmup by construction.
# Replayed against every error-frame sequence captured on 2026-09-06, the worst
# 10 s window anywhere was 4, and it occurred in the PASSIVE control with
# nothing transmitted. A trip of 3 or 4 fires on that noise; 7 and 10 fire on
# none of it. 10 keeps 2.5x headroom over the worst observed background while a
# genuine same-ID collision, at 100 Hz, would blow past it inside a second.
ERR_MIN_TRIP = 10    # in ERR_WINDOW seconds

MODE_NAMES = {0xE0: "init", 0xF0: "normal", 0x90: "BACKDRIVING"}
FAULT_NAMES = {0xC8: "normal", 0xC9: "secondary", 0xCA: "FAULT ACTIVE"}


def le16c(d, i):
    """The bus's centred 16-bit encoding: little-endian, zero at 0x8000."""
    return ((d[i + 1] << 8) | d[i]) - 32768


class DriverClock:
    """Map the adapter's hardware timestamp into the host epoch.

    Measured on the truck, 2026-09-06, 2000 frames of 0x051 per run:

                        host clock (_now)   Kvaser msg.timestamp
      p99 |residual|    8.45 / 48.39 ms     2.80 / 2.84 ms
      inter-arrival min      0.24 ms             5.21 ms
      gaps < 1 ms         51 of 1999           0 of 1999

    A 100 Hz message cannot arrive 0.24 ms after its predecessor: the host
    clock is seeing USB transfer batching, not the bus. The host figure also
    swung 6x between two consecutive runs while the driver figure moved by
    0.04 ms -- the driver timestamps are measuring the truck, the host clock is
    measuring Windows scheduling. Synchronising on the host clock inflates the
    phase error, which widens the guard band and fails preflight for reasons
    that have nothing to do with the vehicle.

    The adapter's clock has its own epoch, so it is mapped rather than used
    raw: everything the inhibitor schedules is compared against the host clock,
    and mixing epochs would be worse than the noise being removed. The offset
    is estimated as the MINIMUM of (host - driver) over a sliding window --
    delivery delay is strictly one-sided, so the least-delayed sample is the
    best estimate of the true offset, and a batched arrival can only ever push
    a sample the wrong way.
    """

    def __init__(self, window=500, sanity=1.0):
        self.window = window
        self.sanity = sanity
        self.offsets = deque(maxlen=window)
        self.offset = None
        self.usable = True
        self.n_reset = 0

    def map(self, drv, host):
        """Driver timestamp -> host epoch. Falls back to `host` if unusable."""
        if not self.usable or not drv:
            return host
        off = host - drv
        self.offsets.append(off)
        lo = min(self.offsets)
        if self.offset is None or lo < self.offset:
            self.offset = lo
        t = drv + self.offset
        # A mapped time far from the host clock means the adapter clock jumped
        # or the offset went stale. Re-seed rather than schedule against it.
        if t - host > self.sanity or host - t > self.sanity:
            self.n_reset += 1
            self.offsets.clear()
            self.offset = off
            return drv + self.offset
        return t


class Monitor:
    """What the generator side of the bus is doing, and whether it is healthy."""

    def __init__(self):
        self.cmd = None
        self.rpm_ref = None
        self.state_byte = None
        self.fb = None
        self.rpm = None
        self.mode = None
        self.sys = None
        self.fault = None
        self.last = {}
        self.fault_ca_seen = False
        self.mode_seen = Counter()

    def update(self, mid, d, now):
        self.last[mid] = now
        if mid == CMD and len(d) >= 6:
            self.cmd = le16c(d, 1)
            self.rpm_ref = le16c(d, 3)
            self.state_byte = d[0]
        elif mid == FB and len(d) >= 2:
            self.fb = le16c(d, 0)
        elif mid == RPM and len(d) >= 2:
            self.rpm = le16c(d, 0)
        elif mid == MODE and len(d) >= 7:
            self.mode = d[6]
            self.mode_seen[d[6]] += 1
        elif mid == SYS and len(d) >= 2:
            self.sys = (d[1] << 8) | d[0]
        elif mid == FAULT and len(d) >= 8:
            self.fault = d[7]
            if d[7] == 0xCA:
                self.fault_ca_seen = True

    def engine_running(self):
        if self.rpm is not None:
            return self.rpm >= 0
        if self.rpm_ref is not None:
            return self.rpm_ref >= 0
        return None

    def gene_alive(self, now, max_age=0.5):
        """Is the generator inverter still talking at all?

        If 0x471 stops, the GENE MCU has reset or gone bus-off. That is the
        single most important thing to be able to distinguish from a working
        inhibit, and it is why this is checked separately from every signal
        value.
        """
        t = self.last.get(FB)
        return t is not None and (now - t) < max_age

    def line(self):
        def f(v, w=6):
            return ("%*d" % (w, v)) if v is not None else ("%*s" % (w, "-"))
        return ("cmd %s  fb %s  rpm %s  rpm_ref %s  B0 %s  mode %-11s fault %s"
                % (f(self.cmd), f(self.fb), f(self.rpm), f(self.rpm_ref),
                   ("0x%02X" % self.state_byte) if self.state_byte is not None else "-",
                   MODE_NAMES.get(self.mode, "0x%02X" % self.mode) if self.mode is not None else "-",
                   FAULT_NAMES.get(self.fault, "0x%02X" % self.fault) if self.fault is not None else "-"))


class Runner:
    def __init__(self, args):
        self.args = args
        self.bus = None
        self.stop = threading.Event()
        self.mon = Monitor()
        self.lock = threading.Lock()
        self.sync = SlotSync()
        self.inh = Inhibitor(
            self.sync,
            strategy=args.strategy,
            payload_mode=args.payload,
            arm_mode="on_demand",
            lead=args.lead / 1000.0,
            trail_delay=args.trail / 1000.0,
            tx_latency=args.tx_latency / 1000.0,
            target_torque=args.target,
        )
        self.live = args.live
        self.reply_latency = deque(maxlen=500)   # recv -> our send, seconds
        self.send_call = deque(maxlen=500)       # duration of bus.send() itself
        self.error_frames = 0
        self.error_times = deque()       # wire times of recent error frames
        self.bg_err_rate = 0.0           # /s, measured during warmup
        self.err_trip_count = 3          # recomputed from the background
        # --host-clock forces the old behaviour: synchronise on when we noticed
        # a frame rather than when the adapter says it arrived. Kept as an
        # escape hatch for an interface whose timestamps cannot be trusted.
        self.dclock = DriverClock()
        if getattr(args, "host_clock", False):
            self.dclock.usable = False
        self._mine = {}          # payload -> time we sent it
        self.echoes = 0
        # Genuine VCM frames carrying the payload we had just sent --
        # the collision the old byte-keyed echo test mistook for echoes.
        # Expected to run at roughly our own transmit rate whenever the
        # VCM is commanding a steady value; near zero means the VCM is
        # changing its command faster than we are anticipating it.
        self.payload_collisions = 0
        self._echo_warned = False
        self.events = []
        self.rows = []
        self.tx_count = 0
        self.armed_since = None
        self.armed_frames = 0            # VCM frames seen while armed
        self.engine_started = False      # engine ran while we were armed
        self.attempt_seen = False        # VCM asked for torque while we were armed
        self.attempt_peak = 0
        self.fb_peak_while_armed = 0
        self.disarm_at = None
        self._epoch0 = time.time() - _now()
        self._log = None
        self._log_n = 0
        self._engine_running_since = None
        self._armed_at_mono = None

    # -- raw capture -------------------------------------------------------

    def open_log(self):
        """Write a candump-format log of everything on the bus, ours included.

        candump because canre parses it directly, so a run drops straight into
        survey.py / analyze_id.py alongside every other capture in the project.
        Our own transmissions are written too -- they really were on the wire,
        and a capture that silently omits them would misrepresent the run to
        anyone reading it later.
        """
        if not self.args.log:
            return
        p = Path(self.args.log)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(p, "w", buffering=1 << 16, encoding="ascii")

    def open_error_log(self):
        """Error frames get their own file next to the capture.

        They are deliberately NOT written into the candump log: that file is
        parsed by canre alongside every other capture in the project, and
        injecting synthetic error records into it would corrupt frame counts
        and rate calculations for every tool that reads it.

        They need to be written somewhere, though. Until now they existed only
        in the console output and in meta.json events, so a run that was
        stopped rather than allowed to finish lost them entirely -- which
        nearly happened to the passive control of 2026-09-06, the measurement
        that set the error trip. A flushed sidecar survives a kill.
        """
        if not self.args.log:
            return
        p = Path(self.args.log)
        p = p.with_suffix(".errors.csv")
        p.parent.mkdir(parents=True, exist_ok=True)
        self._errlog = open(p, "w", buffering=1, encoding="ascii")
        # tx_would_send, not tx: in observe mode the runner counts frames it
        # would have sent but did not. armed=0 means nothing was on the wire.
        self._errlog.write("host_time,driver_time,armed,tx_would_send\n")
        self._errlog_path = p

    def log_error(self, now, drv, armed):
        if getattr(self, "_errlog", None) is None:
            return
        self._errlog.write("%.6f,%s,%d,%d\n"
                           % (now, ("%.6f" % drv) if drv else "",
                              1 if armed else 0, self.tx_count))

    def log_frame(self, mono, arb_id, data, extended=False):
        if self._log is None:
            return
        self._log.write("(%.6f) %s %0*X#%s\n"
                        % (self._epoch0 + mono, self.args.log_iface,
                           8 if extended else 3, arb_id, data.hex().upper()))
        self._log_n += 1

    def close_error_log(self):
        if getattr(self, "_errlog", None) is not None:
            self._errlog.close()
            print("  %d error frames -> %s"
                  % (self.error_frames, self._errlog_path))
            self._errlog = None

    def close_log(self):
        if self._log is not None:
            self._log.close()
            print("  %d frames -> %s" % (self._log_n, self.args.log))

    # -- bus ---------------------------------------------------------------

    def open_and_identify(self):
        a = self.args
        if a.interface == "udp_multicast":
            # Rehearsal transport: the same virtual segment the bench uses.
            # hop_limit=0 keeps it on this host; gi_vbus verifies that by
            # reading the option back and refuses to start if the TTL is not
            # zero, or if it cannot find the socket to read at all.
            #
            # gi_vbus lives beside this file, so NOTHING is put on sys.path --
            # the previous version inserted tools/interposer at sys.path[0] and
            # imported a module called plain `bus`, which meant any other
            # bus.py earlier on the path would have won. That mattered here
            # more than it looks: the module being imported is the one holding
            # the guard that keeps 66,450 packets/s off the house LAN, so
            # "probably the right bus.py" is not good enough. The assert below
            # is cheap and makes the resolved file the thing checked rather
            # than the import mechanism.
            import gi_vbus
            import argparse as _ap
            assert Path(gi_vbus.__file__).resolve().parent == _HERE.resolve(), (
                "gi_vbus resolved to %s, not the copy beside vehicle_runner.py"
                % gi_vbus.__file__)
            self.bus = gi_vbus.open_bus(
                _ap.Namespace(transport="virtual", channel=a.channel,
                              port=a.port, bitrate=a.bitrate),
                receive_own_messages=False)
        elif a.interface == "socketcan":
            self.bus = can.Bus(interface="socketcan", channel=a.channel,
                               bitrate=a.bitrate, receive_own_messages=False)
        elif a.interface == "auto":
            # Same detection path the rest of the project uses, so a dongle
            # that works for can_logger.py works here.
            if str(REPO_ROOT) not in sys.path:
                sys.path.insert(0, str(REPO_ROOT))
            from canbench.live.receiver import detect_all_can_interfaces
            print("detecting CAN interfaces at %d bps..." % a.bitrate)
            found = detect_all_can_interfaces(a.bitrate)
            if not found:
                raise SystemExit("no CAN interface detected")
            for iface, ch, desc in found:
                print("  %s" % desc)
            iface, ch, desc = found[0]
            print("using: %s" % desc)
            kw = dict(bitrate=a.bitrate, receive_own_messages=False)
            if iface == "pcan":
                kw["auto_reset"] = True      # canbench/live/pcan-busoff-recovery.md
            if isinstance(ch, dict):
                self.bus = can.Bus(interface=iface, **kw, **ch)
            else:
                self.bus = can.Bus(interface=iface, channel=ch, **kw)
            self._iface_desc = desc
        else:
            kw = dict(interface=a.interface, bitrate=a.bitrate,
                      receive_own_messages=False)
            if a.interface == "pcan":
                kw["auto_reset"] = True
            if a.channel is not None:
                kw["channel"] = a.channel
            self.bus = can.Bus(**kw)

        # --wait-for-bus lets the runner be started BEFORE the truck is keyed
        # on: it opens the interface, then re-listens until the fingerprint
        # appears rather than exiting on the first silent pass. This exists
        # because a key cycle is not free -- the truck can be mid-way through
        # an emissions readiness monitor, and starting the tool afterwards
        # costs another one. Waiting also means the capture covers the bus from
        # its first frame, which is the part a post-hoc start always misses.
        a_wait = getattr(a, "wait_for_bus", 0.0)
        deadline = (_now() + a_wait) if a_wait and a_wait > 0 else None
        if a_wait:
            print("waiting for the powertrain bus (key the truck on when "
                  "ready; Ctrl-C to give up)...")

        attempt = 0
        while True:
            attempt += 1
            if not a_wait:
                print("listening %.1f s to identify the bus..."
                      % a.identify_seconds)
            ids = Counter()
            ext = 0
            t_end = _now() + a.identify_seconds
            n = 0
            while _now() < t_end:
                m = self.bus.recv(timeout=0.2)
                if m is None:
                    continue
                n += 1
                ids[m.arbitration_id] += 1
                if m.is_extended_id:
                    ext += 1

            # Identify by CONTENT. Channel numbers vary with dongle insertion
            # order and identifying by one is how you end up confidently
            # analysing the wrong bus. Fingerprint from
            # notes/powertrain-messages.md: 0x051 at ~100 Hz, extended
            # 0x18xxxxxx present, no 0x0C1 / 0x1F5.
            rate = ids[CMD] / a.identify_seconds
            checks = [
                ("0x051 present at 80-120 Hz", 80 <= rate <= 120,
                 "%.1f Hz" % rate),
                ("extended 0x18xxxxxx frames present", ext > 0,
                 "%d frames" % ext),
                ("0x0C1 absent (not the vehicle bus)", ids[0x0C1] == 0,
                 "%d frames" % ids[0x0C1]),
                ("0x1F5 absent (not the vehicle bus)", ids[0x1F5] == 0,
                 "%d frames" % ids[0x1F5]),
            ]
            if a.no_inverter:
                # The generator inverter's 12V fuse is out, so the whole GENE
                # family is off the bus. Do not merely skip the 0x471 check --
                # invert it. If 0x471 IS talking, the fuse is in and this is
                # not the test that was intended; running --no-inverter then
                # would disable the safety trips against a live inverter, which
                # is the one combination that must not happen by accident.
                checks.append(("0x471 ABSENT (inverter unpowered)",
                               ids[FB] == 0, "%d frames" % ids[FB]))
            else:
                checks.append(("0x471 present (GENE inverter talking)",
                               ids[FB] > 0,
                               "%.1f Hz" % (ids[FB] / a.identify_seconds)))

            ok = all(good for _, good, _ in checks)

            # While waiting, stay quiet until something is actually there --
            # a wait of unknown length must not bury the moment it succeeds
            # under a wall of identical failing checks.
            if ok or not a_wait:
                print("  %d frames, %d distinct ids" % (n, len(ids)))
                for name, good, detail in checks:
                    print("  %-38s %-4s %s"
                          % (name, "ok" if good else "NO", detail))
            elif n:
                print("  ... %d frames, fingerprint not matched yet "
                      "(attempt %d)" % (n, attempt))

            if ok:
                print("  powertrain bus confirmed by content")
                if a_wait:
                    print("  bus came up after %d attempt(s)" % attempt)
                return True

            if not a_wait:
                if not self.args.force_bus:
                    raise SystemExit(
                        "\nThis does not look like the powertrain bus. "
                        "Refusing to run.\nRe-check the connection, or pass "
                        "--force-bus if you are certain.")
                print("\n  --force-bus given; continuing against the "
                      "fingerprint")
                return False

            if deadline is not None and _now() > deadline:
                raise SystemExit(
                    "timed out after %.0f s waiting for the powertrain bus."
                    % a_wait)

    def bus_health(self):
        """socketcan only: controller state and error counters straight from the
        kernel. This is how a same-ID collision shows up -- as bus errors and a
        controller sliding from error-active to error-passive."""
        if self.args.interface != "socketcan":
            return None
        try:
            out = subprocess.run(["ip", "-details", "-statistics", "link",
                                  "show", self.args.channel],
                                 capture_output=True, text=True, timeout=2).stdout
        except Exception:
            return None
        state = None
        for tok in ("ERROR-ACTIVE", "ERROR-PASSIVE", "BUS-OFF", "STOPPED"):
            if tok in out:
                state = tok
                break
        return {"state": state, "raw": out}

    # -- the CAN thread ----------------------------------------------------

    def can_loop(self):
        inh = self.inh
        try:
            while not self.stop.is_set():
                m = self.bus.recv(timeout=0.001)
                now = _now()
                if m is not None:
                    if getattr(m, "is_error_frame", False):
                        # A single error frame is NOT evidence of anything on
                        # this truck. Measured 2026-09-06: with the generator
                        # running and this tool provably silent (observe mode,
                        # transmit path disabled), the bus produced 9 error
                        # frames in 43 s -- 0.209/s, gaps of 1.3 to 11.1 s. The
                        # armed run's rate was LOWER, 0.103/s. Aborting on the
                        # first one therefore ends every armed run within
                        # seconds on the truck's own background noise, which is
                        # what killed three consecutive attempts.
                        #
                        # What matters is a rate clearly above that floor, so
                        # the floor is measured during warmup -- while we are
                        # provably not transmitting -- and the trip is set
                        # against it. An unambiguous signal that OUR frame
                        # failed is handled separately, where bus.send() raises.
                        self.error_frames += 1
                        self.error_times.append(now)
                        self.log_error(now, getattr(m, "timestamp", None),
                                       inh.armed)
                        cut = now - ERR_WINDOW
                        while self.error_times and self.error_times[0] < cut:
                            self.error_times.popleft()
                        if inh.armed:
                            n = len(self.error_times)
                            if n >= self.err_trip_count:
                                self.event(
                                    "%d CAN error frames in %.0f s -- %.2f/s "
                                    "against a measured background of %.2f/s. "
                                    "Aborting transmission."
                                    % (n, ERR_WINDOW, n / ERR_WINDOW,
                                       self.bg_err_rate))
                                inh.abort()
                        continue
                    d = m.data
                    # Wire time, not notice time. The synchroniser and the
                    # raw capture both want when the frame was on the bus; the
                    # monitor's staleness checks are a host concern and stay on
                    # the host clock. Both are in the host epoch, so the two
                    # mix safely.
                    t_wire = self.dclock.map(getattr(m, "timestamp", None), now)
                    if m.arbitration_id == CMD and len(d) >= 6:
                        key = tuple(d)
                        sent_at = self._mine.pop(key, None)
                        if sent_at is not None:
                            # Payload identity CANNOT separate our frame from
                            # the VCM's. The inhibit frame mirrors B0-B4 and
                            # takes the counter the VCM is about to use, so it
                            # is byte-identical to the VCM's next genuine frame
                            # BY DESIGN -- that is the mechanism, not a defect.
                            #
                            # Keying the echo test on bytes alone therefore made
                            # every genuine frame look like our own echo. On the
                            # truck, 2026-09-06, that discarded ~500 of ~1000
                            # VCM frames, halved our transmit rate to 50 Hz, and
                            # left a capture that was not a record of the bus.
                            # Our own log could not reveal it -- the Android
                            # logger did: a 150 Hz plateau where ours reaching
                            # every slot would have made it 200 Hz.
                            #
                            # Time separates them with three orders of magnitude
                            # to spare. Compared on WIRE time so that USB
                            # transfer batching cannot smear the two together.
                            if t_wire - sent_at < ECHO_WINDOW:
                                self.echoes += 1
                                if self.echoes > 50 and not self._echo_warned:
                                    self._echo_warned = True
                                    self.event(
                                        "this interface ECHOES our transmissions "
                                        "(%d so far). Suppressed in software here, "
                                        "but a real build must suppress them in "
                                        "the controller." % self.echoes)
                                continue
                            # Same bytes, far too late to be our echo: this is
                            # the VCM sending the frame we anticipated. It is
                            # genuine and must reach the synchroniser.
                            self.payload_collisions += 1
                    self.log_frame(t_wire, m.arbitration_id, bytes(d),
                                   m.is_extended_id)
                    with self.lock:
                        self.mon.update(m.arbitration_id, d, now)
                        if m.arbitration_id == CMD and len(d) >= 6:
                            inh.on_genuine(t_wire, tuple(d))
                            self.track(now)
                # poll and transmit
                out = inh.poll(_now())
                if out and self.live:
                    for _cid, payload in out:
                        self._mine[payload] = _now()
                        if len(self._mine) > 64:
                            cut = _now() - 0.5
                            self._mine = {k: v for k, v in self._mine.items()
                                          if v > cut}
                        t0 = _now()
                        try:
                            self.bus.send(can.Message(
                                arbitration_id=CMD, is_extended_id=False,
                                data=bytes(payload), dlc=6))
                        except can.CanError as e:
                            self.event("send failed: %r -- aborting" % (e,))
                            inh.abort()
                            break
                        t1 = _now()
                        self.log_frame(t1, CMD, bytes(payload))
                        self.send_call.append(t1 - t0)
                        self.tx_count += 1
                        if self.sync.last_seen is not None:
                            self.reply_latency.append(t1 - self.sync.last_seen)
                elif out:
                    # observe mode: record what we WOULD have done
                    for _cid, _payload in out:
                        self.tx_count += 1
                        if self.sync.last_seen is not None:
                            self.reply_latency.append(
                                _now() - self.sync.last_seen)
        except Exception as e:                                  # noqa: BLE001
            self.event("CAN thread died: %r" % (e,))
            self.stop.set()

    def track(self, now):
        """Safety interlocks and evidence gathering, once per VCM frame."""
        m = self.mon
        inh = self.inh
        if m.fault == 0xCA and inh.armed:
            self.event("0x617 B7 = 0xCA (fault active) -- disarming")
            inh.abort()
        if inh.armed and not self.args.no_inverter and not m.gene_alive(now):
            self.event("0x471 stopped -- GENE inverter is not talking. Disarming.")
            inh.abort()
        if inh.armed:
            self.armed_frames += 1
            # If the engine actually starts while we are armed, the inhibit has
            # failed and we are now commanding zero torque to a generator on a
            # running engine. Hand control back rather than hold an unloaded
            # engine at whatever speed the VCM is asking for. Debounced,
            # because a momentary crank blip is exactly what a working inhibit
            # produces and must not end the test.
            if m.rpm is not None and m.rpm >= self.args.start_abort_rpm:
                if self._engine_running_since is None:
                    self._engine_running_since = now
                elif now - self._engine_running_since > 0.30:
                    self.engine_started = True
                    self.event("engine reached %d rpm while armed -- the inhibit "
                               "did not hold. Disarming and handing back."
                               % m.rpm)
                    inh.disarm()
            else:
                self._engine_running_since = None
            # Dead-man: never hold the inhibit indefinitely.
            if (self._armed_at_mono is not None
                    and now - self._armed_at_mono > self.args.max_armed):
                self.event("max armed time (%.0f s) reached -- disarming"
                           % self.args.max_armed)
                inh.disarm()
            if m.cmd is not None and abs(m.cmd) > 50:
                self.attempt_seen = True
                self.attempt_peak = max(self.attempt_peak, abs(m.cmd))
            if m.fb is not None:
                self.fb_peak_while_armed = max(self.fb_peak_while_armed, abs(m.fb))
        if len(self.rows) < 400000:
            self.rows.append((now, m.cmd, m.fb, m.rpm, m.rpm_ref, m.state_byte,
                              m.mode, m.sys, m.fault, inh.state, self.tx_count))

    def event(self, text):
        t = _now()
        self.events.append((t, text))
        print("\n  [%8.3f] %s" % (t, text))

    # -- verdict -----------------------------------------------------------

    def verdict(self):
        """Tell apart 'the inhibit worked' from 'we broke the inverter'."""
        m = self.mon
        now = _now()
        lines = []

        # Before anything else: did we actually get on the bus? A run where the
        # guard band suppressed most transmissions says nothing about whether
        # the inhibit works -- it says this host cannot hit the slot. Reporting
        # that as "the inhibit leaked" would be the single most misleading
        # thing this tool could do.
        st = self.inh.status()
        per_frame = 2 if self.args.strategy == "bracket" else 1
        want = self.armed_frames * per_frame
        duty = (self.tx_count / want) if want else 0.0
        if want and duty < 0.5:
            lines.append("TRANSMIT STARVED - only %d of ~%d scheduled frames went "
                         "out (%.1f%%); the guard band suppressed %d. Nothing "
                         "below is a result about the truck -- it is a result "
                         "about this host's timing. Fix that first."
                         % (self.tx_count, want, 100 * duty,
                            st["suppressed_guard"]))
        elif want:
            lines.append("TRANSMIT OK   - %d of ~%d scheduled frames went out "
                         "(%.1f%%)." % (self.tx_count, want, 100 * duty))

        if self.args.no_inverter:
            # There is no inverter powered up to obey or refuse anything, so
            # every inhibit verdict below would be an artefact of its silence
            # rather than a result. Say what this run CAN answer -- whether we
            # coexisted on a real bus with the real VCM -- and claim nothing
            # else. The failure that matters here is a same-ID collision, which
            # shows up as error frames, not as torque.
            lines.append("NO INVERTER  - transmit-integrity test only. The GENE "
                         "family is off the bus (fuse out), so nothing could "
                         "obey or refuse the command and NO inhibit claim is "
                         "made either way.")
            if self.error_frames:
                lines.append("BUS ERRORS   - %d error frames. This is the same-ID "
                             "collision signature and it is the one outcome that "
                             "can hurt the truck. Stop and investigate."
                             % self.error_frames)
            elif want:
                lines.append("BUS CLEAN    - 0 error frames, %d repeated counters "
                             "across %d transmitted frames. We shared 0x051 with "
                             "the VCM without provoking the controller."
                             % (st["counter_repeats"], self.tx_count))
            if self.attempt_seen:
                lines.append("NOTE         - the VCM commanded up to %d counts "
                             "while armed. With the inverter unpowered that is "
                             "not an inhibit result, but it does mean the truck "
                             "asked for generator torque during this run."
                             % self.attempt_peak)
            return lines

        if not self.attempt_seen:
            lines.append("NOT TESTED  - the VCM never commanded generator torque "
                         "while armed, so nothing was inhibited. This is the "
                         "expected result if the truck never tried to start the "
                         "engine during the run.")
        elif self.engine_started:
            lines.append("INHIBIT FAILED - the engine started while armed "
                         "(inverter feedback peaked at %d counts). We disarmed "
                         "and handed back." % self.fb_peak_while_armed)
        elif self.fb_peak_while_armed <= 100:
            lines.append("INHIBIT HELD - the VCM commanded up to %d counts while "
                         "armed and the inverter's own feedback never exceeded "
                         "%d counts."
                         % (self.attempt_peak, self.fb_peak_while_armed))
        else:
            lines.append("INHIBIT LEAKED - the VCM commanded up to %d counts and "
                         "the inverter reported up to %d counts of real torque."
                         % (self.attempt_peak, self.fb_peak_while_armed))

        if self.disarm_at is None:
            lines.append("RECOVERY     - not assessed (%s)."
                         % ("never armed" if self.armed_since is None
                            else "still armed at exit"))
            return lines

        # The distinction that matters: after we stopped transmitting, did the
        # generator go back to obeying the VCM, or is it sulking?
        alive = m.gene_alive(now)
        if not alive:
            lines.append("RECOVERY     - LATCHED / DEAD. 0x471 is not being "
                         "transmitted. The inverter has reset or dropped off "
                         "the bus; this is not an inhibit result, it is damage "
                         "to the CAN state. Key cycle and re-check.")
        elif m.fault == 0xCA:
            lines.append("RECOVERY     - LATCHED FAULT. 0x617 B7 is still 0xCA "
                         "after disarm.")
        elif m.cmd is not None and abs(m.cmd) > 100 and m.fb is not None \
                and abs(m.fb) < 100:
            lines.append("RECOVERY     - LATCHED. The VCM is commanding %d counts "
                         "and the inverter is producing %d. It is being asked "
                         "and not answering." % (m.cmd, m.fb))
        elif m.cmd is not None and m.fb is not None and abs(m.cmd) > 100:
            lines.append("RECOVERY     - GOOD. Command %d, feedback %d: the "
                         "inverter is tracking the VCM again."
                         % (m.cmd, m.fb))
        else:
            lines.append("RECOVERY     - INCONCLUSIVE. The VCM has not asked the "
                         "generator for anything since disarm (command %s), so "
                         "there is nothing for the inverter to answer. Try "
                         "again with a start request after disarming."
                         % (m.cmd,))

        if st["rate_tripped"]:
            lines.append("TX CEILING    - the transmit rate ceiling tripped and "
                         "latched the transmitter off. Something fed us our own "
                         "frames, or the loop ran away. Do not read anything "
                         "else here as a result.")
        if self.echoes:
            lines.append("NOTE         - %d of our own frames came back on this "
                         "interface and were suppressed in software. A real "
                         "build needs controller-level echo suppression."
                         % self.echoes)
        if self.inh.sync.counter_repeats > 20:
            lines.append("NOTE         - %d repeated rolling counters seen. Either "
                         "we are hearing ourselves, or something else is also "
                         "transmitting 0x051."
                         % self.inh.sync.counter_repeats)
        if m.fault_ca_seen:
            lines.append("NOTE         - 0x617 B7 hit 0xCA at some point in this "
                         "run. Pull codes.")
        if self.error_frames:
            lines.append("NOTE         - %d CAN error frames were seen. Check the "
                         "controller state below." % self.error_frames)
        return lines

    # -- timing report -----------------------------------------------------

    def timing_report(self):
        st = self.inh.status()
        print("\n-- timing on this hardware --")
        print("  locked            : %s" % st["locked"])
        print("  slot period       : %.4f ms" % st["period_ms"])
        print("  phase error p99   : %.4f ms" % st["phase_err_p99_ms"])
        print("  guard band        : %s"
              % (("%.4f ms" % st["guard_ms"]) if st["guard_ms"] else "n/a"))
        ok_l, why_l = self.inh.can_lead()
        ok_t, why_t = self.inh.can_trail()
        print("  lead  viable here : %-5s (%s)" % (ok_l, why_l))
        print("  trail viable here : %-5s (%s)" % (ok_t, why_t))
        if self.reply_latency:
            r = sorted(self.reply_latency)
            print("  reply latency     : median %.3f ms  p99 %.3f ms  (recv -> send)"
                  % (r[len(r) // 2] * 1e3, r[int(0.99 * (len(r) - 1))] * 1e3))
            print("                      this is the window a newest-frame-wins")
            print("                      receiver can still obey the VCM in")
        if self.send_call:
            sc = sorted(self.send_call)
            print("  bus.send() call   : median %.3f ms  p99 %.3f ms"
                  % (sc[len(sc) // 2] * 1e3, sc[int(0.99 * (len(sc) - 1))] * 1e3))
        print("  frames %s: %d  (lead %d, trail %d)"
              % ("transmitted" if self.live else "that WOULD have been sent",
                 self.tx_count, st["sent_lead"], st["sent_trail"]))
        print("  suppressed        : unlocked=%d guard=%d late=%d stale=%d rate=%d"
              % (st["suppressed_unlocked"], st["suppressed_guard"],
                 st["suppressed_late"], st["suppressed_stale"],
                 st["suppressed_rate"]))
        dc = self.dclock
        print("  sync clock        : %s"
              % ("host receive time (--host-clock)" if not dc.usable
                 else "adapter hardware timestamp, %d samples, %d re-seeds"
                      % (len(dc.offsets), dc.n_reset)))
        print("  payload collisions: %d   (genuine VCM frames carrying a "
              "payload we had just sent)" % self.payload_collisions)
        print("  own frames echoed : %d   repeated counters seen: %d"
              % (self.echoes, st["counter_repeats"]))
        h = self.bus_health()
        if h:
            print("  CAN controller    : %s" % (h["state"] or "unknown"))

    def preflight(self):
        """Go / no-go, in the terms that decide it, with settings to use."""
        st = self.inh.status()
        ok_l, why_l = self.inh.can_lead()
        ok_t, why_t = self.inh.can_trail()
        p99 = st["phase_err_p99_ms"]
        sc = sorted(self.send_call) if self.send_call else []
        print("\n-- preflight --")
        # Lock and phase error are LEAD criteria and must not veto a trail-only
        # run. A leading frame is placed at a predicted future instant, so the
        # prediction has to be good. A trailing frame answers a frame already
        # received -- inhibit.py gates the lock check on lead alone, and
        # can_trail() documents the phase error as "the wrong quantity
        # entirely" for trail, having once been an expensive bug. Applying them
        # to trail is that same mistake at the reporting layer: on the truck,
        # 2026-09-06, this printed NO-GO while its own mechanism had 501 of 501
        # trail frames going out with nothing suppressed.
        fatal = []          # nothing can run
        lead_only = []      # lead cannot run; trail is unaffected

        if sc:
            call_p99 = sc[int(0.99 * (len(sc) - 1))] * 1e3
            if call_p99 > self.args.tx_latency:
                fatal.append("bus.send() p99 %.3f ms exceeds the assumed "
                             "--tx-latency %.3f ms; raise it"
                             % (call_p99, self.args.tx_latency))
        if not (ok_t or ok_l):
            fatal.append("neither polarity is viable: trail=%s lead=%s"
                         % (why_t, why_l))
        if not st["locked"]:
            lead_only.append("not locked onto the VCM's slot")
        if p99 > 2.0:
            lead_only.append("phase error p99 %.2f ms is very high" % p99)

        if fatal:
            print("  NO-GO:")
            for x in fatal:
                print("    - %s" % x)
        elif ok_t:
            print("  GO on trail.")
            if lead_only or not ok_l:
                print("  lead is NOT available:")
                for x in lead_only:
                    print("    - %s" % x)
                if not ok_l:
                    print("    - %s" % why_l)
                print("  These bear on prediction only. A trailing frame "
                      "answers a frame")
                print("  already received, so they do not affect it. Stay on "
                      "--strategy trail.")
            else:
                print("  lead is available too -- --strategy bracket is an option.")
        else:
            print("  NO-GO:")
            print("    - trail is not viable: %s" % why_t)
            for x in lead_only:
                print("    - %s" % x)
        if self.reply_latency:
            r = sorted(self.reply_latency)
            rp99 = r[int(0.99 * (len(r) - 1))] * 1e3
            print("  reply latency p99 %.3f ms = %.1f%% of a slot. Against a "
                  "receiver that\n  simply obeys the newest frame, that is the "
                  "share of the time the VCM\n  can still be obeyed."
                  % (rp99, 100 * rp99 / st["period_ms"]))

    def write_meta(self):
        """Record what produced this run, next to the data it produced.

        Timing here is a property of the machine, not of the truck: the numbers
        measured on the desktop this was written on do not carry to a laptop.
        A capture that arrives without the host, the interface and the measured
        latencies attached cannot be interpreted later.
        """
        target = self.args.csv or self.args.log
        if not target:
            return
        import json
        import platform
        p = Path(str(Path(target).with_suffix("")) + ".meta.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        st = self.inh.status()
        r = sorted(self.reply_latency)
        sc = sorted(self.send_call)

        def pct(v, q):
            return (v[int(q * (len(v) - 1))] * 1e3) if v else None

        meta = {
            "tool": "gen_inhibit/vehicle_runner.py",
            "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "args": vars(self.args),
            "interface_desc": getattr(self, "_iface_desc", None),
            "live": self.live,
            "timing": {
                "locked": st["locked"],
                "period_ms": st["period_ms"],
                "phase_err_p99_ms": st["phase_err_p99_ms"],
                "guard_ms": st["guard_ms"],
                "reply_latency_median_ms": pct(r, 0.5),
                "reply_latency_p99_ms": pct(r, 0.99),
                "send_call_p99_ms": pct(sc, 0.99),
                "sync_clock": ("host" if not self.dclock.usable
                               else "adapter_hw_timestamp"),
                "sync_clock_reseeds": self.dclock.n_reset,
                "payload_collisions": self.payload_collisions,
                "bg_err_rate_per_s": self.bg_err_rate,
                "err_trip_count": self.err_trip_count,
            },
            "counts": {
                # 'tx' means frames actually put on the wire. In observe
                # mode the runner still counts what it WOULD have sent, which
                # is the useful preflight number -- but recording that under
                # 'tx' on a run that transmitted nothing invites exactly the
                # wrong reading months later. Kept separate.
                "tx": self.tx_count if self.live else 0,
                "tx_would_send": self.tx_count,
                "armed_frames": self.armed_frames,
                "echoes": self.echoes,
                "error_frames": self.error_frames,
                "counter_repeats": st["counter_repeats"],
                "rate_tripped": st["rate_tripped"],
                "suppressed": {k: st[k] for k in st if k.startswith("suppressed")},
            },
            "events": [[round(t, 3), x] for t, x in self.events],
            "verdict": self.verdict() if self.live else None,
        }
        with open(p, "w", encoding="ascii") as f:
            json.dump(meta, f, indent=1)
        print("  run metadata -> %s" % p)

    def write_csv(self):
        if not self.args.csv or not self.rows:
            return
        p = Path(self.args.csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="ascii") as f:
            w = csv.writer(f)
            w.writerow(["t", "cmd", "fb", "rpm", "rpm_ref", "state_byte",
                        "mode", "sys", "fault", "inhibit_state", "tx_count"])
            w.writerows(self.rows)
        print("\n  %d rows -> %s" % (len(self.rows), p))

    # -- main --------------------------------------------------------------

    def run(self):
        self.open_and_identify()
        self.open_log()
        self.open_error_log()
        t = threading.Thread(target=self.can_loop, daemon=True)
        t.start()

        print("\nwarming up (%.1f s) to lock onto the VCM's slot..."
              % self.args.warmup)
        err_before = self.error_frames
        t_warm = _now()
        time.sleep(self.args.warmup)

        # Calibrate the error trip against THIS bus, right now, while we are
        # provably silent -- nothing has been transmitted at this point in the
        # run. A truck whose bus is quiet gets a tight trip; a truck like this
        # one, which idles at ~0.2 error frames/s with the generator running,
        # gets a trip set above its own noise instead of firing on it.
        span = max(_now() - t_warm, 0.001)
        self.bg_err_rate = (self.error_frames - err_before) / span
        # Warmup can only RAISE the trip, never lower it below the floor.
        self.err_trip_count = max(
            self.args.err_trip,
            int(self.bg_err_rate * ERR_WINDOW * ERR_FACTOR) + 1)
        print("  background error frames: %d in %.1f s = %.3f/s"
              % (self.error_frames - err_before, span, self.bg_err_rate))
        print("  error trip set at %d in %.0f s (%.2f/s)"
              % (self.err_trip_count, ERR_WINDOW,
                 self.err_trip_count / ERR_WINDOW))

        if not self.live:
            # Arm the core even though nothing can reach the bus. Without this
            # the run measures lock quality and nothing else; with it, the
            # scheduling and the guard band run exactly as they would live, so
            # "frames that WOULD have been sent" is a real answer rather than a
            # structural zero. allow_running because we are not transmitting,
            # so the engage interlock has nothing to protect here.
            with self.lock:
                self.inh.arm(allow_running=True)
            self._armed_at_mono = None      # no dead-man; we cannot transmit
            print("  measuring for %.1f s with the core armed and the transmit "
                  "path disabled..." % self.args.warmup)
            self._watch(self.args.warmup)

        self.timing_report()
        self.preflight()

        if self.args.preflight:
            print("\n--preflight given: stopping here. Nothing was transmitted.")
        elif not self.live:
            print("\nOBSERVE MODE -- nothing was transmitted. The numbers above")
            print("say whether a live run would work on this hardware.")
            if self.args.observe_seconds > 0:
                print("Watching for %.0f s.\n" % self.args.observe_seconds)
            else:
                print("Watching. Ctrl-C to stop.\n")
            # A bounded observe run finishes on its own and therefore writes its
            # verdict and meta.json. An unbounded one has to be killed, and a
            # killed run writes neither -- which is how the passive control of
            # 2026-09-06 came within one grep of losing its error-frame data.
            t_end = (_now() + self.args.observe_seconds
                     if self.args.observe_seconds > 0 else None)
            try:
                while not self.stop.is_set():
                    if t_end is not None and _now() >= t_end:
                        break
                    self._watch(1.0)
            except KeyboardInterrupt:
                pass
        elif self.args.auto_arm_after is not None:
            self.scripted()
        else:
            self.interactive()

        self.stop.set()
        t.join(timeout=2.0)
        try:
            self.bus.shutdown()
        except Exception:
            pass
        self.close_log()
        self.close_error_log()
        if self.live:
            print("\n=== verdict ===")
            for line in self.verdict():
                print("  " + line)
            self.timing_report()
        self.write_csv()
        self.write_meta()

    def arm_preconditions(self, now, max_age=0.5):
        """Is the bus ALIVE right now, as opposed to recently?

        Inhibitor.arm() reads engine_running() out of the Monitor, which holds
        last-known values with no notion of age. A bus that fell silent after
        the fingerprint passed therefore still answers every question arm()
        asks, and it would arm on a stale picture and start transmitting into a
        dead or half-booted bus.

        That is not hypothetical when the tool is started before the key: the
        fingerprint can pass on traffic from a fuse being reinserted, or on a
        first key-on that is then cycled again. Freshness is the missing check,
        so it is made explicitly here against the receive timestamps.
        """
        t = self.sync.last_seen
        if t is None:
            return False, "no 0x051 seen yet"
        if now - t > max_age:
            return False, ("0x051 last seen %.1f s ago -- the bus is not live"
                           % (now - t))
        if not self.args.no_inverter and not self.mon.gene_alive(now, max_age):
            return False, "0x471 is not arriving -- the GENE inverter is not up"
        return True, "bus is live"

    def try_arm(self, window=60.0, period=0.5):
        """Arm as soon as it is safe to, rather than once at a fixed instant.

        A one-shot attempt is fine on a bench where the truck is a program. On
        a real key-on the modules arrive in their own order and at their own
        pace, so a single try lands wherever it lands. Retrying turns 'not
        ready yet' into a wait instead of a failed run -- which matters most
        when the run cost a key cycle to set up.
        """
        a = self.args
        t_end = _now() + window
        said = None
        while not self.stop.is_set():
            now = _now()
            ready, why = self.arm_preconditions(now)
            if ready:
                with self.lock:
                    ok, why = self.inh.arm(allow_running=a.allow_running)
                if ok:
                    return True, why
            if why != said:
                self.event("waiting to arm: %s" % why)
                said = why
            if now > t_end:
                return False, why
            self._watch(period)
        return False, "stopped"

    def scripted(self):
        """Hands-free arm/disarm on a timer. For the bench, and for a run on the
        truck where you would rather be watching the engine than a keyboard."""
        a = self.args
        print("\nSCRIPTED: arming in %.1f s, disarming %.1f s later.\n"
              % (a.auto_arm_after, a.auto_disarm_after))
        self._watch(a.auto_arm_after)
        ok, why = self.try_arm(window=a.arm_window)
        if not ok:
            self.event("ARM REFUSED: %s" % why)
        else:
            self.armed_since = _now()
            self._armed_at_mono = self.armed_since
            self.event("ARMED")
            self._watch(a.auto_disarm_after)
            with self.lock:
                self.inh.disarm()
            self.disarm_at = _now()
            self.event("DISARMED")
        print("\n  watching recovery for %.0f s..." % a.recovery)
        self._watch(a.recovery)

    def _watch(self, seconds):
        """Live status. Overwrites one line on a terminal; prints one line a
        second when redirected, so a captured run stays readable."""
        tty = sys.stdout.isatty()
        t_end = _now() + seconds
        n = 0
        while _now() < t_end and not self.stop.is_set():
            with self.lock:
                line = self.mon.line()
            if tty:
                print("\r  %-8s %s" % (self.inh.state, line), end="", flush=True)
            elif n % 4 == 0:
                print("  %-8s %s" % (self.inh.state, line), flush=True)
            n += 1
            time.sleep(0.25)
        if tty:
            print()

    def interactive(self):
        print("\nLIVE MODE. Enter arms, Enter again disarms, Ctrl-C exits.")
        print("It will refuse to arm while the generator is running.\n")
        try:
            while not self.stop.is_set():
                with self.lock:
                    print("  " + self.mon.line())
                input("  press Enter to ARM (or Ctrl-C to quit) ")
                ready, rwhy = self.arm_preconditions(_now())
                if not ready:
                    print("  NOT READY: %s" % rwhy)
                    time.sleep(0.5)
                    continue
                with self.lock:
                    ok, why = self.inh.arm(allow_running=self.args.allow_running)
                if not ok:
                    print("  REFUSED: %s\n" % why)
                    time.sleep(0.5)
                    continue
                self.armed_since = _now()
                self._armed_at_mono = self.armed_since
                self.event("ARMED")
                try:
                    input("  ARMED -- press Enter to DISARM ")
                finally:
                    with self.lock:
                        self.inh.disarm()
                    self.disarm_at = _now()
                    self.event("DISARMED")
                print("\n  watching recovery for %.0f s..." % self.args.recovery)
                self._watch(self.args.recovery)
                for line in self.verdict():
                    print("  " + line)
                print()
        except (KeyboardInterrupt, EOFError):
            with self.lock:
                self.inh.disarm()
            if self.disarm_at is None and self.armed_since is not None:
                self.disarm_at = _now()


def main():
    ap = argparse.ArgumentParser(
        description="Generator inhibitor, on-vehicle runner (observe by default)")
    ap.add_argument("--preflight", action="store_true",
                    help="identify the bus, lock on, print go/no-go, exit. "
                         "Transmits nothing. Run this first, every time.")
    ap.add_argument("--live", action="store_true",
                    help="actually transmit. Without this it only listens.")
    ap.add_argument("--interface", default="auto",
                    help="auto | socketcan | kvaser | pcan | ixxat | gs_usb | "
                         "udp_multicast (bench rehearsal)")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--port", type=int, default=43217,
                    help="udp_multicast transport only")
    ap.add_argument("--bitrate", type=int, default=500000)
    ap.add_argument("--observe-seconds", type=float, default=0.0, metavar="SEC",
                    help="in observe mode (no --live), stop after this long and "
                         "write the verdict and meta.json. 0 = watch until "
                         "interrupted, which writes neither.")
    ap.add_argument("--err-trip", type=int, default=ERR_MIN_TRIP, metavar="N",
                    help="abort transmission if this many CAN error frames "
                         "arrive within %.0f s while armed. The floor is set "
                         "from measurement, not taste: see ERR_MIN_TRIP."
                         % ERR_WINDOW)
    ap.add_argument("--arm-window", type=float, default=60.0, metavar="SEC",
                    help="with --auto-arm-after: keep retrying to arm for this "
                         "long while the bus settles, instead of one attempt at "
                         "a fixed instant.")
    ap.add_argument("--wait-for-bus", type=float, default=0.0, metavar="SEC",
                    help="start before the truck is on: keep re-listening until "
                         "the powertrain fingerprint appears, then continue. "
                         "0 disables (default); a negative value waits forever. "
                         "Saves a key cycle and captures from the first frame.")
    ap.add_argument("--host-clock", action="store_true",
                    help="synchronise on the host receive time instead of the "
                         "adapter's hardware timestamp. Measurably worse on "
                         "Kvaser over USB (p99 phase error 8-48 ms vs 2.8 ms); "
                         "for an interface whose timestamps are untrustworthy.")
    ap.add_argument("--no-inverter", action="store_true",
                    help="the generator inverter's 12V fuse is OUT, so the GENE "
                         "family (0x054, 0x470-0x482) is absent. Requires 0x471 "
                         "to be absent, disables the 0x471-quiet safety trip, "
                         "and reports a transmit-integrity result instead of an "
                         "inhibit result. Makes no claim about the inhibit.")
    ap.add_argument("--force-bus", action="store_true",
                    help="run even if the bus fingerprint does not match")
    ap.add_argument("--strategy", default="trail",
                    choices=("lead", "trail", "bracket"))
    ap.add_argument("--payload", default="mirror", choices=("mirror", "idle"))
    ap.add_argument("--target", type=int, default=0,
                    help="torque counts to hold once armed (0 = inhibit). "
                         "Non-zero COMMANDS torque and is refused on a live bus "
                         "without --allow-nonzero-target.")
    ap.add_argument("--allow-nonzero-target", action="store_true",
                    help="permit a non-zero --target on a live bus. This makes "
                         "the tool command generator torque rather than inhibit "
                         "it. Bench use only unless you mean exactly that.")
    ap.add_argument("--lead", type=float, default=2.0, help="ms")
    ap.add_argument("--trail", type=float, default=0.3, help="ms")
    ap.add_argument("--tx-latency", type=float, default=0.5,
                    help="ms, assumed queue-to-wire latency; widens the guard")
    ap.add_argument("--identify-seconds", type=float, default=2.0)
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--recovery", type=float, default=20.0,
                    help="s to watch the generator after each disarm")
    ap.add_argument("--allow-running", action="store_true",
                    help="permit arming with the generator already running. "
                         "This sheds the engine's whole load in one frame.")
    ap.add_argument("--auto-arm-after", type=float, default=None,
                    help="arm this many seconds after warmup, hands-free")
    ap.add_argument("--auto-disarm-after", type=float, default=15.0,
                    help="with --auto-arm-after: seconds to stay armed")
    ap.add_argument("--csv", default=None, help="write a decoded signal log here")
    ap.add_argument("--log", default=None,
                    help="write a candump-format raw capture here; canre parses "
                         "it directly")
    ap.add_argument("--log-iface", default="can0",
                    help="interface name written into the candump log")
    ap.add_argument("--max-armed", type=float, default=120.0,
                    help="s; dead-man timer, disarms if left armed this long")
    ap.add_argument("--start-abort-rpm", type=int, default=300,
                    help="disarm if the engine reaches this RPM while armed "
                         "(debounced 0.3 s); the inhibit has failed by then")
    a = ap.parse_args()

    # The whole point of this tool is to command ZERO torque. --target exists
    # so the bench can prove the transmit path carries a value at all, and
    # test_core exercises a positive target for that reason. On a vehicle it
    # inverts the tool: a positive target asks the generator inverter for real
    # torque, which on a truck with the engine able to crank is the opposite of
    # an inhibit. Nothing downstream re-checks it -- _payload() writes B1/B2
    # from this number unconditionally -- so this is the only place it can be
    # caught.
    if a.target != 0:
        if not (-32768 <= a.target <= 32767):
            raise SystemExit(
                "--target %d does not fit the 16-bit centred encoding and would "
                "wrap silently to some other torque value. Refusing."
                % a.target)
        if a.live and not a.allow_nonzero_target:
            raise SystemExit(
                "--target %d would COMMAND %+d counts of generator torque on a "
                "live bus.\nThis tool exists to command zero; that is what "
                "'inhibit' means here.\nIf you genuinely intend to command "
                "torque, add --allow-nonzero-target." % (a.target, a.target))
        print("*** WARNING: --target %+d -- this run COMMANDS torque, it does "
              "NOT inhibit ***" % a.target)

    if a.live:
        print("*** LIVE: this will transmit 0x051 onto the powertrain bus ***")
        print("*** The VCM transmits the same ID. Read the header of this ***")
        print("*** file if you have not.                                  ***")
    r = Runner(a)
    try:
        r.run()
    finally:
        # run() shuts the bus down on its own way out, but the fingerprint
        # refusal and the no-interface exit both raise SystemExit before
        # reaching it. A Kvaser channel left open that way stays claimed, and
        # the next invocation cannot have it -- which at the truck reads as a
        # dead dongle. Shutting down here is idempotent: python-can tolerates a
        # second shutdown, and self.bus only exists once a bus was opened.
        bus = getattr(r, "bus", None)
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    main()
