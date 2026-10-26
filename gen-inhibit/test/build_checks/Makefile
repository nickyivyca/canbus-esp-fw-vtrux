# E3 build-output checks. See elf_checks.py and config_vs_spec.py.
CC      ?= gcc
COREDIR ?= ../../main
CFLAGS  ?= -std=c11 -O1 -Wall -Wextra -I$(COREDIR)

all: config_probe

config_probe: config_probe.c $(COREDIR)/gen_inhibit_core.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f config_probe

.PHONY: all clean
