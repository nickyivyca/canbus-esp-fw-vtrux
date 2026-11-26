"""Tests of the pins on shared components (vtrux_build.pin): SPEC R23 and
R16's pin clause, for the build tooling's own pin and any other component's,
the entry for a pin move and the `pin` command, on scratch git repositories.

    python -B shared/tools/tests/test_pin.py
    python -B shared/tools/tests/test_pin.py --can-fail

Each project is a git worktree with shared_pins.json committed, and its
pinned checkout is a separate repository at ../shared-<tag>/ (pinkit), except
where a test needs the two in one repository, as the real worktrees are.
Needs git.

--can-fail runs this file against edited copies of vtrux_build/pin.py
(MUTANTS), each a way the R23 check, a component's or the pin command's
could be wrong, and passes only if the
unedited copy passes and every edited one fails. The copy is imported
through VTRUX_BUILD_UNDER_TEST, the folder holding its vtrux_build/.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
UNDER_TEST = os.environ.get("VTRUX_BUILD_UNDER_TEST")
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
if UNDER_TEST:
    # Imported before pinkit, which puts the real shared/tools first.
    sys.path.insert(0, UNDER_TEST)
    import vtrux_build                           # noqa: E402,F401

import pinkit                                    # noqa: E402
import scratch                                   # noqa: E402
from pinkit import TAG, git                      # noqa: E402
from vtrux_build import pin                      # noqa: E402
from vtrux_build.pin import PinError             # noqa: E402


def project(name, pins=None, tag=TAG, sub=None):
    """A worktree <fresh>/proj with its pinned checkout <fresh>/shared-<tag>.
    pins replaces shared_pins.json's text (None: the matching pin). sub puts
    the project root in a subfolder of the worktree. Returns (project root,
    shared/tools folder, the matching pin)."""
    d = scratch.fresh(name)
    tools, good = pinkit.checkout(d, tag)
    top = os.path.join(d, "proj")
    root = os.path.join(top, sub) if sub else top
    scratch.write(os.path.join(root, pin.PIN_FILE),
                  pinkit.pins_text(good) if pins is None else pins)
    git(top, "init", "-q")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "init")
    return root, tools, good


def pins(**entry):
    return json.dumps({"shared/tools": entry})


GOOD_COMMIT, GOOD_DIGEST = "c" * 40, "d" * 64


@unittest.skipUnless(shutil.which("git"), "needs git")
class Read(unittest.TestCase):
    """pin.read: the file and its shared/tools entry."""

    def refused(self, text, *words):
        d = scratch.fresh("pin_read")
        if text is not None:
            scratch.write(os.path.join(d, pin.PIN_FILE), text)
        with self.assertRaises(PinError) as cm:
            pin.read(d)
        for w in words:
            self.assertIn(w, str(cm.exception))

    def test_good(self):
        d = scratch.fresh("pin_read_ok")
        scratch.write(os.path.join(d, pin.PIN_FILE), pinkit.pins_text(
            {"tag": TAG, "commit": GOOD_COMMIT, "digest": GOOD_DIGEST},
            **{"shared/bench": {"anything": 1}}))
        self.assertEqual(pin.read(d), {"tag": TAG, "commit": GOOD_COMMIT,
                                       "digest": GOOD_DIGEST})

    def test_missing_file(self):
        self.refused(None, pin.PIN_FILE, "R23")

    def test_not_json(self):
        self.refused("{", "not valid JSON")

    def test_no_entry(self):
        self.refused(json.dumps({"shared/bench": {}}), "shared/tools", "R23")
        self.refused(json.dumps(["shared/tools"]), "shared/tools", "R23")
        self.refused(json.dumps({"shared/tools": "2026.01.01"}),
                     "shared/tools", "R23")

    def test_r16_no_tag(self):
        for entry in ({"commit": GOOD_COMMIT, "digest": GOOD_DIGEST},
                      {"tag": "", "commit": GOOD_COMMIT, "digest": GOOD_DIGEST},
                      {"tag": "  ", "commit": GOOD_COMMIT, "digest": GOOD_DIGEST},
                      {"tag": None, "commit": GOOD_COMMIT, "digest": GOOD_DIGEST},
                      {"tag": 20261024, "commit": GOOD_COMMIT,
                       "digest": GOOD_DIGEST}):
            self.refused(pins(**entry), "names no tag", "R16")

    def test_r16_tag_is_a_commit(self):
        for tag in ("e40f5cb", "E40F5CB", GOOD_COMMIT, "e40f5cb1234"):
            self.refused(pins(tag=tag, commit=GOOD_COMMIT, digest=GOOD_DIGEST),
                         "commit id", "R16")

    def test_tag_not_a_folder_name(self):
        for tag in ("a/b", "a\\b", " 2026.01.01", "2026.01.01 "):
            self.refused(pins(tag=tag, commit=GOOD_COMMIT, digest=GOOD_DIGEST),
                         "checkout folder")

    def test_bad_commit(self):
        for c in (None, "", "c" * 39, "c" * 41, "C" * 40, "g" * 40, 7):
            self.refused(pins(tag=TAG, commit=c, digest=GOOD_DIGEST),
                         "40 lower-case hex")

    def test_bad_digest(self):
        for d in (None, "", "d" * 63, "d" * 65, "D" * 64, "z" * 64):
            self.refused(pins(tag=TAG, commit=GOOD_COMMIT, digest=d),
                         "64 lower-case hex")


@unittest.skipUnless(shutil.which("git"), "needs git")
class Check(unittest.TestCase):
    """pin.check: R23 against the checkout the tooling runs from."""

    def refused(self, root, tools, *words):
        with self.assertRaises(PinError) as cm:
            pin.check(root, tools)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_good(self):
        root, tools, good = project("pin_ok")
        self.assertEqual(pin.check(root, tools), dict(good, dirty=False))

    def test_project_in_a_subfolder(self):
        """The checkout is beside the worktree's root, not the project's."""
        root, tools, good = project("pin_sub", sub="fw")
        self.assertEqual(pin.check(root, tools), dict(good, dirty=False))

    def test_not_in_a_worktree(self):
        d = scratch.fresh("pin_nogit")
        tools, good = pinkit.checkout(d)
        root = os.path.join(d, "proj")
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(good))
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES":
                                                   os.path.dirname(root)}):
            self.refused(root, tools, "not in a git worktree")

    def test_missing_checkout_prints_the_command(self):
        """The refusal names the command, and running it from the project's
        own repository (which has the tag, as the real worktrees do) makes
        the checkout the check then accepts."""
        d = scratch.fresh("pin_missing")
        top = os.path.join(d, "proj")
        for rel, text in pinkit.TOOL_FILES.items():
            scratch.write(os.path.join(top, "shared", "tools",
                                       *rel.split("/")), text)
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "shared")
        git(top, "tag", TAG)
        good = pinkit.pin_of(os.path.join(top, "shared", "tools"))
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(good))
        git(top, "add", "-A")
        git(top, "commit", "-qm", "pin")
        co = os.path.normpath(os.path.join(d, "shared-" + TAG))
        tools = os.path.join(co, "shared", "tools")
        why = self.refused(top, tools, "does not exist", co,
                           "worktree add --detach ../shared-%s %s" % (TAG, TAG))
        cmd = why.splitlines()[-1].strip()
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        self.assertEqual(pin.check(top, tools), dict(good, dirty=False))
        git(top, "worktree", "remove", "--force", co)

    def test_runs_from_elsewhere(self):
        """A copy of the pinned files elsewhere, with the same digest, is not
        the pinned checkout."""
        root, tools, _ = project("pin_elsewhere")
        other = os.path.join(scratch.fresh("pin_elsewhere_copy"), "tools")
        shutil.copytree(tools, other)
        self.refused(root, other, "runs from", "R23")

    def test_changed_file_refused(self):
        root, tools, _ = project("pin_changed")
        scratch.write(os.path.join(tools, "vtrux_build", "__init__.py"),
                      "x = 2\n")
        self.refused(root, tools, "vtrux_build/__init__.py",
                     "bytes differ", "R23")

    def test_md_change_refused(self):
        """Every tracked file is compared, those R7 leaves out too."""
        root, tools, _ = project("pin_md")
        scratch.write(os.path.join(tools, "README.md"), "edited\n")
        self.refused(root, tools, "README.md", "bytes differ", "R23")

    def test_crlf_rewritten_refused(self):
        """The bytes on disk are compared raw: a file rewritten with CRLF
        differs, whatever git's line-ending settings would say."""
        root, tools, _ = project("pin_crlf")
        co = os.path.dirname(os.path.dirname(tools))
        git(co, "config", "core.autocrlf", "true")
        scratch.write(os.path.join(tools, "vtrux_build", "__init__.py"),
                      b"x = 1\r\n")
        self.refused(root, tools, "vtrux_build/__init__.py",
                     "line endings differ", "R23")

    def test_missing_file_refused(self):
        root, tools, _ = project("pin_missing_file")
        os.remove(os.path.join(tools, "vtrux_build", "__init__.py"))
        self.refused(root, tools, "vtrux_build/__init__.py: missing", "R23")

    @unittest.skipIf(os.name == "nt", "no execute bit on Windows")
    def test_executable_bit_flipped_refused(self):
        root, tools, _ = project("pin_exec")
        f = os.path.join(tools, "vtrux_build", "__init__.py")
        os.chmod(f, os.stat(f).st_mode | stat.S_IXUSR)
        self.refused(root, tools, "vtrux_build/__init__.py",
                     "executable on disk", "R23")

    @unittest.skipIf(os.name == "nt", "no execute bit on Windows")
    def test_executable_file_cleared_refused(self):
        """A file git records as executable must have the bit on disk."""
        files = dict(pinkit.TOOL_FILES, **{"run.sh": "echo\n"})
        d = scratch.fresh("pin_exec_cleared")
        co = os.path.join(d, "shared-" + TAG)
        tools = os.path.join(co, "shared", "tools")
        for rel, text in files.items():
            scratch.write(os.path.join(tools, *rel.split("/")), text)
        scratch.write(os.path.join(co, ".gitignore"), pinkit.ROOT_GITIGNORE)
        os.chmod(os.path.join(tools, "run.sh"), 0o755)
        git(co, "init", "-q")
        git(co, "add", "-A")
        git(co, "commit", "-qm", "shared")
        git(co, "tag", TAG)
        good = pinkit.pin_of(tools)
        top = os.path.join(d, "proj")
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(good))
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "init")
        self.assertEqual(pin.check(top, tools), dict(good, dirty=False))
        os.chmod(os.path.join(tools, "run.sh"), 0o644)
        self.refused(top, tools, "run.sh", "not executable on disk", "R23")

    def test_read_only_passes(self):
        """The mode compared is git's, not the permission bits: the whole
        component made read-only (chmod -R a-w) still passes."""
        root, tools, good = project("pin_read_only")
        made = []
        try:
            for dirpath, dirs, files in os.walk(tools, topdown=False):
                for p in [os.path.join(dirpath, n) for n in files] + [dirpath]:
                    os.chmod(p, os.stat(p).st_mode & ~0o222)
                    made.append(p)
            self.assertEqual(pin.check(root, tools), dict(good, dirty=False))
        finally:
            for p in reversed(made):
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)

    def test_pycache_passes(self):
        """Bytecode beside the code passes: the checkout's committed root
        .gitignore lists __pycache__/ and *.pyc, as shared/main's does."""
        root, tools, good = project("pin_pycache")
        scratch.write(os.path.join(tools, "vtrux_build", "__pycache__",
                                   "__init__.cpython-313.pyc"), b"\0")
        scratch.write(os.path.join(tools, "stray.pyc"), b"\0")
        self.assertEqual(pin.check(root, tools), dict(good, dirty=False))

    def test_pycache_refused_without_the_gitignore(self):
        """Only a committed .gitignore hides a file: without one, the same
        bytecode is an extra file."""
        d = scratch.fresh("pin_pycache_noignore")
        tools, good = pinkit.checkout(d, gitignore=None)
        top = os.path.join(d, "proj")
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(good))
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "init")
        scratch.write(os.path.join(tools, "vtrux_build", "__pycache__",
                                   "__init__.cpython-313.pyc"), b"\0")
        self.refused(top, tools, "__init__.cpython-313.pyc",
                     "no committed .gitignore lists it", "R23")

    def test_untracked_file_refused(self):
        root, tools, _ = project("pin_untracked")
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        self.refused(root, tools, "vtrux_build/extra.py",
                     "no committed .gitignore lists it", "R23")

    def test_staged_file_refused(self):
        """A file staged in the checkout's index but not in the commit:
        git ls-files --others does not list it."""
        root, tools, _ = project("pin_staged")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        git(co, "add", "shared/tools/vtrux_build/extra.py")
        self.assertEqual(git(co, "ls-files", "--others", "--",
                             "shared/tools"), "",
                         "the setup: --others does not list the file")
        self.refused(root, tools, "vtrux_build/extra.py",
                     "staged in the checkout's index", "R23")

    def test_staged_ignored_file_refused(self):
        """A file a committed .gitignore lists, staged with git add -f: once
        in the index it is not the ignored bytecode the rule lets pass."""
        root, tools, _ = project("pin_staged_ignored")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(tools, "vtrux_build", "__pycache__",
                                   "x.cpython-313.pyc"), b"\0")
        git(co, "add", "-f", "shared/tools/vtrux_build/__pycache__")
        self.refused(root, tools, "x.cpython-313.pyc",
                     "staged in the checkout's index", "R23")

    def test_hidden_by_core_excludesfile_refused(self):
        root, tools, _ = project("pin_excludesfile")
        co = os.path.dirname(os.path.dirname(tools))
        excludes = scratch.write(os.path.join(os.path.dirname(co),
                                              "excludes"), "extra.py\n")
        git(co, "config", "core.excludesFile", excludes)
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        self.assertEqual(git(co, "status", "--porcelain"), "",
                         "the setup: git itself hides the file")
        self.refused(root, tools, "vtrux_build/extra.py", "R23")

    def test_hidden_by_info_exclude_refused(self):
        root, tools, _ = project("pin_info_exclude")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(co, ".git", "info", "exclude"),
                      "extra.py\n")
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        self.assertEqual(git(co, "status", "--porcelain"), "",
                         "the setup: git itself hides the file")
        self.refused(root, tools, "vtrux_build/extra.py", "R23")

    def test_uncommitted_gitignore_hiding_itself_refused(self):
        """A .gitignore that is not the commit's cannot hide files, even
        when it lists itself."""
        root, tools, _ = project("pin_self_ignore")
        scratch.write(os.path.join(tools, "vtrux_build", ".gitignore"), "*\n")
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        self.refused(root, tools, "vtrux_build/.gitignore",
                     "not in the commit", "R23")

    def test_changed_root_gitignore_refused(self):
        """The .gitignore above the component counts only as committed."""
        root, tools, _ = project("pin_root_ignore")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(co, ".gitignore"),
                      pinkit.ROOT_GITIGNORE + "extra.py\n")
        scratch.write(os.path.join(tools, "vtrux_build", "extra.py"), "y\n")
        self.refused(root, tools, ".gitignore", "differs from the commit's",
                     "R23")

    def test_pin_digest_not_the_commits_refused(self):
        """HEAD and the tag match the pin and the disk is clean, but the
        pin's digest is not R7 of that commit."""
        root, tools, good = project("pin_digest")
        bad = dict(good, digest="e" * 64)
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(bad))
        self.refused(root, tools, "digest", good["digest"], "R23")

    def test_git_digest_is_r7(self):
        """git_digest gives digest.component_digest's value for the same
        files: nested folders, .md and .gitignore files left out, a
        .markdown counted."""
        files = {"vtrux_build/__init__.py": "x = 1\n", "README.md": "t\n",
                 "a/b/c.txt": b"\x00\x01", "a/.gitignore": "z\n",
                 "notes.markdown": "m\n", "README.MD": "M\n"}
        d = scratch.fresh("pin_git_digest")
        tools, good = pinkit.checkout(d, files=files)
        co = os.path.dirname(os.path.dirname(tools))
        self.assertEqual(pin.git_digest(co, good["commit"]), good["digest"])

    @unittest.skipIf(os.name == "nt", "symbolic links need privileges")
    def test_symlink(self):
        """A committed symbolic link to a file passes, and R7 reads through
        it, from git as from the disk; pointed elsewhere it is refused."""
        d = scratch.fresh("pin_symlink")
        co = os.path.join(d, "shared-" + TAG)
        tools = os.path.join(co, "shared", "tools")
        for rel, text in dict(pinkit.TOOL_FILES, **{"other.py": "o\n"}).items():
            scratch.write(os.path.join(tools, *rel.split("/")), text)
        scratch.write(os.path.join(co, ".gitignore"), pinkit.ROOT_GITIGNORE)
        os.symlink("vtrux_build/__init__.py", os.path.join(tools, "link.py"))
        git(co, "init", "-q")
        git(co, "add", "-A")
        git(co, "commit", "-qm", "shared")
        git(co, "tag", TAG)
        good = pinkit.pin_of(tools)
        self.assertEqual(pin.git_digest(co, good["commit"]), good["digest"])
        top = os.path.join(d, "proj")
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(good))
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "init")
        self.assertEqual(pin.check(top, tools), dict(good, dirty=False))
        os.remove(os.path.join(tools, "link.py"))
        os.symlink("other.py", os.path.join(tools, "link.py"))
        self.refused(top, tools, "link.py", "symbolic link to", "R23")

    @unittest.skipUnless(UNDER_TEST, "only under --can-fail")
    def test_the_copy_under_test_is_imported(self):
        self.assertTrue(os.path.realpath(pin.__file__).startswith(
            os.path.realpath(UNDER_TEST)), pin.__file__)

    def test_head_differs(self):
        """A later commit that changes only a .md file keeps the digest, and
        the checkout's HEAD is still not the pin's commit."""
        root, tools, _ = project("pin_head")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(tools, "README.md"), "edited\n")
        git(co, "commit", "-qam", "md")
        self.refused(root, tools, "is at", "R23")

    def test_r16_pin_tag_is_a_branch(self):
        """A checkout folder named after a branch, at the pin's commit and
        digest: the pin names no tag the repository has."""
        d = scratch.fresh("pin_branch")
        tools, good = pinkit.checkout(d, tag="release")
        co = os.path.dirname(os.path.dirname(tools))
        git(co, "tag", "-d", "release")
        git(co, "branch", "release")
        top = os.path.join(d, "proj")
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(good))
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "init")
        self.refused(top, tools, "no tag of that name", "R16")

    def test_tag_marks_another_commit(self):
        """HEAD is the pin's commit and the digest matches, but the tag was
        cut on another commit."""
        root, tools, good = project("pin_tag_moved")
        co = os.path.dirname(os.path.dirname(tools))
        scratch.write(os.path.join(tools, "README.md"), "edited\n")
        git(co, "commit", "-qam", "md")
        moved = dict(good, commit=git(co, "rev-parse", "HEAD"))
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(moved))
        self.refused(root, tools, "marks", good["commit"], "R23")

    def test_annotated_tag(self):
        root, tools, good = project("pin_annotated")
        co = os.path.dirname(os.path.dirname(tools))
        git(co, "tag", "-d", TAG)
        git(co, "tag", "-a", "-m", "annotated", TAG)
        self.assertEqual(pin.check(root, tools), dict(good, dirty=False))


