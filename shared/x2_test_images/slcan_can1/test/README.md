# slcan_can1/test/

Host tests of the image, built with the machine's C compiler. They need no
board and no ESP-IDF.

    make -C test                  build and run: ALL PASS, exit 0
    python3.13 -B test/mutate.py  the can-fail: every mutant must fail them

| File | What |
|---|---|
| `test_app.c` | **the T8a check** (SPEC T8a) on the whole image, `../main/app_main.c` with the protocol, on `fake/`: at boot the port is set up and registered, never brought up, and nothing is written; idle passes bring nothing up; no frame reaches the port before an open, while listen-only, after a close or after a reset; `L` brings the port up listen-only and `O` normal, `C` takes it down; a board without its TWAI port never brings it up; received frames are drained, never forwarded; the port's settings, the USB buffers and the counters line from the port's status |
| `test_slcan1_proto.c` | the protocol's tests, through a fake of the rest of the image: silent until opened, listen-only, close, a refused open, the rates, the frames refused, the queue full, `V`, `N`, the counters line, line handling, an overlong line dropped with its tail |
| `check.h` | the tests' one assertion |
| `fake/` | host fakes of what `app_main.c` calls; see `fake/README.md` |
| `mutate.py` | breaks one behaviour of `slcan1_proto.c` or `app_main.c` at a time (19 and 14 mutants), the T8a ones first in each, and checks the tests fail on each, after checking they pass on the originals; copies go in `<worktree>/scratch/slcan-can1-mutants/<pid>/`, removed at the end |
| `Makefile` | builds both tests into `build/` (ignored); `SRC=`, `APP=` and `OUT=` for the mutants |
