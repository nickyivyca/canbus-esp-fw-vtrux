#!/usr/bin/env bash
# Baseline part B, the OTA group (baseline_run_list.md): the rows that need another
# image on the WiCAN -- B12 and B19 present (_man_rt), B19 and B23 (txabort), B19
# (abortwin), B19 and B26 (rxq), B19 absent and the prod page (prod), B17's second
# half (_man2) -- then _man back. The same runs as on c65c45e. Gen-inhibit tester,
# 2026-10-16 (run by hand before; this records them).
#
#   GI_COMMIT=e3d9437 GI_FW_TOOLS=... GI_WICAN_SSID=... bash baseline_b_otagroup.sh <worktree> <prod build dir> [step ...]
#   steps: rt txabort abortwin rxq prod man2 man   (default: all, in that order)
#   also: prod_rxq30 (B19's RXQ probe 30 s after QUIESCE, on prod; not in the default)
#   GI_NO_OTA=1: each step's own OTA is left to the chain runner (2026-10-18)
#
# Every OTA goes through ota_pcap.sh (a Wi-Fi capture around it; the open 128 KiB
# observation). An OTA that is not verdict (c) TOOK stops the group: no retry
# (reviewer, 2026-10-16). Run from the reverse-it root.
set -u
W=${1:?worktree}; P=${2:?prod build dir}; shift 2
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
mkdir -p "$D"
STEPS=${*:-rt txabort abortwin rxq prod man2 man}
PWR="12 V on OBD pin 16, USB unplugged (no COM4); X2 756d6714 not held"
export MSYS_NO_PATHCONV=1   # restart_error_frames' /path arguments
ota() {   # ota <label> <image>
  # GI_NO_OTA=1 (the chain runner, 2026-10-18): the runner has already put the step's
  # image on the board, so the OTA named after the step is skipped. Others still run.
  if [ -n "${GI_NO_OTA:-}" ] && [ "$1" = "$s" ]; then echo "OTA $1 left to the runner"; return 0; fi
  local stem="$D/otagroup_$1_$(date +%Y%m%dT%H%M%S)"
  bash $A/ota_pcap.sh "$2" "$stem"
  grep -q "VERDICT: (c) TOOK" "$stem.txt" || { echo "OTA $1 did not TOOK: the group stops here ($stem.txt)"; exit 1; }
}
img() { ls "$W/build-tester-$1"/wican-fw_obd_${C}_man*.bin; }
inert() { local tag=$1 expect=$2; shift 2
  py -3.14 -B $A/test_only_inert.py --expect $expect "$@" --power "$PWR; image $tag" > "$D/B19_${expect}_$tag.txt" 2>&1
  echo "B19 $expect $tag exit $?"; tail -2 "$D/B19_${expect}_$tag.txt"; }
for s in $STEPS; do
  echo "== $(date +%H:%M:%S) $s"
  case $s in
    rt)
      ota rt "$(img rt)"
      k=0
      for r in reboot crash crash reboot reboot crash; do
        k=$((k + 1))
        extra=""; [ $r = crash ] && extra="--crash-path /gi_quiesce?crash=illegal"
        py -3.14 -B $A/restart_error_frames.py --condition live --cycles 1 --restart $r $extra \
          --record-get /gi_quiesce --power "$PWR; image $C _man_rt" > "$D/B12_${k}_live_$r.txt" 2>&1
        echo "B12 $k live $r exit $?"
      done
      py -3.14 -B $A/restart_error_frames.py --condition off --cycles 1 --restart reboot \
        --record-get /gi_quiesce --power "$PWR; image $C _man_rt" > "$D/B12_7_off_reboot.txt" 2>&1
      echo "B12 7 off reboot exit $?"
      inert rt present --switch GI_TEST_RESTART_QUIESCE
      # The page as the rt step leaves it: mode, uptime, latch (reviewer, 2026-10-20).
      # Without rt_self's OTA the next step starts from this state, not a fresh boot.
      py -3.14 -B $A/page_state.py --label "B12 rt end" --out "$D/B12_rt_end_page.json"; echo "rt end page exit $?"
      # No second OTA of _man_rt over itself ("rt_self", dropped 2026-10-20, reviewer). An
      # OTA always writes the slot that is not running, and a failed or rolled-back next
      # upload falls back to the image running when it started: _man_rt either way, with
      # or without rt_self, and _man is never that fallback. The c65c45e B19 addendum's
      # STEP 2 (_man2 over _man2 "so both slots hold it") is the same move, no effect.
      ;;
    txabort)
      ota txabort "$(img txabort)"
      inert txabort present --switch GI_INSTRUMENT_TXABORT
      py -3.14 -B $A/wican_txabort_bench.py --no-ixxat > "$D/B23_txabort_bench.txt" 2>&1; echo "B23 exit $?"
      ;;
    abortwin)
      ota abortwin "$(img abortwin)"
      inert abortwin present --switch GI_INSTRUMENT_ABORTWIN
      ;;
    rxq)
      ota rxq "$(img rxq)"
      inert rxq present --switch GI_INSTRUMENT_RXQ
      for a in idle_a idle_b loaded; do
        arm=idle; [ $a = loaded ] && arm=loaded
        py -3.14 -B $A/e4_device_calibration.py --capture "$(cygpath -m "$PWD/$DATA/runs/truck_passive2.log")" \
          --window-start 0.0 --seconds 35 --arm $arm --out "$D/B26_rxq_$a.json" > "$D/B26_rxq_$a.txt" 2>&1
        echo "B26 $a exit $?"
      done
      ;;
    prod)
      ota prod "$P/wican-fw_obd_${C}_man.bin"
      inert prod absent --switch GI_TEST_RESTART_QUIESCE --switch GI_INSTRUMENT_RXQ \
        --switch GI_INSTRUMENT_ABORTWIN --switch OTA_HEALTH_FAULT_INJECT
      inert prod_txabort absent --switch GI_INSTRUMENT_TXABORT
      py -3.14 -B $A/spec11_page_check.py --image "$P/wican-fw_obd_${C}_man.bin" --other "$(img man2)" \
        --out "$D/B19_spec11_page_prod.json" > "$D/B19_spec11_page_prod.txt" 2>&1; echo "prod page exit $?"
      ;;
    prod_rxq30)   # RXQ again 30 s after the QUIESCE probe, on prod (reviewer, 2026-10-17; e3d9437 00:58)
      py -3.14 -B $A/test_only_inert.py --expect absent --switch GI_TEST_RESTART_QUIESCE --switch GI_INSTRUMENT_RXQ \
        --gap-s 30 --power "$PWR; image prod" > "$D/B19_absent_prod_rxq_gap30.txt" 2>&1
      echo "B19 absent prod rxq gap30 exit $?"; tail -3 "$D/B19_absent_prod_rxq_gap30.txt"
      ;;
    man2)
      ota man2 "$(img man2)"
      py -3.14 -B $A/spec11_page_check.py --image "$(img man2)" --other "$(img man)" \
        --out "$D/B17_spec11_page_${C}_man2.json" > "$D/B17_spec11_page_${C}_man2.txt" 2>&1; echo "B17b exit $?"
      ;;
    man)
      ota man_back "$(img man)"
      ;;
    *) echo "unknown step $s"; exit 2 ;;
  esac
done
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$D"/B12_*.txt "$D"/B19_*.txt "$D"/B23_*.txt "$D"/B26_*.txt "$D"/B17_*.txt 2>/dev/null
echo "== $(date +%H:%M:%S) done"
