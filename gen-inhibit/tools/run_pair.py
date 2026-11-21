#!/usr/bin/env python3
"""Two sets of chain-runner runs side by side, one block per check: each run's verdict and
every figure, and whether the two sides' standing results are the same.

Port plan step 3 (esp-shared-code-port.md, step 3, "Accepted by", third bullet) lists every
difference between gen-inhibit's suites on madhouse-debian and the existing Windows records.
This prints both sides from the runs' own record.json files, so the list in
VERIFICATION.md rests on a committed tool (AGENTS.md, "Spec, validation and verification").
It judges nothing and names no cause: the causes are written in the entry.

    python3.13 -B run_pair.py --a <run dir> [<run dir>...] --b <run dir> [<run dir>...] [--out FILE]

Each side's runs are given in time order: a first run, then its re-runs. A check's
standing result on a side is its result in the LAST run of that side that has it (a re-run
of a flagged check, or a later run of the same check, stands over an earlier one). A
figure is "same" when both sides' standing values are equal, "DIFF" when they are not,
"only a" / "only b" when one side has no value; identity figures (bench_compare.IDENTITY)
change with every image and are labelled "identity". The verdict line is marked the same
way. Ids come from bench_compare.by_index_id, as in the compare reports; a check-level
result (NOT-RUN, an exit code) is under the check's own id.

A run folder with no record.json (killed, or still running) is void: nothing is printed
and the exit is 2. Otherwise exit 0. Gen-inhibit tester, 2026-11-21.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_compare as BC  # noqa: E402

NONE = "-"


def fmt(v):
    if v is None:
        return NONE
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, separators=(",", ":"))
    return str(v)


def standing(per_run, cid):
    """The last run's entry for cid on one side, or None."""
    for p in reversed(per_run):
        if cid in p:
            return p[cid]
    return None


def state(a, b, name=None):
    if name in BC.IDENTITY:
        return "identity"
    if a is None and b is None:
        return "same"
    if a is None:
        return "only b"
    if b is None:
        return "only a"
    return "same" if a == b else "DIFF"


def pair(recs_a, recs_b):
    """[{"id", "verdicts_a", "verdicts_b", "verdict_state", "rows": [(name, state, [a vals],
    [b vals])], "details_a", "details_b"}] in the order the ids first appear, side a first."""
    pa = [BC.by_index_id(r) for r in recs_a]
    pb = [BC.by_index_id(r) for r in recs_b]
    det_a = [{x["id"]: x.get("detail", "") for x in r.get("results", [])} for r in recs_a]
    det_b = [{x["id"]: x.get("detail", "") for x in r.get("results", [])} for r in recs_b]
    ids = []
    for p in pa + pb:
        for i in p:
            if i not in ids:
                ids.append(i)
    out = []
    for i in ids:
        sa, sb = standing(pa, i), standing(pb, i)
        e = {"id": i,
             "verdicts_a": [p[i]["verdict"] if i in p else NONE for p in pa],
             "verdicts_b": [p[i]["verdict"] if i in p else NONE for p in pb],
             "verdict_state": state(sa and sa["verdict"], sb and sb["verdict"]),
             "details_a": [detail(d, p.get(i), i) for d, p in zip(det_a, pa)],
             "details_b": [detail(d, p.get(i), i) for d, p in zip(det_b, pb)],
             "rows": []}
        names = sorted(set((sa or {}).get("figures", {})) | set((sb or {}).get("figures", {}))
                       | {k for p in pa + pb if i in p for k in p[i]["figures"]})
        for n in names:
            va = [p[i]["figures"].get(n) if i in p else None for p in pa]
            vb = [p[i]["figures"].get(n) if i in p else None for p in pb]
            e["rows"].append((n, state((sa or {}).get("figures", {}).get(n), (sb or {}).get("figures", {}).get(n), n), va, vb))
        out.append(e)
    return out


def detail(details, entry, cid):
    """The detail line of this run's result for cid, or "" if the run has none."""
    if not entry:
        return ""
    d = details.get(entry["check"] + "/" + cid, details.get(entry["check"], entry.get("detail", "")))
    return " ".join(str(d).split())


def report_text(entries, names_a, names_b):
    lines = ["side a: %s" % ", ".join(names_a), "side b: %s" % ", ".join(names_b),
             "standing result: the last run on each side that has the check", ""]
    for e in entries:
        mark = "    " if e["verdict_state"] == "same" else "DIFF"
        lines.append("%s %-12s verdict  a: %s  |  b: %s  [%s]" % (
            mark, e["id"], " ".join(e["verdicts_a"]), " ".join(e["verdicts_b"]), e["verdict_state"]))
        for side, names, dets in (("a", names_a, e["details_a"]), ("b", names_b, e["details_b"])):
            for n, d in zip(names, dets):
                if d:
                    lines.append("       %s %s: %s" % (side, n, d[:240]))
        for n, st, va, vb in e["rows"]:
            lines.append("     %-8s %-40s a: %s  |  b: %s" % (st, n, " ".join(fmt(v) for v in va),
                                                           " ".join(fmt(v) for v in vb)))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", nargs="+", required=True)
    ap.add_argument("--b", nargs="+", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    recs_a = [BC.run_record(d) for d in a.a]
    recs_b = [BC.run_record(d) for d in a.b]
    void = [d for d, r in zip(a.a + a.b, recs_a + recs_b) if r is None]
    if void:
        for d in void:
            print("VOID: %s has no record.json (killed, or still running); nothing paired" % d)
        return 2
    name = lambda d: os.path.basename(os.path.normpath(d))  # noqa: E731
    text = report_text(pair(recs_a, recs_b), [name(d) for d in a.a], [name(d) for d in a.b])
    if a.out:
        with open(a.out, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
