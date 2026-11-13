"""Harness test for page_only_baseline: outage grouping, WLAN parsing, judge.

A reboot without a console must show as exactly one outage whether the sign is
an unanswered page, a dropped WLAN, or both in sequence; two separate drops are
two outages; a clean run is no outage; a poll not in mode 0 is reported. The
WLAN signal and rates are read as numbers only, never from the SSID or BSSID
lines, and are logged without changing the verdict. Also --power is required.

Linux (2026-11-12, the reviewer's Linux leftovers item 6): the WLAN is read with nmcli's
terse output (made-up lines in its documented form; madhouse-debian has no Wi-Fi adapter
yet). Connected only with exactly one active access point whose SSID is the WiCAN's; an
escaped ':' in an SSID is part of it; the signal is the active line's, the rates None; a
reader that can't run or exits non-zero is not connected and says why; Windows still
reads netsh; the rejoin goes through the stored profile, nmcli on Linux.

  python3.13 gen-inhibit/tools/bench/test_page_only_baseline.py [--mutants]

--mutants: each mutant of page_only_baseline.py's WLAN reading is a copy in this run's
scratch/ folder with one edit, run through GI_PAGE_ONLY_DIR; a check must fail on each,
and the unmutated copy, the control, must pass.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("GI_PAGE_ONLY_DIR") or HERE)
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
NETSH_LINK = NETSH_UP + """    Radio type             : 802.11n
    Receive rate (Mbps)    : 144.4
    Transmit rate (Mbps)   : 130
    Signal                 : 87%
"""
# SSID and BSSID values shaped like the numbers read: neither may land in the result.
NETSH_DECOY = """
    State                  : connected
    SSID                   : 55%
    BSSID                  : 77
    Signal                 : strong
