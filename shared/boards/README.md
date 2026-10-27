# shared/boards/

The board layer: one ESP-IDF component per board, `shared/boards/<board>/`,
which a project takes at a pinned tag (port plan goal 5 and Layer 1).

| File | What |
|---|---|
| `SPEC.md` | The board layer's spec: the common rules (B1 to B7) and one section per board. |
| `VALIDATION.md` | Why each requirement is written as it is. |

No board component has code yet. `wican_v3/` comes at gen-inhibit's port
step 1a, and `x2/` at the charge interposer's step 2a. Each board's own
`README.md` records who owns each of its files (SPEC B7). `VERIFICATION.md`
is the tester's, written when there is code to test.

The spec and `VALIDATION.md` belong to the reviewers: each section is
drafted by the reviewer of the project that needs it and ruled by the user
in that reviewer's session.
