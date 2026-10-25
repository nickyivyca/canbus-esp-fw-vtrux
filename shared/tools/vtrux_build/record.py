"""The release build record (SPEC R13).

One record per release build, named by its image hash, with the .elf and
.bin beside it, in firmware/builds/<project>/ under $REVERSE_IT_DIR:

    <image hash>.bin    the image, as the build made it
    <image hash>.elf    its ELF, whose SHA-256 is the image hash (checked)
    <image hash>.json   the record

Never edited after it is written. Each file is written with exclusive
create, the .json last, so a .json on disk means its .bin and .elf were
written in full. Writing the same build again (the same image hash, so the
same ELF) writes nothing new: if the files on disk hold the same bytes, the
existing record stands and is returned; if they do not, RecordError, and
nothing is touched.

The record's fields (RECORD_SCHEMA 1):

    schema, kind, project, written (local time, ISO 8601)
    commit                    the project worktree's HEAD, 40 hex digits
    image_hash, code_hash     R1, R2
    image_size                bytes in the .bin
    image                     {"bin", "elf"}: the file names here, and
                              "built_as": the .bin's name in build/
    host_os                   {"system", "release", "version", "machine"}
    toolchain                 {"compiler": its file name, "version": the
                              first line of its --version}
    idf_version               {"project": project_description.json's
                              git_revision, "image": the descriptor's idf_ver}
    components                {name: {"dir", "digest"}}: every component the
                              build used (R7)
    dependencies_lock_sha256_lf
                              SHA-256 of dependencies.lock with its CRLF
                              line endings normalised to LF, so one lock
                              gives one hash on any host and checkout
    shared_components         {name: {"version", "git", "path"} from the
                              project's idf_component.yml, the pinned tag
                              being "version", and "lock_version", the
                              commit the lock resolved it to}
    tooling                   {"tag", "commit", "dirty", "digest"} of the
                              shared/tools checkout this ran from; tag is
                              null when HEAD has no tag
    environment_passed        R24: the names, never the values, the build
                              took from its caller's environment, sorted
    release_checks            what the build script checked on the way, for
                              the reader: "managed_components" (R10's
                              report)
"""

import json
import os
import platform
import shutil
import time

from .image import AppImage, file_sha256

RECORD_SCHEMA = 1
KIND = "release build record (shared/tools SPEC R13)"


class RecordError(RuntimeError):
    pass


def builds_dir(project):
    """firmware/builds/<project>/ under $REVERSE_IT_DIR. RecordError if the
    variable is unset or its firmware/ folder is missing: the record has
    nowhere to go, and a folder tree is not created at a guessed path."""
    root = os.environ.get("REVERSE_IT_DIR")
    if not root:
        raise RecordError("REVERSE_IT_DIR is not set, so a release build has "
                          "nowhere to write its record (R13)")
    fw = os.path.join(root, "firmware")
    if not os.path.isdir(fw):
        raise RecordError("REVERSE_IT_DIR is %s, which has no firmware/ "
                          "folder" % root)
    return os.path.join(fw, "builds", project)


def host_os():
    return {"system": platform.system(), "release": platform.release(),
            "version": platform.version(), "machine": platform.machine()}


def _same_bytes(path, data):
    with open(path, "rb") as f:
        return f.read() == data


def _write_new(path, data):
    with open(path, "xb") as f:
        f.write(data)


def write(project, bin_path, elf_path, fields, dest=None):
    """Write the record for one release build and return its .json path.
    fields holds every record field except schema, kind, project, written,
    image_hash, code_hash, image_size and image, which come from here.
    dest defaults to builds_dir(project)."""
    with open(bin_path, "rb") as f:
        bin_data = f.read()
    img = AppImage(bin_data)
    ihash = img.image_hash
    elf_sha = file_sha256(elf_path)
    if elf_sha != ihash:
        raise RecordError("%s has SHA-256 %s, and the image embeds %s: the "
                          "ELF beside the image is not the one it was made "
                          "from" % (os.path.basename(elf_path), elf_sha, ihash))
    with open(elf_path, "rb") as f:
        elf_data = f.read()

    dest = dest or builds_dir(project)
    os.makedirs(dest, exist_ok=True)
    paths = {ext: os.path.join(dest, ihash + ext)
             for ext in (".bin", ".elf", ".json")}

    if os.path.exists(paths[".json"]):
        for ext, data in ((".bin", bin_data), (".elf", elf_data)):
            if not (os.path.isfile(paths[ext])
                    and _same_bytes(paths[ext], data)):
                raise RecordError("%s exists, and its %s is missing or holds "
                                  "other bytes than this build's; nothing "
                                  "written" % (paths[".json"], ext))
        return paths[".json"]

    for ext, data in ((".bin", bin_data), (".elf", elf_data)):
        p = paths[ext]
        if os.path.exists(p):
            # Left by a write that stopped before its .json: keep it only
            # if it is this build's file.
            if not _same_bytes(p, data):
                raise RecordError("%s exists with other bytes and no record; "
                                  "nothing written" % p)
            continue
        _write_new(p, data)

    rec = {
        "schema": RECORD_SCHEMA,
        "kind": KIND,
        "project": project,
        "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "image_hash": ihash,
        "code_hash": img.code_hash,
        "image_size": len(bin_data),
        "image": {"bin": os.path.basename(paths[".bin"]),
                  "elf": os.path.basename(paths[".elf"]),
                  "built_as": os.path.basename(bin_path)},
    }
    clash = set(rec) & set(fields)
    if clash:
        raise RecordError("record fields given twice: %s" % sorted(clash))
    rec.update(fields)
    text = json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    _write_new(paths[".json"], text.encode("ascii"))
    return paths[".json"]


def read(path):
    """A record, as written."""
    with open(path, encoding="ascii") as f:
        return json.load(f)


def toolchain(c_compiler, run):
    """{"compiler", "version"} for the compiler project_description.json
    names. run(argv) returns (returncode, stdout). RecordError if it cannot
    be read: the record needs it."""
    if not c_compiler or not (os.path.isfile(c_compiler)
                              or shutil.which(c_compiler)):
        raise RecordError("the C compiler %r cannot be found, so the record's "
                          "toolchain version cannot be read" % c_compiler)
    rc, out = run([c_compiler, "--version"])
    first = (out or "").strip().splitlines()[:1]
    if rc != 0 or not first:
        raise RecordError("%s --version failed (exit %s)" % (c_compiler, rc))
    return {"compiler": os.path.basename(c_compiler), "version": first[0]}
