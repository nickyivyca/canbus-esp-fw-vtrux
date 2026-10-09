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

**Whether two images are the same is decided by `elf_sha256`, never by
`src_digest`** (user, 2026-10-08). The digest hashes whole files, comments
included, and covers inputs that cannot change the image at all, so it moves
while the bytes stay identical: in this manifest ELF `2e7ae2ef` appears under
four digests and ELF `18cec2dc` under eleven, because every source edit
anywhere in the tree moves the digest while an environment that compiles none
of the edited files relinks the same image. Measured directly on 2026-10-08:
appending one comment line to `src/machine.cpp` and rebuilding `esp32-can-x2`
with `-t clean` moved the digest from `bcd9518a1a10` to `08b6a5c1e49f` and
left the ELF at `2e7ae2ef` -- one image, two names, no difference in what
would be flashed. (Those rows' `built_utc` reads `2026-10-09` because it is
UTC and this bench is UTC-7; dates in prose here are the local working date.
Reading the date off a row is what put the wrong one in this file first.) The digest stays in use and stays in the filename; it
answers which sources went in, which is a different question and still worth
a row.

**`src_digest` covers** every `.c/.cpp/.h/.hpp/.S` under `src/`, plus
`platformio.ini`, `build_name.py`, `build_identity.py`, `manifest.py`
and `reproducible.py`, hashed by path relative to `firmware/` and by
content. **It does not cover**
docs, tests or notes — they cannot change the image, and hashing them would
churn the digest on every comment edit. `DIGEST_INPUTS` in
`../build_identity.py` is the authoritative statement of this; this paragraph
is a copy and the code is the source of truth.

**Each row also carries `git_commit` and `git_dirty`** (spec 8.2, user
2026-10-08), beside `src_digest` and not in its place: a build with
uncommitted changes shares its commit with one that has none, so the
commit says where to find the sources again and the digest says which
sources they were. `git_dirty` reflects the whole working tree with exactly one exclusion:
`builds/manifest.json`, which the build itself writes. Without that, every
build after the first recorded `dirty=true` on a clean tree -- this file is
written as a post-action, so the write lands after that build has already
asked `git status` and is still uncommitted when the next build asks (found
by the reviewer, reproduced by measurement, 2026-10-08). Nothing else is
excluded, not the rest of `builds/` and not other `.json` files. A row
carries an explicit `git_error` rather than a blank when git cannot
answer.

*Corrected 2026-10-08.* This section used to say "There is **no git
commit** in any of this, and no `-dirty` marker. This project is not a
git repository (the containing folder is literally `NotGit`), so there
is no commit to record and no committed baseline against which a tree
could be called dirty." That was true while the tree lived on
SeaDrive; it moved into `canbus-esp-fw-vtrux` on 2026-10-08 and the
rows have carried both fields since.

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
| `git_commit` | the commit the working tree was at |
| `git_dirty` | uncommitted changes anywhere except to this file |
| `git_error` | present instead of those two when git could not answer |
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
