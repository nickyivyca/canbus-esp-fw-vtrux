#!/usr/bin/env python3
"""The tester's checks of shared/diag against SPEC.md D6, the build-time fit checks
(charge-interposer tester, 2026-11-24).

D6: "The generated table comes with checks that run when the firmware compiles, in C and in
C++. The checks must include a capped form, which caps a value at its signal's maximum. A
build must fail if any of these is passed to the checks: a value whose type could exceed
its signal, other than through the capped form; a mask or a constant that exceeds its
signal; a signed signal."

The macros' names and arguments are the published interface in shared/diag README.md,
"Using it" (<p>_FIT_PUT, _FIT_CAP, _FIT_BIT, _FIT_MASK, _FIT_CONST, each taking the raw
value array, the message and the signal). Each case is one small program that includes a
header made by the generator under test and calls one macro:

  build   it must compile without a warning (-Wall -Wextra -Werror), and on the host its
          program must print the value the macro put into the array. For the capped form
          that is the value capped at the signal's maximum, 2^bits - 1.
  fail    the compiler must stop on a static assertion whose message names the signal
          (Message.signal). It building is a FAIL; it stopping on any other error is VOID,
          which is not the check, and counts as not as expected.

The compilers: the host's gcc as C11 and gnu17, its g++ as C++17 and gnu++2b (the firmware
dialects of ESP-IDF v5.4), and, when they are on PATH (a shell exported for ESP-IDF), the
two boards' compilers in the firmware dialects, compile only: xtensa-esp32s3-elf (the
ESP32-CAN-X2) and riscv32-esp-elf (the WiCAN's ESP32-C3). A target compiler not on PATH is
NOT RUN, never a pass.

The tables: fixtures/fit.dbc (unsigned signals of 1 to 64 bits, Intel and Motorola, and
signed ones), and gen-inhibit's published diag DBC as origin/gen-inhibit/main holds it,
as is and with diag_tx_fail narrowed from 8 to 4 bits, the case gen-inhibit's own width
check (gen-inhibit/test/diag_host/width_check.py, dbc-narrow-tx_fail) builds: a 32-bit
counter capped into it must build and give 15, and the same counter not capped must fail.
The narrowed DBC keeps its stated DiagSchemaHash; the fit checks do not read it. Both
projects' published diag DBCs must give a header that compiles in C and C++. A DBC
missing from this repo is NOT RUN, never a pass.

--mutants is the can-fail: each mutant is a one-line defect planted in the generated
header of the fixture's table (only the check's own text changes), and the host cases
must show it: at least one case not as expected. The unedited header is the control and
must pass every case. --selftest checks the judge on made-up compiler results.

Work goes to the worktree's scratch/diag-fit/<pid>/, removed afterwards (--keep leaves
it). Run from the worktree root, in build.slice on madhouse:

    python3.13 -B shared/diag/test/fit_checks.py [--diag DIR] [--no-git] [--keep]
    python3.13 -B shared/diag/test/fit_checks.py --mutants [--diag DIR]
    python3.13 -B shared/diag/test/fit_checks.py --selftest

Exit 0 every case as expected (with --mutants: the control passes and every mutant is
shown), 1 not, 2 refused (no cantools, no host compiler, a mutant's text not in the
header exactly once).
"""

import argparse
import concurrent.futures
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIAG = os.path.dirname(HERE)
WORKTREE = os.path.dirname(os.path.dirname(DIAG))
FIXTURE = os.path.join(HERE, "fixtures", "fit.dbc")
GI_DBC = ("origin/gen-inhibit/main", "gen-inhibit/diag/vtrux-wican-diag.dbc")
INTP_DBC = ("origin/charge-interposer/main", "charge-interposer/diag/vtrux-interposer-diag.dbc")
GI_TX_FAIL_8 = " SG_ diag_tx_fail : 32|8@1+"
GI_TX_FAIL_4 = " SG_ diag_tx_fail : 32|4@1+"
CC = os.environ.get("CC", "gcc")
CXX = os.environ.get("CXX", "g++")
WARN = ["-Wall", "-Wextra", "-Werror"]
U63_MAX = (1 << 63) - 1


