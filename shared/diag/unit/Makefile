# shared/diag's unit tests of the C packer and the build id's packing. See
# README.md.
#
#   make -C shared/diag/unit run      # `asan` for the sanitiser build
#
# The binaries go to the worktree's scratch/ (AGENTS.md, "Files and folders"),
# never into this folder; give OUT=<folder> to put them elsewhere.

CC     ?= gcc
OUT    ?= ../../../scratch/diag-unit
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -I../include

DEPS := test_diag_pack.c ../diag_pack.c ../include/diag_pack.h ../include/diag_build_id.h

all: $(OUT)/test_diag_pack

$(OUT)/test_diag_pack: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ test_diag_pack.c ../diag_pack.c

$(OUT)/test_diag_pack_asan: $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -fsanitize=address,undefined -fno-sanitize-recover=undefined \
	    -o $@ test_diag_pack.c ../diag_pack.c

run: $(OUT)/test_diag_pack
	$(OUT)/test_diag_pack

asan: $(OUT)/test_diag_pack_asan
	$(OUT)/test_diag_pack_asan

.PHONY: all run asan
