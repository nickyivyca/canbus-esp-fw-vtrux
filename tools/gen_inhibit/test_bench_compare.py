#!/usr/bin/env python3
"""Tests for bench_compare.py on a made-up index and made-up core records: what is
flagged, what re-runs, what is labelled, and the chain's control flow (the core and
the suite stood in for). No hardware, no core needed.

    py -3.14 -B test_bench_compare.py

Prints "ok ..." or "FAIL ..." per check and PASS or FAIL at the end; exit 0 only on
PASS. Gen-inhibit tester, 2026-10-19.
"""
import copy
import io
import json
import os
import shutil
import sys
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))


def scratch_dir(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and
    folders"), never the system temp folder: bench/gi_paths.scratch()'s rule, written out
    here because a mutant's copy of this file has no bench/ beside it. The worktree is
    found by walking up to its .git, so a copy in its scratch/ finds the same one."""
    import time
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("%s is in no git worktree" % __file__)
        d = os.path.dirname(d)
    d = os.path.join(d, "scratch", "gen-inhibit-tests", name, "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d
sys.path.insert(0, HERE)
import bench_compare as BC   # noqa: E402

fails = []
oks = 0
TMP = scratch_dir("bench_compare")


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


INDEX = {"checks": [
    {"id": "X1", "part": "B", "verdict": "PASS", "figures": {"t_min": 10, "t_max": 20, "n": 5}},
    {"id": "X2", "part": "B", "verdict": "PASS", "figures": {"page_elf_sha256": "aaaa1111"}},
    {"id": "X3", "part": "C", "verdict": "PASS", "figures": {"k": 1}, "stage_a": "replaced in stage (a)"},
    {"id": "X4", "part": "B", "verdict": "PASS", "figures": {}},
    {"id": "X5", "part": "B", "verdict": "PASS", "figures": {"c": 3}},
    {"id": "X6", "part": "B", "verdict": "NOT-RUN", "figures": {}},
    {"id": "D", "part": "B", "verdict": "PASS", "figures": {"m": 1}},
]}
IDS_OF = {"g/X1": ["X1"], "g/X2": ["X2"], "h/X3": ["X3"], "g/X4X5": ["X4", "X5"], "g/X6": [], "g/D": ["D"],
          "g/arms": [], "g/pre": []}
READS = {"g/D": ["g/arms"], "g/arms": ["g/pre"]}


def res(rid, verdict="PASS", **figs):
    return {"id": rid, "verdict": verdict, "figures": {k: {"value": v, "unit": ""} for k, v in figs.items()}}


def record(**over):
    """A run matching the index; over: {index id: result or None (absent)}."""
    rs = {"X1": res("g/X1/X1", t_min=10, t_max=20, n=5), "X2": res("g/X2/X2", page_elf_sha256="aaaa1111"),
          "X3": res("h/X3/X3", k=1), "X4": res("g/X4X5/X4"), "X5": res("g/X4X5/X5", c=3),
          "X6": res("g/X6", "NOT-RUN"), "D": res("g/D/D", m=1), "arms": res("g/arms")}
    rs.update(over)
    return {"results": [r for r in rs.values() if r is not None]}


def entry(entries, cid):
    return next(e for e in entries if e["id"] == cid)


def flagged(entries):
    return sorted(e["id"] for e in entries if e["flagged"])


def reran(entries):
    return sorted(e["id"] for e in entries if e["rerun"])


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    try:
        run(lambda: tests())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


def run(fn):
    try:
        fn()
    except Exception as e:
        check("the tests ran to the end", False, repr(e))
        raise


def cmp(rec, reruns=()):
    return BC.compare(INDEX, rec, reruns, IDS_OF)


def tests():
    E = cmp(record())
    check("the same run: nothing flagged, nothing re-run", flagged(E) == [] and reran(E) == [], (flagged(E), reran(E)))

    E = cmp(record(X1=res("g/X1/X1", t_min=12, t_max=20, n=5)))
    check("a figure inside its baseline spread is flagged, not re-run",
          flagged(E) == ["X1"] and reran(E) == [], (flagged(E), reran(E)))
    E = cmp(record(X1=res("g/X1/X1", t_min=9, t_max=20, n=5)))
    check("a _min below the spread re-runs", reran(E) == ["X1"], reran(E))
    E = cmp(record(X1=res("g/X1/X1", t_min=10, t_max=21, n=5)))
    check("a _max above the spread re-runs", reran(E) == ["X1"], reran(E))
    E = cmp(record(X1=res("g/X1/X1", t_min=10, t_max=20, n=6)))
    check("a figure with no spread re-runs on any change", flagged(E) == ["X1"] and reran(E) == ["X1"], reran(E))
    E = cmp(record(X1=res("g/X1/X1", t_min=20, t_max=20, n=5)))
    check("the spread's own edges are inside it", flagged(E) == ["X1"] and reran(E) == [], (flagged(E), reran(E)))
    E = cmp(record(X1=res("g/X1/X1", t_min="x", t_max=20, n=5)))
    check("a non-number where the spread is numeric re-runs", reran(E) == ["X1"], reran(E))

    E = cmp(record(X2=res("g/X2/X2", page_elf_sha256="bbbb2222")))
    check("an identity figure that changes is labelled, not flagged",
          flagged(E) == [] and any(r[1] == "identity" for r in entry(E, "X2")["rows"]), entry(E, "X2"))
    E = cmp(record(X2=res("g/X2/X2", "FAIL", page_elf_sha256="bbbb2222")))
    check("an identity check must still PASS", flagged(E) == ["X2"] and reran(E) == ["X2"], flagged(E))

    E = cmp(record(X3=res("h/X3/X3", "FAIL", k=9)))
    check("a stage (a) check is labelled, not compared", flagged(E) == [] and reran(E) == []
          and "stage (a)" in " ".join(entry(E, "X3")["notes"]), entry(E, "X3"))

    E = cmp(record(X5=res("g/X4X5/X5", "FAIL", c=3)))
    check("a verdict that differs is flagged and re-run", flagged(E) == ["X5"] and reran(E) == ["X5"], flagged(E))
    check("... and its check is the one re-run", BC.rerun_checks(E, READS) == ["g/X4X5"], BC.rerun_checks(E, READS))

    E = cmp(record(X4=None, X5=None, NR=res("g/X4X5", "NOT-RUN")))
    check("a check-level NOT-RUN reaches every id its check judges",
          entry(E, "X4")["verdicts"] == ["NOT-RUN"] and entry(E, "X5")["verdicts"] == ["NOT-RUN"]
          and flagged(E) == ["X4", "X5"], [entry(E, i)["verdicts"] for i in ("X4", "X5")])

    E = cmp(record(RC={"id": "g/X4X5/rc", "verdict": "ERROR", "detail": "the runner script exited 1"}))
    t = BC.report_text(E, ["r1"])
    check("an exit-code ERROR is shown against each id of its check",
          entry(E, "X4")["extra"][0] and entry(E, "X5")["extra"][0] and t.count("the runner script exited 1") == 2, t)

    E = cmp(record())
    check("NOT-RUN in both is not flagged", not entry(E, "X6")["flagged"], entry(E, "X6"))

    E = cmp(record(X5=res("g/X4X5/X5")))
    check("a baseline figure missing from the run is flagged and re-run", flagged(E) == ["X5"] and reran(E) == ["X5"])
    E = cmp(record(X4=res("g/X4X5/X4", extra=1)))
    check("a figure the baseline does not have is flagged", flagged(E) == ["X4"], flagged(E))

    E = cmp(record(X1=None))
    check("a check not in the run (not selected) is noted, not flagged",
          not entry(E, "X1")["flagged"] and "not in the first run" in " ".join(entry(E, "X1")["notes"]))

    E = cmp(record(D=res("g/D/D", m=2)))
    check("a desk check re-runs with what it reads, transitively",
          BC.rerun_checks(E, READS) == ["g/D", "g/arms", "g/pre"], BC.rerun_checks(E, READS))

    first = record(X1=res("g/X1/X1", t_min=9, t_max=20, n=5))
    again = record(X1=res("g/X1/X1", t_min=11, t_max=20, n=5), X5=res("g/X4X5/X5", "FAIL", c=3))
    E = cmp(first, [again])
    t = BC.report_text(E, ["r1", "r2"])
    check("the report shows baseline, first run and re-run side by side",
          "baseline=10  first=9  re-run 1=11" in t, t)
    check("a re-run's verdict that differs is flagged", entry(E, "X5")["flagged"], entry(E, "X5"))
    check("re-runs are decided by the first run only", reran(E) == ["X1"], reran(E))

    E = cmp(record(X5=res("g/X4X5/X5", "FAIL", c=3)), [record()])
    x5 = entry(E, "X5")
    check("a FAIL that passes on re-run: still flagged, reported FAIL first, noted flaky",
          x5["flagged"] and x5["verdicts"][0] == "FAIL" and any("flaky, needs review" in n for n in x5["notes"]), x5)
    E = cmp(record(X5=res("g/X4X5/X5", "FAIL", c=3)), [record(X5=res("g/X4X5/X5", "FAIL", c=3))])
    check("a FAIL that fails again is not called flaky",
          not any("flaky" in n for n in entry(E, "X5")["notes"]), entry(E, "X5")["notes"])
    # reviewer, 2026-10-20: either order is flaky and reads the same
    E = cmp(record(X5=res("g/X4X5/X5", c=4)), [record(X5=res("g/X4X5/X5", "FAIL", c=3))])
    x5p = entry(E, "X5")
    check("a PASS that fails on re-run: flagged, reported PASS first, noted flaky",
          x5p["flagged"] and x5p["verdicts"][0] == "PASS"
          and x5p["notes"] == ["flaky, needs review: PASS first, FAIL on re-run"], x5p)
    check("both orders read the same: one flaky note, nothing order-specific",
          x5["notes"] == ["flaky, needs review: FAIL first, PASS on re-run"], x5["notes"])
    E = cmp(record(X5=res("g/X4X5/X5", "NOT-RUN")), [record()])
    check("NOT-RUN then PASS is not called flaky (still flagged)",
          entry(E, "X5")["flagged"] and not any("flaky" in n for n in entry(E, "X5")["notes"]), entry(E, "X5")["notes"])
    E = cmp(record(), [record(X5=res("g/X4X5/X5", "NOT-RUN"))])
    check("PASS then NOT-RUN on re-run is not called flaky",
          not any("flaky" in n for n in entry(E, "X5")["notes"]), entry(E, "X5")["notes"])
    E = cmp(record(X5=res("g/X4X5/X5", "FAIL", c=3)), [record(X5=res("g/X4X5/X5", "ERROR", c=3))])
    check("FAIL then ERROR, neither the baseline's, is not called flaky",
          entry(E, "X5")["flagged"] and not any("flaky" in n for n in entry(E, "X5")["notes"]), entry(E, "X5")["notes"])
    check("the report names what was re-run", "re-run: g/X1" in t, t.splitlines()[1])

    out = "lane bench:\nrun gen-inhibit-bench_20261019T0101_ab12: C:\\r\\runs\\gen-inhibit-bench_20261019T0101_ab12\\index.txt\n"
    m = BC.RUN_LINE.search(out)
    check("the core's run line gives the run folder",
          m and m.group(2).rstrip("\\/") == "C:\\r\\runs\\gen-inhibit-bench_20261019T0101_ab12", m and m.group(2))

    d = os.path.join(TMP, "fin")
    os.makedirs(d)
    with contextlib.redirect_stdout(io.StringIO()):
        rc0 = BC.finish(cmp(record()), [d], d)
    check("finish writes the report and exits 0 when nothing is flagged",
          rc0 == 0 and os.path.isfile(os.path.join(d, "compare_index.txt"))
          and os.path.isfile(os.path.join(d, "compare_index.json")))
    e = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            BC.finish(cmp(record()), [d], d)
    except FileExistsError as x:
        e = x
    check("finish never overwrites a report", e is not None)
    d2 = os.path.join(TMP, "fin2")
    os.makedirs(d2)
    with contextlib.redirect_stdout(io.StringIO()):
        rc1 = BC.finish(cmp(record(X5=res("g/X4X5/X5", "FAIL", c=3))), [d2], d2)
    check("finish exits 1 when anything is flagged", rc1 == 1)

    test_chain()


def test_chain():
    calls = []

    def fake(first, second):
        def core_run(settings_path, groups=(), checks=()):
            calls.append((list(groups), list(checks)))
            k = len(calls)
            d = os.path.join(TMP, "chain%d_%d" % (len(fakes), k))
            os.makedirs(d)
            with open(os.path.join(d, "record.json"), "w") as f:
                json.dump(first if k == 1 else second, f)
            return 0, d, ""
        return core_run
    fakes = []
    sp = os.path.join(TMP, "settings.json")
    ip = os.path.join(TMP, "index.json")
    with open(ip, "w") as f:
        json.dump(INDEX, f)
    with open(sp, "w") as f:
        json.dump({"gi_index": ip}, f)
    saved = BC.core_run, BC.suite_maps
    BC.suite_maps = lambda s: (READS, IDS_OF)
    try:
        for first, second, want, name in (
                (record(), None, [([], [])], "nothing flagged: one core run"),
                (record(D=res("g/D/D", m=2)), record(D=res("g/D/D", m=1)),
                 [(["G"], []), ([], ["g/D", "g/arms", "g/pre"])], "a flagged desk check: re-run with its inputs")):
            calls.clear()
            fakes.append(1)
            BC.core_run = fake(first, second)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = BC.main(["chain", "--settings", sp] + (["--group", "G"] if second else []))
            check("chain: %s" % name, calls == want, calls)
            if second:
                rep = open(os.path.join(TMP, "chain%d_1" % len(fakes), "compare_index.txt"), encoding="utf-8").read()
                check("chain: the report is in the first run's folder, with the re-run beside it",
                      "first=2  re-run 1=1" in rep, rep)
                check("chain: a figure that moved is flagged even if the re-run is back (exit 1)", rc == 1)
        calls.clear()
        BC.core_run = lambda *a, **k: (calls.append(1), (2, None, "REFUSED"))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = BC.main(["chain", "--settings", sp])
        except Exception as x:
            rc = "RAISED %r" % x
        check("chain: a run the core refused is not compared or re-run (exit 2)", rc == 2 and calls == [1], (rc, calls))

        def killed(first_has_record, second_has_record, n):
            """A core run killed after it made its folder: the folder, no record.json."""
            def core_run(settings_path, groups=(), checks=()):
                calls.append(1)
                d = os.path.join(TMP, "killed%d_%d" % (n, len(calls)))
                os.makedirs(d)
                if (first_has_record if len(calls) == 1 else second_has_record):
                    with open(os.path.join(d, "record.json"), "w") as f:
                        json.dump(record(D=res("g/D/D", m=2)), f)
                return (0 if len(calls) == 1 and first_has_record else None), d, ""
            return core_run

        def chain(core_run):
            calls.clear()
            BC.core_run = core_run
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = BC.main(["chain", "--settings", sp])
            except Exception as x:
                rc = "RAISED %r" % x
            return rc, out.getvalue()
        rc, out = chain(killed(False, False, 1))
        check("chain: a killed run (folder, no record.json) is void: not compared or re-run (exit 2)",
              rc == 2 and calls == [1] and not os.path.exists(os.path.join(TMP, "killed1_1", "compare_index.txt")),
              (rc, calls, out))
        check("chain: and it says the run did not finish, not that it did not start",
              "did not finish" in out and "void" in out and "did not start" not in out, out)
        BC.core_run = lambda *a, **k: (calls.append(1), (None, None, ""))[1]
        rc, out = chain(BC.core_run)
        check("chain: a run killed before it named its folder did not finish either",
              rc == 2 and "did not finish" in out and "did not start" not in out, (rc, out))
        rc, out = chain(killed(True, False, 2))
        rep = ""
        if os.path.exists(os.path.join(TMP, "killed2_1", "compare_index.txt")):
            rep = open(os.path.join(TMP, "killed2_1", "compare_index.txt"), encoding="utf-8").read()
        runs = []
        if os.path.exists(os.path.join(TMP, "killed2_1", "compare_index.json")):
            runs = json.load(open(os.path.join(TMP, "killed2_1", "compare_index.json"), encoding="utf-8"))["runs"]
        check("chain: a killed re-run is void: the first run is reported alone",
              rc == 1 and calls == [1, 1] and "re-run 1" not in rep and "first=2" in rep
              and rep.splitlines()[:1] == ["Compared with the baseline index: killed2_1"] and runs == ["killed2_1"]
              and "the re-run did not finish" in out, (rc, calls, out, rep, runs))

        def report(*dirs):
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = BC.main(["report", "--settings", sp] + list(dirs))
            except Exception as x:
                rc = "RAISED %r" % x
            return rc, out.getvalue()
        good, dead = os.path.join(TMP, "killed2_1"), os.path.join(TMP, "killed1_1")
        rc, out = report(good)
        check("report: a finished run is compared (exit 0)", rc == 0 and "first=2" in out, (rc, out))
        rc, out = report(dead)
        check("report: a run with no record.json is VOID, named, exit 2, no traceback",
              rc == 2 and ("VOID: %s has no record.json" % dead) in out, (rc, out))
        rc, out = report(good, dead)
        check("report: a void re-run voids the report too", rc == 2 and ("VOID: %s" % dead) in out, (rc, out))
    finally:
        BC.core_run, BC.suite_maps = saved


if __name__ == "__main__":
    sys.exit(main())
