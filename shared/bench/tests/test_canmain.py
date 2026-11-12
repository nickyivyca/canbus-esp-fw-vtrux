"""Tests of the run's canmain check (vtrux_bench.run.canmain). No hardware.

    python -B shared/bench/tests/test_canmain.py            every test
    python -B shared/bench/tests/test_canmain.py --can-fail  every test again
        under each of its mutants (a broken copy of the rule it guards),
        each of which must make it FAIL
    python -B shared/bench/tests/test_canmain.py NAME...     some tests

Written from SPEC.md section 8 ("A run checks CAN1 at its start"; CAN1's
Innomaker is canmain) and the gen-inhibit reviewer's rulings of 2026-11-11:
the check fails closed, a missing setting refuses, the setting names its
probe, "absent" is a check that no adapter answering to canmain is present,
and the reason is printed with the start line. The probes read a made-up
sysfs tree and a made-up Plug and Play listing, so most run in either
environment; test_unbound (whose sysfs names hold ":") and test_real_linux
run on Linux only, test_real_windows on Windows only, and each prints "NOT
RUN (other env)" in the other one. The verdict needs a run in each.

A mutant is a text edit of canmain.py's source, executed into the module;
an edit whose text is not found exactly once is NOT APPLIED and counts as a
failure. Files go to this run's own folder (_scratch.py).
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench import bench_lock as BL           # noqa: E402
from vtrux_bench.run import canmain as C           # noqa: E402
import _scratch                                    # noqa: E402

SCRATCH = _scratch.run_dir("canmain")
SRC = os.path.join(os.path.dirname(HERE), "vtrux_bench", "run", "canmain.py")
ENV = BL.detect_env()
SERIAL = "TESTCANMAIN01"
REASON = "made-up reason"


class Fail(Exception):
    pass


def check(cond, what):
    if not cond:
        raise Fail(what)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except C.Refused as e:
        return str(e)
    raise Fail("%s was not refused" % fn.__name__)


def only(env):
    def mark(t):
        t.env = env
        return t
    return mark


class Sys:
    """A made-up sysfs tree: USB devices and network interfaces."""

    def __init__(self, name):
        self.root = os.path.join(SCRATCH, name)
        if os.path.exists(self.root):
            shutil.rmtree(self.root)
        self.usb = os.path.join(self.root, "bus", "usb", "devices")
        self.net = os.path.join(self.root, "class", "net")
        os.makedirs(self.usb)
        os.makedirs(self.net)
        self.device("usb1", "0000:00:14.0")          # a root hub

    def device(self, name, serial):
        d = os.path.join(self.usb, name)
        os.makedirs(d)
        with open(os.path.join(d, "serial"), "w") as f:
            f.write(serial + "\n")

    def interface(self, name, driver=None):
        d = os.path.join(self.usb, name)
        os.makedirs(d)
        if driver:
            target = os.path.join(self.root, "bus", "usb", "drivers", driver)
            os.makedirs(target, exist_ok=True)
            try:
                os.symlink(target, os.path.join(d, "driver"))
            except OSError:          # no symlink rights
                os.makedirs(os.path.join(d, "driver"))

    def iface(self, name, flags):
        d = os.path.join(self.net, name)
        os.makedirs(d)
        with open(os.path.join(d, "flags"), "w") as f:
            f.write(flags + "\n")


def serial_file(name, text):
    p = os.path.join(SCRATCH, name + ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


GOOD = None


def good_file():
    global GOOD
    if GOOD is None:
        GOOD = serial_file("serial_good", "# Bench\n\n- canmain serial: `%s`\n"
                           "- canchg serial: OTHER1\n" % SERIAL)
    return GOOD


def setting(probe, **kw):
    s = {"probe": probe, "reason": REASON, "iface": "canmain",
         "serial_file": good_file()}
    s.update(kw)
    return {"canmain_check": {k: v for k, v in s.items() if v is not None}}


# ---- the tests ----------------------------------------------------------

def test_config():
    """A missing setting refuses (fail closed); the setting names its probe,
    one of link, unbound and absent, with its reason and the keys that
    probe needs; unknown keys refuse."""
    msg = refused(C.config, {})
    check("no canmain_check" in msg, "no setting: %s" % msg)
    for bad, want in (
            (setting("down"), "is not one of"),
            (setting(None), "is not one of"),
            (setting("absent", reason=""), "needs reason"),
            (setting("absent", reason=None), "needs reason"),
            (setting("link", iface=None), "needs iface"),
            (setting("unbound", serial_file=None), "needs serial_file"),
            (setting("absent", iface=None), "needs iface"),
            (setting("absent", serial_file=None), "needs serial_file"),
            (setting("absent", serial="X"), "unknown keys"),
            (setting("link", iface="can main"), "not an interface name"),
            ({"canmain_check": "absent"}, "not an object")):
        msg = refused(C.config, bad)
        check(want in msg, "%r: %s" % (bad["canmain_check"], msg))
    check(C.config(setting("link", serial_file=None))["probe"] == "link",
          "link needs no serial file")


def test_serial_file():
    """canmain's serial comes from its local file, exactly one
    "canmain serial: <serial>" line; never printed in a message."""
    check(C.read_serial(good_file()) == SERIAL, "the serial is read")
    check(C.read_serial(serial_file("plain", "canmain serial: %s\n" % SERIAL))
          == SERIAL, "without bullet or backticks")
    for name, text in (("none", "canchg serial: X1\n"),
                       ("two", "canmain serial: %s\ncanmain serial: X2\n"
                        % SERIAL),
                       ("mid", "the canmain serial: X3\n")):
        msg = refused(C.read_serial, serial_file(name, text))
        check("not one" in msg and SERIAL not in msg,
              "%s: refused without the serial: %s" % (name, msg))
    msg = refused(C.read_serial, os.path.join(SCRATCH, "missing.md"))
    check("cannot be read" in msg, "a missing file: %s" % msg)


def test_link():
    """Probe link (Linux): the interface exists and its link is down; no
    such interface refuses."""
    s = Sys("link")
    st = setting("link")
    msg = refused(C.check, st, env="linux", root=s.root)
    check("no network interface canmain" in msg, "missing: %s" % msg)
    s.iface("canmain", "0x1003")
    msg = refused(C.check, st, env="linux", root=s.root)
    check("canmain is up" in msg, "up: %s" % msg)
    shutil.rmtree(os.path.join(s.net, "canmain"))
    s.iface("canmain", "0x1002")
    line = C.check(st, env="linux", root=s.root)
    check("link is down" in line, "down: %s" % line)


@only("linux")
def test_unbound():
    """Probe unbound (Linux): exactly one USB device carries canmain's
    serial, and none of its interfaces is bound to a driver; none or
    several refuses; another device's binding does not count. Linux only:
    a USB interface's sysfs name ("1-4:1.0") cannot be a Windows file
    name."""
    s = Sys("unbound")
    st = setting("unbound")
    msg = refused(C.check, st, env="linux", root=s.root)
    check("no USB device" in msg and SERIAL not in msg, "none: %s" % msg)
    s.device("1-5", SERIAL + "9")                     # a longer serial
    s.interface("1-5:1.0", driver="gs_usb")
    refused(C.check, st, env="linux", root=s.root)
    s.device("1-4", SERIAL)
    s.interface("1-4:1.0")
    line = C.check(st, env="linux", root=s.root)
    check("1-4" in line and "no interface bound" in line and
          SERIAL not in line, "unbound: %s" % line)
    shutil.rmtree(os.path.join(s.usb, "1-4:1.0"))
    s.interface("1-4:1.0", driver="usbfs")
    msg = refused(C.check, st, env="linux", root=s.root)
    check("is bound" in msg and "1-4:1.0" in msg, "bound: %s" % msg)
    shutil.rmtree(os.path.join(s.usb, "1-4:1.0"))
    s.interface("1-4:1.0")
    s.device("2-1", SERIAL)
    msg = refused(C.check, st, env="linux", root=s.root)
    check("2 USB devices" in msg, "several: %s" % msg)


def test_absent_linux():
    """Probe absent on Linux is a check: no network interface by canmain's
    role name and no USB device with its serial; an empty listing proves
    nothing and refuses."""
    s = Sys("absent_linux")
    st = setting("absent")
    s.device("1-5", SERIAL + "9")
    line = C.check(st, env="linux", root=s.root)
    check("no network interface canmain" in line and "among 2 USB devices"
          in line, "absent: %s" % line)
    s.iface("canmain", "0x1002")
    msg = refused(C.check, st, env="linux", root=s.root)
    check("canmain is present" in msg, "the interface, even down: %s" % msg)
    shutil.rmtree(os.path.join(s.net, "canmain"))
    s.device("1-4", SERIAL)
    msg = refused(C.check, st, env="linux", root=s.root)
    check("canmain's serial" in msg and "1-4" in msg and SERIAL not in msg,
          "the device: %s" % msg)
    e = Sys("absent_empty")
    shutil.rmtree(os.path.join(e.usb, "usb1"))
    msg = refused(C.check, st, env="linux", root=e.root)
    check("no device at all" in msg, "an empty listing: %s" % msg)


def test_absent_windows():
    """Probe absent on Windows: no present Plug and Play device whose
    instance id ends in canmain's serial (any case); an empty listing
    refuses; link and unbound are Linux only; other environments refuse."""
    st = setting("absent")
    hub = "USB\\ROOT_HUB30\\4&2D0A1B&0&0"
    line = C.check(st, env="windows", ids=[hub, "USB\\VID_1D50&PID_606F\\"
                                           + SERIAL + "9"])
    check("among 2 Plug and Play devices" in line, "absent: %s" % line)
    msg = refused(C.check, st, env="windows",
                  ids=[hub, "USB\\VID_1D50&PID_606F\\" + SERIAL.lower()])
    check("carry canmain's serial" in msg and SERIAL not in msg,
          "present: %s" % msg)
    msg = refused(C.check, st, env="windows", ids=[])
    check("no device at all" in msg, "empty: %s" % msg)
    for probe in ("link", "unbound"):
        msg = refused(C.check, setting(probe), env="windows", ids=[hub])
        check("Linux only" in msg, "%s on windows: %s" % (probe, msg))
    msg = refused(C.check, st, env="wsl", ids=[hub])
    check("does not run in the wsl" in msg, "wsl: %s" % msg)


def test_line():
    """The start line names the probe and its reason and says canmain is
    down; it names the serial's file, never the serial."""
    s = Sys("line")
    line = C.check(setting("absent"), env="linux", root=s.root)
    check(line.startswith("canmain check: probe absent (%s): canmain is down"
                          % REASON) and good_file() in line and
          SERIAL not in line, "the line: %s" % line)
    check(C.plan_line(setting("unbound")) ==
          "first: canmain check, probe unbound (%s)" % REASON, "plan line")
    check("the run refuses" in C.plan_line({}), "plan line, no setting")


