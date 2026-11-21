#!/usr/bin/env python3
"""Harness test for arm_void.py and provenance_classifier.rejudge's spec 12.1 fields
(gen-inhibit tester, 2026-11-19; spec 12.1 at c5849685: attribution, counted over the
same window from the arm, joins the void reasons).

Written from spec 12.1's list and the e4 arm record (post_schedule, post_index,
device.tx_ok, void_reasons), not from the device's code:
  - every reason e4_device_calibration.run_arm records maps to its 12.1 entry, and run_arm
    records no reason this test does not know (a new one fails here until it is mapped);
  - a reason on no entry is kept apart (unlisted), never mapped;
  - the old whole-arm attribution reason is dropped, and a recorded same-window one only
    when the caller counted again;
  - the arm is the last mode POST (scheduled, else --arm-after, else the replay's start),
    placed by its replay frame; what the witness counts is what tx_ok counts in the mode
    that POST set (RESPOND: 0x7F0; INHIBIT: the device's 0x051; otherwise nothing);
  - rejudge, with the real classifier on synthetic replays: a RESPOND arm POSTed into
    INHIBIT agrees from the POST although the whole-arm counts differ (the e3d9437 B3
    case), disagrees when tx_ok is off by one, and an INHIBIT arm's count against tx_ok
    is attribution, not the classifier's UNRESOLVED.

    python3.13 -B gen-inhibit/tools/bench/test_arm_void.py [--mutants]

--mutants copies arm_void.py and provenance_classifier.py into the worktree's scratch/,
one edit per copy, and runs this test on it (ARM_VOID_DIR); each must fail by a check.
"""
import inspect
import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("ARM_VOID_DIR"):
    sys.path.insert(0, os.environ["ARM_VOID_DIR"])
import arm_void as AV                     # noqa: E402
import gi_paths                           # noqa: E402
import provenance_classifier as P         # noqa: E402
import witness_clock as WC                # noqa: E402
import e4_device_calibration as E4        # noqa: E402  start_lag_reason's text (2026-11-20)

FAILS = []
OKS = [0]


def check(name, fn, info=None):
    """fn: a callable (raising counts as failing) or a value already worked out."""
    try:
        ok = fn() if callable(fn) else fn
    except Exception as e:      # noqa: BLE001  a check that raises is a failed check, said so
        ok, info = False, "%s: %s" % (type(e).__name__, e)
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %s" % (info,)))
    if ok:
        OKS[0] += 1
    else:
        FAILS.append(name)


# ---------------------------------------------------------------- the list

SAMPLES = [
    (WC.clock_void_reason("SocketCAN (test)"), "clock"),
    ("the witness process: reader died (exit code 1)", "witness_log"),
    ("witness log could not be written: [Errno 28] No space left on device", "witness_log"),
    ("no witness log directory was configured", "witness_log"),
    (WC.window_void({"replayed_frames": 100, "replayed_found": 10})[0], "witness_log"),
    ("3 replay frames were refused by the adapter, so the stimulus is incomplete", "replay_refused"),
    ("384 of 8998 commands (4.27 %) never reached the wire despite refused=0", "stimulus_short"),
    ("the device did not answer at the end of the arm", "device_silent"),
    # B22's replay start (e4_device_calibration.start_lag_reason, 2026-11-20)
    (E4.start_lag_reason(3.374), "stimulus_short"),
    (E4.start_lag_reason(0.0051), "stimulus_short"),
    (E4.start_lag_reason(None), "stimulus_short"),
]
# the opening words of each reason run_arm records, as its source writes them
RUN_ARM_WORDS = ["WC.clock_void_reason(", "the witness process: ", "witness log could not be written: ",
                 "no witness log directory was configured", "replay frames were refused by the adapter",
                 "never reached the wire despite", "the device did not answer at the end of the arm",
                 "void_reasons.append(lag_why)"]


def att(mode="inhibit", witness=4, tx=5, agree=False):
    return {"from": "scheduled POST 1 (at 20.0 s, mode 3)", "index": 7, "start_s": 1.0, "mode": mode,
            "counted_as": "device 0x051 (provenance)", "witness": witness, "device_tx_ok": tx, "agree": agree}


