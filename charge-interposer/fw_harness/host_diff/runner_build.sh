#!/usr/bin/env bash
# How host_runner is built, in one place (diag schema 5; tester, 2026-11-24),
# for diff_all.sh, diff_one.sh and the harness checks that build it from a
# copy of main/ (hold_cadence_gate_mutation.sh, monitor_6h_diff.py). From
# schema 5 machine.cpp packs 0x7F4-0x7F7 through shared/diag's packer and
# the table generated from the DBC (spec 8.2, user 2026-11-23), so a host
# build of machine.cpp also compiles, as C, shared/diag's diag_pack.c from
# the pinned checkout and main/'s intp_diag_table.c, with shared/diag's
# include folder. One list, so no build of the core differs from another
# (l2_build.sh says why).
#
# Sourced:
#   . runner_build.sh
#   hr_build BIN SRC [g++ args]
#       builds host_runner from SRC/machine.cpp (SRC a main/ folder, or a
#       copy of one with its machine.* and intp_diag_table.* files) into
#       BIN, with the two C objects in BIN.obj/. rc 0 built, 1 did not
#       build (the step that failed is printed), 2 refused: no pinned
#       shared/diag (../../shared_pin.py where shared/diag) or a source
#       missing.
#
# Run:
#   bash runner_build.sh BIN SRC [g++ args]     the same, as the exit code.

HR_BUILD_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

hr_build() {
  local bin="$1" src="$2" dg f
  shift 2
  dg="$(python3 "$HR_BUILD_HERE/../../shared_pin.py" where shared/diag)" || {
    echo "refused: no pinned shared/diag (shared_pin.py where shared/diag)" >&2
    return 2
  }
  for f in "$dg/diag_pack.c" "$dg/include/diag_pack.h" "$src/machine.cpp" \
           "$src/intp_diag_table.c" "$src/intp_diag_table.h"; do
    if [ ! -f "$f" ]; then
      echo "refused: $f is missing (a copy of main/ for host_runner holds" \
           "machine.* and intp_diag_table.*)" >&2
      return 2
    fi
  done
  mkdir -p "$bin.obj" || return 1
  rm -f "$bin" "$bin.obj/diag_pack.o" "$bin.obj/intp_diag_table.o"
  if ! gcc -std=c11 -O2 -Wall -Wextra -I "$dg/include" -I "$src" \
       -c -o "$bin.obj/diag_pack.o" "$dg/diag_pack.c"; then
    echo "diag_pack (C, $dg/diag_pack.c) DID NOT BUILD"
    return 1
  fi
  if ! gcc -std=c11 -O2 -Wall -Wextra -I "$dg/include" -I "$src" \
       -c -o "$bin.obj/intp_diag_table.o" "$src/intp_diag_table.c"; then
    echo "intp_diag_table (C, $src/intp_diag_table.c) DID NOT BUILD"
    return 1
  fi
  if ! g++ -std=c++11 -O2 -I "$src" -I "$dg/include" "$@" -o "$bin" \
       "$HR_BUILD_HERE/host_runner.cpp" "$src/machine.cpp" \
       "$bin.obj/diag_pack.o" "$bin.obj/intp_diag_table.o"; then
    echo "host_runner (C++, $src/machine.cpp) DID NOT BUILD"
    return 1
  fi
  [ -x "$bin" ] || return 1
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -u
  if [ "$#" -lt 2 ]; then
    echo "usage: runner_build.sh BIN SRC [g++ args]" >&2
    exit 2
  fi
  hr_build "$@"
  exit $?
fi