BENCH = "shared/bench"
BENCH_FILES = {"vtrux_bench/__init__.py": "b = 1\n", "README.md": "bench\n"}


def two_tags(name, bench_tag="2026.02.02"):
    """A worktree <fresh>/proj pinning shared/tools at TAG and shared/bench at
    bench_tag, each in its own checkout, the bench checkout also holding a
    shared/tools that differs from TAG's. Returns (project root, tools
    folder, bench folder, {component: pin})."""
    d = scratch.fresh(name)
    tools, tools_pin = pinkit.checkout(d)
    _, _ = pinkit.checkout(d, bench_tag, files={"vtrux_build/__init__.py":
                                                "x = 99\n"},
                           others={BENCH: BENCH_FILES})
    bench = os.path.join(d, "shared-" + bench_tag, "shared", "bench")
    bench_pin = pinkit.pin_of(bench, bench_tag)
    top = os.path.join(d, "proj")
    scratch.write(os.path.join(top, pin.PIN_FILE),
                  pinkit.pins_text(tools_pin, **{BENCH: bench_pin}))
    git(top, "init", "-q")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "init")
    return top, tools, bench, {pin.COMPONENT: tools_pin, BENCH: bench_pin}


class ComponentName(unittest.TestCase):
    """pin.component_name: a folder below shared/, written with /."""

    def test_good(self):
        for name in ("shared/tools", "shared/bench", "shared/twai_port",
                     "shared/x2_test_images", "shared/a.b-c",
                     "shared/boards/wican_v3", "shared/a/b/c"):
            self.assertEqual(pin.component_name(name), name)

    def test_refused(self):
        for name in ("bench", "shared", "shared/", "shared/..", "shared/a/..",
                     "shared/.", "shared/.hidden", "/shared/bench",
                     "shared/boards/", "shared//bench", "shared/a/./b",
                     "shared\\bench", "shared/bench ", "Shared/bench", None, 7,
                     "../shared/bench"):
            with self.assertRaises(PinError, msg=repr(name)) as cm:
                pin.component_name(name)
            self.assertIn("a folder below shared/", str(cm.exception))


