# scratch_run.sh -- sourced by the harness's bash scripts, never run.
#
# A bash script's own run folder, as scratch_dir.py's make() gives one to
# the harness's Python (charge-interposer tester, 2026-11-02, the
# charge-interposer reviewer's item (B): the bash scripts wrote to the
# system temp folder, two of them to one fixed folder that two runs at once
# would share; AGENTS.md "Files and folders"):
#
#     <root>/charge-interposer-harness/<tool>/<tool>-<pid>-XXXXXX/
#         .scratch-owner.json   {"argv", "host", "made", "pid", "platform", "start"}
#
#   . "<charge-interposer>/scratch_run.sh" || exit 2
#   scratch_run <tool> || exit 2      # sets SCRATCH_RUN to the new folder
#
# <root> is scratch_dir.root()'s: <worktree>/scratch, the worktree found by
# walking up from this file to a .git, or in a copy with no git (the code
# the chain runner ships to madhouse) scratch/ beside the folder holding
# this file. The owner file is the one scratch_dir.state() reads, so the
# sweep and scratch_dir's prune tell a finished run's folder from a live
# one: the pid is the script's own ($$; on Windows Git Bash its Windows pid,
# /proc/$$/winpid), and the start time is the Linux one from /proc (field
# 22), or null where bash cannot read it (Windows), which state() takes as
# unsure for a running pid, so live.
#
# The folder is made with three tries, after waits of 0.1 s and 0.4 s, as
# scratch_dir.make's: another run's cleanup (below) can remove the tool
# folder while this one is making its run folder in it, and on Windows a
# folder removed while another process holds it stays delete-pending for a
# moment. The first two tries print nothing; the last prints its reason.
#
# It is removed when the script exits, pass, fail or refusal, from an EXIT
# trap that scratch_run sets: so the script sets no EXIT trap of its own.
# The trap runs rm -rf (Git Bash, like Linux, removes the current folder
# and read-only files; tried 2026-11-02), and while anything is left
# waits (0.1 s, then 0.4 s: Windows Search or Defender can hold a file for a
# moment), makes the tree writable (on Linux rm -rf cannot empty a folder
# without write permission) and tries again; it prints
# "scratch_run: LEFTOVER <folder>" to stderr if it still cannot; then the
# tool folder goes too if that leaves it empty (rmdir only, as
# scratch_dir.remove). The script's exit code is its own. A folder left by
# a killed run keeps its owner file, so the sweep takes it once the pid has
# gone.
#
# Test: scratch_run_test.py (with --can-fail), which also runs every bash
# script that uses this with the system temp folder empty and checks where
# its files went and that nothing is left.

_SR_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_RUN=""
SCRATCH_TOOL_DIR=""
# the waits before the second and third tries, and the removal's retries
SCRATCH_RUN_WAITS=(0.1 0.4)

scratch_root() {
  local d="$_SR_HOME" up
  while :; do
    if [ -e "$d/.git" ]; then echo "$d/scratch"; return 0; fi
    up="$(dirname "$d")"
    [ "$up" = "$d" ] && break
    d="$up"
  done
  echo "$(dirname "$_SR_HOME")/scratch"
}

_sr_json() {   # $1 as a JSON string
  local s="${1//\\/\\\\}"
  printf '"%s"' "${s//\"/\\\"}"
}

_sr_owner() {   # sets _SR_PID and _SR_OWNER, the owner file's text, for this script's process
  local start="null" platform stat f
  _SR_PID="$$"
  if [ -r "/proc/$$/winpid" ]; then
    _SR_PID="$(cat "/proc/$$/winpid")"
    platform="win32"
  else
    case "$(uname -s)" in
      Linux) platform="linux" ;;
      *) platform="$(uname -s | tr 'A-Z' 'a-z')" ;;
    esac
    if [ "$platform" = linux ] && stat="$(cat "/proc/$$/stat" 2>/dev/null)"; then
      read -r -a f <<< "${stat##*) }"
      start="${f[19]:-null}"
    fi
  fi
  _SR_OWNER="$(printf '{"argv": [%s], "host": %s, "made": "%s", "pid": %s, "platform": "%s", "start": %s}' \
    "$(_sr_json "$(basename "$0")")" "$(_sr_json "${HOSTNAME:-$(hostname)}")" \
    "$(date +%Y-%m-%dT%H:%M:%S)" "$_SR_PID" "$platform" "$start")"
}

scratch_run() {
  local tool="${1:-}" td p try
  case "$tool" in
    ""|.*|*/*|*\\*)
      echo "scratch_run: the tool must be one plain folder name: '$tool'" >&2
      return 2 ;;
  esac
  if [ -n "$SCRATCH_RUN" ]; then
    echo "scratch_run: this script already has a run folder ($SCRATCH_RUN)" >&2
    return 2
  fi
  _sr_owner
  td="$(scratch_root)/charge-interposer-harness/$tool"
  p=""
  for try in 1 2; do
    [ "$try" = 1 ] || sleep "${SCRATCH_RUN_WAITS[0]}"
    mkdir -p "$td" 2>/dev/null &&
      p="$(mktemp -d "$td/$tool-$_SR_PID-XXXXXX" 2>/dev/null)" && break
    p=""
  done
  if [ -z "$p" ]; then
    sleep "${SCRATCH_RUN_WAITS[1]}"
    mkdir -p "$td" || return 2
    p="$(mktemp -d "$td/$tool-$_SR_PID-XXXXXX")" || return 2
  fi
  SCRATCH_RUN="$p"
  SCRATCH_TOOL_DIR="$td"
  trap scratch_run_cleanup EXIT
  printf '%s\n' "$_SR_OWNER" > "$p/.scratch-owner.json" || return 2
}

scratch_run_cleanup() {
  local p="$SCRATCH_RUN" w
  [ -n "$p" ] || return 0
  rm -rf "$p"
  for w in "${SCRATCH_RUN_WAITS[@]}"; do
    [ -e "$p" ] || break
    sleep "$w"
    chmod -R u+w "$p" 2>/dev/null
    rm -rf "$p"
  done
  if [ -e "$p" ]; then
    echo "scratch_run: LEFTOVER $p -- rm -rf could not remove it (its reason is above)" >&2
  else
    rmdir "$SCRATCH_TOOL_DIR" 2>/dev/null
  fi
  SCRATCH_RUN=""
  return 0
}
