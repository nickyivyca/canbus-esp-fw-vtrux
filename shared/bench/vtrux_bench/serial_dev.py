"""Serial devices found by USB device ID (DEVICES-SPEC S1-S3).

A serial device the bench tooling opens is named in a suite's local
settings by its USB vendor ID, product ID and serial number, with the
interface number on a composite device, never by a COM or tty name:

    {"vid": "303a", "pid": "1001", "serial": "<its serial>", "interface": 0}

  vid, pid    four hex digits each (any case; kept in lower case)
  serial      the USB device's serial number, exactly as the device gives it
  interface   the USB interface number (bInterfaceNumber) of the serial
              port; required when the device has more than one interface
              (a composite device, such as an ESP32's USB-Serial-JTAG, whose
              three interfaces are a CDC serial port and JTAG)

`resolve(device)` finds the port when the caller is about to open it. On
Linux it looks at every link in /dev/serial/by-id/ and reads the sysfs of
the tty it points to: the interface's bInterfaceNumber and, above it, the
USB device's idVendor, idProduct, serial and bNumInterfaces. Exactly one
match gives its /dev/serial/by-id/ path, a name that stays the same when
the board re-enumerates (an ESP32's reset over its own USB). No match, or
more than one, refuses, naming every device that was found with its IDs
(S2): it never guesses. A composite device matched with no interface in the
setting refuses too, saying the interface is needed.

Linux only. New bench hardware tooling is Linux only, and the serial-device
lookup needs no Windows support (user, 2026-11-11, the gen-inhibit
reviewer's tracker row "Bench devices for port plan step 3"); on any other
environment `resolve` refuses. Existing tooling keeps its Windows form: the
esptool flasher's `port` on Windows (flash.py, `device_port`).

What `resolve` returns is what the run record keeps (S3): the port resolved
and the identifiers matched. Serial numbers are bench identifiers: they
live in local settings and run records, never in git.

It is callable on its own, by any harness: give it the IDs, get back one
port or a `Refused` naming every device found.

    python -m vtrux_bench.serial_dev            list what is found here
"""

import os
import re
import sys

from . import bench_lock as BL

KEYS = ("vid", "pid", "serial", "interface")
HEX4 = re.compile(r"[0-9A-Fa-f]{4}\Z")
SERIAL_OK = re.compile(r"[\x21-\x7e]+\Z")   # printable, no spaces
SYS_ROOT = "/sys"                  # tests point it at a made-up tree
BY_ID = "/dev/serial/by-id"        # tests point it at a made-up folder
RULES = {"linux_only": True}


class Refused(Exception):
    """No single device matches, or the setting or the lookup is wrong."""


def config(dev, what="device"):
    """The device setting, checked and with vid and pid in lower case.
    Refused if it is not an object, has an unknown or missing key, or a
    value of the wrong form."""
    if not isinstance(dev, dict):
        raise Refused("%s is not an object {vid, pid, serial[, interface]}: "
                      "a serial device is named by its USB IDs, never a COM "
                      "or tty name (DEVICES-SPEC S1)" % what)
    extra = sorted(set(dev) - set(KEYS))
    if extra:
        raise Refused("%s has unknown keys %s" % (what, ", ".join(extra)))
    out = {}
    for k in ("vid", "pid"):
        v = dev.get(k)
        if not isinstance(v, str) or not HEX4.match(v):
            raise Refused("%s.%s %r is not four hex digits" % (what, k, v))
        out[k] = v.lower()
    s = dev.get("serial")
    if not isinstance(s, str) or not SERIAL_OK.match(s):
        raise Refused("%s.serial is missing or not printable without spaces"
                      % what)
    out["serial"] = s
    i = dev.get("interface")
    if i is not None and (isinstance(i, bool) or not isinstance(i, int)
                          or not 0 <= i <= 255):
        raise Refused("%s.interface %r is not an interface number (0-255)"
                      % (what, i))
    out["interface"] = i
    return out


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def _usb_ids(devdir, root):
    """Walk up from a tty's device folder in sysfs: (interface number,
    the USB device's folder) or (None, None) if it is not on USB."""
    top = os.path.realpath(root)
    p = os.path.realpath(devdir)
    intf = None
    while p.startswith(top + os.sep) and p != top:
        if intf is None:
            n = _read(os.path.join(p, "bInterfaceNumber"))
            if n is not None:
                intf = int(n, 16)
        if _read(os.path.join(p, "idVendor")) is not None:
            return intf, p
        p = os.path.dirname(p)
    return None, None


