# Local paths for this worktree

This is the template for `local-paths.md`, which is gitignored and filled in
separately in each worktree with the local file paths this worktree's tools
use. If `local-paths.md` doesn't exist, copy this file to it. If you see this
text in `local-paths.md`, ask the user what information belongs here, write
it in, and delete this paragraph. Never commit `local-paths.md`; it stays
gitignored.

## Machine

<!-- The hostname, and the Python command to use on it. Source:
$REVERSE_IT_DIR/python-executables-reference.md -->

## Builds

<!-- The ESP-IDF install and how to export it in a shell; how to call
PlatformIO. Source: $REVERSE_IT_DIR/tools-reference.md -->

## Environment variables

<!-- This machine's values for REVERSE_IT_DIR, VTRUX_PUBLIC_REPO,
INTERPOSER_CODE and CAN_TOOLS_DIR, and how they are set (shell profile, user
environment). -->

## Other clones

<!-- The folder holding the anchor and the worktrees. Any other clone this
session works with, such as the public DBC repo or another role's
worktree. -->

## This worktree

<!-- Its role and its branch. If it is an extra worktree, its purpose. -->
