"""The OTA flasher's Wi-Fi module (FLASH-SPEC O4, O4a; vtrux_bench.run.wifi):
each backend from recorded command output, through a fake runner. No Wi-Fi is
touched, no command but the fake runner's (and three harmless local processes
for run_cmd's own checks) is run.

Every fixture value is made up (the repo is public): SSIDs like
WiCAN_test_000000, locally administered MACs and BSSIDs (02:00:00:...), a
GUID of zeros, profile and interface names of our own. The netsh fixtures
have the layout of `netsh wlan show interfaces` on Windows 10; the nmcli ones
the documented terse (-t) formats of `nmcli -t -f GENERAL.STATE device show`
and `nmcli -t -f ACTIVE,SSID device wifi list`.

    py -3.14 -B shared/bench/tests/test_wifi.py             exit 0 only if every check passes
    py -3.14 -B shared/bench/tests/test_wifi.py --can-fail  the control, then every mutant
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import _ota_mutants                                  # noqa: E402
sys.path.insert(0, _ota_mutants.package_root())
from vtrux_bench.run import wifi as WF               # noqa: E402

FAILS = []
SSID = "WiCAN_test_000000"
OTHER = "home_test_net"
PROFILE = "wican-hotspot-test"
IFACE = "Wi-Fi"
SECRET_WORDS = ("psk", "password", "passphrase", "secret", "--ask", "key=clear")
ALL_ARGV = []           # every argv any fake runner saw, for the passphrase check


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL (CRASH)."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not ok:
        FAILS.append(label)


def netsh_block(name, state, ssid=None, ap_bssid_first=False, guid_tail="0"):
    lines = ["    Name                   : %s" % name,
             "    Description            : Made-up Wireless Adapter",
             "    GUID                   : 00000000-0000-0000-0000-00000000000%s" % guid_tail,
             "    Physical address       : 02:00:00:00:00:0%s" % guid_tail,
             "    State                  : %s" % state]
    if ssid is not None:
        if ap_bssid_first:
            lines.append("    AP BSSID               : 02:00:00:00:01:0%s" % guid_tail)
        lines += ["    SSID                   : %s" % ssid,
                  "    BSSID                  : 02:00:00:00:01:0%s" % guid_tail,
                  "    Network type           : Infrastructure",
                  "    Radio type             : 802.11n",
                  "    Authentication         : WPA2-Personal",
                  "    Cipher                 : CCMP",
                  "    Connection mode        : Profile",
                  "    Channel                : 6",
                  "    Receive rate (Mbps)    : 72",
                  "    Transmit rate (Mbps)   : 72",
                  "    Signal                 : 90%",
                  "    Profile                : %s" % PROFILE]
    return "\n".join(lines) + "\n"


def netsh_out(*blocks):
    n = len(blocks)
    head = "\nThere %s %d interface%s on the system: \n\n" % ("is" if n == 1 else "are", n, "" if n == 1 else "s")
    return head + "\n".join(blocks) + "\n    Hosted network status  : Not available\n\n"


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Runner:
    """A fake runner: answers by the command's kind from `answers`, a dict of
    kind -> (rc, text) or a list of them (taken in turn, the last repeating)."""

    def __init__(self, answers, clock=None):
        self.answers, self.calls, self.clock = answers, [], clock

    def kind(self, argv):
        if argv[0] == "netsh":
            return "connect" if argv[2] == "connect" else "show"
        if argv[1:3] == ["connection", "up"]:
            return "up"
        return "state" if "show" in argv else "ssid"

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        ALL_ARGV.append(list(argv))
        a = self.answers[self.kind(argv)]
        if isinstance(a, list):
            a = a.pop(0) if len(a) > 1 else a[0]
        if self.clock is not None:
            self.clock.t += 0.1
        return a


def nm_state(code, text):
    return (0, "GENERAL.STATE:%d (%s)\n" % (code, text))


def nm_list(*rows):
    return (0, "".join("%s:%s\n" % r for r in rows))


def main():
    # -- netsh: reading ---------------------------------------------------------------
    one = netsh_out(netsh_block(IFACE, "connected", SSID))
    be = WF.Netsh(Runner({"show": (0, one)}))
    check("netsh: connected and on the SSID is on it", lambda: WF.on_ssid(IFACE, SSID, be) is True)
    check("netsh: current() reads the SSID and the state", lambda: WF.current(IFACE, be) == (SSID, "connected"),
          WF.current(IFACE, be))
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block(IFACE, "connected", OTHER)))}))
    check("netsh: connected to another SSID is NOT on it", lambda: WF.on_ssid(IFACE, SSID, be) is False)
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block(IFACE, "disconnected")))}))
    check("netsh: disconnected has no SSID", lambda: WF.current(IFACE, be) == (None, "disconnected"),
          WF.current(IFACE, be))
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block(IFACE, "associating", SSID)))}))
    check("netsh: on the SSID but not yet connected is NOT on it", lambda: WF.on_ssid(IFACE, SSID, be) is False)
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block(IFACE, "Connected", SSID)))}))
    check("netsh: the state is compared case-insensitively, as join_ap did",
          lambda: WF.on_ssid(IFACE, SSID, be) is True)
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block(IFACE, "connected", SSID, ap_bssid_first=True)))}))
    check("netsh: BSSID and AP BSSID lines are not the SSID", lambda: WF.current(IFACE, be)[0] == SSID,
          WF.current(IFACE, be))
    two = netsh_out(netsh_block("Wi-Fi 2", "connected", SSID, guid_tail="1"),
                    netsh_block(IFACE, "connected", OTHER, guid_tail="2"))
    be = WF.Netsh(Runner({"show": (0, two)}))
    check("netsh: the named interface's block, not the first", lambda: WF.current(IFACE, be) == (OTHER, "connected")
          and WF.on_ssid(IFACE, SSID, be) is False, WF.current(IFACE, be))
    be = WF.Netsh(Runner({"show": (0, netsh_out(netsh_block("Wi-Fi 2", "connected", SSID)))}))
    check("netsh: no block of that name is no network", lambda: WF.current(IFACE, be) == (None, None)
          and not WF.on_ssid(IFACE, SSID, be), WF.current(IFACE, be))
    be = WF.Netsh(Runner({"show": (1, "The Wireless AutoConfig Service (wlansvc) is not running.\n")}))
    check("netsh: a failed read is no network", lambda: WF.current(IFACE, be) == (None, None))

    # -- netsh: joining -------------------------------------------------------------------
    clk = Clock()
    r = Runner({"connect": (0, "Connection request was completed successfully.\n"),
                "show": [(0, netsh_out(netsh_block(IFACE, "disconnected")))] * 3
                + [(0, netsh_out(netsh_block(IFACE, "connected", SSID)))]}, clk)
    ok, why = WF.join(PROFILE, IFACE, SSID, be=WF.Netsh(r), now=clk.now, sleep=clk.sleep)
    check("netsh join: the saved profile on the interface, then polled until on the SSID",
          lambda: ok and r.calls[0][0] == ["netsh", "wlan", "connect", "name=%s" % PROFILE, "interface=%s" % IFACE]
          and [c[0] for c in r.calls[1:]] == [["netsh", "wlan", "show", "interfaces"]] * 4, (ok, why, r.calls))
    check("netsh join: polled every 1 s", lambda: clk.sleeps == [1.0, 1.0, 1.0], clk.sleeps)
    check("netsh: every call under a 45 s timeout", lambda: r.calls and all(t == 45 for _a, t in r.calls),
          [t for _a, t in r.calls])
    clk = Clock()
    r = Runner({"connect": (0, ""), "show": (0, netsh_out(netsh_block(IFACE, "connected", OTHER)))}, clk)
    ok, why = WF.join(PROFILE, IFACE, SSID, be=WF.Netsh(r), now=clk.now, sleep=clk.sleep)
    check("netsh join: connected to another SSID for 40 s is not joined", lambda: not ok and "40 s" in why
          and OTHER in why, why)
    check("netsh join: it gave up at 40 s, not before or long after",
          lambda: 39.0 <= clk.t - 1000.0 <= 42.5 and 35 <= len(clk.sleeps) <= 40, (clk.t - 1000.0, len(clk.sleeps)))
    clk = Clock()
    r = Runner({"connect": (1, "There is no profile \"x\" assigned to the specified interface.\n"),
                "show": (0, one)}, clk)
    ok, why = WF.join(PROFILE, IFACE, SSID, be=WF.Netsh(r), now=clk.now, sleep=clk.sleep)
    check("netsh join: a failed connect is not joined, and nothing is polled",
          lambda: not ok and "netsh wlan connect failed" in why and len(r.calls) == 1, (why, r.calls))
    clk = Clock()
    r = Runner({"connect": (0, ""), "show": (0, netsh_out(netsh_block(IFACE, "disconnected")))}, clk)
    ok, why = WF.rejoin(PROFILE, IFACE, SSID, be=WF.Netsh(r), now=clk.now, sleep=clk.sleep)
    check("rejoin: the watch's 20 s", lambda: not ok and 19.0 <= clk.t - 1000.0 <= 22.5 and "20 s" in why,
          (clk.t - 1000.0, why))

    # -- nmcli: reading ---------------------------------------------------------------------
    r = Runner({"state": nm_state(100, "connected"), "ssid": nm_list(("no", OTHER), ("yes", SSID))})
    be = WF.Nmcli(r)
    check("nmcli: state 100 and the active line on the SSID is on it", lambda: WF.on_ssid("wlan0", SSID, be) is True)
    check("nmcli: current() reads the active network's SSID", lambda: WF.current("wlan0", be) == (SSID, "connected"),
          WF.current("wlan0", be))
    check("nmcli: reads in terse mode on the interface, not rescanning",
          lambda: ["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", "wlan0"] in [c[0] for c in r.calls]
          and ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "ifname", "wlan0", "--rescan", "no"]
          in [c[0] for c in r.calls], r.calls)
    be = WF.Nmcli(Runner({"state": nm_state(100, "connected"), "ssid": nm_list(("yes", OTHER), ("no", SSID))}))
    check("nmcli: connected to another SSID is NOT on it", lambda: WF.on_ssid("wlan0", SSID, be) is False)
    be = WF.Nmcli(Runner({"state": nm_state(30, "disconnected"), "ssid": nm_list(("yes", SSID))}))
    check("nmcli: the SSID listed active but the device not activated is NOT on it",
          lambda: WF.on_ssid("wlan0", SSID, be) is False and WF.current("wlan0", be)[1] == "disconnected",
          WF.current("wlan0", be))
    be = WF.Nmcli(Runner({"state": nm_state(100, "verbunden"), "ssid": nm_list(("yes", SSID))}))
    check("nmcli: connected is state 100, whatever the text says", lambda: WF.on_ssid("wlan0", SSID, be) is True)
    be = WF.Nmcli(Runner({"state": nm_state(70, "connecting (getting IP configuration)"), "ssid": nm_list(("yes", SSID))}))
    check("nmcli: still getting an address is NOT on it", lambda: WF.on_ssid("wlan0", SSID, be) is False)
    be = WF.Nmcli(Runner({"state": nm_state(100, "connected"), "ssid": (0, "no:%s\nyes:odd\\:name\\\\x\n" % OTHER)}))
    check("nmcli: terse escapes in the SSID are undone", lambda: WF.current("wlan0", be)[0] == "odd:name\\x",
          WF.current("wlan0", be))
    be = WF.Nmcli(Runner({"state": (10, "Error: Device 'wlan9' not found.\n"), "ssid": (10, "Error: x\n")}))
    check("nmcli: a failed read is no network", lambda: WF.current("wlan9", be) == (None, None)
          and not WF.on_ssid("wlan9", SSID, be), WF.current("wlan9", be))

    # -- nmcli: joining ---------------------------------------------------------------------
    clk = Clock()
    r = Runner({"up": (0, "Connection successfully activated (D-Bus active path: /x/1)\n"),
                "state": [nm_state(50, "connecting (configuring)")] * 2 + [nm_state(100, "connected")],
                "ssid": nm_list(("yes", SSID))}, clk)
    ok, why = WF.join(PROFILE, "wlan0", SSID, be=WF.Nmcli(r), now=clk.now, sleep=clk.sleep)
    check("nmcli join: the saved profile brought up by its id, then polled",
          lambda: ok and r.calls[0][0] == ["nmcli", "connection", "up", "id", PROFILE] and clk.sleeps == [1.0, 1.0],
          (ok, why, r.calls[:2], clk.sleeps))
    check("nmcli: every call under a 45 s timeout", lambda: all(t == 45 for _a, t in r.calls), r.calls)
    clk = Clock()
    r = Runner({"up": (4, "Error: Connection activation failed: Secrets were required, but not provided.\n"),
                "state": nm_state(100, "connected"), "ssid": nm_list(("yes", SSID))}, clk)
    ok, why = WF.join(PROFILE, "wlan0", SSID, be=WF.Nmcli(r), now=clk.now, sleep=clk.sleep)
    check("nmcli join: a failed activation is not joined (no secret is supplied), nothing polled",
          lambda: not ok and "nmcli connection up failed" in why and len(r.calls) == 1, (why, r.calls))
    clk = Clock()
    r = Runner({"up": (0, ""), "state": nm_state(100, "connected"), "ssid": nm_list(("yes", OTHER))}, clk)
    ok, why = WF.join(PROFILE, "wlan0", SSID, be=WF.Nmcli(r), now=clk.now, sleep=clk.sleep)
    check("nmcli join: connected to another SSID is not joined, at 40 s", lambda: not ok and "40 s" in why
          and 39.0 <= clk.t - 1000.0 <= 42.5, (why, clk.t - 1000.0))

    # -- the backend, chosen by the system in one place ------------------------------------
    check("backend: Windows is netsh", lambda: isinstance(WF.backend_for("Windows"), WF.Netsh))
    check("backend: Linux is nmcli", lambda: isinstance(WF.backend_for("Linux"), WF.Nmcli))
    try:
        WF.backend_for("Darwin")
        other = "accepted"
    except WF.WifiError as e:
        other = "refused: %s" % e
    except Exception as e:      # noqa: BLE001
        other = "raised %r" % e
    check("backend: any other system is refused", lambda: other.startswith("refused"), other)
    real = WF.platform.system
    try:
        WF.platform.system = lambda: "Linux"
        lin = WF.backend()
        WF.platform.system = lambda: "Windows"
        win = WF.backend()
    finally:
        WF.platform.system = real
    check("backend(): from the host's system", lambda: isinstance(lin, WF.Nmcli) and isinstance(win, WF.Netsh),
          (lin, win))
    check("backend(): its runner is the real one unless one is given",
          lambda: WF.backend_for("Windows").run is WF.run_cmd and WF.backend_for("Linux").run is WF._nm_run)
    pkg = os.path.join(_ota_mutants.package_root(), "vtrux_bench", "run")
    sysref = re.compile(r"\b(platform\.system|sys\.platform|os\.name|os_name)\b")

    def refs(name):
        with open(os.path.join(pkg, name), encoding="utf-8") as f:
            return [ln.strip() for ln in f if sysref.search(ln) and not ln.strip().startswith("#")]
    check("one place: the flasher and the HTTP helpers never look at the system",
          lambda: refs("ota.py") == [] and refs("wican_http.py") == [], refs("ota.py") + refs("wican_http.py"))
    check("one place: the Wi-Fi module looks at it in backend() alone",
          lambda: refs("wifi.py") == ["return backend_for(platform.system())"], refs("wifi.py"))
    check("one place: the capture looks at it to stop, alone",
          lambda: refs("ota_wire.py") == ['def stop_signal(os_name=None):',
                                          'if (os.name if os_name is None else os_name) == "nt":'],
          refs("ota_wire.py"))

    # -- no passphrase ----------------------------------------------------------------------
    words = [w for a in ALL_ARGV for w in SECRET_WORDS if w in " ".join(a).lower()]
    check("no command any backend ran names a secret", lambda: ALL_ARGV and not words, words)
    check("the shown-secrets option appears nowhere in the module",
          lambda: "show-secrets" not in open(os.path.join(pkg, "wifi.py"), encoding="utf-8").read()
          .split('"""', 2)[2])

    # -- the real runner -------------------------------------------------------------------
    try:
        got = WF.run_cmd(["no-such-program-ota-test"], 10)
    except Exception as e:      # noqa: BLE001
        got = "raised %r" % e
    check("run_cmd: a program that is not there is rc -1, never raised", lambda: isinstance(got, tuple)
          and got[0] == -1, got)
    try:
        got = WF.run_cmd([sys.executable, "-c", "import time; time.sleep(5)"], 0.5)
    except Exception as e:      # noqa: BLE001
        got = "raised %r" % e
    check("run_cmd: a timeout is rc -1, never raised", lambda: isinstance(got, tuple) and got[0] == -1, got)
    # this process's standard input made a pipe holding a typed line, which a
    # child that inherited it would read
    r_fd, w_fd = os.pipe()
    os.write(w_fd, b"typed\n")
    os.close(w_fd)
    saved = os.dup(0)
    try:
        os.dup2(r_fd, 0)
        got = WF.run_cmd([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], 10)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(r_fd)
    check("run_cmd: standard input is closed, so nothing can prompt",
          lambda: got[0] == 0 and got[1].strip() == "''", got)

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


