#!/usr/bin/env bash
# Baseline part B (baseline_run_list.md), the rows that need neither an X2 hold
# nor a reflash: B4, B11 and B13, the same arms as on c65c45e. Gen-inhibit tester,
# 2026-10-16 (on c65c45e these were run by hand; this records the commands).
#
#   GI_COMMIT=e3d9437 REVERSE_IT_DIR=<reverse-it root> GI_WICAN_SSID=... bash baseline_b_noX2.sh [B4] [B11] [B13] [B11raw]
#
# B11raw (2026-10-17, not in the default set): B11's arms with raw POSTs and a
# Wi-Fi capture per arm, for b11_rawpost_judge.py.
#
# It runs from the reverse-it root, which it cd's to (DATA below is relative to it);
# canre comes from the pin since 2026-10-26 (canre_pin.py). One arm at a time; each arm's stdout is kept beside its record in runs/baseline_<commit>/bench/.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, gen-inhibit/tools/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
mkdir -p "$D"
STEPS=${*:-B4 B11 B13}
# An arm (or B11raw capture) that exits non-zero makes the script exit 1 after the
# rest have run (reviewer, 2026-10-25): the arms cases have no judge, so they were a
# PASS on exit 0 while every arm failed (6926fb0 re-run 2, a Wi-Fi outage).
ARMS_FAILED=0   # B4 is judged from its own output and is not counted
DUMPCAP=${GI_DUMPCAP:-"/c/Program Files/Wireshark/dumpcap.exe"}
DUMPCAP_PRE=${GI_DUMPCAP_PRE:-}   # a command put in front of dumpcap (the test's fake); empty here
# B11raw's capture stops TAIL s after its arm (reviewer, 2026-10-20; a fixed 75 s before).
# Its records are capture_<arm>.json and window_<arm>.json, not B11raw_e4_*.json, which
# baseline_b_desk.sh B11raw takes as the e4 records.
TAIL=${GI_CAPTURE_TAIL_S:-5}
CAPMAX=${GI_CAPTURE_MAX_S:-300}
IFACE='\Device\NPF_{2D126A09-0299-425A-88DC-51DA04E0C70C}'   # Wi-Fi on NICKY-XPS (dumpcap -D)
uptime_line() {   # one line: uptime_ms and reset reason from the page
  py -3.14 -B - "$A" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import ota_guard_raw_probe as P
pg, err = P.G.gi_status(P.HOST)
print("%s %s" % ((pg or {}).get("uptime_ms"), (pg or {}).get("reset_reason")) if pg else "NONE %s" % err)
PY
}
e4() {   # e4 <out name> <args...>
  local n=$1; shift
  echo "$(date +%H:%M:%S) $n"
  py -3.14 -B $A/e4_device_calibration.py --arm idle --offset-us 500 "$@" --out "$D/$n.json" > "$D/$n.txt" 2>&1
  local r=$?
  echo "  exit $r"
  [ $r -eq 0 ] || ARMS_FAILED=$((ARMS_FAILED + 1))
}
for s in $STEPS; do
  case $s in
    B4)
      py -3.14 -B $A/skip_method_bench.py > "$D/B4_skip_method_stdout.txt" 2>&1
      echo "B4 exit $?"
      ;;
    B11)   # INHIBIT/PASSIVE -> OBSERVE at 10 s -> back at 20 s, interleaved I P P I I P
      stamp=$(date +%Y%m%dT%H%M%S)
      k=0
      for m in inhibit passive passive inhibit inhibit passive; do
        k=$((k + 1))
        back=3; [ $m = passive ] && back=4
        pre=$(uptime_line); t0=$(date +%s.%N)
        e4 B11_e4_quiesce_${m}_${C}_${stamp}_arm$k --capture "$(cygpath -m "$PWD/$DATA/runs/scottsvalley_armable_300s.log")" \
          --window-start 0 --seconds 35 --mode $m --post-schedule "10:1,20:$back"
        post=$(uptime_line); t1=$(date +%s.%N)
        echo "arm$k $m pre=[$pre] post=[$post] wall_s=$(py -3.14 -c "print('%.2f' % ($t1 - $t0))")" >> "$D/B11_quiesce_uptime_$stamp.txt"
      done
      ;;
    B11raw)   # B11's arms again with raw POSTs and a Wi-Fi capture per arm (reviewer, 2026-10-17):
              # the request's arrival at the device read from the capture (b11_rawpost_judge.py)
      stamp=$(date +%Y%m%dT%H%M%S)
      k=0
      for m in inhibit passive passive inhibit inhibit passive; do
        k=$((k + 1))
        back=3; [ $m = passive ] && back=4
        n=B11raw_e4_quiesce_${m}_${C}_${stamp}_arm$k
        py -3.14 -B $A/capture_window.py --stopfile "$D/$n.stop" --record "$D/capture_$n.json" --max $CAPMAX -- \
          $DUMPCAP_PRE "$DUMPCAP" -q -i "$IFACE" -f "host 192.168.80.1 and tcp port 80" -a duration:$CAPMAX \
          -w "$D/$n.pcapng" > "$D/$n.dumpcap.txt" 2>&1 &
        cap=$!
        sleep 3
        pre=$(uptime_line); t0=$(date +%s.%N)
        e4 $n --capture "$(cygpath -m "$PWD/$DATA/runs/scottsvalley_armable_300s.log")" \
          --window-start 0 --seconds 35 --mode $m --post-schedule "10:1,20:$back" --post-raw-timeout 3
        post=$(uptime_line); t1=$(date +%s.%N)
        sleep $TAIL
        : > "$D/$n.stop"
        wait $cap; r=$?; echo "  capture exit $r"
        [ $r -eq 0 ] || ARMS_FAILED=$((ARMS_FAILED + 1))
        rm -f "$D/$n.stop"
        echo "{\"arm_start\": $t0, \"arm_end\": $t1, \"tail_s\": $TAIL}" > "$D/window_$n.json"
        echo "arm$k $m pre=[$pre] post=[$post] wall_s=$(py -3.14 -c "print('%.2f' % ($t1 - $t0))") capture $(stat -c %s "$D/$n.pcapng") bytes" >> "$D/B11raw_quiesce_uptime_$stamp.txt"
      done
      sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$D"/B11raw_e4_*.txt "$D"/B11raw_quiesce_uptime_*.txt   # not B11raw_run_log.txt (2026-10-17)
      ;;
    B13)   # P I I P P I on truck_passive2 0-30 s, then P I on scottsvalley 0-30 s
      stamp=$(date +%Y%m%dT%H%M%S)
      k=0
      for m in passive inhibit inhibit passive passive inhibit; do
        k=$((k + 1))
        e4 B13_e4_passive_tp2_${m}_${C}_${stamp}_arm$k --capture "$(cygpath -m "$PWD/$DATA/runs/truck_passive2.log")" \
          --window-start 0 --seconds 30 --mode $m
      done
      for m in passive inhibit; do
        k=$((k + 1))
        e4 B13_e4_passive_sv_${m}_${C}_${stamp}_arm$k --capture "$(cygpath -m "$PWD/$DATA/runs/scottsvalley_armable_300s.log")" \
          --window-start 0 --seconds 30 --mode $m
      done
      ;;
    *) echo "unknown step $s"; exit 2 ;;
  esac
done
[ "$ARMS_FAILED" -eq 0 ] || { echo "$ARMS_FAILED arm(s) or capture(s) exited non-zero"; exit 1; }
