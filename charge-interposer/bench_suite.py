"""The charge interposer's suites, for the shared chain runner.

    PYTHONPATH=<shared/bench> py -3.14 -B -m vtrux_bench.run plan \\
        --suite charge-interposer/bench_suite.py:emulated --settings <json>
    ... run [--group G]... [--check GLOB]... [--resume RUN_DIR]

<shared/bench> is the pinned one, `py -3.14 -B charge-interposer/shared_pin.py
where shared/bench` (port step 2a: the harness takes shared code only as the
tag charge-interposer/firmware/shared_pins.json pins, from its read-only
checkout ../shared-<tag>/). Loading this file refuses a vtrux_bench imported
from anywhere else (shared_pin.use_bench), so a run on another copy stops
before it starts. -B matters: bytecode in the checkout changes its R7 digest,
and the pin check then refuses it.

Tester, 2026-10-18 (reviewer's item: the chain runner and the baseline).
The checks as they stand at origin 7891a23 / src 36edf3e55552,
unshortened, closed loop at 1x (reviewer's ruling 1). Every check is one
of the evidence doc's checks; skipped ones name the spec item or ruling
that defers them.

Three suites on the one core (user's ruling, port plan "Two suites per
project", 2026-10-18):
  emulated  charge-interposer-emulated: no hardware, no lock. The builds,
            the host suites (host bridge, host unit, host diff, the
            Python logic tests), the 27 closed-loop sims at 1x and the
            capture-trace rebuild. The implementor's self-check; evidence
            when the tester runs it.
  bench     charge-interposer-bench: hardware only, holding the bench lock
            from start to finish: before the cutover the Holder line,
            taken and released by hand; under the bench log
            (settings.bench_lock_log) the runner takes and releases it
            itself. Refuses to start unless the newest emulated run is on
            the same code and matched its reference (Suite.requires).
            Its boards are the X2 and the WiCAN (bench_wican.py: never
            flashed, identified off its page). The WiCAN's start and
            release reboots are the suite's own hooks, so the hand reboots
            before and after a run end here (reviewer, 2026-10-23).
            gen-inhibit's wican_reboot.py disarms a WiCAN that comes up
            armed, so every run, and every pause, leaves the WiCAN
            rebooted and disarmed (mode 0).
  harness   charge-interposer-harness: the tester's harness tests and
            mutants -- the checks that test the checks. Run when the
            harness changes.

Lanes (reviewer's ruling 3): madhouse-debian groups (m-*) and local groups
(l-*) are separate lanes. Local checks are class "build": idle priority and
one job while any wall-clock test runs on the host.

The X2's images: bridge (the standing image), witness, truck, c12_bench,
vport_bench. The interposer's images (port step 2a: ESP-IDF builds) are
taken from images_dir/<images_src>/<variant>/, as their build's
flasher_args.json gives them; each application's own descriptor must name
it (interposer_<variant>, version images_src, ESP-IDF v5.4.2), and
identify must read its build id, the ELF hash's first four bytes, back
(_interposer_image). In an acceptance run they are the list's own builds
instead (interposer_builds, from the build records: ACCEPT-SPEC A9), each
app's sha256 and descriptor ELF hash the record's. The two test images are
identified by their @HELLO and ELF sha256.

Settings beyond the core's (see the NICKY-XPS settings JSON in reverse-it
interposer-firmware/): interposer_code,
images_dir, images_src, serial_port, pcan_channel, wican_state,
madhouse_pio; for the bench suite's WiCAN (bench_wican.py), gi_tools
(gen-inhibit's tools/gen_inhibit, read-only) and gi_chain_settings
(gen-inhibit's chain settings JSON, read for its gi_images). The diag DBC
is the git copy (paths.diag_dbc()); interposer_dbc is retired and refused
(bench_identify).

Every script a check runs is in this tree (port step 0, reviewer
2026-10-26): the bench drivers in bench_drivers/, the mutants and other
checks of the checks in harness_checks/, rebuild_capture_traces.py here.
Until then they ran from reverse-it's interposer-firmware/ folder (the
settings interposer_artifacts and reverse_it, now unread), and the run
record carried their digest as artifacts_sha256; the commit carries them
now. canre is the pinned install (canre_pin.py), shipped to madhouse.
canbench is the pinned can-tools commit's (can_tools_pin.py), and the bench
run records the pin, its folder and the clone it came from.
"""

import atexit
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared_pin  # noqa: E402
# the pinned shared/bench, first on sys.path; a vtrux_bench from elsewhere
# already imported (the runner's own, if PYTHONPATH named another copy) is
# refused here
SHARED_BENCH = shared_pin.use_bench()

from vtrux_bench.run import (Check, Figure, Group, Image, KnownFail,  # noqa: E402
                             Result, Suite, Verdict)
import bench_identify  # noqa: E402
import bench_wican  # noqa: E402
import canre_pin  # noqa: E402
import can_tools_pin  # noqa: E402
import scratch_dir  # noqa: E402
# The ESP-IDF project's own inputs (port step 2a, Part B): every file of its
# one component, main/ (the sources, main/CMakeLists.txt and
# main/idf_component.yml), and the project root's build files. The board
# component and arduino-esp32 come in by dependencies.lock, which names
# their versions and hashes. Before 2a this was firmware/src plus
# build_identity.py's DIGEST_INPUTS (platformio.ini and the PlatformIO
# build scripts), all retired by Part B.
PRODUCT_MAIN = "main"
PRODUCT_ROOT_FILES = ("CMakeLists.txt", "sdkconfig", "dependencies.lock")


def product_files(root):
    """The interposer's product files in the tree at `root` (a clone root),
    for the runner's product digest (user 2026-10-20). The split is the
    component table's (reviewer, 2026-10-20): machine.py, plus the IDF
    project's inputs (PRODUCT_MAIN, PRODUCT_ROOT_FILES; .md files are
    notes, not inputs). Everything else -- the sims, run_scenario, pack.py,
    protocol.py, bus.py, replay.py, regress.py, tests -- is harness."""
    fw = os.path.join(root, "charge-interposer", "firmware")
    out = []
    for dirpath, dirs, files in os.walk(os.path.join(fw, PRODUCT_MAIN)):
        dirs.sort()
        for f in sorted(files):
            if not f.endswith(".md"):
                out.append(os.path.relpath(os.path.join(dirpath, f), root)
                           .replace(os.sep, "/"))
    for x in PRODUCT_ROOT_FILES:
        if os.path.exists(os.path.join(fw, x)):
            out.append("charge-interposer/firmware/" + x)
    out.append("charge-interposer/machine.py")
    return out

ROLE = "tester (charge-interposer)"

