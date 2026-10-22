#!/usr/bin/env python3
"""Test b6_ecc_codes.py on synthetic pulse rows (b6_session1_desk.session_arms stubbed).

    py -3.14 -B tools/gen_inhibit/bench/test_b6_ecc_codes.py [--mutants]

Gen-inhibit tester, 2026-10-20.
"""
import contextlib
import importlib.util
import io
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
        ok = bool(fn() if callable(fn) else fn)
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %s" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


TYPES = ("bit", "form", "stuff", "other")


def isr(seq, ecc, tec=0, rec=1, status=44, page_seg=None):
    return [1000 * seq, seq, ecc, TYPES[ecc >> 6], (ecc >> 5) & 1,
            ecc & 0x1F if page_seg is None else page_seg, tec, rec, status, 128]


def pulse(k, errs, rows, phase="pre-latch", cls=False):
    return {"k": k, "errs": errs, "isr": rows, "ticks": [None] * len(rows), "phase": phase, "cls": cls}


ROWS = [
    pulse(0, [1.0], [isr(1, 0x2A)], cls=True),                                   # bit RX data, class
    pulse(1, [2.0], [isr(2, 0xC3, tec=8, rec=0, status=32)]),                    # other TX SOF, status 32
    pulse(2, [], [isr(3, 0x2A)]),                                                 # witness miss
    pulse(3, [4.0], [], phase=None),                                              # device miss
    pulse(4, [5.0], [isr(4, 0x2A), isr(5, 0x99)], phase="post-latch"),            # over-count; 0x99 stuff TX seg 25
    pulse(5, [6.0], [isr(6, 0x2A, page_seg=11)], phase="post-latch"),             # page seg disagrees
]


def at(seq, ecc, t_us):
    r = isr(seq, ecc, rec=1, status=32)
    r[0] = t_us
    return r


# The per-pulse report (reviewer, 2026-10-21). Device clock = witness clock (fit 0 + 1 x).
FIT_ROWS = [at(s, e, 10000000 + d) for s, e, d in
            ((3, 0xF1, 11), (1, 0xF8, 0), (2, 0xF8, 6), (7, 0xF7, 30), (4, 0xF1, 16), (5, 0xF7, 20), (6, 0xF7, 25))]
ROWS2 = [
    dict(pulse(0, [10.0001], FIT_ROWS), pred=10.0),                                         # fits, shuffled by seq
    dict(pulse(1, [11.0], [at(8, 0xF8, 11000000), at(9, 0xF7, 11000005), at(10, 0xF1, 11000010)]), pred=11.0),
    dict(pulse(2, [12.0], [at(11, 0xF8, 12000000), at(12, 0xB1, 12000005), at(13, 0xF7, 12000010)]), pred=12.0),
    dict(pulse(3, [13.0], [], phase=None), pred=12.9997),                                   # device miss
    dict(pulse(4, [14.0], [isr(14, 0xA2)]), pred=14.0),
    dict(pulse(5, [15.0], [at(15 + i, e, 15000000 + 5 * i) for i, e in enumerate((0xF8, 0xF1, 0xF7))]), pred=15.0),
]
FRAMES = [(9.994, 0x051, "aa"), (10.00045, 0x051, "bb"), (11.006, 0x051, "cc"), (12.006, 0x051, "dd"),
          (12.991, 0x051, "ee"), (13.0004, 0x051, "ff"), (14.006, 0x051, "11"),
          (14.99, 0x051, "22"), (15.0, 0x051, "33"), (15.01, 0x051, "44")]
ARM2 = {"ecc_page": {"ticks": [[11002000, 3, 3, 3, 0, 0], [12002000, 4, 3, 4, 0, 0], [14002000, 5, 1, 5, 0, 0]]}}


def run(C, rows=None, arm=None, fd=None, frames=None):
    real = C.D.session_arms
    fr = frames or []
    C.D.session_arms = lambda s: [("x.json", {"args": {"lead_s": 1.5}}, 0, arm or {}, rows or ROWS, None, fd,
                                   fr, [f[0] for f in fr])]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            C.main("unused")
    finally:
        C.D.session_arms = real
    return out.getvalue()


def section(out, head):
    if head not in out:
        return ""
    return out.split(head, 1)[1].split("\n\n", 1)[0]


