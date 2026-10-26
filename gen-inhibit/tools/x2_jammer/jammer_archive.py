# Vtrux X2 JAMMER -- TEST EQUIPMENT, NOT FOR VEHICLE.
#
# PlatformIO post-script: archive every built jammer image, keyed by its md5.
#
# After the app .bin is linked, copies it, its .elf, the bootloader and the
# partition table into builds/<md5 of app bin>/ and appends one row to
# builds/manifest.json. builds/ is in SeaDrive, not in this repo (goal-20 move, 2026-10-22):
# $REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/, and an
# unset REVERSE_IT_DIR stops the build's archive step. Rows are appended, never replaced, and the key is the
# image's own md5, so two builds of the same sources (same name, different
# bytes -- these builds are not reproducible) each keep their row.
#
# elf_sha256 is the sha256 of the .elf FILE, which is what esptool writes into
# the app descriptor and what `esptool image-info` prints as the ELF SHA256.
import datetime
import hashlib
import json
import os
import shutil

Import("env")  # noqa: F821  (SCons)


def _hash(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def builds_dir():
    v = os.environ.get("REVERSE_IT_DIR", "").strip()
    if not v:
        raise SystemExit("REVERSE_IT_DIR is not set: the jammer's builds live in SeaDrive, "
                         "projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/")
    return os.path.join(v, "projects", "vtrux", "notes", "artifacts", "gen-inhibit", "x2_jammer", "builds")


def archive(source, target, env):
    build = env.subst("$BUILD_DIR")
    prog = env.subst("$PROGNAME")
    proj = env.subst("$PROJECT_DIR")
    app = os.path.join(build, prog + ".bin")
    elf = os.path.join(build, prog + ".elf")
    md5 = _hash(app, "md5")
    out = os.path.join(builds_dir(), md5)
    os.makedirs(out, exist_ok=True)
    files = {"app": app, "elf": elf,
             "bootloader": os.path.join(build, "bootloader.bin"),
             "partitions": os.path.join(build, "partitions.bin"),
             "boot_app0": os.path.join(
                 env.PioPlatform().get_package_dir("framework-arduinoespressif32"),
                 "tools", "partitions", "boot_app0.bin")}
    copied = {}
    for k, p in files.items():
        if os.path.exists(p):
            shutil.copy2(p, out)
            copied[k] = os.path.basename(p)
    # Flash layout, as PlatformIO's own upload writes it for this board.
    offsets = {"bootloader": "0x0", "partitions": "0x8000",
               "boot_app0": "0xe000", "app": "0x10000"}
    row = {"built": datetime.datetime.now().isoformat(timespec="seconds"),
           "progname": prog,
           "app_md5": md5,
           "app_sha256": _hash(app, "sha256"),
           "elf_sha256": _hash(elf, "sha256"),
           "dir": "builds/" + md5,
           "files": copied,
           "flash_offsets": {k: offsets[k] for k in copied if k in offsets},
           "NOT_FOR_VEHICLE": True}
    man = os.path.join(builds_dir(), "manifest.json")
    rows = []
    if os.path.exists(man):
        with open(man, encoding="ascii") as fh:
            rows = json.load(fh)
    rows.append(row)
    with open(man, "w", encoding="ascii") as fh:
        json.dump(rows, fh, indent=1)
    print("JAMMER archived %s md5 %s elf_sha256 %s" % (prog, md5,
                                                       row["elf_sha256"]))


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", archive)  # noqa: F821
