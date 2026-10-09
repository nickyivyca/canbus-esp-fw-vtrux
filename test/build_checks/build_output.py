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

BEYOND ROW 22, this file also REFUSES images that are not shipping builds,
because two configurations of one commit share a filename and an embedded
version and so defeat every provenance row above -- only content separates
them. `check_no_instrument` refuses the three measurement builds
(GI_INSTRUMENT_RXQ, _TXABORT, _ABORTWIN); `check_not_rollback_rehearsal`,
added 2026-10-04 after the rehearsal was run, refuses the
OTA_HEALTH_FAULT_INJECT build, which would otherwise roll itself back 60 s
after every boot on the truck.

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
import json
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

def load_sdkconfig(build_dir=None):
    """Prefer the build's own sdkconfig over the checked-in one.

    <build_dir>/config/sdkconfig.json is what the image was actually built from;
    ./sdkconfig is what the next build would use. They drift, and the image to
    be flashed is what row 22 asks about.

    `build_dir` COMES FROM THE IMAGE'S OWN PATH (2026-09-27). It used to be
    hard-coded to build/, so checking an image from build-txabort or build-rxq
    read the SHIPPING build's config and still printed "config applies to this
    image: yes" -- true only because those directories happened to be built from
    the same commit with the same sdkconfig. The trust flag exists precisely so
    that a pass is a statement about THIS image, and coincidence is not the
    mechanism it is supposed to rest on. Raised by the reviewing session.
    """
    if build_dir is None:
        build_dir = os.path.join(REPO, "build")
    j = os.path.join(build_dir, "config", "sdkconfig.json")
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


def build_dir_version(build_dir=None):
    """The version the given build directory produced, or None."""
    if build_dir is None:
        build_dir = os.path.join(REPO, "build")
    p = os.path.join(build_dir, "project_description.json")
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

def check_partitions(build_dir=None):
    """The table the image's OWN build produced, against stock.

    `build_dir` comes from the image's path, as for load_sdkconfig() (tester,
    2026-10-08). It used to be hard-coded to build/, so an image built with
    -B build-tester-<sha> FAILED this row for a file it never looked for --
    and, worse, an image checked while build/ held some other build would
    have been judged by that build's table.
    """
    if build_dir is None:
        build_dir = os.path.join(REPO, "build")
    built = os.path.join(build_dir, "partition_table", "partition-table.bin")
    stock = os.path.join(REF, "stock-v4.13-partition-table.bin")

    if not os.path.exists(built):
        row("partition table matches stock", False,
            "no %s to check" % os.path.relpath(built, REPO).replace("\\", "/"))
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


