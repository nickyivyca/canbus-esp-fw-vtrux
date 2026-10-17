#!/usr/bin/env python3
"""Mutants of bench_flash.py's OTA verdict; test_bench_flash_outcome.py must
fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply
is an error.

    py -3.14 test_bench_flash_outcome_mutants.py

Each mutant copies bench_flash.py and the test into a fresh folder under this
one, applies one edit, and runs the test there with this folder on PYTHONPATH
for bench_ota_guard. Gen-inhibit tester, 2026-10-16.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "bench_flash.py"
TEST = "test_bench_flash_outcome.py"

MUTANTS = [
    ("took without waiting out the gate",
     ' and last["last_uptime_ms"] >= settle_ms:', ":"),
    ("took after any number of restarts",
     'if last["elf_sha256"] == image_sha and len(new) == 1 and',
     'if last["elf_sha256"] == image_sha and'),
    ("same build: one restart counts as a rollback",
     'if last["elf_sha256"] == before_sha and (not same or len(new) >= 2):',
     'if last["elf_sha256"] == before_sha:'),
    ("no crash rollback", 'if last["reset_reason"] in CRASH_REASONS:', "if False:"),
    ("a gate rollback at any boot time",
     'if last["lo"] - t_end >= GATE_MIN_S and', 'if last["lo"] - t_end >= 0 and'),
    ("a gate rollback with any reset reason",
     ' and last["reset_reason"] == "SW":', ":"),
    ("never-ran at any boot time",
     'if len(new) == 1 and last["hi"] - t_end <= NEVER_RAN_MAX_S:', "if len(new) == 1:"),
    ("a1 from any read after the end",
     'if bs[0]["last_t"] - t_end >= A1_CONFIRM_S:', 'if bs[0]["last_t"] - t_end >= 0:'),
    ("no a2 prefix", 'pre = "a2+" if code is None else ""', 'pre = ""'),
    ("curl's 000 read as a status",
     'out["http_code"] = code if code > 0 else None', 'out["http_code"] = code'),
    ("curl's reply body dropped", '            out["body"] = body[:2000]', '            out["body"] = ""'),
    ("boots merged across 5 s", "<= SAME_BOOT_S + (hi - lo)", "<= 5.0 + (hi - lo)"),
    ("boot instant from the answer only",
     'return r["t_req"] - up, r["t_resp"] - up', 'return r["t_resp"] - up, r["t_resp"] - up'),
    ("the watch never stops early",
     "        if decided(classify(code, t_end, before, readings, image_sha, settle_ms)[0]):\n            break",
     "        pass"),
    ("a1 is never decided", 'return verdict.split("+")[-1] in ("a1", "c",',
     'return verdict.split("+")[-1] in ("c",'),
    ("refused only above 408", "if code is not None and code >= 400:", "if code is not None and code > 408:"),
    ("elf_sha256 from the idf_ver field",
     "return head[APP_DESC_OFF + 0x90:APP_DESC_OFF + 0xB0].hex()",
     "return head[APP_DESC_OFF + 0x70:APP_DESC_OFF + 0x90].hex()"),
    # The capture's line is message only (2026-10-17).
    ("the capture moves the watch",
     "    readings = watch(read, code, t_end, before, image_sha, now, sleep)\n",
     "    readings = watch(read, code, t_end + (1.0 if capture is not None else 0.0), before, image_sha, now, sleep)\n"),
    ("a raising capture is not caught",
     '        return False, "%s: %s" % (type(e).__name__, e)', "        raise"),
    ("the capture is never stopped", 'wire["stop"] = _guarded(capture.stop)[1]', 'wire["stop"] = None'),
    ("the line on every upload", 'return up.get("http_code") != 303', "return True"),
    ("no line on a failed upload", 'return up.get("http_code") != 303', "return False"),
    ("a capture that failed to start is still used", "            capture = None\n    t_start = now()",
     "            pass\n    t_start = now()"),
    ("a skipped capture says nothing",
     '            say(None, "wire capture", "skipped, the flash goes on without it: %s" % why)', "            pass"),
    ("a failed summary says nothing", '                print("wire: diagnostic skipped: %s" % res[1])', "                pass"),
    ("the capture starts after the post",
     "        ok, why = _guarded(capture.start)\n",
     "        ok, why = True, \"deferred\"\n        post = (lambda p: lambda: (p(), capture.start())[0])(post)\n"),
]


def main():
    base = os.path.join(HERE, "_mutants_bench_flash")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=d,
                           env=dict(os.environ, PYTHONPATH=HERE), capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
