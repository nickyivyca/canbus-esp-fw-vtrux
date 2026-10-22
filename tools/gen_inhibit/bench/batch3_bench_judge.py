#!/usr/bin/env python3
"""Judges for pre-port batch 3's bench parts (spec 8.1 rows 8 and 11, 9.3; the
reviewer's checks, 2026-10-16). Gen-inhibit tester.

armed   (ota_armed_bench.py's record, its e4 run and page recording) an OTA upload
        while armed in INHIBIT on a live replay:
          - the reply is the 403 with the armed text, not the worker's 500;
          - every page answered from just before the upload to just after it reads
            worker "armed" and mode 3, and the arm is the same one, still going:
            tx_ok after > tx_ok before;
          - elf_sha256 unchanged and uptime continuous (one boot over every page);
          - bus_violations unchanged;
          - the device answers the VCM throughout, from the upload's start to 1 s
            after its end: no gap between answers (or from either end) over
            WINDOW_S; and no diag id is silent > 2.5 s there.
failed  (failed_upload_probe.py's record, and the e4 run of the arm after it):
          - bench_flash verdict a1 for a short body, b for a truncated image; a
            truncated image TAKEN (c, or a2+c) is a finding, reported as such;
          - a page read answered inside the upload must read worker "updating";
            if none was answered there, that is reported as "not observable on
            the bench (reads queued behind the upload)" with the timings, not as
            a check (reviewer, 2026-10-16);
          - every page after the upload's end reads worker "idle" and mode 0;
          - elf_sha256 unchanged, uptime continuous, bus_violations unchanged;
          - the arm after it works: the e4 run's witness has the device answering.

    py -3.14 batch3_bench_judge.py armed <ota_armed_*.json>
    py -3.14 batch3_bench_judge.py failed <failed_upload_*.json> <e4 run .json of the arm after>
Run from the reverse-it root. Tested by test_batch3_bench_judge.py.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interlock_judge as J        # noqa: E402

ARMED_TEXT = "gen_inhibit is armed; POST /gen_inhibit_set?mode=0 first"
WORKER_500 = "gen_inhibit worker did not answer within 1 s"
WINDOW_S = 0.5
SETTLE_S = 1.0
MIN_ANSWERS = 100
# Spec 8.1 row 11 asks for IDLE with nothing taken, not for a particular reply
# (reviewer, 2026-10-16): a1 (no status) or b (4xx/5xx) passes either kind, and the
# reply is recorded as observed. The expected kinds below are predictions only.
NOT_TAKEN = ("a1", "b")
PREDICTED = {"short": "a1", "truncated": "b"}


def pages_of(rows):
    return [r for r in rows if isinstance(r.get("page"), dict)]


def one_boot(rows):
    """(ok, detail): every page's boot instant (t - uptime) within 1 s plus the
    reads' spread of the first, and one elf_sha256 throughout."""
    ps = pages_of(rows)
    if not ps:
        return False, "no pages"
    boots = [(r["t_req"] - r["page"]["uptime_ms"] / 1000.0, r["t_resp"] - r["page"]["uptime_ms"] / 1000.0)
             for r in ps if isinstance(r["page"].get("uptime_ms"), (int, float))]
    if len(boots) != len(ps):
        return False, "a page with no uptime_ms"
    lo, hi = boots[0]
    jumps = [k for k, (a, b) in enumerate(boots) if a > hi + 1.0 or b < lo - 1.0]
    shas = sorted({r["page"].get("elf_sha256") for r in ps})
    return (not jumps and len(shas) == 1 and shas[0] is not None,
            "%d pages, boot instant spread %.2f s, elf_sha256 %s%s"
            % (len(ps), max(b for _, b in boots) - min(a for a, _ in boots), [str(s)[:8] for s in shas],
               ", a new boot at page %d" % jumps[0] if jumps else ""))


def answer_gaps(c, lo, hi):
    """(the gaps over WINDOW_S as (start s from lo, length s), largest gap s) between
    device answers over [lo, hi), the two ends included: a sliding window, so a
    silence cannot hide by straddling two fixed windows."""
    x = [lo] + sorted(c.dev_answering(lo, hi)) + [hi]
    gaps = [(round(p - lo, 3), round(q - p, 3)) for p, q in zip(x, x[1:])]
    return [g for g in gaps if g[1] > WINDOW_S], max(g for _, g in gaps)


def diag_gaps(c, lo, hi):
    out = {}
    for ident in J.DIAG_IDS:
        x = [lo] + [t for t in c.times(ident) if lo <= t < hi] + [hi]
        out["0x%03X" % ident] = round(max(q - p for p, q in zip(x, x[1:])), 3)
    return out


