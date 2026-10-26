#!/usr/bin/env python3
"""Spec 3 "the worker makes every mode change", 12.4 bench half (reviewer's readings,
2026-10-16). Gen-inhibit tester.

An e4_device_calibration run on a live replay, armed with --arm-after and then
changed by --post-schedule (repeated 3->3 and 3->4->3, POSTs over the raw socket),
with full_page_recorder.py reading the whole page beside it. Per POST:

  reply   HTTP 200 and neither 500 text passes at any latency. A 200 after 1 s
          is in spec (user, 2026-10-16, question (a): a request the worker has
          taken is finished and reported even after 1 s); a 200 is the device's
          word that the worker took it within 1 s, since one not taken is answered
          with the "not taken" 500. Late 200s are listed with their timing (fact
          late_200). Anything else fails, late or not.
  applied the first full page read after the reply shows the requested mode.
  new arm the arm's count restarts: the first page after reads fewer than the last
          page before, by tx_ok out of INHIBIT and would_tx out of PASSIVE (checked
          when the count before is at least MIN_COUNT, else recorded as unchecked).
  balance on the last full page read before the POST (the old arm's end), every
          class: queued + carried_in == sent + dropped + withdrawn + depth + held,
          and the inhibit identity withdrawn + dropped == skipped + standdown.

Over the run:
  identity  on every page: residual (withdrawn + dropped) - (skipped + standdown)
            is 0 outside [sent, reply + WINDOW_S] of each POST; inside it -1 (one
            short) passes only while sched.cls.inhibit.held is 1; +1 (one OVER)
            fails anywhere.
  cadence   no diag id (0x7F1 0x7F2 0x7F3 0x7F8 0x7F9) silent more than 2.5 s while
            the mode is not OFF, and every one seen.
Latencies (reply - sent) are recorded as a distribution.

Preconditions: each POST has a full page before and after it; the bus is live in
the 0.5 s before each POST (the device answering out of INHIBIT, VCM 0x051 on the
wire out of PASSIVE); the schedule has at least one 3->3 and one 3->4->3.

    py -3.14 tools/gen_inhibit/bench/mode_change_judge.py <e4 run .json> <fullpage .jsonl>
Run from the reverse-it root. Tested by test_mode_change_judge.py.
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interlock_judge as J        # noqa: E402
import full_page_recorder as F     # noqa: E402

REPLY_MAX_S = 1.0
WINDOW_S = 1.0
MIN_COUNT = 30
CLASSES = ("inhibit", "probe", "telemetry")
REFUSALS = ("not taken by the gen_inhibit worker", "another mode change was still in progress",
            "not applied",
            "bus is down for a firmware update")    # e3d9437, 8.1 row 12 (a 403)


def class_residuals(page):
    """{class: (queued + carried_in) - (sent + dropped + withdrawn + depth + held)}, None if absent."""
    out = {}
    for c in CLASSES:
        k = F.field(page, "sched.cls.%s" % c)
        if not isinstance(k, dict):
            out[c] = None
            continue
        try:
            out[c] = (k["queued"] + k["carried_in"]) - (k["sent"] + k["dropped"] + k["withdrawn"]
                                                         + k["depth"] + k["held"])
        except KeyError:
            out[c] = None
    return out


def identity(page):
    """(withdrawn + dropped) - (skipped + standdown) for the inhibit class, None if a term is missing."""
    w = F.field(page, "sched.cls.inhibit.withdrawn")
    d = F.field(page, "sched.cls.inhibit.dropped")
    s = F.field(page, "sched.skipped")
    sd = F.field(page, "sched.standdown")
    if None in (w, d, s, sd):
        return None
    return (w + d) - (s + sd)


def merged_diag_gaps(c):
    """interlock_judge.diag_gaps over the armed time as one span: armed_spans splits
    at every POST and restarts the gap there, so a silence across a non-OFF to
    non-OFF change (this case's whole point) would never be seen. Back-to-back
    spans are joined first."""
    merged = []
    for a, b in J.armed_spans(c):
        if merged and abs(merged[-1][1] - a) < 1e-9:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out = {}
    for ident in J.DIAG_IDS:
        ts = c.times(ident)
        if not ts:
            continue
        g = 0.0
        for a, b in merged:
            x = [a] + [t for t in ts if a <= t < b] + [b]
            g = max(g, max(q - p for p, q in zip(x, x[1:])))
        out["0x%03X" % ident] = round(g, 3)
    return out, merged


def reply_class(post):
    """'ok', 'ok (late)', 'ok (untimed)' or 'FAIL: ...' for one POST record."""
    body = (post.get("body") or "")
    if post.get("http") != 200:
        return "FAIL: HTTP %s %s" % (post.get("http"), body[:120])
    if any(r in body for r in REFUSALS):
        return "FAIL: a refusal text in a 200: %s" % body[:120]
    if post.get("untimed"):
        return "ok (untimed)"
    if post["t_ret"] - post["t_sent"] > REPLY_MAX_S:
        return "ok (late)"
    return "ok"


MODE_CODES = {"off": 0, "observe": 1, "respond": 2, "inhibit": 3, "passive": 4}


def post_times(base, post_s, raw):
    """(sent, reply complete) on the epoch clock. With a raw record the reply is its
    t_complete (the whole reply in), never the poster's return, which also waits for
    the connection to end (the device holds it open, 2026-10-16)."""
    if isinstance(raw, dict) and raw.get("t_sent") is not None and raw.get("t_complete") is not None:
        return base + raw["t_sent"], base + raw["t_complete"]
    return base + post_s[0], base + post_s[1]


def posts_from_run(d, arm="idle"):
    """[{mode, t_sent, t_ret, http, body}] on the epoch clock: the --arm-after POST,
    then the --post-schedule ones."""
    a = d["arms"][arm]
    base = a["base_epoch"]
    out = []
    if a.get("post_s"):
        raw = a.get("post_raw")
        ts, tr = post_times(base, a["post_s"], raw)
        # Runs before 2026-10-16 07:0x keep no raw record for the --arm-after POST
        # (e4's arm_post_fields now does); with --post-raw-timeout its post_s also
        # waits for the connection to end, so such a reply cannot be timed.
        out.append({"mode": MODE_CODES[d["mode"]], "t_sent": ts, "t_ret": tr,
                    "http": a.get("post_http"), "body": raw.get("raw") if isinstance(raw, dict) else None,
                    "initial": True,
                    "untimed": a.get("post_raw_timeout_s") is not None and not isinstance(raw, dict)})
    for p in a.get("post_schedule") or []:
        raw = p.get("raw")
        ts, tr = post_times(base, p["post_s"], raw)
        out.append({"mode": p["mode"], "t_sent": ts, "t_ret": tr, "http": p.get("http"),
                    "body": raw.get("raw") if isinstance(raw, dict) else None, "initial": False})
    return out


def judge(frames, posts, rows, decoder=None):
    """frames: witness [(t, id, hex)] (epoch); posts: posts_from_run(); rows: page recording."""
    c = J.Ctx(frames, [{"mode": p["mode"], "t_ret": p["t_ret"]} for p in posts], identity=decoder)
    v = J.Verdict()
    J.diag_errors(c, v)
    pages = [r for r in rows if isinstance(r.get("page"), dict)]
    seq = [p["mode"] for p in posts]
    n33 = sum(1 for a, b in zip(seq, seq[1:]) if a == 3 and b == 3)
    n343 = sum(1 for a, b, e in zip(seq, seq[1:], seq[2:]) if (a, b, e) == (3, 4, 3))
    v.need("the schedule has a 3->3 and a 3->4->3", n33 >= 1 and n343 >= 1, "3->3 x%d, 3->4->3 x%d" % (n33, n343))
    lat, late, per = [], [], []
    prev_mode = None
    for k, p in enumerate(posts):
        before = [r for r in pages if r["t_resp"] < p["t_sent"]]
        after = [r for r in pages if r["t_req"] >= p["t_ret"]]
        pb = before[-1]["page"] if before else None
        pa = after[0]["page"] if after else None
        label = "POST %d (%s->%s)" % (k, prev_mode, p["mode"])
        v.need("%s: a full page before and after" % label, pb is not None and pa is not None)
        if prev_mode == 3:
            live = c.dev_answering(p["t_sent"] - 0.5, p["t_sent"])
            v.need("%s: live before (device answering)" % label, live, "%d device 0x051" % len(live))
        elif prev_mode == 4:
            vcm = [t for t in c.vcm_t if p["t_sent"] - 0.5 <= t < p["t_sent"]]
            v.need("%s: live bus before (VCM 0x051 on the wire)" % label, vcm, "%d VCM 0x051" % len(vcm))
        rc = reply_class(p)
        dt = p["t_ret"] - p["t_sent"]
        if not p.get("untimed"):
            lat.append(dt)
        if rc == "ok (late)":
            late.append((k, round(dt, 3)))
        v.check("%s: reply" % label, rc.startswith("ok"), "%s, %.1f ms" % (rc, dt * 1e3))
        if pa is not None:
            v.check("%s: applied (the page after reads mode %d)" % (label, p["mode"]), pa.get("mode") == p["mode"],
                    "page mode %s" % pa.get("mode"))
        if pb is not None and pa is not None and prev_mode in (3, 4):
            key = "tx_ok" if prev_mode == 3 else "would_tx"
            nb, na = pb.get(key), pa.get(key)
            if isinstance(nb, int) and isinstance(na, int) and nb >= MIN_COUNT:
                v.check("%s: a new arm (%s restarts)" % (label, key), na < nb, "%s %d -> %d" % (key, nb, na))
            else:
                per.append("%s: %s %s -> %s, not checkable" % (label, key, nb, na))
        if pb is not None and not p.get("initial"):
            cr = class_residuals(pb)
            idn = identity(pb)
            v.check("%s: every class balances at the old arm's end" % label,
                    all(x == 0 for x in cr.values()), cr)
            v.check("%s: withdrawn + dropped = skipped + standdown at the old arm's end" % label,
                    idn == 0, "residual %s" % idn)
        prev_mode = p["mode"]

    windows = [(p["t_sent"], p["t_ret"] + WINDOW_S) for p in posts]
    bad = []
    for r in pages:
        x = identity(r["page"])
        inside = any(a <= r["t_req"] <= b or a <= r["t_resp"] <= b for a, b in windows)
        held = F.field(r["page"], "sched.cls.inhibit.held")
        ok = x == 0 or (inside and x == -1 and held == 1)
        if not ok:
            bad.append((round(r["t_req"], 3), x, held, inside))
    v.check("the identity on every page (one short only inside a change window while held is 1; never over)",
            pages and not bad, ("%d of %d pages off (t, residual, held, in window): %s"
                                % (len(bad), len(pages), bad[:5])) if bad else "%d pages" % len(pages))

    gaps, spans = merged_diag_gaps(c)
    over = {k: g for k, g in gaps.items() if g > J.DIAG_GAP_MAX_S}
    missing = ["0x%03X" % i for i in J.DIAG_IDS if "0x%03X" % i not in gaps]
    v.check("every diag id on cadence while armed (none silent > %.1f s)" % J.DIAG_GAP_MAX_S,
            spans and not over and not missing,
            ("over: %s" % over if over else "") + (" never seen: %s" % missing if missing else "")
            or "largest %s s" % max(gaps.values()))
    v.facts["latency_ms"] = {"n": len(lat), "min": round(min(lat) * 1e3, 1) if lat else None,
                             "median": round(statistics.median(lat) * 1e3, 1) if lat else None,
                             "max": round(max(lat) * 1e3, 1) if lat else None,
                             "all": [round(x * 1e3, 1) for x in lat]}
    v.facts["late_200"] = late
    v.facts["unchecked_new_arm"] = per
    v.facts["diag_gap_max_s"] = gaps
    v.facts["sequence"] = seq
    return v.result()


def judge_files(run_path, page_path, arm="idle"):
    sys.path.insert(0, HERE)
    import provenance_classifier as P
    d = json.load(open(run_path, encoding="utf-8"))
    a = d["arms"][arm]
    wit = a.get("witness_raw")
    wpath = os.path.join(os.path.dirname(run_path), os.path.basename(wit)) if wit else \
        run_path[:-5] + "_%s_witness.log.gz" % arm
    frames = [(t, i, b.hex()) for t, i, b in P.read_witness(wpath)]
    rows = [json.loads(line) for line in open(page_path) if line.strip()]
    return judge(frames, posts_from_run(d, arm), rows, decoder=d.get("decoder"))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    r = judge_files(*argv)
    print("%s  (%s, %s)" % (r["verdict"], os.path.basename(argv[0]), os.path.basename(argv[1])))
    for x in r["preconditions"]:
        if not x["ok"]:
            print("    NOT MET: %s -- %s" % (x["name"], x["detail"]))
    print("    %d preconditions met of %d" % (sum(x["ok"] for x in r["preconditions"]), len(r["preconditions"])))
    for x in r["checks"]:
        print("    %s: %s -- %s" % ("ok     " if x["ok"] else "FAIL   ", x["name"], x["detail"]))
    for k in ("sequence", "latency_ms", "late_200", "unchecked_new_arm", "diag_gap_max_s"):
        print("    %s: %s" % (k, r["facts"].get(k)))
    return 0 if r["verdict"] == "AS SPECIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
