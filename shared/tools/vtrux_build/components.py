"""Downloaded components checked against the lock and their own manifests
(SPEC R10, as ruled 2026-10-23, option (a)).

Two parts. The first is the Component Manager's own: the release
reconfigure runs in strict checksum mode (release.py sets
STRICT_ENV in idf.py's environment and refuses if OVERWRITE_ENV is set), so
a component whose folder no longer hashes to its recorded hash stops the
configure. For a component without a CHECKSUMS.json (a git download) that
re-hash covers every file, an added one included.

The second is this module, run after the reconfigure, which is what
downloads the components on a fresh clone. Before it, managed_components/
may not exist, and a check run then finds nothing and passes having judged
nothing (gen-inhibit test evidence 12.3 on e3d9437). For every folder in
managed_components/:
  - its .component_hash equals the component_hash dependencies.lock gives
    it;
  - if it came with a CHECKSUMS.json (a registry download): every file it
    lists is there with its size and SHA-256, and every file there is
    listed, except CHECKSUMS.json and .component_hash themselves. The
    manager's strict mode checks only the listed files and never looks for
    an unlisted one (hash_tools/validate.py, validate_checksums_eq_hashdir),
    so the added-file check is this one's;
  - no CHECKSUMS.json is not a problem in itself.
And every component dependencies.lock says is downloaded (DOWNLOADED_TYPES)
is on disk.

managed_components/ is git-ignored, so a clean worktree says nothing about
it.
"""

import hashlib
import json
import os

from . import lockfile

MANIFEST_EXEMPT = {".component_hash", "CHECKSUMS.json"}

# The Component Manager's settings (idf_component_tools/environment.py in
# idf-component-manager 2.5.2; environment variables only, no aliases).
STRICT_ENV = "IDF_COMPONENT_STRICT_CHECKSUM"
OVERWRITE_ENV = "IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS"


def check_component(cdir, lock_entry):
    """Problems (a list of strings, empty when the component is clean) and
    the number of CHECKSUMS.json-listed files verified, for one component
    folder."""
    problems = _check_hash_file(cdir, lock_entry)
    cpath = os.path.join(cdir, "CHECKSUMS.json")
    if not os.path.isfile(cpath):
        return problems, 0
    listed_problems, checked = _check_listed(cdir, cpath)
    return listed_problems + problems, checked


def _check_hash_file(cdir, lock_entry):
    """.component_hash against the lock's component_hash."""
    hp = os.path.join(cdir, ".component_hash")
    on_disk = None
    if os.path.isfile(hp):
        with open(hp, encoding="utf-8") as fh:
            on_disk = fh.read().strip()
    want = (lock_entry or {}).get("component_hash")
    if lock_entry is None:
        return ["not in dependencies.lock"]
    if want is None:
        return ["dependencies.lock gives no component_hash"]
    if on_disk is None:
        return ["no .component_hash"]
    if on_disk != want:
        return [".component_hash %s != dependencies.lock %s" % (on_disk, want)]
    return []


def _check_listed(cdir, cpath):
    """A registry download against its CHECKSUMS.json, unlisted files
    included."""
    problems = []
    checked = 0
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
