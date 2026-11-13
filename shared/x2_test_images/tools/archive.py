"""Builds one X2 test image and archives it to Seafile with its manifest row.

TEST EQUIPMENT. Shared harness of x2_test_images (owner: the testers, the
gen-inhibit reviewer's ruling of 2026-11-12), generic over the image folder
and its variants (x2_image.json).

    systemd-run --user --scope --slice=build.slice \\
      python3.13 -B archive.py build IMAGE_DIR VARIANT --builder ROLE [--jobs N]

from a shell exported for ESP-IDF v5.4.2 (x2_test_images SPEC T6). In one
go it:
  1. reads the tree's git state before the configure: HEAD, the exact tag
     at HEAD, and `git status --porcelain --ignored --untracked-files=all`;
  2. makes the shared tooling's dev build of the variant (shared/tools R31,
     with its environment check R32 first), with this checkout's
     shared/tools unless --tools names another;
  3. reads the git state again;
  4. checks the build with check_image.py (the variant is the image's own,
     from its project_name; a mismatch refuses);
  5. copies every flash file and the .elf into
     $REVERSE_IT_DIR/projects/vtrux/notes/artifacts/x2_test_images/<image>/
     builds/<app sha256, 16 digits>/, and appends one row to manifest.jsonl
     beside builds/ (SPEC T2, T6: rows are appended, never replaced).

Every build that gets to step 5 gets a row, dev builds too. `pinnable` is
true only when all of these hold (SPEC T7; the reviewer's rulings of
2026-11-12):
  variant_pinnable     the image's own variant is pinnable in x2_image.json
                       (a test variant never is, whatever the caller says)
  head_is_tag          HEAD is a shared/main tag's commit (YYYY.MM.DD[.N])
  clean_before         the status was empty before the configure
  clean_after          ... and after the build
  no_tracked_change    no tracked file changed, and HEAD did not move
  version_is_tag       the app descriptor's version (git describe, R4) is
                       exactly the tag
  check_image          check_image.py passed (otherwise nothing is archived)
For "clean", the only entries allowed are ignored files under the image's
own build/ and managed_components/. So a pinnable build is made in a
worktree of its own, detached at the tag (`git -C anchor worktree add
--detach ../<role>-x2img-<tag> <tag>`), never in ../shared-<tag>/ (R7, R23).

Refused, with nothing archived: REVERSE_IT_DIR unset or without
projects/vtrux/notes/artifacts/; the archive inside the git tree; the build
failing; check_image.py failing or refusing; an existing build folder whose
files differ. Exit 0 archived, 2 refused.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_image  # noqa: E402

TAG_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")
ARCHIVE_REL = os.path.join("projects", "vtrux", "notes", "artifacts")
SCHEMA = 1


class Refused(Exception):
    pass


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip()


def git_state(image_dir):
    """HEAD, the exact tag, describe and the status entries of the tree that
    holds image_dir."""
    rc, top = _git(image_dir, "rev-parse", "--show-toplevel")
    if rc != 0:
        raise Refused("%s is not in a git tree" % image_dir)
    _rc, head = _git(image_dir, "rev-parse", "HEAD")
    rc, tag = _git(image_dir, "describe", "--exact-match", "--tags", "HEAD")
    tag = tag if rc == 0 and TAG_RE.match(tag) else None
    _rc, describe = _git(image_dir, "describe", "--always", "--tags", "--dirty")
    rc, status = _git(top, "status", "--porcelain", "--ignored",
                      "--untracked-files=all")
    if rc != 0:
        raise Refused("git status failed in %s" % top)
    _rc, diff = _git(top, "diff", "--name-only", "HEAD")
    return {"top": top, "head": head, "tag": tag, "describe": describe,
            "status": [l for l in status.splitlines() if l],
            "tracked_changed": [l for l in diff.splitlines() if l]}


def unclean(state, image_dir):
    """Status entries other than ignored files under the image's own build/
    and managed_components/."""
    rel = os.path.relpath(os.path.realpath(image_dir),
                          os.path.realpath(state["top"])).replace(os.sep, "/")
    allowed = tuple("%s/%s/" % (rel, d) if rel != "." else "%s/" % d
                    for d in ("build", "managed_components"))
    out = []
    for line in state["status"]:
        code, path = line[:2], line[3:]
        if code == "!!" and path.startswith(allowed):
            continue
        out.append(line)
    return out


def devbuild(image_dir, variant, tools_dir, jobs=None):
    """The shared tooling's dev build (R31). Returns (exit code, command)."""
    cmd = [sys.executable, "-B", "-m", "vtrux_build", "build", variant,
           "--project", image_dir]
    if jobs:
        cmd += ["--jobs", str(jobs)]
    env = dict(os.environ)
    env["PYTHONPATH"] = tools_dir
    r = subprocess.run(cmd, env=env)
    return r.returncode, "PYTHONPATH=%s %s" % (tools_dir, " ".join(cmd))


