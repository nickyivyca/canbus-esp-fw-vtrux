"""The bench supply's client library: how every caller other than the
protection service (service.py) reaches the supply (DEVICES-SPEC K2).

    s = Supply(role, pid)          # pid: the bench holder's (the runner's,
                                   # or the session's long-lived process)
    s.read()                       # open to every client
    s.run_start(12.0, 0.2)         # K8: off, setpoint, limit, on, settled
    s.case_entry(12.0, 0.2)        # K8: still on at the setpoint in force
    s.case_cycle(13.0, 0.2)        # K8: a case's own power cycle
    s.step(11.0)                   # K8: a setpoint step with the output on
    s.run_end()                    # K8: off, read back
    s.resume_check(12.0, 0.2)      # K8a: at a resume, "kept" or "cycled"
    s.copy_journal(dest, offset)   # K9: the holder's entries into a record

Every request carries the protocol version (K1), the role and the pid
(K7). The service reads back every write (K4) and refuses what the rules
refuse; a refusal raises Refused here, naming the rule. The client checks
each write's read-back in the reply as well.

The K8 sequence calls are the library half of K8. Wiring them into the chain
runner's start, case entries, end and resume is a later topic (the
gen-inhibit reviewer, 2026-11-21, Q4).

A voltage that has not settled within the service's settle tolerance by
its settle timeout turns the output off, read back, and raises NotSettled,
with no latched fault (Q8). A K5 over-voltage during the settle is latched
by the service.
"""

import json
import os
import shutil
import socket
import time

from vtrux_bench.supply import PROTOCOL

DEFAULT_SOCKET = "/run/vtrux-supply/supply.sock"
V_RES = 0.010
A_RES = 0.001
EPS = 1e-6


class Refused(Exception):
    def __init__(self, kind, text):
        Exception.__init__(self, text)
        self.kind = kind


class NotSettled(Exception):
    pass


class NotInForce(Exception):
    """A case entry found the output off or another setpoint in force."""


class Supply:
    def __init__(self, role, pid, socket_path=None, timeout_s=15.0,
                 settle_poll_s=0.1):
        self.role, self.pid = role, pid
        self.socket_path = socket_path or DEFAULT_SOCKET
        self.timeout_s = timeout_s
        self.settle_poll_s = settle_poll_s
        self._hello = None

    # ---- the wire -------------------------------------------------------

    def call(self, op, **kw):
        req = {"v": PROTOCOL, "op": op, "role": self.role, "pid": self.pid}
        req.update(kw)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout_s)
        try:
            s.connect(self.socket_path)
            s.sendall((json.dumps(req) + "\n").encode("ascii"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        if not buf:
            raise Refused("socket", "no reply from the supply service at %s"
                          % self.socket_path)
        reply = json.loads(buf.decode("ascii"))
        if not reply.get("ok"):
            raise Refused(reply.get("refused", "?"), reply.get("error", "refused"))
        return reply

    def hello(self):
        r = self.call("hello")
        if r.get("protocol") != PROTOCOL:
            raise Refused("protocol", "the service speaks protocol %r, this "
                          "client %d (K1)" % (r.get("protocol"), PROTOCOL))
        self._hello = r
        return r

    def settings(self):
        return self._hello or self.hello()

    # ---- reads and writes --------------------------------------------------

    def read(self):
        return self.call("read")

    def set(self, volts=None, amps=None):
        r = self.call("set", volts=volts, amps=amps)
        rb = r["read_back"]
        if volts is not None and abs(rb["volts"] - volts) > V_RES + EPS:
            raise Refused("readback", "VSET1? read back %r for %r" % (rb["volts"], volts))
        if amps is not None and abs(rb["amps"] - amps) > A_RES + EPS:
            raise Refused("readback", "ISET1? read back %r for %r" % (rb["amps"], amps))
        return r

    def output(self, on):
        r = self.call("output", on=on)
        if r["read_back"]["on"] is not on:
            raise Refused("readback", "the output read back %r for %r"
                          % (r["read_back"]["on"], on))
        return r

    def protection(self, which, on):
        return self.call("protection", which=which, on=on)

    # ---- K8 -----------------------------------------------------------------

    def power_up(self, volts, amps):
        """Off, read back; setpoint and current limit, each read back; on,
        read back; then the measured voltage settled. Returns the settled
        reading."""
        self.output(False)
        self.set(volts=volts)
        self.set(amps=amps)
        self.output(True)
        return self.settle(volts)

    run_start = power_up
    case_cycle = power_up

    def settle(self, volts):
        h = self.settings()
        tol, deadline = h["settle_tolerance_v"], time.monotonic() + h["settle_timeout_s"]
        last = None
        while True:
            last = self.read()
            if last["fault"]:
                raise Refused("fault", "a fault latched while settling: %s"
                              % last["fault"]["reason"])
            if last["on"] and abs(last["volts_out"] - volts) <= tol + EPS:
                return last
            if time.monotonic() >= deadline:
                break
            time.sleep(self.settle_poll_s)
        self.output(False)
        raise NotSettled("the output measured %s V, not within %.2f V of %.2f V, "
                         "after %.1f s; turned off and read back"
                         % (last.get("volts_out"), tol, volts, h["settle_timeout_s"]))

    def _in_force(self, r, volts, amps):
        return (r["on"] and abs(r["volts_set"] - volts) <= V_RES + EPS
                and abs(r["amps_set"] - amps) <= A_RES + EPS)

    def case_entry(self, volts, amps):
        """A case that keeps the output: on, at the setpoint in force, read
        at its entry. NotInForce otherwise."""
        r = self.read()
        if not self._in_force(r, volts, amps):
            raise NotInForce("case entry: output %s at %s V, %s A; the case "
                             "needs on at %.2f V, %.3f A"
                             % ("on" if r["on"] else "off", r["volts_set"],
                                r["amps_set"], volts, amps))
        return r

    def step(self, volts):
        """A setpoint step within a case, with the output on, read back."""
        r = self.read()
        if not r["on"]:
            raise NotInForce("a step needs the output on")
        return self.set(volts=volts)

    def run_end(self):
        """Off, read back. Returns the holder's journal path."""
        r = self.output(False)
        return r.get("journal")

    def resume_check(self, volts, amps):
        """At a run's resume: "kept" if the output is as the paused run left
        it (on, at its setpoint), else the output cycled as at a run's start
        and "cycled", after which the caller identifies the boards again."""
        r = self.read()
        if self._in_force(r, volts, amps):
            return "kept", r
        return "cycled", self.power_up(volts, amps)

    # ---- K9 -----------------------------------------------------------------

    def journal_path(self):
        return self.read()["journal"]

    def copy_journal(self, dest, offset=0, path=None):
        """Append the holder's journal from byte offset to dest, a file in
        the run's record. Returns the new offset, for the next copy."""
        path = path or self.journal_path()
        if path is None or not os.path.exists(path):
            return offset
        with open(path, "rb") as src, open(dest, "ab") as out:
            src.seek(offset)
            shutil.copyfileobj(src, out)
            return src.tell()

    def hand_release(self):
        """At a hand release: off, read back, and the line naming the hand
        take's record, its journal, to print."""
        path = self.run_end()
        return "supply journal for this hand take: %s" % path
