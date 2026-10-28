#!/usr/bin/env python3
"""SPEC R19, the reproducible test, on real release builds, shown passing and failing.

    python -B repro_cases.py --root <short folder in scratch/> --out <run folder>
                             [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B repro_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
and minimal project (under --root) and plumbing it reuses. R19 is a test of the tooling: rerun
it when the build script or record writer, the image-reading library, the ESP-IDF version, its
Python environment (esptool included), or the compiler, CMake or Ninja version changes, and once
for each project when it first builds with the tooling. The driver log records each of those.

Each case takes one commit and clones it into two new folders, --root/r19/<key>/a and
--root/r19/<key>/b_longer/deeper (paths of another length and depth), then runs the release
build script in each, one after the other, each with its own stand-in record folder. Each
build's image hash is read three ways: from the record the script wrote, by the tooling's
image-reading library (vtrux_build.image) and by the tester's own reader (independent.py).
The three must agree within a build. R19 is met when the two builds give the same image hash.

A case is VOID unless both clones are clean at the same commit in different folders, with no
build folder before the build, and its trigger shows in each build: the reproducible-build
setting in config/sdkconfig.h, and for time_macro and build_path the case's marker string in
the .bin.

    fixture     release_cases.py's fixture commit (reproducible build on): R19 met
    repro_off   the fixture with CONFIG_APP_REPRODUCIBLE_BUILD off, which is gen-inhibit as
                it is until port step 0b: gen-inhibit's own R19 run, so R19 met
    mini        the minimal project (hello_world, esp32c3, reproducible build on): R19 met
    time_macro  the minimal project printing __DATE__ and __TIME__: R19 met, since the release
                build sets one fixed SOURCE_DATE_EPOCH (R9)
    build_path  the minimal project printing a define that holds CMAKE_BINARY_DIR, which
                differs between the two folders: R19 not met

The case verdict is PASS when R19's result is the one the case wants, so build_path passing
shows the test can fail.

Before each case it checks the bench lock, the "No builds" line and running simulators, as
release_cases.py does.
"""

import hashlib
import json
import os
import platform
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402
import independent as I  # noqa: E402
import options_cases as OC  # noqa: E402

TOOLS = RC.TOOLS
MARK = "r19 time_macro built"
TIME_LINE = '    printf("%s %%s %%s\\n", __DATE__, __TIME__);\n' % MARK
PATH_MARK = "r19 build_path in"
PATH_LINE = '    printf("%s %%s\\n", R19_BUILD_DIR);\n' % PATH_MARK
PATH_DEFINE = '\ntarget_compile_definitions(${COMPONENT_LIB} PRIVATE "R19_BUILD_DIR=\\"${CMAKE_BINARY_DIR}\\"")\n'


# ---------- judges (pure; --self-test runs them on fakes) ----------

def premise(a, b):
    """a, b: {"path", "commit", "status", "build_before", "repro", "marker"} for each clone; the
    case wants "repro" (True/False) and, when "marker" is not None, the marker in the .bin.
    Returns "" or why the case is VOID."""
    why = []
    if os.path.normcase(os.path.abspath(a["path"])) == os.path.normcase(os.path.abspath(b["path"])):
        why.append("one folder, not two")
    if a["commit"] != b["commit"]:
        why.append("commits differ: %s, %s" % (a["commit"][:12], b["commit"][:12]))
    for side, x in (("a", a), ("b", b)):
        if x["status"]:
            why.append("%s not clean: %s" % (side, x["status"][:80]))
        if x["build_before"]:
            why.append("%s had a build folder before the build" % side)
        if x["repro_seen"] != x["repro"]:
            why.append("%s: reproducible build %s in sdkconfig.h, the case wants %s" % (
                side, "on" if x["repro_seen"] else "off", "on" if x["repro"] else "off"))
        if x["marker"] is not None and not x["marker"]:
            why.append("%s: the marker is not in the .bin" % side)
    return "; ".join(why)


def judge(want_met, a, b):
    """a, b: {"record", "library", "independent", "bin_sha256"} from each build. (verdict, detail)."""
    for side, x in (("a", a), ("b", b)):
        got = (x["record"], x["library"], x["independent"])
        if None in got or len(set(got)) != 1:
            return "FAIL", "build %s: the record, the library and the tester's reader give %s" % (
                side, ", ".join(str(g)[:16] for g in got))
    met = a["independent"] == b["independent"]
    detail = "R19 %s: image hash %s, %s" % ("met" if met else "not met", a["independent"][:16],
                                            b["independent"][:16])
    if met and a["bin_sha256"] != b["bin_sha256"]:
        return "FINDING", detail + "; the same image hash but the .bin bytes differ"
    if met != want_met:
        return "FAIL", detail + "; the case wants R19 %s" % ("met" if want_met else "not met")
    return "PASS", detail


