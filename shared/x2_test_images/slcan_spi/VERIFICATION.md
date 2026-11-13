# slcan_spi -- test results -- NOT FOR VEHICLE

**Owner:** charge-interposer tester (x2_test_images SPEC T4).

Test evidence that a build of `slcan_spi` meets `../SPEC.md` section 5 and
the T8, T8a, T10 and T14 requirements it carries. Each entry names what was
tested, the build, the harness commit, the result and the run files. A pass
carries to a build with the same code hash (shared/tools R3) and to nothing
else. Run files are under
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/`.
The X2 is the bench's one ESP32-CAN-X2; its MAC is in
`$REVERSE_IT_DIR/firmware/bench-holder.md` (bench identifiers are never in
git).

## The build without jamming (`bc22d9b0`)

Dev build (T7, not pinnable) from `bc22d9b0` on
`shared/x2_test_images/slcan-spi-2`, made by the shared tooling
(`vtrux_build build dev`, shared/tools at `2026.11.12.2`), ESP-IDF v5.4.2:

- version `2026.11.12.2-13-gbc22d9b0`;
- image hash (ELF SHA-256) `102bc6ac082ed4d2b074d9a09595ec70f7aaed37c4dc8ee47fc06de52ad4a723`;
- code hash `8d02084925f4ced24802d1b4e29fe9448006e0138f6a922b46fc26d8b6cc53c3`.

No jamming in this build (T13: jamming comes after it).

## Off the board, 2026-11-13

On the build above, at `bc22d9b0` (the shared check at `01570bed`, which
adds only `x2_image.json`):

- **Protocol unit tests** (`test/`): ALL PASS; all 14 mutants killed by them
  (`test/mutate.py`).
- **`bench/check_image.py`** (T14 name, T10/T12 ELF hash, T13 no TWAI
  symbol): PASS; `--can-fail`: each of the three fails on its broken input.
- **`../tools/check_image.py slcan_spi dev`**: PASS, all eleven checks
  (name, chip, variant, NOT_FOR_VEHICLE, boot line string, ELF hash, symbol
  table, no `twai_` symbol, console and logs off, the whole ELF hash in the
  identity). The shared tool's own can-fails are its tests' (`../tools/`).

## On the bench, 2026-11-13 00:03-00:10

The X2 flashed with the build above through the shared flasher (T11), "4
writes verified"; canchg (gs_usb) the only other node on the charger
segment; canmain down; the WiCAN untouched. Harness `bench/slcan_spi_bench.py`
at `bc22d9b0`. Run folder `slcan_spi_20261112T233601/`: the checks in
`bc22d9b0_all/result.json` (exit 0), each can-fail in
`bc22d9b0_canfail_<check>/result.json`.

| Check | Requirement | Result | Its can-fail |
|---|---|---|---|
| `identity` | T10, T12: `N` and `V` give `slcan_spi_NOT_FOR_VEHICLE 2026.11.12.2-13-gbc22d9b0 elf=102bc6ac...` (the whole hash); `B` shows CANSTAT 0x80 (T8) | PASS | expected hash altered: FAIL |
| `attach` | T14's boot banner in the kernel's slcan stream: attached after a boot, the kernel counts nothing; attached and up through a reset, `rx_over_errors` 1 (the ROM's line, gap below), no frame, error or error frame; the link works after each | PASS | the ROM line not allowed for: FAIL |
| `silence` | T8a, T12: canchg's frame not ACKed closed, in `L` and after `C`; ACKed in `O`, handed to the host, and the host's frame reaches canchg | PASS | "closed" run with the X2 open: FAIL |
| `reset` | T8a: a reset returns it to silent; the boot line `\n# NOT FOR VEHICLE slcan_spi\r` (T14) 0.101 s after the reset, within 33 characters | PASS | no reset: FAIL |
| `rate-tx` | T12: 1,320 frames/s host to canchg for 60 s: 79,200 sent, 79,200 received in order, 0 reported dropped | PASS | canchg listen-only: FAIL |
| `rate-rx` | T12: 1,320 frames/s canchg to host for 60 s: 79,200 received in order, 0 overruns | PASS | the X2 listen-only: FAIL |
| `drops` | T12: no ACKer: the X2 error-passive and reported; 187 frames past the queue each reported (`e3OOO`: `tx_errors` 187, 187 TX_OVERFLOW error frames); with canchg up, 513 drained; 513 + 187 = 700 sent; back to error-active, reported | PASS | canchg up throughout: FAIL |
| `busoff` | T12: bus-off reported (CAN_ERR_BUSOFF) with canchg at 250 kbit/s | PASS | canchg at 500 kbit/s: FAIL |
| `overrun` | T12: the tty unread under 1,320 frames/s: 7,338 frames + 19,062 `e3ooo` = 26,400 canchg sent; `F` shows the receive-FIFO bit | PASS | the tty read throughout: FAIL |

