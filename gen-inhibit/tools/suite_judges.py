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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench"))
import arm_void as AV  # noqa: E402  spec 12.1's void reasons (2026-11-19)
import arm_rulings as AR  # noqa: E402  the reviewer's rulings on reasons on no 12.1 entry (2026-11-19)


def ruled_verdict(arms):
    """One verdict over arm verdicts (PASS, FAIL, ERROR, VOID, INCONCLUSIVE): a FAIL in an arm
    read fairly stands, then a reason the record can't tell (ERROR, for the gen-inhibit
    reviewer), then a void arm, then an unjudged one; PASS only when every arm passes."""
    for v in ("FAIL", "ERROR", "VOID", "INCONCLUSIVE"):
        if v in arms:
            return v
    return "PASS"

VERDICTS = ("PASS", "FAIL", "KNOWN-FAIL", "VOID", "ERROR", "NOT-RUN", "INCONCLUSIVE", "NOT-APPLICABLE")
# A short commit in a file name, at whatever length git abbreviates it: 7 until the repo
# grew, 8 by port step 1d (2026-11-05: 7f2d5032, and 5f2fe2c became 5f2fe2c4).
C7 = r"[0-9a-f]{7,40}"
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


# Where a host half ran: the first line of H's host_run.log and of B15host's output file
# since 2026-11-12 (host_target.sh; gen-inhibit reviewer: "the run record shows local or
# remote without anyone opening host_run.log"). The two judges keep it as the figure
# host_mode, "local" or "remote", and the line itself at the end of their detail. Only
# the first line counts, so a line the remote prints later can't stand in for it. A file
# from before has none: host_mode is None and the detail says so; the verdict doesn't
# turn on it.
HOST_MODE_RX = re.compile(r"# (local mode|remote): .+")


def host_mode(t):
    first = t.split("\n", 1)[0]          # text() reads with universal newlines: no CR left
    m = HOST_MODE_RX.fullmatch(first)
    if not m:
        return None, "host mode not recorded"
    return ("local" if m.group(1) == "local mode" else "remote"), first[2:]


JUDGES = {}

# Index ids retired by a spec change: no suite runs or judges them, and a run against the
# baseline reports them as retired, never compared (reviewer, 2026-10-25, step 0c Q3).
RETIRED = {
    "C5": "spec 12.4 'no stale image name', retired at port step 0c (user, 2026-10-25)",
    "C6": "spec 12.4 'the name enters the build once', retired at port step 0c (user, 2026-10-25)",
}


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
    lines = re.findall(r"-> (\w+)[^|\n]*\| counted (\w+)", t)    # a FAIL or ERROR line carries its reasons
    passed = sum(1 for a, b in lines if a == "PASS" and b == "PASS")
    # A command whose send raised is ERROR, and an arm not run is a NOT-RUN line; B1 never
    # reads PASS with either (gen-inhibit reviewer, 2026-11-19: run cb08 judged the ws arm's
    # ModuleNotFoundError "-> PASS"). Summaries from before then carry neither key.
    errors = sum(1 for a, b in lines if "ERROR" in (a, b))
    not_run = re.findall(r"^  (\w+) +(\w+) +NOT-RUN (.+?) *$", t, re.M)
    figs = {"commands": fig(s["commands"]), "passed": fig(passed), "fail": fig(s["fail"])}
    detail = "%d of %d refused and counted" % (passed, s["commands"])
    if not_run:
        detail += "; NOT-RUN: " + ", ".join("%s/%s %s" % g for g in not_run)
    if errors or s.get("error"):
        return [result("B1", "ERROR", figs, "%d command(s) raised; %s" % (max(errors, s.get("error") or 0), detail))]
    if len(not_run) != len(s.get("not_run") or []):
        return [result("B1", "ERROR", figs, "NOT-RUN lines and the summary disagree; " + detail)]
    # A command read by a witness whose loop died is VOID where it would have
    # passed (slcan_refusal_probe.command_result, 2026-11-19); with no FAIL, B1 is VOID, never PASS.
    voids = sum(1 for a, _b in lines if a == "VOID")
    if (voids or s.get("void")) and s["fail"] == 0:
        return [result("B1", "VOID", figs, "%d command(s) VOID: the witness died; %s"
                       % (max(voids, s.get("void") or 0), detail))]
    ok = s["fail"] == 0 and passed == s["commands"] == len(lines)
    if ok and s["commands"] > 0 and not not_run:
        return [result("B1", "PASS", figs, detail)]
    if ok and not_run:
        return [result("B1", "NOT-RUN", figs, detail)]
    return [result("B1", "FAIL", figs, detail)]


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


E4_VOID_BLOCK = re.compile(r"^ *---- ARM VOID: .*\n((?: +- .*(?:\n|\Z))*)", re.M)


def e4_void(t, old_attribution_counts=False):
    """Spec 12.1 (c5849685): an e4 arm's void reasons from its text record, (listed,
    unlisted) by arm_void.kind: every "---- ARM VOID:" block's "- " lines (the run's, and
    attach_provenance's for attribution over the same window). The whole-arm attribution
    line of before 2026-11-19 ("attribution disagrees with the device:") counts only where
    the whole arm is the arm's window and the count was of what tx_ok counts: a RESPOND arm
    with no POST (old_attribution_counts). In INHIBIT it was the trail-window count,
    superseded since 2026-10-29, and dropped.
    For an arm with no POST the whole arm is its window, so the provenance line's device
    0x051 against tx_ok ("device says") is attribution too: e4_attribution."""
    listed, unlisted = [], []
    for block in E4_VOID_BLOCK.findall(t):
        for r in re.findall(r"^ +- (.+?) *$", block, re.M):
            if r.startswith(AV.OLD_ATTRIBUTION):
                if old_attribution_counts:
                    listed.append(r)
                continue
            (listed if AV.kind(r) else unlisted).append(r)
    return listed, unlisted


def e4_attribution(p):
    """[reason] when the provenance line's device 0x051 and tx_ok disagree (an arm with no
    POST: the whole arm is its window, spec 12.1), else []."""
    if p is not None and p["dev"] != p["dev_says"]:
        return ["%sthe device reports tx_ok=%d, provenance counts %d device 0x051 over the arm"
                % (AV.ATTRIBUTION, p["dev_says"], p["dev"])]
    return []


@judge("B2", "B")
def b2(F):
    figs, bad, void, err = {}, [], [], []
    for arm in ("inhibit", "respond", "longarm"):
        for x2 in ("x2app", "x2held"):
            t = text(F.one(r"B2_e4_presence_%s_%s_%s\.txt" % (arm, C7, x2)))
            p = e4_prov(t)
            # Spec 12.1 (c5849685): an arm void for any reason on its list is never judged as a
            # normal arm (before, only the witness clock's, 2026-11-18). B2's arms have no POST,
            # so the whole arm is the window: RESPOND's old whole-arm probe count counts, and an
            # INHIBIT arm's provenance count against tx_ok is attribution. A reason on no entry is
            # ERROR, for the gen-inhibit reviewer.
            listed, unlisted = e4_void(t, old_attribution_counts=arm == "respond")
            if arm != "respond":
                listed += e4_attribution(p)
            if unlisted:
                err.append("%s %s: a void reason on no spec 12.1 entry: %s" % (arm, x2, "; ".join(unlisted)))
                continue
            if listed:
                void.append("%s %s (%s)" % (arm, x2, "; ".join(listed)))
                continue
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
    if err and not bad:
        return [result("B2", "ERROR", figs, "; ".join(err))]
    if void and not bad:
        figs["void_arms"] = fig(len(void))
        return [result("B2", "VOID", figs, "arms VOID: %s" % ", ".join(void))]
    return [result("B2", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "6 arms as specified, X2 frames 0")]


B3_MODES = ("off", "observe", "passive", "respond")


