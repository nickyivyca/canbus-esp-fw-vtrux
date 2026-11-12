"""The built-in "esptool" flasher: a verified write over USB, recoverable
over USB alone.

Ported from the charge-interposer tester's flash_custody*.sh (2026-10-14 to
2026-10-17). In order, and stopping at the first failure:
  1. every file exists, and matches its expected md5 where one is given
     (the runner flashes only bins already matched to their manifest row);
  2. the auto-reset check: `esptool chip-id` on the port must return 0.
     If it does not, nothing is written -- a board whose auto-reset does
     not work may not come back without hands. The image (or
     settings.esptool) must name its chip, type and MAC, or nothing is
     written; the chip type and MAC that check reads must match it, and a
     mismatch means the port reached another board, so nothing is written
     and no further flash is allowed on that board (methodology,
     2026-10-20: the X2's image must never reach the WiCAN, and a check an
     image can skip by naming nothing is no check);
  3. `write-flash` of every (offset, file), and esptool must report "Hash
     of data verified" once per file;
  4. the auto-reset check again, first thing after the write.
No step is retried. Every step's output is kept in the log folder.

The port (DEVICES-SPEC S1-S3, `device_port`, the one place the systems
differ): on Linux the board is named by its USB IDs, `device`, and the port
is found when the flash starts, before any esptool call, by
`vtrux_bench.serial_dev` (its /dev/serial/by-id/ path). A `port` there is
refused, as is a device that matches no device or several, with nothing
run. On Windows the flasher keeps its `port`, a COM port name, as existing
tooling (user, 2026-11-11: the serial-device lookup needs no Windows
support; existing tooling keeps its Windows support). The result's `port`
and `device` (the IDs matched; None on Windows) go into the run record, and
into `serial_device.txt` in the log folder.

Image.params:
    files     [[offset, path], ...]  e.g. [["0x0", ".../bootloader.bin"], ...]
    md5       {path: md5}            optional expected digests
    device    {"vid", "pid", "serial", "interface"} (serial_dev.config),
              Linux; default settings["esptool"]["device"]. From local
              settings: a serial number is a bench identifier
    port      a COM port name, Windows only; default
              settings["esptool"]["port"]
    baud      default settings["esptool"]["baud"] or 921600
    chip      {"type": "ESP32-S3", "mac": "<the board's MAC>"}, both
              REQUIRED; default settings["esptool"]["chip"]. The repo is
              public: a suite reads the MAC from its local, unpublished
              settings at run time, never from a literal in a file
Settings:
    esptool   {"argv": [...esptool.py], "device": {...} (Linux) or
               "port": <COM port> (Windows), "baud": 921600}
"""

import hashlib
import os
import re
import subprocess

from .. import bench_lock as BL
from .. import serial_dev as SD
from .procs import run_local

RULES = {"chip_match": True, "chip_required": True, "port_refused": True,
         "resolve_first": True, "windows_port": True}
this_env = BL.this_env      # tests set the environment here


def device_port(params, et, env=None):
    """(port, device): the port to give esptool and the IDs it was found by
    (None on Windows). The one place the two systems differ. Raises
    serial_dev.Refused when the port cannot be had: on Linux a `port`
    setting, no `device`, or a device that is not exactly one; on Windows
    no `port`."""
    env = env or this_env()
    dev = params.get("device") or et.get("device")
    port = params.get("port") or et.get("port")
    if env == "windows" and RULES["windows_port"]:
        if port:
            return port, None
        if dev is None:
            raise SD.Refused("esptool: no port (on windows the flasher keeps "
                             "its COM port setting)")
    if port and RULES["port_refused"]:
        raise SD.Refused("esptool: a port setting (%s) is refused on %s: name "
                         "the board by its USB IDs, device {vid, pid, serial, "
                         "interface} (DEVICES-SPEC S1)" % (port, env))
    if dev is None:
        if port:
            return port, None
        raise SD.Refused("esptool: no device setting {vid, pid, serial, "
                         "interface} (DEVICES-SPEC S1)")
    d = SD.resolve(dev, what="esptool device", env=env)
    return d["port"], d


def chip_read(text):
    """(chip type, MAC) as `esptool chip-id` printed them, or None each."""
    typ = re.search(r"^(?:Chip type:\s*|Chip is\s+)(ESP32[-\w]*)", text, re.M)
    mac = re.search(r"^MAC:\s*([0-9a-fA-F:]{17})", text, re.M)
    return (typ.group(1) if typ else None,
            mac.group(1).lower() if mac else None)


def chip_unnamed(want):
    """A reason string if the image names no chip type or MAC, else None."""
    if not RULES["chip_required"]:
        return None
    miss = [k for k in ("type", "mac") if not (want or {}).get(k)]
    return ("names no chip %s" % " or ".join(miss)) if miss else None


def chip_mismatch(text, want):
    """A reason string if the chip-id output does not match `want`, else
    None. A key `want` does not give is not checked (chip_unnamed refuses
    such an image before this is reached)."""
    if not want or not RULES["chip_match"]:
        return None
    typ, mac = chip_read(text)
    bad = []
    if want.get("type") and typ != want["type"]:
        bad.append("type %s, expected %s" % (typ, want["type"]))
    if want.get("mac") and mac != want["mac"].lower():
        bad.append("MAC %s, expected %s" % (mac, want["mac"].lower()))
    return "; ".join(bad) or None


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _esptool(settings):
    et = settings.get("esptool") or {}
    argv = et.get("argv")
    if not argv:
        raise ValueError("settings.esptool.argv is not set")
    return list(argv), et


