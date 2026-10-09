#!/usr/bin/env bash
# Build host_runner against machine.cpp and diff every trace/golden pair.
# Run from anywhere; paths are relative to this script. On Windows use WSL:
#   wsl bash "/mnt/c/.../firmware/test/host_diff/diff_all.sh"
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$HERE/../.."
ART="$FW/../../../notes/artifacts/interposer-firmware"
BIN="${TMPDIR:-/tmp}/hr_$$"
g++ -std=c++11 -O2 -I "$FW/src" -o "$BIN" "$HERE/host_runner.cpp" "$FW/src/machine.cpp" || exit 1
fail=0

# Rev 2 (charge-interposer tester, 2026-10-07). The frame diff below compares
# machine.cpp with the STORED golden, which is machine.py's output at the
# time it was written. If machine.py has changed since, machine.cpp can match
# a stale golden while the two cores disagree, and rev 1 printed IDENTICAL.
# So first: every golden must equal what machine.py produces now. Together
# the two make the diff machine.cpp against today's machine.py.
echo "goldens current against machine.py:"
if python3 "$HERE/regen_golden.py" --check "$ART"/*.trace > "${BIN}.chk" 2>&1; then
  echo "  $(tail -1 "${BIN}.chk")"
else
  echo "  STALE OR UNREADABLE GOLDENS -- the frame diff below would be"
  echo "  comparing machine.cpp with an old machine.py:"
  grep -v ' unchanged ' "${BIN}.chk" | head -12
  fail=1
fi
rm -f "${BIN}.chk"

# Negative control: a diff that cannot report DIFFERS is not a check. One
# emitted byte flipped in a copy of the first golden must be seen.
t0="$(ls "$ART"/*.trace | head -1)"
g0="${t0%.trace}.golden"
if grep -q '^E ' "$g0"; then
  awk 'BEGIN{d=0} /^E / && !d {sub(/.$/, ($0 ~ /0$/) ? "1" : "0"); d=1} {print}' \
      "$g0" > "${BIN}.neg"
  if "$BIN" "$t0" | diff -q - "${BIN}.neg" >/dev/null; then
    echo "NEGATIVE CONTROL FAILED: a golden with one byte changed still read IDENTICAL"
    fail=1
  else
    echo "negative control: a one-byte change to $(basename "$g0") is reported"
  fi
  rm -f "${BIN}.neg"
else
  echo "NEGATIVE CONTROL COULD NOT RUN: $(basename "$g0") emits nothing"
  fail=1
fi
echo
for t in "$ART"/*.trace; do
  name="$(basename "$t" .trace)"
  if "$BIN" "$t" | diff -q - "$ART/$name.golden" >/dev/null; then
    echo "$name IDENTICAL"
  else
    echo "$name DIFFERS"
    "$BIN" "$t" | diff - "$ART/$name.golden" | head -6
    fail=1
  fi
done

# The four spec 8.2 status frames are packed by a SEPARATE hand-written
# function in each core, and the loop above cannot see them: it compares
# emitted CAN traffic and state names. Review item C1 lists C++ diagFrames()
# as untested at any level. This diffs the two packings directly, with no
# golden -- both sides are generated now, so neither can be stale.
echo
echo "diagnostics (spec 8.2), machine.py vs machine.cpp:"
for t in "$ART"/*.trace; do
  name="$(basename "$t" .trace)"
  if "$BIN" --diag "$t" | diff -q - <(python3 "$HERE/diag_stream.py" "$t")       >/dev/null; then
    echo "  $name diag IDENTICAL"
  else
    echo "  $name diag DIFFERS"
    "$BIN" --diag "$t" | diff - <(python3 "$HERE/diag_stream.py" "$t") | head -6
    fail=1
  fi
done

rm -f "$BIN"
exit $fail