class Refused(Exception):
    pass


# ---------------------------------------------------------------- compilers

def compilers(host_only=False):
    """[(label, lang "c"|"c++", argv before the source, runs on this host)], and the labels NOT RUN."""
    out = [("host gcc c11", "c", [CC, "-std=c11"], True),
           ("host gcc gnu17", "c", [CC, "-std=gnu17"], True),
           ("host g++ c++17", "c++", [CXX, "-std=c++17"], True),
           ("host g++ gnu++2b", "c++", [CXX, "-std=gnu++2b"], True)]
    not_run = []
    if host_only:
        return out, not_run
    targets = [("esp32s3", "xtensa-esp32s3-elf-", ["-mlongcalls"]),
               ("esp32c3", "riscv32-esp-elf-", ["-march=rv32imc_zicsr_zifencei", "-mabi=ilp32"])]
    for name, tp, extra in targets:
        for lang, tool, std in (("c", "gcc", "-std=gnu17"), ("c++", "g++", "-std=gnu++2b")):
            label = "%s %s %s" % (name, tool, std[5:])
            if shutil.which(tp + tool):
                out.append((label, lang, [tp + tool, std] + extra, False))
            else:
                not_run.append("%s (no %s%s on PATH)" % (label, tp, tool))
    return out, not_run


# ---------------------------------------------------------------- cases