# The board scenarios: every run_scenario scenario except the two that are
# not runnable on a board as written (plan Step 4 run sheet, item 4).
BOARD_SCENARIOS = [
    "charger-fault-stop", "charger-fault-stop-flow-first",
    "boot-mid-session", "restart-no-standby", "restart-after-standby",
    "hvil-before-flow-drop", "fault-during-override",
    "fault-during-override-flow-first", "override-ended-by-vcu",
    "repeat-superseded-by-vcu", "repeat-replaced-by-trip",
    "repeat-reset-by-pilot-step", "normal-full", "imbalanced-full",
    "evap-override", "evap-override-unplug", "evap-override-bms-fault",
    "evap-override-high-cell", "evap-override-high-cell-far",
    "evap-override-imbalanced", "evap-override-imbalanced-hold",
    "evap-override-above-ceiling", "evap-override-16a", "balance-hold",
    "charger-silence-after-trip",
]
NOT_ON_BOARD = ["evap-bypass", "hard-ceiling-failsafe"]
SIM_SCENARIOS = BOARD_SCENARIOS + NOT_ON_BOARD

# Python tests in charge-interposer/ that run on madhouse (no pyserial).
# Tests of the product's logic (emulated suite):
LOGIC_PY_TESTS = [
    "test_machine.py", "test_trips.py", "test_invariants.py", "test_signals.py",
]
# Tests of the harness -- scorer, sims, capture pins, paths (harness suite):
HARNESS_PY_TESTS = [
    "test_released_into_hold.py", "test_repeat_bursts.py",
    "test_scorer_wiring.py", "test_hold_wire.py",
    "sims_test.py", "regress_offline_test.py", "capture_pins_test.py",
    "build_lookup_offline_test.py", "paths_test.py", "diag_cadence_test.py",
    "bus_gs_usb_test.py", "canre_pin_test.py", "can_tools_pin_test.py",
    "twai_fake_option_test.py", "scratch_dir_test.py", "scratch_run_test.py",
    "hw_mechanics_test.py",
]
# The ones of those also run with --can-fail, as <name>-canfail. One list
# for the harness suite and accept_adapter's catalogue, so they can't drift.
HARNESS_PY_CANFAILS = [
    "canre_pin_test.py", "can_tools_pin_test.py", "bus_gs_usb_test.py",
    "paths_test.py", "twai_fake_option_test.py", "scratch_dir_test.py",
    "scratch_run_test.py", "hw_mechanics_test.py",
]
MADHOUSE_PIO_PATH = "{madhouse_pio_dir}:/usr/local/bin:/usr/bin:/bin"

# Mutation / can-fail scripts in harness_checks/, run on madhouse from the
# shipped code (cwd {work}/code; each finds the code from its own place).
MADHOUSE_MUTANTS = [
    ("l2-mutations", ["bash", "harness_checks/l2_mutations.sh"]),
    ("twai-mutations", ["bash", "harness_checks/twai_mutations.sh"]),
    ("hold-mutations", ["bash", "harness_checks/hold_mutations.sh"]),
    ("busoff-mutations", ["bash", "harness_checks/busoff_mutations.sh"]),
    ("rx-hook-mutations", ["bash", "harness_checks/rx_hook_mutations.sh"]),
    ("conformance-mutations", ["bash", "harness_checks/conformance_mutations.sh"]),
    ("tx-fail-banking-mutation", ["bash", "harness_checks/tx_fail_banking_mutation.sh"]),
    ("hold-cadence-gate-mutation", ["bash", "harness_checks/hold_cadence_gate_mutation.sh"]),
    ("rx-order-prefix-control", ["bash", "harness_checks/rx_order_prefix_control.sh"]),
    ("switch-discard-witness", ["@python", "harness_checks/switch_discard_witness.py"]),
    ("monitor-6h-diff-canfail", ["@python", "harness_checks/monitor_6h_diff.py", "--can-fail"]),
    ("monitor-6h-diff-corrupt-pin", ["@python", "harness_checks/monitor_6h_diff.py", "--corrupt-pin"]),
]
MUTANT_JUDGES = {"monitor-6h-diff-corrupt-pin": "corrupt_pin_judge"}  # by name

# Bench drivers in bench_drivers/ with a --self-test (no hardware), run
# locally.
SELF_TESTS = [
    "bench_powerup_round", "bench_rx_order", "bench_charger_silent",
    "bench_innomaker_clock", "bench_switch_loss", "bench_switch_errors",
    "bench_switch_pending", "bench_vport", "bench_c12", "bench_flight_silence",
    "bench_usb_host", "bench_mcp_reset", "bench_hold12", "bench_topology",
    "bench_canbench_smoke",
]
# Bench drivers whose --can-fail runs their self-test against each of their
# MUTANTS (script_can_fail.py), run locally (test_script_coverage).
CAN_FAILS = [
    "bench_c12", "bench_mcp_reset", "bench_powerup_round", "bench_switch_loss",
    "bench_topology", "bench_usb_host", "bench_vport", "bench_hold12",
    "bench_canbench_smoke",
]

# Local scripts (they want pyserial or the Windows side).
EMULATED_LOCAL = [
    ("rebuild-capture-traces", ["{interposer_code}/rebuild_capture_traces.py"]),
]
# shared/bench's own tests (the decode identity, the chain runner), each run
# plain and with --can-fail (reviewer, 2026-10-22: no suite ran them).
# From port step 2a they are the pinned shared/bench's (SHARED_BENCH), which
# holds the acceptance runner's tests and scratch helpers too.
SHARED_BENCH_TESTS = [
    "test_accept_canfail.py", "test_accept_close.py", "test_accept_compile.py",
    "test_accept_judge.py", "test_accept_list.py", "test_accept_record.py",
    "test_accept_remote.py", "test_accept_run.py", "test_accept_setup.py",
    "test_accept_suite_runs.py", "test_accept_trees.py", "test_bench_lock.py",
    "test_decode.py", "test_run_core.py", "test_scratch.py",
    "test_scratch_helper.py",
]

