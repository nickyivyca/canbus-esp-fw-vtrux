#!/usr/bin/env python3
"""The tests of diag_host.py and the misfit wrapper (spec 12.3 item 2), with their can-fails.

  1. diag_host.py: the pinned tag is vtrux/diag's in firmware/main/idf_component.yml, its
     checkout has the packer; a project folder holding shared/diag (gi_host_run.sh's
     copy) is taken first; a pin with no checkout is refused, naming the command.
  2. misfit_wrap.c, on misfit_wrap_test.c: a fit is not recorded; a misfit is, with its
     id, count, mask, name and value, the caller's mask set and the frame as the packer
     left it; a refused table entry is recorded as -1; a record nobody takes ends the
     process with exit 3 and the line on stderr.
  3. End to end, the can-fail: host_runner built on a copy of the core with one field
     planted out of range (0x7F3's diag_reserved16 set to 65536, past the static width
     check, written straight into the raw array) prints a "!! DIAG MISFIT" line naming
     it at each 0x7F3, and ../gen_inhibit_host/invariants.py fails that trace; the same
     copy unplanted prints none and passes.
  4. The wrap itself: host_runner linked with misfit_wrap.c but without
     --wrap=diag_pack_message (the core calls the packer directly) refuses its run with
     "DIAG PACKER NOT WRAPPED", exit 2, so a build that cannot see a misfit cannot pass.

Builds under scratch/diag-host-test/<pid>/, removed afterwards. Exit 0 all pass, 1 not.
Run in build.slice: python3.13 -B test_diag_host.py. Gen-inhibit tester, 2026-11-23.
"""

import glob
import importlib.util
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_host as DH  # noqa: E402

HOST = os.path.join(DH.PROJECT, "test", "gen_inhibit_host")
sys.path.insert(0, os.path.join(DH.PROJECT, "tools", "bench"))
import gi_paths  # noqa: E402  remove_tree

TMP = os.path.join(DH.WORKTREE, "scratch", "diag-host-test", str(os.getpid()))
CC = os.environ.get("CC", "gcc")
FAILS = []

PLANT_FROM = "GI_PUT_CONST(v, WICAN_Diag_Build, diag_reserved16, 0);"
PLANT_TO = "v[gi_dbc_WICAN_Diag_Build__diag_reserved16] = 65536;   /* planted: a misfit */"


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, "" if ok else "  -- " + str(detail)[:400]))
    if not ok:
        FAILS.append(label)


def cc(args, out):
    r = subprocess.run([CC, "-std=c11", "-O1", "-g", "-o", out] + args, capture_output=True, text=True)
    return r


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def part1():
    tag = DH.pinned_tag()
    d = DH.diag_dir()
    check("1 the pinned tag is read (%s) and its checkout has diag_pack.c" % tag,
          os.path.isfile(os.path.join(d, "diag_pack.c")) and ("shared-" + tag) in d, d)
    a = DH.args(wrap=True)
    check("1 args: the table, the packer, its include, the wrapper and the link flag",
          a[0] == DH.TABLE_C and a[1].endswith("diag_pack.c") and a[2] == "-I" + os.path.join(d, "include")
          and DH.WRAP_C in a and DH.WRAP_LD in a, a)
    fake = os.path.join(TMP, "proj")
    os.makedirs(os.path.join(fake, "test", "diag_host"))
    os.makedirs(os.path.join(fake, "shared", "diag"))
    open(os.path.join(fake, "shared", "diag", "diag_pack.c"), "w").write("/* the shipped copy */\n")
    saved = DH.PROJECT
    DH.PROJECT = fake
    try:
        check("1 a project folder with shared/diag (the ~/gi_host copy) is taken first",
              DH.diag_dir() == os.path.join(fake, "shared", "diag"), DH.diag_dir())
    finally:
        DH.PROJECT = saved
    try:
        DH.diag_dir("1999.01.01")
        check("1 a tag with no checkout is refused", False, "not refused")
    except DH.DiagHostError as e:
        check("1 a tag with no checkout is refused, naming the command", "git worktree add" in str(e), e)


def part2():
    d = DH.diag_dir()
    exe = os.path.join(TMP, "mwt")
    r = cc([os.path.join(HERE, "misfit_wrap_test.c")] + DH.args(wrap=True)[1:], exe)
    check("2 misfit_wrap_test builds", r.returncode == 0, r.stderr)
    if r.returncode:
        return
    for case in ("fit", "misfit", "bad"):
        p = subprocess.run([exe, case], capture_output=True, text=True)
        bad = [ln for ln in p.stdout.splitlines() if ln.startswith("FAIL")]
        # "bad" leaves nothing untaken; each case takes what it makes
        check("2 %s: every check holds, exit 0" % case, p.returncode == 0 and not bad, (p.returncode, bad, p.stderr))
    p = subprocess.run([exe, "untaken"], capture_output=True, text=True)
    check("2 a record nobody takes: exit 3, the line on stderr",
          p.returncode == 3 and "!! DIAG MISFIT id=7FA count=1" in p.stderr and "not taken" in p.stderr,
          (p.returncode, p.stderr))


