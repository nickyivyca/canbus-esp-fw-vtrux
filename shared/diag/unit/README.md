# shared/diag/unit/

shared/diag's unit tests, the implementor's: the C packer and the build id's
packing on the host, the Python twin, the table generator and its fit
checks.
Self-checks, not verification. The expected bytes are cantools 41.3.1's
encode of the same layouts, scaling off.

    make -C shared/diag/unit run          # from the repo root; `asan` for the sanitiser build
    <python> -B -m unittest discover -s shared/diag/unit -p "test_*.py"

The C binaries go to `scratch/diag-unit/` at the worktree's root. The
generator's Python tests are skipped without cantools. The fit-check tests
need gcc and g++ and are skipped without them. Only their two run cases
write: a binary each, in a folder of its own under `scratch/diag-unit/`,
removed when the case ends. The other Python tests write nothing.

| File | What |
|---|---|
| `test_diag_pack.c` | the C packer: Intel and Motorola, signed and unsigned layouts at their ranges' ends; misfits left 0 and reported, a misfit writing nothing and a signal writing only its own bits; 64-bit and 1-bit ranges; a multiplexer picking its signals, a value with none, and a selector that does not fit; tables refused (a signal past the frame, a Motorola signal running past it, length 0, a multiplexed signal with no selector, DLC 9, two selectors); the build id's packing and its bytes on the wire |
| `test_diag_pack.py` | the Python twin, on the same vectors; the generator's table (order, multiplexing, an extended id), its Python module read back as the same table, its C text, and its refusals (a float signal, no integer `DiagSchemaVersion`) |
| `test_fit_checks.py` | the generated fit checks (D6), on a table built by hand, each case compiled as C11 by gcc and as C++11 by g++ with `-Werror`: values that fit compile; a wider, signed, bool or 64-bit type, a signed signal, and a mask or constant that does not fit stop on a static assertion naming the signal; the capped form takes a wide unsigned type and refuses a signed one; a run case per language checks each macro's value, the cap at the maximum (8, 1, 63 and 64 bits) and that the capped value is read once; two tables in one file |
| `Makefile` | the C build |
