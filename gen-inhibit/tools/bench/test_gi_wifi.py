#!/usr/bin/env python3
"""Harness test for gi_wifi.py, gi_ota.py and ota_pcap.sh: the WiCAN's hotspot only through
the pinned core's vtrux_bench.run.wifi, with the backend settings.ota chooses (gen-inhibit
tester, 2026-11-16, the gen-inhibit reviewer's job (1); FLASH-SPEC O2, O4, O4a, O12).

gi_wifi:
  - the settings come from GI_OTA_SETTINGS and are checked by the core's ota_settings:
    unset, not JSON, not an object, an empty block, the SSID itself in it, a missing
    interface, the phone without its serial, or a phone profile that is not a saved
    network's id are each refused;
  - the phone's settings choose the core's AdbPhone with that serial; the system's choose
    the host's own backend;
  - join selects through the backend and is joined only when the backend reads the
    WiCAN's SSID connected; rejoin is the core's rejoin (20 s), not a 40 s join; select is
    one connect with the settings' profile and interface and no read; state is on the
    hotspot only when connected AND on the SSID, and a backend that raises is not on it;
  - the SSID is replaced by <WICAN_SSID> in every text returned.
gi_ota (the core's flash_once stood in for):
  - refused, exit 2, with nothing sent, without GI_OTA_SETTINGS;
  - one flash_once with the image, the settings' host, profile and interface, the image's
    own app_elf_sha256 as its identity, the record named, the capture as the settings
    say, and no hold;
  - the backend the settings choose is chosen for the upload and cleared after it, also
    when the upload raises;
  - exit 0 a load, 1 not a load once something was sent, 2 nothing sent; its console on
    stdout, SSID masked.
ota_pcap.sh (gi_ota stood in for, in a copy of this folder): it runs gi_ota with the image
and <stem>.json, writes <stem>.txt with the SSID masked in both files, passes gi_ota's exit
on, and refuses without GI_OTA_SETTINGS.

    PYTHONPATH=<core 2026.11.16.5>/shared/bench python3.13 -B test_gi_wifi.py [--mutants]

--mutants: each mutant of gi_wifi.py or gi_ota.py is a copy in this run's scratch folder
with one edit, run through GI_WIFI_DIR; a check must fail on each, and the unmutated copy,
the control, must pass.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
# no bytecode anywhere below this process: a child that imports the pinned core must not write
# __pycache__ into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("GI_WIFI_DIR") or HERE
sys.path.insert(0, HERE)
sys.path.insert(0, SRC)
import gi_paths                 # noqa: E402
import gi_wifi as GW            # noqa: E402
import gi_ota as GO             # noqa: E402
from vtrux_bench.run import ota as OTA      # noqa: E402
from vtrux_bench.run import wifi as W       # noqa: E402

SSID = "WiCAN_test_ssid"
PHONE = {"host": "192.168.80.1", "iface": "wlan0", "profile": "79", "ssid_env": "GI_WICAN_SSID",
         "wifi": "adb", "adb_serial": "SERIALXYZ", "adb": "/x/adb", "capture": False}
SYSTEM = {"iface": "wlx0", "profile": "wican", "ssid_env": "GI_WICAN_SSID"}
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  [%s]" % (detail,)))


def call(fn):
    try:
        return fn()
    except Exception as e:      # noqa: BLE001 -- judged by the check
        return e


class FakeBe:
    name = "fake"

    def __init__(self, reads=(), connect=(True, "asked to join %s" % SSID)):
        self.reads, self.ret = list(reads), connect
        self.read_ifaces, self.connects = [], []

    def read(self, iface):
        self.read_ifaces.append(iface)
        r = self.reads.pop(0) if self.reads else (None, "disconnected", False)
        if isinstance(r, Exception):
            raise r
        return r

    def connect(self, profile, iface):
        self.connects.append((profile, iface))
        return self.ret


def env_with(block):
    e = {"GI_WICAN_SSID": SSID}
    if block is not None:
        e[GW.ENV] = block if isinstance(block, str) else json.dumps(block)
    return e


def refused(fn):
    r = call(fn)
    return isinstance(r, GW.Refused), r


def wifi_cases():
    os.environ["GI_WICAN_SSID"] = SSID
    for name, block in [("unset", None), ("not JSON", "{nope"), ("not an object", "[1]"), ("empty", {}),
                        ("the SSID itself", dict(PHONE, ssid=SSID)), ("no interface", dict(PHONE, iface="")),
                        ("the phone without its serial", dict(PHONE, adb_serial="")),
                        ("a phone profile that is not an id", dict(PHONE, profile="WiCAN"))]:
        ok, r = refused(lambda: GW.from_env(env_with(block)))
        check("settings: %s is refused" % name, ok, r)
    cfg = call(lambda: GW.from_env(env_with(PHONE)))
    check("settings: the phone's block is read through the core", isinstance(cfg, dict) and cfg.get("wifi") == "adb"
          and cfg.get("profile") == "79" and cfg.get("ssid") == SSID, cfg)
    check("settings: GI_OTA_SETTINGS round-trips", GW.from_env(env_with(json.loads(GW.env_value(PHONE)))) == cfg)
    be = GW.backend(cfg)
    check("backend: the phone's settings choose the core's AdbPhone with that serial",
          isinstance(be, W.AdbPhone) and be.serial == "SERIALXYZ" and be.adb == "/x/adb", be)
    scfg = GW.from_env(env_with(SYSTEM))
    sbe = call(lambda: GW.backend(scfg))
    check("backend: the system's settings choose the host's own backend",
          not isinstance(sbe, W.AdbPhone) and type(sbe).__name__ in ("Nmcli", "Netsh"), sbe)

    fb = FakeBe([("Other", "connected", True), (SSID, "connected", True)])
    r = GW.join(cfg, timeout=5, be=fb)
    check("join: selects through the backend with the settings' profile and interface",
          fb.connects == [("79", "wlan0")], fb.connects)
    check("join: joined once the backend reads the WiCAN's SSID connected, masked",
          r[0] is True and SSID not in r[1] and OTA.SSID_MASK in r[1], r)
    fb = FakeBe([(SSID, "no address yet", False)] * 3)
    r = GW.join(cfg, timeout=1.5, be=fb)
    check("join: on the SSID but not connected is not joined", r[0] is False, r)
    r = GW.join(cfg, timeout=5, be=FakeBe(connect=(False, "root denied for %s" % SSID)))
    check("join: a failed select is not joined, masked", r[0] is False and SSID not in r[1], r)

    seen = []
    saved = W.rejoin
    W.rejoin = lambda profile, iface, ssid, **kw: seen.append((profile, iface, ssid, kw)) or (True, "x")
    try:
        GW.rejoin(cfg, be=FakeBe())
    finally:
        W.rejoin = saved
    check("rejoin: the core's rejoin (its 20 s bound), with the settings' profile, interface and SSID",
          len(seen) == 1 and seen[0][:3] == ("79", "wlan0", SSID) and "timeout" not in seen[0][3], seen)

    fb = FakeBe()
    r = GW.select(cfg, be=fb)
    check("select: one connect, the settings' profile and interface, no read",
          fb.connects == [("79", "wlan0")] and fb.read_ifaces == [] and r[0] is True, (fb.connects, fb.read_ifaces))
    check("select: its text masked", SSID not in r[1] and OTA.SSID_MASK in r[1], r)

    fb = FakeBe([(SSID, "connected", True)])
    r = GW.state(cfg, be=fb)
    check("state: connected on the WiCAN's SSID is on the hotspot, read from the settings' interface",
          r[0] is True and fb.read_ifaces == ["wlan0"], (r, fb.read_ifaces))
    sts = [r[1]]
    for name, rd in [("another SSID", ("Other", "connected", True)),
                     ("the SSID, not connected", (SSID, "no address yet", False)),
                     ("no network", (None, "disconnected", False)), ("a backend that raises", OSError("gone"))]:
        r = call(lambda: GW.state(cfg, be=FakeBe([rd])))
        check("state: %s is not on the hotspot, no raise" % name, isinstance(r, tuple) and r[0] is False, r)
        sts.append(r[1] if isinstance(r, tuple) else "")
    check("state: the SSID is in no state text", not any(SSID in s for s in sts), sts)


def fake_image(path, elf="ab" * 32):
    d = bytearray(0x400)
    d[0] = 0xE9
    d[0x20:0x24] = (0xABCD5432).to_bytes(4, "little")
    d[0x30:0x38] = b"2341e73d"
    d[0x20 + 0x90:0x20 + 0xB0] = bytes.fromhex(elf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(bytes(d))
    return elf


def ota_cases(work):
    img = os.path.join(work, "img", "app.bin")
    elf = fake_image(img)
    rec = os.path.join(work, "rec", "ota_x.json")
    calls, chosen = [], []

    def run(block, result, raise_=None):
        calls.clear()
        chosen.clear()

        def fo(args, hold=None):
            calls.append((args, hold))
            chosen.append(("during", W._CHOSEN))
            if raise_:
                raise raise_
            return dict(result, console="VERDICT: (c) TOOK on %s\n" % SSID)
        saved_fo, saved_env = OTA.flash_once, dict(os.environ)
        OTA.flash_once = fo
        os.environ.clear()
        os.environ.update(saved_env)
        os.environ.pop(GW.ENV, None)
        os.environ.update(env_with(block))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = call(lambda: GO.main(["--bin", img, "--record", rec]))
        finally:
            OTA.flash_once = saved_fo
            os.environ.clear()
            os.environ.update(saved_env)
        chosen.append(("after", W._CHOSEN))
        return rc, out.getvalue()

    rc, out = run(None, {"rc": 0, "sent": True})
    check("gi_ota: no GI_OTA_SETTINGS is refused, exit 2, nothing sent", rc == 2 and "REFUSED" in out and not calls,
          (rc, out[:120]))
    rc, out = run(PHONE, {"rc": 0, "sent": True})
    a, hold = calls[0] if calls else (None, "none")
    check("gi_ota: one flash_once", len(calls) == 1, len(calls))
    check("gi_ota: the image, the settings' host, profile and interface, the record named",
          a is not None and a.bin == img and a.host == "192.168.80.1" and a.profile == "79" and a.iface == "wlan0"
          and a.record == rec and a.flash is True and a.status is False, a and vars(a))
    check("gi_ota: the image's own app_elf_sha256 is its identity, no pin", a is not None and a.expect == elf
          and not a.pin, a and a.expect)
    check("gi_ota: the capture as the settings say", a is not None and a.no_capture is True, a and a.no_capture)
    check("gi_ota: no hold handed (an armed device is refused, O7)", hold is None, hold)
    check("gi_ota: the phone chosen for the upload, cleared after",
          len(chosen) == 2 and isinstance(chosen[0][1], W.AdbPhone) and chosen[1][1] is None, chosen)
    check("gi_ota: exit 0 a load, the console on stdout, masked", rc == 0 and "VERDICT: (c) TOOK" in out
          and SSID not in out, (rc, out[:120]))
    rc, out = run(PHONE, {"rc": 1, "sent": True})
    check("gi_ota: not a load once sent is exit 1", rc == 1, rc)
    rc, out = run(PHONE, {"rc": 1, "sent": False})
    check("gi_ota: nothing sent is exit 2", rc == 2, rc)
    rc, out = run(PHONE, {}, raise_=RuntimeError("boom"))
    check("gi_ota: the backend cleared even when the upload raises", chosen and chosen[-1][1] is None, chosen)


FAKE_GI_OTA = r'''
import argparse, json, os, sys
ap = argparse.ArgumentParser(); ap.add_argument("--bin"); ap.add_argument("--record"); a = ap.parse_args()
ssid = os.environ["GI_WICAN_SSID"]
with open(a.record, "w") as f:
    json.dump({"ssid": ssid, "bin": a.bin, "settings": os.environ.get("GI_OTA_SETTINGS")}, f)
print("upload ended on %s" % ssid)
print("VERDICT: (c) TOOK -- page reads it on %s" % ssid)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def pcap_cases(work):
    d = os.path.join(work, "pcap")
    os.makedirs(d)
    for n in ("ota_pcap.sh", "bench_host.sh"):
        shutil.copy2(os.path.join(HERE, n), os.path.join(d, n))
    with open(os.path.join(d, "gi_ota.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_GI_OTA)
    stem = os.path.join(work, "out", "otagroup_rxq")
    os.makedirs(os.path.dirname(stem))
    img = os.path.join(work, "img", "app.bin")

    def sh(rc="0", block=PHONE):
        env = dict(os.environ, GI_WICAN_SSID=SSID, FAKE_RC=rc)
        env.pop(GW.ENV, None)
        if block is not None:
            env[GW.ENV] = json.dumps(block)
        return subprocess.run(["bash", os.path.join(d, "ota_pcap.sh"), img, stem], capture_output=True, text=True,
                              env=env, timeout=60)
    r = sh()
    txt = open(stem + ".txt").read() if os.path.exists(stem + ".txt") else ""
    js = open(stem + ".json").read() if os.path.exists(stem + ".json") else ""
    check("ota_pcap: gi_ota runs with the image and <stem>.json, and passes the settings on",
          r.returncode == 0 and '"bin": "%s"' % img in js and '"wifi": "adb"' in js.replace('\\"', '"'),
          (r.returncode, r.stderr[-200:], js[:200]))
    check("ota_pcap: <stem>.txt holds the console with the VERDICT line", "VERDICT: (c) TOOK" in txt, txt[:200])
    check("ota_pcap: the SSID is masked in both files and the summary", SSID not in txt and SSID not in js
          and SSID not in r.stdout, (txt[:120], js[:120]))
    r = sh(rc="1")
    check("ota_pcap: gi_ota's exit is passed on", r.returncode == 1, r.returncode)
    r = sh(block=None)
    check("ota_pcap: refused without GI_OTA_SETTINGS", r.returncode != 0 and "GI_OTA_SETTINGS" in r.stderr,
          (r.returncode, r.stderr[-200:]))


MUTANTS = [
    ("gi_wifi.py", "the core's settings check skipped", "    cfg, why = OTA.ota_settings({\"ota\": ota})\n",
     "    cfg, why = dict(ota, ssid=os.environ.get(ota.get(\"ssid_env\") or \"\", \"\"), wifi=ota.get(\"wifi\") or \"system\"), None\n"),
    ("gi_wifi.py", "the phone never chosen", "    return OTA.wifi_backend(cfg) or W.backend()\n", "    return W.backend()\n"),
    ("gi_wifi.py", "join not masked", "    return ok, _mask(cfg, why)\n\n\ndef select", "    return ok, why\n\n\ndef select"),
    ("gi_wifi.py", "select waits (reads)", "    ok, why = be.connect(cfg[\"profile\"], cfg[\"iface\"])\n    return ok, _mask(cfg, why)\n\n\ndef rejoin",
     "    ok, why = W.join(cfg[\"profile\"], cfg[\"iface\"], cfg[\"ssid\"], be=be)\n    return ok, _mask(cfg, why)\n\n\ndef rejoin"),
    ("gi_wifi.py", "rejoin is a 40 s join", "    ok, why = W.rejoin(cfg[\"profile\"], cfg[\"iface\"], cfg[\"ssid\"], be=be)\n",
     "    ok, why = W.join(cfg[\"profile\"], cfg[\"iface\"], cfg[\"ssid\"], be=be)\n"),
    ("gi_wifi.py", "state ignores the SSID", "    on = bool(connected and ssid == cfg[\"ssid\"])\n", "    on = bool(connected)\n"),
    ("gi_wifi.py", "state ignores connected", "    on = bool(connected and ssid == cfg[\"ssid\"])\n", "    on = bool(ssid == cfg[\"ssid\"])\n"),
    ("gi_wifi.py", "a backend that raises crashes state", "    except Exception as e:      # noqa: BLE001 -- a read that fails is a read that says no\n",
     "    except ():\n"),
    ("gi_wifi.py", "the SSID in the state", "        return True, \"connected to %s\" % _mask(cfg, ssid)\n",
     "        return True, \"connected to %s\" % ssid\n"),
    ("gi_wifi.py", "a list accepted as settings", "    if not isinstance(ota, dict) or not ota:\n", "    if not ota:\n"),
    ("gi_ota.py", "the backend not chosen", "    W.choose(OTA.wifi_backend(cfg))\n", ""),
    ("gi_ota.py", "the backend left chosen", "    finally:\n        W.choose(None)\n", "    finally:\n        pass\n"),
    ("gi_ota.py", "the identity not the image's own", "        expect=expect or \"\",", "        expect=\"\","),
    ("gi_ota.py", "the capture always on", "        no_capture=not cfg[\"capture\"],", "        no_capture=False,"),
    ("gi_ota.py", "a hold handed", "        res = OTA.flash_once(args, None)\n", "        res = OTA.flash_once(args, lambda: (True, \"held\"))\n"),
    ("gi_ota.py", "nothing sent is exit 1", "    return 0 if res[\"rc\"] == 0 else (1 if res.get(\"sent\") else 2)\n",
     "    return 0 if res[\"rc\"] == 0 else 1\n"),
    ("gi_ota.py", "the console not masked", "    sys.stdout.write(OTA.mask(res[\"console\"], cfg[\"ssid\"]))\n",
     "    sys.stdout.write(res[\"console\"])\n"),
]


def mutants():
    base = gi_paths.scratch("mutants-gi_wifi", start=__file__)
    srcs = {n: open(os.path.join(HERE, n), encoding="utf-8").read() for n in ("gi_wifi.py", "gi_ota.py")}
    killed = errors = 0
    for n, (fname, name, old, new) in enumerate([(None, "the control: the unmutated copies", None, None)] + MUTANTS):
        if old is not None and srcs[fname].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, srcs[fname].count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        for f, s in srcs.items():
            with open(os.path.join(d, f), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(s.replace(old, new) if f == fname else s)
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, GI_WIFI_DIR=d), timeout=300)
        fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d)" % ("PASS    " if ok else "FAILED  ", name, r.returncode))
            if not ok:
                print(r.stdout[-1500:], r.stderr[-1500:])
                print("\nthe control FAILS: no mutant is judged")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s: %s  (%d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, fname, name, len(fails),
                                               ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    work = gi_paths.scratch("test-gi_wifi", start=__file__)
    for name, fn in (("gi_wifi", wifi_cases), ("gi_ota", lambda: ota_cases(work)),
                     ("ota_pcap", lambda: pcap_cases(work))):
        try:
            fn()
        except Exception as e:      # noqa: BLE001 -- a crash is a failed check, never a pass
            check("%s: the cases ran to their end" % name, False, repr(e))
    fails = [n for n, ok in RESULTS if not ok]
    print("PASS, %d ok" % len(RESULTS) if not fails else "FAIL: %d of %d" % (len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv[1:] else main())