@judge("B3", "B")
def b3(F):
    rows = re.findall(r"^(B3_e4_golive_(\w+?)_%s_%s_arm\d\.json) +\S+ +dev0x051 +(\d+|None) +stale (\d+) +(.+?) *$"
                      % (C7, TS), text(F.one(r"B3_golive_stale_check\.txt")), re.M)
    entries = json_lines(F.one(r"B3_mode_entry_analysis\.txt"))
    figs = {"arms": fig(len(rows)), "stale": fig(sum(int(r[3]) for r in rows))}
    # An arm void for any reason on spec 12.1's list (c5849685: the desk tools' "VOID: arm void" and
    # "void", from provenance_classifier.rejudge's arm_void, attribution over the same window from the
    # arm included; before, only the witness clock's, 2026-11-18, and a short window, 2026-11-19) is
    # no judgement: B3 VOID unless an arm read fairly fails. void_arms counts every reason, each arm
    # once, by its run file (the stale check and the entry analysis name the same arm; the figure
    # counted it twice before 2026-11-19). A reason on no entry is the entry analysis's error: ERROR.
    void = {r[0] for r in rows if r[4].startswith("VOID: arm void")}
    void |= {e["run"] for e in entries if e.get("void")}
    void_modes = {m for run in void for m in B3_MODES if re.match(r"B3_e4_golive_%s_" % m, run)}
    bad = [r[1] for r in rows if r[0] not in void
           and (r[2] == "None" or r[4] != "as specified" or int(r[3]) or not int(r[2]))]
    # an entry the analysis could not make (its "error") is ERROR, never a crash (run cb08's KeyError)
    err = ["%s: %s" % (e.get("run"), e["error"]) for e in entries if e.get("error") and e.get("run") not in void]
    entries = [e for e in entries if not e.get("void") and not e.get("error") and e.get("run") not in void]
    for m in B3_MODES:
        ms = [e.get("first_answer_after_post_ms") for e in entries
              if re.match(r"B3_e4_golive_%s_" % m, e["run"])]
        if len(ms) != 5 and m in void_modes:
            continue
        if len(ms) != 5:
            bad.append("%s: %d entries" % (m, len(ms)))
            continue
        if None in ms:
            bad.append("%s: %d entries with no answer after the post" % (m, ms.count(None)))
            continue
        figs["first_answer_ms_%s_min" % m] = fig(min(ms), "ms")
        figs["first_answer_ms_%s_max" % m] = fig(max(ms), "ms")
    bad += [e["run"] for e in entries if e.get("provenance") != "as specified" or e.get("post_http") != 200]
    if err:
        return [result("B3", "ERROR", figs, "; ".join(err))]
    if void and not bad:
        figs["void_arms"] = fig(len(void))
        return [result("B3", "VOID", figs, "arms VOID: %s" % ", ".join(sorted(void)))]
    ok = len(rows) == 20 and len(entries) == 20 and not bad
    return [result("B3", "PASS" if ok else "FAIL", figs, "; ".join(bad) or "20 arms, stale 0")]


@judge("B24", "B")
def b24(F):
    rows = re.findall(r"^(B[23]_e4_\S+\.json) +(AS SPECIFIED|NO DEVICE FRAMES|VOID|[A-Z][A-Z ]+?) +device 0x051",
                      text(F.one(r"B24_frame_content_%s\.txt" % C7)), re.M)
    spec = sum(1 for _, v in rows if v == "AS SPECIFIED")
    none = [n for n, v in rows if v == "NO DEVICE FRAMES"]
    # the arm void for a spec 12.1 reason (c5849685; its witness clock's alone from 2026-11-18); ERROR:
    # a void reason on no 12.1 entry (b24_frame_content_judge), for the gen-inhibit reviewer
    void = [n for n, v in rows if v == "VOID"]
    err = [n for n, v in rows if v == "ERROR"]
    other = [n for n, v in rows if v not in ("AS SPECIFIED", "NO DEVICE FRAMES", "VOID", "ERROR")]
    # The RESPOND arm sends no 0x051 by design; any other arm without device frames judges nothing.
    bad = other + [n for n in none if "_respond_" not in n]
    if err and not bad:
        return [result("B24", "ERROR", {"as_specified": fig(spec), "error": fig(len(err))},
                       "a void reason on no spec 12.1 entry: %s" % ", ".join(err))]
    if void and not bad:
        return [result("B24", "VOID", {"as_specified": fig(spec), "void": fig(len(void))},
                       "arms VOID: %s" % ", ".join(void))]
    ok = spec > 0 and not bad
    return [result("B24", "PASS" if ok else "FAIL",
                   {"as_specified": fig(spec), "no_device_frames": fig(len(none)), "other": fig(len(other))},
                   "; ".join(bad) or "%d arms as specified, %d RESPOND arm(s) with no device frames" % (spec, len(none)))]


B4_CASES = 6        # skip_method_bench's cases, as in the e3d9437 index (cases 6)


@judge("B4", "B")
def b4(F):
    t = text(F.one(r"B4_skip_method_stdout\.txt"))
    rows = re.findall(r"^(\S+) +(PASS|FAIL|INCONCLUSIVE|ERROR|VOID)\b +witness", t, re.M)
    passed = sum(1 for _, v in rows if v == "PASS")
    void = sum(1 for _, v in rows if v == "VOID")
    ok = len(rows) == B4_CASES and passed == len(rows) and "player sent" in t
    # an arm VOID on the witness clock (the Kvaser's stamps not taken as hardware time,
    # 2026-11-18) is no judgement: B4 VOID, unless another arm FAILed. B4 never PASSes while
    # a case is unjudged (gen-inhibit reviewer, 2026-11-19): an INCONCLUSIVE case reads VOID too,
    # and fewer case lines than B4_CASES never PASS.
    unjudged = void + sum(1 for _, v in rows if v == "INCONCLUSIVE")
    if ok:
        verdict = "PASS"
    elif any(v == "FAIL" for _, v in rows):
        verdict = "FAIL"
    elif any(v == "ERROR" for _, v in rows) or len(rows) != B4_CASES:
        verdict = "ERROR" if not unjudged else "VOID"
    elif unjudged:
        verdict = "VOID"
    else:
        verdict = "FAIL"
    figs = {"cases": fig(len(rows)), "passed": fig(passed)}
    if void:
        figs["void"] = fig(void)
    return [result("B4", verdict, figs, "%d of %d PASS%s" % (passed, len(rows), ", %d VOID" % void if void else ""))]


def jam_arms(t):
    """(n, the rest) of each arm line; the replay rate may read None (an arm whose replay
    was not seen), and threshold's arm not armed after its reset has no rate at all."""
    return re.findall(r"^arm (\d+)(?: \(replay [^)]*\))?: (.+)$", t, re.M)


def livetrain_arm(v):
    """The arm's verdict: an INCONCLUSIVE arm's reason as the gen-inhibit reviewer ruled it
    (answer to ba92acf0, 2026-11-19; arm_rulings.b5_arm): not live at the start of the jam
    VOID; a reset by its reason; never went live by the gate on the wire."""
    if v.startswith("INCONCLUSIVE"):
        return AR.VERDICT[AR.b5_arm(v)]
    return "PASS" if re.search(r"\| A: PASS\b.*\| B: PASS\b", v) else "FAIL"


def jam_void(t):
    """x2_jam_cases' run VOID on its witness clock (witness_clock.void_verdict, run_guarded,
    2026-11-18) or on its witness's child process failing or stopping (proc_witness.void_verdict,
    2026-11-19), or on its witness's load (proc_witness.load_reason, "witness load", 2026-11-19):
    its last verdict line. The arms judged before it keep their verdicts."""
    return bool(re.search(r"^verdict VOID: (witness clock |the witness (child|thread) |witness load: )", t, re.M))


def rejudged(files):
    """The run outputs, each replaced by its re-judgement from its record where the desk step
    wrote one (baseline_b_desk.sh B5: <output>_rejudged.txt, x2_jam_cases rejudge-livetrain,
    2026-11-19)."""
    names = {base(f) for f in files}
    return [f for f in files if f.endswith("_rejudged.txt") or base(f)[:-4] + "_rejudged.txt" not in names]