def list_cases():
    check("LIST is spec 12.1's six entries, attribution last",
          AV.KINDS == ("clock", "witness_log", "replay_refused", "stimulus_short", "device_silent", "attribution"))
    for s, k in SAMPLES:
        check("kind: %r -> %s" % (s[:50], k), lambda s=s, k=k: AV.kind(s) == k, AV.kind(s))
    check("kind: attribution over the same window -> attribution",
          AV.kind(AV.attribution_reason(att())) == "attribution")
    for s in ("attribution disagrees with the device: tx_ok=1497, witness attributes 2000",
              "the WiCAN's reboot before the arm was not confirmed in mode 0: no page",
              "the moon was in the wrong phase", ""):
        check("kind: %r is on no entry" % s[:50], lambda s=s: AV.kind(s) is None, AV.kind(s))
    import e4_device_calibration as E
    src = inspect.getsource(E.run_arm)
    n = src.count("void_reasons.append(")
    check("run_arm records exactly the %d reasons this test maps (a new one must be mapped here)"
          % len(RUN_ARM_WORDS), lambda: n == len(RUN_ARM_WORDS), n)
    for w in RUN_ARM_WORDS:
        check("run_arm still writes %r" % w, lambda w=w: w in src)


def reasons_cases():
    old = "attribution disagrees with the device: tx_ok=1497, witness attributes 2000"
    same = AV.attribution_reason(att())
    clock = SAMPLES[0][0]
    check("reasons: the old whole-arm attribution reason is dropped, the rest kept in order",
          AV.reasons({"void_reasons": [old, clock, SAMPLES[5][0]]}) == ([clock, SAMPLES[5][0]], []))
    check("reasons: a reason on no entry is kept apart, never mapped",
          AV.reasons({"void_reasons": ["the moon was in the wrong phase", clock]})
          == ([clock], ["the moon was in the wrong phase"]))
    check("reasons: extra reasons are added and None dropped",
          AV.reasons({"void_reasons": []}, [None, SAMPLES[4][0]]) == ([SAMPLES[4][0]], []))
    check("reasons: a recorded same-window reason is kept when nothing counts again",
          AV.reasons({"void_reasons": [same]}) == ([same], []))
    check("reasons: ... and dropped when the caller counted again (its count in extra)",
          AV.reasons({"void_reasons": [same]}, [None], recounted=True) == ([], []))
    check("reasons: no void_reasons -> nothing", AV.reasons({}) == ([], []))
    check("attribution_reason: a disagreement names both counts and the window",
          same.startswith(AV.ATTRIBUTION) and "tx_ok=5" in same and "counts 4 device 0x051" in same
          and "scheduled POST 1" in same, same)
    check("attribution_reason: agreement, nothing to compare, or no count -> None",
          AV.attribution_reason(att(witness=5, agree=True)) is None
          and AV.attribution_reason(att(mode="passive", witness=None, agree=None)) is None
          and AV.attribution_reason(None) is None)


