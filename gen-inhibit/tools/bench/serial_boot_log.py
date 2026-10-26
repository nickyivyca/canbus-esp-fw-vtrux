"""Timestamped console capture from the WiCAN's USB-serial/JTAG port.

WHY NOT `idf.py monitor`. The monitor wants a terminal, resets the chip on
attach, and interleaves its own chatter with the device's. For diagnosing a boot
that goes wrong we need three things it does not give: a host timestamp on every
line so console output can be lined up against HTTP polling from another
process, a capture that starts BEFORE the reset that provokes the fault, and no
reset of its own at attach time.

THE ATTACH MUST NOT RESET. On the ESP32-C3 the console is USB-serial/JTAG, where
DTR and RTS are wired to reset and boot select. pyserial asserts both on open by
default, which reboots the chip -- and rebooting the device you are trying to
observe boot is a way to lose the very event you came for. So the port is
configured before it is opened and both lines are left deasserted.

    py -3.11 gen-inhibit/tools/bench/serial_boot_log.py \
        --out runs/reboot_hang_console.log --seconds 120

--reset IS OPT-IN, AND THE DEFAULT STAYS OFF. Everything above is why. But after a
flash the device has already booted -- the flash's own hard reset happens before any
logger can attach, and COM4 can only be held by one process, so esptool cannot reset the
chip while this tool listens. --reset pulses RTS once the capture is already running, so
the banner lands inside the log. Use it when you need a boot you have to provoke; leave
it off when you are diagnosing a boot that provokes itself.

Lines are flushed as they arrive, so another process can tail the file while
this runs. Decoding is latin-1 and never fails: a boot log can contain ROM
garbage at the wrong baud, and a decode error mid-capture would throw away the
part that matters.
"""

import argparse
import datetime
import io
import os
import sys
import time

import serial

# Same directory, so the script dir that Python already put on sys.path finds it.
from redact_secrets import redact
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", required=True,
                    help="output path; a bare name lands in runs/")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--reset", action="store_true",
                    help="pulse RTS after the capture starts, to provoke a "
                         "boot that has already happened (e.g. after a flash). "
                         "Off by default: see the module docstring.")
    args = ap.parse_args()

    out = args.out
    if not os.path.isabs(out) and os.path.dirname(out) in ("", "runs"):
        out = os.path.join(OUT_DIR, os.path.basename(out))
    d = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(d):
        os.makedirs(d)

    ser = serial.Serial()
    ser.port = args.port
    ser.baudrate = args.baud
    ser.timeout = 0.2
    # Deassert BEFORE open so the open itself cannot pulse reset/boot.
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.dtr = False
    ser.rts = False

    t0 = time.time()
    fh = io.open(out, "w", encoding="utf-8", newline="\n")
    fh.write("# %s %s @ %d, host start %s\n"
             % (args.port, "console capture", args.baud,
                datetime.datetime.now().isoformat(timespec="milliseconds")))
    fh.flush()
    print("logging %s -> %s for %.0f s" % (args.port, out, args.seconds))
    sys.stdout.flush()

    if args.reset:
        #
        # AFTER the header is written and the read loop is about to start, so the
        # banner cannot land before anything is listening -- which is the whole
        # failure this flag exists to avoid.
        #
        ser.rts = True
        time.sleep(0.1)
        ser.rts = False
        fh.write("# host asserted RTS to reset at %s\n"
                 % datetime.datetime.now().isoformat(timespec="milliseconds"))
        fh.flush()
        print("reset pulsed")
        sys.stdout.flush()

    pending = b""
    lines = 0
    try:
        while time.time() - t0 < args.seconds:
            chunk = ser.read(4096)
            if not chunk:
                continue
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                txt = raw.decode("latin-1").rstrip("\r")
                #
                # REDACT BEFORE THE WRITE, not afterwards. The device prints its
                # whole config.json at boot, including the AP, station and MQTT
                # passwords, and this folder syncs through SeaDrive -- so a capture
                # that reaches the disk unredacted has already leaked, and cleaning
                # it up later only shortens the exposure. Applied per line, which is
                # safe because both shapes the firmware emits are single-line.
                #
                fh.write("[%8.3f] %s\n" % (time.time() - t0, redact(txt)))
                lines += 1
            fh.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if pending:
            # The trailing partial line gets the same treatment; a capture that
            # ends mid-config-dump is exactly when it matters.
            fh.write("[%8.3f] %s\n"
                     % (time.time() - t0, redact(pending.decode("latin-1"))))
        fh.write("# end, %.1f s elapsed, %d lines\n" % (time.time() - t0, lines))
        fh.close()
        ser.close()
    print("captured %d lines" % lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
