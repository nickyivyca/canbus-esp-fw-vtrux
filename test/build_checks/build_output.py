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

Added 2026-09-26 from review, as the source-check half of two spec 12.4 rows
that nothing implemented:

    11 CAN_RX_QUEUE_LEN >= 64, the override actually applied, and E4's own
       RX_DEPTH equal to it (spec 5.1 item 1)
    12 esp_wifi_set_storage(WIFI_STORAGE_RAM) genuinely called (spec 5.1 item 3)

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
import subprocess
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


def build_dir_version():
    """The version the current build directory produced, or None."""
    p = os.path.join(REPO, "build", "project_description.json")
    if not os.path.exists(p):
        return None
    import json
    try:
        with open(p) as fh:
            return json.load(fh).get("project_version")
    except (ValueError, OSError):
        return None


def check_config(cfg, trusted, why):
    """The sdkconfig rows.

    `trusted` is False when the config on disk is not the config that produced
    the image under test. In that case every row here is reported NO REFERENCE
    rather than as a pass, because a pass would be a statement about a
    different build.

    THIS IS THE DEFECT THE REVIEWING SESSION FOUND on 2026-09-26, and it is the
    same shape as the one config_vs_spec.py was corrected for: a row that looks
    like it checked something, against a reference that does not describe the
    thing being checked. The honest answer to "is secure boot off in this
    image?" when the config belongs to another build is "this cannot tell you",
    not "yes".
    """
    def on(key):
        return cfg.get(key) is True

    def crow(name, ok, detail):
        if not trusted:
            row(name, None, "%s (%s)" % (detail, why), ref_missing=True)
        else:
            row(name, ok, detail)

    # 1 -- rollback must be enabled.
    crow("rollback enabled", on("BOOTLOADER_APP_ROLLBACK_ENABLE"),
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
    crow("secure boot unset", not sb,
        "CONFIG_SECURE_BOOT=%s (the _SUPPORTED/_PREFERRED flags are ignored "
        "on purpose: they are a capability, not the feature)"
        % ("y -- ENABLED" if sb else "not set"))
    crow("flash encryption unset", not fe,
        "CONFIG_SECURE_FLASH_ENC_ENABLED=%s"
        % ("y -- ENABLED" if fe else "not set"))

    # 3 -- virtual eFuses off. On a real device this being on means eFuse writes
    # go nowhere, so anything that looks burned is not.
    crow("EFUSE_VIRTUAL off", not on("EFUSE_VIRTUAL"),
        "CONFIG_EFUSE_VIRTUAL=%s" % ("y -- ON" if on("EFUSE_VIRTUAL") else "not set"))

    # 4 -- spec 5: stays at the stock setting; three runs put the flash build
    # inside the IRAM build's own spread.
    crow("TWAI_ISR_IN_IRAM unset", not on("TWAI_ISR_IN_IRAM"),
        "CONFIG_TWAI_ISR_IN_IRAM=%s"
        % ("y -- SET" if on("TWAI_ISR_IN_IRAM") else "not set"))

    # 5 -- the watchdogs, as section 9.1 RECORDS them. This is not "the
    # watchdogs are configured well" -- section 9.1 says plainly that neither
    # rescues a hung app, and that changing either is a decision rather than a
    # given. So the check is that the state still matches what was recorded,
    # and it fails if someone changes it silently in either direction.
    wdt_user = on("BOOTLOADER_WDT_DISABLE_IN_USER_CODE")
    wdt_panic = on("ESP_TASK_WDT_PANIC")
    crow("bootloader WDT still boot-only", not wdt_user,
        "CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE=%s -- section 9.1 records "
        "it NOT set, so the 9 s RTC watchdog covers boot only"
        % ("y -- CHANGED" if wdt_user else "not set"))
    crow("task WDT still log-only", not wdt_panic,
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


def git(*args):
    """Run git in the repo, or return None if that is not possible."""
    try:
        r = subprocess.run(("git",) + args, cwd=REPO, capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def check_image_identity(img_ver):
    """Was this image built from the commit that is checked out now?

    ADDED 2026-09-26 AFTER THIS EXACT TRAP CAUGHT US. ESP-IDF fixes the output
    filename and the esp_app_desc_t version at *configure* time, so a rebuild
    after a commit -- without `idf.py reconfigure` -- produces new bytes carrying
    the PREVIOUS commit's name. It happened between 4b28547 and 3a1f9b8: the
    binary changed size and was written 20 s before 3a1f9b8 was committed, while
    still embedding "4b28547" and no `-dirty`. Every row above passed on it, and
    the image of the commit it claimed to be no longer existed anywhere.

    The rows above cannot see this. `no -dirty` and `git sha embedded` read the
    same configure-time string, and the config-applies-to-this-image comparison
    reads build/project_description.json, which is written at configure time too
    -- so it agrees with the stale name and confirms nothing. Every instrument
    in this file was downstream of the same moment. The repo's HEAD is the one
    reference that is not.

    A mismatch is a FAIL rather than a NO REF: the file's whole premise is that
    it is run on the image about to be flashed, and an image whose provenance is
    misstated must not be flashed whatever else is true of it. Auditing an older
    image on purpose (--bin) is expected to fail this row, and says why.
    """
    head = git("rev-parse", "--short", "HEAD")
    if head is None:
        row("image built from HEAD", None,
            "not a git repository, or git is unavailable", ref_missing=True)
        return
    if img_ver is None:
        row("image built from HEAD", None,
            "no version could be read from the image", ref_missing=True)
        return

    # A dirty tree cannot be identified by a commit at all, and ESP-IDF's own
    # `-dirty` suffix is itself configure-time, so it is no help here.
    dirty = git("status", "--porcelain")
    if dirty:
        n = len(dirty.splitlines())
        row("working tree clean", False,
            "%d uncommitted change(s), so no commit identifies these bytes" % n)
    else:
        row("working tree clean", True, "no uncommitted changes")

    # The embedded string is `git describe`-shaped, so HEAD's short sha may be a
    # prefix of it rather than equal to it.
    ok = img_ver.startswith(head) or head.startswith(img_ver)
    row("image built from HEAD", ok,
        "embedded %r, HEAD is %r" % (img_ver, head) if ok else
        "embedded %r but HEAD is %r -- run `idf.py reconfigure` and rebuild; "
        "the filename and embedded version are fixed at configure time, so "
        "these bytes are almost certainly a later commit wearing an earlier "
        "name" % (img_ver, head))


def check_source():
    """Spec 5.1 items 1 and 3: two facts that live only in the source.

    Neither can be read from the image, and both are things the host suite is
    structurally unable to see -- so without these rows they rest on nobody
    having changed a constant.

    RX QUEUE DEPTH, TWICE OVER. Spec 12.4 asks for ">= 64 whenever the driver
    is installed (E1 reads the depth the driver was installed with; E3 or a
    source check pins it)". This is the pinning half, and it also closes a gap
    that existed in the other direction: E4's load model carries its own
    RX_DEPTH in make_scenarios.py, independent of the firmware's
    CAN_RX_QUEUE_LEN, with nothing requiring them to agree. If they drifted, the
    model would quietly stop describing the device and every E4 result would
    still pass. Review 2026-09-26 confirmed both mutations survived every suite:
    removing the override line, and setting 32 back to 5.

    WIFI STORAGE. Spec 5.1 item 3's argument that no flash write happens while
    the inhibitor owns the bus depends on the WiFi driver keeping its
    configuration in RAM -- the spec says "asserted, not assumed". This asserts
    it.
    """
    can_c = os.path.join(REPO, "main", "can.c")
    ms_py = os.path.join(REPO, "test", "gen_inhibit_host", "make_scenarios.py")
    wifi_c = os.path.join(REPO, "main", "wifi_network.c")

    fw_depth = None
    if os.path.exists(can_c):
        text = open(can_c, encoding="utf-8", errors="replace").read()
        m = re.search(r"^#define\s+CAN_RX_QUEUE_LEN\s+(\d+)", text, re.M)
        if m:
            fw_depth = int(m.group(1))
        # The constant existing is not the same as it reaching the driver.
        #
        # LINE BY LINE, SKIPPING COMMENTS. The first version of this row matched
        # the whole file with a regex, so a COMMENTED-OUT
        # `// g_config.rx_queue_len = CAN_RX_QUEUE_LEN;` satisfied it -- the
        # reviewer demonstrated the mutation passing on 2026-09-26. A check that
        # a comment can satisfy is not a check, and the same discipline was
        # already being used two rows down for the WiFi storage call, which is
        # what makes the miss embarrassing rather than subtle.
        applied = None
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                continue
            if re.search(r"rx_queue_len\s*=\s*CAN_RX_QUEUE_LEN", s):
                applied = s
                break
        row("rx_queue_len override applied", applied is not None,
            "main/can.c assigns %s" % applied if applied else
            "no live assignment of rx_queue_len from CAN_RX_QUEUE_LEN in "
            "main/can.c, so the driver gets TWAI_GENERAL_CONFIG_DEFAULT's 5")
    else:
        row("rx_queue_len override applied", None,
            "no main/can.c at %s" % can_c, ref_missing=True)

    if fw_depth is None:
        row("CAN_RX_QUEUE_LEN >= 64", None,
            "could not read CAN_RX_QUEUE_LEN from main/can.c", ref_missing=True)
    else:
        row("CAN_RX_QUEUE_LEN >= 64", fw_depth >= 64,
            "CAN_RX_QUEUE_LEN is %d (spec 5.1 item 1 requires at least 64)"
            % fw_depth)

    model_depth = None
    if os.path.exists(ms_py):
        m = re.search(r"^RX_DEPTH\s*=\s*(\d+)",
                      open(ms_py, encoding="utf-8", errors="replace").read(), re.M)
        if m:
            model_depth = int(m.group(1))
    if fw_depth is None or model_depth is None:
        row("E4 RX_DEPTH matches firmware", None,
            "firmware=%r model=%r -- one of them could not be read"
            % (fw_depth, model_depth), ref_missing=True)
    else:
        row("E4 RX_DEPTH matches firmware", model_depth == fw_depth,
            "make_scenarios.RX_DEPTH=%d, CAN_RX_QUEUE_LEN=%d"
            % (model_depth, fw_depth))

    if not os.path.exists(wifi_c):
        row("WiFi storage is RAM-only", None,
            "no main/wifi_network.c", ref_missing=True)
    else:
        text = open(wifi_c, encoding="utf-8", errors="replace").read()
        # Called, not merely mentioned: the argument must be WIFI_STORAGE_RAM and
        # the line must not be commented out.
        hit = None
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                continue
            if "esp_wifi_set_storage" in s and "WIFI_STORAGE_RAM" in s:
                hit = s
                break
        row("WiFi storage is RAM-only", hit is not None,
            "main/wifi_network.c calls %s" % hit if hit else
            "no live esp_wifi_set_storage(WIFI_STORAGE_RAM) call found, so spec "
            "5.1 item 3's claim that the WiFi driver writes no NVS is unbacked")


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

    b = pick_bin(args.bin)
    img_ver = None
    if b is not None:
        d = app_desc(b)
        if d:
            img_ver = d[0]

    # DOES THE CONFIG ON DISK DESCRIBE THIS IMAGE? The build directory records
    # what it produced, so the two can be compared instead of assumed.
    bd_ver = build_dir_version()
    trusted = True
    why = ""
    if img_ver is None:
        trusted = False
        why = "no version could be read from the image"
    elif bd_ver is None:
        trusted = False
        why = "build/project_description.json is missing, so the config cannot "\
              "be tied to this image"
    elif bd_ver != img_ver:
        trusted = False
        why = "the build directory holds %r but this image is %r" % (bd_ver, img_ver)

    print("sdkconfig from %s" % os.path.relpath(cfg_src, REPO))
    if b is not None:
        print("image      %s" % os.path.relpath(b, REPO))
    print("config applies to this image: %s%s"
          % ("yes" if trusted else "NO", "" if trusted else " -- " + why))

    check_config(cfg, trusted, why)

    if b is None:
        row("an image to check", False, "no build/wican-fw_obd_*.bin found")
    else:
        check_image(b)

    check_partitions()
    check_diag(args.dbc)
    check_schema_doc(args.schema_doc)
    check_source()
    check_image_identity(img_ver)

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
