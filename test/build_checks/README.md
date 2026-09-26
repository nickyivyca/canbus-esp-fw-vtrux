# E3 — build-output checks (spec 12.3 item 3 / 12.4 row 22)

These run on **the image that will be flashed**, not on the source. The WiCAN
OBD has no USB port and no factory reset, so a bad image is recoverable only by
soldering to UART0 or by bootloader rollback — which means every one of these
checks is guarding against a way to lose a device.

| File | What it checks |
|---|---|
| `build_output.py` | The whole of row 22: sdkconfig assertions, the embedded version, image size against the OTA slot, the partition table against stock, and the diag schema/DBC agreement. |
| `config_vs_spec.py` + `config_probe.c` | `gi_config_defaults()` against the numbers the **spec** states, each row citing its section. |
| `elf_checks.py` | `slcan_parse_str` is not linked into the image. |
| `reference/` | Fixtures a check compares against — see below. |

## Running them

```bash
python3 build_output.py                  # newest build/wican-fw_obd_*.bin
python3 build_output.py --bin build/wican-fw_obd_abc1234.bin
make config_probe && python3 config_vs_spec.py
python3 elf_checks.py
```

`build_output.py` takes `--dbc` and `--schema-doc` because both live in the
analysis project rather than in this repo. A missing one is reported as
**NO REFERENCE**, never as a pass.

## Two rules these files follow

**A check with no reference says so.** Where a comparison needs something this
repo does not hold, the row prints `NO REF` and the summary counts it
separately. That is the same discipline `config_vs_spec.py` was corrected into
on 2026-09-25, when a row cited spec 6.2 for a number section 6.2 does not
state: *a table whose rows do not all mean the same thing is worse than no
table*, because the rows that do mean something stop being distinguishable.

**Only the enables count.** This build sets `CONFIG_SECURE_BOOT_V2_RSA_SUPPORTED`
and `CONFIG_SECURE_BOOT_V2_PREFERRED` — a capability and a preference, not the
feature. A check that tested those would fail a perfectly correct image. The
enables are `CONFIG_SECURE_BOOT` and `CONFIG_SECURE_FLASH_ENC_ENABLED`.

## The version comes from the image, never the filename

ESP-IDF fixes the output filename at **configure** time, so
`wican-fw_obd_37addf4-dirty.bin` can contain a build of something else
entirely. The authoritative string is in `esp_app_desc_t` at offset `0x20` of
the app image, and that is what the `-dirty` and git-sha checks read.

Getting the offsets wrong there produced the kind of wrong that looks like
data: the first version read `0x60` for `idf_ver` and reported
`idf='Sep 25 2026'`, which is the `date` field. The layout is `0x00` magic,
`0x04` secure_version, `0x08` reserv1[2], `0x10` version[32], `0x30`
project_name[32], `0x50` time[16], `0x60` date[16], `0x70` idf_ver[32].

## The checker was proved in both directions

A check nobody has seen fail is not a check, and that applies to these as much
as to the test suites. The `-dirty` row was **observed failing** on
`wican-fw_obd_bea8d51-dirty.bin` — a real artifact built from an uncommitted
tree — and then **observed passing** on `wican-fw_obd_8896311.bin`, built clean
from `8896311`. Two of the rows were over-claiming on that first run and were
corrected rather than blessed:

- the DBC row demanded every `GI_*_ID` including `GI_PROBE_ID`, and failed a
  correct DBC. `0x7F0` is the RESPOND mode's timing measurement, not a status
  page; row 22 says *diag frames*, and `vtrux-wican-diag.dbc` deliberately
  defines only the four pages.
- the project-name row demanded equality with `wican-fw`, which this build
  system never produces — it embeds the git describe.

## `reference/`

| File | Provenance |
|---|---|
| `stock-v4.13-partition-table.bin` | Extracted from `wican-config/restore-v4.13/wican-fw_obd_v413.zip` in the analysis project — the stock MeatPi v4.13 release. 3072 bytes, sha256 `98d193a613ff7e9d…`. This is what "byte-identical to stock" is measured against; a device flashed with a differing table loses its recovery layout. |

## What row 22 still cannot check here

Nothing, as of 2026-09-26 — all nineteen rows report a real verdict against a
real reference. The remaining E3 gap is not in this directory: it is that these
checks are not yet wired into the release step, so they have to be run by hand
before a flash.
