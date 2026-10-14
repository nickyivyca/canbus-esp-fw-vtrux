#!/usr/bin/env python3
"""Spec 12.4 "the name enters the build once" (12.3 item 3; added 2026-10-13,
user), the host (build) half, on real builds.

    python name_once_check.py --commit <sha> --components <a tree's managed_components> --out run.json

Run from a shell where ESP-IDF is exported (IDF_PATH set), with its Python.

THE ROW: on one commit, the hash the device reports in 0x7F3 is the same as
before the change; the image name, the descriptor version and the reported
hash agree; a README.md-only edit recompiles none of the product's own source
files (main/, components/); a name longer than the descriptor's version field
fails the build. Where (user, 2026-10-13): host (build); the reported hash on
the bench -- diag_build_page_check.py reads 0x7F3 off the wire. This script
records fnv1a32 of the descriptor version and of the image name's describe part
as the host cross-check for that bench read.

    clean           `idf.py build`             -> one image; its name, descriptor
                                                  version and project_name name
                                                  HEAD (stale_name_check.judge)
    readme-edit     README.md edited, build    -> an image named <HEAD>-dirty, and
                                                  NO main/ or components/ object
                                                  recompiled
    readme-revert   README.md reverted, build  -> an image named <HEAD>, and none
                                                  recompiled
    overlong        HEAD tagged with a 40-char  -> the build fails and writes no
                    name, build                   image (any guard; which one
                                                  fired is recorded)
    module          a minimal ESP-IDF project   -> the build fails, writes no
                    that includes the commit's    image, and the error names the
                    cmake/git_tree_name.cmake       version field
                    and has no name-length guard
                    of its own, tagged the same

A RECOMPILE is an object file of compile_commands.json whose mtime changed
across the build; it is the product's own when its source is under main/ or
components/ of the tree. Other recompiles (ESP-IDF's descriptor file, its
generated ELF source) are recorded, not judged. A step whose build writes no
image fails: the naming must have re-run for the step to show anything.

THE TWO LONG-NAME STEPS (reviewer, 2026-10-13). In this product a name over 31
characters also makes wican-fw_obd_<name>_man too long for its project-name
guard, so the product step passes on any tree with that guard and cannot show
the version-field guard on its own. The module step can: its fixture has no
project-name guard, so on a tree whose module does not refuse the name the
fixture builds (ESP-IDF cuts the version short), and that is the failing half.

It runs in a throwaway local CLONE of the repo (so the tag stays in the clone,
never in the repo's refs), checked out detached at the commit, with
managed_components/ copied in; the fixture is a fresh git repository beside it.
Both are removed afterwards. Nothing is committed to the repo.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stale_name_check as SN                                      # noqa: E402

README = "README.md"
VERSION_FIELD = 31                     # esp_app_desc.version is char[32]
OVERLONG = "gi-name-once-overlong-0123456789abcdef"     # 38 characters, a valid tag
FIXTURE_IMAGE = "gtn_fixture.bin"
FIXTURE_FILES = {
    "CMakeLists.txt": ("cmake_minimum_required(VERSION 3.16)\n"
                       "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
                       "include(${CMAKE_CURRENT_LIST_DIR}/cmake/git_tree_name.cmake)\n"
                       "git_tree_name(FIXTURE_NAME)\n"
                       "project(gtn_fixture)\n"),
    "main/CMakeLists.txt": 'idf_component_register(SRCS "main.c")\n',
    "main/main.c": "void app_main(void) {}\n",
}


def fnv1a32(s):
    """FNV-1a, 32 bit, over the string's bytes: the 0x7F3 diag_git_hash formula
    (wican_diag_schema.md)."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def under(path, roots):
    p = os.path.normcase(os.path.normpath(path))
    return any(p.startswith(os.path.normcase(os.path.normpath(r)) + os.sep) for r in roots)


def objects(build_dir):
    """{object path: source path} from compile_commands.json, or {} if absent."""
    p = os.path.join(build_dir, "compile_commands.json")
    if not os.path.isfile(p):
        return {}
    out = {}
    for e in json.load(open(p, encoding="utf-8")):
        d = e.get("directory", build_dir)
        o = e.get("output")
        if o:
            out[os.path.normpath(os.path.join(d, o))] = os.path.normpath(os.path.join(d, e["file"]))
    return out


def mtimes(paths):
    out = {}
    for p in paths:
        try:
            out[p] = os.stat(p).st_mtime_ns
        except OSError:
            out[p] = None
    return out


