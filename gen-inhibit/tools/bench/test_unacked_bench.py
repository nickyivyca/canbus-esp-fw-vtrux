#!/usr/bin/env python3
"""Test of unacked_bench.py's pure parts (F5 on the bench, 2026-11-27), with no bench: the plan meets
the gen-inhibit reviewer's minimums (M1 >= 5 with irq off and on, the control >= 5, M2 >= 50 per
error state, M3 >= 20 per delay and state), aims each state by its attempt, puts a hold on every
call that brings the node back and none on M1, and is shuffled by its seed; the query and the echo
check; the witness's trial copies; one call processed into its summary and hold check, voided when
the node was up after the hold ended, an error for an answer that is not one trial; the start
refusals; the positive control failing on a control trial that did not go out, or on none; an aim
that missed its state listed.

    python3.13 -B test_unacked_bench.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ (with unacked_analysis.py beside it)
and runs this test on it: each must fail a check (test_unacked_bench_mutants.py runs it so).
Gen-inhibit tester, 2026-11-27.
"""
import collections
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("UNACKED_BENCH_DIR") or HERE)
import unacked_bench as B  # noqa: E402
import unacked_analysis as U  # noqa: E402

fails = []
oks = 0
TS = 32
HOST_OFF_S = 10.0           # host perf minus witness time
DEV_OFF_US = 1_000_240.0    # witness time minus device time


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[-300:]))


def trial(n=30, cleared="cleanup", hold_ns=1_500_000_000, **kw):
    """n unacknowledged attempts (6 us active idles, 22 us passive), as test_unacked_analysis builds them."""
    ev, tec, now, rises = [[0, 0, 0, 0]], 0, 20_000, []
    for _ in range(n):
        rises.append(now)
        ev.append([now, TS, tec, 0])
        tec = min(tec + 8, 128)
        ev.append([now + 230_000, 0, tec, 0])
        now += 230_000 + (22_000 if tec >= 128 else 6_000)
    t = {"t": 0, "skip": "", "events": ev, "attempts": n, "end_ns": now, "gaps": 0, "max_gap_ns": 1000, "ev_lost": 0,
         "cleared": cleared, "clear_ns": -1, "abort_ns": -1, "hold_ns": hold_ns}
    t.update(kw)
    return t, rises


def witness_msgs(rises, extra_hw=()):
    """(perf, hw_us, id, dlc, hex, is_error, epoch) for each copy, plus a frame of another ID and an error frame."""
    out = []
    for r in rises:
        hw = r / 1000.0 + DEV_OFF_US
        out.append((hw / 1e6 + HOST_OFF_S, hw, 0x7FE, 8, "0000ab7e00000000", False, 0.0))
    for hw in extra_hw:
        out.append((hw / 1e6 + HOST_OFF_S, hw, 0x7FE, 8, "0000ab7e00000000", False, 0.0))
    out.append((out[0][0], out[0][1] + 5, 0x123, 8, "0000ab7e00000000", False, 0.0))   # another ID
    out.append((out[0][0], out[0][1] + 7, 0x7FE, 8, "0000ab7e00000000", True, 0.0))    # an error frame
    out.append((out[0][0], out[0][1] + 9, 0x7FE, 8, "0000ab7f00000000", False, 0.0))   # another tag (AB 7F)
    return out


def node_at(up_w_us, down_w_us, rx_w_us=()):
    """A node record (host perf) whose window and frames sit at the given witness times."""
    p = lambda w: w / 1e6 + HOST_OFF_S   # noqa: E731
    return {"open_called": p(up_w_us), "opened": p(up_w_us) + 0.002, "closed": p(down_w_us),
            "rx": [(p(w), "0000ab7e00000000") for w in rx_w_us], "error": None}