HARNESS_LOCAL = [
    # the bench host's own pinned canbench (madhouse has no clone and
    # opens no dongle): refuses if CAN_TOOLS_DIR is unset or the pin is
    # not installed and checked
    ("can-tools-pin-check", ["{interposer_code}/can_tools_pin.py", "check"]),
    # these read the pinned checkouts ../shared-<tag>/ beside the git
    # worktree (shared_pin, vtrux_tools.tools_dir), which madhouse's
    # shipped copy, with no git, doesn't have (harness run
    # charge-interposer-harness_20261103T175908_7d3c: all three refused there)
    ("shared-pin-test", ["{interposer_code}/shared_pin_test.py"]),
    ("shared-pin-test-canfail", ["{interposer_code}/shared_pin_test.py", "--can-fail"]),
    ("refusal-pairs-test", ["{interposer_code}/refusal_pairs_test.py"]),
    ("refusal-pairs-test-canfail",
     ["{interposer_code}/refusal_pairs_test.py", "--can-fail"]),
    # the acceptance runner's "idf" build shell (accept_adapter.py): the
    # ESP-IDF export is PowerShell's, so Windows only
    ("idf-build-shell-test", ["{interposer_code}/fw_harness/idf_build_shell_test.py"]),
    ("idf-build-shell-test-canfail",
     ["{interposer_code}/fw_harness/idf_build_shell_test.py", "--can-fail"]),
    # spec 9 C2b's "differ only by the switch" (2a acceptance (h)), on
    # stand-in build folders: its judgments and its own can-fail
    ("variant-diff-self-test", ["{interposer_code}/fw_harness/variant_diff.py", "--self-test"]),
    # spec 8's "no LED" (2a acceptance (j)), on a stand-in build: its three
    # rules, its own can-fail and its control
    ("no-led-self-test", ["{interposer_code}/fw_harness/no_led.py", "--self-test"]),
    ("board-reset-test", ["{interposer_code}/board_reset_test.py"]),
    ("bench-suite-test", ["{interposer_code}/bench_suite_test.py"]),
    ("bench-suite-test-canfail",
     ["{interposer_code}/bench_suite_test.py", "--can-fail"]),
    ("bench-wican-test", ["{interposer_code}/bench_wican_test.py"]),
    ("bench-wican-test-canfail",
     ["{interposer_code}/bench_wican_test.py", "--can-fail"]),
    # script_can_fail's baseline run: an unmutated script that fails where
    # the mutants run makes every catch meaningless (2026-10-24)
    ("script-can-fail-test",
     ["{interposer_code}/bench_drivers/script_can_fail_test.py"]),
    ("rebuild-capture-traces-canfail",
     ["{interposer_code}/rebuild_capture_traces.py", "--can-fail"]),
    ("clause1-fall-mutations",
     ["{interposer_code}/harness_checks/clause1_fall_mutations.py"]),
    ("clause1-fall-mutations-canfail",
     ["{interposer_code}/harness_checks/clause1_fall_mutations.py", "--can-fail"]),
    ("bypass-diag-mutations",
     ["{interposer_code}/harness_checks/bypass_diag_mutations.py"]),
    ("e5-arming-controls", ["{interposer_code}/harness_checks/e5_arming_controls.py"]),
    ("clamp-frame-cases", ["{interposer_code}/harness_checks/clamp_frame_cases.py"]),
]

RETIRED_2A = (
    "retired at port step 2a: the PlatformIO build, its manifest and the "
    "build_check / digest_recheck_check family went with Part B "
    "(reviewer 2026-11-03, Q3); the IDF builds are 2a acceptance (h)")

EMULATED_KNOWN_FAILS = [
    KnownFail("m-host/host-bridge/l2.bridge-ok-veh-failed",
              "spec 8.2: a bridged frame that failed toward the vehicle must "
              "clear bridge_ok; labelled KNOWN FAIL on src 36edf3e55552 "
              "(2026-10-16), the ported image must pass it"),
    KnownFail("m-host/host-bridge/l2.bridge-ok-switch-discard",
              "spec 2.3 / 8.2: bridged frames discarded at the switch to "
              "listen-only must be counted; KNOWN FAIL on src 36edf3e55552 "
              "(reviewer 2026-10-16), the ported image must pass it"),
]
BENCH_KNOWN_FAILS = [
    KnownFail("b-vport/vport-listen-wrong-rate",
              "spec 2.3 / spec 9 (reviewer 2026-10-21): in listen-only at a "
              "wrong bit rate, error frames on the bus while a node "
              "transmits, none with it closed (2026-10-16 discriminator; "
              "a25e 77 and 55,359); the source is an open tracker item"),
    KnownFail("b-witness/switch-loss",
              "spec 2.3 / spec 12 (2026-10-15): each switch of the vehicle "
              "port loses 1-2 vehicle frames (44 over 41 switches at "
              "2,000 fps); a known product FAIL"),
]


# ---- judges -------------------------------------------------------------
def _lines(run, pat):
    rx = re.compile(pat)
    return [ln.strip() for ln in run.log_text().splitlines() if rx.search(ln)]


def script_judge(run):
    """Exit code 0 is PASS, unless the script printed VOID verdicts and no
    PASS one: then nothing was tested, and it is VOID (a25e: noread-boots
    printed three VOID boots and exited 0). Otherwise VOID if the script
    printed a VOID verdict (its premise was not met), else FAIL. FAIL /
    VOID lines go in the detail."""
    if run.rc is None:
        return Result(Verdict.ERROR, detail="no exit code (killed)")
    fails = _lines(run, r"^\s*(FAIL|VOID)\b")
    if run.rc == 0:
        voids = [ln for ln in fails if ln.startswith("VOID")]
        if voids and not _lines(run, r"^\s*PASS\b"):
            return Result(Verdict.VOID, detail="rc=0 but every verdict "
                          "VOID: %s" % "; ".join(voids[:3]))
        return Result(Verdict.PASS, detail="rc=0")
    if any(ln.startswith("VOID") for ln in fails) and \
            not any(ln.startswith("FAIL") for ln in fails):
        return Result(Verdict.VOID, detail="; ".join(fails[:3]))
    return Result(Verdict.FAIL, detail="rc=%d; %s" % (run.rc, "; ".join(fails[:3])))


def recorded_judge(run):
    """A measurement with no pass/fail of its own (spec 9's 'measured'
    items): PASS means it ran to completion and recorded; the figures are
    in its outputs."""
    r = script_judge(run)
    if r.verdict == Verdict.PASS:
        r.detail = "recorded (rc=0); see the check's outputs"
    return r


def c12_judge(run):
    """bench_c12 --step all: the silicon transcript against the fake. It
    exits 1 whenever a DATASHEET line differs; with --step all, C9 is not
    run (it needs --charger-segment-alone and is its own check), so C9's
    DATASHEET line always reads absent. PASS = recorded, and no DATASHEET
    line outside C9 differs; the count of differing lines is a figure."""
    out = os.path.join(run.outdir, "%s.txt" % run.check.id)
    if not os.path.exists(out):
        return Result(Verdict.ERROR, detail="no transcript %s (rc=%s)"
                      % (os.path.basename(out), run.rc))
    text = open(out, encoding="utf-8", errors="replace").read()
    m = re.search(r"^--- against the fake .*?: (\d+) line\(s\) differ ---$",
                  text, re.M)
    if not m:
        return script_judge(run)
    block = text[m.end():].split("\n\n", 1)[0].splitlines()
    ds = [ln.strip() for ln in block if "<-- a DATASHEET line" in ln]
    bad = [ln for ln in ds if not ln.startswith("C9-")]
    figs = {"lines_differ": Figure(int(m.group(1)), ""),
            "datasheet_differ_outside_c9": Figure(len(bad), "")}
    if bad:
        return Result(Verdict.FAIL, figures=figs,
                      detail="DATASHEET line(s) differ: " + " | ".join(bad)[:300])
    return Result(Verdict.PASS, figures=figs,
                  detail="recorded; %s line(s) differ from the fake, no "
                         "DATASHEET line outside C9" % m.group(1))


