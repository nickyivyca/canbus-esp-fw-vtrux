#!/usr/bin/env python3
"""Test b6_multicount_landing.py on a synthetic threshold record whose answers are known.

    py -3.14 -B tools/gen_inhibit/bench/test_b6_multicount_landing.py

Three pulses 1 s apart; the device's bus_error reads +1, +11, +1 over them; frames on
the wire put an 0x051 260 us after pulse 2's error frame and 6.65 ms before it. The
script must find one multi-count pulse (pulse 2, delta 11), with those neighbours, and
two others. LANDING_SCRIPT overrides the script under test (the mutant runner).
Scratch goes to the machine's temp folder. Gen-inhibit tester, 2026-10-20.
"""
import gzip
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("LANDING_SCRIPT") or os.path.join(HERE, "b6_multicount_landing.py")
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


def load():
    sys.path.insert(0, HERE)              # x2_jammer, which the script imports
    spec = importlib.util.spec_from_file_location("landing_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make(root):
    t0 = 1000.0
    errs = [t0 + 10.0, t0 + 11.0, t0 + 12.0]
    # bus_error page samples, every 0.1 s: 0 before pulse 1, 1 after, 12 after pulse 2, 13 after pulse 3
    samples = []
    t = t0 + 9.0
    while t < t0 + 13.5:
        be = 0 if t < errs[0] else (1 if t < errs[1] else (12 if t < errs[2] else 13))
        samples.append([t, {"drv_bus_error": be, "drv_tec": 0, "drv_rec": 0}])
        t += 0.1
    frames = [[t0 + 9.0, 0x051, "080080ff7f03"],
              [errs[0] - 0.004, 0x051, "080080ff7f04"], [errs[0] + 0.005, 0x051, "080080ff7f05"],
              [errs[1] - 0.00665, 0x051, "080080ff7f06"], [errs[1] + 0.00026, 0x051, "080080ff7f07"],
              [errs[2] - 0.003, 0x7F3, "0102030405060708"], [errs[2], 0x051, "080080ff7f07"],
              [errs[2] + 0.004, 0x051, "080080ff7f08"]]   # pulse 3: a frame at its own error stamp (a tie)
    rec = {"args": {"n": 3, "interval_ms": 1000}, "witness_file": "x2_jam_threshold_T_witness.json.gz",
           "arms": [{"witness_error_times": errs, "samples": samples}]}
    with open(os.path.join(root, "x2_jam_threshold_T.json"), "w") as f:
        json.dump(rec, f)
    with gzip.open(os.path.join(root, "x2_jam_threshold_T_witness.json.gz"), "wt") as f:
        json.dump({"clock": "test", "frames": frames, "errs": errs}, f)
    return errs


def main():
    root = os.path.join(tempfile.gettempdir(), "gen-inhibit-landing-test", "t%d" % os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    try:
        M = load()
        errs = make(root)
        rows = [r for r in M.arm_rows(os.path.join(root, "x2_jam_threshold_T.json")) if len(r) > 2]
        deltas = [r[3] for r in rows]
        check("the device's delta per pulse is the judge's", deltas == [1, 11, 1], deltas)
        p2 = rows[1] if len(rows) > 1 else None
        check("pulse 2's error frame is the second cluster's", p2 is not None and abs(p2[4] - errs[1]) < 1e-9, p2)
        if p2:
            b, f = p2[5], p2[6]
            check("the frame before is the last one before, 6650 us",
                  b is not None and b[1] == 0x051 and abs((p2[4] - b[0]) * 1e6 - 6650) < 1, b)
            check("the frame after is the first one after, 260 us",
                  f is not None and f[1] == 0x051 and abs((f[0] - p2[4]) * 1e6 - 260) < 1, f)
            check("TEC/REC max around the pulse", p2[7] == (0, 0), p2[7])
        p3 = rows[2] if len(rows) > 2 else None
        check("pulse 3's frame before is the 0x7F3", p3 is not None and p3[5] and p3[5][1] == 0x7F3, p3 and p3[5])
        check("pulse 3's frame after skips the one at its own error stamp: the 0x051 4000 us later (strictly after)",
              p3 is not None and p3[6] is not None and p3[6][2] == "080080ff7f08", p3 and p3[6])
        b, a = M.near([[1.0, 1, "a"], [2.0, 2, "b"], [3.0, 3, "c"]], 2.0)
        check("near: a frame at t itself is neither before nor after", (b[2], a[2]) == ("a", "c"), (b, a))
        out = io.StringIO()
        sys.argv = ["b6_multicount_landing.py", root]
        with redirect_stdout(out):
            M.main()
        text = out.getvalue()
        check("one MULTI line, for pulse 2", text.count("MULTI ") == 1 and "pulse 2: delta 11" in text, text)
        check("the summary counts 1 multi-count and 2 others", "1 pulses counted >= 5, 2 others" in text, text)
        os.remove(os.path.join(root, "x2_jam_threshold_T_witness.json.gz"))
        rows = M.arm_rows(os.path.join(root, "x2_jam_threshold_T.json"))
        check("a record without its witness file is reported, not placed",
              len(rows) == 1 and rows[0][0] == "no witness file", rows)
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
