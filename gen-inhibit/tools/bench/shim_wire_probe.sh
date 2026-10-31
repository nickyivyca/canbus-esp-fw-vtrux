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
# It builds what the shim's Makefile builds, with the probe in shim_test.c's place
# (the probe #includes it): since port step 1b (2026-10-31) the CAN code is the
# shared can_port and twai_port, on twai_port's host fake, which replaced
# main/can.c and the shim's own driver model (fake_twai.c, now shim_harness.c).
# They come from <tree>/shared/, which gi_host_run.sh syncs from the pinned
# checkout. main/ is <tree>/firmware/main since port step 0d (2026-10-26).
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
M=../../firmware/main
S="$HOME/$1/shared"
F="$S/twai_port/test"
for d in "$M" "$S/wican" "$S/can_port" "$F"; do
  [ -d "$d" ] || { echo "missing $d: run gi_host_run.sh --sync-only first" >&2; exit 1; }
done
gcc -std=c11 -O1 -g -Wall -Wno-unused-parameter -Wno-unused-function \
    -I. -Imock -I"$M" -I"$S/wican" -I"$S/can_port/include" -I"$S/twai_port/include" \
    -I"$F" -I"$F/include" -I"$F/host" \
    -o shim_wire_probe shim_wire_probe.c shim_harness.c fake_can.c fake_port.c "$F/twai_fake.c" \
    "$M/gen_inhibit.c" "$M/gen_inhibit_core.c" "$M/gi_sched.c" \
    "$S/can_port/can_port.c" "$S/can_port/can_stock.c" \
    "$S/twai_port/twai_port.c" "$S/twai_port/twai_port_ll_esp32c3.c" \
    -pthread -Wl,--wrap=twai_driver_install
echo "built"
EOS
for name in "$@"; do
  out="$HERE/runs/shim_wire_probe_${name}_${STAMP}.txt"
  echo "$ID" > "$out"
  ssh "$REMOTE" "cd $DEST/test/gen_inhibit_shim_wireprobe && timeout 120 ./shim_wire_probe $name" >> "$out" \
    || echo "$name: exit $? (124 = the 120 s timeout)"
  echo "$name -> $out ($(grep -c '^W ' "$out") wire frames; $(sed -n 2p "$out"))"
done
