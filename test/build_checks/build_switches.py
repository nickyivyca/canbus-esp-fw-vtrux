#!/usr/bin/env python3
"""Spec 12.3 item 3: every build-time switch is classified.

    python3 build_switches.py            # scan this checkout, print every finding

WHAT THE SPEC ASKS (added 2026-10-13, user). Every preprocessor symbol the
product source tests, other than ESP-IDF's `CONFIG_*` and header guards, and
every build option `main/CMakeLists.txt` tests, is listed as one of:

    shipping variant   a configuration that may fly (auto-arm, for example);
    test-only          must never reach a vehicle; has a refusal row in
                       build_output.py that judges the image and the ELF;
    not a switch       a default value, a value the build injects, or a
                       library macro -- each with its reason.

An unlisted one fails the build checks. So does a listed one the source no
longer tests: a renamed switch would otherwise leave a stale entry beside an
unlisted new name, and the stale entry would read as coverage.

Product source is `main/` and `components/` (the repo's own). The downloaded
components (`managed_components/`) and `test/` are not product source (spec
reading, reviewer 2026-10-13).

THE SCAN IS TEXTUAL and deliberately over-inclusive. Every identifier in an
`#if`/`#elif` expression is a tested symbol, so a function-like macro such as
`ESP_IDF_VERSION_VAL` has to be listed too. A scan that tried to be clever
about which identifiers "really" count would be one more place for a switch
to hide. Comments are stripped and continuation lines joined first, so a
commented-out `#if` is not counted and a split one is.

A HEADER GUARD is recognised only in its exact shape: the file's first
directive is `#ifndef X` and the next is `#define X` with no value. The same
`#ifndef X` followed by `#define X 60000` is a default value, which a `-D`
can override, and it has to be listed.

This file is ALSO the switch list that 12.4's "test-only features are inert in
the build that will fly" draws its trigger table from (spec: "This list is also
what keeps the trigger table ... complete").
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

SHIP, TEST, NOT = "shipping variant", "test-only", "not a switch"
# The three kinds of "not a switch" the spec names; a NOT entry must use one.
NOT_KINDS = ("default value", "injected value", "library macro")

# symbol -> (class, kind or None, reason, refusal row or None)
# A test-only entry names the build_output.py row that refuses its image. A
# CMake option and the C symbol it defines are separate entries when their
# names differ (GEN_INHIBIT_AUTOARM -> GEN_INHIBIT_AUTOARM_MODE), and one entry
# when they share a name: either can change without the other, and the scan
# finds each where it is tested.
INSTR = "no measurement instrument in the image"
SWITCHES = {
    # ---- shipping variants
    "GEN_INHIBIT_AUTOARM": (SHIP, None,
        "CMake option for the auto-arm image (boots armed in the given mode); "
        "spec names auto-arm as a shipping variant", None),
    "GEN_INHIBIT_AUTOARM_MODE": (SHIP, None,
        "the C symbol GEN_INHIBIT_AUTOARM defines; defaults to GEN_INHIBIT_OFF "
        "(the manual image)", None),
    # ---- test-only
    "GI_INSTRUMENT_RXQ": (TEST, None,
        "RX-queue backlog measurement build; perturbs what it measures", INSTR),
    "GI_INSTRUMENT_TXABORT": (TEST, None,
        "TX-abort measurement build; writes the abort register from outside "
        "the driver and exposes /gi_txabort", INSTR),
    "GI_INSTRUMENT_ABORTWIN": (TEST, None,
        "abort-window measurement build", INSTR),
    "OTA_HEALTH_FAULT_INJECT": (TEST, None,
        "rollback-rehearsal build; fails its health gate and rolls back 60 s "
        "after every boot", "no OTA fault injection in the image"),
    "GI_TEST_RESTART_QUIESCE": (TEST, None,
        "restart test build (spec 12.4 'no error frames at a restart'): the "
        "crash trigger and the restart record", "no restart test feature in the image"),
    # ---- not a switch
    "GEN_INHIBIT_AUTOARM_OFFSET_US": (SHIP, None,
        "part of the auto-arm configuration (an #ifndef default of 500 us beside "
        "GEN_INHIBIT_AUTOARM_MODE); reviewer's ruling 2026-10-13", None),
    "DIAG_FW_VERSION": (NOT, "default value",
        "#ifndef default for the diag fw_version; build_output.py row 10 checks "
        "it against the schema doc", None),
    "OTA_HEALTH_TIMEOUT_MS": (NOT, "default value",
        "#ifndef default (60000 ms) for the health gate's window", None),
    "DEBUG_LOGS_ENABLE": (NOT, "default value",
        "#ifndef default (1) in the stock debug_logs component; also gates that "
        "component's code", None),
    "DEBUG_LOGS_LEVEL": (NOT, "default value", "#ifndef default in debug_logs_config.h", None),
    "DEBUG_LOGS_MAX_MESSAGE": (NOT, "default value", "#ifndef default in debug_logs_config.h", None),
    "DEBUG_LOGS_QUEUE_LEN": (NOT, "default value", "#ifndef default in debug_logs_config.h", None),
    "DEBUG_LOGS_UDP_DEST_IP": (NOT, "default value", "#ifndef default in debug_logs_config.h", None),
    "DEBUG_LOGS_UDP_PORT": (NOT, "default value", "#ifndef default in debug_logs_config.h", None),
    "GIT_SHA": (NOT, "injected value",
        "the commit, injected by the top-level CMakeLists.txt (add_definitions)", None),
    "HARDWARE_VER": (SHIP, None,
        "the board, set by the top-level CMakeLists.txt (WICAN_V300 for the OBD); "
        "reviewer's ruling 2026-10-13", None),
    "WICAN_PRO": (SHIP, None,
        "the board id HARDWARE_VER is compared with (stock's other board); "
        "reviewer's ruling 2026-10-13", None),
    "MIN": (NOT, "library macro", "#ifndef fallback definition in ftp.h", None),
    "MAX": (NOT, "library macro", "#ifndef fallback definition in ftp.h", None),
    "ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED": (NOT, "library macro",
        "ESP-IDF ADC calibration capability (soc caps), not a CONFIG_ option", None),
    "ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED": (NOT, "library macro",
        "ESP-IDF ADC calibration capability (soc caps), not a CONFIG_ option", None),
    "__cplusplus": (NOT, "library macro",
        "compiler-defined; the extern \"C\" wrappers in headers", None),
    # GEN_INHIBIT_SOC_MIN_PCT is deliberately NOT listed: an #ifdef that changes
    # the SoC floor, settable only by -D. Its class (or its deletion) waits on the
    # user (reviewer, 2026-10-13), and until then it fails as unclassified.
}

CMAKE_KEYWORDS = {
    "AND", "OR", "NOT", "DEFINED", "EQUAL", "LESS", "GREATER", "LESS_EQUAL",
    "GREATER_EQUAL", "STREQUAL", "STRLESS", "STRGREATER", "MATCHES", "EXISTS",
    "IS_DIRECTORY", "IS_ABSOLUTE", "COMMAND", "POLICY", "TARGET", "TEST",
    "IN_LIST", "VERSION_LESS", "VERSION_GREATER", "VERSION_EQUAL",
    "VERSION_LESS_EQUAL", "VERSION_GREATER_EQUAL", "TRUE", "FALSE", "ON", "OFF",
    "YES", "NO", "Y", "N", "IGNORE", "NOTFOUND", "ENV", "CACHE",
}
C_EXTS = (".c", ".h", ".cpp", ".hpp", ".cc")
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
DIRECTIVE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elifdef|elifndef|elif|define)\b(.*)$")


def strip_c_comments(text):
    """Block and line comments out, string and char literals kept, newlines
    preserved so line numbers still mean something."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def logical_lines(text):
    """(first line number, line) with backslash continuations joined."""
    lines = strip_c_comments(text).split("\n")
    out, k = [], 0
    while k < len(lines):
        start, cur = k + 1, lines[k]
        while cur.endswith("\\") and k + 1 < len(lines):
            k += 1
            cur = cur[:-1] + " " + lines[k]
        out.append((start, cur))
        k += 1
    return out


