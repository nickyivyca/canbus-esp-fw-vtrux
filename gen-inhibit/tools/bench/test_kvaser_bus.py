#!/usr/bin/env python3
"""Test kvaser_bus.py, the one way the bench harness opens the Kvaser and the Innomaker, and
scan gen-inhibit's tools for an open that goes round it (gen-inhibit reviewer, 2026-11-11).

  unit      against a fake can and a fake pinned canbench: kvaser_bus() and gs_usb_bus() load
            the pin (can_tools_pin.use()) and apply canbench.live.linux_fixes before the open,
            and open nothing when the pin or the fixes refuse; the fixes apply once a process;
            close_gs_usb() shuts the bus down, then releases its pyusb handle through canbench's
            release_gs_usb, also when the shutdown raises; bus() sends kvaser and gs_usb through
            the two openers, and anything else straight to can.Bus with its arguments
  guard     (reviewer, 2026-11-12) require_bench() runs first: with the lock free, unreadable,
            held by another role, or held under a pid that is neither this process nor an
            ancestor, kvaser_bus(), gs_usb_bus() and bus(interface="kvaser") raise BenchNotHeld
            before the pin, the fixes or an open; held by a gen-inhibit role under this process's
            pid (a runner) or an ancestor's (a hand take's session; the implementor on a loan),
            they open. _ancestors() walks /proc. In a child, the REAL guard: the real kvaser_bus,
            the real python-can (its Bus entry points and CANlib's canOpenChannel replaced by
            tripwires first, so a broken guard still opens nothing), no vtrux_bench importable
            and a tool module with its own fake `can`, as test_e4_rawpost had: every open
            refuses with BenchNotHeld, and no tripwire is reached
  pin       in a child interpreter, with the real pin: linux_fixes comes from the pinned folder
            (scratch/can-tools/<commit>/, can_tools_pin.py install), and on Linux the real
            fixes are installed in python-can's Kvaser backend and in gs_usb, where those are
            importable
  scan      every .py file under gen-inhibit/ but firmware/ (the upstream WiCAN tree), parsed:
            no call of Bus, ThreadSafeBus, KvaserBus or GsUsbBus that opens the Kvaser or the
            Innomaker directly (interface "kvaser" or "gs_usb"), names its interface at run time
            (not a string literal), passes **kwargs, passes no interface= (python-can then takes
            it from its config) or passes the interface by position. ALLOWED lists the files
            that may, with the number of such calls and why; a different number fails. Each
            file that opens with gs_usb_bus() calls close_gs_usb() at least as often
            (CLOSE_EXEMPT: kvaser_bus.py and this test). The scan is shown able to fail on a
            snippet of each form, and on real files reverted. Not scanned: a bus(interface=
            "gs_usb") caller's close; today bus() opens pcan, kvaser and ixxat only.
            A file that replaces a module's `can` attribute (a test faking a tool's python-can)
            must also fake kvaser_bus, which imports the real can (FAKE_CAN_EXEMPT: test_gi_vbus,
            virtual only). test_e4_rawpost.py did not, and opened the bench's real Kvaser during
            this topic's dev run (2026-11-12).

  py -3.14 -B gen-inhibit/tools/bench/test_kvaser_bus.py              # the checks
  py -3.14 -B gen-inhibit/tools/bench/test_kvaser_bus.py --can-fail   # control, then mutants

--can-fail runs the checks on the unmutated kvaser_bus.py first (the control), then on each
mutant: a copy in the worktree's scratch/, one edit applied, beside copies of can_tools_pin.py
and its json, run through KVASER_BUS_DIR in a fresh interpreter. A mutant is killed by a FAIL
line; a crash is not a kill. The pin check needs can_tools_pin.py install in this worktree.
Gen-inhibit tester, 2026-11-12.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import types
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
GI = os.path.dirname(TOOLS)
SRC = os.environ.get("KVASER_BUS_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths      # noqa: E402

FAILS = []

# Files that may open a bus in a form the scan flags: (calls flagged, why). Paths are relative
# to gen-inhibit/, with forward slashes.
ALLOWED = {
    "tools/bench/kvaser_bus.py": (3, "the opener itself"),
    "tools/vehicle_runner.py": (3, "the implementor's file (gen-inhibit README), not the bench harness. Its "
                                   "--interface auto imports canbench.live.receiver, which applies the "
                                   "fixes on import; an explicit --interface kvaser does not (told the "
                                   "reviewer, 2026-11-12)"),
    "tools/test_gi_vbus.py": (1, "gi_vbus's udp_multicast bus, virtual only"),
    "tools/bench/test_wrong_bitrate_sequence.py": (1, "its fake kvaser_bus opens the test's fake can.Bus"),
}
# Files the close rule (gs_usb_bus calls <= close_gs_usb calls) does not apply to, and why.
CLOSE_EXEMPT = {
    "tools/bench/kvaser_bus.py": "defines both; its bus() hands the bus to its caller",
    "tools/bench/test_kvaser_bus.py": "its unit cases open fake buses, some never closed",
}
# A file that replaces a module's `can` attribute (a test faking a tool's python-can) must also
# fake kvaser_bus: it imports the real can, so the tool's Kvaser would open the real adapter.
# That happened once, in test_e4_rawpost.py, during this topic's dev run (2026-11-12). The
# file must hold the string "kvaser_bus" as a literal (sys.modules["kvaser_bus"], a ModuleType
# name), not only in a comment. Exempt, with why:
FAKE_CAN_EXEMPT = {
    "tools/test_gi_vbus.py": "gi_vbus opens udp_multicast only, never the Kvaser or the Innomaker",
}
BUS_NAMES = {"Bus", "ThreadSafeBus", "KvaserBus", "GsUsbBus"}
OPENERS = ("kvaser", "gs_usb")


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- unit, against fakes

class Fakes:
    """A fake can, a fake pin tool and a fake pinned canbench, all logging to one list."""

    LEAF = "Kvaser Leaf Light v2, S/N 1 (#1)"

    def __init__(self, use_raises=False, apply_raises=False, shutdown_raises=False, holder="ours",
                 kv_info=LEAF):
        """holder: the bench log's holder _lock_holder gives: "ours" (tester (gen-inhibit) with
        this process's pid), None (free), "raise" (unreadable), or a dict. kv_info: the opened
        bus's channel_info ("absent": no such attribute)."""
        self.holder = {"who": "tester (gen-inhibit)", "pid": os.getpid(), "run": None} if holder == "ours" \
            else holder
        self.log = []
        self.use_raises, self.apply_raises, self.shutdown_raises = use_raises, apply_raises, shutdown_raises
        log = self.log
        fakes = self

        class Bus:
            def __init__(self, **kw):
                self.kw = kw
                if kv_info != "absent":
                    self.channel_info = kv_info
                log.append(("Bus", kw))

            def shutdown(self):
                log.append(("shutdown", self))
                if fakes.shutdown_raises:
                    raise OSError("shutdown failed")

        can = types.ModuleType("can")
        can.Bus = Bus
        self.can = can

        lf = types.ModuleType("canbench.live.linux_fixes")

        def apply():
            log.append(("apply",))
            if fakes.apply_raises:
                raise RuntimeError("fixes failed")
            return {"kvaser": True, "gs_usb": True}

        lf.apply = apply
        rc = types.ModuleType("canbench.live.receiver")
        rc.release_gs_usb = lambda b: log.append(("release", b))
        self.pinned = {"canbench": types.ModuleType("canbench"),
                       "canbench.live": types.ModuleType("canbench.live"),
                       "canbench.live.linux_fixes": lf, "canbench.live.receiver": rc}
        self.saved = {}

    def pin_tool(self):
        fakes = self

        class Pin:
            @staticmethod
            def use():
                fakes.log.append(("use",))
                if fakes.use_raises:
                    raise RuntimeError("pin refused")
                sys.modules.update(fakes.pinned)
                return fakes.pinned["canbench"]
        return Pin

    def __enter__(self):
        for k in ["can"] + list(self.pinned):
            self.saved[k] = sys.modules.get(k)
            sys.modules.pop(k, None)
        sys.modules["can"] = self.can
        spec = importlib.util.spec_from_file_location("kvaser_bus_under_test", os.path.join(SRC, "kvaser_bus.py"))
        kb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb)
        kb._pin_tool = self.pin_tool
        fakes = self

        def lock_holder():
            fakes.log.append(("lock",))
            if fakes.holder == "raise":
                raise kb.BenchNotHeld("the bench log could not be read: planted")
            return fakes.holder
        kb._lock_holder = lock_holder
        return kb

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        return False

    def kinds(self):
        return [e[0] for e in self.log]


