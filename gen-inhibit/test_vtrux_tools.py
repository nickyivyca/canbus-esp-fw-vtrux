"""Implementor self-test of gen-inhibit/vtrux_tools.py, the launcher of the
pinned shared build tooling. Not evidence.

    py -3.14 -B gen-inhibit/test_vtrux_tools.py

A copy of the launcher runs in a scratch worktree, under scratch/
vtrux-tools-test/ at this worktree's root, laid out as this repo is: the
launcher in gen-inhibit/, the pin in gen-inhibit/firmware/, the checkout
beside the worktree. No pin, a pin with no tag, a pin anywhere but
gen-inhibit/firmware/ (not read), no checkout (the refusal names the
command), and a stand-in checkout whose cli.main() reports the arguments it
was given and exits 7: release gets --project gen-inhibit/firmware/ unless it
names one, and the other commands get nothing added. The stand-in's folder
must have no __pycache__ afterwards, since one would change the real
checkout's digest. Needs git.
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
CLI = ("import json, sys\n"
       "def main(argv=None):\n"
       "    print('ARGS ' + json.dumps(sys.argv[1:] if argv is None else argv))\n"
       "    return 7\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class Launcher(unittest.TestCase):

    def setUp(self):
        name = self.id().rsplit(".", 1)[-1]
        self.d = os.path.join(SCRATCH, name)
        if os.path.exists(self.d):
            shutil.rmtree(self.d)
        self.wt = os.path.join(self.d, "wt")
        self.gi = os.path.join(self.wt, "gen-inhibit")
        self.proj = os.path.join(self.gi, "firmware")
        os.makedirs(self.proj)
        shutil.copyfile(LAUNCHER, os.path.join(self.gi, "vtrux_tools.py"))
        subprocess.run(["git", "-C", self.wt, "init", "-q"], check=True,
                       env=GIT_ENV)

    def pins(self, entry, where=None):
        write(os.path.join(where or self.proj, "shared_pins.json"),
              json.dumps({"shared/tools": entry}))

    def stand_in(self):
        pkg = os.path.join(self.d, "shared-" + TAG, "shared", "tools",
                           "vtrux_build")
        write(os.path.join(pkg, "__init__.py"), "")
        write(os.path.join(pkg, "cli.py"), CLI)

    def run_launcher(self, *args):
        # No -B: the launcher itself must keep bytecode out of the checkout.
        # Run from the worktree's root, as the release command is.
        r = subprocess.run([sys.executable,
                            os.path.join(self.gi, "vtrux_tools.py")]
                           + list(args), capture_output=True, text=True,
                           cwd=self.wt)
        return r.returncode, r.stdout + r.stderr

    def args_seen(self, out):
        lines = [l for l in out.splitlines() if l.startswith("ARGS ")]
        self.assertEqual(len(lines), 1, out)
        return json.loads(lines[0][5:])

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

    def test_a_pin_anywhere_but_firmware_is_not_read(self):
        self.stand_in()
        for where in (self.wt, self.gi):
            self.pins({"tag": TAG}, where=where)
        rc, out = self.run_launcher("image", "x.bin")
        self.assertEqual(rc, 1)
        self.assertIn("no 'shared/tools' tag", out)
        self.assertIn(os.path.join(self.proj, "shared_pins.json"), out)

    def test_missing_checkout_names_the_command(self):
        self.pins({"tag": TAG})
        rc, out = self.run_launcher("release")
        self.assertEqual(rc, 1)
        self.assertIn(os.path.normpath(os.path.join(self.d, "shared-" + TAG)),
                      out)
        self.assertIn("worktree add --detach ../shared-%s %s" % (TAG, TAG), out)

    def test_runs_the_checkouts_cli(self):
        self.pins({"tag": TAG})
        self.stand_in()
        rc, out = self.run_launcher("image", "a.bin", "b.bin")
        self.assertEqual(rc, 7, out)
        self.assertEqual(self.args_seen(out), ["image", "a.bin", "b.bin"])
        found = [d for d, _, _ in os.walk(os.path.join(self.d, "shared-" + TAG))
                 if os.path.basename(d) == "__pycache__"]
        self.assertEqual(found, [])

    def test_release_gets_the_project(self):
        self.pins({"tag": TAG})
        self.stand_in()
        rc, out = self.run_launcher("release", "--jobs", "1")
        self.assertEqual(rc, 7, out)
        seen = self.args_seen(out)
        self.assertEqual(seen[:4], ["release", "--jobs", "1", "--project"])
        self.assertEqual(len(seen), 5, seen)
        self.assertTrue(same(seen[4], self.proj), seen)

    def test_release_keeps_a_named_project(self):
        self.pins({"tag": TAG})
        self.stand_in()
        for args in (["release", "--project", "elsewhere"],
                     ["release", "--project=elsewhere", "--check-only"],
                     ["release", "--proj", "elsewhere"]):
            rc, out = self.run_launcher(*args)
            self.assertEqual(rc, 7, out)
            self.assertEqual(self.args_seen(out), args)

    def test_digest_gets_nothing_added(self):
        self.pins({"tag": TAG})
        self.stand_in()
        rc, out = self.run_launcher("digest", "--project")
        self.assertEqual(rc, 7, out)
        self.assertEqual(self.args_seen(out), ["digest", "--project"])


if __name__ == "__main__":
    unittest.main()
