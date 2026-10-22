#!/usr/bin/env bash
#
# The shim's 0x7F3 payload, before and after a change, on madhouse-debian.
#
# WHY. The shim suite checks which diag IDs are sent, never the 0x7F3 payload,
# so "the shim's 0x7F3 output is unchanged" (reviewer, 2026-10-13, for e998385
# "the name enters the build once") is not shown by the suite passing. This
# copies each synced tree's shim to a scratch directory beside it, adds ONE
# print to the copy's fake_twai.c where every transmitted frame is recorded,
# builds and runs it, and prints the distinct 0x7F3 payloads with their counts.
# The synced trees themselves are not touched; nothing is committed.
#
# Usage, from the project root, after gi_host_run.sh --sync-only for each tree:
#   bash tools/gen_inhibit/bench/shim_7f3_probe.sh gi_host_tester_d74777d gi_host_tester_e998385
#
# A tree whose copy prints no 0x7F3 at all is reported as such: the comparison
# then shows nothing, and must not read as "unchanged".
set -euo pipefail
REMOTE="${REMOTE:-madhouse-debian}"
for dest in "$@"; do
  echo "=== $dest"
  ssh "$REMOTE" "bash -s" "$dest" <<'EOS'
set -euo pipefail
dest="$1"
src="$HOME/$dest/test/gen_inhibit_shim"
probe="$HOME/$dest/test/gen_inhibit_shim_7f3probe"
rm -rf "$probe"
cp -r "$src" "$probe"
line='    if (g_nsent < SENTMAX) g_sent[g_nsent++] = *f;'
n=$(grep -cF "$line" "$probe/fake_twai.c" || true)
[ "$n" = 1 ] || { echo "probe site found $n times, expected 1"; exit 1; }
sed -i "s|^    if (g_nsent < SENTMAX) g_sent\[g_nsent++\] = \*f;|&\n    if (id == 0x7F3) fprintf(stderr, \"PROBE7F3 %02x%02x%02x%02x%02x%02x%02x%02x\\\\n\", f->data[0], f->data[1], f->data[2], f->data[3], f->data[4], f->data[5], f->data[6], f->data[7]);|" "$probe/fake_twai.c"
grep -n "PROBE7F3" "$probe/fake_twai.c" | head -2
cd "$probe"
make -s shim_test >/dev/null 2>&1 || make shim_test
./shim_test >/dev/null 2>"$probe/stderr.txt" || echo "shim_test exit $?"
echo "0x7F3 payloads (count payload):"
grep -o "PROBE7F3 [0-9a-f]*" "$probe/stderr.txt" | sort | uniq -c | sort -rn || echo "  NONE -- no 0x7F3 was transmitted"
EOS
done
