"""CAN transport factory for the Vtrux charge-interposer simulators.

Mirrors projects/coda/notes/dlcm/flash_sim/bus.py so the two benches behave the
same way.

Two transport families:

  virtual : python-can 'udp_multicast'. Processes joining the same group/port
            see each other's frames. No hardware, no admin rights. Each CAN
            *segment* gets its own multicast group, so the interposer really
            does sit between two separate buses.

  real    : a physical dongle brand (ixxat / kvaser / pcan / socketcan /
            gs_usb), channel auto-detected via canbench.live.receiver, so the
            same simulator code drives real silicon once the board exists.

Segment map for this bench:

    vehicle segment  (VCU + A123 BMS  <-> interposer)   239.0.2.1
    charger segment  (interposer      <-> Bel charger)  239.0.2.2

A plain two-node run (no interposer) puts both nodes on the vehicle segment.
"""

import sys
from pathlib import Path

import can

# canre's location comes from paths.py (VTRUX_DATA) since the move to git;
# nothing here imports canre, so a missing setting is not an error.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths                                                # noqa: E402

REPO_ROOT = paths.add_canre(required=False)

VEHICLE_SEGMENT = "239.0.2.1"
CHARGER_SEGMENT = "239.0.2.2"

# The two segments differ by PORT as well as by group, and that is not
# cosmetic. Verified on this build of python-can/Linux: two udp_multicast buses
# sharing a UDP port SEE EACH OTHER'S TRAFFIC even when they join different
# multicast groups -- the socket is bound to the port and receives every group
# joined on it. Group separation alone is not isolation.
#
# With a shared port the charger hears the VCU directly, the interposer is
# bypassed, and the charger sees both the original and the rewritten command --
# which shows up as the charger's mode flapping. Every result from such a run is
# meaningless. assert_segments_isolated() below refuses to let that happen
# silently.
VEHICLE_PORT = 43213
CHARGER_PORT = 43214

# The HV cable, not a CAN bus. The charger and the pack are connected by copper
# that the interposer does not sit in, so the simulated pack must be charged by
# a physical quantity rather than by a CAN telemetry frame. Without this the
# bench has a nasty conceptual hole: the interposer could "charge the battery"
# by rewriting the charger's reported current, and --stealth (which zeroes
# exactly that telemetry) would appear to stop the charge dead.
#
# Carried as ordinary frames on their own segment so no extra transport is
# needed. These IDs exist only inside the bench.
PHYSICS_SEGMENT = "239.0.2.9"
PHYSICS_PORT = 43215
PHYS_CURRENT_ID = 0x7F0    # charger -> pack: delivered DC current
PHYS_VOLTAGE_ID = 0x7F1    # pack -> charger: terminal voltage
VIRTUAL_DEFAULT_PORT = VEHICLE_PORT

REAL_BRANDS = ("ixxat", "kvaser", "pcan", "socketcan", "gs_usb")


# Wall time a sim process spent FROZEN must not become simulated time.
#
# Measured 2026-09-12 on L-EEHTRBPN2 (Windows): the machine suspends every
# process for 40-70 ms, occasionally over 100 ms, at random -- charger_sim and
# vehicle_sim logged 62 ms and 58 ms stalls in the SAME second with zero GC
# collections, and a 0.5 ms sleep took 28 ms. Nothing in the bench causes it
# and nothing in the bench can prevent it. Left alone, an accelerated run turns
# each freeze into --time-scale times as much simulated silence, and the
# interposer -- whose clock is wall-derived -- trips "frames stale" on frames
# that were never late in simulated time. The real board never sees this: its
# millis() does not freeze, which is why hardware runs were always clean.
#
# So every sim clamps a single loop gap to FREEZE_TOLERANCE_S: anything beyond
# it is treated as the bench's wall clock having paused. Freezes are near-
# simultaneous across processes, so each one subtracts about the same amount
# and the three clocks stay together. The stall detectors still log the raw
# gap, so a freeze is visible even though it no longer counts. A loop that is
# merely slow under load is clamped too -- the sim runs slightly slow rather
# than skipping frames -- and sustained overload still shows up as
# interposer_sim's "input saturated" warning. Set to 0 to disable.
FREEZE_TOLERANCE_S = 0.020


def unfreeze(dt_wall):
    """The part of a loop gap that should count as simulated time."""
    if FREEZE_TOLERANCE_S and dt_wall > FREEZE_TOLERANCE_S:
        return FREEZE_TOLERANCE_S
    return dt_wall


