"""Tests of witness.py (slcan_spi's jamming checks, SPEC T13): its pure
judges and window arithmetic, and its collecting thread on a fake bus; and
their can-fail. NOT FOR VEHICLE (SPEC T14). No hardware.

    python3.13 -B test_witness.py            ALL PASS, exit 0
    python3.13 -B test_witness.py --mutants  each mutant of witness.py must
                                             fail them; exit 0 only if all do

Mutants are made in memory, as test_jam_host.py's.
"""

import os
import sys
import time
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

    # the closing pulse, every witness check's positive control (gen-inhibit
    # tester's review, 2026-11-13): exactly one error frame
    check(w.judge_closing([5.0], 4.9, 6.0)[0], "closing pulse heard refused")
    check(not w.judge_closing([], 4.9, 6.0)[0], "deaf witness passed the closing pulse")
    check(not w.judge_closing([5.0, 5.1], 4.9, 6.0)[0], "two error frames as one pulse")
    check(not w.judge_closing([6.5], 4.9, 6.0)[0], "a frame after the window counted")

    # stop windows: a TRAIN sent at 100 for 3 s; slcan_spi_a's backstop
    # (stop (b) at deadline + 2 s) must fall inside the quiet window
    t_sent, ms = 100.0, 3000
    win = w.stop_windows(t_sent, t_sent + ms / 1000.0)
    check(win == {"deadline": 103.0, "quiet_from": 100.0, "quiet_to": 106.0}, "stop windows %r" % (win,))
    train = [t_sent + 0.005 * i for i in range(600)]       # last 102.995
    v, _ = w.judge_stop(train, **win)
    check(v == "PASS", "train to its deadline: %s" % v)
    v, _ = w.judge_stop(train + [105.2], **win)
    check(v == "FAIL", "backstop frame at deadline + 2.2 s passed: %s" % v)
    win = w.stop_windows(t_sent, t_sent + ms / 1000.0, shift_s=2.0)
    v, _ = w.judge_stop(train, **win)
    check(v == "FAIL", "the can-fail's shifted deadline passed: %s" % v)

    fails.extend(witness_thread_tests(w))
    return fails


class FakeBus:
    """python-can's bus as Witness uses it: recv(timeout) and shutdown().
    Gives msgs, then raises `then` if set, else idles."""

    def __init__(self, msgs, then=None):
        self.msgs = list(msgs)
        self.then = then
        self.shut = False

    def recv(self, timeout):
        if self.msgs:
            return self.msgs.pop(0)
        if self.then is not None:
            raise self.then
        time.sleep(min(timeout, 0.01))
        return None

    def shutdown(self):
        self.shut = True


def msg(t, err=True, host_stamped=False, can_id=0):
    return types.SimpleNamespace(timestamp=t, is_error_frame=err, arbitration_id=can_id,
                                 host_stamped=host_stamped)


def witness_thread_tests(w):
    """Witness's collecting thread on a fake bus: the host-stamped skip, the
    Void path, and a thread that died or never ran (gen-inhibit tester's
    review, 2026-11-13: a dead witness must never read as a quiet bus)."""
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    def run(bus, start=True, settle=0.15):
        wit = w.Witness(bus)
        if start:
            wit.start()
            time.sleep(settle)
        return wit.finish()

    now = time.time()
    wit = run(FakeBus([msg(now - 0.01), msg(now - 0.01, host_stamped=True),
                       msg(now - 0.01, err=False, can_id=0x123), msg(now - 0.01)]))
    try:
        errs = wit.err_times()
        check(len(errs) == 2, "error frames %d, want 2 (host-stamped left out)" % len(errs))
    except Exception as e:
        check(False, "a healthy witness raised %r" % e)
    s = wit.summary()
    check(s["host_stamped"] == 1 and s["frames"] == 1 and s["errors"] == 2,
          "summary counts %r" % s)
    check(s["median_lag_s"] is not None and 0 <= s["median_lag_s"] < 0.5,
          "summary lag %r" % s.get("median_lag_s"))
    check(wit.bus.shut, "bus not shut down")

    wit = run(FakeBus([msg(now - 5.0)]))
    try:
        wit.err_times()
        check(False, "5 s lag not VOID")
    except w.Void:
        pass
    except Exception as e:
        check(False, "5 s lag raised %r, not Void" % e)

    wit = run(FakeBus([msg(now - 0.01)], then=OSError("canWrite hardware error -15")))
    try:
        wit.err_times()
        check(False, "a witness whose thread failed gave error frames")
    except w.Dead as e:
        check("-15" in str(e), "Dead does not name the thread's failure: %s" % e)
    except Exception as e:
        check(False, "a failed witness raised %r, not Dead" % e)
    check("-15" in (wit.summary()["failed"] or ""), "the failure not kept: %r" % wit.summary())

    wit = run(FakeBus([msg(now - 0.01)]), start=False)
    try:
        wit.err_times()
        check(False, "a witness that never ran gave error frames")
    except w.Dead:
        pass
    except Exception as e:
        check(False, "a witness that never ran raised %r, not Dead" % e)

    wit = w.Witness(FakeBus([]))
    try:
        wit.err_times()
        check(False, "err_times() before finish() gave error frames")
    except w.Dead:
        pass
    except Exception as e:
        check(False, "err_times() before finish() raised %r, not Dead" % e)
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
    ("closing-any", "return n == 1, ", "return n >= 1, "),
    ('quiet-to-deadline', '"quiet_to": t_stop + quiet_s', '"quiet_to": t_stop'),
    ("shift-ignored", "t_stop - shift_s,", "t_stop,"),
    ("host-stamped-counted", "                    self.host_stamped += 1\n                    continue\n",
     "                    self.host_stamped += 1\n"),
    ("failure-swallowed", "self.failed = \"%s: %s\" % (type(e).__name__, e)", "pass"),
    ("dead-unchecked", "if not self.alive_at_finish:", "if False:"),
    ("failed-unchecked", "if self.failed:", "if False:"),
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
