"""Mutants of the jammer's sources: the can-fail of its host tests.

TEST EQUIPMENT, NOT FOR VEHICLE (x2_test_images SPEC T8).

Each mutant breaks one behaviour of one source file by a text substitution
that must match exactly once. The host tests (Makefile: test_jam_parse, the
engine on its three builds, test_mcp2515_reset, test_main) are built against
the mutated copy and must FAIL on every mutant; the unmutated sources must
PASS first. A mutant that makes a test run away is stopped by the fakes'
runaway guard, or by the timeout here, and counts as killed. Copies and
builds go in <worktree>/scratch/x2-jammer-mutants/<pid>/, removed at the
end, pass or fail (AGENTS.md "Files and folders").

    python3.13 -B test/mutate.py          from the jammer folder, or anywhere
Exit 0: the original passes and every mutant fails. Exit 1 otherwise.
"""

import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES = {
    "PARSE": os.path.join(ROOT, "components", "jam_pulse", "jam_parse.c"),
    "ENGINE": os.path.join(ROOT, "components", "jam_pulse", "jam_engine.c"),
    "MAIN": os.path.join(ROOT, "main", "jammer_main.c"),
    "MCP": os.path.join(ROOT, "main", "mcp2515_reset.c"),
    "CONSOLE": os.path.join(ROOT, "main", "console_usj.c"),
}
TIMEOUT_S = 300

