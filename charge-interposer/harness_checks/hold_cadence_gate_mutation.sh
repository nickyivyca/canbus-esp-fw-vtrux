#!/usr/bin/env bash
# Can-fail for the two hold-cadence unit traces (tester, 2026-10-13).
#
# Spec 5.2 at dfc11cbb: the hold steps at most once per 0.28 s. Every
# other trace in the corpus steps on a 100 ms grid, where a 280 ms and a
# 300 ms gate both step at 300 ms, so unit_hold_cadence_298_302 and
# unit_hold_cadence_290 are the only traces that can tell the C++ port's
# gate from the old one. This builds host_runner against a COPY of
# machine.cpp with the gate put back to 300 ms and requires both traces to
# DIFFER from their goldens; the control (unmutated copy) must match. The
# mutation verifies that it applied.
#
# Run:  bash charge-interposer/harness_checks/hold_cadence_gate_mutation.sh
#       (REVERSE_IT_DIR = SeaDrive's reverse-it root, for the fixtures;
#        under WSL pass it with WSLENV=REVERSE_IT_DIR/p)
set -u

# The code is the tree this script is in (moved into git from reverse-it's
# interposer-firmware/ folder, port step 0, 2026-10-26). INTERPOSER_CODE is
# not needed; if it is set, it must name this same charge-interposer/.
CODE="$(cd "$(dirname "$0")/.." && pwd -P)"
if [ -n "${INTERPOSER_CODE:-}" ] && \
   [ "$(cd "$INTERPOSER_CODE" 2>/dev/null && pwd -P)" != "$CODE" ]; then
  echo "INTERPOSER_CODE=$INTERPOSER_CODE is not this tree's charge-interposer/ ($CODE)" >&2
  exit 2
fi
: "${REVERSE_IT_DIR:?REVERSE_IT_DIR is not set -- the SeaDrive reverse-it root}"
FW="$(cd "$CODE/firmware" && pwd)"
HD="$FW/test/host_diff"
FIX="$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware"
WORK="${TMPDIR:-/tmp}/cadmut_$$"
rm -rf "$WORK"; mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
TRACES="unit_hold_cadence_298_302 unit_hold_cadence_290"

fail=0
for v in control gate300; do
  mkdir -p "$WORK/$v"
  cp "$FW/src/machine.cpp" "$FW/src/machine.h" "$WORK/$v/"
  if [ "$v" = gate300 ]; then
    before="$(md5sum < "$WORK/$v/machine.cpp")"
    sed -i 's|c\.hold_step_min_ms = 280;|c.hold_step_min_ms = 300; /* MUTATION */|' "$WORK/$v/machine.cpp"
    if [ "$(md5sum < "$WORK/$v/machine.cpp")" = "$before" ]; then
      echo "=== gate300: THE MUTATION DID NOT APPLY -- nothing was tested"
      fail=1; continue
    fi
  fi
  if ! g++ -std=c++11 -O2 -I "$WORK/$v" -o "$WORK/$v/hr" \
       "$HD/host_runner.cpp" "$WORK/$v/machine.cpp" 2>"$WORK/$v/err"; then
    echo "=== $v: BUILD FAILED"; head -5 "$WORK/$v/err"; fail=1; continue
  fi
  echo "=== $v ==="
  for t in $TRACES; do
    if "$WORK/$v/hr" "$FIX/$t.trace" | diff -q - "$FIX/$t.golden" >/dev/null; then
      r=IDENTICAL
    else
      r=DIFFERS
    fi
    echo "    $t $r"
    if [ "$v" = control ] && [ "$r" != IDENTICAL ]; then
      echo "    CONTROL DIFFERS -- the trace or golden is broken, not the mutant"; fail=1
    fi
    if [ "$v" = gate300 ] && [ "$r" != DIFFERS ]; then
      echo "    SURVIVED -- this trace cannot tell 0.3 s from 0.28 s"; fail=1
    fi
  done
done
[ $fail -eq 0 ] && echo "MUTATION CAUGHT by both traces; control identical." || echo "PROBLEM (see above)"
exit $fail
