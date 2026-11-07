#!/usr/bin/env python3
"""Mutants of verification_manifest.py; test_verification_manifest.py must fail on each BY A
CHECK. A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/test_verification_manifest_mutants.py

Each mutant is a copy of the script in the worktree's scratch/, one edit applied, run by the test
through VMANIFEST_SCRIPT. The unmutated copy runs first, as the control: if it fails, no mutant is
judged and the run exits 1. Gen-inhibit tester, 2026-11-07.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders

SRC = "verification_manifest.py"
TEST = "test_verification_manifest.py"
MUTANTS = [
    ("a folder's digest over its top level only",
     '        dns.sort()\n', '        dns[:] = []\n'),
    ("a folder's digest without the file names",
     '            lines.append("%s %s" % (os.path.relpath(p, path).replace("\\\\", "/"), sha256_file(p)))\n',
     '            lines.append(sha256_file(p))\n'),
    ("ranges not expanded",
     '                if m:\n', '                if False:\n'),
    ("a lone {x} split as one alternative",
     '                    if len(alts) == 1:              # "{x}" is literal in the shell\n',
     '                    if False:\n'),
    ("unbackticked paths read too",
     'CITE = re.compile(r"`([^`\\s]+)`")\n', 'CITE = re.compile(r"`?(\\S*runs/[^`\\s]+)`?")\n'),
    ("trailing punctuation kept",
     '            t = m.group(1).rstrip(TRAIL)\n', '            t = m.group(1)\n'),
    ("patterns resolved as paths",
     '        if "<" in cite or ">" in cite or "..." in cite:\n', '        if False:\n'),
    ("globs not expanded",
     '    if any(c in rel for c in "*?["):\n', '    if False:\n'),
    ("no stems",
     '                      if f.startswith(base + ".") or f.startswith(base + "_")) if base else []\n',
     '                      if False) if base else []\n'),
    ("a stem matches any name starting with it",
     '                      if f.startswith(base + ".") or f.startswith(base + "_")) if base else []\n',
     '                      if f.startswith(base)) if base else []\n'),
    ("absent and missing not told apart",
     '                seen.setdefault(("absent" if any_found else "missing", rel), n)\n',
     '                seen.setdefault(("missing", rel), n)\n'),
    ("roots hashed",
     '                kind = "root" if h in ROOTS else "living" if h in LIVING else "path"\n',
     '                kind = "living" if h in LIVING else "path"\n'),
    ("living documents hashed",
     '                kind = "root" if h in ROOTS else "living" if h in LIVING else "path"\n',
     '                kind = "root" if h in ROOTS else "path"\n'),
    ("every README living, a run folder's own too",
     '                kind = "root" if h in ROOTS else "living" if h in LIVING else "path"\n',
     '                kind = "root" if h in ROOTS else "living" if h in LIVING or h.endswith("README.md") else "path"\n'),
    ("bench-holder.md not living",
     'LIVING = {"firmware/bench-holder.md", "firmware/bench-log.jsonl", NOTES + "gen-inhibit-wican-spec.md",\n',
     'LIVING = {"firmware/bench-log.jsonl", NOTES + "gen-inhibit-wican-spec.md",\n'),
    ("[_x] read as a glob class",
     '        rel0 = OPTIONAL.sub(lambda m: "{," + m.group(1) + "}", rel0)\n', '        pass\n'),
    ("every [..] read as optional, glob classes too",
     'OPTIONAL = re.compile(r"\\[(_[^\\]\\[]+)\\]")\n', 'OPTIONAL = re.compile(r"\\[([^\\]\\[]+)\\]")\n'),
    ("moved files not followed",
     '            if not hits and rel in MOVED and os.path.isfile(os.path.join(ri, *MOVED[rel].split("/"))):\n',
     '            if False:\n'),
    ("a moved row without its new path",
     '            out.append("%s  moved    %s -> %s  L%d" % (sha256_file(full), path, MOVED[path], n))\n',
     '            out.append("%s  moved    %s  L%d" % (sha256_file(full), path, n))\n'),
    ("the last citing line, not the first",
     '                seen.setdefault((kind, h), n)\n',
     '                seen[(kind, h)] = n\n'),
    ("$REVERSE_IT_DIR/ citations read under runs/",
     '        rel0 = cite[len("$REVERSE_IT_DIR/"):] if cite.startswith("$REVERSE_IT_DIR/") else RUNS_REL + cite\n',
     '        rel0 = RUNS_REL + cite\n'),
    ("the document's sha256 not in the header",
     '            "# document sha256 %s" % hashlib.sha256(doc_raw).hexdigest(),\n',
     '            "# document sha256 -",\n'),
    ("check always current",
     '    if have == want:\n', '    if True:\n'),
    ("no REVERSE_IT_DIR not refused",
     '        if not ri or not os.path.isdir(ri):\n', '        if False:\n'),
]


def main():
    base = gi_paths.scratch("mutants-verification_manifest")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, VMANIFEST_SCRIPT=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
