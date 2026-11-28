#!/usr/bin/env python3
"""Break the probe's no-acknowledge modes one behaviour at a time; the host tests must fail on each.

The implementor's (SPEC.md T11, T13), beside the host tests it scores. Each
mutant below undoes one behaviour in a copy of twai_port_unacked.c or a
probe register file (twai_port_probe_ll_esp32c3.c,
twai_port_probe_ll_esp32s3.c, twai_port_probe_ll_unsupported.c) or the T19
command line (twai_port_t19_parse.c), and builds and runs the five host test
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
BINS = ("test_unacked", "test_probe_ll_c3", "test_probe_ll_s3", "test_probe_ll_unsupported",
        "test_t19_parse")
TIMEOUT_S = 120
UN = "shared/twai_port/test_app/twai_port_unacked.c"
C3 = "shared/twai_port/test_app/twai_port_probe_ll_esp32c3.c"
S3 = "shared/twai_port/test_app/twai_port_probe_ll_esp32s3.c"
T19 = "shared/twai_port/test_app/twai_port_t19_parse.c"
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
    ("no cleanup", UN, "    if (r->cleared == TXP_CLR_NONE)\n    {\n        cleanup(r, cpm);\n    }",
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
    ("no hold", UN, "    if (p->hold_ms > 0)\n    {\n        hold(p, r);",
     "    if (0)\n    {\n        hold(p, r);"),
    ("hold only when the trace saw no free", UN,
     "    if (p->hold_ms > 0)\n    {\n        hold(p, r);",
     "    if (p->hold_ms > 0 && r->clear_t < 0)\n    {\n        hold(p, r);"),
    ("cleanup ignores a frame freed in the hold", UN,
     "    if (r->cleared == TXP_CLR_NONE)\n    {\n        cleanup(r, cpm);",
     "    if (r->clear_t < 0)\n    {\n        cleanup(r, cpm);"),
    ("freed in the hold not reported", UN,
     "        r->cleared = TXP_CLR_HOLD;\n", ""),
    ("hold end read before the hold", UN,
     "    txp_env_hold(p->hold_ms);\n    r->hold_cyc = txp_env_cycles() - c;\n    r->held = 1;\n    r->hold_st = txp_ll_status();",
     "    r->hold_st = txp_ll_status();\n    txp_env_hold(p->hold_ms);\n    r->hold_cyc = txp_env_cycles() - c;\n    r->held = 1;"),
    ("hold not timed", UN, "    r->hold_cyc = txp_env_cycles() - c;", "    r->hold_cyc = 0;\n    (void)c;"),
    ("hold TEC not read", UN, "    r->hold_tec = txp_ll_tec();", "    r->hold_tec = 0;"),
    ("hold alerts not read", UN, "    r->alerts_hold = txp_env_alerts();", "    r->alerts_hold = 0;"),
    ("hold before the driver reads", UN,
     "    r->have_after = txp_env_driver(&r->drv_after);\n",
     "    if (p->hold_ms > 0) { txp_env_hold(1); }\n    r->have_after = txp_env_driver(&r->drv_after);\n"),
    ("hold with interrupts masked", UN,
     "    if (p->irq_off)\n    {\n        txp_env_irq_on();\n    }\n",
     "    if (p->irq_off && p->hold_ms > 0)\n    {\n        hold(p, r);\n    }\n    if (p->irq_off)\n    {\n        txp_env_irq_on();\n    }\n"),
    ("parse: hold_ms past 2000 allowed", UN,
     "{ \"hold_ms\",   P_OFF(hold_ms),   0,   2000,", "{ \"hold_ms\",   P_OFF(hold_ms),   0,   20000,"),
    ("answer: trial buffer on the stack", UN,
     "    char *const b = s_ans.b;\n    int n;", "    char b[ANS_B];\n    int n;"),
    ("answer: head buffer on the stack", UN,
     "    char *const b = s_ans.b;\n    const int n = snprintf(b, ANS_B,",
     "    char b[ANS_B];\n    const int n = snprintf(b, ANS_B,"),
    ("answer: driver views on the stack", UN,
     "    char *const d0 = s_ans.d[0];\n    char *const d1 = s_ans.d[1];\n"
     "    char *const d2 = s_ans.d[2];\n    char *const d3 = s_ans.d[3];\n",
     "    char d0[ANS_D], d1[ANS_D], d2[ANS_D], d3[ANS_D];\n"),
    ("answer: stack margin not reported", UN,
     "(unsigned long)txp_env_stack_free());", "0ul);"),
    ("answer: hold_ns given with no hold", UN,
     "            \"\\\"hold_ns\\\":-1,", "            \"\\\"hold_ns\\\":0,"),
    ("answer: hold TCS from TBS", UN,
     "            (r->hold_st & TXP_ST_TCS) ? 1u : 0u,", "            (r->hold_st & TXP_ST_TBS) ? 1u : 0u,"),
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
    ("s3: if-TS0 ignores TBS", S3,
     "|| (before & (TXP_ST_TBS | TXP_ST_TS)) == 0;", "|| (before & TXP_ST_TS) == 0;"),
    ("s3: TS read from RS", S3,
     "if (st & TWAI_LL_STATUS_TS)  { out |= TXP_ST_TS; }",
     "if (st & TWAI_LL_STATUS_RS)  { out |= TXP_ST_TS; }"),
    ("s3: ES not mapped", S3, "    if (st & TWAI_LL_STATUS_ES)  { out |= TXP_ST_ES; }\n", ""),
    ("s3: after read before the command", S3,
     "    if (issue)\n    {\n        twai_ll_set_cmd_abort_tx(hw);\n    }\n    const uint32_t after = to_neutral(twai_ll_get_status(hw));",
     "    const uint32_t after = to_neutral(twai_ll_get_status(hw));\n    if (issue)\n    {\n        twai_ll_set_cmd_abort_tx(hw);\n    }"),
    ("s3: command outside the critical section", S3,
     "    taskENTER_CRITICAL(&s_probe_lock);\n    const uint32_t before",
     "    const uint32_t before"),
    ("s3: TEC not read", S3, "return twai_ll_get_tec(TWAI_LL_GET_HW(0));", "return 0;"),
    ("s3: says unsupported", S3,
     "bool txp_ll_supported(void)\n{\n    return true;", "bool txp_ll_supported(void)\n{\n    return false;"),
    ("unsupported: says supported", NS,
     "bool txp_ll_supported(void)\n{\n    return false;", "bool txp_ll_supported(void)\n{\n    return true;"),
    ("unsupported: abort reported issued", NS, "    out->issued = false;", "    out->issued = true;"),
    ("t19: long line not refused", T19,
     "    if (len > T19_LINE_MAX)\n    {", "    if (len > T19_LINE_MAX + 1)\n    {"),
    ("t19: empty key taken", T19, "if (eq == NULL || eq == tok)", "if (eq == NULL)"),
    ("t19: one argument too many taken", T19,
     "if (out->n >= T19_ARGS_MAX)", "if (out->n >= T19_ARGS_MAX + 1 && out->n > 99)"),
    ("t19: value that does not fit taken", T19,
     "if (vn == 0 || len >= vn)", "if (vn == 0 || len > vn)"),
    ("t19: last of two keys found", T19,
     "            memcpy(val, l->val[i], len + 1);\n            return 1;",
     "            memcpy(val, l->val[i], len + 1);\n            if (i + 1 < l->n && strcmp(l->key[l->n - 1], key) == 0) { memcpy(val, l->val[l->n - 1], strlen(l->val[l->n - 1]) + 1); }\n            return 1;"),
    ("t19: unknown key passed", T19, "            return l->key[i];", "            (void)0;"),
    ("t19: number over the range taken", T19,
     "v < lo || v > hi", "v < lo || v > hi + 1"),
    ("t19: trailing characters taken", T19,
     "if (end == s || *end != '\\0' ||", "if (end == s ||"),
    ("t19: quote not escaped", T19,
     "if (c == '\"' || c == '\\\\')", "if (c == '\\\\')"),
    ("t19: control characters raw", T19, "else if (c < 0x20)", "else if (c < 0x09)"),
    ("t19: string one past the buffer", T19,
     "if (o + k + 2 > dn)", "if (o + k + 1 > dn)"),
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
    rc, _ = run([sys.executable, "-B", os.path.join(TEST, "test_stack.py"),
                 os.path.join(out, "stack_unacked.su")], copy, timeout=TIMEOUT_S)
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
