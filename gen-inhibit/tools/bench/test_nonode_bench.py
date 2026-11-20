#!/usr/bin/env python3
"""Harness test for nonode_bench.py: judge_arm, arm_verdict, longest_retry_run and the
0x592 key encoding, on made-up arm records. No hardware.

    py -3.14 -B gen-inhibit/tools/bench/test_nonode_bench.py [--mutants]

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
    # a refusing tool: its rc and its own last line, so the record says why (run cb08, 2026-11-19)
    open(fake, "w").write("import sys\nprint('REFUSED: GI_ESPTOOL_ARGV is not set (nothing run, no record)')\n"
                          "sys.exit(2)\n")
    got = m.x2_pins("", tool=fake)
    os.remove(fake)
    check("x2: a refusing x2_hold gives its rc and its refusal, and is not held",
          got == "no pins line (rc 2): REFUSED: GI_ESPTOOL_ARGV is not set (nothing run, no record)"
          and not m.x2_held(got), got)
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
    gi_paths.remove_tree(rj)
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

    close_tests(m)


def close_msgs(m, last_at=10.0, late_at=None, epoch=False, n=60, dev=True, lat=0.0):
    """Witness messages for close_measure: n device frames 2 ms apart, ending 1 ms before the last VCM frame at
    last_at (payload "aa"), the PCAN's device clock 5000 s behind perf (RAW), its receipt lat after
    the wire (device_fit maps onto receipt, so a mapped time carries the median latency); with epoch, stamps on the host's epoch clock (COOKED); late_at: a replayer frame (0x123)
    on the wire then."""
    EPOCH = 1.79e9
    out = []
    def msg(wire, fid, data, err=False):
        p = wire + lat
        ts = (EPOCH + p) if epoch else (wire - 5000.0)
        return (p, ts * 1e6, fid, 8, data, err, EPOCH + p)
    for k in range(n):
        out.append(msg(last_at - (n - k) * 0.002 - 0.001, 0x7F1 if dev else 0x123, "11" * 8))
    out.append(msg(last_at, m.VCM_ID, "aa"))
    if late_at is not None:
        out.append(msg(late_at, 0x123, "00" * 8))
    out.sort()
    return out


def close_tests(m):
    """Condition 5 (2026-11-18): the arm's close on the PCAN's own stamps."""
    cm = m.close_measure(close_msgs(m), 10.005, 13.0, "aa")
    check("close: the shutdown called 5 ms after the last VCM frame -> within the bounds, no breach",
          not cm.get("void") and cm["breaches"] == [] and abs(cm["gap_ms"] - 5.0) < 0.01 and cm["probe"] == "not sent",
          cm)
    for gap_ms, ok in ((7.9, True), (8.2, False), (2.1, True), (1.8, False), (30.0, False)):
        cm = m.close_measure(close_msgs(m), 10.0 + gap_ms / 1000.0, 13.0, "aa")
        check("close: the shutdown %g ms after the last VCM frame -> %s" % (gap_ms, "no breach" if ok else "a breach"),
              (cm["breaches"] == []) == ok, cm["breaches"])
    cm = m.close_measure(close_msgs(m, lat=0.001), 10.005, 13.0, "aa")
    check("close: a 1 ms delivery latency maps the last frame 1 ms later (gap 4 ms, no breach)",
          abs(cm["gap_ms"] - 4.0) < 0.01 and cm["breaches"] == [], cm)
    cm = m.close_measure(close_msgs(m, late_at=10.030), 10.005, 13.0, "aa")
    check("close: a replayer frame 25 ms after the close -> a breach", any("canmain frames" in b for b in cm["breaches"]),
          cm["breaches"])
    cm = m.close_measure(close_msgs(m, late_at=10.020), 10.005, 13.0, "aa")
    check("close: a replayer frame 15 ms after the close (inside the silence edge) -> no breach", cm["breaches"] == [],
          cm["breaches"])
    msgs = close_msgs(m)
    msgs.append((10.030, (10.030 - 5000.0) * 1e6, 0x7F1, 8, "22" * 8, False, 1.79e9 + 10.030))
    cm = m.close_measure(msgs, 10.005, 13.0, "aa")
    check("close: a device frame (0x7F1) 25 ms after the close is not the replayer's -> no breach",
          cm["breaches"] == [] and cm["late"] == 0, cm["breaches"])
    msgs = [x if x[2] != m.VCM_ID else (x[0] + 0.006,) + x[1:] for x in close_msgs(m, n=600)]
    cm = m.close_measure(msgs, 10.005, 13.0, "aa")
    check("close: the last VCM frame received 6 ms late is placed by its stamp (gap 5 ms), not its receipt",
          cm["breaches"] == [] and abs(cm["gap_ms"] - 5.0) < 0.01, cm)
    cm = m.close_measure(close_msgs(m, late_at=13.5), 10.005, 13.0, "aa")
    check("close: a replayer frame after the reopen -> no breach", cm["breaches"] == [], cm["breaches"])
    msgs = close_msgs(m)
    msgs.append((13.5, (13.5 - 5000.0) * 1e6, m.VCM_ID, 8, "aa", False, 1.79e9 + 13.5))
    cm = m.close_measure(msgs, 10.005, 13.0, "aa")
    check("close: a frame with the last payload after the reopen is not the last frame", cm["breaches"] == []
          and abs(cm["gap_ms"] - 5.0) < 0.01, cm["breaches"])
    msgs = close_msgs(m)
    msgs.append((10.010, (10.010 - 5000.0) * 1e6, m.VCM_ID, 8, "aa", False, 1.79e9 + 10.010))
    cm = m.close_measure(msgs, 10.005, 13.0, "aa")
    check("close: the last payload on the wire 5 ms after the shutdown call -> a breach (closed before it)",
          any("not 5 +/- 3 ms" in b for b in cm["breaches"]), cm["breaches"])
    msgs = close_msgs(m)
    msgs.append((10.010, (10.010 - 5000.0) * 1e6, m.VCM_ID, 8, "bb", False, 1.79e9 + 10.010))
    cm = m.close_measure(msgs, 10.005, 13.0, "aa")
    check("close: a 0x051 of another payload after the call is not the last frame", cm["breaches"] == [], cm["breaches"])
    cm = m.close_measure(close_msgs(m), 10.005, 13.0, "bb")
    check("close: no VCM frame with the last payload -> a breach (no last frame)",
          any("no last canmain frame" in b for b in cm["breaches"]), cm["breaches"])
    cm = m.close_measure(close_msgs(m, epoch=True), 10.005, 13.0, "aa")
    check("close: COOKED stamps (the epoch clock) -> the fit VOID, nothing measured",
          bool(cm.get("void")) and "epoch" in cm["void"] and "breaches" not in cm, cm.get("void"))
    cm = m.close_measure([x[:6] for x in close_msgs(m)], 10.005, 13.0, "aa")
    check("close: messages without the epoch -> the fit VOID (no point)", bool(cm.get("void")), cm.get("void"))
    a = base_arm()
    a["close_measure"] = m.close_measure(close_msgs(m), 10.005, 13.0, "aa")
    v = m.judge_arm(a)
    check("close item: within the bounds -> PASS, and the arm still a main arm that passed",
          v.get("close", "").startswith("PASS") and m.arm_verdict(v) == ("main", True), (v.get("close"), m.arm_verdict(v)))
    a["close_measure"] = m.close_measure(close_msgs(m), 10.030, 13.0, "aa")
    v = m.judge_arm(a)
    check("close item: closed 30 ms after -> VOID naming the breach, the arm VOID (not NOT AS SPECIFIED)",
          v.get("close", "").startswith("VOID: burst timing VOID: close:") and m.arm_verdict(v) == ("VOID", False)
          and not any(x.startswith("NOT") for x in v.values()), (v.get("close"), m.arm_verdict(v)))
    a["close_measure"] = m.close_measure(close_msgs(m, epoch=True), 10.005, 13.0, "aa")
    v = m.judge_arm(a)
    check("close item: a VOID fit -> VOID: PCAN clock, the arm VOID",
          v.get("close", "").startswith("VOID: VOID: PCAN clock") and m.arm_verdict(v) == ("VOID", False), v.get("close"))
    v = m.judge_arm(base_arm())
    check("close item: an older record with no close measure has no close item", "close" not in v, v.get("close"))


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
    ("x2_pins drops the tool's refusal, as in run cb08",
     "        line = \"no pins line (rc %s): %s\" % (p.returncode, said[-1] if said else \"no output\")",
     "        line = \"no pins line (rc %s)\" % p.returncode"),
    ("x2_pins asks for hold","[sys.executable, \"-B\", tool or X2_HOLD, \"pins\"]", "[sys.executable, \"-B\", tool or X2_HOLD, \"hold\"]"),
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
    # condition 5 (2026-11-18): the close measured on the PCAN's own stamps
    ("the close item ignores a breach", "                      \"VOID: %s\" % BM.breach_line([cm]) if cm[\"breaches\"] else",
     "                      \"VOID: %s\" % BM.breach_line([cm]) if False else"),
    ("the close item ignores a VOID fit", "        v[\"close\"] = (\"VOID: %s\" % WC.pcan_void_verdict(cm[\"void\"]) if cm.get(\"void\") else",
     "        v[\"close\"] = (\"VOID: %s\" % WC.pcan_void_verdict(cm[\"void\"]) if False else"),
    ("a VOID fit is measured anyway", "    if fit[\"void\"]:\n        return {\"void\": fit[\"void\"], \"fit\": fit}\n", ""),
    ("the last frame taken past the silence edge", "\n                and t < t_close_call + BM.SILENCE_EDGE_S),", "),"),
    ("the last frame of any payload", "m[2] == VCM_ID and m[4] == last_vcm_payload\n", "m[2] == VCM_ID\n"),
    ("late frames from the close, not the silence edge", "t_close_call + BM.SILENCE_EDGE_S <= t < t_reopen", "t_close_call <= t < t_reopen"),
    ("late frames past the reopen", "t_close_call + BM.SILENCE_EDGE_S <= t < t_reopen", "t_close_call + BM.SILENCE_EDGE_S <= t"),
    ("device frames count as late", "if m[2] not in DEVICE_IDS and t_close_call", "if t_close_call"),
    ("receipt time, not the mapped stamp", "    data = [(WC.mapped(fit, m[1] / 1e6), m) for m in msgs if not m[5]]",
     "    data = [(m[0], m) for m in msgs if not m[5]]"),
]


def echo_tests(m):
    """The replayer's echo drain, canbench's from can-tools 7a27cc9 (the gen-inhibit reviewer's rulings (C) and
    (c), 2026-11-19): replayer_open asks for it, and slice_close and back_stop stop it through the REAL
    kvaser_bus.drain_stop, with the REAL Player, on _fake_gs_usb's StallingGs (no frame with 256 echoes unread,
    as canmain's gs_usb on the bench) running the pinned canbench's reader as open_bench(drain=True) does."""
    import threading
    import time
    import _fake_gs_usb as FG
    spec = importlib.util.spec_from_file_location("kvaser_bus_echo_nn", os.path.join(HERE, "kvaser_bus.py"))
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)
    evs = []
    kb.record = evs.append
    stim = [(k * 0.0003, m.VCM_ID if k % 20 == 19 else 0x100 + k % 7, bytes([k % 256]) * 8) for k in range(200)]

    class Inno:
        def __init__(self):
            self.calls = []

        def open(self, bitrate=500000, **kw):
            self.calls.append((bitrate, kw))
            return "bus"
    inno = Inno()
    got = m.replayer_open(inno)
    check("echo: the replayer opens canmain at 500 kbit with canbench's drain (inno.open(drain=True))",
          got == "bus" and inno.calls == [(500000, {"drain": True})], inno.calls)

    g = FG.drained(FG.StallingGs(cap=256), kb=kb)
    pl = m.player_start(g, stim, None, "nonode slice", KB=kb)
    time.sleep(0.4)
    t_stop = []
    real_stop = kb.drain_stop

    def timed_stop(bus, sent, what):
        t_stop.append(time.perf_counter())
        return real_stop(bus, sent, what)
    kb.drain_stop = timed_stop
    last, echoes, t_call, t_close = m.slice_close(pl, g, KB=kb)
    kb.drain_stop = real_stop
    check("echo: the slice sent more than the fake's cap with no stall and no failed send",
          pl.sent > 256 and pl.fail == 0 and g.stalls == 0, (pl.sent, pl.fail, g.stalls))
    check("echo: the slice's drain counted one echo per frame sent, recorded as an echoes event for canmain",
          echoes.get("sent") == pl.sent == echoes.get("echoes") and echoes.get("echoes_equal_sent") is True
          and echoes.get("role") == "canmain" and echoes.get("what") == "nonode slice" and evs[-1] == echoes, echoes)
    check("echo: the slice's drain stop time recorded (canbench's stop_ms)",
          isinstance(echoes.get("stop_ms"), (int, float)), echoes)
    check("echo: the drain stopped CLOSE_AFTER_S - DRAIN_LEAD_S after the last VCM frame's send, before the close "
          "(not running at the shutdown)",
          last is not None and t_stop and m.CLOSE_AFTER_S - m.DRAIN_LEAD_S - 1e-4 <= t_stop[0] - last[0]
          < m.CLOSE_AFTER_S and t_stop[0] < t_call and g.drain_alive_at_shutdown is False,
          (last, t_stop, t_call, g.drain_alive_at_shutdown))
    check("echo: the close called CLOSE_AFTER_S after the last VCM frame's send (and promptly)",
          last is not None and m.CLOSE_AFTER_S - 1e-4 <= t_call - last[0] < m.CLOSE_AFTER_S + 0.05
          and t_close >= t_call, (last, t_call))

    g2 = FG.drained(FG.StallingGs(cap=256), kb=kb)
    pl2 = m.player_start(g2, stim, None, "nonode back", KB=kb)
    time.sleep(0.3)
    t0 = time.perf_counter()
    e2 = m.back_stop(pl2, g2, KB=kb)
    took = time.perf_counter() - t0
    running = bool(g2.bench_drain.get("running"))
    g2.shutdown()
    check("echo: back_stop stops the Player, then (after ECHO_SETTLE_S) its drain, counts recorded",
          not pl2.is_alive() and not running and pl2.fail == 0 and took >= m.ECHO_SETTLE_S
          and e2.get("sent") == pl2.sent == e2.get("echoes") and pl2.sent > 256 and e2.get("what") == "nonode back",
          (e2, pl2.fail, running, took))

    # canbench's stop reads nothing after it: an echo that comes back 2 ms after its frame is lost to a stop made
    # at once, and counted by back_stop's settle
    class LateEchoGs(FG.StallingGs):
        def send(self, msg, timeout=None):
            FG.StallingGs.send(self, msg, timeout)
            with self.lock:
                e = self.q.pop()
            threading.Timer(0.002, lambda: self.put(e)).start()
    g4 = FG.drained(LateEchoGs(cap=256), kb=kb)
    pl4 = m.player_start(g4, stim, None, "nonode back", KB=kb)
    time.sleep(0.1)
    e4 = m.back_stop(pl4, g4, KB=kb)
    check("echo: echoes 2 ms behind their frames are all counted by back_stop (its settle before the stop)",
          e4.get("sent") == pl4.sent == e4.get("echoes") and pl4.sent > 0, e4)

    g3 = FG.drained(FG.StallingGs(cap=256, acked=lambda n: False), kb=kb)
    pl3 = m.player_start(g3, stim, None, "nonode back", KB=kb)
    time.sleep(0.1)
    e3 = m.back_stop(pl3, g3, KB=kb)
    check("echo: with nothing ACKing, no echoes: recorded as fewer echoes than frames sent (not judged)",
          e3.get("echoes") == 0 and e3.get("sent") == pl3.sent > 0 and e3.get("echoes_equal_sent") is False, e3)

    # LEFTOVER FRAMES (the gen-inhibit reviewer's Q1 (i), 2026-11-19): a leftover echo at the open must not make up
    # for an unACKed frame (acked_<phase>, the unsafe side)
    def run_slice(acked, leftover, snapshot=True):
        g = FG.StallingGs(cap=256, acked=acked)
        if leftover:
            g.put(FG.Msg(0x6A9, b"\x01", is_rx=False))      # handed up by the device at the open
        FG.drained(g, kb=kb)
        if snapshot:
            p = m.player_start(g, stim, None, "nonode slice", KB=kb)
        else:                       # a Player started as before player_start, no snapshot taken
            p = m.Player(g, stim, None)
            p.start()
        time.sleep(0.15)
        return m.slice_close(p, g, KB=kb)[1], p
    ok_back = {"sent": 400, "echoes": 400, "rx": 0, "errors": 0, "echoes_equal_sent": True}
    del evs[:]
    e5, pl5 = run_slice(lambda n: True, True)
    a = base_arm()
    a.update(driver_running=True, echoes_slice=e5, echoes_back=ok_back)
    v5 = m.judge_arm(a)
    bs = e5.get("before_send") or {}
    check("echo: a leftover echo at the open -> in canbench's before_send (player_start settles before the slice's "
          "first frame), kind echo, its frame (id 0x6a9), the echoes after it == frames sent, the arm not voided by it",
          bs.get("echoes") == 1 and bs.get("kind") == ["echo"] and [f.get("id") for f in bs.get("frames") or []]
          == [0x6A9] and (e5.get("at_settle") or {}).get("what") == "nonode slice"
          and e5.get("echoes") == pl5.sent > 0 and e5["drained"]["echoes"] == pl5.sent + 1
          and "acked_slice" not in v5 and [x.get("event") for x in evs] == ["before_send", "echoes"], (e5, v5, evs))
    e6, pl6 = run_slice(lambda n: n != 3, True)
    a = base_arm()
    a.update(driver_running=True, echoes_slice=e6, echoes_back=ok_back)
    v6 = m.judge_arm(a)
    check("echo: CAN-FAIL a leftover echo and one unACKed frame -> one echo short after the snapshot, the arm VOID "
          "on acked_slice (the whole count would have matched)",
          e6.get("echoes") == pl6.sent - 1 and e6["drained"]["echoes"] == pl6.sent
          and v6.get("acked_slice", "").startswith("VOID: %d echoes for %d" % (pl6.sent - 1, pl6.sent))
          and m.arm_verdict(v6)[0] == "VOID", (e6, v6.get("acked_slice")))
    e7, _pl7 = run_slice(lambda n: True, True, snapshot=False)
    a = base_arm()
    a.update(driver_running=True, echoes_slice=e7, echoes_back=ok_back)
    v7 = m.judge_arm(a)
    check("echo: a Player started with no drain_before_send -> the drain's stop names it, the arm VOID on acked_slice",
          "no wait for the open's frames (drain_before_send)" in str(e7.get("error"))
          and v7.get("acked_slice", "").startswith("VOID")
          and "no wait for the open's frames (drain_before_send)" in v7["acked_slice"], (e7, v7.get("acked_slice")))


def precondition_tests(m):
    """The stimulus's precondition, VOID items with their reasons (the gen-inhibit reviewer's rulings of 2026-11-19):
    (i) the WiCAN's driver never showed running within the wait, (ii) fewer echoes than frames in either phase."""
    ok_e = lambda n: {"sent": n, "echoes": n, "rx": 0, "errors": 0, "echoes_equal_sent": True}
    a = base_arm()
    a.update(driver_running=True, echoes_slice=ok_e(900), echoes_back=ok_e(400))
    v = m.judge_arm(a)
    check("precondition: driver running and every frame echoed in both phases: no VOID item, a main arm that passed",
          m.arm_verdict(v) == ("main", True) and not any(k in v for k in ("stimulus", "acked_slice", "acked_back")), v)
    a = base_arm()
    a.update(driver_running=False, echoes_slice=ok_e(900), echoes_back=ok_e(400))
    v = m.judge_arm(a)
    check("precondition (i): the driver never showed running -> the arm VOID, its stimulus item naming why",
          m.arm_verdict(v)[0] == "VOID" and v.get("stimulus", "").startswith("VOID") and "running" in v["stimulus"]
          and "5 s" in v["stimulus"], v.get("stimulus"))
    for phase in ("slice", "back"):
        a = base_arm()
        a.update(driver_running=True, echoes_slice=ok_e(900), echoes_back=ok_e(400))
        a["echoes_" + phase] = dict(ok_e(900), echoes=850, echoes_equal_sent=False)
        v = m.judge_arm(a)
        check("precondition (ii): 850 echoes for 900 frames in the %s -> the arm VOID, naming the counts" % phase,
              m.arm_verdict(v)[0] == "VOID" and v.get("acked_" + phase, "").startswith("VOID: 850 echoes for 900")
              and ("acked_back" if phase == "slice" else "acked_slice") not in v, v)
    # more echoes than frames, counted from the first send (the gen-inhibit reviewer's answer to 878d06fc, 2026-11-20)
    leftover = {"echoes": 1, "rx": 0, "errors": 0, "kind": ["echo"],
                "frames": [{"kind": "echo", "id": 0x6A9, "extended": False, "data": "01"}]}
    for phase in ("slice", "back"):
        a = base_arm()
        a.update(driver_running=True, echoes_slice=ok_e(900), echoes_back=ok_e(400))
        n = 900 if phase == "slice" else 400
        a["echoes_" + phase] = dict(ok_e(n), echoes=n + 2, echoes_equal_sent=False, before_send=leftover)
        v = m.judge_arm(a)
        check("CAN-FAIL more echoes: %d echoes for %d frames in the %s -> the arm VOID, the item naming the counts, "
              "the extra count and before_send's frames" % (n + 2, n, phase),
              m.arm_verdict(v)[0] == "VOID"
              and v.get("acked_" + phase, "").startswith("VOID: %d echoes for %d frames sent in the %s: 2 more echoes "
                                                         "than frames sent" % (n + 2, n, phase))
              and v["acked_" + phase].endswith("before the first send: 1 echoes, 0 rx, 0 error frames "
                                               "(frames echo 0x6a9 [01])")
              and ("acked_back" if phase == "slice" else "acked_slice") not in v, v)
    a = base_arm()
    a.update(driver_running=True, echoes_slice=dict(ok_e(900), echoes=901, echoes_equal_sent=False), echoes_back=ok_e(400))
    v = m.judge_arm(a)
    check("more echoes by one, no before_send recorded -> VOID, '1 more', before_send 'not recorded'",
          v.get("acked_slice", "").startswith("VOID: 901 echoes for 900 frames sent in the slice: 1 more echoes")
          and v["acked_slice"].endswith("before the first send: not recorded"), v.get("acked_slice"))
    a = base_arm()
    a.update(driver_running=True, echoes_slice=dict(ok_e(900), before_send=dict(leftover)), echoes_back=ok_e(400))
    v = m.judge_arm(a)
    check("echoes == frames with a leftover in before_send -> no acked item (the leftover stays out of the count)",
          "acked_slice" not in v and m.arm_verdict(v) == ("main", True), v)
    check("before_send_text: an extended frame marked x, an rx frame, no frames 'none'",
          m.before_send_text({"echoes": 0, "rx": 1, "errors": 0, "frames": [
              {"kind": "rx", "id": 0x18FEF100, "extended": True, "data": ""}]})
          == "0 echoes, 1 rx, 0 error frames (frames rx 0x18fef100x [])"
          and m.before_send_text({"echoes": 0, "rx": 0, "errors": 0, "frames": []})
          == "0 echoes, 0 rx, 0 error frames (frames none)", None)
    a = base_arm()
    a.update(driver_running=True, echoes_slice=dict(ok_e(900), echoes=0, error="CanError: recv after shutdown"),
             echoes_back=ok_e(400))
    v = m.judge_arm(a)
    check("precondition (ii): a failed drain -> VOID naming the failure",
          v.get("acked_slice", "").startswith("VOID") and "drain failed" in v["acked_slice"], v.get("acked_slice"))
    v = m.judge_arm(base_arm())
    check("precondition: an older record (neither field) gets neither item",
          not any(k in v for k in ("stimulus", "acked_slice", "acked_back")), v)