def case(name, fn):
    """A case that raises where it should not is a FAIL, not a crash."""
    try:
        fn()
    except Exception as e:      # noqa: BLE001
        check(name, False, "raised %s: %s" % (type(e).__name__, e))


def unit():
    def kvaser_open():
        f = Fakes()
        with f as kb:
            b = kb.kvaser_bus(channel=0, bitrate=500000, driver_mode=True)
        check("kvaser_bus: the lock checked first, then the pin loaded and the fixes applied, then the open",
              f.kinds() == ["lock", "use", "apply", "Bus"], f.log)
        check("kvaser_bus: opens interface kvaser with the caller's arguments",
              f.log[-1][1] == {"interface": "kvaser", "channel": 0, "bitrate": 500000, "driver_mode": True}
              and b.kw is f.log[-1][1], f.log[-1])
    case("kvaser_bus: opens", kvaser_open)

    def virtual():
        # madhouse 2026-11-13 01:06: the Leaf wedged, CANlib listed only its virtual channels
        for label, info in (("Kvaser Virtual CAN (2026-11-13)", "Kvaser Virtual CAN, S/N 1 (#1)"),
                            ("a lower-case virtual name", "kvaser virtual can"),
                            ("no channel_info", "absent"), ("a channel_info that is not text", None)):
            f = Fakes(kv_info=info)
            raised = None
            with f as kb:
                try:
                    kb.kvaser_bus(channel=0, bitrate=500000)
                except Exception as e:      # noqa: BLE001
                    raised = e
                vc = kb.VirtualChannel
            check("kvaser_bus: %s -> VirtualChannel, and the bus it opened is shut" % label,
                  isinstance(raised, vc) and f.kinds()[-2:] == ["Bus", "shutdown"], (raised, f.log))
        f = Fakes()
        with f as kb:
            kb.kvaser_bus(channel=0, bitrate=500000)
        check("kvaser_bus: the Leaf's channel_info opens, nothing shut", "shutdown" not in f.kinds(), f.log)
    case("kvaser_bus: a virtual channel refused", virtual)

    def once():
        f = Fakes()
        with f as kb:
            kb.kvaser_bus(channel=0, bitrate=500000)
            kb.kvaser_bus(channel=0, bitrate=250000)
            kb.gs_usb_bus(channel="can0", index=0, bitrate=500000)
        check("fixes: applied once a process, over three opens", f.kinds().count("apply") == 1
              and f.kinds().count("Bus") == 3, f.log)
    case("fixes: once", once)

    def refused(kind, opener):
        def go():
            f = Fakes(**{kind: True})
            raised = None
            with f as kb:
                try:
                    getattr(kb, opener)(channel=0, bitrate=500000)
                except Exception as e:      # noqa: BLE001
                    raised = e
            check("%s: %s -> raises, and nothing is opened" % (opener, kind.replace("_raises", " refuses")),
                  raised is not None and "Bus" not in f.kinds(), (raised, f.log))
        return go
    for opener in ("kvaser_bus", "gs_usb_bus"):
        for kind in ("use_raises", "apply_raises"):
            case("%s %s" % (opener, kind), refused(kind, opener))

    def gs_open():
        f = Fakes()
        with f as kb:
            kb.gs_usb_bus(channel="can0", index=0, bitrate=500000)
        check("gs_usb_bus: the lock checked first, then the pin loaded and the fixes applied, then the open",
              f.kinds() == ["lock", "use", "apply", "Bus"], f.log)
        check("gs_usb_bus: opens interface gs_usb with the caller's arguments",
              f.log[-1][1] == {"interface": "gs_usb", "channel": "can0", "index": 0, "bitrate": 500000}, f.log[-1])
    case("gs_usb_bus: opens", gs_open)

    def close():
        f = Fakes()
        with f as kb:
            b = kb.gs_usb_bus(channel="can0", index=0, bitrate=500000)
            del f.log[:]
            kb.close_gs_usb(b)
        rel = [e for e in f.log if e[0] == "release"]
        check("close_gs_usb: shuts the bus down, then releases its handle through the pinned receiver",
              [k for k in f.kinds() if k != "use"] == ["shutdown", "release"] and rel and rel[0][1] is b, f.log)
    case("close_gs_usb: closes", close)

    def close_raises():
        f = Fakes(shutdown_raises=True)
        raised = None
        with f as kb:
            b = kb.gs_usb_bus(channel="can0", index=0, bitrate=500000)
            del f.log[:]
            try:
                kb.close_gs_usb(b)
            except OSError as e:
                raised = e
        rel = [e for e in f.log if e[0] == "release"]
        check("close_gs_usb: a shutdown that raises still releases the handle, and the error is not swallowed",
              raised is not None and rel and rel[0][1] is b, (raised, f.log))
    case("close_gs_usb: shutdown raises", close_raises)

    def router():
        for iface, kw in (("kvaser", {"channel": 0, "bitrate": 500000}),
                          ("gs_usb", {"channel": "can0", "index": 0, "bitrate": 500000})):
            f = Fakes()
            cfg = dict(kw, interface=iface)
            with f as kb:
                kb.bus(**cfg)
            check("bus(interface=%r): through its opener, the lock checked and the fixes applied first" % iface,
                  f.kinds() == ["lock", "use", "apply", "Bus"] and f.log[-1][1] == dict(kw, interface=iface), f.log)
        f = Fakes()
        cfg = {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 500000, "auto_reset": True}
        with f as kb:
            kb.bus(**cfg)
        check("bus(interface='pcan'): straight to can.Bus with every argument",
              f.log and f.log[-1] == ("Bus", cfg) and cfg["interface"] == "pcan", f.log)
    case("bus: routes", router)

    # the bench guard (reviewer, 2026-11-12): no real open without the lock
    me, parent = os.getpid(), os.getppid()
    for label, holder in (
            ("the lock free", None),
            ("the lock unreadable", "raise"),
            ("the lock held by the other tester", {"who": "tester (charge-interposer)", "pid": me, "run": None}),
            ("the lock held by can-tools by hand", {"who": "can-tools", "pid": me, "run": None}),
            ("the lock held by tester (gen-inhibit) under a pid that is no ancestor",
             {"who": "tester (gen-inhibit)", "pid": 2 ** 22 + 7, "run": None})):
        for opener, kw in (("kvaser_bus", {"channel": 0, "bitrate": 500000}),
                           ("gs_usb_bus", {"channel": "can0", "index": 0, "bitrate": 500000})):
            def go(label=label, holder=holder, opener=opener, kw=kw):
                f = Fakes(holder=holder)
                raised = None
                with f as kb:
                    try:
                        getattr(kb, opener)(**kw)
                    except kb.BenchNotHeld as e:
                        raised = e
                check("guard: %s, %s refuses (BenchNotHeld) before the pin, the fixes or an open" % (label, opener),
                      raised is not None and f.kinds() == ["lock"], (raised, f.log))
            case("guard %s %s" % (label, opener), go)

    def guard_bus():
        f = Fakes(holder=None)
        raised = None
        with f as kb:
            try:
                kb.bus(interface="kvaser", channel=0, bitrate=500000)
            except kb.BenchNotHeld as e:
                raised = e
        check("guard: bus(interface='kvaser') with the lock free refuses", raised is not None
              and "Bus" not in f.kinds(), (raised, f.log))
    case("guard bus", guard_bus)

    for label, holder in (("a runner's own pid (this process)", {"who": "tester (gen-inhibit)", "pid": me, "run": "r1"}),
                          ("a hand take's pid (an ancestor)", {"who": "tester (gen-inhibit)", "pid": parent, "run": None}),
                          ("the implementor on a loan", {"who": "implementor (gen-inhibit)", "pid": parent, "run": None})):
        def ok(label=label, holder=holder):
            f = Fakes(holder=holder)
            with f as kb:
                kb.kvaser_bus(channel=0, bitrate=500000)
            check("guard: held under %s, kvaser_bus opens" % label, f.kinds() == ["lock", "use", "apply", "Bus"], f.log)
        case("guard ok %s" % label, ok)

    def ancestors():
        spec = importlib.util.spec_from_file_location("kvaser_bus_anc", os.path.join(SRC, "kvaser_bus.py"))
        kb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb)
        anc = kb._ancestors()
        check("guard: _ancestors() is this process, then its parent, up to the first process",
              anc[:2] == [me, parent] and len(anc) >= 3 and len(set(anc)) == len(anc), anc)
    case("guard ancestors", ancestors)


