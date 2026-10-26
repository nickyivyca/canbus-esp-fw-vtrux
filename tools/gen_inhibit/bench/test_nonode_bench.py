#!/usr/bin/env python3
"""Harness test for nonode_bench.py: judge_arm, arm_verdict, longest_retry_run and the
0x592 key encoding, on made-up arm records. No hardware.

    py -3.14 -B tools/gen_inhibit/bench/test_nonode_bench.py [--mutants]

Prints ok/FAIL per check; exit 0 only with no FAIL. --mutants applies each edit in
MUTANTS to a copy of nonode_bench.py in the worktree's scratch/ and requires a FAIL line (a
crash is not a kill). Gen-inhibit tester, 2026-10-20.
"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("NONODE_BENCH") or os.path.join(HERE, "nonode_bench.py")
fails, oks = [], 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def load():
    spec = importlib.util.spec_from_file_location("nonode_bench_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def copies(n, gap_us, fid=0x7F1, data="11" * 8, t0=1000.0):
    return [(t0 + k * gap_us, fid, 8, data) for k in range(n)]


def reads(t_last):
    """unlatched at +0.05 ... +0.55 s, latched from +0.60 s; a key-on at +6.5 s clears it by +7.0 s."""
    out = []
    t = t_last + 0.05
    while t < t_last + 8.0:
        latched = t_last + 0.60 <= t < t_last + 6.9
        out.append({"t_send": t, "t_recv": t + 0.03, "page": True, "latched": latched, "live": not latched,
                    "tx_ok": 500 if t >= t_last + 0.1 else 499})
        t += 0.05
    return out


def base_arm():
    tl = 10.0
    return {"witness_silence": copies(50, 300.0), "answer_copies_before_close": 1, "answer_copies_total": 1,
            "answer_copy_times": [tl + 0.0006], "device_051_payloads_after_last_vcm": ["0800800000ff01"],
            "device_051_latched_phase": [], "vcm_duplicates_latched_phase": 0,
            "t_last_vcm": tl, "reads": reads(tl), "t_reopen": tl + 3.0, "t_keyoff": tl + 6.0, "t_keyon": tl + 6.5,
            "diag_after_reopen_not_stuck": 5, "key_decoded": {"off_phase": [0], "on_phase": [1], "back_phase": [1]}}


def tests():
    m = load()
    v = m.judge_arm(base_arm())
    check("a clean arm passes every item", all(x.startswith(("PASS", "ACKED")) for x in v.values()), v)
    check("a clean arm is a main arm that passed", m.arm_verdict(v) == ("main", True), m.arm_verdict(v))

    # the void check: retries, not cadence, and nothing from another node
    a = base_arm()
    a["witness_silence"] = copies(50, 300.0) + [(99999.0, 0x123, 8, "00" * 8)]
    check("void: a frame from another node in the silence", m.judge_arm(a)["void"].startswith("VOID"), m.judge_arm(a)["void"])
    a = base_arm()
    a["witness_silence"] = copies(9, 300.0)
    check("void: 9 copies are too few", m.judge_arm(a)["void"].startswith("VOID"))
    a["witness_silence"] = copies(10, 300.0)
    check("void: 10 copies are enough", m.judge_arm(a)["void"].startswith("PASS"))
    a["witness_silence"] = copies(50, 100000.0)
    check("void: 50 identical frames 100 ms apart are a cadence, not retries", m.judge_arm(a)["void"].startswith("VOID"))
    gmax = m.max_retry_gap_us(8)
    a["witness_silence"] = copies(20, gmax)
    check("void: copies at the largest retry gap count", m.judge_arm(a)["void"].startswith("PASS"), gmax)
    a["witness_silence"] = copies(20, gmax + 1)
    check("void: copies just past it do not", m.judge_arm(a)["void"].startswith("VOID"))
    a["witness_silence"] = copies(6, 300.0) + copies(6, 300.0, data="22" * 8, t0=5000.0)
    check("void: two short runs of different frames are not one run", m.judge_arm(a)["void"].startswith("VOID"))
    check("max retry gap is about two frame times", 2 * m.frame_time_us(8) < gmax < 3 * m.frame_time_us(8), gmax)

    # the answer and 0x051
    a = base_arm()
    a["answer_copies_total"], a["answer_copies_before_close"] = 40, 0
    v = m.judge_arm(a)
    check("answer: many copies is the answer-stuck case", v["answer"].startswith("ANSWER-STUCK") and
          m.arm_verdict(v) == ("answer-stuck", True), (v["answer"], m.arm_verdict(v)))
    a["answer_copy_times"] = [10.0 + k * 0.0003 for k in range(40)]
    check("answer-stuck: copies only before the latched read's bracket pass", m.judge_arm(a)["no051"].startswith("PASS"),
          m.judge_arm(a)["no051"])
    a["answer_copy_times"] = [10.0 + k * 0.0003 for k in range(40)] + [10.60]
    check("answer-stuck: a copy inside the first latched read's bracket is INCONCLUSIVE",
          m.judge_arm(a)["no051"].startswith("INCONCLUSIVE"), m.judge_arm(a)["no051"])
    a["answer_copy_times"] = [10.0 + k * 0.0003 for k in range(40)] + [10.60, 10.70]
    check("answer-stuck: a copy after the first latched read is NOT AS SPECIFIED",
          m.judge_arm(a)["no051"].startswith("NOT AS SPECIFIED"), m.judge_arm(a)["no051"])
    a = base_arm()
    a["answer_copies_before_close"], a["answer_copies_total"] = 0, 0
    check("answer: none seen is VOID", m.judge_arm(a)["answer"].startswith("VOID"))
    a = base_arm()
    a["device_051_payloads_after_last_vcm"] = ["aa", "bb"]
    check("no051: a second device 0x051 payload is NOT AS SPECIFIED", m.judge_arm(a)["no051"].startswith("NOT AS SPECIFIED"))

    # the latch: 0.7 s on the host clock, conservative, INCONCLUSIVE when straddled
    a = base_arm()
    a["reads"] = [r for r in a["reads"] if not (10.5 < r["t_send"] < 10.6)]
    check("latch: latched by 0.63 s passes", m.judge_arm(base_arm())["latch"].startswith("PASS"), m.judge_arm(base_arm())["latch"])
    a = base_arm()
    for r in a["reads"]:
        r["latched"] = 10.0 + 0.75 <= r["t_send"] < 16.9
    check("latch: unlatched on a read sent at 0.70 s is NOT AS SPECIFIED",
          m.judge_arm(a)["latch"].startswith("NOT AS SPECIFIED"), m.judge_arm(a)["latch"])
    a = base_arm()
    a["reads"] = [r for r in a["reads"] if not (10.62 < r["t_send"] < 10.72)]
    for r in a["reads"]:
        r["latched"] = 10.0 + 0.72 <= r["t_send"] < 16.9
    check("latch: a bracket 0.60-0.75 s is INCONCLUSIVE", m.judge_arm(a)["latch"].startswith("INCONCLUSIVE"),
          m.judge_arm(a)["latch"])
    a = base_arm()
    for r in a["reads"]:
        r["latched"] = False
    check("latch: never latched is NOT AS SPECIFIED", m.judge_arm(a)["latch"].startswith("NOT AS SPECIFIED"))
    a = base_arm()
    for r in a["reads"]:
        if r["t_send"] < 10.0:
            r["latched"] = True
    a["reads"].insert(0, {"t_send": 9.9, "t_recv": 9.93, "page": True, "latched": True})
    check("latch: a latched read before the last VCM command does not count",
          m.judge_arm(a)["latch"].startswith("PASS") and "0.63" in m.judge_arm(a)["latch"], m.judge_arm(a)["latch"])

    # nodes back, key-on
    a = base_arm()
    for r in a["reads"]:
        if 14.0 <= r["t_send"] < 14.1:
            r["latched"] = False
    check("back: an unlatched read with the nodes back and no key-on", m.judge_arm(a)["back"].startswith("NOT AS SPECIFIED"))
    a = base_arm()
    a["diag_after_reopen_not_stuck"] = 0
    check("back: no diag page after the return", m.judge_arm(a)["back"].startswith("NOT AS SPECIFIED"))
    a = base_arm()
    a["reads"] = [r for r in a["reads"] if not (13.0 <= r["t_send"] <= 16.0)]
    check("back: no read with the nodes back is VOID", m.judge_arm(a)["back"].startswith("VOID"))
    a = base_arm()
    for r in a["reads"]:
        if r["t_send"] >= 16.5:
            r["latched"] = True
    check("keyon: still latched after the key-on", m.judge_arm(a)["keyon"].startswith("NOT AS SPECIFIED"))
    a = base_arm()
    a["key_decoded"] = {"off_phase": [0, 1], "on_phase": [1]}
    check("keyon: key frames that did not decode as sent are VOID", m.judge_arm(a)["keyon"].startswith("VOID"))
    check("a VOID item makes the arm VOID", m.arm_verdict(m.judge_arm(a)) == ("VOID", False))

    # the latched phase: no device 0x051, two ways
    a = base_arm()
    a["device_051_latched_phase"] = [1.234]
    check("latched: a device 0x051 on the witness after the return is NOT AS SPECIFIED",
          m.judge_arm(a)["latched_wire"].startswith("NOT AS SPECIFIED"))
    check("latched: and the arm fails", m.arm_verdict(m.judge_arm(a)) == ("main", False), m.arm_verdict(m.judge_arm(a)))
    a = base_arm()
    for r in a["reads"]:
        if r["t_send"] >= 14.0:
            r["tx_ok"] = 501
    check("latched: tx_ok rising between the latch and the key-on is NOT AS SPECIFIED",
          m.judge_arm(a)["latched_tx"].startswith("NOT AS SPECIFIED"), m.judge_arm(a)["latched_tx"])
    a = base_arm()
    for r in a["reads"]:
        if r["t_send"] >= 16.6:
            r["tx_ok"] = 520
    check("latched: tx_ok rising after the key-on is not judged", m.judge_arm(a)["latched_tx"].startswith("PASS"),
          m.judge_arm(a)["latched_tx"])
    a = base_arm()
    for r in a["reads"]:
        r["tx_ok"] = None
    check("latched: no tx_ok read is VOID", m.judge_arm(a)["latched_tx"].startswith("VOID"))
    vcm = lambda c: bytes([0x08, 0x00, 0x80, 0xFF, 0x7F, c])
    seq = [(0.0, vcm(1)), (10000.0, vcm(2)), (20000.0, vcm(3))]
    check("provenance: a VCM stream alone has no device frame", m.device_051_by_provenance(seq) == ([], 0),
          m.device_051_by_provenance(seq))
    seq = [(0.0, vcm(1)), (500.0, vcm(2)), (10000.0, vcm(2)), (20000.0, vcm(3))]
    check("provenance: an answer before the VCM's next frame is the device's",
          m.device_051_by_provenance(seq) == ([500.0], 0), m.device_051_by_provenance(seq))
    seq = [(0.0, vcm(1)), (10000.0, vcm(2)), (10250.0, vcm(2)), (20000.0, vcm(3))]
    check("provenance: one VCM frame seen twice within 0.3 ms is a duplicate",
          m.device_051_by_provenance(seq) == ([], 1), m.device_051_by_provenance(seq))
    seq = [(0.0, vcm(1)), (9600.0, vcm(2)), (10000.0, vcm(2))]
    check("provenance: identical frames 0.4 ms apart count as the device's",
          m.device_051_by_provenance(seq) == ([9600.0], 0), m.device_051_by_provenance(seq))

    # the X2 gate (x2_hold.py pins), 2026-10-20: the tool beside this one, and the verdict, not a substring
    check("x2: x2_hold.py is the one beside nonode_bench", m.X2_HOLD == os.path.join(m.HERE, "x2_hold.py"), m.X2_HOLD)
    pl = "pins: TX (GPIO7) %s -- %s; RX (GPIO6) level None"
    for verdict, why, held in [("not driven, level unreadable", "GPIO7 input disabled (FUN_IE 0), so GPIO_IN says nothing", True),
                               ("recessive", "GPIO7 output not enabled and it reads 1: TXD idles recessive", True),
                               ("dominant", "GPIO7 is not driven but reads 0", False),
                               ("driven", "GPIO7's output driver is enabled (out_sel 1)", False),
                               ("unknown", "a register was not read", False)]:
        check("x2: %s is %s" % (verdict, "held" if held else "not held"), m.x2_held(pl % (verdict, why)) == held)
    check("x2: no pins line is not held", not m.x2_held("no pins line (rc 1)"))
    fake = os.path.join(gi_paths.scratch("nonode_bench"), "nonode_x2_fake.py")
    open(fake, "w").write("import sys\nprint('noise')\nprint('pins: TX (GPIO7) recessive -- x; RX (GPIO6) level 1' "
                          "if sys.argv[1:] == ['pins'] else 'wrong argv %s' % sys.argv[1:])\n")
    got = m.x2_pins("", tool=fake)
    os.remove(fake)
    check("x2: x2_pins asks for pins and returns the pins line", got == "pins: TX (GPIO7) recessive -- x; RX (GPIO6) level 1", got)
    # the run's verdict over its arms, main and answer-stuck alike
    def arm(kind, trip="error-frame rate exceeded", **items):
        v = {k: "PASS: x" for k in ("void", "no051", "latch", "back", "keyon", "latched_wire", "latched_tx")}
        v["answer"] = "ANSWER-STUCK: x" if kind == "answer-stuck" else "ACKED: x"
        v.update(items)
        k, ok = m.arm_verdict(v)
        return {"kind": k, "passed": ok, "verdicts": v, "recorded": {"trip": trip}}
    mains = [arm("main") for _ in range(3)]
    check("run: three passing main arms pass", m.run_verdict(mains, 3) == "PASS", m.run_verdict(mains, 3))
    check("run: two main arms are VOID", m.run_verdict(mains[:2], 3).startswith("VOID"), m.run_verdict(mains[:2], 3))
    r = m.run_verdict(mains + [arm("answer-stuck", no051="NOT AS SPECIFIED: 3 copies")], 3)
    check("run: a stuck arm NOT AS SPECIFIED is the run's verdict", r.startswith("NOT AS SPECIFIED"), r)
    r = m.run_verdict(mains + [arm("answer-stuck", no051="INCONCLUSIVE: 1 copy inside")], 3)
    check("run: a stuck arm INCONCLUSIVE makes the run INCONCLUSIVE", r.startswith("INCONCLUSIVE"), r)
    r = m.run_verdict(mains[:2] + [arm("main", latch="INCONCLUSIVE: bracket straddles")], 3)
    check("run: a straddled main arm is INCONCLUSIVE, not NOT AS SPECIFIED", r.startswith("INCONCLUSIVE"), r)
    r = m.run_verdict(mains[:2] + [arm("main", latch="NOT AS SPECIFIED: not latched")], 3)
    check("run: a main arm NOT AS SPECIFIED is the run's verdict", r.startswith("NOT AS SPECIFIED"), r)
    r = m.run_verdict(mains[:1] + [arm("main", latch="NOT AS SPECIFIED: x")], 3)
    check("run: NOT AS SPECIFIED outranks too few arms", r.startswith("NOT AS SPECIFIED"), r)
    r = m.run_verdict(mains[:2] + [arm("main", trip="bus lost -- no 0x051")], 3)
    check("run: main arms that disagree on the trip pass (recorded, not judged; 2026-10-25)", r == "PASS", r)
    r = m.run_verdict(mains + [arm("VOID", void="VOID: x")], 3)
    check("run: a VOID arm beside three main arms passes", r == "PASS", r)
    r = m.run_verdict(mains + [arm("answer-stuck", trip="bus lost -- no 0x051")], 3)
    check("run: a stuck arm's trip is not compared", r == "PASS", r)
    # --rejudge (2026-10-25): run_verdict again over a stored record, written beside it, the record unchanged
    rj = gi_paths.scratch("nonode_bench_rejudge")
    def rejudged(rec, name):
        src = os.path.join(rj, name + ".json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        before = open(src, "rb").read()
        rc = m.rejudge(src)
        dst = os.path.join(rj, name + "_rejudged.json")
        out = json.load(open(dst, encoding="utf-8")) if os.path.exists(dst) else {}
        return rc, out, open(src, "rb").read() == before
    flaky = mains[:2] + [arm("main", trip="bus lost -- no 0x051")]
    rc, out, same = rejudged({"verdict": "FLAKY: the main arms disagree", "args": {"arms": 3}, "arms": flaky}, "r1")
    check("rejudge: a trip-only FLAKY record rejudges PASS, exit 0", out.get("verdict") == "PASS" and rc == 0,
          (rc, out.get("verdict")))
    check("rejudge: the old verdict is kept beside the new", out.get("verdict_before") == "FLAKY: the main arms disagree",
          out.get("verdict_before"))
    check("rejudge: each arm's trip is listed", [a.get("trip") for a in out.get("arms", [])]
          == ["error-frame rate exceeded", "error-frame rate exceeded", "bus lost -- no 0x051"], out.get("arms"))
    check("rejudge: the record itself is not changed", same)
    rc, out, _ = rejudged({"verdict": "PASS", "args": {"arms": 4}, "arms": mains}, "r2")
    check("rejudge: the run's own --arms is used (3 main arms of 4 are VOID)",
          str(out.get("verdict")).startswith("VOID") and rc == 1, (rc, out.get("verdict")))
    rc, out, _ = rejudged({"verdict": "PASS", "arms": mains}, "r3")
    check("rejudge: no args means 3 arms", out.get("verdict") == "PASS", out.get("verdict"))
    rc, out, _ = rejudged({"verdict": "FLAKY: x", "args": {"arms": 3},
                           "arms": mains[:2] + [arm("main", trip="bus lost", latch="NOT AS SPECIFIED: not latched")]}, "r4")
    check("rejudge: an arm NOT AS SPECIFIED stays the verdict, exit 1",
          str(out.get("verdict")).startswith("NOT AS SPECIFIED") and rc == 1, (rc, out.get("verdict")))
    shutil.rmtree(rj, ignore_errors=True)
    ok_reboot, held = {"rc": 0, "line": "PASS"}, pl % ("recessive", "x")
    check("start: a confirmed reboot and a held X2 start", m.start_refusal({"reboot_start": ok_reboot, "x2_pins_start": held}) is None)
    r = m.start_refusal({"reboot_start": {"rc": 1, "line": "FAIL"}, "x2_pins_start": held})
    check("start: an unconfirmed reboot refuses", bool(r) and r[0] == "start reboot not confirmed", r)
    r = m.start_refusal({"reboot_start": ok_reboot, "x2_pins_start": pl % ("dominant", "GPIO7 is not driven but reads 0")})
    check("start: an X2 not held refuses", bool(r) and r[0] == "X2 not held", r)

    # the 0x592 encoding: cantools for the mapped bits, the rest as captured
    kf = m.KeyFrames()
    orig = bytes([0x10 | 0x02 | 0x01 | 0x08, 0x5A, 0, 0, 0x34, 0x12, 0xBE, 0xEF])
    off, on = kf.with_key(orig, 0), kf.with_key(orig, 1)
    check("key: encoded 0 decodes 0 and 1 decodes 1", kf.key_of(off) == 0 and kf.key_of(on) == 1, (off.hex(), on.hex()))
    check("key: only the key bit changes", off == bytes([orig[0] & ~0x10]) + orig[1:] and on == orig, (off.hex(), on.hex()))
    d0, d1 = kf.msg.decode(orig, decode_choices=False), kf.msg.decode(off, decode_choices=False)
    check("key: the other signals decode the same", all(d0[k] == d1[k] for k in d0 if k != "IgnitionKeyState"), (d0, d1))


MUTANTS = [
    ("another node's frame is ignored", "    v[\"void\"] = (\"VOID: a frame from another node in the silence: %s\" % \", \".join(others) if others else",
     "    v[\"void\"] = (\"VOID: a frame from another node in the silence: %s\" % \", \".join(others) if False else"),
    ("9 copies are enough", "COPIES_MIN = 10 ", "COPIES_MIN = 9 "),
    ("any gap counts as a retry", "and frames[j + 1][0] - frames[j][0] <= max_retry_gap_us(key[1])):",
     "and True):"),
    ("the retry gap is too loose", "return 2 * frame_time_us(dlc) + 25 * BIT_US", "return 20 * frame_time_us(dlc)"),
    ("the stuck answer's late copies are ignored", "late = sum(1 for t in times if t > hi)", "late = 0"),
    ("a stuck answer inside the bracket passes", "inside = sum(1 for t in times if lo < t <= hi)", "inside = 0"),
    ("the latched phase's wire is not judged", "if n_wire == 0 else", "if True else"),
    ("tx_ok rising is not judged", "if max(tx) == tx[0] else", "if True else"),
    ("tx_ok is read past the key-on", "and r[\"t_recv\"] < arm[\"t_keyon\"] and r.get(\"tx_ok\") is not None]",
     "and r.get(\"tx_ok\") is not None]"),
    ("the latched phase is not among the judged items", "judged = [\"no051\", \"latch\", \"back\", \"keyon\", \"latched_wire\", \"latched_tx\"]",
     "judged = [\"no051\", \"latch\", \"back\", \"keyon\"]"),
    ("provenance takes any pair", "if len(d0) >= 6 and len(d1) >= 6 and d0[5] == d1[5]]", "if len(d0) >= 6 and len(d1) >= 6]"),
    ("provenance drops no duplicate", "dups = [p for p in pairs if p[2] == p[3] and p[1] - p[0] <= DUP_MAX_US]", "dups = []"),
    ("provenance's duplicate window is too wide", "DUP_MAX_US = 300.0 ", "DUP_MAX_US = 600.0 "),
    ("a second device 0x051 is allowed", "if len(payloads) <= 1 else", "if len(payloads) <= 2 else"),
    ("the latch limit is 0.8 s", "LATCH_MAX_S = 0.7 ", "LATCH_MAX_S = 0.8 "),
    ("the straddled bracket passes", "\"PASS: latched by %.3f s (bracket %.3f-%.3f s)\" % (hi, lo, hi) if hi <= LATCH_MAX_S else",
     "\"PASS: latched by %.3f s (bracket %.3f-%.3f s)\" % (hi, lo, hi) if lo < LATCH_MAX_S else"),
    ("a latched read before the last command counts", "first = next((r for r in reads if r.get(\"latched\") and r[\"t_send\"] > t_last), None)",
     "first = next((r for r in reads if r.get(\"latched\")), None)"),
    ("back takes an unlatched read", "    unl = [r for r in back if r.get(\"page\") and not r.get(\"latched\")]\n",
     "    unl = []\n"),
    ("back takes no diag page", "if resumed < 1 else", "if resumed < 0 else"),
    ("keyon takes a latch that stays", "\"PASS: the latch cleared after the key-on\" if any(not r.get(\"latched\") for r in after) else",
     "\"PASS: the latch cleared after the key-on\" if True else"),
    ("the key frames are not checked", "    if keys.get(\"off_phase\") != [0] or keys.get(\"on_phase\") != [1]:\n",
     "    if False:\n"),
    ("an answer-stuck arm counts as main", "return (\"answer-stuck\" if v[\"answer\"].startswith(\"ANSWER-STUCK\") else \"main\"), ok",
     "return \"main\", ok"),
    ("the key bit mask is the whole frame", "mask |= ((1 << s.length) - 1) << s.start", "mask |= (1 << 64) - 1"),
    ("x2_hold.py is looked for under GI_FW_TOOLS", "X2_HOLD = os.path.join(HERE, \"x2_hold.py\")",
     "X2_HOLD = os.path.join(os.environ.get(\"GI_FW_TOOLS\", \"\"), \"x2_hold.py\")"),
    ("the X2 gate takes the substring", "    return bool(m) and m.group(1) in (\"recessive\", \"not driven, level unreadable\")\n",
     "    return \"not driven\" in line\n"),
    ("the X2 gate takes dominant", "m.group(1) in (\"recessive\", \"not driven, level unreadable\")",
     "m.group(1) in (\"recessive\", \"not driven, level unreadable\", \"dominant\")"),
    ("the X2 gate refuses recessive", "m.group(1) in (\"recessive\", \"not driven, level unreadable\")",
     "m.group(1) in (\"not driven, level unreadable\",)"),
    ("x2_pins takes the first line", "line = next((l for l in p.stdout.splitlines() if l.startswith(\"pins:\")), ",
     "line = next((l for l in p.stdout.splitlines()), "),
    ("x2_pins asks for hold", "[sys.executable, \"-B\", tool or X2_HOLD, \"pins\"]", "[sys.executable, \"-B\", tool or X2_HOLD, \"hold\"]"),
    ("the X2 start gate is not applied", "    if not x2_held(rec[\"x2_pins_start\"]):", "    if False:"),
    ("the run ignores answer-stuck arms", "judged = [x for x in arms if x[\"kind\"] in (\"main\", \"answer-stuck\")]",
     "judged = [x for x in arms if x[\"kind\"] in (\"main\",)]"),
    ("the run takes VOID arms as judged", "judged = [x for x in arms if x[\"kind\"] in (\"main\", \"answer-stuck\")]",
     "judged = [x for x in arms if x[\"kind\"] in (\"main\", \"answer-stuck\", \"VOID\")]"),
    ("INCONCLUSIVE is not looked for", "bad, unsure = items(\"NOT AS SPECIFIED\"), items(\"INCONCLUSIVE\")",
     "bad, unsure = items(\"NOT AS SPECIFIED\"), []"),
    ("too few arms outranks NOT AS SPECIFIED", "    if bad:\n        return \"NOT AS SPECIFIED: %s\" % \", \".join(bad)\n",
     "    if bad and len(mains) >= want:\n        return \"NOT AS SPECIFIED: %s\" % \", \".join(bad)\n"),
    ("the run takes fewer main arms", "    if len(mains) < want:\n", "    if len(mains) < want - 1:\n"),
    # the trip comparison went on 2026-10-25 (spec 12.4: recorded, not judged); these put it back
    ("main arms' trips are compared again", "    if unsure:\n",
     "    if len({x[\"recorded\"][\"trip\"] for x in mains}) > 1:\n        return \"FLAKY: trips\"\n    if unsure:\n"),
    ("rejudge writes over the record", "    dst = os.path.splitext(path)[0] + \"_rejudged.json\"", "    dst = path"),
    ("rejudge keeps the record's verdict", "           \"verdict\": run_verdict(rec[\"arms\"], want),",
     "           \"verdict\": rec.get(\"verdict\"),"),
    ("rejudge ignores the run's --arms", "    want = (rec.get(\"args\") or {}).get(\"arms\", 3)", "    want = 3"),
    ("rejudge exits 0 whatever the verdict", "    return 0 if out[\"verdict\"] == \"PASS\" else 1", "    return 0"),
    ("rejudge lists no trips", "\"trip\": (x.get(\"recorded\") or {}).get(\"trip\")", "\"trip\": None"),
    ("the reboot start gate is not applied", "    if rec[\"reboot_start\"][\"rc\"] != 0:", "    if False:"),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-nonode_bench")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, "nonode_bench.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, NONODE_BENCH=path), timeout=300)
        fl = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else (r.stderr.strip()[-120:] if r.stderr else "")))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    if "--mutants" in sys.argv:
        sys.exit(mutants())
    try:
        tests()
    except Exception as e:
        check("ran without raising", False, repr(e))
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    sys.exit(1 if fails else 0)
