# shared/diag/

The diag frames' shared parts, to `SPEC.md` D1-D6: the build-id read, a
frame packer driven by a table from each project's DBC (with a Python twin),
the diag schema convention, and the table's build-time fit checks. Each project's frame IDs, cadence, contents
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
- **The fit checks** (D6) are macros at the end of the generated C header.
  Each one puts one raw value into the array, and the build fails if the
  value could exceed its signal or the signal is signed. They compile as
  C11 and as C++ under GCC. For prefix `<p>`:
  - `<p>_FIT_PUT`: an unsigned value, not bool, whose type is no wider than
    the signal and under 64 bits.
  - `<p>_FIT_CAP`: an unsigned value of any width, capped at the signal's
    largest value.
  - `<p>_FIT_BIT`: a condition, as 1 or 0.
  - `<p>_FIT_MASK`: a value masked by a constant that fits the signal.
  - `<p>_FIT_CONST`: a constant that fits the signal.

  The frame-id and schema checks stay in each project.
- **The build id** (D2) is `diag_build_id()`.

## Who owns which file

The code is product code, owned by the implementor of the project that
needs the change; writing a file first does not make a project its owner.

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, D1 to D6, and why each requirement is written as it is | the reviewers, as above |
| `VERIFICATION.md` | test evidence that a commit of the code meets D1 to D6 | the testers |
| `include/diag_pack.h`, `diag_pack.c` | the frame packer (D3): Intel and Motorola, signed and unsigned, 1 to 64 bits, one multiplexer; misfits left 0 and reported. Pure C, no ESP-IDF calls | the implementor of the project that needs the change |
| `include/diag_build_id.h`, `diag_build_id.c` | the build id (D2): the running image's `app_elf_sha256`, first four bytes, byte 0 most significant, read through a volatile pointer | the same |
| `python/diag_pack.py` | the packer's Python twin (D4), over the generated Python table. Stdlib only | the same |
| `tools/diag_table_gen.py` | the table generator (D3, D4, D6): one DBC to a C header and source and a Python module, with the build-time fit checks | the same |
| `CMakeLists.txt`, `idf_component.yml` | the component's build | the same |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20): every symbol the component's files test, with its class. A project that names `vtrux/diag` in its `shared_components` fails R20 without it | the tester of the project that needs the change (charge-interposer reviewer, 2026-11-27) |
| `unit/` | the implementor's unit tests of the packers and the generator, self-checks; see `unit/README.md` | the same |
| `test/` | the testers' checks of D2-D4 against cantools, on a fixture DBC and both projects' diag DBCs; their checks of D6, which compile pass and fail cases in C and C++; and the can-fails of both; see `test/README.md` | the testers |

The parity test of the C and Python packers (D4), each project's DBC check
(D3) and any other test that verifies the component are the testers'.
