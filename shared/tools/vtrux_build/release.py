"""The release build (SPEC section 3): an image meant for a vehicle.

    python -B -m vtrux_build release [--project DIR] [--check-only]
                                     [--jobs N]

Run from an exported ESP-IDF shell (IDF_PATH and IDF_PYTHON_ENV_PATH set),
with REVERSE_IT_DIR set. DIR is the ESP-IDF project root (default: the
current folder), holding the project's vtrux_build.json (R22).

In order, each refusal an exit, never a warning:

  1. the project's build data reads (project.py);
  2. REVERSE_IT_DIR is set and has a firmware/ folder (R13: the build stops
     if it is unset, before anything is built);
  3. the ESP-IDF environment is exported, and R10: the Component Manager's
     overwrite setting (IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS) is not
     set at all;
  4. R8: the worktree has no uncommitted changes and no untracked files
     (git status, ignored files excepted). The commit is HEAD.
     --check-only stops here.
  5. R9: `idf.py --no-ccache reconfigure`, with
     IDF_COMPONENT_STRICT_CHECKSUM=1 in its environment (R10), so the
     manager stops the configure on a component whose files no longer
     match its recorded hash. --no-ccache wins over IDF_CCACHE_ENABLE,
     which the IDF export sets wherever ccache is installed;
     R9: build/CMakeCache.txt has CCACHE_ENABLE, and off; and, by its
     effect, every compile command CMake wrote into build.ninja starts
     with the compiler CMake chose for its language, not a launcher.
     CCACHE_ENABLE is only one way in: CMAKE_<LANG>_COMPILER_LAUNCHER, from
     the environment or the cache, launches ccache with it off;
     R8 again: HEAD is the same and the worktree still clean, before
     anything is compiled (a reconfigure that rewrites a tracked file,
     sdkconfig for example, is refused here);
  6. R10: every downloaded component's .component_hash against
     dependencies.lock, and a registry download against its CHECKSUMS.json,
     unlisted files included (components.py), after the reconfigure
     fetched them;
  7. R16, override_path clause: no shared firmware component the project
     names is taken from a local folder: its dependencies.lock source is
     not "local", and no idf_component.yml of the build gives it an
     override_path;
  8. `idf.py --no-ccache build`, in the same run as 5, also in strict
     mode, so a configure the build itself triggers is strict too and
     keeps the cache off. With --jobs N,
     `ninja -C build -j N all` instead: what `idf.py build` runs, at N
     jobs (idf.py has no jobs option), in the same strict environment.
     --jobs 1 from an Idle shell is the throttled form of AGENTS.md
     "Builds during wall-clock tests". R9 asks for a reconfigure and a
     build in one run, not for idf.py build (gen-inhibit reviewer,
     2026-10-23). A configure ninja itself reruns keeps the cache's
     CCACHE_ENABLE;
     R9 again after the build, either way: CCACHE_ENABLE still off, and
     no launcher in the compile commands of any CMake build folder under
     build/, the bootloader's included (it is configured during the build);
  9. R11: the image is the one build/project_description.json names as
     app_bin (builddir.py), with its app_elf;
 10. R8 at the end: HEAD is still the commit step 4 read and the worktree
     is still clean, so the record's commit describes what was built;
 11. R13: the record (record.py).

Not done here: the build checks (R17 to R21, the tester's), the tooling-pin
refusal of R16 and running from the pinned checkout (R23), both waiting on
the shared/main tag scheme. A bench or dev build (R14) does not use this
script.
"""

