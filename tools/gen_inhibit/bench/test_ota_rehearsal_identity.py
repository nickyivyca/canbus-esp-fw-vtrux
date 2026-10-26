#!/usr/bin/env python3
"""Test that ota_rollback_rehearsal names its unit from the connected AP's BSSID, value-blind,
and its image from /gen_inhibit, never /check_status (reviewer, 2026-10-25; that page answers
with the device's config, credentials included: reviewer, 2026-10-20).

The unit row used /check_status's device_id (the SoftAP MAC). The connected AP's BSSID
carries the same MAC (checked value-blind on the bench, 2026-10-25), so unit_check compares
`netsh wlan show interfaces`'s BSSID with the settings' wican.device_id, and neither value
may be printed, recorded or returned. The verify row that compared /check_status's
git_version with the good image's version now compares /gen_inhibit's elf_sha256 with the
good image's descriptor.

Made-up identities only: a fake GI_CHAIN_SETTINGS (the module reads it when imported), a
fake netsh, a fake device whose /check_status answers a made-up value, fake images.
  connected_bssid  one BSSID (Windows 10 and 11 forms) -> it; none, two, a short one, or
                   netsh failing -> none, with the reason
  unit_check       a match (any case or separator) -> ok; another unit, no reading, no or a
                   short expectation -> not ok; neither value in the reason, ever
  device_state     asks only /gen_inhibit; keeps unit_matches/unit_why, elf_sha256, mode...;
                   no device_id or git_version; neither identity nor the made-up value in it
  ota_precondition the unit row first, and it fails on a mismatch alone
  phase_verify     elf_sha256 against the good image's: equal passes, another fails, none
                   fails; a unit mismatch fails; the record holds neither identity
  source           no string constant outside a docstring names /check_status (a control
                   finds one in a probe file)

    py -3.14 -B tools/gen_inhibit/bench/test_ota_rehearsal_identity.py
Mutants: test_ota_rehearsal_identity_mutants.py (REHEARSAL_DIR runs another copy).
Gen-inhibit tester, 2026-10-25.
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("REHEARSAL_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths                      # noqa: E402
import image_elf_sha as M            # noqa: E402

SCRATCH = gi_paths.scratch("ota-rehearsal-identity")
DEVICE_ID = "02a1b2c3d4e5"           # made up
BASE_MAC = "02:a1:b2:c3:d4:e4"       # made up, device id - 1
OTHER = "02a1b2c3d4e6"
MADE_UP = "made-up-value-not-a-credential"
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def load():
    os.makedirs(SCRATCH, exist_ok=True)
    path = os.path.join(SCRATCH, "chain_settings_fake.json")
    json.dump({"wican": {"base_mac": BASE_MAC, "device_id": DEVICE_ID}}, open(path, "w", encoding="utf-8"))
    os.environ["GI_CHAIN_SETTINGS"] = path
    spec = importlib.util.spec_from_file_location("orr_identity", os.path.join(SRC, "ota_rollback_rehearsal.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def netsh(text=None, raise_=None):
    def run(cmd, **kw):
        assert cmd[:4] == ["netsh", "wlan", "show", "interfaces"], cmd
        if raise_:
            raise raise_
        return types.SimpleNamespace(stdout=text, returncode=0)
    return run


def iface(bssid, label="BSSID"):
    return ("    Name                   : Wi-Fi\n    State                  : connected\n"
            "    SSID                   : made-up-ssid\n    %-22s : %s\n    Signal                 : 99%%\n"
            % (label, bssid))


def colons(h):
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def leaks(text):
    t = str(text).lower()
    return [v for v in (DEVICE_ID, colons(DEVICE_ID), OTHER, colons(OTHER), MADE_UP) if v in t]


def bssid_cases(R):
    for label, run, want in [
            ("one BSSID, Windows 10 form", netsh(iface(colons(DEVICE_ID))), DEVICE_ID),
            ("one BSSID, Windows 11 form (AP BSSID)", netsh(iface(colons(DEVICE_ID), "AP BSSID")), DEVICE_ID),
            ("upper case with dashes", netsh(iface(colons(DEVICE_ID).upper().replace(":", "-"))), DEVICE_ID),
            ("not connected: no BSSID line", netsh("    State                  : disconnected\n"), None),
            ("two interfaces, two BSSIDs", netsh(iface(colons(DEVICE_ID)) + iface(colons(OTHER))), None),
            ("a BSSID too short", netsh(iface("02:a1:b2:c3:d4")), None),
            ("netsh failing", netsh(raise_=OSError("no netsh")), None)]:
        got, why = R.connected_bssid(run)
        check("connected_bssid: %s" % label, got == want and (why is None) == (want is not None), (got, why))
        check("connected_bssid: the reason names no value (%s)" % label, not leaks(why), why)


def unit_cases(R):
    for label, expect, reading, want in [
            ("the connected AP is the unit", DEVICE_ID, (DEVICE_ID, None), True),
            ("the expectation in another form", colons(DEVICE_ID).upper(), (DEVICE_ID, None), True),
            ("another unit", DEVICE_ID, (OTHER, None), False),
            ("no reading", DEVICE_ID, (None, "0 BSSIDs read from netsh, not one"), False),
            ("no expectation", "", (DEVICE_ID, None), False),
            ("a short expectation", DEVICE_ID[:10], (DEVICE_ID[:10], None), False)]:
        ok, why = R.unit_check(expect, read=lambda r=reading: r)
        check("unit_check: %s -> %s" % (label, want), ok is want, (ok, why))
        check("unit_check: the reason names neither value (%s)" % label, not leaks(why), why)


class Device:
    def __init__(self, page):
        self.page, self.paths = page, []

    def http_json(self, ep, timeout=8.0, host=None):
        self.paths.append(ep.split("?")[0])
        if ep.startswith("/check_status"):
            return {"device_id": OTHER, "git_version": MADE_UP, "sta_pass": MADE_UP}
        if ep.startswith("/gen_inhibit"):
            return dict(self.page)
        raise OSError("404")


PAGE = {"mode": 0, "inhibit_live": False, "bus_on": False, "bus_ours": False, "tx_ok": 0, "arm_block": "not armed",
        "abort_reason": "", "abort_latched": False, "elf_sha256": "c0" * 32, "uptime_ms": 70000}


def state_cases(R):
    real = (R.http_json, R.connected_bssid)
    try:
        dev = Device(PAGE)
        R.http_json = dev.http_json
        R.connected_bssid = lambda: (DEVICE_ID, None)
        st = R.device_state(DEVICE_ID)
        check("device_state: only /gen_inhibit asked", dev.paths == ["/gen_inhibit"], dev.paths)
        check("device_state: the unit matches", st.get("unit_matches") is True, st)
        check("device_state: elf_sha256 and mode from the page", st.get("elf_sha256") == "c0" * 32 and st.get("mode") == 0, st)
        check("device_state: no device_id or git_version kept", not {"device_id", "git_version"} & set(st), sorted(st))
        check("device_state: no identity or made-up value in it", not leaks(json.dumps(st)), leaks(json.dumps(st)))
        rows = R.ota_precondition(st)
        check("ota_precondition: the unit row comes first", rows and "expected unit" in rows[0][0], rows)
        check("ota_precondition: every row passes for the unit, idle", all(ok for _, ok in rows), rows)
        R.connected_bssid = lambda: (OTHER, None)
        st2 = R.device_state(DEVICE_ID)
        rows = R.ota_precondition(st2)
        check("ota_precondition: another unit fails the unit row alone",
              [n for n, ok in rows if not ok] == [rows[0][0]], rows)
        check("device_state: another unit, still neither value in it", not leaks(json.dumps(st2)), st2)
    finally:
        R.http_json, R.connected_bssid = real


def fake_image(path, elf_hex, version="abc1234"):
    d = bytearray(0x400)
    d[0] = M.IMAGE_MAGIC
    d[M.DESC_OFF:M.DESC_OFF + 4] = M.DESC_MAGIC.to_bytes(4, "little")
    d[M.DESC_OFF + 0x10:M.DESC_OFF + 0x10 + len(version)] = version.encode()
    d[M.DESC_OFF + M.SHA_OFF_IN_DESC:M.DESC_OFF + M.SHA_OFF_IN_DESC + 32] = bytes.fromhex(elf_hex)
    open(path, "wb").write(bytes(d))
    return path


def verify_cases(R):
    real = (R.http_json, R.connected_bssid, R.RUNS, R.log)
    runs = os.path.join(SCRATCH, "runs")
    shutil.rmtree(runs, ignore_errors=True)
    os.makedirs(runs)
    good = fake_image(os.path.join(SCRATCH, "good.bin"), "c0" * 32)
    try:
        R.RUNS, R.log = runs, (lambda msg: None)
        R.connected_bssid = lambda: (DEVICE_ID, None)
        for label, page_sha, bssid, want in [
                ("the page names the good image", "c0" * 32, DEVICE_ID, 0),
                ("the page names another image", "c1" * 32, DEVICE_ID, 1),
                ("the page names no image", None, DEVICE_ID, 1),
                ("another unit", "c0" * 32, OTHER, 1)]:
            page = dict(PAGE, elf_sha256=page_sha)
            dev = Device(page)
            R.http_json = dev.http_json
            R.connected_bssid = lambda b=bssid: (b, None)
            args = types.SimpleNamespace(good=good, expect_device_id=DEVICE_ID, assume_settled=True)
            rc = R.phase_verify(args)
            recs = sorted(f for f in os.listdir(runs) if f.endswith(".json"))
            rec = json.load(open(os.path.join(runs, recs[-1]), encoding="utf-8")) if recs else {}
            check("phase_verify: %s -> rc %d" % (label, want), rc == want, (rc, rec.get("fails")))
            check("phase_verify: only /gen_inhibit asked (%s)" % label, set(dev.paths) == {"/gen_inhibit"}, dev.paths)
            check("phase_verify: the record holds neither identity (%s)" % label, not leaks(json.dumps(rec)),
                  leaks(json.dumps(rec)))
            if label == "the page names another image":
                check("phase_verify: it is the image row that fails",
                      rec.get("fails") == ["elf_sha256 matches the good image's"], rec.get("fails"))
            if label == "another unit":
                check("phase_verify: it is the unit row that fails",
                      len(rec.get("fails") or []) == 1 and "expected unit" in rec["fails"][0], rec.get("fails"))
    finally:
        R.http_json, R.connected_bssid, R.RUNS, R.log = real


def named_in_code(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "check_status" in n.value and id(n) not in docs]


def source(R=None):
    lines = named_in_code(os.path.join(SRC, "ota_rollback_rehearsal.py"))
    check("source: ota_rollback_rehearsal.py names /check_status in no code string", not lines, lines)
    probe = os.path.join(SCRATCH, "probe.py")
    open(probe, "w", encoding="utf-8").write('"""/check_status in a docstring"""\nx = http_json("/check_status")\n')
    check("source: the check finds a request in code (control)", named_in_code(probe) == [2], named_in_code(probe))


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    try:
        R = load()
    except Exception as e:      # noqa: BLE001
        check("the module loads", False, "%s: %s" % (type(e).__name__, e))
        print("\nFAIL, %d FAIL" % len(FAILS))
        return 1
    for part in (bssid_cases, unit_cases, state_cases, verify_cases, source):
        try:
            part(R)
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
