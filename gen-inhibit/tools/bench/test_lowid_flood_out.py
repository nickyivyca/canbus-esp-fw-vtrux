"""Harness test for lowid_flood_skip_bench.py's record handling: a run must
never overwrite an earlier run's record.

Why: until 2026-10-07 the harness wrote every run to one fixed path, and four
of five runs on 2026-10-06 were lost that way; figures from the lost runs then
circulated with no record behind them.

Each check below is paired with the way it would fail if the guard were
removed, and the self-check at the end runs a deliberately overwriting writer
through the same assertion to show the assertion can fail.

The test's own files go in its run folder in the worktree's scratch/
(gi_paths.scratch, removed when the test exits, pass or fail), never under the
real runs/ (B.RUNS). Until 2026-11-12 it made and removed a folder in the real
runs/ (found by the re-pin evidence run, runs/repin_e59f661).

Run from the repo root:  python3.13 gen-inhibit/tools/bench/test_lowid_flood_out.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lowid_flood_skip_bench as B                              # noqa: E402
import gi_paths  # noqa: E402  scratch (AGENTS.md "Files and folders")

TMP = None      # the run folder, set by main()


def under(path, root):
    """True if path is root or inside it, after resolving links."""
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def check(label, ok, failures):
    print("  %-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append(label)


def preserved_after_second_write(writer):
    """Write record A, then record B to the same path. True if A survives."""
    path = os.path.join(TMP, "clash.json")
    for f in os.listdir(TMP):
        os.remove(os.path.join(TMP, f))
    writer(path, {"run": "A"})
    writer(path, {"run": "B"})
    with open(path) as fh:
        first = json.load(fh)
    found_b = any(json.load(open(os.path.join(TMP, f))).get("run") == "B"
                  for f in os.listdir(TMP))
    return first.get("run") == "A" and found_b


def overwriting_writer(out, rec):
    with open(out, "w") as fh:
        json.dump(rec, fh)
    return out


def main():
    global TMP
    failures = []
    # Removed at exit, pass or fail, by gi_paths.scratch's handler.
    TMP = gi_paths.scratch("lowid_flood_out", start=__file__)

    # 0. The test's own folder is in scratch/, not the real runs/.
    check("the test's folder is not under the real runs/",
          not under(TMP, B.RUNS), failures)
    check("self-check: check 0 FAILS for a folder under runs/",
          under(os.path.join(B.RUNS, "_test_lowid_flood_out"), B.RUNS),
          failures)

    # 1. Default path: timestamped, distinct per stamp, under runs/.
    a = B.run_out_path(None, "20261007T120000")
    b = B.run_out_path(None, "20261007T120001")
    check("default path is timestamped and differs per run", a != b and
          "20261007T120000" in a and os.path.dirname(a) == B.RUNS,
          failures)
    check("default path is not the old fixed name",
          os.path.basename(a) != "lowid_flood_skip.json", failures)

    # 2. An explicit --out that exists is refused up front.
    existing = os.path.join(TMP, "existing.json")
    with open(existing, "w") as fh:
        fh.write("{}")
    try:
        B.run_out_path(existing, "x")
        refused = False
    except SystemExit:
        refused = True
    check("existing --out is refused before the run", refused, failures)
    new = os.path.join(TMP, "new.json")
    check("non-existing --out is accepted unchanged",
          B.run_out_path(new, "x") == new, failures)

    # 3. write_record never replaces a record that appeared mid-run.
    check("write_record keeps the first record and writes the second "
          "beside it", preserved_after_second_write(B.write_record),
          failures)

    # 4. Self-check: the same assertion catches an overwriting writer.
    check("self-check: assertion 3 FAILS for a writer that overwrites",
          not preserved_after_second_write(overwriting_writer), failures)

    print("\n=== %s ===" % ("PASS" if not failures else
                            "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
