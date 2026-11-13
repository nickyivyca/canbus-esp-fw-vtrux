# slcan_spi/bench/ -- NOT FOR VEHICLE

The charge-interposer tester's checks of slcan_spi against
`../../SPEC.md` section 5 and T8a: committed harness, each with its
can-fail (AGENTS.md "Spec, validation and verification").

| File | What |
|---|---|
| `check_image.py` | no board: the image name says NOT FOR VEHICLE (T14), the image's ELF hash is its ELF's (T10, T12), and no TWAI symbol is linked (T13, this build); `--can-fail` runs each on a broken input, an object defining `twai_driver_install` compiled with ESP-IDF's S3 compiler among them |
| `slcan_spi_bench.py` | on the bench, with slcan_spi on the X2: `identity`, `attach`, `silence`, `reset`, `rate-tx`, `rate-rx`, `drops`, `busoff`, `overrun` (its docstring says what each shows); `--can-fail <check>` runs one check with one condition broken on the bench, which it must then FAIL |

## Running the bench checks

On `madhouse-debian`, with the bench lock held by `tester
(charge-interposer)`, `canmain` down, and slcan_spi on the X2, flashed
through the shared flasher (SPEC T11), in `bench.slice`:

    systemd-run --user --scope --slice=bench.slice \
      python3.13 -B bench/slcan_spi_bench.py \
        --settings <the tester's bench settings, with esptool.device> \
        --shared-bench <worktrees>/shared-<tag>/shared/bench \
        --elf build/dev/slcan_spi_NOT_FOR_VEHICLE.elf \
        --out <a run folder outside any git worktree>

The charger segment must hold only `canchg` and the X2's MCP2515. The script
drives both through `canctl` and leaves `canx2` detached and `canchg` down.

## Gaps

- **The MCP2515's own receive overrun** is not provoked: the image keeps up
  with the bus. `overrun` provokes the other overrun the image reports,
  frames the USB link had no room for, which reach the host as the same
  `e3ooo`.
- **Listen-only reception** is not shown: with no third node on the
  charger segment to ACK canchg's frames, canchg's own error flag destroys
  each frame for every receiver. `silence` shows what T12 requires of `L`,
  that it never ACKs.
- **The ROM's boot lines** (`../README.md`): `reset` records them apart
  from the image's output, which starts at its boot line and is judged;
  `attach` allows for the one of them the kernel counts.
- **When the X2's MCP2515 stops ACKing after a reset** is not measured:
  canchg reports error states on a timer of about 1 s, so `reset`'s
  `first_ack_error_after_reset_s` bounds nothing finer. The image's boot
  line comes about 0.1 s after the reset, and the image resets the MCP2515
  before sending it; whether the S3's reset itself resets the MCP2515
  stays unchecked (`../README.md`, "Using it").
- **`attach`'s can-fail** breaks its expectation, not the bench: no bench
  condition makes the image send a long unprompted line, so the ROM's
  long line, not allowed for, is the line that must fail it.
- **canchg's ACKs** are judged from its state-change error frames
  (`probe_ack`): its gs_usb refuses `berr` reporting and counts
  `tx_packets` for a frame whether or not it was ACKed.
