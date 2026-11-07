#!/usr/bin/env python3
"""Check that gen-inhibit/VERIFICATION.md is a move of reverse-it's gen-inhibit-test-evidence.md
and nothing more: the source's lines, in order, with only the substitutions listed in
verification_move_subs.json applied (gen-inhibit reviewer's ruling, 2026-11-07: "a move only";
port plan goal 20 item 3).

    py -3.14 -B gen-inhibit/tools/verification_move.py [--source PATH] [--target PATH]
                                                        [--subs PATH] [--complete]

Defaults: the source is $REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-test-evidence.md, the
target is gen-inhibit/VERIFICATION.md and the substitutions are verification_move_subs.json, both
beside this script's folder. Exit 0 when the target is a move, 1 when it is not (each problem
printed), 2 when nothing could be checked (a file missing, the substitution list malformed).

The rules:
- The source's line endings are dropped (it is CRLF); the target must be LF only, with a final
  newline.
- Each substitution is {"line": n, "old": regex, "new": text, "count": k, "why": text}. On the
  source's line n, the regex must match exactly k times (default 1); those matches are replaced by
  the text, literally. Several may name one line; they apply in list order. One that changes
  nothing is a problem. "old" is a pattern, never a removed value: the list is in git, and a
  removed bench identifier must not be recoverable from it (not even as a hash: they are short).
- The target's lines must equal the substituted source's first lines, one for one.
- The doc moves in batches, so the target may stop early, but only at a section boundary: the
  next source line is a "## " heading. --complete requires the whole source.
Every substitution is checked against the source, in the target's range or not, so a stale entry
shows before its batch. Gen-inhibit tester, 2026-11-07.

Retired since the merge (gen-inhibit/main 2396b4e3, 2026-11-07): the SeaDrive file is now a pointer
and VERIFICATION.md takes new material, so a run against them fails by design. The last run, on the
whole document at 7036d71c, is recorded in reverse-it's runs/verification_move_batch34_7036d71c/.
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_REL = ("projects", "vtrux", "notes", "gen-inhibit-test-evidence.md")
MAX_SHOWN = 20


class Refused(Exception):
    pass


def load_subs(text, n_source):
    """The substitution list, validated. Raises Refused on any malformed entry."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise Refused("the substitution list is not JSON: %s" % e)
    subs = data.get("substitutions") if isinstance(data, dict) else None
    if not isinstance(subs, list):
        raise Refused('the substitution list has no "substitutions" list')
    out = []
    for i, s in enumerate(subs, 1):
        if not isinstance(s, dict):
            raise Refused("substitution %d is not an object" % i)
        extra = set(s) - {"line", "old", "new", "count", "why"}
        if extra:
            raise Refused("substitution %d has unknown keys: %s" % (i, ", ".join(sorted(extra))))
        line, old, new = s.get("line"), s.get("old"), s.get("new")
        count, why = s.get("count", 1), s.get("why")
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= n_source:
            raise Refused("substitution %d: line must be 1 to %d, not %r" % (i, n_source, line))
        if not isinstance(old, str) or not old:
            raise Refused("substitution %d: old must be a non-empty pattern" % i)
        try:
            rx = re.compile(old)
        except re.error as e:
            raise Refused("substitution %d: old is not a valid pattern: %s" % (i, e))
        if not isinstance(new, str) or "\n" in new or "\r" in new:
            raise Refused("substitution %d: new must be text on one line" % i)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise Refused("substitution %d: count must be 1 or more" % i)
        if not isinstance(why, str) or not why.strip():
            raise Refused("substitution %d: why is required" % i)
        out.append({"n": i, "line": line, "rx": rx, "new": new, "count": count, "why": why})
    return out


def source_lines(raw):
    text = raw.decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [l[:-1] if l.endswith("\r") else l for l in lines]


def check(source_raw, target_raw, subs_text, complete=False):
    """(problems, info). Raises Refused when nothing can be checked."""
    src = source_lines(source_raw)
    subs = load_subs(subs_text, len(src))
    problems = []
    for s in subs:
        before = src[s["line"] - 1]
        found = len(s["rx"].findall(before))
        if found != s["count"]:
            problems.append("substitution %d (line %d, %s): its pattern matches %d time(s) on the "
                            "source line, not %d" % (s["n"], s["line"], s["why"], found, s["count"]))
            continue
        after = s["rx"].sub(lambda m: s["new"], before, count=s["count"])
        if after == before:
            problems.append("substitution %d (line %d, %s) changes nothing"
                            % (s["n"], s["line"], s["why"]))
        src[s["line"] - 1] = after
    try:
        target = target_raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return problems + ["the target is not UTF-8: %s" % e], {}
    if "\r" in target:
        problems.append("the target has CR characters: it must be LF only")
    if target and not target.endswith("\n"):
        problems.append("the target does not end with a newline")
    tgt = target.split("\n")
    if tgt and tgt[-1] == "":
        tgt.pop()
    tgt = [l.rstrip("\r") for l in tgt]
    if len(tgt) > len(src):
        problems.append("the target has %d lines, the source only %d" % (len(tgt), len(src)))
    diff = [i + 1 for i in range(min(len(tgt), len(src))) if tgt[i] != src[i]]
    for n in diff[:MAX_SHOWN]:
        problems.append("line %d differs from the source outside the listed substitutions" % n)
    if len(diff) > MAX_SHOWN:
        problems.append("... and %d more lines differ" % (len(diff) - MAX_SHOWN))
    if not tgt:
        problems.append("the target is empty")
    elif len(tgt) < len(src):
        if complete:
            problems.append("--complete: the target stops at line %d of %d" % (len(tgt), len(src)))
        elif not src[len(tgt)].startswith("## "):
            problems.append("the target stops at line %d, inside a section: the next source line "
                            "is not a '## ' heading" % len(tgt))
    info = {"source_lines": len(src), "target_lines": len(tgt),
            "subs_in_target": sum(1 for s in subs if s["line"] <= len(tgt)),
            "subs_after_target": sum(1 for s in subs if s["line"] > len(tgt))}
    return problems, info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check that VERIFICATION.md is a move of the evidence doc.")
    ap.add_argument("--source")
    ap.add_argument("--target", default=os.path.join(os.path.dirname(HERE), "VERIFICATION.md"))
    ap.add_argument("--subs", default=os.path.join(HERE, "verification_move_subs.json"))
    ap.add_argument("--complete", action="store_true")
    a = ap.parse_args(argv)
    try:
        source = a.source
        if not source:
            ri = os.environ.get("REVERSE_IT_DIR", "").strip()
            if not ri:
                raise Refused("REVERSE_IT_DIR is not set and no --source was given")
            source = os.path.join(ri, *SOURCE_REL)
        raws = []
        for what, p in (("source", source), ("target", a.target), ("substitution list", a.subs)):
            if not os.path.isfile(p):
                raise Refused("the %s is not a file: %s" % (what, p))
            with open(p, "rb") as f:
                raws.append(f.read())
        problems, info = check(raws[0], raws[1], raws[2].decode("utf-8"), a.complete)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    for what, raw in (("source", raws[0]), ("target", raws[1]), ("substitutions", raws[2])):
        print("%-14s sha256 %s" % (what, hashlib.sha256(raw).hexdigest()))
    if info:
        print("source %d lines, target %d lines; substitutions: %d in the target's range, %d after it"
              % (info["source_lines"], info["target_lines"], info["subs_in_target"],
                 info["subs_after_target"]))
    for p in problems:
        print("PROBLEM  %s" % p)
    print("NOT A MOVE: %d problem(s)" % len(problems) if problems else "OK: the target is a move")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
