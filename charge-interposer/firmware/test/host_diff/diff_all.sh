#!/usr/bin/env bash
# Build host_runner against machine.cpp and diff every trace/golden pair.
# Run from anywhere; paths are relative to this script. On Windows use WSL:
#   wsl bash "/mnt/c/.../firmware/test/host_diff/diff_all.sh"
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$HERE/../.."
# The traces and goldens stay in SeaDrive; REVERSE_IT_DIR is its reverse-it
# root (see charge-interposer/paths.py). Under WSL: WSLENV=REVERSE_IT_DIR/p.
: "${REVERSE_IT_DIR:?REVERSE_IT_DIR is not set -- see charge-interposer/paths.py}"
ART="$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware"
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

# spec 9.1 pinned stimulus: the capture-derived traces must match the pins
# held in the repo (charge-interposer/capture_pins.py), and no trace may be
# capture-derived without one.
echo "capture pins:"
if python3 "$HERE/../../../capture_pins.py" --dir "$ART" > "${BIN}.pin" 2>&1; then
  echo "  $(tail -1 "${BIN}.pin")"
else
  sed 's/^/  /' "${BIN}.pin"
  fail=1
fi
rm -f "${BIN}.pin"
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

# Spec 8.3's event line is a fixed name and three numbers, `event a= b= c=`.
# Events change no emitted byte, so neither loop above sees them, and until
# 2026-10-13 the two cores' events drifted unseen (the model's top of charge
# read vmax, vmin, chg_max in amps against the board's vmax, chg_max in
# centiamps, vmin). This diffs name, t, a, b and c for every event, both
# sides generated now (event_stream.py against host_runner --events).
echo
echo "events (spec 8.3), machine.py vs machine.cpp, field by field:"
# Negative control: top of charge's b and c swapped in a copy of machine.py
# must read DIFFERS on a trace that reaches top of charge. Only the top of
# charge lines are compared, and the UNMUTATED ones must match first, so the
# control is decided by the swap and not by any other difference the trace
# happens to carry.
MUT="${BIN}_mut"
mkdir -p "$MUT"
cp "$FW/../machine.py" "$MUT/machine.py"
sed -i 's/self\.vmax, self\.chg_max, self\.vmin)/self.vmax, self.vmin, self.chg_max)/' \
    "$MUT/machine.py"
if cmp -s "$FW/../machine.py" "$MUT/machine.py"; then
  echo "  EVENTS NEGATIVE CONTROL COULD NOT RUN: the b/c swap did not apply"
  fail=1
else
  toc=""
  for t in "$ART"/*.trace; do
    if "$BIN" --events "$t" | grep -q ' top of charge reached$'; then
      toc="$t"; break
    fi
  done
  tocl() { grep ' top of charge reached$' || true; }
  if [ -z "$toc" ]; then
    echo "  EVENTS NEGATIVE CONTROL COULD NOT RUN: no trace reaches top of charge"
    fail=1
  elif ! diff -q <("$BIN" --events "$toc" | tocl) \
         <(python3 "$HERE/event_stream.py" "$toc" | tocl) >/dev/null; then
    echo "  EVENTS NEGATIVE CONTROL COULD NOT RUN: the unmutated top of charge"
    echo "  lines already differ on $(basename "$toc")"
    fail=1
  elif diff -q <("$BIN" --events "$toc" | tocl) \
       <(python3 "$HERE/event_stream.py" --core-dir "$MUT" "$toc" | tocl) \
       >/dev/null; then
    echo "  EVENTS NEGATIVE CONTROL FAILED: top of charge with b and c swapped"
    echo "  still read IDENTICAL on $(basename "$toc")"
    fail=1
  else
    echo "  negative control: top of charge's b/c swapped is reported on" \
         "$(basename "$toc" .trace) (its unmutated lines match)"
  fi
fi
rm -rf "$MUT"
# Negative control 2 (reviewer, 2026-10-15, step 4 item 2's (6)): the trace
# that reaches EV_OVERRIDE_ENDED_BY_VCU (a VCU CHARGER command during an
# override, make_unit_traces.override_ended_traces) must read DIFFERS when
# the reference core prints "transparent" there, its text before 2026-10-14.
OE="$ART/unit_charger_during_override.trace"
MUT2="${BIN}_mut2"
oel() { grep 'override ended' || true; }
if [ ! -f "$OE" ]; then
  echo "  EVENTS NEGATIVE CONTROL 2 COULD NOT RUN: no $(basename "$OE")"
  fail=1
else
  mkdir -p "$MUT2"
  cp "$FW/../machine.py" "$MUT2/machine.py"
  sed -i 's/override ended, armed/override ended, transparent/' "$MUT2/machine.py"
  if cmp -s "$FW/../machine.py" "$MUT2/machine.py"; then
    echo "  EVENTS NEGATIVE CONTROL 2 COULD NOT RUN: 'override ended, armed'"
    echo "  is not in machine.py"
    fail=1
  elif [ -z "$("$BIN" --events "$OE" | oel)" ]; then
    echo "  EVENTS NEGATIVE CONTROL 2 COULD NOT RUN: machine.cpp prints no"
    echo "  'override ended' line on $(basename "$OE" .trace)"
    fail=1
  elif ! diff -q <("$BIN" --events "$OE" | oel) \
         <(python3 "$HERE/event_stream.py" "$OE" | oel) >/dev/null; then
    echo "  EVENTS NEGATIVE CONTROL 2 COULD NOT RUN: the unmutated override"
    echo "  ended lines already differ"
    fail=1
  elif diff -q <("$BIN" --events "$OE" | oel) \
       <(python3 "$HERE/event_stream.py" --core-dir "$MUT2" "$OE" | oel) \
       >/dev/null; then
    echo "  EVENTS NEGATIVE CONTROL 2 FAILED: 'override ended, transparent'"
    echo "  still read IDENTICAL on $(basename "$OE" .trace)"
    fail=1
  else
    echo "  negative control 2: 'override ended, transparent' is reported on" \
         "$(basename "$OE" .trace); machine.cpp prints: $("$BIN" --events "$OE" | oel)"
  fi
  rm -rf "$MUT2"
fi
for t in "$ART"/*.trace; do
  name="$(basename "$t" .trace)"
  if "$BIN" --events "$t" | diff -q - <(python3 "$HERE/event_stream.py" "$t") \
     >/dev/null; then
    echo "  $name events IDENTICAL"
  else
    echo "  $name events DIFFER"
    "$BIN" --events "$t" | diff - <(python3 "$HERE/event_stream.py" "$t") | head -6
    fail=1
  fi
done

rm -f "$BIN"
exit $fail
