# The TWAI driver fake's own tests, and the real twai_port built on it
# (SPEC.md T10). See README.md.
#
#   make -C shared/twai_port/test run        # from the repo root
#   make -C shared/twai_port/test mutants    # mutate.py: every mutant must fail
#   make -C shared/twai_port/test mutate-test  # mutate.py removes its scratch folder, pass or fail
#   make -C shared/twai_port/test cxx        # the fake and cpp/ as C++11, as g++ builds them
#   make -C shared/twai_port/test cxx-test   # cpp/'s own tests (test_cpp_wrapper.cpp)
#   make -C shared/twai_port/test t12-standins  # the T12 cases fail on each broken restart stop
#   make -C shared/twai_port/test switch-standins  # the switch cases fail on each broken switch
#   make -C shared/twai_port/test switch-knobs  # the real switch on every unmeasured knob (a report)
#
# Binaries go to the worktree's scratch/ (AGENTS.md, "Files and folders");
# give OUT=<folder> to put them elsewhere.

CC     ?= gcc
CXX    ?= g++
PYTHON ?= python3
OUT    ?= ../../../scratch/twai_port-test
WARN   := -Wall -Wextra -Werror
CFLAGS ?= -std=c11 -O1 -g $(WARN) -fsanitize=address,undefined -fno-omit-frame-pointer
INC    := -Iinclude -Ihost -I. -I../include -I../../can_port/include

FAKE := twai_fake.c twai_fake.h include/driver/twai.h include/hal/twai_ll.h \
        $(wildcard include/soc/*.h) host/esp_rom_sys.h host/esp_cpu.h
PORT := ../twai_port.c ../include/twai_port.h ../../can_port/include/can_port.h \
        $(wildcard host/*.h host/*/*.h) host_platform.c

PORT_BUILDS := test_port_on_fake_c3 test_port_on_fake_s3 test_port_on_fake_other test_port_on_fake_s3probe

all: $(OUT)/test_twai_fake $(addprefix $(OUT)/,$(PORT_BUILDS)) $(OUT)/test_switch_on_fake

$(OUT)/test_twai_fake: test_twai_fake.c $(FAKE)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_twai_fake.c twai_fake.c

$(OUT)/test_port_on_fake_c3: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_esp32c3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_port_on_fake.c twai_fake.c host_platform.c \
	    ../twai_port.c ../twai_port_ll_esp32c3.c

# The S3's register file as shipped: not measured, so T8, T12 and the switch
# report unsupported.
$(OUT)/test_port_on_fake_s3: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_esp32s3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DPORT_ON_S3 -o $@ test_port_on_fake.c twai_fake.c host_platform.c \
	    ../twai_port.c ../twai_port_ll_esp32s3.c

# Any other chip's file, on the fake's S3.
$(OUT)/test_port_on_fake_other: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_unsupported.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DPORT_ON_S3 -o $@ test_port_on_fake.c twai_fake.c host_platform.c \
	    ../twai_port.c ../twai_port_ll_unsupported.c

# The S3's file as the T19 measurement build compiles it (TWAI_PORT_S3_PROBE):
# T8 and T12 supported, so the T12 cases run on it, with the two-core ones.
$(OUT)/test_port_on_fake_s3probe: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_esp32s3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DPORT_ON_S3 -DPORT_S3_PROBE -DTWAI_PORT_S3_PROBE=1 -o $@ \
	    test_port_on_fake.c twai_fake.c host_platform.c ../twai_port.c ../twai_port_ll_esp32s3.c

# The switch cases on the real port: the S3's file with the host-only
# TWAI_PORT_HOST_SWITCH, which lets it report the switch supported.
$(OUT)/test_switch_on_fake: test_switch_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_esp32s3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DTWAI_PORT_HOST_SWITCH=1 -o $@ test_switch_on_fake.c twai_fake.c \
	    host_platform.c ../twai_port.c ../twai_port_ll_esp32s3.c

