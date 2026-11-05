"""Spec 8.3 flight silence on the TRUCK image (step 4 item 7; the plan's
4b, reviewer 2026-10-13/14): with the host attached, first NOT reading and
then reading, nothing arrives over USB serial after the application starts.
The ROM banner and the second-stage bootloader's lines are out of scope.

Each phase sends esptool's USB-Serial/JTAG hard reset (RTS) on a port held
open with DTR and RTS low (bench_usb_host.HostPort, so neither the open nor
the close resets the board), then:
  noread  reads stopped before the reset, held off --unread-s, then resumed
          for --drain-s: whatever the Windows CDC driver buffered meanwhile;
  read    read throughout, for --read-s.
The lines after the reset are classified (classify()):
  rom     the ROM banner, up to and including its "entry 0x..." line;
  boot    second-stage bootloader log lines (tags boot, boot.esp32s3,
          esp_image, qio_mode);
  app     anything else, blank lines included as their bytes.
A phase PASSES only if the ROM banner was captured (the premise: the host
did receive what the board sent around the reset -- without it, silence
proves nothing and the phase is VOID) and no app byte arrived. Any app line
FAILS.

The application's start is shown on CAN instead: with --pcan the PCAN
acknowledges on CAN1 and records the power-up round (spec 2.3), and 0x7F7
is decoded with the diag DBC (cantools) for the build id, each phase.

Can-fails: --self-test (synthetic streams: an app line after the banner
FAILS, banner-only PASSES, nothing at all is VOID, a bootloader line is not
app); and on the bench, the same run on a bench image, which prints, must
FAIL both phases.

--idf-log judges any image, a bench image's own output included, by spec
8.3's "framework logging is compiled out of every build" (from port step
2a; user, 2026-10-25), in the form the charge-interposer reviewer accepted
for the 2a acceptance list (2026-11-04): the same capture and classes, but
only an app line (or a line before the banner) in a framework log form
FAILS: ESP-IDF's esp_log, "[EWIDV] (<ms>) <tag>:", colour codes allowed; or
the Arduino core's log_x, "[<ms>][EWIDV][<file>:<line>] ". The image's
own lines pass, and the bootloader's are out of scope as before. Without a
complete ROM banner the phase is VOID. --self-test covers it too.

    py -3.14 bench_flight_silence.py --self-test
    py -3.14 bench_flight_silence.py --port COM3 --pcan --out <file.txt>
    py -3.14 bench_flight_silence.py --idf-log --port COM3 --out <file.txt>

Tester, 2026-10-15; --idf-log 2026-11-04.
"""

import argparse
import datetime
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never
# from the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

ROM_LINE = re.compile(r"^(ESP-ROM:|Build:|rst:|Saved PC:|SPIWP:|mode:|load:|"
                      r"entry 0x|ets |boot:0x|SPI_FAST|chksum|csum)")
ROM_END = re.compile(r"^entry 0x[0-9a-fA-F]+")
BOOT_LINE = re.compile(r"^(\x1b\[[0-9;]*m)?[IWE] \(\d+\) "
                       r"(boot|boot\.esp32s3|esp_image|qio_mode):")
STATUS_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
# --idf-log: the framework log forms (spec 8.3)
IDF_LOG = re.compile(r"^(\x1b\[[0-9;]*m)?[EWIDV] \(\d+\) [^\s:]+:")
ARDUHAL_LOG = re.compile(r"^(\x1b\[[0-9;]*m)?\[\s*\d+\]\[[EWIDV]\]\[[^\]]+:\d+\] ")


def classify(lines):
    """lines: the serial lines after the reset (no '# ' marks). -> dict
    with rom / boot / app lists and app_bytes. Lines before the ROM banner's
    first line (a tail of the previous boot's output read late) are 'pre'
    and judged as app too: they arrived after the reset was sent."""
    out = dict(pre=[], rom=[], boot=[], app=[], rom_end=False)
    seen_rom = False
    for ln in lines:
        if not out["rom_end"]:
            if ROM_LINE.match(ln):
                seen_rom = True
                out["rom"].append(ln)
                if ROM_END.match(ln):
                    out["rom_end"] = True
                continue
            if not seen_rom:
                out["pre"].append(ln)
                continue
            # inside the banner, a line the pattern does not know: the ROM
            # prints a few variable ones (e.g. a hex dump) before "entry"
            out["rom"].append(ln)
            continue
        if BOOT_LINE.match(ln):
            out["boot"].append(ln)
        else:
            out["app"].append(ln)
    out["app_bytes"] = sum(len(x.encode("utf-8")) + 1
                           for x in out["app"] + out["pre"])
    return out


