"""Downloaded components checked against their own manifests (SPEC R10).

Run after `idf.py reconfigure`, which is what downloads them on a fresh
clone. Before it, managed_components/ may not exist, and a check run then
finds nothing and passes having judged nothing (gen-inhibit test evidence
12.3 on e3d9437: the component that went into the image was never checked).

For every folder in managed_components/:
  - every file CHECKSUMS.json lists is there, with its size and SHA-256;
  - every file there is listed, except CHECKSUMS.json and .component_hash
    themselves. A manifest speaks only about the files it names, so an added
    file leaves every listed hash intact;
  - its .component_hash equals the component_hash dependencies.lock gives
    for it.
And every component dependencies.lock says is downloaded (DOWNLOADED_TYPES)
is on disk.

Why re-hash rather than trust the Component Manager: managed_components/ is
git-ignored, so a clean worktree says nothing about it. The manager's default
validation compares the text of .component_hash against the lock and does not
re-hash the tree (STRICT_CHECKSUM defaults to False), and its directory hash
leaves .component_hash out, so an edited component can still present a
matching one.
"""

import hashlib
import json
import os

from . import lockfile

MANIFEST_EXEMPT = {".component_hash", "CHECKSUMS.json"}


def check_component(cdir, lock_entry):
    """Problems (a list of strings, empty when the component is clean) and
    the number of listed files verified, for one component folder."""
    problems = []
    checked = 0
    cpath = os.path.join(cdir, "CHECKSUMS.json")
    if not os.path.isfile(cpath):
        return ["no CHECKSUMS.json"], 0
    try:
        with open(cpath, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as e:
        return ["CHECKSUMS.json cannot be read: %s" % e], 0
    if not isinstance(man, dict) or man.get("algorithm") != "sha256":
        return ["CHECKSUMS.json: unsupported algorithm %r"
                % (man.get("algorithm") if isinstance(man, dict) else None)], 0

    listed = {}
    for f in man.get("files", []):
        listed[str(f.get("path", "")).replace("\\", "/")] = f
    for rel, f in sorted(listed.items()):
        p = os.path.join(cdir, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            problems.append("missing: %s" % rel)
            continue
        with open(p, "rb") as fh:
            data = fh.read()
        if len(data) != f.get("size"):
            problems.append("size %d != %s: %s" % (len(data), f.get("size"), rel))
            continue
        if hashlib.sha256(data).hexdigest() != f.get("hash"):
            problems.append("hash mismatch: %s" % rel)
            continue
        checked += 1

    for dirpath, _dirs, files in os.walk(cdir):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), cdir)
            rel = rel.replace(os.sep, "/")
            if rel in listed or rel in MANIFEST_EXEMPT:
                continue
            problems.append("not listed in CHECKSUMS.json: %s" % rel)

    hp = os.path.join(cdir, ".component_hash")
    on_disk = None
    if os.path.isfile(hp):
        with open(hp, encoding="utf-8") as fh:
            on_disk = fh.read().strip()
    want = (lock_entry or {}).get("component_hash")
    if lock_entry is None:
        problems.append("not in dependencies.lock")
    elif want is None:
        problems.append("dependencies.lock gives no component_hash")
    elif on_disk is None:
        problems.append("no .component_hash")
    elif on_disk != want:
        problems.append(".component_hash %s != dependencies.lock %s"
                        % (on_disk, want))
    return problems, checked


def check_managed(project_root):
    """R10 over a project after its reconfigure. Returns (ok, report), where
    report is {folder name: {"files_checked": n, "problems": [...]}}; a
    component the lock names but that is not on disk is reported under its
    folder name with that problem."""
    locks = lockfile.read(os.path.join(project_root, "dependencies.lock"))
    root = os.path.join(project_root, "managed_components")
    on_disk = (sorted(n for n in os.listdir(root)
                      if os.path.isdir(os.path.join(root, n)))
               if os.path.isdir(root) else [])
    by_dir = {lockfile.managed_dir_name(n): e for n, e in locks.items()}
    report = {}
    for name, entry in sorted(locks.items()):
        if entry.get("source", {}).get("type") not in lockfile.DOWNLOADED_TYPES:
            continue
        d = lockfile.managed_dir_name(name)
        if d not in on_disk:
            report[d] = {"files_checked": 0, "problems": [
                "named in dependencies.lock, not in managed_components/ "
                "after the reconfigure"]}
    for d in on_disk:
        problems, checked = check_component(os.path.join(root, d),
                                            by_dir.get(d))
        report[d] = {"files_checked": checked, "problems": problems}
    ok = all(not r["problems"] for r in report.values())
    return ok, report
