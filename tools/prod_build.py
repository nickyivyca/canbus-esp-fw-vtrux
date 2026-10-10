#!/usr/bin/env python3
"""Build an image meant for the truck, and refuse to if anything is unclean.

Spec 12.3 item 3, "Prod builds are built on a clean tree" (user, 2026-10-05),
and spec 13 "Which build": the latest HEAD at the time of the flash, as a prod
build. A prod build is ANY image meant for the truck.

WHAT IT REFUSES, and each refusal is a separate exit rather than a warning:

  1. a working tree with uncommitted changes;
  2. a `managed_components/` component whose files differ from its own
     `CHECKSUMS.json`, or which carries a file that `CHECKSUMS.json` does not
     list, or whose `.component_hash` differs from `dependencies.lock`;
  3. a build that was not preceded by `idf.py reconfigure`.

and on success it writes the commit, the image's md5 and its size beside the
image, as `<image>.prod.json`. `test/build_checks/build_output.py` refuses an
image with no such record.

WHY A CLEAN TREE AND A RECONFIGURE ARE THE WHOLE POINT. ESP-IDF fixes the
output filename, `project_name` and the embedded version at CMake CONFIGURE
time, not at build time -- `git describe` runs in `CMakeLists.txt`, once. So
an incremental `ninja` over edited sources produces NEW CODE UNDER THE OLD,
CLEAN-LOOKING NAME, and if the edits are then reverted no other check can see
it. Measured 2026-09-29 on `NICKY-XPS`: five modified files under `main/`
rebuilt as `wican-fw_obd_5364648.bin` with no `-dirty`, overwriting the
verified bench image of that name -- same filename, different bytes (md5
`7503d289...` against `a8793a91...`). A build that starts from a clean tree
and reconfigures first cannot carry a stale name or stale code.

WHY `managed_components/` NEEDS CHECKING AT ALL. It is git-ignored, so the
clean-tree check above says nothing about it: a modified downloaded component
is invisible to `git status` and ships in the image. The IDF component manager
does NOT protect against this by default --
`ComponentManagerSettings().STRICT_CHECKSUM` defaults to False
(`idf_component_tools/environment.py:259-266`), and the default validation
path only compares the TEXT of `.component_hash` against the lock file
(`validate.py:47-60`) rather than re-hashing the tree. Worse,
`file_tools.py:49` excludes `**/.component_hash` from the directory hash, so
a component whose source has been edited can still present a matching
`.component_hash`. This script therefore re-hashes every file listed in
`CHECKSUMS.json` itself rather than trusting either mechanism.

UNLISTED FILES COUNT AS MISMATCHES. A checksum manifest can only speak about
files it names; an attacker or an accident that ADDS a source file to a
component leaves every listed hash intact. The only two exemptions are
`.component_hash` and `CHECKSUMS.json` themselves, which are metadata the
manifest cannot contain.

RUN IT FROM AN EXPORTED IDF SHELL. On `NICKY-XPS` that means PowerShell with
the 3.11 directory prepended to PATH before sourcing `export.ps1`; see
`tools-reference.md`. The Bash tool cannot be used -- `idf_tools.py` refuses
to run when `MSYSTEM` is set, which it is under Git Bash, and unsetting it
does not work because MSYS re-injects it.

    $env:IDF_PATH = "C:\\Users\\Nicky\\esp\\esp-idf"
    $env:PATH = "C:\\Users\\Nicky\\AppData\\Local\\Programs\\Python\\Python311-32;" + $env:PATH
    . "$env:IDF_PATH\\export.ps1"
    python tools\\prod_build.py
    python tools\\prod_build.py --check-only     # refusals only, no build
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# CHECKSUMS.json cannot list itself, and .component_hash is written by the
# component manager after the manifest is made. Everything else on disk must
# be in the manifest.
MANIFEST_EXEMPT = {".component_hash", "CHECKSUMS.json"}


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def fail(msg):
    print("REFUSED: %s" % msg)
    return False


def tracked_at_head(rel):
    """Does HEAD have this path? False if this is not a git repo at all."""
    # Forward slashes: git's path syntax, not the platform's.
    r = run(["git", "cat-file", "-e", "HEAD:%s" % rel.replace(os.sep, "/")])
    return r.returncode == 0


def check_repo():
    """This has to be the firmware repo, not whatever directory we are in."""
    need = ["CMakeLists.txt",
            os.path.join("main", "gen_inhibit.c"),
            "dependencies.lock"]
    missing = [rel for rel in need
               if not os.path.exists(os.path.join(REPO, rel))]
    if not missing:
        return True

    #
    # A MARKER CAN BE MISSING FOR TWO OPPOSITE REASONS, and until 2026-10-10
    # this reported both as the first one.
    #
    # Wrong directory: the file is not here and git has never heard of it.
    # Deleted file: the file is not here, HEAD has it, and the tree is simply
    # dirty -- which check_clean_tree() below refuses, by name and with the
    # spec bullet.
    #
    # Reporting a deletion as "this is not the wican-fw-vtrux repo" sends a
    # reader to look for a wrong path when what they have is an uncommitted
    # delete. Both cases still refuse, in exactly the cases spec 12.3 item 3
    # names; only the reason changes, and one of the two reasons was false.
    #
    # Found 2026-10-10 by the tester's test_prod_build_refusals.py, whose
    # deleted-source case used main/gen_inhibit.c -- one of these very markers
    # -- so it was passing on the identity guard and would have passed with
    # the clean-tree check removed entirely. Reviewer's ruling the same day:
    # make the reason true, since the spec fixes when the script refuses and
    # not the wording.
    #
    unknown = [rel for rel in missing if not tracked_at_head(rel)]
    if unknown:
        return fail("%s is not the wican-fw-vtrux repo (missing %s)"
                    % (REPO, ", ".join(os.path.basename(m) for m in unknown)))
    return True


def check_clean_tree(state):
    r = run(["git", "status", "--porcelain"])
    if r.returncode != 0:
        return fail("git status failed: %s" % r.stderr.strip())
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if dirty:
        print("REFUSED: the working tree has %d uncommitted change(s). A prod "
              "build is built on a clean tree (spec 12.3 item 3), because the "
              "image name and the embedded version are fixed at CMake "
              "configure time and cannot be trusted otherwise." % len(dirty))
        for ln in dirty[:20]:
            print("    %s" % ln)
        if len(dirty) > 20:
            print("    ... and %d more" % (len(dirty) - 20))
        return False
    h = run(["git", "rev-parse", "HEAD"])
    desc = run(["git", "describe", "--tags", "--always", "--dirty"])
    br = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    state["commit"] = h.stdout.strip()
    state["describe"] = desc.stdout.strip()
    state["branch"] = br.stdout.strip()
    print("[  ok  ] working tree clean            HEAD %s (%s) on %s"
          % (state["commit"][:7], state["describe"], state["branch"]))
    return True


def lock_hashes():
    """component_hash per component, from dependencies.lock.

    Parsed with a line scanner rather than a YAML library, because adding a
    dependency to a build-gate script to read the dependency lock is a
    circularity worth avoiding, and the file's shape here is two levels deep
    and stable.
    """
    path = os.path.join(REPO, "dependencies.lock")
    out = {}
    cur = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^  ([A-Za-z0-9_\-./]+):\s*$", line)
            if m:
                cur = m.group(1)
                continue
            m = re.match(r"^    component_hash:\s*([0-9a-f]+)\s*$", line)
            if m and cur:
                out[cur] = m.group(1)
    return out


def check_components(state):
    """Re-hash every managed component against its own CHECKSUMS.json."""
    root = os.path.join(REPO, "managed_components")
    if not os.path.isdir(root):
        print("[  ok  ] managed components          none on disk")
        state["components"] = {}
        return True

    locks = lock_hashes()
    report = {}
    ok = True
    for name in sorted(os.listdir(root)):
        cdir = os.path.join(root, name)
        if not os.path.isdir(cdir):
            continue
        entry = {"files_checked": 0, "problems": []}
        report[name] = entry

        cpath = os.path.join(cdir, "CHECKSUMS.json")
        if not os.path.exists(cpath):
            entry["problems"].append("no CHECKSUMS.json")
            ok = False
            continue
        with open(cpath, encoding="utf-8") as fh:
            man = json.load(fh)
        if man.get("algorithm") != "sha256":
            entry["problems"].append("unsupported algorithm %r"
                                     % man.get("algorithm"))
            ok = False
            continue

        listed = {}
        for f in man.get("files", []):
            listed[f["path"].replace("\\", "/")] = f
        # Every listed file must be present, the right size, and the right hash.
        for rel, f in sorted(listed.items()):
            p = os.path.join(cdir, rel.replace("/", os.sep))
            if not os.path.exists(p):
                entry["problems"].append("missing: %s" % rel)
                continue
            data = open(p, "rb").read()
            if len(data) != f.get("size"):
                entry["problems"].append(
                    "size %d != %s: %s" % (len(data), f.get("size"), rel))
                continue
            got = hashlib.sha256(data).hexdigest()
            if got != f.get("hash"):
                entry["problems"].append("hash mismatch: %s" % rel)
                continue
            entry["files_checked"] += 1

        #
        # UNLISTED FILES ARE MISMATCHES. A manifest can only speak about files
        # it names, so an ADDED file leaves every listed hash intact and is
        # invisible to a checksum-only check.
        #
        for dirpath, _dirs, files in os.walk(cdir):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, cdir).replace(os.sep, "/")
                if rel in listed or os.path.basename(rel) in MANIFEST_EXEMPT:
                    continue
                entry["problems"].append("not listed in CHECKSUMS.json: %s"
                                         % rel)

        # And the component hash must match the lock file.
        hp = os.path.join(cdir, ".component_hash")
        disk_hash = (open(hp, encoding="utf-8").read().strip()
                     if os.path.exists(hp) else None)
        lock_name = name.replace("__", "/", 1)
        want = locks.get(lock_name)
        entry["component_hash_on_disk"] = disk_hash
        entry["component_hash_in_lock"] = want
        if want is None:
            entry["problems"].append(
                "no component_hash in dependencies.lock for %r" % lock_name)
        elif disk_hash != want:
            entry["problems"].append(
                ".component_hash %s != dependencies.lock %s"
                % (disk_hash, want))

        if entry["problems"]:
            ok = False
            print("[ FAIL ] component %-22s %d problem(s)"
                  % (name, len(entry["problems"])))
            for p in entry["problems"][:10]:
                print("             %s" % p)
            if len(entry["problems"]) > 10:
                print("             ... and %d more"
                      % (len(entry["problems"]) - 10))
        else:
            print("[  ok  ] component %-22s %d files verified against "
                  "CHECKSUMS.json, .component_hash matches the lock"
                  % (name, entry["files_checked"]))

    state["components"] = report
    if not ok:
        print("REFUSED: a downloaded component differs from its own "
              "CHECKSUMS.json or from dependencies.lock. managed_components/ "
              "is git-ignored, so the clean-tree check above cannot see this "
              "and a modified component would ship in the image.")
    return ok


def check_idf_env():
    if not os.environ.get("IDF_PATH"):
        return fail("IDF_PATH is not set. Run this from an exported ESP-IDF "
                    "shell -- see this file's docstring for the incantation.")
    print("[  ok  ] IDF environment             IDF_PATH=%s"
          % os.environ["IDF_PATH"])
    return True


def do_build(state):
    """reconfigure THEN build, in one idf.py invocation, never a bare ninja."""
    print("\nidf.py reconfigure build ...")
    r = subprocess.run(["idf.py", "reconfigure", "build"], cwd=REPO,
                       shell=(os.name == "nt"))
    if r.returncode != 0:
        print("REFUSED: the build failed (exit %d)." % r.returncode)
        return None
    return newest_image()


def newest_image():
    bdir = os.path.join(REPO, "build")
    cands = [os.path.join(bdir, f) for f in os.listdir(bdir)
             if f.startswith("wican-fw_obd_") and f.endswith(".bin")]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def write_record(image, state):
    data = open(image, "rb").read()
    rec = {
        "kind": "prod-build record (spec 12.3 item 3)",
        "written_by": "tools/prod_build.py",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "image": os.path.basename(image),
        "commit": state["commit"],
        "describe": state["describe"],
        "branch": state["branch"],
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "idf_path": os.environ.get("IDF_PATH"),
        "components": state.get("components", {}),
        "clean_tree": True,
        "reconfigured": True,
    }
    out = image + ".prod.json"
    with open(out, "w", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("\n[  ok  ] prod record                 %s" % os.path.basename(out))
    print("           commit %s  size %d  md5 %s"
          % (rec["commit"][:7], rec["size"], rec["md5"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true",
                    help="run the refusals and stop; build nothing")
    args = ap.parse_args()

    print("=" * 72)
    print("PROD BUILD -- an image meant for the truck (spec 12.3 item 3)")
    print("=" * 72)

    state = {}
    if not check_repo():
        return 1
    ok = check_clean_tree(state)
    ok = check_components(state) and ok
    if not args.check_only:
        ok = check_idf_env() and ok
    if not ok:
        print("\nNOT BUILT. Every refusal above is a condition of a prod "
              "build, not a warning.")
        return 1

    if args.check_only:
        print("\nCHECK ONLY: every refusal passed. Nothing was built.")
        return 0

    image = do_build(state)
    if image is None:
        return 1
    write_record(image, state)
    print("\nPROD BUILD COMPLETE: %s" % os.path.basename(image))
    print("Run test/build_checks/build_output.py against it before flashing. "
          "This script establishes provenance; it does not check the image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