# One process per case for the port: its state is file statics. The switch
# cases run here on the fake's defaults; `switch-knobs` runs them on every
# value of its unmeasured knobs.
run: all
	$(OUT)/test_twai_fake
	@fail=0; for b in $(PORT_BUILDS) test_switch_on_fake; do \
	    for c in $$($(OUT)/$$b); do $(OUT)/$$b $$c || fail=1; done; \
	done; test $$fail -eq 0 && echo "test_port_on_fake: every case passed on every build"

switch-knobs: $(OUT)/test_switch_on_fake
	@b=$(OUT)/test_switch_on_fake; for k in $(SWITCH_KNOBS); do n=0; f=""; \
	    for c in $$($$b); do n=$$((n+1)); \
	        SWITCH_RESET_TX=$${k%%:*} SWITCH_ABORT_REPORT=$${k##*:} $$b $$c > /dev/null || f="$$f $$c"; \
	    done; \
	    if [ -z "$$f" ]; then echo "$$k: all $$n passed"; else echo "$$k: FAILED$$f"; fi; \
	done

# The T12 cases on the tester's own restart stop (t12_standin.c): as written
# it must pass every one, and each broken form must fail one by a check (exit
# 1 with a FAIL line), not by a sanitizer, whose exit codes are moved off 1.
# Built twice: on the C3's file (one core), and on the S3's file in its T19
# measurement build with -DT12_STANDIN_TWO_CORES, where the two-core names
# apply too.
T12_BROKEN_NAMES := "no wait" "no bound" "no reset on timeout" "no abort" "ignores the clock" \
                    "ignores reset held" "wrong step" "status not recorded" "counters not read"
T12_BROKEN_C3 := $(T12_BROKEN_NAMES) "stalls a core"
T12_BROKEN_S3 := $(T12_BROKEN_NAMES) "no stall" "stall after steps" "restalls" "unstalls"

$(OUT)/test_port_on_fake_t12: test_port_on_fake.c t12_standin.c $(FAKE) $(PORT) ../twai_port_ll_esp32c3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DT12_STANDIN -o $@ test_port_on_fake.c t12_standin.c twai_fake.c \
	    host_platform.c ../twai_port.c ../twai_port_ll_esp32c3.c

$(OUT)/test_port_on_fake_t12_s3: test_port_on_fake.c t12_standin.c $(FAKE) $(PORT) ../twai_port_ll_esp32s3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DT12_STANDIN -DT12_STANDIN_TWO_CORES -DPORT_ON_S3 -DPORT_S3_PROBE \
	    -DTWAI_PORT_S3_PROBE=1 -o $@ test_port_on_fake.c t12_standin.c twai_fake.c \
	    host_platform.c ../twai_port.c ../twai_port_ll_esp32s3.c

t12-standins: $(OUT)/test_port_on_fake_t12 $(OUT)/test_port_on_fake_t12_s3
	@ok=1; export ASAN_OPTIONS=exitcode=99 UBSAN_OPTIONS=halt_on_error=1:exitcode=98; \
	run() { b=$(OUT)/$$1; shift; echo "$$b:"; cases=$$($$b | grep '^t12_'); \
	    test -n "$$cases" || { echo "no t12 cases"; ok=0; }; \
	    for c in $$cases; do $$b $$c > /dev/null || { echo "FAIL the standin as written fails $$c"; ok=0; }; done; \
	    for n in "$$@"; do caught=""; \
	        for c in $$cases; do out=$$(T12_BROKEN="$$n" $$b $$c); rc=$$?; \
	            if [ $$rc -eq 1 ] && printf '%s\n' "$$out" | grep -q '^FAIL '; then caught="$$caught $$c"; \
	            elif [ $$rc -ne 0 ]; then echo "FAIL $$n: $$c exited $$rc, not by a check"; ok=0; fi; \
	        done; \
	        if [ -n "$$caught" ]; then echo "CAUGHT  $$n:$$caught"; else echo "MISSED  $$n"; ok=0; fi; \
	    done; }; \
	run test_port_on_fake_t12 $(T12_BROKEN_C3); \
	run test_port_on_fake_t12_s3 $(T12_BROKEN_S3); \
	test $$ok -eq 1 && echo "t12-standins: the standin as written passed every T12 case on both builds, every broken one caught"

# The switch cases (test_switch_on_fake.c, T14-T18) on the tester's own
# stand-in port (switch_standin.c): as written it must pass every case on
# every value of the fake's unmeasured knobs, and each broken form must fail
# a case by a check, under the fake's defaults or with the abort reported
# failed.
SWITCH_BROKEN_NAMES := "no held bound" "no REC 128" "no REC 0" "REC not read back" \
                       "sends not refused" "teardown switch" "no hook" "held reversed" \
                       "queue left" "head only counted" "aborted head counted failed" "no last poll" \
                       "accepts unmeasured"
SWITCH_KNOBS := keep:success keep:failed resume:success resume:failed release:success release:failed

$(OUT)/test_switch_standin: test_switch_on_fake.c switch_standin.c $(FAKE) ../include/twai_port.h \
        ../../can_port/include/can_port.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_switch_on_fake.c switch_standin.c twai_fake.c

switch-standins: $(OUT)/test_switch_standin
	@ok=1; b=$(OUT)/test_switch_standin; cases=$$($$b); \
	export ASAN_OPTIONS=exitcode=99 UBSAN_OPTIONS=halt_on_error=1:exitcode=98; \
	test -n "$$cases" || { echo "no switch cases"; ok=0; }; \
	um() { [ "$$1" = t14_unmeasured_refused ] && echo SWITCH_UNMEASURED=1 || echo SWITCH_X=0; }; \
	for k in $(SWITCH_KNOBS); do \
	    for c in $$cases; do env $$(um $$c) SWITCH_RESET_TX=$${k%%:*} SWITCH_ABORT_REPORT=$${k##*:} $$b $$c > /dev/null \
	        || { echo "FAIL the standin as written fails $$c ($$k)"; ok=0; }; done; \
	done; \
	for n in $(SWITCH_BROKEN_NAMES); do caught=""; \
	    for a in success failed; do for c in $$cases; do \
	        out=$$(env $$(um $$c) SWITCH_BROKEN="$$n" SWITCH_ABORT_REPORT=$$a $$b $$c); rc=$$?; \
	        if [ $$rc -eq 1 ] && printf '%s\n' "$$out" | grep -q '^FAIL '; then caught="$$caught $$c/$$a"; \
	        elif [ $$rc -ne 0 ]; then echo "FAIL $$n: $$c ($$a) exited $$rc, not by a check"; ok=0; fi; \
	    done; done; \
	    if [ -n "$$caught" ]; then echo "CAUGHT  $$n:$$caught"; else echo "MISSED  $$n"; ok=0; fi; \
	done; \
	test $$ok -eq 1 && echo "switch-standins: the standin as written passed every switch case on every knob, every broken one caught"

mutants:
	$(PYTHON) mutate.py --cc $(CC)

mutate-test:
	$(PYTHON) test_mutate.py

cxx:
	mkdir -p $(OUT)
	$(CXX) -std=c++11 -O1 -Wall -Wextra $(INC) -Icpp -x c++ -c twai_fake.c -o $(OUT)/twai_fake_cxx.o
	$(CXX) -std=c++11 -O1 -Wall -Wextra $(INC) -Icpp -c cpp/twai_fake.cpp -o $(OUT)/twai_fake_cpp.o

cxx-test: cxx
	$(CXX) -std=c++11 -O1 -g -Wall -Wextra -fsanitize=address,undefined $(INC) -Icpp \
	    -o $(OUT)/test_cpp_wrapper cpp/test_cpp_wrapper.cpp cpp/twai_fake.cpp -x c++ twai_fake.c
	$(OUT)/test_cpp_wrapper

clean:
	rm -f $(OUT)/test_twai_fake $(OUT)/test_port_on_fake_c3 $(OUT)/test_port_on_fake_s3 \
	    $(OUT)/test_port_on_fake_t12 $(OUT)/twai_fake_cxx.o $(OUT)/twai_fake_cpp.o $(OUT)/test_cpp_wrapper

.PHONY: all run t12-standins mutants mutate-test cxx cxx-test clean
