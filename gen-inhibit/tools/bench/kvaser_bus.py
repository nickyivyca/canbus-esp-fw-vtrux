"""The one way the bench harness opens the Kvaser, the Innomakers (gs_usb) and the PCAN.

Gen-inhibit tester, 2026-11-11, the gen-inhibit reviewer's opener item of that day. On Linux
no Kvaser channel opens through python-can as it is: linuxcan's CANlib refuses python-can's
1-byte canIOCTL_SET_LOCAL_TXACK buffer ("Error in parameter"). And gs_usb's start() begins
with a USB reset, which re-enumerates the device on Linux and loses the handle. can-tools'
canbench.live.linux_fixes (can-tools fa6c0aea) works around both, and is a no-op elsewhere.
canbench comes from gen-inhibit's can-tools pin (tools/can_tools_pin.json, the implementor's),
loaded by file from scratch/can-tools/<commit>/ by can_tools_pin.use(), which refuses a
canbench imported from anywhere else (AGENTS.md "can-tools").

ON canbench's BENCH LAYER (can-tools ec7cefe; the gen-inhibit reviewer's ruling of 2026-11-19 under
methodology 98d0b749: code both projects need is shared). Setting a bench adapter up by its canctl role,
opening it and putting it back is canbench.bench_layer's: open_bench, link_up / link_down, to_libusb /
to_socketcan, refuse_picks, bus_error / bus_error_class. This module plugs its bench guard into the layer
(layer(): set_guard(require_bench)) and keeps only gen-inhibit's own parts: the guard itself (until shared/bench's
bench_lock guard is pinned), kvaser_opens.jsonl, the CAN_RAW_ERR_FILTER read-back, the link down at the
Kvaser's close, the stamp check's record (the witness-clock gating is witness_clock's), and the role names its
openers take. canbench's refusals (bench_roles.RoleError, bench_layer.BenchLayerError) are re-raised as
KvaserLinkError, InnomakerError or RoleError, naming the step.

  kvaser_bus(**kw)     linux_fixes.apply(), then can.Bus(interface="kvaser", **kw); a channel
                       whose channel_info names a virtual channel (or gives none) is shut and
                       VirtualChannel raised. On madhouse, 2026-11-13 01:06, after the Leaf
                       failed with "canWrite hardware error -15" in another holder's replay,
                       CANlib listed only its two "Kvaser Virtual CAN" channels, so channel 0
                       opened a virtual bus that sends and hears nothing on the wire, and a
                       witness on it would have read an empty bus.
  innomaker_bus(role, bitrate, **kw)   (2026-11-18) an Innomaker by its canctl role, canmain
                       or canchg, never by index (the user's ruling of 2026-11-18), through
                       bench_layer.open_bench (2026-11-19): see "THE INNOMAKERS, BY ROLE" below.
                       The old gs_usb_bus(**kw) and close_gs_usb(bus), which took gs_usb's index
                       (the first device found), are gone.
  innomaker_held(role) (2026-11-18, the gen-inhibit reviewer's condition 5) `with innomaker_held(role)
                       as inno:` hands the role to libusb once, for a run that opens and closes it
                       more than once (B9's and B10's bursts, NONODE's replayer, canmain_burst_check);
                       in bench_layer's calls (to_libusb, open_bench(restore=False), to_socketcan).
                       See its docstring.
  pcan_bus(bitrate, **kw)   (2026-11-18) the PCAN by its canctl role pcanfd, never by channel
                       (PCAN_USBBUS1 is the first PCAN channel found): bench_layer.open_bench("pcanfd"),
                       python-can's pcan by the role's device id.
                       See "THE PCAN, BY ROLE" below.
  bus(**kw)            for a tool that opens an interface named at run time: kvaser through
                       kvaser_bus(), pcan through pcan_bus(), gs_usb refused (TypeError: an
                       Innomaker is asked for by its role, innomaker_bus), anything else
                       straight to can.Bus
  fixes()              the fixes' result, {"kvaser": bool, "gs_usb": bool}, applied once per
                       process (canbench's own apply() is idempotent too)
  layer()              the pinned canbench.bench_layer with require_bench set as its guard (2026-11-19).
  require_bench()      THE BENCH GUARD (reviewer's ask, 2026-11-12). kvaser_bus() calls it first,
                       and bench_layer runs it before every canctl call and open (innomaker_bus,
                       innomaker_held, pcan_bus, pcan_witness_bus, link_down); nothing opens unless it
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
  - kvaser_role() first: canbench's table must not say linuxcan (KvaserLinkError naming `sudo -n
    /usr/local/sbin/canctl mode kvaser socketcan --wait`; read from the table, so CANlib is never
    enumerated and the Kvaser is not moved off leaf here), and resolve("kvaser") must say socketcan,
    netdev kvaser (RoleError: not attached, two Kvasers, no netdev).
  - the bus is bench_layer.open_bench("kvaser", bitrate, mode="socketcan", hw_timestamps=True,
    restart_ms=RESTART_MS, restore=False) (2026-11-19): the link brought up at the bitrate and read
    back (UP, that bitrate, no listen-only, restart-ms), then open_role's SocketcanHwBus. canbench's
    refusal is KvaserLinkError, and the link is taken down again.
  - driver_mode=False (CANlib's silent mode) is refused: kvaser_usb has no listen-only, and this
    Leaf Light v2 has no silent mode either (bench_census, 2026-10-09). Any keyword but channel,
    bitrate, driver_mode, receive_own_messages and can_filters is refused (TypeError): a CANlib
    option must not be dropped without a word.
  - SocketcanHwBus is the pinned canbench's (the gen-inhibit reviewer's ruling of 2026-11-18: the
    Kvaser keeps its witness roles, the hw-time judges included, on its own SO_TIMESTAMPING raw stamps,
    read by canbench). The kernel's error
    frames reach the caller with CAN_ERR_FLAG (is_error_frame True, the class bits in
    arbitration_id, the details in data). The socket's CAN_RAW_ERR_FILTER is read back after
    the open: not the full mask 0x1FFFFFFF, and the bus is shut and KvaserLinkError raised. An
    open that raises takes the link down again.
  - an UP kvaser link ACKs with no socket open (kvaser_link's measurement, 2026-11-13), where
    the Kvaser on CANlib ACKed only while a handle was on bus. So the bus's shutdown() takes the
    link down (bench_layer.link_down, read back) once the last bus this process opened is shut.
  - timestamps: msg.timestamp is the host's receive time (ts[0]); msg.hw_timestamp is the
    Leaf's raw hardware stamp (ts[2]) or None. canbench checks each open's stamps on its first
    CHECK_FRAMES data frames (bus.stamp_check: pending, hardware or host_only, with the
    reason); host_only makes every hw_timestamp None. Error frames carry no hardware stamp on
    kvaser_usb (0 of 835, can-tools' bench check of 2026-11-18): host time only places them.
    The close records bus.stamp_stats() (the check, and zero or backward stamps after it) in
    a "stamps" event. witness_clock.kvaser_times maps the hardware stamps onto the epoch
    clock through device_fit, and only when the check reads "hardware".
  - error frames: the kernel sends controller-state (CRTL, BUSOFF) and RESTARTED frames as well
    as bus errors. bus_error(b, msg) / bus_error_class(b, can_id) (bench_layer's, from 2026-11-19; the
    backend read from the bus's bench_role) count only the bus-error
    classes on SocketCAN, and every error frame on any other bus; the witnesses that count
    error frames (x2_jam_cases, restart_error_frames, e4_rig_calibration) count through them
    and record the frames they left out.
THE INNOMAKERS, BY ROLE (2026-11-18: the user's ruling, a tool asks for an adapter by its canctl
role, never by index, channel number or first found; the gen-inhibit reviewer's conditions 1-2).
innomaker_bus(role) refuses a role that is not canmain or canchg (InnomakerError), then is
bench_layer.open_bench(role, bitrate, mode="libusb", restore=True) (2026-11-19): a pick keyword refused,
the guard, the role handed to libusb and read back from `canctl roles`, gs_usb opened on its USB bus and
address; its shutdown() frees the pyusb handle and puts the role back as found (socketcan, link down), read
back. A failed open puts it back too. canbench's refusal is InnomakerError. The open and the close are
recorded; a second shutdown() does nothing.
A role FOUND WITH ITS LINK UP is refused before bench_layer touches it (InnomakerError), recorded in
kvaser_opens.jsonl as a finding ("found up"), and its link left as it is (the gen-inhibit reviewer's ruling
of 2026-11-19: in a run the runner brings canmain down and the lock keeps everyone else off, so an up
Innomaker is interference; open_bench would take it down and put it back up, and the runner forbids canmain
up). The same holds at innomaker_held's enter.
innomaker_held(role) holds the role in libusb around a run, with the opens and closes inside it
(condition 5, 2026-11-18): bench_layer.to_libusb at the start, open_bench(restore=False) per open,
bench_layer.to_socketcan at the end; a hand-back at every close would sit inside a burst's or NONODE's
5 ms close's timing.
A SENDER ON A libusb ROLE READS ITS ECHOES (the gen-inhibit reviewer's ruling (C) of 2026-11-19). gs_usb
returns every frame the device completed as an echo on its IN endpoint (python-can: is_rx False), and the
host is that endpoint's only buffer: with 257 echoes unread the device took no more frames, and send 258
blocked for pyusb's 1 s default write timeout and raised (canmain at 500 kbit, 2 ms apart, every frame
ACKed by the PCAN; short bench check of 2026-11-19, gen-inhibit runs/benchcheck_9950f96_20261119T015215/).
echo_drain(bus, role, what) starts a thread that reads the bus (DRAIN_RECV_S, 1 ms, gs_usb's least) and
counts what it reads: echoes, other frames, error frames. drain.stop(sent) asks it to stop, it reads what is
queued to the first empty read (at most DRAIN_SWEEP_MAX_S), and joins it, before the bus's shutdown, and records an "echoes" event (sent, echoes, rx, errors, whether echoes == sent).
innomaker_held's exit stops a drain still running before it shuts a bus. canmain_burst_check's replay and
NONODE's Player use it. Kept thin, for canbench's own to replace once its drain stops within the 5 ms
close (the gen-inhibit reviewer's ruling (c), 2026-11-19: can-tools 0f9c85f's drain reads 50 ms at a time).

THE PCAN, BY ROLE (the same ruling, 2026-11-18). PCAN-Basic numbers PCAN-USB channels
PCAN_USBBUS1, 2, ... in the order it finds them, so channel PCAN_USBBUS1 is the first found.
pcan_bus() is bench_layer.open_bench("pcanfd", bitrate, mode="pcan") (2026-11-19: a pick keyword
refused, the guard, then open_role: python-can's pcan by the role's device id, from `canctl roles`,
read-only, no serials). pcan_witness_bus() refuses a pick keyword (bench_layer.refuse_picks) and runs the
layer's guard, then goes by
the witness by resolve()'s device id (pcan_role, which also wants a char device,
/sys/class/pcan/<chardev>, for the witness's identity) into CanbenchPcanBus. canbench refuses
(RoleError, re-raised here) a PCAN not attached or more than one attached: the PCAN reports no
serial, so the role means the one present. The open is recorded with the role, the device id,
the char device and the channel PCAN-Basic gave.

Every open, on either backend, is recorded: one line on stderr, and, when GI_RUNS_DIR is set
(the chain runner points it at the check's folder), a JSON line in kvaser_opens.jsonl there:
the backend ("socketcan" or "canlib"), the channel asked for and opened, the bitrate, and on
socketcan the driver, the link read back and the error mask; the close's link-down too.

Every harness file opens these adapters through this module; test_kvaser_bus.py runs canbench's
bench_scan (from 2026-11-19, in place of its own scan) over gen-inhibit's tree and fails on any hit not
in its allow list, each with its reason. The tools import
it where they open the bus, not at their top, so a tool's copy in a mutant runner's scratch
folder imports without it.
"""
import importlib
import importlib.util
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin.py")
ROLES = ("tester (gen-inhibit)", "implementor (gen-inhibit)")
_fixes = None
PLATFORM = sys.platform
KVASER_ROLE = "kvaser"    # its canctl role
NETDEV = "kvaser"
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
_layer = None                                     # the pinned canbench.bench_layer, once loaded (layer())
_roles = None                                     # the pinned canbench.bench_roles, once loaded


