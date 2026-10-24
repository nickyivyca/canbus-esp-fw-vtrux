#!/usr/bin/env bash
# Negative check for verify_longarm_build.sh point 1 (2026-10-20): that cases 33 and
# 53 really check the stimulus against stimulus.fnv. One digit of one frame of the
# built replay-longarm-300s-all.scn is changed (a copy is kept and restored), and
# shim_test must then fail cases 33 and 53. Under WSL, after verify_longarm_build.sh,
# with REVERSE_IT_DIR passed through WSLENV:
#   wsl.exe -e bash <this script> <clone>
# Gen-inhibit tester. Since 2026-10-24 a WSL build on NICKY-XPS is a self-check only
# (user, through the reviewer); the first output line is host_identity.sh's host and
# compiler.
set -u
bash "$(cd "$(dirname "$0")" && pwd)/host_identity.sh" || exit 2
cd "${1:?clone}/test/gen_inhibit_shim" || exit 2
SCN=scenarios/replay-longarm-300s-all.scn
[ -e "$SCN" ] || { echo "no $SCN"; exit 2; }
orig=$(sha256sum < "$SCN" | cut -d' ' -f1)
cp -p "$SCN" "$SCN.orig"
make shim_test > /tmp/vlf_make.txt 2>&1 || { echo "make failed"; cat /tmp/vlf_make.txt; exit 2; }
# the 1000th data line that is not a comment: change its last hex digit
python3 - "$SCN" <<'PY'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().split("\n")
idx = [i for i, l in enumerate(lines) if l and not l.startswith("#")][1000]
l = lines[idx]
c = l[-1]
lines[idx] = l[:-1] + ("0" if c != "0" else "1")
open(p, "w", encoding="utf-8").write("\n".join(lines))
print("changed line %d: %r -> %r" % (idx + 1, l[-20:], lines[idx][-20:]))
PY
echo "sha256 before $orig, after $(sha256sum < "$SCN" | cut -d' ' -f1)"
./shim_test > /tmp/vlf_shim.txt 2>&1; rc=$?
echo "shim_test exit $rc (mutated stimulus)"
grep -n -i -E "stimulus|fnv|FAIL" /tmp/vlf_shim.txt | grep -v ESP_FAIL | head -12
f33=$(awk '/^  case 33:/{on=1} /^  case 34:/{on=0} on' /tmp/vlf_shim.txt | grep -c -i "FAIL")
f53=$(awk '/^  case 53:/{on=1} /^  case 54:/{on=0} on' /tmp/vlf_shim.txt | grep -c -i "FAIL")
mv -f "$SCN.orig" "$SCN"
back=$(sha256sum < "$SCN" | cut -d' ' -f1)
echo "restored: sha256 $back ($([ "$back" = "$orig" ] && echo same || echo DIFFERENT))"
if [ "$rc" != 0 ] && [ "$f33" -gt 0 ] && [ "$f53" -gt 0 ] && [ "$back" = "$orig" ]; then
  echo "NEGATIVE: PASS shim_test fails on a changed stimulus (case 33: $f33, case 53: $f53 FAIL lines)"
else echo "NEGATIVE: FAIL rc $rc, case 33 $f33, case 53 $f53, restored $([ "$back" = "$orig" ] && echo ok || echo no)"; fi
cd ../.. && echo "git status: [$(git status --porcelain)]"