def judge_armed(rec, frames, rows, arm_post):
    """rec: ota_armed_bench's record; frames: the e4 witness [(t, id, hex)];
    rows: the page recording; arm_post: {"mode": 3, "t_ret": epoch} of the arm."""
    v = J.Verdict()
    up = rec.get("upload")
    v.need("an upload was made into a live inhibit", up is not None, rec.get("abandoned", ""))
    if up is None:
        return v.result()
    t0, t1 = up["epoch_start"], up["epoch_end"]
    c = J.Ctx(frames, [arm_post])
    ps = pages_of(rows)
    before = [r for r in ps if r["t_resp"] < t0]
    after = [r for r in ps if r["t_req"] >= t1]
    v.need("a page before and after the upload", before and after)
    v.need("live before the upload (device answering in the 0.5 s before)", c.dev_answering(t0 - 0.5, t0),
           "%d answers" % len(c.dev_answering(t0 - 0.5, t0)))
    if not (before and after):
        return v.result()
    pb, pa = before[-1]["page"], after[0]["page"]
    head = up.get("raw_head") or ""
    v.check("the reply is the 403 with the armed text", (up.get("status_line") or "").startswith("HTTP/1.1 403")
            and ARMED_TEXT in head and WORKER_500 not in head, "%s / %r" % (up.get("status_line"), head[-80:]))
    span = [r for r in ps if before[-1]["t_req"] <= r["t_req"] <= after[0]["t_req"]]
    off = [(round(r["t_req"] - t0, 2), r["page"].get("worker"), r["page"].get("mode")) for r in span
           if r["page"].get("worker") != "armed" or r["page"].get("mode") != 3]
    v.check("every page from before to after the upload reads worker armed, mode 3", not off,
            "%d pages; off: %s" % (len(span), off[:5]))
    v.check("the same arm, still going (tx_ok after > before)",
            isinstance(pb.get("tx_ok"), int) and isinstance(pa.get("tx_ok"), int) and pa["tx_ok"] > pb["tx_ok"],
            "tx_ok %s -> %s" % (pb.get("tx_ok"), pa.get("tx_ok")))
    ok, det = one_boot(ps)
    v.check("elf_sha256 unchanged and uptime continuous over the run", ok, det)
    v.check("bus_violations unchanged", "bus_violations" in pb and pb.get("bus_violations") == pa.get("bus_violations"),
            "%s -> %s" % (pb.get("bus_violations"), pa.get("bus_violations")))
    empty, gap = answer_gaps(c, t0, t1 + SETTLE_S)
    v.check("the device answers throughout, the upload's start to 1 s after its end (no gap > %.1f s)" % WINDOW_S,
            not empty, "largest gap %.3f s; over %.1f s at (+s, s): %s" % (gap, WINDOW_S, empty[:5]))
    dg = diag_gaps(c, t0, t1 + SETTLE_S)
    v.check("no diag id silent > %.1f s across the upload" % J.DIAG_GAP_MAX_S,
            all(g <= J.DIAG_GAP_MAX_S for g in dg.values()), dg)
    v.facts.update({"upload_s": round(t1 - t0, 3), "reply_s": up.get("t_reply"), "answer_gap_max_s": gap,
                    "diag_gap_s": dg})
    return v.result()


