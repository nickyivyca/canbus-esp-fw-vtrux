# slcan_spi/ -- NOT FOR VEHICLE

SLCAN over USB to the X2's CAN2, the MCP2515 on SPI, to
`../SPEC.md` section 5 (T12 to T15). Test equipment, the charge-interposer
tester's (SPEC T4). NOT FOR VEHICLE (T14): it is never put on the truck,
which has `slcan_can1` for that (T3a).

Written for the Android session's two-bus replay into its app emulator: on
the bench the charger segment holds only `canchg` and the X2's MCP2515, so
the X2 is its only possible sender there (SPEC section 5).

**No jamming in this build.** T13's jamming on CAN1 comes once `jammer/`
holds the jammer's ESP-IDF port (T8); until then the image ships without it,
so the replay is not held up. CAN1's TWAI driver is not linked in, so its
controller is never started and nothing is sent on CAN1.

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
| `t` `T` `r` `R` | queues a frame (reply `z` / `Z`); refused with BEL when closed |
| `F` | the Lawicel status flags, read and cleared |
| `V` / `N` | the identity: `V0100 <identity>` / `N<identity>` |
| `B` | the banner again |

| The image sends | Meaning | The kernel |
|---|---|---|
| `t` `T` `r` `R` lines | a frame received on CAN2 | a received frame |
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

## Console lines

The image's console lines are its banner, at boot and on `B`, each
`\n# NOT FOR VEHICLE | ...\r` (T14): the identity, the protocol in one line,
CANSTAT at boot (0x80, configuration mode), CAN1's state, and the counters.
The kernel drops them unread. ESP-IDF's console is off and its log level is
none, so nothing else from the image reaches the port.

**Gap: the ROM's boot lines.** The ESP32-S3's ROM prints its own boot lines
on the USB port before any image runs; they are the ROM's, not this image's,
and carry no NOT FOR VEHICLE. Turning them off is an eFuse burn, which no
test image does.

## Files

| File | What |
|---|---|
| `CMakeLists.txt` | the ESP-IDF project; its name, `slcan_spi_NOT_FOR_VEHICLE`, is the image's name |
| `sdkconfig.defaults`, `sdkconfig` | the options, and the full configuration generated from them (regenerate with `idf.py -B build/dev reconfigure` after deleting `sdkconfig`) |
| `dependencies.lock` | the Component Manager's lock: the board at its tag |
| `vtrux_build.json` | the shared tooling's project data (shared/tools R22): the `dev` variant |
| `main/` | the image's source; see `main/README.md` |
| `test/` | the protocol layer's host unit tests and their mutants; see `test/README.md` |
| `bench/` | the checks of a built image and of the image on the bench, each with its can-fail; see `bench/README.md` |

## Building

Through the shared tooling (SPEC T6), from a shell exported for ESP-IDF
v5.4.2 (`local-paths.md`), in `build.slice` on `madhouse-debian`:

    PYTHONPATH=<worktrees>/shared-<tag>/shared/tools \
      systemd-run --user --scope --slice=build.slice \
      python3.13 -B -m vtrux_build build dev

The image is `build/dev/slcan_spi_NOT_FOR_VEHICLE.bin`. Such a build is a
dev build (T7); only a build from a shared tag's read-only checkout with a
clean tree is pinned, with its manifest row (T6, T9).
