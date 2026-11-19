# bench_drivers/

The host side of the charge interposer's bench checks: the scripts the bench
suite (`../bench_suite.py:bench`) runs against the X2 and the bench's CAN
adapters, the helpers they import, and the can-fail runner their
`--can-fail` uses. Owner: the charge-interposer tester (harness).

They moved here from reverse-it's
`projects/vtrux/notes/artifacts/interposer-firmware/` folder in port step 0
(reviewer, 2026-10-26; `reset_check.py` and `keepalive_probe.py` from its
`runs/step4_nohands_20261014T1730/`). The SeaDrive copies stay there, with
a pointer, until nothing reads them. Since the move:

- Each script finds its siblings (this folder) and the code
  (`charge-interposer/`: `paths`, `protocol`, `run_scenario`) from its own
  location, never from the working folder.
- Data still comes from SeaDrive through `paths.py` (`REVERSE_IT_DIR`), and
  so does every output written with no `--out`: `paths.fixtures()`, where it
  went before the move, never this tree. The suite always passes `--out`.
- `bench_c12.py` reads the host fake's stored transcript,
  `mcp2515_conformance_fake.txt`, from `paths.fixtures()`.

**The Kvaser on kvaser_usb** (2026-11-13, the charge-interposer reviewer's
rulings A, D1-D6, M6 and the kernel-log addendum): every driver opens the
vehicle segment's Kvaser through `../kvaser_link.py` (netdev `kvaser` by
name; CANlib only in `bench_rate_feasibility.py`, refused on Linux). An
UP link acknowledges with no socket open, so each check sets the link per
its kind and reads it back (ACK checks up, no-ACK checks and phases down);
a sender keeps the stall rule (no own echo 100 ms after send() returned,
or ENOBUFS: VOID "kvaser TX stall", the Kvaser recovered, a failed
recovery stops the bench); and every VOID from a stall, a lost-frame
report or a link read-back names the kernel's USB/CAN lines for its
window. A reader whose check sends busily on another adapter runs in a
process of its own (`rx_proc.py`). Which check changed how is in each
driver's header and in the commits of `charge-interposer/tester/kvaser-socketcan`.

**Every adapter by its canctl role** (2026-11-18, shared/bench SPEC section
8's adapter roles, the user's ruling for the whole bench): each driver asks
`../adapter_roles.py` for the PCAN as `pcanfd` and the charger segment's
Innomaker as `canchg` (libusb mode), and `../kvaser_link.py` opens the
Kvaser on role `kvaser`'s netdev; no driver names a `PCAN_USBBUSn`, an
index, a serial, a netdev or the first adapter found (`../adapter_roles_test.py`'s
scan and its `OPENERS` table). Until then the PCAN was `PCAN_USBBUS1` and
canchg was found by its serial (`adapter_fixes.py`, removed).

