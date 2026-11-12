"""A made-up /dev/serial/by-id/ folder and the sysfs behind it, for the
tests of vtrux_bench.serial_dev and of the esptool flasher that uses it. No
hardware.

    fs = FakeSerial(folder)
    fs.add("ttyACM9", "303a", "1001", "<serial>", interface=0, interfaces=3)
    fs.install()        # points serial_dev at it; fs.uninstall() puts it back

Laid out as Linux lays it out (seen on madhouse-debian, 2026-11-11):
<root>/devices/usb1/<usb>/ holds the USB device's idVendor, idProduct,
serial and bNumInterfaces (" 3", with its leading space), and
<usb>:1.<n>/ the interface's bInterfaceNumber ("00", hex), with the
tty below it (tty/<tty> for cdc_acm; <tty>/ for a USB-serial converter's
port, `converter=True`). <root>/class/tty/<tty>/device links to the
folder above the tty, and <by_id>/<name> links to <dev>/<tty>. Every made-up
serial is plainly made up.

Needs symbolic links: native Linux, or Windows with the right to make them.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeSerial:
    def __init__(self, folder):
        self.root = os.path.join(folder, "sys")
        self.by_id = os.path.join(folder, "by-id")
        self.dev = os.path.join(folder, "dev")
        for d in (os.path.join(self.root, "devices", "usb1"),
                  os.path.join(self.root, "class", "tty"), self.by_id,
                  self.dev):
            os.makedirs(d, exist_ok=True)
        self._saved = None

    @staticmethod
    def _write(path, text):
        with open(path, "w") as f:
            f.write(text + "\n")

    def add(self, tty, vid=None, pid=None, serial=None, interface=0,
            interfaces=1, usb=None, name=None, converter=False):
        """One serial port: its USB device (made once per `usb`), its
        interface, its tty and its by-id link. vid None makes a port that
        is not on USB. Returns the by-id link's path."""
        if vid is None:
            parent = os.path.join(self.root, "devices", "platform", tty)
            os.makedirs(parent, exist_ok=True)
        else:
            usb = usb or "1-%d" % (len(os.listdir(os.path.join(
                self.root, "devices", "usb1"))) + 1)
            udev = os.path.join(self.root, "devices", "usb1", usb)
            if not os.path.isdir(udev):
                os.makedirs(udev)
                self._write(os.path.join(udev, "idVendor"), vid)
                self._write(os.path.join(udev, "idProduct"), pid)
                if serial is not None:
                    self._write(os.path.join(udev, "serial"), serial)
                self._write(os.path.join(udev, "bNumInterfaces"),
                            "%2d" % interfaces)
            intf = os.path.join(udev, "%s:1.%d" % (usb, interface))
            os.makedirs(intf, exist_ok=True)
            self._write(os.path.join(intf, "bInterfaceNumber"),
                        "%02x" % interface)
            parent = intf
            if converter:
                parent = os.path.join(intf, tty)
                os.makedirs(parent, exist_ok=True)
            else:
                os.makedirs(os.path.join(intf, "tty", tty), exist_ok=True)
        cls = os.path.join(self.root, "class", "tty", tty)
        os.makedirs(cls, exist_ok=True)
        os.symlink(parent, os.path.join(cls, "device"))
        node = os.path.join(self.dev, tty)
        open(node, "w").close()
        name = name or "usb-Made_Up_%s_%s-if%02d" % (tty, serial, interface)
        link = os.path.join(self.by_id, name)
        os.symlink(node, link)
        return link

    def install(self, sd=None):
        if sd is None:
            from vtrux_bench import serial_dev as sd
        self._saved = (sd, sd.SYS_ROOT, sd.BY_ID)
        sd.SYS_ROOT, sd.BY_ID = self.root, self.by_id

    def uninstall(self):
        if self._saved:
            sd, root, by_id = self._saved
            sd.SYS_ROOT, sd.BY_ID = root, by_id
            self._saved = None
