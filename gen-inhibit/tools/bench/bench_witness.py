"""The two witnesses of the jammer pass on madhouse after the Kvaser moved to the kernel's
kvaser_usb (the gen-inhibit reviewer's ruling (C), 2026-11-13).

Gen-inhibit tester, 2026-11-13. The Kvaser on kvaser_usb gives no hardware stamps (the
probe of 10:07-10:10: 2987 frames, ts[2] 0 on every one) and refuses berr-reporting, so:
  PcanWitness       the PCAN carries the TIMED checks (boot silence, pulses with the port
                    check and calibration, the timeout set, no duration, after-DONE). Its
                    stamps are its device time; x2_jammer's session places them on the epoch
                    clock with witness_anchor (one anchor per open), so this class only
                    records (device, receipt) pairs and says what it opened. A controller-state
                    frame (CAN_ERR_CRTL warning or passive, CAN_ERR_BUSOFF) puts it down from
                    its receipt (t_fail): in session 1 of the jammer pass (2026-11-13) the PCAN
                    went error passive ~128 pulses into a train and reported nothing after.
  SocketcanWitness  the Kvaser, on SocketCAN, for can2-isolation only: that check COUNTS (every
                    0x7A0 seen, one error group per pulse) and times nothing, so software
                    stamps are enough, and they are used only for the gaps between frames in
                    groups(), never against a window or a margin.
The PCAN is the 0x7A0 sender in can2-isolation, so the two are never open as witnesses at once
(x2_jammer refuses can2-isolation in a session, and can2-isolation refuses while another
process holds the PCAN: pcan_holders()).

Both read in a thread. A read that raises ends the thread with the exception kept, and down()
then says why; x2_jammer turns it into WitnessDown (NOT-RUN), because a record cut short reads
as a quiet bus (2026-11-13). Only bus-error frames are counted (CAN_ERR_PROT set: the jammer's
stuff errors, 0x288 on kvaser_usb, the PCAN's error messages as canbench translates them);
controller-state and counter-only frames are kept apart, and canbench stamps some of those with
the last data frame's time, so they never anchor anything.

  PcanWitness(opener=None, sysfs=PCAN_SYSFS)
                    refusals before the thread starts (WitnessRefused): the sysfs device is not a
                    PCAN-USB adapter; the bus opened is not the named channel PCAN_USBBUS1 of a
                    PCAN bus class (no virtual bus); ts_mode after the open is not 3 (raw device
                    time; RAW libpcanbasic sets it at each open); nom_bitrate not 500000.
                    identity_open / identity_close (adapter, dev, USB device path, ts_mode):
                    same_device() compares them, for the anchor's replug refusal.
  SocketcanWitness(iface="kvaser", sysfs_net=..., sock=None)
                    netdev_why() first: only `kvaser`, a CAN netdev (type 280) with a device
                    whose driver is kvaser_usb (a vcan has no device), operstate UP (brought up
                    by canctl, never here); then a raw socket with every error class and
                    SO_TIMESTAMPING software stamps. Each frame is logged with the phase the
                    caller set when it was read (`phase`), so a check selects frames by what
                    it was doing, not by stamp.
  groups(sw)        software stamps -> groups split at gaps over GROUP_GAP_S
  pcan_holders(proc="/proc", me=None)
                    pids other than this process (and its ancestors) with a /dev/pcan* open
"""
import errno
import os
import socket
import struct
import threading
import time

BITRATE = 500000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_FLAG = 0x80000000
CAN_ERR_MASK = 0x1FFFFFFF
CAN_ERR_PROT = 0x008
CAN_ERR_CRTL = 0x004     # controller state: error warning, error passive
CAN_ERR_BUSOFF = 0x040
STATE_BITS = CAN_ERR_CRTL | CAN_ERR_BUSOFF

PCAN_CHANNEL = "PCAN_USBBUS1"
PCAN_SYSFS = "/sys/class/pcan/pcanusbfd32"
PCAN_TS_MODE = "3"

