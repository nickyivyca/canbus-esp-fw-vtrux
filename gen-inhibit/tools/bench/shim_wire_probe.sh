#!/usr/bin/env bash
#
# Build shim_wire_probe.c on madhouse-debian against a tree synced by
# gi_host_run.sh --sync-only, replay the named host scenarios through the shim,
# and save each one's wire listing here under runs/. Gen-inhibit tester,
# 2026-10-16 (reviewer: liveflag-mmode's diag gap, in emulation first).
#
# The synced shim folder is copied to <tree>/test/gen_inhibit_shim_wireprobe
# (a sibling of gen_inhibit_host, so replay_scn finds the host scenarios) and the
# probe is built there; the synced tree's own files are not touched.
#
# Usage, from the repo root, after gi_host_run.sh --sync-only and --scenarios:
#   DEST=gi_host_tester_c65c45e bash gen-inhibit/tools/bench/shim_wire_probe.sh \
#       disable-clears-live-flag rearm-keeps-section6-latch
set -euo pipefail
REMOTE="${REMOTE:-madhouse-debian}"
DEST="${DEST:?set DEST to the synced tree under ~ on the remote}"
HERE="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%dT%H%M%S)"
ID="$(ssh "$REMOTE" "bash -s" < "$HERE/host_identity.sh")"   # host and compiler; stops here if missing
echo "$ID"
scp -q "$HERE/shim_wire_probe.c" "$REMOTE:$DEST/test/shim_wire_probe.c"
ssh "$REMOTE" "bash -s" "$DEST" <<'EOS'
set -euo pipefail
t="$HOME/$1/test"
rm -rf "$t/gen_inhibit_shim_wireprobe"
cp -r "$t/gen_inhibit_shim" "$t/gen_inhibit_shim_wireprobe"
cp "$t/shim_wire_probe.c" "$t/gen_inhibit_shim_wireprobe/"
cd "$t/gen_inhibit_shim_wireprobe"
gcc -std=c11 -O1 -g -Wall -Wno-unused-parameter -Wno-unused-function -I. -Imock -I../../main \
    -o shim_wire_probe shim_wire_probe.c fake_twai.c fake_can.c \
    ../../main/gen_inhibit.c ../../main/gen_inhibit_core.c ../../main/gi_sched.c ../../main/can.c \
    -pthread
echo "built"
EOS
for name in "$@"; do
  out="$HERE/runs/shim_wire_probe_${name}_${STAMP}.txt"
  echo "$ID" > "$out"
  ssh "$REMOTE" "cd $DEST/test/gen_inhibit_shim_wireprobe && timeout 120 ./shim_wire_probe $name" >> "$out" \
    || echo "$name: exit $? (124 = the 120 s timeout)"
  echo "$name -> $out ($(grep -c '^W ' "$out") wire frames; $(sed -n 2p "$out"))"
done
