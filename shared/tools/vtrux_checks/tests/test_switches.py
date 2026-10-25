"""R20 build-switch check: each rule shown failing on a planted input and passing on
a faithful one. Runs alone: python -B shared/tools/vtrux_checks/tests/test_switches.py

    VTRUX_TEST_ELF=<an .elf> VTRUX_TEST_NM=<its nm>   also compares the ELF reader with nm

The scan cases are the ways a switch could hide from a textual scan (carried over from
gen-inhibit's test_build_switches.py with the readings, user 2026-10-24): a default value
shaped like a header guard, a guard that is not the file's first directive, defined() inside
an expression, #elif, a continuation line, a commented-out #if, CMake conditions with
keywords, variables and comments, and the narrow reading of "set by the file itself".
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "verification"))
from vtrux_checks import switches as W  # noqa: E402
from vtrux_checks.tests.test_scope import scratch  # noqa: E402
import independent as I  # noqa: E402

# ---------- the scan ----------

C = {
    "main/guard.h": "/* a header */\n#ifndef GUARD_H\n#define GUARD_H\n#ifdef INSIDE_GUARD\n#endif\n#endif\n",
    "main/guard2.h": "#ifndef G2_H\n#define G2_H\n#endif\n#ifdef G2_H\n#endif\n",
    "main/default.h": "#ifndef DEFAULT_LIKE_GUARD\n#define DEFAULT_LIKE_GUARD 60000\n#endif\n",
    "main/late.c": "#include <x.h>\nint a;\n#ifndef LATE_GUARD\n#define LATE_GUARD\n#endif\n",
    "main/expr.c": ("#if defined(EXPR_A) && EXPR_B > 2\n#elif !defined(EXPR_C) || EXPR_D == EXPR_E\n"
                    "#endif\n#if CONFIG_SOMETHING\n#endif\n#  if   SPACED\n#endif\n"
                    "#elifdef ELIFDEF_X\n#elifndef ELIFNDEF_Y\n"),
    "main/cont.c": "#if CONT_A && \\\n    CONT_B\n#endif\n",
    "main/comments.c": ("/*\n#if IN_BLOCK_COMMENT\n#endif\n*/\n// #ifdef IN_LINE_COMMENT\n"
                        "#if REAL_ONE /* #if NOT_THIS */ // NOR_THIS\n#endif\n"
                        "#if STR_TEST == 1 // \"#if NOT_IN_STRING\"\n#endif\n"
                        "#if __has_include(\"INSIDE_STRING.h\") && AFTER_STRING\n#endif\n"
                        "#if FN_MACRO(1, 2)\n#endif\n"),
    "components/comp/x.c": "#ifndef COMP_DEFAULT\n#define COMP_DEFAULT 3\n#endif\n#if COMP_DEFAULT\n#endif\n",
}
CMAKE = {
    "main/CMakeLists.txt": ("# if(CM_COMMENTED)\nif(CM_A)\nendif()\nif(CM_B AND NOT CM_C)\nendif()\n"
                            "elseif (${CM_D} EQUAL 2)\nif(DEFINED CM_E AND \"literal\" STREQUAL CM_F)\n"
                            "endif()\nif(CONFIG_CM_SKIPPED)\nendif()\nIF(CM_UPPER_IF)\nendif()\n"
                            "set(NOT_TESTED_HERE 1)\n"),
    "CMakeLists.txt": "project(wican)\nif(TOP_LEVEL_ONLY)\nendif()\ninclude(cmake/extra.cmake)\n",
    "cmake/extra.cmake": "\n".join([
        "set(OWN_A 1)",                                    # 1  set first: not an option
        "if(OWN_A)",                                       # 2
        "endif()",                                         # 3
        "if(LATE_SET)",                                    # 4  tested before it is set
        "endif()",                                         # 5
        "set(LATE_SET 1)",                                 # 6
        "if(SOMETHING_ELSE)",                              # 7
        "  set(COND_SET 1)",                               # 8  set only under a condition
        "endif()",                                         # 9
        "if(COND_SET)",                                    # 10
        "endif()",                                         # 11
        "set(CACHED 1 CACHE BOOL \"doc\")",                # 12 a cache default, -D wins
        "if(CACHED)",                                      # 13
        "endif()",                                         # 14
        "set(PS 1 PARENT_SCOPE)",                          # 15
        "if(PS)",                                          # 16
        "endif()",                                         # 17
        "set(EMPTYSET)",                                   # 18 no value: unsets
        "if(EMPTYSET)",                                    # 19
        "endif()",                                         # 20
        "set(OWN_B 1)",                                    # 21
        "unset(OWN_B)",                                    # 22
        "if(OWN_B)",                                       # 23
        "endif()",                                         # 24
        "set(TAINT ${EXT_SRC})",                           # 25 an outside value through a set
        "if(TAINT)",                                       # 26
        "endif()",                                         # 27
        "set(CLEAN_REF ${CMAKE_CURRENT_LIST_DIR}/x)",      # 28 a built-in's value
        "if(CLEAN_REF)",                                   # 29
        "endif()",                                         # 30
        "set(CHAIN \"${OWN_A}-x\")",                       # 31 the file's own value
        "if(CHAIN)",                                       # 32
        "endif()",                                         # 33
        "set(ENVSET $ENV{SOME_ENV})",                      # 34
        "if(ENVSET)",                                      # 35
        "endif()",                                         # 36
        "set(ENV_OPT2 1)",                                 # 37 a CMake variable, not the env one
        "if($ENV{ENV_OPT} OR $ENV{ENV_OPT2})",             # 38
        "endif()",                                         # 39
        "if(\"${QUOTED_REF}\" STREQUAL \"on\")",           # 40
        "endif()",                                         # 41
        "if(CMAKE_BUILD_TYPE OR WIN32 OR CMAKE_C_FLAGS)",  # 42 built-ins
        "endif()",                                         # 43
        "if(wican_SOURCE_DIR OR other_SOURCE_DIR)",        # 44 <PROJECT-NAME>: only the declared one
        "endif()",                                         # 45
        "if(FOO_ROOT)",                                    # 46 <PackageName>_ROOT: not used
        "endif()",                                         # 47
        "function(f)",                                     # 48
        "  set(FN_SET 1)",                                 # 49 inside a function
        "endfunction()",                                   # 50
        "if(FN_SET)",                                      # 51
        "endif()",                                         # 52
        "string(LENGTH \"abc\" STRLEN_OUT)",               # 53 not set(): stays an option
        "if(STRLEN_OUT)",                                  # 54
        "endif()",                                         # 55
        "set(HASH_STR \"a#b)c\")",                         # 56 '#' and ')' inside quotes
        "if(HASH_STR AND AFTER_QUOTE)",                    # 57
        "endif()",                                         # 58
        "#[[ a bracket comment",                           # 59
        "if(IN_BRACKET_COMMENT) ]]",                       # 60 its second line
        "notanif(NOT_A_CONDITION)",                        # 61
        "while(WHILE_OPT)",                                # 62
        "endwhile()",                                      # 63
        "set(LATE_SET 2)",                                 # 64
        "if(LATE_SET)",                                    # 65 set by now: not counted here
        "endif()",                                         # 66
        "set(PAREN_STR \"x(\")",                           # 67 '(' inside quotes
        "if(AFTER_PAREN)",                                 # 68
        "endif()",                                         # 69
    ]) + "\n",
}
EXPECT = {
    "INSIDE_GUARD", "DEFAULT_LIKE_GUARD", "LATE_GUARD", "G2_H",
    "EXPR_A", "EXPR_B", "EXPR_C", "EXPR_D", "EXPR_E", "SPACED", "ELIFDEF_X", "ELIFNDEF_Y",
    "CONT_A", "CONT_B", "REAL_ONE", "STR_TEST", "AFTER_STRING", "FN_MACRO", "COMP_DEFAULT",
    "CM_A", "CM_B", "CM_C", "CM_D", "CM_E", "CM_F", "CM_UPPER_IF", "TOP_LEVEL_ONLY",
    "LATE_SET", "SOMETHING_ELSE", "COND_SET", "CACHED", "PS", "EMPTYSET", "OWN_B", "TAINT",
    "ENVSET", "ENV_OPT", "ENV_OPT2", "QUOTED_REF", "other_SOURCE_DIR", "FOO_ROOT", "FN_SET",
    "STRLEN_OUT", "AFTER_QUOTE", "WHILE_OPT", "AFTER_PAREN",
}
# A slice of `cmake --help-variable-list` (3.30), placeholders included.
CMAKE_VARS = ["CMAKE_BUILD_TYPE", "CMAKE_CURRENT_LIST_DIR", "CMAKE_<LANG>_FLAGS", "<PROJECT-NAME>_SOURCE_DIR",
              "<PackageName>_ROOT", "WIN32"]


def matcher(cmake=CMAKE):
    return W.builtin_matcher(CMAKE_VARS, W.project_names(cmake.values()))


class Scan(unittest.TestCase):
    def test_found(self):
        found = W.found_symbols(C, CMAKE, matcher())
        self.assertEqual(set(found) - EXPECT, set(), "found but not expected")
        self.assertEqual(EXPECT - set(found), set(), "expected but not found")

    def test_where(self):
        found = W.found_symbols(C, CMAKE, matcher())
        self.assertEqual(found["LATE_SET"], ["cmake/extra.cmake:4"])
        self.assertEqual(found["CONT_B"], ["main/cont.c:1"])
        self.assertEqual(found["INSIDE_GUARD"], ["main/guard.h:4"])
        self.assertEqual(found["G2_H"], ["main/guard2.h:4"])

    def test_project_names(self):
        self.assertEqual(W.project_names(CMAKE.values()), ["wican"])

    def test_no_builtins_lists_them(self):
        found = W.found_symbols({}, CMAKE, W.builtin_matcher([], []))
        for s in ("CMAKE_BUILD_TYPE", "WIN32", "CMAKE_C_FLAGS", "wican_SOURCE_DIR"):
            self.assertIn(s, found)

    def test_extra_scanner_hook(self):
        old = list(W.EXTRA_SCANNERS)
        try:
            W.EXTRA_SCANNERS.append(lambda c, cm: [("FROM_HOOK", "hook")])
            self.assertEqual(W.found_symbols({}, {}, matcher())["FROM_HOOK"], ["hook"])
        finally:
            W.EXTRA_SCANNERS[:] = old
        self.assertNotIn("FROM_HOOK", W.found_symbols({}, {}, matcher()))


# ---------- the ELF ----------

def elf(symbols, cls=1, enc=1):
    """A minimal ELF: section 0 null, 1 .text, 2 .strtab, 3 .symtab. symbols: (name, info, shndx)."""
    e = "<" if enc == 1 else ">"
    strtab = b"\0"
    offs = []
    for name, _i, _s in symbols:
        offs.append(len(strtab) if name else 0)
        if name:
            strtab += name.encode() + b"\0"
    if cls == 1:
        fmt, ehsize, shentsize, symentsize = e + "IIIBBH", 52, 40, 16
        syms = b"".join(struct.pack(fmt, o, 0, 0, i, 0, s) for o, (_n, i, s) in zip(offs, symbols))
        syms = struct.pack(fmt, 0, 0, 0, 0, 0, 0) + syms
    else:
        fmt, ehsize, shentsize, symentsize = e + "IBBHQQ", 64, 64, 24
        syms = b"".join(struct.pack(fmt, o, i, 0, s, 0, 0) for o, (_n, i, s) in zip(offs, symbols))
        syms = struct.pack(fmt, 0, 0, 0, 0, 0, 0) + syms
    text = b"\0" * 4
    o_str = ehsize + len(text)
    o_sym = o_str + len(strtab)
    shoff = o_sym + len(syms)

    def sh(typ, off, size, link=0, entsize=0):
        if cls == 1:
            return struct.pack(e + "IIIIIIIIII", 0, typ, 0, 0, off, size, link, 0, 1, entsize)
        return struct.pack(e + "IIQQQQIIQQ", 0, typ, 0, 0, off, size, link, 0, 1, entsize)
    shdrs = sh(0, 0, 0) + sh(1, ehsize, len(text)) + sh(3, o_str, len(strtab)) + sh(2, o_sym, len(syms), 2,
                                                                                     symentsize)
    ident = b"\x7fELF" + bytes([cls, enc, 1]) + bytes(9)
    if cls == 1:
        hdr = ident + struct.pack(e + "HHIIIIIHHHHHH", 2, 243, 1, 0, 0, shoff, 0, ehsize, 0, 0, shentsize, 4, 2)
    else:
        hdr = ident + struct.pack(e + "HHIQQQIHHHHHH", 2, 243, 1, 0, 0, shoff, 0, ehsize, 0, 0, shentsize, 4, 2)
    return hdr + text + strtab + syms + shdrs


FUNC_GLOBAL, OBJ_LOCAL, SECTION, FILE = 0x12, 0x01, 0x03, 0x04
SYMS = [("defined_fn", FUNC_GLOBAL, 1), ("local_obj", OBJ_LOCAL, 1), ("undef_fn", FUNC_GLOBAL, 0),
        ("", SECTION, 1), ("main.c", FILE, 0xFFF1), ("$x", 0, 1), ("$xrv32i2p1_m2p0", 0, 1), ("$d", 0, 1),
        ("$x.12", 0, 1), ("$xyz_not_mapping", FUNC_GLOBAL, 1), ("abs_sym", OBJ_LOCAL, 0xFFF1)]
WANT = {"defined_fn", "local_obj", "$xyz_not_mapping", "abs_sym"}


class Elf(unittest.TestCase):
    def test_elf32_le(self):
        self.assertEqual(W.elf_defined_symbols(elf(SYMS)), WANT)

    def test_elf64_be(self):
        self.assertEqual(W.elf_defined_symbols(elf(SYMS, cls=2, enc=2)), WANT)

    def test_not_elf(self):
        with self.assertRaises(W.SwitchesError):
            W.elf_defined_symbols(b"\0" * 64)

    def test_truncated(self):
        with self.assertRaises(W.SwitchesError):
            W.elf_defined_symbols(elf(SYMS)[:60])

    def test_stripped(self):
        b = bytearray(elf(SYMS))
        shoff = struct.unpack_from("<I", b, 0x20)[0]
        struct.pack_into("<I", b, shoff + 3 * 40 + 4, 3)          # .symtab's type made STRTAB
        with self.assertRaises(W.SwitchesError):
            W.elf_defined_symbols(bytes(b))

    @unittest.skipUnless(os.environ.get("VTRUX_TEST_ELF") and os.environ.get("VTRUX_TEST_NM"), "no real ELF and nm")
    def test_as_nm(self):
        p, nm = os.environ["VTRUX_TEST_ELF"], os.environ["VTRUX_TEST_NM"]
        out = subprocess.run([nm, "--defined-only", p], capture_output=True, text=True, check=True).stdout
        theirs = {ln.split()[-1] for ln in out.splitlines() if len(ln.split()) >= 3}
        with open(p, "rb") as f:
            self.assertEqual(W.elf_defined_symbols(f.read()), theirs)


# ---------- tables ----------

GOOD = {"SHIP_A": {"class": W.SHIP, "reason": "r"},
        "TEST_B": {"class": W.TEST, "reason": "r", "refusal": [{"bin_strings_absent": ["/x"]},
                                                               {"elf_symbols_absent": ["x_init"]}]},
        "STOCK_C": {"class": W.STOCK, "reason": "r", "refusal": [{"project_row": "no c"}]},
        "NOT_D": {"class": W.NOT, "kind": "default value", "reason": "r"}}


class Table(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def table(self, t, key=W.KEY, raw=None, required=True):
        with open(os.path.join(self.d, W.DATA_FILE), "w") as f:
            f.write(raw if raw is not None else json.dumps({key: t, "release_profile": {"ignored": 1}}))
        return W.read_table(self.d, "here", required)

    def refused(self, t, msg, **kw):
        with self.assertRaises(W.SwitchesError) as c:
            self.table(t, **kw)
        self.assertIn(msg, str(c.exception))

    def test_good(self):
        self.assertEqual(self.table(GOOD), GOOD)

    def test_bad(self):
        def w(sym, **e):
            t = json.loads(json.dumps(GOOD))
            t[sym] = e
            return t
        for t, msg in (
                (w("X", **{"class": "shipping", "reason": "r"}), "not one of the four"),
                (w("X", reason="r"), "not one of the four"),
                (w("X", **{"class": W.SHIP, "reason": " "}), "no reason"),
                (w("X", **{"class": W.SHIP}), "no reason"),
                (w("X", **{"class": W.NOT, "reason": "r"}), "needs a kind"),
                (w("X", **{"class": W.SHIP, "reason": "r", "kind": "k"}), "not allowed"),
                (w("X", **{"class": W.SHIP, "reason": "r", "refusal": [{"project_row": "x"}]}), "not allowed"),
                (w("X", **{"class": W.TEST, "reason": "r"}), "needs a refusal row"),
                (w("X", **{"class": W.STOCK, "reason": "r", "refusal": []}), "needs a refusal row"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": [{"bin_strings_absent": ["a"],
                                                                        "project_row": "b"}]}), "wants one key"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": [{"flag_off": "X"}]}), "is not one of"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": [{"elf_symbols_absent": []}]}), "non-empty list"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": [{"elf_symbols_absent": [""]}]}), "non-empty"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": [{"project_row": ""}]}), "non-empty string"),
                (w("X", **{"class": W.TEST, "reason": "r", "refusal": ["bin_strings_absent"]}), "wants one key"),
                (dict(GOOD, X="test-only"), "wants an object")):
            with self.subTest(msg=msg):
                self.refused(t, msg)

    def test_no_table(self):
        self.refused(GOOD, "no 'build_switches' object", key="switches")
        self.refused({}, "no 'build_switches' object")
        self.refused(None, "cannot be read", raw="{")

    def test_no_file(self):
        with self.assertRaises(W.SwitchesError):
            W.read_table(self.d, "here")
        self.assertIsNone(W.read_table(self.d, "here", required=False))

    def test_optional_without_key(self):
        self.assertIsNone(self.table(GOOD, key="release_profile_only", required=False))
        self.refused({}, "no 'build_switches' object", required=False)


# ---------- rules and project rows ----------

H = "ab" * 32
OUT = {"image": b"\0abc/gi_quiesce\0ELM327 v1.3a\0", "elf_symbols": {"app_main", "esp_mqtt_client_init", "elm327_init"},
       "project_name": "wican-fw_obd", "image_hash": H}


def rule(r, **kw):
    return W.judge_rule(r, dict(OUT, **kw))[0]


class Rules(unittest.TestCase):
    def test_strings(self):
        self.assertFalse(rule({"bin_strings_absent": ["nope", "/gi_quiesce"]}))
        self.assertTrue(rule({"bin_strings_absent": ["nope", "/gi_quiesceX"]}))

    def test_symbols(self):
        self.assertFalse(rule({"elf_symbols_absent": ["elm327_init"]}))
        self.assertTrue(rule({"elf_symbols_absent": ["elm327"]}))       # whole names, not substrings

    def test_prefixes(self):
        self.assertFalse(rule({"elf_symbol_prefixes_absent": ["mqtt_", "esp_mqtt_client_"]}))
        self.assertTrue(rule({"elf_symbol_prefixes_absent": ["mqtt_"]}))

    def test_token(self):
        for name, ok in (("wican-fw_obd_rt", False), ("wican_rt_obd", False), ("wican-fw_obd_rtx", True),
                         ("wican-fw_obdrt", True)):
            with self.subTest(name=name):
                self.assertEqual(rule({"project_name_token_absent": "_rt"}, project_name=name), ok)


class ProjectRows(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def rows(self, d=None, raw=None):
        if d is not None or raw is not None:
            with open(os.path.join(self.d, W.ROWS_FILE), "w") as f:
                f.write(raw if raw is not None else json.dumps(d))
        try:
            return W.read_rows(self.d)
        except W.SwitchesError as e:
            return e

    def j(self, rows, row="no c"):
        return W.judge_rule({"project_row": row}, dict(OUT, rows=rows))

    def test_pass(self):
        self.assertTrue(self.j(self.rows({"image_hash": H.upper(), "rows": {"no c": "PASS"}}))[0])

    def test_failed_row(self):
        self.assertFalse(self.j(self.rows({"image_hash": H, "rows": {"no c": "FAIL"}}))[0])

    def test_absent_row(self):
        self.assertFalse(self.j(self.rows({"image_hash": H, "rows": {"no d": "PASS"}}))[0])

    def test_other_image(self):
        ok, why = self.j(self.rows({"image_hash": "cd" * 32, "rows": {"no c": "PASS"}}))
        self.assertFalse(ok)
        self.assertIn("judged image cdcd", why)

    def test_missing_file(self):
        r = self.rows()
        self.assertIsInstance(r, W.SwitchesError)
        self.assertFalse(self.j(r)[0])

    def test_malformed(self):
        for raw in ("{", json.dumps({"rows": {}}), json.dumps({"image_hash": H, "rows": {"a": True}}), "[]"):
            with self.subTest(raw=raw):
                r = self.rows(raw=raw)
                self.assertIsInstance(r, W.SwitchesError)
                self.assertFalse(self.j(r)[0])

    def test_nothing_read(self):
        self.assertFalse(self.j(None)[0])


# ---------- the judge ----------

def facts(project_table=GOOD, found=None, comps=None, named=(), **out):
    found = {"SHIP_A": ["a.c:1"], "TEST_B": ["a.c:2"], "STOCK_C": ["CMakeLists.txt:3"], "NOT_D": ["d.h:1"]} \
        if found is None else found
    rows = (H, {"no c": "PASS"})
    scopes = {W.PROJECT: {"table": project_table, "found": found}}
    scopes.update(comps or {})
    return {"scopes": scopes, "named_without_table": list(named), "outputs": dict(OUT, rows=rows, **out)}


def what(bad):
    return sorted((s, sym, w) for s, sym, w, _d in bad)


class Judge(unittest.TestCase):
    def test_faithful_passes(self):
        self.assertEqual(W.judge(facts()), [])

    def test_unlisted(self):
        f = facts()
        f["scopes"][W.PROJECT]["found"]["NEW_ONE"] = ["n.c:9"]
        self.assertEqual(what(W.judge(f)), [(W.PROJECT, "NEW_ONE", "unlisted")])

    def test_stale(self):
        f = facts()
        del f["scopes"][W.PROJECT]["found"]["NOT_D"]
        self.assertEqual(what(W.judge(f)), [(W.PROJECT, "NOT_D", "stale")])

    def test_refusal_fails(self):
        self.assertEqual(what(W.judge(facts(image=b"x/x"))), [(W.PROJECT, "TEST_B", "refusal")])
        self.assertEqual(what(W.judge(facts(elf_symbols={"x_init"}))), [(W.PROJECT, "TEST_B", "refusal")])
        f = facts()
        f["outputs"]["rows"] = (H, {"no c": "FAIL"})
        self.assertEqual(what(W.judge(f)), [(W.PROJECT, "STOCK_C", "refusal")])

    def test_component_judged_on_its_own(self):
        comp = {"ns__c": {"table": {"C_SW": {"class": W.SHIP, "reason": "r"}}, "found": {"C_OTHER": ["m.c:1"]}}}
        self.assertEqual(what(W.judge(facts(comps=comp))), [("ns__c", "C_OTHER", "unlisted"),
                                                          ("ns__c", "C_SW", "stale")])

    def test_component_without_table_skipped(self):
        comp = {"espressif__mdns": {"table": None, "found": {"MDNS_X": ["m.c:1"]}}}
        self.assertEqual(W.judge(facts(comps=comp)), [])

    def test_named_without_table(self):
        self.assertEqual(what(W.judge(facts(named=["vtrux/x"]))), [("vtrux/x", "-", "no table")])


# ---------- reading a build ----------

def deps_log(records):
    """ninja's deps log, version 4: records is [(output, [deps])]."""
    ids, body = {}, b""

    def path(p):
        nonlocal body
        if p not in ids:
            b = p.encode()
            b += b"\0" * (-len(b) % 4)
            body += struct.pack("<I", len(b) + 4) + b + struct.pack("<I", (~len(ids)) & 0xFFFFFFFF)
            ids[p] = len(ids)
        return ids[p]
    for out, deps in records:
        n = [path(out)] + [path(d) for d in deps]
        rec = struct.pack("<iII", n[0], 1, 0) + struct.pack("<%di" % len(n[1:]), *n[1:])
        body += struct.pack("<I", len(rec) | 0x80000000) + rec
    return b"# ninjadeps\n" + struct.pack("<i", 4) + body