def window_cases():
    d = {"mode": "respond"}
    rep = [0.0, 0.5, 1.0, 1.5, 2.0]
    ps = [{"at_s": 10.0, "mode": 1, "index": 1}, {"at_s": 20.0, "mode": 3, "index": 3}]
    check("window: the last scheduled POST, in the mode it set; no POST send on record -> its replay frame's "
          "wire time, no completion",
          AV.window(d, {"post_schedule": ps}, rep) == {"from": "scheduled POST 2 (at 20.0 s, mode 3)", "index": 3,
                                                       "start_s": 1.5, "complete_s": None,
                                                       "placed_by": AV.PLACED_FRAME, "mode": "inhibit"},
          AV.window(d, {"post_schedule": ps}, rep))
    check("window: a scheduled POST never sent (index None) is not the arm",
          AV.window(d, {"post_schedule": [ps[0], dict(ps[1], index=None)]}, rep)["index"] == 1)
    check("window: the --arm-after POST, in the run's mode, when nothing was scheduled",
          AV.window({"mode": "inhibit"}, {"post_schedule": [], "post_index": 2}, rep)
          == {"from": "the --arm-after POST", "index": 2, "start_s": 1.0, "complete_s": None,
              "placed_by": AV.PLACED_FRAME, "mode": "inhibit"})
    check("window: neither -> from the start of the replay, in the run's mode",
          AV.window(d, {}, rep) == {"from": "the start of the replay", "index": None, "start_s": None,
                                    "complete_s": None, "placed_by": None, "mode": "respond"})
    check("window: a POST past the replay frames the witness places -> ValueError",
          raises(lambda: AV.window(d, {"post_index": 9}, rep), ValueError))
    # t_sent from the POST's own send, mapped onto the witness's epoch clock (base_epoch + post_s[0]),
    # t_complete that plus its post_s span (the gen-inhibit reviewer's rulings of 2026-11-20). Here the
    # send (-18.6 + 20.0071 = 1.4071) is before its replay frame's wire time (1.5), as in run C.
    ps2 = [dict(ps[0], post_s=[10.0, 10.5]), dict(ps[1], post_s=[20.0071, 20.0462])]
    w = AV.window(d, {"post_schedule": ps2, "base_epoch": -18.6}, rep)
    check("window: start_s is the last scheduled POST's own send, base_epoch + post_s[0] (1.4071), not its frame",
          lambda: abs(w["start_s"] - 1.4071) < 1e-9 and w["placed_by"] == AV.PLACED_POST and w["index"] == 3, w)
    check("window: complete_s is that send plus the POST's post_s span (1.4071 + 0.0391)",
          lambda: abs(w["complete_s"] - 1.4462) < 1e-9, w)
    w2 = AV.window({"mode": "inhibit"}, {"post_index": 2, "post_s": [5.0, 5.04], "base_epoch": -4.1}, rep)
    check("window: ... the --arm-after POST's own send and post_s (0.9, 0.94)",
          lambda: abs(w2["start_s"] - 0.9) < 1e-9 and abs(w2["complete_s"] - 0.94) < 1e-9, w2)
    w3 = AV.window({"mode": "inhibit"}, {"post_s": [5.0, 5.04], "base_epoch": -4.1}, rep)
    check("window: ... no POST, no window (a stray post_s with no post_index is not the arm's)",
          lambda: w3["start_s"] is None and w3["complete_s"] is None and w3["index"] is None, w3)
    for what, extra in (("no base_epoch", {}), ("base_epoch not a number", {"base_epoch": "1.0"}),
                        ("base_epoch a bool", {"base_epoch": True})):
        check("window: post_s but %s -> placed by its replay frame, no completion" % what,
              lambda extra=extra: (lambda w: w["start_s"] == 1.5 and w["complete_s"] is None
                                   and w["placed_by"] == AV.PLACED_FRAME)(
                  AV.window(d, dict({"post_schedule": ps2}, **extra), rep)))
    for what, pst in (("no reply (None)", [20.0, None]), ("completion before the send", [20.05, 20.0]),
                      ("one time", [20.0]), ("not numbers", ["20.0", "20.04"]), ("missing", None)):
        check("window: post_s %s -> placed by its replay frame, no completion" % what,
              lambda pst=pst: (lambda w: w["complete_s"] is None and w["start_s"] == 1.5
                               and w["placed_by"] == AV.PLACED_FRAME)(
                  AV.window(d, {"post_schedule": [dict(ps[1], post_s=pst)], "base_epoch": -18.6}, rep)))


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    return False


