"""Starts the shared build tooling (shared/tools) at the version this project
pins, from its read-only checkout ../shared-<tag>/ beside this worktree's
root (shared/tools SPEC R23).

    python3.13 -B charge-interposer/vtrux_tools.py check-env
    python3.13 -B charge-interposer/vtrux_tools.py release [--check-only] [--jobs N]
    python3.13 -B charge-interposer/vtrux_tools.py build <variant> [--jobs N]
    python3.13 -B charge-interposer/vtrux_tools.py image <file.bin>...
    python3.13 -B charge-interposer/vtrux_tools.py digest <folder>...

The Python command is the one local-paths.md names: python3.13 on
madhouse-debian, py -3.14 on the Windows machines. Run from this repo's
root, in a shell exported for the interposer's ESP-IDF, v5.4.2, with
IDF_TOOLS_PATH set before the export: on madhouse-debian a bash shell,
`export IDF_TOOLS_PATH="$HOME/.espressif-5.4.2"` then
`. ~/esp/esp-idf-v5.4.2/export.sh`; on Windows a PowerShell (each
machine's export lines are in its local-paths.md). While a wall-clock test
is running, follow AGENTS.md "Builds during wall-clock tests": the build
waits, or runs in the machine's throttled form where that section allows
one.

check-env  the IDF environment check (shared/tools R32): IDF_PATH set and
           not empty, IDF_TOOLS_PATH set, not empty and with no spaces
           around it, the IDF's Python environment and ROM ELF folder
           inside IDF_TOOLS_PATH. release and build run it themselves. A
           bench build made with idf.py directly, such as a flash, runs it
           first, in the same shell (R32).
release    the release build (that spec's section 3). It builds the CMake
           default variant, truck (user, 2026-11-03: a release build passes
           no -D). It needs REVERSE_IT_DIR, where it writes its record; a
           stand-in build that is never flashed points REVERSE_IT_DIR at a
           stand-in folder.
build      a dev or bench build of one variant (R31), in
           firmware/build/<variant>: idf.py reconfigure with the variant's
           -D options, then ninja there, with -j N when --jobs is given.
           Nothing is added to the caller's environment. The variants, and
           the options each is configured with, are firmware/vtrux_build.json's
           "variants"; firmware/CMakeLists.txt's INTP_VARIANTS line names the
           same six, and test_vtrux_tools.py checks that the two agree.
image, digest
           as the tooling has them.

The project is charge-interposer/firmware/, the IDF build root beside this
script, which holds shared_pins.json, vtrux_build.json and CMakeLists.txt;
release and build are given it as --project unless the command line names
one.

It reads only the tag from firmware/shared_pins.json's "shared/tools" entry,
so that a missing checkout is named before any shared code runs. Every check
of the pin (the checkout's location, digest, commit and tag; R23, R16) is
the pinned tooling's own, in its vtrux_build/pin.py.

tools_dir() and check_idf_env() are also imported by the tester's harness
(fw_harness/refusal_pairs.py, fw_harness/refusal_pairs_runs.py).
check_idf_env() is the pinned tooling's R32 check, raising SystemExit; it
goes once nothing imports it (reviewer, 2026-11-08).

Not named vtrux_build.py: as the script's own folder is first on sys.path,
that name would shadow the tooling's vtrux_build package.
"""

import json
import os
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


def _use_tools():
    """Put the pinned tooling first on sys.path. SystemExit if a vtrux_build
    from anywhere else is already imported: it would be used instead."""
    tools = tools_dir()
    mod = sys.modules.get("vtrux_build")
    if mod is not None:
        where = os.path.dirname(os.path.abspath(getattr(mod, "__file__", "")
                                                or os.curdir))
        if (os.path.normcase(where) != os.path.normcase(
                os.path.join(os.path.abspath(tools), "vtrux_build"))):
            raise SystemExit("vtrux_tools: a vtrux_build from %s is already "
                             "imported, not the pinned one in %s"
                             % (where, tools))
    if tools not in sys.path:
        sys.path.insert(0, tools)


def check_idf_env(environ=None):
    """The pinned tooling's IDF environment check (R32, vtrux_build.idfenv)
    on environ (default: this process's environment). SystemExit with its
    refusal if it fails."""
    _use_tools()
    from vtrux_build import idfenv
    try:
        idfenv.check(environ)
    except idfenv.EnvError as e:
        raise SystemExit("vtrux_tools: %s" % e)


def _with_project(argv):
    """argv, with --project ROOT added to a release or build that names
    none: the tooling's own default is the current folder, which is the repo
    root. argparse takes an abbreviation (--proj) as the option, so one
    counts."""
    if argv[:1] not in (["release"], ["build"]):
        return argv
    for a in argv[1:]:
        opt = a.split("=", 1)[0]
        if len(opt) >= 3 and "--project".startswith(opt):
            return argv
    return argv + ["--project", ROOT]


def main(argv=None):
    argv = _with_project(sys.argv[1:] if argv is None else list(argv))
    _use_tools()
    from vtrux_build import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
