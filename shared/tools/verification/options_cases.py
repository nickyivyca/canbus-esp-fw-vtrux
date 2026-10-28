#!/usr/bin/env python3
"""R18 and R26-R29 options check (vtrux_checks options) on real release builds, shown passing
and failing.

    python -B options_cases.py --root <short folder in scratch/> --out <run folder> --profile <vtrux_checks.json>
                               [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B options_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(a clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses. It makes
the fixture as release_cases.py does if --root has none, and refuses a --root whose fixture
tags older tooling than this checkout's HEAD (SPEC R23's pin). Each case
commits its sdkconfig lines (and, for one, a CMake line) on top of the fixture, commits what
the reconfigure then rewrites (as the fixture itself was made), and runs the release build.
--profile is copied into the project root as vtrux_checks.json after the build, so the
release build sees a clean tree.

The expected result is worked out here, not by the check's reader, rule by rule:
  R18  every profile option not at the profile's value ("unset" being absent or off) in a build
       folder's config/sdkconfig.json, an option absent from a folder other than the app's
       (build) not judged there (ruled 2026-10-27; sdkconfig.h, read here before, leaves out an
       option that is off, so it cannot tell off from absent)
  R29  (when the build downloaded the board, managed_components/vtrux__wican_v3/) each option
       its sdkconfig.board fixes, judged as R18 judges the profile; and each setting board.json
       requires of the project: an option the profile does not name or the app's folder lacks,
       or a choice of which the profile sets other than exactly one member true, of which the
       app's folder has no member, or of which a folder having a member has other than exactly
       one member true (rulings of 2026-10-27)
  R26  each of R26's four options defined in a build folder's sdkconfig.h
  R28  each of R28's three levels not at its value in a build folder's sdkconfig.h, and each
       of CORE_DEBUG_LEVEL and LOG_LOCAL_LEVEL found as -D in a build folder's
       compile_commands.json (a plain text search here)
  R27  CONFIG_FREERTOS_HZ missing from the profile, or not an integer
The check must name exactly those failures under each rule, print PASS for each rule with
none, and exit 1 when any rule has one. A case whose trigger does not show is VOID.

    release_pass        the fixture as it is (gen-inhibit's log levels at info, known anomaly 5
                        of its port): R18, R26, R27 and R29 pass, R28 fails on the levels; and
                        the profile sets an option true that the bootloader's folder lacks
                        (BT_ENABLED), so R18 passing shows the by-folder reading
    bootloader_missing  then build/bootloader/config/sdkconfig.json moved aside: fails, naming
                        the folder (restored after)
    repro_off           CONFIG_APP_REPRODUCIBLE_BUILD back off: R18 fails on it
    rollback_off        CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE off: R18 fails on it
    wdt_panic_on        CONFIG_ESP_TASK_WDT_PANIC on: R18 fails on it
    free_option         CONFIG_LOG_COLORS off, an option the profile does not name: R18 passes
    r26_efuse_virtual   CONFIG_EFUSE_VIRTUAL on: R26 fails on it (and R18, whose profile names it)
    r28_levels_none     ESP-IDF's default level none and the bootloader's error: R28 passes
    r28_define          r28_levels_none plus LOG_LOCAL_LEVEL defined in main/CMakeLists.txt with
                        target_compile_definitions: R28 fails on the -D alone
    r27_no_tick         release_pass's build, judged against the profile without
                        CONFIG_FREERTOS_HZ: R27 fails (needs release_pass in the same batch)
    r29_unnamed_choice  the same, the profile without CONFIG_ESP_CONSOLE_SECONDARY_NONE: R29
                        fails on the secondary console, R18 passes
    r29_unnamed_option  the same, the profile without CONFIG_BT_ENABLED: R29 fails on Bluetooth
    r29_two_on          the same, the profile also setting CONFIG_ESP_CONSOLE_UART_DEFAULT true:
                        R29 fails on the console, R18 on that option
    r29_fixed_off       CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM off, which the board fixes on: R29
                        fails on it in the app's folder, and not in the bootloader's, which lacks it

R26 is shown on CONFIG_EFUSE_VIRTUAL rather than CONFIG_SECURE_BOOT, which needs a signing key
for the build to finish. Before each case it checks the bench lock, the "No builds" line and
running simulators, as release_cases.py does.
"""

import json
import os
import platform
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402

TOOLS = RC.TOOLS
_DEFINE = re.compile(r"^#define (CONFIG_[A-Z0-9_]+) (.*)$", re.M)
_NAMED = re.compile(r"^    (\S+): (CONFIG_[A-Z0-9_]+|-D[A-Z_]+) ")
RULES = ("R18", "R26", "R27", "R28", "R29")     # R29 judged when expected() is given the board
APP = "build"       # the app's build folder's label
_FOLDER = re.compile(r"^build(/\S+)?$")
R26_NAMES = ("CONFIG_SECURE_BOOT", "CONFIG_SECURE_FLASH_ENC_ENABLED", "CONFIG_FLASH_ENCRYPTION_ENABLED",
             "CONFIG_EFUSE_VIRTUAL")