Before and after: the X2 read `x2_app` `756d6714` off the wire, dumped and
restored by the custody tool (`custody/`, verify_flash digest matched).

## The jamming build (T13)

Dev build (T7, not pinnable) from `4dc0bd00` on
`shared/x2_test_images/slcan-spi-jam`, made by the shared tooling
(`vtrux_build build dev`, shared/tools at `2026.11.13`), ESP-IDF v5.4.2, in
build.slice:

- version `2026.11.13-9-g4dc0bd00`;
- image hash (ELF SHA-256) `f17106a5c02ae7e17e0aceb80f9884f0ed1c5f7a8f37730fe4d681bb4bfc1eb9`;
- code hash `3f77031e0ca534b0426140086a907474a5177a1571506a320b265212f267f0a0`.

It is a new code hash, so the passes above do not carry to it; every check
is run again on it here.

### Off the board, 2026-11-13

- **Unit tests** (`test/`, the protocol and `jam_frame`): ALL PASS; all 29
  mutants killed (`test/mutate.py`), at `a25a9ecf` (the firmware sources as
  at `4dc0bd00`).
- **`../tools/check_image.py . dev`**: PASS, all eleven checks, among them
  the CAN1 banner string and the required symbols `jam_drive_recessive`,
  `jam_init`, `jam_frame_decode`, `jam_frame_reply`, and no `twai_` symbol.
- **The harness's own tests**: `bench/test_jam_host.py` ALL PASS, 8/8
  mutants killed; `bench/test_witness.py` ALL PASS, 17/17 mutants killed.

### On the bench, 2026-11-13 01:33-01:43: the checks without CAN1's witness

The X2 flashed with the build above through the shared flasher (T11), "4
writes verified", 01:19, and read by its own `N`/`V` before (slcan_spi
`2026.11.13`, ELF `327108bf...`) and after. canchg (gs_usb) the only other
node on the charger segment; canmain down; the WiCAN untouched (it is on
CAN1, where the jam pulses went). Harness `bench/slcan_spi_bench.py` at
`a25a9ecf`. Run folder `slcan_spi_jam_20261113T011923/`: the checks in
`harness_a25a9ecf_all/result.json` (exit 0), each can-fail in
`harness_a25a9ecf_canfail_<check>/result.json` (each FAILED, exit 0). A first
pass at harness `4dc0bd00` (`4dc0bd00_all/`, `4dc0bd00_canfail_*/`) FAILED
`jam-channel` and `jam-concurrent` on two harness defects, fixed in
`a25a9ecf` (its commit message); the image and the other nine results were
the same.

