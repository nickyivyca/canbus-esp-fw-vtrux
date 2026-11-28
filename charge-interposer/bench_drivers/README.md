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
name; CANlib only in `bench_rate_feasibility.py`, refused on Linux, until
it was retired on 2026-11-23). An
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

**A reader that dies is not a product result** (2026-11-19, the
charge-interposer reviewer's PCAN ruling 1): python-can's PcanBus.recv
raises on most PCAN statuses (error passive, bus-off, OVERRUN, QOVERRUN),
and an unguarded reader thread then stopped while its check judged the
short row list it left. Every bench reader that judged or counted a PCAN's
rows runs its loop under `reader_death.py`'s guard, which keeps the first
exception's type, text and time; the boot or phase whose window the reader
covered is VOID with it (a FAIL from a boot or phase whose reader lived
still stands): `bench_powerup_round`'s witness, `bench_usb_host.Rx` (its
not-reading and close boots, and `bench_switch_pending`,
`bench_switch_errors`, `bench_flight_silence` and `bench_mcp_reset`),
`reset_check` (RESET PROVEN: NOT-PROVEN with the reason), `keepalive_probe`
and `bench_rate_feasibility`'s drains and witness (until its retirement,
2026-11-23). `bench_switch_errors`
judged a dead `Rx` a TEST FAULT, counted FAIL, until the reviewer ruled
it VOID too (2026-11-19).

**The reader guard is can-tools'** (2026-11-23, the reviewer's
reader_guard ruling; can-tools f19d04b): `reader_death.py`'s guard and
screen went into `canbench.reader_guard` under the shared-first rule, and
every reader now runs on it: `read_loop` with `drain=False` passed
explicitly (no reader drained before; the library's default drains after
stop) in `bench_powerup_round`'s witness, `bench_usb_host.Rx`,
`keepalive_probe`, `reset_check`, `bench_hold12`'s sender drain and
`../diag_cadence.py`'s `DiagWitness`; `guard` alone in `bench_topology`'s
drains, which keep their own screen step, since `reader_guard.screen`
classifies the bus it is given, not a `WatchedBus`'s `.inner` (since the
echo_watch move below the Kvaser's drain reads its bus itself, its frames
passing `saw()` first, and keeps the same step). What stays
in `reader_death.py` is ours: `Death` (canbench's `ReaderRecord` with our
VOID line), `DIED`, the drain check each driver's self-test runs on its own
source (`undrained`, `drain_check`), and the self-tests' stand-ins.
`reader_guard.screen` reads `is_error_frame` directly, so a stand-in
message carries it. A reader's error text on a missed-frames row reads
`MissedFrames: ...` (it read `LostFrames: ...`), with the same
"reports <why>" part.

