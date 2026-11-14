# jammer -- test results -- NOT FOR VEHICLE

**Owner:** gen-inhibit tester (x2_test_images SPEC T4).

Test evidence that a build of `jammer` meets `../SPEC.md` T8 and the bench
checks `README.md` lists ("Bench checks the port must pass"). Each entry
names what was tested, the build, the harness commit, the result and the run
files. A pass carries to a build with the same code hash (shared/tools R3)
and to nothing else. The PlatformIO jammer's passes (`a89ece8c`,
2026-10-08) carry to none of these builds (SPEC T8). The harness is
gen-inhibit's, `gen-inhibit/tools/bench/x2_jammer.py` and
`x2_flash_custody.py`, at the commit each entry names. Run files are under
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/`. The X2
is the bench's one ESP32-CAN-X2; its MAC is in
`$REVERSE_IT_DIR/firmware/bench-holder.md` (bench identifiers are never in
git).

## The port's first builds (`9e017baa`)

Dev builds (T7, not pinnable) from `9e017baa` on
`shared/x2_test_images/jammer-idf`, since merged into `shared/main` with
`jammer/` unchanged. Made by the shared tooling (`vtrux_build build
<variant>`), ESP-IDF v5.4.2, on madhouse-debian, 2026-11-13 08:06-08:07 UTC,
version `2026.11.12.2-6-g9e017baa`. They are in the image's manifest
(`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/x2_test_images/jammer/manifest.jsonl`),
by row (app SHA-256 prefix):

| Variant | Row | Project name | ELF SHA-256 | Code hash |
|---|---|---|---|---|
| `jammer` | `cb5cab143a23f645` | `vtrux_jammer_x2_NOT_FOR_VEHICLE` | `ebad24f8b4c2eeb60f05f974b4dcd24fe74bab8fd1d13e6d0d31792b0259c561` | `4d1241e5026e00a554623a9d6167f449ed1010aaae55094f7bdf868d28f55d3c` |
| `stop_a_only` | `345ac249ca297e1e` | `jammer_stop_a_NOT_FOR_VEHICLE` | `3659f5e9fb955fe82e5db7b0966007c34078d10b6382d7c0d618353c1ed34aa1` | `c4b0398c98a19f7d0e8e64e0bdb11d72b1991ae615ffdf69e5946d246a565949` |
| `stop_b_only` | `185f22b8726ecc32` | `jammer_stop_b_NOT_FOR_VEHICLE` | `b4a077bdb17d0e09f22425bbade32b4a36082633b340344904b0faa8dc40dbbd` | `b5f5cbbf1756581b6e79a1c21c621bc72cbf08ce306f9494efc20ef521f1805b` |

### Off the board

- **`../tools/check_image.py`**, in each row at archive time: PASS, all
  eleven checks (app image, chip, project name, variant, NOT_FOR_VEHICLE,
  banner strings, ELF hash, symbol table, no forbidden TWAI symbol, config,
  build metadata).

### Witness and method (both bench entries below)

The PCAN-USB FD on CAN1 (the WiCAN's segment), normal mode (ACKing), is
the timing witness. Its hardware stamps are placed on the host clock by one
anchor per PCAN open, which is VOID when `|slope| x span` > 0.05 s, when
p99(receipt - mapped) >= 0.25 s, or with fewer than 10 frames. Every judged
error frame must fall between its command's send and the next command's
send (the bracket; a frame outside VOIDs the check). The jammer's replies
are never judged, only what the witness delivered. CAN2 isolation is
witnessed on the Kvaser (kvaser_usb, SocketCAN) and `canchg` instead,
since the PCAN sends there (gen-inhibit reviewer, ruling (C),
2026-11-13). Every bench step ran in `bench.slice`; the WiCAN was not
touched (`--no-wican`); `canmain` was down.

**Identity** (T10). The banner's `elf=` gives 9 hex digits, not the 64
that `README.md` promises (observed vs README: `elf=ebad24f8b`,
`elf=3659f5e9f`, `elf=b4a077bdb`, each a prefix of its row's ELF SHA-256).
So the banner does not name the build, every `id --expect-elf` verdict is
FAIL on that field alone, and identity falls back to esptool's
`verify_flash` against the row's files plus the otadata read (gen-inhibit
reviewer, ruling (a), 2026-11-13). Every name line, `version=`,
`twai=never_started` and `config_mode=yes` matched. Identity is the
committed `x2_flash_custody.py verify-row` read of each row, below ("On the
bench, 2026-11-13 19:18-19:19: identity"). The `verify_flash` reads in the
two entries between ran through a scratch glue (`x2_verify_row.py`), which
is not committed harness, so they are context only.

## On the bench, 2026-11-13 11:30-11:41: the untimed checks (harness `e61f4030`)

The X2 on the `jammer` row since 10:08 (flashed by `x2_flash_custody.py
flash-jammer --no-dump`, not flashed again before this hold). Hand take
by the gen-inhibit tester. Run folder `jammerbench_e61f403/` (its README
and SHA256SUMS). Context only (scratch glue, above): `verify_flash`
11:39:11, bootloader, partition table and app digests matched; otadata
differs from `ota_data_initial.bin`. Identity: the 19:18 entry below.

| Check | Requirement | Result | Its can-fail |
|---|---|---|---|
| `boot-silence --resets 3` | T8 boots silent: no witness error frame from each reset to banner + 5 s, 3 of 3 | PASS | the closing pulse after the silence, seen once (else INCONCLUSIVE) |
| `pulses --n 1` | port: one pulse gives one error frame on CAN1 | PASS | closing pulse seen once |
| `pulses --n 10 --interval-ms 1000` | calibration: 10 groups of one error frame, none before, none after DONE | PASS (session 2) | closing pulse seen once; bracket margins after-send 2.4 ms, before-DONE 15.5 ms at least |
| `pulses --n 9 --interval-ms 1100` | calibration: 9 groups of one | PASS (session 2) | closing pulse; after-send 2.4 ms, before-DONE 25.9 ms |
| `noduration` | a command without a duration is refused: `JAMMER ERR no_duration`, 0 error frames | PASS (session 2) | closing pulse seen once |
| `can2-isolation --n 5 --interval-ms 1000` | T8 MCP2515 neither transmits nor ACKs: canchg (normal, ACKing) saw nothing from the jammer through 5 pulse groups ([1, 1, 1, 1, 1] on the Kvaser), and canchg's own frame `0x7A1` got no ACK | PASS | the PCAN's 50 `0x7A0` all seen on the Kvaser and none on canchg; the no-ACK probe ACKed with the X2 on an SLCAN image open on CAN2 (`jammerbench_6fc4067/`) |

Session 1's anchor: n 134, slope -10.5 ppm, drift 0.16 ms, p99 0.254 ms.
Session 2's: n 22, slope 4.7 ppm, drift 0.18 ms, p99 0.254 ms. Neither VOID.
Session 1's timeout checks and its own pulse trains do not count: after two
controller-state frames in the first train the PCAN reported nothing more
(witness deaf, gen-inhibit reviewer, 2026-11-13). The trains were run
again below.

## On the bench, 2026-11-13 18:45-18:50: the stops (harness `34f25c31`)

One PCAN open per train, called 0.4 s before the train's expected stop
(the host's send time plus the effective ms from the jammer's ACK, 30000 for
a capped 60000). A warning, passive or bus-off controller-state frame would
put the witness down (gen-inhibit reviewer, ruling (3), 2026-11-13). Hand
take by the gen-inhibit tester. Run folder `jammerbench_34f25c3/` (its
README and SHA256SUMS). An earlier take from `f0c6e03d`
(`jammerbench_f0c6e03/`) stopped on a harness defect and counts for
nothing.

Each row flashed by `x2_flash_custody.py flash-jammer --no-dump` (esptool
rc 0), then otadata read by `read-otadata` (PASS each: selects app0, entry
0, ota_seq 1, VALID, the row's app at `0x10000`), the banner, its session,
and `verify_flash` last (bootloader, partition table and app digests
matched for each row; otadata differs, the VALID entry the app writes;
context only, scratch glue). Identity: the next entry.

| Check | Row | Requirement | Result | Its can-fail |
|---|---|---|---|---|
| `timeout --p 5000 --ms 2000` | `jammer` | stop at the deadline: 44 error frames to the deadline, 0 after deadline + 0.3 s, watched 5 s past it; last frame 20.8 ms before the deadline; 0 from DONE to the closing pulse | PASS | the same record judged against a 1000 ms deadline: FAIL |
| `timeout --p 5000 --ms 60000` | `jammer` | the 30 s cap: `CAPPED ms 60000 -> 30000`, ACK ms 30000; 70 frames to the 30 s deadline, 0 after; last 44.7 ms before | PASS | judged against 29000 ms: FAIL |
| `timeout --p 5000 --ms 2000 --host-quiet` | `jammer` | stop at the deadline with the host silent on the console: 71 to the deadline, 0 after; last 33.4 ms before | PASS | judged against 1000 ms: FAIL |
| `timeout --p 5000 --ms 2000 --expect-stop-by deadline_timer` | `stop_a_only` | stop (a) alone: DONE `end=stopped stop_by=deadline_timer`; 47 to the deadline, 0 after; last 16.5 ms before (within 0.5 s, `README.md`) | PASS | judged against 1000 ms: FAIL |
| `timeout --p 5000 --ms 2000 --expect-stop-by none --expect-end deadline_layer_b` | `stop_b_only` | stop (b) alone: DONE `end=deadline_layer_b stop_by=none`; 46 to the deadline, 0 after; last 21.4 ms before | PASS | judged against 1000 ms: FAIL |

Every closing pulse was seen once. No frame fell outside its bracket or
outside any check, and no controller-state frame came in any open. The
witness was open 0.22-0.36 s before each expected stop. Anchors: n 45-72,
slope 12.0-19.4 ppm (stop_a's open 99.7 ppm), p99 spread 0.136-0.879 ms;
none VOID. Over each check's window the resource record showed
bench.slice at 0.8 cores, its CPU pressure 0.2 % at most and 0-0.5 major
faults/s (each window's figures are in the run README). No kernel USB line
appeared for the adapters' hub.

## On the bench, 2026-11-13 19:18-19:19: identity (harness `5a35ebcb`)

T10's identity for the three rows above, with committed harness (gen-inhibit
reviewer, 2026-11-13): `x2_flash_custody.py verify-row` runs esptool
`verify_flash` of every file of the row (each must match except the one at
the row's otadata partition, which the app marks VALID at first boot), then
`read-otadata` (which must select the row's app), then esptool `run`. Its
test (`test_x2_flash_custody.py`, 147 checks) includes the can-fail: another
row's build on the flash FAILs; its mutants are 90 of 90 killed. Hand take
by the gen-inhibit tester; esptool only, nothing sent on any bus. Run folder
`jammeridentity_5a35ebc/` (its README and SHA256SUMS).

| Row | Variant | Flashed (`flash-jammer --no-dump`) | `read-otadata` | `verify-row` |
|---|---|---|---|---|
| `cb5cab143a23f645` | `jammer` | 19:18:41 | PASS: app0 at `0x10000`, ota_seq 1, VALID | PASS: bootloader, partition table, app matched; otadata selects the row's app; `run` exit 0 |
| `345ac249ca297e1e` | `stop_a_only` | 19:18:55 | PASS, the same | PASS, the same |
| `185f22b8726ecc32` | `stop_b_only` | 19:19:02 | PASS, the same | PASS, the same |

The can-fail on the bench, 19:19:18: `verify-row` of the jammer row with the
X2 on the stop_b row: FAIL (bootloader and app differ). Each row's
`verify_flash` output is kept whole in the run folder.

## The tag's builds (`2026.11.13.7`): the passes above carry by code hash

Built by `../tools/archive.py build . <variant> --builder "gen-inhibit
tester"` (T6, T7), in a worktree of the gen-inhibit tester's own detached
at tag `2026.11.13.7` (`feaced29`) with a clean tree, ESP-IDF v5.4.2, on
madhouse-debian, 2026-11-14 03:03-03:05 UTC, version `2026.11.13.7`. Rows
in the same manifest:

