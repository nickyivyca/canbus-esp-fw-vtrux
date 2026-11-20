"""Harness test for b11_rawpost_judge.py: spec 3 in the request-to-reply window (topic (E),
2026-11-19, the gen-inhibit reviewer's readings (m)-(r)), on synthetic times and records (no
device, no capture). The can-fails the reviewer named: a change seen only after the reply (a device
0x051 after it into OBSERVE) and a backlog left queued (a stale answer after a POST back); and the
others: within the clock margin ERROR, nothing to see NOT APPLICABLE, the page after the reply
showing another mode FAIL, its queue over the bound FAIL, a read never sent VOID, one unanswered
ERROR, a reply cut short ERROR.

    python3.13 -B gen-inhibit/tools/bench/test_b11_rawpost_judge.py   (from the reverse-it root)
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import b11_rawpost_judge as J                                      # noqa: E402

TS, TC, END, M = 10.000, 10.030, 20.000, 0.0004      # the window on the witness, the next POST, the margin


def page(mode, q=1, http=200, error=None):
    return {"t_sent": 10.1, "t_answered": 10.12, "http": http, "error": error, "mode": mode, "msgs_to_rx": q,
            "rx_missed": 0}


def post(mode, **kw):
    e = {"at_s": 10.0, "mode": mode, "http": 200, "index": 1000,
         "raw": {"t_sent": 10.0, "t_complete": 10.03, "ending": "complete_open"}, "page_after": page(mode)}
    e.update(kw)
    return e


def answering(until, start=9.0):
    """Device 0x051 0.5 ms after each VCM command every 10 ms from start to until: (times, answers)."""
    cmds = [round(start + 0.01 * k, 4) for k in range(int((until - start) / 0.01) + 1)]
    return [round(c + 0.0005, 4) for c in cmds], [(round(c + 0.0005, 4), c) for c in cmds]


def void_case():
    """judge_run on an e4 record whose arm provenance_classifier reports void: the replay is never read, and
    every POST (both modes) reads VOID with the reason."""
    import gi_paths
    work = gi_paths.scratch("b11-rawpost-void")
    gi_paths.remove_tree(work)
    os.makedirs(work)
    saved = {k: sys.modules.get(k) for k in ("provenance_classifier", "e4_device_calibration")}
    read = []
    try:
        run = os.path.join(work, "B11raw_e4_x_arm1.json")
        sched = [{"at_s": 20.0, "mode": 1, "http": 200}, {"at_s": 30.0, "mode": 3, "http": 200},
                 {"at_s": 40.0, "mode": 1, "http": 200}]
        with open(run, "w", encoding="utf-8") as f:
            json.dump({"arms": {"idle": {"post_schedule": sched, "base_epoch": 1.0}}, "capture": "c.log",
                       "window_start": 0, "seconds": 1, "mode": "inhibit"}, f)
        P = types.ModuleType("provenance_classifier")
        P.rejudge = lambda *a, **k: {"arm_void": ["witness clock 'SocketCAN: x': not judged on host time"]}
        E = types.ModuleType("e4_device_calibration")
        E.load_window = lambda s: read.append("replay") or []
        sys.modules["provenance_classifier"], sys.modules["e4_device_calibration"] = P, E
        try:
            res = J.judge_run(run)
        except Exception as e:      # noqa: BLE001  judged anyway: a FAIL, not a crash
            print("       void case raised %s: %s" % (type(e).__name__, e))
            return False
        v = [p.get("verdict") for p in res["posts"]]
        return (len(v) == 3 and all(x.startswith("VOID: arm void: witness clock") for x in v) and not read
                and [p["at_s"] for p in res["posts"]] == [20.0, 30.0, 40.0])
    finally:
        for k, m in saved.items():
            if m is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = m
        gi_paths.remove_tree(work)


def wiring_case():
    """judge_run on a stand-in record and provenance: the replay's frames 2.0-2.4 ms late place each
    POST (margin 0.4 ms), the device answering in INHIBIT until 10.0305 s, inside the reply placed on the
    witness (10.0324 s) but after it on the replay's own clock, then from 20.025 s; each POST's window,
    margin and verdict as the halves give them."""
    import gi_paths
    work = gi_paths.scratch("b11-rawpost-wiring")
    gi_paths.remove_tree(work)
    os.makedirs(work)
    saved = {k: sys.modules.get(k) for k in ("provenance_classifier", "e4_device_calibration")}
    saved_bound = J.QUEUE_BOUND
    try:
        src = [round(0.01 * k, 4) for k in range(3001)]                 # 0..30 s every 10 ms
        rep = [t + 0.0020 + (0.0004 if k % 7 else 0.0) for k, t in enumerate(src)]
        # (rep - src is 2.0 ms on every 7th frame, 2.4 ms on the rest: median 2.4, margin 0.4)
        dev1, ans1 = answering(10.031)
        dev2, ans2 = answering(29.0, start=20.02)
        sched = [post(1, at_s=10.0, index=1000, raw={"t_sent": 10.000, "t_complete": 10.030, "ending": "complete_open"}),
                 post(3, at_s=20.0, index=2000, raw={"t_sent": 20.000, "t_complete": 20.034, "ending": "complete_open"})]
        run = os.path.join(work, "B11raw_e4_quiesce_inhibit_x_arm1.json")
        with open(run, "w", encoding="utf-8") as f:
            json.dump({"arms": {"idle": {"post_schedule": sched}}, "capture": "c.log", "window_start": 0, "seconds": 30,
                       "mode": "inhibit", "device": "x"}, f)
        P = types.ModuleType("provenance_classifier")
        P.rejudge = lambda *a, **k: {"arm_void": [], "arm_unlisted": [], "replayed_wire_times": rep,
                                     "device_times": dev1 + dev2,
                                     "device_answers": [[t, a, 1] for t, a in ans1 + ans2]}
        E = types.ModuleType("e4_device_calibration")
        E.load_window = lambda s: [(t, 0x100, b"", False) for t in src]
        sys.modules["provenance_classifier"], sys.modules["e4_device_calibration"] = P, E
        J.QUEUE_BOUND = 5
        res = J.judge_run(run)
        p1, p2 = res["posts"]
        return (p1["margin_ms"] == 0.4 and p1["window_ms"] == 30.0 and p1["witness"] == "PASS" and p1["verdict"] == "PASS"
                and p1["from_mode"] == 3 and p2["from_mode"] == 1 and p2["window_ms"] == 34.0
                and p2["witness"] == "PASS" and p2["verdict"] == "PASS"
                and p2["answers"]["first_t_rel_reply_ms"] is not None), res
    except Exception as e:      # noqa: BLE001
        return False, repr(e)
    finally:
        J.QUEUE_BOUND = saved_bound
        for k, m in saved.items():
            if m is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = m
        gi_paths.remove_tree(work)


def main():
    checks = []
    # mapping: the median lateness, and the margin down to the smallest
    rep = [k * 0.01 + d for k, d in enumerate([0.002, 0.0024, 0.0024, 0.0030, 0.0024])]
    src = [k * 0.01 for k in range(5)]
    off, mg = J.mapping(rep, src, 2)
    checks.append(("mapping: the offset the median lateness, the margin it minus the smallest",
                   abs(off - 0.0024) < 1e-9 and abs(mg - 0.0004) < 1e-9))
    checks.append(("mapping: no found frame near the POST -> None", J.mapping([None] * 5, src, 2) == (None, None)))
    off, mg = J.mapping(rep, src, 0, n=1)
    checks.append(("mapping: only the frames within n of the POST", abs(off - 0.002) < 1e-9 and mg == 0.0))

    # into OBSERVE (reading (n), (r))
    dev, _ = answering(10.020)
    checks.append(("OBSERVE: the device silent from inside the window -> PASS",
                   J.observe_half(TS, TC, END, M, dev)[0] == J.PASS))
    # THE CAN-FAIL: the change seen only after the reply
    late, _ = answering(10.060)
    checks.append(("OBSERVE: a device 0x051 after the reply plus the margin -> FAIL (change seen only after the reply)",
                   J.observe_half(TS, TC, END, M, late)[0] == J.FAIL))
    checks.append(("OBSERVE: one within the margin after the reply -> ERROR",
                   J.observe_half(TS, TC, END, M, dev + [10.0302])[0] == J.ERROR))
    checks.append(("OBSERVE: one at the reply itself -> PASS (before the reply)",
                   J.observe_half(TS, TC, END, M, dev + [10.030])[0] == J.PASS))
    checks.append(("OBSERVE: a device 0x051 just past the margin -> FAIL",
                   J.observe_half(TS, TC, END, M, dev + [10.0305])[0] == J.FAIL))
    checks.append(("OBSERVE (r): one long after, still before the next POST -> FAIL (OBSERVE never transmits)",
                   J.observe_half(TS, TC, END, M, dev + [15.0])[0] == J.FAIL))
    checks.append(("OBSERVE: the device after the next POST is that POST's, not this one's",
                   J.observe_half(TS, TC, END, M, dev + [20.5])[0] == J.PASS))
    checks.append(("OBSERVE: no device 0x051 in the 100 ms before the request -> NOT APPLICABLE",
                   J.observe_half(TS, TC, END, M, [])[0] == J.NA and J.observe_half(TS, TC, END, M, [9.85])[0] == J.NA))
    checks.append(("OBSERVE: one 95 ms before the request is something to see -> PASS",
                   J.observe_half(TS, TC, END, M, [9.905])[0] == J.PASS))
    checks.append(("OBSERVE: nothing before but one after the reply -> FAIL, not NOT APPLICABLE",
                   J.observe_half(TS, TC, END, M, [12.0])[0] == J.FAIL))

    # into another mode (reading (o)): stale answers
    _, fresh = answering(10.5, start=10.005)
    v, why, rec = J.stale_half(TS, TC, END, M, fresh)
    checks.append(("back: answers only to commands after the request -> PASS, the first recorded, not judged",
                   v == J.PASS and rec["first_t_rel_reply_ms"] == -24.5 and rec["first_answers_rel_request_ms"] == 5.0))
    # THE CAN-FAIL: a backlog left queued
    v = J.stale_half(TS, TC, END, M, fresh + [(10.05, 9.99)])[0]
    checks.append(("back: a device 0x051 after the reply answering a command from before the request -> FAIL (stale)",
                   v == J.FAIL))
    checks.append(("back: the answered command within the margin before the request -> ERROR",
                   J.stale_half(TS, TC, END, M, fresh + [(10.05, 9.9998)])[0] == J.ERROR))
    checks.append(("back: the stale answer within the margin after the reply -> ERROR",
                   J.stale_half(TS, TC, END, M, fresh + [(10.0302, 9.99)])[0] == J.ERROR))
    v, why, rec = J.stale_half(TS, TC, END, M, fresh + [(10.02, 9.99)])
    checks.append(("back: an answer inside the window to a command from before -> PASS, counted, not judged",
                   v == J.PASS and rec["in_window_answers_to_commands_before_request"] == 1))
    checks.append(("back: a stale answer after the next POST is not this POST's",
                   J.stale_half(TS, TC, END, M, fresh + [(20.5, 9.99)])[0] == J.PASS))
    checks.append(("back: an answer with no command placed is not judged stale",
                   J.stale_half(TS, TC, END, M, fresh + [(10.05, None)])[0] == J.PASS))
    v, why, rec = J.stale_half(TS, TC, END, M, [])
    checks.append(("back: no answer at all (PASSIVE) -> PASS, the first answer None",
                   v == J.PASS and rec["first_t_rel_reply_ms"] is None))

    # the page after the reply (readings (p), (q), (t))
    saved = J.QUEUE_BOUND
    try:
        J.QUEUE_BOUND = 5
        checks.append(("page: the POSTed mode, the queue within the bound -> PASS, PASS",
                       J.page_half(page(1, 5), 1)[:2] == (J.PASS, J.PASS)))
        checks.append(("page: another mode -> FAIL", J.page_half(page(3, 1), 1)[0] == J.FAIL))
        checks.append(("page: msgs_to_rx over the bound -> FAIL", J.page_half(page(1, 6), 1)[1] == J.FAIL))
        checks.append(("page: no msgs_to_rx on an answered page -> ERROR", J.page_half(page(1, None), 1)[1] == J.ERROR))
        J.QUEUE_BOUND = None
        checks.append(("page: no bound set from quiet data yet -> the queue ERROR, never PASS",
                       J.page_half(page(1, 0), 1)[1] == J.ERROR))
    finally:
        J.QUEUE_BOUND = saved
    checks.append(("page: no read in the record (never sent) -> VOID, VOID", J.page_half(None, 1)[:2] == (J.VOID, J.VOID)))
    checks.append(("page: sent and unanswered -> ERROR, ERROR",
                   J.page_half(page(None, None, http=None, error="timed out"), 1)[:2] == (J.ERROR, J.ERROR)))

    # a POST's verdict
    checks.append(("worst: FAIL, then ERROR, then VOID; NOT APPLICABLE counts as neither",
                   J.worst([J.ERROR, J.FAIL]) == J.FAIL and J.worst([J.VOID, J.ERROR]) == J.ERROR
                   and J.worst([J.NA, J.VOID]) == J.VOID and J.worst([J.NA, J.PASS]) == J.PASS))
    r = J.judge_post(post(1, raw={"t_sent": 10.0, "t_complete": None, "ending": "rst"}), 3, TS, None, END, M, dev, [])
    checks.append(("POST: a reply cut short -> ERROR, no window", r["verdict"] == J.ERROR and r["window_ms"] is None))
    saved = J.QUEUE_BOUND
    try:
        J.QUEUE_BOUND = 5
        r = J.judge_post(post(1, http=500), 3, TS, TC, END, M, dev, [])
        checks.append(("POST: a refused POST (500) -> ERROR, no window, its page not judged",
                       r["verdict"] == J.ERROR and r["window_ms"] is None and "witness" not in r))
        r = J.judge_post(post(1), 3, TS, TC, END, M, dev, [])
        checks.append(("POST into OBSERVE: window, margin, witness, mode and queue -> PASS",
                       r["verdict"] == J.PASS and r["window_ms"] == 30.0 and r["margin_ms"] == 0.4
                       and r["witness"] == J.PASS and r["mode_after"] == 1))
        r = J.judge_post(post(1), 4, TS, TC, END, M, [], [])
        checks.append(("POST into OBSERVE from PASSIVE: nothing to see, the page shows it -> PASS",
                       r["witness"] == J.NA and r["verdict"] == J.PASS))
        r = J.judge_post(post(1), 3, TS, TC, END, M, late, [])
        checks.append(("POST into OBSERVE with a device 0x051 after the reply -> FAIL whatever the page says",
                       r["verdict"] == J.FAIL))
        r = J.judge_post(post(3), 1, TS, TC, END, M, [], fresh + [(10.05, 9.99)])
        checks.append(("POST back with a stale answer -> FAIL", r["verdict"] == J.FAIL and r["witness"] == J.FAIL))
        r = J.judge_post(post(3, page_after=None), 1, TS, TC, END, M, [], fresh)
        checks.append(("POST back with no page read in the record -> VOID", r["verdict"] == J.VOID))
        r = J.judge_post(post(3, page_after=page(1)), 1, TS, TC, END, M, [], fresh)
        checks.append(("POST back whose page shows the old mode -> FAIL", r["verdict"] == J.FAIL))
    finally:
        J.QUEUE_BOUND = saved
    checks.append(("an arm void for a spec 12.1 reason: every POST reads VOID with the reason, the replay unread",
                   void_case()))
    ok, detail = wiring_case()
    checks.append(("judge_run: each POST placed by the replay's frames (margin 0.4 ms), its window and verdict", ok))
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    if fails and "judge_run" in fails[-1]:
        print("       %s" % (str(detail)[:600],))
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