def c_symbols(text):
    """[(symbol, line)] for every symbol a directive in this file tests, header
    guard excluded. String literals inside an #if are not identifiers."""
    lines = [(ln, line) for ln, line in logical_lines(text) if line.strip()]
    directives = []
    for ln, line in lines:
        m = DIRECTIVE.match(line)
        if m:
            directives.append((ln, m.group(1), m.group(2).strip()))
    # The guard: the file's first non-blank line is `#ifndef X` and the next is
    # `#define X` with no value. Anything before it -- an #include, code -- and
    # it is an ordinary #ifndef.
    guard = None
    if len(lines) >= 2:
        m0, m1 = DIRECTIVE.match(lines[0][1]), DIRECTIVE.match(lines[1][1])
        if m0 and m1 and m0.group(1) == "ifndef" and m1.group(1) == "define":
            g, d = m0.group(2).split(), m1.group(2).split()
            if len(g) == 1 and len(d) == 1 and g[0] == d[0]:
                guard = g[0]
    out = []
    for k, (ln, kind, rest) in enumerate(directives):
        if kind == "define":
            continue
        if kind in ("ifdef", "ifndef", "elifdef", "elifndef"):
            names = rest.split()[:1]
        else:
            rest = re.sub(r'"(\\.|[^"\\])*"', " ", rest)
            names = [x for x in IDENT.findall(rest) if x not in ("defined", "__has_include")]
        for s in names:
            if k == 0 and s == guard:
                continue
            out.append((s, ln))
    return out


