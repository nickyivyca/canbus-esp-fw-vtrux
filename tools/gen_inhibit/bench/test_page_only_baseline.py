"""Harness test for page_only_baseline: outage grouping, WLAN parsing, judge.

A reboot without a console must show as exactly one outage whether the sign is
an unanswered page, a dropped WLAN, or both in sequence; two separate drops are
two outages; a clean run is no outage; a poll not in mode 0 is reported. Also
--power is required.

  py -3.14 tools/gen_inhibit/bench/test_page_only_baseline.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import page_only_baseline as P                                  # noqa: E402


def S(t, answered=True, wlan=True, mode=0, dur=0.05, port=None, up=None, why="SW",
      has=True):
    s = {"t": t, "answered": answered, "wlan_ok": wlan, "mode": mode if answered else None,
         "dur": dur, "port": port}
    if answered and up is not None:
        s.update({"has_uptime": has, "has_reason": has, "uptime_ms": up if has else None,
                  "reset_reason": why if has else None})
    return s


def U(ts, base_ms=100000, why="SW", rate=1.0, dur=0.05):
    """Answered polls at wall times ts whose uptime advances at `rate` from base_ms."""
    return [S(t, up=int(base_ms + (t - ts[0]) * 1000 * rate), why=why, dur=dur) for t in ts]


def uptime_cases():
    C = P.uptime_check
    steady = U(range(0, 60))
    rb = U(range(0, 30)) + [S(30, dur=7.0, up=1500, why="USB")] + U(range(38, 60), 9500, "USB")
    between = [S(0, up=200, why="SW"), S(1, up=700, why="POWERON")]
    slow = U(range(0, 60), rate=0.5)
    out = [
        ("uptime: steady rise is clean, one boot, rate ~1",
         C(steady)["clean"] and len(C(steady)["boots"]) == 1
         and abs(C(steady)["boots"][0]["rate"] - 1.0) < 0.001),
        ("uptime: a drop is 1 reboot carrying the new boot's reason",
         [(x["t"], x["reason"], x["prev_reason"]) for x in C(rb)["reboots"]] == [(30, "USB", "SW")]
         and len(C(rb)["boots"]) == 2 and C(rb)["behind"] == [] and C(rb)["ahead"] == []),
        ("uptime: a reboot while uptime < the poll gap shows as BEHIND, not missed",
         C(between)["reboots"] == [] and len(C(between)["behind"]) == 1
         and not C(between)["clean"]),
        ("uptime: a clock running at half rate is flagged (behind) and its rate ~0.5",
         len(C(slow)["behind"]) > 0 and abs(C(slow)["boots"][0]["rate"] - 0.5) < 0.01),
        ("uptime: a stuck (equal) uptime is flagged",
         len(C(U(range(0, 5)) + [S(5, up=104000)])["behind"]) == 1),
        ("uptime: fields absent are MISSING",
         C([S(0, up=1, has=False), S(1, up=1, has=False)])["missing"] == [0, 1]),
        ("uptime: a pre-cc34c80 page (no keys at all) is MISSING",
         C([S(0), S(1)])["missing"] == [0, 1]),
        ("uptime: a reason as a JSON integer is bad",
         C(U(range(0, 3), why=99))["bad_reason"] != []),
        ("uptime: reason '99' (string) and every IDF name are good",
         all(P.reason_ok(x) for x in ("99",) + P.RESET_NAMES)),
        ("uptime: reason 'ESP_RST_SW' (prefix kept) or '' is bad",
         not P.reason_ok("ESP_RST_SW") and not P.reason_ok("")),
        ("uptime: uptime as a string or a float or a bool is bad",
         not P.uptime_ok("1000") and not P.uptime_ok(1000.0) and not P.uptime_ok(True)
         and P.uptime_ok(0) and P.uptime_ok(2 ** 40)),
        ("uptime: reason changing within a boot is flagged",
         len(C(U(range(0, 3)) + [S(3, up=103000, why="PANIC")])["reason_changed"]) == 1),
        ("uptime: unanswered polls are skipped, not a reboot",
         C(U(range(0, 5)) + [S(5, answered=False)] + U(range(6, 9), base_ms=106000))["clean"]),
        ("agree: the 7 s stall reboot is covered by its outage",
         P.agree(P.outages(rb), C(rb))["agree"]),
        ("agree: a reboot with no stall is a reboot the stall detector missed, so DISAGREE",
         (lambda g: g["reboots_stall_missed"] == [10] and g["agree"] is False)(
             P.agree(P.outages(U(range(0, 10)) + U(range(10, 20), base_ms=500)),
                     C(U(range(0, 10)) + U(range(10, 20), base_ms=500))))),
        ("agree: a stall with no uptime drop is an outage that was not a reboot",
         len(P.agree(P.outages(U(range(0, 10)) + [S(10, dur=5.0, up=110000)] + U(range(15, 20), base_ms=115000)),
                     C(U(range(0, 10)) + [S(10, dur=5.0, up=110000)] + U(range(15, 20), base_ms=115000)))
             ["outages_not_reboot"]) == 1),
        ("uptime: no answered poll is NOT clean, and the verdict says no readings",
         C([S(0, answered=False), S(1, answered=False)])["clean"] is False
         and "NO UPTIME READINGS" in P.judge([S(0, answered=False)])["uptime_verdict"]),
        ("uptime: a boot first read by a 7 s stalled poll (answered at its end) still rates ~1",
         (lambda b: abs(b["rate"] - 1.0) < 0.001 and b["rate_readings"] == 60)(
             C(U(range(0, 10)) + [S(10, dur=7.0, up=7000, why="USB")]
               + U(range(18, 78), base_ms=8025, why="USB"))["boots"][1])),
        ("judge carries the uptime verdict",
         P.judge(rb)["uptime"]["reboots"] != [] and "1 reboot" in P.judge(rb)["uptime_verdict"]),
    ]
    return out


NETSH_UP = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : AP-X
    BSSID                  : 00:00:5e:00:53:02
"""
NETSH_OTHER = NETSH_UP.replace("AP-X", "AP-Y")
NETSH_DOWN = """
    Name                   : Wi-Fi
    State                  : disconnected
"""


