"""R17, scope: where the files a build read came from.

    python -B -m vtrux_checks scope --build <project>/build --release
    python -B -m vtrux_checks scope --build <project>/build --bench

SPEC R17 (shared/main 5ec6f53). Every file the build read, by ninja's
dependency records and CMake's (vtrux_build's BuildDir.files_read()), in
every CMake build folder under build/ (the app's and the bootloader's), must
come from one of:

  - the project root (project_description.json's project_path), which holds
    build/ and managed_components/;
  - IDF: IDF_PATH (project_description.json's idf_path), plus exactly two
    folders named by each build folder's own record: the install folder of
    the toolchain whose compiler CMake chose (the folder above the bin/ that
    holds CMAKE_<LANG>_COMPILER in CMakeFiles/<version>/CMake<LANG>Compiler.cmake,
    for every language recorded), and CMAKE_ROOT (CMakeCache.txt);
  - for a bench or dev build only, shared/: the shared/ folder of any
    worktree of this repo (git worktree list, from the project root), or of
    a ../shared-<tag>/ checkout beside the worktree root.

A release build that reads a shared/ folder directly fails, even one inside
the project root: shared code counts only from managed_components/.

Each folder in managed_components/ must be at the version dependencies.lock
records: its own idf_component.yml's top-level `version` is the lock's
`version` for that component (the lock's name with "/" as "__").

A build whose records name no files fails: an empty list proves nothing.
"""

import glob
import os
import re
import subprocess

from vtrux_build import lockfile
from vtrux_build.builddir import BuildDir, BuildDirError

_COMPILER = re.compile(r'^set\(CMAKE_([A-Z]+)_COMPILER "([^"]+)"\)', re.M)
_VERSION = re.compile(r"""^version:\s*['"]?([^'"\s#]+)""", re.M)
SHOW = 20


class ScopeError(ValueError):
    pass


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def under(p, root):
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def build_folders(build):
    """build/ and every CMake build folder below it (one with its own
    CMakeCache.txt and build.ninja, such as build/bootloader)."""
    out = [os.path.abspath(build)]
    for d, dirs, files in os.walk(build):
        if os.path.abspath(d) != out[0] and "CMakeCache.txt" in files and "build.ninja" in files:
            out.append(os.path.abspath(d))
    return out


def toolchains(folder):
    """{language: install folder} of the compilers CMake chose in one build folder."""
    out = {}
    for p in glob.glob(os.path.join(folder, "CMakeFiles", "*", "CMake*Compiler.cmake")):
        with open(p, encoding="utf-8", errors="replace") as f:
            for lang, exe in _COMPILER.findall(f.read()):
                out[lang] = os.path.dirname(os.path.dirname(os.path.abspath(exe)))
    return out


