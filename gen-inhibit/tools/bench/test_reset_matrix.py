"""Tests of reset_matrix.boots() and dumps(): the console readings the matrix rests on.

Each case is a synthetic COM4 transcript in the diag image's own line formats.
The point of the cases is that each sign of a boot is enough ON ITS OWN, so a
boot whose ROM lines were lost while the port was down, or whose reason line
was lost, is still counted -- and that one boot's three signs are not counted
as three boots.

The mutation step swaps in boots() variants that each drop one rule and
requires every variant to get at least one case wrong, so each rule is tested.
boot_count_note (port step 1a2): ROM banner lines alone make the count a lower
bound, said in the run file and printed as a WARNING; a log-tick, reason or uptime
line each takes it away. Its three mutants are each caught.

  py -3.14 gen-inhibit/tools/bench/test_reset_matrix.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reset_matrix as M                                        # noqa: E402

ROM = ["ESP-ROM:esp32c3-api1-20210207",
       "Build:Feb  7 2021",
       "rst:0x15 (USB_UART_CHIP_RESET),boot:0xe (SPI_FAST_FLASH_BOOT)"]
POR = ["ESP-ROM:esp32c3-api1-20210207",
       "rst:0x1 (POWERON),boot:0xe (SPI_FAST_FLASH_BOOT)"]


def reason(name, n):
    return "GI_DIAG: DIAG BUILD, NOT FOR VEHICLE. reset reason %s (%d)" % (name, n)


def ups(k, name, n, start=1):
    return ["GI_DIAG: up %d s, uptime %d ms, last reset %s (%d)"
            % (i, i * 1000 + 280, name, n) for i in range(start, start + k)]


def tick(ms, text):
    return ["[0;32mI (%d) %s[0m" % (ms, text)]


def timed(*blocks):
    out, t = [], 0.0
    for b in blocks:
        for x in b:
            t += 0.01
            out.append((round(t, 3), x))
        t += 5.0
    return out


CASES = [
    ("one full boot is one boot",
     timed(ROM + ["I (281) boot: ..."] + [reason("USB", 11)] + ups(30, "USB", 11)),
     [("USB", "0x15 USB_UART_CHIP_RESET")]),
    ("two full boots",
     timed(POR + [reason("POWERON", 1)] + ups(5, "POWERON", 1),
           POR + [reason("POWERON", 1)] + ups(3, "POWERON", 1)),
     [("POWERON", "0x1 POWERON"), ("POWERON", "0x1 POWERON")]),
    ("second boot with ROM lines lost: reason line alone",
     timed(POR + [reason("POWERON", 1)] + ups(5, "POWERON", 1),
           [reason("BROWNOUT", 15)] + ups(2, "BROWNOUT", 15)),
     [("POWERON", "0x1 POWERON"), ("BROWNOUT", None)]),
    ("second boot with ROM and reason lost: uptime restarts",
     timed(POR + [reason("POWERON", 1)] + ups(8, "POWERON", 1),
           ups(4, "POWERON", 1)),
     [("POWERON", "0x1 POWERON"), ("POWERON", None)]),
    ("second boot seen only by its ROM banner (reboot before any diag line)",
     timed(POR + [reason("POWERON", 1)] + ups(3, "POWERON", 1), POR),
     [("POWERON", "0x1 POWERON"), (None, "0x1 POWERON")]),
    ("second boot's ESP-ROM line lost, its rst: line kept",
     timed(ROM + [reason("USB", 11)] + ups(4, "USB", 11),
           [POR[1], reason("POWERON", 1)] + ups(2, "POWERON", 1)),
     [("USB", "0x15 USB_UART_CHIP_RESET"), ("POWERON", "0x1 POWERON")]),
    ("two ROM banners back to back, nothing between",
     timed(POR, POR),
     [(None, "0x1 POWERON"), (None, "0x1 POWERON")]),
    ("a71b90a boot loop, ROM lost: the log tick restarts (twice)",
     timed(POR + tick(24, "boot: ESP-IDF") + tick(266, "boot: Loaded app") +
           tick(571, "phy_init: phy_version"),
           tick(246, "config_server_init: Partition size") +
           tick(571, "phy_init: phy_version"),
           tick(246, "config_server_init: Partition size") +
           tick(697, "read_ss_adc_voltage: Sample[1]")),
     [(None, "0x1 POWERON"), (None, None), (None, None)]),
    ("spliced lines across port drops: each restarts the tick",
     timed(POR + tick(24, "boot: x") + tick(571, "phy_init: phy_version"),
           ["[0;32mI (246) config_server_init: Partition size: total: 2"
            "[0;32mI (571) phy_init: phy_version 1200"],
           ["[0;32mI (246) config_server_init: Partition size: total: 2"
            "[0;32mI (571) phy_init: phy_version 1200"] + tick(690, "wifi")),
     [(None, "0x1 POWERON"), (None, None), (None, None)]),
    ("small tick inversion inside one boot is not a boot",
     timed(POR + tick(300, "a") + tick(290, "b") + tick(310, "c")),
     [(None, "0x1 POWERON")]),
    ("log lines from an already-running image are not a boot",
     timed(tick(5000, "x") + tick(5100, "y")),
     []),
    ("a stale mid-run uptime line before a ROM banner is not a boot",
     timed(["GI_DIAG: up 26 s, uptime 26002 ms, last reset ESP_RST_USB (11)"] + ROM +
           tick(24, "boot: x") + tick(651, "app_main: Running firmware version: a71b90a")),
     [(None, "0x15 USB_UART_CHIP_RESET")]),
    ("a boot seen from 'up 1 s' alone still counts",
     timed(ups(3, "POWERON", 1)),
     [("POWERON", None)]),
    ("non-diag chatter is not a boot",
     timed(["I (12) wifi: started", "random text rst: none", "GI_DIAG: something"]),
     []),
]

DUMP = ["Guru Meditation Error: Core  0 panic'ed (Load access fault).",
        "Core  0 register dump:", "MEPC    : 0x420145e7",
        "Backtrace: 0x420145e7:0x3fc9a000"]


def summary(bs):
    return [(b["reason"], b["rst"]) for b in bs]


def run(fn, verbose):
    bad = []
    for label, lines, want in CASES:
        got = summary(fn(lines))
        if got != want:
            bad.append(label)
        if verbose:
            print("  %-4s %-66s %s" % ("ok" if got == want else "FAIL", label,
                                       "" if got == want else "got %s" % got))
    return bad


def mutant(rule):
    """boots() with one rule removed, built by editing its own source."""
    import inspect
    src = inspect.getsource(M.boots)
    repl = {
        "rom-after-stage": ('(cur["reason_line"] or cur["up_last"] is not None or',
                            '(False or'),
        "second-esp-rom": ('(x.startswith("ESP-ROM") and cur["esp_rom"]))', 'False)'),
        "reason-again": ('new = cur["reason_line"]', 'new = False'),
        "uptime-back": ('int(up.group(1)) < cur["up_last"]', 'False'),
        "tick-back": ('tick < cur["tick_last"] - TICK_BACK_MS', 'False'),
        "stale-uptime": ('(tk or int(up.group(1)) > 1)', '(tk or False)'),
        "all-ticks": ('ticks = [int(v) for v in TICK_ANY_RE.findall(x)] or [tick]',
                      'ticks = [tick]'),
    }[rule]
    assert src.count(repl[0]) == 1, rule
    ns = dict(vars(M))
    exec(src.replace(repl[0], repl[1]), ns)
    return ns["boots"]


def witness_cases():
    """A/B/E must have NO acker and must not open the Kvaser; C/D must."""
    out = []
    for case in ("A", "B", "E"):
        p = M.witness_plan(case)
        out.append(("case %s: nobody ACKs, PCAN listen-only witness" % case,
                    p["acker"] is None and p["witness"] == "pcan"
                    and p["witness_mode"] == "listen-only"))
    out.append(("case C: the Kvaser ACKs", M.witness_plan("C")["acker"] == "kvaser"))
    for case in ("A", "B"):
        try:
            p = M.witness_plan(case, True)
        except ValueError:
            p = {"acker": None, "witness": None, "witness_mode": None}
        out.append(("case %s --kvaser-acks: the Kvaser witnesses and ACKs" % case,
                    p["acker"] == "kvaser" and p["witness"] == "kvaser"
                    and p["witness_mode"] == "normal"))
    for case in ("C", "D", "E"):
        try:
            M.witness_plan(case, True)
            ok = False
        except ValueError:
            ok = True
        out.append(("case %s --kvaser-acks refused" % case, ok))
    import inspect
    out.append(("main passes --kvaser-acks to witness_plan",
                "witness_plan(a.case, a.kvaser_acks)" in inspect.getsource(M.main)))
    out.append(("case D: the PCAN replays", M.witness_plan("D")["replay"] == "pcan"))
    return out


def power_cases():
    """--power is required and is what the run file records (it was a
    hard-coded 'USB only (PC)' until the rig gained a 12 V supply)."""
    out = []
    try:
        M.build_parser().parse_args(["--case", "baseline"])
        out.append(("--power is required", False))
    except SystemExit:
        out.append(("--power is required", True))
    a = M.build_parser().parse_args(["--case", "baseline", "--power", "12V+USB"])
    out.append(("--power parsed", a.power == "12V+USB"))
    import inspect
    src = inspect.getsource(M.main)
    out.append(("run file records a.power, not a literal",
                '"power": a.power' in src and "USB only (PC)" not in src))
    return out


def note_cases(fn=None):
    """boot_count_note (port step 1a2): a capture with only ROM banner lines (an image at
    log level none, a bootloader at error) says the count is a lower bound; any log-tick,
    reason or uptime line, each on its own, takes the note away."""
    fn = fn or M.boot_count_note
    out = []
    none_level = timed(ROM, POR)
    n = fn(M.boot_signs(none_level))
    out.append(("ROM banners only: the count is said to be a lower bound",
                bool(n) and "LOWER BOUND" in n and "4 ROM banner line(s)" in n))
    for label, extra in (("a log-tick line", tick(281, "boot: ...")), ("a reason line", [reason("USB", 11)]),
                         ("an uptime line", ups(1, "USB", 11))):
        out.append(("ROM banners and %s: no note" % label, fn(M.boot_signs(timed(ROM + extra, POR))) is None))
    out.append(("an empty capture: the note too (nothing seen is no boot count)", bool(fn(M.boot_signs([])))))
    if fn is M.boot_count_note:
        import inspect
        src = inspect.getsource(M.main)
        out.append(("main records the note in the run file",
                    'rec["boot_count_note"] = boot_count_note(rec["boot_signs"])' in src))
        out.append(("main prints it as a WARNING", 'print("WARNING: " + rec["boot_count_note"])' in src))
    return out


def note_mutant(rule):
    """boot_count_note with one rule removed, built by editing its own source."""
    import inspect
    src = inspect.getsource(M.boot_count_note)
    repl = {
        "tick-ignored": ('if signs["reason"] or signs["uptime"] or signs["tick"]:',
                         'if signs["reason"] or signs["uptime"]:'),
        "never-said": ('    return ("BOOT COUNT', '    return None and ("BOOT COUNT'),
        "said-always": ('if signs["reason"] or signs["uptime"] or signs["tick"]:', 'if False:'),
    }[rule]
    assert src.count(repl[0]) == 1, rule
    ns = dict(vars(M))
    exec(src.replace(repl[0], repl[1]), ns)
    return ns["boot_count_note"]


def main():
    failures = run(M.boots, True)
    for label, ok in note_cases():
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)
    for rule in ("tick-ignored", "never-said", "said-always"):
        caught = [l for l, ok in note_cases(note_mutant(rule)) if not ok]
        print("  %-4s note mutant %-14s gets %d case(s) wrong" % ("ok" if caught else "FAIL", rule, len(caught)))
        if not caught:
            failures.append("note mutant %s not caught" % rule)
    for label, ok in power_cases():
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)
    for label, ok in witness_cases():
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)
    d = M.dumps(timed(["I (1) x"] + DUMP + ["more"]))
    ok = len(d) == 3           # Guru, register dump, Backtrace; MEPC is not a sign
    print("  %-4s dump lines found: %d of 3" % ("ok" if ok else "FAIL", len(d)))
    if not ok:
        failures.append("dumps")
    if M.dumps(timed(ROM + [reason("USB", 11)])):
        failures.append("dumps on a clean boot")
    for rule in ("rom-after-stage", "second-esp-rom", "reason-again",
                 "uptime-back", "tick-back", "all-ticks", "stale-uptime"):
        caught = run(mutant(rule), False)
        print("  %-4s mutant without %-16s gets %d case(s) wrong"
              % ("ok" if caught else "FAIL", rule, len(caught)))
        if not caught:
            failures.append("mutant %s not caught" % rule)
    print("%s: %d failure(s)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
