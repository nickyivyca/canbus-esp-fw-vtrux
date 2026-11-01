# Shared restart guard: specification

**Status:** approved by the user on 2026-11-01, in the gen-inhibit
reviewer's session, and drafted that day by the gen-inhibit reviewer for
gen-inhibit's port step 1c; G3's "not supported" wording amended that day
(user), on the implementor's question. As a shared component's spec this follows port plan
goal 20: the reviewer whose project needs a change drafts it, the user
rules in that reviewer's session, and the other project's reviewer reads it
before it merges into `shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence:** `VERIFICATION.md`, the tester's, when there is code to
  test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (`restart_guard` and the component test apps under Layer 2 of "The
  split"), gen-inhibit's spec section 3 ("No restart leaves the controller
  on the bus"), `shared/can_port/SPEC.md` C7, `shared/twai_port/SPEC.md`
  T12 and `shared/boards/SPEC.md` B5.

Requirements are numbered G1 to G6. The numbers are stable.

- **G1. One component.** `shared/restart_guard/` is one ESP-IDF component,
  `vtrux/restart_guard`, in C on ESP-IDF APIs only (port plan goal 15). It
  is made from gen-inhibit's `gi_restart_quiesce.c`, at the commit the move
  is made from. gen-inhibit uses it from its port step 1c. The interposer
  adopts it in its stage (b) (gap fix 3).
- **G2. Every software restart takes the CAN ports off the bus first.**
  Every software restart, a crash included, runs G3's steps before the CPU
  resets. The guard runs at the point every software restart passes
  through, after the last code that could skip it: on ESP-IDF v5.4.1,
  `esp_restart_noos()`, reached through a link-time wrap that the component
  sets itself. A project gets the guard by depending on the component, with
  no switch and no call. Resets that bypass software (watchdog final
  stages, brown-out, power loss) are outside it.
- **G3. The steps,** in this order, with interrupts off from the start:
  1. for each CAN port the board has (`shared/boards` B5), in port order:
     the port's restart stop (`can_port` C7), then
     `board_can_standby(port, true)`. A port whose stop or standby answers
     "not supported" is skipped, and the steps go on: its standby bit stays
     0, and the record's controller fields come only from a TWAI port's
     stop that ran (G4; user, 2026-11-01);
  2. the record (G4);
  3. the real `esp_restart_noos()`.

  The guard and everything it calls are in IRAM or ROM: it takes no lock,
  allocates nothing, logs nothing, and touches only registers and the
  record. The longest wait is the TWAI stop's 1 ms (`twai_port` T12).
- **G4. The record of the last restart.** The guard writes a record of what
  it found and did at every restart, in every build, so a test build runs
  exactly the guard a release build runs. It is in RTC no-init memory, so it
  survives a software restart and a panic, and a magic number and a
  checksum tell it from what a power-on leaves there. It counts the guard's
  entries across restarts, and holds the TWAI port's stop report
  (`twai_port` T12) and which ports' transceivers were put into standby,
  one bit per port. Its layout and magic number are those of gen-inhibit's
  `gi_restart_record_t` at the move (user, 2026-11-01), with the standby
  field read as that bit field, which on a one-port board reads as it did.
  Only a test build reads it (G5).
- **G5. The test app.** `restart_guard/test_app/` holds the restart test,
  moved from gen-inhibit's `gi_restart_test.c`. It is compiled into a
  project's image only when the component's build switch
  `RESTART_GUARD_TEST` is set, and never into a release image
  (`shared/tools` R20). The project registers it from its URI hook
  (`shared/wican` W8). It gives:
  - `GET /gi_quiesce`: the record of the last restart, the controller and
    the standby pin as the new boot found them before `app_main`, the same
    just before the first driver install of this boot, the reset reason,
    the version and the OTA partitions;
  - `POST /gi_quiesce?crash=illegal|abort`: answer, then panic 300 ms
    later, from a task of its own.

  The URIs, the page's fields and the record are unchanged from gen-inhibit's
  at the move (user, 2026-11-01), so the bench reads them as before. The
  pre-install snapshot is a call the project makes just before its first
  driver install. The test app reads the standby pin from the GPIO
  registers, which no board call offers, so its source is on the
  component's own list of files that use pins (`shared/boards` B6). Its
  results are evidence for this component version, citable by both
  projects.
- **G6. Ownership** is per file in the component's `README.md`: the guard,
  its test app and their unit tests are the implementors'; the component's
  build-switch table is the testers'.
