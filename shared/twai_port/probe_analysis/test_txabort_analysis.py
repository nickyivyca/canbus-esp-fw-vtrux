#!/usr/bin/env python3
"""Test of txabort_analysis.py (the transmit-abort probe's older phases, shared/twai_port T13;
gen-inhibit spec 5.2 item 9), with no bench, on synthetic answers and witness rows: the alert
words, unknown bits included; the echo check passing on a full echo and refusing a missing parse,
an argument with no echo, a changed value, settle under its own name, and sweep arithmetic that
does not follow; the arguments, phase first; the answer record; the witness's rows by trial number
and tag, error frames and other IDs left out, its rate and census; each outcome classify() names;
the join by trial number, not by order; and the summary's flags: the RS gate, no completion (not in
the control phase), the control's alerts, no TX alert, TX_FAILED, the follow-up refused, witness
error frames, witnesses that disagree, a failed cleanup, an overshoot, the retry loop, and skipped
trials.

The older phases' answer fields are not written in test_app/README.md, which gives m1-m3's; the
trials here use the fields wican_txabort_bench.py read at gen-inhibit/main 74555175. New with the
move to shared/twai_port: gen-inhibit had no test of that file. Charge-interposer tester,
2026-11-28.

    python3.13 -B test_txabort_analysis.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ and runs this test on it: each must
fail a check (mutants.py runs them).
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("TXABORT_ANALYSIS_DIR") or HERE)
import txabort_analysis as X  # noqa: E402

fails = []
oks = 0
TX_SUCCESS, RX_DATA, ARB_LOST, TX_FAILED = 0x2, 0x4, 0x80, 0x400


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[-300:]))


def echo(sent, rec):
    """'ok', or 'refused: <why>' when verify_echo() raises SystemExit, or 'error: <exception>'."""
    try:
        X.verify_echo(sent, rec)
        return "ok"
    except SystemExit as e:
        return "refused: %s" % e
    except Exception as e:      # noqa: BLE001  a check fails on it, not the test
        return "error: %s: %s" % (type(e).__name__, e)


def header(**kw):
    """A header echoing phase=abort gate=off label=x reps=3 d0=0 d1=40 step=20 settle=5: 3 steps, n 9."""
    h = {"query_given": True, "phase": "abort", "gate": "off", "label": "x", "reps": 3, "d0": 0, "d1": 40,
         "step": 20, "settle_ms": 5, "steps": 3, "n": 9}
    h.update(kw)
    return h


SENT = {"phase": "abort", "gate": "off", "label": "x", "reps": 3, "d0": 0, "d1": 40, "step": 20, "settle": 5}


def frame(trial, tag=X.TAG_TRIAL, ident=X.TRIAL_ID, err=False, ext=False, ts=0.0, short=False):
    data = bytes([trial & 0xFF, trial >> 8, X.TAG_HI, tag, 0, 0, 0, 0])
    return (ts, ident, data[:3] if short else data, err, ext)


def trial(t, **kw):
    """A live trial of the abort phase: the abort landed with TS = 1 and the frame completed."""
    d = {"t": t, "skip": "", "req": 100, "act": 104, "gate_us": 0, "edge_us": -1, "tbs_b": 0, "ts_b": 1,
         "rs_b": 0, "tcs_b": 0, "mtx_b": 1, "mtx_a": 0, "free_us": 240, "mtx0_us": 250, "txalert_us": 251,
         "txalerts": TX_SUCCESS, "alerts": TX_SUCCESS, "d_tx_failed": 0, "d_bus_err": 0, "d_arb_lost": 0,
         "next_tx_err": 0}
    d.update(kw)
    return d


def rec_of(phase, trials, gate="off", **kw):
    r = {"phase": phase, "gate": gate, "label": phase, "n": len(trials), "run_us": 1_000_000, "git": "g",
         "trials": trials}
    r.update(kw)
    return r


def summary(rec, wits):
    """The joined phase's printed summary."""
    X.join_phase(rec, wits)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        X.summarise(rec, wits)
    return buf.getvalue()


