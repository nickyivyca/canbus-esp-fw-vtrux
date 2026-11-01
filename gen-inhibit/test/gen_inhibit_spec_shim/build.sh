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
# NOT YET ON THE TWAI_PORT FAKE (port step 1b, 2026-10-31). main/can.c is gone:
# the CAN code is the shared can_port and twai_port, on twai_port's host fake,
# and the implementor's fake_twai.c is now shim_harness.c around that fake. This
# folder's spec_layer.c wraps the TWAI driver calls with a bus-off and error-
# counter model of its own, and defines twai_initiate_recovery; the fake models
# those itself, and shim_harness.c already wraps twai_driver_install. Moving
# these cases onto the fake is the tester's port step 1b acceptance item; until
# then this refuses, rather than compile a can.c that is not there. The build
# line below is the one before 1b, kept for that port.
echo "build.sh: the spec 12.4 shim cases are not yet ported to twai_port's host fake" \
     "(port step 1b: main/can.c is gone); nothing built" >&2
exit 2
# The stock headers that can.c and gen_inhibit.c include (can.h, comm_server.h)
# are vtrux/wican's since port step 1a: the pinned version's read-only checkout,
# found by the implementor's ../gen_inhibit_shim/wican_ref.py, or WICANDIR where
# that is elsewhere (a copy of the project folder, another machine).
WICANDIR=${WICANDIR:-$(${PYTHON:-python3} ../gen_inhibit_shim/wican_ref.py)}
$CC -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter -Wno-unused-variable "$@" \
    -I. -I../gen_inhibit_shim -I../gen_inhibit_shim/mock -I../../firmware/main -I"$WICANDIR" \
    -o "$OUT" "$TEST" "$JUDGE" "$LAYER" \
    ../gen_inhibit_shim/fake_twai.c ../gen_inhibit_shim/fake_can.c \
    ../../firmware/main/gen_inhibit.c ../../firmware/main/gen_inhibit_core.c ../../firmware/main/gi_sched.c ../../firmware/main/can.c \
    -pthread \
    -Wl,--wrap=twai_receive,--wrap=twai_get_status_info,--wrap=twai_read_alerts,--wrap=twai_reconfigure_alerts,--wrap=twai_driver_install
