#!/usr/bin/env python3
"""Tests for gi_serial.py, and the scan that no harness tool names a serial port by a COM or
tty name (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12, re-pin batch item 4).

    PYTHONPATH=<core 2026.11.12>/shared/bench python3.13 -B test_gi_serial.py
    ... test_gi_serial.py --can-fail

gi_serial: on a made-up bench of serial devices (_fake_serial.py, the core's own fixture), a
board's IDs from GI_X2_DEVICE / GI_WICAN_DEVICE resolve to its by-id link and what a record
keeps (S3); unset, not JSON, a tty name, an unknown board, IDs matching no device or several,
and any system other than Linux refuse (S2; Linux only, reviewer 2026-11-12); present()
tells plugged from unplugged and refuses several; env_from_settings maps
settings.esptool.device and settings.wican.usb_device; the command line prints the port on
stdout and a refusal on stderr with exit 2.

The scan reads every harness Python file and shell runner under gen-inhibit/tools/ (tests,
fakes and mutant lists excepted, which name tty strings to test with) and fails on:
  - a string that is a whole COM or tty name ("COM3", "/dev/ttyACM0") in Python code;
  - a COM or tty name in a shell runner's commands (comments excepted);
  - a file that opens a serial port (open_console, serial.Serial, serial_for_url, an slcan
    can.Bus, a ConsoleCapture or jammer Console made, or esptool given --port) without going
    through gi_serial, unless it is one of PORT_FROM_CALLER, which take a port their caller
    resolved, each with its reason.
Its can-fail: the same scan on a made-up tree with one planted offender of each kind must
fail on each, and pass on the tree without them.

--can-fail also runs this test against mutants of gi_serial.py (each a copy with one edit,
first on the path through GI_SERIAL_DIR): the unmutated copy first, as the control, then each
mutant, which must make a check FAIL. A crash is not a kill. Gen-inhibit tester, 2026-11-12.
"""
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if os.environ.get("GI_SERIAL_DIR"):
    sys.path.insert(0, os.environ["GI_SERIAL_DIR"])
sys.path.insert(1 if os.environ.get("GI_SERIAL_DIR") else 0, HERE)
import gi_serial as GS          # noqa: E402
import _fake_serial as FS       # noqa: E402
import gi_paths                 # noqa: E402  scratch/ folders

fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def refused(fn):
    try:
        fn()
    except GS.Refused as e:
        return str(e)
    except Exception as e:      # noqa: BLE001  any other exception is not a refusal
        return "NOT A REFUSAL: %r" % (e,)
    return None


# ------------------------------------------------------------------ gi_serial

