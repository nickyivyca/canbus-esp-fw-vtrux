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
# The rollback-rehearsal marker as a "_"-delimited token of project_name.
FLT_TOKEN = re.compile(r"_flt(?=_|$)")

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

# The DBC and the schema doc live in the analysis project (reverse-it), not in the
# firmware repo, and that folder is somewhere else on every machine. So the
# defaults come from $REVERSE_IT_DIR (projects/vtrux/ under it), and with it unset
# there are none. A missing one is reported as NO REFERENCE, never as a pass, and
# never looked for at a guessed path (harness-defects row, 2026-10-24).
def _proj():
    root = os.environ.get("REVERSE_IT_DIR", "").strip()
    return os.path.join(os.path.expanduser(root), "projects", "vtrux") if root else None


_PROJ = _proj()
DEFAULT_DBC = os.path.join(_PROJ, "vtrux-wican-diag.dbc") if _PROJ else None
DEFAULT_SCHEMA_DOC = (os.path.join(_PROJ, "notes", "artifacts", "gen-inhibit", "wican_diag_schema.md")
                      if _PROJ else None)


def _no_ref(what, path, flag):
    """The detail for a reference that is not there: where it was looked for, and how to give one."""
    how = "pass %s, or set REVERSE_IT_DIR to the reverse-it root" % flag
    return "no %s at %s -- %s" % (what, path, how) if path else "no %s given: %s" % (what, how)


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
            _no_ref("DBC", dbc_path, "--dbc"), ref_missing=True)
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
    # mode's timing measurement, not a status page. Until 2026-10-12 the DBC did
    # not define it; since then (user) it does, as WICAN_Probe, with its layout
    # in wican_diag_schema.md, but it is still not a diag frame and not versioned
    # by diag_schema_ver, so this row only reports whether the DBC has it. The
    # first version of this check demanded every GI_*_ID including the probe and
    # failed a correct DBC -- an over-claim of exactly the kind config_vs_spec.py
    # was corrected for.
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
    # "_flt" as a whole suffix token ANYWHERE in the name, not only at the end:
    # nothing makes it the last suffix, and on the 2026-10-08 diag branch a
    # "_DIAG" appended after it would have hidden it from an endswith().
    flt = FLT_TOKEN.search(project) is not None
    row("project name is not a rehearsal build", not flt,
        "project_name=%r%s"
        % (project, "  <-- the _flt suffix marks a fault-injected "
                    "rollback-rehearsal image" if flt else ""))


RT_TOKEN = re.compile(r"_rt(?=_|$)")
RESTART_TEST_MARKERS = (b"/gi_quiesce", b'{"build":"restart-test"', b"expected ?crash=illegal|abort")
RESTART_TEST_SYMS = ("gi_restart_test_register", "gi_restart_test_pre_install", "gi_restart_record_get")


