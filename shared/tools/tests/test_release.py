"""Tests of the release build (vtrux_build.release): SPEC R8, R9, R10, R11,
R13 and R16's override_path clause, end to end on a scratch git project.

    python -B shared/tools/tests/test_release.py

idf.py is replaced by FakeIdf, which on "reconfigure" downloads a managed
component and writes build/project_description.json, and on "build" writes
the image and its ELF, as a real build would. Each test changes one thing
and checks the refusal and that nothing after it ran. Needs git.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                           # noqa: E402
from imagekit import descriptor, make_image              # noqa: E402
from vtrux_build import cli, record                      # noqa: E402
from vtrux_build.builddir import BuildDir                # noqa: E402
from vtrux_build.image import AppImage                   # noqa: E402
from vtrux_build.release import (Refused, check_compilers,  # noqa: E402
                                 check_no_launcher, cmake_build_folders,
                                 cmake_false, first_word, ninja_runner,
                                 release)
from vtrux_build.toolchain import Toolchain, ToolchainError  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
CHASH = "ab" * 32
COMP_FILES = {"CMakeLists.txt": b"idf_component_register()\n",
              "m.c": b"int m;\n"}
TC_RUN = (lambda argv: (0, "riscv32-esp-elf-gcc (crosstool-NG) 14.2.0\n"))
STRICT = {"IDF_COMPONENT_STRICT_CHECKSUM": "1"}
EXE = ".exe" if os.name == "nt" else ""
TC_VER = "esp-14.2.0_20241119"
TOOLCHAIN_CMAKE = ("set(CMAKE_SYSTEM_NAME Generic)\n\n"
                   "set(CMAKE_C_COMPILER riscv32-esp-elf-gcc)\n"
                   "set(CMAKE_CXX_COMPILER riscv32-esp-elf-g++)\n"
                   "set(CMAKE_ASM_COMPILER riscv32-esp-elf-gcc)\n")


def fake_idf(name, installed=TC_VER):
    """An IDF checkout and its tools folder, in the shape the export gives:
    idf/tools/tools.json and the esp32c3 toolchain file, and under
    espressif/tools/ the riscv32-esp-elf toolchain at `installed` (the
    recommended version unless changed), ccache and ninja. Returns
    (Toolchain, {name: path}): gcc, g++, ccache, the toolchain file."""
    d = scratch.fresh(name)
    idf, tools = os.path.join(d, "idf"), os.path.join(d, "espressif")
    scratch.write(os.path.join(idf, "tools", "tools.json"), json.dumps({
        "version": 2, "tools": [
            {"name": "riscv32-esp-elf",
             "export_paths": [["riscv32-esp-elf", "bin"]],
             "versions": [{"name": TC_VER, "status": "recommended"},
                          {"name": "esp-13.2.0_20240530",
                           "status": "supported"}]},
            {"name": "ccache", "export_paths": [["ccache-4.10.2"]],
             "versions": [{"name": "4.10.2", "status": "recommended"}]},
            {"name": "ninja", "export_paths": [[""]],
             "versions": [{"name": "1.12.1", "status": "recommended"}]}]}))
    tfile = scratch.write(os.path.join(idf, "tools", "cmake",
                                       "toolchain-esp32c3.cmake"),
                          TOOLCHAIN_CMAKE)
    bin_ = os.path.join(tools, "tools", "riscv32-esp-elf", installed,
                        "riscv32-esp-elf", "bin")
    paths = {"toolchain_file": tfile}
    for n in ("gcc", "g++"):
        paths[n] = scratch.write(
            os.path.join(bin_, "riscv32-esp-elf-%s%s" % (n, EXE)), b"real " + n.encode())
    paths["ccache"] = scratch.write(
        os.path.join(tools, "tools", "ccache", "4.10.2", "ccache-4.10.2",
                     "ccache" + EXE), b"ccache 4.10.2")
    scratch.write(os.path.join(tools, "tools", "ninja", "1.12.1",
                               "ninja" + EXE), b"ninja")
    return Toolchain(idf, tools), paths


def cmake_form(path):
    return path.replace("\\", "/")


def ninja_form(path):
    """As the Ninja generator writes a compiler into a command: on Windows
    the same file with backslashes, as a real IDF configure gives."""
    return path.replace("/", "\\") if os.name == "nt" else path


TOOLCHAIN, TC_FILES = fake_idf("fake_idf")
# The compilers CMake chose (CMake<LANG>Compiler.cmake): the toolchain's own.
CC_CMAKE, CXX_CMAKE = cmake_form(TC_FILES["gcc"]), cmake_form(TC_FILES["g++"])


def git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "core.autocrlf=false"]
                          + list(args), check=True, env=GIT_ENV,
                          capture_output=True, text=True).stdout


def lock(extra=""):
    return ("dependencies:\n"
            "  espressif/m:\n"
            "    component_hash: %s\n"
            "    source:\n"
            "      registry_url: https://components.espressif.com/\n"
            "      type: service\n"
            "    version: 1.0.0\n%s"
            "  idf:\n    source:\n      type: idf\n    version: 5.4.1\n"
            "target: esp32c3\nversion: 2.0.0\n" % (CHASH, extra))


GIT_DEP = ("  twai_port:\n"
           "    component_hash: %s\n"
           "    source:\n"
           "      git: https://example.invalid/r.git\n"
           "      path: shared/components/twai_port\n"
           "      type: git\n"
           "    version: 0123456789abcdef0123456789abcdef01234567\n" % CHASH)
LOCAL_DEP = ("  twai_port:\n"
             "    source:\n"
             "      path: C:/w/shared/components/twai_port\n"
             "      type: local\n"
             "    version: '*'\n")
MANIFEST = ('dependencies:\n  espressif/m: "1.0.0"\n'
            '  twai_port:\n    version: "twai-v1"\n'
            '    git: https://example.invalid/r.git\n'
            '    path: shared/components/twai_port\n')


class FakeIdf:
    """The parts of idf.py reconfigure and build the script relies on."""

    def __init__(self, root, cc, edit=None):
        self.root, self.cc, self.edit = root, cc, edit or {}
        self.calls = []
        self.envs = {}
        self.argv = {}

    def __call__(self, args, env):
        action = args[-1]
        self.calls.append(action)
        self.envs[action] = dict(env)
        self.argv[action] = list(args)
        # idf.py's --ccache/--no-ccache: the flag given wins, else
        # IDF_CCACHE_ENABLE; every idf.py call writes it into the cache.
        flags = args[:-1]
        if "--no-ccache" in flags:
            ccache = False
        elif "--ccache" in flags:
            ccache = True
        else:
            ccache = os.environ.get("IDF_CCACHE_ENABLE", "0") not in ("", "0")
        e = self.edit
        if action == "reconfigure":
            if e.get("reconfigure_rc"):
                return e["reconfigure_rc"]
            self._cache(e.get("ccache_value", "1" if ccache else "0"))
            self._generate(os.path.join(self.root, "build"),
                           ccache or e.get("rule_ccache"), e.get("launcher"),
                           not e.get("no_compile_edges"), e.get("compilers"))
            self._component("espressif__m", e.get("component_files", COMP_FILES))
            if e.get("shared_on_disk"):
                # A git download: no CHECKSUMS.json, as the manager gives it.
                self._component("twai_port", COMP_FILES, checksums=False)
            self._description()
            if e.get("write_config_file"):
                # IDF writes the sdkconfig it is given if it is not there.
                scratch.write(e["config_file"], "CONFIG_X=y\n")
            if e.get("dirty_reconfigure"):
                scratch.write(os.path.join(self.root, "sdkconfig"),
                              "CONFIG_X=y\r\n")
            if e.get("commit_during_reconfigure"):
                scratch.write(os.path.join(self.root, "early.txt"), "x")
                git(self.root, "add", "early.txt")
                git(self.root, "commit", "-qm", "early")
            return 0
        if action == "build":
            if flags != ["--no-ccache"] and ccache:
                self._cache("1")    # idf.py reruns CMake with it on
                self._generate(os.path.join(self.root, "build"), True)
            return self._build()
        raise AssertionError(args)

    def _generate(self, b, ccache, launcher=None, compile_edges=True,
                  compilers=None):
        """What CMake's Ninja generator writes into build folder b, in the
        shape IDF's configure gives it: CCACHE_ENABLE puts ccache into each
        compile rule's command (IDF's RULE_LAUNCH_COMPILE), and
        CMAKE_C_COMPILER_LAUNCHER (from the environment, as CMake reads it,
        or launcher) into the C edges' LAUNCHER binding. compilers is
        {language: path} where CMake chose another compiler than the
        toolchain's own (a ccache masquerade first on PATH)."""
        if launcher is None:
            launcher = os.environ.get("CMAKE_C_COMPILER_LAUNCHER")
        e = self.edit
        chosen = {"C": CC_CMAKE, "CXX": CXX_CMAKE, "ASM": CC_CMAKE}
        chosen.update(compilers or {})
        cmk = os.path.join(b, "CMakeFiles", "3.30.2")
        for lang in ("C", "CXX", "ASM"):
            cc = chosen[lang]
            if lang == "C" and e.get("no_c_compiler"):
                continue
            scratch.write(os.path.join(cmk, "CMake%sCompiler.cmake" % lang),
                          'set(CMAKE_%s_COMPILER "%s")\n'
                          'set(CMAKE_%s_COMPILER_ARG1 "")\n' % (lang, cc, lang))
        pre = "ccache " if ccache else ""
        rules = []
        for lang in ("C", "CXX", "ASM"):
            cc = ninja_form(chosen[lang])
            rules.append("rule %s_COMPILER__m_unscanned_\n"
                         "  depfile = $DEP_FILE\n  deps = gcc\n"
                         "  command = %s${LAUNCHER}${CODE_CHECK}%s $DEFINES "
                         "$FLAGS -o $out -c $in\n" % (lang, pre, cc))
        rules.append("rule CUSTOM_COMMAND\n  command = $COMMAND\n")
        scratch.write(os.path.join(b, "CMakeFiles", "rules.ninja"),
                      "\n".join(rules))
        out = ["ninja_required_version = 1.5", "",
               "include CMakeFiles/rules.ninja", ""]
        if compile_edges:
            for lang, src in (("C", "m.c"), ("CXX", "k.cpp"), ("ASM", "s.S")):
                out.append("build m.dir/%s.obj: %s_COMPILER__m_unscanned_ "
                           "C$:/proj/main/%s || order" % (src, lang, src))
                out.append("  DEFINES = -DX=1")
                out.append("  DEP_FILE = m.dir/%s.obj.d" % src)
                out.append("  FLAGS = -O2")
                if lang == "C" and launcher:
                    out.append("  LAUNCHER = %s " % launcher)
                out.append("")
        out += ["build gen.txt: CUSTOM_COMMAND", "  COMMAND = ccache -s", "",
                "build order: phony", ""]
        scratch.write(os.path.join(b, "build.ninja"), "\n".join(out))

    def _cache(self, value):
        """build/CMakeCache.txt, with CCACHE_ENABLE as idf.py defines it
        (value None: no entry)."""
        lines = ["# This is the CMakeCache file.",
                 "CMAKE_GENERATOR:INTERNAL=Ninja",
                 "CMAKE_TOOLCHAIN_FILE:FILEPATH=%s"
                 % self.edit.get("toolchain_file",
                                 cmake_form(TC_FILES["toolchain_file"]))]
        if value is not None:
            lines.append("CCACHE_ENABLE:UNINITIALIZED=%s" % value)
        scratch.write(os.path.join(self.root, "build", "CMakeCache.txt"),
                      "\n".join(lines) + "\n")

    def ninja(self, args, env):
        """The --jobs build step: ninja, in place of idf.py build."""
        self.calls.append("ninja")
        self.envs["ninja"] = dict(env)
        self.ninja_args = list(args)
        return self._build()

    def _build(self):
        e = self.edit
        if e.get("build_rc"):
            return e["build_rc"]
        if e.get("build_turns_ccache_on"):
            self._cache("ON")
        # The bootloader subproject, which the build itself configures.
        boot = os.path.join(self.root, "build", "bootloader")
        scratch.write(os.path.join(boot, "CMakeCache.txt"),
                      "CCACHE_ENABLE:UNINITIALIZED=0\n"
                      "CMAKE_TOOLCHAIN_FILE:FILEPATH=%s\n"
                      % cmake_form(TC_FILES["toolchain_file"]))
        self._generate(boot, False, e.get("bootloader_launcher"),
                       compilers=e.get("bootloader_compilers"))
        elf = b"\x7fELF built from " + git(self.root, "rev-parse",
                                            "HEAD").encode()
        sha = hashlib.sha256(elf).digest()
        data, _ = make_image(desc=descriptor(version="abc1234",
                                             elf_sha256=sha))
        b = os.path.join(self.root, "build")
        if not e.get("no_bin"):
            scratch.write(os.path.join(b, "app.bin"), data)
        scratch.write(os.path.join(b, "app.elf"), e.get("elf_bytes", elf))
        scratch.write(os.path.join(b, "stray_newer.bin"), data)
        if e.get("commit_during_build"):
            scratch.write(os.path.join(self.root, "late.txt"), "x")
            git(self.root, "add", "late.txt")
            git(self.root, "commit", "-qm", "late")
        if e.get("dirty_after"):
            scratch.write(os.path.join(self.root, "sdkconfig"), "changed\n")
        return 0

    def _component(self, name, files, checksums=True):
        cdir = os.path.join(self.root, "managed_components", name)
        for rel, data in files.items():
            scratch.write(os.path.join(cdir, rel), data)
        if checksums:
            scratch.write(os.path.join(cdir, "CHECKSUMS.json"), json.dumps({
                "algorithm": "sha256", "files": [
                    {"path": r, "size": len(d),
                     "hash": hashlib.sha256(d).hexdigest()}
                    for r, d in COMP_FILES.items()]}))
        scratch.write(os.path.join(cdir, ".component_hash"), CHASH)

    def _description(self):
        main = os.path.join(self.root, "main").replace("\\", "/")
        comp = os.path.join(self.root, "managed_components",
                            "espressif__m").replace("\\", "/")
        desc = {
            "app_bin": "app.bin", "app_elf": "app.elf", "c_compiler": self.cc,
            "git_revision": "v5.4.1",
            # IDF's SDKCONFIG build property: <project>/sdkconfig unless
            # -D SDKCONFIG, a preset or the CMakeLists.txt sets another.
            "config_file": self.edit.get(
                "config_file", os.path.join(self.root, "sdkconfig")
            ).replace("\\", "/"),
            "build_components": ["main", "espressif__m", ""],
            "build_component_info": {"main": {"dir": main},
                                     "espressif__m": {"dir": comp}}}
        if self.edit.get("no_config_file"):
            del desc["config_file"]
        scratch.write(os.path.join(self.root, "build",
                                   "project_description.json"),
                      json.dumps(desc))


