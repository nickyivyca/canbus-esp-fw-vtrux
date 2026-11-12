#!/usr/bin/env python3
"""Test kvaser_bus.py, the one way the bench harness opens the Kvaser and the Innomaker, and
scan gen-inhibit's tools for an open that goes round it (gen-inhibit reviewer, 2026-11-11).

  unit      against a fake can and a fake pinned canbench: kvaser_bus() and gs_usb_bus() load
            the pin (can_tools_pin.use()) and apply canbench.live.linux_fixes before the open,
            and open nothing when the pin or the fixes refuse; the fixes apply once a process;
            close_gs_usb() shuts the bus down, then releases its pyusb handle through canbench's
            release_gs_usb, also when the shutdown raises; bus() sends kvaser and gs_usb through
            the two openers, and anything else straight to can.Bus with its arguments
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

    def __init__(self, use_raises=False, apply_raises=False, shutdown_raises=False):
        self.log = []
        self.use_raises, self.apply_raises, self.shutdown_raises = use_raises, apply_raises, shutdown_raises
        log = self.log
        fakes = self

        class Bus:
            def __init__(self, **kw):
                self.kw = kw
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
        check("kvaser_bus: the pin is loaded and the fixes applied before the open",
              f.kinds() == ["use", "apply", "Bus"], f.log)
        check("kvaser_bus: opens interface kvaser with the caller's arguments",
              f.log[-1][1] == {"interface": "kvaser", "channel": 0, "bitrate": 500000, "driver_mode": True}
              and b.kw is f.log[-1][1], f.log[-1])
    case("kvaser_bus: opens", kvaser_open)

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
        check("gs_usb_bus: the pin is loaded and the fixes applied before the open",
              f.kinds() == ["use", "apply", "Bus"], f.log)
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
            check("bus(interface=%r): through its opener, the fixes applied first" % iface,
                  f.kinds() == ["use", "apply", "Bus"] and f.log[-1][1] == dict(kw, interface=iface), f.log)
        f = Fakes()
        cfg = {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 500000, "auto_reset": True}
        with f as kb:
            kb.bus(**cfg)
        check("bus(interface='pcan'): straight to can.Bus with every argument",
              f.log and f.log[-1] == ("Bus", cfg) and cfg["interface"] == "pcan", f.log)
    case("bus: routes", router)


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
    ("kvaser_bus applies no fixes", "def kvaser_bus(**kw):\n    fixes()\n", "def kvaser_bus(**kw):\n"),
    ("gs_usb_bus applies no fixes", "def gs_usb_bus(**kw):\n    fixes()\n", "def gs_usb_bus(**kw):\n"),
    ("kvaser_bus applies the fixes after the open",
     '    fixes()\n    import can\n    return can.Bus(interface="kvaser", **kw)\n',
     '    import can\n    b = can.Bus(interface="kvaser", **kw)\n    fixes()\n    return b\n'),
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
    if "--no-scan" not in argv:
        scan()
        scan_can_fail()
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
