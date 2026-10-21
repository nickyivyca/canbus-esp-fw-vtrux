#!/usr/bin/env python3
"""Gen-inhibit bench suite: one judge per check, reading only that check's run files.

Gen-inhibit tester, 2026-10-18. The chain runner's core (the charge-interposer
tester's, port plan "Shared bench toolkit" item 8) calls a judge after each check;
this module is gen-inhibit's half. A judge never runs anything: it reads the files
the check wrote and returns a list of results, each

    {"id": "B3", "verdict": "PASS", "figures": {"name": {"value": v, "unit": u}},
     "detail": "one line"}

with the verdict one of VERDICTS. A judge whose files are missing, or which finds
nothing to count, returns ERROR, never PASS: an empty population is not a negative.

Run on a run folder, it judges every check whose files are there and prints one
line per check (the index of that run):

    py -3.14 suite_judges.py <run dir> [--json out.json] [--only B3,B12]

The accepted baseline's index is written by hand from baseline_run_list.md, not by
this module; test_suite_judges.py checks that these judges, run on the e3d9437 run
files, give that index.
"""
import ast
import json
import os
import re
import sys

VERDICTS = ("PASS", "FAIL", "KNOWN-FAIL", "VOID", "ERROR", "NOT-RUN", "INCONCLUSIVE", "NOT-APPLICABLE")
C7 = r"[0-9a-f]{7}"           # a short commit in a file name
TS = r"\d{8}T\d{6}"           # a stamp in a file name


def fig(value, unit=""):
    return {"value": value, "unit": unit}


def result(cid, verdict, figures=None, detail=""):
    assert verdict in VERDICTS, verdict
    return {"id": cid, "verdict": verdict, "figures": figures or {}, "detail": detail}


class Missing(Exception):
    pass


class Files:
    """The run folder, and the files in it matched by full-name regex."""

    def __init__(self, folder):
        self.folder = folder
        self.names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def all(self, pattern):
        rx = re.compile(pattern + r"\Z")
        return [os.path.join(self.folder, n) for n in self.names if rx.match(n)]

    def one(self, pattern):
        got = self.all(pattern)
        if len(got) != 1:
            raise Missing("%d files match %s" % (len(got), pattern))
        return got[0]

    def some(self, pattern, n=None):
        got = self.all(pattern)
        if not got or (n is not None and len(got) != n):
            raise Missing("%d files match %s%s" % (len(got), pattern, "" if n is None else ", want %d" % n))
        return got


def text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def json_lines(path):
    return [json.loads(l) for l in text(path).splitlines() if l.startswith("{")]


def base(path):
    return os.path.basename(path)


JUDGES = {}


def judge(cid, part):
    def reg(fn):
        JUDGES[cid] = (part, fn)
        return fn
    return reg


# ---------------------------------------------------------------- part B

@judge("B1", "B")
def b1(F):
    t = text(F.one(r"B1_slcan_refusal_stdout\.txt"))
    m = re.search(r"^summary (\{.*\})$", t, re.M)
    if not m:
        return [result("B1", "ERROR", detail="no summary line")]
    s = ast.literal_eval(m.group(1))
    lines = re.findall(r"-> (\w+) +\| counted (\w+)", t)
    passed = sum(1 for a, b in lines if a == "PASS" and b == "PASS")
    ok = s["commands"] > 0 and s["fail"] == 0 and passed == s["commands"] == len(lines)
    return [result("B1", "PASS" if ok else "FAIL",
                   {"commands": fig(s["commands"]), "passed": fig(passed), "fail": fig(s["fail"])},
                   "%d of %d refused and counted" % (passed, s["commands"]))]


PROV = re.compile(r"provenance \(live\): (.+?) -- replayed (\d+)/(\d+) found, device 0x051 (\d+) "
                  r"\(device says (\d+)\), late (\d+), double (\d+), ambiguous (\d+)")


def e4_prov(t):
    """The e4 arm's live provenance line, or None."""
    m = PROV.search(t)
    if not m:
        return None
    g = m.groups()
    return {"verdict": g[0], "replayed": int(g[1]), "found": int(g[2]), "dev": int(g[3]),
            "dev_says": int(g[4]), "late": int(g[5]), "double": int(g[6]), "ambiguous": int(g[7])}


def prov_ok(p):
    return (p is not None and p["verdict"] == "as specified" and p["replayed"] == p["found"] > 0
            and p["dev"] == p["dev_says"] and p["late"] == p["double"] == p["ambiguous"] == 0)


@judge("B2", "B")
def b2(F):
    figs, bad = {}, []
    for arm in ("inhibit", "respond", "longarm"):
        for x2 in ("x2app", "x2held"):
            t = text(F.one(r"B2_e4_presence_%s_%s_%s\.txt" % (arm, C7, x2)))
            p = e4_prov(t)
            if not prov_ok(p):
                bad.append("%s %s provenance %s" % (arm, x2, p and p["verdict"]))
            if arm == "respond":
                m = re.search(r"wire agrees with the device: (\d+) transmitted, (\d+) seen \(probe 0x7F0\)", t)
                if not m or m.group(1) != m.group(2) or p is None or p["dev"] != 0:
                    bad.append("respond %s probes" % x2)
                figs["respond_probes_" + x2] = fig(int(m.group(1)) if m else None)
            else:
                figs["%s_0x051_%s" % (arm, x2)] = fig(p["dev"] if p else None)
    rows = re.findall(r"^B2_e4_presence_\S+ frames (\d+)  x2 7F4=(\d+) 7F5=(\d+) 7F6=(\d+) 7F7=(\d+)$",
                      text(F.one(r"B2_x2_presence_count\.txt")), re.M)
    x2 = sum(int(v) for r in rows for v in r[1:])
    if len(rows) != 6 or any(int(r[0]) == 0 for r in rows):
        bad.append("X2 count over %d witness logs" % len(rows))
    figs["x2_frames"] = fig(x2)
    if x2:
        bad.append("X2 sent %d frames" % x2)
    return [result("B2", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "6 arms as specified, X2 frames 0")]


