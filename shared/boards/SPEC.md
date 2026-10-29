# Shared board layer: specification

**Status:** the common section (1) and the `wican_v3` section (2) were
approved by the user on 2026-10-26, in the gen-inhibit reviewer's session,
and drafted that day by the gen-inhibit reviewer; B3 and B5 amended there
2026-10-27, after the charge-interposer reviewer's read, and B1, B3 and B5
again that day, after the gen-inhibit tester's reading of a real build and
the gen-inhibit implementor's first build of `wican_v3`; section 2's
console members listed 2026-10-27, after the charge-interposer reviewer's
read. The `x2` section (3) is
drafted by the charge-interposer reviewer and ruled in its session; its
console members listed 2026-10-28. As a
shared component's spec this follows port plan goal 20: the reviewer whose
project needs a section drafts it, the user rules in that reviewer's
session, and the other project's reviewer reads it before it merges into
`shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence** that a board component meets it: `VERIFICATION.md`,
  the tester's, when there is code to test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (goal 5, Layer 1 of "The split"), and `shared/tools/SPEC.md` R21 and R22.

Requirements in section 1 are numbered B1 to B7. The numbers are stable.

## 1. Common

- **B1. One component per board.** `shared/boards/<board>/` is one ESP-IDF
  component on `shared/main`. A project takes it at a pinned tag like any
  shared firmware component (`shared/tools` R16 and R25), through the
  Component Manager, so it is recorded in the project's `dependencies.lock`.
  The project's build takes the partition table from the downloaded copy
  in `managed_components/`, so every build, dev builds included, takes the
  board at a tag, never through `override_path`; a board change is tried
  at a tag, if need be in a scratch repo. The board's settings are not
  layered into the project's: the project's committed `sdkconfig` carries
  the board's fixed values, and `shared/tools` R29 holds it to them.
- **B2. A project names its board by its dependency, and nowhere else.** A
  release build is refused unless `dependencies.lock` has exactly one
  `shared/boards/*` component. The checks read that component's data from
  `managed_components/`, so the board checked is the board built
  (`shared/tools` R21).
- **B3. Three kinds of setting.**
  1. *Fixed by the board.* The board's `sdkconfig.board` holds what the
     hardware fixes: the target, the flash size, frequency and mode, the
     partition table, and chip-bug workarounds. A project's build has the
     board's value for each of them (`shared/tools` R29).
  2. *Required of the project.* The board's section lists settings it
     leaves to the project but that every project building for it must
     decide. The project's spec sets and cites each value, and its release
     profile names it. A release build whose profile leaves one out fails
     (`shared/tools` R29). A required setting is one option, or one of
     ESP-IDF's pick-one choices (such as the console) listed with its
     member options; the project decides a choice by setting exactly one
     member on.
  3. *Offered.* Outputs and inputs the board provides through calls, such
     as LEDs and buttons. A project uses them or not.

- **B4. `board.json` is the checks' data:** `target`, `flash_size`, the
  path of the reference partition table `.bin`, the project-required
  settings (B3.2), and the hardware name. It holds no pins. The application
  slot is the smallest app partition of the reference table, not a separate
  number (`shared/tools` R21).
- **B5. Interface.** Project code asks the board for a port, never for a
  pin.
  - `board_can_ports()`: the number of CAN ports.
  - `board_twai_pins(port)`: the ESP's own TWAI pins, for a TWAI port.
  - `board_spi_can(port)`: the controller, SPI host, pins and crystal, for
    an SPI CAN port.
  - `board_can_init(port)`: run once at start-up, outside the restart path.
    It sets up the port's standby line and puts the transceiver into
    standby. It returns "not supported" where the board has no standby
    line.
  - `board_can_standby(port, on)`: puts the port's transceiver into standby
    (on) or normal operation (off). It returns "not supported" where the
    board has no standby line. It is safe to call from the restart path: no
    locks, no allocation, no logging, a register write only. It replaces the
    restart guard's direct `GPIO_OUT_W1TS` write and its GPIO-below-32
    assertion.
  - The offered outputs and inputs (B3.3), by name, where the board has
    them.
  - `board_hw_name()`: the hardware name, which a status page shows where
    the project has one.
  - Values upstream code sets per hardware model and uses at compile time,
    such as WiCAN's filesystem mount point and hotspot address, are macros
    in `include/board.h`, under the names that code uses, so a second model
    of the family needs no change to project code.
- **B6. Pins live in `include/board_pins.h`.** `board.c` includes it, and so
  may test equipment such as gen-inhibit's X2 jammer. A project file may
  include it only if it is on that project's list of stock files that still
  use pins (`shared/tools` R30). A file leaves the list when it moves to
  board calls. Boards of one family, such as the WiCANs, give their pins
  the same names, so stock code builds for any of them.
- **B7. Ownership** is recorded per file in each board's `README.md`.
  `board.c`, `include/board.h`, `include/board_pins.h`, `sdkconfig.board`
  and `partitions.csv` are product code, owned by the implementor of the
  project that needs the change. `board.json` and the reference partition
  table `.bin` are the checks' data, owned by that project's tester.

## 2. `wican_v3`: MeatPi WiCAN OBD, hardware v3

- **Target** `esp32c3`, single core. **Hardware name** `OBD`.
- **Fixed by the board:**
  - Flash: 4 MB, 80 MHz, QIO. ESP-IDF writes DIO into the image headers when
    QIO is chosen, and the bootloader switches to quad itself; R21 checks
    the size, not the mode.
  - Partition table: byte-identical to stock WiCAN v4.13 (gen-inhibit spec
    section 2):

    | Name | Type | Offset | Size |
    |---|---|---|---|
    | `nvs` | data, nvs | 0x9000 | 16K |
    | `otadata` | data, ota | 0xd000 | 8K |
    | `phy_init` | data, phy | 0xf000 | 4K |
    | `ota_0` | app | 0x10000 | 0x1b3000 (1,781,760 bytes) |
    | `ota_1` | app | 0x1d0000 | 0x1b3000 (1,781,760 bytes) |
    | `storage` | data, spiffs | 0x383000 | 300K, stock WiCAN's configuration |

    The reference is `stock-v4.13-partition-table.bin`, 3072 bytes, sha256
    `98d193a613ff7e9d14269ccfdb11e4bc652339d41d8b6f1aedea9fd7a9e2185e`, from
    the stock v4.13 full flash set. The application slot is 1,781,760 bytes.
  - `CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM=y`.
- **Required of the project:**
  - the console: ESP-IDF's pick-one "Channel for console output"
    (esp_system Kconfig), with members `CONFIG_ESP_CONSOLE_UART_DEFAULT`,
    `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`, `CONFIG_ESP_CONSOLE_UART_CUSTOM`
    and `CONFIG_ESP_CONSOLE_NONE` on the C3;
  - the secondary console: ESP-IDF's pick-one "Channel for console
    secondary output", with members `CONFIG_ESP_CONSOLE_SECONDARY_NONE`
    and `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG`. A USB-Serial/JTAG
    primary forces it to none; a UART primary leaves it open, so it is
    required;
  - the watchdogs' behaviour on a hang: `CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE`
    and `CONFIG_ESP_TASK_WDT_PANIC`;
  - Bluetooth on or off: `CONFIG_BT_ENABLED`.
- **CAN:** one port. Port 0 is the C3's TWAI, TX on GPIO0 and RX on GPIO3.
  Standby is GPIO6, wired to the MCP2561/2's STBY: high is standby, with
  the receiver and transmitter off, and low is normal. The transceiver's
  pull-up holds it in standby while the pin floats. No SPI CAN.
- **Offered:** LEDs `connected` (GPIO8), `active` (GPIO9) and `power`
  (GPIO7); the Bluetooth-enable input (GPIO5, low is enabled).
- **Console hardware:** the board's internal USB connector reaches the C3's
  USB-Serial/JTAG. Which console a project uses is project data.
- **Per-model values** (B5): the filesystem mount point `FS_MOUNT_POINT`,
  `/spiffs` (the `storage` partition); the hotspot address 192.168.80.1,
  which is also the gateway, with netmask 255.255.255.0.

## 3. `x2`: Autosport Labs ESP32-CAN-X2

Approved by the user on 2026-10-27, in the charge-interposer reviewer's
session.

- **Target** `esp32s3`, dual core. **Hardware name** `ESP32-CAN-X2`.
- **Fixed by the board:**
  - Flash: 8 MB, 80 MHz, QIO. As for `wican_v3`, ESP-IDF writes DIO into
    the image headers and the bootloader switches to quad itself; R21
    checks the size, not the mode.
  - Partition table: byte-identical to the one the X2 carries, PlatformIO's
    `default_8MB.csv`:

    | Name | Type | Offset | Size |
    |---|---|---|---|
    | `nvs` | data, nvs | 0x9000 | 20K |
    | `otadata` | data, ota | 0xe000 | 8K |
    | `app0` | app, ota_0 | 0x10000 | 0x330000 (3,342,336 bytes) |
    | `app1` | app, ota_1 | 0x340000 | 0x330000 (3,342,336 bytes) |
    | `spiffs` | data, spiffs | 0x670000 | 1536K, present but not mounted by the interposer |
    | `coredump` | data, coredump | 0x7f0000 | 64K |

    The reference is the charge interposer's baseline `partitions.bin`,
    3072 bytes, sha256
    `1d9cca96de0fe07ad7fc0648b9878ddecd9ce565e38b589ad20fea698ed4c80c`. The
    application slot is 3,342,336 bytes.
  - `CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM=y`: without it the S3's TWAI
    controller sends dominant bits in listen-only mode when it sees an
    error.
- **Required of the project:**
  - the console: ESP-IDF's pick-one "Channel for console output"
    (esp_system Kconfig), with members `CONFIG_ESP_CONSOLE_UART_DEFAULT`,
    `CONFIG_ESP_CONSOLE_USB_CDC`, `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`,
    `CONFIG_ESP_CONSOLE_UART_CUSTOM` and `CONFIG_ESP_CONSOLE_NONE` on the
    S3;
  - the secondary console: ESP-IDF's pick-one "Channel for console
    secondary output", with members `CONFIG_ESP_CONSOLE_SECONDARY_NONE`
    and `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG`. A USB-Serial/JTAG
    primary forces it to none; any other primary leaves it open, so it is
    required;
  - the watchdogs' behaviour on a hang: `CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE`
    and `CONFIG_ESP_TASK_WDT_PANIC`;
  - Bluetooth on or off: `CONFIG_BT_ENABLED`;
  - PSRAM on or off: `CONFIG_SPIRAM`. The module carries 8 MB of PSRAM;
    its mode is fixed here when a project first turns it on;
  - where a core dump goes: the `ESP_COREDUMP_TO_FLASH_OR_UART` choice
    (`CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH`, `_TO_UART` or `_TO_NONE`),
    since the table has a `coredump` partition.
- **CAN:** two ports. Port 0 is the S3's TWAI, TX on GPIO7 and RX on GPIO6.
  Port 1 is an MCP2515 on SPI2 (HSPI): CS GPIO10, SCK GPIO12, MISO GPIO13,
  MOSI GPIO11, INT GPIO3 (active low), with a 16 MHz crystal. The SPI clock
  is the driver's choice. Neither port has a standby line: both MCP2562
  transceivers have STBY hardwired, so `board_can_standby` returns "not
  supported" for both.
- **Offered:** LED `led1` (GPIO2).
- **Console hardware:** nothing is attached to UART0 (GPIO43/44); the USB
  connector reaches the S3's USB-Serial/JTAG. Which console a project uses
  is project data.
