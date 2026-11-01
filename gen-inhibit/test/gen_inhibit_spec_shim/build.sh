#!/usr/bin/env bash
# Build the spec 12.4 shim cases (nonode_test) on the host, against the
# implementor's harness in ../gen_inhibit_shim (not edited) and main/ as shipped,
# with the tester's layer linked over the driver calls. Run from this folder:
#   bash build.sh <out binary> [extra CFLAGS...]
# Gen-inhibit tester, 2026-10-20; onto twai_port's host fake at port step 1b,
# 2026-10-31.
set -e
OUT=${1:?usage: build.sh <out binary> [extra CFLAGS...]}
shift
# a mutant replaces one of these three (mutate.py)
LAYER=${SPEC_LAYER:-spec_layer.c}
TEST=${NN_TEST:-nonode_test.c}
JUDGE=${NN_JUDGE:-nonode_judge.c}
CC=${CC:-gcc}
# The stock headers gen_inhibit.c includes (comm_server.h) are vtrux/wican's
# since port step 1a, and since port step 1b main/can.c is gone: the CAN code is
# vtrux/can_port and vtrux/twai_port, beside vtrux/wican in the same pinned
# shared checkout, and the TWAI driver is twai_port's one host fake there
# (twai_port T10). The checkout is found by the implementor's
# ../gen_inhibit_shim/wican_ref.py, or WICANDIR where it is elsewhere (a copy of
# the project folder, another machine).
WICANDIR=${WICANDIR:-$(${PYTHON:-python3} ../gen_inhibit_shim/wican_ref.py)}
SHARED=$WICANDIR/..
FAKE=$SHARED/twai_port/test
# The sources, include order and the install wrap are the shim's Makefile's
# (../gen_inhibit_shim/Makefile): its stand-ins first, then the fake's ESP-IDF
# TWAI headers, then the fake's minimal host stand-ins. shim_test.c is
# replaced by this folder's cases, judge and layer. The layer wraps five driver
# calls (spec_layer.c); shim_harness.c wraps twai_driver_install.
$CC -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter -Wno-unused-variable "$@" \
    -I. -I../gen_inhibit_shim -I../gen_inhibit_shim/mock -I../../firmware/main -I"$WICANDIR" \
    -I"$SHARED/can_port/include" -I"$SHARED/twai_port/include" \
    -I"$FAKE" -I"$FAKE/include" -I"$FAKE/host" \
    -o "$OUT" "$TEST" "$JUDGE" "$LAYER" \
    ../gen_inhibit_shim/shim_harness.c ../gen_inhibit_shim/fake_can.c ../gen_inhibit_shim/fake_port.c \
    "$FAKE/twai_fake.c" \
    ../../firmware/main/gen_inhibit.c ../../firmware/main/gen_inhibit_core.c ../../firmware/main/gi_sched.c \
    "$SHARED/can_port/can_port.c" "$SHARED/can_port/can_stock.c" \
    "$SHARED/twai_port/twai_port.c" "$SHARED/twai_port/twai_port_ll_esp32c3.c" \
    -pthread \
    -Wl,--wrap=twai_driver_install \
    -Wl,--wrap=twai_receive,--wrap=twai_get_status_info,--wrap=twai_read_alerts \
    -Wl,--wrap=twai_reconfigure_alerts,--wrap=twai_initiate_recovery
