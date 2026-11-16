"""Joining the WiCAN's hotspot, the same calls on Windows and on Linux
(FLASH-SPEC O4, O4a).

    join(profile, iface, ssid, timeout=40)    -> (ok, why)
    rejoin(profile, iface, ssid, timeout=20)  -> (ok, why)
    on_ssid(iface, ssid)                      -> bool
    current(iface)                            -> (ssid or None, state or None)

Joined means the interface is connected AND on the WiCAN's SSID, read back
from the system, polled every 1 s up to the timeout. A connected interface on
another SSID is not joined.

THE PASSPHRASE. Nothing here reads, stores or passes one. A join brings up the
saved profile the user made; the system holds its secret. No command asks for
secrets (no `--show-secrets`, no `--ask`, no key=clear), and standard input is
closed, so nothing can prompt.

Backends, chosen in ONE place, `backend()`: the one the machine's settings
chose (choose(), the phone), else the host's system's (O4a: nothing else in
the flasher branches on the system, except stopping the optional capture,
ota_wire.stop_signal):

  Windows  netsh (gen-inhibit's bench_flash join_ap / wlan_state at 40ecfa5e):
             netsh wlan connect name=<profile> interface=<iface>
             netsh wlan show interfaces
           The interface's block is the one whose "Name" line is <iface>; in it
           the first "State : ..." and "SSID : ..." lines, as wlan_state reads
           them (anchored, so "BSSID" and "AP BSSID" never match). Connected
           is State "connected", case-insensitive, as join_ap compares it.
           Output with no "Name" line at all is read whole, as wlan_state did.
  Linux    NetworkManager's nmcli, with LC_ALL=C:
             nmcli connection up id <profile>
             nmcli -t -f GENERAL.STATE device show <iface>
             nmcli -t -f ACTIVE,SSID device wifi list ifname <iface> --rescan no
           GENERAL.STATE reads "GENERAL.STATE:<code> (<text>)"; connected is
           code 100 (NM_DEVICE_STATE_ACTIVATED), whatever the text. The SSID
           is that of the line whose ACTIVE field is "yes", with terse mode's
           escapes ("\\:" and "\\\\") undone.

  Phone    AdbPhone: a rooted phone on USB (RNDIS tethering and adb), whose
           own Wi-Fi joins the hotspot (FLASH-SPEC O4, the 3T's form). Not
           chosen by the system: the machine's settings choose it, and the
           flasher hands it to choose() (O4a). `profile` is the saved network's
           id on the phone, `iface` the phone's Wi-Fi interface (wlan0). The
           phone is always named by its adb serial (another adb device, such
           as an emulator, may be attached), and the serial is masked in every
           message. A join, and every rejoin, selects the network explicitly;
           the phone is never left to rejoin by itself, which it did not do
           with its screen off (oneplus3t-bench-endpoint A1).
             adb -s <serial> shell getprop ro.lineage.version
                 the build must be one in CONNECT_TX: the binder transaction
                 number of IWifiManager.connect is the build's own
             adb -s <serial> shell su -c 'service call wifi <tx> i32 0
                 i32 <id> null null i32 0'
                 connect(null config, id): the system's saved network, so no
                 passphrase; the reply must be "Result: Parcel(NULL)"
             adb -s <serial> shell 'cmd wifi status; echo ...; ip -4 -o addr
                 show <iface>'
                 the SSID of the line 'Wifi is connected to "<ssid>"'; connected
                 is "Supplicant state: COMPLETED" AND an IPv4 address on
                 <iface>, since the first moments after COMPLETED have none
           And the host's route to the device through the phone (Linux, with
           NetworkManager; the RNDIS link's own profile is made with
           ipv4.never-default, O5): the RNDIS link is the network interface
           whose driver is rndis_host; the phone's address on it is the
           DHCP router (`nmcli -g DHCP4.OPTION device show <link>`); the route
           is runtime only, `nmcli device modify <link> +ipv4.routes
           "<host>/32 <phone>"`, and is read back with `ip route get <host>`
           every 0.2 s up to 5 s (NetworkManager applies it asynchronously).
           The link's name and the phone's address change with each tethering
           session, so both are read at each join.

Every command runs under a 45 s timeout (as bench_flash's netsh()), through
an injectable runner `run(argv, timeout) -> (rc, text)`, so the tests feed
recorded output. A runner failure (no such program, a timeout) is rc -1 with
its reason, never an exception.
"""

