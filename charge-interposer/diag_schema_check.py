#!/usr/bin/env python3
"""The diag DBC against spec 8.2 at schema 5, and the generated tables
against the DBC (charge-interposer tester, 2026-11-24; diag schema 5, user
2026-11-23, spec 8.2, 8.3 and 9; tracker J).

    python3.13 -B charge-interposer/diag_schema_check.py
    python3.13 -B charge-interposer/diag_schema_check.py --can-fail

THE CHECKS, on the diag DBC (paths.diag_dbc()):
  1. its DiagSchemaVersion is run_scenario.SPEC_SCHEMA_VER (5), the schema
     every scenario's frames are judged against (spec 9);
  2. its DiagSchemaHash is the layout_hash of its own layout and names,
     from the pinned shared/bench's vtrux_bench.decode (spec 8.2: "a hash
     of its layout and names, so a layout or rename without a bump is
     caught"; shared/diag SPEC: DiagSchemaHash is always layout_hash);
  3. the schema byte is at 0x7F4 B0 and 0x7F7 B6, intp_schema_ver and
     intp_schema_ver_build, unsigned, 8 bits (spec 8.2: every layout keeps
     it there);
  4. the firmware version is retired (spec 8.2): 0x7F4 B1 is
     intp_reserved_b1 (8 bits at bit 8) and 0x7F7 B4-B5 intp_reserved16
     (16 bits at bit 32), each unsigned, little-endian, with RenamedFrom
     "intp_fw_ver" and "intp_fw_ver16"; no signal named intp_fw_ver or
     intp_fw_ver16 is left. Not the DBC's range [0|0], which cantools
     reads as no range: that the bytes are 0 is judged on the wire, in
     every scenario (run_scenario's SPEC_RESERVED) and in L2;
  5. the generated tables machine.cpp and machine.py pack from
     (firmware/main/intp_diag_table.h, .c, intp_diag_table.py) are what
     the pinned shared/diag's tools/diag_table_gen.py makes from the DBC
     now, byte for byte, with the prefix intp_dbc (spec 8.2: the frame
     table is generated from the DBC, so no layout is kept by hand).
     The shared code comes from the pinned checkout after shared_pin's
     R23 checks, loaded by file. The implementor's diag_table.py check is
     its own check of its files; this is the tester's.

Exit 0 every check passes, 1 one fails (each named), 2 cannot run (no
cantools, no pinned checkout).

--can-fail runs the checks on copies, in scratch/diag-schema-check/<pid>/
(removed at the end), and passes only if each copy fails the check it
names and the unchanged copy passes them all:
  - the DBC with DiagSchemaVersion 4 (1);
  - the DBC with a signal renamed and the hash kept (2);
  - the DBC with intp_t_in_state_s one bit shorter and the hash kept (2);
  - the DBC with intp_schema_ver_build moved to B7 and the hash re-made
    (3, and 5 for the tables made from the old DBC);
  - the DBC with intp_reserved_b1 back to intp_fw_ver [0|255], the hash
    re-made (4);
  - the DBC with intp_reserved16's RenamedFrom removed, the hash
    re-made (4);
  - each generated table with one byte changed (5).
"""

import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths            # noqa: E402
import scratch_dir      # noqa: E402

PREFIX = "intp_dbc"
HEADER = "intp_diag_table.h"
TABLES = (("firmware/main/intp_diag_table.h", 0),
          ("firmware/main/intp_diag_table.c", 1),
          ("intp_diag_table.py", 2))
# (frame id, signal, start bit, length, its name through schema 4)
RESERVED = ((0x7F4, "intp_reserved_b1", 8, 8, "intp_fw_ver"),
            (0x7F7, "intp_reserved16", 32, 16, "intp_fw_ver16"))
SCHEMA_BYTES = ((0x7F4, 0, "intp_schema_ver"),
                (0x7F7, 48, "intp_schema_ver_build"))


class CannotRun(Exception):
    pass


def _shared(name):
    import shared_pin
    try:
        return shared_pin.folder(name)
    except shared_pin.SharedPinError as e:
        raise CannotRun("the pinned %s: %s" % (name, e))


