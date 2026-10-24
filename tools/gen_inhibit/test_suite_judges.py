#!/usr/bin/env python3
"""Tests for suite_judges.py, on the accepted e3d9437 baseline's own run files.

    GI_BASELINE_DIR=<reverse-it>/projects/vtrux/notes/artifacts/gen-inhibit/runs/baseline_e3d9437 \
        py -3.14 test_suite_judges.py

1. Every judge, run on the e3d9437 run files, gives the verdict and every figure of
   suite_index_e3d9437.json, which was written by hand from baseline_run_list.md.
   The index and the judges agree in both directions: no judge without an entry,
   no entry without a judge unless it is NOT-RUN.
2. Each judge can fail: one edit to a copy of its files (a verdict word, a count)
   must turn its verdict to the one named, and no other check's verdict may move.
3. With no files, every judge says ERROR, never PASS; so does a judge that raises
   or returns nothing.

Copies go to the worktree's scratch/. Gen-inhibit tester, 2026-10-18.
"""
import json
import os
import re
import shutil
import sys

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
import suite_judges as J   # noqa: E402

FAILS = []


def check(name, fn, info=None):
    try:
        ok = fn()
    except Exception as e:      # a check that raises is a failed check, said so
        ok, info = False, "%s: %s" % (type(e).__name__, e)
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %s" % (info,)))
    if not ok:
        FAILS.append(name)


