"""Reader for the B6 measurement build's /gi_ecc page (_ecc, GI_INSTRUMENT_ECC).

Gen-inhibit tester, 2026-10-20, for the B6 investigation (user: investigate first;
reviewer's plan, option 1 with 29-pulse arms). The format is the implementor's
(implementor/b6-ecc, main/gi_ecc_probe.c, ecc_handler), as sent 2026-10-20:

  {"probe":"gi_ecc", "now_us", "isr_total", "isr_overwritten", "driver_bus_error",
   "ticks_total", "ticks_overwritten", "isr":[...], "ticks":[...]}
  isr row:  [t_us, seq, ecc, type, dir, seg, tec, rec, status, events]
  tick row: [t_us, bus_error_count, pushed, err_n, seeded, latched]

All t_us are the device's esp_timer clock (us since boot). The rings are cleared
only by a reboot, so the page is read after each arm, before the next arm's reset.

This module REPORTS; it judges nothing about the device. Three kinds of output:
  problems  the page is not in the format (a reader that cannot parse it must say
            so rather than report fewer events);
  caveats   the reviewer's three, reported and not judged: every row the same
            ecc (the build's assumption that the probe reads the captured code
            would then be wrong); a ring overwritten (too small for the arm);
            an over-count whose device rows outnumber the witness's error frames
            (compare them: an error captured between the HAL's release and the
            probe's read is logged under the earlier interrupt);
  pulses    per witness pulse: the witness error frames, the device's isr rows
            (decoded) and tick rows, pre- or post-latch by the device's own
            latch tick.

Pure functions are tested by test_gi_ecc.py (no hardware). fetch() needs the bench.
"""
import json
import statistics

PROBE = "gi_ecc"
ISR_COLS = ("t_us", "seq", "ecc", "type", "dir", "seg", "tec", "rec", "status", "events")
TICK_COLS = ("t_us", "bus_error_count", "pushed", "err_n", "seeded", "latched")
ISR_RING, TICK_RING = 128, 64
TYPES = ("bit", "form", "stuff", "other")          # ecc bits 7:6
# Segment codes (ecc bits 4:0), ESP32-C3 TRM v1.4 Table 31.4-12, the SJA1000 layout
# (reviewer, 2026-10-20: confirmed against the implementor's ECC_TRM_SEGS and IDF v5.4.1's
# twai_ll_err_seg_t, which agrees except start of frame: IDF numbers it 0, the TRM 3).
# The implementor quoted the TRM's table (reviewer, 2026-10-20): SOF is 0b00011, and 0, 1,
# 16, 20, 21 and 29-31 are undefined. Until 2026-10-20 this table was IDF's (0: SOF, no 3).
SEG = {3: "SOF", 2: "ID28-21", 4: "SRTR", 5: "IDE", 6: "ID20-18", 7: "ID17-13",
       8: "CRC seq", 9: "R0", 10: "data", 11: "DLC", 12: "RTR", 13: "R1", 14: "ID4-0",
       15: "ID12-5", 17: "active error flag", 18: "intermission",
       19: "dominant-bit tolerance", 22: "passive error flag", 23: "error delimiter",
       24: "CRC delimiter", 25: "ACK slot", 26: "EOF", 27: "ACK delimiter",
       28: "overload flag"}
STATUS = {0x01: "rx buffer", 0x02: "data overrun", 0x04: "tx buffer free",
          0x08: "tx complete", 0x10: "receiving", 0x20: "transmitting",
          0x40: "error status", 0x80: "bus-off", 0x100: "miss"}
EVENTS = {0x01: "bus off", 0x02: "recovery complete", 0x04: "recovery in progress",
          0x08: "above warning", 0x10: "below warning", 0x20: "error passive",
          0x40: "error active", 0x80: "BUS_ERR", 0x100: "arbitration lost",
          0x200: "rx frame", 0x400: "tx buffer free", 0x800: "peripheral reset"}
BUS_ERR = 0x80
UNDEFINED_SEG = frozenset(range(32)) - frozenset(SEG)
ALIGN_TOL_S = 0.05     # witness vs device group start, after the median offset


