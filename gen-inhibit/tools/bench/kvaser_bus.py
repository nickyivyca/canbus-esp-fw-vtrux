"""The one way the bench harness opens the Kvaser, the Innomakers (gs_usb) and the PCAN.

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
  innomaker_bus(role, bitrate, **kw)   (2026-11-18) an Innomaker by its canctl role, canmain
                       or canchg, never by index (the user's ruling of 2026-11-18): see "THE
                       INNOMAKERS, BY ROLE" below. Its shutdown() closes the bus, frees the
                       pyusb handle (canbench's release_gs_usb: python-can's GsUsbBus.shutdown()
                       leaves it open, reviewer, 2026-11-11) and hands the role back to
                       socketcan, down. The old gs_usb_bus(**kw) and close_gs_usb(bus), which
                       took gs_usb's index (the first device found), are gone.
  pcan_bus(bitrate, **kw)   (2026-11-18) the PCAN by its canctl role pcanfd, never by channel
                       (PCAN_USBBUS1 is the first PCAN channel found): the guard, then canbench's
                       bench_roles.open_role("pcanfd"), python-can's pcan by the role's device id.
                       See "THE PCAN, BY ROLE" below.
  bus(**kw)            for a tool that opens an interface named at run time: kvaser through
                       kvaser_bus(), pcan through pcan_bus(), gs_usb refused (TypeError: an
                       Innomaker is asked for by its role, innomaker_bus), anything else
                       straight to can.Bus
  fixes()              the fixes' result, {"kvaser": bool, "gs_usb": bool}, applied once per
                       process (canbench's own apply() is idempotent too)
  require_bench()      THE BENCH GUARD (reviewer's ask, 2026-11-12). kvaser_bus(),
                       innomaker_bus(), pcan_bus() and pcan_witness_bus() call it before
                       anything else (bar refusing a keyword), and open nothing unless it
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
                       lock. From 2026-11-18 the PCAN is guarded too (pcan_bus); interfaces
                       opened straight through can.Bus are not.
  pcan_witness_bus(bitrate, **kw)  (2026-11-13) the guard, the role as pcan_bus, then the pinned
                       canbench's CanbenchPcanBus on that device id with its bus-off reset off:
                       x2_jammer's session witness (bench_witness.PcanWitness)

THE KVASER ON LINUX IS SOCKETCAN (gen-inhibit reviewer, 2026-11-18, ruling (a)). The bench's
Kvaser has been on the kernel's kvaser_usb, netdev `kvaser`, since canctl's owner moved it off
linuxcan's leaf after leaf oopsed (canctl's header, 2026-11-13), and the charge-interposer's
X2 identify, which every gen-inhibit bench run calls, opens it there (kvaser_link). Through
CANlib the suite needed the Kvaser on leaf, so a run could pass on neither driver
(gen-inhibit-bench_20261118T143756_7d89: VirtualChannel; ..._150414_86a9: "no netdev kvaser").
So on Linux kvaser_bus() opens python-can's socketcan on netdev `kvaser`, and CANlib stays the
backend everywhere else (Windows). On Linux, after the guard:
  - the Kvaser is resolved by its canctl role through canbench's bench_roles (can-tools 5220d37,
    the gen-inhibit reviewer's ruling of 2026-11-18): resolve("kvaser") must say socketcan mode,
    netdev kvaser (on kvaser_usb). canbench's refusal (not attached, two Kvasers, no netdev) is
    RoleError; linuxcan's leaf is KvaserLinkError (`sudo -n /usr/local/sbin/canctl mode kvaser
    socketcan --wait` moves it). Nothing opened either way.
  - driver_mode=False (CANlib's silent mode) is refused: kvaser_usb has no listen-only, and this
    Leaf Light v2 has no silent mode either (bench_census, 2026-10-09). Any keyword but channel,
    bitrate, driver_mode, receive_own_messages and can_filters is refused (TypeError): a CANlib
    option must not be dropped without a word.
  - the link is brought up at the asked bitrate through canctl (`up kvaser <bitrate> restart-ms
    RESTART_MS`; canctl names listen-only off at every up), then read back with `ip -details
    -json link show kvaser`: UP, that bitrate, no listen-only. Not: KvaserLinkError.
  - the bus is canbench's bench_roles.open_role("kvaser", modes=("socketcan",),
    hw_timestamps=True), which is the pinned canbench's SocketcanHwBus on the role's netdev (the
    gen-inhibit reviewer's ruling of 2026-11-18: the Kvaser keeps its witness roles, the hw-time
    judges included, on its own SO_TIMESTAMPING raw stamps, read by canbench). open_role runs
    after this module's canctl up and refuses a down link; canbench never brings a link up or
    down. The kernel's error
    frames reach the caller with CAN_ERR_FLAG (is_error_frame True, the class bits in
    arbitration_id, the details in data). The socket's CAN_RAW_ERR_FILTER is read back after
    the open: not the full mask 0x1FFFFFFF, and the bus is shut and KvaserLinkError raised. An
    open that raises takes the link down again.
  - an UP kvaser link ACKs with no socket open (kvaser_link's measurement, 2026-11-13), where
    the Kvaser on CANlib ACKed only while a handle was on bus. So the bus's shutdown() takes the
    link down (canctl down kvaser, read back) once the last bus this process opened is shut.
  - timestamps: msg.timestamp is the host's receive time (ts[0]); msg.hw_timestamp is the
    Leaf's raw hardware stamp (ts[2]) or None. canbench checks each open's stamps on its first
    CHECK_FRAMES data frames (bus.stamp_check: pending, hardware or host_only, with the
    reason); host_only makes every hw_timestamp None. Error frames carry no hardware stamp on
    kvaser_usb (0 of 835, can-tools' bench check of 2026-11-18): host time only places them.
    The close records bus.stamp_stats() (the check, and zero or backward stamps after it) in
    a "stamps" event. witness_clock.kvaser_times maps the hardware stamps onto the epoch
    clock through device_fit, and only when the check reads "hardware".
  - error frames: the kernel sends controller-state (CRTL, BUSOFF) and RESTARTED frames as well
    as bus errors. bus_error(b, msg) / bus_error_class(b, can_id) count only the bus-error
    classes on SocketCAN, and every error frame on any other bus; the witnesses that count
    error frames (x2_jam_cases, restart_error_frames, e4_rig_calibration) count through them
    and record the frames they left out.
THE INNOMAKERS, BY ROLE (2026-11-18: the user's ruling, a tool asks for an adapter by its canctl
role, never by index, channel number or first found; the gen-inhibit reviewer's conditions 1-2).
innomaker_bus(role) refuses channel=, index=, bus= and address= (TypeError) and a role that is not
canmain or canchg (InnomakerError), then, after the guard:
  - canbench's bench_roles.resolve(role) must say socketcan mode, its netdev there, its link
    down (the runner's canmain probe `link`); canbench's refusal is named in InnomakerError.
    No serial is read.
  - `canctl mode <role> libusb` hands it to libusb (canbench never changes a mode); then
    bench_roles.open_role(role, modes=("libusb",), bitrate=...) opens gs_usb on the role's USB bus
    and address, with the fixes applied; its shutdown() frees the pyusb handle (5220d37), so the
    close here does not release it again.
  - a failed hand-over or open hands the role back (`canctl mode <role> socketcan --wait`, read
    back: the netdev exists and is down) and re-raises; if the hand-back fails too, one
    InnomakerError names both.
  - the close hands it back the same way, read back, recorded (InnomakerError if not); a second
    shutdown() does nothing.

THE PCAN, BY ROLE (the same ruling, 2026-11-18). PCAN-Basic numbers PCAN-USB channels
PCAN_USBBUS1, 2, ... in the order it finds them, so channel PCAN_USBBUS1 is the first found.
pcan_bus() and pcan_witness_bus() refuse channel=, device_id= and interface= (TypeError), then,
after the guard, go through canbench's bench_roles (can-tools 5220d37), which reads `canctl
roles` (read-only, no root, no serials: canctl's table, the bench's only list of adapters):
pcan_bus by open_role("pcanfd", modes=("pcan",)), python-can's pcan by the role's device id;
the witness by resolve()'s device id (pcan_role, which also wants a char device,
/sys/class/pcan/<chardev>, for the witness's identity) into CanbenchPcanBus. canbench refuses
(RoleError, re-raised here) a PCAN not attached or more than one attached: the PCAN reports no
serial, so the role means the one present. The open is recorded with the role, the device id,
the char device and the channel PCAN-Basic gave.

Every open, on either backend, is recorded: one line on stderr, and, when GI_RUNS_DIR is set
(the chain runner points it at the check's folder), a JSON line in kvaser_opens.jsonl there:
the backend ("socketcan" or "canlib"), the channel asked for and opened, the bitrate, and on
socketcan the driver, the link read back and the error mask; the close's link-down too.

Every harness file opens these adapters through this module; test_kvaser_bus.py scans for a
can.Bus that opens any of them directly (interface kvaser, gs_usb or pcan), or with an interface
named at run time, and for a PCAN channel named as a literal (PCAN_USBBUS<n>) outside the tests. The tools import
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
KVASER_ROLE = "kvaser"    # its canctl role (bench_roles)
NETDEV = "kvaser"
CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
IP_SHOW = ["ip", "-details", "-json", "link", "show", NETDEV]
CMD_TIMEOUT_S = 60
RESTART_MS = 100
ERR_MASK = 0x1FFFFFFF
SOL_CAN_RAW, CAN_RAW_ERR_FILTER = 101, 2         # linux/can/raw.h (SOL_CAN_BASE + CAN_RAW)
SOCKETCAN_KW = ("receive_own_messages", "can_filters")
# canbench's per-open stamp check (SocketcanHwBus): the first CHECK_FRAMES data frames must carry
# nonzero, advancing hardware stamps more than HOST_GAP_S from host time (can-tools 5952c0c's
# defaults; the gen-inhibit reviewer's ruling of 2026-11-18: every open checks its stamps).
CHECK_FRAMES = 20
HOST_GAP_S = 2.0
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


class RoleError(RuntimeError):
    """canbench.bench_roles' RoleError, re-raised here with its message (so a tool can catch it
    without importing the pinned canbench itself): a role canbench would not resolve or open."""


def bench_roles():
    """canbench.bench_roles from the pinned canbench (can-tools 5220d37; can_tools_pin.use()):
    every bench adapter's role-to-device mapping (the user's ruling of 2026-11-18)."""
    _pin_tool().use()
    return importlib.import_module("canbench.bench_roles")


def _resolve(role):
    br = bench_roles()
    try:
        return br.resolve(role)
    except br.RoleError as e:
        raise RoleError(str(e)) from e


def _open_role(role, **kw):
    br = bench_roles()
    try:
        return br.open_role(role, **kw)
    except br.RoleError as e:
        raise RoleError(str(e)) from e


def kvaser_role():
    """bench_roles.resolve("kvaser"), in socketcan mode (netdev kvaser on kvaser_usb). RoleError
    when canbench will not resolve it; KvaserLinkError when it is on linuxcan's leaf, read from
    canbench's table first (bench_roles.roles), so resolve never enumerates CANlib channels here."""
    br = bench_roles()
    try:
        row = br.roles().get(KVASER_ROLE)
    except br.RoleError as e:
        raise RoleError(str(e)) from e
    if row is not None and row.mode == "linuxcan":
        raise KvaserLinkError("the Kvaser is on linuxcan's leaf (driver %s): on Linux it opens through SocketCAN "
                              "only; `sudo -n /usr/local/sbin/canctl mode kvaser socketcan --wait` moves it"
                              % (row.driver,))
    res = _resolve(KVASER_ROLE)
    if res.mode != "socketcan" or res.netdev != NETDEV:
        raise KvaserLinkError("the Kvaser is in mode %s (netdev %s, driver %s): on Linux it opens through "
                              "SocketCAN only; `sudo -n /usr/local/sbin/canctl mode kvaser socketcan --wait` "
                              "moves it" % (res.mode, res.netdev, res.role.driver))
    return res


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


