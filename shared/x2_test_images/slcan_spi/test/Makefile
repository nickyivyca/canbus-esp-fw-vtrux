# Host unit tests of slcan_spi's protocol layer. NOT FOR VEHICLE.
#   make -C test            build and run
#   make -C test SRC=<file> run against another copy of slcan_proto.c (mutate.py)
CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Wno-unused-parameter -Werror -O1
SRC ?= ../main/slcan_proto.c
OUT ?= build

all: run

$(OUT)/test_slcan_proto: test_slcan_proto.c $(SRC) ../main/slcan_proto.h ../main/can_frame.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../main -o $@ test_slcan_proto.c $(SRC)

run: $(OUT)/test_slcan_proto
	$(abspath $(OUT))/test_slcan_proto

clean:
	rm -rf $(OUT)

.PHONY: all run clean
