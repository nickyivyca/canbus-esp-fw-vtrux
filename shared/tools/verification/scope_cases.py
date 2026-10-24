#!/usr/bin/env python3
"""R17 scope check (vtrux_checks scope) on real builds, shown passing and failing.

    python -B scope_cases.py --root <short folder in scratch/> --out <run folder> [--cases a,b] [--jobs N]
    python -B scope_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(a clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses. Each
case makes a build, confirms its trigger by an observation that does not use the check's
own reader (ninja's `-t deps`, or the text of build.ninja), then runs the check's command
line and judges its exit code and whether its output names the planted file.

    clean_release     the fixture by the release build: passes --release and --bench
    version_edited    then its mdns manifest's version changed: fails (restored after)
    managed_unlisted  then a managed component the lock does not list: fails (removed after)
    outside_header    a bench build whose main.c includes a header from outside the
                      project root: fails --bench and --release, naming the header
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
SC = "vtrux-fx-shared"


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
    norm()ed, or component folder names) must appear in a failing run's output."""
    for kind in ("release", "bench"):
        rc, out = check(ctx, proj, kind, log)
        v, d = judge(rc, out, want[kind], names if want[kind] == 1 else ())
        RC.report("%s --%s" % (name, kind), v, d, ctx.log)


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
    two(ctx, "clean_release", ctx.p1, log, {"release": 0, "bench": 0})
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


def c_shared_override(ctx, log):
    override(ctx, log, "shared_override", os.path.join(ctx.root, "shared-fx", "shared", SC), {"release": 1, "bench": 0})


def c_other_override(ctx, log):
    override(ctx, log, "other_override", os.path.join(ctx.root, "elsewhere", SC), {"release": 1, "bench": 1})


CASES = [("clean_release", c_clean_release), ("version_edited", c_version_edited),
         ("managed_unlisted", c_managed_unlisted), ("outside_header", c_outside_header),
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