def stamp_stats(b):
    """The bus's stamp check and what its stamps did after it (SocketcanHwBus.stamp_stats()), or
    None for a bus with none (CANlib, a fake)."""
    try:
        return b.stamp_stats()
    except Exception:       # noqa: BLE001  not a SocketcanHwBus
        return None


def err_mask(b):
    """The socket's CAN_RAW_ERR_FILTER, read back, or None when it cannot be read."""
    try:
        sock = getattr(b, "socket", None) or getattr(b, "_sock")
        return sock.getsockopt(SOL_CAN_RAW, CAN_RAW_ERR_FILTER)
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
    res = kvaser_role()
    got = link_up(bitrate)
    try:
        # canbench's open by role, after this module's canctl up (open_role refuses a down link):
        # SocketcanHwBus on the role's netdev, with its per-open stamp check
        b = _open_role(KVASER_ROLE, modes=("socketcan",), hw_timestamps=True, check_frames=CHECK_FRAMES,
                       host_gap_s=HOST_GAP_S, **kw)
    except BaseException:
        _canctl("down", NETDEV)         # nothing open: not left ACKing
        raise
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
    record({"event": "open", "backend": "socketcan", "role": KVASER_ROLE, "channel_asked": channel,
            "channel": NETDEV, "bitrate": bitrate, "driver": res.role.driver, "link": got, "err_mask": "0x%X" % mask,
            "reader": "canbench.bench_roles.open_role -> SocketcanHwBus", "check_frames": CHECK_FRAMES,
            "host_gap_s": HOST_GAP_S})
    return b