def project(name, data=None, lock_text=None, manifest=MANIFEST):
    root = os.path.join(scratch.fresh(name), "proj")   # records beside it
    files = {
        "vtrux_build.json": json.dumps(data or {"project": "proj",
                                                "shared_components": []}),
        "CMakeLists.txt": "project(proj)\n",
        "dependencies.lock": lock_text or lock(),
        "main/CMakeLists.txt": "idf_component_register(SRCS m.c)\n",
        "main/m.c": "int main_c;\n",
        "main/idf_component.yml": manifest,
        "sdkconfig": "CONFIG_X=y\n",
        ".gitignore": "build/\nmanaged_components/\n",
    }
    for rel, text in files.items():
        scratch.write(os.path.join(root, *rel.split("/")), text)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    cc = scratch.write(os.path.join(scratch.ROOT, "fake-gcc.exe"), b"")
    return root, cc


def records_dir(root):
    """The record folder for a test project: beside it, inside the
    test's own fresh folder, so no earlier run's records survive."""
    return os.path.join(os.path.dirname(root), "records")


def run(root, cc, edit=None, check_only=False, dest="default", jobs=None):
    idf = FakeIdf(root, cc, edit)
    if dest == "default":
        dest = records_dir(root)
    try:
        p = release(root, check_only=check_only, idf=idf,
                    toolchain_run=TC_RUN, dest=dest, jobs=jobs,
                    ninja=idf.ninja, toolchain=TOOLCHAIN)
        return p, idf, None
    except Refused as e:
        return None, idf, str(e)


