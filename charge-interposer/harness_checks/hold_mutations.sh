#!/usr/bin/env bash
# Mutation check for test_sameid_hold (tester, 2026-10-09).
#
# Spec 2's same-identifier hold has two halves: one buffer per identifier
# (with order kept), and "nothing behind it is loaded" while a frame is
# held. test_sameid_hold checks both, the second since 2026-10-09 (the
# head-of-line block). Each mutation below puts the driver back to one way
# of breaking a half, and the test must fail on that half's own check.
#
# Same discipline as conformance_mutations.sh rev 4: the pattern must be
# in the source first, the file must change, the binary is removed before
# every build, and a failed build is never a result.
#
# Run:  INTERPOSER_CODE=<clone>/charge-interposer wsl bash ".../hold_mutations.sh"
#       (under WSL pass it with WSLENV=INTERPOSER_CODE/p)
set -u
: "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root}"
HB="$(cd "$INTERPOSER_CODE/firmware/test/host_bridge" && pwd)"
SRC="$(cd "$HB/../../src" && pwd)"
WORK="${TMPDIR:-/tmp}/hold_mut_$$"
rm -rf "$WORK"
mkdir -p "$WORK/driver" "$WORK/fw"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
cp "$HB"/driver/*.h "$WORK/driver"/ 2>/dev/null || true
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/fw"/
cd "$WORK" || exit 2
trap 'rm -rf "$WORK"' EXIT

# test_sameid_hold as host_bridge/run.sh builds it: every non-test .cpp in
# host_bridge plus the driver, from fw/ so a mutated fw/ file is compiled.
build() {
  rm -f "$WORK/run"
  local srcs=(test_sameid_hold.cpp)
  for f in "$WORK"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  srcs+=(fw/port_mcp2515.cpp)
  if ! g++ -std=c++11 -O2 -I . -I fw -o "$WORK/run" "${srcs[@]}" \
       > "$WORK/build.txt" 2>&1; then
    head -5 "$WORK/build.txt"
    return 1
  fi
  [ -x "$WORK/run" ]
}

rc=0
echo "=== CONTROL (unmutated) ==="
if ! build; then echo "*** CONTROL DID NOT BUILD ***"; exit 1; fi
if "$WORK/run" > "$WORK/control.txt" 2>&1; then
  echo "control PASSES, $(grep -c '^  ok' "$WORK/control.txt") checks ok"
else
  echo "*** CONTROL FAILED ***"; grep '^FAIL' "$WORK/control.txt" | head; exit 1
fi

# $1 label, $2 file under the work tree, $3 sed expression, $4 string that
# must vanish, $5 text the failing check must contain
mutate() {
  local label="$1" file="$2" expr="$3" gone="$4" want="$5"
  echo
  echo "=== $label ==="
  local orig="$SRC/${file#fw/}"
  if ! grep -qF "$gone" "$orig"; then
    echo "*** PATTERN NOT IN THE SOURCE -- result meaningless"; rc=1; return
  fi
  cp "$orig" "$file"
  sed -i "$expr" "$file"
  if cmp -s "$orig" "$file" || grep -qF "$gone" "$file"; then
    echo "*** MUTATION DID NOT APPLY -- result meaningless"; rc=1
    cp "$orig" "$file"; return
  fi
  if ! build; then
    echo "*** the mutated tree did not build -- meaningless"; rc=1
    cp "$orig" "$file"; return
  fi
  if "$WORK/run" > "$WORK/out.txt" 2>&1; then
    echo "*** THE TEST STILL PASSED -- not covered ***"; rc=1
  else
    grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -4
    if grep '^FAIL' "$WORK/out.txt" | grep -qF "$want"; then
      echo "    caught, on the expected check ($want)"
    else
      echo "*** failed, but not on the expected check ($want) -- incidental ***"; rc=1
    fi
  fi
  cp "$orig" "$file"
}

# A: the held head is requeued at the tail and the next free buffer is
# given whatever is behind it -- the skip-ahead spec 2 forbids.
mutate "A: a held frame is requeued and the frames behind it are loaded" \
  fw/port_mcp2515.cpp \
  's|      tx_head_deferred_ = true;   // this FRAME was held at least once|      tx_head_deferred_ = true; { RxFrame h; tx_.pop(h); tx_.push(h.id, h.ext, h.data, h.len, h.t_ms); } continue;|' \
  '      tx_head_deferred_ = true;   // this FRAME was held at least once' \
  'nothing behind the held X2 is loaded'

# B: no hold at all -- two buffers may hold one identifier.
mutate "B: the hold is removed" \
  fw/port_mcp2515.cpp \
  's|    if (idHeld(head->id, head->ext)) {|    if (false) {|' \
  '    if (idHeld(head->id, head->ext)) {' \
  'at no pass does more than ONE buffer hold'

st=$( rc=0; mutate "SELFTEST: a stale pattern" fw/port_mcp2515.cpp 's/NO_SUCH_CODE_XYZ/;/' 'NO_SUCH_CODE_XYZ' 'none'; echo "rc=$rc" )
if echo "$st" | grep -q "PATTERN NOT IN THE SOURCE" && echo "$st" | grep -q "rc=1"; then
  echo
  echo "self-test: a pattern absent from the source is refused"
else
  echo "*** SELF-TEST FAILED: a stale pattern was not refused"; rc=1
fi

echo
if [ "$rc" -eq 0 ]; then echo "ALL MUTATIONS CAUGHT."; else echo "SOMETHING IS NOT COVERED."; fi
exit "$rc"