def main():
    clean = [S(t) for t in range(10)]
    reboot_page = [S(0), S(1), S(2, answered=False), S(3, answered=False), S(4)]
    reboot_mixed = [S(0), S(1, answered=False), S(2, answered=False, wlan=False),
                    S(3, answered=False, wlan=False), S(4, answered=False), S(5)]
    two = [S(0), S(1, wlan=False, answered=False), S(2), S(3), S(4, answered=False), S(5)]
    odd = [S(0), S(1, mode=3), S(2)]
    checks = [
        ("clean run: no outage", P.judge(clean)["outages"] == []
         and P.judge(clean)["verdict"] == "no outage"),
        ("page-only reboot: 1 outage of 2 polls",
         [(o["start"], o["polls"]) for o in P.outages(reboot_page)] == [(2, 2)]),
        ("page then WLAN then page: 1 outage, not 3", len(P.outages(reboot_mixed)) == 1
         and P.outages(reboot_mixed)[0]["polls"] == 4
         and P.outages(reboot_mixed)[0]["wlan_down"] == 2),
        ("two separate drops: 2 outages", len(P.outages(two)) == 2),
        ("WLAN drop alone counts", len(P.outages([S(0), S(1, wlan=False), S(2)])) == 1),
        ("the 11:26 positive control: one 7.4 s stalled poll = 1 outage",
         len(P.outages([S(29.0), S(30.0, dur=7.4), S(38.4), S(39.4)])) == 1),
        ("a 2.9 s poll is not a stall (default 3 s)",
         P.outages([S(0), S(1, dur=2.9), S(4)]) == []),
        ("unplug stamped at the first poll without the port",
         P.port_gone_at([S(0, port=True), S(1, port=True), S(2, port=False), S(3, port=False)]) == 2),
        ("port never seen -> no unplug", P.port_gone_at([S(0, port=False), S(1, port=False)]) is None),
        ("outage at the unplug and one later are split",
         (lambda u: len(u["outages_at_unplug"]) == 1 and len(u["outages_after"]) == 1
          and len(u["outages_before"]) == 0)(P.judge(
              [S(t, port=(t < 30)) for t in range(0, 30)]
              + [S(30, port=False, dur=7.0)]
              + [S(t, port=False) for t in range(38, 100)]
              + [S(100, port=False, answered=False)]
              + [S(t, port=False) for t in range(101, 400)])["unplug"])),
        ("not mode 0 reported", P.judge(odd)["not_mode0_at"] == [1]
         and P.judge(odd)["verdict"] != "no outage"),
        ("netsh connected to the SSID -> ok", P.parse_wlan(NETSH_UP, "AP-X") == (True, "connected")),
        ("netsh connected to ANOTHER SSID -> not ok", P.parse_wlan(NETSH_OTHER, "AP-X")[0] is False),
        ("netsh disconnected -> not ok", P.parse_wlan(NETSH_DOWN, "AP-X") == (False, "disconnected")),
        ("BSSID line is not read as SSID", P.parse_wlan(NETSH_UP, "00:00:5e:00:53:02")[0] is False),
    ] + uptime_cases()
    try:
        P.build_parser().parse_args([])
        checks.append(("--power required", False))
    except SystemExit:
        checks.append(("--power required", True))
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
