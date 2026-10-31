# can_port's unit tests (SPEC.md C6): can_port.c and can_stock.c compiled on
# the host against a scripted backend. See README.md.
#
#   make -C shared/can_port/unit run
#
# The binary goes to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put it elsewhere. The driver
# types and esp_err/esp_log come from twai_port's unit stand-ins, so the two
# suites build against one copy of them. -Wno-unused-parameter: can_stock.c
# keeps the stock code's unused parameters as they were.

CC     ?= gcc
OUT    ?= ../../../scratch/can_port-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Wno-unused-parameter -Imock -I../../twai_port/unit/mock -I../include

SRC := test_can_port.c
DEPS := $(SRC) ../can_port.c ../can_stock.c ../can_port_priv.h ../include/can_port.h ../include/can.h \
        $(wildcard mock/*.h mock/*/*.h ../../twai_port/unit/mock/*.h ../../twai_port/unit/mock/*/*.h)

all: $(OUT)/test_can_port

$(OUT)/test_can_port: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC)

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_can_port

clean:
	rm -f $(OUT)/test_can_port

.PHONY: all asan run clean