def check_no_instrument(path):
    """Row 26: this image must NOT be ANY measurement build.

    ADDED 2026-09-26. The measurement build (spec 12.4's backlog row) reads the
    TWAI driver's queue state on every dequeue, which perturbs what it measures --
    tx_queued_behind went from 0 to 1 on the bench -- and it has no business on a
    vehicle. Two builds of one commit share a filename AND an embedded version, so
    NONE of the provenance rows above can tell them apart: "embedded 69ecae3, HEAD
    69ecae3" is equally true of both. Only the content can.

    Checked two ways because they can fail independently. The snprintf format
    string is emitted into .rodata verbatim and has to be there if the code is; the
    statics show up as ELF symbols, which is the check that keeps working if the
    JSON is ever restructured.

    EXTENDED 2026-09-27 to GI_INSTRUMENT_TXABORT (spec 5.2 item 9), and the
    reason it is now a TABLE is the gap the extension exposed. The first version
    looked only for the backlog instrument's markers, so a TX-abort measurement
    image -- one that writes the TWAI abort command register directly from
    outside the driver and exposes an HTTP endpoint to do it -- passed a row
    whose name says there is no measurement instrument in the image. A row that
    cannot fail on the thing it is named after is worse than no row, because it
    reads as coverage. Adding a second instrument to the table is now one line
    in one place instead of an edit in two.
    """
    try:
        img = open(path, "rb").read()
    except OSError as e:
        row("no measurement instrument", None,
            "cannot read %s (%s)" % (os.path.basename(path), e),
            ref_missing=True)
        return

    # (flag, image markers, ELF symbols). Every entry is an instrument that
    # must not reach a vehicle; a hit on ANY marker fails the row.
    instruments = (
        ("GI_INSTRUMENT_RXQ",
         (b'"rxq":{',),
         ("s_rxq_max", "s_rxq_samples", "rxq_sample", "rxq_reset")),
        ("GI_INSTRUMENT_TXABORT",
         (b'"probe":"gi_txabort"', b"/gi_txabort"),
         # txab_trial is NOT in this list, and the omission is the point: it
         # is static and the compiler inlines it away, so a table containing it
         # would claim coverage it does not have. The reviewer's mutation M5
         # (2026-09-27) proved it -- with only txab_trial left, the row passes on
         # a measurement image. These three are what nm actually reports:
         # gi_txabort_register (T), txab_handler (t, address taken by the uri
         # struct), txab_uri (d, registered with the server).
         ("gi_txabort_register", "txab_handler", "txab_uri")),
        # Added 2026-10-03 with the instrument. `gs_abortwin_note` is non-static
        # precisely so that nm reports it -- see M5 above.
        ("GI_INSTRUMENT_ABORTWIN",
         (b'"abortwin":{',),
         ("gs_abortwin_note",)),
    )

    img_hits = []
    for flag, markers, _syms in instruments:
        for m in markers:
            if m in img:
                img_hits.append("%s (%r)" % (flag, m.decode("latin-1")))
    row("no measurement instrument in the image", not img_hits,
        "image carries " + "; ".join(img_hits) + " -- a measurement build, and "
        "must not be flashed to the truck" if img_hits else
        "none of the %d instruments' markers are in the image"
        % len(instruments))

    # The ELF, where the statics live. Absent on a downloaded image, which is a
    # missing reference rather than a pass.
    elf = os.path.splitext(path)[0] + ".elf"
    if not os.path.exists(elf):
        row("no instrument symbols in the ELF", None,
            "no ELF beside the image, so the symbol check has no reference",
            ref_missing=True)
        return
    # elf_checks.py already knows where the toolchain nm is, including the
    # ~/.espressif path that is absent from PATH outside an exported IDF shell.
    # Searching separately here found nothing and quietly reported NO REFERENCE.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from elf_checks import find_nm
    except ImportError as e:
        row("no instrument symbols in the ELF", None,
            "cannot import elf_checks.find_nm (%s)" % e, ref_missing=True)
        return
    exe = find_nm()
    if exe is None:
        row("no instrument symbols in the ELF", None,
            "no riscv32-esp-elf-nm found, so the symbol check has no reference",
            ref_missing=True)
        return
    r = subprocess.run((exe, "--defined-only", elf),
                       capture_output=True, text=True)
    if r.returncode != 0:
        row("no instrument symbols in the ELF", None,
            "nm exited %d on %s" % (r.returncode, os.path.basename(elf)),
            ref_missing=True)
        return
    nm = r.stdout
    hits = []
    nsym = 0
    for flag, _markers, syms in instruments:
        nsym += len(syms)
        for sym in syms:
            if sym in nm:
                hits.append("%s:%s" % (flag, sym))
    row("no instrument symbols in the ELF", not hits,
        "ELF defines %s" % ", ".join(hits) if hits
        else "none of the %d instrument symbols are defined" % nsym)


