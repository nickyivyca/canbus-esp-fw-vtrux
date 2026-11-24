#!/usr/bin/env python3
"""Spec 10's compile-time width check, and its can-fail (stage (b) batch 2, 2026-11-23).

Spec 10: "a compile-time check against the frame table's signal widths fails the build
when a field's type could exceed its signal without a cap". Each case compiles
gen_inhibit_core.c (-c, the firmware's C dialect gnu17, -Werror off) against a frame
table, in a copy under scratch/width-check/<pid>/:

  control              the core and the committed table as they are: builds
  dbc-narrow-tx_ok     the DBC with diag_tx_ok 32 -> 16 bits, the table regenerated
                       with the pinned shared/diag generator (a u32 counter, no cap):
                       must FAIL, naming WICAN_Diag_Counters.diag_tx_ok
  dbc-narrow-flags     diag_flags 8 -> 7 bits (a u8, no cap): must FAIL, naming it
  dbc-narrow-mode      diag_mode 8 -> 2 bits (an enum whose last value, PASSIVE 4,
                       does not fit): must FAIL, naming it
  dbc-signed-flags     diag_flags made signed: must FAIL, naming it
  core-wide-flags      the core putting diag_flags as a uint16_t: must FAIL, naming it
  core-signed-flags    the core putting diag_flags as an int8_t: must FAIL, naming it
  dbc-narrow-tx_fail   diag_tx_fail 8 -> 4 bits: a field the core caps at the signal's
                       maximum (spec 10 names tx_fail among the capped ones), which spec
                       10 allows: builds

A FAIL case passes only when the compiler stops on a static assertion naming the
signal; another compile error is not the width check and the case is VOID. The core's
and the DBC's text are read only to plant the cases. Exit 0 every case as expected, 1
not, 2 refused (no pinned generator, no cantools, an anchor not found).

--selftest runs the judge on made-up compiler results: a build where one was expected,
an error where a build was, a build where a failure was, a static assertion naming the
signal, one naming another (VOID) and another error (VOID).

Run in build.slice: python3.13 -B width_check.py [--keep] | --selftest
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_host as DH  # noqa: E402
sys.path.insert(0, os.path.join(DH.PROJECT, "tools", "bench"))
import gi_paths  # noqa: E402

# By its git path, read at HEAD with git show, as build_output.py reads it (test_gi_diag item 7)
DBC_GIT = "gen-inhibit/diag/vtrux-wican-diag.dbc"
CORE = os.path.join(DH.MAIN, "gen_inhibit_core.c")
TMP = os.path.join(DH.WORKTREE, "scratch", "width-check", str(os.getpid()))
CC = os.environ.get("CC", "gcc")
PREFIX = "gi_dbc"   # the committed table's (build_output.TABLE_PREFIX)

FLAGS_PUT = "GI_PUT(v, WICAN_Diag_Status, diag_flags, flags);"

# (name, DBC substitutions [(old, new)], core substitutions, expect "fail" naming M.S, or "build")
CASES = [
    ("control", [], [], "build", None),
    ("dbc-narrow-tx_ok", [(" SG_ diag_tx_ok : 0|32@1+", " SG_ diag_tx_ok : 0|16@1+")], [], "fail",
     "WICAN_Diag_Counters.diag_tx_ok"),
    ("dbc-narrow-flags", [(" SG_ diag_flags : 24|8@1+", " SG_ diag_flags : 24|7@1+")], [], "fail",
     "WICAN_Diag_Status.diag_flags"),
    ("dbc-narrow-mode", [(" SG_ diag_mode : 16|8@1+", " SG_ diag_mode : 16|2@1+")], [], "fail",
     "WICAN_Diag_Status.diag_mode"),
    ("dbc-signed-flags", [(" SG_ diag_flags : 24|8@1+", " SG_ diag_flags : 24|8@1-")], [], "fail",
     "WICAN_Diag_Status.diag_flags"),
    ("core-wide-flags", [], [(FLAGS_PUT, "GI_PUT(v, WICAN_Diag_Status, diag_flags, (uint16_t)flags);")], "fail",
     "WICAN_Diag_Status.diag_flags"),
    ("core-signed-flags", [], [(FLAGS_PUT, "GI_PUT(v, WICAN_Diag_Status, diag_flags, (int8_t)flags);")], "fail",
     "WICAN_Diag_Status.diag_flags"),
    ("dbc-narrow-tx_fail", [(" SG_ diag_tx_fail : 32|8@1+", " SG_ diag_tx_fail : 32|4@1+")], [], "build", None),
]


class Refused(Exception):
    pass


def sub(text, pairs, what):
    for old, new in pairs:
        if text.count(old) != 1:
            raise Refused("%s: %r is not in it exactly once" % (what, old))
        text = text.replace(old, new)
    return text


def restate_hash(text):
    import gi_diag
    D = gi_diag.core()
    _v, h = D.schema_identity(D.load(text=text))
    return text.replace('BA_ "DiagSchemaHash" "%s";' % h,
                        'BA_ "DiagSchemaHash" "%s";' % D.layout_hash(D.load(text=text)))


def run_case(gen, name, dbc_subs, core_subs):
    d = os.path.join(TMP, name)
    os.makedirs(d)
    for f in os.listdir(DH.MAIN):
        if f.endswith(".h") or f in ("gen_inhibit_core.c", "gi_diag_table.c"):
            shutil.copy(os.path.join(DH.MAIN, f), d)
    if dbc_subs:
        import gi_diag
        dbc = os.path.join(d, os.path.basename(DBC_GIT))
        text = gi_diag.git(DH.WORKTREE, "show", "HEAD:%s" % DBC_GIT).decode("utf-8")
        open(dbc, "w", encoding="utf-8", newline="").write(restate_hash(sub(text, dbc_subs, "the DBC")))
        r = subprocess.run([sys.executable, "-B", gen, dbc, "--prefix", PREFIX, "--c-header", "gi_diag_table.h",
                            "--c-source", "gi_diag_table.c", "--python", "gi_diag_table.py"],
                           cwd=d, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if r.returncode:
            raise Refused("%s: the generator refused the planted DBC: %s" % (name, r.stderr.strip()[-300:]))
    if core_subs:
        p = os.path.join(d, "gen_inhibit_core.c")
        text = sub(open(p, encoding="utf-8").read(), core_subs, "the core")   # read before the "w" truncates
        open(p, "w", encoding="utf-8").write(text)
    inc = "-I" + os.path.join(DH.diag_dir(), "include")
    r = subprocess.run([CC, "-std=gnu17", "-c", "-o", os.path.join(d, "core.o"), "-I" + d, inc,
                        os.path.join(d, "gen_inhibit_core.c")], capture_output=True, text=True)
    return r.returncode, r.stderr


def judge(expect, sig, rc, err):
    if expect == "build":
        return ("PASS" if rc == 0 else "FAIL"), ("builds" if rc == 0 else "did not build: %s" % last_error(err))
    if rc == 0:
        return "FAIL", "BUILT: the width check let it through"
    asserts = [ln for ln in err.splitlines() if "static assert" in ln]
    if any(sig in ln for ln in asserts):
        return "PASS", "stopped: %s" % next(ln for ln in asserts if sig in ln).split("error:", 1)[-1].strip()[:160]
    return "VOID", "did not build, but not on a static assertion naming %s: %s" % (sig, last_error(err))


def last_error(err):
    e = [ln for ln in err.splitlines() if "error" in ln]
    return (e[0] if e else err.strip()[-200:])[:200]


def selftest():
    """judge() on made-up compiler results, so each verdict is shown reachable."""
    sig = "M.S"
    cases = [
        ("build expected, built", ("build", None, 0, ""), "PASS"),
        ("build expected, an error", ("build", None, 1, "x.c:1: error: boom"), "FAIL"),
        ("fail expected, built", ("fail", sig, 0, ""), "FAIL"),
        ("fail expected, a static assertion naming it", ("fail", sig, 1,
                                                          'x.c:9: error: static assertion failed: "M.S: no"'), "PASS"),
        ("fail expected, a static assertion naming another", ("fail", sig, 1,
                                                               'x.c:9: error: static assertion failed: "M.T: no"'),
         "VOID"),
        ("fail expected, another error", ("fail", sig, 1, "x.c:3: error: expected ';'"), "VOID"),
    ]
    bad = 0
    for label, a, want in cases:
        got = judge(*a)[0]
        bad += got != want
        print("  %-4s %-50s want %-4s got %s" % ("ok" if got == want else "FAIL", label, want, got))
    print("selftest: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
    return 1 if bad else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    try:
        gen = os.path.join(DH.diag_dir(), "tools", "diag_table_gen.py")
        import cantools  # noqa: F401  the generator's
    except (DH.DiagHostError, ImportError) as e:
        print("width_check: refused: %s" % e)
        return 2
    gi_paths.remove_tree(TMP)
    os.makedirs(TMP)
    bad = 0
    try:
        for name, dsubs, csubs, expect, sig in CASES:
            try:
                rc, err = run_case(gen, name, dsubs, csubs)
            except Refused as e:
                print("width_check: refused: %s" % e)
                return 2
            v, why = judge(expect, sig, rc, err)
            bad += v != "PASS"
            print("[%-4s] %-20s expect %-5s %s" % (v, name, expect, why))
    finally:
        if "--keep" not in argv:
            gi_paths.remove_tree(TMP)
            parent = os.path.dirname(TMP)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
    print("\n%s" % ("PASS: every case as spec 10 asks" if not bad else "FAIL: %d case(s) not as expected" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
