#!/usr/bin/env python3
"""R30 pins check (vtrux_checks pins) on real release builds, shown passing and failing.

    python -B pins_cases.py --root <short folder in scratch/> --out <run folder> [--cases a,b] [--jobs N]
    python -B pins_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture (a
clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses; --root must
hold a fixture whose project takes the board from git (port step 1a C on), which this script
does not make. The check reads the project's list, "stock_files_using_pins" in vtrux_checks.json
in the project root, when it runs, so the list cases change that file after release_pass's
build (restored after) instead of building again.

What each case expects is worked out here, not by the check's reader: the objects that read the
board's include/board_pins.h from `ninja -t deps` in each CMake build folder, each traced to
its source by `ninja -t query` (its first explicit input); a source under the project root,
outside build/ and managed_components/, not on the list is a failure, named by its path from the
project root. The check must exit 1 naming exactly those, or 0 with PASS R30 when there are none.

    release_pass   the fixture as it is: passes (every project source that reads pins is listed)
    unlisted       release_pass's build, main/main.c taken off the list: fails naming it
    listed_absent  the same, the list also naming main/r30_none.c, no such file: cannot read
    listed_twice   the same, main/main.c named twice: cannot read
    no_list        the same, the list's key removed: cannot read
    new_reader     a release build of a commit making main/can.c include board_pins.h (not on
                   the list): fails naming main/can.c, and main/main.c not

Before each case it checks the bench lock, the "No builds" line and running simulators, as
release_cases.py does.
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
KEY = "stock_files_using_pins"
HEADER = "managed_components/vtrux__wican_v3/include/board_pins.h"
_FAILED = re.compile(r"^    (\S+): (\S+) read board_pins\.h and is not on ")
PROBE_FILE = "main/can.c"
PROBE_LINE = '#include "board_pins.h"  /* R30 case new_reader: a project file reading the board\'s pins */'


# ---------- the tester's own readings (pure; --self-test runs them on fakes) ----------

def key(p):
    p = p.replace("\\", "/")
    return p.lower() if os.name == "nt" else p


def deps_readers(text, header):
    """The objects in `ninja -t deps` text whose dependencies include header (a path compared
    after key(); a dependency that is relative is taken against base, the folder's own path)."""
    out, obj = [], None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            obj = line.split(": #deps", 1)[0] if ": #deps" in line else None
        elif obj and line.strip() and key(line.strip()).endswith(key(header)):
            out.append(obj)
            obj = None
    return out


def query_input(text):
    """The first explicit input in `ninja -t query <target>` text, or None."""
    inputs = False
    for line in text.splitlines():
        s = line.strip()
        if line.startswith("  input:"):
            inputs = True
            continue
        if line.startswith("  outputs:"):
            inputs = False
        if inputs and line.startswith("    ") and s and not s.startswith(("|", "||")):
            return s
    return None


def expected(project, sources, listed):
    """sources: absolute paths of the sources whose objects read the header; listed: the list.
    The failures: each source from the project root (outside build/ and managed_components/)
    not on the list, as its path from the project root."""
    root = key(os.path.normpath(project)).rstrip("/") + "/"
    on = {key(x) for x in listed}
    bad = set()
    for s in sources:
        k = key(os.path.normpath(s))
        if not k.startswith(root):
            continue
        rel = k[len(root):]
        if rel.startswith(("build/", "managed_components/")):
            continue
        if rel not in on:
            bad.add(rel)
    return sorted(bad)


def judge(rc, out, want):
    """(verdict, detail). want: expected()'s failures."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    if "cannot read" in out:
        return "FAIL", "exit %d, the check could not read the build" % rc
    named = sorted(key(m.group(2)) for m in map(_FAILED.match, out.splitlines()) if m)
    if want:
        if rc != 1 or not re.search(r"^FAIL R30\b", out, re.M):
            return "FAIL", "exit %d, wanted 1 with FAIL R30 naming %s" % (rc, want)
        if named != [key(w) for w in want]:
            return "FAIL", "the check names %s, wanted %s" % (named, want)
        return "PASS", "exit 1, names %s" % ", ".join(want)
    if rc != 0 or not re.search(r"^PASS R30$", out, re.M) or named:
        return "FAIL", "exit %d, wanted 0 with PASS R30 (named %s)" % (rc, named)
    return "PASS", "exit 0, PASS R30"


def judge_unreadable(rc, out, mark):
    if rc != 1 or "FAIL R30: cannot read" not in out:
        return "FAIL", "exit %d; wanted 1 with 'cannot read'" % rc
    if mark not in out:
        return "FAIL", "exit 1, but the output does not say %r" % mark
    return "PASS", "exit 1, cannot read: %s" % mark


def self_test():
    ok = True
    deps = ("esp-idf/main/CMakeFiles/__idf_main.dir/main.c.obj: #deps 3, deps mtime 1 (VALID)\n"
            "    C:/p/main/main.c\n    C:/p/managed_components/vtrux__wican_v3/include/board_pins.h\n    x.h\n\n"
            "esp-idf/main/CMakeFiles/__idf_main.dir/can.c.obj: #deps 2, deps mtime 1 (VALID)\n"
            "    C:/p/main/can.c\n    C:/p/managed_components/vtrux__wican_v3/include/board_pins_other.h\n\n"
            "b.obj: #deps 1, deps mtime 1 (VALID)\n    ../managed_components/vtrux__wican_v3/include/board_pins.h\n")
    query = ("esp-idf/main/CMakeFiles/__idf_main.dir/main.c.obj:\n  input: C_COMPILER____idf_main_\n"
             "    C:/p/main/main.c\n    | C:/p/x.h\n  outputs:\n    esp-idf/main/libmain.a\n")
    for label, got, want in (
            ("deps readers", deps_readers(deps, HEADER), ["esp-idf/main/CMakeFiles/__idf_main.dir/main.c.obj", "b.obj"]),
            ("query input", query_input(query), "C:/p/main/main.c"),
            ("query input, implicit only", query_input("t:\n  input: r\n    | a.h\n  outputs:\n    o\n"), None),
            ("query input, an output is not an input", query_input("t:\n  input: r\n  outputs:\n    o\n"), None),
            ("all listed", expected("C:/p", ["C:/p/main/main.c"], ["main/main.c"]), []),
            ("one unlisted", expected("C:/p", ["C:/p/main/main.c", "C:/p/main/can.c"], ["main/main.c"]), ["main/can.c"]),
            ("the board's own and the build's not the project's",
             expected("C:/p", ["C:/p/managed_components/vtrux__wican_v3/board.c", "C:/p/build/x.c", "C:/q/y.c"], []), []),
            ("a listed file that reads none", expected("C:/p", [], ["main/main.c"]), [])):
        print("  %s %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    fail = ("R30 pins: ...\nFAIL R30: 1 failure(s)\n"
            "    build: main/can.c read board_pins.h and is not on stock_files_using_pins\n")
    for label, args, want in (("passes as wanted", (0, "R30 pins: ...\nPASS R30\n", []), "PASS"),
                              ("fails as wanted", (1, fail, ["main/can.c"]), "PASS"),
                              ("passes when it should fail", (0, "PASS R30\n", ["main/can.c"]), "FAIL"),
                              ("fails when it should pass", (1, fail, []), "FAIL"),
                              ("names another file", (1, fail.replace("can.c", "main.c"), ["main/can.c"]), "FAIL"),
                              ("names one too few", (1, fail, ["main/can.c", "main/x.c"]), "FAIL"),
                              ("exit 0 without PASS", (0, "R30 pins: ...\n", []), "FAIL"),
                              ("cannot read", (1, "FAIL R30: cannot read the build: x\n", ["main/can.c"]), "FAIL"),
                              ("crashes", (2, "Traceback", []), "ERROR")):
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    for label, args, want in (("unreadable, said", (1, "FAIL R30: cannot read ...: x is named twice", "named twice"), "PASS"),
                              ("unreadable, not said", (1, "FAIL R30: cannot read ...: x", "named twice"), "FAIL"),
                              ("passes instead", (0, "PASS R30", "named twice"), "FAIL")):
        v = judge_unreadable(*args)[0]
        print("  %s judge_unreadable(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def check(proj, log):
    e = RC.idf_env(PYTHONPATH=TOOLS)
    return RC.run([sys.executable, "-B", "-m", "vtrux_checks", "pins", "--build", os.path.join(proj, "build")],
                  cwd=proj, env=e, log=log)


def reader_sources(proj, log):
    """Absolute paths of the sources whose objects read the board's header, in build/ and each
    CMake build folder below it, by ninja's own -t deps and -t query."""
    header = os.path.join(proj, *HEADER.split("/"))
    out = []
    b = os.path.join(proj, "build")
    for d, _, files in os.walk(b):
        if not (d == b or "CMakeCache.txt" in files) or not os.path.exists(os.path.join(d, "build.ninja")):
            continue
        _, text = RC.run(["ninja", "-C", d, "-t", "deps"], env=RC.idf_env())
        for obj in deps_readers(text, HEADER):
            _, q = RC.run(["ninja", "-C", d, "-t", "query", obj], env=RC.idf_env())
            src = query_input(q)
            with open(log, "a") as f:
                f.write("%s: %s read %s; source %s\n" % (os.path.relpath(d, proj), obj, HEADER, src))
            if src:
                out.append(src if os.path.isabs(src) else os.path.normpath(os.path.join(d, src)))
    if not os.path.exists(header):
        raise RuntimeError("no %s in the build's project" % HEADER)
    return out


def listed(proj):
    return json.load(open(os.path.join(proj, "vtrux_checks.json"), encoding="utf-8")).get(KEY)


def with_list(ctx, log, name, change, mark=None):
    """release_pass's build, the list changed by change(data) (in place), restored after. mark:
    the check cannot read the list and says so; else judged against expected()."""
    if not getattr(ctx, "pass_built", False):
        RC.report(name, "VOID", "needs release_pass in the same batch", ctx.log)
        return
    p = os.path.join(ctx.p1, "vtrux_checks.json")
    raw = open(p, "rb").read()
    try:
        data = json.loads(raw)
        change(data)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
        with open(log, "a") as f:
            f.write("%s: the list %s\n" % (name, json.dumps(data.get(KEY))))
        rc, out = check(ctx.p1, log)
        if mark:
            RC.report(name, *judge_unreadable(rc, out, mark), ctx.log)
        else:
            want = expected(ctx.p1, ctx.sources, data.get(KEY) or [])
            if not want:
                RC.report(name, "VOID", "the changed list leaves no failure to show", ctx.log)
            else:
                RC.report(name, *judge(rc, out, want), ctx.log)
    finally:
        with open(p, "wb") as f:
            f.write(raw)


def release_built(ctx, name, log, append=None):
    """A release build of the fixture, with append = (file, line) committed on top first."""
    RC.reset(ctx.p1, "fixture")
    if append:
        RC.git(ctx.p1, "checkout", "-q", "-B", "r30-" + name, "origin/fixture")
        p = os.path.join(ctx.p1, append[0])
        text = open(p, newline="").read()
        with open(p, "w", newline="") as f:
            f.write(text + ("" if text.endswith("\n") else "\n") + append[1] + "\n")
        RC.git(ctx.p1, "commit", "-q", "-am", "pins case %s" % name)
        with open(log, "a") as fx:
            RC.normalise(ctx, ctx.p1, name, fx)
            fx.write("%s: %s\n" % (name, RC.git(ctx.p1, "rev-parse", "HEAD")))
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return None
    return reader_sources(ctx.p1, log)


# ---------- cases ----------

def c_release_pass(ctx, log):
    srcs = release_built(ctx, "release_pass", log)
    if srcs is None:
        return
    want = expected(ctx.p1, srcs, listed(ctx.p1) or [])
    if not any(key(os.path.normpath(s)).endswith("/main/main.c") for s in srcs):
        RC.report("release_pass", "VOID", "main/main.c does not read the board's pins here: %s" % srcs, ctx.log)
        return
    ctx.sources, ctx.pass_built = srcs, True
    rc, out = check(ctx.p1, log)
    RC.report("release_pass", *judge(rc, out, want), ctx.log)


def c_unlisted(ctx, log):
    with_list(ctx, log, "unlisted", lambda d: d.__setitem__(KEY, [x for x in d[KEY] if x != "main/main.c"]))


def c_listed_absent(ctx, log):
    with_list(ctx, log, "listed_absent", lambda d: d[KEY].append("main/r30_none.c"), "is not a file in the project")


def c_listed_twice(ctx, log):
    with_list(ctx, log, "listed_twice", lambda d: d[KEY].append("main/main.c"), "is named twice")


def c_no_list(ctx, log):
    with_list(ctx, log, "no_list", lambda d: d.pop(KEY), "has no %r list" % KEY)


def c_new_reader(ctx, log):
    srcs = release_built(ctx, "new_reader", log, (PROBE_FILE, PROBE_LINE))
    if srcs is None:
        return
    want = expected(ctx.p1, srcs, listed(ctx.p1) or [])
    if want != [PROBE_FILE]:
        RC.report("new_reader", "VOID", "the build does not show %s alone reading pins unlisted: %s" % (PROBE_FILE, want),
                  ctx.log)
    else:
        rc, out = check(ctx.p1, log)
        RC.report("new_reader", *judge(rc, out, want), ctx.log)
    RC.reset(ctx.p1, "fixture")
    RC.rmtree(os.path.join(ctx.p1, "build"))


CASES = [("release_pass", c_release_pass), ("unlisted", c_unlisted), ("listed_absent", c_listed_absent),
         ("listed_twice", c_listed_twice), ("no_list", c_no_list), ("new_reader", c_new_reader)]


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
    ctx.log = os.path.join(ctx.out, "pins_driver.log")
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
    RC.make_pin(ctx)    # the fixture's tag must mark the tooling under test
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "pins_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    RC.reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.out, "pins_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