def _git(project, *args):
    try:
        r = subprocess.run(["git"] + list(args), cwd=project, capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None


def worktrees(project):
    out = _git(project, "worktree", "list", "--porcelain") or ""
    return [ln[len("worktree "):] for ln in out.splitlines() if ln.startswith("worktree ")]


def shared_roots(project):
    """shared/ of every worktree, and of every ../shared-<tag>/ beside the worktree root (not
    the project root: a project may sit in a subfolder of its worktree)."""
    top = (_git(project, "rev-parse", "--show-toplevel") or "").strip() or project
    roots = [os.path.join(w, "shared") for w in worktrees(project)]
    roots += [os.path.join(d, "shared") for d in glob.glob(os.path.join(os.path.dirname(os.path.abspath(top)), "shared-*"))
              if os.path.isdir(d)]
    return sorted(set(norm(r) for r in roots))


def manifest_version(folder):
    p = os.path.join(folder, "idf_component.yml")
    try:
        with open(p, encoding="utf-8") as f:
            m = _VERSION.search(f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def collect(build):
    """The facts judge() needs, all from the build's own records."""
    folders = build_folders(build)
    desc = BuildDir(folders[0]).description()
    try:
        project, idf = desc["project_path"], desc["idf_path"]
    except KeyError as e:
        raise ScopeError("project_description.json has no %s" % e)
    files, tools, croots = set(), {}, set()
    for f in folders:
        bd = BuildDir(f)
        files.update(norm(p) for p in bd.files_read())
        for lang, d in toolchains(f).items():
            tools["%s %s" % (os.path.relpath(f, folders[0]), lang)] = norm(d)
        cr = bd.cmake_cache().get("CMAKE_ROOT")
        if cr:
            croots.add(norm(cr[1]))
    lock_path = os.path.join(project, "dependencies.lock")
    lock = lockfile.read(lock_path) if os.path.exists(lock_path) else None
    mdir = os.path.join(project, "managed_components")
    managed = {}
    if os.path.isdir(mdir):
        for n in sorted(os.listdir(mdir)):
            if os.path.isdir(os.path.join(mdir, n)):
                managed[n] = manifest_version(os.path.join(mdir, n))
    return {"project": norm(project), "idf": norm(idf), "toolchains": tools, "cmake_roots": sorted(croots),
            "shared": shared_roots(project), "files": sorted(files), "lock": lock, "managed": managed,
            "folders": folders}


def judge(facts, kind):
    """[(rule, detail)] of everything R17 forbids; empty when the build passes."""
    bad = []
    if not facts["files"]:
        bad.append(("records", "the build's records name no files"))
    if not facts["toolchains"]:
        bad.append(("records", "no compiler recorded in any CMakeFiles/<version>/CMake<LANG>Compiler.cmake"))
    if not facts["cmake_roots"]:
        bad.append(("records", "no CMAKE_ROOT in any CMakeCache.txt"))
    idf = [facts["idf"]] + sorted(set(facts["toolchains"].values())) + list(facts["cmake_roots"])
    for p in facts["files"]:
        if any(under(p, s) for s in facts["shared"]):
            if kind == "release":
                bad.append(("shared", p))
        elif not (under(p, facts["project"]) or any(under(p, r) for r in idf)):
            bad.append(("outside", p))
    lock = facts["lock"]
    for name, version in sorted(facts["managed"].items()):
        entries = [k for k in (lock or {}) if lockfile.managed_dir_name(k) == name]
        if not entries:
            bad.append(("version", "managed_components/%s: no entry in dependencies.lock" % name))
        elif version is None:
            bad.append(("version", "managed_components/%s: no version in its idf_component.yml" % name))
        elif lock[entries[0]].get("version") != version:
            bad.append(("version", "managed_components/%s: version %s, dependencies.lock %s" % (
                name, version, lock[entries[0]].get("version"))))
    return bad


def main(args):
    try:
        facts = collect(args.build)
    except (OSError, BuildDirError, ScopeError, lockfile.LockError) as e:
        print("FAIL R17: cannot read the build: %s" % e)
        return 1
    bad = judge(facts, args.kind)
    print("R17 scope, %s build: %d files read in %d build folder(s)" % (args.kind, len(facts["files"]),
                                                                         len(facts["folders"])))
    print("  project root  %s" % facts["project"])
    print("  IDF_PATH      %s" % facts["idf"])
    for k, d in sorted(facts["toolchains"].items()):
        print("  toolchain     %s  (%s)" % (d, k))
    for d in facts["cmake_roots"]:
        print("  CMAKE_ROOT    %s" % d)
    for d in facts["shared"]:
        print("  shared/       %s  (%s)" % (d, "allowed" if args.kind == "bench" else "not allowed"))
    for name, v in sorted(facts["managed"].items()):
        print("  managed       %s %s" % (name, v))
    for rule in ("records", "outside", "shared", "version"):
        hits = [d for r, d in bad if r == rule]
        if hits:
            what = {"records": "unreadable records", "outside": "files read from outside the scope",
                    "shared": "files read directly from shared/ in a release build",
                    "version": "managed components not at the lock's version"}[rule]
            print("FAIL R17: %d %s" % (len(hits), what))
            for d in hits[:SHOW]:
                print("    %s" % d)
            if len(hits) > SHOW:
                print("    ... and %d more" % (len(hits) - SHOW))
    if not bad:
        print("PASS R17")
    return 1 if bad else 0
