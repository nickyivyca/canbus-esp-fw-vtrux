#!/usr/bin/env python3
"""Test kvaser_bus.py, the one way the bench harness opens the Kvaser and the Innomakers, and
scan gen-inhibit's tools for an open that goes round it (gen-inhibit reviewer, 2026-11-11).

FROM 2026-11-19 (the gen-inhibit reviewer's ruling: the generic parts are canbench's, can-tools ec7cefe)
kvaser_bus opens through canbench.bench_layer, and the unit, pcan, innomaker and socketcan cases run the
REAL pinned bench_layer and bench_roles (loaded by file from the pin folder) against a fake canctl, a fake
`ip` and a fake `canctl roles`: what bench_layer does (the link up and down, the mode hand-over and put-back,
the pick refusals) is canbench's own and tested there; these cases check that kvaser_bus asks it for the
right role, mode, bitrate, restore, restart-ms and stamp check, plugs in the guard, maps canbench's refusals
to its own errors, keeps the Kvaser's link down at its close, reads back the error mask and records every
open and close. The scan is canbench's bench_scan, with gen-inhibit's allow list (SCAN_ALLOWED).

  unit      against a fake can and a fake pinned canbench: kvaser_bus() loads the pin
            (can_tools_pin.use()) and applies canbench.live.linux_fixes before the open, and
            opens nothing when the pin or the fixes refuse; the fixes apply once a process;
            bus() sends kvaser through kvaser_bus(), refuses gs_usb (an Innomaker is asked for by
            its role) and sends anything else straight to can.Bus with its arguments;
            pcan_witness_bus() (2026-11-13) opens the pinned canbench's CanbenchPcanBus with
            the caller's arguments and its bus-off reset off, after the guard and the pin
  pcan      (2026-11-18, the same ruling) against a fake `canctl roles`: pcan_bus() and
            pcan_witness_bus() refuse channel/device_id/interface, then after the guard open by
            pcanfd's device id; no PCAN, two, another kind, no row, no number, no char device, a
            failed or empty canctl roles each refuse with RoleError before an open
  innomaker (2026-11-18, the user's ruling: an adapter by its canctl role, never by index or
            first found; the gen-inhibit reviewer's conditions 1-2) against a made-up sysfs
            (net/<role> with flags and a device link, usb/<usb> with busnum and devnum) and a
            fake canctl: innomaker_bus(role) refuses channel/index/bus/address and a role not
            canmain or canchg, a missing or up netdev and a free lock, before canctl or an open;
            it hands the role to libusb and opens gs_usb on THAT role's USB device by bus and
            address (canmain and canchg each their own); its shutdown frees the pyusb handle and
            hands the role back (canctl mode <role> socketcan --wait), read back down, recorded;
            a failed hand-over, pin, fixes or open hands it back and re-raises; a failed
            hand-back raises InnomakerError (naming both failures after a failed open)
  guard     (reviewer, 2026-11-12) require_bench() runs first: with the lock free, unreadable,
            held by another role, or held under a pid that is neither this process nor an
            ancestor, kvaser_bus(), innomaker_bus(), pcan_bus(), pcan_witness_bus() and bus(interface="kvaser") raise BenchNotHeld
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
  scan      (2026-11-19) canbench's bench_scan, from the pin, over every .py file under gen-inhibit/
            but firmware/ (the upstream WiCAN tree): every hit (bus-open, adapter-class, usb-scan,
            pcan-channel, role-pick) must be in SCAN_ALLOWED, keyed by file, kind and why, with
            its count and reason; a hit outside it, or an entry whose count no longer matches,
            fails. bench_scan's self_check must pass (each kind caught on its snippet), and the
            scan is shown able to fail on real files with one of their role opens reverted.
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
import re
import os
import shutil
import subprocess
import sys
import time
import types
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
GI = os.path.dirname(TOOLS)
SRC = os.environ.get("KVASER_BUS_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths      # noqa: E402

FAILS = []

# A file that replaces a module's `can` attribute (a test faking a tool's python-can) must also
# fake kvaser_bus: it imports the real can, so the tool's Kvaser would open the real adapter.
# That happened once, in test_e4_rawpost.py, during this topic's dev run (2026-11-12). The
# file must hold the string "kvaser_bus" as a literal (sys.modules["kvaser_bus"], a ModuleType
# name), not only in a comment. Exempt, with why:
FAKE_CAN_EXEMPT = {
    "tools/test_gi_vbus.py": "gi_vbus opens udp_multicast only, never the Kvaser or the Innomaker",
}


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- unit, against fakes

# The REAL canbench.bench_roles (can-tools 5220d37, the pinned folder), loaded by file into each
# Fakes' pinned canbench, with only its `canctl roles` replaced by a fake built from per-role
# state that the fake canctl's up / down / mode change (the gen-inhibit reviewer, 2026-11-18:
# canbench's RoleError is the refusal, and each refusal is covered against fakes of bench_roles).
# Made-up USB places, no serials, as the real `canctl roles` prints none.
ROLE_FIELDS = ("role", "kind", "mode", "attached", "usb", "busnum", "devnum", "driver", "netdev", "link", "tty",
               "chardev", "devid")
DEFAULT_ROLES = {
    "canmain": dict(kind="gs_usb", mode="socketcan", attached=1, usb="1-3.2", busnum=3, devnum=7, driver="gs_usb",
                    netdev="canmain", link="down", tty="-", chardev="-", devid="-"),
    "canchg": dict(kind="gs_usb", mode="socketcan", attached=1, usb="1-4", busnum=1, devnum=12, driver="gs_usb",
                   netdev="canchg", link="down", tty="-", chardev="-", devid="-"),
    "canx2": dict(kind="slcan", mode="tty", attached=1, usb="1-5", busnum=1, devnum=13, driver="cdc_acm",
                  netdev="none", link="none", tty="/dev/ttyACM1", chardev="-", devid="-"),
    "kvaser": dict(kind="kvaser", mode="socketcan", attached=1, usb="1-6", busnum=1, devnum=14, driver="kvaser_usb",
                   netdev="kvaser", link="down", tty="-", chardev="-", devid="-"),
    "pcanfd": dict(kind="pcan", mode="-", attached=1, usb="1-7", busnum=1, devnum=15, driver="pcan", netdev="none",
                   link="none", tty="-", chardev="pcanusbfd32", devid="0"),
}


def roles_text(state):
    return "".join(" ".join("%s=%s" % (f, name if f == "role" else state[name][f]) for f in ROLE_FIELDS) + "\n"
                   for name in state)


def real_canbench_path(name="bench_roles"):
    """The pinned canbench's <name>.py (can_tools_pin.py install puts it there)."""
    spec = importlib.util.spec_from_file_location("_tkb_pin", os.path.join(os.path.dirname(SRC), "can_tools_pin.py"))
    pin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pin)
    return os.path.join(pin.folder(), "canbench", name + ".py")


def real_bench_roles_path():
    return real_canbench_path("bench_roles")


def load_real(name, mods):
    """The pinned canbench.<name>, loaded by file with mods (sys.modules entries) in place while it runs."""
    spec = importlib.util.spec_from_file_location("canbench." + name, real_canbench_path(name))
    m = importlib.util.module_from_spec(spec)
    mods = dict(mods, **{"canbench." + name: m})
    before = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)        # its dataclasses and `from canbench import ...` look there
    try:
        spec.loader.exec_module(m)
    finally:
        for k, v in before.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return m


class Fakes:
    """A fake can, a fake pin tool and a pinned canbench (fake but for bench_roles, the real one
    with a fake `canctl roles`), all logging to one list."""

    LEAF = "Kvaser Leaf Light v2, S/N 1 (#1)"

    def __init__(self, use_raises=False, apply_raises=False, shutdown_raises=False, holder="ours",
                 kv_info=LEAF, roles=None, roles_rc=0, roles_raw=None):
        """holder: the bench log's holder _lock_holder gives: "ours" (tester (gen-inhibit) with
        this process's pid), None (free), "raise" (unreadable), or a dict. kv_info: the opened
        bus's channel_info ("absent": no such attribute). roles: {role: {field: value}} changes
        to DEFAULT_ROLES; roles_rc: `canctl roles` exits so (the real one raises RoleError);
        roles_raw: its output verbatim."""
        self.state = {k: dict(v) for k, v in DEFAULT_ROLES.items()}
        for k, v in (roles or {}).items():
            if v is None:
                self.state.pop(k, None)
            else:
                self.state.setdefault(k, {}).update(v)
        self.roles_rc, self.roles_raw = roles_rc, roles_raw
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

            def send(self, msg, timeout=None):  # can-tools 0f9c85f's open_role wraps a libusb gs_usb bus's send
                log.append(("send", msg))

            def recv(self, timeout=None):       # for an echo drain (2026-11-19): nothing queued
                if getattr(self, "shut", False):
                    raise OSError("recv after shutdown")
                script = getattr(self, "script", None)     # a test's reads, in order (2026-11-19)
                if script:
                    item = script.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                time.sleep(max(timeout or 0.0, 0.001))
                return None

            def shutdown(self):
                d = getattr(self, "echo_drain", None)
                self.drain_alive_at_shutdown = bool(d is not None and d.is_alive())
                self.shut = True
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
        pb = types.ModuleType("canbench.live.pcan_bus")

        class CanbenchPcanBus:
            def __init__(self, **kw):
                self.kw = kw
                log.append(("Pcan", kw))
        pb.CanbenchPcanBus = CanbenchPcanBus
        br = load_real("bench_roles", {})

        def canctl_roles_text(canctl=None):
            log.append(("roles",))
            if fakes.roles_rc:
                raise br.RoleError("canctl roles exited %d: planted" % fakes.roles_rc)
            return roles_text(fakes.state) if fakes.roles_raw is None else fakes.roles_raw
        br.canctl_roles_text = canctl_roles_text
        br.CANCTL = "/usr/local/sbin/canctl"
        self.br = br
        # The REAL canbench.bench_layer (can-tools ec7cefe; the gen-inhibit reviewer, 2026-11-19), its
        # subprocess the fake canctl and `ip` (self.run), its sleep and clock fake.
        pkg = types.ModuleType("canbench")
        pkg.__path__ = []
        errs = load_real("errors", {"canbench": pkg})
        pkg.bench_roles, pkg.errors = br, errs
        bl = load_real("bench_layer", {"canbench": pkg, "canbench.bench_roles": br, "canbench.errors": errs})

        def sub_run(argv, **kw):
            rc, out = fakes.run([str(a) for a in argv])
            return types.SimpleNamespace(returncode=127 if rc is None else rc, stdout=out, stderr="")
        bl.subprocess = types.SimpleNamespace(run=sub_run, TimeoutExpired=subprocess.TimeoutExpired,
                                              DEVNULL=subprocess.DEVNULL)
        bl.CANCTL = "/usr/local/sbin/canctl"
        clock = [0.0]

        def fake_sleep(s):
            clock[0] += s

        bl.sleep, bl.monotonic = fake_sleep, lambda: clock[0]
        self.bl = bl
        pkg.bench_layer = bl
        live = types.ModuleType("canbench.live")
        live.linux_fixes, live.receiver, live.pcan_bus = lf, rc, pb
        pkg.live = live
        self.pinned = {"canbench": pkg, "canbench.live": live,
                       "canbench.live.linux_fixes": lf, "canbench.live.receiver": rc,
                       "canbench.live.pcan_bus": pb, "canbench.bench_roles": br, "canbench.errors": errs,
                       "canbench.bench_layer": bl}
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
        kb.PLATFORM = "win32"      # CANlib: the socketcan cases are SockFakes (2026-11-18)
        fakes = self

        def lock_holder():
            fakes.log.append(("lock",))
            if fakes.holder == "raise":
                raise kb.BenchNotHeld("the bench log could not be read: planted")
            return fakes.holder
        kb._lock_holder = lock_holder
        kb._run = self.run
        self.saved_runs = os.environ.get("GI_RUNS_DIR")
        if hasattr(self, "runs_dir"):            # SockFakes, InnoFakes: their own runs dir, or none
            if self.runs_dir:
                os.environ["GI_RUNS_DIR"] = self.runs_dir
            else:
                os.environ.pop("GI_RUNS_DIR", None)
        return kb

    def run(self, argv):
        if argv[:5] == ["ip", "-details", "-json", "link", "show"] and len(argv) == 6:
            # a role's netdev, as the fake roles say (SockFakes plays the Kvaser's own)
            dev = argv[5]
            s = next((v for v in self.state.values() if v.get("netdev") == dev), None)
            self.log.append(("ip", dev))
            if s is None:
                return 1, "Device \"%s\" does not exist." % dev
            up = s.get("link") == "up"
            return 0, json.dumps([{"ifname": dev, "flags": ["NOARP"] + (["UP", "LOWER_UP"] if up else []),
                                   "linkinfo": {"info_kind": "can", "info_data": {
                                       "state": "ERROR-ACTIVE" if up else "STOPPED", "restart_ms": 0,
                                       "bittiming": {"bitrate": 500000}}}}])
        self.log.append(("run?",) + tuple(argv))
        return 127, "not faked"

    def __exit__(self, *exc):
        if self.saved_runs is None:
            os.environ.pop("GI_RUNS_DIR", None)
        else:
            os.environ["GI_RUNS_DIR"] = self.saved_runs
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        return False

    def kinds(self):
        return [e[0] for e in self.log]

    def canctls(self):
        return [e[1:] for e in self.log if e[0] == "canctl"]


