# shared/diag/

The diag frames' shared parts, to `SPEC.md` D1-D5: the build-id read, a
frame packer driven by a table from each project's DBC (with a Python twin),
and the diag schema convention. Each project's frame IDs, cadence, contents
and schema byte stay in its own spec, schema doc and DBC.

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

## Who owns which file

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, D1 to D5, and why each requirement is written as it is | the reviewers, as above |

No code yet (2026-11-23). Its files will be product code, owned by the
implementor of the project that needs the change, and their tests the
tester's.