KVASER_IFACE = "kvaser"
KVASER_DRIVER = "kvaser_usb"
ARPHRD_CAN = "280"
SO_TIMESTAMPING = 37
SCM_TIMESTAMPING = 37
SOF_SW = (1 << 3) | (1 << 4)            # RX_SOFTWARE | SOFTWARE
CAN_RAW_ERR_FILTER = 2
FRAME = "=IB3x8s"

# The grouping gap for the software stamps (ruling (C) condition: fixed here, at least 10x the
# stamps' jitter and well under the pulse spacing). Measured on madhouse in the 2026-11-13
# probe: receipt minus software stamp median 0.21 ms, max 1.08 ms over 2987 frames; pulse
# spacing 0.999958-1.000023 s at 1000 ms. 0.1 s is over 90x the 1.08 ms worst case, and
# x2_jammer refuses can2-isolation with --interval-ms under MIN_INTERVAL_MS (5x this gap).
SW_JITTER_MAX_S = 0.00108
GROUP_GAP_S = 0.1
MIN_INTERVAL_MS = 500


class WitnessRefused(RuntimeError):
    """The witness would not open: what it found is not the bench's named adapter."""


def is_bus_error(can_id):
    return bool(can_id & CAN_ERR_PROT)


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def pcan_identity(sysfs=PCAN_SYSFS):
    dev = os.path.join(sysfs, "device")
    return {"adapter_name": _read(os.path.join(sysfs, "adapter_name")),
            "dev": _read(os.path.join(sysfs, "dev")),
            "ts_mode": _read(os.path.join(sysfs, "ts_mode")),
            "nom_bitrate": _read(os.path.join(sysfs, "nom_bitrate")),
            "usb_path": os.path.realpath(dev) if os.path.exists(dev) else None}


def pcan_why(ident, bus):
    """Why the opened PCAN is refused as the witness, or None."""
    if not ident.get("adapter_name") or not ident["adapter_name"].startswith("PCAN-USB"):
        return "the sysfs device is %r, not a PCAN-USB adapter" % ident.get("adapter_name")
    if not ident.get("usb_path"):
        return "the PCAN's sysfs entry has no USB device"
    cls = type(bus).__name__
    if "Pcan" not in cls:
        return "the bus opened is a %s, not a PCAN bus" % cls
    if getattr(bus, "channel_info", None) != PCAN_CHANNEL:
        return "the bus opened is channel %r, not %s" % (getattr(bus, "channel_info", None), PCAN_CHANNEL)
    if ident.get("ts_mode") != PCAN_TS_MODE:
        return "ts_mode %r after the open, not %s (raw device time)" % (ident.get("ts_mode"), PCAN_TS_MODE)
    if ident.get("nom_bitrate") != str(BITRATE):
        return "nom_bitrate %r, not %d" % (ident.get("nom_bitrate"), BITRATE)
    return None