**The stall rule's bookkeeping is can-tools'** (2026-11-23, the reviewer's
echo_watch ruling; can-tools f19d04b): `../kvaser_link.py`'s `EchoWatch`
and `WatchedBus` are thin wrappers over `canbench.echo_watch`, with the
bound `kvaser_link.STALL_S` (100 ms) passed in and canbench's
`SendRefused` / `EchoMissing` raised as our `kvaser_link.Stall`, its text
and time as before, so every VOID "kvaser TX stall" line reads as it did.
The drivers that send and read the Kvaser on one thread keep the default
(`read_after_send=True`: the watch also reads after every send):
`bench_usb_host`'s injector, `bench_hold12`'s `kvaser_send`,
`bench_vport`'s node and `../vehicle_sim.py` (through `bus.open_kvaser`'s
`WatchedBus`); their code is unchanged. The two whose own reader thread
reads the Kvaser while another thread sends pass `read_after_send=False`
(the watch never reads): `bench_charger_silent`'s `vehicle_reader` and
`bench_topology`'s Kvaser drain each hand every frame read to
`watch.saw()` first (an own echo is the stall rule's, never a frame heard)
and call `watch.check()` after every read, so the 100 ms bound is judged
in the reader as `WatchedBus.recv()` judged it; neither calls `finish()`,
as before.

**The pacer is can-tools'** (2026-11-23, the reviewer's pace ruling;
can-tools f19d04b): `bench_pacer.py`'s schedule went into
`canbench.replay.pace` under the shared-first rule, and every driver that
offers frames at a rate (`bench_hold12`'s three senders,
`bench_switch_loss`'s filler, `bench_innomaker_clock`, `bench_charger_witness`)
calls `bench_offer.offer`, which runs `pace` and keeps what pace leaves to
its caller. The end stays by time, as `bench_pacer`'s was: a stop Event set
by a timer at t0 + seconds (pace's own `until_s` is by schedule offset, so a
sender running late would send every frame and end later, moving
`bench_hold12`'s judged window end t1, `innomaker-rate-runs`' count against
0.95 x rate x seconds and `bench_switch_loss`'s cycle stops), with the
schedule ending when asked for a pass at or after the end and a pass called
at or after it not sent, since pace checks its stop before it waits and the
timer's thread can run late. The counts (sent, errs, the first error's
repr) and the sender gaps are `bench_offer`'s; the lag and catch-up are
pace's (catch-up now pace's count of passes already due, where
`bench_pacer` counted nearly every pass's frames). canbench is loaded
before a sender's go or ready (its first load takes about 0.2 s). Retired
with `bench_pacer.py`: `bench_rate_feasibility.py` (CANlib-only, refused on
Linux, the bench is Linux); its `open_gs_usb`, the one helper other
drivers imported from it, was the same opener as
`bench_charger_witness.open_gs_usb` (role canchg), which they import now.
Each driver this touched prints a VOID line where its premise failed (the
X2's port, the Innomaker, the filler's process not ready, a device not in
hardware-timestamp mode) and before ended in a traceback (the reviewer's
sweep ruling on premise failures).

**Bus states reported, lost frames VOID** (2026-11-20, the reviewer's PCAN
item 2, can-tools 262ba7b9): the PCAN readers open the PCAN with
`bus_states="report"` (`adapter_roles.pcanfd_report`: can-tools'
CanbenchPcanBus, its bus states, status messages and overruns handed up as
Linux-layout error frames, never raised): `bench_powerup_round`'s witness,
`bench_usb_host.open_pcan` (so `Rx` and `rx_proc`'s readers on it),
`reset_check`, `keepalive_probe`, `bench_rate_feasibility` (until its
retirement, 2026-11-23), `bench_hold12`'s
PCAN sender and its drain, and `bench_topology`; `bench_layer_check` opens
it once each way; `bench_vport`'s witness stays on python-can's default
opener, since its fast reader calls PCAN-Basic's `CAN_Read` itself. From
2026-11-20 (the reviewer's ruling on the 3-bench-rerun5 draft) that reader
decodes a bus state Read returns, and a status message, with can-tools'
`errors.from_pcan_status`, `lost_frames` and `decode` (`pcan_state`):
frames lost or bus-off is a receive failure, as before; any other state is
counted by its text per window and is no failure. Until then, on Linux,
where PCAN-Basic returns status messages as Read results, each
error-passive report faulted its window as a lost frame, and each status
message the library returned as a message was written as a data frame.
The PCAN's own status string, printed at each wrong-rate window's end, is
its GetStatus word with every bit by name (`pcan_status_text`), not
python-can's `status_string`, which gives nothing for a combination such
as error passive with bus heavy.
Each reader passes what it reads through `canbench.reader_guard.screen`
(`reader_death.screen` until 2026-11-23): a frame or
a bus error (can-tools' classifier, `bench_layer.bus_error`) is a row; a
state row (an error frame that is no bus error) is not, and is counted; a
row saying the reader missed frames (`reader_guard.missed`: frames lost,
can-tools' `errors.lost_frames`; or, from the reviewer's ruling of
2026-11-20, a bus-off or the restart after it, `errors.decode`'s state,
since the reader reads nothing while off) stops the reader as
`MissedFrames` (`LostFrames` until 2026-11-23), and its window is VOID with
"lost frames" and the row's reason. can-tools' `canbench_busoff_reset` stays on, so the reader
recovers. `rx_proc` keeps such rows in `lost_frames`, and
`witness_void` VOIDs on them; `bench_topology`'s drains and `bench_hold12`'s
sender drain VOID their run the same way.

| File | What |
|---|---|
| `bench_boot_reset.py` | Spec 9, core dumps (tracker I, the reviewer, 2026-11-18): `--resets` chip resets 0x15 (esptool's USB-Serial/JTAG hard reset) on a port held open and read straight through each reset (`bench_usb_host.HostPort`); each reset VOID if its `rst:0x15` line was not read, FAIL on a panic report or a second ROM reset line after it, or, with `--app-line`, on the image's own boot line missing (the bridge and witness: spec 8.3's banner; the truck: none); the run PASSES only if every reset does. The bench suite's `boot-reset-0x15` in b-bridge, b-witness and b-truck, 50 resets each. `--self-test` (one boot a real pre-fix panic capture) and `--can-fail` (14 mutants); on the bench, a build without the core startup stage's partition table read must FAIL (`bench_boot_reset_fixture.py`) |
| `bench_boot_reset_fixture.py` | `boot-reset-0x15`'s bench can-fail (tracker I step 2, the reviewer's fixture cf2, 2026-11-18). `build --commit C --out DIR`: in a detached worktree `interposer-tester-bootfix` beside this one at C, the fixture (`main/CMakeLists.txt`'s `set(boot boot_partitions.c)` emptied and its `-u intp_boot_partitions_include` link lines removed; `sdkconfig`'s `CONFIG_ESP_COREDUMP_CHECK_BOOT=y`), committed there by the script and its diff checked to be exactly those edits before `vtrux_tools.py build bridge`; the map must name no `intp_read_partition_table` and `sdkconfig.json` have `ESP_COREDUMP_ENABLE_TO_FLASH` and `_CHECK_BOOT` true; the image copied to `DIR/image/`, the worktree removed. Linux: build.slice, an exported IDF, a clean harness tree. `flash --image DIR --port P --out DIR`: esptool (`VTRUX_ESPTOOL_ARGV`) erases the image's coredump partition (from its partition table) and writes its `flasher_args.json` files, each verified; any image of that layout. `canfail` the same, then `bench_boot_reset.py` with the banner as app line: SHOWN only on a FAIL with a reset failing on a panic report. Bench parts: Linux bench.slice; the lock is the caller's; the X2 is left on the image flashed. `--self-test` and `--can-fail` (19 mutants) |
| `bench_coredump_readback.py` | Spec 9, core dumps: check `coredump-readback`, a crash's dump read off over USB (the reviewer's follow-on to tracker I step 2, 2026-11-18). `build --commit C --out DIR`: in a detached worktree `interposer-tester-crashfix` beside this one, the product bridge of C and three fixtures, each C plus a block in `main/main.cpp` before `setup()` and a call as its first statement, committed there by the script and its diff checked: on any boot but a panic restart a task waits 5 s and crashes, `crash-a` in `intp_fixture_crash_abort()` (`abort()`), `crash-b` in `intp_fixture_crash_trap()` (`__builtin_trap()`), `crash-b-keep` as `crash-b` with `CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE=y`; each built fresh, its map naming its crash function and not the other, `sdkconfig.json` checked; images to `DIR/image-<name>/`. `run --images DIR --port P --out DIR`: `read` (crash-a's dump read off over USB with esp-coredump against its ELF: the crashed task the fixture's, its backtrace naming the function), `keep` (the product written over it without touching the coredump partition, `--resets` chip resets with `bench_boot_reset.py`, the partition's raw bytes unchanged and the dump read again), `replace` (crash-b over it, read against its own ELF, the bytes changed); the X2 left on the product with the partition erased. `canfail`: crash-b-keep over crash-a's dump, and the partition erased across the resets: `read`, `keep` and `replace` must each FAIL. Bench parts: Linux bench.slice, an exported IDF; the lock is the caller's. `--self-test` and `--can-fail` (32 mutants) |
| `bench_c12.py` | C12 and the rest of the MCP2515 conformance sequence on silicon, against the fake's transcript. `--step C9` (the suite's `c9-noack`) with the board told it cannot withhold ACK (CANWITHHOLD answered @NO) prints a VOID line and exits 3: the firmware skips C9 and still ends ok, and until 2026-11-21 the run exited 0 (`c9_void`, the reviewer's sweep) |
| `bench_canbench_smoke.py` | The can-tools pin on the bench: pcanfd, kvaser and canchg opened by role through `bus.open_bus` with canbench from the pinned commit (each judged on the role it opened and where canctl's table resolved it, since 2026-11-18: the role opens' bench check), the refusal with `CAN_TOOLS_DIR` unset, the four diag pages heard with the charger up, and the X2's build id from 0x7F7; run by hand under a hand take (2026-11-01; a scratch script at the 2026-10-30 cutover) |
| `bench_charger_silent.py` | Spec 2.2 on the bench, the single-adapter variant. The held frame and the reported return burst use the Innomaker's device stamps unwrapped (`bench_usb_host.unwrap_us32`, 2026-11-12); `--can-fail` since then. Vehicle-side reader errors leave the CAN cross-check incomplete: a VOID line and a VOID verdict, a FAIL kept (`reader_void`; until 2026-11-21 a note and exit 0, the reviewer's sweep). From 2026-11-23 (the reviewer's echo_watch ruling) the Kvaser is a plain bus with a `kvaser_link.EchoWatch` over `canbench.echo_watch`, `read_after_send=False` (a `WatchedBus` before): the tagger and the page-00 burst send through the watch, and `vehicle_reader`, the one thread reading the Kvaser, hands each frame to `watch.saw()` and calls `watch.check()` after every read (self-test on a stand-in Kvaser: echoes taken, a lost echo a stall from the reader's check; can-fails: the saw() or the check() dropped, the stall not kept, the watch reading after each send, the tagger past the watch); the Kvaser, the X2's port or the Innomaker not opening prints a VOID line (`premise_line`; before, a traceback: the reviewer's sweep ruling on premise failures). `--self-test`, `--can-fail` (18 mutants) |
| `bench_charger_witness.py` | The charger-side wire witness, and the `Tee` and Innomaker helpers the other drivers import (`open_gs_usb`, role canchg: since 2026-11-23 also `bench_hold12`'s and `bench_switch_loss`'s fillers and `bench_switch_errors`' and `bench_switch_pending`'s Innomaker, which took `bench_rate_feasibility`'s copy of it until its retirement). From 2026-11-23 (the reviewer's pace ruling) its `offer` offers through `bench_offer` (ended by time; `bench_pacer` before), its witness reset first, `flush_s` after; an Innomaker that does not open prints a VOID line (`premise_line`; before, a FAILED line, exit 2 kept). `--self-test` (`offer` on a stand-in bus and witness: the reset before the first send, the filler frame, the count on time and running late, whole bursts of 8, `queued_fps`; the VOID line) and `--can-fail` (5 mutants) since 2026-11-23; the measurement is hand-run |
| `bench_flight_silence.py` | Spec 8.3 flight silence on the truck image; with `--idf-log`, spec 8.3's "framework logging is compiled out of every build" on any image: only an application line in ESP-IDF's or the Arduino core's log form fails (2a acceptance, 2026-11-04). The bench suite runs it as `flight-silence` in b-truck (the default verdict: no application byte at all) and with `--idf-log` as `idf-log` in b-bridge and b-witness |
| `bench_hold12.py` | Spec 12 entry 2: the same-identifier hold on silicon. The filler's echoes are sorted by the Innomaker's device stamps unwrapped (`filler_echoes`, 2026-11-12). Its Kvaser sender takes no error frames and reads its socket after every send, a refused one too (`kvaser_send`; 2026-11-20, the reviewer's Q2). The PCAN sender's drain (`proc_identifier`) is `canbench.reader_guard.read_loop`, `drain=False` (2026-11-23, the reviewer's reader_guard ruling). From 2026-11-23 (the reviewer's pace ruling) its three senders (`proc_identifier`, `proc_identifier_kvaser`, `proc_filler`) offer through `bench_offer` (can-tools' pace, ended by time; `bench_pacer` before), so the identifier's t1, the judged run window's end, stays at its offer's time however late the sends run; its sender gaps are the offer's `sender_gaps_ms` (`_sender_gaps_ms` before); canbench is loaded before each sender's go or ready; the filler's Innomaker is `bench_charger_witness.open_gs_usb` (`bench_rate_feasibility`'s, the same opener, before). The X2's port not opening, or the filler's process not ready within 30 s (its Innomaker not opening), prints a VOID line, not run (`premise_line`; before, a traceback). Self-test: `proc_identifier` on a stand-in PCAN for 0.3 s, on time (its window, sender gaps) and running late (the window ends at the offer's time); `--can-fail` (24 mutants), among them the identifier's offer by schedule alone (`until_s`) and canbench loaded after go |
| `bench_innomaker_clock.py` | The Innomaker's own clock: device-timestamped echoes and rate runs. From 2026-11-23 (the reviewer's pace ruling) `offer` offers through `bench_offer` (can-tools' pace, ended by time; `bench_pacer` before), so rate-runs' >= 95 % stays held against what its seconds offered, and its result prints as its figures (`bench_pacer`'s printed as an object); the Innomaker not opening, a device not in hardware-timestamp mode or stamps not usable each print a VOID line after any REFUSED line, exit 1 kept (`open_or_void`, `premise_line`; before, a traceback or a REFUSED line alone, read FAIL: the reviewer's sweep ruling on premise failures). `--self-test`; `--can-fail` (9 mutants) since 2026-11-23 |
| `bench_layer_check.py` | The hand-take bench check of the harness's use of can-tools' bench-role layer (2026-11-19, the reviewer's bench-layer item (6)): the guard passes in this process and refuses (NotHeld, not an ancestor) in a systemd --user service outside the holder's tree; `kvaser_link.set_link` down and up through the layer, read back in sysfs and `ip` (500000); the Kvaser, the PCAN (listen-only) and canchg opened through the harness's openers and shut; `canchg_mode.start` / `end` (to_libusb, canchg's open changing nothing, to_socketcan: gs_usb, a netdev, link down); `kvaser_link.recover`; canchg's mode and the Kvaser's link put back as found; from 2026-11-20 (PCAN item 2) the PCAN opened both ways, `pcanfd` (python-can's PcanBus) and `pcanfd_report` (can-tools' CanbenchPcanBus), each judged on the class it returns. Transmits nothing; touches neither board. `--out DIR` under a hand take; `--self-test` (the judge on made-up observations), `--can-fail` |
| `bench_mcp_reset.py` | Whether the X2's MCP2515 keeps ACKing on the charger segment while the ESP32 is down. Its ROM holds run the core flasher's esptool, the settings' `esptool.argv` passed as `VTRUX_ESPTOOL_ARGV` (2026-11-12); unset, PlatformIO's esptool.py on Windows as before, refused on Linux. From 2026-11-21: `run` takes `--build`, the loaded image's build id each boot's 0x7F7 must read (the suite passes the bridge image's; it was fixed at 0x756D6714), prints a VOID line for each boot whose premise failed and exits 3 (it exited 0 on every outcome, so 0d0d, b467, 40e6 and 69d8 recorded PASS on TEST FAULT 8), and releases each hold with `--before default_reset --after hard_reset` (with `--before no_reset` the X2 stayed in the serial loader on Linux) |
| `bench_offer.py` | Frames offered at a stated rate for a stated time, for every driver that offers frames at a rate (2026-11-23, the reviewer's pace ruling; replaces `bench_pacer.py`, 2026-10-05 to 2026-11-23, retired): `offer(send, rate, seconds, burst=1, seq_writer=None)` runs can-tools' `canbench.replay.pace` (pinned f19d04b, loaded by file through `can_tools_pin`, before t0: `rp()`), one pace item a pass of `burst` frames, `"hybrid"` wait. The end is by time, as `bench_pacer`'s: a stop Event set by a timer at t0 + seconds, the schedule ending when asked for a pass at or after the end, and a pass pace calls at or after the end not sent (pace checks its stop before it waits; the timer's thread can run late). `Offered`: `sent`, `errs`, `first_err` (the first error's repr), `sender_gaps_ms` (one a pass, the first from t0) are its own; `elapsed`, `max_lag_ms`, `catchup` (pace's: passes already due when the previous returned) and `ended` are pace's; `queued_fps` is sent over elapsed, a queue-accept rate. `--self-test` (a fake clock: the end on time and late, no lag or catch-up counted past the end, bursts, sequence numbers, errors, the timer's stop, a slow canbench load before t0; the real clock: 2,000 fps, a late sender), `--can-fail` (18 mutants) |
| `bench_powerup_round.py` | Spec 2.3's power-up round on the board; also run by `../bench_identify.py` to read 0x7F7. On Linux the no-ACKer boot's 16 leading error-active attempts are counted from the witness's timing (first error row to first valid row, over one attempt's length in bit times), since PEAK's Linux driver passes on one error row for the 16; the error-row count is shown, the board's total is unchanged, and `--lead-timing` prints the count for saved witness files (2026-11-18, the reviewer's ruling on 8b8e). From 2026-11-23 the witness reads with `canbench.reader_guard.read_loop` (`drain=False`; the reviewer's reader_guard ruling), and a boot whose witness or serial port does not open is a VOID line, not run (`NOT_OPENED`, exit 1 with no FAIL line; before, a traceback, read FAIL: the reviewer's sweep ruling on premise failures) |
| `bench_rx_order.py` | Step 4 item 4: receive order on silicon, charger to vehicle. The reported echo span per burst uses the Innomaker's device stamps unwrapped (`echo_spans`, 2026-11-12); `--can-fail` since then. Each variant's verdict line starts with VOID or FAIL (`variant_verdict`, 2026-11-21, the reviewer's sweep): a failed variant printed "ORDER/COMPLETENESS FAILED", so another variant's VOID line made script_judge read the run VOID; a short offer is a VOID line |
| `bench_switch_errors.py` | Spec 2.3: vehicle-side bus errors from a mode switch with an acknowledger present. From 2026-11-23 its Innomaker is `bench_charger_witness.open_gs_usb` (`bench_rate_feasibility`'s, the same opener, until its retirement), and the X2's port or the Innomaker not opening prints a VOID line, exit 3 (`premise_line`; before, a traceback: the reviewer's sweep ruling on premise failures); `--can-fail` (10 mutants) |
| `bench_switch_loss.py` | Spec 2 / 2.3: frames lost when the vehicle port switches mode. From 2026-11-23 (the reviewer's pace ruling) the filler's schedule is `filler_cycles`, each on window and the control offered through `bench_offer` (can-tools' pace, ended by time; `bench_pacer` before), so a cycle's stop stays at its start + on time however late the sends run, canbench loaded before the first start and before the filler's ready; its Innomaker is `bench_charger_witness.open_gs_usb`; the X2's port not opening, or the filler's process not ready within 30 s, prints a VOID line, exit 3 (`premise_line`; before, a traceback). `--can-fail` (22 mutants) |
| `bench_switch_pending.py` | Spec 2 / 2.3: a pending vehicle-bound frame at a switch to listen-only. From 2026-11-23 its Innomaker is `bench_charger_witness.open_gs_usb` (`bench_rate_feasibility`'s, the same opener, until its retirement), and the X2's port or the Innomaker not opening writes a VOID line to the out file, which `switch_pending_judge` reads, exit 3 (`premise_line`; before, a traceback and no out file: the reviewer's sweep ruling on premise failures); `--can-fail` (8 mutants) |
| `bench_lom_errflag.py` | Spec 2.3, stage (b) batch 1 step 3, check (c) (the reviewer, 2026-11-28): no dominant error flag from a listen-only vehicle port after a provoked error. The Kvaser sends at a wrong rate (250k, then 1M) on the 500k segment, the PCAN closed. The judge is the Kvaser's own link counters: with every receiver silent it goes error-passive and never bus-off (ACK errors only); a receiver driving dominant error flags gives it bus-offs. `--board vport` (check `b-vport/lom-errflag-vport`): vport_bench in driver listen-only and in register listen-only with REC 128 (the controls, which must be clean) and with no REC write (`LOMREG norec`, error-active: the can-fail, which must give bus-offs). Each arm's board state read back first; exit 0 PASS, 1 FAIL, 3 VOID. The product image's form joins it with the ported image; `--can-fail` (23 mutants) |
| `bench_topology.py` | Which interposer segment each adapter is on, separated, not guessed. Its drains count error frames by can-tools' classifier (`adapter_roles.bus_error`, 2026-11-19, the reviewer's bench-layer item): on SocketCAN, the Kvaser included (in a `WatchedBus` until 2026-11-23, classified on its `.inner`), a controller-state or counters-only frame is `not_bus`, not an error; elsewhere every error frame counts. An adapter that does not open prints a VOID line and RESOLVED then exits 3 (`open_void_lines`, `open_rc`, 2026-11-21, the reviewer's sweep: it printed "FAILED", not a FAIL line, and exited 0). An adapter left unplaced in RESOLVED is VOID, exit 3, since each one it opens is a bench role (`ADAPTER_ROLE`, `unplaced_lines`; a device that is no bench role would be reported only), and each TEST FAULT exit prints a VOID line (`fault_line`) (2026-11-23, the reviewer's sweep rulings of 2026-11-21: 5, and premise failures as VOID lines); UNRESOLVED stays exit 1. Its drains run under `canbench.reader_guard.guard` and raise its `MissedFrames` on a missed-frames row, keeping their own screen step (2026-11-23, the reviewer's reader_guard ruling). The Kvaser is a `Kvaser`, its bus with a `kvaser_link.EchoWatch` over `canbench.echo_watch`, `read_after_send=False` (2026-11-23, the reviewer's echo_watch ruling; a `WatchedBus` before): sends go through the watch, and its drain (`drain_of`) reads the bus, hands each frame to `watch.saw()` before counting it and calls `watch.check()` after every read (self-test on a stand-in Kvaser: probes' echoes taken and never heard, a lost echo a stall from the drain's check; can-fails: the saw() or the check() dropped, an echo counted, the watch reading after each send, the drain without its watch). `--self-test`, `--can-fail` (34 mutants) |
| `bench_usb_host.py` | Spec 8.3: output a USB host is not reading is dropped, never held. Its `unwrap_us32` (gs_usb's 32-bit microsecond stamp, which wraps every 71.6 min) is the one `bench_charger_silent`, `bench_hold12` and `bench_rx_order` import (2026-11-12). close-boots on Linux sends esptool's hard reset and closes the port at once, since there the close does not reset the board; Windows keeps the close alone (2026-11-18, the reviewer's ruling (a) on 8b8e). Its injector's Kvaser takes no error frames, and the injector reads its socket after a refused send too (`open_kvaser`, `Injector`; 2026-11-20, the reviewer's Q2). Each case ends on a RESULT line counting its items' PASS, FAIL, VOID and reader deaths with the exit code (`result_line`; a VOID boot among passes still exits 0, so the line names the count), and the suite puts it in the PASS's detail; bridge-cycles is VOID unless every stop met its premise, since no minimum of judged stops is named (`stops_verdict`; before, one judged stop of five was enough) (2026-11-23, the reviewer's sweep rulings 1 and 2 of 2026-11-21). `Rx` reads with `canbench.reader_guard.read_loop`, `drain=False` (2026-11-23, the reviewer's reader_guard ruling). The injector sends through a `kvaser_link.EchoWatch` with the default `read_after_send=True`, so canbench's non-blocking read follows every send, and its sent stamp is taken after the send returns, so the stamp includes that read (2026-11-23, the reviewer's echo_watch ruling and its acceptance of B's judgement move 2) |
| `bench_vport.py` | Spec 9's vehicle-port measurements, with the vport_bench image. Its node starts with the stall rule suspended, since step 0 leaves nothing to acknowledge it, and each step turns the rule on once its ACKer is on the bus (2026-11-20; `step_blocks` checks each step). The node opens without error frames and reads its socket after every send, a refused one too, so the echoes of frames held through a no-ACK window are not lost on a full receive queue (2026-11-20; `held_flush_cases` runs the flush over `_HeldBus`, a stand-in for the Kvaser as the bench showed it). From 2026-11-27 that read after a refused send is canbench's alone (`EchoWatch.send`, `read_after_send`): the node's own `poll(0.0)` there is gone (the reviewer, 2026-11-23: at bench_vport's next change). From the reviewer's rulings of 2026-11-20 (Q1, Q3, stall 16): the node's link comes up with restart-ms 100, read back, on every link_up; each window reads the node's link counters before and after (`kvaser_link.link_counters`, can-tools' `bench_layer.link_counters`): a wrong-rate window reports its bus-offs, and the flush when the ACK returns forgives as many never-echoed frames as tx_dropped grew in the wrong-rate windows of the span (`expect_acks(False, wrong_rate=True)`), and no more; a node bus-off in a window expecting its frames to complete makes it VOID (`judge_node_link`, `node_fault`); the no-ack arm's `score_noack` also counts the window's rows of the node's head frame (`noack_rows`); a node frame is the window's own by its send log or its sent seqs, so a send called just before the cut keeps its row. From the reviewer's ruling (a) of 2026-11-20: the flush excuses every never-echoed send the node made in a wrong-rate span, however many (`Node.wrong_ev`, `node_sender`, `kvaser_link.EchoWatch.finish(excuse)`), tx_dropped's count forgiving only the others, and prints each wrong-rate window's figures: its sends never echoed, tx_dropped's growth, and the difference, its sends neither echoed nor dropped (`span_figures`). Step 2 judges each wrong-rate window after it ends in `judge_bitrate_window`, out of `run_bench` since run 69d8 (2026-11-21), whose vp1-vp3 stopped there on a name the coverage change had moved; the self-test drives each of its paths to the end (judged, flagged, refused, witness fault). recstart's declared STOP outcomes (SOMETHING ELSE, NEITHER) are VOID with their text (`STOP_OUTCOME`, `stop_outcome`; `combine` names them apart from a TEST FAULT), its NOT JUDGED premise failures are TEST FAULTs with a VOID line (`not_judged`; before, a FAIL), and the verdicts name the stall step's no-ACKer variant as NOT RUN with why, in a RESULT line too, printed only when a variant was not run so vport-windows' reading of a report is unchanged (`verdict_lines`, `NO_ACKER_WHY`) (2026-11-23, the reviewer's sweep rulings 3 and 4 of 2026-11-21, and premise failures as VOID lines). Step 1 has no wrong-rate arms from 2026-11-27: spec 9 leaves the wrong bit rate out of the listen-only cases (the reviewer, on the user's ruling), so the arms `wrong-250k` and `wrong-1m` and the bench suite's `vport-listen-wrong-rate` are gone; every listen window sets 500k and expects the node's frames to complete. The diagnostic steps 1b discrim and 1c offpair stay |
| `klog_windows.py` | The kernel's USB and CAN lines (journalctl -k) over every judged hardware check's window of a run record (the bench suite's runs, vp and bench, and a chain's row for one of them; never the emulated or harness suites, whose loop has no dongle: the reviewer, 2026-11-16, at the user's correction), each result beside a -71, an EPROTO or a USB disconnect FLAGGED (the reviewer, 2026-11-13, after CAN replay's bench advisory: both bench hubs bus-powered), and from 2026-11-16 beside a port "disabled by hub" or an "error -32" (run e79d's WiCAN failure lines); the lines are `kvaser_link.KLOG_RE`'s, since the hub swap every device under the bench hub 1-3 by any driver and the bench's devices by ID (the reviewer, 2026-11-16); an entry-time scan, re-runnable since madhouse's journal is persistent; judges nothing. `python3.13 -B klog_windows.py RUN_DIR [--out FILE]`; `--self-test`, `--can-fail` |
| `reader_death.py` | A reader thread that dies is VOID, not a product result (2026-11-19, the reviewer's PCAN ruling 1): this harness's verdict on a bench reader. Since 2026-11-23 (the reviewer's reader_guard ruling, can-tools f19d04b) the guard and the screen are `canbench.reader_guard`'s (`guard`, `screen`, `read_loop`), which do what this module's own `guard`, `screen` and record did (its `LostFrames` is canbench's `MissedFrames`); it keeps the name because what is left is the death's verdict: `rg()` (the pinned `reader_guard`, loaded once by file), `Death(name)` (canbench's `ReaderRecord` with `.lost`, the first exception a `MissedFrames`, and `.void_line(tag)`, the VOID line its check prints, unchanged), `DIED` (the verdict of a boot or phase whose reader died), `undrained(src)` / `drain_check(path, check)` (every canbench reader in a driver's source passes `drain=False`; each driver's self-test runs it on itself), and the self-tests' stand-ins `RaisingBus`, `report_rows()` (can-tools' own state, overrun, bus-off, restarted and bus-error rows) and `report_bus(frames)` (an unopened CanbenchPcanBus handing them up). Its self-test drives canbench's guard, screen and `read_loop` with a `Death` to every verdict the old screen gave (a state row counted, bus errors kept, an overrun, bus-off or RESTARTED row stopping the reader as lost frames, `drain=False` against the default); `first_dead`, used only by `bench_rate_feasibility`, went. `--self-test`, `--can-fail` (12 mutants; the old ones on the guard, screen, record and `first_dead` retired with that code) |
| `rx_proc.py` | A CAN reader in a process of its own, for a check that reads one adapter while it sends busily on another (the reviewer's M6, 2026-11-13: reader threads beside a busy sender lost frames); `witness_void` (a witness that did not open, raised recv errors, or for which the kernel reports lost frames voids the check, with the kernel's lines) and `lost_lines`. An error frame is a row only when can-tools' classifier counts it a bus error (`adapter_roles.bus_error`, 2026-11-19, the reviewer's bench-layer item: on SocketCAN not a controller-state change, bus-off, restarted or the counters alone, which are counted in `not_bus_errors`; on any other backend every error frame, as before); from 2026-11-20 (PCAN item 2) an error frame saying the reader missed frames (`adapter_roles.missed`: frames lost, and from the reviewer's ruling of the same day a bus-off or restart) is kept in `lost_frames` with its reason, not a row, and `witness_void` VOIDs on it; `--self-test`, `--can-fail` |
| `reset_check.py` | Step 4 item 5: boot-mid-session's board reset verified on the bench; `--serial-log` names its serial log. Since 2026-11-18 the X2's port comes from `--port`, required (was COM3 in the code; the bench suite passes `{serial_port}`). Since 2026-11-19 it exits 1, with a FAIL line, on `RESET PROVEN: NO` (it exited 0 either way before, so the suite's reset-check could not fail). Since 2026-11-19 (PCAN ruling 1) a PCAN reader that dies gives `RESET PROVEN: NOT-PROVEN` with the reason and a VOID line, exit 3. From 2026-11-23 the reader is `canbench.reader_guard.read_loop`, `drain=False` (the reviewer's reader_guard ruling), and the board's port or an adapter that does not open is a VOID line, exit 3 (`NotOpened`; before, a traceback, read FAIL: the reviewer's sweep ruling on premise failures); `--self-test` (the port, the verdict on stand-in rows and frames, and `collect` with stand-in adapters), `--can-fail` (25 mutants) |
| `keepalive_probe.py` | Step 4 item 3: where the rx-order keep-alive goes, recorded. Since 2026-11-13 the port comes from `--port` (was COM3), the Kvaser's link is brought up and read back first; since 2026-11-18 the PCAN is role pcanfd and `--pcan-channel` (2026-11-13 to 2026-11-18; PCAN_USBBUS1 before) is refused. From 2026-11-23 each reader is `canbench.reader_guard.read_loop`, `drain=False` (the reviewer's reader_guard ruling), and an adapter or the board's port that does not open is a VOID line, exit 3 (before, a traceback, read FAIL: the reviewer's sweep ruling on premise failures); `--self-test`, `--can-fail` (14 mutants) |
| `script_can_fail.py` | Runs a driver's self-test against each of its `MUTANTS`, from a copy in a `scratch_dir` run folder (removed in a finally; before 2026-10-31, the machine temp folder) |
| `script_can_fail_test.py` | Test for `script_can_fail.py` |