class SockFakes(Fakes):
    """The Kvaser on Linux (ruling (a), 2026-11-18): Fakes on PLATFORM linux, with its role in the
    fake `canctl roles` (`kvaser`: changes to role kvaser, e.g. mode linuxcan or no netdev), and a
    fake _run that plays canctl (up/down change the fake link and the role's link; rc from
    canctl_rc) and `ip -details -json link show kvaser` (the fake link, with read_bitrate /
    read_lo / stuck_up to lie). The fake pinned canbench.live.socketcan_hw.SocketcanHwBus
    (can-tools 5952c0c on, opened by bench_roles.open_role from 5220d37) logs ("HwBus", channel,
    kw), raises hw_raises if set, and its socket reads back err_filter as its CAN_RAW_ERR_FILTER;
    stamp_stats() gives `stamps`."""

    def __init__(self, kvaser=None, canctl_rc=0, read_bitrate=None, read_lo=False, stuck_up=False,
                 err_filter=0x1FFFFFFF, runs_dir=None, canctl_applies=False, hw_raises=None,
                 stamps=None, role_link_lags=False, found_up=None, **kw):
        # canctl_applies: canctl changes the link even when it exits non-zero (it failed after
        # its own `ip link set up`)
        roles = dict(kw.pop("roles", None) or {})
        if kvaser is not None:
            roles["kvaser"] = kvaser
        Fakes.__init__(self, roles=roles, **kw)
        self.canctl_applies = canctl_applies
        self.canctl_rc, self.read_bitrate = canctl_rc, read_bitrate
        self.read_lo, self.stuck_up, self.runs_dir = read_lo, stuck_up, runs_dir
        self.role_link_lags = role_link_lags     # canctl roles still says link down after the up
        self.linkst = {"up": False, "bitrate": None}
        if found_up:                # the link found up at that bitrate (restart-ms as kvaser_bus asks)
            self.linkst = {"up": True, "bitrate": found_up}
            self.state["kvaser"]["link"] = "up"
        log, fakes = self.log, self
        self.stamps = stamps if stamps is not None else {"state": "hardware", "reason": "fake",
                                                          "after_check": {"frames": 0}}

        class Sock:
            def getsockopt(self, level, opt):
                log.append(("getsockopt", level, opt))
                return fakes.err_filter
        self.err_filter = err_filter

        class SocketcanHwBus:
            def __init__(self, channel, **kw):
                log.append(("HwBus", channel, kw))
                if hw_raises is not None:
                    raise hw_raises
                self._sock = Sock()

            def stamp_stats(self):
                return dict(fakes.stamps)

            def shutdown(self):
                log.append(("shutdown", self))
                if fakes.shutdown_raises:
                    raise OSError("shutdown failed")
        hw = types.ModuleType("canbench.live.socketcan_hw")
        hw.SocketcanHwBus = SocketcanHwBus
        self.pinned["canbench.live.socketcan_hw"] = hw
        self.pinned["canbench.live"].socketcan_hw = hw

    def setlink(self, st):
        self.linkst = st
        if "kvaser" in self.state and not self.role_link_lags:
            self.state["kvaser"]["link"] = "up" if st["up"] else "down"

    def run(self, argv):
        if argv[:3] == ["sudo", "-n", "/usr/local/sbin/canctl"]:
            args = argv[3:]
            self.log.append(("canctl",) + tuple(args))
            if self.canctl_rc == 0 or self.canctl_applies:
                if args[0] == "up":
                    self.setlink({"up": True, "bitrate": int(args[2])})
                elif args[0] == "down" and not self.stuck_up:
                    self.setlink(dict(self.linkst, up=False))
            return self.canctl_rc, "canctl said %s\n" % args[0]
        if argv == ["ip", "-details", "-json", "link", "show", "kvaser"]:
            self.log.append(("ip",))
            st = self.linkst
            info = {"state": "ERROR-ACTIVE" if st["up"] else "STOPPED", "restart_ms": 100}
            if st["bitrate"] is not None:
                info["bittiming"] = {"bitrate": self.read_bitrate or st["bitrate"]}
            if self.read_lo:
                info["ctrlmode"] = ["LISTEN-ONLY"]
            flags = ["NOARP", "ECHO"] + (["UP", "LOWER_UP"] if st["up"] else [])
            return 0, json.dumps([{"ifname": "kvaser", "flags": flags,
                                   "linkinfo": {"info_kind": "can", "info_data": info}}])
        return Fakes.run(self, argv)

    def __enter__(self):
        kb = Fakes.__enter__(self)
        kb.PLATFORM = "linux"
        kb._open[:] = []
        return kb


class InnoFakes(Fakes):
    """The two Innomakers by canctl role (2026-11-18), as the fake `canctl roles` gives them, and
    a fake _run playing canctl: `mode <role> libusb` puts the role in libusb mode (no netdev,
    unbound), `mode <role> socketcan --wait` puts it back, its link down. libusb_rc / restore_rc
    are canctl's exit codes (restore_applies: the role comes back even when canctl exits
    non-zero); restore_up: it comes back with its link up; restore_gone: it comes back with no
    netdev; libusb_bound: libusb mode leaves it bound to gs_usb (canbench refuses). start_up /
    missing: roles that start with their link up / with no netdev. bus_raises: the gs_usb open
    raises it."""

    def __init__(self, libusb_rc=0, restore_rc=0, restore_applies=False, restore_up=False,
                 restore_gone=False, libusb_bound=False, start_up=(), missing=(), bus_raises=None, runs_dir=None,
                 **kw):
        roles = dict(kw.pop("roles", None) or {})
        for r in start_up:
            roles.setdefault(r, {})["link"] = "up"
        for r in missing:
            roles.setdefault(r, {}).update(netdev="none", link="none")
        Fakes.__init__(self, roles=roles, **kw)
        self.libusb_rc, self.restore_rc, self.restore_applies = libusb_rc, restore_rc, restore_applies
        self.restore_up, self.restore_gone, self.libusb_bound = restore_up, restore_gone, libusb_bound
        self.runs_dir = runs_dir
        if bus_raises is not None:
            base = self.can.Bus

            class RaisingBus(base):
                def __init__(self, **kw):
                    base.__init__(self, **kw)
                    raise bus_raises
            self.can.Bus = RaisingBus

    def netdev(self, role, up=False):
        self.state[role].update(mode="socketcan", driver="gs_usb", netdev=role, link="up" if up else "down")

    def run(self, argv):
        if argv[:3] == ["sudo", "-n", "/usr/local/sbin/canctl"]:
            args = argv[3:]
            self.log.append(("canctl",) + tuple(args))
            role = args[1]
            if args[0] == "mode" and args[2] == "libusb":
                if self.libusb_rc == 0:
                    self.state[role].update(mode="libusb", driver="gs_usb" if self.libusb_bound else "none",
                                            netdev="none", link="none")
                return self.libusb_rc, "canctl said libusb\n"
            if args[0] in ("up", "down"):           # bench_layer takes an up link down first, and puts it back
                self.state[role]["link"] = "up" if args[0] == "up" else "down"
                return 0, "canctl said %s\n" % args[0]
            if args[0] == "mode" and args[2] == "socketcan":
                if self.restore_rc == 0 or self.restore_applies:
                    self.netdev(role, up=self.restore_up)
                    if self.restore_gone:
                        self.state[role].update(netdev="none", link="none")
                return self.restore_rc, "canctl said socketcan\n"
        return Fakes.run(self, argv)

    def down(self, role):
        """The role is in socketcan mode, its netdev there and its link down, as the runner's
        probe `link` reads it."""
        s = self.state.get(role) or {}
        return s.get("mode") == "socketcan" and s.get("netdev") == role and s.get("link") == "down"


INNOS = ("canmain", "canchg")
LIBUSB = ("mode", "canmain", "libusb")
RESTORE = ("mode", "canmain", "socketcan", "--wait", "10")


def read_evs(runs):
    with open(os.path.join(runs, "kvaser_opens.jsonl"), encoding="ascii") as fh:
        return [json.loads(x) for x in fh]


def raises(f, fn, *exc):
    """fn(kb) inside f; the exception it raised (of exc), or None."""
    with f as kb:
        try:
            fn(kb)
        except exc or (Exception,) as e:      # noqa: BLE001
            return e
    return None


