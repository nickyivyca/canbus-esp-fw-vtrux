"""Where gen-inhibit's bench tools find what is not in this repo.

Gen-inhibit tester, 2026-10-22, with the move of the bench tooling out of SeaDrive
(port plan goal 20; the reviewer's rulings of the same day). Before the move these
tools ran from the reverse-it root and reached their data by relative paths and by
counting `..` up the SeaDrive tree; in the repo that data is elsewhere, so every such
path goes through this module, and nothing machine-specific is committed. The pattern
is the charge interposer's `charge-interposer/paths.py`.

One setting, an environment variable:

  REVERSE_IT_DIR   REQUIRED for anything that reads or writes data: the absolute path
                   of SeaDrive's reverse-it root on this machine (the folder holding
                   canre/ and projects/). From it come the captures
                   (projects/vtrux/logs/), the vehicle DBCs, the bench lock
                   (firmware/bench-log.jsonl) and bench-holder.md, the run folders and fixtures
                   (projects/vtrux/notes/artifacts/gen-inhibit/runs/), the X2 jammer's
                   build binaries. No default: unset is an error, never a guess. The
                   root still holds canre/, which marks it, but canre is not taken
                   from there (below).

The diag DBCs are not here: gi_diag.py reads both from git (toolkit item 10,
2026-10-24), and this module no longer resolves them.

Retired, and refused if set (reviewer, 2026-10-22), so an old environment fails
loudly instead of resolving somewhere else: GI_FW_TOOLS (the moved tools find
gen-inhibit/tools/ as their parent), GEN_INHIBIT_REPO and VTRUX_DATA (replaced by
REVERSE_IT_DIR on 2026-10-20), REVERSE_IT_ROOT (an old name in the artifacts index).

Run records never land in the repo: runs() is the SeaDrive runs/ folder. A tool that
honours GI_RUNS_DIR still does, around runs().

canre comes from the public repo at the commit pins.json pins (the canre-pin step,
2026-10-26; before it, from SeaDrive through REVERSE_IT_DIR): use_canre() hands over
to canre_pin.py, which refuses any other canre.

PINS. pins.json beside this file holds the sha256 of the inputs the reviewer listed
(toolkit item 9): captures, cut replays, DBCs, the jammer binaries, and the run
records the harness tests read. A pinned file is checked when a tool resolves it
(capture(), vehicle_dbc(), jammer_builds(), verify(), pinned_tree()); a
mismatch is refused. A file that is not pinned passes through verify() unchanged.

Every function resolves when CALLED, so importing a module that uses these does not
require the setting; only touching the data does.

    py -3.14 -B gi_paths.py --pin REL [REL ...]    print pins.json entries for files
                                                  relative to REVERSE_IT_DIR
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(HERE, "pins.json")
DATA_REL = "projects/vtrux/notes/artifacts/gen-inhibit"
RETIRED = {
    "GI_FW_TOOLS": "the bench tools find gen-inhibit/tools/ as their parent folder",
    "GEN_INHIBIT_REPO": "use REVERSE_IT_DIR (2026-10-20)",
    "VTRUX_DATA": "use REVERSE_IT_DIR (2026-10-20)",
    "REVERSE_IT_ROOT": "use REVERSE_IT_DIR",
}


class PathsError(RuntimeError):
    pass


def check_retired(environ=None):
    """Refuse a retired setting. Called on import, so every tool that uses this module
    refuses one before it does anything."""
    env = os.environ if environ is None else environ
    bad = sorted(k for k in RETIRED if env.get(k, "").strip())
    if bad:
        raise PathsError("retired setting%s set: %s. Unset %s (%s)."
                         % ("s" if len(bad) > 1 else "", ", ".join(bad), "them" if len(bad) > 1 else "it",
                            "; ".join("%s: %s" % (k, RETIRED[k]) for k in bad)))


def project_root():
    """gen-inhibit's project folder, gen-inhibit/ in the repo since port step 0d
    (2026-10-26): firmware/ (the WiCAN tree, the IDF build root), test/, tools/ (flat;
    tools/bench/ is two below the project folder) and vtrux_tools.py. A copy with no git
    (the host suite's ~/gi_host) has the same layout."""
    return os.path.normpath(os.path.join(HERE, "..", ".."))


def firmware_root():
    """The WiCAN tree, gen-inhibit/firmware/: main/, CMakeLists.txt, vtrux_checks.json,
    shared_pins.json and vtrux_build.json, and the build folders."""
    return os.path.join(project_root(), "firmware")


def repo_root():
    """This firmware repo's root, one above the project folder: where the repo's own
    files and scratch/ are, and what a repo-relative git path is relative to."""
    return os.path.dirname(project_root())


PROJECT_DIR = "gen-inhibit"
# Before port step 0d the project's files were at the repo root, with the tooling one
# folder deeper. Each entry maps a path relative to the project folder now (the prefix)
# to its path in a commit before the move; the first prefix that matches wins.
OLD_LAYOUT = (("firmware/", ""), ("vtrux_tools.py", "tools/vtrux_tools.py"),
              ("tools/", "tools/gen_inhibit/"), ("test/", "test/"))


def old_path(rel):
    """rel (relative to the project folder, as laid out since port step 0d) as it was
    before the move, relative to the repo root; None for a path that had no place then."""
    rel = rel.replace("\\", "/")
    for new, old in OLD_LAYOUT:
        if not new.endswith("/"):
            if rel == new:
                return old
        elif rel.startswith(new):
            return old + rel[len(new):]
        elif rel == new[:-1]:               # the folder itself
            return old.rstrip("/")
    return None


def path_at(rev, rel, repo=None):
    """The repo-relative git path, at commit rev, of rel, a path relative to the project
    folder as laid out since port step 0d (test/gen_inhibit_host/golden,
    tools/bench/gi_diag.py, firmware/main/gen_inhibit.c): gen-inhibit/<rel> where rev has
    it there, else its place before the move (old_path), as in every commit before.
    For `git show rev:<path>` and pathspecs given at the repo root. Asked per path, so a
    commit on a topic branch that has moved only part of the tree answers right too.
    A path in neither place is returned in the new layout."""
    import subprocess
    repo = repo or repo_root()
    moved = "%s/%s" % (PROJECT_DIR, rel.replace("\\", "/"))

    def has(p):
        return subprocess.run(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (rev, p)],
                              capture_output=True).returncode == 0
    if has(moved):
        return moved
    old = old_path(rel)
    return old if old is not None and has(old) else moved


