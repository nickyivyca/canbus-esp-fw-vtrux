#!/usr/bin/env python3
"""Every real bench adapter the harness opens, opened by its canctl role,
through can-tools' bench-role layer.

Charge-interposer tester, 2026-11-18: shared/bench SPEC section 8's adapter
roles (user, 2026-11-18, the whole bench; shared/main b4e95179, tag
2026.11.18.2). A tool asks for a bench adapter by its canctl role --
canmain, canchg, canx2, kvaser, pcanfd -- and never by an enumeration index,
a channel number or the first one of its kind it finds.

Since 2026-11-19 (the charge-interposer reviewer's bench-layer item) this
module is a thin caller of canbench.bench_layer, the bench-role layer both
ESP projects share (can-tools 262ba7b9, pinned in pins.json and loaded by
can_tools_pin.use; reverse-it tools/plans/bench-role-layer.md). The layer
brings a role to the mode asked for, brings a SocketCAN link up, opens the
role through canbench.bench_roles (which applies canbench.live.linux_fixes
and releases a libusb role's USB claim on shutdown), and with restore=True
(the default) puts the role back as found on shutdown. It refuses a pick by
index, channel, USB bus or address, device id, serial or interface. What is
left here: the role names, the canctl path, and the bench guard.

    open_bench(role, bitrate, mode=None, restore=True, **kw)
                             the layer's open_bench for a name in ROLES, in
                             the layer's DEFAULT_MODES unless mode= is given
                             (pcanfd pcan, canchg libusb, canmain libusb,
                             kvaser socketcan; canx2 has none)
    pcanfd(bitrate, **kw)    the PEAK PCAN-USB FD on the vehicle segment
                             (pcan, by the device id canctl reads), python-
                             can's PcanBus (bus_states="raise", the layer's
                             default): only bench_vport's witness
    pcanfd_report(bitrate, **kw)
                             the same role with bus_states="report" (the
                             charge-interposer reviewer's PCAN item 2,
                             2026-11-20): canbench's CanbenchPcanBus, whose
                             bus states, status messages and overruns come
                             up in the stream as Linux-layout error frames,
                             never raised (python-can 4.6.1's PcanBus raises
                             from recv on error passive, bus-off and
                             overruns). Every other PCAN reader opens this
    canchg(bitrate, **kw)    the charger segment's Innomaker (libusb, by USB
                             bus and address)
    kvaser_canlib(bitrate, **kw)
                             the Kvaser on CANlib (mode="linuxcan": the
                             layer moves it there and back), only for
                             bench_rate_feasibility's hand-run witness
    kvaser_netdev()          the Kvaser's netdev, from resolve("kvaser") in
                             socketcan mode (kvaser_link opens and watches
                             it)
    resolve(role)            where a role is now (read-only `canctl roles`)
    layer()                  the pinned bench_layer with CANCTL and the guard
                             set, for kvaser_link (link_up, link_down, the
                             recovery's usbreset) and canchg_mode (to_libusb,
                             to_socketcan)
    bus_error(bus, msg)      the layer's error-frame classifier: on
                             SocketCAN and on CanbenchPcanBus only the
                             bus-error classes count, so a state row is not
                             an error row; every error frame elsewhere
    lost_frames(msg)         an error frame saying the reader missed frames:
                             canbench.errors.lost_frames (CAN_ERR_CRTL_RX_
                             OVERFLOW: a PCAN overrun or queue overrun), or
                             errors.decode's state bus-off or restarted (the
                             controller off the bus; canbench_busoff_reset
                             stays on, so the reader recovers). A reader's
                             window with such a row is VOID (the reviewer's
                             PCAN item 2, and its ruling on bus-off,
                             2026-11-20)
    missed(msg)              which of those msg says ("frames lost",
                             "bus-off", "a restart after bus-off"), or None

THE GUARD. The layer touches nothing on the bench unless a guard is set
(bench_layer.set_guard). layer() always sets _guard, which runs shared/
bench's bench_lock.guard(bench_lock.default_log(), HOLDER_ROLES) at each
call: the bench log must replay on this host, be held by this project's
tester or implementor (a loan), under this process's pid or an ancestor's
(a run's checks under the runner, a hand take's tools under the session).
Otherwise bench_lock.NotHeld, and nothing is run. A pinned shared/bench with
no bench_lock.guard refuses the same way (BenchGuardMissing).

CANCTL, LAYER, BENCH_ROLES, ERRORS and BENCH_LOCK are module globals read at each
call, so adapter_roles_test.py points them at a fake canctl and stand-ins.
The harness's own openers are listed, with their roles, in
adapter_roles_test's OPENERS, and canbench.bench_scan, run there, refuses any
other real-adapter open in the harness.

    python3.13 -B charge-interposer/adapter_roles.py [ROLE ...]
        resolves each role (all of ROLES by default) and prints where it is
        and how it would be opened; opens nothing; exit 1 if any is refused
"""

import sys

# canctl's roles (shared/bench SPEC section 8)
ROLES = ("canmain", "canchg", "canx2", "kvaser", "pcanfd")
# read-only `canctl roles` with no root; the layer runs the rest as root
# through sudo -n
CANCTL = "/usr/local/sbin/canctl"
# the bench log's holders that may use the bench from this harness
HOLDER_ROLES = ("tester (charge-interposer)", "implementor (charge-interposer)")
LAYER = None            # canbench.bench_layer, or a stand-in (tests)
BENCH_ROLES = None      # canbench.bench_roles, or a stand-in (tests)
ERRORS = None           # canbench.errors, or a stand-in (tests)
BENCH_LOCK = None       # shared/bench's vtrux_bench.bench_lock, or a stand-in (tests)


