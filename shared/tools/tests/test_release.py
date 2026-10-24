"""Tests of the release build (vtrux_build.release): SPEC R8, R9, R10, R11,
R13 and R16's override_path clause, end to end on a scratch git project.

    python -B shared/tools/tests/test_release.py

idf.py is replaced by FakeIdf, which on "reconfigure" downloads a managed
component and writes build/project_description.json, and on "build" writes
the image and its ELF, as a real build would. Each test changes one thing
and checks the refusal and that nothing after it ran. Needs git.
"""

import hashlib
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
from vtrux_build.release import (Refused, cmake_false,   # noqa: E402
                                 ninja_runner, release)

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
CHASH = "ab" * 32
COMP_FILES = {"CMakeLists.txt": b"idf_component_register()\n",
              "m.c": b"int m;\n"}
TC_RUN = (lambda argv: (0, "riscv32-esp-elf-gcc (crosstool-NG) 14.2.0\n"))
STRICT = {"IDF_COMPONENT_STRICT_CHECKSUM": "1"}


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
            self._component("espressif__m", e.get("component_files", COMP_FILES))
            if e.get("shared_on_disk"):
                # A git download: no CHECKSUMS.json, as the manager gives it.
                self._component("twai_port", COMP_FILES, checksums=False)
            self._description()
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
            return self._build()
        raise AssertionError(args)

    def _cache(self, value):
        """build/CMakeCache.txt, with CCACHE_ENABLE as idf.py defines it
        (value None: no entry)."""
        lines = ["# This is the CMakeCache file.",
                 "CMAKE_GENERATOR:INTERNAL=Ninja"]
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
        scratch.write(os.path.join(self.root, "build",
                                   "project_description.json"), json.dumps({
            "app_bin": "app.bin", "app_elf": "app.elf", "c_compiler": self.cc,
            "git_revision": "v5.4.1",
            "build_components": ["main", "espressif__m", ""],
            "build_component_info": {"main": {"dir": main},
                                     "espressif__m": {"dir": comp}}}))


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
                    ninja=idf.ninja)
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
        self.assertEqual(r["dependencies_lock_sha256"], hashlib.sha256(
            scratch.read(os.path.join(root, "dependencies.lock"))).hexdigest())
        self.assertEqual(sorted(r["tooling"]),
                         ["commit", "digest", "dirty", "tag"])
        self.assertEqual(len(r["tooling"]["digest"]), 64)

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

    def test_jobs_without_ninja_on_path_stops_first(self):
        root, cc = project("rel_jobs_noninja")
        idf = FakeIdf(root, cc)
        with mock.patch("vtrux_build.release.shutil.which", return_value=None):
            with self.assertRaises(Refused) as cm:
                release(root, idf=idf, toolchain_run=TC_RUN,
                        dest=records_dir(root), jobs=1)
        self.assertIn("ninja", str(cm.exception))
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