def check_not_rollback_rehearsal(path):
    """This image must NOT be the rollback-rehearsal build.

    ADDED 2026-10-04, after running the rehearsal (gap 6) exposed that nothing
    here could refuse its image. `OTA_HEALTH_FAULT_INJECT` suppresses a
    subsystem's health report so the gate times out and the image rolls back,
    which is exactly right on a bench and catastrophic on a truck: the image
    would roll back 60 s after every boot, forever, and with no console record
    of why (main.c:623 silences all logging at the end of app_main, so the
    gate's own UNHEALTHY and "rolling back" lines never appear -- measured).

    It is a SEPARATE row from "no measurement instrument" rather than a fourth
    entry in that table, because it is not an instrument -- it changes what the
    firmware DOES, not what it reports -- and a row's name has to describe what
    it refuses or it stops being readable as coverage.

    TWO INDEPENDENT CHECKS, because the name and the content can diverge. The
    CMake plumbing adds "_flt" to the project name whenever the mask is set, so
    the name is the cheap check and the one that survives into
    esp_app_desc.project_name on the device. But the name comes from a CMake
    variable and the behaviour from a preprocessor macro, so a macro reaching
    the compiler by some other route would produce a faulty image with an
    innocent name. The format string is the check that cannot be fooled that
    way: with the mask at 0 the `if (0 & bits)` body is dead code and the
    string is not emitted at all, so its presence means the fault branch is
    live in this image. Verified on the pair built that day -- the good image
    does not contain it, the _flt image does.

    There is deliberately NO symbol check. `ota_health_report` is defined in
    both builds and nothing on the fault path is a separate symbol, so a symbol
    row here would pass on a faulty image -- the shape mutation M5 caught in
    the instrument table, and the reason that table's comment singles out
    `txab_trial`.
    """
    try:
        img = open(path, "rb").read()
    except OSError as e:
        row("no OTA fault injection in the image", None,
            "cannot read %s (%s)" % (os.path.basename(path), e),
            ref_missing=True)
        return

    marker = b"FAULT INJECT: suppressing report of"
    has_marker = marker in img
    row("no OTA fault injection in the image", not has_marker,
        ("image carries %r -- built with OTA_HEALTH_FAULT_INJECT, so it fails "
         "its health gate and rolls back on every boot; never flash this to "
         "the truck" % marker.decode("ascii")) if has_marker
        else "the fault-injection format string is absent, so that branch is "
             "dead code in this image")

    d = app_desc(path)
    if d is None:
        row("project name is not a rehearsal build", None,
            "no app descriptor to read the project name from",
            ref_missing=True)
        return
    project = d[1]
    flt = project.endswith("_flt")
    row("project name is not a rehearsal build", not flt,
        "project_name=%r%s"
        % (project, "  <-- the _flt suffix marks a fault-injected "
                    "rollback-rehearsal image" if flt else ""))


def _count_calls(rel, needle):
    """How many times `needle(` is called in main/<rel>, comments excluded.

    Crude on purpose. A real parser would be better and is not available here; what
    matters is that the count is CONSERVATIVE -- a commented-out call is skipped, so
    the row cannot pass because a real call hid behind a comment marker.
    """
    p = os.path.join(REPO, "main", rel)
    if not os.path.exists(p):
        return None
    n = 0
    for line in open(p, encoding="utf-8", errors="replace"):
        t = line.strip()
        if t.startswith("*") or t.startswith("//") or t.startswith("/*"):
            continue
        n += t.count(needle + "(")
    return n


def check_one_transmit_owner():
    """Spec 5.2 item 1: one transmit owner, asserted in the source.

    The image cannot show this -- a call is a call -- so it lives with the other
    source rows. Two callers of twai_transmit() are allowed and no more:

      can.c        can_send(), the client path (SLCAN / ELM327 / MQTT), which is
                   refused whenever gen_inhibit owns the bus (spec 3.2, review A3).
                   That refusal is ALSO a trip-7 correctness requirement: the
                   inhibit is judged complete from msgs_to_tx == 0, which proves our
                   frame went out only because nothing else can be queued behind it.
      gen_inhibit.c the scheduler's device HAL, the single site item 1 names.

    A third caller anywhere would break trip 7 silently, which is why this is a
    check and not a comment.
    """
    allowed = {"can.c": 1, "gen_inhibit.c": 1}
    bad = []
    total = 0
    for rel, limit in sorted(allowed.items()):
        n = _count_calls(rel, "twai_transmit")
        if n is None:
            row("one transmit owner", None, "main/%s is missing" % rel,
                ref_missing=True)
            return
        total += n
        if n > limit:
            bad.append("%s has %d (at most %d)" % (rel, n, limit))

    # Anything else under main/ must not call it at all.
    for f in sorted(os.listdir(os.path.join(REPO, "main"))):
        if not f.endswith(".c") or f in allowed or f == "gi_txabort_probe.c":
            continue
        n = _count_calls(f, "twai_transmit")
        if n:
            bad.append("%s calls it %d time(s) and must not" % (f, n))
            total += n

    row("one transmit owner (spec 5.2 item 1)", not bad,
        "; ".join(bad) if bad else
        "twai_transmit is called only by can_send() and the scheduler's device "
        "HAL (%d call sites)" % total)


