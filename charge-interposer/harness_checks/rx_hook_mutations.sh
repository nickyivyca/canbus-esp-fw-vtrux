#!/usr/bin/env bash
# Mutation check for Mcp2515Fake::deliverWhenFreed() (tester, 2026-10-08).
#
# test_l2_bridge's rx-rollover-order case places frame C with this hook:
# C must arrive at the instant RXB0 is freed, and not before. If the hook
# fired early, C would meet two full buffers and be lost; if it never
# fired, the case would test no window at all. Its model checks live in
# test_mcp2515_fake ("hook: ..."), and this script shows they can fail:
# each mutation breaks the hook one way, and the model test must FAIL on a
# "hook:" line.
#
# Same discipline as conformance_mutations.sh: each mutation verifies it
# applied, the binary is removed before every build, and a failed build is
# never a result.
#
# Run:  INTERPOSER_CODE=<clone>/charge-interposer wsl bash ".../rx_hook_mutations.sh"
#       (under WSL pass it with WSLENV=INTERPOSER_CODE/p)
set -u
: "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root}"
SRC="$(cd "$INTERPOSER_CODE/firmware/test/host_bridge" && pwd)"
WORK="${TMPDIR:-/tmp}/rx_hook_mut_$$"
rm -rf "$WORK"
mkdir -p "$WORK/driver"
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK"/
cp "$SRC"/driver/*.h "$WORK/driver"/ 2>/dev/null || true
cd "$WORK" || exit 2
trap 'rm -rf "$WORK"' EXIT

build() {
  rm -f "$WORK/run"
  local srcs=(test_mcp2515_fake.cpp)
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
if ! build; then echo "*** CONTROL DID NOT BUILD ***"; exit 1; fi
if "$WORK/run" > "$WORK/control.txt" 2>&1; then
  echo "control PASSES, $(grep -c '^  ok' "$WORK/control.txt") checks ok," \
       "$(grep -c '^  ok  hook:' "$WORK/control.txt") of them hook checks"
else
  echo "*** CONTROL FAILED ***"; grep '^FAIL' "$WORK/control.txt" | head; exit 1
fi

# $1 label, $2 file, $3 sed expression, $4 string that must vanish,
# $5 text the failing check must contain (default "hook:")
mutate() {
  local label="$1" file="$2" expr="$3" gone="$4" want="${5:-hook:}"
  echo
  echo "=== $label ==="
  cp "$SRC/$file" "$file"
  # The pattern must be in the source first: absence afterwards alone is
  # satisfied by a pattern the code no longer has (conformance_mutations
  # D, found 2026-10-09).
  if ! grep -qF "$gone" "$SRC/$file"; then
    echo "*** PATTERN NOT IN THE SOURCE -- result meaningless"; rc=1; return
  fi
  sed -i "$expr" "$file"
  if cmp -s "$SRC/$file" "$file" || grep -qF "$gone" "$file"; then
    echo "*** MUTATION DID NOT APPLY -- result meaningless"; rc=1
    cp "$SRC/$file" "$file"; return
  fi
  if ! build; then
    echo "*** the mutated tree did not build -- meaningless"; rc=1
    cp "$SRC/$file" "$file"; return
  fi
  if "$WORK/run" > "$WORK/out.txt" 2>&1; then
    echo "*** THE MODEL TEST STILL PASSED -- not covered ***"; rc=1
  else
    grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -4
    if grep '^FAIL' "$WORK/out.txt" | grep -qF "$want"; then
      echo "    caught, on the expected check ($want)"
    else
      echo "*** failed, but not on the expected check ($want) -- incidental ***"; rc=1
    fi
  fi
  cp "$SRC/$file" "$file"
}

mutate "K: the hook never delivers" \
       mcp2515_fake_chip.cpp \
       's/    deliverFrame(pend_\[n\]);/    ;/' \
       '    deliverFrame(pend_[n]);'

mutate "L: the hook delivers at once, when armed" \
       mcp2515_fake_chip.h \
       's/    pend_fired_\[n \& 1\] = false;/    pend_fired_[n \& 1] = true; pend_armed_[n \& 1] = false; deliverFrame(f);/' \
       '    pend_fired_[n & 1] = false;'

# The two accessors rx-rollover-refill's setup assertion rests on
# (added with that case, 2026-10-08).
mutate "M: otherFullWhenFired is constant true" \
       mcp2515_fake_chip.cpp \
       's/pend_other_full_\[n\] = rxFull(1 - n);/pend_other_full_[n] = true;/' \
       'pend_other_full_[n] = rxFull(1 - n);'

mutate "N: firedSeq is constant" \
       mcp2515_fake_chip.cpp \
       's/pend_seq_\[n\] = ++fire_seq_;/pend_seq_[n] = 1;/' \
       'pend_seq_[n] = ++fire_seq_;'

# Moved here from l2_mutations.sh (its B), 2026-10-09. Ignoring the CANINTE
# enable mask is a datasheet behaviour of the chip (p51 s7.0), asserted
# against the model directly in test_mcp2515_fake, whose own comment says
# no L2 assertion can see it. In l2_mutations.sh it "survived" from
# 2026-10-07 because it ran against the wrong test.
mutate "O (was L2 B): INT ignores the CANINTE enable mask" \
       mcp2515_fake_chip.h \
       's|return (regs_\[R_CANINTF\] \& regs_\[R_CANINTE\]) != 0;|return regs_[R_CANINTF] != 0;|' \
       'return (regs_[R_CANINTF] & regs_[R_CANINTE]) != 0;' \
       'CANINTE clear does NOT assert INT'

# P, Q, R (2026-10-09): deliverDuringRead(), added for be4c921's mid-read
# interleaving (test_l2_bridge rx-rollover-midread). Each breaks it one
# way; test_mcp2515_fake must fail on a "midread:" line.
mutate "P: midread fires on a READ RX of either buffer" \
       mcp2515_fake_chip.cpp \
       's|mid_armed_ \&\& rx_which_ == mid_buf_ \&\&|mid_armed_ \&\&|' \
       'mid_armed_ && rx_which_ == mid_buf_ &&' \
       'midread:'

mutate "Q: midread fires on an EMPTY buffer" \
       mcp2515_fake_chip.cpp \
       's|          rxFull(mid_buf_)) {|          true) {|' \
       '          rxFull(mid_buf_)) {' \
       'midread:'

mutate "R: midread is never one-shot (stays armed)" \
       mcp2515_fake_chip.cpp \
       's|        mid_armed_ = false;|        ;|' \
       '        mid_armed_ = false;' \
       'midread:'

# Self-test of the applied-check (2026-10-09): a pattern absent from the
# source must be refused, not run. Run in a subshell so its rc stays local.
st=$( rc=0; mutate "SELFTEST: a stale pattern" mcp2515_fake_chip.cpp 's/NO_SUCH_CODE_XYZ/;/' 'NO_SUCH_CODE_XYZ'; echo "rc=$rc" )
if echo "$st" | grep -q "PATTERN NOT IN THE SOURCE" && echo "$st" | grep -q "rc=1"; then
  echo
  echo "self-test: a pattern absent from the source is refused"
else
  echo "*** SELF-TEST FAILED: a stale pattern was not refused"; rc=1
fi

echo
if [ "$rc" -eq 0 ]; then echo "ALL MUTATIONS CAUGHT."; else echo "SOMETHING IS NOT COVERED."; fi
exit "$rc"
