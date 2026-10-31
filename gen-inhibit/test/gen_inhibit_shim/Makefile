# E1: build gen_inhibit.c (the SHIM) on the host against mock platform headers.
#
# The sibling suite in ../gen_inhibit_host compiles only gen_inhibit_core.c and
# therefore cannot see any of this file. Four defects in the four days to
# 2026-09-25 lived exactly here.
#
# -Wconversion is deliberately NOT on: this compiles upstream wican-fw headers
# as well as our own, and their warnings would drown ours. The core keeps it.

CC      ?= gcc
COREDIR ?= ../../firmware/main
PYTHON  ?= python3

# THE STOCK HEADERS ARE THE SHARED COMPONENT'S (shared/wican W3, port step
# 1a). can.c and gen_inhibit.c include can.h and comm_server.h, which are
# vtrux/wican's now, so the shim compiles against the version gen-inhibit pins:
# its read-only checkout ../shared-<tag>/ beside the worktree root, found by
# wican_ref.py. Give WICANDIR, on the command line or in the environment, where
# that checkout is elsewhere -- in a copy of the project folder, as mutate.py
# makes, or on another machine.
ifneq ($(MAKECMDGOALS),clean)
WICANDIR ?= $(shell $(PYTHON) wican_ref.py)
ifeq ($(strip $(WICANDIR)),)
$(error no vtrux/wican headers: see wican_ref.py's message above, or give WICANDIR=<folder>)
endif
endif

# THE CAN PORT IS SHARED TOO (spec 3.3, port step 1b): vtrux/can_port and
# vtrux/twai_port, beside vtrux/wican in the same pinned checkout.
SHAREDDIR = $(WICANDIR)/..

CFLAGS  ?= -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter \
           -I. -Imock -I$(COREDIR) -I$(WICANDIR) \
           -I$(SHAREDDIR)/can_port/include -I$(SHAREDDIR)/twai_port/include
LDFLAGS ?= -pthread

# The CAN port is compiled AS SHIPPED, not reimplemented: can_port's stock
# calls (can_stock.c, which replaced main/can.c at port step 1b) and the real
# twai_port with its C3 register file, from the pinned shared checkout. The
# reviewing session once mutated the spec 3.2 refusal out of the real can.c and
# every case stayed green, because the old fake_can.c had its own copy of it.
# fake_port.c holds the port's two link stubs.
# gi_sched.c is compiled AS SHIPPED for the same reason: spec 5.2 item 8
# says the scheduler is platform-free and compiles unchanged on the host, and a
# reimplementation here would test the copy. mock/hal/twai_ll.h supplies the two
# private calls twai_port's C3 file reaches for, backed by fake_twai's own queue.
SRC := shim_test.c fake_twai.c fake_can.c fake_port.c \
       $(COREDIR)/gen_inhibit.c $(COREDIR)/gen_inhibit_core.c \
       $(COREDIR)/gi_sched.c \
       $(SHAREDDIR)/can_port/can_port.c $(SHAREDDIR)/can_port/can_stock.c \
       $(SHAREDDIR)/twai_port/twai_port.c $(SHAREDDIR)/twai_port/twai_port_ll_esp32c3.c

all: shim_test shim_test_autoarm

# THE LONG-ARM STIMULUS. Cases 33 and 53 replay a real 300 s capture, and the
# file they replay is generated, not in git. build_longarm.py builds it from the
# capture in the project folder named by REVERSE_IT_DIR, rebuilds it when it is
# older than the capture or the converter or was parsed with another canre pin
# (../../tools/bench/pins.json), and stops the build, naming the
# variable, when the folder is not set. It runs on every make and is a no-op when
# the file is current. An order-only prerequisite, so it never relinks shim_test.
# shim_test_autoarm does not replay anything and does not wait on it.
longarm:
	$(PYTHON) build_longarm.py

shim_test: $(SRC) fake_twai.h | longarm
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

# THE AUTO-ARM BUILD (spec 3.2, "arming comes after it"): the same sources with
# the boot auto-arm compiled in, running the one case that needs it. The other
# cases arm by hand and would meet an arm they did not make, so they are not in
# this binary -- hence -Wno-unused-function.
shim_test_autoarm: $(SRC) fake_twai.h
	$(CC) $(CFLAGS) -Wno-unused-function -DSHIM_AUTOARM_BUILD=1 -DGEN_INHIBIT_AUTOARM_MODE=GEN_INHIBIT_PASSIVE -o $@ $(SRC) $(LDFLAGS)

# The sanitiser build is where a mock-HAL suite earns its keep: it drives the
# shim's pointer and lifetime handling, which the core-only harness never does.
asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean shim_test

run: shim_test shim_test_autoarm
	./shim_test && ./shim_test_autoarm

clean:
	rm -f shim_test shim_test_autoarm

.PHONY: all longarm asan run clean