B3_MODES = ("off", "observe", "passive", "respond")


@judge("B3", "B")
def b3(F):
    rows = re.findall(r"^B3_e4_golive_(\w+?)_%s_%s_arm\d\.json +\S+ +dev0x051 +(\d+) +stale (\d+) +(.+?) *$" % (C7, TS),
                      text(F.one(r"B3_golive_stale_check\.txt")), re.M)
    entries = json_lines(F.one(r"B3_mode_entry_analysis\.txt"))
    figs = {"arms": fig(len(rows)), "stale": fig(sum(int(r[2]) for r in rows))}
    bad = [r[0] for r in rows if r[3] != "as specified" or int(r[2]) or not int(r[1])]
    for m in B3_MODES:
        ms = [e["first_answer_after_post_ms"] for e in entries
              if re.match(r"B3_e4_golive_%s_" % m, e["run"])]
        if len(ms) != 5:
            bad.append("%s: %d entries" % (m, len(ms)))
            continue
        figs["first_answer_ms_%s_min" % m] = fig(min(ms), "ms")
        figs["first_answer_ms_%s_max" % m] = fig(max(ms), "ms")
    bad += [e["run"] for e in entries if e.get("provenance") != "as specified" or e.get("post_http") != 200]
    ok = len(rows) == 20 and len(entries) == 20 and not bad
    return [result("B3", "PASS" if ok else "FAIL", figs, "; ".join(bad) or "20 arms, stale 0")]


@judge("B24", "B")
def b24(F):
    rows = re.findall(r"^(B[23]_e4_\S+\.json) +(AS SPECIFIED|NO DEVICE FRAMES|[A-Z][A-Z ]+?) +device 0x051",
                      text(F.one(r"B24_frame_content_%s\.txt" % C7)), re.M)
    spec = sum(1 for _, v in rows if v == "AS SPECIFIED")
    none = [n for n, v in rows if v == "NO DEVICE FRAMES"]
    other = [n for n, v in rows if v not in ("AS SPECIFIED", "NO DEVICE FRAMES")]
    # The RESPOND arm sends no 0x051 by design; any other arm without device frames judges nothing.
    bad = other + [n for n in none if "_respond_" not in n]
    ok = spec > 0 and not bad
    return [result("B24", "PASS" if ok else "FAIL",
                   {"as_specified": fig(spec), "no_device_frames": fig(len(none)), "other": fig(len(other))},
                   "; ".join(bad) or "%d arms as specified, %d RESPOND arm(s) with no device frames" % (spec, len(none)))]


@judge("B4", "B")
def b4(F):
    t = text(F.one(r"B4_skip_method_stdout\.txt"))
    rows = re.findall(r"^(\S+) +(PASS|FAIL|INCONCLUSIVE|ERROR)\b +witness", t, re.M)
    passed = sum(1 for _, v in rows if v == "PASS")
    ok = rows and passed == len(rows) and "player sent" in t
    return [result("B4", "PASS" if ok else "FAIL", {"cases": fig(len(rows)), "passed": fig(passed)},
                   "%d of %d PASS" % (passed, len(rows)))]


def jam_arms(t):
    return re.findall(r"^arm (\d+) \(replay [\d.]+/s\): (.+)$", t, re.M)


def livetrain_arm(v):
    if v.startswith("INCONCLUSIVE"):
        return "INCONCLUSIVE"
    return "PASS" if re.search(r"\| A: PASS\b.*\| B: PASS\b", v) else "FAIL"


@judge("B5", "B")
def b5(F):
    figs, bad = {}, []
    for rate, pat in (("200", r"B5_livetrain_p5000\.txt"), ("500", r"B5_livetrain_p2000_run\d\.txt")):
        arms = [livetrain_arm(v) for f in F.some(pat) for _, v in jam_arms(text(f))]
        figs["arms_%s_per_s" % rate] = fig(len(arms))
        figs["pass_%s_per_s" % rate] = fig(arms.count("PASS"))
        figs["inconclusive_%s_per_s" % rate] = fig(arms.count("INCONCLUSIVE"))
        if not arms or "FAIL" in arms:
            bad.append("%s/s: %s" % (rate, arms))
    return [result("B5", "FAIL" if bad else "PASS", figs, "; ".join(bad) or
                   "200/s %d of %d, 500/s %d of %d, %d inconclusive" % (
                       figs["pass_200_per_s"]["value"], figs["arms_200_per_s"]["value"],
                       figs["pass_500_per_s"]["value"], figs["arms_500_per_s"]["value"],
                       figs["inconclusive_500_per_s"]["value"] + figs["inconclusive_200_per_s"]["value"]))]