def unit_checks(work):
    env, ports = FS.fake_bench(os.path.join(work, "bench"))
    got = None
    try:
        got = GS.resolve("x2", env)
    except Exception as e:      # noqa: BLE001
        got = e
    check("resolve: the X2's IDs give its by-id link", isinstance(got, dict) and got.get("port") == ports["x2"], got)
    check("resolve: what a record keeps: the IDs matched, the board and its variable (S3)",
          isinstance(got, dict) and got.get("serial") == FS.X2_SERIAL and got.get("interface") == 0
          and got.get("vid") == "303a" and got.get("pid") == "1001" and got.get("board") == "x2"
          and got.get("env") == "GI_X2_DEVICE" and got.get("tty") == "ttyACM1", got)
    check("resolve: a lookup on the made-up tree says so in what it returns",
          isinstance(got, dict) and "made up (test)" in str(got.get("lookup")), got)
    w = None
    try:
        w = GS.port("wican", env)
    except Exception as e:      # noqa: BLE001
        w = e
    check("port: the WiCAN's IDs give its by-id link, not the X2's", w == ports["wican"], w)

    def without(k):
        return {x: v for x, v in env.items() if x != k}
    for label, e2, want in (
            ("GI_X2_DEVICE unset", without("GI_X2_DEVICE"), "GI_X2_DEVICE is not set"),
            ("GI_X2_DEVICE empty", dict(env, GI_X2_DEVICE=""), "GI_X2_DEVICE is not set"),
            ("a tty name", dict(env, GI_X2_DEVICE="/dev/ttyACM1"), "not JSON"),
            ("a COM name, quoted as JSON", dict(env, GI_X2_DEVICE='"COM3"'), "is not an object"),
            ("IDs with an unknown key", dict(env, GI_X2_DEVICE=json.dumps(dict(FS.device(FS.X2_SERIAL), port="x"))),
             "unknown keys"),
            ("IDs that match no device", dict(env, GI_X2_DEVICE=json.dumps(FS.device("NOSUCHSERIAL"))),
             "no device matches")):
        r = refused(lambda: GS.resolve("x2", e2))
        check("resolve: %s refuses, saying why" % label, r is not None and want in r and "NOT A" not in r, r)
    r = refused(lambda: GS.resolve("jammer", env))
    check("resolve: an unknown board refuses", r is not None and "no board" in r, r)
    # a composite device named with no interface: the core refuses it, and so does gi_serial
    nointf = {k: v for k, v in FS.device(FS.X2_SERIAL).items() if k != "interface"}
    r = refused(lambda: GS.resolve("x2", dict(env, GI_X2_DEVICE=json.dumps(nointf))))
    check("resolve: a composite device named with no interface refuses", r is not None and "interface" in r, r)
    # two devices with the X2's IDs: never a guess (S2)
    env2, _ = FS.fake_bench(os.path.join(work, "twice"), wican=False)
    fx = FS.fixture()(os.path.join(work, "twice"))
    fx.add("ttyACM7", *FS.ESP, serial=FS.X2_SERIAL, interface=0, interfaces=3, usb="1-9")
    r = refused(lambda: GS.resolve("x2", env2))
    check("resolve: two devices matching refuses, naming what was found", r is not None and "2 devices match" in r, r)
    r = refused(lambda: GS.present("x2", env2))
    check("present: two devices matching refuses", r is not None and "2 devices match" in r, r)
    # Linux only (reviewer, 2026-11-12): any other system refuses, naming S1
    SD = GS.serial_dev()
    saved = SD.BL.this_env
    SD.BL.this_env = lambda: "windows"
    try:
        r1 = refused(lambda: GS.resolve("x2", env))
        r2 = refused(lambda: GS.present("x2", env))
    finally:
        SD.BL.this_env = saved
    check("resolve: on Windows it refuses (Linux only), naming S1",
          r1 is not None and "Linux only" in r1 and "S1" in r1, r1)
    check("present: on Windows it refuses (Linux only), naming S1",
          r2 is not None and "Linux only" in r2 and "S1" in r2, r2)
    # present: plugged and unplugged
    envu, _ = FS.fake_bench(os.path.join(work, "unplugged"), wican=False)
    p1 = p2 = None
    try:
        p1, p2 = GS.present("x2", env), GS.present("wican", envu)
    except Exception as e:      # noqa: BLE001
        p1 = e
    check("present: plugged in is True, unplugged is False", p1 is True and p2 is False, (p1, p2))
    # present() matches on the IDs as the core checks them: vid and pid in any case, and a
    # COM name refused, not matched
    upper = dict(FS.device(FS.X2_SERIAL), vid="303A")
    p3 = None
    try:
        p3 = GS.present("x2", dict(env, GI_X2_DEVICE=json.dumps(upper)))
    except Exception as e:      # noqa: BLE001
        p3 = e
    check("present: vid in upper case matches, as the core takes it", p3 is True, p3)
    r = refused(lambda: GS.present("x2", dict(env, GI_X2_DEVICE='"COM3"')))
    check("present: a COM name refuses", r is not None and "is not an object" in r, r)
    # env_from_settings
    s = {"esptool": {"argv": ["p", "-m", "esptool"], "device": FS.device(FS.X2_SERIAL)},
         "wican": {"usb_device": FS.device(FS.WICAN_SERIAL)}}
    e3 = GS.env_from_settings(s)
    check("env_from_settings: esptool.device to GI_X2_DEVICE, wican.usb_device to GI_WICAN_DEVICE",
          json.loads(e3.get("GI_X2_DEVICE", "null")) == FS.device(FS.X2_SERIAL)
          and json.loads(e3.get("GI_WICAN_DEVICE", "null")) == FS.device(FS.WICAN_SERIAL), e3)
    check("env_from_settings: a board with no IDs is left out",
          GS.env_from_settings({"esptool": {"argv": ["p"]}}) == {}, GS.env_from_settings({"esptool": {"argv": ["p"]}}))
    # the command line
    src_dir = os.environ.get("GI_SERIAL_DIR") or HERE
    cli = [sys.executable, "-B", os.path.join(src_dir, "gi_serial.py")]
    p = subprocess.run(cli + ["port", "x2"], capture_output=True, text=True, env=dict(os.environ, **env), timeout=60)
    check("command line: port x2 prints the by-id link alone on stdout, exit 0",
          p.returncode == 0 and p.stdout.strip() == ports["x2"], (p.returncode, p.stdout, p.stderr))
    p = subprocess.run(cli + ["port", "x2"], capture_output=True, text=True,
                       env=dict(os.environ, **without("GI_X2_DEVICE")), timeout=60)
    check("command line: a refusal is on stderr, stdout empty, exit 2",
          p.returncode == 2 and p.stdout.strip() == "" and "REFUSED" in p.stderr, (p.returncode, p.stdout, p.stderr))