def innomaker():
    """The Innomakers by canctl role (the user's ruling, 2026-11-18), through canbench's bench_layer (the
    gen-inhibit reviewer, 2026-11-19): the real bench_layer and bench_roles against a fake canctl."""
    def opens():
        runs = gi_paths.scratch("kvaser-bus-inno-runs")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        try:
            f = InnoFakes(runs_dir=runs)
            with f as kb:
                b = kb.innomaker_bus("canmain", bitrate=250000)
                k = f.kinds()
                check("innomaker: the lock checked before any canctl; canctl mode canmain libusb; then the fixes and "
                      "the open", "lock" in k and k.index("lock") < k.index("canctl") < k.index("apply") < k.index("Bus")
                      and f.canctls() == [LIBUSB], f.log)
                check("innomaker: gs_usb on canmain's own USB device, by its bus and address, never by index",
                      f.log[-1][1] == {"interface": "gs_usb", "channel": "gs_usb:canmain", "bus": 3, "address": 7,
                                       "bitrate": 250000}, f.log[-1])
                check("innomaker: the bus says its backend and role",
                      getattr(b, "vtrux_backend", None) == "gs_usb" and getattr(b, "vtrux_role", None) == "canmain")
                check("innomaker: while open, canmain is on libusb (no netdev)",
                      f.state["canmain"]["mode"] == "libusb" and f.state["canmain"]["netdev"] == "none")
                del f.log[:]
                b.shutdown()
                rel = [e for e in f.log if e[0] == "release"]
                k = f.kinds()
                check("innomaker: shutdown shuts the bus, frees its pyusb handle ONCE, then canctl mode canmain "
                      "socketcan --wait (bench_layer's put-back), read back", k[:2] == ["shutdown", "release"]
                      and len(rel) == 1 and rel[0][1] is b and f.canctls() == [RESTORE], f.log)
                check("innomaker: after the close canmain's netdev exists and is down", f.down("canmain"))
                del f.log[:]
                b.shutdown()
                check("innomaker: a second shutdown does nothing", f.log == [], f.log)
            evs = read_evs(runs)
            check("innomaker: the open (role, USB device, bus, address, bitrate, what was found, the reader) and the "
                  "close recorded in GI_RUNS_DIR's kvaser_opens.jsonl",
                  len(evs) == 2 and evs[0]["event"] == "open" and evs[0]["backend"] == "gs_usb"
                  and evs[0]["role"] == "canmain" and evs[0]["usb"] == "1-3.2" and evs[0]["bus"] == 3
                  and evs[0]["address"] == 7 and evs[0]["bitrate"] == 250000 and evs[0]["held"] is False
                  and evs[0]["found"]["mode"] == "socketcan" and evs[0]["reader"] == "canbench.bench_layer.open_bench"
                  and evs[1]["event"] == "close" and evs[1]["role"] == "canmain" and "error" not in evs[1], evs)
        finally:
            gi_paths.remove_tree(runs)
    case("innomaker: opens", opens)

    def by_role():
        f = InnoFakes()
        with f as kb:
            b = kb.innomaker_bus("canchg")
            check("innomaker: canchg opens canchg's own USB device (bus 1, address 12) at 500k by default, "
                  "canmain untouched", f.log[-1][1] == {"interface": "gs_usb", "channel": "gs_usb:canchg", "bus": 1,
                                                        "address": 12, "bitrate": 500000}
                  and f.canctls() == [("mode", "canchg", "libusb")] and f.down("canmain"), f.log)
            b.shutdown()
            check("innomaker: canchg handed back to socketcan, down",
                  f.canctls()[-1] == ("mode", "canchg", "socketcan", "--wait", "10") and f.down("canchg"), f.log)
    case("innomaker: by role", by_role)

    def refusals():
        for kw in ({"index": 0}, {"channel": "can0"}, {"bus": 3}, {"address": 7}, {"serial": "X"}):
            f = InnoFakes()
            e = raises(f, lambda kb: kb.innomaker_bus("canmain", **kw))
            check("innomaker: asked by %s -> InnomakerError naming it (bench_layer.refuse_picks), no canctl, no open"
                  % ", ".join(kw), type(e).__name__ == "InnomakerError" and all(k in str(e) for k in kw)
                  and f.canctls() == [] and "Bus" not in f.kinds(), (e, f.log))
        for role in ("kvaser", "can0", "pcanfd", 0, None):
            f = InnoFakes()
            e = raises(f, lambda kb: kb.innomaker_bus(role))
            check("innomaker: role %r is not an Innomaker's -> InnomakerError, no canctl, no open" % (role,),
                  type(e).__name__ == "InnomakerError" and "not an Innomaker role" in str(e) and f.canctls() == []
                  and "Bus" not in f.kinds(), (e, f.log))
        for label, kw, want in (("no netdev canmain (canbench: socketcan mode with no netdev)",
                                 {"missing": ("canmain",)}, "no netdev"),
                                ("canmain not attached (canbench)", {"roles": {"canmain": {"attached": 0}}},
                                 "not attached"),
                                ("no role canmain in canctl's table (canbench)", {"roles": {"canmain": None}},
                                 "not a canctl role")):
            f = InnoFakes(**kw)
            e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
            check("innomaker: %s -> InnomakerError naming it, no canctl, no open" % label,
                  type(e).__name__ == "InnomakerError" and want in str(e) and f.canctls() == []
                  and "Bus" not in f.kinds(), (e, f.log))
        f = InnoFakes(holder=None)
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: the lock free -> BenchNotHeld (the guard plugged into bench_layer), before canctl or an "
              "open", type(e).__name__ == "BenchNotHeld" and f.canctls() == [] and "Bus" not in f.kinds(), (e, f.log))
    case("innomaker: refusals", refusals)

    def found_states():
        # the gen-inhibit reviewer's ruling of 2026-11-19: an Innomaker found up is interference, refused before
        # bench_layer touches it, recorded as a finding, its link left up
        runs = gi_paths.scratch("kvaser-bus-inno-foundup")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        try:
            for role in INNOS:
                f = InnoFakes(start_up=(role,), runs_dir=runs)
                e = raises(f, lambda kb: kb.innomaker_bus(role))
                evs = [x for x in read_evs(runs) if x.get("role") == role]
                check("innomaker: %s found up -> InnomakerError naming it, no canctl call (not taken down, not "
                      "handed over), nothing opened, its link still up" % role,
                      type(e).__name__ == "InnomakerError" and "found up" in str(e) and role in str(e)
                      and f.canctls() == [] and "Bus" not in f.kinds() and f.state[role]["link"] == "up", (e, f.log))
                check("innomaker: %s found up -> recorded in kvaser_opens.jsonl as a finding (refused, found up), "
                      "and no open recorded" % role,
                      [(x["event"], x.get("finding"), x.get("what")) for x in evs]
                      == [("refused", "found up", "innomaker_bus")], evs)
        finally:
            gi_paths.remove_tree(runs)
        f = InnoFakes(start_up=("canchg",))
        with f as kb:
            b = kb.innomaker_bus("canmain")
            b.shutdown()
        check("innomaker: canchg up does not refuse canmain's open (each role's own link)",
              f.canctls() == [LIBUSB, RESTORE] and f.down("canmain") and f.state["canchg"]["link"] == "up", f.log)
        f = InnoFakes(holder=None, start_up=("canmain",))
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: the lock free and canmain up -> BenchNotHeld (the guard before the link is read)",
              type(e).__name__ == "BenchNotHeld" and f.canctls() == [], (e, f.log))
        f = InnoFakes(roles={"canmain": {"mode": "libusb", "driver": "none", "netdev": "none", "link": "none"}})
        with f as kb:
            b = kb.innomaker_bus("canmain")
            b.shutdown()
        check("innomaker: canmain found in libusb mode -> opened as it is, left in libusb (nothing to put back)",
              f.canctls() == [] and "Bus" in f.kinds() and f.state["canmain"]["mode"] == "libusb", f.log)
    case("innomaker: found states", found_states)

    def failures():
        f = InnoFakes(libusb_rc=1)
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: canctl mode libusb fails -> InnomakerError naming it, no open, canmain still down",
              type(e).__name__ == "InnomakerError" and "libusb failed (rc 1" in str(e) and "Bus" not in f.kinds()
              and f.canctls() == [LIBUSB] and f.down("canmain"), (e, f.log))
        f = InnoFakes(use_raises=True)
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: the pin refuses -> its error raised before canctl is run or anything opens",
              isinstance(e, RuntimeError) and f.canctls() == [] and "Bus" not in f.kinds(), (e, f.log))
        f = InnoFakes(libusb_bound=True)
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: canmain still bound after canctl mode libusb -> InnomakerError naming it (bench_layer: not "
              "settled), nothing opened", type(e).__name__ == "InnomakerError" and "not settled in libusb" in str(e)
              and "Bus" not in f.kinds(), (e, f.log))
        for label, kw in (("the gs_usb open raises", {"bus_raises": OSError("planted open failure")}),
                          ("the fixes raise", {"apply_raises": True})):
            f = InnoFakes(**kw)
            e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
            check("innomaker: %s -> its own error raised, canmain handed back (socketcan --wait), down" % label,
                  e is not None and type(e).__name__ != "InnomakerError" and f.canctls() == [LIBUSB, RESTORE]
                  and f.down("canmain"), (e, f.log))
        f = InnoFakes(bus_raises=OSError("planted open failure"), restore_gone=True)
        e = raises(f, lambda kb: kb.innomaker_bus("canmain"))
        check("innomaker: the open raises and canmain does not come back -> one InnomakerError naming both",
              type(e).__name__ == "InnomakerError" and "planted open failure" in str(e) and "not put back" in str(e),
              (e, f.log))
    case("innomaker: failures", failures)

    def close_failures():
        runs = gi_paths.scratch("kvaser-bus-inno-runs")
        for label, kw, want in (
                ("canctl socketcan --wait fails (though canmain came back)",
                 {"restore_rc": 3, "restore_applies": True}, "socketcan --wait 10 failed (rc 3"),
                ("canmain comes back up", {"restore_up": True}, "not settled in socketcan"),
                ("canmain does not come back", {"restore_gone": True}, "not settled in socketcan")):
            gi_paths.remove_tree(runs)
            os.makedirs(runs)
            try:
                f = InnoFakes(runs_dir=runs, **kw)
                with f as kb:
                    b = kb.innomaker_bus("canmain")
                    try:
                        b.shutdown()
                        e = None
                    except kb.InnomakerError as x:
                        e = x
                evs = read_evs(runs)
                check("innomaker: at the close %s -> InnomakerError, and the close recorded with the error" % label,
                      e is not None and want in str(e) and evs[-1]["event"] == "close"
                      and want in evs[-1].get("error", ""), (e, evs))
            finally:
                gi_paths.remove_tree(runs)
        f = InnoFakes(shutdown_raises=True)
        with f as kb:
            b = kb.innomaker_bus("canmain")
            del f.log[:]
            try:
                b.shutdown()
                e = None
            except OSError as x:
                e = x
            down = f.down("canmain")
        rel = [x for x in f.log if x[0] == "release"]
        check("innomaker: a shutdown that raises still frees the handle (once) and hands canmain back, and the "
              "error is not swallowed", isinstance(e, OSError) and len(rel) == 1 and rel[0][1] is b
              and f.canctls() == [RESTORE] and down, (e, f.log))
    case("innomaker: close failures", close_failures)

    def held():
        """innomaker_held (condition 5, 2026-11-18) in bench_layer's calls (2026-11-19)."""
        runs = gi_paths.scratch("kvaser-bus-held-runs")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        try:
            f = InnoFakes(runs_dir=runs)
            with f as kb:
                with kb.innomaker_held("canmain") as inno:
                    k = f.kinds()
                    check("held: entering checks the lock, then canctl mode canmain libusb (bench_layer.to_libusb), "
                          "read back; nothing opened", "lock" in k and k.index("lock") < k.index("canctl")
                          and f.canctls() == [LIBUSB] and "Bus" not in k
                          and f.state["canmain"]["mode"] == "libusb", f.log)
                    del f.log[:]
                    b1 = inno.open(bitrate=250000)
                    check("held: open is open_bench on the held role, by its bus and address, at the asked "
                          "bitrate, with no canctl", f.canctls() == [] and f.log[-1][1] == {
                              "interface": "gs_usb", "channel": "gs_usb:canmain", "bus": 3, "address": 7,
                              "bitrate": 250000}, f.log)
                    check("held: the bus says its backend and role",
                          (getattr(b1, "vtrux_backend", None), getattr(b1, "vtrux_role", None)) == ("gs_usb", "canmain"))
                    del f.log[:]
                    b1.shutdown()
                    rel = [e for e in f.log if e[0] == "release"]
                    check("held: a close is the bus's only (the bus, the handle freed once), no hand-back",
                          [k for k in f.kinds() if k not in ("use", "roles", "lock")] == ["shutdown", "release"]
                          and len(rel) == 1 and f.canctls() == [], f.log)
                    check("held: canmain stays on libusb after a close", f.state["canmain"]["mode"] == "libusb")
                    del f.log[:]
                    b1.shutdown()
                    check("held: a second shutdown does nothing", f.log == [], f.log)
                    b2 = inno.open(bitrate=500000)
                    check("held: it opens again, at another bitrate, still no canctl",
                          f.canctls() == [] and f.log[-1][1].get("bitrate") == 500000, f.log)
                    del f.log[:]
                k = f.kinds()
                rel = [e for e in f.log if e[0] == "release"]
                check("held: exit shuts the bus left open (once), THEN hands canmain back (bench_layer.to_socketcan), "
                      "read back", k[:2] == ["shutdown", "release"] and len(rel) == 1 and rel[0][1] is b2
                      and f.canctls() == [RESTORE] and f.down("canmain"), f.log)
                e = None
                try:
                    inno.open()
                except kb.InnomakerError as x:
                    e = x
                check("held: an open after the block -> InnomakerError, nothing opened",
                      e is not None and "not held" in str(e), e)
            evs = read_evs(runs)
            check("held: the hold, two opens and two closes recorded as held, then the hand-back with its read-back",
                  [(x["event"], x.get("held"), x.get("bitrate")) for x in evs]
                  == [("hold", None, None), ("open", True, 250000), ("close", True, None), ("open", True, 500000),
                      ("close", True, None), ("close", None, None)]
                  and evs[-1]["to"] == "socketcan" and evs[-1]["readback"] == "mode socketcan, netdev canmain, link down",
                  evs)
        finally:
            gi_paths.remove_tree(runs)

        for kw in ({"index": 0}, {"channel": "can0"}, {"bus": 3}, {"address": 7}):
            f = InnoFakes()
            with f as kb:
                with kb.innomaker_held("canmain") as inno:
                    del f.log[:]
                    try:
                        inno.open(**kw)
                        e = None
                    except kb.InnomakerError as x:
                        e = x
            check("held: open asked by %s -> InnomakerError, nothing opened, canmain still handed back"
                  % ", ".join(kw), e is not None and "Bus" not in [x[0] for x in f.log]
                  and f.canctls() == [RESTORE] and f.down("canmain"), (e, f.log))
        for label, kw, exc in (("the lock free", {"holder": None}, "BenchNotHeld"),
                               ("netdev canmain found up (refused before bench_layer, ruling 2026-11-19)",
                                {"start_up": ("canmain",)}, "InnomakerError")):
            runs = gi_paths.scratch("kvaser-bus-held-enter")
            gi_paths.remove_tree(runs)
            os.makedirs(runs)
            try:
                f = InnoFakes(runs_dir=runs, **kw)
                entered = []

                def go(kb):
                    with kb.innomaker_held("canmain"):
                        entered.append(1)
                e = raises(f, go)
                evs = read_evs(runs) if os.path.exists(os.path.join(runs, "kvaser_opens.jsonl")) else []
            finally:
                gi_paths.remove_tree(runs)
            check("held: %s -> %s at enter, no canctl, the block not run" % (label, exc),
                  type(e).__name__ == exc and f.canctls() == [] and not entered, (e, f.log))
            if "start_up" in kw:
                check("held: canmain found up -> the refusal names it, recorded as a finding (refused, found up, "
                      "innomaker_held), no hold recorded, its link still up",
                      "found up" in str(e) and [(x["event"], x.get("finding"), x.get("what")) for x in evs]
                      == [("refused", "found up", "innomaker_held")] and f.state["canmain"]["link"] == "up",
                      (e, evs, f.log))
        f = InnoFakes()
        e = raises(f, lambda kb: kb.innomaker_held("kvaser").__enter__())
        check("held: role kvaser -> InnomakerError at enter, no canctl",
              type(e).__name__ == "InnomakerError" and "not an Innomaker role" in str(e) and f.canctls() == [], (e, f.log))
        f = InnoFakes(libusb_rc=1)
        e = raises(f, lambda kb: kb.innomaker_held("canmain").__enter__())
        check("held: canctl mode libusb fails -> InnomakerError, canmain still down, the block not run",
              type(e).__name__ == "InnomakerError" and "libusb failed (rc 1" in str(e)
              and f.canctls() == [LIBUSB] and f.down("canmain"), (e, f.log))

        def block_raises(kb):
            with kb.innomaker_held("canmain") as inno:
                inno.open()
                raise ValueError("planted block failure")
        f = InnoFakes()
        e = raises(f, block_raises)
        check("held: the block raises -> its error raised, the bus shut, canmain handed back, down",
              isinstance(e, ValueError) and "release" in f.kinds() and f.canctls() == [LIBUSB, RESTORE]
              and f.down("canmain"), (e, f.log))

        def open_close(kb):
            with kb.innomaker_held("canmain") as inno:
                inno.open().shutdown()
        f = InnoFakes(restore_up=True)
        e = raises(f, open_close)
        check("held: the hand-back at exit not settled (canmain comes back up) -> InnomakerError",
              type(e).__name__ == "InnomakerError" and "not settled in socketcan" in str(e), (e, f.log))

        def open_only(kb):
            with kb.innomaker_held("canmain") as inno:
                inno.open()
        f = InnoFakes(shutdown_raises=True)
        e = raises(f, open_only)
        check("held: a close that raises at exit -> canmain still handed back, then InnomakerError naming it",
              type(e).__name__ == "InnomakerError" and "close failed" in str(e) and f.canctls() == [LIBUSB, RESTORE]
              and f.down("canmain"), (e, f.log))
        f = InnoFakes(bus_raises=OSError("planted open failure"))
        e = raises(f, open_only)
        check("held: an open that raises -> its error raised, canmain handed back once at exit, down",
              isinstance(e, OSError) and f.canctls() == [LIBUSB, RESTORE] and f.down("canmain"), (e, f.log))
    case("innomaker: held", held)