| Variant | Row | ELF SHA-256 | Code hash | Pinnable |
|---|---|---|---|---|
| `jammer` | `ea762c62044cb5c6` | `ae2783e25ad536ab5f56cb3725d4d205fd5ee42f47543e5a4e9996e0f2cf3fc9` | `4d1241e5026e00a554623a9d6167f449ed1010aaae55094f7bdf868d28f55d3c` | yes: every pinnable check true |
| `stop_a_only` | `badac5b632f4a142` | `c4f6a637c21ab8a3649de327ef1501238159a5d24064205950856e8aa9577999` | `c4b0398c98a19f7d0e8e64e0bdb11d72b1991ae615ffdf69e5946d246a565949` | no: the variant is never pinnable (`README.md`); every other check true |
| `stop_b_only` | `075ce70c5de105ed` | `8b04eee7871599dce130f6766520d741a68849438be003dd61e8b9494ea2430c` | `b5f5cbbf1756581b6e79a1c21c621bc72cbf08ce306f9494efc20ef521f1805b` | no: as `stop_a_only` |

`check_image.py` at archive time: PASS, all eleven checks, in each row. Each
row's code hash equals its variant's `9e017baa` row's (table at the top), so
every pass above carries to it (shared/tools R3, gen-inhibit reviewer
2026-11-13), and nothing was run on the bench for these rows. Their ELF and
image hashes differ from the `9e017baa` rows': the image hash includes the
version string, which the code hash blanks (R1, R2, R5).

## Not yet shown

- **gen-inhibit's B5-B8 on the ported image** (`README.md`, the last
  bench check). They need the WiCAN, which madhouse-debian reaches only
  over Wi-Fi, and it has no working Wi-Fi adapter yet. gen-inhibit keeps
  pinning the PlatformIO jammer `a89ece8c` until they pass (SPEC T8).
- **The banner's whole ELF hash** (above): the image prints 9 hex digits.