def main():
    # 1. the plan
    calls = B.plan("full", 1)
    c = collections.Counter((x["kind"], x.get("irq"), x["state"], x.get("delay_us")) for x in calls)
    check("M1: at least 5 with irq off and 5 with irq on", c[("M1", "off", "both", None)] >= 5
          and c[("M1", "on", "both", None)] >= 5, c)
    check("the control: at least 5", c[("control", "off", "both", None)] >= 5, c)
    check("M2: at least 50 per error state", c[("M2", "off", "active", None)] >= 50
          and c[("M2", "off", "passive", None)] >= 50, c)
    check("M3: at least 20 per delay (20, 100, 200 us) and state",
          all(c[("M3", "off", st, d)] >= 20 for st in ("active", "passive") for d in (20, 100, 200)), c)
    act = {x["wait_att"] for x in calls if x["kind"] in ("M2", "M3") and x["state"] == "active"}
    pas = {x["wait_att"] for x in calls if x["kind"] in ("M2", "M3") and x["state"] == "passive"}
    check("active aims at attempts 1-15, passive at 17 or later", max(act) <= 15 and min(pas) >= 17, (act, pas))
    check("a hold on every control, M2 and M3 call; none on M1",
          all(x.get("hold_ms") == B.HOLD_MS for x in calls if x["kind"] != "M1")
          and not any("hold_ms" in x for x in calls if x["kind"] == "M1"), "hold")
    check("one fresh trial per call", all(x["reps"] == 1 and x["fresh"] == 1 for x in calls), "reps/fresh")
    check("the node's window inside the hold", 0 < B.NODE_UP_S < B.NODE_DOWN_S < B.HOLD_MS / 1000.0,
          (B.NODE_UP_S, B.NODE_DOWN_S, B.HOLD_MS))
    k2 = lambda x: (x["kind"], x["state"] if x["kind"] in ("M2", "M3") else x["irq"], x.get("delay_us"))  # noqa: E731
    kinds10 = {k2(x) for x in calls[7:17]}
    check("shuffled: the ten calls after the first seven are of at least four kinds", len(kinds10) >= 4, kinds10)
    check("the same seed gives the same order", [x["label"] for x in B.plan("full", 1)] == [x["label"] for x in calls],
          "order")

    key = lambda x: (x["kind"], x["state"] if x["kind"] in ("M2", "M3") else x["irq"])   # noqa: E731
    check("one call of each kind first, in order", [key(x) for x in calls[:7]] ==
          [("M1", "off"), ("M1", "on"), ("control", "off"), ("M2", "active"), ("M2", "passive"),
           ("M3", "active"), ("M3", "passive")], [key(x) for x in calls[:7]])
    check("no call lost or doubled by the reorder", sorted(x["label"] for x in calls)
          == sorted(x["label"] for x in B.plan("full", 2)) and len({x["label"] for x in calls}) == len(calls), "labels")

    # 1b. the hotspot's recovery: a restart from the uptime, the stop rule, a call with no route
    check("uptime below the last read plus the time between: a restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 3_000}, 140.0) is True, "restart")
    check("uptime grown 10 s in 100 s: a restart (it restarted 10 s ago)",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 610_000}, 200.0) is True, "short growth")
    check("uptime grown with the time: no restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 639_500}, 140.0) is False, "no restart")
    check("uptime 4 s short of the time between (inside the slack): no restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 636_000}, 140.0) is False, "slack")
    check("no page after: unknown, not a restart", B.restart_seen({"uptime_ms": 1}, 0.0, None, 1.0) is None, "none")
    check("the plan stops at 10 calls with no answer, not at 9",
          B.stop_now(10) and not B.stop_now(9), (B.stop_now(9), B.stop_now(10)))
    host = B.HOST
    try:
        B.HOST = "127.0.0.1:9"       # nothing listens: refused, as a missing route is
        st, body = B.http_post("/gi_txabort?phase=m1", timeout=2.0)
    except Exception as e:          # noqa: BLE001  the check below fails on it, not the test
        st, body = "raised", str(e)
    finally:
        B.HOST = host
    check("a call that cannot connect gives no status and its reason, no exception", st is None and body, (st, body))

    # 2. query and echo
    m3 = next(x for x in calls if x["kind"] == "M3")
    q = B.query(m3)
    check("the query: phase first, the label last, the hold, aim and delay in it",
          q.startswith("/gi_txabort?phase=m3&") and q.endswith("label=" + m3["label"])
          and "hold_ms=%d" % B.HOLD_MS in q and "wait_att=%d" % m3["wait_att"] in q and "delay_us=" in q, q)
    m1 = next(x for x in calls if x["kind"] == "M1")
    check("an M1 query has no hold", "hold_ms" not in B.query(m1), B.query(m1))
    ans = {k: m3[k] for k in B.ECHO if k in m3}
    ans["label"] = m3["label"]
    check("an answer echoing every argument: no mismatch", B.echo_mismatch(m3, ans) == [], B.echo_mismatch(m3, ans))
    check("a different hold_ms is a mismatch", B.echo_mismatch(m3, dict(ans, hold_ms=0)) != [], "hold")
    check("a different label is a mismatch", B.echo_mismatch(m3, dict(ans, label="x")) != [], "label")

    # 3. the trial copies
    t, rises = trial(5)
    cps = B.trial_copies(witness_msgs(rises))
    check("trial copies: the five 0x7FE AB 7E frames, not another ID, an error frame or another tag",
          len(cps) == 5, cps)

    # 4. one call: the control, held, received by the node inside the hold
    ctl = dict(next(x for x in calls if x["kind"] == "control"))
    t, rises = trial(30)
    end_w = t["end_ns"] / 1000.0 + DEV_OFF_US
    msgs = witness_msgs(rises)
    row = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 1_100_000,
                                                             [end_w + 400_300]))
    h = row.get("hold") or {}
    check("the control: summary and hold, as expected", "summary" in row and "hold" in row and h.get("void") is None
          and h.get("held") and h.get("as_expected"), h)
    row2 = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 1_600_000,
                                                              [end_w + 400_300]))
    h2 = row2.get("hold") or {}
    check("the node still up after the hold ended: void", "after the hold ended" in str(h2.get("void")), h2)
    row3 = B.process_call(ctl, {"trials": [t, t]}, msgs, None)
    check("an answer of two trials is an error", "error" in row3, row3)
    row4 = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 1_100_000, []))
    check("the control, nothing received: not as expected", (row4.get("hold") or {}).get("as_expected") is False,
          row4.get("hold"))

    # 5. the tables and the positive control
    rows = [dict(row, kind="control"), dict(row4, label="CTL99", kind="control")]
    tb = B.tables(rows)
    check("a control trial that did not go out fails the control", B.control_failed(tb), tb["control_hold"])
    check("every control trial out: the control passes", not B.control_failed(B.tables([row])), "control")
    check("no control trial: the control fails", B.control_failed(B.tables([])), "none")
    m2 = dict(next(x for x in calls if x["kind"] == "M2" and x["state"] == "passive"))
    ta, ra = trial(8, abort_ns=7 * 236_000 + 20_000 + 232_000, ab_issued=True, ab_st_before=0, ab_st_after=4,
                   ab_tec=40, ab_attempt=7, free_after_abort_ns=900, cleared="tbs")
    end_a = ta["end_ns"] / 1000.0 + DEV_OFF_US
    r_m2 = B.process_call(m2, {"trials": [ta]}, witness_msgs(ra[:7]), node_at(end_a + 400_000, end_a + 1_100_000))
    check("an M2 aimed passive whose abort read TEC 40 is listed as a missed aim",
          B.tables([r_m2])["aim_missed"] == [m2["label"]], B.tables([r_m2])["aim_missed"])

    # 6. start refusals
    good = {"x2_pins_start": "pins: TX (GPIO7) recessive -- read", "kvaser_link_start": {"down": True},
            "quiet_frames": 0}
    check("all shown: no refusal", B.start_refusal(good) is None, B.start_refusal(good))
    check("the X2 not held: refused", B.start_refusal(dict(good, x2_pins_start="pins: TX (GPIO7) driven -- x"))
          is not None, "x2")
    check("the Kvaser not down: refused",
          B.start_refusal(dict(good, kvaser_link_start={"down": False, "error": "up"})) is not None, "kvaser")
    check("a frame in the quiet: refused", B.start_refusal(dict(good, quiet_frames=3)) is not None, "quiet")

    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("M2 at 49 per state", '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 50, "m3": 20}',
     '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 49, "m3": 20}'),
    ("the control at 4", '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 50, "m3": 20}',
     '"full": {"m1_off": 6, "m1_on": 6, "control": 4, "m2": 50, "m3": 20}'),
    ("active aims up to 17", "ACTIVE_AIMS = tuple(range(1, 16, 2))", "ACTIVE_AIMS = tuple(range(1, 18, 2))"),
    ("the control with no hold", '        calls.append(dict(base, phase="m1", irq="off", hold_ms=HOLD_MS, kind="control"',
     '        calls.append(dict(base, phase="m1", irq="off", kind="control"'),
    ("not shuffled", "    random.Random(seed).shuffle(calls)\n", ""),
    ("the node down after the hold", "NODE_DOWN_S = 1.2\n", "NODE_DOWN_S = 1.6\n"),
    ("the hold not echoed", 'ECHO = ("phase", "reps", "settle", "fresh", "irq", "window_us", "wait_att", "delay_us", "hold_ms")',
     'ECHO = ("phase", "reps", "settle", "fresh", "irq", "window_us", "wait_att", "delay_us")'),
    ("the label not checked", '    if ans.get("label") != call["label"]:\n', "    if False:\n"),
    ("copies of any tag", 'm[2] == TRIAL_ID and m[4][4:8] == TAG]', "m[2] == TRIAL_ID]"),
    ("error frames counted as copies", "if not m[5] and m[2] == TRIAL_ID", "if m[2] == TRIAL_ID"),
    ("no void after the hold's end", "                if nw[\"down_us\"] >= hold_end_w:\n", "                if False:\n"),
    ("two trials accepted", "    if len(trials) != 1:\n", "    if not trials:\n"),
    ("the control passes on no trials", "    return bool(c[\"not_as_expected\"]) or c[\"trials\"] == 0\n",
     "    return bool(c[\"not_as_expected\"])\n"),
    ("missed aims not listed", "and r[\"summary\"][\"abort\"][\"state\"] != r[\"state_aimed\"]]", "and False]"),
    ("a frame in the quiet not refused", "    if rec.get(\"quiet_frames\"):\n", "    if False:\n"),
    ("the Kvaser not checked", "    if kl.get(\"down\") is not True:\n", "    if False:\n"),
    ("no call of each kind first", "    return first + [x for x in calls if x not in first]\n", "    return calls\n"),
    ("the restart slack ignored", "    return a < b + (t_after - t_before) * 1000.0 - RESTART_SLACK_MS\n",
     "    return a < b + (t_after - t_before) * 1000.0\n"),
    ("the time between ignored", "    return a < b + (t_after - t_before) * 1000.0 - RESTART_SLACK_MS\n",
     "    return a < b - RESTART_SLACK_MS\n"),
    ("the stop one call late", "    return failed_calls >= limit\n", "    return failed_calls > limit\n"),
    ("a refused call raises", "    except (urllib.error.URLError, OSError) as e:", "    except urllib.error.HTTPError as e:"),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "unacked_bench.py"), encoding="utf-8", newline="").read()
    if subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True).returncode != 0:
        print("ERROR    control: the unmutated tool fails this test")
        return 1
    print("PASS     control: the unmutated tool passes this test")
    base = gi_paths.scratch("mutants-unacked_bench")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "unacked_bench.py"), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            shutil.copy2(os.path.join(HERE, "unacked_analysis.py"), d)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, UNACKED_BENCH_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "  (%s)" % failed[0][:150] if failed else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
