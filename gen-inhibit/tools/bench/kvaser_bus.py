"""The one way the bench harness opens the Kvaser and the Innomaker (gs_usb).

Gen-inhibit tester, 2026-11-11, the gen-inhibit reviewer's opener item of that day. On Linux
no Kvaser channel opens through python-can as it is: linuxcan's CANlib refuses python-can's
1-byte canIOCTL_SET_LOCAL_TXACK buffer ("Error in parameter"). And gs_usb's start() begins
with a USB reset, which re-enumerates the device on Linux and loses the handle. can-tools'
canbench.live.linux_fixes (can-tools fa6c0aea) works around both, and is a no-op elsewhere.
canbench comes from gen-inhibit's can-tools pin (tools/can_tools_pin.json, the implementor's),
loaded by file from scratch/can-tools/<commit>/ by can_tools_pin.use(), which refuses a
canbench imported from anywhere else (AGENTS.md "can-tools").

  kvaser_bus(**kw)     linux_fixes.apply(), then can.Bus(interface="kvaser", **kw); a channel
                       whose channel_info names a virtual channel (or gives none) is shut and
                       VirtualChannel raised. On madhouse, 2026-11-13 01:06, after the Leaf
                       failed with "canWrite hardware error -15" in another holder's replay,
                       CANlib listed only its two "Kvaser Virtual CAN" channels, so channel 0
                       opened a virtual bus that sends and hears nothing on the wire, and a
                       witness on it would have read an empty bus.
  gs_usb_bus(**kw)     linux_fixes.apply(), then can.Bus(interface="gs_usb", **kw)
  close_gs_usb(bus)    bus.shutdown(), then canbench's release_gs_usb(bus). python-can's
                       GsUsbBus.shutdown() leaves the pyusb handle open, so a second open in
                       the same process, or canctl handing the Innomaker back to socketcan
                       from it, fails without a word (reviewer, 2026-11-11). Call it, not
                       bus.shutdown(), for every gs_usb bus.
  bus(**kw)            for a tool that opens an interface named at run time: kvaser and
                       gs_usb through the two above, anything else straight to can.Bus
  fixes()              the fixes' result, {"kvaser": bool, "gs_usb": bool}, applied once per
                       process (canbench's own apply() is idempotent too)
  require_bench()      THE BENCH GUARD (reviewer's ask, 2026-11-12). kvaser_bus() and
                       gs_usb_bus() call it before anything else, and open nothing unless it
                       passes: the bench log (the lock, AGENTS.md "The shared bench") replays,
                       its holder is a gen-inhibit role (tester, or implementor on a loan), and
                       the holder's pid is this process or one of its ancestors. A runner takes
                       the lock with its own pid and runs the tools as its children; a take by
                       hand gives the Claude Code session's pid, and the tools that session
                       runs descend from it (bench-holder.md rule 2). Anything else raises
                       BenchNotHeld: a free lock, another holder, a log that does not replay,
                       vtrux_bench not importable (the pinned core's shared/bench is on
                       PYTHONPATH for bench runs, as for bench_held.py), a pid that is not an
                       ancestor, or no /proc to walk. There is no switch to turn it off. It
                       exists because a host test once faked a tool's `can` and reached the
                       real Kvaser through this module (2026-11-12, test_e4_rawpost.py): a
                       host test fakes this module, or its _lock_holder, and never needs the
                       lock. The PCAN and other interfaces opened straight through can.Bus are
                       not guarded here; the PCAN as a witness is (pcan_witness_bus).
  pcan_witness_bus(**kw)  (2026-11-13) the guard, then the pinned canbench's CanbenchPcanBus
                       with its bus-off reset off: x2_jammer's session witness
                       (bench_witness.PcanWitness)

THE KVASER ON LINUX IS SOCKETCAN (gen-inhibit reviewer, 2026-11-18, ruling (a)). The bench's
Kvaser has been on the kernel's kvaser_usb, netdev `kvaser`, since canctl's owner moved it off
linuxcan's leaf after leaf oopsed (canctl's header, 2026-11-13), and the charge-interposer's
X2 identify, which every gen-inhibit bench run calls, opens it there (kvaser_link). Through
CANlib the suite needed the Kvaser on leaf, so a run could pass on neither driver
(gen-inhibit-bench_20261118T143756_7d89: VirtualChannel; ..._150414_86a9: "no netdev kvaser").
So on Linux kvaser_bus() opens python-can's socketcan on netdev `kvaser`, and CANlib stays the
backend everywhere else (Windows). On Linux, after the guard:
  - the Kvaser must be on kvaser_usb: /sys/class/net/kvaser/device/driver names it. Not:
    KvaserLinkError, nothing opened (`sudo -n /usr/local/sbin/canctl mode kvaser socketcan
    --wait` moves it).
  - driver_mode=False (CANlib's silent mode) is refused: kvaser_usb has no listen-only, and this
    Leaf Light v2 has no silent mode either (bench_census, 2026-10-09). Any keyword but channel,
    bitrate, driver_mode, receive_own_messages and can_filters is refused (TypeError): a CANlib
    option must not be dropped without a word.
  - the link is brought up at the asked bitrate through canctl (`up kvaser <bitrate> restart-ms
    RESTART_MS`; canctl names listen-only off at every up), then read back with `ip -details
    -json link show kvaser`: UP, that bitrate, no listen-only. Not: KvaserLinkError.
  - the socket is python-can's socketcan with ignore_rx_error_frames=False, so the kernel's
    error frames reach the caller with CAN_ERR_FLAG (is_error_frame True; python-can keeps the
    class bits under 0x7FF in arbitration_id and the details in data). The socket's
    CAN_RAW_ERR_FILTER is read back after the open (python-can only logs a failed set): not
    the full mask 0x1FFFFFFF, and the bus is shut and KvaserLinkError raised.
  - an UP kvaser link ACKs with no socket open (kvaser_link's measurement, 2026-11-13), where
    the Kvaser on CANlib ACKed only while a handle was on bus. So the bus's shutdown() takes the
    link down (canctl down kvaser, read back) once the last bus this process opened is shut.
  - timestamps: python-can's socketcan stamps are host software stamps (SO_TIMESTAMPNS, the
    kernel's receive time), not the Leaf's hardware clock that CANlib's are.
  - error frames: the kernel sends controller-state (CRTL, BUSOFF) and RESTARTED frames as well
    as bus errors. bus_error(b, msg) / bus_error_class(b, can_id) count only the bus-error
    classes on SocketCAN, and every error frame on any other bus; the witnesses that count
    error frames (x2_jam_cases, restart_error_frames, e4_rig_calibration) count through them
    and record the frames they left out.
Every open, on either backend, is recorded: one line on stderr, and, when GI_RUNS_DIR is set
(the chain runner points it at the check's folder), a JSON line in kvaser_opens.jsonl there:
the backend ("socketcan" or "canlib"), the channel asked for and opened, the bitrate, and on
socketcan the driver, the link read back and the error mask; the close's link-down too.

Every harness file opens these adapters through this module; test_kvaser_bus.py scans for a
can.Bus that opens either directly, or with an interface named at run time. The tools import
it where they open the bus, not at their top, so a tool's copy in a mutant runner's scratch
folder imports without it.
"""
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin.py")
ROLES = ("tester (gen-inhibit)", "implementor (gen-inhibit)")
_fixes = None
# The Kvaser on Linux (ruling (a), 2026-11-18). Module globals read at each call, so
# test_kvaser_bus.py points them at a made-up sysfs, a fake canctl and a fake `ip`.
PLATFORM = sys.platform
NETDEV = "kvaser"
DRIVER = "kvaser_usb"
SYSFS_NET = "/sys/class/net"
CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
IP_SHOW = ["ip", "-details", "-json", "link", "show", NETDEV]
CMD_TIMEOUT_S = 60
RESTART_MS = 100
ERR_MASK = 0x1FFFFFFF
SOL_CAN_RAW, CAN_RAW_ERR_FILTER = 101, 2         # linux/can/raw.h (SOL_CAN_BASE + CAN_RAW)
SOCKETCAN_KW = ("receive_own_messages", "can_filters")
OPENS_FILE = "kvaser_opens.jsonl"
_open = []                                        # this process's open socketcan Kvaser buses