def check_no_restart_test(path):
    """This image must NOT be the restart test build (GI_TEST_RESTART_QUIESCE).

    ADDED 2026-10-13 (spec 12.3 item 3, the refusal row for a test-only switch).
    The test build adds GET/POST /gi_quiesce: the restart record, and a crash
    trigger that panics the device 300 ms after a POST. That is what spec 12.4's
    "no error frames at a restart" needs on the bench, and a remotely reachable
    crash on a truck. The quiesce itself is in both builds; only the record and
    the trigger are test-only.

    The markers were picked by comparing images of 42a860d built with and without
    the switch, both from a clean tree (the test image's filename is the same as
    the default's, `wican-fw_obd_42a860d_man.bin`, so only content separates
    them). Image: the URI, the record's JSON head, and the crash-argument error
    text. ELF: three non-static functions, the shape the instrument table's M5
    note asks for -- a static that the compiler can inline away would claim
    coverage it does not have.
    """
    try:
        img = open(path, "rb").read()
    except OSError as e:
        row("no restart test feature in the image", None,
            "cannot read %s (%s)" % (os.path.basename(path), e), ref_missing=True)
        return
    hits = [m.decode("latin-1") for m in RESTART_TEST_MARKERS if m in img]
    row("no restart test feature in the image", not hits,
        "image carries %s -- built with GI_TEST_RESTART_QUIESCE (a crash trigger "
        "on /gi_quiesce); never flash this to the truck" % ", ".join(repr(h) for h in hits)
        if hits else "none of the %d restart-test markers are in the image"
        % len(RESTART_TEST_MARKERS))

    # The name, from a3394ca on: CMake appends "_rt" to the project name of a
    # test build. A whole "_"-delimited token anywhere, as the _flt row reads it.
    d = app_desc(path)
    if d is None:
        row("project name is not a restart test build", None,
            "no app descriptor to read the project name from", ref_missing=True)
    else:
        rt = RT_TOKEN.search(d[1]) is not None
        row("project name is not a restart test build", not rt,
            "project_name=%r%s" % (d[1], "  <-- the _rt suffix marks a restart test build" if rt else ""))

    elf = os.path.splitext(path)[0] + ".elf"
    if not os.path.exists(elf):
        row("no restart test symbols in the ELF", None,
            "no ELF beside the image, so the symbol check has no reference",
            ref_missing=True)
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from elf_checks import find_nm
    except ImportError as e:
        row("no restart test symbols in the ELF", None,
            "cannot import elf_checks.find_nm (%s)" % e, ref_missing=True)
        return
    exe = find_nm()
    if exe is None:
        row("no restart test symbols in the ELF", None,
            "no riscv32-esp-elf-nm found, so the symbol check has no reference",
            ref_missing=True)
        return
    r = subprocess.run((exe, "--defined-only", elf), capture_output=True, text=True)
    if r.returncode != 0:
        row("no restart test symbols in the ELF", None,
            "nm exited %d on %s" % (r.returncode, os.path.basename(elf)), ref_missing=True)
        return
    defined = {ln.split()[-1] for ln in r.stdout.splitlines() if len(ln.split()) >= 3}
    shits = [s for s in RESTART_TEST_SYMS if s in defined]
    row("no restart test symbols in the ELF", not shits,
        "ELF defines %s" % ", ".join(shits) if shits
        else "none of the %d restart-test symbols are defined" % len(RESTART_TEST_SYMS))


# ---- stock features, off in this product (spec 3.2, 12.3 item 3; 2026-10-16) ----
#
# (switch, row, ELF symbols, ELF symbol prefixes). The symbols are the ones nm
# reported on the implementor's switch-on builds of e3d9437 (his self-check), and
# the rows were then shown failing on the tester's own switch-on builds and passing
# on the product build (test_refusal_pairs.py). Non-static functions only, the M5
# lesson in check_no_instrument: a static the compiler can inline away would claim
# coverage it does not have. A prefix catches the esp-mqtt library, whose own
# function names are not ours to list one by one.
STOCK_FEATURES = (
    ("WICAN_FEATURE_MQTT", "no MQTT feature in the ELF",
     ("mqtt_init", "mqtt_publish", "mqtt_connected"), ("esp_mqtt_client_", "mqtt_msg_")),
    ("WICAN_FEATURE_ELM327", "no ELM327 feature in the ELF",
     ("elm327_init", "elm327_process_cmd", "elm327_ready_to_receive", "elm327_get_identifier",
      "elm327_can_log"), ()),
    ("WICAN_FEATURE_AUTOPID", "no AutoPID feature in the ELF",
     ("autopid_init", "autopid_parser", "autopid_data_handler", "autopid_find_standard_pid"), ()),
)
# The image half (spec 12.3 judges the image and the ELF): strings in every
# image the tester built of e3d9437 with that feature on and in none built with it
# off (string diffs of the switch-on images against the product image, and of
# AutoPID's against MQTT+ELM327; test_refusal_pairs.py shows each row failing and
# passing). RealDash adds no string of its own, so it has no image row.
STOCK_IMAGE_MARKERS = (
    ("WICAN_FEATURE_MQTT", "no MQTT feature in the image",
     (b"MQTT_EVENTS", b"esp_mqtt_client_init", b"mqtt_parse_data")),
    ("WICAN_FEATURE_ELM327", "no ELM327 feature in the image",
     (b"ELM327 v1.3a", b"elm327_process_cmd", b"elm327_set_protocol")),
    ("WICAN_FEATURE_AUTOPID", "no AutoPID feature in the image",
     (b"autopid_task", b"/autopid_data", b"Autopid Task Started")),
)
REALDASH_ROW = "no boot-time bus enable in app_main"
BUS_STATE_ROW = "bus state changed only by gen_inhibit and the OTA handler (ELF call sites)"
# Spec 3.2: in every mode only gen_inhibit and the OTA handler bring the bus up or
# down or flush it, and the OTA handler takes it only through the worker (8.1 rows 4
# and 8), so it may tear down but never bring up. Callers by function name, as
# objdump reports them; can_flush_rx and can_send are not in the product image at all.
BUS_STATE_CALLERS = {
    "can_enable": ("apply_mode",),
    "can_disable": ("apply_mode", "gen_inhibit_task", "upload_post_handler"),
}
BUS_STATE_ABSENT = ("can_flush_rx", "can_send")
_FUNC_RE = re.compile(r"^[0-9a-f]+ <([^>]+)>:\s*$")
_CALL_MNEMONICS = ("j", "jal", "jalr", "c.j", "c.jal", "c.jalr", "tail", "call")