def recompiled(before, after):
    """Objects whose mtime changed, or that appeared, across a build."""
    return sorted(p for p in after if after[p] is not None and after[p] != before.get(p))


def product_recompiled(objs, changed, tree):
    roots = [os.path.join(tree, "main"), os.path.join(tree, "components")]
    return sorted(o for o in changed if under(objs.get(o, ""), roots))


def judge_readme(rc, images, desc, head, dirty, product, n_objects):
    """(ok, detail) for a README-only step: the image names the tree, and no
    product object recompiled. An empty object list means nothing was measured."""
    if n_objects == 0:
        return False, "no objects in compile_commands.json, so recompiles were not measured"
    ok, detail = SN.judge(rc, images, desc, head, dirty)
    if not ok:
        return False, detail
    if product:
        return False, "%d product object(s) recompiled: %s" % (
            len(product), ", ".join(os.path.basename(p) for p in product[:6]))
    return True, "%s; no main/ or components/ object recompiled" % detail


def judge_overlong(rc, images):
    """(ok, detail) for the product's long-name step: fails, no image."""
    if images:
        return False, "an image was written: %s" % ", ".join(os.path.basename(p) for p in images)
    if rc == 0:
        return False, "the build succeeded"
    return True, "the build failed (rc %d) and wrote no image" % rc


def error_block(log):
    """The text of the first CMake error, through the next blank line, or ''."""
    i = log.find("CMake Error")
    if i < 0:
        return ""
    j = log.find("\n\n\n", i)
    return log[i:j if j > 0 else i + 2000][:2000]