def attribution_cases():
    rep = [0.0, 1.0, 2.0, 3.0]
    wire = [(0.5, 0x7F0, b""), (1.5, 0x7F0, b""), (2.0, 0x7F0, b""), (2.5, 0x7F0, b""), (2.6, 0x051, b"")]
    a = {"post_schedule": [{"at_s": 20.0, "mode": 2, "index": 2}], "device": {"tx_ok": 2}}
    r = AV.attribution({"mode": "inhibit"}, a, wire, [], rep)
    check("attribution: RESPOND from the POST counts the 0x7F0 at or after it (2.0 and 2.5), not before",
          r["witness"] == 2 and r["agree"] is True and r["counted_as"] == "probe 0x7F0", r)
    a = {"post_schedule": [{"at_s": 20.0, "mode": 3, "index": 2}], "device": {"tx_ok": 3}}
    r = AV.attribution({"mode": "respond"}, a, wire, [1.9, 2.0, 2.1, 2.9], rep)
    check("attribution: INHIBIT from the POST counts provenance's device 0x051 at or after it",
          r["witness"] == 3 and r["agree"] is True and r["counted_as"] == "device 0x051 (provenance)", r)
    a["device"]["tx_ok"] = 4
    r = AV.attribution({"mode": "respond"}, a, wire, [1.9, 2.0, 2.1, 2.9], rep)
    check("attribution: tx_ok one over the count from the POST -> disagree",
          r["agree"] is False and r["device_tx_ok"] == 4, r)
    r = AV.attribution({"mode": "inhibit"}, {"device": {"tx_ok": 4}}, wire, [1.9, 2.0, 2.1, 2.9], rep)
    check("attribution: no POST -> the whole arm is the window", r["witness"] == 4 and r["agree"] is True, r)
    for m in (4, 1, 0):
        r = AV.attribution({"mode": "inhibit"}, {"post_schedule": [{"mode": m, "index": 1}], "device": {"tx_ok": 7}},
                           wire, [1.9], rep)
        check("attribution: mode %d puts nothing on the wire that tx_ok counts -> agree None" % m,
              lambda r=r: r["agree"] is None and r["witness"] is None, r)
    check("attribution: no tx_ok (or not a number) -> None",
          AV.attribution({"mode": "inhibit"}, {"device": {}}, wire, [], rep) is None
          and AV.attribution({"mode": "inhibit"}, {"device": {"tx_ok": "5"}}, wire, [], rep) is None
          and AV.attribution({"mode": "inhibit"}, {}, wire, [], rep) is None)
    # The ruling (the gen-inhibit reviewer, answer to the run (C) report, 2026-11-20): the witness
    # counts from t_sent and from t_complete; the device's count between the two agrees, outside
    # stays void. POST sent at 2.0 (base_epoch -18 + post_s 20.0; its replay frame 2 also at 2.0),
    # 0.04 s to complete (t_complete 2.04): device 0x051 at 1.9 | 2.0 2.01 2.03 | 2.1 2.9 -> 5 from
    # the send, 2 from the completion.
    dev = [1.9, 2.0, 2.01, 2.03, 2.1, 2.9]
    for tx, want in ((1, False), (2, True), (3, True), (5, True), (6, False)):
        a = {"post_schedule": [{"at_s": 20.0, "mode": 3, "index": 2, "post_s": [20.0, 20.04]}],
             "base_epoch": -18.0, "device": {"tx_ok": tx}}
        r = AV.attribution({"mode": "respond"}, a, wire, dev, rep)
        check("attribution: counts 5 from t_sent, 2 from t_complete; tx_ok %d -> %s" % (
            tx, "agree" if want else "void"),
            lambda r=r, want=want: r["agree"] is want and r["witness"] == 5 and r["witness_from_complete"] == 2, r)
    a = {"post_schedule": [{"at_s": 20.0, "mode": 2, "index": 2, "post_s": [20.0, 20.55]}], "base_epoch": -18.0,
         "device": {"tx_ok": 1}}
    r = AV.attribution({"mode": "inhibit"}, a, wire, [], rep)
    check("attribution: RESPOND's probes too: 2 from t_sent (2.0, 2.5), 0 from t_complete (2.55); tx_ok 1 agrees",
          lambda: r["agree"] is True and r["witness"] == 2 and r["witness_from_complete"] == 0, r)
    a = {"post_index": 2, "post_s": [5.0, 5.04], "base_epoch": -3.0, "device": {"tx_ok": 2}}
    r2 = AV.attribution({"mode": "inhibit"}, a, wire, dev, rep)
    check("attribution: the --arm-after POST's completion too: 5 from the send, 2 from the completion; tx_ok 2 agrees",
          lambda: r2["agree"] is True and r2["witness"] == 5 and r2["witness_from_complete"] == 2, r2)
    a = {"post_schedule": [{"at_s": 20.0, "mode": 3, "index": 2}], "device": {"tx_ok": 3}}
    r3 = AV.attribution({"mode": "respond"}, a, wire, dev, rep)
    check("attribution: no completion on record -> both counts from the send (5), tx_ok 3 void",
          lambda: r3["agree"] is False and r3["witness"] == 5 and r3["witness_from_complete"] == 5, r3)
    # run C's B24 shape (2026-11-20): the POST sent at 1.97 (base_epoch -18.03), before its replay
    # frame's wire time (2.0); the device's first 0x051 at 1.98, between the two. Counted from the
    # send: 6 (1.98 on), from the completion (2.01): 4; tx_ok 6 agrees. Placed by the frame, as before
    # the ruling, the count from the send would be 5 and the arm void.
    dev_c = [1.9, 1.98, 2.0, 2.01, 2.03, 2.1, 2.9]
    a = {"post_schedule": [{"at_s": 20.0, "mode": 3, "index": 2, "post_s": [20.0, 20.04]}], "base_epoch": -18.03,
         "device": {"tx_ok": 6}}
    r4 = AV.attribution({"mode": "respond"}, a, wire, dev_c, rep)
    check("attribution: run C's shape: a device 0x051 after the POST's send, before its replay frame, is counted; "
          "6 from the send, 4 from the completion, tx_ok 6 agrees",
          lambda: r4["agree"] is True and r4["witness"] == 6 and r4["witness_from_complete"] == 4
          and r4["placed_by"] == AV.PLACED_POST, r4)
    r5 = AV.attribution({"mode": "respond"}, dict(a, base_epoch=None), wire, dev_c, rep)
    check("attribution: ... the same arm with no base_epoch is placed by its frame: 5 from it, tx_ok 6 void",
          lambda: r5["agree"] is False and r5["witness"] == 5 and r5["placed_by"] == AV.PLACED_FRAME, r5)
    a = {"post_schedule": [{"at_s": 20.0, "mode": 3, "index": 2, "post_s": [20.0, 20.04]}], "base_epoch": -18.0,
         "device": {"tx_ok": 6}}
    want = (AV.ATTRIBUTION + "the device reports tx_ok=6, the witness counts 5 device 0x051 (provenance) from "
            "scheduled POST 1 (at 20.0 s, mode 3)'s send and 2 from its completion")
    check("attribution_reason: with a completion it names the count from the send and from the completion",
          lambda: (lambda s: s == want and AV.kind(s) == "attribution")(
              AV.attribution_reason(AV.attribution({"mode": "respond"}, a, wire, dev, rep))))