def host_bridge_judge(run):
    """host_bridge/run.sh: one sub-result per test, and per test_l2_bridge
    case. A case that opened (--- l2: X ---) but never closed is an ERROR."""
    text = run.log_text()
    out = []
    pin = "capture pin: l3_stimulus.txt matches its pin" in text
    out.append(Result(Verdict.PASS if pin else Verdict.FAIL, "capture-pin",
                      detail="pin line %s" % ("present" if pin else "MISSING")))
    opened = re.findall(r"^--- l2: (\S+) ---", text, re.M)
    passed = set(re.findall(r"^test_l2_bridge\[(\S+?)\]: all checks OK", text, re.M))
    failed = dict((c, n) for n, c in re.findall(r"^(\d+) failure\(s\) in (\S+)",
                                                text, re.M))
    for c in opened:
        if c in passed:
            out.append(Result(Verdict.PASS, "l2." + c, detail="all checks OK"))
        elif c in failed:
            fl = [ln for ln in text.split("--- l2: %s ---" % c, 1)[1]
                  .split("\n--- l2:", 1)[0].splitlines() if ln.startswith("FAIL")]
            out.append(Result(Verdict.FAIL, "l2." + c, detail="%s failure(s): %s"
                              % (failed[c], " | ".join(fl)[:300])))
        else:
            out.append(Result(Verdict.ERROR, "l2." + c,
                              detail="case opened but never reported"))
    tests = sorted(os.path.basename(p)[:-4] for p in glob.glob(
        os.path.join(run.ctx["interposer_code"], "fw_harness",
                     "host_bridge", "test_*.cpp")))
    for t in tests:
        if t == "test_l2_bridge":
            continue
        if re.search(r"^%s DID NOT BUILD" % t, text, re.M):
            out.append(Result(Verdict.ERROR, t, detail="did not build"))
        elif re.search(r"^%s: all checks OK" % t, text, re.M):
            out.append(Result(Verdict.PASS, t, detail="all checks OK"))
        else:
            out.append(Result(Verdict.FAIL, t, detail="no 'all checks OK' line"))
    if "test_l2_bridge" in tests and len(opened) < 35:
        out.append(Result(Verdict.FAIL, "l2.population",
                          detail="only %d L2 cases ran; 35 at the baseline"
                          % len(opened)))
    return out


def corrupt_pin_judge(run):
    """monitor_6h_diff.py --corrupt-pin flips each expected trace pin; its
    documented outcome is both pin checks FAILING (exit 1) while both
    machine.cpp replays still read IDENTICAL. PASS is exactly that."""
    if run.rc is None:
        return Result(Verdict.ERROR, detail="no exit code (killed)")
    fails = _lines(run, r"^\s*FAIL\b")
    pin = [ln for ln in fails if "do NOT match their pins" in ln]
    other = [ln for ln in fails if ln not in pin]
    same = _lines(run, r"^\s*ok\s+\S+ on machine\.cpp: IDENTICAL")
    if run.rc == 1 and len(pin) == 2 and not other and len(same) == 2:
        return Result(Verdict.PASS, detail="both corrupted pins caught; both "
                      "replays IDENTICAL")
    return Result(Verdict.FAIL, detail="rc=%s, %d pin FAIL(s) (want 2), "
                  "%d other FAIL(s), %d IDENTICAL (want 2)%s" % (
                      run.rc, len(pin), len(other), len(same),
                      ("; " + other[0][:100]) if other else ""))


def scenario_judge(run):
    """run_scenario.py: its final '-> PASS' / '-> FAIL' line."""
    v = _lines(run, r"^\s*-> (PASS|FAIL)")
    if not v:
        return Result(Verdict.ERROR, detail="no '-> PASS/FAIL' line (rc=%s)" % run.rc)
    fails = _lines(run, r"^\s*(FAIL|VOID)\b")
    if "-> PASS" in v[-1] and run.rc == 0:
        return Result(Verdict.PASS, detail=v[-1][:120])
    return Result(Verdict.FAIL, detail="%s; %s" % (v[-1][:80], "; ".join(fails[:3])))


# ---- images -------------------------------------------------------------
# The X2 the esptool flasher must find on the port before it writes
# (methodology, 2026-10-20: a chip-id mismatch writes nothing, and an image
# that names no chip is refused). The repo is public, so the MAC is never a
# literal here: it comes from the local, unpublished settings (x2_mac); the
# WiCAN is an ESP32-C3.
X2_CHIP_TYPE = "ESP32-S3"


def _x2_chip(settings):
    return {"type": X2_CHIP_TYPE, "mac": settings.get("x2_mac")}


