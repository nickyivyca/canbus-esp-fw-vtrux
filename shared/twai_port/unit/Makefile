# The port's unit tests (SPEC.md T11): twai_port.c with the C3 register file,
# and with the S3 register file built three times (with the T19 measurement
# switch, with the testers' host-only switch, and with neither), compiled on
# the host against the scripted stub. See README.md.
#
#   make -C shared/twai_port/unit run
#
# The binaries go to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put them elsewhere.

CC     ?= gcc
OUT    ?= ../../../scratch/twai_port-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Imock -I../include -I../../can_port/include

COMMON := stub.c stub.h ../twai_port.c ../twai_port_ll.h ../include/twai_port.h \
          ../../can_port/include/can_port.h $(wildcard mock/*.h mock/*/*.h)
BINS := $(OUT)/test_twai_port $(OUT)/test_twai_port_s3 $(OUT)/test_twai_port_s3_probe \
        $(OUT)/test_twai_port_s3_host

all: $(BINS)

$(OUT)/test_twai_port: test_twai_port.c ../twai_port_ll_esp32c3.c $(COMMON)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ test_twai_port.c stub.c

$(OUT)/test_twai_port_s3: test_twai_port_s3.c ../twai_port_ll_esp32s3.c $(COMMON)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DTWAI_PORT_S3_PROBE=0 -o $@ test_twai_port_s3.c stub.c

$(OUT)/test_twai_port_s3_probe: test_twai_port_s3.c ../twai_port_ll_esp32s3.c $(COMMON)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DTWAI_PORT_S3_PROBE=1 -o $@ test_twai_port_s3.c stub.c

# The testers' host-only switch (twai_port_ll.h, TWAI_PORT_HOST_SWITCH).
$(OUT)/test_twai_port_s3_host: test_twai_port_s3.c ../twai_port_ll_esp32s3.c $(COMMON)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DTWAI_PORT_S3_PROBE=0 -DTWAI_PORT_HOST_SWITCH=1 -o $@ test_twai_port_s3.c stub.c

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_twai_port
	$(OUT)/test_twai_port_s3
	$(OUT)/test_twai_port_s3_probe
	$(OUT)/test_twai_port_s3_host

clean:
	rm -f $(BINS)

.PHONY: all asan run clean