R28_LEVELS = {"CONFIG_LOG_DEFAULT_LEVEL": 0, "CONFIG_LOG_MAXIMUM_LEVEL": 0, "CONFIG_BOOTLOADER_LOG_LEVEL": 1}
R28_DEFINES = ("CORE_DEBUG_LEVEL", "LOG_LOCAL_LEVEL")
TICK = "CONFIG_FREERTOS_HZ"
LEVELS_NONE = [("# CONFIG_LOG_DEFAULT_LEVEL_NONE is not set", "CONFIG_LOG_DEFAULT_LEVEL_NONE=y"),
               ("CONFIG_LOG_DEFAULT_LEVEL_INFO=y", "# CONFIG_LOG_DEFAULT_LEVEL_INFO is not set"),
               ("# CONFIG_BOOTLOADER_LOG_LEVEL_ERROR is not set", "CONFIG_BOOTLOADER_LOG_LEVEL_ERROR=y"),
               ("CONFIG_BOOTLOADER_LOG_LEVEL_INFO=y", "# CONFIG_BOOTLOADER_LOG_LEVEL_INFO is not set")]
DEFINE_LINE = "target_compile_definitions(${COMPONENT_LIB} PRIVATE LOG_LOCAL_LEVEL=0)"
TWAI_ON = "CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM=y"        # the board's sdkconfig.board fixes it on
TWAI_OFF = "# CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM is not set"


# ---------- judges (pure; --self-test runs them on fakes) ----------

def header_meets(want, defines, name):
    """want from the profile; defines: {name: text} from one sdkconfig.h."""
    raw = defines.get(name)
    if want == "unset":
        return raw is None
    if raw is None:
        return False
    if want is True:
        return raw == "1"
    if isinstance(want, int):
        try:
            return int(raw, 0) == want
        except ValueError:
            return False
    return raw == json.dumps(want)


ABSENT = object()


def json_meets(want, got):
    """want from the profile or sdkconfig.board; got from one config/sdkconfig.json (ABSENT when
    the folder has no such option). "unset" is absent or off (SPEC R18)."""
    if want == "unset":
        return got is ABSENT or got is False
    if want is True:
        return got is True
    if isinstance(want, int):
        return type(got) is int and got == want
    return isinstance(got, str) and got == want


def board_fixed(text):
    """{CONFIG_<NAME>: value} from an sdkconfig.board's text: y is true, n or "is not set" is
    "unset", a quoted value a string, anything else a number (0x as hex)."""
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        m = re.match(r"^# (CONFIG_[A-Z0-9_]+) is not set$", line)
        if m:
            out[m.group(1)] = "unset"
            continue
        m = re.match(r"^(CONFIG_[A-Z0-9_]+)=(.*)$", line)
        if m:
            v = m.group(2)
            out[m.group(1)] = True if v == "y" else "unset" if v == "n" else v[1:-1] if v.startswith('"') else int(v, 0)
    return out


def expected_r18(profile, options, app):
    """SPEC R18 as ruled 2026-10-27: an option the profile sets is in the app's folder with that
    value, and in another folder has it where the folder has the option at all. options: {folder:
    {name without CONFIG_: value}} from each folder's config/sdkconfig.json. [(folder, name)]."""
    bad = []
    for f, o in sorted(options.items()):
        for n, v in sorted(profile.items()):
            got = o.get(n[len("CONFIG_"):], ABSENT)
            if got is ABSENT and f != app:
                continue
            if not json_meets(v, got):
                bad.append((f, n))
    return bad


def expected_r29(fixed, required, profile, options, app):
    """SPEC R29 (with its rulings of 2026-10-27): each sdkconfig.board option judged as R18 judges
    the profile, a failure (folder, name); and each required setting, a failure ("required",
    setting): an option the profile does not name or the app's folder lacks; a choice of which
    the profile sets other than exactly one member true, of which the app's folder has no member,
    or of which a folder having a member has other than exactly one member true."""
    bad = expected_r18(fixed, options, app)
    app_o = options.get(app, {})
    for r in required:
        s = r["setting"]
        if "option" in r:
            if r["option"] not in profile or r["option"][len("CONFIG_"):] not in app_o:
                bad.append(("required", s))
            continue
        keys = [m[len("CONFIG_"):] for m in r["choice"]]
        named = [m for m in r["choice"] if profile.get(m) is True]
        folders_bad = [f for f, o in options.items() if any(k in o for k in keys)
                       and len([k for k in keys if o.get(k) is True]) != 1]
        if len(named) != 1 or not any(k in app_o for k in keys) or folders_bad:
            bad.append(("required", s))
    return sorted(set(bad))