# The interposer's images are built with ESP-IDF v5.4.2 (user, 2026-11-03);
# the bench's own test images (c12_bench, vport_bench) stay on v5.4.1
# (reviewer, 2026-11-03), so _idf_image does not check this.
INTERPOSER_IDF = "v5.4.2"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _interposer_image(variant, settings):
    """An ESP-IDF image of the interposer (port step 2a), from the folder
    images_dir/<images_src>/<variant>/, which holds the build's
    flasher_args.json and the files it names at their paths (the build's
    bootloader, partition table, otadata and application). Flashed exactly
    as flasher_args.json gives them, otadata included (the build's
    ota_data_initial.bin, not PlatformIO's boot_app0.bin).

    The identity is the application's own descriptor (spec 8.2): its
    project name must be interposer_<variant>, its version the folder's
    images_src (ESP-IDF's git describe of the commit; never -dirty), its
    ESP-IDF INTERPOSER_IDF, and identify must read the first four bytes of
    its ELF hash back from 0x7F7. A folder that does not say so is refused
    when the suite is built, before anything is flashed.

    In an acceptance run (shared/bench ACCEPT-SPEC A9, the list's
    images_from) the settings carry interposer_builds, {variant: {"dir",
    "build", "elf_sha256", "app_sha256"}}, from the build records the
    acceptance runner wrote (accept_adapter.suite_settings), and the images
    are the list's own builds, read from their build folders: a variant
    none of them gives is refused. The version is then the build's own (any
    but -dirty), and the record pins the bytes instead: the app's sha256
    must be the record's, and its descriptor's ELF hash the record's ELF
    sha256, so the bytes flashed, and the build id identify reads back,
    are those of the build the acceptance record names (the
    charge-interposer reviewer, 2026-11-04). The image's note names the
    build and its ELF sha256, for the nested run's record."""
    import build_lookup
    builds = settings.get("interposer_builds")
    pin = None
    if builds is not None:
        pin = builds.get(variant)
        if not pin:
            raise ValueError("the bench suite flashes the %s image, and no build of the "
                             "acceptance list gives it (its images_from gives %s)"
                             % (variant, ", ".join(sorted(builds)) or "none"))
        src, d = None, pin["dir"]
    else:
        src = settings["images_src"]
        d = os.path.join(settings["images_dir"], src, variant)
    fa_path = os.path.join(d, "flasher_args.json")
    with open(fa_path, encoding="utf-8") as f:
        fa = json.load(f)
    app_rel = (fa.get("app") or {}).get("file")
    files = sorted(((off, os.path.join(d, *rel.split("/")))
                    for off, rel in (fa.get("flash_files") or {}).items()),
                   key=lambda x: int(x[0], 16))
    want_offs = {"0x0", "0x8000", "0xe000", "0x10000"}
    if set(o for o, _p in files) != want_offs:
        raise ValueError("%s: flash_files at %s, not the X2's bootloader, "
                         "partition table, otadata and app (%s)"
                         % (fa_path, sorted(o for o, _p in files),
                            sorted(want_offs)))
    app = os.path.join(d, *(app_rel or "?").split("/"))
    if dict(files).get("0x10000") != app:
        raise ValueError("%s: its app is not the file at 0x10000" % fa_path)
    desc = build_lookup.app_desc_of_bin(app)
    if desc is None:
        raise ValueError("%s is not an ESP-IDF application image" % app)
    project = "interposer_%s" % variant
    for what, got, want in (("project name", desc["project_name"], project),
                            ("version", desc["version"], src),
                            ("ESP-IDF", desc["idf_ver"], INTERPOSER_IDF)):
        if want is not None and got != want:
            raise ValueError("%s: its %s is %r, not %r" % (app, what, got,
                                                          want))
    sha = desc["elf_sha256"]
    build_note = ""
    if pin is not None:
        if desc["version"].endswith("-dirty"):
            raise ValueError("%s: its version %r is a dirty tree's"
                             % (app, desc["version"]))
        if _sha256(app) != pin["app_sha256"]:
            raise ValueError("%s: its sha256 is not build %s's record's (%s)"
                             % (app, pin["build"], pin["app_sha256"][:16]))
        if sha != pin["elf_sha256"]:
            raise ValueError("%s: its descriptor's ELF hash %s is not build %s's "
                             "ELF sha256 %s" % (app, sha[:16], pin["build"],
                                                pin["elf_sha256"][:16]))
        build_note = "; list build %s, ELF sha256 %s" % (pin["build"],
                                                         pin["elf_sha256"])
    return Image(variant, "esptool", board="x2", expect=sha[:8],
                 params={"files": [[o, p] for o, p in files],
                         "md5": {p: _md5(p) for _o, p in files},
                         "chip": _x2_chip(settings)},
                 note="%s %s, %s, ELF %s%s" % (project, desc["version"],
                                               desc["idf_ver"], sha[:16], build_note))


def _idf_image(name, settings):
    d = os.path.join(settings["images_dir"], "idf", name)
    elf = os.path.join(d, "%s.elf" % name)
    with open(elf, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return Image(name, "esptool", board="x2", expect="%s:%s" % (name, sha),
                 params={"files": [["0x0", os.path.join(d, "bootloader.bin")],
                                   ["0x8000", os.path.join(d, "partition-table.bin")],
                                   ["0x10000", os.path.join(d, "%s.bin" % name)]],
                         "chip": _x2_chip(settings)},
                 note="ELF %s" % sha[:8])


# ---- staging for madhouse -----------------------------------------------
_STAGED = {}                # commit -> its gitb folder, in this process


def _remove_at_exit(path):
    """scratch_dir.remove(path) when this process exits."""
    atexit.register(scratch_dir.remove, path)


def _stage_git_bundle(code):
    """A git bundle of the clone's HEAD, for build-check on madhouse
    (build_check_in_clone.py): the shipped copy has no .git, and a build
    there gets no manifest row (e7d4, 2026-10-19). -> (dir, full commit).
    The bundle goes in gitb/ of a scratch_dir run folder that this
    process owns, scratch/charge-interposer-harness/runner-stage/
    runner-stage-<pid>-<rand>/, made once per commit and removed when
    the process exits: the runner ships it from there when the m-build
    group starts, so it lives as long as the run. A folder a killed
    run left is pruned by the next one. Never the system temp folder
    (AGENTS.md; reviewer, 2026-10-31: 170 MB there after one day). A
    bundle takes under a second to make, so none is kept between runs."""
    import subprocess
    repo = os.path.dirname(code)
    commit = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True,
                            check=True).stdout.strip()
    if commit in _STAGED:
        return _STAGED[commit], commit
    run = scratch_dir.make("runner-stage")
    _remove_at_exit(run)
    d = os.path.join(run, "gitb")
    os.makedirs(d)
    subprocess.run(["git", "-C", repo, "bundle", "create",
                    os.path.join(d, "repo.bundle"), "HEAD"],
                   capture_output=True, check=True)
    _STAGED[commit] = d
    return d, commit


def can_tools_info():
    """The can-tools pin the bench's canbench comes from (pins.json, beside
    canre's; user ruling 2026-10-30, via the reviewer): its repo and
    commit, the folder can_tools_pin.py wrote that commit's canbench/ to,
    and the clone at CAN_TOOLS_DIR it came from, once the folder passes
    the pin's checks. The clone's own HEAD plays no part. A pin that fails
    its checks, or CAN_TOOLS_DIR unset, is recorded as the refusal, with
    the pin still named; the checks that open a dongle other than gs_usb
    then refuse with it themselves."""
    p = can_tools_pin.pin()
    out = {"repo": p["repo"], "commit": p["commit"]}
    try:
        clone = can_tools_pin.clone()
        out["folder"] = can_tools_pin.verify(can_tools_pin.folder(p), p, clone)
        out["clone"] = clone
    except can_tools_pin.CanToolsPinError as e:
        out["error"] = str(e)
    return out


