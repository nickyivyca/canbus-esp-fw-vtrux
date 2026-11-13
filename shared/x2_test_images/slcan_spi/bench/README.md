# slcan_spi/bench/ -- NOT FOR VEHICLE

The charge-interposer tester's checks of slcan_spi against
`../../SPEC.md` section 5 and T8a: committed harness, each with its
can-fail (AGENTS.md "Spec, validation and verification"). A built image is
checked off the board by the shared `../../tools/check_image.py` with
`../x2_image.json`, whose can-fails are its own tests (`../../tools/`).

| File | What |
|---|---|
| `slcan_spi_bench.py` | on the bench, with slcan_spi on the X2: `identity`, `attach`, `silence`, `reset`, `rate-tx`, `rate-rx`, `drops`, `busoff`, `overrun`, and the jamming checks (T13) `jam-channel`, `jam-concurrent`, `jam-boot-silence`, `jam-port`, `jam-calibration`, `jam-stop`, `jam-cap`, `jam-stop-cmd` (its docstring says what each shows); `--can-fail <check>` runs one check with one condition broken on the bench, which it must then FAIL |
| `jam_host.py` | the jam channel's host side, written from the format (`../main/jam_frame.h`): command frames, the reply joiner, reply parsing |
| `witness.py` | CAN1's witness, the Kvaser: since 2026-11-13 the kernel's kvaser_usb, netdev `kvaser`, python-can's socketcan after its link reads up in sysfs (until then CANlib through a can-tools pin's canbench, `--can-tools`); its collecting thread, which never lets a failed or ended witness read as a quiet bus, and which leaves out an error frame reporting only a controller state change, by its CAN_ERR class (`state_only`; the charge-interposer reviewer's C), and counts only an error frame with a wire class (`on_wire`), so kvaser_usb's counter-only frame, sent every 512 ms while its controller is not error-active, is counted apart (`non_wire`); and the pure judges of what it saw: its clock against the host's, pulse groups, stops at a deadline and their windows, counts, the closing pulse; and each judged window's margin over the host stamps' measured error (`stamp_margin`: at least 10 times about 1.5 ms, the gen-inhibit reviewer's ruling of 2026-11-13, relayed by the charge-interposer reviewer), which every witness check writes into its detail as `stamp_margins` and checks before it touches the bench, NOT-RUN if one is under |
| `test_jam_host.py`, `test_witness.py` | the tests of those two, no hardware (`test_witness.py` runs the collecting thread on a fake bus and the opener on a stand-in sysfs), each with `--mutants`, its can-fail (8 and 31 mutants, made in memory) |

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

The jamming checks from `jam-boot-silence` on also need CAN1's witness, the
bench's Kvaser on the kernel's kvaser_usb (netdev `kvaser`, canctl's
default mode since 2026-11-13): the script brings its link up through
`canctl up kvaser 500000` and the witness reads IFF_UP back before it
opens; `--can-tools` is no longer read. They put pulses on CAN1, where the
WiCAN is, and the release note says so, with the Kvaser's link left up. `jam-stop` is run once on each image, dev, `stop_a_only` and
`stop_b_only` (`--elf` the image's), each flashed in turn. Each witness
check ends with one closing pulse the witness must count exactly once, so a
deaf witness (wrong segment, cable off, an adapter failure mid-check) makes
the check NOT-RUN instead of passing a quiet window (the gen-inhibit
tester's review, 2026-11-13).

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
