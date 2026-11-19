#!/usr/bin/env python3
"""Every real bench adapter the harness opens, opened by its canctl role.

Charge-interposer tester, 2026-11-18: shared/bench SPEC section 8's adapter
roles (user, 2026-11-18, the whole bench; shared/main b4e95179, tag
2026.11.18.2), assigned by the charge-interposer reviewer the same day. A
tool asks for a bench adapter by its canctl role -- canmain, canchg, canx2,
kvaser, pcanfd -- and never by an enumeration index, a channel number or
the first one of its kind it finds; turning a role into a device is
canctl's and canbench's job.

So this module is the harness's one way to a real adapter. It loads the
pinned can-tools' canbench.bench_roles (can_tools_pin.use: the commit in
pins.json, 5220d374 or later), asks `canctl roles` (read-only, no root)
where the role is now, and opens it with bench_roles' own resolve() and
open_role(), which also apply canbench.live.linux_fixes and, for a gs_usb
role in libusb mode, release the USB claim on shutdown:

    pcanfd(bitrate, **kw)    the PEAK PCAN-USB FD on the vehicle segment:
                             open_role("pcanfd", modes=("pcan",)), python-can
                             pcan by the device id canctl reads from the
                             driver (no PCAN_USBBUSn)
    canchg(bitrate, **kw)    the charger segment's Innomaker:
                             open_role("canchg", modes=("libusb",)), python-
                             can gs_usb by USB bus and address (no index, no
                             serial), refused while the kernel's gs_usb holds
                             it
    kvaser_netdev()          the vehicle segment's Kvaser's netdev, from
                             resolve("kvaser") in socketcan mode
                             (kvaser_link opens and watches it)
    kvaser_canlib(bitrate, **kw)
                             the Kvaser on CANlib: open_role("kvaser",
                             modes=("linuxcan",)), only for
                             bench_rate_feasibility's hand-run witness
    resolve(role) / open_role(role, ...)
                             the same for any role, the name checked
                             against ROLES first

A role canbench cannot resolve or open as asked raises RoleError (canbench's
own class), naming why: canctl missing (off the bench host, or Windows: no
role is opened there), a device not attached or two matching, a mode the
caller does not take, a netdev down. Nothing here brings a link up or down,
changes a mode or picks among devices: that is canctl's (kvaser_link and
canchg_mode run canctl for that).

CANCTL is the canctl the role table is read from, a module global read at
each call, so adapter_roles_test.py points it at a fake canctl. The
harness's own openers are listed, with their roles, in adapter_roles_test's
OPENERS, and its scan refuses any other real-adapter open in the harness.

    python3.13 -B charge-interposer/adapter_roles.py [ROLE ...]
        resolves each role (all of ROLES by default) and prints where it is
        and how it would be opened; opens nothing; exit 1 if any is refused
"""

import sys

# canctl's roles (shared/bench SPEC section 8)
ROLES = ("canmain", "canchg", "canx2", "kvaser", "pcanfd")
# read-only, no root (canbench.bench_roles: `canctl roles`)
CANCTL = "/usr/local/sbin/canctl"


def bench_roles(mod=None):
    """The pinned canbench.bench_roles (can_tools_pin.use). mod: a stand-in
    (tests)."""
    if mod is not None:
        return mod
    import can_tools_pin
    can_tools_pin.use()
    from canbench import bench_roles as br
    return br


def _table(br):
    return br.canctl_roles_text(CANCTL)


def _check(role, br):
    if role not in ROLES:
        raise br.RoleError("%r is not a bench role (canctl's roles: %s); an adapter is asked "
                           "for by its role, never by index, channel or first found"
                           % (role, ", ".join(ROLES)))


def resolve(role, mod=None):
    """canbench's Resolved for role: where it is now and how it opens."""
    br = bench_roles(mod)
    _check(role, br)
    return br.resolve(role, text=_table(br))


def open_role(role, bitrate=None, modes=None, hw_timestamps=False, mod=None, **kw):
    """canbench's open_role(role, ...): an open python-can bus."""
    br = bench_roles(mod)
    _check(role, br)
    return br.open_role(role, bitrate=bitrate, modes=modes, hw_timestamps=hw_timestamps,
                        text=_table(br), **kw)


def pcanfd(bitrate, mod=None, **kw):
    """The PCAN-USB FD (role pcanfd), python-can pcan by canctl's device
    id. kw: python-can's pcan keywords (auto_reset, state, fd, ...), never
    channel or device_id."""
    for k in ("channel", "device_id"):
        if k in kw:
            raise bench_roles(mod).RoleError("pcanfd is opened by its role, not by %s=%r"
                                             % (k, kw[k]))
    return open_role("pcanfd", bitrate=bitrate, modes=("pcan",), mod=mod, **kw)


def canchg(bitrate, mod=None, **kw):
    """The charger segment's Innomaker (role canchg) in libusb mode, by USB
    bus and address. kw: never index, bus, address or channel."""
    for k in ("index", "bus", "address", "channel"):
        if k in kw:
            raise bench_roles(mod).RoleError("canchg is opened by its role, not by %s=%r"
                                             % (k, kw[k]))
    return open_role("canchg", bitrate=bitrate, modes=("libusb",), mod=mod, **kw)


def kvaser_netdev(mod=None):
    """The Kvaser's netdev (role kvaser, socketcan mode on kvaser_usb)."""
    res = resolve("kvaser", mod)
    if res.mode != "socketcan" or not res.netdev:
        raise bench_roles(mod).RoleError("kvaser is in mode %s, not socketcan (kvaser_usb): the "
                                         "harness opens it as a SocketCAN netdev" % res.mode)
    return res.netdev


def kvaser_canlib(bitrate, mod=None, **kw):
    """The Kvaser on CANlib (role kvaser in linuxcan mode, linuxcan's leaf)."""
    return open_role("kvaser", bitrate=bitrate, modes=("linuxcan",), mod=mod, **kw)


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
