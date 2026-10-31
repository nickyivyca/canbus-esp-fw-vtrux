#!/usr/bin/env python3
"""Break the fake one behaviour at a time; test_twai_fake must fail on each.

A check that cannot fail is not a check (AGENTS.md; the tester skill). Each
mutant below undoes one behaviour README.md says the fake has, in a copy of
twai_fake.c under the worktree's scratch/, and builds test_twai_fake.c
against the copy. The run passes only if the unmutated build passes and
every mutant build fails.

    python3 shared/twai_port/test/mutate.py [--out DIR] [--cc gcc]
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))

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
    ("bus-off clears the RX queue",
     "g_state = TWAI_STATE_BUS_OFF;\n    raise_alert(TWAI_ALERT_BUS_OFF);",
     "g_state = TWAI_STATE_BUS_OFF;\n    g_rxq.n = 0;\n    raise_alert(TWAI_ALERT_BUS_OFF);"),
    ("bus-off re-enters while bus-off",
     "if (!g_installed || g_state != TWAI_STATE_RUNNING) { return; }\n    g_state = TWAI_STATE_BUS_OFF;",
     "if (!g_installed || g_state == TWAI_STATE_STOPPED) { return; }\n    g_state = TWAI_STATE_BUS_OFF;"),
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
     "if (!g_installed || g_state != TWAI_STATE_RUNNING)\n    {\n        g_rx_not_rx++;",
     "if (!g_installed)\n    {\n        g_rx_not_rx++;"),
    ("filter ignored",
     "if (!g_fcfg.single_filter) { return true; }",
     "return true;"),
    ("abort puts the frame on the wire",
     "g_buf_full = false;\n    g_ll_removed++;",
     "flog_push(&g_wire, &g_buf.f);\n    g_buf_full = false;\n    g_ll_removed++;"),
    ("abort reports no TX_SUCCESS",
     "g_ll_removed++;\n    raise_alert(TWAI_ALERT_TX_SUCCESS);",
     "g_ll_removed++;"),
    ("the S3 counted as measured",
     "uint32_t twai_ll_get_status(twai_dev_t *hw)\n{\n    if (g_chip != TWAI_FAKE_CHIP_C3) { g_ll_unmeasured++; }",
     "uint32_t twai_ll_get_status(twai_dev_t *hw)\n{"),
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "scratch", "twai_fake-mutants"))
    ap.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
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
