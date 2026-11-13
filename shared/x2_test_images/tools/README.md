# x2_test_images/tools/

Shared harness for the X2 test images: checks a build and archives it with
its manifest row (x2_test_images SPEC T6, T7), and flashes an archived
build from its row (T9-T11). Owner: the testers, as
shared harness (gen-inhibit reviewer, 2026-11-12). Either project's tester
changes these files on a `shared/x2_test_images/<topic>` branch, and that
project's reviewer merges it. Generic over the image folder and its
variants: everything image-specific is in `<image>/x2_image.json`.

| File | What |
|---|---|
| `check_image.py` | checks one build against its `x2_image.json`: the image parses, its chip, its variant read from its own `project_name` (exactly one variant's, and the one offered), NOT_FOR_VEHICLE in the name where the image needs it, the banner strings in the `.bin`, the `.elf` hash against the descriptor's, the symbol table (read, non-empty, required symbols), no forbidden symbols, the required config values. Exit 0 PASS, 1 FAIL, 2 REFUSED |
| `archive.py` | `build`: the git state, the shared tooling's dev build, the git state again, `check_image.py`, then the files and one appended row in Seafile; `pinnable` only for a pinnable variant built at a shared tag from a clean tree whose version is the tag. Exit 0 archived, 2 refused |
| `flash_row.py` | flashes one archived build onto the bench X2 from its manifest row (SPEC T9-T11): the row, every file's size, SHA-256 and MD5, the offsets and the app's descriptor checked against the row first, then the same checkout's shared/bench esptool flasher. Exit 0 flashed (or checked), 1 the flash failed, 2 refused, nothing written |
| `test_tools.py` | the three tools' tests on stand-in builds (real formats, small), each check shown failing, the flasher driven through a stand-in esptool; under `<worktree>/scratch/x2-tools-tests/<pid>/`, removed at the end |
| `mutate.py` | breaks one check at a time (37 mutants) and checks `test_tools.py` fails on each |

The tools import `vtrux_build.image` from the same checkout's
`shared/tools`, so the image is read as the shared tooling reads it (R1,
R2). `flash_row.py` also imports `vtrux_bench` from the same checkout's
`shared/bench`, and refuses one imported from anywhere else.

## x2_image.json

Each image folder holds one:

| Key | What |
|---|---|
| `image` | the image folder's name, and the archive folder's |
| `chip` | the ESP-IDF target the image must be for |
| `variants` | `{variant: {"project_name", "pinnable"}}`; the variant names are `vtrux_build.json`'s, the project names the image's own (at most 31 characters, the descriptor's limit) |
| `not_for_vehicle` | true: the project name must say NOT_FOR_VEHICLE (SPEC T8, T14) |
| `required_strings` | strings the `.bin` must hold, e.g. the banner's |
| `forbidden_symbol_prefixes` | symbols the `.elf` must not have, e.g. the TWAI driver's |
| `required_symbols` | symbols it must have |
| `required_config` | `CONFIG_*` values the build's `config/sdkconfig.json` must hold |

## Archiving a build

From a shell exported for ESP-IDF v5.4.2, on `madhouse-debian`, with
`REVERSE_IT_DIR` set (unset is refused):

    systemd-run --user --scope --slice=build.slice \
      python3.13 -B shared/x2_test_images/tools/archive.py \
      build shared/x2_test_images/<image> <variant> --builder "<role>"

The files go to
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/x2_test_images/<image>/builds/<app sha256, 16 digits>/`,
and the row to `manifest.jsonl` beside `builds/`, one JSON object per line,
appended. A row records: the shared commit and tag, `git describe`, the
version, the code hash (R2) and image hash (R1), every flash file's offset,
name, size, SHA-256 and MD5, the `.elf`'s, `pinnable` with each of its
checks, the host, the builder and the command.

**To pin**, build in a worktree of your own, detached at the shared tag,
never in `../shared-<tag>/` (whose digest counts ignored files, R7, and
which every session pinning the tag shares):

    git -C anchor worktree add --detach ../<role>-x2img-<tag> <tag>

and run `archive.py` there before anything else writes into that tree:
only ignored files under the image's own `build/` and
`managed_components/` count as clean.

## Flashing a build

From a checkout of the shared tag you pin (T9), holding the bench lock
under the role you name, on the bench host:

    python3.13 -B shared/x2_test_images/tools/flash_row.py <image> <build> \
      --settings <local settings JSON> --role "<lock holder>" \
      --log-dir <new folder outside this checkout>

`<build>` is the build folder's name, the app's SHA-256 to 16 digits (or
all 64). The settings are the local, unpublished ones: `esptool.argv`
(esptool, e.g. from the ESP-IDF v5.4.2 python env), `esptool.device` (the
X2's USB IDs, DEVICES-SPEC S1) and the X2's MAC as `esptool.chip.mac` or
`x2_mac`; the chip type comes from the image's `x2_image.json`.

Before anything is written it refuses: a build with no pinnable row,
unless `--dev` (SPEC T7: a dev build is for its builder's own bench work);
rows for the build that disagree; any file whose size, SHA-256 or MD5
differs from the row, a name with a path, offsets that repeat or overlap;
an app that is not the row's (its SHA-256, project name, ELF hash, code
hash, chip); no MAC, or a settings chip type for another chip; a bench lock
not held by `--role`; and `canx2` attached as SLCAN (shared/bench SPEC
section 8), with the `canctl` command that detaches it. `--check-only`
makes the row and file checks alone, with no lock, settings or board. The
flasher then checks the files' MD5 again, auto-reset and the chip's type
and MAC before and after, and "Hash of data verified" for every file.

The record is `<log-dir>/flash_row.json`, beside the flasher's logs. The
line printed before it, `FLASHED x2 <image> <variant> build <folder>
app_sha256 <sha> (...)`, is what the release note carries for the image
(shared/bench SPEC section 8). Confirming the identity after the flash is
the consumer's (T10).
