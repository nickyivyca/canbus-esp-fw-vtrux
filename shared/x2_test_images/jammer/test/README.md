# jammer/test/ -- TEST EQUIPMENT, NOT FOR VEHICLE

Host tests of the jammer's sources, built with the machine's C compiler on
fakes of the ESP-IDF, FreeRTOS and board interfaces (`fake/`), on an
emulated microsecond clock. They need no board and no ESP-IDF. The engine
is judged from the emulated wire (what the RMT put on the pin), not from
what it replies.

    make -C test                          build and run: each ALL PASS, exit 0
    python3.13 -B test/mutate.py          the can-fail: every mutant must fail them

Run the mutants in `build.slice` on `madhouse-debian`
(`systemd-run --user --scope --slice=build.slice ...`).

| File | What |
|---|---|
| `test_jam_parse.c` | the parser: every limit at and past its edge, the refusals and their order, CAPPED |
| `test_jam_engine.c` | the engine, built three times (both stops, `JAM_TEST_STOP_A_ONLY`, `JAM_TEST_STOP_B_ONLY`): pin recessive at start and after every command, inverted output with a recessive end level, pulse widths, refusals put nothing on the wire, each stop ending a train at the deadline on its own, a hung task stopping within one chunk, STOP during and before a command, busy, the 30 s cap, PULSES timing and its deadline |
| `test_mcp2515_reset.c` | only RESET and READ go out, RESET first, 10 ms before the reads, the board's SPI port and pins, a chip out of configuration mode reported as it is |
| `test_main.c` | the app: start-up order, every line a whole `JAMMER` line, the banner's five lines (the name and NEVER ON THE TRUCK first), line handling, the console's cut and line-end handling |
| `check.h` | the CHECK macro |
| `fake/` | the fakes; see `fake/README.md` |
| `Makefile` | builds into `build/` (ignored); `PARSE=`, `ENGINE=`, `MAIN=`, `MCP=`, `CONSOLE=` and `OUT=` for the mutants |
| `mutate.py` | breaks one behaviour at a time (35 mutants over the five sources) and checks the tests fail on each, after checking they pass on the originals; copies go in `<worktree>/scratch/x2-jammer-mutants/<pid>/`, removed at the end |