def echo():
    """echo_drain (the gen-inhibit reviewer's ruling (C), 2026-11-19): a sender on a libusb gs_usb role reads its
    echoes, or the device takes no more frames (on the bench: none after 257 unread). Against _fake_gs_usb's
    StallingGs, which stalls the same way at its cap."""
    import _fake_gs_usb as FG

    def load():
        spec = importlib.util.spec_from_file_location("kvaser_bus_echo", os.path.join(SRC, "kvaser_bus.py"))
        kb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb)
        evs = []
        kb.record = evs.append
        return kb, evs

    def send(g, n, gap=0.0001):
        for i in range(n):
            try:
                g.send(FG.Msg(0x6A8, i.to_bytes(4, "big")))
            except FG.StallError:
                return i
            time.sleep(gap)
        return n

    def stall_without():
        g = FG.StallingGs(cap=256)
        got = send(g, 300, gap=0.0)
        check("echo: the fake, with nothing reading it, takes 256 frames and stalls on the 257th (as canmain did "
              "at its cap)", got == 256 and g.stalls == 1, (got, g.stalls))
    case("echo: the fake stalls", stall_without)

    def drained():
        kb, evs = load()
        g = FG.StallingGs(cap=256)
        d = kb.echo_drain(g, "canmain", "test")
        got = send(g, 700)
        g.put(FG.Msg(0x123, b"\x01"))                       # another node's frame
        g.put(FG.Msg(0x20000004, b"", is_error_frame=True))  # an error frame
        ev = d.stop(sent=got)
        alive = d.is_alive()
        g.shutdown()
        check("echo: with the drain, 700 frames sent and no stall", got == 700 and g.stalls == 0, (got, g.stalls))
        check("echo: stop() joins the drain (not running after it), so the bus's shutdown finds it stopped",
              not alive and g.drain_alive_at_shutdown is False, (alive, g.drain_alive_at_shutdown))
        check("echo: every echo counted, the other node's frame and the error frame apart, echoes == sent",
              (ev.get("sent"), ev.get("echoes"), ev.get("rx"), ev.get("errors"), ev.get("echoes_equal_sent"))
              == (700, 700, 1, 1, True) and "error" not in ev, ev)
        check("echo: the counts recorded once, as an echoes event naming the role and what",
              evs == [ev] and ev.get("event") == "echoes" and ev.get("role") == "canmain" and ev.get("what") == "test"
              and ev.get("backend") == "gs_usb", evs)
        check("echo: the drain is attached to the bus (bus.echo_drain), for innomaker_held's exit",
              getattr(g, "echo_drain", None) is d)
    case("echo: drained", drained)

    def unacked():
        kb, evs = load()
        g = FG.StallingGs(cap=256, acked=lambda n: n % 10 != 0)
        d = kb.echo_drain(g, "canmain", "test")
        got = send(g, 300)
        ev = d.stop(sent=got)
        check("echo: frames nobody ACKed give no echo: fewer echoes than frames, recorded as not equal",
              (ev.get("sent"), ev.get("echoes"), ev.get("echoes_equal_sent")) == (300, 270, False), ev)
    case("echo: unACKed", unacked)

    def queued_at_stop():
        kb, evs = load()
        g = FG.StallingGs(cap=256)
        d = kb.echo_drain(g, "canmain", "test")
        time.sleep(0.01)
        with g.lock:                    # 50 echoes land at once, just as the stop is asked
            g.q.extend(FG.Msg(0x6A8, b"", is_rx=False) for _ in range(50))
        ev = d.stop(sent=50)
        check("echo: echoes queued when the stop is asked are read before it returns",
              ev.get("echoes") == 50 and ev.get("echoes_equal_sent") is True, ev)
    case("echo: queued at stop", queued_at_stop)

    def recv_raises():
        kb, evs = load()
        g = FG.StallingGs(cap=256)
        d = kb.echo_drain(g, "canmain", "test")
        g.closed = True
        time.sleep(0.01)
        ev = d.stop(sent=0)
        check("echo: a read that raises ends the drain and is recorded at its stop",
              "recv after shutdown" in str(ev.get("error")) and not d.is_alive(), ev)
    case("echo: recv raises", recv_raises)

    def held_exit():
        runs = gi_paths.scratch("kvaser-bus-echo-held")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        try:
            f = InnoFakes(runs_dir=runs)
            with f as kb:
                with kb.innomaker_held("canmain") as inno:
                    b = inno.open(bitrate=500000)
                    d = kb.echo_drain(b, "canmain", "test held")
                    time.sleep(0.005)
                    alive_in = d.is_alive()
            evs = read_evs(runs)
        finally:
            gi_paths.remove_tree(runs)
        kinds = [x["event"] for x in evs]
        check("echo: innomaker_held's exit stops a drain still running BEFORE it shuts the bus, and records it",
              alive_in and getattr(b, "drain_alive_at_shutdown", None) is False and not d.is_alive()
              and "echoes" in kinds and kinds.index("echoes") < len(kinds) - 1 and kinds[-1] == "close", (kinds, f.log))
    case("echo: held exit", held_exit)


def pcan():
    """The PCAN by its canctl role pcanfd (the user's ruling, 2026-11-18)."""
    def opens():
        runs = gi_paths.scratch("kvaser-bus-pcan-runs")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        saved = os.environ.get("GI_RUNS_DIR")
        os.environ["GI_RUNS_DIR"] = runs
        try:
            f = Fakes()
            with f as kb:
                b = kb.pcan_bus(bitrate=250000, auto_reset=True)
            k = f.kinds()
            check("pcan: the lock checked before the open (bench_layer.open_bench's guard), then the fixes and the "
                  "open, no canctl", "lock" in k and k.index("lock") < k.index("apply") < k.index("Bus")
                  and f.canctls() == [], f.log)
            check("pcan: python-can's pcan by pcanfd's device id, never by channel, with the caller's other arguments",
                  f.log[-1][1] == {"interface": "pcan", "device_id": 0, "bitrate": 250000, "auto_reset": True}, f.log[-1])
            check("pcan: the bus says its backend, role, device id and char device",
                  (b.vtrux_backend, b.vtrux_role, b.vtrux_device_id, b.vtrux_chardev)
                  == ("pcan", "pcanfd", 0, "pcanusbfd32"))
            with open(os.path.join(runs, "kvaser_opens.jsonl"), encoding="ascii") as fh:
                evs = [json.loads(x) for x in fh]
            check("pcan: the open recorded with the role, device id, char device, channel and bitrate",
                  len(evs) == 1 and evs[0]["event"] == "open" and evs[0]["backend"] == "pcan"
                  and evs[0]["role"] == "pcanfd" and evs[0]["device_id"] == 0 and evs[0]["chardev"] == "pcanusbfd32"
                  and evs[0]["bitrate"] == 250000 and evs[0]["reader"] == "canbench.bench_layer.open_bench", evs)
        finally:
            if saved is None:
                os.environ.pop("GI_RUNS_DIR", None)
            else:
                os.environ["GI_RUNS_DIR"] = saved
            gi_paths.remove_tree(runs)
        f = Fakes(roles={"pcanfd": {"devid": "42"}})
        with f as kb:
            kb.pcan_bus()
        check("pcan: the device id as canctl gives it (42), 500k by default",
              f.log[-1][1] == {"interface": "pcan", "device_id": 42, "bitrate": 500000}, f.log[-1])
    case("pcan: opens", opens)

    def refusals():
        for opener in ("pcan_bus", "pcan_witness_bus"):
            for kw in ({"channel": "PCAN_USBBUS1"}, {"device_id": 0}, {"interface": "pcan"}, {"serial": "X"}):
                f = Fakes()
                e = raises(f, lambda kb: getattr(kb, opener)(bitrate=500000, **kw))
                check("%s: asked by %s -> RoleError naming it (bench_layer.refuse_picks), before the lock or an open"
                      % (opener, list(kw)[0]), type(e).__name__ == "RoleError" and list(kw)[0] in str(e)
                      and "lock" not in f.kinds() and "Bus" not in f.kinds() and "Pcan" not in f.kinds(), (e, f.log))
        # canbench's refusals (bench_roles.resolve / open_role), re-raised as kvaser_bus.RoleError
        for label, kw, want, openers in (
                ("no PCAN attached", {"roles": {"pcanfd": {"attached": 0}}}, "not attached", "both"),
                ("two PCANs attached (the one present is not one)", {"roles": {"pcanfd": {"attached": 2}}},
                 "2 USB devices match pcanfd", "both"),
                ("no PCAN device id (pcan.ko not loaded)", {"roles": {"pcanfd": {"devid": "-", "driver": "none"}}},
                 "no PCAN chardev", "both"),
                ("no pcanfd role", {"roles": {"pcanfd": None}}, "not a canctl role", "both"),
                ("canctl roles fails", {"roles_rc": 1}, "canctl roles exited 1", "both"),
                ("canctl roles names no role", {"roles_raw": "\n"}, "not a canctl role", "both"),
                ("a canctl roles line that lacks fields (canctl too old)", {"roles_raw": "role=pcanfd kind=pcan\n"},
                 "lacks", "both"),
                ("no char device: the witness has no sysfs identity", {"roles": {"pcanfd": {"chardev": "none"}}},
                 "no PCAN char device", ("pcan_witness_bus",)),
                ("pcanfd is not a PCAN (kind slcan, on a tty)", {"roles": {"pcanfd": {"kind": "slcan", "tty": "/dev/x"}}},
                 "mode tty, not pcan", ("pcan_witness_bus",))):
            for opener in (("pcan_bus", "pcan_witness_bus") if openers == "both" else openers):
                f = Fakes(**kw)
                raised = None
                with f as kb:
                    try:
                        getattr(kb, opener)(bitrate=500000)
                    except kb.RoleError as e:
                        raised = e
                check("%s: %s -> RoleError naming it, nothing opened" % (opener, label),
                      raised is not None and want in str(raised) and not {"Bus", "Pcan", "canctl"} & set(f.kinds()),
                      (raised, f.log))
        f = Fakes(roles={"pcanfd": {"chardev": "none"}})
        with f as kb:
            b = kb.pcan_bus(bitrate=500000)
        check("pcan_bus: no char device opens anyway (only the witness needs its sysfs entry), recorded as None",
              b.vtrux_chardev is None and "Bus" in f.kinds(), f.log)
    case("pcan: refusals", refusals)

    def read_on():
        """Every PCAN reader reads on past a bus state python-can's pcan raises from recv (F6, 2026-11-19:
        run cb08's B9, B10 and B14 PCAN readers died of "error passive")."""
        class PcanCanOperationError(Exception):
            pass
        passive = PcanCanOperationError("Bus error: The CAN controller is error passive")
        heavy = PcanCanOperationError("Bus error: an error counter reached the 'heavy'/'warning' limit")
        f = Fakes()
        with f as kb:
            b = kb.pcan_bus(bitrate=500000)
            b.script = ["f1", passive, "f2", heavy, "f3"]
            got = [b.recv(timeout=0.01) for _ in range(5)]
            check("pcan_bus: a bus state raised from recv reads as None and the reads go on",
                  got == ["f1", None, "f2", None, "f3"], got)
            check("pcan_bus: each bus state is kept in vtrux_states with its text, in order",
                  [s[1] for s in getattr(b, "vtrux_states", [])]
                  == ["PcanCanOperationError: " + str(passive), "PcanCanOperationError: " + str(heavy)],
                  getattr(b, "vtrux_states", None))
            check("pcan_bus: a bus state's time is the perf_counter's",
                  all(isinstance(s[0], float) and 0 < time.perf_counter() - s[0] < 60 for s in b.vtrux_states))
            b.script = [PcanCanOperationError("The handle is invalid")]
            other = None
            try:
                b.recv(timeout=0.01)
            except PcanCanOperationError as e:
                other = e
            check("pcan_bus: any other error from recv is raised as before, and not kept",
                  other is not None and len(b.vtrux_states) == 2, (other, b.vtrux_states))
            b.script = []
            check("pcan_bus: nothing queued still reads as None, no state kept",
                  b.recv(timeout=0.0) is None and len(b.vtrux_states) == 2)
    case("pcan: reads on past a bus state", read_on)