@unittest.skipUnless(shutil.which("git"), "needs git")
class Component(unittest.TestCase):
    """pin.check_component: R23 for any pinned component, in its own tag's
    checkout."""

    def refused(self, root, comp, *words):
        with self.assertRaises(PinError) as cm:
            pin.check_component(root, comp)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_good_at_its_own_tag(self):
        root, tools, bench, pins_ = two_tags("comp_ok")
        got = pin.check_component(root, BENCH)
        self.assertEqual(got, dict(pins_[BENCH], dirty=False, folder=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(bench))),
            "shared-2026.02.02", "shared", "bench")))
        self.assertTrue(pin._same(got["folder"], bench))
        # shared/tools is checked in TAG's checkout, not the bench's.
        self.assertTrue(pin._same(pin.check_component(root, pin.COMPONENT)
                                  ["folder"], tools))
        self.assertEqual(pin.check(root, tools),
                         dict(pins_[pin.COMPONENT], dirty=False))

    def test_read_other_component(self):
        root, _, _, pins_ = two_tags("comp_read")
        self.assertEqual(pin.read(root, BENCH), pins_[BENCH])
        self.assertEqual(pin.read(root), pins_[pin.COMPONENT])

    def test_not_pinned(self):
        root, _, _, _ = two_tags("comp_unpinned")
        self.refused(root, "shared/diag", "no 'shared/diag' entry",
                     "the project's shared/diag is not pinned", "R23")

    def test_bad_name(self):
        root, _, _, _ = two_tags("comp_badname")
        self.refused(root, "shared/bench/", "a folder below")
        self.refused(root, "bench", "a folder below")

    def test_bad_entry(self):
        root, _, _, pins_ = two_tags("comp_badentry")
        for bad, words in (({"tag": "e40f5cb"}, ("commit id", "R16")),
                           ({"tag": ""}, ("names no tag", "R16")),
                           ({"tag": "a/b"}, ("checkout folder",)),
                           ({"commit": "c" * 39}, ("40 lower-case hex",)),
                           ({"digest": "D" * 64}, ("64 lower-case hex",))):
            scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(
                pins_[pin.COMPONENT], **{BENCH: dict(pins_[BENCH], **bad)}))
            self.refused(root, BENCH, BENCH, *words)

    def test_missing_checkout_prints_the_command(self):
        root, _, bench, pins_ = two_tags("comp_nocheckout")
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(
            pins_[pin.COMPONENT], **{BENCH: dict(pins_[BENCH],
                                                 tag="2026.03.03")}))
        self.refused(root, BENCH, "does not exist",
                     "worktree add --detach ../shared-2026.03.03 2026.03.03")

    def test_component_not_in_the_commit(self):
        """The pin's tag's checkout has no such folder."""
        root, _, _, pins_ = two_tags("comp_nofolder")
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(
            pins_[pin.COMPONENT], **{"shared/diag": dict(pins_[BENCH])}))
        self.refused(root, "shared/diag", "no folder shared/diag", "R23")

    def test_digest_is_the_components(self):
        """The bench pin carrying shared/tools' digest is refused: the digest
        is R7 of the named component."""
        root, _, _, pins_ = two_tags("comp_digest")
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(
            pins_[pin.COMPONENT], **{BENCH: dict(
                pins_[BENCH], digest=pins_[pin.COMPONENT]["digest"])}))
        self.refused(root, BENCH, "digest", pins_[BENCH]["digest"], "R23")

    def test_head_differs(self):
        root, _, bench, _ = two_tags("comp_head")
        co = os.path.dirname(os.path.dirname(bench))
        scratch.write(os.path.join(bench, "README.md"), "edited\n")
        git(co, "commit", "-qam", "md")
        self.refused(root, BENCH, "is at", "'shared/bench' pin", "R23")

    def test_tag_marks_another_commit(self):
        root, _, bench, pins_ = two_tags("comp_tagmoved")
        co = os.path.dirname(os.path.dirname(bench))
        scratch.write(os.path.join(bench, "README.md"), "edited\n")
        git(co, "commit", "-qam", "md")
        moved = dict(pins_[BENCH], commit=git(co, "rev-parse", "HEAD"))
        scratch.write(os.path.join(root, pin.PIN_FILE), pinkit.pins_text(
            pins_[pin.COMPONENT], **{BENCH: moved}))
        self.refused(root, BENCH, "marks", pins_[BENCH]["commit"], "R23")

    def test_tag_is_a_branch(self):
        root, _, bench, _ = two_tags("comp_branch")
        co = os.path.dirname(os.path.dirname(bench))
        git(co, "tag", "-d", "2026.02.02")
        git(co, "branch", "2026.02.02")
        self.refused(root, BENCH, "no tag of that name", "R16")

    def test_changed_file_refused(self):
        root, _, bench, _ = two_tags("comp_changed")
        scratch.write(os.path.join(bench, "vtrux_bench", "__init__.py"),
                      "b = 2\n")
        self.refused(root, BENCH, "vtrux_bench/__init__.py", "bytes differ",
                     "R23")

    def test_missing_file_refused(self):
        root, _, bench, _ = two_tags("comp_missing")
        os.remove(os.path.join(bench, "README.md"))
        self.refused(root, BENCH, "README.md: missing", "R23")

    def test_untracked_file_refused(self):
        root, _, bench, _ = two_tags("comp_untracked")
        scratch.write(os.path.join(bench, "extra.py"), "y\n")
        self.refused(root, BENCH, "extra.py",
                     "no committed .gitignore lists it", "R23")

    def test_staged_file_refused(self):
        root, _, bench, _ = two_tags("comp_staged")
        co = os.path.dirname(os.path.dirname(bench))
        scratch.write(os.path.join(bench, "extra.py"), "y\n")
        git(co, "add", "shared/bench/extra.py")
        self.refused(root, BENCH, "extra.py", "staged in the checkout's index")

    def test_pycache_passes(self):
        root, _, bench, pins_ = two_tags("comp_pycache")
        scratch.write(os.path.join(bench, "vtrux_bench", "__pycache__",
                                   "__init__.cpython-313.pyc"), b"\0")
        self.assertEqual(pin.check_component(root, BENCH)["digest"],
                         pins_[BENCH]["digest"])

    def test_nested_component(self):
        """A board's folder, two below shared/, is pinned and checked like
        any other; the board beside it is not checked."""
        d = scratch.fresh("comp_nested")
        tools, tools_pin = pinkit.checkout(d, others={
            "shared/boards/wican_v3": {"board.json": "{}\n"},
            "shared/boards/x2": {"board.json": "{\"x\": 2}\n"}})
        co = os.path.dirname(os.path.dirname(tools))
        board = os.path.join(co, "shared", "boards", "wican_v3")
        board_pin = pinkit.pin_of(board)
        self.assertEqual(board_pin["commit"], tools_pin["commit"])
        top = os.path.join(d, "proj")
        scratch.write(os.path.join(top, pin.PIN_FILE), pinkit.pins_text(
            tools_pin, **{"shared/boards/wican_v3": board_pin}))
        git(top, "init", "-q")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "init")
        got = pin.check_component(top, "shared/boards/wican_v3")
        self.assertTrue(pin._same(got["folder"], board))
        self.assertEqual(got["digest"], board_pin["digest"])
        self.assertEqual(pin.entry(top, TAG, "shared/boards/wican_v3"),
                         board_pin)
        scratch.write(os.path.join(co, "shared", "boards", "x2", "extra"), "y\n")
        pin.check_component(top, "shared/boards/wican_v3")
        scratch.write(os.path.join(board, "extra"), "y\n")
        self.refused(top, "shared/boards/wican_v3", "extra",
                     "no committed .gitignore lists it")
        os.remove(os.path.join(board, "extra"))
        scratch.write(os.path.join(co, "shared", "boards", ".gitignore"),
                      "extra\n")
        scratch.write(os.path.join(board, "extra"), "y\n")
        self.refused(top, "shared/boards/wican_v3", "shared/boards/.gitignore",
                     "not in the commit")

    def test_other_components_not_checked(self):
        """Only the named component's folder counts: a changed shared/tools
        in the bench's checkout does not refuse the bench."""
        root, _, bench, pins_ = two_tags("comp_only")
        co = os.path.dirname(os.path.dirname(bench))
        scratch.write(os.path.join(co, "shared", "tools", "vtrux_build",
                                   "__init__.py"), "x = 100\n")
        scratch.write(os.path.join(co, "shared", "tools", "extra.py"), "y\n")
        self.assertEqual(pin.check_component(root, BENCH)["commit"],
                         pins_[BENCH]["commit"])

    def test_check_still_requires_running_from_the_checkout(self):
        root, tools, bench, _ = two_tags("comp_runsfrom")
        self.assertIn("runs from", self._refused_check(root, bench))

    def _refused_check(self, root, tools):
        with self.assertRaises(PinError) as cm:
            pin.check(root, tools)
        return str(cm.exception)


