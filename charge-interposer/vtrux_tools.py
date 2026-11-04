"""Starts the shared build tooling (shared/tools) at the version this project
pins, from its read-only checkout ../shared-<tag>/ beside this worktree's
root (shared/tools SPEC R23), and makes the interposer's dev builds.

    py -3.14 -B charge-interposer/vtrux_tools.py release [--check-only] [--jobs N]
    py -3.14 -B charge-interposer/vtrux_tools.py build <variant> [--jobs N]
    py -3.14 -B charge-interposer/vtrux_tools.py image <file.bin>...
    py -3.14 -B charge-interposer/vtrux_tools.py digest <folder>...

Run from this repo's root, in a PowerShell exported for the interposer's
ESP-IDF (the export lines are per machine, in local-paths.md). The project
is charge-interposer/firmware/, the IDF build root beside this script, which
holds shared_pins.json and CMakeLists.txt. Throttled, from an Idle shell,
with --jobs 1 (AGENTS.md "Builds during wall-clock tests").

release   the pinned tooling's release build (that spec's section 3), given
          --project charge-interposer/firmware unless the command line names
          one. It builds the CMake default variant, truck (user, 2026-11-03:
          the tooling passes no -D). It needs REVERSE_IT_DIR, where it writes
          its record; a stand-in build that is never flashed points
          REVERSE_IT_DIR at a stand-in folder.
build     a dev or bench build of one variant (shared/tools R14), in
          firmware/build/<variant>: `idf.py -B build/<variant> -D
          INTP_VARIANT=<variant> reconfigure`, then ninja there, with -j N
          when --jobs is given. Nothing is added to the caller's
          environment. The variants are the ones firmware/CMakeLists.txt
          lists in its INTP_VARIANTS line.
image, digest
          handed to the pinned tooling as they are.

Before release or build hands anything to ESP-IDF, the environment is
checked: IDF_TOOLS_PATH is set and not empty, and IDF_PYTHON_ENV_PATH and
ESP_ROM_ELF_DIR are both set and inside it. The interposer's ESP-IDF has
its own IDF_TOOLS_PATH beside gen-inhibit's ~/.espressif, and with the
variable unset both the export and the release tooling fall back to
~/.espressif, so a shell exported that way would build with the other
install's Python environment and tools. idf_component.yml already pins the
ESP-IDF version itself.

It reads only the tag from firmware/shared_pins.json's "shared/tools" entry,
so that a missing checkout is named before any shared code runs. Every check
of the pin (the checkout's location, digest, commit and tag; R23, R16) is
the pinned tooling's own, in its vtrux_build/pin.py. build does not use the
pinned tooling.

Not named vtrux_build.py: as the script's own folder is first on sys.path,
that name would shadow the tooling's vtrux_build package.
"""

import json
import os
import re
import shutil
import subprocess
import sys

# A __pycache__ written into the checkout would change its R7 digest, which
# the pin check then refuses.
sys.dont_write_bytecode = True

# The project, charge-interposer/firmware/: the IDF build root, which holds
# shared_pins.json. The pinned checkout is beside the git worktree's root,
# not this folder.
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware")
PIN_FILE = "shared_pins.json"
COMPONENT = "shared/tools"
VARIANTS_LINE = re.compile(r"^\s*set\(\s*INTP_VARIANTS\s+([^)]*)\)", re.M)


def _tag():
    path = os.path.join(ROOT, PIN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            tag = json.load(f)[COMPONENT]["tag"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit("vtrux_tools: no %r tag in %s (%s: %s)"
                         % (COMPONENT, path, type(e).__name__, e))
    if not isinstance(tag, str) or not tag:
        raise SystemExit("vtrux_tools: the %r pin in %s names no tag"
                         % (COMPONENT, path))
    return tag


def tools_dir():
    """shared/tools in the pinned checkout. SystemExit, naming the command
    that creates it, if it is missing."""
    tag = _tag()
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    top = r.stdout.strip()
    if r.returncode != 0 or not top:
        raise SystemExit("vtrux_tools: %s is not in a git worktree" % ROOT)
    checkout = os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag))
    tools = os.path.join(checkout, "shared", "tools")
    if not os.path.isdir(os.path.join(tools, "vtrux_build")):
        raise SystemExit(
            "vtrux_tools: the pinned checkout %s is missing. Create it, "
            "read-only and detached at the pin's tag, with:\n"
            '    git -C "%s" worktree add --detach ../shared-%s %s'
            % (checkout, top, tag, tag))
    return tools


def _inside(path, folder):
    path = os.path.normcase(os.path.abspath(path))
    folder = os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # another drive
        return False


