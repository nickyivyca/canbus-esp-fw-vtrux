#!/usr/bin/env python3
"""Test of adapter_fixes.py: the pinned can-tools' Linux fixes are applied
before every Kvaser and Innomaker (gs_usb) open the harness makes. No
hardware.

    python3.13 -B charge-interposer/adapter_fixes_test.py [--can-fail]     (madhouse)
    py -3.14 -B charge-interposer/adapter_fixes_test.py [--can-fail]       (Windows)

Each case names what it checks:
  1. apply-linux    on Linux, apply() calls linux_fixes.apply() once and
                    returns what it returns
  2. apply-other    off Linux, apply() calls nothing and returns {}
  3. bus-order      bus() applies the fixes BEFORE python-can's Bus(), with
                    the keywords passed through as given; off Linux it is
                    python-can's Bus() alone
  4. pinned-start   Linux only (elsewhere it says so and passes): in a
                    process of its own, apply() with the real pinned
                    canbench (can_tools_pin.use) patches a stand-in gs_usb
                    package's GsUsb.start, so start() on an Innomaker asks
                    for no USB reset, detaches a bound kernel driver, and
                    sends MODE RESET then MODE START; and without apply()
                    the stand-in's own start() does reset (the control)
  5. scan           no harness .py (tests aside) opens a Kvaser or an
                    Innomaker with python-can except through
                    adapter_fixes.bus: no call or dict with interface=
                    "kvaser" / "gs_usb" outside it, and no function naming
                    GsUsbBus without calling adapter_fixes.apply(); at least
                    16 opens go through it; and a planted file with each
                    form is caught, so the scan can fail
  6. bus-py         bus.open_bus's real-adapter path calls
                    adapter_fixes.apply() before canbench's detection
--can-fail runs the cases on the unedited adapter_fixes.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail. The edits include dropping the apply() call.

Charge-interposer tester, 2026-11-11 (the reviewer's ruling: the can-tools
pin at fa6c0aea and linux_fixes applied explicitly, no local copy).
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "adapter_fixes.py")
MIN_BUS_CALLS = 16


def load(path):
    spec = importlib.util.spec_from_file_location("adapter_fixes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeFixes:
    def __init__(self, events):
        self.events = events

    def apply(self):
        self.events.append("apply")
        return {"kvaser": True, "gs_usb": True}


class FakeCan:
    def __init__(self, events):
        self.events = events

    def Bus(self, **kw):
        self.events.append(("bus", kw))
        return "a bus"


def case_apply_linux(M, work):
    ev = []
    got = M.apply("linux", FakeFixes(ev))
    assert got == {"kvaser": True, "gs_usb": True} and ev == ["apply"], (got, ev)


def case_apply_other(M, work):
    for plat in ("win32", "darwin"):
        ev = []
        got = M.apply(plat, FakeFixes(ev))
        assert got == {} and ev == [], (plat, got, ev)


def case_bus_order(M, work):
    ev = []
    b = M.bus("linux", FakeFixes(ev), FakeCan(ev), interface="kvaser", channel=0,
              bitrate=500000)
    want = ["apply", ("bus", {"interface": "kvaser", "channel": 0, "bitrate": 500000})]
    assert b == "a bus" and ev == want, ev
    ev = []
    M.bus("win32", FakeFixes(ev), FakeCan(ev), interface="gs_usb", index=0)
    assert ev == [("bus", {"interface": "gs_usb", "index": 0})], ev


# the child of case 4: stand-in gs_usb and usb packages, then the real
# pinned linux_fixes through adapter_fixes.apply() (or not, the control)
CHILD = r'''
import json, sys, types
here, path, do_apply = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
sys.path.insert(0, here)
calls = []
usb = types.ModuleType("usb"); core = types.ModuleType("usb.core")
class USBError(Exception): pass
core.USBError = USBError; usb.core = core
g = types.ModuleType("gs_usb.gs_usb"); pkg = types.ModuleType("gs_usb"); pkg.gs_usb = g
g.GS_CAN_MODE_NORMAL, g.GS_CAN_MODE_LISTEN_ONLY = 0, 1
g.GS_CAN_MODE_LOOP_BACK, g.GS_CAN_MODE_ONE_SHOT, g.GS_CAN_MODE_HW_TIMESTAMP = 2, 8, 16
g.GS_CAN_MODE_RESET, g.GS_CAN_MODE_START, g._GS_USB_BREQ_MODE = 0, 1, 2
class DeviceMode:
    def __init__(self, mode, flags): self.mode, self.flags = mode, flags
    def pack(self): return ("mode", self.mode, self.flags)
g.DeviceMode = DeviceMode
class Dev:
    def is_kernel_driver_active(self, i): return True
    def detach_kernel_driver(self, i): calls.append("detach")
    def ctrl_transfer(self, *a): calls.append(["ctrl", a[1], list(a[4])])
    def reset(self): calls.append("usb-reset")
class Cap: feature = 0xFF
class GsUsb:
    def __init__(self): self.gs_usb, self.device_capability = Dev(), Cap()
    def start(self, flags=None):
        self.gs_usb.reset(); calls.append("own-start")
g.GsUsb = GsUsb
sys.modules.update({"usb": usb, "usb.core": core, "gs_usb": pkg, "gs_usb.gs_usb": g})
import importlib.util
spec = importlib.util.spec_from_file_location("adapter_fixes", path)
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
got = M.apply() if do_apply else None
GsUsb().start(g.GS_CAN_MODE_LISTEN_ONLY | g.GS_CAN_MODE_HW_TIMESTAMP)
print(json.dumps({"applied": got, "calls": calls}))
'''


def _child(path, do_apply):
    r = subprocess.run([sys.executable, "-B", "-c", CHILD, HERE, path, "1" if do_apply else "0"],
                       capture_output=True, text=True, timeout=120, cwd=HERE)
    if r.returncode:
        raise AssertionError("child rc %d: %s" % (r.returncode, (r.stderr or r.stdout)[-400:]))
    return json.loads(r.stdout.strip().splitlines()[-1])


def case_pinned_start(M, work):
    if not sys.platform.startswith("linux"):
        print("      (pinned-start: Linux only, not run on %s)" % sys.platform)
        return
    ctl = _child(M.__file__, False)
    assert "usb-reset" in ctl["calls"], ("control: the stand-in's own start must reset", ctl)
    got = _child(M.__file__, True)
    assert got["applied"] and got["applied"].get("gs_usb") is True, got
    c = got["calls"]
    assert "usb-reset" not in c and "own-start" not in c, c
    assert c[0] == "detach" and [x[2][1] for x in c[1:]] == [0, 1], c
    assert c[2][2][2] == 1 | 16, ("START flags as asked, masked to the device", c)


def _is_bus_call(func):
    return (isinstance(func, ast.Attribute) and func.attr == "bus"
            and isinstance(func.value, ast.Name) and func.value.id == "adapter_fixes")


def _is_apply_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "apply" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "adapter_fixes")


def scan(paths, fixed):
    """-> (hits ["path:line what"], adapter_fixes.bus calls seen)."""
    hits, n = [], 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if _is_bus_call(node.func):
                    n += 1
                    continue
                for kw in node.keywords:
                    if (kw.arg == "interface" and isinstance(kw.value, ast.Constant)
                            and kw.value.value in fixed):
                        hits.append("%s:%d interface=%r not through adapter_fixes.bus"
                                    % (p, node.lineno, kw.value.value))
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "interface"
                            and isinstance(v, ast.Constant) and v.value in fixed):
                        hits.append("%s:%d {'interface': %r}" % (p, node.lineno, v.value))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
                names |= {x.attr for x in ast.walk(node) if isinstance(x, ast.Attribute)}
                names |= {a.name for x in ast.walk(node) if isinstance(x, ast.ImportFrom)
                          for a in x.names}
                if "GsUsbBus" in names and not any(_is_apply_call(x) for x in ast.walk(node)):
                    hits.append("%s:%d %s() names GsUsbBus with no adapter_fixes.apply()"
                                % (p, node.lineno, node.name))
    return hits, n


def harness_files():
    r = subprocess.run(["git", "-C", HERE, "ls-files", "--", "."], capture_output=True,
                       text=True, check=True)
    out = []
    for rel in r.stdout.splitlines():
        base = os.path.basename(rel)
        if not rel.endswith(".py") or rel.startswith("firmware/"):
            continue
        if base == "adapter_fixes.py" or base.endswith("_test.py") or base.startswith("test_"):
            continue
        out.append(os.path.join(HERE, rel))
    return out


def case_scan(M, work):
    files = harness_files()
    assert len(files) > 20, len(files)
    hits, n = scan(files, M.FIXED)
    assert not hits, hits
    assert n >= MIN_BUS_CALLS, "only %d adapter_fixes.bus calls found" % n
    planted = os.path.join(work, "planted.py")
    with open(planted, "w", encoding="utf-8") as f:
        f.write("import can\n"
                "a = can.Bus(interface='kvaser', channel=0)\n"
                "b = can.Bus(interface='gs_usb', index=0)\n"
                "kw = {'interface': 'kvaser'}\n"
                "def opener():\n"
                "    from can.interfaces.gs_usb import GsUsbBus\n"
                "    return GsUsbBus(index=0)\n"
                "def fine():\n"
                "    import adapter_fixes\n"
                "    adapter_fixes.apply()\n"
                "    from can.interfaces.gs_usb import GsUsbBus\n"
                "    return GsUsbBus(index=0)\n"
                "c = can.Bus(interface='pcan')\n")
    hits, _n = scan([planted], M.FIXED)
    lines = sorted(int(h.split(":")[1].split()[0]) for h in hits)
    assert lines == [2, 3, 4, 5], hits


def case_bus_py(M, work):
    with open(os.path.join(HERE, "bus.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "open_bus"][0]
    applied = [x.lineno for x in ast.walk(fn) if _is_apply_call(x)]
    detect = [x.lineno for x in ast.walk(fn) if isinstance(x, ast.ImportFrom)
              and any(a.name == "detect_all_can_interfaces" for a in x.names)]
    assert applied and detect and min(applied) < min(detect), (applied, detect)


CASES = [case_apply_linux, case_apply_other, case_bus_order, case_pinned_start, case_scan,
         case_bus_py]

MUTANTS = [
    ("bus() never applies the fixes", "    apply(platform, fixes)\n    if can_mod", "    if can_mod"),
    ("apply() never calls linux_fixes", "    return fixes.apply()", "    return {}"),
    ("the Linux test inverted", 'return (platform or sys.platform).startswith("linux")',
     'return not (platform or sys.platform).startswith("linux")'),
    ("bus() opens before it applies",
     "    apply(platform, fixes)\n    if can_mod is None:\n        import can as can_mod\n"
     "    return can_mod.Bus(**kw)",
     "    if can_mod is None:\n        import can as can_mod\n    b = can_mod.Bus(**kw)\n"
     "    apply(platform, fixes)\n    return b"),
    ("the Kvaser left out of FIXED", 'FIXED = ("gs_usb", "kvaser")', 'FIXED = ("gs_usb",)'),
    ("bus() drops the keywords", "    return can_mod.Bus(**kw)", "    return can_mod.Bus()"),
]


def run_cases(path, base):
    M = load(path)
    bad = []
    for c in CASES:
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("adapter-fixes-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nadapter_fixes_test: %s" % ("all checks OK" if not bad
                                               else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited adapter_fixes.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        with open(MODULE, encoding="utf-8") as f:
            src = f.read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "adapter_fixes.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