@judge("B5", "B")
def b5(F):
    figs, bad, void, ruled, all_arms = {}, [], 0, [], []
    # The jam's start is the witness's first error frame at or after the train's send (gen-inhibit
    # reviewer, answer to 0cfd6689, 2026-11-19; x2_jam_cases.judge_livetrain_arm): a run's own
    # lines judge so from that day, and an older run's re-judgement from its record stands in
    # for its lines where the desk step wrote one.
    for rate, pat in (("200", r"B5_livetrain_p5000(?:_rejudged)?\.txt"),
                      ("500", r"B5_livetrain_p2000_run\d(?:_rejudged)?\.txt")):
        files = rejudged(F.some(pat))
        lines = [(base(f), n, v) for f in files for n, v in jam_arms(text(f))]
        arms = [livetrain_arm(v) for _f, _n, v in lines]
        nv = sum(jam_void(text(f)) for f in files)
        void += nv
        figs["arms_%s_per_s" % rate] = fig(len(arms))
        figs["pass_%s_per_s" % rate] = fig(arms.count("PASS"))
        # the arms the tool recorded INCONCLUSIVE, whatever the ruling makes of each
        figs["inconclusive_%s_per_s" % rate] = fig(sum(1 for _f, _n, v in lines if v.startswith("INCONCLUSIVE")))
        if "FAIL" in arms or (not arms and not nv):
            bad.append("%s/s: %s" % (rate, arms))
        ruled += ["%s arm %s %s (%s)" % (f, n, a, v.split("|")[1].strip()[:160] if "|" in v else v[:160])
                  for (f, n, v), a in zip(lines, arms) if v.startswith("INCONCLUSIVE")]
        all_arms += arms
    arms = all_arms
    if void and not bad and "ERROR" not in arms:
        figs["void_runs"] = fig(void)
        return [result("B5", "VOID", figs, "%d run(s) VOID on the witness clock; no arm FAIL%s"
                       % (void, "; " + "; ".join(ruled) if ruled else ""))]
    # Spec 12.1 (c5849685): never PASS while an arm it asked for is unjudged, and the gen-inhibit
    # reviewer's rulings on its INCONCLUSIVE arms' reasons (answer to ba92acf0, 2026-11-19;
    # arm_rulings.b5_arm): "not live at the start of the jam" is the bench's (VOID); a reset seen is a
    # result read by its reason (a panic, watchdog or software reset FAIL, a brownout or power-on
    # VOID, anything else ERROR); "never went live" VOID only if the gate never held on the wire,
    # FAIL if it held, ERROR if the record can't tell. From c5849685 until then such an arm made B5
    # INCONCLUSIVE; before c5849685, B5 PASSed beside one.
    verdict = "FAIL" if bad else ruled_verdict(arms)
    return [result("B5", verdict, figs, "; ".join(bad + ruled) or
                   "200/s %d of %d, 500/s %d of %d" % (
                       figs["pass_200_per_s"]["value"], figs["arms_200_per_s"]["value"],
                       figs["pass_500_per_s"]["value"], figs["arms_500_per_s"]["value"]))]


@judge("B6", "B")
def b6(F):
    files = F.some(r"B6_threshold_n\d+_lead[\d.]+\.txt")
    lines = [(base(f), n, v) for f in files for n, v in jam_arms(text(f))]
    verdicts = [re.search(r"^verdict (\S+)", text(f), re.M) for f in files]
    # An arm the tool calls INCONCLUSIVE is judged by its reason, as the gen-inhibit reviewer ruled
    # (answer to ba92acf0, 2026-11-19; arm_rulings.b6_arm): a reset while armed by its reason (a
    # panic, watchdog or software reset FAIL, a brownout or power-on VOID, anything else ERROR);
    # not live at the start of the pulses, an abort already latched, error frames between the arm
    # and the pulses, and not armed because the reset before it was not shown, VOID (the bench's);
    # never went live by the gate on the wire, as B5. A delivery other than one error frame per
    # pulse stays INCONCLUSIVE (12.1 lists a stimulus that fell short, not one that went over; step
    # 1a's acceptance, 2026-10-29). A FAIL arm, a FINDING (another trip latched) or any other word
    # is FAIL; so is a run whose own verdict is not PASS or INCONCLUSIVE (FAIL, FINDING, STOPPED).
    # A run VOID on its witness clock (2026-11-18) is no judgement: VOID unless an arm FAILs. Until
    # then every INCONCLUSIVE arm made B6 INCONCLUSIVE, and a not-armed arm was not read.
    void = [jam_void(text(f)) for f in files]
    arms, notes = [], []
    for f, n, v in lines:
        word = v.split()[0]
        if word == "INCONCLUSIVE":
            why = v[len(word):].split("|")[0].strip()
            k = AR.b6_arm(why)
            a = "INCONCLUSIVE" if k == "inconclusive" else AR.VERDICT[k]
            notes.append("%s arm %s %s (%s)" % (f, n, a, why[:160]))
        else:
            a = "PASS" if word == "PASS" else "FAIL"
        arms.append(a)
    runs = {v.group(1) if v else None for v, vd in zip(verdicts, void) if not vd}
    if not runs <= {"PASS", "INCONCLUSIVE"}:
        arms.append("FAIL")
        notes.append("a run's verdict %s" % sorted(str(r) for r in runs - {"PASS", "INCONCLUSIVE"}))
    n_arms = len(lines)
    figs = {"arms": fig(n_arms), "passed": fig(arms.count("PASS"))}
    if any(void) and not {"FAIL", "ERROR"} & set(arms):
        figs["void_runs"] = fig(sum(void))
        return [result("B6", "VOID", figs, "%d of %d runs VOID on the witness clock; %d arms PASS before it%s"
                       % (sum(void), len(files), arms.count("PASS"), "; " + "; ".join(notes) if notes else ""))]
    verdict = "FAIL" if not n_arms else ruled_verdict(arms)
    return [result("B6", verdict, figs,
                   "%d of %d arms PASS over %d runs%s" % (arms.count("PASS"), n_arms, len(files),
                                                          "; " + "; ".join(notes) if notes else ""))]


@judge("B7", "B")
def b7(F):
    t = text(F.one(r"B7_busoff_rearm\.txt"))
    lines = re.findall(r"^arm (\d+): (.+?)(?: \| restore .*)?$", t, re.M)
    words = [x.split(":")[0] for _n, x in lines]
    v = re.search(r"^verdict (.+?) *$", t, re.M)
    # An arm the case could not judge (INCONCLUSIVE) is judged by its reason, as the gen-inhibit
    # reviewer ruled (answer to ba92acf0, 2026-11-19; arm_rulings.b7_arm): too few answered readings
    # and the replay not running after the re-arm are the bench's (VOID); the page never reading
    # bus_off after the jam is the device's (FAIL), unless no page read from the jam to the re-arm
    # was answered (device silent, VOID), and ERROR where the record does not say; never went live
    # before the jam by the gate on the wire, as B5's never went live (the tester's reading, put to
    # the reviewer). Any other word but AS SPECIFIED is FAIL. Until then every INCONCLUSIVE arm made
    # B7 INCONCLUSIVE when the case said so (2026-11-16).
    arms, notes = [], []
    for (n, x), w in zip(lines, words):
        if w == "INCONCLUSIVE":
            a = AR.VERDICT[AR.b7_arm(x)]
            notes.append("arm %s %s (%s)" % (n, a, x[len("INCONCLUSIVE: "):][:160]))
        else:
            a = "PASS" if w == "AS SPECIFIED" else "FAIL"
        arms.append(a)
    figs = {"arms": fig(len(lines)), "as_specified": fig(words.count("AS SPECIFIED"))}
    # a run VOID on its witness clock (2026-11-18): VOID unless an arm FAILs
    if jam_void(t) and not {"FAIL", "ERROR"} & set(arms):
        figs["void_runs"] = fig(1)
        return [result("B7", "VOID", figs, "run VOID on the witness clock%s" % ("; " + "; ".join(notes) if notes else ""))]
    # the case's own verdict (not one VOID on its witness clock): AS SPECIFIED, or INCONCLUSIVE beside an
    # INCONCLUSIVE arm; anything else FAILs
    if jam_void(t):
        pass
    elif not v or v.group(1) not in ("AS SPECIFIED", "INCONCLUSIVE") or (v.group(1) == "INCONCLUSIVE") != ("INCONCLUSIVE" in words):
        arms.append("FAIL")
        notes.append("the case's verdict %s" % (v.group(1) if v else None))
    verdict = "FAIL" if not lines else ruled_verdict(arms)
    return [result("B7", verdict, figs, "%d of %d arms AS SPECIFIED%s" % (
        words.count("AS SPECIFIED"), len(lines), "; " + "; ".join(notes) if notes else ""))]