def socketcan():
    """The Kvaser on Linux through SocketCAN (gen-inhibit reviewer, 2026-11-18, ruling (a))."""
    def opens():
        runs = gi_paths.scratch("kvaser-bus-runs")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        try:
            f = SockFakes(runs_dir=runs)
            with f as kb:
                b = kb.kvaser_bus(channel=0, bitrate=500000, driver_mode=True)
                opened = [e for e in f.log if e[0] == "HwBus"]
                k = f.kinds()
                check("socketcan: the lock checked first; canctl up kvaser at the asked bitrate with restart-ms "
                      "(bench_layer.open_bench), read back; then the reader, then the error mask read",
                      k[0] == "lock" and f.canctls() == [("up", "kvaser", "500000", "restart-ms", "100")]
                      and "ip" in k[k.index("canctl"):k.index("HwBus")] and k.index("HwBus") < k.index("getsockopt")
                      and "apply" in k[:k.index("HwBus")], f.log)
                check("socketcan: open_role's SocketcanHwBus (hw_timestamps) on netdev kvaser, its stamp check "
                      "on 20 data frames, 2 s from host time",
                      opened and opened[0][1:] == ("kvaser", {"check_frames": 20, "host_gap_s": 2.0}), opened)
                check("socketcan: the error mask read back is CAN_RAW_ERR_FILTER on SOL_CAN_RAW",
                      ("getsockopt", 101, 2) in f.log, f.log)
                check("socketcan: the bus says its backend", getattr(b, "vtrux_backend", None) == "socketcan")
                del f.log[:]
                b.shutdown()
                k = f.kinds()
                check("socketcan: shutdown shuts the bus, then canctl down kvaser (bench_layer.link_down), read back",
                      k[0] == "shutdown" and f.canctls() == [("down", "kvaser")] and k.index("canctl") < k.index("ip")
                      and f.linkst["up"] is False, f.log)
            with open(os.path.join(runs, "kvaser_opens.jsonl"), encoding="ascii") as fh:
                evs = [json.loads(x) for x in fh]
            check("socketcan: the open, the stamp check and the close recorded in GI_RUNS_DIR's "
                  "kvaser_opens.jsonl, naming the backend, the channel asked and opened, the bitrate, the "
                  "driver, the link, the mask and the reader",
                  len(evs) == 3 and evs[0]["event"] == "open"
                  and evs[0]["reader"] == "canbench.bench_layer.open_bench -> SocketcanHwBus" and evs[0]["role"] == "kvaser"
                  and evs[0]["found"]["mode"] == "socketcan" and evs[0]["found"]["link"]["up"] is False
                  and evs[1]["event"] == "stamps" and evs[1]["stamp_stats"]["state"] == "hardware" and evs[0]["backend"] == "socketcan"
                  and evs[0]["channel_asked"] == 0 and evs[0]["channel"] == "kvaser" and evs[0]["bitrate"] == 500000
                  and evs[0]["driver"] == "kvaser_usb" and evs[0]["link"]["up"] is True
                  and evs[0]["link"]["bitrate"] == 500000 and evs[0]["err_mask"] == "0x1FFFFFFF"
                  and evs[2]["event"] == "close" and evs[2]["backend"] == "socketcan"
                  and evs[2]["link"]["up"] is False, evs)
        finally:
            gi_paths.remove_tree(runs)
    case("socketcan: opens", opens)

    def two():
        f = SockFakes()
        with f as kb:
            a = kb.kvaser_bus(channel=0, bitrate=250000)
            b = kb.kvaser_bus(channel=0, bitrate=250000)
            del f.log[:]
            a.shutdown()
            first = f.canctls()
            b.shutdown()
        check("socketcan: B9's 250000 asked of canctl", f.linkst["bitrate"] == 250000, f.linkst)
        check("socketcan: the link goes down only when the last bus this process opened is shut",
              first == [] and f.canctls() == [("down", "kvaser")], f.log)
    case("socketcan: two buses", two)

    def refuses(label, fk, kw, exc_name, opened_none=True):
        def go():
            f = fk()
            raised = None
            with f as kb:
                try:
                    kb.kvaser_bus(**kw)
                except Exception as e:      # noqa: BLE001
                    raised = e
                want = {"TypeError": TypeError, "OSError": OSError}.get(exc_name) or getattr(kb, exc_name)
            check("socketcan: %s -> %s, nothing left open" % (label, exc_name),
                  isinstance(raised, want) and ("HwBus" not in f.kinds() if opened_none else True)
                  and f.linkst["up"] is False, (raised, f.log))
            return f
        return go
    std = {"channel": 0, "bitrate": 500000}
    for label, fk, kw, exc in (
            ("the Kvaser on linuxcan's leaf", lambda: SockFakes(kvaser={"mode": "linuxcan", "driver": "leaf", "netdev": "none", "link": "none"}), std, "KvaserLinkError"),
            ("no netdev kvaser (canbench)", lambda: SockFakes(kvaser={"netdev": "none", "link": "none"}), std,
             "RoleError"),
            ("two Kvasers attached (canbench)", lambda: SockFakes(kvaser={"attached": 2}), std, "RoleError"),
            ("the Kvaser not attached (canbench)", lambda: SockFakes(kvaser={"attached": 0}), std, "RoleError"),
            ("no kvaser role (canbench)", lambda: SockFakes(roles={"kvaser": None}), std, "RoleError"),
            ("the kvaser role on another netdev (can9)", lambda: SockFakes(kvaser={"netdev": "can9"}), std,
             "KvaserLinkError"),
            ("canctl roles fails (canbench)", lambda: SockFakes(roles_rc=1), std, "RoleError"),
            ("driver_mode=False (CANlib's silent mode)", SockFakes, dict(std, driver_mode=False), "KvaserLinkError"),
            ("a CANlib-only keyword (single_handle)", SockFakes, dict(std, single_handle=True), "TypeError"),
            ("canctl up refused (rc 2)", lambda: SockFakes(canctl_rc=2), std, "KvaserLinkError"),
            ("canctl up failing (rc 3) after the link came up", lambda: SockFakes(canctl_rc=3, canctl_applies=True),
             std, "KvaserLinkError"),
            ("the link read back at another bitrate", lambda: SockFakes(read_bitrate=250000), std, "KvaserLinkError"),
            ("the link read back listen-only", lambda: SockFakes(read_lo=True), std, "KvaserLinkError"),
            ("the reader's open raising after the link came up",
             lambda: SockFakes(hw_raises=OSError("bind failed")), std, "OSError"),
            ("the link found up at the bitrate (bench_layer leaves it) and the reader's open raising",
             lambda: SockFakes(found_up=500000, hw_raises=OSError("bind failed")), std, "OSError"),
            ("open_role finding the link down after canctl up (canbench refuses a down link)",
             lambda: SockFakes(role_link_lags=True), std, "KvaserLinkError")):
        # the reader's own open raising is past the open call: what matters is the link down
        case("socketcan refuses: " + label, refuses(label, fk, kw, exc, opened_none=(exc != "OSError")))
    for label, fk in (("the Kvaser on linuxcan's leaf", lambda: SockFakes(kvaser={"mode": "linuxcan", "driver": "leaf", "netdev": "none", "link": "none"})),
                      ("two Kvasers attached", lambda: SockFakes(kvaser={"attached": 2})),
                      ("the kvaser role on another netdev (can9)", lambda: SockFakes(kvaser={"netdev": "can9"})),
                      ("driver_mode=False", SockFakes)):
        def nocanctl(label=label, fk=fk):
            f = fk()
            with f as kb:
                try:
                    kb.kvaser_bus(channel=0, bitrate=500000, driver_mode=(label != "driver_mode=False"))
                except Exception:       # noqa: BLE001
                    pass
            check("socketcan: %s -> canctl never asked" % label, f.canctls() == [], f.log)
        case("socketcan no canctl: " + label, nocanctl)

    def nomask():
        f = SockFakes(err_filter=0)
        raised = None
        with f as kb:
            try:
                kb.kvaser_bus(channel=0, bitrate=500000)
            except Exception as e:      # noqa: BLE001
                raised = e
            want = kb.KvaserLinkError
        check("socketcan: an error mask that reads 0 -> KvaserLinkError, the bus shut and the link down",
              isinstance(raised, want) and "shutdown" in f.kinds() and f.canctls()[-1] == ("down", "kvaser")
              and f.linkst["up"] is False, (raised, f.log))
    case("socketcan: no error mask", nomask)

    def stuck():
        f = SockFakes(stuck_up=True)
        raised = None
        with f as kb:
            b = kb.kvaser_bus(channel=0, bitrate=500000)
            try:
                b.shutdown()
            except Exception as e:      # noqa: BLE001
                raised = e
            want = kb.KvaserLinkError
        check("socketcan: a link still UP after canctl down -> the close raises KvaserLinkError",
              isinstance(raised, want), (raised, f.log))
    case("socketcan: link stuck up", stuck)

    def canlib_record():
        runs = gi_paths.scratch("kvaser-bus-runs-canlib")
        gi_paths.remove_tree(runs)
        os.makedirs(runs)
        saved = os.environ.get("GI_RUNS_DIR")
        os.environ["GI_RUNS_DIR"] = runs
        try:
            f = Fakes()
            with f as kb:
                b = kb.kvaser_bus(channel=0, bitrate=500000)
            with open(os.path.join(runs, "kvaser_opens.jsonl"), encoding="ascii") as fh:
                evs = [json.loads(x) for x in fh]
            check("canlib (off Linux): the open recorded with backend canlib, and the bus says so",
                  len(evs) == 1 and evs[0]["backend"] == "canlib" and evs[0]["channel"] == 0
                  and getattr(b, "vtrux_backend", None) == "canlib", evs)
        finally:
            if saved is None:
                os.environ.pop("GI_RUNS_DIR", None)
            else:
                os.environ["GI_RUNS_DIR"] = saved
            gi_paths.remove_tree(runs)
    case("canlib: recorded", canlib_record)

    def detect():
        for label, fk, want in (("kvaser_usb", SockFakes, {"interface": "kvaser", "channel": "kvaser"}),
                                ("leaf", lambda: SockFakes(kvaser={"mode": "linuxcan", "driver": "leaf", "netdev": "none", "link": "none"}), None),
                                ("no netdev", lambda: SockFakes(kvaser={"netdev": "none", "link": "none"}), None),
                                ("two Kvasers", lambda: SockFakes(kvaser={"attached": 2}), None)):
            f = fk()
            with f as kb:
                got = kb.detect()
            check("detect on Linux, the Kvaser on %s -> %r" % (label, want), got == want, got)
    case("socketcan: detect", detect)

    def guard():
        f = SockFakes(holder=None)
        raised = None
        with f as kb:
            try:
                kb.kvaser_bus(channel=0, bitrate=500000)
            except kb.BenchNotHeld as e:
                raised = e
        check("socketcan: the lock free -> BenchNotHeld before canctl, sysfs or an open",
              raised is not None and f.kinds() == ["lock"], (raised, f.log))
        f = SockFakes()
        raised = None
        with f as kb:
            kb._lock_holder = lambda: (f.log.append(("lock",)), f.holder)[1] if len(
                [e for e in f.log if e[0] == "lock"]) == 0 else None
            try:
                kb.kvaser_bus(channel=0, bitrate=500000)
            except kb.BenchNotHeld as e:
                raised = e
        check("socketcan: the guard plugged into bench_layer: the lock lost after kvaser_bus's own check -> "
              "BenchNotHeld from the layer before canctl", raised is not None and f.canctls() == [], (raised, f.log))
    case("socketcan: guard", guard)

    def backend():
        spec = importlib.util.spec_from_file_location("kvaser_bus_be", os.path.join(SRC, "kvaser_bus.py"))
        kb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb)
        check("backend: socketcan on linux, canlib on win32",
              kb.backend("linux") == "socketcan" and kb.backend("win32") == "canlib")
    case("socketcan: backend", backend)


# The pinned canbench's SocketcanHwBus socket (2026-11-18): the error mask kvaser_bus reads back
# is what canbench set (a PF_CAN raw socket needs no interface or root until it is bound, so the
# bind is skipped). A plain CAN_RAW socket reading 0 shows the read-back can tell.
CHILD_MASK = r"""
import json, socket, sys
sys.path.insert(0, sys.argv[1])
import kvaser_bus as K
K._pin_tool().use()
import importlib
hw = importlib.import_module("canbench.live.socketcan_hw")
socket.socket.bind = lambda self, addr: None     # no interface or root needed until bound
b = hw.SocketcanHwBus("kvaser")
out = {"reader": K.err_mask(b)}
b.shutdown()
plain = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
class P:
    pass
p = P()
p._sock = plain
out["plain"] = K.err_mask(p)
plain.close()
print("CHILD " + json.dumps(out))
"""


def real_mask():
    if not sys.platform.startswith("linux"):
        print("      real mask: not Linux, not checked")
        return
    r = subprocess.run([sys.executable, "-B", "-c", CHILD_MASK, SRC], capture_output=True, text=True, timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("CHILD ")]
    if r.returncode != 0 or not line:
        check("real mask: the child ran", False, (r.returncode, r.stdout[-300:], r.stderr[-600:]))
        return
    out = json.loads(line[0][6:])
    check("real mask: the pinned canbench's SocketcanHwBus socket reads back 0x1FFFFFFF through err_mask",
          out.get("reader") == 0x1FFFFFFF, out)
    check("real mask: a plain CAN_RAW socket reads 0 (the read-back can tell)", out.get("plain") == 0, out)


