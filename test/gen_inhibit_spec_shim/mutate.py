#!/usr/bin/env python3
"""Mutants of the spec 12.4 shim harness: each must be caught BY A CHECK.

    py -3.14 -B mutate.py [--only N,N...]          (from Windows; builds in WSL)
    python3 -B mutate.py --local [--only ...]       (on a Linux host with gcc)

A judge mutant (nonode_judge.c) is caught when judge_test prints a FAIL line. A
layer or case mutant (spec_layer.c, nonode_test.c) is caught when a case run
prints a RESULT line that is not PASS. A crash, a build failure or a run with no
RESULT line is NOT a catch, and an edit that does not apply is an error. Each
mutant is a copy in the machine's temp folder; nothing here is edited.
Gen-inhibit tester, 2026-10-20.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

J, L, T = "nonode_judge.c", "spec_layer.c", "nonode_test.c"
MUTANTS = [
    # the judge, caught by judge_test
    (J, "a tie is trip 6", "e.trip = (e.c_six >= 0 && e.c_six < e.c_stale) ? 6 : 2;",
     "e.trip = (e.c_six >= 0 && e.c_six <= e.c_stale) ? 6 : 2;"),
    (J, "errors older than 10 s count", "o->errs[k] >= o->checks[i] - NN_TRIP6_WIN", "o->errs[k] >= o->checks[i] - 100 * NN_TRIP6_WIN"),
    (J, "the threshold is 11", "if (n >= NN_TRIP6_N) i_six = i;", "if (n > NN_TRIP6_N) i_six = i;"),
    (J, "an error at the check's instant is not counted", "if (o->errs[k] <= o->checks[i] &&", "if (o->errs[k] < o->checks[i] &&"),
    (J, "the 0.5 s edge is not guarded", "if (age - NN_FRESH_US > -NN_EDGE_US && age - NN_FRESH_US < NN_EDGE_US) edge = true;", ";"),
    (J, "check points before the last 0x051 are taken", "        if (o->checks[i] <= o->t_last)\n",
     "        if (0)\n"),
    (J, "frames in the silence are ignored", "|| edge || o->frames_in_silence)", "|| edge)"),
    (J, "the region is not checked", "        if (region != o->region)\n", "        if (0)\n"),
    (J, "R2 takes any check point before the latch", "o->checks[i] >= o->t_unlatched - NN_SLACK_US) found++;",
     "o->checks[i] >= o->t_unlatched - 100000) found++;"),
    (J, "R3 ignores the reason", "bool ok = when && (e->trip == 6 ? got6 : got2);", "bool ok = when;"),
    (J, "R3 ignores the time", "bool ok = when && (e->trip == 6 ? got6 : got2);", "bool ok = (e->trip == 6 ? got6 : got2);"),
    (J, "R3 has no 0.7 s limit", "&& e->c_exp - o->t_last <= NN_TRIP2_MAX;", ";"),
    (J, "R1 allows one", "return verdict(late051 == 0 ? PASS : NAS,", "return verdict(late051 <= 1 ? PASS : NAS,"),
    (J, "the layer check takes any err_n", "n_errs > 0 && err_n_max >= want ? PASS : VOID", "n_errs > 0 && err_n_max >= 0 ? PASS : VOID"),
    (J, "the layer may err before a frame waits", "first_wait >= 0 && first_err >= first_wait ? PASS : VOID", "first_wait >= 0 ? PASS : VOID"),
    (J, "R4 takes a stopped worker", "returns >= 8 && page_gap_max <= 1000 ? PASS : NAS", "page_gap_max <= 1000 ? PASS : NAS"),
    (J, "R5 takes no diag pages", "diag_more >= 2 && sent051_more == 0", "sent051_more == 0"),
    (J, "R5 takes an inhibit frame while latched", "diag_more >= 2 && sent051_more == 0", "diag_more >= 2"),
    (J, "R6 takes a latch", "return verdict(!latched ? PASS : NAS, \"after key off", "return verdict(PASS, \"after key off"),
    (J, "R7 takes a recovery call", "recovery_calls == 0 && latched && dropped > 0 && installs == 0",
     "latched && dropped > 0 && installs == 0"),
    (J, "R7 takes a reinstall with no re-arm", "recovery_calls == 0 && latched && dropped > 0 && installs == 0",
     "recovery_calls == 0 && latched && dropped > 0"),
    (J, "R7 takes a latch after the re-arm", "frames_received > 0 && !latched ? PASS : NAS", "frames_received > 0 ? PASS : NAS"),
    # the layer, caught by a case
    (L, "errors never reach the product", "    ft_set_bus_errors(L.base_errors + (uint32_t)L.n_err);\n", "\n"),
    (L, "errors before a frame waits", "if (L.retry_t0 < 0 && s.msgs_to_tx > 0)", "if (L.retry_t0 < 0)"),
    (L, "frames complete in the silence", "    L.tenth_us = -1;\n    ft_set_air_time(AIR_NEVER);", "    L.tenth_us = -1;\n    ft_set_air_time(AIR_NORMAL);"),
    (L, "frames never complete after the nodes return", "    L.retry_t0 = -1;\n    ft_set_air_time(AIR_NORMAL);\n}", "    L.retry_t0 = -1;\n}"),
    (L, "the receive log calls every return a timeout", "L.rx[L.n_rx].ok = (r == ESP_OK);", "L.rx[L.n_rx].ok = 0;"),
    (L, "the receive log's times are 50 ms early", "L.rx[L.n_rx].t = ft_now();", "L.rx[L.n_rx].t = ft_now() - 50000;"),
    (L, "TEC never rises", "        L.tec += 8;\n", "        L.tec += 0;\n"),
    (L, "no bus-error alert", "    L.pending |= SL_ALERT_BUS_ERROR;\n", "\n"),
    (L, "reading the alerts does not clear them", "    L.pending &= ~mine;\n", "\n"),
    (L, "recovery calls not counted", "    L.recovery_calls++;\n", "\n"),
    (L, "errors go on after bus-off", "    if (L.silent && !L.bus_off)\n", "    if (L.silent)\n"),
    (L, "the 10th error misplaced", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 10;", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 5;"),
    # the case code, caught by a case
    (T, "the judge gets no errors", "for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++) g_errs[o.n_errs++]",
     "for (int k = 0; k < 0; k++) g_errs[o.n_errs++]"),
    (T, "the checks include returns before the last 0x051", "        if (sl_rx(i)->t > t_last) g_checks[o.n_checks++]",
     "        if (sl_rx(i)->t > t_last - 1000000) g_checks[o.n_checks++]"),
]


def wsl_path(p):
    p = os.path.abspath(p).replace("\\", "/")
    return "/mnt/" + p[0].lower() + p[2:] if re.match(r"^[A-Za-z]:/", p) else p


def sh(cmd, local, timeout=900):
    argv = ["bash", "-c", cmd] if local else ["wsl", "-e", "bash", "-c", cmd]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def main(argv):
    local = "--local" in argv
    only = None
    if "--only" in argv:
        only = {int(x) for x in argv[argv.index("--only") + 1].split(",")}
    here = HERE if local else wsl_path(HERE)
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "spec_shim")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    caught = errors = 0
    picked = [(n, m) for n, m in enumerate(MUTANTS, 1) if only is None or n in only]
    for n, (f, name, old, new) in picked:
        src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        mp = os.path.join(d, f)
        open(mp, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        m = mp if local else wsl_path(mp)
        bin_ = "/tmp/spec_shim_m%d" % n
        if f == J:
            cmd = ('cd "%s" && gcc -std=c11 -I. -o %s judge_test.c "%s" && %s; rm -f %s'
                   % (here, bin_, m, bin_, bin_))
            rc, out, err = sh(cmd, local)
            hits = [l for l in out.splitlines() if l.startswith("FAIL ")]
            ran = "ok " in out or hits
        else:
            env = {L: "SPEC_LAYER", T: "NN_TEST"}[f]
            cmd = ('cd "%s" && %s="%s" bash build.sh %s >/dev/null 2>&1 && for c in $(%s); do timeout 300 %s $c; done; rm -f %s'
                   % (here, env, m, bin_, bin_, bin_, bin_))
            rc, out, err = sh(cmd, local)
            res = [l for l in out.splitlines() if l.startswith("RESULT ")]
            hits = [l for l in res if l.split()[3] != "PASS"]
            ran = bool(res)
        dead = bool(hits)
        caught += dead
        print("%s m%d %s  (%s%s)" % ("CAUGHT  " if dead else "SURVIVED", n, name,
                                    "%d failing line(s)" % len(hits) if ran else "NO RESULT: build or crash",
                                    ("; " + hits[0][:140]) if hits else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d caught by a check, %d errors" % (caught, len(picked), errors))
    return 0 if caught == len(picked) and not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