@judge("B6", "B")
def b6(F):
    files = F.some(r"B6_threshold_n\d+_lead[\d.]+\.txt")
    arms = [v.split()[0] for f in files for _, v in jam_arms(text(f))]
    verdicts = [re.search(r"^verdict (\S+)", text(f), re.M) for f in files]
    ok = arms and all(a == "PASS" for a in arms) and all(v and v.group(1) == "PASS" for v in verdicts)
    return [result("B6", "PASS" if ok else "FAIL", {"arms": fig(len(arms)), "passed": fig(arms.count("PASS"))},
                   "%d of %d arms PASS over %d runs" % (arms.count("PASS"), len(arms), len(files)))]


@judge("B7", "B")
def b7(F):
    t = text(F.one(r"B7_busoff_rearm\.txt"))
    arms = re.findall(r"^arm \d+: (.+?):", t, re.M)
    v = re.search(r"^verdict (.+?) *$", t, re.M)
    ok = arms and all(a == "AS SPECIFIED" for a in arms) and v and v.group(1) == "AS SPECIFIED"
    return [result("B7", "PASS" if ok else "FAIL", {"arms": fig(len(arms)), "as_specified": fig(arms.count("AS SPECIFIED"))},
                   "%d of %d arms AS SPECIFIED" % (arms.count("AS SPECIFIED"), len(arms)))]


@judge("B8", "B")
def b8(F):
    t = text(F.one(r"B8_busoff\.txt"))
    arms = re.findall(r"^arm \d+ train=(True|False): (\w+)", t, re.M)
    train = [v for tr, v in arms if tr == "True"]
    control = [v for tr, v in arms if tr == "False"]
    v = re.search(r"^verdict (\S+)", t, re.M)
    # The control arm sends no train, so no bus-off is reached and it reads INCONCLUSIVE by design.
    ok = train and all(a == "PASS" for a in train) and v and v.group(1) == "PASS" and all(c != "FAIL" for c in control)
    return [result("B8", "PASS" if ok else "FAIL",
                   {"train_arms": fig(len(train)), "train_passed": fig(train.count("PASS")),
                    "control_arms": fig(len(control)), "control_inconclusive": fig(control.count("INCONCLUSIVE"))},
                   "train %d of %d PASS; control %s" % (train.count("PASS"), len(train), control))]


@judge("B9", "B")
def b9(F):
    t = text(F.one(r"B9_wrong_bitrate_calibrate\.txt"))
    bursts = [int(n) for n in re.findall(r"^  burst at [\d.]+ s, \d+ ms -> witness error frames (\d+)$", t, re.M)]
    m = re.search(r"^summary (\{.*\}) witness total (\d+)", t, re.M)
    if not m or not bursts:
        return [result("B9", "ERROR", detail="no summary or no bursts")]
    s = ast.literal_eval(m.group(1))
    latched = "error-frame rate exceeded" in s["aborts"]
    ok = latched and min(bursts) > 0
    return [result("B9", "PASS" if ok else "FAIL",
                   {"bursts": fig(len(bursts)), "burst_errors_min": fig(min(bursts)), "burst_errors_max": fig(max(bursts)),
                    "witness_error_frames": fig(int(m.group(2))), "tec_max": fig(s["tec_max"]),
                    "trip6_latched": fig(int(latched))},
                   "%d bursts, %d witness error frames, TEC max %d, trip 6 %s" % (
                       len(bursts), int(m.group(2)), s["tec_max"], "latched" if latched else "NOT latched"))]


@judge("B10", "B")
def b10(F):
    t = text(F.one(r"B10_trip6_rearm_check\.txt"))
    # The flood arms are the stimulus and carry no verdict; every other arm ends with its own.
    arms = re.findall(r"^(A\d+) (\w+) +armed .*? aborts \[[^]]*\] *(.*?) *$", t, re.M)
    judged = [(a, k, v) for a, k, v in arms if k != "flood"]
    hist = re.findall(r"^ +history (.+?) \| prediction (.+?) *$", t, re.M)
    spec = sum(1 for *_, v in judged if v == "as specified")
    ok = (judged and spec == len(judged) and len(hist) == len(judged)
          and all(h == p == "as specified" for h, p in hist))
    return [result("B10", "PASS" if ok else "FAIL",
                   {"judged_arms": fig(len(judged)), "flood_arms": fig(len(arms) - len(judged))},
                   "%d of %d judged arms as specified" % (spec, len(judged)))]


@judge("B11", "B")
def b11(F):
    rows = json_lines(F.one(r"B11_quiesce_analysis\.txt"))
    bad = [r["run"] for r in rows if r.get("provenance") != "as specified" or r.get("late") or r.get("double")
           or (r["initial_mode"] == "inhibit" and not (r.get("tx_ok_vs_last_inhibit_span") or {}).get("agree"))]
    ok = len(rows) == 6 and not bad
    return [result("B11", "PASS" if ok else "FAIL", {"arms": fig(len(rows))},
                   "; ".join(bad) or "6 arms as specified, tx_ok agrees with every INHIBIT span")]


