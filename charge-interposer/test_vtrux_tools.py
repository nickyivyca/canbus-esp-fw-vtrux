"""Implementor self-test of charge-interposer/vtrux_tools.py, the launcher of
the pinned shared build tooling and of the interposer's dev builds. Not
evidence.

    py -3.14 -B charge-interposer/test_vtrux_tools.py
    py -3.14 -B charge-interposer/test_vtrux_tools.py --can-fail

A copy of the launcher runs in a scratch worktree laid out as this repo is:
the launcher in charge-interposer/, the pin and a copy of the real
CMakeLists.txt in charge-interposer/firmware/, the pinned checkout beside
the worktree. The ESP-IDF it sees is a stand-in: a tools folder holding a
Python venv (made here with --without-pip) and a ROM folder, an idf.py and a
ninja that record their arguments and environment and exit with a code the
test chooses. Covered:

- the pin: none, one with no tag, one anywhere but firmware/ (not read), a
  missing checkout (the refusal names the command), and a stand-in checkout
  whose cli.main() reports its arguments, with no __pycache__ left in it;
- release gets --project charge-interposer/firmware unless it names one;
  image and digest get nothing added and need no ESP-IDF environment;
- release and build refuse, before anything runs, an IDF_TOOLS_PATH unset or
  empty, and an IDF_PYTHON_ENV_PATH or ESP_ROM_ELF_DIR unset or outside it;
- build: the variants come from CMakeLists.txt's INTP_VARIANTS line; an
  unknown or missing variant is refused; reconfigure in build/<variant> with
  -D INTP_VARIANT, then ninja there, -j N only with --jobs; a failed
  reconfigure stops it with its exit code; the environment passed on is the
  caller's, with no SOURCE_DATE_EPOCH added.

--can-fail runs the same tests on mutants of the launcher, each a premise-
checked edit that breaks one of these rules, and on an unchanged copy as the
control. It passes when the control passes and every mutant fails.

Scratch: <worktree>/scratch/charge-interposer-vtrux-tools-test/<pid>/,
removed when the run ends, pass or fail, read-only files included (AGENTS.md
"Removing scratch"); a folder that cannot be removed is printed with the
reason. Needs git.
"""

import atexit
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.environ.get("VTRUX_TOOLS_UNDER_TEST",
                          os.path.join(HERE, "vtrux_tools.py"))
CMAKELISTS = os.path.join(HERE, "firmware", "CMakeLists.txt")
SCRATCH = os.path.join(os.path.dirname(HERE), "scratch",
                       "charge-interposer-vtrux-tools-test")
TAG = "2026.01.01"
VARIANTS = ["bridge", "witness", "truck", "selftest", "diag", "slcan"]
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
IDF_NAMES = ("IDF_TOOLS_PATH", "IDF_PATH", "IDF_PYTHON_ENV_PATH",
             "ESP_ROM_ELF_DIR", "SOURCE_DATE_EPOCH")
CLI = ("import json, sys\n"
       "def main(argv=None):\n"
       "    print('ARGS ' + json.dumps(sys.argv[1:] if argv is None else argv))\n"
       "    return 7\n")
# The stand-in idf.py and ninja: append {tool, argv, epoch} to $RECORD,
# exit with $<TOOL>_RC (default 0).
RECORDER = ("import json, os, sys\n"
            "tool = sys.argv[1]\n"
            "with open(os.environ['RECORD'], 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps({'tool': tool, 'argv': sys.argv[2:],\n"
            "        'epoch': os.environ.get('SOURCE_DATE_EPOCH')}) + '\\n')\n"
            "sys.exit(int(os.environ.get(tool.upper() + '_RC', '0')))\n")


# ---------- scratch, removed whatever happens ----------

def _long(path):
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def _clear_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def remove_tree(path):
    """Remove path, read-only files included, retrying after short waits.
    Returns "" or why it is still there."""
    why = ""
    for wait in (0, 0.1, 0.4):
        time.sleep(wait)
        if not os.path.exists(_long(path)):
            return ""
        try:
            shutil.rmtree(_long(path), onerror=_clear_readonly)
        except OSError as e:
            why = "%s: %s" % (type(e).__name__, e)
    return "" if not os.path.exists(_long(path)) else (why or "still there")


RUN_DIR = os.path.join(SCRATCH, str(os.getpid()))


def _cleanup():
    for d in (RUN_DIR, SCRATCH):
        if d == SCRATCH and os.path.isdir(d) and os.listdir(d):
            continue  # another run's folder is in it
        why = remove_tree(d)
        if why:
            print("LEFTOVER %s (%s)" % (d, why))


atexit.register(_cleanup)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def same(a, b):
    return (os.path.normcase(os.path.abspath(a))
            == os.path.normcase(os.path.abspath(b)))


