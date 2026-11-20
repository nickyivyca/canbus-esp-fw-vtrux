# .claude/tools

Small read-only helpers for the role sessions. Each branch gets them by
cherry-pick from the methodology owner, like the skills.

- `check_context.py`: prints this session's context size, from the usage of
  its last API call in its own transcript. `--mark TOKENS` adds whether it is
  above that mark. It writes nothing. Run it with the Python command from
  `local-paths.md`. (Whether the skill copies in context are stale is checked
  after each compaction by the compact-note hook,
  `.claude/skills/self-compact/compact_note_hook.py`.)
