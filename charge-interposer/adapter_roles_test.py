#!/usr/bin/env python3
"""Test of adapter_roles.py: every real bench adapter the harness opens is
opened by its canctl role, through the pinned canbench.bench_roles (shared/
bench SPEC section 8's adapter roles, user 2026-11-18). No hardware and no
real canctl: a fake canctl (a script this test writes) prints a made-up
`canctl roles` table, and python-can's Bus is a stand-in while a case runs.

    python3.13 -B charge-interposer/adapter_roles_test.py [--can-fail]

Each case names what it checks:
  1. resolve       with the real pinned canbench (can_tools_pin.use) over
                   the fake canctl: canchg resolves to its own USB bus and
                   address, not canmain's (both gs_usb, libusb); kvaser to
                   the netdev the table names (not "kvaser"); pcanfd to its
                   device id; a name that is not a role (an index, a
                   channel number, a netdev, a serial) is refused before
                   canctl runs; canctl missing is refused, naming it
  2. open          pcanfd opens python-can pcan by canctl's device id with
                   the caller's keywords, in pcan mode; canchg opens
                   python-can gs_usb by bus and address, in libusb mode;
                   the Linux fixes applied first; refused, nothing opened:
                   a channel, device id, index, bus or address from the
                   caller, canchg still on the kernel's gs_usb driver, two
                   PCANs matching pcanfd, pcanfd not attached, kvaser in
                   socketcan mode asked for on CANlib, a bitrate missing
  3. kvaser-netdev kvaser_netdev() is the table's netdev in socketcan mode;
                   linuxcan mode, or socketcan with no netdev, refused
  4. scan          no harness .py (tests aside) opens a python-can Bus but
                   adapter_roles (and kvaser_link, the Kvaser's socketcan
                   on the role's netdev; and the virtual udp_multicast),
                   names python-can's adapter classes, canbench's or
                   python-can's detection, or the removed serial and index
                   pickers, or passes an adapter_roles opener a channel,
                   device id, index, bus or address; a planted file with
                   each form is caught, so the scan can fail
  5. openers       each opener of a real adapter in the harness (OPENERS:
                   file, function, role) asks adapter_roles (or
                   kvaser_link, for role kvaser) for its role, and none
                   names another role; a planted file with an opener on
                   another role, or none, is caught
--can-fail runs the cases on the unedited adapter_roles.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail.

Charge-interposer tester, 2026-11-18 (the charge-interposer reviewer's
adapter-roles assignment; can-tools pinned at 5220d374).
"""

import ast
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "adapter_roles.py")

FIELDS = ("role", "kind", "mode", "attached", "usb", "busnum", "devnum", "driver", "netdev",
          "link", "tty", "chardev", "devid")
# canctl roles as madhouse prints it, its usb paths and numbers made up
BENCH = {
    "canmain": dict(kind="gs_usb", mode="libusb", attached=1, usb="1-3.2.3", busnum=1,
                    devnum=41, driver="none"),
    "canchg": dict(kind="gs_usb", mode="libusb", attached=1, usb="1-3.3", busnum=1, devnum=40,
                   driver="none"),
    "canx2": dict(kind="slcan", mode="-", attached=1, usb="1-3.4", busnum=1, devnum=10,
                  driver="cdc_acm", tty="/dev/ttyACM1"),
    "kvaser": dict(kind="kvaser", mode="socketcan", attached=1, usb="1-3.2.1", busnum=1,
                   devnum=17, driver="kvaser_usb", netdev="kvnet7", link="up"),
    "pcanfd": dict(kind="pcan", mode="-", attached=1, usb="1-3.2.2", busnum=1, devnum=11,
                   driver="pcan", chardev="pcanusbfd32", devid=3),
}

FAKE_CANCTL = r'''#!%s -B
import sys
here = %r
with open(here + "/calls.txt", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] != ["roles"]:
    print("canctl: refused")
    sys.exit(2)
sys.stdout.write(open(here + "/roles.txt").read())
'''


