#!/usr/bin/env python3
"""Test ota_rollback_rehearsal at log level none (port step 1a2; gen-inhibit reviewer,
2026-10-30): boots from the ROM banner, the good image's gate judged by its effect from
the page, the fault phase's rollback shown the same way, and the unit's bootloader
recorded (its sha256, and the log level its strings show).

The release and bench _man images log nothing (app none, bootloader error), so on the
good image the bootloader's "Loaded app from partition" and ota_health's "gate armed",
"subsystem up" and "marking image valid" lines are gone. The _flt image stays at info.
Made-up identities and images only, in this worktree's scratch/:
  boots           a none capture (ROM banner only) is one boot; an info capture is one
                  boot with its slot and bootloader lines; an rst: line whose ESP-ROM:
                  line was lost is a boot; one banner is one boot; two banners, or two
                  ESP-ROM: lines, are two; lines before any banner belong to no boot
  judge_control   the passing record, then each row's failure on its own: two boots or
                  none, the device already on the good image (control only; restore
                  passes), another image after, up too short, the reboot failing, the
                  page naming another image after the reboot (a rollback), a fault line
  judge_fault     the passing record (the _flt lines on boot 1, a bare banner 65 s later,
                  the page on the good image before and after), then each failure
  wiring          run_ota_phase reboots for control and restore and not for fault, and
                  judges restore without the change row; _do_ota reboots after reading
                  the page, and only when asked; reboot_check asks wican_reboot.py for
                  the good image (--want), and refuses with no hash
  bootloader      bootloader_facts on fake images: the length (segments, the checksum at
                  the 16-byte boundary, the appended hash), the sha256 of the image only,
                  the level from the markers; phase_bootloader records them
  esptool MAC     the otadata and bootloader phases compare esptool's MAC value-blind: a
                  fake esptool printing a made-up MAC; neither MAC printed or in the run
                  file, the match recorded, another unit fails (2026-10-30)

    py -3.14 -B gen-inhibit/tools/bench/test_ota_rehearsal_none.py
Mutants: test_ota_rehearsal_none_mutants.py (REHEARSAL_DIR runs another copy).
Gen-inhibit tester, 2026-10-30.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("REHEARSAL_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths                      # noqa: E402
import image_elf_sha as M            # noqa: E402

SCRATCH = gi_paths.scratch("ota-rehearsal-none")
DEVICE_ID = "00005e005311"           # made up, in the RFC 7042 documentation range
BASE_MAC = "00:00:5e:00:53:10"
GOOD, OTHER, FAULTY = "a0" * 32, "b0" * 32, "f0" * 32
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
    spec = importlib.util.spec_from_file_location("orr_none", os.path.join(SRC, "ota_rollback_rehearsal.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROM = ["ESP-ROM:esp32c3-api1-20210207", "Build:Feb  7 2021",
       "rst:0xc (RTC_SW_CPU_RST),boot:0xe (SPI_FAST_FLASH_BOOT)"]
BOOTLOADER = ["\x1b[0;32mI (31) boot: ESP-IDF v5.4.1 2nd stage bootloader\x1b[0m",
              "I (40) boot:  3 ota_0            OTA app          00 10 00010000 001b3000",
              "I (41) boot:  4 ota_1            OTA app          00 11 001d0000 001b3000",
              "I (60) boot: Loaded app from partition at offset 0x1d0000"]
FLT = ["W (400) ota_health: gate armed: running ota_1, state PENDING_VERIFY, waiting up to 60000 ms for 0x03",
       "W (410) ota_health: FAULT INJECT: suppressing report of 0x01",
       "I (420) ota_health: subsystem up: 0x02"]


def timed(*blocks):
    """[(t, text)]: each block's lines 10 ms apart, the blocks at the times given."""
    out = []
    for t0, lines in blocks:
        out += [(round(t0 + 0.01 * i, 3), x) for i, x in enumerate(lines)]
    return out


