"""The bench supply's protection service (DEVICES-SPEC K1-K9). It alone
opens the KA3005P. Every other caller is a client over a local socket,
through client.py.

    python -B -m vtrux_bench.supply.service --config /etc/vtrux-supply/config.json

It runs as a system service under its own user, from a pinned shared/main
tag the user installs read-only (K1, K2). What the user installs, and how,
is in shared/bench/SUPPLY-INSTALL.md.

The rules, and where each is enforced here:

- K1: every request carries `v`, the request protocol (supply.PROTOCOL).
  Any other version is refused (`dispatch`).
- K2: the supply is found by its USB IDs (serial_dev, S1-S3), opened
  exclusively (ka3005p.open_port, TIOCEXCL), and confirmed by its `*IDN?`
  answer (`open_supply`).
- K3, the absolute tier: the configuration's limits. A request above them is
  refused and logged before anything is sent (`_check_limits`). Turning the
  output on reads the setpoints back first and refuses if either is above
  them, so a setpoint left from the front panel never goes out. The
  configuration must be owned by root and writable by nobody else
  (`load_config`). The suite tier's source is open: it awaits the user's
  ruling (the gen-inhibit reviewer, 2026-11-21, Q1), so no request carries
  a suite limit yet.
- K4: every setpoint, current-limit, output and protection write is read
  back. A difference over the resolution (10 mV, 1 mA), or a garbled or
  missing answer, trips (`trip`): output off, read back, a latched fault.
- K5: while the output is on, VOUT1? and IOUT1? are polled every `poll_s`.
  A measured voltage above the commanded one plus `over_tolerance_v`, or
  above the absolute limit, trips. So does a garbled or missing answer
  (the gen-inhibit reviewer, 2026-11-21, Q7).
- K6: a latched fault refuses every write until the service restarts. It is
  held in memory only, so a restart clears it and nothing else does. Each
  fault goes to the log and the holder's journal.
- K7: a write is accepted only when the bench lock shows the request's
  role and pid holding the bench, and the claimed pid is the connecting
  process or one of its ancestors, read from the socket's peer credentials
  (SO_PEERCRED; the gen-inhibit reviewer, 2026-11-21, Q2). An outside claim
  is refused and logged. Reads are open to every client.
- K8a: the service watches the bench log. When it shows anything but the
  holder that turned the output on, the output goes off and is read back.
  The exceptions are that holder's run pausing (`free_paused`) and resuming
  (its `take`), and requests and withdrawals. A log that cannot be read or
  does not replay counts as not showing the holder: off, read back, writes
  refused until it replays, and no latched fault (Q5).
- K8b: OVP and OCP are set and read back through the STATUS? bits the
  configuration names (`protection_bits`). With no bit named, the service
  refuses the write (Q6).
- K9: every command, read-back and measurement goes to the service's log
  and to the holder's journal, stamped with the time it was read. There is
  one journal per holder (role, pid, run) in `journals`, a folder sessions
  can read (Q3). The client copies a run's entries into its record.

Starting with the output on, the service turns it off: no holder is known to
have turned it on. Stopping (SIGTERM, SIGINT) turns it off too.
"""

import argparse
import datetime
import json
import os
import re
import signal
import socket
import socketserver
import struct
import sys
import threading
import time

from vtrux_bench import bench_lock as BL
from vtrux_bench import serial_dev as SD
from vtrux_bench.supply import PROTOCOL
from vtrux_bench.supply import ka3005p as KA

V_RES = 0.010         # the supply's resolution: K4's read-back tolerance
A_RES = 0.001
EPS = 1e-6
WRITES = ("set", "output", "protection")
READS = ("hello", "read")
REQUIRED = ("device", "idn_prefix", "absolute", "socket", "log", "journals",
            "bench_log", "poll_s", "lock_poll_s", "over_tolerance_v",
            "settle_tolerance_v", "settle_timeout_s")
OPTIONAL = ("protection_bits", "socket_mode")


class ConfigError(Exception):
    pass


class Refused(Exception):
    """A request the rules refuse. kind names the rule."""

    def __init__(self, kind, text):
        Exception.__init__(self, text)
        self.kind = kind


def stamp():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _number(cfg, key, lo, hi):
    v = cfg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo < v <= hi:
        raise ConfigError("%s must be a number above %g and at most %g, not %r"
                          % (key, lo, hi, v))


