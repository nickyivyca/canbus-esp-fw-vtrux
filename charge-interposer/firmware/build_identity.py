"""Which image filename belongs to which environment.

Shared by `build_name.py`, which sets the name at build time, and
`test/build_check.py`, which asserts it afterwards. IMPORTED BY BOTH,
never copied into either: a second copy of this table would drift, and
the failure would be a check that agrees with a stale idea of the
truth rather than with the build. (The generator-inhibit project hit
exactly that with a duplicated offline-test helper.)

The names look like `interposer_<srcdigest12>_<tag>.bin`. See
`build_name.py` for how the digest is computed and why there is no git
sha in it -- this project is not a git repository.
"""

# WHAT THE SOURCE DIGEST COVERS. Stated here because this is the file
# both the build and the check import, and because a digest whose
# inputs are guessed at is worse than none -- someone will later assume
# it covers something it does not.
#
# Hashed, by path relative to firmware/ and by content, sorted:
#   - every .c/.cpp/.h/.hpp/.S under src/
#   - platformio.ini        (a build flag changes the image with no
#                            source edit -- that IS the difference
#                            between the bridge and witness builds)
#   - build_name.py         (decides the name)
#   - build_identity.py     (this file: the tag table)
#   - manifest.py           (the other extra_script)
#
# manifest.py only WRITES a json file and cannot change the image, but
# it is hashed anyway: excluding it would rest on having read its code
# rather than on anything structural, and a post-action is exactly the
# kind of hook that could start touching the image later.
#
# NOT hashed, and deliberately: docs, tests, notes, anything under
# .pio. None of them can change the image, and including them would
# churn the digest on every comment edit.
#
# The digest answers "which sources produced this". It does NOT
# identify the image: the same sources under a different toolchain or
# platform version give different bytes. The ELF SHA-256 -- which the
# board reports in 0x7F7 B0-B3 -- answers that, and the build manifest
# records both along with the toolchain versions.
DIGEST_INPUTS = ("src/*.{c,cpp,h,hpp,S}", "platformio.ini",
                 "build_name.py", "build_identity.py", "manifest.py",
                 "reproducible.py")

# The two halves of DIGEST_INPUTS, in the form the walk needs.
SRC_SUFFIXES = (".c", ".cpp", ".h", ".hpp", ".S")
EXTRA_INPUTS = ("platformio.ini", "build_name.py", "build_identity.py",
                "manifest.py", "reproducible.py")


def source_digest(fw_dir):
    """SHA-256 over the sources that shape the image -> hex.

    LIVES HERE, NOT IN build_name.py, since 2026-10-05, so that the
    pre-action that NAMES the image and the post-action that VERIFIES it
    cannot compute the digest two different ways. A verifier with its own
    copy of the rule would eventually agree with a stale idea of the rule
    rather than with the build -- the same reason the TAGS table is
    imported by both users instead of copied.

    Note it hashes BYTES, so a line-ending change counts. That is correct
    and it is not merely theoretical: on 2026-10-05 a revert script that
    read a source in text mode and wrote it back with newline="\\n"
    converted machine.h's 594 CRLF endings to LF, the C++ was unaffected,
    the build succeeded, and the digest moved from 2e9cd83d4967 to
    7c32d27600b4. Nothing else in the build would have reported that;
    restoring CRLF restored the digest exactly, which is what identified
    the cause.
    """
    import hashlib
    import os
    h = hashlib.sha256()
    paths = []
    src = os.path.join(fw_dir, "src")
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(SRC_SUFFIXES):
                paths.append(os.path.join(root, f))
    for extra in EXTRA_INPUTS:
        q = os.path.join(fw_dir, extra)
        if os.path.exists(q):
            paths.append(q)
    # Sorted by path RELATIVE to fw_dir, so the digest does not depend on
    # where the project is checked out -- it differs between machines
    # otherwise, and this project is on a drive that syncs across several.
    paths.sort(key=lambda p: os.path.relpath(p, fw_dir).replace(os.sep, "/"))
    for p in paths:
        rel = os.path.relpath(p, fw_dir).replace(os.sep, "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with open(p, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


DIGEST_CHARS = 12

# Variant tag per PlatformIO environment.
#
# An UNKNOWN ENVIRONMENT IS A HARD FAILURE in both users of this table,
# never a default. Adding an environment should force a decision about
# what its image is called; defaulting would quietly reintroduce the
# filename collision the tagging exists to prevent.
TAGS = {
    "esp32-can-x2": "bridge",
    "esp32-can-x2-witness": "witness",
    "truck": "truck",
    "selftest": "selftest",
    "diag": "diag",
    "slcan": "slcan",
}

# The tag that marks an image carrying the completion-order witness.
# Exactly one environment may produce it.
WITNESS_TAG = "witness"

PREFIX = "interposer_"


def tag_for(env_name):
    """The variant tag for `env_name`, or raise."""
    try:
        return TAGS[env_name]
    except KeyError:
        raise KeyError(
            "no image-name tag for environment %r -- add it to TAGS in "
            "firmware/build_identity.py" % env_name)


def name_glob(env_name):
    """Glob matching the one image `env_name` should have produced."""
    return "%s*_%s.bin" % (PREFIX, tag_for(env_name))


def digest_of(filename):
    """The source digest embedded in an image filename, or None.

    `interposer_f5798a13b4fa_witness.bin` -> `f5798a13b4fa`.
    """
    base = filename[:-4] if filename.endswith(".bin") else filename
    if not base.startswith(PREFIX):
        return None
    rest = base[len(PREFIX):]
    if "_" not in rest:
        return None
    return rest.rsplit("_", 1)[0]
