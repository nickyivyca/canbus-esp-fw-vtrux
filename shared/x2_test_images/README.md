# x2_test_images/

Sources of the test-equipment images for the Autosport Labs ESP32-CAN-X2,
to `SPEC.md` (user, 2026-11-12). Test equipment, not product code: built
images are never committed; they go to Seafile with a manifest, and each
consumer pins them by hash (SPEC T2, T6, T9).

## Owners (the per-file exception to shared ownership, SPEC T4)

| Folder | Image | Owner |
|---|---|---|
| `jammer/` | CAN1 error-frame jammer, NOT FOR VEHICLE | gen-inhibit tester |
| `slcan_can1/` | SLCAN on CAN1 (TWAI) | charge-interposer tester |
| `slcan_spi/` | SLCAN on CAN2 (MCP2515), with CAN1 jamming (SPEC section 5), NOT FOR VEHICLE | charge-interposer tester |
| `tools/` | not an image: `archive.py` (T6, T7) and `check_image.py`, for every image here, with their tests | the testers, as shared harness: either project's tester changes it on a `shared/x2_test_images/<topic>` branch, and that project's reviewer merges it (gen-inhibit reviewer, 2026-11-12) |
| `SPEC.md` | the specification | gen-inhibit reviewer, which drafted it (user rules on changes) |
| `README.md` | this file | the testers, each for its own rows; ownership rows by the reviewers |

Each image folder's sources move in on its owning tester's topic branch:
`jammer/` from `gen-inhibit/tools/x2_jammer/` (ported to ESP-IDF, T8),
`slcan_can1/` from the interposer's `slcan` variant, `slcan_spi/` new.
