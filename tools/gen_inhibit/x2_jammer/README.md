# x2_jammer/ -- CAN error-frame jammer for the ESP32-CAN-X2

**TEST EQUIPMENT. NOT FOR VEHICLE.** Never flash this to anything that goes on
the truck. Its env name, image name, boot banner and every console line say so.

Gen-inhibit tester, 2026-10-08. The reviewer asked for it, the user chose
the board, and it is owned by the gen-inhibit tester (harness, not product
code). It puts short dominant pulses on CAN1 of the charge interposer's
Autosport Labs ESP32-CAN-X2, so the bench can make error frames on demand:

1. spec 11, the bus-off half: a pulse train that makes every WiCAN
   transmit fail, driving its TEC to bus-off;
2. arm (ii) of the armed bus-off run, the same thing on a live INHIBIT arm;
3. the 12.4 error-frame row "at the trip threshold": single pulses, so a
   controlled ~10 in 10 s, and 9 in 10 s as the control.

It never sends a CAN frame. The TWAI controller is never started. At boot the
MCP2515 (CAN2) is sent the SPI RESET instruction, which puts it in
configuration mode, where it neither transmits nor ACKs. Its CANSTAT is read
back and printed in the banner.

## Files

| File | What |
|---|---|
| `platformio.ini` | One env, `vtrux_jammer_x2_NOT_FOR_VEHICLE`. Same toolchain and board settings as the interposer's: its pinned pioarduino espressif32 54.03.21-2, by release URL (pinned here too since 2026-10-24; before, a bare `espressif32`), `esp32-s3-devkitc-1`, USB-Serial-JTAG as `Serial`. Changing this file changes the source digest `jammer_name.py` takes, so the next build gets a new name; images already built keep theirs. `bench/test_x2_jammer.py` checks the pin. `build_dir` is under `%LOCALAPPDATA%`, so objects do not sync. |
| `src/main.cpp` | The firmware. The header comment has the safety design and the command set. |
| `jammer_name.py` | Pre-build script. Names the image `vtrux_jammer_x2_NOT_FOR_VEHICLE_<digest12>` after a sha256 of `platformio.ini`, both scripts and `src/`, and compiles the full digest into the banner. This folder is not in git, so the digest stands in for a commit. It identifies the **sources**, not the image. |
| `jammer_archive.py` | Post-build script. Copies the app `.bin`/`.elf`, bootloader, partition table and `boot_app0` into `builds/<app md5>/`, and **appends** a row to `builds/manifest.json` with the md5, the app sha256, the ELF sha256 and the flash offsets. Builds are not reproducible, so rows are keyed by md5 and never replaced. |
| `builds/` (in SeaDrive) | Archived images and `manifest.json`. Flash only from here, by md5, via `../x2_flash_custody.py flash-jammer`. |

Host side, in `../bench/`: `x2_jammer.py` (control, witness, judges and bench
subcommands), `x2_flash_custody.py` (dump, flash the jammer, restore) and their
tests `test_x2_jammer.py` and `test_x2_flash_custody.py`.

Build from PowerShell, with `REVERSE_IT_DIR` set (the build archives the image into
SeaDrive, `projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/`; the sources
are here, in the firmware repo, since 2026-10-22): `py -3.14 -m platformio run -d tools/gen_inhibit/x2_jammer`

## Pins and port

From `projects/vtrux/notes/interposer-firmware-bringup.md` ("The board"):
- CAN1 = built-in TWAI: RX GPIO6, TX **GPIO7**, into an MCP2562 transceiver.
- CAN2 = MCP2515 over SPI (CS 10, SCK 12, MISO 13, MOSI 11, IRQ 3).

**Only CAN1 can be jammed.** GPIO7 is the transceiver's TXD. CAN2's transmit
line runs from the MCP2515 to its transceiver, and no ESP pin is on it.
GPIO7 is not an S3 strapping pin. Whether CAN1 is on the WiCAN's segment is
shown on the bench (bench step 6), not assumed.

## Safety design

- **Boots silent.** `setup()` drives GPIO7 high (recessive) before anything
  else. Before that, through the ROM and the bootloader, GPIO7 is an input, and
  the MCP2562's internal TXD pull-up holds the bus recessive. Shown on the
  bench by the `boot-silence` check.
- **Pulses only.** W is limited to 12-50 us; 12 us is 6 bit times at
  500 kbit/s, so any pulse forces a stuff or bit error. The period must be at
  least 4 W. The RMT channel output is inverted, so its idle and
  end-of-transmission level (internal 0) is recessive on the pin.
  - The MCP2561/2FD datasheet (DS20005284) gives a typical TXD
    permanent-dominant timeout of 1.25 ms. The X2 carries the non-FD
    MCP2562-E, and its own figure has not been checked.
  - 50 us is 1/25 of 1.25 ms. A held dominant line would also stop nodes
    transmitting rather than make their transmits fail, which is not what
    any use here wants.