@unittest.skipUnless(shutil.which("git"), "needs git")
class Entry(unittest.TestCase):
    """pin.entry: the entry a checkout gives, for a pin move."""

    def refused(self, root, tag, comp, *words):
        with self.assertRaises(PinError) as cm:
            pin.entry(root, tag, comp)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_good(self):
        root, _, _, pins_ = two_tags("entry_ok")
        self.assertEqual(pin.entry(root, "2026.02.02", BENCH), pins_[BENCH])
        self.assertEqual(pin.entry(root, TAG, pin.COMPONENT),
                         pins_[pin.COMPONENT])

    def test_digest_from_git_not_the_disk(self):
        root, _, bench, pins_ = two_tags("entry_git")
        scratch.write(os.path.join(bench, "vtrux_bench", "__init__.py"),
                      "b = 3\n")
        self.assertEqual(pin.entry(root, "2026.02.02", BENCH), pins_[BENCH])

    def test_writes_nothing(self):
        root, _, _, _ = two_tags("entry_nowrite")
        path = os.path.join(root, pin.PIN_FILE)
        with open(path, "rb") as f:
            before = f.read()
        pin.entry(root, "2026.02.02", BENCH)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_missing_checkout_prints_the_command(self):
        root, _, _, _ = two_tags("entry_nocheckout")
        self.refused(root, "2026.04.04", BENCH, "does not exist",
                     "worktree add --detach ../shared-2026.04.04 2026.04.04")

    def test_head_not_the_tags_commit(self):
        root, _, bench, _ = two_tags("entry_head")
        co = os.path.dirname(os.path.dirname(bench))
        scratch.write(os.path.join(bench, "README.md"), "edited\n")
        git(co, "commit", "-qam", "md")
        self.refused(root, "2026.02.02", BENCH, "is at", "marks", "R23")

    def test_no_such_tag(self):
        root, _, bench, _ = two_tags("entry_notag")
        co = os.path.dirname(os.path.dirname(bench))
        git(co, "tag", "-d", "2026.02.02")
        self.refused(root, "2026.02.02", BENCH, "no tag", "R16")

    def test_bad_tag_or_name(self):
        root, _, _, _ = two_tags("entry_bad")
        self.refused(root, "e40f5cb", BENCH, "commit id", "R16")
        self.refused(root, "", BENCH, "names no tag", "R16")
        self.refused(root, "a/b", BENCH, "checkout folder")
        self.refused(root, "2026.02.02", "bench", "a folder below")

    def test_component_not_in_the_commit(self):
        root, _, _, _ = two_tags("entry_nofolder")
        self.refused(root, TAG, BENCH, "no folder shared/bench", "R23")


