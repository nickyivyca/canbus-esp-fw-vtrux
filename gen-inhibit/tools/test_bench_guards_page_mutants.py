#!/usr/bin/env python3
"""Mutants of the guard benches' device reads; test_bench_guards_page.py must fail
on each BY A CHECK. A crash is not a kill (a FAIL whose detail starts CRASH does
not count), and an edit that does not apply is an error.

    py -3.14 test_bench_guards_page_mutants.py

Each mutant copies bench_ota_guard.py and bench_flash_guard.py into a fresh folder
in the worktree's scratch/, applies one edit to one of them, and runs the test
against that folder (GUARDS_DIR). A "both" mutant edits bench_ota_guard.py's
wifi_mode() or ap_mode_detail(), which the flash guard imports. The control is
both guards as they were at 95aa77a, before they moved off /check_status and
read wifi_mode from /load_config: the test must fail there too.
Gen-inhibit tester, 2026-10-21.

Port step 4 item 2 (2026-11-25): the HTTP helpers (http, wifi_mode,
ap_mode_detail, gi_status, set_mode, device_identity, runs_dir and the rest)
are the pinned shared/bench's vtrux_bench/run/wican_http.py now, so the
mutants that edited their copies here (device_identity's four, and the five
"both" ones on wifi_mode and ap_mode_detail) have no text left to edit in this
folder. The same rules are shared/bench's tests/test_wican_http.py's, one
mutant per rule, at the pinned tag. In their place, mutants of the wiring: a
helper that is not the core's (device_identity with a /check_status read, the
set_mode form without its offset). Each copy carries bench/gi_core_pin.py, which
bench_ota_guard.py loads by file from there.
"""
import os
import shutil
import subprocess
import sys
# no bytecode anywhere below this process: a child importing the pinned core must not write
# __pycache__ into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
TEST = "test_bench_guards_page.py"
OTA, FLASH = "bench_ota_guard.py", "bench_flash_guard.py"
CONTROL = "95aa77a"

PIN = os.path.join("bench", "gi_core_pin.py")
ALIVE = "            alive = gi_status(host, strict=False)[0] is not None\n"
FLASH_DEVICE = '           "device": device_identity(args.host),\n'
CORE_IDENT = "device_identity = _WH.device_identity\n"
CORE_SET_MODE = "set_mode = _WH.set_mode\n"

MUTANTS = [
    # The wiring onto the pinned core (port step 4 item 2)
    ("ota: device_identity a copy of its own that reads /check_status", OTA, CORE_IDENT,
     "def device_identity(host):\n    http(host, \"/check_status\")\n    return _WH.device_identity(host)\n"),
    ("ota: device_identity a copy of its own, the old text", OTA, CORE_IDENT,
     "def device_identity(host):\n    page, _ = gi_status(host, strict=False)\n    if page is None:\n"
     "        return None\n    return {k: page.get(k) for k in (\"elf_sha256\", \"git_version\", \"uptime_ms\")}\n"),
    ("ota: set_mode without its offset (the flasher's disarm form)", OTA, CORE_SET_MODE,
     "def set_mode(host, mode, offset=500):\n    return _WH.set_mode(host, mode, offset=None)\n"),
    ("ota: set_mode a copy of its own, the same form", OTA, CORE_SET_MODE,
     "def set_mode(host, mode, offset=500):\n    return _WH.set_mode(host, mode, offset=offset)\n"),
    ("flash: the version read from /check_status again", FLASH, FLASH_DEVICE,
     '           "device": json.loads(http(args.host, "/check_status")[1]).get("git_version"),\n'),
    ("flash: liveness asked of /check_status again", FLASH, ALIVE,
     '            alive = http(host, "/check_status")[0] == 200\n'),
    ("flash: the device always counted alive", FLASH, ALIVE, "            alive = True\n"),
    ("flash: liveness from any answer, a failed one included", FLASH, ALIVE,
     "            alive = gi_status(host, strict=False) is not None\n"),
    # The AP-mode precondition (user, 2026-10-21): its reads are the core's
    # wifi_mode and ap_mode_detail now (see the docstring); enforcing it is the guards'.
    ("ota: the precondition not enforced", OTA, "    if not ap_ok:\n        return 2\n",
     "    if False:\n        return 2\n"),
    ("ota: the whole config recorded", OTA, '           "wifi_mode": mode_value,\n',
     '           "wifi_mode": http(args.host, "/load_config")[1],\n'),
    ("flash: the precondition not enforced", FLASH, "    if not ap_ok:\n        return False\n",
     "    if False:\n        return False\n"),
    ("flash: wifi_mode from /gen_inhibit again", FLASH, "    mode_value, err = wifi_mode(host)\n",
     '    mode_value, err = page.get("wifi_mode"), "not on the page"\n'),
    ("flash: the old reading, the page's value or AP when absent", FLASH,
     "    mode_value, err = wifi_mode(host)\n", '    mode_value, err = page.get("wifi_mode") or "AP", None\n'),
    ("flash: the value not recorded", FLASH,
     '    say(ap_ok, "device is in AP mode", detail, wifi_mode=mode_value)\n',
     '    say(ap_ok, "device is in AP mode", detail)\n'),
    # The car-data endpoints are not registered: 404 in every mode (spec 12.4, user 2026-11-16)
    ("flash: car-data, a 403 in modes 1-4 accepted", FLASH,
     'say(code == 404, "%-8s %s not registered (404)"', 'say(code in (403, 404), "%-8s %s not registered (404)"'),
    ("flash: car-data, not probed in modes 1-4", FLASH,
     '        for path in UNREGISTERED:\n            code, txt = http(host, path, method="POST", body=b"")\n',
     '        for path in ():\n            code, txt = http(host, path, method="POST", body=b"")\n'),
    ("flash: car-data, OFF probed with a POST", FLASH,
     '        code, txt = http(host, path, method="GET")\n', '        code, txt = http(host, path, method="POST", body=b"")\n'),
    ("flash: car-data, a 405 accepted when OFF", FLASH,
     'say(code == 404, "OFF      %s not registered (404)"', 'say(code in (404, 405), "OFF      %s not registered (404)"'),
    ("flash: car-data, the OFF probe only with --check-accepted", FLASH,
     "    refusals(args.host)\n    unregistered_when_off(args.host)\n    if args.check_accepted:\n",
     "    refusals(args.host)\n    if args.check_accepted:\n        unregistered_when_off(args.host)\n"),
    ("flash: car-data, no OFF probe", FLASH, "    unregistered_when_off(args.host)\n", ""),
    ("flash: car-data, the PASS line without the 404 sentence", FLASH,
     'each 403 names the reason; %s" % UNREGISTERED_LINE)', 'each 403 names the reason.")'),
]