# (check, folder, file regex, line regex, replacement, verdict wanted). The regex must
# match exactly once in that file unless it is marked "all".
EDITS = [
    ("B1", "bench", r"B1_slcan_refusal_stdout\.txt", r"'fail': 0", "'fail': 1", "FAIL"),
    ("B1", "bench", r"B1_slcan_refusal_stdout\.txt", [(r"'commands': 20", "'commands': 0"), (r"^.*-> \w+ +\| counted .*\n", "")],
     None, "FAIL all"),
    ("B2", "bench", r"B2_e4_presence_inhibit_e3d9437_x2app\.txt", r"device 0x051 670 \(device says 670\)",
     "device 0x051 670 (device says 671)", "FAIL"),
    ("B17a", "bench", "+B17_spec11_page_aaaaaaa_man.txt", None, "AS SPECIFIED\n", "ERROR"),
    ("B2", "bench", r"B2_x2_presence_count\.txt", r"(inhibit_e3d9437_x2app frames 71433  x2 7F4=)0", r"\g<1>3", "FAIL"),
    ("B2", "bench", r"B2_e4_presence_respond_e3d9437_x2app\.txt", r"2994 transmitted, 2994 seen", "2994 transmitted, 2993 seen", "FAIL"),
    ("B2", "bench", r"B2_e4_presence_longarm_e3d9437_x2held\.txt", r"(provenance \(live\).*?)late 0, double 0", r"\g<1>late 1, double 0", "FAIL"),
    ("B3", "bench", r"B3_golive_stale_check\.txt", r"(T213139_arm2\.json +observe +dev0x051 1499 +)stale 0", r"\g<1>stale 2", "FAIL"),
    ("B3", "bench", r"B3_mode_entry_analysis\.txt", r'("run": "B3_e4_golive_respond_e3d9437_20261016T213601_arm3\.json".*?"provenance": )"as specified"', r'\g<1>"late"', "FAIL"),
    ("B24", "bench", r"B24_frame_content_e3d9437\.txt", r"(B2_e4_presence_inhibit_e3d9437_x2app\.json +)AS SPECIFIED", r"\g<1>FAULT SEEN  ", "FAIL"),
    ("B24", "bench", r"B24_frame_content_e3d9437\.txt", r"(B3_e4_golive_observe_e3d9437_20261016T213056_arm1\.json +)AS SPECIFIED    ", r"\g<1>NO DEVICE FRAMES", "FAIL"),
    ("B4", "bench", r"B4_skip_method_stdout\.txt", r"^(l2 +)PASS", r"\g<1>FAIL", "FAIL"),
    ("B5", "bench", r"B5_livetrain_p5000\.txt", r"^(arm 1 .*?\| A: )PASS", r"\g<1>FAIL", "FAIL"),
    ("B5", "bench", r"B5_livetrain_p2000_run2\.txt", r"^(arm 0 .*?\| B: )PASS", r"\g<1>FAIL", "FAIL"),
    ("B6", "bench", r"B6_threshold_n10_lead5\.txt", r"^(arm 1 \(replay [\d.]+/s\): )PASS", r"\g<1>FAIL", "FAIL"),
    ("B6", "bench", r"B6_threshold_n9_lead1\.5\.txt", r"^verdict PASS", "verdict FAIL", "FAIL"),
    ("B7", "bench", r"B7_busoff_rearm\.txt", r"^arm 1: AS SPECIFIED", "arm 1: NOT AS SPECIFIED", "FAIL"),
    ("B8", "bench", r"B8_busoff\.txt", r"^arm 1 train=True: PASS", "arm 1 train=True: FAIL", "FAIL"),
    ("B8", "bench", r"B8_busoff\.txt", r"^arm 3 train=False: INCONCLUSIVE", "arm 3 train=False: FAIL", "FAIL"),
    ("B9", "bench", r"B9_wrong_bitrate_calibrate\.txt", r"'aborts': \['error-frame rate exceeded'\]", "'aborts': []", "FAIL"),
    ("B10", "bench", r"B10_trip6_rearm_check\.txt", r"^(A3 recent .*?)  as specified", r"\g<1>  NOT as specified", "FAIL"),
    ("B10", "bench", r"B10_trip6_rearm_check\.txt", r"^(A4 expired .*\n +history )as specified", r"\g<1>NOT", "FAIL"),
    ("B11", "bench", r"B11_quiesce_analysis\.txt", r'(_arm4\.json".*?"agree": )true', r"\g<1>false", "FAIL"),
    ("B11", "bench", r"B11_quiesce_analysis\.txt", r'(_arm3\.json".*?"provenance": )"as specified"', r'\g<1>"double"', "FAIL"),
    ("B11raw", "bench", r"B11raw_rawpost_judge\.txt", r'("local_port": 61947, .*?"verdict": )"PASS"', r'\g<1>"strict FAIL"', "FAIL"),
    ("B11raw", "bench", r"B11raw_rawpost_judge\.txt", r'"verdict": "strict FAIL, PASS on ACK"', '"verdict": "strict FAIL"', "FAIL"),
    ("B11raw", "bench", r"B11raw_rawpost_judge\.txt", r'("local_port": 61873, .*?"other_requests_in_progress": )\[\]', r'\g<1>[61874]', "FAIL"),
    ("B12", "bench", r"B12_2_live_crash\.txt", r"reset \['PANIC'\]", "reset ['SW']", "FAIL"),
    ("B12", "bench", r"B12_7_off_reboot\.txt", r"(after POST, )errors 0", r"\g<1>errors 2", "FAIL"),
    ("B13", "bench", r"B13_passive_analysis\.txt", r'(_arm7\.json", "mode": "passive", "diag_modes": \[4\], "provenance": )"as specified"', r'\g<1>"late"', "FAIL"),
    ("B13", "bench", r"B13_passive_analysis\.txt", r'(_arm7\.json".*?"device_0x051": )0', r"\g<1>3", "FAIL"),
    ("B13", "bench", r"B13_respond_booking_check\.txt", r"^\[PASS        \](   balance: unbooked 7)", r"[FAIL        ]\g<1>", "FAIL"),
    ("B14", "bench", r"B14_observe_ack_probe\.txt", r"mode at end 0", "mode at end 3", "FAIL"),
    ("B14", "bench", r"B14_observe_ack_probe\.txt", r"^(  mode 2: ack errors False -> )as specified", r"\g<1>NOT as specified", "FAIL"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^ "verdict": "PASS"', ' "verdict": "NOT AS SPECIFIED: arm 1 latch"', "FAIL"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^ "verdict": "PASS"', ' "verdict": "VOID: 2 main arms of 3"', "VOID"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^ "verdict": "PASS"', ' "verdict": "FLAKY: the main arms disagree"', "INCONCLUSIVE"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^ "verdict": "PASS"', ' "verdict": "INCONCLUSIVE: arm 2 latch"', "INCONCLUSIVE"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^ "termination": "fixed[^"]*"', ' "termination": ""', "ERROR"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^  "arms": 3,', '  "arms": 4,', "ERROR"),
    ("NONODE", "bench", r"nonode_bench_\d{8}T\d{6}\.json", r'^(    "latched_tx": )"PASS: ', r'\g<1>"NOT AS SPECIFIED: ', "ERROR all"),
    ("B15host", "bench", r"B15_diag_decode_check_host_e3d9437\.txt", r"all emitted diag frames decode against the DBC", "2 diag frames do not decode", "FAIL"),
    ("B15", "bench", r"B15_wican_segment_probe\.txt", r"^restored: \{'mode': 0", "restored: {'mode': 3", "FAIL"),
    ("B15", "bench", r"B15_wican_segment_probe\.txt", r"(--- pcan .*?ids )\{'0x7F9': 20930\}", r"\g<1>{'0x123': 20930}", "FAIL"),
    ("B16", "bench", r"B16_page_only_baseline\.txt", r"outages 0 -> no outage", "outages 1 -> 1 outage", "FAIL"),
    ("B17a", "bench", r"B17_spec11_page_e3d9437_man\.txt", r"^  page  25f5", "  page  35f5", "FAIL"),
    ("B17b", "bench", r"B17_spec11_page_e3d9437_man2\.txt", r"^AS SPECIFIED", "NOT AS SPECIFIED", "FAIL"),
    ("B18", "bench", r"B18_diag_build_page_check\.txt", r"(expected from e3d9437 +)0x2FE85674", r"\g<1>0x2FE85675", "FAIL"),
    ("B19present", "bench", r"B19_present_rxq\.txt", r"^(GI_INSTRUMENT_RXQ +)PASS", r"\g<1>FAIL", "FAIL"),
    ("B19absent", "bench", r"B19_absent_prod_rxq_gap30\.txt", r"^(GI_INSTRUMENT_RXQ +)PASS", r"\g<1>FAIL", "FAIL"),
    ("B19absent", "bench", r"B19_absent_prod\.txt", r"^(OTA_HEALTH_FAULT_INJECT +)PASS", r"\g<1>FAIL", "FAIL"),
    ("B19page", "bench", r"B19_spec11_page_prod\.txt", r"^AS SPECIFIED", "NOT AS SPECIFIED", "FAIL"),
    ("B20", "bench", r"B20_interlock_bench_run\.txt", r"^(gate-open +)AS SPECIFIED", r"\g<1>NOT AS SPECIFIED", "FAIL"),
    ("B21", "bench", r"B21_bench_flash_guard\.txt", r"spec 5\.1 item 3 holds on the device", "spec 5.1 item 3 does not hold", "FAIL"),
    ("B21", "bench", r"B21_wire_image_e3d9437\.txt", r"^(PASSIVE  rep 2 .*?device_end )fin", r"\g<1>rst", "FAIL"),
    ("B21", "bench", r"B21_wire_multipart_e3d9437\.txt", r"^(OBSERVE  rep 3 .*? wire )403", r"\g<1>200", "FAIL"),
    ("B22", "bench", r"B22_case3_e3d9437_man_b\.txt", r"tx_fail=0", "tx_fail=1", "FAIL"),
    ("B22", "bench", r"B22_case3_e3d9437_man_a\.txt", r"\), 0 error frames", "), 4 error frames", "FAIL"),
    ("B22", "bench", r"B22_case3_e3d9437_man_b\.txt", r"8983 transmitted, 8983 seen", "8983 transmitted, 8980 seen", "FAIL"),
    ("B23", "bench", r"B23_txabort_bench\.txt", r"RETRY LOOP: 78 of 78", "RETRY LOOP: 77 of 78", "FAIL"),
    ("B23", "bench", r"B23_txabort_bench\.txt", r"reached the wire anyway on 0 of 78", "reached the wire anyway on 1 of 78", "FAIL"),
    ("B23", "bench", r"B23_txabort_bench\.txt", r"monotonic: True", "monotonic: False", "FAIL"),
    ("B25", "bench", r"B25_keystate_rekey-long_e3d9437\.txt", r"\.json: AS SPECIFIED", ".json: NOT AS SPECIFIED", "FAIL"),
    ("B25", "bench", r"B25_keystate_rekey-long_e3d9437\.txt", r"^  ok    rule 2", "  FAIL  rule 2", "FAIL"),
    ("B26", "bench", r"B26_rxq_idle_b\.json", r'"samples": 82464', '"samples": 0', "ERROR"),
    ("H", "host", r"host_run\.log", r"^=== exit 0 \(sched\)$", "=== exit 1 (sched)", "FAIL"),
    ("H", "host", r"host_run\.log", r"^=== exit 0 \(shim_asan\)$", "", "FAIL"),
    ("H", "host", r"host_run\.log", r"^(\d+) scenarios hold, 0 violate", r"\g<1> scenarios hold, 1 violate", "FAIL"),
    ("H", "host", r"host_run\.log", r"^80 passed, 0 failed", "79 passed, 1 failed", "FAIL all"),
    ("H", "host", r"host_run\.log", r"0 with violations$", "2 with violations", "FAIL"),
    ("C1C7", "root", r"tester_rows_e3d9437\.txt", r"(== prod [\s\S]*?\n)  ok   (bus state changed only)", r"\g<1>  FAIL \g<2>", "FAIL"),
    ("C1C7", "root", r"tester_rows_e3d9437\.txt", r"^== prod \(checks", "== man (checks", "FAIL"),
    ("C3", "root", r"test_refusal_pairs_e3d9437\.txt", r"11 of 11 negatives", "10 of 11 negatives", "FAIL"),
    ("C5", "root", r"stale_name_check_e3d9437\.txt", r"^PASS$", "FAIL", "FAIL"),
    ("C6", "root", r"name_once_check_e3d9437\.txt", r"^  ok   (overlong)", r"  FAIL \g<1>", "FAIL"),
    ("C2scan", "root", r"scan_scope_check_e3d9437\.txt", r"^PASS ", "FAIL ", "FAIL"),
]

