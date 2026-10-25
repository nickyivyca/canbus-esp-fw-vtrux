"""A pinned shared/tools checkout for the tests (SPEC R23): a small git
repository at <parent>/shared-<tag>/, its shared/tools/ committed and tagged,
and the pin that matches it."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scratch                                    # noqa: E402
from vtrux_build.digest import component_digest   # noqa: E402

TAG = "2026.01.01"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
TOOL_FILES = {"vtrux_build/__init__.py": "x = 1\n", "README.md": "tools\n"}


def git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "core.autocrlf=false"]
                          + list(args), check=True, env=GIT_ENV,
                          capture_output=True, text=True).stdout.strip()


def checkout(parent, tag=TAG, files=None):
    """<parent>/shared-<tag>/: a git repository whose one commit holds
    shared/tools/ (files, TOOL_FILES by default) and is tagged tag. Returns
    (its shared/tools folder, the pin that matches it)."""
    co = os.path.join(parent, "shared-" + tag)
    tools = os.path.join(co, "shared", "tools")
    for rel, text in (files or TOOL_FILES).items():
        scratch.write(os.path.join(tools, *rel.split("/")), text)
    git(co, "init", "-q")
    git(co, "add", "-A")
    git(co, "commit", "-qm", "shared")
    git(co, "tag", tag)
    return tools, pin_of(tools, tag)


def pin_of(tools, tag=TAG):
    """The pin for the shared/tools folder tools at its checkout's HEAD."""
    co = os.path.dirname(os.path.dirname(tools))
    return {"tag": tag, "commit": git(co, "rev-parse", "HEAD"),
            "digest": component_digest(tools)}


def pins_text(pin, **others):
    """shared_pins.json holding pin as the shared/tools entry, plus others
    ({component: entry})."""
    data = {"shared/tools": pin}
    data.update(others)
    return json.dumps(data, indent=2) + "\n"