# ---------------------------------------------------------------- the real pin, in a child

CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import kvaser_bus as K
out = {"kvaser_bus": os.path.abspath(K.__file__)}
m = K.linux_fixes()
out["linux_fixes"] = os.path.abspath(m.__file__)
out["fixes"] = K.fixes()
out["canbench"] = os.path.abspath(sys.modules["canbench"].__file__)
pin = K._pin_tool()
out["folder"] = os.path.abspath(pin.folder())
out["linux"] = sys.platform.startswith("linux")
try:
    from can.interfaces.kvaser import canlib as kv
    out["kvaser_fixed"] = bool(getattr(kv.canIoCtlInit, "canbench_linux_fix", False))
except Exception as e:
    out["kvaser_fixed"] = "not importable: %s" % type(e).__name__
try:
    from gs_usb import gs_usb as g
    out["gs_usb_fixed"] = bool(getattr(g.GsUsb.start, "canbench_linux_fix", False))
except Exception as e:
    out["gs_usb_fixed"] = "not importable: %s" % type(e).__name__
print("CHILD " + json.dumps(out))
"""


def pin():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-B", "-c", CHILD, SRC], env=env, capture_output=True, text=True,
                       timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("CHILD ")]
    if r.returncode != 0 or not line:
        check("pin: the child loaded the pinned fixes", False, (r.returncode, r.stdout[-300:], r.stderr[-600:]))
        return
    out = json.loads(line[0][6:])
    folder = out["folder"] + os.sep
    print("      pin folder %s; fixes %s; kvaser %s; gs_usb %s" % (
        out["folder"], out["fixes"], out["kvaser_fixed"], out["gs_usb_fixed"]))
    check("pin: linux_fixes and canbench come from the pinned folder",
          out["linux_fixes"].startswith(folder) and out["canbench"].startswith(folder), out)
    check("pin: fixes() gives the kvaser and gs_usb results",
          isinstance(out["fixes"], dict) and sorted(out["fixes"]) == ["gs_usb", "kvaser"], out["fixes"])
    if out["linux"]:
        for k in ("kvaser", "gs_usb"):
            v = out[k + "_fixed"]
            if isinstance(v, str):
                print("      %s: %s, so its fix is not checked here" % (k, v))
            else:
                check("pin: on Linux the real %s fix is installed" % k, v is True and out["fixes"][k] is True, out)


# The real guard, in a child, as a host test that fakes a tool's `can` the old way would meet it:
# the real kvaser_bus, the real python-can and the real _lock_holder. Tripwires replace the
# real python-can's Bus entry points and CANlib's canOpenChannel first, so a guard that let the
# call through records a tripwire and still opens nothing (its mutants run this too). With no
# vtrux_bench importable (PYTHONPATH unset), or the lock free or held elsewhere, every open
# must raise BenchNotHeld.
CHILD_GUARD = r"""
import json, os, sys, types
sys.path.insert(0, sys.argv[1])            # kvaser_bus under test
sys.path.insert(1, sys.argv[2])            # the bench folder, for gi_paths
import can
import can.interface
trips = []
def trip(name):
    def f(*a, **kw):
        trips.append(name)
        raise RuntimeError("TRIPWIRE: %s reached" % name)
    return f
