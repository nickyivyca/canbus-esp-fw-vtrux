"""A simulated Korad KA3005P on a pseudo-terminal, for test_supply.py. No
hardware.

    sim = FakeKA3005P()        # sim.port: the pty's path, which the service opens
    sim.start() ... sim.stop()

It answers the commands of vtrux_bench/supply/ka3005p.py as the protocol
note describes the supply: plain ASCII, no terminator, an answer only to a
query. The output's measured voltage is the setpoint while on (0 while off),
and its current `load_a`. The knobs make it misbehave, one rule at a time:

    vset_offset, iset_offset   VSET1?/ISET1? read back this far off
    garble                     queries whose answer is garbled bytes
    garble_ascii               queries whose answer is printable but no number
    mute                       queries left unanswered
    vout_extra                 added to the measured voltage while on
    out_stuck                  STATUS? reports this output state (True/False)
    rise_s                     the measured voltage ramps to the setpoint over
                               this long after OUT1 (a slow settle)
    prot_bits                  the STATUS? bits that report OVP and OCP
                               ({"ovp": 7, "ocp": 5}); {} reports neither

Every command received is kept in `commands`, with its time, so a test can
show that a refused request never reached the supply. The made-up identity
is plainly made up.
"""

import os
import re
import select
import threading
import time
import tty

TOKEN = re.compile(rb"\*IDN\?|VSET1\?|VSET1:(\d+\.\d+)|ISET1\?|ISET1:(\d+\.\d+)"
                   rb"|VOUT1\?|IOUT1\?|OUT([01])|STATUS\?|OVP([01])|OCP([01])")
IDN = "KORAD KA3005P V5.8 SN:TESTSUPPLY01"


class FakeKA3005P:
    def __init__(self, idn=IDN, prot_bits=None, load_a=0.035):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.idn = idn
        self.prot_bits = {"ovp": 7, "ocp": 5} if prot_bits is None else prot_bits
        self.load_a = load_a
        self.vset, self.iset = 5.0, 1.0      # a front panel's leftovers
        self.out = False
        self.on_at = None
        self.ovp = self.ocp = False
        self.vset_offset = self.iset_offset = 0.0
        self.garble, self.garble_ascii, self.mute = set(), set(), set()
        self.vout_extra = 0.0
        self.out_stuck = None
        self.rise_s = 0.0
        self.commands = []
        self.mu = threading.Lock()
        self._stop = threading.Event()
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def sent(self, prefix=""):
        with self.mu:
            return [c for _, c in self.commands if c.startswith(prefix)]

    def vout(self):
        if not self.out:
            return 0.0
        v = self.vset
        if self.rise_s:
            v *= min(1.0, (time.monotonic() - self.on_at) / self.rise_s)
        return v + self.vout_extra

    def _answer(self, name, text):
        if name in self.mute:
            return b""
        if name in self.garble:
            return b"\x8f#?\x00"
        if name in self.garble_ascii:
            return b"E1.#"
        return text if isinstance(text, bytes) else text.encode("ascii")

    def _status(self):
        out = self.out if self.out_stuck is None else self.out_stuck
        s = 1 | (out << 6)
        if "ovp" in self.prot_bits and self.ovp:
            s |= 1 << self.prot_bits["ovp"]
        if "ocp" in self.prot_bits and self.ocp:
            s |= 1 << self.prot_bits["ocp"]
        return bytes([s])

    def _do(self, m):
        cmd = m.group(0).decode("ascii")
        with self.mu:
            self.commands.append((time.monotonic(), cmd))
        if m.group(1):
            self.vset = float(m.group(1))
        elif m.group(2):
            self.iset = float(m.group(2))
        elif m.group(3):
            self.out = m.group(3) == b"1"
            self.on_at = time.monotonic() if self.out else None
        elif m.group(4):
            self.ovp = m.group(4) == b"1"
        elif m.group(5):
            self.ocp = m.group(5) == b"1"
        elif cmd == "*IDN?":
            return self._answer(cmd, self.idn)
        elif cmd == "VSET1?":
            return self._answer(cmd, "%05.2f" % (self.vset + self.vset_offset))
        elif cmd == "ISET1?":
            return self._answer(cmd, "%.3f" % (self.iset + self.iset_offset))
        elif cmd == "VOUT1?":
            return self._answer(cmd, "%05.2f" % self.vout())
        elif cmd == "IOUT1?":
            return self._answer(cmd, "%.3f" % (self.load_a if self.out else 0.0))
        elif cmd == "STATUS?":
            return self._answer(cmd, self._status())
        return b""

    def _loop(self):
        buf = b""
        while not self._stop.is_set():
            r, _, _ = select.select([self.master], [], [], 0.01)
            if r:
                try:
                    buf += os.read(self.master, 256)
                except OSError:
                    time.sleep(0.01)
                continue
            while buf:
                m = TOKEN.match(buf)
                if not m:
                    buf = buf[1:]
                    continue
                buf = buf[m.end():]
                ans = self._do(m)
                if ans:
                    try:
                        os.write(self.master, ans)
                    except OSError:
                        pass
