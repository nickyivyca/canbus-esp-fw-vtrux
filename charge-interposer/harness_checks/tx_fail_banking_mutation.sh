#!/usr/bin/env bash
# Can-fail for test_l2_bridge's tx-fail case (tester, 2026-10-13).
#
# Spec 2.2's 0x7F6 B7 counts charger-port failures only WHILE the VCU's flow
# bit is 1. The case's flow-1 check was `gated > 0`, which an implementation
# that BANKS the flow-0 failures and adds them once flow is 1 also passes
# (the implementor's note, 2026-10-13). The case now runs 60 passes of flow
# 0 and then 5 of flow 1 and bounds the count by the flow-1 phase.
#
# This builds test_l2_bridge against a COPY of the firmware with one
# mutation in charger_tx_failures.h -- the baseline only moves when it
# counts, so failures outside a session are banked -- and requires the
# tx-fail case to FAIL on that bound. The control (unmutated copy) must
# pass. The mutation verifies that it applied: a sed that matches nothing
# would make a "clean" run that tested nothing.
#
# Run:  bash charge-interposer/harness_checks/tx_fail_banking_mutation.sh
#       (under WSL: wsl bash ..., from any folder)
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
FW="$(cd "$CODE/firmware" && pwd)"
# The work folder is this run's own under the tree's scratch/, never the
# system temp folder, removed when the script exits (scratch_run.sh).
. "$CODE/scratch_run.sh" || exit 2
scratch_run tx-fail-banking-mutation || exit 2
WORK="$SCRATCH_RUN"

# test_l2_bridge as host_bridge/run.sh builds it (port step 2b: on the
# shared vehicle port and TWAI driver fake), by its l2_build.sh, which
# finds the shared folders (refused here, before anything is built, if
# they are not there).
L2_BUILD="$CODE/fw_harness/host_bridge/l2_build.sh"
. "$L2_BUILD"
hb_shared || exit 2

build_and_run() {   # $1 = tree; prints the case's output; returns its status
  local T="$1" HB="$1/test/host_bridge" SRC="$1/main" bin="$1/l2"
  rm -f "$bin"
  if ! bash "$L2_BUILD" "$bin" "$HB" "$SRC" > "$T/build.err" 2>&1; then
    echo "BUILD FAILED:"; head -5 "$T/build.err"; return 2
  fi
  "$bin" tx-fail
}

fail=0
for v in control banking; do
  T="$WORK/$v"
  mkdir -p "$T/test"
  cp -r "$FW/main" "$T/main"
  cp -r "$CODE/fw_harness/host_bridge" "$T/test/host_bridge"
  H="$T/main/charger_tx_failures.h"
  if [ "$v" = banking ]; then
    before="$(md5sum < "$H")"
    # seen_ = total; moves inside the `expected` branch
    sed -i 's|^\(\s*\)seen_ = total;\(.*\)$|\1/* MUTATION: baseline moves only when counting */\2|' "$H"
    sed -i 's|if (delta \&\& expected) while_expected_ += delta;|if (delta \&\& expected) { while_expected_ += delta; seen_ = total; }|' "$H"
    if [ "$(md5sum < "$H")" = "$before" ] || ! grep -q "MUTATION" "$H" \
       || ! grep -q "seen_ = total; }" "$H"; then
      echo "=== banking: THE MUTATION DID NOT APPLY -- nothing was tested"
      fail=1
      continue
    fi
  fi
  echo "=== $v ==="
  out="$(build_and_run "$T")"
  rc=$?
  echo "$out" | grep -E "flow=|banked|FAIL|all checks OK|BUILD" | sed 's/^/    /'
  if [ "$v" = control ]; then
    [ $rc -eq 0 ] || { echo "    CONTROL FAILED -- the case is broken, not the mutant"; fail=1; }
  else
    if [ $rc -ne 0 ] && echo "$out" | grep -q "FAIL.*not banked"; then
      echo "    caught it, on the not-banked line"
    else
      echo "    SURVIVED -- the case cannot see banking"; fail=1
    fi
  fi
done
[ $fail -eq 0 ] && echo "MUTATION CAUGHT; control passes." || echo "PROBLEM (see above)"
exit $fail