@judge("B11raw", "B")
def b11raw(F):
    t = text(F.one(r"B11raw_rawpost_judge\.txt"))
    posts = [json.loads(l.strip()) for l in t.splitlines() if l.strip().startswith('{"at_s"')]
    verdicts = [p["verdict"] for p in posts if "verdict" in p]
    # "strict FAIL, PASS on ACK" passes (reviewer, 2026-10-17: the bound is the device's ACK);
    # "strict FAIL" alone fails.
    passing = [v for v in verdicts if v in ("PASS", "strict FAIL, PASS on ACK")]
    na = [v for v in verdicts if v.startswith("NOT APPLICABLE")]
    fails = [v for v in verdicts if v not in passing and v not in na]
    sends = [p["send_to_first_byte_ms"] for p in posts]
    others = sum(len(p.get("other_requests_in_progress") or []) for p in posts)
    prov = json_lines(F.one(r"B11raw_quiesce_analysis\.txt"))
    pbad = [r["run"] for r in prov if r.get("provenance") != "as specified"]
    ok = len(verdicts) == 6 and not fails and not pbad and others == 0
    return [result("B11raw", "PASS" if ok else "FAIL",
                   {"observe_posts": fig(len(verdicts)), "pass": fig(len(passing)), "not_applicable": fig(len(na)),
                    "pass_on_ack": fig(verdicts.count("strict FAIL, PASS on ACK")),
                    "send_to_first_byte_ms_min": fig(min(sends) if sends else None, "ms"),
                    "send_to_first_byte_ms_max": fig(max(sends) if sends else None, "ms"),
                    "other_requests_in_progress": fig(others)},
                   "; ".join(fails + pbad) or "%d PASS (%d on ACK), %d not applicable" % (
                       len(passing), verdicts.count("strict FAIL, PASS on ACK"), len(na)))]


@judge("B12", "B")
def b12(F):
    sw, panic, bad = [], [], []
    files = F.some(r"B12_\d_(live|off)_(reboot|crash)\.txt", 7)
    for f in files:
        restart = re.search(r"_(reboot|crash)\.txt$", f).group(1)
        m = re.search(r"^cycle 0 \w+: hook \S+, reset \['(\w+)'\], build \S+, live_at_post \S+, boot ([\d.]+) s after POST, errors (\d+)",
                      text(f), re.M)
        if not m:
            bad.append(base(f) + " no cycle line")
            continue
        want = "SW" if restart == "reboot" else "PANIC"
        (sw if want == "SW" else panic).append(float(m.group(2)))
        if m.group(1) != want or int(m.group(3)):
            bad.append("%s reset %s errors %s" % (base(f), m.group(1), m.group(3)))
    figs = {"cycles": fig(len(sw) + len(panic)), "error_frames": fig(0 if not bad else None)}
    if sw:
        figs.update(sw_boot_s_min=fig(min(sw), "s"), sw_boot_s_max=fig(max(sw), "s"))
    if panic:
        figs.update(panic_boot_s_min=fig(min(panic), "s"), panic_boot_s_max=fig(max(panic), "s"))
    return [result("B12", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "7 of 7 cycles, 0 error frames")]


@judge("B13", "B")
def b13(F):
    rows = json_lines(F.one(r"B13_passive_analysis\.txt"))
    bad = [r["run"] for r in rows if r.get("provenance") != "as specified"
           or (r["mode"] == "passive" and r["device_0x051"] != 0)
           or (r["mode"] == "inhibit" and r["device_0x051"] == 0)]
    book = re.findall(r"^\[(\w+) *\] ", text(F.one(r"B13_respond_booking_check\.txt")), re.M)
    bfail = [b for b in book if b != "PASS"]
    ok = len(rows) == 8 and not bad and book and not bfail
    return [result("B13", "PASS" if ok else "FAIL", {"arms": fig(len(rows)), "booking_rows": fig(len(book)),
                                                     "booking_fail": fig(len(bfail))},
                   "; ".join(bad) or "%d arms ok, booking %s" % (len(rows), "PASS" if not bfail else bfail))]


@judge("B14", "B")
def b14(F):
    t = text(F.one(r"B14_observe_ack_probe\.txt"))
    modes = re.findall(r"^  mode \d: ack errors \w+ -> (.+?) *$", t, re.M)
    v = re.search(r"^verdict: (.+?); mode at end (\d)", t, re.M)
    ok = modes and all(m == "as specified" for m in modes) and v and v.group(1) == "as specified" and v.group(2) == "0"
    return [result("B14", "PASS" if ok else "FAIL", {"mode_checks": fig(len(modes))},
                   v.group(0) if v else "no verdict line")]


@judge("NONODE", "B")
def nonode(F):
    # spec 12.4 "a bus with no other node" (nonode_bench.py, added 2026-10-20): the record's
    # own run_verdict, and a PASS only if every main and answer-stuck arm's items read PASS
    rec = json.loads(text(F.one(r"nonode_bench_%s\.json" % TS)))
    text(F.one(r"NONODE_nonode_bench\.txt"))
    v, arms = rec.get("verdict", ""), rec.get("arms", [])
    judged_arms = [a for a in arms if a.get("kind") in ("main", "answer-stuck")]
    mains = [a for a in judged_arms if a["kind"] == "main"]
    lat = [re.match(r"PASS: latched by ([\d.]+) s", a["verdicts"].get("latch", "")) for a in mains]
    figs = {"main_arms": fig(len(mains)), "answer_stuck_arms": fig(len(judged_arms) - len(mains))}
    if mains and all(lat):
        figs["latch_max_s"] = fig(max(float(m.group(1)) for m in lat), "s")
    trips = sorted({a["recorded"]["trip"] for a in mains})
    detail = "%s; trips %s" % (v, trips) + ("; release reboot not confirmed: %s" % rec["release_not_confirmed"]
                                            if rec.get("release_not_confirmed") else "")
    if not rec.get("termination"):
        return [result("NONODE", "ERROR", figs, "no termination recorded")]
    off = ["%s %s" % (a.get("n"), k) for a in judged_arms for k, x in a["verdicts"].items()
           if k not in ("answer",) and not x.startswith("PASS")]
    if v == "PASS" and (off or len(mains) < 3):
        return [result("NONODE", "ERROR", figs, "the record says PASS, but: %s" % (off or "%d main arms" % len(mains)))]
    verdict = ("PASS" if v == "PASS" else "FAIL" if v.startswith("NOT AS SPECIFIED") else
               "VOID" if v.startswith("VOID") else "INCONCLUSIVE" if v.startswith(("INCONCLUSIVE", "FLAKY")) else "ERROR")
    return [result("NONODE", verdict, figs, detail)]