def core_copy(name, plant=False):
    d = os.path.join(TMP, name)
    os.makedirs(d)
    for f in glob.glob(os.path.join(DH.MAIN, "*.h")) + [os.path.join(DH.MAIN, "gen_inhibit_core.c"), DH.TABLE_C]:
        shutil.copy(f, d)
    if plant:
        p = os.path.join(d, "gen_inhibit_core.c")
        t = open(p, encoding="utf-8").read()
        if t.count(PLANT_FROM) != 1:
            raise SystemExit("the plant's anchor is not in the core once: %r" % PLANT_FROM)
        open(p, "w", encoding="utf-8").write(t.replace(PLANT_FROM, PLANT_TO))
    return d


def runner(coredir, out, wrap=True):
    args = ["-I" + coredir, os.path.join(HOST, "host_runner.c"), os.path.join(coredir, "gen_inhibit_core.c")]
    if wrap:
        args += DH.args(wrap=True, table_c=os.path.join(coredir, "gi_diag_table.c"))
    else:
        # the wrapper linked, its __real_ bound to the packer, but no --wrap: the core
        # calls diag_pack_message() itself and the wrapper never sees a call
        d = DH.diag_dir()
        args += [os.path.join(coredir, "gi_diag_table.c"), os.path.join(d, "diag_pack.c"),
                 "-I" + os.path.join(d, "include"), DH.WRAP_C, "-I" + HERE,
                 "-Wl,--defsym=__real_diag_pack_message=diag_pack_message"]
    return cc(args, out)


SCENARIO = "mode 0 3 500\nend 2000000\n"


def walk(trace, scn):
    inv = load("gi_invariants", os.path.join(HOST, "invariants.py"))
    c = inv.Checker("diag-host-test", scn)
    inv.walk(c, trace)
    return c.problems


def part3_4():
    scn = os.path.join(TMP, "s.scn")
    open(scn, "w").write(SCENARIO)
    for name, plant in (("clean", False), ("planted", True)):
        d = core_copy(name, plant)
        exe = os.path.join(TMP, "hr_" + name)
        r = runner(d, exe)
        check("3 %s copy: host_runner builds" % name, r.returncode == 0, r.stderr)
        if r.returncode:
            continue
        p = subprocess.run([exe], input=SCENARIO, capture_output=True, text=True)
        mis = [ln for ln in p.stdout.splitlines() if " !! DIAG " in ln]
        n7f3 = sum(1 for ln in p.stdout.splitlines() if " TX DIAG id=7F3 " in ln)
        probs = walk(p.stdout, scn)
        if plant:
            check("3 planted: a MISFIT line per 0x7F3, naming diag_reserved16 = 65536 (%d of %d)" % (len(mis), n7f3),
                  n7f3 > 0 and len(mis) == n7f3 and all("DIAG MISFIT id=7F3 count=1" in m and
                                                        "first=diag_reserved16 raw=65536" in m for m in mis),
                  (mis[:2], n7f3))
            check("3 planted: invariants.py fails the trace on it",
                  any("12.3 item 2" in x for x in probs), probs[:3])
            check("3 planted: the runner's exit is 0 (the trace, not the exit, carries it)", p.returncode == 0,
                  p.returncode)
        else:
            check("3 clean copy: no MISFIT line, exit 0, %d 0x7F3 sent" % n7f3,
                  not mis and p.returncode == 0 and n7f3 > 0, (mis[:2], p.returncode, p.stderr[-200:]))
            check("3 clean copy: invariants.py passes it", not probs, probs[:3])
    d = os.path.join(TMP, "clean")
    exe = os.path.join(TMP, "hr_unwrapped")
    r = runner(d, exe, wrap=False)
    check("4 host_runner without --wrap builds", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        p = subprocess.run([exe], input=SCENARIO, capture_output=True, text=True)
        check("4 without --wrap: DIAG PACKER NOT WRAPPED, exit 2",
              p.returncode == 2 and "DIAG PACKER NOT WRAPPED" in p.stdout, (p.returncode, p.stdout[-300:]))
        check("4 without --wrap: invariants.py fails the trace on it",
              any("PACKER NOT WRAPPED" in x for x in walk(p.stdout, scn)))


def main():
    gi_paths.remove_tree(TMP)
    os.makedirs(TMP)
    try:
        part1()
        part2()
        part3_4()
    finally:
        gi_paths.remove_tree(TMP)
        parent = os.path.dirname(TMP)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    print("\n=== %s ===" % ("PASS" if not FAILS else "FAIL: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