class NotARole(ValueError):
    """A name that is not one of canctl's roles."""


class BenchGuardMissing(RuntimeError):
    """The pinned shared/bench has no bench_lock.guard."""


def _check(role):
    if role not in ROLES:
        raise NotARole("%r is not a bench role (canctl's roles: %s); an adapter is asked "
                       "for by its role, never by index, channel or first found"
                       % (role, ", ".join(ROLES)))


def _pinned_layer():
    """The pinned canbench.bench_layer (can_tools_pin.use)."""
    import can_tools_pin
    can_tools_pin.use()
    from canbench import bench_layer
    return bench_layer


def bench_roles():
    """The pinned canbench.bench_roles (or BENCH_ROLES)."""
    if BENCH_ROLES is not None:
        return BENCH_ROLES
    import can_tools_pin
    can_tools_pin.use()
    from canbench import bench_roles as br
    return br


def bench_guard():
    """shared/bench's guard for this harness: bench_lock.guard over the
    bench log for HOLDER_ROLES."""
    bl = BENCH_LOCK
    if bl is None:
        import shared_pin
        shared_pin.use_bench()
        from vtrux_bench import bench_lock as bl
    if not hasattr(bl, "guard"):
        raise BenchGuardMissing("the pinned shared/bench has no bench_lock.guard: nothing on "
                                "the bench is touched until the harness is re-pinned to it")
    return bl.guard(bl.default_log(), HOLDER_ROLES)


def _guard():
    """The guard the layer runs before every canctl call and every open."""
    bench_guard()()


def layer():
    """The pinned canbench.bench_layer (or LAYER), its canctl CANCTL and its
    guard _guard, set at each call."""
    L = LAYER if LAYER is not None else _pinned_layer()
    L.CANCTL = CANCTL
    L.set_guard(_guard)
    return L


def resolve(role):
    """canbench's Resolved for role: where it is now and how it opens
    (read-only)."""
    _check(role)
    br = bench_roles()
    return br.resolve(role, text=br.canctl_roles_text(CANCTL))


def open_bench(role, bitrate=None, mode=None, restore=True, **kw):
    """The layer's open_bench(role, ...): an open python-can bus, the role
    brought to mode (or the layer's default) first."""
    _check(role)
    return layer().open_bench(role, bitrate, mode=mode, restore=restore, **kw)


def pcanfd(bitrate, **kw):
    """The PCAN-USB FD (role pcanfd) in pcan mode. kw: python-can's pcan
    keywords (auto_reset, state, fd, ...), never a pick."""
    return open_bench("pcanfd", bitrate, **kw)


def pcanfd_report(bitrate, **kw):
    """The PCAN-USB FD (role pcanfd) with bus_states="report": can-tools'
    CanbenchPcanBus, bus states and overruns as error frames in the stream,
    never raised. kw: python-can's pcan keywords (auto_reset, state, ...),
    never a pick."""
    return open_bench("pcanfd", bitrate, bus_states="report", **kw)


def canchg(bitrate, **kw):
    """The charger segment's Innomaker (role canchg) in libusb mode."""
    return open_bench("canchg", bitrate, **kw)


def kvaser_canlib(bitrate, **kw):
    """The Kvaser on CANlib (role kvaser, mode linuxcan: linuxcan's leaf)."""
    return open_bench("kvaser", bitrate, mode="linuxcan", **kw)


def kvaser_netdev():
    """The Kvaser's netdev (role kvaser, socketcan mode on kvaser_usb)."""
    res = resolve("kvaser")
    if res.mode != "socketcan" or not res.netdev:
        raise bench_roles().RoleError("kvaser is in mode %s, not socketcan (kvaser_usb): the "
                                      "harness opens it as a SocketCAN netdev" % res.mode)
    return res.netdev


def bus_error(bus, msg):
    """Whether msg, read from bus, is an error frame that counts as a bus
    error (the layer's classifier: on SocketCAN the bus-error classes only;
    on any other backend every error frame). Reads nothing on the bench. A
    wrapper that names the bus underneath as .inner (kvaser_link.WatchedBus)
    is classified on that bus."""
    L = LAYER if LAYER is not None else _pinned_layer()
    return L.bus_error(getattr(bus, "inner", bus), msg)


def errors():
    """The pinned canbench.errors (or ERRORS)."""
    if ERRORS is not None:
        return ERRORS
    import can_tools_pin
    can_tools_pin.use()
    from canbench import errors as e
    return e


def missed(msg):
    """What msg says a reader missed frames on, or None: "frames lost"
    (can-tools' errors.lost_frames: a controller report with
    CAN_ERR_CRTL_RX_OVERFLOW), "bus-off" or "a restart after bus-off"
    (errors.decode's state busoff or restarted: the reader's controller was
    off the bus and read nothing meanwhile; the reviewer's ruling,
    2026-11-20). Reads nothing on the bench."""
    E = errors()
    if E.lost_frames(msg):
        return "frames lost"
    if not getattr(msg, "is_error_frame", False):
        return None
    state = getattr(E.decode(msg), "state", None)
    if state == "busoff":
        return "bus-off"
    if state == "restarted":
        return "a restart after bus-off"
    return None


def lost_frames(msg):
    """Whether msg is an error frame that says the reader missed frames
    (missed: an overrun, a bus-off, or a restart after bus-off). Reads
    nothing on the bench."""
    return missed(msg) is not None


def main(argv):
    names = argv or list(ROLES)
    bad = 0
    for r in names:
        try:
            res = resolve(r)
            print("%-8s %s mode %s: %s %s" % (r, res.role.kind, res.mode, res.interface,
                                               res.kwargs))
        except Exception as e:                          # noqa: BLE001
            print("%-8s REFUSED: %s" % (r, e))
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
