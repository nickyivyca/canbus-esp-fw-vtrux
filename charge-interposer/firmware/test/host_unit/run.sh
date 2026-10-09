#!/usr/bin/env bash
# Build and run every host C++ unit test in this folder.
#
# These are the pieces of the firmware that are pure enough to compile on a
# PC without faking a peripheral -- today just spec 8.2's bridge_ok interval
# rule. The bridge loop itself needs the fake ports of spec 9.1 L2 and will
# live beside this, not in it.
#
# On Windows run under WSL:
#   wsl bash "/mnt/c/.../firmware/test/host_unit/run.sh"
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../../src"
fail=0
for t in "$HERE"/test_*.cpp; do
  name="$(basename "$t" .cpp)"
  bin="${TMPDIR:-/tmp}/${name}_$$"
  if ! g++ -std=c++11 -O2 -Wall -Wextra -I "$SRC" -o "$bin" "$t"; then
    echo "$name DID NOT BUILD"
    fail=1
    continue
  fi
  if "$bin"; then :; else fail=1; fi
  rm -f "$bin"
done
exit $fail