class Collect(unittest.TestCase):
    """A small project with a configured build folder, as the release build leaves it."""

    def setUp(self):
        self.d = scratch()
        self.p = os.path.join(self.d, "proj")
        self.b = os.path.join(self.p, "build")
        self._old = W.builtins_of
        W.builtins_of = lambda folder: CMAKE_VARS
        files = {
            "CMakeLists.txt": "project(proj)\nif(TOP_OPT)\nendif()\n",
            "main/CMakeLists.txt": "if(MAIN_OPT)\nendif()\n",
            "main/a.c": '#include "h.h"\n#if A_SWITCH\n#endif\n',
            "main/h.h": "#ifndef H_H\n#define H_H\n#ifdef H_SWITCH\n#endif\n#endif\n",
            "main/not_compiled.c": "#ifdef NOT_COMPILED\n#endif\n",
            "managed_components/ns__comp/CMakeLists.txt": "if(COMP_OPT)\nendif()\n",
            "managed_components/ns__comp/c.c": "#ifdef COMP_SWITCH\n#endif\n",
            "managed_components/other__lib/l.c": "#ifdef LIB_SWITCH\n#endif\n",
            "build/config/sdkconfig.h": "#ifdef IN_BUILD_FOLDER\n#endif\n",
            "vtrux_build.json": json.dumps({"project": "proj", "shared_components": []}),
        }
        for rel, text in files.items():
            self.write(rel, text)
        self.table(W.PROJECT, {s: {"class": W.SHIP, "reason": "r"} for s in
                               ("TOP_OPT", "MAIN_OPT", "A_SWITCH", "H_SWITCH")})
        self.table("ns__comp", {s: {"class": W.SHIP, "reason": "r"} for s in ("COMP_OPT", "COMP_SWITCH")})
        img = I.build()
        with open(os.path.join(self.b, "proj.bin"), "wb") as f:
            f.write(img)
        with open(os.path.join(self.b, "proj.elf"), "wb") as f:
            f.write(elf([("app_main", FUNC_GLOBAL, 1)]))
        self.write("build/project_description.json", json.dumps(
            {"project_path": self.p.replace(os.sep, "/"), "app_bin": "proj.bin", "app_elf": "proj.elf"}))
        self.write("build/CMakeCache.txt", "CMAKE_COMMAND:INTERNAL=cmake\n")
        with open(os.path.join(self.b, ".ninja_deps"), "wb") as f:
            f.write(deps_log([("esp-idf/main/a.c.obj", ["../main/a.c", "../main/h.h", "config/sdkconfig.h"]),
                              ("esp-idf/comp/c.c.obj", ["../managed_components/ns__comp/c.c"]),
                              ("esp-idf/lib/l.c.obj", ["../managed_components/other__lib/l.c"])]))
        self.ninja()

    def tearDown(self):
        W.builtins_of = self._old
        shutil.rmtree(self.d)

    def write(self, rel, text):
        p = os.path.join(self.p, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="\n") as f:
            f.write(text)

    def table(self, scope, t):
        base = "" if scope == W.PROJECT else "managed_components/%s/" % scope
        self.write(base + W.DATA_FILE, json.dumps({W.KEY: t}))

    def ninja(self, cmake_inputs=("../CMakeLists.txt", "../main/CMakeLists.txt",
                                  "../managed_components/ns__comp/CMakeLists.txt", "../sdkconfig")):
        self.write("build/build.ninja", "\n".join([
            "rule CC", "  command = cc $in", "rule RERUN_CMAKE", "  command = cmake",
            "build esp-idf/main/a.c.obj: CC ../main/a.c",
            "build esp-idf/comp/c.c.obj: CC ../managed_components/ns__comp/c.c",
            "build esp-idf/lib/l.c.obj: CC ../managed_components/other__lib/l.c",
            "build build.ninja: RERUN_CMAKE | " + " ".join(cmake_inputs), ""]))

    def run_main(self):
        class A:
            build = self.b
        return W.main(A)

    def test_collect(self):
        f = W.collect(self.b)
        self.assertEqual(sorted(f["scopes"][W.PROJECT]["found"]), ["A_SWITCH", "H_SWITCH", "MAIN_OPT", "TOP_OPT"])
        self.assertEqual(sorted(f["scopes"]["ns__comp"]["found"]), ["COMP_OPT", "COMP_SWITCH"])
        self.assertIsNone(f["scopes"]["other__lib"]["table"])
        self.assertEqual(f["named_without_table"], [])
        self.assertEqual(f["outputs"]["elf_symbols"], {"app_main"})
        self.assertEqual(W.judge(f), [])
        self.assertEqual(self.run_main(), 0)

    def test_unlisted_fails(self):
        self.write("main/a.c", '#include "h.h"\n#if A_SWITCH || A_NEW\n#endif\n')
        os.utime(os.path.join(self.b, "build.ninja"))
        self.assertEqual(what(W.judge(W.collect(self.b))), [(W.PROJECT, "A_NEW", "unlisted")])
        self.assertEqual(self.run_main(), 1)

    def test_shared_component_without_table(self):
        self.write("vtrux_build.json", json.dumps({"project": "proj", "shared_components": ["other/lib", "ns/comp"]}))
        self.assertEqual(W.collect(self.b)["named_without_table"], ["other/lib"])

    def test_component_table_not_read_is_stale(self):
        self.write("managed_components/unread__c/" + W.DATA_FILE, json.dumps(
            {W.KEY: {"U_SW": {"class": W.SHIP, "reason": "r"}}}))
        self.assertEqual(what(W.judge(W.collect(self.b))), [("unread__c", "U_SW", "stale")])

    def test_cmake_newer_than_record(self):
        p = os.path.join(self.p, "main", "CMakeLists.txt")
        t = os.path.getmtime(os.path.join(self.b, "build.ninja"))
        os.utime(p, (t + 10, t + 10))
        with self.assertRaises(W.SwitchesError):
            W.collect(self.b)
        self.assertEqual(self.run_main(), 1)

    def test_record_without_top_cmakelists(self):
        self.ninja(("../main/CMakeLists.txt",))
        with self.assertRaises(W.SwitchesError):
            W.collect(self.b)

    def test_record_without_c_files(self):
        with open(os.path.join(self.b, ".ninja_deps"), "wb") as f:
            f.write(deps_log([]))
        with self.assertRaises(W.SwitchesError):
            W.collect(self.b)

    def test_rows_read_only_when_named(self):
        self.assertNotIn("rows", W.collect(self.b)["outputs"])
        self.table(W.PROJECT, {"TOP_OPT": {"class": W.SHIP, "reason": "r"},
                               "MAIN_OPT": {"class": W.SHIP, "reason": "r"},
                               "A_SWITCH": {"class": W.TEST, "reason": "r", "refusal": [{"project_row": "no a"}]},
                               "H_SWITCH": {"class": W.SHIP, "reason": "r"}})
        f = W.collect(self.b)
        self.assertIsInstance(f["outputs"]["rows"], W.SwitchesError)
        self.assertEqual(what(W.judge(f)), [(W.PROJECT, "A_SWITCH", "refusal")])
        self.write("build/" + W.ROWS_FILE, json.dumps({"image_hash": f["outputs"]["image_hash"],
                                                       "rows": {"no a": "PASS"}}))
        self.assertEqual(W.judge(W.collect(self.b)), [])


if __name__ == "__main__":
    unittest.main()
