# jammer/ -- CAN1 error-frame jammer for the ESP32-CAN-X2

**TEST EQUIPMENT. NOT FOR VEHICLE.** Never flash it to anything that goes
on the truck. Its image name and boot banner say so (x2_test_images SPEC
T8, as the user ruled on 2026-11-12).

The gen-inhibit tester's (SPEC T4). It puts short dominant pulses on CAN1
of the Autosport Labs ESP32-CAN-X2, so the bench can make error frames on
demand: gen-inhibit's bus-off runs (spec 11, the armed bus-off run) and the
12.4 error-frame row at the trip threshold.

It never sends a CAN frame. CAN1's TWAI driver is not in the image, so the
controller is never started (`../tools/check_image.py` checks the ELF for
it). At boot the MCP2515 (CAN2) is sent the SPI RESET instruction, which
puts it in configuration mode, where it neither transmits nor ACKs; its
CANSTAT is read back and printed in the banner.

This is the ESP-IDF v5.4.2 port (SPEC T8) of the PlatformIO jammer in
`gen-inhibit/tools/x2_jammer/` (build `a89ece8c`, commissioned on
2026-10-08). A port is new code, so the old image's bench passes do not
carry to it; gen-inhibit keeps pinning `a89ece8c` until this image has
passed the bench checks below (SPEC T8).

## Variants

Build variants are CMake options (shared/tools R20), listed in
`vtrux_build.json`. Each has its own project name, which is the image's
name, its `.bin`'s name and its app descriptor's `project_name`.
`../tools/` judges the variant from that name, never from what a caller
says (gen-inhibit reviewer, 2026-11-12). ESP-IDF cuts `project_name` at 31
characters without an error, so `CMakeLists.txt` stops a configure whose
name is longer.

| Variant | `JAMMER_TEST_STOP` | Project name | Pinnable |
|---|---|---|---|
| `jammer` | `none` | `vtrux_jammer_x2_NOT_FOR_VEHICLE` | yes, from a tag |
| `stop_a_only` | `a_only` | `jammer_stop_a_NOT_FOR_VEHICLE` | never |
| `stop_b_only` | `b_only` | `jammer_stop_b_NOT_FOR_VEHICLE` | never |

The two test variants each show one stop alone (below): `stop_a_only` moves
stop (b) to the deadline plus 2 s, so only stop (a) can end a train at the
deadline; `stop_b_only` has no deadline timer.

## Safety design

- **Boots silent.** `app_main` drives GPIO7 (the board layer's CAN1 TX,
  `board_twai_pins(0)`) high, recessive, before anything else. Before that,
  through the ROM and the bootloader, GPIO7 is an input, and the MCP2562's
  internal TXD pull-up holds the bus recessive.
- **Pulses only.** W is 12-50 us; 12 us is 6 bit times at 500 kbit/s, so
  any pulse forces a stuff or bit error. The period is at least 4 W. The
  RMT output is inverted, so its idle and end-of-transmission level
  (internal 0) is recessive on the pin. The MCP2561/2FD datasheet
  (DS20005284) gives a typical TXD permanent-dominant timeout of 1.25 ms;
  the X2 carries the non-FD MCP2562-E, whose own figure has not been
  checked. 50 us is 1/25 of 1.25 ms.
- **Every command carries a duration, capped at 30 s.** Nothing extends a
  deadline and there is no keepalive. A command without a duration is
  refused.
- **Two independent stops at the deadline:**
  - (a) a one-shot `esp_timer` disables the RMT channel;
  - (b) the RMT only ever runs finite chunks of at most 200 ms, each sized
    not to cross the deadline, and the task re-arms one only while
    now < deadline; PULSES re-arms a pulse only while it ends before the
    deadline. If the task hangs, pulses stop within one chunk.

### Changes from the PlatformIO jammer

- Replies go through `jam_pulse`'s callback, and `main/` prints them with
  the same `JAMMER ` prefix, so `gen-inhibit/tools/bench/x2_jammer.py`
  reads them unchanged.
- The banner names the build (`build version=<git describe> elf=<ELF
  SHA-256> idf=<IDF>`, SPEC T10) where the source digest was.
- A STOP that arrives between a command's acceptance and its start is
  kept (before, the task cleared it).
- PULSES has stop (b) too (before, only its pulse count bounded it).
- A number is decimal digits only: `-5` ends the arguments as a letter
  does (sscanf took it as 4294967291; refused either way).

## Commands (the X2's USB Serial/JTAG port, one per line)

Lines end at LF; CR is dropped; a line over 63 characters is cut.

