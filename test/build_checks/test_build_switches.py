#!/usr/bin/env python3
"""Tests build_switches.py (spec 12.3 item 3) on synthetic source trees.

    python3 test_build_switches.py

Each case writes a throwaway tree under _test_build_switches_tmp/ (removed
afterwards) and checks what the scan finds and how the table judges it. The
cases are the ways a switch could hide from a textual scan: a default value
shaped like a header guard, a guard that is not the file's first directive,
defined() inside an expression, #elif, a continuation line, a commented-out
#if, a CMake condition with keywords, variables and comments. Plus the scope
(managed_components/ and test/ are not product source; the top-level and
components/ CMakeLists.txt are, user 2026-10-13) and the table's own
rules (unlisted, stale, malformed entries).
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_switches as B                                         # noqa: E402

TMP = os.path.join(HERE, "_test_build_switches_tmp")

FILES = {
    "main/guard.h": "/* a header */\n#ifndef GUARD_H\n#define GUARD_H\n#ifdef INSIDE_GUARD\n#endif\n#endif\n",
    "main/guard2.h": "#ifndef G2_H\n#define G2_H\n#endif\n#ifdef G2_H\n#endif\n",
    "main/default.h":"#ifndef DEFAULT_LIKE_GUARD\n#define DEFAULT_LIKE_GUARD 60000\n#endif\n",
    "main/late.c": "#include <x.h>\nint a;\n#ifndef LATE_GUARD\n#define LATE_GUARD\n#endif\n",
    "main/expr.c": ("#if defined(EXPR_A) && EXPR_B > 2\n#elif !defined(EXPR_C) || EXPR_D == EXPR_E\n"
                    "#endif\n#if CONFIG_SOMETHING\n#endif\n#  if   SPACED\n#endif\n"),
    "main/cont.c": "#if CONT_A && \\\n    CONT_B\n#endif\n",
    "main/comments.c": ("/*\n#if IN_BLOCK_COMMENT\n#endif\n*/\n// #ifdef IN_LINE_COMMENT\n"
                        "#if REAL_ONE /* #if NOT_THIS */ // NOR_THIS\n#endif\n"
                        "#if STR_TEST == 1 // \"#if NOT_IN_STRING\"\n#endif\n"
                        "#if __has_include(\"INSIDE_STRING.h\") && AFTER_STRING\n#endif\n"),
    "main/sub/deep.h": "#ifdef DEEP_SWITCH\n#endif\n",
    "main/notc.txt": "#ifdef NOT_C_SOURCE\n#endif\n",
    "components/comp/x.c": "#ifndef COMP_DEFAULT\n#define COMP_DEFAULT 3\n#endif\n#if COMP_DEFAULT\n#endif\n",
    "managed_components/m/y.c": "#ifdef MANAGED_ONLY\n#endif\n",
    "test/t.c": "#ifdef TEST_DIR_ONLY\n#endif\n",
    "main/CMakeLists.txt": ("# if(CM_COMMENTED)\nif(CM_A)\nendif()\nif(CM_B AND NOT CM_C)\nendif()\n"
                            "elseif (${CM_D} EQUAL 2)\nif(DEFINED CM_E AND \"literal\" STREQUAL CM_F)\n"
                            "endif()\nif(CONFIG_CM_SKIPPED)\nendif()\nIF(CM_UPPER_IF)\nendif()\n"
                            "set(NOT_TESTED_HERE 1)\n"),
    "CMakeLists.txt": "set(X 1)\nif(TOP_LEVEL_ONLY)\nendif()\n",
    "components/comp/CMakeLists.txt": "if(COMP_CM)\nendif()\n",
    "managed_components/m/CMakeLists.txt": "if(MANAGED_CM)\nendif()\n",
    "test/CMakeLists.txt": "if(TEST_DIR_CM)\nendif()\n",
    "build/CMakeLists.txt": "if(BUILD_DIR_CM)\nendif()\n",
}

EXPECT = {
    "INSIDE_GUARD", "DEFAULT_LIKE_GUARD", "LATE_GUARD", "G2_H",
    "EXPR_A", "EXPR_B", "EXPR_C", "EXPR_D", "EXPR_E", "SPACED",
    "CONT_A", "CONT_B", "REAL_ONE", "STR_TEST", "AFTER_STRING", "DEEP_SWITCH", "COMP_DEFAULT",
    "CM_A", "CM_B", "CM_C", "CM_D", "CM_E", "CM_F", "CM_UPPER_IF",
    "TOP_LEVEL_ONLY", "COMP_CM",
}


def build_tree():
    shutil.rmtree(TMP, ignore_errors=True)
    for rel, text in FILES.items():
        p = os.path.join(TMP, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def main():
    build_tree()
    try:
        found = B.scan(TMP)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    got = set(found)
    ok_entry = (B.NOT, "default value", "a reason", None)
    t_ok = {"A": ok_entry, "B": (B.TEST, None, "why", "some row"), "C": (B.SHIP, None, "why", None)}
    PRB = {"probes": (("GET", "/x"),)}
    TRG = {"B": PRB}
    checks = [
        ("the scan finds exactly the expected symbols", got == EXPECT,
         "missing %s, extra %s" % (sorted(EXPECT - got), sorted(got - EXPECT))),
        ("a header guard (first directive, #define with no value) is not a symbol",
         "GUARD_H" not in got, ""),
        ("a default value shaped like a guard (#define X 60000) IS a symbol",
         "DEFAULT_LIKE_GUARD" in got, ""),
        ("a guard-shaped #ifndef that is not the file's first directive IS a symbol",
         "LATE_GUARD" in got, ""),
        ("a guard symbol tested again later in its file is counted there, and only there",
         found.get("G2_H") == ["main/guard2.h:4"], "%r" % found.get("G2_H")),
        ("defined() is not a symbol, its argument is", "defined" not in got and "EXPR_A" in got, ""),
        ("#elif is scanned", {"EXPR_C", "EXPR_D", "EXPR_E"} <= got, ""),
        ("a continuation line's second half is scanned", "CONT_B" in got, ""),
        ("a string literal inside an #if is not scanned; what follows it is",
         "INSIDE_STRING" not in got and "__has_include" not in got and "AFTER_STRING" in got, ""),
        ("commented-out directives are not scanned",
         not ({"IN_BLOCK_COMMENT", "IN_LINE_COMMENT", "NOT_THIS", "NOR_THIS", "NOT_IN_STRING"} & got), ""),
        ("CONFIG_* is excluded, from C and CMake", not any(s.startswith("CONFIG_") for s in got), ""),
        ("managed_components/ and test/ are not product source",
         not ({"MANAGED_ONLY", "TEST_DIR_ONLY"} & got), ""),
        ("only C sources are scanned in main/", "NOT_C_SOURCE" not in got, ""),
        ("CMake: keywords and a commented if() are not symbols",
         not ({"AND", "NOT", "DEFINED", "EQUAL", "STREQUAL", "CM_COMMENTED",
               "NOT_TESTED_HERE", "literal"} & got), ""),
        ("CMake: the top-level CMakeLists.txt is scanned, with its own location (user, 2026-10-13)",
         found.get("TOP_LEVEL_ONLY") == ["CMakeLists.txt:2"], "%r" % found.get("TOP_LEVEL_ONLY")),
        ("CMake: a component's CMakeLists.txt is scanned",
         found.get("COMP_CM") == ["components/comp/CMakeLists.txt:1"], "%r" % found.get("COMP_CM")),
        ("CMake: managed_components/, test/ and a build directory's CMakeLists.txt are not",
         not ({"MANAGED_CM", "TEST_DIR_CM", "BUILD_DIR_CM"} & got), ""),
        ("CMake: ${VAR}, elseif and an upper-case IF are scanned",
         {"CM_D", "CM_UPPER_IF"} <= got, ""),
        ("each symbol carries where it is tested",
         found.get("CONT_A") == ["main/cont.c:1"] and found.get("CM_B") == ["main/CMakeLists.txt:4"],
         "%r %r" % (found.get("CONT_A"), found.get("CM_B"))),
        ("classify: a clean table passes", B.classify({"A": [], "B": [], "C": []}, t_ok, TRG) == ([], [], []), ""),
        ("classify: an unlisted symbol is reported",
         B.classify({"A": [], "B": [], "C": [], "D": []}, t_ok, TRG)[0] == ["D"], ""),
        ("classify: a stale entry (no longer tested) is reported",
         B.classify({"A": [], "B": []}, t_ok, TRG)[1] == ["C"], ""),
        ("classify: 'not a switch' needs one of the three kinds",
         len(B.classify({"A": []}, {"A": (B.NOT, "a guess", "why", None)}, {})[2]) == 1, ""),
        ("classify: a test-only switch needs a refusal row",
         len(B.classify({"A": []}, {"A": (B.TEST, None, "why", None)}, {"A": PRB})[2]) == 1, ""),
        ("classify: an entry needs a reason",
         len(B.classify({"A": []}, {"A": (B.SHIP, None, "  ", None)}, {})[2]) == 1, ""),
        ("classify: a test-only switch with no trigger is malformed (12.4 inert check)",
         len(B.classify({"B": []}, {"B": (B.TEST, None, "why", "some row")}, {})[2]) == 1, ""),
        ("classify: a trigger with no probes counts as no trigger",
         len(B.classify({"B": []}, {"B": (B.TEST, None, "why", "some row")}, {"B": {"probes": ()}})[2]) == 1, ""),
        ("classify: a trigger for a switch that is not test-only is malformed",
         len(B.classify({"C": []}, {"C": (B.SHIP, None, "why", None)}, {"C": PRB})[2]) == 1, ""),
        ("classify: a trigger for an unlisted name is malformed",
         len(B.classify({}, {}, {"Z": PRB})[2]) == 1, ""),
        ("the real tables agree: every test-only switch has a trigger, every trigger a test-only switch",
         sorted(k for k, e in B.SWITCHES.items() if e[0] == B.TEST) == sorted(B.TRIGGERS), ""),
        ("classify: an unknown class is malformed",
         len(B.classify({"A": []}, {"A": ("maybe", None, "why", None)}, {})[2]) == 1, ""),
    ]
    fails = 0
    for name, ok, detail in checks:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok or not detail else "  -- " + detail))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