# (name, table, kind "build"|"fail", "Message.signal", input value, body, expected value or None)
# The body runs in main() with r, an int64_t array, and g_in, a volatile uint64_t holding the
# input, so a value is never a constant the compiler can see. P_FIT_ is the table's prefix.
CASES = [
    # --- a fitting value compiles and is put as it is
    ("put u8 into 8 bits", "fit", "build", "Fit_A.u8", 200,
     "const uint8_t v = (uint8_t)g_in; P_FIT_PUT(r, Fit_A, u8, v);", 200),
    ("put const u8 into 8 bits", "fit", "build", "Fit_A.u8", 7,
     "const uint8_t v = (uint8_t)g_in; const uint8_t w = v; P_FIT_PUT(r, Fit_A, u8, w);", 7),
    ("put u8 into 12 bits", "fit", "build", "Fit_A.u12", 255,
     "const uint8_t v = (uint8_t)g_in; P_FIT_PUT(r, Fit_A, u12, v);", 255),
    ("put u16 into 16 bits", "fit", "build", "Fit_A.u16", 65535,
     "const uint16_t v = (uint16_t)g_in; P_FIT_PUT(r, Fit_A, u16, v);", 65535),
    ("put u32 into 32 bits", "fit", "build", "Fit_W.u32", 0xFFFFFFFF,
     "const uint32_t v = (uint32_t)g_in; P_FIT_PUT(r, Fit_W, u32, v);", 0xFFFFFFFF),
    ("put u32 into 63 bits", "fit", "build", "Fit_U63.u63", 0xFFFFFFFF,
     "const uint32_t v = (uint32_t)g_in; P_FIT_PUT(r, Fit_U63, u63, v);", 0xFFFFFFFF),
    ("put u32 into 64 bits", "fit", "build", "Fit_U64.u64", 0x89ABCDEF,
     "const uint32_t v = (uint32_t)g_in; P_FIT_PUT(r, Fit_U64, u64, v);", 0x89ABCDEF),
    ("put u8 into 8 Motorola bits", "fit", "build", "Fit_W.m8", 0xA5,
     "const uint8_t v = (uint8_t)g_in; P_FIT_PUT(r, Fit_W, m8, v);", 0xA5),
    # --- the capped form: any unsigned width, capped at the signal's maximum
    ("cap u32 300 into 4 bits", "fit", "build", "Fit_A.tx_fail", 300,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, Fit_A, tx_fail, v);", 15),
    ("cap u32 16 into 4 bits", "fit", "build", "Fit_A.tx_fail", 16,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, Fit_A, tx_fail, v);", 15),
    ("cap u32 15 into 4 bits", "fit", "build", "Fit_A.tx_fail", 15,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, Fit_A, tx_fail, v);", 15),
    ("cap u32 9 into 4 bits", "fit", "build", "Fit_A.tx_fail", 9,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, Fit_A, tx_fail, v);", 9),
    ("cap u16 4096 into 12 bits", "fit", "build", "Fit_A.u12", 4096,
     "const uint16_t v = (uint16_t)g_in; P_FIT_CAP(r, Fit_A, u12, v);", 4095),
    ("cap u8 255 into 12 bits", "fit", "build", "Fit_A.u12", 255,
     "const uint8_t v = (uint8_t)g_in; P_FIT_CAP(r, Fit_A, u12, v);", 255),
    ("cap u64 max into 32 bits", "fit", "build", "Fit_W.u32", (1 << 64) - 1,
     "const uint64_t v = g_in; P_FIT_CAP(r, Fit_W, u32, v);", 0xFFFFFFFF),
    ("cap u64 max into 63 bits", "fit", "build", "Fit_U63.u63", (1 << 64) - 1,
     "const uint64_t v = g_in; P_FIT_CAP(r, Fit_U63, u63, v);", U63_MAX),
    ("cap u64 2^63 into 63 bits", "fit", "build", "Fit_U63.u63", 1 << 63,
     "const uint64_t v = g_in; P_FIT_CAP(r, Fit_U63, u63, v);", U63_MAX),
    # --- a condition, a fitting mask, a fitting constant
    ("bit true into 1 bit", "fit", "build", "Fit_A.u1", 9,
     "const uint64_t v = g_in; P_FIT_BIT(r, Fit_A, u1, v > 3);", 1),
    ("bit false into 1 bit", "fit", "build", "Fit_A.u1", 2,
     "const uint64_t v = g_in; P_FIT_BIT(r, Fit_A, u1, v > 3);", 0),
    ("mask 0x0F of u32 into 4 bits", "fit", "build", "Fit_A.tx_fail", 0xFFFFFFF6,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, tx_fail, v, 0x0F);", 6),
    ("mask 0x5 of u8 into 3 bits", "fit", "build", "Fit_A.u3", 0xFE,
     "const uint8_t v = (uint8_t)g_in; P_FIT_MASK(r, Fit_A, u3, v, 0x5);", 4),
    ("mask 0xFFF of u32 into 12 bits", "fit", "build", "Fit_A.u12", 0x12345,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, u12, v, 0xFFF);", 0x345),
    ("mask 0x0F of int -1 into 4 bits", "fit", "build", "Fit_A.tx_fail", 0,
     "const int v = (int)g_in - 1; P_FIT_MASK(r, Fit_A, tx_fail, v, 0x0F);", 15),
    ("constant 15 into 4 bits", "fit", "build", "Fit_A.tx_fail", 0,
     "P_FIT_CONST(r, Fit_A, tx_fail, 15);", 15),
    ("constant 0 into 4 bits", "fit", "build", "Fit_A.tx_fail", 0,
     "P_FIT_CONST(r, Fit_A, tx_fail, 0);", 0),
    ("constant 4095 into 12 bits", "fit", "build", "Fit_A.u12", 0,
     "P_FIT_CONST(r, Fit_A, u12, 4095);", 4095),
    # --- D6: a value whose type could exceed its signal, other than through the capped form
    ("put u16 into 8 bits", "fit", "fail", "Fit_A.u8", 1,
     "const uint16_t v = (uint16_t)g_in; P_FIT_PUT(r, Fit_A, u8, v);", None),
    ("put u16 into 12 bits", "fit", "fail", "Fit_A.u12", 1,
     "const uint16_t v = (uint16_t)g_in; P_FIT_PUT(r, Fit_A, u12, v);", None),
    ("put u8 into 4 bits", "fit", "fail", "Fit_A.tx_fail", 1,
     "const uint8_t v = (uint8_t)g_in; P_FIT_PUT(r, Fit_A, tx_fail, v);", None),
    ("put u32 into 24 bits", "fit", "fail", "Fit_W.u24", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_PUT(r, Fit_W, u24, v);", None),
    ("put u64 into 63 bits", "fit", "fail", "Fit_U63.u63", 1,
     "const uint64_t v = g_in; P_FIT_PUT(r, Fit_U63, u63, v);", None),
    ("put u64 into 64 bits", "fit", "fail", "Fit_U64.u64", 1,
     "const uint64_t v = g_in; P_FIT_PUT(r, Fit_U64, u64, v);", None),
    ("put int8 into 8 bits", "fit", "fail", "Fit_A.u8", 1,
     "const int8_t v = (int8_t)g_in; P_FIT_PUT(r, Fit_A, u8, v);", None),
    ("put int into 32 bits", "fit", "fail", "Fit_W.u32", 1,
     "const int v = (int)g_in; P_FIT_PUT(r, Fit_W, u32, v);", None),
    ("put int literal into 8 bits", "fit", "fail", "Fit_A.u8", 1,
     "P_FIT_PUT(r, Fit_A, u8, 5);", None),
    ("cap int32 into 4 bits", "fit", "fail", "Fit_A.tx_fail", 1,
     "const int32_t v = (int32_t)g_in; P_FIT_CAP(r, Fit_A, tx_fail, v);", None),
    ("cap int64 into 63 bits", "fit", "fail", "Fit_U63.u63", 1,
     "const int64_t v = (int64_t)g_in; P_FIT_CAP(r, Fit_U63, u63, v);", None),
    # --- D6: a mask or a constant that exceeds its signal
    ("mask 0x1F into 4 bits", "fit", "fail", "Fit_A.tx_fail", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, tx_fail, v, 0x1F);", None),
    ("mask 0x1000 into 12 bits", "fit", "fail", "Fit_A.u12", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, u12, v, 0x1000);", None),
    ("mask -1 into 4 bits", "fit", "fail", "Fit_A.tx_fail", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, tx_fail, v, -1);", None),
    ("constant 16 into 4 bits", "fit", "fail", "Fit_A.tx_fail", 0,
     "P_FIT_CONST(r, Fit_A, tx_fail, 16);", None),
    ("constant 256 into 8 bits", "fit", "fail", "Fit_A.u8", 0,
     "P_FIT_CONST(r, Fit_A, u8, 256);", None),
    ("constant -1 into 4 bits", "fit", "fail", "Fit_A.tx_fail", 0,
     "P_FIT_CONST(r, Fit_A, tx_fail, -1);", None),
    # --- D6: a signed signal, through each form
    ("put u8 into signed 8 bits", "fit", "fail", "Fit_A.s8", 1,
     "const uint8_t v = (uint8_t)g_in; P_FIT_PUT(r, Fit_A, s8, v);", None),
    ("cap u32 into signed 8 bits", "fit", "fail", "Fit_A.s8", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, Fit_A, s8, v);", None),
    ("bit into signed 1 bit", "fit", "fail", "Fit_A.s1", 1,
     "const uint64_t v = g_in; P_FIT_BIT(r, Fit_A, s1, v > 3);", None),
    ("mask 0x3 into signed 4 bits", "fit", "fail", "Fit_A.s4", 1,
     "const uint32_t v = (uint32_t)g_in; P_FIT_MASK(r, Fit_A, s4, v, 0x3);", None),
    ("constant 1 into signed 4 bits", "fit", "fail", "Fit_A.s4", 0,
     "P_FIT_CONST(r, Fit_A, s4, 1);", None),
    # --- gen-inhibit's width-check case: diag_tx_fail 8 -> 4 bits, a counter it caps
    ("gen-inhibit: cap u32 300 into diag_tx_fail of 4 bits", "gi4", "build",
     "WICAN_Diag_Counters.diag_tx_fail", 300,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, WICAN_Diag_Counters, diag_tx_fail, v);", 15),
    ("gen-inhibit: put u32 into diag_tx_fail of 4 bits", "gi4", "fail",
     "WICAN_Diag_Counters.diag_tx_fail", 300,
     "const uint32_t v = (uint32_t)g_in; P_FIT_PUT(r, WICAN_Diag_Counters, diag_tx_fail, v);", None),
    ("gen-inhibit: cap u32 300 into diag_tx_fail of 8 bits", "gi", "build",
     "WICAN_Diag_Counters.diag_tx_fail", 300,
     "const uint32_t v = (uint32_t)g_in; P_FIT_CAP(r, WICAN_Diag_Counters, diag_tx_fail, v);", 255),
    # --- both projects' headers compile with the checks in them
    ("gen-inhibit: header compiles", "gi", "build", None, 0, "", None),
    ("charge-interposer: header compiles", "intp", "build", None, 0, "", None),
]

PREFIX = {"fit": "fit", "gi4": "gi", "gi": "gi", "intp": "intp"}

PROGRAM = """#include <stdint.h>
#include <stdio.h>
#include "%(table)s.h"

static volatile uint64_t g_in = UINT64_C(%(input)d);

int main(void)
{
    int64_t r[64] = {0};
    (void)g_in;
    (void)r;
    %(body)s
    %(show)s
    return 0;
}
"""


def program(case):
    name, table, kind, sig, value, body, expect = case
    p = PREFIX[table]
    show = ""
    if sig is not None:
        m, s = sig.split(".")
        show = 'printf("VAL %%lld\\n", (long long)r[%s_%s__%s]);' % (p, m, s)
    return PROGRAM % {"table": table, "input": value, "body": body.replace("P_FIT_", p + "_FIT_"),
                      "show": show}


# ---------------------------------------------------------------- the judge

def judge(kind, sig, expect, compiled, stderr, ran, value):
    """-> ("ok" | "FAIL" | "VOID", detail). compiled: the compiler's exit was 0. ran: None
    when the program was not run (a target compiler), else (exit code, stdout)."""
    if kind == "build":
        if not compiled:
            return "FAIL", "did not build: " + first_error(stderr)
        if ran is None or expect is None:
            return "ok", ""
        code, out = ran
        if code != 0:
            return "FAIL", "the program exited %d" % code
        if value != expect:
            return "FAIL", "put %r, expected %d" % (value, expect)
        return "ok", ""
    if compiled:
        return "FAIL", "built"
    for line in stderr.splitlines():
        if "static assertion failed" in line:
            if re.search(re.escape(sig) + r"[: ]", line):
                return "ok", ""
            return "VOID", "a static assertion not naming %s: %s" % (sig, line.strip()[:200])
    return "VOID", "another error: " + first_error(stderr)


def first_error(stderr):
    for line in stderr.splitlines():
        if "error" in line:
            return line.strip()[:240]
    return stderr.strip()[:240]


def selftest():
    sig = "Fit_A.u1"
    rows = [
        (("build", sig, 1, True, "", (0, "VAL 1\n"), 1), "ok"),
        (("build", sig, 1, True, "", (0, "VAL 2\n"), 2), "FAIL"),
        (("build", sig, 1, True, "", (1, ""), None), "FAIL"),
        (("build", sig, 1, True, "", None, None), "ok"),
        (("build", sig, 1, False, "x.c:3: error: expected ';'", None, None), "FAIL"),
        (("build", None, None, True, "", (0, ""), None), "ok"),
        (("fail", sig, None, True, "", None, None), "FAIL"),
        (("fail", sig, None, False, 'x.c:9: error: static assertion failed: "Fit_A.u1: the type"', None, None), "ok"),
        (("fail", sig, None, False, "x.cpp:9: error: static assertion failed: Fit_A.u1 is signed", None, None), "ok"),
        (("fail", sig, None, False, 'x.c:9: error: static assertion failed: "Fit_A.u12: the type"', None, None),
         "VOID"),
        (("fail", sig, None, False, "x.c:9: error: 'v' undeclared", None, None), "VOID"),
    ]
    bad = 0
    for args, want in rows:
        got, detail = judge(*args)
        ok = got == want
        bad += not ok
        print("  %-4s judge %s -> %s%s" % ("ok" if ok else "FAIL", args[:2] + (args[4][:50],), got,
                                           "" if ok else " (wanted %s) %s" % (want, detail)))
    print("\n%s" % ("PASS" if not bad else "FAIL: %d" % bad))
    return 1 if bad else 0


# ---------------------------------------------------------------- tables

def git_show(rev, path):
    r = subprocess.run(["git", "-C", WORKTREE, "show", "%s:%s" % (rev, path)], capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def generate(diag, dbc_path, prefix, outdir, name):
    gen = os.path.join(diag, "tools", "diag_table_gen.py")
    r = subprocess.run([sys.executable, "-B", gen, dbc_path, "--prefix", prefix,
                        "--c-header", os.path.join(outdir, name + ".h"),
                        "--c-source", os.path.join(outdir, name + ".c"),
                        "--python", os.path.join(outdir, name + "_table.py")],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if r.returncode:
        raise Refused("the generator refused %s: %s" % (os.path.basename(dbc_path), r.stderr.strip()[-300:]))


def make_tables(diag, work, use_git):
    """-> {table: folder holding <table>.h}, [labels NOT RUN]."""
    tables, not_run = {}, []
    d = os.path.join(work, "tables")
    os.makedirs(d)
    generate(diag, FIXTURE, PREFIX["fit"], d, "fit")
    tables["fit"] = d
    if not use_git:
        return tables, ["gen-inhibit and charge-interposer tables (--no-git)"]
    gi = git_show(*GI_DBC)
    if gi is None:
        not_run.append("gen-inhibit tables (no %s:%s)" % GI_DBC)
    else:
        if gi.count(GI_TX_FAIL_8) != 1:
            raise Refused("gen-inhibit's DBC: %r is not in it exactly once" % GI_TX_FAIL_8)
        for table, text in (("gi", gi), ("gi4", gi.replace(GI_TX_FAIL_8, GI_TX_FAIL_4))):
            p = os.path.join(d, table + ".dbc")
            open(p, "w", encoding="utf-8", newline="").write(text)
            generate(diag, p, PREFIX[table], d, table)
            tables[table] = d
    ip = git_show(*INTP_DBC)
    if ip is None:
        not_run.append("charge-interposer table (no %s:%s)" % INTP_DBC)
    else:
        p = os.path.join(d, "intp.dbc")
        open(p, "w", encoding="utf-8", newline="").write(ip)
        generate(diag, p, PREFIX["intp"], d, "intp")
        tables["intp"] = d
    return tables, not_run


# ---------------------------------------------------------------- running

def run_one(case, comp, include, tabledir, cdir):
    name, table, kind, sig, value, body, expect = case
    label, lang, argv, runs = comp
    os.makedirs(cdir)
    src = os.path.join(cdir, "case.c" if lang == "c" else "case.cpp")
    open(src, "w", encoding="ascii", newline="\n").write(program(case))
    out = os.path.join(cdir, "case" if runs else "case.o")
    cmd = argv + WARN + ["-I", include, "-I", tabledir, src, "-o", out] + ([] if runs else ["-c"])
    c = subprocess.run(cmd, capture_output=True, text=True)
    ran, val = None, None
    if c.returncode == 0 and runs and sig is not None:
        p = subprocess.run([out], capture_output=True, text=True, timeout=30)
        ran = (p.returncode, p.stdout)
        m = re.search(r"^VAL (-?\d+)$", p.stdout, re.M)
        val = int(m.group(1)) if m else None
    return judge(kind, sig, expect, c.returncode == 0, c.stderr, ran, val)


def run_cases(diag, tables, comps, work, quiet=False):
    """-> list of (case name, compiler label, verdict, detail) for every case whose table exists."""
    include = os.path.join(diag, "include")
    jobs = []
    for i, case in enumerate(CASES):
        if case[1] not in tables:
            continue
        for j, comp in enumerate(comps):
            jobs.append((case, comp, os.path.join(work, "c%02d_%d" % (i, j))))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        futs = [(case, comp, ex.submit(run_one, case, comp, include, tables[case[1]], cdir))
                for case, comp, cdir in jobs]
        for case, comp, f in futs:
            verdict, detail = f.result()
            results.append((case[0], comp[0], verdict, detail))
    if not quiet:
        by_case = {}
        for name, label, verdict, detail in results:
            by_case.setdefault(name, []).append((label, verdict, detail))
        for case in CASES:
            if case[0] not in by_case:
                continue
            rows = by_case[case[0]]
            wrong = [r for r in rows if r[1] != "ok"]
            what = "builds" if case[2] == "build" else "fails on %s" % case[3]
            n_run = sum(1 for c in comps if c[3])
            if case[2] == "build" and case[6] is not None:
                what += ", puts %d" % case[6]
                count = "%d compilers, the value on the %d host ones" % (len(rows), n_run)
            else:
                count = "%d compilers" % len(rows)
            if not wrong:
                print("  ok   %s: %s (%s)" % (case[0], what, count))
            else:
                for label, verdict, detail in wrong:
                    print("  %-4s %s: %s [%s] -- %s" % (verdict, case[0], what, label, detail))
    return results


def rm_tree(path):
    def onerror(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(p)
        except OSError as e:
            print("fit_checks: could not remove %s: %s" % (p, e))
    if os.path.exists(path):
        shutil.rmtree(path, onerror=onerror)
    parent = os.path.dirname(path)
    if os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)


# ---------------------------------------------------------------- the can-fail

# (what it breaks, old text, new text), planted in the fixture's generated header, prefix "fit"
MUTANTS = [
    ("the put form takes a type wider than the signal",
     "sizeof(e) * 8 <= fit_FIT_BITS_(M, S) &&", ""),
    ("the put form takes a 64-bit type",
     "sizeof(e) * 8 < 64,", "1,"),
    ("C: every type taken as unsigned",
     "#define fit_FIT_UNSIGNED_TYPE_(e) ((__typeof__(e))-1 > 0)", "#define fit_FIT_UNSIGNED_TYPE_(e) (1)"),
    ("C++: every integer type taken as unsigned",
     "(std::is_unsigned<std::remove_cv<__typeof__(e)>::type>::value)",
     "(std::is_integral<std::remove_cv<__typeof__(e)>::type>::value)"),
    ("a signed signal taken",
     "static_assert(!fit_##M##__##S##__SIGNED,", "static_assert(1 || fit_##M##__##S##__SIGNED,"),
    ("the capped form does not cap",
     "? fit_FIT_MAX_(M, S) : (int64_t)fit_fit_v_;", "? (int64_t)fit_fit_v_ : (int64_t)fit_fit_v_;"),
    ("the capped form caps one above the maximum",
     "? fit_FIT_MAX_(M, S) : (int64_t)fit_fit_v_;", "? fit_FIT_MAX_(M, S) + 1 : (int64_t)fit_fit_v_;"),
    ("the capped form takes a signed value",
     "static_assert(fit_FIT_UNSIGNED_TYPE_(e),", "static_assert(1,"),
    ("a mask up to twice the signal taken",
     "(int64_t)(mask) <= fit_FIT_MAX_(M, S),", "(int64_t)(mask) <= 2 * fit_FIT_MAX_(M, S) + 1,"),
    ("a negative mask taken",
     "static_assert((int64_t)(mask) >= 0 &&", "static_assert(1 &&"),
    ("a constant one over the signal taken",
     "(int64_t)(c) <= fit_FIT_MAX_(M, S),", "(int64_t)(c) <= fit_FIT_MAX_(M, S) + 1,"),
    ("a negative constant taken",
     "static_assert((int64_t)(c) >= 0 &&", "static_assert(1 &&"),
    ("a signal's maximum one too high below 63 bits",
     "(int64_t)((UINT64_C(1) << (fit_FIT_BITS_(M, S) % 64)) - 1))",
     "(int64_t)((UINT64_C(1) << (fit_FIT_BITS_(M, S) % 64))))"),
]


def mutants(diag, work):
    comps, _ = compilers(host_only=True)
    tables, _ = make_tables(diag, work, use_git=False)
    header = os.path.join(tables["fit"], "fit.h")
    text = open(header, encoding="utf-8").read()
    for what, old, new in MUTANTS:
        if text.count(old) != 1:
            raise Refused("mutant %r: %r is not in the generated header exactly once" % (what, old))
    print("control: the unedited header")
    res = run_cases(diag, tables, comps, os.path.join(work, "control"), quiet=True)
    wrong = [r for r in res if r[2] != "ok"]
    print("  %-4s control: %d of %d case runs as expected%s" % (
        "ok" if not wrong else "FAIL", len(res) - len(wrong), len(res),
        "" if not wrong else " -- " + "; ".join("%s [%s] %s" % (r[0], r[1], r[2]) for r in wrong[:3])))
    survived = 0
    for n, (what, old, new) in enumerate(MUTANTS):
        md = os.path.join(work, "m%02d" % n)
        os.makedirs(md)
        open(os.path.join(md, "fit.h"), "w", encoding="utf-8", newline="").write(text.replace(old, new))
        res = run_cases(diag, {"fit": md}, comps, os.path.join(md, "cases"), quiet=True)
        shown = sorted({"%s [%s] %s" % (r[0], r[1], r[2]) for r in res if r[2] != "ok"})
        survived += not shown
        print("  %-8s m%02d %s%s" % ("KILLED" if shown else "SURVIVED", n, what,
                                     " -- %d case runs, e.g. %s" % (len(shown), shown[0]) if shown else ""))
    print("\n%s" % ("PASS: control as expected, %d of %d mutants killed" % (len(MUTANTS), len(MUTANTS))
                    if not wrong and not survived else
                    "FAIL: control %s, %d mutant(s) survived" % ("not as expected" if wrong else "ok", survived)))
    return 1 if wrong or survived else 0


# ---------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", default=DIAG, help="the shared/diag folder under test")
    ap.add_argument("--no-git", action="store_true", help="the fixture's table only")
    ap.add_argument("--mutants", action="store_true", help="the can-fail")
    ap.add_argument("--selftest", action="store_true", help="the judge on made-up results")
    ap.add_argument("--keep", action="store_true", help="leave the work folder")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        import cantools  # noqa: F401
    except ImportError as e:
        print("fit_checks: refused: %s" % e)
        return 2
    for tool in (CC, CXX):
        if not shutil.which(tool):
            print("fit_checks: refused: no %s" % tool)
            return 2
    diag = os.path.abspath(a.diag)
    work = os.path.join(WORKTREE, "scratch", "diag-fit", str(os.getpid()))
    rm_tree(work)
    os.makedirs(work)
    print("shared/diag under test: %s" % diag)
    try:
        if a.mutants:
            return mutants(diag, work)
        comps, not_run = compilers()
        tables, t_not_run = make_tables(diag, work, not a.no_git)
        not_run += t_not_run
        print("compilers: %s" % "; ".join("%s (%s)" % (c[0], " ".join(c[2])) for c in comps))
        res = run_cases(diag, tables, comps, os.path.join(work, "cases"))
        wrong = [r for r in res if r[2] != "ok"]
        print("\n%s: %d case runs, %d cases x compilers%s" % (
            "PASS" if not wrong else "FAIL: %d case run(s) not as expected" % len(wrong), len(res),
            len({r[0] for r in res}), "; NOT RUN: %s" % "; ".join(not_run) if not_run else ""))
        return 1 if wrong else 0
    except Refused as e:
        print("fit_checks: refused: %s" % e)
        return 2
    finally:
        if a.keep:
            print("kept %s" % work)
        else:
            rm_tree(work)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
