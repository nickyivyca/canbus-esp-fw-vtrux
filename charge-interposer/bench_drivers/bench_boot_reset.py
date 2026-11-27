"""Chip resets 0x15 with no boot panic (spec 9, the X2 settings, core dumps;
tracker I, the charge-interposer reviewer, 2026-11-18).

Spec 9 has the partition table read in ESP-IDF's core startup stage, so the
core dump setup in the secondary stage reads no flash; before that, core 1
panicked "Cache disabled but cached memory region accessed" on almost every
chip reset 0x15 (bench runs 0d0d, b467 and 8b8e). This gives the image on
the X2 --resets chip resets and FAILS on any boot that panics.

Each reset is esptool's USB-Serial/JTAG hard reset (DTR low, RTS high
0.2 s, low), sent on a port held open with DTR and RTS low
(bench_usb_host.HostPort) and read straight through the reset for
--window-s, as run_scenario.SerialTap reads it: on madhouse the port
survives a chip reset, and a port closed and reopened after the reset loses
the ROM banner and any panic (tracker I step 1, 2026-11-18: 0 of 30 seen on
the pre-fix bridge that way, 10 of 10 with the port held open).

Each reset's lines run from its RESET SENT mark to the next mark. Lines
before the ROM's "rst:0x15" line are the previous boot's tail (it can run
into the banner with no newline) and are not judged. From that line on
(judge()):
  VOID  no "rst:0x15" line was read: the reset was not seen, so its silence
        proves nothing;
  FAIL  a panic report ("Guru Meditation Error", "panic'ed", "Backtrace:",
        "abort() was called") or a second ROM reset line ("rst:0x..."): the
        board restarted inside the window;
  FAIL  with --app-line, the image's own boot line not read after the
        "rst:0x15" line (it did not boot to its application). The bridge
        and witness print spec 8.3's banner, "Vtrux charge interposer"; the
        truck prints nothing after its bootloader (spec 8.3), so it is given
        no --app-line and is judged on the panic report and the reset line;
  PASS  otherwise.
The run (overall()) PASSES only if every reset PASSES: any FAIL fails it,
and otherwise any VOID voids it (a run of 0 resets is VOID).

Output: one line per reset and the totals to --out, the stamped serial log
to <out>.serial.log. Exit 0 only on PASS.

Can-fails: --self-test judges synthetic boots (one a real pre-fix panic
capture, trimmed) and --can-fail runs it against MUTANTS
(script_can_fail.py). On the bench, a build with the core startup stage's
partition table read removed must FAIL (the reviewer, 2026-11-18).

    python3.13 -B bench_boot_reset.py --self-test
    python3.13 -B bench_boot_reset.py --port /dev/ttyACM1 --resets 50 \\
        [--window-s 5] [--app-line "Vtrux charge interposer"] --out <file.txt>

Tester, 2026-11-18.
"""

import argparse
import datetime
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (bench_usb_host) first, then charge-interposer/
# (serial_open): both found from this file.
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

RESET_LINE = "rst:0x15"
ANY_RESET = re.compile(r"rst:0x[0-9a-fA-F]+")
PANIC = ("Guru Meditation Error", "panic'ed", "Backtrace:", "abort() was called")
MARK = "# RESET SENT"


def split_resets(lines):
    """lines: the stamped log's text lines in order, marks included. -> one
    list of lines per RESET SENT mark, each up to the next mark."""
    out = []
    for ln in lines:
        if ln.startswith(MARK):
            out.append([])
        elif out and not ln.startswith("# "):
            out[-1].append(ln)
    return out


def judge(lines, app_line=None):
    """One reset's lines. -> (verdict, why)."""
    start = None
    for i, ln in enumerate(lines):
        if RESET_LINE in ln:
            start = i
            break
    if start is None:
        return "VOID", "no %s line read" % RESET_LINE
    after = lines[start:]
    resets = [ln for ln in after if ANY_RESET.search(ln)]
    panics = [ln for ln in after if any(p in ln for p in PANIC)]
    if panics:
        return "FAIL", "panic report: %s" % panics[0].strip()[:120]
    if len(resets) > 1:
        return "FAIL", "the board restarted: %s" % resets[1].strip()[:120]
    if app_line is not None and not any(app_line in ln for ln in after[1:]):
        return "FAIL", "%r not read after %s" % (app_line, RESET_LINE)
    return "PASS", "%s read, no panic report, no second reset line%s" % (
        RESET_LINE, ", %r read" % app_line if app_line is not None else "")


def overall(verdicts):
    """-> the run's verdict from its resets' verdicts."""
    if not verdicts:
        return "VOID"
    if "FAIL" in verdicts:
        return "FAIL"
    if "VOID" in verdicts:
        return "VOID"
    return "PASS"


def run(a):
    import bench_usb_host
    log = a.out + ".serial.log"
    hp = bench_usb_host.HostPort(a.port, log, lines_low=True)
    try:
        time.sleep(0.5)
        for i in range(a.resets):
            hp.hard_reset()
            time.sleep(a.window_s)
    finally:
        hp.close()
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n").split("\t", 1)[-1] for ln in f]
    return split_resets(lines)


