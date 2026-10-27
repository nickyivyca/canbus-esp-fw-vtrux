#!/usr/bin/env bash
# Mutation check for the MCP2515 conformance sequence.
#
# A conformance transcript is only worth diffing if the assertions behind
# it would actually notice a wrong chip. Each mutation below breaks one
# DATASHEET behaviour in the chip model; the sequence must FAIL on it, and
# must fail on the line that behaviour belongs to.
#
# Every mutation verifies that it applied. A sed that silently matches
# nothing produces a "clean" run that looks like the test passing when in
# fact nothing was changed -- the same shape as the test that cannot fail.
#
# Rev 2 (charge-interposer tester, 2026-10-07): rev 1's control did not
# link (its hand-kept source list lacked provenance.cpp) and a build that
# failed left the previous binary in place to be run. The sources are now
# globbed the way host_bridge/run.sh globs them, the binary is removed
# before every build, and a build that fails is a failed control or a
# meaningless mutation -- never a result.
#
# Run:  wsl bash ".../interposer-firmware/conformance_mutations.sh"
set -u

# INTERPOSER_CODE (required since 2026-10-08): the charge-interposer code
# root to test -- a clone's charge-interposer/ (repo canbus-esp-fw-vtrux).
# The SeaDrive copy it used to default to is gone; under WSL pass it with
# WSLENV=INTERPOSER_CODE/p.
: "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}"
SRC="$(cd "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}/firmware/test/host_bridge" && pwd)"
WORK="${TMPDIR:-/tmp}/conf_mut_$$"
rm -rf "$WORK"
mkdir -p "$WORK/driver"
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK"/
cp "$SRC"/driver/*.h "$WORK/driver"/ 2>/dev/null || true
cd "$WORK" || exit 2
trap 'rm -rf "$WORK"' EXIT

# test_conformance.cpp plus every non-test .cpp in host_bridge, as run.sh
# builds it (that test needs no firmware source). -> 0 built, 1 not.
build() {
  rm -f "$WORK/run"
  local srcs=(test_conformance.cpp)
  for f in "$WORK"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  if ! g++ -std=c++11 -O2 -I . -o "$WORK/run" "${srcs[@]}" > "$WORK/build.txt" 2>&1; then
    head -5 "$WORK/build.txt"
    return 1
  fi
  [ -x "$WORK/run" ]
}

rc=0

echo "=== CONTROL (unmutated) ==="
if ! build; then
  echo "*** CONTROL DID NOT BUILD -- nothing below would mean anything ***"
  exit 1
fi
if "$WORK/run" > "$WORK/control.txt" 2>&1; then
  echo "control PASSES, $(grep -c '^  ok' "$WORK/control.txt") checks ok"
else
  echo "*** CONTROL FAILED -- fix that before reading any mutation below ***"
  grep '^FAIL' "$WORK/control.txt" | head
  exit 1
fi

# $1 = label, $2 = file, $3 = sed expression, $4 = string that must VANISH,
# $5 = transcript key the failure is expected on
mutate() {
  local label="$1" file="$2" expr="$3" gone="$4" want_key="$5"
  echo
  echo "=== $label ==="
  cp "$SRC/$file" "$file"
  # Rev 4 (2026-10-09): the pattern must be IN the source first. Rev 1-3
  # checked only that it was absent afterwards, which a pattern the code
  # no longer contains satisfies vacuously -- D's target had been
  # rewritten (tecSet, 2026-10-04) and D "survived" for days while
  # mutating nothing.
  if ! grep -qF "$gone" "$SRC/$file"; then
    echo "*** PATTERN NOT IN THE SOURCE -- this mutation targets code that no longer exists; result meaningless"
    rc=1
    return
  fi
  sed -i "$expr" "$file"
  if cmp -s "$SRC/$file" "$file" || grep -qF "$gone" "$file"; then
    echo "*** MUTATION DID NOT APPLY (file unchanged or pattern still present) -- result below is meaningless"
    rc=1
    cp "$SRC/$file" "$file"
    return
  fi
  if ! build; then
    echo "*** the mutated tree did not build -- meaningless, not caught"
    rc=1
    cp "$SRC/$file" "$file"
    return
  fi
  if "$WORK/run" > "$WORK/out.txt" 2>&1; then
    echo "*** THE SEQUENCE STILL PASSED. It does not cover this behaviour. ***"
    rc=1
  else
    echo "caught it:"
    grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -4
    if grep '^FAIL' "$WORK/out.txt" | grep -qF "$want_key"; then
      echo "    (and on the expected line: $want_key)"
    else
      echo "*** failed, but NOT on $want_key -- the coverage is incidental ***"
      rc=1
    fi
  fi
  cp "$SRC/$file" "$file"
}

mutate "A: RTS tie-break picks the LOWEST buffer (p15 s3.2 inverted)" \
       mcp2515_fake_chip.cpp \
       's/(p == best_p \&\& n > best)/(p == best_p \&\& n < best)/' \
       'p == best_p && n > best' \
       'C5-rts-one-instruction'

mutate "B: BIT MODIFY honours the mask on every register" \
       mcp2515_fake_chip.cpp \
       's|if (!isBitModifiable(addr_)) mask_ = 0xFF;|;|' \
       'if (!isBitModifiable(addr_)) mask_ = 0xFF;' \
       'C2-bit-modify'

mutate "C: TXP ignored, so buffer number alone decides" \
       mcp2515_fake_chip.cpp \
       's/const uint8_t p = (uint8_t)(c \& TXB_TXP_MASK);/const uint8_t p = 0;/' \
       'const uint8_t p = (uint8_t)(c & TXB_TXP_MASK);' \
       'C7-txp-priority'

mutate "D: a no-ACK transmitter is allowed past the error-passive limit" \
       mcp2515_fake_chip.cpp \
       's/if (tec_ >= 128) tecSet(128);/;/' \
       'if (tec_ >= 128) tecSet(128);' \
       'C9-no-ack'

mutate "E: TXREQ left set after a successful send" \
       mcp2515_fake_chip.cpp \
       's/regs_\[ctrl\] = (uint8_t)(regs_\[ctrl\] \& ~TXB_TXREQ);/;/' \
       'regs_[ctrl] = (uint8_t)(regs_[ctrl] & ~TXB_TXREQ);' \
       'C4-txflags-on-request'

# Rev 3 (tester, 2026-10-08): C12, receive rollover and buffer order. Each
# mutation puts back one way the receive model could be wrong, two of them
# being how the model WAS wrong until that day (RX0OVR on a rollover
# overflow; READ RX ignoring m).

# F re-aimed 2026-10-18: its old target (RX1OVR on a rollover into a full
# RXB1) was replaced on 2026-10-15 (04a2355) by what step 4 item 8
# measured on silicon -- RX0OVR, and the frame kept in the MAB. F1 and F2
# put back the model before that measurement, one half each.
mutate "F1: a rollover into a full RXB1 sets RX1OVR (the model before 2026-10-15)" \
       mcp2515_fake_chip.cpp \
       's/^  mab_ = f;$/  regs_[R_EFLG] = (uint8_t)((regs_[R_EFLG] \& ~EFLG_RX0OVR) | EFLG_RX1OVR); mab_=f;/' \
       'mab_ = f;' \
       'C12-rx-rollover/D.overflow'

mutate "F2: the frame of a rollover into a full RXB1 is lost (the model before 2026-10-15)" \
       mcp2515_fake_chip.cpp \
       's/mab_held_ = true;/mab_held_ = false;/' \
       'mab_held_ = true;' \
       'C12-rx-rollover/E.RXB1D0'

mutate "G: READ RX ignores m and always starts at SIDH (the pre-2026-10-08 model)" \
       mcp2515_fake_chip.cpp \
       's/((out \& 0x02) ? 5 : 0)/0/' \
       '((out & 0x02) ? 5 : 0)' \
       'C12-rx-rollover/E.read96-first-byte'

mutate "H: only READ RX frees a buffer; clearing RXnIF by BIT MODIFY does not" \
       mcp2515_fake_chip.cpp \
       's/if (a == R_CANINTF) rxFlagsChanged(intf_before);/if (a == R_CANINTF) regs_[a] = (uint8_t)(v | (intf_before \& 0x03));/' \
       'if (a == R_CANINTF) rxFlagsChanged(intf_before);' \
       'C12-rx-rollover/E.RXB1D0'

mutate "I: READ RX leaves RXnIF set at CS high" \
       mcp2515_fake_chip.cpp \
       's/regs_\[R_CANINTF\] &= (uint8_t)~(which ? INTF_RX1IF : INTF_RX0IF);/;/' \
       'regs_[R_CANINTF] &= (uint8_t)~(which ? INTF_RX1IF : INTF_RX0IF);' \
       'C12-rx-rollover/takeA.rx-flags'

mutate "J: BUKT ignored, so nothing rolls over" \
       mcp2515_fake_chip.cpp \
       's/if (!(regs_\[R_RXB0CTRL\] \& RXB0_BUKT)) {/if (true) {/' \
       'if (!(regs_[R_RXB0CTRL] & RXB0_BUKT)) {' \
       'C12-rx-rollover/AB.RXB1D0'

# Self-test of the applied-check (2026-10-09): a pattern absent from the
# source must be refused, not run. Run in a subshell so its rc stays local.
st=$( rc=0; mutate "SELFTEST: a stale pattern" mcp2515_fake_chip.cpp 's/NO_SUCH_CODE_XYZ/;/' 'NO_SUCH_CODE_XYZ' 'none'; echo "rc=$rc" )
if echo "$st" | grep -q "PATTERN NOT IN THE SOURCE" && echo "$st" | grep -q "rc=1"; then
  echo
  echo "self-test: a pattern absent from the source is refused"
else
  echo "*** SELF-TEST FAILED: a stale pattern was not refused"; rc=1
fi

echo
if [ "$rc" -eq 0 ]; then
  echo "ALL MUTATIONS CAUGHT, each on its own line."
else
  echo "SOMETHING IS NOT COVERED -- see the markers above."
fi
exit "$rc"