def judge_failed(rec, frames_after, arm_post_after):
    """rec: failed_upload_probe's record; frames_after: the witness of the e4 arm run
    after it; arm_post_after: that arm's {"mode", "t_ret"}."""
    v = J.Verdict()
    kind = rec["kind"]
    up = rec["upload"]
    t0, t1 = up["epoch_start"], up["epoch_end"]
    rows = rec["pages"]
    ps = pages_of(rows)
    before = [r for r in ps if r["t_resp"] < t0]
    after = [r for r in ps if r["t_req"] >= t1]
    v.need("a page before the upload and pages after it", before and after)
    v.need("the device was IDLE before (worker idle, mode 0)",
           before and before[-1]["page"].get("worker") == "idle" and before[-1]["page"].get("mode") == 0,
           before[-1]["page"].get("worker") if before else None)
    verdict = (rec.get("bench_flash_verdict") or [None])[0]
    taken = isinstance(verdict, str) and verdict.split("+")[-1] in ("c", "d", "d'", "e")
    if kind == "truncated" and taken:
        v.check("FINDING: the truncated image was taken (it must be refused)", False, rec["bench_flash_verdict"])
    v.check("nothing taken (bench_flash verdict a1 or b)", verdict in NOT_TAKEN, rec.get("bench_flash_verdict"))
    if not (before and after):
        return v.result()
    # A read requested before the upload connected is not inside it, even if answered
    # after: the server may serve it before the upload's handler runs (reviewer,
    # 2026-10-16). It is listed as in flight.
    inside = [r for r in ps if r["t_req"] >= t0 and t0 < r["t_resp"] < t1]
    v.facts["in_flight_at_connect"] = [(round(r["t_req"] - t0, 3), round(r["t_resp"] - t0, 3), r["page"].get("worker"))
                                       for r in ps if r["t_req"] < t0 < r["t_resp"]]
    if inside:
        bad = [(round(r["t_resp"] - t0, 2), r["page"].get("worker")) for r in inside if r["page"].get("worker") != "updating"]
        v.check("a page answered inside the upload reads worker updating", not bad,
                "%d answered inside; not updating: %s" % (len(inside), bad[:5]))
    else:
        q = [(round(r["t_req"] - t0, 2), round(r["t_resp"] - t0, 2)) for r in ps if t0 < r["t_req"] < t1]
        v.facts["updating"] = ("not observable on the bench (reads queued behind the upload): %d read(s) "
                               "sent inside it, answered after it ended (sent, answered; s from the "
                               "upload's start): %s; upload %.2f s" % (len(q), q[:6], t1 - t0))
    off = [(round(r["t_req"] - t1, 2), r["page"].get("worker"), r["page"].get("mode")) for r in after
           if r["page"].get("worker") != "idle" or r["page"].get("mode") != 0]
    v.check("every page after the upload reads worker idle, mode 0", not off, "%d pages; off: %s" % (len(after), off[:5]))
    ok, det = one_boot(rows)
    v.check("elf_sha256 unchanged and uptime continuous", ok, det)
    pb, pz = before[-1]["page"], after[-1]["page"]
    v.check("bus_violations unchanged", "bus_violations" in pb and pb.get("bus_violations") == pz.get("bus_violations"),
            "%s -> %s" % (pb.get("bus_violations"), pz.get("bus_violations")))
    c = J.Ctx(frames_after, [arm_post_after])
    n = len(c.dev_answering(arm_post_after["t_ret"], float("inf")))
    v.check("the arm after it works (the device answers the VCM)", n >= MIN_ANSWERS, "%d answers" % n)
    v.facts.update({"kind": kind, "verdict": rec.get("bench_flash_verdict"), "predicted": PREDICTED[kind],
                    "upload_s": round(t1 - t0, 3),
                    "status": up.get("status_line"), "ending": up.get("ending"), "answers_after": n})
    return v.result()


def e4_frames_and_arm(run_path, arm="idle"):
    import provenance_classifier as P
    import mode_change_judge as K
    d = json.load(open(run_path, encoding="utf-8"))
    a = d["arms"][arm]
    wit = a.get("witness_raw")
    wpath = os.path.join(os.path.dirname(run_path), os.path.basename(wit)) if wit else \
        run_path[:-5] + "_%s_witness.log.gz" % arm
    frames = [(t, i, b.hex()) for t, i, b in P.read_witness(wpath)]
    p = K.posts_from_run(d, arm)[0]
    return frames, {"mode": p["mode"], "t_ret": p["t_ret"]}


def show(r, title):
    print("%s  (%s)" % (r["verdict"], title))
    for x in r["preconditions"]:
        if not x["ok"]:
            print("    NOT MET: %s -- %s" % (x["name"], x["detail"]))
    print("    %d preconditions met of %d" % (sum(x["ok"] for x in r["preconditions"]), len(r["preconditions"])))
    for x in r["checks"]:
        print("    %s: %s -- %s" % ("ok     " if x["ok"] else "FAIL   ", x["name"], x["detail"]))
    for k, val in r["facts"].items():
        print("    %s: %s" % (k, val))
    return 0 if r["verdict"] == "AS SPECIFIED" else 1


def main(argv):
    if len(argv) >= 2 and argv[0] == "armed":
        rec = json.load(open(argv[1], encoding="utf-8"))
        base = os.path.dirname(argv[1])
        frames, arm = e4_frames_and_arm(os.path.join(base, rec["e4"]))
        rows = [json.loads(l) for l in open(os.path.join(base, rec["pages"])) if l.strip()]
        return show(judge_armed(rec, frames, rows, arm), os.path.basename(argv[1]))
    if len(argv) == 3 and argv[0] == "failed":
        rec = json.load(open(argv[1], encoding="utf-8"))
        frames, arm = e4_frames_and_arm(argv[2])
        return show(judge_failed(rec, frames, arm), "%s, %s" % (os.path.basename(argv[1]), os.path.basename(argv[2])))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