def bus_errors():
    """Error frames on kvaser_usb (gen-inhibit reviewer, 2026-11-18): B6, B12 and B22 count bus
    errors only. The kernel's classes (linux/can/error.h) are written out here from the header,
    not from kvaser_bus. The can-fail is a real frame: the first error frame of the 2026-11-18
    measurement (runs/kvaser_usb_measure_20261118T154231), id 0x20000288 (PROT | BUSERROR | CNT),
    data 0000020800000000, which must count."""
    import can
    spec = importlib.util.spec_from_file_location("kvaser_bus_err", os.path.join(SRC, "kvaser_bus.py"))
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)
    TX_TIMEOUT, LOSTARB, CRTL, PROT, TRX, ACK, BUSOFF, BUSERROR, RESTARTED, CNT = (
        0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x200)
    # bench_layer's classifier (2026-11-19) reads the backend from the bus's bench_role (open_role sets it)
    sock = types.SimpleNamespace(vtrux_backend="socketcan", bench_role=types.SimpleNamespace(interface="socketcan"))
    canlib = types.SimpleNamespace(vtrux_backend="canlib")
    pcan = types.SimpleNamespace(bench_role=types.SimpleNamespace(interface="pcan"))

    def err(cid, data=bytes(8)):
        return can.Message(arbitration_id=cid, is_error_frame=True, is_extended_id=False, data=data)

    real = err(0x288, bytes.fromhex("0000020800000000"))
    check("bus errors: the 2026-11-18 frame (PROT | BUSERROR | CNT) counts on SocketCAN (can-fail)",
          kb.bus_error(sock, real) is True and kb.bus_error_class(sock, 0x288) is True)
    for name, cid in (("PROT", PROT), ("BUSERROR", BUSERROR), ("ACK", ACK), ("TX_TIMEOUT", TX_TIMEOUT),
                      ("LOSTARB", LOSTARB), ("TRX", TRX), ("PROT | CRTL", PROT | CRTL),
                      ("ACK | CNT", ACK | CNT)):
        check("bus errors: %s counts on SocketCAN" % name, kb.bus_error(sock, err(cid)) is True, hex(cid))
    for name, cid in (("CRTL", CRTL), ("CRTL | CNT", CRTL | CNT), ("RESTARTED", RESTARTED),
                      ("BUSOFF", BUSOFF), ("BUSOFF | CRTL | CNT", BUSOFF | CRTL | CNT), ("CNT", CNT),
                      ("no class", 0)):
        check("bus errors: %s alone does not count on SocketCAN" % name, kb.bus_error(sock, err(cid)) is False
              and kb.bus_error_class(sock, cid) is False, hex(cid))
    check("bus errors: a data frame is never one, whatever its id",
          kb.bus_error(sock, can.Message(arbitration_id=0x288, data=bytes(8))) is False)
    for name, b in (("CANlib", canlib), ("a bus with no backend tag (the PCAN)", pcan)):
        check("bus errors: on %s every error frame counts, CRTL alone or no class" % name,
              kb.bus_error(b, err(CRTL)) is True and kb.bus_error(b, err(0)) is True
              and kb.bus_error_class(b, RESTARTED) is True)
        check("bus errors: on %s a data frame does not" % name,
              kb.bus_error(b, can.Message(arbitration_id=0x100, data=bytes(8))) is False)


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
        # (an open by role applies the fixes inside canbench's open_role, idempotent there, 5220d37)
        f = Fakes()
        with f as kb:
            kb.kvaser_bus(channel=0, bitrate=500000)
            kb.kvaser_bus(channel=0, bitrate=250000)
            kb.kvaser_bus(channel=1, bitrate=250000)
        check("fixes: applied once a process, over three CANlib opens", f.kinds().count("apply") == 1
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
    for kind in ("use_raises", "apply_raises"):
        case("kvaser_bus %s" % kind, refused(kind, "kvaser_bus"))

    def router():
        for iface, kw in (("kvaser", {"channel": 0, "bitrate": 500000}),):
            f = Fakes()
            cfg = dict(kw, interface=iface)
            with f as kb:
                kb.bus(**cfg)
            check("bus(interface=%r): through its opener, the lock checked and the fixes applied first" % iface,
                  f.kinds() == ["lock", "use", "apply", "Bus"] and f.log[-1][1] == dict(kw, interface=iface), f.log)
        f = Fakes()
        with f as kb:
            kb.bus(interface="pcan", bitrate=500000, auto_reset=True)
        k = f.kinds()
        check("bus(interface='pcan'): through pcan_bus, by pcanfd's device id, the lock checked first",
              "lock" in k and k.index("lock") < k.index("Bus")
              and f.log[-1][1] == {"interface": "pcan", "device_id": 0, "bitrate": 500000, "auto_reset": True}, f.log)
        f = Fakes()
        cfg = {"interface": "ixxat", "channel": 0, "bitrate": 500000}
        with f as kb:
            kb.bus(**cfg)
        check("bus(interface='ixxat'): straight to can.Bus with every argument",
              f.log and f.log[-1] == ("Bus", cfg) and cfg["interface"] == "ixxat", f.log)
        f = Fakes()
        raised = None
        with f as kb:
            try:
                kb.bus(interface="gs_usb", channel="can0", index=0, bitrate=500000)
            except TypeError as e:
                raised = e
        check("bus(interface='gs_usb'): refused (TypeError naming innomaker_bus), nothing checked or opened",
              raised is not None and "innomaker_bus" in str(raised) and f.log == [], (raised, f.log))
    case("bus: routes", router)

    def pcan_witness():
        # 2026-11-13, the reviewer's ruling (C): the PCAN as the session's timing witness
        f = Fakes()
        with f as kb:
            b = kb.pcan_witness_bus(bitrate=500000)
        k = f.kinds()
        check("pcan_witness_bus: the lock checked (bench_layer.guard), then bench_roles.resolve (canctl roles), then "
              "the pinned CanbenchPcanBus", "lock" in k and k.index("lock") < k.index("roles") < k.index("Pcan")
              and set(k[:k.index("lock")]) <= {"use"}, f.log)
        check("pcan_witness_bus: pcanfd's device id, the bitrate, and the bus-off reset off",
              f.log[-1][1] == {"device_id": 0, "bitrate": 500000, "canbench_busoff_reset": False}
              and b.kw is f.log[-1][1] and b.vtrux_device_id == 0, f.log[-1])
        f = Fakes(use_raises=True)
        raised = None
        with f as kb:
            try:
                kb.pcan_witness_bus(bitrate=500000)
            except Exception as e:      # noqa: BLE001
                raised = e
        check("pcan_witness_bus: the pin refuses -> raises, and nothing is opened",
              raised is not None and "Pcan" not in f.kinds(), (raised, f.log))
    case("pcan_witness_bus: opens", pcan_witness)

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
                           ("innomaker_bus", {"role": "canmain", "bitrate": 500000}),
                           ("pcan_bus", {"bitrate": 500000}),
                           ("pcan_witness_bus", {"bitrate": 500000})):
            def go(label=label, holder=holder, opener=opener, kw=kw):
                f = Fakes(holder=holder)
                raised = None
                with f as kb:
                    try:
                        getattr(kb, opener)(**kw)
                    except kb.BenchNotHeld as e:
                        raised = e
                k = f.kinds()
                check("guard: %s, %s refuses (BenchNotHeld) before canctl, the fixes or an open (nothing but the pin "
                      "loaded before it)" % (label, opener),
                      raised is not None and "lock" in k and set(k) - {"use"} == {"lock"}, (raised, f.log))
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
# The bench's root operations and CAN sockets too (2026-11-19): with only python-can tripwired, a mutant
# that broke the guard let the real bench_layer run the real `sudo -n canctl up kvaser` and `canctl mode
# canmain libusb` from this child, with no lock held, during other sessions' holds.
import socket, subprocess
ROOT_CMDS = ("sudo", "canctl", "ip")
def _argv0s(a, kw):
    args = a[0] if a else kw.get("args")
    if isinstance(args, (str, bytes)):
        args = str(args).split()
    return [os.path.basename(str(x)) for x in (args or [])[:3]]
def guard_proc(name, real):
    def f(*a, **kw):
        if any(x in ROOT_CMDS for x in _argv0s(a, kw)):
            trips.append("%s %s" % (name, " ".join(_argv0s(a, kw))))
            raise RuntimeError("TRIPWIRE: %s %s reached" % (name, _argv0s(a, kw)))
        return real(*a, **kw)
    return f
for _n in ("run", "Popen", "call", "check_call", "check_output"):
    setattr(subprocess, _n, guard_proc("subprocess." + _n, getattr(subprocess, _n)))
_real_socket = socket.socket
class _GuardSocket(_real_socket):
    def __init__(self, family=-1, *a, **kw):
        if family == getattr(socket, "AF_CAN", 29):
            trips.append("socket AF_CAN")
            raise RuntimeError("TRIPWIRE: a CAN socket reached")
        super().__init__(family, *a, **kw)
socket.socket = _GuardSocket
import kvaser_bus as K
out = {"refused": {}, "trips": trips}
# a tool module with its own fake can, as test_e4_rawpost faked e4_device_calibration
tool = types.ModuleType("a_tool")
tool.can = types.SimpleNamespace(Bus=lambda **kw: None)
for name, call in (("kvaser_bus", lambda: K.kvaser_bus(channel=0, bitrate=500000)),
                   ("innomaker_bus", lambda: K.innomaker_bus("canmain", bitrate=500000)),
                   ("pcan_bus", lambda: K.pcan_bus(bitrate=500000)),
                   ("bus pcan", lambda: K.bus(interface="pcan", bitrate=500000)),
                   ("bus kvaser", lambda: K.bus(interface="kvaser", channel=0, bitrate=500000))):
    try:
        call()
        out["refused"][name] = None
    except K.BenchNotHeld as e:
        out["refused"][name] = "BenchNotHeld: %s" % e
    except Exception as e:
        out["refused"][name] = "OTHER %s: %s" % (type(e).__name__, e)
out["trips"] = list(trips)
# the tripwires themselves, shown to stand (each must trip, nothing reaches the bench)
selftest = []
for label, call in (("canctl", lambda: subprocess.run(["sudo", "-n", "/usr/local/sbin/canctl", "roles"])),
                    ("ip", lambda: subprocess.check_output(["ip", "-details", "-json", "link", "show", "kvaser"])),
                    ("socket", lambda: socket.socket(getattr(socket, "AF_CAN", 29), socket.SOCK_RAW, 1)),
                    ("a harmless command still runs", lambda: subprocess.run([sys.executable, "-c", "pass"]))):
    try:
        call()
        selftest.append((label, "ran"))
    except RuntimeError as e:
        selftest.append((label, "tripped" if "TRIPWIRE" in str(e) else "other"))
out["selftest"] = selftest
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
    check("guard (real): no tripwire on the real python-can, CANlib, canctl/sudo/ip or a CAN socket was reached",
          out["trips"] == [], out["trips"])
    check("guard (real): the child's tripwires stand: canctl, ip and a CAN socket trip, a harmless command runs",
          [tuple(x) for x in out.get("selftest", [])] == [("canctl", "tripped"), ("ip", "tripped"),
                                                          ("socket", "tripped"),
                                                          ("a harmless command still runs", "ran")],
          out.get("selftest"))
    print("      guard (real): %s" % (out["refused"].get("kvaser_bus") or "")[:160])


# kvaser_bus loaded by file, in a process with no folder holding gi_paths on sys.path, as the chain
# runner loads it through suite_gen_inhibit.kvaser_link_down (run gen-inhibit-bench_20261119T031110_ffdf
# stopped at its start identify on "No module named 'gi_paths'"). A stand-in vtrux_bench.bench_lock
# records the log the guard asks for, so nothing real is read.
CHILD_BYFILE = r"""
import importlib.util, json, os, sys, types
src = sys.argv[1]
on_path = [p for p in sys.path if os.path.isfile(os.path.join(p or os.getcwd(), "gi_paths.py"))]
asked = []
vb = types.ModuleType("vtrux_bench")
bl = types.ModuleType("vtrux_bench.bench_lock")
def read_state(path):
    asked.append(path)
    return {"holder": {"who": "planted"}}
bl.read_state = read_state
vb.bench_lock = bl
sys.modules["vtrux_bench"] = vb
sys.modules["vtrux_bench.bench_lock"] = bl
spec = importlib.util.spec_from_file_location("_suite_kvaser_bus", os.path.join(src, "kvaser_bus.py"))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
out = {"on_path": on_path, "gi_paths_before": "gi_paths" in sys.modules}
try:
    out["holder"] = K._lock_holder()
except Exception as e:
    out["holder"] = None
    out["error"] = "%s: %s" % (type(e).__name__, e)
out["asked"] = asked
print("CHILD " + json.dumps(out))
"""


def by_file():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    cwd = os.path.dirname(TOOLS)                       # gen-inhibit/: no gi_paths.py there
    r = subprocess.run([sys.executable, "-B", "-c", CHILD_BYFILE, SRC], env=env, cwd=cwd,
                       capture_output=True, text=True, timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("CHILD ")]
    if r.returncode != 0 or not line:
        check("by file: the child ran", False, (r.returncode, r.stdout[-300:], r.stderr[-600:]))
        return
    out = json.loads(line[0][6:])
    check("by file: the child had no folder holding gi_paths on sys.path and no gi_paths imported",
          out["on_path"] == [] and out["gi_paths_before"] is False, out)
    want = os.path.join(os.path.normpath(os.environ["REVERSE_IT_DIR"]), "firmware", "bench-log.jsonl")
    check("by file: _lock_holder reads the holder from the bench log, as the runner's kvaser_link_down needs",
          out["holder"] == {"who": "planted"}, out.get("error") or out)
    check("by file: the log asked for is REVERSE_IT_DIR's firmware/bench-log.jsonl",
          out["asked"] == [want], (out["asked"], want))


# ---------------------------------------------------------------- the scan

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


def bench_scan():
    """The pinned canbench.bench_scan (can-tools ec7cefe; the gen-inhibit reviewer's ruling of 2026-11-19: the
    adapter-open scan both projects share, run from this test on gen-inhibit's tree)."""
    return load_real("bench_scan", {})


