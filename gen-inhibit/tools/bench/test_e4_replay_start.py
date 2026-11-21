"""Harness test for e4_device_calibration's replay start (gen-inhibit reviewer's answer to the
run (C) report, item 4(iv), 2026-11-20): the replay's base is taken after the --flood-slcan
filler is open and started, an arm whose first frame goes out more than START_LAG_MAX_S behind
the replay's own schedule is void, and each refusal's text, index and time is kept.

Run C's B22 arms (gen-inhibit-bench_20261120T122548_6846) started ~3.37 s behind schedule and
had ~6600 frames refused; the record kept only the count.

    python3.13 -B gen-inhibit/tools/bench/test_e4_replay_start.py
Mutants: test_e4_replay_start_mutants.py (E4_DIR runs another copy of the tool).
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("E4_DIR"):
    sys.path.insert(0, os.environ["E4_DIR"])
import e4_device_calibration as E                               # noqa: E402


class FakeCanError(Exception):
    pass


def ledger_after(steps, keep=E.REFUSALS_KEPT):
    """A ReplayLedger fed ("sent", due, now) and ("refuse", index, t_s, text) steps."""
    led = E.ReplayLedger(keep=keep)
    for s in steps:
        if s[0] == "sent":
            led.sent(s[1], s[2])
        else:
            led.refuse(s[1], s[2], FakeCanError(s[3]))
    return led


def first_lag_is_the_first():
    led = ledger_after([("sent", 10.0, 10.0002), ("sent", 10.1, 10.104), ("sent", 10.2, 10.2001)])
    return abs(led.first_lag_s - 0.0002) < 1e-9 and abs(led.max_lag_s - 0.004) < 1e-9


def worst_lag_kept_when_first():
    led = ledger_after([("sent", 0.0, 3.374), ("sent", 0.001, 3.3741)])
    return abs(led.first_lag_s - 3.374) < 1e-9 and abs(led.max_lag_s - 3.374) < 1e-9


def no_send_no_lag():
    led = E.ReplayLedger()
    f = led.fields()
    return (led.first_lag_s is None and led.max_lag_s is None and f["start_lag_s"] is None
            and f["max_send_lag_s"] is None and f["refusals_kept"] == [])


def refusal_recorded():
    led = ledger_after([("refuse", 47, 0.1234567, "Transmit buffer full")])
    r = led.refusals
    return (led.refused == 1 and len(r) == 1 and r[0]["index"] == 47 and r[0]["t_s"] == 0.123457
            and r[0]["error"] == "FakeCanError: Transmit buffer full")


def refusals_bounded_but_counted():
    led = ledger_after([("refuse", i, i * 0.001, "full") for i in range(7)], keep=3)
    return (led.refused == 7 and [r["index"] for r in led.refusals] == [0, 1, 2]
            and led.fields()["refusals_kept"] == led.refusals)


def default_keep():
    return E.ReplayLedger().keep == E.REFUSALS_KEPT == 50


def fields_carry_lags():
    led = ledger_after([("sent", 1.0, 1.0000123), ("sent", 2.0, 2.0021)])
    f = led.fields()
    return (f["start_lag_s"] == 1.2e-05 and f["max_send_lag_s"] == 0.0021
            and f["start_lag_max_s"] == E.START_LAG_MAX_S)


def reason(lag):
    return E.start_lag_reason(lag)


def run_arm_src():
    return inspect.getsource(E.run_arm)


def base_after_filler():
    """The base is taken once, after the filler's open, its board read and its start, and
    after the schedule's posters are built; base_epoch comes from the same base."""
    src = run_arm_src()
    i = src.find("    base = perf()\n")
    return (src.count("base = perf()") == 1 and i > 0
            and i > src.find("sl_filler.start()") > src.find("sl_before = slcan_report(sl)") > 0
            and i > src.find("sched = [") > 0
            and src.find("base_epoch = time.time() - (perf() - base)") > i
            and i < src.find("for k in range(len(msgs)):"))


def loop_feeds_ledger():
    """Each frame's lag is taken after its busy-wait and before its send; a refusal is kept
    in the send's own except, beside the count."""
    src = run_arm_src()
    w = src.find("        while perf() < due:\n            pass\n")
    s = src.find("ledger.sent(due, perf())")
    t = src.find("tx.send(msgs[k], timeout=0.5)")
    x = src.find("except can.CanError as e:", t)
    r = src.find("ledger.refuse(k, perf() - base, e)", x)
    return 0 < w < s < t < x < r and src[x:r].count("fails += 1") == 1 and r - x < 120


def lag_voids_and_is_recorded():
    src = run_arm_src()
    j = src.find("lag_why = start_lag_reason(ledger.first_lag_s)")
    return (j > 0 and "if lag_why:\n        void_reasons.append(lag_why)" in src[j:j + 120]
            and "**ledger.fields()," in src and j < src.find("arm[\"void\"] = bool(void_reasons)"))


def main():
    checks = [
        ("the first frame's lag is the first, the worst is the largest", first_lag_is_the_first()),
        ("run C's shape: 3.374 s late at the first frame is both the first and the worst lag",
         worst_lag_kept_when_first()),
        ("no frame sent: no lag, nothing kept", no_send_no_lag()),
        ("a refusal keeps its index, time (us) and the error's type and text", refusal_recorded()),
        ("refusals past the bound are counted, not kept", refusals_bounded_but_counted()),
        ("50 refusals kept by default", default_keep()),
        ("the record's fields carry the start lag, the worst lag and the bound", fields_carry_lags()),
        ("start bound is 5 ms", E.START_LAG_MAX_S == 0.005),
        ("on time (0.0 s) voids nothing", reason(0.0) is None),
        ("2 ms late voids nothing", reason(0.002) is None),
        ("exactly at the 5 ms bound voids nothing", reason(0.005) is None),
        ("5.1 ms late voids, naming the lag and the bound",
         reason(0.0051) is not None and "5.1 ms behind" in reason(0.0051) and "bound 5.0 ms" in reason(0.0051)),
        ("run C's 3374 ms start voids", reason(3.374) is not None and "3374.0 ms behind" in reason(3.374)),
        ("no frame sent voids: the start is not known", reason(None) is not None and "not known" in reason(None)),
        ("a bound given is used", E.start_lag_reason(0.004, bound=0.003) is not None
         and E.start_lag_reason(0.004, bound=0.01) is None),
        ("run_arm takes the replay's base after the filler is started", base_after_filler()),
        ("run_arm's replay loop feeds the ledger: lag before each send, refusal in its except",
         loop_feeds_ledger()),
        ("run_arm voids on the start lag and records the ledger's fields", lag_voids_and_is_recorded()),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
