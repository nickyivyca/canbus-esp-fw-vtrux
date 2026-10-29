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

The shared components' own lists (R30 as amended at shared/main 1b4ab4d, shared/wican/SPEC.md
W10) are "files_using_pins" in pins.json in each shared component's folder in
managed_components/, paths from that folder; a component without one has an empty list. A
shared component is a dependencies.lock entry from git whose path is shared/<name>, read here
from the lock's text, not by the tooling's lock reader. The component cases change that file
after release_pass's build (restored after).

What each case expects is worked out here, not by the check's reader: the objects that read the
board's include/board_pins.h from `ninja -t deps` in each CMake build folder, each traced to
its source by `ninja -t query` (its first explicit input); a source under the project root,
outside build/ and managed_components/, not on the project's list is a failure, and so is a
source in a shared component's folder not on that component's list, each named by its path from
the project root. The check must exit 1 naming exactly those, or 0 with PASS R30 when there are
none.

    release_pass   the fixture as it is: passes (every source that reads pins is listed; VOID
                   unless one listed source, the project's or a shared component's, reads them)
    unlisted       release_pass's build, the first listed project file that reads pins taken
                   off the list: fails naming it (VOID if there is none)
    listed_absent  the same, the list also naming main/r30_none.c, no such file: cannot read
    listed_twice   the same, the list's first file named twice: cannot read
    no_list        the same, the list's key removed: cannot read
    comp_unlisted  release_pass's build, a shared component's first listed reader taken off its
                   pins.json: fails naming it (VOID unless a shared component's file reads pins)
    comp_no_list   the same, the component's pins.json removed: fails naming each of its readers
    comp_listed_absent  the same, its list also naming r30_none.c, no such file: cannot read
    comp_listed_twice   the same, its first reader named twice: cannot read
    new_reader     a release build of a commit making main/can.c include board_pins.h (not on
                   the list): fails naming main/can.c alone

Before each case it checks the bench lock, the "No builds" line and running simulators, as
release_cases.py does.
"""

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402

TOOLS = RC.TOOLS
KEY = "stock_files_using_pins"
COMP_FILE = "pins.json"
COMP_KEY = "files_using_pins"
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


def shared_folders(lock_text):
    """{managed_components/<ns>__<name>: lock name} for each dependencies.lock entry whose source
    is git with path shared/<name> (one level: shared/boards/<b> is the board), from the text."""
    out, name, src = {}, None, {}

    def close():
        if name and src.get("type") == "git" and re.match(r"^shared/[^/]+$", src.get("path", "").strip("'\"")):
            out["managed_components/" + name.replace("/", "__", 1)] = name
    deps = False
    for line in lock_text.splitlines():
        if not line.startswith(" "):
            close()
            name, src, deps = None, {}, line.rstrip() == "dependencies:"
            continue
        m = re.match(r"^  ([^ :][^:]*):\s*$", line)
        if deps and m:
            close()
            name, src = m.group(1).strip("'\""), {}
            continue
        m = re.match(r"^      (type|path): *(.*?)\s*$", line)
        if name and m:
            src[m.group(1)] = m.group(2)
    close()
    return out


def comp_lists(project):
    """{managed_components/<folder>: [paths from that folder]} for each shared component, read
    from its pins.json as it is (no pins.json: [])."""
    lock = open(os.path.join(project, "dependencies.lock"), encoding="utf-8").read()
    out = {}
    for d in shared_folders(lock):
        p = os.path.join(project, *d.split("/"), COMP_FILE)
        out[d] = json.load(open(p, encoding="utf-8")).get(COMP_KEY) or [] if os.path.exists(p) else []
    return out


def expected(project, sources, listed, comps=None):
    """sources: absolute paths of the sources whose objects read the header; listed: the
    project's list; comps: {managed_components/<folder>: its list}. The failures: each source from
    the project root (outside build/ and managed_components/) not on the project's list, and each
    source in a shared component's folder not on its list, as its path from the project root."""
    root = key(os.path.normpath(project)).rstrip("/") + "/"
    lists = {"": {key(x) for x in listed}}
    for d, lst in (comps or {}).items():
        lists[key(d) + "/"] = {key(d + "/" + x) for x in lst}
    bad = set()
    for s in sources:
        k = key(os.path.normpath(s))
        if not k.startswith(root):
            continue
        rel = k[len(root):]
        owner = next((d for d in lists if d and rel.startswith(d)), "")
        if rel.startswith("build/") or not owner and rel.startswith("managed_components/"):
            continue
        if rel not in lists[owner]:
            bad.add(rel)
    return sorted(bad)


def readers_of(project, sources, folder):
    """The sources in folder (managed_components/<x>), as paths from that folder, sorted."""
    pre = key(os.path.normpath(os.path.join(project, *folder.split("/")))).rstrip("/") + "/"
    return sorted(key(os.path.normpath(s))[len(pre):] for s in sources if key(os.path.normpath(s)).startswith(pre))


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


LOCK_TEXT = """dependencies:
  espressif/mdns:
    component_hash: 0
    source:
      registry_url: https://components.espressif.com/
      type: service
    version: 1.8.2
  idf:
    source:
      type: idf
    version: 5.4.1
  vtrux/wican:
    component_hash: null
    dependencies: []
    source:
      git: https://example.invalid/r.git
      path: shared/wican
      type: git
    version: 1111111111111111111111111111111111111111
  vtrux/wican_v3:
    source:
      git: https://example.invalid/r.git
      path: shared/boards/wican_v3
      type: git
    version: 2222222222222222222222222222222222222222
  vtrux/local_one:
    source:
      path: shared/local_one
      type: local
    version: 1.0.0
  vtrux/deeper:
    source:
      git: https://example.invalid/r.git
      path: shared/a/b
      type: git
    version: 3333333333333333333333333333333333333333
direct_dependencies:
- vtrux/wican
manifest_hash: 1
target: esp32c3
version: 2.0.0
"""


def self_test_comp_cases():
    """The component cases on a fake project, the check and the two judges stubbed: the list each
    one leaves for the check, what it expects (failures or the unreadable mark), and the file put
    back as it was, or left absent."""
    global check, judge, judge_unreadable
    ok, real = True, (check, judge, judge_unreadable, RC.report)
    d = tempfile.mkdtemp(prefix="r30st")
    comp = os.path.join(d, "managed_components", "vtrux__wican")
    lp = os.path.join(comp, COMP_FILE)
    seen = {}

    def fake_check(proj, log):
        seen["list"] = json.load(open(lp)).get(COMP_KEY) if os.path.exists(lp) else None
        return 1, "x"
    W = "managed_components/vtrux__wican/"
    try:
        os.makedirs(comp)
        for f, text in (("dependencies.lock", LOCK_TEXT), ("vtrux_checks.json", json.dumps({KEY: []})),
                        (W + "wican.c", ""), (W + "other.c", "")):
            with open(os.path.join(d, *f.split("/")), "w", newline="\n") as fh:
                fh.write(text)
        ctx = type("Ctx", (), {})()
        ctx.p1, ctx.log, ctx.pass_built = d, os.path.join(d, "log"), True
        ctx.sources = [os.path.join(comp, "wican.c"), os.path.join(comp, "other.c")]
        check, RC.report = fake_check, lambda name, verdict, detail, log: seen.__setitem__("report", verdict)
        judge = lambda rc, out, want: (seen.__setitem__("want", want), ("PASS", ""))[1]
        judge_unreadable = lambda rc, out, mark: (seen.__setitem__("mark", mark), ("PASS", ""))[1]
        log = os.path.join(d, "case.log")
        for start, want in (('{"files_using_pins": ["wican.c"]}', ["wican.c"]), (None, [])):
            if start:
                with open(lp, "w", newline="\n") as fh:
                    fh.write(start)
            elif os.path.exists(lp):
                os.remove(lp)
            got = comp_lists(d)
            good = got == {W.rstrip("/"): want}
            print("  %s comp_lists, pins.json %s: %s" % ("ok  " if good else "FAIL", "present" if start else "absent", got))
            ok &= good
        both = [W + "other.c", W + "wican.c"]
        present = '{"files_using_pins": ["wican.c", "other.c"]}'
        for start, name, fn, want_list, want in (
                (present, "comp_unlisted", c_comp_unlisted, ["other.c"], ("want", [W + "wican.c"])),
                (present, "comp_no_list", c_comp_no_list, None, ("want", both)),
                (present, "comp_listed_absent", c_comp_listed_absent, ["wican.c", "other.c", "r30_none.c"],
                 ("mark", "is not a file in the component")),
                (present, "comp_listed_twice", c_comp_listed_twice, ["wican.c", "other.c", "other.c", "other.c"],
                 ("mark", "is named twice")),
                (None, "comp_no_list", c_comp_no_list, None, ("want", both)),
                (None, "comp_listed_absent", c_comp_listed_absent, ["r30_none.c"], ("mark", "is not a file in the component")),
                (None, "comp_listed_twice", c_comp_listed_twice, ["other.c", "other.c"], ("mark", "is named twice"))):
            if start is None:
                if os.path.exists(lp):
                    os.remove(lp)
            else:
                with open(lp, "w", newline="\n") as fh:
                    fh.write(start)
            seen.clear()
            fn(ctx, log)
            after = open(lp).read() if os.path.exists(lp) else None
            good = seen.get("list", "unset") == want_list and seen.get(want[0]) == want[1] and after == start \
                and seen.get("report") == "PASS"
            print("  %s %s, pins.json %s: list %s, %s %s, put back %s" % (
                "ok  " if good else "FAIL", name, "present" if start else "absent", seen.get("list", "unset"), want[0],
                seen.get(want[0]), after == start))
            ok &= good
        ctx.sources = [os.path.join(d, "main", "can.c")]
        seen.clear()
        c_comp_unlisted(ctx, log)
        v = seen.get("report") == "VOID" and "list" not in seen
        print("  %s the component cases VOID when no component file reads pins: %s" % ("ok  " if v else "FAIL", seen))
        ok &= v
        # The project's list cases: the first listed file that reads pins, not a fixed name.
        vp = os.path.join(d, "vtrux_checks.json")
        check = lambda proj, log: (seen.__setitem__("list", json.load(open(vp)).get(KEY)), (1, "x"))[1]
        os.makedirs(os.path.join(d, "main"))
        for f in ("main/a.c", "main/b.c"):
            open(os.path.join(d, *f.split("/")), "w").close()
        start = json.dumps({KEY: ["main/a.c", "main/b.c"]})
        for name, fn, srcs, want_list, want in (
                ("unlisted", c_unlisted, ["main/b.c"], ["main/a.c"], ("want", ["main/b.c"])),
                ("unlisted", c_unlisted, ["main/a.c", "main/b.c"], ["main/b.c"], ("want", ["main/a.c"])),
                ("listed_twice", c_listed_twice, ["main/b.c"], ["main/a.c", "main/b.c", "main/a.c"],
                 ("mark", "is named twice")),
                ("unlisted", c_unlisted, ["main/c.c"], "unset", ("report", "VOID"))):
            with open(vp, "w", newline="\n") as fh:
                fh.write(start)
            ctx.sources = [os.path.join(d, *x.split("/")) for x in srcs]
            seen.clear()
            fn(ctx, log)
            good = seen.get("list", "unset") == want_list and seen.get(want[0]) == want[1] \
                and open(vp).read() == start and seen.get("report") == ("VOID" if want[0] == "report" else "PASS")
            print("  %s %s, readers %s: list %s, %s %s" % ("ok  " if good else "FAIL", name, srcs, seen.get("list", "unset"),
                                                          want[0], seen.get(want[0])))
            ok &= good
    finally:
        check, judge, judge_unreadable, RC.report = real
        shutil.rmtree(d, ignore_errors=True)
    return ok


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
            ("a listed file that reads none", expected("C:/p", [], ["main/main.c"]), []),
            ("a component's reader on its list", expected("C:/p", ["C:/p/managed_components/vtrux__wican/wican.c"], [],
                                                          {"managed_components/vtrux__wican": ["wican.c"]}), []),
            ("a component's reader off its list",
             expected("C:/p", ["C:/p/managed_components/vtrux__wican/wican.c", "C:/p/main/main.c"], ["main/main.c"],
                      {"managed_components/vtrux__wican": []}), ["managed_components/vtrux__wican/wican.c"]),
            ("the project's list does not cover a component's file",
             expected("C:/p", ["C:/p/managed_components/vtrux__wican/wican.c"], ["managed_components/vtrux__wican/wican.c"],
                      {"managed_components/vtrux__wican": []}), ["managed_components/vtrux__wican/wican.c"]),
            ("a component's list does not cover a project file",
             expected("C:/p", ["C:/p/main/wican.c"], [], {"managed_components/vtrux__wican": ["wican.c"]}), ["main/wican.c"]),
            ("a component not shared is not judged",
             expected("C:/p", ["C:/p/managed_components/espressif__mdns/mdns.c"], [],
                      {"managed_components/vtrux__wican": []}), []),
            ("a folder whose name starts like a component's",
             expected("C:/p", ["C:/p/managed_components/vtrux__wican_v3/board.c"], [],
                      {"managed_components/vtrux__wican": []}), []),
            ("readers_of", readers_of("C:/p", ["C:/p/managed_components/vtrux__wican/wican.c", "C:/p/main/main.c",
                                               "C:/p/managed_components/vtrux__wican_v3/board.c"],
                                      "managed_components/vtrux__wican"), ["wican.c"]),
            ("shared_folders", shared_folders(LOCK_TEXT), {"managed_components/vtrux__wican": "vtrux/wican"})):
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
    ok &= self_test_comp_cases()
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


