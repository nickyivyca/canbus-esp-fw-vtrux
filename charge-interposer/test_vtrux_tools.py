"""Implementor self-test of charge-interposer/vtrux_tools.py, the launcher of
the pinned shared build tooling, and of the project's variant list. Not
evidence.

    py -3.14 -B charge-interposer/test_vtrux_tools.py
    py -3.14 -B charge-interposer/test_vtrux_tools.py --can-fail

A copy of the launcher runs in a scratch worktree laid out as this repo is:
the launcher in charge-interposer/, the pin and copies of the real
vtrux_build.json and CMakeLists.txt in charge-interposer/firmware/, a
checkout beside the worktree. The checkout is either a stand-in whose
cli.main() reports its arguments, or a copy of the real vtrux_build package
from the checkout this repo's shared_pins.json pins, so that build,
check-env and check_idf_env() run the pinned tooling's own code. The ESP-IDF
it sees is a stand-in: a tools folder holding a Python venv (made here with
--without-pip) and a ROM folder, an idf.py and a ninja that record their
arguments and environment and exit with a code the test chooses. Covered:

- the pin: none, one with no tag, one anywhere but firmware/ (not read), a
  missing checkout (the refusal names the command), and the checkout's
  cli.main() run, with no __pycache__ left in it;
- release and build get --project charge-interposer/firmware unless they
  name one (an abbreviation counts); check-env, image and digest get
  nothing added, and image and digest need no ESP-IDF environment;
- build, through the pinned tooling: for each variant, the idf.py and ninja
  commands the 2a launcher ran (reconfigure in build/<variant> with
  -D INTP_VARIANT=<variant>, then ninja there, -j N only with --jobs); no
  SOURCE_DATE_EPOCH added; an unknown or missing variant, a bad --jobs and
  a wrong IDF environment refused before anything runs; a failed
  reconfigure or ninja exits 1 and is never reported as BUILT;
- check-env and check_idf_env(): the tooling's R32 refusals, each as exit
  1; check_idf_env() refuses a vtrux_build imported from anywhere else;
- the variants: vtrux_build.json lists the six CMakeLists.txt's
  INTP_VARIANTS line names, in its order, each configured with exactly
  -D INTP_VARIANT=<its name>.

--can-fail runs the same tests on mutants of the launcher and of
vtrux_build.json, each a premise-checked edit that breaks one of these
rules, and on unchanged copies as the control. It passes when the control
passes and every mutant fails.

Scratch: <worktree>/scratch/charge-interposer-vtrux-tools-test/<pid>/,
removed when the run ends, pass or fail, read-only files included (AGENTS.md
"Removing scratch"); a folder that cannot be removed is printed with the
reason. Needs git, and the pinned shared/tools checkout.
"""

import atexit
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.environ.get("VTRUX_TOOLS_UNDER_TEST",
                          os.path.join(HERE, "vtrux_tools.py"))
DATA = os.environ.get("VTRUX_BUILD_JSON_UNDER_TEST",
                      os.path.join(HERE, "firmware", "vtrux_build.json"))
CMAKELISTS = os.path.join(HERE, "firmware", "CMakeLists.txt")
PINS = os.path.join(HERE, "firmware", "shared_pins.json")
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
CMAKE_VARIANTS = re.compile(r"^\s*set\(\s*INTP_VARIANTS\s+([^)]*)\)", re.M)


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


def pinned_package():
    """The vtrux_build package in the checkout this repo pins (the real
    one, read only here). Fails the run, naming the folder, if missing."""
    with open(PINS, encoding="utf-8") as f:
        tag = json.load(f)["shared/tools"]["tag"]
    top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True
                         ).stdout.strip()
    pkg = os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag,
                                        "shared", "tools", "vtrux_build"))
    if not os.path.isfile(os.path.join(pkg, "devbuild.py")):
        raise SystemExit("test_vtrux_tools: the pinned tooling %s is missing "
                         "or has no devbuild.py (R31)" % pkg)
    return pkg


