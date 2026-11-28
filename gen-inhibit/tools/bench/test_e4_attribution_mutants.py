#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py's attribution check (2026-10-29, gen-inhibit
reviewer: the old trail-window count voids no arm, provenance decides; spec 12.1,
c5849685, 2026-11-19: no whole-arm count voids, attach_provenance voids on the count over
the same window from the arm); test_e4_stimulus_split.py must fail on each BY A CHECK. A crash is not a kill, and an
edit that does not apply is an error. The unmutated copy runs first as the control.

    py -3.14 -B gen-inhibit/tools/bench/test_e4_attribution_mutants.py

Each mutant is a copy of e4_device_calibration.py in the worktree's scratch/, one edit
applied, run through E4_DIR. About 5 s a mutant. Gen-inhibit tester, 2026-10-29.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_e4_stimulus_split.py")
TARGET = "e4_device_calibration.py"
MUTANTS = [
    (None, None, None),
    ("the superseded count still voids, as before 2026-10-29",
     "    if mode != MODE_RESPOND:\n", "    if False:\n"),
    ("RESPOND's whole-arm count printed as the trail count",
     "    if mode != MODE_RESPOND:\n", "    if True:\n"),
    ("an agreeing count is read as a disagreement",
     "    if ours_on_wire == tx_ok:\n", "    if False:\n"),
    # spec 12.1 (c5849685): the whole-arm count voids nothing; the same-window count does
    ("run_arm voids on the whole-arm count again, as before c5849685",
     "        line, _none = attribution_check(mode, len(ours_counted), tx_ok, counted_as)\n        print(line)\n",
     "        line, _none = attribution_check(mode, len(ours_counted), tx_ok, counted_as)\n        print(line)\n"
     "        void_reasons.append(line)\n"),
    ("attach_provenance drops the same-window reason",
     "        if why:\n            arm.setdefault(\"void_reasons\", []).append(why)\n",
     "        if False:\n            arm.setdefault(\"void_reasons\", []).append(why)\n"),
    ("attach_provenance records the reason but leaves the arm unvoided",
     "            arm[\"void\"] = True\n            print(\"  ---- ARM VOID:", "            print(\"  ---- ARM VOID:"),
    ("attach_provenance voids on an agreement too",
     "        why = AV.attribution_reason(att)\n",
     "        why = AV.attribution_reason(att) or (att and \"x\")\n"),
    # F4 (2026-11-27): rx_missed and ctr_bad over time from a POST
    ("the Hammer row drops ctr_bad",
     "                                   + page_counter_fields(txt))\n", "                                   )\n"),
    ("page_counter_fields reads ctr_ok for ctr_bad",
     "        return [d.get(\"ctr_bad\")]\n", "        return [d.get(\"ctr_ok\")]\n"),
    ("the Hammer row's time is taken after the answer",
     "                self.n += 1\n", "                self.n += 1\n                t0 = time.perf_counter()\n"),
    ("perf_base is the epoch base, not the perf one",
     "        \"perf_base\": round(base, 4),\n", "        \"perf_base\": round(base_epoch, 4),\n"),
]


def main():
    base = gi_paths.scratch("mutants-e4_attribution")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate(MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, E4_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
