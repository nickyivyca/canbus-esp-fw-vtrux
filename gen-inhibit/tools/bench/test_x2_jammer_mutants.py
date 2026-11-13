#!/usr/bin/env python3
"""Mutants of x2_jammer.py's sub_id, per_pulse's poll stamp (added 2026-11-05) and sub_id's
reset since the open (added 2026-11-11);
test_x2_jammer.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_x2_jammer_mutants.py

Written 2026-10-20 for the empty line sub_id sends before ID (the 2026-10-19 chain run:
bench_identify's "V\r" "N\r" joined the ID line). Each mutant is a copy of the tool and
the test in the worktree's scratch/, one edit applied, with x2_jammer/platformio.ini beside
them where the test looks for it (one folder up). The control, the unmutated copy, must
pass with no crash first (2026-10-24: without the ini every copy crashed after its first
failing check, so the later checks never ran). Gen-inhibit tester.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_jammer.py"
TEST = "test_x2_jammer.py"
INI = os.path.join(os.path.dirname(HERE), "x2_jammer", "platformio.ini")   # the test reads it
MUTANTS = [
    ("no empty line before ID", '    con.send("")\n', ""),
    ("the empty line after ID", '    con.send("")\n    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n',
     '    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n    con.send("")\n'),
    # per_pulse's poll stamp (2026-11-05, gen-inhibit reviewer)
    ("per_pulse: the old return-time stamp",
     '            good.append((s[0], s[1], s[2] if len(s) > 2 and s[2] is not None else s[0]))\n',
     '            good.append((s[0], s[1], s[0]))\n'),
    ("per_pulse: the before reading bounded by its return",
     '        if b[2] <= lo or a[0] >= hi:', '        if b[0] <= lo or a[0] >= hi:'),
    ("per_pulse: the after reading bounded by its request",
     '        if b[2] <= lo or a[0] >= hi:', '        if b[2] <= lo or a[2] >= hi:'),
    # sub_id's reset since the open (2026-11-11, the X2 no-reset open)
    ("id: a banner counts after a reset",
     '    rec["banner_ok"] = got is not None and rec["reset_since_open"] is False\n',
     '    rec["banner_ok"] = got is not None\n'),
    ("id: a lost or reopened port is no reset",
     '    if any(e.startswith("lost") or e == "reopened" for _t, e in events):\n',
     '    if False:\n'),
    ("id: the uptime is not compared with the time since the open",
     '    return up[1]["uptime_us"] / 1e6 < up[0] - opened[0]\n', '    return False\n'),
    ("id: no uptime line is taken as no reset",
     '    if up is None or not opened or not isinstance(up[1].get("uptime_us"), int):\n        return None\n',
     '    if up is None or not opened or not isinstance(up[1].get("uptime_us"), int):\n        return False\n'),
    ("id: the uptime read from the mcp2515 line, which has none",
     '    up = con.wait(lambda p: p["kind"] == "INFO" and "uptime_us" in p, 0.5, t) if got else None\n',
     '    up = got\n'),
    # the ESP-IDF port's bench pass (2026-11-13)
    ("identity: the elf not compared",
     '    if not expect_elf or elf != expect_elf:\n', '    if not expect_elf:\n'),
    ("identity: NOT_FOR_VEHICLE not required in the name",
     '    if not expect_name or "NOT_FOR_VEHICLE" not in expect_name or name != BANNER_NAME % expect_name:\n',
     '    if not expect_name or name != BANNER_NAME % expect_name:\n'),
    ("identity: twai not checked", '    if twai != "never_started":\n', '    if False:\n'),
    ("identity: mcp2515 config mode not checked", '    if cfg != "yes":\n', '    if False:\n'),
    ("id: the identity PASS despite a reset since the open",
     '        if j["verdict"] == "PASS" and not rec["banner_ok"]:\n', '        if False:\n'),
    ("after_done: no margin after DONE", '    lo = t_done + margin_s\n', '    lo = t_done\n'),
    ("after_done: the window's last instant left out",
     '    return {"n": sum(1 for t in err_ts if lo < t <= obs_end),',
     '    return {"n": sum(1 for t in err_ts if lo < t < obs_end),'),
    ("after_done: any window long enough",
     '"enough": obs_end - lo >= post_s}', '"enough": True}'),
    ("judge_pulses: frames after DONE ignored",
     '    elif post["n"]:\n        out["verdict"], out["why"] = "FAIL", "error frames after DONE"\n',
     '    elif False:\n        out["verdict"], out["why"] = "FAIL", "error frames after DONE"\n'),
    ("judge_pulses: a noisy bus before the send ignored",
     '    elif pre:\n        out["verdict"], out["why"] = "INCONCLUSIVE", "bus not quiet before the send"\n',
     '    elif False:\n        out["verdict"], out["why"] = "INCONCLUSIVE", "bus not quiet before the send"\n'),
    ("judge_pulses: groups of one not required",
     '    elif not pp["exactly_one_each"]:\n', '    elif False:\n'),
    ("judge_pulses: a short window after DONE accepted",
     '    elif not post["enough"]:\n', '    elif False:\n'),
    ("judge_pulses: no ACK accepted",
     '    if not ack or t_done is None:\n', '    if t_done is None:\n'),
    ("judge_done: the fields not compared",
     '           if done.get(k) != v]\n', '           if False]\n'),
    ("judge_done: no DONE line taken as a match",
     '        return None, "no DONE line to judge %s"', '        return True, "no DONE line to judge %s"'),
    ("fold_timeout: frames after DONE ignored",
     '    if post.get("n"):\n        return "FAIL", "error frames after DONE"\n',
     '    if False:\n        return "FAIL", "error frames after DONE"\n'),
    ("fold_timeout: a mismatched DONE ignored", '    if ok is False:\n', '    if False:\n'),
    ("fold_timeout: no DONE line keeps the PASS",
     '    if ok is None and verdict == "PASS":\n', '    if False:\n'),
    ("sub_timeout: the DONE checks not folded in",
     '    rec["verdict"], rec["why"] = fold_timeout(rec["verdict"], rec["why"], rec["after_done"],\n',
     '    rec["verdict"], rec["why"] = fold_timeout(rec["verdict"], rec["why"], {"n": 0},\n'),
    ("sub_timeout: the expectations not passed",
     '    rec["done_check"] = judge_done(done[1] if done else None, a.expect_stop_by, a.expect_end)\n',
     '    rec["done_check"] = judge_done(done[1] if done else None)\n'),
    ("isolation: a missed 0x7A0 on the Kvaser accepted",
     '    if sent < 1 or kv_seen != sent or kv_groups != n_pulses:\n',
     '    if sent < 1 or kv_groups != n_pulses:\n'),
    ("isolation: canchg frames ignored", '    if in_frames or in_err:\n        return "FAIL", "canchg saw',
     '    if in_err:\n        return "FAIL", "canchg saw'),
    ("isolation: an ACKed probe accepted",
     '    if probe_verdict == "ACKED":\n', '    if False:\n'),
    ("isolation: an unclear probe accepted",
     '    if probe_verdict != "NO_ACK":\n', '    if False:\n'),
    ("args: --no-wican with --wican-mode", '    if a.no_wican and a.wican_mode:\n', '    if False:\n'),
    ("args: can2-check with --no-wican", '    if a.no_wican and a.sub in NEEDS_WICAN:\n', '    if False:\n'),
    ("args: can2-isolation without --no-wican",
     '    if a.sub in NO_WICAN_ONLY and not a.no_wican:\n', '    if False:\n'),
    ("args: id with one expectation",
     '    if a.sub == "id" and bool(a.expect_name) != bool(a.expect_elf):\n', '    if False:\n'),
    ("args: expectations on the wrong subcommand",
     '    if (a.expect_stop_by or a.expect_end) and a.sub != "timeout":\n', '    if False:\n'),
    ("pulses: the WiCAN set and read under --no-wican",
     '    if not a.no_wican:\n        wican_mode(a.wican_mode)\n', '    if True:\n        wican_mode(a.wican_mode)\n'),
    ("pulses: no verdict in the record",
     '                "judge": j, "verdict": j["verdict"], "why": j["why"],\n', '                "judge": j,\n'),
    ("pulses: watched only a second past DONE",
     '    time.sleep(a.post_s + 0.3 + 1.0 + 0.2)\n', '    time.sleep(a.post_s + 1.0)\n'),
]
# Not a mutant: a space instead of the empty line. The firmware's sscanf("%11s") finds no
# word in " " either and ignores the line, so it behaves the same (equivalent).


def run(base, label, text):
    """A copy of the tool (text) and the test in base/label; the test's FAIL lines, crash, exit."""
    d = os.path.join(base, label)
    gi_paths.remove_tree(d)
    os.makedirs(d)
    open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(text)
    shutil.copy(os.path.join(HERE, TEST), d)
    shutil.copy(os.path.join(HERE, "gi_paths.py"), d)   # the moved tool imports it
    shutil.copy(os.path.join(HERE, "witness_clock.py"), d)   # and this, since 2026-11-11 (A2 (b))
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
    fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
    crash = r.stderr.strip().splitlines()[-1][:120] if "Traceback" in r.stderr else ""
    return fails, crash, r.returncode


def main():
    base = gi_paths.scratch("mutants-x2_jammer")
    os.makedirs(os.path.join(base, "x2_jammer"))
    shutil.copy(INI, os.path.join(base, "x2_jammer"))
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    fails, crash, rc = run(base, "control", src)
    control_ok = rc == 0 and not fails and not crash
    print("%s control: the unmutated copy  (exit %d, %d FAIL%s)" % (
        "PASS    " if control_ok else "FAILED  ", rc, len(fails), ", CRASHED: " + crash if crash else ""))
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        fails, crash, rc = run(base, "m%d" % n, src.replace(old, new))
        killed += bool(fails)
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if fails else "SURVIVED", n, name, rc, len(fails),
            ", CRASHED: " + crash if crash else "",
            ("; " + fails[0][:100]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