def table(**changes):
    """canctl roles text for BENCH with each role's fields changed as given
    ({role: {field: value}})."""
    lines = []
    for name, f in BENCH.items():
        v = dict(f, **changes.get(name, {}))
        vals = dict(role=name, **v)
        lines.append(" ".join("%s=%s" % (k, vals.get(k, "none")) for k in FIELDS))
    return "\n".join(lines) + "\n"


def load(path):
    spec = importlib.util.spec_from_file_location("adapter_roles", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Canctl:
    """The fake canctl in work, patched into M; set(**changes) writes its
    table."""

    def __init__(self, M, work, **changes):
        self.work = work
        self.path = os.path.join(work, "canctl")
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_CANCTL % (sys.executable, work))
        os.chmod(self.path, 0o755)
        self.set(**changes)
        open(os.path.join(work, "calls.txt"), "w").close()
        M.CANCTL = self.path

    def set(self, **changes):
        with open(os.path.join(self.work, "roles.txt"), "w", encoding="utf-8") as f:
            f.write(table(**changes))

    def calls(self):
        with open(os.path.join(self.work, "calls.txt"), encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]


def br():
    import can_tools_pin
    can_tools_pin.use()
    from canbench import bench_roles
    return bench_roles


def _raises(fn, *a, **kw):
    E = br().RoleError
    try:
        got = fn(*a, **kw)
    except E as e:
        return str(e)
    raise AssertionError("RoleError not raised (got %r)" % (got,))


def case_resolve(M, work):
    c = Canctl(M, work)
    r = M.resolve("canchg")
    assert r.mode == "libusb" and r.interface == "gs_usb", (r.mode, r.interface)
    assert r.kwargs == {"channel": "gs_usb:canchg", "bus": 1, "address": 40}, r.kwargs
    r = M.resolve("canmain")
    assert r.kwargs["address"] == 41, r.kwargs
    r = M.resolve("kvaser")
    assert r.netdev == "kvnet7" and r.kwargs == {"channel": "kvnet7"}, (r.netdev, r.kwargs)
    r = M.resolve("pcanfd")
    assert r.interface == "pcan" and r.kwargs == {"device_id": 3}, r.kwargs
    assert c.calls() == ["roles"] * 4, c.calls()
    for bad in ("0", 0, "PCAN_USBBUS1", "can0", "kvnet7", "00CC00DD5555666677778888",
                "gs_usb", None, "CANCHG"):
        e = _raises(M.resolve, bad)
        assert "not a bench role" in e, e
    assert c.calls() == ["roles"] * 4, "canctl ran for a name that is not a role"
    M.CANCTL = os.path.join(work, "no-canctl")
    e = _raises(M.resolve, "canchg")
    assert "not found" in e, e


class FakeCan:
    """Stands in for python-can's Bus while a case runs."""

    def __init__(self):
        self.opened = []

    def Bus(self, **kw):
        self.opened.append(kw)
        return Opened(kw)


class Opened:
    def __init__(self, kw):
        self.kw, self.shut = kw, 0

    def shutdown(self):
        self.shut += 1


def _with_fake_can(fn):
    import can
    from canbench.live import linux_fixes
    import canbench.live.receiver as rcv
    fc, applied, released = FakeCan(), [], []
    saved = (can.Bus, linux_fixes.apply, rcv.release_gs_usb)
    can.Bus = fc.Bus
    linux_fixes.apply = lambda: applied.append(len(fc.opened)) or {}
    rcv.release_gs_usb = lambda b: released.append(b)
    try:
        fn(fc, applied, released)
    finally:
        can.Bus, linux_fixes.apply, rcv.release_gs_usb = saved