class PcanWitness(threading.Thread):
    """The PCAN in NORMAL mode for one x2_jammer session (module doc)."""

    name_ = "pcan"

    def __init__(self, opener=None, sysfs=PCAN_SYSFS):
        super().__init__(daemon=True)
        self.sysfs = sysfs
        if opener is None:
            from kvaser_bus import pcan_witness_bus as opener   # noqa: E402  the guard, the pin
        self.bus = opener(channel=PCAN_CHANNEL, bitrate=BITRATE)
        self.opens = 1
        self.identity_open = pcan_identity(sysfs)
        why = pcan_why(self.identity_open, self.bus)
        if why:
            try:
                self.bus.shutdown()
            finally:
                raise WitnessRefused(why)
        self.identity_close = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.err = []         # (device_s, receipt_epoch): bus-error frames
        self.other_err = []   # (device_s, receipt_epoch, can_id hex): state / counter frames
        self.frames = 0
        self.ids = {}
        self.t_open = time.time()
        self.failed = None

    def run(self):
        try:
            while not self.stop_flag.is_set():
                m = self.bus.recv(timeout=0.05)
                while m is not None:
                    now = time.time()
                    with self.lock:
                        if m.is_error_frame:
                            if is_bus_error(m.arbitration_id):
                                self.err.append((m.timestamp, now))
                            else:
                                self.other_err.append((m.timestamp, now, "0x%03X" % m.arbitration_id))
                            if m.arbitration_id & STATE_BITS and self.failed is None:
                                # a controller-state frame: from here the PCAN may stop reporting
                                # bus errors (error passive after ~128 pulses in session 1,
                                # 2026-11-13), so the witness is down from this moment
                                self.failed = (now, "controller-state frame 0x%03X data %s" % (
                                    m.arbitration_id, bytes(getattr(m, "data", b"") or b"").hex()))
                        else:
                            self.frames += 1
                            k = "0x%03X" % m.arbitration_id
                            self.ids[k] = self.ids.get(k, 0) + 1
                    m = self.bus.recv(timeout=0.0)
        except Exception as e:      # noqa: BLE001  kept; every judgement refuses on it
            if self.failed is None:
                self.failed = (time.time(), "%s: %s" % (type(e).__name__, e))

    def down(self):
        if self.failed:
            return "witness down at %.3f: %s" % self.failed
        if not self.stop_flag.is_set() and not self.is_alive():
            return "witness thread not running"
        return None

    @property
    def t_fail(self):
        """When the witness went down (receipt time), or None."""
        return self.failed[0] if self.failed else None

    def close(self):
        self.stop_flag.set()
        self.join(timeout=2.0)
        self.identity_close = pcan_identity(self.sysfs)
        self.bus.shutdown()

    def same_device(self):
        a, b = self.identity_open, self.identity_close or {}
        return all(a.get(k) == b.get(k) for k in ("adapter_name", "dev", "usb_path"))

    def points(self):
        with self.lock:
            return list(self.err)

    def describe(self):
        return {"name": "pcan", "adapter": self.identity_open.get("adapter_name"),
                "channel": PCAN_CHANNEL, "mode": "normal (ACKing)",
                "stamps": "device time (ts_mode %s), placed by the session anchor" % self.identity_open.get("ts_mode"),
                "identity_open": self.identity_open, "identity_close": self.identity_close}


# ------------------------------------------------------------ the Kvaser on SocketCAN

def netdev_why(iface, sysfs_net="/sys/class/net"):
    """Why `iface` is refused as the can2-isolation witness, or None."""
    if iface != KVASER_IFACE:
        return "only the bench's Kvaser netdev %r is a witness, not %r" % (KVASER_IFACE, iface)
    base = os.path.join(sysfs_net, iface)
    if not os.path.isdir(base):
        return "no netdev %r" % iface
    if _read(os.path.join(base, "type")) != ARPHRD_CAN:
        return "%r is not a CAN netdev (type %s)" % (iface, _read(os.path.join(base, "type")))
    drv = os.path.join(base, "device", "driver")
    if not os.path.exists(drv):
        return "%r has no device: a virtual netdev (vcan or the like)" % iface
    name = os.path.basename(os.path.realpath(drv))
    if name != KVASER_DRIVER:
        return "%r is driven by %r, not %s" % (iface, name, KVASER_DRIVER)
    if _read(os.path.join(base, "operstate")) != "up":
        return "%r is %s, not up (canctl brings it up)" % (iface, _read(os.path.join(base, "operstate")))
    return None


def open_raw(iface):
    s = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.setsockopt(socket.SOL_CAN_RAW, CAN_RAW_ERR_FILTER, struct.pack("=I", CAN_ERR_MASK))
    s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPING, SOF_SW)
    s.bind((iface,))
    s.settimeout(0.05)
    return s


