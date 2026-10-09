#!/usr/bin/env bash
# Build and run the spec 9.1 L2 host tests.
#
# Separate from ../host_unit/run.sh because these need the fakes compiled
# alongside them, and because L2 is where the fakes live -- host_unit is for
# firmware logic pure enough to need no peripheral at all.
#
# On Windows run under WSL:
#   wsl bash "/mnt/c/.../firmware/test/host_bridge/run.sh"
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../../src"
fail=0
for t in "$HERE"/test_*.cpp; do
  name="$(basename "$t" .cpp)"
  bin="${TMPDIR:-/tmp}/${name}_$$"
  # Glob the fakes rather than list them: a hand-kept source list going
  # stale is how the gen-inhibit session built against old headers three
  # times without noticing.
  srcs=("$t")
  for f in "$HERE"/*.cpp; do
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
    # Likewise the only build that defines INTP_ORDER_WITNESS. No
    # PlatformIO environment defines it either, and build_check.py
    # asserts the truck image is byte-identical with and without it.
    test_order_witness)
      srcs+=("$SRC/port_mcp2515.cpp")
      defs+=(-DINTP_ORDER_WITNESS=1)
      ;;
    test_twai_port) srcs+=("$SRC/port_twai.cpp") ;;
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
  if ! g++ -std=c++11 -O2 -Wall -Wextra -I "$HERE" -I "$SRC" \
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
