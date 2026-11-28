# shared/twai_port: verification evidence

Test evidence that the code in `shared/twai_port/` meets each requirement
in `SPEC.md`, and the evidence its build-switch table (`vtrux_checks.json`,
shared/tools R20) rests on. Why each requirement is written as it is:
`VALIDATION.md`. The tester's file (`README.md`).

Each entry names the requirements it covers, the commit of the code it
tested, the checks and the commit they ran from, and its run files under
`$REVERSE_IT_DIR`, with each file's hash. A pass verifies the code it ran
on. Entries arrive as commits on a tester's `shared/twai_port/<topic>`
branch, and the reviewer of the project that needs them merges and pushes.

## `TWAI_PORT_HOST_SWITCH` refused by every ESP-IDF build (charge-interposer tester, 2026-11-28)

**What it is evidence for:** the table's entry for `TWAI_PORT_HOST_SWITCH`,
class "not a switch", kind "host-test-only define, refused by every ESP-IDF
build". No image can carry the define, so it has no image row. The
evidence is the refusal itself (charge-interposer reviewer, 2026-11-27).
**Code tested:** `shared/twai_port/CMakeLists.txt` and `twai_port_ll.h` at
`f41286d3` on `shared/twai_port/stage-b-fake`. Both files are the
implementor's `544d74ae`, unchanged to `f41286d3`. **Check:**
`shared/twai_port/test/host_switch_refused.sh` at `f41286d3`, clean tracked
tree. **Host:** madhouse-debian, ESP-IDF v5.4.2, gcc 14.2.0, in
`build.slice`. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/shared_twai_port_host_switch_f41286d3/`
(`SHA256SUMS` hashes both; the folder's `README.md` lists them).

| Check | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| ESP-IDF configure | PASS | A minimal ESP32-S3 project with `twai_port`, `can_port` and the x2 board. Without the define, `idf.py reconfigure` exits 0 and the component builds the S3 register file (the control). With `-DTWAI_PORT_HOST_SWITCH=1`, `idf.py reconfigure` exits 2 on the component's `FATAL_ERROR` (CMakeLists.txt:77): "vtrux/twai_port: TWAI_PORT_HOST_SWITCH is for host builds on the T10 fake only; an ESP-IDF build must not set it". | `host_switch_refused_plain.txt` (`af5cc33e...89fce8`) |
| Compile guard | PASS | `twai_port_ll.h` compiles on the host with the define (the control: the testers' host builds). With `ESP_PLATFORM` also defined, the compile stops on its `#error` (twai_port_ll.h:32). | `host_switch_refused_plain.txt` |
| Can-fail | PASS | `HOST_SWITCH_REFUSED_BROKEN=1` makes the two refused runs without the define. The check then fails both of them and exits 1. | `host_switch_refused_broken.txt` (`72fdf537...658dfd`) |

## Refusal rows on interposer images, tag 2026.11.27.2 (charge-interposer tester, 2026-11-27 PST)

