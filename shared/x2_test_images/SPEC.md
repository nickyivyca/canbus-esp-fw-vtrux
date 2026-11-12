# X2 test images: specification

Approved by the user 2026-11-12, in the gen-inhibit reviewer's session,
which drafted it; read by the charge-interposer reviewer before merge
(AGENTS.md "Shared code"). From the user's ruling of 2026-11-12, in the
methodology owner's session: the X2 test-equipment image sources move into a
shared component, so that CAN replay tooling and the Android sessions can pin
a shared tag, then build and flash these images themselves. The ESP sessions
still develop them. Until the move, no spec covered these images; only the
jammer's README, `slcan_main.cpp`'s header and the interposer's variant table
described them.

## 1. What the component holds

- **T1. Sources only.** `shared/x2_test_images/` on `shared/main` holds the
  source of each test image for the Autosport Labs ESP32-CAN-X2, one folder
  per image:
  - `jammer/`: the CAN1 error-frame jammer, from
    `gen-inhibit/tools/x2_jammer/`;
  - `slcan_can1/`: the SLCAN on CAN1 (TWAI), from the charge interposer's
    `slcan` variant (`charge-interposer/firmware/main/slcan_main.cpp`);
  - `slcan_spi/`: the SLCAN on CAN2 (the MCP2515 on SPI), when it is written
    (port plan step 3, stage (b)). It is written here and is never an
    interposer variant.
- **T2. No images in git.** Built images go to Seafile, each build in its own
  folder with an append-only manifest row (T6). Git holds the code only.
- **T3. Test equipment.** None of these images is product code, and none is
  built into, or flashed by, either project's product build.
- **T3a. On the truck** (user, 2026-11-12, here). The SLCAN images may go on
  the truck for testing during bring-up and installation. They are never
  flight images. No formal protection enforces this; it is a decision on
  record. The jammer stays NOT FOR VEHICLE (T8).

## 2. Who owns what

- **T4. Testers own the images, per image** (test equipment, under the
  ownership split): `jammer/` is the gen-inhibit tester's; `slcan_can1/` and
  `slcan_spi/` are the charge-interposer tester's. The component's
  `README.md` records this as its per-file exception. `slcan_can1/` was the
  charge-interposer implementor's product code until the move; from the move
  it is the charge-interposer tester's.
- **T5. Changes** follow the shared code rules: the project that needs a
  change makes it in its tester's role on a `shared/x2_test_images/<topic>`
  branch, and that project's reviewer merges it. CAN replay tooling and the
  Android sessions build and flash from a pinned tag, and send any change
  they need to the owning tester.

## 3. Builds

- **T6. One toolchain.** Every image builds on ESP-IDF v5.4.2 through the
  shared build tooling (`shared/tools`), whose IDF environment check (R32)
  runs first. A build records, in the image folder's manifest: the shared
  commit and tag it was built from, the code hash, and for each file its
  flash offset, name, size, sha256 and md5, and the app's ELF sha256. Rows
  are appended, never replaced.
- **T7. Only a tag's build is pinned.** A build from a shared tag's
  read-only checkout, with a clean tree, may be pinned (T9). Any other build
  is a dev build, for its builder's own bench work only.
- **T8. The jammer moves to ESP-IDF in the move** (user, 2026-11-12, here,
  approving the reviewers' default; and in the methodology owner's session,
  "lean towards yes, using shared codebase when possible": the shared board
  and port layers and the shared build tooling, where they fit). Its
  PlatformIO build retires. A
  ported jammer is new code, so its passes do not carry (AGENTS.md "Spec,
  validation and verification"): gen-inhibit keeps pinning the current
  PlatformIO jammer image until the port has passed the bench checks that
  gen-inhibit's bus-off and trip-threshold cases rest on, run by the
  gen-inhibit tester on the ported image. The jammer's safety properties are
  requirements of the port, each kept and checked:
  - it never sends a CAN frame, and its TWAI controller is never started;
  - at boot it resets the MCP2515 into configuration mode, where it neither
    transmits nor ACKs, and reports CANSTAT in its banner;
  - "NOT FOR VEHICLE" is in its image name, its boot banner and every
    console line.
- **T8a. Each SLCAN image is silent until told to open,** as
  `slcan_main.cpp` is today: nothing is transmitted before an open command,
  a reset returns it to silent, and a listen-only open never ACKs.

## 4. Pins and flashing

- **T9. Consumers pin by hash.** A project, CAN replay tooling or an Android
  session uses an image only through a pin that names the shared tag, the
  manifest row and every file's size and hashes, as
  `gen-inhibit/tools/x2_pin.json` does today, and refuses files that differ.
  Each consumer owns its pin.
- **T10. Identity.** A consumer confirms which image a board runs before it
  relies on it: by the image's own identity answer where it gives one that
  names the build, otherwise by esptool's `verify_flash` against the pinned
  files.
- **T11. Flashing** goes through the shared flasher (`shared/bench`), to the
  X2's own port resolved by its USB identifiers (`DEVICES-SPEC.md` S1),
  never the WiCAN's. Who may flash the X2 under a hand take is
  `shared/bench/SPEC.md` section 8's, still with the user.
</content>
</invoke>
