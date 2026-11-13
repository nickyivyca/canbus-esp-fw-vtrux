#!/usr/bin/env python3
"""Opening the Kvaser and the Innomaker (gs_usb) on Linux: the pinned
can-tools' canbench.live.linux_fixes, applied before every open.

Charge-interposer tester, 2026-11-11 (the reviewer's ruling the same day,
port plan step 3). Two python-can / gs_usb defects stop real adapters
opening on madhouse-debian (Debian 13, python-can 4.6.1, gs_usb 0.3,
linuxcan CANlib 5.53), found and fixed by the CAN replay tooling session in
can-tools fa6c0aea5399ab1ac887d95a6edb381590e1c644, canbench/live/
linux_fixes.py, hardware-checked under the Linux lock 21:00-21:02:

  Kvaser   python-can's KvaserBus passes canIoCtl(canIOCTL_SET_LOCAL_TXACK)
           a 1-byte buffer; linuxcan wants 4 and refuses, so no channel
           opens. The fix passes that one ioctl as a uint32.
  gs_usb   gs_usb's GsUsb.start() begins with a USB reset; on Linux the
           Innomaker re-enumerates on it and the handle is lost. The fix
           replaces start() on GsUsb itself with the same steps minus the
           reset, so python-can's GsUsbBus open and the harness's own
           in-process stop()/start() (bench_c12's listen-only switch,
           bench_charger_silent, bench_switch_pending) are both covered.

canbench.live.receiver applies them on import, but the harness opens most
adapters with python-can directly and never imports the receiver, so
(reviewer, 2026-11-11) every harness opener of a Kvaser or an Innomaker
goes through here and applies them explicitly:

    import adapter_fixes
    bus = adapter_fixes.bus(interface="kvaser", channel=0, bitrate=500000)

    adapter_fixes.apply()      # before an open this module does not make
                               # (bus.open_gs_usb's GsUsbBus subclass)

Since 2026-11-13 the Kvaser on madhouse is the kernel's kvaser_usb, opened
through kvaser_link.py (SocketCAN, netdev kvaser), not CANlib: the only
CANlib Kvaser opener left is bench_rate_feasibility's hand-run witness,
CANlib-only and refused on Linux (the reviewer's E). The Kvaser fix stays
for it and for the Windows bench.

On Linux, apply() loads canbench from the can-tools pin (can_tools_pin.use:
the pinned commit's canbench/, checked, loaded by file) and calls
linux_fixes.apply(). Off Linux it does nothing and loads nothing, so the
Windows bench opens its adapters as before, CAN_TOOLS_DIR or not.
adapter_fixes_test.py checks that every harness file opens these two only
through here.

The trap they flagged: after shutdown(), pyusb keeps its claim on the
Innomaker in-process, so a harness that hands it back to the kernel's
gs_usb driver in the same process must call
canbench.live.receiver.release_gs_usb(bus) first. No harness driver does
that (checked 2026-11-11: each opens the Innomaker by libusb only, switches
its mode on the same handle, and exits); one that ever does calls it there.

The charger segment's Innomaker, canchg (tester, 2026-11-12; the reviewer's
E2, approved 2026-11-11). madhouse has two Innomakers, canchg and canmain
(CAN1, the Android app's, kept down during ESP runs), both 1d50:606f, and
gs_usb's scan lists both, kernel-bound or not. The harness used to open
"index 0", and the no-reset start() above detaches a bound kernel driver,
so index 0 could have taken canmain from its owner. On Linux every harness
open of the charger segment's Innomaker goes through canchg() (or, for
bus.open_gs_usb's own GsUsbBus subclass, canchg_target()), which:

  - reads which device it is from VTRUX_CANCHG_DEVICE, "<vid>:<pid>:<serial>",
    set by bench_suite.py for every bench check from the local settings'
    canchg_device {vid, pid, serial} (never committed); unset is refused,
    and there is no index fallback;
  - finds that one device in sysfs (none or two is refused) and refuses
    unless its interface 0 has no kernel driver, i.e. canctl has it in
    libusb mode (sudo -n /usr/local/sbin/canctl mode canchg libusb), so a
    SocketCAN interface is never detached;
  - opens it by its USB bus and address, and after the open checks that the
    device opened carries that serial, shutting it down and refusing if not.

Off Linux it is index 0 as before (the Windows bench, NICKY-XPS until step
3: WinUSB, no kernel driver to take it from).

    python3.13 -B charge-interposer/adapter_fixes.py canchg-check
        canchg found and in libusb mode -> exit 0, else the refusal -> exit 1
"""

import os
import re
import sys

# the interfaces the fixes are for; a harness file opening either of these
# with python-can goes through bus() (adapter_fixes_test.py)
FIXED = ("gs_usb", "kvaser")

CANCHG_ENV = "VTRUX_CANCHG_DEVICE"
SYSFS_USB = "/sys/bus/usb/devices"
_DEVICE = re.compile(r"([0-9a-f]{4}):([0-9a-f]{4}):(\S+)\Z")
LIBUSB_MODE = "sudo -n /usr/local/sbin/canctl mode canchg libusb"


class CanchgError(RuntimeError):
    """The charger segment's Innomaker cannot be opened safely: nothing was
    opened, or what was opened has been shut down."""


def linux(platform=None):
    return (platform or sys.platform).startswith("linux")


def apply(platform=None, fixes=None):
    """Install canbench.live.linux_fixes. -> its {"kvaser": bool, "gs_usb":
    bool}, or {} off Linux (nothing loaded). fixes: a stand-in for the
    linux_fixes module (tests)."""
    if not linux(platform):
        return {}
    if fixes is None:
        import can_tools_pin
        can_tools_pin.use()
        from canbench.live import linux_fixes as fixes
    return fixes.apply()