def verdict(c):
    """-> (PASS / FAIL / VOID, why)."""
    if c["app"] or c["pre"]:
        first = (c["pre"] + c["app"])[0]
        return ("FAIL", "%d line(s), %d byte(s) after the reset that are "
                "neither the ROM banner nor the bootloader, first %r"
                % (len(c["app"]) + len(c["pre"]), c["app_bytes"],
                   first[:80]))
    if not c["rom_end"]:
        return ("VOID", "no complete ROM banner captured after the reset "
                "(%d banner line(s)): the capture cannot show silence"
                % len(c["rom"]))
    return ("PASS", "ROM banner captured (%d lines), %d bootloader line(s), "
            "0 bytes from the application" % (len(c["rom"]), len(c["boot"])))


def verdict_idf_log(c):
    """--idf-log: -> (PASS / FAIL / VOID, why), failing only framework log
    lines among the app's (spec 8.3)."""
    logs = [ln for ln in c["pre"] + c["app"] if IDF_LOG.match(ln) or ARDUHAL_LOG.match(ln)]
    if logs:
        return ("FAIL", "%d framework log line(s) from the application, first %r"
                % (len(logs), logs[0][:80]))
    if not c["rom_end"]:
        return ("VOID", "no complete ROM banner captured after the reset "
                "(%d banner line(s)): the capture cannot show the absence"
                % len(c["rom"]))
    return ("PASS", "ROM banner captured (%d lines), %d bootloader line(s), "
            "%d application line(s), none in a framework log form"
            % (len(c["rom"]), len(c["boot"]), len(c["app"]) + len(c["pre"])))


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)
    banner = ["ESP-ROM:esp32s3-20210327", "Build:Mar 27 2021",
              "rst:0x15 (USB_UART_CHIP_RESET),boot:0x8 (SPI_FAST_FLASH_BOOT)",
              "Saved PC:0x40378b8e", "SPIWP:0xee",
              "mode:DIO, clock div:1", "load:0x3fce3808,len:0x44c",
              "load:0x403c9700,len:0xbe4", "load:0x403cc700,len:0x2a38",
              "entry 0x403c98d4"]
    v, why = verdict(classify(banner))
    check(v == "PASS", "the ROM banner alone -> PASS -> %s" % why)
    v, why = verdict(classify(banner + ["I (24) boot: ESP-IDF v4.4.7",
                                        "I (31) esp_image: segment 0"]))
    check(v == "PASS", "bootloader lines are out of scope -> PASS")
    v, why = verdict(classify(banner + ["Vtrux charge interposer"]))
    check(v == "FAIL" and "Vtrux" in why,
          "can-fail: the bench image's boot line after the banner -> FAIL "
          "-> %s" % why)
    v, why = verdict(classify(banner + [""]))
    check(v == "FAIL", "can-fail: a blank line from the app is a byte -> "
          "FAIL")
    v, why = verdict(classify(banner + ["I (300) cpu_start: Pro cpu up."]))
    check(v == "FAIL", "can-fail: an app-side esp_log line (cpu_start) -> "
          "FAIL")
    v, why = verdict(classify([]))
    check(v == "VOID", "can-fail: nothing at all -> VOID, never PASS -> %s"
          % why)
    v, why = verdict(classify(banner[:4]))
    check(v == "VOID", "a banner cut short (no entry line) -> VOID")
    v, why = verdict(classify(["t=5s state=MONITOR"] + banner))
    check(v == "FAIL", "can-fail: a line read after the reset but before the "
          "banner -> FAIL")
    # --idf-log (spec 8.3)
    own = ["Vtrux charge interposer", "t=5s state=MONITOR", "",
           "frame E (12) seen", "[bridge] up"]
    for lines, want, what in (
            (banner + own, "PASS", "a bench image's own lines -> PASS"),
            (banner + ["I (24) boot: ESP-IDF v5.4.2", "I (31) esp_image: segment 0"] + own,
             "PASS", "bootloader lines are out of scope -> PASS"),
            (banner + own + ["E (312) gpio: gpio_set_level(227): GPIO output gpio_num error"],
             "FAIL", "can-fail: an ESP-IDF error line -> FAIL"),
            (banner + ["\x1b[0;33mW (55) twai: tx queue full\x1b[0m"], "FAIL",
             "can-fail: a coloured ESP-IDF warning -> FAIL"),
            (banner + ["I (300) cpu_start: Pro cpu up."], "FAIL",
             "can-fail: an app-side ESP-IDF info line (cpu_start) -> FAIL"),
            (banner + ["[   312][E][esp32-hal-gpio.c:102] __pinMode(): Invalid pin"], "FAIL",
             "can-fail: an Arduino core log_e line -> FAIL"),
            (["E (5) spi: before the banner"] + banner, "FAIL",
             "can-fail: a log line before the banner -> FAIL"),
            ([], "VOID", "can-fail: nothing at all -> VOID, never PASS"),
            (banner[:4] + own, "VOID", "a banner cut short -> VOID")):
        v, why = verdict_idf_log(classify(lines))
        check(v == want, "--idf-log: %s -> %s -> %s" % (what, v, why))
    print("\nbench_flight_silence self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def decode_7f7(rows):
    """rows (stamp, host, id, err, data) -> the 0x7F7 decodes seen."""
    import cantools
    import paths
    db = cantools.database.load_file(paths.diag_dbc())
    out = []
    for r in rows:
        if r[2] == 0x7F7 and not r[3]:
            d = db.decode_message(0x7F7, r[4], decode_choices=False)
            out.append("%08x fw %d schema %d up %d min"
                       % (int(d["intp_build_id"]), int(d["intp_fw_ver16"]),
                          int(d["intp_schema_ver_build"]),
                          int(d["intp_uptime_min"])))
    return out


def run_phase(hp, rx, kind, a, log):
    if rx is not None:
        rx.take()
    if kind == "noread":
        hp.stop_reading()
    t0 = hp.hard_reset()
    if kind == "noread":
        time.sleep(a.unread_s)
        hp.resume()
        time.sleep(a.drain_s)
    else:
        time.sleep(a.read_s)
    lines = [s for h, s in hp.stamped if h > t0 and not s.startswith("# ")]
    c = classify(lines)
    v, why = (verdict_idf_log if a.idf_log else verdict)(c)
    log("phase %s: reset at host %.6f; %d line(s) after it" %
        (kind, t0, len(lines)))
    for ln in lines[:40]:
        log("    | %s" % ln[:120])
    log("  %s -- %s" % (v, why))
    if rx is not None:
        rows = rx.take()
        st = [r for r in rows if r[2] in STATUS_IDS and not r[3]]
        ids = sorted(set(r[2] for r in st))
        log("  CAN (PCAN acknowledging): %d status frame(s) after the reset, "
            "ids %s; 0x7F7: %s" % (len(st), " ".join("0x%03X" % i for i in ids)
                                    or "none",
                                    "; ".join(decode_7f7(st)) or "none"))
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--pcan", action="store_true",
                    help="record the power-up round on CAN1 (acknowledging)")
    ap.add_argument("--unread-s", type=float, default=30.0)
    ap.add_argument("--drain-s", type=float, default=5.0)
    ap.add_argument("--read-s", type=float, default=30.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--idf-log", action="store_true",
                    help="fail only framework log lines (spec 8.3), for any image")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    import bench_usb_host as U
    out = open(a.out, "w", encoding="utf-8") if a.out else None

    def log(s):
        print(s)
        if out:
            out.write(s + "\n")
            out.flush()
    log("run at %s; port %s; unread %.0f s + drain %.0f s, read %.0f s"
        % (datetime.datetime.now().isoformat(), a.port, a.unread_s,
           a.drain_s, a.read_s))
    import paths      # no --out: SeaDrive, as before the move, never the tree
    serial_path = (os.path.splitext(a.out)[0] + "_serial.txt") if a.out \
        else os.path.join(paths.fixtures(), "flight_silence_serial.txt")
    rx = None
    if a.pcan:
        rx = U.Rx(U.open_pcan(True), "pcan")
    hp = U.HostPort(a.port, serial_path)
    verdicts = []
    try:
        time.sleep(1.0)
        verdicts.append(run_phase(hp, rx, "noread", a, log))
        time.sleep(2.0)
        verdicts.append(run_phase(hp, rx, "read", a, log))
    finally:
        hp.close()
        if rx is not None:
            rx.close()
    overall = ("FAIL" if "FAIL" in verdicts else
               "VOID" if "VOID" in verdicts else "PASS")
    log("serial record: %s" % serial_path)
    log("8.3 %s: %s" % ("no framework log lines (--idf-log)" if a.idf_log
                        else "flight silence", overall))
    if out:
        out.close()
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
