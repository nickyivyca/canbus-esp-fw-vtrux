# E3 — build-output checks (spec 12.3 item 3 / 12.4 row 22)

These run on **the image that will be flashed**, not on the source. The WiCAN
OBD has **no external USB port** and no factory reset, so a bad image is
recoverable only by bootloader rollback or by opening the case -- the ESP32-C3's
native USB-serial/JTAG is on the board and reachable once it is off, which is how
the bench unit is flashed (COM4, MAC d4:f9:8d:1d:0d:74). Neither is a field
option on a dongle plugged into a truck, which is what makes every one of these
checks a guard against a way to lose a device. *Corrected 2026-09-27 (user): this
said "no USB port ... recoverable only by soldering to UART0 or by bootloader
rollback", which overstated it -- and a rationale that overstates its own stakes
is the kind that gets discounted when it matters.*

| File | What it checks |
|---|---|
| `build_output.py` | The whole of row 22: sdkconfig assertions, the embedded version, image size against the OTA slot, the partition table against stock, and the diag schema/DBC agreement. Also **refuses non-shipping builds**: the three measurement instruments, and (since 2026-10-04) the `OTA_HEALTH_FAULT_INJECT` rollback-rehearsal image, which rolls itself back 60 s after every boot. Those have to be content checks, because two configurations of one commit share both the filename and the embedded version. |
| `config_vs_spec.py` + `config_probe.c` | `gi_config_defaults()` against the numbers the **spec** states, each row citing its section. |
| `elf_checks.py` | `slcan_parse_str` is not linked into the image. |
| `test_prod_record.py` | Tests `check_prod_record()` against spec 12.3 item 3: no `<image>.prod.json` is NO REFERENCE ("not a prod build"), a present record that disagrees with the image or cannot be read is a FAIL. Fails against the pre-2026-10-07 behaviour, where a missing record was a FAIL. Owned by the gen-inhibit tester. |
| `test_flt_row.py` | Tests the "project name is not a rehearsal build" row: `_flt` is refused as a `_`-delimited token anywhere in `project_name`, not only as the last suffix (a `_DIAG` after it hid it from the old `endswith`). Fails against the old predicate. Owned by the gen-inhibit tester. |
| `reference/` | Fixtures a check compares against — see below. |

## Running them

```bash
python3 build_output.py                  # newest build/wican-fw_obd_*.bin
python3 build_output.py --bin build/wican-fw_obd_abc1234.bin
make config_probe && python3 config_vs_spec.py
python3 elf_checks.py
python3 test_prod_record.py             # tests the prod-record row itself
python3 test_flt_row.py                 # tests the rehearsal-name row itself
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