def with_comp_list(ctx, log, name, change, mark=None):
    """release_pass's build, a shared component's pins.json changed by change(data, readers) (in
    place; returning False removes the file), restored after. The component is the first whose
    files read pins. mark: the check cannot read the list and says so; else judged against
    expected()."""
    if not getattr(ctx, "pass_built", False):
        RC.report(name, "VOID", "needs release_pass in the same batch", ctx.log)
        return
    comps = comp_lists(ctx.p1)
    folder = next((d for d in sorted(comps) if readers_of(ctx.p1, ctx.sources, d)), None)
    if folder is None:
        RC.report(name, "VOID", "no shared component's file reads the board's pins (components %s)" % sorted(comps),
                  ctx.log)
        return
    readers = readers_of(ctx.p1, ctx.sources, folder)
    p = os.path.join(ctx.p1, *folder.split("/"), COMP_FILE)
    raw = open(p, "rb").read() if os.path.exists(p) else None
    try:
        data = json.loads(raw) if raw is not None else {COMP_KEY: []}
        if change(data, readers) is False:
            if raw is not None:
                os.remove(p)
            data = {COMP_KEY: []}
        else:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=1)
        with open(log, "a") as f:
            f.write("%s: %s readers %s, its list %s\n" % (name, folder, readers,
                                                         "removed" if not os.path.exists(p) else json.dumps(data.get(COMP_KEY))))
        rc, out = check(ctx.p1, log)
        if mark:
            RC.report(name, *judge_unreadable(rc, out, mark), ctx.log)
        else:
            comps[folder] = data.get(COMP_KEY) or []
            want = expected(ctx.p1, ctx.sources, listed(ctx.p1) or [], comps)
            if not want:
                RC.report(name, "VOID", "the changed list leaves no failure to show", ctx.log)
            else:
                RC.report(name, *judge(rc, out, want), ctx.log)
    finally:
        if raw is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            with open(p, "wb") as f:
                f.write(raw)


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
            want = expected(ctx.p1, ctx.sources, data.get(KEY) or [], comp_lists(ctx.p1))
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
    comps = comp_lists(ctx.p1)
    want = expected(ctx.p1, srcs, listed(ctx.p1) or [], comps)
    on = [x for x in listed(ctx.p1) or []] + [d + "/" + x for d, lst in comps.items() for x in lst]
    root = key(os.path.normpath(ctx.p1)).rstrip("/") + "/"
    if not any(key(os.path.normpath(s)) == root + key(x) for s in srcs for x in on):
        RC.report("release_pass", "VOID", "no listed file reads the board's pins here: %s" % srcs, ctx.log)
        return
    with open(log, "a") as f:
        f.write("release_pass: shared components' lists %s\n" % json.dumps(comps))
    ctx.sources, ctx.pass_built = srcs, True
    rc, out = check(ctx.p1, log)
    RC.report("release_pass", *judge(rc, out, want), ctx.log)


