"""R17 scope check: each rule shown failing on a planted input and passing on
a faithful one. Runs alone: python -B shared/tools/vtrux_checks/tests/test_scope.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from vtrux_checks import scope as S  # noqa: E402


def scratch():
    """A folder under the worktree's gitignored scratch/, else the temp folder."""
    d = HERE
    while not os.path.exists(os.path.join(d, ".git")) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    base = os.path.join(d, "scratch", "vtrux-checks-tests") if os.path.exists(os.path.join(d, ".git")) else None
    if base:
        os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(dir=base)


def j(*parts):
    return S.norm(os.path.join(*parts))


ROOT = os.path.abspath(os.sep)
PROJ = j(ROOT, "w", "proj")
IDF = j(ROOT, "esp", "esp-idf")
TOOL = j(ROOT, "tools", "riscv32-esp-elf", "esp-14.2.0", "riscv32-esp-elf")
CMR = j(ROOT, "tools", "cmake", "3.30.2", "share", "cmake-3.30")
SHARED = j(ROOT, "w", "proj-other", "shared")


def facts(**kw):
    f = {"project": PROJ, "idf": IDF, "toolchains": {". C": TOOL}, "cmake_roots": [CMR],
         "shared": [SHARED, j(PROJ, "shared")],
         "files": [j(PROJ, "main", "main.c"), j(PROJ, "build", "config", "sdkconfig.h"),
                   j(PROJ, "managed_components", "espressif__mdns", "mdns.c"), j(IDF, "components", "x", "x.h"),
                   j(TOOL, "riscv32-esp-elf", "include", "stdio.h"), j(CMR, "Modules", "CMakeDetermineSystem.cmake")],
         "lock": {"espressif/mdns": {"version": "1.4.0", "source": {"type": "service"}},
                  "idf": {"version": "5.4.1", "source": {"type": "idf"}}},
         "managed": {"espressif__mdns": "1.4.0"}, "folders": [j(PROJ, "build")]}
    f.update(kw)
    return f


def rules(bad):
    return sorted(set(r for r, _ in bad))


class Judge(unittest.TestCase):
    def test_faithful_passes_both_kinds(self):
        self.assertEqual(S.judge(facts(), "release"), [])
        self.assertEqual(S.judge(facts(), "bench"), [])

    def test_file_outside_fails(self):
        for p in (j(ROOT, "w", "proj-other", "main", "x.h"),         # another worktree, not its shared/
                  j(ROOT, "tools", "riscv32-esp-elf", "esp-13", "x.h"),   # another toolchain beside the chosen one
                  j(ROOT, "tools", "ccache", "x"),                      # elsewhere in the tools folder
                  j(ROOT, "w", "projector", "x.c")):                    # a sibling whose name starts like the root
            f = facts(files=facts()["files"] + [p])
            for kind in ("release", "bench"):
                self.assertEqual(S.judge(f, kind), [("outside", p)], (p, kind))

    def test_shared_only_in_bench(self):
        for p in (j(SHARED, "comp", "c.c"), j(PROJ, "shared", "comp", "c.h")):
            f = facts(files=facts()["files"] + [p])
            self.assertEqual(S.judge(f, "bench"), [], p)
            self.assertEqual(S.judge(f, "release"), [("shared", p)], p)

    def test_version_mismatch(self):
        self.assertEqual(rules(S.judge(facts(managed={"espressif__mdns": "1.4.1"}), "bench")), ["version"])

    def test_version_missing(self):
        self.assertEqual(rules(S.judge(facts(managed={"espressif__mdns": None}), "release")), ["version"])

    def test_managed_not_in_lock(self):
        self.assertEqual(rules(S.judge(facts(managed={"espressif__mdns": "1.4.0", "x__y": "1.0.0"}), "release")),
                         ["version"])
        self.assertEqual(rules(S.judge(facts(lock=None), "release")), ["version"])

    def test_empty_records_fail(self):
        for kw in ({"files": []}, {"toolchains": {}}, {"cmake_roots": []}):
            self.assertIn("records", rules(S.judge(facts(**kw), "bench")), kw)


