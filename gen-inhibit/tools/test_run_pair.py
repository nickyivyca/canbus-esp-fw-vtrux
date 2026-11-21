#!/usr/bin/env python3
"""Tests for run_pair.py on made-up core records: the standing result is each side's last
run that has the check, figures and verdicts are marked same / DIFF / only a / only b,
identity figures are labelled, a check-level result is under its check's id, each run's
detail is shown, and a run folder without record.json is void (exit 2, nothing written).
Can-fails: the same checks run against wrong stand-ins of the tool's parts (the first run
standing instead of the last; a state that calls every pair "same"; identity not
labelled) and must fail. No hardware.

    python3.13 -B test_run_pair.py     # exit 0 only if every check passes

Gen-inhibit tester, 2026-11-21.
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402
import run_pair as RP  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("%s  %s  %s" % ("ok  " if cond else "FAIL", label, " | ".join(str(detail).splitlines())[:160]))
    if not cond:
        FAILS.append(label)


def res(rid, verdict, figs=None, detail=""):
    return {"id": rid, "verdict": verdict, "detail": detail,
            "figures": {k: {"unit": "", "value": v} for k, v in (figs or {}).items()}}


# side a: a first run and a re-run of B20; side b: one run
A1 = {"results": [res("man_app/B3/B3", "PASS", {"arms": 20, "stale": 0}, "20 arms, stale 0"),
                  res("man_app/B20/B20", "FAIL", {"as_specified": 27}, "27 of 28"),
                  res("man_app/B17a/B17a", "PASS", {"page_elf_sha256": "f515a383"}),
                  res("man_app/B27", "NOT-RUN", None, "skipped: needs the user"),
                  res("man_app/B1/B1", "PASS", {"passed": 20})]}
A2 = {"results": [res("man_app/B20/B20", "PASS", {"as_specified": 28}, "28 of 28")]}
B1 = {"results": [res("man_app/B3/B3", "PASS", {"arms": 20, "stale": 1}, "20 arms, stale 1"),
                  res("man_app/B20/B20", "PASS", {"as_specified": 28}, "28 of 28"),
                  res("man_app/B17a/B17a", "PASS", {"page_elf_sha256": "483bb940"}),
                  res("man_app/B27", "NOT-RUN", None, "skipped: needs the user"),
                  res("man_app/B26/B26", "PASS", {"ge2_loaded": 2559})]}


def by_id(entries):
    return {e["id"]: e for e in entries}


def rows(e):
    return {n: (st, va, vb) for n, st, va, vb in e["rows"]}


def core_checks(tag=""):
    """The checks on pair(); returns how many failed (for the can-fails)."""
    before = len(FAILS)
    e = by_id(RP.pair([A1, A2], [B1]))
    # first-seen order, side a first; within a run bench_compare.by_index_id puts judge
    # results first and check-level ones (B27's NOT-RUN) after them
    check(tag + "every id from both sides, in first-seen order",
          list(e) == ["B3", "B20", "B17a", "B1", "B27", "B26"], list(e))
    check(tag + "same figure same, differing figure DIFF",
          rows(e["B3"])["arms"][0] == "same" and rows(e["B3"])["stale"][0] == "DIFF", rows(e["B3"]))
    check(tag + "standing result is the last run that has the check (a: FAIL then PASS; b: PASS)",
          e["B20"]["verdict_state"] == "same" and rows(e["B20"])["as_specified"][0] == "same"
          and e["B20"]["verdicts_a"] == ["FAIL", "PASS"], (e["B20"]["verdict_state"], rows(e["B20"])))
    check(tag + "every run's value is listed, not only the standing one",
          rows(e["B20"])["as_specified"][1] == [27, 28] and rows(e["B20"])["as_specified"][2] == [28],
          rows(e["B20"]))
    check(tag + "identity figure labelled, not DIFF", rows(e["B17a"])["page_elf_sha256"][0] == "identity",
          rows(e["B17a"]))
    check(tag + "check-level NOT-RUN under the check's id, same on both sides",
          e["B27"]["verdicts_a"] == ["NOT-RUN", "-"] and e["B27"]["verdict_state"] == "same",
          (e["B27"]["verdicts_a"], e["B27"]["verdict_state"]))
    check(tag + "a check on one side only: verdict 'only a' / 'only b'",
          e["B1"]["verdict_state"] == "only a" and e["B26"]["verdict_state"] == "only b"
          and rows(e["B26"])["ge2_loaded"][0] == "only b",
          (e["B1"]["verdict_state"], e["B26"]["verdict_state"]))
    check(tag + "each run's detail kept", e["B20"]["details_a"] == ["27 of 28", "28 of 28"]
          and e["B27"]["details_b"] == ["skipped: needs the user"], (e["B20"]["details_a"], e["B27"]["details_b"]))
    return len(FAILS) - before


def write_run(root, name, rec):
    d = os.path.join(root, name)
    os.makedirs(d)
    if rec is not None:
        with open(os.path.join(d, "record.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f)
    return d


def main_checks():
    root = gi_paths.scratch("run_pair")
    a1, a2 = write_run(root, "run_a1", A1), write_run(root, "run_a2", A2)
    b1, void = write_run(root, "run_b1", B1), write_run(root, "run_void", None)
    out = os.path.join(root, "pair.txt")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = RP.main(["--a", a1, a2, "--b", b1, "--out", out])
    text = buf.getvalue()
    check("main: exit 0, the report written and printed", rc == 0 and os.path.isfile(out)
          and open(out, encoding="utf-8").read() == text, rc)
    check("main: the report names both sides' runs and marks B3's DIFF and B26's only b",
          "side a: run_a1, run_a2" in text and "side b: run_b1" in text
          and any(l.startswith("     DIFF") and "stale" in l for l in text.splitlines())
          and any(l.startswith("DIFF B26") for l in text.splitlines()), text[:300])
    check("main: B20's verdict line not marked (same standing verdict)",
          any(l.startswith("     B20") for l in text.splitlines()), text[:300])
    out2 = os.path.join(root, "pair_void.txt")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = RP.main(["--a", a1, "--b", void, "--out", out2])
    check("main: a run with no record.json is void: exit 2, nothing written",
          rc == 2 and not os.path.exists(out2) and "VOID" in buf.getvalue(), (rc, buf.getvalue()[:120]))


def can_fails():
    real_standing, real_state = RP.standing, RP.state
    try:
        RP.standing = lambda per_run, cid: next((p[cid] for p in per_run if cid in p), None)
        with contextlib.redirect_stdout(io.StringIO()):
            n = core_checks("[first run stands] ")
        del FAILS[len(FAILS) - n:]
        check("can-fail: the checks fail when the FIRST run stands", n > 0, n)
        RP.standing = real_standing
        RP.state = lambda a, b, name=None: "same"
        with contextlib.redirect_stdout(io.StringIO()):
            n = core_checks("[all same] ")
        del FAILS[len(FAILS) - n:]
        check("can-fail: the checks fail when every pair reads same", n > 0, n)
        RP.state = lambda a, b, name=None: real_state(a, b, None)
        with contextlib.redirect_stdout(io.StringIO()):
            n = core_checks("[no identity] ")
        del FAILS[len(FAILS) - n:]
        check("can-fail: the checks fail when identity figures are not labelled", n > 0, n)
    finally:
        RP.standing, RP.state = real_standing, real_state


def main():
    core_checks()
    main_checks()
    can_fails()
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
