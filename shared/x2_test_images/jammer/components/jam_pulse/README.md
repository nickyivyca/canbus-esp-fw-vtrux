# jam_pulse/ -- the jammer's pulse engine. TEST EQUIPMENT, NOT FOR VEHICLE

Puts short dominant pulses on a CAN transceiver's TXD from the RMT, on
command, within the jammer's limits and with its two stops at the deadline
(x2_test_images SPEC T8). The interface, the safety design and the command
set are in `include/jam_pulse.h`.

Transport-free: commands come in as text lines (`jam_line`), and replies go
out through the caller's callback, one line each, without a prefix or a
line end. It links no TWAI or SPI driver. So `slcan_spi` can take it
unchanged (SPEC T13), with
`set(EXTRA_COMPONENT_DIRS ../jammer/components/jam_pulse)` in its project.
The caller drives the pin recessive first (`jam_drive_recessive`) and then
calls `jam_init`.

Two test-only builds, by compile definition (the jammer project sets them
from its `JAMMER_TEST_STOP` option): `JAM_TEST_STOP_A_ONLY` moves stop (b)
to the deadline plus 2 s; `JAM_TEST_STOP_B_ONLY` has no deadline timer.
Neither is ever pinned (`../../x2_image.json`).

| File | What |
|---|---|
| `CMakeLists.txt` | the component |
| `include/jam_pulse.h` | the interface, the limits and the safety design |
| `jam_internal.h` | the internals shared by the two files and the host tests |
| `jam_parse.c` | the parser and limits, pure (no hardware) |
| `jam_engine.c` | the RMT, the deadline timer, the jam task and both stops |