def _load_file(key, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise CannotRun("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mods = {}


def decode_mod():
    """The pinned shared/bench's vtrux_bench/decode.py (layout_hash)."""
    if "decode" not in _mods:
        d = _shared("shared/bench")
        _mods["decode"] = _load_file("intp_check_vtrux_decode", os.path.join(
            d, "vtrux_bench", "decode.py"))
    return _mods["decode"]


def generator():
    """The pinned shared/diag's tools/diag_table_gen.py."""
    if "gen" not in _mods:
        d = _shared("shared/diag")
        _mods["gen"] = _load_file("intp_check_diag_table_gen", os.path.join(
            d, "tools", "diag_table_gen.py"))
    return _mods["gen"]


def spec_schema():
    import run_scenario
    return run_scenario.SPEC_SCHEMA_VER


def _sig(db, fid, name):
    m = db.get_message_by_frame_id(fid)
    for s in m.signals:
        if s.name == name:
            return s
    return None


def _renamed_from(s):
    a = (s.dbc.attributes if s.dbc else {}).get("RenamedFrom")
    return a.value if a is not None else None


def checks(dbc, root, schema):
    """-> [(ok, name, detail)] for checks 1-5 on dbc, and the tables under
    root (charge-interposer/, or a copy laid out the same)."""
    import cantools
    dec = decode_mod()
    out = []
    db = cantools.database.load_file(dbc)
    v, h = dec.schema_identity(db)
    out.append((v == schema, "1 DiagSchemaVersion is the scenarios' schema",
                "DBC %r, run_scenario.SPEC_SCHEMA_VER %r" % (v, schema)))
    actual = dec.layout_hash(db)
    out.append((h == actual, "2 DiagSchemaHash is the DBC's own layout_hash",
                "stated %r, layout_hash %r" % (h, actual)))
    bad = []
    for fid, start, name in SCHEMA_BYTES:
        found = [s for s in db.get_message_by_frame_id(fid).signals
                 if s.start == start]
        s = found[0] if len(found) == 1 else None
        if (s is None or s.name != name or s.length != 8 or s.is_signed
                or s.byte_order != "little_endian"):
            bad.append("0x%03X bit %d, want %s: %s" % (fid, start, name, [
                (x.name, x.length) for x in found] or "no signal"))
    out.append((not bad, "3 the schema byte is at 0x7F4 B0 and 0x7F7 B6",
                "; ".join(bad) or "both 8-bit unsigned"))
    bad = []
    for fid, name, start, length, old in RESERVED:
        s = _sig(db, fid, name)
        if s is None:
            bad.append("0x%03X has no %s" % (fid, name))
            continue
        # not the range: cantools reads [0|0] as no range at all; that the
        # bytes are 0 is judged on the wire (run_scenario SPEC_RESERVED)
        got = (s.start, s.length, s.is_signed, s.byte_order, _renamed_from(s))
        want = (start, length, False, "little_endian", old)
        if got != want:
            bad.append("0x%03X %s is %r, spec %r" % (fid, name, got, want))
        if _sig(db, fid, old) is not None:
            bad.append("0x%03X still has %s" % (fid, old))
    out.append((not bad, "4 the firmware version is retired: 0x7F4 B1 and "
                "0x7F7 B4-B5 reserved, [0|0], with RenamedFrom",
                "; ".join(bad) or "intp_reserved_b1, intp_reserved16"))
    gen = generator()
    try:
        texts = gen.generate(dbc, PREFIX, HEADER)
    except gen.Refused as e:
        out.append((False, "5 the generated tables are the DBC's",
                    "the generator refused the DBC: %s" % e))
        return out
    bad = []
    for rel, i in TABLES:
        p = os.path.join(root, *rel.split("/"))
        try:
            with open(p, "rb") as f:
                have = f.read()
        except OSError as e:
            bad.append("%s: %s" % (rel, e))
            continue
        if have != texts[i].encode("ascii"):
            bad.append("%s differs" % rel)
    out.append((not bad, "5 the generated tables are the DBC's, from the "
                "pinned shared/diag generator",
                "; ".join(bad) or "3 files identical"))
    return out


def report(results, label=""):
    fails = 0
    for ok, name, detail in results:
        print("%s %s%s -- %s" % ("ok  " if ok else "FAIL", label, name, detail))
        fails += 0 if ok else 1
    return fails


# --- can-fail ---------------------------------------------------------------

def _edit(text, a, b, count=1):
    if text.count(a) != count:
        raise CannotRun("a can-fail edit no longer applies (%r found %d "
                        "times)" % (a[:60], text.count(a)))
    return text.replace(a, b)


def _rehash(text):
    """The DBC text with its DiagSchemaHash re-made for its own layout."""
    import cantools
    db = cantools.database.load_string(text, "dbc")
    h = decode_mod().layout_hash(db)
    return re.sub(r'BA_ "DiagSchemaHash" "[0-9a-f]*";',
                  'BA_ "DiagSchemaHash" "%s";' % h, text)


def can_fail(schema):
    with open(paths.diag_dbc(), encoding="ascii", newline="") as f:
        dbc = f.read()
    sig_b6 = " SG_ intp_schema_ver_build : 48|8@1+"
    cases = [
        ("1", "DiagSchemaVersion 4",
         lambda t: _edit(t, 'BA_ "DiagSchemaVersion" 5;',
                         'BA_ "DiagSchemaVersion" 4;'), None),
        ("2", "a signal renamed, the hash kept",
         lambda t: _edit(t, " SG_ intp_uptime_min :",
                         " SG_ intp_uptime_mins :"), None),
        ("2", "intp_t_in_state_s one bit shorter, the hash kept",
         lambda t: _edit(t, "intp_t_in_state_s : 48|16@1+",
                         "intp_t_in_state_s : 48|15@1+"), None),
        ("3", "intp_schema_ver_build moved to B7, the hash re-made",
         lambda t: _rehash(_edit(_edit(t, sig_b6,
                                       " SG_ intp_schema_ver_build : 56|8@1+"),
                                 " SG_ intp_uptime_min : 56|8@1+",
                                 " SG_ intp_uptime_min : 48|8@1+")), None),
        ("4", "intp_reserved_b1 back to intp_fw_ver [0|255], the hash re-made",
         lambda t: _rehash(_edit(_edit(
             t, " SG_ intp_reserved_b1 : 8|8@1+ (1,0) [0|0]",
             " SG_ intp_fw_ver : 8|8@1+ (1,0) [0|255]"),
             'BA_ "RenamedFrom" SG_ 2036 intp_reserved_b1 "intp_fw_ver";\n',
             "").replace("CM_ SG_ 2036 intp_reserved_b1 ",
                         "CM_ SG_ 2036 intp_fw_ver ")), None),
        ("4", "intp_reserved16's RenamedFrom removed, the hash re-made",
         lambda t: _rehash(_edit(
             t, 'BA_ "RenamedFrom" SG_ 2039 intp_reserved16 "intp_fw_ver16";\n',
             "")), None),
    ]
    for rel, i in TABLES:
        cases.append(("5", "%s with one byte changed" % rel, None, rel))
    base = scratch_dir.make("diag-schema-check")
    fails = 0
    try:
        def copy_tree(where, dbc_text):
            os.makedirs(os.path.join(where, "firmware", "main"))
            for rel, _i in TABLES:
                shutil.copyfile(os.path.join(HERE, *rel.split("/")),
                                os.path.join(where, *rel.split("/")))
            p = os.path.join(where, os.path.basename(paths.diag_dbc()))
            with open(p, "w", encoding="ascii", newline="") as f:
                f.write(dbc_text)
            return p
        ctl = os.path.join(base, "control")
        res = checks(copy_tree(ctl, dbc), ctl, schema)
        ok = all(r[0] for r in res)
        print("%s control: the unchanged copy passes every check"
              % ("ok  " if ok else "FAIL"))
        fails += 0 if ok else 1
        if not ok:
            report(res, "  control ")
        for n, (want, label, edit, table) in enumerate(cases):
            where = os.path.join(base, "m%d" % n)
            p = copy_tree(where, edit(dbc) if edit else dbc)
            if table:
                tp = os.path.join(where, *table.split("/"))
                with open(tp, "rb") as f:
                    b = bytearray(f.read())
                k = len(b) // 2
                b[k] = ord("#") if b[k] != ord("#") else ord("$")
                with open(tp, "wb") as f:
                    f.write(bytes(b))
            res = checks(p, where, schema)
            failed = [name.split(" ", 1)[0] for ok, name, _d in res if not ok]
            caught = want in failed
            print("%s %s: fails check %s -> %s" % (
                "ok  " if caught else "FAIL", label, want,
                "; ".join("%s %s" % (name.split(" ", 1)[0], d)
                          for ok, name, d in res if not ok) or "passes all"))
            fails += 0 if caught else 1
    finally:
        scratch_dir.remove(base)
    print("\ndiag_schema_check --can-fail: %d case(s) and the control, %s"
          % (len(cases), "all as expected" if not fails
             else "%d not as expected" % fails))
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--can-fail", action="store_true")
    args = ap.parse_args(argv)
    try:
        import cantools  # noqa: F401
    except ImportError:
        print("cannot run: cantools is not installed for %s" % sys.executable)
        return 2
    try:
        schema = spec_schema()
        if args.can_fail:
            return 1 if can_fail(schema) else 0
        fails = report(checks(paths.diag_dbc(), HERE, schema))
    except CannotRun as e:
        print("cannot run: %s" % e)
        return 2
    print("\ndiag_schema_check: %s" % ("all checks OK" if not fails
                                       else "%d check(s) FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