def check_private_abort_confined():
    """The private HAL abort appears at most ONCE, and only in gen_inhibit.c.

    twai_ll_set_cmd_abort_tx() is below the public driver API. Spec 5.2 items 4 and
    9 sanction its use -- item 4's abort-and-recheck loop is built on it -- so it
    legitimately ships. But it is exactly the kind of call that spreads, and the
    measurement build existed specifically to keep it OUT of a shipping image, so
    the no-instrument rows say nothing about it.

    One site, in gen_inhibit.c, inside the critical section that makes the read and
    the write indivisible. Anywhere else is a finding.
    """
    hits = []
    for f in sorted(os.listdir(os.path.join(REPO, "main"))):
        if not f.endswith(".c") or f == "gi_txabort_probe.c":
            continue
        n = _count_calls(f, "twai_ll_set_cmd_abort_tx")
        if n:
            hits.append((f, n))

    # IS THE SCHEDULER WIRED IN? Keyed on gen_inhibit.c including gi_sched.h,
    # because that is what makes the abort site mandatory rather than merely
    # permitted. Before integration zero sites is correct; after it, zero would
    # mean the abort had been LOST -- an inhibit that can never preempt telemetry,
    # which is the whole point of section 5.2. So the requirement flips with the
    # integration instead of needing someone to remember to tighten it.
    gi = os.path.join(REPO, "main", "gen_inhibit.c")
    wired = False
    if os.path.exists(gi):
        for line in open(gi, encoding="utf-8", errors="replace"):
            if line.strip().startswith('#include "gi_sched.h"'):
                wired = True
                break

    if not wired:
        ok = (len(hits) == 0)
        row("the private TWAI abort is confined", ok,
            "no shipping source calls twai_ll_set_cmd_abort_tx, and the scheduler "
            "is not wired in yet" if ok else
            "called from %s, but gen_inhibit.c does not include gi_sched.h -- the "
            "private abort has no business here yet"
            % ", ".join("%s x%d" % h for h in hits))
        return

    ok = (len(hits) == 1 and hits[0][0] == "gen_inhibit.c" and hits[0][1] == 1)
    row("the private TWAI abort is confined", ok,
        "exactly one site, in gen_inhibit.c (the scheduler's device HAL)" if ok
        else ("the scheduler IS wired in but NOTHING calls "
              "twai_ll_set_cmd_abort_tx -- the abort has been lost, so an inhibit "
              "can never preempt telemetry"
              if not hits else
              "called from %s -- it may appear ONCE, in gen_inhibit.c, and "
              "nowhere else" % ", ".join("%s x%d" % h for h in hits)))


