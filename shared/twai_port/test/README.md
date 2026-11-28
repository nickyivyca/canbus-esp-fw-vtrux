# shared/twai_port/test/

The one host fake of the ESP-IDF TWAI driver (`../SPEC.md` T10), the
testers' (T11). Both projects' host builds compile the real `twai_port`,
and any other code that calls the TWAI driver, on it. Each project keeps
its own logic-level fakes (gen-inhibit's worker scheduler and wire
invariant, the interposer's MCP2515 model and host clock).

It was merged at gen-inhibit's port step 1b from gen-inhibit's
`gen-inhibit/test/gen_inhibit_shim/fake_twai.c` (with its register fakes,
`gen-inhibit/main` `802c1f7`) and the interposer's
`charge-interposer/firmware/test/host_bridge/twai_fake.cpp` (with its S3
calibration and recovery, `charge-interposer/main` `5426202`), and checked
against ESP-IDF v5.4.1's `components/driver/twai/twai.c`. Wherever the two
fakes disagreed with each other or with the source, the source decides. It
follows v5.4.2 since gen-inhibit's port step 1d ("ESP-IDF v5.4.2" below).

    make -C shared/twai_port/test run        # the fake's tests, and the port on it (every build)
    make -C shared/twai_port/test t12-standins  # the T12 cases fail on each broken restart stop
    make -C shared/twai_port/test switch-standins  # the switch cases fail on each broken switch
    make -C shared/twai_port/test switch-knobs  # the real switch on every unmeasured knob value (a report)
    make -C shared/twai_port/test mutants    # every mutant of the fake must fail its tests
    make -C shared/twai_port/test mutate-test  # mutate.py removes its scratch folder, pass or fail
    make -C shared/twai_port/test cxx        # the fake and cpp/ compiled as C++11
    make -C shared/twai_port/test cxx-test   # cpp/'s own tests
    make -C shared/twai_port/test host-switch-refused  # an ESP-IDF build refuses TWAI_PORT_HOST_SWITCH

`host-switch-refused` needs a shell exported for ESP-IDF v5.4.2
(`local-paths.md`); the others need only gcc and g++.

The binaries go to `scratch/twai_port-test/` and the mutants to
`scratch/twai_fake-mutants/<time>_<pid>/`, at the worktree's root. `mutate.py`
makes its folder, refuses one that already exists (`--out` names another),
and removes it when it ends, pass or fail (AGENTS.md, "Removing scratch"). A
killed run's folder stays, but as its own `<time>_<pid>`, it does not stop the
next run, as in `unit/mutate.py`. gcc and g++; the C
builds run under ASan and UBSan.

## Files

| File | What |
|---|---|
| `twai_fake.h`, `twai_fake.c` | the model and the driver's entry points: `twai_driver_install` to `twai_clear_receive_queue`, the ten register calls of `hal/twai_ll.h`, `REG_READ` of the TWAI's clock and reset bits, `esp_rom_delay_us`, a weak `esp_timer_get_time`, and `esp_cpu_stall`, `esp_cpu_unstall` and `esp_cpu_get_core_id`. C in the common subset of C11 and C++11 |
| `include/driver/twai.h`, `include/hal/twai_ll.h` | the driver's headers for host builds, from ESP-IDF v5.4.1's own (`driver/twai.h`, `hal/twai_types.h`, the C3's `hal/twai_ll.h`): the real types, field order, alert values and initialisers. The `_v2` calls are left out, so a call the fake does not implement fails to link |
| `include/soc/soc.h`, `include/soc/reg_base.h`, `include/soc/system_reg.h` | `BIT()`, `REG_READ()` and the TWAI's clock-enable and reset bits of `SYSTEM_PERIP_CLK_EN0_REG` and `SYSTEM_PERIP_RST_EN0_REG`, ESP-IDF v5.4.1's addresses and bits, for the restart stop (T12). `REG_READ()` is a call into the fake |
| `include/soc/rtc_cntl_reg.h` | the S3's two stall-code registers and their field shifts, ESP-IDF v5.4.2's, for the restart stop on two cores (T12, 2026-11-27) |
| `host/` | minimal stand-ins for `esp_err.h`, `esp_rom_sys.h`, `esp_cpu.h`, `freertos/FreeRTOS.h`, `freertos/task.h`, `driver/gpio.h`, `esp_attr.h`, `esp_log.h` and `board.h`, for a build with none of its own. A project's own come first on its include path |
| `cpp/twai_fake.h`, `cpp/twai_fake.cpp` | the interposer's C++ interface to the fake: `namespace twaifake`, `TwaiFake`, `fake()`, as its host_bridge tests call them, each member a call into the model. Its clock adapter is the interposer's `hostclock::micros()` |
| `cpp/test_cpp_wrapper.cpp` | the interface's own tests, for the members that do more than pass their arguments on (`setBusOffAfterSent`); `cpp/README.md` |
| `test_twai_fake.c` | the fake's own tests: 38 cases, each a driver or register behaviour from the source, or one of the assumptions the table names. The last 8 are the register-level switch, the two cores and the held interrupt ("The register-level switch" and "Two cores" below); `case_esp_timer` is `esp_timer_get_time` ("Busy waits") |
| `test_esp_timer_strong.c` | a build that links the fake and defines its own `esp_timer_get_time` keeps its own, as gen-inhibit's `gen_inhibit_shim` does: the fake's is weak (2026-11-27). Built by `all`, run by `run`, and built on every mutant by `mutate.py` |
| `mutate.py` | 109 mutants of `twai_fake.c`, one behaviour each; the run passes only if the unmutated build passes and every mutant fails, in `test_twai_fake.c` or in `test_esp_timer_strong.c` (which must link and keep its own) |
| `test_mutate.py` | `mutate.py`'s removal of its scratch folder, with the builds stood in for (no compiler): removed after a pass, a fail and an exception, read-only files and paths past 260 characters included; a folder it cannot remove printed with the reason; an existing `--out` refused and left as it was; with no `--out`, a new `<time>_<pid>` folder, which a killed run's folder does not stop. `--mutants` breaks these twelve ways, each of which it must fail by a check (three only fail on Windows) |
| `test_port_on_fake.c`, `host_platform.c` | the real `twai_port.c`, compiled as shipped, on the fake, four builds: the C3's register file on the fake's C3; the S3's file as shipped (not measured) and the unsupported file, each on the fake's S3; and the S3's file as the T19 measurement build compiles it (`TWAI_PORT_S3_PROBE`), on the fake's S3. 15 cases from T2 to T9 and T14 in each, one process per case: T14's is the switch refused where the file is not measured for it, and taken in the measurement build. On the C3 and in the measurement build, the T12 cases below; on the C3 one more, the stop stalls no core; in the measurement build two more, on two cores: the driver installed from the other core, a task there sending, interrupts off on the stop's core, and the stop stalls the other core before any of T12's steps, never starts it again, and cuts no frame; and a core already stalled (a panic) is not stalled again. On the S3 as shipped and the unsupported file, T12 is not supported. The T12 cases, the restart stop, 11 of them: an idle bus (reset mode at once, the report filled, nothing logged, and nothing takes it out of reset mode until the next install), a frame on the wire and one being received let finish, polled every 2 us, a reception and a transmission outlasting 1 ms (the wait runs out at 1 ms, reset mode anyway, the frame cut), a frame awaiting arbitration aborted, the clock off and the reset held (touches nothing, the report 0 but those bits), the driver running with the port never set up, bus-off (already in reset mode), and a NULL report; and, where T12 is not supported, 1 in their place (not supported, touching nothing). `host_platform.c` is the board, tick and log calls the port makes |
| `t12_standin.c` | the T12 cases' can-fail: the tester's own restart stop, written from T12 and the fake's register calls. Built with `-DT12_STANDIN`, the T12 cases call it in place of the port's, on the C3's file and on the S3's measurement build (with `-DT12_STANDIN_TWO_CORES`). As written it passes them all; `T12_BROKEN` breaks one rule (9 ways on both: no wait, no bound, no reset on timeout, no abort, the clock or the reset ignored, 10 us steps, the status or the error counters not reported; on the C3, a core stalled; on the S3, no stall, the stall after the steps, a core stalled again, the core started again), and `t12-standins` passes only if each is caught by a check (exit 1 with a FAIL line), never by a sanitizer |
| `test_switch_on_fake.c` | the kept-driver switch (T14, T15, T17, T18) through the port's interface, on the fake's S3, 13 cases: the driver kept across the switch with no queue discarded, sends refused in listen-only as `tx_refused`, frames still received; a port set up in listen-only; no-ACK still T4's; the switch refused while bus-off; the held-frame bound; REC 128 and 0 written in reset mode, read back and reported, the status's counters from the registers; no active error flag in listen-only; the held frames handed to the hook once each, head first, with their tags, counted once as `tx_discarded_switch` and never as `tx_failed`, and a send reaching the wire after; none held; no hook; every bus error up to the switch counted. Built against the real port (the S3's file with the host-only `TWAI_PORT_HOST_SWITCH`) and against `switch_standin.c`. `SWITCH_RESET_TX` and `SWITCH_ABORT_REPORT` choose the fake's unmeasured knobs. No result for the held frames is final until T16 is written and T19 has measured |
| `switch_standin.c` | the switch cases' can-fail: the tester's own port, the part the switch cases call, written from T14-T18 on the fake's calls. As written it passes every case on every knob value; `SWITCH_BROKEN` breaks one rule (13 ways), and `switch-standins` passes only if each is caught by a check |
| `host_switch_refused.sh` | `TWAI_PORT_HOST_SWITCH` refused by every ESP-IDF build, the evidence `vtrux_checks.json` rests on for it (a host-test-only define, "not a switch"; charge-interposer reviewer, 2026-11-27). In `scratch/`, a minimal S3 project with the port, `can_port` and the x2 board: its configure succeeds without the define and stops on the component's `FATAL_ERROR` with it; and `twai_port_ll.h` compiles on the host with the define and stops on its `#error` once `ESP_PLATFORM` is defined too. Each refusal has its control, so a refusal for another reason fails the control. `HOST_SWITCH_REFUSED_BROKEN=1` makes the refused runs without the define, and the script must then fail (`host-switch-refused` runs both) |
| `Makefile` | the builds above |
| `README.md` (here and in `include/`, `host/`, `cpp/`) | this |

## Wiring a project's host build to it

- **Sources:** `twai_fake.c` (and, for C++ tests written against the
  interposer's interface, `cpp/twai_fake.cpp`).
- **Include path:** the project's own stand-ins first, then `include/`,
  then `host/` for whatever the project has none of, then `cpp/` for the
  C++ interface. Remove the project's own `driver/twai.h` and
  `hal/twai_ll.h`.
- **The clock adapter:** `twai_fake_set_clock(fn, ctx)`, a function giving
  the project's virtual time in microseconds, in place of Arduino
  `millis()`. With none, the fake has its own clock, which
  `twai_fake_advance()` moves. `twai_fake_set_tick_us()` gives the tick
  length (default 1000 us).
- **Blocking calls:** `twai_receive`, `twai_read_alerts` and a
  `twai_transmit` waiting for queue space call the project's
  `twai_fake_set_wait_hook()` until the wait is satisfied or the deadline
  passes; without one they give up at once, as a zero wait does.
  `twai_fake_set_receive_yields_first()` is gen-inhibit's one frame per
  worker turn.
- **Checks on what is sent:** `twai_fake_set_tx_hook()` sees every
  `twai_transmit()` call before any check, refused or not, and
  `twai_fake_set_tx_accepted_hook()` every frame the driver accepted.
  gen-inhibit's wire invariant goes there.
- **Busy waits:** `esp_rom_delay_us()` is the fake's, so a project's host
  build must not define its own. It moves the fake's own clock; with a
  clock adapter it calls the wait hook up to its deadline, and with no hook
  it only counts (`twai_fake_delay_calls()`). `esp_timer_get_time()` is the
  fake's as well, but weak, so a build may keep its own: the fake's clock
  plus every busy wait the clock did not see (with an adapter and no hook,
  or a hook that gave up short of the deadline). It moves by each delay in
  every mode, so a wait bounded on it, such as `twai_port`'s abort wait at
  a switch (T17), ends (the implementor and the charge-interposer tester,
  2026-11-27).
- **The cores:** `esp_cpu_stall()`, `esp_cpu_unstall()` and
  `esp_cpu_get_core_id()` are the fake's too, so a project's host build must
  not define its own ("Two cores").
- **The peripheral (T12):** install turns the TWAI's clock on and leaves
  the controller in reset mode until start, as ESP-IDF does; uninstall
  turns the clock off. `twai_fake_set_periph()` sets the clock and reset
  bits to any state the driver did not make, and `twai_fake_set_receiving()`
  a frame being received (RS). A `twai_ll_*` call with the clock off or the
  reset held is counted in `twai_fake_ll_unclocked_calls()`, and a
  `REG_READ()` of any address but the two modelled registers in
  `twai_fake_reg_unknown_reads()`; a healthy run keeps both at zero.
- **Each project's defaults:** the fake defaults to timed air time (each
  frame's unstuffed length at the installed bit timing), automatic
  recovery, an ACKing bus and the C3. gen-inhibit's harness sets a 200 us
  air time; the interposer's `hardReset()` sets instant completion and
  recovery completed by the test, its own fake's defaults.

The interposer's suite on this fake, as a trial on 2026-10-31
(`charge-interposer/main` `5426202`, WSL gcc 9.4, a scratch copy of
`host_bridge/` with its `twai_fake.h`, `twai_fake.cpp` and `driver/twai.h`
removed and `run.sh` given the sources and include path above): the same
transcript, line for line, as on its own fake, its two recorded KNOWN FAILs
included. That is a self-check; the interposer's tester runs the suite that
counts.

## ESP-IDF v5.4.2

Re-read for gen-inhibit's port step 1d (gen-inhibit tester, 2026-11-03),
v5.4.1 against v5.4.2. Byte-identical in v5.4.2: every header `include/`
copies from (`driver/twai.h`, `hal/twai_types.h`, the C3's and the S3's
`hal/twai_ll.h`, the C3's `soc/soc.h`, `soc/reg_base.h` and
`soc/system_reg.h`), `esp_rom_sys.h`, `twai_hal.c`, `twai_hal_iram.c` (the
interrupt decode, bus-off included), both chips' `soc_caps.h` and
`Kconfig.twai`. Two changes:

- **`twai.c`'s TX buffer handler loads no next frame while bus-off**
  (v5.4.2 `twai.c:194-197`): it raises `TX_IDLE`, and the queued frames
  stay, counted in `msgs_to_tx`, until recovery or uninstall discards them.
  The decode sets the bus-off flag before the handler runs, so this is the
  path of a buffer-free event in the bus-off interrupt, the fake's branch a
  (`twai_fake_set_busoff_inflight_fails()`), which under v5.4.1 loaded the
  next frame into the buffer. The fake follows v5.4.2: `complete_buf()`
  raises `TX_IDLE` in bus-off. The case "bus-off branch a, a frame queued"
  and mutant "bus-off loads the next frame (v5.4.1)" cover it.
- **`twai_hal_start_bus_recovery()` clears the TX-buffer-occupied flag**
  (`hal/twai_hal.h`). The fake keeps no such flag apart from its buffer,
  which recovery already discards, so nothing changes.

The default branch, where the frame in the buffer at bus-off stays counted
with no report and recovery discards it, is unchanged. Line numbers cited as
`twai.c:N` are v5.4.1's; from line 196 on, v5.4.2's are N + 2.

## The C3 and the S3

In ESP-IDF v5.4.1 the two chips have the same TWAI driver; the same
registers in `hal/esp32c3/` and `hal/esp32s3/twai_ll.h` (only the clock
enable's and reset's field names differ); the same TWAI capabilities in
`soc_caps.h` (one controller, BRP 2 to 16384, RX status); the same default
clock (APB, 80 MHz); and the same errata options (the four ESP32-only ones
off on both, `TWAI_ERRATA_FIX_LISTEN_ONLY_DOM` on both). So nothing in the
driver is modelled apart. What differs is the evidence: only the C3's
transmit-buffer register behaviour is measured (T8). On the fake's S3
(`twai_fake_set_chip(TWAI_FAKE_CHIP_S3)`) the registers behave as the C3's,
and every `twai_ll_*` call and `REG_READ()` is counted in
`twai_fake_ll_unmeasured_calls()`. The S3's file as shipped makes none; its
measurement build and the host-only switch build make them on purpose,
which the count shows. ESP-IDF v5.4.2's two `twai_ll.h` files were
diffed again for the switch (2026-11-27): only the clock and reset field
names, a copyright line and indentation differ. The one difference the fake
models is the cores: the S3 has two, the C3 one ("Two cores").

## The register-level switch (T14 to T18)

What code outside the driver can do to a running controller through
`hal/twai_ll.h`, read in ESP-IDF v5.4.2's `twai_hal.c` and the S3's
`twai_ll.h` (the C3's is the same):

- **The mode register** (`twai_fake_hw_mode()`): listen-only at install
  (`twai_hal.c:34`), the install's mode at start (`:73`), listen-only at stop
  (`:98`) and at bus-off (`twai_hal_iram.c`, `twai_hal_get_events`).
  `twai_ll_set_mode()` writes it. The driver's own mode, which gates
  `twai_transmit()`, never changes. In listen-only the controller sends
  nothing: a frame in the buffer stays, reading as awaiting arbitration.
- **The error counters:** install writes both 0 (`:36-37`); start writes TEC
  0 and REC 128 in listen-only, the `LISTEN_ONLY_DOM` errata, or 0
  (`:75-87`). `twai_ll_set_rec()` and `twai_ll_set_tec()` write them, and
  every REC value written from outside the driver is logged.
- **Reset mode:** `twai_ll_exit_reset_mode()` leaves a reset mode that
  `twai_ll_enter_reset_mode()` entered.
- **Misuse, counted:** a mode or counter write outside reset mode does
  nothing (`twai_ll.h`: "Must be called in reset mode"); an exit from a reset
  mode the driver keeps (stopped, bus-off) does nothing. A healthy run keeps
  both counts at zero.

Three behaviours are not measured. T19 measures them on the S3, so each is
a knob, and a port can be tested on every choice:

| Knob | Choices (default first) |
|---|---|
| `twai_fake_set_reset_tx()`: reset mode with a frame in the buffer | KEEP: the frame stays with its transmit request cancelled, never sent until an abort removes it; RESUME: sent once reset mode is left; RELEASE: the buffer freed with no transmit interrupt, the driver's TX-buffer-occupied flag and `msgs_to_tx` left set, so later sends queue behind a frame that is not there (`twai_fake_driver_buf_stuck()`) until a stop, uninstall or recovery |
| `twai_fake_set_abort_report()`: an abort during an unacknowledged attempt | the frame ends with the attempt, never on the wire (`twai_ll.h`: no retransmission), and the driver reports TX_SUCCESS (the abort sets TCS, as the C3's abort of an awaiting frame measured) or TX_FAILED |
| `twai_fake_set_ack_errors()` | off: frames retrying for ever count no bus errors, as before; on: every unacknowledged attempt raises one bus error |

Each unacknowledged attempt lasts one air time, back to back, with no error
frame, intermission or suspend between attempts, and TS stays set
throughout. What TS reads between resends is T13's and T19's measurement.
`twai_fake_active_error_flags()` counts active error flags: one per bus
error while the controller is on the bus with both counters below 128, in
listen-only too, which is the errata that REC 128 works around.

## Two cores

On a chip with two cores the restart stop first stalls the other core (T12,
added 2026-11-27). `esp_cpu_stall()`, `esp_cpu_unstall()` and
`esp_cpu_get_core_id()` (`host/esp_cpu.h`) are the fake's.
`twai_fake_set_core()` names the core the code under test runs on.
`twai_fake_set_other_core_sender()` queues a frame through the driver every
given number of microseconds while the other core runs. Time passes for it
in `twai_fake_advance()` and `esp_rom_delay_us()`, so it sends during the
stop's wait. A stall of the calling core, or any stall on the C3, is counted
in `twai_fake_stall_misused()` and does nothing.
`twai_fake_ll_calls_at_stall()` gives the number of TWAI register calls
made before the first stall, so a test can tell whether the stall came
first. `REG_READ()` of `RTC_CNTL_OPTIONS0_REG` and
`RTC_CNTL_SW_CPU_STALL_REG` (`include/soc/rtc_cntl_reg.h`) gives each
stalled core's fields as `esp_cpu_stall()` writes them (C0 = 2, C1 = 0x21).
A stall of a core already stalled is counted in `twai_fake_restalls()`: in
ESP-IDF v5.4.2 it clears and sets the fields again, so the core runs for
about one register write, twice.

The driver's interrupt runs on the core that installed the driver
(`twai_fake_isr_core()`). It cannot run while that core has interrupts off
(`twai_fake_set_interrupts_off()`, as on the restart path) or is stalled.
Meanwhile the controller still finishes or drops the frame in its buffer,
but nothing books it or loads the next: the driver's TX-buffer-occupied
flag stays set, no alert is raised, and `twai_fake_isr_held()` counts the
events held. So a stop that leaves the other core running, with the
interrupt there, sees the next frame loaded after each one ends, and the
reset cuts one; a stop that stalls it first sees nothing more loaded.

## What it rests on

`twai_fake_assumptions()` (the table at the end of `twai_fake.c`) lists
each behaviour with its basis: read in the IDF v5.4.1 source (with line
numbers), measured on the bench (the C3's abort, 2026-09-27), assumed, or
not modelled. Assumed for the restart stop (T12): the clock off before the
first install; registers that read 0 while unclocked or held in reset; what
a reset mode entered from outside the driver does to the frame on the wire
(cut, kept unsent), to reception, to bus-off and to an abort (nothing); and
the status register's RS, BS and ES bits. Assumed for the switch: the three
knobs' defaults above, a write outside reset mode doing nothing, and the
other core's sender. Not modelled: arbitration, error frames, error counters that
climb by themselves, error-passive and warning alerts,
`RECOVERY_IN_PROGRESS`, `ARB_LOST`, dual-filter mode, the ESP32-only errata
workarounds, non-compliant DLCs above 8, and the error counters' changes at
bus-off.

## What changed from each source fake

The source decided each of these:

- **No driver installed:** every call returns `ESP_ERR_INVALID_ARG` (the
  handle check in `twai.c`), not `ESP_ERR_INVALID_STATE` as the header's
  comments say and the interposer's fake returned.
  `twai_fake_wrong_state_calls()` counts both kinds, as the interposer's
  `invalidStateCalls()` did.
- **Alerts:** one is latched only if it is enabled when it occurs; both fakes
  latched everything and masked on read. `twai_reconfigure_alerts()` hands
  back the alerts triggered and not yet read, and clears them; both fakes
  handed back the old mask.
- **The TX queue:** a frame goes straight into the transmit buffer when
  nothing is waiting, so `msgs_to_tx` reaches `tx_queue_len + 1`. A full
  queue waits, then returns `ESP_ERR_TIMEOUT`. gen-inhibit's fake had a fixed
  16; the interposer's never refused.
- **Order of a transmit's checks:** the arguments first, then listen-only
  (`ESP_ERR_NOT_SUPPORTED`), then the state.
- **Bus-off** leaves the RX queue readable. The interposer's fake emptied
  it. Nothing is received while the controller is not running.
- **No ACK:** a normal-mode frame nobody ACKs is retransmitted and never
  completes, unless single-shot. Without air time, the interposer's fake
  booked it failed at once.
- **The receive filter** is applied in single-filter mode (neither fake had
  one).
- **Failed frames** (`twai_fake_fail`) are accepted, take their air time,
  then complete failed. The interposer's fake failed them inside the
  transmit.
- **A blocked receive** returns a waiting frame at once unless the harness
  asks for one frame per turn (gen-inhibit's scheduling, kept as
  `twai_fake_set_receive_yields_first()`).

## gen-inhibit's adoption

gen-inhibit's shim keeps its `fake_twai.h` interface (`ft_*`), its worker
scheduler, its wire invariant and its log, and moves its driver model onto
this one. That change is on gen-inhibit's side, after its re-pin to a
`shared/main` tag that has this folder.
