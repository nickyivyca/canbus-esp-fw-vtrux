# shared/diag/

The diag frames' shared parts, to `SPEC.md` D1-D5: the build-id read, a
frame packer driven by a table from each project's DBC (with a Python twin),
and the diag schema convention. Each project's frame IDs, cadence, contents
and schema byte stay in its own spec, schema doc and DBC.

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

## Using it

- **The C side** is one ESP-IDF component, `vtrux/diag`, which a project
  takes from git at a pinned tag of `shared/main`, as it takes the other
  shared components.
- **The table** is generated from the project's published DBC and committed
  in the project, as product code (D3):

      <python> -B shared/diag/tools/diag_table_gen.py <the DBC> --prefix <p> \
          --c-header <p>.h --c-source <p>.c --python <p>.py

  The C header and source go into the project's own component; the Python
  module goes wherever the project's Python reads it. The output depends
  only on the DBC and the prefix, so a check can regenerate and compare.
  The generator needs cantools.
- **The packers** take raw values, the integers on the wire, one per signal
  in the table's order: `diag_pack_message()` in C, `pack_message()` in
  `python/diag_pack.py`. A value that does not fit its signal is left 0 and
  reported, never truncated (D3); the project's spec says what follows.
- **The build id** (D2) is `diag_build_id()`.

## Who owns which file

The code is product code, owned by the implementor of the project that
needs the change; writing a file first does not make a project its owner.

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, D1 to D5, and why each requirement is written as it is | the reviewers, as above |
| `include/diag_pack.h`, `diag_pack.c` | the frame packer (D3): Intel and Motorola, signed and unsigned, 1 to 64 bits, one multiplexer; misfits left 0 and reported. Pure C, no ESP-IDF calls | the implementor of the project that needs the change |
| `include/diag_build_id.h`, `diag_build_id.c` | the build id (D2): the running image's `app_elf_sha256`, first four bytes, byte 0 most significant, read through a volatile pointer | the same |
| `python/diag_pack.py` | the packer's Python twin (D4), over the generated Python table. Stdlib only | the same |
| `tools/diag_table_gen.py` | the table generator (D3, D4): one DBC to a C header and source and a Python module | the same |
| `CMakeLists.txt`, `idf_component.yml` | the component's build | the same |
| `unit/` | the implementor's unit tests of the packers and the generator, self-checks; see `unit/README.md` | the same |

The parity test of the C and Python packers (D4), each project's DBC check
(D3) and any other test that verifies the component are the testers'.
