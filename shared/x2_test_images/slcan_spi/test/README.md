# slcan_spi/test/ -- NOT FOR VEHICLE

Host unit tests of the protocol layer, `../main/slcan_proto.c`, built with
the machine's C compiler. They need no board and no ESP-IDF.

    make -C test                  build and run: ALL PASS, exit 0
    python3.13 -B test/mutate.py  the can-fail: every mutant must fail them

| File | What |
|---|---|
| `test_slcan_proto.c` | the tests, through a fake of the rest of the image: silent until opened, the rate rules, listen-only, the dropped-frame reports, frame parsing and formatting, the state and error lines in the kernel's forms, F, V, N, B, line handling |
| `mutate.py` | breaks one behaviour of `slcan_proto.c` at a time (14 mutants) and checks the tests fail on each, after checking they pass on the original; copies go in `<worktree>/scratch/slcan-spi-mutants/<pid>/`, removed at the end |
| `Makefile` | builds into `build/` (ignored); `SRC=` and `OUT=` for the mutants |
