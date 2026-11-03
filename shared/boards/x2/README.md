# shared/boards/x2/

The board component for the Autosport Labs ESP32-CAN-X2: an ESP32-S3 with
8 MB of flash and two CAN ports. Its spec is `../SPEC.md`, the common rules
(B1 to B7) and section 3.

## Who owns which file (SPEC B7)

| File | What | Owner |
|---|---|---|
| `board.c` | the B5 calls for this board | the implementor of the project that needs the change |
| `include/board.h` | the B5 interface: CAN ports and their kind, TWAI pins, SPI CAN, standby, the offered LED, the hardware name | the same |
| `include/board_pins.h` | the pins (B6) | the same |
| `sdkconfig.board` | the ESP-IDF options the hardware fixes (B3.1) | the same |
| `partitions.csv` | the partition table, byte-identical to the one the X2 carries | the same |
| `CMakeLists.txt`, `idf_component.yml`, `README.md` | the component itself | the same |
| `board.json` | the checks' data (B4): target, flash size, the reference table's path, the settings required of the project, the hardware name | the tester of the project that needs the change |
| `baseline-partition-table.bin` | the reference partition table, the charge interposer's baseline `partitions.bin` (3072 bytes, sha256 `1d9cca96...4c80c`): what `partitions.csv` must build to (B4, `shared/tools` R21) | the same |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) | the same |

The charge interposer's implementor and tester wrote them first, at its port
step 2a. Writing a file first does not make a project its owner.

## Taking it into a project

A project takes this component from git, at a tag of `shared/main` it pins
(SPEC B1, `shared/tools` R16 and R25), named `vtrux/x2`:

    dependencies:
      vtrux/x2:
        git: https://github.com/nickyivyca/canbus-esp-fw-vtrux.git
        path: shared/boards/x2
        version: <tag>

It is then in `managed_components/vtrux__x2/`, and `dependencies.lock`
records it as the project's one `shared/boards/*` component (B2). The name is
fixed: `sdkconfig.board` names the partition table by its path from the
project root, `managed_components/vtrux__x2/partitions.csv`, so a project
whose dependency had another name, or that took the component from a local
folder (`override_path`), would not find it. So every build, dev builds
included, takes the board at a tag, and a board change is tried at a tag, in
a scratch repo if need be (B1).

The project's committed `sdkconfig` carries every option `sdkconfig.board`
names, with the board's value; a release build is checked for them
(`shared/tools` R29). The board also lists settings it leaves to the project
but every project must decide (SPEC section 3, "Required of the project"): the
console and the secondary console, the watchdogs' behaviour on a hang,
Bluetooth on or off, PSRAM on or off, and where a core dump goes. The
project's spec sets them, and its release profile names them.

## The interface

Project code includes `board.h` and asks for a port, never a pin:

- `board_can_ports()`: 2.
- `board_can_kind(0)`: `BOARD_CAN_TWAI`; `board_can_kind(1)`:
  `BOARD_CAN_SPI`; any other port is `BOARD_CAN_NONE`. It and
  `board_can_ports()` are in IRAM and return constants, so the restart path
  may call them (`shared/can_port` C7, `shared/restart_guard` G3).
- `board_twai_pins(0, &p)`: TX GPIO7, RX GPIO6. Port 1 is not supported.
- `board_spi_can(1, &s)`: an MCP2515 on `SPI3_HOST` (arduino-esp32's `HSPI`
  on the S3), CS GPIO10, SCK GPIO12, MISO GPIO13, MOSI GPIO11, INT GPIO3
  (active low), 16 MHz crystal. Port 0 is not supported. The SPI clock is
  the driver's choice.
- `board_can_init(port)` and `board_can_standby(port, on)`: not supported
  for either port. Both MCP2562 transceivers have STBY hardwired, so there
  is no standby line. `board_can_standby` is in IRAM, so the restart path
  may still call it.
- `board_led(BOARD_LED_LED1, on)`: GPIO2, set up as an output on first use.
- `board_hw_name()`: `"ESP32-CAN-X2"`.

`board_pins.h` holds the pins. Only `board.c`, test equipment, and the files
on a project's list of files that still use pins include it (B6,
`shared/tools` R30).
