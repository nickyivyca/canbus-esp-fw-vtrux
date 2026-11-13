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

## The build

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

## Gaps

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