def report(segs, a):
    """-> (exit code, the report's text)."""
    rows, verdicts = [], []
    for i, seg in enumerate(segs, 1):
        v, why = judge(seg, a.app_line)
        verdicts.append(v)
        rows.append("%s reset %d: %s" % (v, i, why))
    v = overall(verdicts)
    n = {k: verdicts.count(k) for k in ("PASS", "FAIL", "VOID")}
    lines = ["# bench_boot_reset %s port %s resets %d window %.1f s app line %r" % (
        datetime.datetime.now().isoformat(timespec="seconds"), a.port, a.resets,
        a.window_s, a.app_line)] + rows + [
        "resets judged %d of %d sent: %d PASS, %d FAIL, %d VOID" % (
            len(segs), a.resets, n["PASS"], n["FAIL"], n["VOID"])]
    if len(segs) != a.resets:
        v = "FAIL" if v == "FAIL" else "VOID"
        lines.append("VOID %d reset mark(s) in the log for %d sent" % (len(segs), a.resets))
    lines.append("%s boot-reset-0x15: %s" % (v, "every reset seen, none panicked"
                                            if v == "PASS" else
                                            "a boot panicked or restarted" if v == "FAIL"
                                            else "not every reset was seen"))
    return (0 if v == "PASS" else 1), "\n".join(lines) + "\n"


# --- self-test ---------------------------------------------------------------------

BANNER = ["ESP-ROM:esp32s3-20210327", "Build:Mar 27 2021",
          "rst:0x15 (USB_UART_CHIP_RESET),boot:0x28 (SPI_FAST_FLASH_BOOT)",
          "Saved PC:0x40379222", "SPIWP:0xee", "mode:DIO, clock div:1",
          "load:0x3fce2820,len:0x1150", "entry 0x403c88b8"]
APP = ["", "Vtrux charge interposer", "bitrate 500000, tick 10 ms",
       "diag st=PASSTHROUGH trip=0 flags=0xc0 ilim=- vmax=0 build=7f197bfd "
       "schema=5"]
# the pre-fix bridge (run 40e6's build), tracker I step 1, trimmed
PANIC_BOOT = ["=0 flags=0x40 ilim=- vmax=0 fw=9 schema=4",
              "diag st=PASSTHROUGH tESP-ROM:esp32s3-20210327", "Build:Mar 27 2021",
              "rst:0x15 (USB_UART_CHIP_RESET),boot:0x28 (SPI_FAST_FLASH_BOOT)",
              "entry 0x403c88b8",
              "Guru Meditation Error: Core  1 panic'ed (Cache disabled but cached "
              "memory region accessed). ", "", "Core  1 register dump:",
              "Backtrace: 0x42002515:0x3fced690 0x403759dd:0x3fced6b0", "",
              "ELF file SHA256: d25f8e773", "", "Rebooting...",
              "ESP-ROM:esp32s3-20210327", "Build:Mar 27 2021",
              "rst:0xc (RTC_SW_CPU_RST),boot:0x28 (SPI_FAST_FLASH_BOOT)",
              "entry 0x403c88b8"] + APP
APPL = "Vtrux charge interposer"


