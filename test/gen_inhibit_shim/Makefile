# E1: build gen_inhibit.c (the SHIM) on the host against mock platform headers.
#
# The sibling suite in ../gen_inhibit_host compiles only gen_inhibit_core.c and
# therefore cannot see any of this file. Four defects in the four days to
# 2026-09-25 lived exactly here.
#
# -Wconversion is deliberately NOT on: this compiles upstream wican-fw headers
# as well as our own, and their warnings would drown ours. The core keeps it.

CC      ?= gcc
COREDIR ?= ../../main
CFLAGS  ?= -std=c11 -O1 -g -Wall -Wextra -Wno-unused-parameter \
           -I. -Imock -I$(COREDIR)
LDFLAGS ?= -pthread

# main/can.c is compiled AS SHIPPED, not reimplemented: the reviewing session
# mutated the spec 3.2 refusal out of the real file and every case stayed green,
# because the old fake_can.c had its own copy of it.
SRC := shim_test.c fake_twai.c fake_can.c \
       $(COREDIR)/gen_inhibit.c $(COREDIR)/gen_inhibit_core.c \
       $(COREDIR)/can.c

all: shim_test

shim_test: $(SRC) fake_twai.h
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

# The sanitiser build is where a mock-HAL suite earns its keep: it drives the
# shim's pointer and lifetime handling, which the core-only harness never does.
asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean shim_test

run: shim_test
	./shim_test

clean:
	rm -f shim_test

.PHONY: all asan run clean
