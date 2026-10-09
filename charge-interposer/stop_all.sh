#!/bin/bash
# Stop every interposer-bench process and prove it.
#
# Two traps this avoids (see AGENTS.md "Killing background processes"):
#   * `pkill -f interposer` also matches the bash wrapper running THIS script
#     and kills it mid-way. Matching /proc/<pid>/comm avoids that.
#   * run_scenario.py spawns children via sys.executable, so their command line
#     starts with /usr/bin/python3.13 -- an anchored '^python3.13' misses them,
#     and they keep transmitting after you believe they are dead.
for pid in $(pgrep -f 'tools/interposer' || true); do
  case "$(cat /proc/$pid/comm 2>/dev/null)" in
    python*) echo "  killing $pid"; kill -9 "$pid" 2>/dev/null || true ;;
  esac
done
sleep 1
# pgrep prints PIDs only, so filtering its output for "bash" matches nothing --
# re-check via /proc/<pid>/comm the same way the kill loop does.
survivors=0
for pid in $(pgrep -f 'tools/interposer' || true); do
  case "$(cat /proc/$pid/comm 2>/dev/null)" in python*) survivors=$((survivors+1)) ;; esac
done
echo "survivors: $survivors"
echo "bench sockets still open:"
ss -uanp 2>/dev/null | grep -E '432[0-9][0-9]' || echo "  none"