@unittest.skipUnless(shutil.which("git"), "needs git")
class Cli(unittest.TestCase):
    """The `pin` command (pin.main through cli.main): the tooling's own
    check first, then each component."""

    def run_cli(self, argv, tools_root):
        import io
        from vtrux_build import cli
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pin, "TOOLS_ROOT", tools_root), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def test_check(self):
        root, tools, bench, _ = two_tags("cli_check")
        rc, out, err = self.run_cli(["pin", "check", BENCH, pin.COMPONENT,
                                     "--project", root], tools)
        self.assertEqual((rc, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("OK: ") and
                        lines[0].endswith(" is the pinned shared/bench at "
                                          "2026.02.02"), lines[0])
        self.assertTrue(lines[1].endswith(" is the pinned shared/tools at "
                                          + TAG), lines[1])

    def test_where(self):
        root, tools, bench, _ = two_tags("cli_where")
        rc, out, _ = self.run_cli(["pin", "where", BENCH, "--project", root],
                                  tools)
        self.assertEqual(rc, 0)
        self.assertTrue(pin._same(out.strip(), bench), out)

    def test_entry(self):
        root, tools, _, pins_ = two_tags("cli_entry")
        rc, out, _ = self.run_cli(["pin", "entry", "2026.02.02", BENCH,
                                   "--project", root], tools)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {BENCH: pins_[BENCH]})

    def test_tooling_not_the_pinned_refuses_every_action(self):
        """Run from a copy of the pinned tooling, nothing is checked or
        written: the tooling's own check comes first."""
        root, tools, _, _ = two_tags("cli_selfcheck")
        other = os.path.join(scratch.fresh("cli_selfcheck_copy"), "tools")
        shutil.copytree(tools, other)
        for argv in (["pin", "check", BENCH], ["pin", "where", BENCH],
                     ["pin", "entry", "2026.02.02", BENCH]):
            rc, out, err = self.run_cli(argv + ["--project", root], other)
            self.assertEqual((rc, out), (1, ""), argv)
            self.assertIn("REFUSED:", err)
            self.assertIn("runs from", err)

    def test_tooling_changed_refuses(self):
        root, tools, _, _ = two_tags("cli_toolschanged")
        scratch.write(os.path.join(tools, "vtrux_build", "__init__.py"),
                      "x = 2\n")
        rc, out, err = self.run_cli(["pin", "check", BENCH, "--project", root],
                                    tools)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("vtrux_build/__init__.py", err)

    def test_component_refused(self):
        root, tools, bench, _ = two_tags("cli_refused")
        scratch.write(os.path.join(bench, "extra.py"), "y\n")
        rc, out, err = self.run_cli(["pin", "check", pin.COMPONENT, BENCH,
                                     "--project", root], tools)
        self.assertEqual(rc, 1)
        self.assertEqual(len(out.splitlines()), 1, "shared/tools passed first")
        self.assertIn("REFUSED:", err)
        self.assertIn("extra.py", err)

    def test_usage(self):
        root, tools, _, _ = two_tags("cli_usage")
        for argv in (["pin", "check"], ["pin", "where"],
                     ["pin", "where", BENCH, pin.COMPONENT],
                     ["pin", "entry", BENCH], ["pin", "entry", "a", "b", "c"]):
            rc, out, err = self.run_cli(argv + ["--project", root], tools)
            self.assertEqual((rc, out), (2, ""), argv)
            self.assertIn("usage:", err)
        rc, _, _ = self.run_cli(["pin", "list", "--project", root], tools)
        self.assertEqual(rc, 2)


