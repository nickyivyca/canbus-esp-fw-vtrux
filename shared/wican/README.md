# shared/wican/

The stock WiCAN code, as one ESP-IDF component, `vtrux/wican`, which a
project takes at a pinned tag (port plan goal 18, `SPEC.md` W1). It came from
gen-inhibit's tree at port step 1a, byte for byte from `gen-inhibit/main`
`0b03841`, and changes only as `SPEC.md` W3 to W10 say (W2). Stock behaviour
here includes gen-inhibit's changes made before the port: the 403 replies,
the removed sleep state machine, the Bluetooth strip.

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

## Who owns which file (SPEC W11)

The component's files are product code, owned by the implementor of the
project that needs the change. gen-inhibit's implementor moved them in;
writing a file first does not make a project its owner.

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, W1 to W11, and why each requirement is written as it is | the reviewers, as above |
| `wican.c`, `wican.h` | stock `main.c`'s start-up as `wican_init()` and `wican_start()` (W4), the CAN receive and send tasks, the bus-owner callbacks (W5), the SLCAN command switch (W6), the URI hook (W8) | the implementor of the project that needs the change |
| `can.h` | the CAN functions the component's files call, declared, not implemented: the project supplies them (W3) | the same |
| `config_server.c`/`.h` | the HTTP server: configuration, status, OTA, the websocket; the flash-write and OTA gates call the bus owner (W5); the project's URIs at the hook (W8) | the same |
| `comm_server.c`/`.h` | the TCP/UDP port | the same |
| `slcan.c`/`.h` | SLCAN framing, and the command parser W6 switches | the same |
| `elm327.c`/`.h`, `autopid.c`/`.h`, `mqtt.c`/`.h` | three stock features, each with its switch (W7); ELM327's `ATSP` is refused while the bus is owned (W5) | the same |
| `expression_parser.c`/`.h`, `std_pid.h`, `obd2_standard_pids.h` | AutoPID's parser and PID tables | the same |
| `sleep_mode.c`/`.h` | the battery voltage sampler (the sleep state machine is removed) | the same |
| `ota_health.c`/`.h` | the rollback gate: marks the image valid once Wi-Fi and the OTA endpoint are up | the same |
| `wifi_network.c`/`.h`, `wc_mdns.c`/`.h` | Wi-Fi, the hotspot (its address from the board's `board.h`), mDNS | the same |
| `dev_status.c`/`.h`, `wc_timer.c`/`.h`, `wc_uart.c`/`.h` | device status bits, timers, the UART channel | the same |
| `hw_config.c`/`.h` | stock's model numbers and the device id; includes the board's `board.h` | the same |
| `types.h`, `ver.h` | shared types and stock's version header | the same |
| `wican_features.h` | refuses a build that left a switch undefined (W6, W7) | the same |
| `homepage.html` | the web UI, embedded in the image | the same |
| `ftp.c`/`.h`, `gvret.c`/`.h`, `realdash.c`/`.h`, `led.c`/`.h`, `homepage_full.html` | files stock does not build; kept, still not built (W1) | the same |
| `CMakeLists.txt`, `idf_component.yml`, `README.md` | the component itself: its sources, its switches as CMake options, finding the project's board (W9) | the same |
| `pins.json` | the component's list of files that include the board's `board_pins.h` (W10), which `shared/tools` R30 checks | the same |

## Taking it into a project

A project takes this component and `vtrux/debug_logs` from git, at a tag of
`shared/main` it pins, beside its board:

    dependencies:
      vtrux/wican:
        git: https://github.com/nickyivyca/canbus-esp-fw-vtrux.git
        path: shared/wican
        version: <tag>
      vtrux/debug_logs:
        git: https://github.com/nickyivyca/canbus-esp-fw-vtrux.git
        path: shared/debug_logs
        version: <tag>

The project then supplies:

- **The CAN functions** `can.h` declares (W3), in its own files.
- **`app_main`** (W4): register the bus-owner callbacks
  (`wican_register_bus_owner()`) and the URI hook (`wican_register_uris()`),
  if it has them, then `wican_init()`, then its own start-up, then
  `wican_start()`. It reads the configured bitrate and CAN mode through the
  config server's getters, `config_server_get_can_rate()` and
  `config_server_get_can_mode()`.
- **Its board** (W9): the component names none. It builds against the one
  component of the build that has `include/board_pins.h`, the project's
  `shared/boards` dependency, and stops the configure if there is not
  exactly one.
- **The switches** it wants off, in its top-level `CMakeLists.txt`, before
  `project()`, as cache variables, e.g. `set(WICAN_SLCAN_COMMANDS OFF CACHE
  BOOL "...")`: `WICAN_SLCAN_COMMANDS` (W6) and `WICAN_FEATURE_MQTT`,
  `_ELM327`, `_AUTOPID` and `_REALDASH` (W7), all on by default, as upstream.

Without callbacks the component behaves as upstream WiCAN: the bus is never
owned and an OTA always proceeds. A project that registers them gives the
refusal bodies too, so its replies are its own (W5).
