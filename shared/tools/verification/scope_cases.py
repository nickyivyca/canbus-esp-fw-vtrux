#!/usr/bin/env python3
"""R17 scope check (vtrux_checks scope) on real builds, shown passing and failing.

    python -B scope_cases.py --root <short folder in scratch/> --out <run folder> [--cases a,b] [--jobs N]
    python -B scope_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(a clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses. Each
case makes a build, confirms its trigger by an observation that does not use the check's
own reader (ninja's `-t deps`, or the text of build.ninja), then runs the check's command
line and judges its exit code and whether its output names the planted file.

    clean_release     the fixture by the release build: passes --release and --bench (since port
                      step 1a C with the board taken from git at its tag, R17's git clause)
    version_edited    then its mdns manifest's version changed: fails (restored after)
    managed_unlisted  then a managed component the lock does not list: fails (removed after)
  R17's git clause (ruled 2026-10-27), on clean_release's build, each undone after; each needs
  clean_release to have passed both ways and the board taken from git with its tag naming the
  lock's commit (else VOID), and fails both ways naming managed_components/vtrux__wican_v3:
    git_lock_commit   the lock's board commit changed to the tag commit's parent
    git_tag_absent    the pinned tag deleted from the project's repository
    git_tag_branch    the tag deleted and a branch of its name made at its commit
    git_own_version   the downloaded board's own idf_component.yml given a version
  R17's annotated tags (shared/main b21cb87, ruled 2026-10-28), the same way but with the
  lock's board version replaced by an annotated tag's own hash, made in the project's
  repository; the trigger is the tag's chain of objects read with git cat-file, not
  rev-parse's peel (clean_release is the lightweight-tag pass: the real tag is lightweight):
    git_lock_tag_other  an annotated tag on the tag commit's parent: fails both ways, naming
                        managed_components/vtrux__wican_v3:
    git_lock_tag_same   another annotated tag on the tag's commit: passes both
    git_lock_tag_of_tag an annotated tag of such a tag: passes both
    outside_header    a bench build whose main.c includes a header from outside the
                      project root: fails --bench and --release, naming the header
    detached_worktree a bench build in a detached linked worktree of the fixture clone, which
                      reads the clone's HEAD beside its own (R17's git folder, ruled
                      2026-10-28): passes both; VOID unless its CMakeFiles/git-data/head-ref
                      holds the clone's HEAD and the worktree's own HEAD is a commit
    shared_override   a bench build taking the fx-r16 shared component by override_path
                      from a ../shared-fx/shared/ checkout: passes --bench, fails --release
    other_override    the same from ../elsewhere/, not a shared/ folder: fails both

Before each case it checks the bench lock, the "No builds" line and running simulators,
as release_cases.py does.
"""

import json
import os
import platform
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402

TOOLS = RC.TOOLS
SC = "vtrux_fx_shared"           # the component's folder name: IDF names a component by it


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def judge(rc, out, want_rc, names=(), absent=()):
    """(verdict, detail) for one run of the check."""
    low = out.lower() if os.name == "nt" else out
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    if rc != want_rc:
        return "FAIL", "the check exited %d, wanted %d" % (rc, want_rc)
    missing = [n for n in names if (n.lower() if os.name == "nt" else n) not in low]
    if missing:
        return "FAIL", "exit %d, but the output does not name %s" % (rc, missing)
    there = [n for n in absent if (n.lower() if os.name == "nt" else n) in low]
    if there:
        return "FAIL", "exit %d, but the output names %s" % (rc, there)
    return "PASS", "exit %d%s" % (rc, (", names " + ", ".join(names)) if names else "")


