#!/usr/bin/env python3
"""The error code of every /gi_ecc isr row in a B6 _ecc session, by pulse category
(reviewer, 2026-10-20, for session 2). Gen-inhibit tester.

    py -3.14 -B tools/gen_inhibit/bench/b6_ecc_codes.py SESSION_DIR

Rows are paired with pulses by b6_session1_desk.arm_table (the jammer's PULSE line, the
witness error frames, the isr rows and their ticks). Each row's raw ecc is decoded here
from the TRM table in gi_ecc.py (type bits 7:6, dir bit 5, segment bits 4:0), and the
page's own type/dir/seg fields must agree with that decode (a disagreement is listed).
Categories: pre-latch and post-latch by the row's ticks row against the device's latch
tick; the landing class (the next frame after the witness error frame is an 0x051
200-320 us later); an over-count (more isr rows than witness error frames for a pulse);
and the undelivered pulses (missing on the witness, the device, or both). Then the rows
with status 32 (0x20), each with its code, and every class pulse with its code. Reads
records only.
"""
import bisect
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b6_session1_desk as D    # noqa: E402
import gi_ecc as GE             # noqa: E402


def decode(ecc):
    """(type, dir name, segment code, segment name) from the raw ecc, by the TRM table."""
    return GE.TYPES[(ecc >> 6) & 3], ("RX" if (ecc >> 5) & 1 else "TX"), ecc & 0x1F, GE.seg_name(ecc & 0x1F)


def code_s(ecc):
    t, d, sg, name = decode(ecc)
    return "0x%02x %s %s seg %d %s" % (ecc, t, d, sg, name)


PATTERN = (24, 17, 23)   # CRC delimiter, active error flag, error delimiter (reviewer, 2026-10-21)
NEAR_US = 470.0          # a data frame this close to the pulse's predicted instant (reviewer, 2026-10-21)


def pattern_misfits(rows):
    """Why an over-count's rows are not the pattern, or [] if they are (reviewer,
    2026-10-21): sorted by seq, every row "other" and RX, segments only 24, 17, 23, each
    present, in that order (one or more of each). The span is reported, not judged."""
    rows = sorted(rows, key=lambda x: x[1])
    why = []
    for row in rows:
        t, dn, sg, name = decode(row[2])
        if t != "other" or dn != "RX":
            why.append("seq %d is %s %s, not other RX" % (row[1], t, dn))
        if sg not in PATTERN:
            why.append("seq %d is seg %d %s" % (row[1], sg, name))
    segs = [r[2] & 0x1F for r in rows if r[2] & 0x1F in PATTERN]
    rank = [PATTERN.index(s) for s in segs]
    if rank != sorted(rank):
        why.append("segments out of order: %s" % " ".join(str(s) for s in segs))
    for s in PATTERN:
        if s not in segs:
            why.append("no %s" % GE.seg_name(s))
    return why


def pattern_s(rows):
    segs = [r[2] & 0x1F for r in sorted(rows, key=lambda x: x[1])]
    return ", ".join("%s x%d" % (GE.seg_name(s), segs.count(s)) for s in PATTERN)


def span_us(rows):
    t = [r[0] for r in rows]
    return max(t) - min(t) if t else None


def near(frames, times, t):
    """(nearest data frame strictly before t, strictly after, at t exactly, whether any
    frame is within NEAR_US); t and the frames on the witness clock. A frame stamped at
    t itself is neither before nor after it (session 3 had one at a witness error frame's
    stamp), so it is counted on its own and is within."""
    if t is None or not frames:
        return None, None, [], None
    b, a = D.prev_frame(frames, times, t), D.next_frame(frames, times, t)
    same = frames[bisect.bisect_left(times, t):bisect.bisect_right(times, t)]
    hit = bool(same) or any(f is not None and abs(f[0] - t) * 1e6 <= NEAR_US for f in (b, a))
    return b, a, same, hit


def same_s(same):
    return "" if not same else "; at the instant itself: %s" % ", ".join("0x%03X %s" % (f[1], f[2]) for f in same)


def bus_lines(r, frames, times):
    out = []
    p = r.get("pred")
    b, a, same, hit = near(frames, times, p)
    if hit is None:
        out.append("bus at the predicted instant: not placed (no jammer fit or no witness frames)")
    else:
        out.append("bus at the predicted instant: nearest data frame before %s, after %s%s; within %.0f us: %s"
                   % (D.fr_s(b, p), D.fr_s(a, p), same_s(same), NEAR_US, "YES" if hit else "no"))
    if r["errs"] and p is not None:
        e = r["errs"][0]
        b, a, same, _ = near(frames, times, e)
        out.append("from the witness error frame (%s from the predicted instant): before %s, after %s%s"
                   % (D.us(e - p), D.fr_s(b, e), D.fr_s(a, e), same_s(same)))
    return out


def row_s(row, t0):
    return "seq %d %+d us %s; tec/rec/status/events %s" % (row[1], row[0] - t0, code_s(row[2]), tuple(row[6:10]))


def ticks_around(arm, fd, r):
    """The page's ticks rows either side of a missed pulse's predicted instant, mapped to
    the device clock by the arm's device-to-witness fit; (before, after) or None."""
    ticks = sorted((arm.get("ecc_page") or {}).get("ticks") or [], key=lambda t: t[0])
    p = r.get("pred")
    if not fd or p is None or not fd[1]:
        return None
    dev_us = (p - fd[0]) / fd[1] * 1e6
    before = [t for t in ticks if t[0] <= dev_us]
    after = [t for t in ticks if t[0] > dev_us]
    return (before[-1] if before else None), (after[0] if after else None)


