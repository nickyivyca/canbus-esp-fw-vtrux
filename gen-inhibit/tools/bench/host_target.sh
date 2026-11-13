# host_target.sh -- sourced by baseline_host_run.sh, gi_host_run.sh, shim_wire_probe.sh,
# shim_7f3_probe.sh and the emulated suite's B15host: where their remote commands run.
# Gen-inhibit tester, 2026-11-12 (the probes and B15host the same day, gen-inhibit
# reviewer, after the first two).
#
# THE LOCAL MODE (gen-inhibit reviewer, 2026-11-12, approving the Linux leftovers'
# finding): the bench host is madhouse-debian, both scripts' REMOTE defaults to it,
# and madhouse-debian cannot ssh to itself ("Host key verification failed"), so on the
# bench host both scripts stopped at their first ssh. When REMOTE names this host, the
# same commands run here without ssh, and the run's record says local mode and names
# the host, so a run here never reads as a remote one.
#
# REMOTE names this host when its host part (after any user@) is this machine's
# hostname, short or full (any case), localhost, 127.0.0.1 or ::1, and its user part,
# if any, is this user. An ssh alias for this host is not looked up: it goes to ssh as
# before. Anything else goes to ssh as before.
#
# A local command runs as ssh would run it: `bash -c "<command>"` from $HOME, with
# stdin, stdout, stderr and the exit status passed through, and an environment holding
# only HOME, USER, LOGNAME, PATH and LANG. The ssh shell on the remote doesn't have
# this shell's exports (REVERSE_IT_DIR above all, which the scripts export in the
# command where it is needed), so a local run that saw them could pass where the
# remote one would fail. HOST_BASH names the shell (default bash), so a test can stand
# in for it.
#
# HOST_SSH_BATCH=1 adds -o BatchMode=yes to each ssh (the suite's B15host, which had it
# before, so a missing key fails at once instead of asking for a password).

# host_target: from $REMOTE, set HOST_LOCAL (1 or 0) and HOST_MODE, the record's line.
# Returns 2, setting neither, when REMOTE is not a host name.
host_target() {
  case "$REMOTE" in
    "" | *[!A-Za-z0-9@._:-]*)
      echo "REMOTE=$REMOTE is not a host name (letters, digits and @ . _ : - only)" >&2
      return 2 ;;
  esac
  local user="" host="$REMOTE" me n
  case "$REMOTE" in *@*) user="${REMOTE%@*}"; host="${REMOTE##*@}" ;; esac
  me="$(hostname)"
  HOST_LOCAL=0
  if [ -z "$user" ] || [ "$user" = "$(id -un)" ]; then
    for n in "$me" "$(hostname -s 2>/dev/null)" "$(hostname -f 2>/dev/null)" localhost 127.0.0.1 ::1; do
      if [ -n "$n" ] && [ "${host,,}" = "${n,,}" ]; then HOST_LOCAL=1; break; fi
    done
  fi
  if [ "$HOST_LOCAL" = 1 ]; then
    HOST_MODE="local mode: REMOTE=$REMOTE names this host, $me; the commands run here without ssh"
  else
    HOST_MODE="remote: ssh $REMOTE, from $me"
  fi
}

# on_host <command>: run one remote command, on REMOTE over ssh or here in local mode.
on_host() {
  if [ "${HOST_LOCAL:-0}" = 1 ]; then
    (cd "$HOME" && env -i HOME="$HOME" USER="${USER:-}" LOGNAME="${LOGNAME:-}" PATH="$PATH" \
      LANG="${LANG:-C.UTF-8}" "${HOST_BASH:-bash}" -c "$1")
  else
    ssh ${HOST_SSH_BATCH:+-o BatchMode=yes} "$REMOTE" "$1"
  fi
}
