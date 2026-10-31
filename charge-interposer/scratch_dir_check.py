#!/usr/bin/env python3
"""Every harness file that takes its throwaway folder from scratch_dir
removes it, read-only git objects and all, and would not without the
read-only handler (charge-interposer tester, 2026-10-31; the reviewer's
acceptance for the scratch-removal topic).

Each entry point below is run twice, each time in a fresh interpreter that
runs the file as __main__ (runpy) after patching scratch_dir in place:

  planted    every folder scratch_dir.make() gives out gets a real git repo
             with read-only .git/objects (scratch_dir_test.plant). The run
             must exit as it does unpatched (0), make at least one folder,
             and leave none of them, with no LEFTOVER line.
  no-chmod   the same, with scratch_dir's read-only handler taken out
             (_writable does nothing). At least one folder must be left
             and reported as a LEFTOVER: the handler is what removes them.
             The check then removes them with the handler put back.

Folders made by the entry point's own subprocesses (a can-fail's mutant
runs) are not patched; their removal is the same call.

Each run first checks that the fixture bites here: a plain
shutil.rmtree(ignore_errors=True) must leave a planted repo. On a
filesystem that removes read-only files anyway (WSL on /mnt/c: found
2026-10-31) it does not, and the no-chmod half is reported as not
applicable there, never as caught; it is checked on NTFS (NICKY-XPS) and
on madhouse's own disk.

    py -3.14 -B charge-interposer/scratch_dir_check.py [NAME ...]   # default: all
    py -3.14 -B charge-interposer/scratch_dir_check.py --list

The g++ ones (monitor_6h_diff, switch_discard_witness) run where g++ is:
madhouse, or WSL on NICKY-XPS (python3 -B ... NAME). build_check_dirty_flag
(a real git clone of HEAD, whose objects git marks read-only, and three
PlatformIO builds) runs only when named, and not while a wall-clock test
runs (AGENTS.md, "Builds during wall-clock tests").
"""

import json
import os
import runpy
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# name -> (file under charge-interposer/, args)
TARGETS = {
    "scratch_dir_test": ("scratch_dir_test.py", []),
    "bench_usb_host": ("bench_drivers/bench_usb_host.py", ["--self-test"]),
    "bench_vport": ("bench_drivers/bench_vport.py", ["--self-test"]),
    "script_can_fail_test": ("bench_drivers/script_can_fail_test.py", []),
    "bench_suite_test": ("bench_suite_test.py", []),
    "bench_wican_test": ("bench_wican_test.py", []),
    "bench_wican_test_canfail": ("bench_wican_test.py", ["--can-fail"]),
    "board_reset_test": ("board_reset_test.py", []),
    "canre_pin_test": ("canre_pin_test.py", []),
    "canre_pin_test_canfail": ("canre_pin_test.py", ["--can-fail"]),
    "can_tools_pin_test": ("can_tools_pin_test.py", []),
    "can_tools_pin_test_canfail": ("can_tools_pin_test.py", ["--can-fail"]),
    "capture_pins_test": ("capture_pins_test.py", []),
    "diag_cadence_test": ("diag_cadence_test.py", []),
    "regress_offline_test": ("regress_offline_test.py", []),
    "test_scorer_wiring": ("test_scorer_wiring.py", []),
    "paths_test": ("paths_test.py", []),
    "bus_gs_usb_test": ("bus_gs_usb_test.py", []),
    "build_lookup_offline_test": ("build_lookup_offline_test.py", []),
    "rebuild_capture_traces": ("rebuild_capture_traces.py", []),
    "build_check_links_test": ("firmware/test/build_check_links_test.py", []),
    "build_check_manifest_test": ("firmware/test/build_check_manifest_test.py", []),
    "build_check_schema_test": ("firmware/test/build_check_schema_test.py", []),
    "build_check_in_clone_test": ("firmware/test/build_check_in_clone_test.py", []),
    "digest_recheck_check": ("firmware/test/digest_recheck_check.py", []),
    "build_check_dirty_flag": ("firmware/test/build_check.py", ["--dirty-flag"]),
    "cutover_verify_test": ("harness_checks/cutover_verify_test.py", []),
    "cutover_verify_guard": ("harness_checks/cutover_verify_test.py", ["--guard-test"]),
    "monitor_6h_diff": ("harness_checks/monitor_6h_diff.py", []),
    "switch_discard_witness": ("harness_checks/switch_discard_witness.py", []),
}
GPP = ("monitor_6h_diff", "switch_discard_witness")
NAMED_ONLY = GPP + ("build_check_dirty_flag",)
# Entry points that exit non-zero on charge-interposer/main b423488 too,
# run there unchanged on 2026-10-31: their exit is reported, not judged
# here; whether they remove their folders still is.
KNOWN = {
    "digest_recheck_check": "part 2 (a real build edited mid-flight) "
    "FAILs 'the build FAILED (exit 0)' and 'the digest mismatch' on main too",
    "cutover_verify_test": "the full mode finds no machine.py in SeaDrive's "
    "tools/interposer/ (now README.md and firmware/ only) on main too; the "
    "suite runs only --guard-test",
    "build_check_dirty_flag": "step 3 (src/ edited in the clone) records no "
    "manifest row on main too",
}
MARK = "SCRATCH_DIR_CHECK "