def defined_symbols(nm_text):
    """The names nm --defined-only lists (address, type, name)."""
    return {ln.split()[-1] for ln in nm_text.splitlines() if len(ln.split()) >= 3}


def call_sites(disasm, targets):
    """{target: {caller: n}} from objdump -d text: every jump or call whose target is
    exactly <target> (not <target+0x..>, a branch inside it), by the function it is in."""
    out = {t: {} for t in targets}
    fn = None
    for ln in disasm.splitlines():
        m = _FUNC_RE.match(ln)
        if m:
            fn = m.group(1)
            continue
        parts = ln.split("\t")
        if len(parts) < 3 or not ln.rstrip().endswith(">"):
            continue
        mnem = parts[-2].strip() if len(parts) >= 4 else parts[1].strip()
        if mnem not in _CALL_MNEMONICS:
            continue
        tgt = ln.rstrip()[ln.rstrip().rfind("<") + 1:-1]
        if tgt in out and fn is not None and fn != tgt:
            out[tgt][fn] = out[tgt].get(fn, 0) + 1
    return out


def _elf_tools():
    """(nm, objdump) paths, or (None, why)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from elf_checks import find_nm
    except ImportError as e:
        return None, "cannot import elf_checks.find_nm (%s)" % e
    nm = find_nm()
    if nm is None:
        return None, "no riscv32-esp-elf-nm found"
    od = os.path.join(os.path.dirname(nm), os.path.basename(nm).replace("-nm", "-objdump"))
    if os.path.dirname(nm) == "":
        od = nm.replace("-nm", "-objdump")
    return (nm, od), None


def stock_image_rows(img):
    """[(row, ok, detail)] for the stock-feature image rows, from the image bytes."""
    out = []
    for flag, name, markers in STOCK_IMAGE_MARKERS:
        hits = [m.decode("latin-1") for m in markers if m in img]
        out.append((name, not hits, "image carries %s -- built with %s, a stock feature off in this product"
                    % (", ".join(hits), flag) if hits
                    else "none of %s's %d markers are in the image" % (flag, len(markers))))
    return out


def stock_rows(defined, sites):
    """[(row, ok, detail)] for the stock-feature rows, from the ELF's defined
    symbols and its call sites (call_sites over can_enable, can_disable and the
    absent ones). Pure, so it is tested without a build."""
    out = []
    for flag, name, syms, prefixes in STOCK_FEATURES:
        hits = [x for x in syms if x in defined]
        hits += sorted(x for x in defined if any(x.startswith(p) for p in prefixes))
        out.append((name, not hits, "ELF defines %s -- built with %s, a stock feature off in this "
                    "product" % (", ".join(hits[:6]) + (" and %d more" % (len(hits) - 6) if len(hits) > 6 else ""),
                                 flag) if hits else "none of %s's %d symbols%s are defined"
                    % (flag, len(syms), (" or %s* names" % "/".join(prefixes)) if prefixes else "")))
    n = sites.get("can_enable", {}).get("app_main", 0)
    out.append((REALDASH_ROW, n == 0,
                "app_main calls can_enable at %d site(s) -- a boot-time bus enable (the RealDash, "
                "MQTT, ELM327 or AutoPID boot branch)" % n if n else "app_main never calls can_enable"))
    bad = []
    for fn, allowed in sorted(BUS_STATE_CALLERS.items()):
        for caller, k in sorted(sites.get(fn, {}).items()):
            if caller not in allowed:
                bad.append("%s from %s (%d)" % (fn, caller, k))
    present = [x for x in BUS_STATE_ABSENT if x in defined]
    seen = "; ".join("%s from %s" % (fn, ", ".join(sorted(sites.get(fn, {})))) for fn in sorted(BUS_STATE_CALLERS))
    out.append((BUS_STATE_ROW, not bad and not present,
                ("other callers: %s" % "; ".join(bad) if bad else "")
                + ("; " if bad and present else "")
                + ("in the image: %s" % ", ".join(present) if present else "")
                if bad or present else "%s; %s not in the image" % (seen, " and ".join(BUS_STATE_ABSENT))))
    return out


def check_no_stock_features(path):
    """Spec 3.2 and 12.3 item 3 (2026-10-16): no stock feature that is off in this
    product is in the image, and only gen_inhibit and the OTA handler change bus state.

    Judged from the image's bytes (STOCK_IMAGE_MARKERS) and the ELF beside it --
    its symbols and the call sites objdump finds -- never from the switches, so a switch set off but not wired to its code
    is still caught. RealDash has no symbol of its own; its boot branch is a
    can_enable() call in app_main, so its row counts those call sites (0 in the
    product, one per enabled boot branch: implementor's objdump, e3d9437).
    """
    try:
        img = open(path, "rb").read()
    except OSError as e:
        img = None
        for _f, n, _m in STOCK_IMAGE_MARKERS:
            row(n, None, "cannot read the image (%s)" % e, ref_missing=True)
    if img is not None:
        for n, ok, detail in stock_image_rows(img):
            row(n, ok, detail)
    names = [r[1] for r in STOCK_FEATURES] + [REALDASH_ROW, BUS_STATE_ROW]
    elf = os.path.splitext(path)[0] + ".elf"
    if not os.path.exists(elf):
        for n in names:
            row(n, None, "no ELF beside the image, so the check has no reference", ref_missing=True)
        return
    tools, why = _elf_tools()
    if tools is None:
        for n in names:
            row(n, None, why, ref_missing=True)
        return
    r = subprocess.run((tools[0], "--defined-only", elf), capture_output=True, text=True)
    d = subprocess.run((tools[1], "-d", "--no-show-raw-insn", elf), capture_output=True, text=True)
    if r.returncode != 0 or d.returncode != 0 or not d.stdout:
        for n in names:
            row(n, None, "nm exit %d / objdump exit %d on %s" % (r.returncode, d.returncode,
                os.path.basename(elf)), ref_missing=True)
        return
    sites = call_sites(d.stdout, tuple(BUS_STATE_CALLERS) + BUS_STATE_ABSENT)
    for n, ok, detail in stock_rows(defined_symbols(r.stdout), sites):
        row(n, ok, detail)


def check_switches_classified(build_dir=None):
    """Spec 12.3 item 3 (added 2026-10-13, user): every build-time switch is
    classified, and an unclassified one fails the build checks.

    The CMake files come from the record of the image's own build directory
    (build_switches.read_record); a record that cannot be read or trusted
    fails the row rather than falling back to a list.

    The scan and the table are in build_switches.py, which tests itself
    (test_build_switches.py). These rows report its three findings separately,
    because they mean different things: an unlisted symbol is a switch nobody
    has decided about; a stale entry is coverage that no longer covers anything
    (a renamed switch leaves one behind); a malformed entry is a decision with
    no reason or no refusal row.
    """
    try:
        import build_switches as BS
    except ImportError as e:
        row("every build-time switch classified", None,
            "cannot import build_switches (%s)" % e, ref_missing=True)
        return
    try:
        rec = BS.read_record(REPO, build_dir or os.path.join(REPO, "build"))
    except BS.RecordError as e:
        row("every build-time switch classified", False,
            "the build's record of its CMake files cannot be used: %s" % e)
        return
    found = BS.scan(REPO, rec)
    unlisted, stale, bad = BS.classify(found)
    counts = {}
    for s in found:
        if s in BS.SWITCHES:
            c = BS.SWITCHES[s][0]
            counts[c] = counts.get(c, 0) + 1
    row("every build-time switch classified", not unlisted,
        ("UNLISTED: " + "; ".join("%s (%s)" % (s, ", ".join(found[s][:2])) for s in unlisted))
        if unlisted else "%d symbols tested, all listed (%s)"
        % (len(found), ", ".join("%d %s" % (n, c) for c, n in sorted(counts.items()))))
    row("no stale switch entries", not stale,
        "listed but no longer tested: " + ", ".join(stale) if stale
        else "every listed switch is still tested")
    row("switch table well-formed", not bad, "; ".join(bad) if bad
        else "every entry has a class, a reason, and (test-only) a refusal row")


APP_ELF_SHA_OFF = 0xB0           # esp_app_desc_t.app_elf_sha256, at 0x90 in the descriptor
ROWS_FILE = "vtrux_project_rows.json"


def image_hash(path):
    """The ELF's SHA-256 the image embeds (shared/tools SPEC R1), hex, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(APP_ELF_SHA_OFF)
            b = fh.read(32)
    except OSError:
        return None
    return b.hex() if len(b) == 32 and app_desc(path) is not None else None


def judge_rule(rule, img, syms, project, produced):
    """(ok, detail) for one of a switch's refusal rules, as shared/tools R20 reads
    them. ok None: no reference (no image, no ELF or no nm). syms: the ELF's
    defined symbols or None; produced: {row name: ok} of the rows this run wrote."""
    (k, v), = rule.items()
    if k == "project_row":
        if v not in produced:
            return False, "row %r was not produced by this run" % v
        return True, "row %r produced" % v
    if k == "bin_strings_absent":
        if img is None:
            return None, "no image"
        hits = [x for x in v if x.encode("latin-1") in img]
        return not hits, "image carries %s" % ", ".join(repr(h) for h in hits) if hits else "%d strings absent" % len(v)
    if k == "project_name_token_absent":
        if project is None:
            return None, "no project name"
        hit = v.strip("_") in project.split("_")
        return not hit, "project_name %r%s" % (project, " has the token %r" % v if hit else "")
    if syms is None:
        return None, "no ELF symbols"
    if k == "elf_symbols_absent":
        hits = sorted(x for x in v if x in syms)
    else:
        hits = sorted(x for x in syms if any(x.startswith(pre) for pre in v))
    return not hits, "ELF defines %s" % ", ".join(hits[:3]) if hits else "none defined"


def check_test_only_refused(path=None):
    """Each test-only switch's, and each stock feature's, refusal rules pass on
    this image.

    Run AFTER the image rows. The rules are the table's (vtrux_checks.json,
    "build_switches", moved there 2026-10-24 so the shared R20 check reads the
    same table): strings absent from the image, symbols absent from the ELF,
    a project-name token absent, or a row this run produced. A project_row rule
    whose row was never produced -- renamed, or never written -- would otherwise
    be a refusal on paper only. Evaluated here as well as by the shared check, so
    this gate stays as strong as it was until gen-inhibit's release builds move
    to the shared tooling (reviewer, 2026-10-24).
    """
    try:
        import build_switches as BS
    except ImportError as e:
        for label in ("test-only switch", "stock feature off in this product"):
            row("every %s has a refusal row" % label, None,
                "cannot import build_switches (%s)" % e, ref_missing=True)
        return
    produced = {r[0]: r[1] for r in rows}
    img = syms = project = None
    if path is not None:
        try:
            img = open(path, "rb").read()
        except OSError:
            img = None
        d = app_desc(path) if img is not None else None
        project = d[1] if d else None
        elf = os.path.splitext(path)[0] + ".elf"
        exe = None
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from elf_checks import find_nm
            exe = find_nm()
        except ImportError:
            pass
        if exe and os.path.exists(elf):
            r = subprocess.run((exe, "--defined-only", elf), capture_output=True, text=True)
            if r.returncode == 0:
                syms = defined_symbols(r.stdout)
    for cls, label in ((BS.TEST, "test-only switch"), (BS.STOCK, "stock feature off in this product")):
        entries = sorted((s, e) for s, e in BS.SWITCHES.items() if e[0] == cls)
        bad, noref = [], []
        for s, e in entries:
            rules = e[3] if isinstance(e[3], list) else []
            if not rules:
                bad.append("%s: no refusal rules" % s)
            for rule in rules:
                if not (isinstance(rule, dict) and len(rule) == 1):
                    bad.append("%s: malformed rule %r" % (s, rule))
                    continue
                ok, detail = judge_rule(rule, img, syms, project, produced)
                if ok is None:
                    noref.append("%s: %s" % (s, detail))
                elif not ok:
                    bad.append("%s: %s" % (s, detail))
        if bad:
            row("every %s has a refusal row" % label, False, "; ".join(bad))
        elif noref:
            row("every %s has a refusal row" % label, None, "no reference: " + "; ".join(noref),
                ref_missing=True)
        else:
            row("every %s has a refusal row" % label, True,
                "all %d pass their table rules on this image" % len(entries))


def write_project_rows(path):
    """build/vtrux_project_rows.json for the shared R20 check (Q8, reviewer
    2026-10-24): this run's rows, PASS or FAIL (a NO REF is not a pass), bound to
    the image they judged by its image hash. Written beside the image, in its
    build directory. Returns the file's path, or None with no image hash."""
    h = image_hash(path)
    if h is None:
        return None
    verdicts = {}
    for name, ok, _detail, ref_missing in rows:
        v = "PASS" if ok is True and not ref_missing else "FAIL"
        verdicts[name] = "FAIL" if verdicts.get(name) == "FAIL" else v
    out = os.path.join(os.path.dirname(os.path.abspath(path)), ROWS_FILE)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"image_hash": h, "rows": verdicts}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return out


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

      can.c        can_send(), the one way any other task can transmit, which is
                   refused whenever gen_inhibit owns the bus (spec 3.2, review A3).
                   Stock reached it from SLCAN, ELM327 and MQTT; since 2026-10-16
                   the SLCAN parser is disabled on every channel and the ELM327
                   and MQTT CAN paths are given up (spec 3.2), but the refusal
                   stays whoever calls it.
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
            _no_ref("DBC", dbc_path, "--dbc"), ref_missing=True)
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

    A NEWER FILE IS NOT ALWAYS A CHANGED ONE (2026-10-09, reviewer). A branch
    switch rewrites every file that differs between the branches, so switching
    away and back leaves files newer than the image with exactly the content it
    was built from, and the row failed on them. So each newer file is compared,
    through git's own hashing (line-ending filters included), with the same
    path at the commit the image names:
      - content differs, or the image's commit cannot be read (no version,
        `-dirty`, not in this repo)          -> FAIL, as before;
      - content identical                    -> NO REFERENCE, not a pass: an
        edit, a build and then a revert looks exactly the same, and mtimes
        cannot tell it from a branch switch.
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
    d = app_desc(path)
    ver = d[0] if d else None
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
    changed = [rel for rel in newer if not _blob_same(rel, ver)]
    same = [rel for rel in newer if rel not in changed]
    if changed:
        row("sources older than the image", False,
            "%d tracked source(s) are NEWER than the image and differ from (or cannot "
            "be compared with) %s, so it was not built from them: %s%s"
            % (len(changed), ver if _commitish(ver) else "any commit the image names",
               ", ".join(sorted(changed)[:6]), " ..." if len(changed) > 6 else ""))
    elif same:
        row("sources older than the image", None,
            "%d tracked source(s) are newer than the image but byte-identical to %s "
            "(a branch switch, or an edit-build-revert -- mtimes cannot tell which): "
            "%s%s" % (len(same), ver, ", ".join(sorted(same)[:6]),
                      " ..." if len(same) > 6 else ""),
            ref_missing=True)
    else:
        row("sources older than the image", True,
            "every tracked source under main/, components/ and the build files is "
            "older than the image")