@only("linux")
def test_real_linux():
    """This machine's sysfs: the probes see its USB devices (root hubs have
    serials) and its interfaces (lo is up), so absent is not vacuous."""
    check(C.usb_count("/sys") > 0, "no USB device with a serial in /sys")
    check(C.iface_up("/sys", "lo"), "lo is not seen up")
    msg = refused(C.check, setting("link", iface="lo"), env="linux",
                  root="/sys")
    check("lo is up" in msg, "link on lo: %s" % msg)
    line = C.check(setting("absent", iface="nosuchcan0"), env="linux",
                   root="/sys")
    check("among" in line, "absent on this machine: %s" % line)


@only("windows")
def test_real_windows():
    """This machine's Plug and Play listing has its USB root hubs, so
    absent is not vacuous."""
    ids = C.pnp_ids()
    check(any(i.upper().startswith("USB\\ROOT_HUB") for i in ids),
          "no USB root hub among %d Plug and Play ids" % len(ids))
    line = C.check(setting("absent"), env="windows")
    check("among %d" % len(ids) in line or "among" in line,
          "absent on this machine: %s" % line)


TESTS = [test_config, test_serial_file, test_link, test_unbound,
         test_absent_linux, test_absent_windows, test_line,
         test_real_linux, test_real_windows]


