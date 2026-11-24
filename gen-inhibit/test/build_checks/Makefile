# E3 build-output checks. See elf_checks.py and config_vs_spec.py.
#
# Since stage (b) batch 2 (gen-inhibit/main db1bfeae) the core packs its diag frames
# through shared/diag, so the probe adds the generated table and the pinned packer,
# as ../diag_host/diag_host.py gives them (DIAG_ARGS overrides it).
CC      ?= gcc
PYTHON  ?= python3.13
COREDIR ?= ../../firmware/main
CFLAGS  ?= -std=c11 -O1 -Wall -Wextra -I$(COREDIR)
DIAG_ARGS ?= $(shell $(PYTHON) -B ../diag_host/diag_host.py args)
ifeq ($(strip $(DIAG_ARGS)),)
$(error ../diag_host/diag_host.py gave no arguments: see its message above)
endif

all: config_probe

config_probe: config_probe.c $(COREDIR)/gen_inhibit_core.c $(COREDIR)/gi_diag_table.c
	$(CC) $(CFLAGS) -o $@ config_probe.c $(COREDIR)/gen_inhibit_core.c $(DIAG_ARGS)

clean:
	rm -f config_probe

.PHONY: all clean
