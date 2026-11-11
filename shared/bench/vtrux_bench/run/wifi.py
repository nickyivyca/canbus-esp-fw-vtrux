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

Backends, chosen by the host's system in ONE place, `backend()` (O4a: nothing
else in the flasher branches on the system, except stopping the optional
capture, ota_wire.stop_signal):

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


# ---- the one place the system is looked at ------------------------------

BACKENDS = {"Windows": Netsh, "Linux": Nmcli}


def backend_for(system, run=None):
    """The backend for `system` ("Windows" or "Linux", as the platform module
    names them). Refused for any other system."""
    cls = BACKENDS.get(system)
    if cls is None:
        raise WifiError("no Wi-Fi backend for %r (only %s)"
                        % (system, ", ".join(sorted(BACKENDS))))
    return cls(run)


def backend():
    """This host's backend: the one place the flasher's Wi-Fi looks at the
    system (O4a)."""
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
