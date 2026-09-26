#!/usr/bin/env python3
"""E3 / spec 12.4 row 22: the build-output checks, run on the image to flash.

    python3 build_output.py                       # newest build/*.bin
    python3 build_output.py --bin build/wican-fw_obd_abc1234.bin
    python3 build_output.py --dbc /path/vtrux-wican-diag.dbc

WHAT ROW 22 ASKS FOR, and each is a separate check below:

    1  CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
    2  secure boot and flash encryption unset
    3  CONFIG_EFUSE_VIRTUAL off
    4  CONFIG_TWAI_ISR_IN_IRAM unset
    5  watchdog settings as section 9.1 records
    6  partition table byte-identical to stock
    7  image size against the 1,781,760-byte slot
    8  no `-dirty`
    9  GIT_SHA embedded
    10 DIAG_FW_VERSION, the schema doc and the DBC consistent

THE VERSION IS READ FROM THE IMAGE, NOT THE FILENAME. ESP-IDF fixes the output
filename at *configure* time, so `wican-fw_obd_37addf4-dirty.bin` can contain a
build of something else entirely -- a trap already recorded once in this
project. The authoritative string is in the `esp_app_desc_t` structure at offset
0x20 of the app image, and that is what checks 8 and 9 read.

A CHECK WITH NO REFERENCE SAYS SO. Where a comparison needs something this repo
does not hold, the row reports NO REFERENCE rather than inventing a pass -- the
same discipline config_vs_spec.py uses for a number the spec does not state. A
table whose rows do not all mean the same thing is worse than no table.
"""

import argparse
import glob
import hashlib
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
REF = os.path.join(HERE, "reference")

# Spec 9.3 / row 22: the OTA slot the image has to fit.
OTA_SLOT_BYTES = 1781760

# esp_app_desc_t, at offset 0x20 of an ESP-IDF app image.
APP_DESC_OFF = 0x20
APP_DESC_MAGIC = 0xABCD5432

rows = []


def row(name, ok, detail, ref_missing=False):
    rows.append((name, ok, detail, ref_missing))


# ----------------------------------------------------------------- sdkconfig --

def load_sdkconfig():
    """Prefer the build's own sdkconfig over the checked-in one.

    build/config/sdkconfig.json is what the image was actually built from;
    ./sdkconfig is what the next build would use. They drift, and the image to
    be flashed is what row 22 asks about.
    """
    j = os.path.join(REPO, "build", "config", "sdkconfig.json")
    if os.path.exists(j):
        import json
        with open(j) as fh:
            return json.load(fh), j

    p = os.path.join(REPO, "sdkconfig")
    cfg = {}
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if v == "y":
                cfg[k[len("CONFIG_"):]] = True
            elif v.startswith('"'):
                cfg[k[len("CONFIG_"):]] = v.strip('"')
            else:
                try:
                    cfg[k[len("CONFIG_"):]] = int(v, 0)
                except ValueError:
                    cfg[k[len("CONFIG_"):]] = v
    return cfg, p


