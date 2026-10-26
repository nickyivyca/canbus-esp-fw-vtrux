#!/usr/bin/env bash
# Baseline B21 (baseline_run_list.md): flash and OTA writes refused while armed in
# each non-OFF mode, as on c65c45e and c6fdab5. Gen-inhibit tester, 2026-10-16
# (run by hand before; this records the commands).
#   1. bench_flash_guard.py --check-accepted   (firmware repo tools/gen_inhibit)
#   2. bench_ota_guard.py --allow-ota-write    (OFF gets a deliberately invalid image)
#   3. ota_guard_raw_probe.py, 5 reps per non-OFF mode, the 184-byte multipart body
#      and then the whole running image, each with an unelevated dumpcap of the
#      laptop's Wi-Fi, joined by ota_guard_pcap_join.py --expect=no-rst
#
#   GI_COMMIT=e3d9437 REVERSE_IT_DIR=<reverse-it root> GI_WICAN_SSID=... [GI_B21_STEPS="guards probe"] bash baseline_b21.sh <running image .bin>
# Before a first run, read the INCIDENT in plans/gen-inhibit-wican-implementation.md
# (~line 1672): no write in the OFF half may leave a bad config.
set -u
img=${1:?the running image}
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
DUMPCAP=${GI_DUMPCAP:-"/c/Program Files/Wireshark/dumpcap.exe"}
# GI_DUMPCAP_PRE: a command put in front of dumpcap (test_baseline_capture_stop.py's fake); empty here.
DUMPCAP_PRE=${GI_DUMPCAP_PRE:-}
# The capture stops TAIL s after the probe exits (reviewer, 2026-10-20; it ran a fixed
# 600 s before). In the two e3d9437 captures the device ended every connection within
# 24 ms of the client's FIN, and sent nothing on port 80 later than 3 ms after the last.
TAIL=${GI_CAPTURE_TAIL_S:-10}
CAPMAX=${GI_CAPTURE_MAX_S:-900}
IFACE='\Device\NPF_{2D126A09-0299-425A-88DC-51DA04E0C70C}'   # Wi-Fi on NICKY-XPS (dumpcap -D)
mkdir -p "$D"
export GI_RUNS_DIR="${GI_RUNS_DIR:-$(cygpath -m "$PWD/$DATA/runs")}"   # the guards and the probe write their records there
STEPS=${GI_B21_STEPS:-guards probe}
case " $STEPS " in *" guards "*)
py -3.14 -B "$(dirname "$A")/bench_flash_guard.py" --check-accepted > "$D/B21_bench_flash_guard.txt" 2>&1; echo "flash guard exit $?"
py -3.14 -B "$(dirname "$A")/bench_ota_guard.py" --host 192.168.80.1 --allow-ota-write > "$D/B21_bench_ota_guard.txt" 2>&1; echo "ota guard exit $?"
;; esac
case " $STEPS " in *" probe "*)
for body in multipart image; do
  stamp=$(date +%Y%m%dT%H%M%S)
  cap="$D/B21_${C}_${body}_$stamp.pcapng"
  stop="$D/B21_capture_${body}_$stamp.stop"
  py -3.14 -B $A/capture_window.py --stopfile "$stop" --record "$D/B21_capture_${body}_$stamp.json" --max $CAPMAX -- \
    $DUMPCAP_PRE "$DUMPCAP" -q -i "$IFACE" -f "host 192.168.80.1 and tcp port 80" -a duration:$CAPMAX -w "$cap" \
    > "$D/B21_dumpcap_${body}_$stamp.txt" 2>&1 &
  dc=$!
  sleep 3
  extra=""; [ $body = image ] && extra="--image $img"
  t0=$(date +%s.%N)
  py -3.14 -B $A/ota_guard_raw_probe.py --body $body $extra > "$D/B21_probe_${body}_$stamp.txt" 2>&1; echo "probe $body exit $?"
  t1=$(date +%s.%N)
  run=$(ls -t "$GI_RUNS_DIR"/ota_guard_raw_probe_${body}_*.json | head -1)
  sleep $TAIL
  : > "$stop"
  wait $dc; echo "capture $body exit $?"
  rm -f "$stop"
  # the capture's start and stop beside the probe's (reviewer, 2026-10-20)
  echo "{\"probe_start\": $t0, \"probe_end\": $t1, \"tail_s\": $TAIL}" > "$D/B21_probe_window_${body}_$stamp.json"
  py -3.14 -B $A/ota_guard_pcap_join.py --expect=no-rst "$run" "$cap" > "$D/B21_wire_${body}_$C.txt" 2>&1; echo "join $body exit $?"
  tail -3 "$D/B21_wire_${body}_$C.txt"
done
;; esac
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$D"/B21_*.txt
