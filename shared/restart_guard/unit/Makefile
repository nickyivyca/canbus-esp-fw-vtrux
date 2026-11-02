# restart_guard's unit tests (SPEC.md G6): the guard of restart_guard.c
# compiled on the host against a scripted board, restart stop and restart.
# See README.md.
#
#   make -C shared/restart_guard/unit run
#
# The binary goes to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put it elsewhere. esp_err.h,
# esp_attr.h, board.h and the driver types under can_port.h come from
# twai_port's unit stand-ins, so the suites build against one copy of them.

CC     ?= gcc
OUT    ?= ../../../scratch/restart_guard-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Imock -I../../twai_port/unit/mock -I../include -I../../can_port/include

SRC := test_restart_guard.c
DEPS := $(SRC) ../restart_guard.c ../include/restart_guard.h ../../can_port/include/can_port.h \
        $(wildcard mock/*.h mock/*/*.h ../../twai_port/unit/mock/*.h ../../twai_port/unit/mock/*/*.h)

all: $(OUT)/test_restart_guard

$(OUT)/test_restart_guard: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC)

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_restart_guard

clean:
	rm -f $(OUT)/test_restart_guard

.PHONY: all asan run clean