# ---- mutants ------------------------------------------------------------

with open(SRC, encoding="utf-8") as _f:
    ORIGINAL = _f.read()


def _load(text):
    exec(compile(text, SRC, "exec"), C.__dict__)


def _edit(old, new):
    def on():
        n = ORIGINAL.count(old)
        if n != 1:
            raise RuntimeError("mutant text found %d times: %r" % (n, old[:60]))
        _load(ORIGINAL.replace(old, new))

    def off():
        _load(ORIGINAL)
    return on, off


MUTANTS = {
    "test_config": [
        ("a missing setting defaults",
         _edit("    c = settings.get(SETTING)\n    if c is None:\n        raise",
               "    c = settings.get(SETTING) or {\"probe\": \"absent\", \"reason\": \"d\", "
               "\"iface\": \"canmain\", \"serial_file\": \"x\"}\n    if c is None:\n        raise")),
        ("reason optional",
         _edit("    for key in (\"reason\",) + NEEDS[c[\"probe\"]]:",
               "    for key in NEEDS[c[\"probe\"]]:")),
        ("unknown keys allowed",
         _edit("    if extra:\n", "    if False:\n")),
        ("absent needs no iface",
         _edit("\"absent\": (\"iface\", \"serial_file\")}", "\"absent\": (\"serial_file\",)}")),
        ("any interface name",
         _edit("    if \"iface\" in NEEDS[c[\"probe\"]] and not IFACE_RE.match(c[\"iface\"]):",
               "    if False:")),
    ],
    "test_serial_file": [
        ("the first of several lines",
         _edit("    if len(found) != 1:\n", "    if not found:\n")),
        ("the serial anywhere in a line",
         _edit("SERIAL_RE = re.compile(r\"^\\s*(?:[-*]\\s+)?canmain serial:",
               "SERIAL_RE = re.compile(r\"^.*?canmain serial:")),
        ("the serial in the message",
         _edit("        raise Refused(\"%s has %d \\\"canmain serial: <serial>\\\" lines, not one\"\n"
               "                      % (path, len(found)))",
               "        raise Refused(\"%s has %d \\\"canmain serial: <serial>\\\" lines, not one: %s\"\n"
               "                      % (path, len(found), found))")),
    ],
    "test_link": [
        ("a missing interface counts as down",
         _edit("    if not os.path.isdir(iface_dir(root, iface)):\n        raise",
               "    if False:\n        raise")),
        ("IFF_UP ignored",
         _edit("    if iface_up(root, iface):\n", "    if False:\n")),
    ],
    "test_unbound": [
        ("no device counts as unbound",
         _edit("    if not devs:\n        raise Refused(\"no USB device",
               "    if not devs:\n        return \"none\"\n        raise Refused(\"no USB device")),
        ("several devices: the first",
         _edit("    if len(devs) > 1:\n", "    if False:\n")),
        ("a binding ignored",
         _edit("    if b:\n        raise Refused(\"canmain (USB", "    if False:\n        raise Refused(\"canmain (USB")),
        ("another device's binding counts",
         _edit("        if name.startswith(dev + \":\") and os.path.lexists(drv):",
               "        if \":\" in name and os.path.lexists(drv):")),
        ("a serial prefix matches",
         _edit("os.path.isfile(p) and _read(p) == serial:",
               "os.path.isfile(p) and _read(p).startswith(serial):")),
    ],
    "test_absent_linux": [
        ("the interface not looked for",
         _edit("    if os.path.lexists(iface_dir(root, iface)):\n", "    if False:\n")),
        ("the serial not looked for",
         _edit("    devs = usb_with_serial(root, serial)\n    if devs:\n",
               "    devs = []\n    if devs:\n")),
        ("an empty listing passes",
         _edit("    if n == 0:\n", "    if False:\n")),
    ],
    "test_absent_windows": [
        ("the serial not looked for",
         _edit("    if hits:\n", "    if False:\n")),
        ("a case-sensitive match",
         _edit(".upper()\n            == serial.upper()]", "\n            == serial]")),
        ("an empty listing passes",
         _edit("    if not ids:\n", "    if False:\n")),
        ("link runs on windows",
         _edit("        if probe != \"absent\":\n", "        if False:\n")),
        ("any environment",
         _edit("    else:\n        raise Refused(\"the canmain check does not run in the %s environment\"\n"
               "                      % env)",
               "    else:\n        what = \"nothing checked\"")),
    ],
    "test_line": [
        ("the reason not printed",
         _edit("        probe, c[\"reason\"], what)", "        probe, \"-\", what)")),
        ("the serial printed",
         _edit("    return \"no network interface %s and no USB device with canmain's serial \" \\\n"
               "           \"(from %s), among %d USB devices\" % (iface, c[\"serial_file\"], n)",
               "    return \"no network interface %s and no USB device with canmain's serial \" \\\n"
               "           \"(from %s), among %d USB devices\" % (iface, serial, n)")),
    ],
    "test_real_linux": [
        ("no USB device counted",
         _edit("    return sum(1 for n in os.listdir(base) if \":\" not in n",
               "    return 0 and sum(1 for n in os.listdir(base) if \":\" not in n"), "linux"),
        ("the wrong up flag",
         _edit("IFF_UP = 0x1\n", "IFF_UP = 0x2\n"), "linux"),
    ],
    "test_real_windows": [
        ("an empty listing",
         _edit("    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]",
               "    return []"), "windows"),
        ("the wrong command",
         _edit("ForEach-Object { $_.PNPDeviceID }", "ForEach-Object { $_.Name }"),
         "windows"),
    ],
}