def run_test(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                       env=dict(os.environ, GUARDS_DIR=d), cwd=d)
    fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
    crashed = "Traceback" in r.stderr
    dead = bool(fails) and not crashed and not all(" CRASH " in f for f in fails)
    return dead, crashed, fails, r.returncode


def report(tag, name, dead, crashed, fails, rc):
    print("%s %s %s  (exit %d, %d FAIL%s)%s" % (
        "KILLED  " if dead else "SURVIVED", tag, name, rc, len(fails), ", CRASHED" if crashed else "",
        "".join("\n           " + f[:140] for f in fails[:4])))


def main():
    base = gi_paths.scratch("mutants-guards_page")
    src = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in (OTA, FLASH)}
    killed = errors = 0
    for n, (name, target, old, new) in enumerate(MUTANTS, 1):
        if src[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src[target].count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(os.path.join(d, "bench"))
        shutil.copyfile(os.path.join(HERE, PIN), os.path.join(d, PIN))
        for f in (OTA, FLASH):
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(
                src[f].replace(old, new) if f == target else src[f])
        dead, crashed, fails, rc = run_test(d)
        killed += dead
        report("m%d" % n, name, dead, crashed, fails, rc)

    # The copy as it is, with its bench/gi_core_pin.py: it must pass, or every kill above
    # could be the copy's fault (port step 4 item 2)
    d = os.path.join(base, "as_is")
    gi_paths.remove_tree(d)
    os.makedirs(os.path.join(d, "bench"))
    shutil.copyfile(os.path.join(HERE, PIN), os.path.join(d, PIN))
    for f in (OTA, FLASH):
        open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(src[f])
    a_dead, crashed, fails, rc = run_test(d)
    as_is_ok = rc == 0 and not fails and not crashed
    print("%s as_is   the unmutated copy  (exit %d, %d FAIL%s)" % (
        "PASS    " if as_is_ok else "FAILED  ", rc, len(fails), ", CRASHED" if crashed else ""))

    d = os.path.join(base, "control")
    gi_paths.remove_tree(d)
    os.makedirs(d)
    for f in (OTA, FLASH):
        old = subprocess.run(["git", "-C", HERE, "show", "%s:tools/gen_inhibit/%s" % (CONTROL, f)],
                             capture_output=True, text=True, check=True).stdout
        open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(old)
    c_dead, crashed, fails, rc = run_test(d)
    report("control", "both guards at %s" % CONTROL, c_dead, crashed, fails, rc)
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the unmutated copy %s; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if as_is_ok else "DOES NOT PASS",
             "fails by a check, as required" if c_dead else "DOES NOT FAIL"))
    return 0 if killed == len(MUTANTS) and not errors and c_dead and as_is_ok else 1


if __name__ == "__main__":
    sys.exit(main())