def self_test():
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    v = lambda lines, app=APPL: judge(lines, app)[0]          # noqa: E731
    check(v(BANNER + APP) == "PASS", "a clean boot to the banner PASSES")
    check(v(["diag st=PASSTHROUGH t" + BANNER[0]] + BANNER[1:] + APP) == "PASS",
          "the previous boot's tail run into the ROM banner is not judged")
    check(v(PANIC_BOOT) == "FAIL", "the real pre-fix panic boot FAILS")
    check(v(BANNER + ["Guru Meditation Error: Core  1 panic'ed (x)"] + APP) == "FAIL",
          "a Guru Meditation line FAILS")
    check(v(BANNER + ["abort() was called at PC 0x42001234 on core 0"] + APP) == "FAIL",
          "an abort FAILS")
    check(v(BANNER + ["Backtrace: 0x42002515:0x3fced690"] + APP) == "FAIL",
          "a backtrace FAILS")
    check(v(BANNER + APP + ["ESP-ROM:esp32s3-20210327",
                            "rst:0xc (RTC_SW_CPU_RST),boot:0x28 (SPI_FAST_FLASH_BOOT)"] + APP)
          == "FAIL", "a second reset line with no panic text FAILS")
    check(v(BANNER + APP + ["rst:0x7 (TG0WDT_SYS_RST),boot:0x28"]) == "FAIL",
          "a watchdog reset line FAILS")
    check(v(BANNER + ["I (31) boot: ESP-IDF v5.4.2 2nd stage bootloader"]) == "FAIL",
          "with an app line, a boot that never prints it FAILS")
    check(v(BANNER, None) == "PASS", "with no app line (truck), the banner alone PASSES")
    check(v(BANNER + ["Guru Meditation Error: Core  1 panic'ed (x)"], None) == "FAIL",
          "with no app line (truck), a panic still FAILS")
    check(v(APP) == "VOID", "no rst:0x15 line read is VOID")
    check(v([]) == "VOID", "nothing read is VOID")
    check(v(["Guru Meditation Error: Core  1 panic'ed (x)"] + BANNER + APP) == "PASS",
          "a panic before the rst:0x15 line belongs to the previous boot")
    check(v(BANNER[:2] + ["rst:0xc (RTC_SW_CPU_RST),boot:0x28"] + APP) == "VOID",
          "a boot whose first reset line is not 0x15 is not this reset (VOID)")
    check(v(BANNER + [APPL[:10]]) == "FAIL", "a part of the app line is not the app line")
    check(v(["Vtrux charge interposer"] + BANNER) == "FAIL",
          "the app line before the reset line is the previous boot's")
    segs = split_resets(["x", MARK + " host=1.0", "a", "# READS STOPPED", "b",
                         MARK + " host=2.0", "c"])
    check(segs == [["a", "b"], ["c"]], "the log splits at each RESET SENT mark, other marks dropped")
    check(split_resets([MARK, MARK]) == [[], []], "a reset with no lines is its own empty segment")
    check(overall(["PASS"] * 50) == "PASS", "every reset PASS -> PASS")
    check(overall(["PASS"] * 49 + ["FAIL"]) == "FAIL", "one FAIL among PASSes -> FAIL")
    check(overall(["PASS", "VOID", "FAIL"]) == "FAIL", "a FAIL beats a VOID")
    check(overall(["PASS"] * 49 + ["VOID"]) == "VOID", "one VOID among PASSes -> VOID")
    check(overall([]) == "VOID", "no resets -> VOID")

    class A(object):
        port, resets, window_s, app_line = "p", 2, 5.0, APPL
    rc_ok, t_ok = report([BANNER + APP, BANNER + APP], A)
    rc_bad, t_bad = report([BANNER + APP, PANIC_BOOT], A)
    rc_short, t_short = report([BANNER + APP], A)
    check(rc_ok == 0 and "PASS boot-reset-0x15" in t_ok, "report: two clean resets PASS, exit 0")
    check(rc_bad == 1 and "FAIL reset 2:" in t_bad and "FAIL boot-reset-0x15" in t_bad,
          "report: a panicking reset FAILS, exit 1")
    check(rc_short == 1 and "VOID boot-reset-0x15" in t_short,
          "report: fewer reset marks than resets sent is VOID, exit 1")
    print("\nbench_boot_reset self-test: %s" % ("all checks OK" if not fails
                                              else "%d FAILED" % len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("the reset premise ignored", "    if start is None:\n        return \"VOID\"",
     "    if start is None:\n        start = 0\n    if False:\n        return \"VOID\""),
    ("the previous boot's tail judged", "    after = lines[start:]", "    after = lines"),
    ("panic reports not judged", "    if panics:\n", "    if False:\n"),
    ("Guru Meditation not a panic", "PANIC = (\"Guru Meditation Error\", \"panic'ed\", ",
     "PANIC = ("),
    ("a second reset line not judged", "    if len(resets) > 1:", "    if False:"),
    ("the app line not required", "    if app_line is not None and not any(",
     "    if False and not any("),
    ("the app line read before the reset line",
     "not any(app_line in ln for ln in after[1:])",
     "not any(app_line in ln for ln in lines)"),
    ("any reset line taken as 0x15", "RESET_LINE = \"rst:0x15\"", "RESET_LINE = \"rst:0x\""),
    ("a FAIL among PASSes passes", "    if \"FAIL\" in verdicts:\n        return \"FAIL\"",
     "    if verdicts.count(\"FAIL\") == len(verdicts):\n        return \"FAIL\""),
    ("a VOID among PASSes passes", "    if \"VOID\" in verdicts:\n        return \"VOID\"",
     "    if verdicts.count(\"VOID\") == len(verdicts):\n        return \"VOID\""),
    ("no resets passes", "    if not verdicts:\n        return \"VOID\"",
     "    if not verdicts:\n        return \"PASS\""),
    ("missing reset marks not judged", "    if len(segs) != a.resets:", "    if False:"),
    ("the exit code always 0", "    return (0 if v == \"PASS\" else 1), ", "    return 0, "),
    ("other marks kept in a reset's lines", "        elif out and not ln.startswith(\"# \"):",
     "        elif out:"),
]


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--can-fail"]:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--resets", type=int, default=50)
    ap.add_argument("--window-s", type=float, default=5.0)
    ap.add_argument("--app-line", default=None,
                    help="the image's own boot line, required after each reset "
                    "(the bridge and witness: \"Vtrux charge interposer\"; none for the truck)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.resets < 1:
        ap.error("--resets must be at least 1")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    rc, text = report(run(a), a)
    sys.stdout.write(text)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
