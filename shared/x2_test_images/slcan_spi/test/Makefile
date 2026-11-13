# Host unit tests of slcan_spi's protocol layer. NOT FOR VEHICLE.
#   make -C test            build and run
#   make -C test SRC=<file> run against another copy of slcan_proto.c (mutate.py)
#   make -C test JSRC=<file> run against another copy of jam_frame.c (mutate.py)
CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Wno-unused-parameter -Werror -O1
SRC ?= ../main/slcan_proto.c
JSRC ?= ../main/jam_frame.c
OUT ?= build

all: run

$(OUT)/test_slcan_proto: test_slcan_proto.c $(SRC) ../main/slcan_proto.h ../main/can_frame.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../main -o $@ test_slcan_proto.c $(SRC)

$(OUT)/test_jam_frame: test_jam_frame.c $(JSRC) ../main/jam_frame.h ../main/can_frame.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../main -o $@ test_jam_frame.c $(JSRC)

run: $(OUT)/test_slcan_proto $(OUT)/test_jam_frame
	$(abspath $(OUT))/test_slcan_proto
	$(abspath $(OUT))/test_jam_frame

clean:
	rm -rf $(OUT)

.PHONY: all run clean
