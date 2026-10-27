#!/usr/bin/env bash
# Mutation check for test_l2_bridge.
#
# The L2 harness exists because the whole bridge loop had never run on a
# host. Its coverage assertion exists because the MCP2515 RECEIVE path had
# never run either -- the INT pin was unmodelled, every suite was green,
# and nothing said so. This script puts that back and requires the
# coverage check to catch it.
#
# Rev 2 (charge-interposer tester, 2026-10-07). Rev 1 could not fail:
#   - build() listed its sources by hand, missed l3_replay.cpp, and did not
#     link -- and nothing checked that it had;
#   - the control then listed cases from a binary that did not exist, got
#     none, looped zero times and printed "control PASSES, all cases";
#   - mutations A and B were "caught" by the same link failure;
#   - mutation C named a path that did not exist, and `cmp -s` on two
#     missing files counted as "applied" -- and the build compiled a
#     different copy of main.cpp anyway.
# Now the sources are globbed exactly as host_bridge/run.sh globs them; a
# build that fails is a failed control (or a meaningless mutation), never a
# pass; the control must find at least one case and pass every one; every
# mutation names a file under the work tree, is compared against its own
# pristine copy, and is compiled from the place it was mutated.
#
# Run:  wsl bash ".../interposer-firmware/l2_mutations.sh"
set -u

