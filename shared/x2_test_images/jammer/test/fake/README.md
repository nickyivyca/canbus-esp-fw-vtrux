# jammer/test/fake/ -- TEST EQUIPMENT, NOT FOR VEHICLE

Host fakes of what the jammer calls in ESP-IDF, FreeRTOS and the board
layer, so its sources compile unchanged for the host tests. The model (one
emulated clock, events for an RMT batch's end, the timer and scheduled
lines, the wire as pulses) is described at the top of `fake_idf.h`.

| File | What |
|---|---|
| `fake_idf.h`, `fake_idf.c` | every declaration and fake, and the test's side: the call log, the wire, injected faults (a broken timer, a late task), the SPI log and the MCP2515's model, the USB output; `esp_app_get_elf_sha256()` as ESP-IDF v5.4.2's, the hash text cut to `CONFIG_APP_RETRIEVE_LEN_ELF_SHA` characters (from `../sdkconfig`, through the Makefile) |
| `board.h`, `esp_*.h`, `driver/`, `freertos/` | one-line headers under the real names, each including `fake_idf.h` |
| `driver/README.md`, `freertos/README.md` | those folders' descriptions |

A run that passes ten emulated minutes is stopped as a runaway (exit 3):
no command runs that long.
