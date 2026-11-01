#!/usr/bin/env python3
"""Break can_port one behaviour at a time; its unit tests must fail on each.

The implementor's (SPEC.md C6), beside the unit tests it scores. Each mutant
below undoes one behaviour in a copy of can_port.c or can_stock.c, the stock
calls, and builds and runs test_can_port against the copy. The owner's
refusal (C4, gen-inhibit spec 3.2) is among them: it moved here from
gen-inhibit's main/can.c at its port step 1b, and gen-inhibit's shim mutants
no longer reach it.
The run passes only if the unmutated build passes and every mutant builds and
fails. A mutant that does not build is not a catch, and one whose text does
not occur exactly once ran nothing; either fails the run. Self-checks, not
verification.

    python3 shared/can_port/unit/mutate.py [--only N,N...] [--cc gcc]

From the repo root, where make and the C compiler are (Linux, or WSL). The
copies and binaries go in this run's own folder in the worktree's scratch/,
scratch/can_port-unit-mutants/<time>_<pid>/, removed when the run ends, pass
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
COMPONENTS = ("shared/can_port", "shared/twai_port")   # its stand-ins are twai_port's
UNIT = "shared/can_port/unit"
CS = "shared/can_port/can_stock.c"
CP = "shared/can_port/can_port.c"

# (name, file, text, its replacement). Each text occurs once in its file.
MUTANTS = [
    ("refusal not counted", CS, "\t\tcan_port_owner_refused();\n", ""),
    ("refusal logged every time", CS,
     "if(now_us - s_last_refusal_log > 5000000 || s_last_refusal_log == 0)",
     "if(1 || now_us - s_last_refusal_log > 5000000)"),
    ("owned send not refused", CS,
     "\tif(can_port_owner_says_owned())\n\t{", "\tif(0)\n\t{"),
    ("refused teardown marks off", CS,
     "\t\t\tcan_cfg.bus_state = ON_BUS;\n\t\t}", "\t\t\tcan_cfg.bus_state = OFF_BUS;\n\t\t}"),
    ("never-enabled disable skips the teardown", CS,
     "\tconst esp_err_t err = can_port_down(CAN_STOCK_PORT);",
     "\tconst esp_err_t err = (was == ON_BUS) ? can_port_down(CAN_STOCK_PORT) : ESP_OK;"),
    ("silent ignored", CS,
     ".mode = can_cfg.silent ? CAN_PORT_MODE_LISTEN_ONLY : CAN_PORT_MODE_NORMAL,",
     ".mode = CAN_PORT_MODE_NORMAL,"),
    ("no flush after enable", CS,
     "\tcan_port_flush_rx(CAN_STOCK_PORT);\n\tcan_unblock();", "\tcan_unblock();"),
    ("silent settable while on", CS,
     "void can_set_silent(uint8_t flag)\n{\n\tif(can_cfg.bus_state == ON_BUS)",
     "void can_set_silent(uint8_t flag)\n{\n\tif(0)"),
    ("double register allowed", CP,
     "    if (s_ports[port].ops != NULL)\n    {\n        return ESP_ERR_INVALID_STATE;",
     "    if (0)\n    {\n        return ESP_ERR_INVALID_STATE;"),
    ("msgs_to_rx is the depth", CS, "\treturn st.rx_waiting;", "\treturn st.rx_queue_len;"),
    ("enabled although up failed", CS,
     "\t\tESP_LOGE(TAG, \"port 0 would not come up: %s\", esp_err_to_name(err));\n\t\treturn err;",
     "\t\tESP_LOGE(TAG, \"port 0 would not come up: %s\", esp_err_to_name(err));"),
    ("stock enable does not restart", CS,
     "\tESP_ERROR_CHECK(can_port_stock_enable());", "\t(void)can_port_stock_enable();"),
    ("reporting form restarts", CS,
     "esp_err_to_name(err));\n\t\treturn err;",
     "esp_err_to_name(err));\n\t\tESP_ERROR_CHECK(err);\n\t\treturn err;"),
    ("reporting form says OK on failure", CS,
     "esp_err_to_name(err));\n\t\treturn err;", "esp_err_to_name(err));\n\t\treturn ESP_OK;"),
    ("configure skipped", CS,
     "\tesp_err_t err = can_port_configure(CAN_STOCK_PORT, &s);",
     "\tesp_err_t err = ESP_OK; (void)s;"),
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
    rc, _ = run([os.path.join(out, "test_can_port")], copy)
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

    run_dir = os.path.join(ROOT, "scratch", "can_port-unit-mutants",
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