| Command | Reply |
|---|---|
| `TRAIN <w_us> <p_us> <ms>` | `JAMMER ACK TRAIN w_us= p_us= ms= t0_us=`, then `JAMMER DONE TRAIN end= stop_by= pulses= chunks= elapsed_us= over_deadline_us=`. `JAMMER CAPPED ms <n> -> 30000` first if ms > 30000. |
| `PULSES <w_us> <n> <interval_ms>` | `ACK PULSES ... deadline_ms=`, one `JAMMER PULSE k= t_us= rel_us=` per pulse, then `DONE`. n x interval + 100 ms must be <= 30 s. |
| `STOP` | `JAMMER ACK STOP busy=`, and the running command ends `stop_by=stop_cmd`. |
| `STATUS` | `JAMMER STATUS idle\|jamming uptime_us=` |
| `ID` | the banner |
| anything malformed | `JAMMER ERR <reason>`; nothing is transmitted |

`end=` is `complete`, `stopped`, `deadline_layer_b` (stop (b)),
`backstop_layer_b` (stop (b) at deadline + 2 s, `stop_a_only` only),
`tx_refused` or `chunk_timeout`. `stop_by=` is `deadline_timer` (stop (a)),
`stop_cmd` or `none`. `pulses=` counts pulses **queued**, not delivered:
delivery is judged from the independent witness only.

The banner, at boot after 300 ms and on `ID`, five lines in the PlatformIO
jammer's order:

    JAMMER <project name> -- TEST EQUIPMENT, NEVER ON THE TRUCK
    JAMMER build version=<git describe> elf=<64 hex> idf=<IDF version>
    JAMMER tx=GPIO7(CAN1/TWAI) twai=never_started stops=<a+b|a_only(...)|b_only> reset_reason=<n> uptime_us=<n>
    JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes
    JAMMER limits w_us=12..50 p_us>=4w..20000 ms<=30000 n<=1000 interval_ms=1..10000

ESP-IDF's console is off and its log level is none, so nothing else from
the image reaches the port. The ESP32-S3 ROM prints its own boot lines on
the port before any image runs.

## Files

| File | What |
|---|---|
| `CMakeLists.txt` | the ESP-IDF project; the variant option and its project name |
| `vtrux_build.json` | the shared tooling's project data (shared/tools R22, R31): the three variants |
| `x2_image.json` | what `../tools/` checks in a build of this image: each variant's project name and whether it may be pinned, the banner strings, the forbidden TWAI symbols, the required symbols and config values |
| `sdkconfig.defaults`, `sdkconfig` | the options, and the full configuration generated from them (regenerate with `idf.py -B build/jammer reconfigure` after deleting `sdkconfig`) |
| `dependencies.lock` | the Component Manager's lock: the board at its tag |
| `main/` | the app: start-up, console, MCP2515 reset; see `main/README.md` |
| `components/` | `jam_pulse`, the pulse engine; see `components/README.md` |
| `test/` | host tests on fakes and their mutants; see `test/README.md` |
| `VERIFICATION.md` | the bench and off-board test results for each build, against SPEC T8 and the bench checks below |

## Building

Through the shared tooling (SPEC T6), from a shell exported for ESP-IDF
v5.4.2 (`local-paths.md`), in `build.slice` on `madhouse-debian`.

A dev build, for the builder's own bench work (SPEC T7):

    PYTHONPATH=<worktrees>/shared-<tag>/shared/tools \
      systemd-run --user --scope --slice=build.slice \
      python3.13 -B -m vtrux_build build <variant>

The image is `build/<variant>/<project name>.bin`.

A build to archive, and to pin when it is from a tag:
`../tools/archive.py build . <variant> --builder "<role>"` (see
`../tools/README.md`). It is pinnable only from a worktree of its own,
detached at a shared tag, with a clean tree.

## Host tests

`make -C test` and `python3.13 -B test/mutate.py`: see `test/README.md`.
On the fakes they show each stop ending a train at the deadline on its own
(the timer on `stop_a_only` and with a late task, the chunk sizing with a
broken timer and on `stop_b_only`), the pin recessive after every command,
the MCP2515 sent only RESET and READ, and the banner.

## Bench checks the port must pass (SPEC T8)

Run by the gen-inhibit tester under the bench lock, judged from the
hardware-timestamped witness, never from what was commanded:

- the commissioning set the PlatformIO jammer passed on 2026-10-08:
  identity, boot silence (3 resets), port, calibration (10 at 1 s, 9 at
  1.1 s), stop at the deadline, no duration refused, CAN2 isolation;
- stop (a) alone on `stop_a_only`: `stop_by=deadline_timer` and the last
  witness error frame within 0.5 s of the deadline; can-fail: the same
  record rejudged against a shifted deadline FAILs;
- stop (b) alone on `stop_b_only`;
- then gen-inhibit's B5-B8 on the ported image.

Custody of the X2 (dump, flash, restore) is
`gen-inhibit/tools/bench/x2_flash_custody.py` under `shared/bench/SPEC.md`
section 8.

Results are in `VERIFICATION.md`. On the PlatformIO jammer every train
ended through stop (b) (`end=deadline_layer_b`, `stop_by=none`), so stop (a)
on its own was never shown there. On the port's first builds (`9e017baa`)
the `stop_a_only` check passed on the bench on 2026-11-13. B5-B8 have not
been run yet.
