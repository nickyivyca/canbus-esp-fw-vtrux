# Host tests of slcan_can1: the protocol layer, and T8a on the whole image
# built on fakes (README.md).
#   make -C test             build and run both
#   make -C test SRC=<file>  run against another copy of slcan1_proto.c (mutate.py)
#   make -C test APP=<file>  run against another copy of app_main.c (mutate.py)
CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Wno-unused-parameter -Werror -O1
SRC ?= ../main/slcan1_proto.c
APP ?= ../main/app_main.c
OUT ?= build

all: run

$(OUT)/test_slcan1_proto: test_slcan1_proto.c check.h $(SRC) ../main/slcan1_proto.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../main -o $@ test_slcan1_proto.c $(SRC)

# fake/ first, so the app's ESP-IDF and shared-port headers are the fakes.
$(OUT)/test_app: test_app.c check.h $(APP) $(SRC) ../main/slcan1_proto.h ../main/slcan1_app.h fake/fake_idf.c fake/fake_idf.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -Ifake -I../main -o $@ test_app.c $(APP) $(SRC) fake/fake_idf.c

run: $(OUT)/test_slcan1_proto $(OUT)/test_app
	$(abspath $(OUT))/test_slcan1_proto
	$(abspath $(OUT))/test_app

clean:
	rm -rf $(OUT)

.PHONY: all run clean
