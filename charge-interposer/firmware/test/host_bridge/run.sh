#!/usr/bin/env bash
# Build and run the spec 9.1 L2 host tests.
#
# Separate from ../host_unit/run.sh because these need the fakes compiled
# alongside them, and because L2 is where the fakes live -- host_unit is for
# firmware logic pure enough to need no peripheral at all.
#
# On Windows run under WSL:
#   wsl bash "/mnt/c/.../firmware/test/host_bridge/run.sh"
#
# WHICH TWAI DRIVER FAKE the tests build against, TWAI_FAKE (tester,
# 2026-11-02, the reviewer's order: an option of committed harness, so that
# port step 1b's acceptance E can be re-run by the acceptance runner):
#   own     the default, as before the option: this folder's twai_fake.h /
#           .cpp and driver/twai.h.
#   shared  the shared fake (shared/twai_port/test, twai_port T10), from the
#           folder TWAI_FAKE_DIR names, built exactly as acceptance E built
#           it (2026-10-31): a copy of this folder, at the same depth beside
#           a copy of src/, without our twai_fake.h,
#           twai_fake.cpp and driver/twai.h (a quoted #include looks in the
#           including file's own folder first, so they would win over any
#           -I), every test given the shared twai_fake.c and
#           cpp/twai_fake.cpp, and -I <fake>/include -I <fake>/host
#           -I <fake>/cpp after this folder and src/. Nothing else changes,
#           and nothing extra is printed: the two transcripts are compared
#           line for line.
# run.sh --can-fail is the shared fake's can-fail
# (../../../harness_checks/shared_fake_canfail.py).
#
# WHERE IT WORKS: one folder per run, W, under the tree's own scratch/, at
# scratch/charge-interposer-harness/host-bridge/run_XXXXXX, never the system
# temp folder (AGENTS.md "Files and folders"; the charge-interposer
# reviewer, 2026-11-02). Every test binary is built there, and so is
# TWAI_FAKE=shared's copy. It is removed when run.sh ends, pass, fail or
# refusal, with the two tool folders above it if that leaves them empty; a
# folder it cannot remove is printed with its path. The run folder is made
# with up to three tries, a moment apart, since another run's cleanup can
# remove the tool folders between making them and making the run folder in
# them.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../../src"
case "${1:-}" in
  "") ;;
  --can-fail) exec python3 "$HERE/../../../harness_checks/shared_fake_canfail.py" ;;
  *) echo "usage: run.sh [--can-fail]" >&2; exit 2 ;;
esac
# the tree's root: host_bridge is charge-interposer/firmware/test/host_bridge
ROOT="$(cd "$HERE/../../../.." && pwd)"
WT="$ROOT/scratch/charge-interposer-harness"
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
# B: the folder the tests and our fakes are built from
B="$HERE"
FAKE_SRCS=()
FAKE_INC=()
case "${TWAI_FAKE:-own}" in
  own) ;;
  shared)
    SH="${TWAI_FAKE_DIR:-}"
    if [ -z "$SH" ] || [ ! -f "$SH/twai_fake.c" ] || [ ! -f "$SH/cpp/twai_fake.cpp" ]; then
      echo "TWAI_FAKE=shared needs TWAI_FAKE_DIR: the shared fake's folder" \
           "(shared/twai_port/test), with twai_fake.c and cpp/twai_fake.cpp" >&2
      exit 2
    fi
    # The copy sits at host_bridge's own depth, test/host_bridge under a
    # folder T in this run's W, with a copy of src/ beside test/, so a
    # test's relative #include "../../src/main.cpp" (test_event_layout)
    # reaches main.cpp as it does from host_bridge itself.
    T="$W/shared"
    B="$T/test/host_bridge"
    mkdir -p "$B" "$T/src" || exit 2
    cp -R "$SRC/." "$T/src/" || exit 2
    cp -R "$HERE/." "$B/" || exit 2
    rm "$B/twai_fake.h" "$B/twai_fake.cpp" "$B/driver/twai.h"
    rmdir "$B/driver" 2>/dev/null || true
    FAKE_SRCS=("$SH/twai_fake.c" "$SH/cpp/twai_fake.cpp")
    FAKE_INC=(-I "$SH/include" -I "$SH/host" -I "$SH/cpp")
    ;;
  *)
    echo "TWAI_FAKE=$TWAI_FAKE: own (the default) or shared" >&2
    exit 2
    ;;