| Check | Requirement | Result | Its can-fail |
|---|---|---|---|
| `identity` | T10, T12: `N` and `V` give `slcan_spi_NOT_FOR_VEHICLE 2026.11.13-9-g4dc0bd00 elf=f17106a5...` (the whole hash); `B` shows CANSTAT 0x80 (T8) and the CAN1 line "jam pulses on GPIO7, TWAI never started, stops=a+b" | PASS | expected hash altered: FAIL |
| `attach` | T14's boot banner in the kernel's slcan stream: attached after a boot, nothing counted; attached and up through a reset, `rx_over_errors` 1 (the ROM's line), no frame, error or error frame; the link works after each | PASS | the ROM line not allowed for: FAIL |
| `silence` | T8a, T12: canchg's frame not ACKed closed, in `L` and after `C`; ACKed in `O`, handed to the host, and the host's frame reaches canchg | PASS | "closed" run with the X2 open: FAIL |
| `reset` | T8a: a reset returns it to silent; the boot line (T14) 0.101 s after the reset, within 33 characters | PASS | no reset: FAIL |
| `rate-tx` | T12: 1,320 frames/s host to canchg for 60 s: 79,200 sent, 79,200 received in order, 0 reported dropped | PASS | canchg listen-only: FAIL |
| `rate-rx` | T12: 1,320 frames/s canchg to host for 60 s: 79,200 received in order, 0 overruns | PASS | the X2 listen-only: FAIL |
| `drops` | T12: no ACKer: error-passive reported; 187 frames past the queue each reported (`tx_errors` 187, 187 TX_OVERFLOW error frames); 513 drained; 513 + 187 = 700 sent; back to error-active, reported | PASS | canchg up throughout: FAIL |
| `busoff` | T12: bus-off reported (CAN_ERR_BUSOFF) with canchg at 250 kbit/s | PASS | canchg at 500 kbit/s: FAIL |
| `overrun` | T12: the tty unread under 1,320 frames/s: 7,331 frames + 19,069 `e3ooo` = 26,400 canchg sent; `F` shows the receive-FIFO bit | PASS | the tty read throughout: FAIL |
| `jam-channel` | T13's channel through the kernel's slcan: STATUS, a bad signature (`ERR bad_jam_frame`), a host frame on the reply ID (`ERR bad_jam_frame`), `ms` 0 (`ERR ms_zero`), a 1 s TRAIN (ACK, then DONE `end=deadline_layer_b stop_by=none`, as dev may end it) and STOP, each answered by reply frames that join into whole lines (0 out of form); STATUS with the X2 open listen-only, on the raw tty; canchg, listening on CAN2, saw no jam frame (0) and no error frame | PASS | the commands on `0x1F4A4D44`: no replies, and canchg saw them: FAIL |
| `jam-concurrent` | T13 "jamming and the SLCAN at the same time": a 30 s TRAIN (W 20 us, P 1000 us; asked 30.5 s, `CAPPED` to 30 s) through a 30 s run of 1,320 frames/s host to canchg: 39,600 sent, 39,600 received in order, 0 dropped; ACK and DONE (`end=deadline_layer_b`) came back through the same kernel slcan; canchg error-active, no error frame, no jam frame on CAN2 | PASS | no TRAIN sent: no ACK or DONE: FAIL |

**Not yet run on this build** (they need CAN1's witness, the bench's
Kvaser, which CANlib has not presented since 2026-11-13 00:32):
`jam-boot-silence`, `jam-port`, `jam-calibration`, `jam-stop` (on dev,
`stop_a_only` and `stop_b_only`), `jam-cap`, `jam-stop-cmd`. Until they
pass, T13's stops at the deadline, its 30 s cap as delivered, its boot
silence on CAN1 and the pulses' delivery are not shown for this build;
`jam-concurrent` shows the image answering for the TRAIN while the SLCAN
runs, not that the pulses reached CAN1.
(They were run on 2026-11-13 12:31-12:55, below.)

After the run the X2 was left on this build (read by `N`/`V` 01:43:2x).

### On the bench, 2026-11-13 12:31-12:55: the checks with CAN1's witness

**The builds.** Archived by `../tools/archive.py` from a worktree detached
at `shared/main` `63e10898` (dev builds, T7, not pinnable; manifest lines
2-4), ESP-IDF v5.4.2, in build.slice, each `check_image` PASS in its row:

| Variant | Row (app SHA-256) | ELF SHA-256 | Code hash |
|---|---|---|---|
| dev | `a6ea62c9773930a2` | `def7d7092bb52bc757157fb00d4ae1af6d7dd4d4175699877e083d08e8e5e5fa` | `3f77031e0ca534b0426140086a907474a5177a1571506a320b265212f267f0a0` |
| `stop_a_only` | `4e3fc6de6ae14843` | `830cf5b96d2fd897383c7fd7ae12db966283265edf2394ee6fdd3bc72890f516` | `a8066ca2563b98891727e6a6d563aacd22bec0a4887ab61fcf82118816298f15` |
| `stop_b_only` | `614aad5e89d87602` | `a2916c07aa608abd94d0c7899dbf75ef11e4d748e0580fbb9ddcd3b38d5baf98` | `eb77c44d704bccb393f0c725bcb1d7c221efef7381292b2a32e6564a9c6e6569` |

The dev build has the code hash of `4dc0bd00`'s above, so the passes of
both sections carry to each other (shared/tools R3). `stop_a_only` and
`stop_b_only` are judged by `jam-stop` alone.

**The bench.** Bench lock held by hand by tester (charge-interposer),
12:31:01-12:55:14. Each image flashed by `../tools/flash_row.py --dev` (T11)
from the `63e10898` checkout, and read after by its own `N`/`V` (the
version `2026.11.13.5-2-g63e10898` and the whole ELF hash of its row).
canx2 attached by the checks, canchg down (the X2 alone on the charger
segment), canmain down, the WiCAN untouched (it is on CAN1, where the
pulses went). CAN1's witness, the Kvaser on the kernel's kvaser_usb,
netdev `kvaser`, its link brought up by each check and read back
(`bench/witness.py`).

**Harness** `bench/slcan_spi_bench.py` and `bench/witness.py` at `6d6c78b0`
on `shared/x2_test_images/witness-margin`. Its own tests:
`bench/test_witness.py` ALL PASS, 32/32 mutants killed;
`bench/test_jam_host.py` ALL PASS; `bench/klog_scan.py --self-test` ALL
PASS, `--can-fail` 6/6 mutants caught. Run folder
`slcan_spi_witness_20261113T1232/`: `harness_6d6c78b0_dev_all/result.json`
(exit 0), three more `jam-calibration` runs on dev
(`harness_6d6c78b0_dev_calibration_<1-3>/`, each exit 0), each can-fail in
`harness_6d6c78b0_dev_canfail_<check>/` (each FAILED, exit 0), and `jam-stop`
with its can-fail on the other two images
(`harness_6d6c78b0_<variant>_jam-stop/`,
`harness_6d6c78b0_<variant>_canfail_jam-stop/`).

| Check | Requirement | Result | Its can-fail |
|---|---|---|---|
| `jam-boot-silence` | T13 "it boots silent": 3 resets through the USB port; 0, 0 and 0 CAN1 error frames from each reset to 5 s after its boot line | PASS | a pulse commanded after the third boot line: [0, 0, 1]: FAIL |
| `jam-port` | one `PULSES 20 1` through the jam channel is exactly one CAN1 error frame, from the command to 1 s after its DONE: 1 | PASS | two pulses: 2: FAIL |
| `jam-calibration` | 10 pulses at 1 s read as 10 groups of one, 1.000 s apart (+/- 50 ms); 9 at 1.1 s read as 9 groups 1.100 s apart, and not as 10 at 1 s; in each of the 4 runs | PASS | the 10 at 1 s judged as 9 at 1.1 s: FAIL |
| `jam-stop`, dev | T13's stops at the deadline: a 3 s TRAIN (W 20 us, P 5000 us): last error frame 0.009 s before the deadline, none in the 3 s after; DONE `end=deadline_layer_b stop_by=none` (dev: either stop) | PASS | judged against a deadline 2 s early: 299 frames after it: FAIL |
| `jam-stop`, `stop_a_only` | stop (a), the esp_timer, alone: last error frame 0.003 s before the deadline, none after; DONE `end=stopped stop_by=deadline_timer` | PASS | the same: 300 frames after: FAIL |
| `jam-stop`, `stop_b_only` | stop (b), the chunk sizing, alone: last error frame 0.009 s before the deadline, none after; DONE `end=deadline_layer_b stop_by=none` | PASS | the same: 299 frames after: FAIL |
| `jam-cap` | T13's 30 s cap: a TRAIN asked for 60 s: `CAPPED ms 60000 -> 30000`; last error frame 0.010 s before 30 s, none in the 3 s after | PASS | judged against 28 s: 299 frames after: FAIL |
| `jam-stop-cmd` | STOP 3 s into a 20 s TRAIN: `ACK STOP busy=1`; last error frame 0.004 s before the host sent STOP, none in the 3 s after; DONE `end=stopped stop_by=stop_cmd` | PASS | judged against a STOP 2 s earlier: 300 frames after: FAIL |

Every check ended with its closing pulse counted exactly once. The
witness's median lag behind host receipt was 0.1-0.4 ms in every check.

**The stamps' margin** (the gen-inhibit reviewer's ruling, relayed by the
charge-interposer reviewer, 2026-11-13: a window judged on the Kvaser's
host stamps at least 10 times their measured error, about 1.5 ms). Each
check's detail has its `stamp_margins`: the closing pulse's and
`jam-port`'s 50 ms lead 33x; `jam-calibration`'s +/- 50 ms on the gap of
two stamps 16.7x and its 0.2 s grouping 66.7x; the stop window 0.5 s 333x
and the quiet window 2.5 s; `jam-boot-silence`'s 5 s windows. None under
10x, so none moves to the PCAN or to counts.

