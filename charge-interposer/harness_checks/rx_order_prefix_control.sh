#!/usr/bin/env bash
# Can-fail control for the spec 2 receive-order cases (tester, 2026-10-09;
# re-aimed 2026-10-18).
#
# rx-rollover-order, rx-rollover-refill and rx-rollover-midread all PASS
# since E4 (be4c921, the rollover fix). A pass only means something if the
# same case FAILS on a driver with the defect. Rev 1 built the driver file
# from a commit before the fix (9021d5e); by 2026-10-18 that file no longer
# compiled against the current header (the chain runner's first harness
# run), so rev 2 puts the defect back into the CURRENT driver instead, one
# source edit per mutant, each confirmed to apply exactly once:
#   N2  one buffer per pass, RXB0 first -- after taking RXB0 the status is
#       forced to 0, so RXB1 waits for the next pass, where RXB0 (refilled)
#       is taken first again: the pre-fix behaviour, E3's A,C,B.
#       rx-rollover-order and rx-rollover-midread must FAIL on their order
#       check with their setup met.
#   N1  the immediate re-read removed -- RXB1 is taken in the same pass
#       only if it was flagged before the RXB0 read. rx-rollover-midread
#       (B rolls in DURING that read) must FAIL on its order check.
#   rx-rollover-refill is reported, not judged, under both.
# And first, as the positive half, the same build with the unmutated
# driver must pass all three -- so the difference is the edit alone.
#
# Run:  bash charge-interposer/harness_checks/rx_order_prefix_control.sh
#       (REVERSE_IT_DIR = SeaDrive's reverse-it root, for the fixtures;
#        under WSL pass it with WSLENV=REVERSE_IT_DIR/p)
set -u

# The code is the tree this script is in (moved into git from reverse-it's
# interposer-firmware/ folder, port step 0, 2026-10-26). INTERPOSER_CODE is
# not needed; if it is set, it must name this same charge-interposer/.
CODE="$(cd "$(dirname "$0")/.." && pwd -P)"
if [ -n "${INTERPOSER_CODE:-}" ] && \
   [ "$(cd "$INTERPOSER_CODE" 2>/dev/null && pwd -P)" != "$CODE" ]; then
  echo "INTERPOSER_CODE=$INTERPOSER_CODE is not this tree's charge-interposer/ ($CODE)" >&2
  exit 2
