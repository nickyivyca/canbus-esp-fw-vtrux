#!/usr/bin/env bash
# Baseline B22 (baseline_run_list.md): case 3 saturation with the filler on a separate
# adapter, the X2 on the charge-interposer tester's slcan image (their custody: flashed,
# identified and restored by them). Two arms, the WiCAN rebooted by software before
# each (each arm ends latched "bus lost" once the replay stops), as on c65c45e.
# Gen-inhibit tester, 2026-10-17 (run by hand on c65c45e; this records it).
#
#   GI_COMMIT=e3d9437 GI_WICAN_SSID=... bash baseline_b22.sh [a] [b]
#
# Opens COM3 only through e4's --flood-slcan session; never flashes or resets the X2.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, gen-inhibit/tools/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
mkdir -p "$D"
ARMS=${*:-a b}
for a in $ARMS; do
  echo "== $(date +%H:%M:%S) arm $a: reboot"
  py -3.14 -B - "$A" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import interlock_bench as I
import lowid_flood_skip_bench as LF
print("reboot", I.reboot(LF))
PY
  n=B22_case3_${C}_man_$a
  py -3.14 -B $A/e4_device_calibration.py --capture "$(cygpath -m "$PWD/$DATA/runs/scottsvalley_armable_300s.log")" \
    --window-start 0 --seconds 90 --arm idle --flood-slcan COM3 --out "$D/$n.json" > "$D/$n.txt" 2>&1
  echo "  e4 exit $?"
  grep -E "provenance \(live\)|tx_ok=|FLOOD|board after|carried load|error frames" "$D/$n.txt" | cut -c1-220
done
# The case files only: a glob over B22_*.txt also took B22_run_log.txt, the stdout this
# script was writing to, and sed -i swapped it out under the open descriptor (2026-10-17).
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$D"/B22_case3_${C}_*.txt 2>/dev/null
echo "== $(date +%H:%M:%S) done"
