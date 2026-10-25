#!/usr/bin/env python3
"""Mutants of gi_diag.py; test_gi_diag.py must fail on each BY A CHECK. A crash is not a
kill (a FAIL whose detail starts CRASH does not count), and an edit that does not apply
is an error. Each mutant is a copy of gi_diag.py, with pins.json, in this run's scratch/
folder, run through GI_DIAG_DIR. Gen-inhibit tester, 2026-10-24 (toolkit item 10).

    py -3.14 -B test_gi_diag_mutants.py
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "gi_diag.py"
MUTANTS = [
    ("the schema table is not checked", "            probs = D.check_identity(db) + check_table(db, v)\n",
     "            probs = D.check_identity(db)\n"),
    ("check_table reads the start bit from the table, not the DBC", "        got = (sig.start, sig.length,",
     "        got = (byte * 8, sig.length,"),
    ("an unknown schema falls back to the newest",
     "        e = self.entries.get(version)\n        if e is None:\n            raise UnknownSchema(",
     "        e = self.entries.get(version)\n        if e is None:\n            return self.entries[max(self.entries)]\n"
     "        if e is None:\n            raise UnknownSchema("),
    ("a page before the first schema page decodes on HEAD's schema",
     '            return self._bad(frame_id, t, "before the first schema page")',
     "            self.version = self.schemas.head_version"),
    ("the run identity is never compared", '            if self.identity is not None and e.hash != self.identity["dbc_hash"]:',
     "            if False:"),
    ("the schema switches only at the first schema page", "            if e.version != self.version:",
     "            if self.version is None:"),
    ("run_identity takes an uncommitted DBC", "    if dirty:\n        raise DiagError(", "    if False:\n        raise DiagError("),
    ("from_git skips check_history", "        if probs:\n            raise DiagError(\"%s's history",
     "        if False:\n            raise DiagError(\"%s's history"),
    ("from_git keeps the first commit of a version", "            entries[v] = (c, h, text)",
     "            entries.setdefault(v, (c, h, text))"),
    ("core() never compares the blob", '    if got != pin["blob"]:', "    if False:"),
    ("the interposer DBC's sha256 is not compared", '    if got != pin["sha256"]:', "    if False:"),
    ("an unpinned interposer read is allowed",
     '    if pin is None:\n        raise DiagError("%s has no pin',
     '    if pin is None:\n        pin = {"commit": "d83fc87", "sha256": ""}\n    if False:\n        raise DiagError("%s has no pin'),
    ("a filed hash is not compared with the stated one", "            if sh != h:", "            if False:"),
    ("an export's identity is not compared", '        if s.identity is not None and s.get(s.identity["dbc_version"]).hash',
     '        if False and s.get(s.identity["dbc_version"]).hash'),
    ("normalised traces keep the schema signals", "if k not in SCHEMA_SIGNALS)", "if True)"),
    ("normalised traces ignore RenamedFrom", "        sigs = sorted((ren.get(k, k), v) for k, v in page.items())",
     "        sigs = sorted((k, v) for k, v in page.items())"),
    ("an undecodable trace line loses its raw text", '            out.append("%s   [undecodable: %s]" % (ln, page.reason))',
     '            out.append("[undecodable: %s]" % page.reason)'),
    ("decode_pages does not sort by time", "    for t, i, d in sorted(frames, key=lambda f: f[0]):\n        if i not in dec.ids:",
     "    for t, i, d in frames:\n        if i not in dec.ids:"),
    ("the export leaves out the identity", '"identity": identity, "core"', '"identity": None, "core"'),
]


def run_test(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_gi_diag.py")], capture_output=True, text=True,
                       env=dict(os.environ, GI_DIAG_DIR=d), cwd=HERE, timeout=900)
    fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
    crashed = "Traceback" in r.stderr
    dead = bool(fails) and not crashed and not all(" CRASH " in f for f in fails)
    return dead, crashed, fails, r.returncode


def copy(base, name, text):
    d = os.path.join(base, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
    shutil.copy(os.path.join(HERE, "pins.json"), d)
    return d


def main():
    base = gi_paths.scratch("mutants-gi_diag")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    dead, crashed, fails, rc = run_test(copy(base, "control", src))
    control_ok = rc == 0 and not fails
    print("%s control: the unmutated copy  (exit %d, %d FAIL)%s"
          % ("PASS    " if control_ok else "FAILED  ", rc, len(fails), "".join("\n           " + f[:130] for f in fails[:3])))
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        dead, crashed, fails, rc = run_test(copy(base, "m%d" % n, src.replace(old, new)))
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:130] for f in fails[:2])))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
