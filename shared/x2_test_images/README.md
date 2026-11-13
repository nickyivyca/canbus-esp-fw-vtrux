# x2_test_images/

Sources of the test-equipment images for the Autosport Labs ESP32-CAN-X2,
to `SPEC.md` (user, 2026-11-12). Test equipment, not product code: built
images are never committed; they go to Seafile with a manifest, and each
consumer pins them by hash (SPEC T2, T6, T9).

## Owners (the per-file exception to shared ownership, SPEC T4)

| Folder | Image | Owner |
|---|---|---|
| `jammer/` | CAN1 error-frame jammer, NOT FOR VEHICLE | gen-inhibit tester |
| `slcan_can1/` | SLCAN on CAN1 (TWAI): a transmitter, which does not forward what it receives; `S6` (500 kbit/s) and standard `t` frames only; `V`/`N` fixed `V0101`/`NF001`, which name the protocol, not the build, so T10's identity falls back to `verify_flash`; may go on the truck (T3a); see `slcan_can1/README.md` | charge-interposer tester |
| `slcan_spi/` | SLCAN on CAN2 (MCP2515), with CAN1 jamming (SPEC section 5, T13), NOT FOR VEHICLE; see `slcan_spi/README.md` | charge-interposer tester |
| `tools/` | not an image: `archive.py` (T6, T7), `check_image.py` and `flash_row.py` (T9-T11), for every image here, with their tests | the testers, as shared harness: either project's tester changes it on a `shared/x2_test_images/<topic>` branch, and that project's reviewer merges it (gen-inhibit reviewer, 2026-11-12) |
| `SPEC.md` | the specification | gen-inhibit reviewer, which drafted it (user rules on changes) |
| `README.md` | this file | the testers, each for its own rows; ownership rows by the reviewers |

Each image folder's sources move in on its owning tester's topic branch:
`jammer/` from `gen-inhibit/tools/x2_jammer/` (ported to ESP-IDF, T8),
`slcan_can1/` from the interposer's `slcan` variant (moved 2026-11-13,
`slcan_can1/README.md`), `slcan_spi/` new, first without its jamming (SPEC
T13).

## Idle-safe images (shared/bench SPEC section 8)

An image here may be marked idle-safe by its owning tester: safe to leave on
the X2 with nothing using it. Since 2026-11-13 a mark only decides whether a
bench release note opens with `LEFT ON IDLE-UNSAFE IMAGE: <board> on
<image>`; any holder may leave a board on any image (shared/bench SPEC
section 8, `shared/main` from `9342bf8b`). An image with no row here is not
marked.

| Image | Builds the mark covers | Idle-safe | Why | Marked by |
|---|---|---|---|---|
| `slcan_can1` | every build of `slcan_can1/` whose `test/test_app.c` and `test/test_slcan1_proto.c` pass, with `test/mutate.py` killing every mutant, at the build's commit; no tag build or pin yet | yes | SPEC T8a: nothing is transmitted before an `O`, a reset returns it to silent (it boots with CAN1's port set up and never brought up), and an `L` open never ACKs. The T8a check is `slcan_can1/test/test_app.c`, on the whole image on fakes of the port (`slcan_can1/README.md`, "Silent until told to open"); not yet run on the bench | charge-interposer tester, 2026-11-13 |
| `slcan_spi` | the builds of `slcan_spi/`'s dev variant with code hash `3f77031e` (`slcan_spi/VERIFICATION.md`, "The jamming build (T13)"); not `stop_a_only` or `stop_b_only`, and no other code hash until its own checks pass | yes | SPEC T8a on CAN2: nothing is transmitted before an `O`, a reset returns it to silent, an `L` open never ACKs (`silence`, `reset`); T13 on CAN1: it boots silent (`jam-boot-silence`: no CAN1 error frame from a reset to 5 s after the boot line, 3 of 3), it pulses only on a signed command frame on the reserved ID (`jam-channel`: a bad signature and the neighbouring ID refused), and every command is capped at 30 s (`jam-cap`); all on the bench for this code hash. Left alone it puts nothing on either bus. It is NOT FOR VEHICLE (T14) whatever this mark says | charge-interposer tester, 2026-11-13 |
