#!/bin/bash
# Stop every interposer-bench process and prove it.
#
#   bash stop_all.sh               stop the bench (Linux only)
#   bash stop_all.sh --self-test   prove the match on dummies (Linux only)
#   bash stop_all.sh --can-fail    run the self-test on each mutant copy;
#                                  every one must fail
#
# Two traps this avoids (see firmware/esp-development.md "Killing background
# processes"):
#   * `pkill -f interposer` also matches the bash wrapper running THIS script
#     and kills it mid-way. Matching /proc/<pid>/comm avoids that.
#   * run_scenario.py spawns children via sys.executable, so their command line
#     starts with /usr/bin/python3.13 -- an anchored '^python3.13' misses them,
#     and they keep transmitting after you believe they are dead.
# And a third (methodology, 2026-10-23): until then both loops matched only
# 'tools/interposer', the reverse-it path before the 2026-10-08 move. In this
# repo the processes run from charge-interposer/, so it killed nothing and
# reported "survivors: 0", a check that could not fail.
PAT='charge-interposer|tools/interposer'

if [ "$(uname -s)" != "Linux" ] || [ ! -d /proc/self ] ||
   ! command -v pgrep > /dev/null; then
  # elsewhere (Git Bash on Windows has a /proc but no Linux process table,
  # and no pgrep) the loops match nothing, and a "survivors: 0" would be the
  # same check that cannot fail
  echo "REFUSED: stop_all.sh needs Linux (/proc and pgrep); on Windows use"
  echo "the recipe in firmware/esp-development.md"
  exit 2
fi

# the PIDs of interpreter processes whose command line matches $1
bench_pids() {
  for pid in $(pgrep -f "$1" || true); do
    case "$(cat /proc/$pid/comm 2>/dev/null)" in
      python*) echo "$pid" ;;
    esac
  done
}

kill_all() {
  for pid in $(bench_pids "$1"); do
    echo "  killing $pid"
    kill -9 "$pid" 2>/dev/null || true
  done
}

# pgrep prints PIDs only, so filtering its output for "bash" matches nothing --
# re-check via /proc/<pid>/comm the same way the kill loop does.
count_survivors() {
  bench_pids "$1" | wc -l
}

# The self-test runs the kill and the count on dummies in a fresh temp
# directory, with the pattern narrowed to that directory, so no copy of this
# script (a mutant included) can touch any process but its own dummies. PAT
# itself is checked against the dummy's path separately.
self_test() {
  local fails=0 dir py a b n mine
  py=$(command -v python3)
  dir=$(mktemp -d "${TMPDIR:-/tmp}/stop_all_selftest.XXXXXX")
  mkdir -p "$dir/charge-interposer"
  mine="$dir/charge-interposer"
  if echo "/srv/x/charge-interposer/run_scenario.py" | grep -Eq "$PAT" &&
     echo "/srv/x/projects/vtrux/tools/interposer/run_scenario.py" | grep -Eq "$PAT"; then
    echo "  ok    the pattern matches a charge-interposer/ path and the old tools/interposer/ one"
  else
    echo "  FAIL  the pattern '$PAT' misses a charge-interposer/ or tools/interposer/ path"
    fails=$((fails+1))
  fi
  printf 'import time\ntime.sleep(60)\n' > "$mine/dummy_sim.py"
  "$py" "$mine/dummy_sim.py" < /dev/null > /dev/null 2>&1 &
  a=$!
  # a shell whose command line names the path, as the Bash tool's wrapper
  # does: it must be left alone
  bash -c "sleep 60; : $mine/wrapper" < /dev/null > /dev/null 2>&1 &
  b=$!
  sleep 0.5
  n=$(count_survivors "$mine")
  if [ "$n" = 1 ]; then echo "  ok    a live python dummy is counted as 1 survivor"
  else echo "  FAIL  a live python dummy counted as $n, not 1"; fails=$((fails+1)); fi
  kill_all "$mine" > /dev/null
  sleep 1
  if kill -0 "$a" 2>/dev/null; then
    echo "  FAIL  the python dummy survived the kill"; fails=$((fails+1))
    kill -9 "$a" 2>/dev/null
  else echo "  ok    the python dummy was killed"; fi
  n=$(count_survivors "$mine")
  if [ "$n" = 0 ]; then echo "  ok    0 survivors after the kill"
  else echo "  FAIL  $n survivor(s) after the kill"; fails=$((fails+1)); fi
  if kill -0 "$b" 2>/dev/null; then echo "  ok    a bash process naming the path was left alone"
  else echo "  FAIL  a bash process naming the path was killed"; fails=$((fails+1)); fi
  pkill -9 -P "$b" 2>/dev/null       # its sleep, then the shell itself
  kill -9 "$b" 2>/dev/null
  wait 2>/dev/null
  rm -rf "$dir"
  if [ "$fails" = 0 ]; then echo "stop_all self-test: all checks OK"; return 0; fi
  echo "stop_all self-test: $fails FAILED"
  return 1
}

# each: a label, then a sed expression that makes one mutant copy
MUTANTS=(
  "the pre-2026-10-23 pattern (tools/interposer only)"
  "s/^PAT='charge-interposer|tools\\/interposer'/PAT='tools\\/interposer'/"
  "the survivor count always 0"
  "s/^  bench_pids \"\$1\" | wc -l$/  echo 0/"
  "no comm filter (kills the wrapper shell too)"
  "s/^      python\\*) echo \"\$pid\" ;;$/      *) echo \"\$pid\" ;;/"
  "the kill loop sends nothing"
  "s/^    kill -9 \"\$pid\" 2>\\/dev\\/null || true$/    :/"
)

can_fail() {
  local i bad=0 tmp label rc
  for ((i = 0; i < ${#MUTANTS[@]}; i += 2)); do
    label=${MUTANTS[i]}
    tmp=$(mktemp "${TMPDIR:-/tmp}/stop_all_mutant.XXXXXX")
    sed "${MUTANTS[i+1]}" "$0" > "$tmp"
    if cmp -s "$0" "$tmp"; then
      echo "FAIL  $label: the edit matched nothing"
      bad=$((bad+1)); rm -f "$tmp"; continue
    fi
    bash "$tmp" --self-test > /dev/null 2>&1
    rc=$?
    if [ "$rc" = 1 ]; then echo "ok    $label: caught"
    else echo "FAIL  $label: self-test rc $rc -- the test checks nothing"; bad=$((bad+1)); fi
    rm -f "$tmp"
  done
  echo
  echo "$(( ${#MUTANTS[@]} / 2 - bad )) of $(( ${#MUTANTS[@]} / 2 )) mutants caught"
  [ "$bad" = 0 ]
}

case "$1" in
  --self-test) self_test; exit $? ;;
  --can-fail) can_fail; exit $? ;;
esac

kill_all "$PAT"
sleep 1
echo "survivors: $(count_survivors "$PAT")"
echo "bench sockets still open:"
ss -uanp 2>/dev/null | grep -E '432[0-9][0-9]' || echo "  none"
