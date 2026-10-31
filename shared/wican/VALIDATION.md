# Shared WiCAN stock code: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *complete*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`.
gen-inhibit's spec is
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`. The
readings of gen-inhibit's code below are of `gen-inhibit/main` at `0b03841`,
`gen-inhibit/firmware/main/`.

### W1. One component
- **Basis:** port plan goal 18 (user, 2026-10-17): all stock WiCAN code,
  live and dead, goes into `shared/` during the port; the list is the
  plan's "Stock WiCAN code" (Layer 4 of "The split"). One component rather
  than several: user, 2026-10-29, in the gen-inhibit reviewer's session.
  The stock files call each other throughout (the config server calls
  autopid, sleep mode and Wi-Fi; the start-up's CAN tasks feed slcan,
  ELM327 and MQTT), so a split would not be a move. `debug_logs` is already
  an IDF component of its own in gen-inhibit's tree
  (`firmware/components/debug_logs/`), so it stays one.
- **Reading:** `ftp.c`, `gvret.c`, `realdash.c`, `led.c` and
  `homepage_full.html` are not in the build of `main/CMakeLists.txt`;
  RealDash's live code is its boot branch in `main.c`.
- **`debug_logs`'s one changed line** (user, 2026-10-30, in the gen-inhibit
  reviewer's session, from gen-inhibit's port step 1a2): a build with
  AutoPID on, and MQTT and ELM327 with it, failed to link, with every
  undefined reference from `autopid.c` to `debug_logs_get_level` and
  `debug_logs_log` (the gen-inhibit tester's builds, at `c48e05b` and at
  `abf755b`). The cause (the gen-inhibit implementor's reading of the
  build's link line, 2026-10-30): `debug_logs` required `main`, `main`
  takes `vtrux/wican` and `vtrux/debug_logs`, and `vtrux/wican` requires
  `main` privately (W3), so the three are one cycle, which CMake lists
  twice; `app_main` pulls `wican.c`, and so `autopid.c`, from the second
  copy of `vtrux/wican`, after the last copy of `debug_logs`. In stock,
  `autopid.c` was in `main`, so the order did not matter. `debug_logs.c`
  includes nothing of `main`'s, its config header is its own, so the
  requirement was unused and was dropped. The other option was a
  link-order workaround in `vtrux/wican`'s `CMakeLists.txt`, leaving the
  cycle.
- **Status:** complete.

### W2. It starts as a move
- **Basis:** port plan "Stock WiCAN code": stage (a) keeps stock behaviour
  as it is; port plan step 1, each step accepted by the full suite giving
  the baseline's results, which holds only if the moved code is the code
  the baseline ran. gen-inhibit's earlier changes to stock files are part
  of that code. User, 2026-10-29, in the gen-inhibit reviewer's session,
  approving the draft, which named the consequence: the charge interposer
  inherits them when it takes this component in its stage (b).
- **Status:** complete.

### W3. The CAN functions are `can_port`'s
- **Since port step 1b** (user, 2026-10-31, in the gen-inhibit reviewer's
  session): `vtrux/can_port` implements the stock calls on port 0
  (`shared/can_port` C3), with today's behaviour; the project no longer
  supplies them.
- **Until then, the basis was:** user, 2026-10-29, in the gen-inhibit
  reviewer's session.
  Port step 1b replaces `can.c` with `twai_port` and `can_port`; moving it
  at 1a would move it twice and add hooks 1b removes.
- **Reading:** stock files that call `can.c`: `slcan.c`, `elm327.c`,
  `mqtt.c`, `config_server.c` (`can_disable`, `can_get_bitrate`),
  `sleep_mode.c` (`can_disable`).
- **Status:** complete.

### W4. Start-up is two calls
- **Basis:** user, 2026-10-29, in the gen-inhibit reviewer's session: the
  order stays visible in the project's own file. gen-inhibit's spec 3.2
  ("the boot-time configuration stays, and arming comes after it", user,
  2026-10-16) puts `gen_inhibit_init()` after `can_init` and before the
  protocol branches, which is where the split falls.
- **Status:** complete.

### W5. Bus-owner callbacks
- **Basis:** port plan "Stock WiCAN code": gen-inhibit's calls into the
  config server become bus-owner callbacks (`owns_bus`, `ota_begin`,
  `ota_failed`). User, 2026-10-29, in the gen-inhibit reviewer's session,
  approving the draft, which adds `command_refused()` and the project's
  refusal texts.
- **Reading:** gen-inhibit's calls in stock files: `config_server.c`,
  `gen_inhibit_owns_bus()` in the flash-write gate and the Bluetooth
  config writer, `gen_inhibit_ota_begin()` and `gen_inhibit_ota_failed()`
  in the OTA handler; `elm327.c`, `gen_inhibit_owns_bus()` refusing `ATSP`;
  `main.c`, `gen_inhibit_owns_bus()` in the CAN receive task and
  `gen_inhibit_note_refused_slcan()` in the send task. The plan's "five
  calls" in the config server counted differently; the code has the four
  above, besides the two endpoints' handlers, which move out whole (W8).
  The refusal bodies name gen-inhibit's endpoint.
- **Context, a known limit** (the charge-interposer reviewer's read,
  2026-10-29): `owns_bus()` does two jobs, keeping stock code off the bus
  and refusing flash writes. The interposer needs the first always and the
  second only by its own gate, so its stage (b) takes the flash-write
  decision away from `owns_bus()` (the plan's "flash write still
  permitted" callback), defaulting so gen-inhibit is unchanged. Until then
  `owns_bus()` covers both.
- **Status:** complete.

### W6. SLCAN commands are a build switch
- **Basis:** gen-inhibit spec 3.2 (review A3) and section 1 goal 2: a
  non-zero torque command is structurally impossible, which a switch that
  leaves no call compiled keeps and a run-time callback would not. User,
  2026-10-29, in the gen-inhibit reviewer's session, approving the draft.
  The build switches of a shared component are CMake options with the code
  they switch (user, 2026-10-25).
- **Status:** complete.

### W7. Feature switches
- **Basis:** gen-inhibit spec 3.2 and 12.3 item 3 (user, 2026-10-16), the
  four switches, on in the shared code and off in gen-inhibit's build;
  switches located with the code they own (user, 2026-10-25).
- **The fault hook** (user, 2026-10-29, in the gen-inhibit reviewer's
  session, raised by the gen-inhibit implementor): `OTA_HEALTH_FAULT_INJECT`
  gates code in `ota_health.c`, which is now the component's, so under
  `shared/tools` R20 the switch is the component's and its table lists it.
  The other option was gen-inhibit's top-level CMake file setting the
  define for every component; the component's source would still test the
  symbol, so its table would list it either way.
- **The fault hook's symbol** (user, 2026-10-30, in the gen-inhibit
  reviewer's session, from gen-inhibit's port step 1a2): at the release
  log levels of `shared/tools` R28 (none), the switch left nothing in the
  image or the ELF that a refusal row could judge. Its only string was
  the `ESP_LOGW` format, compiled out at none, and the rest was a few
  instructions inside `ota_health_report()`, which exists either way
  (the gen-inhibit implementor's reading of `2026.10.29`, 2026-10-30).
  R20's row judges the image and the ELF, never the flag, so the hook's
  code became a function of its own, out of line, whose symbol is
  present only with the switch on. The other option was to record the
  component's row as not catching the switch at log level none, leaving
  each project its own guard (gen-inhibit's `_flt` image name).
- **Context, a known limit** (the gen-inhibit reviewer's reading,
  2026-10-29, raised by the gen-inhibit tester): under `shared/tools` R20
  the component's switches are classified in its own table, which applies
  in every release build that takes it. The feature switches and
  `WICAN_SLCAN_COMMANDS` are classed there as stock features off, with
  refusal rows, as gen-inhibit's table had them. A project that turns one
  on, such as the charge interposer in its stage (b), needs R20 to give
  that class per project first.
- **Status:** complete.

### W8. Project URIs
- **Basis:** port plan "Stock WiCAN code": the project's endpoints move to
  its own layer through a "project URIs" hook, which also carries the test
  and probe registrations; stage (a) keeps `config_server_restart`
  registering fewer handlers than `config_server_init`. User, 2026-10-29,
  in the gen-inhibit reviewer's session, approving the draft.
- **Reading:** today the restart-test URIs are registered last, after the
  stock list, and the table is sized 21 plus their count. Under W8 they are
  registered at the hook's place, so a restart-test build's order changes;
  the table is sized for them, so no handler is displaced. Flagged to the
  user with the draft.
- **Status:** complete.

### W9. It names no board
- **Basis:** `shared/boards` B2 (user, 2026-10-26): a project names its
  board by its dependency and nowhere else, so a shared component naming
  one would be a second record.
- **Context, a known limit** (the charge-interposer reviewer's read,
  2026-10-29): the component builds for the WiCAN family today, whose
  boards share pin names (`shared/boards` B6). A board of another family,
  such as `x2`, which offers only `led1`, needs W10's pin users moved to
  board calls first, in the interposer's stage (b).
- **Status:** complete.

### W10. Pins
- **Basis:** user, 2026-10-29, in the gen-inhibit reviewer's session:
  listed and checked, as the project's own list is (`shared/boards`
  VALIDATION B6). Listed only was the other option.
- **Reading:** the pin users are `main.c`'s LEDs and Bluetooth-enable
  input; the config server, MQTT and the TCP server take the LED pin as a
  parameter.
- **Status:** complete.

### W11. Ownership
- **Basis:** AGENTS.md "Who owns what" and "Shared code", as
  `shared/boards` B7.
- **Status:** complete.
