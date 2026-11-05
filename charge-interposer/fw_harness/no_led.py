#!/usr/bin/env python3
"""Spec 8: "The board drives no LED, in any build (user, 2026-10-26)."

Charge-interposer tester, 2026-11-04, port step 2a acceptance (j), in the
form the charge-interposer reviewer ruled that day ((B), after this tool's
first probe of the 2a builds). No bench read: three rules over one build,
and one mutant build.

    py -3.14 -B no_led.py --build <build folder>
    py -3.14 -B no_led.py --can-fail --build <build folder>
    py -3.14 -B no_led.py --mutant-build <variant> [--jobs N]
    py -3.14 -B no_led.py --self-test

A build folder is an ESP-IDF build's (firmware/build/<variant>/). Its
project_description.json names the app ELF, the components and their
folders. The rules:
  1. Symbols: the app ELF's symbol table (read here, an ELF32 little-endian
     reader; no toolchain) holds no function or object named led_strip_*,
     neopixelWrite or board_led (the board's own route to LED1), plain or
     inside a C++ mangled name. The ELF must be Xtensa and hold app_main, so
     a reader that read nothing cannot pass.
     NOT in this rule: rgbLedWrite, rmtInit, rmtWrite and rmt_*. They come
     into every 2a build through the Arduino core's own GPIO object, whether
     or not anything drives an LED: the link map's "archive member
     included" chain is esp32-hal-gpio.c.obj -> (rgbLedWrite)
     esp32-hal-rgb-led.c.obj -> (rmtWrite) esp32-hal-rmt.c.obj -> ESP-IDF's
     esp_driver_rmt (the six 2a variants' builds at fab7313, 2026-11-04). So
     their presence shows nothing either way; the source rule carries them.
     --can-fail checks that the exclusion is deliberate: on the real ELF,
     rgbLedWrite is present and the rule still passes.
  2. Sources: the project's own components (each component folder inside
     the project folder and not in managed_components/; firmware/main for
     the interposer), every C, C++ and header file, with comments and string
     literals taken out first, name none of LED_BUILTIN, RGB_BUILTIN,
     rgbLedWrite, rmtInit, rmtWrite, rmt_*, neopixelWrite, led_strip_*,
     board_led, BOARD_LED_LED1, LED1_GPIO_NUM, GPIO_NUM_2 or GPIO_NUM_48 (the
     last two anywhere, which is stricter than "as a pin argument"), and
     give no literal 2 or 48 as the pin argument of pinMode, digitalWrite,
     gpio_set_direction or gpio_set_level, or as the bit of a gpio_config
     pin_bit_mask (1ULL << n, BIT(n), BIT64(n)). No source file found is
     refused.
     THE LIMIT (accepted for 2a by the reviewer, 2026-11-04): this rule
     reads only the project's own components. A managed or IDF component
     that drove the RGB LED from its own code would pass it, and rule 1
     would catch that only through led_strip_*, neopixelWrite or board_led,
     not through the core's rgbLedWrite.
  3. Board data: the board component the build used (the one component with
     include/board_pins.h; for the interposer managed_components/vtrux__x2,
     shared/boards/x2 at the tag dependencies.lock pins) gives every port
     pin (each *_GPIO_NUM not named LED*) as neither 2 nor 48: the X2's LED1
     is on GPIO2, and the devkit board definition's RGB LED on GPIO48 (spec
     8's port note). The LED pins it offers are listed, not failed: using
     one is rule 1's and 2's board_led. No TWAI_TX_GPIO_NUM is refused.

--can-fail copies the build's ELF, source folders and board_pins.h into this
run's scratch folder and judges the copy unchanged (it must PASS, with
rgbLedWrite in its ELF: the control above), then with each of: a function
symbol renamed in the ELF's string table to board_led, neopixelWrite,
led_strip_new_rmt_device, and the mangled _ZN3Led9board_ledEv; one source
line per source-rule name and form (a call or a use of each); a port pin set
to 48, and one to 2. Each must FAIL. One more edit, the names only in a
comment and a string, must still PASS (the stripping is not what passes).

--mutant-build makes the one mutant build: run from a git checkout of this
repo (the list's tree), in a shell exported for the interposer's ESP-IDF
(the runner's "idf" shell), it clones the checkout's HEAD into this run's
scratch folder, adds to firmware/main/main.cpp a global object whose
constructor calls board_led(BOARD_LED_LED1, true) (kept by the linker: the
constructor table is never collected), builds <variant> there with
charge-interposer/vtrux_tools.py build, and requires main.cpp in the build's
compile commands, board_led in the ELF, and rule 1 failing on it. PASS (exit
0) means the mutant was caught; the clone and its build are removed after.

--self-test runs the rules and the --can-fail edits on a stand-in build (a
small ELF written here), for the harness suite where no build exists.

Exit 0 PASS, 1 FAIL, 2 refused (an unreadable build, a bad call).
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

EM_XTENSA = 94
SHT_SYMTAB = 2
STT_NOTYPE, STT_OBJECT, STT_FUNC = 0, 1, 2

SYM_PREFIXES = ("led_strip_",)
SYM_NAMES = ("neopixelWrite", "board_led")
# present in every 2a build through the Arduino core's GPIO object, so not
# rule 1's (the docstring); --can-fail's control requires this one present
CORE_SYMBOL = "rgbLedWrite"

SRC_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino")
SRC_TOKENS = ("LED_BUILTIN", "RGB_BUILTIN", "rgbLedWrite", "rmtInit", "rmtWrite",
              "neopixelWrite", "board_led", "BOARD_LED_LED1", "LED1_GPIO_NUM", "GPIO_NUM_2",
              "GPIO_NUM_48")
SRC_PREFIXES = ("rmt_", "led_strip_")
SRC_TOKEN_RE = re.compile(r"\b(%s|(?:%s)\w*)\b" % ("|".join(SRC_TOKENS),
                                                   "|".join(SRC_PREFIXES)))
SRC_PIN_RE = re.compile(
    r"\b(pinMode|digitalWrite|gpio_set_direction|gpio_set_level)\s*\(\s*"
    r"(?:\(\s*gpio_num_t\s*\)\s*|static_cast\s*<\s*gpio_num_t\s*>\s*\(\s*)?\(?\s*(2|48)\s*\)?\s*[,)]")
SRC_MASK_RE = re.compile(
    r"\bpin_bit_mask\s*=\s*[^;}]*?(?:<<\s*\(?\s*(2|48)\b|\bBIT(?:64)?\s*\(\s*(2|48)\s*\))")
PIN_DEF_RE = re.compile(r"^\s*#\s*define\s+(\w+_GPIO_NUM)\s+\(?\s*(\d+)\s*\)?", re.M)
BAD_PINS = (2, 48)


class Refused(Exception):
    pass


# ---- rule 1: the ELF's symbols ---------------------------------------------------
def elf_symbols(path):
    """(e_machine, [(name, type, section index)]) of an ELF32 LE file's
    .symtab sections."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise Refused("%s: %s" % (path, e))
    if data[:4] != b"\x7fELF" or len(data) < 52:
        raise Refused("%s: not an ELF file" % path)
    if data[4] != 1 or data[5] != 1:
        raise Refused("%s: not ELF32 little-endian (class %d, data %d)" % (path, data[4], data[5]))
    (_, _, machine, _, _, _, shoff, _, _, _, _, shentsize, shnum, _) = struct.unpack_from(
        "<16sHHIIIIIHHHHHH", data, 0)
    if not shoff or shentsize < 40 or shoff + shnum * shentsize > len(data):
        raise Refused("%s: no readable section headers" % path)
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    syms = []
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        off, size, link, entsize = s[4], s[5], s[6], s[9] or 16
        if link >= len(secs):
            raise Refused("%s: a symbol table's string table is missing" % path)
        stroff, strsize = secs[link][4], secs[link][5]
        strtab = data[stroff:stroff + strsize]
        for i in range(size // entsize):
            st_name, _, _, info, _, shndx = struct.unpack_from("<IIIBBH", data, off + i * entsize)
            end = strtab.find(b"\0", st_name)
            name = strtab[st_name:end if end >= 0 else None].decode("ascii", "replace")
            syms.append((name, info & 0xF, shndx))
    return machine, syms


def _parts(name):
    """The name, and for a C++ mangled name each <length><identifier> in it."""
    out = [name]
    if name.startswith("_Z"):
        i = 2
        while i < len(name):
            m = re.match(r"(\d+)", name[i:])
            if not m:
                i += 1
                continue
            n = int(m.group(1))
            j = i + len(m.group(1))
            if n and j + n <= len(name):
                out.append(name[j:j + n])
            i = j
    return out


def symbol_forbidden(name):
    for p in _parts(name):
        if p.startswith(SYM_PREFIXES) or p in SYM_NAMES:
            return True
    return False


def rule_symbols(elf):
    machine, syms = elf_symbols(elf)
    funcs = [s for s in syms if s[1] == STT_FUNC]
    named = {s[0] for s in syms}
    out = [(machine == EM_XTENSA, "ELF machine %d (Xtensa is %d)" % (machine, EM_XTENSA)),
           ("app_main" in named, "the symbol table read: %d symbols, %d functions, app_main %s"
            % (len(syms), len(funcs), "present" if "app_main" in named else "ABSENT"))]
    bad = sorted({s[0] for s in syms if s[1] in (STT_NOTYPE, STT_OBJECT, STT_FUNC)
                  and symbol_forbidden(s[0])})
    out.append((not bad, "LED symbols (led_strip_*, neopixelWrite, board_led): %s" % (
        "none" if not bad else "%d: %s" % (len(bad), ", ".join(bad[:8])))))
    out.append((True, "%s, the Arduino core's, not judged: %s" % (
        CORE_SYMBOL, "present" if CORE_SYMBOL in named else "absent")))
    return out


def has_symbol(elf, name):
    return any(s[0] == name for s in elf_symbols(elf)[1])


# ---- rule 2: the project's own sources -------------------------------------------
def strip_c(text):
    """text with C/C++ comments and string and character literals blanked,
    newlines kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " + "\n" * text.count("\n", i, j))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def source_hits(text):
    """[(line, what)] in one file's text."""
    s = strip_c(text)
    hits = []

    def line(pos):
        return s.count("\n", 0, pos) + 1
    for m in SRC_TOKEN_RE.finditer(s):
        hits.append((line(m.start()), m.group(1)))
    for m in SRC_PIN_RE.finditer(s):
        hits.append((line(m.start()), "%s(%s, ...)" % (m.group(1), m.group(2))))
    for m in SRC_MASK_RE.finditer(s):
        hits.append((line(m.start()), "pin_bit_mask bit %s" % (m.group(1) or m.group(2))))
    return sorted(hits)


def source_files(dirs):
    files = []
    for d in dirs:
        for root, subdirs, names in os.walk(d):
            subdirs.sort()
            for x in sorted(names):
                if x.lower().endswith(SRC_EXT):
                    files.append(os.path.join(root, x))
    return files


def rule_sources(dirs):
    files = source_files(dirs)
    if not files:
        raise Refused("no C, C++ or header file in %s" % ", ".join(dirs))
    bad = []
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln, what in source_hits(f.read()):
                bad.append("%s:%d %s" % (os.path.basename(p), ln, what))
    return [(not bad, "sources: %d files in %d folder(s), %s" % (
        len(files), len(dirs), "no LED use" if not bad else "%d LED uses: %s" % (
            len(bad), "; ".join(bad[:8]))))]


# ---- rule 3: the board's pins ------------------------------------------------------
def rule_board(pins_h):
    try:
        with open(pins_h, encoding="utf-8") as f:
            text = strip_c(f.read())
    except OSError as e:
        raise Refused("%s: %s" % (pins_h, e))
    pins = PIN_DEF_RE.findall(text)
    names = [n for n, _ in pins]
    if "TWAI_TX_GPIO_NUM" not in names:
        raise Refused("%s: no TWAI_TX_GPIO_NUM, so no port pins were read" % pins_h)
    ports = [(n, int(v)) for n, v in pins if not n.startswith("LED")]
    offered = [(n, int(v)) for n, v in pins if n.startswith("LED")]
    bad = [(n, v) for n, v in ports if v in BAD_PINS]
    return [(not bad, "board port pins: %s%s" % (
        ", ".join("%s %d" % p for p in ports),
        "" if not bad else " -- on an LED pin: %s" % ", ".join("%s %d" % p for p in bad))),
            (True, "board LED pins offered (rules 1 and 2 cover their use): %s"
             % (", ".join("%s %d" % p for p in offered) or "none"))]


# ---- a build folder -----------------------------------------------------------------
def _fwd(s):
    return s.replace("\\", "/")


def build_inputs(folder):
    """{"elf", "sources": [dirs], "board_pins"} of a build folder."""
    p = os.path.join(folder, "project_description.json")
    try:
        with open(p, encoding="utf-8") as f:
            desc = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("%s: %s" % (p, e))
    proj = _fwd(desc.get("project_path") or "").rstrip("/")
    if not proj or not desc.get("app_elf"):
        raise Refused("%s: no project_path or app_elf" % p)
    elf = os.path.join(folder, desc["app_elf"])
    info = desc.get("build_component_info") or {}
    own, boards = [], []
    for name, c in sorted(info.items()):
        d = _fwd(c.get("dir") or "").rstrip("/")
        if not d:
            continue
        if d.startswith(proj + "/") and not d.startswith(proj + "/managed_components/"):
            own.append(d)
        if os.path.isfile(os.path.join(d, "include", "board_pins.h")):
            boards.append(d)
    if not own:
        raise Refused("%s: no component inside the project folder %s" % (p, proj))
    if len(boards) != 1:
        raise Refused("%s: %d components with include/board_pins.h (%s), not 1"
                      % (p, len(boards), ", ".join(boards)))
    return {"elf": elf, "sources": own, "board_pins": os.path.join(boards[0], "include",
                                                                   "board_pins.h")}


def judge(inp):
    return (rule_symbols(inp["elf"]) + rule_sources(inp["sources"])
            + rule_board(inp["board_pins"]))


def report(title, lines):
    print("== " + title, flush=True)
    for ok, ln in lines:
        print("%s %s" % ("  ok  " if ok else "FAIL  ", ln), flush=True)
    passed = all(ok for ok, _ in lines)
    print("no_led: %s" % ("PASS" if passed else "FAIL"), flush=True)
    return passed


# ---- can-fail and self-test ------------------------------------------------------------
def _copy_inputs(inp, dst):
    os.makedirs(dst, exist_ok=True)
    elf = os.path.join(dst, "app.elf")
    shutil.copyfile(inp["elf"], elf)
    srcs = []
    for i, d in enumerate(inp["sources"]):
        t = os.path.join(dst, "src%d" % i)
        shutil.copytree(d, t, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        srcs.append(t)
    pins = os.path.join(dst, "board_pins.h")
    shutil.copyfile(inp["board_pins"], pins)
    return {"elf": elf, "sources": srcs, "board_pins": pins}


def _rename_symbol(elf, new):
    """Overwrite one function symbol's name in elf's string table with new
    (a name at least as long, not app_main, not already forbidden)."""
    with open(elf, "rb") as f:
        data = bytearray(f.read())
    _, _, _, _, _, _, shoff, _, _, _, _, shentsize, shnum, _ = struct.unpack_from(
        "<16sHHIIIIIHHHHHH", data, 0)
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        for i in range(s[5] // (s[9] or 16)):
            st_name, _, _, info, _, _ = struct.unpack_from("<IIIBBH", data, s[4] + i * (s[9] or 16))
            a = stroff + st_name
            end = data.index(0, a)
            old = bytes(data[a:end]).decode("ascii", "replace")
            if (info & 0xF) == STT_FUNC and len(old) >= len(new) \
                    and old not in ("app_main", CORE_SYMBOL) \
                    and not symbol_forbidden(old) and old.isidentifier():
                data[a:end] = new.encode() + b"\0" * (end - a - len(new))
                with open(elf, "wb") as f:
                    f.write(data)
                return old
    raise Refused("%s: no function symbol of %d characters or more to rename" % (elf, len(new)))


def _append_source(inp, line):
    files = [f for f in source_files(inp["sources"]) if f.lower().endswith((".cpp", ".c", ".ino"))]
    if not files:
        raise Refused("no C or C++ source file to edit in the copy")
    with open(files[0], "a", encoding="utf-8", newline="\n") as f:
        f.write("\n" + line + "\n")


def _set_pin(inp, name, value):
    with open(inp["board_pins"], encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(r"(#\s*define\s+%s\s+)\d+" % name, r"\g<1>%d" % value, text)
    if not n:
        raise Refused("%s: no %s to change" % (inp["board_pins"], name))
    with open(inp["board_pins"], "w", encoding="utf-8", newline="\n") as f:
        f.write(new)


EDITS = (
    [("ELF: a function renamed to %s" % n, "elf", n, False)
     for n in ("board_led", "neopixelWrite", "led_strip_new_rmt_device", "_ZN3Led9board_ledEv")]
    + [("source: %s" % ln, "src", ln, False)
       for ln in ("void intp_canfail_a(void) { rgbLedWrite(48, 0, 0, 0); }",
                  "void intp_canfail_b(void) { rmtInit(48, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 1); }",
                  "void intp_canfail_c(void) { rmtWrite(48, 0, 0, 0); }",
                  "void intp_canfail_d(void) { rmt_new_tx_channel(0, 0); }",
                  "void intp_canfail_e(void) { neopixelWrite(48, 0, 0, 0); }",
                  "void intp_canfail_f(void) { led_strip_new_rmt_device(0, 0, 0); }",
                  "void intp_canfail_g(void) { board_led(BOARD_LED_LED1, true); }",
                  "static const int intp_canfail_h = BOARD_LED_LED1;",
                  "static const int intp_canfail_i = LED1_GPIO_NUM;",
                  "static const int intp_canfail_j = LED_BUILTIN;",
                  "static const int intp_canfail_k = RGB_BUILTIN;",
                  "static const int intp_canfail_l = GPIO_NUM_48;",
                  "static const int intp_canfail_m = GPIO_NUM_2;",
                  "void intp_canfail_n(void) { pinMode(48, OUTPUT); }",
                  "void intp_canfail_o(void) { digitalWrite( 2 , HIGH); }",
                  "void intp_canfail_p(void) { gpio_set_direction((gpio_num_t)48, GPIO_MODE_OUTPUT); }",
                  "void intp_canfail_q(void) { gpio_set_level(static_cast<gpio_num_t>(2), 1); }",
                  "static const gpio_config_t intp_canfail_r = { .pin_bit_mask = 1ULL << 48 };",
                  "static const gpio_config_t intp_canfail_s = { .pin_bit_mask = BIT64(2) };")]
    + [("source: the names only in a comment and a string",
        "src", '/* LED_BUILTIN rmtInit */ static const char *intp_canfail_t = '
        '"LED_BUILTIN rgbLedWrite rmt_new_tx_channel pinMode(48, OUTPUT)"; // board_led', True)]
    + [("board: TWAI_TX_GPIO_NUM 48", "pin", ("TWAI_TX_GPIO_NUM", 48), False),
       ("board: MCP2515_INT_GPIO_NUM 2", "pin", ("MCP2515_INT_GPIO_NUM", 2), False)])


def can_fail(inp, base):
    """0 if the copy passes, each corruption fails and the comment edit passes."""
    import scratch_dir
    bad = 0
    with scratch_dir.run(base) as work:
        cases = [("the copy, unchanged", None, None, True)] + list(EDITS)
        for i, (label, kind, arg, want) in enumerate(cases):
            c = _copy_inputs(inp, os.path.join(work, "c%d" % i))
            note = ""
            if kind == "elf":
                note = " (was %s)" % _rename_symbol(c["elf"], arg)
            elif kind == "src":
                _append_source(c, arg)
            elif kind == "pin":
                _set_pin(c, *arg)
            lines = judge(c)
            passed = all(ok for ok, _ in lines)
            good = passed == want
            bad += not good
            if kind is None:
                core = has_symbol(c["elf"], CORE_SYMBOL)
                bad += not core
                print("%s control: %s is in the ELF%s" % (
                    "ok    " if core else "FAIL  ", CORE_SYMBOL,
                    " and rule 1 passes all the same" if core else
                    " -- ABSENT, so this run does not show the exclusion is deliberate"),
                      flush=True)
            print("%s %s%s: %s" % ("ok    " if good else "FAIL  ", label, note,
                                   ("passes" if passed else "fails")
                                   + ("" if good else " -- wanted it to %s; %s" % (
                                       "pass" if want else "fail",
                                       "; ".join(ln for ok, ln in lines if not ok)))),
                  flush=True)
    print("\nno_led --can-fail: %s" % ("every corruption caught, the copies that should pass pass"
                                       if not bad else "%d WRONG" % bad))
    return 1 if bad else 0


# ---- the mutant build --------------------------------------------------------------------
MUTANT_FILE = ("charge-interposer", "firmware", "main", "main.cpp")
MUTANT_SYMBOL = "board_led"
MUTANT = """
// no_led.py --mutant-build: a mutant, never flashed. A global object's
// constructor calls the board's LED route at start-up; the constructor table
// is kept by the linker, so board_led must arrive in the ELF.
#include "board.h"
namespace {
struct IntpNoLedMutant {
    IntpNoLedMutant() { board_led(BOARD_LED_LED1, true); }
} intp_no_led_mutant;
}
"""


def mutant_caught(elf):
    """(caught, lines): board_led in the ELF and rule 1 failing on it."""
    lines = rule_symbols(elf)
    has = has_symbol(elf, MUTANT_SYMBOL)
    caught = has and not all(ok for ok, _ in lines)
    return caught, [(has, "%s in the mutant's ELF: %s" % (MUTANT_SYMBOL,
                                                         "present" if has else "ABSENT"))] + lines


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise Refused("git %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def mutant_build(variant, jobs):
    """0 if the mutant build is caught, 1 if not; Refused if it cannot be made."""
    import scratch_dir
    here = os.getcwd()
    head = _git(["rev-parse", "HEAD"], here)
    if not os.environ.get("IDF_PYTHON_ENV_PATH"):
        raise Refused("no IDF_PYTHON_ENV_PATH: run it in a shell exported for the interposer's "
                      "ESP-IDF (the runner's \"idf\" shell)")
    with scratch_dir.run("no-led-mutant") as work:
        repo = os.path.join(work, "repo")
        _git(["clone", "--shared", "-q", "--no-checkout", here, repo], work)
        _git(["checkout", "-q", "--detach", head], repo)
        target = os.path.join(repo, *MUTANT_FILE)
        if not os.path.isfile(target):
            raise Refused("%s: not in %s" % ("/".join(MUTANT_FILE), head))
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(MUTANT)
        print("no_led: the mutant: %s at %s, plus a board_led(BOARD_LED_LED1, true) call; "
              "building %s in %s" % ("/".join(MUTANT_FILE), head[:12], variant, repo), flush=True)
        cmd = [sys.executable, "-B", os.path.join(repo, "charge-interposer", "vtrux_tools.py"),
               "build", variant] + (["--jobs", str(jobs)] if jobs else [])
        rc = subprocess.run(cmd, cwd=repo).returncode
        if rc:
            raise Refused("the mutant did not build (vtrux_tools.py build %s: exit %d)"
                          % (variant, rc))
        build = os.path.join(repo, "charge-interposer", "firmware", "build", variant)
        try:
            with open(os.path.join(build, "compile_commands.json"), encoding="utf-8") as f:
                files = [_fwd(e.get("file", "")) for e in json.load(f)]
        except (OSError, ValueError) as e:
            raise Refused("the mutant build's compile_commands.json: %s" % e)
        if not any(x.endswith("/" + "/".join(MUTANT_FILE[-2:])) for x in files):
            raise Refused("%s is not compiled in variant %s, so the mutant is not in it"
                          % ("/".join(MUTANT_FILE), variant))
        inp = build_inputs(build)
        caught, lines = mutant_caught(inp["elf"])
        print("== the mutant build (%s, board_led called), rule 1 on its ELF:" % variant)
        for ok, ln in lines:
            print("   %s %s" % ("holds " if ok else "breaks", ln), flush=True)
        print("no_led --mutant-build: %s" % ("PASS, the mutant is caught" if caught
                                             else "FAIL, the mutant is NOT caught"), flush=True)
        return 0 if caught else 1


def write_elf(path, names, machine=EM_XTENSA):
    """A small ELF32 LE file whose .symtab holds each name as a function."""
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n in names)
    syms = b"\0" * 16
    pos = 1
    for n in names:
        syms += struct.pack("<IIIBBH", pos, 0x42000000, 4, (1 << 4) | STT_FUNC, 0, 1)
        pos += len(n) + 1
    off = 52
    sym_off, str_off = off, off + len(syms)
    shs_off = str_off + len(strtab)
    shoff = shs_off + len(shstr)
    shoff += (-shoff) % 4
    body = syms + strtab + shstr
    body += b"\0" * (shoff - off - len(body))
    sh = b"\0" * 40
    sh += struct.pack("<IIIIIIIIII", 1, SHT_SYMTAB, 0, 0, sym_off, len(syms), 2, 1, 4, 16)
    sh += struct.pack("<IIIIIIIIII", 9, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    sh += struct.pack("<IIIIIIIIII", 17, 3, 0, 0, shs_off, len(shstr), 0, 0, 1, 0)
    hdr = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + b"\0" * 9, 2, machine, 1,
                      0, 0, shoff, 0, 52, 0, 0, 40, 4, 3)
    with open(path, "wb") as f:
        f.write(hdr + body + sh)


STANDIN_PINS = """/* stand-in board_pins.h */
#define TWAI_TX_GPIO_NUM            7
#define TWAI_RX_GPIO_NUM            6
#define MCP2515_CS_GPIO_NUM         10
#define MCP2515_INT_GPIO_NUM        3
#define MCP2515_CRYSTAL_HZ          16000000
/* Offered: led1. */
#define LED1_GPIO_NUM               2
"""

STANDIN_MAIN = """// main.cpp, a stand-in: no LED (not LED_BUILTIN, not GPIO_NUM_48)
#include "board.h"
static const char *tag = "rgbLedWrite is not called";
void setup() { pinMode(21, OUTPUT); digitalWrite(12, LOW); gpio_set_level(GPIO_NUM_20, 0); }
/* gpio_config with pin_bit_mask = 1ULL << 48 would be one */
static const gpio_config_t io = { .pin_bit_mask = (1ULL << 4) | BIT64(5) };
"""


STANDIN_SYMS = ("app_main", "setup", "loop", "board_can_kind", "twai_driver_install",
                "spi_bus_initialize", "gpio_set_level", "board_can_kind_long_name",
                # the core's, in every 2a build (rule 1's docstring)
                "rgbLedWrite", "rmtInit", "rmtWrite", "rmt_new_tx_channel")


def _standin(folder, syms=STANDIN_SYMS):
    proj = os.path.join(folder, "proj")
    build = os.path.join(proj, "build", "bridge")
    main = os.path.join(proj, "main")
    x2 = os.path.join(proj, "managed_components", "vtrux__x2")
    for d in (build, main, os.path.join(x2, "include")):
        os.makedirs(d, exist_ok=True)
    write_elf(os.path.join(build, "interposer_bridge.elf"), list(syms))
    with open(os.path.join(main, "main.cpp"), "w", encoding="utf-8", newline="\n") as f:
        f.write(STANDIN_MAIN)
    with open(os.path.join(main, "led_free.h"), "w", encoding="utf-8", newline="\n") as f:
        f.write("#pragma once\n// board_led_t is a type, not board_led\nint pins(board_led_t x);\n")
    with open(os.path.join(x2, "include", "board_pins.h"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(STANDIN_PINS)
    with open(os.path.join(build, "project_description.json"), "w", encoding="utf-8") as f:
        json.dump({"project_path": _fwd(proj), "app_elf": "interposer_bridge.elf",
                   "build_component_info": {
                       "main": {"dir": _fwd(main)},
                       "vtrux__x2": {"dir": _fwd(x2)},
                       "esp_driver_rmt": {"dir": _fwd(os.path.join(folder, "idf", "rmt"))}}}, f)
    return build


def self_test():
    import scratch_dir
    bad = 0

    def expect(label, got, want):
        nonlocal bad
        good = got == want
        bad += not good
        print("%s %s: %r%s" % ("ok    " if good else "FAIL  ", label, got,
                               "" if good else " -- wanted %r" % (want,)), flush=True)

    for name, want in (("led_strip_new_rmt_device", True), ("neopixelWrite", True),
                       ("board_led", True), ("_ZN4Foo9board_ledEv", True),
                       ("_Z13neopixelWritehhhh", True),
                       ("board_led_t", False), ("board_led2", False), ("_Z3fooi", False),
                       ("app_main", False),
                       # the core's, deliberately not rule 1's (the docstring)
                       ("rgbLedWrite", False), ("rmtInit", False), ("rmtWrite", False),
                       ("rmt_new_tx_channel", False), ("_Z11rgbLedWritehhhh", False)):
        expect("symbol %s forbidden" % name, symbol_forbidden(name), want)
    for text, want in (("rmtInit(48, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 1);", 1),
                       ("rmtWrite(48, d, 1, 0);", 1), ("rmt_channel_handle_t h;", 1),
                       ("led_strip_handle_t s;", 1), ("neopixelWrite(48,1,2,3)", 1),
                       ("RGB_BUILTIN", 1), ("BOARD_LED_LED1", 1), ("LED1_GPIO_NUM", 1),
                       ("my_rmt_like(); xrmt_y(); rmtx(); board_led_t t;", 0),
                       ("pinMode(48, OUTPUT);", 1), ("pinMode(  48 ,OUTPUT)", 1),
                       ("gpio_set_direction((gpio_num_t)2, GPIO_MODE_OUTPUT);", 1),
                       ("gpio_set_level(static_cast<gpio_num_t>(48), 1);", 1),
                       ("digitalWrite(21, HIGH); digitalWrite(12, 2);", 0),
                       ("pinMode(248, OUTPUT); pinMode(2 + x, OUTPUT)", 0),
                       ("x.pin_bit_mask = BIT(2);", 1), ("x.pin_bit_mask = 1ULL<<48;", 1),
                       ("x.pin_bit_mask = 1ULL << 24;", 0),
                       ("// rgbLedWrite(48)\n/* LED_BUILTIN\n */ \"board_led\"", 0),
                       ("char c = '\"'; rgbLedWrite(1,2,3,4);", 1),
                       ("GPIO_NUM_48", 1), ("GPIO_NUM_4 GPIO_NUM_20 GPIO_NUM_21", 0)):
        expect("source %r: hits" % text, len(source_hits(text)), want)
    with scratch_dir.run("no-led-self-test") as work:
        build = _standin(work)
        inp = build_inputs(build)
        lines = judge(inp)
        expect("the stand-in build passes (%s)" % "; ".join(ln for _, ln in lines),
               all(ok for ok, _ in lines), True)
        for label, fn in (
                ("a non-Xtensa ELF fails", lambda: write_elf(inp["elf"], ["app_main"], machine=3)),
                ("an ELF without app_main fails", lambda: write_elf(inp["elf"], ["setup"]))):
            fn()
            expect(label, all(ok for ok, _ in judge(inp)), False)
            write_elf(inp["elf"], list(STANDIN_SYMS))
        def _write(path, data):
            with open(path, "wb") as f:
                f.write(data)
        for i, (label, fn) in enumerate((
                ("a file that is not an ELF is refused",
                 lambda c: _write(c["elf"], b"not an elf")),
                ("a board_pins.h with no TWAI_TX is refused",
                 lambda c: _write(c["board_pins"], b"#define X_GPIO_NUM 4\n")),
                ("no source file is refused",
                 lambda c: [os.remove(f) for f in source_files(c["sources"])]))):
            c = _copy_inputs(inp, os.path.join(work, "r%d" % i))
            fn(c)
            try:
                judge(c)
                got = "judged"
            except Refused:
                got = "refused"
            expect(label, got, "refused")
        bad += can_fail(inp, "no-led-self-canfail") != 0
        # the control can fail: an ELF without the core's rgbLedWrite
        write_elf(inp["elf"], [s for s in STANDIN_SYMS if s != CORE_SYMBOL])
        print("-- the same --can-fail on an ELF without %s (its control must say WRONG):"
              % CORE_SYMBOL, flush=True)
        expect("--can-fail without %s in the ELF is WRONG" % CORE_SYMBOL,
               can_fail(inp, "no-led-self-canfail"), 1)
        for label, fn, want in (
                ("a mutant build without board_led in the ELF is not caught",
                 lambda: write_elf(inp["elf"], list(STANDIN_SYMS)), False),
                ("a mutant build with board_led in the ELF is caught",
                 lambda: write_elf(inp["elf"], list(STANDIN_SYMS) + ["board_led"]), True)):
            fn()
            expect(label, mutant_caught(inp["elf"])[0], want)
    print("\nno_led --self-test: %s" % ("all checks OK" if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


def main(argv):
    ap = argparse.ArgumentParser(prog="no_led.py")
    ap.add_argument("--build")
    ap.add_argument("--can-fail", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--mutant-build", metavar="VARIANT")
    ap.add_argument("--jobs", type=int)
    a = ap.parse_args(argv)
    try:
        if a.self_test:
            return self_test()
        if a.mutant_build:
            return mutant_build(a.mutant_build, a.jobs)
        if not a.build:
            print("no_led: REFUSED: --build <build folder> is needed")
            return 2
        inp = build_inputs(a.build)
        if a.can_fail:
            return can_fail(inp, "no-led-canfail")
        print("elf %s\nsources %s\nboard %s" % (inp["elf"], ", ".join(inp["sources"]),
                                               inp["board_pins"]), flush=True)
        return 0 if report(a.build, judge(inp)) else 1
    except Refused as e:
        print("no_led: REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
