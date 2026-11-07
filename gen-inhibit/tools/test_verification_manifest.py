#!/usr/bin/env python3
"""Test of verification_manifest.py (VERIFICATION.md's hash manifest), on a made-up reverse-it
folder in this run's own folder in the worktree's scratch/, removed at exit.

    py -3.14 -B gen-inhibit/tools/test_verification_manifest.py

VMANIFEST_SCRIPT names another copy of the script to test (the mutants runner sets it). Prints one
"ok"/"FAIL" line per check and exits 1 on any FAIL. Gen-inhibit tester, 2026-11-07.
"""
import hashlib
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("VMANIFEST_SCRIPT") or os.path.join(HERE, "verification_manifest.py")
spec = importlib.util.spec_from_file_location("verification_manifest_under_test", SCRIPT)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else "  -- %s" % (detail,)))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def put(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


RUNS = "projects/vtrux/notes/artifacts/gen-inhibit/runs/"
DOC = ("# Doc\n"
       "Run files are in `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/`.\n"
       "A file `runs/a.json`, a folder `runs/b/` and again `runs/a.json`.\n"
       "Braces `runs/c_{x,y}.txt` and a range `runs/arm{1..3}.json`.\n"
       "A glob `runs/g_*.log`, a stem `runs/stem_20261107`, a pattern `runs/<run>.json`.\n"
       "Gone: `runs/nothere.json`. Elsewhere `$REVERSE_IT_DIR/firmware/f.md`. Not cited: runs/z.txt.\n"
       "Trailing dot `runs/a.json`. and a chain `runs/chain/` with `x/runs/q.json` not a citation.\n"
       "A stem cut short with its dot inside: `runs/t_20261107.`\n"
       "A living doc `$REVERSE_IT_DIR/firmware/bench-holder.md` and a run's own `runs/b/README.md`.\n")


def fixture(root):
    files = {RUNS + "a.json": b"A", RUNS + "b/one.txt": b"1", RUNS + "b/sub/two.txt": b"2",
             RUNS + "c_x.txt": b"cx", RUNS + "c_y.txt": b"cy", RUNS + "arm1.json": b"r1",
             RUNS + "arm3.json": b"r3", RUNS + "g_1.log": b"g1", RUNS + "g_2.log": b"g2",
             RUNS + "stem_20261107.json": b"sj", RUNS + "stem_20261107_by_source.txt": b"st",
             RUNS + "stem_202611079.json": b"not this stem", RUNS + "z.txt": b"z",
             RUNS + "t_20261107.json": b"t",
             RUNS + "chain/r1/record.json": b"c", "firmware/f.md": b"F",
             "firmware/bench-holder.md": b"holder", RUNS + "b/README.md": b"run readme"}
    for rel, data in files.items():
        put(root, rel, data)
    os.makedirs(os.path.join(root, "canre"), exist_ok=True)


def row(rows, path):
    return [r for r in rows if r.split()[2] == path]


def main():
    root = os.path.join(_scratch(), "ri")
    fixture(root)
    rows = M.rows(DOC, root)
    kinds = {r.split()[2]: r.split()[1] for r in rows}

    r = row(rows, RUNS + "a.json")
    check("a cited file is one row with its sha256, at its first line",
          r == ["%s  file     %s  L3" % (sha(b"A"), RUNS + "a.json")], r)
    want_tree = sha(("README.md %s\none.txt %s\nsub/two.txt %s"
                     % (sha(b"run readme"), sha(b"1"), sha(b"2"))).encode())
    r = row(rows, RUNS + "b/")
    check("a cited folder is a tree row: sha256 of its sorted 'relpath sha256' lines",
          r == ["%s  tree     %s  L3" % (want_tree, RUNS + "b/")], r)
    check("braces expand to each file", kinds.get(RUNS + "c_x.txt") == "file" and
          kinds.get(RUNS + "c_y.txt") == "file", kinds)
    check("a range expands; an expansion that does not exist, beside ones that do, is absent",
          kinds.get(RUNS + "arm1.json") == "file" and kinds.get(RUNS + "arm2.json") == "absent" and
          kinds.get(RUNS + "arm3.json") == "file", kinds)
    check("a glob lists each match", kinds.get(RUNS + "g_1.log") == "file" and
          kinds.get(RUNS + "g_2.log") == "file", kinds)
    check("a stem resolves to <stem>.* and <stem>_*", kinds.get(RUNS + "stem_20261107.json") == "file"
          and kinds.get(RUNS + "stem_20261107_by_source.txt") == "file", kinds)
    check("... but not to a file whose name only starts with the stem's characters",
          RUNS + "stem_202611079.json" not in kinds, kinds)
    check("a trailing dot inside the backticks is dropped, so the cut-short stem resolves",
          kinds.get(RUNS + "t_20261107.json") == "file" and RUNS + "t_20261107." not in kinds, kinds)
    check("a citation with <...> is a pattern, not hashed",
          row(rows, "runs/<run>.json") == ["-  pattern  runs/<run>.json  L5"], row(rows, "runs/<run>.json"))
    check("a citation that resolves to nothing is missing",
          row(rows, RUNS + "nothere.json") == ["-  missing  %s  L6" % (RUNS + "nothere.json")], kinds)
    check("a $REVERSE_IT_DIR/ citation resolves from the root", kinds.get("firmware/f.md") == "file", kinds)
    check("runs/ and runs/chain/ are roots, named but never hashed",
          row(rows, RUNS) == ["-  root     %s  L2" % RUNS] and
          row(rows, RUNS + "chain/") == ["-  root     %s  L7" % (RUNS + "chain/")], (row(rows, RUNS),))
    check("a living document (bench-holder.md) is named, never hashed",
          row(rows, "firmware/bench-holder.md") == ["-  living   firmware/bench-holder.md  L9"],
          row(rows, "firmware/bench-holder.md"))
    check("... but a run folder's own README stays hashed",
          row(rows, RUNS + "b/README.md") == ["%s  file     %s  L9" % (sha(b"run readme"), RUNS + "b/README.md")],
          row(rows, RUNS + "b/README.md"))
    check("an unbackticked path is not a citation", RUNS + "z.txt" not in kinds, kinds)
    check("a backticked path not starting runs/ is not a citation",
          not any("q.json" in k for k in kinds), kinds)
    check("one row per path, sorted by path", len(kinds) == len(rows) and
          [x.split()[2] for x in rows] == sorted(x.split()[2] for x in rows), rows)

    check("brace expansion: nested and repeated",
          M.expand_braces("a{b,c{d,e}}f{1..2}") == ["abf1", "abf2", "acdf1", "acdf2", "acef1", "acef2"],
          M.expand_braces("a{b,c{d,e}}f{1..2}"))
    check("brace expansion: a falling range", M.expand_braces("x{3..1}") == ["x3", "x2", "x1"],
          M.expand_braces("x{3..1}"))
    check("brace expansion: a lone {x} stays literal", M.expand_braces("a{x}b") == ["a{x}b"],
          M.expand_braces("a{x}b"))

    doc_p = put(root, "doc/VERIFICATION.md", DOC.encode())
    man_p = os.path.join(root, "doc", "VERIFICATION-manifest.txt")
    env = dict(os.environ, REVERSE_IT_DIR=root)
    base = [sys.executable, "-B", SCRIPT]
    w = subprocess.run(base + ["write", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    text = open(man_p, encoding="utf-8", newline="").read() if os.path.exists(man_p) else ""
    check("write exits 0 and writes LF text", w.returncode == 0 and text and "\r" not in text,
          (w.returncode, w.stdout[-200:], w.stderr[-300:]))
    check("the header names the document's sha256", "# document sha256 %s" % sha(DOC.encode()) in text,
          text[:400])
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    check("check of a fresh manifest exits 0", c.returncode == 0 and "OK" in c.stdout, (c.returncode, c.stdout))
    put(root, "firmware/bench-holder.md", b"holder, handed over")
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    check("a living document that changes leaves the manifest current", c.returncode == 0 and "OK" in c.stdout,
          (c.returncode, c.stdout[-400:]))
    put(root, RUNS + "b/sub/two.txt", b"changed")
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    check("a changed file in a cited folder makes check exit 1, naming the folder",
          c.returncode == 1 and RUNS + "b/" in c.stdout, (c.returncode, c.stdout[-400:]))
    put(root, RUNS + "b/sub/two.txt", b"2")
    put(root, "doc/VERIFICATION.md", (DOC + "One more line.\n").encode())
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    check("a changed document makes the manifest stale (its sha256 in the header)",
          c.returncode == 1 and "document sha256" in c.stdout, (c.returncode, c.stdout[-400:]))
    put(root, "doc/VERIFICATION.md", DOC.encode())
    os.remove(os.path.join(root, *(RUNS + "g_2.log").split("/")))
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=env)
    check("a cited file gone makes check exit 1", c.returncode == 1 and "g_2.log" in c.stdout,
          (c.returncode, c.stdout[-400:]))
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p], capture_output=True, text=True,
                       env=dict(os.environ, REVERSE_IT_DIR=""))
    check("no REVERSE_IT_DIR is refused, exit 2", c.returncode == 2 and "REFUSED" in c.stdout,
          (c.returncode, c.stdout[-200:]))
    c = subprocess.run(base + ["check", "--doc", doc_p, "--manifest", man_p + ".none"], capture_output=True,
                       text=True, env=env)
    check("a missing manifest is refused, exit 2", c.returncode == 2 and "REFUSED" in c.stdout,
          (c.returncode, c.stdout[-200:]))

    bad = results.count(False)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not bad else "FAIL", results.count(True), bad))
    return 1 if bad else 0


def _scratch():
    sys.path.insert(0, os.path.join(HERE, "bench"))
    import gi_paths
    return gi_paths.scratch("verification_manifest", start=HERE)


if __name__ == "__main__":
    sys.exit(main())
