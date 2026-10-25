"""Tests of the build tooling's pin (vtrux_build.pin): SPEC R23 and R16's
tooling-pin clause, on scratch git repositories.

    python -B shared/tools/tests/test_pin.py

Each project is a git worktree with shared_pins.json committed, and its
pinned checkout is a separate repository at ../shared-<tag>/ (pinkit), except
where a test needs the two in one repository, as the real worktrees are.
Needs git.
"""

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

    def test_digest_differs(self):
        root, tools, _ = project("pin_digest")
        scratch.write(os.path.join(tools, "vtrux_build", "__init__.py"),
                      "x = 2\n")
        self.refused(root, tools, "digest", "R23")

    def test_new_file_changes_the_digest(self):
        """A file the checkout never had, a __pycache__ for example, is
        counted by R7 and refused."""
        root, tools, _ = project("pin_pycache")
        scratch.write(os.path.join(tools, "vtrux_build", "__pycache__",
                                   "x.pyc"), b"\0")
        self.refused(root, tools, "digest", "R23")

    def test_md_only_change_is_dirty_not_refused(self):
        root, tools, good = project("pin_md")
        scratch.write(os.path.join(tools, "README.md"), "edited\n")
        self.assertEqual(pin.check(root, tools), dict(good, dirty=True))

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


if __name__ == "__main__":
    unittest.main()
