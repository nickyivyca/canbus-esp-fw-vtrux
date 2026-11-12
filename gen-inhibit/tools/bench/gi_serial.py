#!/usr/bin/env python3
"""The one place gen-inhibit's harness finds a board's serial port: by its USB IDs, through
the pinned core's vtrux_bench.serial_dev (shared/bench DEVICES-SPEC S1-S3).

Gen-inhibit tester, 2026-11-12, the gen-inhibit reviewer's re-pin batch item 4 (design
accepted the same day):

  - A serial port is never named by a COM or tty name, in settings or in code (S1). Each
    board's USB IDs come from the environment, as a JSON object
    {"vid", "pid", "serial", "interface"}:
        GI_X2_DEVICE      the X2's USB-Serial-JTAG: esptool, its console (the jammer's,
                          x2_app's), and SLCAN
        GI_WICAN_DEVICE   the WiCAN's USB-Serial-JTAG: its console
    The suite sets them from its local settings (settings.esptool.device and
    settings.wican.usb_device), as it sets GI_ESPTOOL_ARGV. Unset, not JSON, a string (a
    COM or tty name), or an object the core refuses: Refused. There is no default.
  - The port is resolved when a tool is about to open it, through
    vtrux_bench.serial_dev.resolve: exactly one match gives its /dev/serial/by-id/ path;
    none or several refuse, naming every device found (S2).
  - Linux only, with no COM branch (gen-inhibit reviewer, 2026-11-12: new bench hardware
    tooling is Linux only, user 2026-11-11, and no Windows run is made on this harness).
    On any other system it refuses, naming S1, as the core does.
  - What resolve() returns is what a tool's record keeps (S3): the port resolved and the
    IDs matched. Serial numbers are bench identifiers: local settings and run records only,
    never git.

TESTS ONLY: GI_SERIAL_SYS_ROOT and GI_SERIAL_BY_ID point the lookup at a made-up sysfs and
by-id folder, so a tool's test runs the real lookup in its subprocess. Set, they are named
in what resolve() returns ("lookup"), so a record shows a lookup that did not read the
host's devices. The suite never sets them.

    python3.13 -B gi_serial.py port x2       print the X2's port, or REFUSED (on stderr), exit 2
    python3.13 -B gi_serial.py record x2     print what a record keeps, as JSON
    python3.13 -B gi_serial.py list          every serial device found here

Nothing here opens a port.
"""
import json
import os
import sys

ENV = {"x2": "GI_X2_DEVICE", "wican": "GI_WICAN_DEVICE"}
WHAT = {"x2": "the X2's USB-Serial-JTAG (esptool, console, SLCAN)",
        "wican": "the WiCAN's USB-Serial-JTAG (console)"}
SETTING = {"x2": ("esptool", "device"), "wican": ("wican", "usb_device")}
TEST_ROOT = "GI_SERIAL_SYS_ROOT"
TEST_BY_ID = "GI_SERIAL_BY_ID"


class Refused(Exception):
    """No port: the IDs are missing or wrong, the lookup refused, or this is not Linux."""


def serial_dev():
    """The pinned core's vtrux_bench.serial_dev (core 2026.11.11.6 or later)."""
    try:
        from vtrux_bench import serial_dev as SD
    except ImportError as e:
        raise Refused("vtrux_bench.serial_dev is not importable (%s): put the pinned core's "
                      "shared/bench on PYTHONPATH (2026.11.11.6 or later)" % e)
    return SD


