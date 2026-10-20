#!/usr/bin/env python3
"""Compare chain-runner bench runs with the baseline index, and re-run what moved.

The rule (user, 2026-10-19, option a; the reviewer's ruling the same day):
  - each check's verdict must match the index's exactly;
  - every figure that differs from the baseline is flagged, no threshold, with the
    baseline, the first run and any re-runs side by side;
  - a flagged figure re-runs its check once, automatically: a figure with a baseline
    spread (its _min/_max pair across arms) when it falls outside that spread; any
    other figure on any change;
  - identity figures (page_elf_sha256, diag_git_hash) change with every image: labelled,
    not flagged. Their checks must still PASS;
  - part C checks marked stage_a in the index are replaced in stage (a): labelled, not
    compared.
A verdict that differs is flagged and re-run too, so a re-run can tell a one-off FAIL
from a repeatable one; the reviewer reviews every flagged difference either way.

    PYTHONPATH="<core>/shared/bench;<interposer clone>/charge-interposer" \
    py -3.14 -B bench_compare.py chain  --settings <json> [--group G]... [--check C]...
    py -3.14 -B bench_compare.py report --settings <json> <first run dir> [<re-run dir>...]

chain runs the bench suite (vtrux_bench.run run), compares, runs the flagged checks
again in one more core run (with the checks their desk steps read), and writes
compare_index.txt and compare_index.json into the first run's folder. The bench lock
is held throughout; nothing here takes or releases it. The index is settings.gi_index.
Gen-inhibit tester, 2026-10-19.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IDENTITY = {"page_elf_sha256", "diag_git_hash"}
NO_RESULT = "-"
RUN_LINE = re.compile(r"^run (\S+): (.+)index\.txt\s*$", re.M)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def by_index_id(record, ids_of=None):
    """{index id: {"verdict", "figures": {name: value}, "check", "extra": [...]}} from a
    core record. A judge's result is named by its sub (the index id); a check-level
    result (NOT-RUN, an ERROR for the exit code or a timeout) goes to every id the
    check judges (ids_of: {"group/check": [ids]}; without it, the check's own id)."""
    ids_of = ids_of or {}
    out, extra, check_level = {}, {}, {}
    for r in record.get("results", []):
        parts = r["id"].split("/")
        fid, sub = "/".join(parts[:2]), "/".join(parts[2:])
        if sub in ("", "rc", "timeout"):
            (check_level if sub == "" else extra).setdefault(fid, []).append(r)
            continue
        out[sub] = {"verdict": r["verdict"], "check": fid, "extra": [],
                    "figures": {k: v.get("value") for k, v in (r.get("figures") or {}).items()}}
    for fid, rs in check_level.items():
        for i in ids_of.get(fid) or [fid.split("/")[1]]:
            if i not in out:
                out[i] = {"verdict": rs[0]["verdict"], "check": fid, "figures": {}, "extra": [],
                          "detail": rs[0].get("detail", "")}
    for fid, rs in extra.items():
        for e in out.values():
            if e["check"] == fid:
                e["extra"] += ["%s %s" % (r["verdict"], r.get("detail", "")) for r in rs]
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def spread(base_figs, name):
    """(lo, hi) of the figure's baseline _min/_max pair, or None."""
    m = re.match(r"^(.*)_(min|max)$", name)
    if not m:
        return None
    lo, hi = base_figs.get(m.group(1) + "_min"), base_figs.get(m.group(1) + "_max")
    return (lo, hi) if _num(lo) and _num(hi) else None


def figure_rows(base, run):
    """[(name, state, rerun, note)]: state same | DIFF | identity."""
    rows = []
    for k in sorted(set(base) | set(run)):
        b, r = base.get(k, NO_RESULT), run.get(k, NO_RESULT)
        if k in IDENTITY:
            rows.append((k, "identity", False, "changes with the image; labelled, not flagged"))
        elif b == r:
            rows.append((k, "same", False, ""))
        else:
            sp = spread(base, k)
            if sp and _num(r):
                inside = sp[0] <= r <= sp[1]
                rows.append((k, "DIFF", not inside, "%s the baseline spread %s..%s" % (
                    "inside" if inside else "outside", sp[0], sp[1])))
            else:
                rows.append((k, "DIFF", True, "no baseline spread: any change re-runs"))
    return rows


def compare(index, first, reruns=(), ids_of=None):
    """One entry per index check, in the index's order."""
    runs = [by_index_id(first, ids_of)] + [by_index_id(r, ids_of) for r in reruns]
    out = []
    for c in index["checks"]:
        cid = c["id"]
        got = [r.get(cid) for r in runs]
        e = {"id": cid, "part": c["part"], "baseline": c["verdict"], "base_figures": c.get("figures") or {},
             "verdicts": [g["verdict"] if g else NO_RESULT for g in got],
             "figures": [g["figures"] if g else {} for g in got],
             "extra": [g["extra"] if g else [] for g in got],
             "check": next((g["check"] for g in got if g), None),
             "flagged": False, "rerun": False, "notes": [], "rows": []}
        if c.get("stage_a"):
            e["notes"].append("replaced in stage (a): labelled, not compared")
            out.append(e)
            continue
        if not got[0]:
            e["notes"].append("not in the first run (not selected)")
            out.append(e)
            continue
        if got[0]["verdict"] != c["verdict"]:
            e["flagged"] = e["rerun"] = True
            e["notes"].append("VERDICT %s, the baseline's %s" % (got[0]["verdict"], c["verdict"]))
        e["rows"] = figure_rows(e["base_figures"], got[0]["figures"])
        for name, state, rerun, note in e["rows"]:
            if state == "DIFF":
                e["flagged"] = True
                e["rerun"] = e["rerun"] or rerun
        if any(c2 != c["verdict"] for c2 in e["verdicts"][1:] if c2 != NO_RESULT):
            e["flagged"] = True
            e["notes"].append("a re-run's verdict is not the baseline's")
        if got[0]["verdict"] != c["verdict"] and c["verdict"] in e["verdicts"][1:]:
            # reviewer, 2026-10-19: the first run's verdict is the one reported; a FAIL
            # that passes on re-run is never a PASS
            e["notes"].append("flaky, needs review: %s first, %s on re-run" % (got[0]["verdict"], c["verdict"]))
        out.append(e)
    return out


def rerun_checks(entries, reads_map):
    """The checks to run again: each flagged-for-re-run entry's check, and every check
    its desk steps read, transitively."""
    todo = sorted({e["check"] for e in entries if e["rerun"] and e["check"]})
    seen = []
    while todo:
        f = todo.pop(0)
        if f in seen:
            continue
        seen.append(f)
        todo += [d for d in reads_map.get(f, []) if d not in seen]
    return sorted(seen)


def _fmt(v):
    return NO_RESULT if v is None else str(v)


def report_text(entries, run_names):
    cols = ["baseline", "first"] + ["re-run %d" % k for k in range(1, len(run_names))]
    lines = ["Compared with the baseline index: %s" % ", ".join(run_names),
             "flagged: %d of %d; re-run: %s" % (
                 sum(e["flagged"] for e in entries), len(entries),
                 ", ".join(sorted({e["check"] for e in entries if e["rerun"]})) or "none"),
             ""]
    for e in entries:
        mark = "FLAG" if e["flagged"] else "    "
        lines.append("%s %-11s verdict  %s" % (mark, e["id"], "  ".join(
            "%s=%s" % (n, v) for n, v in zip(cols, [e["baseline"]] + e["verdicts"]))))
        for n in e["notes"]:
            lines.append("                 %s" % n)
        for k, x in enumerate(e["extra"]):
            for t in x:
                lines.append("                 %s: %s" % (cols[k + 1], t[:120]))
        for name, state, rerun, note in e["rows"]:
            if state == "same":
                continue
            vals = [e["base_figures"].get(name)] + [f.get(name) for f in e["figures"]]
            lines.append("     %-8s %-30s %s%s%s" % (
                state, name, "  ".join("%s=%s" % (n, _fmt(v)) for n, v in zip(cols, vals)),
                "  [%s]" % note if note else "", "  RE-RUN" if rerun else ""))
    return "\n".join(lines) + "\n"


def write_new(path, text):
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ------------------------------------------------------------------ the chain

def suite_maps(settings):
    sys.path.insert(0, HERE)
    import suite_gen_inhibit as SG
    s = SG.bench(settings)
    ids_of = {"%s/%s" % (g.name, c.id): list(getattr(c.judge, "ids", ()))
              for g in s.groups for c in g.checks}
    return SG.reads(s), ids_of


def core_run(settings_path, groups=(), checks=()):
    """One core run; (rc, run dir or None, its output)."""
    argv = [sys.executable, "-B", "-m", "vtrux_bench.run", "run", "--suite",
            os.path.join(HERE, "suite_gen_inhibit.py") + ":bench", "--settings", settings_path]
    for g in groups:
        argv += ["--group", g]
    for c in checks:
        argv += ["--check", c]
    p = subprocess.run(argv, capture_output=True, text=True)
    out = p.stdout + p.stderr
    sys.stdout.write(out)
    m = RUN_LINE.search(p.stdout)
    return p.returncode, (m.group(2).rstrip("\\/") if m else None), out


def finish(entries, run_dirs, out_dir):
    names = [os.path.basename(os.path.normpath(d)) for d in run_dirs]
    text = report_text(entries, names)
    write_new(os.path.join(out_dir, "compare_index.txt"), text)
    write_new(os.path.join(out_dir, "compare_index.json"),
              json.dumps({"runs": names, "entries": entries}, indent=1, default=str))
    sys.stdout.write(text)
    return 1 if any(e["flagged"] for e in entries) else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chain")
    c.add_argument("--settings", required=True)
    c.add_argument("--group", action="append", default=[])
    c.add_argument("--check", action="append", default=[])
    r = sub.add_parser("report")
    r.add_argument("--settings", required=True)
    r.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    settings = load_json(a.settings)
    index = load_json(settings["gi_index"])
    reads_map, ids_of = suite_maps(settings)
    if a.cmd == "report":
        recs = [load_json(os.path.join(d, "record.json")) for d in a.runs]
        sys.stdout.write(report_text(compare(index, recs[0], recs[1:], ids_of),
                                     [os.path.basename(os.path.normpath(d)) for d in a.runs]))
        return 0
    rc, first, _ = core_run(a.settings, a.group, a.check)
    if not first:
        print("chain: the core run did not start (rc %s); nothing to compare" % rc)
        return 2
    rec1 = load_json(os.path.join(first, "record.json"))
    entries = compare(index, rec1, (), ids_of)
    again = rerun_checks(entries, reads_map)
    dirs = [first]
    if again:
        print("chain: re-running %s" % ", ".join(again))
        rc2, second, _ = core_run(a.settings, (), again)
        if second:
            dirs.append(second)
            entries = compare(index, rec1, [load_json(os.path.join(second, "record.json"))], ids_of)
        else:
            print("chain: the re-run did not start (rc %s)" % rc2)
    return finish(entries, dirs, first)


if __name__ == "__main__":
    sys.exit(main())