def install_break_handler():
    """Make CTRL_BREAK_EVENT behave like SIGINT on Windows.

    run_scenario.py cannot send SIGINT to a child on Windows, so it sends
    CTRL_BREAK_EVENT to the child's process group instead. But Python installs
    no handler for SIGBREAK: the C runtime's default simply terminates the
    process, so no KeyboardInterrupt is raised, no `finally` runs, and every
    sim's shutdown path -- interposer_sim's "final state=" line included -- is
    skipped. That is why final_state() expectations never passed on the
    emulated bench on Windows.

    "Behave like SIGINT" means dispatching to WHATEVER handler SIGINT has at
    the moment the break arrives -- not raising KeyboardInterrupt. The two
    sender sims keep Python's default (KeyboardInterrupt, which they catch),
    but interposer_sim installs its own `stop` that clears a running flag and
    never expects an exception; raising into it produced a traceback in the
    log, which run_scenario counts as a crash. Resolving the SIGINT handler at
    signal time, rather than when this is installed, also means it does not
    matter whether a sim registers its handler before or after calling this.
    No-op off Windows.
    """
    import signal
    if not hasattr(signal, "SIGBREAK"):
        return

    def _as_sigint(signum, frame):
        handler = signal.getsignal(signal.SIGINT)
        if callable(handler):
            handler(signal.SIGINT, frame)
        else:
            raise KeyboardInterrupt
    signal.signal(signal.SIGBREAK, _as_sigint)


def add_bus_args(parser, *, default_channel=VEHICLE_SEGMENT, prefix="",
                 help_role="the bus", default_port=None):
    """Add transport CLI options. `prefix` namespaces them for a two-bus node.

    prefix="" gives --transport/--channel/--port/--bitrate.
    prefix="vehicle-" gives --vehicle-transport/--vehicle-channel/...
    """
    p = prefix
    dest = prefix.replace("-", "_")
    parser.add_argument(
        "--%stransport" % p, dest=dest + "transport", default="virtual",
        choices=("virtual",) + REAL_BRANDS,
        help="transport for %s: 'virtual' = localhost udp_multicast "
             "(no hardware); otherwise a real dongle brand" % help_role)
    parser.add_argument(
        "--%schannel" % p, dest=dest + "channel", default=None,
        help="virtual: multicast group IP (default %s); "
             "real: dongle channel (default: first auto-detected)"
             % default_channel)
    if default_port is None:
        default_port = {CHARGER_SEGMENT: CHARGER_PORT,
                        PHYSICS_SEGMENT: PHYSICS_PORT}.get(default_channel,
                                                           VEHICLE_PORT)
    parser.add_argument(
        "--%sport" % p, dest=dest + "port", type=int, default=default_port,
        help="virtual transport UDP port (default %d). The two segments MUST "
             "use different ports -- see the note in bus.py." % default_port)
    parser.add_argument(
        "--%sbitrate" % p, dest=dest + "bitrate", type=int, default=500000,
        help="real transport CAN bitrate (bps); ignored for virtual")
    parser._interposer_bus_defaults = getattr(
        parser, "_interposer_bus_defaults", {})
    parser._interposer_bus_defaults[dest] = default_channel


def open_bus(args, *, prefix="", default_channel=VEHICLE_SEGMENT,
             receive_own_messages=False, logger=None):
    """Open a python-can Bus from parsed args (see add_bus_args)."""
    d = prefix.replace("-", "_")
    transport = getattr(args, d + "transport")
    channel = getattr(args, d + "channel")
    port = getattr(args, d + "port")
    bitrate = getattr(args, d + "bitrate")

    if transport == "virtual":
        channel = channel or default_channel
        if logger:
            logger.info("Opening virtual bus (udp_multicast %s:%d)", channel, port)
        # hop_limit=0 is NOT optional. python-can defaults it to 1, which puts
        # every frame on the wire: the kernel routes 239.0.0.0/8 out the
        # default interface, so an accelerated run floods the real LAN with
        # tens of thousands of multicast datagrams a second. Multicast is
        # flooded to every switch port unless IGMP snooping catches it, and
        # WiFi APs send it at the lowest basic rate, so this takes down a home
        # network. TTL 0 means the datagram is looped back to local sockets and
        # never transmitted on any interface.
        b = can.interface.Bus(
            interface="udp_multicast", channel=channel, port=port,
            hop_limit=0, receive_own_messages=receive_own_messages)
        _confine_to_host(b, logger)
        _enlarge_rx_buffer(b, logger)
        return b

    from canbench.live.receiver import detect_all_can_interfaces
    if logger:
        logger.info("Detecting %s interfaces at %d bps...", transport, bitrate)
    interfaces = detect_all_can_interfaces(bitrate, brands={transport})
    matches = [i for i in interfaces if i[0] == transport]
    if not matches:
        raise RuntimeError("no %s interface detected" % transport)
    iface, ch, desc = matches[0]
    if channel is not None:
        ch = channel
    if logger:
        logger.info("Using real interface: %s", desc)

    kwargs = dict(bitrate=bitrate, receive_own_messages=receive_own_messages)
    if iface == "pcan":
        # see canbench/live/pcan-busoff-recovery.md
        kwargs["auto_reset"] = True
    if isinstance(ch, dict):
        bus = can.interface.Bus(interface=iface, **kwargs, **ch)
    else:
        bus = can.interface.Bus(interface=iface, channel=ch, **kwargs)
    if iface == "pcan":
        try:
            bus.reset()
        except Exception as e:
            if logger:
                logger.debug("pcan reset() at startup failed (non-fatal): %s", e)
    return bus