def device(board, environ=None):
    """The board's IDs from its environment variable, checked by the core. Refused if the
    board is unknown, the variable is unset, not JSON, not an object, or the core refuses it."""
    environ = os.environ if environ is None else environ
    if board not in ENV:
        raise Refused("no board %r: one of %s" % (board, ", ".join(sorted(ENV))))
    var = ENV[board]
    raw = environ.get(var)
    if not raw:
        raise Refused("%s is not set: give %s as its USB IDs, a JSON object {vid, pid, "
                      "serial, interface} (DEVICES-SPEC S1); the suite sets it from "
                      "settings.%s" % (var, WHAT[board], ".".join(SETTING[board])))
    try:
        dev = json.loads(raw)
    except ValueError:
        raise Refused("%s is not JSON: a serial device is named by its USB IDs, never a COM "
                      "or tty name (DEVICES-SPEC S1)" % var)
    SD = serial_dev()
    try:
        return SD.config(dev, var)
    except SD.Refused as e:
        raise Refused(str(e))


def resolve(board, environ=None):
    """{port, vid, pid, serial, interface, usb, tty, board, env} for the board's one port, as a
    record keeps it (S3), plus "lookup" when the test overrides are set. Refused otherwise."""
    environ = os.environ if environ is None else environ
    dev = device(board, environ)
    SD = serial_dev()
    root, by_id = environ.get(TEST_ROOT) or None, environ.get(TEST_BY_ID) or None
    try:
        got = SD.resolve(dev, what=ENV[board], root=root, by_id=by_id)
    except SD.Refused as e:
        raise Refused("%s (DEVICES-SPEC S1, S2)" % e)
    got = dict(got, board=board, env=ENV[board])
    if root or by_id:
        got["lookup"] = "made up (test): sysfs %s, by-id %s" % (root or SD.SYS_ROOT, by_id or SD.BY_ID)
    return got


def port(board, environ=None):
    """The board's port: its /dev/serial/by-id/ path. Refused if there is not exactly one."""
    return resolve(board, environ)["port"]


def present(board, environ=None):
    """True when exactly one device matches the board's IDs, False when none does (unplugged).
    Refused when the IDs are wrong, several devices match, or the listing cannot be read: a
    presence watch (page_only_baseline's unplug stamp) is never a guess either (S2)."""
    environ = os.environ if environ is None else environ
    dev = device(board, environ)
    SD = serial_dev()
    env = SD.BL.this_env()
    if env != "linux":
        raise Refused("%s: the serial-device lookup is Linux only (new bench hardware tooling is "
                      "Linux only, user 2026-11-11; DEVICES-SPEC S1); this is %s" % (ENV[board], env))
    try:
        found = SD.matches(dev, SD.listing(environ.get(TEST_ROOT) or None,
                                           environ.get(TEST_BY_ID) or None))
    except SD.Refused as e:
        raise Refused("%s (DEVICES-SPEC S1, S2)" % e)
    if len(found) > 1:
        raise Refused("%s: %d devices match, which one is meant is not known (DEVICES-SPEC S2)"
                      % (ENV[board], len(found)))
    return len(found) == 1


def env_from_settings(settings):
    """{GI_X2_DEVICE, GI_WICAN_DEVICE} as JSON, for each board whose IDs the settings hold."""
    out = {}
    for board, (sect, key) in SETTING.items():
        dev = (settings.get(sect) or {}).get(key)
        if dev is not None:
            out[ENV[board]] = json.dumps(dev, sort_keys=True)
    return out


def main(argv):
    if argv[:1] == ["list"] and len(argv) == 1:
        SD = serial_dev()
        try:
            found = SD.listing(os.environ.get(TEST_ROOT) or None, os.environ.get(TEST_BY_ID) or None)
        except SD.Refused as e:
            print("REFUSED: %s" % e)
            return 2
        for e in found:
            print(SD.describe(e))
        print("%d serial device(s)" % len(found))
        return 0
    if len(argv) != 2 or argv[0] not in ("port", "record"):
        print("usage: gi_serial.py port|record x2|wican, or gi_serial.py list")
        return 2
    try:
        got = resolve(argv[1])
    except Refused as e:
        # on stderr: a runner takes the port from stdout, PORT=$(gi_serial.py port x2)
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    print(got["port"] if argv[0] == "port" else json.dumps(got, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