# ------------------------------------------------------------------ the scan

TTY = re.compile(r"(COM\d+|/dev/tty[A-Za-z]+\d*)\Z")
# a port name standing as a word of a command (an argument, or an assignment's whole value),
# not inside prose such as a --power description "USB unplugged (no COM4)"
TTY_SH = re.compile(r"(^|[\s=\"'])(COM\d+|/dev/tty[A-Za-z]+\d*)([\s\"';]|$)")
OPENS = re.compile(r"\bopen_console\(|\bserial\.Serial\(|serial_for_url\(|interface=\"slcan\"|"
                   r"\bConsoleCapture\(|\bXJ\.Console\(|\bConsole\(|[\[,]\s*\"--port\",")
# ("--port" as an item of an argv list: esptool's, slcan's; add_argument("--port", ...) of a
# TCP port, as in bench_vehicle.py, is not a serial open)
# Files that take a port their caller resolved through gi_serial, with why.
PORT_FROM_CALLER = {
    "serial_noreset.py": "the open itself (open_console); every caller passes a resolved port",
    "x2_hold_inputs.py": "run only by x2_hold.py with the port it resolved (--port)",
    "e4_device_calibration.py": "--flood-slcan resolves the X2 through gi_serial in main(); run_arm takes that port",
    "gi_serial.py": "the lookup itself",
}
SKIP = re.compile(r"^(test_.*|_fake_.*|.*_mutants)\.py$")


