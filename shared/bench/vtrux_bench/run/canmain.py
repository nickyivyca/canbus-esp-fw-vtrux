"""The run's canmain check (SPEC.md section 8, "A run checks CAN1 at its
start"): beside listening first, a run that needs the bench makes sure
canmain, CAN1's Innomaker, is down, so a hand release that went wrong, or a
crash, cannot leave a second ACKing node on CAN1 under checks that need
none. The runner makes it, for every suite (the testers' call, which the
spec leaves to them: the charge-interposer and gen-inhibit testers,
2026-11-13).

Since the lock became a mutex (user, 2026-11-13: a run brings canmain down
itself instead of refusing; shared/main 9342bf8b), the runner checks only
the setting before its take (config()), and after its own take, and again
at every resume before the start hooks, calls bring_down(): a probe made
then, never an earlier read (the gen-inhibit tester, 2026-11-13: a read
from before the take is stale); if it shows canmain up (probe link, on
Linux), `sudo -n /usr/local/sbin/canctl down <iface>`, then the probe again.
The line it returns names the holder whose run brought canmain down, so
the next holder sees why it is down. canctl failing, or the probe after it
still not showing canmain down, raises Refused, and the runner stops the
bench. Probes unbound and absent have no canctl action: they show canmain
down or refuse, as before. Before the take nothing is brought down, so a
run never takes canmain down under another role's lock.

It fails closed. Settings with no canmain_check refuse the run. The setting
names its probe, and every probe is a check:

    "canmain_check": {
        "probe":       "link" | "unbound" | "absent",
        "reason":      why this probe, printed with the check's start line,
        "iface":       canmain's SocketCAN interface (its canctl role name),
        "serial_file": the local file holding canmain's serial (reverse-it
                       firmware/bench-holder.md, never git), on a line
                       "canmain serial: <serial>"
    }

  link     (Linux) the interface exists and is down (IFF_UP clear in
           /sys/class/net/<iface>/flags). No such interface refuses: the
           device may be passed through or unbound, and up elsewhere.
           Needs iface.
  unbound  (Linux) exactly one USB device carries canmain's serial, and no
           interface of it is bound to a driver (usbfs, an emulator's
           passthrough, counts as bound). None or several refuses. Needs
           serial_file.
  absent   no adapter answering to canmain is present: on Linux no network
           interface named iface and no USB device with canmain's serial;
           on Windows (no SocketCAN, so no role name) no present Plug and
           Play device whose instance id ends in canmain's serial, read with
           PowerShell's Get-CimInstance Win32_PnPEntity. A listing that
           cannot be read refuses. Needs iface and serial_file.

Which of link and unbound is canmain's down is settled at step 3's adapter
characterisation. New bench hardware tooling is Linux only (user,
2026-11-11), so on Windows only absent works; a run there before the move
uses it, with its reason. The serial is never printed: the line names the
file it came from.
"""

import os
import re
import subprocess

from .. import bench_lock as BL

SETTING = "canmain_check"
PROBES = ("link", "unbound", "absent")
NEEDS = {"link": ("iface",), "unbound": ("serial_file",),
         "absent": ("iface", "serial_file")}
KEYS = ("probe", "reason", "iface", "serial_file")
SERIAL_RE = re.compile(r"^\s*(?:[-*]\s+)?canmain serial:\s*`?([A-Za-z0-9._:-]+)`?"
                       r"\s*$", re.M)
IFACE_RE = re.compile(r"[A-Za-z0-9._-]{1,15}\Z")
IFF_UP = 0x1
SYS_ROOT = "/sys"          # tests point it at a made-up tree
CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]    # tests point it at a fake
CANCTL_TIMEOUT_S = 60
RC_MEANING = {2: "refused", 3: "did not take"}       # canctl's header


class Refused(Exception):
    """The run refuses: canmain is not shown down, or the check cannot run."""


def config(settings):
    """The canmain_check setting, checked. Refused if missing or malformed."""
    c = settings.get(SETTING)
    if c is None:
        raise Refused("the settings have no %s: a run that needs the bench "
                      "makes sure canmain is down (SPEC section 8); give "
                      "its probe (%s) and the reason for it"
                      % (SETTING, ", ".join(PROBES)))
    if not isinstance(c, dict):
        raise Refused("%s is not an object" % SETTING)
    extra = sorted(set(c) - set(KEYS))
    if extra:
        raise Refused("%s has unknown keys %s" % (SETTING, ", ".join(extra)))
    if c.get("probe") not in PROBES:
        raise Refused("%s.probe %r is not one of %s"
                      % (SETTING, c.get("probe"), ", ".join(PROBES)))
    for key in ("reason",) + NEEDS[c["probe"]]:
        v = c.get(key)
        if not isinstance(v, str) or not v.strip():
            raise Refused("%s with probe %s needs %s"
                          % (SETTING, c["probe"], key))
    if "iface" in NEEDS[c["probe"]] and not IFACE_RE.match(c["iface"]):
        raise Refused("%s.iface %r is not an interface name"
                      % (SETTING, c["iface"]))
    return c


