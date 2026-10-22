#!/usr/bin/env python3
"""Test b6_class_count.py and b6_ecc_session_summary.py on synthetic records.

b6_class_count: the class is "the next frame after the error frame is an 0x051 starting
200-320 us later", both bounds inclusive, split by the device's per-pulse count.
b6_ecc_session_summary: per arm the verdict, groups, the first post-latch pulse, the
over-counts, the isr values and the caveats; totals over the session.

    py -3.14 -B tools/gen_inhibit/bench/test_b6_session_tools.py [--mutants]

Gen-inhibit tester, 2026-10-20.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAILS = []


def check(name, fn, detail=""):
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %s" % (str(detail)[:200],)))
    if not ok:
        FAILS.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(fn, *a):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*a)
    return out.getvalue()


def count(out, key):
    for ln in out.splitlines():
        if ln.strip().startswith(key):
            return int(ln.split()[-1])
    return 0


def class_checks(C):
    E = 100.0
    rows = [
        ("p", 0, 0, 1, E, None, (E + 250e-6, 0x051, "00"), None),     # class single
        ("p", 0, 1, 1, E, None, (E + 200e-6, 0x051, "00"), None),     # class single, low bound
        ("p", 0, 2, 1, E, None, (E + 320e-6, 0x051, "00"), None),     # class single, high bound
        ("p", 0, 3, 1, E, None, (E + 199e-6, 0x051, "00"), None),     # other single
        ("p", 0, 4, 1, E, None, (E + 321e-6, 0x051, "00"), None),     # other single
        ("p", 0, 5, 1, E, None, (E + 250e-6, 0x7F2, "00"), None),     # other single, another id
        ("p", 0, 6, 3, E, None, (E + 250e-6, 0x051, "00"), None),     # class over
        ("p", 0, 7, 0, E, None, (E + 250e-6, 0x7F3, "00"), None),     # other zero
        ("p", 0, 8, 1, E, None, None, None),                          # other single, no next frame
        ("p", 0, 9, None, None, None, None, None),                    # no delta
        ("p", 0, 10, 1, E, None, (E - 250e-6, 0x051, "00"), None),    # before the error: other
    ]
    tmp = tempfile.mkdtemp(prefix="gi-b6-class-")
    try:
        open(os.path.join(tmp, "x2_jam_threshold_20261020T000000.json"), "w").write("{}")
        real = C.L.arm_rows
        C.L.arm_rows = lambda p: rows
        try:
            out = run(C.main, [tmp])
        finally:
            C.L.arm_rows = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("class single: 250 us and both bounds (200, 320 inclusive)", lambda: count(out, "class single") == 3, out)
    check("other single: 199 us, 321 us, another id, no next frame, a frame before the error",
          lambda: count(out, "other single") == 5, out)
    check("a class pulse the device counted 3 times is 'class over', and named",
          lambda: count(out, "class over") == 1 and "pulse 7 delta 3" in out, out)
    check("a zero count is its own row", lambda: count(out, "other zero") == 1, out)
    check("no delta is counted apart", lambda: count(out, "no delta or no error frame") == 1, out)
    check("the next-frame ids are tallied", lambda: "0x051 7" in out and "0x7F2 1" in out, out)


ISR = lambda t, ecc=0, typ="bit", d=0, seg=0: [t, 1, ecc, typ, d, seg, 0, 0, 0, 0]   # noqa: E731


def summary_checks(S):
    arm0 = {"judge": {"verdict": "PASS", "per_pulse": {"groups": 10}}, "witness_error_times": [1.0] * 29,
            "ecc": {"device_groups": 29, "witness_groups": 29, "aligned": True, "overcounts": [15],
                    "caveats": ["every isr row has ecc 0x00 (29 rows)"], "problems": [],
                    "pulses": [{"pulse": 1, "phase": "pre-latch"}, {"pulse": 2, "phase": "pre-latch"},
                               {"pulse": 3, "phase": "post-latch"}, {"pulse": 4, "phase": "post-latch"}]},
            "ecc_page": {"isr_total": 3, "isr_overwritten": 0, "ticks_overwritten": 2,
                         "isr": [ISR(1), ISR(2), ISR(3, ecc=0x12, typ="form", d=1, seg=26)]}}
    arm1 = {"judge": {"verdict": "INCONCLUSIVE", "per_pulse": {"groups": 9}}, "witness_error_times": [1.0] * 28,
            "ecc": {"device_groups": 29, "witness_groups": 28, "aligned": False, "overcounts": [],
                    "caveats": [], "problems": ["/gi_ecc not read: timed out"], "pulses": []},
            "ecc_page": None}
    rec = {"args": {"lead_s": 1.5}, "verdict": "INCONCLUSIVE", "arms": [arm0, arm1]}
    tmp = tempfile.mkdtemp(prefix="gi-b6-summary-")
    try:
        json.dump(rec, open(os.path.join(tmp, "x2_jam_threshold_20261020T000000.json"), "w"))
        # Shaped like a record, so reading it as one shows as a second record, not a crash.
        json.dump({"args": {"lead_s": 9}, "verdict": "X", "arms": [], "frames": []},
                  open(os.path.join(tmp, "x2_jam_threshold_20261020T000000_witness.json"), "w"))
        out = run(S.main, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines = out.splitlines()
    a0 = next((l for l in lines if l.startswith("arm 0:")), "")
    a1 = next((l for l in lines if l.startswith("arm 1:")), "")
    check("one record read, with its lead and verdict", lambda: out.count("== x2_jam_threshold_") == 1
          and "lead 1.5 s, 2 arms, verdict INCONCLUSIVE" in out, out)
    check("arm 0: verdict, groups, witness errors, device/witness groups",
          lambda: "PASS" in a0 and "judged groups 10/10" in a0 and "witness errs 29" in a0
          and "29/29 aligned True" in a0, a0)
    check("arm 0: the first post-latch pulse is the pulse number (3)", lambda: "first post-latch pulse 3 " in a0, a0)
    check("arm 0: over-counts and the ring counters", lambda: "over-counts [15]" in a0 and "isr_total 3" in a0
          and "ticks_overwritten 2" in a0, a0)
    check("arm 0: the isr values are counted per field",
          lambda: "'ecc': [(0, 2), (18, 1)]" in out and "'seg': [(0, 2), (26, 1)]" in out
          and "'type': [('bit', 2), ('form', 1)]" in out and "'dir': [(0, 2), (1, 1)]" in out, out)
    check("caveats and problems are printed", lambda: "caveat: every isr row has ecc 0x00 (29 rows)" in out
          and "PROBLEM: /gi_ecc not read: timed out" in out, out)
    check("arm 1: no latch, not aligned, no page", lambda: "INCONCLUSIVE" in a1 and "first post-latch pulse None" in a1
          and "29/28 aligned False" in a1 and "isr_total None" in a1, a1)
    check("totals: verdicts and over-counts summed",
          lambda: "'PASS': 1" in out and "'INCONCLUSIVE': 1" in out and "'overcounts': 1" in out, out)
    check("the witness file is not read as a record", lambda: "_witness.json" not in out.split("== totals")[0], out)


MUTANTS = [
    ("b6_class_count.py", "low bound exclusive", "LO_US <= round(", "LO_US < round("),
    ("b6_class_count.py", "high bound exclusive", ", 3) <= HI_US", ", 3) < HI_US"),
    ("b6_class_count.py", "any id is the class", "f[1] == CLASS_ID and ", ""),
    ("b6_class_count.py", "the gap's sign ignored", "round((f[0] - e) * 1e6, 3)", "round(abs(f[0] - e) * 1e6, 3)"),
    ("b6_class_count.py", "no rounding", "round((f[0] - e) * 1e6, 3)", "(f[0] - e) * 1e6"),
    ("b6_class_count.py", "an over-count is single", '(" single" if d == 1', '(" single" if d >= 1'),
    ("b6_class_count.py", "class over-counts not named", "            if c and d != 1:\n", "            if False:\n"),
    ("b6_ecc_session_summary.py", "latch read from the wrong field", 'latch = p.get("pulse")', 'latch = p.get("n")'),
    ("b6_ecc_session_summary.py", "over-counts not summed", '    tot["overcounts"] += len(e.get("overcounts") or [])\n'.replace("    ", "            ", 1), ""),
    ("b6_ecc_session_summary.py", "caveats not printed", '                print("        caveat: %s" % c)\n', "                pass\n"),
    ("b6_ecc_session_summary.py", "the witness file read as a record", '"x2_jam_threshold_*[0-9].json"', '"x2_jam_threshold_*.json"'),
    ("b6_ecc_session_summary.py", "the first latch pulse not the first", "                    break\n", ""),
]


def checks_from(folder):
    class_checks(load(os.path.join(folder, "b6_class_count.py"), "cc_under_test"))
    summary_checks(load(os.path.join(folder, "b6_ecc_session_summary.py"), "ss_under_test"))


def main():
    if "--mutants" not in sys.argv[1:]:
        checks_from(HERE)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = errors = 0
    for i, (f, name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        tmp = tempfile.mkdtemp(prefix="gi-b6-tools-m%d-" % i)
        try:
            for g in ("b6_class_count.py", "b6_ecc_session_summary.py", "b6_multicount_landing.py", "gi_ecc.py",
                      "x2_jammer.py"):
                shutil.copy(os.path.join(HERE, g), tmp)
            open(os.path.join(tmp, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--folder", tmp],
                               capture_output=True, text=True, timeout=120)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        real = [l for l in fails if "-- CRASH " not in l]
        dead = bool(real) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (%d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", i, name, len(fails),
                                            "" if len(real) == len(fails) else ", %d crashed" % (len(fails) - len(real)),
                                            ("; " + real[0][6:90]) if real else ""))
    print("%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--folder"]:
        checks_from(sys.argv[2])
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
