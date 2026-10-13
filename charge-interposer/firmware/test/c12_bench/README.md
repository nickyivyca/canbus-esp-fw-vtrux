# c12_bench -- the MCP2515 conformance sequence on the X2's real chip

Tester-owned harness (reviewer, 2026-10-13). A minimal standalone
**ESP-IDF** image for the ESP32-CAN-X2 (ESP32-S3): no Arduino, no
PlatformIO, no product code. It implements `mcpconf::Target`
(`../host_bridge/conformance.h`) over `spi_master` and nothing else, and runs
the host suite's `conformance.cpp` unchanged, so its transcript diffs line
for line against the fake's (`mcp2515_conformance_fake.txt` in SeaDrive
`projects/vtrux/notes/artifacts/interposer-firmware/`). It judges the chip
against its datasheet, not our driver, so the same image serves before and
after the ESP-IDF port.

| File | What |
|---|---|
| `main/c12_bench.cpp` | `BenchTarget` (SPI2 on CS 10 / SCK 12 / MISO 13 / MOSI 11, CS as a GPIO; esp_timer clock) and the console protocol: `HELLO?`, `RUN <C1..C12\|all>`, and the board's `@REQ` lines that the host answers |
| `main/CMakeLists.txt` | the component: `c12_bench.cpp` plus `../../host_bridge/conformance.cpp` and `provenance.cpp`. host_bridge is deliberately NOT an include directory (its fake `Arduino.h`, `SPI.h`, `driver/twai.h` must not shadow IDF's) |
| `CMakeLists.txt`, `sdkconfig.defaults` | the project: esp32s3, 8 MB flash, console on USB Serial/JTAG, 1 kHz tick, logs at WARN, task watchdog off |
| `.gitignore` | `build/`, `sdkconfig` |

The host side is `bench_c12.py` in that SeaDrive folder: it answers the
board's requests through the Innomaker (inject a frame and wait for its
echo; the data[0] tags received since a CLEAR; listen-only for C9) and
writes the record -- image identity, requests, transcript, diff against the
fake. Its `--self-test` needs no hardware.

**Single-adapter, not clean** (user via the reviewer, 2026-10-13): the
Innomaker sends and is the only witness; the verdict is the chip's SPI
registers. The two-adapter C12 stays open.

## Build (NICKY-XPS, PowerShell -- see `tools-reference.md`)

```powershell
$env:IDF_PATH = "C:\Users\Nicky\esp\esp-idf"
$env:PATH = "C:\Users\Nicky\AppData\Local\Programs\Python\Python311-32;" + $env:PATH
. "$env:IDF_PATH\export.ps1"
idf.py set-target esp32s3    # once
idf.py build
```

Every run record carries this folder's source sha (`git log -1 -- .`, plus
`../host_bridge/conformance.cpp`) and the ELF sha256, which the image prints
in `@HELLO` from its app descriptor and `bench_c12.py --elf` checks against
`build/c12_bench.elf`. No manifest row (reviewer, 2026-10-13).

**Flashing it replaces the X2's interposer image.** That is a bench action
(step 4, with the bench taken and the user's go): read 0x7F7 first, keep
the flight image's `.bin` to restore, and read 0x7F7 again after the restore.
