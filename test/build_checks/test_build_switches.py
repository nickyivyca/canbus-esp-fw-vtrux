#!/usr/bin/env python3
"""Tests build_switches.py (spec 12.3 item 3) on synthetic source trees.

    python3 test_build_switches.py

Each case writes a throwaway tree under _test_build_switches_tmp/ (removed
afterwards) and checks what the scan finds and how the table judges it. The
cases are the ways a switch could hide from a textual scan: a default value
shaped like a header guard, a guard that is not the file's first directive,
defined() inside an expression, #elif, a continuation line, a commented-out
#if, a CMake condition with keywords, variables and comments. Plus the scope
(managed_components/ and test/ are not product C source), the build's record
of its CMake files (spec, changed 2026-10-13, user) with its guards, the
build-option definition read narrowly (reviewer, 2026-10-13), and the table's
own rules (unlisted, stale, malformed entries).

No ninja or cmake is run: the record is given as text, and read_record()'s
process calls are substituted.
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_switches as B                                         # noqa: E402

TMP = os.path.join(HERE, "_test_build_switches_tmp")
REPO = os.path.join(TMP, "repo")       # the tree; TMP/idf beside it stands for the IDF install

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
    "CMakeLists.txt": "project(wican)\nif(TOP_LEVEL_ONLY)\nendif()\ninclude(cmake/extra.cmake)\n",
    "components/comp/CMakeLists.txt": "if(COMP_CM)\nendif()\n",
    # In the record: a .cmake file no CMakeLists.txt walk would find. One case
    # per line, so the expected line numbers can be read off the list.
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
        "set(EMPTYSET)",                                   # 18 no value: unsets, cache shows through
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
        "set(PAREN_STR \"x(\")",                         # 67 '(' inside quotes
        "if(AFTER_PAREN)",                                 # 68
        "endif()",                                         # 69
    ]) + "\n",
    "managed_components/m/CMakeLists.txt": "if(MANAGED_CM)\nendif()\n",
    "test/CMakeLists.txt": "if(TEST_DIR_CM)\nendif()\n",
    "build/CMakeLists.txt": "if(BUILD_DIR_CM)\nendif()\n",
    "main/NotInRecord/CMakeLists.txt": "if(NOT_IN_RECORD)\nendif()\n",
    "sdkconfig": "CONFIG_X=y\n",
    # the record's other files, present so a scan that wrongly takes them finds
    # their names instead of crashing on a missing file
    "build/CMakeFiles/3.30.2/CMakeSystem.cmake": "if(BUILD_RECORD_CM)\nendif()\n",
    "build/CMakeFiles/git-data/grabRef.cmake": "if(BUILD_RECORD_CM2)\nendif()\n",
    "outputs_are_not_inputs.cmake": "if(OUTPUT_FILE_CM)\nendif()\n",
    "main/idf_component.yml": "dependencies: {}\n",
}
OUTSIDE = {"../idf/tools/cmake/project.cmake": "if(IDF_OUTSIDE)\nendif()\n"}

EXPECT = {
    "INSIDE_GUARD", "DEFAULT_LIKE_GUARD", "LATE_GUARD", "G2_H",
    "EXPR_A", "EXPR_B", "EXPR_C", "EXPR_D", "EXPR_E", "SPACED",
    "CONT_A", "CONT_B", "REAL_ONE", "STR_TEST", "AFTER_STRING", "DEEP_SWITCH", "COMP_DEFAULT",
    "CM_A", "CM_B", "CM_C", "CM_D", "CM_E", "CM_F", "CM_UPPER_IF",
    "TOP_LEVEL_ONLY", "COMP_CM",
    "LATE_SET", "SOMETHING_ELSE", "COND_SET", "CACHED", "PS", "EMPTYSET", "OWN_B", "TAINT",
    "ENVSET", "ENV_OPT", "ENV_OPT2", "QUOTED_REF", "other_SOURCE_DIR", "FOO_ROOT", "FN_SET",
    "STRLEN_OUT", "AFTER_QUOTE", "WHILE_OPT", "AFTER_PAREN",
}

# A slice of `cmake --help-variable-list` (3.30), placeholders included.
CMAKE_VARS = """CMAKE_BUILD_TYPE
CMAKE_CURRENT_LIST_DIR
CMAKE_<LANG>_FLAGS
<PROJECT-NAME>_SOURCE_DIR
<PackageName>_ROOT
WIN32
"""

SCANNED = ["CMakeLists.txt", "cmake/extra.cmake", "components/comp/CMakeLists.txt", "main/CMakeLists.txt"]
EXCLUDED = {"build/CMakeFiles/git-data/grabRef.cmake", "build/CMakeFiles/3.30.2/CMakeSystem.cmake",
            "managed_components/m/CMakeLists.txt"}


def build_tree():
    shutil.rmtree(TMP, ignore_errors=True)
    for rel, text in list(FILES.items()) + list(OUTSIDE.items()):
        p = os.path.normpath(os.path.join(REPO, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def query_text(repo, build):
    """What `ninja -t query build.ninja` prints, in its shape: absolute repo
    paths, a path relative to the build directory, implicit and order-only."""
    j = lambda *p: os.path.join(*p).replace(os.sep, "/")
    return "\n".join([
        "build.ninja:",
        "  input: RERUN_CMAKE",
        "    | " + j(repo, "CMakeLists.txt"),
        "    | " + j(repo, "cmake", "extra.cmake"),
        "    | " + j(repo, "components", "comp", "CMakeLists.txt"),
        "    | " + j(repo, "main", "CMakeLists.txt"),
        "    | " + j(repo, "main", "idf_component.yml"),
        "    | " + j(repo, "managed_components", "m", "CMakeLists.txt"),
        "    | " + j(repo, "sdkconfig"),
        "    | " + j(os.path.dirname(repo), "idf", "tools", "cmake", "project.cmake"),
        "    | CMakeFiles/3.30.2/CMakeSystem.cmake",
        "    || " + j(build, "CMakeFiles", "git-data", "grabRef.cmake"),
        "  outputs:",
        "    " + j(repo, "outputs_are_not_inputs.cmake"),
        "",
    ])


class Done:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_run(query, varlist, q_rc=0):
    def run(cmd, **kw):
        if "query" in cmd:
            return Done(q_rc, query, "ninja: error" if q_rc else "")
        return Done(0, varlist)
    return run


def refused(fn):
    """The RecordError's text, or None if fn() did not refuse."""
    try:
        fn()
    except B.RecordError as e:
        return str(e)
    return None


