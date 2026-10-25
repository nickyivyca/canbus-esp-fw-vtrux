# gen-inhibit/diag/

The generator inhibitor's diagnostic frames, a published interface and so
part of the spec (`AGENTS.md`, "Who owns what"). The gen-inhibit reviewer owns
both files; changes need the user's approval.

| File | What |
|---|---|
| `wican_diag_schema.md` | The schema, the source of truth: every frame, field and value, the version history and the ID allocation shared with the charge interposer. |
| `vtrux-wican-diag.dbc` | The decoder for it, with the shared decoder's identity attributes (`DiagSchemaVersion`, `DiagSchemaHash`). |

The first commit here is schema 7, the layout the `e3d9437` before-port
baseline ran on (port plan toolkit item 10). Schema 8 (port step 0c,
2026-10-25) makes `0x7F3` B0-B3 the image-hash build id; the firmware follows
in the step 0c batch, and `gi_diag` picks the DBC for each capture by its
schema byte from this file's git history. The firmware, this doc and the DBC
change together, and every layout change bumps `diag_schema_ver`.
