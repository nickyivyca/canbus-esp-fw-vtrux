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
| `verify_digest.py` | R7, by its properties only, since SPEC does not define the serialisation. Pairs that R7 says cover the same thing give one digest: a copy elsewhere, an mtime change, `*.md` added, a root `.gitignore`, an empty folder, a `.git` above. Pairs that differ in what it covers give two: a content byte, a rename or move, an empty file, `__pycache__`, CRLF, `README.md.txt`, a dotfile. `component_files` lists exactly what R7 covers. With `--commit`, the component checked out at a commit and exported with `git archive` give one digest. 12 fakes. Left out until the reviewer rules: `.gitignore` below the root, `.markdown`, `*.md` case, and the path separator across host OSes. |

## Running

From this folder, with the Python command in `local-paths.md` and `-B` (a
`__pycache__` in `shared/tools/` changes its digest):

```
py -3.14 -B verify_image.py --self-test
py -3.14 -B verify_image.py [--same A.bin B.bin ...] [--different C.bin] [--expect-code PREFIX]
py -3.14 -B verify_digest.py --self-test
py -3.14 -B verify_digest.py [--commit <commit> [--component shared/tools]]
```

Exit 0 is all ok, 1 is a FAIL. The run records go to the gen-inhibit harness
folder in reverse-it (`projects/vtrux/notes/artifacts/gen-inhibit/runs/`), and
the results to `../VERIFICATION.md`.
