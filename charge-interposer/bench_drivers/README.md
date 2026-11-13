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

| File | What |
|---|---|
| `bench_c12.py` | C12 and the rest of the MCP2515 conformance sequence on silicon, against the fake's transcript |
| `bench_canbench_smoke.py` | The can-tools pin on the bench: kvaser and gs_usb opened through `bus.open_bus` with canbench from the pinned commit, the refusal with `CAN_TOOLS_DIR` unset, the four diag pages heard with the charger up, and the X2's build id from 0x7F7; run by hand under a hand take (2026-11-01; a scratch script at the 2026-10-30 cutover) |
| `bench_charger_silent.py` | Spec 2.2 on the bench, the single-adapter variant. The held frame and the reported return burst use the Innomaker's device stamps unwrapped (`bench_usb_host.unwrap_us32`, 2026-11-12); `--can-fail` since then |
| `bench_charger_witness.py` | The charger-side wire witness, and the `Tee` and Innomaker helpers the other drivers import |
| `bench_flight_silence.py` | Spec 8.3 flight silence on the truck image; with `--idf-log`, spec 8.3's "framework logging is compiled out of every build" on any image: only an application line in ESP-IDF's or the Arduino core's log form fails (2a acceptance, 2026-11-04). The bench suite runs it as `flight-silence` in b-truck (the default verdict: no application byte at all) and with `--idf-log` as `idf-log` in b-bridge and b-witness |
| `bench_hold12.py` | Spec 12 entry 2: the same-identifier hold on silicon. The filler's echoes are sorted by the Innomaker's device stamps unwrapped (`filler_echoes`, 2026-11-12) |
| `bench_innomaker_clock.py` | The Innomaker's own clock: device-timestamped echoes and rate runs |
| `bench_mcp_reset.py` | Whether the X2's MCP2515 keeps ACKing on the charger segment while the ESP32 is down. Its ROM holds run the core flasher's esptool, the settings' `esptool.argv` passed as `VTRUX_ESPTOOL_ARGV` (2026-11-12); unset, PlatformIO's esptool.py on Windows as before, refused on Linux |
| `bench_pacer.py` | The pacer every driver that offers frames at a stated rate uses |
| `bench_powerup_round.py` | Spec 2.3's power-up round on the board; also run by `../bench_identify.py` to read 0x7F7 |
| `bench_rate_feasibility.py` | Whether this host can offer the rates spec 12 entry 2 asks for; imported for its rate helpers (`open_gs_usb`). **CANlib-only** (the reviewer's E, 2026-11-13): its Kvaser witness judges rates on CANlib's device stamps, so on Linux, where the Kvaser is the kernel's kvaser_usb with software stamps, `open_witness` refuses; not ported, hand-run only. The one harness file the Kvaser scan (`kvaser_link_test.py`) lets open the CANlib `kvaser` interface |
| `bench_rx_order.py` | Step 4 item 4: receive order on silicon, charger to vehicle. The reported echo span per burst uses the Innomaker's device stamps unwrapped (`echo_spans`, 2026-11-12); `--can-fail` since then |
| `bench_switch_errors.py` | Spec 2.3: vehicle-side bus errors from a mode switch with an acknowledger present |
| `bench_switch_loss.py` | Spec 2 / 2.3: frames lost when the vehicle port switches mode |
| `bench_switch_pending.py` | Spec 2 / 2.3: a pending vehicle-bound frame at a switch to listen-only |
| `bench_topology.py` | Which interposer segment each adapter is on, separated, not guessed |
| `bench_usb_host.py` | Spec 8.3: output a USB host is not reading is dropped, never held. Its `unwrap_us32` (gs_usb's 32-bit microsecond stamp, which wraps every 71.6 min) is the one `bench_charger_silent`, `bench_hold12` and `bench_rx_order` import (2026-11-12) |
| `bench_vport.py` | Spec 9's vehicle-port measurements, with the vport_bench image |
| `klog_windows.py` | The kernel's USB and CAN lines (journalctl -k) over every judged check's window of a run record, each result beside a -71, an EPROTO or a USB disconnect FLAGGED (the reviewer, 2026-11-13, after CAN replay's bench advisory: both bench hubs bus-powered); an entry-time scan, re-runnable since madhouse's journal is persistent; judges nothing. `python3.13 -B klog_windows.py RUN_DIR [--out FILE]`; `--self-test`, `--can-fail` |
| `rx_proc.py` | A CAN reader in a process of its own, for a check that reads one adapter while it sends busily on another (the reviewer's M6, 2026-11-13: reader threads beside a busy sender lost frames); `witness_void` (a witness that did not open, raised recv errors, or for which the kernel reports lost frames voids the check, with the kernel's lines) and `lost_lines`; `--self-test`, `--can-fail` |
| `reset_check.py` | Step 4 item 5: boot-mid-session's board reset verified on the bench; `--serial-log` names its serial log |
| `keepalive_probe.py` | Step 4 item 3: where the rx-order keep-alive goes, recorded. Since 2026-11-13 the port and the PCAN channel come from `--port` / `--pcan-channel` (were COM3 / PCAN_USBBUS1), the Kvaser's link is brought up and read back first; `--self-test`, `--can-fail` |
| `script_can_fail.py` | Runs a driver's self-test against each of its `MUTANTS`, from a copy in a `scratch_dir` run folder (removed in a finally; before 2026-10-31, the machine temp folder) |
| `script_can_fail_test.py` | Test for `script_can_fail.py` |