def boot_cases(R):
    def bs(lines):
        return R.boots(R.console_events(lines))
    b = bs(timed((10.0, ROM)))
    check("boots: a none capture (ROM banner only) is one boot", len(b) == 1, b)
    check("boots: ... its banner and reset reason kept, no slot, no bootloader line",
          b and b[0]["banner"] == ["rom", "rst"] and b[0]["rst"] == "0xc RTC_SW_CPU_RST"
          and b[0]["slot"] is None and b[0]["bootloader_lines"] == 0, b)
    b = bs(timed((10.0, ROM + BOOTLOADER + FLT)))
    check("boots: an info capture is one boot", len(b) == 1, b)
    check("boots: ... with its slot and its bootloader lines counted",
          b and b[0]["slot"] == "ota_1" and b[0]["bootloader_lines"] == 4, b)
    check("boots: ... and the _flt lines on it", b and b[0]["gate"] and b[0]["gate"]["state"] == "PENDING_VERIFY"
          and b[0]["fault_masks"] == [1] and b[0]["subsystems"] == [2], b)
    b = bs(timed((10.0, ROM[1:])))
    check("boots: an rst: line whose ESP-ROM: line was lost is a boot", len(b) == 1, b)
    b = bs(timed((10.0, ROM), (75.0, ROM)))
    check("boots: two banners are two boots, at their banners' times",
          [x["t"] for x in b] == [10.0, 75.0], [x["t"] for x in b])
    b = bs(timed((10.0, ROM[:1]), (75.0, ROM[:1])))
    check("boots: two ESP-ROM: lines with their rst: lines lost are two boots", len(b) == 2, b)
    b = bs(timed((10.0, ROM), (75.0, ROM[2:])))
    check("boots: a banner, then an rst: line alone, are two boots", len(b) == 2, b)
    b = bs(timed((1.0, ["I (5000) wifi: running", FLT[1]])))
    check("boots: lines before any banner belong to no boot", b == [], b)


def boot1(R, *extra):
    return R.boots(R.console_events(timed((10.0, ROM + list(extra)))))


def control_rec(R, **kw):
    rec = {"image": {"elf_sha256": GOOD}, "good_elf_sha256": GOOD, "boots": boot1(R),
           "before": {"elf_sha256": OTHER, "uptime_ms": 900000},
           "after": {"elf_sha256": GOOD, "uptime_ms": 200000},
           "reboot": {"verdict": "PASS", "found_sha": GOOD}}
    rec.update(kw)
    return rec


def fails(rows):
    return [n for n, ok in rows if not ok]


def control_cases(R):
    rows = R.judge_control(control_rec(R))
    check("judge_control: the passing record passes every row", rows and not fails(rows), fails(rows))
    names = [n for n, _ok in rows]
    for label, rec, want, kw in [
            ("two boots", control_rec(R, boots=R.boots(R.console_events(timed((10.0, ROM), (75.0, ROM))))),
             "exactly one boot", {}),
            ("no boot", control_rec(R, boots=[]), "exactly one boot", {}),
            ("the device already on the good image", control_rec(R, before={"elf_sha256": GOOD}),
             "ran another image", {}),
            ("another image after the OTA", control_rec(R, after={"elf_sha256": OTHER, "uptime_ms": 200000}),
             "runs the good image", {}),
            ("up 30 s, short of the gate's timeout", control_rec(R, after={"elf_sha256": GOOD, "uptime_ms": 30000}),
             "past the gate's timeout", {}),
            ("the reboot failing", control_rec(R, reboot={"verdict": "FAIL", "found_sha": GOOD}),
             "the reboot passed", {}),
            ("after the reboot the page names another image (rolled back)",
             control_rec(R, reboot={"verdict": "FAIL", "found_sha": OTHER}), "gate marked it valid", {}),
            ("no reboot record", control_rec(R, reboot=None), "gate marked it valid", {}),
            ("a fault line on the boot", control_rec(R, boots=boot1(R, FLT[1])), "no fault-inject line", {})]:
        bad = fails(R.judge_control(rec, **kw))
        check("judge_control: %s fails '%s'" % (label, want), any(want in n for n in bad), bad)
    bad = fails(R.judge_control(control_rec(R, before={"elf_sha256": GOOD}), require_change=False))
    check("judge_control: restore (require_change=False) on the good image passes", not bad, bad)
    check("judge_control: no row reads the console's gate lines",
          not any(w in n for n in names for w in ("PENDING", "0x03", "WIFI_AP", "HTTPD", "slot")), names)


def fault_rec(R, **kw):
    b = R.boots(R.console_events(timed((10.0, ROM + FLT), (75.0, ROM))))
    rec = {"image": {"elf_sha256": FAULTY}, "good_elf_sha256": GOOD, "boots": b,
           "before": {"elf_sha256": GOOD}, "after": {"elf_sha256": GOOD, "uptime_ms": 200000}}
    rec.update(kw)
    return rec


