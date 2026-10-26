#!/usr/bin/env bash
#
# The host and compiler of a host C build, as one line for the run's log:
#   # host <uname -n>; <first line of gcc --version>
# Exit 1 if either is missing, so a script under `set -e` stops before building.
# Run it in the shell the build runs in, e.g. ssh "$REMOTE" "bash -s" < host_identity.sh,
# or bash host_identity.sh under WSL. Only a run built on madhouse-debian with its
# gcc 14.2 counts for VERIFICATION.md; a WSL build on NICKY-XPS is a self-check
# (user, 2026-10-24, through the reviewer). Gen-inhibit tester, 2026-10-24.
h=$(uname -n 2>/dev/null)
c=$(gcc --version 2>/dev/null | head -1)
echo "# host ${h:-NOT RECORDED}; ${c:-compiler NOT RECORDED}"
[ -n "$h" ] && [ -n "$c" ]
