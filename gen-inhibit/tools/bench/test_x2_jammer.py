"""Harness test for x2_jammer.py: parse_line, judge_stop, judge_counter_stop,
combine, judge_can2, clusters, per_pulse, and sub_id against a fake jammer console.

Synthetic inputs, no hardware. Every verdict has a case that must come out
the other way, so no judge can pass by construction. The firmware's console
lines below are copied from the format strings in x2_jammer/src/main.cpp.

Run: py -3.14 gen-inhibit/tools/bench/test_x2_jammer.py
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import x2_jammer as X                                           # noqa: E402

# The jammer is built on the charge interposer's platform: its pin in
# charge-interposer/firmware/platformio.ini on charge-interposer/main.
PLATFORM = "https://github.com/pioarduino/platform-espressif32/releases/download/54.03.21-2/platform-espressif32.zip"
JAMMER_INI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "x2_jammer", "platformio.ini")


def ini_platforms(text):
    """Every 'platform = ...' value in a platformio.ini, comment lines skipped."""
    out = []
    for ln in text.splitlines():
        k, sep, v = ln.partition("=")
        if sep and k.strip() == "platform" and not ln.lstrip().startswith(";"):
            out.append(v.strip())
    return out

PARSE = [
    ("JAMMER ACK TRAIN w_us=20 p_us=200 ms=2000 t0_us=123",
     {"kind": "ACK", "what": "TRAIN", "w_us": 20, "ms": 2000}),
    ("JAMMER DONE TRAIN end=stopped stop_by=deadline_timer pulses=9990 chunks=10 "
     "elapsed_us=2000400 over_deadline_us=-12",
     {"kind": "DONE", "what": "TRAIN", "stop_by": "deadline_timer",
      "over_deadline_us": -12}),
    ("JAMMER ERR need_exactly_3_args got=2", {"kind": "ERR", "got": 2}),
    ("JAMMER CAPPED ms 60000 -> 30000", {"kind": "CAPPED", "what": "ms"}),
    ("JAMMER PULSE k=3 t_us=5000 rel_us=3001000", {"kind": "PULSE", "k": 3}),
    ("JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes",
     {"kind": "INFO", "canstat": "0x80", "config_mode": "yes"}),
    ("E (123) rmt: something", None),
    ("JAMMERX ACK", None),
]

# judge_stop(err, t_start=10, t_deadline=12, obs_start=0, obs_end=20)
TRAIN = [10.0 + k * 0.01 for k in range(200)]          # 10.00 .. 11.99
STOP_CASES = [
    ("clean stop", TRAIN, {}, "PASS"),
    ("one frame 0.5 s after the deadline", TRAIN + [12.5], {}, "FAIL"),
    ("frames run to 14 s", TRAIN + [12.0 + k * 0.01 for k in range(200)], {},
     "FAIL"),
    ("nothing delivered", [], {}, "INCONCLUSIVE"),
    ("bus noisy before start", [8.5] + TRAIN, {}, "INCONCLUSIVE"),
    ("observation ended too early", TRAIN, {"obs_end": 15.0}, "INCONCLUSIVE"),
    ("observation started too late", TRAIN, {"obs_start": 9.0}, "INCONCLUSIVE"),
    ("within the margin is not late", TRAIN + [12.25], {}, "PASS"),
    ("witness blind for the last second", [x for x in TRAIN if x < 11.0], {},
     "INCONCLUSIVE"),
    ("last frame 0.3 s before the deadline still counts as seeing",
     [x for x in TRAIN if x < 11.7], {}, "PASS"),
    ("negative control: 2 s train judged against 1 s", TRAIN,
     {"t_deadline": 11.0}, "FAIL"),
]


def p_case(n, per, spacing=1.0, start=100.0):
    out = []
    for k in range(n):
        out += [start + k * spacing + j * 0.0003 for j in range(per[k])]
    return out


def samples_for(n, deltas, spacing=1.0, start=100.0):
    """Page readings at -0.4 and +0.4 s around each pulse."""
    s, v = [], 1000
    for k in range(n):
        s.append((start + k * spacing - 0.4, v))
        v += deltas[k]
        s.append((start + k * spacing + 0.4, v))
    return s


PULSE_CASES = [
    ("10 pulses, one error frame each", p_case(10, [1] * 10), 10, True, 10, None),
    ("9 delivered of 10 commanded", p_case(9, [1] * 9), 10, False, 9, None),
    ("one pulse gives two error frames", p_case(10, [1] * 4 + [2] + [1] * 5),
     10, False, 10, None),
    ("bus_error moves 3 per pulse", p_case(10, [1] * 10), 10, True, 10,
     ([3] * 10, [3] * 10)),
    ("bus_error deltas differ by pulse", p_case(3, [1] * 3), 3, True, 3,
     ([1, 5, 1], [1, 5, 1])),
]


def counter(rise_until, end=20.0, step=0.1, start=7.0, per=5, skip=None):
    """Page readings every step s from start to end; the counter rises by per
    each reading from t=10 until rise_until."""
    out, v, t = [], 100, start
    while t <= end + 1e-9:
        if 10.0 <= t <= rise_until:
            v += per
        if not (skip and skip[0] <= t <= skip[1]):
            out.append((round(t, 3), v))
        t += step
    return out


# judge_counter_stop(samples, t_start=10, t_deadline=12)
COUNTER_CASES = [
    ("counter stops at the deadline", counter(12.0), {}, "PASS"),
    ("counter still rising 1 s late", counter(13.0), {}, "FAIL"),
    ("counter never rose", counter(0.0), {}, "INCONCLUSIVE"),
    ("counter stopped 1.5 s early (device bus-off)", counter(10.5), {},
     "INCONCLUSIVE"),
    ("readings end before deadline + quiet", counter(12.0, end=15.0), {},
     "INCONCLUSIVE"),
    ("2 s gap in readings after the start", counter(12.0, skip=(13.0, 15.0)), {},
     "INCONCLUSIVE"),
    ("negative control: judged against 11 s", counter(12.0),
     {"t_deadline": 11.0}, "FAIL"),
]


COMBINE_CASES = [
    ("kvaser PASS, control FAIL, wican blind (the 01:25 shape)",
     {"kvaser": "PASS", "wican_bus_error": "INCONCLUSIVE"},
     {"kvaser": "FAIL", "wican_bus_error": "INCONCLUSIVE"}, "PASS"),
    ("both PASS, both controls FAIL",
     {"kvaser": "PASS", "wican_bus_error": "PASS"},
     {"kvaser": "FAIL", "wican_bus_error": "FAIL"}, "PASS"),
    ("passing witness whose control did not fail",
     {"kvaser": "PASS", "wican_bus_error": "PASS"},
     {"kvaser": "FAIL", "wican_bus_error": "PASS"}, "FAIL"),
    ("one witness saw a late rise", {"kvaser": "PASS", "wican_bus_error": "FAIL"},
     {"kvaser": "FAIL", "wican_bus_error": "FAIL"}, "FAIL"),
    ("no witness could show it", {"kvaser": "INCONCLUSIVE"}, {}, "INCONCLUSIVE"),
]


CAN2_CASES = [
    ("isolated: Kvaser sees all, Innomaker nothing", (12, 5, 5, 0, 0), "PASS"),
    ("Innomaker sees a WiCAN page", (12, 5, 5, 1, 0), "FAIL"),
    ("Innomaker sees an error frame", (12, 5, 5, 0, 1), "FAIL"),
    ("Kvaser saw no pages: control failed", (0, 5, 5, 0, 0), "INCONCLUSIVE"),
    ("Kvaser saw 4 of 5 pulses: control failed", (12, 4, 5, 0, 0), "INCONCLUSIVE"),
]


def main():
    bad = []
    for line, want in PARSE:
        got = X.parse_line(line)
        ok = (got is None) if want is None else (
            got is not None and all(got.get(k) == v for k, v in want.items()))
        print("  %-4s parse    %-60s" % ("ok" if ok else "FAIL", line[:60]))
        if not ok:
            bad.append(line)
            print("       %s" % got)
    for label, err, kw, want in STOP_CASES:
        args = dict(t_start=10.0, t_deadline=12.0, obs_start=0.0, obs_end=20.0)
        args.update(kw)
        got = X.judge_stop(err, **args)
        ok = got["verdict"] == want
        print("  %-4s stop     %-60s %s" % ("ok" if ok else "FAIL", label,
                                           got["verdict"]))
        if not ok:
            bad.append(label)
            print("       %s" % got)
    for label, err, n, want_one, want_groups, be in PULSE_CASES:
        samples = samples_for(len(be[0]), be[0]) if be else None
        got = X.per_pulse(err, n, 1.0, samples)
        ok = got["exactly_one_each"] == want_one and got["groups"] == want_groups
        if be:
            ok = ok and got["bus_error_delta_per_pulse"] == be[1]
        print("  %-4s pulses   %-60s %s %s" % ("ok" if ok else "FAIL", label,
                                              got["per_pulse"],
                                              got["bus_error_delta_per_pulse"]))
        if not ok:
            bad.append(label)
    for label, smp, kw, want in COUNTER_CASES:
        args = dict(t_start=10.0, t_deadline=12.0)
        args.update(kw)
        got = X.judge_counter_stop(smp, **args)
        ok = got["verdict"] == want
        print("  %-4s counter  %-60s %s" % ("ok" if ok else "FAIL", label,
                                           got["verdict"]))
        if not ok:
            bad.append(label)
            print("       %s" % got)
    for label, mains, controls, want in COMBINE_CASES:
        got = X.combine(mains, controls)[0]
        ok = got == want
        print("  %-4s combine  %-60s %s" % ("ok" if ok else "FAIL", label[:60], got))
        if not ok:
            bad.append(label)
    for label, args, want in CAN2_CASES:
        got = X.judge_can2(*args)[0]
        ok = got == want
        print("  %-4s can2     %-60s %s" % ("ok" if ok else "FAIL", label, got))
        if not ok:
            bad.append(label)
    # clock skew: a reading stamped 1.4 ms BEFORE the error frame already
    # counts it (the 01:45 shape); with the slack it is not used as "before"
    skew = [(99.6, 0), (99.9986, 1), (100.3, 1), (100.6, 1), (100.9986, 2), (101.3, 2)]
    got = X.per_pulse([100.0, 101.0], 2, 1.0, skew)
    ok = got["bus_error_delta_per_pulse"] == [1, 1]
    print("  %-4s pulses   %-60s %s" % ("ok" if ok else "FAIL",
                                       "reading stamped just before the error frame",
                                       got["bus_error_delta_per_pulse"]))
    if not ok:
        bad.append("skew")
    # a page reading that straddles two pulses must not be attributed
    got = X.per_pulse(p_case(2, [1, 1]), 2, 1.0, [(99.5, 10), (101.5, 14)])
    ok = got["bus_error_delta_per_pulse"] == [None, None]
    print("  %-4s pulses   %-60s %s" % ("ok" if ok else "FAIL",
                                       "reading straddles two pulses -> None",
                                       got["bus_error_delta_per_pulse"]))
    if not ok:
        bad.append("straddle")
    # The poll stamp (2026-11-05, gen-inhibit reviewer): (returned, bus_error, sent). A
    # reading is "after" a pulse only if sent after it, "before" only if returned before
    # it; step 1d's a61f n9_lead5 arm 0 had a poll sent 0.64 s before pulse 2's error
    # frame return 0.50 s after it with the old count, and the delta came out 0.
    for label, smp, want in (
            ("a stalled poll, sent before the frame, is not its after reading",
             [(99.5, 10, 99.48), (100.5, 10, 99.6), (100.7, 11, 100.68), (101.5, 12, 101.48)], [1, 1]),
            ("a before reading sent before the previous pulse -> None",
             [(99.5, 10, 99.48), (100.9, 11, 99.9), (101.5, 12, 101.48)], [None, None]),
            ("an after reading returned after the next pulse -> None",
             [(99.5, 10, 99.48), (101.2, 11, 100.6), (101.6, 12, 101.58)], [None, None]),
            ("req_start None is taken as sent at the return",
             [(99.5, 10, None), (100.5, 11, None), (101.5, 12, None)], [1, 1])):
        got = X.per_pulse([100.0, 101.0], 2, 1.0, smp)
        ok = got["bus_error_delta_per_pulse"] == want
        print("  %-4s pulses   %-60s %s" % ("ok" if ok else "FAIL", label[:60],
                                           got["bus_error_delta_per_pulse"]))
        if not ok:
            bad.append(label)
    # sub_id against the firmware's line discipline, with input an earlier probe left
    # unterminated (bench_identify's slcan "V\r" "N\r" leave "VN"; 2026-10-19)
    # The reset since the open (2026-11-11): a banner counts only from a board whose uptime
    # is longer than the time since the console opened, on a port neither lost nor reopened.
    for label, pending, kw, want, want_reset in (
            ("id after a probe left VN pending", "VN", {}, True, False),
            ("id with nothing pending", "", {}, True, False),
            ("id: the board booted after the open (uptime 0.5 s, open 1 s before)", "",
             {"uptime_us": 500000}, False, True),
            ("id: the port was lost and reopened (uptime long)", "",
             {"events": ["lost: SerialException", "reopened"]}, False, True),
            ("id: no uptime line in the banner", "", {"uptime_us": None}, False, None)):
        rec = {}
        try:
            X.sub_id(None, rec, FakeCon(FakeJammer(pending, kw.get("uptime_us", 3600 * 10 ** 6)),
                                        kw.get("events", [])), None)
        except Exception as e:      # a crash is not a kill: report it against this case
            rec["banner_ok"] = "RAISED %r" % e
        ok = rec.get("banner_ok") is want and rec.get("reset_since_open") is want_reset
        print("  %-4s id       %-60s %s %s" % ("ok" if ok else "FAIL", label[:60], rec.get("banner_ok"),
                                              rec.get("reset_since_open")))
        if not ok:
            bad.append(label)
    jam = FakeJammer("VN")
    jam.feed("ID\n", 0.0)
    ok = [p["kind"] for _t, _x, p in jam.out] == ["ERR"]
    print("  %-4s id       %-60s %s" % ("ok" if ok else "FAIL", "control: the fake jammer refuses VNID",
                                       [x for _t, x, _p in jam.out]))
    if not ok:
        bad.append("fake jammer control")
    got = ini_platforms(open(JAMMER_INI, encoding="utf-8").read())
    ok = got == [PLATFORM]
    print("  %-4s ini      %-60s %s" % ("ok" if ok else "FAIL", "the jammer pins the interposer's platform", got))
    if not ok:
        bad.append("platform pin")
    ok = ini_platforms("[env:x]\nplatform = espressif32\n") == ["espressif32"]
    print("  %-4s ini      %-60s" % ("ok" if ok else "FAIL", "control: a bare platform line is read as it is"))
    if not ok:
        bad.append("platform control")
    port_cases(bad)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


# ---- the ESP-IDF port's bench pass (2026-11-13) ----------------------------------------
PN = "vtrux_jammer_x2_NOT_FOR_VEHICLE"
ELF = "ab" * 32


def port_banner(name=PN, elf=ELF, twai="never_started", cfg="yes", uptime=3600 * 10 ** 6):
    """The port's five banner lines (shared/x2_test_images/jammer/README.md, "The banner")."""
    return ["JAMMER %s -- TEST EQUIPMENT, NEVER ON THE TRUCK" % name,
            "JAMMER build version=2026.11.12.2-6-g9e017baa elf=%s idf=v5.4.2" % elf,
            "JAMMER tx=GPIO7(CAN1/TWAI) twai=%s stops=a+b reset_reason=1 uptime_us=%d" % (twai, uptime),
            "JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=%s" % cfg,
            "JAMMER limits w_us=12..50 p_us>=4w..20000 ms<=30000 n<=1000 interval_ms=1..10000"]