def esptool_flash(image, params, settings, logdir):
    """Returns dict(ok, phase, detail, md5). Never raises for a failed
    flash; raises only for a mis-written image definition."""
    argv0, et = _esptool(settings)
    baud = str(params.get("baud") or et.get("baud") or 921600)
    files = params.get("files") or []
    if not files:
        raise ValueError("image %s: files are required" % image.name)
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # recoverable: may another flash be attempted in this run? False once
    # an auto-reset check has failed.
    out = dict(ok=False, phase="device", detail="", md5={}, recoverable=True,
               port=None, device=None)

    # DEVICES-SPEC S1-S3: the port is found when the flash starts, before
    # anything else, and kept in the result for the record
    port = None
    if RULES["resolve_first"]:
        try:
            port, out["device"] = device_port(params, et)
        except SD.Refused as e:
            out["detail"] = "%s: nothing run" % e
            return out
    else:
        port = params.get("port") or et.get("port")
    out["port"] = port
    with open(os.path.join(logdir, "serial_device.txt"), "w",
              encoding="utf-8") as f:
        f.write("port: %s\n" % port)
        for k in SD.KEYS + ("usb", "tty"):
            f.write("%s: %s\n" % (k, (out["device"] or {}).get(k)))
    out["phase"] = "files"

    for off, path in files:
        if not os.path.isfile(path):
            out["detail"] = "missing %s" % path
            return out
        out["md5"][path] = md5_of(path)
    want = params.get("md5") or {}
    for path, m in want.items():
        if out["md5"].get(path) != m:
            out["detail"] = "md5 of %s is %s, expected %s" % (
                path, out["md5"].get(path), m)
            return out
    with open(os.path.join(logdir, "flash_md5.txt"), "w") as f:
        for path, m in out["md5"].items():
            f.write("%s  %s\n" % (m, path))
    why = chip_unnamed(params.get("chip") or et.get("chip"))
    if why:
        out["phase"] = "chip-unnamed"
        out["detail"] = ("image %s %s (params['chip'] = {'type', 'mac'}, "
                         "from local settings): nothing written"
                         % (image.name, why))
        return out

    out["phase"] = "autoreset-before"
    log = os.path.join(logdir, "autoreset_before.txt")
    rc, to = run_local(argv0 + ["--port", port, "chip-id"], None, env, 120, log)
    if rc != 0:
        out["detail"] = ("auto-reset check before the flash failed (rc=%s%s): "
                         "nothing written" % (rc, ", timeout" if to else ""))
        out["recoverable"] = False
        return out
    with open(log, encoding="utf-8", errors="replace") as f:
        why = chip_mismatch(f.read(), params.get("chip") or et.get("chip"))
    if why:
        out["phase"] = "chip-mismatch"
        out["detail"] = ("chip-id mismatch (%s): the port reached another "
                         "board; nothing written" % why)
        out["recoverable"] = False
        return out

    out["phase"] = "write"
    log = os.path.join(logdir, "flash.txt")
    cmd = argv0 + ["--port", port, "--baud", baud, "write-flash"]
    for off, path in files:
        cmd += [str(off), path]
    rc, to = run_local(cmd, None, env, 600, log)
    with open(log, encoding="utf-8", errors="replace") as f:
        verified = f.read().count("Hash of data verified")
    if rc != 0 or verified != len(files):
        out["detail"] = ("write-flash rc=%s, %d of %d writes verified"
                         % (rc, verified, len(files)))
        # Still run the after-check: it says whether USB recovery works.
        log2 = os.path.join(logdir, "autoreset_after.txt")
        rc2, _ = run_local(argv0 + ["--port", port, "chip-id"], None, env,
                           120, log2)
        out["detail"] += "; auto-reset check after: rc=%s" % rc2
        out["recoverable"] = rc2 == 0
        return out

    out["phase"] = "autoreset-after"
    log = os.path.join(logdir, "autoreset_after.txt")
    rc, to = run_local(argv0 + ["--port", port, "chip-id"], None, env, 120, log)
    if rc != 0:
        out["detail"] = ("written and verified, but the auto-reset check "
                         "after the flash failed (rc=%s): no further flash "
                         "may be attempted" % rc)
        out["recoverable"] = False
        return out
    out.update(ok=True, phase="done",
               detail="%d writes verified; auto-reset check rc=0 before and "
                      "after" % verified)
    return out


def ota_flash(image, params, settings, logdir, hold=None):
    """The second built-in, "ota": an app image into a WiCAN over its hotspot
    (FLASH-SPEC, ota.py). It is also given the runner's hold of the bench (its
    takes_hold attribute; Engine.ensure_image). ota.py is imported when it is
    first called, so `python -m vtrux_bench.run.ota` runs it as the only copy."""
    from .ota import ota_flash as flasher
    return flasher(image, params, settings, logdir, hold=hold)


ota_flash.takes_hold = True

BUILTIN = {"esptool": esptool_flash, "ota": ota_flash}
