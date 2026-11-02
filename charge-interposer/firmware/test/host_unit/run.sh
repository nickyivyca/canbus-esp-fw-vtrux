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
#
# The binaries are built in this run's own folder under the tree's scratch/,
# never the system temp folder, removed when the script exits
# (charge-interposer/scratch_run.sh; tester, 2026-11-02).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../../src"
. "$HERE/../../../scratch_run.sh" || exit 2
scratch_run host-unit || exit 2
fail=0
for t in "$HERE"/test_*.cpp; do
  name="$(basename "$t" .cpp)"
  bin="$SCRATCH_RUN/$name"
  if ! g++ -std=c++11 -O2 -Wall -Wextra -I "$SRC" -o "$bin" "$t"; then
    echo "$name DID NOT BUILD"
    fail=1
    continue
  fi
  if "$bin"; then :; else fail=1; fi
  rm -f "$bin"
done
exit $fail
