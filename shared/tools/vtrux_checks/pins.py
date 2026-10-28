"""R30, pins stay in the board: no project object read the board's include/board_pins.h unless
its source is on the project's list.

    python -B -m vtrux_checks pins --build <project>/build

SPEC R30 (shared/main 2f72a20) and shared/boards/SPEC.md B6. The board is the project's one
shared/boards/* component, in managed_components/ (boarddata.py), and its header is
include/board_pins.h in that folder. The project's list of stock files that still use pins is
"stock_files_using_pins" in vtrux_checks.json in the project root (project_description.json's
project_path), paths from the project root with "/":

    {"stock_files_using_pins": ["main/can.c", ...]}

From ninja's dependency records (.ninja_deps) in every CMake build folder under build/, each
object that read the header is traced to its source, the first explicit input of the build
statement in build.ninja that writes it. It fails when:

  - an object compiled from the project root (under it, minus the build folder and
    managed_components/) read the header and its source is not on the list;
  - an object that read the header has no build statement, or one with no input;
  - no object at all read the header: B6 has the board's own board.c include it, so a build in
    which nothing did is one whose records this check cannot see the header in;
  - the list is missing (an empty list is a list), is not a list of paths from the project root,
    names a path twice, or names a file that does not exist.

A listed file whose objects read no pins is printed, not failed: it may leave the list (B6).

Release builds only; any session may run it. The check does not tell them apart: run it on a
finished release build.
"""

import json
import os

from vtrux_build.builddir import BuildDir, BuildDirError

from . import boarddata
from .scope import build_folders, norm, under

DATA_FILE = "vtrux_checks.json"
KEY = "stock_files_using_pins"
HEADER = ("include", "board_pins.h")


class PinsError(ValueError):
    pass


def read_list(project):
    """[path from the project root] from vtrux_checks.json, checked."""
    p = os.path.join(project, DATA_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise PinsError("%s cannot be read (%s)" % (DATA_FILE, e))
    lst = data.get(KEY) if isinstance(data, dict) else None
    if not isinstance(lst, list):
        raise PinsError("%s has no %r list" % (DATA_FILE, KEY))
    seen = set()
    for x in lst:
        if not isinstance(x, str) or not x or "\\" in x or x.startswith("/") or os.path.isabs(x) \
                or any(part in ("", ".", "..") for part in x.split("/")):
            raise PinsError("%s %r: %r is not a path from the project root with /" % (DATA_FILE, KEY, x))
        k = os.path.normcase(x)
        if k in seen:
            raise PinsError("%s %r: %s is named twice" % (DATA_FILE, KEY, x))
        seen.add(k)
        if not os.path.isfile(os.path.join(project, x)):
            raise PinsError("%s %r: %s is not a file in the project" % (DATA_FILE, KEY, x))
    return lst


def readers(bd, header):
    """[(object, source or None)] for every object in the folder's deps log that read header."""
    sources = {}
    for e in bd.ninja_edges():
        for o in e.outputs + e.implicit_outputs:
            sources[norm(bd.resolve(o))] = norm(bd.resolve(e.inputs[0])) if e.inputs else None
    out = []
    for obj, deps in sorted(bd.ninja_deps().items()):
        if any(norm(bd.resolve(d)) == header for d in deps):
            out.append((norm(bd.resolve(obj)), sources.get(norm(bd.resolve(obj)), None)))
    return out


def collect(build):
    folders = build_folders(build)
    try:
        project = BuildDir(folders[0]).description()["project_path"]
    except KeyError:
        raise PinsError("project_description.json has no project_path")
    name, folder = boarddata.find(project)
    listed = read_list(project)
    header = norm(os.path.join(folder, *HEADER))
    root, out_dir, mdir = norm(project), norm(folders[0]), norm(os.path.join(project, "managed_components"))
    found = []                          # (folder label, object, source, from the project root)
    for f in folders:
        for obj, src in readers(BuildDir(f), header):
            ours = src is not None and under(src, root) and not under(src, out_dir) and not under(src, mdir)
            found.append((os.path.relpath(f, project).replace(os.sep, "/"), obj, src, ours))
    return {"project": project, "board": name, "header": header, "listed": listed, "found": found}


def judge(facts):
    """([failure], [listed file that read no pins])."""
    root = norm(facts["project"])
    rel = lambda p: os.path.relpath(p, root).replace(os.sep, "/")
    listed = {os.path.normcase(x) for x in facts["listed"]}
    bad, used = [], set()
    if not facts["found"]:
        bad.append("no object read the board's include/board_pins.h (B6: the board's board.c includes it); "
                   "the records do not show the header")
    for folder, obj, src, ours in facts["found"]:
        if src is None:
            bad.append("%s: %s read board_pins.h and has no build statement with an input" % (folder, obj))
        elif ours:
            r = rel(src)
            used.add(os.path.normcase(r))
            if os.path.normcase(r) not in listed:
                bad.append("%s: %s read board_pins.h and is not on %s" % (folder, r, KEY))
    unused = [x for x in facts["listed"] if os.path.normcase(x) not in used]
    return bad, unused


def main(args):
    try:
        f = collect(args.build)
    except (OSError, BuildDirError, PinsError, boarddata.BoardError) as e:
        print("FAIL R30: cannot read the build, its board or its list: %s" % e)
        return 1
    root = norm(f["project"])
    rel = lambda p: os.path.relpath(p, root).replace(os.sep, "/") if p else "?"
    print("R30 pins: board %s, header %s, %d file(s) on %s" % (f["board"], rel(f["header"]), len(f["listed"]), KEY))
    for folder, obj, src, ours in f["found"]:
        print("  %-16s %s read it (%s)" % (folder, rel(src), "project" if ours else "not the project's"))
    bad, unused = judge(f)
    for x in unused:
        print("  listed, read no pins: %s" % x)
    if bad:
        print("FAIL R30: %d failure(s)" % len(bad))
        for b in bad[:20]:
            print("    %s" % b)
        return 1
    print("PASS R30")
    return 0
