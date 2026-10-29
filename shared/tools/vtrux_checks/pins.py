"""R30, pins stay in the board: no project object read the board's include/board_pins.h unless
its source is on the project's list, and no object of another shared component unless its source
is on that component's own list.

    python -B -m vtrux_checks pins --build <project>/build

SPEC R30 (shared/main 2f72a20, amended 1b4ab4d) and shared/boards/SPEC.md B6. The board is the
project's one shared/boards/* component, in managed_components/ (boarddata.py), and its header
is include/board_pins.h in that folder. The project's list of stock files that still use pins is
"stock_files_using_pins" in vtrux_checks.json in the project root (project_description.json's
project_path), paths from the project root with "/":

    {"stock_files_using_pins": ["main/can.c", ...]}

The other shared components are the dependencies.lock entries whose source is a shared/<name>
folder (a git source whose path is exactly shared/<name>; shared/boards/* is the board). Each is
read from its folder in managed_components/, and its own list (shared/wican/SPEC.md W10) is
"files_using_pins" in pins.json in that folder, paths from the component's folder with "/":

    {"files_using_pins": ["main.c", ...]}

A component without pins.json has an empty list.

From ninja's dependency records (.ninja_deps) in every CMake build folder under build/, each
object that read the header is traced to its source, the first explicit input of the build
statement in build.ninja that writes it. It fails when:

  - an object compiled from the project root (under it, minus the build folder and
    managed_components/) read the header and its source is not on the list;
  - an object compiled from another shared component's folder read the header and its source is
    not on that component's list;
  - an object that read the header has no build statement, or one with no input;
  - no object at all read the header: B6 has the board's own board.c include it, so a build in
    which nothing did is one whose records this check cannot see the header in;
  - the list is missing (an empty list is a list), is not a list of paths from the project root,
    names a path twice, or names a file that does not exist; or a component's pins.json, when
    there is one, is not such a list from the component's folder;
  - a shared component comes from a local folder, or is not in managed_components/, so the
    component built is not shown to be the one whose list is read.

A listed file whose objects read no pins is printed, not failed: it may leave the list (B6).

Release builds only; any session may run it. The check does not tell them apart: run it on a
finished release build.
"""

import json
import os
import re

from vtrux_build import lockfile
from vtrux_build.builddir import BuildDir, BuildDirError

from . import boarddata
from .scope import build_folders, norm, under

DATA_FILE = "vtrux_checks.json"
KEY = "stock_files_using_pins"
HEADER = ("include", "board_pins.h")
COMPONENT_FILE = "pins.json"
COMPONENT_KEY = "files_using_pins"
_SHARED_GIT = re.compile(r"^shared/[^/]+$")
_SHARED_LOCAL = re.compile(r"(?:^|/)shared/[^/]+$")


class PinsError(ValueError):
    pass


def _read(root, data_file, key, from_, in_, optional=False):
    """[path from root] from root/data_file's key, checked; [] when optional and there is no file."""
    p = os.path.join(root, data_file)
    if optional and not os.path.lexists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise PinsError("%s cannot be read (%s)" % (data_file, e))
    lst = data.get(key) if isinstance(data, dict) else None
    if not isinstance(lst, list):
        raise PinsError("%s has no %r list" % (data_file, key))
    seen = set()
    for x in lst:
        if not isinstance(x, str) or not x or "\\" in x or x.startswith("/") or os.path.isabs(x) \
                or any(part in ("", ".", "..") for part in x.split("/")):
            raise PinsError("%s %r: %r is not a path from %s with /" % (data_file, key, x, from_))
        k = os.path.normcase(x)
        if k in seen:
            raise PinsError("%s %r: %s is named twice" % (data_file, key, x))
        seen.add(k)
        if not os.path.isfile(os.path.join(root, x)):
            raise PinsError("%s %r: %s is not a file in %s" % (data_file, key, x, in_))
    return lst


