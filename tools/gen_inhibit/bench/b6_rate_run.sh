#!/usr/bin/env bash
# B6 multi-count rate (gen-inhibit reviewer, 2026-10-20, request (b)): 20 more B6
# threshold arms of n10 on e3d9437, across the three leads (7 at 1.5 s, 7 at 5 s, 6 at
# 8.5 s), the WiCAN reset before each arm (--reset-wican, shown in each record). The same
# x2_jam_cases.py threshold command as B6 in baseline_b_jam.sh; per pulse the record
# keeps the device's bus_error, TEC and REC page samples beside the witness's error
# frames (b6_multicount_landing.py reads them). Gen-inhibit tester.
#
#   GI_COMMIT=e3d9437 INTERPOSER_CODE=... bash b6_rate_run.sh
#
# Custody and restore are baseline_b_jam.sh's own steps: dump (two reads must match;
# SENSITIVE, only path, size and sha256 recorded), flash the jammer a89ece8c, read its
# banner; at the end restore from the dump, hold in ROM, release with the PCAN ACKing and
# read 0x7F7 off the wire. Run from the reverse-it root, holding the bench lock.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
: "${INTERPOSER_CODE:?set INTERPOSER_CODE}"
export GI_WICAN_SSID=${GI_WICAN_SSID:-$(grep -o 'SoftAP `WiCAN_[0-9a-fA-F]*`' firmware/bench-holder.md | head -1 | grep -o 'WiCAN_[0-9a-fA-F]*')}
[ -n "$GI_WICAN_SSID" ] || { echo "no WiCAN SSID in firmware/bench-holder.md"; exit 2; }
stamp=$(date +%Y%m%dT%H%M%S)
export GI_OUT=${GI_OUT:-$DATA/runs/b6_rate_${C}_$stamp}
# Relative, no spaces: baseline_b_jam.sh expands GI_RUNS_DIR unquoted in its ls. An
# absolute path ("My Libraries") split there on 2026-10-20 01:35, the dump record came
# back empty and flash-jammer refused before writing (runs/b6_rate_e3d9437_20261020T013136).
export GI_RUNS_DIR=$GI_OUT
case "$GI_RUNS_DIR" in *" "*) echo "GI_RUNS_DIR has a space: $GI_RUNS_DIR"; exit 2;; esac
mkdir -p "$GI_OUT"
PW="12 V on OBD pin 16, USB unplugged (no COM4); WiCAN $C _man; X2 jammer a89ece8c (custody)"
echo "== $(date +%H:%M:%S) custody -> $GI_OUT"
bash $A/baseline_b_jam.sh custody || { echo "custody failed: stop (X2 state: see $GI_OUT/x2)"; exit 1; }
rec=$(ls -t "$GI_RUNS_DIR"/x2_flash_custody_dump_*.json | head -1)
echo "dump record: $rec"
idrec=$(ls -t "$GI_RUNS_DIR"/x2_jammer_id_*.json 2>/dev/null | head -1)
if [ -z "$idrec" ] || ! grep -q '"banner_ok": true' "$idrec"; then
  echo "jammer banner not read (${idrec:-no record}): no arms; restoring"
  bash $A/baseline_b_jam.sh restore "$rec"
  exit 1
fi
for spec in 1.5:7 5:7 8.5:6; do
  l=${spec%%:*}; n=${spec##*:}
  echo "$(date +%H:%M:%S) B6_rate_n10_lead$l ($n arms)"
  py -3.14 -B $A/x2_jam_cases.py threshold --arms $n --n 10 --interval-ms 1000 --lead-s $l --reset-wican \
    --power "$PW" > "$GI_OUT/B6_rate_n10_lead$l.txt" 2>&1
  echo "  exit $?"
  grep -E "^arm |^verdict" "$GI_OUT/B6_rate_n10_lead$l.txt" | cut -c1-200
done
echo "== $(date +%H:%M:%S) restore"
bash $A/baseline_b_jam.sh restore "$rec"
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$GI_OUT"/B6_rate_*.txt "$GI_OUT"/x2/*.txt 2>/dev/null
echo "== $(date +%H:%M:%S) done"