# ---------------------------------------------------------------- rejudge, end to end

def replay(n=40):
    """A replay of n VCM 0x051 commands 10 ms apart (counter in byte 5's low nibble) with a
    filler frame between each: [(t, id, data, ext)] in send order."""
    out = []
    for k in range(n):
        out.append((k * 0.010, 0x051, bytes([0x08, 0, 0, 0, 0, k & 0x0F, 0, 0]), False))
        out.append((k * 0.010 + 0.005, 0x100, bytes([k & 0xFF]), False))
    return out


def on_wire(src, respond_upto=0, inhibit_from=None, stray_051_at=None):
    """The witness: each replayed frame 0.1 ms after its send, a 0x7F0 probe 0.5 ms after each
    command below respond_upto, the device's 0x051 answer (counter + 1) 1 ms after each command
    from inhibit_from on."""
    wire = []
    for t, i, d, _x in src:
        wire.append((t + 0.0001, i, d))
        if i != 0x051:
            continue
        k = int(round(t / 0.010))
        if k < respond_upto:
            wire.append((t + 0.0005, 0x7F0, b"\x00"))
        if inhibit_from is not None and k >= inhibit_from:
            wire.append((t + 0.001, 0x051, bytes([0x08, 0, 0, 0, 0, (k + 1) & 0x0F, 0, 0])))
        if stray_051_at is not None and k == stray_051_at:
            wire.append((t + 0.002, 0x051, bytes([0x08, 0, 0, 0, 0, (k + 1) & 0x0F, 0, 0])))
    return sorted(wire, key=lambda x: x[0])


