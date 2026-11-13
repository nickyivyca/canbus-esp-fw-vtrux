# slcan_spi/ -- NOT FOR VEHICLE

SLCAN over USB to the X2's CAN2, the MCP2515 on SPI, to
`../SPEC.md` section 5 (T12 to T15). Test equipment, the charge-interposer
tester's (SPEC T4). NOT FOR VEHICLE (T14): it is never put on the truck,
which has `slcan_can1` for that (T3a).

Written for the Android session's two-bus replay into its app emulator: on
the bench the charger segment holds only `canchg` and the X2's MCP2515, so
the X2 is its only possible sender there (SPEC section 5).

**Jamming on CAN1** (T13, the image's primary objective): the jammer's
pulse engine, `../jammer/components/jam_pulse`, taken unchanged, puts short
dominant pulses on CAN1's TXD on command, within the jammer's limits and
with its two stops at the deadline. CAN1's TWAI driver is not linked in, so
its controller is never started and no CAN frame is sent on CAN1. Jam
commands and replies go through the SLCAN as reserved frames ("Jamming"
below), and jamming may run while the SLCAN is open.

## Using it

The image enumerates as the X2's USB Serial/JTAG port, the same port that
flashes it. On `madhouse-debian` that port is `canctl`'s `canx2` role:

    sudo -n /usr/local/sbin/canctl slcan attach canx2
    sudo -n /usr/local/sbin/canctl up canx2 500000              # O: normal, ACKs
    sudo -n /usr/local/sbin/canctl up canx2 500000 listen-only  # L: never ACKs
    sudo -n /usr/local/sbin/canctl down canx2                   # C: silent again
    sudo -n /usr/local/sbin/canctl slcan detach canx2

Linux 6.12's slcan driver sends `C`, `S6`, then `O` or `L` at `up`, and `C`
at `down`. Any bitrate but 500000 is refused by the image, and so is the
open after it, so the image stays silent rather than open at the wrong rate.

**Close it before the X2 is reset or flashed** (`canctl down canx2`). The
board layer lists no MCP2515 reset line, and whether a hardware reset of
the ESP32-S3, such as esptool's, also resets the MCP2515 has not been
checked. If it does not, an open MCP2515 keeps its mode through the S3's
reset, ACKing and retrying a frame it was sending, until an image resets
it: this one at boot, or the next image flashed. A software restart of
this image does not happen: nothing in it calls one.

## The protocol

Lawicel SLCAN as `main/slcan_proto.h` sets out, in the forms Linux 6.12's
`drivers/net/can/slcan/slcan-core.c` parses (SPEC T12, settled from that
source):

| Host sends | The image |
|---|---|
| `S6` | 500 kbit/s; any other `Sn`, and `s`, is refused (BEL) and clears the rate |
| `O` / `L` | opens normal (transmits, ACKs) / listen-only (never ACKs); refused unless closed and after `S6` |
| `C` | closes: the MCP2515 is reset into configuration mode, where it neither transmits nor ACKs |
| `t` `T` `r` `R` | queues a frame (reply `z` / `Z`); refused with BEL when closed. A frame on a jam identifier is the image's own, open or closed, and never sent ("Jamming") |
| `F` | the Lawicel status flags, read and cleared |
| `V` / `N` | the identity: `V0100 <identity>` / `N<identity>` |
| `B` | the banner again |

| The image sends | Meaning | The kernel |
|---|---|---|
| `t` `T` `r` `R` lines | a frame received on CAN2, or a jam reply frame (`T1F4A4D52`, "Jamming") | a received frame |
| `e3OOO` | one frame the host asked to send was dropped: the 512-frame transmit queue was full, or the image is open listen-only | an error frame with `CAN_ERR_CRTL_TX_OVERFLOW`; `tx_errors` + 1 |
| `e3ooo` | a receive overrun: the MCP2515's, or a frame the USB link had no room for | an error frame with `CAN_ERR_CRTL_RX_OVERFLOW`; `rx_errors` and `rx_over_errors` + 1 |
| `s<a\|w\|p\|b><rec><tec>` | the bus state changed: error-active, warning, error-passive, bus-off, with the receive and transmit error counters | a state change; bus-off takes the interface off the bus |

The kernel parses a line only when more than four characters come before
its CR, so each error line repeats its one code three times; it counts once
per line, so the image sends one line per dropped frame or overrun, and the
host's counters stay exact.

The identity is `slcan_spi_NOT_FOR_VEHICLE <version> elf=<64 hex>`: the
image's name, ESP-IDF's version string for the build (`git describe` of the
tree it was built from) and the ELF's SHA-256, the value a manifest row
records (T6), so it is T10's identity answer without a flash read.

Frames go out in the order the host sent them, one at a time through the
MCP2515's transmit buffer 0. Received frames reach the host in bus order.

## Jamming

T13 wants jam commands on a channel of their own, never inside traffic a
replay could carry, first choice a second USB serial port. The X2 cannot
present one without harm to flashing: the ESP32-S3 offers a second serial
port only through its USB OTG peripheral, which takes the USB pins from the
Serial/JTAG that flashes and resets it, so a crashed image could no longer be
re-flashed over USB. So the image uses T13's fallback: a reserved CAN frame
with a signature, sent by the host through the SLCAN, which the image acts
on and never puts on a bus. The replies come back as frames on a second
reserved identifier in the host's receive stream, never put on a bus (user,
2026-11-13). Both work through the kernel's slcan, with the interface up
in normal mode. Linux's slcan sends nothing the host writes while the
interface is listen-only (seen on the bench 2026-11-13: the command is
counted in `tx_dropped` and never reaches the image), so a replay that wants
jamming brings `canx2` up normal. On the raw tty the image takes a jam frame
open normal, open listen-only or closed alike.

    cansend canx2 1F4A4D43#4A4D541400641388     # TRAIN 20 100 5000
    cansend canx2 1F4A4D43#4A4D530000000000     # STOP

