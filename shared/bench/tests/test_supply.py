"""Tests of the bench supply's protection service and client library
(vtrux_bench.supply) against a simulated KA3005P (fake_ka3005p.py). No
hardware.

    python -B shared/bench/tests/test_supply.py            every test
    python -B shared/bench/tests/test_supply.py --can-fail every test
        unmutated first (the control, which must pass, or no mutant is
        counted), then again under each of its mutants (a broken copy of
        the rule it guards), each of which must make it FAIL
    python -B shared/bench/tests/test_supply.py NAME...     some tests

Written from DEVICES-SPEC S1-S3 and K1-K9, with the gen-inhibit reviewer's
readings of 2026-11-21 (Q2-Q8):
- Q2: the claimed pid must be the connecting process or an ancestor.
- Q3: one journal per holder; the client copies it into a run's record.
- Q4: K8 is the library's sequence calls; the runner wiring comes later.
- Q5: an unreadable bench log turns the output off with no latch.
- Q6: OVP and OCP are read back through configured STATUS? bits.
- Q7: a garbled or missing poll answer latches like K4.
- Q8: not settled turns the output off and raises, with no latch.
K3's suite tier is not tested: its source awaits the user's ruling (Q1).
K10's items are run later on the real supply; the ones a simulation can
show are here.

Each test runs the service in this process on its own made-up bench: the
simulated supply on a pseudo-terminal, found through a made-up
/dev/serial/by-id/ (_serial_fixture.py) whose link leads to the pty, and a
scratch bench lock log (bench_lock), with the service's socket, log and
journals in the test's folder. Linux only, like the service: elsewhere
every test prints "NOT RUN (other env)".

A mutant is a text edit of service.py, client.py or ka3005p.py, executed
into the module; an edit whose text is not found exactly once is NOT
APPLIED and counts as a failure. Files go to this run's own folder
(_scratch.py), which is also the process's working directory, so the
sockets' paths stay short.
"""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from vtrux_bench import bench_lock as BL           # noqa: E402
from vtrux_bench import serial_dev as SD           # noqa: E402
from vtrux_bench.supply import PROTOCOL            # noqa: E402
from vtrux_bench.supply import client as CL        # noqa: E402
from vtrux_bench.supply import ka3005p as KA       # noqa: E402
from vtrux_bench.supply import service as SVC      # noqa: E402
from _serial_fixture import FakeSerial             # noqa: E402
from fake_ka3005p import FakeKA3005P               # noqa: E402
import _scratch                                    # noqa: E402

ENV = BL.detect_env()
SCRATCH = _scratch.run_dir("supply") if ENV == "linux" else None
GI, CI = "tester (gen-inhibit)", "tester (charge-interposer)"
ME = os.getpid()
SUPPLY_IDS = {"vid": "0416", "pid": "5011", "serial": "TESTSUPPLY01"}
FAST = {"gap_s": 0.003, "read_gap_s": 0.01, "timeout_s": 0.15}
BOARDS = {"wican": "not read: supply test", "x2": "not read: supply test"}
SRCS = {m: m.__file__ for m in (SVC, CL, KA)}


class Fail(Exception):
    pass


def check(cond, what):
    if not cond:
        raise Fail(what)


