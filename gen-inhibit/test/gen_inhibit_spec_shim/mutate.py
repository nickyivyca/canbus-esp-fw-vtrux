#!/usr/bin/env python3
"""Mutants of the spec 12.4 shim harness: each must be caught BY A CHECK.

    python3 -B mutate.py --local [--only ...]       (on madhouse-debian: the run that counts)
    py -3.14 -B mutate.py [--only N,N...]          (from Windows, builds in WSL: a self-check)

A judge mutant (nonode_judge.c) is caught when judge_test prints a FAIL line. A
layer or case mutant (spec_layer.c, nonode_test.c) is caught when a case run
prints a RESULT line whose verdict differs from the unmutated build's (the
control, built and run first; since 2026-11-26, when a case can fail on the
product under test by design, as the withdrawn-frame case does before its fix).
With a control of only PASS lines that is any RESULT line that is not PASS, as
before. A line the mutant does not print is not a catch: a crash, a build
failure or a run with no RESULT line is NOT a catch, and an edit that does not
apply is an error. Each
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

def _bench_dir():
    """gen-inhibit/tools/bench in this file's worktree, found by walking up to its .git (a
    copy in scratch/ finds the same one); in a copy with no git, two folders up."""
    d = here = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            return os.path.normpath(os.path.join(here, "..", "..", "tools", "bench"))
        d = os.path.dirname(d)
    return os.path.join(d, "gen-inhibit", "tools", "bench")


sys.path.append(_bench_dir())
import gi_paths  # noqa: E402  remove_tree (AGENTS.md "Removing scratch")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from host_env import PROBE_BUILD_ARGS, describe, host_identity, missing, scratch_dir, sh, wsl_path  # noqa: E402


J, L, T = "nonode_judge.c", "spec_layer.c", "nonode_test.c"
PJ, PT = "probe_judge.c", "probe_test.c"          # 2026-11-23: spec 7 trip 7 and spec 11's probe
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
    # the withdrawn frame stuck in the controller (2026-11-26), caught by judge_test
    (J, "W1 takes a frame never removed", "const bool ok = removed_at >= 0 && removed_at < t_return;",
     "const bool ok = removed_at < t_return;"),
    (J, "W1 takes a removal after traffic returned", "const bool ok = removed_at >= 0 && removed_at < t_return;",
     "const bool ok = removed_at >= 0;"),
    (J, "W1 judges with no latch", "    if (t_latch < 0) return verdict(VOID, \"no latch", "    if (0) return verdict(VOID, \"no latch"),
    (J, "W1 judges a stimulus that did not land", "    if (!held_at_latch && (removed_at < 0 || removed_at > t_latch))\n",
     "    if (0)\n"),
    (J, "W2 takes a completion", "return verdict(on_wire_at < 0 ? PASS : NAS,", "return verdict(PASS,"),
    (J, "W3 allows one", "return verdict(wire051_after_return == 0 ? PASS : NAS,",
     "return verdict(wire051_after_return <= 1 ? PASS : NAS,"),
    (J, "W4 takes telemetry left unsent", "sent_after_return >= waiting_at_return && diag_wire_after_return",
     "diag_wire_after_return"),
    (J, "W4 takes no diag pages on the wire", "&& diag_wire_after_return >= waiting_at_return ? PASS",
     "? PASS"),
    (J, "W4 judges with nothing waiting", "    if (waiting_at_return < 1) return", "    if (0) return"),
    # the layer, caught by a case
    (L, "errors never reach the product", "    ft_set_bus_errors(L.base_errors + (uint32_t)L.n_err);", "    (void)0;"),
    (L, "errors before a frame waits", "if (L.retry_t0 < 0 && s.msgs_to_tx > 0)", "if (L.retry_t0 < 0)"),
    (L, "frames complete in the silence", "    L.tenth_us = -1;\n    twai_fake_set_acked(false);", "    L.tenth_us = -1;\n    twai_fake_set_acked(true);"),
    (L, "frames never complete after the nodes return", "    if (!L.bus_off) set_tec(0);\n    twai_fake_set_acked(true);\n}",
     "    if (!L.bus_off) set_tec(0);\n}"),
    (L, "the receive log calls every return a timeout", "L.rx[L.n_rx].ok = (r == ESP_OK);", "L.rx[L.n_rx].ok = 0;"),
    (L, "the receive log's times are 50 ms early", "L.rx[L.n_rx].t = ft_now();", "L.rx[L.n_rx].t = ft_now() - 50000;"),
    (L, "TEC never rises", "        set_tec(L.tec + 8);\n", "        set_tec(L.tec + 0);\n"),
    (L, "no error-passive alert", "        if (L.tec >= 128) raise(SL_ALERT_ERR_PASS);\n", "\n"),
    (L, "reading the alerts does not clear them", "ticks);\n    L.pending = 0;\n", "ticks);\n"),
    (L, "alerts raised whether enabled or not", "L.pending |= a & twai_fake_alerts_enabled();", "L.pending |= a;"),
    (L, "the status reads no TEC", "    twai_fake_set_error_counters(tec, 0);\n", "\n"),
    (L, "recovery calls not counted", "    L.recovery_calls++;\n", "\n"),
    (L, "errors go on after bus-off", "    if (L.silent && !L.bus_off)\n", "    if (L.silent)\n"),
    (L, "the passive period is never used", "(L.passive_us > 0 && L.tec >= 128) ? L.passive_us : L.retry_us",
     "L.retry_us", 2),
    (L, "the passive period starts at TEC 120", "L.tec >= 128) ? L.passive_us", "L.tec >= 120) ? L.passive_us", 2),
    (L, "the measured silence drops its passive period", "    L.passive_us = passive_us;\n", "\n"),
    (T, "the bench cases run a fixed period", "    else if (c->passive_us > 0) sl_silence_measured(c->retry_us, c->passive_us);\n",
     "    else if (c->passive_us > 0) sl_silence(c->retry_us);\n"),
    (L, "the 10th error misplaced", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 10;", "L.retry_us = (L.tenth_ref + L.tenth_us - now) / 5;"),
    # the layer's stuck frame (2026-11-26), caught by layer_stuck, layer_stuck_back or a withdrawn case
    (L, "an abort during an attempt removes the stuck frame", "    if (attempt) return;\n", "\n"),
    (L, "the stuck frame never reads TS set", "        if (!stuck_gap()) st |= TWAI_LL_STATUS_TS;\n",
     "        if (!stuck_gap()) st |= 0;\n"),
    (L, "the stuck frame always reads TS set", "        if (!stuck_gap()) st |= TWAI_LL_STATUS_TS;\n",
     "        if (1) st |= TWAI_LL_STATUS_TS;\n"),
    (L, "no suspend transmission in error passive", "SL_IFS_US + SL_SUSPEND_US : SL_IFS_US", "SL_IFS_US : SL_IFS_US"),
    (L, "the idle time is the whole period", "return now >= L.last_err_t + period_now() - idle_now();",
     "return now >= L.last_err_t;"),
    (L, "a held frame never completes after the nodes return", "        S.wire_at = S.back_done;\n", "        return;\n"),
    (L, "a held frame completes as the nodes return", "        S.back_done = S.back_start + period_now() - idle_now();\n",
     "        S.back_done = ft_now();\n"),
    (L, "the stuck frame is never armed", "    S.armed = true;\n", "    S.armed = false;\n"),
    (L, "a removal is not recorded", "    if (twai_fake_ll_removed() > removed0) S.removed_at = ft_now();\n", "\n"),
    # the case code, caught by a case
    (T, "the judge gets no errors", "for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++) g_errs[o.n_errs++]",
     "for (int k = 0; k < 0; k++) g_errs[o.n_errs++]", 3),
    (T, "the stuck answer cannot be aborted", "    ft_stall_id(0x051, 1);                      /* the answer",
     "    (void)0;                                    /* the answer"),
    (T, "the withdrawn cases arm no stuck frame", "    sl_retry_stuck(0x051);\n", "\n"),
    (T, "W2 ignores the layer's wire record", "    int64_t on_wire = sl_stuck_on_wire_at();\n", "    int64_t on_wire = -1;\n"),
    (T, "W4 reads no telemetry waiting", 'const long tele_wait = cls_num(pr, "telemetry", "depth") + cls_num(pr, "telemetry", "held");',
     "const long tele_wait = 0;"),
    (T, "the checks include returns before the last 0x051", "        if (sl_rx(i)->t > t_last) g_checks[o.n_checks++]",
     "        if (sl_rx(i)->t > t_last - 1000000) g_checks[o.n_checks++]", 3),
    # probe_judge.c (2026-11-23, spec 7 trip 7 and spec 11), caught by probe_judge_test
    (PJ, "a late frame may be booked with frames waiting", "don == 0 && dunv == 0,", "don == 0,"),
    (PJ, "a frame with frames waiting may be booked on time", "don == 0 && dunv == 1,", "dunv == 1,"),
    (PJ, "a frame with nothing waiting may be booked with frames waiting", "don == 1 && dunv == 0,", "don == 1,"),
    (PJ, "the max backlog may exceed the frames waiting", "o->p1.mx == o->behind,", "o->p1.mx >= o->behind,"),
    (PJ, "a max backlog already at the case's backlog passes", "if (o->kind == PJ_WAITING && o->p0.mx >= o->behind)",
     "if (0)"),
    (PJ, "the max backlog is not checked outside the waiting case", "o->p1.mx == o->p0.mx,", "1,"),
    (PJ, "skips are judged only at the settled read", "o->p1.skip == o->p0.skip && o->p2.skip == o->p0.skip,",
     "o->p2.skip == o->p0.skip,", 2),
    (PJ, "an inhibit frame found late need not be a skip", "o->p1.skip == o->p0.skip + 1 && o->p2.skip == o->p1.skip",
     "o->p2.skip == o->p1.skip"),
    (PJ, "the next frame need not be booked on time", "o->p2.on - o->p1.on == 1 && o->p2.unv == o->p1.unv,",
     "o->p2.unv == o->p1.unv,"),
    (PJ, "the next frame may be booked with frames waiting", "o->p2.on - o->p1.on == 1 && o->p2.unv == o->p1.unv,",
     "o->p2.on - o->p1.on == 1,"),
    (PJ, "a withdrawn probe passes", "ds == want && dw == 0,", "ds == want,"),
    (PJ, "a probe on the wire need not be credited sent", "o->ours_wire == want && ds == want && dw == 0",
     "o->ours_wire == want && dw == 0"),
    (PJ, "the probes on the wire are not counted", "o->ours_wire == want && ds == want && dw == 0",
     "ds == want && dw == 0"),
    (PJ, "the inhibit frames on the wire are not counted", 'n = put(out, n, "sent", o->ours_wire == want, "inhibit',
     'n = put(out, n, "sent", 1, "inhibit'),
    (PJ, "one frame of ours expected where the next command came", "return PJ_IS_LATE(k) ? 2 : 1;", "return 1;"),
    (PJ, "a frame seen complete only after the next 0x051 was dequeued is not late",
     "const bool late = PJ_IS_LATE(o->kind);", "const bool late = o->kind == PJ_LATE || o->kind == PJ_BETWEEN;"),
    (PJ, "a stimulus that did not land is judged", "    if (!o->pre_ok)\n", "    if (0)\n"),
    # probe_test.c's case code (2026-11-23), caught by a probe_test RESULT line
    (PT, "the late case puts no 0x051 behind the filler", "        ft_deliver(0x051, next, 6);       /* N+1, behind",
     "        (void)next;                       /* N+1, behind"),
    (PT, "the waiting case puts an 0x051 behind the filler", "        ft_deliver(0x411, SOC, 8);\n",
     "        ft_deliver(0x051, VCM, 6);\n"),
    (PT, "the between case never arms the arrival", "        ft_deliver_at_count(0x051, next, 6);\n",
     "        (void)next;\n"),
    (PT, "our frame is the inhibit frame in RESPOND too", "c->respond ? 0x7F0 : 0x051", "0x051"),
    (PT, "the decision is read after the last filler", '    if (!read_page(&o.p1, "p1"))',
     '    ft_deliver(0x617, FLT, 8);\n    ft_run(500);\n    drain(10, 500);\n    if (!read_page(&o.p1, "p1"))'),
]
# A 5th element is how many places the edit must match (each case that gathers
# the same observation); without it, exactly one.
# Not a mutant (port step 1b, 2026-10-31): the layer's read of the alerts waiting for
# the driver while an alert of its own is pending ("(&a, mine ? 0 : ticks)" made
# "(&a, ticks)"). It survived every case on WSL (2026-10-31): no case's outcome turns
# on a read that waits while only an alert of the layer's is pending (each comes in
# the same step as the fake's own BUS_ERROR). Kept in the layer as the driver's
# semantics, with no case that turns on it.


def verdicts(out):
    """Each RESULT line's verdict, keyed by (case, requirement, its count among that case's lines
    for that requirement), so a requirement judged twice in one case keeps both."""
    got, seen = {}, {}
    for l in out.splitlines():
        p = l.split()
        if len(p) >= 4 and p[0] == "RESULT":
            n = seen.get((p[1], p[2]), 0)
            seen[(p[1], p[2])] = n + 1
            got[(p[1], p[2], n)] = p[3]
    return got


def differs(got, ctl):
    """The mutant's lines whose verdict differs from the control's: a line the control does not
    have counts as a control PASS; a control line the mutant does not print is not a catch (a
    crash is not a catch)."""
    return ["%s %s: %s, the control %s" % (k[0], k[1], v, ctl.get(k, "PASS (no line)"))
            for k, v in got.items() if v != ctl.get(k, "PASS")]


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
        gi_paths.remove_tree(base)
        return 1
    caught = errors = 0
    picked = [(n, m) for n, m in enumerate(MUTANTS, 1) if only is None or n in only]
    controls = {}

    def control(kind):
        """The unmutated build's verdicts for one binary, built and run once (2026-11-26): a
        case may fail on the product under test by design (a can-fail before its fix), so a
        layer or case mutant is caught only by a verdict that differs from the control's."""
        if kind not in controls:
            d = os.path.join(base, "control-%s" % kind)
            os.makedirs(d)
            b = os.path.join(d, "spec_shim_control")
            b = b if local else wsl_path(b)
            extra, env = (PROBE_BUILD_ARGS, 'NN_TEST="probe_test.c" ') if kind == "probe" else ("", "")
            rc, out, err = sh('cd "%s" && %sbash build.sh %s %s >/dev/null 2>&1 && for c in $(%s); do timeout 300 %s $c; '
                              'done; rm -f %s' % (here, env, b, extra, b, b, b), local)
            controls[kind] = verdicts(out)
            bad = sorted(k for k, v in controls[kind].items() if v != "PASS")
            print("control %s: %d RESULT lines, %d not PASS%s" % (kind, len(controls[kind]), len(bad),
                                                                 "".join("\n         %s %s: %s" % (k[0], k[1], controls[kind][k])
                                                                         for k in bad)))
            sys.stdout.flush()
        return controls[kind]

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
        if f in (J, PJ):
            test = {J: "judge_test.c", PJ: "probe_judge_test.c"}[f]
            cmd = ('cd "%s" && gcc -std=c11 -I. -o %s %s "%s" && %s; rm -f %s'
                   % (here, bin_, test, m, bin_, bin_))
            rc, out, err = sh(cmd, local)
            hits = [l for l in out.splitlines() if l.startswith("FAIL ")]
            ran = "ok " in out or hits
        else:
            ctl = control("probe" if f == PT else "nonode")
            if not ctl:
                print("ERROR    the unmutated build gave no RESULT line: no mutant is judged")
                gi_paths.remove_tree(base)
                return 1
            env = {L: "SPEC_LAYER", T: "NN_TEST", PT: "NN_TEST"}[f]
            extra = PROBE_BUILD_ARGS if f == PT else ""
            cmd = ('cd "%s" && %s="%s" bash build.sh %s %s >/dev/null 2>&1 && for c in $(%s); do timeout 300 %s $c; done; rm -f %s'
                   % (here, env, m, bin_, extra, bin_, bin_, bin_))
            rc, out, err = sh(cmd, local)
            res = [l for l in out.splitlines() if l.startswith("RESULT ")]
            hits = differs(verdicts(out), ctl)
            ran = bool(res)
        dead = bool(hits)
        caught += dead
        print("%s m%d %s  (%s%s)" % ("CAUGHT  " if dead else "SURVIVED", n, name,
                                    "%d failing line(s)" % len(hits) if ran else "NO RESULT: build or crash",
                                    ("; " + hits[0][:140]) if hits else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d caught by a check, %d errors" % (caught, len(picked), errors))
    return 0 if caught == len(picked) and not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