can.Bus = trip("can.Bus")
can.interface.Bus = trip("can.interface.Bus")
try:
    from can.interfaces.kvaser import canlib as kv
    kv.canOpenChannel = trip("canOpenChannel")
except Exception:
    pass
import kvaser_bus as K
out = {"refused": {}, "trips": trips}
# a tool module with its own fake can, as test_e4_rawpost faked e4_device_calibration
tool = types.ModuleType("a_tool")
tool.can = types.SimpleNamespace(Bus=lambda **kw: None)
for name, call in (("kvaser_bus", lambda: K.kvaser_bus(channel=0, bitrate=500000)),
                   ("gs_usb_bus", lambda: K.gs_usb_bus(channel="can0", index=0, bitrate=500000)),
                   ("bus kvaser", lambda: K.bus(interface="kvaser", channel=0, bitrate=500000))):
    try:
        call()
        out["refused"][name] = None
    except K.BenchNotHeld as e:
        out["refused"][name] = "BenchNotHeld: %s" % e
    except Exception as e:
        out["refused"][name] = "OTHER %s: %s" % (type(e).__name__, e)
print("CHILD " + json.dumps(out))
"""


def guard_real():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-B", "-c", CHILD_GUARD, SRC, HERE], env=env, capture_output=True,
                       text=True, timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("CHILD ")]
    if r.returncode != 0 or not line:
        check("guard (real): the child ran", False, (r.returncode, r.stdout[-300:], r.stderr[-600:]))
        return
    out = json.loads(line[0][6:])
    for name, why in sorted(out["refused"].items()):
        check("guard (real): a host test's %s, the real can and no lock held, refuses with BenchNotHeld" % name,
              isinstance(why, str) and why.startswith("BenchNotHeld"), why)
    check("guard (real): no tripwire on the real python-can or CANlib was reached", out["trips"] == [],
          out["trips"])
    print("      guard (real): %s" % (out["refused"].get("kvaser_bus") or "")[:160])


# ---------------------------------------------------------------- the scan

def flagged(tree):
    """[(line, why)] for each call that opens a bus in a form only kvaser_bus.py may use."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        if name not in BUS_NAMES:
            continue
        iface = [k for k in n.keywords if k.arg == "interface"]
        why = None
        if name in ("KvaserBus", "GsUsbBus"):
            why = "constructs %s directly" % name
        elif any(k.arg is None for k in n.keywords):
            why = "passes **kwargs"
        elif len(n.args) >= 2:
            why = "passes the interface by position"
        elif not iface:
            why = "passes no interface= (python-can takes it from its config)"
        elif not (isinstance(iface[0].value, ast.Constant) and isinstance(iface[0].value.value, str)):
            why = "names its interface at run time"
        elif iface[0].value.value in OPENERS:
            why = "opens interface %r directly" % iface[0].value.value
        if why:
            out.append((n.lineno, why))
    return out