def check_config(cfg):
    def on(key):
        return cfg.get(key) is True

    # 1 -- rollback must be enabled.
    row("rollback enabled", on("BOOTLOADER_APP_ROLLBACK_ENABLE"),
        "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=%s"
        % ("y" if on("BOOTLOADER_APP_ROLLBACK_ENABLE") else "NOT SET"))

    # 2 -- secure boot and flash encryption unset.
    #
    # ONLY THE ENABLES COUNT. This build has SECURE_BOOT_V2_RSA_SUPPORTED and
    # SECURE_BOOT_V2_PREFERRED set, which are a capability and a preference, not
    # the feature -- testing for those would fail a perfectly correct image. The
    # enables are SECURE_BOOT and SECURE_FLASH_ENC_ENABLED.
    sb = on("SECURE_BOOT")
    fe = on("SECURE_FLASH_ENC_ENABLED") or on("FLASH_ENCRYPTION_ENABLED")
    row("secure boot unset", not sb,
        "CONFIG_SECURE_BOOT=%s (the _SUPPORTED/_PREFERRED flags are ignored "
        "on purpose: they are a capability, not the feature)"
        % ("y -- ENABLED" if sb else "not set"))
    row("flash encryption unset", not fe,
        "CONFIG_SECURE_FLASH_ENC_ENABLED=%s"
        % ("y -- ENABLED" if fe else "not set"))

    # 3 -- virtual eFuses off. On a real device this being on means eFuse writes
    # go nowhere, so anything that looks burned is not.
    row("EFUSE_VIRTUAL off", not on("EFUSE_VIRTUAL"),
        "CONFIG_EFUSE_VIRTUAL=%s" % ("y -- ON" if on("EFUSE_VIRTUAL") else "not set"))

    # 4 -- spec 5: stays at the stock setting; three runs put the flash build
    # inside the IRAM build's own spread.
    row("TWAI_ISR_IN_IRAM unset", not on("TWAI_ISR_IN_IRAM"),
        "CONFIG_TWAI_ISR_IN_IRAM=%s"
        % ("y -- SET" if on("TWAI_ISR_IN_IRAM") else "not set"))

    # 5 -- the watchdogs, as section 9.1 RECORDS them. This is not "the
    # watchdogs are configured well" -- section 9.1 says plainly that neither
    # rescues a hung app, and that changing either is a decision rather than a
    # given. So the check is that the state still matches what was recorded,
    # and it fails if someone changes it silently in either direction.
    wdt_user = on("BOOTLOADER_WDT_DISABLE_IN_USER_CODE")
    wdt_panic = on("ESP_TASK_WDT_PANIC")
    row("bootloader WDT still boot-only", not wdt_user,
        "CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE=%s -- section 9.1 records "
        "it NOT set, so the 9 s RTC watchdog covers boot only"
        % ("y -- CHANGED" if wdt_user else "not set"))
    row("task WDT still log-only", not wdt_panic,
        "CONFIG_ESP_TASK_WDT_PANIC=%s -- section 9.1 records it unset, so a "
        "timeout warns and continues"
        % ("y -- CHANGED" if wdt_panic else "not set"))


# ------------------------------------------------------------------- the image --

def pick_bin(explicit):
    if explicit:
        return explicit
    cands = [p for p in glob.glob(os.path.join(REPO, "build", "wican-fw_obd_*.bin"))]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def app_desc(path):
    """(version, project_name, idf_ver) from esp_app_desc_t, or None."""
    with open(path, "rb") as fh:
        fh.seek(APP_DESC_OFF)
        blob = fh.read(256)
    if len(blob) < 0x70:
        return None
    magic, = struct.unpack_from("<I", blob, 0)
    if magic != APP_DESC_MAGIC:
        return None
    # esp_app_desc_t, and the offsets matter:
    #   0x00 magic  0x04 secure_version  0x08 reserv1[2]
    #   0x10 version[32]   0x30 project_name[32]
    #   0x50 time[16]      0x60 date[16]      0x70 idf_ver[32]
    # The first version of this read 0x60 and reported the DATE as the IDF
    # version -- "idf='Sep 25 2026'" -- which is the kind of wrong that looks
    # like data.
    version = blob[0x10:0x30].split(b"\0")[0].decode("ascii", "replace")
    project = blob[0x30:0x50].split(b"\0")[0].decode("ascii", "replace")
    btime = blob[0x50:0x60].split(b"\0")[0].decode("ascii", "replace")
    bdate = blob[0x60:0x70].split(b"\0")[0].decode("ascii", "replace")
    idf = blob[0x70:0x90].split(b"\0")[0].decode("ascii", "replace")
    return version, project, idf, btime, bdate


def check_image(path):
    size = os.path.getsize(path)

    # 7 -- the slot.
    row("image fits the OTA slot", size <= OTA_SLOT_BYTES,
        "%d bytes of %d (%.1f %% of the slot, %d free)"
        % (size, OTA_SLOT_BYTES, 100.0 * size / OTA_SLOT_BYTES,
           OTA_SLOT_BYTES - size))

    d = app_desc(path)
    if d is None:
        row("app descriptor readable", False,
            "no esp_app_desc_t magic at offset 0x%02X -- this may not be an "
            "app image" % APP_DESC_OFF)
        return
    version, project, idf, btime, bdate = d

    # 8 -- no -dirty, read from the IMAGE.
    dirty = "dirty" in version
    row("no -dirty in the version", not dirty,
        "embedded version %r%s" % (version,
                                   "  <-- uncommitted changes were built"
                                   if dirty else ""))

    # 9 -- GIT_SHA embedded. ESP-IDF puts the describe output in `version`, so a
    # bare 7-or-more hex digit run is the sha.
    has_sha = bool(re.search(r"\b[0-9a-f]{7,40}\b", version))
    row("git sha embedded", has_sha,
        "embedded version %r" % version)

    # The project name carries the git describe in this build system, so an
    # equality test against "wican-fw" fails a perfectly correct image. A prefix
    # is the real invariant; the rest is informational.
    row("project name", project.startswith("wican-fw"),
        "project_name=%r" % project)
    row("build stamp", True, "idf=%r built %s %s" % (idf, bdate, btime))