# ---------- the stand-in ESP-IDF and the tooling copy, made once per run ----

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
    # idf.py is run by the venv's Python, as the tooling runs the real one.
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
    package = os.path.join(RUN_DIR, "tooling", "vtrux_build")
    shutil.copytree(pinned_package(), package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    IDF.update(tools=tools, venv=venv, rom=rom, idf_path=idf_path,
               bindir=bindir, package=package)


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
        shutil.copyfile(DATA, os.path.join(self.proj, "vtrux_build.json"))
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

    def checkout(self):
        return os.path.join(self.d, "shared-" + TAG)

    def stand_in(self):
        pkg = os.path.join(self.checkout(), "shared", "tools", "vtrux_build")
        write(os.path.join(pkg, "__init__.py"), "")
        write(os.path.join(pkg, "cli.py"), CLI)

    def ready(self):
        """The pin and a stand-in checkout."""
        self.pins({"tag": TAG})
        self.stand_in()

    def real(self):
        """The pin and a checkout holding the pinned tooling's code."""
        self.pins({"tag": TAG})
        shutil.copytree(IDF["package"], os.path.join(
            self.checkout(), "shared", "tools", "vtrux_build"))

    def run_launcher(self, *args, env=None):
        # No -B: the launcher itself must keep bytecode out of the checkout.
        # Run from the worktree's root, as the commands are.
        r = subprocess.run([sys.executable,
                            os.path.join(self.ci, "vtrux_tools.py")]
                           + list(args), capture_output=True, text=True,
                           cwd=self.wt, env=self.env if env is None else env)
        return r.returncode, r.stdout + r.stderr

    def run_python(self, code, env=None):
        """code in a fresh Python with the scratch launcher's folder first on
        sys.path, as the harness imports it."""
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r)\n" % self.ci
                            + code], capture_output=True, text=True,
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

    def clear_record(self):
        if os.path.exists(self.record):
            os.remove(self.record)

    def no_bytecode(self):
        found = [d for d, _, _ in os.walk(self.checkout())
                 if os.path.basename(d) == "__pycache__"]
        self.assertEqual(found, [])

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
        for args in (["release"], ["build", "truck"], ["check-env"]):
            with self.subTest(args):
                rc, out = self.run_launcher(*args)
                self.assertEqual(rc, 1)
                self.assertIn(os.path.normpath(self.checkout()), out)
                self.assertIn("worktree add --detach ../shared-%s %s"
                              % (TAG, TAG), out)
                self.assertEqual(self.calls(), [])

    def test_runs_the_checkouts_cli(self):
        self.ready()
        rc, out = self.run_launcher("image", "a.bin", "b.bin")
        self.assertEqual(rc, 7, out)
        self.assertEqual(self.args_seen(out), ["image", "a.bin", "b.bin"])
        self.no_bytecode()

    # --project

    def test_release_and_build_get_the_project(self):
        self.ready()
        for args in (["release", "--jobs", "1"], ["build", "truck"],
                     ["build", "bridge", "--jobs", "1"], ["build"]):
            with self.subTest(args):
                rc, out = self.run_launcher(*args)
                self.assertEqual(rc, 7, out)
                seen = self.args_seen(out)
                self.assertEqual(seen[:-1], args + ["--project"], seen)
                self.assertTrue(same(seen[-1], self.proj), seen)

    def test_a_named_project_is_kept(self):
        self.ready()
        for args in (["release", "--project", "elsewhere"],
                     ["release", "--project=elsewhere", "--check-only"],
                     ["release", "--proj", "elsewhere"],
                     ["build", "truck", "--project", "elsewhere"],
                     ["build", "truck", "--proj=elsewhere"]):
            with self.subTest(args):
                rc, out = self.run_launcher(*args)
                self.assertEqual(rc, 7, out)
                self.assertEqual(self.args_seen(out), args)

    def test_other_commands_get_nothing_added(self):
        self.ready()
        rc, out = self.run_launcher("check-env")
        self.assertEqual(rc, 7, out)
        self.assertEqual(self.args_seen(out), ["check-env"])
        env = {k: v for k, v in self.env.items() if k.upper() not in IDF_NAMES}
        for args in (["digest", "--project"], ["image", "a.bin"]):
            with self.subTest(args):
                rc, out = self.run_launcher(*args, env=env)
                self.assertEqual(rc, 7, out)
                self.assertEqual(self.args_seen(out), args)

    # build, through the pinned tooling

    def test_build_runs_the_2a_commands(self):
        self.real()
        for v in VARIANTS:
            with self.subTest(v):
                self.clear_record()
                rc, out = self.run_launcher("build", v)
                self.assertEqual(rc, 0, out)
                folder = os.path.join(self.proj, "build", v)
                self.assertIn("BUILT", out)
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
        self.no_bytecode()

    def test_build_jobs(self):
        self.real()
        for args in (["--jobs", "1"], ["--jobs=3"]):
            with self.subTest(args):
                self.clear_record()
                rc, out = self.run_launcher("build", "witness", *args)
                self.assertEqual(rc, 0, out)
                n = self.calls()[1]["argv"]
                self.assertEqual(n[2:], ["-j", args[-1].split("=")[-1], "all"])
        for bad in ("0", "-2"):
            with self.subTest(bad):
                self.clear_record()
                rc, out = self.run_launcher("build", "witness", "--jobs", bad)
                self.assertEqual(rc, 1, out)
                self.assertIn("--jobs", out)
                self.assertNotIn("BUILT", out)
                self.assertEqual(self.calls(), [])
        # Not a number at all: argparse's usage error, exit 2, before
        # anything runs.
        rc, out = self.run_launcher("build", "witness", "--jobs", "x")
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("BUILT", out)
        self.assertEqual(self.calls(), [])

    def test_build_adds_no_epoch(self):
        self.real()
        rc, out = self.run_launcher("build", "bridge")
        self.assertEqual(rc, 0, out)
        self.assertEqual([x["epoch"] for x in self.calls()], [None, None])
        # A caller's own value is passed through, not replaced.
        self.clear_record()
        rc, out = self.run_launcher("build", "bridge",
                                    env=dict(self.env, SOURCE_DATE_EPOCH="5"))
        self.assertEqual(rc, 0, out)
        self.assertEqual([x["epoch"] for x in self.calls()], ["5", "5"])

    def test_unknown_or_missing_variant_is_refused(self):
        self.real()
        for args in (["build", "nonesuch"], ["build"], ["build", "Truck"]):
            with self.subTest(args):
                rc, out = self.run_launcher(*args)
                self.assertEqual(rc, 1, out)
                self.assertIn("the variants are: " + " ".join(VARIANTS), out)
                self.assertNotIn("BUILT", out)
                self.assertEqual(self.calls(), [])

    def test_failed_reconfigure_exits_1_not_built(self):
        self.real()
        for code in ("3", "1", "255"):
            with self.subTest(code):
                self.clear_record()
                rc, out = self.run_launcher("build", "diag",
                                            env=dict(self.env, IDF_RC=code))
                self.assertEqual(rc, 1, out)
                self.assertEqual([x["tool"] for x in self.calls()], ["idf"])
                self.assertIn("reconfigure failed", out)
                self.assertNotIn("BUILT", out)

    def test_failed_ninja_exits_1_not_built(self):
        self.real()
        for code in ("4", "1", "255"):
            with self.subTest(code):
                self.clear_record()
                rc, out = self.run_launcher("build", "diag",
                                            env=dict(self.env, NINJA_RC=code))
                self.assertEqual(rc, 1, out)
                self.assertEqual([x["tool"] for x in self.calls()],
                                 ["idf", "ninja"])
                self.assertIn("ninja failed", out)
                self.assertNotIn("BUILT", out)

    # the IDF environment (R32), through the pinned tooling

    def bad_envs(self):
        outside = os.path.join(self.d, "elsewhere")
        e = self.env

        def without(name):
            return {k: v for k, v in e.items() if k != name}
        yield "IDF_PATH unset", without("IDF_PATH"), "IDF_PATH is not set"
        yield "IDF_PATH empty", dict(e, IDF_PATH=""), "IDF_PATH is empty"
        yield ("IDF_TOOLS_PATH unset", without("IDF_TOOLS_PATH"),
               "IDF_TOOLS_PATH is not set")
        yield ("IDF_TOOLS_PATH empty", dict(e, IDF_TOOLS_PATH=""),
               "IDF_TOOLS_PATH is empty")
        yield ("IDF_TOOLS_PATH blank", dict(e, IDF_TOOLS_PATH="  "),
               "IDF_TOOLS_PATH is empty")
        yield ("IDF_TOOLS_PATH with a space after it",
               dict(e, IDF_TOOLS_PATH=IDF["tools"] + " "), "spaces")
        for name in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
            yield name + " unset", without(name), name + " is not set"
            yield name + " outside", dict(e, **{name: outside}), "not inside"
        # A tools folder whose name only starts like the real one's.
        yield ("ESP_ROM_ELF_DIR in a sibling",
               dict(e, ESP_ROM_ELF_DIR=IDF["tools"] + "-other"), "not inside")

    def test_build_refuses_a_wrong_idf_env(self):
        self.real()
        for what, env, says in self.bad_envs():
            with self.subTest(what):
                rc, out = self.run_launcher("build", "bridge", env=env)
                self.assertEqual(rc, 1, out)
                self.assertIn(says, out)
                self.assertNotIn("BUILT", out)
                self.assertEqual(self.calls(), [])

    def test_check_env(self):
        self.real()
        rc, out = self.run_launcher("check-env")
        self.assertEqual(rc, 0, out)
        self.assertIn("IDF ENVIRONMENT OK", out)
        for what, env, says in self.bad_envs():
            with self.subTest(what):
                rc, out = self.run_launcher("check-env", env=env)
                self.assertEqual(rc, 1, out)
                self.assertIn(says, out)
        self.no_bytecode()

    def test_check_idf_env_for_the_harness(self):
        self.real()
        code = "import vtrux_tools; vtrux_tools.check_idf_env(); print('OK')"
        rc, out = self.run_python(code)
        self.assertEqual((rc, out.strip()), (0, "OK"), out)
        for what, env, says in self.bad_envs():
            with self.subTest(what):
                rc, out = self.run_python(code, env=env)
                self.assertEqual(rc, 1, out)
                self.assertIn("vtrux_tools: ", out)
                self.assertIn(says, out)
                self.assertNotIn("OK", out.split())
        # tools_dir() names the checkout's shared/tools.
        rc, out = self.run_python("import vtrux_tools; "
                                  "print(vtrux_tools.tools_dir())")
        self.assertEqual(rc, 0, out)
        self.assertTrue(same(out.strip(), os.path.join(
            self.checkout(), "shared", "tools")), out)
        self.no_bytecode()

    def test_check_idf_env_refuses_another_vtrux_build(self):
        self.real()
        other = os.path.join(self.d, "other")
        write(os.path.join(other, "vtrux_build", "__init__.py"), "")
        write(os.path.join(other, "vtrux_build", "idfenv.py"),
              "class EnvError(ValueError): pass\n"
              "def check(environ=None): return {}\n")
        bad = dict(self.env, IDF_PATH="")
        rc, out = self.run_python(
            "sys.path.insert(0, %r); import vtrux_build\n"
            "import vtrux_tools; vtrux_tools.check_idf_env(); print('OK')"
            % other, env=bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("already imported", out)

    # the variants

    def test_variants_agree_with_cmakelists(self):
        with open(DATA, encoding="utf-8") as f:
            variants = json.load(f).get("variants")
        with open(CMAKELISTS, encoding="utf-8") as f:
            m = CMAKE_VARIANTS.search(f.read())
        self.assertIsNotNone(m, "no set(INTP_VARIANTS ...) in CMakeLists.txt")
        cmake = m.group(1).split()
        self.assertEqual(cmake, VARIANTS)
        self.assertIsInstance(variants, dict)
        self.assertEqual(list(variants), cmake)
        for name, opts in variants.items():
            self.assertEqual(opts, {"INTP_VARIANT": name}, name)


# ---------- --can-fail ----------

# (name, file, text in it, its replacement). Each text must be in its file
# exactly once, or the mutant is not made and the run fails.
MUTANTS = [
    ("release without --project", "launcher",
     "    return argv + [\"--project\", ROOT]\n", "    return argv\n"),
    ("build without --project", "launcher",
     "    if argv[:1] not in ([\"release\"], [\"build\"]):\n",
     "    if argv[:1] != [\"release\"]:\n"),
    ("an abbreviated --project not counted", "launcher",
     "        if len(opt) >= 3 and \"--project\".startswith(opt):\n",
     "        if opt == \"--project\":\n"),
    ("pin read beside the launcher", "launcher",
     "    path = os.path.join(ROOT, PIN_FILE)\n",
     "    path = os.path.join(os.path.dirname(ROOT), PIN_FILE)\n"),
    ("bytecode written", "launcher",
     "sys.dont_write_bytecode = True\n", "sys.dont_write_bytecode = False\n"),
    ("the tool's exit code dropped", "launcher",
     "    return cli.main(argv)\n", "    cli.main(argv)\n    return 0\n"),
    ("check_idf_env checks nothing", "launcher",
     "        idfenv.check(environ)\n", "        pass\n"),
    ("check_idf_env does not refuse", "launcher",
     "        raise SystemExit(\"vtrux_tools: %s\" % e)\n",
     "        print(\"vtrux_tools: %s\" % e)\n"),
    ("another vtrux_build accepted", "launcher",
     "            raise SystemExit(\"vtrux_tools: a vtrux_build from %s is "
     "already \"\n",
     "            if False: raise SystemExit(\"vtrux_tools: a vtrux_build from "
     "%s is already \"\n"),
    ("a variant missing from the data", "data",
     ",\n    \"slcan\": {\"INTP_VARIANT\": \"slcan\"}\n", "\n"),
    ("a variant's option misnamed", "data",
     "\"truck\": {\"INTP_VARIANT\": \"truck\"}",
     "\"truck\": {\"INTP_VARIAN\": \"truck\"}"),
    ("a variant configured as another", "data",
     "\"bridge\": {\"INTP_VARIANT\": \"bridge\"}",
     "\"bridge\": {\"INTP_VARIANT\": \"witness\"}"),
    ("two variants swapped", "data",
     "    \"bridge\": {\"INTP_VARIANT\": \"bridge\"},\n"
     "    \"witness\": {\"INTP_VARIANT\": \"witness\"},\n",
     "    \"witness\": {\"INTP_VARIANT\": \"witness\"},\n"
     "    \"bridge\": {\"INTP_VARIANT\": \"bridge\"},\n"),
]


def can_fail():
    sources = {}
    for key, path in (("launcher", LAUNCHER), ("data", DATA)):
        with open(path, encoding="utf-8") as f:
            sources[key] = f.read()
    folder = os.path.join(RUN_DIR, "mutants")
    os.makedirs(folder)
    results, bad = [], False
    for i, (name, key, old, new) in enumerate(
            [("control", None, "", "")] + MUTANTS):
        if old and sources[key].count(old) != 1:
            results.append((name, "NOT MADE: the text is in the %s %d times, "
                                  "not once" % (key, sources[key].count(old))))
            bad = True
            continue
        d = os.path.join(folder, "m%02d" % i)
        paths = {"launcher": os.path.join(d, "vtrux_tools.py"),
                 "data": os.path.join(d, "vtrux_build.json")}
        for k, p in paths.items():
            write(p, sources[k].replace(old, new) if k == key else sources[k])
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                           env=dict(os.environ,
                                    VTRUX_TOOLS_UNDER_TEST=paths["launcher"],
                                    VTRUX_BUILD_JSON_UNDER_TEST=paths["data"]),
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