def called(tree, name):
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Name) and n.func.id == name)
                    or (isinstance(n.func, ast.Attribute) and n.func.attr == name)))


def fakes_can_without_kvaser_bus(tree):
    """[line] of each replacement of a module's `can` attribute, when the file has no literal
    "kvaser_bus"; [] otherwise."""
    lines = []
    for n in ast.walk(tree):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(
            n, (ast.AugAssign, ast.AnnAssign)) else []
        flat = []
        for t in targets:
            flat += t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]
        lines += [n.lineno for t in flat if isinstance(t, ast.Attribute) and t.attr == "can"
                  and not (isinstance(t.value, ast.Name) and t.value.id == "self")]
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr"
                and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value == "can"):
            lines.append(n.lineno)
    literal = any(isinstance(n, ast.Constant) and n.value == "kvaser_bus" for n in ast.walk(tree))
    return [] if literal else sorted(lines)


def scan_text(text, path="<snippet>"):
    tree = ast.parse(text, path)
    return flagged(tree), called(tree, "gs_usb_bus"), called(tree, "close_gs_usb")


def files():
    for dp, dn, fn in os.walk(GI):
        rel = os.path.relpath(dp, GI).replace(os.sep, "/")
        dn[:] = sorted(d for d in dn if d not in ("__pycache__", ".git") and not (rel == "." and d == "firmware"))
        for f in sorted(fn):
            if f.endswith(".py"):
                yield os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), GI).replace(os.sep, "/")