def rejudge_with(rec, src, wire):
    import json
    work = gi_paths.scratch("arm-void-rejudge")
    path = os.path.join(work, "rec.json")
    rec = dict({"capture": os.path.join(work, "cap.log"), "window_start": 0, "seconds": 1}, **rec)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    fake_e4 = types.ModuleType("e4_device_calibration")
    fake_e4.load_window = lambda seconds: list(src)
    saved = (sys.modules.get("e4_device_calibration"), P.read_witness, P.gi_paths.verify)
    sys.modules["e4_device_calibration"] = fake_e4
    P.read_witness = lambda p: list(wire)
    P.gi_paths.verify = lambda p: None
    try:
        return P.rejudge(path, "idle")
    finally:
        if saved[0] is None:
            sys.modules.pop("e4_device_calibration", None)
        else:
            sys.modules["e4_device_calibration"] = saved[0]
        P.read_witness, P.gi_paths.verify = saved[1], saved[2]
        gi_paths.remove_tree(work)


def arm(mode, tx_ok, post=None, reasons=()):
    a = {"device": {"tx_ok": tx_ok}, "witness_raw": "w.log.gz", "void": bool(reasons), "void_reasons": list(reasons)}
    if post is not None:
        a["post_schedule"] = [{"at_s": 0.2, "mode": post[1], "index": post[0]}]
    return {"mode": mode, "arms": {"idle": a}}


def rejudge_cases():
    src = replay()
    post = (40, 3)                    # replay frame 40 = command 20: POSTed into INHIBIT there
    wire = on_wire(src, respond_upto=20, inhibit_from=20)
    old = "attribution disagrees with the device: tx_ok=20, witness attributes 20"
    r = rejudge_with(arm("respond", 20, post, [old]), src, wire)
    check("rejudge: a RESPOND arm POSTed into INHIBIT mid-replay (the e3d9437 B3 case) agrees from the POST: "
          "20 device 0x051 against tx_ok 20, though the whole arm also holds 20 probes",
          r["attribution"]["agree"] is True and r["attribution"]["witness"] == 20
          and r["attribution"]["start_s"] is not None and r["arm_void"] == [] and r["arm_unlisted"] == [], r)
    r = rejudge_with(arm("respond", 21, post), src, wire)
    check("rejudge: ... tx_ok 21 -> void on attribution, with the count kept",
          len(r["arm_void"]) == 1 and r["arm_void"][0].startswith(AV.ATTRIBUTION)
          and r["attribution"]["agree"] is False, (r["arm_void"], r["attribution"]))
    stale = AV.ATTRIBUTION + "the device reports tx_ok=20, the witness counts 19 device 0x051 (provenance) from x"
    r = rejudge_with(arm("respond", 20, post, [stale]), src, wire)
    check("rejudge: a same-window reason the record carries is replaced by the count made again (agreeing here)",
          r["arm_void"] == [] and r["attribution"]["agree"] is True, (r["arm_void"], r["attribution"]))
    r = rejudge_with(arm("respond", 20, post, [old, "the moon was in the wrong phase"]), src, wire)
    check("rejudge: a reason on no entry comes back as arm_unlisted",
          r["arm_unlisted"] == ["the moon was in the wrong phase"] and r["arm_void"] == [], r)
    wire_i = on_wire(src, inhibit_from=0)
    r = rejudge_with(arm("inhibit", 41), src, wire_i)
    check("rejudge: an INHIBIT arm's device 0x051 (40) against tx_ok (41) is attribution, not the "
          "classifier's UNRESOLVED", r["verdict"] == "as specified" and r["device_cmd_tx"] == 41
          and len(r["arm_void"]) == 1 and r["arm_void"][0].startswith(AV.ATTRIBUTION), (r["verdict"], r["arm_void"]))
    r = rejudge_with(arm("inhibit", 40), src, wire_i)
    check("rejudge: ... 40 against 40 agrees, nothing void", r["verdict"] == "as specified" and r["arm_void"] == [],
          (r["verdict"], r["arm_void"]))
    wire_r = on_wire(src, respond_upto=40, stray_051_at=10)
    r = rejudge_with(arm("respond", 40), src, wire_r)
    check("rejudge: a device 0x051 in a RESPOND arm is still the classifier's (behaviour, not attribution)",
          r["verdict"] == "UNRESOLVED" and r["arm_void"] == [], (r["verdict"], r["arm_void"]))
    short = [w for w in wire if w[0] < 0.1]
    r = rejudge_with(arm("respond", 20, post), src, short)
    check("rejudge: the witness short of the replay -> void on it, attribution not counted",
          r["attribution"] is None and len(r["arm_void"]) == 1 and AV.kind(r["arm_void"][0]) == "witness_log",
          (r["attribution"], r["arm_void"]))
    check("rejudge: the frame lists are dropped unless asked for",
          "device_times" not in r and "replayed_wire_times" not in r)


