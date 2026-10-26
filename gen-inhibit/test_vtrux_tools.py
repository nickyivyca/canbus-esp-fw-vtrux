"""Implementor self-test of tools/vtrux_tools.py, the launcher of the pinned
shared build tooling. Not evidence.

    py -3.14 -B tools/test_vtrux_tools.py

A copy of the launcher runs in a scratch worktree, under scratch/
vtrux-tools-test/ in this one: no pin, a pin with no tag, no checkout (the
refusal names the command), and a stand-in checkout whose cli.main() reports
the arguments it was given and exits 7. The stand-in's folder must have no
__pycache__ afterwards, since one would change the real checkout's digest.
Needs git.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(HERE, "vtrux_tools.py")
SCRATCH = os.path.join(os.path.dirname(HERE), "scratch", "vtrux-tools-test")
TAG = "2026.01.01"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
CLI = ("import sys\n"
       "def main(argv=None):\n"
       "    print('ARGS %r' % (sys.argv[1:] if argv is None else argv))\n"
       "    return 7\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Launcher(unittest.TestCase):

    def setUp(self):
        name = self.id().rsplit(".", 1)[-1]
        self.d = os.path.join(SCRATCH, name)
        if os.path.exists(self.d):
            shutil.rmtree(self.d)
        self.wt = os.path.join(self.d, "wt")
        os.makedirs(os.path.join(self.wt, "tools"))
        shutil.copyfile(LAUNCHER, os.path.join(self.wt, "tools",
                                               "vtrux_tools.py"))
        subprocess.run(["git", "-C", self.wt, "init", "-q"], check=True,
                       env=GIT_ENV)

    def pins(self, entry):
        write(os.path.join(self.wt, "shared_pins.json"),
              json.dumps({"shared/tools": entry}))

    def run_launcher(self, *args):
        # No -B: the launcher itself must keep bytecode out of the checkout.
        r = subprocess.run([sys.executable,
                            os.path.join(self.wt, "tools", "vtrux_tools.py")]
                           + list(args), capture_output=True, text=True,
                           cwd=self.wt)
        return r.returncode, r.stdout + r.stderr

    def test_no_pin(self):
        rc, out = self.run_launcher("image", "x.bin")
        self.assertEqual(rc, 1)
        self.assertIn("no 'shared/tools' tag", out)

    def test_pin_without_tag(self):
        self.pins({"commit": "c" * 40})
        rc, out = self.run_launcher("image", "x.bin")
        self.assertEqual(rc, 1)
        self.assertIn("no 'shared/tools' tag", out)
        self.pins({"tag": ""})
        rc, out = self.run_launcher("image", "x.bin")
        self.assertEqual(rc, 1)
        self.assertIn("names no tag", out)

    def test_missing_checkout_names_the_command(self):
        self.pins({"tag": TAG})
        rc, out = self.run_launcher("release")
        self.assertEqual(rc, 1)
        self.assertIn(os.path.normpath(os.path.join(self.d, "shared-" + TAG)),
                      out)
        self.assertIn("worktree add --detach ../shared-%s %s" % (TAG, TAG), out)

    def test_runs_the_checkouts_cli(self):
        self.pins({"tag": TAG})
        pkg = os.path.join(self.d, "shared-" + TAG, "shared", "tools",
                           "vtrux_build")
        write(os.path.join(pkg, "__init__.py"), "")
        write(os.path.join(pkg, "cli.py"), CLI)
        rc, out = self.run_launcher("image", "a.bin", "b.bin")
        self.assertEqual(rc, 7, out)
        self.assertIn("ARGS ['image', 'a.bin', 'b.bin']", out)
        found = [d for d, _, _ in os.walk(os.path.join(self.d, "shared-" + TAG))
                 if os.path.basename(d) == "__pycache__"]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
