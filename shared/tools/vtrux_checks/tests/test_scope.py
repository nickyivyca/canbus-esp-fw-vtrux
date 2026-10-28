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
         "managed": {"espressif__mdns": "1.4.0"}, "pins": {}, "tags": {}, "folders": [j(PROJ, "build")]}
    f.update(kw)
    return f


SHA = "0123456789abcdef0123456789abcdef01234567"
TAG = "2026.10.28"


def git_facts(**kw):
    """facts() with a board taken from git at a tag, as R17 wants it."""
    lock = dict(facts()["lock"], **{"vtrux/b": {"version": SHA, "source": {"type": "git", "path": "shared/boards/b"}}})
    f = facts(lock=lock, managed={"espressif__mdns": "1.4.0", "vtrux__b": None},
              pins={"vtrux/b": [("main/idf_component.yml", TAG)]}, tags={TAG: SHA})
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

    def test_git_faithful(self):
        self.assertEqual(S.judge(git_facts(), "release"), [])
        self.assertEqual(S.judge(git_facts(tags={TAG: SHA.upper()}), "release"), [])

    def test_git_each_fails(self):
        two = [("main/idf_component.yml", TAG), ("components/x/idf_component.yml", "2026.10.29")]
        for label, kw, want in (
                ("own version", {"managed": {"espressif__mdns": "1.4.0", "vtrux__b": "1.0.0"}}, "a version of its own (1.0.0)"),
                ("pinned nowhere", {"pins": {"vtrux/b": []}}, "no idf_component.yml of the project pins vtrux/b"),
                ("pinned twice, apart", {"pins": {"vtrux/b": two}}, "pin vtrux/b at 2 versions"),
                ("not a tag", {"tags": {TAG: None}}, "which is not a tag"),
                ("another commit", {"tags": {TAG: "f" * 40}}, "records commit %s, but tag %s names %s" % (SHA, TAG, "f" * 40))):
            got = S.judge(git_facts(**kw), "release")
            self.assertEqual(rules(got), ["version"], label)
            self.assertEqual(len(got), 1, (label, got))
            self.assertIn(want, got[0][1], label)
            self.assertIn("managed_components/vtrux__b: ", got[0][1], label)

    def test_git_pinned_twice_alike(self):
        same = [("main/idf_component.yml", TAG), ("components/x/idf_component.yml", TAG)]
        self.assertEqual(S.judge(git_facts(pins={"vtrux/b": same}), "release"), [])

    def test_service_clause_unchanged_beside_git(self):
        got = S.judge(git_facts(managed={"espressif__mdns": "1.4.1", "vtrux__b": None}), "release")
        self.assertEqual(got, [("version", "managed_components/espressif__mdns: version 1.4.1, dependencies.lock 1.4.0")])

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

    def test_git_pins(self):
        proj = os.path.join(self.d, "proj")
        subprocess.run(["git", "init", "-q", proj], check=True)
        subprocess.run(["git", "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        head = subprocess.run(["git", "-C", proj, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        subprocess.run(["git", "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", "-m", "t", TAG],
                       check=True)    # annotated: its commit
        subprocess.run(["git", "-C", proj, "branch", "br"], check=True)
        dep = "dependencies:\n  vtrux/b:\n    git: https://example.invalid/r.git\n    path: shared/boards/b\n    version: %s\n"
        self.write("proj/main/idf_component.yml", dep % TAG)
        self.write("proj/components/c/idf_component.yml", dep % "br")
        self.write("proj/components/d/idf_component.yml", "dependencies:\n  espressif/mdns: '1.4.0'\n")
        self.write("proj/managed_components/vtrux__x/idf_component.yml", dep % "9.9.9")   # not the project's
        self.write("outside/idf_component.yml", dep % "8.8.8")                           # not under the root
        comps = {n: os.path.join(self.d, p) for n, p in (
            ("main", "proj/main"), ("c", "proj/components/c"), ("d", "proj/components/d"),
            ("vtrux__x", "proj/managed_components/vtrux__x"), ("o", "outside"), ("none", "proj/components/none"))}
        lock = {"vtrux/b": {"version": head, "source": {"type": "git"}},
                "espressif/mdns": {"version": "1.4.0", "source": {"type": "service"}}}
        pins, tags = S.git_pins(proj, comps, lock)
        self.assertEqual(pins, {"vtrux/b": [("components/c/idf_component.yml", "br"),
                                            ("main/idf_component.yml", TAG)]})
        self.assertEqual(tags, {TAG: head, "br": None})
        self.assertEqual(S.git_pins(proj, comps, None), ({}, {}))
        self.write("proj/main/idf_component.yml", "dependencies:\n  vtrux/b: {version: x}\n")
        with self.assertRaises(S.ScopeError):
            S.git_pins(proj, comps, lock)

    def test_collect_pins_beside_bootloader_main(self):
        """The project's main/ pins the board, and the bootloader's build folder has a main of its
        own, in IDF: collect() reads the pin whichever build folder comes first. A project
        component used only by the bootloader's build pins it too."""
        proj, idf = os.path.join(self.d, "proj"), os.path.join(self.d, "idf")
        subprocess.run(["git", "init", "-q", proj], check=True)
        subprocess.run(["git", "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "-C", proj, "tag", TAG], check=True)
        head = subprocess.run(["git", "-C", proj, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        self.write("proj/dependencies.lock",
                   "dependencies:\n  vtrux/b:\n    source:\n      git: https://example.invalid/r.git\n"
                   "      path: shared/boards/b\n      type: git\n    version: %s\ntarget: chipa\n" % head)
        self.write("proj/managed_components/vtrux__b/idf_component.yml", "description: the board\n")
        pin = "dependencies:\n  vtrux/b:\n    git: https://example.invalid/r.git\n    path: shared/boards/b\n" \
              "    version: \"%s\"\n" % TAG
        app, boot = os.path.join(proj, "build"), os.path.join(proj, "build", "bootloader")
        app_main, boot_main = os.path.join(proj, "main"), os.path.join(idf, "bootloader", "subproject", "main")
        boot_comp = os.path.join(proj, "bootloader_components", "bc")
        self.write("proj/main/idf_component.yml", pin)
        self.write("idf/bootloader/subproject/main/idf_component.yml", "dependencies:\n  idf: '>=5.0'\n")
        mb = {"vtrux__b": os.path.join(proj, "managed_components", "vtrux__b")}

        class Fake:
            comps = {}

            def __init__(self, f):
                self.f = S.norm(f)

            def description(self):
                return {"project_path": proj, "idf_path": idf}

            def files_read(self):
                return [os.path.join(proj, "main", "main.c")]

            def cmake_cache(self):
                return {"CMAKE_ROOT": ("INTERNAL", os.path.join(idf, "cmake"))}

            def components(self):
                return Fake.comps[self.f]

        saved = S.BuildDir, S.build_folders, S.toolchains
        self.addCleanup(lambda: setattr(S, "BuildDir", saved[0]) or setattr(S, "build_folders", saved[1])
                        or setattr(S, "toolchains", saved[2]))
        S.BuildDir, S.toolchains = Fake, lambda f: {"C": os.path.join(idf, "tc")}
        for label, order, comps in (
                ("app first", [app, boot], {app: dict(mb, main=app_main), boot: {"main": boot_main}}),
                ("bootloader first", [boot, app], {app: dict(mb, main=app_main), boot: {"main": boot_main}}),
                ("pin only in the bootloader's build", [app, boot],
                 {app: dict(mb, main=boot_main), boot: {"main": boot_main, "bc": boot_comp}})):
            if label.startswith("pin only"):
                self.write("proj/bootloader_components/bc/idf_component.yml", pin)
            Fake.comps = {S.norm(k): v for k, v in comps.items()}
            S.build_folders = lambda b, o=order: o
            f = S.collect(app)
            want = "bootloader_components/bc/idf_component.yml" if label.startswith("pin only") \
                else "main/idf_component.yml"
            self.assertEqual(f["pins"], {"vtrux/b": [(want, TAG)]}, label)
            self.assertEqual(f["tags"], {TAG: head}, label)
            self.assertEqual([b for b in S.judge(f, "bench") if b[0] == "version"], [], label)

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