# ------------------------------------------------------------ partition table --

def check_partitions():
    built = os.path.join(REPO, "build", "partition_table", "partition-table.bin")
    stock = os.path.join(REF, "stock-v4.13-partition-table.bin")

    if not os.path.exists(built):
        row("partition table matches stock", False,
            "no build/partition_table/partition-table.bin to check")
        return
    if not os.path.exists(stock):
        row("partition table matches stock", None,
            "no stock reference at %s -- extract partition-table.bin from the "
            "stock release zip" % os.path.relpath(stock, REPO),
            ref_missing=True)
        return

    a = open(built, "rb").read()
    b = open(stock, "rb").read()
    same = a == b
    row("partition table matches stock", same,
        "built sha256 %s vs stock %s%s"
        % (hashlib.sha256(a).hexdigest()[:16],
           hashlib.sha256(b).hexdigest()[:16],
           "" if same else "  <-- DIFFERS; a device flashed with this loses "
                           "its recovery layout"))


# ------------------------------------------------------- diag schema and DBC --

# The DBC and the schema doc live in the analysis project, not in the firmware
# repo, so these are defaults to be overridden rather than guarantees. A missing
# one is reported as NO REFERENCE, never as a pass.
_PROJ = os.path.join(REPO, "..", "..", "seadrive_root", "nickivyc_1",
                     "My Libraries", "NotGit", "reverse-it", "projects", "vtrux")
DEFAULT_DBC = os.path.join(_PROJ, "vtrux-wican-diag.dbc")
DEFAULT_SCHEMA_DOC = os.path.join(_PROJ, "notes", "artifacts", "gen-inhibit",
                                  "wican_diag_schema.md")


def core_schema_ver():
    p = os.path.join(REPO, "main", "gen_inhibit_core.h")
    m = re.search(r"#define\s+GI_DIAG_SCHEMA_VER\s+(\d+)", open(p).read())
    return int(m.group(1)) if m else None


def core_diag_ids():
    p = os.path.join(REPO, "main", "gen_inhibit_core.h")
    s = open(p).read()
    out = {}
    for name, val in re.findall(r"#define\s+(GI_DIAG_ID_\w+|GI_PROBE_ID)\s+(0x[0-9A-Fa-f]+)", s):
        out[name] = int(val, 16)
    return out


def check_diag(dbc_path):
    ver = core_schema_ver()
    ids = core_diag_ids()

    row("diag schema version readable", ver is not None,
        "GI_DIAG_SCHEMA_VER = %s" % ver)

    if not ids:
        row("diag IDs readable", False, "no GI_DIAG_ID_* found in the core header")
        return
    row("diag IDs readable", True,
        ", ".join("%s=0x%03X" % (k.replace("GI_DIAG_ID_", ""), v)
                  for k, v in sorted(ids.items(), key=lambda kv: kv[1])))

    if not dbc_path or not os.path.exists(dbc_path):
        row("DBC defines every diag ID", None,
            "no DBC at %s -- pass --dbc" % dbc_path, ref_missing=True)
        return

    try:
        import cantools
    except ImportError:
        row("DBC defines every diag ID", None,
            "cantools not installed", ref_missing=True)
        return

    db = cantools.database.load_file(dbc_path)
    defined = {m.frame_id for m in db.messages}

    # ROW 22 SAYS "diag frames", AND THE PROBE IS NOT ONE. 0x7F0 is the RESPOND
    # mode's timing measurement, not a status page, and vtrux-wican-diag.dbc
    # deliberately does not define it. The first version of this check demanded
    # every GI_*_ID including the probe and failed a correct DBC -- an
    # over-claim of exactly the kind config_vs_spec.py was corrected for.
    diag_ids = {k: v for k, v in ids.items() if k.startswith("GI_DIAG_ID_")}
    missing = sorted(v for v in diag_ids.values() if v not in defined)
    row("DBC defines every diag page", not missing,
        "DBC has %d messages; missing %s"
        % (len(db.messages),
           ", ".join("0x%03X" % v for v in missing) if missing else "none"))

    probe = ids.get("GI_PROBE_ID")
    row("probe ID is out of scope", True,
        "0x%03X is %sin the DBC, and row 22 asks about diag frames only"
        % (probe, "" if probe in defined else "not "))

    # DECODE THE FRAMES THE HOST BUILD ACTUALLY EMITTED. This is the half of
    # row 22 that a definition check cannot do: the DBC can define every ID and
    # still disagree with the bytes the firmware puts in them. The goldens hold
    # real emitted diag frames, so they are the sample.
    _decode_emitted(db, defined)


