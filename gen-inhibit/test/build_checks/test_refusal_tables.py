#!/usr/bin/env python3
"""Tests test_refusal_pairs.py's list of switches to prove, table_switches(), on
throwaway projects.

Gen-inhibit tester, 2026-10-29, port step 1a's rest. The stock features' entries moved
from the project's table to vtrux/wican's own (shared/tools R20), so the 12.3 refusal
proof must take its switches from the project's table and every shared component's,
read from the copies the --off image's build downloaded. Otherwise the stock features
would drop out of the proof with nothing failing.

    python3 test_refusal_tables.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as BO                                       # noqa: E402
import build_switches as BS                                     # noqa: E402
import test_refusal_pairs as P                                  # noqa: E402

TMP = os.path.join(BO.WORKTREE, "scratch", "build-checks", "test_refusal_tables")
BUILD = os.path.join(TMP, "build")
OFF = os.path.join(BUILD, "wican-fw_obd_man.bin")
MC = os.path.join(TMP, "managed_components")
WICAN_TABLE = {"build_switches": {
    "WICAN_FEATURE_X": {"class": BS.STOCK, "reason": "r", "refusal": [{"elf_symbols_absent": ["x_init"]}]},
    "SOME_FAULT": {"class": BS.TEST, "reason": "r", "refusal": [{"bin_strings_absent": ["FAULT"]}]},
    "SOME_DEFAULT": {"class": BS.NOT, "reason": "r", "kind": "default value"},
}}


def project(names=("vtrux/wican_v3", "vtrux/wican", "vtrux/debug_logs"), folders=None, wican_text=None,
            build_file=True):
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(BUILD)
    with open(os.path.join(BUILD, "project_description.json"), "w") as fh:
        json.dump({"project_path": TMP}, fh)
    if build_file:
        with open(os.path.join(TMP, BO.BUILD_FILE), "w") as fh:
            json.dump({"project": "gen-inhibit", "shared_components": list(names)}, fh)
    for n in folders if folders is not None else names:
        os.makedirs(os.path.join(MC, n.replace("/", "__")))
    w = os.path.join(MC, "vtrux__wican")
    if os.path.isdir(w):
        with open(os.path.join(w, "vtrux_checks.json"), "w") as fh:
            fh.write(wican_text if wican_text is not None else json.dumps(WICAN_TABLE))


def main():
    checks = []
    own = {s for s, e in BS.SWITCHES.items() if e[0] in (BS.TEST, BS.STOCK)}

    project()
    got, problems = P.table_switches(OFF)
    checks.append(("a component's stock feature and test-only switch are proved", not problems
                   and got.get("WICAN_FEATURE_X") == BS.STOCK and got.get("SOME_FAULT") == BS.TEST, (got, problems)))
    checks.append(("... with the project's own test-only switches", own <= set(got), sorted(own - set(got))))
    checks.append(("... and not a component's 'not a switch' entry", "SOME_DEFAULT" not in got, ""))
    checks.append(("a component with no table (debug_logs, the board here) is no problem", not problems, problems))

    project(folders=("vtrux/wican_v3", "vtrux/debug_logs"))
    got, problems = P.table_switches(OFF)
    checks.append(("a shared component with no copy in managed_components/ is a problem, naming it",
                   any("vtrux/wican" in p for p in problems) and "WICAN_FEATURE_X" not in got, problems))

    project(wican_text="{ not json")
    got, problems = P.table_switches(OFF)
    checks.append(("a component table that does not load is a problem", len(problems) == 1, problems))

    project(build_file=False)
    got, problems = P.table_switches(OFF)
    checks.append(("no vtrux_build.json is a problem", len(problems) == 1 and BO.BUILD_FILE in problems[0], problems))

    project(names=("vtrux/wican_v3", "vtrux/debug_logs"), folders=("vtrux/wican_v3", "vtrux/debug_logs", "vtrux/wican"))
    got, problems = P.table_switches(OFF)
    checks.append(("a component vtrux_build.json does not name is not read", "WICAN_FEATURE_X" not in got
                   and not problems, (sorted(got), problems)))

    shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    for label, ok, detail in checks:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, "" if ok else "  -- " + str(detail)[:140]))
        fails += not ok
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