def checks(C):
    check("decode: 0x2a is bit RX seg 10 data; 0xc3 other TX seg 3 SOF; 0x00 seg 0 undefined",
          C.decode(0x2A) == ("bit", "RX", 10, "data") and C.decode(0xC3) == ("other", "TX", 3, "SOF")
          and C.decode(0x00) == ("bit", "TX", 0, "undefined seg 0"), (C.decode(0x2A), C.decode(0xC3), C.decode(0)))
    check("decode: 0x99 is stuff TX seg 25 ACK slot", C.decode(0x99) == ("stuff", "TX", 25, "ACK slot"), C.decode(0x99))
    try:
        out = run(C)
    except Exception as e:      # noqa: BLE001
        out = "CRASH %s: %s" % (type(e).__name__, e)
    check("rows counted: 6", "isr rows 6;" in out, out[:200])
    check("a page seg that disagrees with the decode is listed",
          "disagreeing with the decode: 1" in out and "pulse 6 seq 6" in out, out[:400])
    check("landing class: the class pulse's code", section(out, "landing class (") .strip().startswith("1 rows):")
          and "0x2a bit RX seg 10 data" in section(out, "landing class ("), section(out, "landing class ("))
    check("over-count: both rows of pulse 5", "over-count (2 rows)" in out and "0x99 stuff TX seg 25 ACK slot"
          in section(out, "over-count ("), section(out, "over-count ("))
    check("witness miss: its row", "undelivered: witness miss (1 rows)" in out)
    check("pre-latch 3 rows, post-latch 3 rows", "pre-latch (3 rows)" in out and "post-latch (3 rows)" in out, out)
    check("undelivered pulses: the witness miss and the device miss",
          "lead 1.5 arm 0 pulse 3: witness 0, device 1" in out and "lead 1.5 arm 0 pulse 4: witness 1, device 0" in out
          and "undelivered pulses (lead, arm, pulse, witness error frames, isr rows): 2" in out, out)
    s32 = section(out, "rows with status 32 (0x20):")
    check("status 32: only pulse 2's row, with its code", s32.strip().startswith("1") and "pulse 2" in s32
          and "0xc3 other TX seg 3 SOF" in s32 and "pulse 1 " not in s32, s32)
    cl = section(out, "landing-class pulses' rows:")
    check("class rows listed with their code", cl.strip().startswith("1") and "pulse 1 [" in cl and "0x2a" in cl, cl)
    report_checks(C)


def report_checks(C):
    """The per-pulse report of over-counts and device misses (reviewer, 2026-10-21)."""
    try:
        out = run(C, ROWS2, ARM2, (0.0, 1.0), FRAMES)
    except Exception as e:      # noqa: BLE001
        out = "CRASH %s: %s" % (type(e).__name__, e)
    rep = out.split("over-counts and device misses, per pulse:", 1)[-1]
    lines = rep.splitlines()
    pl = lambda n: [l for l in lines if (" pulse %d [" % n) in l]
    check("report header: 4 over-counts, 2 do not fit, 1 device miss",
          "over-counts 4 (2 do not fit the pattern), device misses 1" in out, rep[:200])
    check("pulse 6: a frame stamped at the predicted instant itself is named and is within 470 us",
          "before 0x051 22 -10000.0 us, after 0x051 44 +10000.0 us; at the instant itself: 0x051 33; within 470 us: YES"
          in rep, rep[-1200:])
    p1 = pl(1)
    check("pulse 1 (rows out of seq order in the page) fits, with its counts and the span",
          p1 and "DOES NOT FIT" not in p1[0] and
          "pattern: fits (CRC delimiter x2, active error flag x2, error delimiter x3); span 30 us" in rep, (p1, rep[:900]))
    check("pulse 1: a data frame 450 us after the predicted instant is within 470 us",
          "after 0x051 bb +450.0 us; within 470 us: YES" in rep, rep[:900])
    check("pulse 1: the distances from the witness error frame are reported too",
          "from the witness error frame (+100.0 us from the predicted instant): before 0x051 aa -6100.0 us, "
          "after 0x051 bb +350.0 us" in rep, rep[:900])
    p2 = pl(2)
    check("pulse 2 (error delimiter before active error flag): DOES NOT FIT said first, out of order",
          p2 and p2[0].strip().startswith("DOES NOT FIT THE PATTERN -- lead 1.5 arm 0 pulse 2 [")
          and "segments out of order: 24 23 17" in rep, (p2, rep[:1500]))
    p3 = pl(3)
    check("pulse 3 (a stuff row among them): DOES NOT FIT, not other RX",
          p3 and p3[0].strip().startswith("DOES NOT FIT THE PATTERN") and "seq 12 is stuff RX, not other RX" in rep, p3)
    p4 = pl(4)
    check("pulse 4: a device miss, its bus measured from the predicted instant (700 us: no)",
          p4 and "device miss, witness 1 error frame(s), device 0 isr rows" in p4[0]
          and "before 0x051 ee -8700.0 us, after 0x051 ff +700.0 us; within 470 us: no" in rep, (p4, rep[-1500:]))
    check("pulse 4: the codes of the pulses either side",
          "pulse 3's rows: seq 11 0xf8 other RX seg 24 CRC delimiter" in rep
          and "pulse 5's rows: seq 14 0xa2 stuff RX seg 2 ID28-21" in rep, rep[-1500:])
    check("pulse 4: the ticks rows either side, with bus_error_count and pushed",
          "ticks rows either side: before t_us 12002000, bus_error_count 4, pushed 3; "
          "after t_us 14002000, bus_error_count 5, pushed 1" in rep, rep[-800:])
    check("pattern_misfits: a missing segment is named",
          C.pattern_misfits([at(1, 0xF8, 0), at(2, 0xF7, 5)]) == ["no active error flag"],
          C.pattern_misfits([at(1, 0xF8, 0), at(2, 0xF7, 5)]))
    check("pattern_misfits: a TX row is not other RX",
          any("not other RX" in w for w in C.pattern_misfits([at(1, 0xF8, 0), at(2, 0xD1, 5), at(3, 0xF7, 9)])))