@judge("B15", "B")
def b15(F):
    t = text(F.one(r"B15_wican_segment_probe\.txt"))
    adapters = re.findall(r"^--- (\S+) .*?: (\d+) frames, ids (\{.*\})$", t, re.M)
    figs, bad, repeats = {}, [], set()
    for name, n, ids in adapters:
        ids = ast.literal_eval(ids)
        figs["frames_" + name] = fig(int(n))
        if not set(ids) & {"0x7F1", "0x7F2", "0x7F3", "0x7F8", "0x7F9"}:
            bad.append(name + " sees no WiCAN diag page")
        if len(ids) == 1:
            repeats |= set(ids)
    figs["non_ack_repeat_id"] = fig(",".join(sorted(repeats)))
    restored = re.search(r"^restored: \{'mode': 0\b", t, re.M)
    if not restored:
        bad.append("mode 0 not restored")
    ok = adapters and not bad
    return [result("B15", "PASS" if ok else "FAIL", figs,
                   "; ".join(bad) or "%d adapters on the segment; non-ACK repeat on %s" % (
                       len(adapters), figs["non_ack_repeat_id"]["value"]))]


@judge("B15host", "H")
def b15host(F):
    # B15's host half: every diag frame the host build emits decodes against the DBC
    # (diag_decode_check.py on the host tree; the emulated suite).
    host = text(F.one(r"B15_diag_decode_check_host_%s\.txt" % C7))
    pages = re.findall(r"pages seen: (.+)$", host, re.M)
    decodes = "all emitted diag frames decode against the DBC" in host and re.search(r"^exit 0$", host, re.M)
    return [result("B15host", "PASS" if decodes and pages else "FAIL", {"modes_with_pages": fig(len(pages))},
                   "every emitted diag frame decodes against the DBC" if decodes else "a diag frame does not decode")]


@judge("B16", "B")
def b16(F):
    t = text(F.one(r"B16_page_only_baseline\.txt"))
    m = re.search(r"^polls (\d+), unanswered (\d+), .*outages (\d+) -> (.+?) *$", t, re.M)
    ok = m and int(m.group(1)) > 0 and m.group(4) == "no outage" and "uptime: no reboot" in t
    return [result("B16", "PASS" if ok else "FAIL",
                   {"polls": fig(int(m.group(1)) if m else None), "outages": fig(int(m.group(3)) if m else None)},
                   m.group(0) if m else "no polls line")]


def spec11(F, cid, pattern):
    t = text(F.one(pattern))
    first = t.strip().splitlines()[0] if t.strip() else ""
    m = re.search(r"^  page  ([0-9a-f]{64})$", t, re.M)
    i = re.search(r"^  image ([0-9a-f]{64})$", t, re.M)
    ok = first == "AS SPECIFIED" and m and i and m.group(1) == i.group(1)
    return [result(cid, "PASS" if ok else "FAIL", {"page_elf_sha256": fig(m.group(1)[:8] if m else None)}, first)]


@judge("B17a", "B")
def b17a(F):
    return spec11(F, "B17a", r"B17_spec11_page_%s_man\.txt" % C7)


@judge("B17b", "B")
def b17b(F):
    return spec11(F, "B17b", r"B17_spec11_page_%s_man2\.txt" % C7)


@judge("B18", "B")
def b18(F):
    t = text(F.one(r"B18_diag_build_page_check\.txt"))
    w = re.search(r"diag_git_hash on the wire +(0x[0-9A-F]+)", t)
    e = re.search(r"expected from \S+ +(0x[0-9A-F]+)", t)
    ok = w and e and w.group(1) == e.group(1) and "MATCH --" in t
    return [result("B18", "PASS" if ok else "FAIL", {"diag_git_hash": fig(w.group(1) if w else None)},
                   "MATCH" if ok else "no match")]


def inert_rows(t):
    return re.findall(r"^(GI_\w+|OTA_\w+) +(PASS|FAIL) +(.+?) *$", t, re.M)


@judge("B19present", "B")
def b19present(F):
    figs, bad = {}, []
    for tag, switch in (("rt", "GI_TEST_RESTART_QUIESCE"), ("txabort", "GI_INSTRUMENT_TXABORT"),
                        ("abortwin", "GI_INSTRUMENT_ABORTWIN"), ("rxq", "GI_INSTRUMENT_RXQ")):
        rows = inert_rows(text(F.one(r"B19_present_%s\.txt" % tag)))
        v = [r[1] for r in rows if r[0] == switch]
        figs[switch] = fig(v[0] if len(v) == 1 else None)
        if v != ["PASS"]:
            bad.append("%s %s" % (switch, v))
    return [result("B19present", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "all four present on their test builds")]


