#!/usr/bin/env bash
# Release the X2 from an x2_hold.py hold and read its build off the wire.
#
#   bash x2_release_identify.sh <out dir> [adapter]     # adapter: pcan (default) or kvaser
#
# Since 2026-10-16 the X2's standing image is 756d6714, which is silent on CAN1
# except for its power-up round (0x7F4-0x7F7) at boot. So the reader has to be
# listening, in normal mode so it ACKs, BEFORE the release boots the app:
# bench_identify_board.py (interposer-firmware, read-only use) is started
# first, the release follows 2 s later, and the reader's 0x7F7 decode is the
# identification. Needs INTERPOSER_CODE (a charge-interposer/ clone, read only
# for build_lookup.py and the manifest). Run from the reverse-it root.
set -u
OUT=$1
AD=${2:-pcan}
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
I=projects/vtrux/notes/artifacts/interposer-firmware
: "${INTERPOSER_CODE:?set INTERPOSER_CODE}"
mkdir -p "$OUT"
stamp=$(date +%Y%m%dT%H%M%S)
py -3.14 $I/bench_identify_board.py --adapter "$AD" --seconds 12 > "$OUT/x2_identify_${stamp}.txt" 2>&1 &
rd=$!
sleep 2
py -3.14 $A/x2_hold.py release > "$OUT/x2_release_${stamp}.txt" 2>&1
echo "release exit $?"
wait $rd
echo "identify exit $?"
cat "$OUT/x2_identify_${stamp}.txt"
mv "${GI_RUNS_DIR:-$DATA/runs}"/x2_hold_release_*.json "$OUT/" 2>/dev/null