MUTANTS = [
    ("dir from the wrong bit", '("RX" if (ecc >> 5) & 1 else "TX")', '("RX" if (ecc >> 4) & 1 else "TX")'),
    ("type from the wrong bits", "GE.TYPES[(ecc >> 6) & 3]", "GE.TYPES[(ecc >> 5) & 3]"),
    ("segment masked to 4 bits", "ecc & 0x1F, GE.seg_name(ecc & 0x1F)", "ecc & 0x0F, GE.seg_name(ecc & 0x0F)"),
    ("page fields never compared", "                if (row[3], row[4], row[5]) != (t, 1 if dn == \"RX\" else 0, sg):\n",
     "                if False:\n"),
    ("over-count at equal rows", '        cats.append("landing class")\n    if len(r["isr"]) > max(1, len(r["errs"])):',
     '        cats.append("landing class")\n    if len(r["isr"]) >= max(1, len(r["errs"])):'),
    ("the report treats every pulse as a miss",
     '    if len(r["isr"]) > max(1, len(r["errs"])):\n        why = pattern_misfits(r["isr"])',
     '    if False:\n        why = pattern_misfits(r["isr"])'),
    ("status 32 as a bit", "if row[8] == 0x20:", "if row[8] & 0x20:"),
    ("device misses not listed", '                undeliv.append((lead, k, r["k"] + 1, len(r["errs"]), 0))\n', ""),
    ("class rows not listed", "                if r.get(\"cls\"):\n                    cls.append(tag)\n", ""),
    ("the witness miss not a category", '        cats.append("undelivered: witness miss")\n', "        pass\n"),
    ("the pattern ignores type and dir", '        if t != "other" or dn != "RX":\n', "        if False:\n"),
    ("the pattern ignores order", "    if rank != sorted(rank):\n", "    if False:\n"),
    ("the pattern is read in page order", "    rows = sorted(rows, key=lambda x: x[1])\n    why = []\n", "    why = []\n"),
    ("the pattern does not need all three", "        if s not in segs:\n", "        if False:\n"),
    ("the bus from the witness error frame", '    p = r.get("pred")\n    b, a, same, hit = near(frames, times, p)',
     '    p = r["errs"][0] if r["errs"] else r.get("pred")\n    b, a, same, hit = near(frames, times, p)'),
    ("720 us, not 470", "NEAR_US = 470.0 ", "NEAR_US = 720.0 "),
    ("a misfit is not said first", '"DOES NOT FIT THE PATTERN -- " if why else ""', '""'),
    ("device misses not reported", '            miss = len(r["isr"]) < len(r["errs"])\n', "            miss = False\n"),
    ("the ticks row after is the first", "    after = [t for t in ticks if t[0] > dev_us]\n", "    after = ticks\n"),
    ("only the next pulse's codes", '        for q in (r["k"] - 1, r["k"] + 1):\n', '        for q in (r["k"] + 1,):\n'),
    ("a frame at the instant itself is not within", "    hit = bool(same) or any(", "    hit = any("),
]


def checks_from(folder):
    checks(load(os.path.join(folder, "b6_ecc_codes.py"), "codes_under_test"))


def main():
    if "--mutants" not in sys.argv[1:]:
        checks_from(HERE)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = errors = 0
    f = "b6_ecc_codes.py"
    src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        tmp = tempfile.mkdtemp(prefix="gi-b6-codes-m%d-" % i)
        try:
            for g in (f, "b6_session1_desk.py", "b6_class_count.py", "b6_multicount_landing.py", "gi_ecc.py", "x2_jammer.py"):
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
