# Build manifest

`manifest.json` maps the four bytes the board reports in `0x7F7` back to an
image. It is **generated**, never edited by hand: `firmware/manifest.py` runs
as a PlatformIO post-build action, so every build of every environment
updates its own row.

## Why it exists

`0x7F7` B0-B3 is the first four bytes of the application ELF's SHA-256. That
identifies a build precisely and tells you nothing on its own, because nothing
maps it back to a file. This is that table.

It matters most for the **witness** image. A board carrying the
completion-order instrumentation looks and behaves exactly like the bench
bridge, and which one is on the board decides whether an ordering measurement
means anything. Reading the build id off the wire and finding the row is how a
bench arm *proves* which image it is talking to instead of assuming.

The same sources produce a different ELF hash per environment, so the bridge
and witness images are already distinguishable on the wire with no schema
change — `0fc580b6` against `c4bcb52b` on 2026-10-05, from one source digest.

## The two identities, which are not interchangeable

| Field | Answers | Changes when |
|---|---|---|
| `src_digest` | *which sources produced this* | any hashed input changes |
| `elf_sha256` | *which exact image this is* | the sources **or the toolchain** change |

So `toolchain` is recorded in every row: identical sources under a different
platform or compiler version give different bytes, and without those versions
a hash mismatch would be unexplainable.

**`src_digest` covers** every `.c/.cpp/.h/.hpp/.S` under `src/`, plus
`platformio.ini`, `build_name.py`, `build_identity.py` and `manifest.py`,
hashed by path relative to `firmware/` and by content. **It does not cover**
docs, tests or notes — they cannot change the image, and hashing them would
churn the digest on every comment edit. `DIGEST_INPUTS` in
`../build_identity.py` is the authoritative statement of this; this paragraph
is a copy and the code is the source of truth.

There is **no git commit** in any of this, and no `-dirty` marker. This project
is not a git repository (the containing folder is literally `NotGit`), so there
is no commit to record and no committed baseline against which a tree could be
called dirty. The source digest does what a commit id was wanted for here.

## Fields

| Field | Meaning |
|---|---|
| `image` | the tagged filename, `interposer_<src_digest>_<tag>.bin` |
| `env` | PlatformIO environment |
| `tag` | variant: `bridge`, `witness`, `truck`, `selftest`, `diag`, `slcan` |
| `src_digest` | first 12 hex of the source hash (also in the filename) |
| `elf_sha256` | full ELF SHA-256, **read from `esptool image-info`**, not recomputed |
| `build_id_0x7F7` | the first 8 hex — exactly what `0x7F7` B0-B3 carries |
| `intp_fw_ver16` | `DIAG_FW_VER` from `machine.h`, as `0x7F7` B4-B5 reports it |
| `md5`, `size` | of the `.bin` |
| `built_utc` | build time |
| `toolchain` | platform and package versions |

`elf_sha256` is read from `esptool image-info` rather than derived another way
on purpose: the field's job is to match what the board reports, and the board
takes it from the same ESP-IDF app descriptor esptool is reading. Computing it
independently would record a number that *ought* to agree instead of the one
that does. If esptool does not report it the field is `null` and the row is
visibly incomplete — `build_check.py` fails on that rather than inventing one.

## Checks

`test/build_check.py` asserts, every run:

- every environment produced exactly one correctly tagged image, and only the
  witness environment's carries `_witness`;
- all images share one `src_digest` — a mismatch means a **partially rebuilt
  tree**, which is otherwise invisible;
- the `WITNESS BUILD` banner is in the witness image and in no other;
- every built image has a manifest row, so the manifest cannot silently lag;
- **no two entries share a `build_id_0x7F7` with different full hashes.** A
  32-bit prefix collision is unlikely and is the single way this table can lie
  — the lookup would name the wrong image, confidently. Verified to fail by
  injecting a collision, 2026-10-05.

## Reading it back from the wire

`../../build_lookup.py` reads `0x7F7`, decodes it through the DBC with
`cantools`, and returns the row. It **refuses** rather than guesses, with a
distinct reason for each way it can fail: unknown id, no `0x7F7` frames at all,
a prefix collision, and a missing or empty manifest. Those are four different
problems — a silent bus, an unrecorded build, an ambiguous table, and no table
— and collapsing them would make the commonest bench mistake unreadable.