KEEP = (".txt", ".json", ".log")


def copy_tree(base, dest):
    """The baseline's text files (no captures, no witness logs), into dest/{bench,host,root}."""
    for sub, src in (("bench", os.path.join(base, "bench")), ("host", os.path.join(base, "host")), ("root", base)):
        os.makedirs(os.path.join(dest, sub))
        for n in os.listdir(src):
            p = os.path.join(src, n)
            if os.path.isfile(p) and n.endswith(KEEP):
                shutil.copy(p, os.path.join(dest, sub, n))


def judge_all(root):
    out = {}
    for cid, (part, _fn) in J.JUDGES.items():
        folder = {"B15host": "bench"}.get(cid) or {"B": "bench", "H": "host", "C": "root"}[part]   # B15host ran from NICKY-XPS; its file is in bench/
        res = J.judge_one(cid, os.path.join(root, folder))
        out[cid] = res
    return out


def figures(res):
    return {k: v["value"] for r in res for k, v in r["figures"].items()}


def main():
    base = os.environ.get("GI_BASELINE_DIR")
    if not base or not os.path.isdir(os.path.join(base, "bench")):
        print("FAIL GI_BASELINE_DIR is not set to the baseline_e3d9437 run folder: nothing can be checked")
        return 1
    with open(os.path.join(base, "suite_index_e3d9437.json"), encoding="utf-8") as f:
        index = {c["id"]: c for c in json.load(f)["checks"]}
    scratch = scratch_dir("suite_judges")
    shutil.rmtree(scratch, ignore_errors=True)
    real = os.path.join(scratch, "real")
    copy_tree(base, real)

    # 1. The judges give the hand-written index.
    got = judge_all(real)
    check("every judge has an index entry", lambda: not set(J.JUDGES) - set(index), sorted(set(J.JUDGES) - set(index)))
    check("every index entry has a judge, or is NOT-RUN",
          lambda: all(c in J.JUDGES or index[c]["verdict"] == "NOT-RUN" for c in index),
          [c for c in index if c not in J.JUDGES and index[c]["verdict"] != "NOT-RUN"])
    for cid, res in got.items():
        if cid not in index:
            continue
        want = index[cid]
        check("%s: the verdict is the index's (%s)" % (cid, want["verdict"]),
              lambda: [r["verdict"] for r in res] == [want["verdict"]], [(r["verdict"], r["detail"]) for r in res])
        f = figures(res)
        check("%s: every figure is the index's, and no other" % cid, lambda: f == want["figures"],
              {k: (f.get(k), want["figures"].get(k)) for k in set(f) | set(want["figures"])
               if f.get(k) != want["figures"].get(k)})

    # 2. Each judge can fail.
    for n, (cid, sub, frx, old, new, want) in enumerate(EDITS, 1):
        allm = want.endswith(" all")
        want = want.split()[0]
        d = os.path.join(scratch, "e%d" % n)
        shutil.copytree(real, d)
        folder = os.path.join(d, sub)
        if frx.startswith("+"):     # a new file beside the check's own
            p = os.path.join(folder, frx[1:])
            if os.path.exists(p):
                check("edit %d (%s): %s is new" % (n, cid, frx[1:]), lambda: False)
                continue
            open(p, "w", encoding="utf-8").write(new)
            old = "a new file %s" % frx[1:]
        else:
            names = [x for x in os.listdir(folder) if re.fullmatch(frx, x)]
            if len(names) != 1:
                check("edit %d (%s): its file %s" % (n, cid, frx), lambda: False, names)
                continue
            p = os.path.join(folder, names[0])
            s = open(p, encoding="utf-8").read()      # universal newlines: some run files are CRLF
            pairs = old if isinstance(old, list) else [(old, new)]
            bad = None
            for pat, rep in pairs:
                s, k = re.subn(pat, rep, s, flags=re.M)
                if k == 0 or (k != 1 and not allm):
                    bad = (pat, k)
                    break
            if bad:
                check("edit %d (%s): %r matches once" % (n, cid, bad[0]), lambda: False, bad[1])
                continue
            open(p, "w", encoding="utf-8").write(s)
            old = pairs[0][0]
            new = pairs[0][1]
        after = judge_all(d)
        v = [r["verdict"] for r in after[cid]]
        moved = [c for c in after if c != cid and [r["verdict"] for r in after[c]] != [r["verdict"] for r in got[c]]]
        check("edit %d: %s reads %s after %r -> %r" % (n, cid, want, old[:40], new[:30]),
              lambda: v == [want] and not moved, (v, after[cid][0]["detail"], moved))
        shutil.rmtree(d, ignore_errors=True)

    # 3. No files, a judge that raises, a judge that returns nothing.
    empty = os.path.join(scratch, "empty")
    os.makedirs(empty)
    res = {cid: J.judge_one(cid, empty) for cid in J.JUDGES}
    check("with no files every judge says ERROR", lambda: all([r["verdict"] for r in v] == ["ERROR"] for v in res.values()),
          [c for c, v in res.items() if [r["verdict"] for r in v] != ["ERROR"]])
    saved = dict(J.JUDGES)
    try:
        J.JUDGES["B1"] = ("B", lambda F: 1 / 0)
        check("a judge that raises says ERROR", lambda: J.judge_one("B1", os.path.join(real, "bench"))[0]["verdict"] == "ERROR")
        J.JUDGES["B1"] = ("B", lambda F: [])
        check("a judge that returns nothing says ERROR", lambda: J.judge_one("B1", os.path.join(real, "bench"))[0]["verdict"] == "ERROR")
    finally:
        J.JUDGES.clear()
        J.JUDGES.update(saved)
    check("result() refuses a verdict outside the list", lambda: _refuses())

    shutil.rmtree(scratch, ignore_errors=True)
    print("\n%s: %d failed" % ("FAIL" if FAILS else "PASS", len(FAILS)))
    return 1 if FAILS else 0


def _refuses():
    try:
        J.result("X", "MOSTLY")
    except AssertionError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