def fault_cases(R):
    rows, extra = R.judge_fault(fault_rec(R))
    check("judge_fault: the passing record passes every row", rows and not fails(rows), fails(rows))
    check("judge_fault: the gap is timed between ROM banners", extra.get("boot_gap_s") == 65.0, extra)
    check("judge_fault: the boots' reset reasons and bootloader lines recorded",
          extra.get("boot_rst") == ["0xc RTC_SW_CPU_RST"] * 2 and extra.get("bootloader_lines") == [0, 0], extra)
    ev = R.console_events
    for label, rec, want in [
            ("one boot", fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT))))), "booted exactly twice"),
            ("boots 20 s apart", fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT), (30.0, ROM))))),
             "55-75 s apart"),
            ("boots 90 s apart", fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT), (100.0, ROM))))),
             "55-75 s apart"),
            ("still on the faulty image after", fault_rec(R, after={"elf_sha256": FAULTY}), "back on the good image"),
            ("no page after", fault_rec(R, after=None), "back on the good image"),
            ("another image before the OTA", fault_rec(R, before={"elf_sha256": OTHER}),
             "ran the good image before"),
            ("the faulty image is the good one", fault_rec(R, image={"elf_sha256": GOOD}), "not the good one"),
            ("a fault line on the second boot", fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT),
                                                                                  (75.0, ROM + FLT[1:2]))))),
             "no fault-inject line on the second boot"),
            ("no fault line on the first boot", fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT[:1] + FLT[2:]),
                                                                                  (75.0, ROM))))),
             "fault injection fired"),
            ("WIFI_AP reported on the first boot",
             fault_rec(R, boots=R.boots(ev(timed((10.0, ROM + FLT + ["I (430) ota_health: subsystem up: 0x01"]),
                                                 (75.0, ROM))))), "WIFI_AP did NOT report")]:
        bad = fails(R.judge_fault(rec)[0])
        check("judge_fault: %s fails '%s'" % (label, want), any(want in n for n in bad), bad)
    names = [n for n, _ok in R.judge_fault(fault_rec(R))[0]]
    check("judge_fault: no row reads the second boot's slot or gate", not any("slot" in n or "second boot's image"
                                                                              in n for n in names), names)


def fake_image(path, elf_hex, version="abc1234"):
    d = bytearray(0x400)
    d[0] = M.IMAGE_MAGIC
    d[M.DESC_OFF:M.DESC_OFF + 4] = M.DESC_MAGIC.to_bytes(4, "little")
    d[M.DESC_OFF + 0x10:M.DESC_OFF + 0x10 + len(version)] = version.encode()
    d[M.DESC_OFF + M.SHA_OFF_IN_DESC:M.DESC_OFF + M.SHA_OFF_IN_DESC + 32] = bytes.fromhex(elf_hex)
    open(path, "wb").write(bytes(d))
    return path


def wiring_cases(R):
    real = (R._do_ota, R.save, R.log, R.RUNS)
    good = fake_image(os.path.join(SCRATCH, "good.bin"), GOOD)
    calls = []
    try:
        R.save, R.log, R.RUNS = (lambda phase, rec: None), (lambda m: None), SCRATCH

        def fake_do(args, label, image, watch_s, good_sha=None, reboot=False):
            calls.append((label, good_sha, reboot))
            before = {"elf_sha256": GOOD if label == "restore" else OTHER}
            return control_rec(R, before=before), 0
        R._do_ota = fake_do
        args = types.SimpleNamespace(good=good, faulty=good, watch=1.0)
        rc_c = R.run_ota_phase(args, "control", good, 1.0, R.judge_control)
        rc_r = R.run_ota_phase(args, "restore", good, 1.0, R.judge_control)
        R.run_ota_phase(args, "fault", good, 1.0, R.judge_fault)
        check("run_ota_phase: control and restore reboot, fault does not",
              [(c[0], c[2]) for c in calls] == [("control", True), ("restore", True), ("fault", False)], calls)
        check("run_ota_phase: the good image's elf_sha256 passed to every phase",
              [c[1] for c in calls] == [GOOD] * 3, calls)
        check("run_ota_phase: control passes on the change, restore on the good image without the change row",
              rc_c == 0 and rc_r == 0, (rc_c, rc_r))
    finally:
        R._do_ota, R.save, R.log, R.RUNS = real
    do_ota_cases(R, good)
    reboot_check_cases(R)