@unittest.skipUnless(shutil.which("git"), "needs git")
class Release(unittest.TestCase):

    def assertRefused(self, result, words, calls):
        p, idf, why = result
        self.assertIsNone(p)
        self.assertIsNotNone(why, "not refused")
        for w in words:
            self.assertIn(w, why)
        self.assertEqual(idf.calls, calls)

    def test_records_a_clean_build(self):
        root, cc = project("rel_ok")
        p, idf, why = run(root, cc)
        self.assertIsNone(why)
        self.assertEqual(idf.calls, ["reconfigure", "build"])
        r = record.read(p)
        bin_path = os.path.join(root, "build", "app.bin")
        self.assertEqual(r["image_hash"], AppImage.read(bin_path).image_hash)
        self.assertEqual(r["image"]["built_as"], "app.bin")
        self.assertEqual(r["commit"], git(root, "rev-parse", "HEAD").strip())
        self.assertEqual(sorted(r["components"]), ["espressif__m", "main"])
        self.assertEqual(r["toolchain"]["version"],
                         "riscv32-esp-elf-gcc (crosstool-NG) 14.2.0")
        self.assertEqual(r["idf_version"], {"project": "v5.4.1",
                                            "image": "v5.4.1"})
        self.assertEqual(r["dependencies_lock_sha256_lf"], hashlib.sha256(
            scratch.read(os.path.join(root, "dependencies.lock"))).hexdigest())
        self.assertEqual(sorted(r["tooling"]),
                         ["commit", "digest", "dirty", "tag"])
        self.assertEqual(len(r["tooling"]["digest"]), 64)

    def test_r13_lock_hash_ignores_crlf(self):
        """R13: one lock gives one hash in any checkout. The same lock, in
        one project as LF and in another as CRLF (committed as it is, so
        both worktrees are clean), records one hash: the LF lock's."""
        hashes = []
        for name, crlf in (("rel_r13_lf", False), ("rel_r13_crlf", True)):
            root, cc = project(name)
            lp = os.path.join(root, "dependencies.lock")
            lf = scratch.read(lp)
            self.assertNotIn(b"\r", lf)
            if crlf:
                scratch.write(os.path.join(root, ".gitattributes"),
                              "dependencies.lock -text\n")
                scratch.write(lp, lf.replace(b"\n", b"\r\n"))
                git(root, "add", "-A")
                git(root, "commit", "-qm", "the lock as CRLF")
                self.assertIn(b"\r\n", scratch.read(lp))
            p, _, why = run(root, cc)
            self.assertIsNone(why)
            hashes.append(record.read(p)["dependencies_lock_sha256_lf"])
        self.assertEqual(hashes, [hashlib.sha256(lf).hexdigest()] * 2)

    def test_r11_named_image_not_newest(self):
        root, cc = project("rel_r11")
        p, _, _ = run(root, cc)
        self.assertEqual(record.read(p)["image"]["built_as"], "app.bin")
        root, cc = project("rel_r11_missing")
        self.assertRefused(run(root, cc, {"no_bin": True}),
                           ["app.bin"], ["reconfigure", "build"])

    def test_r8_modified_file(self):
        root, cc = project("rel_r8_mod")
        scratch.write(os.path.join(root, "main", "m.c"), "int edited;\n")
        self.assertRefused(run(root, cc), ["R8", "main/m.c"], [])

    def test_r8_untracked_file(self):
        root, cc = project("rel_r8_new")
        scratch.write(os.path.join(root, "main", "new.c"), "")
        self.assertRefused(run(root, cc), ["R8", "main/new.c"], [])

    def test_r8_ignored_files_are_fine(self):
        root, cc = project("rel_r8_ign")
        scratch.write(os.path.join(root, "build", "old.o"), "")
        self.assertIsNone(run(root, cc)[2])

    def test_r8_staged_change(self):
        root, cc = project("rel_r8_staged")
        scratch.write(os.path.join(root, "main", "m.c"), "int staged;\n")
        git(root, "add", "-A")
        self.assertRefused(run(root, cc), ["R8"], [])

    def test_r10_strict_mode_in_the_reconfigure(self):
        root, cc = project("rel_r10_strict")
        _, idf, why = run(root, cc)
        self.assertIsNone(why)
        self.assertEqual(idf.envs["reconfigure"],
                         {"IDF_COMPONENT_STRICT_CHECKSUM": "1"})
        self.assertEqual(idf.envs["build"],
                         {"IDF_COMPONENT_STRICT_CHECKSUM": "1"})

    def test_r10_overwrite_setting_refused(self):
        """Set at all is refused, "0" too: no reading of the value can let
        it through. --check-only refuses it as well."""
        for value in ("1", "0", ""):
            root, cc = project("rel_r10_overwrite")
            with mock.patch.dict(os.environ, {
                    "IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS": value}):
                self.assertRefused(run(root, cc), [
                    "IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS", "R10"], [])
                self.assertRefused(run(root, cc, check_only=True), [
                    "IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS"], [])

    def test_r10_strict_reconfigure_failure_stops(self):
        """The manager stopping the strict configure (a changed component)
        is a reconfigure failure: nothing is built."""
        root, cc = project("rel_r10_modified")
        self.assertRefused(run(root, cc, {"reconfigure_rc": 1}),
                           ["reconfigure failed"], ["reconfigure"])

    def test_check_only_builds_nothing(self):
        root, cc = project("rel_check")
        p, idf, why = run(root, cc, check_only=True)
        self.assertEqual((p, why, idf.calls), (None, None, []))

    def test_r13_no_reverse_it_dir_stops_first(self):
        root, cc = project("rel_r13")
        env = dict(os.environ)
        env.pop("REVERSE_IT_DIR", None)
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            self.assertRefused(run(root, cc, dest=None), ["REVERSE_IT_DIR"],
                               [])
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_r9_reconfigure_failure(self):
        root, cc = project("rel_r9")
        self.assertRefused(run(root, cc, {"reconfigure_rc": 2}),
                           ["reconfigure"], ["reconfigure"])

    def test_r10_after_the_reconfigure(self):
        root, cc = project("rel_r10")
        bad = dict(COMP_FILES)
        bad["m.c"] = b"int m = 1;\n"
        self.assertRefused(run(root, cc, {"component_files": bad}),
                           ["R10"], ["reconfigure"])

    def test_r10_no_lock_after_the_reconfigure(self):
        # A project that downloads nothing still needs a lock (R10), and
        # the refusal names the rule rather than a missing file.
        root, cc = project("rel_r10_nolock")
        git(root, "rm", "-q", "dependencies.lock")
        git(root, "commit", "-qm", "no lock")
        self.assertRefused(run(root, cc),
                           ["R10", "no dependencies.lock",
                            "component manifest"], ["reconfigure"])

    def test_r10_added_file(self):
        root, cc = project("rel_r10_add")
        extra = dict(COMP_FILES)
        extra["extra.c"] = b""
        self.assertRefused(run(root, cc, {"component_files": extra}),
                           ["R10"], ["reconfigure"])

    def test_r16_shared_from_local_folder(self):
        root, cc = project("rel_r16_local",
                           data={"project": "proj",
                                 "shared_components": ["twai_port"]},
                           lock_text=lock(LOCAL_DEP))
        self.assertRefused(run(root, cc), ["R16", "local folder"],
                           ["reconfigure"])

    def test_r16_override_path_in_manifest(self):
        root, cc = project(
            "rel_r16_override",
            data={"project": "proj", "shared_components": ["twai_port"]},
            lock_text=lock(GIT_DEP),
            manifest=MANIFEST + '    override_path: "../shared/twai_port"\n')
        self.assertRefused(run(root, cc, {"shared_on_disk": True}),
                           ["R16", "override_path"], ["reconfigure"])

    def test_r16_not_pinned(self):
        root, cc = project("rel_r16_unpinned",
                           data={"project": "proj",
                                 "shared_components": ["twai_port"]})
        self.assertRefused(run(root, cc), ["R16", "not pinned"],
                           ["reconfigure"])

    def test_r16_pinned_is_recorded(self):
        root, cc = project("rel_r16_ok",
                           data={"project": "proj",
                                 "shared_components": ["twai_port"]},
                           lock_text=lock(GIT_DEP))
        p, _, why = run(root, cc, {"shared_on_disk": True})
        self.assertIsNone(why)
        self.assertEqual(record.read(p)["shared_components"]["twai_port"], {
            "version": "twai-v1", "git": "https://example.invalid/r.git",
            "path": "shared/components/twai_port",
            "lock_version": "0123456789abcdef0123456789abcdef01234567"})

    def test_build_failure(self):
        root, cc = project("rel_buildfail")
        self.assertRefused(run(root, cc, {"build_rc": 1}), ["build failed"],
                           ["reconfigure", "build"])

    def test_head_moved(self):
        root, cc = project("rel_head")
        self.assertRefused(run(root, cc, {"commit_during_build": True}),
                           ["HEAD moved"], ["reconfigure", "build"])

    def test_elf_not_the_embedded_one(self):
        root, cc = project("rel_elf")
        p, idf, why = run(root, cc, {"elf_bytes": b"\x7fELF other"})
        self.assertIsNone(p)
        self.assertIn("no record written", why)
        dest = records_dir(root)
        self.assertFalse(os.path.isdir(dest) and os.listdir(dest))

    def test_r8_reconfigure_rewrites_a_tracked_file(self):
        """Refused after the reconfigure, before anything is compiled."""
        root, cc = project("rel_r8_reconf")
        self.assertRefused(run(root, cc, {"dirty_reconfigure": True}),
                           ["R8", "after the reconfigure", "sdkconfig"],
                           ["reconfigure"])

    def test_r8_head_moved_during_reconfigure(self):
        root, cc = project("rel_r8_reconf_head")
        self.assertRefused(run(root, cc, {"commit_during_reconfigure": True}),
                           ["HEAD moved", "after the reconfigure"],
                           ["reconfigure"])

    def test_r8_build_leaves_the_tree_changed(self):
        root, cc = project("rel_r8_after")
        self.assertRefused(run(root, cc, {"dirty_after": True}),
                           ["R8", "at the end of the build", "sdkconfig"],
                           ["reconfigure", "build"])
        dest = records_dir(root)
        self.assertFalse(os.path.isdir(dest) and os.listdir(dest))

    # R8: the sdkconfig the build used (project_description.json's
    # config_file) is committed in the project. Every refusal comes after
    # the reconfigure, before anything is compiled.

    def test_r8_sdkconfig_default_path(self):
        root, cc = project("rel_r8_sdk_default")
        p, idf, why = run(root, cc)
        self.assertIsNone(why)
        desc = json.loads(scratch.read(os.path.join(
            root, "build", "project_description.json")))
        self.assertEqual(os.path.normpath(desc["config_file"]),
                         os.path.normpath(os.path.join(root, "sdkconfig")))

    def test_r8_sdkconfig_elsewhere_committed(self):
        """config_file pointing at another committed file in the project
        (-D SDKCONFIG, say) is fine, though <project>/sdkconfig is gone."""
        root, cc = project("rel_r8_sdk_other_ok")
        scratch.write(os.path.join(root, "configs", "sdkconfig.release"),
                      "CONFIG_X=y\n")
        git(root, "rm", "-q", "sdkconfig")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "sdkconfig elsewhere")
        other = os.path.join(root, "configs", "sdkconfig.release")
        p, idf, why = run(root, cc, {"config_file": other})
        self.assertIsNone(why)

    def test_r8_sdkconfig_default_ignored_and_untracked(self):
        """The hole R8's clause closes: an ignored sdkconfig leaves the
        worktree clean whatever it holds."""
        root, cc = project("rel_r8_sdk_ign_untracked")
        scratch.write(os.path.join(root, ".gitignore"),
                      "build/\nmanaged_components/\nsdkconfig\n")
        git(root, "rm", "-q", "--cached", "sdkconfig")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "ignore sdkconfig")
        self.assertEqual(git(root, "status", "--porcelain"), "")
        self.assertRefused(run(root, cc),
                           ["R8", "after the reconfigure", "sdkconfig",
                            "not tracked"], ["reconfigure"])

    def test_r8_sdkconfig_elsewhere_untracked(self):
        """-D SDKCONFIG naming a file that is not there: the reconfigure
        writes it, untracked. Named as the sdkconfig, not only as an
        untracked file."""
        root, cc = project("rel_r8_sdk_other_untracked")
        other = os.path.join(root, "sdkconfig.local")
        self.assertRefused(run(root, cc, {"config_file": other,
                                          "write_config_file": True}),
                           ["R8", "sdkconfig.local", "not tracked"],
                           ["reconfigure"])

    def test_r8_sdkconfig_tracked_but_ignored(self):
        root, cc = project("rel_r8_sdk_tracked_ign")
        scratch.write(os.path.join(root, ".gitignore"),
                      "build/\nmanaged_components/\nsdkconfig\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "ignore the tracked sdkconfig")
        self.assertIn("sdkconfig", git(root, "ls-files"))
        self.assertRefused(run(root, cc),
                           ["R8", "ignore rule", ".gitignore"],
                           ["reconfigure"])

    def test_r8_sdkconfig_ignored_by_info_exclude(self):
        """Any ignore source counts, not only a committed .gitignore."""
        root, cc = project("rel_r8_sdk_exclude")
        scratch.write(os.path.join(root, ".git", "info", "exclude"),
                      "/sdkconfig\n")
        self.assertRefused(run(root, cc),
                           ["R8", "ignore rule", "exclude"], ["reconfigure"])

    def test_r8_sdkconfig_missing(self):
        root, cc = project("rel_r8_sdk_missing")
        self.assertRefused(
            run(root, cc, {"config_file": os.path.join(root, "nope",
                                                        "sdkconfig")}),
            ["R8", "does not exist"], ["reconfigure"])

    def test_r8_sdkconfig_outside_the_project(self):
        """Outside, including a sibling folder whose name starts with the
        project's."""
        for name, where in (("rel_r8_sdk_out", ".."),
                            ("rel_r8_sdk_sibling", os.path.join("..",
                                                                "proj2"))):
            root, cc = project(name)
            other = os.path.normpath(os.path.join(root, where, "sdkconfig"))
            scratch.write(other, "CONFIG_X=y\n")
            self.assertRefused(run(root, cc, {"config_file": other}),
                               ["R8", "outside the project"], ["reconfigure"])

    def test_r8_sdkconfig_not_named(self):
        root, cc = project("rel_r8_sdk_unnamed")
        self.assertRefused(run(root, cc, {"no_config_file": True}),
                           ["R8", "config_file"], ["reconfigure"])

    def test_project_data(self):
        for name, data in (("rel_pd1", {"project": "Gen Inhibit",
                                        "shared_components": []}),
                           ("rel_pd2", {"project": "p"}),
                           ("rel_pd3", {"project": "p",
                                        "shared_components": ["a", "a"]})):
            root, cc = project(name, data=data)
            self.assertRefused(run(root, cc), ["vtrux_build.json"], [])

    def test_jobs_builds_with_ninja(self):
        """--jobs N: the build step is ninja -C build -j N all, in the same
        strict environment, after the strict idf.py reconfigure."""
        for n in (1, 4):
            root, cc = project("rel_jobs_%d" % n)
            p, idf, why = run(root, cc, jobs=n)
            self.assertIsNone(why)
            self.assertEqual(idf.calls, ["reconfigure", "ninja"])
            self.assertEqual(idf.ninja_args, [
                "-C", os.path.join(os.path.abspath(root), "build"),
                "-j", str(n), "all"])
            self.assertEqual(idf.envs["reconfigure"], STRICT)
            self.assertEqual(idf.envs["ninja"], STRICT)
            self.assertEqual(idf.argv["reconfigure"],
                             ["--no-ccache", "reconfigure"])
            self.assertEqual(record.read(p)["image"]["built_as"], "app.bin")

    def test_jobs_build_failure(self):
        root, cc = project("rel_jobs_fail")
        self.assertRefused(run(root, cc, {"build_rc": 1}, jobs=1),
                           ["ninja -j 1 build failed"], ["reconfigure", "ninja"])

    def test_jobs_must_be_a_whole_number(self):
        for bad in (0, -1, "2", 1.0, True):
            root, cc = project("rel_jobs_bad")
            self.assertRefused(run(root, cc, jobs=bad), ["--jobs"], [])

    def test_r9_no_ccache_on_both_idf_calls(self):
        """--no-ccache on the reconfigure and on idf.py build, so it wins
        over IDF_CCACHE_ENABLE, which the IDF export sets."""
        root, cc = project("rel_r9_noccache")
        with mock.patch.dict(os.environ, {"IDF_CCACHE_ENABLE": "1"}):
            p, idf, why = run(root, cc)
        self.assertIsNone(why)
        self.assertEqual(idf.argv, {"reconfigure": ["--no-ccache", "reconfigure"],
                                    "build": ["--no-ccache", "build"]})
        cache = BuildDir(os.path.join(root, "build")).cmake_cache()
        self.assertEqual(cache["CCACHE_ENABLE"][1], "0")

    def test_r9_ccache_on_after_the_reconfigure(self):
        """Refused before the build, on either build path."""
        for jobs in (None, 1):
            for value in ("1", "ON"):
                root, cc = project("rel_r9_on")
                self.assertRefused(
                    run(root, cc, {"ccache_value": value}, jobs=jobs),
                    ["R9", "CCACHE_ENABLE=%s" % value, "after the reconfigure"],
                    ["reconfigure"])

    def test_r9_no_ccache_entry(self):
        for jobs in (None, 1):
            root, cc = project("rel_r9_none")
            self.assertRefused(
                run(root, cc, {"ccache_value": None}, jobs=jobs),
                ["R9", "no CCACHE_ENABLE"], ["reconfigure"])

    def test_r9_off_written_as_off(self):
        root, cc = project("rel_r9_off")
        self.assertIsNone(run(root, cc, {"ccache_value": "OFF"}, jobs=1)[2])

    def test_r9_ccache_on_after_the_build(self):
        """A configure during the build that turns it on: refused, no
        record, on either build path."""
        for jobs, step in ((None, "build"), (1, "ninja")):
            root, cc = project("rel_r9_late")
            self.assertRefused(
                run(root, cc, {"build_turns_ccache_on": True}, jobs=jobs),
                ["R9", "after the build"], ["reconfigure", step])
            dest = records_dir(root)
            self.assertFalse(os.path.isdir(dest) and os.listdir(dest))

    def test_r9_launcher_from_the_environment(self):
        """CMAKE_C_COMPILER_LAUNCHER=ccache launches ccache with
        CCACHE_ENABLE off: refused by its effect, before the build, on
        either build path."""
        for jobs in (None, 1):
            root, cc = project("rel_r9_envlaunch")
            with mock.patch.dict(os.environ,
                                 {"CMAKE_C_COMPILER_LAUNCHER": "ccache"}):
                self.assertRefused(
                    run(root, cc, jobs=jobs),
                    ["R9", "after the reconfigure", "launcher",
                     "1 of the 3 compile commands", "m.c.obj runs ccache"],
                    ["reconfigure"])

    def test_r9_any_launcher(self):
        root, cc = project("rel_r9_sccache")
        self.assertRefused(run(root, cc, {"launcher": "C:/t/sccache.exe"}),
                           ["R9", "runs C:/t/sccache.exe"], ["reconfigure"])

    def test_r9_ccache_in_the_rules_with_the_setting_off(self):
        """The effect is checked, not only CCACHE_ENABLE."""
        root, cc = project("rel_r9_rules")
        self.assertRefused(run(root, cc, {"rule_ccache": True}),
                           ["R9", "3 of the 3 compile commands", "runs ccache"],
                           ["reconfigure"])

    def test_r9_nothing_to_check_is_refused(self):
        for name, edit, words in (
                ("rel_r9_noedges", {"no_compile_edges": True},
                 ["no compile commands"]),
                ("rel_r9_nocc", {"no_c_compiler": True},
                 ["records no C compiler"])):
            root, cc = project(name)
            self.assertRefused(run(root, cc, edit), ["R9"] + words,
                               ["reconfigure"])

    def test_r9_bootloader_launcher_after_the_build(self):
        """The bootloader is configured during the build, so its compile
        commands are checked after it, on either build path; no record."""
        for jobs, step in ((None, "build"), (1, "ninja")):
            root, cc = project("rel_r9_boot")
            self.assertRefused(
                run(root, cc, {"bootloader_launcher": "ccache"}, jobs=jobs),
                ["R9", "after the build", "bootloader", "runs ccache"],
                ["reconfigure", step])
            dest = records_dir(root)
            self.assertFalse(os.path.isdir(dest) and os.listdir(dest))

    def test_r9_clean_build_checks_the_bootloader_too(self):
        root, cc = project("rel_r9_bootok")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            p, _, why = run(root, cc)
        self.assertIsNone(why)
        self.assertIn("after the build, 6 compile commands in build, "
                      + os.path.join("build", "bootloader"), out.getvalue())

    def test_jobs_without_ninja_on_path_stops_first(self):
        root, cc = project("rel_jobs_noninja")
        idf = FakeIdf(root, cc)
        with mock.patch("vtrux_build.release.shutil.which", return_value=None):
            with self.assertRaises(Refused) as cm:
                release(root, idf=idf, toolchain_run=TC_RUN,
                        dest=records_dir(root), jobs=1, toolchain=TOOLCHAIN)
        self.assertIn("ninja", str(cm.exception))
        self.assertEqual(idf.calls, [])

    def test_r9_masquerade_after_the_reconfigure(self):
        """The tester's case: a copy of ccache named after the toolchain's
        compilers, first on PATH, which CMake then chose. Its compile
        commands start with the chosen compiler, so only the positive
        check sees it; nothing is built."""
        root, cc = project("rel_r9_masq")
        d = scratch.fresh("rel_r9_masq_path")
        masq = {}
        for lang, n in (("C", "gcc"), ("CXX", "g++"), ("ASM", "gcc")):
            masq[lang] = cmake_form(os.path.join(d, "riscv32-esp-elf-%s%s"
                                                 % (n, EXE)))
            shutil.copyfile(TC_FILES["ccache"], masq[lang])
        self.assertRefused(run(root, cc, {"compilers": masq}),
                           ["R9", "after the reconfigure", "not this IDF's",
                            "riscv32-esp-elf-gcc", TC_VER],
                           ["reconfigure"])

    def test_r9_masquerade_in_the_bootloader(self):
        """The bootloader is configured during the build: a masquerade
        there is refused after it, on either build path; no record."""
        d = scratch.fresh("rel_r9_bmasq_path")
        gcc = cmake_form(os.path.join(d, "riscv32-esp-elf-gcc" + EXE))
        shutil.copyfile(TC_FILES["ccache"], gcc)
        for jobs, step in ((None, "build"), (1, "ninja")):
            root, cc = project("rel_r9_bmasq")
            self.assertRefused(
                run(root, cc, {"bootloader_compilers": {"C": gcc}}, jobs=jobs),
                ["R9", "after the build", "bootloader", "not this IDF's"],
                ["reconfigure", step])
            dest = records_dir(root)
            self.assertFalse(os.path.isdir(dest) and os.listdir(dest))

    def test_r9_toolchain_file_not_this_idfs(self):
        root, cc = project("rel_r9_tfile")
        other = scratch.write(os.path.join(scratch.fresh("rel_r9_tfile_x"),
                                           "toolchain-esp32c3.cmake"),
                              TOOLCHAIN_CMAKE)
        self.assertRefused(run(root, cc, {"toolchain_file": cmake_form(other)}),
                           ["R9", "not one of this IDF's"], ["reconfigure"])

    def test_r9_no_idf_toolchain_stops_first(self):
        root, cc = project("rel_r9_noidf")
        idf = FakeIdf(root, cc)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IDF_PATH", None)
            with self.assertRaises(Refused) as cm:
                release(root, idf=idf, toolchain_run=TC_RUN,
                        dest=records_dir(root), ninja=idf.ninja)
        self.assertIn("IDF_PATH", str(cm.exception))
        self.assertIn("R9", str(cm.exception))
        self.assertEqual(idf.calls, [])


class CmakeFalse(unittest.TestCase):
    """R9's reading of CCACHE_ENABLE: CMake's if() truth."""

    def test_false(self):
        for v in ("0", "OFF", "off", "No", "FALSE", "n", "IGNORE",
                  "NOTFOUND", "", "CCACHE-NOTFOUND", "0.0", " 0 "):
            self.assertTrue(cmake_false(v), repr(v))

    def test_true(self):
        for v in ("1", "ON", "yes", "TRUE", "Y", "2", "-1", "0.5", "x"):
            self.assertFalse(cmake_false(v), repr(v))


class LauncherCheck(unittest.TestCase):
    """R9's launcher check on hand-written build folders."""

    def folder(self, name, command, compiler='C:/tc/gcc.exe', extra=""):
        b = scratch.fresh(name)
        scratch.write(os.path.join(b, "CMakeFiles", "3.30.2",
                                   "CMakeCCompiler.cmake"),
                      'set(CMAKE_C_COMPILER "%s")\n' % compiler + extra)
        scratch.write(os.path.join(b, "build.ninja"),
                      "rule C_COMPILER__x_\n  command = %s -c $in\n\n"
                      "build a.obj: C_COMPILER__x_ a.c\n" % command)
        return b

    def test_compiler_alone_passes(self):
        for name, cmd, compiler in (
                ("lc_plain", "C:\\tc\\gcc.exe", "C:/tc/gcc.exe"),
                ("lc_quoted", '"C:\\t c\\gcc.exe"', "C:/t c/gcc.exe")):
            with mock.patch("sys.stdout"):
                check_no_launcher(self.folder(name, cmd, compiler), "t")

    def test_anything_in_front_is_refused(self):
        for name, cmd in (("lc_ccache", "ccache C:\\tc\\gcc.exe"),
                          ("lc_env", "C:/x/env.exe C:\\tc\\gcc.exe"),
                          ("lc_other", "C:\\tc\\clang.exe")):
            with self.assertRaises(Refused, msg=name) as cm:
                check_no_launcher(self.folder(name, cmd), "t")
            self.assertIn("1 of the 1 compile commands", str(cm.exception))

    def test_two_compilers_for_one_language(self):
        b = self.folder("lc_two", "C:\\tc\\gcc.exe",
                        extra='set(CMAKE_C_COMPILER "C:/other/gcc.exe")\n')
        with self.assertRaises(Refused) as cm:
            check_no_launcher(b, "t")
        self.assertIn("two C compilers", str(cm.exception))

    def test_first_word(self):
        for cmd, word in (("gcc -c a", "gcc"), ('  "C:/a b/gcc" -c', "C:/a b/gcc"),
                          ("", ""), ("ccache gcc", "ccache")):
            self.assertEqual(first_word(cmd), word)

    def test_cmake_build_folders(self):
        b = scratch.fresh("lc_folders")
        for d in ("bootloader", "deep/sub"):
            scratch.write(os.path.join(b, d, "CMakeCache.txt"), "")
            scratch.write(os.path.join(b, d, "build.ninja"), "")
        scratch.write(os.path.join(b, "half", "build.ninja"), "")
        self.assertEqual(
            [os.path.relpath(p, b) for p in cmake_build_folders(b)],
            [".", "bootloader", os.path.join("deep", "sub")])


class Compilers(unittest.TestCase):
    """R9's positive check: each compiler CMake chose is this IDF's
    toolchain binary (toolchain.py), on hand-written build folders."""

    def folder(self, name, compilers=None, toolchain_file=None):
        """A CMake build folder whose chosen compilers are the toolchain's
        own unless compilers ({language: path}) says otherwise."""
        b = scratch.fresh(name)
        chosen = {"C": TC_FILES["gcc"], "CXX": TC_FILES["g++"],
                  "ASM": TC_FILES["gcc"]}
        chosen.update(compilers or {})
        for lang, cc in chosen.items():
            if cc is None:
                continue
            scratch.write(os.path.join(b, "CMakeFiles", "3.30.2",
                                       "CMake%sCompiler.cmake" % lang),
                          'set(CMAKE_%s_COMPILER "%s")\n' % (lang, cmake_form(cc)))
        scratch.write(os.path.join(b, "CMakeCache.txt"),
                      "CMAKE_TOOLCHAIN_FILE:FILEPATH=%s\n" % cmake_form(
                          toolchain_file or TC_FILES["toolchain_file"]))
        return b

    def refused(self, b, *words, toolchain=TOOLCHAIN):
        with self.assertRaises(Refused) as cm:
            check_compilers(b, "t", toolchain)
        for w in ("R9",) + words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def masq_dir(self, name):
        return scratch.fresh(name)

    def test_the_toolchains_own_pass(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            check_compilers(self.folder("cc_own"), "t", TOOLCHAIN)
        self.assertIn("riscv32-esp-elf %s" % TC_VER, out.getvalue())

    def test_copy_of_ccache_named_after_the_compiler(self):
        """The tester's case: a copy, which no link resolution sees."""
        d = self.masq_dir("cc_copy_path")
        for lang, n in (("C", "gcc"), ("CXX", "g++"), ("ASM", "gcc")):
            gcc = os.path.join(d, "riscv32-esp-elf-%s%s" % (n, EXE))
            shutil.copyfile(TC_FILES["ccache"], gcc)
            self.refused(self.folder("cc_copy", {lang: gcc}),
                         "the %s compiler" % lang, "not this IDF's",
                         "not the toolchain's own")

    def test_hardlink_of_ccache(self):
        d = self.masq_dir("cc_hard_path")
        gcc = os.path.join(d, "riscv32-esp-elf-gcc" + EXE)
        try:
            os.link(TC_FILES["ccache"], gcc)
        except (OSError, NotImplementedError) as e:
            self.skipTest("no hardlinks here (%s)" % e)
        self.assertEqual(os.stat(gcc).st_ino, os.stat(TC_FILES["ccache"]).st_ino)
        self.refused(self.folder("cc_hard", {"C": gcc}), "not this IDF's")

    def test_wrapper_script(self):
        """A script named after the compiler that runs ccache: refused by
        where it is, whether or not it has the compiler's exact name."""
        d = self.masq_dir("cc_wrap_path")
        for n in ("riscv32-esp-elf-gcc", "riscv32-esp-elf-gcc.cmd"):
            w = scratch.write(os.path.join(d, n),
                              "#!/bin/sh\nexec ccache riscv32-esp-elf-gcc \"$@\"\n")
            self.refused(self.folder("cc_wrap", {"C": w}), "not this IDF's")

    def test_symlink_to_ccache(self):
        d = self.masq_dir("cc_sym_path")
        gcc = os.path.join(d, "riscv32-esp-elf-gcc" + EXE)
        try:
            os.symlink(TC_FILES["ccache"], gcc)
        except (OSError, NotImplementedError) as e:
            self.skipTest("no symlinks here (%s)" % e)
        why = self.refused(self.folder("cc_sym", {"C": gcc}), "resolved")
        self.assertIn("ccache", why)

    def test_symlink_to_the_toolchains_own_passes(self):
        """A link elsewhere that resolves to the toolchain's binary runs
        that binary, and is its own."""
        d = self.masq_dir("cc_symok_path")
        gcc = os.path.join(d, "riscv32-esp-elf-gcc" + EXE)
        try:
            os.symlink(TC_FILES["gcc"], gcc)
        except (OSError, NotImplementedError) as e:
            self.skipTest("no symlinks here (%s)" % e)
        with mock.patch("sys.stdout"):
            check_compilers(self.folder("cc_symok", {"C": gcc}), "t",
                            TOOLCHAIN)

    def test_links_resolved_mocked(self):
        """The two symlink cases where links need a privilege (Windows):
        realpath gives what a link would resolve to."""
        d = self.masq_dir("cc_mlink_path")
        gcc = os.path.join(d, "riscv32-esp-elf-gcc" + EXE)
        scratch.write(gcc, b"")
        real = os.path.realpath
        for target, passes in ((TC_FILES["ccache"], False),
                               (TC_FILES["gcc"], True)):
            with mock.patch("vtrux_build.toolchain.os.path.realpath",
                            lambda p: target if p == cmake_form(gcc) else real(p)):
                b = self.folder("cc_mlink", {"C": gcc})
                if passes:
                    with mock.patch("sys.stdout"):
                        check_compilers(b, "t", TOOLCHAIN)
                else:
                    self.assertIn("resolved: %s" % TC_FILES["ccache"],
                                  self.refused(b, "not this IDF's"))

    def test_another_name_in_the_toolchain_folder(self):
        cc = os.path.join(os.path.dirname(TC_FILES["gcc"]),
                          "riscv32-esp-elf-cc" + EXE)
        scratch.write(cc, b"real cc")
        try:
            self.refused(self.folder("cc_name", {"C": cc}),
                         "not the riscv32-esp-elf-gcc the toolchain file names")
        finally:
            os.remove(cc)

    def test_missing_compiler_file(self):
        gone = os.path.join(self.masq_dir("cc_gone_path"),
                            "riscv32-esp-elf-gcc" + EXE)
        self.refused(self.folder("cc_gone", {"C": gone}), "not a file")

    def test_recommended_version_not_installed(self):
        """Only the supported, not the recommended, toolchain is installed:
        not the one this IDF's export uses."""
        tc, files = fake_idf("cc_oldtc", installed="esp-13.2.0_20240530")
        b = self.folder("cc_old", {"C": files["gcc"], "CXX": files["g++"],
                                   "ASM": files["gcc"]},
                        toolchain_file=files["toolchain_file"])
        self.refused(b, "not installed", toolchain=tc)

    def test_compiler_name_in_two_tool_folders(self):
        """A copy dropped into another tool's folder makes the toolchain's
        own folder ambiguous: refused rather than guessed."""
        tc, files = fake_idf("cc_twice")
        extra = os.path.join(os.path.dirname(files["ccache"]),
                             "riscv32-esp-elf-gcc" + EXE)
        shutil.copyfile(files["ccache"], extra)
        b = self.folder("cc_two", {"C": files["gcc"], "CXX": files["g++"],
                                   "ASM": files["gcc"]},
                        toolchain_file=files["toolchain_file"])
        self.refused(b, "2 tool folders", toolchain=tc)

    def test_toolchain_file_outside_the_idf(self):
        other = scratch.write(os.path.join(scratch.fresh("cc_tfile_x"),
                                           "toolchain-esp32c3.cmake"),
                              TOOLCHAIN_CMAKE)
        self.refused(self.folder("cc_tfile", toolchain_file=other),
                     "not one of this IDF's")

    def test_no_toolchain_file_recorded(self):
        b = self.folder("cc_notfile")
        scratch.write(os.path.join(b, "CMakeCache.txt"), "X:STRING=1\n")
        self.refused(b, "no CMAKE_TOOLCHAIN_FILE")

    def test_no_c_compiler_recorded(self):
        self.refused(self.folder("cc_noc", {"C": None}), "records no C compiler")

    def test_language_the_toolchain_file_does_not_name(self):
        tfile = scratch.write(os.path.join(
            os.path.dirname(TC_FILES["toolchain_file"]), "toolchain-noasm.cmake"),
            "set(CMAKE_C_COMPILER riscv32-esp-elf-gcc)\n"
            "set(CMAKE_CXX_COMPILER riscv32-esp-elf-g++)\n")
        try:
            self.refused(self.folder("cc_lang", toolchain_file=tfile),
                         "names no ASM compiler")
        finally:
            os.remove(tfile)

    def test_every_folder_is_checked(self):
        """build/ passes and the bootloader's has the masquerade."""
        good = self.folder("cc_folders")
        d = self.masq_dir("cc_folders_path")
        gcc = os.path.join(d, "riscv32-esp-elf-gcc" + EXE)
        shutil.copyfile(TC_FILES["ccache"], gcc)
        boot = os.path.join(good, "bootloader")
        shutil.copytree(self.folder("cc_folders_boot", {"C": gcc}), boot)
        with self.assertRaises(Refused) as cm:
            with mock.patch("sys.stdout"):
                check_compilers(good, "t", TOOLCHAIN, [good, boot])
        self.assertIn(os.path.join("cc_folders", "bootloader"), str(cm.exception))


class ToolchainFromEnviron(unittest.TestCase):

    def test_tools_path(self):
        idf = TOOLCHAIN.idf_path
        tc = Toolchain.from_environ({"IDF_PATH": idf,
                                     "IDF_TOOLS_PATH": TOOLCHAIN.tools_path})
        self.assertEqual(tc.tools_path, TOOLCHAIN.tools_path)
        tc = Toolchain.from_environ({"IDF_PATH": idf})
        self.assertEqual(tc.tools_path, os.path.abspath(
            os.path.expanduser(os.path.join("~", ".espressif"))))
        self.assertIn(("ccache", "4.10.2", os.path.join(
            tc.tools_path, "tools", "ccache", "4.10.2", "ccache-4.10.2")),
            tc.folders)
        self.assertNotIn("esp-13.2.0_20240530",
                         [v for _, v, _ in tc.folders])

    def test_no_idf(self):
        with self.assertRaises(ToolchainError):
            Toolchain.from_environ({})
        with self.assertRaises(ToolchainError):
            Toolchain(scratch.fresh("tc_noidf"), TOOLCHAIN.tools_path)


class NinjaRunner(unittest.TestCase):
    """The real ninja runner --jobs uses: its command line and environment."""

    def test_command_line_and_environment(self):
        """The strict setting goes to ninja's environment, on top of this
        process's, and not into this process's own."""
        before = os.environ.get("IDF_COMPONENT_STRICT_CHECKSUM")
        with mock.patch("vtrux_build.release.shutil.which",
                        return_value="C:/x/ninja.exe"), \
                mock.patch("vtrux_build.release.subprocess.run") as sp, \
                mock.patch.dict(os.environ, {"VTRUX_TEST_KEEP": "kept"}):
            sp.return_value.returncode = 3
            rc = ninja_runner()(["-C", "b", "-j", "1", "all"], STRICT)
            self.assertEqual(os.environ.get("IDF_COMPONENT_STRICT_CHECKSUM"),
                             before)
        self.assertEqual(rc, 3)
        self.assertEqual(sp.call_args.args[0],
                         ["C:/x/ninja.exe", "-C", "b", "-j", "1", "all"])
        env = sp.call_args.kwargs["env"]
        self.assertEqual(env["IDF_COMPONENT_STRICT_CHECKSUM"], "1")
        self.assertEqual(env["VTRUX_TEST_KEEP"], "kept")

    def test_no_ninja_on_path(self):
        with mock.patch("vtrux_build.release.shutil.which", return_value=None):
            self.assertRaises(Refused, ninja_runner)


class Cli(unittest.TestCase):

    def test_jobs_option(self):
        for argv, jobs in ((["release"], None),
                           (["release", "--jobs", "2"], 2)):
            with mock.patch("vtrux_build.release.release",
                            return_value="r.json") as rel, \
                    mock.patch("sys.stdout"):
                self.assertEqual(cli.main(argv), 0)
            self.assertEqual(rel.call_args.kwargs["jobs"], jobs)

    def test_jobs_not_a_number(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as cm:
            cli.main(["release", "--jobs", "x"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