def _decode_emitted(db, defined):
    """Decode every diag frame in the host goldens against the DBC."""
    gold = os.path.join(REPO, "test", "gen_inhibit_host", "golden")
    if not os.path.isdir(gold):
        row("emitted diag frames decode", None,
            "no goldens to decode", ref_missing=True)
        return

    seen = 0
    errs = []
    ids_seen = set()
    for fn in sorted(os.listdir(gold)):
        if not fn.endswith(".trace"):
            continue
        with open(os.path.join(gold, fn)) as fh:
            for line in fh:
                if " TX DIAG " not in line:
                    continue
                m = re.search(r"id=([0-9A-Fa-f]{3}) dlc=(\d+) data=([0-9A-Fa-f]+)",
                              line)
                if not m:
                    continue
                ident = int(m.group(1), 16)
                data = bytes.fromhex(m.group(3))
                ids_seen.add(ident)
                if ident not in defined:
                    errs.append("%s: 0x%03X is emitted but not in the DBC"
                                % (fn, ident))
                    continue
                try:
                    db.decode_message(ident, data)
                    seen += 1
                except Exception as e:      # noqa: BLE001 -- report anything
                    errs.append("%s: 0x%03X %s -- %s"
                                % (fn, ident, data.hex().upper(), e))

    if seen == 0 and not errs:
        row("emitted diag frames decode", None,
            "no TX DIAG lines found in the goldens, so nothing was decoded",
            ref_missing=True)
        return

    row("emitted diag frames decode", not errs,
        "%d frames on %s decoded clean%s"
        % (seen, ", ".join("0x%03X" % i for i in sorted(ids_seen)),
           "" if not errs else "; %d problem(s): %s" % (len(errs), errs[0])))


def check_schema_doc(doc_path):
    """The schema doc's stated version against the core's."""
    ver = core_schema_ver()
    if not doc_path or not os.path.exists(doc_path):
        row("schema doc version matches", None,
            "no schema doc at %s" % doc_path, ref_missing=True)
        return
    text = open(doc_path, encoding="utf-8").read()
    m = re.search(r"diag_schema_ver[^|]*\|[^|]*?\*\*(\d+)\*\*", text)
    if not m:
        m = re.search(r"schema version \(currently \*\*(\d+)\*\*\)", text)
    if not m:
        row("schema doc version matches", None,
            "could not find a stated version in the doc", ref_missing=True)
        return
    doc_ver = int(m.group(1))
    row("schema doc version matches", doc_ver == ver,
        "doc says %d, GI_DIAG_SCHEMA_VER is %s" % (doc_ver, ver))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin")
    ap.add_argument("--dbc", default=DEFAULT_DBC)
    ap.add_argument("--schema-doc", default=DEFAULT_SCHEMA_DOC)
    args = ap.parse_args()

    cfg, cfg_src = load_sdkconfig()
    print("sdkconfig from %s" % os.path.relpath(cfg_src, REPO))
    check_config(cfg)

    b = pick_bin(args.bin)
    if b is None:
        row("an image to check", False, "no build/wican-fw_obd_*.bin found")
    else:
        print("image      %s" % os.path.relpath(b, REPO))
        check_image(b)

    check_partitions()
    check_diag(args.dbc)
    check_schema_doc(args.schema_doc)

    print()
    bad = 0
    noref = 0
    for name, ok, detail, ref_missing in rows:
        if ok is None or ref_missing:
            mark = "NO REF"
            noref += 1
        elif ok:
            mark = "  ok  "
        else:
            mark = " FAIL "
            bad += 1
        print("[%s] %-32s %s" % (mark, name, detail))

    print()
    if bad:
        print("%d check(s) FAILED. This image must not be flashed to the truck "
              "until each is understood -- row 22 exists because every one of "
              "them is a way to brick a device with no USB port and no factory "
              "reset." % bad)
    else:
        print("every check with a reference passed")
    if noref:
        print("%d check(s) had NO REFERENCE and proved nothing. They are not "
              "passes." % noref)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