def check_idf_env(environ=None):
    """SystemExit unless IDF_TOOLS_PATH is set and not empty and
    IDF_PYTHON_ENV_PATH and ESP_ROM_ELF_DIR are both inside it."""
    environ = os.environ if environ is None else environ
    tools = environ.get("IDF_TOOLS_PATH", "")
    if not tools.strip():
        raise SystemExit(
            "vtrux_tools: IDF_TOOLS_PATH is %s. The interposer's ESP-IDF "
            "has its own tools folder; set IDF_TOOLS_PATH to it before the "
            "export (local-paths.md), or ESP-IDF falls back to ~/.espressif, "
            "the other install's" % ("empty" if "IDF_TOOLS_PATH" in environ
                                     else "not set"))
    for name in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
        value = environ.get(name, "")
        if not value:
            raise SystemExit("vtrux_tools: %s is not set; run this from a "
                             "shell exported for the interposer's ESP-IDF"
                             % name)
        if not _inside(value, tools):
            raise SystemExit(
                "vtrux_tools: %s is %s, not inside IDF_TOOLS_PATH %s: this "
                "shell was exported with another IDF_TOOLS_PATH"
                % (name, value, tools))


def variants():
    """The variants firmware/CMakeLists.txt lists in its INTP_VARIANTS
    line."""
    path = os.path.join(ROOT, "CMakeLists.txt")
    try:
        with open(path, encoding="utf-8") as f:
            m = VARIANTS_LINE.search(f.read())
    except OSError as e:
        raise SystemExit("vtrux_tools: cannot read %s (%s)" % (path, e))
    names = m.group(1).split() if m else []
    if not names:
        raise SystemExit("vtrux_tools: no set(INTP_VARIANTS ...) line in %s"
                         % path)
    return names


def _jobs(text):
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise SystemExit("vtrux_tools: --jobs must be a whole number of at "
                         "least 1, not %r" % text)
    return n


def _run(argv):
    print("vtrux_tools: " + " ".join(argv), flush=True)
    return subprocess.run(argv).returncode


def build(args):
    """The build command: a dev or bench build of one variant. Returns the
    exit code."""
    usage = "usage: vtrux_tools.py build <variant> [--jobs N]"
    variant, jobs, rest = None, None, list(args)
    while rest:
        a = rest.pop(0)
        if a == "--jobs" and rest:
            jobs = _jobs(rest.pop(0))
        elif a.startswith("--jobs="):
            jobs = _jobs(a.split("=", 1)[1])
        elif not a.startswith("-") and variant is None:
            variant = a
        else:
            raise SystemExit("vtrux_tools: %r not understood\n%s" % (a, usage))
    known = variants()
    if variant not in known:
        raise SystemExit("vtrux_tools: %s\nthe variants are: %s"
                         % (usage if variant is None
                            else "no variant %r" % variant, " ".join(known)))
    check_idf_env()
    env_path = os.environ["IDF_PYTHON_ENV_PATH"]
    py = os.path.join(env_path, "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    idf_path = os.environ.get("IDF_PATH", "")
    idf_py = os.path.join(idf_path, "tools", "idf.py")
    for p in (py, idf_py):
        if not os.path.isfile(p):
            raise SystemExit("vtrux_tools: %s does not exist; the ESP-IDF "
                             "environment is not the one exported" % p)
    ninja = shutil.which("ninja")
    if not ninja:
        raise SystemExit("vtrux_tools: ninja is not on PATH; run this from "
                         "an exported ESP-IDF shell")
    folder = os.path.join(ROOT, "build", variant)
    rc = _run([py, idf_py, "-C", ROOT, "-B", folder,
               "-D", "INTP_VARIANT=" + variant, "reconfigure"])
    if rc != 0:
        print("vtrux_tools: reconfigure failed (exit %d)" % rc)
        return rc
    rc = _run([ninja, "-C", folder]
              + (["-j", str(jobs)] if jobs else []) + ["all"])
    if rc != 0:
        print("vtrux_tools: build failed (exit %d)" % rc)
        return rc
    print("vtrux_tools: %s" % os.path.join(folder,
                                           "interposer_%s.bin" % variant))
    return 0


def _with_project(argv):
    """argv, with --project ROOT added to a release that names none: the
    tooling's own default is the current folder, which is the repo root.
    argparse takes an abbreviation (--proj) as the option, so one counts."""
    if argv[:1] != ["release"]:
        return argv
    for a in argv[1:]:
        opt = a.split("=", 1)[0]
        if len(opt) >= 3 and "--project".startswith(opt):
            return argv
    return argv + ["--project", ROOT]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["build"]:
        return build(argv[1:])
    if argv[:1] == ["release"]:
        check_idf_env()
    argv = _with_project(argv)
    sys.path.insert(0, tools_dir())
    from vtrux_build import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