class BenchNotHeld(RuntimeError):
    pass


class VirtualChannel(RuntimeError):
    pass


class KvaserLinkError(RuntimeError):
    """The Kvaser is not on kvaser_usb, or its link could not be set or read back."""


def _lock_holder():
    """The bench log's holder ({who, run, pid, ts}, or None when free), replayed by shared/bench's
    own vtrux_bench.bench_lock. Raises BenchNotHeld when it cannot say."""
    try:
        from vtrux_bench import bench_lock
    except ImportError as e:
        raise BenchNotHeld("vtrux_bench is not importable (%s): a real adapter opens only in a bench "
                           "run, with the pinned core's shared/bench on PYTHONPATH" % e)
    try:
        import gi_paths
        return bench_lock.read_state(gi_paths.bench_log())["holder"]
    except Exception as e:      # noqa: BLE001  any failure to read the lock is "not held"
        raise BenchNotHeld("the bench log could not be read: %s: %s" % (type(e).__name__, e))


def _ancestors(pid=None):
    """This process's pid and each ancestor's, read from /proc. Raises BenchNotHeld without it."""
    pid = os.getpid() if pid is None else pid
    out = []
    while pid > 0 and pid not in out:
        out.append(pid)
        try:
            with open("/proc/%d/stat" % pid, encoding="ascii", errors="replace") as f:
                stat = f.read()
        except OSError as e:
            if not out[1:]:
                raise BenchNotHeld("cannot read this process's parent from /proc (%s)" % e)
            break
        # pid (comm) state ppid ...: comm may hold spaces and parentheses, so split after the last ')'
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return out


