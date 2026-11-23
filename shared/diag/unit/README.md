# shared/diag/unit/

shared/diag's unit tests, the implementor's: the C packer and the build id's
packing on the host, and the Python twin and the table generator.
Self-checks, not verification. The expected bytes are cantools 41.3.1's
encode of the same layouts, scaling off.

    make -C shared/diag/unit run          # from the repo root; `asan` for the sanitiser build
    <python> -B -m unittest discover -s shared/diag/unit -p "test_*.py"

The C binaries go to `scratch/diag-unit/` at the worktree's root. The Python
tests write nothing; the generator's are skipped without cantools.

| File | What |
|---|---|
| `test_diag_pack.c` | the C packer: Intel and Motorola, signed and unsigned layouts at their ranges' ends; misfits left 0 and reported, a misfit writing nothing and a signal writing only its own bits; 64-bit and 1-bit ranges; a multiplexer picking its signals, a value with none, and a selector that does not fit; tables refused (a signal past the frame, a Motorola signal running past it, length 0, a multiplexed signal with no selector, DLC 9, two selectors); the build id's packing and its bytes on the wire |
| `test_diag_pack.py` | the Python twin, on the same vectors; the generator's table (order, multiplexing, an extended id), its Python module read back as the same table, its C text, and its refusals (a float signal, no integer `DiagSchemaVersion`) |
| `Makefile` | the C build |