def witness_tests(m):
    """The PCAN witness reads on past a bus state python-can's pcan raises from recv (2026-11-19: error passive
    killed canmain_burst_check's witness on the bench)."""
    import time
    import types as T

    class FakePcan:
        def __init__(self):
            self.script = ["f", "f", "raise", "f", "f", "f"]

        def recv(self, timeout=None):
            if not self.script:
                time.sleep(timeout or 0.0)
                return None
            k = self.script.pop(0)
            if k == "raise":
                raise RuntimeError("Bus error: The CAN controller is error passive")
            return T.SimpleNamespace(timestamp=time.perf_counter(), arbitration_id=0x123, dlc=1, data=b"\x01",
                                     is_error_frame=False)
    w = m.Witness(FakePcan())
    w.start()
    time.sleep(0.3)
    w.stop.set()
    w.join(2)
    check("witness: a bus state raised from recv is recorded and the witness reads on (5 frames, 1 state)",
          len(w.msgs) == 5 and len(w.states) == 1 and "error passive" in w.states[0][1] and not w.is_alive(),
          (len(w.msgs), w.states))


MUTANTS += [
    ("(i) the driver's precondition not judged", "    if arm.get(\"driver_running\") is False:\n",
     "    if False:\n"),
    ("(ii) the echo counts not judged",
     "        if e.get(\"error\") or e.get(\"echoes\") is None or e.get(\"sent\") is None or e[\"echoes\"] < e[\"sent\"]:\n",
     "        if e.get(\"error\"):\n"),
    ("(ii) the back phase's echoes not judged", '    for phase in ("slice", "back"):\n', '    for phase in ("slice",):\n'),
    ("the witness stops at a bus state raised from recv",
     "                    self.states.append((time.perf_counter(), \"%s: %s\" % (type(e).__name__, e)))\n"
     "                time.sleep(0.001)\n",
     "                    self.states.append((time.perf_counter(), \"%s: %s\" % (type(e).__name__, e)))\n"
     "                return\n"),
    ("the witness's bus states not kept",
     "                    self.states.append((time.perf_counter(), \"%s: %s\" % (type(e).__name__, e)))\n",
     "                    pass\n"),
    # the replayer's echo drain, canbench's from can-tools 7a27cc9 (the gen-inhibit reviewer, 2026-11-19)
    ("the replayer opened without canbench's drain", "    return inno.open(bitrate=500000, drain=True)\n",
     "    return inno.open(bitrate=500000)\n"),
    ("the slice's drain not stopped before its close (left to the close)",
     '    echoes = KB.drain_stop(kv, pl.sent, "nonode slice")\n    time.sleep(max(0.0, t_last + CLOSE_AFTER_S - time.perf_counter()))\n'
     "    t_close_call = time.perf_counter()     # condition 5's measure: from the shutdown call\n    kv.shutdown()\n",
     "    time.sleep(max(0.0, t_last + CLOSE_AFTER_S - time.perf_counter()))\n"
     "    t_close_call = time.perf_counter()     # condition 5's measure: from the shutdown call\n    kv.shutdown()\n"
     '    echoes = KB.drain_stop(kv, pl.sent, "nonode slice")\n'),
    ("the slice's drain stopped at the last frame, no wait for its echoes",
     "    time.sleep(max(0.0, t_last + CLOSE_AFTER_S - DRAIN_LEAD_S - time.perf_counter()))\n", ""),
    ("the back phase's drain stopped with no settle", "    time.sleep(ECHO_SETTLE_S)\n", ""),
    ("the back phase's drain not stopped", '    return KB.drain_stop(kv, pl.sent, "nonode back")\n',
     "    return {}\n"),
    # leftover frames at the open (the gen-inhibit reviewer's Q1 (i), 2026-11-19)
    ("player_start takes no snapshot", "    _kb(KB).drain_before_send(kv, what)\n", ""),
    ("player_start takes the snapshot after the Player starts",
     "    _kb(KB).drain_before_send(kv, what)\n    pl = Player(kv, stim, kf)\n    pl.start()\n",
     "    pl = Player(kv, stim, kf)\n    pl.start()\n    _kb(KB).drain_before_send(kv, what)\n"),
    ("player_start starts no Player", "    pl.start()\n    return pl\n", "    return pl\n"),
    ("the slice's echoes judged only when fewer is dropped (any count passes)",
     ' or e["echoes"] < e["sent"]:', ' or False:'),
    # more echoes than frames sent (the gen-inhibit reviewer's answer to 878d06fc, 2026-11-20)
    ("more echoes than frames not judged", '        elif e["echoes"] > e["sent"]:\n', '        elif False:\n'),
    ("more echoes judged only from two extra", '        elif e["echoes"] > e["sent"]:\n',
     '        elif e["echoes"] > e["sent"] + 1:\n'),
    ("the extra count not written", '                                   % (e["echoes"], e["sent"], phase, e["echoes"] - e["sent"],\n',
     '                                   % (e["echoes"], e["sent"], phase, 0,\n'),
    ("before_send's frames not written", '                                      before_send_text(e.get("before_send"))))\n',
     '                                      before_send_text(None)))\n'),
    ("before_send_text drops the extended mark",
     '"x" if f.get("extended") else ""', '""'),
    ("before_send_text drops the frames",
     '                                           f.get("data") or "") for f in bs.get("frames") or []) or "none"\n',
     '                                           f.get("data") or "") for f in []) or "none"\n'),
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
        gi_paths.remove_tree(d)
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
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    if "--mutants" in sys.argv:
        sys.exit(mutants())
    try:
        tests()
    except Exception as e:
        check("ran without raising", False, repr(e))
    try:
        echo_tests(load())
    except Exception as e:      # noqa: BLE001
        check("echo tests ran without raising", False, repr(e))
    try:
        precondition_tests(load())
    except Exception as e:      # noqa: BLE001
        check("precondition tests ran without raising", False, repr(e))
    try:
        witness_tests(load())
    except Exception as e:      # noqa: BLE001
        check("witness tests ran without raising", False, repr(e))
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    sys.exit(1 if fails else 0)
