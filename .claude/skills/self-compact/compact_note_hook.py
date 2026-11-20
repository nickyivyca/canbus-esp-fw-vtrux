"""SessionStart hook (matcher "compact"): after any compaction, tell the session which skill
copies in its context are stale.

Claude Code re-attaches each skill invoked earlier as the text it had when invoked, not the
SKILL.md on disk. After a compaction this hook compares each attached copy with the file and
adds one sentence to the session's context: the stale copies to re-read in full, or that all
match. It runs on every compaction: a self-compaction (with or without a resume), an idle
compaction, or one at the context limit. Its output waits in context for whatever next wakes
the session (the hook starts no turn), so a compact-only session gets it on its real wake.

Read-only, apart from its log. Any error still prints a sentence (re-read your role skill) and
exits 0, so it can never block or break a session start.

One entry under SessionStart, matcher "compact", in settings.local.json, guarded so a missing
file prints nothing:
  S="$CLAUDE_PROJECT_DIR/.claude/skills/self-compact/compact_note_hook.py"; test -f "$S" && <python> "$S"
where <python> is the machine's command from local-paths.md. Log:
scratch/session-compact/compact-note.log in the session's worktree.
"""
import datetime
import json
import os
import re
import sys

SKILL_MARK = "Base directory for this skill: "
FALLBACK = "Skill check failed; re-read your role skill in full before acting."


def attached_skills(path):
    """Latest attached text per skill folder: the invocation itself, or the invoked_skills
    attachment Claude Code re-attaches after a compaction."""
    last = {}

    def take(text):
        if text.startswith(SKILL_MARK):
            last[text[len(SKILL_MARK):].split("\n", 1)[0].strip()] = text

    with open(path, "rb") as f:
        for raw in f:
            if SKILL_MARK.encode() not in raw:
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if r.get("isSidechain"):
                continue
            if r.get("type") == "attachment":
                a = r.get("attachment") or {}
                if a.get("type") == "invoked_skills":
                    for s in a.get("skills") or []:
                        take(s.get("content") or "")
            elif r.get("type") == "user":
                c = (r.get("message") or {}).get("content")
                for t in [c] if isinstance(c, str) else [x.get("text", "") for x in c or []
                                                         if isinstance(x, dict) and x.get("type") == "text"]:
                    take(t)
    return last


def copy_is_current(d, attached):
    """True if the attached copy is SKILL.md as it is now, with the invocation's arguments
    matched as any text and ${CLAUDE_SKILL_DIR} filled in."""
    with open(os.path.join(d, "SKILL.md"), encoding="utf-8") as f:
        body = f.read()
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    body = body.strip().replace("${CLAUDE_SKILL_DIR}", d)
    got = attached[len(SKILL_MARK):].split("\n", 1)[1].strip() if "\n" in attached else ""
    return re.fullmatch("(.*?)".join(re.escape(p) for p in body.split("$ARGUMENTS")), got, re.S) is not None


def skill_note(transcript, root):
    stale, current = [], []
    for d, text in sorted(attached_skills(transcript).items()):
        f = os.path.join(d, "SKILL.md")
        name = os.path.relpath(f, root).replace(os.sep, "/") if root else f
        if os.path.isfile(f) and copy_is_current(d, text):
            current.append(name)
        else:
            stale.append(name)
    many = len(stale) > 1
    if stale:
        return ("Skill check after this compaction: the attached %s of %s %s stale; re-read %s in full"
                " before acting." % ("copies" if many else "copy", ", ".join(stale),
                                     "are" if many else "is", "them" if many else "it"))
    if current:
        many = len(current) > 1
        return ("Skill check after this compaction: the attached %s of %s match%s the file%s; no skill"
                " re-read needed." % ("copies" if many else "copy", ", ".join(current),
                                      "" if many else "es", "s" if many else ""))
    return "Skill check after this compaction: no invoked skill found; re-read your role skill in full before acting."


def log(root, line):
    try:
        d = os.path.join(root or ".", "scratch", "session-compact")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "compact-note.log"), "a", encoding="ascii", errors="replace") as f:
            f.write("%s %s\n" % (datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), line))
    except OSError:
        pass


def main():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    note = FALLBACK
    try:
        event = json.load(sys.stdin)
        if event.get("source") != "compact":
            return 0
        note = skill_note(event["transcript_path"], root)
    except Exception as e:
        note = FALLBACK
        log(root, "error %s" % type(e).__name__)
    note = "".join(c for c in note if 0x20 <= ord(c) <= 0x7E)
    log(root, note)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": note}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
