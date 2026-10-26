#!/usr/bin/env bash
# Baseline part B, the rows run by hand on e3d9437 (baseline_run_list.md): B1, B2's
# six arms, B9, B10, B14, B15-B18 and B20, with the arguments of those runs as the
# transcripts recorded them (2026-10-17). Gen-inhibit tester, 2026-10-18, for the
# chain runner; each step is one check.
#
#   GI_COMMIT=e3d9437 REVERSE_IT_DIR=<reverse-it root> GI_IMAGES=<worktree with build-tester-*> \
#   GI_PW_IMAGE="image e3d9437 _man (md5 95977df0, elf 25f50ac0)" [GI_OUT=<dir>] \
#   bash baseline_b_hand.sh <step> ...
#   steps: B1 B2app B2held B9 B10 B14 B15 B16 B17a B18 B20 NONODE
#
# NONODE (2026-10-20) is nonode_bench.py, spec 12.4 "a bus with no other node"; it
# needs GI_TERMINATION and GI_MAN_SHA, and writes its record beside its .txt.
# The X2's state is not changed here: B2held, B9, B10, B14 and NONODE need it held in ROM
# (x2_hold.py), the rest need it on its own image, and the runner sees to that. The
# power line says which. GI_WICAN_SSID is read from firmware/bench-holder.md when
# unset (that file is its only home) and redacted from this script's .txt outputs.
# Run from the reverse-it root.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}
W=${GI_IMAGES:-}
mkdir -p "$D"
: "${GI_PW_IMAGE:?set GI_PW_IMAGE (the image line of the power record)}"
export GI_WICAN_SSID=${GI_WICAN_SSID:-$(grep -o 'SoftAP `WiCAN_[0-9a-fA-F]*`' firmware/bench-holder.md | head -1 | grep -o 'WiCAN_[0-9a-fA-F]*')}
[ -n "$GI_WICAN_SSID" ] || { echo "no WiCAN SSID in firmware/bench-holder.md"; exit 2; }
G=$A
GD=$(cygpath -m "$PWD/$DATA")
PW_APP="12 V on OBD pin 16, USB unplugged; $GI_PW_IMAGE; X2 756d6714 not held"
PW_HELD="12 V on OBD pin 16, USB unplugged; $GI_PW_IMAGE; X2 756d6714 held in ROM (x2_hold.py)"
# A B2 arm that exits non-zero makes the script exit 1 after the
# rest have run (reviewer, 2026-10-25): the arms cases have no judge, so they were a
# PASS on exit 0 while every arm failed (6926fb0 re-run 2, a Wi-Fi outage).
# The judged steps are judged from their own outputs and are not counted.
ARMS_FAILED=0
e4() {   # e4 <name> <args...>: one e4 arm, as B2 on e3d9437
  local n=$1; shift
  py -3.14 -B "$G/e4_device_calibration.py" --arm idle --offset-us 500 "$@" --out "$D/$n.json" > "$D/$n.txt" 2>&1
  local r=$?
  echo "$n exit $r"
  [ $r -eq 0 ] || ARMS_FAILED=$((ARMS_FAILED + 1))
}
red() { for f in "$@"; do [ -f "$f" ] && sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$f"; done; }
. "$A/bench_images.sh"   # img_in: the image of $C, named with or without the commit (port step 0c)
b2() {   # b2 <x2app|x2held>
  e4 B2_e4_presence_inhibit_${C}_$1 --capture "$GD/runs/truck_passive2.log" --window-start 0 --seconds 30 --mode inhibit
  e4 B2_e4_presence_respond_${C}_$1 --capture "$GD/runs/truck_passive2.log" --window-start 20 --seconds 30 --mode respond
  e4 B2_e4_presence_longarm_${C}_$1 --capture "$GD/runs/scottsvalley_armable_300s.log" --window-start 0 --seconds 300 --mode inhibit
  red "$D"/B2_e4_presence_*_${C}_$1.txt
}
for s in "$@"; do
  echo "== $(date +%H:%M:%S) $s"
  case $s in
    B1)
      py -3.14 -B $A/slcan_refusal_probe.py --power "$PW_APP" > "$D/B1_slcan_refusal_stdout.txt" 2>&1; echo "B1 exit $?"
      red "$D/B1_slcan_refusal_stdout.txt" ;;
    B2app) b2 x2app ;;
    B2held) b2 x2held ;;
    B9)
      py -3.14 -B $A/wrong_bitrate_errors.py calibrate --x2 held --power "$PW_HELD" > "$D/B9_wrong_bitrate_calibrate.txt" 2>&1; echo "B9 exit $?"
      red "$D/B9_wrong_bitrate_calibrate.txt" ;;
    B10)
      py -3.14 -B $A/trip6_rearm_check.py --x2 held --power "$PW_HELD" > "$D/B10_trip6_rearm_check.txt" 2>&1; echo "B10 exit $?"
      red "$D/B10_trip6_rearm_check.txt" ;;
    B14)
      py -3.14 -B $A/observe_ack_probe.py --power "$PW_HELD" --x2 held > "$D/B14_observe_ack_probe.txt" 2>&1; echo "B14 exit $?"
      red "$D/B14_observe_ack_probe.txt" ;;
    B15)
      py -3.14 -B $A/wican_segment_probe.py > "$D/B15_wican_segment_probe.txt" 2>&1; echo "B15 exit $?"
      red "$D/B15_wican_segment_probe.txt" ;;
    B16)
      py -3.14 -B $A/page_only_baseline.py --power "$PW_APP" --seconds 360 > "$D/B16_page_only_baseline.txt" 2>&1; echo "B16 exit $?"
      red "$D/B16_page_only_baseline.txt" ;;
    B17a)
      [ -n "$W" ] || { echo "B17a needs GI_IMAGES"; exit 2; }
      # --other: the rxq image, a different binary (reviewer, 2026-10-25): with reproducible builds
      # (step 0b) a second clean build, man2, is byte-identical to man and can be no control.
      M=$(img_in "$W/build-tester-man" "") && M2=$(img_in "$W/build-tester-rxq" "") || exit 2
      py -3.14 -B $A/spec11_page_check.py --image "$M" \
        --other "$M2" --out "$D/B17_spec11_page_${C}_man.json" \
        > "$D/B17_spec11_page_${C}_man.txt" 2>&1; echo "B17a exit $?"
      red "$D/B17_spec11_page_${C}_man.txt" ;;
    B18)   # schema 8 (port step 0c): the build id against the flashed _man image; --expect-sha for schema 7
      [ -n "$W" ] || { echo "B18 needs GI_IMAGES"; exit 2; }
      M=$(img_in "$W/build-tester-man" "") || exit 2
      py -3.14 -B $A/diag_build_page_check.py --image "$M" --expect-sha $C \
        --out "$D/B18_diag_build_page_check.json" \
        > "$D/B18_diag_build_page_check.txt" 2>&1; echo "B18 exit $?"
      red "$D/B18_diag_build_page_check.txt" ;;
    B20)   # every case interlock_bench lists, as on e3d9437 (28 then)
      CASES=$(py -3.14 -B $A/interlock_bench.py list 2>/dev/null | awk 'NF>=4 && $1 ~ /^[a-z0-9]+(-[a-z0-9.]+)+$/ {print $1}' | tr '\n' ' ')
      echo "cases: $(echo $CASES | wc -w)"
      [ -n "$CASES" ] || { echo "interlock_bench list gave no cases"; exit 2; }
      py -3.14 -B $A/interlock_bench.py run $CASES --power "$PW_APP" > "$D/B20_interlock_bench_run.txt" 2>&1; echo "B20 exit $?"
      red "$D/B20_interlock_bench_run.txt" ;;
    NONODE)
      : "${GI_TERMINATION:?set GI_TERMINATION (where the CAN1 terminators are, as read on the bench)}"
      : "${GI_MAN_SHA:?set GI_MAN_SHA (wican_man elf_sha256)}"
      GI_RUNS_DIR=$D py -3.14 -B $A/nonode_bench.py --termination "$GI_TERMINATION" --man-sha "$GI_MAN_SHA" \
        > "$D/NONODE_nonode_bench.txt" 2>&1; echo "NONODE exit $?"
      red "$D/NONODE_nonode_bench.txt" ;;
    *) echo "unknown step $s"; exit 2 ;;
  esac
done
echo "== $(date +%H:%M:%S) done"
[ "$ARMS_FAILED" -eq 0 ] || { echo "$ARMS_FAILED B2 arm(s) exited non-zero"; exit 1; }