def child(mode, rel, args):
    """Runs rel as __main__ with scratch_dir patched; prints one MARK line."""
    sys.path.insert(0, HERE)
    import scratch_dir
    import scratch_dir_test
    made = []
    real_make, real_writable = scratch_dir.make, scratch_dir._writable

    def make(tool, start=None):
        d = real_make(tool, start)
        scratch_dir_test.plant(d)
        made.append(d)
        return d
    ctl = real_make("scratch-dir-check-control")
    g = scratch_dir_test.plant(ctl)
    shutil.rmtree(g, ignore_errors=True)
    bites = os.path.exists(g)
    scratch_dir.remove(ctl)
    scratch_dir.make = make
    if mode == "no-chmod":
        scratch_dir._writable = lambda p: None
    path = os.path.join(HERE, *rel.split("/"))
    sys.argv = [path] + args
    sys.path.insert(0, os.path.dirname(path))
    rc = 0
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:     # noqa: BLE001
        print("raised %s: %s" % (type(e).__name__, e))
        rc = 99
    sys.stdout.flush()
    left = [d for d in made if os.path.exists(scratch_dir.long_path(d))]
    if mode == "no-chmod":
        scratch_dir._writable = real_writable
        for d in left:
            scratch_dir.remove(d, quiet=True)
    print("\n" + MARK + json.dumps({"rc": rc, "made": len(made), "left": left,
                                    "bites": bites}),
          flush=True)
    return 0


def run_one(name, py):
    rel, args = TARGETS[name]
    out = {}
    for mode in ("planted", "no-chmod"):
        t0 = time.time()
        r = subprocess.run(py + [os.path.abspath(__file__), "--child", mode, rel]
                           + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        rec = None
        for line in r.stdout.splitlines():
            if line.startswith(MARK):
                rec = json.loads(line[len(MARK):])
        out[mode] = (rec, "scratch_dir: LEFTOVER" in r.stdout, r, time.time() - t0)
    (p, p_left, pr, pt), (n, n_left, nr, nt) = out["planted"], out["no-chmod"]
    why = []
    if p is None:
        why.append("planted run gave no result (rc %d): %s" % (
            pr.returncode, (pr.stdout + pr.stderr).strip()[-300:]))
    else:
        if p["rc"] != 0 and name not in KNOWN:
            why.append("planted run exited %s" % p["rc"])
        if p["made"] < 1:
            why.append("no run folder made in process")
        if p["left"] or p_left:
            why.append("planted run left %d folder(s)%s" % (
                len(p["left"]), ", LEFTOVER printed" if p_left else ""))
    na = n is not None and not n.get("bites")
    if n is None:
        why.append("no-chmod run gave no result (rc %d)" % nr.returncode)
    elif na:
        pass
    elif not (n["left"] and n_left):
        why.append("no-chmod run left %d folder(s), LEFTOVER %s: the handler "
                   "is not what removes them" % (len(n["left"]),
                                                  "printed" if n_left else "not printed"))
    print("%s  %-28s planted: rc %s, %s made, %s left (%3.0fs); no-chmod: %s "
          "(%3.0fs)%s" % (
              "ok  " if not why else "FAIL", name,
              p and p["rc"], p and p["made"], p and len(p["left"]), pt,
              "NOT APPLICABLE here: the control shows this filesystem "
              "removes read-only files anyway" if na else
              "%s left, LEFTOVER %s" % (n and len(n["left"]),
                                         "yes" if n_left else "no"), nt,
              "".join("\n      - " + w for w in why)), flush=True)
    if p is not None and p["rc"] != 0 and name in KNOWN:
        print("      KNOWN: exits %s, as on main: %s" % (p["rc"], KNOWN[name]))
    if why:
        for mode in ("planted", "no-chmod"):
            r = out[mode][2]
            print("      [%s output tail] %s" % (mode, (r.stdout + r.stderr)
                                                 .strip()[-600:].replace("\n", "\n      ")))
    return not why, na


def main(argv):
    if argv[:1] == ["--child"]:
        return child(argv[1], argv[2], argv[3:])
    if argv == ["--list"]:
        for k, (rel, args) in TARGETS.items():
            print("%-28s %s %s" % (k, rel, " ".join(args)))
        return 0
    names = argv or [k for k in TARGETS if k not in NAMED_ONLY]
    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        print("unknown: %s (see --list)" % ", ".join(unknown))
        return 2
    py = [sys.executable, "-B"]
    res = [run_one(n, py) for n in names]
    bad = sum(not ok for ok, _na in res)
    na = sum(ok and na for ok, na in res)
    print("\nscratch_dir_check: %d of %d entry points remove their folders; "
          "%d caught without the handler, %d where this filesystem cannot "
          "tell (NOT APPLICABLE), %d FAILED"
          % (len(names) - bad, len(names), len(names) - bad - na, na, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