**Does the witness go deaf?** (the same relay: gen-inhibit's PCAN went
error-passive about 128 pulses into a 200/s train and stopped reporting.)
Measured first, in this hold, by a hand probe (`deaf_probe/`; not a
check): in a 30 s TRAIN at 200/s the Kvaser's link went ERROR-PASSIVE 0.83
s in, and the witness still got one wire-class error frame per pulse,
5,998 for 5,998, 200 in every second, and counted a pulse after the train
once. A link bounce (canctl down, up 500000) took 0.080 s and returned it
to ERROR-ACTIVE. So the checks ran with **neither a reopen nor a bounce**;
the closing pulse after each train shows the witness still heard.

**The kernel log** over every judged check's window, by `bench/klog_scan.py`
(`klog_scan.json` in each run folder): no USB or CAN line in any of the 51
windows, the 19 of the runs above and the 32 of the two earlier runs below.

**Two earlier runs in the same hold,** on the same images and run folder:

- **Harness `f29ad2d1`** (`harness_f29ad2d1_*`): `jam-boot-silence`,
  `jam-port` and `jam-calibration` PASS and their can-fails FAIL, as above;
  every train check (`jam-stop` on all three images, `jam-cap`,
  `jam-stop-cmd`) and their can-fails NOT-RUN, "3 error frames for the
  closing pulse". A capture of every Kvaser error frame (`err_ids_probe/`)
  showed kvaser_usb sending a CAN_ERR_CNT-only frame (0x200, data all zero)
  every 512 ms while its controller is not error-active, as after a train;
  the witness counted them. Fixed in `e7583c19`: only error frames with a
  wire class are counted (its commit message has the judgement change).
