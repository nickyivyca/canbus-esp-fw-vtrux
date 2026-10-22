#!/usr/bin/env bash
# Baseline B3 (baseline_run_list.md): the go-live batch, 5 entries into INHIBIT
# from each of OFF, OBSERVE, RESPOND and PASSIVE, exactly the arms
# `golive_stale_check.py plan` prints, written to runs/baseline_<commit>/bench/
# (GI_COMMIT, default c65c45e; e3d9437 added 2026-10-16).
# Then judged by golive_stale_check.py and mode_entry_analysis.py.
#
#   [GI_COMMIT=e3d9437] bash baseline_b3_golive.sh [modes...]   # default: off observe respond passive
#
# Run from the reverse-it root. One arm at a time; each arm's stdout is kept.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:-c65c45e}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
mkdir -p "$D"
MODES=${*:-off observe respond passive}
for m in $MODES; do
  for n in 1 2 3 4 5; do
    stamp=$(date +%Y%m%dT%H%M%S)
    out=$D/B3_e4_golive_${m}_${C}_${stamp}_arm${n}
    if [ "$m" = off ]; then
      extra="--mode inhibit --arm-after 5"
    else
      extra="--mode $m --post-schedule 20:3"
    fi
    echo "$(date +%H:%M:%S) $m arm $n -> $out.json"
    py -3.14 $A/e4_device_calibration.py --arm idle --capture runs/scottsvalley_armable_300s.log \
      --seconds 35 --offset-us 500 $extra --out "$out.json" > "$out.txt" 2>&1
    echo "  exit $?"
  done
done
