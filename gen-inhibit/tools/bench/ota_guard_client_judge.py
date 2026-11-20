#!/usr/bin/env python3
"""B21's OTA refusal judged at the client (spec 9.3, user 2026-11-19, f7865461): per refused request,
the whole 403 with its reason reached the client, and nothing was touched. The connection's end is
recorded, not judged.

Gen-inhibit tester, topic (E), 2026-11-19, on the gen-inhibit reviewer's readings (s)-(w) (answer to
b07f7984): the pcap join and the probe's capture leave B21. Input: ota_guard_raw_probe.py's record
(runs/ota_guard_raw_probe_<body>_<stamp>.json), whose rows keep the reply as the client holds it
(status line, reply_complete, content_length, reply_body) and a page read before and after each
request (page_before, page_after: mode, uptime_ms, elf_sha256, with the host's send and answer
times), and the run's first page (page_first).

Per rep (one non-OFF mode set, one refused upload):
  - FAIL when the reply is not a whole 403 with a reason: another status or none, fewer bytes than
    its Content-Length (or no Content-Length), or an empty body. The reason's text is not judged.
  - FAIL when something was touched: the mode after is not the mode set; the image after
    (elf_sha256) is not the run's first; the uptime is not continuous (UPTIME_PPM, UPTIME_SLACK_S:
    the device's uptime from the read before to the read after is outside the host's interval
    between them, from the before-read's answer to the after-read's send at least, and from the
    before-read's send to the after-read's answer at most). The image check cannot see a write to
    the other slot without a reboot (reading (v)); the uptime sees an install's reboot.
  - VOID when a reading the harness never sent is missing (reading (t)): the mode set was not read
    back as set, so no request was sent; a record with no page read before or after the request,
    or no first page. Where the mode setter replied 200 and the page shows another mode, the rep is
    VOID and that is also printed as a spec 3 finding (the gen-inhibit reviewer, answer to
    8e2ed20c, 2026-11-19).
  - ERROR when a read was sent and got no answer, or an answer without the uptime or the image: the
    record can't tell the bench from the device (reading (t)).
  - The ending (fin, rst, "open: client closed", timeout) is printed beside it, never judged (u).
  - A record from before the reply fields were kept has them from raw_head where raw_head holds the
    whole reply (where it holds only part, ERROR). Such a record has no page read around each
    request, so its reps are VOID at best (reading (w): the e3d9437 baseline's, every 403 whole
    with its reason).
The run (arm_rulings.ruled_verdict's order): FAIL if any rep FAILs, then ERROR, then VOID; no rep
VOID; else PASS.

    python3.13 -B ota_guard_client_judge.py <ota_guard_raw_probe_*.json>
Prints a header line naming the record, one line per rep (a rep with a spec 3 finding is followed
by a line "spec 3 finding (<mode> rep <n>): ..."), and a last line "verdict V: N reps, ..., W with
the whole 403 and its reason (<body> body)", with "; F spec 3 finding(s)" after it when there are
any; exit 0 PASS, 1 otherwise.
"""
import json
import os
import re
import sys

UPTIME_PPM = 1000        # the device's clock against the host's, generously
UPTIME_SLACK_S = 0.02    # uptime_ms is whole ms; the page's own encoding time
RAW_HEAD_KEEP = 300      # ota_guard_raw_probe keeps the reply's first 300 bytes as raw_head
PASS, FAIL, ERROR, VOID = "PASS", "FAIL", "ERROR", "VOID"
RANK = {PASS: 0, VOID: 1, ERROR: 2, FAIL: 3}
NOT_SENT, UNANSWERED, ANSWERED = "not sent", "unanswered", "answered"
MODE_NUMBER = {"OBSERVE": 1, "RESPOND": 2, "INHIBIT": 3, "PASSIVE": 4}   # ota_guard_raw_probe.MODES
SPEC3 = "spec 3 finding"


def with_reply(row):
    """The row with reply_complete, content_length and reply_body; a record from before they were
    kept (2026-11-19) has them from raw_head when raw_head holds the whole reply (bytes fewer than
    it keeps), else None: the record can't tell."""
    if "reply_complete" in row:
        return row
    raw = (row.get("raw_head") or "").encode("latin-1")
    if row.get("bytes") is None or row["bytes"] != len(raw) or len(raw) >= RAW_HEAD_KEEP:
        return None
    head, sep, body = raw.partition(b"\r\n\r\n")
    m = re.search(rb"(?im)^content-length:\s*(\d+)\s*$", head) if sep else None
    cl = int(m.group(1)) if m else None
    return dict(row, content_length=cl, reply_complete=cl is not None and len(body) >= cl,
                reply_body=body.decode("latin-1") if sep else None)


def whole_403(row):
    """True when the row's reply is a whole 403 with a reason."""
    full = with_reply(row)
    return full is not None and full.get("status_line") is not None and not reply_faults(full)


def reply_faults(row):
    """Why the reply is not a whole 403 with a reason; [] when it is."""
    st = row.get("status_line") or ""
    parts = st.split(" ", 2)
    if len(parts) < 2 or parts[1] != "403":
        return ["no 403 reached the client (status %r)" % (st or None)]
    out = []
    if row.get("content_length") is None:
        out.append("the 403 has no Content-Length, so it cannot be known whole")
    elif not row.get("reply_complete"):
        out.append("the 403 cut short (Content-Length %s, body %d bytes in)" % (
            row.get("content_length"), len(row.get("reply_body") or "")))
    if row.get("reply_complete") and not (row.get("reply_body") or "").strip():
        out.append("the 403 has no reason (an empty body)")
    return out