def _hashes(path):
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return {"size": size, "sha256": sha.hexdigest(), "md5": md5.hexdigest()}


def archive_root(image):
    rid = os.environ.get("REVERSE_IT_DIR")
    if not rid:
        raise Refused("REVERSE_IT_DIR is not set")
    base = os.path.join(rid, ARCHIVE_REL)
    if not os.path.isdir(base):
        raise Refused("%s does not exist" % base)
    return os.path.join(base, "x2_test_images", image)


IMAGE_README = """# x2_test_images/{image}/

Built images of the X2 test image `{image}` (x2_test_images SPEC T2, T6),
written by `shared/x2_test_images/tools/archive.py`. Not in git.

- `manifest.jsonl`: one JSON row per build, appended, never rewritten.
- `builds/<app sha256, 16 digits>/`: the build's flash files and `.elf`,
  each folder with its own README.

Only a row with `"pinnable": true` may be pinned (SPEC T7, T9).
"""

BUILD_README = """# {folder_id}

One build of the X2 test image `{image}`, variant `{variant}`
(`{project_name}`), archived by `shared/x2_test_images/tools/archive.py`
at {built_utc}. Its manifest row (`../../manifest.jsonl`, the rows naming
this folder) has every file's offset, size, SHA-256 and MD5, the shared
commit and tag, the code hash and whether it may be pinned.

| File | Flash offset |
|---|---|
{table}
"""


