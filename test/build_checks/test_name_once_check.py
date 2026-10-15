#!/usr/bin/env python3
"""Tests name_once_check.py's judgements (no build, no ESP-IDF).

    python3 test_name_once_check.py

The real-build half is the run itself: it must fail on a tree that passes the
name on every compile line and pass on one that does not (spec 12.4 "the name
enters the build once").
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import name_once_check as N                                        # noqa: E402

TMP = os.path.join(HERE, "_test_name_once_tmp")
SHA = "e998385a23bace040dcd96b67ace2bfb224b718c"


def desc(ver):
    return (ver, "wican-fw_obd_%s_man" % ver, "v5.4.1", "", "")


def img(ver):
    return ["build/wican-fw_obd_%s_man.bin" % ver]


ERR_VERSION = ("-- noise\nCMake Error at cmake/git_tree_name.cmake:153 (message):\n"
               "  git_tree_name: the name is 38 characters, over esp_app_desc.version's 31\n\n\n-- more\n")
ERR_OTHER = "CMake Error at CMakeLists.txt:170 (message):\n  project name too long\n\n\n"


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "build", "esp-idf", "main"))
    try:
        main_src = os.path.join(TMP, "main", "gen_inhibit.c")
        cc = [{"directory": os.path.join(TMP, "build"), "file": main_src,
               "output": "esp-idf/main/CMakeFiles/__idf_main.dir/gen_inhibit.c.obj", "command": "cc -DX"},
              {"directory": os.path.join(TMP, "build"), "file": os.path.join(TMP, "..", "idf", "esp_app_desc.c"),
               "output": "esp-idf/esp_app_format/esp_app_desc.c.obj", "command": "cc"}]
        with open(os.path.join(TMP, "build", "compile_commands.json"), "w") as fh:
            json.dump(cc, fh)
        parsed = N.objects(os.path.join(TMP, "build"))
        no_objs = N.objects(os.path.join(TMP, "no_build"))
        o_main = os.path.normpath(os.path.join(TMP, "build", cc[0]["output"]))
        o_desc = os.path.normpath(os.path.join(TMP, "build", cc[1]["output"]))
        o_comp = os.path.normpath(os.path.join(TMP, "build", "esp-idf", "debug_logs", "debug_logs.c.obj"))
        # the rest uses an explicit map, so it does not lean on objects()
        objs = {o_main: os.path.normpath(main_src), o_desc: os.path.normpath(cc[1]["file"]),
                o_comp: os.path.normpath(os.path.join(TMP, "components", "debug_logs", "src", "debug_logs.c"))}
        for o in (o_main, o_comp):
            os.makedirs(os.path.dirname(o), exist_ok=True)
            open(o, "w").close()
        before = N.mtimes(objs)
        time.sleep(0.05)
        os.makedirs(os.path.dirname(o_desc), exist_ok=True)
        open(o_desc, "w").close()                       # appeared
        after = N.mtimes(objs)
        ch_desc_only = N.recompiled(before, after)
        os.utime(o_main, ns=(before[o_main] + 10**9, before[o_main] + 10**9))
        ch_both = N.recompiled(before, N.mtimes(objs))
        prod = N.product_recompiled(objs, ch_both, TMP)
        prod_none = N.product_recompiled(objs, ch_desc_only, TMP)
        prod_comp = N.product_recompiled(objs, [o_comp, o_desc], TMP)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    checks = [
        ("fnv1a32: the wire-measured 0x7F3 values (4a0a796, 2026-10-03; a71b90a, evidence doc)",
         N.fnv1a32("4a0a796") == 0x4F28F92D and N.fnv1a32("a71b90a") == 0x991D4542),
        ("fnv1a32: an empty string is the offset basis", N.fnv1a32("") == 0x811C9DC5),
        ("objects: compile_commands outputs resolved against their directory, sources kept",
         parsed.get(o_main) == os.path.normpath(main_src) and len(parsed) == 2 and no_objs == {}),
        ("recompiled: an object that appeared counts; an untouched one does not",
         ch_desc_only == [o_desc]),
        ("recompiled: a changed mtime counts", sorted(ch_both) == sorted([o_main, o_desc])),
        ("product: a main/ object is the product's, ESP-IDF's descriptor is not",
         prod == [o_main] and prod_none == []),
        ("product: a components/ object is the product's too", prod_comp == [o_comp]),
        ("name on compile lines: a quoted define counts; a path containing the sha does not",
         N.lines_with_name([
             {"command": 'gcc -DGIT_SHA=\\"d74777d\\" -c /x/wican-nameonce-d74777d-1/main/a.c'},
             {"command": 'gcc -DPROJECT_VER="d74777d" -c b.c'},
             {"command": 'gcc -I/x/wican-nameonce-d74777d-1/build/config -c c.c'},
             {"command": 'gcc -DGIT_SHA=\\"d74777d-dirty\\" -c d.c'}], "d74777d") == 2),
        ("under: main/ but not a sibling that starts with 'main'",
         N.under(os.path.join("t", "main", "a.c"), [os.path.join("t", "main")])
         and not N.under(os.path.join("t", "mainx", "a.c"), [os.path.join("t", "main")])),
        ("readme: the right name and no product recompile passes",
         N.judge_readme(0, img("e998385-dirty"), desc("e998385-dirty"), SHA, True, [], 900)[0] is True),
        ("readme: THE ROW -- a product object recompiled fails",
         N.judge_readme(0, img("e998385-dirty"), desc("e998385-dirty"), SHA, True, ["x/gen_inhibit.c.obj"], 900)[0]
         is False),
        ("readme: no objects measured fails (undetermined is not a pass)",
         N.judge_readme(0, img("e998385-dirty"), desc("e998385-dirty"), SHA, True, [], 0)[0] is False),
        ("readme: no image (the naming did not re-run) fails",
         N.judge_readme(0, [], None, SHA, True, [], 900)[0] is False),
        ("readme: a stale name fails", N.judge_readme(0, img("e998385"), desc("e998385"), SHA, True, [], 900)[0]
         is False),
        ("readme: a failed build fails", N.judge_readme(1, img("e998385-dirty"), desc("e998385-dirty"), SHA, True,
                                                        [], 900)[0] is False),
        ("overlong: a failed build with no image passes", N.judge_overlong(1, [])[0] is True),
        ("overlong: an image written fails, even with the build failing", N.judge_overlong(1, img("x"))[0] is False),
        ("overlong: a build that succeeded fails", N.judge_overlong(0, [])[0] is False),
        ("module: failed, no image, the error names the version field: passes",
         N.judge_module(1, [], ERR_VERSION)[0] is True),
        ("module: the fixture building (the version cut short) fails",
         N.judge_module(0, ["build/gtn_fixture.bin"], "")[0] is False),
        ("module: an image written fails, even with the build failing and the right error",
         N.judge_module(1, ["build/gtn_fixture.bin"], ERR_VERSION)[0] is False),
        ("module: a build that succeeded fails, even with the right error text in the log",
         N.judge_module(0, [], ERR_VERSION)[0] is False),
        ("module: a failure that does not name the version field fails", N.judge_module(1, [], ERR_OTHER)[0] is False),
        ("module: a failure with no CMake error fails, saying so",
         N.judge_module(1, [], "ninja: build stopped")[0] is False
         and "no CMake error" in N.judge_module(1, [], "ninja: build stopped version")[1]),
        ("error_block: only the first error, to the blank lines",
         "more" not in N.error_block(ERR_VERSION) and "esp_app_desc" in N.error_block(ERR_VERSION)),
        ("the long name is over the version field and a plain tag name",
         len(N.OVERLONG) > N.VERSION_FIELD and all(c.isalnum() or c in "-." for c in N.OVERLONG)),
        ("the fixture calls git_tree_name() before project() and has no length guard of its own",
         N.FIXTURE_FILES["CMakeLists.txt"].index("git_tree_name(") < N.FIXTURE_FILES["CMakeLists.txt"].index("project(")
         and "string(LENGTH" not in N.FIXTURE_FILES["CMakeLists.txt"]),
    ]
    fails = 0
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
