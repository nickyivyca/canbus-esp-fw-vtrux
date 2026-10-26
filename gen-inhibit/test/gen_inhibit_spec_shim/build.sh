#!/usr/bin/env bash
# Build the spec 12.4 shim cases (nonode_test) on the host, against the
# implementor's fakes in ../gen_inhibit_shim (not edited) and main/ as shipped,
# with the tester's layer linked over the driver calls. Run from this folder:
#   bash build.sh <out binary> [extra CFLAGS...]
# Gen-inhibit tester, 2026-10-20.
set -e
OUT=${1:?usage: build.sh <out binary> [extra CFLAGS...]}
shift
# a mutant replaces one of these three (mutate.py)
LAYER=${SPEC_LAYER:-spec_layer.c}
TEST=${NN_TEST:-nonode_test.c}
JUDGE=${NN_JUDGE:-nonode_judge.c}
CC=${CC:-gcc}
$CC -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter -Wno-unused-variable "$@" \
    -I. -I../gen_inhibit_shim -I../gen_inhibit_shim/mock -I../../firmware/main \
    -o "$OUT" "$TEST" "$JUDGE" "$LAYER" \
    ../gen_inhibit_shim/fake_twai.c ../gen_inhibit_shim/fake_can.c \
    ../../firmware/main/gen_inhibit.c ../../firmware/main/gen_inhibit_core.c ../../firmware/main/gi_sched.c ../../firmware/main/can.c \
    -pthread \
    -Wl,--wrap=twai_receive,--wrap=twai_get_status_info,--wrap=twai_read_alerts,--wrap=twai_reconfigure_alerts,--wrap=twai_driver_install
