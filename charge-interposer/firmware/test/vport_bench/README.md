# vport_bench -- the X2's vehicle port on its own

Tester-owned harness (reviewer, 2026-10-13). A minimal standalone
**ESP-IDF** image for the ESP32-CAN-X2 (ESP32-S3), for spec 9's "What the
board can put on the vehicle segment by itself". No Arduino, no PlatformIO,
**no product code**: it drives the S3's built-in TWAI controller (the
vehicle port) and nothing else, and the MCP2515 is never started. It judges
the controller and the transceiver, not our driver, so the same image serves
before and after the ESP-IDF port. **It never goes on the truck.**

| File | What |
|---|---|
| `main/vport_bench.cpp` | the console protocol (one command per line, one `@` reply per line, each ending `mode=<normal\|listen> bitrate=<...>`): `HELLO?`, `MODE normal\|listen`, `BITRATE 125k\|250k\|500k\|1M`, `SEND <hexid> [n]`, `FLOOD <hexid>` / `STOP`, `STALL <n>`, `DOMINANT <us> [reps]`, `STATUS`, `RESET`, `AUTORECOVER on\|off`. Its header describes each |
| `main/CMakeLists.txt` | the component: `vport_bench.cpp` only |
| `CMakeLists.txt`, `sdkconfig.defaults` | the project: esp32s3, 8 MB flash, console on USB Serial/JTAG, 1 kHz tick, logs at WARN, task watchdog off (STALL blocks a task forever on purpose) |
| `.gitignore` | `build/`, `sdkconfig` |

**Pins:** TWAI TX GPIO7, RX GPIO6, MCP2562 transceiver whose STBY pin has no
GPIO. From the bring-up notes' "The board" section, not from product source.

**Power-up state is LISTEN-ONLY at 500 kbit/s.** The image puts nothing on
the segment until the host sends `MODE normal`. In listen-only the
controller sends no frame, ACK or error flag, and the driver refuses
`twai_transmit` (`ESP_ERR_NOT_SUPPORTED`). SEND, FLOOD and STALL then report
that refusal in their reply.

What each command is for (spec 9's list):
- wrong bit rate: `BITRATE 250k` or `1M` against the 500 kbit/s bus, in
  normal mode, with and without a `FLOOD`. `AUTORECOVER on` (the default)
  takes bus-off back to running by itself, as the product does (B-7c), so
  the bursts can repeat;
- sending as fast as it can: `FLOOD 7F4` (diagnostic) and `FLOOD 649` (a
  vehicle identifier);
- a stalled task: `STALL n` queues n frames on 0x7F0, and then the task
  blocks forever while the controller keeps running;
- the MCP2562's permanent-dominant timeout, timed by the board itself (spec
  9 at 731e9c15, user 2026-10-13; no scope, no bench changes): `DOMINANT
  <us> [reps]` stops the driver, detaches TWAI TX in the GPIO matrix, holds
  TXD low for at most `<us>` and polls RXD (GPIO6) in a critical section
  with esp_timer: RXD going low, then high while TXD is still low, is the
  transceiver releasing the bus (`by=transceiver`, the interval reported).
  One `@DOM` line per pulse and an `@DOMINANT` summary (min / median /
  max). The can-fail is a pulse shorter than the timeout (500 us), which
  must report RXD following TXD back high after the release (`by=txd`);
- listen-only under each of the above: `MODE listen`, then the same
  commands.

The host side is `bench_vport.py` in SeaDrive
`projects/vtrux/notes/artifacts/interposer-firmware/`. It drives this image,
the PCAN (another node) and the Kvaser (listen-only witness, hardware
timestamps), and writes the record: image identity, adapters, pins, and each
measurement. Its `--self-test` needs no hardware.

## Build (PowerShell; this machine's ESP-IDF and Python paths are in `tools-reference.md`)

```powershell
$env:IDF_PATH = "<ESP-IDF checkout>"
$env:PATH = "<Python 3.11 folder>;" + $env:PATH
. "$env:IDF_PATH\export.ps1"
idf.py set-target esp32s3    # once
idf.py build
```

Built at ESP-IDF v5.4.1, the same as c12_bench. Every run record carries
this folder's source sha (`git log -1 -- .`) and the ELF sha256, which the
image prints in `@HELLO` from its app descriptor and `bench_vport.py --elf`
checks against `build/vport_bench.elf`. No manifest row (as c12_bench).

**Flashing it replaces the X2's interposer image.** That is a bench action
(step 4, with the bench taken and the user's go), under the same custody as
c12_bench: read 0x7F7 first, dump the flash, flash, restore with verify,
and read 0x7F7 again after the restore.
