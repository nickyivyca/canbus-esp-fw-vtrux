"""Tests of the build folder readers (vtrux_build.builddir): R11, and the
records the build checks read (ninja's deps log and build graph, the
options in effect, project_description.json).

    python -B shared/tools/tests/test_builddir.py

The deps logs are written here by deps_log(), from ninja's format
(src/deps_log.cc), not taken from a build. The reader was also checked by
hand against `ninja -t deps` on a real IDF 5.4.1 build of 1203 outputs.
"""

import json
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                                   # noqa: E402
from vtrux_build.builddir import (BuildDir, BuildDirError,       # noqa: E402
                                  parse_ninja_deps, parse_ninja_text)


def deps_log(records, version=4):
    """records: [(output, [deps])], in order. Writes path records for new
    names as ninja does, then one deps record per entry."""
    out = bytearray(b"# ninjadeps\n" + struct.pack("<i", version))
    ids = {}

    def node(p):
        if p in ids:
            return ids[p]
        ids[p] = len(ids)
        b = p.encode()
        b += b"\0" * ((4 - len(b) % 4) % 4)
        out.extend(struct.pack("<I", len(b) + 4) + b
                   + struct.pack("<I", (~ids[p]) & 0xFFFFFFFF))
        return ids[p]

    for o, deps in records:
        dep_ids = [node(d) for d in deps]
        oid = node(o)
        mtime = [123, 0] if version >= 4 else [123]
        body = struct.pack("<%di" % (1 + len(mtime) + len(dep_ids)),
                           oid, *(mtime + dep_ids))
        out.extend(struct.pack("<I", len(body) | 0x80000000) + body)
    return bytes(out)


BUILD_NINJA = """\
# a CMake-shaped build.ninja
ninja_required_version = 1.5
cmake_ninja_workdir = C$:/proj/build/
include CMakeFiles/rules.ninja

build esp-idf/main/CMakeFiles/m.dir/a.c.obj: C_COMPILER C$:/proj/main/a.c || cmake_object_order
  FLAGS = -O2
build app.elf | app.map: C_EXECUTABLE_LINKER esp-idf/main/CMakeFiles/m.dir/a.c.obj $
    libx.a | C$:/proj/ld/memory.ld C$:/proj/path$ with$ space.ld || order.txt
build CMakeFiles/menuconfig | ${cmake_ninja_workdir}CMakeFiles/menuconfig: CUSTOM_COMMAND
build build.ninja: RERUN_CMAKE | C$:/proj/CMakeLists.txt C$:/proj/main/CMakeLists.txt $
    C$:/idf/tools/cmake/project.cmake
build dollar$$sign: phony
subninja sub.ninja
build after_sub: phony ${sub_only}
"""

RULES_NINJA = "rule C_COMPILER\n  command = gcc $in\nshared = from_rules\n"
SUB_NINJA = "sub_only = leaked\nbuild in_sub: phony $shared\n"


def build_dir(name, files):
    root = scratch.fresh(name)
    for rel, data in files.items():
        scratch.write(os.path.join(root, *rel.split("/")), data)
    return BuildDir(root)


class NinjaDeps(unittest.TestCase):

    def test_v4_and_v3(self):
        recs = [("a.o", ["a.c", "a.h"]), ("b.o", ["b.c", "a.h"])]
        for v in (3, 4):
            p = scratch.write(os.path.join(scratch.fresh("deps_v%d" % v),
                                           ".ninja_deps"), deps_log(recs, v))
            self.assertEqual(parse_ninja_deps(p),
                             {"a.o": ["a.c", "a.h"], "b.o": ["b.c", "a.h"]})

    def test_newest_record_wins(self):
        p = scratch.write(os.path.join(scratch.fresh("deps_newest"),
                                       ".ninja_deps"),
                          deps_log([("a.o", ["old.h"]), ("a.o", ["new.h"])]))
        self.assertEqual(parse_ninja_deps(p), {"a.o": ["new.h"]})

    def test_path_padding(self):
        recs = [("x" * n + ".o", ["y" * m]) for n in range(1, 6)
                for m in range(1, 5)]
        p = scratch.write(os.path.join(scratch.fresh("deps_pad"),
                                       ".ninja_deps"), deps_log(recs))
        self.assertEqual(parse_ninja_deps(p), dict(recs))

    def test_refusals(self):
        d = scratch.fresh("deps_bad")
        good = deps_log([("a.o", ["a.c"])])
        bad_check = bytearray(good)
        # The first record is the path "a.c": size word at 16, the path
        # padded to 4 bytes at 20, its checksum word at 24.
        bad_check[24] ^= 1
        for name, data in (("sig", b"# notninja\n" + good[11:]),
                           ("ver", good[:12] + struct.pack("<i", 5) + good[16:]),
                           ("trunc", good[:-2]),
                           ("check", bytes(bad_check))):
            p = scratch.write(os.path.join(d, name), data)
            with self.assertRaises(BuildDirError, msg=name):
                parse_ninja_deps(p)

    def test_current_only(self):
        bd = build_dir("deps_current", {
            "build.ninja": "build a.o: cc a.c\n",
            ".ninja_deps": deps_log([("a.o", ["a.c", "a.h"]),
                                     ("old_name.o", ["old.c"])])})
        self.assertEqual(bd.ninja_deps(), {"a.o": ["a.c", "a.h"]})
        self.assertEqual(sorted(bd.ninja_deps(current_only=False)),
                         ["a.o", "old_name.o"])