def fw_tools():
    """gen-inhibit/tools/ (flat since port step 0d): the suite, the judges, the guards."""
    return os.path.normpath(os.path.join(HERE, ".."))


def worktree_of(path):
    """The git worktree holding path: the nearest folder upwards with a .git (a folder
    in a clone, a file in a worktree). Found by walking up, not by counting, so a copy
    of a file in that worktree's scratch/ (a mutant's) finds the same one."""
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise PathsError("%s is in no git worktree" % path)
        d = up


def scratch(name, run=True, start=None):
    """A test's or mutant runner's own folder in the worktree's gitignored scratch/
    (AGENTS.md "Files and folders"), never the system temp folder:
    <worktree>/scratch/gen-inhibit-tests/<name>/<time>_<pid> with run, so two runs at
    once never share one; without run, <...>/<name> itself. Created; start is the file
    whose worktree is meant (default this module's). Harness-defects row, 2026-10-24."""
    import time
    d = os.path.join(worktree_of(start or HERE), "scratch", "gen-inhibit-tests", name)
    if run:
        d = os.path.join(d, "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d


def reverse_it_dir():
    """SeaDrive's reverse-it root, from REVERSE_IT_DIR."""
    v = os.environ.get("REVERSE_IT_DIR", "").strip()
    if not v:
        raise PathsError("REVERSE_IT_DIR is not set: set it to SeaDrive's reverse-it root on this machine "
                         "(the folder holding canre/ and projects/; tools-reference.md)")
    v = os.path.normpath(os.path.expanduser(v))
    if not (os.path.isdir(os.path.join(v, "canre")) and os.path.isdir(os.path.join(v, "projects"))):
        raise PathsError("REVERSE_IT_DIR=%s does not hold canre/ and projects/" % v)
    return v


def project(*parts):
    """A path under the reverse-it root; parts may hold forward slashes."""
    return os.path.normpath(os.path.join(reverse_it_dir(), *parts))


def data_dir(*parts):
    """The SeaDrive half of this harness, projects/vtrux/notes/artifacts/gen-inhibit/."""
    return project(DATA_REL, *parts)


def runs(*parts):
    """The SeaDrive run folder (runs/ under data_dir), never the repo."""
    return data_dir("runs", *parts)


def bench_holder():
    """firmware/bench-holder.md: the bench's rules, its board state and the lock's
    history before the 2026-10-24 cutover. Not the lock: that is bench_log()."""
    return project("firmware", "bench-holder.md")


def bench_log():
    """The bench lock, firmware/bench-log.jsonl, since the 2026-10-24 cutover; read
    and written only through shared/bench's bench_lock (bench_held.py reads it)."""
    return project("firmware", "bench-log.jsonl")


def capture(name):
    """A capture in projects/vtrux/logs/, checked against its pin if it has one."""
    return verify(project("projects", "vtrux", "logs", name))


def vehicle_dbc(name):
    """A vehicle DBC in projects/vtrux/, checked against its pin."""
    return verify(project("projects", "vtrux", name))


def jammer_builds(*parts):
    """The X2 jammer's build binaries (TEST EQUIPMENT, NOT FOR VEHICLE), in SeaDrive;
    a file given is checked against its pin."""
    p = data_dir("x2_jammer", "builds", *parts)
    return verify(p) if parts else p


def use_canre():
    """Make `import canre` load the pinned commit (canre_pin.use): its install folder
    goes first on sys.path, and any other canre is refused. Not the reverse-it root,
    which is where canre came from before the canre-pin step (2026-10-26)."""
    import canre_pin
    try:
        return canre_pin.use()
    except canre_pin.CanrePinError as e:
        raise PathsError(str(e))


def _rel(path):
    """path relative to the reverse-it root, with forward slashes, or None."""
    try:
        r = os.path.relpath(os.path.abspath(path), reverse_it_dir())
    except ValueError:              # another drive
        return None
    return None if r.startswith("..") else r.replace("\\", "/")


def load_pins():
    """{normcase(relative path): pin}. Keys are compared case-folded where the file
    system is (Windows), so a path spelled in another case cannot slip past its pin."""
    with open(PINS_FILE, encoding="utf-8") as fh:
        return {os.path.normcase(k): v for k, v in json.load(fh)["files"].items()}


_checked = {}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify(path):
    """Refuse a pinned file whose contents differ from its pin; return the path. A file
    with no pin, or outside the reverse-it root, passes unchanged."""
    rel = _rel(path) if os.environ.get("REVERSE_IT_DIR", "").strip() else None
    if rel is None:
        return path
    want = load_pins().get(os.path.normcase(rel))
    if want is None:
        return path
    st = os.stat(path)
    key = (rel, st.st_size, st.st_mtime_ns)
    if key not in _checked:
        _checked[key] = sha256_file(path)
    if _checked[key] != want:
        raise PathsError("pinned input changed: %s has sha256 %s, its pin is %s (pins.json)"
                         % (rel, _checked[key], want))
    return path


def pinned_tree(path):
    """Refuse a pinned folder whose file list or contents differ from its pin; return the
    path. The folder's pin is the sha256 of its sorted 'relpath sha256' lines, over every
    file except those its pin entry excludes (by relative path). A folder with no pin is
    refused: a tree pin is asked for only where one is expected."""
    rel = _rel(path)
    entry = load_pins().get(os.path.normcase(rel + "/")) if rel is not None else None
    if entry is None:
        raise PathsError("no tree pin for %s in pins.json" % (rel or path))
    got = tree_digest(path, set(entry.get("exclude", [])))
    if got != entry["sha256"]:
        raise PathsError("pinned folder changed: %s has digest %s, its pin is %s (pins.json)"
                         % (rel, got, entry["sha256"]))
    return path


def tree_digest(path, exclude=()):
    lines = []
    for dp, dns, fns in os.walk(path):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            r = os.path.relpath(p, path).replace("\\", "/")
            if r in exclude:
                continue
            lines.append("%s %s" % (r, sha256_file(p)))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


check_retired()


if __name__ == "__main__":
    if sys.argv[1:2] != ["--pin"]:
        print(__doc__)
        sys.exit(2)
    out = {}
    for r in sys.argv[2:]:
        p = project(r)
        if r.endswith("/"):
            out[r] = {"sha256": tree_digest(p)}
        else:
            out[r] = sha256_file(p)
    print(json.dumps(out, indent=1))