def bits(v, table):
    return [name for b, name in sorted(table.items()) if v & b]


def seg_name(seg):
    # Code 0 is undefined in the TRM; IDF's twai_ll_err_seg_t calls it SOF.
    return SEG.get(seg, "undefined seg %s" % seg)


def liveness(p):
    """The session gate (reviewer, 2026-10-20), on a page check_page() passed.

    stop when more than half the rows have a segment the TRM table does not define.
    One distinct ecc over many rows is NOT a stop (reviewer, 2026-10-21): session 2 on
    a55d52c read 0xa2 on all 29 rows of 5 live arms; distinct_ecc is still reported. Every
    undefined-segment row is listed either way, with its code; a few are reported, not
    a stop, so a real start-of-frame hit cannot stop a session if the TRM/IDF
    difference cuts the other way. Counted from the rows; the page's own
    isr_distinct_ecc and isr_undefined_seg (a55d52c on; absent before) are compared
    with that count, and a difference is listed, not a stop."""
    isr = p["isr"]
    n = len(isr)
    distinct = len({r[2] for r in isr})
    und = [(r[1], r[5]) for r in isr if r[5] in UNDEFINED_SEG]
    why = []
    if n and len(und) * 2 > n:
        why.append("%d of %d isr rows have a segment the TRM does not define" % (len(und), n))
    mism = []
    for key, mine in (("isr_distinct_ecc", distinct), ("isr_undefined_seg", len(und))):
        if key in p and p[key] != mine:
            mism.append("page %s %s, the rows give %d" % (key, p[key], mine))
    return {"rows": n, "distinct_ecc": distinct, "undefined_seg": len(und), "undefined_rows": und,
            "page_distinct_ecc": p.get("isr_distinct_ecc"), "page_undefined_seg": p.get("isr_undefined_seg"),
            "mismatch": mism, "stop": bool(why), "why": why}


def dir_check(isr, first_after_reset=True):
    """Each row's dir against the counter that moved (reviewer, 2026-10-20). TEC above
    the previous row's (above 0 on the first row after the reset) expects TX (0);
    otherwise REC above 0 expects RX (1); otherwise not judged. When the first row on
    the page is not the first after the reset (the ring was overwritten), it is not
    judged. Reports; never a stop."""
    out = {"TX": 0, "RX": 0, "not judged": 0, "disagree": []}
    prev_tec = 0
    for i, r in enumerate(isr):
        d = dict(zip(ISR_COLS, r))
        exp = 0 if d["tec"] > prev_tec else 1 if d["rec"] > 0 else None
        if i == 0 and not first_after_reset:
            exp = None
        prev_tec = d["tec"]
        if exp is None:
            out["not judged"] += 1
            continue
        out["TX" if exp == 0 else "RX"] += 1
        if d["dir"] != exp:
            out["disagree"].append({"seq": d["seq"], "tec": d["tec"], "rec": d["rec"], "dir": d["dir"],
                                    "expect": exp})
    return out


def decode_isr(r):
    d = dict(zip(ISR_COLS, r))
    d["seg_name"] = seg_name(d["seg"])
    d["dir_name"] = "RX" if d["dir"] else "TX"
    d["status_bits"] = bits(d["status"], STATUS)
    d["event_bits"] = bits(d["events"], EVENTS)
    return d


def decode_tick(r):
    return dict(zip(TICK_COLS, r))