# Every bench_scan hit gen-inhibit allows: (path relative to gen-inhibit/, kind, the hit's why) -> (count, reason).
# Anything else bench_scan finds FAILS the scan, and an entry whose count no longer matches FAILS too. The
# reasons are for the gen-inhibit reviewer to rule on (merge request, 2026-11-19).
SCAN_ALLOWED = {
    # kvaser_bus: the layer's own opener
    ("tools/bench/kvaser_bus.py", "bus-open", "passes **kwargs (the interface may hide there)"): (2,
        "kvaser_bus is the opener: the CANlib Kvaser off Linux (can.Bus(interface='kvaser', **kw); Linux opens by "
        "bench_layer.open_bench) and bus()'s other interfaces (can.Bus(interface=iface, **kw), for the IXXAT "
        "callers below)"),
    ("tools/bench/kvaser_bus.py", "adapter-class", "names detect_available_configs"): (1,
        "detect(), off Linux only: CANlib's own channel list (on Linux the Kvaser is resolved by role)"),
    ("tools/bench/kvaser_bus.py", "adapter-class", "names CanbenchPcanBus"): (1,
        "pcan_witness_bus: the PCAN timing witness is canbench's CanbenchPcanBus on the role's device id (ruling (C), "
        "2026-11-13); bench_layer has no form of it"),
    # the implementor's runner
    ("tools/vehicle_runner.py", "bus-open", "opens interface 'socketcan' directly, outside the bench-role layer"): (1,
        "the implementor's vehicle_runner, its truck path (a car's adapter, not the bench); refused on a bench host "
        "(1bb455f6, 07075d05)"),
    ("tools/vehicle_runner.py", "adapter-class", "imports detect_all_can_interfaces"): (1,
        "vehicle_runner's truck path, --interface auto; refused on a bench host"),
    ("tools/vehicle_runner.py", "adapter-class", "names detect_all_can_interfaces"): (1,
        "vehicle_runner's truck path, --interface auto; refused on a bench host"),
    ("tools/vehicle_runner.py", "bus-open", "passes **kwargs (the interface may hide there)"): (3,
        "vehicle_runner's truck path, an explicit --interface; refused on a bench host"),
    # adapters with no canctl role: for the reviewer's ruling
    ("tools/bench/e4_rig_calibration.py", "bus-open", "opens interface 'ixxat' directly, outside the bench-role layer"): (2,
        "the IXXAT, a third adapter with no canctl role; it left the bench on 2026-10-04, and these topology "
        "phases refuse when it is absent"),
    ("tools/bench/e4_rig_calibration.py", "adapter-class", "names detect_available_configs"): (1,
        "the topology phase's check that the IXXAT is present (refuses without it; the PCAN and the Kvaser open by role)"),
    ("tools/bench/wican_txabort_bench.py", "bus-open", "opens interface 'ixxat' directly, outside the bench-role layer"): (1,
        "the IXXAT as an optional second witness (--no-ixxat skips it; a failed open is recorded and the run goes on)"),
    ("tools/bench/diag_build_page_check.py", "adapter-class", "names detect_available_configs"): (1,
        "detection of the IXXAT only (detect_available_configs(['ixxat'])), an optional third adapter; the PCAN and the "
        "Kvaser go by role"),
    ("tools/bench/e4_device_calibration.py", "bus-open", "opens interface 'slcan' directly, outside the bench-role layer"): (1,
        "the separate-adapter filler, an slcan device named on the command line (--flood-slcan), only when asked; no "
        "canctl role"),
    # tests: their own fakes and refusal cases
    ("tools/test_gi_vbus.py", "bus-open", "passes **kwargs (the interface may hide there)"): (1,
        "gi_vbus's udp_multicast bus, virtual only"),
    ("tools/test_vehicle_runner_roles.py", "pcan-channel",
     "names PCAN channel PCAN_USBBUS1 (the n-th PCAN found, not the role pcanfd)"): (1,
        "the implementor's test: a --role run asked for channel PCAN_USBBUS1 must be refused"),
    ("tools/bench/test_diag_build_open.py", "pcan-channel",
     "names PCAN channel PCAN_USBBUS1 (the n-th PCAN found, not the role pcanfd)"): (1,
        "its fake detection lists the PCAN, to show the script no longer asks it"),
    ("tools/bench/test_diag_build_open.py", "adapter-class", "names detect_available_configs"): (2,
        "its fake of can.detect_available_configs"),
    ("tools/bench/test_jammer_witnesses.py", "pcan-channel",
     "names PCAN channel PCAN_USBBUS1 (the n-th PCAN found, not the role pcanfd)"): (10,
        "its fake PCAN buses' channel_info, as PCAN-Basic names the channel found"),
    ("tools/bench/test_jammer_witnesses.py", "pcan-channel",
     "names PCAN channel PCAN_USBBUS2 (the n-th PCAN found, not the role pcanfd)"): (1,
        "its fake PCAN buses' channel_info (a second channel, refused)"),
    ("tools/bench/test_kvaser_bus.py", "adapter-class", "names CanbenchPcanBus"): (2,
        "its fake pinned canbench.live.pcan_bus.CanbenchPcanBus"),
    ("tools/bench/test_kvaser_bus.py", "adapter-class", "names SocketcanHwBus"): (2,
        "its fake pinned canbench.live.socketcan_hw.SocketcanHwBus"),
    ("tools/bench/test_kvaser_bus.py", "pcan-channel",
     "names PCAN channel PCAN_USBBUS1 (the n-th PCAN found, not the role pcanfd)"): (1,
        "its refusal cases ask the openers by channel and must be refused"),
    ("tools/bench/test_serial_noreset.py", "bus-open", "opens interface 'slcan' directly, outside the bench-role layer"): (1,
        "its fake can.Bus for the slcan serial-reset check: nothing is opened"),
    ("tools/bench/test_x2_jam_cases.py", "bus-open", "passes no interface= (python-can then reads its config)"): (5,
        "Bus(api) is the test's own stand-in class for pcan_reopen, not python-can's"),
}


def scan_hits(BS):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return BS.scan_paths(BS.py_files(GI, skip_dirs=("__pycache__", ".git", "firmware")), GI)


def judge_hits(hits, allowed=None):
    """(bad hits, stale or miscounted allow entries) for the hits against the allow list."""
    allowed = SCAN_ALLOWED if allowed is None else allowed
    counts = {}
    bad = []
    for h in hits:
        key = (h.path, h.kind, h.why)
        if key in allowed:
            counts[key] = counts.get(key, 0) + 1
        else:
            bad.append(str(h))
    stale = ["%s [%s] %s: allowed %d, found %d" % (k[0], k[1], k[2], n, counts.get(k, 0))
             for k, (n, _why) in sorted(allowed.items()) if counts.get(k, 0) != n]
    return bad, stale


def files():
    for dp, dn, fn in os.walk(GI):
        rel = os.path.relpath(dp, GI).replace(os.sep, "/")
        dn[:] = sorted(d for d in dn if d not in ("__pycache__", ".git") and not (rel == "." and d == "firmware"))
        for f in sorted(fn):
            if f.endswith(".py"):
                yield os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), GI).replace(os.sep, "/")


def scan():
    BS = bench_scan()
    check("scan: canbench's bench_scan self_check passes (each kind caught, clean snippets clean)",
          BS.self_check() == [], BS.self_check())
    hits = scan_hits(BS)
    bad, stale = judge_hits(hits)
    check("scan: every bench_scan hit on gen-inhibit's tree is in SCAN_ALLOWED", not bad, bad)
    check("scan: every SCAN_ALLOWED entry matches its count (none stale)", not stale, stale)
    n_files = openers = 0
    fake_bad = []
    import warnings
    for path, rel in files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            fake_can = fakes_can_without_kvaser_bus(ast.parse(text, path))
        if fake_can and rel not in FAKE_CAN_EXEMPT:
            fake_bad.append("%s replaces a module's can (lines %s) and fakes no kvaser_bus" % (rel, fake_can))
        if rel in FAKE_CAN_EXEMPT and not fake_can:
            fake_bad.append("%s is in FAKE_CAN_EXEMPT but no longer needs it" % rel)
        n_files += 1
        openers += text.count("kvaser_bus(") + text.count("innomaker_bus(") + text.count("pcan_bus(")
    check("scan: read the harness (over 100 files, over 30 opens through kvaser_bus)",
          n_files > 100 and openers > 30, (n_files, openers))
    check("scan: no test fakes a tool's can without faking kvaser_bus", not fake_bad, fake_bad)
    print("      %d files, %d calls of kvaser_bus/innomaker_bus/pcan_bus, %d bench_scan hits, all allowed: %s"
          % (n_files, openers, len(hits), not bad))


FAKE_CAN_SNIPPETS = [
    ("a tool's can replaced, no kvaser_bus fake", "E4.can, E4.gi = fake_can, gi\n", True),
    ("setattr(tool, 'can', ...), no kvaser_bus fake", 'setattr(E4, "can", fake_can)\n', True),
    ("kvaser_bus only in a comment", "# kvaser_bus\nE4.can = fake_can\n", True),
    ("a tool's can replaced, kvaser_bus faked", 'E4.can = fake_can\nsys.modules["kvaser_bus"] = kb\n', False),
    ("self.can, an instance's own", "self.can = None\n", False),
]
# Real files with one of this topic's sites reverted: (file, old, new). bench_scan must find a hit in each that the
# allow list does not cover.
REVERTED = [
    ("interlock_bench.py", "kvaser_bus(", 'can.Bus(interface="kvaser", '),
    ("x2_jammer.py", 'inno = innomaker_bus("canchg", ', 'inno = can.Bus(interface="gs_usb", index=0, '),
    ("x2_jammer.py", '        step, inno = AP.up_open("canchg")\n',
     '        step, inno = None, can.Bus(interface="socketcan", channel="canchg")\n'),
    ("can2_ack_probe.py", "        bus = layer().open_bench(role, BITRATE, mode=\"socketcan\", restore=False)\n",
     "        bus = can.Bus(interface=\"socketcan\", channel=role)\n"),
    ("bench_census.py", 'return innomaker_bus("canmain", ', 'return can.Bus(interface="gs_usb", channel="can0", index=0, '),
    ("bench_link_check.py", "bus = kvaser_bus_open(**cfg)", "bus = can.Bus(**cfg)"),
    ("diag_build_page_check.py", "buses[name] = open_bus(interface=name,", "buses[name] = can.Bus(interface=name,"),
    ("interlock_bench.py", "pc = pcan_bus(", 'pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", '),
    ("bench_witness.py", "self.bus = opener(bitrate=BITRATE)", 'self.bus = opener(channel="PCAN_USBBUS1", bitrate=BITRATE)'),
    ("test_only_inert.py", "tx = pcan_bus(bitrate=500000)    # PCAN first",
     'tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)    # PCAN first'),
    ("wican_trip_bench.py", "pcan = pcan_bus(", 'pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"], '),
    ("kvaser_bus.py", '    b = _call(RoleError, "open_bench %s" % PCAN_ROLE, bl.open_bench, PCAN_ROLE, bitrate, mode="pcan", **kw)\n',
     '    import can\n    b = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=bitrate, **kw)\n'),
]


def scan_can_fail():
    BS = bench_scan()
    for label, text, want in FAKE_CAN_SNIPPETS:
        got = fakes_can_without_kvaser_bus(ast.parse(text))
        check("scan %s: %s" % ("can fail" if want else "passes", label), bool(got) == want, got)
    text = open(os.path.join(HERE, "test_e4_rawpost.py"), encoding="utf-8").read()
    old = 'fake_kb = types.ModuleType("kvaser_bus")'
    cut = text.replace('"kvaser_bus"', "KB")       # every literal gone: the fake no longer named
    check("scan can fail: test_e4_rawpost.py without its fake kvaser_bus",
          text.count(old) == 1 and bool(fakes_can_without_kvaser_bus(ast.parse(cut))),
          fakes_can_without_kvaser_bus(ast.parse(cut)))
    for name, old, new in REVERTED:
        text = open(os.path.join(HERE, name), encoding="utf-8").read()
        n = text.count(old)
        if n < 1:
            check("scan can fail: %s reverted (%s)" % (name, old.strip()), False, "edit matched %d times" % n)
            continue
        rel = "tools/bench/" + name
        hits = BS.scan_source(text.replace(old, new, 1), rel)
        base = BS.scan_source(text, rel)
        bad, _ = judge_hits(hits)
        check("scan can fail: %s with %r put back as %r -> a hit outside SCAN_ALLOWED" % (name, old.strip()[:50],
                                                                                      new.strip()[:50]),
              bool(bad) or len(hits) > len(base), (bad, len(hits), len(base)))
    fake = [BS.Hit("tools/bench/new_tool.py", 3, "bus-open", "opens interface 'kvaser' directly, outside the bench-role layer")]
    bad, stale = judge_hits(fake, {})
    check("scan can fail: a hit in a file not allowed -> FAIL", bad == [str(fake[0])], bad)
    bad, stale = judge_hits([], {("tools/x.py", "bus-open", "y"): (1, "z")})
    check("scan can fail: an allow entry with no hit left -> stale", len(stale) == 1, stale)
    k = ("tools/bench/kvaser_bus.py", "adapter-class", "names CanbenchPcanBus")
    bad, stale = judge_hits([BS.Hit(k[0], 1, k[1], k[2])] * 2, {k: (1, "r")})
    check("scan can fail: one hit more than allowed -> miscounted", len(stale) == 1, stale)


# ---------------------------------------------------------------- mutants of kvaser_bus.py

