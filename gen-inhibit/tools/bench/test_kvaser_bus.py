#!/usr/bin/env python3
"""Test kvaser_bus.py, the one way the bench harness opens the Kvaser and the Innomaker, and
scan gen-inhibit's tools for an open that goes round it (gen-inhibit reviewer, 2026-11-11).

  unit      against a fake can and a fake pinned canbench: kvaser_bus() and gs_usb_bus() load
            the pin (can_tools_pin.use()) and apply canbench.live.linux_fixes before the open,
            and open nothing when the pin or the fixes refuse; the fixes apply once a process;
            close_gs_usb() shuts the bus down, then releases its pyusb handle through canbench's
            release_gs_usb, also when the shutdown raises; bus() sends kvaser and gs_usb through
            the two openers, and anything else straight to can.Bus with its arguments;
            pcan_witness_bus() (2026-11-13) opens the pinned canbench's CanbenchPcanBus with
            the caller's arguments and its bus-off reset off, after the guard and the pin
  guard     (reviewer, 2026-11-12) require_bench() runs first: with the lock free, unreadable,
            held by another role, or held under a pid that is neither this process nor an
            ancestor, kvaser_bus(), gs_usb_bus(), pcan_witness_bus() and bus(interface="kvaser") raise BenchNotHeld
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
    "tools/bench/kvaser_bus.py": (3, "the opener itself (the SocketCAN open goes through canbench's SocketcanHwBus "
                                     "from 2026-11-18, which the scan does not flag)"),
    "tools/bench/test_x2_jam_cases.py": (5, "Bus(api) is the test's own stand-in class for pcan_reopen, not "
                                            "python-can's (on gen-inhibit/main from 4e81b9c2, 2026-11-16)"),
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
        pb = types.ModuleType("canbench.live.pcan_bus")

        class CanbenchPcanBus:
            def __init__(self, **kw):
                self.kw = kw
                log.append(("Pcan", kw))
        pb.CanbenchPcanBus = CanbenchPcanBus
        self.pinned = {"canbench": types.ModuleType("canbench"),
                       "canbench.live": types.ModuleType("canbench.live"),
                       "canbench.live.linux_fixes": lf, "canbench.live.receiver": rc,
                       "canbench.live.pcan_bus": pb}
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


class SockFakes(Fakes):
    """The Kvaser on Linux (ruling (a), 2026-11-18): Fakes on PLATFORM linux, with a made-up
    sysfs (netdev kvaser's device bound to `driver`, or no netdev with driver None), and a fake
    _run that plays canctl (up/down change the fake link; rc from canctl_rc) and `ip -details
    -json link show kvaser` (the fake link, with read_bitrate / read_lo / stuck_up to lie). The
    fake pinned canbench.live.socketcan_hw.SocketcanHwBus (can-tools 5952c0c, 2026-11-18) logs
    ("HwBus", channel, kw), raises hw_raises if set, and its socket reads back err_filter as its
    CAN_RAW_ERR_FILTER; stamp_stats() gives `stamps`."""

    def __init__(self, driver="kvaser_usb", canctl_rc=0, read_bitrate=None, read_lo=False, stuck_up=False,
                 err_filter=0x1FFFFFFF, runs_dir=None, canctl_applies=False, hw_raises=None,
                 stamps=None, **kw):
        # canctl_applies: canctl changes the link even when it exits non-zero (it failed after
        # its own `ip link set up`)
        Fakes.__init__(self, **kw)
        self.canctl_applies = canctl_applies
        self.driver, self.canctl_rc, self.read_bitrate = driver, canctl_rc, read_bitrate
        self.read_lo, self.stuck_up, self.runs_dir = read_lo, stuck_up, runs_dir
        self.linkst = {"up": False, "bitrate": None}
        self.root = gi_paths.scratch("kvaser-bus-sysfs-%d" % id(self))
        gi_paths.remove_tree(self.root)
        if driver is not None:
            os.makedirs(os.path.join(self.root, "drivers", driver))
            os.makedirs(os.path.join(self.root, "kvaser", "device"))
            os.symlink(os.path.join(self.root, "drivers", driver),
                       os.path.join(self.root, "kvaser", "device", "driver"))
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

    def run(self, argv):
        if argv[:3] == ["sudo", "-n", "/usr/local/sbin/canctl"]:
            args = argv[3:]
            self.log.append(("canctl",) + tuple(args))
            if self.canctl_rc == 0 or self.canctl_applies:
                if args[0] == "up":
                    self.linkst = {"up": True, "bitrate": int(args[2])}
                elif args[0] == "down" and not self.stuck_up:
                    self.linkst = dict(self.linkst, up=False)
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
        self.log.append(("run?",) + tuple(argv))
        return 127, "not faked"

    def __enter__(self):
        kb = Fakes.__enter__(self)
        kb.PLATFORM = "linux"
        kb.SYSFS_NET = self.root
        kb._run = self.run
        kb._open[:] = []
        self.saved_runs = os.environ.get("GI_RUNS_DIR")
        if self.runs_dir:
            os.environ["GI_RUNS_DIR"] = self.runs_dir
        else:
            os.environ.pop("GI_RUNS_DIR", None)
        return kb

    def __exit__(self, *exc):
        if self.saved_runs is None:
            os.environ.pop("GI_RUNS_DIR", None)
        else:
            os.environ["GI_RUNS_DIR"] = self.saved_runs
        gi_paths.remove_tree(self.root)
        return Fakes.__exit__(self, *exc)

    def canctls(self):
        return [e[1:] for e in self.log if e[0] == "canctl"]


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
                check("socketcan: the lock checked first; no CANlib fixes; canctl up kvaser at the "
                      "asked bitrate with restart-ms, read back, then the pin and the open",
                      [k for k in f.kinds()] == ["lock", "canctl", "ip", "use", "HwBus", "getsockopt"]
                      and f.canctls() == [("up", "kvaser", "500000", "restart-ms", "100")], f.log)
                check("socketcan: the pinned canbench's SocketcanHwBus on netdev kvaser, its stamp check "
                      "on 20 data frames, 2 s from host time",
                      opened and opened[0][1:] == ("kvaser", {"check_frames": 20, "host_gap_s": 2.0}), opened)
                check("socketcan: the error mask read back is CAN_RAW_ERR_FILTER on SOL_CAN_RAW",
                      ("getsockopt", 101, 2) in f.log, f.log)
                check("socketcan: the bus says its backend", getattr(b, "vtrux_backend", None) == "socketcan")
                del f.log[:]
                b.shutdown()
                check("socketcan: shutdown shuts the bus, then canctl down kvaser, read back",
                      f.kinds() == ["shutdown", "canctl", "ip"] and f.canctls() == [("down", "kvaser")]
                      and f.linkst["up"] is False, f.log)
            with open(os.path.join(runs, "kvaser_opens.jsonl"), encoding="ascii") as fh:
                evs = [json.loads(x) for x in fh]
            check("socketcan: the open, the stamp check and the close recorded in GI_RUNS_DIR's "
                  "kvaser_opens.jsonl, naming the backend, the channel asked and opened, the bitrate, the "
                  "driver, the link, the mask and the reader",
                  len(evs) == 3 and evs[0]["event"] == "open"
                  and evs[0]["reader"] == "canbench.live.socketcan_hw.SocketcanHwBus"
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
            ("the Kvaser on linuxcan's leaf", lambda: SockFakes(driver="leaf"), std, "KvaserLinkError"),
            ("no netdev kvaser", lambda: SockFakes(driver=None), std, "KvaserLinkError"),
            ("driver_mode=False (CANlib's silent mode)", SockFakes, dict(std, driver_mode=False), "KvaserLinkError"),
            ("a CANlib-only keyword (single_handle)", SockFakes, dict(std, single_handle=True), "TypeError"),
            ("canctl up refused (rc 2)", lambda: SockFakes(canctl_rc=2), std, "KvaserLinkError"),
            ("canctl up failing (rc 3) after the link came up", lambda: SockFakes(canctl_rc=3, canctl_applies=True),
             std, "KvaserLinkError"),
            ("the link read back at another bitrate", lambda: SockFakes(read_bitrate=250000), std, "KvaserLinkError"),
            ("the link read back listen-only", lambda: SockFakes(read_lo=True), std, "KvaserLinkError"),
            ("the reader's open raising after the link came up",
             lambda: SockFakes(hw_raises=OSError("bind failed")), std, "OSError")):
        # the reader's own open raising is past the open call: what matters is the link down
        case("socketcan refuses: " + label, refuses(label, fk, kw, exc, opened_none=(exc != "OSError")))
    for label, fk in (("the Kvaser on linuxcan's leaf", lambda: SockFakes(driver="leaf")),
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
                                ("leaf", lambda: SockFakes(driver="leaf"), None),
                                ("no netdev", lambda: SockFakes(driver=None), None)):
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
    sock, canlib, pcan = (types.SimpleNamespace(vtrux_backend="socketcan"),
                          types.SimpleNamespace(vtrux_backend="canlib"), types.SimpleNamespace())

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
        check("bus errors: %s alone does not count on SocketCAN" % name, kb.bus_error(sock, err(cid)) is False,
              hex(cid))
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

    def pcan_witness():
        # 2026-11-13, the reviewer's ruling (C): the PCAN as the session's timing witness
        f = Fakes()
        with f as kb:
            b = kb.pcan_witness_bus(channel="PCAN_USBBUS1", bitrate=500000)
        check("pcan_witness_bus: the lock checked first, then the pin loaded, then the pinned CanbenchPcanBus",
              f.kinds() == ["lock", "use", "Pcan"], f.log)
        check("pcan_witness_bus: the caller's arguments, and the bus-off reset off",
              f.log[-1][1] == {"channel": "PCAN_USBBUS1", "bitrate": 500000, "canbench_busoff_reset": False}
              and b.kw is f.log[-1][1], f.log[-1])
        f = Fakes(use_raises=True)
        raised = None
        with f as kb:
            try:
                kb.pcan_witness_bus(channel="PCAN_USBBUS1", bitrate=500000)
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
                           ("gs_usb_bus", {"channel": "can0", "index": 0, "bitrate": 500000}),
                           ("pcan_witness_bus", {"channel": "PCAN_USBBUS1", "bitrate": 500000})):
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
    # bus errors only on kvaser_usb (reviewer, 2026-11-18)
    ("bus errors: controller state counts", "| CAN_ERR_ACK | CAN_ERR_BUSERROR)",
     "| CAN_ERR_ACK | CAN_ERR_BUSERROR | CAN_ERR_CRTL)"),
    ("bus errors: RESTARTED counts", "| CAN_ERR_ACK | CAN_ERR_BUSERROR)",
     "| CAN_ERR_ACK | CAN_ERR_BUSERROR | CAN_ERR_RESTARTED)"),
    ("bus errors: BUSOFF counts", "| CAN_ERR_ACK | CAN_ERR_BUSERROR)",
     "| CAN_ERR_ACK | CAN_ERR_BUSERROR | CAN_ERR_BUSOFF)"),
    ("bus errors: PROT and BUSERROR are not bus errors",
     "BUS_ERROR_CLASSES = (CAN_ERR_TX_TIMEOUT | CAN_ERR_LOSTARB | CAN_ERR_PROT | CAN_ERR_TRX\n"
     "                     | CAN_ERR_ACK | CAN_ERR_BUSERROR)",
     "BUS_ERROR_CLASSES = (CAN_ERR_TX_TIMEOUT | CAN_ERR_LOSTARB | CAN_ERR_TRX\n"
     "                     | CAN_ERR_ACK)"),
    ("bus errors: ACK is not one", "| CAN_ERR_ACK | CAN_ERR_BUSERROR)", "| CAN_ERR_BUSERROR)"),
    ("bus errors: CNT excludes a frame", "    return bool(can_id & BUS_ERROR_CLASSES)\n",
     "    return bool(can_id & BUS_ERROR_CLASSES) and not can_id & CAN_ERR_CNT\n"),
    ("bus errors: every SocketCAN error frame counts", "    return bool(can_id & BUS_ERROR_CLASSES)\n",
     "    return True\n"),
    ("bus errors: filtered on every bus but CANlib", '    if getattr(b, "vtrux_backend", None) != "socketcan":\n',
     '    if getattr(b, "vtrux_backend", None) == "canlib":\n'),
    ("bus errors: a data frame can count", "    return bool(msg.is_error_frame) and bus_error_class(",
     "    return bus_error_class("),
    ("kvaser_bus applies no fixes", "        return _socketcan_bus(**kw)\n    fixes()\n",
     "        return _socketcan_bus(**kw)\n"),
    ("gs_usb_bus applies no fixes", "def gs_usb_bus(**kw):\n    require_bench()\n    fixes()\n",
     "def gs_usb_bus(**kw):\n    require_bench()\n"),
    # the bench guard (reviewer, 2026-11-12)
    ("kvaser_bus opens without the guard", "def kvaser_bus(**kw):\n    require_bench()\n", "def kvaser_bus(**kw):\n"),
    ("gs_usb_bus opens without the guard", "def gs_usb_bus(**kw):\n    require_bench()\n", "def gs_usb_bus(**kw):\n"),
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
    # the PCAN witness (2026-11-13, ruling (C))
    ("pcan_witness_bus opens without the guard",
     '    require_bench()\n    _pin_tool().use()\n    mod = importlib.import_module("canbench.live.pcan_bus")\n',
     '    _pin_tool().use()\n    mod = importlib.import_module("canbench.live.pcan_bus")\n'),
    ("pcan_witness_bus resets on bus-off", "CanbenchPcanBus(canbench_busoff_reset=False, **kw)",
     "CanbenchPcanBus(**kw)"),
    # the Kvaser on Linux through SocketCAN (gen-inhibit reviewer, 2026-11-18, ruling (a))
    ("Linux keeps CANlib", '    return "socketcan" if (platform or PLATFORM).startswith("linux") else "canlib"',
     '    return "canlib"'),
    ("SocketCAN everywhere", '    return "socketcan" if (platform or PLATFORM).startswith("linux") else "canlib"',
     '    return "socketcan"'),
    ("socketcan: any driver opens", "    if drv != DRIVER:\n        raise KvaserLinkError(\"the Kvaser is not on",
     "    if False:\n        raise KvaserLinkError(\"the Kvaser is not on"),
    ("socketcan: no netdev reads as kvaser_usb", "    if not os.path.exists(p):\n        return None\n",
     "    if not os.path.exists(p):\n        return DRIVER\n"),
    ("socketcan: silent mode dropped without a word", "    if driver_mode is not True:\n", "    if False:\n"),
    ("socketcan: CANlib keywords dropped without a word", "    if other:\n        raise TypeError(",
     "    kw = {k: v for k, v in kw.items() if k in SOCKETCAN_KW}\n    if False:\n        raise TypeError("),
    ("socketcan: the link not brought up", "    got = link_up(bitrate)\n", "    got = link()\n"),
    ("socketcan: canctl's refusal ignored", '    if rc != 0:\n        _canctl("down", NETDEV)         # canctl may have failed',
     '    if False:\n        _canctl("down", NETDEV)         # canctl may have failed'),
    ("socketcan: the bitrate not read back", ' or got["bitrate"] != bitrate or got["listen_only"]:', ' or got["listen_only"]:'),
    ("socketcan: listen-only not read back", ' or got["bitrate"] != bitrate or got["listen_only"]:', ' or got["bitrate"] != bitrate:'),
    ("socketcan: a failed canctl up left up", '        _canctl("down", NETDEV)         # canctl may have failed',
     '        pass         # canctl may have failed'),
    ("socketcan: a bad read-back left up", '        _canctl("down", NETDEV)         # not left ACKing', '        pass         # not left ACKing'),
    ("socketcan: no restart-ms", 'args = ("up", NETDEV, str(bitrate), "restart-ms", str(RESTART_MS))',
     'args = ("up", NETDEV, str(bitrate))'),
    # canbench's reader (can-tools 5952c0c; gen-inhibit reviewer, 2026-11-18)
    ("socketcan: no stamp check (check_frames 0)", "CHECK_FRAMES = 20\n", "CHECK_FRAMES = 0\n"),
    ("socketcan: host stamps pass the check (gap 0)", "HOST_GAP_S = 2.0\n", "HOST_GAP_S = 0.0\n"),
    ("socketcan: the reader not taken from the pin",
     "        _pin_tool().use()\n        hw = importlib.import_module(\"canbench.live.socketcan_hw\")\n",
     "        hw = importlib.import_module(\"canbench.live.socketcan_hw\")\n"),
    ("socketcan: a failed reader open left the link up",
     "        _canctl(\"down\", NETDEV)         # nothing open: not left ACKing\n", "        pass\n"),
    ("socketcan: the stamp check not recorded at the close",
     '        record({"event": "stamps", "backend": "socketcan", "channel": NETDEV, "stamp_stats": stamps})\n', ""),
    ("socketcan: the error mask not checked", "    if mask != ERR_MASK:\n", "    if False:\n"),
    ("socketcan: the mask's bus left open", "        _open.append(b)\n        _close(b, b.shutdown)\n", ""),
    ("socketcan: the link left up at the close", "        if not _open:\n            ev = ", "        if False:\n            ev = "),
    ("socketcan: the link down at the first close", "        if not _open:\n            ev = ", "        if True:\n            ev = "),
    ("socketcan: the link down not read back", '    got = link()\n    if got["up"]:\n', '    got = link()\n    if False:\n'),
    ("socketcan: the close not hooked", "    b.shutdown = lambda: _close(b, shut)\n", ""),
    ("socketcan: the backend not recorded", '    record({"event": "open", "backend": "socketcan", ', '    record({"event": "open", '),
    ("canlib: the open not recorded", '    record({"event": "open", "backend": "canlib", "channel_asked": kw.get("channel"),\n',
     '    ({"event": "open", "backend": "canlib", "channel_asked": kw.get("channel"),\n'),
    ("records not written to GI_RUNS_DIR", "    if d:\n        with open(", "    if False:\n        with open("),
    ("detect: a leaf Kvaser found on Linux", 'return {"interface": "kvaser", "channel": NETDEV} if kvaser_driver() == DRIVER else None',
     'return {"interface": "kvaser", "channel": NETDEV} if kvaser_driver() else None'),
    ("pcan_witness_bus: the class not taken from the pin",
     '    _pin_tool().use()\n    mod = importlib.import_module("canbench.live.pcan_bus")\n',
     '    mod = importlib.import_module("canbench.live.pcan_bus")\n'),
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
    socketcan()
    bus_errors()
    real_mask()
    pin()
    guard_real()
    if "--no-scan" not in argv:
        scan()
        scan_can_fail()
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