def read_state(p):
    """A page read in the record: not sent (absent), unanswered (an error, or no uptime or image),
    or answered."""
    if p is None:
        return NOT_SENT
    if p.get("error") or p.get("uptime_ms") is None or not p.get("elf_sha256"):
        return UNANSWERED
    return ANSWERED


def uptime_continuous(before, after):
    """(True/False, how): the device's uptime from `before` to `after` against the host's interval."""
    el = (after["uptime_ms"] - before["uptime_ms"]) / 1000.0
    lo = after["epoch_sent"] - before["epoch_answered"]
    hi = after["epoch_answered"] - before["epoch_sent"]
    lo_b = lo * (1 - UPTIME_PPM / 1e6) - UPTIME_SLACK_S
    hi_b = hi * (1 + UPTIME_PPM / 1e6) + UPTIME_SLACK_S
    return lo_b <= el <= hi_b, "uptime +%.3f s against the host's %.3f..%.3f s" % (el, lo, hi)


def judge_rep(row, first):
    """(verdict, [why]) for one rep; `first` the run's first page read (None: not in the record)."""
    before, after = row.get("page_before"), row.get("page_after")
    if row.get("mode_set") is None:     # an older record names the mode only: OBSERVE etc.
        row = dict(row, mode_set=MODE_NUMBER.get(row.get("mode")))
    sb = read_state(before)
    if sb == UNANSWERED and before.get("mode") is None:
        return ERROR, ["the page read after the mode set did not answer (%s): no request sent" % before.get("error")]
    mode_read = before.get("mode") if before is not None else row.get("mode_read")
    if row.get("set_http") != 200 or mode_read != row.get("mode_set"):
        why = ["the mode set was not read back as set (set %s, read %s): no request sent" % (
            row.get("set_http"), mode_read)]
        if row.get("set_http") == 200 and mode_read is not None:
            # the reviewer, answer to 8e2ed20c: VOID for the rep, and printed as a spec 3 finding
            why.append("%s: the mode setter replied 200 and the page shows mode %s, not the mode set %s"
                       % (SPEC3, mode_read, row.get("mode_set")))
        return VOID, why
    full = with_reply(row)
    if full is None:
        return ERROR, ["the record keeps only the reply's first %d of %s bytes: whether it was whole can't "
                       "be told" % (RAW_HEAD_KEEP, row.get("bytes"))]
    bad = reply_faults(full)
    states = {"before": sb, "after": read_state(after), "first": read_state(first)}
    unanswered = [n for n, s in states.items() if s == UNANSWERED]
    not_sent = [n for n, s in states.items() if s == NOT_SENT]
    if unanswered:
        return (FAIL if bad else ERROR), bad + ["the page read %s did not answer in full" % " and ".join(unanswered)]
    if not_sent:
        return (FAIL if bad else VOID), bad + ["no page read %s in the record" % " and ".join(not_sent)]
    if after.get("mode") != row["mode_set"]:
        bad.append("the mode after is %s, not the mode set %s" % (after.get("mode"), row["mode_set"]))
    if after.get("elf_sha256") != first.get("elf_sha256"):
        bad.append("the image after (%s...) is not the run's first (%s...)" % (
            str(after.get("elf_sha256"))[:8], str(first.get("elf_sha256"))[:8]))
    ok, how = uptime_continuous(before, after)
    if not ok:
        bad.append("the uptime is not continuous: " + how)
    return (FAIL if bad else PASS), bad or [how]


def judge(rec):
    """(run verdict, [(row, verdict, why)])."""
    out = [(r,) + judge_rep(r, rec.get("page_first")) for r in rec.get("rows") or []]
    if not out:
        return VOID, out
    return max((v for _r, v, _w in out), key=RANK.get), out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        rec = json.load(fh)
    v, rows = judge(rec)
    print("judged at the client from %s (spec 9.3)" % os.path.basename(argv[0]))
    for r, rv, why in rows:
        r = with_reply(r) or r          # an older record's reply as read from raw_head
        print("%-8s rep %s: %-5s %s | status %s whole %s body %r | ending %s (recorded, not judged)" % (
            r.get("mode"), r.get("rep"), rv, "; ".join(why), r.get("status_line"), r.get("reply_complete"),
            (r.get("reply_body") or "")[:60], r.get("ending")))
        for w in why:
            if w.startswith(SPEC3 + ": "):
                print("%s (%s rep %s): %s" % (SPEC3, r.get("mode"), r.get("rep"), w[len(SPEC3) + 2:]))
    n = {k: sum(1 for _r, x, _w in rows if x == k) for k in (PASS, FAIL, ERROR, VOID)}
    found = sum(1 for _r, _v, why in rows for w in why if w.startswith(SPEC3 + ": "))
    print("verdict %s: %d reps, %d PASS, %d FAIL, %d ERROR, %d VOID, %d with the whole 403 and its reason "
          "(%s body)%s" % (v, len(rows), n[PASS], n[FAIL], n[ERROR], n[VOID],
                           sum(1 for r, _v, _w in rows if whole_403(r)), rec.get("body"),
                           "; %d %s(s)" % (found, SPEC3) if found else ""))
    return 0 if v == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