def read_serial(path):
    """canmain's serial from its local file: exactly one line
    "canmain serial: <serial>". Refused otherwise; the serial itself is
    never put in a message."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        raise Refused("canmain's serial file %s cannot be read: %s"
                      % (path, e.strerror or e))
    found = SERIAL_RE.findall(text)
    if len(found) != 1:
        raise Refused("%s has %d \"canmain serial: <serial>\" lines, not one"
                      % (path, len(found)))
    return found[0]


# ---- Linux: sysfs ------------------------------------------------------

def _read(path):
    with open(path, encoding="ascii", errors="replace") as f:
        return f.read().strip()


def iface_dir(root, iface):
    return os.path.join(root, "class", "net", iface)


def iface_up(root, iface):
    """True if the interface's IFF_UP flag is set."""
    return bool(int(_read(os.path.join(iface_dir(root, iface), "flags")), 16)
                & IFF_UP)


def usb_with_serial(root, serial):
    """The USB devices (not interfaces) whose serial is this one."""
    base = os.path.join(root, "bus", "usb", "devices")
    out = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name, "serial")
        if ":" not in name and os.path.isfile(p) and _read(p) == serial:
            out.append(name)
    return out


def usb_count(root):
    """How many USB devices have a serial file (root hubs included): a
    listing that sees none cannot show anything absent."""
    base = os.path.join(root, "bus", "usb", "devices")
    return sum(1 for n in os.listdir(base) if ":" not in n
               and os.path.isfile(os.path.join(base, n, "serial")))


def bound(root, dev):
    """[(interface, driver)] for each interface of the device bound to a
    driver."""
    base = os.path.join(root, "bus", "usb", "devices")
    out = []
    for name in sorted(os.listdir(base)):
        drv = os.path.join(base, name, "driver")
        if name.startswith(dev + ":") and os.path.lexists(drv):
            out.append((name, os.path.basename(os.path.realpath(drv))))
    return out


def probe_link(c, root):
    iface = c["iface"]
    if not os.path.isdir(iface_dir(root, iface)):
        raise Refused("no network interface %s: its link cannot be shown "
                      "down (passed through or unbound, it may be up "
                      "elsewhere; unplugged, the probe is absent)" % iface)
    if iface_up(root, iface):
        raise Refused("%s is up: take it down (ip link set %s down) first"
                      % (iface, iface))
    return "%s exists and its link is down" % iface


def probe_unbound(c, root, serial):
    devs = usb_with_serial(root, serial)
    if not devs:
        raise Refused("no USB device carries canmain's serial (from %s): it "
                      "cannot be shown unbound (unplugged, the probe is "
                      "absent)" % c["serial_file"])
    if len(devs) > 1:
        raise Refused("%d USB devices carry canmain's serial (%s): which one "
                      "is canmain is not known" % (len(devs), ", ".join(devs)))
    b = bound(root, devs[0])
    if b:
        raise Refused("canmain (USB %s) is bound: %s" % (devs[0], ", ".join(
            "%s to %s" % x for x in b)))
    return "canmain (USB %s, serial from %s) has no interface bound to a " \
           "driver" % (devs[0], c["serial_file"])


def probe_absent_linux(c, root, serial):
    iface = c["iface"]
    if os.path.lexists(iface_dir(root, iface)):
        raise Refused("network interface %s is present: canmain is not "
                      "absent" % iface)
    try:
        n = usb_count(root)
    except OSError as e:
        raise Refused("the USB devices cannot be listed: %s" % e)
    if n == 0:
        raise Refused("the USB device listing shows no device at all: it "
                      "cannot show canmain absent")
    devs = usb_with_serial(root, serial)
    if devs:
        raise Refused("a USB device with canmain's serial (from %s) is "
                      "present (%s): canmain is not absent"
                      % (c["serial_file"], ", ".join(devs)))
    return "no network interface %s and no USB device with canmain's serial " \
           "(from %s), among %d USB devices" % (iface, c["serial_file"], n)


# ---- Windows: Plug and Play --------------------------------------------

