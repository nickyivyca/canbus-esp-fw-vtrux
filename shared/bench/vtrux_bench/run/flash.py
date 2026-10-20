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

Image.params:
    files     [[offset, path], ...]  e.g. [["0x0", ".../bootloader.bin"], ...]
    md5       {path: md5}            optional expected digests
    port      e.g. "COM3"; default settings["esptool"]["port"]
    baud      default settings["esptool"]["baud"] or 921600
    chip      {"type": "ESP32-S3", "mac": "<the board's MAC>"}, both
              REQUIRED; default settings["esptool"]["chip"]. The repo is
              public: a suite reads the MAC from its local, unpublished
              settings at run time, never from a literal in a file
Settings:
    esptool   {"argv": ["py", "-3.14", ".../esptool.py"], "port": "COM3",
               "baud": 921600}
"""

import hashlib
import os
import re
import subprocess

from .procs import run_local

RULES = {"chip_match": True, "chip_required": True}


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
    port = params.get("port") or et.get("port")
    baud = str(params.get("baud") or et.get("baud") or 921600)
    files = params.get("files") or []
    if not port or not files:
        raise ValueError("image %s: port and files are required" % image.name)
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # recoverable: may another flash be attempted in this run? False once
    # an auto-reset check has failed.
    out = dict(ok=False, phase="files", detail="", md5={}, recoverable=True)

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


BUILTIN = {"esptool": esptool_flash}