def case_open(M, work):
    br()
    c = Canctl(M, work)

    def body(fc, applied, released):
        b = M.pcanfd(500000, auto_reset=True)
        assert fc.opened == [{"interface": "pcan", "bitrate": 500000, "device_id": 3,
                              "auto_reset": True}], fc.opened
        assert applied == [0], ("the Linux fixes not applied before the open", applied)
        assert b.bench_role.role.role == "pcanfd"
        b = M.canchg(250000)
        assert fc.opened[-1] == {"interface": "gs_usb", "bitrate": 250000,
                                 "channel": "gs_usb:canchg", "bus": 1, "address": 40}, fc.opened
        b.shutdown()
        assert b.shut == 1 and released == [b], ("libusb claim not released", released)
        n = len(fc.opened)
        for fn, kw in ((M.pcanfd, {"channel": "PCAN_USBBUS1"}), (M.pcanfd, {"device_id": 0}),
                       (M.canchg, {"index": 0}), (M.canchg, {"bus": 1}),
                       (M.canchg, {"address": 41}), (M.canchg, {"channel": "can0"})):
            e = _raises(fn, 500000, **kw)
            assert "by its role" in e, e
        c.set(canchg={"driver": "gs_usb"})
        e = _raises(M.canchg, 500000)
        assert "bound to the kernel" in e, e
        c.set(canchg={"mode": "socketcan", "driver": "gs_usb", "netdev": "canchg",
                      "link": "up"})
        e = _raises(M.canchg, 500000)
        assert "mode socketcan" in e, e
        c.set(pcanfd={"attached": 2})
        e = _raises(M.pcanfd, 500000)
        assert "2 USB devices" in e, e
        c.set(pcanfd={"attached": 0})
        e = _raises(M.pcanfd, 500000)
        assert "not attached" in e, e
        c.set()
        e = _raises(M.kvaser_canlib, 500000)
        assert "mode socketcan" in e, e
        e = _raises(M.pcanfd, None)
        assert "bitrate" in e, e
        e = _raises(M.open_role, "kvnet7", bitrate=500000)
        assert "not a bench role" in e, e
        assert len(fc.opened) == n, ("opened on a refusal", fc.opened[n:])
    _with_fake_can(body)

    # the one mode each opener takes, as handed to bench_roles (a stand-in
    # that records it: canbench resolves a PCAN in pcan mode only, so the
    # real table cannot show pcanfd's)
    class Rec:
        RoleError = br().RoleError
        got = []

        @staticmethod
        def canctl_roles_text(canctl):
            return ""

        @classmethod
        def open_role(cls, role, bitrate=None, modes=None, **kw):
            cls.got.append((role, tuple(modes) if modes is not None else None))
    M.pcanfd(500000, mod=Rec)
    M.canchg(500000, mod=Rec)
    M.kvaser_canlib(500000, mod=Rec)
    assert Rec.got == [("pcanfd", ("pcan",)), ("canchg", ("libusb",)),
                       ("kvaser", ("linuxcan",))], Rec.got


def case_kvaser_netdev(M, work):
    c = Canctl(M, work)
    assert M.kvaser_netdev() == "kvnet7"
    c.set(kvaser={"netdev": "none", "link": "none"})
    e = _raises(M.kvaser_netdev)
    assert "no netdev" in e, e
    c.set(kvaser={"mode": "linuxcan", "driver": "kvaser_usb"})
    e = _raises(M.kvaser_netdev)
    assert "linuxcan" in e or "socketcan" in e, e

    # a role table that resolved kvaser in another mode with a netdev
    # still named: refused on the mode alone (a stand-in bench_roles)
    class Other:
        RoleError = br().RoleError

        @staticmethod
        def canctl_roles_text(canctl):
            return ""

        @staticmethod
        def resolve(role, text=None):
            return type("R", (), {"mode": "linuxcan", "netdev": "kvnet7"})()
    e = _raises(M.kvaser_netdev, mod=Other)
    assert "linuxcan" in e, e


# ---- the scan -----------------------------------------------------------------

# python-can's and canbench's adapter classes, detection, and the pickers
# this harness had before 2026-11-18 (adapter_fixes.canchg_target and the
# rest: by serial or index)
FORBIDDEN_NAMES = {"GsUsbBus", "PcanBus", "KvaserBus", "SocketcanBus", "SlcanBus",
                   "IXXATBus", "SocketcanHwBus", "detect_all_can_interfaces",
                   "detect_available_configs", "canchg_target", "find_canchg",
                   "adapter_fixes"}
