# can_port's unit tests (SPEC.md C6): can_port.c and can_stock.c compiled on
# the host against a scripted backend, and can_port_restart.c (C7) against a
# scripted board and TWAI restart stop, built with and without the TWAI
# driver. See README.md.
#
#   make -C shared/can_port/unit run
#
# The binaries go to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put them elsewhere. The driver
# types and esp_err/esp_log come from twai_port's unit stand-ins, so the two
# suites build against one copy of them. -Wno-unused-parameter: can_stock.c
# keeps the stock code's unused parameters as they were.

CC     ?= gcc
OUT    ?= ../../../scratch/can_port-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Wno-unused-parameter -Imock -I../../twai_port/unit/mock -I../include

SRC := test_can_port.c
DEPS := $(SRC) ../can_port.c ../can_stock.c ../can_port_priv.h ../include/can_port.h ../include/can.h \
        $(wildcard mock/*.h mock/*/*.h ../../twai_port/unit/mock/*.h ../../twai_port/unit/mock/*/*.h)
RESTART_DEPS := test_can_port_restart.c ../can_port_restart.c ../include/can_port.h \
        ../../twai_port/include/twai_port.h \
        $(wildcard ../../twai_port/unit/mock/*.h ../../twai_port/unit/mock/*/*.h)
BINS := test_can_port test_can_port_restart_twai test_can_port_restart_none

all: $(addprefix $(OUT)/,$(BINS))

$(OUT)/test_can_port: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC)

# C7 as a build with vtrux/twai_port has it, and as one without.
$(OUT)/test_can_port_restart_twai: $(RESTART_DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../../twai_port/include -DCAN_PORT_HAVE_TWAI_PORT=1 -o $@ test_can_port_restart.c

$(OUT)/test_can_port_restart_none: $(RESTART_DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DCAN_PORT_HAVE_TWAI_PORT=0 -o $@ test_can_port_restart.c

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_can_port
	$(OUT)/test_can_port_restart_twai
	$(OUT)/test_can_port_restart_none

clean:
	rm -f $(addprefix $(OUT)/,$(BINS))

.PHONY: all asan run clean
