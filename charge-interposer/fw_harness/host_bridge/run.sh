#!/usr/bin/env bash
# Build and run the spec 9.1 L2 host tests.
#
# Separate from ../../host_unit/run.sh because these need the
# fakes compiled alongside them, and because L2 is where the fakes live --
# host_unit is for firmware logic pure enough to need no peripheral at all.
#
# On Windows run under WSL:
#   wsl bash "/mnt/c/.../charge-interposer/fw_harness/host_bridge/run.sh"
#
# THE VEHICLE PORT IS THE REAL ONE, ON THE ONE HOST FAKE (port step 2b,
# spec 2.4; twai_port T10; tester, 2026-11-08, the charge-interposer
# reviewer's rulings for 2b). Since 2b the firmware's vehicle port is the
# shared vtrux/twai_port behind vtrux/can_port, so the tests compile them as
# they ship: twai_port.c with the S3's register file,
# twai_port_ll_unsupported.c, and can_port.c, as C (gcc -std=c11), on the
# shared TWAI driver fake (twai_fake.c, as C, with the interposer's C++
# interface cpp/twai_fake.cpp). This folder's own fake and driver/twai.h,
# and the TWAI_FAKE=own|shared option, are retired. The shared folders
# (found by l2_build.sh's hb_shared, which the harness checks that build
# test_l2_bridge from a copy use too) are those of ONE shared/main
# checkout, at the tag
# charge-interposer/firmware/shared_pins.json pins for shared/twai_port
# (shared/can_port is pinned at the same tag, and
# ../../shared_pin_test.py checks both against main/idf_component.yml):
#   TWAI_FAKE_DIR  the fake's folder, <checkout>/shared/twai_port/test, if
#                  set (the madhouse lane ships the pinned folders and sets
#                  it); twai_port is its parent, can_port ../../can_port
#   otherwise      `python3 ../../shared_pin.py where shared/twai_port`,
#                  the pinned read-only checkout ../shared-<tag>/
# Our own host stand-ins come first on the include path (board.h, with
# board_fake.cpp's answers from shared/boards SPEC section 3; esp_log.h;
# idf_host.cpp: the log, error-check and tick calls twai_port makes), then
# firmware/main, then the fake's include/, host/ and cpp/, then
# twai_port's and can_port's include/. The fake's host_platform.c is not
# built: its board answers are not the X2's.
# run.sh --can-fail is the shared fake's can-fail
# (../../harness_checks/shared_fake_canfail.py).
#
# WHERE IT WORKS: one folder per run, W, under the tree's own scratch/, at
# scratch/cih/host-bridge/run_XXXXXX, never the system
# temp folder (AGENTS.md "Files and folders"; the charge-interposer
# reviewer, 2026-11-02; cih/, scratch_dir.py's short root, since
# 2026-11-05). Every object and test binary is built there. It is removed
# when run.sh ends, pass, fail or refusal, with the two tool folders above
# it if that leaves them empty; a folder it cannot remove is printed with
# its path. The run folder is made with up to three tries, a moment apart,
# since another run's cleanup can remove the tool folders between making
# them and making the run folder in them.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# The firmware's sources: the IDF project's main/ component (port step 2a
# moved them there from src/). main.cpp's board_ports.h includes "board.h",
# which this folder's host stand-in answers (board.h, board_fake.cpp).
# host_bridge is charge-interposer/fw_harness/host_bridge (port step 2a).
SRC="$HERE/../../firmware/main"
case "${1:-}" in
  "") ;;
  --can-fail) exec python3 "$HERE/../../harness_checks/shared_fake_canfail.py" ;;
  *) echo "usage: run.sh [--can-fail]" >&2; exit 2 ;;
esac
# The shared folders (l2_build.sh, which the harness checks that build
# test_l2_bridge from a copy share with this script): refused before
# anything is made if they are not there.
. "$HERE/l2_build.sh"
hb_shared || exit 2
SH="$HB_SH"
# the tree's root
ROOT="$(cd "$HERE/../../.." && pwd)"
WT="$ROOT/scratch/cih"
# Another run's cleanup (below) removes host-bridge/ and its parent once
# they are empty, and that can fall between this run's mkdir -p and its
# mktemp. So both are made again, three tries in all, as
# charge-interposer/scratch_dir.py's make() does (the charge-interposer
# reviewer, 2026-11-02), with the same waits before the second and third
# tries, 0.1 s and 0.4 s: on Windows a folder removed while another process
# holds it stays delete-pending for a moment, and making one in it fails
# until it is gone. The first two tries print nothing; the last one prints
# its reason.
W=""
for try in 1 2; do
  [ "$try" = 1 ] || sleep 0.1
  mkdir -p "$WT/host-bridge" 2>/dev/null &&
    W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" && break
  W=""