def self_test():
    ok = True
    h1, h2 = "1" * 64, "2" * 64

    def side(h, rec=None, lib=None, ind=None, b="b1"):
        return {"record": h if rec is None else rec, "library": h if lib is None else lib,
                "independent": h if ind is None else ind, "bin_sha256": b}
    for label, args, want in (
            ("met, wanted met", (True, side(h1), side(h1)), "PASS"),
            ("not met, wanted not met", (False, side(h1), side(h2, b="b2")), "PASS"),
            ("not met, wanted met", (True, side(h1), side(h2, b="b2")), "FAIL"),
            ("met, wanted not met", (False, side(h1), side(h1)), "FAIL"),
            ("a's record disagrees", (True, side(h1, rec=h2), side(h1)), "FAIL"),
            ("b's library disagrees", (True, side(h1), side(h1, lib=h2)), "FAIL"),
            ("b's own reader disagrees", (False, side(h1), side(h2, ind=h1, b="b2")), "FAIL"),
            ("a has no record", (True, side(h1, rec=False), side(h1)), "FAIL"),
            ("same hash, other bytes", (True, side(h1), side(h1, b="b2")), "FINDING")):
        if args[1]["record"] is False:
            args[1]["record"] = None
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want

    def clone(path, **kw):
        x = {"path": path, "commit": "c" * 40, "status": "", "build_before": False, "repro": True,
             "repro_seen": True, "marker": None}
        x.update(kw)
        return x
    for label, a, b, want in (
            ("as wanted", clone("r/a"), clone("r/b_longer/deeper"), True),
            ("one folder", clone("r/a"), clone("r/./a"), False),
            ("other commits", clone("r/a"), clone("r/b", commit="d" * 40), False),
            ("b dirty", clone("r/a"), clone("r/b", status="?? x"), False),
            ("a had a build", clone("r/a", build_before=True), clone("r/b"), False),
            ("reproducible off, wanted on", clone("r/a"), clone("r/b", repro_seen=False), False),
            ("on, wanted off", clone("r/a", repro=False), clone("r/b", repro=False), False),
            ("off, wanted off", clone("r/a", repro=False, repro_seen=False),
             clone("r/b", repro=False, repro_seen=False), True),
            ("marker missing", clone("r/a", marker=True), clone("r/b", marker=False), False),
            ("marker in both", clone("r/a", marker=True), clone("r/b", marker=True), True)):
        got = premise(a, b) == ""
        print("  %s premise(), %s: %s" % ("ok  " if got == want else "FAIL", label, "ok" if got else "VOID"))
        ok &= got == want

    # the readers on a synthetic image: the library and the tester's own give its embedded hash
    import vtrux_build.image as LIB
    d = I.scratch("repro-cases-selftest")
    img = I.build()
    p = os.path.join(d, "x.bin")
    with open(p, "wb") as f:
        f.write(img)
    r = hashes(p, None)
    want = I.field(img, "elf_sha256").hex()
    good = r["library"] == r["independent"] == want and r["record"] is None
    print("  %s hashes() on a synthetic image: %s" % ("ok  " if good else "FAIL", r["independent"][:16]))
    ok &= good
    other = I.with_field(img, "elf_sha256", bytes(32))
    with open(p, "wb") as f:
        f.write(other)
    r2 = hashes(p, None)
    good = r2["independent"] != want and r2["library"] == r2["independent"] and LIB.image_hash(p) == r2["library"]
    print("  %s hashes() follows the embedded hash: %s" % ("ok  " if good else "FAIL", r2["independent"][:16]))
    ok &= good
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def hashes(bin_path, record_dir):
    """The image hash three ways, and the .bin's own sha256. record_dir: the stand-in folder the
    build wrote its record to (None: no record expected)."""
    import vtrux_build.image as LIB
    data = open(bin_path, "rb").read()
    rec = None
    if record_dir and os.path.isdir(record_dir):
        js = [n for n in os.listdir(record_dir) if n.endswith(".json")]
        if len(js) == 1:
            rec = json.load(open(os.path.join(record_dir, js[0]))).get("image_hash")
    return {"record": rec, "library": LIB.image_hash(bin_path), "independent": I.image_hash(data),
            "bin_sha256": RC.sha256_file(bin_path)}