def _close(b, shut):
    """The bus's own shutdown, then the link down once this process has no Kvaser bus open."""
    if b not in _open:
        return shut()
    _open.remove(b)
    stamps = stamp_stats(b)
    try:
        shut()
    finally:
        record({"event": "stamps", "backend": "socketcan", "channel": NETDEV, "stamp_stats": stamps})
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
        try:
            kvaser_role()
        except (RoleError, KvaserLinkError):
            return None
        return {"interface": "kvaser", "channel": NETDEV}
    import can
    for cfg in can.detect_available_configs(["kvaser"]):
        if "virtual" not in str(cfg.get("device_name", "")).lower():
            return cfg
    return None


# ------------------------------------------------ the Innomakers, by canctl role (2026-11-18)
#
# The user's ruling of 2026-11-18, for the whole bench: a tool asks for an adapter by its canctl
# role, never by index, channel number or first found. python-can's gs_usb by index takes
# GsUsb.scan()[0], the first gs_usb device libusb lists, which may be either Innomaker. So an
# Innomaker opens here by its role, through canbench's bench_roles (can-tools 5220d37; the
# gen-inhibit reviewer's ruling of 2026-11-18): resolve() says where the role is (its mode, netdev,
# link, USB bus and address, from `canctl roles`), open_role() opens it. No serial is read.
# canbench never changes a mode or a link, so this module does (the reviewer's conditions of
# 2026-11-18): the role is handed to libusb (`canctl mode <role> libusb`) before open_role(modes=
# ("libusb",)), and back to socketcan, down, in the close, read back through resolve() as the
# chain runner's canmain probe `link` reads it (the netdev exists, its link down), so a start or a
# resume never meets canmain without its netdev. open_role's bus frees its pyusb handle in its own
# shutdown() (5220d37), so the close here does not release it again.