def bus(platform=None, fixes=None, can_mod=None, **kw):
    """python-can's Bus(**kw), after apply(). can_mod: a stand-in for the
    can module (tests)."""
    apply(platform, fixes)
    if can_mod is None:
        import can as can_mod
    return can_mod.Bus(**kw)


def parse_device(spec):
    """"<vid>:<pid>:<serial>" -> (vid, pid, serial); vid and pid four hex
    digits (lower-cased), the serial as given."""
    v = (spec or "").strip()
    m = _DEVICE.match(v[:10].lower() + v[10:])
    if not m:
        raise CanchgError("%s=%r is not <vid>:<pid>:<serial> (four hex digits each, "
                          "then the USB serial)" % (CANCHG_ENV, spec))
    return m.groups()


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def find_canchg(vid, pid, serial, root=SYSFS_USB):
    """-> (busnum, devnum) of the one USB device in sysfs with this vid, pid
    and serial, whose interface 0 no kernel driver holds. CanchgError
    otherwise."""
    found = []
    for name in sorted(os.listdir(root)):
        if ":" in name:
            continue                    # an interface, not a device
        d = os.path.join(root, name)
        ids = (_read(os.path.join(d, "idVendor")), _read(os.path.join(d, "idProduct")),
               _read(os.path.join(d, "serial")))
        if ids == (vid, pid, serial):
            found.append(name)
    if not found:
        raise CanchgError("no USB device %s:%s with serial %s in %s: is the charger "
                          "segment's Innomaker plugged in?" % (vid, pid, serial, root))
    if len(found) > 1:
        raise CanchgError("%d USB devices %s:%s carry serial %s (%s): refused"
                          % (len(found), vid, pid, serial, ", ".join(found)))
    name = found[0]
    intf = os.path.join(root, name + ":1.0")
    if not os.path.isdir(intf):
        raise CanchgError("canchg (%s, USB %s) has no interface 0 in sysfs" % (serial, name))
    drv = os.path.join(intf, "driver")
    if os.path.lexists(drv):
        raise CanchgError(
            "canchg (%s, USB %s) interface 0 is bound to the kernel's %s driver (SocketCAN "
            "mode), and opening it by libusb would detach it: refused. Put it in libusb mode "
            "first: %s" % (serial, name, os.path.basename(os.path.realpath(drv)), LIBUSB_MODE))
    busnum = _read(os.path.join(root, name, "busnum"))
    devnum = _read(os.path.join(root, name, "devnum"))
    if not (busnum or "").isdigit() or not (devnum or "").isdigit():
        raise CanchgError("canchg (%s, USB %s): busnum %r, devnum %r unreadable"
                          % (serial, name, busnum, devnum))
    return int(busnum), int(devnum)


def canchg_target(index=0, platform=None, environ=None, root=SYSFS_USB):
    """Which Innomaker python-can's GsUsbBus opens for the charger segment.
    Off Linux {"index": index}, as before. On Linux {"bus", "address",
    "serial"} from VTRUX_CANCHG_DEVICE and sysfs (see the module text); an
    index other than 0 is refused there, since an index is never used.
    Pass gs_usb_kwargs(target) to GsUsbBus, then check_opened()."""
    if not linux(platform):
        return {"index": index}
    if index not in (0, None):
        raise CanchgError("gs_usb index %r: on Linux the charger segment's Innomaker is "
                          "opened by its serial (%s), never by index" % (index, CANCHG_ENV))
    env = os.environ if environ is None else environ
    spec = env.get(CANCHG_ENV, "")
    if not spec.strip():
        raise CanchgError("%s is not set: on Linux the charger segment's Innomaker is "
                          "opened by its USB serial, never by index (index 0 may be "
                          "canmain); bench_suite.py sets it from the local settings' "
                          "canchg_device" % CANCHG_ENV)
    vid, pid, serial = parse_device(spec)
    b, a = find_canchg(vid, pid, serial, root)
    return {"bus": b, "address": a, "serial": serial}


def gs_usb_kwargs(target):
    """The target's GsUsbBus keywords (all but the serial)."""
    return {k: v for k, v in target.items() if k != "serial"}


def check_opened(b, target):
    """b if the device opened is the target's serial (or the target names
    none, off Linux); else b shut down and CanchgError."""
    want = target.get("serial")
    if want is None:
        return b
    got = getattr(getattr(b, "gs_usb", None), "serial_number", None)
    if got != want:
        note = "shut down"
        try:
            b.shutdown()
        except Exception as e:                          # noqa: BLE001
            note = "its shutdown raised %s: %s" % (type(e).__name__, e)
        raise CanchgError("the gs_usb device opened carries serial %r, not canchg's %r (%s): "
                          "refused" % (got, want, note))
    return b


def canchg(platform=None, fixes=None, can_mod=None, environ=None, root=SYSFS_USB, **kw):
    """python-can's gs_usb Bus on the charger segment's Innomaker:
    canchg_target() first (a refusal opens nothing), then bus(), then
    check_opened(). kw: bitrate and the like, never index/bus/address."""
    target = canchg_target(0, platform, environ, root)
    b = bus(platform, fixes, can_mod, interface="gs_usb", channel="can0",
            **gs_usb_kwargs(target), **kw)
    return check_opened(b, target)


def main(argv):
    if argv != ["canchg-check"]:
        print("usage: adapter_fixes.py canchg-check")
        return 2
    try:
        t = canchg_target()
    except CanchgError as e:
        print("REFUSED: %s" % e)
        return 1
    print("OK: canchg %s" % (", ".join("%s %s" % kv for kv in sorted(t.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