def _run(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    print("environment: %s" % ENV)
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = total = other = 0
    for t in tests:
        if getattr(t, "env", ENV) != ENV:
            print("NOT RUN (other env)  %-22s %s only" % (t.__name__, t.env))
            other += 1
            continue
        if not can_fail:
            total += 1
            outcome = _run(t)
            print("%s  %-22s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""))
            bad += outcome is not None
            continue
        for m in MUTANTS[t.__name__]:
            label, (on, off) = m[0], m[1]
            if len(m) > 2 and m[2] != ENV:
                print("NOT RUN (other env)  %-22s %-34s %s only"
                      % (t.__name__, label, m[2]))
                other += 1
                continue
            total += 1
            try:
                on()
            except RuntimeError as e:
                print("FAIL  %-22s %-34s NOT APPLIED: %s" % (t.__name__, label, e))
                bad += 1
                continue
            try:
                outcome = _run(t)
            finally:
                off()
            ok = outcome is not None
            print("%s  %-22s %-34s %s" % (
                "ok  " if ok else "FAIL", t.__name__, label,
                ("fails (" + outcome[:70] + ")") if ok else
                "STILL PASSES -- the test checks nothing"))
            bad += not ok
    print("\n%d of %d %s; %d NOT RUN (other env)"
          % (total - bad, total, "mutants caught" if can_fail else "passed",
             other))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
