"""The acceptance record's start (ACCEPT-SPEC A13), and the runner's Python
guard.

start() makes the record folder before anything else the runner does --
before any lock, tree or build -- so every console line has a home:

    <runs>/accept_<step>_<YYYYmmddTHHMMSS>_<hex4>/
        console.txt     every line the runner says, from the first

The first console line names the folder. The folder is made with mkdir, so
it is never reused. A runs folder inside a git worktree is refused: this
repo is public, and run records stay on reverse-it's held paths (A13; there
the adapter names the ruling that holds its runs folder).

The runner itself needs Python >= 3.11 (tomllib); WSL's 3.10 is a check
host, never a runner host (reviewer, 2026-11-01). The package refuses to
import on an older Python (vtrux_bench/accept/__init__.py), before tomllib
is reached.

RULES: each is switched off by a mutant in tests/test_accept_record.py,
which must then fail.
"""

import datetime
import os
import secrets
import subprocess

from ..run.model import ID_RE

RULES = {k: True for k in ("folder_first", "console_tee", "exclusive", "off_git",
                           "step_id", "python_guard")}


class RecordError(Exception):
    pass


def python_refusal(version):
    """Why accept cannot run on this Python version, or None."""
    if RULES["python_guard"] and tuple(version[:2]) < (3, 11):
        return ("the acceptance runner needs Python >= 3.11 (tomllib); this is "
                "%d.%d. Run it on NICKY-XPS (3.14) or madhouse (3.13): WSL is a "
                "check host, never a runner host" % tuple(version[:2]))
    return None


def in_git_worktree(path, ceiling=None):
    """True if path (or the nearest folder above it that exists) is inside
    a git worktree. ceiling, for tests: GIT_CEILING_DIRECTORIES."""
    p = os.path.abspath(path)
    while not os.path.isdir(p):
        up = os.path.dirname(p)
        if up == p:
            return False
        p = up
    env = dict(os.environ)
    if ceiling:
        env["GIT_CEILING_DIRECTORIES"] = os.path.abspath(ceiling)
    r = subprocess.run(["git", "-C", p, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0


class Record:
    """An open acceptance record: say() prints a line and writes it to
    console.txt."""

    def __init__(self, path, record_id, out):
        self.path = path
        self.id = record_id
        self.out = out
        self._console = open(os.path.join(path, "console.txt"), "a",
                             encoding="utf-8", newline="\n")

    def say(self, line):
        self.out(line)
        if RULES["console_tee"] and self._console:
            self._console.write(line + "\n")
            self._console.flush()

    def close(self):
        if self._console:
            self._console.close()
            self._console = None


def start(runs_root, step_id, out=None, ceiling=None, record_id=None):
    """Make and open the record folder; its path is the first line said."""
    out = out or (lambda s: print(s, flush=True))
    if RULES["step_id"] and (not isinstance(step_id, str) or not ID_RE.match(step_id)
                             or step_id in (".", "..")):
        raise RecordError("the step id %r cannot name a folder" % (step_id,))
    if RULES["off_git"] and in_git_worktree(runs_root, ceiling):
        raise RecordError("the runs folder %s is inside a git worktree: run records "
                          "are never published (A13)" % runs_root)
    os.makedirs(runs_root, exist_ok=True)
    rid = record_id or "accept_%s_%s_%s" % (
        step_id, datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), secrets.token_hex(2))
    path = os.path.join(runs_root, rid)
    try:
        if RULES["exclusive"]:
            os.mkdir(path)
        else:
            os.makedirs(path, exist_ok=True)
    except FileExistsError:
        raise RecordError("the record folder %s is already there: a record "
                          "folder is never reused (A13)" % path)
    rec = Record(path, rid, out)
    if RULES["folder_first"]:
        rec.say("acceptance record: %s" % path)
    return rec