- **Harness `e7583c19`** (`harness_e7583c19_*`): every check PASS and every
  can-fail FAIL except `jam-calibration` on dev, FAIL "9 at 1.1 s: 8
  groups, want 9": 19 error frames counted and one left out as having no
  wire class, 20 in all, as in every other `jam-calibration` run. One pulse
  came as an error frame with no wire class; which class is not known, as
  the witness did not yet keep the frames it leaves out. `6d6c78b0` records
  them. It did not recur: 38 pulses captured by hand in the same sequence
  (`cal_ids_probe/`) were all 0x288, and the 4 `jam-calibration` runs at
  `6d6c78b0` saw no left-out frame. In those runs the only frames left out
  were the 0x200 frames after trains (14 to 68 in each train check).

**Seen, not judged:** in `jam-stop-cmd` DONE reports `pulses=640` for a
train of `elapsed_us` 3,020,363, where the witness counted 603 error
frames in the train (in all four `jam-stop-cmd` runs: 640 against 603 or
604). For the trains that end at their deadline, DONE's count and the
witness's agree within 2.

After the hold the X2 was flashed back to the dev build (12:54) and read by
`N`/`V` at 12:55:00; the Kvaser's link was left down.

## Gaps

- **Jamming under the kernel needs `canx2` up normal.** Linux's slcan sends
  nothing the host writes while the interface is listen-only (the command
  counted in `tx_dropped`, no reply, 2026-11-13), so a listen-only replay
  cannot jam through it. The image takes the jam frame open listen-only on
  the raw tty (`jam-channel`).
- **The ESP32-S3 ROM's boot lines** reach a host holding the port at a
  reset; one (`rst:... boot:...`) is over 33 characters, so the kernel's
  slcan, up through a reset, counts one `rx_over_errors`. Turning them off is
  an eFuse burn.
- **The MCP2515's own receive overrun** is not provoked; `overrun` provokes
  the USB link's, reported as the same `e3ooo`.
- **When the MCP2515 stops ACKing after an S3 reset** is not measured
  (canchg reports error states on a timer of about 1 s); whether the S3's
  reset resets the MCP2515 stays unchecked (`README.md`, "Using it").
- **`attach`'s can-fail** breaks its expectation, not the bench (`bench/README.md`).
- **Listen-only reception** is not a check (`bench/README.md`).
- **One pulse seen as an error frame with no wire class** (run at
  `e7583c19`, above), class unknown; the witness now records such frames,
  so a recurrence names it.
- **DONE's `pulses` after a STOP** is not judged by any check (640 against
  the witness's 603, above).