# INTERPOSER_CODE (required since 2026-10-08): the charge-interposer code
# root to test -- a clone's charge-interposer/ (repo canbus-esp-fw-vtrux).
# The SeaDrive copy it used to default to is gone; under WSL pass it with
# WSLENV=INTERPOSER_CODE/p.
: "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}"
HB="$(cd "${INTERPOSER_CODE:?INTERPOSER_CODE is not set -- the charge-interposer code root, a clone of canbus-esp-fw-vtrux/charge-interposer}/firmware/test/host_bridge" && pwd)"
SRC="$(cd "$HB/../../src" && pwd)"
# The work tree is outside the project, so l3_replay.cpp's relative
# candidates for its pinned stimulus cannot resolve; it reads this first.
L3_STIMULUS="$(cd "$(dirname "$0")" && pwd)/l3_stimulus.txt"
export L3_STIMULUS
WORK="${TMPDIR:-/tmp}/l2_mut_$$"
rm -rf "$WORK"
mkdir -p "$WORK/driver" "$WORK/fw" "$WORK/PRISTINE/fw" "$WORK/PRISTINE/driver"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
cp "$HB"/driver/*.h "$WORK/driver"/
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/fw"/
cp "$HB"/*.cpp "$HB"/*.h "$WORK/PRISTINE"/
cp "$HB"/driver/*.h "$WORK/PRISTINE/driver"/
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/PRISTINE/fw"/
cd "$WORK" || exit 2
trap 'rm -rf "$WORK"' EXIT

# The same source set as host_bridge/run.sh builds test_l2_bridge from:
# every non-test .cpp in host_bridge (globbed, so a new shim cannot be
# missed the way l3_replay.cpp was), plus main.cpp, both ports and the core,
# from fw/ so a mutation of fw/main.cpp is the main.cpp that is compiled.
# -> 0 if it built, 1 if not. The binary is removed first, so a failed
# build can never leave the previous one behind to be run.
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

echo "=== CONTROL (unmutated) ==="
if ! build; then
  echo "*** CONTROL DID NOT BUILD -- nothing below would mean anything ***"
  exit 1
fi
# Rev 3 (2026-10-09): the control GATES on the cases the mutations run
# (NEED), each of which must pass unmutated. Every other case is run and
# reported, but a failure there does not void the script: since
# 2026-10-08 rx-rollover-order and rx-rollover-refill fail on a known
# product defect (evidence 2), and rev 2's all-cases gate then refused to
# run any mutation at all.
NEED="boot failed-start rtr-not-forwarded busoff-charger busoff-vehicle bridge-ok-veh-refused bridge-ok-rx-missed bridge-ok-rx-overrun"
cases="$("$WORK/run")"
n=0
failing=""
for c in $cases; do
  n=$((n + 1))
  "$WORK/run" "$c" > "$WORK/c_$c.txt" 2>&1 || failing="$failing $c"
done
if [ "$n" -eq 0 ]; then
  echo "*** CONTROL LISTED NO CASES -- a loop over nothing is not a pass ***"
  exit 1
fi
# gate CASES FAILING -> prints the refusal and returns 1 if a NEED case is
# missing from CASES or present in FAILING.
gate() {
  local all="$1" bad="$2" need
  for need in $NEED; do
    case " $all " in
      *" $need "*) ;;
      *) echo "*** the control has no case '$need', which a mutation below needs ***"
         return 1 ;;
    esac
    case " $bad " in
      *" $need "*)
        echo "*** CONTROL FAILED on '$need', which a mutation below runs -- fix that first ***"
        [ -f "$WORK/c_$need.txt" ] && grep -h '^FAIL' "$WORK/c_$need.txt" | head
        return 1 ;;
    esac
  done
  return 0
}
# The gate's own can-fail: a needed case failing, or missing, is refused.
if gate "boot cadence failed-start" " boot" > /dev/null || \
   gate "cadence failed-start" "" > /dev/null; then
  echo "*** GATE SELF-TEST FAILED: it let a failing or missing needed case through ***"
  exit 1
fi
gate "$(echo $cases)" "$failing" || exit 1
echo "control: the cases the mutations run all PASS ($NEED); $n cases listed"
if [ -n "$failing" ]; then
  echo "  failing in the control, used by no mutation here:$failing"
fi

# $1 label, $2 file (relative to the work tree), $3 sed expr, $4 case,
# $5 expected failure text
mutate() {
  local label="$1" file="$2" expr="$3" case_name="$4" want="$5"
  echo
  echo "=== $label ==="
  if [ ! -f "PRISTINE/$file" ]; then
    echo "*** no such file '$file' -- the mutation names nothing; meaningless"
    rc=1
    return
  fi
  cp "PRISTINE/$file" "$file"
  sed -i "$expr" "$file"
  if cmp -s "PRISTINE/$file" "$file"; then
    echo "*** MUTATION DID NOT APPLY (file unchanged) -- meaningless"
    rc=1
    return
  fi
  if ! build; then
    echo "*** the mutated tree did not build -- meaningless, not caught"
    rc=1
    cp "PRISTINE/$file" "$file"
    return
  fi
  if "$WORK/run" "$case_name" > "$WORK/out.txt" 2>&1; then
    echo "*** case '$case_name' STILL PASSED. Not constrained. ***"
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
  cp "PRISTINE/$file" "$file"
}

# A: THE regression this coverage assertion exists for. With the INT pin
# unmodelled the driver's drain loop never runs, the receive path never
# executes, and before the assertion existed everything stayed green.
mutate "A: the INT pin goes unmodelled again" \
  host_clock.cpp \
  's|if (g_chip \&\& pin == g_int_pin) return g_chip->intAsserted() ? 0 : 1;|;|' \
  boot \
  'the MCP2515 receive path actually ran'

# B (INT ignoring the CANINTE enable mask) MOVED, 2026-10-09, to
# rx_hook_mutations.sh as O. It is a datasheet behaviour of the chip model
# (p51 s7.0), asserted directly in test_mcp2515_fake ("a pending flag with
# CANINTE clear does NOT assert INT"), whose comment says no L2 assertion
# can see it: it only changes how long the drain loop spins. Run here
# against the cadence case it "survived" from 2026-10-07 -- aimed at the
# wrong test, not a gap. There it is caught on its own check.

# C RETIRED 2026-10-16: "a board that did not start sends all four
# diagnostic frames" is now what the spec requires. Since 2026-10-13 (8.2's
# B-7d row, d8c9e87d058abd52) such a board sends ONE power-up round of all
# four and then stays listen-only, and failed-start checks exactly that.
# C's pattern stopped applying when main.cpp changed for it, which the
# 2026-10-16 run reported as "MUTATION DID NOT APPLY".

# D, E (2026-10-09): spec 2's RTR exception, one driver at a time. Each
# port forwards remote frames; rtr-not-forwarded must fail on that
# direction's own check.
mutate "D: the vehicle port (TWAI) forwards RTR frames" \
  fw/port_twai.cpp \
  's|  if (m.rtr) return false;  // nothing on this bus uses remote frames|  ;|' \
  rtr-not-forwarded \
  'vehicle->charger: no RTR forwarded'

mutate "E: the charger port (MCP2515) forwards RTR frames" \
  fw/port_mcp2515.cpp \
  's|  if (rtr) return;|  ;|' \
  rtr-not-forwarded \
  'charger->vehicle: no RTR forwarded'

# F, G, H (2026-10-09): spec 2 B-7c / 8.2 bus-off through main.cpp.
mutate "F: a charger-side bus-off is left out of bridge_ok" \
  fw/main.cpp \
  's|  const uint32_t boe = vehicle.busOffEvents() + charger.busOffEvents();|  const uint32_t boe = vehicle.busOffEvents();|' \
  busoff-charger \
  'first status frame after the bus-off reports'

mutate "G: a vehicle-side bus-off is left out of bridge_ok" \
  fw/main.cpp \
  's|  const uint32_t boe = vehicle.busOffEvents() + charger.busOffEvents();|  const uint32_t boe = charger.busOffEvents();|' \
  busoff-vehicle \
  'first status frame after recovery reports'

mutate "H: the vehicle port does not restart the controller after recovery" \
  fw/port_twai.cpp \
  's|      if (twai_start() == ESP_OK) bus_off_ = false;   // the episode is over|      ;|' \
  busoff-vehicle \
  'it recovers and is RUNNING again'

# I (2026-10-09): spec 2.1 B-7d, a half-open bridge -- the board bridges
# when EITHER controller started. Expected check changed 2026-10-16: since
# 2026-10-13 a board that did not start is listen-only on the vehicle side
# after its power-up round, and its charger controller never left
# configuration mode, so a half-open bridge puts nothing on either wire and
# failed-start's two "nothing" checks cannot see it (on 2026-10-09 the
# vehicle-side one did). It is caught on bridge_ok, which `healthy` drives:
# the board would report a bridge it is not running.
mutate "I: a half-open bridge runs when only one controller started" \
  fw/main.cpp \
  's|  healthy = v_ok \&\& c_ok;|  healthy = v_ok \|\| c_ok;|' \
  failed-start \
  'bridge_ok is CLEAR on a board that is not bridging'

# J, K, L (2026-10-16, coverage gap 1): spec 8.2's other bridge_ok inputs.
# J removes the refused bridged send; K and L each drop one of the two TWAI
# receive-loss counters (both count: spec 461cf456998e419d). The accepted-
# then-failed route has no mutation: it FAILS unmutated (bridge-ok-veh-
# failed, a finding against the baseline, reported to the reviewer).
mutate "J: a bridged frame refused toward the vehicle is left out of bridge_ok" \
  fw/main.cpp \
  's|      if (e.port == TO_VEHICLE \&\& !mirror) veh_bridge_fail++;|      ;|' \
  bridge-ok-veh-refused \
  'refused toward the vehicle clears it'

mutate "K: frames lost to the TWAI driver's full RX queue are not counted" \
  fw/port_twai.cpp \
  's|    const uint32_t raw = st.rx_missed_count + st.rx_overrun_count;|    const uint32_t raw = st.rx_overrun_count;|' \
  bridge-ok-rx-missed \
  "full RX queue clear it"

mutate "L: a TWAI FIFO overrun is not counted" \
  fw/port_twai.cpp \
  's|    const uint32_t raw = st.rx_missed_count + st.rx_overrun_count;|    const uint32_t raw = st.rx_missed_count;|' \
  bridge-ok-rx-overrun \
  'FIFO overrun clears it'

# NOT run against failed-start-vehicle: the same half-open mutation
# SURVIVES there, tried twice on 2026-10-09.
#   1. Against wire silence: with the vehicle controller down a half-open
#      bridge puts nothing on any wire.
#   2. Against twai_transmit ATTEMPTS, refused ones included (reviewer's
#      suggestion; the counter's self-check shows it counts a refused
#      call): still zero, so the attempt is refused before the driver API.
# So "bridges nothing" with the vehicle controller down is observable at
# no interface the harness has. The case is not counted as B-7d coverage.

echo
if [ "$rc" -eq 0 ]; then
  echo "ALL MUTATIONS CAUGHT, each on its own check."
else
  echo "SOMETHING IS NOT COVERED -- see the markers above."
fi
exit "$rc"
