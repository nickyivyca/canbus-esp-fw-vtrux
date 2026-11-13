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
| `slcan_spi/` | SLCAN on CAN2 (MCP2515), with jamming on CAN1 to come (SPEC section 5), NOT FOR VEHICLE; see `slcan_spi/README.md` | charge-interposer tester |
| `SPEC.md` | the specification | gen-inhibit reviewer, which drafted it (user rules on changes) |
| `README.md` | this file | the testers, each for its own rows; ownership rows by the reviewers |

`slcan_spi/` is written here, first without its jamming (SPEC T13). The
other sources move in from `gen-inhibit/tools/x2_jammer/` and the
interposer's `slcan` variant on the owning testers' topic branches.