def parse(data, anc):
    """(can_id with flags, data bytes, software stamp or None) from one recvmsg."""
    cid, dlc, payload = struct.unpack(FRAME, data[:16])
    sw = None
    for lvl, typ, val in anc:
        if lvl == socket.SOL_SOCKET and typ == SCM_TIMESTAMPING and len(val) >= 16:
            s, ns = struct.unpack("=2q", val[:16])
            sw = (s + ns / 1e9) if (s or ns) else None
    return cid, payload[:dlc], sw


class SocketcanWitness(threading.Thread):
    """The Kvaser on kvaser_usb, normal mode (ACKing), for can2-isolation (module doc)."""

    def __init__(self, iface=KVASER_IFACE, sysfs_net="/sys/class/net", sock=None):
        super().__init__(daemon=True)
        why = netdev_why(iface, sysfs_net)
        if why:
            raise WitnessRefused(why)
        self.iface = iface
        self.sock = sock if sock is not None else open_raw(iface)
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.phase = "open"
        self.log = []         # (phase, receipt_epoch, sw or None, can_id with flags, data hex)
        self.failed = None

    def run(self):
        try:
            while not self.stop_flag.is_set():
                try:
                    data, anc, _fl, _ad = self.sock.recvmsg(16, 256)
                except socket.timeout:
                    continue
                now = time.time()
                cid, payload, sw = parse(data, anc)
                with self.lock:
                    self.log.append((self.phase, now, sw, cid, payload.hex()))
        except OSError as e:
            if not self.stop_flag.is_set():
                self.failed = (time.time(), "%s: %s" % (errno.errorcode.get(e.errno, e.errno), e))
        except Exception as e:      # noqa: BLE001  kept; every judgement refuses on it
            self.failed = (time.time(), "%s: %s" % (type(e).__name__, e))

    def down(self):
        if self.failed:
            return "witness read failed at %.3f: %s" % self.failed
        if not self.stop_flag.is_set() and not self.is_alive():
            return "witness thread not running"
        return None

    def close(self):
        self.stop_flag.set()
        self.join(timeout=2.0)
        self.sock.close()

    def frames(self, phases):
        """The log's entries read while the caller was in one of `phases`."""
        with self.lock:
            return [e for e in self.log if e[0] in phases]

    def describe(self):
        return {"name": "kvaser", "netdev": self.iface, "driver": KVASER_DRIVER,
                "mode": "normal (ACKing), through canctl", "stamps": "software (SO_TIMESTAMPING), "
                "for gaps in groups() only; nothing is judged against a window on them",
                "berr": "off: kvaser_usb_flex refuses berr-reporting (canctl up kvaser 500000 berr, "
                        "RTNETLINK Operation not supported, 2026-11-13); error frames arrive without it",
                "group_gap_s": GROUP_GAP_S}


def groups(sw):
    """Software stamps -> groups, split where the gap is over GROUP_GAP_S."""
    out = []
    for t in sorted(sw):
        if out and t - out[-1][-1] <= GROUP_GAP_S:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _ancestors(pid):
    out = []
    while pid > 0 and pid not in out:
        out.append(pid)
        st = _read("/proc/%d/stat" % pid)
        if not st:
            break
        pid = int(st.rsplit(")", 1)[1].split()[1])
    return out


def pcan_holders(proc="/proc", me=None):
    """pids, other than this process and its ancestors, with a /dev/pcan* device open."""
    skip = set(_ancestors(os.getpid() if me is None else me))
    out = []
    for d in os.listdir(proc):
        if not d.isdigit() or int(d) in skip:
            continue
        fd = os.path.join(proc, d, "fd")
        try:
            names = os.listdir(fd)
        except OSError:
            continue
        for n in names:
            try:
                tgt = os.readlink(os.path.join(fd, n))
            except OSError:
                continue
            if tgt.startswith("/dev/pcan"):
                out.append(int(d))
                break
    return sorted(out)