def scan():
    n_files = openers = 0
    bad, counts = [], {}
    import warnings
    for path, rel in files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            hits, gs, closes = scan_text(text, path)
            fake_can = fakes_can_without_kvaser_bus(ast.parse(text, path))
        if fake_can and rel not in FAKE_CAN_EXEMPT:
            bad.append("%s replaces a module's can (lines %s) and fakes no kvaser_bus" % (rel, fake_can))
        if rel in FAKE_CAN_EXEMPT and not fake_can:
            bad.append("%s is in FAKE_CAN_EXEMPT but no longer needs it" % rel)
        n_files += 1
        openers += text.count("kvaser_bus(") + text.count("gs_usb_bus(")
        if hits:
            counts[rel] = hits
            if rel not in ALLOWED:
                bad.extend("%s:%d %s" % (rel, ln, why) for ln, why in hits)
        if gs > closes and rel not in CLOSE_EXEMPT:
            bad.append("%s opens with gs_usb_bus %d times and closes with close_gs_usb %d" % (rel, gs, closes))
    check("scan: read the harness (over 100 files, over 30 opens through kvaser_bus)",
          n_files > 100 and openers > 30, (n_files, openers))
    check("scan: no open goes round kvaser_bus outside ALLOWED", not bad, bad)
    for rel, (want, why) in sorted(ALLOWED.items()):
        got = len(counts.get(rel, []))
        check("scan: ALLOWED %s has its %d (%s)" % (rel, want, why[:60]), got == want,
              (got, counts.get(rel)))
    print("      %d files, %d calls of kvaser_bus/gs_usb_bus" % (n_files, openers))


SNIPPETS = [
    ("can.Bus(interface='kvaser')", 'import can\nb = can.Bus(interface="kvaser", channel=0, bitrate=500000)\n'),
    ("can.interface.Bus(interface='gs_usb')", 'b = can.interface.Bus(interface="gs_usb", channel="can0")\n'),
    ("Bus(interface='kvaser'), imported bare", 'from can import Bus\nb = Bus(interface="kvaser", channel=0)\n'),
    ("can.ThreadSafeBus(interface='kvaser')", 'b = can.ThreadSafeBus(interface="kvaser", channel=0)\n'),
    ("can.Bus(**cfg)", 'b = can.Bus(**cfg)\n'),
    ("can.Bus(interface=name)", 'b = can.Bus(interface=name, channel=0)\n'),
    ("can.Bus() with no interface", 'b = can.Bus(channel=0, bitrate=500000)\n'),
    ("can.Bus(0, 'kvaser'), by position", 'b = can.Bus(0, "kvaser")\n'),
    ("KvaserBus(0)", 'from can.interfaces.kvaser import KvaserBus\nb = KvaserBus(0)\n'),
    ("can.interfaces.gs_usb.GsUsbBus(...)", 'b = can.interfaces.gs_usb.GsUsbBus(channel="can0", index=0)\n'),
]
CLEAN = [
    ("can.Bus(interface='pcan')", 'b = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)\n'),
    ("kvaser_bus(...)", 'b = kvaser_bus(channel=0, bitrate=500000)\n'),
    ("gs_usb_bus then close_gs_usb", 'b = gs_usb_bus(channel="can0")\nclose_gs_usb(b)\n'),
]
FAKE_CAN_SNIPPETS = [
    ("a tool's can replaced, no kvaser_bus fake", "E4.can, E4.gi = fake_can, gi\n", True),
    ("setattr(tool, 'can', ...), no kvaser_bus fake", 'setattr(E4, "can", fake_can)\n', True),
    ("kvaser_bus only in a comment", "# kvaser_bus\nE4.can = fake_can\n", True),
    ("a tool's can replaced, kvaser_bus faked", 'E4.can = fake_can\nsys.modules["kvaser_bus"] = kb\n', False),
    ("self.can, an instance's own", "self.can = None\n", False),
]
# Real files with one of this topic's sites reverted: (file, old, new, what the scan must find)
REVERTED = [
    ("interlock_bench.py", "kvaser_bus(", 'can.Bus(interface="kvaser", ', "flag"),
    ("x2_jammer.py", "inno = gs_usb_bus(", 'inno = can.Bus(interface="gs_usb", ', "flag"),
    ("x2_jammer.py", "close_gs_usb(inno)", "inno.shutdown()", "close"),
    ("bench_census.py", "close_gs_usb(bus)", "pass", "close"),
    ("bench_link_check.py", "bus = kvaser_bus_open(**cfg)", "bus = can.Bus(**cfg)", "flag"),
    ("diag_build_page_check.py", "buses[name] = open_bus(interface=name,", "buses[name] = can.Bus(interface=name,",
     "flag"),
]