| File | What |
|---|---|
| `bench_boot_reset.py` | Spec 9, core dumps (tracker I, the reviewer, 2026-11-18): `--resets` chip resets 0x15 (esptool's USB-Serial/JTAG hard reset) on a port held open and read straight through each reset (`bench_usb_host.HostPort`); each reset VOID if its `rst:0x15` line was not read, FAIL on a panic report or a second ROM reset line after it, or, with `--app-line`, on the image's own boot line missing (the bridge and witness: spec 8.3's banner; the truck: none); the run PASSES only if every reset does. The bench suite's `boot-reset-0x15` in b-bridge, b-witness and b-truck, 50 resets each. `--self-test` (one boot a real pre-fix panic capture) and `--can-fail` (14 mutants); on the bench, a build without the core startup stage's partition table read must FAIL (`bench_boot_reset_fixture.py`) |
| `bench_boot_reset_fixture.py` | `boot-reset-0x15`'s bench can-fail (tracker I step 2, the reviewer's fixture cf2, 2026-11-18). `build --commit C --out DIR`: in a detached worktree `interposer-tester-bootfix` beside this one at C, the fixture (`main/CMakeLists.txt`'s `set(boot boot_partitions.c)` emptied and its `-u intp_boot_partitions_include` link lines removed; `sdkconfig`'s `CONFIG_ESP_COREDUMP_CHECK_BOOT=y`), committed there by the script and its diff checked to be exactly those edits before `vtrux_tools.py build bridge`; the map must name no `intp_read_partition_table` and `sdkconfig.json` have `ESP_COREDUMP_ENABLE_TO_FLASH` and `_CHECK_BOOT` true; the image copied to `DIR/image/`, the worktree removed. Linux: build.slice, an exported IDF, a clean harness tree. `flash --image DIR --port P --out DIR`: esptool (`VTRUX_ESPTOOL_ARGV`) erases the image's coredump partition (from its partition table) and writes its `flasher_args.json` files, each verified; any image of that layout. `canfail` the same, then `bench_boot_reset.py` with the banner as app line: SHOWN only on a FAIL with a reset failing on a panic report. Bench parts: Linux bench.slice; the lock is the caller's; the X2 is left on the image flashed. `--self-test` and `--can-fail` (19 mutants) |
| `bench_coredump_readback.py` | Spec 9, core dumps: check `coredump-readback`, a crash's dump read off over USB (the reviewer's follow-on to tracker I step 2, 2026-11-18). `build --commit C --out DIR`: in a detached worktree `interposer-tester-crashfix` beside this one, the product bridge of C and three fixtures, each C plus a block in `main/main.cpp` before `setup()` and a call as its first statement, committed there by the script and its diff checked: on any boot but a panic restart a task waits 5 s and crashes, `crash-a` in `intp_fixture_crash_abort()` (`abort()`), `crash-b` in `intp_fixture_crash_trap()` (`__builtin_trap()`), `crash-b-keep` as `crash-b` with `CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE=y`; each built fresh, its map naming its crash function and not the other, `sdkconfig.json` checked; images to `DIR/image-<name>/`. `run --images DIR --port P --out DIR`: `read` (crash-a's dump read off over USB with esp-coredump against its ELF: the crashed task the fixture's, its backtrace naming the function), `keep` (the product written over it without touching the coredump partition, `--resets` chip resets with `bench_boot_reset.py`, the partition's raw bytes unchanged and the dump read again), `replace` (crash-b over it, read against its own ELF, the bytes changed); the X2 left on the product with the partition erased. `canfail`: crash-b-keep over crash-a's dump, and the partition erased across the resets: `read`, `keep` and `replace` must each FAIL. Bench parts: Linux bench.slice, an exported IDF; the lock is the caller's. `--self-test` and `--can-fail` (32 mutants) |
| `bench_c12.py` | C12 and the rest of the MCP2515 conformance sequence on silicon, against the fake's transcript |
| `bench_canbench_smoke.py` | The can-tools pin on the bench: pcanfd, kvaser and canchg opened by role through `bus.open_bus` with canbench from the pinned commit (each judged on the role it opened and where canctl's table resolved it, since 2026-11-18: the role opens' bench check), the refusal with `CAN_TOOLS_DIR` unset, the four diag pages heard with the charger up, and the X2's build id from 0x7F7; run by hand under a hand take (2026-11-01; a scratch script at the 2026-10-30 cutover) |
| `bench_charger_silent.py` | Spec 2.2 on the bench, the single-adapter variant. The held frame and the reported return burst use the Innomaker's device stamps unwrapped (`bench_usb_host.unwrap_us32`, 2026-11-12); `--can-fail` since then |
| `bench_charger_witness.py` | The charger-side wire witness, and the `Tee` and Innomaker helpers the other drivers import |
| `bench_flight_silence.py` | Spec 8.3 flight silence on the truck image; with `--idf-log`, spec 8.3's "framework logging is compiled out of every build" on any image: only an application line in ESP-IDF's or the Arduino core's log form fails (2a acceptance, 2026-11-04). The bench suite runs it as `flight-silence` in b-truck (the default verdict: no application byte at all) and with `--idf-log` as `idf-log` in b-bridge and b-witness |
| `bench_hold12.py` | Spec 12 entry 2: the same-identifier hold on silicon. The filler's echoes are sorted by the Innomaker's device stamps unwrapped (`filler_echoes`, 2026-11-12) |
| `bench_innomaker_clock.py` | The Innomaker's own clock: device-timestamped echoes and rate runs |
| `bench_layer_check.py` | The hand-take bench check of the harness's use of can-tools' bench-role layer (2026-11-19, the reviewer's bench-layer item (6)): the guard passes in this process and refuses (NotHeld, not an ancestor) in a systemd --user service outside the holder's tree; `kvaser_link.set_link` down and up through the layer, read back in sysfs and `ip` (500000); the Kvaser, the PCAN (listen-only) and canchg opened through the harness's openers and shut; `canchg_mode.start` / `end` (to_libusb, canchg's open changing nothing, to_socketcan: gs_usb, a netdev, link down); `kvaser_link.recover`; canchg's mode and the Kvaser's link put back as found. Transmits nothing; touches neither board. `--out DIR` under a hand take; `--self-test` (the judge on made-up observations), `--can-fail` |
| `bench_mcp_reset.py` | Whether the X2's MCP2515 keeps ACKing on the charger segment while the ESP32 is down. Its ROM holds run the core flasher's esptool, the settings' `esptool.argv` passed as `VTRUX_ESPTOOL_ARGV` (2026-11-12); unset, PlatformIO's esptool.py on Windows as before, refused on Linux |
| `bench_pacer.py` | The pacer every driver that offers frames at a stated rate uses |
| `bench_powerup_round.py` | Spec 2.3's power-up round on the board; also run by `../bench_identify.py` to read 0x7F7. On Linux the no-ACKer boot's 16 leading error-active attempts are counted from the witness's timing (first error row to first valid row, over one attempt's length in bit times), since PEAK's Linux driver passes on one error row for the 16; the error-row count is shown, the board's total is unchanged, and `--lead-timing` prints the count for saved witness files (2026-11-18, the reviewer's ruling on 8b8e) |
| `bench_rate_feasibility.py` | Whether this host can offer the rates spec 12 entry 2 asks for; imported for its rate helpers (`open_gs_usb`). **CANlib-only** (the reviewer's E, 2026-11-13): its Kvaser witness judges rates on CANlib's device stamps, so on Linux, where the Kvaser is the kernel's kvaser_usb with software stamps, `open_witness` refuses; not ported, hand-run only. Since 2026-11-18 the witness is role kvaser on CANlib (`adapter_roles.kvaser_canlib`, linuxcan mode), never canlib channel 0, and roles exist only on the bench host, so with E's refusal on Linux it opens nowhere until it is ported |
| `bench_rx_order.py` | Step 4 item 4: receive order on silicon, charger to vehicle. The reported echo span per burst uses the Innomaker's device stamps unwrapped (`echo_spans`, 2026-11-12); `--can-fail` since then |
| `bench_switch_errors.py` | Spec 2.3: vehicle-side bus errors from a mode switch with an acknowledger present |
| `bench_switch_loss.py` | Spec 2 / 2.3: frames lost when the vehicle port switches mode |
| `bench_switch_pending.py` | Spec 2 / 2.3: a pending vehicle-bound frame at a switch to listen-only |
| `bench_topology.py` | Which interposer segment each adapter is on, separated, not guessed. Its drains count error frames by can-tools' classifier (`adapter_roles.bus_error`, 2026-11-19, the reviewer's bench-layer item): on SocketCAN, a Kvaser in a `WatchedBus` included, a controller-state or counters-only frame is `not_bus`, not an error; elsewhere every error frame counts |
| `bench_usb_host.py` | Spec 8.3: output a USB host is not reading is dropped, never held. Its `unwrap_us32` (gs_usb's 32-bit microsecond stamp, which wraps every 71.6 min) is the one `bench_charger_silent`, `bench_hold12` and `bench_rx_order` import (2026-11-12). close-boots on Linux sends esptool's hard reset and closes the port at once, since there the close does not reset the board; Windows keeps the close alone (2026-11-18, the reviewer's ruling (a) on 8b8e) |
| `bench_vport.py` | Spec 9's vehicle-port measurements, with the vport_bench image |
| `klog_windows.py` | The kernel's USB and CAN lines (journalctl -k) over every judged hardware check's window of a run record (the bench suite's runs, vp and bench, and a chain's row for one of them; never the emulated or harness suites, whose loop has no dongle: the reviewer, 2026-11-16, at the user's correction), each result beside a -71, an EPROTO or a USB disconnect FLAGGED (the reviewer, 2026-11-13, after CAN replay's bench advisory: both bench hubs bus-powered), and from 2026-11-16 beside a port "disabled by hub" or an "error -32" (run e79d's WiCAN failure lines); the lines are `kvaser_link.KLOG_RE`'s, since the hub swap every device under the bench hub 1-3 by any driver and the bench's devices by ID (the reviewer, 2026-11-16); an entry-time scan, re-runnable since madhouse's journal is persistent; judges nothing. `python3.13 -B klog_windows.py RUN_DIR [--out FILE]`; `--self-test`, `--can-fail` |
| `rx_proc.py` | A CAN reader in a process of its own, for a check that reads one adapter while it sends busily on another (the reviewer's M6, 2026-11-13: reader threads beside a busy sender lost frames); `witness_void` (a witness that did not open, raised recv errors, or for which the kernel reports lost frames voids the check, with the kernel's lines) and `lost_lines`. An error frame is a row only when can-tools' classifier counts it a bus error (`adapter_roles.bus_error`, 2026-11-19, the reviewer's bench-layer item: on SocketCAN not a controller-state change, bus-off, restarted or the counters alone, which are counted in `not_bus_errors`; on any other backend every error frame, as before); `--self-test`, `--can-fail` |
| `reset_check.py` | Step 4 item 5: boot-mid-session's board reset verified on the bench; `--serial-log` names its serial log. Since 2026-11-18 the X2's port comes from `--port`, required (was COM3 in the code; the bench suite passes `{serial_port}`). Since 2026-11-19 it exits 1, with a FAIL line, on `RESET PROVEN: NO` (it exited 0 either way before, so the suite's reset-check could not fail); `--self-test` (the port, and the verdict on stand-in rows and frames), `--can-fail` |
| `keepalive_probe.py` | Step 4 item 3: where the rx-order keep-alive goes, recorded. Since 2026-11-13 the port comes from `--port` (was COM3), the Kvaser's link is brought up and read back first; since 2026-11-18 the PCAN is role pcanfd and `--pcan-channel` (2026-11-13 to 2026-11-18; PCAN_USBBUS1 before) is refused; `--self-test`, `--can-fail` |
| `script_can_fail.py` | Runs a driver's self-test against each of its `MUTANTS`, from a copy in a `scratch_dir` run folder (removed in a finally; before 2026-10-31, the machine temp folder) |
| `script_can_fail_test.py` | Test for `script_can_fail.py` |
