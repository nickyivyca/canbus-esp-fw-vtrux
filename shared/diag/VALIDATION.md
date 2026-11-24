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
  Wording approved by the user, 2026-11-23, and revised the same day on the
  charge-interposer reviewer's read: `DiagSchemaHash` is computed, not
  bumped. `layout_hash` hashes `canonical_text` (shared/bench
  `vtrux_bench/decode.py`): each message's id, name, length and extended
  flag, and each signal's name, start, length, byte order,
  signedness/float, scale, offset, mux and choices. So it moves with every
  layout change and not with a change of meaning alone, which only
  `DiagSchemaVersion` records (revision approved by the user, 2026-11-23).
- **Status:** validated.

### D6. Fit checks at build time
- **Basis:** user, 2026-11-23, in the charge-interposer reviewer's session.
  The interposer's schema 5 ruling says no diag value may overflow its
  signal, and a misfit is a defect. The user also ruled that both projects
  use one set of checks, not one copy each. Wording approved by the user,
  2026-11-23.
- **Capped form:** added on the gen-inhibit reviewer's read, 2026-11-23.
  Gen-inhibit spec 10 allows a value whose type is wider than its signal
  if the core caps it at the signal's maximum (`tx_fail`, `ctr_steps_bad`,
  `rx_errors`, `tx_queued_behind`), through `GI_PUT_CAP`. Without the
  capped form, D6 would forbid that. Revision approved by the user,
  2026-11-23, in the charge-interposer reviewer's session.
- **Reading:** each project has its own copy of the same checks today.
  Gen-inhibit's is in C, in `gen_inhibit_core.c` (`GI_PUT` and others,
  `gen-inhibit/main` line ~1256). The interposer's is in C++, in
  `machine.cpp` (`INTP_PUT` and others), on the implementor's branch
  `charge-interposer/implementor/diag-schema5` at `404a6932`, line ~1125.
  GCC's `__typeof__` and the `static_assert` name work in C11 and in C++,
  so one set can serve both languages.
- **Status:** validated.
