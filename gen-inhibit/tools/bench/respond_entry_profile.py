#!/usr/bin/env python3
"""F4: RESPOND's receive loss over time from the mode POST, into live traffic (gen-inhibit
reviewer, 2026-11-26: drv.rx_missed, and ctr_bad, over time from each POST /gen_inhibit_set,
on the current image, entered into live traffic; phase 1 makes one POST per truck wake). A
measurement, not pass or fail.

Gen-inhibit tester, 2026-11-27. Input: e4 records from `baseline_b_hand.sh F4`, each one
RESPOND arm with --arm-after (the POST from the replay's own thread), polled or not. A polled
arm's `queue_series` rows are [send time on the perf clock, msgs_to_rx, rx_missed, mode,
self_off, ctr_bad]; the arm's `perf_base` puts them on the replay's clock, where `post_s[0]`
is the POST's send.

For each record it prints:
  - the baseline: the last answered read sent before the POST. A null rx_missed there (no
    `drv`, the driver not installed) counts from 0, and the line says so;
  - each read after the POST where rx_missed or ctr_bad rose, with its time from the POST;
  - the counts added by each of BY_S seconds after the POST (the last read sent by then), or
    "no read" when no read was sent in that span;
  - the counts on the page read after the arm (`device`), as read.
Then the same "added by" figures over every polled record, as a list per mark.

An unpolled record (no series) gives the page after the arm only. A record that is not a
RESPOND --arm-after arm, is void, lacks perf_base or a POST time while polled, or has no read
before or after the POST, is named and counted out, never read as zero.

    python3.13 -B respond_entry_profile.py <F4_e4_*.json> [...]
Exit 0 when every record gave its figures, 1 otherwise.
"""
import json
import os
import sys

BY_S = (1.0, 5.0, 15.0, 30.0, 60.0)


def drv_end(dev):
    """(rx_missed, ctr_bad) from the page after the arm; None for what is absent."""
    dev = dev or {}
    return (dev.get("drv") or {}).get("rx_missed"), dev.get("ctr_bad")


def profile(rec):
    """The figures of one record, or {"why": reason} when it is counted out. Pure."""
    if rec.get("mode") != "respond":
        return {"why": "mode %r, not respond" % rec.get("mode")}
    arms = list(rec.get("arms") or {})
    if not arms:
        return {"why": "no arm in the record"}
    a = rec["arms"][arms[0]]
    if a.get("arm_after_s") is None:
        return {"why": "no --arm-after POST: not an entry into live traffic"}
    if a.get("void"):
        return {"why": "arm void: %s" % "; ".join(a.get("void_reasons") or [])}
    end = drv_end(a.get("device"))
    out = {"post_http": a.get("post_http"), "poll_hz": a.get("poll_hz"), "end": end}
    series = a.get("queue_series") or []
    if not series:
        out["polled"] = False
        return out
    post = (a.get("post_s") or [None])[0]
    base = a.get("perf_base")
    if post is None or base is None:
        return {"why": "polled, but no %s" % ("POST time" if post is None else "perf_base")}
    rows = []
    for r in series:
        rows.append({"t": round(r[0] - base - post, 4), "rx_missed": r[2], "mode": r[3],
                     "ctr_bad": r[5] if len(r) > 5 else None})
    before = [r for r in rows if r["t"] < 0]
    after = [r for r in rows if r["t"] >= 0]
    if not before or not after:
        return {"why": "no read %s the POST" % ("before" if not before else "after")}
    b = before[-1]
    notes = []
    b_rx = b["rx_missed"]
    if b_rx is None:
        b_rx = 0
        notes.append("rx_missed null before the POST (no drv): counted from 0")
    b_ctr = b["ctr_bad"]
    if b_ctr is None:
        notes.append("ctr_bad absent before the POST: not counted")

    def added(r):
        rx = None if r["rx_missed"] is None else r["rx_missed"] - b_rx
        ctr = None if (r["ctr_bad"] is None or b_ctr is None) else r["ctr_bad"] - b_ctr
        return rx, ctr

    rises, last = [], (0, 0 if b_ctr is not None else None)
    for r in after:
        rx, ctr = added(r)
        if (rx is not None and rx != last[0]) or (ctr is not None and ctr != last[1]):
            rises.append({"t": r["t"], "rx_missed": rx, "ctr_bad": ctr, "mode": r["mode"]})
        last = (rx if rx is not None else last[0], ctr if ctr is not None else last[1])
    by = {}
    for i, s in enumerate(BY_S):
        lo = BY_S[i - 1] if i else 0.0
        span = [r for r in after if lo <= r["t"] < s]
        upto = [r for r in after if r["t"] < s]
        by[s] = added(upto[-1]) if span else None
    out.update(polled=True, reads=len(rows), reads_before=len(before), baseline_t=b["t"],
               baseline=(b["rx_missed"], b["ctr_bad"]), notes=notes, rises=rises, by=by,
               last_t=after[-1]["t"], last=added(after[-1]))
    return out


def fmt(v):
    return "-" if v is None else str(v)


def report(name, p):
    lines = []
    if "why" in p:
        lines.append("%s: COUNTED OUT: %s" % (name, p["why"]))
        return lines
    end = "page after the arm: rx_missed %s, ctr_bad %s (as read)" % tuple(fmt(v) for v in p["end"])
    if not p["polled"]:
        lines.append("%s: RESPOND, POST HTTP %s, unpolled; %s" % (name, fmt(p["post_http"]), end))
        return lines
    lines.append("%s: RESPOND, POST HTTP %s, polled %s Hz, %d reads (%d before the POST)"
                 % (name, fmt(p["post_http"]), fmt(p["poll_hz"]), p["reads"], p["reads_before"]))
    lines.append("  baseline: the read at %+.3f s: rx_missed %s, ctr_bad %s"
                 % (p["baseline_t"], fmt(p["baseline"][0]), fmt(p["baseline"][1])))
    for n in p["notes"]:
        lines.append("  note: " + n)
    for r in p["rises"]:
        lines.append("  rise at %+.3f s: added rx_missed %s, ctr_bad %s (mode %s)"
                     % (r["t"], fmt(r["rx_missed"]), fmt(r["ctr_bad"]), fmt(r["mode"])))
    if not p["rises"]:
        lines.append("  no rise after the POST")
    parts = []
    for s in BY_S:
        v = p["by"][s]
        parts.append("%g s: %s" % (s, "no read" if v is None else "%s/%s" % (fmt(v[0]), fmt(v[1]))))
    lines.append("  added by (rx_missed/ctr_bad): " + "; ".join(parts))
    lines.append("  last read at %+.3f s: added %s/%s; %s"
                 % (p["last_t"], fmt(p["last"][0]), fmt(p["last"][1]), end))
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: respond_entry_profile.py <F4_e4_*.json> [...]")
        return 1
    bad = 0
    every = {s: [] for s in BY_S}
    for path in argv:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            p = profile(rec)
        except (OSError, ValueError) as e:
            p = {"why": "unreadable: %s" % e}
        for line in report(name, p):
            print(line)
        if "why" in p:
            bad += 1
        elif p["polled"]:
            for s in BY_S:
                every[s].append(p["by"][s])
    print("every polled record, added by (rx_missed/ctr_bad):")
    for s in BY_S:
        print("  %g s: %s" % (s, ", ".join("no read" if v is None else "%s/%s" % (fmt(v[0]), fmt(v[1]))
                                           for v in every[s]) or "none"))
    print("%d record(s), %d counted out" % (len(argv), bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