INNOMAKERS = ("canmain", "canchg")


class InnomakerError(RuntimeError):
    """An Innomaker role could not be found, handed over, opened or handed back down."""


def innomaker_down(role):
    """The chain runner's probe `link` for the role, through bench_roles.resolve: socketcan mode,
    its netdev there, its link down. Returns the line; InnomakerError otherwise (canbench's
    refusal named)."""
    try:
        res = _resolve(role)
    except RoleError as e:
        raise InnomakerError("%s: %s" % (role, e))
    if res.mode != "socketcan" or res.netdev != role:
        raise InnomakerError("%s is in mode %s (netdev %s), not socketcan with netdev %s" % (role, res.mode,
                                                                                            res.netdev, role))
    if res.role.link != "down":
        raise InnomakerError("netdev %s is %s, not down" % (role, res.role.link))
    return "%s exists and its link is down" % role


def innomaker_restore(role):
    """`canctl mode <role> socketcan --wait`, then innomaker_down read back. Returns the line;
    InnomakerError otherwise. Each step recorded."""
    rc, last = _canctl("mode", role, "socketcan", "--wait")
    ev = {"event": "close", "backend": "gs_usb", "role": role, "canctl": "mode %s socketcan --wait" % role,
          "canctl_rc": rc}
    try:
        if rc != 0:
            raise InnomakerError("canctl mode %s socketcan --wait failed (rc %s): %s" % (role, rc, last))
        ev["readback"] = innomaker_down(role)
    except InnomakerError as e:
        ev["error"] = str(e)
        record(ev)
        raise
    record(ev)
    return ev["readback"]


