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
# The runner is built in this call's own folder under the tree's scratch/,
# never the system temp folder, and removed with it when the script exits
# (charge-interposer/scratch_run.sh; tester, 2026-11-02). Until then it was
# cached in one fixed TMPDIR file, hr_l1, shared by every run on the machine
# and rebuilt only when a source was newer than it; a build of host_runner
# and machine.cpp now comes with each call, which run_scenario.py makes once
# per scenario, after its simulators have stopped. That build is a build
# under AGENTS.md, "Builds during wall-clock tests": if a scenario chain runs
# while another wall-clock test is running on the same machine, it comes
# under that section's throttle rule like any other build (reviewer,
# 2026-11-02).
#
#   diff_one.sh <trace>        -> exit 0 identical, 1 differs, 2 cannot run
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$HERE/../.."

if [ $# -ne 1 ]; then echo "usage: diff_one.sh <trace>" >&2; exit 2; fi
TRACE="$1"
[ -f "$TRACE" ] || { echo "no such trace: $TRACE" >&2; exit 2; }

. "$FW/../scratch_run.sh" || exit 2
scratch_run host-diff-one || exit 2
BIN="$SCRATCH_RUN/hr_l1"
g++ -std=c++11 -O2 -I "$FW/src" -o "$BIN" \
    "$HERE/host_runner.cpp" "$FW/src/machine.cpp" || exit 2

PY="${PYTHON:-python3}"
if "$BIN" "$TRACE" | diff -q - <("$PY" "$HERE/regen_golden.py" --stdout "$TRACE") >/dev/null; then
  exit 0
fi
echo "L1 DIVERGENCE in $(basename "$TRACE"): machine.cpp vs machine.py"
"$BIN" "$TRACE" | diff - <("$PY" "$HERE/regen_golden.py" --stdout "$TRACE") | head -10
exit 1
