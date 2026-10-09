#!/usr/bin/env bash
# Replay ONE trace through machine.cpp and diff it against machine.py.
#
# Spec 9.1 L1: run_scenario.py records each scenario as a trace and calls
# this on it, so a scenario says something about the code that flies and not
# only about machine.py. There is no golden on disk -- both sides are
# generated now, which is the point: a recorded scenario is not reproducible
# run to run (interposer_sim's clock comes from wall time), so a stored
# golden would be stale by construction.
#
# The binary is cached in TMPDIR and rebuilt only when a source is newer, so
# calling this once per scenario does not rebuild 19 times.
#
#   diff_one.sh <trace>        -> exit 0 identical, 1 differs, 2 cannot run
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$HERE/../.."
BIN="${TMPDIR:-/tmp}/hr_l1"

if [ $# -ne 1 ]; then echo "usage: diff_one.sh <trace>" >&2; exit 2; fi
TRACE="$1"
[ -f "$TRACE" ] || { echo "no such trace: $TRACE" >&2; exit 2; }

need_build=0
if [ ! -x "$BIN" ]; then
  need_build=1
else
  for src in "$HERE/host_runner.cpp" "$FW/src/machine.cpp" "$FW/src/machine.h"; do
    [ "$src" -nt "$BIN" ] && need_build=1
  done
fi
if [ "$need_build" = 1 ]; then
  g++ -std=c++11 -O2 -I "$FW/src" -o "$BIN" \
      "$HERE/host_runner.cpp" "$FW/src/machine.cpp" || exit 2
fi

PY="${PYTHON:-python3}"
if "$BIN" "$TRACE" | diff -q - <("$PY" "$HERE/regen_golden.py" --stdout "$TRACE") >/dev/null; then
  exit 0
fi
echo "L1 DIVERGENCE in $(basename "$TRACE"): machine.cpp vs machine.py"
"$BIN" "$TRACE" | diff - <("$PY" "$HERE/regen_golden.py" --stdout "$TRACE") | head -10
exit 1
