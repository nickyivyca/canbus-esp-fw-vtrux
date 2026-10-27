#!/usr/bin/env bash
# Mutation check for test_mcp2515_busoff.
#
# Bus-off on the charger port was NOT_MODELLED until 2026-10-04 and
# `Mcp2515Port::busOffEvents()` had never run. Before the status table is
# allowed to claim DATASHEET for any of it, each datasheet behaviour gets
# broken in the chip model and the test must fail on the line that
# behaviour belongs to.
#
# Verification is by FILE CHANGE, not by a string vanishing -- an
# insertion removes nothing, so a vanish-check cannot see it.
#
# Run:  wsl bash ".../interposer-firmware/busoff_mutations.sh"
set -u

# INTERPOSER_CODE (required since 2026-10-08): the charge-interposer code
# root to test -- a clone's charge-interposer/ (repo canbus-esp-fw-vtrux).
# The SeaDrive copy it used to default to is gone; under WSL pass it with
# WSLENV=INTERPOSER_CODE/p.
: "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}"
HB="$(cd "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}/firmware/test/host_bridge" && pwd)"
SRC="$(cd "$HB/../../src" && pwd)"
WORK=/tmp/busoff_mut
rm -rf "$WORK"
mkdir -p "$WORK"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
cd "$WORK" || exit 2

TARGET=mcp2515_fake_chip.cpp

build() {
  g++ -std=c++11 -O2 -I . -I "$SRC" -o "$WORK/run" \
      test_mcp2515_busoff.cpp mcp2515_fake.cpp mcp2515_fake_chip.cpp \
      provenance.cpp host_clock.cpp "$SRC/port_mcp2515.cpp" 2>&1 | head -5
}

rc=0

echo "=== CONTROL (unmutated) ==="
build
if "$WORK/run" > "$WORK/control.txt" 2>&1; then
  echo "control PASSES, $(grep -c '^  ok' "$WORK/control.txt") checks ok"
else
  echo "*** CONTROL FAILED -- fix that first ***"
  grep '^FAIL' "$WORK/control.txt" | head
  exit 1
fi

# $1 label, $2 sed expr, $3 expected failure text,
# $4 optional: file to mutate (default mcp2515_fake_chip.cpp),
# $5 optional: "expect-survives" -- the mutation SHOULD NOT be caught.
mutate() {
  local label="$1" expr="$2" want="$3"
  local TARGET="${4:-mcp2515_fake_chip.cpp}" mode="${5:-expect-caught}"
  echo
  echo "=== $label ==="
  cp "$HB/$TARGET" "$TARGET"
  sed -i "$expr" "$TARGET"
  if cmp -s "$HB/$TARGET" "$TARGET"; then
    echo "*** MUTATION DID NOT APPLY (file unchanged) -- result meaningless"
    rc=1
    cp "$HB/$TARGET" "$TARGET"
    return
  fi
  build
  local survived=0
  "$WORK/run" > "$WORK/out.txt" 2>&1 && survived=1

  if [ "$mode" = "expect-survives" ]; then
    if [ "$survived" -eq 1 ]; then
      echo "survived, as expected -- see the note above this mutation"
    else
      echo "*** CAUGHT, and it was expected to survive. Re-justify before"
      echo "*** accepting: the model's behaviour has changed."
      rc=1
    fi
    cp "$HB/$TARGET" "$TARGET"
    return
  fi

  if [ "$survived" -eq 1 ]; then
    echo "*** THE TEST STILL PASSED. It does not constrain this. ***"
    rc=1
  else
    echo "caught it:"
    grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -3
    if grep '^FAIL' "$WORK/out.txt" | grep -qF "$want"; then
      echo "    (and on the expected check)"
    else
      echo "*** failed, but not on the expected check -- incidental ***"
      rc=1
    fi
  fi
  cp "$HB/$TARGET" "$TARGET"
}

# A: EXPECTED TO SURVIVE -- an EQUIVALENT mutation, not a coverage gap.
#
# p47 s6.7 says bus-off is entered when TEC EXCEEDS 255, so `> 255` is the
# faithful predicate and it stays. But on this error path TEC only moves
# by +8 from 0, so it takes the values 8, 16 ... 248, 256 and NEVER 255.
# `>= 255` and `> 255` therefore fire on the very same attempt, and no
# test can separate them without contriving a TEC of exactly 255 -- which
# the chip's own arithmetic will not produce here.
#
# Recorded rather than papered over, and NOT a reason to loosen the
# predicate to match what is testable: the wording is the authority, the
# test simply cannot defend this particular character of it.
mutate "A: bus-off entered when TEC reaches 255, not when it exceeds it" \
  's|if (tec_ > BUS_OFF_TEC_LIMIT) {|if (tec_ >= BUS_OFF_TEC_LIMIT) {|' \
  '(not applicable)' \
  mcp2515_fake_chip.cpp \
  expect-survives

# B: the chip keeps transmitting while bus-off (p47 s6.6 says it cannot).
mutate "B: the transmit engine runs while bus-off" \
  's|  if (regs_\[R_EFLG\] \& EFLG_TXBO) return;|  ;|' \
  'nothing is transmitted while bus-off'

# C: recovery returns to error-passive rather than error-active, i.e. TEC
# is not cleared (p48 Fig 6-1 shows the edge going to Error-Active).
mutate "C: recovery leaves TEC where it was instead of clearing it" \
  's|^  tecSet(0);$|  ;|' \
  'returning to ERROR-ACTIVE with TEC cleared'

# D: the recovery window is wrong -- 128 bits instead of 128 x 11.
mutate "D: recovery waits 128 bit times, not 128 x 11" \
  's|BUS_OFF_RECOVERY_BITS = 128 \* 11;|BUS_OFF_RECOVERY_BITS = 128;|' \
  '2816 us' \
  mcp2515_fake_chip.h

# E: the no-ACK pin removed, so an unacknowledged transmitter also reaches
# bus-off. This is the DEFERRED reading, and conflating the two error
# sources is the specific mistake this whole split exists to prevent.
mutate "E: the no-ACK path is allowed past the error-passive limit" \
  's|  if (tec_ >= 128) tecSet(128);|  ;|' \
  'still pins at the error-passive limit'

echo
if [ "$rc" -eq 0 ]; then
  echo "EVERY MUTATION BEHAVED AS SPECIFIED (A is an equivalent mutation)."
else
  echo "SOMETHING IS NOT COVERED -- see the markers above."
fi
exit "$rc"