# ---------------------------------------------------------------- mutants

MUTANTS = [
    ("arm_void.py", "the stimulus-short reason on no entry",
     '    (re.compile(r"\\d+ of \\d+ commands \\([\\d.]+ %\\) never reached the wire"), "stimulus_short"),\n', ""),
    ("arm_void.py", "B22's start-lag reason on no entry",
     '    (re.compile(r"the replay\'s first frame went out [\\d.]+ ms behind its own schedule"), "stimulus_short"),\n',
     ""),
    ("arm_void.py", "the no-frame reason on no entry",
     '    (re.compile(r"the replay sent no frame, so its start against its schedule is not known\\Z"), '
     '"stimulus_short"),\n', ""),
    ("arm_void.py", "the device-silent reason on no entry",
     '    (re.compile(r"the device did not answer at the end of the arm\\Z"), "device_silent"),\n', ""),
    ("arm_void.py", "the first scheduled POST taken as the arm", "        e = posts[-1]\n", "        e = posts[0]\n"),
    ("arm_void.py", "the --arm-after POST ignored", '    if a.get("post_index") is not None:\n', "    if False:\n"),
    ("arm_void.py", "a frame at the arm not counted", "frm is None or t >= frm)", "frm is None or t > frm)"),
    ("arm_void.py", "the whole arm counted, not from the arm", "return sum(1 for t in times if frm is None or t >= frm)",
     "return len(times)"),
    ("arm_void.py", "RESPOND counts every frame, not the probes", "times = [t for t, i, _d in wire if i == PROBE_ID]",
     "times = [t for t, i, _d in wire]"),
    # the t_sent / t_complete ruling (the gen-inhibit reviewer, answer to the run (C) report, 2026-11-20)
    ("arm_void.py", "agreement only on the count from the send (before the ruling)",
     "agree=n_complete <= tx_ok <= n_sent)", "agree=tx_ok == n_sent)"),
    ("arm_void.py", "agreement only on the count from the completion",
     "agree=n_complete <= tx_ok <= n_sent)", "agree=tx_ok == n_complete)"),
    ("arm_void.py", "anything at or below the count from the send agrees",
     "agree=n_complete <= tx_ok <= n_sent)", "agree=tx_ok <= n_sent)"),
    ("arm_void.py", "anything at or above the count from the completion agrees",
     "agree=n_complete <= tx_ok <= n_sent)", "agree=n_complete <= tx_ok)"),
    ("arm_void.py", "no completion ever placed",
     '"complete_s": sent + post_elapsed(a),', '"complete_s": None,'),
    # t_sent from the POST's own send (the gen-inhibit reviewer's rulings of 2026-11-20)
    ("arm_void.py", "t_sent placed by the replay frame (before the ruling)",
     '"start_s": sent, "complete_s": sent + post_elapsed(a),',
     '"start_s": rep_t[idx], "complete_s": rep_t[idx] + post_elapsed(a),'),
    ("arm_void.py", "base_epoch ignored (post_s[0] alone)", "    return be + arm_post_s(a)[0]\n",
     "    return arm_post_s(a)[0]\n"),
    ("arm_void.py", "the POST's completion taken as its send", "    return be + arm_post_s(a)[0]\n",
     "    return be + arm_post_s(a)[1]\n"),
    ("arm_void.py", "a bool base_epoch accepted", " or isinstance(be, bool):\n", ":\n"),
    ("arm_void.py", "no POST send on record counts the whole arm",
     '"start_s": rep_t[idx], "complete_s": None, "placed_by": PLACED_FRAME,',
     '"start_s": None, "complete_s": None, "placed_by": PLACED_FRAME,'),
    ("arm_void.py", "the placement not recorded", '"placed_by": PLACED_POST, "mode": mode}', '"mode": mode}'),
    ("arm_void.py", "the completion from the first scheduled POST",
     'return posts[-1].get("post_s") if posts', 'return posts[0].get("post_s") if posts'),
    ("arm_void.py", "the --arm-after POST's post_s ignored",
     'return posts[-1].get("post_s") if posts else a.get("post_s")', 'return posts[-1].get("post_s") if posts else None'),
    ("arm_void.py", "a completion before the send accepted", " and ps[1] >= ps[0]):", "):"),
    ("arm_void.py", "the reason names only the count from the send",
     '    if att.get("complete_s") is not None:\n', "    if False:\n"),
    ("arm_void.py", "PASSIVE counted as INHIBIT", '    elif w["mode"] == "inhibit":\n', "    elif True:\n"),
    ("arm_void.py", "the old whole-arm reason kept",
     "           if not str(r).startswith(OLD_ATTRIBUTION) and", "           if True and"),
    ("arm_void.py", "a recorded same-window reason dropped without a recount",
     'not (recounted and kind(r) == "attribution")', 'kind(r) != "attribution"'),
    ("arm_void.py", "a reason on no entry mapped", "        (listed if kind(r) else unlisted).append(str(r))\n",
     "        listed.append(str(r))\n"),
    ("arm_void.py", "agreement read as a disagreement", '    if att is None or att["agree"] is not False:\n',
     "    if att is None:\n"),
    ("provenance_classifier.py", "the classifier keeps INHIBIT's count against tx_ok (UNRESOLVED)",
     'None if win_mode == "inhibit" else device_cmd_tx', "device_cmd_tx"),
    ("provenance_classifier.py", "the classifier never checks the device's count (RESPOND's 0 too)",
     'None if win_mode == "inhibit" else device_cmd_tx', "None"),
    ("provenance_classifier.py", "attribution not counted again",
     'AV.reasons(a, wv + [AV.attribution_reason(res["attribution"])],', "AV.reasons(a, wv,"),
    ("provenance_classifier.py", "a recorded same-window reason kept beside the count made again",
     "                                                      recounted=True)",
     "                                                      recounted=False)"),
    ("provenance_classifier.py", "the frame lists kept",
     '    if not keep_frames:\n        for k in ("vcm_times",', '    if False:\n        for k in ("vcm_times",'),
]