class BenchNotHeld(RuntimeError):
    pass


class VirtualChannel(RuntimeError):
    pass


class KvaserLinkError(RuntimeError):
    """The Kvaser could not be set up, opened, or its link set or read back (bench_layer's refusal named)."""


class RoleError(RuntimeError):
    """canbench's refusal of a role (bench_roles.RoleError or bench_layer.BenchLayerError), re-raised here with
    its message, so a tool can catch it without importing the pinned canbench itself."""


class InnomakerError(RuntimeError):
    """An Innomaker role could not be handed over, opened or handed back (bench_layer's refusal named)."""


_gi_paths_mod = None                              # gi_paths, once loaded (_gi_paths())


def _gi_paths():
    """gi_paths, the module beside this file. A process that loaded this module by file need not
    have this folder on sys.path: the chain runner does, through the suite's kvaser_link_down,
    and from can-tools 0f9c85f every link_down there runs the guard, so a plain `import gi_paths`
    raised and stopped run gen-inhibit-bench_20261119T031110_ffdf at its start identify. A
    gi_paths already imported, or importable, is used as it is; otherwise it is loaded by file
    from here."""
    global _gi_paths_mod
    if _gi_paths_mod is None:
        try:
            import gi_paths as mod
        except ImportError:
            spec = importlib.util.spec_from_file_location("_kvaser_bus_gi_paths",
                                                          os.path.join(HERE, "gi_paths.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        _gi_paths_mod = mod
    return _gi_paths_mod


def _lock_holder():
    """The bench log's holder ({who, run, pid, ts}, or None when free), replayed by shared/bench's
    own vtrux_bench.bench_lock. Raises BenchNotHeld when it cannot say."""
    try:
        from vtrux_bench import bench_lock
    except ImportError as e:
        raise BenchNotHeld("vtrux_bench is not importable (%s): a real adapter opens only in a bench "
                           "run, with the pinned core's shared/bench on PYTHONPATH" % e)
    try:
        return bench_lock.read_state(_gi_paths().bench_log())["holder"]
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
    """The holder, when this process may open a real adapter; raises BenchNotHeld otherwise. Plugged into
    canbench's bench_layer as its guard (layer()), so it runs before every canctl call and open there."""
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


def bench_roles():
    """canbench.bench_roles from the pinned canbench (can_tools_pin.use()): read-only role resolution."""
    global _roles
    if _roles is None:
        _pin_tool().use()
        _roles = importlib.import_module("canbench.bench_roles")
    return _roles


def layer():
    """canbench.bench_layer from the pinned canbench (can-tools ec7cefe; the gen-inhibit reviewer's ruling of
    2026-11-19: the generic set-up, open and put-back of a bench adapter is canbench's), with require_bench
    plugged in as its guard. Every canctl call and open below goes through it."""
    global _layer
    if _layer is None:
        _pin_tool().use()
        _layer = importlib.import_module("canbench.bench_layer")
    _layer.set_guard(require_bench)
    return _layer


def _call(err, what, fn, *a, **k):
    """fn(*a, **k), with canbench's refusals (bench_roles.RoleError, bench_layer.BenchLayerError) re-raised as
    this module's err naming what; BenchNotHeld (the guard's) passes through."""
    br, bl = bench_roles(), layer()
    try:
        return fn(*a, **k)
    except (br.RoleError, bl.BenchLayerError) as e:
        raise err("%s: %s" % (what, e)) from e


def _resolve(role):
    return _call(RoleError, role, bench_roles().resolve, role)


def kvaser_role():
    """bench_roles.resolve("kvaser") in socketcan mode (netdev kvaser on kvaser_usb), read-only: for detect() and
    before every open. RoleError when canbench will not resolve it; KvaserLinkError on linuxcan's leaf, read from
    canbench's table first (bench_roles.roles), so nothing here enumerates CANlib channels or moves the Kvaser off
    leaf (it oopsed there, canctl's header 2026-11-13: that move is canctl's, by hand)."""
    br = bench_roles()
    row = _call(RoleError, KVASER_ROLE, br.roles).get(KVASER_ROLE)
    if row is not None and row.mode == "linuxcan":
        raise KvaserLinkError("the Kvaser is on linuxcan's leaf (driver %s): on Linux it opens through SocketCAN "
                              "only; `sudo -n /usr/local/sbin/canctl mode kvaser socketcan --wait` moves it"
                              % (row.driver,))
    res = _resolve(KVASER_ROLE)
    if res.mode != "socketcan" or res.netdev != NETDEV:
        raise KvaserLinkError("the Kvaser is in mode %s (netdev %s, driver %s), not socketcan with netdev %s"
                              % (res.mode, res.netdev, res.role.driver, NETDEV))
    return res


def link():
    """netdev kvaser read back (bench_layer.link_info): {up, bitrate, listen_only, restart_ms, state}."""
    bl = layer()
    return _call(KvaserLinkError, "netdev %s" % NETDEV, bl.link_info, NETDEV)


def link_down():
    """bench_layer.link_down("kvaser"): canctl down, read back not UP. KvaserLinkError otherwise."""
    bl = layer()
    return _call(KvaserLinkError, "link down %s" % KVASER_ROLE, bl.link_down, KVASER_ROLE)


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


def bus_error_class(b, can_id):
    """bench_layer.bus_error_class (the gen-inhibit reviewer, 2026-11-18): on SocketCAN only a bus-error class
    counts (not CRTL, BUSOFF, RESTARTED, nor CNT alone); on any other backend every error frame does. The
    backend is read from the bus's bench_role."""
    return layer().bus_error_class(b, can_id)


def bus_error(b, msg):
    """bench_layer.bus_error: whether msg, read from bus b, is an error frame that counts as a bus error."""
    return layer().bus_error(b, msg)


def _socketcan_bus(channel=None, bitrate=500000, driver_mode=True, **kw):
    other = sorted(set(kw) - set(SOCKETCAN_KW))
    if other:
        raise TypeError("kvaser_bus on SocketCAN takes channel, bitrate, driver_mode, %s; not %s"
                        % (", ".join(SOCKETCAN_KW), ", ".join(other)))
    if driver_mode is not True:
        raise KvaserLinkError("driver_mode=%r (CANlib's silent mode): kvaser_usb has no listen-only, "
                              "and this Leaf Light v2 has no silent mode" % (driver_mode,))
    kvaser_role()                   # RoleError / KvaserLinkError (leaf) before anything is changed
    bl = layer()
    try:
        # canbench's open_bench: the link up at bitrate (restart-ms RESTART_MS, read back), then open_role's
        # SocketcanHwBus with its per-open stamp check. restore=False: the close below takes the link down,
        # whatever it was found at.
        b = _call(KvaserLinkError, "open_bench %s" % KVASER_ROLE, bl.open_bench, KVASER_ROLE, bitrate,
                  mode="socketcan", hw_timestamps=True, restore=False, restart_ms=RESTART_MS,
                  check_frames=CHECK_FRAMES, host_gap_s=HOST_GAP_S, **kw)
    except BaseException:
        try:
            link_down()             # nothing open: not left ACKing, also when the link was found up
        except Exception:           # noqa: BLE001  the open's error is the one raised
            pass
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
    res = getattr(b, "bench_role", None)
    lay = getattr(b, "bench_layer", None) or {}
    record({"event": "open", "backend": "socketcan", "role": KVASER_ROLE, "channel_asked": channel,
            "channel": NETDEV, "bitrate": bitrate, "driver": res.role.driver if res else None, "link": link(),
            "found": lay.get("found"), "err_mask": "0x%X" % mask,
            "reader": "canbench.bench_layer.open_bench -> SocketcanHwBus", "check_frames": CHECK_FRAMES,
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


# ------------------------------------------------ the Innomakers, by canctl role (2026-11-18/19)
#
# A tool asks for an adapter by its canctl role, never by index, channel number or first found (the user's
# ruling of 2026-11-18). From 2026-11-19 (the gen-inhibit reviewer's ruling: the generic part is canbench's)
# the hand-over to libusb, the open and the hand-back are canbench's bench_layer: open_bench(role, mode=
# "libusb"), and to_libusb / to_socketcan for a run that holds the role. This module keeps the bench guard
# (plugged into the layer), the records and the role names it opens.

INNOMAKERS = ("canmain", "canchg")


def _innomaker_role(role):
    if role not in INNOMAKERS:
        raise InnomakerError("%r is not an Innomaker role (%s)" % (role, ", ".join(INNOMAKERS)))


def _innomaker_not_up(role, what):
    """The guard, then the role's link from `canctl roles` (bench_roles.roles): found up, the open is refused
    BEFORE bench_layer touches it, recorded as a finding ("found up"), and its link left as it is (the gen-inhibit
    reviewer's ruling of 2026-11-19: in a run the runner brings canmain down and the lock keeps everyone else
    off, so an up Innomaker is interference; open_bench would take it down and put it back up, and the runner
    forbids canmain up). InnomakerError."""
    bl = layer()
    bl.guard("%s %s" % (what, role))
    row = _call(InnomakerError, role, bench_roles().roles).get(role)
    if row is not None and row.link == "up":
        record({"event": "refused", "backend": "gs_usb", "role": role, "what": what, "finding": "found up",
                "netdev": row.netdev, "link": row.link})
        raise InnomakerError("%s: netdev %s found up, which is interference (in a run the runner brings it "
                             "down); recorded as a finding, its link not touched, %s refused"
                             % (role, row.netdev, what))


def _innomaker_open(role, bitrate, kw, restore):
    bl = layer()
    b = _call(InnomakerError, "open_bench %s" % role, bl.open_bench, role, bitrate, mode="libusb",
              restore=restore, **kw)
    res = getattr(b, "bench_role", None)
    b.vtrux_backend, b.vtrux_role = "gs_usb", role
    record({"event": "open", "backend": "gs_usb", "role": role, "usb": res.role.usb if res else None,
            "bus": res.busnum if res else None, "address": res.devnum if res else None, "bitrate": bitrate,
            "found": (getattr(b, "bench_layer", None) or {}).get("found"),
            "reader": "canbench.bench_layer.open_bench", "held": not restore})
    shut = b.shutdown

    def close():
        if getattr(b, "_vtrux_closed", False):
            return None
        b._vtrux_closed = True
        ev = {"event": "close", "backend": "gs_usb", "role": role, "held": not restore}
        try:
            # open_bench's shutdown: the bus, the pyusb handle freed (open_role), and with restore the role
            # put back as found (bench_layer, read back from canctl roles)
            _call(InnomakerError, "close %s" % role, shut)
        except BaseException as e:
            ev["error"] = "%s: %s" % (type(e).__name__, e)
            record(ev)
            raise
        record(ev)
        return None
    b.shutdown = close
    return b


def innomaker_bus(role, bitrate=500000, **kw):
    """The Innomaker with canctl role `role` (canmain or canchg), through bench_layer.open_bench(role,
    mode="libusb", restore=True): handed to libusb, opened by open_role, and its shutdown() puts the role back
    as found (socketcan, its link as it was), read back. NORMAL mode (gs_usb has no listen-only). A pick
    keyword (index, channel, bus, address, ...) is refused by the layer (InnomakerError). Found with its link up,
    it is refused before the layer touches it, and recorded (_innomaker_not_up)."""
    _innomaker_role(role)
    _innomaker_not_up(role, "innomaker_bus")
    return _innomaker_open(role, bitrate, kw, True)


class innomaker_held:
    """`with innomaker_held(role) as inno:` for a run that opens and closes the Innomaker more than once
    (condition 5, 2026-11-18: B9's and B10's bursts, NONODE's replayer, canmain_burst_check), in bench_layer's
    calls (the gen-inhibit reviewer, 2026-11-19): enter bench_layer.to_libusb(role); inno.open(bitrate) is
    open_bench(role, mode="libusb", restore=False), whose close is the bus's only; exit shuts any bus left
    open, then bench_layer.to_socketcan(role), read back. Each step recorded; a failed hand-back raises
    InnomakerError, also when the block raised. Kept thin, for canbench's own run hold to replace."""

    def __init__(self, role):
        self.role, self.buses, self.held = role, [], False

    def __enter__(self):
        _innomaker_role(self.role)
        _innomaker_not_up(self.role, "innomaker_held")
        bl = layer()
        _call(InnomakerError, "to_libusb %s" % self.role, bl.to_libusb, self.role)
        record({"event": "hold", "backend": "gs_usb", "role": self.role, "mode": "libusb"})
        self.held = True
        return self

    def open(self, bitrate=500000, **kw):
        if not self.held:
            raise InnomakerError("%s is not held: open it inside `with innomaker_held(%r)`" % (self.role, self.role))
        b = _innomaker_open(self.role, bitrate, kw, False)
        self.buses.append(b)
        return b

    def __exit__(self, exc_type, exc, tb):
        failed = []
        for b in self.buses:
            try:
                d = getattr(b, "echo_drain", None)
                if d is not None and d.is_alive():
                    d.stop()        # its reads end before the bus's handle is freed
                b.shutdown()
            except Exception as e:      # noqa: BLE001  named below; the hand-back still runs
                failed.append("%s: %s" % (type(e).__name__, e))
        self.held = False
        bl = layer()
        ev = {"event": "close", "backend": "gs_usb", "role": self.role, "to": "socketcan"}
        try:
            res = _call(InnomakerError, "to_socketcan %s" % self.role, bl.to_socketcan, self.role)
            ev["readback"] = "mode %s, netdev %s, link %s" % (res.mode, res.netdev, res.role.link)
        except InnomakerError as e:
            ev["error"] = str(e)
            record(ev)
            raise
        record(ev)
        if failed:
            raise InnomakerError("%s handed back, but a close failed: %s" % (self.role, "; ".join(failed)))
        return False


DRAIN_RECV_S = 0.001     # python-can's gs_usb waits at least 1 ms per empty read
DRAIN_JOIN_S = 1.0
DRAIN_SWEEP_MAX_S = 0.05  # after the stop is asked, reading what is queued ends at the first empty read, or this


class _EchoDrain(threading.Thread):
    """Reads a libusb gs_usb bus while its owner sends, so the device's echoes never fill (module docstring,
    "A SENDER ON A libusb ROLE READS ITS ECHOES")."""

    def __init__(self, bus, role, what):
        super().__init__(daemon=True)
        self.bus, self.role, self.what = bus, role, what
        self.echoes = self.rx = self.errors = 0
        self.error = None
        self._halt = threading.Event()      # not _stop: threading.Thread has its own

    def run(self):
        t_halt = None
        while True:
            if t_halt is None and self._halt.is_set():
                t_halt = time.perf_counter()
            try:
                m = self.bus.recv(timeout=DRAIN_RECV_S)
            except Exception as e:      # noqa: BLE001  recorded at stop; the owner's send shows the effect
                self.error = "%s: %s" % (type(e).__name__, e)
                return
            if t_halt is not None and (m is None or time.perf_counter() - t_halt > DRAIN_SWEEP_MAX_S):
                return      # asked to stop: what was queued is read first, to the first empty read
            if m is None:
                continue
            if m.is_error_frame:
                self.errors += 1
            elif getattr(m, "is_rx", True) is False:
                self.echoes += 1
            else:
                self.rx += 1

    def stop(self, sent=None):
        """Stops and joins the thread (before the bus's shutdown); records and returns the counts."""
        self._halt.set()
        self.join(DRAIN_JOIN_S)
        ev = {"event": "echoes", "backend": "gs_usb", "role": self.role, "what": self.what, "sent": sent,
              "echoes": self.echoes, "rx": self.rx, "errors": self.errors,
              "echoes_equal_sent": (None if sent is None else self.echoes == sent)}
        if self.is_alive():
            ev["error"] = "the drain did not stop within %g s" % DRAIN_JOIN_S
        elif self.error:
            ev["error"] = self.error
        record(ev)
        return ev


def echo_drain(bus, role, what):
    """Starts reading `bus` (a libusb gs_usb role's) so its echoes never fill; drain.stop(sent) before the bus's
    shutdown. Kept thin, for canbench's own to replace."""
    d = _EchoDrain(bus, role, what)
    bus.echo_drain = d
    d.start()
    return d


# ------------------------------------------------ the PCAN, by canctl role (2026-11-18/19)
#
# python-can's channel PCAN_USBBUS1 is the first PCAN-USB channel PCAN-Basic numbers, whichever device that
# is. So the PCAN opens by its canctl role, pcanfd: pcan_bus through bench_layer.open_bench (python-can's pcan
# by the role's device id), the witness through bench_roles.resolve's device id into the pinned canbench's
# CanbenchPcanBus (no bench_layer form of it: the gen-inhibit reviewer's ruling (C) of 2026-11-13).

PCAN_ROLE = "pcanfd"


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


def _pcan_record(b, r, bitrate, reader):
    b.vtrux_backend, b.vtrux_role, b.vtrux_device_id = "pcan", r["role"], r["device_id"]
    b.vtrux_chardev = r["chardev"]
    record({"event": "open", "backend": "pcan", "role": r["role"], "device_id": r["device_id"],
            "chardev": r["chardev"], "channel": getattr(b, "channel_info", None), "bitrate": bitrate,
            "reader": reader})
    return b


# python-can's pcan raises PCAN-Basic's bus-error statuses from recv (python-can 4.6.1:
# "Bus error: The CAN controller is error passive", "Bus error: an error counter reached the
# 'heavy'/'warning' limit", ...). Each is a state change, not a lost frame and not an end.
PCAN_BUS_STATE = re.compile(r"^Bus error: ")


def _read_on_bus_states(b):
    """Every PCAN reader in this harness reads on past a bus state (gen-inhibit reviewer's F6,
    2026-11-19: run cb08's B9, B10 and B14 PCAN readers died of "error passive", as
    canmain_burst_check's witness had before 45f0332d). The bus's recv records each bus state
    raised, (perf_counter, text), in b.vtrux_states and returns None for that read, after 1 ms;
    any other exception is raised as before."""
    raw = b.recv
    b.vtrux_states = []

    def recv(timeout=None):
        try:
            return raw(timeout=timeout)
        except Exception as e:      # noqa: BLE001  only a bus state is kept; anything else is re-raised
            if not PCAN_BUS_STATE.match(str(e)):
                raise
            b.vtrux_states.append((time.perf_counter(), "%s: %s" % (type(e).__name__, e)))
            time.sleep(0.001)
            return None
    b.recv = recv
    return b


def pcan_bus(bitrate=500000, **kw):
    """The PCAN by its canctl role pcanfd: bench_layer.open_bench("pcanfd", bitrate) (the guard, then
    open_role: python-can's pcan by the role's device id) with the caller's other arguments. A pick keyword
    (channel, device_id, interface, ...) is refused by the layer (RoleError). The open is recorded. Its recv
    reads on past a bus state, kept in bus.vtrux_states (_read_on_bus_states)."""
    bl = layer()
    b = _call(RoleError, "open_bench %s" % PCAN_ROLE, bl.open_bench, PCAN_ROLE, bitrate, mode="pcan", **kw)
    res = getattr(b, "bench_role", None)
    r = {"role": PCAN_ROLE, "device_id": res.kwargs.get("device_id") if res else None,
         "chardev": res.role.chardev if res else None}
    return _read_on_bus_states(_pcan_record(b, r, bitrate, "canbench.bench_layer.open_bench"))


def pcan_witness_bus(bitrate=500000, **kw):
    """The PCAN as a timing witness (2026-11-13, the gen-inhibit reviewer's ruling (C)): a pick keyword refused
    (bench_layer.refuse_picks), the bench guard, the role resolved (pcan_role), then the pinned canbench's
    CanbenchPcanBus on that device id, which hands up PCAN-Basic's status messages and bus states as
    controller-state error frames instead of data frames or an exception (python-can 4.6.1 raises on a
    bus-passive Read). canbench_busoff_reset=False: a witness never resets its own controller, so its device
    clock runs on through the session (a reset would also restart it)."""
    bl = layer()
    _call(RoleError, "pcan_witness_bus", bl.refuse_picks, kw, "pcan_witness_bus")
    bl.guard("pcan_witness_bus")
    r = pcan_role()
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