def pnp_ids():
    """Every present Plug and Play device's instance id. Raises Refused if
    the listing cannot be read."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           "Get-CimInstance Win32_PnPEntity | ForEach-Object { $_.PNPDeviceID }"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused("the Plug and Play listing could not be read: %s" % e)
    if r.returncode != 0:
        raise Refused("the Plug and Play listing failed (exit %d)"
                      % r.returncode)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def probe_absent_windows(c, serial, ids):
    if not ids:
        raise Refused("the Plug and Play listing shows no device at all: it "
                      "cannot show canmain absent")
    hits = [i for i in ids if i.replace("/", "\\").split("\\")[-1].upper()
            == serial.upper()]
    if hits:
        raise Refused("%d present device(s) carry canmain's serial (from %s): "
                      "canmain is not absent" % (len(hits), c["serial_file"]))
    return "no present device with canmain's serial (from %s), among %d " \
           "Plug and Play devices (windows: no SocketCAN, so no interface " \
           "name to look for)" % (c["serial_file"], len(ids))


def check(settings, env=None, root=None, ids=None):
    """Run the canmain check the settings name. Returns its start line;
    raises Refused if canmain is not shown down or the check cannot run."""
    c = config(settings)
    env = env or BL.this_env()
    root = SYS_ROOT if root is None else root
    probe = c["probe"]
    serial = read_serial(c["serial_file"]) if "serial_file" in NEEDS[probe] \
        else None
    if env == "linux":
        try:
            if probe == "link":
                what = probe_link(c, root)
            elif probe == "unbound":
                what = probe_unbound(c, root, serial)
            else:
                what = probe_absent_linux(c, root, serial)
        except OSError as e:
            raise Refused("probe %s could not read sysfs: %s" % (probe, e))
    elif env == "windows":
        if probe != "absent":
            raise Refused("probe %s is Linux only (new bench hardware tooling "
                          "is Linux only); on windows only absent can run"
                          % probe)
        what = probe_absent_windows(c, serial, pnp_ids() if ids is None
                                    else ids)
    else:
        raise Refused("the canmain check does not run in the %s environment"
                      % env)
    return "canmain check: probe %s (%s): canmain is down: %s" % (
        probe, c["reason"], what)


def link_is_up(c, root):
    """True only when probe link's interface exists and its IFF_UP is set:
    the one state canctl down can fix. Unreadable counts as not up, so the
    probe's own refusal stands."""
    try:
        return os.path.isdir(iface_dir(root, c["iface"])) and \
            iface_up(root, c["iface"])
    except (OSError, ValueError):
        return False


def canctl_down(iface):
    """sudo -n canctl down <iface>. -> (rc or None if it could not run,
    the command, the last line of its output)."""
    argv = list(CANCTL) + ["down", iface]
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=CANCTL_TIMEOUT_S)
        out, rc = r.stdout.decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired:
        out, rc = "timed out after %d s" % CANCTL_TIMEOUT_S, None
    except OSError as e:
        out, rc = "could not run: %s" % e, None
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return rc, "canctl down %s" % iface, (lines[-1] if lines else "no output")


def bring_down(settings, holder, env=None, root=None, ids=None):
    """SPEC section 8 (user, 2026-11-13): made by the runner after its own
    take and at every resume. A probe made now; if it shows canmain up
    (probe link, on Linux), canctl down, then the probe again. `holder`
    names whose run it is ("<role>'s run <id>"). Returns (line, info):
    the line for the start and record.json, info for the run's canmain
    event (action None when canmain was already down). Raises Refused when
    canmain is not shown down at the end, saying why."""
    c = config(settings)
    env = env or BL.this_env()
    root = SYS_ROOT if root is None else root
    try:
        return check(settings, env=env, root=root, ids=ids), dict(
            action=None, holder=holder)
    except Refused as first:
        if not (c["probe"] == "link" and env == "linux"
                and link_is_up(c, root)):
            raise
        before = str(first)
    rc, action, last = canctl_down(c["iface"])
    if rc != 0:
        raise Refused("canmain was up (%s); `%s` under %s failed (rc %s%s): %s"
                      % (before, action, holder, rc,
                         (", %s" % RC_MEANING[rc]) if rc in RC_MEANING else "",
                         last))
    try:
        after = check(settings, env=env, root=root, ids=ids)
    except Refused as e:
        raise Refused("canmain was up (%s); after `%s` under %s (rc 0) it is "
                      "still not shown down: %s" % (before, action, holder, e))
    return ("canmain check: probe %s (%s): canmain was up (%s); brought down "
            "under %s by `%s` (rc 0); now: %s" % (
                c["probe"], c["reason"], before, holder, action, after),
            dict(action=action, rc=rc, before=before, holder=holder))


def plan_line(settings):
    """The plan's line for the check, without running it."""
    c = settings.get(SETTING)
    if not isinstance(c, dict):
        return "first: canmain check, no %s setting: the run refuses" % SETTING
    return "first: canmain check, probe %s (%s); after the take and at each " \
           "resume, brought down through canctl if probe link shows it up" % (
               c.get("probe"), c.get("reason"))