@judge("B8", "B")
def b8(F):
    t = text(F.one(r"B8_busoff\.txt"))
    arms = re.findall(r"^arm \d+ train=(True|False): (\w+)", t, re.M)
    train = [v for tr, v in arms if tr == "True"]
    control = [v for tr, v in arms if tr == "False"]
    v = re.search(r"^verdict (\S+)", t, re.M)
    # The control arm sends no train, so no bus-off is reached and it reads INCONCLUSIVE by design.
    ok = train and all(a == "PASS" for a in train) and v and v.group(1) == "PASS" and all(c != "FAIL" for c in control)
    # a run VOID on its witness clock (2026-11-18): VOID unless an arm FAILs
    if jam_void(t) and "FAIL" not in train and "FAIL" not in control:
        return [result("B8", "VOID", {"train_arms": fig(len(train)), "train_passed": fig(train.count("PASS")),
                                      "control_arms": fig(len(control)),
                                      "control_inconclusive": fig(control.count("INCONCLUSIVE")),
                                      "void_runs": fig(1)}, "run VOID on the witness clock")]
    return [result("B8", "PASS" if ok else "FAIL",
                   {"train_arms": fig(len(train)), "train_passed": fig(train.count("PASS")),
                    "control_arms": fig(len(control)), "control_inconclusive": fig(control.count("INCONCLUSIVE"))},
                   "train %d of %d PASS; control %s" % (train.count("PASS"), len(train), control))]


B9_PRE_S = 0.5     # the newest clean, live page sample may be this far before the first burst
# B9 and B10 judge the PCAN's own stamps, mapped onto the host clock; a fit outside the
# harness bounds prints this line, and the check is VOID, never judged (A2 (a), 2026-11-11).
CLOCK_VOID_RE = re.compile(r"^witness clock VOID: (.*)$", re.M)
# Condition 5 (the gen-inhibit reviewer, 2026-11-18): B9's and B10's bursts come from canmain, and
# each is measured on the PCAN's mapped stamps (bench/burst_measures.py); a burst outside the bounds
# prints this line, and the check is VOID, never FAIL, whatever its arms say.
BURST_VOID_RE = re.compile(r"^burst timing VOID: (.*)$", re.M)
# A witness whose loop died (B9's and B10's thread, B12's child: proc_witness.void_line, 2026-11-19) prints
# this line at the run's end, and the check is VOID, read before the rest: never a short witness's count.
WITNESS_VOID_RE = re.compile(r"^witness VOID: (.*)$", re.M)


@judge("B9", "B")
def b9(F):
    # Trip 6 on a LIVE arm, latched by the wrong-bit-rate bursts (reviewer, 2026-10-25). Until
    # then this read only "error-frame rate exceeded" among the aborts, and every B9 record on
    # file (e3d9437 to 6926fb0) was never live and had latched before the first burst. Now the
    # record (wrong_bitrate_errors.py's json) must show: live_before true; no abort in any page
    # sample before the first burst (t < 0, the series' clock); every sample before the latch
    # live; and a live sample without an abort within B9_PRE_S before the first burst, so the
    # latch is placed after the burst to the page's 5 Hz resolution (a burst latches within a
    # poll period, so no clean sample can come after it); and the tool's pre_burst_window
    # showing 0 witness error frames from that sample to the first burst, so a latch in the gap
    # cannot be credited to the bursts (reviewer, 2026-10-25). Short of that it is INCONCLUSIVE.
    # Then, as before, trip 6 the latch and every burst drawing error frames, or FAIL.
    t = text(F.one(r"B9_wrong_bitrate_calibrate\.txt"))
    pv = WITNESS_VOID_RE.search(t)
    if pv:
        return [result("B9", "VOID", detail="the witness: %s" % pv.group(1)[:300])]
    cv = CLOCK_VOID_RE.search(t)
    if cv:
        return [result("B9", "VOID", detail="the PCAN's clock: %s" % cv.group(1))]
    bv = BURST_VOID_RE.search(t)
    if bv:
        return [result("B9", "VOID", detail="the bursts' timing: %s" % bv.group(1)[:300])]
    bursts = [int(n) for n in re.findall(r"^  burst at [\d.]+ s, \d+ ms -> witness error frames (\d+)$", t, re.M)]
    m = re.search(r"^summary (\{.*\}) witness total (\d+)", t, re.M)
    if not m or not bursts:
        return [result("B9", "ERROR", detail="no summary or no bursts")]
    s = ast.literal_eval(m.group(1))
    rec = json.loads(text(F.one(r"wrong_bitrate_calibrate_%s\.json" % TS)))
    series = [x for x in rec.get("series") or [] if isinstance(x.get("t"), (int, float))]
    first = next((x for x in series if x.get("abort")), None)
    latched = bool(first) and first["abort"] == "error-frame rate exceeded"
    figs = {"bursts": fig(len(bursts)), "burst_errors_min": fig(min(bursts)), "burst_errors_max": fig(max(bursts)),
            "witness_error_frames": fig(int(m.group(2))), "tec_max": fig(s["tec_max"]),
            "trip6_latched": fig(int(latched))}
    head = "%d bursts, %d witness error frames, TEC max %d" % (len(bursts), int(m.group(2)), s["tec_max"])
    if not series:
        return [result("B9", "ERROR", figs, "the record has no page samples")]
    before = series[:series.index(first)] if first else series
    if rec.get("live_before") is not True:
        why = "the device was not live before the first burst (live_before %r)" % rec.get("live_before")
    elif first is not None and first["t"] < 0:
        why = "latched before the first burst: %r at %+.2f s" % (first["abort"], first["t"])
    elif any(x.get("live") is not True for x in before):
        x = next(x for x in before if x.get("live") is not True)
        why = "not live at %+.2f s, before any latch" % x["t"]
    elif not any(-B9_PRE_S <= x["t"] < 0 for x in before):
        last = [x for x in before if x["t"] < 0]
        why = ("the latch cannot be placed after the first burst: no live sample without an abort within "
               "%.1f s before it (the last at %s)" % (B9_PRE_S, "%+.2f s" % last[-1]["t"] if last else "none"))
    elif not isinstance(rec.get("pre_burst_window"), dict) or rec["pre_burst_window"].get("errors") is None:
        why = ("the record has no witness count between the last clean live sample and the first burst "
               "(wrong_bitrate_errors.py before 2026-10-25)")
    elif rec["pre_burst_window"]["errors"] != 0:
        why = ("%d witness error frames between the last clean live sample (%+.2f s) and the first burst, "
               "so a latch in that gap cannot be credited to the bursts"
               % (rec["pre_burst_window"]["errors"], rec["pre_burst_window"].get("from_s") or 0.0))
    else:
        why = None
    if why:
        return [result("B9", "INCONCLUSIVE", figs, "%s; %s" % (head, why))]
    ok = latched and min(bursts) > 0
    return [result("B9", "PASS" if ok else "FAIL", figs,
                   "%s, trip 6 %s" % (head, "latched at %+.2f s (first burst 0)" % first["t"] if latched else
                                      "NOT latched (%s)" % ("%r at %+.2f s" % (first["abort"], first["t"]) if first
                                                            else "no trip latched")))]


