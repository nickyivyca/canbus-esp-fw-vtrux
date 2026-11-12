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
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


class FakeJammer:
    """The firmware's console (x2_jammer/src/main.cpp loop() and handle()): CR dropped,
    a line runs on LF, an empty line is ignored, ID prints the banner's src_digest line
    (its uptime, unless uptime_us is None) and its mcp2515 line, any other word is
    unknown_command."""

    def __init__(self, pending="", uptime_us=3600 * 10 ** 6):
        self.buf, self.out, self.uptime_us = pending, [], uptime_us

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
            if words[0] == "ID":
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
        self.jam = jam
        t = time.time()
        self.events = [(t - 1.0, "open")] + [(t - 0.5, e) for e in events]

    def send(self, line):
        self.jam.feed(line + "\n", time.time())

    def wait(self, pred, timeout, since):
        for t, _txt, p in self.jam.out:
            if t >= since and p is not None and pred(p):
                return t, p
        return None


if __name__ == "__main__":
    sys.exit(main())
