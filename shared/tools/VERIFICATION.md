# Shared build tooling: verification evidence

Test evidence that the tooling in `shared/tools/` meets each requirement in
`SPEC.md`. Why each requirement is written as it is: `VALIDATION.md`.

Each entry names the requirements it covers (R1 to R23), the commit of the
tooling it tested, and its run files under `$REVERSE_IT_DIR`, with each
file's hash where a verdict rests on it (port plan goal 20). Entries arrive
as commits on the tester's branch, and the reviewer pushes them.

## Library side: R1, R2, R7, R15 (C6f part A, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `736a1c7`. The library ran
from `shared/tools/checks` at `ebfe34d`, whose `vtrux_build/` tree (`707f59c`) is
the same as `736a1c7`'s. **Checks:** `verification/verify_image.py` and
`verification/verify_digest.py` at `ebfe34d`, written from SPEC and ESP-IDF's
published image format, not from `vtrux_build`'s source. Each one's `--self-test`
catches every one of its broken fakes (8 and 12) and passes its faithful one.
**Host:** NICKY-XPS, Python 3.14.6, 2026-10-24. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`; `nicky_xps/SHA256SUMS` hashes every output).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| R2 | PASS | Synthetic images: the code hash and its blanked ranges match the tester's reading of R2. Version, date, time and the ELF hash each keep it. Project name, IDF version and one code byte each change it. A three-segment image passes. Truncated, padded, bad-checksum, bad-SHA and non-images are refused (`ImageError`). Reading leaves the file's bytes and mtime unchanged. Real images: the six clean `repro_a24de99` builds, from NICKY-XPS and madhouse-debian at two clone paths each, with and without ccache, give one code hash, `8f02c415...a37969`, the tester's reading for each. The dirty control gives another. | `verify_image_a24de99.txt` (`f9cd33fc...4c8bb`), `verify_image_a24de99_elf.txt` (`6ad5023c...89b64`) |
| R1 | PASS | Synthetic: the image hash is the embedded ELF SHA-256 field, and changes with it. Real: on the same seven images, read where their `.elf` files are kept (each `.bin` byte-identical to the SeaDrive copy), the image hash is the sha256 of the ELF built with it. | `verify_image_a24de99_elf.txt` (`6ad5023c...89b64`), `elf_copies_match.txt` (`fd4bb835...05b60`) |
| R15 (with R3) | PASS, library side | `code_hash(path)` and `AppImage.read(path).code_hash` give the tester's R2 code hash on synthetic and real images. Logging it is the bench toolkit's half, not tested here. | `verify_image_a24de99.txt` |
| R7 | PASS, partial | Same digest for: a copy elsewhere, an mtime change, `*.md` anywhere, a root `.gitignore`, empty folders, a `.git` above it on a branch, on a tag or absent. A different digest for: a content byte, a rename, a move, an empty file, `__pycache__`, CRLF, `README.md.txt`, a dotfile, a removed file, swapped contents, the path/content boundary. `component_files` lists exactly what R7 covers. `shared/tools` at `736a1c7`, checked out and exported by `git archive`: one digest, `daf1ffa9...79fea0`, 22 files. **Not tested** until the reviewer rules: `.gitignore` below the root, `.markdown`, `*.md` case, and the path separator across host OSes. | `verify_digest_736a1c7.txt` (`5d862596...cced`) |
| R4 | not yet verified | Waits on gen-inhibit's port step 0c, which retires the name stamp. | |

## Real release builds: R5, R8-R11, R13, R14 (with R3), R16, R22 (C6f part A, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `736a1c7`. Every batch ran with
`vtrux_build/` tree `707f59c`, the same as `736a1c7`'s, from `shared/tools/checks`
at `cbb49af`, `4c4686d` or `b4ce55d`. Those commits differ only in the tester's
`verification/` folder. **Checks:** `verification/release_cases.py`, whose
`--self-test` catches every broken record and second-build input. **Project:** a
throwaway clone of gen-inhibit `d2e0bb5` with fixture commits:
- `vtrux_build.json`, reproducible build on, sdkconfig as the reconfigure writes it;
- a branch whose build deletes its own `.bin`;
- a branch with a shared component by `override_path`;
- for R22, IDF's `hello_world` on esp32c3 as a second project.

Records go to a stand-in `REVERSE_IT_DIR`. **Hosts:** NICKY-XPS and madhouse-debian:
IDF v5.4.1, toolchain esp-14.2.0_20241119 (gcc 14.2.0) on both. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`; `xps_release_1/SHA256SUMS`, sha256 `e04670fb123f...`, hashes every
NICKY-XPS file; `madhouse_release/`).

| Req | Result | What was tested |
|---|---|---|
| R8 | PASS | Refused: an untracked file or a modified tracked file at the start; an untracked file made during the reconfigure, with nothing compiled; an untracked file or a new commit made mid-build, refused at the end. No record in any. |
| R9 | PASS | A release build with `IDF_CCACHE_ENABLE=1` from the export has `CCACHE_ENABLE` off in every CMakeCache and no `ccache` in any `.ninja` file (both build paths ran). `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER=ccache`, taken by CMake, are refused. |
| R10 | PASS | Refused: the overwrite setting set (to `0`); a managed component file changed; its `.component_hash` changed; a file its `CHECKSUMS.json` does not list. After a release build, each `.component_hash` is the lock's. |
| R11 | PASS | The recorded `.bin` is the one `project_description.json` names. A build that deletes that file is refused. |
| R13 | PASS | `REVERSE_IT_DIR` unset: refused, with no `build/` made. A release build writes one record named by its image hash, with the `.bin` and `.elf`, judged field by field against what the tester read itself, on both hosts. A second build of the same image exits 0 and leaves the record folder's bytes and mtimes unchanged. |
| R16 (`override_path`) | PASS | A shared component taken by `override_path` (the lock shows a local source) is refused. |
| R22 | PASS, one question open | A second project builds and records with no tooling change. The same project with no component manifest, so no `dependencies.lock`, is refused after the reconfigure. Whether SPEC allows that is with the reviewer. |
| R5 | PASS, except one clause not met | Same commit, another branch and clone path: the same image hash. Same files on NICKY-XPS and madhouse-debian: the same code hash, `a7e2d28084f3122c...`, with different image hashes. **Not met, cause step 0c:** identical files at another commit give another code hash. They differ only in `project_name`, gen-inhibit's name stamp `wican-fw_obd_<commit>_man`, outside R2's blanks. |
| R14 (with R3) | not met, cause step 0c | A bench build in a modified tree builds without the release script's refusals. Its code hash is not the release build's, for the same reason: `project_name` carries `-dirty`. |
| R6 | not tested on its own | No case builds an image whose name or version string disagrees with its tree. The records name images by image hash, not by name. |
