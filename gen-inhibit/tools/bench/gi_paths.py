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
                   build binaries, and the shared X2 test images' archive
                   (projects/vtrux/notes/artifacts/x2_test_images/). No default: unset is an error, never a guess. The
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

    python3.13 -B gi_paths.py --pin REL [REL ...]  print pins.json entries for files
                                                  relative to REVERSE_IT_DIR
    python3.13 -B gi_paths.py --stage-scratch DEST [--at COMMIT]
                                                  stage the shared scratch helper and
                                                  its record in DEST, for a copy with
                                                  no git (stage_core); exit 2 refused

SCRATCH. scratch(), run_dir(), finish(), remove_tree() and the owner files are
gen-inhibit's layout on shared/bench's scratch helper (port step 4 item 3): see "THE
SHARED SCRATCH HELPER" below for where it is loaded from.
"""
import hashlib
import json
import os
import shutil
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


# THE SHARED SCRATCH HELPER (port step 4 item 3, the gen-inhibit reviewer's batch of
# 2026-11-25 and its ruling of 2026-11-26). The owner files, the removal and the process
# check are shared/bench's vtrux_bench/scratch.py (ACCEPT-SPEC A14), at the tag
# gen-inhibit pins; gen-inhibit keeps only its own layout below. The helper is loaded
# when first needed, never at import:
#   - in a git worktree (a .git found upwards from this file), only through
#     gi_core_pin.py beside this file: the R23 check of the pinned checkout
#     ../shared-<tag>/, then its vtrux_bench.scratch. A refusal is a PathsError. A staged
#     copy beside this file is never used there;
#   - in a copy with no git (the host suite's ~/gi_host from gi_host_run.sh, H's
#     ~/gi_baseline_<commit> from baseline_host_run.sh), only the staged file
#     gi_scratch_core.py beside this file, and only once its sha256 is the one its record
#     gi_scratch_core.json gives. A missing or bad record, or another sha256, is a
#     PathsError. The copiers stage both with stage_core() (--stage-scratch).
CORE_COMPONENT = "shared/bench"
CORE_FILE = "vtrux_bench/scratch.py"            # in shared/bench
CORE_STAGED = "gi_scratch_core.py"              # the staged copy, beside this file
CORE_RECORD = "gi_scratch_core.json"            # its record, beside it
PIN_REL = "gen-inhibit/firmware/shared_pins.json"
IN_WORKTREE = None      # None: found from this file's folder; the tests set True or False
_CORE = []


def in_worktree():
    """True if this file is in a git worktree (a .git upwards from it)."""
    if IN_WORKTREE is not None:
        return bool(IN_WORKTREE)
    try:
        worktree_of(HERE)
    except PathsError:
        return False
    return True


def core_pin():
    """gi_core_pin: the one already imported, else loaded by file from beside this file."""
    import importlib.util
    mod = sys.modules.get("gi_core_pin")
    if mod is None:
        spec = importlib.util.spec_from_file_location("gi_core_pin", os.path.join(HERE, "gi_core_pin.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gi_core_pin"] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("gi_core_pin", None)
            raise
    return mod


def _staged_core():
    """The staged helper beside this file, loaded only if its sha256 is its record's."""
    import types
    rec_path, src_path = os.path.join(HERE, CORE_RECORD), os.path.join(HERE, CORE_STAGED)
    try:
        with open(rec_path, encoding="utf-8") as fh:
            want = json.load(fh)["sha256"]
        if not (isinstance(want, str) and len(want) == 64 and all(c in "0123456789abcdef" for c in want)):
            raise ValueError("its sha256 is not 64 lower-case hex digits: %r" % (want,))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise PathsError("%s is in no git worktree, so the shared scratch helper is taken only from the "
                         "staged %s with its record %s, and the record is missing or not valid (%s: %s). "
                         "The copier stages both (gi_paths.py --stage-scratch)"
                         % (HERE, CORE_STAGED, CORE_RECORD, type(e).__name__, e))
    try:
        with open(src_path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        raise PathsError("the staged shared scratch helper %s cannot be read: %s" % (src_path, e))
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise PathsError("the staged shared scratch helper %s has sha256 %s, and its record %s says %s"
                         % (src_path, got, CORE_RECORD, want))
    mod = types.ModuleType("gi_scratch_core")
    mod.__file__ = src_path
    exec(compile(data, src_path, "exec"), mod.__dict__)
    return mod


def scratch_core():
    """shared/bench's vtrux_bench/scratch.py, loaded as the block above says."""
    if _CORE:
        return _CORE[0]
    if in_worktree():
        try:
            core_pin().use_bench()
            from vtrux_bench import scratch as S
        except Exception as e:      # noqa: BLE001  gi_core_pin.Refused, or an import that fails
            raise PathsError("the shared scratch helper (%s's %s) cannot be taken from the pinned checkout "
                             "(gi_core_pin): %s: %s" % (CORE_COMPONENT, CORE_FILE, type(e).__name__, e))
    else:
        S = _staged_core()
    _CORE.append(S)
    return S


def _git(repo, *args):
    import subprocess
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    if r.returncode != 0:
        raise PathsError("git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def stage_core(dest, commit=None):
    """Stage the shared scratch helper for a copy with no git: dest/gi_scratch_core.py,
    then its record dest/gi_scratch_core.json {component, tag, commit, file, sha256}.
    Without commit, from the checkout this worktree pins, after gi_core_pin's check; with
    commit (H's commit under test), from git's objects at the shared/bench pin that
    commit's shared_pins.json gives, once the pin's tag is checked to mark the pin's
    commit. Returns the record."""
    if commit is None:
        try:
            c = core_pin().check()
        except Exception as e:      # noqa: BLE001  gi_core_pin.Refused
            raise PathsError("nothing staged: %s: %s" % (type(e).__name__, e))
        tag, pin_commit = c["tag"], c["commit"]
        with open(os.path.join(c["folder"], *CORE_FILE.split("/")), "rb") as fh:
            data = fh.read()
    else:
        repo = worktree_of(HERE)
        try:
            pin = json.loads(_git(repo, "show", "%s:%s" % (commit, PIN_REL)).decode("utf-8"))[CORE_COMPONENT]
            tag, pin_commit = pin["tag"], pin["commit"]
        except (ValueError, KeyError, TypeError) as e:
            raise PathsError("nothing staged: %s at %s has no %s pin (%s: %s)"
                             % (PIN_REL, commit, CORE_COMPONENT, type(e).__name__, e))
        tagged = _git(repo, "rev-parse", "--verify", "refs/tags/%s^{commit}" % tag).decode().strip()
        if tagged != pin_commit:
            raise PathsError("nothing staged: the tag %s marks %s, and %s at %s says %s"
                             % (tag, tagged, PIN_REL, commit, pin_commit))
        data = _git(repo, "show", "%s:%s/%s" % (pin_commit, CORE_COMPONENT, CORE_FILE))
    rec = {"component": CORE_COMPONENT, "tag": tag, "commit": pin_commit, "file": CORE_FILE,
           "sha256": hashlib.sha256(data).hexdigest()}
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, CORE_STAGED), "wb") as fh:
        fh.write(data)
    with open(os.path.join(dest, CORE_RECORD), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return rec


def _say(out):
    """The shared helper's out callable: a line to out (default stderr)."""
    return lambda s: print(s, file=sys.stderr if out is None else out)


def scratch(name, run=True, start=None, keep=None, at_exit=True):
    """A test's or mutant runner's own folder in the worktree's gitignored scratch/
    (AGENTS.md "Files and folders"), never the system temp folder: with run, a new
    folder <worktree>/scratch/gen-inhibit-tests/<name>/<time>-<pid>-<hex6> made by the
    shared helper's make(), so two runs at once never share one; without run,
    <...>/<name> itself. Created; start is the file whose worktree is meant (default
    this module's). Harness-defects row, 2026-10-24.

    A run folder (AGENTS.md "Removing scratch", 2026-10-31) has the shared helper's
    owner file beside it, <folder>.owner.json (this process's pid, start time, host and
    platform), and is removed with it when this process exits, pass or fail: an atexit
    handler calls finish(), which runs on a normal end, sys.exit and an uncaught
    exception alike. keep, or GI_KEEP_SCRATCH set, keeps it (finish() says so);
    at_exit=False leaves the removal to the caller (run_dir). A run killed outright
    leaves its folder; the next run of the same name removes it, since its owner file
    then names a finished process (make() prunes the folder's siblings first)."""
    import atexit
    import time
    d = os.path.join(worktree_of(start or HERE), "scratch", "gen-inhibit-tests", name)
    if not run:
        os.makedirs(d, exist_ok=True)
        return d
    d = scratch_core().make(d, time.strftime("%Y%m%dT%H%M%S"), out=_say(None))
    if at_exit:
        atexit.register(finish, d, keep, None, os.getpid())
    return d


OWNER_SUFFIX = ".owner.json"        # the shared helper's; test_gi_scratch checks they agree
KEEP_ENV = "GI_KEEP_SCRATCH"


def long_path(path):
    """path made absolute and, on Windows, long-path safe (the shared helper's)."""
    return scratch_core().long_path(path)


def _short(p):
    """p without a long-path prefix, for printing."""
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    return p[4:] if p.startswith("\\\\?\\") else p


def remove_tree(path, out=None):
    """Remove path (a folder and everything in it, or a file or link), with an owner file
    beside it if there is one, through the shared helper's remove(), and return True; or
    print to out (default stderr) "could not remove <path>: N entries failed" and each
    entry left with its reason, and return False. It never raises for what it can't
    remove (AGENTS.md "Removing scratch", 2026-10-31). The helper clears a read-only flag
    and tries again; on POSIX it makes a failing entry's folder writable too, but only
    while that folder is inside path, and it never chmods a link (gen-inhibit's Linux fix
    of 2026-11-12, shared/bench from tag 2026.11.26). A path that isn't there is already
    removed. Never shutil.rmtree's ignore_errors, which leaves the folder without a word."""
    out = sys.stderr if out is None else out
    left = scratch_core().remove(path, quiet=True)
    if not left:
        return True
    print("could not remove %s: %d entr%s failed" % (_short(long_path(path)), len(left),
                                                     "y" if len(left) == 1 else "ies"), file=out)
    for p, why in left[:10]:
        print("  %s: %s" % (p, why), file=out)
    if len(left) > 10:
        print("  ... and %d more" % (len(left) - 10), file=out)
    return False


def process_start(pid):
    """When process pid started, as a number fixed for its life; None if no process pid
    is running; "?" if one may be but its start can't be read (the shared helper's)."""
    return scratch_core().process_start(pid)


def owner_path(d):
    """The owner file of run folder d: beside it, so nothing is added inside the folder."""
    return scratch_core().owner_path(d)


STATES = {"live": "running", "finished": "finished", "kept": "kept"}


def owner_state(d):
    """"running", "finished" or "kept", from the shared helper's state() of d's owner
    file (live, finished, kept); None for no owner file, one that can't be read, or one
    from another host or platform (not ours to judge). Finished: no process with its pid
    runs, or one does that started at another time (the pid reused)."""
    return STATES.get(scratch_core().state(d))


def remove_finished_siblings(parent, out=None):
    """Remove each run folder in parent whose owner file says its run has finished, with
    the owner file, and owner files whose folder is gone (the shared helper's prune());
    the folders removed. A folder with no owner file is left alone (the one-time
    sweep's, scratch_sweep.py), and so is a running, kept or other host's one."""
    return scratch_core().prune(parent, out=_say(out))


def finish(d, keep=None, out=None, pid=None):
    """The end of run folder d: remove it and its owner file (True), or print why not
    (False). keep, or GI_KEEP_SCRATCH set, keeps both instead, marking the owner file
    kept (the shared helper's keep()) so later runs leave it, and says so. pid: the
    process that made d; a forked child that inherited the atexit handler leaves d to it."""
    out = sys.stderr if out is None else out
    if pid is not None and pid != os.getpid():
        return True
    if keep is None:
        keep = bool(os.environ.get(KEEP_ENV, "").strip())
    if keep:
        if os.path.isdir(d):
            S = scratch_core()
            try:
                S.keep(d, "%s set" % KEEP_ENV if os.environ.get(KEEP_ENV, "").strip() else "keep=True")
            except S.ScratchError as e:
                print("could not mark %s kept: %s" % (d, e), file=out)
            print("kept %s" % d, file=out)
        return True
    return remove_tree(d, out)


def run_dir(name, keep=None, start=None):
    """scratch(name) for a with-block: the folder is removed when the block ends, pass or
    fail, not only at exit (for a runner that makes many)."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        d = scratch(name, start=start, at_exit=False)
        try:
            yield d
        finally:
            finish(d, keep)
    return cm()


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


TSHARK_WINDOWS = r"C:\Program Files\Wireshark\tshark.exe"


def tshark(environ=None, platform=None, which=None):
    """The tshark the capture readers run (2026-11-12, the reviewer's Linux fix item):
    $GI_TSHARK if set; else Wireshark's install folder on Windows; else tshark on PATH.
    Off Windows with none on PATH it returns the bare name, so the reader's own run of
    it fails, saying so, rather than reading nothing."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    if environ.get("GI_TSHARK"):
        return environ["GI_TSHARK"]
    if platform == "win32":
        return TSHARK_WINDOWS
    return which("tshark") or "tshark"


def jammer_builds(*parts):
    """The X2 jammer's build binaries (TEST EQUIPMENT, NOT FOR VEHICLE), in SeaDrive;
    a file given is checked against its pin."""
    p = data_dir("x2_jammer", "builds", *parts)
    return verify(p) if parts else p


def x2_test_images(*parts):
    """The archive of the shared X2 test images' builds (TEST EQUIPMENT; x2_test_images
    SPEC T2, T6), projects/vtrux/notes/artifacts/x2_test_images/: one folder per image,
    each with its manifest.jsonl and builds/<app sha256, 16 digits>/ (written only by
    shared/x2_test_images/tools/archive.py). Not pinned here: each row carries every
    file's size and hashes, and x2_flash_custody checks them (tester, 2026-11-13)."""
    return project("projects", "vtrux", "notes", "artifacts", "x2_test_images", *parts)


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
    if sys.argv[1:2] == ["--stage-scratch"] and len(sys.argv) in (3, 5) and (len(sys.argv) == 3 or sys.argv[3] == "--at"):
        try:
            r = stage_core(sys.argv[2], sys.argv[4] if len(sys.argv) == 5 else None)
        except PathsError as e:
            print("gi_paths: REFUSED: %s" % e, file=sys.stderr)
            sys.exit(2)
        print("staged %s %s (%s) at %s, sha256 %s, in %s"
              % (r["component"], r["tag"], r["commit"][:12], r["file"], r["sha256"], sys.argv[2]))
        sys.exit(0)
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
