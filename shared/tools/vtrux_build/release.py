"""The release build (SPEC section 3): an image meant for a vehicle.

    python -B -m vtrux_build release [--project DIR] [--check-only]

Run from an exported ESP-IDF shell (IDF_PATH and IDF_PYTHON_ENV_PATH set),
with REVERSE_IT_DIR set. DIR is the ESP-IDF project root (default: the
current folder), holding the project's vtrux_build.json (R22).

In order, each refusal an exit, never a warning:

  1. the project's build data reads (project.py);
  2. REVERSE_IT_DIR is set and has a firmware/ folder (R13: the build stops
     if it is unset, before anything is built);
  3. the ESP-IDF environment is exported;
  4. R8: the worktree has no uncommitted changes and no untracked files
     (git status, ignored files excepted). The commit is HEAD.
     --check-only stops here.
  5. R9: `idf.py reconfigure`;
  6. R10: every downloaded component checks against its CHECKSUMS.json and
     dependencies.lock (components.py), after the reconfigure fetched them;
  7. R16, override_path clause: no shared firmware component the project
     names is taken from a local folder: its dependencies.lock source is
     not "local", and no idf_component.yml of the build gives it an
     override_path;
  8. `idf.py build`, in the same run as 5;
  9. R11: the image is the one build/project_description.json names as
     app_bin (builddir.py), with its app_elf;
 10. HEAD is still the commit step 4 read, so the record's commit is the
     one that was built;
 11. R13: the record (record.py).

Not done here: the build checks (R17 to R21, the tester's), the tooling-pin
refusal of R16 and running from the pinned checkout (R23), both waiting on
the shared/main tag scheme. A bench or dev build (R14) does not use this
script.
"""

import os
import subprocess

from . import components, lockfile, manifest, record
from .builddir import BuildDir, BuildDirError
from .digest import component_digest
from .image import AppImage, ImageError
from .project import Project, ProjectError

TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Refused(Exception):
    """A release build condition not met. str() is the reason."""


def _run(argv, cwd=None):
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout


def git(root, *args):
    """(returncode, stdout) of git -C root <args>."""
    return _run(["git", "-C", root] + list(args))