@judge("B10", "B")
def b10(F):
    t = text(F.one(r"B10_trip6_rearm_check\.txt"))
    pv = WITNESS_VOID_RE.search(t)
    if pv:
        return [result("B10", "VOID", detail="the witness: %s" % pv.group(1)[:300])]
    cv = CLOCK_VOID_RE.search(t)
    if cv:
        return [result("B10", "VOID", detail="the PCAN's clock: %s" % cv.group(1))]
    bv = BURST_VOID_RE.search(t)
    if bv:
        return [result("B10", "VOID", detail="the bursts' timing: %s" % bv.group(1)[:300])]
    # The flood arms are the stimulus and carry no verdict; every other arm ends with its own.
    arms = re.findall(r"^(A\d+) (\w+) +armed .*? aborts \[[^]]*\] *(.*?) *$", t, re.M)
    judged = [(a, k, v) for a, k, v in arms if k != "flood"]
    hist = re.findall(r"^ +history (.+?) \| prediction (.+?) *$", t, re.M)
    spec = sum(1 for *_, v in judged if v == "as specified")
    figs = {"judged_arms": fig(len(judged)), "flood_arms": fig(len(arms) - len(judged))}
    # An arm the tool VOIDs (its stimulus not as planned, or since 2026-11-11 its mapped gap
    # disagreeing with the receipt gap it paced on) supports no verdict: the check is VOID, not
    # FAIL, unless another arm or history is NOT as specified, which stays FAIL (A2 (a); the
    # gen-inhibit reviewer, 2026-11-11: VOID is not PASS, so it is no weakening). So a record
    # with an old arm verdict such as "VOID: armed X s after the burst (needs <= 8)" or "VOID:
    # N witness error frames since arming" rejudges as VOID, where it read FAIL before.
    voided = [a for a, _k, v in judged if v.startswith("VOID")] + \
        [h for h, _p in hist if h.startswith("VOID")]
    notspec = [v for *_, v in judged if v.startswith("NOT")] + [x for h, p in hist for x in (h, p)
                                                                  if x.startswith("NOT")]
    if voided and not notspec:
        return [result("B10", "VOID", figs, "%d of %d judged arms as specified; VOID: %s"
                       % (spec, len(judged), "; ".join(v for _a, _k, v in judged if v.startswith("VOID"))[:300]))]
    ok = (judged and spec == len(judged) and len(hist) == len(judged)
          and all(h == p == "as specified" for h, p in hist))
    return [result("B10", "PASS" if ok else "FAIL", figs,
                   "%d of %d judged arms as specified" % (spec, len(judged)))]


def row_errors(rows):
    """An analysis row's "error" (passive_analysis, quiesce_analysis): a judged figure it
    could not compute from decodable diag pages. The check is then ERROR, never graded
    on the rest (reviewer, 2026-10-25, decode rework reading Q9). "" if none."""
    return "; ".join("%s: %s" % (r.get("run"), r["error"]) for r in rows if r.get("error"))


@judge("B11", "B")
def b11(F):
    rows = json_lines(F.one(r"B11_quiesce_analysis\.txt"))
    # an arm its e4 run voided on its witness clock (quiesce_analysis's "void", 2026-11-18) is no
    # judgement: B11 VOID unless another arm fails
    # an arm voided on its witness clock, or whose witness does not hold the whole replay (an empty or
    # short window, 2026-11-19; quiesce_analysis's "void"), is no judgement: B11 VOID unless another fails
    void = [r["run"] for r in rows if r.get("void")]
    bad = [r["run"] for r in rows if not r.get("void") and not r.get("error") and (
           r.get("provenance") != "as specified" or r.get("late") or r.get("double")
           or (r.get("initial_mode") == "inhibit" and not (r.get("tx_ok_vs_last_inhibit_span") or {}).get("agree")))]
    err = row_errors(rows)
    if not err and len(rows) != 6:      # run cb08: the analysis died on its first arm and wrote no row
        err = "%d analysis rows, 6 arms expected" % len(rows)
    if void and not bad and not err:
        return [result("B11", "VOID", {"arms": fig(len(rows)), "void_arms": fig(len(void))},
                       "arms VOID: %s" % "; ".join("%s (%s)" % (r["run"], "; ".join(r["void"]))
                                                   for r in rows if r.get("void")))]
    return [result("B11", "ERROR" if err else "FAIL" if bad else "PASS", {"arms": fig(len(rows))},
                   err or "; ".join(bad) or "6 arms as specified, tx_ok agrees with every INHIBIT span")]


B11RAW_POSTS = 12      # baseline_b_noX2.sh B11raw: 6 arms, an OBSERVE POST and a POST back in each


@judge("B11raw", "B")
def b11raw(F):
    # spec 3 in the request-to-reply window (user, 2026-11-19; topic (E), the gen-inhibit reviewer's
    # readings (m)-(r), answer to b07f7984): b11_rawpost_judge's row per POST, FAIL, then ERROR, then
    # VOID (an arm void for a spec 12.1 reason, or a read the harness never sent), else PASS; the
    # witness half NOT APPLICABLE where nothing could show on it. Until then the capture judge's
    # B11raw_rawpost_judge.txt, its OBSERVE POSTs by arrival on the air.
    t = text(F.one(r"B11raw_window_judge\.txt"))
    posts = [json.loads(l.strip()) for l in t.splitlines() if l.strip().startswith('{"at_s"')]
    vs = []
    for p in posts:
        v = str(p.get("verdict"))
        vs.append("VOID" if v.startswith("VOID") else "ERROR" if v.startswith("ERROR") else v)
    bad_words = [v for v in vs if v not in ("PASS", "FAIL", "ERROR", "VOID")]
    prov = json_lines(F.one(r"B11raw_quiesce_analysis\.txt"))
    pvoid = [r["run"] for r in prov if r.get("void")]
    pbad = [r["run"] for r in prov if not r.get("void") and r.get("provenance") != "as specified"]
    err = row_errors(prov)
    arms = vs + ["FAIL"] * len(pbad) + ["VOID"] * len(pvoid) + (["ERROR"] if err or bad_words else [])
    if len(posts) != B11RAW_POSTS:
        arms.append("ERROR")
    verdict = ruled_verdict(arms)
    wins = [p["window_ms"] for p in posts if p.get("window_ms") is not None]
    margins = [p["margin_ms"] for p in posts if p.get("margin_ms") is not None]
    figs = {"posts": fig(len(posts)), "pass": fig(vs.count("PASS")), "fail": fig(vs.count("FAIL")),
            "error": fig(vs.count("ERROR")), "void": fig(vs.count("VOID")),
            "witness_not_applicable": fig(sum(1 for p in posts if p.get("witness") == "NOT APPLICABLE")),
            "window_ms_min": fig(min(wins) if wins else None, "ms"), "window_ms_max": fig(max(wins) if wins else None, "ms"),
            "margin_ms_max": fig(max(margins) if margins else None, "ms")}
    why = ["%s at %s s into mode %s: %s" % (v, p.get("at_s"), p.get("mode"), "; ".join(p.get("why") or [str(p.get("verdict"))]))
           for p, v in zip(posts, vs) if v != "PASS"]
    if len(posts) != B11RAW_POSTS:
        why.insert(0, "%d POST rows, want %d" % (len(posts), B11RAW_POSTS))
    return [result("B11raw", verdict, figs, err or "; ".join(why + pbad + bad_words)[:600]
                   or "%d POSTs PASS in the request-to-reply window" % len(posts))]


# The restart record each B12 cycle reads back after the restart (--record-get, record_get_after
# in the cycle's JSON): the stop the restart hook made before it (gen-inhibit reviewer,
# 2026-11-03, follow-up (iii)). On _rt the record must be there and read; these three as said.
B12_LAST_RESTART = (("did_twai", True), ("did_stdby", True), ("timed_out", False))


