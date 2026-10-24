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
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
TEST = "test_bench_guards_page.py"
OTA, FLASH = "bench_ota_guard.py", "bench_flash_guard.py"
CONTROL = "95aa77a"

IDENT = '''    page, _ = gi_status(host, strict=False)
    if page is None:
        return None
    return {k: page.get(k) for k in ("elf_sha256", "git_version", "uptime_ms")}
'''
ALIVE = "            alive = gi_status(host, strict=False)[0] is not None\n"
FLASH_DEVICE = '           "device": device_identity(args.host),\n'

MUTANTS = [
    ("ota: the version read from /check_status again", OTA, IDENT,
     '    st, txt = http(host, "/check_status")\n    if st != 200:\n        return None\n'
     '    return json.loads(txt).get("git_version")\n'),
    ("ota: /check_status asked for, the page still used", OTA, IDENT, '    http(host, "/check_status")\n' + IDENT),
    ("ota: elf_sha256 left out of the identity", OTA,
     'for k in ("elf_sha256", "git_version", "uptime_ms")', 'for k in ("git_version", "uptime_ms")'),
    ("ota: an empty identity when the page does not answer", OTA,
     "    if page is None:\n        return None\n    return {k:", "    if page is None:\n        return {}\n    return {k:"),
    ("flash: the version read from /check_status again", FLASH, FLASH_DEVICE,
     '           "device": json.loads(http(args.host, "/check_status")[1]).get("git_version"),\n'),
    ("flash: liveness asked of /check_status again", FLASH, ALIVE,
     '            alive = http(host, "/check_status")[0] == 200\n'),
    ("flash: the device always counted alive", FLASH, ALIVE, "            alive = True\n"),
    ("flash: liveness from any answer, a failed one included", FLASH, ALIVE,
     "            alive = gi_status(host, strict=False) is not None\n"),
    # The AP-mode precondition (user, 2026-10-21). The reviewer asked for the
    # absent field by name: it must be caught, not read as AP.
    ("both: an absent wifi_mode read as AP", OTA,
     '    if value is None:\n        return None, "the config has no wifi_mode"\n',
     '    if value is None:\n        value = "AP"\n'),
    ("both: a value not read passes the precondition", OTA,
     '    if value is None:\n        return False, "wifi_mode not read: %s" % err\n',
     '    if value is None:\n        return True, "wifi_mode not read: %s" % err\n'),
    ("both: a config that is not JSON read as AP", OTA,
     '    except ValueError:\n        return None, "the config was not JSON"\n',
     '    except ValueError:\n        return "AP", None\n'),
    ("both: the whole config printed", OTA, "        cfg = json.loads(txt)\n",
     "        print(txt)\n        cfg = json.loads(txt)\n"),
    ("both: the error carries the config's text", OTA,
     '        return None, "GET /load_config returned %s" % st\n',
     '        return None, "GET /load_config returned %s %s" % (st, txt)\n'),
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
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f in (OTA, FLASH):
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(
                src[f].replace(old, new) if f == target else src[f])
        dead, crashed, fails, rc = run_test(d)
        killed += dead
        report("m%d" % n, name, dead, crashed, fails, rc)

    d = os.path.join(base, "control")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f in (OTA, FLASH):
        old = subprocess.run(["git", "-C", HERE, "show", "%s:tools/gen_inhibit/%s" % (CONTROL, f)],
                             capture_output=True, text=True, check=True).stdout
        open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(old)
    c_dead, crashed, fails, rc = run_test(d)
    report("control", "both guards at %s" % CONTROL, c_dead, crashed, fails, rc)
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "fails by a check, as required" if c_dead else "DOES NOT FAIL"))
    return 0 if killed == len(MUTANTS) and not errors and c_dead else 1


if __name__ == "__main__":
    sys.exit(main())