import os
import platform
import re
import subprocess
import time

CMD_TIMEOUT_S = 45
JOIN_TIMEOUT_S = 40.0
REJOIN_TIMEOUT_S = 20.0
POLL_S = 1.0
NM_CONNECTED = 100      # NM_DEVICE_STATE_ACTIVATED


class WifiError(Exception):
    """No backend for this system."""


def run_cmd(argv, timeout=CMD_TIMEOUT_S, env=None):
    """(rc, stdout + stderr). Standard input closed; never raises."""
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL,
                           env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


# ---- Windows: netsh ----------------------------------------------------

def parse_netsh(out, iface):
    """(ssid, state) for interface `iface` in `netsh wlan show interfaces`."""
    blocks, cur = [], None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"^Name\s*:\s*(.+)$", s)
        if m:
            cur = {"name": m.group(1).strip(), "lines": []}
            blocks.append(cur)
            continue
        if cur is not None:
            cur["lines"].append(s)
    if blocks:
        mine = [b for b in blocks if b["name"] == iface]
        if not mine:
            return None, None
        lines = mine[0]["lines"]
    else:
        lines = [ln.strip() for ln in out.splitlines()]
    ssid, state = None, None
    for s in lines:
        m = re.match(r"^SSID\s*:\s*(.+)$", s)
        if m and ssid is None:
            ssid = m.group(1).strip()
        m = re.match(r"^State\s*:\s*(.+)$", s)
        if m and state is None:
            state = m.group(1).strip()
    return ssid, state


class Netsh:
    name = "netsh"

    def __init__(self, run=None):
        self.run = run or run_cmd

    def connect(self, profile, iface):
        rc, out = self.run(["netsh", "wlan", "connect", "name=%s" % profile,
                            "interface=%s" % iface], CMD_TIMEOUT_S)
        if rc != 0:
            return False, "netsh wlan connect failed: %s" % out.strip()[:200]
        return True, out.strip()[:200]

    def read(self, iface):
        """(ssid, state, connected)."""
        _, out = self.run(["netsh", "wlan", "show", "interfaces"], CMD_TIMEOUT_S)
        ssid, state = parse_netsh(out, iface)
        return ssid, state, bool(state and state.lower() == "connected")


# ---- Linux: NetworkManager's nmcli --------------------------------------

def nm_unescape(v):
    """A terse-mode field's value: "\\:" is ":" and "\\\\" is "\\"."""
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            out.append(v[i + 1])
            i += 2
            continue
        out.append(v[i])
        i += 1
    return "".join(out)


def nm_split(line):
    """Terse-mode fields of one line, split on unescaped ":"."""
    fields, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i:i + 2])
            i += 2
            continue
        if c == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    fields.append("".join(cur))
    return [nm_unescape(f) for f in fields]


def parse_nm_state(out):
    """(code or None, state text or None) from `nmcli -t -f GENERAL.STATE
    device show <iface>`: "GENERAL.STATE:100 (connected)"."""
    for line in out.splitlines():
        if line.startswith("GENERAL.STATE:"):
            v = line[len("GENERAL.STATE:"):].strip()
            m = re.match(r"^(\d+)\s*(?:\((.*)\))?$", v)
            if m:
                return int(m.group(1)), (m.group(2) or m.group(1)).strip()
            return None, v or None
    return None, None


def parse_nm_ssid(out):
    """The active network's SSID from `nmcli -t -f ACTIVE,SSID device wifi
    list ifname <iface> --rescan no`: the line "yes:<ssid>"; None if none."""
    for line in out.splitlines():
        f = nm_split(line.rstrip("\r\n"))
        if len(f) >= 2 and f[0] == "yes":
            return ":".join(f[1:]) or None
    return None


def _nm_run(argv, timeout=CMD_TIMEOUT_S):
    return run_cmd(argv, timeout, env=dict(os.environ, LC_ALL="C"))