esac
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
if ! python3 "$HERE/../../../capture_pins.py" --file "$L3_STIMULUS"; then
  fail=1
fi
for t in "$B"/test_*.cpp; do
  name="$(basename "$t" .cpp)"
  bin="$W/$name"
  # Glob the fakes rather than list them: a hand-kept source list going
  # stale is how the gen-inhibit session built against old headers three
  # times without noticing.
  srcs=("$t" ${FAKE_SRCS[@]+"${FAKE_SRCS[@]}"})
  for f in "$B"/*.cpp; do
    [ "$f" = "$t" ] && continue
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  # Firmware sources are listed per test: most tests want the chip model
  # alone, and only the driver tests want the driver itself compiled in.
  defs=()
  case "$name" in
    test_tx_failed|test_canport_contract|test_mcp2515_busoff|test_tx_throughput|test_sameid_hold) srcs+=("$SRC/port_mcp2515.cpp") ;;
    # The stall trace is the only build that defines INTP_TX_TRACE, which
    # compiles the per-pass instrumentation into the driver. No PlatformIO
    # environment defines it, so no firmware image carries it.
    test_tx_stall_trace)
      srcs+=("$SRC/port_mcp2515.cpp")
      defs+=(-DINTP_TX_TRACE=1)
      ;;
    # Likewise the only host build that defines INTP_ORDER_WITNESS. One
    # PlatformIO environment defines it, [env:esp32-can-x2-witness] (the
    # bench's order-witness image); build_check.py allows it there only
    # and refuses it in any other environment or in [common].
    test_order_witness)
      srcs+=("$SRC/port_mcp2515.cpp")
      defs+=(-DINTP_ORDER_WITNESS=1)
      ;;
    test_twai_port) srcs+=("$SRC/port_twai.cpp") ;;
    # Spec 8.3's event-line form. The test #includes main.cpp itself (to
    # reach its static reportEvents() and core without a src change), so
    # main.cpp is NOT listed; built WITH serial, into the capturing Serial
    # of Arduino.h, which only HOST_SERIAL_CAPTURE compiles in.
    test_event_layout)
      srcs+=("$SRC/port_mcp2515.cpp" "$SRC/port_twai.cpp" "$SRC/machine.cpp")
      defs+=(-DHOST_SERIAL_CAPTURE=1)
      ;;
    # L2 is the whole bridge: main.cpp itself, both ports and the core.
    # Built with INTP_NO_SERIAL so it is the TRUCK image's code path --
    # the one that actually goes in the vehicle, and the one whose
    # `intp_serial` bit reads 0 on the wire.
    test_l2_bridge)
      srcs+=("$SRC/main.cpp" "$SRC/port_mcp2515.cpp" "$SRC/port_twai.cpp"              "$SRC/machine.cpp")
      defs+=(-DINTP_NO_SERIAL=1)
      ;;
  esac
  # ${defs[@]+...} so an EMPTY array is not an unbound-variable error
  # under `set -u`, which is how most of these tests build.
  if ! g++ -std=c++11 -O2 -Wall -Wextra -I "$B" -I "$SRC" ${FAKE_INC[@]+"${FAKE_INC[@]}"} \
       ${defs[@]+"${defs[@]}"} -o "$bin" "${srcs[@]}"; then
    echo "$name DID NOT BUILD"
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

  if [ "$cased" -eq 1 ]; then
    for c in $("$bin"); do
      "$bin" "$c" || fail=1
    done
  else
    "$bin" || fail=1
  fi
  rm -f "$bin"
done
exit $fail
