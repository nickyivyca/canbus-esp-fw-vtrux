"""Make the image depend only on its sources and the pinned toolchain.

Spec 8.2: "The image depends only on its sources and the toolchain: the
same sources built with the same pinned platform version, in any folder
on the same host OS, give the same ELF and the same id. [...] A rebuild
on the same host OS that does not reproduce the id is a build defect."

ON THE SAME HOST OS -- those words were added to the spec on 2026-10-08,
after this file had been measured across two of them, and they are the
limit of what it can deliver. The platform ships a prebuilt toolchain
per host OS, and each one records its own internal paths and header
layout in the ELF's debug information; -ffile-prefix-map cannot reach
those, because they were baked in before the toolchain arrived here.
Commit 3eb9a8f built on NICKY-XPS and on madhouse-debian gave two ELFs
differing only in the seven .debug_* sections -- every loaded section
identical, and `objcopy --strip-debug` of both giving one file -- and
two .bin files differing in 65 of 355,040 bytes: the ELF hash at 0xb0
plus the one-byte checksum and appended SHA-256 that cover it. Because
the id hashes the whole ELF, it differs too.

The user ruled that such a build gets its own manifest row and that the
id is never patched to match, so there is nothing here to fix. A build
on another host OS that differs anywhere ELSE is a build defect.

Two things made the ELF differ on every build until 2026-10-08, and they
are independent:

  1. THE ARDUINO CORE'S COMPILE DATE/TIME STRING. The prebuilt Arduino
     core embeds __DATE__/__TIME__, so the same sources built a minute
     apart differed. GCC honours SOURCE_DATE_EPOCH for __DATE__ and
     __TIME__, so fixing it to a constant fixes that string.

     IT IS A CONSTANT AND NOT THE COMMIT TIME, which the spec requires
     explicitly. Using the commit time would make the image depend on
     when the tree was committed rather than on what is in it, so the
     same sources would build differently before and after a rebase,
     amend or cherry-pick -- a rebuild that does not reproduce the id,
     which 8.2 calls a build defect.

  2. THE BUILD FOLDER, recorded in the ELF's debug information. Two
     clones at different paths produced different ELFs for identical
     sources. -ffile-prefix-map rewrites those paths to a fixed stem.

SETTING SOURCE_DATE_EPOCH THROUGH os.environ, NOT env. SCons spawns the
compiler as a child process, and it inherits the interpreter's
environment rather than this SCons Environment's dictionary, so setting
it on `env` alone does not reach gcc reliably (reviewer's scratch test,
2026-10-08).

BOTH ENVIRONMENTS, AND BOTH PATH SPELLINGS. The flags go on `env` and on
DefaultEnvironment(): the Arduino framework's own sources are compiled
in the default environment, so flags added only to `env` leave the
framework objects carrying absolute paths. Each path is mapped in its
backslash and forward-slash forms, because the toolchain sees Windows
paths in both spellings depending on who passed them, and a map that
matches only one spelling silently does nothing for the other -- the
failure would look exactly like success, since the build still works and
only the embedded strings differ.

WHY THIS IS A SEPARATE FILE rather than more lines in build_name.py:
that script answers "what is this image called", this one answers "what
makes two builds identical". It is listed in build_identity.py's
DIGEST_INPUTS, so editing it moves src_digest -- which is correct, since
changing these flags changes the bytes.
"""

import os

Import("env")                                             # noqa: F821

# 2026-01-01T00:00:00Z. Any fixed value satisfies 8.2; this one is a round
# date near the start of the project's firmware work, and it is recorded
# here rather than computed so that nothing about the machine or the
# moment of building can move it.
SOURCE_DATE_EPOCH = "1767225600"


def prefix_map_flags(paths):
    """-ffile-prefix-map for each path, in both spellings, longest first.

    Longest first because GCC applies the first matching -ffile-prefix-map
    and the packages directory can sit inside the home directory: a
    shorter prefix listed first would win and leave the longer one with
    nothing to do.

    Duplicates are dropped rather than emitted twice: on a POSIX machine
    the two spellings of a path are the same string, and a flag list that
    repeats itself is harder to read in a build log than one that does
    not.
    """
    out, seen = [], set()
    for p in sorted(paths, key=len, reverse=True):
        if not p:
            continue
        for form in (p.replace("/", os.sep), p.replace(os.sep, "/")):
            flag = "-ffile-prefix-map=%s=." % form
            if flag not in seen:
                seen.add(flag)
                out.append(flag)
    return out


os.environ["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH

project_dir = env.subst("$PROJECT_DIR")                   # noqa: F821
packages_dir = env.subst("$PROJECT_PACKAGES_DIR")         # noqa: F821

flags = prefix_map_flags([project_dir, packages_dir])

env.Append(CCFLAGS=flags, ASFLAGS=flags)                  # noqa: F821
try:
    default_env = DefaultEnvironment()                    # noqa: F821
except Exception:
    default_env = None
if default_env is not None and default_env is not env:    # noqa: F821
    default_env.Append(CCFLAGS=flags, ASFLAGS=flags)

print("reproducible.py: SOURCE_DATE_EPOCH=%s, %d prefix-map flag(s)"
      % (SOURCE_DATE_EPOCH, len(flags)))
