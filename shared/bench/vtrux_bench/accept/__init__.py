"""The acceptance runner (shared/bench/ACCEPT-SPEC.md, A1-A16): one step's
acceptance, from the reviewer's committed list, as a layer on the chain
runner (vtrux_bench.run).

    listfile.py   the list: read from git, checked, expanded (A2, A3, A8)
    record.py     the record folder, made first (A13); the Python guard

It needs Python >= 3.11 and refuses to import on an older one: it runs on
NICKY-XPS or madhouse, and WSL is a check host only (reviewer, 2026-11-01).
"""

import sys

from .record import python_refusal

_why = python_refusal(sys.version_info)
if _why:
    raise ImportError(_why)
