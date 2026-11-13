# Host tests of the jammer, on the fakes in fake/. TEST EQUIPMENT, NOT FOR
# VEHICLE. They need no board and no ESP-IDF.
#   make -C test            build and run every test: ALL PASS, exit 0
#   make -C test PARSE=<file> ENGINE=<file> MAIN=<file> MCP=<file> \
#        CONSOLE=<file> OUT=<dir>
#                           against other copies of the sources (mutate.py)
CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1
PARSE ?= ../components/jam_pulse/jam_parse.c
ENGINE ?= ../components/jam_pulse/jam_engine.c
MAIN ?= ../main/jammer_main.c
MCP ?= ../main/mcp2515_reset.c
CONSOLE ?= ../main/console_usj.c
OUT ?= build

INC = -Ifake -I../components/jam_pulse/include -I../components/jam_pulse -I../main
HDRS = fake/fake_idf.h check.h ../components/jam_pulse/include/jam_pulse.h \
       ../components/jam_pulse/jam_internal.h ../main/console_usj.h \
       ../main/jammer_main.h ../main/mcp2515_reset.h
FAKE = fake/fake_idf.c

TESTS = test_jam_parse test_jam_engine_ab test_jam_engine_a \
        test_jam_engine_b test_mcp2515_reset test_main

all: run

$(OUT)/test_jam_parse: test_jam_parse.c $(PARSE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_jam_parse.c $(PARSE)

$(OUT)/test_jam_engine_ab: test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE)

$(OUT)/test_jam_engine_a: test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DJAM_TEST_STOP_A_ONLY $(INC) -o $@ test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE)

$(OUT)/test_jam_engine_b: test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DJAM_TEST_STOP_B_ONLY $(INC) -o $@ test_jam_engine.c $(PARSE) $(ENGINE) $(FAKE)

$(OUT)/test_mcp2515_reset: test_mcp2515_reset.c $(MCP) $(FAKE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_mcp2515_reset.c $(MCP) $(FAKE)

$(OUT)/test_main: test_main.c $(MAIN) $(CONSOLE) $(MCP) $(PARSE) $(ENGINE) $(FAKE) $(HDRS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_main.c $(MAIN) $(CONSOLE) $(MCP) $(PARSE) $(ENGINE) $(FAKE)

# Every test runs, and the exit is non-zero if any failed.
run: $(addprefix $(OUT)/,$(TESTS))
	@rc=0; for t in $(TESTS); do $(abspath $(OUT))/$$t || rc=1; done; exit $$rc

clean:
	rm -rf $(OUT)

.PHONY: all run clean
