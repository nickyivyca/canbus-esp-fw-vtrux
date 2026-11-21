"""The Korad KA3005P's serial protocol: the driver the protection service
(service.py) uses. Nothing else opens the supply (DEVICES-SPEC K2).

The KA3005P speaks plain ASCII at 9600 8N1 over its USB serial port, with
no line terminator in either direction: a command ends when the host stops
sending, and an answer when the supply stops. So every command is followed
by a gap (GAP_S), and an answer is read until no byte has come for READ_GAP_S
after the first, or until TIMEOUT_S passes with none (a missing answer).

    *IDN?         identity, e.g. "KORAD KA3005P V5.8 SN:..."
    VSET1:<v>     set the voltage, two decimals ("12.00")
    VSET1?        the voltage set, "12.00"
    ISET1:<a>     set the current limit, three decimals ("0.200")
    ISET1?        the current limit set, "0.200"
    VOUT1?        the voltage measured at the output
    IOUT1?        the current measured
    OUT1 / OUT0   output on / off
    STATUS?       one byte; bit 6 is the output (1 on)
    OVP1 / OVP0   the supply's own over-voltage protection on / off
    OCP1 / OCP0   its over-current protection on / off

The protection's state has no query of its own. Which STATUS? bits report
it, if any, is set in the service's root-owned configuration after K10's
characterisation (the gen-inhibit reviewer, 2026-11-21, Q6), and passed to
`protection_on` here.

An answer that is not what the query returns (a number where a number is
due, one byte for STATUS?) is garbled, and a missing one a timeout: both
raise SupplyError, which the service treats as K4 says.

`open_port` opens the port for this process alone (TIOCEXCL): a second open,
by any process but root's, fails with EBUSY while it is held (K2).
"""

import errno
import os
import re
import select
import time

GAP_S = 0.06          # after every command, before the next byte is sent
READ_GAP_S = 0.04     # an answer has ended when no byte comes for this long
TIMEOUT_S = 0.6       # no first byte within this long: a missing answer
BAUD = 9600
OUTPUT_BIT = 6        # STATUS? bit for the output
NUMBER = re.compile(r"\d{1,2}\.\d{2,3}\Z")
IDN_TEXT = re.compile(r"[\x20-\x7e]+\Z")


class SupplyError(Exception):
    """A garbled or missing answer, or a port that cannot be used."""


def open_port(path):
    """The supply's port at path (its /dev/serial/by-id/ link), opened
    read-write, raw 9600 8N1, held exclusively (TIOCEXCL). Returns the file
    descriptor. Raises SupplyError, naming the errno, when it cannot be
    opened, as when another process holds it (EBUSY)."""
    import fcntl
    import termios
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        raise SupplyError("the supply's port %s cannot be opened: %s (%s)"
                          % (path, e.strerror, errno.errorcode.get(e.errno, e.errno)))
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        iflag = 0
        oflag = 0
        lflag = 0
        cflag = (cflag & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB
                           | getattr(termios, "CRTSCTS", 0))) \
            | termios.CS8 | termios.CREAD | termios.CLOCAL
        speed = termios.B9600
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(fd, termios.TCIOFLUSH)
    except OSError as e:
        os.close(fd)
        raise SupplyError("the supply's port %s cannot be set up: %s"
                          % (path, e))
    return fd


class KA3005P:
    """The supply on an open port (a file descriptor). Each method sends
    one command, and a query reads its answer. Not thread-safe: the service
    serialises every call."""

    def __init__(self, fd, gap_s=GAP_S, read_gap_s=READ_GAP_S,
                 timeout_s=TIMEOUT_S):
        self.fd = fd
        self.gap_s, self.read_gap_s, self.timeout_s = gap_s, read_gap_s, timeout_s

    def close(self):
        """Give up the exclusive hold (TIOCNXCL), then close. The kernel
        keeps a tty's exclusive flag while any descriptor of it is open."""
        if self.fd is not None:
            import fcntl
            import termios
            try:
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None

    # ---- the wire ------------------------------------------------------

    def _drain(self):
        """Throw away any byte left over from an earlier answer."""
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            try:
                if not os.read(self.fd, 256):
                    return
            except BlockingIOError:
                return

    def send(self, text):
        self._drain()
        data = text.encode("ascii")
        try:
            while data:
                n = os.write(self.fd, data)
                data = data[n:]
        except OSError as e:
            raise SupplyError("writing %r to the supply failed: %s" % (text, e))
        time.sleep(self.gap_s)

    def query(self, text):
        """Send a query; its answer as bytes. SupplyError on no answer."""
        self.send(text)
        out = b""
        deadline = time.monotonic() + self.timeout_s
        while True:
            wait = (deadline - time.monotonic()) if not out else self.read_gap_s
            if wait <= 0:
                break
            r, _, _ = select.select([self.fd], [], [], wait)
            if not r:
                break
            try:
                chunk = os.read(self.fd, 256)
            except BlockingIOError:
                continue
            except OSError as e:
                raise SupplyError("reading the answer to %r failed: %s" % (text, e))
            if not chunk:
                break
            out += chunk
        if not out:
            raise SupplyError("no answer to %r within %.1f s" % (text, self.timeout_s))
        return out

    def number(self, text):
        raw = self.query(text)
        try:
            s = raw.decode("ascii")
        except UnicodeDecodeError:
            s = None
        if s is None or not NUMBER.match(s):
            raise SupplyError("garbled answer to %r: %r" % (text, raw[:32]))
        return float(s)

    # ---- the commands --------------------------------------------------

    def idn(self):
        raw = self.query("*IDN?")
        try:
            s = raw.decode("ascii")
        except UnicodeDecodeError:
            s = None
        if s is None or not IDN_TEXT.match(s):
            raise SupplyError("garbled answer to '*IDN?': %r" % raw[:64])
        return s

    def set_voltage(self, volts):
        self.send("VSET1:%05.2f" % volts)

    def voltage_set(self):
        return self.number("VSET1?")

    def set_current(self, amps):
        self.send("ISET1:%.3f" % amps)

    def current_set(self):
        return self.number("ISET1?")

    def voltage_out(self):
        return self.number("VOUT1?")

    def current_out(self):
        return self.number("IOUT1?")

    def output(self, on):
        self.send("OUT1" if on else "OUT0")

    def status(self):
        raw = self.query("STATUS?")
        if len(raw) != 1:
            raise SupplyError("garbled answer to 'STATUS?': %r" % raw[:32])
        return raw[0]

    def output_on(self, status=None):
        s = self.status() if status is None else status
        return bool(s >> OUTPUT_BIT & 1)

    def protection(self, which, on):
        """which is "ovp" or "ocp"."""
        self.send("%s%d" % (which.upper(), 1 if on else 0))

    @staticmethod
    def protection_on(status, bit):
        return bool(status >> bit & 1)
