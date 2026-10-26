#!/usr/bin/env python3
"""B25 (baseline part B): spec 7.1, key state, judged from an interlock run's
witness log -- B20's rekey-long. Gen-inhibit tester, 2026-10-16. No new rig.

Spec 7.1, as checked here (key = 0x592 IgnitionKeyState, fresh within 0.5 s):
  rule 1        no device 0x051 unless the latest key reading, fresh, is 1;
  decision 3    never-seen reads key-OFF: no device 0x051 before the first 0x592;
  rule 2        at each key 0 -> 1 (actual readings), a latch set before it
                (0x7F8 abort reason, or 0x7F1 disable reason) reads cleared on
                the first page of each after the edge + PAGE_LAG_S;
  re-entry      after a key-on that cleared a latch, no device 0x051 before the
                arm gate's contactor condition: a closed 0x440 (11/12) after the
                edge followed by a 0x411 (amendment 2026-09-24);
  armed         the device stays armed through a key-off: every 0x7F1 page in a
                key-off span reads a non-OFF diag_mode (and spec 10's cadence is
                interlock_judge's check, run separately).
Records the key edges, the latches and the resumptions as facts.

    py -3.14 gen-inhibit/tools/bench/b25_keystate_judge.py <run.json> [...]
Run from the reverse-it root. Tested by test_b25_keystate_judge.py.
"""
import bisect
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interlock_judge as J      # noqa: E402

KEY_FRESH_S = 0.5
EDGE_S = J.EDGE_S


def key_on_at(keys, t):
    """keys: [(t, 0/1)] sorted. The key as the device may read it at t: the latest
    reading at or before t + EDGE_S, if within KEY_FRESH_S; else off."""
    ts = [k[0] for k in keys]
    i = bisect.bisect_right(ts, t + EDGE_S) - 1
    if i < 0:
        return False
    tk, v = keys[i]
    if v == 1 and t - tk <= KEY_FRESH_S:
        return True
    # A 1 -> 0 reading within EDGE_S before t may not have reached the device yet.
    if v == 0 and i > 0 and keys[i - 1][1] == 1 and t - tk <= EDGE_S:
        return True
    return False


def judge(frames, posts, identity=None):
    c = J.Ctx(frames, posts, [], None, identity=identity)
    keys = c.series(0x592, J.PT, "IgnitionKeyState")
    checks, facts = [], {}

    def chk(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    dev = [t for t, _h in c.dev]
    bad = [t for t in dev if not key_on_at(keys, t)]
    chk("rule 1: no device 0x051 unless the key reads on (fresh)", not bad,
        "%d of %d, first %s" % (len(bad), len(dev), bad[:3]))
    first_key = keys[0][0] if keys else None
    early = [t for t in dev if first_key is None or t < first_key]
    chk("decision 3: no device 0x051 before the first 0x592", not early, "%d" % len(early))
    edges = [(t, v) for i, (t, v) in enumerate(keys) if i > 0 and v != keys[i - 1][1]]
    facts["key_edges"] = [(round(t, 3), v) for t, v in edges]
    ons = [t for t, v in edges if v == 1]
    facts["key_ons"] = len(ons)
    latched_ons = 0
    for n_on, te in enumerate(ons, 1):
        b8 = [(t, d) for t, d in c.p7f8 if t < te]
        b1 = [(t, d) for t, d in c.p7f1 if t < te]
        ab = J.abort_name(b8[-1][1]) if b8 else "none"
        dis = int(b1[-1][1]["diag_disable_reason"]) if b1 else 0
        a8 = [(t, d) for t, d in c.p7f8 if t >= te + J.PAGE_LAG_S]
        a1 = [(t, d) for t, d in c.p7f1 if t >= te + J.PAGE_LAG_S]
        rec = {"key_on": round(te, 3), "abort_before": ab, "disable_before": dis}
        if ab != "none" or dis:
            latched_ons += 1
            ab2 = J.abort_name(a8[0][1]) if a8 else None
            dis2 = int(a1[0][1]["diag_disable_reason"]) if a1 else None
            rec.update(abort_after=ab2, disable_after=dis2)
            chk("rule 2: key-on #%d clears the latch" % n_on,
                ab2 == "none" and (dis2 == 0 or not dis),
                "abort %s -> %s, disable %s -> %s" % (ab, ab2, dis, dis2))
            mc = [t for t, s in c.series(0x440, J.PT, "bcm_mainc_stat") if t > te and s in J.CLOSED]
            soc = [t for t in c.times(0x411) if mc and t > mc[0]]
            gate = soc[0] if soc else None
            nxt = [t for t, v in edges if t > te]
            span_end = nxt[0] if nxt else float("inf")
            pre = [t for t in dev if te < t < (gate if gate else span_end) and t < span_end]
            resumed = [t for t in dev if gate and gate <= t < span_end]
            rec.update(gate_open=round(gate, 3) if gate else None,
                       resumed=round(resumed[0], 3) if resumed else None)
            chk("re-entry: after key-on #%d, nothing before the contactor condition" % n_on,
                not pre, "%d device 0x051 before it" % len(pre))
        facts.setdefault("key_on_records", []).append(rec)
    facts["latched_key_ons"] = latched_ons
    offs = []
    for t, v in edges:
        if v == 0:
            nxt = [x for x, w in edges if x > t]
            offs.append((t, nxt[0] if nxt else (c.vcm_t[-1] if c.vcm_t else t)))
    zero = [(round(t, 2), int(d["diag_mode"])) for t, d in c.p7f1
            if any(a + J.PAGE_LAG_S <= t < b for a, b in offs) and int(d["diag_mode"]) == 0]
    in_off = [t for t, d in c.p7f1 if any(a + J.PAGE_LAG_S <= t < b for a, b in offs)]
    chk("armed through the key-off: every 0x7F1 page in a key-off span reads a non-OFF mode",
        in_off and not zero, "%d pages in key-off spans, %d read mode 0" % (len(in_off), len(zero)))
    facts["key_off_spans"] = [(round(a, 3), round(b, 3)) for a, b in offs]
    verdict = "AS SPECIFIED" if all(x["ok"] for x in checks) else "NOT AS SPECIFIED"
    if not ons:
        verdict = "INCONCLUSIVE (no key-on edge)"
    facts["diag_decode"] = c.diag_summary
    if c.undecodable:
        verdict = "ERROR (%d diag page(s) undecodable)" % len(c.undecodable)    # reading Q2
    return {"verdict": verdict, "checks": checks, "facts": facts}


def main(paths):
    worst = 0
    for p in paths:
        d = json.load(open(p))
        w = json.load(gzip.open(p[:-5] + "_witness.json.gz"))["frames"]
        r = judge([(x[0], x[1], x[2]) for x in w], d["posts"], d.get("decoder"))
        t0 = d["t0_epoch"]
        print("== %s: %s" % (os.path.basename(p), r["verdict"]))
        for ch in r["checks"]:
            print("  %s  %s  %s" % ("ok  " if ch["ok"] else "FAIL", ch["name"], ch["detail"]))
        f = r["facts"]
        print("  key edges (s from start): %s" % [(round(t - t0, 2), v) for t, v in f["key_edges"]])
        print("  key-off spans: %s" % [(round(a - t0, 2), round(b - t0, 2)) for a, b in f["key_off_spans"]])
        for rec in f.get("key_on_records", []):
            print("  key-on %s" % {k: (round(v - t0, 3) if isinstance(v, float) and v > 1e9 else v)
                                    for k, v in rec.items()})
        worst = max(worst, 0 if r["verdict"] == "AS SPECIFIED" else 1)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
