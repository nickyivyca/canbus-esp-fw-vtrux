#!/usr/bin/env python3
"""Break twai_port one behaviour at a time; its unit tests must fail on each.

The implementor's (SPEC.md T11), beside the unit tests it scores. Each mutant
below undoes one behaviour in a copy of twai_port.c or the C3 register file,
twai_port_ll_esp32c3.c, and builds and runs test_twai_port against the copy.
The run passes only if the unmutated build passes and every mutant builds and
fails. A mutant that does not build is not a catch, and one whose text does
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
    rc, _ = run([os.path.join(out, "test_twai_port")], copy)
    return "pass" if rc == 0 else "fail"


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