class Nmcli:
    name = "nmcli"

    def __init__(self, run=None):
        self.run = run or _nm_run

    def connect(self, profile, iface):
        rc, out = self.run(["nmcli", "connection", "up", "id", profile], CMD_TIMEOUT_S)
        if rc != 0:
            return False, "nmcli connection up failed: %s" % out.strip()[:200]
        return True, out.strip()[:200]

    def read(self, iface):
        """(ssid, state, connected)."""
        _, out = self.run(["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", iface],
                          CMD_TIMEOUT_S)
        code, state = parse_nm_state(out)
        _, out = self.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list",
                           "ifname", iface, "--rescan", "no"], CMD_TIMEOUT_S)
        ssid = parse_nm_ssid(out)
        connected = code == NM_CONNECTED
        return ssid, ("connected" if connected else state), connected


# ---- a rooted phone over adb ------------------------------------------------

# IWifiManager.Stub.TRANSACTION_connect, read from each build's own
# framework-wifi.jar (oneplus3t-bench-endpoint A0.6). A build not here is refused.
CONNECT_TX = {"18.1-20210705-NIGHTLY-oneplus3": 91}
ROUTE_WAIT_S = 5.0
ROUTE_POLL_S = 0.2
IP_MARK = "@@ip@@"


def parse_phone(out):
    """(ssid, supplicant state, has an IPv4 address, wifi enabled) from the
    phone's `cmd wifi status`, IP_MARK, `ip -4 -o addr show <iface>`."""
    status, _, ipout = out.partition(IP_MARK)
    ssid = sup = None
    for line in status.splitlines():
        s = line.strip()
        m = re.match(r'^Wifi is connected to "(.*)"$', s)
        if m and ssid is None:
            ssid = m.group(1)
        m = re.search(r"\bSupplicant state: ([A-Z_]+)", s)
        if m and sup is None:
            sup = m.group(1)
    has_ip = re.search(r"\binet \d+\.\d+\.\d+\.\d+/\d+", ipout) is not None
    enabled = re.search(r"^Wifi is enabled$", status, re.M) is not None
    return ssid, sup, has_ip, enabled


def rndis_link(root="/sys/class/net"):
    """The host's network interface whose driver is rndis_host, or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for n in names:
        drv = os.path.join(root, n, "device", "driver")
        if os.path.exists(drv) and os.path.basename(os.path.realpath(drv)) == "rndis_host":
            return n
    return None


def parse_routers(out):
    """The DHCP router from `nmcli -g DHCP4.OPTION device show <link>`
    ("... | routers = a.b.c.d | ..."), or None."""
    m = re.search(r"(?:^|[|\s])routers = (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def parse_route_dev(out):
    """(dev, via) from `ip route get <host>`."""
    dev = re.search(r"\bdev (\S+)", out)
    via = re.search(r"\bvia (\S+)", out)
    return (dev.group(1) if dev else None), (via.group(1) if via else None)


class AdbPhone:
    name = "adb"

    def __init__(self, serial, host, adb="adb", run=None, link=None, now=None, sleep=None):
        self.serial, self.host, self.adb = serial, host, adb
        self.run = run or _nm_run
        self.link = link or rndis_link
        self.now, self.sleep = now or time.time, sleep or time.sleep

    def _mask(self, text):
        text = text.strip()[:200]
        return text.replace(self.serial, "<adb serial>") if self.serial else text

    def _shell(self, cmd):
        return self.run([self.adb, "-s", self.serial, "shell", cmd], CMD_TIMEOUT_S)

    def route(self):
        """(ok, why): the host's route to the device through the phone."""
        link = self.link()
        if not link:
            return False, "no RNDIS link from the phone on this host (is USB tethering on?)"
        rc, out = self.run(["nmcli", "-g", "DHCP4.OPTION", "device", "show", link], CMD_TIMEOUT_S)
        phone = parse_routers(out) if rc == 0 else None
        if not phone:
            return False, "the RNDIS link %s has no DHCP router (is its NetworkManager profile up?)" % link
        _, out = self.run(["ip", "route", "get", self.host], CMD_TIMEOUT_S)
        if parse_route_dev(out) == (link, phone):
            return True, "route to %s via the phone on %s" % (self.host, link)
        rc, out = self.run(["nmcli", "device", "modify", link, "+ipv4.routes",
                            "%s/32 %s" % (self.host, phone)], CMD_TIMEOUT_S)
        if rc != 0:
            return False, "nmcli device modify failed: %s" % out.strip()[:200]
        deadline = self.now() + ROUTE_WAIT_S
        while True:
            _, out = self.run(["ip", "route", "get", self.host], CMD_TIMEOUT_S)
            if parse_route_dev(out) == (link, phone):
                return True, "route to %s via the phone on %s" % (self.host, link)
            if self.now() >= deadline:
                return False, "the route to %s is not on %s within %.0f s (%r)" % (
                    self.host, link, ROUTE_WAIT_S, parse_route_dev(out))
            self.sleep(ROUTE_POLL_S)

    def connect(self, profile, iface):
        if not re.match(r"^\d+$", str(profile)):
            return False, "the profile %r is not a saved network's id on the phone" % (profile,)
        rc, out = self._shell("getprop ro.lineage.version")
        if rc != 0:
            return False, "adb: the phone is not reachable: %s" % self._mask(out)
        build = out.strip()
        tx = CONNECT_TX.get(build)
        if tx is None:
            return False, "the phone's build %r has no known connect transaction" % self._mask(build)
        ok, why = self.route()
        if not ok:
            return False, why
        rc, out = self._shell("su -c 'service call wifi %d i32 0 i32 %d null null i32 0'" % (tx, int(profile)))
        if rc != 0 or out.strip() != "Result: Parcel(NULL)":
            return False, "the phone's connect call failed: %s" % self._mask(out)
        return True, "the phone asked to join its saved network %s; %s" % (profile, why)

    def read(self, iface):
        """(ssid, state, connected)."""
        rc, out = self._shell("cmd wifi status; echo %s; ip -4 -o addr show %s" % (IP_MARK, iface))
        if rc != 0:
            return None, None, False
        ssid, sup, has_ip, enabled = parse_phone(out)
        connected = sup == "COMPLETED" and has_ip and ssid is not None
        if connected:
            state = "connected"
        elif not enabled:
            state = "wifi disabled"
        elif sup == "COMPLETED":
            state = "no address yet"
        else:
            state = (sup or "not connected").lower()
        return ssid, state, connected


