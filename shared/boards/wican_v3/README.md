# shared/boards/wican_v3/

The board component for the MeatPi WiCAN OBD, hardware v3: an ESP32-C3 with
4 MB of flash and one CAN port. Its spec is `../SPEC.md`, the common rules
(B1 to B7) and section 2.

## Who owns which file (SPEC B7)

| File | What | Owner |
|---|---|---|
| `board.c` | the B5 calls for this board | the implementor of the project that needs the change |
| `include/board.h` | the B5 interface: CAN ports, TWAI pins, SPI CAN, standby, the offered LEDs and input, the hardware name, the per-model values | the same |
| `include/board_pins.h` | the pins, by upstream WiCAN's names (B6) | the same |
| `sdkconfig.board` | the ESP-IDF options the hardware fixes (B3.1) | the same |
| `partitions.csv` | the partition table, byte-identical to stock WiCAN v4.13 | the same |
| `CMakeLists.txt`, `idf_component.yml`, `README.md` | the component itself | the same |
| `board.json` | the checks' data (B4): target, flash size, the reference table's path, the settings required of the project, the hardware name | the tester of the project that needs the change |
| `stock-v4.13-partition-table.bin` | the reference partition table, extracted from the stock MeatPi v4.13 release: what `partitions.csv` must build to (B4, `shared/tools` R21) | the same |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) | the same |

gen-inhibit's implementor and tester wrote them first, at gen-inhibit's port
step 1a. Writing a file first does not make a project its owner.

## Taking it into a project

A project takes this component from git, at a tag of `shared/main` it pins
(SPEC B1, `shared/tools` R16 and R25), named `vtrux/wican_v3`:

    dependencies:
      vtrux/wican_v3:
        git: https://github.com/nickyivyca/canbus-esp-fw-vtrux.git
        path: shared/boards/wican_v3
        version: <tag>

It is then in `managed_components/vtrux__wican_v3/`, and `dependencies.lock`
records it as the project's one `shared/boards/*` component (B2). The name is
fixed: `sdkconfig.board` names the partition table by its path from the
project root, `managed_components/vtrux__wican_v3/partitions.csv`, so a
project whose dependency had another name, or that took the component from a
local folder (`override_path`), would not find it. So every build, dev builds
included, takes the board at a tag, and a board change is tried at a tag, in
a scratch repo if need be (B1).

The project's committed `sdkconfig` carries every option `sdkconfig.board`
names, with the board's value; a release build is checked for them
(`shared/tools` R29). The board also lists settings it leaves to the project
but every project must decide (SPEC section 2, "Required of the project"): the
console, the watchdogs' behaviour on a hang, and Bluetooth on or off. The
project's spec sets them, and its release profile names them.

## The interface

Project code includes `board.h` and asks for a port, never a pin:

- `board_can_ports()`: 1.
- `board_twai_pins(0, &p)`: TX GPIO0, RX GPIO3.
- `board_spi_can(0, &s)`: not supported; this board has no SPI CAN.
- `board_can_init(0)`: makes the standby line, GPIO6, an output and puts the
  transceiver in standby (high). Once, at start-up, not from the restart
  path.
- `board_can_standby(0, on)`: standby (on, GPIO6 high, the receiver and
  transmitter off) or normal operation (off, low). One register write from
  IRAM, so the restart path may call it with interrupts off and the cache
  disabled. The transceiver's pull-up holds it in standby while the pin
  floats.
- `board_led(BOARD_LED_CONNECTED | BOARD_LED_ACTIVE | BOARD_LED_POWER, on)`:
  GPIO8, GPIO9 and GPIO7, each set up as an output on first use.
- `board_ble_enable_input()`: the Bluetooth-enable input, GPIO5, true when it
  is low; set up as an input on first use.
- `board_hw_name()`: `"OBD"`.

The per-model values that stock WiCAN code sets per model and uses at compile
time are macros in `board.h` (B5, SPEC section 2 "Per-model values"), so a
second WiCAN model's board needs no change to project code:

- `FS_MOUNT_POINT`: `"/spiffs"`, the `storage` partition, under upstream's
  name.
- `BOARD_AP_IP_ADDR`: `"192.168.80.1"`, the hotspot's address, which is also
  its gateway.
- `BOARD_AP_NETMASK`: `"255.255.255.0"`.

The hotspot's two are dotted strings, for `esp_netif_str_to_ip4()`.

`board_pins.h` holds the pins under upstream WiCAN's names (`TX_GPIO_NUM`,
`CAN_STDBY_GPIO_NUM`, the LED names, `BLE_EN_PIN_NUM`), so stock code builds
for any WiCAN. Only `board.c`, test equipment, and the stock files on a
project's list of files that still use pins include it (B6, `shared/tools`
R30).