@judge("B19absent", "B")
def b19absent(F):
    # GI_INSTRUMENT_RXQ is judged from its own run 30 s after the QUIESCE probe (reviewer, 2026-10-17);
    # straight after it, it read undetermined twice on e3d9437.
    want = (("GI_TEST_RESTART_QUIESCE", r"B19_absent_prod\.txt"), ("GI_INSTRUMENT_ABORTWIN", r"B19_absent_prod\.txt"),
            ("OTA_HEALTH_FAULT_INJECT", r"B19_absent_prod\.txt"), ("GI_INSTRUMENT_RXQ", r"B19_absent_prod_rxq_gap30\.txt"),
            ("GI_INSTRUMENT_TXABORT", r"B19_absent_prod_txabort\.txt"))
    figs, bad = {}, []
    for switch, pat in want:
        v = [r[1] for r in inert_rows(text(F.one(pat))) if r[0] == switch]
        figs[switch] = fig(v[0] if len(v) == 1 else None)
        if v != ["PASS"]:
            bad.append("%s %s" % (switch, v))
    return [result("B19absent", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "all five absent on prod")]


@judge("B19page", "B")
def b19page(F):
    return spec11(F, "B19page", r"B19_spec11_page_prod\.txt")


@judge("B20", "B")
def b20(F):
    t = text(F.one(r"B20_interlock_bench_run\.txt"))
    cases = re.findall(r"^(\S+) +(AS SPECIFIED|NOT AS SPECIFIED|[A-Z][A-Z ]+?) +\(interlock_\S+\.json\)$", t, re.M)
    spec = sum(1 for _, v in cases if v == "AS SPECIFIED")
    ok = cases and spec == len(cases)
    return [result("B20", "PASS" if ok else "FAIL", {"cases": fig(len(cases)), "as_specified": fig(spec)},
                   "%d of %d AS SPECIFIED" % (spec, len(cases)))]


@judge("B21", "B")
def b21(F):
    bad, figs = [], {}
    if "spec 5.1 item 3 holds on the device" not in text(F.one(r"B21_bench_flash_guard\.txt")):
        bad.append("flash guard")
    if "the OTA guard holds in every non-OFF mode" not in text(F.one(r"B21_bench_ota_guard\.txt")):
        bad.append("OTA guard")
    for kind in ("multipart", "image"):
        reps = re.findall(r"^\w+ +rep \d+ .*? wire (\d+) .*? device_end (\w+) .*? (consistent|\S+) *$",
                          text(F.one(r"B21_wire_%s_%s\.txt" % (kind, C7))), re.M)
        got = sum(1 for w, _, _ in reps if w == "403")
        rst = sum(1 for _, e, _ in reps if e == "rst")
        figs[kind + "_403"] = fig(got)
        figs[kind + "_reps"] = fig(len(reps))
        figs[kind + "_rst"] = fig(rst)
        if not reps or got != len(reps) or rst or any(c != "consistent" for *_, c in reps):
            bad.append("%s %d of %d 403, %d RST" % (kind, got, len(reps), rst))
    return [result("B21", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "both guards hold; raw probe 403 without RST")]


def e4_response(t):
    m = re.search(r"device response us: count=(\d+) min=(\d+) mean=(\d+) max=(\d+)", t)
    return tuple(int(v) for v in m.groups()) if m else None


@judge("B22", "B")
def b22(F):
    figs, bad = {}, []
    for arm in ("a", "b"):
        t = text(F.one(r"B22_case3_%s_man_%s\.txt" % (C7, arm)))
        p = e4_prov(t)
        r = e4_response(t)
        w = re.search(r"wire agrees with the device: (\d+) transmitted, (\d+) seen \(zero-torque 0x051\)", t)
        d = re.search(r"device: tx_ok=(\d+) tx_fail=(\d+) ctr_ok=\d+ ctr_bad=(\d+) tx_queued_behind=(\d+)", t)
        e = re.search(r"witness saw \d+ frames \([\d.]+/s\), (\d+) error frames", t)
        ld = re.search(r"carried load >= ([\d.]+)%", t)
        if not (p and r and w and d and e and ld):
            bad.append(arm + ": a line is missing")
            continue
        if not prov_ok(p) or w.group(1) != w.group(2) or int(d.group(2)) or int(d.group(3)) or int(d.group(4)) or int(e.group(1)):
            bad.append(arm + " not as before")
        figs.update({"ours_%s" % arm: fig(int(w.group(1))), "response_us_max_%s" % arm: fig(r[3], "us"),
                     "response_us_mean_%s" % arm: fig(r[2], "us"), "tx_fail_%s" % arm: fig(int(d.group(2))),
                     "ctr_bad_%s" % arm: fig(int(d.group(3))), "error_frames_%s" % arm: fig(int(e.group(1))),
                     "load_pct_lower_%s" % arm: fig(float(ld.group(1)), "%")})
    return [result("B22", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "both arms: provenance clean, 0 tx_fail, 0 error frames")]


