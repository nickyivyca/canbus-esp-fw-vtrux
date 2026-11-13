"""Checks one built X2 test image against its image folder's x2_image.json.

TEST EQUIPMENT. Shared harness of x2_test_images (owner: the testers, the
gen-inhibit reviewer's ruling of 2026-11-12), generic over the image folder
and its variants: everything image-specific is in <image>/x2_image.json.

    python3.13 -B check_image.py IMAGE_DIR VARIANT [--build-dir DIR]

IMAGE_DIR is an image folder (jammer/, slcan_spi/, ...); VARIANT one of its
x2_image.json variants; the build is IMAGE_DIR/build/VARIANT unless
--build-dir names another. Exit 0 PASS, 1 FAIL, 2 REFUSED (nothing checked:
no x2_image.json, an unknown variant, a build file missing).

The variant is judged from the image, never from the argument (the
reviewer's ruling, after shared/tools R20): the app descriptor's
project_name must be exactly one variant's project_name in x2_image.json,
and that variant must be the one offered. So a stop_a_only image offered as
the jammer FAILs, and a name ESP-IDF cut at 31 characters matches nothing.

The checks (each names what it rests on):
  app_image        the .bin parses, its checksum and SHA-256 match
                   (shared/tools vtrux_build.image)
  chip             the image's chip id is x2_image.json's chip
  project_name     exactly one variant's name; names fit the 31-character
                   descriptor field
  variant          the image's variant is the one offered
  not_for_vehicle  if the image is NOT FOR VEHICLE: the name says so
                   (x2_test_images SPEC T8, T14)
  strings          every required_strings entry is in the .bin
  elf_hash         the .elf's SHA-256 is the descriptor's (shared/tools R1)
  elf_symbols      the ELF's symbol table reads, holds over 100 symbols and
                   every required_symbols entry (so an empty read cannot
                   pass the next check)
  no_forbidden     no symbol starts with a forbidden_symbol_prefixes entry
                   (the jammer: no TWAI driver, SPEC T8)
  config           every required_config value in the build's
                   config/sdkconfig.json
  build_metadata   project_description.json names the same project
"""

import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The same checkout's shared tooling: one shared/main commit holds both.
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from vtrux_build.image import AppImage, ImageError  # noqa: E402

DATA_FILE = "x2_image.json"
NAME_MAX = 31           # esp_app_desc_t.project_name is 32 bytes with its NUL
SYMBOLS_MIN = 100


class Refused(Exception):
    """Nothing was checked."""


