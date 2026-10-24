#!/usr/bin/env python3
"""Mutants of the spec 12.4 shim harness: each must be caught BY A CHECK.

    python3 -B mutate.py --local [--only ...]       (on madhouse-debian: the run that counts)
    py -3.14 -B mutate.py [--only N,N...]          (from Windows, builds in WSL: a self-check)

A judge mutant (nonode_judge.c) is caught when judge_test prints a FAIL line. A
layer or case mutant (spec_layer.c, nonode_test.c) is caught when a case run
prints a RESULT line that is not PASS. A crash, a build failure or a run with no
RESULT line is NOT a catch, and an edit that does not apply is an error. Each
mutant is a copy in this run's own folder in the worktree's scratch/, with its
binary beside it, so two runs at once share nothing; nothing here is edited.
The output's second line is the host and compiler the build shell reported
(host_env.py); a run where either is missing is an error. Only a run built on
madhouse-debian with its gcc 14.2 counts for VERIFICATION.md (user, 2026-10-24,
through the reviewer). Gen-inhibit tester, 2026-10-20.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from host_env import describe, host_identity, missing, scratch_dir, sh, wsl_path  # noqa: E402


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
    (J, "A1 ignores the answer on the wire", "!answer_on_wire && wire051_after_return == 0 ? PASS : NAS",
     "wire051_after_return == 0 ? PASS : NAS"),
    (J, "A1 ignores an 0x051 after the return", "!answer_on_wire && wire051_after_return == 0 ? PASS : NAS",
     "!answer_on_wire ? PASS : NAS"),
    (J, "A2 takes a skip", "standdown_delta == 1 && skipped_delta == 0 ? PASS : NAS", "standdown_delta == 1 ? PASS : NAS"),
    (J, "A2 takes no stand-down", "standdown_delta == 1 && skipped_delta == 0 ? PASS : NAS", "skipped_delta == 0 ? PASS : NAS"),
    (J, "A3 takes an unbalanced read", "return verdict(bad_reads == 0 ? PASS : NAS, \"queued + carried_in",
     "return verdict(PASS, \"queued + carried_in"),
    (J, "A4 takes an equality that is off", "return verdict(bad_reads == 0 ? PASS : NAS, \"withdrawn + dropped",
     "return verdict(PASS, \"withdrawn + dropped"),
    (J, "A5 takes diag pages held behind the answer", "diag_handed_after >= 1 && first_done_on_return ? PASS : NAS",
     "first_done_on_return ? PASS : NAS"),
    (J, "A5 takes a later diag page that never completed", "diag_handed_after >= 1 && first_done_on_return ? PASS : NAS",
     "diag_handed_after >= 1 ? PASS : NAS"),
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
    (L, "the passive period is never used", "(L.passive_us > 0 && L.tec >= 128) ? L.passive_us : L.retry_us",
     "L.retry_us"),
    (L, "the passive period starts at TEC 120", "L.tec >= 128) ? L.passive_us", "L.tec >= 120) ? L.passive_us"),
    (L, "the measured silence drops its passive period", "    L.passive_us = passive_us;\n", "\n"),
    (T, "the bench cases run a fixed period", "    else if (c->passive_us > 0) sl_silence_measured(c->retry_us, c->passive_us);\n",
     "    else if (c->passive_us > 0) sl_silence(c->retry_us);\n"),
    (L, "the 10th error misplaced", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 10;", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 5;"),
    # the case code, caught by a case
    (T, "the judge gets no errors", "for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++) g_errs[o.n_errs++]",
     "for (int k = 0; k < 0; k++) g_errs[o.n_errs++]", 2),
    (T, "the stuck answer cannot be aborted", "    ft_stall_id(0x051, 1);                      /* the answer",
     "    (void)0;                                    /* the answer"),
    (T, "the checks include returns before the last 0x051", "        if (sl_rx(i)->t > t_last) g_checks[o.n_checks++]",
     "        if (sl_rx(i)->t > t_last - 1000000) g_checks[o.n_checks++]", 2),
]
# A 5th element is how many places the edit must match (each case that gathers
# the same observation); without it, exactly one.


def main(argv):
    local = "--local" in argv
    only = None
    if "--only" in argv:
        only = {int(x) for x in argv[argv.index("--only") + 1].split(",")}
    here = HERE if local else wsl_path(HERE)
    base = scratch_dir("mutants-spec_shim")
    print("work folder: %s" % base)
    ident = host_identity(local)
    print(describe(ident))
    if missing(ident):
        print("ERROR    not recorded: %s" % ", ".join(missing(ident)))
        shutil.rmtree(base, ignore_errors=True)
        return 1
    caught = errors = 0
    picked = [(n, m) for n, m in enumerate(MUTANTS, 1) if only is None or n in only]
    for n, mut in picked:
        f, name, old, new = mut[:4]
        want = mut[4] if len(mut) > 4 else 1
        src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
        if src.count(old) != want:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        mp = os.path.join(d, f)
        open(mp, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        m = mp if local else wsl_path(mp)
        bin_ = os.path.join(d, "spec_shim_m%d" % n)          # this mutant's folder, not a shared /tmp name
        bin_ = bin_ if local else wsl_path(bin_)
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