def check_abort_reason_parity(dbc_path):
    """Every gi_abort_t value has a VAL_ entry for diag_abort_reason.

    Counts VALUES, not names. The DBC's CM_ for this signal says APPEND ONLY --
    position is meaning, because reordering changes what every recorded log means --
    so if the enum has N values the VAL_ table must define 0..N-1. Names are
    deliberately not compared: the DBC shortens them, so a name check would be noisy
    and the row would get loosened. A missing VALUE is unambiguous.

    This exists because "DBC defines every diag page" covers MESSAGES and nothing
    covered this enum, so GI_ABORT_SKIPS = 15 reached the core while the DBC stopped
    at 14 -- a capture would have decoded it as a bare integer beside a column of
    names, which reads like a data problem rather than a missing definition.
    """
    hdr = os.path.join(REPO, "main", "gen_inhibit_core.h")
    if not os.path.exists(hdr):
        row("abort reasons are all in the DBC", None,
            "main/gen_inhibit_core.h is missing", ref_missing=True)
        return
    src = open(hdr, encoding="utf-8", errors="replace").read()
    i = src.find("GI_ABORT_NONE")
    j = src.find("} gi_abort_t;", i)
    if i < 0 or j < 0:
        row("abort reasons are all in the DBC", None,
            "could not find the gi_abort_t enum", ref_missing=True)
        return
    names = []
    for line in src[i:j].split("\n"):
        t = line.strip()
        if t.startswith("GI_ABORT_"):
            names.append(t.split(",")[0].split()[0])
    if not names:
        row("abort reasons are all in the DBC", None,
            "no GI_ABORT_ names parsed", ref_missing=True)
        return

    if not dbc_path or not os.path.exists(dbc_path):
        row("abort reasons are all in the DBC", None,
            "no DBC at %s" % dbc_path, ref_missing=True)
        return
    val_line = None
    for line in open(dbc_path, encoding="utf-8", errors="replace"):
        if line.startswith("VAL_ ") and "diag_abort_reason" in line:
            val_line = line
            break
    if val_line is None:
        row("abort reasons are all in the DBC", False,
            "the DBC has no VAL_ table for diag_abort_reason at all")
        return

    defined = set()
    toks = val_line.split()
    for k in range(len(toks) - 1):
        if toks[k].isdigit() and toks[k + 1].startswith('"'):
            defined.add(int(toks[k]))

    missing = [v for v in range(len(names)) if v not in defined]
    row("abort reasons are all in the DBC", not missing,
        "the enum has %d values and the DBC defines %d; MISSING %s (%s)"
        % (len(names), len(defined), missing,
           ", ".join(names[v] for v in missing if v < len(names)))
        if missing else
        "all %d gi_abort_t values have a VAL_ entry" % len(names))


def check_lateness_has_a_detector():
    """Something must actually call gi_on_inhibit_skip().

    Spec 7 trip 7 as amended moved lateness detection out of the core: the core no
    longer trips when an inhibit is still outstanding at the VCM's next 0x051, and
    the transmit scheduler is what withdraws the frame and reports a skip. Between
    those two changes there is a window in which the core has stopped checking and
    nothing has started -- and an image built there detects NO late or withdrawn
    inhibit at all. No trip, no skip, no count. That is worse than the build before
    the rework began.

    So this row fails until gen_inhibit.c calls gi_on_inhibit_skip(), which refuses
    every image built in that window. It replaces remembering, and the bench counts:
    the bench is exactly where an image gets flashed without thinking.
    """
    gi = os.path.join(REPO, "main", "gen_inhibit.c")
    core = os.path.join(REPO, "main", "gen_inhibit_core.c")
    if not (os.path.exists(gi) and os.path.exists(core)):
        row("lateness has a detector", None, "main sources missing",
            ref_missing=True)
        return

    def calls(path, needle):
        for line in open(path, encoding="utf-8", errors="replace"):
            t = line.strip()
            if t.startswith("*") or t.startswith("//") or t.startswith("/*"):
                continue
            if needle + "(" in t:
                return True
        return False

    core_trips = "GI_ABORT_TX_LATE," in open(
        core, encoding="utf-8", errors="replace").read().replace(" ", "")
    shim_reports = calls(gi, "gi_on_inhibit_skip")

    # The core's own TX_LATE abort: present only if something still raises it.
    raises_tx_late = False
    for line in open(core, encoding="utf-8", errors="replace"):
        t = line.strip()
        if t.startswith("*") or t.startswith("//"):
            continue
        if "inhibit_abort(" in t and "GI_ABORT_TX_LATE" in t:
            raises_tx_late = True

    ok = shim_reports or raises_tx_late
    row("lateness has a detector", ok,
        "gen_inhibit.c calls gi_on_inhibit_skip()" if shim_reports else
        ("the core still raises GI_ABORT_TX_LATE itself" if raises_tx_late else
         "NOTHING DETECTS A LATE OR WITHDRAWN INHIBIT. The core's TX_LATE abort is "
         "gone (spec 7 trip 7, amended) and gen_inhibit.c does not call "
         "gi_on_inhibit_skip(), so this image has no lateness check of any kind -- "
         "no trip, no skip, no count. Do not flash it anywhere, bench included."))
    del core_trips