def _confine_to_host(b, logger=None):
    """Re-assert TTL 0 and VERIFY it. Raises if the traffic could reach the LAN.

    This is a hard failure on purpose. A virtual CAN bench that silently ends up
    on the wire pushes tens of thousands of multicast packets a second onto the
    real network -- measured at 66,450 pps / 13.6 MB/s here on 2026-09-01, which
    took the house network down. Refusing to start is strictly better than
    running and flooding.
    """
    import socket
    import struct
    sock = getattr(getattr(b, "_multicast", None), "_socket", None)
    if sock is None:
        return
    try:
        # TTL 0 only. Do NOT also pin IP_MULTICAST_IF to loopback: python-can
        # joins the group with INADDR_ANY, which the kernel resolves to the
        # default multicast interface, so sending via a different interface
        # means the receivers never see anything. TTL 0 already guarantees the
        # datagram is looped back locally and never put on the wire.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                        struct.pack("@I", 0))
    except OSError as e:
        raise SystemExit(
            "refusing to start: could not set IP_MULTICAST_TTL to 0 (%s).\n"
            "Without it every simulated CAN frame is transmitted on the real "
            "network." % e)
    ttl = sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
    if ttl != 0:
        raise SystemExit(
            "refusing to start: multicast TTL is %d, not 0. Every simulated "
            "CAN frame would be\nput on the real network (239.0.0.0/8 routes "
            "out the default interface). See the\n'Simulators, Virtual CAN "
            "Buses' section of AGENTS.md." % ttl)
    if logger:
        logger.debug("multicast TTL=0 verified -- traffic confined to this host")


def _enlarge_rx_buffer(b, logger=None, want=16 * 1024 * 1024):
    """Give the virtual bus the biggest receive buffer the kernel allows.

    An accelerated run pushes thousands of datagrams a second; the default
    ~200 KB socket buffer overflows in bursts and the kernel drops frames
    silently, which shows up downstream as spurious staleness trips. The kernel
    caps this at net.core.rmem_max, so it raises the ceiling rather than
    removing it -- keep --time-scale within what the loop can retire.
    """
    import socket
    sock = getattr(getattr(b, "_multicast", None), "_socket", None)
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, want)
        if logger:
            logger.debug("rx buffer: %d bytes",
                         sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
    except OSError as e:
        if logger:
            logger.debug("could not enlarge rx buffer (non-fatal): %s", e)


class EchoSuppressor:
    """Drop a node's own frames when they loop back on the virtual transport.

    python-can's udp_multicast backend does NOT honour receive_own_messages: a
    socket always sees what it transmitted (multicast loopback). A node that
    only reads specific IDs tolerates this, but the interposer forwards
    everything, so each frame it emits echoes back and is re-forwarded to the
    other segment -- an infinite bridge loop.

    Records (arbitration_id, is_extended, data) for every frame sent onto a
    segment and drops the first byte-identical frame read back from it. Engaged
    only on the virtual transport; real dongles honour receive_own_messages.

    Same design as flash_sim's proxy suppressor. See that README for the
    original write-up of the failure mode.
    """

    __slots__ = ("_pending", "_max")

    def __init__(self, max_pending=4096):
        self._pending = {}
        self._max = max_pending

    def sent(self, msg):
        key = (msg.arbitration_id, bool(msg.is_extended_id), bytes(msg.data))
        self._pending[key] = self._pending.get(key, 0) + 1
        if len(self._pending) > self._max:
            self._pending.clear()

    def is_echo(self, msg):
        key = (msg.arbitration_id, bool(msg.is_extended_id), bytes(msg.data))
        n = self._pending.get(key)
        if not n:
            return False
        if n == 1:
            del self._pending[key]
        else:
            self._pending[key] = n - 1
        return True


def encode_phys(value, scale=100.0):
    """Signed 32-bit fixed point on the physics link."""
    v = int(round(value * scale))
    v &= 0xFFFFFFFF
    return bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF,
                  0, 0, 0, 0))


def decode_phys(data, scale=100.0):
    v = data[0] | (data[1] << 8) | (data[2] << 16) | (data[3] << 24)
    if v & 0x80000000:
        v -= 1 << 32
    return v / scale


def assert_segments_isolated(args):
    """Refuse to run a virtual bench whose two segments are not isolated.

    Two udp_multicast buses on the same UDP port see each other's frames
    regardless of multicast group (see the note at the top of this module), so
    a shared port silently bypasses the interposer. This is a hard error, not a
    warning: a run that looks fine and proves nothing is worse than a crash.
    """
    if args.vehicle_transport != "virtual" or args.charger_transport != "virtual":
        return
    if args.vehicle_port == args.charger_port:
        raise SystemExit(
            "refusing to start: --vehicle-port and --charger-port are both %d.\n"
            "On the virtual transport, two buses sharing a UDP port receive "
            "each other's\ntraffic even on different multicast groups, so the "
            "charger would hear the VCU\ndirectly and the interposer would be "
            "bypassed. Use different ports (defaults\n%d and %d)."
            % (args.vehicle_port, VEHICLE_PORT, CHARGER_PORT))
