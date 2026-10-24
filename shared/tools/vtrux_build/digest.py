"""Component digest (SPEC R7).

A component's digest covers the content of every file under the component's
root, with each file's path relative to that root, written with "/" on every
host OS, so one tree gives one digest on Windows and on Linux. It leaves out,
in any folder, files whose name ends ".md" and files named ".gitignore",
matched case-sensitively. Every other file is counted, ".markdown" and
"README.MD" included. It never covers a git ref, so the same files give the
same digest whether they came from a tag, a commit or a working tree, and at
whatever folder the component sits.

Exactly what is hashed:

    files    every regular file under root, found by os.walk (which does not
             follow symlinked folders; a symlinked file is read through,
             and a broken one, or any other entry that is not a regular
             file, raises OSError rather than being left out)
    left out a file whose name ends ".md" or is ".gitignore", in any folder
             (the name is matched exactly, so README.MD is counted)
    path     relative to root, with "/" separators, UTF-8
    order    sorted by that path's UTF-8 bytes
    digest   SHA-256 over, for each file in order: path, NUL, the file's
             bytes, NUL; as 64 hex digits

Nothing else is left out. A file the build or a tool writes into the folder,
a __pycache__ for example, is counted like any other: run Python tooling from
a component folder with -B (or PYTHONDONTWRITEBYTECODE=1) if its digest
matters.
"""

import hashlib
import os

EXCLUDED_NAMES = {".gitignore"}
EXCLUDED_SUFFIXES = (".md",)


def excluded(name):
    return name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES)


def component_files(root):
    """[(relative path with "/", absolute path)] that the digest covers,
    in digest order."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for fn in files:
            if excluded(fn):
                continue
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                # A broken symlink or a special file: content unknown, so
                # no digest rather than one that silently leaves it out.
                raise OSError("not a readable regular file: %s" % full)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    out.sort(key=lambda t: t[0].encode("utf-8"))
    return out


def component_digest(root):
    """R7 digest of the component whose root folder is root."""
    h = hashlib.sha256()
    for rel, full in component_files(root):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()