class NinjaGraph(unittest.TestCase):

    def setUp(self):
        self.bd = build_dir("graph", {"build.ninja": BUILD_NINJA,
                                      "CMakeFiles/rules.ninja": RULES_NINJA,
                                      "sub.ninja": SUB_NINJA})
        self.edges = {e.outputs[0]: e for e in self.bd.ninja_edges()}

    def test_groups_and_escapes(self):
        e = self.edges["app.elf"]
        self.assertEqual(e.rule, "C_EXECUTABLE_LINKER")
        self.assertEqual(e.implicit_outputs, ["app.map"])
        self.assertEqual(e.inputs, ["esp-idf/main/CMakeFiles/m.dir/a.c.obj",
                                    "libx.a"])
        self.assertEqual(e.implicit, ["C:/proj/ld/memory.ld",
                                      "C:/proj/path with space.ld"])
        self.assertEqual(e.order_only, ["order.txt"])

    def test_variables(self):
        e = self.edges["CMakeFiles/menuconfig"]
        self.assertEqual(e.implicit_outputs, ["C:/proj/build/CMakeFiles/menuconfig"])

    def test_include_shares_scope_subninja_does_not_leak(self):
        self.assertEqual(self.edges["in_sub"].inputs, ["from_rules"])
        self.assertEqual(self.edges["after_sub"].inputs, [])

    def test_dollar(self):
        self.assertIn("dollar$sign", self.edges)

    def test_cmake_inputs(self):
        self.assertEqual(self.bd.cmake_inputs(), [
            "C:/proj/CMakeLists.txt", "C:/proj/main/CMakeLists.txt",
            "C:/idf/tools/cmake/project.cmake"])

    def test_files_read(self):
        self.bd = build_dir("graph_read", {
            "build.ninja": BUILD_NINJA, "CMakeFiles/rules.ninja": RULES_NINJA,
            "sub.ninja": SUB_NINJA,
            ".ninja_deps": deps_log([("esp-idf/main/CMakeFiles/m.dir/a.c.obj",
                                      ["C:/proj/main/a.c",
                                       "C:/proj/main/inc/a.h"])])})
        got = self.bd.files_read()
        for p in ("C:/proj/main/inc/a.h", "C:/proj/path with space.ld",
                  "C:/proj/CMakeLists.txt"):
            self.assertIn(os.path.normpath(p), got)
        self.assertIn(self.bd.resolve("libx.a"), got)

    def test_no_rerun_edge(self):
        bd = build_dir("graph_norerun", {"build.ninja": "build a: phony\n"})
        with self.assertRaises(BuildDirError):
            bd.cmake_inputs()

    def test_text_errors(self):
        for bad in ("build a b\n", "build a: rule ${unclosed\n",
                    "build a: rule $!\n"):
            with self.assertRaises(BuildDirError, msg=bad):
                parse_ninja_text(bad, ".")


DESC = {"app_bin": "app.bin", "app_elf": "app.elf",
        "c_compiler": "/x/gcc", "git_revision": "v5.4.1",
        "build_components": ["main", "freertos", ""],
        "build_component_info": {"main": {"dir": "C:/proj/main"},
                                 "freertos": {"dir": "C:/idf/components/freertos"}}}


class Description(unittest.TestCase):
    """R11: the image is the one project_description.json names."""

    def bd(self, name, desc=DESC, files=("app.bin", "app.elf")):
        f = {"project_description.json": json.dumps(desc)}
        f.update({n: b"x" for n in files})
        return build_dir(name, f)

    def test_named_image(self):
        bd = self.bd("desc_ok", files=("app.bin", "app.elf", "newer.bin"))
        self.assertEqual(os.path.basename(bd.app_bin()), "app.bin")
        self.assertEqual(os.path.basename(bd.app_elf()), "app.elf")

    def test_never_falls_back(self):
        cases = {
            "desc_nokey": (dict(DESC, app_bin=None), ("app.bin",)),
            "desc_empty": (dict(DESC, app_bin=""), ("app.bin",)),
            "desc_path": (dict(DESC, app_bin="../app.bin"), ("app.bin",)),
            "desc_nofile": (DESC, ("other.bin",)),
        }
        for name, (desc, files) in cases.items():
            with self.assertRaises(BuildDirError, msg=name):
                self.bd(name, desc, files).app_bin()

    def test_no_description(self):
        with self.assertRaises(BuildDirError):
            build_dir("desc_none", {"app.bin": b"x"}).app_bin()

    def test_components(self):
        self.assertEqual(self.bd("desc_comp").components(), {
            "main": os.path.normpath("C:/proj/main"),
            "freertos": os.path.normpath("C:/idf/components/freertos")})

    def test_component_without_dir(self):
        d = dict(DESC, build_components=["main", "ghost"])
        with self.assertRaises(BuildDirError):
            self.bd("desc_ghost", d).components()


class Options(unittest.TestCase):

    def test_sdkconfig_json_and_cache(self):
        bd = build_dir("opts", {
            "config/sdkconfig.json": json.dumps(
                {"APP_REPRODUCIBLE_BUILD": True, "LOG_DEFAULT_LEVEL": 3}),
            "CMakeCache.txt": "# comment\n// help\nGI_X:BOOL=ON\n"
                              "SDKCONFIG:FILEPATH=C:/p/sdkconfig\nnot a line\n"})
        self.assertIs(bd.options()["APP_REPRODUCIBLE_BUILD"], True)
        self.assertEqual(bd.cmake_cache()["GI_X"], ("BOOL", "ON"))
        self.assertEqual(bd.cmake_cache()["SDKCONFIG"],
                         ("FILEPATH", "C:/p/sdkconfig"))

    def test_missing(self):
        bd = build_dir("opts_none", {})
        for f in (bd.options, bd.cmake_cache, bd.ninja_edges):
            with self.assertRaises(BuildDirError):
                f()


if __name__ == "__main__":
    unittest.main()