def wait_for(cond, what, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise Fail("timed out waiting: %s" % what)


def refused(fn, *a, kind=None, **k):
    try:
        fn(*a, **k)
    except CL.Refused as e:
        if kind is not None and e.kind != kind:
            raise Fail("%s refused as %r, not %r: %s" % (fn.__name__, e.kind, kind, e))
        return str(e)
    raise Fail("%s was not refused" % getattr(fn, "__name__", fn))


def entries(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="ascii") as f:
        return [json.loads(ln) for ln in f]


class Bench:
    """A made-up bench: the simulated supply, its by-id link, a bench log and
    the service's configuration. start() runs a service on it."""

    n = 0

    def __init__(self, sim=None, holder=(GI, ME, None), bits=None, **cfg_kw):
        Bench.n += 1
        self.name = "b%d" % Bench.n
        self.dir = os.path.join(SCRATCH, self.name)
        os.makedirs(self.dir)
        self.sim = (sim or FakeKA3005P()).start()
        self.fs = FakeSerial(os.path.join(self.dir, "dev"))
        tty = os.path.basename(self.sim.port)
        link = self.fs.add(tty, SUPPLY_IDS["vid"], SUPPLY_IDS["pid"],
                           serial=SUPPLY_IDS["serial"], interface=0, interfaces=1)
        os.remove(link)
        os.symlink(self.sim.port, link)
        self.fs.install(SD)
        self.log = os.path.join(self.dir, "bench-log.jsonl")
        BL.init(self.log, GI)
        if holder:
            BL.take(self.log, holder[0], holder[1], run=holder[2])
        self.cfg = {
            "device": dict(SUPPLY_IDS, interface=0),
            "idn_prefix": "KORAD KA3005P",
            "absolute": {"volts": 14.5, "amps": 0.2},
            "socket": "%s.sock" % self.name,
            "log": os.path.join(self.dir, "service.jsonl"),
            "journals": os.path.join(self.dir, "journals"),
            "bench_log": self.log,
            "poll_s": 0.05, "lock_poll_s": 0.05, "over_tolerance_v": 0.5,
            "settle_tolerance_v": 0.2, "settle_timeout_s": 1.0,
            "protection_bits": bits if bits is not None else {"ovp": None, "ocp": None},
            "socket_mode": "0660",
        }
        self.cfg.update(cfg_kw)
        self.svc = self.srv = None

    def start(self):
        self.svc = SVC.Service(dict(self.cfg), driver_kw=FAST)
        self.srv = SVC.serve(self.svc)
        return self

    def restart(self):
        self.stop_service()
        return self.start()

    def client(self, role=GI, pid=ME):
        return CL.Supply(role, pid, socket_path=self.cfg["socket"], timeout_s=10,
                         settle_poll_s=0.05)

    def stop_service(self):
        if self.srv is not None:
            SVC.stop(self.svc, self.srv)
            self.srv = None

    def close(self):
        try:
            self.stop_service()
        finally:
            self.fs.uninstall()
            self.sim.stop()

    def log_entries(self):
        return entries(self.cfg["log"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def only_linux(t):
    t.env = "linux"
    return t


def sleeper():
    """A process that is not this one's ancestor or descendant's holder."""
    return subprocess.Popen(["sleep", "60"])


CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from vtrux_bench.supply import client as CL
s = CL.Supply(sys.argv[2], int(sys.argv[3]), socket_path=sys.argv[4])
try:
    r = s.set(volts=float(sys.argv[5]))
    print(json.dumps({"ok": True, "read_back": r["read_back"]}))
except CL.Refused as e:
    print(json.dumps({"ok": False, "kind": e.kind, "error": str(e)}))
"""


def child_set(b, role, pid, volts):
    """client.set(volts) from a child process of this one."""
    out = subprocess.run([sys.executable, "-B", "-c", CHILD, ROOT, role, str(pid),
                          b.cfg["socket"], str(volts)], capture_output=True,
                         text=True, timeout=30,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if out.returncode:
        raise Fail("the child client failed: %s" % out.stderr[-400:])
    return json.loads(out.stdout)


# ---- the tests ----------------------------------------------------------

@only_linux
def test_driver():
    """The driver's commands are the KA3005P's, and a garbled or missing
    answer is a SupplyError, never a value."""
    sim = FakeKA3005P().start()
    try:
        sup = KA.KA3005P(KA.open_port(sim.port), **FAST)
        sup.set_voltage(12)
        sup.set_current(0.2)
        sup.output(True)
        check(sup.voltage_set() == 12.0 and sup.current_set() == 0.2, "set values")
        check(sim.sent("VSET1:") == ["VSET1:12.00"], "VSET1 form: %s" % sim.sent("VSET1:"))
        check(sim.sent("ISET1:") == ["ISET1:0.200"], "ISET1 form: %s" % sim.sent("ISET1:"))
        check(sup.output_on() and sup.voltage_out() == 12.0, "output on")
        for knob, q, fn in (("garble", "VSET1?", sup.voltage_set),
                            ("garble_ascii", "VOUT1?", sup.voltage_out),
                            ("mute", "IOUT1?", sup.current_out),
                            ("garble", "STATUS?", sup.status),
                            ("garble", "*IDN?", sup.idn)):
            getattr(sim, knob).add(q)
            try:
                v = fn()
            except KA.SupplyError:
                pass
            else:
                raise Fail("%s %s gave %r, not a SupplyError" % (knob, q, v))
            getattr(sim, knob).discard(q)
        sup.close()
    finally:
        sim.stop()


@only_linux
def test_k2_exclusive():
    """K2 (K10's third item): while the service holds the supply's port, a
    second process cannot open it."""
    with Bench() as b:
        b.start()
        code = ("import os, sys\n"
                "try:\n    os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY)\n"
                "    print('opened')\n"
                "except OSError as e:\n    print('refused', e.errno)\n")
        out = subprocess.run([sys.executable, "-B", "-c", code, b.sim.port],
                             capture_output=True, text=True, timeout=30)
        check(out.stdout.startswith("refused 16"),
              "a second process opening the port: %r" % out.stdout.strip())
        # once the service stops, the port is free again, so a restart (K6)
        # can open it
        b.stop_service()
        out = subprocess.run([sys.executable, "-B", "-c", code, b.sim.port],
                             capture_output=True, text=True, timeout=30)
        check(out.stdout.startswith("opened"),
              "the port after the service stopped: %r" % out.stdout.strip())


@only_linux
def test_k2_found_and_confirmed():
    """K2 with S1-S3: the supply is found by its USB IDs and confirmed by
    *IDN?; another identity, or no device matching, refuses; the start is
    logged with the port and identity. The output left on is turned off at
    the start, read back."""
    with Bench(FakeKA3005P(idn="ACME PSU 1.0")) as b:
        try:
            b.start()
        except SVC.Refused as e:
            check(e.kind == "idn", "refused as %s" % e.kind)
        else:
            raise Fail("a supply answering ACME PSU was accepted")
    with Bench(device=dict(SUPPLY_IDS, serial="OTHERSERIAL", interface=0)) as b:
        try:
            b.start()
        except SD.Refused as e:
            check("no device matches" in str(e), str(e))
        else:
            raise Fail("a device with another serial was accepted")
    sim = FakeKA3005P()
    sim.out = True
    with Bench(sim) as b:
        b.start()
        start = [e for e in b.log_entries() if e["kind"] == "start"]
        check(len(start) == 1 and start[0]["port"]["serial"] == "TESTSUPPLY01"
              and start[0]["idn"].startswith("KORAD KA3005P"), "start record %s" % start)
        check(not sim.out, "the output left on at start is still on")
        check(any(e["kind"] == "off" for e in b.log_entries()), "no off record")


@only_linux
def test_k1_protocol():
    """K1: a request of another protocol version is refused and logged."""
    import socket as S
    with Bench() as b:
        b.start()
        for v in (PROTOCOL + 1, None):
            s = S.socket(S.AF_UNIX, S.SOCK_STREAM)
            s.connect(b.cfg["socket"])
            req = {"op": "hello", "role": GI, "pid": ME}
            if v is not None:
                req["v"] = v
            s.sendall((json.dumps(req) + "\n").encode())
            reply = json.loads(s.makefile().readline())
            s.close()
            check(not reply["ok"] and reply["refused"] == "protocol",
                  "protocol %r: %s" % (v, reply))
        check(any(e.get("rule") == "K1" for e in b.log_entries()), "not logged")
        check(b.client().hello()["protocol"] == PROTOCOL, "this protocol refused")


@only_linux
def test_k3_absolute():
    """K3's absolute tier (K10's first item): 20 V against 14.5 V, and
    0.3 A against 0.2 A, are refused and logged, and nothing reaches the
    supply. A setpoint above the limit left on the supply (its front panel)
    stops the output being turned on. At the limit is accepted."""
    with Bench() as b:
        b.start()
        c = b.client()
        refused(c.set, volts=20.0, kind="limit")
        refused(c.set, amps=0.3, kind="limit")
        refused(c.set, volts=-1.0, kind="limit")
        check(b.sim.sent("VSET1:") == [] and b.sim.sent("ISET1:") == [],
              "a refused setpoint reached the supply: %s" % b.sim.commands)
        k3 = [e for e in b.log_entries() if e.get("rule") == "K3"]
        check(len(k3) == 3, "%d K3 refusals logged, not 3" % len(k3))
        b.sim.vset = 20.0
        refused(c.output, True, kind="limit")
        check(b.sim.sent("OUT1") == [] and not b.sim.out, "OUT1 reached the supply")
        c.set(volts=14.5, amps=0.2)
        check(b.sim.vset == 14.5 and b.sim.iset == 0.2, "the limit itself refused")


def _tripped(b, reason="readback"):
    check(b.svc.fault is not None and b.svc.fault["reason"] == reason,
          "fault %s, not %s" % (b.svc.fault, reason))
    check(not b.sim.out, "the output is still on after the fault")


@only_linux
def test_k4_readback():
    """K4 (K10's fourth item): a read-back off by more than the resolution,
    a garbled answer, a missing answer, or an output that reads back wrong
    each turn the output off, read back, and latch a fault. Off by the
    resolution itself is accepted."""
    with Bench() as b:
        b.start()
        c = b.client()
        b.sim.vset_offset = 0.01
        c.set(volts=12.0)
        check(b.svc.fault is None, "a read-back within 10 mV tripped")
        b.sim.vset_offset = 0.0
        c.run_start(12.0, 0.1)
        b.sim.vset_offset = 0.05
        refused(c.set, volts=11.0, kind="fault")
        _tripped(b)
        check(b.sim.sent("OUT0")[-1:] == ["OUT0"], "no OUT0 after the mismatch")
    for knob, q, call in (("garble", "ISET1?", lambda c: c.set(amps=0.1)),
                          ("mute", "STATUS?", lambda c: c.output(True)),
                          ("stuck", None, lambda c: c.output(True))):
        with Bench() as b:
            b.start()
            c = b.client()
            c.set(volts=12.0, amps=0.1)
            if knob == "stuck":
                b.sim.out_stuck = False
            else:
                getattr(b.sim, knob).add(q)
            refused(call, c, kind="fault")
            _tripped(b)


@only_linux
def test_k6_latched():
    """K6: after a fault every write is refused, reads still answer and name
    the fault, and only a restart clears it. The fault is in the service's
    log and the holder's journal."""
    with Bench() as b:
        b.start()
        c = b.client()
        b.sim.vset_offset = 0.5
        refused(c.set, volts=12.0, kind="fault")
        b.sim.vset_offset = 0.0
        n = len(b.sim.commands)
        refused(c.set, volts=5.0, kind="fault")
        refused(c.output, True, kind="fault")
        check(not [x for _, x in b.sim.commands[n:] if not x.endswith("?")],
              "a write reached the supply under a fault")
        r = c.read()
        check(r["fault"] and r["fault"]["reason"] == "readback", "read: %s" % r["fault"])
        faults = [e for e in b.log_entries() if e["kind"] == "fault"]
        check(len(faults) == 1, "%d faults logged" % len(faults))
        jn = entries(r["journal"])
        check(any(e["kind"] == "fault" for e in jn), "no fault in the journal")
        b.restart()
        check(b.client().read()["fault"] is None, "the restart kept the fault")
        b.client().set(volts=5.0)
        check(b.sim.vset == 5.0, "a write after the restart")


@only_linux
def test_k5_watch():
    """K5: while on, a measured voltage above the commanded one plus the
    tolerance trips, and so does one above the absolute limit though within
    the tolerance; a measurement within both does not. Measurements are
    logged with their time."""
    with Bench() as b:
        b.start()
        c = b.client()
        c.run_start(10.0, 0.1)
        b.sim.vout_extra = 0.3
        time.sleep(0.4)
        check(b.svc.fault is None, "within tolerance tripped: %s" % b.svc.fault)
        b.sim.vout_extra = 3.0
        wait_for(lambda: b.svc.fault is not None, "the over-voltage trip")
        _tripped(b, "over-voltage")
        check(b.svc.fault["detail"]["measured"] == 13.0, b.svc.fault)
        m = [e for e in b.log_entries() if e["kind"] == "measure"]
        check(m and all("ts" in e and "." in e["ts"] for e in m), "measurements")
    with Bench() as b:
        b.start()
        c = b.client()
        c.run_start(14.4, 0.1)
        b.sim.vout_extra = 0.3
        wait_for(lambda: b.svc.fault is not None, "the absolute-limit trip")
        _tripped(b, "over-voltage")


@only_linux
def test_k5_unreadable():
    """Q7: a garbled or missing measurement while on trips as K4 does."""
    for knob, q in (("garble", "VOUT1?"), ("mute", "IOUT1?")):
        with Bench() as b:
            b.start()
            b.client().run_start(12.0, 0.1)
            getattr(b.sim, knob).add(q)
            wait_for(lambda: b.svc.fault is not None, "the %s %s trip" % (knob, q))
            _tripped(b, "watch")


@only_linux
def test_k7_holder():
    """K7 (K10's fifth item): no write while the lock is free, held by
    another role, or held by this role under another pid; each refusal is
    logged and nothing reaches the supply. Reads are open to all."""
    p = sleeper()
    try:
        for holder in (None, (CI, ME, None), (GI, p.pid, None)):
            with Bench(holder=holder) as b:
                b.start()
                c = b.client()
                refused(c.set, volts=5.0, kind="holder")
                refused(c.output, True, kind="holder")
                check(b.sim.sent("VSET1:") == [] and b.sim.sent("OUT") == [],
                      "a non-holder's write reached the supply")
                check(len([e for e in b.log_entries() if e.get("rule") == "K7"]) == 2,
                      "refusals not logged")
                check(c.read()["volts_set"] == 5.0, "a read refused")
                check(b.client(CI, ME).read()["on"] is False, "another role's read")
    finally:
        p.kill()
        p.wait()


@only_linux
def test_k7_claim():
    """Q2: a client claiming the holder's pid from outside its process tree
    is refused and logged; a child of the holder's process may write."""
    p = sleeper()
    try:
        with Bench(holder=(GI, p.pid, None)) as b:
            b.start()
            refused(b.client(GI, p.pid).set, volts=5.0, kind="claim")
            check(b.sim.sent("VSET1:") == [], "the claim's write reached the supply")
            check(any(e.get("rule") == "K7-claim" for e in b.log_entries()),
                  "the claim not logged")
    finally:
        p.kill()
        p.wait()
    with Bench(holder=(GI, ME, None)) as b:
        b.start()
        r = child_set(b, GI, ME, 6.0)
        check(r["ok"] and r["read_back"]["volts"] == 6.0, "a child of the holder: %s" % r)


@only_linux
def test_k8_sequence():
    """K8 (the library's calls): a run's start turns the output off first,
    even when it was on, then sets the setpoint and the limit, then turns it
    on, each read back, and waits for it to settle; a case entry passes only
    on at the setpoint in force; a step moves the setpoint with the output
    on; a case's cycle goes off and on again; the run's end turns it off."""
    with Bench() as b:
        b.start()
        b.svc.on_by = None
        b.sim.out = True             # on when the run starts (not by this run)
        c = b.client()
        n = len(b.sim.commands)
        r = c.run_start(12.0, 0.15)
        check(abs(r["volts_out"] - 12.0) <= 0.2, "not settled: %s" % r)
        seq = [x for _, x in b.sim.commands[n:]]
        writes = [x for x in seq if not x.endswith("?")]
        check(writes == ["OUT0", "VSET1:12.00", "ISET1:0.150", "OUT1"],
              "run start writes: %s" % writes)
        for w, q in (("OUT0", "STATUS?"), ("VSET1:12.00", "VSET1?"),
                     ("ISET1:0.150", "ISET1?"), ("OUT1", "STATUS?")):
            i = seq.index(w)
            check(seq[i + 1] == q, "%s read back by %s, not %s" % (w, seq[i + 1], q))
        c.case_entry(12.0, 0.15)
        try:
            c.case_entry(13.0, 0.15)
        except CL.NotInForce:
            pass
        else:
            raise Fail("a case entry at another setpoint passed")
        c.step(11.5)
        check(b.sim.vset == 11.5 and b.sim.out, "the step")
        n = len(b.sim.commands)
        c.case_cycle(13.0, 0.15)
        writes = [x for _, x in b.sim.commands[n:] if not x.endswith("?")]
        check(writes == ["OUT0", "VSET1:13.00", "ISET1:0.150", "OUT1"],
              "case cycle writes: %s" % writes)
        c.run_end()
        check(not b.sim.out, "the run's end left the output on")
        try:
            c.case_entry(13.0, 0.15)
        except CL.NotInForce:
            pass
        else:
            raise Fail("a case entry with the output off passed")


@only_linux
def test_k8_not_settled():
    """Q8: a voltage not settled by the timeout turns the output off, read
    back, and raises NotSettled, with no latched fault."""
    sim = FakeKA3005P()
    sim.rise_s = 100.0
    with Bench(sim) as b:
        b.start()
        try:
            b.client().run_start(12.0, 0.1)
        except CL.NotSettled:
            pass
        else:
            raise Fail("a voltage that never settled passed")
        check(not b.sim.out, "the output is on after not settling")
        check(b.svc.fault is None, "not settling latched a fault: %s" % b.svc.fault)


def _on_then(b, c, event, *a, **k):
    c.run_start(12.0, 0.1)
    event(b.log, *a, **k)
    wait_for(lambda: not b.sim.out, "the output off after %s" % event.__name__)
    wait_for(lambda: any(e["kind"] == "off" and "bench log" in e["cause"]
                         for e in b.log_entries()), "the off record")


HOLDER_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from vtrux_bench.supply import client as CL
go = sys.argv[3] + ".go"
while not os.path.exists(go):
    time.sleep(0.02)
CL.Supply(sys.argv[2], os.getpid(), socket_path=sys.argv[4]).run_start(12.0, 0.1)
open(sys.argv[3] + ".on", "w").close()
time.sleep(60)
"""


@only_linux
def test_k8a_off():
    """K8a (K10's sixth item): the output goes off, read back, when the
    holder that turned it on releases, releases and takes again
    (--keep-lock), or is cleared after it died. At its run's pause it stays
    on; another role's take during the pause turns it off; and at the
    resume the runner's check cycles it, while one that finds it as left
    keeps it."""
    with Bench() as b:
        b.start()
        _on_then(b, b.client(), BL.release, GI, BOARDS)
    with Bench() as b:
        b.start()
        _on_then(b, b.client(), BL.release_and_take, GI, BOARDS, ME)
    with Bench(holder=None) as b:
        b.start()
        flag = os.path.join(b.dir, "holder")
        p = subprocess.Popen([sys.executable, "-B", "-c", HOLDER_CHILD, ROOT, GI,
                              flag, b.cfg["socket"]],
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            BL.take(b.log, GI, p.pid)
            open(flag + ".go", "w").close()
            wait_for(lambda: os.path.exists(flag + ".on"), "the child's run start", 10)
            check(b.sim.out, "the child did not turn the output on")
        finally:
            p.kill()
            p.wait()
        BL.write(b.log, {"event": "clear", "who": CI, "reason": "supply test"})
        wait_for(lambda: not b.sim.out, "the output off after a clear")
    with Bench(holder=(GI, ME, "r1")) as b:
        b.start()
        c = b.client()
        c.run_start(12.0, 0.1)
        BL.free_paused(b.log, GI, "r1", BOARDS)
        time.sleep(0.3)
        check(b.sim.out, "the run's pause turned the output off")
        BL.take(b.log, GI, ME, run="r1")
        time.sleep(0.3)
        check(b.sim.out, "the run's resume turned the output off")
        how, _ = c.resume_check(12.0, 0.1)
        check(how == "kept", "a resume finding it as left: %s" % how)
        BL.free_paused(b.log, GI, "r1", BOARDS)
        BL.take(b.log, CI, ME)
        wait_for(lambda: not b.sim.out, "the output off after a take in the pause")
        BL.release(b.log, CI, BOARDS)
        BL.take(b.log, GI, ME, run="r1")
        how, r = c.resume_check(12.0, 0.1)
        check(how == "cycled" and b.sim.out, "the resume after another's take: %s" % how)
    p = sleeper()
    try:
        with Bench(holder=(GI, ME, "r1")) as b:
            b.start()
            b.client().run_start(12.0, 0.1)
            BL.free_paused(b.log, GI, "r1", BOARDS)
            BL.take(b.log, GI, p.pid, run="r1")   # the run's id, another pid
            wait_for(lambda: not b.sim.out, "the output off after a take of the "
                     "paused run under another pid")
    finally:
        p.kill()
        p.wait()


@only_linux
def test_k8a_unreadable():
    """Q5: a bench log that does not replay turns the output off, read
    back, with no latched fault, and the cause logged; writes are refused
    until it replays again."""
    with Bench() as b:
        b.start()
        c = b.client()
        c.run_start(12.0, 0.1)
        with open(b.log, "a") as f:
            f.write("not json\n")
        wait_for(lambda: not b.sim.out, "the output off with an unreadable log")
        check(b.svc.fault is None, "an unreadable log latched: %s" % b.svc.fault)
        bad = [e for e in b.log_entries() if e["kind"] == "lock-unreadable"]
        check(bad and "not JSON" in bad[0]["cause"], "the cause: %s" % bad)
        refused(c.set, volts=5.0, kind="holder")
        os.remove(b.log)
        BL.init(b.log, GI)
        BL.take(b.log, GI, ME)
        c.set(volts=5.0)
        check(b.sim.vset == 5.0, "a write once the log replays")


@only_linux
def test_k8b_protection():
    """Q6: with no STATUS? bit configured, OVP and OCP writes are refused
    and nothing is sent; with bits configured each is set and read back; a
    supply whose status does not report it trips."""
    with Bench() as b:
        b.start()
        refused(b.client().protection, "ovp", True, kind="protection")
        refused(b.client().protection, "ocp", False, kind="protection")
        check(b.sim.sent("OVP") == [] and b.sim.sent("OCP") == [], "sent anyway")
    with Bench(bits={"ovp": 7, "ocp": 5}) as b:
        b.start()
        c = b.client()
        c.protection("ovp", True)
        c.protection("ocp", True)
        c.protection("ocp", False)
        check(b.sim.ovp and not b.sim.ocp, "OVP/OCP not as set")
    with Bench(FakeKA3005P(prot_bits={}), bits={"ovp": 7, "ocp": 5}) as b:
        b.start()
        refused(b.client().protection, "ovp", True, kind="fault")
        _tripped(b)


@only_linux
def test_k9_journal():
    """K9 and Q3: every command, read-back and measurement is in the
    service's log and the holder's journal, with the time it was read; the
    client copies the journal into a run's record, each copy taking only
    what is new; a hand release names the journal."""
    with Bench() as b:
        b.start()
        c = b.client()
        c.run_start(12.0, 0.1)
        path = c.journal_path()
        check(os.path.basename(path) == "tester-gen-inhibit_pid%d_hand.jsonl" % ME,
              "journal name %s" % path)
        time.sleep(0.2)
        jn = entries(path)
        kinds = {e["kind"] for e in jn}
        check({"command", "readback", "measure"} <= kinds, "journal kinds %s" % kinds)
        check(all(e["holder"] == {"who": GI, "pid": ME, "run": None} for e in jn),
              "an entry of another holder")
        cmds = [e["command"] for e in jn if e["kind"] == "command"]
        check(cmds[:4] == ["OUT0", "VSET1:12.00", "ISET1:0.100", "OUT1"], cmds)
        log_cmds = [e["command"] for e in b.log_entries() if e["kind"] == "command"]
        check(log_cmds[:4] == cmds[:4], "the log's commands %s" % log_cmds[:4])
        rec = os.path.join(b.dir, "record-supply.jsonl")
        off = c.copy_journal(rec)
        n1 = len(entries(rec))
        check(n1 >= len(jn), "copied %d of %d" % (n1, len(jn)))
        line = c.hand_release()
        check(path in line, "the hand release line: %s" % line)
        c.copy_journal(rec, off)
        copied = entries(rec)
        check(len(copied) == len(entries(path)) and copied[-1] == entries(path)[-1],
              "the second copy is not the rest of the journal")


@only_linux
def test_config():
    """K3: the configuration holding the absolute limits must be owned by
    root and writable by nobody else; a malformed one refuses."""
    d = os.path.join(SCRATCH, "config")
    os.makedirs(d, exist_ok=True)
    good = {"device": dict(SUPPLY_IDS, interface=0), "idn_prefix": "KORAD",
            "absolute": {"volts": 14.5, "amps": 0.2}, "socket": "s", "log": "l",
            "journals": "j", "bench_log": "b", "poll_s": 0.5, "lock_poll_s": 1,
            "over_tolerance_v": 0.5, "settle_tolerance_v": 0.2,
            "settle_timeout_s": 3}
    p = os.path.join(d, "config.json")
    with open(p, "w") as f:
        json.dump(good, f)
    os.chmod(p, 0o644)
    try:
        SVC.load_config(p)
    except SVC.ConfigError as e:
        check("owned by root" in str(e), str(e))
    else:
        if os.getuid() != 0:
            raise Fail("a configuration owned by uid %d was accepted" % os.getuid())
    SVC.load_config(p, require_root=False)
    for bad in ({"absolute": {"volts": 14.5}}, {"poll_s": 0}, {"extra": 1},
                {"protection_bits": {"ovp": 9}}):
        cfg = dict(good, **bad)
        with open(p, "w") as f:
            json.dump(cfg, f)
        try:
            SVC.load_config(p, require_root=False)
        except SVC.ConfigError:
            continue
        raise Fail("a malformed configuration was accepted: %s" % bad)


TESTS = [test_driver, test_k2_exclusive, test_k2_found_and_confirmed,
         test_k1_protocol, test_k3_absolute, test_k4_readback, test_k6_latched,
         test_k5_watch, test_k5_unreadable, test_k7_holder, test_k7_claim,
         test_k8_sequence, test_k8_not_settled, test_k8a_off, test_k8a_unreadable,
         test_k8b_protection, test_k9_journal, test_config]


# ---- the mutants ----------------------------------------------------------

ORIGINAL = {}
for _m, _p in SRCS.items():
    with open(_p, encoding="utf-8") as _f:
        ORIGINAL[_m] = _f.read()


def _load(mod, text):
    exec(compile(text, SRCS[mod], "exec"), mod.__dict__)


def _edit(mod, old, new):
    def on():
        n = ORIGINAL[mod].count(old)
        if n != 1:
            raise RuntimeError("mutant text found %d times: %r" % (n, old[:60]))
        _load(mod, ORIGINAL[mod].replace(old, new))

    def off():
        _load(mod, ORIGINAL[mod])
    return on, off


MUTANTS = {
    "test_driver": [
        ("VSET1 with one decimal",
         _edit(KA, 'self.send("VSET1:%05.2f" % volts)', 'self.send("VSET1:%.1f" % volts)')),
        ("any answer a number",
         _edit(KA, 'NUMBER = re.compile(r"\\d{1,2}\\.\\d{2,3}\\Z")',
               'NUMBER = re.compile(r"(?s).*")')),
        ("STATUS? of any length",
         _edit(KA, "if len(raw) != 1:", "if not raw:")),
    ],
    "test_k2_exclusive": [
        ("no TIOCEXCL",
         _edit(KA, "fcntl.ioctl(fd, termios.TIOCEXCL)", "pass")),
        ("the hold kept at close",
         _edit(KA, "fcntl.ioctl(self.fd, termios.TIOCNXCL)", "pass")),
    ],
    "test_k2_found_and_confirmed": [
        ("any *IDN? answer",
         _edit(SVC, "if not idn.startswith(self.cfg[\"idn_prefix\"]):", "if False:")),
        ("the output left on at start",
         _edit(SVC, 'self.turn_off("the output was on when the service started")', "pass")),
    ],
    "test_k1_protocol": [
        ("any protocol",
         _edit(SVC, 'if req.get("v") != PROTOCOL:', "if False:")),
    ],
    "test_k3_absolute": [
        ("no voltage limit",
         _edit(SVC, 'if volts is not None and not -EPS <= volts <= a["volts"] + EPS:',
               "if False:")),
        ("no current limit",
         _edit(SVC, 'if amps is not None and not -EPS <= amps <= a["amps"] + EPS:',
               "if False:")),
        ("output on without the setpoint check",
         _edit(SVC, 'self._check_limits(volts, amps, holder=h, what="output on at the "\n'
               '                               "setpoints read back")', "pass")),
        ("a refusal not logged",
         _edit(SVC, 'self.record("refused", holder=holder, rule="K3", what=what,\n'
               '                        volts=volts, amps=amps, why="; ".join(bad))',
               "pass")),
    ],
    "test_k4_readback": [
        ("the read-back tolerance 1 V",
         _edit(SVC, "V_RES = 0.010 ", "V_RES = 1.000 ")),
        ("the output not read back",
         _edit(SVC, "if sup.output_on(st) != on:", "if False:")),
        ("a garbled ISET1? accepted",
         _edit(SVC, 'self.trip("readback", what="ISET1?", want=amps, error=str(e))\n'
               '            raise Refused("fault", "ISET1? read-back failed: %s" % e)',
               "got = amps")),
        ("a trip leaves the output on",
         _edit(SVC, '            self.send("OUT0", self.supply.output, False)\n'
               '            off = not', '            off = not')),
    ],
    "test_k6_latched": [
        ("a fault not checked on writes",
         _edit(SVC, "            if self.fault is not None:\n"
               "                self.record(\"refused\", rule=\"K6\"",
               "            if False:\n                self.record(\"refused\", rule=\"K6\"")),
        ("a fault not recorded",
         _edit(SVC, 'self.record("fault", holder=holder, fault=self.fault)', "pass")),
    ],
    "test_k5_watch": [
        ("no commanded-voltage check",
         _edit(SVC, "if vo > over + EPS or vo > a + EPS:", "if vo > a + EPS:")),
        ("no absolute check",
         _edit(SVC, "if vo > over + EPS or vo > a + EPS:", "if vo > over + EPS:")),
        ("the watch never polls",
         _edit(SVC, "            if self.on_by is None or self.fault is not None:\n"
               "                return\n", "            return\n")),
    ],
    "test_k5_unreadable": [
        ("a failed poll ignored",
         _edit(SVC, '                self.trip("watch", error=str(e))\n                return',
               "                return")),
    ],
    "test_k7_holder": [
        ("any pid of the role",
         _edit(SVC, 'if h is None or h["who"] != role or h["pid"] != pid:',
               'if h is None or h["who"] != role:')),
        ("any holder",
         _edit(SVC, 'if h is None or h["who"] != role or h["pid"] != pid:',
               "if h is None:")),
        ("no lock check",
         _edit(SVC, "        h = self.require_holder(req, peer)\n",
               "        h = {\"who\": role, \"pid\": pid, \"run\": None}\n")),
    ],
    "test_k7_claim": [
        ("no peer check",
         _edit(SVC, "        if pid not in anc:\n", "        if False:\n")),
    ],
    "test_k8_sequence": [
        ("the run start not off first",
         _edit(CL, "        self.output(False)\n        self.set(volts=volts)\n",
               "        self.set(volts=volts)\n")),
        ("the case entry not compared",
         _edit(CL, "        if not self._in_force(r, volts, amps):\n            raise NotInForce",
               "        if False:\n            raise NotInForce")),
        ("the run end leaves it on",
         _edit(CL, '        r = self.output(False)\n        return r.get("journal")',
               '        return self.read().get("journal")')),
    ],
    "test_k8_not_settled": [
        ("not settled left on",
         _edit(CL, "        self.output(False)\n        raise NotSettled",
               "        raise NotSettled")),
    ],
    "test_k8a_off": [
        ("a release keeps it on",
         _edit(SVC, 'if kind in ("request", "withdraw"):',
               'if kind in ("request", "withdraw", "release"):')),
        ("a clear keeps it on",
         _edit(SVC, 'if kind in ("request", "withdraw"):',
               'if kind in ("request", "withdraw", "clear"):')),
        ("a pause turns it off",
         _edit(SVC, '        if kind == "free_paused":\n            return True',
               '        if kind == "free_paused":\n            return False')),
        ("any take in the pause keeps it",
         _edit(SVC, 'return kind == "take" and ev.get("pid") == ob["pid"]',
               'return kind == "take"')),
        ("another role's take keeps it",
         _edit(SVC, 'if not ob["run"] or ev.get("who") != ob["who"] or ev.get("run") != ob["run"]:',
               'if not ob["run"] and kind != "take":')),
        ("the resume never cycles",
         _edit(CL, "        if self._in_force(r, volts, amps):\n            return \"kept\", r",
               "        if True:\n            return \"kept\", r")),
    ],
    "test_k8a_unreadable": [
        ("an unreadable log keeps it on",
         _edit(SVC, '            if self.on_by is not None:\n'
               '                self.turn_off("the bench log cannot be read',
               '            if False:\n                self.turn_off("the bench log cannot be read')),
    ],
    "test_k8b_protection": [
        ("enabled with no bit",
         _edit(SVC, "        if bit is None:\n            self.record(\"refused\", holder=h, rule=\"K8b\"",
               "        if False:\n            self.record(\"refused\", holder=h, rule=\"K8b\"")),
        ("protection not read back",
         _edit(SVC, "if sup.protection_on(st, bit) != on:", "if False:")),
    ],
    "test_k9_journal": [
        ("no journal",
         _edit(SVC, "        if key:\n            path = os.path.join(self.cfg[\"journals\"]",
               "        if False:\n            path = os.path.join(self.cfg[\"journals\"]")),
        ("the copy restarts at 0",
         _edit(CL, "            src.seek(offset)\n", "            src.seek(0)\n")),
    ],
    "test_config": [
        ("not root-owned accepted",
         _edit(SVC, "if require_root and (st.st_uid != 0 or st.st_mode & 0o022):",
               "if False:")),
        ("unknown keys accepted",
         _edit(SVC, "    if missing or extra:\n", "    if missing:\n")),
    ],
}


def _run(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    print("environment: %s" % ENV)
    if SCRATCH:
        os.chdir(SCRATCH)
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = total = other = 0
    if can_fail:
        print("-- control: every test, unmutated", flush=True)
        control_failed = []
        for t in tests:
            if getattr(t, "env", ENV) != ENV:
                continue
            outcome = _run(t)
            print("%s  %-28s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""), flush=True)
            if outcome is not None:
                control_failed.append(t.__name__)
        if control_failed:
            print("\nREFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing; no mutant is counted" % ", ".join(control_failed))
            return 1
        print("-- mutants", flush=True)
    for t in tests:
        if getattr(t, "env", ENV) != ENV:
            print("NOT RUN (other env)  %-28s %s only" % (t.__name__, t.env))
            other += 1
            continue
        if not can_fail:
            total += 1
            outcome = _run(t)
            print("%s  %-28s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""), flush=True)
            bad += outcome is not None
            continue
        for label, (on, off) in MUTANTS[t.__name__]:
            total += 1
            try:
                on()
            except RuntimeError as e:
                print("FAIL  %-28s %-36s NOT APPLIED: %s" % (t.__name__, label, e))
                bad += 1
                continue
            try:
                outcome = _run(t)
            finally:
                off()
            ok = outcome is not None
            print("%s  %-28s %-36s %s" % (
                "ok  " if ok else "FAIL", t.__name__, label,
                ("fails (" + outcome[:70] + ")") if ok else
                "STILL PASSES -- the test checks nothing"), flush=True)
            bad += not ok
    print("\n%d of %d %s; %d NOT RUN (other env)%s"
          % (total - bad, total, "mutants caught" if can_fail else "passed",
             other, "; the control passes" if can_fail else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