# (name, source, the behaviour it breaks, old text, new text)
MUTANTS = [
    # ---- the engine: pin, RMT, stops ----
    ("start-dominant", "ENGINE", "the pin is driven low before it is an output",
     "gpio_set_level(gpio, 1);    /* level first",
     "gpio_set_level(gpio, 0);    /* level first"),
    ("no-redrive", "ENGINE", "the pin is left undriven after a command",
     "    xSemaphoreGive(s_chan_mux);\n    jam_drive_recessive(s_cfg.tx_gpio);\n}\n\nstatic esp_err_t transmit",
     "    xSemaphoreGive(s_chan_mux);\n}\n\nstatic esp_err_t transmit"),
    ("not-inverted", "ENGINE", "the RMT output is not inverted (idle dominant)",
     "cfg.flags.invert_out = 1;", "cfg.flags.invert_out = 0;"),
    ("eot-dominant", "ENGINE", "a batch ends dominant",
     "tc.flags.eot_level = 0;", "tc.flags.eot_level = 1;"),
    ("wide-pulse", "ENGINE", "TRAIN's pulses are twice W",
     "s_sym.duration0 = c->w_us;\n        s_sym.level0 = 1;       /* inverted",
     "s_sym.duration0 = 2 * c->w_us;\n        s_sym.level0 = 1;       /* inverted"),
    ("no-layer-b", "ENGINE", "TRAIN's chunks are not sized to the deadline",
     "int64_t loops = left > 0 ? left / c->p_us : 0;",
     "int64_t loops = JAM_CHUNK_US / c->p_us;"),
    ("no-timer", "ENGINE", "the deadline timer is never started (layer a gone)",
     "    esp_timer_start_once(s_deadline_timer, (uint64_t)ms * 1000);\n",
     ""),
    ("stop-no-disable", "ENGINE", "a stop does not disable the RMT channel",
     "            rmt_disable(s_ch);\n            s_enabled = false;\n        }\n        xSemaphoreGive(s_chan_mux);\n    }\n    if (s_task)",
     "        }\n        xSemaphoreGive(s_chan_mux);\n    }\n    if (s_task)"),
    ("stop-ignored", "ENGINE", "STOP does not stop a running command",
     "if (busy) hw_stop(WHY_STOP_CMD);", "(void)busy;"),
    ("early-stop-lost", "ENGINE", "a STOP before the task starts is dropped",
     "    xTaskNotifyStateClear(NULL);\n    ulTaskNotifyValueClear(NULL, 0xFFFFFFFF);\n    uint32_t ms",
     "    xTaskNotifyStateClear(NULL);\n    ulTaskNotifyValueClear(NULL, 0xFFFFFFFF);\n    s_stop = false;\n    s_stop_why = WHY_NONE;\n    uint32_t ms"),
    ("pulses-no-layer-b", "ENGINE", "PULSES sends past the deadline",
     "            if (t + c->w_us + PULSE_GAP_US > limit) { end = LAYER_B_END; break; }\n",
     ""),
    ("a-only-backstop-0", "ENGINE", "a_only keeps layer (b) at the deadline",
     "#define LAYER_B_EXTRA_US ((int64_t)JAM_BACKSTOP_US)",
     "#define LAYER_B_EXTRA_US ((int64_t)0)"),
    ("b-only-has-timer", "ENGINE", "b_only still has the deadline timer",
     "#define HAVE_LAYER_A 0", "#define HAVE_LAYER_A 1"),
    ("train-count-double", "ENGINE", "TRAIN's pulses= counts each chunk twice",
     "            pulses += (uint32_t)loops;\n",
     "            pulses += 2 * (uint32_t)loops;\n"),
    ("train-count-cut-dropped", "ENGINE",
     "TRAIN's pulses= leaves out the chunk a stop cuts",
     "            pulses += (uint32_t)loops;\n            uint32_t note = wait_note(JAM_CHUNK_US / 1000 * 2 + 50);\n            if (note & NOTE_STOP) { end = \"stopped\"; break; }\n",
     "            uint32_t note = wait_note(JAM_CHUNK_US / 1000 * 2 + 50);\n            if (note & NOTE_STOP) { end = \"stopped\"; break; }\n            pulses += (uint32_t)loops;\n"),
    ("pulses-count-stopped", "ENGINE",
     "PULSES counts the pulse a STOP kept off the wire",
     "                    && (wait_note((uint32_t)(wait_us / 1000)) & NOTE_STOP)) {\n                end = \"stopped\";\n",
     "                    && (wait_note((uint32_t)(wait_us / 1000)) & NOTE_STOP)) {\n                pulses++;\n                end = \"stopped\";\n"),
    ("refused-queued", "ENGINE", "a refused command is still queued",
     "    case JP_REFUSED:\n        reply(\"%s\", note);\n        return JAM_LINE_DONE;\n",
     "    case JP_REFUSED:\n        reply(\"%s\", note);\n        break;\n"),
    # ---- the parser: limits and refusals ----
    ("w-max-60", "PARSE", "W up to 60 us",
     "a[0] > JAM_W_MAX_US", "a[0] > 60"),
    ("w-min-5", "PARSE", "W down to 5 us",
     "a[0] < JAM_W_MIN_US", "a[0] < 5"),
    ("p-min-2w", "PARSE", "the period down to 2 W",
     "(uint64_t)a[1] < 4ull * a[0]", "(uint64_t)a[1] < 2ull * a[0]"),
    ("not-capped", "PARSE", "CAPPED is said but the duration is kept",
     "            out->ms = JAM_MS_MAX;\n", ""),
    ("ms-zero-ok", "PARSE", "a zero duration is accepted",
     "if (a[2] < 1) {", "if (0) {"),
    ("pulses-total", "PARSE", "PULSES' total skips the 100 ms",
     "(uint64_t)a[1] * a[2] + 100 > JAM_MS_MAX",
     "(uint64_t)a[1] * a[2] > JAM_MS_MAX"),
    ("busy-ignored", "PARSE", "a command is accepted while busy",
     "    if (busy) {\n", "    if (0) {\n"),
    ("extra-args-ok", "PARSE", "trailing arguments are accepted",
     "if (got != 3) {", "if (got < 3) {"),
    # ---- the MCP2515 reset ----
    ("no-reset", "MCP", "RESET is not sent",
     "const uint8_t reset = INS_RESET;", "const uint8_t reset = INS_READ;"),
    ("write-instr", "MCP", "a WRITE goes out in place of READ",
     "#define INS_READ    0x03", "#define INS_READ    0x02"),
    ("short-wait", "MCP", "the reads come 1 ms after RESET",
     "vTaskDelay(pdMS_TO_TICKS(10));", "vTaskDelay(pdMS_TO_TICKS(1));"),
    ("wrong-reg", "MCP", "CANCTRL is read as CANSTAT",
     "#define REG_CANSTAT 0x0E", "#define REG_CANSTAT 0x0F"),
    # ---- the app ----
    ("late-recessive", "MAIN", "the pin is driven after the MCP2515 reset",
     "    jam_drive_recessive(s_tx);      /* first */\n\n    s_mcp_err = mcp2515_reset_config(CAN2_PORT, &s_canstat, &s_canctrl);",
     "    s_mcp_err = mcp2515_reset_config(CAN2_PORT, &s_canstat, &s_canctrl);\n    jam_drive_recessive(s_tx);"),
    ("config-mode-inverted", "MAIN", "config_mode says yes for a chip out of it",
     "? \"yes\" : \"NO\"", "? \"NO\" : \"yes\""),
    ("elf-buffer-short", "MAIN", "the banner's ELF hash buffer cuts it short",
     "esp_app_get_elf_sha256(elf, sizeof elf);",
     "esp_app_get_elf_sha256(elf, 9);"),
    ("version-missing", "MAIN", "the banner names the IDF, not the build",
     "d->version, elf,", "d->idf_ver, elf,"),
    ("id-ignored", "MAIN", "ID gives no banner",
     "        jammer_banner();\n        return;\n    }\n    if (jam_line",
     "        return;\n    }\n    if (jam_line"),
    ("unknown-silent", "MAIN", "an unknown word gets no reply",
     "if (jam_line(line) == JAM_LINE_NOT_MINE) jcon_line(\"ERR unknown_command\");",
     "jam_line(line);"),
    ("no-boot-banner", "MAIN", "no banner at boot",
     "    vTaskDelay(pdMS_TO_TICKS(300));\n    jammer_banner();",
     "    vTaskDelay(pdMS_TO_TICKS(300));"),
    ("cr-kept", "MAIN", "a '\\r' from the host stays in the line",
     "        if (ch == '\\r') continue;\n", ""),
    # ---- the console ----
    ("no-prefix", "CONSOLE", "lines go out without the JAMMER prefix the host parses",
     "    memcpy(line, CONSOLE_PREFIX, n);\n", "    n = 0;\n"),
    ("line-end-passed", "CONSOLE", "a line end inside a reply starts a bare line",
     "line[n++] = (ch == '\\r' || ch == '\\n') ? ' ' : ch;",
     "line[n++] = ch;"),
]