def command_defines(commands):
    """{folder: compile_commands.json's list}: [(folder, "-D" + name)] for each of R28's names
    passed with -D, by a plain search of each command's text."""
    out = set()
    for folder, entries in commands.items():
        for e in entries:
            text = e.get("command") or " ".join(e.get("arguments") or [])
            for name in R28_DEFINES:
                if re.search(r"-D\s*%s(?![A-Za-z0-9_])" % name, text):
                    out.add((folder, "-D" + name))
    return sorted(out)


def expected(profile, headers, commands=None, options=None, app=APP, board=None):
    """profile: {name: value}; headers: {folder: defines}; commands: {folder: compile commands};
    options: {folder: sdkconfig.json's options}, for R18 (sdkconfig.h leaves out an option that
    is off, so it cannot tell off from absent) and R29; board: (sdkconfig.board's options,
    board.json's required) for R29, judged only when given. {rule: sorted failures}: (folder,
    name) pairs, ("required", setting) for R29, and [TICK] for R27."""
    tick = profile.get(TICK)
    want = {
        "R18": expected_r18(profile, options or {}, app),
        "R26": sorted((f, n) for f, d in headers.items() for n in R26_NAMES if not header_meets("unset", d, n)),
        "R27": [] if isinstance(tick, int) and not isinstance(tick, bool) else [TICK],
        "R28": sorted([(f, n) for f, d in headers.items() for n, v in R28_LEVELS.items() if not header_meets(v, d, n)]
                      + command_defines(commands or {})),
    }
    if board is not None:
        want["R29"] = expected_r29(board[0], board[1], profile, options or {}, app)
    return want


def parse(out):
    """{rule: (passed, [named failures])} from the check's output: a rule's failures are the
    indented "folder: name" lines under its FAIL line; R27 names its option on the FAIL line."""
    res, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^(PASS|FAIL) (R\d\d)\b", line)
        if m:
            cur = m.group(2) if m.group(1) == "FAIL" else None
            res[m.group(2)] = (m.group(1) == "PASS", [TICK] if (m.group(2) == "R27" and m.group(1) == "FAIL") else [])
            continue
        n = _NAMED.match(line)
        if n and cur in ("R18", "R26", "R28", "R29") and _FOLDER.match(n.group(1)):
            res[cur][1].append((n.group(1), n.group(2)))
        elif cur == "R29" and line.startswith("    ") and ": " in line:
            res[cur][1].append(("required", line[4:].split(": ", 1)[0]))
        elif not line.startswith("    "):
            cur = None
    return {r: (p, sorted(set(names))) for r, (p, names) in res.items()}