import glob
import os
import re
import shutil
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
    """A callable run(args, env) that runs idf.py <args> in project_root
    with the exported environment's Python, this process's environment
    updated with env, and returns its exit code. Output goes to this
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

    def run(args, env):
        full = dict(os.environ)
        full.update(env)
        return subprocess.run([py, idf_py, "-C", project_root] + list(args),
                              env=full).returncode
    return run


def ninja_runner():
    """A callable run(args, env) that runs ninja <args>, the one on PATH
    (the exported environment's), with this process's environment updated
    with env, and returns its exit code."""
    exe = shutil.which("ninja")
    if not exe:
        raise Refused("ninja is not on PATH; run this from an exported "
                      "ESP-IDF shell")

    def run(args, env):
        full = dict(os.environ)
        full.update(env)
        return subprocess.run([exe] + list(args), env=full).returncode
    return run


def check_jobs(jobs):
    """--jobs: None, or a whole number of at least 1."""
    if jobs is None:
        return
    if isinstance(jobs, bool) or not isinstance(jobs, int) or jobs < 1:
        raise Refused("--jobs must be a whole number of at least 1 (given "
                      "%r)" % (jobs,))
    ok("build jobs", "ninja -j %d" % jobs)


# CMake's false constants; a number is true unless it is 0.
CMAKE_FALSE = ("0", "OFF", "NO", "FALSE", "N", "IGNORE", "NOTFOUND", "")


def cmake_false(value):
    """True if CMake's if() reads value as false."""
    v = value.strip().upper()
    if v in CMAKE_FALSE or v.endswith("-NOTFOUND"):
        return True
    try:
        return float(v) == 0
    except ValueError:
        return False


def check_no_ccache(build_dir, when):
    """R9: the build's configuration has the compiler cache off:
    CCACHE_ENABLE is in build/CMakeCache.txt and CMake reads it as false.
    Missing is refused too, since then nothing shows it is off."""
    try:
        cache = BuildDir(build_dir).cmake_cache()
    except BuildDirError as e:
        raise Refused("%s, the compiler cache setting cannot be read: %s"
                      % (when, e))
    if "CCACHE_ENABLE" not in cache:
        raise Refused("%s, build/CMakeCache.txt has no CCACHE_ENABLE, so "
                      "nothing shows the compiler cache is off (R9)" % when)
    value = cache["CCACHE_ENABLE"][1]
    if not cmake_false(value):
        raise Refused("%s, build/CMakeCache.txt has CCACHE_ENABLE=%s: the "
                      "compiler cache is on, and a release build is built "
                      "with it off (R9)" % (when, value))
    ok("compiler cache off (R9)", "%s, CCACHE_ENABLE=%s" % (when, value))


COMPILE_RULE = re.compile(r"^(C|CXX|ASM)_COMPILER__")
_SET_COMPILER = re.compile(r'^set\(CMAKE_(C|CXX|ASM)_COMPILER "([^"]*)"\)$')


def configured_compilers(build_dir):
    """{language: compiler} as CMake chose them for this build folder, from
    its CMakeFiles/<cmake version>/CMake<LANG>Compiler.cmake. Two different
    answers for one language raise BuildDirError."""
    out = {}
    pat = os.path.join(build_dir, "CMakeFiles", "*", "CMake*Compiler.cmake")
    for f in sorted(glob.glob(pat)):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                m = _SET_COMPILER.match(ln.strip())
                if not m:
                    continue
                lang, cc = m.groups()
                if lang in out and out[lang] != cc:
                    raise BuildDirError("%s names two %s compilers, %s and %s"
                                        % (build_dir, lang, out[lang], cc))
                out[lang] = cc
    return out


def first_word(command):
    """The program a command runs: its first word, quotes removed."""
    c = command.lstrip()
    if c.startswith('"'):
        end = c.find('"', 1)
        return c[1:end] if end > 0 else c[1:]
    return c.split(None, 1)[0] if c else ""


def _same_path(a, b):
    return (os.path.normcase(os.path.normpath(a))
            == os.path.normcase(os.path.normpath(b)))


def is_ccache(path):
    """True if path, or the file its links resolve to, is a ccache binary
    (basename ccache or ccache.exe): ccache's masquerade mode puts a link
    named after the compiler on PATH, and CMake then chooses that link."""
    for p in (path, os.path.realpath(path)):
        if os.path.basename(p).lower() in ("ccache", "ccache.exe"):
            return True
    return False


def cmake_build_folders(build_dir):
    """build_dir, then every folder under it that CMake configured (it has
    both CMakeCache.txt and build.ninja): the bootloader's, once the build
    has configured it."""
    out = [build_dir]
    for d, _, files in os.walk(build_dir):
        if (d != build_dir and "CMakeCache.txt" in files
                and "build.ninja" in files):
            out.append(d)
    return out


def check_no_launcher(build_dir, when, folders=None):
    """R9, by its effect: in each CMake build folder (build_dir alone by
    default), every compile edge's command, as ninja would run it, starts
    with the compiler CMake chose for its language. Anything else in front,
    from CCACHE_ENABLE (IDF's RULE_LAUNCH_COMPILE) or from
    CMAKE_<LANG>_COMPILER_LAUNCHER, is refused. So is a chosen compiler
    that is itself ccache once its links are resolved (masquerade mode). A
    folder with no compile edges, or no recorded compiler, is refused too:
    then nothing shows the compiler runs alone."""
    folders = folders or [build_dir]
    total = 0
    for b in folders:
        name = os.path.relpath(b, os.path.dirname(build_dir))
        try:
            compilers = configured_compilers(b)
            edges = BuildDir(b).ninja_edges()
        except (BuildDirError, OSError) as e:
            raise Refused("%s, the compile commands in %s cannot be read: %s"
                          % (when, name, e))
        comp = [e for e in edges if COMPILE_RULE.match(e.rule)]
        if not comp:
            raise Refused("%s, %s has no compile commands, so nothing shows "
                          "they run without a compiler launcher (R9)"
                          % (when, name))
        bad = []
        for e in comp:
            lang = COMPILE_RULE.match(e.rule).group(1)
            cc = compilers.get(lang)
            if cc is None:
                raise Refused("%s, %s records no %s compiler, so its compile "
                              "commands cannot be checked for a launcher (R9)"
                              % (when, name, lang))
            if is_ccache(cc):
                raise Refused("%s, the %s compiler CMake chose in %s is %s, "
                              "which is ccache (%s): ccache runs every "
                              "compile, and a release build is built "
                              "without it (R9)"
                              % (when, lang, name, cc, os.path.realpath(cc)))
            word = first_word(e.command or "")
            if not word or not _same_path(word, cc):
                bad.append((e.outputs[0], word))
        if bad:
            shown = "\n".join("    %s runs %s" % (o, w or "(nothing)")
                              for o, w in bad[:5])
            raise Refused("%s, %d of the %d compile commands in %s do not "
                          "start with the compiler: a compiler launcher "
                          "(ccache or another) runs them, and a release "
                          "build is built without one (R9).\n%s"
                          % (when, len(bad), len(comp), name, shown))
        total += len(comp)
    ok("no compiler launcher (R9)", "%s, %d compile commands in %s"
       % (when, total, ", ".join(os.path.relpath(b, os.path.dirname(build_dir))
                                 for b in folders)))


def check_no_overwrite(environ=None):
    """R10: nothing set that lets the Component Manager overwrite managed
    components. Its only such setting is OVERWRITE_ENV, read from the
    environment; it is refused if present at all, whatever its value, so
    no reading of the value can let it through."""
    environ = os.environ if environ is None else environ
    if components.OVERWRITE_ENV in environ:
        raise Refused("%s is set (%r). It lets the Component Manager replace "
                      "managed components without checking them, so a "
                      "release build is refused (R10); unset it."
                      % (components.OVERWRITE_ENV,
                         environ[components.OVERWRITE_ENV]))
    ok("no managed-component overwrite", "%s unset" % components.OVERWRITE_ENV)


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


def check_clean(root, when="at the start", commit=None):
    """R8: the worktree is clean, and, given commit, HEAD is still it.
    Returns HEAD."""
    head, dirty = worktree_state(root)
    if commit is not None and head != commit:
        raise Refused("HEAD moved %s (%s, now %s), so no one commit describes "
                      "the image (R8)" % (when, commit, head))
    if dirty:
        shown = "\n".join("    " + ln for ln in dirty[:20])
        more = ("\n    ... and %d more" % (len(dirty) - 20)
                if len(dirty) > 20 else "")
        raise Refused("%s, the worktree has %d uncommitted change(s) or "
                      "untracked file(s). A release build is built from a "
                      "clean worktree and leaves it clean (R8), so that its "
                      "record's commit describes the image.\n%s%s"
                      % (when, len(dirty), shown, more))
    ok("worktree clean (R8)", "%s, HEAD %s" % (when, head))
    return head


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
            dest=None, jobs=None, ninja=None):
    """The release build. Returns the record's path (None for check_only).
    Raises Refused with the reason. jobs is --jobs: None builds with
    idf.py build, N with ninja -j N. idf, ninja, toolchain_run and dest
    replace the real idf.py and ninja runners, the compiler call and the
    record folder (tests)."""
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
    check_jobs(jobs)
    if jobs is not None and ninja is None:
        ninja = ninja_runner()
    check_no_overwrite()

    commit = check_clean(root)
    if check_only:
        return None

    # R10: the manager's strict mode for every configure this run makes.
    strict = {components.STRICT_ENV: "1"}
    build_dir = os.path.join(root, "build")
    say("\nidf.py --no-ccache reconfigure (%s=1) ..." % components.STRICT_ENV)
    rc = idf(["--no-ccache", "reconfigure"], strict)
    if rc != 0:
        raise Refused("idf.py reconfigure failed (exit %d)" % rc)
    ok("reconfigure (R9)")
    check_clean(root, "after the reconfigure", commit)
    check_no_ccache(build_dir, "after the reconfigure")
    check_no_launcher(build_dir, "after the reconfigure")

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
        elif r["files_checked"]:
            ok("component %s" % name,
               "%d files match CHECKSUMS.json; .component_hash matches the "
               "lock" % r["files_checked"])
        else:
            ok("component %s" % name,
               "no CHECKSUMS.json; .component_hash matches the lock (the "
               "strict reconfigure re-hashed its files)")
    if not good:
        raise Refused("a downloaded component's .component_hash differs from "
                      "dependencies.lock, or it differs from its CHECKSUMS.json, "
                      "or one the lock names is missing (R10). "
                      "managed_components/ is git-ignored, so the clean "
                      "worktree says nothing about it.")
    if not report:
        ok("downloaded components (R10)", "none in the lock or on disk")

    bd = BuildDir(build_dir)
    lock_path = os.path.join(root, "dependencies.lock")
    try:
        locks = lockfile.read(lock_path)
        shared = check_shared_pins(proj, locks, build_manifests(bd))
    except (OSError, lockfile.LockError, BuildDirError) as e:
        raise Refused("the pins cannot be read: %s" % e)

    if jobs is None:
        what = "idf.py build"
        say("\nidf.py --no-ccache build (%s=1) ..." % components.STRICT_ENV)
        rc = idf(["--no-ccache", "build"], strict)
    else:
        what = "ninja -j %d build" % jobs
        args = ["-C", build_dir, "-j", str(jobs), "all"]
        say("\nninja %s (%s=1) ..." % (" ".join(args), components.STRICT_ENV))
        rc = ninja(args, strict)
    if rc != 0:
        raise Refused("%s failed (exit %d)" % (what, rc))
    check_no_ccache(build_dir, "after the build")
    check_no_launcher(build_dir, "after the build",
                      cmake_build_folders(build_dir))

    bd = BuildDir(build_dir)
    try:
        bin_path, elf_path = bd.app_bin(), bd.app_elf()
        desc = bd.description()
        comps = bd.components()
        img = AppImage.read(bin_path)
    except (BuildDirError, ImageError, OSError) as e:
        raise Refused(str(e))
    ok("image named by the build (R11)", os.path.basename(bin_path))

    check_clean(root, "at the end of the build", commit)

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
            "release_checks": {"managed_components": report},
        }
        path = record.write(proj.name, bin_path, elf_path, fields,
                            dest=out_dir)
    except (record.RecordError, ImageError, OSError) as e:
        raise Refused("no record written: %s" % e)
    ok("record (R13)", path)
    say("           image hash %s" % img.image_hash)
    say("           code hash  %s" % img.code_hash)
    return path


def main(args):
    say("=" * 72)
    say("RELEASE BUILD (shared/tools SPEC section 3)")
    say("=" * 72)
    try:
        path = release(args.project, check_only=args.check_only,
                       jobs=args.jobs)
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
