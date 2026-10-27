#!/usr/bin/env bash
# Mutation check for test_twai_port.
#
# `TwaiPort`'s rxDropped() and busOffEvents() had no executed test until
# 2026-10-04, and both carried a defect. This script puts each defect BACK
# and requires the test to fail on it -- otherwise the test is only
# describing the current code rather than constraining it.
#
# Mutation A is the one the test's own comment claims ("the max-form would
# have booked 0 here"). A claim in a comment is not evidence; this runs it.
#
# Run:  bash charge-interposer/harness_checks/twai_mutations.sh
#       (under WSL: wsl bash ..., from any folder)
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
WORK=/tmp/twai_mut
rm -rf "$WORK"
mkdir -p "$WORK"
cp "$HB"/*.cpp "$HB"/*.h "$WORK"/
mkdir -p "$WORK/driver"
cp "$HB"/driver/*.h "$WORK/driver"/
cp "$SRC"/port_twai.cpp "$SRC"/port_twai.h "$SRC"/can_port.h "$WORK"/
cd "$WORK" || exit 2

# Every non-test source in the work dir, as host_bridge/run.sh globs them
# (2026-10-18: a hand-kept list went stale when host_clock.cpp was added,
# and the control stopped linking).
build() {
  local srcs=(test_twai_port.cpp)
  for f in *.cpp; do
    case "$f" in test_*) continue ;; esac
    srcs+=("$f")
  done
  g++ -std=c++11 -O2 -I . -o "$WORK/run" "${srcs[@]}" 2>&1 | head -5
}

rc=0

echo "=== CONTROL (unmutated) ==="
build
if "$WORK/run" > "$WORK/control.txt" 2>&1; then
  echo "control PASSES, $(grep -c '^  ok' "$WORK/control.txt") checks ok"
else
  echo "*** CONTROL FAILED -- fix that before reading any mutation below ***"
  grep '^FAIL' "$WORK/control.txt" | head
  exit 1
fi

# $1 label, $2 sed expr, $3 unused (kept for readability), $4 expected failure text.
# $5 is optional: "expect-survives" means this mutation SHOULD NOT be caught,
# and the script fails if it ever is. See mutation C for why that is a real
# thing to assert rather than a way of excusing a gap.
mutate() {
  local label="$1" expr="$2" gone="$3" want="$4" mode="${5:-expect-caught}"
  echo
  echo "=== $label ==="
  cp "$SRC/port_twai.cpp" port_twai.cpp
  sed -i "$expr" port_twai.cpp
  # Verify by FILE CHANGE, not by a string vanishing: an insertion leaves
  # every original string in place, so a vanish-check cannot see it and
  # would report a no-op mutation as applied.
  if cmp -s "$SRC/port_twai.cpp" port_twai.cpp; then
    echo "*** MUTATION DID NOT APPLY (file unchanged) -- result meaningless"
    rc=1
    cp "$SRC/port_twai.cpp" port_twai.cpp
    return
  fi
  build
  local survived=0
  "$WORK/run" > "$WORK/out.txt" 2>&1 && survived=1

  if [ "$mode" = "expect-survives" ]; then
    if [ "$survived" -eq 1 ]; then
      echo "survived, as expected -- see the note above this mutation"
    else
      echo "*** CAUGHT, and it was expected to survive. The fake's alert"
      echo "*** behaviour has changed; re-justify it before accepting."
      grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -3
      rc=1
    fi
    cp "$SRC/port_twai.cpp" port_twai.cpp
    return
  fi

  if [ "$survived" -eq 1 ]; then
    echo "*** THE TEST STILL PASSED. It does not constrain this behaviour. ***"
    rc=1
  else
    echo "caught it:"
    grep '^FAIL' "$WORK/out.txt" | sed 's/^/    /' | head -3
    if grep '^FAIL' "$WORK/out.txt" | grep -qF "$want"; then
      echo "    (and on the expected check)"
    else
      echo "*** failed, but not on the expected check -- coverage is incidental ***"
      rc=1
    fi
  fi
  cp "$SRC/port_twai.cpp" port_twai.cpp
}

# A: the pre-2026-10-04 form. Takes the MAXIMUM of the controller's
# cumulative count, so after a reinstall restarts it at zero every loss is
# swallowed until the fresh count overtakes the old peak.
mutate "A: rxDropped takes the maximum instead of accumulating the delta" \
  's|rx_dropped_ += (raw >= rx_lost_raw_) ? (raw - rx_lost_raw_) : raw;|if (raw > rx_dropped_) rx_dropped_ = raw;|' \
  'rx_dropped_ += (raw >= rx_lost_raw_)' \
  'the port counts them'

# B: the bus-off flag left set by the restarting pass -- the defect this
# test found. A second episode is then never counted.
mutate "B: a successful restart does not end the bus-off episode" \
  's|if (twai_start() == ESP_OK) bus_off_ = false;   // the episode is over|twai_start();|' \
  'if (twai_start() == ESP_OK) bus_off_ = false;' \
  'a second bus-off is a second event'

# C: EXPECTED TO SURVIVE, and the reason is the useful part.
#
# `if (!bus_off_)` guards against the BUS_OFF alert being raised more than
# once for a single episode. Per the ESP-IDF header that cannot happen:
# TWAI_ALERT_BUS_OFF is "Bus-off condition OCCURRED" -- an edge raised on
# the transition -- and `twai_read_alerts` consumes it. So the alert stream
# a correct driver produces never exercises the guard, and no honest test
# can catch its removal. The guard stays as defence; it is not dead weight
# to be deleted on the strength of a mutation surviving.
#
# Asserting the survival is not an excuse for a gap, it is a tripwire on
# the FAKE: if this mutation ever starts being caught, the fake has begun
# re-raising BUS_OFF within one episode, which contradicts the header and
# needs justifying before anything resting on it is believed.
mutate "C: bus_off_events_ increments on every poll while bus-off" \
  's|if (!bus_off_) bus_off_events_++;|bus_off_events_++;|' \
  'if (!bus_off_) bus_off_events_++;' \
  '(not applicable)' \
  expect-survives

# E: read the pending-TX depth AFTER recovery instead of before.
#
# The ORDERING is the entire mechanism. `twai_initiate_recovery()` resets
# the TX queue, so a status read taken afterwards reports 0 and every
# discarded frame is counted as none. The counter, the accessor and the
# LOSS line all survive this mutation -- it still looks implemented.
mutate "E: the pending-TX depth is read after recovery, not before" \
  's|twai_status_info_t pre;|twai_initiate_recovery(); twai_status_info_t pre;|' \
  '(verified by file change)' \
  'the port counted them'

# D: drop the FIFO-overrun half of the loss figure.
mutate "D: rx_overrun_count ignored, only a full queue counts" \
  's|const uint32_t raw = st.rx_missed_count + st.rx_overrun_count;|const uint32_t raw = st.rx_missed_count;|' \
  'st.rx_missed_count + st.rx_overrun_count' \
  'rxDropped'

echo
if [ "$rc" -eq 0 ]; then
  echo "EVERY MUTATION BEHAVED AS SPECIFIED (C is expected to survive)."
else
  echo "SOMETHING IS NOT COVERED -- see the markers above."
fi
exit "$rc"