def check(bad, group, label, ok, got=""):
    print("  %-4s %-8s %-60s %s" % ("ok" if ok else "FAIL", group, label[:60], got))
    if not ok:
        bad.append(label)


class Args:
    def __init__(self, **kw):
        d = dict(sub="id", no_wican=False, wican_mode=0, expect_name=None, expect_elf=None,
                 expect_stop_by=None, expect_end=None, post_s=3.0, w=20, n=1, interval_ms=1000)
        d.update(kw)
        self.__dict__.update(d)


def port_cases(bad):
    parsed = lambda lines: [X.parse_line(x) for x in lines]   # noqa: E731
    for label, lines, name, elf, want in (
            ("identity: the port's banner, the row's name and elf", port_banner(), PN, ELF, "PASS"),
            ("identity: a different elf", port_banner(elf="cd" * 32), PN, ELF, "FAIL"),
            ("identity: a different name", port_banner(name="jammer_stop_a_NOT_FOR_VEHICLE"), PN, ELF, "FAIL"),
            ("identity: a name without NOT_FOR_VEHICLE, both sides", port_banner(name="vtrux_jammer_x2"),
             "vtrux_jammer_x2", ELF, "FAIL"),
            ("identity: twai started", port_banner(twai="started"), PN, ELF, "FAIL"),
            ("identity: mcp2515 not in config mode", port_banner(cfg="no"), PN, ELF, "FAIL"),
            ("identity: no name line", port_banner()[1:], PN, ELF, "FAIL"),
            ("identity: no build line", port_banner()[:1] + port_banner()[2:], PN, ELF, "FAIL"),
            ("identity: no expected elf given", port_banner(), PN, None, "FAIL")):
        got = X.judge_identity(parsed(lines), name, elf)
        check(bad, "identity", label, got["verdict"] == want, "%s %s" % (got["verdict"], got["why"]))
    # sub_id with the expectations, through the fake console
    for label, kw, want in (("id: the port's banner -> identity PASS", {}, "PASS"),
                            ("id: the port booted after the open -> FAIL", {"uptime_us": 500000}, "FAIL"),
                            ("id: the wrong elf -> FAIL", {"elf": "cd" * 32}, "FAIL")):
        rec = {}
        try:
            jam = FakeJammer("", kw.get("uptime_us", 3600 * 10 ** 6),
                             banner=port_banner(elf=kw.get("elf", ELF), uptime=kw.get("uptime_us", 3600 * 10 ** 6)))
            X.sub_id(Args(expect_name=PN, expect_elf=ELF), rec, FakeCon(jam), None)
        except Exception as e:      # noqa: BLE001  a crash is reported against the case
            rec["verdict"] = "RAISED %r" % e
        check(bad, "id", label, rec.get("verdict") == want, rec.get("verdict"))
    rec = {}
    X.sub_id(Args(), rec, FakeCon(FakeJammer("", banner=port_banner())), None)
    check(bad, "id", "id without expectations writes no verdict", "verdict" not in rec, rec.get("verdict"))
    # after_done: (t_done, obs_end) with frames at the given times
    for label, ts, t_done, obs_end, want_n, want_enough in (
            ("after DONE: quiet for 3.7 s", [99.0], 100.0, 104.0, 0, True),
            ("after DONE: one frame 0.5 s after", [99.0, 100.5], 100.0, 104.0, 1, True),
            ("after DONE: a frame within the margin is not after", [100.2], 100.0, 104.0, 0, True),
            ("after DONE: a frame at the end of the window counts", [104.0], 100.0, 104.0, 1, True),
            ("after DONE: watched 2 s only", [], 100.0, 102.3, 0, False),
            ("after DONE: no DONE", [], None, 104.0, None, False)):
        got = X.after_done(ts, t_done, obs_end)
        check(bad, "after", label, got["n"] == want_n and got["enough"] is want_enough, got)
    # judge_pulses(err_ts, n, interval_s, t_send, ack, t_done, obs_end)
    one = [100.05]
    ten = [100.05 + k for k in range(10)]
    for label, ts, n, iv, ack, t_done, obs_end, want in (
            ("pulses: port, 1 pulse 1 frame, quiet around", one, 1, 1.0, True, 100.2, 104.0, "PASS"),
            ("pulses: calibration, 10 at 1 s, 1 each", ten, 10, 1.0, True, 109.2, 113.0, "PASS"),
            ("pulses: 9 at 1.1 s, 1 each", [100.05 + 1.1 * k for k in range(9)], 9, 1.1, True, 109.0, 112.5, "PASS"),
            ("pulses: a frame 1 s after DONE", one + [101.2], 1, 1.0, True, 100.2, 104.0, "FAIL"),
            ("pulses: a frame just before the window's end", one + [103.9], 1, 1.0, True, 100.2, 104.0, "FAIL"),
            ("pulses: one pulse gave two frames", one + [100.0503], 1, 1.0, True, 100.2, 104.0, "FAIL"),
            ("pulses: 9 groups for 10", ten[:9], 10, 1.0, True, 109.2, 113.0, "FAIL"),
            ("pulses: nothing delivered", [], 1, 1.0, True, 100.2, 104.0, "FAIL"),
            ("pulses: a frame 2 s before the send", [98.0] + one, 1, 1.0, True, 100.2, 104.0, "INCONCLUSIVE"),
            ("pulses: no DONE", one, 1, 1.0, True, None, 104.0, "INCONCLUSIVE"),
            ("pulses: no ACK", one, 1, 1.0, False, 100.2, 104.0, "INCONCLUSIVE"),
            ("pulses: watched 2 s after DONE", one, 1, 1.0, True, 100.2, 102.4, "INCONCLUSIVE")):
        got = X.judge_pulses(ts, n, iv, 100.0, ack, t_done, obs_end)
        check(bad, "judge_p", label, got["verdict"] == want, "%s %s" % (got["verdict"], got["why"]))
    # judge_done
    done_a = {"stop_by": "deadline_timer", "end": "stopped"}
    done_b = {"stop_by": "none", "end": "deadline_layer_b"}
    for label, done, sb, end, want in (
            ("done: nothing expected", None, None, None, True),
            ("done: stop (a) alone shows deadline_timer", done_a, "deadline_timer", None, True),
            ("done: stop (a) expected, stop (b) ended it", done_b, "deadline_timer", None, False),
            ("done: stop (b) alone, end and stop_by match", done_b, "none", "deadline_layer_b", True),
            ("done: stop (b) expected, the timer stopped it", done_a, "none", "deadline_layer_b", False),
            ("done: end expected, stop_by matches only", {"stop_by": "none", "end": "backstop_layer_b"},
             "none", "deadline_layer_b", False),
            ("done: expected, no DONE line", None, "deadline_timer", None, None)):
        got = X.judge_done(done, sb, end)
        check(bad, "done", label, got[0] is want, got)
    # fold_timeout
    clean = {"n": 0}
    for label, v, post, dc, want in (
            ("fold: PASS, quiet after, DONE matches", "PASS", clean, (True, ""), "PASS"),
            ("fold: PASS but a frame after DONE", "PASS", {"n": 1}, (True, ""), "FAIL"),
            ("fold: PASS but DONE does not match", "PASS", clean, (False, "x"), "FAIL"),
            ("fold: PASS with no DONE to judge", "PASS", clean, (None, "x"), "INCONCLUSIVE"),
            ("fold: INCONCLUSIVE with a frame after DONE", "INCONCLUSIVE", {"n": 2}, (True, ""), "FAIL"),
            ("fold: INCONCLUSIVE stays with no DONE", "INCONCLUSIVE", clean, (None, "x"), "INCONCLUSIVE"),
            ("fold: FAIL stays FAIL", "FAIL", clean, (True, ""), "FAIL"),
            ("fold: no DONE time (n None) is not a frame after", "PASS", {"n": None}, (True, ""), "PASS")):
        got = X.fold_timeout(v, "", post, dc)
        check(bad, "fold", label, got[0] == want, got)
    # judge_can2_isolation(sent, kv_seen, kv_groups, n, in_frames, in_err, probe)
    for label, args, want in (
            ("isolation: all seen on CAN1, nothing on canchg, no ACK", (50, 50, 5, 5, 0, 0, "NO_ACK"), "PASS"),
            ("isolation: canchg saw a 0x7A0", (50, 50, 5, 5, 1, 0, "NO_ACK"), "FAIL"),
            ("isolation: canchg saw an error frame", (50, 50, 5, 5, 0, 1, "NO_ACK"), "FAIL"),
            ("isolation: canchg's frame was ACKed", (50, 50, 5, 5, 0, 0, "ACKED"), "FAIL"),
            ("isolation: the probe was unclear", (50, 50, 5, 5, 0, 0, "UNCLEAR"), "INCONCLUSIVE"),
            ("isolation: the probe did not run", (50, 50, 5, 5, 0, 0, "NOT RUN"), "INCONCLUSIVE"),
            ("isolation: the Kvaser missed one 0x7A0", (50, 49, 5, 5, 0, 0, "NO_ACK"), "INCONCLUSIVE"),
            ("isolation: the PCAN sent nothing", (0, 0, 5, 5, 0, 0, "NO_ACK"), "INCONCLUSIVE"),
            ("isolation: 4 groups for 5 pulses", (50, 50, 4, 5, 0, 0, "NO_ACK"), "INCONCLUSIVE")):
        got = X.judge_can2_isolation(*args)
        check(bad, "iso", label, got[0] == want, got)
    # args_why
    for label, kw, refused in (
            ("args: pulses --no-wican", dict(sub="pulses", no_wican=True), False),
            ("args: --no-wican with --wican-mode 2", dict(sub="pulses", no_wican=True, wican_mode=2), True),
            ("args: can2-check with --no-wican", dict(sub="can2-check", no_wican=True), True),
            ("args: can2-check without it", dict(sub="can2-check"), False),
            ("args: can2-isolation with --no-wican", dict(sub="can2-isolation", no_wican=True), False),
            ("args: can2-isolation without --no-wican", dict(sub="can2-isolation"), True),
            ("args: id with both expectations", dict(expect_name=PN, expect_elf=ELF), False),
            ("args: id with the name only", dict(expect_name=PN), True),
            ("args: id with the elf only", dict(expect_elf=ELF), True),
            ("args: --expect-name with pulses", dict(sub="pulses", expect_name=PN, expect_elf=ELF), True),
            ("args: timeout --expect-stop-by", dict(sub="timeout", expect_stop_by="deadline_timer"), False),
            ("args: --expect-end with pulses", dict(sub="pulses", expect_end="deadline_layer_b"), True),
            ("args: --expect-stop-by with id", dict(expect_stop_by="none"), True)):
        got = X.args_why(Args(**kw))
        check(bad, "args", label, bool(got) is refused, got)
    pulses_no_wican(bad)
    timeout_cases(bad)
    boot_silence_cases(bad)
    witness_down_cases(bad)