def idf_runner(project_root):
    """A callable that runs idf.py <args> in project_root with the exported
    environment's Python and returns its exit code. Output goes to this
    process's stdout and stderr."""
    idf_path = os.environ.get("IDF_PATH")
    env_path = os.environ.get("IDF_PYTHON_ENV_PATH")
    if not idf_path or not env_path:
        raise Refused("IDF_PATH and IDF_PYTHON_ENV_PATH are not both set; run "
                      "this from an exported ESP-IDF shell")
    py = os.path.join(env_path, "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    idf_py = os.path.join(idf_path, "tools", "idf.py")
    for p in (py, idf_py):
        if not os.path.isfile(p):
            raise Refused("%s does not exist; the ESP-IDF environment is not "
                          "the one exported" % p)

    def run(args):
        return subprocess.run([py, idf_py, "-C", project_root] + list(args)
                              ).returncode
    return run


def say(msg):
    print(msg, flush=True)


def ok(what, detail=""):
    say("[  ok  ] %-30s %s" % (what, detail))


def worktree_state(root):
    """(commit, status lines). Refused if root is not in a git worktree."""
    rc, top = git(root, "rev-parse", "--show-toplevel")
    if rc != 0 or not top.strip():
        raise Refused("%s is not in a git worktree" % root)
    rc, head = git(root, "rev-parse", "--verify", "HEAD")
    if rc != 0 or len(head.strip()) != 40:
        raise Refused("HEAD cannot be read in %s" % root)
    rc, st = git(root, "status", "--porcelain=v1", "--untracked-files=all",
                 "--ignore-submodules=none")
    if rc != 0:
        raise Refused("git status failed in %s" % root)
    return head.strip(), [ln for ln in st.splitlines() if ln.strip()]


def check_clean(root):
    """R8. Returns the commit."""
    commit, dirty = worktree_state(root)
    if dirty:
        shown = "\n".join("    " + ln for ln in dirty[:20])
        more = ("\n    ... and %d more" % (len(dirty) - 20)
                if len(dirty) > 20 else "")
        raise Refused("the worktree has %d uncommitted change(s) or untracked "
                      "file(s). A release build is built from a clean "
                      "worktree (R8), so that its record's commit describes "
                      "the image.\n%s%s" % (len(dirty), shown, more))
    ok("worktree clean (R8)", "HEAD %s" % commit)
    return commit


def build_manifests(bd):
    """{component dir: parsed idf_component.yml} for every component the
    build used that has one."""
    out = {}
    for d in sorted(set(bd.components().values())):
        p = os.path.join(d, "idf_component.yml")
        if os.path.isfile(p):
            try:
                out[d] = manifest.read(p)
            except manifest.ManifestError as e:
                raise Refused("%s cannot be read: %s" % (p, e))
    return out


def check_shared_pins(proj, locks, manifests):
    """R16, override_path clause. Returns {name: what R13 records}."""
    out = {}
    problems = []
    for name in proj.shared_components:
        entry = locks.get(name)
        if entry is None:
            problems.append("%s is not in dependencies.lock, so it is not "
                            "pinned" % name)
            continue
        src = entry.get("source", {})
        if src.get("type") == "local":
            problems.append("%s is taken from the local folder %s, not its "
                            "pinned version" % (name, src.get("path")))
        declared = {}
        for d, deps in manifests.items():
            if name in deps:
                if "override_path" in deps[name]:
                    problems.append("%s/idf_component.yml gives %s an "
                                    "override_path (%s)"
                                    % (d, name, deps[name]["override_path"]))
                declared = declared or deps[name]
        out[name] = {"version": declared.get("version"),
                     "git": declared.get("git"),
                     "path": declared.get("path"),
                     "lock_version": entry.get("version")}
    if problems:
        raise Refused("shared code only at a pinned tag (R16):\n"
                      + "\n".join("    " + p for p in problems))
    ok("shared components pinned (R16)",
       ", ".join(sorted(out)) or "the project names none")
    return out


def tooling_identity(root=TOOLS_ROOT):
    """{"tag", "commit", "dirty", "digest"} of the shared/tools folder this
    module runs from. Outside a git checkout, tag, commit and dirty are
    None."""
    ident = {"tag": None, "commit": None, "dirty": None,
             "digest": component_digest(root)}
    rc, head = git(root, "rev-parse", "--verify", "HEAD")
    if rc != 0:
        return ident
    ident["commit"] = head.strip()
    rc, st = git(root, "status", "--porcelain=v1", "--untracked-files=all",
                 "--", ".")
    ident["dirty"] = bool(st.strip()) if rc == 0 else None
    rc, tag = git(root, "describe", "--tags", "--exact-match", "HEAD")
    if rc == 0 and tag.strip():
        ident["tag"] = tag.strip()
    return ident


def release(project_root, check_only=False, idf=None, toolchain_run=None,
            dest=None):
    """The release build. Returns the record's path (None for check_only).
    Raises Refused with the reason. idf, toolchain_run and dest replace the
    real idf.py runner, the compiler call and the record folder (tests)."""
    root = os.path.abspath(project_root)
    try:
        proj = Project.load(root)
    except ProjectError as e:
        raise Refused(str(e))
    ok("project build data", "%s (%s)" % (proj.name, proj.root))

    try:
        out_dir = dest or record.builds_dir(proj.name)
    except record.RecordError as e:
        raise Refused(str(e))
    ok("record folder (R13)", out_dir)

    if idf is None:
        idf = idf_runner(root)
    ok("ESP-IDF environment", os.environ.get("IDF_PATH", "(test runner)"))

    commit = check_clean(root)
    if check_only:
        return None

    say("\nidf.py reconfigure ...")
    rc = idf(["reconfigure"])
    if rc != 0:
        raise Refused("idf.py reconfigure failed (exit %d)" % rc)
    ok("reconfigure (R9)")

    try:
        good, report = components.check_managed(root)
    except (OSError, lockfile.LockError) as e:
        raise Refused("downloaded components cannot be checked: %s" % e)
    for name, r in sorted(report.items()):
        if r["problems"]:
            say("[ FAIL ] component %s:" % name)
            for p in r["problems"][:10]:
                say("             %s" % p)
            if len(r["problems"]) > 10:
                say("             ... and %d more" % (len(r["problems"]) - 10))
        else:
            ok("component %s" % name,
               "%d files match CHECKSUMS.json; .component_hash matches the "
               "lock" % r["files_checked"])
    if not good:
        raise Refused("a downloaded component differs from its CHECKSUMS.json "
                      "or from dependencies.lock, or one the lock names is "
                      "missing (R10). managed_components/ is git-ignored, so "
                      "the clean worktree says nothing about it.")
    if not report:
        ok("downloaded components (R10)", "none in the lock or on disk")

    bd = BuildDir(os.path.join(root, "build"))
    lock_path = os.path.join(root, "dependencies.lock")
    try:
        locks = lockfile.read(lock_path)
        shared = check_shared_pins(proj, locks, build_manifests(bd))
    except (OSError, lockfile.LockError, BuildDirError) as e:
        raise Refused("the pins cannot be read: %s" % e)

    say("\nidf.py build ...")
    rc = idf(["build"])
    if rc != 0:
        raise Refused("idf.py build failed (exit %d)" % rc)

    bd = BuildDir(os.path.join(root, "build"))
    try:
        bin_path, elf_path = bd.app_bin(), bd.app_elf()
        desc = bd.description()
        comps = bd.components()
        img = AppImage.read(bin_path)
    except (BuildDirError, ImageError, OSError) as e:
        raise Refused(str(e))
    ok("image named by the build (R11)", os.path.basename(bin_path))

    head_now, after = worktree_state(root)
    if head_now != commit:
        raise Refused("HEAD moved during the build (%s, now %s), so no one "
                      "commit describes the image" % (commit, head_now))

    try:
        tc = record.toolchain(desc.get("c_compiler"),
                              toolchain_run or (lambda a: _run(a)))
        fields = {
            "commit": commit,
            "host_os": record.host_os(),
            "toolchain": tc,
            "idf_version": {"project": desc.get("git_revision"),
                            "image": img.idf_ver},
            "components": {n: {"dir": d, "digest": component_digest(d)}
                           for n, d in sorted(comps.items())},
            "dependencies_lock_sha256": lockfile.file_sha256(lock_path),
            "shared_components": shared,
            "tooling": tooling_identity(),
            "release_checks": {"managed_components": report,
                               "tree_after_build": after},
        }
        path = record.write(proj.name, bin_path, elf_path, fields,
                            dest=out_dir)
    except (record.RecordError, ImageError, OSError) as e:
        raise Refused("no record written: %s" % e)
    ok("record (R13)", path)
    say("           image hash %s" % img.image_hash)
    say("           code hash  %s" % img.code_hash)
    if after:
        say("NOTE: the build changed the worktree; the record lists it under "
            "release_checks.tree_after_build:")
        for ln in after[:20]:
            say("    " + ln)
    return path


def main(args):
    say("=" * 72)
    say("RELEASE BUILD (shared/tools SPEC section 3)")
    say("=" * 72)
    try:
        path = release(args.project, check_only=args.check_only)
    except Refused as e:
        say("\nREFUSED: %s" % e)
        say("\nNOT BUILT, or no record written. Every refusal is a condition "
            "of a release build, not a warning.")
        return 1
    if args.check_only:
        say("\nCHECK ONLY: the pre-build conditions hold. Nothing was built.")
    else:
        say("\nRELEASE BUILD RECORDED: %s" % path)
    return 0