def judge(rc, out, want):
    """(verdict, detail): want is expected()'s {rule: failures}."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    rules = [r for r in RULES if r in want]
    any_fail = any(want[r] for r in rules)
    if rc != (1 if any_fail else 0):
        return "FAIL", "the check exited %d, wanted %d" % (rc, 1 if any_fail else 0)
    got = parse(out)
    for r in rules:
        if r not in got:
            return "FAIL", "the check printed no PASS or FAIL line for %s" % r
        passed, named = got[r]
        if passed != (not want[r]):
            return "FAIL", "%s %s, wanted %s" % ("PASS" if passed else "FAIL", r, "PASS" if not want[r] else "FAIL")
        if named != want[r]:
            return "FAIL", "%s names %s, wanted %s" % (r, named, want[r])
    return "PASS", "exit %d; %s" % (rc, "; ".join("%s %s" % (r, "PASS" if not want[r] else "FAIL on " + ", ".join(
        " ".join(x) if isinstance(x, tuple) else x for x in want[r])) for r in rules))


def judge_unreadable(rc, out, folder):
    if rc != 1 or "FAIL R18: cannot read" not in out:
        return "FAIL", "exit %d; wanted 1 with 'cannot read'" % rc
    if folder not in out:
        return "FAIL", "exit 1, but the output does not name %s" % folder
    return "PASS", "exit 1, cannot read, names %s" % folder


def self_test():
    ok = True
    prof = {"CONFIG_A": True, "CONFIG_B": "unset", "CONFIG_N": 3, "CONFIG_S": "x", TICK: 1000}
    good = {"CONFIG_A": "1", "CONFIG_N": "0x3", "CONFIG_S": '"x"', "CONFIG_FREE": "1", TICK: "1000",
            "CONFIG_LOG_DEFAULT_LEVEL": "0", "CONFIG_LOG_MAXIMUM_LEVEL": "0", "CONFIG_BOOTLOADER_LOG_LEVEL": "1"}
    none = {"R18": [], "R26": [], "R27": [], "R28": []}
    jgood = {"A": True, "B": False, "N": 3, "S": "x", "FREE": True, "FREERTOS_HZ": 1000}
    for label, opts, want in (("all at value", {"build": jgood}, []),
                              ("an unset option absent", {"build": {k: v for k, v in jgood.items() if k != "B"}}, []),
                              ("a set option absent from the app", {"build": {k: v for k, v in jgood.items() if k != "A"}},
                               [("build", "CONFIG_A")]),
                              ("a set option off", {"build": dict(jgood, A=False)}, [("build", "CONFIG_A")]),
                              ("an unset option on", {"build": dict(jgood, B=True)}, [("build", "CONFIG_B")]),
                              ("a number differs", {"build": dict(jgood, N=4)}, [("build", "CONFIG_N")]),
                              ("a number as a string", {"build": dict(jgood, N="3")}, [("build", "CONFIG_N")]),
                              ("true as 1", {"build": dict(jgood, A=1)}, [("build", "CONFIG_A")]),
                              ("absent from the bootloader, not judged there",
                               {"build": jgood, "build/bootloader": {"N": 3}}, []),
                              ("off in the bootloader", {"build": jgood, "build/bootloader": {"A": False}},
                               [("build/bootloader", "CONFIG_A")]),
                              ("another number in the bootloader", {"build": jgood, "build/bootloader": {"N": 4}},
                               [("build/bootloader", "CONFIG_N")])):
        got = expected_r18(prof, opts, APP)
        print("  %s expected_r18(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    fixed = board_fixed('# c\nCONFIG_T="esp32c3"\nCONFIG_F=y\nCONFIG_O=0x8000\n# CONFIG_U is not set\nCONFIG_W=y\n')
    req = [{"setting": "the console", "choice": ["CONFIG_C_A", "CONFIG_C_B"]},
           {"setting": "the second console", "choice": ["CONFIG_S_N", "CONFIG_S_J"]},
           {"setting": "wdt", "option": "CONFIG_D"}]
    rprof = {"CONFIG_C_B": True, "CONFIG_S_N": True, "CONFIG_D": "unset"}
    app_o = {"T": "esp32c3", "F": True, "O": 32768, "U": False, "W": True, "C_A": False, "C_B": True, "S_N": True, "D": False}
    boot_o = {"T": "esp32c3", "F": True, "O": 32768, "C_A": False, "C_B": True}
    for label, args, want in (
            ("board_fixed reads each form", None, {"CONFIG_T": "esp32c3", "CONFIG_F": True, "CONFIG_O": 32768,
                                                   "CONFIG_U": "unset", "CONFIG_W": True}),
            ("all met", (rprof, {"build": app_o, "build/bootloader": boot_o}), []),
            ("a fixed option off in the app", (rprof, {"build": dict(app_o, W=False)}), [("build", "CONFIG_W")]),
            ("a fixed option absent from the bootloader, not judged", (rprof, {"build": app_o, "build/bootloader": boot_o}), []),
            ("a fixed number differs in the bootloader", (rprof, {"build": app_o, "build/bootloader": dict(boot_o, O=36864)}),
             [("build/bootloader", "CONFIG_O")]),
            ("a fixed unset option on", (rprof, {"build": dict(app_o, U=True)}), [("build", "CONFIG_U")]),
            ("a required option the profile does not name", ({k: v for k, v in rprof.items() if k != "CONFIG_D"},
                                                              {"build": app_o}), [("required", "wdt")]),
            ("a required option absent from the app", (rprof, {"build": {k: v for k, v in app_o.items() if k != "D"}}),
             [("required", "wdt")]),
            ("a choice the profile names none of", (dict(rprof, CONFIG_C_B="unset"), {"build": app_o}),
             [("required", "the console")]),
            ("a choice the profile names two of", (dict(rprof, CONFIG_C_A=True), {"build": app_o}),
             [("required", "the console")]),
            ("a choice member set to 1 in the profile", (dict(rprof, CONFIG_C_B=1), {"build": app_o}),
             [("required", "the console")]),
            ("a choice member absent from the app, not judged", (rprof, {"build": {k: v for k, v in app_o.items() if k != "S_J"}}), []),
            ("a choice with no member in the app", (rprof, {"build": {k: v for k, v in app_o.items() if k not in ("S_N", "S_J")}}),
             [("required", "the second console")]),
            ("a folder with two members on", (rprof, {"build": app_o, "build/bootloader": dict(boot_o, C_A=True)}),
             [("required", "the console")]),
            ("a folder with members, none on", (rprof, {"build": app_o, "build/bootloader": dict(boot_o, C_B=False)}),
             [("required", "the console")]),
            ("a folder without the choice, not judged", (rprof, {"build": app_o, "build/bootloader": {"T": "esp32c3"}}), [])):
        got = fixed if args is None else expected_r29(fixed, req, args[0], args[1], APP)
        print("  %s R29, %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    hdr_cases = (("all at value", {"build": good}, None, prof, none),
                 ("an R26 option on", {"build": dict(good, CONFIG_EFUSE_VIRTUAL="1")}, None, prof,
                  dict(none, R26=[("build", "CONFIG_EFUSE_VIRTUAL")])),
                 ("a level at info", {"build": dict(good, CONFIG_LOG_DEFAULT_LEVEL="3")}, None, prof,
                  dict(none, R28=[("build", "CONFIG_LOG_DEFAULT_LEVEL")])),
                 ("a level missing", {"build": dict(good, CONFIG_BOOTLOADER_LOG_LEVEL=None)}, None, prof,
                  dict(none, R28=[("build", "CONFIG_BOOTLOADER_LOG_LEVEL")])),
                 ("a -D in a command", {"build": good}, {"build": [{"command": "gcc -DLOG_LOCAL_LEVEL=0 -c a.c"},
                                                                    {"arguments": ["gcc", "-D", "CORE_DEBUG_LEVEL"]}]}, prof,
                  dict(none, R28=[("build", "-DCORE_DEBUG_LEVEL"), ("build", "-DLOG_LOCAL_LEVEL")])),
                 ("a longer name is not the define", {"build": good}, {"build": [{"command": "gcc -DLOG_LOCAL_LEVEL_X"}]},
                  prof, none),
                 ("no tick in the profile", {"build": good}, None, {k: v for k, v in prof.items() if k != TICK},
                  dict(none, R27=[TICK])))
    for label, headers, cmds, pr, want in hdr_cases:
        headers = {f: {k: v for k, v in d.items() if v is not None} for f, d in headers.items()}
        got = expected(pr, headers, cmds)
        print("  %s expected(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    allpass = "R18 ...\nPASS R18\nR26 options ...\nPASS R26\nR28 options ...\nPASS R28\nPASS R27: CONFIG_FREERTOS_HZ 1000 (s)\n"
    out1 = allpass.replace("PASS R18\n", "FAIL R18: 1 option(s) not at the profile's value\n"
                           "    build: CONFIG_A is false, the profile says true (s)\n")
    w1 = dict(none, R18=[("build", "CONFIG_A")])
    out28 = allpass.replace("PASS R28\n", "FAIL R28\n    build: CONFIG_LOG_DEFAULT_LEVEL is 3, R28 says 0\n"
                            "    build/bootloader: -DLOG_LOCAL_LEVEL in 2 compile command(s), e.g. a.c\n")
    w28 = dict(none, R28=[("build", "CONFIG_LOG_DEFAULT_LEVEL"), ("build/bootloader", "-DLOG_LOCAL_LEVEL")])
    out27 = allpass.replace("PASS R27: CONFIG_FREERTOS_HZ 1000 (s)\n", "FAIL R27: the profile does not name CONFIG_FREERTOS_HZ\n")
    for label, args, want in (("passes as wanted", (0, allpass, none), "PASS"),
                              ("R18 fails as wanted", (1, out1, w1), "PASS"),
                              ("R28 fails as wanted, a level and a -D", (1, out28, w28), "PASS"),
                              ("R27 fails as wanted", (1, out27, dict(none, R27=[TICK])), "PASS"),
                              ("passes when it should fail", (0, allpass, w1), "FAIL"),
                              ("fails when it should pass", (1, out1, none), "FAIL"),
                              ("names another folder", (1, out1.replace("build:", "build/bootloader:"), w1), "FAIL"),
                              ("names one too many", (1, out1.replace("(s)\n", "(s)\n    build: CONFIG_B is true, x\n", 1), w1),
                               "FAIL"),
                              ("a failure under the wrong rule", (1, out28, dict(none, R26=w28["R28"])), "FAIL"),
                              ("a rule with no line", (0, allpass.replace("PASS R26\n", ""), none), "FAIL"),
                              ("exit 1 with every rule PASS", (1, allpass, none), "FAIL"),
                              ("R27 fails when it should pass", (1, out27, none), "FAIL"),
                              ("crashes", (2, "Traceback", none), "ERROR")):
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    p29 = allpass + "R29 board settings: vtrux/wican_v3 (x)\n  fixed ...\n  required ...\nPASS R29\n"
    f29 = p29.replace("PASS R29\n", "FAIL R29\n    build: CONFIG_W is false, the board's sdkconfig.board says true\n"
                      "    the console: the profile sets 2 of its choice's options true (CONFIG_C_A, CONFIG_C_B); "
                      "it needs exactly one\n    the console: build has 2 of its choice's options true (x)\n")
    w29 = dict(none, R29=[("build", "CONFIG_W"), ("required", "the console")])
    for label, args, want in (("R29 passes as wanted", (0, p29, dict(none, R29=[])), "PASS"),
                              ("R29 fails as wanted, fixed and required", (1, f29, w29), "PASS"),
                              ("R29 fails when it should pass", (1, f29, dict(none, R29=[])), "FAIL"),
                              ("R29 names another setting", (1, f29.replace("the console:", "the second console:"), w29), "FAIL"),
                              ("R29 misses the fixed option", (1, f29.replace("    build: CONFIG_W is false, the board's "
                                                                              "sdkconfig.board says true\n", ""), w29), "FAIL"),
                              ("R29 wanted, no R29 line", (0, allpass, dict(none, R29=[])), "FAIL"),
                              ("R29 cannot read the board", (1, allpass + "FAIL R29: cannot read the board: x\n", w29), "FAIL"),
                              ("R29 not wanted, not judged", (0, p29, none), "PASS")):
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    for label, args, want in (("unreadable, named", (1, "FAIL R18: cannot read ...: build/bootloader: x", "build/bootloader"), "PASS"),
                              ("unreadable, not named", (1, "FAIL R18: cannot read ...: x", "build/bootloader"), "FAIL"),
                              ("passes instead", (0, "PASS R18", "build/bootloader"), "FAIL")):
        v = judge_unreadable(*args)[0]
        print("  %s judge_unreadable(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def check(proj, log):
    e = RC.idf_env(PYTHONPATH=TOOLS)
    return RC.run([sys.executable, "-B", "-m", "vtrux_checks", "options", "--build", os.path.join(proj, "build")],
                  cwd=proj, env=e, log=log)


def build_folders(proj):
    b = os.path.join(proj, "build")
    for d, _, files in os.walk(b):
        if d == b or "CMakeCache.txt" in files:
            yield d, os.path.relpath(d, proj).replace(os.sep, "/")


def headers(proj):
    """{folder label: {name: text}} from config/sdkconfig.h in build/ and in each CMake build
    folder below it (one with its own CMakeCache.txt)."""
    out = {}
    for d, label in build_folders(proj):
        p = os.path.join(d, "config", "sdkconfig.h")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[label] = dict(_DEFINE.findall(f.read()))
    return out


def options_of(proj):
    """{folder label: {name without CONFIG_: value}} from config/sdkconfig.json in the same folders."""
    out = {}
    for d, label in build_folders(proj):
        p = os.path.join(d, "config", "sdkconfig.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out[label] = json.load(f)
    return out


def board_of(proj):
    """(sdkconfig.board's options, board.json's required) of the board the build downloaded,
    managed_components/vtrux__wican_v3/, or None when there is none."""
    d = os.path.join(proj, "managed_components", "vtrux__wican_v3")
    if not os.path.isdir(d):
        return None
    with open(os.path.join(d, "sdkconfig.board"), encoding="utf-8") as f:
        fixed = board_fixed(f.read())
    with open(os.path.join(d, "board.json"), encoding="utf-8") as f:
        return fixed, json.load(f)["required"]


def facts(proj):
    """(headers, commands, options, board) of the build, by the tester's own readings."""
    return headers(proj), commands(proj), options_of(proj), board_of(proj)