def listing(root=None, by_id=None):
    """Every serial device found: one dict per link in by_id, with its
    port (the link), tty, vid, pid, serial, interface, interfaces (the USB
    device's bNumInterfaces) and usb (its sysfs name); vid is None for a
    device not on USB. No by_id folder is an empty listing (nothing
    plugged in); one that cannot be read refuses."""
    root = SYS_ROOT if root is None else root
    by_id = BY_ID if by_id is None else by_id
    try:
        names = sorted(os.listdir(by_id))
    except FileNotFoundError:
        return []
    except OSError as e:
        raise Refused("%s cannot be listed: %s" % (by_id, e.strerror or e))
    out = []
    for name in names:
        link = os.path.join(by_id, name)
        tty = os.path.basename(os.path.realpath(link))
        e = dict(port=link, tty=tty, vid=None, pid=None, serial=None,
                 interface=None, interfaces=None, usb=None)
        intf, usb = _usb_ids(os.path.join(root, "class", "tty", tty, "device"),
                             root)
        if usb is not None:
            n = _read(os.path.join(usb, "bNumInterfaces"))
            e.update(vid=(_read(os.path.join(usb, "idVendor")) or "").lower(),
                     pid=(_read(os.path.join(usb, "idProduct")) or "").lower(),
                     serial=_read(os.path.join(usb, "serial")),
                     interface=intf, interfaces=int(n) if n else None,
                     usb=os.path.basename(usb))
        out.append(e)
    return out


def describe(e):
    """One device of the listing, as a refusal names it."""
    if e["vid"] is None:
        return "%s (%s, not a USB device)" % (e["port"], e["tty"])
    return ("%s:%s serial %s interface %s of %s (USB %s) at %s"
            % (e["vid"], e["pid"], e["serial"], e["interface"],
               e["interfaces"], e["usb"], e["port"]))


def matches(want, entries):
    """The entries matching the checked setting `want`."""
    return [e for e in entries
            if e["vid"] == want["vid"] and e["pid"] == want["pid"]
            and e["serial"] == want["serial"]
            and (want["interface"] is None
                 or e["interface"] == want["interface"])]


def resolve(dev, what="device", env=None, root=None, by_id=None):
    """The one port the device setting names, as the record keeps it:
    {port, vid, pid, serial, interface, usb, tty}. Raises Refused on any
    other environment than Linux, on a malformed setting, when no device
    or more than one matches (naming every device found), and when a
    composite device is matched with no interface named."""
    env = env or BL.this_env()
    if env != "linux" and RULES["linux_only"]:
        raise Refused("%s: the serial-device lookup is Linux only (new bench "
                      "hardware tooling is Linux only, user 2026-11-11); "
                      "this is %s" % (what, env))
    want = config(dev, what)
    found = listing(root, by_id)
    hits = matches(want, found)
    named = ("%s:%s serial %s%s" % (want["vid"], want["pid"], want["serial"],
             "" if want["interface"] is None
             else " interface %d" % want["interface"]))
    seen = ("; found: " + "; ".join(describe(e) for e in found)) if found \
        else "; found: no serial device at all"
    if not hits:
        raise Refused("%s %s: no device matches%s" % (what, named, seen))
    if len(hits) > 1:
        raise Refused("%s %s: %d devices match, which one is meant is not "
                      "known%s" % (what, named, len(hits), seen))
    h = hits[0]
    if want["interface"] is None and (h["interfaces"] or 1) > 1:
        raise Refused("%s %s: the device has %d interfaces, so the setting "
                      "must name the interface (DEVICES-SPEC S1)%s"
                      % (what, named, h["interfaces"], seen))
    return {k: h[k] for k in ("port", "vid", "pid", "serial", "interface",
                              "usb", "tty")}


def main(argv):
    try:
        found = listing()
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    for e in found:
        print(describe(e))
    print("%d serial device(s) in %s" % (len(found), BY_ID))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
