"""Virtual CAN bus for the gen-inhibit rehearsal rigs, confined to this host.

Gen-inhibit tester, 2026-10-08. A trimmed copy of the charge interposer's
bus.py (charge-interposer/bus.py in canbus-esp-fw-vtrux), made when this
folder moved into the firmware repo and could no longer load the interposer's.
Only the virtual transport is kept: its callers are bench_vehicle.py,
live_bench.py and vehicle_runner.py's --interface udp_multicast, and nothing
here opens a real adapter (those are opened with python-can directly).

WHY IT EXISTS. python-can's udp_multicast transport defaults to hop_limit=1,
and the OS routes 239.0.0.0/8 out the default interface, so every simulated
frame goes on the real LAN. On 2026-09-01 a bench did exactly that at 66,450
packets/s and took the house network down. hop_limit=0 keeps the datagrams on
this host; _confine_to_host re-asserts it and reads it back.

Two differences from the original, both deliberate:
  - _confine_to_host FAILS CLOSED. The original returned silently when it
    could not find the socket, so a python-can release that renamed the
    private attribute it reads would have switched the guard off unannounced.
  - Its refusals point to firmware/esp-development.md, where the rules live.

Proof by running: test_gi_vbus.py.
"""

import socket
import struct
import sys

import can

GUIDE = ("See 'Simulators, Virtual CAN Buses, and Background Processes' in "
         "firmware/esp-development.md.")


def open_bus(args, *, prefix="", default_channel=None,
             receive_own_messages=False, logger=None):
    """Open a host-confined udp_multicast bus.

    Same call shape as the interposer's bus.open_bus: `args` carries
    <prefix>transport/channel/port (and bitrate, ignored here). Any transport
    other than 'virtual' is refused rather than guessed at.
    """
    d = prefix.replace("-", "_")
    transport = getattr(args, d + "transport")
    channel = getattr(args, d + "channel") or default_channel
    port = getattr(args, d + "port")
    if transport != "virtual":
        raise SystemExit("gi_vbus opens only the virtual transport, not %r. "
                         "Open a real adapter with python-can directly."
                         % transport)
    if not channel or port is None:
        raise SystemExit("gi_vbus: a virtual bus needs a multicast group and "
                         "a port (got %r, %r)" % (channel, port))
    b = can.interface.Bus(interface="udp_multicast", channel=channel,
                          port=port, hop_limit=0,
                          receive_own_messages=receive_own_messages)
    try:
        ttl = _confine_to_host(b)
    except BaseException:
        try:
            b.shutdown()
        except Exception:
            pass                # the refusal, not a cleanup error, is the news
        raise
    _enlarge_rx_buffer(b)
    # Printed on every open, so each run's output carries its own proof.
    print("gi_vbus: %s:%d multicast TTL read back %d -- confined to this host"
          % (channel, port, ttl), file=sys.stderr, flush=True)
    if logger:
        logger.debug("multicast TTL=0 verified on %s:%d", channel, port)
    return b


def _socket_of(b):
    return getattr(getattr(b, "_multicast", None), "_socket", None)


def _confine_to_host(b):
    """Set TTL 0 and VERIFY it by reading it back. Returns the TTL read (0).

    Raises SystemExit if the socket cannot be found, the option cannot be
    set, or the read-back is not 0. Refusing to start is strictly better than
    running and flooding.
    """
    sock = _socket_of(b)
    if sock is None:
        raise SystemExit(
            "refusing to start: cannot find the udp_multicast socket on %s "
            "(python-can %s), so its multicast TTL cannot be verified.\n"
            "Without that check every simulated CAN frame may be transmitted "
            "on the real network. %s"
            % (type(b).__name__, getattr(can, "__version__", "?"), GUIDE))
    try:
        # TTL 0 only. Do NOT also pin IP_MULTICAST_IF to loopback: python-can
        # joins the group with INADDR_ANY, so sending via another interface
        # means the receivers never see anything.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                        struct.pack("@I", 0))
    except OSError as e:
        raise SystemExit(
            "refusing to start: could not set IP_MULTICAST_TTL to 0 (%s).\n"
            "Without it every simulated CAN frame is transmitted on the real "
            "network. %s" % (e, GUIDE))
    ttl = sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
    if ttl != 0:
        raise SystemExit(
            "refusing to start: multicast TTL is %d, not 0. Every simulated "
            "CAN frame would be\nput on the real network (239.0.0.0/8 routes "
            "out the default interface). %s" % (ttl, GUIDE))
    return ttl


def _enlarge_rx_buffer(b, want=16 * 1024 * 1024):
    """Ask for a large receive buffer; bursts otherwise drop frames silently."""
    sock = _socket_of(b)
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, want)
    except OSError:
        pass