"""


def link_cases():
    L = P.parse_wlan_link
    sig = [S(0), S(1, answered=False), S(2, answered=False), S(3)]
    for s, v in zip(sig, (10, 40, 30, 5)):
        s.update({"wlan_signal": v, "wlan_rx_mbps": v * 2.0})
    o = P.outages(sig)
    none = {"wlan_signal": None, "wlan_rx_mbps": None, "wlan_tx_mbps": None}
    low = [dict(S(t), wlan_signal=1) for t in range(5)]
    return [
        ("link: signal, rx and tx read as numbers",
         L(NETSH_LINK) == {"wlan_signal": 87, "wlan_rx_mbps": 144.4, "wlan_tx_mbps": 130.0}),
        ("link: disconnected output gives None for each", L(NETSH_DOWN) == none),
        ("link: SSID/BSSID lines and a non-number signal give nothing", L(NETSH_DECOY) == none),
        ("link: the outage's min is over its own polls only",
         [(x["min_signal"], x["min_rx_mbps"]) for x in o] == [(30, 60.0)]),
        ("link: an outage with no link readings has None",
         P.outages([S(0, answered=False)])[0]["min_signal"] is None),
        ("link: summary min/median/polls, unread polls left out",
         P.link_summary(sig + [S(4)])["wlan_signal"] == {"polls": 4, "min": 5, "median": 30}
         and P.link_summary([S(0)])["wlan_tx_mbps"] == {"polls": 0, "min": None, "median": None}),
        ("link: logged, not judged (signal 1 % still no outage)",
         P.judge(low)["verdict"] == "no outage" and P.judge(low)["wlan_link"]["wlan_signal"]["min"] == 1),
    ]


# nmcli -t -e yes -f ACTIVE,SSID,SIGNAL device wifi list --rescan no (made-up names)
NM_UP = "no:Neighbour:40\nyes:AP-X:71\nno:AP-X-5G:20\n"
NM_OTHER = "yes:Neighbour:80\nno:AP-X:60\n"
NM_DOWN = "no:AP-X:55\nno:Neighbour:30\n"
NM_TWO = "yes:AP-X:71\nyes:Neighbour:40\n"
NM_COLON = "yes:AP\\:X:66\n"               # an SSID with a ':' in it, escaped by nmcli
NM_ODD = "yes:AP-X:--\n"


class FakeRun:
    def __init__(self, out="", rc=0, raise_=None):
        self.out, self.rc, self.raise_, self.argv = out, rc, raise_, None

    def __call__(self, argv, **kw):
        self.argv = argv
        if self.raise_:
            raise self.raise_
        return subprocess.CompletedProcess(argv, self.rc, self.out, "")


def call(fn):
    """fn(), or the exception it raised: a raise is judged by the check, not a crash."""
    try:
        return fn()
    except Exception as e:      # noqa: BLE001
        return e


def nmcli_cases():
    none = {"wlan_signal": None, "wlan_rx_mbps": None, "wlan_tx_mbps": None}
    win = FakeRun(NETSH_LINK)
    lin = FakeRun(NM_UP)
    rw = P.read_wlan("AP-X", run=lin, platform="linux")
    rww = P.read_wlan("AP-X", run=win, platform="win32")
    return [
        ("nmcli: one active AP with the SSID -> connected", P.parse_nmcli(NM_UP, "AP-X") == (True, "connected")),
        ("nmcli: the active AP is ANOTHER SSID -> not ok", P.parse_nmcli(NM_OTHER, "AP-X") == (False, "connected")),
        ("nmcli: the SSID listed but not active -> not ok", P.parse_nmcli(NM_DOWN, "AP-X") == (False, "disconnected")),
        ("nmcli: two active lines -> not ok, said", P.parse_nmcli(NM_TWO, "AP-X") == (False, "2 active")),
        ("nmcli: an escaped ':' is part of the SSID", P.parse_nmcli(NM_COLON, "AP:X")[0] is True
         and P.parse_nmcli(NM_COLON, "AP")[0] is False),
        ("nmcli: a prefix of the SSID is not it", P.parse_nmcli(NM_UP, "AP")[0] is False),
        ("nmcli link: the active line's signal, rates None",
         P.parse_nmcli_link(NM_UP) == dict(none, wlan_signal=71)),
        ("nmcli link: none active, or two, or a non-number -> all None",
         P.parse_nmcli_link(NM_DOWN) == none and P.parse_nmcli_link(NM_TWO) == none
         and P.parse_nmcli_link(NM_ODD) == none),
        ("read_wlan on Linux runs nmcli's terse ACTIVE,SSID,SIGNAL list, no rescan, and parses it",
         lin.argv == P.WLAN_LINUX and lin.argv[0] == "nmcli" and "-t" in lin.argv and "--rescan" in lin.argv
         and rw == (True, "connected", dict(none, wlan_signal=71))),
        ("read_wlan on Windows runs netsh, as before",
         win.argv == ["netsh", "wlan", "show", "interfaces"] and rww[:2] == (True, "connected")
         and rww[2]["wlan_signal"] == 87),
        ("read_wlan: nmcli missing -> not connected, said, no raise",
         call(lambda: P.read_wlan("AP-X", run=FakeRun(raise_=FileNotFoundError("nmcli")), platform="linux"))
         == (False, "nmcli failed (FileNotFoundError)", none)),
        ("read_wlan: nmcli exiting non-zero -> not connected, with its code, even with output",
         P.read_wlan("AP-X", run=FakeRun(NM_UP, rc=8), platform="linux") == (False, "nmcli exit 8", none)),
        ("read_wlan: a timeout -> not connected, said",
         call(lambda: P.read_wlan("AP-X", run=FakeRun(raise_=subprocess.TimeoutExpired("nmcli", 10)),
                                  platform="linux")[:2]) == (False, "nmcli failed (TimeoutExpired)")),
        ("rejoin: nmcli through the stored profile on Linux, netsh on Windows",
         P.rejoin_argv("AP-X", platform="linux") == ["nmcli", "device", "wifi", "connect", "AP-X"]
         and P.rejoin_argv("AP-X", platform="win32") == ["netsh", "wlan", "connect", "name=AP-X"]),
    ]


MUTANTS = [
    ("an SSID's escaped ':' splits it", '        elif ch == "\\\\":\n            esc = True\n', ""),
    ("any line counts as active", 'if f[0] == "yes" and len(f) >= 3]', "if len(f) >= 3]"),
    ("two active lines read as connected", "    return (len(act) == 1 and ssid is not None and act[0][1] == ssid), state",
     "    return (len(act) >= 1 and ssid is not None and act[0][1] == ssid), state"),
    ("the SSID not compared", "and ssid is not None and act[0][1] == ssid), state", "and ssid is not None), state"),
    ("the signal not read", '        out["wlan_signal"] = int(act[0][2])\n', "        pass\n"),
    ("nmcli's exit code ignored", '    if r.returncode != 0:\n        return False, "nmcli exit %d"',
     '    if False:\n        return False, "nmcli exit %d"'),
    ("a reader that can't run raises", "    except (OSError, subprocess.SubprocessError) as e:\n        return False, \"%s failed",
     "    except ():\n        return False, \"%s failed"),
    ("Linux reads netsh", "    argv = WLAN_WINDOWS if win else WLAN_LINUX", "    argv = WLAN_WINDOWS"),
    ("Linux rejoins with netsh", '    if (platform or sys.platform) == "win32":\n        return ["netsh", "wlan", "connect"',
     '    if True:\n        return ["netsh", "wlan", "connect"'),
    ("nmcli rescans on every poll", '"list", "--rescan", "no"]', '"list"]'),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "page_only_baseline.py"), encoding="utf-8").read()
    base = gi_paths.scratch("mutants-page_only_baseline", start=__file__)
    killed = errors = 0
    control_ok = None
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, "page_only_baseline.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, GI_PAGE_ONLY_DIR=d), timeout=120)
        fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d)" % ("PASS    " if control_ok else "FAILED  ", name, r.returncode))
            if not control_ok:
                print("\nthe control FAILS: no mutant is judged")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (%d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, len(fails),
                                            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


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
    ] + uptime_cases() + link_cases() + nmcli_cases()
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
    sys.exit(mutants() if "--mutants" in sys.argv[1:] else main())
