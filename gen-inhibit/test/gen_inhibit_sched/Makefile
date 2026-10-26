# The transmit scheduler's cases, spec 5.2 item 10 (emulation half).
#
# Needs only gcc (user, 2026-10-24): self-checks in WSL on NICKY-XPS
# (Ubuntu-20.04, gcc 9.4.0); evidence runs on madhouse-debian (gcc 14.2.0).
# See README.md.
#
#   make && ./sched_test
#   make asan && ./sched_test

CC      ?= gcc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -g -O1
INC     := -I../../firmware/main -I.

SRC := sched_test.c fake_buf.c ../../firmware/main/gi_sched.c

all: sched_test

sched_test: $(SRC) ../../firmware/main/gi_sched.h fake_buf.h
	$(CC) $(CFLAGS) $(INC) $(SRC) -o $@

# The scheduler copies frames and indexes ring buffers; an out-of-bounds write
# there would corrupt a neighbouring queue and look like a scheduling bug.
asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean sched_test

clean:
	rm -f sched_test

.PHONY: all asan clean
