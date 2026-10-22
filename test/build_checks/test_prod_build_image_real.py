#!/usr/bin/env python3
"""Tests which image tools/prod_build.py judges and records, with a REAL build.

Spec 12.3 item 3: a prod build "records the commit, the image's md5 and its
size beside the image". The image is the one the build named: `app_bin` in
`build/project_description.json` (reviewer, 2026-10-21), not whichever .bin in
build/ is newest, and a missing name or file is a refusal.

This runs the clone's own tools/prod_build.py as committed, main() with no
arguments, in a clone with `gen-inhibit/main` checked out and clean. Every
check and the real `idf.py reconfigure` and `idf.py build` run. Two things are
added and nothing is replaced:

  - a hook on subprocess.run: when an `idf.py` command that builds returns 0,
    the case's change is made to build/, so it is in place when the script
    goes on to judge the image. A reconfigure rewrites
    project_description.json, so a change made before the run would be undone.
  - a spy on the script's image judge (check_image_symbols): the real judge
    runs, and the spy notes which image it was given.

Cases, in this order, plain last so the clone keeps its record:

  plain     no change: exit 0, the judged image and the one record are
            app_bin's, and the record's md5, size and commit match that file
            and HEAD.
  stray     a .bin of another name matching wican-fw_obd_*.bin, different
            bytes, mtime an hour ahead: as plain, and the stray has no record.
  no-desc   project_description.json moved out of build/: exit != 0, nothing
            judged, no record.
  no-key    project_description.json without "app_bin": likewise.
  no-file   app_bin's file moved out of build/, the stray beside it: likewise.

Before each case the records in build/ and the files a case moves or adds are
put back or removed, so a record left by one case cannot pass the next. Each
case must fire the hook exactly once and leave `git status` empty, or it fails:
a case whose change never happened would otherwise pass on an untouched build.

The control is the same run in a clone whose gen-inhibit/main is at the parent
of the fix (the newest .bin picked by mtime): stray, no-desc, no-key and
no-file must FAIL there, and plain must PASS.

Run it with the ESP-IDF environment exported (idf.py on PATH), as prod_build.py
needs:

    python test_prod_build_image_real.py --repo <clone>

Prints one line per check and PASS/FAIL per case; exit 0 only if every case
passes. It changes build/ only, and puts each change back; plain's record
is left in place.
"""
import argparse
import glob
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

STRAY = "wican-fw_obd_7654321_man_ecc.bin"
CASES = ["stray", "no-desc", "no-key", "no-file", "plain"]
REFUSE = {"no-desc", "no-key", "no-file"}


def md5_of(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=True, check=True).stdout.strip()