def load_config(path, require_root=True):
    """The service's configuration (JSON). It must be owned by root and
    writable by nobody else, since it holds the absolute limits (K3).
    Tests pass require_root=False. The service never does."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise ConfigError("the configuration %s cannot be read: %s" % (path, e))
    if require_root and (st.st_uid != 0 or st.st_mode & 0o022):
        raise ConfigError("the configuration %s must be owned by root and "
                          "writable by root alone (owner uid %d, mode %o)"
                          % (path, st.st_uid, st.st_mode & 0o777))
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ConfigError("the configuration is not a JSON object")
    missing = [k for k in REQUIRED if k not in cfg]
    extra = [k for k in cfg if k not in REQUIRED + OPTIONAL and not k.startswith("_")]
    if missing or extra:
        raise ConfigError("configuration keys missing: %s; unknown: %s"
                          % (", ".join(missing) or "none", ", ".join(extra) or "none"))
    a = cfg["absolute"]
    if not isinstance(a, dict) or set(a) != {"volts", "amps"}:
        raise ConfigError("absolute must be {\"volts\": ..., \"amps\": ...}")
    _number(a, "volts", 0, 30)
    _number(a, "amps", 0, 5)
    for k, hi in (("poll_s", 10), ("lock_poll_s", 10), ("over_tolerance_v", 5),
                  ("settle_tolerance_v", 5), ("settle_timeout_s", 60)):
        _number(cfg, k, 0, hi)
    SD.config(cfg["device"], "supply")
    bits = cfg.setdefault("protection_bits", {})
    if not isinstance(bits, dict) or set(bits) - {"ovp", "ocp"} or any(
            b is not None and (isinstance(b, bool) or b not in range(8))
            for b in bits.values()):
        raise ConfigError("protection_bits must map ovp and ocp to a STATUS? "
                          "bit (0-7) or null")
    cfg.setdefault("socket_mode", "0660")
    if not re.match(r"0[0-7]{3}\Z", cfg["socket_mode"]):
        raise ConfigError("socket_mode must be an octal mode such as \"0660\"")
    return cfg


def read_log(path):
    """The bench log's lines, read under a shared lock on its open file
    description, so a writer's append is never read half done. Needs only
    read access: the service's user cannot write the log."""
    import fcntl
    impl = BL.env_impl(BL.this_env())
    with open(path, "rb") as f:
        if impl is BL.LinuxEnv:
            def once():
                try:
                    BL.LinuxEnv._ofd(f, fcntl.F_RDLCK)
                    return True
                except OSError:
                    return False
            BL.wait_lock(once, BL.LOCK_WAIT_S)
        try:
            data = f.read()
        finally:
            if impl is BL.LinuxEnv:
                BL.LinuxEnv._ofd(f, fcntl.F_UNLCK)
    return BL.split_lines(data)


def holder_key(h):
    return None if h is None else {"who": h["who"], "pid": h["pid"], "run": h.get("run")}


def journal_name(key):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", key["who"]).strip("-")
    run = re.sub(r"[^A-Za-z0-9_.-]+", "-", key["run"]) if key["run"] else "hand"
    return "%s_pid%d_%s.jsonl" % (slug, key["pid"], run)


def peer_pid(sock):
    data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                           struct.calcsize("3i"))
    return struct.unpack("3i", data)[0]


