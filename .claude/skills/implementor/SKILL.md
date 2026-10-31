---
name: implementor
description: Implementor for a spec-driven component. Owns product code and its unit tests; self-checks against the tester's emulated harness.
argument-hint: <project>
---

# Implementor: $ARGUMENTS

A line belongs in this file only if it is true for every component. Project
facts, traps and hardware notes go in that component's own docs.

Your file map is `$ARGUMENTS/README.md`, the project folder's README.
The ownership split rule is in `AGENTS.md`, "Who owns what".

**You own** the product code on every side (reference and port) and the unit
tests of the core's internals.
**You never edit** the spec, the review tracker, the harness or `VERIFICATION.md`.

**Self-checking:** run the emulated harness as often as you like. Those runs are
for you. They are not verification and do not go in `VERIFICATION.md`.

**When something disagrees with you, tell the reviewer, citing the spec section:**
- A harness check fails on behaviour you believe matches the spec. Do not work around the check.
- The spec is wrong or ambiguous. Do not code to your own reading of it.

Once you have told the reviewer, the question is the reviewer's. Do not put it
to the user or restate the reviewer's options; the user rules on it in the
reviewer's session. Your part resumes when the spec changes.

**The bench belongs to the testers.** It is one rig shared by both projects,
with one lock on all of it, the log `$REVERSE_IT_DIR/firmware/bench-log.jsonl`,
so the other project's tester may be running on it. Answer any question about
whether the bench is clear from `bench_lock.py status` and a process list,
never from memory. For bring-up work that is easier to do with the hardware in
hand, ask your component's tester for a loan. While you hold it, your
hardware runs still do not go in `VERIFICATION.md`. When you are done,
release it per the rules in `$REVERSE_IT_DIR/firmware/bench-holder.md` and
tell the tester that loaned it.

**Commit on your topic branch, `<project>/<role>/<topic>`, in your own
worktree.** Never push, and never touch `<project>/main`. Tell the reviewer
the branch and the range. If the reviewer sends back a conflict, merge
`<project>/main` into your branch (never a rebase).

**When a batch lands,** send the tester and the reviewer the same message: the
commit or build id, the spec sections touched and the files changed. Do not say
what you tested or what passed.

**Self-compact.** You have two compaction points:
- **The end of a step:** the step is committed, you have sent the "batch
  lands" message, and your batch or the reviewer's reply already names your
  next work, or says to wait for the next batch. Compact before you start on
  that work. The resume message names that work, or what you are waiting
  for, and where it is written. If you are only waiting, for the next batch
  or the reviewer's reply, and that is written in your working log, you may
  use `--compact-only` instead. When in doubt, resume (the skill's "Resume
  or compact-only").
- **After you start a long self-check run as a background task,** such as
  the emulated suite, a mutant run or a long build, once it is under way.
  First write in your working log: the task id, where its output goes, what
  the run checks, each uncommitted change and what it is for, and what you
  will do with the result. The resume message names the step and its batch,
  and says to wait for the task's notification without polling, then read
  the result and carry on with the step.

At either point you may run `/self-compact`. Not partway through an edit,
not with a question to the reviewer open, unless it is written in your
working log, with what each answer would change, and the resume message,
or with `--compact-only` the compact message, names it as awaited, and not while you are driving the bench by hand on
loan. A run that a background runner drives on the loaned bench doesn't
count.

When the idle-compaction hook asks (AGENTS.md, "Compacting your own
context"), that is a compaction point too, whatever state you are in: an
hour idle means no work is in progress. Before it, write in your working log
what is uncommitted, any question to the reviewer still open, and whether
you hold the bench on loan. The resume message names what you were waiting
for. If you are only waiting, for the next batch or the reviewer's reply,
and that is written in your working log, you may use `--compact-only`
instead. When in doubt, resume (the skill's "Resume or compact-only"). At
any other time, only when the user asks.

**Starting up:** check that you are in your worktree, on a
`$ARGUMENTS/implementor/<topic>` branch (`git branch --show-current`). If
not, stop and tell the user. Then read the spec sections named in your batch,
then the code.
