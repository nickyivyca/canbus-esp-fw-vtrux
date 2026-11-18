#!/usr/bin/env bash
#
# The shim's 0x7F3 payload, before and after a change, on madhouse-debian.
#
# WHY. The shim suite checks which diag IDs are sent, never the 0x7F3 payload,
# so "the shim's 0x7F3 output is unchanged" (reviewer, 2026-10-13, for e998385
# "the name enters the build once") is not shown by the suite passing. This
# copies each synced tree's shim to a scratch directory beside it, adds ONE
# print to the copy where every frame the TWAI driver accepted is recorded,
# builds and runs it, and prints the distinct 0x7F3 payloads with their counts.
# The synced trees themselves are not touched; nothing is committed.
#
# Usage, from the repo root, after gi_host_run.sh --sync-only for each tree:
#   RROOT=<remote reverse-it root> bash gen-inhibit/tools/bench/shim_7f3_probe.sh \
#       gi_host_tester_d74777d gi_host_tester_e998385
#
# A tree whose copy prints no 0x7F3 at all is reported as such: the comparison
# then shows nothing, and must not read as "unchanged".
#
# THE RECORD SITE FOLLOWS THE TREE (2026-11-17, gen-inhibit reviewer's tracker item: the
# probe still looked for fake_twai.c, which port step 1b removed, so on every tree since
# it stopped at "probe site found 0 times"). A tree whose shim has its own fake_twai.c
# (before 1b) gets the print where that fake records each transmitted frame, as before.
# A tree without one runs on twai_port's shared fake (twai_port T10), and gets it in
# shim_harness.c's on_accepted(), the fake's tx-accepted hook: every frame the driver
# accepted (twai_fake.h), the same frames the old record held. The site line must be
# there exactly once, as a whole line, or the probe stops.
#
# THE COPY BUILDS AS gi_host_run.sh --shim BUILDS THE SHIM (2026-11-17). Since 1a the
# shim compiles against vtrux/wican, can_port and twai_port, which the sync puts in
# ~/<tree>/shared/, so make gets WICANDIR there when the tree has it. The make target
# builds the long-arm stimulus first (build_longarm.py), which needs REVERSE_IT_DIR on
# the host and the pinned canre: RROOT is the reverse-it root on the host, as
# gi_host_run.sh takes it, with no default (a leading ~/ is the host's $HOME), and
# canre_pin.py install runs first when the tree has it. The copy is cleaned before the
# build, so a binary copied from the synced tree is never run in place of the probe's.
#
# THE PAYLOADS ARE ALSO DECODED BY NAME (2026-10-24, port plan toolkit item 10): each
# distinct payload goes through the synced tree's gi_diag.py, against the diag_schemas/
# export gi_host_run.sh copies with it, on the schema its own byte states. A schema no
# DBC in the export states is refused (DECODE REFUSED), never decoded with a fallback.
#
# WHERE IT RUNS (host_target.sh, 2026-11-12, gen-inhibit reviewer): over ssh to REMOTE,
# or, when REMOTE names this host (madhouse-debian, which cannot ssh to itself), here
# without ssh. The first output line says which.
#
# Tests: test_host_target.py part 6 (the commands, with fakes) and test_shim_7f3_probe.py
# (a real synced tree, built and run here).
set -euo pipefail
REMOTE="${REMOTE:-madhouse-debian}"
PY="${PY:-python3.13}"
RROOT="${RROOT:?set RROOT to the reverse-it root on the host (the shim build needs it)}"
. "$(dirname "$0")/host_target.sh"
host_target || exit 2
echo "$HOST_MODE"
on_host "bash -s" < "$(dirname "$0")/host_identity.sh"   # host and compiler; stops here if missing
for dest in "$@"; do
  echo "=== $dest"
  on_host "bash -s $(printf '%q %q %q' "$dest" "$PY" "$RROOT")" <<'EOS'
set -euo pipefail
dest="$1"
py="$2"
rroot="$3"
case "$rroot" in "~/"*) rroot="$HOME/${rroot#"~/"}" ;; esac
export REVERSE_IT_DIR="$rroot"
src="$HOME/$dest/test/gen_inhibit_shim"
probe="$HOME/$dest/test/gen_inhibit_shim_7f3probe"
# the tree's gi_diag: tools/bench/ in a copy of the project layout (port step 0d, 2026-10-26),
# tools/gen_inhibit/bench/ in a copy synced before it
gd="$HOME/$dest/tools/bench/gi_diag.py"; [ -e "$gd" ] || gd="$HOME/$dest/tools/gen_inhibit/bench/gi_diag.py"
rm -rf "$probe"
cp -r "$src" "$probe"
if [ -e "$probe/fake_twai.c" ]; then
  site="$probe/fake_twai.c"     # before port step 1b: the shim's own fake records each sent frame
  line='    if (g_nsent < SENTMAX) g_sent[g_nsent++] = *f;'
  print='    if (id == 0x7F3) fprintf(stderr, "PROBE7F3 %02x%02x%02x%02x%02x%02x%02x%02x\n", f->data[0], f->data[1], f->data[2], f->data[3], f->data[4], f->data[5], f->data[6], f->data[7]);'
else
  site="$probe/shim_harness.c"  # since 1b: twai_port's shared fake calls this for each accepted frame
  line='    check_wire_accepted(m);'
  print='    if (m->identifier == 0x7F3) fprintf(stderr, "PROBE7F3 %02x%02x%02x%02x%02x%02x%02x%02x\n", m->data[0], m->data[1], m->data[2], m->data[3], m->data[4], m->data[5], m->data[6], m->data[7]);'
fi
n=$(grep -cxF -- "$line" "$site" || true)
[ "$n" = 1 ] || { echo "probe site found $n times in ${site##*/}, expected 1"; exit 1; }
L="$line" P="$print" awk '{ print } $0 == ENVIRON["L"] { print ENVIRON["P"] }' "$site" > "$site.probe"
mv "$site.probe" "$site"
grep -n "PROBE7F3" "$site" | head -2
[ ! -e "$HOME/$dest/tools/bench/canre_pin.py" ] || "$py" -B "$HOME/$dest/tools/bench/canre_pin.py" install
mk=(make)
[ ! -d "$HOME/$dest/shared/wican" ] || mk+=("WICANDIR=$HOME/$dest/shared/wican")
cd "$probe"
"${mk[@]}" -s clean >/dev/null
"${mk[@]}" -s shim_test >/dev/null 2>&1 || "${mk[@]}" shim_test
./shim_test >/dev/null 2>"$probe/stderr.txt" || echo "shim_test exit $?"
echo "0x7F3 payloads (count payload):"
grep -o "PROBE7F3 [0-9a-f]*" "$probe/stderr.txt" | sort | uniq -c | sort -rn || echo "  NONE -- no 0x7F3 was transmitted"
echo "0x7F3 payloads decoded (gi_diag, by name):"
grep -o "PROBE7F3 [0-9a-f]*" "$probe/stderr.txt" | sort -u | sed 's/^PROBE7F3 /7F3 /'   | "$py" -B "$gd" decode || echo "  DECODE REFUSED or failed (above)"
EOS
done
