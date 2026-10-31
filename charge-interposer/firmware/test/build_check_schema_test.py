"""Tests the HARNESS: build_check.check_schema_version (user ruling,
2026-10-19: the firmware's schema constant equals the diag DBC's
DiagSchemaVersion in the same commit).

Runs on copies of the real machine.h and diag DBC in a scratch_dir run
folder, one per run() and removed in its finally (2026-10-31):
the unedited pair must pass, and each edit below must fail -- the constant
or the attribute moved alone, the constant missing, written twice or in
another form, the attribute missing or a string. Each edit first proves it
applied, so a text edit that matched nothing cannot pass as a clean run.

    py -3.14 charge-interposer/firmware/test/build_check_schema_test.py
    ... --can-fail   each mutant of the check must make this test fail
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# charge-interposer/, for scratch_dir; last, so it shadows nothing
sys.path.append(os.path.dirname(os.path.dirname(HERE)))
import scratch_dir                                  # noqa: E402

import build_check as BC                            # noqa: E402

WORK = None            # each run()'s scratch_dir folder
DECL = "static const uint8_t DIAG_SCHEMA_VER = 4;"
VER = 'BA_ "DiagSchemaVersion" 4;'

# (name, edits to machine.h, edits to the DBC, text a problem must hold)
CASES = [
    ("the constant moved alone", [(DECL, DECL.replace("4", "5"))], [],
     "DIAG_SCHEMA_VER = 5 but"),
    ("the attribute moved alone", [], [(VER, VER.replace("4", "3"))],
     "DiagSchemaVersion 3"),
    ("no constant", [(DECL, "")], [], "found 0 in that form"),
    ("the constant twice", [(DECL, DECL + "\n" + DECL)], [],
     "found 2 in that form"),
    ("a #define as well", [(DECL, DECL + "\n#define DIAG_SCHEMA_VER 4")], [],
     "2 definition(s) in any form"),
    ("another type", [(DECL, DECL.replace("uint8_t", "int"))], [],
     "found 0 in that form"),
    ("no attribute", [], [(VER + "\n", "")], "no integer DiagSchemaVersion"),
    ("two values", [], [(VER, VER + "\n" + VER.replace("4", "5"))],
     "no integer DiagSchemaVersion"),
    ("a string attribute", [],
     [('BA_DEF_ "DiagSchemaVersion" INT 0 255;',
       'BA_DEF_ "DiagSchemaVersion" STRING ;'),
      ('BA_DEF_DEF_ "DiagSchemaVersion" 0;',
       'BA_DEF_DEF_ "DiagSchemaVersion" "";'),
      (VER, 'BA_ "DiagSchemaVersion" "4";')],
     "no integer DiagSchemaVersion"),
]


def _copy(name, h_edits, dbc_edits):
    d = os.path.join(WORK, name.replace(" ", "_").replace("#", ""))
    os.makedirs(d, exist_ok=True)
    out = []
    for src, edits in ((BC.MACHINE_H, h_edits), (BC.DIAG_DBC, dbc_edits)):
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for a, b in edits:
            assert text.count(a) == 1, "%s: edit target %r occurs %d times" % (
                name, a, text.count(a))
            text = text.replace(a, b)
        p = os.path.join(d, os.path.basename(src))
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        out.append(p)
    return out


def run():
    """-> list of failures (empty: the check behaves)."""
    global WORK
    WORK = scratch_dir.make("build-check-schema-test")
    try:
        return _run()
    finally:
        scratch_dir.remove(WORK)


def _run():
    bad = []
    h, dbc = _copy("unedited", [], [])
    probs = BC.check_schema_version(h, dbc)
    if probs:
        bad.append("the unedited pair fails: %s" % probs)
    for name, he, de, want in CASES:
        h, dbc = _copy(name, he, de)
        probs = BC.check_schema_version(h, dbc)
        if not any(want in p for p in probs):
            bad.append("%s: expected a problem holding %r, got %r"
                       % (name, want, probs))
    return bad


def _first_match(text):
    """Takes the first strict definition and ignores any others; its
    'none found' message matches the real one, so only the duplicate
    cases can catch it."""
    m = BC.SCHEMA_RE.search(text)
    return (int(m.group(1)), []) if m else (None, ["found 0 in that form"])


MUTANTS = [
    ("the firmware side always 4", "firmware_schema", lambda t: (4, [])),
    ("the DBC side always 4", "dbc_schema", lambda p: (4, [])),
    ("the first definition taken", "firmware_schema", _first_match),
]


def main(argv):
    if "--can-fail" not in argv:
        bad = run()
        for b in bad:
            print("FAIL  " + b)
        print("\n%s -- %d case(s) plus the unedited pair"
              % ("FAIL" if bad else "OK", len(CASES)))
        return 1 if bad else 0
    missed = 0
    for label, attr, fake in MUTANTS:
        saved = getattr(BC, attr)
        setattr(BC, attr, fake)
        try:
            bad = run()
        finally:
            setattr(BC, attr, saved)
        print("%s  %-30s %s" % ("ok  " if bad else "FAIL", label,
                                ("fails (" + bad[0][:80] + ")") if bad else
                                "STILL PASSES -- the test checks nothing"))
        missed += not bad
    print("\n%d of %d mutants caught" % (len(MUTANTS) - missed, len(MUTANTS)))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