# (name, text of vtrux_build/pin.py, its replacement): each must make this
# file fail. The text must occur in pin.py exactly once.
MUTANTS = [
    ("a machine's excludes counted (--exclude-standard)",
     '"--exclude-per-directory=.gitignore",', '"--exclude-standard",'),
    ("no check for other files",
     '    extra = untracked_files(checkout, pin["commit"], component)\n',
     '    extra = []\n'),
    ("a file staged in the index passes",
     '        if p and entries.get(p, (None, "tree"))[1] == "tree":\n',
     '        if False:\n'),
    ("an uncommitted .gitignore may hide files",
     'if p and posixpath.basename(p) == ".gitignore" and not any(',
     'if False and not any('),
    ("the .gitignore above the component not compared",
     "        elif got != r.stdout:\n",
     "        elif False:\n"),
    ("tracked files not compared",
     '    changed = tracked_differences(checkout, pin["commit"], component)\n',
     '    changed = []\n'),
    ("bytes not compared",
     "        if got != want:\n            out.append(\"%s: its bytes",
     "        if False:\n            out.append(\"%s: its bytes"),
    ("bytes compared after line-ending conversion",
     "        if got != want:\n            out.append(\"%s: its bytes",
     "        if got.replace(b\"\\r\\n\", b\"\\n\") != want:\n"
     "            out.append(\"%s: its bytes"),
    ("a missing file passes",
     '            out.append("%s: missing" % p)\n',
     '            pass\n'),
    ("the execute bit not compared",
     "        elif not windows:\n", "        elif False:\n"),
    ("the full permission bits compared",
     "            if executable != (mode == EXECUTABLE):\n",
     "            if executable != (mode == EXECUTABLE) or not "
     "st.st_mode & stat.S_IWUSR:\n"),
    ("a symbolic link's target not compared",
     "                if got != want:\n                    out.append(\"%s: a "
     "symbolic link to",
     "                if False:\n                    out.append(\"%s: a "
     "symbolic link to"),
    ("the digest taken from the disk",
     '    digest = git_digest(checkout, pin["commit"], component)\n',
     '    from .digest import component_digest\n'
     '    digest = component_digest(want)\n'),
    ("the pin's digest not compared",
     '    if digest != pin["digest"]:\n', '    if False:\n'),
    ("git_digest counts .md files",
     "        if excluded(posixpath.basename(p)):\n",
     "        if posixpath.basename(p) == \".gitignore\":\n"),
    ("HEAD not compared",
     '    if rc != 0 or head != pin["commit"]:\n', '    if False:\n'),
    ("the tag's commit not compared",
     '    if tagged != pin["commit"]:\n', '    if False:\n'),
    ("a component's files compared in shared/tools",
     'changed = tracked_differences(checkout, pin["commit"], component)',
     'changed = tracked_differences(checkout, pin["commit"], COMPONENT)'),
    ("a component's other files looked for in shared/tools",
     'extra = untracked_files(checkout, pin["commit"], component)',
     'extra = untracked_files(checkout, pin["commit"], COMPONENT)'),
    ("a component's digest taken over shared/tools",
     'digest = git_digest(checkout, pin["commit"], component)',
     'digest = git_digest(checkout, pin["commit"], COMPONENT)'),
    ("every pin read from the shared/tools entry",
     '    pin = data[component]\n', '    pin = data[COMPONENT]\n'),
    ("any name taken as a component",
     '    if not isinstance(component, str) or not _NAME.match(component):\n',
     '    if not isinstance(component, str):\n'),
    ("a component's tag not checked",
     '    tag_name(tag, "the %r pin in %s" % (component, path))\n',
     '    pass\n'),
    ("entry: HEAD not compared with the tag",
     '    if rc != 0 or head != tagged:\n', '    if rc != 0:\n'),
    ("entry: the digest taken from the disk",
     '            "digest": git_digest(checkout, head, component)}',
     '            "digest": __import__("vtrux_build.digest", fromlist=["x"])'
     '.component_digest(os.path.join(checkout, *component.split("/")))}'),
    ("the pin command does not check the tooling first",
     '        check(args.project, TOOLS_ROOT)\n', '        pass\n'),
]