`main/jam_frame.h` has the format:

| Identifier (29-bit) | Direction | Data |
|---|---|---|
| `0x1F4A4D43` | host to image: a command | `J` `M`, then the command byte and its arguments, DLC 8: `T` TRAIN (byte 3 `w_us`, bytes 4-5 `p_us`, bytes 6-7 `ms`), `P` PULSES (byte 3 `w_us`, bytes 4-5 `n`, bytes 6-7 `interval_ms`), `S` STOP and `?` STATUS (bytes 3-7 zero); numbers big-endian |
| `0x1F4A4D52` | image to host: a reply | `J` `M`, then byte 2 the frame's index in the reply line (0 first, `0x80` set on the last), then up to 5 characters of the line; DLC 3 + their count |

- **The command** becomes the jammer's own text command (`TRAIN 20 100
  5000`), and `jam_pulse` decides what runs, by its limits, as in the jammer
  (`../jammer/README.md`, "Commands"): W 12-50 us, period at least 4 W, every
  command capped at 30 s, nothing extending a deadline, both stops.
- **The replies** are `jam_pulse`'s reply lines, `ACK`, `PULSE`, `DONE`,
  `ERR`, `CAPPED` and `STATUS`, as text, without the jammer's `JAMMER `
  prefix. The host joins each line's frames back by index. One line's frames
  go out together, never mixed with another line's; a line the link has no
  room for is lost whole and counted (`jam_replies_lost` in the banner).
- **A frame from the host on either identifier** is the image's own: it is
  never queued for CAN2, decoded or not. One that does not decode is
  answered `ERR bad_jam_frame`; the reply identifier from the host is
  always refused so.
- **The identifiers** have J1939's EDP and DP bits both set, which J1939
  leaves to ISO 15765-3, and no bench or truck bus uses them (no Coda or
  Vtrux DBC has one). A frame received from CAN2 on either is passed to the
  host like any other; it is never acted on.
- **Boots silent:** CAN1's TXD (the board layer's `board_twai_pins(0)`) is
  driven recessive before anything else in `app_main`. Before that, through
  the ROM and bootloader, the pin is an input and the transceiver's TXD
  pull-up holds the bus recessive (`../jammer/README.md`).

## Variants

Build variants are CMake options (shared/tools R20), listed in
`vtrux_build.json`. Each has its own project name, which is the image's
name, so `../tools/` judges the variant from the image itself.

| Variant | `SLCAN_SPI_TEST_STOP` | Project name | Pinnable |
|---|---|---|---|
| `dev` | `none` | `slcan_spi_NOT_FOR_VEHICLE` | yes, from a tag |
| `stop_a_only` | `a_only` | `slcan_spi_a_NOT_FOR_VEHICLE` | never |
| `stop_b_only` | `b_only` | `slcan_spi_b_NOT_FOR_VEHICLE` | never |

The two test variants are `jam_pulse`'s test builds, as in the jammer: each
shows one stop at the deadline alone. `stop_a_only` moves stop (b) to the
deadline plus 2 s, so only stop (a), the `esp_timer`, can end a train at the
deadline; `stop_b_only` has no deadline timer.

## Console lines

T14 puts NOT FOR VEHICLE in the image name and the boot banner (every
console line until 2026-11-12). SLCAN replies are plain protocol. The
image's console lines:

- **The boot banner,** one line at boot: `\n# NOT FOR VEHICLE slcan_spi\r`.
  It shares the one USB port with the SLCAN: the S3 presents a second
  serial port only through its USB OTG peripheral, which takes the USB pins
  from the Serial/JTAG that flashes it. Output written while no host holds
  the port does not reach the next opener (seen on the bench, 2026-11-12),
  so the line reaches only a host holding the port at boot, which may be
  the kernel's slcan, up. So it is made for the kernel's stream: it starts
  with LF, and Linux 6.12's slcan parses a line only by its first character
  (`t T r R e s`), dropping any other unread; and it is within the 33
  characters the kernel buffers per line (its `SLCAN_MTU`), past which it
  counts one `rx_over_errors`. The `attach` bench check attaches the kernel
  after a boot, and resets the X2 under it.
