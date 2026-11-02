# The TWAI driver fake's own tests, and the real twai_port built on it
# (SPEC.md T10). See README.md.
#
#   make -C shared/twai_port/test run        # from the repo root
#   make -C shared/twai_port/test mutants    # mutate.py: every mutant must fail
#   make -C shared/twai_port/test mutate-test  # mutate.py removes its scratch folder, pass or fail
#   make -C shared/twai_port/test cxx        # the fake and cpp/ as C++11, as g++ builds them
#   make -C shared/twai_port/test cxx-test   # cpp/'s own tests (test_cpp_wrapper.cpp)
#   make -C shared/twai_port/test t12-standins  # the T12 cases fail on each broken restart stop
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
        $(wildcard include/soc/*.h) host/esp_rom_sys.h
PORT := ../twai_port.c ../include/twai_port.h ../../can_port/include/can_port.h \
        $(wildcard host/*.h host/*/*.h) host_platform.c

all: $(OUT)/test_twai_fake $(OUT)/test_port_on_fake_c3 $(OUT)/test_port_on_fake_s3

$(OUT)/test_twai_fake: test_twai_fake.c $(FAKE)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_twai_fake.c twai_fake.c

$(OUT)/test_port_on_fake_c3: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_esp32c3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -o $@ test_port_on_fake.c twai_fake.c host_platform.c \
	    ../twai_port.c ../twai_port_ll_esp32c3.c

$(OUT)/test_port_on_fake_s3: test_port_on_fake.c $(FAKE) $(PORT) ../twai_port_ll_unsupported.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DPORT_ON_S3 -o $@ test_port_on_fake.c twai_fake.c host_platform.c \
	    ../twai_port.c ../twai_port_ll_unsupported.c

# One process per case for the port: its state is file statics.
run: all
	$(OUT)/test_twai_fake
	@fail=0; for b in test_port_on_fake_c3 test_port_on_fake_s3; do \
	    for c in $$($(OUT)/$$b); do $(OUT)/$$b $$c || fail=1; done; \
	done; test $$fail -eq 0 && echo "test_port_on_fake: every case passed on C3 and S3"

# The T12 cases on the tester's own restart stop (t12_standin.c): as written
# it must pass every one, and each broken form must fail one by a check (exit
# 1 with a FAIL line), not by a sanitizer, whose exit codes are moved off 1.
T12_BROKEN_NAMES := "no wait" "no bound" "no reset on timeout" "no abort" "ignores the clock" \
                    "ignores reset held" "wrong step" "status not recorded" "counters not read"

$(OUT)/test_port_on_fake_t12: test_port_on_fake.c t12_standin.c $(FAKE) $(PORT) ../twai_port_ll_esp32c3.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) $(INC) -DT12_STANDIN -o $@ test_port_on_fake.c t12_standin.c twai_fake.c \
	    host_platform.c ../twai_port.c ../twai_port_ll_esp32c3.c

t12-standins: $(OUT)/test_port_on_fake_t12
	@ok=1; b=$(OUT)/test_port_on_fake_t12; cases=$$($$b | grep '^t12_'); \
	export ASAN_OPTIONS=exitcode=99 UBSAN_OPTIONS=halt_on_error=1:exitcode=98; \
	test -n "$$cases" || { echo "no t12 cases"; ok=0; }; \
	for c in $$cases; do $$b $$c > /dev/null || { echo "FAIL the standin as written fails $$c"; ok=0; }; done; \
	for n in $(T12_BROKEN_NAMES); do caught=""; \
	    for c in $$cases; do out=$$(T12_BROKEN="$$n" $$b $$c); rc=$$?; \
	        if [ $$rc -eq 1 ] && printf '%s\n' "$$out" | grep -q '^FAIL '; then caught="$$caught $$c"; \
	        elif [ $$rc -ne 0 ]; then echo "FAIL $$n: $$c exited $$rc, not by a check"; ok=0; fi; \
	    done; \
	    if [ -n "$$caught" ]; then echo "CAUGHT  $$n:$$caught"; else echo "MISSED  $$n"; ok=0; fi; \
	done; \
	test $$ok -eq 1 && echo "t12-standins: the standin as written passed every T12 case, every broken one caught"

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
