# slcan_spi/test/ -- NOT FOR VEHICLE

Host unit tests of the protocol layer, `../main/slcan_proto.c`, and the jam
channel, `../main/jam_frame.c`, built with the machine's C compiler. They
need no board and no ESP-IDF.

    make -C test                  build and run: ALL PASS, exit 0
    python3.13 -B test/mutate.py  the can-fail: every mutant must fail them

| File | What |
|---|---|
| `test_slcan_proto.c` | the protocol's tests, through a fake of the rest of the image: silent until opened, the rate rules, listen-only, the dropped-frame reports, frame parsing and formatting, the state and error lines in the kernel's forms, F, V, N, B, line handling, and the jam channel's frames taken before any of it and never sent |
| `test_jam_frame.c` | the jam channel's tests (SPEC T13): the reserved identifiers, each command decoded to the jammer's text, every malformed frame on a reserved identifier refused, other frames left alone, reply lines split into signed frames and joined back as the bench harness joins them |
| `mutate.py` | breaks one behaviour of `slcan_proto.c` or `jam_frame.c` at a time (16 and 13 mutants) and checks the tests fail on each, after checking they pass on the originals; copies go in `<worktree>/scratch/slcan-spi-mutants/<pid>/`, removed at the end |
| `Makefile` | builds both tests into `build/` (ignored); `SRC=`, `JSRC=` and `OUT=` for the mutants |
