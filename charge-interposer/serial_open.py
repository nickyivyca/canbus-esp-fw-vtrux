#!/usr/bin/env python3
"""Opening the X2's USB serial port (USB-Serial-JTAG) without resetting it.

Charge-interposer tester, 2026-11-11 (the reviewer's batch, after the CAN
replay tooling session's adapter characterisation step 7 on madhouse-debian,
tools/plans/madhouse-adapter-characterisation.md in reverse-it):

  Windows (usbser.sys)  DTR and RTS set low BEFORE open(): neither the open
                        nor the close resets the board (bench facts,
                        2026-10-14). Unchanged.
  Linux (cdc_acm)       that order RESETS the board at every open: the
                        kernel asserts both lines on open, and pyserial
                        then clears DTR before RTS, which leaves DTR=0/RTS=1
                        for an instant (rst:0x15, a power-up round 0.32 s
                        after the open, 2 of 2). Opening with pyserial's
                        defaults and THEN setting RTS low, then DTR low, did
                        not reset it (2 of 2). So on Linux: open(), then
                        rts=False, then dtr=False.

Either way the port is left with both lines low, so a later close does not
reset the board on Windows; on Linux the close after this order has not been
measured. A deliberate reset is not this module's: run_scenario.SerialTap
and bench_usb_host send esptool's HardReset themselves.

    import serial_open
    s = serial_open.open_quiet(port, 115200, 0.2)

Every harness opener of the X2's port that holds the lines low goes through
open_quiet; serial_open_test.py checks the order of line changes from the
open on, for both systems, and that no other harness file builds a port
with serial.Serial() and presets its lines.
"""

import os

# (system, the line changes in order): what open_quiet does, as the test
# records it on a fake port. "pre" is before open(), "post" after.
ORDER = {
    "nt": [("pre", "dtr", False), ("pre", "rts", False), ("open",)],
    "posix": [("open",), ("post", "rts", False), ("post", "dtr", False)],
}


def system(name=None):
    """"nt" or "posix" (os.name), or the name given."""
    return name or ("nt" if os.name == "nt" else "posix")


def open_quiet(port, baudrate, timeout, serial_mod=None, os_name=None):
    """An open pyserial port with DTR and RTS low, opened in the order that
    does not reset the X2 on this system. serial_mod: a stand-in for the
    pyserial module (tests); os_name: "nt" or "posix" (tests)."""
    S = serial_mod
    if S is None:
        import serial as S
    s = S.Serial()
    s.port, s.baudrate, s.timeout = port, baudrate, timeout
    if system(os_name) == "nt":
        s.dtr = False
        s.rts = False
        s.open()
    else:
        s.open()
        s.rts = False
        s.dtr = False
    return s