def require_bench():
    """The holder, when this process may open a real adapter; raises BenchNotHeld otherwise."""
    h = _lock_holder()
    if h is None:
        raise BenchNotHeld("the bench lock is free: a real adapter opens only under the lock")
    if h.get("who") not in ROLES:
        raise BenchNotHeld("the bench lock is held by %s, not a gen-inhibit role" % h.get("who"))
    anc = _ancestors()
    if h.get("pid") not in anc:
        raise BenchNotHeld("the bench lock's holder pid %s (%s) is not this process or an ancestor "
                           "(%s)" % (h.get("pid"), h.get("who"), " <- ".join(str(p) for p in anc)))
    return h


def _pin_tool():
    spec = importlib.util.spec_from_file_location("_kvaser_bus_can_tools_pin", PIN_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def linux_fixes():
    """canbench.live.linux_fixes from the pinned canbench, loaded by file (can_tools_pin.use)."""
    _pin_tool().use()
    return importlib.import_module("canbench.live.linux_fixes")


def fixes():
    global _fixes
    if _fixes is None:
        _fixes = linux_fixes().apply()
    return _fixes


def backend(platform=None):
    """"socketcan" on Linux, "canlib" elsewhere (ruling (a), 2026-11-18)."""
    return "socketcan" if (platform or PLATFORM).startswith("linux") else "canlib"


def record(event):
    """One open or close: a line on stderr, and a JSON line in GI_RUNS_DIR's kvaser_opens.jsonl."""
    event = dict(event, t=time.strftime("%Y-%m-%dT%H:%M:%S%z"), pid=os.getpid(),
                 tool=os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else None)
    sys.stderr.write("kvaser_bus: %s\n" % json.dumps(event, sort_keys=True))
    sys.stderr.flush()
    d = os.environ.get("GI_RUNS_DIR")
    if d:
        with open(os.path.join(d, OPENS_FILE), "a", encoding="ascii") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def kvaser_driver(root=None):
    """The driver netdev kvaser's device is bound to (kvaser_usb on the bench), or None when
    there is no netdev kvaser (the Kvaser on linuxcan's leaf, or unplugged)."""
    p = os.path.join(root or SYSFS_NET, NETDEV)
    if not os.path.exists(p):
        return None
    drv = os.path.join(p, "device", "driver")
    return os.path.basename(os.path.realpath(drv)) if os.path.exists(drv) else "none"


def require_socketcan(root=None):
    """KvaserLinkError unless the Kvaser is netdev kvaser on kvaser_usb. Returns the driver."""
    drv = kvaser_driver(root)
    if drv != DRIVER:
        raise KvaserLinkError("the Kvaser is not on %s (netdev %s: %s): on Linux it opens through "
                              "SocketCAN only; `sudo -n /usr/local/sbin/canctl mode kvaser socketcan "
                              "--wait` moves it" % (DRIVER, NETDEV, "absent" if drv is None
                                                    else "driver " + drv))
    return drv


def _run(argv):
    """-> (rc or None if it could not run, its output)."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=CMD_TIMEOUT_S)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None, "timed out after %d s" % CMD_TIMEOUT_S
    except OSError as e:
        return None, "could not run: %s" % e


def _canctl(*args):
    rc, out = _run(list(CANCTL) + list(args))
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return rc, (lines[-1] if lines else "no output")


def link(argv=None):
    """netdev kvaser read back from `ip -details -json link show kvaser`: {up, bitrate, state,
    restart_ms, listen_only}. KvaserLinkError if it cannot be read."""
    rc, out = _run(list(argv or IP_SHOW))
    try:
        if rc != 0:
            raise ValueError("rc %s: %s" % (rc, out.strip()[-200:]))
        d = json.loads(out)[0]
        info = (d.get("linkinfo") or {}).get("info_data") or {}
        return {"up": "UP" in (d.get("flags") or []), "bitrate": (info.get("bittiming") or {}).get("bitrate"),
                "state": info.get("state"), "restart_ms": info.get("restart_ms"),
                "listen_only": "LISTEN-ONLY" in (info.get("ctrlmode") or [])}
    except (ValueError, IndexError, KeyError, AttributeError, TypeError) as e:
        raise KvaserLinkError("netdev %s could not be read back (%s)" % (NETDEV, e))


def link_up(bitrate):
    """canctl up kvaser <bitrate> restart-ms RESTART_MS, read back: UP, that bitrate, not
    listen-only. Returns the read-back; KvaserLinkError otherwise."""
    args = ("up", NETDEV, str(bitrate), "restart-ms", str(RESTART_MS))
    rc, last = _canctl(*args)
    if rc != 0:
        _canctl("down", NETDEV)         # canctl may have failed after its own `ip link set up`
        raise KvaserLinkError("canctl %s failed (rc %s): %s" % (" ".join(args), rc, last))
    got = link()
    if not got["up"] or got["bitrate"] != bitrate or got["listen_only"]:
        _canctl("down", NETDEV)         # not left ACKing in a state nobody asked for
        raise KvaserLinkError("after canctl %s netdev %s reads %r, not up at %d without listen-only"
                              % (" ".join(args), NETDEV, got, bitrate))
    return got


def link_down():
    """canctl down kvaser, read back not UP. Returns the read-back; KvaserLinkError otherwise."""
    rc, last = _canctl("down", NETDEV)
    if rc != 0:
        raise KvaserLinkError("canctl down %s failed (rc %s): %s" % (NETDEV, rc, last))
    got = link()
    if got["up"]:
        raise KvaserLinkError("after canctl down %s netdev %s still reads UP" % (NETDEV, NETDEV))
    return got


def err_mask(b):
    """The socket's CAN_RAW_ERR_FILTER, read back, or None when it cannot be read."""
    try:
        return b.socket.getsockopt(SOL_CAN_RAW, CAN_RAW_ERR_FILTER)
    except Exception:       # noqa: BLE001  no socket, or not a CAN socket
        return None


# The kernel's error classes (linux/can/error.h), as python-can leaves them in an error
# frame's arbitration_id. A bus error is one of BUS_ERROR_CLASSES. CRTL (controller state),
# BUSOFF and RESTARTED report the controller, not an error on the wire, and CNT rides on
# real bus errors too (every one on the 2026-11-18 measurement carried it), so it decides
# nothing. Gen-inhibit reviewer, 2026-11-18: on kvaser_usb, B6, B12 and B22 count bus errors only.
CAN_ERR_TX_TIMEOUT, CAN_ERR_LOSTARB, CAN_ERR_CRTL, CAN_ERR_PROT = 0x1, 0x2, 0x4, 0x8
CAN_ERR_TRX, CAN_ERR_ACK, CAN_ERR_BUSOFF, CAN_ERR_BUSERROR = 0x10, 0x20, 0x40, 0x80
CAN_ERR_RESTARTED, CAN_ERR_CNT = 0x100, 0x200
BUS_ERROR_CLASSES = (CAN_ERR_TX_TIMEOUT | CAN_ERR_LOSTARB | CAN_ERR_PROT | CAN_ERR_TRX
                     | CAN_ERR_ACK | CAN_ERR_BUSERROR)


def bus_error_class(b, can_id):
    """For an error frame read from bus b with arbitration_id can_id: does it count as a bus
    error? On SocketCAN (b.vtrux_backend "socketcan"), only if it carries a bus-error class.
    On any other bus (CANlib, the PCAN), every error frame is a bus error, as before."""
    if getattr(b, "vtrux_backend", None) != "socketcan":
        return True
    return bool(can_id & BUS_ERROR_CLASSES)


def bus_error(b, msg):
    """Whether msg, read from bus b, is an error frame that counts as a bus error."""
    return bool(msg.is_error_frame) and bus_error_class(b, msg.arbitration_id)


def _socketcan_bus(channel=None, bitrate=500000, driver_mode=True, **kw):
    other = sorted(set(kw) - set(SOCKETCAN_KW))
    if other:
        raise TypeError("kvaser_bus on SocketCAN takes channel, bitrate, driver_mode, %s; not %s"
                        % (", ".join(SOCKETCAN_KW), ", ".join(other)))
    if driver_mode is not True:
        raise KvaserLinkError("driver_mode=%r (CANlib's silent mode): kvaser_usb has no listen-only, "
                              "and this Leaf Light v2 has no silent mode" % (driver_mode,))
    drv = require_socketcan()
    got = link_up(bitrate)
    import can
    b = can.Bus(interface="socketcan", channel=NETDEV, ignore_rx_error_frames=False, **kw)
    mask = err_mask(b)
    if mask != ERR_MASK:
        _open.append(b)
        _close(b, b.shutdown)
        raise KvaserLinkError("the %s socket's CAN_RAW_ERR_FILTER reads %r, not 0x%X: error frames "
                              "would not reach the caller" % (NETDEV, mask, ERR_MASK))
    _open.append(b)
    shut = b.shutdown
    b.shutdown = lambda: _close(b, shut)
    b.vtrux_backend = "socketcan"
    record({"event": "open", "backend": "socketcan", "channel_asked": channel, "channel": NETDEV,
            "bitrate": bitrate, "driver": drv, "link": got, "err_mask": "0x%X" % mask})
    return b


def _close(b, shut):
    """The bus's own shutdown, then the link down once this process has no Kvaser bus open."""
    if b not in _open:
        return shut()
    _open.remove(b)
    try:
        shut()
    finally:
        if not _open:
            ev = {"event": "close", "backend": "socketcan", "channel": NETDEV}
            try:
                ev["link"] = link_down()
            except KvaserLinkError as e:
                ev["error"] = str(e)
                record(ev)
                raise
            record(ev)


def kvaser_bus(**kw):
    require_bench()
    if backend() == "socketcan":
        return _socketcan_bus(**kw)
    fixes()
    import can
    b = can.Bus(interface="kvaser", **kw)
    info = getattr(b, "channel_info", None)
    if not isinstance(info, str) or "virtual" in info.lower():
        b.shutdown()
        raise VirtualChannel("kvaser channel %r is %r, not the bench's Kvaser: CANlib lists only "
                             "its virtual channels when the Leaf is not presented" % (kw.get("channel"), info))
    b.vtrux_backend = "canlib"
    record({"event": "open", "backend": "canlib", "channel_asked": kw.get("channel"),
            "channel": kw.get("channel"), "bitrate": kw.get("bitrate"), "channel_info": info})
    return b


def detect():
    """The Kvaser's config as can.detect_available_configs gives one, for a tool that finds its
    adapters by detection: on Linux {"interface": "kvaser", "channel": "kvaser"} while netdev
    kvaser is on kvaser_usb (CANlib's detection finds nothing there), else None; elsewhere
    CANlib's first channel that is not virtual."""
    if backend() == "socketcan":
        return {"interface": "kvaser", "channel": NETDEV} if kvaser_driver() == DRIVER else None
    import can
    for cfg in can.detect_available_configs(["kvaser"]):
        if "virtual" not in str(cfg.get("device_name", "")).lower():
            return cfg
    return None


def gs_usb_bus(**kw):
    require_bench()
    fixes()
    import can
    return can.Bus(interface="gs_usb", **kw)


def close_gs_usb(b):
    try:
        b.shutdown()
    finally:
        _pin_tool().use()
        importlib.import_module("canbench.live.receiver").release_gs_usb(b)


def pcan_witness_bus(**kw):
    """The PCAN as a timing witness (2026-11-13, the gen-inhibit reviewer's ruling (C)): the
    bench guard first, then the pinned canbench's CanbenchPcanBus, which hands up PCAN-Basic's
    status messages and bus states as controller-state error frames instead of data frames or
    an exception (python-can 4.6.1 raises on a bus-passive Read). canbench_busoff_reset=False:
    a witness never resets its own controller, so its device clock runs on through the
    session (a reset would also restart it)."""
    require_bench()
    _pin_tool().use()
    mod = importlib.import_module("canbench.live.pcan_bus")
    return mod.CanbenchPcanBus(canbench_busoff_reset=False, **kw)


def bus(**kw):
    iface = kw.pop("interface")
    if iface == "kvaser":
        return kvaser_bus(**kw)
    if iface == "gs_usb":
        return gs_usb_bus(**kw)
    import can
    return can.Bus(interface=iface, **kw)
