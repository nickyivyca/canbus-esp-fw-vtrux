"""Give every environment's image a distinct, self-describing filename.

PlatformIO writes `.pio/build/<env>/firmware.bin` for every environment,
so six images differ only by their parent directory. That is the same
trap the generator-inhibit project hit and fixed: its shipping and
auto-arm images were both `wican-fw_obd_<sha>.bin`, told apart only by
directory and 112 bytes, and the fix was to put the variant in the name
(`..._autoarm.bin`). The witness image makes it urgent here -- a board
carrying measurement instrumentation must not be confusable with the
bench bridge by a filename.

Output name:  interposer_<srcdigest>_<tag>.bin

THERE IS NO GIT SHA IN THAT NAME, and the omission is deliberate rather
than an oversight, though the reason has changed.

*Corrected 2026-10-08.* This paragraph used to say the omission was
forced: "This project is not a git repository -- the containing folder
is literally `NotGit`, it lives on SeaDrive, and `git rev-parse` fails
at every level. So there is no commit to record." That was true when
written and is not any more: the tree moved into
`canbus-esp-fw-vtrux` as `charge-interposer/` on 2026-10-08, and there
is now both a commit and a committed baseline to call a tree dirty
against. manifest.py records both, per spec 8.2.

THE NAME STILL CARRIES THE DIGEST AND NOT THE COMMIT, now by choice.
Spec 8.2 puts the commit "beside the source digest, not in its place:
a build with uncommitted changes shares its commit with one that has
none." A commit does not identify the sources of a build made from a
working tree, and most builds here are; the digest does, saved or not.
So the name answers which sources, and the manifest row adds where to
find them again.

What the name carries is a digest of the sources that actually went in --
every file under `src/` plus `platformio.ini`, hashed by name and
content. That is strictly what a commit id is wanted FOR here: telling
two images apart and saying which sources produced one. Two builds of
the same sources give the same digest; any edit, saved or not, changes
it.

The authoritative identity of a built image is still its ELF SHA-256,
which is what the board reports in 0x7F7 B0-B3 and what the build
manifest records. This name is for humans and for flash commands.
"""

import sys

Import("env")                                             # noqa: F821

# The tag table lives in build_identity.py and is IMPORTED, not copied:
# test/build_check.py asserts against the same table, and a second copy
# would drift into a check that agrees with a stale idea of the truth.
# SCons does not put the project directory on sys.path.
sys.path.insert(0, env.subst("$PROJECT_DIR"))             # noqa: F821
from build_identity import (PREFIX, DIGEST_CHARS,         # noqa: E402
                             source_digest, tag_for)

# WHAT COUNTS AS "THE SOURCES" and how it is hashed both live in
# build_identity.py, which is imported by this pre-action AND by
# manifest.py's post-link verifier. They were separate copies until
# 2026-10-05; a verifier with its own copy of the rule eventually agrees
# with a stale idea of the rule rather than with the build.

fw_dir = env.subst("$PROJECT_DIR")
env_name = env.subst("$PIOENV")

try:
    tag = tag_for(env_name)
except KeyError as exc:
    raise SystemExit("build_name.py: %s" % exc)

digest = source_digest(fw_dir)[:DIGEST_CHARS]
env.Replace(PROGNAME="%s%s_%s" % (PREFIX, digest, tag))