def check_page(p):
    """(problems, caveats) for one page. problems: not in the format."""
    probs, cav = [], []
    if not isinstance(p, dict) or p.get("probe") != PROBE:
        return ["not a %s page" % PROBE], cav
    for k in ("now_us", "isr_total", "isr_overwritten", "driver_bus_error", "ticks_total",
              "ticks_overwritten", "isr", "ticks"):
        if k not in p:
            probs.append("no %s" % k)
    if probs:
        return probs, cav
    isr, ticks = p["isr"], p["ticks"]
    if len(isr) > ISR_RING:
        probs.append("%d isr rows (ring is %d)" % (len(isr), ISR_RING))
    if len(ticks) > TICK_RING:
        probs.append("%d tick rows (ring is %d)" % (len(ticks), TICK_RING))
    for i, r in enumerate(isr):
        if not isinstance(r, list) or len(r) != len(ISR_COLS):
            probs.append("isr row %d has %s fields" % (i, len(r) if isinstance(r, list) else "no"))
            continue
        d = dict(zip(ISR_COLS, r))
        if d["type"] != TYPES[(d["ecc"] >> 6) & 3]:
            probs.append("isr seq %s: type %r but ecc 0x%02x" % (d["seq"], d["type"], d["ecc"]))
        if d["dir"] != (d["ecc"] >> 5) & 1 or d["seg"] != d["ecc"] & 0x1F:
            probs.append("isr seq %s: dir/seg do not match ecc 0x%02x" % (d["seq"], d["ecc"]))
        if not d["events"] & BUS_ERR:
            probs.append("isr seq %s: no BUS_ERR event bit" % d["seq"])
    for i, r in enumerate(ticks):
        if not isinstance(r, list) or len(r) != len(TICK_COLS):
            probs.append("tick row %d has %s fields" % (i, len(r) if isinstance(r, list) else "no"))
    if probs:
        return probs, cav
    seqs = [r[1] for r in isr]
    if any(b != a + 1 for a, b in zip(seqs, seqs[1:])):
        probs.append("isr seq not consecutive")
    if isr and seqs[-1] != p["isr_total"]:
        probs.append("last isr seq %d but isr_total %d" % (seqs[-1], p["isr_total"]))
    if p["isr_overwritten"] != p["isr_total"] - len(isr):
        probs.append("isr_overwritten %d but isr_total %d with %d rows" % (
            p["isr_overwritten"], p["isr_total"], len(isr)))
    if p["ticks_overwritten"] != p["ticks_total"] - len(ticks):
        probs.append("ticks_overwritten %d but ticks_total %d with %d rows" % (
            p["ticks_overwritten"], p["ticks_total"], len(ticks)))
    for name, rows in (("isr", isr), ("tick", ticks)):
        ts = [r[0] for r in rows]
        if any(b < a for a, b in zip(ts, ts[1:])):
            probs.append("%s t_us goes backwards" % name)
        if ts and ts[-1] > p["now_us"]:
            probs.append("%s t_us after now_us" % name)
    if probs:
        return probs, cav
    if len(isr) >= 2 and len({r[2] for r in isr}) == 1:
        cav.append("every isr row has ecc 0x%02x (%d rows): the build's assumption that the "
                   "probe reads the captured code may be wrong" % (isr[0][2], len(isr)))
    if p["isr_overwritten"] > 0:
        cav.append("isr ring overwritten: %d rows lost" % p["isr_overwritten"])
    if p["ticks_overwritten"] > 0:
        cav.append("tick ring overwritten: %d rows lost" % p["ticks_overwritten"])
    return probs, cav


def groups_us(ts_us, gap_us):
    out = []
    for t in sorted(ts_us):
        if out and t - out[-1][-1] <= gap_us:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def groups_s(ts, gap_s):
    return groups_us(ts, gap_s)


def align(dev_groups_us, wit_groups_s, tol_s=ALIGN_TOL_S):
    """Map device groups to witness groups by order, and show the mapping holds.

    Returns (offset_s or None, residual_s or None, why). Aligned only when the
    counts are equal and every group's start agrees within tol_s after the median
    offset; otherwise offset is None and why says which."""
    if not dev_groups_us or not wit_groups_s:
        return None, None, "no %s groups" % ("device" if not dev_groups_us else "witness")
    if len(dev_groups_us) != len(wit_groups_s):
        return None, None, "%d device groups, %d witness groups" % (len(dev_groups_us), len(wit_groups_s))
    offs = [w[0] - d[0] / 1e6 for d, w in zip(dev_groups_us, wit_groups_s)]
    med = statistics.median(offs)
    res = max(abs(o - med) for o in offs)
    if res > tol_s:
        return None, round(res, 4), "group starts disagree by up to %.3f s after the median offset" % res
    return med, round(res, 4), ""


