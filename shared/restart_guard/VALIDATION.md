# Shared restart guard: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *complete*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
("Layer 2: shared components", `restart_guard` and the component test
apps; goal 15). gen-inhibit's spec is
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`. The
readings of gen-inhibit's code are of `gen-inhibit/main` at `6e56289`
(`gen-inhibit/firmware/main/gi_restart_quiesce.c` and `.h`,
`gi_restart_test.c` and `.h`, `gen_inhibit.c`, `main/CMakeLists.txt`).

**Measurements on the code before the move are context, not the basis.**
The bench figures below were made on gen-inhibit's own restart step, before
it was a shared component. They show why the rule exists; they are not
evidence that this component meets it (AGENTS.md, "Spec, validation and
verification").

### G1. One component
- **Basis:** port plan "Layer 2", `restart_guard`, gen-inhibit stage (a),
  from `gi_restart_quiesce.c`, using the board standby call; the
  interposer adopts it in its stage (b), as the third of its gap fixes
  (goal 14). Goal 15 (user, 2026-10-17): shared components use ESP-IDF
  APIs only.
- **Status:** complete.

### G2. Every software restart takes the CAN ports off the bus first
- **Basis:** gen-inhibit spec 3, "No restart leaves the controller on the
  bus" (user, 2026-10-13): every software restart, a crash included, takes
  the controller off the bus before the CPU resets, at the point every
  software restart passes through, after the last code that could skip it.
- **Reading of ESP-IDF v5.4.1** (in `gi_restart_quiesce.c`'s header,
  reviewed by the gen-inhibit reviewer 2026-10-13): `esp_restart()`,
  `panic_restart()` and `call_start_cpu0`'s two early-boot failures all
  call `esp_restart_noos()`, each from another object file, so a link-time
  wrap reaches all of them. On the C3 that function does not reset the
  TWAI peripheral, and drops the CPU clock from 80 to 40 MHz before the CPU
  reset, so a running controller keeps going at half its clock.
- **Context (before the move):** on the bench without the step, each live
  restart put 107-139 error frames on the bus (2026-10-12); with it, on the
  trial build `089cd21`, 6 of 6 live restarts put 0 (2026-10-13).
- **The component sets the wrap itself** (user, 2026-11-01, sources in the
  component): `gi_restart_quiesce.c` has no switch ("the step is the
  behaviour"), and today `main/CMakeLists.txt` adds the wrap. Moving it into
  the component keeps the step in every build that has the component, with
  nothing for the project to remember.
- **Known limits** (charge-interposer reviewer's read, 2026-11-01; user,
  2026-11-01):
  - G2 holds only where the panic action restarts. With the panic action
    set to halt or to the GDB stub, a crash never reaches
    `esp_restart_noos()`: the guard never runs, and the controller stays
    on the bus.
  - On a crash the guard does not run first. The panic handler first
    prints its output and, if the project enables them, writes a core dump
    or runs other panic-time features. The controller is unguarded for
    that time. From reading the code, not measured: the controller's clock
    has not dropped yet at that point (the drop is inside
    `esp_restart_noos()`, after the guard), so it runs at its normal timing
    with interrupts off: it keeps acknowledging frames and can still send
    or retry the frame in its buffer. Each project weighs its panic action,
    panic output and core-dump choice against this in its own settings.
  - gen-inhibit's settings today: print and restart, no restart delay, no
    core dump (`sdkconfig`).
- **Status:** complete.

### G3. The steps
- **Basis:** the order and bounds of `gi_restart_quiesce.c`, unchanged
  (stage (a), goal 14): interrupts off, the controller into reset mode after
  a wait of at most 1 ms for the bus to be between frames with our own
  awaiting frame aborted on every poll, then the transceiver into standby,
  then the record, then the real restart.
- **Each port's driver stops its own controller** (user, 2026-11-01): the
  guard reaches each controller through `can_port`'s restart stop (C7),
  which calls the driver for the port's kind, so the guard holds no
  register code and needs no change when an SPI CAN driver gains a stop.
  The TWAI stop is `twai_port` T12. An SPI port has none until stage (b):
  the plan resets the MCP2515 over SPI at start-up instead, in
  `mcp2515_port`'s `begin()`, because it keeps running through an ESP reset
  and the SPI driver needs interrupts and locks the restart path does not
  have.
- **Every port the board has** (user, 2026-11-01): today the step puts
  port 0 into standby, the only CAN port of `wican_v3`. Both of the X2's
  ports answer "not supported" (`shared/boards` section 3: both
  transceivers' STBY is hardwired).
- **IRAM, nothing that needs the cache:** the callers have the cache
  enabled, but the real function disables it, so nothing in the guard may
  depend on where that happens (`gi_restart_quiesce.c`).
- **A "not supported" step is skipped, not recorded** (user, 2026-11-01,
  on the gen-inhibit implementor's questions at stage A): G3 first said
  such a port is recorded as such, but G4's unchanged layout has no field
  for it. The record keeps gen-inhibit's layout, so the step is skipped:
  the controller fields are filled only from a TWAI port's stop that ran,
  so they read as "clock off" when none did, and a standby that is not
  supported leaves the port's bit 0.
- **Each step answers for itself** (user, 2026-11-01): a port whose stop
  is not supported still gets standby. Standby does not depend on the
  controller, and it is then the only protection the port has: on an S3
  board with a standby line, such as the WiCAN Pro if it is added, the
  TWAI stop is unsupported until it is measured there; on an SPI CAN port
  with a standby line, the controller has no restart stop until
  `mcp2515_port` offers one (C7), if one proves possible (stage (b)). On both of today's boards the two readings behave the same:
  `wican_v3`'s port 0 is TWAI with a supported stop, and the X2 has no
  standby line.
- **Status:** complete.

### G4. The record of the last restart
- **Basis:** gen-inhibit spec 12.4, "no error frames at a restart": the
  restart record shows the step ran. Written in every build so the test
  build runs the guard a release build runs (`gi_restart_quiesce.h`).
- **Unchanged layout** (user, 2026-11-01: keep what the bench reads): the
  bench reads the record through the test app's page (G5), and a record
  left by an image from before the move reads the same after it. The
  standby field becomes one bit per port; with one port it reads 0 or 1, as
  before.
- **Status:** complete.

### G5. The test app
- **Basis:** port plan "Layer 2": a test app for each shared component, in
  its folder, among them `gi_restart_test.c` for the restart guard, whose
  results are evidence for that component version, citable by both
  projects.
- **Built into a project's image** (user, 2026-11-01): the sources move
  into the component and a project builds them in by the component's
  switch, as gen-inhibit's restart-test build does today, rather than as a
  separate image with its own control interface. The rig drives it as
  before; gen-inhibit's own 12.4 row needs a gen-inhibit image in any case,
  since the guard must run in the product's image. The folder is
  `test_app/`, because `test/` holds host tests in the other shared
  components.
- **Unchanged URIs and fields** (user, 2026-11-01): `/gi_quiesce` and its
  page stay as the baseline's, so step 1c is judged against the same
  results. Renaming them is left to stage (b).
- **The pin read:** `gi_restart_test.c` reads CAN_STDBY from the GPIO
  registers, which is test equipment's use of `board_pins.h`
  (`shared/boards` B6).
- **Status:** complete.

### G6. Ownership
- **Basis:** AGENTS.md, "Who owns what": product code and its unit tests
  are the implementor's; what verifies it is the tester's. The test app's
  sources were the implementor's in gen-inhibit (`gen-inhibit/README.md`),
  and the build-switch tables are the testers' (`shared/twai_port`
  README).
- **Status:** complete.
