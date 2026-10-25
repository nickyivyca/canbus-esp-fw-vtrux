#!/usr/bin/env python3
"""Spec 12.4 "a stand-down with an answer pending" on liveflag-mmode-hold
(reviewer, 2026-10-16, on the c6fdab5 batch). Gen-inhibit tester.

The M-mode latch lands ~250 us after a VCM 0x051, while the device's answer to it
is still pending. Across that stand-down the reviewer's check is:
  - sched.standdown +1 (the last full page read wholly before the latch against
    the first read SETTLE_S after it);
  - the skips unchanged: sched.skipped, sched.skipped_withdrawn, sched.skipped_late,
    skips_withdrawn, skips_late (a stand-down is never a skip, user's option C);
  - every diag id (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9) on cadence: none silent more
    than 2.5 s while the mode is not OFF (interlock_judge.diag_gaps);
  - the balance identity
        sched.cls.inhibit.withdrawn + sched.cls.inhibit.dropped
            == sched.skipped + sched.standdown
    on every page outside [latch, latch + SETTLE_S). Inside that window it may read
    one short (spec 5.2 item 6: a held answer is booked standdown when given up and
    withdrawn when the abort loop removes it).
A page with no sched.standdown fails both the +1 and the identity: c65c45e has no
such field, and its pages also go silent after the latch, so it must fail here.

Preconditions (INCONCLUSIVE when not met; they say the run tested the case):
shift_lever_pos 4 on the wire; the device live before it; the first position 4
lands after a VCM 0x051 with no device 0x051 between them (an answer pending);
a full page before the latch and one SETTLE_S after; the after page reads disabled.

Inputs: an interlock_bench run (its _witness.json.gz beside it) and the
full_page_recorder.py recording made beside it.

    py -3.14 tools/gen_inhibit/bench/standdown_pending_judge.py \\
        runs/<...>/interlock_liveflag-mmode-hold_<stamp>.json runs/<...>/fullpage_<...>.jsonl
Run from the reverse-it root. Tested by test_standdown_pending_judge.py.
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interlock_judge as J        # noqa: E402
import full_page_recorder as F     # noqa: E402

SETTLE_S = 1.0
SKIP_FIELDS = ("sched.skipped", "sched.skipped_withdrawn", "sched.skipped_late",
               "skips_withdrawn", "skips_late")
SHOWN = ("mode", "disabled", "disable_reason", "sched.standdown", "sched.skipped",
         "sched.skipped_withdrawn", "sched.skipped_late", "skips_withdrawn", "skips_late",
         "sched.aborts", "sched.cls.inhibit.withdrawn", "sched.cls.inhibit.dropped", "tx_ok")


def residual(page):
    """(withdrawn + dropped) - (skipped + standdown), or None if a term is missing."""
    w = F.field(page, "sched.cls.inhibit.withdrawn")
    d = F.field(page, "sched.cls.inhibit.dropped")
    s = F.field(page, "sched.skipped")
    sd = F.field(page, "sched.standdown")
    if None in (w, d, s, sd):
        return None
    return (w + d) - (s + sd)


def judge(frames, posts, rows, settle=SETTLE_S, identity=None):
    c = J.Ctx(frames, posts, identity=identity)
    v = J.Verdict()
    J.diag_errors(c, v)
    m4 = [t for t, p in c.series(0x639, J.PTX, "shift_lever_pos") if p == 4]
    v.need("shift_lever_pos 4 on the wire", len(m4) >= 3, "%d frames" % len(m4))
    if not m4:
        return v.result()
    tl = m4[0]
    v.facts["latch_t"] = tl
    J.live_before(c, v, tl)
    prior = [t for t in c.vcm_t if t < tl]
    tv = prior[-1] if prior else None
    between = [t for t, _h in c.dev if tv is not None and tv < t < tl]
    v.need("the latch lands with an answer pending (VCM 0x051 before it, no device 0x051 between)",
           tv is not None and not between,
           "0x639 %s us after the VCM 0x051, %d device frame(s) between"
           % (round((tl - tv) * 1e6) if tv is not None else None, len(between)))
    before, after = F.around(rows, tl, settle)
    v.need("a full page before the latch and one %.1f s after it" % settle,
           before is not None and after is not None,
           "before %s, after %s" % (before is not None, after is not None))
    if before is None or after is None:
        return v.result()
    pb, pa = before["page"], after["page"]
    v.facts["before"] = {f: F.field(pb, f) for f in SHOWN}
    v.facts["after"] = {f: F.field(pa, f) for f in SHOWN}
    v.facts["after_read_s"] = round(after["t_req"] - tl, 3)
    v.need("the device stood down (the page after reads disabled)", pa.get("disabled") is True,
           "disabled %s, reason %s" % (pa.get("disabled"), pa.get("disable_reason")))

    sb, sa = F.field(pb, "sched.standdown"), F.field(pa, "sched.standdown")
    v.check("sched.standdown +1 across the latch",
            sb is not None and sa is not None and sa - sb == 1,
            "before %s, after %s" % (sb, sa) if sb is not None and sa is not None
            else "no sched.standdown on the page")

    present = [f for f in SKIP_FIELDS if F.field(pb, f) is not None and F.field(pa, f) is not None]
    changed = {f: (F.field(pb, f), F.field(pa, f)) for f in present if F.field(pb, f) != F.field(pa, f)}
    v.check("the skips unchanged across the latch",
            "sched.skipped" in present and not changed,
            ("changed: %s" % changed) if changed else
            ("compared %s" % present if "sched.skipped" in present else "no sched.skipped on the page"))

    gaps, spans = J.diag_gaps(c)
    v.facts["diag_gap_max_s"] = gaps
    over = {k: g for k, g in gaps.items() if g > J.DIAG_GAP_MAX_S}
    missing = ["0x%03X" % i for i in J.DIAG_IDS if "0x%03X" % i not in gaps]
    v.check("every diag id on cadence while armed (none silent > %.1f s)" % J.DIAG_GAP_MAX_S,
            spans and not over and not missing,
            ("over: %s" % over if over else "") + (" never seen: %s" % missing if missing else "")
            or "largest %s s" % max(gaps.values()))

    out = [r for r in rows if isinstance(r.get("page"), dict)
           and (r["t_resp"] < tl or r["t_req"] >= tl + settle)]
    res = [(r["t_req"], residual(r["page"])) for r in out]
    bad = [(round(t - tl, 3), x) for t, x in res if x != 0]
    v.facts["identity_pages"] = len(res)
    v.check("withdrawn + dropped = skipped + standdown on every page outside the withdrawal",
            res and not bad,
            ("%d of %d pages off (s from the latch, residual; None = a term missing): %s"
             % (len(bad), len(res), bad[:5])) if bad else "%d pages" % len(res))
    return v.result()


def judge_files(run_path, page_path, settle=SETTLE_S):
    d = json.load(open(run_path))
    w = json.load(gzip.open(run_path[:-5] + "_witness.json.gz"))["frames"]
    frames = [(r[0], r[1], r[2]) for r in w]
    rows = [json.loads(line) for line in open(page_path) if line.strip()]
    return judge(frames, d["posts"], rows, settle, identity=d.get("decoder"))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    r = judge_files(*argv)
    print("%s  (%s, %s)" % (r["verdict"], os.path.basename(argv[0]), os.path.basename(argv[1])))
    for x in r["preconditions"]:
        print("    %s: %s -- %s" % ("met    " if x["ok"] else "NOT MET", x["name"], x["detail"]))
    for x in r["checks"]:
        print("    %s: %s -- %s" % ("ok     " if x["ok"] else "FAIL   ", x["name"], x["detail"]))
    for k in ("before", "after", "after_read_s", "identity_pages", "diag_gap_max_s"):
        print("    %s: %s" % (k, r["facts"].get(k)))
    return 0 if r["verdict"] == "AS SPECIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
