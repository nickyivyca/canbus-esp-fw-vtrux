"""The acceptance runner (shared/bench/ACCEPT-SPEC.md, A1-A16): one step's
acceptance, from the reviewer's committed list, as a layer on the chain
runner (vtrux_bench.run).

    listfile.py   the list: read from git, checked, expanded (A2, A3, A8)
    record.py     the record folder, made first (A13); the Python guard
    trees.py      the trees, from git objects at exact commits (A4)
    builds.py     the builds, each in a tree of its own, as build work (A5)
    settings.py   the run's settings, from the machine's and the builds (A6)
    compile.py    the checks as one chain suite, and its run (A7)
    nested.py     a suite run as a nested record, and its re-run (A7)
    judge.py      comparisons, premises and the verdict (A10-A12)
    canfail.py    can-fails: mutants in trees of their own (A10)
    close.py      the cleanup, then the record's close, once (A13, A14)

It needs Python >= 3.11 and refuses to import on an older one: it runs on
NICKY-XPS or madhouse, and WSL is a check host only (reviewer, 2026-11-01).
"""

import sys

from .record import python_refusal

_why = python_refusal(sys.version_info)
if _why:
    raise ImportError(_why)
