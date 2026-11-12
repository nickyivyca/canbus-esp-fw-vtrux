"""Tests of the serial-device lookup (vtrux_bench.serial_dev). No hardware.

    python -B shared/bench/tests/test_serial_dev.py            every test
    python -B shared/bench/tests/test_serial_dev.py --can-fail  every test
        unmutated first (the control, which must pass, or no mutant is
        counted), then again under each of its mutants (a broken copy of
        the rule it guards), each of which must make it FAIL
    python -B shared/bench/tests/test_serial_dev.py NAME...     some tests

Written from DEVICES-SPEC S1-S3 and the gen-inhibit reviewer's rulings of
2026-11-11: a device is named by USB vendor ID, product ID and serial
number, with the interface number on a composite device (one with more than
one interface: a setting that names none there refuses); the port is
/dev/serial/by-id/'s; no match, or more than one, refuses, naming every
device found; what is returned is what the record keeps; the lookup is
Linux only (the user's ruling of 2026-11-11), and refuses elsewhere.

The lookup reads a made-up by-id folder and sysfs tree (_serial_fixture.py),
which need symbolic links, so every test but test_linux_only runs on Linux
only and prints "NOT RUN (other env)" elsewhere. test_real_linux reads this
machine's own /dev/serial/by-id/; it can fail only with a USB serial device
plugged in. No serial number of a real device is printed.

A mutant is a text edit of serial_dev.py's source, executed into the
module; an edit whose text is not found exactly once is NOT APPLIED and
counts as a failure. Files go to this run's own folder (_scratch.py).
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench import bench_lock as BL           # noqa: E402
from vtrux_bench import serial_dev as SD           # noqa: E402
from _serial_fixture import FakeSerial             # noqa: E402
import _scratch                                    # noqa: E402

SCRATCH = _scratch.run_dir("serial_dev")
SRC = os.path.join(os.path.dirname(HERE), "vtrux_bench", "serial_dev.py")
ENV = BL.detect_env()
# made-up serials: an X2 and a WiCAN (both Espressif USB-Serial-JTAG, the
# same IDs, three interfaces), and a supply (two interfaces)
X2, WICAN, SUPPLY = "TESTX2SERIAL01", "TESTWICAN01", "TESTSUPPLY01"
ESP = ("303a", "1001")


class Fail(Exception):
    pass


def check(cond, what):
    if not cond:
        raise Fail(what)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except SD.Refused as e:
        return str(e)
    raise Fail("%s was not refused" % fn.__name__)


def only(env):
    def mark(t):
        t.env = env
        return t
    return mark


def bench(name):
    """A made-up bench: the X2, the WiCAN and the supply plugged in."""
    d = os.path.join(SCRATCH, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    fs = FakeSerial(d)
    fs.x2 = fs.add("ttyACM1", *ESP, serial=X2, interface=0, interfaces=3)
    fs.wican = fs.add("ttyACM2", *ESP, serial=WICAN, interface=0,
                      interfaces=3)
    fs.supply = fs.add("ttyACM0", "0416", "5011", serial=SUPPLY, interface=0,
                       interfaces=2)
    return fs


def dev(serial=X2, interface=0, vid=ESP[0], pid=ESP[1], **kw):
    d = {"vid": vid, "pid": pid, "serial": serial, "interface": interface}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


def resolve(fs, d, env="linux"):
    return SD.resolve(d, env=env, root=fs.root, by_id=fs.by_id)


# ---- the tests ----------------------------------------------------------

def test_config():
    """The setting is an object of vid, pid, serial and, optionally,
    interface: never a COM or tty name, no other key, four hex digits for
    each ID (kept in lower case), a printable serial, an interface number
    0-255."""
    msg = refused(SD.config, "/dev/ttyACM1")
    check("never a COM or tty name" in msg, "a tty name: %s" % msg)
    for bad, want in (
            (dev(port="/dev/ttyACM1"), "unknown keys port"),
            (dev(vid="303"), "vid"), (dev(vid="30x3"), "vid"),
            (dev(vid=0x303a), "vid"), (dev(pid="10011"), "pid"),
            (dev(serial=None), "serial"), (dev(serial=""), "serial"),
            (dev(serial="TEST X2"), "serial"),
            (dev(interface=True), "interface"),
            (dev(interface=-1), "interface"),
            (dev(interface=256), "interface"),
            (dev(interface="0"), "interface")):
        msg = refused(SD.config, bad)
        check(want in msg, "%r: %s" % (bad, msg))
    got = SD.config(dev(vid="303A", pid="1001", interface=None))
    check(got == {"vid": "303a", "pid": "1001", "serial": X2,
                  "interface": None}, "the setting, checked: %r" % got)


@only("linux")
def test_one_match():
    """Exactly one match gives its /dev/serial/by-id/ link (not the tty)
    and, for the record, the IDs it matched, its USB device and its tty;
    a vid or pid in upper case in the setting still matches."""
    fs = bench("one")
    got = resolve(fs, dev(vid="303A"))
    check(got == {"port": fs.x2, "vid": "303a", "pid": "1001",
                  "serial": X2, "interface": 0, "usb": "1-1",
                  "tty": "ttyACM1"}, "the X2: %r" % got)
    got = resolve(fs, dev(SUPPLY, vid="0416", pid="5011"))
    check(got["port"] == fs.supply, "the supply: %r" % got)


@only("linux")
def test_none_refuses():
    """No match refuses, naming every device found with its IDs and port;
    an empty folder, or none at all, refuses as no serial device at all."""
    fs = bench("none")
    msg = refused(resolve, fs, dev("TESTX2SERIAL09"))
    check("no device matches" in msg, "no match: %s" % msg)
    for s, link in ((X2, fs.x2), (WICAN, fs.wican), (SUPPLY, fs.supply)):
        check(s in msg and link in msg, "the refusal names %s: %s" % (s, msg))
    check("0416:5011" in msg and "303a:1001" in msg, "the IDs: %s" % msg)
    e = FakeSerial(os.path.join(SCRATCH, "none_empty"))
    msg = refused(resolve, e, dev())
    check("no serial device at all" in msg, "an empty folder: %s" % msg)
    shutil.rmtree(e.by_id)
    msg = refused(resolve, e, dev())
    check("no serial device at all" in msg, "no folder: %s" % msg)


@only("linux")
def test_several_refuse():
    """More than one match refuses, naming each: it never takes the
    first."""
    fs = bench("several")
    other = fs.add("ttyACM3", *ESP, serial=X2, interface=0, interfaces=3,
                   usb="1-9")
    msg = refused(resolve, fs, dev())
    check("2 devices match" in msg and fs.x2 in msg and other in msg,
          "several: %s" % msg)


@only("linux")
def test_interface():
    """On a composite device the setting must name the interface: none
    refuses, saying so, even with one port found; the interface named
    picks the port; another interface number does not match. A device with
    one interface needs none."""
    fs = bench("intf")
    msg = refused(resolve, fs, dev(interface=None))
    check("has 3 interfaces" in msg and "must name the interface" in msg,
          "composite, no interface: %s" % msg)
    msg = refused(resolve, fs, dev(interface=1))
    check("no device matches" in msg, "interface 1: %s" % msg)
    # bInterfaceNumber is hex in sysfs: interface 16 reads "10"
    second = fs.add("ttyACM5", "1d50", "6018", serial="TESTTWOPORT01",
                    interface=0, interfaces=17, usb="1-7")
    third = fs.add("ttyACM6", "1d50", "6018", serial="TESTTWOPORT01",
                   interface=16, interfaces=17, usb="1-7")
    got = resolve(fs, dev("TESTTWOPORT01", vid="1d50", pid="6018",
                          interface=16))
    check(got["port"] == third and got["interface"] == 16,
          "interface 16: %r" % got)
    got = resolve(fs, dev("TESTTWOPORT01", vid="1d50", pid="6018",
                          interface=0))
    check(got["port"] == second, "interface 0: %r" % got)
    fs.add("ttyUSB0", "0403", "6001", serial="TESTFTDI01", interface=0,
           interfaces=1, converter=True)
    got = resolve(fs, dev("TESTFTDI01", vid="0403", pid="6001",
                          interface=None))
    check(got["tty"] == "ttyUSB0" and got["interface"] == 0,
          "one interface, none named: %r" % got)


@only("linux")
def test_exact():
    """Every ID must match exactly: a serial that only starts the same, or
    another product ID, is no match."""
    fs = bench("exact")
    for d in (dev("TESTX2SERIAL0"), dev(X2 + "1"), dev(pid="1002"),
              dev(vid="303b")):
        msg = refused(resolve, fs, d)
        check("no device matches" in msg, "%r: %s" % (d, msg))


@only("linux")
def test_converter():
    """A USB-serial converter's tty sits one folder below its interface
    (<usb>:1.0/ttyUSB0): it is found and matched by the interface above
    it. A serial port not on USB is listed in a refusal and never
    matched."""
    fs = bench("conv")
    link = fs.add("ttyUSB1", "10c4", "ea60", serial="TESTCP210X01",
                  interface=0, interfaces=1, converter=True)
    got = resolve(fs, dev("TESTCP210X01", vid="10c4", pid="ea60"))
    check(got["port"] == link and got["usb"] and got["interface"] == 0,
          "converter: %r" % got)
    plat = fs.add("ttyS4", name="platform-ttyS4")
    msg = refused(resolve, fs, dev("TESTNONE01"))
    check(plat in msg and "not a USB device" in msg, "non-USB: %s" % msg)


def test_linux_only():
    """The lookup is Linux only (user, 2026-11-11): on Windows, WSL or any
    other environment it refuses, whatever the setting."""
    for env in ("windows", "wsl", "other"):
        msg = refused(SD.resolve, dev(), env=env,
                      root=os.path.join(SCRATCH, "nothing"),
                      by_id=os.path.join(SCRATCH, "nothing"))
        check("Linux only" in msg and env in msg, "%s: %s" % (env, msg))


@only("linux")
def test_real_linux():
    """This machine's /dev/serial/by-id/: the listing has one entry per
    link, each with the tty its link points to, and a made-up device
    matches none of them. Nothing a device says is printed."""
    real = "/dev/serial/by-id"
    names = os.listdir(real) if os.path.isdir(real) else []
    found = SD.listing()
    check(len(found) == len(names), "%d entries for %d links"
          % (len(found), len(names)))
    for e in found:
        check(os.path.dirname(e["port"]) == real and e["tty"] ==
              os.path.basename(os.path.realpath(e["port"])),
              "an entry's port and tty do not agree")
    try:
        SD.resolve(dev("TESTNOSUCHDEVICE01"), env="linux")
        raise Fail("a made-up device was matched")
    except SD.Refused as e:
        check("no device matches" in str(e), "a made-up device: refused, "
              "but not as no match")
    print("     (this machine: %d serial device(s) in %s)"
          % (len(found), SD.BY_ID))


TESTS = [test_config, test_one_match, test_none_refuses, test_several_refuse,
         test_interface, test_exact, test_converter, test_linux_only,
         test_real_linux]


# ---- mutants ------------------------------------------------------------

with open(SRC, encoding="utf-8") as _f:
    ORIGINAL = _f.read()


def _load(text):
    exec(compile(text, SRC, "exec"), SD.__dict__)


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
        ("a tty name as a string",
         _edit("    if not isinstance(dev, dict):\n        raise",
               "    if isinstance(dev, str):\n        return dev\n"
               "    if not isinstance(dev, dict):\n        raise")),
        ("unknown keys allowed",
         _edit("    if extra:\n", "    if False:\n")),
        ("any vid or pid",
         _edit("HEX4 = re.compile(r\"[0-9A-Fa-f]{4}\\Z\")",
               "HEX4 = re.compile(r\".*\")")),
        ("a serial with spaces",
         _edit("SERIAL_OK = re.compile(r\"[\\x21-\\x7e]+\\Z\")",
               "SERIAL_OK = re.compile(r\".+\")")),
        ("the interface not checked",
         _edit("    if i is not None and (isinstance(i, bool)",
               "    if False and (isinstance(i, bool)")),
    ],
    "test_one_match": [
        ("the tty, not the by-id link",
         _edit("        e = dict(port=link, tty=tty,",
               "        e = dict(port=\"/dev/\" + tty, tty=tty,")),
        ("vid kept as written",
         _edit("        out[k] = v.lower()", "        out[k] = v")),
        ("the IDs not returned",
         _edit("    return {k: h[k] for k in (\"port\", \"vid\", \"pid\", \"serial\", \"interface\",",
               "    return {k: h[k] for k in (\"port\",")),
    ],
    "test_none_refuses": [
        ("no match: the first device",
         _edit("    if not hits:\n        raise",
               "    if not hits and found:\n        hits = found[:1]\n"
               "    if not hits:\n        raise")),
        ("the devices found not named",
         _edit("    seen = (\"; found: \" + \"; \".join(describe(e) for e in found)) if found \\",
               "    seen = \"\" if found \\")),
        ("a missing folder refuses as unreadable",
         _edit("    except FileNotFoundError:\n        return []\n    except OSError",
               "    except FileNotFoundError:\n        raise Refused(\"cannot\")\n    except OSError")),
    ],
    "test_several_refuse": [
        ("several: the first",
         _edit("    if len(hits) > 1:\n", "    if False:\n")),
    ],
    "test_interface": [
        ("a composite device needs no interface",
         _edit("    if want[\"interface\"] is None and (h[\"interfaces\"] or 1) > 1:",
               "    if False:")),
        ("the interface not matched",
         _edit("            and (want[\"interface\"] is None\n",
               "            and (True\n")),
        ("the interface number read as decimal",
         _edit("                intf = int(n, 16)", "                intf = int(n)")),
    ],
    "test_exact": [
        ("a serial prefix matches",
         _edit("            and e[\"serial\"] == want[\"serial\"]",
               "            and (e[\"serial\"] or \"\").startswith(want[\"serial\"][:-1])")),
        ("the pid not compared",
         _edit("e[\"pid\"] == want[\"pid\"]", "True")),
    ],
    "test_converter": [
        ("the interface read only beside the tty",
         _edit("        if intf is None:\n            n = _read",
               "        if intf is None and p == os.path.realpath(devdir):\n            n = _read")),
        ("a non-USB port left out",
         _edit("        out.append(e)\n", "        if usb is not None:\n            out.append(e)\n")),
    ],
    "test_linux_only": [
        ("any environment",
         _edit("RULES = {\"linux_only\": True}", "RULES = {\"linux_only\": False}")),
    ],
    "test_real_linux": [
        ("the listing empty",
         _edit("        names = sorted(os.listdir(by_id))",
               "        names = sorted(os.listdir(by_id))[:0]"), "linux"),
        ("the wrong folder",
         _edit("BY_ID = \"/dev/serial/by-id\"", "BY_ID = \"/dev/serial\""),
         "linux"),
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
    if can_fail:
        print("-- control: every test, unmutated", flush=True)
        control_failed = []
        for t in tests:
            if getattr(t, "env", ENV) != ENV:
                continue
            outcome = _run(t)
            print("%s  %-22s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""), flush=True)
            if outcome is not None:
                control_failed.append(t.__name__)
        if control_failed:
            print("\nREFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing; no mutant is counted" % ", ".join(control_failed))
            return 1
        print("-- mutants", flush=True)
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
    print("\n%d of %d %s; %d NOT RUN (other env)%s"
          % (total - bad, total, "mutants caught" if can_fail else "passed",
             other, "; the control passes" if can_fail else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