def can_fail():
    """Run this file against the unedited pin.py and each mutant; True if
    the first passes and every mutant fails."""
    src_pkg = os.path.join(os.path.dirname(HERE), "vtrux_build")
    with open(os.path.join(src_pkg, "pin.py"), encoding="utf-8") as f:
        source = f.read()
    ok = True
    for i, (name, old, new) in enumerate([("control", None, None)] + MUTANTS):
        if old is not None and source.count(old) != 1:
            print("%-52s BAD MUTANT (its text occurs %d times in pin.py)"
                  % (name, source.count(old)))
            ok = False
            continue
        base = scratch.fresh("canfail_%02d" % i)
        pkg = os.path.join(base, "vtrux_build")
        shutil.copytree(src_pkg, pkg,
                        ignore=shutil.ignore_patterns("__pycache__"))
        if old is not None:
            scratch.write(os.path.join(pkg, "pin.py"),
                          source.replace(old, new))
        env = dict(os.environ, VTRUX_BUILD_UNDER_TEST=base,
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                           env=env, capture_output=True, text=True)
        last = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
        if old is None:
            good = r.returncode == 0
            print("%-52s %s (%s)" % (name, "passes" if good else "FAILS", last))
            if not good:
                print(r.stderr[-3000:])
        else:
            good = r.returncode != 0
            print("%-52s %s (%s)" % (name, "caught" if good else "MISSED",
                                     last))
        ok = ok and good
    print("can-fail: %s, %s" % ("PASS" if ok else "FAIL",
                                "the control passes and all %d mutants fail"
                                % len(MUTANTS) if ok else "see above"))
    return ok


if __name__ == "__main__":
    if sys.argv[1:] == ["--can-fail"]:
        sys.exit(0 if can_fail() else 1)
    unittest.main()