def load(script):
    spec = importlib.util.spec_from_file_location("prod_build_real", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def builds(cmd):
    return (isinstance(cmd, (list, tuple)) and cmd and os.path.basename(str(cmd[0])) == "idf.py"
            and "build" in cmd)


class Case:
    """One case's change to build/, made by the hook and undone by restore()."""

    def __init__(self, name, bdir, keep):
        self.name, self.bdir, self.keep = name, bdir, keep
        self.fired = 0
        self.named = None       # app_bin's file name, read after the real build
        self.named_md5 = self.named_size = None
        self.moved = []         # (path in build/, path in keep)

    def desc_path(self):
        return os.path.join(self.bdir, "project_description.json")

    def move_out(self, path):
        dst = os.path.join(self.keep, os.path.basename(path))
        shutil.move(path, dst)
        self.moved.append((path, dst))

    def add_stray(self):
        p = os.path.join(self.bdir, STRAY)
        with open(p, "wb") as fh:
            fh.write(b"stray image, not the one the build named\n" * 4096)
        t = time.time() + 3600
        os.utime(p, (t, t))

    def apply(self):
        self.fired += 1
        with open(self.desc_path(), encoding="utf-8") as fh:
            self.named = os.path.basename(json.load(fh)["app_bin"])
        named = os.path.join(self.bdir, self.named)
        self.named_md5, self.named_size = md5_of(named), os.path.getsize(named)
        if self.name == "stray":
            self.add_stray()
        elif self.name == "no-desc":
            self.move_out(self.desc_path())
        elif self.name == "no-key":
            with open(self.desc_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            shutil.copy2(self.desc_path(), os.path.join(self.keep, "project_description.json"))
            self.moved.append((self.desc_path(), os.path.join(self.keep, "project_description.json")))
            del d["app_bin"]
            with open(self.desc_path(), "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2)
        elif self.name == "no-file":
            self.add_stray()
            self.move_out(named)

    def restore(self):
        for src, dst in reversed(self.moved):
            if os.path.exists(src):
                os.remove(src)
            shutil.move(dst, src)
        self.moved = []


def clear(bdir):
    for p in glob.glob(os.path.join(bdir, "*.prod.json")) + [os.path.join(bdir, STRAY)]:
        if os.path.exists(p):
            os.remove(p)


def records(bdir):
    out = {}
    for p in sorted(glob.glob(os.path.join(bdir, "*.prod.json"))):
        with open(p, encoding="utf-8") as fh:
            out[os.path.basename(p)] = json.load(fh)
    return out


def run_case(repo, name, keep):
    bdir = os.path.join(repo, "build")
    os.makedirs(keep, exist_ok=True)
    if os.path.isdir(bdir):
        clear(bdir)
    case = Case(name, bdir, keep)
    mod = load(os.path.join(repo, "tools", "prod_build.py"))
    if not hasattr(mod, "check_image_symbols"):
        return case, None, [], {}, "the script has no check_image_symbols to spy on"
    judged = []
    real_judge = mod.check_image_symbols

    def spy(image, *a, **kw):
        judged.append(os.path.basename(str(image)))
        return real_judge(image, *a, **kw)

    mod.check_image_symbols = spy
    real_run = subprocess.run

    def hooked(cmd, *a, **kw):
        r = real_run(cmd, *a, **kw)
        if builds(cmd) and getattr(r, "returncode", 1) == 0:
            case.apply()
        return r

    mod.subprocess.run = hooked
    argv, cwd = sys.argv, os.getcwd()
    sys.argv = [os.path.join(repo, "tools", "prod_build.py")]
    os.chdir(repo)
    try:
        try:
            rc = mod.main()
        except SystemExit as e:
            rc = e.code
        rc = 0 if rc is None else rc if isinstance(rc, int) else 1
        sys.stdout.flush()
        recs = records(bdir) if os.path.isdir(bdir) else {}
    finally:
        mod.subprocess.run = real_run
        sys.argv = argv
        os.chdir(cwd)
        case.restore()
    return case, rc, judged, recs, None


def verdict(repo, case, rc, judged, recs):
    """List of (ok, text) checks for one case."""
    head = git(repo, "rev-parse", "HEAD")
    c = [(case.fired == 1, "the hook fired %d time(s) after a build (needs 1)" % case.fired),
         (git(repo, "status", "--porcelain") == "", "git status empty after the case")]
    if case.name in REFUSE:
        c += [(rc != 0, "refused: exit %s" % rc),
              (judged == [], "nothing judged (judged %s)" % judged),
              (recs == {}, "no record (records %s)" % sorted(recs))]
        return c
    want = (case.named or "?") + ".prod.json"
    c += [(rc == 0, "built: exit %s" % rc),
          (judged == [case.named], "judged app_bin's image %s (judged %s)" % (case.named, judged)),
          (sorted(recs) == [want], "one record, %s (records %s)" % (want, sorted(recs)))]
    r = recs.get(want) or {}
    c += [(r.get("image") == case.named, "record image %s" % r.get("image")),
          (r.get("md5") == case.named_md5, "record md5 %s, app_bin's file %s" % (r.get("md5"), case.named_md5)),
          (r.get("size") == case.named_size, "record size %s, app_bin's file %s" % (r.get("size"), case.named_size)),
          (r.get("commit") == head, "record commit %s, HEAD %s" % (r.get("commit"), head))]
    if case.name == "stray":
        c.append((STRAY + ".prod.json" not in recs, "the stray %s has no record" % STRAY))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="a clone with gen-inhibit/main checked out, clean")
    ap.add_argument("--cases", default=",".join(CASES))
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    cases = [x for x in a.cases.split(",") if x]
    bad = [x for x in cases if x not in CASES]
    if bad:
        ap.error("unknown case(s): %s" % ", ".join(bad))
    print("repo HEAD %s (%s), branch %s" % (git(repo, "rev-parse", "--short", "HEAD"),
                                           git(repo, "log", "-1", "--format=%s"),
                                           git(repo, "rev-parse", "--abbrev-ref", "HEAD")), flush=True)
    keep = os.path.join(tempfile.gettempdir(), "gen-inhibit-prodbuild-real-keep")
    shutil.rmtree(keep, ignore_errors=True)
    results = []
    for name in cases:
        print("\n=== case %s ===" % name, flush=True)
        case, rc, judged, recs, err = run_case(repo, name, keep)
        checks = [(False, "ERROR: " + err)] if err else verdict(repo, case, rc, judged, recs)
        ok = all(k for k, _ in checks)
        for k, text in checks:
            print("  %s %s" % ("ok  " if k else "FAIL", text), flush=True)
        print("case %s: %s" % (name, "PASS" if ok else "FAIL"), flush=True)
        results.append((name, ok))
    shutil.rmtree(keep, ignore_errors=True)
    print("\nsummary: " + ", ".join("%s %s" % (n, "PASS" if k else "FAIL") for n, k in results))
    allok = all(k for _, k in results)
    print("PASS" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