def pulses(p, wit_errs, interval_s, judge_first=None):
    """Per witness pulse, the device's rows. p: a page check_page() passed.

    wit_errs: witness error-frame times (host clock) from just before the pulses to
    the arm's end. Groups split at interval/2 on both clocks. Returns a dict:
    aligned, why, offset_s, residual_s, latch_t_us, pulses (list), overcounts."""
    half = interval_s / 2.0
    isr = [decode_isr(r) for r in p["isr"]]
    ticks = [decode_tick(r) for r in p["ticks"]]
    dg = groups_us([r["t_us"] for r in isr], half * 1e6)
    wg = groups_s(wit_errs, half)
    off, res, why = align(dg, wg)
    latch = next((t["t_us"] for t in ticks if t["latched"]), None)
    out = {"aligned": off is not None, "why": why, "offset_s": None if off is None else round(off, 4),
           "residual_s": res, "device_groups": len(dg), "witness_groups": len(wg),
           "latch_t_us": latch, "pulses": [], "overcounts": []}
    if off is None:
        return out
    for i, (d, w) in enumerate(zip(dg, wg)):
        lo, hi = d[0], (dg[i + 1][0] if i + 1 < len(dg) else float("inf"))
        rows = [r for r in isr if d[0] <= r["t_us"] <= d[-1]]
        tk = [t for t in ticks if lo <= t["t_us"] < hi]
        k = {"pulse": i + 1, "witness_t": round(w[0], 6), "witness_error_frames": len(w),
             "device_rows": len(rows), "device_t_us": d[0],
             "phase": ("pre-latch" if latch is None or d[0] < latch else "post-latch"),
             "judged": judge_first is None or i < judge_first,
             "isr": rows, "ticks": tk}
        if len(rows) > len(w):
            k["overcount"] = True
            out["overcounts"].append(i + 1)
        out["pulses"].append(k)
    return out


def summary(p, wit_errs, interval_s, judge_first=None):
    """check_page + pulses, as one record entry (the raw page is kept beside it)."""
    probs, cav = check_page(p)
    s = {"problems": probs, "caveats": cav}
    if probs:
        return s
    g = s["liveness"] = liveness(p)
    dc = s["dir"] = dir_check(p["isr"], first_after_reset=p["isr_overwritten"] == 0)
    if g["stop"]:
        s["caveats"].append("liveness gate: STOP: %s" % "; ".join(g["why"]))
    if g["undefined_rows"]:
        s["caveats"].append("%d isr rows with a segment the TRM does not define (seq:code): %s" % (
            len(g["undefined_rows"]), ", ".join("%d:0x%02x" % (q, c)
                                                for q, c in g["undefined_rows"])))
    for m in g["mismatch"]:
        s["caveats"].append("liveness: %s" % m)
    if dc["disagree"]:
        s["caveats"].append("%d isr rows whose dir disagrees with the counter that moved: %s" % (
            len(dc["disagree"]), ", ".join("seq %d (TEC %d REC %d dir %s, expected %s)" % (
                x["seq"], x["tec"], x["rec"], "RX" if x["dir"] else "TX", "RX" if x["expect"] else "TX")
                for x in dc["disagree"])))
    s.update(pulses(p, wit_errs, interval_s, judge_first))
    for k in s.get("pulses", []):
        if k.get("overcount"):
            s["caveats"].append(
                "pulse %d (%s): %d device rows against %d witness error frames; compare them "
                "(an error between the HAL's release and the probe's read is logged under the "
                "earlier interrupt)" % (k["pulse"], k["phase"], k["device_rows"], k["witness_error_frames"]))
    if not s.get("aligned", True):
        s["caveats"].append("device rows not aligned with the witness: %s" % s["why"])
    return s


def fetch(get):
    """The page via get(path) -> text (lowid_flood_skip_bench.dev_get). Returns
    (page or None, text or error)."""
    try:
        txt = get("/gi_ecc")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    try:
        d, _ = json.JSONDecoder().raw_decode(txt.lstrip())
        return d, txt
    except ValueError as e:
        return None, "not JSON: %s" % e
