"""Tests of witness.py's pure judges (slcan_spi's jamming checks, SPEC T13),
and their can-fail. NOT FOR VEHICLE (SPEC T14). No hardware.

    python3.13 -B test_witness.py            ALL PASS, exit 0
    python3.13 -B test_witness.py --mutants  each mutant of witness.py must
                                             fail them; exit 0 only if all do

Mutants are made in memory, as test_jam_host.py's.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "witness.py")


def run_tests(w):
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    # clock
    check(w.clock_ok([(100.0, 100.01), (101.0, 101.02)])[0], "small lag refused")
    check(not w.clock_ok([(100.0, 103.0), (101.0, 104.0)])[0], "3 s lag accepted")
    check(not w.clock_ok([(5.0, 1000.0)])[0], "device clock not epoch accepted")
    check(not w.clock_ok([(100.5, 100.0)])[0], "stamps ahead of receipt accepted")
    check(w.clock_ok([])[0], "empty refused")

    # clusters
    g = w.clusters([1.0, 1.001, 1.002, 2.0, 3.5, 3.6])
    check(g == [(1.0, 1.002, 3), (2.0, 2.0, 1), (3.5, 3.6, 2)], "clusters %r" % g)

    # judge_stop: a train to deadline 10.0
    train = [5.0 + 0.005 * i for i in range(1000)]        # last 9.995
    v, _ = w.judge_stop(train, 10.0, quiet_to=12.0)
    check(v == "PASS", "stop at the deadline: %s" % v)
    v, _ = w.judge_stop(train + [10.6], 10.0, quiet_to=12.0)
    check(v == "FAIL", "late error frame passed: %s" % v)
    v, _ = w.judge_stop(train + [11.9], 10.0, quiet_to=12.0)
    check(v == "FAIL", "error frame in the quiet window passed: %s" % v)
    v, _ = w.judge_stop(train + [12.5], 10.0, quiet_to=12.0)
    check(v == "PASS", "an error frame after quiet_to counted: %s" % v)
    v, _ = w.judge_stop([5.0, 6.0], 10.0, quiet_to=12.0)
    check(v == "INCONCLUSIVE", "early stop not inconclusive: %s" % v)
    v, _ = w.judge_stop([], 10.0)
    check(v == "INCONCLUSIVE", "nothing seen not inconclusive: %s" % v)
    v, _ = w.judge_stop(train, 8.0, quiet_to=12.0)
    check(v == "FAIL", "shifted deadline (the can-fail) passed: %s" % v)
    v, _ = w.judge_stop([1.0] + train, 10.0, quiet_from=4.0, quiet_to=12.0)
    check(v == "PASS", "an error frame before quiet_from counted: %s" % v)

    # counts
    check(w.judge_count([1.0, 5.0], 0.5, 2.0, 1) == (True, 1), "count 1")
    check(w.judge_count([1.0, 1.1], 0.5, 2.0, 1) == (False, 2), "count 2 as 1")
    check(w.judge_count([2.0], 0.5, 2.0, 1)[0], "count includes t1")

    # groups: 10 at 1 s; the 9-at-1.1 s control must not pass as it
    ten = [10.0 + i for i in range(10)]
    nine = [10.0 + 1.1 * i for i in range(9)]
    check(w.judge_groups(ten, 9.0, 21.0, 10, 1.0)[0], "10 at 1 s refused")
    check(not w.judge_groups(nine, 9.0, 21.0, 10, 1.0)[0], "9 at 1.1 s passed as 10 at 1 s")
    check(w.judge_groups(nine, 9.0, 21.0, 9, 1.1)[0], "9 at 1.1 s refused")
    check(not w.judge_groups(ten, 9.0, 21.0, 9, 1.1)[0], "10 at 1 s passed as 9 at 1.1 s")
    uneven = ten[:5] + [t + 0.3 for t in ten[5:]]
    check(not w.judge_groups(uneven, 9.0, 21.0, 10, 1.0)[0], "a 1.3 s interval passed as 1 s")
    two = ten[:5] + [15.0005] + ten[5:]
    check(not w.judge_groups(two, 9.0, 21.0, 10, 1.0)[0], "a group of 2 passed")
    # the channel: the Leaf, not CANlib's virtual stand-in
    check(w.real_channel({"device_name": "Kvaser Leaf Light v2", "serial": 1})[0], "Leaf refused")
    check(not w.real_channel({"device_name": "Kvaser Virtual CAN Driver"})[0], "virtual accepted")
    check(not w.real_channel({"device_name": ""})[0], "unnamed accepted")
    check(not w.real_channel(None)[0], "no info accepted")
    return fails


MUTANTS = [
    ("virtual-accepted", 'if "virtual" in name.lower():', "if False:"),
    ("lag-unbounded", "return -0.1 <= lag < MAX_LAG_S, lag", "return -0.1 <= lag, lag"),
    ("ahead-allowed", "return -0.1 <= lag < MAX_LAG_S, lag", "return lag < MAX_LAG_S, lag"),
    ("one-cluster", "if out and t - out[-1][1] <= gap_s:", "if out:"),
    ("late-ignored", "    if late:\n", "    if False:\n"),
    ("quiet-window-ignored",
     "ts = [t for t in err_ts if (quiet_from is None or t >= quiet_from) and t <= quiet_to]",
     "ts = [t for t in err_ts if (quiet_from is None or t >= quiet_from) and t <= deadline + window_s]"),
    ("early-passes", "if last < deadline - window_s:", "if False:"),
    ("count-at-least", "return n == want, n", "return n >= want, n"),
    ("group-size-ignored", "if any(c != 1 for _, _, c in g):", "if False:"),
    ("interval-ignored", "if abs(x - interval_s) > tol_s]", "if False]"),
]


def load(text):
    m = types.ModuleType("witness_under_test")
    m.__file__ = SRC
    exec(compile(text, SRC, "exec"), m.__dict__)
    return m


def main():
    with open(SRC, encoding="utf-8") as f:
        original = f.read()
    fails = run_tests(load(original))
    if "--mutants" not in sys.argv:
        for x in fails:
            print("FAIL", x)
        print("%d FAILED" % len(fails) if fails else "ALL PASS")
        return 1 if fails else 0
    if fails:
        print("ORIGINAL FAILS:", fails)
        return 1
    bad = 0
    for name, old, new in MUTANTS:
        if original.count(old) != 1:
            print("%-22s BROKEN MUTANT (matches %d times)" % (name, original.count(old)))
            bad += 1
            continue
        try:
            f = run_tests(load(original.replace(old, new)))
        except Exception as e:
            f = ["raised %r" % e]
        print("%-22s %s" % (name, "killed" if f else "SURVIVED"))
        bad += 0 if f else 1
    print("%d of %d mutants survived or broken" % (bad, len(MUTANTS)) if bad
          else "all %d mutants killed" % len(MUTANTS))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
