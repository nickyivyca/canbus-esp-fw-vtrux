"""R21, board data: the partition table, the application slot, the flash size and the tool prefix.

    python -B -m vtrux_checks board --build <project>/build

SPEC R21 as worded in a5e7c46 (shared/tools/spec-r21; the flash size: user,
2026-10-24, option B), with the gen-inhibit reviewer's readings of
2026-10-24. The board data is the "board" object in vtrux_checks.json in the
project root (project_description.json's project_path), for a board only
this project builds for (R22: the WiCAN):

    {"board": {"target": "<IDF_TARGET>",
               "partition_table": "<the reference partition-table .bin, from the project root>",
               "flash_size": "<the board's flash size, as ESP-IDF names it: 4MB>"}}

A board named by a string instead (R22: a board more than one project
builds for, the X2) comes from a shared board component pinned in
dependencies.lock, found as boards/<name>/ under managed_components/
(reading Q7 (a)). That form is not built yet, since no board component
exists until the interposer's port step 2a; the check refuses it, naming
that.

It fails when:

  - the build's own partition table (the file flasher_args.json names as
    "partition-table", under build/) is not byte-identical to the board's
    reference table (Q1 (a), Q2 (a): git holds the reference; no stored hash);
  - the app image (project_description.json's app_bin) is larger than the
    board's application slot, which is the smallest app partition in the
    reference table, ota_0 and ota_1 alike (Q3 (b)); a reference table that
    cannot be read or has no app partition fails as malformed;
  - the flash size in the header of the app image or of the bootloader image
    (the file flasher_args.json names as "bootloader") is not the board's;
  - the build's CONFIG_IDF_TARGET (build/config/sdkconfig.json) is not the
    board's target: its tools would be the wrong ones (Q4);
  - ESP-IDF names no toolchain for the board's target, or no flash size by
    the board's name.

The tool prefix comes from the board's target through ESP-IDF itself: the
_CMAKE_TOOLCHAIN_PREFIX that the build's ESP-IDF (project_description.json's
idf_path) sets in tools/cmake/toolchain-<target>.cmake (Q4 (b); R22: no chip
fact in the checks or the board data that IDF already holds). tool() is the
one place a check names a tool such as nm; no check names one otherwise.
The flash size is read the same way: the image header's magic byte and the
values of its flash size field (the high four bits of byte 3) are the ones
that ESP-IDF's components/bootloader_support/include/esp_app_format.h
defines, ESP_IMAGE_HEADER_MAGIC and esp_image_flash_size_t.

Release builds only; any session may run it. The check does not tell them
apart: run it on a finished release build.
"""

import json
import os
import re

from vtrux_build.builddir import BuildDir, BuildDirError

from . import parttable

DATA_FILE = "vtrux_checks.json"
KEY = "board"
KEYS = ("target", "partition_table", "flash_size")
_PREFIX = re.compile(r"^\s*set\(\s*_CMAKE_TOOLCHAIN_PREFIX\s+(\S+?)\s*\)", re.M)
_TARGET = re.compile(r"^[a-z0-9]+$")
APP_FORMAT = ("components", "bootloader_support", "include", "esp_app_format.h")
_SIZE_ENUM = re.compile(r"typedef\s+enum\s*\{([^}]*)\}\s*esp_image_flash_size_t\s*;")
_MAGIC = re.compile(r"^\s*#define\s+ESP_IMAGE_HEADER_MAGIC\s+(0x[0-9A-Fa-f]+|\d+)\b", re.M)
_ENUMERATOR = re.compile(r"^ESP_IMAGE_FLASH_SIZE_(\w+?)\s*(?:=\s*(0x[0-9A-Fa-f]+|\d+))?$")
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


class BoardError(ValueError):
    pass