def main():
    # 1. alert words
    check("TX_SUCCESS by its bit", X.alert_words(TX_SUCCESS) == ["TX_SUCCESS"], X.alert_words(TX_SUCCESS))
    check("two bits, both named", X.alert_words(ARB_LOST | TX_FAILED) == ["ARB_LOST", "TX_FAILED"],
          X.alert_words(ARB_LOST | TX_FAILED))
    check("an unknown bit named as unknown", X.alert_words(0x20000 | TX_SUCCESS) == ["TX_SUCCESS", "unknown:0x00020000"],
          X.alert_words(0x20000 | TX_SUCCESS))
    check("no bits, no names", X.alert_words(0) == [], X.alert_words(0))

    # 2. the echo
    check("a full echo, settle as settle_ms: accepted", echo(SENT, header()) == "ok", echo(SENT, header()))
    check("numbers echoed as strings: the same", echo(SENT, header(reps="3", d1="40")) == "ok",
          echo(SENT, header(reps="3", d1="40")))
    check("no query parsed: refused", echo(SENT, header(query_given=False)).startswith("refused"),
          echo(SENT, header(query_given=False)))
    check("a value the device changed (d1 clamped): refused", "d1" in echo(SENT, header(d1=20, steps=2, n=6))
          and echo(SENT, header(d1=20, steps=2, n=6)).startswith("refused"), echo(SENT, header(d1=20, steps=2, n=6)))
    h = header()
    del h["settle_ms"]
    check("settle not echoed as settle_ms: refused", echo(SENT, dict(h, settle=5)).startswith("refused"),
          echo(SENT, dict(h, settle=5)))
    check("an argument nothing echoes: refused", echo(dict(SENT, foo=1), header(foo=1)).startswith("refused"),
          echo(dict(SENT, foo=1), header(foo=1)))
    check("steps that do not follow from d0, d1, step (n right): refused",
          echo(SENT, header(steps=4)).startswith("refused") and "steps:" in echo(SENT, header(steps=4)),
          echo(SENT, header(steps=4)))
    check("n that is not steps x reps: refused", echo(SENT, header(n=8)).startswith("refused"), echo(SENT, header(n=8)))

    # 3. the arguments and the answer record
    sent, args = X.probe_args("abort", gate="rs", reps=20, d0=15, d1=15, settle=10)
    check("the arguments: phase first, the rest in the order given",
          args == ["phase=abort", "gate=rs", "reps=20", "d0=15", "d1=15", "settle=10"], args)
    check("sent holds every argument and the phase", sent == {"phase": "abort", "gate": "rs", "reps": 20, "d0": 15,
                                                               "d1": 15, "settle": 10}, sent)
    r = X.answer_record(dict(SENT), header())
    check("the answer record carries what was sent", r.get("sent") == SENT, r.get("sent"))
    try:
        X.answer_record(dict(SENT), header(n=8))
        got = "accepted"
    except SystemExit:
        got = "refused"
    except Exception as e:      # noqa: BLE001
        got = "error: %s" % e
    check("an answer that does not echo what was sent is refused", got == "refused", got)

    # 4. the witness's rows
    rows = [frame(1, ts=1.0), frame(2, ts=1.1), frame(2, ts=1.2), frame(258, ts=1.3), frame(3, err=True, ts=1.4),
            frame(4, ident=0x300, ts=1.5), frame(5, short=True, ts=1.6), frame(6, tag=X.TAG_FOLLOW, ts=1.7),
            frame(7, ident=0x123, ext=True, ts=1.8), (1.9, X.TRIAL_ID, bytes([8, 0, 0xAC, X.TAG_TRIAL]), False, False),
            (2.0, X.TRIAL_ID, b"", True, False)]
    w = X.WitnessRows("kv", rows)
    tr = dict(w.trials(X.TAG_TRIAL))
    check("trial copies by number: 1 once, 2 twice, 258 from two bytes", tr == {1: [1.0], 2: [1.1, 1.2], 258: [1.3]}, tr)
    check("the follow-up's tag apart", dict(w.trials(X.TAG_FOLLOW)) == {6: [1.7]}, dict(w.trials(X.TAG_FOLLOW)))
    check("error frames counted", w.errors() == 2, w.errors())
    check("the rate over the rows' own span: 10 intervals in 1.0 s", abs(w.fps() - 10.0) < 1e-9, w.fps())
    check("one row: no rate", X.WitnessRows("a", [frame(1)]).fps() == 0.0, X.WitnessRows("a", [frame(1)]).fps())
    c = w.id_census()
    check("the census: IDs, extended marked, error frames", c.get("7FE") == 7 and c.get("300") == 1
          and c.get("123x") == 1 and c.get("error") == 2, c)

    # 5. outcomes
    cases = [
        ("a skipped trial", trial(1, skip="busy"), False, "skipped: busy"),
        ("noabort on the wire", dict(trial(1), noabort=True), True, "completed (no abort)"),
        ("noabort not on the wire", dict(trial(1), noabort=True), False, "NOT ON THE WIRE (no abort)"),
        ("control", dict(trial(1), control=True), False, "abort on empty buffer"),
        ("buffer free at the abort, frame seen", trial(1, tbs_b=1), True, "buffer already free, frame had gone"),
        ("buffer free at the abort, nothing seen", trial(1, tbs_b=1), False, "buffer already free, NOTHING ON THE WIRE"),
        ("awaiting, aborted", trial(1, ts_b=0), False, "awaiting, aborted, not on the wire"),
        ("awaiting, yet on the wire", trial(1, ts_b=0), True, "awaiting at abort BUT REACHED THE WIRE"),
        ("transmitting, completed", trial(1), True, "transmitting, abort was a no-op, completed"),
        ("transmitting, still in the buffer", trial(1, mtx_a=1, mtx0_us=-1), False,
         "ABORT HAD NO EFFECT: still in the buffer, no completion, not on the wire"),
        ("transmitting, out, arb_lost counted", trial(1, d_arb_lost=1), False,
         "in arbitration, lost, then aborted out; reported as success"),
        ("transmitting, out by mtx0_us, ARB_LOST raised", dict(trial(1, mtx_a=1), alert_names=["ARB_LOST"]), False,
         "in arbitration, lost, then aborted out; reported as success"),
        ("transmitting, out, no arb_lost", trial(1), False, "transmitting, NOT ON THE WIRE, no arb_lost -- UNEXPLAINED"),
    ]
    for name, t, wire, want in cases:
        got = X.classify(t, wire)
        check("outcome: %s" % name, got == want, got)

    # 6. the join, by trial number
    rec = rec_of("abort", [trial(1, txalerts=TX_SUCCESS, alerts=TX_SUCCESS | RX_DATA), trial(2)])
    w1 = X.WitnessRows("kv", [frame(2, ts=0.5), frame(2, tag=X.TAG_FOLLOW, ts=0.6), frame(1, tag=X.TAG_FOLLOW, ts=0.7)])
    X.join_phase(rec, [w1])
    t1, t2 = rec["trials"]
    check("joined by number: trial 2's copy is trial 2's, not the first trial's",
          t1["wire"] == {"kv": 0} and t2["wire"] == {"kv": 1}, (t1["wire"], t2["wire"]))
    check("the follow-ups counted apart", t1["wire_follow"] == {"kv": 1} and t2["wire_follow"] == {"kv": 1},
          (t1["wire_follow"], t2["wire_follow"]))
    check("the TX mask's word is the measurement, the full word beside it",
          t1["alert_names"] == ["TX_SUCCESS"] and t1["all_alert_names"] == ["TX_SUCCESS", "RX_DATA"], t1)
    check("each trial classified from its own wire", t2["outcome"] == "transmitting, abort was a no-op, completed"
          and t1["outcome"].startswith("transmitting, NOT ON THE WIRE"), (t1["outcome"], t2["outcome"]))
    rc = rec_of("control", [trial(1)])
    X.join_phase(rc, [w1])
    check("the control phase's trials marked control", rc["trials"][0]["control"] is True
          and rc["trials"][0]["outcome"] == "abort on empty buffer", rc["trials"][0])

    # 7. the summary's flags
    wk = X.WitnessRows("kv", [frame(1, ts=0.0), frame(2, ts=0.1)])
    out = summary(rec_of("abort", [trial(1, ts_b=0, edge_us=12, gate_us=300), trial(2, edge_us=8, gate_us=280)],
                         gate="rs"), [wk])
    check("RS gate: a row with TS = 1 before the abort is a failed gate", "THE RS GATE FAILED on 1 of 2" in out, out)
    out = summary(rec_of("abort", [trial(1, ts_b=0, edge_us=12, gate_us=300)], gate="rs"), [wk])
    check("RS gate: every row TS = 0, the gate held", "RS gate held" in out and "GATE FAILED" not in out, out)
    nc = dict(mtx_a=1, mtx0_us=-1, free_us=-1)
    out = summary(rec_of("abort", [trial(1, **nc), trial(2)]), [wk])
    check("no completion and the buffer never freed: the stop condition",
          "NO COMPLETION EVENT AND THE BUFFER NEVER FREED on 1 of 2" in out, out)
    out = summary(rec_of("control", [trial(1, txalert_us=-1, **nc)]), [wk])
    check("the control phase: no completion is not flagged", "NO COMPLETION" not in out, out)
    check("the control phase: no TX alert is the expected result", "RAISED NO TX ALERT on all 1" in out
          and "NO TX ALERT AT ALL" not in out, out)
    out = summary(rec_of("control", [trial(1)]), [wk])
    check("the control phase: a TX alert on an empty buffer is flagged", "raised a TX alert with an EMPTY BUFFER" in out,
          out)
    out = summary(rec_of("abort", [trial(1, txalert_us=-1), trial(2)]), [wk])
    check("another phase: no TX alert is a stop condition", "NO TX ALERT AT ALL in 1 of 2" in out, out)
    out = summary(rec_of("abort", [trial(1, txalerts=TX_FAILED), trial(2)]), [wk])
    check("TX_FAILED flagged", "TX_FAILED raised in 1 trials: [1]" in out, out)
    out = summary(rec_of("abort", [trial(1, next_tx_err=1)]), [wk])
    check("the follow-up transmit refused: flagged", "FOLLOW-UP TRANSMIT WAS REFUSED in 1 trials" in out, out)
    we = X.WitnessRows("kv", [frame(1, ts=0.0), frame(9, err=True, ts=0.1)])
    out = summary(rec_of("abort", [trial(1)]), [we])
    check("a witness's error frames flagged", "kv saw 1 ERROR FRAMES" in out, out)
    wx = X.WitnessRows("ix", [frame(2, ts=0.0)])
    out = summary(rec_of("abort", [trial(1), trial(2)]), [wk, wx])
    check("witnesses that disagree on a trial: flagged", "THE WITNESSES DISAGREE on 1 trials: [1]" in out, out)
    out = summary(rec_of("abort", [trial(1, cleanup=1, cleanup_us=-1, cleanup_mtx=1), trial(2, cleanup=1, cleanup_us=900)]),
                  [wk])
    check("a cleanup that did not free the buffer: flagged", "FAILED on 1" in out and "when it FAILED" in out, out)
    out = summary(rec_of("abort", [trial(1, req=100, act=180), trial(2, req=100, act=140)]), [wk])
    check("an abort 80 us late is an overshoot, one 40 us late is not",
          "1 of 2 trials overshot the requested delay by > 50 us" in out, out)
    out = summary(rec_of("abort", [trial(1, tbs_us=30, att=1, ts1=0), trial(2, att=3, ts1=2, retry_bound_hit=1)],
                         retry="1"), [wk])
    check("the retry loop: converged trials counted, the bound hit listed",
          "RETRY LOOP: 1 of 2 trials reached a free buffer" in out and "the 2 ms bound was hit on 1 trials: [2]" in out,
          out)
    out = summary(rec_of("abort", [trial(1, skip="busy"), trial(2)]), [wk])
    check("a skipped trial is not live", "2 trials, 1 live; skipped: busy x1" in out, out)
    out = summary(rec_of("abort", [trial(1, skip="busy")]), [wk])
    check("no live trial: the summary stops after the count", "0 live" in out and "outcomes:" not in out, out)

    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("unknown alert bits dropped", "    if left:\n", "    if False:\n"),
    ("TX_SUCCESS at RX_DATA's bit", '(0x00000002, "TX_SUCCESS")', '(0x00000004, "TX_SUCCESS")'),
    ("no parse not refused", '    if rec.get("query_given") is not True:\n', "    if False:\n"),
    ("an argument with no echo sent anyway", "        if key not in ECHO_KEYS:\n", "        if False:\n"),
    ("settle compared under its own name", '"settle": ("settle_ms", int),', '"settle": ("settle", int),'),
    ("values never compared", "        if got != want:\n", "        if False:\n"),
    ("the echo compared uncast", "            got = cast(rec[hk])\n", "            got = rec[hk]\n"),
    ("steps not checked", '        if int(rec["steps"]) != steps:\n', "        if False:\n"),
    ("n not checked", '        if int(rec["n"]) != steps * int(rec["reps"]):\n', "        if False:\n"),
    ("the phase not first", '["phase=%s" % phase] + ["%s=%s" % (k, v) for k, v in kw.items()]',
     '["%s=%s" % (k, v) for k, v in kw.items()] + ["phase=%s" % phase]'),
    ("the phase not in sent", '    sent["phase"] = phase\n', ""),
    ("the answer record not checked", "    verify_echo(sent, rec)\n    rec[\"sent\"] = sent\n", "    rec[\"sent\"] = sent\n"),
    ("error frames taken as copies", "            if err or ident != TRIAL_ID or len(data) < 4:\n",
     "            if ident != TRIAL_ID or len(data) < 4:\n"),
    ("a copy of any tag", "            if data[2] != TAG_HI or data[3] != tag:\n", "            if data[2] != TAG_HI:\n"),
    ("the trial number's high byte dropped", "out[data[0] | (data[1] << 8)]", "out[data[0]]"),
    ("the rate over one row too many", "        return (len(self.rows) - 1) / span if span > 0 else 0.0\n",
     "        return len(self.rows) / span if span > 0 else 0.0\n"),
    ("extended IDs not marked", '("%03X%s" % (ident, "x" if ext else ""))', '("%03X%s" % (ident, ""))'),
    ("a free buffer at the abort not its own outcome", '    if t["tbs_b"]:\n', "    if False:\n"),
    ("awaiting read as transmitting", '    if not t["ts_b"]:\n', '    if t["ts_b"] == 2:\n'),
    ("left the buffer from msgs_to_tx only", '    left_buffer = (t["mtx_a"] == 0) or (t["mtx0_us"] >= 0)\n',
     '    left_buffer = (t["mtx_a"] == 0)\n'),
    ("ARB_LOST in the alerts ignored", '    if t["d_arb_lost"] or "ARB_LOST" in t.get("alert_names", ()):\n',
     '    if t["d_arb_lost"]:\n'),
    ("joined by order", '        n = t["t"]\n', '        n = rec["trials"].index(t)\n'),
    ("the follow-up's copies taken as the trial's", "    seen = {w.name: w.trials(TAG_TRIAL) for w in wits}\n",
     "    seen = {w.name: w.trials(TAG_FOLLOW) for w in wits}\n"),
    ("the full alert word as the measurement", '        t["alert_names"] = alert_words(t["txalerts"])\n',
     '        t["alert_names"] = alert_words(t["alerts"])\n'),
    ("the control phase not marked", '        t["control"] = rec["phase"] == "control"\n', '        t["control"] = False\n'),
    ("skipped trials taken as live", '    live = [t for t in rec["trials"] if not t["skip"]]\n', '    live = list(rec["trials"])\n'),
    ("no early stop with no live trial", "    if not live:\n        return\n", ""),
    ("the overshoot mark at 500 us", "        big = [e for e in errs if e > 50]\n", "        big = [e for e in errs if e > 500]\n"),
    ("the RS gate never fails", '        ungated = [t["t"] for t in live if t["ts_b"]]\n', "        ungated = []\n"),
    ("no completion never flagged", "    if nocomp:\n", "    if False:\n"),
    ("no completion flagged in the control phase", '              and not t.get("noabort") and not t.get("control")]\n',
     '              and not t.get("noabort")]\n'),
    ("the control phase judged as any other", '    if rec["phase"] == "control":\n', "    if False:\n"),
    ("no TX alert never flagged", "    elif noal:\n", "    elif False:\n"),
    ("TX_FAILED never flagged", '    failed = [t["t"] for t in live if "TX_FAILED" in t["alert_names"]]\n', "    failed = []\n"),
    ("a refused follow-up never flagged", '    nx = [t["t"] for t in live if t["next_tx_err"]]\n', "    nx = []\n"),
    ("a witness's error frames never flagged", "        if w.errors():\n", "        if False:\n"),
    ("disagreeing witnesses never flagged", 'if len(set(bool(v) for v in t["wire"].values())) > 1]', "if False]"),
    ("a failed cleanup never counted", '        failed = [t for t in cl if t.get("cleanup_us", -1) < 0]\n', "        failed = []\n"),
    ("the retry loop's bound never listed", '        hit = [t["t"] for t in live if t.get("retry_bound_hit")]\n',
     "        hit = []\n"),
]


def mutants():
    sys.path.insert(0, HERE)
    import mutants as MR
    return MR.run(os.path.abspath(__file__), "txabort_analysis.py", MUTANTS, "TXABORT_ANALYSIS_DIR")


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