done
if [ -z "$W" ]; then
  sleep 0.4
  mkdir -p "$WT/host-bridge" || exit 2
  W="$(mktemp -d "$WT/host-bridge/run_XXXXXX")" || exit 2
fi
cleanup() {
  rm -rf "$W"
  if [ -e "$W" ]; then echo "run.sh: could not remove $W" >&2; fi
  rmdir "$WT/host-bridge" "$WT" 2>/dev/null || true
}
trap cleanup EXIT
# B: the folder the tests and our stand-ins are built from
B="$HERE"
INC=(-I "$B" -I "$SRC" "${HB_INC_SHARED[@]}")
# test_l2_bridge replays l3_stimulus.txt, which stays in SeaDrive; its
# relative candidates in l3_replay.cpp no longer reach it from this repo,
# so the runner hands it over through L3_STIMULUS (an explicit setting
# still wins). REVERSE_IT_DIR: see charge-interposer/paths.py.
if [ -z "${L3_STIMULUS:-}" ]; then
  : "${REVERSE_IT_DIR:?REVERSE_IT_DIR is not set -- see charge-interposer/paths.py}"
  export L3_STIMULUS="$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/l3_stimulus.txt"
fi
fail=0
# spec 9.1 pinned stimulus: the L3 file must match the pin HELD IN THE REPO
# (charge-interposer/capture_pins.py). l3_replay.cpp's own check compares the
# body with the hash in the file's header, which a regenerated file carries
# along with it; this one does not move with the file.
if ! python3 "$HERE/../../capture_pins.py" --file "$L3_STIMULUS"; then
  fail=1
fi
# THE C SOURCES, compiled once per run as C, as they ship: the fake (every
# test links it, as every test linked our own fake before 2b), and the
# vehicle port's two components (the tests that build the bridge link
# them). A source that does not compile fails the run, and the tests that
# need it then do not build.
hb_c_objs "$W" "$B" "$SRC" || fail=1
FAKE_OBJS=("$W/twai_fake.o")
PORT_OBJS=("$W/twai_port.o" "$W/twai_port_ll.o" "$W/can_port.o")
# The vehicle port's firmware side (port step 2b): vehicle_port.cpp sets
# twai_port up and registers it with can_port, port_view.cpp is how the
# bridge reaches a port by its index, and charger_port.cpp registers the
# MCP2515 driver with can_port (main/CMakeLists.txt).
PORTS=("$SRC/port_mcp2515.cpp" "$SRC/vehicle_port.cpp" "$SRC/port_view.cpp"
       "$SRC/charger_port.cpp")
# THE TEST LIST, printed once before any build (tester, 2026-11-08; the
# charge-interposer reviewer's follow-up (B) after port step 2a): every
# test_*.cpp of the folder the tests are built from, in glob order, on one
# line, "host_bridge tests (N): test_a test_b ...". The loop below runs
# exactly this list, and after each test prints its closing line,
# "run.sh: <test> exit <rc>" (rc 0 only if the test, or every case of a
# case-based one, exited 0), or "<test> DID NOT BUILD". So the judge
# (bench_suite.host_bridge_judge) reads from the run itself which tests it
# should find, and whether each one ended, even one that prints no line of
# its own name when it fails. No test at all is a failure (rc 1).
tests=()
for t in "$B"/test_*.cpp; do
  [ -e "$t" ] && tests+=("$(basename "$t" .cpp)")
