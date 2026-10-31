# The port's unit tests (SPEC.md T11): twai_port.c and the C3 register file
# compiled on the host against the scripted stub. See README.md.
#
#   make -C shared/twai_port/unit run
#
# The binary goes to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put it elsewhere.

CC     ?= gcc
OUT    ?= ../../../scratch/twai_port-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Imock -I../include -I../../can_port/include

SRC := test_twai_port.c stub.c
DEPS := $(SRC) stub.h ../twai_port.c ../twai_port_ll_esp32c3.c ../include/twai_port.h \
        ../../can_port/include/can_port.h $(wildcard mock/*.h mock/*/*.h)

all: $(OUT)/test_twai_port

$(OUT)/test_twai_port: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC)

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_twai_port

clean:
	rm -f $(OUT)/test_twai_port

.PHONY: all asan run clean