def c_unlisted(ctx, log):
    root = key(os.path.normpath(ctx.p1)).rstrip("/") + "/"
    reading = {key(os.path.normpath(s)) for s in getattr(ctx, "sources", [])}
    first = next((x for x in listed(ctx.p1) or [] if root + key(x) in reading), None)
    if first is None:
        RC.report("unlisted", "VOID", "no file on the project's list reads the board's pins", ctx.log)
        return
    with_list(ctx, log, "unlisted", lambda d: d.__setitem__(KEY, [x for x in d[KEY] if x != first]))


def c_listed_absent(ctx, log):
    with_list(ctx, log, "listed_absent", lambda d: d[KEY].append("main/r30_none.c"), "is not a file in the project")


def c_listed_twice(ctx, log):
    with_list(ctx, log, "listed_twice", lambda d: d[KEY].append(d[KEY][0]), "is named twice")


def c_no_list(ctx, log):
    with_list(ctx, log, "no_list", lambda d: d.pop(KEY), "has no %r list" % KEY)


def c_comp_unlisted(ctx, log):
    def change(d, readers):
        lst = d.setdefault(COMP_KEY, [])
        first = next((x for x in lst if key(x) in readers), None)
        d[COMP_KEY] = [x for x in lst if x != first]
    with_comp_list(ctx, log, "comp_unlisted", change)