def app_bin(proj):
    bd = os.path.join(proj, "build")
    return os.path.join(bd, json.load(open(os.path.join(bd, "project_description.json")))["app_bin"])


def repro_seen(proj):
    p = os.path.join(proj, "build", "config", "sdkconfig.h")
    return re.search(r"^#define CONFIG_APP_REPRODUCIBLE_BUILD 1$", open(p, errors="replace").read(), re.M) is not None


def tool_versions():
    """What R19 says to rerun on, as this shell finds it."""
    def first(cmd):
        rc, out = RC.run(cmd)
        return out.strip().splitlines()[0] if out.strip() else "exit %d" % rc
    import vtrux_build.digest as D
    return {"tooling_commit": RC.git(TOOLS, "rev-parse", "HEAD"),
            "tooling_status": RC.git(TOOLS, "status", "--porcelain", "--", ".") or "clean",
            "vtrux_build_digest": D.component_digest(os.path.join(TOOLS, "vtrux_build")),
            "idf": RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            "gcc": first(["riscv32-esp-elf-gcc", "--version"]), "cmake": first(["cmake", "--version"]),
            "ninja": first(["ninja", "--version"]), "python": platform.python_version(),
            "idf_python_env": sys.prefix, "esptool": first([sys.executable, "-m", "esptool", "version"]),
            "pip_freeze_sha256": hashlib.sha256(RC.run([sys.executable, "-m", "pip", "freeze"])[1].encode()).hexdigest(),
            "host": platform.node(), "os": platform.platform()}


def prep(ctx, key, source, branch, edit=None, msg=None, sub=""):
    """A work clone of the repository source at branch, with edit (given the project's folder,
    sub inside the clone) committed and settled as the fixture was made.
    Returns (repo to clone from, commit)."""
    w = os.path.join(ctx.root, "r19", key, "prep")
    RC.rmtree(w)
    RC.run(["git", "clone", "-q", "-b", branch, source, w], check=True)
    if edit:
        p = RC.in_sub(w, sub)
        edit(p)
        RC.git(p, "commit", "-q", "-am", msg)
        with open(os.path.join(ctx.out, "repro_fixture.txt"), "a") as fx:
            RC.normalise(ctx, p, key, fx)
            fx.write("%s: %s\n" % (key, RC.git(w, "rev-parse", "HEAD")))
    return w, RC.git(w, "rev-parse", "HEAD")


def build_one(ctx, key, side, repo, commit, log, want_repro, marker, sub=""):
    clone = os.path.join(ctx.root, "r19", key, *(("a",) if side == "a" else ("b_longer", "deeper")))
    path = RC.in_sub(clone, sub)        # the project's folder in the clone
    ri = os.path.join(ctx.root, "r19", key, "ri_" + side)
    RC.rmtree(clone)
    RC.rmtree(ri)
    os.makedirs(os.path.dirname(clone), exist_ok=True)
    os.makedirs(os.path.join(ri, "firmware"))
    RC.run(["git", "clone", "-q", "--no-checkout", repo, clone], check=True)
    RC.git(clone, "checkout", "-q", "-b", "r19-" + side, commit)
    info = {"path": path, "commit": RC.git(clone, "rev-parse", "HEAD"),
            "status": RC.git(clone, "status", "--porcelain", "--untracked-files=all"),
            "build_before": os.path.exists(os.path.join(path, "build")), "repro": want_repro}
    rc, _ = RC.release(ctx, path, log, jobs=ctx.jobs, env={"REVERSE_IT_DIR": ri})
    info["rc"] = rc
    if rc:
        return info, None
    b = app_bin(path)
    info["repro_seen"] = repro_seen(path)
    info["marker"] = (marker.encode() in open(b, "rb").read()) if marker else None
    project = json.load(open(os.path.join(path, "vtrux_build.json")))["project"]
    h = hashes(b, os.path.join(ri, "firmware", "builds", project))
    with open(log, "a") as f:
        f.write("build %s: %s at %s; image hash record %s library %s own %s; .bin sha256 %s\n\n" % (
            side, path, info["commit"][:12], h["record"], h["library"], h["independent"], h["bin_sha256"]))
    return info, h


