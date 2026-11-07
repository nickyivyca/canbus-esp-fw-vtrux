#!/usr/bin/env python3
"""Test of verification_move.py (the VERIFICATION.md move checker), on made-up documents.

    py -3.14 -B gen-inhibit/tools/test_verification_move.py

VMOVE_SCRIPT names another copy of the script to test (the mutants runner sets it). The command
line cases write their files in this run's own folder in the worktree's scratch/, removed at
exit. Prints one "ok"/"FAIL" line per check and exits 1 on any FAIL. Gen-inhibit tester,
2026-11-07.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("VMOVE_SCRIPT") or os.path.join(HERE, "verification_move.py")
spec = importlib.util.spec_from_file_location("verification_move_under_test", SCRIPT)
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else "  -- %s" % (detail,)))


FAKE_MAC = ":".join(["aa", "bb", "cc", "dd", "ee", "ff"])     # made up, built from parts
SRC = ("# Title\r\n"
       "\r\n"
       "## 1 First\r\n"
       "Run files are in `artifacts/runs/`.\r\n"
       "The unit's MAC is **`" + FAKE_MAC + "`** here.\r\n"
       "two x and x here\r\n"
       "\r\n"
       "## 2 Second\r\n"
       "body of two\r\n"
       "last line\r\n")
SRC_LINES = SRC.replace("\r\n", "\n")


def subs(*entries):
    return json.dumps({"substitutions": [dict(e) for e in entries]})


def S(line, old, new, why="test", **kw):
    d = {"line": line, "old": old, "new": new, "why": why}
    d.update(kw)
    return d


def run(target, sub_text="{\"substitutions\": []}", complete=False, source=SRC):
    """check()'s (problems, info); anything it raises becomes a problem, so a check fails on it
    rather than the test crashing."""
    try:
        return V.check(source.encode(), target.encode(), sub_text, complete)
    except Exception as e:
        return ["check() raised %s: %s" % (type(e).__name__, e)], {}


def has(problems, text):
    return any(text in p for p in problems)


def first_lines(text, n):
    return "".join(text.splitlines(True)[:n])


def refused(target, sub_text):
    """Refused's text, or None when check() did not refuse (anything else it raises included)."""
    try:
        V.check(SRC.encode(), target.encode(), sub_text, False)
    except V.Refused as e:
        return str(e)
    except Exception:
        return None
    return None


