"""Starts the shared build tooling (shared/tools) at the version this project
pins, from its read-only checkout ../shared-<tag>/ beside this worktree's
root (shared/tools SPEC R23).

    py -3.14 -B tools/vtrux_tools.py release [--check-only] [--jobs N]
    py -3.14 -B tools/vtrux_tools.py image <file.bin>...
    py -3.14 -B tools/vtrux_tools.py digest <folder>...

A release build runs from an exported ESP-IDF shell, in this repo's root,
with REVERSE_IT_DIR set; throttled, from an Idle shell with --jobs 1
(AGENTS.md "Builds during wall-clock tests").

It reads only the tag from shared_pins.json's "shared/tools" entry, so that
a missing checkout is named before any shared code runs. Every check of the
pin (the checkout's location, digest, commit and tag; R23, R16) is the pinned
tooling's own, in its vtrux_build/pin.py.

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def main(argv=None):
    sys.path.insert(0, tools_dir())
    from vtrux_build import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