def _commitish(ver):
    """A version string that names one commit: hex, no -dirty."""
    return bool(ver) and re.fullmatch(r"[0-9a-f]{7,40}", ver) is not None


def _blob_same(rel, ver):
    """Is the working file's content the blob at `ver`:`rel`? False if unknowable."""
    if not _commitish(ver):
        return False
    a = subprocess.run(("git", "-C", REPO, "rev-parse", "--verify", "-q",
                        "%s:%s" % (ver, rel)), capture_output=True, text=True)
    b = subprocess.run(("git", "-C", REPO, "hash-object", "--path", rel,
                        os.path.join(REPO, rel)), capture_output=True, text=True)
    return (a.returncode == 0 and b.returncode == 0
            and a.stdout.strip() == b.stdout.strip() != "")


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
            _no_ref("schema doc", doc_path, "--schema-doc"), ref_missing=True)
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


def source_fw_version(path=None):
    """DIAG_FW_VERSION as main/gen_inhibit.c defines it, or None."""
    path = path or os.path.join(REPO, "main", "gen_inhibit.c")
    if not os.path.exists(path):
        return None
    m = re.search(r"#define\s+DIAG_FW_VERSION\s+(\d+)", open(path, encoding="utf-8").read())
    return int(m.group(1)) if m else None