MUTANTS = [
    (None, None, None),
    # bus errors only on kvaser_usb (reviewer, 2026-11-18)
    ("kvaser_bus applies no fixes", "        return _socketcan_bus(**kw)\n    fixes()\n",
     "        return _socketcan_bus(**kw)\n"),
    # the bench guard (reviewer, 2026-11-12)
    ("kvaser_bus opens without the guard", "def kvaser_bus(**kw):\n    require_bench()\n", "def kvaser_bus(**kw):\n"),
    ("the guard checks after the fixes", "def kvaser_bus(**kw):\n    require_bench()\n    if backend()",
     "def kvaser_bus(**kw):\n    fixes()\n    require_bench()\n    if backend()"),
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
    ("the fixes apply on every open", "    if _fixes is None:\n", "    if True:\n"),
    ("bus() opens the Kvaser straight", '    if iface == "kvaser":\n        return kvaser_bus(**kw)\n', ""),
    ("bus() opens gs_usb straight (by its caller's index)", '    if iface == "gs_usb":\n        raise TypeError(',
     '    if False:\n        raise TypeError('),
    ("bus() drops the caller's arguments", "    return can.Bus(interface=iface, **kw)\n",
     "    return can.Bus(interface=iface)\n"),
    # the PCAN witness (2026-11-13, ruling (C))
    ("pcan_witness_bus resets on bus-off", "CanbenchPcanBus(canbench_busoff_reset=False, device_id=",
     "CanbenchPcanBus(device_id="),
    # the PCAN by canctl role (the user's ruling, 2026-11-18)
    ("pcan witness: the device id not passed", "CanbenchPcanBus(canbench_busoff_reset=False, device_id=r[\"device_id\"], ",
     "CanbenchPcanBus(canbench_busoff_reset=False, "),
    ("pcan: the open not recorded", '    record({"event": "open", "backend": "pcan", ', '    ({"event": "open", "backend": "pcan", '),
    ("pcan: the char device not on the bus", '    b.vtrux_chardev = r["chardev"]\n', ""),
    ("bus() opens pcan straight", '    if iface == "pcan":\n        return pcan_bus(**kw)\n', ""),
    # the Kvaser on Linux through SocketCAN (gen-inhibit reviewer, 2026-11-18, ruling (a))
    ("Linux keeps CANlib", '    return "socketcan" if (platform or PLATFORM).startswith("linux") else "canlib"',
     '    return "canlib"'),
    ("SocketCAN everywhere", '    return "socketcan" if (platform or PLATFORM).startswith("linux") else "canlib"',
     '    return "socketcan"'),
    ("socketcan: silent mode dropped without a word", "    if driver_mode is not True:\n", "    if False:\n"),
    ("socketcan: CANlib keywords dropped without a word", "    if other:\n        raise TypeError(",
     "    kw = {k: v for k, v in kw.items() if k in SOCKETCAN_KW}\n    if False:\n        raise TypeError("),
    # canbench's reader (can-tools 5952c0c; gen-inhibit reviewer, 2026-11-18)
    ("socketcan: no stamp check (check_frames 0)", "CHECK_FRAMES = 20\n", "CHECK_FRAMES = 0\n"),
    ("socketcan: host stamps pass the check (gap 0)", "HOST_GAP_S = 2.0\n", "HOST_GAP_S = 0.0\n"),
    ("socketcan: the stamp check not recorded at the close",
     '        record({"event": "stamps", "backend": "socketcan", "channel": NETDEV, "stamp_stats": stamps})\n', ""),
    ("socketcan: the error mask not checked", "    if mask != ERR_MASK:\n", "    if False:\n"),
    ("socketcan: the mask's bus left open", "        _open.append(b)\n        _close(b, b.shutdown)\n", ""),
    ("socketcan: the link left up at the close", "        if not _open:\n            ev = ", "        if False:\n            ev = "),
    ("socketcan: the link down at the first close", "        if not _open:\n            ev = ", "        if True:\n            ev = "),
    ("socketcan: the close not hooked", "    b.shutdown = lambda: _close(b, shut)\n", ""),
    ("socketcan: the backend not recorded", '    record({"event": "open", "backend": "socketcan", ', '    record({"event": "open", '),
    ("canlib: the open not recorded", '    record({"event": "open", "backend": "canlib", "channel_asked": kw.get("channel"),\n',
     '    ({"event": "open", "backend": "canlib", "channel_asked": kw.get("channel"),\n'),
    ("records not written to GI_RUNS_DIR", "    if d:\n        with open(", "    if False:\n        with open("),
    # the Innomakers by canctl role (the user's ruling and the reviewer's conditions, 2026-11-18)
    ("innomaker: any role passes", "    if role not in INNOMAKERS:\n", "    if False:\n"),
    ("innomaker: the open not recorded", '    record({"event": "open", "backend": "gs_usb", ', '    ({"event": "open", "backend": "gs_usb", '),
    ("the pin tool is not the project's", 'PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin.py")\n',
     'PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin_missing.py")\n'),
    # canbench's bench_roles (can-tools 5220d37; the gen-inhibit reviewer, 2026-11-18)
    ("the fixes are not taken from the pin", '    _pin_tool().use()\n    return importlib.import_module("canbench.live.linux_fixes")',
     '    return importlib.import_module("canbench.live.linux_fixes")'),
    ("pcan: a role in another mode passes", '    if res.mode != "pcan":\n', "    if False:\n"),
    ("pcan: no char device passes", '    if not chardev or "/" in chardev:\n', "    if False:\n"),
    ("socketcan: linuxcan's leaf passes", '    if row is not None and row.mode == "linuxcan":\n', "    if False:\n"),
    ("socketcan: another netdev passes", '    if res.mode != "socketcan" or res.netdev != NETDEV:\n', "    if False:\n"),
    ("detect: a refused Kvaser role found on Linux",
     "        except (RoleError, KvaserLinkError):\n            return None\n",
     "        except (RoleError, KvaserLinkError):\n            pass\n"),
    # innomaker_held (condition 5, 2026-11-18)
    ("held: an open outside the block passes", "        if not self.held:\n            raise InnomakerError(", "        if False:\n            raise InnomakerError("),
    ("held: a second shutdown closes again", "        if getattr(b, \"_vtrux_closed\", False):\n            return None\n        b._vtrux_closed = True\n        ev = ",
     "        ev = "),
    ("held: the close not recorded", "            raise\n        record(ev)\n        return None\n", "            raise\n        return None\n"),
    ("held: exit leaves a bus open", "        for b in self.buses:\n            try:\n                d = getattr(", "        for b in []:\n            try:\n                d = getattr("),
    ("held: exit hands back before the closes", "        failed = []\n        for b in self.buses:", "        failed = []\n        innomaker_restore(self.role)\n        for b in self.buses:"),
    ("held: a failed close at exit swallowed", "        if failed:\n            raise InnomakerError(", "        if False:\n            raise InnomakerError("),
    # on canbench's bench_layer (the gen-inhibit reviewer's ruling, 2026-11-19)
    ("bus_error: not bench_layer's (every error frame counts)", "    return layer().bus_error(b, msg)\n",
     "    return bool(msg.is_error_frame)\n"),
    ("bus_error_class: every class counts", "    return layer().bus_error_class(b, can_id)\n", "    return True\n"),
    ("the layer's guard not plugged in", "    _layer.set_guard(require_bench)\n", "    _layer.set_guard(lambda: None)\n"),
    ("bench_layer not taken from the pin",
     '        _pin_tool().use()\n        _layer = importlib.import_module("canbench.bench_layer")\n',
     '        _layer = importlib.import_module("canbench.bench_layer")\n'),
    ("bench_roles not taken from the pin",
     '        _pin_tool().use()\n        _roles = importlib.import_module("canbench.bench_roles")\n',
     '        _roles = importlib.import_module("canbench.bench_roles")\n'),
    ("canbench's refusal not re-raised as kvaser_bus's", '        raise err("%s: %s" % (what, e)) from e\n', "        raise\n"),
    ("pcan_witness_bus: picks not refused",
     '    _call(RoleError, "pcan_witness_bus", bl.refuse_picks, kw, "pcan_witness_bus")\n', ""),
    ("pcan_witness_bus: no guard", '    bl.guard("pcan_witness_bus")\n', ""),
    ("pcan: opens by channel PCAN_USBBUS1",
     '    b = _call(RoleError, "open_bench %s" % PCAN_ROLE, bl.open_bench, PCAN_ROLE, bitrate, mode="pcan", **kw)\n',
     '    import can\n    b = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=bitrate, **kw)\n'),
    ("socketcan: no restart-ms", 'hw_timestamps=True, restore=False, restart_ms=RESTART_MS,',
     'hw_timestamps=True, restore=False,'),
    ("socketcan: open_bench without hw_timestamps", 'mode="socketcan", hw_timestamps=True, restore=False,',
     'mode="socketcan", restore=False,'),
    ("socketcan: open_bench restores at the close", 'mode="socketcan", hw_timestamps=True, restore=False,',
     'mode="socketcan", hw_timestamps=True, restore=True,'),
    ("socketcan: the stamp check's frames not asked", "check_frames=CHECK_FRAMES, host_gap_s=HOST_GAP_S, **kw)",
     "host_gap_s=HOST_GAP_S, **kw)"),
    ("socketcan: a failed open leaves the link as found",
     "        try:\n            link_down()             # nothing open: not left ACKing, also when the link was found up\n",
     "        try:\n            pass\n"),
    ("socketcan: the leaf not asked before the open",
     "    kvaser_role()                   # RoleError / KvaserLinkError (leaf) before anything is changed\n", ""),
    ("socketcan: the close leaves the link up", '                ev["link"] = link_down()\n', '                ev["link"] = link()\n'),
    ("innomaker: handed over in socketcan mode",
     '    b = _call(InnomakerError, "open_bench %s" % role, bl.open_bench, role, bitrate, mode="libusb",\n',
     '    b = _call(InnomakerError, "open_bench %s" % role, bl.open_bench, role, bitrate, mode="socketcan",\n'),
    ("innomaker_bus: nothing put back at the close", "    return _innomaker_open(role, bitrate, kw, True)\n",
     "    return _innomaker_open(role, bitrate, kw, False)\n"),
    ("held: opens recorded as not held", "        b = _innomaker_open(self.role, bitrate, kw, False)\n",
     "        b = _innomaker_open(self.role, bitrate, kw, True)\n"),
    ("held: enter not handed to libusb",
     '        _call(InnomakerError, "to_libusb %s" % self.role, bl.to_libusb, self.role)\n', ""),
    ("held: exit not handed back",
     '            res = _call(InnomakerError, "to_socketcan %s" % self.role, bl.to_socketcan, self.role)\n',
     "            res = _resolve(self.role)\n"),
    ("held: exit hands back before the closes", "        failed = []\n        for b in self.buses:",
     "        failed = []\n        layer().to_socketcan(self.role)\n        for b in self.buses:"),
    ("held: exit does not mark it released", "        self.held = False\n        bl = layer()\n", "        bl = layer()\n"),
    ("held: the hand-back's read-back not recorded",
     '            ev["readback"] = "mode %s, netdev %s, link %s" % (res.mode, res.netdev, res.role.link)\n', ""),
    ("held: the hold not recorded",
     '        record({"event": "hold", "backend": "gs_usb", "role": self.role, "mode": "libusb"})\n', ""),
    ("innomaker: the close not recorded", "        record(ev)\n        return None\n    b.shutdown = close\n",
     "        return None\n    b.shutdown = close\n"),
    ("innomaker: a second shutdown closes again",
     '        if getattr(b, "_vtrux_closed", False):\n            return None\n        b._vtrux_closed = True\n'
     '        ev = {"event": "close", "backend": "gs_usb", "role": role, "held": not restore}\n',
     '        ev = {"event": "close", "backend": "gs_usb", "role": role, "held": not restore}\n'),
    ("innomaker: a failed close not recorded",
     '            ev["error"] = "%s: %s" % (type(e).__name__, e)\n            record(ev)\n            raise\n',
     "            raise\n"),
    ("innomaker: what was found not recorded",
     '            "found": (getattr(b, "bench_layer", None) or {}).get("found"),\n', ""),
    ("socketcan: what was found not recorded", '            "found": lay.get("found"), "err_mask": "0x%X" % mask,',
     '            "err_mask": "0x%X" % mask,'),
    # found up (the gen-inhibit reviewer's ruling of 2026-11-19)
    ("innomaker: a role found up passes to bench_layer", '    if row is not None and row.link == "up":\n',
     "    if False:\n"),
    ("innomaker_bus: found up not checked", '    _innomaker_not_up(role, "innomaker_bus")\n', ""),
    ("held: found up not checked at enter", '        _innomaker_not_up(self.role, "innomaker_held")\n', ""),
    ("innomaker: the found-up finding not recorded",
     '        record({"event": "refused", "backend": "gs_usb", "role": role, "what": what, "finding": "found up",\n'
     '                "netdev": row.netdev, "link": row.link})\n', ""),
    ("innomaker: the link read before the guard",
     '    bl.guard("%s %s" % (what, role))\n    row = _call(InnomakerError, role, bench_roles().roles).get(role)\n',
     '    row = _call(InnomakerError, role, bench_roles().roles).get(role)\n    bl.guard("%s %s" % (what, role))\n'),
    ("innomaker: canmain's link read for every role",
     "    row = _call(InnomakerError, role, bench_roles().roles).get(role)\n",
     '    row = _call(InnomakerError, role, bench_roles().roles).get("canmain")\n'),
    # the echo drain (the gen-inhibit reviewer's ruling (C), 2026-11-19)
    ("echo: the drain never started", "    bus.echo_drain = d\n    d.start()\n", "    bus.echo_drain = d\n"),
    ("echo: the drain reads nothing", "                m = self.bus.recv(timeout=DRAIN_RECV_S)\n",
     "                m = None\n                time.sleep(DRAIN_RECV_S)\n"),
    ("echo: echoes counted as other frames", '            elif getattr(m, "is_rx", True) is False:\n',
     "            elif False:\n"),
    ("echo: stop does not join", "        self._halt.set()\n        self.join(DRAIN_JOIN_S)\n",
     "        self._halt.set()\n"),
    ("echo: the queue not read after the stop is asked",
     "            if t_halt is not None and (m is None or time.perf_counter() - t_halt > DRAIN_SWEEP_MAX_S):\n",
     "            if t_halt is not None:\n"),
    ("echo: echoes == sent not compared",
     '"echoes_equal_sent": (None if sent is None else self.echoes == sent)}', '"echoes_equal_sent": True}'),
    ("echo: the counts not recorded", "        record(ev)\n        return ev\n", "        return ev\n"),
    ("echo: a read that raises not recorded", '        elif self.error:\n            ev["error"] = self.error\n', ""),
    ("echo: held's exit does not stop a running drain (not stopped before shutdown)",
     "                if d is not None and d.is_alive():\n                    d.stop()        # its reads end before the bus's handle is freed\n",
     ""),
    ("echo: the drain not attached to its bus", "    bus.echo_drain = d\n    d.start()\n", "    d.start()\n"),
    # gi_paths for the guard in a process without this folder on sys.path (run ffdf, 2026-11-19)
    ("by file: gi_paths only by a plain import (the run ffdf defect)",
     "        try:\n            import gi_paths as mod\n        except ImportError:\n",
     "        import gi_paths as mod\n        if False:\n"),
    ("by file: gi_paths looked for beside the tools folder, not beside kvaser_bus",
     'os.path.join(HERE, "gi_paths.py")', 'os.path.join(os.path.dirname(HERE), "gi_paths.py")'),
    # the PCAN's readers read on past a bus state (F6, 2026-11-19)
    ("pcan_bus: recv raises bus states, as in run cb08",
     '    return _read_on_bus_states(_pcan_record(b, r, bitrate, "canbench.bench_layer.open_bench"))',
     '    return _pcan_record(b, r, bitrate, "canbench.bench_layer.open_bench")'),
    ("pcan_bus: every error read as a bus state", "            if not PCAN_BUS_STATE.match(str(e)):\n                raise\n",
     ""),
    ("pcan_bus: bus states not kept",
     '            b.vtrux_states.append((time.perf_counter(), "%s: %s" % (type(e).__name__, e)))\n', ""),
    ("pcan_bus: a bus state's read is retried until it returns a frame",
     "            time.sleep(0.001)\n            return None\n    b.recv = recv",
     "            time.sleep(0.001)\n            return raw(timeout=timeout)\n    b.recv = recv"),
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
            # gi_paths sits beside kvaser_bus, as in the tree (by_file loads it from there)
            shutil.copy2(os.path.join(HERE, "gi_paths.py"), os.path.join(d, "bench", "gi_paths.py"))
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
    innomaker()
    echo()
    pcan()
    socketcan()
    case("bus errors", bus_errors)
    real_mask()
    pin()
    guard_real()
    by_file()
    if "--no-scan" not in argv:
        scan()
        scan_can_fail()
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