def b12_record(path):
    """None if the restart record B12's cycle file names reads as specified, else why. Only the
    record beside the file, by the name its cycle line gives (reviewer, 2026-11-03, reading (a))."""
    m = re.search(r"^cycle 0 \w+: .*, wrote (restart_error_frames_\S+\.json)$", text(path), re.M)
    if not m:
        return "%s names no record" % base(path)
    name = m.group(1)
    p = os.path.join(os.path.dirname(path), name)
    if not os.path.isfile(p):
        return "%s: its record %s is not beside it" % (base(path), name)
    try:
        d = json.loads(text(p))
    except ValueError as e:
        return "%s cannot be read (%s)" % (name, e)
    r = d.get("record_get_after") if isinstance(d, dict) else None
    lr = r.get("last_restart") if isinstance(r, dict) else None
    if not isinstance(lr, dict):
        return "%s: the restart record was not read (record_get_after %s)" % (
            name, repr(r if not isinstance(r, dict) else {"last_restart": lr})[:80])
    wrong = ["%s %s" % (k, json.dumps(lr.get(k))) for k, v in B12_LAST_RESTART if lr.get(k) is not v]
    return "%s: last_restart %s" % (name, ", ".join(wrong)) if wrong else None


@judge("B12", "B")
def b12(F):
    sw, panic, bad, rec_bad, voids = [], [], [], [], []
    files = F.some(r"B12_\d_(live|off)_(reboot|crash)\.txt", 7)
    for f in files:
        restart = re.search(r"_(reboot|crash)\.txt$", f).group(1)
        # a cycle whose witness died read a short witness (restart_error_frames, proc_witness.void_line,
        # 2026-11-19): its 0 error frames are not "no error frames", so B12 is VOID unless it FAILs
        pv = WITNESS_VOID_RE.search(text(f))
        if pv:
            voids.append("%s witness: %s" % (base(f), pv.group(1)[:200]))
        m = re.search(r"^cycle 0 \w+: hook \S+, reset \['(\w+)'\], build \S+, live_at_post \S+, boot ([\d.]+) s after POST, errors (\d+)",
                      text(f), re.M)
        if not m:
            bad.append(base(f) + " no cycle line")
            continue
        want = "SW" if restart == "reboot" else "PANIC"
        (sw if want == "SW" else panic).append(float(m.group(2)))
        if m.group(1) != want or int(m.group(3)):
            bad.append("%s reset %s errors %s" % (base(f), m.group(1), m.group(3)))
        why = b12_record(f)
        if why:
            rec_bad.append(why)
    figs = {"cycles": fig(len(sw) + len(panic)), "error_frames": fig(0 if not bad else None)}
    if sw:
        figs.update(sw_boot_s_min=fig(min(sw), "s"), sw_boot_s_max=fig(max(sw), "s"))
    if panic:
        figs.update(panic_boot_s_min=fig(min(panic), "s"), panic_boot_s_max=fig(max(panic), "s"))
    if voids and not (bad or rec_bad):
        return [result("B12", "VOID", figs, "; ".join(voids))]
    return [result("B12", "FAIL" if bad or rec_bad else "PASS", figs, "; ".join(bad + rec_bad) or
                   "7 of 7 cycles, 0 error frames; 7 restart records: did_twai, did_stdby true, timed_out false")]


@judge("B13", "B")
def b13(F):
    rows = json_lines(F.one(r"B13_passive_analysis\.txt"))
    # an arm void for a spec 12.1 reason (c5849685; passive_analysis's "void") is no judgement: B13
    # VOID unless an arm read fairly, or the booking check, fails
    void = [r["run"] for r in rows if r.get("void")]
    bad = [r["run"] for r in rows if not r.get("void") and not r.get("error") and (
           r.get("provenance") != "as specified"
           or (r["mode"] == "passive" and r["device_0x051"] != 0)
           or (r["mode"] == "inhibit" and r["device_0x051"] == 0))]
    book = re.findall(r"^\[(\w+) *\] ", text(F.one(r"B13_respond_booking_check\.txt")), re.M)
    bfail = [b for b in book if b != "PASS"]
    ok = len(rows) == 8 and not bad and book and not bfail
    err = row_errors(rows)
    if void and not err and not bad and book and not bfail:
        return [result("B13", "VOID", {"arms": fig(len(rows)), "void_arms": fig(len(void))},
                       "arms VOID: %s" % "; ".join("%s (%s)" % (r["run"], "; ".join(r["void"]))
                                                   for r in rows if r.get("void")))]
    return [result("B13", "ERROR" if err else "PASS" if ok else "FAIL",
                   {"arms": fig(len(rows)), "booking_rows": fig(len(book)), "booking_fail": fig(len(bfail))},
                   err or "; ".join(bad) or "%d arms ok, booking %s" % (len(rows), "PASS" if not bfail else bfail))]


@judge("B14", "B")
def b14(F):
    t = text(F.one(r"B14_observe_ack_probe\.txt"))
    modes = re.findall(r"^  mode \d: ack errors \w+ -> (.+?) *$", t, re.M)
    v = re.search(r"^verdict: (.+?); mode at end (\S+)", t, re.M)
    figs = {"mode_checks": fig(len(modes))}
    if not v:
        return [result("B14", "FAIL", figs, "no verdict line")]
    # The gen-inhibit reviewer's rulings (answer to ba92acf0, 2026-11-19; arm_rulings.b14_row and
    # b14_verdict): both VOID verdicts (no valid mode-0 control; the control ACKed) are the bench's,
    # VOID; INCOMPLETE is VOID when the probe was cut short, otherwise ERROR, for the reviewer. An
    # INVALID row (the page reads another mode than asked) FAILs, unless its mode POST got no answer
    # on the bench path (VOID). A row NOT as specified FAILs where its run is read (not beside a VOID
    # control, which leaves the WiCAN not the only possible ACKer); a mode at the end other than 0
    # FAILs. Until then anything but as specified FAILed.
    arms, notes = [], []
    for m in modes:
        k = AR.b14_row(m)
        if k is not None:
            arms.append(AR.VERDICT[k])
            notes.append("%s: %s" % (AR.VERDICT[k], m[:160]))
        elif m != "as specified":
            arms.append("FAIL" if not v.group(1).startswith("VOID") else "VOID")
    kv = AR.b14_verdict(v.group(1))
    if kv is not None:
        arms.append(AR.VERDICT[kv])
    elif v.group(1) == "NOT as specified":
        # the probe's word for any row not as specified, an INVALID one included: its rows decide
        if not arms:
            arms.append("FAIL")
    elif v.group(1) != "as specified":
        arms.append("FAIL")
    if v.group(2) != "0":
        arms.append("FAIL")
    verdict = "FAIL" if not modes and kv is None else ruled_verdict(arms)
    return [result("B14", verdict, figs, "; ".join([v.group(0)] + notes))]


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
    want = (rec.get("args") or {}).get("arms", 3)      # the run's own --arms
    if v == "PASS" and (off or len(mains) < want):
        return [result("NONODE", "ERROR", figs, "the record says PASS, but: %s" % (off or "%d main arms of %d" % (len(mains), want)))]
    verdict = ("PASS" if v == "PASS" else "FAIL" if v.startswith("NOT AS SPECIFIED") else
               "VOID" if v.startswith("VOID") else "INCONCLUSIVE" if v.startswith(("INCONCLUSIVE", "FLAKY")) else "ERROR")
    return [result("NONODE", verdict, figs, detail)]


@judge("B15", "B")
def b15(F):
    t = text(F.one(r"B15_wican_segment_probe\.txt"))
    row = r"^--- (\S+) .*?: (\d+) frames, ids (\{.*\})$"
    adapters = re.findall(row, t, re.M)
    # an adapter that did not open, or a row that does not read, is no census of it: ERROR,
    # never a PASS on the adapters that did (gen-inhibit reviewer, 2026-11-18: de6e passed
    # B15 on the PCAN alone, the Kvaser and the Innomaker "open failed")
    unread = [ln for ln in re.findall(r"^--- .*$", t, re.M) if not re.match(row, ln)]
    if unread:
        return [result("B15", "ERROR", {}, "; ".join(
            ("%s open failed" % m.group(1)) if m else ("row not read: %s" % ln[:80])
            for ln in unread for m in [re.match(r"^--- (\S+) open failed", ln)]))]
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
    mode, line = host_mode(host)
    return [result("B15host", "PASS" if decodes and pages else "FAIL",
                   {"modes_with_pages": fig(len(pages)), "host_mode": fig(mode)},
                   "%s; %s" % ("every emitted diag frame decodes against the DBC" if decodes
                               else "a diag frame does not decode", line))]


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
    # _man2 at the e3d9437 baseline; _rxq since 2026-10-25 (reproducible builds: man2 = man)
    return spec11(F, "B17b", r"B17_spec11_page_%s_(?:man2|rxq)\.txt" % C7)


