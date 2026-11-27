#!/usr/bin/env bash
# TWAI_PORT_HOST_SWITCH is refused by every ESP-IDF build (vtrux_checks.json:
# "not a switch", a host-test-only define; charge-interposer reviewer,
# 2026-11-27). No image can carry it, so the evidence is the refusal itself:
#
#   1. control: an ESP-IDF configure of a minimal S3 project with
#      vtrux/twai_port, vtrux/can_port and the x2 board, without the define,
#      exits 0 and names the S3 register file;
#   2. the same configure with -DTWAI_PORT_HOST_SWITCH=1 exits non-zero and
#      prints the component's FATAL_ERROR message;
#   3. control: twai_port_ll.h compiles on the host with the define and
#      without ESP_PLATFORM (the testers' host builds);
#   4. the same compile with ESP_PLATFORM defined stops on its #error.
#
# Each refusal has its control, so a refusal for some other reason (a broken
# project, a missing header) fails the control, not only the refusal.
#
# Needs a shell exported for ESP-IDF v5.4.2 (local-paths.md), with
# check-env run first as AGENTS.md says. Run from anywhere:
#
#   bash shared/twai_port/test/host_switch_refused.sh
#
# HOST_SWITCH_REFUSED_BROKEN=1 is this script's own can-fail: the refused runs
# (2 and 4) are made without the define, and the script must then FAIL.
#
# The project and its build folders go to the worktree's scratch/ and are
# removed at the end, pass or fail. Exit 0: every step as expected. Exit 1:
# a step not as expected. Exit 2: refused, nothing checked.

set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
top="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" || {
    echo "host_switch_refused: REFUSED: not in a git worktree"; exit 2; }
if ! command -v idf.py >/dev/null 2>&1 || [ -z "${IDF_PATH:-}" ]; then
    echo "host_switch_refused: REFUSED: no ESP-IDF in this shell (export it per local-paths.md)"
    exit 2
fi

define="-DTWAI_PORT_HOST_SWITCH=1"
cdefine="-DTWAI_PORT_HOST_SWITCH=1"
if [ "${HOST_SWITCH_REFUSED_BROKEN:-}" = "1" ]; then
    echo "host_switch_refused: BROKEN form: the refused runs are made without the define"
    define=""
    cdefine=""
fi

work="$top/scratch/twai_port-host-switch"
cleanup() {
    if [ -e "$work" ]; then
        rm -rf "$work" || echo "host_switch_refused: could not remove $work"
    fi
}
trap cleanup EXIT
rm -rf "$work"
mkdir -p "$work/proj/main" || { echo "host_switch_refused: REFUSED: cannot make $work"; exit 2; }

cat > "$work/proj/CMakeLists.txt" <<EOF
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS "$top/shared/twai_port" "$top/shared/can_port" "$top/shared/boards/x2")
include(\$ENV{IDF_PATH}/tools/cmake/project.cmake)
project(host_switch_refused)
EOF
cat > "$work/proj/main/CMakeLists.txt" <<'EOF'
idf_component_register(SRCS "main.c" REQUIRES twai_port)
EOF
cat > "$work/proj/main/main.c" <<'EOF'
void app_main(void) {}
EOF

ok=1
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; ok=0; }

# configure <build folder> [extra idf.py args]: idf.py's exit code, log in <build folder>.log
configure() {
    local b="$1"; shift
    ( cd "$work/proj" && idf.py -B "$work/$b" -DIDF_TARGET=esp32s3 \
          -DSDKCONFIG="$work/$b/sdkconfig" "$@" reconfigure ) > "$work/$b.log" 2>&1
}

mkdir -p "$work/plain" "$work/switch"
configure plain; rc=$?
echo "      1. configure without the define: exit $rc"
if [ $rc -eq 0 ] && grep -q "vtrux/twai_port: register file twai_port_ll_esp32s3.c" "$work/plain.log"; then
    pass "1. without the define the configure succeeds and builds the S3 register file"
else
    fail "1. without the define the configure succeeds and builds the S3 register file"
    tail -n 20 "$work/plain.log"
fi

# shellcheck disable=SC2086
configure switch $define; rc=$?
msg="$(grep -o "vtrux/twai_port: TWAI_PORT_HOST_SWITCH is for host" "$work/switch.log" | head -n 1)"
echo "      2. configure with the define: exit $rc"
grep -A2 "CMake Error" "$work/switch.log" | head -n 6 | sed 's/^/      | /'
if [ $rc -ne 0 ] && [ -n "$msg" ]; then
    pass "2. with the define the configure is refused by the component's FATAL_ERROR"
else
    fail "2. with the define the configure is refused by the component's FATAL_ERROR"
fi

cc="${CC:-gcc}"
src="$work/ll.c"
printf '#include "twai_port_ll.h"\nint twai_port_ll_host_switch_refused_probe;\n' > "$src"
inc="-I$here/include -I$here/host -I$top/shared/twai_port -I$top/shared/twai_port/include -I$top/shared/can_port/include"

# shellcheck disable=SC2086
"$cc" -std=c11 -fsyntax-only $inc -DTWAI_PORT_HOST_SWITCH=1 "$src" > "$work/cc_host.log" 2>&1; rc=$?
echo "      3. host compile with the define, no ESP_PLATFORM: exit $rc"
if [ $rc -eq 0 ]; then
    pass "3. twai_port_ll.h compiles on the host with the define"
else
    fail "3. twai_port_ll.h compiles on the host with the define"
    head -n 10 "$work/cc_host.log"
fi

# shellcheck disable=SC2086
"$cc" -std=c11 -fsyntax-only $inc -DESP_PLATFORM $cdefine "$src" > "$work/cc_esp.log" 2>&1; rc=$?
echo "      4. host compile with the define and ESP_PLATFORM: exit $rc"
grep "#error\|error:" "$work/cc_esp.log" | head -n 2 | sed 's/^/      | /'
if [ $rc -ne 0 ] && grep -q "TWAI_PORT_HOST_SWITCH is for host builds on the T10 fake only" "$work/cc_esp.log"; then
    pass "4. with ESP_PLATFORM twai_port_ll.h stops the compile on its #error"
else
    fail "4. with ESP_PLATFORM twai_port_ll.h stops the compile on its #error"
fi

if [ $ok -eq 1 ]; then
    echo "host_switch_refused: PASS (IDF $(idf.py --version 2>/dev/null | head -n 1))"
    exit 0
fi
echo "host_switch_refused: FAIL"
exit 1