def _restore_after_failure(role, what):
    """After a failed hand-over or open: hand the role back. If that fails too, one
    InnomakerError names both."""
    try:
        innomaker_restore(role)
    except InnomakerError as r:
        raise InnomakerError("%s; and the hand-back failed: %s" % (what, r))


def innomaker_bus(role, bitrate=500000, **kw):
    """The Innomaker with canctl role `role` (canmain or canchg), opened through libusb by
    canbench's open_role, in NORMAL mode (gs_usb has no listen-only). Its shutdown() closes the
    bus (open_role's shutdown frees the pyusb handle) and hands the role back to socketcan, down,
    read back (InnomakerError if not). Asking by index, channel, bus or address is refused."""
    picked = sorted(set(kw) & {"channel", "index", "bus", "address"})
    if picked:
        raise TypeError("an Innomaker is opened by its role, not by %s" % ", ".join(picked))
    require_bench()
    if role not in INNOMAKERS:
        raise InnomakerError("%r is not an Innomaker role (%s)" % (role, ", ".join(INNOMAKERS)))
    innomaker_down(role)
    rc, last = _canctl("mode", role, "libusb")
    if rc != 0:
        what = "canctl mode %s libusb failed (rc %s): %s" % (role, rc, last)
        _restore_after_failure(role, what)
        raise InnomakerError(what)
    try:
        b = _open_role(role, modes=("libusb",), bitrate=bitrate, **kw)
    except BaseException as e:
        _restore_after_failure(role, "the gs_usb open of %s failed: %s: %s" % (role, type(e).__name__, e))
        raise
    res = getattr(b, "bench_role", None)
    b.vtrux_backend, b.vtrux_role = "gs_usb", role
    record({"event": "open", "backend": "gs_usb", "role": role, "usb": res.role.usb if res else None,
            "bus": res.busnum if res else None, "address": res.devnum if res else None, "bitrate": bitrate,
            "reader": "canbench.bench_roles.open_role"})
    shut = b.shutdown
    b.shutdown = lambda: _innomaker_close(b, shut, role)
    return b


def _innomaker_close(b, shut, role):
    if getattr(b, "_vtrux_closed", False):
        return None
    b._vtrux_closed = True
    try:
        shut()              # open_role's shutdown: the bus, then canbench's release_gs_usb
    finally:
        innomaker_restore(role)


# ------------------------------------------------ the PCAN, by canctl role (2026-11-18)
#
# The same ruling: python-can's channel PCAN_USBBUS1 is the first PCAN-USB channel PCAN-Basic
# numbers, whichever device that is. So the PCAN opens by its canctl role, pcanfd, through
# canbench's bench_roles: resolve() gives its PCAN device id (from `canctl roles`, refusing when
# more than one PCAN is attached: the PCAN reports no serial, so the role means the one present),
# and open_role() opens python-can's pcan by that device id. The witness opens the pinned
# canbench's CanbenchPcanBus on resolve()'s device id.