def worktree_root():
    out = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def ignored(root, path):
    r = subprocess.run(["git", "-C", root, "check-ignore", "-q", path])
    return r.returncode == 0


def _onerror(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError as e:
        print("could not remove %s: %s" % (path, e))


def remove(path):
    if os.path.exists(path):
        shutil.rmtree(path, onerror=_onerror)
    if os.path.exists(path):
        print("could not remove %s" % path)


def run_tests(overrides, out):
    args = ["make", "-s", "-C", HERE, "OUT=" + out]
    args += ["%s=%s" % kv for kv in overrides.items()]
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %d s" % TIMEOUT_S
    return r.returncode, r.stdout + r.stderr


def main():
    root = worktree_root()
    scratch = os.path.join(root, "scratch", "x2-jammer-mutants",
                           str(os.getpid()))
    if not ignored(root, scratch):
        print("REFUSED: %s is not ignored by git (scratch/ must be)" % scratch)
        return 1
    originals = {}
    for key, path in SOURCES.items():
        with open(path, encoding="utf-8", newline="") as f:
            originals[key] = f.read()
    bad = 0
    try:
        os.makedirs(scratch)
        rc, text = run_tests({}, os.path.join(scratch, "original"))
        if rc != 0:
            print("ORIGINAL FAILS: the host tests must pass on the unmutated "
                  "sources")
            print(text)
            return 1
        print("%-22s PASS (as it must)" % "original")
        for name, key, what, old, new in MUTANTS:
            count = originals[key].count(old)
            if count != 1:
                print("%-22s BROKEN MUTANT: its text matches %d times in %s, "
                      "not once" % (name, count, os.path.basename(SOURCES[key])))
                bad += 1
                continue
            d = os.path.join(scratch, name)
            os.makedirs(d)
            src = os.path.join(d, os.path.basename(SOURCES[key]))
            with open(src, "w", encoding="utf-8", newline="") as f:
                f.write(originals[key].replace(old, new))
            rc, text = run_tests({key: src}, os.path.join(d, "build"))
            if rc == 0:
                print("%-22s SURVIVED: %s" % (name, what))
                bad += 1
            else:
                how = "timeout" if rc == 124 else "killed"
                print("%-22s %-7s (%s)" % (name, how, what))
    finally:
        remove(scratch)
        try:
            os.rmdir(os.path.dirname(scratch))
        except OSError:
            pass    # another run's folder is still there, or it is gone
    print()
    if bad:
        print("%d of %d mutants survived or are broken" % (bad, len(MUTANTS)))
        return 1
    print("all %d mutants killed" % len(MUTANTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