# ---- check builders -----------------------------------------------------
class _Build:
    """The check builders and shared pieces the three suites use."""

    def __init__(self, settings):
        self.settings = settings
        self.code = settings["interposer_code"]
        # madhouse gets the pinned canre this host checked, beside the code
        # (canre_pin.py: CANRE_DIR names it in a copy with no git, and the
        # same checks run there before any import).
        canre = canre_pin.folder()
        canre_pin.verify(canre)
        self.ship = {"code": self.code, "canre": canre}
        self.mad_env = {"INTERPOSER_CODE": "{work}/code",
                        "CANRE_DIR": "{work}/canre",
                        "L3_STIMULUS": "{madhouse_artifacts}/l3_stimulus.txt"}

    def mpy(self, cid, argv, **kw):
        kw.setdefault("judge", script_judge)
        kw.setdefault("cwd", "{work}/code")
        kw.setdefault("env", dict(self.mad_env))
        return Check(cid, argv, python=True, **kw)

    def mbash(self, cid, argv, **kw):
        kw.setdefault("judge", script_judge)
        kw.setdefault("cwd", "{work}/code")
        kw.setdefault("env", dict(self.mad_env))
        return Check(cid, argv, **kw)

    def mpio(self, cid, argv, **kw):
        """A madhouse check that runs PlatformIO."""
        kw["env"] = dict(self.mad_env, PATH=MADHOUSE_PIO_PATH)
        return self.mpy(cid, argv, **kw)

    def lpy(self, cid, argv, **kw):
        kw.setdefault("judge", script_judge)
        kw.setdefault("cls", "build")    # idle priority beside a wall-clock test
        kw.setdefault("cwd", "{interposer_code}")
        return Check(cid, argv, python=True, **kw)

    def bpy(self, cid, script, args, **kw):
        # --out is a folder for these two drivers and a file for the rest
        if os.path.basename(script) not in ("bench_powerup_round.py",
                                            "bench_usb_host.py"):
            args = [("{outdir}/%s.txt" % cid) if a == "{outdir}" else a
                    for a in args]
        kw.setdefault("judge", script_judge)
        kw.setdefault("cls", "wallclock")
        kw.setdefault("cwd", "{interposer_code}")
        kw.setdefault("timeout_s", 3600)
        return Check(cid, ["{interposer_code}/bench_drivers/%s" % script] + args,
                     python=True, **kw)

    def code_info(self, images=None):
        """What the run tested. For the emulated and bench suites the
        identity is the product digest over product_files(), which the core
        adds (user 2026-10-20); the commit and dirty state are the harness
        identity, recorded and not gated: every script a check runs is in
        the tree since port step 0 (2026-10-26), so the artifacts digest
        of the scripts in reverse-it is no longer recorded. The
        harness suite gives no product files, so its identity stays the
        commit of a clean tree."""
        code, settings = self.code, self.settings

        def info(ctx):
            import subprocess
            repo = os.path.dirname(code)
            head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                                   capture_output=True, text=True).stdout.strip()
            out = {"repo": repo, "commit": head, "dirty": bool(dirty),
                   "dirty_files": dirty.splitlines()[:20],
                   "code_hash": None}
            if images:
                if settings.get("interposer_builds") is not None:
                    # an acceptance run's own builds (ACCEPT-SPEC A9)
                    out["interposer_builds"] = {
                        v: {"build": b["build"], "elf_sha256": b["elf_sha256"]}
                        for v, b in sorted(settings["interposer_builds"].items())}
                else:
                    out["images_src"] = settings["images_src"]
                out["images"] = {k: v.note for k, v in images.items()}
                # gen-inhibit's tree the WiCAN's hooks run from (read-only)
                gi = settings.get("gi_tools")
                if gi:
                    out["gi_tools"] = {
                        "commit": subprocess.run(
                            ["git", "-C", gi, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip(),
                        "dirty": bool(subprocess.run(
                            ["git", "-C", gi, "status", "--porcelain"],
                            capture_output=True, text=True).stdout.strip())}
                out["can_tools"] = can_tools_info()
            return out
        return info


# ---- emulated -----------------------------------------------------------
def emulated(settings):
    """No hardware, no lock: builds, host suites, the 1x sims, the capture
    trace rebuild."""
    b = _Build(settings)
    # RETIRED at port step 2a (Part B): the PlatformIO build and its
    # manifest are gone (platformio.ini, manifest.py and its two tests,
    # build_check's environments), and build_check / digest_recheck_check
    # retire with them (reviewer, 2026-11-03, Q3). Listed, not counted
    # (2a acceptance (c)); the IDF builds are checked by acceptance (h).
    m_build = Group("m-build", checks=[
        Check(cid, ["-"], skip=RETIRED_2A) for cid in
        ("build-check", "manifest-test", "manifest-offline-test")])
    m_host = Group("m-host", host="madhouse", ship=b.ship, checks=[
        b.mbash("host-bridge", ["bash", "fw_harness/host_bridge/run.sh"],
                cls="build", timeout_s=3600, judge=host_bridge_judge),
        b.mbash("host-unit", ["bash", "host_unit/run.sh"],
                cls="build", timeout_s=1800),
        b.mbash("host-diff", ["bash", "fw_harness/host_diff/diff_all.sh"],
                cls="build", timeout_s=7200),
        b.mpy("check-port", ["fw_harness/host_diff/check_port.py"]),
        b.mpy("regress-all", ["regress.py", "--all"], cls="build", timeout_s=7200),
    ] + [b.mpy(t[:-3].replace("_", "-"), [t], cls="build", timeout_s=3600)
         for t in LOGIC_PY_TESTS])
    # The sims are wall-clock: the core runs one at a time per host and
    # throttles every build there meanwhile. They come last in the lane.
    m_sims = Group("m-sims", host="madhouse", ship=b.ship, checks=[
        b.mpy("sim." + s, ["run_scenario.py", s, "--time-scale", "1",
                           "--keep-traces", "--outdir", "{outdir}"],
              cls="wallclock", timeout_s=4 * 3600, judge=scenario_judge)
        for s in SIM_SCENARIOS])
    l_emulated = Group("l-emulated", checks=[
        b.lpy(cid, argv, timeout_s=3600) for cid, argv in EMULATED_LOCAL])
    return Suite(
        name="charge-interposer-emulated", role=ROLE,
        groups=[m_build, m_host, m_sims, l_emulated],
        known_fails=EMULATED_KNOWN_FAILS, code_info=b.code_info(),
        product_files=product_files)


# ---- bench --------------------------------------------------------------
def bench(settings):
    """Hardware only, the lock from start to finish; requires a matched
    emulated run on the same code."""
    b = _Build(settings)
    bpy = b.bpy
    board_args = ["--external-interposer", "--serial-port", "{serial_port}",
                  "--vehicle-transport", "kvaser", "--charger-transport",
                  "gs_usb", "--diag-witness", "{pcan_channel}",
                  "--outdir", "{outdir}"]

    # In image order: bridge (standing), witness, truck, c12_bench,
    # vport_bench; then the core restores the bridge image.
    b_bridge = Group("b-bridge", needs_lock=True, image="bridge", checks=[
        bpy("powerup-round-ack", "bench_powerup_round.py",
            ["--port", "{serial_port}", "--mode", "ack", "--boots", "5",
             "--wican-state", "{wican_state}", "--out", "{outdir}"]),
        bpy("powerup-round-noack", "bench_powerup_round.py",
            ["--port", "{serial_port}", "--mode", "noack", "--boots", "3",
             "--wican-state", "{wican_state}", "--out", "{outdir}"]),
        bpy("powerup-round-com3", "bench_powerup_round.py",
            ["--port", "{serial_port}", "--mode", "com3",
             "--wican-state", "{wican_state}", "--out", "{outdir}"],
            judge=recorded_judge),
        bpy("charger-silent", "bench_charger_silent.py",
            ["--port", "{serial_port}", "--out", "{outdir}"]),
        Check("charger-silent-unplugged", ["-"], skip=(
            "spec 2.2, the form with the Innomaker physically unplugged: needs "
            "hands (no-hands sheet, DEFERRED until the user is back)")),
        bpy("switch-pending", "bench_switch_pending.py",
            ["--port", "{serial_port}", "--out", "{outdir}"]),
        bpy("switch-errors", "bench_switch_errors.py",
            ["--port", "{serial_port}", "--out", "{outdir}"]),
        bpy("rx-order", "bench_rx_order.py",
            ["--port", "{serial_port}", "--bursts", "2000", "--out", "{outdir}"]),
        bpy("mcp-reset", "bench_mcp_reset.py",
            ["run", "--port", "{serial_port}", "--out", "{outdir}"]),
        bpy("mcp-reset-canfail", "bench_mcp_reset.py",
            ["canfail", "--port", "{serial_port}", "--out", "{outdir}"]),
        # moved from reverse-it's runs/step4_nohands_20261014T1730/; the
        # serial log now goes to the check's own folder
        bpy("reset-check", "reset_check.py",
            ["--serial-log", "{outdir}/reset_check.serial.log"]),
        bpy("keepalive-probe", "keepalive_probe.py", [], judge=recorded_judge),
        bpy("usb-host-baseline-ack", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "baseline-boots", "--ack",
             "--wican-state", "{wican_state}", "--out", "{outdir}"]),
        # The dependent cases read their baseline from the folder of the
        # case that wrote it (a25e: each case has its own folder, and
        # close-ack / bridge-cycles found none; test_usb_baselines).
        # Stop lengths: 600 s for the bridge cycles, as the 2026-10-14 runs
        # that met the premise (reverse-it
        # runs/bench_usbhost_control_20261014T0528/CODE.txt); 720 s for the
        # not-reading boots (reviewer ruling 2026-10-22: the host side held
        # 12.6 kB on 2026-10-14 and 98.6 kB in 700f, so no fitted stop; 700f
        # noread-ack VOID at 480 s, about 539 s needed at its rate;
        # runs/noread_rate_compare_20261022.txt; test_usb_stops).
        bpy("usb-host-noread-ack", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "noread-boots", "--ack",
             "--stop-s", "720",
             "--wican-state", "{wican_state}", "--out", "{outdir}",
             "--baseline-dir", "{outdir}/../usb-host-baseline-ack"]),
        bpy("usb-host-close-ack", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "close-boots", "--ack",
             "--wican-state", "{wican_state}", "--out", "{outdir}",
             "--baseline-dir", "{outdir}/../usb-host-baseline-ack"]),
        bpy("usb-host-baseline-noack", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "baseline-boots", "--noack",
             "--wican-state", "{wican_state}", "--out", "{outdir}"]),
        bpy("usb-host-noread-noack", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "noread-boots", "--noack",
             "--stop-s", "720",
             "--wican-state", "{wican_state}", "--out", "{outdir}",
             "--baseline-dir", "{outdir}/../usb-host-baseline-noack"]),
        bpy("usb-host-bridge-absent", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "bridge", "--com3", "absent",
             "--wican-state", "{wican_state}", "--out", "{outdir}"]),
        bpy("usb-host-bridge-cycles", "bench_usb_host.py",
            ["--port", "{serial_port}", "--case", "bridge", "--com3", "cycles",
             "--stop-s", "600", "--wican-state", "{wican_state}",
             "--out", "{outdir}",
             "--baseline-dir", "{outdir}/../usb-host-bridge-absent"],
            timeout_s=3 * 3600),
        Check("brownout-cold-boot", ["-"], skip=(
            "spec 10 hardware mechanics, 'reset and brownout': a power cycle "
            "or brownout needs hands (no-hands sheet, DEFERRED)")),
    ] + [Check("board." + s, ["{interposer_code}/run_scenario.py", s] + board_args,
               python=True, cls="wallclock", cwd="{interposer_code}",
               timeout_s=4 * 3600, judge=scenario_judge)
         for s in BOARD_SCENARIOS] + [
        Check("board." + s, ["-"], skip=(
            "not runnable on a board as written (plan Step 4 run sheet, "
            "item 4: the scenario disables or bypasses the core in the sim)"))
        for s in NOT_ON_BOARD])

    b_witness = Group("b-witness", needs_lock=True, image="witness", checks=[
        bpy("topology", "bench_topology.py", [], judge=recorded_judge),
        bpy("hold12", "bench_hold12.py",
            ["--port", "{serial_port}", "--out", "{outdir}"]),
        bpy("switch-loss", "bench_switch_loss.py",
            ["--port", "{serial_port}", "--out", "{outdir}"]),
        bpy("innomaker-clock", "bench_innomaker_clock.py",
            ["--out", "{outdir}", "clock", "--seconds", "120", "--rate", "100"],
            judge=recorded_judge),
        bpy("innomaker-echo", "bench_innomaker_clock.py",
            ["--out", "{outdir}", "echo"], judge=recorded_judge),
        bpy("innomaker-rate-runs-idle", "bench_innomaker_clock.py",
            ["--out", "{outdir}", "rate-runs", "--between", "idle:60"]),
        bpy("innomaker-rate-runs-reopen", "bench_innomaker_clock.py",
            ["--out", "{outdir}", "rate-runs", "--between", "reopen"],
            skip=("plan Step 4 no-hands sheet item 1 (2026-10-14): reopening "
                  "the Innomaker in the same process fails with libusb "
                  "'Access denied', a harness defect, not fixed (a25e and "
                  "4cdb failed the same way); reviewer 2026-10-22: a skip "
                  "until it reopens in a fresh process")),
        Check("innomaker-replug", ["-"], skip=(
            "plan Step 4 no-hands sheet item 1: the Innomaker replug remedy "
            "needs hands (DEFERRED)")),
        Check("sameid-arm-12e2", ["-"], skip=(
            "spec 12 entry 2, the same-identifier arm: BLOCKED (reviewer "
            "2026-10-14: F items 27, 28, 30 with the user, and the published "
            "deferral count)")),
    ])
    b_truck = Group("b-truck", needs_lock=True, image="truck", checks=[
        # --pcan is a flag: the script opens the PCAN itself (a25e passed
        # it a value and the script refused the argument line).
        bpy("flight-silence", "bench_flight_silence.py",
            ["--port", "{serial_port}", "--pcan", "--out", "{outdir}"]),
        bpy("hold12-flight", "bench_hold12.py",
            ["--port", "{serial_port}", "--flight", "--out", "{outdir}"]),
    ])
    b_c12 = Group("b-c12", needs_lock=True, image="c12_bench", checks=[
        bpy("c12-all", "bench_c12.py",
            ["--port", "{serial_port}", "--step", "all", "--elf",
             "{images_dir}/idf/c12_bench/c12_bench.elf", "--out", "{outdir}"],
            judge=c12_judge),
        bpy("c9-noack", "bench_c12.py",
            ["--port", "{serial_port}", "--step", "C9",
             "--charger-segment-alone", "--elf",
             "{images_dir}/idf/c12_bench/c12_bench.elf", "--out", "{outdir}"],
            judge=recorded_judge),
    ])
    def vport(cid, st, extra=()):
        return bpy(cid, "bench_vport.py",
                   ["--port", "{serial_port}", "--elf",
                    "{images_dir}/idf/vport_bench/vport_bench.elf", "--steps",
                    st] + list(extra) + ["--out", "{outdir}"],
                   judge=recorded_judge if st in ("bitrate", "flood",
                                                  "dominant", "recstart")
                   else script_judge)

    # vport-listen's two wrong-rate arms are a KNOWN FAIL (reviewer,
    # 2026-10-21), so they run as their own check and the rest of the
    # listen step stays judged.
    b_vport = Group("b-vport", needs_lock=True, image="vport_bench", checks=[
        vport("vport-listen", "listen",
              ["--listen-arms", "no-ack,idle,flood-7F4,flood-649,stall"]),
        vport("vport-listen-wrong-rate", "listen",
              ["--listen-arms", "wrong-250k,wrong-1m"])] + [
        vport("vport-" + st, st)
        for st in ("bitrate", "flood", "stall", "dominant", "recstart")])

    truck_only = Group("truck-only", checks=[
        Check("spec10-item%d" % i, ["-"], skip=(
            "spec 10 item %d: only the truck can answer" % i))
        for i in range(1, 10)] + [
        Check("spec2.1-offline-board", ["-"], skip=(
            "spec 2.1: what the truck does with the board offline needs the "
            "truck (spec 10 item 6)")),
    ])

    images = {
        "bridge": _interposer_image("bridge", settings),
        "witness": _interposer_image("witness", settings),
        "truck": _interposer_image("truck", settings),
        "c12_bench": _idf_image("c12_bench", settings),
        "vport_bench": _idf_image("vport_bench", settings),
    }
    # The WiCAN is a board of every bench group (bench_wican.py), so the
    # runner identifies it, runs its start and release reboots and reads it
    # at every release and free_paused (shared/bench SPEC.md section 6).
    wican = bench_wican.make(settings)
    images[bench_wican.IMAGE] = wican["image"]
    for g in (b_bridge, b_witness, b_truck, b_c12, b_vport):
        g.image = [g.image, bench_wican.IMAGE]

    def identify(image, ctx, logdir):
        if image.board == bench_wican.BOARD:
            return wican["identify"](image, ctx, logdir)
        return bench_identify.identify(image, ctx, logdir, settings)

    return Suite(
        name="charge-interposer-bench", role=ROLE,
        groups=[b_bridge, b_witness, b_truck, b_c12, b_vport, truck_only],
        images=images,
        standing_images={"x2": "bridge", bench_wican.BOARD: bench_wican.IMAGE},
        flashers={bench_wican.FLASHER: wican["flasher"]},
        on_start=wican["on_start"], on_end=wican["on_end"],
        known_fails=BENCH_KNOWN_FAILS, identify=identify,
        code_info=b.code_info(images), requires="charge-interposer-emulated",
        product_files=product_files)


