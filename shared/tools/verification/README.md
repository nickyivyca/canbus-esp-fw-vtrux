# shared/tools/verification

**Owner:** tester (gen-inhibit; C6f, reviewer ruling 2026-10-24).

The tester's verification of the build tooling against `../SPEC.md`. The
expected values come from SPEC and from ESP-IDF's published app image format,
never from `vtrux_build`'s source: `independent.py` is the tester's own reading,
and each check compares the library's answer with it, or tests a property SPEC
states. The build checks (R17-R21) are not here; they will be in
`../vtrux_checks/`.

Each script has a `--self-test` that runs the same checks against fake
libraries: each fake breaks one rule and must be caught, and one fake breaks
none and must pass. A check that no fake can fail is not a check.

## Files

| File | What |
|---|---|
| `independent.py` | The tester's reading of R1 and R2: the image layout, the ranges R2 blanks, the code hash and the image hash. It also builds valid synthetic images and changes one field or byte while keeping the image valid. `scratch(name)` gives a run its own folder in the worktree's `scratch/shared-tools-verification/`. |
| `verify_image.py` | R1, R2 and R15 (the library side). On synthetic images: the code hash and its blanked ranges, which fields keep it and which change it, refusal of a truncated, padded or badly sealed image, and that reading never patches the file. With `--same`, `--different` and `--expect-code`, on real images: the same checks, plus one shared code hash across builds of the same files. Where an `.elf` sits beside a `.bin`, it also checks R1 against the ELF's sha256. 8 fakes. |
| `verify_digest.py` | R7, by its properties only, since SPEC does not define the serialisation. Pairs that R7 says cover the same thing give one digest: a copy elsewhere, an mtime change, `*.md` added, a root `.gitignore`, an empty folder, a `.git` above. Pairs that differ in what it covers give two: a content byte, a rename or move, an empty file, `__pycache__`, CRLF, `README.md.txt`, `.markdown`, `README.MD`, a dotfile. A `.gitignore` in a subfolder is left out too. These are R7's edges as the user ruled them (2026-10-24, SPEC `904b98a`). `component_files` lists exactly what R7 covers. With `--commit`, the component checked out at a commit and exported with `git archive` give one digest. With `--expect-digest`, that digest must equal the other host OS's. 16 fakes. |

| `release_cases.py` | Real release builds: R5, R8-R11, R13, R14 with R3, R16 (`override_path`), R22. Cases include, for R8: staged, deleted and untracked files, and a committed sdkconfig the reconfigure rewrites. For R9: ccache launchers and ccache masquerading as the compiler on PATH, judged by the compiler CMake chose and the compiles ccache cached in its own `CCACHE_DIR`. For R10: a project with no `dependencies.lock`. It clones gen-inhibit at `--base` into `--root` and adds a fixture commit: `vtrux_build.json`, reproducible build on, and the tracked files as `idf.py reconfigure` writes them. Branch `fx-r11` deletes the build's own `.bin`; branch `fx-r16` takes a shared component from a local folder. A minimal second project for R22 is IDF's `hello_world` on esp32c3. Records go to a stand-in `REVERSE_IT_DIR` under `--root`. Each case judges from outside: the exit code, the record folder before and after, the build folder and git. It never judges message wording. A case whose trigger did not happen is VOID. Before each case it checks the bench lock, the "No builds" line and running simulators, and stops if any is set. `--self-test` runs the R13 record judge, the second-build judge, the refusal verdict and the masquerade's evidence readers against broken inputs. |

## Running

From this folder, with the Python command in `local-paths.md` and `-B` (a
`__pycache__` in `shared/tools/` changes its digest):

```
py -3.14 -B verify_image.py --self-test
py -3.14 -B verify_image.py [--same A.bin B.bin ...] [--different C.bin] [--expect-code PREFIX]
py -3.14 -B verify_digest.py --self-test
py -3.14 -B verify_digest.py [--commit <commit> [--component shared/tools]]
py -3.14 -B release_cases.py --self-test
```

`release_cases.py` runs from an exported ESP-IDF shell, with its Python:
`python -B release_cases.py --root <short folder in scratch/> --out <run folder>
--base <gen-inhibit commit> [--cases a,b] [--jobs N] [--fixture <repo>]`.
Set `REAL_REVERSE_IT_DIR` to the real reverse-it folder so it can read the "No
builds" line. Keep `--root` short: the IDF build paths under it are long.

Exit 0 is all ok, 1 is a FAIL. The run records go to the gen-inhibit harness
folder in reverse-it (`projects/vtrux/notes/artifacts/gen-inhibit/runs/`), and
the results to `../VERIFICATION.md`.
