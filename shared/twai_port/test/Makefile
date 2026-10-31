# The TWAI driver fake's own tests, and the real twai_port built on it
# (SPEC.md T10). See README.md.
#
#   make -C shared/twai_port/test run        # from the repo root
#   make -C shared/twai_port/test mutants    # mutate.py: every mutant must fail
#   make -C shared/twai_port/test cxx        # the fake and cpp/ as C++11, as g++ builds them
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

FAKE := twai_fake.c twai_fake.h include/driver/twai.h include/hal/twai_ll.h
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

mutants:
	$(PYTHON) mutate.py --cc $(CC)

cxx:
	mkdir -p $(OUT)
	$(CXX) -std=c++11 -O1 -Wall -Wextra $(INC) -Icpp -x c++ -c twai_fake.c -o $(OUT)/twai_fake_cxx.o
	$(CXX) -std=c++11 -O1 -Wall -Wextra $(INC) -Icpp -c cpp/twai_fake.cpp -o $(OUT)/twai_fake_cpp.o

clean:
	rm -f $(OUT)/test_twai_fake $(OUT)/test_port_on_fake_c3 $(OUT)/test_port_on_fake_s3 \
	    $(OUT)/twai_fake_cxx.o $(OUT)/twai_fake_cpp.o

.PHONY: all run mutants cxx clean