class Service:
    """The service's state and rules. Every supply call is made under
    self.mu, from a request or from the watch."""

    def __init__(self, cfg, driver_kw=None):
        self.cfg = cfg
        self.driver_kw = driver_kw or {}
        self.mu = threading.RLock()
        self.supply = None
        self.port = None
        self.idn = None
        self.fault = None
        self.on_by = None          # the holder that turned the output on
        self.commanded = {"volts": None, "amps": None}
        self.holder = None         # the bench's holder at the last read
        self.lock_seen = 0         # the bench log's lines already looked at
        self.lock_ok = None        # whether the last read replayed
        self.stopping = threading.Event()
        self.watcher = None
        os.makedirs(cfg["journals"], exist_ok=True)

    # ---- the log and the journals (K9) ----------------------------------

    def record(self, kind, holder=None, **fields):
        ev = {"ts": stamp(), "kind": kind}
        ev.update(fields)
        key = holder_key(holder) if holder is not None else holder_key(self.holder)
        if key:
            ev["holder"] = key
        line = json.dumps(ev, ensure_ascii=True, separators=(",", ":")) + "\n"
        with open(self.cfg["log"], "a", encoding="ascii") as f:
            f.write(line)
        if key:
            path = os.path.join(self.cfg["journals"], journal_name(key))
            new = not os.path.exists(path)
            with open(path, "a", encoding="ascii") as f:
                f.write(line)
            if new:
                os.chmod(path, 0o644)
        return ev

    def journal_of(self, h):
        key = holder_key(h)
        return os.path.join(self.cfg["journals"], journal_name(key)) if key else None

    def send(self, what, fn, *args):
        fn(*args)
        self.record("command", command=what)

    def ask(self, what, fn, kind="readback"):
        try:
            v = fn()
        except KA.SupplyError as e:
            self.record(kind + "-error", query=what, error=str(e))
            raise
        self.record(kind, query=what, value=v)
        return v

    # ---- the supply (K2) -------------------------------------------------

    def open_supply(self):
        found = SD.resolve(self.cfg["device"], "supply")
        fd = KA.open_port(found["port"])
        sup = KA.KA3005P(fd, **self.driver_kw)
        try:
            idn = sup.idn()
        except KA.SupplyError as e:
            sup.close()
            raise Refused("idn", "the device at %s gives no *IDN? answer: %s"
                          % (found["port"], e))
        if not idn.startswith(self.cfg["idn_prefix"]):
            sup.close()
            raise Refused("idn", "the device at %s answers *IDN? with %r, not "
                          "%r...: not the supply" % (found["port"], idn,
                                                     self.cfg["idn_prefix"]))
        self.supply, self.port, self.idn = sup, found, idn
        self.record("start", port=found, idn=idn, protocol=PROTOCOL,
                    absolute=self.cfg["absolute"])
        with self.mu:
            self.check_lock()
            st = self.ask("STATUS?", sup.status)
            if sup.output_on(st):
                self.turn_off("the output was on when the service started")

    def close(self):
        with self.mu:
            if self.supply is not None:
                try:
                    if self.on_by is not None or self.fault is None:
                        self.turn_off("the service is stopping")
                except Exception:
                    pass
                self.supply.close()
                self.supply = None

    # ---- faults (K4, K5, K6) ----------------------------------------------

    def trip(self, reason, **detail):
        """Output off, read back, and a latched fault."""
        off = None
        try:
            self.send("OUT0", self.supply.output, False)
            off = not self.supply.output_on(self.ask("STATUS?", self.supply.status))
        except KA.SupplyError as e:
            detail["off_error"] = str(e)
            off = False
        self.fault = {"ts": stamp(), "reason": reason, "detail": detail,
                      "off_read_back": off}
        holder = self.on_by
        self.on_by = None
        self.record("fault", holder=holder, fault=self.fault)
        return self.fault

    def turn_off(self, cause):
        """Output off and read back, with no fault unless the read-back
        fails (K4)."""
        holder = self.on_by
        try:
            self.send("OUT0", self.supply.output, False)
            st = self.ask("STATUS?", self.supply.status)
        except KA.SupplyError as e:
            self.on_by = holder
            return self.trip("readback", what="OUT0", error=str(e), cause=cause)
        if self.supply.output_on(st):
            self.on_by = holder
            return self.trip("readback", what="OUT0", got="on", cause=cause)
        self.on_by = None
        self.record("off", holder=holder, cause=cause)
        return None

    # ---- the bench lock (K7, K8a) ------------------------------------------

    def keeps(self, ev):
        """True if bench-log event ev leaves the output with the holder that
        turned it on (K8a)."""
        ob, kind = self.on_by, ev.get("event")
        if kind in ("request", "withdraw"):
            return True
        if not ob["run"] or ev.get("who") != ob["who"] or ev.get("run") != ob["run"]:
            return False
        if kind == "free_paused":
            return True
        return kind == "take" and ev.get("pid") == ob["pid"]

    def check_lock(self):
        """Read the bench log, keep its holder, and turn the output off when
        a new line shows anything but the holder that turned it on, or when
        the log cannot be read. Returns the state, or None if unreadable."""
        try:
            lines = read_log(self.cfg["bench_log"])
            state = BL.replay(lines)
            BL.bench_impl(state)
        except Exception as e:
            why = "%s: %s" % (type(e).__name__, e)
            self.holder = None
            if self.lock_ok is not False:
                self.record("lock-unreadable", cause=why)
            self.lock_ok = False
            if self.on_by is not None:
                self.turn_off("the bench log cannot be read or does not replay "
                              "(%s)" % why)
            return None
        if self.lock_ok is False:
            self.record("lock-readable")
        self.lock_ok = True
        new = lines[self.lock_seen:] if len(lines) >= self.lock_seen else None
        self.lock_seen = len(lines)
        self.holder = state["holder"]
        if self.on_by is not None:
            if new is None:
                self.turn_off("the bench log is shorter than before (replaced)")
            else:
                for ln in new:
                    ev = json.loads(ln)
                    if not self.keeps(ev):
                        self.turn_off("bench log: %s by %s%s" % (
                            ev.get("event"), ev.get("who"),
                            ", run %s" % ev["run"] if ev.get("run") else ""))
                        break
        return state

    def require_holder(self, req, peer):
        """K7 and Q2: the holder, or Refused."""
        role, pid = req["role"], req["pid"]
        anc = BL.env_impl(BL.this_env()).ancestors(peer)
        if pid not in anc:
            self.record("refused", rule="K7-claim", role=role, pid=pid, peer=peer,
                        ancestors=anc)
            raise Refused("claim", "pid %d is not the connecting process %d or "
                          "one of its ancestors (%s)"
                          % (pid, peer, " <- ".join(map(str, anc))))
        state = self.check_lock()
        if state is None:
            self.record("refused", rule="K7", role=role, pid=pid,
                        why="the bench log cannot be read")
            raise Refused("holder", "the bench log cannot be read or does not "
                          "replay: no write is accepted")
        h = state["holder"]
        if h is None or h["who"] != role or h["pid"] != pid:
            self.record("refused", rule="K7", role=role, pid=pid,
                        why=BL.holder_text(state))
            raise Refused("holder", "%s, pid %d, does not hold the bench (%s)"
                          % (role, pid, BL.holder_text(state)))
        return h

    # ---- the limits (K3) -----------------------------------------------------

    def _check_limits(self, volts=None, amps=None, holder=None, what="request"):
        a = self.cfg["absolute"]
        bad = []
        if volts is not None and not -EPS <= volts <= a["volts"] + EPS:
            bad.append("%.3f V against the absolute %.2f V" % (volts, a["volts"]))
        if amps is not None and not -EPS <= amps <= a["amps"] + EPS:
            bad.append("%.3f A against the absolute %.3f A" % (amps, a["amps"]))
        if bad:
            self.record("refused", holder=holder, rule="K3", what=what,
                        volts=volts, amps=amps, why="; ".join(bad))
            raise Refused("limit", "%s refused (K3): %s" % (what, "; ".join(bad)))

    # ---- the writes (K4) -------------------------------------------------------

    def set_voltage(self, volts, h):
        self._check_limits(volts=volts, holder=h)
        sup = self.supply
        self.send("VSET1:%05.2f" % volts, sup.set_voltage, volts)
        try:
            got = self.ask("VSET1?", sup.voltage_set)
        except KA.SupplyError as e:
            self.trip("readback", what="VSET1?", want=volts, error=str(e))
            raise Refused("fault", "VSET1? read-back failed: %s" % e)
        if abs(got - volts) > V_RES + EPS:
            self.trip("readback", what="VSET1?", want=volts, got=got)
            raise Refused("fault", "VSET1? read back %.2f V for %.2f V" % (got, volts))
        self.commanded["volts"] = got
        return got

    def set_current(self, amps, h):
        self._check_limits(amps=amps, holder=h)
        sup = self.supply
        self.send("ISET1:%.3f" % amps, sup.set_current, amps)
        try:
            got = self.ask("ISET1?", sup.current_set)
        except KA.SupplyError as e:
            self.trip("readback", what="ISET1?", want=amps, error=str(e))
            raise Refused("fault", "ISET1? read-back failed: %s" % e)
        if abs(got - amps) > A_RES + EPS:
            self.trip("readback", what="ISET1?", want=amps, got=got)
            raise Refused("fault", "ISET1? read back %.3f A for %.3f A" % (got, amps))
        self.commanded["amps"] = got
        return got

    def set_output(self, on, h):
        sup = self.supply
        if on:
            try:
                volts = self.ask("VSET1?", sup.voltage_set)
                amps = self.ask("ISET1?", sup.current_set)
            except KA.SupplyError as e:
                self.trip("readback", what="setpoints before OUT1", error=str(e))
                raise Refused("fault", "the setpoints could not be read before "
                              "OUT1: %s" % e)
            self._check_limits(volts, amps, holder=h, what="output on at the "
                               "setpoints read back")
            self.commanded = {"volts": volts, "amps": amps}
        self.send("OUT1" if on else "OUT0", sup.output, on)
        try:
            st = self.ask("STATUS?", sup.status)
        except KA.SupplyError as e:
            if on:
                self.on_by = holder_key(h)
            self.trip("readback", what="STATUS?", want=on, error=str(e))
            raise Refused("fault", "STATUS? read-back failed: %s" % e)
        if sup.output_on(st) != on:
            self.on_by = holder_key(h) if on else self.on_by
            self.trip("readback", what="STATUS? output bit", want=on,
                      got=sup.output_on(st))
            raise Refused("fault", "the output read back %s for %s"
                          % ("on" if not on else "off", "on" if on else "off"))
        self.on_by = holder_key(h) if on else None
        return on

    def set_protection(self, which, on, h):
        bit = self.cfg["protection_bits"].get(which)
        if bit is None:
            self.record("refused", holder=h, rule="K8b", which=which, on=on,
                        why="no STATUS? bit configured")
            raise Refused("protection", "%s cannot be read back: no STATUS? bit "
                          "is configured for it (K8b, set after K10)" % which.upper())
        sup = self.supply
        self.send("%s%d" % (which.upper(), int(on)), sup.protection, which, on)
        try:
            st = self.ask("STATUS?", sup.status)
        except KA.SupplyError as e:
            self.trip("readback", what="STATUS? %s bit" % which, want=on, error=str(e))
            raise Refused("fault", "STATUS? read-back failed: %s" % e)
        if sup.protection_on(st, bit) != on:
            self.trip("readback", what="STATUS? %s bit" % which, want=on,
                      got=not on)
            raise Refused("fault", "%s read back %s" % (which.upper(),
                                                        "off" if on else "on"))
        return on

    # ---- the watch (K5, K8a) -------------------------------------------------

    def watch_once(self, lock=True):
        with self.mu:
            if lock:
                self.check_lock()
            if self.on_by is None or self.fault is not None:
                return
            sup = self.supply
            try:
                vo = self.ask("VOUT1?", sup.voltage_out, kind="measure")
                io = self.ask("IOUT1?", sup.current_out, kind="measure")
            except KA.SupplyError as e:
                self.trip("watch", error=str(e))
                return
            a = self.cfg["absolute"]["volts"]
            over = self.commanded["volts"] + self.cfg["over_tolerance_v"]
            if vo > over + EPS or vo > a + EPS:
                self.trip("over-voltage", measured=vo, amps=io,
                          commanded=self.commanded["volts"],
                          tolerance=self.cfg["over_tolerance_v"], absolute=a)

    def watch(self):
        poll, lock_poll = self.cfg["poll_s"], self.cfg["lock_poll_s"]
        next_lock = 0.0
        while not self.stopping.wait(poll):
            now = time.monotonic()
            due = now >= next_lock
            if due:
                next_lock = now + lock_poll
            try:
                self.watch_once(lock=due)
            except Exception as e:      # the watch never dies quietly
                with self.mu:
                    self.record("watch-error", error="%s: %s" % (type(e).__name__, e))
                    if self.on_by is not None and self.fault is None:
                        self.trip("watch", error="%s: %s" % (type(e).__name__, e))

    def start_watch(self):
        self.watcher = threading.Thread(target=self.watch, name="supply-watch",
                                        daemon=True)
        self.watcher.start()

    # ---- requests (K1, K6, K7) -----------------------------------------------

    def dispatch(self, req, peer):
        """One request: its reply, a dict with ok."""
        try:
            return dict(self._dispatch(req, peer), ok=True)
        except Refused as e:
            return {"ok": False, "refused": e.kind, "error": str(e)}
        except Exception as e:      # never a dropped connection
            why = "%s: %s" % (type(e).__name__, e)
            with self.mu:
                self.record("error", op=req.get("op") if isinstance(req, dict)
                            else None, error=why)
            return {"ok": False, "refused": "error", "error": why}

    def _dispatch(self, req, peer):
        if not isinstance(req, dict):
            raise Refused("request", "a request is a JSON object")
        if req.get("v") != PROTOCOL:
            with self.mu:
                self.record("refused", rule="K1", got=req.get("v"), peer=peer)
            raise Refused("protocol", "request protocol %r, but this service "
                          "speaks %d (K1)" % (req.get("v"), PROTOCOL))
        op, role, pid = req.get("op"), req.get("role"), req.get("pid")
        if not isinstance(role, str) or not role or isinstance(pid, bool) \
                or not isinstance(pid, int) or pid <= 0:
            raise Refused("request", "every request carries its role and pid (K7)")
        if op not in WRITES + READS:
            raise Refused("request", "unknown op %r" % op)
        with self.mu:
            if op == "hello":
                return {"protocol": PROTOCOL, "absolute": self.cfg["absolute"],
                        "settle_tolerance_v": self.cfg["settle_tolerance_v"],
                        "settle_timeout_s": self.cfg["settle_timeout_s"],
                        "poll_s": self.cfg["poll_s"], "idn": self.idn,
                        "journals": self.cfg["journals"]}
            if op == "read":
                return self.read(req)
            self.record("request", op=op, role=role, pid=pid, peer=peer,
                        args={k: v for k, v in req.items()
                              if k not in ("v", "op", "role", "pid")})
            if self.fault is not None:
                self.record("refused", rule="K6", role=role, pid=pid)
                raise Refused("fault", "a fault is latched (%s at %s): every "
                              "write is refused until the service is restarted"
                              % (self.fault["reason"], self.fault["ts"]))
            h = self.require_holder(req, peer)
            if op == "set":
                out = {}
                if req.get("volts") is None and req.get("amps") is None:
                    raise Refused("request", "set needs volts or amps")
                for k in ("volts", "amps"):
                    v = req.get(k)
                    if v is not None and (isinstance(v, bool)
                                          or not isinstance(v, (int, float))):
                        raise Refused("request", "%s must be a number" % k)
                if req.get("volts") is not None:
                    out["volts"] = self.set_voltage(float(req["volts"]), h)
                if req.get("amps") is not None:
                    out["amps"] = self.set_current(float(req["amps"]), h)
                return {"read_back": out, "journal": self.journal_of(h)}
            if op == "output":
                if not isinstance(req.get("on"), bool):
                    raise Refused("request", "output needs on: true or false")
                return {"read_back": {"on": self.set_output(req["on"], h)},
                        "journal": self.journal_of(h)}
            which = req.get("which")
            if which not in ("ovp", "ocp") or not isinstance(req.get("on"), bool):
                raise Refused("request", "protection needs which (ovp, ocp) and on")
            return {"read_back": {which: self.set_protection(which, req["on"], h)},
                    "journal": self.journal_of(h)}

    def read(self, req):
        sup = self.supply
        out = {"fault": self.fault, "on_by": self.on_by,
               "commanded": dict(self.commanded), "holder": holder_key(self.holder),
               "journal": self.journal_of(self.holder)}
        try:
            st = self.ask("STATUS?", sup.status)
            out["status"] = st
            out["on"] = sup.output_on(st)
            out["volts_set"] = self.ask("VSET1?", sup.voltage_set)
            out["amps_set"] = self.ask("ISET1?", sup.current_set)
            out["volts_out"] = self.ask("VOUT1?", sup.voltage_out, kind="measure")
            out["amps_out"] = self.ask("IOUT1?", sup.current_out, kind="measure")
        except KA.SupplyError as e:
            raise Refused("supply", "the supply's answer could not be read: %s" % e)
        return out