class Readers(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def write(self, rel, text):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="") as f:
            f.write(text)
        return p

    def test_toolchains(self):
        self.write("b/CMakeFiles/3.30.2/CMakeCCompiler.cmake",
                   'set(CMAKE_C_COMPILER_ARG1 "")\nset(CMAKE_C_COMPILER "C:/t/tc/bin/gcc.exe")\n')
        self.write("b/CMakeFiles/3.30.2/CMakeCXXCompiler.cmake", 'set(CMAKE_CXX_COMPILER "/t/tc2/bin/g++")\n')
        got = S.toolchains(os.path.join(self.d, "b"))
        self.assertEqual(sorted(got), ["C", "CXX"])
        self.assertEqual(S.norm(got["C"]), S.norm("C:/t/tc"))
        self.assertEqual(S.norm(got["CXX"]), S.norm("/t/tc2"))
        self.assertEqual(S.toolchains(os.path.join(self.d, "none")), {})

    def test_manifest_version(self):
        self.write("m1/idf_component.yml", "dependencies:\n  idf: '>=5.0'\n  version: 9.9.9\nversion: 1.4.0\n")
        self.write("m2/idf_component.yml", "version: \"2.0.1\"  # quoted\n")
        self.write("m3/idf_component.yml", "description: no version\n  version: 3.0.0\n")
        self.assertEqual(S.manifest_version(os.path.join(self.d, "m1")), "1.4.0")
        self.assertEqual(S.manifest_version(os.path.join(self.d, "m2")), "2.0.1")
        self.assertIsNone(S.manifest_version(os.path.join(self.d, "m3")))
        self.assertIsNone(S.manifest_version(os.path.join(self.d, "m4")))

    def test_build_folders(self):
        for rel in ("build/CMakeCache.txt", "build/build.ninja", "build/bootloader/CMakeCache.txt",
                    "build/bootloader/build.ninja", "build/esp-idf/CMakeCache.txt"):
            self.write(rel, "")
        got = [os.path.relpath(p, self.d) for p in S.build_folders(os.path.join(self.d, "build"))]
        self.assertEqual(sorted(got), sorted(["build", os.path.join("build", "bootloader")]))

    def test_shared_roots(self):
        proj = os.path.join(self.d, "proj")
        os.makedirs(os.path.join(self.d, "shared-v1", "shared"))
        os.makedirs(os.path.join(self.d, "not-shared-v1", "shared"))
        subprocess.run(["git", "init", "-q", proj], check=True)
        subprocess.run(["git", "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "-C", proj, "worktree", "add", "-q", "--detach", os.path.join(self.d, "wt2")], check=True)
        got = S.shared_roots(proj)
        want = sorted(S.norm(p) for p in (os.path.join(proj, "shared"), os.path.join(self.d, "wt2", "shared"),
                                           os.path.join(self.d, "shared-v1", "shared")))
        self.assertEqual(got, want)
        self.assertEqual(S.shared_roots(os.path.join(self.d, "nogit")),
                         [S.norm(os.path.join(self.d, "shared-v1", "shared"))])

    def test_shared_roots_project_in_subfolder(self):
        """A project in a subfolder of its worktree: ../shared-<tag>/ is beside the worktree
        root, and a shared-* folder beside the project, inside the worktree, is not one."""
        wt = os.path.join(self.d, "wt")
        proj = os.path.join(wt, "charge-interposer")
        os.makedirs(proj)
        os.makedirs(os.path.join(self.d, "shared-v2", "shared"))
        os.makedirs(os.path.join(wt, "shared-inside", "shared"))
        subprocess.run(["git", "init", "-q", wt], check=True)
        subprocess.run(["git", "-C", wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        got = S.shared_roots(proj)
        self.assertEqual(got, sorted(S.norm(p) for p in (os.path.join(wt, "shared"),
                                                         os.path.join(self.d, "shared-v2", "shared"))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
