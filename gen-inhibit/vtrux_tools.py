"""Starts the shared build tooling (shared/tools) at the version this project
pins, from its read-only checkout ../shared-<tag>/ beside this worktree's
root (shared/tools SPEC R23).

    py -3.14 -B gen-inhibit/vtrux_tools.py release [--check-only] [--jobs N]
    py -3.14 -B gen-inhibit/vtrux_tools.py image <file.bin>...
    py -3.14 -B gen-inhibit/vtrux_tools.py digest <folder>...

A release build runs from an exported ESP-IDF shell, in this repo's root,
with REVERSE_IT_DIR set; throttled, from an Idle shell, as
`py -3.14 -B gen-inhibit/vtrux_tools.py release --jobs 1` (AGENTS.md
"Builds during wall-clock tests"). The project is gen-inhibit/firmware/,
the IDF build root beside this script, which holds shared_pins.json and
CMakeLists.txt; release is given it as --project unless the command line
names one.

It reads only the tag from firmware/shared_pins.json's "shared/tools"
entry, so that a missing checkout is named before any shared code runs.
Every check of the pin (the checkout's location, digest, commit and tag;
R23, R16) is the pinned tooling's own, in its vtrux_build/pin.py.

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

# The project, gen-inhibit/firmware/: the IDF build root, which holds
# shared_pins.json. The pinned checkout is beside the git worktree's root, not
# this folder.
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
    argv = _with_project(sys.argv[1:] if argv is None else list(argv))
    sys.path.insert(0, tools_dir())
    from vtrux_build import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
