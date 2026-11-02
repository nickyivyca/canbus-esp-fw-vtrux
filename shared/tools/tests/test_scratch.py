"""Tests of the tests' scratch folders (tests/scratch.py): AGENTS.md "Files and
folders" and "Removing scratch".

    python -B shared/tools/tests/test_scratch.py

Needs git. The cases outside a worktree set GIT_CEILING_DIRECTORIES, so that
git stops looking for a repository at a scratch folder of this run.
"""

import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scratch                                           # noqa: E402

WINDOWS = os.name == "nt"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def lp(p):
    """Absolute path p with the \\\\?\\ prefix, made here rather than by
    scratch.long_path, so a case does not lean on what it tests. Not through
    os.path.abspath, which on Windows drops a trailing dot or space."""
    assert os.path.isabs(p), p
    return "\\\\?\\" + p


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True,
                   capture_output=True, text=True, env=GIT_ENV)


def repo(name, ignore):
    """A scratch git repository, with one commit, and a root .gitignore of
    /scratch/ if ignore."""
    d = os.path.join(scratch.fresh(name), "repo")
    os.makedirs(d)
    git(d, "init", "-q")
    scratch.write(os.path.join(d, "f.txt"), "x\n")
    if ignore:
        scratch.write(os.path.join(d, ".gitignore"), "/scratch/\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "one")
    return d


def ceiling(at):
    """The environment, with git's search for a repository stopped at at."""
    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = at
    return env


def child(code, cwd, env=None):
    """Runs code in a fresh interpreter whose path starts at cwd."""
    pre = "import sys; sys.path.insert(0, %r)\n" % cwd
    return subprocess.run([sys.executable, "-B", "-c", pre + code], cwd=cwd,
                          env=env, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class Where(unittest.TestCase):

    def test_root_is_this_worktrees_scratch(self):
        top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(scratch.ROOT, os.path.join(
            os.path.normpath(top), "scratch", "shared-tools-tests",
            str(os.getpid())))

    def test_ignored_scratch(self):
        d = repo("where_ignored", ignore=True)
        self.assertEqual(scratch.scratch_dir(d), os.path.join(
            os.path.normpath(d), "scratch", "shared-tools-tests"))

    def test_refused_when_scratch_is_not_ignored(self):
        d = repo("where_not_ignored", ignore=False)
        with self.assertRaises(scratch.Refused) as e:
            scratch.scratch_dir(d)
        self.assertIn("does not ignore scratch/", str(e.exception))

    def test_refused_outside_a_worktree(self):
        top = scratch.fresh("where_no_git")
        d = os.path.join(top, "a")
        os.makedirs(d)
        with self.assertRaises(scratch.Refused) as e:
            scratch.scratch_dir(d, env=ceiling(top))
        self.assertIn("not in a git worktree", str(e.exception))

    def test_import_refused_outside_a_worktree(self):
        """No fallback: the module is not imported, and nothing is written."""
        top = scratch.fresh("where_import")
        d = os.path.join(top, "a")
        scratch.write(os.path.join(d, "scratch.py"),
                      scratch.read(os.path.join(HERE, "scratch.py")))
        r = child("import scratch\nprint('ROOT', scratch.ROOT)\n", d,
                  env=ceiling(top))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Refused", r.stderr)
        self.assertIn("not in a git worktree", r.stderr)
        self.assertNotIn("ROOT", r.stdout)
        self.assertEqual(sorted(os.listdir(d)), ["scratch.py"])


@unittest.skipUnless(shutil.which("git"), "needs git")
class Remove(unittest.TestCase):

    def test_never_there(self):
        self.assertTrue(scratch.remove_tree(
            os.path.join(scratch.ROOT, "no-such-folder")))

    def test_read_only_files_and_a_git_repository(self):
        """Git for Windows makes .git/objects read-only."""
        d = repo("rm_git", ignore=True)
        ro = scratch.write(os.path.join(d, "sub", "ro.txt"), "x\n")
        os.chmod(ro, stat.S_IREAD)
        top = os.path.dirname(d)
        self.assertTrue(scratch.remove_tree(top))
        self.assertFalse(os.path.lexists(top))

    @unittest.skipUnless(WINDOWS, "a Windows path limit")
    def test_long_path(self):
        top = scratch.fresh("rm_long")
        deep = os.path.join(top, *["d" * 40] * 7)
        self.assertGreater(len(deep), 300)
        os.makedirs(lp(deep))
        with open(lp(os.path.join(deep, "f.txt")), "w") as f:
            f.write("x")
        self.assertTrue(scratch.remove_tree(top))
        self.assertFalse(os.path.lexists(lp(top)))

    @unittest.skipUnless(WINDOWS, "only Windows rewrites a trailing dot")
    def test_names_ending_in_a_dot_or_a_space(self):
        """Nested, with a file: Python 3.14's rmtree removes an empty x.
        without the prefix, but not one with something in it."""
        top = scratch.fresh("rm_dot")
        inner = os.path.join(top, "x.", "y ")
        os.makedirs(lp(inner))
        open(lp(os.path.join(inner, "f.")), "w").close()
        self.assertTrue(scratch.remove_tree(top))
        self.assertFalse(os.path.lexists(lp(top)))

    def test_what_is_left_is_printed(self):
        top = scratch.fresh("rm_left")
        held = scratch.write(os.path.join(top, "sub", "held.txt"), "x\n")
        out = io.StringIO()
        if WINDOWS:
            # An open file cannot be deleted on Windows.
            f = open(held, "rb")
            undo = f.close
        else:
            if os.geteuid() == 0:
                self.skipTest("root removes from a read-only folder")
            sub = os.path.dirname(held)
            os.chmod(sub, stat.S_IREAD | stat.S_IEXEC)
            try:
                open(os.path.join(sub, "probe"), "w").close()
            except PermissionError:
                pass
            else:
                os.chmod(sub, stat.S_IRWXU)
                self.skipTest("this file system does not enforce folder "
                              "permissions (WSL's /mnt)")

            def undo():
                os.chmod(sub, stat.S_IRWXU)
        try:
            with contextlib.redirect_stdout(out):
                ok = scratch.remove_tree(top)
        finally:
            undo()
        self.assertFalse(ok)
        self.assertIn("could not remove %s" % top, out.getvalue())
        self.assertIn("held.txt", out.getvalue())
        self.assertTrue(scratch.remove_tree(top))

    def test_root_removed_at_exit(self):
        """A run's ROOT, a read-only file in it, is gone once it exits."""
        r = child("import os, stat, scratch\n"
                  "p = scratch.write(os.path.join(scratch.fresh('x'), 'ro.txt'), 'x')\n"
                  "os.chmod(p, stat.S_IREAD)\n"
                  "print(scratch.ROOT)\n", HERE)
        self.assertEqual(r.returncode, 0, r.stderr)
        root = r.stdout.strip().splitlines()[0]
        self.assertTrue(root.startswith(os.path.dirname(scratch.ROOT)), root)
        self.assertNotIn("could not remove", r.stdout)
        self.assertFalse(os.path.lexists(root))


if __name__ == "__main__":
    unittest.main()
