#!/usr/bin/env bash
# Baseline B21 (baseline_run_list.md): flash and OTA writes refused while armed in
# each non-OFF mode, as on c65c45e and c6fdab5. Gen-inhibit tester, 2026-10-16
# (run by hand before; this records the commands).
#   1. bench_flash_guard.py --check-accepted   (firmware repo gen-inhibit/tools)
#   2. bench_ota_guard.py --allow-ota-write    (OFF gets a deliberately invalid image)
#   3. ota_guard_raw_probe.py, 5 reps per non-OFF mode, the 184-byte multipart body
#      and then the whole running image, each record judged at the client by
#      ota_guard_client_judge.py into B21_client_<body>_<commit>.txt (spec 9.3, topic (E),
#      2026-11-19: no Wi-Fi capture and no pcap join since)
#
#   GI_COMMIT=e3d9437 REVERSE_IT_DIR=<reverse-it root> GI_WICAN_SSID=... [GI_B21_STEPS="guards probe"] bash baseline_b21.sh <running image .bin>
# Before a first run, read the INCIDENT in plans/gen-inhibit-wican-implementation.md
# (~line 1672): no write in the OFF half may leave a bad config.
set -u
img=${1:?the running image}
H="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$H/bench_host.sh"; A=$(hpath "$H")   # this folder, gen-inhibit/tools/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
mkdir -p "$D"
export GI_RUNS_DIR="${GI_RUNS_DIR:-$(hpath "$PWD/$DATA/runs")}"   # the guards and the probe write their records there
STEPS=${GI_B21_STEPS:-guards probe}
# The device reached through the run's ota block first: its backend joins the WiCAN's hotspot (the
# phone, with the host's /32 route to the device through it) and the page must answer after the join
# (wican_reboot.ota_route; gen-inhibit reviewer, topic (E), 2026-11-19: the guard steps go through the
# ota block, not whatever route answers). Unset, refused, not joined or no page: B21 is NOT-RUN with
# the reason, never judged on an unread page (run cb08's guards failed on "returned None").
if ! $PY -B - "$A" "$D/B21_not_run.txt" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import wican_reboot as W
ok, why = W.ota_route()
print("ota route: %s" % why)
if not ok:
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write("NOT-RUN: the device is not reached through the run's ota block: %s\n" % why)
    sys.exit(1)
PY
then
  echo "B21 not run: the device is not reached through the run's ota block"
  exit 0
fi
case " $STEPS " in *" guards "*)
$PY -B "$(dirname "$A")/bench_flash_guard.py" --check-accepted > "$D/B21_bench_flash_guard.txt" 2>&1; echo "flash guard exit $?"
$PY -B "$(dirname "$A")/bench_ota_guard.py" --host 192.168.80.1 --allow-ota-write > "$D/B21_bench_ota_guard.txt" 2>&1; echo "ota guard exit $?"
;; esac
case " $STEPS " in *" probe "*)
# Judged at the client (spec 9.3, user 2026-11-19; the gen-inhibit reviewer's topic (E)): the probe
# keeps every byte of each reply and a page read around each request, and ota_guard_client_judge
# judges its record. No capture, no pcap join.
for body in multipart image; do
  stamp=$(date +%Y%m%dT%H%M%S)
  extra=""; [ $body = image ] && extra="--image $img"
  $PY -B $A/ota_guard_raw_probe.py --body $body $extra > "$D/B21_probe_${body}_$stamp.txt" 2>&1; echo "probe $body exit $?"
  run=$(sed -n 's/^.*; wrote \(.*ota_guard_raw_probe_[a-z]*_[0-9T]*\.json\)\r*$/\1/p' "$D/B21_probe_${body}_$stamp.txt")
  if [ -n "$run" ] && [ -f "$run" ]; then
    $PY -B $A/ota_guard_client_judge.py "$run" > "$D/B21_client_${body}_$C.txt" 2>&1; echo "client $body exit $?"
  else
    echo "verdict ERROR: the probe wrote no record (${run:-no wrote line})" > "$D/B21_client_${body}_$C.txt"
    echo "client $body: no record"
  fi
  tail -1 "$D/B21_client_${body}_$C.txt"
done
;; esac
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$D"/B21_*.txt