- **The full banner, on `B` only,** each line `\n# NOT FOR VEHICLE | ...\r`:
  the identity, the protocol in one line, CANSTAT at boot (0x80,
  configuration mode), CAN1's jam pin and stops, the jam limits and command
  identifier, and the counters. Its lines are longer
  than the kernel takes, and the kernel never sends `B`.

ESP-IDF's console is off and its log level is none, so nothing else from
the image reaches the port.

**Gap: the ROM's boot lines.** The ESP32-S3's ROM prints its own boot lines
on the USB port before any image runs; they are the ROM's, not this image's.
Turning them off is an eFuse burn, which no test image does. They reach a
host holding the port at boot, like the boot banner, and one of them
(`rst:... boot:...`) is longer than 33 characters, so the kernel's slcan,
attached and up through a reset, counts one `rx_over_errors` (`attach`).

## Files

| File | What |
|---|---|
| `CMakeLists.txt` | the ESP-IDF project: `jam_pulse` from `../jammer/components/`, the variant option and its project name, which is the image's name |
| `sdkconfig.defaults`, `sdkconfig` | the options, and the full configuration generated from them (regenerate with `idf.py -B build/dev reconfigure` after deleting `sdkconfig`) |
| `dependencies.lock` | the Component Manager's lock: the board at its tag |
| `vtrux_build.json` | the shared tooling's project data (shared/tools R22): the three variants |
| `VERIFICATION.md` | the test results, by build (the charge-interposer tester's) |
| `x2_image.json` | what `../tools/check_image.py` and `../tools/archive.py` check of a build (format in `../tools/README.md`): each variant's name and whether it may be pinned, the boot line and the CAN1 banner line, no `twai_` symbol (the TWAI driver; the board layer's pin lookup `board_twai_pins` is not one), the jam engine's symbols, the console and log off, the whole ELF hash in the identity |
| `main/` | the image's source; see `main/README.md` |
| `test/` | the protocol layer's host unit tests and their mutants; see `test/README.md` |
| `bench/` | the checks of the image on the bench, each with its can-fail; see `bench/README.md` |

## Building

Through the shared tooling (SPEC T6), from a shell exported for ESP-IDF
v5.4.2 (`local-paths.md`), in `build.slice` on `madhouse-debian`:

    PYTHONPATH=<worktrees>/shared-<tag>/shared/tools \
      systemd-run --user --scope --slice=build.slice \
      python3.13 -B -m vtrux_build build <variant>

The image is `build/<variant>/<project name>.bin`. Such a build is a
dev build (T7); only a build from a shared tag's read-only checkout with a
clean tree is pinned, with its manifest row (T6, T9).