PCAN_ROLE = "pcanfd"
PCAN_REFUSED_KW = ("channel", "device_id", "interface")


def pcan_role(role=PCAN_ROLE):
    """{"role", "device_id", "chardev"} for the PCAN role, from bench_roles.resolve (mode pcan,
    with a char device for the witness's sysfs identity). RoleError otherwise."""
    res = _resolve(role)
    if res.mode != "pcan":
        raise RoleError("role %s is in mode %s, not pcan" % (role, res.mode))
    chardev = res.role.chardev
    if not chardev or "/" in chardev:
        raise RoleError("role %s: no PCAN char device (%r)" % (role, chardev))
    return {"role": role, "device_id": res.kwargs["device_id"], "chardev": chardev}


def _pcan_refuse(kw):
    picked = sorted(set(kw) & set(PCAN_REFUSED_KW))
    if picked:
        raise TypeError("the PCAN is opened by its canctl role (%s), not by %s" % (PCAN_ROLE, ", ".join(picked)))


def _pcan_record(b, r, bitrate, reader):
    b.vtrux_backend, b.vtrux_role, b.vtrux_device_id = "pcan", r["role"], r["device_id"]
    b.vtrux_chardev = r["chardev"]
    record({"event": "open", "backend": "pcan", "role": r["role"], "device_id": r["device_id"],
            "chardev": r["chardev"], "channel": getattr(b, "channel_info", None), "bitrate": bitrate,
            "reader": reader})
    return b


def pcan_bus(bitrate=500000, **kw):
    """The PCAN by its canctl role pcanfd: the bench guard, then canbench's open_role (modes pcan:
    python-can's pcan by the role's device id) with bitrate and the caller's other arguments.
    Asking by channel, device_id or interface is refused (TypeError). The open is recorded."""
    _pcan_refuse(kw)
    require_bench()
    b = _open_role(PCAN_ROLE, modes=("pcan",), bitrate=bitrate, **kw)
    res = getattr(b, "bench_role", None)
    r = {"role": PCAN_ROLE, "device_id": res.kwargs.get("device_id") if res else None,
         "chardev": res.role.chardev if res else None}
    return _pcan_record(b, r, bitrate, "canbench.bench_roles.open_role")


def pcan_witness_bus(bitrate=500000, **kw):
    """The PCAN as a timing witness (2026-11-13, the gen-inhibit reviewer's ruling (C)): the
    bench guard first, the role resolved (pcan_role; asking by channel, device_id or interface
    refused), then the pinned canbench's CanbenchPcanBus on that device id, which hands up
    PCAN-Basic's status messages and bus states as controller-state error frames instead of data
    frames or an exception (python-can 4.6.1 raises on a bus-passive Read).
    canbench_busoff_reset=False: a witness never resets its own controller, so its device clock
    runs on through the session (a reset would also restart it)."""
    _pcan_refuse(kw)
    require_bench()
    r = pcan_role()
    _pin_tool().use()
    mod = importlib.import_module("canbench.live.pcan_bus")
    b = mod.CanbenchPcanBus(canbench_busoff_reset=False, device_id=r["device_id"], bitrate=bitrate, **kw)
    return _pcan_record(b, r, bitrate, "canbench.live.pcan_bus.CanbenchPcanBus")


def bus(**kw):
    iface = kw.pop("interface")
    if iface == "kvaser":
        return kvaser_bus(**kw)
    if iface == "gs_usb":
        raise TypeError("an Innomaker is asked for by its canctl role: innomaker_bus(role), not "
                        "bus(interface='gs_usb')")
    if iface == "pcan":
        return pcan_bus(**kw)
    import can
    return can.Bus(interface=iface, **kw)