def do_ota_cases(R, good):
    real = (R.device_state, R.start_serial, R.post_ota, R.reboot_check, R.log, R.time)
    console = os.path.join(SCRATCH, "rollback_x_console.log")
    open(console, "w", encoding="utf-8").write("".join("[%8.3f] %s\n" % (t, x) for t, x in timed((10.0, ROM))))
    order = []
    try:
        pages = iter([{"elf_sha256": OTHER, "mode": 0, "inhibit_live": False, "bus_ours": False, "unit_matches": True},
                      {"elf_sha256": GOOD, "uptime_ms": 200000, "mode": 0, "inhibit_live": False,
                       "bus_ours": False, "unit_matches": True}] * 2)
        R.device_state = lambda dev=None: (order.append("page"), next(pages))[1]
        proc = types.SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0, kill=lambda: None)
        R.start_serial = lambda name, secs: (proc, console)
        R.post_ota = lambda path: {"status": 200, "body": "ok", "seconds": 1.0}
        R.reboot_check = lambda label, sha: (order.append(("reboot", label, sha)),
                                             {"verdict": "PASS", "found_sha": sha})[1]
        R.log = lambda m: None
        R.time = types.SimpleNamespace(sleep=lambda s: None, time=lambda: 0.0)
        args = types.SimpleNamespace(expect_device_id=DEVICE_ID)
        rec, rc = R._do_ota(args, "control", good, 1.0, good_sha=GOOD, reboot=True)
        check("_do_ota: the page read before and after, then the reboot, wanting the good image",
              rc == 0 and order == ["page", "page", ("reboot", "control", GOOD)], order)
        check("_do_ota: the record keeps the reboot, the good hash and the ROM-banner boot",
              (rec.get("reboot") or {}).get("found_sha") == GOOD and rec.get("good_elf_sha256") == GOOD
              and len(rec.get("boots") or []) == 1, {k: rec.get(k) for k in ("reboot", "good_elf_sha256")})
        order.clear()
        rec, rc = R._do_ota(args, "fault", good, 1.0, good_sha=GOOD, reboot=False)
        check("_do_ota: without reboot, no reboot", order == ["page", "page"] and rec.get("reboot") is None, order)
    finally:
        R.device_state, R.start_serial, R.post_ota, R.reboot_check, R.log, R.time = real


def reboot_check_cases(R):
    got = []

    def fake_main(argv):
        got.append(list(argv))
        out = argv[argv.index("--out") + 1]
        json.dump({"verdict": "PASS", "found_sha": GOOD}, open(out, "w", encoding="utf-8"))
        return 0
    real_mod, real_runs = sys.modules.get("wican_reboot"), R.RUNS
    try:
        sys.modules["wican_reboot"] = types.SimpleNamespace(main=fake_main)
        R.RUNS = SCRATCH
        rb = R.reboot_check("control", GOOD)
        a = got[0] if got else []
        check("reboot_check: wican_reboot.py asked for the good image by --want",
              "--want" in a and a[a.index("--want") + 1] == "good" and "good=%s" % GOOD in a, a)
        check("reboot_check: its record returned", rb.get("verdict") == "PASS" and rb.get("found_sha") == GOOD, rb)
        got.clear()
        rb = R.reboot_check("control", None)
        check("reboot_check: no good hash -> FAIL, nothing asked", rb.get("verdict") == "FAIL" and not got, (rb, got))
    finally:
        if real_mod is None:
            sys.modules.pop("wican_reboot", None)
        else:
            sys.modules["wican_reboot"] = real_mod
        R.RUNS = real_runs


def fake_bootloader(text, hash_appended=True):
    """An ESP image: the 24-byte header, one segment holding text, padding to the checksum
    at the 16-byte boundary, and the appended sha256."""
    seg = text + b"\x00" * (-len(text) % 4)
    h = bytearray(24)
    h[0], h[1], h[23] = 0xE9, 1, 1 if hash_appended else 0
    body = bytes(h) + (0x3FCD0000).to_bytes(4, "little") + len(seg).to_bytes(4, "little") + seg
    body += b"\x00" * (15 - len(body) % 16) + b"\xaa"
    return body + (hashlib.sha256(body).digest() if hash_appended else b"")