def judge_module(rc, images, log):
    """(ok, detail) for the fixture: fails, no image, and the error names the
    version field (reviewer, 2026-10-13)."""
    if images:
        return False, "the fixture built an image: %s" % ", ".join(os.path.basename(p) for p in images)
    if rc == 0:
        return False, "the fixture's build succeeded"
    block = error_block(log)
    if not block:
        return False, "the build failed (rc %d) but with no CMake error to read" % rc
    if not re.search(r"version", block, re.I):
        return False, "the build failed, but the error does not name the version field: %r" % block[:200]
    return True, "the build failed (rc %d), and the error names the version field" % rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--components", required=True, help="a managed_components/ directory to copy in")
    ap.add_argument("--out", required=True, help="JSON record; the build log goes beside it (.log)")
    ap.add_argument("--keep", action="store_true", help="keep the clone and the fixture")
    a = ap.parse_args()
    if "IDF_PATH" not in os.environ:
        print("FAIL: IDF_PATH is not set -- run from a shell where ESP-IDF is exported")
        return 2
    import build_output as BO

    repo = SN.git(["rev-parse", "--show-toplevel"], HERE).stdout.strip()
    c0 = SN.git(["rev-parse", a.commit], repo).stdout.strip()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    tree = os.path.join(os.path.dirname(repo), "wican-nameonce-%s-%s" % (c0[:7], stamp))
    fixture = os.path.join(os.path.dirname(repo), "wican-nameonce-fixture-%s-%s" % (c0[:7], stamp))
    log = os.path.splitext(a.out)[0] + ".log"
    r = SN.git(["clone", "-q", "--no-checkout", repo, tree], os.path.dirname(repo))
    if r.returncode != 0 or SN.git(["checkout", "-q", "--detach", c0], tree).returncode != 0:
        print("FAIL: clone or checkout of %s: %s" % (c0[:7], r.stderr.strip()))
        return 2
    shutil.copytree(a.components, os.path.join(tree, "managed_components"))
    original = open(os.path.join(tree, README), "rb").read()
    build_dir = os.path.join(tree, "build")
    record = {"commit": c0, "tree": os.path.basename(tree), "steps": [],
              "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    fails = []

    def note(name, ok, detail, **kw):
        kw.update({"step": name, "ok": ok, "detail": detail})
        record["steps"].append(kw)
        print("  %-4s %-14s %s" % ("ok" if ok else "FAIL", name, detail), flush=True)
        if not ok:
            fails.append(name)

    def readme_step(name, change, dirty):
        objs = objects(build_dir)
        before = mtimes(objs)
        change()
        since = SN.step_start()
        rc, secs = SN.build(tree, "idf", log)
        images = SN.written_images(build_dir, since)
        desc = BO.app_desc(images[0]) if len(images) == 1 else None
        objs = objects(build_dir) or objs
        changed = recompiled(before, mtimes(objs))
        product = product_recompiled(objs, changed, tree)
        ok, detail = judge_readme(rc, images, desc, c0, dirty, product, len(objs))
        note(name, ok, detail, rc=rc, seconds=round(secs, 1),
             images=[os.path.basename(p) for p in images], version=desc[0] if desc else None,
             objects=len(objs), recompiled=[os.path.relpath(p, build_dir).replace(os.sep, "/") for p in changed],
             product_recompiled=[os.path.relpath(p, build_dir).replace(os.sep, "/") for p in product])

    try:
        # clean
        since = SN.step_start()
        rc, secs = SN.build(tree, "idf", log)
        images = SN.written_images(build_dir, since)
        desc = BO.app_desc(images[0]) if len(images) == 1 else None
        ok, detail = SN.judge(rc, images, desc, c0, False)
        name_part = SN.name_version(os.path.basename(images[0])) if len(images) == 1 else None
        objs = objects(build_dir)
        on_lines = sum(1 for e in json.load(open(os.path.join(build_dir, "compile_commands.json")))
                       if desc and desc[0] in e.get("command", "")) if objs else None
        note("clean", ok, detail, rc=rc, seconds=round(secs, 1), images=[os.path.basename(p) for p in images],
             version=desc[0] if desc else None, project_name=desc[1] if desc else None,
             fnv1a32_version="0x%08X" % fnv1a32(desc[0]) if desc else None,
             fnv1a32_image_name="0x%08X" % fnv1a32(name_part) if name_part else None,
             compile_lines_with_name=on_lines, objects=len(objs))
        if not ok:
            print("  the clean build is the precondition; stopping")
            return 1

        def edit():
            with open(os.path.join(tree, README), "ab") as fh:
                fh.write(b"\nname-once probe\n")

        def revert():
            with open(os.path.join(tree, README), "wb") as fh:
                fh.write(original)

        readme_step("readme-edit", edit, True)
        readme_step("readme-revert", revert, False)

        # overlong, product
        SN.git(["tag", OVERLONG, "HEAD"], tree)
        since = SN.step_start()
        rc, secs = SN.build(tree, "idf", log)
        images = SN.written_images(build_dir, since)
        ok, detail = judge_overlong(rc, images)
        tail = open(log, encoding="utf-8", errors="replace").read()
        tail = tail[tail.rfind("===== "):]
        note("overlong", ok, detail, rc=rc, seconds=round(secs, 1), name=OVERLONG, chars=len(OVERLONG),
             images=[os.path.basename(p) for p in images], error=error_block(tail)[:600])

        # module fixture
        for rel, text in FIXTURE_FILES.items():
            p = os.path.join(fixture, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        os.makedirs(os.path.join(fixture, "cmake"), exist_ok=True)
        shutil.copy(os.path.join(tree, "cmake", "git_tree_name.cmake"), os.path.join(fixture, "cmake"))
        ident = ["-c", "user.name=name-once-check", "-c", "user.email=name-once-check@invalid"]
        SN.git(["init", "-q"], fixture)
        SN.git(["add", "-A"], fixture)
        SN.git(ident + ["commit", "-q", "--no-verify", "-m", "fixture"], fixture)
        SN.git(["tag", OVERLONG], fixture)
        since = SN.step_start()
        rc, secs = SN.build(fixture, "idf", log, dict(os.environ, IDF_TARGET="esp32c3"))
        fbuild = os.path.join(fixture, "build")
        images = [p for p in (os.path.join(fbuild, FIXTURE_IMAGE),)
                  if os.path.isfile(p) and os.path.getmtime(p) >= since]
        desc = BO.app_desc(images[0]) if images else None
        tail = open(log, encoding="utf-8", errors="replace").read()
        tail = tail[tail.rfind("===== "):]
        ok, detail = judge_module(rc, images, tail)
        note("module", ok, detail, rc=rc, seconds=round(secs, 1), name=OVERLONG, chars=len(OVERLONG),
             images=[os.path.basename(p) for p in images],
             version=desc[0] if desc else None, error=error_block(tail)[:600])
    finally:
        record["verdict"] = "PASS" if not fails and len(record["steps"]) == 5 else "FAIL"
        record["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not a.keep:
            def writable(fn, p, _exc):          # git's object files are read-only on Windows
                os.chmod(p, 0o700)
                fn(p)
            for d in (tree, fixture):
                if os.path.exists(d):
                    shutil.rmtree(d, onerror=writable)
            record["removed"] = not (os.path.exists(tree) or os.path.exists(fixture))
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1)
    print("%s  (%s)" % (record["verdict"], a.out))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
