# Shared diag frames: validation

Why each requirement of `SPEC.md` is written as it is. Readings are of
`shared/main` `8e16cd74`, `gen-inhibit/main` `18643807` and
`charge-interposer/main` as of 2026-11-23.

### D1. Scope
- **Basis:** user, 2026-10-25 (one shared diag layout in stage (b), the
  hand-kept revision dropped); port plan step 4, "the one diag encoder and
  `build_id` (stage (b)): the layouts stay each project's spec, but the
  encoder and the build-id read are written once" (user, 2026-11-19, in the
  methodology owner's session). The split (shared: build id, packer, schema
  convention; per project: IDs, cadence, contents, schema byte) was agreed
  by both reviewers on 2026-11-23; the interposer's cadence (1 Hz plus an
  immediate `0x7F4` on a state change, gated by its spec 2.3's listen-only)
  is why cadence stays per project. Wording approved by the user,
  2026-11-23.
- **Reading:** both projects hand-pack their frames today, gen-inhibit in
  `gen_inhibit_core.c` (e.g. `0x7F1` and `0x7F3`, lines ~1281 and ~1370),
  the interposer in `machine.cpp` and its Python reference `machine.py`.
- **Status:** validated.

### D2. The build id
- **Basis:** user, 2026-10-19 and 2026-10-25 (gen-inhibit's `0x7F3`
  `diag_build_id`, packed as the interposer's `0x7F7` build id; spec 10,
  schema 8). Wording approved by the user, 2026-11-23.
- **Reading:** gen-inhibit's schema doc, `0x7F3` B0-B3, and the
  interposer's `0x7F7` give the same packing.
- **Status:** validated.

### D3. The layout comes from the project's DBC
- **Basis:** the charge-interposer reviewer, 2026-11-23: a table that can
  drift from the published DBC would be a third copy of the layout. Wording
  approved by the user, 2026-11-23.
- **Status:** validated.

### D4. One table, two languages
- **Basis:** the charge-interposer reviewer, 2026-11-23: the interposer's
  product has a Python reference (`machine.py`) and a C++ port compared by
  `host_diff`/`check_port`, and both hand-pack the frames today. Wording
  approved by the user, 2026-11-23.
- **Status:** validated.

### D5. The schema convention
- **Basis:** the charge-interposer reviewer, 2026-11-23: the convention is
  already half shared (schema bytes at fixed positions in both projects;
  `DiagSchemaVersion`, `DiagSchemaHash` from shared/bench's `layout_hash`,
  and `RenamedFrom` in both DBCs). Gen-inhibit's schema doc already counts a
  byte whose meaning changes as a layout change (schema 8, 2026-10-25).
  Wording approved by the user, 2026-11-23.
- **Status:** validated.