# ---- the socket ---------------------------------------------------------------

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        peer = peer_pid(self.request)
        for raw in self.rfile:
            try:
                req = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                reply = {"ok": False, "refused": "request", "error": "not JSON"}
            else:
                reply = self.server.service.dispatch(req, peer)
            self.wfile.write((json.dumps(reply, ensure_ascii=True) + "\n").encode("ascii"))
            self.wfile.flush()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, service):
        path = service.cfg["socket"]
        if os.path.exists(path):
            os.unlink(path)
        socketserver.UnixStreamServer.__init__(self, path, Handler)
        os.chmod(path, int(service.cfg["socket_mode"], 8))
        self.service = service


def serve(service):
    """Open the supply, start the watch and serve until stopped. Returns
    the server, already serving on its own thread."""
    service.open_supply()
    service.start_watch()
    srv = Server(service)
    t = threading.Thread(target=srv.serve_forever, name="supply-socket", daemon=True)
    t.start()
    return srv


def stop(service, srv):
    service.stopping.set()
    srv.shutdown()
    srv.server_close()
    service.close()
    try:
        os.unlink(service.cfg["socket"])
    except FileNotFoundError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux_bench.supply.service")
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        service = Service(cfg)
        srv = serve(service)
    except (ConfigError, Refused, KA.SupplyError, SD.Refused, BL.Refused) as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    done = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: done.set())
    done.wait()
    stop(service, srv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
