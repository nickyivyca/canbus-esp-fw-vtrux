#!/usr/bin/env bash
# session_pause.sh - sleep for N seconds, then exit cleanly
# Usage: bash session_pause.sh <seconds>
SECS=${1:-6300}
echo "Pausing for ${SECS}s (started at $(date))"
sleep "$SECS"
echo "Done at $(date)"
