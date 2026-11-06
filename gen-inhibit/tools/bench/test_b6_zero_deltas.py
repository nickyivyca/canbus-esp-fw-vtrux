#!/usr/bin/env python3
"""Test b6_zero_deltas.py on synthetic threshold records whose answers are known.

    py -3.14 -B gen-inhibit/tools/bench/test_b6_zero_deltas.py

One record, three arms of three pulses 1 s apart, page polls every 0.1 s:
  arm 0  a stalled poll, sent 0.3 s before pulse 2's error frame and returned 60 ms after
         it with the count from before it: old 0, new 1 (an artifact);
  arm 1  the device does not count pulse 2, the polls either side answered at once:
         old 0, new 0 (stands);
  arm 2  every pulse counted once: no row.
A second record without its witness file is not read; a third, renamed _VOID_noreset after
the run with its witness file still under the old name (arm 2 again), is. ZERO_SCRIPT overrides the script
under test (the mutant runner). Scratch goes to the worktree's scratch/. Gen-inhibit
tester, 2026-11-05.
"""
import gzip
import importlib.util
import io
import json
import os
import sys
import gi_paths  # noqa: E402  scratch/ folders
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("ZERO_SCRIPT") or os.path.join(HERE, "b6_zero_deltas.py")
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
    spec = importlib.util.spec_from_file_location("zero_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


UP0 = 50.0     # the device's uptime at host time 0, in s


def arm(errs, counted):
    """Polls every 0.1 s from 9.0 to 13.4 s, answered in 10 ms; bus_error counts the frames in counted."""
    s, k = [], 0
    while k < 45:
        t = 1000.0 + 9.0 + 0.1 * k + 0.003       # never on an error frame's 50 ms slack edge
        sent = t - 0.01
        be = sum(1 for e, c in zip(errs, counted) if c and e < sent)
        s.append([t, {"drv_bus_error": be, "req_start": sent, "uptime_ms": int(round((UP0 + sent) * 1000))}])
        k += 1
    return s


def make(root):
    errs = [1010.0, 1011.0, 1012.0]
    a0 = arm(errs, [1, 1, 1])
    # the stalled poll: sent 0.3 s before pulse 2's frame, built then, returned 60 ms after the frame
    a0.append([errs[1] + 0.06, {"drv_bus_error": 1, "req_start": errs[1] - 0.3,
                                "uptime_ms": int(round((UP0 + errs[1] - 0.3) * 1000))}])
    a0.sort(key=lambda s: s[0])
    a1 = arm(errs, [1, 0, 1])
    a2 = arm(errs, [1, 1, 1])
    rec = {"args": {"n": 3, "interval_ms": 1000},
           "arms": [{"witness_error_times": errs, "samples": x} for x in (a0, a1, a2)]}
    with open(os.path.join(root, "x2_jam_threshold_A.json"), "w") as f:
        json.dump(rec, f)
    with gzip.open(os.path.join(root, "x2_jam_threshold_A_witness.json.gz"), "wt") as f:
        json.dump({"frames": [], "errs": errs}, f)
    with open(os.path.join(root, "x2_jam_threshold_B.json"), "w") as f:   # no witness file
        json.dump(rec, f)
    # renamed after the run, its witness file still under the old name (c65c45e's VOID records)
    with open(os.path.join(root, "x2_jam_threshold_C_VOID_noreset.json"), "w") as f:
        json.dump({"args": rec["args"], "arms": [rec["arms"][2]]}, f)
    with gzip.open(os.path.join(root, "x2_jam_threshold_C_witness.json.gz"), "wt") as f:
        json.dump({"frames": [], "errs": errs}, f)


def main():
    root = gi_paths.scratch("zero-deltas-test")
    gi_paths.remove_tree(root)
    os.makedirs(root)
    try:
        M = load()
        make(root)
        paths = M.records([root])
        check("the records with a witness file are read, a renamed one too",
              [os.path.basename(p) for p in paths] == ["x2_jam_threshold_A.json",
                                                       "x2_jam_threshold_C_VOID_noreset.json"], paths)
        rows, st = M.arm_rows(os.path.join(root, "x2_jam_threshold_A.json"))
        got = [(k, i, a, b) for k, i, e, a, b, bef, aft, off in rows]
        check("arm 0 pulse 2: new 1, old 0 (the stalled poll)", (0, 2, 1, 0) in got, got)
        check("arm 1 pulse 2: new 0, old 0 (a real miss)", (1, 2, 0, 0) in got, got)
        check("no other rows", len(got) == 2, got)
        check("placed 9, delta 0 new 1, old 2", st == {"placed": 9, "zero_new": 1, "zero_old": 2}, st)
        r1 = [r for r in rows if r[0] == 1]
        if r1:
            k, i, e, a, b, bef, aft, off = r1[0]
            check("the uptime offset is the device's", off is not None and abs(off - UP0) < 0.02, off)
            text = M.reading(aft, e, off)
            check("arm 1's after reading: sent +0.093, built after the frame, still 1",
                  text.startswith("1, ") and "sent +0.093, built +0.103" in text, text)
        # 3 polls answered in 10 ms and 4 in 500 ms (each page built when its request was sent):
        # the offset comes from the quick ones only, so it is the device's within 10 ms
        quick = [(100.0 + j, {"req_start": 99.99 + j, "uptime_ms": int(round((UP0 + 99.99 + j) * 1000))})
                 for j in range(3)]
        slow = [(110.0 + j, {"req_start": 109.5 + j, "uptime_ms": int(round((UP0 + 109.5 + j) * 1000))})
                for j in range(4)]
        off2 = M.uptime_offset(quick + slow)
        check("the uptime offset ignores slowly answered polls", off2 is not None and abs(off2 - UP0) < 0.015, off2)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = M.main([root])
        text = out.getvalue()
        check("an artifact row and a stands row", "artifact x2_jam_threshold_A.json arm 0 pulse 2: new 1, old 0" in text
              and "stands x2_jam_threshold_A.json arm 1 pulse 2: new 0, old 0" in text, text)
        check("the stalled poll is not the after reading under the new rule", "returned +0.060" not in
              text.split("stands")[0].split("after:")[-1], text)
        check("the summary", "placed 12; delta 0: new 1, old 2" in text and rc == 0, text)
        check("no roots is refused", M.main([]) == 2, "")
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        gi_paths.remove_tree(root)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
