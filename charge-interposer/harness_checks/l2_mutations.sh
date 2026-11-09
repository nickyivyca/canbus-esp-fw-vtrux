#!/usr/bin/env bash
# Mutation check for test_l2_bridge, and since port step 2b for
# test_canport_contract's vehicle half (Q-X, spec 2.4's counter mapping).
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
# Run:  bash charge-interposer/harness_checks/l2_mutations.sh
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
HB="$(cd "$CODE/fw_harness/host_bridge" && pwd)"
SRC="$(cd "$CODE/firmware/main" && pwd)"
# The work tree is outside the project, so l3_replay.cpp's relative
# candidates for its pinned stimulus cannot resolve; it reads this first.
# The fixture stays in SeaDrive (beside this script before the move): the
# runner's L3_STIMULUS if set, else under REVERSE_IT_DIR.
L3_STIMULUS="${L3_STIMULUS:-${REVERSE_IT_DIR:?REVERSE_IT_DIR is not set -- the SeaDrive reverse-it root}/projects/vtrux/notes/artifacts/interposer-firmware/l3_stimulus.txt}"
export L3_STIMULUS
# The shared folders, found as host_bridge/run.sh finds them, by its
# l2_build.sh (port step 2b: the vehicle port is the shared twai_port behind
# can_port, on the shared TWAI driver fake). They are compiled from where
# they are, never mutated here: every mutation below is of the interposer's
# own code or of this harness.
. "$HB/l2_build.sh"
hb_shared || exit 2
# The work folder is this run's own under the tree's scratch/, never the
# system temp folder, removed when the script exits (scratch_run.sh).
. "$CODE/scratch_run.sh" || exit 2
scratch_run l2-mutations || exit 2
WORK="$SCRATCH_RUN"
mkdir -p "$WORK/fw" "$WORK/obj" "$WORK/PRISTINE/fw"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/fw"/
cp "$HB"/*.cpp "$HB"/*.h "$WORK/PRISTINE"/
cp "$SRC"/*.cpp "$SRC"/*.h "$WORK/PRISTINE/fw"/
cd "$WORK" || exit 2
INC=(-I . -I fw "${HB_INC_SHARED[@]}")
# The C sources, once, as C, as run.sh compiles them.
if ! hb_c_objs "$WORK/obj" "$WORK" "$WORK/fw" > "$WORK/build.txt" 2>&1; then
  head -5 "$WORK/build.txt"
  echo "*** the C sources DID NOT BUILD -- nothing below would mean anything ***"
  exit 1
fi

# The same source set as host_bridge/run.sh builds test_l2_bridge from:
# every non-test .cpp in host_bridge (globbed, so a new shim cannot be
# missed the way l3_replay.cpp was), the shared fake's C++ interface, plus
# main.cpp, both ports and the core, from fw/ so a mutation of fw/main.cpp
# is the main.cpp that is compiled, and the C objects above.
# -> 0 if it built, 1 if not. The binary is removed first, so a failed
# build can never leave the previous one behind to be run.
build() {
  rm -f "$WORK/run"
  local srcs=(test_l2_bridge.cpp "$HB_SH/cpp/twai_fake.cpp") f
  for f in "$WORK"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  for f in "${HB_FW_L2[@]}"; do srcs+=("fw/$f"); done
  srcs+=(obj/*.o)
  if ! g++ -std=c++11 -O2 "${INC[@]}" -DINTP_NO_SERIAL=1 -o "$WORK/run" \
       "${srcs[@]}" > "$WORK/build.txt" 2>&1; then
    head -5 "$WORK/build.txt"
    return 1
  fi
  [ -x "$WORK/run" ]
}

# test_canport_contract, as run.sh builds it (since port step 2b it checks
# the vehicle port's PortView too, so port_view.h's counter mapping is
# mutated against it below, Q-X): from fw/, so a mutation of fw/port_view.h
# is the one compiled. -> 0 if it built, 1 if not; the binary is removed
# first.
build_contract() {
  rm -f "$WORK/contract"
  local srcs=(test_canport_contract.cpp "$HB_SH/cpp/twai_fake.cpp") f
  for f in "$WORK"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  srcs+=(fw/port_mcp2515.cpp fw/vehicle_port.cpp fw/port_view.cpp obj/*.o)
  if ! g++ -std=c++11 -O2 "${INC[@]}" -o "$WORK/contract" \
       "${srcs[@]}" > "$WORK/build.txt" 2>&1; then
    head -5 "$WORK/build.txt"
    return 1
  fi
  [ -x "$WORK/contract" ]
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
NEED="boot failed-start rtr-not-forwarded busoff-charger busoff-vehicle bridge-ok-veh-refused bridge-ok-rx-missed bridge-ok-rx-overrun failed-start-vehicle listen-only-switch listen-only-revert rx-lost-at-recovery"
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
# The contract test's control: it builds and passes unmutated.
if ! build_contract; then
  echo "*** CONTROL test_canport_contract DID NOT BUILD -- Q-X would mean nothing ***"
  exit 1
fi
if ! "$WORK/contract" > "$WORK/c_contract.txt" 2>&1; then
  echo "*** CONTROL FAILED on test_canport_contract, which Q-X run -- fix that first ***"
  grep -h '^FAIL' "$WORK/c_contract.txt" | head
  exit 1
fi
echo "control: test_canport_contract PASSES"

# $1 label, $2 file (relative to the work tree), $3 sed expr, $4 case of
# test_l2_bridge, or @contract for test_canport_contract, $5 expected
# failure text
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
  local bld=build cmd=("$WORK/run" "$case_name")
  if [ "$case_name" = "@contract" ]; then
    bld=build_contract
    cmd=("$WORK/contract")
  fi
  if ! "$bld"; then
    echo "*** the mutated tree did not build -- meaningless, not caught"
    rc=1
    cp "PRISTINE/$file" "$file"
    return
  fi
  if "${cmd[@]}" > "$WORK/out.txt" 2>&1; then
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
# D's site moved at port step 2b: the vehicle port is twai_port, which drops
# RTR frames when the interposer's setup asks it to (spec 2.4).
mutate "D: the vehicle port (TWAI) forwards RTR frames" \
  fw/vehicle_port.cpp \
  's|  c.drop_rtr = true;|  c.drop_rtr = false;|' \
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

# H's site moved at port step 2b: the restart after recovery is twai_port's
# (T2's automatic recovery), chosen by the interposer's setup (spec 2.4,
# "bus-off recovery automatic").
mutate "H: the vehicle port does not restart the controller after recovery" \
  fw/vehicle_port.cpp \
  's|  c.recovery = TWAI_PORT_RECOVERY_AUTO;|  c.recovery = TWAI_PORT_RECOVERY_NEVER;|' \
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

# K and L's site moved at port step 2b: the board's RX-loss count is the
# vehicle port's rx_lost (twai_port T7) as port_view reads it, and each
# mutation takes one of its parts back out.
mutate "K: frames lost to the TWAI driver's full RX queue are not counted" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_queue_full; }|' \
  bridge-ok-rx-missed \
  "full RX queue clear it"

mutate "L: a TWAI FIFO overrun is not counted" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_overrun; }|' \
  bridge-ok-rx-overrun \
  'FIFO overrun clears it'

# M (port step 2b, spec 2.4's D3): frames lost when automatic bus-off
# recovery restarts the controller are left out of the RX overflow count.
mutate "M: frames lost at the restart after recovery are not counted" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_restart; }|' \
  rx-lost-at-recovery \
  'counted as RX overflow'

# N (port step 2b; the charge-interposer reviewer's ruling (c),
# 2026-11-08): I's half-open bridge against failed-start-vehicle. Before 2b
# it SURVIVED there (2026-10-09: no wire showed it, and the attempt was
# refused before twai_transmit, so the attempt count stayed zero). The case
# now also judges the vehicle port's tx_refused, which counts that refusal.
mutate "N: a half-open bridge, judged with the vehicle controller down" \
  fw/main.cpp \
  's|  healthy = v_ok \&\& c_ok;|  healthy = v_ok \|\| c_ok;|' \
  failed-start-vehicle \
  'no send refused by the vehicle port'

# O, P (port step 2b, the same ruling): the board tries to send toward the
# vehicle while the port is listen-only -- O a forwarded charger frame (the
# listen-only gate of dispatch() removed), P its own status frames (the
# quiet gate removed). twai_port refuses both, so each case's "no transmit
# attempted" must see it.
mutate "O: frames toward the vehicle are sent in listen-only" \
  fw/main.cpp \
  's|    if (e.port == TO_VEHICLE \&\& vehicleListenOnly()) {|    if (false) {|' \
  listen-only-switch \
  'no transmit was attempted while the driver was listen-only'

mutate "P: the status frames are sent in listen-only" \
  fw/main.cpp \
  's|  const bool quiet = vehicleListenOnly();|  const bool quiet = false;|' \
  listen-only-revert \
  'no transmit attempted'

# Q-X (tester, 2026-11-09; the charge-interposer reviewer's ruling on the
# 2b fit): spec 2.4's counter mapping, port_view.h's lines, each against
# test_canport_contract's vehicle half (the PortView through CanPort&).
# Q, R and S take K, L and M's edits there; T-X are the other lines.
mutate "Q: rxDropped() leaves out the full receive queue (contract)" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_queue_full; }|' \
  @contract \
  'frames lost to the full receive queue raise rxDropped()'

mutate "R: rxDropped() leaves out the FIFO overrun (contract)" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_overrun; }|' \
  @contract \
  'frames lost to a FIFO overrun raise rxDropped()'

mutate "S: rxDropped() leaves out the restart after recovery (contract, D3)" \
  fw/port_view.h \
  's|  uint32_t rxDropped() const override { return status().rx_lost; }|  uint32_t rxDropped() const override { return status().rx_lost - status().rx_lost_restart; }|' \
  @contract \
  'restart after bus-off recovery raise rxDropped()'

mutate "T: txDropped() leaves out the sends refused for another reason (D2)" \
  fw/port_view.h \
  's|    return s.tx_dropped + s.tx_refused;|    return s.tx_dropped;|' \
  @contract \
  'refused with the port down raises txDropped()'

mutate "U: txDropped() leaves out the sends refused for a full queue (D2)" \
  fw/port_view.h \
  's|    return s.tx_dropped + s.tx_refused;|    return s.tx_refused;|' \
  @contract \
  'refused for a full queue raises txDropped()'

mutate "V: busErrors() is not the driver's count (D1)" \
  fw/port_view.h \
  's|  uint32_t busErrors() const override { return status().bus_errors; }|  uint32_t busErrors() const override { return status().bus_off_entries; }|' \
  @contract \
  'busErrors() rises by one per bus error'

mutate "W: busOff() is not the level (it stays set once an episode was seen)" \
  fw/port_view.h \
  's|  bool busOff() const override { return status().bus_off; }|  bool busOff() const override { return status().bus_off_entries > 0; }|' \
  @contract \
  'busOff() is clear once the controller has recovered'

mutate "X: busOffEvents() is the level, not the count of episodes" \
  fw/port_view.h \
  's|    return status().bus_off_entries;|    return status().bus_off ? 1u : 0u;|' \
  @contract \
  'busOffEvents() rises by one'

echo
if [ "$rc" -eq 0 ]; then
  echo "ALL MUTATIONS CAUGHT, each on its own check."
else
  echo "SOMETHING IS NOT COVERED -- see the markers above."
fi
exit "$rc"
