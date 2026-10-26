#!/usr/bin/env bash
# Baseline part B, the desk steps: judges and analyses over bench run files, no
# hardware. The same tools and inputs as on e3d9437 (baseline_run_list.md; the
# transcripts' commands, 2026-10-17). Gen-inhibit tester, 2026-10-18, for the chain
# runner, where each bench check has its own folder; a desk step reads the folders
# named on its line and writes into GI_OUT.
#
#   GI_COMMIT=e3d9437 [GI_OUT=<dir>] bash baseline_b_desk.sh <step> <input dir> ...
#
#   B2      <app dir> <held dir>   copy the six arms' .txt in, count the X2's frames
#   B3      <B3 dir>               golive_stale_check judge, mode_entry_analysis
#   B24     <B2 app dir> <B3 dir>  b24_frame_content_judge over B2's x2app arms and B3
#                                  (on e3d9437 it ran before the x2held arms existed)
#   B25     <B20 dir>              b25_keystate_judge on B20's rekey-long record
#   B11     <B11 dir>              quiesce_analysis
#   B11raw  <B11raw dir>           b11_rawpost_judge, quiesce_analysis
#   B13     <B13 dir> <B2 app dir> <B2 held dir>
#                                  passive_analysis; respond_booking_check over B13's
#                                  PASSIVE arms and B2's RESPOND arms
#   B19present <rt dir> <txabort dir> <abortwin dir> <rxq dir>   copy the four in
#
# Run from the reverse-it root. A step whose inputs are missing says so and exits 2:
# an analysis of nothing is not a result.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}
mkdir -p "$D"
s=${1:?step}; shift
need() {   # need <glob result...>: refuse an empty input set
  [ $# -gt 0 ] && [ -e "$1" ] || { echo "$s: no input files"; exit 2; }
}
case $s in
  B2)
    app=${1:?app dir}; held=${2:?held dir}
    set -- "$app"/B2_e4_presence_*_${C}_x2app.json "$held"/B2_e4_presence_*_${C}_x2held.json; need "$@"
    [ $# -eq 6 ] || { echo "B2: $# arm records, want 6"; exit 2; }
    for j in "$@"; do cp "${j%.json}.txt" "$D/"; cp "$j" "$D/"; cp "${j%.json}_idle_witness.log.gz" "$D/" 2>/dev/null; done
    py -3.14 -B $A/x2_presence_count.py "$D"/B2_e4_presence_*_${C}_x2*.json > "$D/B2_x2_presence_count.txt" 2>&1
    echo "B2 count exit $?" ;;
  B3)
    set -- "${1:?B3 dir}"/B3_e4_golive_*_${C}_*.json; need "$@"
    py -3.14 -B $A/golive_stale_check.py judge "$@" > "$D/B3_golive_stale_check.txt" 2>&1; echo "stale exit $?"
    py -3.14 -B $A/mode_entry_analysis.py "$@" > "$D/B3_mode_entry_analysis.txt" 2>&1; echo "mode entry exit $?" ;;
  B24)
    app=${1:?B2 app dir}; b3=${2:?B3 dir}
    set -- "$app"/B2_e4_presence_*_${C}_x2app.json "$b3"/B3_e4_golive_*_${C}_*.json; need "$@"
    py -3.14 -B $A/b24_frame_content_judge.py "$@" > "$D/B24_frame_content_${C}.txt" 2>&1; echo "B24 exit $?" ;;
  B25)
    set -- "${1:?B20 dir}"/interlock_rekey-long_*.json; need "$@"
    [ $# -eq 1 ] || { echo "B25: $# rekey-long records, want 1"; exit 2; }
    py -3.14 -B $A/b25_keystate_judge.py "$1" > "$D/B25_keystate_rekey-long_${C}.txt" 2>&1; echo "B25 exit $?" ;;
  B11)
    set -- "${1:?B11 dir}"/B11_e4_quiesce_*_${C}_*.json; need "$@"
    py -3.14 -B $A/quiesce_analysis.py "$@" > "$D/B11_quiesce_analysis.txt" 2>&1; echo "B11 exit $?" ;;
  B11raw)
    dir=${1:?B11raw dir}
    set -- "$dir"/B11raw_e4_quiesce_*.json; need "$@"
    args=(); for n in "$@"; do args+=("$n" "${n%.json}.pcapng"); done   # an array: run dirs have spaces
    py -3.14 -B $A/b11_rawpost_judge.py "${args[@]}" > "$D/B11raw_rawpost_judge.txt" 2>&1; echo "judge exit $?"
    py -3.14 -B $A/quiesce_analysis.py "$@" > "$D/B11raw_quiesce_analysis.txt" 2>&1; echo "qa exit $?" ;;
  B13)
    b13=${1:?B13 dir}; app=${2:?B2 app dir}; held=${3:?B2 held dir}
    set -- "$b13"/B13_e4_passive_*_${C}_*.json; need "$@"
    py -3.14 -B $A/passive_analysis.py "$@" > "$D/B13_passive_analysis.txt" 2>&1; echo "passive exit $?"
    # B2's RESPOND arms by their full names: a glob in each folder would take both
    # arms twice when the two folders are one (an offline rerun over one bench folder)
    set -- "$b13"/B13_e4_passive_*passive_${C}_*.json "$app"/B2_e4_presence_respond_${C}_x2app.json "$held"/B2_e4_presence_respond_${C}_x2held.json; need "$@"
    for j in "$@"; do [ -f "$j" ] || { echo "B13: no $j"; exit 2; }; done
    py -3.14 -B $A/respond_booking_check.py "$@" > "$D/B13_respond_booking_check.txt" 2>&1; echo "booking exit $?" ;;
  B19present)
    [ $# -eq 4 ] || { echo "B19present: 4 dirs, got $#"; exit 2; }
    for t in rt txabort abortwin rxq; do
      f="$1/B19_present_$t.txt"; shift
      [ -f "$f" ] || { echo "B19present: no $f"; exit 2; }
      cp "$f" "$D/"
    done
    echo "B19present copied 4" ;;
  *) echo "unknown step $s"; exit 2 ;;
esac
