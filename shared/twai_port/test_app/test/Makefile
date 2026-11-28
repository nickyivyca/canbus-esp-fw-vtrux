# Host tests of the probe's no-acknowledge modes and its register files
# (SPEC.md T13; the implementor's). See README.md.
#
#   make -C shared/twai_port/test_app/test run
#
# The binaries go to the worktree's scratch/ (AGENTS.md, "Files and
# folders"), never into this folder; give OUT=<folder> to put them elsewhere.

CC     ?= gcc
OUT    ?= ../../../../scratch/twai_port-probe-test
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -Imock

BINS := $(OUT)/test_unacked $(OUT)/test_probe_ll_c3 $(OUT)/test_probe_ll_s3 \
        $(OUT)/test_probe_ll_unsupported

all: $(BINS)

$(OUT)/test_unacked: test_unacked.c ../twai_port_unacked.c ../twai_port_unacked.h ../twai_port_probe_ll.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -Wno-missing-field-initializers -o $@ test_unacked.c ../twai_port_unacked.c

$(OUT)/test_probe_ll_c3: test_probe_ll.c ../twai_port_probe_ll_esp32c3.c ../twai_port_probe_ll.h $(wildcard mock/*/*.h)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ test_probe_ll.c ../twai_port_probe_ll_esp32c3.c

# The S3's file (T19), against the same mock: the S3's v5.4.2 twai_ll.h has
# the C3's calls and status bits.
$(OUT)/test_probe_ll_s3: test_probe_ll.c ../twai_port_probe_ll_esp32s3.c ../twai_port_probe_ll.h $(wildcard mock/*/*.h)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DCHIP='"s3"' -o $@ test_probe_ll.c ../twai_port_probe_ll_esp32s3.c

$(OUT)/test_probe_ll_unsupported: test_probe_ll.c ../twai_port_probe_ll_unsupported.c ../twai_port_probe_ll.h $(wildcard mock/*/*.h)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DPROBE_LL_UNSUPPORTED -o $@ test_probe_ll.c ../twai_port_probe_ll_unsupported.c

asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
asan: clean all

run: all
	$(OUT)/test_unacked
	$(OUT)/test_probe_ll_c3
	$(OUT)/test_probe_ll_s3
	$(OUT)/test_probe_ll_unsupported

clean:
	rm -f $(BINS)

.PHONY: all asan run clean