- **Every command carries a duration, capped at 30 s.** Nothing extends a
  deadline and there is no keepalive, so a host that disappears cannot keep it
  jamming. A command without a duration is refused.
- **Two independent stops at the deadline:**
  - (a) a one-shot `esp_timer` disables the RMT channel;
  - (b) the RMT only ever runs finite chunks of at most 200 ms, each sized not
    to cross the deadline, and the task re-arms one only while
    now < deadline. If the task hangs, pulses stop within one chunk.
- Closing COM3 with pyserial's default open resets the S3, and it reboots
  silent. `x2_jammer.py` opens with DTR/RTS pre-set low so that open and close
  do not reset it.

## Commands (COM3, 115200, one per line)

| Command | Reply |
|---|---|
| `TRAIN <w_us> <p_us> <ms>` | `JAMMER ACK TRAIN ...`, then `JAMMER DONE TRAIN end=... stop_by=... pulses=... over_deadline_us=...`. `JAMMER CAPPED` first if ms > 30000. |
| `PULSES <w_us> <n> <interval_ms>` | `ACK`, one `JAMMER PULSE k=.. t_us=..` per pulse, then `DONE`. n x interval + 100 ms must be <= 30 s. |
| `STOP` | `JAMMER ACK STOP busy=..`, and the running command ends `stop_by=stop_cmd`. |
| `STATUS`, `ID` | A state line, or the banner. |
| Anything malformed | `JAMMER ERR <reason>`. Nothing is transmitted. |

`pulses=` in `DONE` counts pulses **queued**, not delivered. Delivery is
judged from the independent witness only.

## Custody of the X2

The X2 is the charge interposer's board. Its interposer image (`7f197bfd`)
cannot be rebuilt, so it goes back by writing back a full read-back of the
flash. The procedure, agreed with both reviewers and the interposer tester:
1. read `0x7F7` off the wire (`bench_identify_board.py`);
2. `x2_flash_custody.py dump`: two reads, matching sha256, and the dump's
   own partition table must show an app slot carrying `7f197bfd`;
3. `flash-jammer`;
4. the jammer runs;
5. `restore`: write-flash, then verify-flash;
6. a wire read again.

Every step is recorded in `firmware/bench-holder.md` (X2 section) and
messaged to the interposer tester.

**The dump is sensitive** (it holds NVS). It is never printed, hexdumped or
quoted; only its path, size and sha256 are recorded.

## Commissioning on the bench, 2026-10-08 (build `a89ece8c...`, source `bb20e4a37b64...`)

All results are judged from the Kvaser witness (normal mode), never from what was
commanded. Run files are `../runs/x2_jammer_*_20261008T0*.json`.

| Check | Result |
|---|---|
| Identity | Banner `bb20e4a37b64...`; MCP2515 CANSTAT 0x80, CANCTRL 0x87 (config mode) |
| Boot silence | 3 resets, 0 error frames from each reset to banner + 5 s |
| Port | 1 pulse gave 1 error frame at +1.6 ms. CAN1 is on the WiCAN's segment |
| Calibration | 10 at 1 s gave 10 error frames, 1 each; 9 at 1.1 s gave 9 x 1. WiCAN in mode 2: its `bus_error` +1 per pulse, REC +1 per pulse, TEC 0 |
| Stop at deadline | 2 s train (rejudged PASS), 30 s cap of a 60 s command (PASS), console unread during the train (PASS at p=5000 us; INCONCLUSIVE at p=200, witness blind). Every negative control FAILed |
| No duration | Refused (`ERR`), 0 error frames |
| CAN2 isolation | The Innomaker on CAN2 saw 0 frames and 0 error frames (`..._012912`); the first run saw one stray `0x051` of unknown source (`..._012835`) |

**Witness limit:** the Kvaser counts 1:1 at 200 pulses/s. At ~5,000/s
(p=200 us) it reports in bursts of 430 with gaps of up to 1.5 s, so it cannot
count a dense train. The stop judges therefore need the last error frame within
0.5 s of the deadline.

**Not shown:** that layer (a), the `esp_timer`, stops a train on its own. Every
train ended through layer (b) (`end=deadline_layer_b`, `stop_by=none`).

## Builds

See `builds/manifest.json`. Which build was flashed, and when, is recorded in
the custody run files (`../runs/x2_flash_custody_*.json`) and in
`firmware/bench-holder.md`, not here.
