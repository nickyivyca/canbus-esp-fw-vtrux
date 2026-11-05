# scratch_run.sh -- sourced by the harness's bash scripts, never run.
#
# A bash script's own run folder, as scratch_dir.py's make() gives one to
# the harness's Python (charge-interposer tester, 2026-11-02, the
# charge-interposer reviewer's item (B): the bash scripts wrote to the
# system temp folder, two of them to one fixed folder that two runs at once
# would share; AGENTS.md "Files and folders"):
#
#     <root>/cih/<tool>/<pid>-XXXXXX/
#         .scratch-owner.json   {"argv", "host", "made", "pid", "platform", "start"}
#
# The short names, and the length guard, are scratch_dir.py's (the
# charge-interposer reviewer, 2026-11-05: Windows' path limit in the
# acceptance runner's trees): the run folder's full path plus
# SCRATCH_RUN_DEPTH must be at most SCRATCH_RUN_LIMIT, the same numbers as
# scratch_dir's DEPTH and LIMIT, or scratch_run refuses (exit 2) before it
# makes anything, naming the full path and its length.
#
# The git ceiling, as scratch_dir's: the run folder goes first in the
# exported GIT_CEILING_DIRECTORIES (';' between entries on Windows, where it
# is given in Git for Windows' C:/ form; ':' elsewhere), so git never looks
# for a repository above it, and a fixture whose own git init failed gets
# "not a git repository" rather than the enclosing repo.
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
# the length guard: scratch_dir.py's LIMIT and DEPTH (scratch_run_test
# checks they agree)
SCRATCH_RUN_LIMIT=240
SCRATCH_RUN_DEPTH=12

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
  td="$(scratch_root)/cih/$tool"
  local full="$td/$_SR_PID-XXXXXX" n
  n=$(( ${#full} + SCRATCH_RUN_DEPTH ))
  if [ "$n" -gt "$SCRATCH_RUN_LIMIT" ]; then
    echo "scratch_run: REFUSED the run folder $full: it is ${#full} characters, and its fixtures' git goes up to $SCRATCH_RUN_DEPTH below it, $n in all, past the $SCRATCH_RUN_LIMIT that git for Windows needs (it will not make a folder past about 248, core.longpaths or not). Run from a tree with a shorter path" >&2
    return 2
  fi
  p=""
  for try in 1 2; do
    [ "$try" = 1 ] || sleep "${SCRATCH_RUN_WAITS[0]}"
    mkdir -p "$td" 2>/dev/null &&
      p="$(mktemp -d "$td/$_SR_PID-XXXXXX" 2>/dev/null)" && break
    p=""
  done
  if [ -z "$p" ]; then
    sleep "${SCRATCH_RUN_WAITS[1]}"
    mkdir -p "$td" || return 2
    p="$(mktemp -d "$td/$_SR_PID-XXXXXX")" || return 2
  fi
  SCRATCH_RUN="$p"
  SCRATCH_TOOL_DIR="$td"
  trap scratch_run_cleanup EXIT
  printf '%s\n' "$_SR_OWNER" > "$p/.scratch-owner.json" || return 2
  local ceil="$p" sep=":"
  if [ -r "/proc/$$/winpid" ]; then
    ceil="$(cd "$p" && pwd -W)" || return 2
    sep=";"
  fi
  export GIT_CEILING_DIRECTORIES="$ceil${GIT_CEILING_DIRECTORIES:+$sep$GIT_CEILING_DIRECTORIES}"
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