def read_list(project):
    """[path from the project root] from vtrux_checks.json, checked."""
    return _read(project, DATA_FILE, KEY, "the project root", "the project")


def read_component_list(name, folder):
    """[path from the component's folder] from its pins.json, checked; [] without one."""
    try:
        return _read(folder, COMPONENT_FILE, COMPONENT_KEY, "the component's folder", "the component", optional=True)
    except PinsError as e:
        raise PinsError("the shared component %s: %s" % (name, e))


def shared_components(project):
    """[(lock name, folder in managed_components/)] of the shared components other than the board."""
    try:
        entries = lockfile.read(os.path.join(project, "dependencies.lock"))
    except (OSError, lockfile.LockError) as e:
        raise PinsError("dependencies.lock cannot be read (%s)" % e)
    out = []
    for name, e in sorted(entries.items()):
        src = e.get("source", {})
        path = src.get("path", "").replace("\\", "/").rstrip("/")
        if path.startswith("./"):
            path = path[2:]
        if src.get("type") == "local" and _SHARED_LOCAL.search(path):
            raise PinsError("the shared component %s comes from a local folder (%s), not downloaded into "
                            "managed_components/" % (name, path))
        if src.get("type") != "git" or not _SHARED_GIT.match(path):
            continue
        folder = os.path.join(project, "managed_components", lockfile.managed_dir_name(name))
        if not os.path.isdir(folder):
            raise PinsError("the shared component %s is not in managed_components/ (%s)" % (
                name, os.path.relpath(folder, project).replace(os.sep, "/")))
        out.append((name, folder))
    return out


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
    components = [(n, norm(d), read_component_list(n, d)) for n, d in shared_components(project)]
    header = norm(os.path.join(folder, *HEADER))
    root, out_dir, mdir = norm(project), norm(folders[0]), norm(os.path.join(project, "managed_components"))
    found = []      # (folder label, object, source, owner: "" the project's, a component's name, None neither)
    for f in folders:
        for obj, src in readers(BuildDir(f), header):
            owner = None
            if src is not None and under(src, root) and not under(src, out_dir) and not under(src, mdir):
                owner = ""
            elif src is not None:
                owner = next((n for n, d, _l in components if under(src, d)), None)
            found.append((os.path.relpath(f, project).replace(os.sep, "/"), obj, src, owner))
    return {"project": project, "board": name, "header": header, "listed": listed, "found": found,
            "components": components}


def judge(facts):
    """([failure], [listed file that read no pins]), every path from the project root."""
    root = norm(facts["project"])
    rel = lambda p, r=root: os.path.relpath(p, r).replace(os.sep, "/")
    lists = {"": (root, facts["listed"], KEY)}
    for n, d, lst in facts["components"]:
        lists[n] = (d, lst, "%s's %s %s" % (n, COMPONENT_FILE, COMPONENT_KEY))
    bad, used = [], set()
    if not facts["found"]:
        bad.append("no object read the board's include/board_pins.h (B6: the board's board.c includes it); "
                   "the records do not show the header")
    for folder, obj, src, owner in facts["found"]:
        if src is None:
            bad.append("%s: %s read board_pins.h and has no build statement with an input" % (folder, obj))
        elif owner is not None:
            d, lst, what = lists[owner]
            k = (owner, os.path.normcase(rel(src, d)))
            used.add(k)
            if k[1] not in {os.path.normcase(x) for x in lst}:
                bad.append("%s: %s read board_pins.h and is not on %s" % (folder, rel(src), what))
    unused = [rel(os.path.join(d, x)) for owner, (d, lst, _w) in sorted(lists.items())
              for x in lst if (owner, os.path.normcase(x)) not in used]
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
    for n, d, lst in f["components"]:
        print("  shared component %s: %d file(s) on its %s %s" % (n, len(lst), COMPONENT_FILE, COMPONENT_KEY))
    for folder, obj, src, owner in f["found"]:
        print("  %-16s %s read it (%s)" % (folder, rel(src), "project" if owner == "" else owner or "not checked"))
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