def run_case(ctx, key, name, log, repo, commit, want_met, want_repro, marker=None, sub=""):
    res = []
    for side in ("a", "b"):
        info, h = build_one(ctx, key, side, repo, commit, log, want_repro, marker, sub)
        if h is None:
            RC.report(name, "VOID", "build %s: the release build failed (exit %d)" % (side, info["rc"]), ctx.log)
            return
        res.append((info, h))
    (ia, ha), (ib, hb) = res
    why = premise(ia, ib)
    if why:
        RC.report(name, "VOID", why, ctx.log)
        return
    v, d = judge(want_met, ha, hb)
    if v != "PASS" or not want_met:
        d += "; " + RC.differing(open(app_bin(ia["path"]), "rb").read(), open(app_bin(ib["path"]), "rb").read())
    RC.report(name, v, d, ctx.log)
    if not ctx.keep:
        for side in ("a", "b_longer"):
            RC.rmtree(os.path.join(ctx.root, "r19", key, side))


# ---------- cases ----------

def c_fixture(ctx, log):
    repo, commit = prep(ctx, "fx", ctx.repo_src, "fixture", sub=ctx.sub)
    run_case(ctx, "fx", "fixture", log, repo, commit, True, True, sub=ctx.sub)


def c_repro_off(ctx, log):
    def edit(p):
        RC.git(p, "checkout", "-q", "-b", "r19-repro-off")
        OC.set_line(p, RC.REPRO_ON, RC.REPRO_OFF)
    repo, commit = prep(ctx, "off", ctx.repo_src, "fixture", edit, "r19 case repro_off: reproducible build off",
                        sub=ctx.sub)
    run_case(ctx, "off", "repro_off", log, repo, commit, True, False, sub=ctx.sub)


def mini_source(ctx):
    if not os.path.isdir(ctx.mini):
        RC.make_mini(ctx)
    return ctx.mini


def c_mini(ctx, log):
    repo, commit = prep(ctx, "mini", mini_source(ctx), "main")
    run_case(ctx, "mini", "mini", log, repo, commit, True, True)


def add_print(p, line):
    f = os.path.join(p, "main", "hello_world_main.c")
    s = open(f, newline="").read()
    anchor = '    printf("Hello world!\\n");\n'
    if s.count(anchor) != 1:
        raise RuntimeError("hello_world_main.c: %d '%s' lines" % (s.count(anchor), anchor.strip()))
    with open(f, "w", newline="") as o:
        o.write(s.replace(anchor, anchor + line))


def c_time_macro(ctx, log):
    def edit(p):
        RC.git(p, "checkout", "-q", "-b", "r19-time-macro")
        add_print(p, TIME_LINE)
    repo, commit = prep(ctx, "time", mini_source(ctx), "main", edit, "r19 case time_macro: print __DATE__ __TIME__")
    run_case(ctx, "time", "time_macro", log, repo, commit, True, True, marker=MARK)


def c_build_path(ctx, log):
    def edit(p):
        RC.git(p, "checkout", "-q", "-b", "r19-build-path")
        add_print(p, PATH_LINE)
        with open(os.path.join(p, "main", "CMakeLists.txt"), "a", newline="") as f:
            f.write(PATH_DEFINE)
    repo, commit = prep(ctx, "path", mini_source(ctx), "main", edit, "r19 case build_path: print CMAKE_BINARY_DIR")
    run_case(ctx, "path", "build_path", log, repo, commit, False, True, marker=PATH_MARK)


CASES = [("fixture", c_fixture), ("repro_off", c_repro_off), ("mini", c_mini), ("time_macro", c_time_macro),
         ("build_path", c_build_path)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--base", default="gen-inhibit/main", help="unused; the fixture under --root is reused")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--keep", action="store_true", help="keep each case's two clones and builds")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out) or "IDF_PATH" not in os.environ:
        ap.error("--root and --out, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.keep = a.keep
    ctx.log = os.path.join(ctx.out, "repro_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    if not os.path.exists(ctx.src):
        ap.error("no fixture at %s: make it with release_cases.py first" % ctx.src)
    versions = tool_versions()
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s\n%s\ncases %s\n\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                            "\n".join("%s: %s" % kv for kv in versions.items()), names))
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "repro_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    with open(os.path.join(ctx.out, "repro_results.json"), "a") as f:
        f.write(json.dumps({"versions": versions}) + "\n")
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
