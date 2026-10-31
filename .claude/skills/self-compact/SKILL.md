---
name: self-compact
description: Compact this session's own context and resume a step the user or the project's reviewer already assigned. Use only when the user asks, at the compaction point your role skill names, or when the idle-compaction hook asks for it.
---

# Self-compact

The user asked for this procedure (2026-10-20) so that long-running sessions
can keep their context under control without someone at the keyboard. It is
the only sanctioned way for a session to type into its own input, and
`${CLAUDE_SKILL_DIR}/session_compact.py`, bundled with this skill, is the only
tool that does it. The design and its limits are in
`$REVERSE_IT_DIR/../Infrastructure/tools/self-compact/plan.md`, the tool's
main line since 2026-10-31 (its `README.md` there has the decisions and who
owns what); the research behind them is in `research.md` beside it.

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

Only when the user asks for it, at the compaction point your role skill
names, or when the idle-compaction hook's wake message asks for it (user,
2026-10-24; AGENTS.md "Compacting your own context"). Do not use it to
avoid a question for the user, to drop context the user is relying on, or
to end the conversation.

**Write it out first, then compact.** What you have read and worked out is
in your context now. If any of it can become a file, such as a draft, an
entry, a worklog note, or an analysis of results already in, write that
file before compacting. If you compact first, the resumed session has to
re-read the same basis before it can write it. So a background job running
is not by itself a reason to compact: use the wait to write out what the
context holds. When the user says to compact "once X and you're just
waiting", both halves must hold.

## The two messages must be clean

They restate what the user, or your project's reviewer, has already decided
and add nothing.

- **The compact message** says what the summary must keep:
  - the task in progress, with its plan file and step;
  - the user's rulings and standing rules;
  - open questions and pending approvals;
  - file paths.

  It must not tell the summary to drop or soften any of these, and must not
  record anything as approved that the user has not approved.
- **The resume message** names the next step the user, or your project's
  reviewer, already assigned, and where it is written down: a plan file, or
  the reviewer's message or batch. For example: "Resume: run the suite per
  <plan file> step 4, then report the results."

  After the idle-compaction hook's wake, it names the step the session was
  waiting to do, if the user or the reviewer assigned one. If the session
  was only waiting for the user, the resume message is exactly: "Resume:
  re-read the docs per AGENTS.md, then wait for the user's next message."

  It must not:
  - grant permissions;
  - claim approval;
  - add tasks;
  - change rules;
  - ask for anything the user or the reviewer has not asked for.

  The next turn reads it as user input, so it must carry nothing beyond
  what the user or the reviewer actually said.

## Procedure

1. Finish the current piece of work, and write everything worth keeping
   from your context to its files, including work you can finish while a
   background job runs. The summary loses detail; files do not, and
   anything not written down now has to be re-read after the resume. Write
   the files in their own tool calls, never in the same call as the command
   in step 2 or 3, so a refusal can't take the write with it.
2. Check the messages without typing anything, with a Bash call that
   contains only this command:
   `<python> ${CLAUDE_SKILL_DIR}/session_compact.py --check --compact "..." --resume "..."`.
   - The Python command for the machine is in `local-paths.md`.
   - Claude Code fills in this skill folder's absolute path when the skill
     loads, so the path above is already written out in full. Copy it
     exactly as shown. The auto-mode allow rule allows the tool only in
     exactly this form: that path, with no variables such as `$PWD` and no
     flags other than `--compact`, `--resume`, `--check` and `--timeout`.
   - Nothing else goes in that Bash call: no `cd`, no `&&`, `;` or pipe, no
     heredoc, no second command. A compound command doesn't fit the rule,
     and auto mode refuses it as self-typing.
3. Run the same command without `--check`, the same way: a Bash call with
   only this command, sent on its own and not alongside another tool call,
   as the last action of the turn.
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