def scan_can_fail():
    for label, text in SNIPPETS:
        hits, _, _ = scan_text(text)
        check("scan can fail: flags %s" % label, len(hits) == 1, hits)
    for label, text in CLEAN:
        hits, gs, closes = scan_text(text)
        check("scan: passes %s" % label, not hits and gs <= closes, (hits, gs, closes))
    for label, text, want in FAKE_CAN_SNIPPETS:
        got = fakes_can_without_kvaser_bus(ast.parse(text))
        check("scan %s: %s" % ("can fail" if want else "passes", label), bool(got) == want, got)
    text = open(os.path.join(HERE, "test_e4_rawpost.py"), encoding="utf-8").read()
    old = 'fake_kb = types.ModuleType("kvaser_bus")'
    cut = text.replace('"kvaser_bus"', "KB")       # every literal gone: the fake no longer named
    check("scan can fail: test_e4_rawpost.py without its fake kvaser_bus",
          text.count(old) == 1 and bool(fakes_can_without_kvaser_bus(ast.parse(cut))),
          fakes_can_without_kvaser_bus(ast.parse(cut)))
    for name, old, new, want in REVERTED:
        text = open(os.path.join(HERE, name), encoding="utf-8").read()
        n = text.count(old)
        if n < 1:
            check("scan can fail: %s reverted (%s)" % (name, old), False, "edit matched %d times" % n)
            continue
        hits, gs, closes = scan_text(text.replace(old, new, 1), name)
        found = bool(hits) if want == "flag" else gs > closes
        check("scan can fail: %s with %r put back as %r" % (name, old, new), found, (hits, gs, closes))


# ---------------------------------------------------------------- mutants of kvaser_bus.py

