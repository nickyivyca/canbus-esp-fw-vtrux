#!/usr/bin/env python3
"""Mutants of bench_flash.py's preflight read; test_bench_flash_preflight.py must
fail on each BY A CHECK. A crash is not a kill (a FAIL whose detail starts CRASH
does not count), and an edit that does not apply is an error.

    py -3.14 test_bench_flash_preflight_mutants.py

Each mutant copies bench_flash.py, what it imports and the test into a fresh folder
in the worktree's scratch/, applies one edit, and runs the test there.
Gen-inhibit tester, 2026-10-20.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
SRC = "bench_flash.py"
TEST = "test_bench_flash_preflight.py"
ALSO = ("bench_ota_guard.py", "bench_flash_wire.py")

WAIT_READ = "        page, err = gi_status(host, strict=False)\n"
MAIN_WAIT = "    st, err = wait_for_device(args.host)\n"
REPORTS = '''        % (str(st.get("elf_sha256"))[:16], st.get("uptime_ms")))\n'''

MUTANTS = [
    # The reviewer asked for this one by name: /check_status put back.
    ("/check_status read again while waiting", WAIT_READ,
     '        http(host, "/check_status")\n' + WAIT_READ),
    ("the config line printed again", MAIN_WAIT,
     '    cfg = json.loads(http(args.host, "/check_status")[1])\n'
     '    say(True, "device reports", "git_version=%r fw_version=%r hw=%r"\n'
     '        % (cfg.get("git_version"), cfg.get("fw_version"), cfg.get("hw_version")))\n' + MAIN_WAIT),
    ("the wifi_mode check put back", REPORTS,
     REPORTS + '    need(st.get("wifi_mode") == "AP", "wifi_mode is AP", "wifi_mode=%r" % st.get("wifi_mode"))\n'),
    ("elf_sha256 not required", 'for k in ("elf_sha256", "uptime_ms")', 'for k in ("uptime_ms",)'),
    ("uptime_ms not required", 'for k in ("elf_sha256", "uptime_ms")', 'for k in ("elf_sha256",)'),
    ("a null field counts as present", "if page.get(k) is None]", "if k not in page]"),
    ("the first failed read gives up", '        last = err or "no answer"\n',
     '        return None, err or "no answer"\n'),
    ("the waiting read is strict", WAIT_READ, "        page, err = gi_status(host, strict=True)\n"),
    ("the mode is not checked", 'need(mode == 0, "device is OFF, so OTA is permitted",',
     'need(True, "device is OFF, so OTA is permitted",'),
    ("the report names no image", '''    say(True, "device reports", "elf_sha256 %s... uptime %s ms"''',
     '''    say(True, "device reports", "elf_sha256 %.0s... uptime %s ms"'''),
]


def main():
    base = gi_paths.scratch("mutants-bench_flash_preflight")
    killed = errors = 0
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        for f in (TEST,) + ALSO:
            shutil.copy(os.path.join(HERE, f), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True,
                           text=True, timeout=120)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        real = [l for l in fails if "  CRASH " not in l]
        crashed = "Traceback" in r.stderr or len(real) < len(fails)
        dead = bool(real) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + (real or fails)[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
