"""The one place gen-inhibit's harness finds esptool, and the version it must be.

Gen-inhibit tester, 2026-11-11, on the gen-inhibit reviewer's ruling of the same day
(esptool, option 1 with its part (b)):

  - esptool is the argv the chain settings name, settings.esptool.argv, of the form
    [<python>, "-m", "esptool"]. The suite passes it to the tools it runs as the
    environment variable GI_ESPTOOL_ARGV, a JSON list. Unset, not a JSON list, or any
    other form refuses: there is no default (before, ~/.platformio's esptool.py).
  - Its version must be PINNED, read by `<argv> version` (esptool prints it as its last
    line). Any other version refuses, before esptool touches a board. Each tool puts the
    argv and the version read in its record.
  - Command names are the underscore ones (chip_id, read_mem, read_flash, write_flash,
    verify_flash), which esptool v4 requires; v5 takes them too, with warnings.
  - VERIFY_OK is the line the PINNED version prints once per file when verify_flash
    finds the digest matched (esptool/cmds.py of 4.13.0, in ESP-IDF v5.4.2's python env,
    read 2026-11-11). Only the pinned version's line counts (reviewer, 2026-11-11), so a
    version change cannot pass on another version's text.

Nothing here opens a port. version() runs esptool with no port and no board.
"""
import json
import os
import re
import subprocess

ENV = "GI_ESPTOOL_ARGV"
PINNED = "4.13.0"
VERIFY_OK = {"4.13.0": "-- verify OK (digest matched)"}[PINNED]
CHIP = "esp32s3"


class Refused(Exception):
    """esptool cannot be used as the ruling requires; the reason is the message."""


def check_form(argv):
    """argv as a list if it is [<python>, "-m", "esptool"], else Refused."""
    if not (isinstance(argv, list) and argv[1:] == ["-m", "esptool"] and isinstance(argv[0], str)
            and argv[0].strip()):
        raise Refused("esptool argv must be [<python>, \"-m\", \"esptool\"] "
                      "(settings.esptool.argv), not %r" % (argv,))
    return list(argv)


def from_env(environ=None):
    """The argv from GI_ESPTOOL_ARGV, form checked. Refused when it is unset or wrong."""
    raw = (os.environ if environ is None else environ).get(ENV, "")
    if not raw.strip():
        raise Refused("%s is not set: the suite passes settings.esptool.argv there; "
                      "there is no default esptool" % ENV)
    try:
        argv = json.loads(raw)
    except ValueError:
        raise Refused("%s is not a JSON list: %r" % (ENV, raw[:200]))
    return check_form(argv)


def read_version(text):
    """The version esptool's `version` printed: its last non-empty line, if that is a bare
    x.y.z (v5 prints warning lines before it), else None."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if lines and re.fullmatch(r"\d+\.\d+\.\d+", lines[-1]):
        return lines[-1]
    return None


def version(argv, timeout=60):
    """(version, output) of `<argv> version`; version None when it could not be read."""
    try:
        p = subprocess.run(list(argv) + ["version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "%s: %s" % (type(e).__name__, e)
    out = p.stdout + p.stderr
    return (read_version(p.stdout) if p.returncode == 0 else None), out


def check_version(argv):
    """The version read, if it is PINNED; else Refused, naming what was read."""
    v, out = version(argv)
    if v != PINNED:
        tail = [ln for ln in out.splitlines() if ln.strip()][-2:]
        raise Refused("esptool %s is version %s, not the pinned %s (%s)"
                      % (argv, v, PINNED, " | ".join(tail)))
    return v


def checked(environ=None):
    """{"argv", "version"} for a record: the argv from the environment, its version the
    pinned one. Refused otherwise."""
    argv = from_env(environ)
    return {"argv": argv, "version": check_version(argv)}