def main():
    build_tree()
    build = os.path.join(REPO, "build")
    try:
        # the record, read the way the scan reads it
        paths = B.parse_query(query_text(REPO, build))
        scanned, excluded, outside = B.record_files(paths, REPO, build)
        is_builtin = B.builtin_matcher(CMAKE_VARS.splitlines(), B.project_names(REPO, scanned))
        found = B.scan(REPO, B.Record(build, scanned, excluded, outside, is_builtin, "cmake"))
        got = set(found)

        # read_record end to end, ninja and cmake substituted
        with open(os.path.join(build, "CMakeCache.txt"), "w") as fh:
            fh.write("CMAKE_COMMAND:INTERNAL=/x/cmake\nCMAKE_MAKE_PROGRAM:FILEPATH=/x/ninja\n")
        bn = os.path.join(build, "build.ninja")
        open(bn, "w").close()
        future = time.time() + 100
        os.utime(bn, (future, future))
        good = None
        try:
            good = B.read_record(REPO, build, fake_run(query_text(REPO, build), CMAKE_VARS))
        except B.RecordError as e:
            good_refused = str(e)
        no_ninja_file = refused(lambda: B.read_record(REPO, os.path.join(REPO, "no_such_build")))
        query_failed = refused(lambda: B.read_record(      # the text is good: only the exit status refuses
            REPO, build, fake_run(query_text(REPO, build), CMAKE_VARS, q_rc=1)))
        no_vars = refused(lambda: B.read_record(REPO, build, fake_run(query_text(REPO, build), "")))
        not_this_tree = refused(lambda: B.read_record(
            REPO, build, fake_run(query_text(os.path.join(TMP, "other"), build), CMAKE_VARS)))
        empty = refused(lambda: B.check_record([], REPO, bn))
        no_top = refused(lambda: B.check_record(["main/CMakeLists.txt"], REPO, bn))
        os.utime(bn, (future - 1000, future - 1000))
        stale = refused(lambda: B.check_record(SCANNED, REPO, bn))
        os.utime(bn, (future, future))
        fresh = refused(lambda: B.check_record(SCANNED, REPO, bn))
        os.remove(os.path.join(build, "CMakeCache.txt"))
        no_cache = refused(lambda: B.read_record(REPO, build, fake_run(query_text(REPO, build), CMAKE_VARS)))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    ok_entry = (B.NOT, "default value", "a reason", None)
    t_ok = {"A": ok_entry, "B": (B.TEST, None, "why", "some row"), "C": (B.SHIP, None, "why", None)}
    PRB = {"probes": (("GET", "/x"),)}
    TRG = {"B": PRB}
    at = found.get
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
         at("G2_H") == ["main/guard2.h:4"], "%r" % at("G2_H")),
        ("defined() is not a symbol, its argument is", "defined" not in got and "EXPR_A" in got, ""),
        ("#elif is scanned", {"EXPR_C", "EXPR_D", "EXPR_E"} <= got, ""),
        ("a continuation line's second half is scanned", "CONT_B" in got, ""),
        ("a string literal inside an #if is not scanned; what follows it is",
         "INSIDE_STRING" not in got and "__has_include" not in got and "AFTER_STRING" in got, ""),
        ("commented-out directives are not scanned",
         not ({"IN_BLOCK_COMMENT", "IN_LINE_COMMENT", "NOT_THIS", "NOR_THIS", "NOT_IN_STRING"} & got), ""),
        ("CONFIG_* is excluded, from C and CMake", not any(s.startswith("CONFIG_") for s in got), ""),
        ("managed_components/ and test/ are not product C source",
         not ({"MANAGED_ONLY", "TEST_DIR_ONLY"} & got), ""),
        ("only C sources are scanned in main/", "NOT_C_SOURCE" not in got, ""),
        ("CMake: keywords and a commented if() are not symbols",
         not ({"AND", "NOT", "DEFINED", "EQUAL", "STREQUAL", "CM_COMMENTED",
               "NOT_TESTED_HERE", "literal"} & got), ""),
        ("CMake: ${VAR}, elseif and an upper-case IF are scanned",
         {"CM_D", "CM_UPPER_IF"} <= got, ""),
        ("each symbol carries where it is tested",
         at("CONT_A") == ["main/cont.c:1"] and at("CM_B") == ["main/CMakeLists.txt:4"]
         and at("TOP_LEVEL_ONLY") == ["CMakeLists.txt:2"],
         "%r %r %r" % (at("CONT_A"), at("CM_B"), at("TOP_LEVEL_ONLY"))),

        # ---- the record
        ("record: every input line is read, | and || included; outputs are not",
         len(paths) == 10 and not any("outputs_are_not_inputs" in p for p in paths), "%r" % paths),
        ("record: the repo's CMake files are scanned, a .cmake one included",
         scanned == SCANNED, "%r" % scanned),
        ("record: managed_components/ and the build directory are excluded, each with a reason",
         {r for r, _ in excluded} == EXCLUDED and all(why for _, why in excluded), "%r" % excluded),
        ("record: a CMake file outside the repo (the IDF install) is counted, not scanned",
         outside == 1 and "IDF_OUTSIDE" not in got, "outside=%d" % outside),
        ("record: non-CMake inputs (sdkconfig, idf_component.yml) are not scanned",
         not any(r.endswith(("sdkconfig", ".yml")) for r in scanned), ""),
        ("record: a .cmake file in the record is scanned with its own location",
         at("LATE_SET") == ["cmake/extra.cmake:4"], "%r" % at("LATE_SET")),
        ("record: a CMake file the record does not name is not scanned (nor test/, build/, managed)",
         not ({"NOT_IN_RECORD", "TEST_DIR_CM", "BUILD_DIR_CM", "MANAGED_CM"} & got), ""),
        ("read_record: end to end, it reads the same files", good is not None and good.scanned == SCANNED
         and good.cmake == "/x/cmake", "%r" % ((good.scanned,) if good else good_refused)),
        ("read_record: no build.ninja refuses", no_ninja_file is not None, ""),
        ("read_record: a failed ninja query refuses", query_failed is not None, ""),
        ("read_record: no built-in variable list refuses", no_vars is not None, ""),
        ("read_record: no CMakeCache.txt (no cmake to ask) refuses", no_cache is not None, ""),
        ("read_record: another tree's record refuses (nothing of this repo in it)",
         not_this_tree is not None, "%r" % not_this_tree),
        ("check_record: an empty list refuses, saying so", empty is not None and "no CMake file" in empty,
         "%r" % empty),
        ("check_record: a list without the top-level CMakeLists.txt refuses", no_top is not None, ""),
        ("check_record: a file newer than build.ninja refuses (reconfigure first)",
         stale is not None and "newer" in stale, "%r" % stale),
        ("check_record: a fresh, complete list is accepted", fresh is None, "%r" % fresh),

        # ---- build options, narrowly
        ("option: a variable set unconditionally before its test is not one",
         not ({"OWN_A", "CHAIN", "CLEAN_REF", "HASH_STR"} & got), ""),
        ("option: tested before it is set, it is one; tested after, it is not",
         at("LATE_SET") == ["cmake/extra.cmake:4"], "%r" % at("LATE_SET")),
        ("option: set only under a condition, it is one", "COND_SET" in got, ""),
        ("option: a CACHE set, a PARENT_SCOPE set, a set with no value, an unset: still one",
         {"CACHED", "PS", "EMPTYSET", "OWN_B"} <= got, ""),
        ("option: a set from an outside variable keeps it one", "TAINT" in got and "EXT_SRC" not in got, ""),
        ("option: a set from the environment keeps it one", "ENVSET" in got, ""),
        ("option: $ENV{X} is always one, even beside a CMake variable of the name",
         {"ENV_OPT", "ENV_OPT2"} <= got and at("ENV_OPT2") == ["cmake/extra.cmake:38"], "%r" % at("ENV_OPT2")),
        ("option: a reference inside a quoted condition is tested", "QUOTED_REF" in got and "on" not in got, ""),
        ("option: CMake built-ins are not, exact or by placeholder",
         not ({"CMAKE_BUILD_TYPE", "WIN32", "CMAKE_C_FLAGS"} & got), ""),
        ("option: <PROJECT-NAME> expands to the declared project only",
         "wican_SOURCE_DIR" not in got and "other_SOURCE_DIR" in got, ""),
        ("option: a pattern opening with a placeholder (<PackageName>_ROOT) is not used",
         "FOO_ROOT" in got, ""),
        ("option: a set inside a function, or by string(), leaves it one",
         {"FN_SET", "STRLEN_OUT"} <= got, ""),
        ("option: '#' and ')' inside quotes do not end the command",
         at("AFTER_QUOTE") == ["cmake/extra.cmake:57"], "%r" % at("AFTER_QUOTE")),
        ("option: '(' inside quotes does not open a parenthesis", at("AFTER_PAREN") == ["cmake/extra.cmake:68"],
         "%r" % at("AFTER_PAREN")),
        ("option: a bracket comment is not scanned (to its close), nor a command ending in 'if'",
         not ({"IN_BRACKET_COMMENT", "NOT_A_CONDITION"} & got), ""),
        ("option: while() is a test", "WHILE_OPT" in got, ""),
        ("builtin_matcher: the rules on their own",
         [B.builtin_matcher(CMAKE_VARS.splitlines(), ["p"])(s) for s in
          ("WIN32", "CMAKE_CXX_FLAGS", "CMAKE_FLAGS", "p_SOURCE_DIR", "q_SOURCE_DIR", "X_ROOT", "WIN32X",
           "CMAKE_C_FLAGS_EXTRA")]
         == [True, True, False, True, False, False, False, False], ""),

        # ---- the table
        ("classify: a clean table passes", B.classify({"A": [], "B": [], "C": []}, t_ok, TRG) == ([], [], []), ""),
        ("classify: an unlisted symbol is reported",
         B.classify({"A": [], "B": [], "C": [], "D": []}, t_ok, TRG)[0] == ["D"], ""),
        ("classify: a stale entry (no longer tested) is reported",
         B.classify({"A": [], "B": []}, t_ok, TRG)[1] == ["C"], ""),
        ("classify: 'not a switch' needs a kind",
         len(B.classify({"A": []}, {"A": (B.NOT, " ", "why", None)}, {})[2]) == 1
         and len(B.classify({"A": []}, {"A": (B.NOT, None, "why", None)}, {})[2]) == 1, ""),
        ("classify: a kind beyond the spec's three examples is accepted (reviewer, 2026-10-13)",
         B.classify({"A": []}, {"A": (B.NOT, "computed value", "why", None)}, {})[2] == [], ""),
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