def c_comp_no_list(ctx, log):
    with_comp_list(ctx, log, "comp_no_list", lambda d, readers: False)


def c_comp_listed_absent(ctx, log):
    with_comp_list(ctx, log, "comp_listed_absent", lambda d, readers: d.setdefault(COMP_KEY, []).append("r30_none.c"),
                   "is not a file in the component")


def c_comp_listed_twice(ctx, log):
    with_comp_list(ctx, log, "comp_listed_twice", lambda d, readers: d.setdefault(COMP_KEY, []).extend([readers[0]] * 2),
                   "is named twice")


def c_new_reader(ctx, log):
    srcs = release_built(ctx, "new_reader", log, (PROBE_FILE, PROBE_LINE))
    if srcs is None:
        return
    want = expected(ctx.p1, srcs, listed(ctx.p1) or [], comp_lists(ctx.p1))
    if want != [PROBE_FILE]:
        RC.report("new_reader", "VOID", "the build does not show %s alone reading pins unlisted: %s" % (PROBE_FILE, want),
                  ctx.log)
    else:
        rc, out = check(ctx.p1, log)
        RC.report("new_reader", *judge(rc, out, want), ctx.log)
    RC.reset(ctx.p1, "fixture")
    RC.rmtree(os.path.join(ctx.p1, "build"))


CASES = [("release_pass", c_release_pass), ("unlisted", c_unlisted), ("listed_absent", c_listed_absent),
         ("listed_twice", c_listed_twice), ("no_list", c_no_list), ("comp_unlisted", c_comp_unlisted),
         ("comp_no_list", c_comp_no_list), ("comp_listed_absent", c_comp_listed_absent),
         ("comp_listed_twice", c_comp_listed_twice), ("new_reader", c_new_reader)]


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
