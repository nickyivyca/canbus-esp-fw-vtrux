#!/usr/bin/env python3
"""The one way gen-inhibit's suite and harness reach the WiCAN's hotspot: the pinned core's
vtrux_bench.run.wifi, with the backend the machine's settings choose (shared/bench
FLASH-SPEC O2, O4, O4a, O5, O12; core shared/main tag 2026.11.16.5).

Gen-inhibit tester, 2026-11-16, the gen-inhibit reviewer's job (1): the suite's WiCAN OTA
and every rejoin through vtrux_bench.run.wifi, the phone's adb backend in the machine
settings, and the HTTP checks over the phone's /32 route.

  - The settings are the core flasher's own settings.ota block (vtrux_bench.run.ota,
    ota_settings): host, iface, profile, ssid_env, wifi ("system" or "adb"), and with
    "adb" adb_serial and adb. They are read and checked by the core's ota_settings, so
    the suite, its tools and the core's flasher agree on one form. The SSID is never in
    them: ssid_env names the variable holding it (GI_WICAN_SSID, the suite's, read from
    bench-holder.md).
  - The suite hands the block to its tools as JSON in GI_OTA_SETTINGS, as it hands
    GI_X2_DEVICE. Unset, not JSON, not an object, or a block the core refuses: Refused.
    There is no default and no netsh or nmcli of this harness's own.
  - Every join and every rejoin selects the network through the chosen backend (the
    phone: explicitly, by its saved network's id; it is never left to rejoin by itself),
    and the phone's join also puts the host's /32 route to the device through the
    phone (AdbPhone.route). An HTTP read of the device goes over that route.
  - What these return has the SSID replaced by <WICAN_SSID> (the core's mask) and the
    phone's serial by <adb serial> (the core's AdbPhone).

    python3.13 -B gi_wifi.py status     the interface's network, masked, exit 0/1
    python3.13 -B gi_wifi.py join       join (40 s), exit 0 joined, 1 not, 2 refused
"""
import json
import os
import sys

ENV = "GI_OTA_SETTINGS"


class Refused(Exception):
    """No settings.ota block, a block the core refuses, or no core to read it with."""


def core_pin():
    """gi_core_pin: the one already imported, else loaded by file from beside this file."""
    import importlib.util
    mod = sys.modules.get("gi_core_pin")
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            "gi_core_pin", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gi_core_pin.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gi_core_pin"] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("gi_core_pin", None)
            raise
    return mod


def core():
    """(vtrux_bench.run.ota, vtrux_bench.run.wifi) from the checkout gen-inhibit pins
    (gi_core_pin; port step 4 item 1, 2026-11-25)."""
    try:
        core_pin().use_bench()
        from vtrux_bench.run import ota as OTA
        from vtrux_bench.run import wifi as W
    except Exception as e:  # noqa: BLE001  a refused pin or a failed import: no core
        raise Refused("vtrux_bench.run.ota could not be taken from the pinned core "
                      "(gi_core_pin): %s: %s" % (type(e).__name__, e))
    if not hasattr(OTA, "wifi_backend"):
        raise Refused("the pinned core has no phone backend (vtrux_bench.run.ota.wifi_backend): "
                      "it must be shared/main 2026.11.16.5 or later")
    return OTA, W


def config(ota):
    """The core's checked form of a settings.ota block (ota_settings), or Refused."""
    if not isinstance(ota, dict) or not ota:
        raise Refused("no settings.ota block: the WiCAN's hotspot is reached only through the "
                      "Wi-Fi backend the machine's settings choose (FLASH-SPEC O2)")
    OTA, _ = core()
    cfg, why = OTA.ota_settings({"ota": ota})
    if cfg is None:
        raise Refused(why)
    return cfg


def from_env(environ=None):
    """The settings.ota block from GI_OTA_SETTINGS, checked (config), or Refused."""
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV)
    if not raw:
        raise Refused("%s is not set: the suite sets it from settings.ota (the core flasher's "
                      "block: host, iface, profile, ssid_env, wifi)" % ENV)
    try:
        ota = json.loads(raw)
    except ValueError:
        raise Refused("%s is not JSON" % ENV)
    return config(ota)      # config refuses anything but a non-empty object


def env_value(ota):
    """GI_OTA_SETTINGS's value for a settings.ota block."""
    return json.dumps(ota, sort_keys=True)


def backend(cfg):
    """The backend the settings chose: the phone over adb, or the host system's."""
    OTA, W = core()
    return OTA.wifi_backend(cfg) or W.backend()


def _mask(cfg, text):
    OTA, _ = core()
    return OTA.mask(text, cfg["ssid"])


def join(cfg, timeout=None, be=None):
    """(ok, why): select the hotspot through the backend and wait until joined (40 s)."""
    _, W = core()
    be = be or backend(cfg)
    ok, why = W.join(cfg["profile"], cfg["iface"], cfg["ssid"], be=be,
                     **({} if timeout is None else {"timeout": timeout}))
    return ok, _mask(cfg, why)


def select(cfg, be=None):
    """(ok, why): select the hotspot through the backend and return without waiting (a poll
    loop that goes on reading, page_only_baseline's): the phone's connect call by the saved
    network's id, with the /32 route first; nmcli's or netsh's connect of the profile."""
    be = be or backend(cfg)
    ok, why = be.connect(cfg["profile"], cfg["iface"])
    return ok, _mask(cfg, why)


def rejoin(cfg, be=None):
    """(ok, why): join with the watch's shorter bound (20 s, O12)."""
    _, W = core()
    be = be or backend(cfg)
    ok, why = W.rejoin(cfg["profile"], cfg["iface"], cfg["ssid"], be=be)
    return ok, _mask(cfg, why)


def state(cfg, be=None):
    """(on_the_hotspot, state text): connected AND on the WiCAN's SSID, read back from the
    backend; the state masked. A backend that cannot be read is not on it."""
    be = be or backend(cfg)
    try:
        ssid, st, connected = be.read(cfg["iface"])
    except Exception as e:      # noqa: BLE001 -- a read that fails is a read that says no
        return False, "read failed (%s)" % type(e).__name__
    on = bool(connected and ssid == cfg["ssid"])
    if on:
        return True, "connected to %s" % _mask(cfg, ssid)
    return False, "%s, on %s" % (st or "unknown", "the WiCAN's SSID" if ssid == cfg["ssid"]
                                  else ("another network" if ssid else "no network"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["status"], ["join"]):
        print(__doc__)
        return 2
    try:
        cfg = from_env()
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    if argv == ["status"]:
        on, st = state(cfg)
        print("%s backend: %s" % (cfg["wifi"], st))
        return 0 if on else 1
    ok, why = join(cfg)
    print("%s backend: %s" % (cfg["wifi"], why))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