def cmake_symbols(text):
    """[(variable, line)] for every variable an if()/elseif() tests."""
    out = []
    body = re.sub(r"#[^\n]*", "", text)
    for m in re.finditer(r"\b(if|elseif)\s*\(", body, flags=re.IGNORECASE):
        depth, j = 1, m.end()
        while j < len(body) and depth:
            depth += {"(": 1, ")": -1}.get(body[j], 0)
            j += 1
        cond = body[m.end():j - 1]
        ln = body.count("\n", 0, m.start()) + 1
        cond = re.sub(r'"(\\.|[^"\\])*"', " ", cond)
        # ${VAR} and $ENV{VAR} need no unwrapping: $ { } are not word characters
        for s in IDENT.findall(cond):
            if s.upper() in CMAKE_KEYWORDS or s.isdigit():
                continue
            out.append((s, ln))
    return out


def scan(repo=REPO):
    """{symbol: [where, ...]} over the product source, CONFIG_* excluded."""
    found = {}

    def add(sym, where):
        if sym.startswith("CONFIG_"):
            return
        found.setdefault(sym, []).append(where)

    for top in ("main", "components"):
        root = os.path.join(repo, top)
        for d, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                if not f.endswith(C_EXTS):
                    continue
                p = os.path.join(d, f)
                rel = os.path.relpath(p, repo).replace(os.sep, "/")
                text = open(p, encoding="utf-8", errors="replace").read()
                for s, ln in c_symbols(text):
                    add(s, "%s:%d" % (rel, ln))
    cm = os.path.join(repo, "main", "CMakeLists.txt")
    if os.path.exists(cm):
        for s, ln in cmake_symbols(open(cm, encoding="utf-8", errors="replace").read()):
            add(s, "main/CMakeLists.txt:%d" % ln)
    return found


def classify(found, table=None):
    """The comparison, as data: (unlisted, stale, malformed)."""
    table = SWITCHES if table is None else table
    unlisted = sorted(s for s in found if s not in table)
    stale = sorted(s for s in table if s not in found)
    bad = []
    for s, entry in sorted(table.items()):
        cls, kind, reason, refusal = entry
        if cls not in (SHIP, TEST, NOT):
            bad.append("%s: class %r is not one of the three" % (s, cls))
        if cls == NOT and kind not in NOT_KINDS:
            bad.append("%s: 'not a switch' needs one of %s, has %r" % (s, NOT_KINDS, kind))
        if not (reason or "").strip():
            bad.append("%s: no reason" % s)
        if cls == TEST and not refusal:
            bad.append("%s: test-only with no refusal row" % s)
    return unlisted, stale, bad


def main():
    found = scan()
    unlisted, stale, bad = classify(found)
    for s in sorted(found):
        e = SWITCHES.get(s)
        print("%-40s %-16s %s" % (s, e[0] if e else "UNLISTED", ", ".join(found[s][:3])))
    print()
    print("unlisted %d, stale %d, malformed %d" % (len(unlisted), len(stale), len(bad)))
    return 1 if (unlisted or stale or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