B23_OUTCOMES = (
    ("control", "abort on empty buffer", "empty"),
    ("noabort", "completed (no abort)", "completed"),
    ("idle", "transmitting, abort was a no-op, completed", "noop_completed"),
    ("idle", "buffer already free, frame had gone", "already_free"),
    ("held", "awaiting, aborted, not on the wire", "aborted"),
    ("contend", "awaiting, aborted, not on the wire", "aborted"),
    ("contend", "ABORT HAD NO EFFECT: still in the buffer, no completion, not on the wire", "no_effect"),
    ("contend", "in arbitration, lost, then aborted out; reported as success", "lost_aborted_out"),
    ("retry", "awaiting, aborted, not on the wire", "aborted"),
    ("retry", "in arbitration, lost, then aborted out; reported as success", "lost_aborted_out"),
)


@judge("B23", "B")
def b23(F):
    t = text(F.one(r"B23_txabort_bench\.txt"))
    blocks = dict(re.findall(r"^== (\w+)\n(.*?)(?=^== |\Z)", t, re.M | re.S))
    figs = {}
    for phase, label, name in B23_OUTCOMES:
        m = re.search(r"^ +(\d+)  %s$" % re.escape(label), blocks.get(phase, ""), re.M)
        figs["%s_%s" % (phase, name)] = fig(int(m.group(1)) if m else 0)
    free = re.search(r"RETRY LOOP: (\d+) of (\d+) trials reached a free buffer", t)
    first = re.search(r"the FIRST abort was enough on (\d+) of (\d+)", t)
    wire = re.search(r"the frame reached the wire anyway on (\d+) of (\d+) trials", t)
    mono = re.search(r"over \d+ phases, monotonic: (\w+)", t)
    if not (free and first and wire and mono and blocks):
        return [result("B23", "ERROR", figs, "a summary line is missing")]
    figs.update(retry_free=fig(int(free.group(1))), retry_trials=fig(int(free.group(2))),
                retry_first_abort_enough=fig(int(first.group(1))), retry_on_wire=fig(int(wire.group(1))),
                stop_flag_printed=fig(int("This is a STOP condition" in t)))
    ok = free.group(1) == free.group(2) and wire.group(1) == "0" and mono.group(1) == "True"
    return [result("B23", "PASS" if ok else "FAIL", figs,
                   "contend %d aborted / %d no effect / %d lost-then-aborted; retry %s of %s free, %s on the wire" % (
                       figs["contend_aborted"]["value"], figs["contend_no_effect"]["value"],
                       figs["contend_lost_aborted_out"]["value"], free.group(1), free.group(2), wire.group(1)))]


@judge("B25", "B")
def b25(F):
    t = text(F.one(r"B25_keystate_rekey-long_%s\.txt" % C7))
    v = re.search(r"^== \S+: (.+?) *$", t, re.M)
    rules = re.findall(r"^  (ok|FAIL|\w+) +(rule|decision|re-entry|armed)", t, re.M)
    r1 = re.search(r"rule 1: .*?  (\d+) of (\d+)", t)
    k = re.search(r"'gate_open': ([\d.]+), 'resumed': ([\d.]+)", t)
    ok = v and v.group(1) == "AS SPECIFIED" and rules and all(r[0] == "ok" for r in rules)
    return [result("B25", "PASS" if ok else "FAIL",
                   {"rule1_violations": fig(int(r1.group(1)) if r1 else None),
                    "rule1_frames": fig(int(r1.group(2)) if r1 else None),
                    "gate_open_s": fig(float(k.group(1)) if k else None, "s"),
                    "resumed_s": fig(float(k.group(2)) if k else None, "s")},
                   v.group(1) if v else "no verdict line")]


@judge("B26", "B")
def b26(F):
    figs, bad = {}, []
    for arm in ("idle_a", "idle_b", "loaded"):
        d = json.load(open(F.one(r"B26_rxq_%s\.json" % arm), encoding="utf-8"))
        rxq = ((d.get("arms") or {}).get("idle" if arm != "loaded" else "loaded") or {}).get("device", {}).get("rxq")
        if not rxq or not rxq.get("samples"):
            bad.append(arm + ": no rxq samples")
            continue
        figs["max_backlog_" + arm] = fig(rxq["max"])
        figs["ge2_" + arm] = fig(dict(zip(rxq["ge_edges"], rxq["ge"])).get(2))
        figs["samples_" + arm] = fig(rxq["samples"])
    return [result("B26", "ERROR" if bad else "PASS", figs, "; ".join(bad) or "max backlog %s / %s / %s" % tuple(
        figs["max_backlog_" + a]["value"] for a in ("idle_a", "idle_b", "loaded")))]


# ---------------------------------------------------------------- part H

H_STEPS = ("make", "make_scenarios", "replays_regen", "run_tests", "test_signals", "invariants", "passive_diff",
           "fuzz_sequences", "load_margin", "spec_expect_selftest", "spec_expect_check", "bus_on_check",
           "drained_selftest", "drained_check", "longarm_fixture", "fixture_size", "shim_E1", "shim_asan",
           "shim_autoarm", "shim_autoarm_asan", "sched", "sched_asan", "config_vs_spec")