def scan(root):
    """[(file, what)] of every offence under root (a gen-inhibit/tools folder)."""
    out = []
    for d in (root, os.path.join(root, "bench")):
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if n.endswith(".sh"):
                for k, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                    code = line.split("#", 1)[0]
                    if TTY_SH.search(code):
                        out.append((rel, "line %d names a COM or tty port: %s" % (k, code.strip()[:80])))
                continue
            if not n.endswith(".py") or SKIP.match(n):
                continue
            src = open(p, encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                out.append((rel, "does not parse: %s" % e))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and TTY.match(node.value):
                    out.append((rel, "line %d: the string %r is a port name" % (node.lineno, node.value)))
            if OPENS.search(src) and "gi_serial" not in src and n not in PORT_FROM_CALLER:
                out.append((rel, "opens a serial port without gi_serial"))
    return out


PLANTED = {
    "bench/planted_default.py": 'import argparse\nap = argparse.ArgumentParser()\nap.add_argument("--port", default="COM3")\n',
    "bench/planted_tty.py": 'PORT = "/dev/ttyACM0"\n',
    "bench/planted_open.py": 'from serial_noreset import open_console\n\ndef go(p):\n    return open_console(p, 115200, 0.1)\n',
    "bench/planted_runner.sh": '#!/usr/bin/env bash\n$PY x.py --flood-slcan COM3   # fills the bus\n',
    "bench/planted_assign.sh": '#!/usr/bin/env bash\nPORT=/dev/ttyACM0\n$PY x.py --port "$PORT"\n',
}
CLEAN = {
    "bench/clean_tool.py": ('import gi_serial as GS\nfrom serial_noreset import open_console\n\n'
                            'def go():\n    """The X2 (COM3 on the old bench)."""\n'
                            '    return open_console(GS.port("x2"), 115200, 0.1)\n'),
    "bench/clean_runner.sh": ('#!/usr/bin/env bash\n# no COM3 here: the X2 by its IDs\n'
                              'PW="12 V on OBD pin 16, USB unplugged (no COM4)"\n$PY x.py --flood-slcan --power "$PW"\n'),
}


def scan_checks(work):
    found = scan(TOOLS)
    check("scan: no harness tool or runner names a serial port by a COM or tty name, and every one that "
          "opens a port goes through gi_serial", not found, found)
    t = os.path.join(work, "scan_tree")
    os.makedirs(os.path.join(t, "bench"))
    for rel, text in CLEAN.items():
        with open(os.path.join(t, rel), "w", encoding="utf-8") as f:
            f.write(text)
    got = scan(t)
    check("scan can-fail control: a made-up tree with only clean files passes "
          "(prose and comments may name a COM port)", not got, got)
    for rel, text in PLANTED.items():
        p = os.path.join(t, rel)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        got = scan(t)
        os.remove(p)
        check("scan can-fail: the planted %s is caught" % os.path.basename(rel),
              any(f == rel.replace("/", os.sep) for f, _ in got), got)


# ------------------------------------------------------------------ mutants of gi_serial

MUTANTS = [
    ("a tty name is taken as the port", "        dev = json.loads(raw)\n    except ValueError:\n",
     "        dev = json.loads(raw)\n    except ValueError:\n        return {\"port\": raw}\n"),
    ("the core's config check is skipped", "        return SD.config(dev, var)\n", "        return dev\n"),
    ("the test overrides are ignored", "    root, by_id = environ.get(TEST_ROOT) or None, environ.get(TEST_BY_ID) or None\n",
     "    root, by_id = None, None\n"),
    ("a made-up lookup is not said", '        got["lookup"] =', '        got["_lookup"] ='),
    ("the board is not recorded", "    got = dict(got, board=board, env=ENV[board])\n", "    got = dict(got)\n"),
    ("the boards' variables swapped", 'ENV = {"x2": "GI_X2_DEVICE", "wican": "GI_WICAN_DEVICE"}',
     'ENV = {"x2": "GI_WICAN_DEVICE", "wican": "GI_X2_DEVICE"}'),
    ("the settings' keys swapped", 'SETTING = {"x2": ("esptool", "device"), "wican": ("wican", "usb_device")}',
     'SETTING = {"wican": ("esptool", "device"), "x2": ("wican", "usb_device")}'),
    ("present takes the first of several", "    if len(found) > 1:\n", "    if False:\n"),
    ("present is not Linux only", '    if env != "linux":\n        raise Refused("%s: the serial-device',
     '    if False:\n        raise Refused("%s: the serial-device'),
    ("present says plugged for none", "    return len(found) == 1\n", "    return len(found) <= 1\n"),
    ("a refusal on stdout", '        print("REFUSED: %s" % e, file=sys.stderr)\n        return 2\n    print(got["port"]',
     '        print("REFUSED: %s" % e)\n        return 2\n    print(got["port"]'),
    ("the command line prints the record for port",
     '    print(got["port"] if argv[0] == "port" else json.dumps(got, sort_keys=True))',
     '    print(json.dumps(got, sort_keys=True))'),
    ("an unset variable is an empty device", "    if not raw:\n        raise Refused(", "    if raw is None:\n        raise Refused("),
]


def can_fail():
    base = gi_paths.scratch("mutants-gi_serial")
    src = open(os.path.join(HERE, "gi_serial.py"), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, "gi_serial.py"), "w", encoding="utf-8", newline="") as f:
            f.write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, GI_SERIAL_DIR=d), timeout=600)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        if old is None:
            if r.returncode != 0 or fl:
                print("FAILED   m0 %s (exit %s, %d FAIL; %s)" % (name, r.returncode, len(fl), (fl or [r.stderr[-300:]])[0]))
                print("\nthe control FAILS: no mutant is judged")
                return 1
            print("PASS     m0 %s" % name)
            continue
        if fl:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (n, name, len(fl), fl[0][:110]))
        else:
            print("SURVIVED m%d %s  (exit %s%s)" % (n, name, r.returncode,
                                                   "; " + r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    work = gi_paths.scratch("gi_serial")
    unit_checks(work)
    scan_checks(work)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