def doc_latest_fw_rev(text):
    """The highest diag_fw_ver in the schema doc's rev table, or None.

    The table is the one whose header row starts "| diag_fw_ver | git rev |";
    its rows run until the first line that is not a table line. The highest
    number is taken rather than the last row, so the row order cannot matter.
    """
    lines = text.splitlines()
    try:
        i = next(k for k, x in enumerate(lines)
                 if re.match(r"\|\s*diag_fw_ver\s*\|\s*git rev\s*\|", x))
    except StopIteration:
        return None
    revs = []
    for x in lines[i + 1:]:
        if not x.startswith("|"):
            break
        m = re.match(r"\|\s*(\d+)\s*\|", x)
        if m:
            revs.append(int(m.group(1)))
    return max(revs) if revs else None


def check_fw_version(doc_path, src_path=None):
    """Row 10's other half: DIAG_FW_VERSION against the schema doc's rev table.

    Added 2026-10-09 (reviewer): the host suite cannot see this value, because
    the core's default config carries fw_version 1 and no scenario overrides
    it, so every golden reads 01 and only a device read checked it. A bump
    the doc does not record, or a doc row the source never reached, fails.
    The value can also be set with -DDIAG_FW_VERSION (the define is under
    #ifndef); this row reads the source default, which is what a normal build
    uses.
    """
    src = source_fw_version(src_path)
    if not doc_path or not os.path.exists(doc_path):
        row("DIAG_FW_VERSION is the schema doc's latest rev", None,
            _no_ref("schema doc", doc_path, "--schema-doc"), ref_missing=True)
        return
    doc = doc_latest_fw_rev(open(doc_path, encoding="utf-8").read())
    if src is None or doc is None:
        row("DIAG_FW_VERSION is the schema doc's latest rev", None,
            "could not read %s" % ("DIAG_FW_VERSION in main/gen_inhibit.c"
                                   if src is None else "the doc's rev table"),
            ref_missing=True)
        return
    row("DIAG_FW_VERSION is the schema doc's latest rev", src == doc,
        "main/gen_inhibit.c defines %d, the doc's rev table ends at %d" % (src, doc))


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
    check_fw_version(args.schema_doc)
    check_source()
    check_one_transmit_owner()
    check_lateness_has_a_detector()
    check_abort_reason_parity(args.dbc)
    check_private_abort_confined()
    check_switches_classified(build_dir)
    check_image_identity(img_ver)
    if b is not None:
        check_no_instrument(b)
        check_not_rollback_rehearsal(b)
        check_no_restart_test(b)
        check_no_stock_features(b)
        check_sources_older_than_image(b)
        check_prod_record(b)
    else:
        row("no measurement instrument in the image", None,
            "no image to inspect", ref_missing=True)
        row("no OTA fault injection in the image", None,
            "no image to inspect", ref_missing=True)
        row("no restart test feature in the image", None,
            "no image to inspect", ref_missing=True)
        for _f, n, _m in STOCK_IMAGE_MARKERS:
            row(n, None, "no image to inspect", ref_missing=True)
        for _f, n, _s, _p in STOCK_FEATURES:
            row(n, None, "no image to inspect", ref_missing=True)
        row(REALDASH_ROW, None, "no image to inspect", ref_missing=True)
        row(BUS_STATE_ROW, None, "no image to inspect", ref_missing=True)
        row("prod-build record present", None,
            "no image to inspect", ref_missing=True)
    check_test_only_refused(b)
    if b is not None:
        rf = write_project_rows(b)
        print("project rows for shared/tools R20: %s" % (rf or "not written (no image hash)"))

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
