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
fakes disagreed with each other or with the source, the source decides.

    make -C shared/twai_port/test run        # the fake's tests, and the port on it (C3 and S3)
    make -C shared/twai_port/test t12-standins  # the T12 cases fail on each broken restart stop
    make -C shared/twai_port/test mutants    # every mutant of the fake must fail its tests
    make -C shared/twai_port/test mutate-test  # mutate.py removes its scratch folder, pass or fail
    make -C shared/twai_port/test cxx        # the fake and cpp/ compiled as C++11
    make -C shared/twai_port/test cxx-test   # cpp/'s own tests

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
| `twai_fake.h`, `twai_fake.c` | the model and the driver's entry points: `twai_driver_install` to `twai_clear_receive_queue`, the six register calls of `hal/twai_ll.h`, `REG_READ` of the TWAI's clock and reset bits, and `esp_rom_delay_us`. C in the common subset of C11 and C++11 |
| `include/driver/twai.h`, `include/hal/twai_ll.h` | the driver's headers for host builds, from ESP-IDF v5.4.1's own (`driver/twai.h`, `hal/twai_types.h`, the C3's `hal/twai_ll.h`): the real types, field order, alert values and initialisers. The `_v2` calls are left out, so a call the fake does not implement fails to link |
| `include/soc/soc.h`, `include/soc/reg_base.h`, `include/soc/system_reg.h` | `BIT()`, `REG_READ()` and the TWAI's clock-enable and reset bits of `SYSTEM_PERIP_CLK_EN0_REG` and `SYSTEM_PERIP_RST_EN0_REG`, ESP-IDF v5.4.1's addresses and bits, for the restart stop (T12). `REG_READ()` is a call into the fake |
| `host/` | minimal stand-ins for `esp_err.h`, `esp_rom_sys.h`, `freertos/FreeRTOS.h`, `freertos/task.h`, `driver/gpio.h`, `esp_attr.h`, `esp_log.h` and `board.h`, for a build with none of its own. A project's own come first on its include path |
| `cpp/twai_fake.h`, `cpp/twai_fake.cpp` | the interposer's C++ interface to the fake: `namespace twaifake`, `TwaiFake`, `fake()`, as its host_bridge tests call them, each member a call into the model. Its clock adapter is the interposer's `hostclock::micros()` |
| `cpp/test_cpp_wrapper.cpp` | the interface's own tests, for the members that do more than pass their arguments on (`setBusOffAfterSent`); `cpp/README.md` |
| `test_twai_fake.c` | the fake's own tests: 29 cases, each a driver or register behaviour from the source, or one of the assumptions the table names |
| `mutate.py` | 53 mutants of `twai_fake.c`, one behaviour each; the run passes only if the unmutated build passes and every mutant fails |
| `test_mutate.py` | `mutate.py`'s removal of its scratch folder, with the builds stood in for (no compiler): removed after a pass, a fail and an exception, read-only files and paths past 260 characters included; a folder it cannot remove printed with the reason; an existing `--out` refused and left as it was; with no `--out`, a new `<time>_<pid>` folder, which a killed run's folder does not stop. `--mutants` breaks these twelve ways, each of which it must fail by a check (three only fail on Windows) |
| `test_port_on_fake.c`, `host_platform.c` | the real `twai_port.c`, compiled as shipped, on the fake: built with the C3's register file on the fake's C3 and with the unsupported one on its S3, 14 cases from T2 to T9 each, one process per case. For T12, the restart stop, 11 more on the C3: an idle bus (reset mode at once, the report filled, nothing logged, and nothing takes it out of reset mode until the next install), a frame on the wire and one being received let finish, polled every 2 us, a reception and a transmission outlasting 1 ms (the wait runs out at 1 ms, reset mode anyway, the frame cut), a frame awaiting arbitration aborted, the clock off and the reset held (touches nothing, the report 0 but those bits), the driver running with the port never set up, bus-off (already in reset mode), and a NULL report; and 1 on the S3 (not supported, touching nothing). `host_platform.c` is the board, tick and log calls the port makes |
| `t12_standin.c` | the T12 cases' can-fail: the tester's own restart stop, written from T12 and the fake's register calls. Built with `-DT12_STANDIN`, the T12 cases call it in place of the port's. As written it passes them all; `T12_BROKEN` breaks one rule (9 ways: no wait, no bound, no reset on timeout, no abort, the clock or the reset ignored, 10 us steps, the status or the error counters not reported), and `t12-standins` passes only if each is caught by a check (exit 1 with a FAIL line), never by a sanitizer |
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
  it only counts (`twai_fake_delay_calls()`).
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
`twai_fake_ll_unmeasured_calls()`, which a healthy S3 run keeps at zero; the
port's S3 build makes none.

## What it rests on

`twai_fake_assumptions()` (the table at the end of `twai_fake.c`) lists
each behaviour with its basis: read in the IDF v5.4.1 source (with line
numbers), measured on the bench (the C3's abort, 2026-09-27), assumed, or
not modelled. Assumed for the restart stop (T12): the clock off before the
first install; registers that read 0 while unclocked or held in reset; what
a reset mode entered from outside the driver does to the frame on the wire
(cut, kept unsent), to reception, to bus-off and to an abort (nothing); and
the status register's RS, BS and ES bits. Not modelled: arbitration, error frames, error counters that
climb by themselves, error-passive and warning alerts,
`RECOVERY_IN_PROGRESS`, `ARB_LOST`, dual-filter mode, the ESP32-only errata
workarounds, and non-compliant DLCs above 8.

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
