# Vtrux X2 JAMMER -- TEST EQUIPMENT, NOT FOR VEHICLE.
#
# PlatformIO pre-script: names the image after a digest of its sources.
#
# This folder was not in git until 2026-10-22, so the source digest stood in for
# a commit, and it still names the image: a
# sha256 over platformio.ini, both build scripts and every file in src/, in
# sorted order, each as "<relpath>\0<bytes>\0". The image is named
# vtrux_jammer_x2_NOT_FOR_VEHICLE_<digest12>.bin and the full digest is
# compiled in as JAMMER_SRC_DIGEST, so the boot banner names its sources.
#
# The digest says WHICH SOURCES, not which image: ESP builds here are not
# reproducible (tools-reference.md, PlatformIO notes). The image is
# identified by its md5 / ELF sha256, recorded by jammer_archive.py.
import hashlib
import os

Import("env")  # noqa: F821  (SCons)

PROJ = env.subst("$PROJECT_DIR")  # noqa: F821


def source_digest(proj):
    files = ["platformio.ini", "jammer_name.py", "jammer_archive.py"]
    src = os.path.join(proj, "src")
    for root, _dirs, names in os.walk(src):
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), proj))
    h = hashlib.sha256()
    for rel in sorted(f.replace("\\", "/") for f in files):
        with open(os.path.join(proj, rel), "rb") as fh:
            h.update(rel.encode() + b"\0" + fh.read() + b"\0")
    return h.hexdigest()


digest = source_digest(PROJ)
env.Replace(PROGNAME="vtrux_jammer_x2_NOT_FOR_VEHICLE_%s" % digest[:12])  # noqa: F821
env.Append(CPPDEFINES=[("JAMMER_SRC_DIGEST", '\\"%s\\"' % digest)])  # noqa: F821
print("JAMMER source digest %s" % digest)
