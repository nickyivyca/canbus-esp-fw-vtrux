"""Harness test for page_only_baseline: outage grouping, the WLAN read and rejoin, judge.

A reboot without a console must show as exactly one outage whether the sign is
an unanswered page, a dropped WLAN, or both in sequence; two separate drops are
two outages; a clean run is no outage; a poll not in mode 0 is reported. The
WLAN signal and rates are read as numbers only, never from the SSID or BSSID
lines, and are logged without changing the verdict. Also --power is required.

The WLAN read and the rejoin (2026-11-16, the gen-inhibit reviewer's job (1): every rejoin
through the core's vtrux_bench.run.wifi) go through gi_wifi with the backend settings.ota
chooses, here a stand-in backend: on the hotspot only when connected AND on the WiCAN's
SSID, read from the configured interface; a backend that cannot be read is not on it and
says why; the SSID never in a state; the link fields None; the rejoin selects the network
once through the backend, with the settings' profile and interface, and does not wait (no
read); its failure is kept, masked. Without GI_OTA_SETTINGS the tool refuses, exit 2, no
record. Until 2026-11-16 this read netsh and nmcli output itself.

  PYTHONPATH=<core 2026.11.16.5>/shared/bench \
  python3.13 gen-inhibit/tools/bench/test_page_only_baseline.py [--mutants]

--mutants: each mutant of page_only_baseline.py's WLAN read and rejoin is a copy in this run's
scratch/ folder with one edit, run through GI_PAGE_ONLY_DIR; a check must fail on each,
and the unmutated copy, the control, must pass.
"""
import os
import subprocess
import sys
# no bytecode anywhere below this process: a child that imports the pinned core must not write
# __pycache__ into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

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


NONE = {"wlan_signal": None, "wlan_rx_mbps": None, "wlan_tx_mbps": None}
CFG = {"host": "192.168.80.1", "iface": "wlan0", "profile": "79", "ssid": "AP-X", "wifi": "adb",
       "capture": False, "capture_iface": "wlan0", "echo": False, "phone": {"serial": "x", "adb": "adb"}}


def link_cases():
    sig = [S(0), S(1, answered=False), S(2, answered=False), S(3)]
    for s, v in zip(sig, (10, 40, 30, 5)):
        s.update({"wlan_signal": v, "wlan_rx_mbps": v * 2.0})
    o = P.outages(sig)
    low = [dict(S(t), wlan_signal=1) for t in range(5)]
    return [
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


class FakeBe:
    """A Wi-Fi backend's read() and connect(), as gi_wifi calls them."""
    name = "fake"

    def __init__(self, reads=(), connect=(True, "asked to join AP-X")):
        self.reads, self.ret = list(reads), connect
        self.read_ifaces, self.connects = [], []

    def read(self, iface):
        self.read_ifaces.append(iface)
        r = self.reads.pop(0) if self.reads else (None, None, False)
        if isinstance(r, Exception):
            raise r
        return r

    def connect(self, profile, iface):
        self.connects.append((profile, iface))
        return self.ret


def call(fn):
    """fn(), or the exception it raised: a raise is judged by the check, not a crash."""
    try:
        return fn()
    except Exception as e:      # noqa: BLE001
        return e


def refused_without_settings():
    """The tool run with no GI_OTA_SETTINGS: (exit, stdout, files left in its runs folder)."""
    import gi_paths
    d = gi_paths.scratch("test-page_only_baseline", start=__file__)
    env = {k: v for k, v in os.environ.items() if k != "GI_OTA_SETTINGS"}
    env.update(GI_WICAN_SSID="AP-X", GI_RUNS_DIR=d)
    # a mutant copy runs from its own folder: the harness beside this test stays importable
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [x for x in [env.get("PYTHONPATH")] if x])
    r = subprocess.run([sys.executable, "-B", os.path.join(P.HERE, "page_only_baseline.py"), "--power", "test",
                        "--seconds", "1"], capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, r.stdout, os.listdir(d)


def wifi_cases():
    on = FakeBe([("AP-X", "connected", True)])
    r_on = call(lambda: P.read_wlan(CFG, on))
    other = call(lambda: P.read_wlan(CFG, FakeBe([("AP-Y", "connected", True)])))
    noaddr = call(lambda: P.read_wlan(CFG, FakeBe([("AP-X", "no address yet", False)])))
    gone = call(lambda: P.read_wlan(CFG, FakeBe([OSError("adb gone")])))
    nothing = call(lambda: P.read_wlan(CFG, FakeBe([(None, "disconnected", False)])))
    rj = FakeBe(connect=(True, "the phone asked to join its saved network 79; route via AP-X"))
    r_rj = call(lambda: P.rejoin(CFG, rj))
    r_bad = call(lambda: P.rejoin(CFG, FakeBe(connect=(False, "root denied"))))
    states = [x[1] for x in (r_on, other, noaddr, gone, nothing) if isinstance(x, tuple)]
    rc, out, left = refused_without_settings()
    return [
        ("wifi: connected and on the WiCAN's SSID -> on the hotspot, link fields None",
         isinstance(r_on, tuple) and r_on[0] is True and r_on[2] == NONE),
        ("wifi: read from the settings' interface", on.read_ifaces == ["wlan0"]),
        ("wifi: connected to ANOTHER SSID -> not on it", isinstance(other, tuple) and other[0] is False),
        ("wifi: on the SSID but not connected (no address yet) -> not on it",
         isinstance(noaddr, tuple) and noaddr[0] is False and "no address yet" in noaddr[1]),
        ("wifi: a backend that cannot be read -> not on it, said, no raise",
         isinstance(gone, tuple) and gone[0] is False and "OSError" in gone[1]),
        ("wifi: no network -> not on it", isinstance(nothing, tuple) and nothing[0] is False),
        ("wifi: the SSID is in no state", len(states) == 5 and not any("AP-X" in st for st in states)),
        ("rejoin: one select through the backend, the settings' profile and interface",
         rj.connects == [("79", "wlan0")]),
        ("rejoin: does not wait (no read), and its text is masked",
         rj.read_ifaces == [] and isinstance(r_rj, tuple) and r_rj[0] is True
         and "AP-X" not in r_rj[1] and "<WICAN_SSID>" in r_rj[1]),
        ("rejoin: a failed select is kept as failed, with its reason",
         r_bad == (False, "root denied")),
        ("rejoin: the record's event names the backend and keeps a failure as FAILED",
         P.rejoin_note(CFG, False, "root denied") == "rejoin (adb backend) FAILED: root denied"
         and P.rejoin_note(CFG, True, "asked") == "rejoin (adb backend) ok: asked"),
        ("no GI_OTA_SETTINGS: refused, exit 2, no record",
         rc == 2 and "REFUSED" in out and "GI_OTA_SETTINGS" in out and left == []),
    ]


MUTANTS = [
    ("the backend's read not used", "    on, state = GW.state(cfg, be)\n", "    on, state = True, \"connected\"\n"),
    ("the rejoin waits for the join", "    return GW.select(cfg, be)\n", "    return GW.join(cfg, be=be)\n"),
    ("the rejoin not made", "    return GW.select(cfg, be)\n", "    return True, \"skipped\"\n"),
    ("no settings goes on", '''        print("REFUSED: %s (nothing run, no record)" % e)
        return 2
    import lowid_flood_skip_bench''', '''        wcfg, wbe = {"wifi": "none"}, None
    import lowid_flood_skip_bench'''),
    ("a rejoin's failure recorded as ok", '"ok" if ok else "FAILED", why', '"ok", why'),
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
    ] + uptime_cases() + link_cases() + wifi_cases()
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