def check_sources_older_than_image(path):
    """Row 22: no tracked source may be newer than the image.

    WHY THIS EXISTS AND WHY THE VERSION STRING IS NOT ENOUGH. GIT_SHA and the app
    descriptor both come from `git describe --dirty` inside execute_process() in
    the top-level CMakeLists.txt, which CMake evaluates at CONFIGURE time. Editing
    a .c file and rebuilding does not re-run CMake, so the version string stays
    exactly as it was -- clean, with no -dirty marker -- while the image contains
    uncommitted code. Editing a CMakeLists DOES force a re-run, so the marker
    works or not depending on which files changed. Measured on a real artifact
    2026-09-27: build-txabort/wican-fw_obd_bff3c21.bin reported version 'bff3c21'
    while carrying code that existed only in an uncommitted diff, and the
    measurement records it produced claimed that commit.

    Mtimes are independent of every version string and of whether CMake re-ran,
    and the failure names the offending files instead of being a silent pass.
    This is the check tools-reference.md already prescribes for the interposer
    board, which has no git checkout to ask.
    """
    if not os.path.exists(path):
        row("sources older than the image", None, "no image", ref_missing=True)
        return
    try:
        img_mtime = os.path.getmtime(path)
    except OSError as e:
        row("sources older than the image", None, "cannot stat the image (%s)" % e,
            ref_missing=True)
        return
    r = subprocess.run(("git", "-C", REPO, "ls-files", "main", "components",
                        "CMakeLists.txt", "sdkconfig",
                        "wican_partitions_table.csv"),
                       capture_output=True, text=True)
    if r.returncode != 0:
        row("sources older than the image", None,
            "git ls-files failed (%d)" % r.returncode, ref_missing=True)
        return
    newer = []
    for rel in r.stdout.split("\n"):
        rel = rel.strip()
        if not rel:
            continue
        p = os.path.join(REPO, rel)
        try:
            if os.path.getmtime(p) > img_mtime + 1.0:
                newer.append(rel)
        except OSError:
            continue
    row("sources older than the image", not newer,
        "%d tracked source(s) are NEWER than the image, so it was not built from "
        "them: %s%s" % (len(newer), ", ".join(sorted(newer)[:6]),
                        " ..." if len(newer) > 6 else "")
        if newer else
        "every tracked source under main/, components/ and the build files is "
        "older than the image")