class PulseCon:
    """A console that answers PULSES with ACK, one PULSE, DONE (times t+0.01..0.2)."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.lines, self.sent = [], []

    def send(self, line):
        t = time.time()
        self.sent.append(line)
        for dt, txt in ((0.01, "JAMMER ACK PULSES w_us=20 n=1 interval_ms=1000 deadline_ms=1100"),
                        (0.05, "JAMMER PULSE k=0 t_us=1 rel_us=0"), (0.2, "JAMMER DONE PULSES pulses=1")):
            self.lines.append((t + dt, txt, X.parse_line(txt)))

    def wait(self, pred, timeout, since):
        for t, _x, p in self.lines:
            if t >= since and p and pred(p):
                return t, p
        return None


class PulseWit:
    def __init__(self, con):
        self.con = con

    def times(self):
        t = self.con.lines[1][0]
        return [t + 0.002], "kvaser_hw (median lag 0.001 s)"


def fake_pulses(a):
    """sub_pulses on PulseCon with a fake clock (sleep advances it) and the WiCAN calls
    recorded instead of made. -> (rec, calls)."""
    import threading
    calls, rec = [], {}
    clock, lk = [time.time()], threading.Lock()
    real_sleep = time.sleep
    saved = (X.wican_snap, X.wican_mode, X.time.sleep, X.time.time)

    def fsleep(s):
        with lk:
            clock[0] += s
        real_sleep(0.0005)

    def snap():
        if len(calls) < 50:
            calls.append("snap")
    X.wican_snap = snap
    X.wican_mode = lambda n: calls.append("mode %d" % n)
    X.time.sleep, X.time.time = fsleep, lambda: clock[0]
    try:
        con = PulseCon()
        X.sub_pulses(a, rec, con, PulseWit(con))
    except Exception as e:      # noqa: BLE001  reported against the case
        rec["verdict"] = "RAISED %r" % e
    finally:
        X.wican_snap, X.wican_mode, X.time.sleep, X.time.time = saved
    return rec, calls


class TrainCon(PulseCon):
    """Answers TRAIN with ACK at +0.01 s and DONE at +ms/1000 with the given fields."""

    def __init__(self, done_fields, done_at=None):
        super().__init__()
        self.done_fields, self.done_at = done_fields, done_at
        self.paused = __import__("threading").Event()

    def send(self, line):
        t = time.time()
        self.sent.append(line)
        ms = int(line.split()[3])
        at = self.done_at if self.done_at is not None else ms / 1000.0
        for dt, txt in ((0.01, "JAMMER ACK TRAIN w_us=20 p_us=5000 ms=%d t0_us=1" % ms),
                        (at, "JAMMER DONE TRAIN %s pulses=1 chunks=1" % self.done_fields)):
            self.lines.append((t + dt, txt, X.parse_line(txt)))


class TrainWit:
    """Error frames every 5 ms from the ACK to `last` s after it, plus any `extra` (s after
    the ACK); t_open well before."""

    def __init__(self, con, last, extra=()):
        self.con, self.last, self.extra = con, last, extra
        self.t_open = time.time() - 100.0

    def times(self):
        t0 = self.con.lines[0][0]
        ts = [t0 + 0.005 * k for k in range(int(self.last / 0.005) + 1)] + [t0 + e for e in self.extra]
        return ts, "kvaser_hw (median lag 0.001 s)"


def fake_timeout(a, done_fields, last, extra=(), done_at=None):
    import threading
    rec = {}
    clock, lk = [time.time()], threading.Lock()
    real_sleep = time.sleep
    saved = (X.time.sleep, X.time.time)

    def fsleep(s):
        with lk:
            clock[0] += s
        real_sleep(0.0005)
    X.time.sleep, X.time.time = fsleep, lambda: clock[0]
    try:
        con = TrainCon(done_fields, done_at)
        X.sub_timeout(a, rec, con, TrainWit(con, last, extra))
    except Exception as e:      # noqa: BLE001  reported against the case
        rec["verdict"] = "RAISED %r" % e
    finally:
        X.time.sleep, X.time.time = saved
    return rec


def timeout_cases(bad):
    """sub_timeout with the DONE line judged and the window after DONE (2026-11-13)."""
    a_done = "end=stopped stop_by=deadline_timer"
    b_done = "end=deadline_layer_b stop_by=none"
    for label, kw, done, last, extra, want in (
            ("timeout: stop (a) alone, DONE stop_by=deadline_timer", dict(expect_stop_by="deadline_timer"),
             a_done, 1.99, (), "PASS"),
            ("timeout: stop (a) expected, DONE from stop (b)", dict(expect_stop_by="deadline_timer"),
             b_done, 1.99, (), "FAIL"),
            ("timeout: stop (b) alone, end and stop_by", dict(expect_stop_by="none", expect_end="deadline_layer_b"),
             b_done, 1.99, (), "PASS"),
            ("timeout: stop (b) expected, the timer ended it", dict(expect_stop_by="none",
                                                                    expect_end="deadline_layer_b"),
             a_done, 1.99, (), "FAIL"),
            ("timeout: nothing expected, clean stop", {}, a_done, 1.99, (), "PASS"),
            ("timeout: a frame 4 s after DONE", {}, a_done, 1.99, (6.0,), "FAIL"),
            ("timeout: the can-fail, control 1 s against a 2 s train", dict(control_ms=1000),
             a_done, 1.99, (), "PASS")):
        args = Args(sub="timeout", w=20, p=5000, ms=2000, expect_ms=0, control_ms=0, host_quiet=False)
        args.__dict__.update(kw)
        rec = fake_timeout(args, done, last, extra)
        ok = rec.get("verdict") == want
        if "control_ms" in kw:
            ok = ok and rec.get("negative_control", {}).get("ok") is True
        check(bad, "timeout", label, ok, "%s %s" % (rec.get("verdict"), rec.get("why")))
    # DONE early (a STOP, say) with error frames 0.5 s past it but inside the train's window:
    # judge_stop alone would call it INCONCLUSIVE (stopped seeing early); after DONE FAILs it.
    args = Args(sub="timeout", w=20, p=5000, ms=2000, expect_ms=0, control_ms=0, host_quiet=False)
    rec = fake_timeout(args, "end=stopped stop_by=stop_cmd", 1.5, done_at=1.0)
    check(bad, "timeout", "timeout: frames after an early DONE, inside the window -> FAIL",
          rec.get("verdict") == "FAIL" and rec.get("after_done", {}).get("n", 0) > 0,
          "%s %s" % (rec.get("verdict"), rec.get("why")))


def pulses_no_wican(bad):
    """sub_pulses with --no-wican: no WiCAN call at all, and the verdict in the record."""
    rec, calls = fake_pulses(Args(sub="pulses", no_wican=True))
    check(bad, "pulses", "pulses --no-wican: no WiCAN call", calls == [], calls)
    check(bad, "pulses", "pulses --no-wican: the verdict is in the record",
          rec.get("verdict") == "PASS" and rec.get("wican_samples") == X.NO_WICAN,
          "%s %s" % (rec.get("verdict"), rec.get("why")))
    rec, calls = fake_pulses(Args(sub="pulses"))
    check(bad, "pulses", "control: without --no-wican the WiCAN is set and read",
          "mode 0" in calls and "snap" in calls, calls[:4])


def resets_ok(n=3):
    return [{"n": k, "banner_after_s": 0.4, "witness_err_frames": 0, "config_mode": "yes"}
            for k in range(n)]


CONTROL_OK = {"witness_err_frames": 1, "done": {"kind": "DONE"}}


class BootCon(PulseCon):
    """The jammer's console for boot-silence: a reset prints the mcp2515 banner line 0.4 s
    later; PULSES is answered ACK, PULSE and DONE (PulseCon's)."""

    def __init__(self):
        super().__init__()
        self.resets = 0

    def pulse_reset(self):
        self.resets += 1
        txt = "JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes"
        self.lines.append((time.time() + 0.4, txt, X.parse_line(txt)))


class BootWit:
    """Error frames: the closing pulse's (when `control`), 2 ms after its PULSE line, and
    `after_reset` s after each reset's banner."""

    def __init__(self, con, control=True, after_reset=None):
        self.con, self.control, self.after_reset = con, control, after_reset

    def times(self):
        ts = []
        for t, _x, p in self.con.lines:
            if p and p["kind"] == "PULSE" and self.control:
                ts.append(t + 0.002)
            if p and p["kind"] == "INFO" and self.after_reset is not None:
                ts.append(t + self.after_reset)
        return sorted(ts), "kvaser_hw (median lag 0.001 s)"


def fake_boot(con, wit, resets=3):
    import threading
    rec = {}
    clock, lk = [time.time()], threading.Lock()
    real_sleep = time.sleep
    saved = (X.time.sleep, X.time.time)

    def fsleep(s):
        with lk:
            clock[0] += s
        real_sleep(0.0005)
    X.time.sleep, X.time.time = fsleep, lambda: clock[0]
    try:
        X.sub_boot_silence(Args(sub="boot-silence", resets=resets, w=20), rec, con, wit)
    except Exception as e:      # noqa: BLE001  reported against the case
        rec["verdict"] = "RAISED %r" % e
    finally:
        X.time.sleep, X.time.time = saved
    return rec


def boot_silence_cases(bad):
    """judge_boot_silence and sub_boot_silence's closing pulse, the positive control
    (2026-11-13)."""
    for label, resets, control, want in (
            ("boot: 3 silent resets, closing pulse once -> PASS", resets_ok(), CONTROL_OK, "PASS"),
            ("boot: an error frame after a reset -> FAIL",
             resets_ok()[:2] + [dict(resets_ok()[2], witness_err_frames=1)], CONTROL_OK, "FAIL"),
            ("boot: an error frame and a deaf control -> FAIL, not INCONCLUSIVE",
             [dict(resets_ok()[0], witness_err_frames=2)], {"witness_err_frames": 0, "done": {}}, "FAIL"),
            ("boot: no banner after a reset -> FAIL",
             [dict(resets_ok()[0], banner_after_s=None)], CONTROL_OK, "FAIL"),
            ("boot: MCP2515 not in configuration mode -> FAIL",
             [dict(resets_ok()[0], config_mode="no")], CONTROL_OK, "FAIL"),
            ("boot: closing pulse not seen (deaf witness) -> INCONCLUSIVE", resets_ok(),
             dict(CONTROL_OK, witness_err_frames=0), "INCONCLUSIVE"),
            ("boot: closing pulse seen twice -> INCONCLUSIVE", resets_ok(),
             dict(CONTROL_OK, witness_err_frames=2), "INCONCLUSIVE"),
            ("boot: no control record -> INCONCLUSIVE", resets_ok(), None, "INCONCLUSIVE"),
            ("boot: closing pulse with no DONE -> INCONCLUSIVE", resets_ok(),
             dict(CONTROL_OK, done=None), "INCONCLUSIVE"),
            ("boot: no reset made -> INCONCLUSIVE", [], CONTROL_OK, "INCONCLUSIVE")):
        v, why = X.judge_boot_silence(resets, control)
        check(bad, "boot", label, v == want, "%s %s" % (v, why))
    con = BootCon()
    rec = fake_boot(con, BootWit(con))
    check(bad, "boot", "sub: 3 resets, then one closing PULSES w 1 -> PASS",
          rec.get("verdict") == "PASS" and con.resets == 3 and con.sent == ["PULSES 20 1 1000"]
          and rec.get("control", {}).get("witness_err_frames") == 1,
          "%s %s sent %s" % (rec.get("verdict"), rec.get("why"), con.sent))
    con = BootCon()
    rec = fake_boot(con, BootWit(con, control=False))
    check(bad, "boot", "sub: a witness that hears nothing -> INCONCLUSIVE",
          rec.get("verdict") == "INCONCLUSIVE", "%s %s" % (rec.get("verdict"), rec.get("why")))
    con = BootCon()
    rec = fake_boot(con, BootWit(con, after_reset=1.0))
    check(bad, "boot", "sub: an error frame 1 s after a banner -> FAIL",
          rec.get("verdict") == "FAIL", "%s %s" % (rec.get("verdict"), rec.get("why")))


class DyingBus:
    """A Kvaser bus: `good` error frames, then recv raises (CANlib's error), or None forever."""

    def __init__(self, good=2, raises=True):
        self.left, self.raises, self.shut = good, raises, False

    def recv(self, timeout=None):
        if self.left > 0:
            self.left -= 1
            m = types.SimpleNamespace(is_error_frame=True, timestamp=time.time(), arbitration_id=0)
            return m
        if self.raises:
            raise RuntimeError("canRead hardware error (-15)")
        time.sleep(0.005)
        return None

    def shutdown(self):
        self.shut = True


def bare_witness(bus):
    """An x2_jammer.Witness on a fake bus, without kvaser_bus (no lock, no adapter)."""
    import threading
    w = X.Witness.__new__(X.Witness)
    threading.Thread.__init__(w, daemon=True)
    w._state()
    w.bus = bus
    return w


def witness_down_cases(bad):
    """A witness whose read raised, or whose thread is not running, is never judged
    (WitnessDown -> NOT-RUN, 2026-11-13)."""
    w = bare_witness(DyingBus(good=2, raises=True))
    w.start()
    w.join(2.0)
    try:
        w.times()
        got = "no raise"
    except X.WitnessDown as e:
        got = str(e)
    check(bad, "witness", "a read that raised: times() raises WitnessDown",
          "read failed" in got and "-15" in got and len(w.err) == 2, got)
    w = bare_witness(DyingBus(good=2, raises=False))
    w.start()
    time.sleep(0.05)
    try:
        ts, _note = w.times()
        ok = len(ts) == 2 and w.down() is None
    except X.WitnessDown as e:
        ok, ts = False, str(e)
    w.close()
    check(bad, "witness", "control: a running witness is judged, and down() is None after close",
          ok and w.down() is None and w.bus.shut, ts)
    w = bare_witness(DyingBus(good=0, raises=False))
    check(bad, "witness", "a thread not running (never started) is down",
          w.down() == "witness thread not running", w.down())

    def sub(a, rec, con, wit):
        raise X.WitnessDown("witness read failed at 1.000: RuntimeError: x")
    rec = {}
    X.run_guarded(sub, Args(sub="boot-silence"), rec, None, None)
    check(bad, "witness", "run_guarded: WitnessDown -> NOT-RUN, not a verdict",
          rec.get("verdict") == "NOT-RUN" and "witness down" in rec.get("why", ""), rec)


class FakeJammer:
    """The firmware's console (x2_jammer/src/main.cpp loop() and handle()): CR dropped,
    a line runs on LF, an empty line is ignored, ID prints the banner's src_digest line
    (its uptime, unless uptime_us is None) and its mcp2515 line, any other word is
    unknown_command."""

    def __init__(self, pending="", uptime_us=3600 * 10 ** 6, banner=None):
        self.buf, self.out, self.uptime_us, self.banner = pending, [], uptime_us, banner

    def feed(self, data, t):
        for ch in data:
            if ch == "\r":
                continue
            if ch != "\n":
                self.buf += ch
                continue
            words, self.buf = self.buf.split(), ""
            if not words:
                continue
            if words[0] == "ID" and self.banner is not None:
                lines = list(self.banner)       # the ESP-IDF port's five lines
            elif words[0] == "ID":
                up = "" if self.uptime_us is None else " uptime_us=%d" % self.uptime_us
                lines = ["JAMMER src_digest=0123abcd tx=GPIO7(CAN1/TWAI) reset_reason=1" + up,
                         "JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes"]
            else:
                lines = ["JAMMER ERR unknown_command"]
            for txt in lines:
                self.out.append((t, txt, X.parse_line(txt)))


class FakeCon:
    """The console: opened 1 s before it is made, then `events` after the open."""

    def __init__(self, jam, events=()):
        import threading
        self.jam = jam
        self.lock = threading.Lock()
        t = time.time()
        self.events = [(t - 1.0, "open")] + [(t - 0.5, e) for e in events]

    @property
    def lines(self):
        return self.jam.out

    def send(self, line):
        self.jam.feed(line + "\n", time.time())

    def wait(self, pred, timeout, since):
        for t, _txt, p in self.jam.out:
            if t >= since and p is not None and pred(p):
                return t, p
        return None


if __name__ == "__main__":
    sys.exit(main())