def self_test():
    ok = True
    probe, other = norm(os.path.join(os.sep, "x", "probe.h")), norm(os.path.join(os.sep, "x", "other.h"))
    out = "R17 scope ...\nFAIL R17: 1 files read from outside the scope\n    %s\n" % probe
    for label, args, want in (("passes as wanted", (0, "PASS R17\n", 0), "PASS"),
                              ("fails as wanted, naming the file", (1, out, 1, [probe]), "PASS"),
                              ("fails as wanted, naming a component", (1, out + "  x__y: no entry\n", 1, ["x__y"]), "PASS"),
                              ("passes when it should fail", (0, "PASS R17\n", 1), "FAIL"),
                              ("fails when it should pass", (1, out, 0), "FAIL"),
                              ("fails without naming the file", (1, out, 1, [other]), "FAIL"),
                              ("names what it should not", (1, out, 1, [], [probe]), "FAIL"),
                              ("crashes", (2, "Traceback", 0), "ERROR")):
        v = judge(*args)[0]
        print("  %s %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    sha, ref = b"0123456789abcdef0123456789abcdef01234567\n", b"ref: refs/heads/fixture\n"
    for label, args, want in (("the clone's HEAD copied, the worktree detached", (sha, ref, ref), True),
                              ("head-ref is the worktree's own HEAD", (sha, ref, sha), False),
                              ("no head-ref", (sha, ref, None), False),
                              ("the worktree on a branch", (ref, ref, ref), False),
                              ("the clone detached at the same commit", (sha, sha, sha), False),
                              ("no clone HEAD", (sha, None, ref), False)):
        v = detached_read_clone_head(*args)[0]
        print("  %s detached trigger, %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    ok &= self_test_git(RC.I.scratch("scope-cases-selftest-git"))
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def check(ctx, proj, kind, log):
    e = RC.idf_env(PYTHONPATH=TOOLS)
    rc, out = RC.run([sys.executable, "-B", "-m", "vtrux_checks", "scope", "--build", os.path.join(proj, "build"),
                      "--" + kind], cwd=proj, env=e, log=log)
    return rc, out


def deps_text(proj, log):
    return RC.run(["ninja", "-C", os.path.join(proj, "build"), "-t", "deps"], log=None)[1]


def ninja_text(proj):
    with open(os.path.join(proj, "build", "build.ninja"), encoding="utf-8", errors="replace") as f:
        return f.read().replace("$:", ":")


def mentions(text, path):
    """path appears in text, with either slash, case-folded on Windows."""
    t = text.replace("\\", "/")
    p = os.path.abspath(path).replace("\\", "/")
    if os.name == "nt":
        t, p = t.lower(), p.lower()
    return p in t


def two(ctx, name, proj, log, want, names=()):
    """Run the check both ways; want = {"release": rc, "bench": rc}. names (paths already
    norm()ed, or component folder names) must appear in a failing run's output. Returns the two
    verdicts."""
    verdicts = []
    for kind in ("release", "bench"):
        rc, out = check(ctx, proj, kind, log)
        v, d = judge(rc, out, want[kind], names if want[kind] == 1 else ())
        RC.report("%s --%s" % (name, kind), v, d, ctx.log)
        verdicts.append(v)
    return verdicts


def bench_build(ctx, proj, log):
    args = ["build"] if not ctx.jobs else ["reconfigure"]
    rc, _ = RC.idf(ctx, proj, *args, log=log)
    if rc == 0 and ctx.jobs:
        rc, _ = RC.run(["ninja", "-C", os.path.join(proj, "build"), "-j", str(ctx.jobs), "all"],
                       env=RC.idf_env(), log=log)
    return rc


def mdns_manifest(ctx):
    return os.path.join(ctx.p1, "managed_components", "espressif__mdns", "idf_component.yml")


# ---------- cases ----------

def c_clean_release(ctx, log):
    RC.reset(ctx.p1, "fixture")
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report("clean_release", "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return
    with open(log, "a") as f:
        f.write("board facts (tag, its commit, its ref, the lock's commit): %s\n" % (board_facts(ctx),))
    ctx.clean_passed = two(ctx, "clean_release", ctx.p1, log, {"release": 0, "bench": 0}) == ["PASS", "PASS"]
    ctx.clean_built = True


def c_version_edited(ctx, log):
    if not getattr(ctx, "clean_built", False):
        RC.report("version_edited", "VOID", "needs clean_release in the same batch", ctx.log)
        return
    p = mdns_manifest(ctx)
    raw = open(p, "rb").read()
    m = re.search(rb"^version: *'?\"?([0-9][^'\"\s]*)", raw, re.M)
    if not m:
        RC.report("version_edited", "VOID", "no top-level version line in mdns's manifest", ctx.log)
        return
    edited = raw[:m.start(1)] + m.group(1) + b".99" + raw[m.end(1):]
    try:
        open(p, "wb").write(edited)
        with open(log, "a") as f:
            f.write("mdns manifest version %s -> %s.99\n" % (m.group(1).decode(), m.group(1).decode()))
        two(ctx, "version_edited", ctx.p1, log, {"release": 1, "bench": 1}, names=["espressif__mdns"])
    finally:
        open(p, "wb").write(raw)


def c_managed_unlisted(ctx, log):
    if not getattr(ctx, "clean_built", False):
        RC.report("managed_unlisted", "VOID", "needs clean_release in the same batch", ctx.log)
        return
    d = os.path.join(ctx.p1, "managed_components", "tester__r17_probe")
    try:
        os.makedirs(d)
        with open(os.path.join(d, "idf_component.yml"), "w", newline="") as f:
            f.write('version: "1.0.0"\n')
        two(ctx, "managed_unlisted", ctx.p1, log, {"release": 1, "bench": 1}, names=["tester__r17_probe"])
    finally:
        RC.rmtree(d)


BOARD = "vtrux/wican_v3"


def self_test_git(d):
    """The git cases' plumbing on a small repo (the check itself replaced by a recorder): each
    case's change shows, the check is run once, and everything is undone after."""
    global two
    ok = True
    proj = os.path.join(d, "p")
    RC.run(["git", "init", "-q", "-b", "fixture", proj], check=True)
    RC.git(proj, "commit", "-q", "--allow-empty", "-m", "c0")
    RC.git(proj, "commit", "-q", "--allow-empty", "-m", "c1")
    c1 = RC.git(proj, "rev-parse", "HEAD")
    RC.git(proj, "tag", "-a", "-m", "the board's tag", "2026.10.27")
    os.makedirs(os.path.join(proj, "main"))
    with open(os.path.join(proj, "main", "idf_component.yml"), "w", newline="") as f:
        f.write('dependencies:\n  vtrux/wican_v3:\n    git: https://h/r.git\n    path: shared/boards/wican_v3\n'
                '    version: "2026.10.27"\n')
    with open(os.path.join(proj, "dependencies.lock"), "w", newline="") as f:
        f.write("dependencies:\n  espressif/mdns:\n    version: 1.4.0\n  vtrux/wican_v3:\n    source:\n"
                "      type: git\n    version: %s\nmanifest_hash: x\n" % c1)
    md = os.path.join(proj, "managed_components", "vtrux__wican_v3")
    os.makedirs(md)
    with open(os.path.join(md, "idf_component.yml"), "w", newline="") as f:
        f.write("dependencies:\n  idf: '>=5.4'\n")
    with open(os.path.join(proj, ".gitignore"), "w", newline="") as f:
        f.write("managed_components/\n")
    RC.git(proj, "add", "-A")
    RC.git(proj, "commit", "-q", "-m", "project")

    class C:
        p1, clean_built, clean_passed, log = proj, True, True, os.path.join(d, "driver.log")
    calls = []
    real_two, real_report = two, RC.report
    reports = []
    two = lambda ctx, name, p, log, want, names=(): calls.append((name, board_facts(ctx), open(
        os.path.join(md, "idf_component.yml")).read(), RC.ref(proj, "refs/heads/2026.10.27"), want, list(names)))
    RC.report = lambda name, v, detail, log: reports.append((name, v))
    try:
        before = board_facts(C)
        files = (open(os.path.join(proj, "dependencies.lock"), "rb").read(), open(os.path.join(md, "idf_component.yml"), "rb").read())
        c0 = RC.git(proj, "rev-parse", "HEAD~2")

        def lock_tag(chain_types, commit):
            return lambda f, m, b: (f[1] == c1 and f[3] not in (c1, f[2]) and [t for _, t in tag_chain(proj, f[3])]
                                    == chain_types and tag_chain(proj, f[3])[-1][0] == commit)
        for fn, shows, want in ((c_git_lock_commit, lambda f, m, b: f[3] != f[1] and f[3] == c0, 1),
                                (c_git_tag_absent, lambda f, m, b: f[1] == "" and b == "", 1),
                                (c_git_tag_branch, lambda f, m, b: f[1] == "" and b == c1, 1),
                                (c_git_own_version, lambda f, m, b: m.startswith('version: "2026.10.27"'), 1),
                                (c_git_lock_tag_other, lock_tag(["tag", "commit"], c0), 1),
                                (c_git_lock_tag_same, lock_tag(["tag", "commit"], c1), 0),
                                (c_git_lock_tag_of_tag, lock_tag(["tag", "tag", "commit"], c1), 0)):
            del calls[:], reports[:]
            fn(C, os.path.join(d, "case.log"))
            after = board_facts(C)
            same = after == before and files == (open(os.path.join(proj, "dependencies.lock"), "rb").read(),
                                                  open(os.path.join(md, "idf_component.yml"), "rb").read()) \
                and RC.ref(proj, "refs/heads/2026.10.27") == "" and RC.git(proj, "tag", "-l", "r17-*") == ""
            good = (len(calls) == 1 and shows(*calls[0][1:4]) and calls[0][4] == {"release": want, "bench": want}
                    and calls[0][5] == (["managed_components/vtrux__wican_v3:"] if want else []) and same and not reports)
            print("  %s %s: the change shown to the check (%s), undone after: %s" % (
                "ok  " if good else "FAIL", fn.__name__, calls[0][1] if calls else reports, same))
            ok &= good
        good = before == ("2026.10.27", c1, RC.git(proj, "rev-parse", "refs/tags/2026.10.27"), c1) and before[2] != c1
        print("  %s board_facts: the tag, its commit, its (annotated) tag object, the lock's commit" % ("ok  " if good else "FAIL"))
        ok &= good
        C.clean_passed = False
        del calls[:], reports[:]
        c_git_tag_absent(C, os.path.join(d, "case.log"))
        good = not calls and reports and reports[0][1] == "VOID"
        print("  %s clean_release built but did not pass: VOID, no check run" % ("ok  " if good else "FAIL"))
        ok &= bool(good)
        C.clean_built = False
        del calls[:], reports[:]
        c_git_tag_absent(C, os.path.join(d, "case.log"))
        good = not calls and reports and reports[0][1] == "VOID"
        print("  %s without clean_release: VOID, no check run" % ("ok  " if good else "FAIL"))
        ok &= bool(good)
    finally:
        two, RC.report = real_two, real_report
    return ok


def board_facts(ctx):
    """(tag, the commit refs/tags/<tag> names, the tag's ref target, the lock's board version) by
    the tester's own reading: the tag main/idf_component.yml pins the board at, git, and the lock's
    text. None when the fixture takes no board from git."""
    import yaml
    deps = (yaml.safe_load(open(os.path.join(ctx.p1, "main", "idf_component.yml"))) or {}).get("dependencies") or {}
    b = deps.get(BOARD)
    if not isinstance(b, dict) or not b.get("git"):
        return None
    tag = str(b["version"])
    lock = (yaml.safe_load(open(os.path.join(ctx.p1, "dependencies.lock"))) or {}).get("dependencies") or {}
    return (tag, RC.ref(ctx.p1, "refs/tags/" + tag), RC.run(["git", "-C", ctx.p1, "rev-parse", "-q", "--verify",
                                                             "refs/tags/" + tag])[1].strip(),
            str((lock.get(BOARD) or {}).get("version")))


def git_case(ctx, log, name, change, undo, want=1):
    """clean_release's build with the board's git facts changed by change(facts) -> trigger shown
    (else VOID), undone by undo(facts) whatever happens: R17 fails both ways, naming the board's
    managed folder (want 1), or passes both (want 0). VOID unless clean_release passed both ways,
    since only then does naming the folder show the change was seen."""
    if not getattr(ctx, "clean_built", False):
        RC.report(name, "VOID", "needs clean_release in the same batch", ctx.log)
        return
    if not getattr(ctx, "clean_passed", False):
        RC.report(name, "VOID", "clean_release did not pass, and a failing clean build may name the board's "
                  "folder already", ctx.log)
        return
    f = board_facts(ctx)
    if f is None or not f[1] or f[1] != f[3]:
        RC.report(name, "VOID", "no git board whose tag names the lock's commit to change: %s" % (f,), ctx.log)
        return
    try:
        shown = change(f)
        with open(log, "a") as fx:
            fx.write("%s: board facts before %s; after %s; trigger %s\n" % (name, f, board_facts(ctx), shown))
        if not shown:
            RC.report(name, "VOID", "the change did not show: %s" % (board_facts(ctx),), ctx.log)
        else:
            two(ctx, name, ctx.p1, log, {"release": want, "bench": want},
                names=["managed_components/vtrux__wican_v3:"] if want else ())
    finally:
        undo(f)
        with open(log, "a") as fx:
            fx.write("%s: board facts restored %s\n" % (name, board_facts(ctx)))


def c_git_lock_commit(ctx, log):
    lp = os.path.join(ctx.p1, "dependencies.lock")

    def change(f):
        other = RC.git(ctx.p1, "rev-parse", f[1] + "^")
        text = open(lp, newline="").read()
        if text.count("version: %s" % f[3]) != 1:
            return False
        with open(lp, "w", newline="") as fh:
            fh.write(text.replace("version: %s" % f[3], "version: %s" % other))
        g = board_facts(ctx)
        return g[3] == other and g[1] != other
    git_case(ctx, log, "git_lock_commit", change, lambda f: RC.git(ctx.p1, "checkout", "-q", "--", "dependencies.lock"))


def c_git_tag_absent(ctx, log):
    def change(f):
        RC.git(ctx.p1, "tag", "-d", f[0])
        return RC.ref(ctx.p1, "refs/tags/" + f[0]) == ""
    git_case(ctx, log, "git_tag_absent", change, lambda f: RC.git(ctx.p1, "update-ref", "refs/tags/" + f[0], f[2]))


def c_git_tag_branch(ctx, log):
    def change(f):
        RC.git(ctx.p1, "tag", "-d", f[0])
        RC.git(ctx.p1, "branch", f[0], f[1])
        return RC.ref(ctx.p1, "refs/tags/" + f[0]) == "" and RC.ref(ctx.p1, "refs/heads/" + f[0]) == f[1]

    def undo(f):
        RC.run(["git", "-C", ctx.p1, "branch", "-q", "-D", f[0]])
        RC.git(ctx.p1, "update-ref", "refs/tags/" + f[0], f[2])
    git_case(ctx, log, "git_tag_branch", change, undo)


def c_git_own_version(ctx, log):
    p = os.path.join(ctx.p1, "managed_components", "vtrux__wican_v3", "idf_component.yml")
    keep = {}

    def change(f):
        keep["raw"] = open(p, "rb").read() if os.path.exists(p) else None
        with open(p, "wb") as fh:
            fh.write(b'version: "%s"\n' % f[0].encode() + (keep["raw"] or b""))
        return True

    def undo(f):
        if keep.get("raw") is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            with open(p, "wb") as fh:
                fh.write(keep["raw"])
    git_case(ctx, log, "git_own_version", change, undo)


def c_outside_header(ctx, log):
    RC.reset(ctx.p1, "fixture")
    outside = os.path.join(ctx.root, "outside")
    os.makedirs(outside, exist_ok=True)
    h = os.path.join(outside, "r17_outside_probe.h")
    with open(h, "w", newline="") as f:
        f.write("/* R17 scope case: a header outside the project root */\n#define R17_OUTSIDE_PROBE 1\n")
    with open(os.path.join(ctx.p1, "main", "main.c"), "a", newline="") as f:
        f.write('\n#include "%s"\n' % h.replace("\\", "/"))
    rc = bench_build(ctx, ctx.p1, log)
    if rc or not mentions(deps_text(ctx.p1, log), h):
        RC.report("outside_header", "VOID", "build exit %d; header in ninja -t deps: %s" % (
            rc, mentions(deps_text(ctx.p1, log), h)), ctx.log)
    else:
        two(ctx, "outside_header", ctx.p1, log, {"release": 1, "bench": 1}, names=[norm(h)])
    RC.reset(ctx.p1, "fixture")


def detached_read_clone_head(own_head, clone_head, head_ref):
    """(read, why): the build copied the clone's HEAD (its CMakeFiles/git-data/head-ref holds
    the clone's HEAD bytes) while the worktree's own HEAD is detached (a commit, not a ref)."""
    own, clone, got = [(b or b"").strip() for b in (own_head, clone_head, head_ref)]
    if not re.fullmatch(rb"[0-9a-f]{40}", own):
        return False, "the worktree's HEAD is not a detached commit: %r" % own[:60]
    if clone == own:
        return False, "the clone's HEAD is the worktree's, so a copy proves nothing"
    if got != clone:
        return False, "head-ref %r is not the clone's HEAD %r" % (got[:60], clone[:60])
    return True, "head-ref is the clone's HEAD %r; the worktree's is %s" % (clone[:60].decode(errors="replace"),
                                                                        own.decode())


def c_detached_worktree(ctx, log):
    RC.reset(ctx.p1, "fixture")
    wt = os.path.join(ctx.root, "wtd")
    RC.rmtree(wt)
    RC.git(ctx.repo_p1, "worktree", "prune")
    RC.git(ctx.repo_p1, "worktree", "add", "-q", "--detach", wt, "fixture")
    try:
        proj = RC.in_sub(wt, ctx.sub)
        rc = bench_build(ctx, proj, log)
        gd = RC.git(proj, "rev-parse", "--absolute-git-dir")
        common = RC.git(proj, "rev-parse", "--path-format=absolute", "--git-common-dir")

        def raw(*p):
            try:
                with open(os.path.join(*p), "rb") as f:
                    return f.read()
            except OSError:
                return None
        read, why = detached_read_clone_head(raw(gd, "HEAD"), raw(common, "HEAD"),
                                             raw(proj, "build", "CMakeFiles", "git-data", "head-ref"))
        with open(log, "a") as f:
            f.write("detached worktree %s: git dir %s, clone's %s; build exit %d; %s\n" % (wt, gd, common, rc, why))
        if rc or not read:
            RC.report("detached_worktree", "VOID", "build exit %d; %s" % (rc, why), ctx.log)
        else:
            two(ctx, "detached_worktree", proj, log, {"release": 0, "bench": 0})
    finally:
        RC.git(ctx.repo_p1, "worktree", "remove", "--force", wt)


def override(ctx, log, name, folder, want):
    """fx-r16 with its shared component copied to folder and taken from there by override_path."""
    RC.reset(ctx.p1, "fx-r16")
    RC.rmtree(folder)
    shutil.copytree(os.path.join(ctx.p1, "fx_shared", "vtrux_fx_shared"), folder)
    y = os.path.join(ctx.p1, "main", "idf_component.yml")
    s = open(y, newline="").read()
    s2 = s.replace('override_path: "../fx_shared/vtrux_fx_shared"', 'override_path: "%s"' % folder.replace("\\", "/"))
    if s2 == s:
        RC.report(name, "VOID", "fx-r16's override_path line not found", ctx.log)
        return
    with open(y, "w", newline="") as f:
        f.write(s2)
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc = bench_build(ctx, ctx.p1, log)
    src = os.path.join(folder, "fx_shared.c")
    took = rc == 0 and mentions(ninja_text(ctx.p1), src)
    with open(log, "a") as f:
        f.write("override_path -> %s; build exit %d; %s in build.ninja: %s\n" % (folder, rc, src, took))
    if not took:
        RC.report(name, "VOID", "build exit %d; the override's source in build.ninja: %s" % (rc, took), ctx.log)
    else:
        two(ctx, name, ctx.p1, log, want, names=[norm(src)])
    RC.reset(ctx.p1, "fixture")
    RC.rmtree(os.path.join(ctx.p1, "build"))


def tag_chain(repo, obj):
    """[(object, type)] from obj through each annotated tag's target, read with git cat-file (not
    rev-parse's peel, which is the check's own reader), ending at the first object not a tag."""
    out = []
    while len(out) < 10:
        t = RC.git(repo, "cat-file", "-t", obj)
        out.append((obj, t))
        if t != "tag":
            break
        obj = RC.git(repo, "cat-file", "-p", obj).splitlines()[0].split()[1]   # "object <hash>"
    return out


LOCK_TAGS = ("r17-lock-tag", "r17-lock-tag-of-tag")


def lock_tag_case(ctx, log, name, onto, of_tag, want):
    """The lock's board version replaced by the hash of an annotated tag made in the project's
    repository on onto(facts) (of_tag: then a tag of that tag), shown by tag_chain."""
    lp = os.path.join(ctx.p1, "dependencies.lock")

    def change(f):
        target = onto(f)
        RC.git(ctx.p1, "tag", "-a", "-m", name, LOCK_TAGS[0], target)
        top = LOCK_TAGS[0]
        if of_tag:
            RC.git(ctx.p1, "tag", "-a", "-m", name, LOCK_TAGS[1], LOCK_TAGS[0])
            top = LOCK_TAGS[1]
        obj = RC.git(ctx.p1, "rev-parse", "refs/tags/" + top)
        text = open(lp, newline="").read()
        if text.count("version: %s" % f[3]) != 1:
            return False
        with open(lp, "w", newline="") as fh:
            fh.write(text.replace("version: %s" % f[3], "version: %s" % obj))
        chain = tag_chain(ctx.p1, obj)
        return (board_facts(ctx)[3] == obj and obj not in (f[1], f[2]) and chain[-1] == (target, "commit")
                and [t for _, t in chain] == ["tag"] * (2 if of_tag else 1) + ["commit"])

    def undo(f):
        RC.git(ctx.p1, "checkout", "-q", "--", "dependencies.lock")
        for t in LOCK_TAGS:
            RC.run(["git", "-C", ctx.p1, "tag", "-d", t])
    git_case(ctx, log, name, change, undo, want)


def c_git_lock_tag_other(ctx, log):
    lock_tag_case(ctx, log, "git_lock_tag_other", lambda f: RC.git(ctx.p1, "rev-parse", f[1] + "^"), False, 1)


def c_git_lock_tag_same(ctx, log):
    lock_tag_case(ctx, log, "git_lock_tag_same", lambda f: f[1], False, 0)


def c_git_lock_tag_of_tag(ctx, log):
    lock_tag_case(ctx, log, "git_lock_tag_of_tag", lambda f: f[1], True, 0)


def c_shared_override(ctx, log):
    override(ctx, log, "shared_override", os.path.join(ctx.root, "shared-fx", "shared", SC), {"release": 1, "bench": 0})


def c_other_override(ctx, log):
    override(ctx, log, "other_override", os.path.join(ctx.root, "elsewhere", SC), {"release": 1, "bench": 1})


CASES = [("clean_release", c_clean_release), ("version_edited", c_version_edited),
         ("managed_unlisted", c_managed_unlisted), ("git_lock_commit", c_git_lock_commit),
         ("git_tag_absent", c_git_tag_absent), ("git_tag_branch", c_git_tag_branch),
         ("git_own_version", c_git_own_version), ("git_lock_tag_other", c_git_lock_tag_other),
         ("git_lock_tag_same", c_git_lock_tag_same), ("git_lock_tag_of_tag", c_git_lock_tag_of_tag),
         ("outside_header", c_outside_header),
         ("detached_worktree", c_detached_worktree),
         ("shared_override", c_shared_override), ("other_override", c_other_override)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out) or "IDF_PATH" not in os.environ:
        ap.error("--root and --out, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.log = os.path.join(ctx.out, "scope_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain") or "clean", names))
    if not os.path.exists(ctx.src):
        ap.error("no fixture at %s: make it with release_cases.py first" % ctx.src)
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "scope_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    with open(os.path.join(ctx.out, "scope_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