def write_archive(image_data, paths, app, row, dest):
    """Copies the files into dest/builds/<id>/ and appends row to
    dest/manifest.jsonl. Returns the folder's path."""
    folder_id = row["app_sha256"][:16]
    folder = os.path.join(dest, "builds", folder_id)
    files = [(off, p) for off, p in paths["flash"]] + [(None, paths["elf"])]
    if os.path.isdir(folder):
        for _off, p in files:
            have = os.path.join(folder, os.path.basename(p))
            if not os.path.isfile(have) or _hashes(have) != _hashes(p):
                raise Refused("%s exists with different files" % folder)
    else:
        tmp = folder + ".partial"
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        for _off, p in files:
            shutil.copy2(p, os.path.join(tmp, os.path.basename(p)))
        table = "\n".join("| `%s` | %s |" % (os.path.basename(p),
                                             "0x%x" % off if off is not None
                                             else "(not flashed)")
                          for off, p in files)
        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(BUILD_README.format(folder_id=folder_id, table=table, **row))
        os.rename(tmp, folder)
    readme = os.path.join(dest, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8", newline="\n") as f:
            f.write(IMAGE_README.format(image=image_data["image"]))
    with open(os.path.join(dest, "manifest.jsonl"), "a", encoding="utf-8",
              newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return folder


def run(image_dir, variant, builder, tools_dir=None, jobs=None,
        build_fn=None, now=None):
    """The whole of `archive.py build`. Returns (row, folder). Raises
    Refused. build_fn(image_dir, variant) -> (exit code, command) replaces
    the dev build (tests)."""
    image_dir = os.path.abspath(image_dir)
    data = check_image.load_data(image_dir) if os.path.isdir(image_dir) \
        else None
    if data is None:
        raise Refused("no image folder %s" % image_dir)
    if variant not in data["variants"]:
        raise Refused("no variant %r; the variants are: %s"
                      % (variant, " ".join(data["variants"])))
    if not builder:
        raise Refused("--builder (the role that builds) is required")
    dest = archive_root(data["image"])
    before = git_state(image_dir)
    if os.path.realpath(dest).startswith(os.path.realpath(before["top"]) + os.sep):
        raise Refused("the archive %s is inside the git tree" % dest)
    unclean_before = unclean(before, image_dir)

    if tools_dir is None:
        tools_dir = os.path.normpath(os.path.join(HERE, "..", "..", "tools"))
    if build_fn is None:
        rc, command = devbuild(image_dir, variant, tools_dir, jobs)
    else:
        rc, command = build_fn(image_dir, variant)
    if rc != 0:
        raise Refused("the build failed (exit %d)" % rc)

    after = git_state(image_dir)
    unclean_after = unclean(after, image_dir)
    try:
        res = check_image.check(image_dir, variant)
    except check_image.Refused as e:
        raise Refused("check_image refused: %s" % e)
    if not res.ok:
        failed = ["%s: %s" % (n, d) for n, ok, d in res.checks if not ok]
        raise Refused("check_image FAILED: %s" % "; ".join(failed))
    app = res.app
    image_variant = res.image_variant

    checks = {
        "variant_pinnable": data["variants"][image_variant]["pinnable"],
        "head_is_tag": before["tag"] is not None and after["tag"] == before["tag"],
        "clean_before": not unclean_before,
        "clean_after": not unclean_after,
        "no_tracked_change": (not before["tracked_changed"]
                              and not after["tracked_changed"]
                              and before["head"] == after["head"]),
        "version_is_tag": before["tag"] is not None and app.version == before["tag"],
        "check_image": True,
    }
    paths = res.paths
    files = []
    for off, p in paths["flash"]:
        h = _hashes(p)
        files.append({"offset": "0x%x" % off, "name": os.path.basename(p),
                      "size": h["size"], "sha256": h["sha256"], "md5": h["md5"]})
    elf = dict(name=os.path.basename(paths["elf"]), **_hashes(paths["elf"]))
    app_h = _hashes(paths["app"])
    now = now or datetime.datetime.now(datetime.timezone.utc)
    row = {
        "schema": SCHEMA,
        "image": data["image"],
        "variant": image_variant,
        "project_name": app.project_name,
        "pinnable": all(checks.values()),
        "pinnable_checks": checks,
        "unclean_before": unclean_before,
        "unclean_after": unclean_after,
        "shared_commit": before["head"],
        "shared_tag": before["tag"],
        "describe": before["describe"],
        "version": app.version,
        "idf_ver": app.idf_ver,
        "code_hash": app.code_hash,
        "image_hash": app.image_hash,
        "app_elf_sha256": elf["sha256"],
        "app_sha256": app_h["sha256"],
        "app_offset": "0x%x" % paths["app_offset"],
        "files": files,
        "elf": elf,
        "folder": "builds/%s" % app_h["sha256"][:16],
        "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": socket.gethostname(),
        "builder": builder,
        "build_command": command,
        "check_image": res.as_rows(),
    }
    folder = write_archive(data, paths, app, row, dest)
    return row, folder


def _slice_ok():
    """On Linux: is this process in build.slice? (AGENTS.md "Parallel work
    on the shared machine")."""
    try:
        with open("/proc/self/cgroup", encoding="ascii") as f:
            return "/build.slice/" in f.read()
    except OSError:
        return True             # not Linux: no slices to be in


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build, check and archive one variant")
    b.add_argument("image_dir")
    b.add_argument("variant")
    b.add_argument("--builder", required=True,
                   help="the role that builds, e.g. 'gen-inhibit tester'")
    b.add_argument("--tools", help="the shared/tools folder (default: this "
                   "checkout's)")
    b.add_argument("--jobs", type=int)
    a = ap.parse_args(argv)
    if not _slice_ok():
        print("REFUSED: not in build.slice; run it as\n  systemd-run --user "
              "--scope --slice=build.slice python3.13 -B %s ..." % __file__)
        return 2
    try:
        row, folder = run(a.image_dir, a.variant, a.builder, a.tools, a.jobs)
    except (Refused, check_image.Refused) as e:
        print("REFUSED: %s" % e)
        return 2
    print("ARCHIVED %s %s -> %s" % (row["image"], row["variant"], folder))
    print("pinnable: %s" % row["pinnable"])
    for k, v in row["pinnable_checks"].items():
        print("  %-18s %s" % (k, v))
    print("code_hash %s  image_hash %s" % (row["code_hash"], row["image_hash"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
