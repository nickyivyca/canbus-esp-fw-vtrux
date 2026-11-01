#!/usr/bin/env python3
"""Break restart_guard one behaviour at a time; its unit tests must fail on each.

The implementor's (SPEC.md G6), beside the unit tests it scores. Each mutant
below undoes one behaviour of the guard in a copy of restart_guard.c (G2 to
G4), and builds and runs test_restart_guard against the copy. The run passes
only if the unmutated build passes and every mutant builds and fails. A
mutant that does not build is not a catch, and one whose text does not occur
exactly once ran nothing; either fails the run. Self-checks, not
verification.

    python3 shared/restart_guard/unit/mutate.py [--only N,N...] [--cc gcc]

From the repo root, where make and the C compiler are (Linux, or WSL). The
copies and binaries go in this run's own folder in the worktree's scratch/,
scratch/restart_guard-unit-mutants/<time>_<pid>/, removed when the run ends,
pass or fail (AGENTS.md "Removing scratch").
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
# The unit build reads can_port's header and twai_port's stand-ins.
COMPONENTS = ("shared/restart_guard", "shared/can_port", "shared/twai_port")
UNIT = "shared/restart_guard/unit"
RG = "shared/restart_guard/restart_guard.c"
BINS = ("test_restart_guard",)

# (name, file, text, its replacement). Each text occurs once in its file.
MUTANTS = [
    ("interrupts left on", RG,
     "#if CONFIG_IDF_TARGET_ARCH_RISCV\n    rv_utils_intr_global_disable();\n#else",
     "#if CONFIG_IDF_TARGET_ARCH_RISCV\n    (void)0;\n#else"),
    ("standby before the stop", RG,
     "        can_port_restart_report_t s;\n        if (can_port_restart_stop(port, &s) == ESP_OK &&",
     "        can_port_restart_report_t s;\n        (void)board_can_standby(port, true);\n        if (can_port_restart_stop(port, &s) == ESP_OK &&"),
    ("port 0 only", RG,
     "    for (int port = 0; port < ports && port < 32; port++)",
     "    for (int port = 0; port < ports && port < 1; port++)"),
    ("standby not called", RG,
     "        if (board_can_standby(port, true) == ESP_OK)\n        {\n            r.did_stdby |= 1u << port;\n        }\n",
     ""),
    ("standby flag not per port", RG,
     "            r.did_stdby |= 1u << port;", "            r.did_stdby = 1u;"),
    ("stop's answer ignored", RG,
     "        if (can_port_restart_stop(port, &s) == ESP_OK &&",
     "        if ((void)can_port_restart_stop(port, &s), 1 &&"),
    ("any port's report recorded", RG,
     " &&\n            board_can_kind(port) == BOARD_CAN_TWAI)", ")"),
    ("entry count from 0 every time", RG,
     "    r.entries   = (rg_valid(&s_rec) ? s_rec.entries : 0u) + 1u;",
     "    r.entries   = 1u;"),
    ("entry count kept from an invalid record", RG,
     "    r.entries   = (rg_valid(&s_rec) ? s_rec.entries : 0u) + 1u;",
     "    r.entries   = s_rec.entries + 1u;"),
    ("checksum not written", RG, "    r.check = rg_sum(&r);\n", "    r.check = 0;\n"),
    ("checksum over the check word too", RG,
     "         i < offsetof(restart_guard_record_t, check) / sizeof(uint32_t); i++)",
     "         i <= offsetof(restart_guard_record_t, check) / sizeof(uint32_t); i++)"),
    ("valid on the magic alone", RG,
     "    return r->magic == RESTART_GUARD_RECORD_MAGIC && r->check == rg_sum(r);",
     "    return r->magic == RESTART_GUARD_RECORD_MAGIC;"),
    ("record not stored", RG, "    s_rec = r;\n", ""),
    ("timed_out not recorded", RG,
     "            r.timed_out = s.timed_out ? 1u : 0u;", "            r.timed_out = 0u;"),
    ("did_twai from the clock", RG,
     "            r.did_twai  = s.entered_reset ? 1u : 0u;",
     "            r.did_twai  = s.clock_on ? 1u : 0u;"),
    ("init leaves entries", RG,
     "    memset(&s_rec, 0, sizeof(s_rec));\n", ""),
]


def long_path(p):
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def remove_tree(p):
    """Remove `p`, clearing read-only flags and retrying, and say so when it
    can't (AGENTS.md "Removing scratch": never ignore_errors=True)."""
    def retry(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError as e:
            print("could not remove %s: %s" % (path, e))
    if os.path.lexists(p):
        if sys.version_info >= (3, 12):
            shutil.rmtree(long_path(p), onexc=retry)
        else:
            shutil.rmtree(long_path(p), onerror=retry)
    if os.path.lexists(p):
        print("LEFT BEHIND: %s" % p)
        return False
    return True


def run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def score(cc, copy, out):
    """'pass', 'fail' or 'did not build', for the copy at `copy`."""
    rc, _ = run(["make", "-s", "-C", UNIT, "CC=" + cc, "OUT=" + out, "all"], copy)
    if rc != 0:
        return "did not build"
    for b in BINS:
        rc, _ = run([os.path.join(out, b)], copy)
        if rc != 0:
            return "fail"
    return "pass"


def fresh_copy(run_dir):
    copy = os.path.join(run_dir, "copy")
    if not remove_tree(copy):
        sys.exit("the last mutant's copy is still there (above)")
    for d in COMPONENTS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(copy, d))
    return copy


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated mutant numbers")
    ap.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = ap.parse_args()
    only = None if not a.only else {int(n) for n in a.only.split(",")}

    run_dir = os.path.join(ROOT, "scratch", "restart_guard-unit-mutants",
                           "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(run_dir)
    try:
        copy = fresh_copy(run_dir)
        clean = score(a.cc, copy, os.path.join(run_dir, "bin"))
        print("unmutated: %s" % clean)
        if clean != "pass":
            print("the unmutated build must pass before any mutant means anything")
            return 2
        ok = True
        for i, (name, f, old, new) in enumerate(MUTANTS):
            if only is not None and i not in only:
                continue
            copy = fresh_copy(run_dir)
            p = os.path.join(copy, f)
            with open(p, encoding="utf-8", newline="") as fh:
                s = fh.read()
            n = s.count(old)
            if n != 1:
                print("M%02d %-40s BAD MUTANT: its text occurs %d times" % (i, name, n))
                ok = False
                continue
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(s.replace(old, new))
            verdict = score(a.cc, copy, os.path.join(run_dir, "bin"))
            killed = verdict == "fail"
            print("M%02d %-40s %s" % (i, name, "killed" if killed
                                      else "SURVIVED (" + verdict + ")"), flush=True)
            ok = ok and killed
        print("mutants: %d, %s" % (len(MUTANTS), "all killed" if ok else "NOT ALL KILLED"))
        return 0 if ok else 1
    finally:
        remove_tree(run_dir)


if __name__ == "__main__":
    sys.exit(main())