@judge("H", "H")
def h(F):
    t = text(F.one(r"host_run\.log"))
    exits = dict((n, int(c)) for c, n in re.findall(r"^=== exit (\d+) \((\w+)\)$", t, re.M))
    missing = [s for s in H_STEPS if s not in exits]
    nonzero = [s for s, c in exits.items() if c]
    rt = re.findall(r"^(\d+) passed, (\d+) failed, (\d+) without goldens$", t, re.M)
    inv = re.search(r"^(\d+) scenarios hold, (\d+) violate; (\d+) inhibit frames checked$", t, re.M)
    pdiff = re.search(r"^(\d+) identical, (\d+) differing, (\d+) skipped", t, re.M)
    fuzz = re.search(r"^(\d+) sequences checked: .* (\d+) with violations$", t, re.M)
    spec = re.search(r"^(\d+) of (\d+) rows pass, (\d+) fail", t, re.M)
    case26 = re.search(r"case 26 bound: .* -> (\d+) against cap (\d+)", t)
    fx = re.search(r"^fixture sha256: ([0-9a-f]{64})$", t, re.M)
    figs = {
        "steps": fig(len(exits)),
        "golden_passed": fig(int(rt[-1][0]) if rt else None), "golden_failed": fig(int(rt[-1][1]) if rt else None),
        "without_goldens": fig(int(rt[-1][2]) if rt else None),
        "replays_identical": fig(t.count("BEHAVIOUR: identical to the golden")),
        "invariant_violations": fig(int(inv.group(2)) if inv else None),
        "passive_differing": fig(int(pdiff.group(2)) if pdiff else None),
        "fuzz_violations": fig(int(fuzz.group(2)) if fuzz else None),
        "spec_expect_failed": fig(int(spec.group(3)) if spec else None),
        "case26_bound": fig(int(case26.group(1)) if case26 else None),
        "fixture_sha256": fig(fx.group(1)[:8] if fx else None),
    }
    bad = ["missing step " + s for s in missing] + ["exit != 0: " + s for s in nonzero]
    for name in ("golden_failed", "invariant_violations", "passive_differing", "fuzz_violations", "spec_expect_failed"):
        if figs[name]["value"] != 0:
            bad.append("%s %s" % (name, figs[name]["value"]))
    if case26 and int(case26.group(1)) > int(case26.group(2)):
        bad.append("case 26 over its cap")
    return [result("H", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "%d steps exit 0" % len(exits))]


# ---------------------------------------------------------------- part C

def ok_pass(cid, path, want_steps=None):
    t = text(path)
    steps = re.findall(r"^  (ok|FAIL|\w+) +", t, re.M)
    passed = re.search(r"^PASS\b", t, re.M) is not None
    ok = passed and steps and all(s == "ok" for s in steps) and (want_steps is None or len(steps) == want_steps)
    return result(cid, "PASS" if ok else "FAIL", {"steps": fig(len(steps)), "ok": fig(steps.count("ok"))},
                  "%d of %d steps ok" % (steps.count("ok"), len(steps)))


@judge("C1C7", "C")
def c1c7(F):
    p = F.one(r"tester_rows_%s\.txt" % C7)
    r = ok_pass("C1C7", p)
    images = re.findall(r"^== (\w+) \(checks from ", text(p), re.M)
    r["figures"]["images"] = fig(",".join(images))
    if images != ["man", "prod"]:
        r["verdict"] = "FAIL"
        r["detail"] += "; images %s, want man and prod" % images
    return [r]


@judge("C3", "C")
def c3(F):
    t = text(F.one(r"test_refusal_pairs_%s\.txt" % C7))
    m = re.search(r"^SUMMARY: positive (\w+); (\d+) of (\d+) negatives caught on their own check$", t, re.M)
    ok = m and m.group(1) == "PASS" and m.group(2) == m.group(3)
    return [result("C3", "PASS" if ok else "FAIL",
                   {"negatives": fig(int(m.group(3)) if m else None), "caught": fig(int(m.group(2)) if m else None)},
                   m.group(0) if m else "no SUMMARY line")]


@judge("C5", "C")
def c5(F):
    return [ok_pass("C5", F.one(r"stale_name_check_%s\.txt" % C7))]


@judge("C6", "C")
def c6(F):
    return [ok_pass("C6", F.one(r"name_once_check_%s\.txt" % C7))]


@judge("C2scan", "C")
def c2scan(F):
    return [ok_pass("C2scan", F.one(r"scan_scope_check_%s\.txt" % C7))]


# ---------------------------------------------------------------- running

def judge_one(cid, folder):
    part, fn = JUDGES[cid]
    try:
        out = fn(Files(folder))
    except Missing as e:
        return [result(cid, "ERROR", detail="files: %s" % e)]
    except Exception as e:          # a judge that breaks says so; it never reads as a verdict
        return [result(cid, "ERROR", detail="judge raised %s: %s" % (type(e).__name__, e))]
    return out or [result(cid, "ERROR", detail="the judge returned nothing")]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folders", nargs="+", help="run folders; each check is judged in the first that has its files")
    ap.add_argument("--only", help="comma-separated check ids")
    ap.add_argument("--json", help="write the results here (refuses to overwrite)")
    a = ap.parse_args(argv)
    ids = a.only.split(",") if a.only else list(JUDGES)
    out = []
    for cid in ids:
        if cid not in JUDGES:
            print("unknown check %s" % cid)
            return 2
        res = None
        for d in a.folders:
            res = judge_one(cid, d)
            if not (len(res) == 1 and res[0]["verdict"] == "ERROR" and res[0]["detail"].startswith("files:")):
                break
        out += res
    for r in out:
        print("%-11s %-14s %s" % (r["id"], r["verdict"], r["detail"]))
    if a.json:
        with open(a.json, "x", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