def tick_s(t):
    return "-" if t is None else "t_us %d, bus_error_count %d, pushed %d" % (t[0], t[1], t[2])


def pulse_report(lead, k, r, rows, arm, fd, frames, times):
    """The lines for one over-count or device miss (reviewer, 2026-10-21)."""
    head = "lead %s arm %d pulse %d [%s]" % (lead, k, r["k"] + 1, ", ".join(categories(r)))
    out = []
    if len(r["isr"]) > max(1, len(r["errs"])):
        why = pattern_misfits(r["isr"])
        out.append("%s%s: over-count, witness %d error frame(s), device %d isr rows"
                   % ("DOES NOT FIT THE PATTERN -- " if why else "", head, len(r["errs"]), len(r["isr"])))
        out.append("  pattern: %s; span %d us" % ("does not fit: " + "; ".join(why) if why else
                                                  "fits (%s)" % pattern_s(r["isr"]), span_us(r["isr"])))
        out += ["  " + x for x in bus_lines(r, frames, times)]
        t0 = min(x[0] for x in r["isr"])
        out += ["    " + row_s(x, t0) for x in sorted(r["isr"], key=lambda x: x[1])]
    else:
        out.append("%s: device miss, witness %d error frame(s), device %d isr rows" % (head, len(r["errs"]), len(r["isr"])))
        out += ["  " + x for x in bus_lines(r, frames, times)]
        for q in (r["k"] - 1, r["k"] + 1):
            nb = next((x for x in rows if x["k"] == q), None)
            if nb is None:
                out.append("  pulse %d: none" % (q + 1))
                continue
            out.append("  pulse %d's rows: %s" % (q + 1, "; ".join(
                "seq %d %s, tec/rec/status/events %s" % (x[1], code_s(x[2]), tuple(x[6:10])) for x in nb["isr"]) or "none"))
        ta = ticks_around(arm, fd, r)
        out.append("  ticks rows either side: %s" % ("not placed (no device fit)" if ta is None else
                                                    "before %s; after %s" % (tick_s(ta[0]), tick_s(ta[1]))))
    return out


def categories(r):
    cats = []
    if not r["errs"]:
        cats.append("undelivered: witness miss")
    if r["phase"]:
        cats.append(r["phase"])
    if r.get("cls"):
        cats.append("landing class")
    if len(r["isr"]) > max(1, len(r["errs"])):
        cats.append("over-count")
    return cats or ["phase unknown"]


def main(session):
    by = collections.defaultdict(collections.Counter)
    allc = collections.Counter()
    mism, s32, cls, undeliv = [], [], [], []
    reports, n_over, n_misfit, n_miss = [], 0, 0, 0
    n_rows = 0
    for p, rec, k, arm, rows, fj, fd, frames, times in D.session_arms(session):
        lead = rec["args"]["lead_s"]
        for r in rows:
            over = len(r["isr"]) > max(1, len(r["errs"]))
            miss = len(r["isr"]) < len(r["errs"])
            if over or miss:
                reports.append(pulse_report(lead, k, r, rows, arm, fd, frames, times))
                n_over += over
                n_miss += miss
                n_misfit += bool(over and pattern_misfits(r["isr"]))
            if not r["isr"]:
                undeliv.append((lead, k, r["k"] + 1, len(r["errs"]), 0))
                continue
            if not r["errs"]:
                undeliv.append((lead, k, r["k"] + 1, 0, len(r["isr"])))
            for row in r["isr"]:
                n_rows += 1
                ecc = row[2]
                t, dn, sg, _name = decode(ecc)
                if (row[3], row[4], row[5]) != (t, 1 if dn == "RX" else 0, sg):
                    mism.append((lead, k, r["k"] + 1, row[1], row[2:6]))
                c = code_s(ecc)
                allc[c] += 1
                for cat in categories(r):
                    by[cat][c] += 1
                tag = (lead, k, r["k"] + 1, ", ".join(categories(r)), c, tuple(row[6:10]))
                if row[8] == 0x20:
                    s32.append(tag)
                if r.get("cls"):
                    cls.append(tag)
    print("isr rows %d; decoded from the raw ecc by the TRM table (gi_ecc.SEG)" % n_rows)
    print("page type/dir/seg disagreeing with the decode: %d" % len(mism))
    for m in mism:
        print("  lead %s arm %d pulse %d seq %d: %s" % m)
    print("\nall rows, by code:")
    for c, n in allc.most_common():
        print("  %4d  %s" % (n, c))
    for cat in sorted(by):
        print("\n%s (%d rows):" % (cat, sum(by[cat].values())))
        for c, n in by[cat].most_common():
            print("  %4d  %s" % (n, c))
    print("\nundelivered pulses (lead, arm, pulse, witness error frames, isr rows): %d" % len(undeliv))
    for u in undeliv:
        print("  lead %s arm %d pulse %d: witness %d, device %d" % u)
    print("\nrows with status 32 (0x20): %d" % len(s32))
    for x in s32:
        print("  lead %s arm %d pulse %d [%s]: %s; tec/rec/status/events %s" % x)
    print("\nlanding-class pulses' rows: %d" % len(cls))
    for x in cls:
        print("  lead %s arm %d pulse %d [%s]: %s; tec/rec/status/events %s" % x)
    print("\nover-counts and device misses, per pulse: over-counts %d (%d do not fit the pattern), device misses %d"
          % (n_over, n_misfit, n_miss))
    print("  (the pattern: sorted by seq, every row other RX, segments %s each present and in that order; "
          "frames within %.0f us of the pulse's predicted instant; reviewer, 2026-10-21)"
          % (" then ".join(GE.seg_name(s) for s in PATTERN), NEAR_US))
    for rep in reports:
        for line in rep:
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
