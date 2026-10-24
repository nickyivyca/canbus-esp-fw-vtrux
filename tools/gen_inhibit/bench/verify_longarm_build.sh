#!/usr/bin/env bash
# Verify the shim's long-arm stimulus build (test/gen_inhibit_shim/build_longarm.py
# and the Makefile's order-only longarm step), reviewer's five points, 2026-10-20.
# Runs under WSL on NICKY-XPS (not madhouse-debian), in a clean clone of the commit:
#
#   REVERSE_IT_DIR=<windows reverse-it root> WSLENV=REVERSE_IT_DIR/p \
#     wsl.exe -e bash <this script, as a /mnt path> <clone> <commit>
#
# 1. make with REVERSE_IT_DIR set builds scenarios/replay-longarm-300s-all.scn
#    (23,241,533 bytes); shim_test exits 0, cases 33 and 53 passing.
# 2. a second make with nothing changed reports "up to date" and does not rebuild.
# 3. after touching ../gen_inhibit_host/from_capture.py the next make rebuilds.
# 4. REVERSE_IT_DIR unset (and shim_test removed): make stops naming the variable,
#    and shim_test is neither built nor run.
# 5. git status is clean afterwards (the .scn and any .tmp ignored).
# Each point prints "POINT n: PASS|FAIL <why>". Gen-inhibit tester.
# Since 2026-10-24 a WSL build on NICKY-XPS is a self-check only (user, through the
# reviewer); the second output line is host_identity.sh's host and compiler.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CL=${1:?clone}; C=${2:?commit}
cd "$CL" || exit 2
[ "$(git rev-parse --short=7 HEAD)" = "$C" ] || { echo "clone is not at $C"; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "clone not clean at the start"; git status --short; exit 2; }
echo "# commit $(git rev-parse HEAD), $(date -Is), $(uname -sr), $(gcc --version | head -1), $(python3 --version 2>&1)"
bash "$HERE/host_identity.sh" || exit 2
echo "# REVERSE_IT_DIR set: $([ -n "${REVERSE_IT_DIR:-}" ] && echo yes || echo no)"
cd test/gen_inhibit_shim
SCN=scenarios/replay-longarm-300s-all.scn
pt() { echo "POINT $1: $2"; }

echo "=== 1: make"
make 2>&1 | tee /tmp/vl_make1.txt; rc=${PIPESTATUS[0]}
sz=$(stat -c %s "$SCN" 2>/dev/null || echo none)
echo "make exit $rc; $SCN size $sz; sha256 $(sha256sum < "$SCN" 2>/dev/null | cut -d' ' -f1)"
./shim_test > /tmp/vl_shim.txt 2>&1; src=$?
echo "shim_test exit $src; $(grep -c . /tmp/vl_shim.txt) lines"
grep -n -E "^(case|CASE)? ?(33|53)\b|\b(33|53)[ :.]" /tmp/vl_shim.txt | head -8
tail -3 /tmp/vl_shim.txt
if [ "$rc" = 0 ] && [ "$sz" = 23241533 ] && grep -q "^building $SCN (missing)" /tmp/vl_make1.txt && [ "$src" = 0 ]; then
  pt 1 "PASS built (missing), 23241533 bytes, shim_test exit 0"
else pt 1 "FAIL make $rc, size $sz, shim_test $src"; fi

echo "=== 2: make again, nothing changed"
m0=$(stat -c %y "$SCN")
make 2>&1 | tee /tmp/vl_make2.txt; rc=${PIPESTATUS[0]}
m1=$(stat -c %y "$SCN")
if [ "$rc" = 0 ] && grep -q "$SCN is up to date" /tmp/vl_make2.txt && ! grep -q "^building" /tmp/vl_make2.txt && [ "$m0" = "$m1" ]; then
  pt 2 "PASS up to date, not rebuilt (mtime $m1)"
else pt 2 "FAIL make $rc, mtime $m0 -> $m1"; fi

echo "=== 3: touch from_capture.py, make"
sleep 1; touch ../gen_inhibit_host/from_capture.py
make 2>&1 | tee /tmp/vl_make3.txt; rc=${PIPESTATUS[0]}
m2=$(stat -c %y "$SCN"); sz=$(stat -c %s "$SCN")
if [ "$rc" = 0 ] && grep -q "^building $SCN (older than from_capture.py)" /tmp/vl_make3.txt && [ "$m2" != "$m1" ] && [ "$sz" = 23241533 ]; then
  pt 3 "PASS rebuilt (older than from_capture.py), $sz bytes"
else pt 3 "FAIL make $rc, mtime $m1 -> $m2, size $sz"; fi

echo "=== 4: REVERSE_IT_DIR unset, shim_test removed"
make clean
env -u REVERSE_IT_DIR make 2>&1 | tee /tmp/vl_make4.txt; rc=${PIPESTATUS[0]}
if [ "$rc" != 0 ] && grep -q "REVERSE_IT_DIR is not set" /tmp/vl_make4.txt && [ ! -e shim_test ] && ! grep -q "shim_test.c" /tmp/vl_make4.txt; then
  pt 4 "PASS make exit $rc, names REVERSE_IT_DIR, no shim_test"
else pt 4 "FAIL make $rc, shim_test $([ -e shim_test ] && echo present || echo absent)"; fi
ls -la . | grep -E "shim_test" || echo "(no shim_test binaries)"

echo "=== 5: git status"
cd "$CL"
st=$(git status --porcelain); ig=$(git status --porcelain --ignored | grep '^!!')
echo "status: [${st}]"; echo "ignored: $ig"
if [ -z "$st" ]; then pt 5 "PASS clean"; else pt 5 "FAIL not clean"; fi