def mutants():
    base = gi_paths.scratch("arm-void-mutants")
    killed = errors = 0
    try:
        for n, (fn, name, old, new) in enumerate([(None, "the control", None, None)] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            gi_paths.remove_tree(d)
            os.makedirs(d)
            for f in ("arm_void.py", "provenance_classifier.py"):
                shutil.copy(os.path.join(HERE, f), d)
            if fn is not None:
                p = os.path.join(d, fn)
                src = open(p, encoding="utf-8", newline="").read()
                if src.count(old) != 1:
                    print("ERROR    m%d %s: the edit matches %d times" % (n, name, src.count(old)))
                    errors += 1
                    continue
                open(p, "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], env=dict(os.environ, ARM_VOID_DIR=d),
                               capture_output=True, text=True, timeout=600)
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            crashed = "Traceback" in r.stderr
            if fn is None:
                if r.returncode or fails or crashed:
                    print("the control FAILS (exit %d, %d FAIL): no mutant is judged" % (r.returncode, len(fails)))
                    return 1
                print("PASS     control: the unmutated copies")
                continue
            dead = bool(fails) and not crashed
            killed += dead
            print("%s m%d %s: %s  (%d FAIL%s%s)" % ("KILLED  " if dead else "SURVIVED", n, fn, name, len(fails),
                                                    ", CRASHED" if crashed else "",
                                                    "; " + fails[0][5:95] if fails else ""))
            sys.stdout.flush()
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    if "--mutants" in sys.argv[1:]:
        return mutants()
    list_cases()
    reasons_cases()
    window_cases()
    attribution_cases()
    rejudge_cases()
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not FAILS else "FAIL", OKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.exit(main())
