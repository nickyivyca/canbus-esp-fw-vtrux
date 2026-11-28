#!/usr/bin/env python3
"""Break twai_port one behaviour at a time; its unit tests must fail on each.

The implementor's (SPEC.md T11), beside the unit tests it scores. Each mutant
below undoes one behaviour in a copy of twai_port.c or a register file
(twai_port_ll_esp32c3.c, twai_port_ll_esp32s3.c), and builds and runs the
three unit binaries against the copy; a mutant is caught when any of them
fails. The run passes only if the unmutated build passes and every mutant
builds and fails. A mutant that does not build is not a catch, and one whose text does
not occur exactly once ran nothing; either fails the run. Self-checks, not
verification: acceptance is the testers', on the T10 fake in test/.

    python3 shared/twai_port/unit/mutate.py [--only N,N...] [--cc gcc]

From the repo root, where make and the C compiler are (Linux, or WSL). The
copies and binaries go in this run's own folder in the worktree's scratch/,
scratch/twai_port-unit-mutants/<time>_<pid>/, removed when the run ends, pass
or fail (AGENTS.md "Removing scratch").
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
COMPONENTS = ("shared/twai_port", "shared/can_port")   # the unit build reads both
UNIT = "shared/twai_port/unit"
TP = "shared/twai_port/twai_port.c"
LL = "shared/twai_port/twai_port_ll_esp32c3.c"
S3 = "shared/twai_port/twai_port_ll_esp32s3.c"
BINS = ("test_twai_port", "test_twai_port_s3", "test_twai_port_s3_probe",
        "test_twai_port_s3_host")

# (name, file, text, its replacement). Each text occurs once in its file.
MUTANTS = [
    ("hook skipped when down", TP,
     "    if (s_cfg.pre_teardown != NULL)\n    {\n        s_cfg.pre_teardown(s_cfg.hook_ctx);\n    }\n    if (!s_installed)\n    {\n        return ESP_OK;\n    }",
     "    if (!s_installed)\n    {\n        return ESP_OK;\n    }\n    if (s_cfg.pre_teardown != NULL)\n    {\n        s_cfg.pre_teardown(s_cfg.hook_ctx);\n    }"),
    ("no recovering refusal", TP,
     "    if (st.state == TWAI_STATE_RECOVERING)\n    {\n        /*",
     "    if (0)\n    {\n        /*"),
    ("stop from any state", TP,
     "    if (st.state == TWAI_STATE_RUNNING)\n    {\n        err = twai_stop();",
     "    if (1)\n    {\n        err = twai_stop();"),
    ("restart losses not counted", TP,
     "            s_count.rx_lost_restart += st.msgs_to_rx;\n", ""),
    ("restart losses outside the total", TP,
     "                      + s_count.rx_lost_restart;", ";"),
    ("flush not counted", TP,
     "            count_add(&s_count.rx_flushed, st.msgs_to_rx);", "            (void)st;"),
    ("flush counted as a loss", TP,
     "            count_add(&s_count.rx_flushed, st.msgs_to_rx);",
     "            count_add(&s_count.rx_lost_queue_full, st.msgs_to_rx);"),
    ("flush counted though the clear failed", TP,
     "        if (twai_clear_receive_queue() == ESP_OK && have)",
     "        (void)twai_clear_receive_queue();\n        if (have)"),
    ("no standby restore on refused uninstall", TP,
     "         * when the stop is refused. */\n        (void)board_can_standby(s_cfg.board_port, false);",
     "         * when the stop is refused. */"),
    ("rx read before stop", TP,
     "            rx_gone = st.msgs_to_rx;", "            (void)0;"),
    ("discards booked on a failed uninstall", TP,
     "    err = twai_driver_uninstall();\n    if (err != ESP_OK)\n    {",
     "    s_count.tx_discarded_teardown += tx_gone;\n    s_count.rx_discarded_teardown += rx_gone;\n    err = twai_driver_uninstall();\n    if (err != ESP_OK)\n    {"),
    ("mode recorded before the teardown", TP,
     "    /* The new mode is recorded only once the teardown has succeeded. */\n    const esp_err_t err = twai_port_teardown();",
     "    s_mode = mode;\n    const esp_err_t err = twai_port_teardown();"),
    ("same mode reinstalls", TP,
     "    if (mode == s_mode)\n    {\n        return ESP_OK;\n    }\n    if (!s_installed)",
     "    if (!s_installed)"),
    ("counts taken as the raw value", TP,
     "        *total += raw - *base;", "        *total = raw;"),
    ("bus-off counted per read", TP,
     "    if (!s_count.bus_off)\n    {\n        s_count.bus_off_entries++;",
     "    {\n        s_count.bus_off_entries++;"),
    ("every refusal tx_dropped", TP,
     "    else if (err == ESP_ERR_TIMEOUT || err == ESP_FAIL)", "    else if (1)"),
    ("no single-shot", TP, "        tx.ss = 1;", "        (void)0;"),
    ("no self-reception", TP,
     "        tx.self = 1;    /* the chip's loopback: each frame sent is received */",
     "        (void)0;"),
    ("tx failed not held", TP,
     "        s_tx_failed_held = true;    /* until the project takes it */", "        (void)0;"),
    ("recovery before counting", TP,
     "            twai_status_info_t st;\n            if (read_status(&st) == ESP_OK)\n            {\n                count_add(&s_count.tx_discarded_bus_off, st.msgs_to_tx);\n            }\n            const esp_err_t err = twai_initiate_recovery();",
     "            const esp_err_t err = twai_initiate_recovery();\n            twai_status_info_t st;\n            if (read_status(&st) == ESP_OK)\n            {\n                count_add(&s_count.tx_discarded_bus_off, st.msgs_to_tx);\n            }"),
    ("dropped rtr restarts the wait", TP,
     "            left = (spent >= wait) ? 0 : wait - spent;", "            left = wait; (void)spent;"),
    ("rtr never dropped", TP,
     "        if (!(s_cfg.drop_rtr && out->rtr))", "        if (1)"),
    ("alerts failure leaves it installed", TP,
     "        (void)twai_stop();\n        (void)twai_driver_uninstall();\n        return install_failed(TWAI_PORT_STEP_ALERTS, err);",
     "        return install_failed(TWAI_PORT_STEP_ALERTS, err);"),
    ("install hook after install", TP,
     "    twai_port_filter_t filter = s_filter;\n    if (s_cfg.install_hook != NULL)\n    {\n        s_cfg.install_hook(s_cfg.hook_ctx, &filter);\n    }",
     "    twai_port_filter_t filter = s_filter;"),
    ("no standby release", TP,
     "    (void)board_can_standby(s_cfg.board_port, false);\n    return ESP_OK;\n}",
     "    return ESP_OK;\n}"),
    ("no abort on failure", TP, "        ESP_ERROR_CHECK(err);", "        (void)0;"),
    ("down status calls the driver", TP,
     "    if (s_installed)\n    {\n        const esp_err_t err = read_status(&st);",
     "    if (1)\n    {\n        const esp_err_t err = read_status(&st);"),
    ("listen-only installs normal", TP,
     "    case CAN_PORT_MODE_LISTEN_ONLY:      *out = TWAI_MODE_LISTEN_ONLY; return true;",
     "    case CAN_PORT_MODE_LISTEN_ONLY:      *out = TWAI_MODE_NORMAL; return true;"),
    ("abort while transmitting", LL,
     "    if (seen == TWAI_PORT_BUF_AWAITING)\n    {\n        twai_ll_set_cmd_abort_tx(hw);",
     "    if (seen != TWAI_PORT_BUF_EMPTY)\n    {\n        twai_ll_set_cmd_abort_tx(hw);"),
    ("TS read before TBS", LL,
     "    if (st & TWAI_LL_STATUS_TBS) { return TWAI_PORT_BUF_EMPTY; }",
     "    if (st & TWAI_LL_STATUS_TS) { return TWAI_PORT_BUF_TRANSMITTING; }\n    if (st & TWAI_LL_STATUS_TBS) { return TWAI_PORT_BUF_EMPTY; }"),
    # T12, the restart stop.
    ("restart stop with the clock off", LL,
     "    if (!r->clock_on || r->held_in_reset)\n    {\n        return ESP_OK;\n    }",
     "    if (0)\n    {\n        return ESP_OK;\n    }"),
    ("restart stop while held in reset", LL,
     "    if (!r->clock_on || r->held_in_reset)", "    if (!r->clock_on)"),
    ("restart stop waits for TS only", LL,
     "             (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) == 0 ||",
     "             (TWAI_LL_STATUS_TS)) == 0 ||"),
    ("restart stop never waits", LL,
     "            waited >= RESTART_WAIT_MAX_US)\n", "            1)\n"),
    ("restart stop aborts once", LL,
     "    for (;;)\n    {\n        if (twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING)\n        {\n            r->aborts++;\n        }\n",
     "    if (twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING)\n    {\n        r->aborts++;\n    }\n    for (;;)\n    {\n"),
    ("restart stop no reset mode on time-out", LL,
     "    r->status_at_reset = twai_ll_get_status(hw);\n    twai_ll_enter_reset_mode(hw);",
     "    r->status_at_reset = twai_ll_get_status(hw);\n    if (!r->timed_out) { twai_ll_enter_reset_mode(hw); }"),
    ("restart stop bound 2 ms", LL,
     "#define RESTART_WAIT_MAX_US  1000u", "#define RESTART_WAIT_MAX_US  2000u"),
    ("restart stop steps of 1 us", LL,
     "#define RESTART_WAIT_STEP_US 2u", "#define RESTART_WAIT_STEP_US 1u"),
    ("restart stop entry status read late", LL,
     "    r->was_in_reset    = twai_ll_is_in_reset_mode(hw);\n    r->status_at_entry = twai_ll_get_status(hw);",
     "    r->was_in_reset    = twai_ll_is_in_reset_mode(hw);\n    r->status_at_entry = TWAI_LL_STATUS_TBS;"),
    # T14 to T18, the kept-driver switch.
    ("switch allowed where unmeasured", TP,
     "        if (!twai_port_ll_switch_supported())", "        if (0)"),
    ("kept port installs in listen-only", TP,
     "        /* T14: the driver's mode is fixed at install and gates every send. */\n        mode = TWAI_MODE_NORMAL;",
     "        /* T14 */"),
    ("sends taken in listen-only", TP,
     "    if (!s_installed || s_tx_block)", "    if (!s_installed)"),
    ("no-ACK through the switch", TP,
     "    return s_cfg.kept_driver_switch && from_kept && to_kept;",
     "    return s_cfg.kept_driver_switch && (from_kept || to_kept);"),
    ("switch while not running", TP,
     "    if (err == ESP_OK && st.state != TWAI_STATE_RUNNING)", "    if (0)"),
    ("no alert poll at the switch", TP,
     "    (void)switch_poll();\n\n    if (to_lom)\n    {\n        rep.held = switch_remove_held();",
     "    if (to_lom)\n    {\n        rep.held = switch_remove_held();"),
    ("switch's events lost", TP,
     "    uint32_t ev = s_ev_pending;", "    uint32_t ev = 0;"),
    ("held frames not counted", TP,
     "    s_count.tx_discarded_switch += held;\n", ""),
    ("queue not cleared at the switch", TP,
     "    (void)twai_clear_transmit_queue();\n", ""),
    ("oldest frames handed back", TP,
     "(recorded - held + i) % TWAI_PORT_SWITCH_HELD_MAX", "(i) % TWAI_PORT_SWITCH_HELD_MAX"),
    ("aborted frame counted twice", TP,
     "        s_tx_failed_skip++;\n", ""),
    ("abort's event passed up", TP,
     "        s_ev_pending = (s_ev_pending & ~TWAI_PORT_EV_TX_FAILED) | failed_was_pending;\n        s_tx_failed_held = failed_was_held;\n",
     "        (void)failed_was_pending;\n        (void)failed_was_held;\n"),
    ("uncounted abort eats a later failure", TP,
     "    if (driver_holds)\n    {\n        s_tx_failed_skip = 0;\n    }", ""),
    ("REC 127 into listen-only", TP,
     "    rep.rec_written = to_lom ? 128u : 0u;", "    rep.rec_written = to_lom ? 127u : 0u;"),
    ("counters from the driver", TP,
     "            twai_port_ll_counters(&out->tec, &out->rec);", "            (void)0;"),
    ("no bound on the abort wait", TP,
     "        if (waited >= SWITCH_ABORT_WAIT_MAX_US)\n        {\n            break;\n        }",
     "        if (waited >= 100u * SWITCH_ABORT_WAIT_MAX_US)\n        {\n            break;\n        }"),
    ("abort wait summed from its steps", TP,
     "        waited = esp_timer_get_time() - t0;\n        if (freed)\n        {\n            driver_holds = false;\n            break;\n        }\n        if (waited >= SWITCH_ABORT_WAIT_MAX_US)\n        {\n            break;\n        }\n        esp_rom_delay_us(SWITCH_ABORT_WAIT_STEP_US);\n",
     "        (void)t0;\n        if (freed)\n        {\n            driver_holds = false;\n            break;\n        }\n        if (waited >= SWITCH_ABORT_WAIT_MAX_US)\n        {\n            break;\n        }\n        esp_rom_delay_us(SWITCH_ABORT_WAIT_STEP_US);\n        waited += SWITCH_ABORT_WAIT_STEP_US;\n"),
    ("abort wait from the first status read", TP,
     "    const int64_t t0 = esp_timer_get_time();\n    int64_t waited = 0;\n    bool driver_holds = true;\n    for (;;)\n    {\n        const bool freed = read_status(&st) == ESP_OK && st.msgs_to_tx == 0;\n        waited = esp_timer_get_time() - t0;",
     "    int64_t t0 = -1;\n    int64_t waited = 0;\n    bool driver_holds = true;\n    for (;;)\n    {\n        const bool freed = read_status(&st) == ESP_OK && st.msgs_to_tx == 0;\n        const int64_t now = esp_timer_get_time();\n        if (t0 < 0) { t0 = now; }\n        waited = now - t0;"),
    # The S3 register file (T19).
    ("host switch ignored", S3,
     "#define S3_SWITCH_SUPPORTED 1", "#define S3_SWITCH_SUPPORTED 0"),
    ("switch aborts through the public T8", TP,
     "    const twai_port_buf_t seen = twai_port_ll_abort_if_awaiting();",
     "    const twai_port_buf_t seen = twai_port_abort_if_awaiting();"),
    ("S3 supported without the switch", S3,
     "#define S3_SUPPORTED 0      /* not measured yet (T19) */", "#define S3_SUPPORTED 1"),
    ("S3 abort while transmitting", S3,
     "    if (seen == TWAI_PORT_BUF_AWAITING)\n    {\n        twai_ll_set_cmd_abort_tx(hw);",
     "    if (seen != TWAI_PORT_BUF_EMPTY)\n    {\n        twai_ll_set_cmd_abort_tx(hw);"),
    ("S3 switch without reset mode", S3,
     "    out->buf = buf_from_status(out->status);\n    twai_ll_enter_reset_mode(hw);",
     "    out->buf = buf_from_status(out->status);"),
    ("S3 REC written after reset mode", S3,
     "    twai_ll_set_rec(hw, rec);\n    *rec_back = twai_ll_get_rec(hw);\n    *tec_back = twai_ll_get_tec(hw);\n    twai_ll_exit_reset_mode(hw);",
     "    twai_ll_exit_reset_mode(hw);\n    twai_ll_set_rec(hw, rec);\n    *rec_back = twai_ll_get_rec(hw);\n    *tec_back = twai_ll_get_tec(hw);"),
    ("S3 restart stop no stall", S3,
     "        esp_cpu_stall(other);", "        (void)other;"),
    ("S3 restart stop stalls a stalled core", S3,
     "    if (!stalled_at_entry)\n    {\n        esp_cpu_stall(other);",
     "    if (stalled_at_entry || !stalled_at_entry)\n    {\n        esp_cpu_stall(other);"),
    ("S3 restart stop stalls its own core", S3,
     "    const int other = (core == 0) ? 1 : 0;",
     "    const int other = core;"),
    ("S3 stall record before reset mode", S3,
     "    twai_ll_enter_reset_mode(hw);\n    r->entered_reset   = true;\n    STALL_RECORD(core, other, stalled_at_entry, !stalled_at_entry, true);",
     "    STALL_RECORD(core, other, stalled_at_entry, !stalled_at_entry, true);\n    twai_ll_enter_reset_mode(hw);\n    r->entered_reset   = true;"),
    ("S3 stall record not at the early return", S3,
     "        STALL_RECORD(core, other, stalled_at_entry, !stalled_at_entry, false);\n", ""),
    ("S3 stall record called from the entry read", S3,
     "s_stall_rec.stall_called = (called_) ? 1u : 0u;",
     "s_stall_rec.stall_called = (stalled_) ? 1u : 0u;"),
    ("S3 stall record core swapped", S3,
     "s_stall_rec.core = (uint32_t)(core_);", "s_stall_rec.core = (uint32_t)(other_);"),
    ("S3 stall record C0 fields from the C1 register", S3,
     "cap_options0 = REG_READ(RTC_CNTL_OPTIONS0_REG)",
     "cap_options0 = REG_READ(RTC_CNTL_SW_CPU_STALL_REG)"),
    ("S3 stall record check not read", S3,
     "    return out->magic == TWAI_PORT_S3_STALL_MAGIC\n           && out->check == stall_check(out);",
     "    return out->magic == TWAI_PORT_S3_STALL_MAGIC;"),
    ("S3 stall record not cleared", S3,
     "    s_stall_rec.magic = 0;\n    s_stall_rec.check = 0;", "    (void)0;"),
    ("S3 restart stop no abort", S3,
     "        if (abort_if_awaiting_raw(hw) == TWAI_PORT_BUF_AWAITING)\n        {\n            r->aborts++;\n        }\n",
     ""),
    ("S3 restart stop no reset mode", S3,
     "    r->status_at_reset = twai_ll_get_status(hw);\n    twai_ll_enter_reset_mode(hw);",
     "    r->status_at_reset = twai_ll_get_status(hw);"),
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
    """'pass', 'fail' or 'did not build', for the copy at `copy`: 'fail' if
    any of the unit binaries fails."""
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

    run_dir = os.path.join(ROOT, "scratch", "twai_port-unit-mutants",
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