fi
HB="$(cd "$CODE/firmware/test/host_bridge" && pwd)"
SRC="$(cd "$HB/../../src" && pwd)"
# The fixture stays in SeaDrive (beside this script before the move): the
# runner's L3_STIMULUS if set, else under REVERSE_IT_DIR.
L3_STIMULUS="${L3_STIMULUS:-${REVERSE_IT_DIR:?REVERSE_IT_DIR is not set -- the SeaDrive reverse-it root}/projects/vtrux/notes/artifacts/interposer-firmware/l3_stimulus.txt}"
export L3_STIMULUS
WORK="${TMPDIR:-/tmp}/rx_prefix_$$"
rm -rf "$WORK"
mkdir -p "$WORK/driver" "$WORK/fw"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
cp "$HB"/driver/*.h "$WORK/driver"/ 2>/dev/null || true
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/fw"/
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 2

build() {
  rm -f "$WORK/run"
  local srcs=(test_l2_bridge.cpp)
  for f in "$WORK"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  srcs+=(fw/main.cpp fw/port_mcp2515.cpp fw/port_twai.cpp fw/machine.cpp)
  if ! g++ -std=c++11 -O2 -I . -I fw -DINTP_NO_SERIAL=1 -o "$WORK/run" \
       "${srcs[@]}" > "$WORK/build.txt" 2>&1; then
    head -5 "$WORK/build.txt"
    return 1
  fi
  [ -x "$WORK/run" ]
}

rc=0
echo "=== POSITIVE: the current driver, unmutated ==="
if ! build; then echo "*** current tree did not build"; exit 1; fi
for c in rx-rollover-order rx-rollover-refill rx-rollover-midread; do
  if "$WORK/run" "$c" > "$WORK/pos_$c.txt" 2>&1; then
    echo "  $c PASS"
  else
    echo "*** $c FAILS on the current driver -- the control below means nothing"
    grep '^FAIL' "$WORK/pos_$c.txt" | head -3; rc=1
  fi
done

# $1 case, $2 setup-check text that must be ok, $3 order text that must FAIL
expect_order_fail() {
  local c="$1" setup="$2" ord="$3"
  "$WORK/run" "$c" > "$WORK/neg_$c.txt" 2>&1
  local st=$?
  grep '^  --  rep' "$WORK/neg_$c.txt" | head -2 | sed 's/^/    /'
  if [ "$st" -eq 0 ]; then
    echo "*** $c PASSED on the mutant -- the case cannot see the defect"; rc=1; return
  fi
  if [ -n "$setup" ] && grep '^FAIL' "$WORK/neg_$c.txt" | grep -qF "$setup"; then
    echo "*** $c failed on its SETUP, not its order -- the window never opened"; rc=1; return
  fi
  if grep '^FAIL' "$WORK/neg_$c.txt" | grep -qF "$ord"; then
    echo "  $c FAILS on the mutant, on its order check ($(grep -c '^FAIL' "$WORK/neg_$c.txt") FAIL lines)"
  else
    echo "*** $c failed, but not on the order check -- incidental"; rc=1
  fi
}
REREAD='      st = readStatus();   // see RECEIVE ORDER above -- not optional'

# $1 label, $2 sed expression; the re-read line must be in the driver once
mutant() {
  echo
  echo "=== NEGATIVE $1 ==="
  cp "$SRC/port_mcp2515.cpp" "$WORK/fw/port_mcp2515.cpp"
  local n
  n=$(grep -cxF "$REREAD" "$SRC/port_mcp2515.cpp")
  if [ "$n" -ne 1 ]; then
    echo "*** the re-read line is in the driver $n time(s), not once -- re-aim this control"
    rc=1; return 1
  fi
  sed -i "$2" "$WORK/fw/port_mcp2515.cpp"
  if cmp -s "$SRC/port_mcp2515.cpp" "$WORK/fw/port_mcp2515.cpp" || \
     grep -qxF "$REREAD" "$WORK/fw/port_mcp2515.cpp"; then
    echo "*** the mutation did not apply -- meaningless"; rc=1; return 1
  fi
  if ! build; then echo "*** the mutated driver did not build -- meaningless"; rc=1; return 1; fi
  return 0
}

refill_report() {
  "$WORK/run" rx-rollover-refill > "$WORK/neg_refill.txt" 2>&1
  echo "  rx-rollover-refill (reported only): exit $?," \
       "$(grep -c '^FAIL' "$WORK/neg_refill.txt") FAIL lines"
}

if mutant "N2: one buffer per pass, RXB0 first (the pre-fix form)" \
     's#^      st = readStatus();   // see RECEIVE ORDER above -- not optional$#      st = 0;#'; then
  expect_order_fail rx-rollover-order "setup held" "per spec 2"
  expect_order_fail rx-rollover-midread "the interleaving happened" "per spec 2"
  refill_report
fi
if mutant "N1: the immediate re-read removed" \
     '\#^      st = readStatus();   // see RECEIVE ORDER above -- not optional$#d'; then
  expect_order_fail rx-rollover-midread "the interleaving happened" "per spec 2"
  "$WORK/run" rx-rollover-order > "$WORK/neg_n1_order.txt" 2>&1
  echo "  rx-rollover-order (reported only; both flags were set before the read): exit $?"
  refill_report
fi

echo
if [ "$rc" -eq 0 ]; then
  echo "CONTROL HOLDS: the current driver passes all three; N2 fails order and midread, N1 fails midread, each on its order check."
else
  echo "CONTROL DOES NOT HOLD -- see the markers above."
fi
exit "$rc"