def commands(proj):
    """{folder label: compile_commands.json's list} for the same folders."""
    out = {}
    for d, label in build_folders(proj):
        p = os.path.join(d, "compile_commands.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[label] = json.load(f)
    return out


def set_line(proj, old, new, rel="sdkconfig"):
    p = os.path.join(proj, rel)
    lines = open(p, newline="").read().replace("\r\n", "\n").split("\n")
    if lines.count(old) != 1:
        raise RuntimeError("%s: %d '%s' lines" % (rel, lines.count(old), old))
    with open(p, "w", newline="") as f:
        f.write("\n".join(new if x == old else x for x in lines))


def append_line(proj, rel, line):
    p = os.path.join(proj, rel)
    text = open(p, newline="").read()
    with open(p, "w", newline="") as f:
        f.write(text + ("" if text.endswith("\n") else "\n") + line + "\n")


def built(ctx, name, log, changes=None, cmake=None):
    """A release build of the fixture, with changes = [(old line, new line)] in sdkconfig and
    cmake = (file, line to append) committed on top. Returns facts(), or None after reporting
    VOID."""
    RC.reset(ctx.p1, "fixture")
    if changes or cmake:
        RC.git(ctx.p1, "checkout", "-q", "-B", "opt-" + name, "origin/fixture")
        for old, new in changes or []:
            set_line(ctx.p1, old, new)
        if cmake:
            append_line(ctx.p1, *cmake)
        RC.git(ctx.p1, "commit", "-q", "-am", "options case %s" % name)
        with open(log, "a") as fx:
            RC.normalise(ctx, ctx.p1, name, fx)
            fx.write("%s: %s\n" % (name, RC.git(ctx.p1, "rev-parse", "HEAD")))
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return None
    shutil.copyfile(ctx.profile, os.path.join(ctx.p1, "vtrux_checks.json"))
    return facts(ctx.p1)


def run_case(ctx, name, log, changes, premise, cmake=None, prof=None):
    """premise(want, headers, commands, options) -> True when the case's trigger shows in the build."""
    b = built(ctx, name, log, changes, cmake)
    if b is None:
        return
    h, cc, opts, board = b
    want = expected(prof or ctx.prof, h, cc, opts, APP, board)
    with open(log, "a") as f:
        f.write("sdkconfig.h folders %s; compile_commands folders %s; sdkconfig.json folders %s; board %s; expected %s\n"
                % (sorted(h), sorted(cc), sorted(opts), "downloaded" if board else "none", want))
    if not premise(want, h, cc, opts):
        RC.report(name, "VOID", "the build does not show the case's trigger; expected %s" % want, ctx.log)
        return
    rc, out = check(ctx.p1, log)
    v, d = judge(rc, out, want)
    RC.report(name, v, d, ctx.log)
    return True


# ---------- cases ----------

def by_folder(prof, o):
    """The profile's options set true that the app's folder has and the bootloader's lacks, so that
    R18 passing on them shows the by-folder reading (BT_ENABLED in gen-inhibit's, since port step
    1a C)."""
    return [n for n, v in prof.items() if v is True and n[len("CONFIG_"):] in o.get(APP, {})
            and n[len("CONFIG_"):] not in o.get("build/bootloader", {"": 0})]


def c_release_pass(ctx, log):
    ctx.pass_built = run_case(ctx, "release_pass", log, None,
                              lambda w, h, cc, o: "build/bootloader" in h and set(cc) == set(h) and not w["R18"]
                              and not w["R26"] and not w["R27"] and not w.get("R29", ["no board"])
                              and by_folder(ctx.prof, o))


def c_bootloader_missing(ctx, log):
    if not getattr(ctx, "pass_built", False):
        RC.report("bootloader_missing", "VOID", "needs release_pass in the same batch", ctx.log)
        return
    p = os.path.join(ctx.p1, "build", "bootloader", "config", "sdkconfig.json")
    os.replace(p, p + ".aside")
    try:
        rc, out = check(ctx.p1, log)
        RC.report("bootloader_missing", *judge_unreadable(rc, out, "build/bootloader"), ctx.log)
    finally:
        os.replace(p + ".aside", p)


def r18(trigger):
    return lambda w, h, cc, o: trigger in w["R18"]


def c_repro_off(ctx, log):
    run_case(ctx, "repro_off", log, [(RC.REPRO_ON, RC.REPRO_OFF)], r18(("build", "CONFIG_APP_REPRODUCIBLE_BUILD")))


def c_rollback_off(ctx, log):
    run_case(ctx, "rollback_off", log, [("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
                                         "# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set")],
             r18(("build/bootloader", "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE")))


def c_wdt_panic_on(ctx, log):
    run_case(ctx, "wdt_panic_on", log, [("# CONFIG_ESP_TASK_WDT_PANIC is not set", "CONFIG_ESP_TASK_WDT_PANIC=y")],
             r18(("build", "CONFIG_ESP_TASK_WDT_PANIC")))


def c_free_option(ctx, log):
    run_case(ctx, "free_option", log, [("CONFIG_LOG_COLORS=y", "# CONFIG_LOG_COLORS is not set")],
             lambda w, h, cc, o: not w["R18"] and "CONFIG_LOG_COLORS" not in h.get("build", {}))


def c_r26_efuse_virtual(ctx, log):
    run_case(ctx, "r26_efuse_virtual", log, [("# CONFIG_EFUSE_VIRTUAL is not set", "CONFIG_EFUSE_VIRTUAL=y")],
             lambda w, h, cc, o: ("build", "CONFIG_EFUSE_VIRTUAL") in w["R26"])


def c_r28_levels_none(ctx, log):
    run_case(ctx, "r28_levels_none", log, LEVELS_NONE,
             lambda w, h, cc, o: not w["R28"] and "build/bootloader" in h and set(cc) == set(h))


def c_r28_define(ctx, log):
    run_case(ctx, "r28_define", log, LEVELS_NONE,
             lambda w, h, cc, o: w["R28"] and all(n.startswith("-D") for _, n in w["R28"]),
             cmake=("main/CMakeLists.txt", DEFINE_LINE))


def profile_case(ctx, log, name, drop=(), add=None, premise=lambda w: True):
    """release_pass's build judged against the profile with the options drop taken out and add
    ({name: value}) put in, each of which must change it (else VOID); premise(want) must hold."""
    if not getattr(ctx, "pass_built", False):
        RC.report(name, "VOID", "needs release_pass in the same batch", ctx.log)
        return
    data = json.load(open(ctx.profile, encoding="utf-8"))
    rp = data["release_profile"]
    if any(n not in rp for n in drop) or any(n in rp for n in add or {}):
        RC.report(name, "VOID", "the profile does not name %s to take out, or names %s already" % (
            list(drop), list(add or {})), ctx.log)
        return
    for n in drop:
        del rp[n]
    for n, v in (add or {}).items():
        rp[n] = {"value": v, "spec": "options case %s" % name}
    p = os.path.join(ctx.p1, "vtrux_checks.json")
    try:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
        prof = {n: e["value"] for n, e in rp.items()}
        h, cc, o, b = facts(ctx.p1)
        want = expected(prof, h, cc, o, APP, b)
        with open(log, "a") as f:
            f.write("%s: profile without %s, with %s; expected %s\n" % (name, list(drop), add or {}, want))
        if not premise(want):
            RC.report(name, "VOID", "the case's trigger does not show; expected %s" % want, ctx.log)
            return
        rc, out = check(ctx.p1, log)
        RC.report(name, *judge(rc, out, want), ctx.log)
    finally:
        shutil.copyfile(ctx.profile, p)


def c_r27_no_tick(ctx, log):
    profile_case(ctx, log, "r27_no_tick", drop=(TICK,), premise=lambda w: w["R27"] == [TICK])


def c_r29_unnamed_choice(ctx, log):
    profile_case(ctx, log, "r29_unnamed_choice", drop=("CONFIG_ESP_CONSOLE_SECONDARY_NONE",),
                 premise=lambda w: w.get("R29") == [("required", "the secondary console")] and not w["R18"])


def c_r29_unnamed_option(ctx, log):
    profile_case(ctx, log, "r29_unnamed_option", drop=("CONFIG_BT_ENABLED",),
                 premise=lambda w: w.get("R29") == [("required", "Bluetooth on or off")] and not w["R18"])


def c_r29_two_on(ctx, log):
    profile_case(ctx, log, "r29_two_on", add={"CONFIG_ESP_CONSOLE_UART_DEFAULT": True},
                 premise=lambda w: w.get("R29") == [("required", "the console")]
                 and ("build", "CONFIG_ESP_CONSOLE_UART_DEFAULT") in w["R18"])


def c_r29_fixed_off(ctx, log):
    run_case(ctx, "r29_fixed_off", log, [(TWAI_ON, TWAI_OFF)],
             lambda w, h, cc, o: w.get("R29") == [("build", "CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM")]
             and "TWAI_ERRATA_FIX_LISTEN_ONLY_DOM" not in o.get("build/bootloader", {"TWAI_ERRATA_FIX_LISTEN_ONLY_DOM": 0}))


CASES = [("release_pass", c_release_pass), ("bootloader_missing", c_bootloader_missing),
         ("r27_no_tick", c_r27_no_tick), ("r29_unnamed_choice", c_r29_unnamed_choice),
         ("r29_unnamed_option", c_r29_unnamed_option), ("r29_two_on", c_r29_two_on), ("r29_fixed_off", c_r29_fixed_off),
         ("repro_off", c_repro_off), ("rollback_off", c_rollback_off), ("wdt_panic_on", c_wdt_panic_on),
         ("free_option", c_free_option), ("r26_efuse_virtual", c_r26_efuse_virtual),
         ("r28_levels_none", c_r28_levels_none), ("r28_define", c_r28_define)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--profile", help="the vtrux_checks.json to judge against")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out and a.profile) or "IDF_PATH" not in os.environ:
        ap.error("--root, --out and --profile, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.profile = os.path.abspath(a.profile)
    ctx.prof = {n: e["value"] for n, e in json.load(open(ctx.profile))["release_profile"].items()}
    ctx.log = os.path.join(ctx.out, "options_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\nprofile %s sha256 %s\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain") or "clean",
            ctx.profile, RC.sha256_file(ctx.profile), names))
    if not os.path.exists(ctx.src):
        if not ctx.made_fixture:
            ap.error("--fixture %s does not exist" % ctx.src)
        RC.make_fixture(ctx)
    else:
        RC.make_pin(ctx)    # the fixture's tag must mark the tooling under test (refuses an older root)
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "options_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    RC.reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.out, "options_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