def adb_phone(serial, host, adb="adb"):
    """The phone backend, for the flasher to choose() from its settings."""
    return AdbPhone(serial, host, adb)


# ---- the one place the system is looked at ------------------------------

BACKENDS = {"Windows": Netsh, "Linux": Nmcli}
_CHOSEN = None      # a backend the settings chose (choose()), else the system's


def choose(be):
    """Use `be` for every call until choose(None); None goes back to the
    host system's backend. The flasher calls it from its settings (O4a)."""
    global _CHOSEN
    _CHOSEN = be


def backend_for(system, run=None):
    """The backend for `system` ("Windows" or "Linux", as the platform module
    names them). Refused for any other system."""
    cls = BACKENDS.get(system)
    if cls is None:
        raise WifiError("no Wi-Fi backend for %r (only %s)"
                        % (system, ", ".join(sorted(BACKENDS))))
    return cls(run)


def backend():
    """The backend chosen from the settings, else this host's: the one place
    the flasher's Wi-Fi looks at the system (O4a)."""
    if _CHOSEN is not None:
        return _CHOSEN
    return backend_for(platform.system())


# ---- the calls ------------------------------------------------------------

def current(iface, be=None):
    """(ssid, state) the interface is on now, read from the system."""
    ssid, state, _ = (be or backend()).read(iface)
    return ssid, state


def on_ssid(iface, ssid, be=None):
    """True only when the interface is connected AND on `ssid`, read back."""
    cur, _state, connected = (be or backend()).read(iface)
    return bool(connected and cur == ssid)


def join(profile, iface, ssid, timeout=JOIN_TIMEOUT_S, be=None, now=None, sleep=None):
    """Bring up the saved profile, then poll every POLL_S until the interface
    is connected and on `ssid`, up to `timeout`. (ok, why)."""
    be = be or backend()
    now, sleep = now or time.time, sleep or time.sleep
    ok, out = be.connect(profile, iface)
    if not ok:
        return False, out
    deadline = now() + timeout
    while now() < deadline:
        if on_ssid(iface, ssid, be):
            return True, "associated with %s" % ssid
        sleep(POLL_S)
    cur, state = current(iface, be)
    return False, "did not associate within %.0f s (now %r / %r)" % (timeout, cur, state)


def rejoin(profile, iface, ssid, timeout=REJOIN_TIMEOUT_S, be=None, now=None, sleep=None):
    """join() again, with the watch's shorter timeout (O12: 20 s)."""
    return join(profile, iface, ssid, timeout=timeout, be=be, now=now, sleep=sleep)
