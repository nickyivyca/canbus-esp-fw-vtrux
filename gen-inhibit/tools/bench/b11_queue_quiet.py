#!/usr/bin/env python3
"""Quiet data for B11raw's queue bound (spec 3, user 2026-11-19: no frame received before a mode
change left queued for the new mode, the device's receive queue showing no backlog; the gen-inhibit
reviewer's reading (q), answer to b07f7984: drv.msgs_to_rx on the page after each reply within a bound
the tester sets from quiet data on the same replay with no mode change, in the test plan beside
GATE_HELD_S).

Gen-inhibit tester, topic (E), 2026-11-19. Input: e4 records from `baseline_b_noX2.sh B11rawquiet`
(B11raw's replay, scottsvalley_armable_300s.log 0-35 s, its six arms with no mode change, the page polled
at 10 Hz: each arm's `queue_series`, [t, msgs_to_rx, rx_missed, mode, self_off] per answered read).
Prints per arm the answered reads, the reads in the arm's own mode, and msgs_to_rx's median, p99 and
maximum over them, then the same over every arm, and the largest as the bound the data supports. It
records; the bound goes into b11_rawpost_judge.QUEUE_BOUND and the test plan by hand, with the run.
A record with no queue series, or an arm with no read in its mode, is named and counted out, never
read as zero.

    python3.13 -B b11_queue_quiet.py <B11rawquiet_e4_*.json> [...]
Exit 0 when every record gave reads, 1 otherwise.
"""
import json
import os
import sys

MODE = {"observe": 1, "respond": 2, "inhibit": 3, "passive": 4}


def rank(vs, q):
    s = sorted(vs)
    return s[min(len(s) - 1, max(0, int(-(-q * len(s) // 1)) - 1))] if s else None


def arm_reads(rec):
    """(name, mode, [msgs_to_rx in the arm's own mode], answered reads, why or None). Pure."""
    arm = list(rec.get("arms") or {})
    if not arm:
        return None, rec.get("mode"), [], 0, "no arm in the record"
    a = rec["arms"][arm[0]]
    series = a.get("queue_series") or []
    want = MODE.get(rec.get("mode"))
    if a.get("post_schedule"):
        return arm[0], rec.get("mode"), [], len(series), "the arm has a post schedule: not quiet"
    q = [row[1] for row in series if len(row) > 3 and row[3] == want and row[1] is not None]
    why = None if q else ("no queue series" if not series else "no read in the arm's own mode")
    return arm[0], rec.get("mode"), q, len(series), why


def summary(vs):
    return "n %d, median %s, p99 %s, max %s" % (len(vs), rank(vs, 0.5), rank(vs, 0.99), max(vs) if vs else None)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    every, bad = [], 0
    for p in argv:
        with open(p, encoding="utf-8") as fh:
            rec = json.load(fh)
        _arm, mode, q, n, why = arm_reads(rec)
        print("%s (%s): %d answered reads; %s%s" % (os.path.basename(p), mode, n, summary(q),
                                                   "" if why is None else "; COUNTED OUT: " + why))
        if why is None:
            every += q
        else:
            bad += 1
    print("all arms: %s" % summary(every))
    print("bound the data supports (the largest msgs_to_rx read): %s" % (max(every) if every else None))
    return 0 if every and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