MUTANTS = [
    (None, None, None),
    ("kvaser_bus applies no fixes", "def kvaser_bus(**kw):\n    require_bench()\n    fixes()\n",
     "def kvaser_bus(**kw):\n    require_bench()\n"),
    ("gs_usb_bus applies no fixes", "def gs_usb_bus(**kw):\n    require_bench()\n    fixes()\n",
     "def gs_usb_bus(**kw):\n    require_bench()\n"),
    # the bench guard (reviewer, 2026-11-12)
    ("kvaser_bus opens without the guard", "def kvaser_bus(**kw):\n    require_bench()\n", "def kvaser_bus(**kw):\n"),
    ("gs_usb_bus opens without the guard", "def gs_usb_bus(**kw):\n    require_bench()\n", "def gs_usb_bus(**kw):\n"),
    ("the guard checks after the fixes", "def kvaser_bus(**kw):\n    require_bench()\n    fixes()\n",
     "def kvaser_bus(**kw):\n    fixes()\n    require_bench()\n"),
    ("a free lock passes", "    if h is None:\n        raise", "    if h is None:\n        return h\n        raise"),
    ("any holder's role passes", '    if h.get("who") not in ROLES:\n', "    if False:\n"),
    ("any holder's pid passes", '    if h.get("pid") not in anc:\n', "    if False:\n"),
    ("vtrux_bench missing reads as held by us",
     '        raise BenchNotHeld("vtrux_bench is not importable',
     '        return {"who": ROLES[0], "pid": os.getpid()}\n        raise BenchNotHeld("vtrux_bench is not importable'),
    ("_ancestors stops at this process", '        pid = int(stat.rsplit(")", 1)[1].split()[1])\n', "        break\n"),
    ("kvaser_bus applies the fixes after the open",
     '    fixes()\n    import can\n    b = can.Bus(interface="kvaser", **kw)\n',
     '    import can\n    b = can.Bus(interface="kvaser", **kw)\n    fixes()\n'),
    # a virtual channel refused (2026-11-13)
    ("kvaser_bus: a virtual channel opens", '    if not isinstance(info, str) or "virtual" in info.lower():\n',
     "    if not isinstance(info, str):\n"),
    ("kvaser_bus: no channel_info opens", '    if not isinstance(info, str) or "virtual" in info.lower():\n',
     '    if isinstance(info, str) and "virtual" in info.lower():\n'),
    ("kvaser_bus: the name compared case-sensitively", '"virtual" in info.lower():\n', '"Virtual" in info:\n'),
    ("kvaser_bus: the refused bus left open", "        b.shutdown()\n        raise VirtualChannel(",
     "        raise VirtualChannel("),
    ("kvaser_bus: a virtual channel returned", "        raise VirtualChannel(", "        return b\n        raise VirtualChannel("),
    ("the fixes are not taken from the pin", "    _pin_tool().use()\n    return importlib.import_module(",
     "    return importlib.import_module("),
    ("the fixes apply on every open", "    if _fixes is None:\n", "    if True:\n"),
    ("close_gs_usb releases nothing",
     '        importlib.import_module("canbench.live.receiver").release_gs_usb(b)\n', "        pass\n"),
    ("close_gs_usb releases only after a clean shutdown",
     "    try:\n        b.shutdown()\n    finally:\n",
     "    b.shutdown()\n    if True:\n"),
    ("close_gs_usb releases before the shutdown",
     '    try:\n        b.shutdown()\n    finally:\n        _pin_tool().use()\n'
     '        importlib.import_module("canbench.live.receiver").release_gs_usb(b)\n',
     '    _pin_tool().use()\n    importlib.import_module("canbench.live.receiver").release_gs_usb(b)\n'
     '    b.shutdown()\n'),
    ("bus() opens the Kvaser straight", '    if iface == "kvaser":\n        return kvaser_bus(**kw)\n', ""),
    ("bus() opens the Innomaker straight", '    if iface == "gs_usb":\n        return gs_usb_bus(**kw)\n', ""),
    ("bus() drops the caller's arguments", "    return can.Bus(interface=iface, **kw)\n",
     "    return can.Bus(interface=iface)\n"),
    ("the pin tool is not the project's", 'PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin.py")\n',
     'PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin_missing.py")\n'),
]


def can_fail():
    base = gi_paths.scratch("kvaser-bus-mutants")
    src = open(os.path.join(HERE, "kvaser_bus.py"), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    try:
        for n, (name, old, new) in enumerate(MUTANTS):
            if old is not None and src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            gi_paths.remove_tree(d)
            os.makedirs(os.path.join(d, "bench"))
            for f in ("can_tools_pin.py", "can_tools_pin.json"):
                shutil.copy2(os.path.join(TOOLS, f), os.path.join(d, f))
            with open(os.path.join(d, "bench", "kvaser_bus.py"), "w", encoding="utf-8", newline="") as fh:
                fh.write(src if old is None else src.replace(old, new))
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env["KVASER_BUS_DIR"] = os.path.join(d, "bench")
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--no-scan"], env=env,
                               capture_output=True, text=True, timeout=300)
            fails = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            crashed = "Traceback" in r.stderr
            if old is None:
                control_ok = r.returncode == 0 and not fails and not crashed
                print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                    "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
                if not control_ok:
                    print(r.stdout[-1500:], r.stderr[-1500:])
                continue
            if crashed:
                print("ERROR    m%d %s: crashed, not killed by a check\n%s" % (n, name, r.stderr[-600:]))
                errors += 1
            elif fails:
                killed += 1
                print("KILLED   m%d %s  (%s)" % (n, name, fails[0][:110]))
            else:
                print("SURVIVED m%d %s" % (n, name))
    finally:
        gi_paths.remove_tree(base)
    total = len(MUTANTS) - 1
    print("\n%d of %d mutants killed, %d errors, control %s" % (killed, total, errors,
                                                                "passing" if control_ok else "FAILING"))
    return 0 if control_ok and killed == total and not errors else 1


def main(argv):
    if "--can-fail" in argv:
        return can_fail()
    unit()
    pin()
    guard_real()
    if "--no-scan" not in argv:
        scan()
        scan_can_fail()
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
