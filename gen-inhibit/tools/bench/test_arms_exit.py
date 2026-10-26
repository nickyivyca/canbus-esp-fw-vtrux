#!/usr/bin/env python3
"""Test that the arms scripts exit non-zero when an arm does (reviewer, 2026-10-25).

The suite's arms cases (B2app, B2held, B3arms, B11arms, B11rawarms, B13arms) have no
judge: the runner rates them on the script's exit code alone, and the scripts exited 0
whatever their arms did. In the 6926fb0 bench run's re-run 2 every B3 respond and passive
arm and every B11raw arm exited 2 (a Wi-Fi outage) and both cases read PASS; only the desk
reads after them caught it. Now an arm that exits non-zero, or a B11raw capture, makes
the script exit 1 once the rest have run, and every arm still runs.

  baseline_b3_golive.sh off   5 arms: all 0 -> exit 0; arm 3 fails -> exit 1, 5 arms run
  baseline_b_noX2.sh B11      6 arms: the same, arm 6 failing (the last counts)
  baseline_b_noX2.sh B13      8 arms: arm 1 failing (the first counts)
  baseline_b_noX2.sh B11raw   6 arms: an arm failing -> exit 1; a capture failing -> exit 1
  baseline_b_noX2.sh B4       not an arm: B4's own exit is judged from its output, not counted
  baseline_b_hand.sh B2app    3 arms: all 0 -> exit 0; arm 2 fails -> exit 1

The fakes are test_baseline_capture_stop.py's (a fake `py` first on PATH, a fake dumpcap
through GI_DUMPCAP_PRE), with the e4 fake failing on the call FAKE_E4_FAIL_AT names and
the dumpcap fake exiting FAKE_DUMPCAP_RC when stopped.

    py -3.14 -B gen-inhibit/tools/bench/test_arms_exit.py
Mutants: test_arms_exit_mutants.py (BATCH_DIR names another copy of the scripts).
About a minute. Gen-inhibit tester, 2026-10-25.
"""
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths                              # noqa: E402
import test_baseline_capture_stop as T       # noqa: E402  its fakes, env and bash runner

E4_OLD = """  e4_device_calibration.py) sleep 1; echo '{}' > "$(arg --out "$@")" ;;"""
E4_NEW = """  e4_device_calibration.py) n=$(( $(cat "$FAKE_E4_COUNT" 2>/dev/null || echo 0) + 1 )); echo $n > "$FAKE_E4_COUNT"
    echo '{}' > "$(arg --out "$@")"; [ "$n" = "${FAKE_E4_FAIL_AT:-0}" ] && exit 2; exit 0 ;;
  skip_method_bench.py) exit "${FAKE_B4_RC:-0}" ;;"""
assert T.FAKE_PY.count(E4_OLD) == 1
FAKE_PY = T.FAKE_PY.replace(E4_OLD, E4_NEW)
DC_OLD = "    sys.exit(0)\n"
assert T.FAKE_DUMPCAP.count(DC_OLD) == 1
FAKE_DUMPCAP = T.FAKE_DUMPCAP.replace(DC_OLD, '    sys.exit(int(os.environ.get("FAKE_DUMPCAP_RC", "0")))\n')

fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[-300:]))
    sys.stdout.flush()


def case(tmp, label, script, args, arms, fail_at=0, **extra):
    """Runs the script under the fakes; returns (rc, e4 calls, output)."""
    k = len(glob.glob(os.path.join(tmp, "run*")))
    runs, out = os.path.join(tmp, "run%d" % k, "runs"), os.path.join(tmp, "run%d" % k, "out")
    os.makedirs(runs)
    count = os.path.join(tmp, "run%d" % k, "e4_count")
    env = T.env_for(tmp, runs, out, FAKE_E4_COUNT=T.fw(count), FAKE_E4_FAIL_AT=str(fail_at),
                    GI_PW_IMAGE="image test", **extra)
    rc, text = T.bash(script, args, env)
    n = int(open(count).read()) if os.path.exists(count) else 0
    return rc, n, text


def main():
    tmp = gi_paths.scratch("arms-exit-test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "bin"))
    with open(os.path.join(tmp, "bin", "py"), "w", newline="\n") as f:
        f.write(FAKE_PY)
    open(os.path.join(tmp, "fake_dumpcap.py"), "w").write(FAKE_DUMPCAP)
    try:
        for label, script, args, arms, bad in [
                ("B3 off", "baseline_b3_golive.sh", ["off"], 5, 3),
                ("B11", "baseline_b_noX2.sh", ["B11"], 6, 6),
                ("B13", "baseline_b_noX2.sh", ["B13"], 8, 1),
                ("B11raw", "baseline_b_noX2.sh", ["B11raw"], 6, 4),
                ("B2app", "baseline_b_hand.sh", ["B2app"], 3, 2)]:
            rc, n, text = case(tmp, label, script, args, arms)
            check("%s: every arm exits 0 -> exit 0 (%d arms)" % (label, n), rc == 0 and n == arms, (rc, n, text))
            rc, n, text = case(tmp, label, script, args, arms, fail_at=bad)
            check("%s: arm %d exits 2 -> exit 1" % (label, bad), rc == 1, (rc, text))
            check("%s: every arm still runs after the failing one" % label, n == arms, (n, text))
            check("%s: the failure is said" % label, "exited non-zero" in text, text)
        rc, n, text = case(tmp, "B11raw capture", "baseline_b_noX2.sh", ["B11raw"], 6, FAKE_DUMPCAP_RC="3")
        check("B11raw: a capture that fails -> exit 1", rc == 1 and n == 6, (rc, n, text))
        rc, n, text = case(tmp, "B4", "baseline_b_noX2.sh", ["B4"], 0, FAKE_B4_RC="1")
        check("B4: its own exit 1 is not an arm's -> exit 0", rc == 0 and "B4 exit 1" in text, (rc, text))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