# ---- harness ------------------------------------------------------------
def harness(settings):
    """The tester's checks of the checks: harness tests, mutants, can-fails
    and the bench drivers' self-tests. Run when the harness changes."""
    b = _Build(settings)
    m_harness = Group("m-harness", host="madhouse", ship=b.ship, checks=[
        # retired with build_check / digest_recheck_check (emulated m-build)
        Check(cid, ["-"], skip=RETIRED_2A) for cid in (
            "build-check-links-test", "build-check-manifest-test",
            "build-check-schema-test", "build-check-schema-test-canfail",
            "build-check-in-clone-test", "build-check-in-clone-test-canfail",
            "digest-recheck-check", "digest-recheck-check-canfail")
    ] + [b.mpy(t[:-3].replace("_", "-"), [t], cls="build", timeout_s=3600)
         for t in HARNESS_PY_TESTS] + [
        b.mpy(t[:-3].replace("_", "-") + "-canfail", [t, "--can-fail"],
              cls="build", timeout_s=3600)
        for t in HARNESS_PY_CANFAILS])
    def mutant(cid, argv):
        kw = dict(cls="build", timeout_s=7200,
                  judge=globals()[MUTANT_JUDGES.get(cid, "script_judge")])
        if argv[0] == "@python":
            return b.mpy(cid, argv[1:], **kw)
        return b.mbash(cid, argv, **kw)
    m_mut = Group("m-mutants", host="madhouse", ship=b.ship, checks=[
        mutant(cid, argv) for cid, argv in MADHOUSE_MUTANTS])
    l_harness = Group("l-harness", checks=[
        b.lpy("st." + s.replace("_", "-"),
              ["{interposer_code}/bench_drivers/%s.py" % s, "--self-test"],
              timeout_s=1800)
        for s in SELF_TESTS] + [
        b.lpy("cf." + s.replace("_", "-"),
              ["{interposer_code}/bench_drivers/%s.py" % s, "--can-fail"],
              timeout_s=3600)
        for s in CAN_FAILS] + [b.lpy(cid, argv, timeout_s=3600)
                                for cid, argv in HARNESS_LOCAL] + [
        b.lpy("sb.%s%s" % (t[:-3].replace("_", "-"), sfx),
              [os.path.join(SHARED_BENCH, "tests", t).replace(os.sep, "/")]
              + arg,
              timeout_s=3600)
        for t in SHARED_BENCH_TESTS
        for sfx, arg in (("", []), ("-canfail", ["--can-fail"]))] + [
        Check("cutover-verify-test", ["-"], skip=(
            "historical: the one-time 2026-10-08 cutover, verified then "
            "(reviewer 2026-10-18); cutover_verify_test.py and its "
            "--guard-test retired, nothing left to guard (reviewer "
            "2026-10-31)"))])
    return Suite(
        name="charge-interposer-harness", role=ROLE,
        groups=[m_harness, m_mut, l_harness], code_info=b.code_info())
