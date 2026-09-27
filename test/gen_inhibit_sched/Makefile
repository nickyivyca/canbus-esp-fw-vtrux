# The transmit scheduler's cases, spec 5.2 item 10 (emulation half).
#
# Needs only gcc. NICKY-XPS has no native gcc -- run this on madhouse-debian
# (gcc 14.2.0, python3 3.13.5), per tools-reference.md.
#
#   make && ./sched_test
#   make asan && ./sched_test

CC      ?= gcc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -g -O1
INC     := -I../../main -I.

SRC := sched_test.c fake_buf.c ../../main/gi_sched.c

all: sched_test

sched_test: $(SRC) ../../main/gi_sched.h fake_buf.h
	$(CC) $(CFLAGS) $(INC) $(SRC) -o $@

# The scheduler copies frames and indexes ring buffers; an out-of-bounds write
# there would corrupt a neighbouring queue and look like a scheduling bug.
asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean sched_test

clean:
	rm -f sched_test

.PHONY: all asan clean