WIFI = "run/wifi.py"
MUTANTS = [(n, WIFI, o, w) for n, o, w in [
    ("connected on any SSID is joined", "return bool(connected and cur == ssid)", "return bool(connected)"),
    ("on the SSID but not connected is joined", "return bool(connected and cur == ssid)", "return bool(cur == ssid)"),
    ("netsh: the first interface, whatever its name", 'mine = [b for b in blocks if b["name"] == iface]',
     "mine = blocks"),
    ("netsh: no block of that name reads the whole output",
     "        if not mine:\n            return None, None\n        lines = mine[0][\"lines\"]",
     "        lines = mine[0][\"lines\"] if mine else [ln.strip() for ln in out.splitlines()]"),
    ("netsh: the SSID line matched anywhere (BSSID too)", 'm = re.match(r"^SSID\\s*:\\s*(.+)$", s)',
     'm = re.search(r"SSID\\s*:\\s*(.+)$", s)'),
    ("netsh: the state compared case-sensitively", 'state.lower() == "connected"', 'state == "connected"'),
    ("netsh: a failed connect goes on to poll",
     '        if rc != 0:\n            return False, "netsh wlan connect failed', '        if False:\n            return False, "netsh wlan connect failed'),
    ("nmcli: connected by the state's text", "connected = code == NM_CONNECTED", 'connected = state == "connected"'),
    ("nmcli: connected while still getting an address", "connected = code == NM_CONNECTED", "connected = code is not None and code >= 70"),
    ("nmcli: the first listed network, active or not", 'if len(f) >= 2 and f[0] == "yes":', "if len(f) >= 2:"),
    ("nmcli: terse escapes kept", "            out.append(v[i + 1])", "            out.append(v[i:i + 2])"),
    ("nmcli: a failed activation goes on to poll",
     '        if rc != 0:\n            return False, "nmcli connection up failed', '        if False:\n            return False, "nmcli connection up failed'),
    ("nmcli: the read asks for secrets", '"GENERAL.STATE", "device", "show", iface]',
     '"GENERAL.STATE", "device", "show", "--show-secrets", iface]'),
    ("nmcli: the read rescans", '"ifname", iface, "--rescan", "no"]', '"ifname", iface]'),
    ("Linux gets netsh", 'BACKENDS = {"Windows": Netsh, "Linux": Nmcli}', 'BACKENDS = {"Windows": Netsh, "Linux": Netsh}'),
    ("another system falls back to netsh", "cls = BACKENDS.get(system)", "cls = BACKENDS.get(system, Netsh)"),
    ("backend() ignores the system", "return backend_for(platform.system())", 'return backend_for("Windows")'),
    ("a second look at the system", "    be = be or backend()\n    now, sleep",
     "    be = be or (Netsh() if os.name == \"nt\" else backend())\n    now, sleep"),
    ("a 60 s command timeout", "CMD_TIMEOUT_S = 45", "CMD_TIMEOUT_S = 60"),
    ("polled every 2 s", "POLL_S = 1.0", "POLL_S = 2.0"),
    ("joined within 60 s", "JOIN_TIMEOUT_S = 40.0", "JOIN_TIMEOUT_S = 60.0"),
    ("rejoined within 40 s", "REJOIN_TIMEOUT_S = 20.0", "REJOIN_TIMEOUT_S = 40.0"),
    ("standard input left open", "timeout=timeout, stdin=subprocess.DEVNULL,", "timeout=timeout,"),
    ("a missing program raises", "    except (OSError, subprocess.SubprocessError) as e:\n        return -1, str(e)",
     "    except subprocess.SubprocessError as e:\n        return -1, str(e)"),
]]


if __name__ == "__main__":
    if "--can-fail" in sys.argv[1:]:
        sys.exit(_ota_mutants.run_mutants(os.path.abspath(__file__), MUTANTS, "wifi"))
    sys.exit(main())
