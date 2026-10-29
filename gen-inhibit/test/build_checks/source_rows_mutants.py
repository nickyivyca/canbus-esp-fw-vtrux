"""Mutants of the build checks' port step 1a changes (the stock code in vtrux/wican,
2026-10-29): each must make its test fail -- test_source_rows.py, test_switch_rows.py,
test_build_switches.py or test_refusal_tables.py. m00 is build_output.py and t01 the
project's vtrux_checks.json as they were before the change (gen-inhibit/main a287e1c).
Controls first: each test passes on an unmutated copy. The copies go under the worktree's
scratch/build-checks/source_rows_mutants/ and are removed at the end.

    py -3.14 -B gen-inhibit/test/build_checks/source_rows_mutants.py"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # the worktree root
BEFORE = "a287e1c"                                            # gen-inhibit/main before the change
PROJECT_REL = "gen-inhibit"
SRC = os.path.join(ROOT, PROJECT_REL)
BC = os.path.join("test", "build_checks")

SR = "test_source_rows.py"
SW = "test_switch_rows.py"
BS = "test_build_switches.py"
MUTANTS = [
    # (name, file under build_checks, old, new, test)
    ("m01_shared_files_not_scanned", "build_output.py",
     "                if BS._inside(a, comp) and a.endswith((\".c\", \".h\")):",
     "                if False:", SR),
    ("m02_missing_component_accepted", "build_output.py",
     "    if missing:\n        return None, \"the build's record names no file compiled from",
     "    if False:\n        return None, \"the build's record names no file compiled from", SR),
    ("m03_headers_not_scanned", "build_output.py",
     "                if BS._inside(a, comp) and a.endswith((\".c\", \".h\")):",
     "                if BS._inside(a, comp) and a.endswith(\".c\"):", SR),
    ("m04_main_only_compiled", "build_output.py",
     "        if f.endswith((\".c\", \".h\")):\n            out[\"main/\" + f]",
     "        if f in (\"can.c\", \"gen_inhibit.c\", \"main.c\") and f.endswith((\".c\", \".h\")):\n            out[\"main/\" + f]", SR),
    ("m05_probe_not_exempt", "build_output.py",
     "        if rel in allowed or rel == PROBE:", "        if rel in allowed:", SR),
    ("m06_wifi_from_main", "build_output.py",
     "    wifi_c = os.path.join(wican, \"wifi_network.c\") if wican else None",
     "    wifi_c = os.path.join(project_path(build_dir or os.path.join(REPO, \"build\")), \"main\", \"wifi_network.c\")", SR),
    ("m07_every_managed_component", "build_output.py",
     "            for n in names if isinstance(n, str)}",
     "            for n in list(names) + [\"espressif/mdns\"] if isinstance(n, str)}", SR),
    ("m08_stale_records_counted", "build_output.py",
     "        for p in BS.parse_deps(d.stdout):",
     "        for p in [l.strip() for l in d.stdout.splitlines() if l.startswith(\"    \")]:", SR),
    ("m09_record_failure_skipped", "build_output.py",
     "        if d.returncode != 0:\n            return None, \"ninja -t deps in %s failed\" % folder",
     "        if d.returncode != 0:\n            continue", SR),
    ("m10_startup_is_app_main_only", "build_output.py",
     "STARTUP = (\"app_main\", \"wican_init\", \"wican_start\")", "STARTUP = (\"app_main\",)", SW),
    ("m11_old_row_name", "build_output.py",
     "REALDASH_ROW = \"no boot-time bus enable at start-up\"",
     "REALDASH_ROW = \"no boot-time bus enable in app_main\"", SW),
    ("m12_wired_check_on_repo", "build_output.py",
     "    gi = files.get(\"main/gen_inhibit.c\", \"\")",
     "    gi = os.path.join(REPO, \"main\", \"gen_inhibit_none.c\")", SR),
]
RT = "test_refusal_tables.py"
MUTANTS += [
    ("r01_component_tables_not_read", "test_refusal_pairs.py",
     "    for name, folder in sorted(shared.items()):", "    for name, folder in []:", RT),
    ("r02_missing_copy_accepted", "test_refusal_pairs.py",
     "            problems.append(\"no copy of %s at %s\" % (name, folder))\n", "", RT),
    ("r03_not_a_switch_proved", "test_refusal_pairs.py",
     "        out.update({s: e[0] for s, e in sw.items() if e[0] in (BS.TEST, BS.STOCK)})",
     "        out.update({s: e[0] for s, e in sw.items()})", RT),
    ("r04_bad_table_ignored", "test_refusal_pairs.py",
     "            problems.append(\"%s: %s\" % (p, e))\n", "", RT),
    ("r05_no_build_file_accepted", "test_refusal_pairs.py",
     "    if not shared:\n        problems.append(", "    if False:\n        problems.append(", RT),
    ("r06_project_table_dropped", "test_refusal_pairs.py",
     "    out = {s: e[0] for s, e in BS.SWITCHES.items() if e[0] in (BS.TEST, BS.STOCK)}",
     "    out = {}", RT),
]
TABLE_MUTANTS = [
    # the project's table as before the change: the stock switches are back in it
    ("t01_stock_switches_in_project_table", BS),
]


def copy(dst):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", "build", "build-*", "managed_components",
                                                            "runs", "scratch"))


def run(name, test, dst):
    env = dict(os.environ)
    # the copy is not beside the worktree root: point the wican lookup at the pinned checkout
    env["WICANDIR"] = WICANDIR
    r = subprocess.run([sys.executable, "-B", os.path.join(dst, BC, test)], capture_output=True, text=True,
                       env=env, cwd=ROOT)
    out = r.stdout + r.stderr
    bad = [ln for ln in out.splitlines() if ln.lstrip().startswith(("FAIL", "Traceback"))]
    if name == "control":
        verdict = "FAILS" if r.returncode else "passes"
    else:
        verdict = "killed" if r.returncode else "SURVIVED"
    print("== %s (%s): exit %d: %s" % (name, test, r.returncode, verdict))
    for ln in bad[:3]:
        print("   " + ln.strip()[:130])
    return r.returncode != 0


survivors = []
base = os.path.join(ROOT, "scratch", "build-checks", "source_rows_mutants")
WICANDIR = os.environ.get("WICANDIR") or subprocess.run(
    [sys.executable, os.path.join(SRC, "test", "gen_inhibit_shim", "wican_ref.py")],
    capture_output=True, text=True, check=True).stdout.strip()
# controls: each test passes on an unmutated copy, or its kills mean nothing
dst = os.path.join(base, "control")
copy(dst)
for test in (SR, SW, BS, RT):
    if run("control", test, dst):
        sys.exit("control: %s fails on an unmutated copy" % test)
# m00: build_output.py before the change
dst = os.path.join(base, "m00")
copy(dst)
old = subprocess.run(["git", "-C", ROOT, "show", "%s:%s/%s/build_output.py" % (BEFORE, PROJECT_REL, BC.replace(os.sep, "/"))],
                     capture_output=True, text=True, check=True).stdout
open(os.path.join(dst, BC, "build_output.py"), "w", newline="").write(old)
if not run("m00_before_the_change", SR, dst):
    survivors.append("m00")
for name, f, a, b, test in MUTANTS:
    dst = os.path.join(base, name)
    copy(dst)
    p = os.path.join(dst, BC, f)
    s = open(p, newline="").read()
    if s.count(a) != 1:
        sys.exit("%s: edit matches %d times" % (name, s.count(a)))
    open(p, "w", newline="").write(s.replace(a, b))
    if not run(name, test, dst):
        survivors.append(name)
for name, test in TABLE_MUTANTS:
    dst = os.path.join(base, name)
    copy(dst)
    old = subprocess.run(["git", "-C", ROOT, "show", "%s:%s/firmware/vtrux_checks.json" % (BEFORE, PROJECT_REL)],
                         capture_output=True, text=True, check=True).stdout
    open(os.path.join(dst, "firmware", "vtrux_checks.json"), "w", newline="").write(old)
    if not run(name, test, dst):
        survivors.append(name)
shutil.rmtree(base, ignore_errors=True)
print("survivors: %s" % (", ".join(survivors) or "none"))
sys.exit(1 if survivors else 0)
