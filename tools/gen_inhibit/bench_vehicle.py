"""
A truck-shaped rig to rehearse `vehicle_runner.py` against before the truck.

Puts a realistic slice of the powertrain bus on a virtual segment -- enough
that the runner's content-based bus identification passes, its monitor has
every signal it looks for, and its verdict logic can be exercised against all
three outcomes we care about telling apart:

  --receiver counter | last | per_frame
        how the modelled GENE MCU consumes 0x051. `counter` validates the
        rolling counter (the inhibit wins outright); `last` obeys the newest
        frame; `per_frame` acts on all of them.

  --latch-on-dup
        the pessimistic case. The modelled inverter notices a repeated
        counter, decides the bus is misbehaving, latches a fault, stops
        transmitting 0x471 and never comes back. This is what "generator
        inverter latched a bad CAN state" would look like, and it exists so
        you can confirm the runner reports LATCHED rather than success before
        you find out on the vehicle.

  --no-start
        the VCM never tries to start the engine, so the runner should report
        NOT TESTED rather than claiming a win it did not earn.

Run this in one terminal and vehicle_runner.py in another:

    python3.13 .../bench_vehicle.py --receiver counter
    python3.13 .../vehicle_runner.py --interface udp_multicast \\
        --channel 239.0.2.4 --port 43217 --live
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import argparse
import time

import can
import gi_vbus as busmod

# -- clock ------------------------------------------------------------------
# time.monotonic() is GetTickCount64() on Windows: 15.625 ms resolution, which
# is coarser than the 10 ms slot this tool has to hit. Every interval measured
# here then quantises to a tick, which shows up as a phase error of half a tick
# (~8 ms) and a reply latency that reads as exactly 0.000 ms. perf_counter() is
# QueryPerformanceCounter() on Windows and clock_gettime(MONOTONIC) on Linux --
# monotonic on both, sub-microsecond on both -- so it is the correct clock for
# every interval in this file. Measured on the Windows laptop, 2026-09-06.
_now = time.perf_counter


SEGMENT = "239.0.2.4"
PORT = 43217
CMD, FB, RPM, MODE, SYS, FAULT = 0x051, 0x471, 0x054, 0x644, 0x053, 0x617
PT_EXT = 0x18EFC000          # so the "extended frames present" check passes


def c16(v):
    return (v + 32768) & 0xFFFF


class Gene:
    """The generator inverter, as far as the bus can see."""

    def __init__(self, receiver, latch_on_dup):
        self.receiver = receiver
        self.latch_on_dup = latch_on_dup
        self.last_counter = None
        self.accepted = 0
        self.mailbox = 0
        self.torque = 0
        self.rpm = -1
        self.latched = False
        self.fault = 0xC8
        self.mode = 0xF0
        self.cranking_since = None

    def deliver(self, t, d):
        if self.latched:
            return
        c = d[5] & 0x0F
        tq = ((d[2] << 8) | d[1]) - 32768
        if self.last_counter is not None:
            step = (c - self.last_counter) % 16
            if step == 0 and self.latch_on_dup:
                self.latched = True
                self.fault = 0xCA
                self.torque = 0
                return
            if self.receiver == "counter" and step != 1:
                return
        self.last_counter = c
        self.accepted += 1
        self.mailbox = tq

    def tick(self, t, dt):
        if self.latched:
            self.torque = 0
            self.rpm = -1
            self.mode = 0xF0
            return
        # first-order lag toward the command it accepted
        self.torque += (self.mailbox - self.torque) * min(1.0, dt / 0.02)
        cranking = self.torque > 150
        if cranking:
            if self.cranking_since is None:
                self.cranking_since = t
            self.mode = 0x90
        else:
            self.cranking_since = None
            self.mode = 0xF0 if self.rpm < 0 else 0x90
        # sustained motoring torque turns the engine over
        if self.cranking_since is not None and t - self.cranking_since > 0.30:
            self.rpm = 800 if self.rpm < 0 else min(1400, self.rpm + 400 * dt)
            self.mode = 0xF0
        elif self.rpm >= 0 and self.torque > -50 and self.mailbox == 0:
            self.rpm = max(-1, self.rpm - 900 * dt) if self.rpm < 100 else self.rpm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receiver", default="counter",
                    choices=("counter", "last", "per_frame"))
    ap.add_argument("--latch-on-dup", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--start-at", type=float, default=12.0,
                    help="s after launch that the VCM tries to start the engine")
    ap.add_argument("--gen-seconds", type=float, default=8.0,
                    help="how long the VCM keeps the generator loaded")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--channel", default=SEGMENT)
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()

    b = busmod.open_bus(argparse.Namespace(transport="virtual",
                                           channel=a.channel, port=a.port,
                                           bitrate=500000),
                        receive_own_messages=False)
    gene = Gene(a.receiver, a.latch_on_dup)
    print("bench vehicle on %s:%d  receiver=%s latch_on_dup=%s start_at=%s"
          % (a.channel, a.port, a.receiver,
             a.latch_on_dup, "never" if a.no_start else a.start_at))

    # This transport loops our own frames back to us, so every frame we send
    # would otherwise reach the modelled inverter twice -- which looks exactly
    # like a duplicate rolling counter and made --latch-on-dup fire before the
    # runner had even connected.
    mine = {}
    t0 = _now()
    counter = 0
    k = 0
    last = t0
    try:
        while True:
            now = _now()
            el = now - t0
            if el > a.duration:
                break
            # drain anything injected onto the segment
            while True:
                m = b.recv(timeout=0.0)
                if m is None:
                    break
                if m.arbitration_id == CMD and len(m.data) >= 6:
                    key = bytes(m.data)
                    if mine.pop(key, None) is not None:
                        continue
                    gene.deliver(now, m.data)

            due = t0 + k * 0.010
            if now < due:
                time.sleep(max(0.0, min(0.002, due - now)))
                continue

            # what the VCM wants
            if a.no_start or el < a.start_at:
                cmd, rpm_ref, b0 = 0, -1, 0x08
            elif el < a.start_at + 1.0:
                cmd, rpm_ref, b0 = 328, -1, 0x0B
            elif el < a.start_at + a.gen_seconds:
                cmd, rpm_ref, b0 = -1200, 800, 0x0B
            else:
                cmd, rpm_ref, b0 = 0, -1, 0x08

            payload = bytes((b0, c16(cmd) & 0xFF, c16(cmd) >> 8,
                             c16(rpm_ref) & 0xFF, c16(rpm_ref) >> 8, counter))
            b.send(can.Message(arbitration_id=CMD, is_extended_id=False,
                               data=payload, dlc=6))
            mine[payload] = now
            if len(mine) > 64:
                mine = {k: v for k, v in mine.items() if now - v < 0.5}
            gene.deliver(now, payload)          # the real VCM frame reaches it too
            gene.tick(now, now - last)
            last = now

            t = int(round(gene.torque))
            if not gene.latched:
                b.send(can.Message(arbitration_id=FB, is_extended_id=False,
                                   data=bytes((c16(t) & 0xFF, c16(t) >> 8,
                                               0, 0, 0, 0, 0)), dlc=7))
            r = int(round(gene.rpm))
            b.send(can.Message(arbitration_id=RPM, is_extended_id=False,
                               data=bytes((c16(r) & 0xFF, c16(r) >> 8,
                                           0, 0, 0, 0, 0, 0)), dlc=8))
            b.send(can.Message(arbitration_id=MODE, is_extended_id=False,
                               data=bytes((0, 0, 0, 0, 0, 0, gene.mode, 0)), dlc=8))
            if k % 10 == 0:
                b.send(can.Message(arbitration_id=FAULT, is_extended_id=False,
                                   data=bytes((0, 0, 0, 0, 0, 0, 0x50,
                                               gene.fault)), dlc=8))
                b.send(can.Message(arbitration_id=SYS, is_extended_id=False,
                                   data=bytes((0x34 if gene.rpm >= 0 else 0x04,
                                               0x00)), dlc=2))
                b.send(can.Message(arbitration_id=PT_EXT, is_extended_id=True,
                                   data=bytes(8), dlc=8))

            counter = (counter + 1) & 0x0F
            k += 1
            if k % 200 == 0:
                print("\r  t=%6.1f  vcm_cmd=%6d  gene_tq=%6d  rpm=%5d  "
                      "accepted=%d %s" % (el, cmd, t, r, gene.accepted,
                                          "LATCHED" if gene.latched else ""),
                      end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nbench vehicle stopping. accepted=%d latched=%s"
              % (gene.accepted, gene.latched))
        b.shutdown()


if __name__ == "__main__":
    main()
