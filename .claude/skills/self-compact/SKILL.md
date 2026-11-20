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
   next turn. With `--compact-only` it types nothing: the session stays idle
   until a message, a task notification or the user arrives.
4. If no compaction finishes within 60 minutes, it types nothing.

It has no other mode:
- It cannot type other text, or target another session or window.
- The compact message is mandatory. So is the resume message, unless you
  pass `--compact-only` instead. The two exclude each other, and a missing
  resume is refused, never taken as compact-only. Each message is one line
  of printable ASCII, at most 600 characters.
- The resume message cannot start with `/`, `!` or `#`.
- It refuses, typing nothing, if its own file is untracked or differs from
  `HEAD`.
- One request runs per session at a time. Every real call (not `--check`) is
  logged, to `scratch/session-compact/` in this worktree.

## The compact-note hook

`compact_note_hook.py`, beside the tool, is a SessionStart hook on
compactions. After every compaction (yours, an idle one, or one at the
context limit) it compares each skill copy Claude Code re-attached with the
`SKILL.md` on disk and adds one note to your context naming the stale copies
to re-read, or saying all match. It starts no turn: the note waits for
whatever wakes you, so a compact-only session gets it on its real wake. Its
entry goes in each worktree's `settings.local.json`, in the guarded form
its header gives.

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
and add nothing. Each is one line of printable ASCII, at most 600
characters, so draft the compact message to fit the first time.

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

  It must not:
  - grant permissions;
  - claim approval;
  - add tasks;
  - change rules;
  - ask for anything the user or the reviewer has not asked for.

  The next turn reads it as user input, so it must carry nothing beyond
  what the user or the reviewer actually said.

## Resume or compact-only

Resume when your next step is work you start by yourself, already assigned
by your batch, the reviewer or the user. Pass `--compact-only` when your
next step waits on an outside trigger: a cross-session message, a
background-task notification, or the user. A resume that only says to wait
re-reads the docs and goes idle, and the trigger wakes the session anyway;
compact-only reads them once, when it is woken (user, 2026-11-20).

Two cases are always compact-only:
- the idle-compaction hook's wake: an hour idle means you are waiting on an
  outside trigger;
- a compaction just after you start a background task you will wait on: its
  notification wakes you.

With `--compact-only`, the compact message names what you wait for and where
your next step is written, because no resume message restates it. If you are
not sure anything outside is due to wake you, resume: a session left without
a needed resume stays wedged.

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
     flags other than `--compact`, `--resume` or `--compact-only`, `--check`
     and `--timeout`. If auto mode blocks it, carry on without compacting,
     per step 5; don't switch to a resume message to get past the block.
   - Nothing else goes in that Bash call: no `cd`, no `&&`, `;` or pipe, no
     redirection (no `2>&1`, `>`, `| tail` or `| head`: the output is a few
     lines of JSON), no heredoc, no second command. This holds for the
     `--check` call too. A compound command doesn't fit the rule, and auto
     mode refuses it as self-typing.
3. Run the same command without `--check`, the same way: a Bash call with
   only this command, sent on its own and not alongside another tool call,
   as the last action of the turn.
4. End the turn with one line: a compaction is queued, and either the step
   it resumes with or that it is compact-only and what will wake the
   session.
5. **If auto mode blocks the call,** do not retry it, reword it or run it
   another way. Carry on without compacting, and tell the user in one line.
6. **After the resume, or whatever wakes a compact-only session.** The
   next turn starts from a summary. Before acting on it, follow the "Skill
   check after this compaction" note the compact-note hook put in your
   context: re-read in full each skill it names stale, and none if it says
   the copies match. Then re-read the docs the step needs, per AGENTS.md
   "After a compaction".

## Platforms

- **Linux:** the session must run inside tmux, or the tool refuses. It works
  with the screen off.
- **Windows:** terminal Claude Code in PowerShell or Windows Terminal.
- **Claude Desktop** is not supported.
