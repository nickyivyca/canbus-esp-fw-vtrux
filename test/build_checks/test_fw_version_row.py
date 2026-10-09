"""Test of check_fw_version() in build_output.py: DIAG_FW_VERSION against the doc.

Spec 10 and the schema doc's rule: diag_fw_ver is hand-bumped, and every value
has a row in the doc's rev table. The row compares main/gen_inhibit.c's
DIAG_FW_VERSION with the highest rev in that table. Each case writes a source
file and a doc, runs the row, and classifies it the way build_output.main()
does. The FAIL cases are the point: a bump the doc does not record, a doc row
the source never reached.

Run:  python test/build_checks/test_fw_version_row.py   (exit 0 = all pass)
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(HERE, "_test_fw_version_tmp")

SRC = """#include "x.h"
#ifndef DIAG_FW_VERSION
#define DIAG_FW_VERSION              %s
#endif
"""

DOC = """## Versions

text

| diag_fw_ver | git rev | notes |
|---|---|---|
%s

After the table.
| 99 | not part of the rev table | it comes after a non-table line |
"""


def rows_for(n):
    return "\n".join("| %d | `rev %d` | notes %d |" % (k, k, k) for k in n)


def mark(r):
    _name, ok, _detail, ref_missing = r
    if ok is None or ref_missing:
        return "NO REF"
    return "ok" if ok else "FAIL"


def run_case(src_val, doc_text):
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    src = os.path.join(TMP, "gen_inhibit.c")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(SRC % src_val if src_val is not None else "/* no define */\n")
    doc = os.path.join(TMP, "schema.md")
    with open(doc, "w", encoding="utf-8") as fh:
        fh.write(doc_text)
    del B.rows[:]
    B.check_fw_version(doc, src)
    assert len(B.rows) == 1, B.rows
    return mark(B.rows[0]), B.rows[0][2]


def main():
    cases = [
        ("source 6, table ends at 6 -> ok", "6", DOC % rows_for(range(1, 7)), "ok"),
        ("source bumped to 7, doc still ends at 6 -> FAIL", "7", DOC % rows_for(range(1, 7)), "FAIL"),
        ("doc has a row 7, source still 6 -> FAIL", "6", DOC % rows_for(range(1, 8)), "FAIL"),
        ("rows out of order: the highest counts -> ok", "6",
         DOC % rows_for([1, 2, 6, 3, 4, 5]), "ok"),
        ("a number after the table is not a rev -> ok", "6", DOC % rows_for(range(1, 7)), "ok"),
        ("no rev table -> NO REF", "6", "## Versions\n\nnothing\n", "NO REF"),
        ("no DIAG_FW_VERSION define -> NO REF", None, DOC % rows_for(range(1, 7)), "NO REF"),
    ]
    failures = []
    try:
        for label, src, doc, want in cases:
            got, detail = run_case(src, doc)
            ok = got == want
            print("  %-4s %-52s got %-6s %s" % ("ok" if ok else "FAIL", label, got, detail))
            if not ok:
                failures.append(label)
        del B.rows[:]
        B.check_fw_version(os.path.join(TMP, "missing.md"))
        if mark(B.rows[0]) != "NO REF":
            failures.append("missing doc is not NO REF")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n=== %s ===" % ("PASS" if not failures else "FAIL: %s" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