# ---------- the stand-in ESP-IDF, made once per run ----------

IDF = {}


def setUpModule():
    base = os.path.join(RUN_DIR, "idf")
    tools = os.path.join(base, "espressif")
    venv = os.path.join(tools, "python_env", "idf_test_env")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv],
                   check=True)
    py = os.path.join(venv, "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    rom = os.path.join(tools, "tools", "esp-rom-elfs", "test")
    os.makedirs(rom)
    idf_path = os.path.join(base, "esp-idf")
    recorder = os.path.join(base, "recorder.py")
    write(recorder, RECORDER)
    # idf.py is run by the venv's Python, as the launcher runs the real one.
    write(os.path.join(idf_path, "tools", "idf.py"),
          "import runpy, sys\n"
          "sys.argv = [%r, 'idf'] + sys.argv[1:]\n"
          "runpy.run_path(%r, run_name='__main__')\n" % (recorder, recorder))
    bindir = os.path.join(base, "bin")
    if os.name == "nt":
        write(os.path.join(bindir, "ninja.bat"),
              '@"%s" "%s" ninja %%*\r\n' % (py, recorder))
    else:
        ninja = os.path.join(bindir, "ninja")
        write(ninja, '#!/bin/sh\nexec "%s" "%s" ninja "$@"\n' % (py, recorder))
        os.chmod(ninja, 0o755)
    IDF.update(tools=tools, venv=venv, rom=rom, idf_path=idf_path,
               bindir=bindir)


class Launcher(unittest.TestCase):

    def setUp(self):
        name = self.id().rsplit(".", 1)[-1]
        self.d = os.path.join(RUN_DIR, name)
        self.wt = os.path.join(self.d, "wt")
        self.ci = os.path.join(self.wt, "charge-interposer")
        self.proj = os.path.join(self.ci, "firmware")
        os.makedirs(self.proj)
        shutil.copyfile(LAUNCHER, os.path.join(self.ci, "vtrux_tools.py"))
        shutil.copyfile(CMAKELISTS, os.path.join(self.proj, "CMakeLists.txt"))
        subprocess.run(["git", "-C", self.wt, "init", "-q"], check=True,
                       env=GIT_ENV)
        self.record = os.path.join(self.d, "record.jsonl")
        self.env = {k: v for k, v in os.environ.items()
                    if k.upper() not in IDF_NAMES}
        self.env.update(IDF_TOOLS_PATH=IDF["tools"], IDF_PATH=IDF["idf_path"],
                        IDF_PYTHON_ENV_PATH=IDF["venv"],
                        ESP_ROM_ELF_DIR=IDF["rom"], RECORD=self.record,
                        PATH=IDF["bindir"] + os.pathsep
                        + os.environ.get("PATH", ""))

    def tearDown(self):
        why = remove_tree(self.d)
        if why:
            print("LEFTOVER %s (%s)" % (self.d, why))

    # helpers

    def pins(self, entry, where=None):
        write(os.path.join(where or self.proj, "shared_pins.json"),
              json.dumps({"shared/tools": entry}))

    def stand_in(self):
        pkg = os.path.join(self.d, "shared-" + TAG, "shared", "tools",
                           "vtrux_build")
        write(os.path.join(pkg, "__init__.py"), "")
        write(os.path.join(pkg, "cli.py"), CLI)

    def run_launcher(self, *args, env=None):
        # No -B: the launcher itself must keep bytecode out of the checkout.
        # Run from the worktree's root, as the commands are.
        r = subprocess.run([sys.executable,
                            os.path.join(self.ci, "vtrux_tools.py")]
                           + list(args), capture_output=True, text=True,
                           cwd=self.wt, env=self.env if env is None else env)
        return r.returncode, r.stdout + r.stderr

    def args_seen(self, out):
        lines = [l for l in out.splitlines() if l.startswith("ARGS ")]
        self.assertEqual(len(lines), 1, out)
        return json.loads(lines[0][5:])

    def calls(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def ready(self):
        self.pins({"tag": TAG})
        self.stand_in()

    # the pin (as gen-inhibit's launcher)

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
        for where in (self.wt, self.ci):
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
        self.ready()
        rc, out = self.run_launcher("image", "a.bin", "b.bin")
        self.assertEqual(rc, 7, out)
        self.assertEqual(self.args_seen(out), ["image", "a.bin", "b.bin"])
        found = [d for d, _, _ in os.walk(os.path.join(self.d, "shared-" + TAG))
                 if os.path.basename(d) == "__pycache__"]
        self.assertEqual(found, [])

    def test_release_gets_the_project(self):
        self.ready()
        rc, out = self.run_launcher("release", "--jobs", "1")
        self.assertEqual(rc, 7, out)
        seen = self.args_seen(out)
        self.assertEqual(seen[:4], ["release", "--jobs", "1", "--project"])
        self.assertEqual(len(seen), 5, seen)
        self.assertTrue(same(seen[4], self.proj), seen)

    def test_release_keeps_a_named_project(self):
        self.ready()
        for args in (["release", "--project", "elsewhere"],
                     ["release", "--project=elsewhere", "--check-only"],
                     ["release", "--proj", "elsewhere"]):
            rc, out = self.run_launcher(*args)
            self.assertEqual(rc, 7, out)
            self.assertEqual(self.args_seen(out), args)

    def test_image_and_digest_need_no_idf_and_get_nothing_added(self):
        self.ready()
        env = {k: v for k, v in self.env.items() if k.upper() not in IDF_NAMES}
        for args in (["digest", "--project"], ["image", "a.bin"]):
            rc, out = self.run_launcher(*args, env=env)
            self.assertEqual(rc, 7, out)
            self.assertEqual(self.args_seen(out), args)

    # the ESP-IDF environment, for release and build

    def bad_envs(self):
        outside = os.path.join(self.d, "elsewhere")
        e = self.env
        yield "IDF_TOOLS_PATH unset", {k: v for k, v in e.items()
                                       if k != "IDF_TOOLS_PATH"}, "not set"
        yield "IDF_TOOLS_PATH empty", dict(e, IDF_TOOLS_PATH=""), "empty"
        yield "IDF_TOOLS_PATH blank", dict(e, IDF_TOOLS_PATH="  "), "empty"
        for name in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
            yield (name + " unset", {k: v for k, v in e.items() if k != name},
                   name + " is not set")
            yield name + " outside", dict(e, **{name: outside}), "not inside"
        # A tools folder whose name only starts like the real one's.
        yield ("ESP_ROM_ELF_DIR in a sibling",
               dict(e, ESP_ROM_ELF_DIR=IDF["tools"] + "-other"), "not inside")

    def test_release_refuses_a_wrong_idf_env(self):
        self.ready()
        for what, env, says in self.bad_envs():
            with self.subTest(what):
                rc, out = self.run_launcher("release", env=env)
                self.assertEqual(rc, 1, out)
                self.assertIn(says, out)
                self.assertNotIn("ARGS ", out)

    def test_build_refuses_a_wrong_idf_env(self):
        for what, env, says in self.bad_envs():
            with self.subTest(what):
                rc, out = self.run_launcher("build", "bridge", env=env)
                self.assertEqual(rc, 1, out)
                self.assertIn(says, out)
                self.assertEqual(self.calls(), [])

    def test_a_good_idf_env_reaches_the_cli(self):
        self.ready()
        rc, out = self.run_launcher("release", "--check-only")
        self.assertEqual(rc, 7, out)

    # build

    def test_variants_come_from_cmakelists(self):
        rc, out = self.run_launcher("build", "nonesuch")
        self.assertEqual(rc, 1, out)
        self.assertIn("no variant 'nonesuch'", out)
        self.assertIn("the variants are: " + " ".join(VARIANTS), out)
        self.assertEqual(self.calls(), [])
        rc, out = self.run_launcher("build")
        self.assertEqual(rc, 1, out)
        self.assertIn("usage", out)
        self.assertEqual(self.calls(), [])

    def test_build_reconfigures_then_ninja(self):
        for v in ("bridge", "truck", "slcan"):
            with self.subTest(v):
                if os.path.exists(self.record):
                    os.remove(self.record)
                rc, out = self.run_launcher("build", v)
                self.assertEqual(rc, 0, out)
                folder = os.path.join(self.proj, "build", v)
                c = self.calls()
                self.assertEqual([x["tool"] for x in c], ["idf", "ninja"], c)
                a = c[0]["argv"]
                self.assertEqual(a[0], "-C")
                self.assertTrue(same(a[1], self.proj), a)
                self.assertEqual(a[2], "-B")
                self.assertTrue(same(a[3], folder), a)
                self.assertEqual(a[4:], ["-D", "INTP_VARIANT=" + v,
                                         "reconfigure"])
                n = c[1]["argv"]
                self.assertEqual(n[0], "-C")
                self.assertTrue(same(n[1], folder), n)
                self.assertEqual(n[2:], ["all"])

    def test_build_jobs(self):
        for args in (["--jobs", "1"], ["--jobs=3"]):
            with self.subTest(args):
                if os.path.exists(self.record):
                    os.remove(self.record)
                rc, out = self.run_launcher("build", "witness", *args)
                self.assertEqual(rc, 0, out)
                n = self.calls()[1]["argv"]
                self.assertEqual(n[2:], ["-j", args[-1].split("=")[-1], "all"])
        for bad in ("0", "x"):
            rc, out = self.run_launcher("build", "witness", "--jobs", bad)
            self.assertEqual(rc, 1, out)
            self.assertIn("--jobs", out)

    def test_build_adds_no_epoch(self):
        rc, out = self.run_launcher("build", "bridge")
        self.assertEqual(rc, 0, out)
        self.assertEqual([x["epoch"] for x in self.calls()], [None, None])
        # A caller's own value is passed through, not replaced.
        os.remove(self.record)
        rc, out = self.run_launcher("build", "bridge",
                                    env=dict(self.env, SOURCE_DATE_EPOCH="5"))
        self.assertEqual(rc, 0, out)
        self.assertEqual([x["epoch"] for x in self.calls()], ["5", "5"])

    def test_failed_reconfigure_stops_the_build(self):
        rc, out = self.run_launcher("build", "diag",
                                    env=dict(self.env, IDF_RC="3"))
        self.assertEqual(rc, 3, out)
        self.assertEqual([x["tool"] for x in self.calls()], ["idf"])
        self.assertIn("reconfigure failed", out)

    def test_failed_ninja_is_the_exit_code(self):
        rc, out = self.run_launcher("build", "diag",
                                    env=dict(self.env, NINJA_RC="4"))
        self.assertEqual(rc, 4, out)
        self.assertIn("build failed", out)


# ---------- --can-fail ----------

# (name, text in the launcher, its replacement). Each must be in the
# launcher exactly once, or the mutant is not made and the run fails.
MUTANTS = [
    ("IDF_TOOLS_PATH not checked",
     "    if not tools.strip():\n", "    if False:\n"),
    ("IDF paths not checked to be inside it",
     "        if not _inside(value, tools):\n", "        if False:\n"),
    ("inside by string prefix",
     "        return os.path.commonpath([path, folder]) == folder\n",
     "        return path.startswith(folder)\n"),
    ("release not checked",
     "    if argv[:1] == [\"release\"]:\n        check_idf_env()\n", ""),
    ("build not checked",
     "    check_idf_env()\n    env_path = ", "    env_path = "),
    ("release without --project",
     "    return argv + [\"--project\", ROOT]\n", "    return argv\n"),
    ("pin read beside the launcher",
     "    path = os.path.join(ROOT, PIN_FILE)\n",
     "    path = os.path.join(os.path.dirname(ROOT), PIN_FILE)\n"),
    ("bytecode written",
     "sys.dont_write_bytecode = True\n", "sys.dont_write_bytecode = False\n"),
    ("variant not checked",
     "    if variant not in known:\n", "    if variant is None:\n"),
    ("one build folder for every variant",
     "    folder = os.path.join(ROOT, \"build\", variant)\n",
     "    folder = os.path.join(ROOT, \"build\")\n"),
    ("--jobs dropped",
     "              + ([\"-j\", str(jobs)] if jobs else []) + [\"all\"])\n",
     "              + [\"all\"])\n"),
    ("epoch added",
     "    return subprocess.run(argv).returncode\n",
     "    return subprocess.run(argv, env=dict(os.environ, "
     "SOURCE_DATE_EPOCH=\"1767225600\")).returncode\n"),
    ("failed reconfigure ignored",
     "    if rc != 0:\n        print(\"vtrux_tools: reconfigure failed",
     "    if False:\n        print(\"vtrux_tools: reconfigure failed"),
]


def can_fail():
    with open(LAUNCHER, encoding="utf-8") as f:
        source = f.read()
    folder = os.path.join(RUN_DIR, "mutants")
    os.makedirs(folder)
    results, bad = [], False
    for i, (name, old, new) in enumerate([("control", "", "")] + MUTANTS):
        if old and source.count(old) != 1:
            results.append((name, "NOT MADE: the text is in the launcher %d "
                                  "times, not once" % source.count(old)))
            bad = True
            continue
        path = os.path.join(folder, "m%02d" % i, "vtrux_tools.py")
        write(path, source.replace(old, new) if old else source)
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                           env=dict(os.environ, VTRUX_TOOLS_UNDER_TEST=path),
                           capture_output=True, text=True)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
        if name == "control":
            ok = r.returncode == 0
            results.append((name, ("passes" if ok else "FAILS: ") +
                            ("" if ok else (r.stdout + r.stderr)[-2000:])))
        else:
            ok = r.returncode != 0
            results.append((name, ("caught (%s)" % tail[0]) if ok
                            else "MISSED: every test passes"))
        bad = bad or not ok
    for name, what in results:
        print("%-40s %s" % (name, what))
    print("can-fail: %s" % ("FAIL" if bad else
                            "PASS, the control passes and all %d mutants "
                            "fail" % len(MUTANTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--can-fail"]:
        sys.exit(can_fail())
    unittest.main()
