#!/usr/bin/env python3
"""Break the probe's no-acknowledge modes one behaviour at a time; the host tests must fail on each.

The implementor's (SPEC.md T11, T13), beside the host tests it scores. Each
mutant below undoes one behaviour in a copy of twai_port_unacked.c or a
probe register file (twai_port_probe_ll_esp32c3.c,
twai_port_probe_ll_unsupported.c), and builds and runs the three host test
binaries against the copy. The run passes only if the unmutated build passes
and every mutant builds and fails. A mutant that does not build is not a
catch, and one whose text does not occur exactly once ran nothing; either
fails the run. A test binary that runs past TIMEOUT_S is stopped and counts
as failing. Self-checks, not verification.

    python3 shared/twai_port/test_app/test/mutate.py [--only N,N...] [--cc gcc]

From the repo root, where make and the C compiler are (Linux, or WSL). The
copies and binaries go in this run's own folder in the worktree's scratch/,
scratch/twai_port-probe-mutants/<time>_<pid>/, removed when the run ends,
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
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
COPY = "shared/twai_port/test_app"
TEST = "shared/twai_port/test_app/test"
BINS = ("test_unacked", "test_probe_ll_c3", "test_probe_ll_unsupported")
TIMEOUT_S = 120
UN = "shared/twai_port/test_app/twai_port_unacked.c"
C3 = "shared/twai_port/test_app/twai_port_probe_ll_esp32c3.c"
NS = "shared/twai_port/test_app/twai_port_probe_ll_unsupported.c"

# (name, file, text, its replacement). Each text occurs once in its file.
MUTANTS = [
    ("m2 abort in any state", UN,
     "                txp_ll_abort(TXP_ABORT_IF_TS0, &a);\n                if (a.issued)\n                {\n                    record_abort",
     "                txp_ll_abort(TXP_ABORT_ALWAYS, &a);\n                if (a.issued)\n                {\n                    record_abort"),
    ("m3 abort only in an idle", UN,
     "txp_ll_abort(TXP_ABORT_ALWAYS, &a);", "txp_ll_abort(TXP_ABORT_IF_TS0, &a);"),
    ("every TS = 1 sample an attempt", UN,
     "if ((st & TXP_ST_TS) && !(s->prev_st & TXP_ST_TS))", "if (st & TXP_ST_TS)"),
    ("every sample recorded", UN,
     "if (!s->have || st != s->last_st || tec != s->last_tec)", "if (1)"),
    ("TEC change not recorded", UN,
     "if (!s->have || st != s->last_st || tec != s->last_tec)",
     "if (!s->have || st != s->last_st)"),
    ("first sample not recorded", UN,
     "if (!s->have || st != s->last_st || tec != s->last_tec)",
     "if (st != s->last_st || tec != s->last_tec)"),
    ("gap not counted", UN,
     "if (d > s->gap)         { r->gaps++; gap = 1; }", "if (0)         { r->gaps++; gap = 1; }"),
    ("lost changes not counted", UN, "            r->ev_lost++;\n", ""),
    ("m3 delay ignored", UN, "\n                                    + (uint32_t)p->delay_us * cpm;", ";"),
    ("wait_att one attempt early", UN,
     "while (r->attempts < (uint32_t)p->wait_att)",
     "while (r->attempts + 1 < (uint32_t)p->wait_att)"),
    ("m3 wait_us anchors on an earlier rise", UN,
     "            while (r->attempts == att0)", "            while (0 && r->attempts == att0)"),
    ("driver read with interrupts masked", UN,
     "    if (p->irq_off)\n    {\n        return;\n    }", "    if (0 && p->irq_off)\n    {\n        return;\n    }"),
    ("interrupts left masked", UN,
     "    if (p->irq_off)\n    {\n        txp_env_irq_on();\n    }", ""),
    ("no cleanup", UN, "    if (r->clear_t < 0)\n    {\n        cleanup(r, cpm);\n    }",
     "    if (0)\n    {\n        cleanup(r, cpm);\n    }"),
    ("cleanup aborts at TS = 1", UN,
     "        txp_ll_abort(TXP_ABORT_IF_TS0, &a);\n        if (a.issued)\n        {\n            r->cleanup_aborts++;",
     "        txp_ll_abort(TXP_ABORT_ALWAYS, &a);\n        if (a.issued)\n        {\n            r->cleanup_aborts++;"),
    ("fresh ignored", UN,
     "    if (p->fresh)\n    {\n        r->reinstalled = 1;", "    if (0)\n    {\n        r->reinstalled = 1;"),
    ("busy not checked", UN,
     "if (!(r->st_pre & TXP_ST_TBS) || !r->have_pre || r->drv_pre.msgs_to_tx != 0)",
     "if (!r->have_pre)"),
    ("no trail", UN, "        while (r->end_cyc < trail)", "        while (r->end_cyc < trail && 0)"),
    ("no wait for TBS after the abort", UN,
     "while (r->clear_t < 0 && r->end_cyc < bound)",
     "while (r->clear_t < 0 && r->end_cyc < bound && 0)"),
    ("m2 freed-before not seen", UN,
     "                if (r->clear_t >= 0)     { r->freed_before = 1; break; }\n", ""),
    ("times not converted to ns", UN,
     "* 1000000000ull) / run->cpu_hz", "* 1000000ull) / run->cpu_hz"),
    ("event times left in cycles", UN, "to_ns(run, e->t)", "(long long)e->t"),
    ("unsupported chip runs", UN,
     "    if (!txp_ll_supported())\n    {\n        return false;\n    }\n    memset(run",
     "    memset(run"),
    ("no settle", UN, "        txp_env_settle(p->settle_ms);\n", ""),
    ("no clock check", UN,
     "    r->clk_us = txp_env_timer_us() - u_start;", "    r->clk_us = 0;\n    (void)u_start;"),
    ("parse: older phases' arguments allowed", UN,
     "            return refuse(err, errn, foreign[i], \"is not used by m1, m2 or m3\");",
     "            (void)0;"),
    ("parse: another phase's argument allowed", UN,
     "        if (!(nums[i].modes & MB(p->mode)))", "        if (0)"),
    ("parse: both waits allowed", UN,
     "if (p->wait_us >= 0 && p->wait_att >= 0)", "if (0)"),
    ("parse: wait_us past the window allowed", UN,
     "if (p->wait_us >= p->window_us)", "if (0)"),
    ("parse: an unusable argument takes the default", UN,
     "if (g < 0 || !whole(", "if (g > 0 && !whole("),
    ("parse: a quote in the label allowed", UN,
     "        if (*c == '\"' || *c == '\\\\' || (unsigned char)*c < 0x20)", "        if (0)"),
    ("parse: wait_att not defaulted", UN,
     "        if (p->wait_us < 0 && p->wait_att < 0)\n        {\n            p->wait_att = 1;\n        }\n", ""),
    ("c3: if-TS0 ignores TBS", C3,
     "|| (before & (TXP_ST_TBS | TXP_ST_TS)) == 0;", "|| (before & TXP_ST_TS) == 0;"),
    ("c3: TS read from RS", C3,
     "if (st & TWAI_LL_STATUS_TS)  { out |= TXP_ST_TS; }",
     "if (st & TWAI_LL_STATUS_RS)  { out |= TXP_ST_TS; }"),
    ("c3: ES not mapped", C3, "    if (st & TWAI_LL_STATUS_ES)  { out |= TXP_ST_ES; }\n", ""),
    ("c3: after read before the command", C3,
     "    if (issue)\n    {\n        twai_ll_set_cmd_abort_tx(hw);\n    }\n    const uint32_t after = to_neutral(twai_ll_get_status(hw));",
     "    const uint32_t after = to_neutral(twai_ll_get_status(hw));\n    if (issue)\n    {\n        twai_ll_set_cmd_abort_tx(hw);\n    }"),
    ("c3: command outside the critical section", C3,
     "    taskENTER_CRITICAL(&s_probe_lock);\n    const uint32_t before",
     "    const uint32_t before"),
    ("c3: TEC not read", C3, "return twai_ll_get_tec(TWAI_LL_GET_HW(0));", "return 0;"),
    ("unsupported: says supported", NS,
     "bool txp_ll_supported(void)\n{\n    return false;", "bool txp_ll_supported(void)\n{\n    return true;"),
    ("unsupported: abort reported issued", NS, "    out->issued = false;", "    out->issued = true;"),
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


def run(cmd, cwd, timeout=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    return p.returncode, p.stdout


def score(cc, copy, out):
    """'pass', 'fail', 'fail (timed out)' or 'did not build'."""
    rc, _ = run(["make", "-s", "-C", TEST, "CC=" + cc, "OUT=" + out, "all"], copy)
    if rc != 0:
        return "did not build"
    for b in BINS:
        rc, _ = run([os.path.join(out, b)], copy, timeout=TIMEOUT_S)
        if rc is None:
            return "fail (timed out)"
        if rc != 0:
            return "fail"
    return "pass"


def fresh_copy(run_dir):
    copy = os.path.join(run_dir, "copy")
    if not remove_tree(copy):
        sys.exit("the last mutant's copy is still there (above)")
    shutil.copytree(os.path.join(ROOT, COPY), os.path.join(copy, COPY))
    return copy


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated mutant numbers")
    ap.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = ap.parse_args()
    only = None if not a.only else {int(n) for n in a.only.split(",")}

    run_dir = os.path.join(ROOT, "scratch", "twai_port-probe-mutants",
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
                print("M%02d %-44s BAD MUTANT: its text occurs %d times" % (i, name, n))
                ok = False
                continue
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(s.replace(old, new))
            verdict = score(a.cc, copy, os.path.join(run_dir, "bin"))
            killed = verdict.startswith("fail")
            print("M%02d %-44s %s" % (i, name, ("killed" + verdict[4:]) if killed
                                      else "SURVIVED (" + verdict + ")"), flush=True)
            ok = ok and killed
        print("mutants: %d, %s" % (len(MUTANTS), "all killed" if ok else "NOT ALL KILLED"))
        return 0 if ok else 1
    finally:
        remove_tree(run_dir)


if __name__ == "__main__":
    sys.exit(main())