done
echo "host_bridge tests (${#tests[@]}): ${tests[*]+${tests[*]}}"
[ "${#tests[@]}" -gt 0 ] || fail=1
for name in ${tests[@]+"${tests[@]}"}; do
  t="$B/$name.cpp"
  bin="$W/$name"
  # Glob our stand-ins rather than list them: a hand-kept source list going
  # stale is how the gen-inhibit session built against old headers three
  # times without noticing.
  srcs=("$t" "$SH/cpp/twai_fake.cpp")
  objs=(${FAKE_OBJS[@]+"${FAKE_OBJS[@]}"})
  for f in "$B"/*.cpp; do
    [ "$f" = "$t" ] && continue
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  # Firmware sources are listed per test: most tests want the chip model
  # alone, and only the driver tests want the driver itself compiled in.
  defs=()
  case "$name" in
    test_tx_failed|test_mcp2515_busoff|test_tx_throughput|test_sameid_hold) srcs+=("$SRC/port_mcp2515.cpp") ;;
    # The CanPort contract, for both ports (since port step 2b the vehicle
    # port too: its PortView on twai_port and can_port).
    test_canport_contract)
      srcs+=("$SRC/port_mcp2515.cpp" "$SRC/vehicle_port.cpp" "$SRC/port_view.cpp")
      objs+=("${PORT_OBJS[@]}")
      ;;
    # The stall trace is the only build that defines INTP_TX_TRACE, which
    # compiles the per-pass instrumentation into the driver. No firmware
    # variant defines it, so no firmware image carries it.
    test_tx_stall_trace)
      srcs+=("$SRC/port_mcp2515.cpp")
      defs+=(-DINTP_TX_TRACE=1)
      ;;
    # Likewise the only host build that defines INTP_ORDER_WITNESS. One
    # firmware variant defines it, witness (the bench's order-witness
    # image); the project's build-switch rows (shared/tools R20,
    # firmware/vtrux_checks.json) refuse it in a release image.
    test_order_witness)
      srcs+=("$SRC/port_mcp2515.cpp")
      defs+=(-DINTP_ORDER_WITNESS=1)
      ;;
    # Spec 8.3's event-line form. The test #includes main.cpp itself (to
    # reach its static reportEvents() and core without a src change), so
    # main.cpp is NOT listed; built WITH serial, into the capturing Serial
    # of Arduino.h, which only HOST_SERIAL_CAPTURE compiles in.
    test_event_layout)
      srcs+=("${PORTS[@]}" "$SRC/machine.cpp")
      objs+=("${PORT_OBJS[@]}")
      defs+=(-DHOST_SERIAL_CAPTURE=1)
      ;;
    # L2 is the whole bridge: main.cpp itself, both ports and the core.
    # Built with INTP_NO_SERIAL so it is the TRUCK image's code path --
    # the one that actually goes in the vehicle, and the one whose
    # `intp_serial` bit reads 0 on the wire.
    test_l2_bridge)
      for f in "${HB_FW_L2[@]}"; do srcs+=("$SRC/$f"); done
      objs+=("${PORT_OBJS[@]}")
      defs+=(-DINTP_NO_SERIAL=1)
      ;;
  esac
  # A test that needs an object that did not build does not build either.
  missing=""
  for o in ${objs[@]+"${objs[@]}"}; do
    [ -f "$o" ] || missing="$missing $(basename "$o")"
  done
  # ${defs[@]+...} so an EMPTY array is not an unbound-variable error
  # under `set -u`, which is how most of these tests build.
  if [ -n "$missing" ] ||
     ! g++ -std=c++11 -O2 -Wall -Wextra "${INC[@]}" \
       ${defs[@]+"${defs[@]}"} -o "$bin" "${srcs[@]}" ${objs[@]+"${objs[@]}"}; then
    echo "$name DID NOT BUILD${missing:+ (no$missing)}"
    fail=1
    continue
  fi
  # ONE PROCESS PER CASE where a test declares cases.
  #
  # A test that bundles independent cases into one process shares its
  # statics between them -- and `main.cpp`'s state IS file statics, with
  # no reset. Rather than add a reset hook to flight code for the
  # harness's benefit, a test with cases prints its case names when run
  # with no argument and is then invoked once per name, so each starts
  # from a genuinely fresh process. The failed-start case cannot exist
  # any other way: it needs a boot where begin() fails.
  # Which tests are case-based is declared HERE, not sniffed. Asking
  # every binary to list its cases by running it with no arguments would
  # RUN the ones that are not case-based -- they do their work and print
  # it, and those lines would then come back as case names and be run
  # again as arguments. Explicit is the only safe form.
  case "$name" in
    test_l2_bridge) cased=1 ;;
    *) cased=0 ;;
  esac

  rc=0
  if [ "$cased" -eq 1 ]; then
    for c in $("$bin"); do
      "$bin" "$c" || rc=1
    done
  else
    "$bin" || rc=$?
  fi
  [ "$rc" -eq 0 ] || fail=1
  # on a line of its own, even after a test that died mid-line
  printf '\nrun.sh: %s exit %s\n' "$name" "$rc"
  rm -f "$bin"
done
exit $fail
