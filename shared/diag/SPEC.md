# Shared diag frames: specification

**Status:** approved by the user on 2026-11-23, in the gen-inhibit
reviewer's session, and drafted by the gen-inhibit reviewer for
gen-inhibit's stage (b) (port plan step 4, "the one diag encoder and
`build_id`"), with the charge-interposer reviewer's scope answers of the
same day. As a shared component's spec this follows port plan goal 20: the
reviewer whose project needs a change drafts it, the user rules in that
reviewer's session, and the other project's reviewer reads it before it
merges into `shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence:** `VERIFICATION.md`, the tester's, when there is code to
  test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (step 4), each project's diag schema doc and DBC, and shared/bench's
  `vtrux_bench/decode.py`.

Requirements are numbered D1 to D5. The numbers are stable.

- **D1. Scope.** `shared/diag` holds the build-id read, the frame packer
  and the diag schema convention. Each project keeps in its own spec its
  frame IDs, cadence, contents, its schema byte's value and positions, and
  when it sends.
- **D2. The build id** is the first four bytes of the running image's hash
  (`esp_app_get_description()->app_elf_sha256`), read at run time, packed
  as a u32 with hash byte 0 most significant and sent little-endian, so the
  DBC's unsigned value printed as `%08x` reads as the hash's first four
  bytes in order.
- **D3. The layout comes from the project's DBC.** Each project's frame
  table is generated from its published DBC, or checked against it by a
  test that fails on any difference. There is no hand-kept layout. The
  packer fills a frame from that table: start bit, length, byte order and
  signedness as the DBC gives them. A value that does not fit its signal is
  never truncated silently: the packer reports it, and the project's spec
  says what follows.
- **D4. One table, two languages.** The same table is available to Python,
  generated or by a twin packer. A test checks that the C and Python
  packers give the same bytes for the same table and values.
- **D5. The schema convention.** Each project's frames carry a schema byte
  at fixed positions its spec names. Its DBC carries `DiagSchemaVersion`,
  `DiagSchemaHash` and `RenamedFrom` on a renamed signal. Any change to a
  signal's name, start, length, byte order, signedness, scale, offset or
  value table, or to what a byte means, bumps `DiagSchemaVersion`.
  `DiagSchemaHash` is always the DBC's `layout_hash` (shared/bench's
  `decode.py`), so it changes with every layout change, but not with a
  change of meaning alone.
