"""Record every built image in firmware/builds/manifest.json.

WHY THIS EXISTS. The board announces which image it is running in
`0x7F7` B0-B3: the first four bytes of the application ELF's SHA-256.
That is a good identifier and a useless one on its own, because
nothing maps it back to an image. This is that table.

It matters most for the witness build. A board carrying the
completion-order instrumentation looks and behaves exactly like the
bench bridge, and the difference decides whether an ordering
measurement means anything. Reading four bytes off the wire and
finding the row is how an arm proves which image it is talking to
instead of assuming.

IT RUNS AS A POST-BUILD ACTION, so every build of every environment
updates its row. A manifest maintained by hand, or only by the test
runner, would be stale exactly when someone had built something
unusual -- which is the case it exists for.

THE ELF SHA-256 IS READ FROM `esptool image-info`, not recomputed from
the .elf by other means. The point of the field is to match what the
board reports, and the board gets it from the ESP-IDF app descriptor
that esptool is reading. Deriving it independently would be recording
a number that ought to agree rather than the one that does.

Both identities are kept, because they answer different questions:

  src_digest    which SOURCES produced this (see build_identity.py)
  elf_sha256    which exact IMAGE this is

They are not interchangeable. The same sources under a different
toolchain give a different ELF hash, so the toolchain and platform
versions are recorded alongside.
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

Import("env")                                             # noqa: F821

sys.path.insert(0, env.subst("$PROJECT_DIR"))             # noqa: F821
from build_identity import (DIGEST_CHARS, digest_of,      # noqa: E402
                             source_digest, tag_for)

MANIFEST_DIR = "builds"
MANIFEST = "manifest.json"


def elf_sha256_from_esptool(bin_path):
    """The ELF SHA-256 as `esptool image-info` reports it, or None.

    Returns None rather than guessing. A missing hash must leave the
    row visibly incomplete; inventing one would put a wrong answer in
    the one table a bench arm trusts.
    """
    esptool = os.path.join(
        os.path.expanduser("~"), ".platformio", "packages",
        "tool-esptoolpy", "esptool.py")
    if not os.path.exists(esptool):
        return None
    try:
        r = subprocess.run([sys.executable, esptool, "image-info", bin_path],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    m = re.search(r"ELF file SHA256:\s*([0-9a-fA-F]{64})", r.stdout + r.stderr)
    return m.group(1).lower() if m else None


def fw_ver16():
    """DIAG_FW_VER from machine.h -- the number the board puts in 0x7F7."""
    h = os.path.join(env.subst("$PROJECT_DIR"), "src", "machine.h")  # noqa: F821
    try:
        with open(h, "r") as f:
            m = re.search(r"DIAG_FW_VER\s*=\s*(\d+)", f.read())
        return int(m.group(1)) if m else None
    except IOError:
        return None


def toolchain():
    """Platform and package versions, or an explicit error.

    NEVER an empty dict on failure. The first version swallowed every
    exception and returned {}, which recorded "no toolchain
    information" in a field whose whole purpose is to explain why two
    builds of identical sources produced different bytes -- a silent
    blank in exactly the place someone would later be trying to
    account for a discrepancy.
    """
    out = {}
    try:
        p = env.PioPlatform()                             # noqa: F821
    except Exception as exc:
        return {"error": "PioPlatform unavailable: %s" % exc}
    try:
        out["platform"] = "%s@%s" % (p.name, p.version)
    except Exception as exc:
        out["platform_error"] = str(exc)
    # Every package the platform actually has, rather than a guessed
    # list: the names differ by platform version, and a hardcoded list
    # that stops matching would silently record nothing.
    try:
        for item in p.dump_used_packages():
            n = item.get("name")
            v = item.get("version")
            if n and v:
                out[n] = v
    except Exception as exc:
        out["packages_error"] = str(exc)
    return out


def porcelain_paths(line):
    """Every path one `git status --porcelain` line refers to, or None.

    None means "this line was not understood", and the caller treats
    that as dirty. The direction is deliberate: under-reporting a dirty
    tree puts a wrong provenance claim in the manifest, while
    over-reporting only loses the exclusion below.

    The format is two status columns, a space, then the path -- and for
    a rename or copy, `old -> new`, in which case BOTH paths are
    returned, so a line is ignorable only when everything it names is
    ignorable. Every form below was captured from git itself on
    2026-10-08 rather than written from the documentation, including the
    one fact the exclusion depends on: porcelain paths are relative to
    the REPOSITORY ROOT, not to the directory git ran in, whatever `-C`
    says.

        " M charge-interposer/firmware/builds/manifest.json"
        "M  charge-interposer/firmware/builds/manifest.json"
        "?? charge-interposer/firmware/builds/scratch_probe.tmp"
        "R  charge-interposer/firmware/src/machine.cpp -> ...moved.cpp"

    A path git had to quote starts with a double quote and is rejected
    here rather than unquoted. `builds/manifest.json` is plain ASCII
    with no space, so git never quotes the one path this function exists
    to recognise; anything quoted is some other file, which is dirty.
    """
    if len(line) < 4 or line[2] != " ":
        return None
    rest = line[3:]
    parts = rest.split(" -> ") if " -> " in rest else [rest]
    for p in parts:
        if not p or p.startswith('"'):
            return None
    return parts


def porcelain_is_dirty(out, excluded):
    """True if the porcelain output names any path but `excluded`.

    Spec 8.2 (user, 2026-10-08): a row records "whether the working
    tree had uncommitted changes other than to `builds/manifest.json`,
    which the build itself writes".

    WHY THE EXCLUSION EXISTS (reviewer, 2026-10-08). record() writes
    builds/manifest.json as a post-action of the build, so that write
    lands after `git status` has run for that build and is still
    uncommitted when the NEXT build asks. Under the rule this replaced
    -- any `--porcelain` output at all means dirty -- a second build of
    an untouched tree recorded dirty=true, and committing the manifest
    between builds was the only thing keeping the flag honest. A flag
    that reads true whatever the tree looks like carries no
    information, and this one is read by someone trying to find the
    sources an image came from.

    The two rows that carried git identity before this change happen
    not to show it: both were built from a tree whose state the flag
    described correctly, the second after a commit. The defect is in
    what `git status` returns between those builds, which is what the
    offline test feeds this function.

    EXACTLY THAT ONE PATH, not the builds/ directory and not *.json:
    builds/ also holds README.md, which describes this very field, and
    an uncommitted edit to it is a real difference between the tree and
    its commit.
    """
    for line in out.splitlines():
        if not line.strip():
            continue
        paths = porcelain_paths(line)
        if paths is None or any(p != excluded for p in paths):
            return True
    return False


def git_identity(fw_dir):
    """The commit this tree is at, and whether anything is uncommitted.

    Spec 8.2 (user, 2026-10-08): "Each manifest row also records the git
    commit the image was built from and whether the working tree had
    uncommitted changes other than to `builds/manifest.json`, which the
    build itself writes. This sits beside the source digest, not in its
    place: a build with uncommitted changes shares its commit with one
    that has none."

    NEVER A GUESS. If git is missing or this is not a repository the row
    carries an explicit `git_error` and no commit, the same rule
    `toolchain()` follows above and for the same reason: a blank in a
    provenance field is read as "nothing to report" when it means "could
    not tell", and this one would be read while someone tried to find
    the sources an image came from. It mattered until today -- the tree
    lived on SeaDrive outside any repository, which is why build_name.py
    has no git sha in the image name.

    DIRTY MEANS THE WHOLE WORKING TREE, not just the files that feed the
    image, with exactly one exclusion -- builds/manifest.json, which
    this script itself writes (porcelain_is_dirty above). A check
    narrowed to the image's own inputs is more informative when it is
    right and silently wrong when it is not: it would report clean
    while an uncommitted edit sat in a file it had not thought to look
    at, and a provenance field that under-reports is worse than one
    that over-reports. `--porcelain` output is non-empty for staged,
    unstaged and untracked changes alike.

    THE EXCLUDED PATH IS DERIVED, NOT SPELLED OUT, because porcelain
    paths are relative to the repository root while MANIFEST_DIR is
    relative to the firmware directory; `rev-parse --show-prefix`
    supplies the difference. A literal would have gone stale when this
    tree moved from projects/vtrux/tools/interposer/ to
    charge-interposer/ earlier the same week, and it would have gone
    stale in the direction that is hardest to see: the exclusion
    matching nothing, so the flag silently returning to always-true.
    """
    def run(args):
        try:
            r = subprocess.run(["git", "-C", fw_dir] + args,
                               capture_output=True, text=True, timeout=60)
        except Exception as exc:
            return None, str(exc)
        if r.returncode != 0:
            return None, (r.stderr or "").strip()[:200] or "git failed"
        return r.stdout, None

    out, err = run(["rev-parse", "HEAD"])
    if out is None:
        return {"git_error": "no commit: %s" % err}
    commit = out.strip()
    prefix, err = run(["rev-parse", "--show-prefix"])
    if prefix is None:
        return {"git_commit": commit,
                "git_error": "dirty state unknown: %s" % err}
    excluded = prefix.strip() + MANIFEST_DIR + "/" + MANIFEST

    out, err = run(["status", "--porcelain"])
    if out is None:
        return {"git_commit": commit,
                "git_error": "dirty state unknown: %s" % err}
    return {"git_commit": commit,
            "git_dirty": porcelain_is_dirty(out, excluded)}


def row_key(e):
    """What makes a manifest row the same row as another.

    THE IMAGE NAME IS NOT ENOUGH, and that is not a style preference --
    it lost a flashed image's provenance on 2026-10-08. The name is
    `interposer_<src_digest>_<tag>.bin` and the digest hashes the
    SOURCES, so rebuilding unchanged sources reuses the name. Builds
    were not reproducible when that happened (re-measured the same day:
    two clean builds of identical source, same toolchain, both digest
    111342c1dd0f, both 357,648 bytes, gave ELF c190275a... then
    b1b23f81...), so one name legitimately covered MANY different
    binaries.

    REPRODUCIBLE BUILDS LANDED LATER THE SAME DAY (spec 8.2) AND THIS
    RULE STAYS, because they removed one of three reasons a name can
    cover several ELFs and left the other two: a build on a different
    host OS still produces its own ELF from the same sources (8.2 says
    so explicitly), and every row recorded before 8.2 landed is still
    in the table. Keying on the name again would begin by deleting
    those.

    The rule this replaced kept one row per name and justified it as
    not appending "a near-duplicate". Given the above they are not
    near-duplicates; they are different images sharing a name, and
    replacing one with another silently deletes the record of whatever
    is on the board. That is what happened: the row pairing digest
    111342c1dd0f with ELF 7f197bfd..., the image the board was running
    and reporting in 0x7F7, was overwritten by a rebuild and could not
    be restored, because builds/ archives no binaries.

    `elf_sha256 or ""` rather than a special case for a missing hash:
    esptool can fail, and then the row cannot be told apart from
    another failed one, so failures collapse onto a single row instead
    of accumulating while real rows are left alone. One rule, no
    branch.

    The general form, worth keeping past this file: a digest of the
    INPUTS answers "which sources"; only a hash of the ARTEFACT answers
    "which image".
    """
    return (e.get("image") or "", e.get("elf_sha256") or "")


def merge_entry(entries, entry):
    """`entries` with `entry` added, replacing only the SAME build.

    Pure, and separate from record() so the rule can be exercised
    without PlatformIO -- the previous rule lived inline in a
    post-action and so was never tested at all.

    A genuine rebuild that happens to produce identical bytes replaces
    its row (same key), so repeating a build does not grow the file. A
    rebuild producing different bytes keeps both, because both are
    facts about images that exist.
    """
    out = [e for e in entries if row_key(e) != row_key(entry)]
    out.append(entry)
    # built_utc last so several rows under one name read chronologically
    # rather than in whatever order they were merged.
    out.sort(key=lambda e: (e.get("tag") or "", e.get("image") or "",
                            e.get("built_utc") or ""))
    return out


def record(source, target, env):                          # noqa: F841
    bin_path = str(target[0])
    if not os.path.exists(bin_path):
        return

    # PRUNE STALE IMAGES FROM THIS BUILD DIRECTORY FIRST.
    #
    # The filename carries a digest of the sources, so every source
    # edit produces a NEW name and PlatformIO leaves the previous one
    # beside it. Two images in one directory is exactly how the wrong
    # file gets flashed, and build_check treats it as an error -- so
    # without this, every source change breaks the build check until
    # someone cleans by hand. Only this environment's own directory is
    # touched, and only files matching the generated pattern.
    # THE .elf FILES TOO, which this missed until 2026-10-05. The prune
    # globbed only "*.bin", and build_check.py also globs only "*.bin",
    # so the two agreed and neither noticed nine stale ELFs accumulating
    # in .pio/build/selftest from earlier digests -- found by a test that
    # listed the directory for an unrelated reason. The ELF is what the
    # manifest's elf_sha256 is taken from and what a reader reaches for
    # when asking which image a board is running, so a stale one beside a
    # fresh .bin is exactly the confusion the naming scheme exists to
    # prevent. Pruning what build_check looks at, and only that, is a
    # check and a cleanup that agree with each other rather than with the
    # directory.
    # PRUNE BY STEM, NOT BY EXTENSION LIST. Enumerating extensions is
    # what let this miss things twice in one afternoon: the original
    # globbed "*.bin" only and nine stale .elf files had accumulated in
    # .pio/build/selftest; adding ".elf" then left ten stale .map files
    # behind. The rule that does not need maintaining is "anything named
    # interposer_<otherdigest>_<tag>.*", whatever the toolchain decides
    # to emit next.
    import glob as _glob
    d = os.path.dirname(bin_path)
    stem = os.path.basename(os.path.splitext(bin_path)[0])
    for old in _glob.glob(os.path.join(d, "interposer_*")):
        if not os.path.basename(old).startswith(stem + "."):
            try:
                os.remove(old)
            except OSError:
                pass
    env_name = env.subst("$PIOENV")
    base = os.path.basename(bin_path)

    # RE-CHECK THE DIGEST AGAINST THE TREE, AFTER THE LINK.
    #
    # Added 2026-10-05. Until then the only verification of src_digest
    # was digest_of(base), which reads the digest back out of the
    # filename the pre-action wrote -- so it compared the name with
    # itself and could not fail. build_check.py's cross-environment
    # comparison has the same blind spot: it proves all six images agree
    # with each other, not that any of them agrees with the tree.
    #
    # What this catches that the pre-action cannot: a source edited
    # after build_name.py computed the name but before the link
    # finished. The window is one build long -- about 12 seconds here --
    # but this project lives on a drive that syncs across machines, so
    # the tree can change underneath a build without anybody typing
    # anything. The image would then carry a name asserting sources it
    # was not built from, which is the one thing the digest exists to
    # prevent.
    #
    # Raising here fails the build rather than recording a warning
    # nobody reads. A mismatched image must not reach a flash command.
    fw_dir = env.subst("$PROJECT_DIR")
    tree_digest = source_digest(fw_dir)[:DIGEST_CHARS]
    name_digest = digest_of(base)
    if name_digest != tree_digest:
        raise SystemExit(
            "manifest.py: SOURCE DIGEST MISMATCH -- %s carries %s but the "
            "tree now hashes to %s. A source changed between the start of "
            "this build and its link, so the image's name asserts sources "
            "it was not built from. Rebuild; do not flash this image."
            % (base, name_digest, tree_digest))

    with open(bin_path, "rb") as f:
        blob = f.read()

    entry = {
        "image": base,
        "env": env_name,
        "tag": tag_for(env_name),
        "src_digest": digest_of(base),
        # Beside src_digest, never instead of it (8.2). The digest
        # answers which sources; the commit answers where to find
        # them again, and only if git_dirty is false.
        "git_commit": None,
        "git_dirty": None,
        "elf_sha256": elf_sha256_from_esptool(bin_path),
        "intp_fw_ver16": fw_ver16(),
        "md5": hashlib.md5(blob).hexdigest(),
        "size": len(blob),
        "built_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toolchain": toolchain(),
    }
    # What 0x7F7 B0-B3 will actually carry, spelled out rather than
    # left for a reader to slice off the full hash and hope they took
    # it from the right end.
    entry.update(git_identity(fw_dir))
    if entry.get("git_commit") is None:
        entry.pop("git_commit", None)
    if entry.get("git_dirty") is None:
        entry.pop("git_dirty", None)
    if entry["elf_sha256"]:
        entry["build_id_0x7F7"] = entry["elf_sha256"][:8]

    d = os.path.join(env.subst("$PROJECT_DIR"), MANIFEST_DIR)
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, MANIFEST)

    entries = []
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                entries = json.load(f).get("builds", [])
        except (IOError, ValueError):
            entries = []

    entries = merge_entry(entries, entry)

    # The newline argument is set to LF EXPLICITLY. Without it Python's
    # text mode writes CRLF on Windows, and the repo pins LF
    # (charge-interposer/.gitattributes, user ruling 2026-10-08), so every
    # build left this file differing from its blob in line endings alone:
    # git warned on each add, and the one file the build is supposed to
    # keep authoritative looked dirty for a reason that had nothing to do
    # with its contents. A reader asking whether a build had changed the
    # manifest could not tell that from the noise.
    #
    # The rows themselves are unaffected: json.dump with indent=2 and
    # sort_keys=True is deterministic, so re-writing the same entries
    # reproduces the same text and only the line endings change.
    with open(path, "w", newline="\n") as f:
        json.dump({
            "comment": "Generated by firmware/manifest.py on every build. "
                       "See builds/README.md. elf_sha256 is what the board "
                       "reports in 0x7F7 B0-B3 (first 4 bytes).",
            "builds": entries,
        }, f, indent=2, sort_keys=True)
        f.write("\n")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", record)   # noqa: F821