ROLE_FNS = {"pcanfd", "canchg", "kvaser_canlib", "open_role", "resolve", "kvaser_netdev"}
ROLE_KW = {"channel", "device_id", "index", "bus", "address", "serial"}
VIRTUAL = {"udp_multicast", "virtual"}
# the files that may open python-can's Bus themselves: adapter_roles (none
# itself: canbench does) and kvaser_link (socketcan on the role's netdev)
BUS_OK = {"adapter_roles.py", "kvaser_link.py"}


def _kw(node, name):
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def scan(paths):
    """-> (hits ["path:line what"], adapter_roles opener calls seen)."""
    hits, n = [], 0
    for p in paths:
        base = os.path.basename(p)
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        # a class named only to make an unopened instance, object.__new__(X)
        # (bench_vport's self-test drives python-can's own PCAN reader on a
        # fake), opens nothing
        unopened = {id(a) for x in ast.walk(tree) if isinstance(x, ast.Call)
                    and isinstance(x.func, ast.Attribute) and x.func.attr == "__new__"
                    and isinstance(x.func.value, ast.Name) and x.func.value.id == "object"
                    for a in x.args}
        for node in ast.walk(tree):
            if id(node) in unopened:
                continue
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                hits.append("%s:%d names %s" % (p, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                hits.append("%s:%d names %s" % (p, node.lineno, node.attr))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [node.module or ""] \
                    if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                for nm in names:
                    if nm.split(".")[-1] in FORBIDDEN_NAMES or nm in FORBIDDEN_NAMES:
                        hits.append("%s:%d imports %s" % (p, node.lineno, nm))
            elif isinstance(node, ast.Call):
                f = node.func
                attr = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None)
                if attr == "Bus":
                    iface = _kw(node, "interface")
                    virtual = isinstance(iface, ast.Constant) and iface.value in VIRTUAL
                    if not virtual and base not in BUS_OK:
                        hits.append("%s:%d python-can Bus opened outside adapter_roles"
                                    % (p, node.lineno))
                if attr in ROLE_FNS and isinstance(f, ast.Attribute):
                    n += 1
                    bad = [k.arg for k in node.keywords if k.arg in ROLE_KW]
                    if bad:
                        hits.append("%s:%d %s() given %s" % (p, node.lineno, attr,
                                                              ", ".join(bad)))
    return hits, n


def harness_files():
    r = subprocess.run(["git", "-C", HERE, "ls-files", "--", "."], capture_output=True,
                       text=True, check=True)
    out = set()
    for rel in r.stdout.splitlines():
        base = os.path.basename(rel)
        if not rel.endswith(".py") or rel.startswith("firmware/"):
            continue
        if base.endswith("_test.py") or base.startswith("test_"):
            continue
        out.add(os.path.join(HERE, rel))
    out.add(os.path.join(HERE, "adapter_roles.py"))     # before its first commit
    return sorted(p for p in out if os.path.exists(p))


MIN_ROLE_CALLS = 20


def case_scan(M, work):
    files = harness_files()
    assert len(files) > 20, len(files)
    hits, n = scan(files)
    assert not hits, hits
    assert n >= MIN_ROLE_CALLS, "only %d adapter_roles opener calls found" % n
    planted = os.path.join(work, "planted.py")
    with open(planted, "w", encoding="utf-8") as f:
        f.write("import can\n"                                          # 1
                "a = can.Bus(interface='pcan', channel='PCAN_USBBUS1')\n"  # 2
                "b = can.interface.Bus(interface='gs_usb', index=0)\n"   # 3
                "from can.interfaces.gs_usb import GsUsbBus\n"           # 4
                "c = adapter_roles.pcanfd(500000, channel='PCAN_USBBUS1')\n"  # 5
                "d = adapter_roles.canchg(500000, index=0)\n"            # 6
                "from canbench.live.receiver import detect_all_can_interfaces\n"  # 7
                "e = adapter_fixes.canchg_target(0)\n"                    # 8
                "f = can.detect_available_configs(['gs_usb'])\n"          # 9
                "g = can.Bus(interface='udp_multicast', channel='239.0.2.1')\n"  # 10
                "h = adapter_roles.pcanfd(500000, auto_reset=True)\n"     # 11
                "i = adapter_roles.resolve('canchg')\n"                   # 12
                "j = can_mod.Bus(**kw)\n"                                 # 13
                "k = object.__new__(pc.PcanBus)\n"                        # 14
                "m = pc.PcanBus('PCAN_USBBUS1')\n")                       # 15
    hits, n = scan([planted])
    lines = sorted({int(h.split(":")[1].split()[0]) for h in hits})
    assert lines == [2, 3, 4, 5, 6, 7, 8, 9, 13, 15], hits
    assert n == 4, n
    # the files that may open a Bus themselves are named, not any file
    ok = os.path.join(work, "kvaser_link.py")
    with open(ok, "w", encoding="utf-8") as f:
        f.write("b = can_mod.Bus(interface='socketcan', channel=dev)\n")
    assert scan([ok]) == ([], 0), scan([ok])


# ---- the openers --------------------------------------------------------------

# (file under charge-interposer/, function or "<module>", role): every place
# the harness opens or resolves a real adapter (the reviewer's step 2:
# bus.open_bus, the bench drivers, the PCAN witness, the Kvaser netdev,
# gs_usb, canchg's mode checks), with the role it must ask for
OPENERS = [
    ("bus.py", "open_pcan", "pcanfd"),
    ("bus.py", "open_gs_usb", "canchg"),
    ("bus.py", "open_kvaser", "kvaser"),
    ("kvaser_link.py", "netdev", "kvaser"),     # open_bus opens on it: kvaser_link_test
    ("canchg_mode.py", "_resolve", "canchg"),
    ("diag_cadence.py", "start", "pcanfd"),
    ("bench_drivers/bench_usb_host.py", "open_pcan", "pcanfd"),
    ("bench_drivers/bench_usb_host.py", "open_innomaker", "canchg"),
    ("bench_drivers/bench_usb_host.py", "open_kvaser", "kvaser"),
    ("bench_drivers/bench_charger_witness.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_topology.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_topology.py", "<module>", "pcanfd"),
    ("bench_drivers/bench_rate_feasibility.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_rate_feasibility.py", "<module>", "pcanfd"),
    ("bench_drivers/bench_rate_feasibility.py", "open_witness", "kvaser"),
    ("bench_drivers/bench_hold12.py", "proc_identifier", "pcanfd"),
    ("bench_drivers/bench_powerup_round.py", "__init__", "pcanfd"),
    ("bench_drivers/bench_vport.py", "_open_witness_reader", "pcanfd"),
    ("bench_drivers/bench_mcp_reset.py", "open_innomaker", "canchg"),
    ("bench_drivers/keepalive_probe.py", "open_all", "pcanfd"),
    ("bench_drivers/keepalive_probe.py", "open_all", "kvaser"),
    ("bench_drivers/reset_check.py", "<module>", "pcanfd"),
]
# how each role is asked for: the adapter_roles opener of that role, or
# open_role / resolve with it named, or (kvaser) kvaser_link's opener
ASKS = {"pcanfd": {"pcanfd"}, "canchg": {"canchg"},
        "kvaser": {"kvaser_netdev", "kvaser_canlib", "open_bus"}}
ROLE_CONSTS = {"ROLE"}      # a module's ROLE constant names its role (kvaser_link, canchg_mode)


def _scope(tree, name):
    if name == "<module>":
        return [tree]
    return [x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
            and x.name == name]


def asks(tree, scope_name, src_consts=None):
    """-> the set of roles the scope asks for, from its calls."""
    got = set()
    for sc in _scope(tree, scope_name):
        nodes = ast.walk(sc) if scope_name != "<module>" else [
            n for top in sc.body if not isinstance(top, (ast.FunctionDef, ast.ClassDef))
            for n in ast.walk(top)]
        for node in nodes:
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            a = node.func.attr
            for role, fns in ASKS.items():
                if a in fns:
                    got.add(role)
            if a in ("open_role", "resolve") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    got.add(arg.value)
                elif isinstance(arg, ast.Name) and arg.id in ROLE_CONSTS and src_consts:
                    got.add(src_consts.get(arg.id))
    return got


def _consts(tree):
    out = {}
    for top in tree.body:
        if isinstance(top, ast.Assign) and isinstance(top.value, ast.Constant):
            for t in top.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = top.value.value
    return out


def check_openers(openers, root):
    bad = []
    for rel, fn, role in openers:
        p = os.path.join(root, rel)
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        if not _scope(tree, fn):
            bad.append("%s: no %s" % (rel, fn))
            continue
        got = asks(tree, fn, _consts(tree))
        if role not in got:
            bad.append("%s %s asks for %s, not %s" % (rel, fn, sorted(got) or "nothing", role))
    return bad


def case_openers(M, work):
    bad = check_openers(OPENERS, HERE)
    assert not bad, bad
    d = os.path.join(work, "planted")
    os.makedirs(d)
    with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
        f.write("ROLE = 'canchg'\n"
                "def a():\n    return adapter_roles.canchg(500000)\n"
                "def b():\n    return adapter_roles.pcanfd(500000)\n"
                "def c():\n    return roles.resolve(ROLE)\n"
                "def d():\n    return 1\n"
                "e = adapter_roles.open_role('pcanfd', bitrate=1)\n")
    got = check_openers([("x.py", "a", "canchg"), ("x.py", "b", "canchg"),
                         ("x.py", "c", "canchg"), ("x.py", "d", "pcanfd"),
                         ("x.py", "<module>", "pcanfd"), ("x.py", "zz", "pcanfd")], d)
    assert [g.split()[1] for g in got] == ["b", "d", "no"], got


CASES = [case_resolve, case_open, case_kvaser_netdev, case_scan, case_openers]

MUTANTS = [
    ("any name taken as a role", "    if role not in ROLES:", "    if False:"),
    ("canmain left out of the roles",
     'ROLES = ("canmain", "canchg", "canx2", "kvaser", "pcanfd")',
     'ROLES = ("canchg", "canx2", "kvaser", "pcanfd")'),
    ("the role table not from CANCTL", "    return br.canctl_roles_text(CANCTL)",
     "    return br.canctl_roles_text()"),
    ("pcanfd takes a channel",
     '    for k in ("channel", "device_id"):', '    for k in ("device_id",):'),
    ("pcanfd takes a device id",
     '    for k in ("channel", "device_id"):', '    for k in ("channel",):'),
    ("canchg takes an index",
     '    for k in ("index", "bus", "address", "channel"):',
     '    for k in ("bus", "address", "channel"):'),
    ("canchg takes a bus and address",
     '    for k in ("index", "bus", "address", "channel"):',
     '    for k in ("index", "channel"):'),
    ("pcanfd in any mode", 'bitrate=bitrate, modes=("pcan",)', "bitrate=bitrate, modes=None"),
    ("canchg in any mode (socketcan opened too)", 'bitrate=bitrate, modes=("libusb",)',
     "bitrate=bitrate, modes=None"),
    ("kvaser_canlib in any mode", 'bitrate=bitrate, modes=("linuxcan",)',
     "bitrate=bitrate, modes=None"),
    ("kvaser_netdev in any mode", '    if res.mode != "socketcan" or not res.netdev:',
     "    if not res.netdev:"),
    ("pcanfd opens another role", 'return open_role("pcanfd", bitrate=bitrate',
     'return open_role("canmain", bitrate=bitrate'),
    ("canchg opens canmain", 'return open_role("canchg", bitrate=bitrate',
     'return open_role("canmain", bitrate=bitrate'),
    ("the caller's keywords dropped", "                        text=_table(br), **kw)",
     "                        text=_table(br))"),
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
    if not sys.platform.startswith("linux"):
        print("adapter_roles_test: the bench roles are canctl's, on the Linux bench host only; "
              "nothing to test here")
        return 0
    with scratch_dir.run("adapter-roles-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nadapter_roles_test: %s" % ("all checks OK" if not bad
                                               else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited adapter_roles.py", flush=True)
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
            p = os.path.join(d, "adapter_roles.py")
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