@judge("B18", "B")
def b18(F):
    t = text(F.one(r"B18_diag_build_page_check\.txt"))
    w8 = re.search(r"^  diag_build_id on the wire +([0-9a-f]{8})$", t, re.M)
    if w8:
        # Schema 8 (port step 0c): the wire's build id, the image file's hash and the page's
        # elf_sha256 name one image (spec 12.4 "0x7F3 carries the build id", bench half, and
        # "the page names the binary"). Every row the check printed must be ok.
        end = re.search(r"^  (MATCH|MISMATCH) -- ", t[w8.start():], re.M)
        sec = t[w8.start():w8.start() + end.start()] if end else ""
        img = re.search(r"^  image hash, first 4 bytes +([0-9a-f]{8})$", sec, re.M)
        page = re.search(r"^  page elf_sha256, first 4 +([0-9a-f]{8})$", sec, re.M)
        rows = re.findall(r"^  (ok|FAIL) +\S", sec, re.M)
        ok = (end and end.group(1) == "MATCH" and img and page and w8.group(1) == img.group(1) == page.group(1)
              and len(rows) >= 7 and all(r == "ok" for r in rows))
        return [result("B18", "PASS" if ok else "FAIL", {"diag_build_id": fig(w8.group(1))},
                       "MATCH, %d rows ok" % len(rows) if ok else
                       "wire %s, image %s, page %s, %d of %d rows ok, %s" % (
                           w8.group(1), img and img.group(1), page and page.group(1), rows.count("ok"), len(rows),
                           end.group(1) if end else "no verdict line"))]
    # Schema 7 and older: diag_git_hash against fnv1a32 of the expected sha.
    w = re.search(r"diag_git_hash on the wire +(0x[0-9A-F]+)", t)
    e = re.search(r"expected from \S+ +(0x[0-9A-F]+)", t)
    ok = w and e and w.group(1) == e.group(1) and "MATCH --" in t
    return [result("B18", "PASS" if ok else "FAIL", {"diag_git_hash": fig(w.group(1) if w else None)},
                   "MATCH" if ok else "no match")]


# Port step 1c moved the restart test and the TX-abort probe into vtrux/restart_guard and
# vtrux/twai_port and renamed their switches. A run file from before it names the old
# switch; the figure is named by the new one either way.
RENAMED = {"RESTART_GUARD_TEST": "GI_TEST_RESTART_QUIESCE", "TWAI_PORT_TXABORT_PROBE": "GI_INSTRUMENT_TXABORT"}


def inert_rows(t):
    return re.findall(r"^(GI_\w+|OTA_\w+|RESTART_GUARD_\w+|TWAI_PORT_\w+) +(PASS|FAIL) +(.+?) *$", t, re.M)


def inert_verdicts(rows, switch):
    """The verdicts test_only_inert.py printed for switch, under its name or its pre-1c one."""
    return [r[1] for r in rows if r[0] in (switch, RENAMED.get(switch))]


@judge("B19present", "B")
def b19present(F):
    figs, bad = {}, []
    for tag, switch in (("rt", "RESTART_GUARD_TEST"), ("txabort", "TWAI_PORT_TXABORT_PROBE"),
                        ("abortwin", "GI_INSTRUMENT_ABORTWIN"), ("rxq", "GI_INSTRUMENT_RXQ")):
        rows = inert_rows(text(F.one(r"B19_present_%s\.txt" % tag)))
        v = inert_verdicts(rows, switch)
        figs[switch] = fig(v[0] if len(v) == 1 else None)
        if v != ["PASS"]:
            bad.append("%s %s" % (switch, v))
    return [result("B19present", "FAIL" if bad else "PASS", figs, "; ".join(bad) or "all four present on their test builds")]


@judge("B19absent", "B")
def b19absent(F):
    # GI_INSTRUMENT_RXQ is judged from its own run 30 s after the QUIESCE probe (reviewer, 2026-10-17);
    # straight after it, it read undetermined twice on e3d9437.
    want = (("RESTART_GUARD_TEST", r"B19_absent_prod\.txt"), ("GI_INSTRUMENT_ABORTWIN", r"B19_absent_prod\.txt"),
            ("OTA_HEALTH_FAULT_INJECT", r"B19_absent_prod\.txt"), ("GI_INSTRUMENT_RXQ", r"B19_absent_prod_rxq_gap30\.txt"),
            ("TWAI_PORT_TXABORT_PROBE", r"B19_absent_prod_txabort\.txt"))
    figs, bad = {}, []
    for switch, pat in want:
        v = inert_verdicts(inert_rows(text(F.one(pat))), switch)
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
    # The requested scenarios are interlock_bench's stimulus table (one line per case asked for, before
    # any runs); each one's verdict is its "<case> <verdict>  (interlock_<case>_<stamp>.json)" line.
    # B20 never reads PASS while a requested scenario is unjudged: a VOID, INCONCLUSIVE or missing case
    # makes it VOID unless another is NOT AS SPECIFIED (gen-inhibit reviewer, 2026-11-19: run cb08 read
    # "1 of 1 AS SPECIFIED" PASS with 28 cases VOID, whose lines this pattern did not match).
    requested = re.findall(r"^(\S+) +\d+ frames +[\d.]+ s +sha ", t, re.M)
    got = {}
    for name, verdict in re.findall(r"^(\S+) +(.+?) +\(interlock_\S+\.json\)$", t, re.M):
        got[name] = verdict
    spec = [n for n in requested if got.get(n) == "AS SPECIFIED"]
    notspec = [n for n in requested if got.get(n) == "NOT AS SPECIFIED"]
    unjudged = ["%s (%s)" % (n, got.get(n, "no verdict line")) for n in requested
                if got.get(n) not in ("AS SPECIFIED", "NOT AS SPECIFIED")]
    extra = sorted(set(got) - set(requested))
    figs = {"cases": fig(len(requested)), "as_specified": fig(len(spec))}
    detail = "%d of %d AS SPECIFIED" % (len(spec), len(requested))
    if not requested or extra:
        return [result("B20", "ERROR", figs, "no stimulus table" if not requested
                       else "verdicts for cases not requested: %s" % ", ".join(extra))]
    if notspec:
        return [result("B20", "FAIL", figs, "%s; NOT AS SPECIFIED: %s" % (detail, ", ".join(notspec)))]
    if unjudged:
        return [result("B20", "VOID", figs, "%s; unjudged: %s" % (detail, "; ".join(unjudged)))]
    return [result("B20", "PASS", figs, detail)]


# The flash guard's PASS line from 2026-11-16 (spec 12.4's flash-write row, user 2026-11-16):
# the two car-data endpoints are not registered, 404 in every mode. bench_flash_guard.py prints
# it after "spec 5.1 item 3 holds on the device" only when every row passed.
B21_UNREGISTERED = "/store_car_data and /upload/car_data.json are not registered (404 in every mode)."


