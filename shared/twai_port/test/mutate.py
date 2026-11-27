#!/usr/bin/env python3
"""Break the fake one behaviour at a time; test_twai_fake must fail on each.

A check that cannot fail is not a check (AGENTS.md; the tester skill). Each
mutant below undoes one behaviour README.md says the fake has, in a copy of
twai_fake.c under the worktree's scratch/, and builds test_twai_fake.c
against the copy. The run passes only if the unmutated build passes and
every mutant build fails.

    python3 shared/twai_port/test/mutate.py [--out DIR] [--cc gcc]

DIR must not exist: the run makes it and removes it when it ends, pass or fail
(AGENTS.md, "Removing scratch"). A folder it cannot remove is printed with the
reason. By default DIR is this run's own folder,
scratch/twai_fake-mutants/<time>_<pid>/ at the worktree's root, as unit/mutate.py
does, so a folder left by a killed run (which no finally covers) does not stop
the next run.
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
RUNS = os.path.join(ROOT, "scratch", "twai_fake-mutants")


def long_path(p):
    """p made absolute, and on Windows in the \\\\?\\ form, which has no 260-character limit."""
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return p


def remove_tree(path):
    """Remove path and everything in it. A file or folder that will not go is made
    writable and tried again (Git for Windows' objects are read-only, which
    shutil.rmtree alone cannot remove on Windows). True if path is gone; otherwise
    the path and the reason are printed and False is returned."""
    failed = []

    def again(func, p, exc):
        try:
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(p) else 0))
            func(p)
        except OSError as e:
            failed.append((p, e))

    lp = long_path(path)
    if not os.path.lexists(lp):
        return True
    if sys.version_info >= (3, 12):
        shutil.rmtree(lp, onexc=again)
    else:
        shutil.rmtree(lp, onerror=lambda func, p, ei: again(func, p, ei[1]))
    if os.path.lexists(lp):
        why = failed[0] if failed else (path, "still there after removal")
        print("could not remove %s: %s (%s)" % (path, why[1], why[0]), file=sys.stderr)
        return False
    return True

# (name, text in twai_fake.c, its replacement). Each text occurs once.
MUTANTS = [
    ("no driver reads INVALID_STATE",
     "g_wrong_state++;\n    return ESP_ERR_INVALID_ARG;",
     "g_wrong_state++;\n    return ESP_ERR_INVALID_STATE;"),
    ("alerts latched when disabled",
     "if (g_alerts_enabled & a)\n    {",
     "if (1)\n    {"),
    ("reconfigure hands back the old mask",
     "*current_alerts = g_alerts_triggered;",
     "*current_alerts = g_alerts_enabled;"),
    ("TX queue holds one more",
     "if (g_txq.n >= g_txq.cap)\n",
     "if (g_txq.n > g_txq.cap)\n"),
    ("listen-only transmits",
     "if (g_mode == TWAI_MODE_LISTEN_ONLY)\n    {\n        g_lo_attempts++;",
     "if (0)\n    {\n        g_lo_attempts++;"),
    ("stop clears the RX queue",
     "discard_tx(&g_disc_stop, true, cut);",
     "discard_tx(&g_disc_stop, true, cut);\n    discard_rx();"),
    ("bus-off loads the next frame (v5.4.1)",
     "if (g_state == TWAI_STATE_BUS_OFF)\n    {\n        raise_alert(TWAI_ALERT_TX_IDLE);\n    }\n    else\n    {\n        load_next(done);\n    }",
     "load_next(done);"),
    ("bus-off clears the RX queue",
     "/* twai_hal_iram.c, twai_hal_get_events */\n    raise_alert(TWAI_ALERT_BUS_OFF);",
     "/* twai_hal_iram.c, twai_hal_get_events */\n    g_rxq.n = 0;\n    raise_alert(TWAI_ALERT_BUS_OFF);"),
    ("bus-off re-enters while bus-off",
     "if (!g_installed || g_state != TWAI_STATE_RUNNING || g_rm_ll) { return; }\n    g_state = TWAI_STATE_BUS_OFF;",
     "if (!g_installed || g_state == TWAI_STATE_STOPPED || g_rm_ll) { return; }\n    g_state = TWAI_STATE_BUS_OFF;"),
    ("recovery ends running",
     "g_state = TWAI_STATE_STOPPED;           /* twai.c:242 */",
     "g_state = TWAI_STATE_RUNNING;"),
    ("install keeps the counts",
     "g_rx_missed = g_rx_overrun = g_tx_failed = g_bus_errors = 0;\n    g_buf_full = false;\n    fifo_size",
     "g_buf_full = false;\n    fifo_size"),
    ("standard frame one bit short",
     "(extd ? 67 : 47)",
     "(extd ? 67 : 46)"),
    ("received while stopped",
     "if (!g_installed || g_state != TWAI_STATE_RUNNING || g_rm_ll)\n    {\n        g_rx_not_rx++;",
     "if (!g_installed || g_rm_ll)\n    {\n        g_rx_not_rx++;"),
    ("filter ignored",
     "if (!g_fcfg.single_filter) { return true; }",
     "return true;"),
    ("abort puts the frame on the wire",
     "g_buf_full = false;\n    g_abort_pend = false;\n    g_ll_removed++;",
     "flog_push(&g_wire, &g_buf.f);\n    g_buf_full = false;\n    g_abort_pend = false;\n    g_ll_removed++;"),
    ("abort reports no TX_SUCCESS",
     "g_ll_removed++;\n    raise_alert(TWAI_ALERT_TX_SUCCESS);",
     "g_ll_removed++;"),
    ("the S3 counted as measured",
     "static bool ll_usable(twai_dev_t *hw)\n{\n    g_ll_calls++;\n    if (g_chip != TWAI_FAKE_CHIP_C3) { g_ll_unmeasured++; }",
     "static bool ll_usable(twai_dev_t *hw)\n{\n    g_ll_calls++;"),
    ("unacked normal frame completes",
     "if (!g_buf.f.ss) { return false; }",
     "if (!g_buf.f.ss) { return true; }"),
    ("start keeps the RX queue",
     "/* twai.c:605-611: both queues reset. */\n    discard_rx();",
     "/* twai.c:605-611: both queues reset. */"),
    ("uninstall from running",
     "if (g_state != TWAI_STATE_STOPPED && g_state != TWAI_STATE_BUS_OFF)\n    {\n        return wrong_state();",
     "if (g_state == TWAI_STATE_RECOVERING)\n    {\n        return wrong_state();"),
    ("stop never cuts the head",
     "cut = g_timing == TWAI_FAKE_TX_TIMED",
     "cut = false && g_timing == TWAI_FAKE_TX_TIMED"),
    ("no self-reception",
     "if (q.f.self)\n        {",
     "if (0)\n        {"),
    ("recovery keeps the TX queue",
     "discard_tx(&g_disc_recovery, false, false);",
     ";"),
    ("full RX queue not counted",
     "g_rx_missed++;\n        g_tot_missed++;",
     "g_tot_missed++;"),
    ("transmit queue ignores the wait",
     "if (g_txq.cap == 0) { return ESP_FAIL; }",
     "if (g_txq.cap == 0) { return ESP_ERR_TIMEOUT; }"),
    ("teardown in a receive not counted",
     "if (g_in_receive) { g_unsafe++; }\n    discard_tx(&g_disc_uninstall",
     "discard_tx(&g_disc_uninstall"),
    ("brp parity not checked",
     "|| (brp & 1u)",
     ""),
    ("listen-only error counters reported",
     "if (g_mode != TWAI_MODE_LISTEN_ONLY)\n    {\n        s->tx_error_counter",
     "if (1)\n    {\n        s->tx_error_counter"),
    # The peripheral and reset mode (twai_port T12).
    ("power-on with the clock on",
     "g_clk_on = g_rst_held = false;",
     "g_clk_on = true;\n    g_rst_held = false;"),
    ("install leaves the clock off",
     "    g_clk_on = true;\n    g_rst_held = false;",
     "    g_rst_held = false;"),
    ("uninstall keeps the clock on",
     "g_clk_on = false;                       /* twai.c:568-571 */",
     ";"),
    ("start stays in reset mode",
     "g_rm_ll = false;                        /* twai_hal.c:91, out of reset mode */",
     ";"),
    ("stopped not in reset mode",
     "return !(g_installed && (g_state == TWAI_STATE_RUNNING || g_state == TWAI_STATE_RECOVERING));",
     "return !g_installed;"),
    ("recovering in reset mode",
     "(g_state == TWAI_STATE_RUNNING || g_state == TWAI_STATE_RECOVERING));",
     "(g_state == TWAI_STATE_RUNNING));"),
    ("registers usable with the clock off or reset held",
     "if (!g_clk_on || g_rst_held)",
     "if (0)"),
    ("registers usable with the reset held",
     "if (!g_clk_on || g_rst_held)",
     "if (!g_clk_on)"),
    ("reset mode does not cut the frame on the wire",
     "if (buf_transmitting()) { g_cut_reset++; }",
     ""),
    ("frames sent in outside reset mode",
     "g_state == TWAI_STATE_RUNNING && !g_rm_ll)\n    {",
     "g_state == TWAI_STATE_RUNNING)\n    {"),
    ("frames received in outside reset mode",
     "|| g_rm_ll)\n    {\n        g_rx_not_rx++;",
     ")\n    {\n        g_rx_not_rx++;"),
    ("bus-off in outside reset mode",
     "g_state != TWAI_STATE_RUNNING || g_rm_ll) { return; }\n    g_state = TWAI_STATE_BUS_OFF;",
     "g_state != TWAI_STATE_RUNNING) { return; }\n    g_state = TWAI_STATE_BUS_OFF;"),
    ("reset mode entered twice",
     "if (in_reset_mode()) { return; }\n    if (buf_transmitting())",
     "if (buf_transmitting())"),
    ("abort works in reset mode",
     "if (!g_buf_full || in_reset_mode()) { return; }",
     "if (!g_buf_full) { return; }"),
    ("recovery completes in outside reset mode",
     "g_auto_recovery && !g_rm_ll && t",
     "g_auto_recovery && t"),
    ("RS never set",
     "if (receiving()) { st |= TWAI_LL_STATUS_RS; }",
     ""),
    ("reset mode does not cut the receive",
     "g_rx_busy_until = 0;\n    g_ll_rm_entries++;",
     "g_ll_rm_entries++;"),
    ("BS never set",
     "st |= TWAI_LL_STATUS_BS;",
     ";"),
    ("ES only above 96",
     "if (g_tec >= 96 || g_rec >= 96)",
     "if (g_tec > 96 || g_rec > 96)"),
    ("TEC register reads 0 in listen-only",
     "return ll_usable(hw) ? g_tec : 0u;",
     "return ll_usable(hw) && g_mode != TWAI_MODE_LISTEN_ONLY ? g_tec : 0u;"),
    ("clock bit never set",
     "return g_clk_on ? (uint32_t)SYSTEM_TWAI_CLK_EN : 0u;",
     "return 0u;"),
    ("reset bit never set",
     "return g_rst_held ? (uint32_t)SYSTEM_TWAI_RST : 0u;",
     "return 0u;"),
    ("unknown register reads not counted",
     "g_reg_unknown++;\n    return 0;",
     "return 0;"),
    ("REG_READ on the S3 counted as measured",
     "uint32_t twai_fake_reg_read(uint32_t addr)\n{\n    g_ll_calls++;\n    if (g_chip != TWAI_FAKE_CHIP_C3) { g_ll_unmeasured++; }",
     "uint32_t twai_fake_reg_read(uint32_t addr)\n{\n    g_ll_calls++;"),
    ("delay does not move the clock",
     "        g_own_now += us;\n    }\n    else if (g_wait",
     "    }\n    else if (g_wait"),
    ("delay ignores the wait hook",
     "while (twai_fake_now() < deadline && g_wait(deadline, g_wait_ctx)) { }",
     "(void)deadline;"),
    # The register-level switch (twai_port T14-T18).
    ("install leaves the mode register",
     "    g_hw_mode = TWAI_MODE_LISTEN_ONLY;\n    g_tec = g_rec = 0;",
     "    g_tec = g_rec = 0;"),
    ("install leaves the error counters",
     "    g_tec = g_rec = 0;\n    g_phantom = false;\n    g_install_n++;",
     "    g_phantom = false;\n    g_install_n++;"),
    ("start leaves the mode register",
     "    g_hw_mode = g_mode;\n",
     ""),
    ("start leaves TEC",
     "    g_tec = 0;\n    g_rec = g_mode",
     "    g_rec = g_mode"),
    ("listen-only start writes REC 0",
     "g_rec = g_mode == TWAI_MODE_LISTEN_ONLY ? 128u : 0u;",
     "g_rec = 0u;"),
    ("stop leaves the mode register",
     "    g_hw_mode = TWAI_MODE_LISTEN_ONLY;      /* twai_hal.c:98, REC frozen */\n",
     ""),
    ("bus-off leaves the mode register",
     "    g_hw_mode = TWAI_MODE_LISTEN_ONLY;      /* twai_hal_iram.c, twai_hal_get_events */\n",
     ""),
    ("writes allowed outside reset mode",
     "    if (!in_reset_mode())\n    {\n        g_ll_outside_reset++;",
     "    if (0)\n    {\n        g_ll_outside_reset++;"),
    ("set_mode ignored",
     "if (ll_writable(hw)) { g_hw_mode = mode; }",
     "(void)ll_writable(hw); (void)mode;"),
    ("set_rec ignored",
     "g_rec = rec & 0xFFu;",
     "(void)rec;"),
    ("REC writes not logged",
     "if (g_n_rec_log < RECLOGMAX) { g_rec_log[g_n_rec_log++] = g_rec; }",
     ""),
    ("set_tec ignored",
     "if (ll_writable(hw)) { g_tec = tec & 0xFFu; }",
     "(void)ll_writable(hw); (void)tec;"),
    ("REC reads not counted",
     "    g_ll_rec_reads++;\n",
     ""),
    ("exit does not leave reset mode",
     "    g_rm_ll = false;\n    if (g_buf_full && g_buf_tr)",
     "    if (g_buf_full && g_buf_tr)"),
    ("exit from the driver's reset mode not counted",
     "if (in_reset_mode()) { g_ll_exit_misused++; }",
     ""),
    ("listen-only sends",
     " && g_hw_mode != TWAI_MODE_LISTEN_ONLY;",
     ";"),
    ("KEEP leaves the transmit request",
     "        g_buf_tr = false;\n        g_reset_kept++;",
     "        g_reset_kept++;"),
    ("RESUME without a fresh attempt",
     "        g_buf_start = twai_fake_now();      /* RESUME: a fresh attempt */\n",
     ""),
    ("RELEASE keeps the buffer",
     "        g_buf_full = false;\n        g_phantom = true;",
     "        g_phantom = true;"),
    ("RELEASE leaves no stuck flag",
     "        g_phantom = true;\n",
     ""),
    ("the driver sends past its stuck flag",
     "if (!g_buf_full && !g_phantom)",
     "if (!g_buf_full)"),
    ("msgs_to_tx ignores the stuck flag",
     "(g_buf_full || g_phantom ? 1 : 0)",
     "(g_buf_full ? 1 : 0)"),
    ("a stop leaves the stuck flag",
     "    g_phantom = false;\n    g_abort_pend = false;\n    if (g_buf_full)",
     "    g_abort_pend = false;\n    if (g_buf_full)"),
    ("abort on a frame on the wire forgotten",
     "        g_abort_pend = true;\n",
     ""),
    ("aborted attempt retransmitted",
     "        if (g_abort_pend)\n        {\n            end_aborted_attempt(end);",
     "        if (0)\n        {\n            end_aborted_attempt(end);"),
    ("aborted attempt ends at the abort",
     "if (g_timing == TWAI_FAKE_TX_INSTANT) { tx_advance(twai_fake_now(), true); }\n        return;",
     "tx_advance(twai_fake_now(), true);\n        return;"),
    ("aborted attempt reported failed by default",
     "if (g_abort_report == TWAI_FAKE_ABORT_REPORTS_FAILED)",
     "if (g_abort_report != TWAI_FAKE_ABORT_REPORTS_FAILED)"),
    ("attempt ends one microsecond late",
     "while (unacked_retrying() && g_buf_start + (g_attempts + 1) * air <= t)",
     "while (unacked_retrying() && g_buf_start + (g_attempts + 1) * air < t)"),
    ("no ACK error per attempt",
     "        g_attempts++;\n        if (g_ack_errors) { bus_errors(1); }",
     "        g_attempts++;"),
    ("ACK errors whether asked for or not",
     "        if (g_ack_errors) { bus_errors(1); }\n        if (g_abort_pend)",
     "        bus_errors(1);\n        if (g_abort_pend)"),
    ("single-shot unacknowledged raises no ACK error",
     "if (failed && g_ack_errors && g_hw_mode == TWAI_MODE_NORMAL && !g_acked) { bus_errors(1); }",
     ""),
    ("active flags while error-passive",
     " && g_tec < 128 && g_rec < 128)",
     ")"),
    ("active flags not counted",
     "        g_active_flags += n;\n",
     ""),
    ("active flags in reset mode",
     "if (g_state == TWAI_STATE_RUNNING && !g_rm_ll && g_tec < 128",
     "if (g_state == TWAI_STATE_RUNNING && g_tec < 128"),
    # Two cores (twai_port T12, 2026-11-27).
    ("stall does nothing",
     "    g_stalled[core_id] = true;\n",
     ""),
    ("stall of the calling core allowed",
     " || core_id != other_core())",
     ")"),
    ("stall allowed on the C3",
     "if (g_chip == TWAI_FAKE_CHIP_C3 || core_id",
     "if (core_id"),
    ("unstall does nothing",
     "if (core_id == 0 || core_id == 1) { g_stalled[core_id] = false; }",
     ""),
    ("the sender runs while stalled",
     "while (!g_stalled[other_core()] && g_oc_next <= now)",
     "while (g_oc_next <= now)"),
    ("the sender runs on the C3",
     "if (!g_oc_on || g_oc_busy || g_chip == TWAI_FAKE_CHIP_C3) { return; }",
     "if (!g_oc_on || g_oc_busy) { return; }"),
    ("a busy wait does not run the other core",
     "    catch_up();\n    other_core_run();\n}\n\n/* ----",
     "    catch_up();\n}\n\n/* ----"),
    ("the ll calls at the stall not recorded",
     "{ g_ll_at_stall = g_ll_calls; }",
     "{ }"),
]


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True).returncode


def build_and_run(cc, src, out, name):
    exe = os.path.join(out, name)
    rc = run([cc, "-std=c11", "-O1", "-w", "-I" + os.path.join(HERE, "include"),
              "-I" + os.path.join(HERE, "host"), "-I" + HERE, "-o", exe,
              os.path.join(HERE, "test_twai_fake.c"), src])
    if rc != 0:
        return "did not build"
    return "pass" if run([exe]) == 0 else "fail"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="the run's folder, which must not exist (default: a new "
                    "<time>_<pid> folder under scratch/twai_fake-mutants/)")
    ap.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = ap.parse_args(argv)
    if a.out is None:
        a.out = os.path.join(RUNS, "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    if os.path.lexists(a.out):
        print("%s already exists: remove it or give another --out (this run removes the folder "
              "it makes, so it does not run in one it did not make)" % a.out, file=sys.stderr)
        return 2
    os.makedirs(a.out)
    try:
        return run_mutants(a)
    finally:
        remove_tree(a.out)


def run_mutants(a):
    with open(os.path.join(HERE, "twai_fake.c"), encoding="utf-8") as f:
        base = f.read()

    clean = build_and_run(a.cc, os.path.join(HERE, "twai_fake.c"), a.out, "clean")
    print("unmutated: %s" % clean)
    ok = clean == "pass"
    for i, (name, old, new) in enumerate(MUTANTS):
        n = base.count(old)
        if n != 1:
            print("M%02d %-40s BAD MUTANT: its text occurs %d times" % (i, name, n))
            ok = False
            continue
        src = os.path.join(a.out, "m%02d.c" % i)
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write(base.replace(old, new))
        verdict = build_and_run(a.cc, src, a.out, "m%02d" % i)
        killed = verdict == "fail"
        print("M%02d %-40s %s" % (i, name, "killed" if killed else "SURVIVED (" + verdict + ")"))
        ok = ok and killed
    print("mutants: %d, %s" % (len(MUTANTS), "all killed" if ok else "NOT ALL KILLED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