**What it is evidence for:** shared/tools R20 for the component's table on
interposer images. Each test-only row must fail on an image built with its
switch and pass on one without it, and the component's scope must pass the
whole R20 check on an image without the switches.
**Code tested:** the component at tag 2026.11.27.2 (`7ce07f75`), in
charge-interposer `d6a29e6b`'s dev builds: bridge and truck (without the
switches) and s3probe (with `TWAI_PORT_S3_PROBE` and
`TWAI_PORT_TXABORT_PROBE`). **Check:**
`charge-interposer/fw_harness/refusal_pairs.py --component vtrux__twai_port`
at `2e2dbd8c` on `charge-interposer/tester/switch-checks`, clean tracked
tree, with the pinned shared/tools (tag 2026.11.17.4) `vtrux_checks.switches`.
Its test and can-fail: `charge-interposer/refusal_pairs_test.py` (21 of 21
pass; 34 of 34 mutants caught). **Host:** madhouse-debian, ESP-IDF v5.4.2,
in `build.slice`. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/shared_twai_port_r20_pairs_2e2dbd8c/`
(`SHA256SUMS` hashes the three files; the folder's `README.md` lists them,
with each build's R1 image hash).

| Check | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| `TWAI_PORT_S3_PROBE`, both rows | PASS | Each row fails on s3probe: the ELF defines `twai_port_s3_probe_build`, `twai_port_t19_init` and `twai_port_t19_line`, and the image carries `resetprobe`, `switchloop` and `unknown command; the commands are info`. Each row passes on bridge and on truck. | `positive_bridge.txt` (`61925cc7...7b3bfa`), `positive_truck.txt` (`84bab525...58ad40`) |
| `TWAI_PORT_TXABORT_PROBE`, strings row | PASS | Fails on s3probe: the image carries `"probe":"gi_txabort"` (not `/gi_txabort`). Passes on bridge and on truck. | `positive_bridge.txt`, `positive_truck.txt` |
| `TWAI_PORT_TXABORT_PROBE`, symbols row | FAIL | Not shown failing on any interposer image. On s3probe, built with the switch, the ELF defines none of `twai_port_txabort_register`, `txab_handler` and `txab_uri`, so the row passes there. It passes on bridge and on truck. | `positive_bridge.txt`, `positive_truck.txt` |
| The component's scope, whole R20 check | FAIL | On bridge and on truck, the scope has two symbols that the table does not list: `RTC_NOINIT_ATTR` (twai_port_ll_esp32s3.c:154) and `S3_SUPPORTED` (twai_port_ll_esp32s3.c:147, :214). | `positive_bridge.txt`, `positive_truck.txt` |
| Can-fail on real builds | PASS | OFF and ON swapped (s3probe as OFF, the bridge build as the s3probe ON): FAIL 10, exit 1, each on its own check. | `negative_swapped.txt` (`c0ce3071...d8c639`) |

## Refusal rows on interposer images, tag 2026.11.27.5 (charge-interposer tester, 2026-11-27 PST)

**What it is evidence for:** shared/tools R20 for the component's table on
interposer images, as in the entry for tag 2026.11.27.2, after the table's
changes `80d85fc7` (`RTC_NOINIT_ATTR` and `S3_SUPPORTED`, "not a switch")
and `a2cab7de` (the `TWAI_PORT_TXABORT_PROBE` symbols row names
`twai_port_txabort_run`; charge-interposer reviewer, 2026-11-27). Both
failures of that entry are gone.
**Code tested:** the component at tag 2026.11.27.5 (`cf75bcfe`), in
charge-interposer `88396fce`'s dev builds, made at `53396ed2` (harness
commits only after `88396fce`): bridge and truck (without the switches) and
s3probe (with `TWAI_PORT_S3_PROBE` and `TWAI_PORT_TXABORT_PROBE`).
**Check:** `charge-interposer/fw_harness/refusal_pairs.py --component
vtrux__twai_port` at `53396ed2` on `charge-interposer/tester/switch-checks`,
clean tracked tree, with the pinned shared/tools (tag 2026.11.17.4)
`vtrux_checks.switches`. Its test and can-fail:
`charge-interposer/refusal_pairs_test.py` (21 of 21 pass; 34 of 34 mutants
caught). **Host:** madhouse-debian, ESP-IDF v5.4.2, in `build.slice`.
**Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/shared_twai_port_r20_pairs_53396ed2/`
(`SHA256SUMS` hashes the three files; the folder's `README.md` lists them,
with each build's R1 image hash).

| Check | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| `TWAI_PORT_S3_PROBE`, both rows | PASS | Each row fails on s3probe (the 3 symbols and the 3 strings) and passes on bridge and on truck. | `positive_bridge.txt` (`1dd286db...cd6e6b`), `positive_truck.txt` (`a2cf2921...8df379`) |
| `TWAI_PORT_TXABORT_PROBE`, strings row | PASS | Fails on s3probe (`"probe":"gi_txabort"`). Passes on bridge and on truck. | `positive_bridge.txt`, `positive_truck.txt` |
| `TWAI_PORT_TXABORT_PROBE`, symbols row | PASS | Fails on s3probe: the ELF defines `twai_port_txabort_run`. Passes on bridge and on truck. | `positive_bridge.txt`, `positive_truck.txt` |
| The component's scope, whole R20 check | PASS | On bridge and on truck, no failure in the component's scope: no symbol unlisted, none stale, every refusal rule passes. | `positive_bridge.txt`, `positive_truck.txt` |
| Can-fail on real builds | PASS | OFF and ON swapped (s3probe as OFF, the bridge build as the s3probe ON): FAIL 11, exit 1, each on its own check. | `negative_swapped.txt` (`6bab0fc7...c2c375`) |

## T19 on the X2's S3: the kept-driver switch, tag 2026.11.27.6 (charge-interposer tester, 2026-11-27 PST)

**What it is evidence for:** T19's measurement on the ESP32-S3 of the
kept-driver switch (T14, T15, T17). The arms for the restart stop (T12) and
for reset mode on a frame left in the buffer did not run (see the table).
T13's measurements on a bus where no node acknowledges (`txabort`, m1-m3)
are not in this check. They wait for the probe analysis to move into this
component (charge-interposer reviewer, 2026-11-27).
**Code tested:** the component at tag 2026.11.27.6 (`a34d5a73`), in the
s3probe dev build of charge-interposer `1d4221c9`, made at `7ab0151b` on
`charge-interposer/tester/switch-checks` (a merge of `1d4221c9` with no
product change): ELF sha256 `d66b2890...242e37eb`, app sha256
`c175685a...`, ESP-IDF v5.4.2. The runner flashed it (4 writes verified)
and read its `@HELLO` `elf_sha256` off the wire at 23:42:49.
**Check:** `charge-interposer/bench_drivers/bench_t19.py` at `7ab0151b`
(written in `53396ed2`, unchanged since), check `b-s3probe/t19-s3probe` of
`charge-interposer/bench_suite.py:bench`, run by the shared/bench chain
runner (tag 2026.11.20) from a clean tracked tree. The runner's premise
was a passing emulated run on the same product digest
(`emulated_sc_1d4221c9`, digest `c4fb8191...`). Its test and can-fail:
`bench_t19.py`'s self-test (OK) and can-fail (31 of 31 caught). **Rig:**
the X2's CAN1 on the vehicle segment with the Kvaser and the PCAN, nothing
else; "ack" is the Kvaser up at 500k, "noack" is the Kvaser down and the
PCAN closed. **Host:** madhouse-debian, in `bench.slice`, the runner
holding the bench lock. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/bench_t19_1d4221c9/`,
the runner's record (`record.json`, `f7dcc7e0...76e2e7`; `index.txt`,
`bbc29b70...9d39d0`).

The runner's verdict is FAIL (the driver's exit 3, `RESULT: VOID`).

| Arm | Result | What was tested and observed | Run file (sha256) |
|---|---|---|---|
| `info` (the premise) | PASS | `info` answers build `7ab0151b`, idf v5.4.2 (the version T19 names), no driver installed at boot (`port_core` -1); `regs` answers. | `b-s3probe/t19-s3probe/t19-s3probe.txt` (`32e20a7e...07b37a`) |
| `switch`, T14 | FAIL | Ack rig. `switch to=lom` answers status 200, `report.result` 0, `switch_us` 1. After it, `mode` is "normal", and `regs` reads the LOM bit 0 with the driver installed (`drv.state` 1). `report` is `seq` 0, `to` "normal". T14 wants listen-only reached through the controller's registers, with the driver kept installed. | `t19-s3probe.txt` |
| `switch`, T15 | FAIL | After `switch to=lom`: `rec_written` 0 and `rec_read_back` 0. T15 wants 128 written and read back on the way into listen-only. | `t19-s3probe.txt` |
| `held`, T17 | FAIL | Noack rig. `switch to=lom n=8`: `queued` 8, then `report.held` 0, `head_aborted` false, `hook` empty (`hook_lost` 0), `tx_discarded_switch` +0, `mtx_after` 8, `mode` "normal". T17 wants the 8 frames removed, each given to the project once (ID 0x7FD, tags 0x7100 to 0x7107) and counted once as discarded at the switch. In the 0.5 s after the following `switch to=normal` on the ack rig, the Kvaser saw no 0x7FD frame. | `t19-s3probe.txt` |
| `switchloop`, T14, T15 | VOID: no answer | Ack rig, the PCAN sending 0x5A6 (5727 frames). `switchloop k=20 period_ms=100` gave no answer in 12 s. The resource record over 23:42:50 to 23:43:25 is clean (`bench.slice` CPU pressure 0.2 %, no memory or IO pressure, longest gap 5 s), so this is not starvation. The driver stops at a VOID arm. *Correction (charge-interposer tester, 2026-11-28): the "no answer" is a harness defect, not a board result. The driver's reader took the serial library's part line at its 0.2 s timeout as a line. The app sends this answer in pieces (its start before the work, the rest after), so neither piece parsed. Fixed in `bench_t19.py` at charge-interposer `b4dbf413`. The arm has no result on this image.* | `t19-s3probe.txt`, `b-s3probe/t19-s3probe/check.log` (`5c0a70ba...f44129`) |
| `resetprobe`, 6 arms (measured) | NOT RUN | The driver stopped after `switchloop`. No RELEASED WITHOUT AN INTERRUPT line. | |
| `restart`, 6 arms, T12 | NOT RUN | The driver stopped after `switchloop`. | |
