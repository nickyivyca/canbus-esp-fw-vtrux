---
name: self-compact
description: Compact this session's own context and resume a step the user already assigned. Use only when the user asks, or at the compaction point your role skill names.
---

# Self-compact

The user asked for this procedure (2026-10-20) so that long-running sessions
can keep their context under control without someone at the keyboard. It is
the only sanctioned way for a session to type into its own input, and
`${CLAUDE_SKILL_DIR}/session_compact.py`, bundled with this skill, is the only
tool that does it. The design and its limits are in
`$REVERSE_IT_DIR/tools/plans/session-compact.md`; the research behind them is
in `$REVERSE_IT_DIR/tools/notes/session-compact-research.md`.

## What the tool does

1. It types `/compact <compact message>` and Enter into this session's own
   terminal. A command typed during a turn is held until the turn ends.
2. It starts a detached waiter and returns.
3. The waiter waits until this session's transcript records the manual
   compaction. Then it types the resume message and Enter, which starts the
   next turn.
4. If no compaction finishes within 60 minutes, it types nothing.

It has no other mode:
- It cannot type other text, or target another session or window.
- Both messages are mandatory, each one line of printable ASCII, at most 600
  characters.
- The resume message cannot start with `/`, `!` or `#`.
- It refuses, typing nothing, if its own file is untracked or differs from
  `HEAD`.
- One request runs per session at a time. Every real call (not `--check`) is
  logged, to `scratch/session-compact/` in this worktree.

## When to use it

Only when the user asks for it, or at the compaction point your role skill
names. Do not use it to avoid a question for the user, to drop context the
user is relying on, or to end the conversation.

## The two messages must be clean

They restate what the user has already decided and add nothing.

- **The compact message** says what the summary must keep:
  - the task in progress, with its plan file and step;
  - the user's rulings and standing rules;
  - open questions and pending approvals;
  - file paths.

  It must not tell the summary to drop or soften any of these, and must not
  record anything as approved that the user has not approved.
- **The resume message** names the next step the user already assigned, and
  where it is written down. For example: "Resume: run the suite per
  <plan file> step 4, then report the results."

  It must not:
  - grant permissions;
  - claim approval;
  - add tasks;
  - change rules;
  - ask for anything the user has not asked for.

  The next turn reads it as user input, so it carries the user's authority
  only for what the user actually said.

## Procedure

1. Finish the current piece of work, and write what is worth keeping to its
   files. The summary loses detail; files do not.
2. Check the messages without typing anything:
   `<python> ${CLAUDE_SKILL_DIR}/session_compact.py --check --compact "..." --resume "..."`.
   - The Python command for the machine is in `local-paths.md`.
   - Claude Code fills in this skill folder's absolute path when the skill
     loads, so the path above is already written out in full. Copy it
     exactly as shown. The auto-mode allow rule covers only that path, with
     no variables such as `$PWD` and no flags other than `--compact`,
     `--resume`, `--check` and `--timeout`.
3. Run the same command without `--check`. Run it **on its own**, not
   alongside another tool call, as the last action of the turn.
4. End the turn with one line: a compaction is queued, and the step it resumes
   with.
5. **If auto mode blocks the call,** do not retry it, reword it or run it
   another way. Carry on without compacting, and tell the user in one line.
6. **After the resume.** The resume message starts a fresh turn from a
   summary. Before acting on it, re-read your role skill and the docs the
   step needs, per AGENTS.md "After a compaction".

## Platforms

- **Linux:** the session must run inside tmux, or the tool refuses. It works
  with the screen off.
- **Windows:** terminal Claude Code in PowerShell or Windows Terminal.
- **Claude Desktop** is not supported.