@judge("B21", "B")
def b21(F):
    bad, figs = [], {}
    # the device not reached through the run's ota block (baseline_b21.sh's gate, 2026-11-19; through
    # the ota block since topic (E)): NOT-RUN, with why
    nr = F.all(r"B21_not_run\.txt")
    if nr:
        return [result("B21", "NOT-RUN", figs, text(nr[0]).strip()[:300])]
    fg = text(F.one(r"B21_bench_flash_guard\.txt"))
    if "spec 5.1 item 3 holds on the device" not in fg:
        bad.append("flash guard")
    elif B21_UNREGISTERED not in fg:
        bad.append("flash guard: the car-data endpoints not shown unregistered (404 in every mode)")
    if "the OTA guard holds in every non-OFF mode" not in text(F.one(r"B21_bench_ota_guard\.txt")):
        bad.append("OTA guard")
    # the refusal judged at the client (spec 9.3, user 2026-11-19; topic (E)): ota_guard_client_judge's
    # verdict per body, FAIL, ERROR or VOID as it reads them (the reviewer's readings (s)-(w), answer to
    # b07f7984); how each connection ended is in its lines, recorded, not judged
    worst, notes, spec3 = "PASS", [], []
    for kind in ("multipart", "image"):
        cf = F.one(r"B21_client_%s_%s\.txt" % (kind, C7))
        m = re.search(r"^verdict (PASS|FAIL|ERROR|VOID): (\d+) reps, (\d+) PASS, (\d+) FAIL, (\d+) ERROR, "
                      r"(\d+) VOID, (\d+) with the whole 403 and its reason(.*)$", text(cf), re.M)
        if not m:
            v, n, whole = "ERROR", 0, 0
            notes.append("%s: no client verdict line" % kind)
        else:
            v, n, whole = m.group(1), int(m.group(2)), int(m.group(7))
            # a rep whose mode setter replied 200 while the page shows another mode: VOID in the
            # client's verdict, and its spec 3 finding count lifted to the front of this line when
            # above 0, so it can't sit unseen in the client file (the reviewer, answer to 48b1100e)
            f3 = re.search(r"; (\d+) spec 3 finding\(s\)\s*$", m.group(8))
            if f3 and int(f3.group(1)) > 0:
                spec3.append((kind, int(f3.group(1)), base(cf)))
            if v != "PASS":
                notes.append("%s %s (%s PASS, %s FAIL, %s ERROR, %s VOID of %d reps)" % (
                    kind, v, m.group(3), m.group(4), m.group(5), m.group(6), n))
        figs[kind + "_reps"] = fig(n)
        figs[kind + "_whole_403"] = fig(whole)
        worst = ruled_verdict([worst, v])
        notes.append("%s %d of %d with the whole 403 and its reason" % (kind, whole, n))
    lead = []
    if spec3:
        figs["spec3_findings"] = fig(sum(k for _b, k, _f in spec3))
        lead = ["%d spec 3 finding(s): %s" % (figs["spec3_findings"]["value"], ", ".join(
            "%s %d (%s)" % (b, k, f) for b, k, f in spec3))]
    v = "FAIL" if bad else worst
    return [result("B21", v, figs, "; ".join(lead + bad + notes) if v != "PASS"
                   else "both guards hold; " + "; ".join(notes))]


def e4_response(t):
    m = re.search(r"device response us: count=(\d+) min=(\d+) mean=(\d+) max=(\d+)", t)
    return tuple(int(v) for v in m.groups()) if m else None


@judge("B22", "B")
def b22(F):
    figs, bad, void, err = {}, [], [], []
    for arm in ("a", "b"):
        t = text(F.one(r"B22_case3_%s_man_%s\.txt" % (C7, arm)))
        # an arm whose reboot before it was not confirmed was never run (baseline_b22.sh, 2026-11-19:
        # run cb08's arms ran after unconfirmed reboots): judged on nothing. VOID, the bench's stimulus
        # (the gen-inhibit reviewer's answer to ba92acf0, 2026-11-19: F3 already voids an unconfirmed
        # reboot); INCONCLUSIVE from F3 until then.
        rb = re.search(r"^       - (the WiCAN's reboot before the arm was not confirmed.*)$", t, re.M)
        if rb:
            void.append("%s: not run (%s)" % (arm, rb.group(1)))
            continue
        p = e4_prov(t)
        r = e4_response(t)
        d = re.search(r"device: tx_ok=(\d+) tx_fail=(\d+) ctr_ok=\d+ ctr_bad=(\d+) tx_queued_behind=(\d+)", t)
        e = re.search(r"witness saw \d+ frames \([\d.]+/s\), (\d+) error frames", t)
        ld = re.search(r"carried load >= ([\d.]+)%", t)
        # Spec 12.1 (c5849685): an arm void for any reason on its list is never judged as a normal
        # arm, so it makes B22 VOID, whatever its figures show; a FAIL in the other arm, read fairly,
        # stands. Before, a void arm made B22 INCONCLUSIVE, or FAIL when its figures failed (step 1a's
        # acceptance, 2026-10-29). The old trail-window count of our zero-torque frames ("attribution
        # disagrees with the device:", voiding nothing since 2026-10-29) is dropped (e4_void); B22's
        # arms have no POST, so provenance's count against tx_ok is attribution (e4_attribution). A
        # reason on no entry is ERROR, for the gen-inhibit reviewer.
        listed, unlisted = e4_void(t)
        listed += e4_attribution(p)
        if unlisted:
            err.append("%s: a void reason on no spec 12.1 entry: %s" % (arm, "; ".join(unlisted)))
            continue
        if listed:
            void.append(arm + ": void (" + "; ".join(listed) + ")")
            continue
        if not (p and r and d and e and ld):
            bad.append(arm + ": a line is missing")
            continue
        if not prov_ok(p) or int(d.group(2)) or int(d.group(3)) or int(d.group(4)) or int(e.group(1)):
            bad.append(arm + " not as before")
        figs.update({"ours_%s" % arm: fig(p["dev"]), "response_us_max_%s" % arm: fig(r[3], "us"),
                     "response_us_mean_%s" % arm: fig(r[2], "us"), "tx_fail_%s" % arm: fig(int(d.group(2))),
                     "ctr_bad_%s" % arm: fig(int(d.group(3))), "error_frames_%s" % arm: fig(int(e.group(1))),
                     "load_pct_lower_%s" % arm: fig(float(ld.group(1)), "%")})
    if bad:
        return [result("B22", "FAIL", figs, "; ".join(bad + void + err))]
    if err:
        return [result("B22", "ERROR", figs, "; ".join(err + void))]
    if void:
        return [result("B22", "VOID", figs, "; ".join(void))]
    return [result("B22", "PASS", figs, "both arms: provenance clean, 0 tx_fail, 0 error frames")]


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
    figs, bad, void, err = {}, [], [], []
    for arm in ("idle_a", "idle_b", "loaded"):
        d = json.load(open(F.one(r"B26_rxq_%s\.json" % arm), encoding="utf-8"))
        a = (d.get("arms") or {}).get("idle" if arm != "loaded" else "loaded") or {}
        # Spec 12.1 (c5849685): an arm void for a reason on its list, as its run recorded it
        # (attach_provenance adds attribution over the same window), is not judged; the old
        # trail-window attribution reason is dropped (arm_void.reasons); a reason on no entry: ERROR
        listed, unlisted = AV.reasons(a)
        if unlisted:
            err.append("%s: a void reason on no spec 12.1 entry: %s" % (arm, "; ".join(unlisted)))
            continue
        if listed:
            void.append("%s (%s)" % (arm, "; ".join(listed)))
            continue
        rxq = a.get("device", {}).get("rxq")
        if not rxq or not rxq.get("samples"):
            bad.append(arm + ": no rxq samples")
            continue
        figs["max_backlog_" + arm] = fig(rxq["max"])
        figs["ge2_" + arm] = fig(dict(zip(rxq["ge_edges"], rxq["ge"])).get(2))
        figs["samples_" + arm] = fig(rxq["samples"])
    if err or bad:
        return [result("B26", "ERROR", figs, "; ".join(err + bad + void))]
    if void:
        return [result("B26", "VOID", figs, "arms VOID: %s" % "; ".join(void))]
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
    mode, line = host_mode(t)
    figs["host_mode"] = fig(mode)
    bad = ["missing step " + s for s in missing] + ["exit != 0: " + s for s in nonzero]
    for name in ("golden_failed", "invariant_violations", "passive_differing", "fuzz_violations", "spec_expect_failed"):
        if figs[name]["value"] != 0:
            bad.append("%s %s" % (name, figs[name]["value"]))
    if case26 and int(case26.group(1)) > int(case26.group(2)):
        bad.append("case 26 over its cap")
    return [result("H", "FAIL" if bad else "PASS", figs,
                   "%s; %s" % ("; ".join(bad) or "%d steps exit 0" % len(exits), line))]


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
