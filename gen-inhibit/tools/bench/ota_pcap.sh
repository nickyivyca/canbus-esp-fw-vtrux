#!/usr/bin/env bash
# An ordinary bench_flash.py OTA with an unelevated capture of the laptop's Wi-Fi
# around it (host 192.168.80.1, TCP port 80), for the reviewer's 128 KiB-reset
# observation (2026-10-16): if an OTA stops short again, the pcap shows who reset
# the connection and what came before. Gen-inhibit tester. Changes nothing in the
# OTA itself.
#
#   GI_WICAN_SSID=... bash ota_pcap.sh <image .bin> <out stem, e.g. runs/x/ota_rt_<stamp>>
# Writes <stem>.txt, <stem>.json (bench_flash) and <stem>.pcapng; the SSID is
# redacted from the first two. The capture starts 3 s before the upload and stops
# itself after 200 s (an OTA with its 90 s settle reads takes about 120 s); the
# script waits for it rather than killing it, so the file is closed cleanly.
set -u
img="$1"; stem="$2"
: "${GI_WICAN_SSID:?set GI_WICAN_SSID}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bench_host.sh"   # $PY, hpath, capture defaults (2026-11-12)
DUMPCAP=${GI_DUMPCAP:-$GI_HOST_DUMPCAP}
IFACE=${GI_CAPTURE_IFACE:-$GI_HOST_IFACE}   # none on Linux
need_iface
BF="$(hpath "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)")/bench_flash.py"   # gen-inhibit/tools
"$DUMPCAP" -q -i "$IFACE" -f "host 192.168.80.1 and tcp port 80" -a duration:200 -w "$stem.pcapng" 2>"$stem.dumpcap.txt" &
cap=$!
sleep 3
$PY -B "$BF" --bin "$img" --flash --record "$stem.json" > "$stem.txt" 2>&1
rc=$?
wait "$cap" 2>/dev/null
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$stem.txt" "$stem.json"
echo "bench_flash exit $rc; capture $(stat -c %s "$stem.pcapng" 2>/dev/null || echo none) bytes"
grep -E "upload ended|VERDICT" "$stem.txt" | cut -c1-250
exit $rc
