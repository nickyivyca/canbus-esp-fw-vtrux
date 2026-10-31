# Shared WiCAN stock code: specification

**Status:** approved by the user on 2026-10-29, in the gen-inhibit
reviewer's session, and drafted that day by the gen-inhibit reviewer for
gen-inhibit's port step 1a; W7 amended 2026-10-29, for the rollback
rehearsal's fault hook, and 2026-10-30, for its symbol; W1 amended
2026-10-30, for `debug_logs`'s link cycle; W3 amended 2026-10-31, for
port step 1b's `can_port`. As
a shared component's spec this follows port plan goal 20: the reviewer whose
project needs a change drafts it, the user rules in that reviewer's session,
and the other project's reviewer reads it before it merges into
`shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence:** `VERIFICATION.md`, the tester's, when there is code to
  test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (goal 18, and "Stock WiCAN code" under Layer 4 of "The split"), and
  `shared/boards/SPEC.md`.

Requirements are numbered W1 to W11. The numbers are stable.

- **W1. One component.** `shared/wican/` is one ESP-IDF component,
  `vtrux/wican`, which a project takes at a pinned tag through the
  Component Manager, as it takes its board (`shared/boards` B1). It holds
  the stock WiCAN code, live and dead (goal 18): `config_server`,
  `comm_server`, `slcan`, `elm327`, `mqtt`, `autopid`,
  `expression_parser`, `sleep_mode`, `ota_health`, `wifi_network`,
  `wc_mdns`, `dev_status`, `wc_timer`, `wc_uart`, `hw_config`, their
  headers and the headers they share, `homepage.html`, `main.c`'s stock
  start-up (W4), and the files stock does not build (`ftp.c`, `gvret.c`,
  `realdash.c`, `led.c`, `homepage_full.html`), still not built.
  `debug_logs` is its own component, `shared/debug_logs/`
  (`vtrux/debug_logs`), unchanged but for one line of its
  `CMakeLists.txt`: it no longer requires `main`, which none of its files
  uses. That line closed a link cycle that left AutoPID's calls into it
  unresolved.
- **W2. It starts as a move.** The component's files start as the files of
  `gen-inhibit/main` at the commit the move is made from, byte for byte,
  and change only as W3 to W10 say. **Stock behaviour** in this spec means
  that code's behaviour, which includes gen-inhibit's changes made before
  the port: the 403 replies, the removed sleep state machine, the
  Bluetooth strip. Any other change is a change to this spec.
- **W3. The CAN functions are `can_port`'s** (port step 1b). The
  component's files call the CAN functions declared in `can.h`, and
  `vtrux/can_port` implements them on port 0 (`shared/can_port` C3). Until
  1b the project supplied them.
- **W4. Start-up is two calls.** The project's `app_main` registers its
  callbacks (W5) and URI hook (W8), then calls `wican_init()`, then its own
  start-up, then `wican_start()`.
  - `wican_init()` does stock start-up up to and including the CAN
    driver's init: device status, OTA health, NVS, netif and the event
    loop, the LEDs, `board_can_init(0)`, the queues, the device id, the
    config server, `slcan_init`, `can_init` at the configured bitrate, and
    the CAN health report.
  - `wican_start()` does the rest, in today's order: the protocol
    branches, Wi-Fi, the TCP server, the version and hardware strings,
    mDNS, the CAN receive and send tasks, sleep mode, the power LED and the
    log level.
  - The project reads the configured bitrate and CAN mode through the
    config server's getters, as today.
- **W5. Bus-owner callbacks.** A project may register one set of
  callbacks before `wican_init()`. Without them the component behaves as
  upstream WiCAN: the bus is never owned and an OTA always proceeds.
  - `owns_bus()`: while it returns true, the HTTP handlers that write
    flash answer 403 and write nothing; the Bluetooth config writer writes
    nothing; ELM327's `ATSP` is refused; and the CAN receive task does not
    read the bus.
  - `ota_begin()`: called before the OTA handler touches the bus. "Go
    ahead" lets the upload run. "Refused" gives 403, and "no answer" gives
    500, with nothing touched in either case.
  - `ota_failed()`: called on every exit of the OTA handler that does not
    end in the restart into the new image.
  - `command_refused()`: called once for each inbound SLCAN command that
    W6 refuses.
  - The body of each refusal comes from the project, so a project's replies
    are its own.
  - Each callback runs on the task that reaches it (the HTTP server, the
    CAN tasks) and bounds its own waiting; the component adds no timeout.
- **W6. SLCAN commands are a build switch.** `WICAN_SLCAN_COMMANDS`, a
  CMake option of the component, on by default, as upstream. Off, no call
  to the SLCAN command parser is compiled, on any channel: each inbound
  buffer that would have reached it is refused, counted through
  `command_refused()`, and logged at most once in 5 s, as today.
  Receive-only monitoring is not affected.
- **W7. Feature switches.** `WICAN_FEATURE_MQTT`, `_ELM327`, `_AUTOPID`
  and `_REALDASH` are CMake options of the component, on by default, with
  `wican_features.h`, under today's rules: off takes the feature's source
  out of the component and its call sites out of the build. A project sets
  them in its top-level `CMakeLists.txt`. `OTA_HEALTH_FAULT_INJECT`, the
  fault hook in `ota_health.c` that a rollback rehearsal builds with, is a
  CMake option of the component too: off by default, and test-only. The
  fault hook's code is a function of its own, kept out of line, so the
  ELF of an image built with the switch on carries its symbol at any log
  level, and the component's refusal row judges that symbol
  (`shared/tools` R20).
- **W8. Project URIs.** A project may register one URI hook and the number
  of handler slots it needs. The server's handler table is stock's 21 plus
  that number. The hook is called where gen-inhibit's endpoints are
  registered today, after the OTA endpoint and before the HTTP health
  report, at the first start and at a restart, with a flag saying which.
  The restart's shorter stock list stays (stage (a)).
- **W9. It names no board.** The component builds against the project's
  one board dependency (`shared/boards` B2, `shared/tools` R21) for
  `board.h`, `board_hw_name()` and pins, and names no board itself.
- **W10. Pins.** The component's files that include `board_pins.h` are
  listed in the component (the start-up file, for the LEDs and the
  Bluetooth-enable input), and `shared/tools` R30 checks the list.
- **W11. Ownership** is recorded per file in the component's `README.md`:
  its files are product code, owned by the implementor of the project that
  needs the change; a check's data, if any, by that project's tester.
