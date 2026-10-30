#!/usr/bin/env python3
"""Mutants of wrong_bitrate_errors.py's arming order and pre-burst window;
test_wrong_bitrate_sequence.py must fail on each BY A CHECK. A crash is not a kill, and an
edit that does not apply is an error. The unmutated copy runs first as the control.

    py -3.14 -B gen-inhibit/tools/bench/test_wrong_bitrate_sequence_mutants.py

Each mutant is a copy of wrong_bitrate_errors.py in the worktree's scratch/, one edit
applied, run through WRONG_BITRATE_DIR. About 20 s a mutant. Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_wrong_bitrate_sequence.py")
TARGET = "wrong_bitrate_errors.py"
END_ORDER = ('        if player is not None:\n            player.stop_flag.set()\n            player.join(15)\n'
             '        time.sleep(0.15)\n        try:\n            LF.dev_post("/gen_inhibit_set?mode=0")\n'
             '        except Exception:\n            pass\n')
END_OLD = ('        try:\n            LF.dev_post("/gen_inhibit_set?mode=0")\n        except Exception:\n            pass\n'
           '        if player is not None:\n            player.stop_flag.set()\n            player.join(15)\n')
MUTANTS = [
    (None, None, None),
    ("the slice starts before the arm, as before", "        wit.start()\n",
     "        wit.start()\n        S.Player(pc, stim).start()\n"),
    ("no wait for the driver", "        while time.perf_counter() - t0 < 3.0:\n", "        while False:\n"),
    ("the disarm comes before the slice stops, as before", END_ORDER, END_OLD),
    ("the window counts every frame before the burst", "if t0 <= e < first_burst]", "if e < first_burst]"),
    ("the window counts the burst's own frame", "if t0 <= e < first_burst]", "if t0 <= e <= first_burst]"),
    ("the window starts at any last sample",
     '             if s["t"] < first_burst and s.get("live") is True and not s.get("abort")]',
     '             if s["t"] < first_burst]'),
    ("no clean sample reads as 0 frames",
     '        return {"from_s": None, "errors": None, "error_times_s": []}\n    t0 = clean[-1]',
     '        return {"from_s": None, "errors": 0, "error_times_s": []}\n    t0 = clean[-1]'),
    ("the window is not recorded", '    rec["pre_burst_window"] = (', '    rec["pre_burst_window_unused"] = ('),
    ("the driver flag is always true", 'rec["driver_running_before_slice"] = running',
     'rec["driver_running_before_slice"] = True'),
    # the burst starts at the Kvaser's open (2026-10-29)
    ("the burst starts after the open, as before",
     '    t = time.perf_counter()\n    kv = can.Bus(interface="kvaser", channel=0, bitrate=250000)\n'
     '    opened = time.perf_counter() - t\n',
     '    kv = can.Bus(interface="kvaser", channel=0, bitrate=250000)\n    t = time.perf_counter()\n'
     '    opened = 0.0\n'),
    ("the open's time is not recorded", '"open_s": round(o, 4), ', ''),
]


def main():
    base = gi_paths.scratch("mutants-wrong_bitrate_sequence")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate(MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, WRONG_BITRATE_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
