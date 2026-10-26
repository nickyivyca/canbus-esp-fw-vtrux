#!/usr/bin/env bash
# What the Wi-Fi captures of ota_pcap.sh show about an OTA upload, for the
# reviewer's open 128 KiB-reset observation (2026-10-16). Descriptive only: it
# prints what is on the wire and gives no verdict. Gen-inhibit tester, 2026-10-17.
#
#   bash ota_pcap_stall.sh <failed stem> [<good stem> ...]   (stems without .pcapng)
#
# For every capture: the upload connection's device ACKs (count, highest ack, lowest
# window, the first ack advertising less than one segment, and the longest gap
# between device ACKs). For the first capture also: every packet of the upload
# connection, then every packet from 1 s after the upload's last device ACK to the
# device's next answer. For each further capture: the packets around its first
# small-window ACK, to show how a good upload resumes. Last for each capture: that
# pause timed from the upload's SYN (the PAUSE line). tshark reads files only.
set -u
T="/c/Program Files/Wireshark/tshark.exe"
S=$(mktemp)
F='-T fields -e frame.number -e frame.time_relative -e ip.src -e tcp.srcport -e tcp.dstport -e tcp.flags.str -e tcp.seq -e tcp.ack -e tcp.len -e tcp.window_size -e _ws.expert.message'
summary() {   # summary <pcap>: device ACKs on the stream that reached the highest ack
  "$T" -r "$1" -Y "ip.src==192.168.80.1 && tcp.srcport==80 && tcp.len==0 && tcp.flags.syn==0 && tcp.flags.fin==0" \
    -T fields -e tcp.stream -e frame.time_relative -e tcp.ack -e tcp.window_size 2>/dev/null > "$S"
  py -3.14 -B - "$(basename "$1")" "$(cygpath -m "$S")" <<'PY'
import sys, collections
rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[2]) if l.strip()]
by = collections.defaultdict(list)
for st, t, a, w in rows:
    by[st].append((float(t), int(a), int(w)))
st = max(by, key=lambda k: max(a for _, a, _ in by[k]))
v = sorted(by[st])
g = max(((v[i + 1][0] - v[i][0], v[i][1], v[i][0]) for i in range(len(v) - 1)), default=(0, 0, 0))
low = [x for x in v if x[2] < 1440]
print("%s  stream %s: device acks %d, highest ack %d, lowest window %d, first window < 1 segment at ack %s (t %s s), "
      "longest gap between device acks %.2f s after ack %d (t %.2f s), last device ack t %.2f s"
      % (sys.argv[1], st, len(v), max(a for _, a, _ in v), min(w for *_, w in v),
         low[0][1] if low else None, "%.2f" % low[0][0] if low else None, g[0], g[1], g[2], v[-1][0]))
la = low[0][1] if low else 0
print("STREAM %s AFTER %.3f ALO %d AHI %d SLO %d SHI %d LOW %d LOWHI %d" % (st, v[-1][0] + 1, la - 3000, la + 4500, la - 3000, la + 3000, la, la + 90))
PY
}
first=1
for stem in "$@"; do
  p="$stem.pcapng"
  echo "== $(basename "$p") ($(stat -c %s "$p") bytes)"
  out=$(summary "$p"); echo "$out" | grep -v '^STREAM'
  set -- $(echo "$out" | grep '^STREAM'); st=$2; after=$4; alo=$6; ahi=$8; slo=${10}; shi=${12}; low=${14}; lowhi=${16}
  if [ $first = 1 ]; then
    echo "-- every packet of the upload connection (stream $st)"
    "$T" -r "$p" -Y "tcp.stream==$st" $F 2>/dev/null
    echo "-- every packet on other connections from 1 s after the upload's last device ack (t > $after s) to the device's first answer with data"
    "$T" -r "$p" -Y "frame.time_relative > $after && tcp.stream != $st" $F 2>/dev/null | awk -F'\t' '{print} $3=="192.168.80.1" && $9>0 {exit}'
    first=0
  else
    echo "-- around the first small-window ack (device acks $alo-$ahi, laptop seq $slo-$shi)"
    "$T" -r "$p" -Y "tcp.stream==$st && ((ip.src==192.168.80.1 && tcp.ack>=$alo && tcp.ack<=$ahi) || (ip.src==192.168.80.2 && tcp.seq>=$slo && tcp.seq<=$shi))" $F 2>/dev/null
  fi
  # the pause, timed from the upload's SYN: the first 90-byte window on the
  # device's ACK of the first small-window ack, and the first window over 1000 after it
  "$T" -r "$p" -Y "tcp.stream==$st && ((tcp.flags.syn==1 && tcp.flags.ack==0) || (ip.src==192.168.80.1 && tcp.ack>=$low && tcp.ack<=$lowhi))" \
    -T fields -e frame.time_relative -e tcp.flags.syn -e tcp.ack -e tcp.window_size 2>/dev/null |
    awk -F'\t' '$2==1{syn=$1} $2==0 && $4<1440 && w==""{w=$1} $2==0 && $4>1000 && w!="" && up==""{up=$1}
      END{printf "PAUSE from the SYN: small window +%.2f s, reopened %s\n", w-syn, (up==""?"never":sprintf("+%.2f s", up-syn))}'
done
rm -f "$S"