def check_prod_record(path):
    """Spec 12.3 item 3: a truck image must carry its prod-build record.

    `tools/prod_build.py` writes `<image>.prod.json` after refusing to build on
    a dirty tree or against a modified `managed_components/`, and after forcing
    a reconfigure. The record is the only evidence that those refusals ran, and
    the spec says the build-output checks refuse an image that has no such
    record.

    WHY THE RECORD RATHER THAN RE-CHECKING HERE. The conditions are about the
    MOMENT OF THE BUILD and cannot be reconstructed afterwards: the tree can be
    committed after a dirty build, a component can be restored after being
    modified, and `git describe` has already been baked in at configure time.
    Re-running the checks now would pass on an image that was built when they
    would have failed -- which is the same trap `check_sources_older_than_image`
    exists for, one level up.

    NO RECORD IS NO REFERENCE, NOT A FAILURE (spec 12.3 item 3, changed
    2026-10-07, user). An image with no record is "not a prod build". A bench
    image is normally built on a dirty tree, so a FAIL here fired on every
    bench run and meant nothing. The truck pre-flash checklist is what refuses
    such an image (bring-up row 1.3b), not this row. A record that IS present
    but unreadable or disagrees with the image is still a FAIL: that is a
    claim to be a prod build that does not hold.
    """
    rec_path = path + ".prod.json"
    if not os.path.exists(rec_path):
        row("prod-build record present", None,
            "not a prod build: no %s beside the image, so nothing establishes "
            "it was built on a clean tree with verified components after a "
            "reconfigure. A truck image must be built with tools/prod_build.py "
            "(spec 12.3 item 3); the pre-flash checklist refuses this one."
            % os.path.basename(rec_path), ref_missing=True)
        return
    try:
        with open(rec_path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception as e:
        row("prod-build record present", False,
            "%s is unreadable: %s" % (os.path.basename(rec_path), e))
        return

    data = open(path, "rb").read()
    got_md5 = hashlib.md5(data).hexdigest()
    problems = []
    if rec.get("md5") != got_md5:
        problems.append("md5 %s != recorded %s" % (got_md5, rec.get("md5")))
    if rec.get("size") != len(data):
        problems.append("size %d != recorded %s" % (len(data), rec.get("size")))
    if not rec.get("clean_tree"):
        problems.append("record does not assert a clean tree")
    if not rec.get("reconfigured"):
        problems.append("record does not assert a reconfigure")
    for name, c in (rec.get("components") or {}).items():
        if c.get("problems"):
            problems.append("component %s had %d problem(s) at build time"
                            % (name, len(c["problems"])))
    row("prod-build record present", not problems,
        "; ".join(problems) if problems else
        "built from %s on a clean tree, components verified, md5 %s"
        % ((rec.get("commit") or "?")[:7], got_md5))


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

    b = pick_bin(args.bin)

    # The build directory is the image's own parent, so every config row below is
    # about the image that was handed in rather than about whatever build/ holds.
    build_dir = (os.path.dirname(os.path.abspath(b)) if b is not None
                 else os.path.join(REPO, "build"))
    cfg, cfg_src = load_sdkconfig(build_dir)

    img_ver = None
    if b is not None:
        d = app_desc(b)
        if d:
            img_ver = d[0]

    # DOES THE CONFIG ON DISK DESCRIBE THIS IMAGE? The build directory records
    # what it produced, so the two can be compared instead of assumed.
    bd_ver = build_dir_version(build_dir)
    trusted = True
    why = ""
    if img_ver is None:
        trusted = False
        why = "no version could be read from the image"
    elif bd_ver is None:
        trusted = False
        why = "%s/project_description.json is missing, so the config cannot "\
              "be tied to this image" % os.path.relpath(build_dir, REPO)
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

    check_partitions(build_dir)
    check_diag(args.dbc)
    check_schema_doc(args.schema_doc)
    check_source()
    check_one_transmit_owner()
    check_lateness_has_a_detector()
    check_abort_reason_parity(args.dbc)
    check_private_abort_confined()
    check_image_identity(img_ver)
    if b is not None:
        check_no_instrument(b)
        check_not_rollback_rehearsal(b)
        check_sources_older_than_image(b)
        check_prod_record(b)
    else:
        row("no measurement instrument in the image", None,
            "no image to inspect", ref_missing=True)
        row("no OTA fault injection in the image", None,
            "no image to inspect", ref_missing=True)
        row("prod-build record present", None,
            "no image to inspect", ref_missing=True)

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
              "them is a way to brick a device with no EXTERNAL USB port and no "
              "factory reset: recovery is bootloader rollback, or taking "
              "the case off to reach the C3's native USB "
              "(user, 2026-09-27)." % bad)
    else:
        print("every check with a reference passed")
    if noref:
        print("%d check(s) had NO REFERENCE and proved nothing. They are not "
              "passes." % noref)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