def read_board(project):
    """(target, reference table path, flash size name) from the project's vtrux_checks.json."""
    p = os.path.join(project, DATA_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise BoardError("%s cannot be read (%s)" % (DATA_FILE, e))
    b = data.get(KEY) if isinstance(data, dict) else None
    if isinstance(b, str):
        raise BoardError("%s names board %r: a named board comes from a shared board component in "
                         "managed_components/, which is not built yet" % (DATA_FILE, b))
    if not isinstance(b, dict):
        raise BoardError("%s has no %r object" % (DATA_FILE, KEY))
    if set(b) != set(KEYS):
        raise BoardError("%s %r: needs exactly %s, has %s" % (DATA_FILE, KEY, ", ".join('"%s"' % k for k in KEYS), sorted(b)))
    t, ref, flash = b["target"], b["partition_table"], b["flash_size"]
    if not isinstance(t, str) or not _TARGET.match(t):
        raise BoardError("%s %r: target %r is not an IDF_TARGET name" % (DATA_FILE, KEY, t))
    if not isinstance(ref, str) or not ref or os.path.isabs(ref):
        raise BoardError("%s %r: partition_table %r is not a path from the project root" % (DATA_FILE, KEY, ref))
    if not isinstance(flash, str) or not flash:
        raise BoardError("%s %r: flash_size %r is not a size name" % (DATA_FILE, KEY, flash))
    return t, os.path.normpath(os.path.join(project, ref)), flash


def tool_prefix(idf_path, target):
    """The toolchain prefix ESP-IDF sets for target, e.g. riscv32-esp-elf-."""
    p = os.path.join(idf_path, "tools", "cmake", "toolchain-%s.cmake" % target)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            m = _PREFIX.findall(f.read())
    except OSError as e:
        raise BoardError("ESP-IDF has no toolchain for target %r (%s)" % (target, e))
    if len(m) != 1:
        raise BoardError("%s sets _CMAKE_TOOLCHAIN_PREFIX %d times" % (p, len(m)))
    return m[0]


def tool(idf_path, target, name):
    """A tool of the board's toolchain by name: tool(idf, "esp32c3", "nm") -> riscv32-esp-elf-nm."""
    return tool_prefix(idf_path, target) + name


def image_format(idf_path):
    """(magic, {flash size field value: size name}) from ESP-IDF's esp_app_format.h:
    ESP_IMAGE_HEADER_MAGIC, and esp_image_flash_size_t's enumerators less ESP_IMAGE_FLASH_SIZE_MAX,
    valued as C values them (one more than the one before, unless given)."""
    p = os.path.join(idf_path, *APP_FORMAT)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = _COMMENT.sub("", f.read())
    except OSError as e:
        raise BoardError("ESP-IDF's esp_app_format.h cannot be read (%s)" % e)
    magic, enum = _MAGIC.findall(text), _SIZE_ENUM.findall(text)
    if len(magic) != 1 or len(enum) != 1:
        raise BoardError("%s defines ESP_IMAGE_HEADER_MAGIC %d times and esp_image_flash_size_t %d times"
                         % (p, len(magic), len(enum)))
    sizes, value = {}, -1
    for e in (x.strip() for x in enum[0].split(",")):
        if not e:
            continue
        m = _ENUMERATOR.match(e)
        if not m:
            raise BoardError("%s: esp_image_flash_size_t has an enumerator %r this check cannot read" % (p, e))
        value = int(m.group(2), 0) if m.group(2) else value + 1
        if m.group(1) != "MAX":
            sizes[value] = m.group(1)
    if not sizes or len(set(sizes.values())) != len(sizes):
        raise BoardError("%s: esp_image_flash_size_t names no flash sizes, or one twice" % p)
    return int(magic[0], 0), sizes


def header_flash_size(path, magic, sizes):
    """The flash size an image's header gives: its name, or "unknown value N"."""
    with open(path, "rb") as f:
        h = f.read(4)
    if len(h) < 4 or h[0] != magic:
        raise BoardError("%s is not an ESP-IDF image (first byte %s, ESP_IMAGE_HEADER_MAGIC 0x%02X)"
                         % (path, "0x%02X" % h[0] if h else "missing", magic))
    return sizes.get(h[3] >> 4, "unknown value %d" % (h[3] >> 4))


def slot(reference):
    """The board's application slot: the smallest app partition in its reference table, in bytes."""
    try:
        with open(reference, "rb") as f:
            entries = parttable.read(f.read())
    except OSError as e:
        raise BoardError("the reference partition table cannot be read (%s)" % e)
    except parttable.TableError as e:
        raise BoardError("the reference partition table is malformed (%s)" % e)
    apps = parttable.apps(entries)
    if not apps:
        raise BoardError("the reference partition table is malformed (no app partition)")
    return min(size for _l, _s, _o, size in apps), apps


def flasher_file(bd, key):
    """The file flasher_args.json names under key, e.g. "partition-table" or "bootloader"."""
    try:
        with open(bd.file("flasher_args.json"), encoding="utf-8") as f:
            rel = json.load(f)[key]["file"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BoardError("build/flasher_args.json names no %s file (%s)" % (key, e))
    p = bd.resolve(rel)
    if not os.path.isfile(p):
        raise BoardError("build/flasher_args.json names %s as the %s, and it is missing" % (rel, key))
    return p


def collect(build):
    bd = BuildDir(build)
    d = bd.description()
    project, idf = d.get("project_path"), d.get("idf_path")
    if not project or not idf:
        raise BoardError("project_description.json has no project_path or idf_path")
    target, reference, flash = read_board(project)
    magic, sizes = image_format(idf)
    if flash not in sizes.values():
        raise BoardError("%s %r: flash_size %r is not one ESP-IDF names (%s)" % (
            DATA_FILE, KEY, flash, ", ".join(sizes[v] for v in sorted(sizes))))
    size_slot, apps = slot(reference)
    table = flasher_file(bd, "partition-table")
    with open(table, "rb") as f:
        built = f.read()
    with open(reference, "rb") as f:
        ref = f.read()
    image = bd.app_bin()
    bootloader = flasher_file(bd, "bootloader")
    headers = [("app", image, header_flash_size(image, magic, sizes)),
               ("bootloader", bootloader, header_flash_size(bootloader, magic, sizes))]
    return {"project": project, "target": target, "reference": reference, "slot": size_slot, "apps": apps,
            "table": table, "table_same": built == ref, "image": image, "image_size": os.path.getsize(image),
            "flash": flash, "headers": headers,
            "build_target": bd.options().get("IDF_TARGET"), "prefix": tool_prefix(idf, target)}


def judge(f):
    """[failure text] for collect()'s result."""
    bad = []
    if not f["table_same"]:
        bad.append("the build's partition table is not byte-identical to the board's reference")
    if f["image_size"] > f["slot"]:
        bad.append("the image is %d bytes, over the %d-byte slot by %d" % (
            f["image_size"], f["slot"], f["image_size"] - f["slot"]))
    for what, _p, size in f["headers"]:
        if size != f["flash"]:
            bad.append("the %s image's header gives flash size %s, the board's is %s" % (what, size, f["flash"]))
    if f["build_target"] != f["target"]:
        bad.append("the build's CONFIG_IDF_TARGET is %r, the board's target is %r" % (f["build_target"], f["target"]))
    return bad


def main(args):
    try:
        f = collect(args.build)
    except (OSError, BuildDirError, BoardError) as e:
        print("FAIL R21: cannot read the build or its board data: %s" % e)
        return 1
    rel = lambda p: os.path.relpath(p, f["project"]).replace(os.sep, "/")
    print("R21 board: target %s, reference %s, flash size %s" % (f["target"], rel(f["reference"]), f["flash"]))
    print("  partition table  %s %s the reference" % (rel(f["table"]), "is identical to" if f["table_same"] else "DIFFERS from"))
    print("  slot             %d bytes (smallest of %s)" % (f["slot"], ", ".join("%s %d" % (a[0], a[3]) for a in f["apps"])))
    print("  image            %s, %d bytes, %d free" % (rel(f["image"]), f["image_size"], f["slot"] - f["image_size"]))
    for what, p, size in f["headers"]:
        print("  flash size       %s header %s (%s)" % (what, size, rel(p)))
    print("  build target     %s" % f["build_target"])
    print("  tool prefix      %s (ESP-IDF's toolchain-%s.cmake)" % (f["prefix"], f["target"]))
    bad = judge(f)
    if bad:
        print("FAIL R21: %d failure(s)" % len(bad))
        for b in bad:
            print("    %s" % b)
    else:
        print("PASS R21")
    return 1 if bad else 0
