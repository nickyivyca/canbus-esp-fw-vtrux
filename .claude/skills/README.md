# .claude/skills/

Role skills for this repo's specification-driven projects, loaded with
`/<name> <project>`. They are committed: each branch keeps its own role's skill
current, and changes reach other branches by cherry-pick, never by hand edit
(`AGENTS.md`, "Who owns what").

| Skill | What |
|---|---|
| `reviewer/` | Reviewer/orchestrator: owns the spec and review tracker, merges and pushes. |
| `implementor/` | Implementor: owns product code and core unit tests; self-checks on the emulated harness. |
| `tester/` | Independent tester: owns the harness, `VERIFICATION.md` and the bench. |
| `self-compact/` | A session compacts its own context and resumes an assigned step, through the bundled `session_compact.py`, when the user asks, at its role's compaction point, or when the bundled PreCompact hook `idle_compact_hook.py` blocks an idle compaction and asks for it. Design: `$REVERSE_IT_DIR/tools/plans/session-compact.md`. |

Each project's file map is its folder's `README.md`; the ownership split is in
`AGENTS.md`, "Who owns what". Each skill must stay true for every project.
Project facts go in the project's docs, not here.
