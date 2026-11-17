#!/usr/bin/env bash
# An ordinary OTA with a capture around it (host 192.168.80.1, TCP port 80), for the
# reviewer's 128 KiB-reset observation (2026-10-16): if an OTA stops short again, the pcap
# shows who reset the connection and what came before. Gen-inhibit tester. Changes nothing
# in the OTA itself.
#
#   GI_WICAN_SSID=... GI_OTA_SETTINGS=<settings.ota as JSON> bash ota_pcap.sh <image .bin> <out stem>
# Writes <stem>.txt (the console), <stem>.json (the record) and, when the capture runs,
# <stem>.pcapng; the SSID is masked in the first two.
#
# From 2026-11-16 (the gen-inhibit reviewer's job (1)) the upload is gi_ota.py: the pinned
# core's flasher (vtrux_bench.run.ota.flash_once, bench_flash's --flash moved, FLASH-SPEC
# O1-O12), joined through the Wi-Fi backend settings.ota chooses (the phone over adb on
# madhouse-debian), and the capture is the core's own (FLASH-SPEC O12: message-only, of
# settings.ota.capture_iface; with the phone, the host's RNDIS link, not the air). A missing
# dumpcap or capture right is noted in the console, not refused. Before, this script ran
# bench_flash.py (netsh) with its own dumpcap.
set -u
img="$1"; stem="$2"
: "${GI_WICAN_SSID:?set GI_WICAN_SSID}"
: "${GI_OTA_SETTINGS:?set GI_OTA_SETTINGS (the suite sets it from settings.ota)}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bench_host.sh"   # $PY, hpath (2026-11-12)
GO="$(hpath "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")/gi_ota.py"   # gen-inhibit/tools/bench
$PY -B "$GO" --bin "$img" --record "$stem.json" > "$stem.txt" 2>&1
rc=$?
[ -f "$stem.json" ] && sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$stem.json"
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$stem.txt"
echo "gi_ota exit $rc; capture $(stat -c %s "$stem.pcapng" 2>/dev/null || echo none) bytes"
grep -E "upload ended|VERDICT" "$stem.txt" | cut -c1-250
exit $rc
