# slcan_can1/

A plain SLCAN transmitter on the X2's CAN1, the ESP32-S3's TWAI
(`../SPEC.md` T1). Test equipment, the charge-interposer tester's (T4). It
may go on the truck for testing during bring-up and installation, and is
never a flight image (T3a).

Moved on 2026-11-13 from the charge interposer's `slcan` variant,
`charge-interposer/firmware/main/slcan_main.cpp` at `charge-interposer/main`
`8090d938`, onto ESP-IDF v5.4.2 through the shared build tooling (T6),
without the Arduino core, with its behaviour kept (below, "Changes from
slcan_main.cpp"). The interposer's own `slcan` variant is left as it is;
dropping it is the interposer's change. A moved image is new code, so the
variant's results do not carry to this one (AGENTS.md "Spec, validation and
verification").

## Why it exists

The generator-inhibit bench needs a second transmitter on the powertrain
segment, because of CAN arbitration. Frames queued in one adapter do not
arbitrate against each other: they leave in queue order, so low-priority
filler queued ahead of a 0x051 delays that 0x051 whatever its priority. At
97.6 % carried load the PCAN accepted every frame and 384 of 8998 commands
never reached the wire, with no refusal reported (2026-09-29, from
`slcan_main.cpp`'s header). Filler from a separate controller arbitrates
properly: 0x7E0 loses to every truck identifier and to 0x051, so it fills
gaps and can never delay a command, while still beating the diag pages on
0x7F1-0x7F8. The X2 already sits on that segment, so it is the transmitter.
CAN2 and the MCP2515 are not touched.

## Silent until told to open (SPEC T8a)

Nothing is transmitted before an `O`; a `C` closes; a reset returns it to
silent; an `L` opens listen-only, which never ACKs and cannot send. At boot
CAN1's port is set up and registered with `can_port` but never brought up,
until an `O` or an `L`. Before the image runs, through the ROM and the
bootloader, CAN1's TXD is an input and the transceiver's TXD pull-up holds
the bus recessive (`../jammer/README.md`).

The host check is `test/test_app.c`: the whole image, `main/app_main.c` with
the protocol, on fakes of ESP-IDF and the shared ports, shows the port not
brought up at boot or by any idle pass, no frame offered before an open,
while listen-only, after a close or after a reset, `L` bringing the port up
listen-only and `O` normal, and `C` taking it down. `test/mutate.py` is its
can-fail (`test/README.md`). On those, `../README.md` marks this image
idle-safe.

## The protocol

The SLCAN subset python-can sends, and nothing more (`main/slcan1_proto.h`):

| Host sends | The image |
|---|---|
| `S6` | 500 kbit/s: CR. Any other rate: BEL. It does not gate `O` |
| `O` / `L` | brings the port up normal / listen-only: CR, or BEL if it would not come up. While open: CR, and nothing changes |
| `C` | takes the port down if open: CR |
| `tIIILDD..` | queues a standard-id data frame. No reply, queued or not: python-can never reads one. Refused with BEL while closed or listen-only, or malformed |
| `V` / `N` | `V0101` / `NF001` |
| `?` | the counters line (below). Not SLCAN |
| anything else, `T`, `r`, `R` and `F` included | BEL |

Lines end at CR or LF. A line longer than 63 characters is dropped whole,
answered BEL once, and the input resynchronises at the next CR or LF.

Four refusals are deliberate, each because silence would be worse: only
`S6` (a controller brought up at the wrong rate on a live 500 kbit/s bus
puts error frames on every message); only `t` (a filler needs no extended or
remote frame, and accepting them lets a typo put an unexpected frame on a
shared bus); received frames are not forwarded (the image is a transmitter,
and the bench's witness counts the wire better than a USB link would; they
are still drained, so the controller cannot overrun); no `F` (`?` reports
more).

`?` answers what SLCAN cannot, whether every frame sent went out, on one
line ending CR LF:

    slcan1 open=1 listen_only=0 accepted=2 queue_full=0 rejected=0 tx=2 tx_dropped=0 rx=0 rx_dropped=0 bus_errors=0 bus_off=0

`accepted`: `t` commands queued; `queue_full`: `t` commands the transmit
queue had no room for; `rejected`: commands answered BEL. From the port's
status (`can_port_status`): `tx` its queued sends, `tx_dropped` its refused
sends (queue full or otherwise), `rx` frames received, `rx_dropped` frames
lost on receive, `bus_errors`, and `bus_off` while inside a bus-off episode.
python-can leaves the serial port reachable as `bus.serialPortOrig`, which
is how a harness sends it.

**Identity (SPEC T10).** `V` and `N` are fixed and name the protocol, not
the build, as the variant's did, so a consumer confirms the image by
esptool's `verify_flash` against its pinned files (gen-inhibit's
`x2_pin.json` and its identify already do so for the variant).

## The port

The shared `vtrux/twai_port` behind `vtrux/can_port`, on the board's TWAI
port 0, at shared/main tag `2026.11.12.2`, with the interposer's
vehicle-port settings the variant ran with: 500 kbit/s, every identifier
accepted, retries (not single-shot), automatic bus-off recovery, remote
frames dropped, an install failure reported, never a restart. Queue depths:
transmit 64 (a filler's burst is not reported as a drop), receive 16.

## Changes from slcan_main.cpp

- **ESP-IDF without the Arduino core.** The USB Serial/JTAG driver directly,
  with the variant's buffers (receive 8192 bytes: with Arduino's default,
  of 12,930 frames written the board saw only 7,058, measured 2026-09-29;
  transmit 2048), and a write that waits at most one tick (1 ms) for the
  host, as the variant's 1 ms timeout did. The loop runs in a task on
  core 1, as Arduino's did, reading the USB with a one-tick wait.
- **The tail of an overlong line is dropped** (T8a). The variant dropped the
  characters kept so far, then took the rest of the line as a new line, so
  garbage ending in `O` opened the port, against its own comment
  ("resynchronise at the next terminator"). Here the whole line is dropped.
- **Nothing on the port but the replies.** ESP-IDF's console is off and the
  bootloader's and ESP-IDF's logs are at none; the image prints no banner.

## Gaps

- **Not yet run on the bench.** The T8a properties are shown on the host,
  on fakes of the port; the port itself is the shared `twai_port`, whose
  own tests cover it. A bench check of the image on CAN1, with the Kvaser
  as witness, is not part of the move.
- **The ROM's boot lines** reach a host holding the port at a reset, as for
  every X2 image (`../slcan_spi/README.md`, "Gap: the ROM's boot lines").

## Files

| File | What |
|---|---|
| `CMakeLists.txt` | the ESP-IDF project; its name, `slcan_can1`, is the image's name |
| `sdkconfig.defaults`, `sdkconfig` | the options, and the full configuration generated from them (regenerate with `idf.py -B build/dev reconfigure` after deleting `sdkconfig`) |
| `dependencies.lock` | the Component Manager's lock: the board, `can_port` and `twai_port` at their tag |
| `vtrux_build.json` | the shared tooling's project data (shared/tools R22): one variant, `dev` |
| `x2_image.json` | what `../tools/check_image.py` and `../tools/archive.py` check of a build (format in `../tools/README.md`): the name, pinnable; the `V`, `N` and `?` strings; the image's and the ports' symbols; the console and logs off |
| `main/` | the image's source; see `main/README.md` |
| `test/` | the host tests, T8a's among them, and their mutants; see `test/README.md` |

## Building

Through the shared tooling (SPEC T6), from a shell exported for ESP-IDF
v5.4.2 (`local-paths.md`), in `build.slice` on `madhouse-debian`, from this
folder:

    PYTHONPATH=<worktrees>/shared-<tag>/shared/tools \
      systemd-run --user --scope --slice=build.slice \
      python3.13 -B -m vtrux_build build dev

The image is `build/dev/slcan_can1.bin`. Such a build is a dev build (T7);
only a build from a shared tag's read-only checkout with a clean tree is
pinned, with its manifest row (`../tools/README.md`, T6, T9).
