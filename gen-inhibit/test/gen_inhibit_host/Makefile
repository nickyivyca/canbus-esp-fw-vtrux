# Host build of gen_inhibit_core. No ESP-IDF, no hardware.
#
# -Wconversion is on deliberately: the core mixes uint8/uint32/int32/int64 and
# an implicit narrowing is exactly the kind of bug a replay trace would show
# as a plausible-looking wrong number rather than as a crash.
#
# Since stage (b) batch 2 (gen-inhibit/main db1bfeae) the core packs its diag
# frames through shared/diag: the build adds the generated table
# (firmware/main/gi_diag_table.c) and the pinned shared/diag's diag_pack.c, as
# ../diag_host/diag_host.py gives them. host_runner also links
# ../diag_host/misfit_wrap.c with --wrap=diag_pack_message, so the packer's
# misfit report reaches the trace (spec 12.3 item 2). The core uses __typeof__,
# so CC must be GCC or compatible. DIAG_ARGS and DIAG_WRAP_ARGS override the
# helper, e.g. on a machine whose shared checkouts are elsewhere.

CC      ?= gcc
PYTHON  ?= python3.13
CFLAGS  ?= -std=c11 -O2 -g -Wall -Wextra -Wpedantic -Wconversion -Wshadow \
           -Wstrict-prototypes -Wmissing-prototypes
COREDIR ?= ../../firmware/main
INCS    := -I$(COREDIR)
DIAG_ARGS      ?= $(shell $(PYTHON) -B ../diag_host/diag_host.py args)
DIAG_WRAP_ARGS ?= $(shell $(PYTHON) -B ../diag_host/diag_host.py args --wrap)
ifeq ($(strip $(DIAG_WRAP_ARGS)),)
$(error ../diag_host/diag_host.py gave no arguments: see its message above)
endif
DIAG_DEPS := $(COREDIR)/gi_diag_table.c $(COREDIR)/gi_diag_table.h \
             ../diag_host/misfit_wrap.c ../diag_host/misfit_wrap.h

BIN := host_runner extract_probe

all: $(BIN)

host_runner: host_runner.c $(COREDIR)/gen_inhibit_core.c $(COREDIR)/gen_inhibit_core.h $(DIAG_DEPS)
	$(CC) $(CFLAGS) $(INCS) -o $@ host_runner.c $(COREDIR)/gen_inhibit_core.c $(DIAG_WRAP_ARGS)

extract_probe: extract_probe.c $(COREDIR)/gen_inhibit_core.c $(COREDIR)/gen_inhibit_core.h $(DIAG_DEPS)
	$(CC) $(CFLAGS) $(INCS) -o $@ extract_probe.c $(COREDIR)/gen_inhibit_core.c $(DIAG_ARGS)

# A sanitiser build catches what -O2 hides. Same sources, different binary.
asan: CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer -O1
asan: clean $(BIN)

clean:
	rm -f $(BIN) *.o

.PHONY: all asan clean
