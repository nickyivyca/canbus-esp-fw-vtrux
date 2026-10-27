# Shared board layer: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that a
board component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *complete*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`.
gen-inhibit's spec is
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`.

## 1. Common

### B1. One component per board
- **Basis:** port plan goal 5 (user, 2026-10-16; scope 2026-10-17), board
  definitions in shared code; `shared/tools` R22 (user, 2026-10-25), every
  board's data in its own folder under `shared/boards/`; goal 20 items 7 and
  8, shared code only as a pinned tag.
- **Status:** complete.

### B2. A project names its board by its dependency
- **Basis:** user, 2026-10-26, in the gen-inhibit reviewer's session,
  proposed by the charge-interposer reviewer: the fact is recorded once, so
  it cannot disagree with a second record. The other option was a `board`
  key in the project's build data, checked against the lock.
- **Status:** complete.

### B3. Three kinds of setting
- **Basis:** user, 2026-10-26, in the gen-inhibit reviewer's session: "there
  should be a set of required parameters set by the consuming project, such
  as console, watchdogs, bluetooth status, and then things like LEDs and
  buttons are exposed but not required at the same level". This narrows
  the plan's Layer 1 list of `wican_v3` chip defaults: the 20 ms erase
  yield and the watchdog settings are gen-inhibit spec requirements (9.3
  and 9.1), so they stay project data.
- **ESP-IDF options only** (user, 2026-10-27, in the gen-inhibit reviewer's
  session, raised by the charge-interposer reviewer): the options check
  judges every build folder, and the bootloader's has no options from
  downloaded components (the finding that kept
  `CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL` out of the interposer's profile). No
  board fact needs one: the cases are settings such as mDNS's or the Arduino
  core's, which are one project's choice. Revisit if a board needs one.
- **Status:** complete.

### B4. `board.json`
- **Basis:** `shared/tools` R21 and its readings (2026-10-24): the reference
  table is a `.bin` named by path, with no stored hash beside it, since git
  is the reference copy, and the slot is the smallest app partition of the
  reference table. The project-required settings (B3) and the hardware name
  (B5) are added for the checks of `shared/tools` R29 and for
  `board_hw_name()` (user, 2026-10-26).
- **Status:** complete.

### B5. Interface
- **Basis:** port plan goal 5 and Layer 1 (user, 2026-10-16/17): project
  code asks for a port, never a pin; standby control is a board-layer call,
  because gen-inhibit's restart guard handles only standby pins below 32 and
  the WiCAN Pro's is GPIO 38. Offered outputs and inputs: B3. The hardware
  name comes from the board (user, 2026-10-26), replacing the string upstream
  WiCAN sets from `HARDWARE_VER`; it is board data whether or not a project
  has a status page to show it (wording, user, 2026-10-27, raised by the
  charge-interposer reviewer: the X2 has none yet).
- **Status:** complete.

### B6. Pins in `include/board_pins.h`
- **Basis:** user, 2026-10-26, in the gen-inhibit reviewer's session: a
  separate file, with the stock files that still use pins listed, so the
  port-not-pin rule is checked rather than left to review. Same names
  across a family: upstream WiCAN builds one tree for every WiCAN by giving
  each model the same pin names (`hw_config.h`), which the WiCAN boards
  keep.
- **Status:** complete.

### B7. Ownership
- **Basis:** AGENTS.md "Who owns what" and "Shared code": product code is
  the implementor's, everything that verifies it the tester's, and a
  shared file is owned by the role, in whichever project needs the change.
  The board data and reference table are the tester's today, in
  gen-inhibit's `vtrux_checks.json` and `test/build_checks/reference/`
  (user, 2026-10-26).
- **Status:** complete.

## 2. `wican_v3`

- **Basis:**
  - target, flash size, slot and the partition table byte-identical to
    stock: gen-inhibit spec section 2 and 12.3 item 3;
  - the reference table and its entries: the stock v4.13 full flash set's
    partition table, as gen-inhibit's build checks hold it (read
    2026-10-26);
  - flash frequency and mode, the errata workaround, the console hardware:
    gen-inhibit's committed `sdkconfig` and spec section 2 (the internal USB
    port, 2026-09-10);
  - pins and the hardware name: upstream WiCAN's `main/hw_config.h` and
    top-level `CMakeLists.txt`, `WICAN_V300` (read 2026-10-26);
  - standby polarity and the pull-up: gen-inhibit spec section 14 item 1,
    which describes the wiring (that section binds nothing), and the stock
    code, which sets GPIO6 high at boot and low on the bus (`main.c`,
    `can.c`); the receiver off in standby: gen-inhibit spec section 3;
  - the required settings: B3 (user, 2026-10-26). gen-inhibit's spec sets
    them in 9.1, 9.4 and 12.3 item 3.
- **Status:** complete.

## 3. `x2`

- **Basis:**
  - target, flash size and frequency, and the partition table: the board
    itself, read 2026-10-25 by the charge-interposer reviewer from its
    2026-09-29 flash backup (the table at 0x8000; chip id 9, size code 3 in
    the bootloader and app headers), and confirmed by that project's tester
    the same day against the baseline images (`36edf3e55552`, built at
    `4a59647`): the baseline `partitions.bin` is byte-identical to the
    board's table, and the header bytes are the same in all six images
    (charge-interposer review tracker, "X2 board data"). The table is
    PlatformIO's `default_8MB.csv`, which the interposer's `platformio.ini`
    has used since `7a27421` (2026-10-08);
  - flash mode: `platformio.ini` sets QIO; the headers read DIO because
    ESP-IDF writes DIO for QIO (`components/esptool_py/Kconfig.projbuild`,
    v5.4.1, read 2026-10-26);
  - the errata workaround: ESP-IDF's TWAI Kconfig lists the S3 among the
    affected chips; it is on in the prebuilt Arduino libraries the
    interposer builds with today, and that project's spec 2.3 relies on a
    silent listen-only port;
  - the required settings: B3 (user, 2026-10-26), with the same list as
    `wican_v3`, plus PSRAM, which the module carries, and the core dump,
    for which the table has a partition (user, 2026-10-27). The charge
    interposer's spec sets them at its port step 2a;
  - pins and crystal: the port plan's Layer 1, the interposer's board
    wiring in `firmware/src/main.cpp` and `port_mcp2515.h`, arduino-esp32's
    `aslcanx2` variant, and the Autosport Labs wiki for INT on GPIO3, which
    the variant lacks; STBY hardwired: the interposer's `main.cpp` header,
    from the board;
  - console hardware: the interposer's first bring-up, 2026-09-10 (UART0
    pins with nothing attached);
  - the LED on GPIO2: the `aslcanx2` variant, and the board itself: a
    throwaway image toggling GPIO2 every 500 ms blinked LED1, seen by the
    user (charge-interposer test evidence, section 9, "BOARD DATA ONLY --
    the X2's LED1 is on GPIO2", 2026-10-27). Its polarity was not
    separated.
- **Status:** complete.
