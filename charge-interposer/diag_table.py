#!/usr/bin/env python3
"""The status frames' table, generated from the published DBC by shared/diag
(spec 8.2, schema 5, user 2026-11-23; shared/diag D3, D4).

    python3.13 -B charge-interposer/diag_table.py write
    python3.13 -B charge-interposer/diag_table.py check [--can-fail]

`write` runs the pinned shared/diag's tools/diag_table_gen.py on
diag/vtrux-interposer-diag.dbc with the prefix intp_dbc, into

    firmware/main/intp_diag_table.h, firmware/main/intp_diag_table.c
                                      (machine.cpp packs from these)
    intp_diag_table.py                (machine.py packs from this)

`check` generates the same three texts in memory and compares them with the
committed files, byte for byte: exit 0 the same, 1 a difference (each file
named), 2 cannot run. `--can-fail` then shows the comparison fails: each
text with one byte changed, and a copy of the DBC with intp_t_in_state_s
one bit shorter, written to scratch/charge-interposer-diag-table/<pid>/ and removed.
The implementor's check of its own generated files; not evidence (the D3
check that verifies them is the tester's).

THE SHARED CODE COMES FROM THE PINNED CHECKOUT. shared_pin.folder()
("shared/diag", the pin in firmware/shared_pins.json) gives the folder after
its R23 checks, and the generator and the Python packer are loaded from it
by file, never through sys.path. machine.py loads the packer through
`packer()` here, so there is one way in.

Needs cantools for write and check (the generator's parser); machine.py's
packing needs only the stdlib.
"""

import argparse
import importlib.util
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DBC = os.path.join(HERE, "diag", "vtrux-interposer-diag.dbc")
PREFIX = "intp_dbc"
C_HEADER = os.path.join(HERE, "firmware", "main", "intp_diag_table.h")
C_SOURCE = os.path.join(HERE, "firmware", "main", "intp_diag_table.c")
PY_TABLE = os.path.join(HERE, "intp_diag_table.py")
OUTPUTS = (C_HEADER, C_SOURCE, PY_TABLE)
COMPONENT = "shared/diag"

_loaded = {}


class TableError(RuntimeError):
    """The shared code cannot be loaded or the table cannot be made."""


def shared_folder():
    """The checked shared/diag folder of the pinned checkout."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import shared_pin
    try:
        return shared_pin.folder(COMPONENT)
    except shared_pin.SharedPinError as e:
        raise TableError("the pinned %s: %s" % (COMPONENT, e))


def _load(key, rel):
    if key not in _loaded:
        path = os.path.join(shared_folder(), *rel.split("/"))
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise TableError("cannot load %s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _loaded[key] = mod
    return _loaded[key]


def packer():
    """shared/diag's python/diag_pack.py, the Python twin (D4)."""
    return _load("intp_shared_diag_pack", "python/diag_pack.py")


def generator():
    """shared/diag's tools/diag_table_gen.py (D3)."""
    return _load("intp_shared_diag_table_gen", "tools/diag_table_gen.py")


def generate(dbc=DBC):
    """-> {output path: text} for the three outputs."""
    gen = generator()
    try:
        h, c, p = gen.generate(dbc, PREFIX, os.path.basename(C_HEADER))
    except gen.Refused as e:
        raise TableError("the generator refused %s: %s" % (dbc, e))
    except Exception as e:   # cantools' own parse errors
        raise TableError("the DBC %s did not load: %s" % (dbc, e))
    return {C_HEADER: h, C_SOURCE: c, PY_TABLE: p}


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def differences(texts, files=None):
    """[path] whose committed bytes differ from texts (files: path -> bytes,
    read from disk if not given)."""
    out = []
    for path in OUTPUTS:
        have = files[path] if files is not None else _read(path)
        if have != texts[path].encode("ascii"):
            out.append(path)
    return out


def write():
    texts = generate()
    for path, text in texts.items():
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        print("wrote %s" % os.path.relpath(path, HERE))
    return 0


def _rm_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def can_fail(texts):
    """True when every mutant is caught. Prints each."""
    files = {p: texts[p].encode("ascii") for p in OUTPUTS}
    caught = 0
    total = 0
    if differences(texts, files):
        print("  control: the generated texts differ from themselves")
        return False
    print("  control: identical texts pass")
    for path in OUTPUTS:
        b = bytearray(files[path])
        i = len(b) // 2
        b[i] = ord("x") if b[i] != ord("x") else ord("y")
        mut = dict(files, **{path: bytes(b)})
        total += 1
        hit = differences(texts, mut) == [path]
        caught += hit
        print("  %s: one byte changed in %s" % ("caught" if hit else "MISSED",
                                                 os.path.relpath(path, HERE)))
    root = os.path.abspath(os.path.join(HERE, os.pardir))
    work = os.path.join(root, "scratch", "charge-interposer-diag-table", str(os.getpid()))
    os.makedirs(work)
    try:
        with open(DBC, encoding="ascii") as f:
            dbc = f.read()
        old = " SG_ intp_t_in_state_s : 48|16@1+"
        if old not in dbc:
            print("  MISSED: the DBC mutant's anchor %r is not in the DBC" % old)
            return False
        mdbc = os.path.join(work, os.path.basename(DBC))
        with open(mdbc, "w", encoding="ascii", newline="\n") as f:
            f.write(dbc.replace(old, " SG_ intp_t_in_state_s : 48|15@1+", 1))
        total += 1
        try:
            mtexts = generate(mdbc)
            hit = bool(differences(mtexts, files))
        except TableError as e:
            hit = False
            print("  (the mutant DBC was refused: %s)" % e)
        caught += hit
        print("  %s: intp_t_in_state_s one bit shorter in a copy of the DBC"
              % ("caught" if hit else "MISSED"))
    finally:
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(work, onexc=_rm_readonly)
            else:
                shutil.rmtree(work, onerror=_rm_readonly)
        except OSError as e:
            print("  could not remove %s: %s" % (work, e))
    return caught == total


def check(with_can_fail):
    texts = generate()
    diff = differences(texts)
    for path in diff:
        print("DIFFERS: %s is not what the generator makes from %s"
              % (os.path.relpath(path, HERE), os.path.relpath(DBC, HERE)))
    if not diff:
        print("OK: the three generated files match %s" % os.path.relpath(DBC, HERE))
    rc = 1 if diff else 0
    if with_can_fail:
        print("can-fail:")
        ok = can_fail(texts)
        print("can-fail %s" % ("PASS" if ok else "FAIL"))
        if not ok:
            rc = 1
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("write", "check"))
    ap.add_argument("--can-fail", action="store_true",
                    help="with check: show the comparison fails on changed files")
    a = ap.parse_args(argv)
    try:
        if a.action == "write":
            return write()
        return check(a.can_fail)
    except TableError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
