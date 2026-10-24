#!/usr/bin/env python3
"""Test gi_ecc.py, the /gi_ecc reader: format checks, the reviewer's three caveats,
alignment with the witness and the per-pulse split. Synthetic pages, no hardware.

    py -3.14 -B tools/gen_inhibit/bench/test_gi_ecc.py
    py -3.14 -B tools/gen_inhibit/bench/test_gi_ecc.py --mutants

Gen-inhibit tester, 2026-10-20.
"""
import copy
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def load(path):
    spec = importlib.util.spec_from_file_location("gi_ecc_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ECC_ACK_TX = (3 << 6) | (0 << 5) | 25     # "other", TX, ACK slot
ECC_BIT_RX = (0 << 6) | (1 << 5) | 10     # "bit", RX, data
TYPES = ("bit", "form", "stuff", "other")


def row(t, seq, ecc=ECC_ACK_TX, tec=8, rec=0, status=0x20, events=0x80):
    return [t, seq, ecc, TYPES[ecc >> 6], (ecc >> 5) & 1, ecc & 0x1F, tec, rec, status, events]


def page(n=29, over=None, latch_pulse=10, t0=5000000, step=1000000, ecc_alt=True, extra_rows=0):
    """n pulses at t0 + k*step on the device clock; over: {pulse: rows}."""
    over = over or {}
    isr, ticks, seq, be = [], [], 0, 0
    for k in range(n):
        t = t0 + k * step
        for j in range(over.get(k + 1, 1)):
            seq += 1
            ecc = ECC_BIT_RX if (ecc_alt and (k + j) % 2) else ECC_ACK_TX
            isr.append(row(t + j * 120, seq, ecc=ecc))
        be += over.get(k + 1, 1)
        ticks.append([t + 2000, be, min(over.get(k + 1, 1), 16), min(be, 16), 0,
                      1 if k + 1 == latch_pulse else 0])
    for _ in range(extra_rows):
        seq += 1
        isr.append(row(isr[-1][0] + 10, seq))
    total = len(isr)
    keep_isr = isr[-128:]
    keep_ticks = ticks[-64:]
    return {"probe": "gi_ecc", "now_us": t0 + n * step + 20000000, "isr_total": total,
            "isr_overwritten": total - len(keep_isr), "driver_bus_error": be,
            "ticks_total": len(ticks), "ticks_overwritten": len(ticks) - len(keep_ticks),
            "isr": keep_isr, "ticks": keep_ticks}


def wit(n=29, w0=110.0, step=1.0, per=None, jitter=None):
    per = per or {}
    out = []
    for k in range(n):
        t = w0 + k * step + (jitter or {}).get(k + 1, 0.0)
        out += [t + j * 0.0003 for j in range(per.get(k + 1, 1))]
    return out


def safe(f):
    """A check's expression, False (not a crash) when the shape is wrong."""
    try:
        return f()
    except (KeyError, IndexError, TypeError):
        return False


def checks(G):
    real = G.summary

    def summary(*a, **k):
        s = real(*a, **k)
        for key, v in (("pulses", []), ("overcounts", []), ("caveats", []), ("why", "")):
            s.setdefault(key, v)
        return s
    G.summary = summary
    # ---- a good page
    p = page()
    probs, cav = G.check_page(p)
    check("a good page: no problems, no caveats", probs == [] and cav == [], (probs, cav))
    s = G.summary(p, wit(), 1.0, judge_first=10)
    check("a good page aligns 29 device groups with 29 witness groups",
          s.get("aligned") and s["device_groups"] == 29 and s["witness_groups"] == 29, s.get("why"))
    check("pulses 1-10 (the 10th latches) are pre-latch, 11-29 post-latch, by the device latch tick",
          [k["phase"] for k in s["pulses"]] == ["pre-latch"] * 10 + ["post-latch"] * 19, [k["phase"] for k in s["pulses"]])
    check("judged marks the first 10 only", [k["judged"] for k in s["pulses"]] == [True] * 10 + [False] * 19)
    check("each pulse carries its decoded rows and its tick",
          safe(lambda: len(s["pulses"]) == 29 and
               all(k["device_rows"] == 1 and len(k["isr"]) == 1 and len(k["ticks"]) == 1 for k in s["pulses"]) and
               s["pulses"][0]["isr"][0]["seg_name"] == "ACK slot" and s["pulses"][1]["isr"][0]["seg_name"] == "data" and
               s["pulses"][0]["isr"][0]["dir_name"] == "TX" and s["pulses"][1]["isr"][0]["dir_name"] == "RX" and
               "BUS_ERR" in s["pulses"][0]["isr"][0]["event_bits"] and
               "transmitting" in s["pulses"][0]["isr"][0]["status_bits"]),
          s["pulses"][:2])
    check("no over-counts", s["overcounts"] == [] and s["caveats"] == [], (s["overcounts"], s["caveats"]))

    # ---- an over-count, pre- and post-latch
    p = page(over={2: 11, 15: 10})
    s = G.summary(p, wit(), 1.0, judge_first=10)
    check("over-counts at pulses 2 and 15 are found", s.get("overcounts") == [2, 15], s.get("overcounts"))
    k2, k15 = (s["pulses"][1], s["pulses"][14]) if len(s["pulses"]) == 29 else ({}, {})
    check("pulse 2: 11 device rows, 1 witness frame, pre-latch; pulse 15 post-latch",
          safe(lambda: k2["device_rows"] == 11 and k2["witness_error_frames"] == 1 and k2["phase"] == "pre-latch" and
               k15["device_rows"] == 10 and k15["phase"] == "post-latch"), (k2.get("device_rows"), k15.get("phase")))
    check("each over-count is a caveat to compare, naming both counts",
          sum("11 device rows against 1 witness" in c for c in s["caveats"]) == 1 and
          sum("pulse 15 (post-latch): 10 device rows" in c for c in s["caveats"]) == 1, s["caveats"])
    s = G.summary(page(over={2: 3}), wit(per={2: 3}), 1.0)
    check("three device rows against three witness frames is not an over-count",
          s.get("overcounts") == [], s.get("overcounts"))

    # ---- the caveats
    p = page(ecc_alt=False)
    probs, cav = G.check_page(p)
    check("every row the same ecc is a caveat, not a problem",
          probs == [] and any("every isr row has ecc 0x" in c for c in cav), (probs, cav))
    p = page(n=29, over={3: 120})
    probs, cav = G.check_page(p)
    check("an overwritten isr ring is a caveat naming the lost rows",
          probs == [] and any("isr ring overwritten: %d rows lost" % (p["isr_total"] - 128) in c for c in cav), (probs, cav))
    p = page(n=70)
    probs, cav = G.check_page(p)
    check("an overwritten tick ring is a caveat", probs == [] and any("tick ring overwritten: 6 rows lost" in c for c in cav),
          (probs, cav))

    # ---- problems (not in the format)
    def bad(mut, want):
        q = copy.deepcopy(page())
        mut(q)
        pr, _ = G.check_page(q)
        return any(want in x for x in pr), pr
    for name, mut, want in (
            ("not a gi_ecc page", lambda q: q.update(probe="gi_txabort"), "not a gi_ecc page"),
            ("a missing key", lambda q: q.pop("ticks_overwritten"), "no ticks_overwritten"),
            ("a short isr row", lambda q: q["isr"][3].pop(), "isr row 3 has 9 fields"),
            ("a short tick row", lambda q: q["ticks"][2].pop(), "tick row 2 has 5 fields"),
            ("a type that does not match ecc", lambda q: q["isr"][0].__setitem__(3, "bit"), "type 'bit' but ecc"),
            ("a seg that does not match ecc", lambda q: q["isr"][0].__setitem__(5, 10), "dir/seg do not match"),
            ("a dir that does not match ecc", lambda q: q["isr"][0].__setitem__(4, 1), "dir/seg do not match"),
            ("a row without BUS_ERR", lambda q: q["isr"][0].__setitem__(9, 0x200), "no BUS_ERR event bit"),
            ("a seq gap", lambda q: q["isr"][5].__setitem__(1, 99), "seq"),
            ("isr_total not the last seq", lambda q: q.update(isr_total=30), "but isr_total 30"),
            ("isr_overwritten inconsistent", lambda q: q.update(isr_overwritten=2), "isr_overwritten 2"),
            ("ticks_overwritten inconsistent", lambda q: q.update(ticks_overwritten=1), "ticks_overwritten 1"),
            ("time going backwards", lambda q: q["isr"][4].__setitem__(0, 1), "isr t_us goes backwards"),
            ("a row after now_us", lambda q: q.update(now_us=1), "after now_us"),
            ("too many isr rows", lambda q: q.update(isr=[row(i, i + 1) for i in range(129)], isr_total=129),
             "129 isr rows")):
        ok, pr = bad(mut, want)
        check("problem: %s" % name, ok, pr)
    s = real(dict(page(), probe="x"), wit(), 1.0)
    check("a page with problems gives no pulses", s["problems"] and "pulses" not in s, s)

    # ---- alignment
    s = G.summary(page(), wit(n=28), 1.0)
    check("29 device groups against 28 witness groups is not aligned, and says so",
          s.get("aligned") is False and "29 device groups, 28 witness groups" in s["why"] and s["pulses"] == [] and
          any("not aligned" in c for c in s["caveats"]), s.get("why"))
    s = G.summary(page(), wit(jitter={20: 0.2}), 1.0)
    check("a group 0.2 s off the others is not aligned", s.get("aligned") is False and "disagree" in s["why"], s.get("why"))
    s = G.summary(page(), wit(jitter={20: 0.02}), 1.0)
    check("20 ms of host jitter still aligns", s.get("aligned") is True, s.get("why"))
    s = G.summary(page(), wit(w0=5000.0), 1.0)
    check("the offset is the clock difference", s.get("aligned") and abs(s["offset_s"] - (5000.0 - 5.0)) < 1e-6,
          s.get("offset_s"))
    s = G.summary(page(latch_pulse=12), wit(), 1.0)
    check("a latch at pulse 12: 12 pre-latch, 17 post-latch",
          [k["phase"] for k in s["pulses"]] == ["pre-latch"] * 12 + ["post-latch"] * 17, [k["phase"] for k in s["pulses"]])
    s = G.summary(page(latch_pulse=0), wit(), 1.0)
    check("no latch tick: every pulse pre-latch, latch_t_us None",
          safe(lambda: len(s["pulses"]) == 29 and s["latch_t_us"] is None and all(k["phase"] == "pre-latch" for k in s["pulses"])))

    # ---- fetch
    d, txt = G.fetch(lambda path: '  {"probe":"gi_ecc","x":1}' if path == "/gi_ecc" else "")
    check("fetch reads /gi_ecc", d == {"probe": "gi_ecc", "x": 1}, (d, txt))
    d, txt = G.fetch(lambda path: (_ for _ in ()).throw(OSError("timed out")))
    check("fetch reports a failed GET", d is None and "OSError: timed out" in txt, txt)
    d, txt = G.fetch(lambda path: "<html>")
    check("fetch reports a non-JSON answer", d is None and "not JSON" in txt, txt)
    gate_checks(G, real)


S1 = gi_paths.pinned_tree(gi_paths.runs("b6_ecc_20261020T214017"))


def seg_page(segs, ecc_type=0, d=1):
    """A good page whose rows carry these segment codes (type bit, dir RX, REC 1)."""
    p = page(n=len(segs))
    for r, sg in zip(p["isr"], segs):
        e = (ecc_type << 6) | (d << 5) | sg
        r[2:6] = [e, TYPES[ecc_type], d, sg]
        r[6], r[7] = 0, 1
    return p


def gate_checks(G, summary):
    # ---- the TRM segment table (reviewer, 2026-10-20)
    check("TRM table: 3 is SOF, 0 and 1 undefined; 25 ACK slot, 10 data",
          G.seg_name(3) == "SOF" and G.seg_name(0) == "undefined seg 0" and G.seg_name(1) == "undefined seg 1"
          and G.seg_name(25) == "ACK slot" and G.seg_name(10) == "data",
          [G.seg_name(x) for x in (3, 0, 1, 25, 10)])
    check("TRM table: 24 codes defined; undefined 0, 1, 16, 20, 21, 29, 30, 31",
          len(G.SEG) == 24 and sorted(G.UNDEFINED_SEG) == [0, 1, 16, 20, 21, 29, 30, 31], sorted(G.UNDEFINED_SEG))
    # ---- the liveness gate: session 1 is the positive control
    import json
    recs = sorted(f for f in os.listdir(S1) if f.startswith("x2_jam_threshold_") and f.endswith(("0.json", "1.json",
                  "2.json", "3.json", "4.json", "5.json", "6.json", "7.json", "8.json", "9.json")))
    pages = [a["ecc_page"] for f in recs for a in json.load(open(os.path.join(S1, f)))["arms"]]
    gs = [G.liveness(pg) for pg in pages]
    check("session 1 (positive control): 20 pages, every one stops the gate on its undefined segments",
          len(gs) == 20 and all(g["stop"] and g["why"] == ["%d of %d isr rows have a segment the TRM does not define"
                                                           % (g["rows"], g["rows"])]
                               and g["distinct_ecc"] == 1 and g["undefined_seg"] == g["rows"] for g in gs), [(g["rows"], g["distinct_ecc"], g["undefined_seg"], g["stop"]) for g in gs][:3])
    dcs = [G.dir_check(pg["isr"]) for pg in pages]
    check("session 1 (positive control): the dir check: 7 TEC-8 rows TX and agreeing, 1 not judged, the other 570 (all dir TX) disagree",
          all(dc["disagree"] for dc in dcs) and sum(len(dc["disagree"]) for dc in dcs) == 570
          and sum(dc["TX"] for dc in dcs) == 7 and sum(dc["not judged"] for dc in dcs) == 1,
          (sum(len(dc["disagree"]) for dc in dcs), sum(dc["TX"] for dc in dcs), sum(dc["not judged"] for dc in dcs)))
    s = summary(pages[0], [], 1.0)
    check("session 1's first page: the summary names the gate's STOP and the undefined rows with their codes",
          any(c.startswith("liveness gate: STOP") for c in s["caveats"])
          and any("29 isr rows with a segment the TRM does not define" in c and "1:0x00, 2:0x00" in c for c in s["caveats"]),
          s["caveats"])
    # ---- a live page with one 0x00 row in 29 (the reviewer's fixture) passes, and lists it
    live = [10, 25, 3, 8, 17] * 6
    p = seg_page(live[:28] + [0])
    g = G.liveness(p)
    check("1 row in 29 with segment 0 among live codes: no stop, the row listed with its code",
          not g["stop"] and g["undefined_rows"] == [(29, 0)] and g["distinct_ecc"] > 1, g)
    s = summary(p, wit(), 1.0)
    check("... and the summary reports it without a STOP",
          not any("STOP" in c for c in s["caveats"]) and any("1 isr rows with a segment" in c and "29:0x00" in c for c in s["caveats"]),
          s["caveats"])
    # ---- the boundaries
    check("15 of 29 undefined stops; 14 of 29 does not",
          G.liveness(seg_page([0] * 15 + [10] * 14))["stop"] and not G.liveness(seg_page([0] * 14 + [10] * 15))["stop"])
    check("14 of 28 undefined (exactly half) does not stop",
          not G.liveness(seg_page([1] * 14 + [10, 25] * 7))["stop"])
    a2 = page(n=29)
    for r in a2["isr"]:
        r[2:8] = [0xA2, "stuff", 1, 2, 0, 1]
    g = G.liveness(a2)
    check("29 rows all 0xa2 (session 2's live single-code arms): no stop, one distinct ecc reported",
          not g["stop"] and g["why"] == [] and g["distinct_ecc"] == 1, g)
    g = G.liveness(seg_page([0] * 29))
    check("29 rows all segment 0: stop, on the undefined segments alone",
          g["stop"] and g["why"] == ["29 of 29 isr rows have a segment the TRM does not define"], g)
    q = dict(seg_page(live[:29]), isr_distinct_ecc=5, isr_undefined_seg=0)
    check("the page's own counts agreeing: no mismatch", G.liveness(q)["mismatch"] == [], G.liveness(q)["mismatch"])
    q = dict(seg_page(live[:29]), isr_distinct_ecc=1, isr_undefined_seg=3)
    g = G.liveness(q)
    check("the page's own counts disagreeing: both listed, not a stop",
          not g["stop"] and g["mismatch"] == ["page isr_distinct_ecc 1, the rows give 5",
                                              "page isr_undefined_seg 3, the rows give 0"], g["mismatch"])
    # ---- the dir rule, on session 1's values (reviewer, 2026-10-20)
    def rows(vals):
        return [row(1000 * i, i + 1, ecc=(d << 5) | 10, tec=t, rec=r) for i, (t, r, d) in enumerate(vals)]
    seq = [(8, 0, 0), (3, 1, 1), (0, 1, 1), (0, 9, 1), (0, 0, 0)]
    dc = G.dir_check(rows(seq))
    check("dir rule: TEC 8/REC 0 TX, TEC 3/REC 1 RX (TEC fell), TEC 0/REC 1 and 0/9 RX, TEC 0/REC 0 not judged",
          dc["disagree"] == [] and (dc["TX"], dc["RX"], dc["not judged"]) == (1, 3, 1), dc)
    dc = G.dir_check(rows([(8, 0, 1), (3, 1, 0), (0, 0, 1)]))
    check("dir rule: disagreements listed with what was expected; not judged is never one",
          [(x["seq"], x["expect"]) for x in dc["disagree"]] == [(1, 0), (2, 1)], dc)
    dc = G.dir_check(rows([(8, 0, 0), (8, 0, 0), (16, 0, 0)]))
    check("dir rule: TEC equal to the previous row is not a rise; above it is",
          (dc["TX"], dc["not judged"]) == (2, 1), dc)
    p = page(n=29, over={3: 120})
    p["isr"][0] = row(p["isr"][0][0], p["isr"][0][1], ecc=ECC_BIT_RX)     # TEC 8, dir RX, first kept row
    s = summary(p, wit(), 1.0)
    check("the summary does not judge the first row of an overwritten isr ring",
          safe(lambda: p["isr_overwritten"] > 0 and s["dir"]["disagree"] == [] and s["dir"]["not judged"] == len(p["isr"])),
          s.get("dir"))
    dc = G.dir_check(rows([(8, 0, 1), (16, 0, 0)]), first_after_reset=False)
    check("dir rule: the first row is not judged when the ring was overwritten",
          dc["not judged"] == 1 and dc["disagree"] == [] and dc["TX"] == 1, dc)


MUTANTS = [
    ("the same-ecc caveat never fires", "if len(isr) >= 2 and len({r[2] for r in isr}) == 1:", "if False:"),
    ("an overwritten ring is not a caveat", '    if p["isr_overwritten"] > 0:\n', '    if False:\n'),
    ("an overwritten tick ring is not a caveat", '    if p["ticks_overwritten"] > 0:\n', '    if False:\n'),
    ("the over-count is >= not >", "if len(rows) > len(w):", "if len(rows) >= len(w):"),
    ("no over-count is ever found", "            k[\"overcount\"] = True\n            out[\"overcounts\"].append(i + 1)\n",
     "            pass\n"),
    ("the latch split is inverted", '"pre-latch" if latch is None or d[0] < latch else "post-latch"',
     '"post-latch" if latch is None or d[0] < latch else "pre-latch"'),
    ("the phase is by pulse number, not the latch tick", "d[0] < latch else", "i < 10 else"),
    ("judged ignores judge_first", "judge_first is None or i < judge_first", "True"),
    ("alignment ignores the group counts", "    if len(dev_groups_us) != len(wit_groups_s):\n",
     "    if False:\n"),
    ("alignment ignores the residual", "    if res > tol_s:\n", "    if False:\n"),
    ("the seq check is missing", '    if any(b != a + 1 for a, b in zip(seqs, seqs[1:])):\n', '    if False:\n'),
    ("the overwritten count is not checked", '    if p["isr_overwritten"] != p["isr_total"] - len(isr):\n',
     '    if False:\n'),
    ("the type is not checked against ecc", '        if d["type"] != TYPES[(d["ecc"] >> 6) & 3]:\n', '        if False:\n'),
    ("dir is read from the wrong bit", '(d["ecc"] >> 5) & 1', '(d["ecc"] >> 4) & 1'),
    ("BUS_ERR is not required", '        if not d["events"] & BUS_ERR:\n', '        if False:\n'),
    ("the over-count caveat is dropped", '        if k.get("overcount"):\n', '        if False:\n'),
    ("rows are taken from the next pulse too", 'rows = [r for r in isr if d[0] <= r["t_us"] <= d[-1]]',
     'rows = [r for r in isr if lo <= r["t_us"] < hi + 1]'),
    ("SOF back to IDF's 0", '3: "SOF", 2: "ID28-21"', '0: "SOF", 2: "ID28-21"'),
    ("seg 0 named SOF", 'return SEG.get(seg, "undefined seg %s" % seg)', 'return SEG.get(seg, "SOF" if seg == 0 else "x")'),
    ("one ecc stops again", "    why = []\n    if n and len(und)",
     "    why = [\"one ecc\"] if n >= 20 and distinct == 1 else []\n    if n and len(und)"),
    ("half the rows stop", "if n and len(und) * 2 > n:", "if n and len(und) * 2 >= n:"),
    ("any undefined row stops", "if n and len(und) * 2 > n:", "if und:"),
    ("the page's counts not compared", "        if key in p and p[key] != mine:\n", "        if False:\n"),
    ("no STOP caveat", '    if g["stop"]:\n', "    if False:\n"),
    ("dir: the previous TEC never moves", '        prev_tec = d["tec"]\n', "        pass\n"),
    ("dir: equal TEC is a rise", 'exp = 0 if d["tec"] > prev_tec else', 'exp = 0 if d["tec"] >= prev_tec else'),
    ("dir: REC never expects RX", '1 if d["rec"] > 0 else None', "None"),
    ("dir: the overwritten first row judged", "        if i == 0 and not first_after_reset:\n", "        if False:\n"),
    ("dir: summary ignores the overwritten ring", 'first_after_reset=p["isr_overwritten"] == 0', "first_after_reset=True"),
    ("fetch swallows a failed GET", '        return None, "%s: %s" % (type(e).__name__, e)', '        return {}, ""'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(load(os.path.join(HERE, "gi_ecc.py")))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    src = open(os.path.join(HERE, "gi_ecc.py"), encoding="utf-8").read()
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            continue
        with tempfile.TemporaryDirectory(prefix="gi-ecc-m%d-" % i, dir=gi_paths.scratch("tmp", run=False)) as tmp:
            mp = os.path.join(tmp, "gi_ecc.py")
            open(mp, "w", encoding="utf-8").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--module", mp],
                               capture_output=True, text=True, timeout=300)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
        if fails and not crashed:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:90]))
        else:
            print("%s m%d %s  (rc %d)%s" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode,
                                            ("  " + r.stderr.strip().splitlines()[-1]) if crashed and r.stderr.strip() else ""))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--module"]:
        checks(load(sys.argv[2]))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
