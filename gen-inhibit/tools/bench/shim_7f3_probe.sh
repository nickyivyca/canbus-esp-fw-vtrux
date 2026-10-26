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
# Usage, from the repo root, after gi_host_run.sh --sync-only for each tree:
#   bash gen-inhibit/tools/bench/shim_7f3_probe.sh gi_host_tester_d74777d gi_host_tester_e998385
#
# A tree whose copy prints no 0x7F3 at all is reported as such: the comparison
# then shows nothing, and must not read as "unchanged".
#
# THE PAYLOADS ARE ALSO DECODED BY NAME (2026-10-24, port plan toolkit item 10): each
# distinct payload goes through the synced tree's gi_diag.py, against the diag_schemas/
# export gi_host_run.sh copies with it, on the schema its own byte states. A schema no
# DBC in the export states is refused (DECODE REFUSED), never decoded with a fallback.
set -euo pipefail
REMOTE="${REMOTE:-madhouse-debian}"
PY="${PY:-python3.13}"
ssh "$REMOTE" "bash -s" < "$(dirname "$0")/host_identity.sh"   # host and compiler; stops here if missing
for dest in "$@"; do
  echo "=== $dest"
  ssh "$REMOTE" "bash -s" "$dest" "$PY" <<'EOS'
set -euo pipefail
dest="$1"
py="$2"
src="$HOME/$dest/test/gen_inhibit_shim"
probe="$HOME/$dest/test/gen_inhibit_shim_7f3probe"
# the tree's gi_diag: tools/bench/ in a copy of the project layout (port step 0d, 2026-10-26),
# tools/gen_inhibit/bench/ in a copy synced before it
gd="$HOME/$dest/tools/bench/gi_diag.py"; [ -e "$gd" ] || gd="$HOME/$dest/tools/gen_inhibit/bench/gi_diag.py"
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
echo "0x7F3 payloads decoded (gi_diag, by name):"
grep -o "PROBE7F3 [0-9a-f]*" "$probe/stderr.txt" | sort -u | sed 's/^PROBE7F3 /7F3 /'   | "$py" -B "$gd" decode || echo "  DECODE REFUSED or failed (above)"
EOS
done