def load_data(image_dir):
    """x2_image.json, validated. Raises Refused."""
    path = os.path.join(image_dir, DATA_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("%s: %s" % (path, e))
    variants = data.get("variants")
    if not isinstance(data.get("image"), str) or not isinstance(variants, dict) \
            or not variants:
        raise Refused("%s: needs 'image' and a non-empty 'variants'" % path)
    names = []
    for v, d in variants.items():
        if not isinstance(d, dict) or not isinstance(d.get("project_name"), str) \
                or not isinstance(d.get("pinnable"), bool):
            raise Refused("%s: variant %r needs project_name and pinnable"
                          % (path, v))
        names.append(d["project_name"])
    if len(set(names)) != len(names):
        raise Refused("%s: two variants share a project_name" % path)
    for key in ("required_strings", "forbidden_symbol_prefixes",
                "required_symbols"):
        if not isinstance(data.get(key, []), list):
            raise Refused("%s: %s must be a list" % (path, key))
    if not isinstance(data.get("required_config", {}), dict):
        raise Refused("%s: required_config must be an object" % path)
    return data


def build_paths(build_dir):
    """The build's files, from its flasher_args.json and
    project_description.json. Raises Refused if any is missing."""
    try:
        with open(os.path.join(build_dir, "flasher_args.json"),
                  encoding="utf-8") as f:
            fa = json.load(f)
        with open(os.path.join(build_dir, "project_description.json"),
                  encoding="utf-8") as f:
            pd = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("build %s: %s" % (build_dir, e))
    try:
        flash = sorted((int(off, 16), rel) for off, rel in fa["flash_files"].items())
        app_rel = fa["app"]["file"]
        app_off = int(fa["app"]["offset"], 16)
        elf_rel = pd["app_elf"]
    except (KeyError, ValueError, TypeError) as e:
        raise Refused("build %s: flasher_args/project_description: %s"
                      % (build_dir, e))
    out = {
        "flash": [(off, os.path.join(build_dir, rel)) for off, rel in flash],
        "app": os.path.join(build_dir, app_rel),
        "app_offset": app_off,
        "elf": os.path.join(build_dir, elf_rel),
        "sdkconfig": os.path.join(build_dir, "config", "sdkconfig.json"),
        "project_name": pd.get("project_name"),
    }
    for p in [p for _o, p in out["flash"]] + [out["app"], out["elf"],
                                               out["sdkconfig"]]:
        if not os.path.isfile(p):
            raise Refused("build file missing: %s" % p)
    return out


def elf_symbols(data):
    """The names in an ELF32 little-endian file's symbol tables. Raises
    ValueError on anything else."""
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 1 or data[5] != 1:
        raise ValueError("not ELF32 little-endian")
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
    if shentsize != 40 or shoff + shnum * 40 > len(data):
        raise ValueError("section headers out of range")
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * 40)
            for i in range(shnum)]
    names = set()
    for s in secs:
        if s[1] != 2:           # SHT_SYMTAB
            continue
        off, size, link, entsize = s[4], s[5], s[6], s[9]
        if entsize != 16 or link >= shnum:
            raise ValueError("bad symbol table")
        st = secs[link]
        stroff, strsize = st[4], st[5]
        for k in range(size // 16):
            nameoff, = struct.unpack_from("<I", data, off + k * 16)
            if nameoff == 0 or nameoff >= strsize:
                continue
            end = data.index(b"\0", stroff + nameoff)
            names.add(data[stroff + nameoff:end].decode("ascii", "replace"))
    return names


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Result:
    def __init__(self):
        self.checks = []        # [(name, ok, detail)]
        self.image_variant = None
        self.app = None
        self.paths = None

    def add(self, name, ok, detail):
        self.checks.append((name, bool(ok), detail))

    @property
    def ok(self):
        return bool(self.checks) and all(ok for _n, ok, _d in self.checks)

    def as_rows(self):
        return [{"name": n, "ok": ok, "detail": d} for n, ok, d in self.checks]


def check(image_dir, variant, build_dir=None):
    """Checks the build. Returns a Result; raises Refused when nothing can be
    checked."""
    data = load_data(image_dir)
    if variant not in data["variants"]:
        raise Refused("no variant %r in %s; the variants are: %s"
                      % (variant, DATA_FILE, " ".join(data["variants"])))
    if build_dir is None:
        build_dir = os.path.join(image_dir, "build", variant)
    paths = build_paths(build_dir)
    r = Result()
    r.paths = paths

    try:
        app = AppImage.read(paths["app"])
        r.app = app
        r.add("app_image", True, "%s, %d bytes" % (os.path.basename(paths["app"]),
                                                    len(app.data)))
    except (ImageError, OSError) as e:
        r.add("app_image", False, str(e))
        return r

    want_chip = data.get("chip")
    r.add("chip", want_chip is None or app.chip == want_chip,
          "image %s, want %s" % (app.chip, want_chip))

    by_name = {d["project_name"]: v for v, d in data["variants"].items()}
    long_names = [n for n in by_name if len(n) > NAME_MAX]
    found = by_name.get(app.project_name)
    r.image_variant = found
    r.add("project_name", found is not None and not long_names,
          "image %r -> variant %s%s" % (
              app.project_name, found or "NONE (matches no variant)",
              "; names over %d characters in %s: %s"
              % (NAME_MAX, DATA_FILE, " ".join(long_names)) if long_names else ""))
    r.add("variant", found == variant,
          "image is %s, offered as %s" % (found, variant))

    if data.get("not_for_vehicle"):
        r.add("not_for_vehicle", "NOT_FOR_VEHICLE" in app.project_name,
              "project_name %r" % app.project_name)

    missing = [s for s in data.get("required_strings", [])
               if s.encode("ascii") not in app.data]
    r.add("strings", not missing,
          "all %d present" % len(data.get("required_strings", []))
          if not missing else "missing: %r" % missing)

    elf_sha = _sha256(paths["elf"])
    r.add("elf_hash", elf_sha == app.image_hash,
          "elf %s, descriptor %s" % (elf_sha[:16], app.image_hash[:16]))

    try:
        with open(paths["elf"], "rb") as f:
            syms = elf_symbols(f.read())
    except (ValueError, OSError, struct.error) as e:
        r.add("elf_symbols", False, "unreadable: %s" % e)
        return r
    req_missing = [s for s in data.get("required_symbols", []) if s not in syms]
    r.add("elf_symbols", len(syms) > SYMBOLS_MIN and not req_missing,
          "%d symbols%s" % (len(syms), "; missing: %s" % " ".join(req_missing)
                            if req_missing else ""))
    prefixes = data.get("forbidden_symbol_prefixes", [])
    bad = sorted(s for s in syms if any(s.startswith(p) for p in prefixes))
    r.add("no_forbidden", not bad,
          "none of %s" % " ".join(prefixes) if not bad
          else "found: %s" % " ".join(bad[:10]))

    try:
        with open(paths["sdkconfig"], encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        r.add("config", False, str(e))
        return r
    wrong = []
    for k, want in data.get("required_config", {}).items():
        key = k[len("CONFIG_"):] if k.startswith("CONFIG_") else k
        if cfg.get(key) != want:
            wrong.append("%s=%r (want %r)" % (k, cfg.get(key), want))
    r.add("config", not wrong, "; ".join(wrong) or "all %d as required"
          % len(data.get("required_config", {})))

    r.add("build_metadata", paths["project_name"] == app.project_name,
          "project_description %r" % paths["project_name"])
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image_dir")
    ap.add_argument("variant")
    ap.add_argument("--build-dir")
    a = ap.parse_args(argv)
    try:
        r = check(os.path.abspath(a.image_dir), a.variant, a.build_dir)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    for name, ok, detail in r.checks:
        print("%-16s %s  %s" % (name, "PASS" if ok else "FAIL", detail))
    print("\n%s" % ("PASS" if r.ok else "FAIL"))
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