def main():
    whole = SRC_LINES
    p, info = run(whole)
    check("an LF copy of a CRLF source is a move", p == [], p)
    check("the info counts the lines", info.get("source_lines") == 10 and info.get("target_lines") == 10, info)

    p, _ = run(first_lines(whole, 7))
    check("a target stopping before a '## ' heading is a move", p == [], p)
    p, _ = run(first_lines(whole, 6))
    check("a target stopping inside a section is not", has(p, "inside a section"), p)
    p, _ = run(first_lines(whole, 7), complete=True)
    check("--complete refuses a partial target", has(p, "--complete"), p)
    p, _ = run(whole, complete=True)
    check("--complete takes the whole source", p == [], p)

    p, _ = run(whole.replace("\n", "\r\n"))
    check("a CRLF target is not a move", has(p, "CR characters"), p)
    p, _ = run(whole[:-1])
    check("a target without a final newline is not a move", has(p, "final newline") or
          has(p, "end with a newline"), p)
    p, _ = run(whole.replace("body of two", "body of 2"))
    check("an unlisted change is reported at its line", has(p, "line 9 differs"), p)
    p, _ = run(whole.replace("body of two\n", ""))
    check("a dropped line is not a move", any("differs" in x for x in p), p)
    p, _ = run(whole + "an added line\n")
    check("a target longer than the source is not a move", has(p, "the source only"), p)
    p, _ = run("")
    check("an empty target is not a move", has(p, "empty"), p)

    mac = S(5, r"\*\*`[0-9a-f]{2}(?::[0-9a-f]{2}){5}`\*\*", "on record elsewhere", "mac")
    done = whole.replace("**`" + FAKE_MAC + "`**", "on record elsewhere")
    p, _ = run(done, subs(mac))
    check("a listed substitution applied is a move", p == [], p)
    p, _ = run(whole, subs(mac))
    check("a listed substitution not applied is not a move", has(p, "line 5 differs"), p)
    p, _ = run(done, subs(S(5, "nomatch", "z")))
    check("a substitution whose pattern does not match the source is reported",
          has(p, "matches 0 time(s)"), p)
    p, _ = run(whole.replace("two x and x", "two y and x"), subs(S(6, "x", "y")))
    check("a pattern matching twice with count 1 is reported", has(p, "matches 2 time(s)"), p)
    p, _ = run(whole.replace("two x and x", "two y and y"), subs(S(6, "x", "y", count=2)))
    check("count 2 replaces both", p == [], p)
    p, _ = run(whole, subs(S(9, "two", "two")))
    check("a substitution that changes nothing is reported", has(p, "changes nothing"), p)
    lit = S(4, "`artifacts/", r"`$R/\1\g<0>/")
    p, _ = run(whole.replace("`artifacts/", r"`$R/\1\g<0>/"), subs(lit))
    check("the new text goes in literally (no group references)", p == [], p)
    two = [S(5, r"\*\*`[0-9a-f:]{17}`\*\*", "MACX"), S(5, "MACX", "on record")]
    p, _ = run(whole.replace("**`" + FAKE_MAC + "`**", "on record"), subs(*two))
    check("two substitutions on one line apply in order", p == [], p)
    p, _ = run(first_lines(whole, 7), subs(S(9, "body", "BODY")))
    check("a substitution after the target's range is still checked on the source", p == [], p)
    p, _ = run(first_lines(whole, 7), subs(S(9, "nobody", "BODY")))
    check("... and a stale one there is reported", has(p, "matches 0 time(s)"), p)

    for name, text, want in [
            ("not JSON", "{", "not JSON"),
            ("no list", "{}", "no \"substitutions\""),
            ("line 0", subs(S(0, "a", "b")), "line must be"),
            ("line past the source", subs(S(11, "a", "b")), "line must be"),
            ("a bool line", subs(S(True, "a", "b")), "line must be"),
            ("no why", subs({"line": 4, "old": "a", "new": "b"}), "why is required"),
            ("a bad pattern", subs(S(4, "(", "b")), "not a valid pattern"),
            ("a two-line new", subs(S(4, "a", "b\nc")), "one line"),
            ("count 0", subs(S(4, "a", "b", count=0)), "count must be"),
            ("an unknown key", subs(S(4, "a", "b", hash="x")), "unknown keys")]:
        r = refused(whole, text)
        check("a substitution list with %s is refused" % name, r is not None and want in r, r)

    d = os.path.join(_scratch(), "cli")
    os.makedirs(d, exist_ok=True)
    src_p, tgt_p, sub_p = (os.path.join(d, n) for n in ("src.md", "VERIFICATION.md", "subs.json"))
    open(src_p, "wb").write(SRC.encode())
    open(tgt_p, "wb").write(done.encode())
    open(sub_p, "w", encoding="utf-8").write(subs(mac))
    cmd = [sys.executable, "-B", SCRIPT, "--source", src_p, "--target", tgt_p, "--subs", sub_p]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("command line: a move exits 0 and says so", r.returncode == 0 and "OK: the target is a move"
          in r.stdout, (r.returncode, r.stdout[-200:], r.stderr[-200:]))
    check("command line: it prints the three files' sha256", r.stdout.count(" sha256 ") == 3, r.stdout)
    open(tgt_p, "wb").write(whole.encode())
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("command line: not a move exits 1 with the problem", r.returncode == 1 and
          "line 5 differs" in r.stdout, (r.returncode, r.stdout[-300:]))
    r = subprocess.run(cmd[:-2] + ["--subs", os.path.join(d, "missing.json")], capture_output=True, text=True)
    check("command line: a missing file is refused, exit 2", r.returncode == 2 and "REFUSED" in r.stdout,
          (r.returncode, r.stdout[-200:]))
    r = subprocess.run([sys.executable, "-B", SCRIPT, "--target", tgt_p, "--subs", sub_p], capture_output=True,
                       text=True, env=dict(os.environ, REVERSE_IT_DIR=""))
    check("command line: no --source and no REVERSE_IT_DIR is refused", r.returncode == 2 and
          "REVERSE_IT_DIR" in r.stdout, (r.returncode, r.stdout[-200:]))

    real = os.path.join(HERE, "verification_move_subs.json")
    try:
        n = len(V.load_subs(open(real, encoding="utf-8").read(), 10 ** 6))
        check("the repo's substitution list is well formed (%d entries)" % n, n > 0)
    except V.Refused as e:
        check("the repo's substitution list is well formed", False, str(e))

    bad = results.count(False)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not bad else "FAIL", results.count(True), bad))
    return 1 if bad else 0


def _scratch():
    sys.path.insert(0, os.path.join(HERE, "bench"))
    import gi_paths
    return gi_paths.scratch("verification_move", start=HERE)


if __name__ == "__main__":
    sys.exit(main())
