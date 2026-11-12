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
"""

import sys

# the interfaces the fixes are for; a harness file opening either of these
# with python-can goes through bus() (adapter_fixes_test.py)
FIXED = ("gs_usb", "kvaser")


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
