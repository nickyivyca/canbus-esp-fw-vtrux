"""Open an ESP32 USB-Serial-JTAG console without resetting the chip, on Linux and on Windows.

Gen-inhibit tester, 2026-11-11, for the gen-inhibit reviewer's condition of the first
gen-inhibit bench run on madhouse-debian. The X2's console (ESP32-S3) and the WiCAN's
(ESP32-C3) are both the chip's USB-Serial-JTAG, where the host's DTR and RTS drive the
chip's reset: RTS asserted while DTR is not holds the chip in reset, and releasing it
boots the chip.

THE OLD FORM RESETS ON LINUX. These tools set `dtr = False` and `rts = False` before
`open()`, so that the open would leave both lines deasserted. That holds on Windows,
where the open sets both lines as configured. On Linux the kernel asserts both lines when
the port opens, and pyserial's posix `open()` then writes the configured values in the
order DTR, RTS. So for a moment DTR is clear and RTS is still asserted, which is the
reset. Measured by CAN replay tooling on madhouse-debian, 2026-11-11 (its step 7, relayed
by the gen-inhibit reviewer): opening the X2's console with both pre-set low reset the
board (rst:0x15, its power-up round 0.32 s after the open, 2 of 2). pyserial's defaults
did not reset it, and neither did the defaults followed by `rts = False`, then
`dtr = False` (2 of 2 each).

THE FORM HERE. On Linux (any os.name but "nt"): open with pyserial's defaults, both lines
asserted, which is no reset; then clear RTS first, which leaves DTR asserted and RTS
clear, again no reset; then clear DTR. The port ends with both lines deasserted, as the
old form left it on Windows, so a deliberate reset pulse that follows starts from the
same lines on both systems. On Windows: the old form, unchanged.

    s = open_console(port, 115200, 0.1)

test_serial_noreset.py checks this form and every tool that opens a console through it,
against a model of the lines; it also shows that the model sees the old form's reset.
"""
import os


def os_name():
    """os.name, at call time. A test swaps this function, never os.name itself."""
    return os.name


def open_console(port, baudrate=115200, timeout=0.1, serial_mod=None):
    """An open pyserial port on `port`, opened without a reset (the module docstring).
    serial_mod is the pyserial module, or a stand-in for a test (default: `import serial`)."""
    if serial_mod is None:
        import serial as serial_mod
    s = serial_mod.Serial()
    s.port, s.baudrate, s.timeout = port, baudrate, timeout
    if os_name() == "nt":
        s.dtr = False
        s.rts = False
        s.open()
        s.dtr = False
        s.rts = False
    else:
        s.open()            # the kernel asserts both lines: no reset
        s.rts = False       # RTS first: DTR asserted with RTS clear is no reset either
        s.dtr = False
    return s