def bootloader_cases(R):
    info = b"I (%lu) boot: Loaded app from partition at offset 0x%lx\x00" \
           b"W (%lu) boot: Falling back to test app as only bootable partition\x00" \
           b"E (%lu) boot: No bootable app partitions in the partition table\x00"
    err = b"E (%lu) boot: No bootable app partitions in the partition table\x00"
    for label, img, level in (("an info bootloader", fake_bootloader(info), "info"),
                              ("an error bootloader", fake_bootloader(err), "error"),
                              ("a bootloader with none of the markers", fake_bootloader(b"nothing"),
                               "none of the markers"),
                              ("no appended hash", fake_bootloader(err, hash_appended=False), "error")):
        f = R.bootloader_facts(img + b"\xff" * 4096)
        check("bootloader_facts: %s: its length" % label, f["image_bytes"] == len(img), (f["image_bytes"], len(img)))
        check("bootloader_facts: %s: the sha256 of the image only" % label,
              f["sha256"] == hashlib.sha256(img).hexdigest(), f["sha256"])
        check("bootloader_facts: %s: level %s" % (label, level), f["level"] == level, f)
    f = R.bootloader_facts(b"\xff" * 0x8000)
    check("bootloader_facts: an erased read has no image", f["image_bytes"] is None and f["sha256"] is None, f)
    f = R.bootloader_facts(fake_bootloader(info)[:40])
    check("bootloader_facts: a cut image has no image", f["image_bytes"] is None, f)
    # phase_bootloader records them, from a dump given with --bootloader-file
    real = (R.RUNS, R.log)
    runs = os.path.join(SCRATCH, "runs")
    shutil.rmtree(runs, ignore_errors=True)
    os.makedirs(runs)
    fw = os.path.join(SCRATCH, "fw")
    os.makedirs(os.path.join(fw, "build", "bootloader"), exist_ok=True)
    img = fake_bootloader(err)
    open(os.path.join(fw, "build", "bootloader", "bootloader.bin"), "wb").write(img)
    dump = os.path.join(SCRATCH, "dump.bin")
    open(dump, "wb").write(img + b"\xff" * (0x8000 - len(img)))
    try:
        R.RUNS, R.log = runs, (lambda m: None)
        R.phase_bootloader(types.SimpleNamespace(fw=fw, bootloader_file=dump))
        recs = sorted(f for f in os.listdir(runs) if f.startswith("rollback_bootloader_2"))
        rec = json.load(open(os.path.join(runs, recs[-1]), encoding="utf-8")) if recs else {}
        dev = rec.get("device_bootloader") or {}
        check("phase_bootloader: the record names the device bootloader's sha256 and level",
              dev.get("sha256") == hashlib.sha256(img).hexdigest() and dev.get("level") == "error", dev)
    finally:
        R.RUNS, R.log = real


def mac_cases(R):
    """The otadata and bootloader phases compare esptool's MAC with the expected unit's
    value-blind: neither MAC printed or in the run file (the esptool output kept there has
    it cut out); a match goes on, another unit fails. A fake esptool, a made-up MAC."""
    other_mac = "00:00:5e:00:53:99"
    img = fake_bootloader(b"E (%lu) boot: No bootable app partitions in the partition table\x00")
    real = (R.RUNS, R.log, R.subprocess)
    runs = os.path.join(SCRATCH, "runs_mac")
    for phase, blob in (("bootloader", img + b"\xff" * (0x8000 - len(img))), ("otadata", b"\xff" * 0x2000)):
        for label, mac, want_rc in (("the expected unit", BASE_MAC, 0), ("another unit", other_mac, 1)):
            shutil.rmtree(runs, ignore_errors=True)
            os.makedirs(runs)
            logged = []

            def run(cmd, **kw):
                open(cmd[-1], "wb").write(blob)
                return types.SimpleNamespace(returncode=0, stderr="",
                                             stdout="esptool.py v4.8\nChip is ESP32-C3\nMAC: %s\nRead done\n" % mac)
            try:
                R.RUNS, R.log = runs, logged.append
                R.subprocess = types.SimpleNamespace(run=run, SubprocessError=Exception)
                args = types.SimpleNamespace(fw=os.path.join(SCRATCH, "fw"), bootloader_file=None, otadata_file=None)
                rc = R.phase_bootloader(args) if phase == "bootloader" else R.phase_otadata(args)
            finally:
                R.RUNS, R.log, R.subprocess = real
            recs = sorted(f for f in os.listdir(runs) if f.startswith("rollback_%s_2" % phase))
            rec = open(os.path.join(runs, recs[-1]), encoding="utf-8").read() if recs else ""
            seen = [m for m in (BASE_MAC, other_mac, BASE_MAC.replace(":", "")) if m in rec + "\n".join(logged)]
            check("%s phase, %s: exit %d" % (phase, label, want_rc), rc == want_rc, rc)
            check("%s phase, %s: no MAC printed or in the run file" % (phase, label), recs and not seen, seen or recs)
            check("%s phase, %s: the run file says whether it matched" % (phase, label),
                  ('"mac_matches": %s' % ("true" if want_rc == 0 else "false")) in rec, rec[:200])


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    try:
        R = load()
    except Exception as e:      # noqa: BLE001
        check("the module loads", False, "%s: %s" % (type(e).__name__, e))
        print("\nFAIL, %d FAIL" % len(FAILS))
        return 1
    for part in (boot_cases, control_cases, fault_cases, wiring_cases, bootloader_cases, mac_cases):
        try:
            part(R)
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
