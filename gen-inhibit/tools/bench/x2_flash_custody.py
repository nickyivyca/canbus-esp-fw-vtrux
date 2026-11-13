"""Custody of the X2's flash around the jammer: dump, flash jammer, restore.

Gen-inhibit tester, 2026-10-08. The interposer's image on the X2 cannot be
rebuilt (its spec 8.2: two clean builds give different ELFs) and no copy of
the 7f197bfd binary survives, so the board is returned by writing back a
full read-back of its flash, never from a rebuild or from .pio.

  dump           esptool read_flash of the whole chip, TWICE; the two sha256
                 must match. Then the dump's own partition table and otadata
                 are parsed, every app partition is sliced out (next to the
                 dump, deleted at once) and its ELF sha256 read with
                 build_lookup.elf_sha256_of_bin. PASS needs a slot carrying
                 --expect-elf, which is required (see below).
  flash-jammer   refuses unless a PASS dump record is given and the dump file
                 still has the recorded sha256; then writes one jammer build,
                 named by exactly one of:
                   --build <md5>   the PlatformIO jammer: one builds/<md5>/ row of
                                   x2_jammer/builds/manifest.json; writes its
                                   bootloader, partition table, otadata and app
                                   after checking the app's md5 and ELF sha256
                                   against that row;
                   --row <16 hex> --variant <v> [--dev-builder <who>]
                                   the ESP-IDF port in shared/x2_test_images: the
                                   one row of x2_test_images/jammer/manifest.jsonl
                                   whose folder is builds/<16 hex> (see below).
  restore        write_flash 0x0 <dump>, then verify_flash 0x0 <dump>.
  check-dump     no board: re-checks a saved dump against its record (size and
                 sha256), then repeats dump's analysis on it. The dry run that
                 proves the ELF-hash path works before the board is touched.
  check-row      no board, no dump: flash-jammer --row's checks on a row
                 (--row, --variant, --dev-builder as there), nothing written;
                 PASS or REFUSED with the reason (2026-11-13).

BUILD_LOOKUP COMES FROM THE INTERPOSER'S CLONE (2026-10-08). The SeaDrive copy
in projects/vtrux/tools/interposer/ is retired; $INTERPOSER_CODE must point at
a clone's charge-interposer/ (tools-reference.md, "Settings the interposer
harness reads"). Unset or wrong is an error, never a fallback. Two names are
used from it: elf_sha256_of_bin and APP_DESC_MAGIC
(firmware/esp-development.md, "Cross-project dependency").

THE DUMP IS SENSITIVE. It holds the whole flash, NVS included. Nothing here
prints, hexdumps or quotes its contents: only its path, size and sha256 are
recorded. Records go to runs/x2_flash_custody_<sub>_<stamp>.json.

esptool (gen-inhibit reviewer, 2026-11-11; gi_esptool.py): the argv in GI_ESPTOOL_ARGV,
[<python>, "-m", "esptool"] from settings.esptool.argv, at the pinned version, read
before the board is touched; unset, another form or another version refuses (exit 1, a
record with verdict REFUSED, nothing run). dump, flash-jammer and restore records hold
esptool_argv and esptool_version; check-dump runs no esptool and records None for both.
Command names are the underscore ones (read_flash, write_flash, verify_flash).

The port (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12; gi_serial.py): the X2 is
named by its USB IDs in GI_X2_DEVICE and resolved to its /dev/serial/by-id/ path through
the core's serial_dev, after esptool's check and before the board is touched. No device,
or not exactly one, refuses like esptool (exit 1, verdict REFUSED, nothing run). dump,
flash-jammer and restore records hold the port and serial_device, what was matched (S3);
check-dump opens no port and records None for both. Linux only.

--expect-elf has no default (gen-inhibit reviewer, 2026-11-12). It was 7f197bfd, the
interposer image the X2 held when this tool was written; since images are loaded at a
run's start the X2 holds whatever the last run loaded, and shared/bench SPEC section 8
lets the android and can-tools hand-take roles run this tool on whatever image they find.
So dump and check-dump take the ELF sha256 prefix the caller read off the wire (0x7F7)
or pinned: 8 to 64 hex digits, matched in lower case. Missing, shorter, longer or not
hex refuses (exit 1, verdict REFUSED, nothing run, no esptool or port touched); the
record keeps expect_elf. flash-jammer and restore do not read it.

flash-jammer --row (gen-inhibit reviewer's jammer-idf merge reply, 2026-11-13; x2_test_images
SPEC T6, T7, T9). The ported jammer's builds are in gi_paths.x2_test_images("jammer"), written
only by shared/x2_test_images/tools/archive.py: manifest.jsonl, one JSON row per build, and
builds/<app sha256, first 16 hex digits>/. --row names that folder; --variant, required, is the
variant the caller means to flash (jammer, stop_a_only, stop_b_only). Before anything is
written, each of these must hold, or the record is REFUSED with the reason and esptool never
writes:
  - --row is 16 lower-case hex digits, and exactly one row has folder builds/<row>;
  - the row is schema 1, image "jammer", and its variant is --variant;
  - the row's app_sha256 starts with --row;
  - every file of the row is a bare name in that folder with the row's size, sha256 and md5,
    at a distinct offset, the offsets including 0x0, 0x8000 and the row's app_offset;
  - the file at app_offset has the row's app_sha256, and the ELF sha256 in its app descriptor
    (build_lookup) is the row's app_elf_sha256 and image_hash;
  - the project_name in its app descriptor is the row's project_name and holds
    NOT_FOR_VEHICLE (the variant is judged from the image, gen-inhibit reviewer, 2026-11-12);
  - a row with pinnable not true is a dev build, "for its builder's own bench work only"
    (T7): it needs --dev-builder equal to the row's builder. A pinnable row needs none.
Then every file of the row is written at its offset, in offset order, in one write_flash. The
record keeps the row's folder, variant, project_name, image hash, code hash, shared commit and
tag, pinnable, builder and the --dev-builder given. --build and --row exclude each other, one
is required, and --variant and --dev-builder are refused without --row: REFUSED in main,
before esptool or the port.

After `dump` and after `restore`, identify the board from the wire with
interposer-firmware/bench_identify_board.py; this script does not, because
esptool and the CAN witness are separate steps and each is recorded.

  python3.13 gen-inhibit/tools/bench/x2_flash_custody.py dump --expect-elf <8+ hex digits>
  python3.13 ... check-dump --dump-record runs/x2_flash_custody_dump_<stamp>.json --expect-elf <same>
  python3.13 ... flash-jammer --dump-record runs/x2_flash_custody_dump_<stamp>.json --build <md5>
  python3.13 ... check-row --row <16 hex> --variant jammer [--dev-builder "<builder>"]
  python3.13 ... flash-jammer --dump-record <same> --row <16 hex> --variant jammer [--dev-builder "<builder>"]
  python3.13 ... restore --dump-record runs/x2_flash_custody_dump_<stamp>.json
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import zlib
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_esptool as GE  # noqa: E402  esptool from the settings, pinned (2026-11-11)
import gi_serial as GS  # noqa: E402  the X2's port by its USB IDs (DEVICES-SPEC S1, 2026-11-12)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BACKUPS = gi_paths.data_dir("board-backups")
JAMMER = gi_paths.data_dir("x2_jammer")
X2IMG = gi_paths.x2_test_images("jammer")   # the ESP-IDF port's builds (--row, 2026-11-13)
ESPTOOL_SUBS = ("dump", "flash-jammer", "restore")   # the subcommands that run esptool
_ET = {}    # {"argv", "version"}, checked in main before a board subcommand runs


def _load_build_lookup():
    code = os.environ.get("INTERPOSER_CODE", "").strip()
    if not code:
        raise SystemExit("INTERPOSER_CODE is not set: point it at a clone's "
                         "charge-interposer/ (tools-reference.md)")
    path = os.path.join(code, "build_lookup.py")
    if not os.path.isfile(path):
        raise SystemExit("INTERPOSER_CODE=%r has no build_lookup.py" % code)
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_lookup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("elf_sha256_of_bin", "APP_DESC_MAGIC"):
        if not hasattr(mod, name):
            raise SystemExit("%s has no %s" % (path, name))
    return mod, os.path.abspath(path)


bl, BUILD_LOOKUP = _load_build_lookup()

PT_AT = 0x8000


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def esptool(port, *args):
    """Run esptool; returns (rc, tail of its output). Never given dump bytes."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if not _ET.get("argv"):
        raise GE.Refused("esptool was not checked before this call (main checks it)")
    cmd = _ET["argv"] + ["--chip", GE.CHIP, "--port", port, "--baud", "921600"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = (p.stdout + p.stderr).splitlines()
    # esptool's progress bar is non-ASCII; printing it on cp1252 crashed the
    # 2026-10-08 00:49 flash-jammer run after a successful write.
    keep = [x.encode("ascii", "replace").decode("ascii") for x in out
            if not x.startswith("Warning: DEPRECATED")][-12:]
    return p.returncode, keep


def partitions(img):
    """Partition table entries from a flash image: (type, subtype, off, size, label)."""
    out = []
    for k in range(0, 0xC00, 32):
        e = img[PT_AT + k:PT_AT + k + 32]
        if e[:2] != b"\xaa\x50":
            break
        typ, sub, off, size = struct.unpack_from("<BBII", e, 2)
        label = e[12:28].split(b"\0")[0].decode("ascii", "replace")
        out.append((typ, sub, off, size, label))
    return out


def boot_slot(img, parts):
    """Which OTA slot otadata selects, or a reason it cannot be told."""
    ota = [p for p in parts if p[0] == 1 and p[1] == 0]
    slots = sorted((p for p in parts if p[0] == 0 and 0x10 <= p[1] <= 0x1F),
                   key=lambda p: p[1])
    if not ota or not slots:
        return None, "no otadata or no OTA slots"
    base = ota[0][2]
    seqs = []
    for k in range(2):
        e = img[base + k * 0x1000:base + k * 0x1000 + 32]
        seq = struct.unpack_from("<I", e, 0)[0]
        crc = struct.unpack_from("<I", e, 28)[0]
        if seq == 0xFFFFFFFF:
            continue
        if zlib.crc32(e[0:4], 0xFFFFFFFF) != crc:
            continue
        seqs.append(seq)
    if not seqs:
        return None, "otadata empty or no valid entry (boots factory or ota_0)"
    return slots[(max(seqs) - 1) % len(slots)][4], "otadata seq %d" % max(seqs)


def app_hashes(img, parts, workdir):
    """ELF sha256 of every app partition in a flash image, by label.

    Each slice is written to workdir, hashed with build_lookup's reader, and
    deleted at once; nothing of the image is kept or printed.
    """
    apps = {}
    for t, _s, o, z, l in parts:
        if t != 0:
            continue
        sp = os.path.join(workdir, ".slice_tmp_%s.bin" % l)
        with open(sp, "wb") as fh:
            fh.write(img[o:o + z])
        try:
            apps[l] = bl.elf_sha256_of_bin(sp)
        finally:
            os.remove(sp)
    return apps


def write_rec(sub, rec):
    """runs/x2_flash_custody_<sub>_<when>.json, or with _2, _3, ... before .json when a record
    of that second exists (2026-11-13: two check-row calls in one second crashed on the second
    record, its check already done). Never overwrites."""
    os.makedirs(RUNS, exist_ok=True)
    base = os.path.join(RUNS, "x2_flash_custody_%s_%s" % (sub, rec["when"]))
    n = 1
    while True:
        path = base + (".json" if n == 1 else "_%d.json" % n)
        try:
            with open(path, "x", encoding="ascii", errors="replace") as fh:
                json.dump(rec, fh, indent=1)
            break
        except FileExistsError:
            n += 1
    print("wrote", path)
    return path


def sub_dump(a, rec):
    stamp = rec["when"]
    paths = [os.path.join(BACKUPS, "canx2_s3_full_%s_%s.bin" % (stamp, s))
             for s in ("a", "b")]
    rec["reads"] = []
    for p in paths:
        if os.path.exists(p):
            raise SystemExit("refusing to overwrite %s" % p)
        rc, tail = esptool(a.port, "read_flash", "0", "ALL", p)
        size = os.path.getsize(p) if os.path.exists(p) else None
        # relative to the data folder, which is what dump_path() reads it against
        # (2026-10-23: relative to HERE, the repo's bench/, it no longer resolved)
        rec["reads"].append({"path": os.path.relpath(p, gi_paths.data_dir()).replace("\\", "/"),
                             "rc": rc, "size": size,
                             "sha256": sha256_file(p) if size else None,
                             "esptool_tail": tail})
        print("read %s rc %d size %s" % (os.path.basename(p), rc, size))
        if rc != 0:
            rec["verdict"] = "FAIL"
            rec["why"] = "read_flash failed"
            return
    r0, r1 = rec["reads"]
    rec["reads_match"] = r0["sha256"] == r1["sha256"] and r0["size"] == r1["size"]
    if not rec["reads_match"]:
        rec["verdict"] = "FAIL"
        rec["why"] = "the two reads differ"
        return
    os.remove(paths[1])  # identical; keep one copy
    rec["reads"][1]["deleted_identical_copy"] = True
    rec["dump"] = r0["path"]
    rec["dump_sha256"] = r0["sha256"]
    rec["dump_size"] = r0["size"]
    analyse(paths[0], a.expect_elf, rec)


def analyse(dump_path, expect_elf, rec):
    """Partition table, boot slot and app ELF hashes of a dump; sets verdict."""
    with open(dump_path, "rb") as fh:
        img = fh.read()
    parts = partitions(img)
    rec["partitions"] = [{"type": t, "subtype": s, "offset": hex(o),
                          "size": hex(z), "label": l} for t, s, o, z, l in parts]
    slot, why = boot_slot(img, parts)
    rec["boot_slot"], rec["boot_slot_why"] = slot, why
    # slices go next to the dump (board-backups/ for a real one), deleted at once
    apps = app_hashes(img, parts, os.path.dirname(os.path.abspath(dump_path)))
    del img
    rec["app_elf_sha256"] = apps
    carriers = [l for l, h in apps.items() if h and h.startswith(expect_elf)]
    rec["slots_carrying_expected"] = carriers
    rec["verdict"] = "PASS" if carriers else "FAIL"
    if not carriers:
        rec["why"] = "no app slot carries ELF %s" % expect_elf
    print("partitions %s" % [(p["label"], p["offset"]) for p in rec["partitions"]])
    print("app ELF sha256 %s; boot slot %s (%s); carrying %s: %s -> %s"
          % ({k: (v[:8] if v else None) for k, v in apps.items()}, slot, why,
             expect_elf, carriers, rec["verdict"]))


def sub_check_dump(a, rec):
    """Board-free: the saved dump still matches its record, and still carries
    the expected app. Nothing of the dump is printed."""
    if not a.dump_record:
        raise SystemExit("check-dump needs --dump-record")
    with open(a.dump_record, encoding="ascii") as fh:
        dr = json.load(fh)
    rec["dump_record"] = a.dump_record.replace("\\", "/")
    path = dump_path(a.dump_record, dr["dump"])
    rec["dump"] = dr["dump"]
    size = os.path.getsize(path) if os.path.exists(path) else None
    got = sha256_file(path) if size else None
    rec["dump_size"], rec["dump_sha256"] = size, got
    rec["matches_record"] = size == dr["dump_size"] and got == dr["dump_sha256"]
    print("dump %s size %s sha256 %s matches record: %s"
          % (dr["dump"], size, got and got[:16], rec["matches_record"]))
    if not rec["matches_record"]:
        rec["verdict"] = "FAIL"
        rec["why"] = "the dump no longer matches its record"
        return
    analyse(path, a.expect_elf, rec)


def dump_path(record_path, rel):
    """A dump record's "dump" field, resolved against the data folder. Refuses an
    absolute path or one with a ".." part rather than resolving it: a record written
    by a24de99 (relative to the repo's bench/) then stops here, naming the record."""
    parts = rel.replace("\\", "/").split("/")
    if os.path.isabs(rel) or rel.startswith(("/", "\\")) or ".." in parts:
        raise SystemExit("refusing dump record %s: its dump path %r is not relative to the "
                         "data folder (absolute, or has a '..' part)" % (record_path, rel))
    return gi_paths.data_dir(*parts)


def _checked_dump(a):
    with open(a.dump_record, encoding="ascii") as fh:
        d = json.load(fh)
    if d.get("verdict") != "PASS":
        raise SystemExit("dump record is not PASS: %s" % d.get("verdict"))
    p = dump_path(a.dump_record, d["dump"])
    now = sha256_file(p)
    if now != d["dump_sha256"]:
        raise SystemExit("dump file sha256 changed: %s != %s" % (now, d["dump_sha256"]))
    return d, p


APP_DESC_AT = 32                            # esp_app_desc_t after the image and segment headers
PROJECT_NAME_AT = APP_DESC_AT + 4 + 4 + 8 + 32   # magic, secure_version, reserv1, version[32]
ROW_RE = re.compile(r"[0-9a-f]{16}")
ROW_OFFSETS = (0x0, 0x8000)                 # a bootloader and a partition table, besides the app


class RowRefused(Exception):
    """A manifest.jsonl row that may not be flashed; nothing has been written."""


def app_project_name(path):
    """project_name from an app image's descriptor, or None without the descriptor magic."""
    with open(path, "rb") as fh:
        head = fh.read(PROJECT_NAME_AT + 32)
    if len(head) < PROJECT_NAME_AT + 32 or struct.unpack_from("<I", head, APP_DESC_AT)[0] != bl.APP_DESC_MAGIC:
        return None
    return head[PROJECT_NAME_AT:PROJECT_NAME_AT + 32].split(b"\0")[0].decode("ascii", "replace")


def jsonl_row(a):
    """(row, folder, [(offset, path)] in offset order) for --row, checked as the module
    docstring lists, else RowRefused naming the first check that fails."""
    if not ROW_RE.fullmatch(a.row or ""):
        raise RowRefused("--row %r is not 16 lower-case hex digits (a builds/ folder name)" % a.row)
    manifest = os.path.join(X2IMG, "manifest.jsonl")
    rows = []
    with open(manifest, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise RowRefused("%s line %d is not JSON" % (manifest, n))
            if isinstance(r, dict) and r.get("folder") == "builds/" + a.row:
                rows.append(r)
    if len(rows) != 1:
        raise RowRefused("need exactly one manifest.jsonl row with folder builds/%s, got %d" % (a.row, len(rows)))
    row = rows[0]
    if row.get("schema") != 1 or row.get("image") != "jammer":
        raise RowRefused("row is schema %r, image %r; want schema 1, image 'jammer'"
                         % (row.get("schema"), row.get("image")))
    if row.get("variant") != a.variant:
        raise RowRefused("row is variant %r, offered as %r" % (row.get("variant"), a.variant))
    if row.get("pinnable") is not True and a.dev_builder != row.get("builder"):
        raise RowRefused("row is a dev build (pinnable %r), for its builder's own bench work only "
                         "(x2_test_images SPEC T7): builder %r, --dev-builder %r"
                         % (row.get("pinnable"), row.get("builder"), a.dev_builder))
    app_sha = row.get("app_sha256") or ""
    if not app_sha.startswith(a.row):
        raise RowRefused("row's app_sha256 %s does not start with its folder name %s" % (app_sha[:16], a.row))
    folder = os.path.join(X2IMG, "builds", a.row)
    try:
        app_off = int(row["app_offset"], 16)
        files = [(int(f["offset"], 16), f) for f in row["files"]]
    except (KeyError, TypeError, ValueError) as e:
        raise RowRefused("row's offsets cannot be read: %s: %s" % (type(e).__name__, e))
    offsets = [o for o, _ in files]
    if len(set(offsets)) != len(offsets):
        raise RowRefused("row has two files at one offset: %s" % [hex(o) for o in offsets])
    missing = [hex(o) for o in ROW_OFFSETS + (app_off,) if o not in offsets]
    if missing:
        raise RowRefused("row has no file at %s" % ", ".join(missing))
    out = []
    for off, f in sorted(files, key=lambda x: x[0]):
        name = f.get("name") or ""
        if not name or name != os.path.basename(name) or name in (".", "..") or "\\" in name:
            raise RowRefused("row's file name %r is not a bare name in its folder" % name)
        p = os.path.join(folder, name)
        if not os.path.isfile(p):
            raise RowRefused("%s is missing from builds/%s" % (name, a.row))
        got = (os.path.getsize(p), sha256_file(p), md5_file(p))
        want = (f.get("size"), f.get("sha256"), f.get("md5"))
        for what, g, w in zip(("size", "sha256", "md5"), got, want):
            if g != w:
                raise RowRefused("%s: %s %s, row %s" % (name, what, g, w))
        out.append((off, p))
    app = dict(out)[app_off]
    if sha256_file(app) != app_sha:
        raise RowRefused("the file at app_offset %s is not the row's app (app_sha256)" % hex(app_off))
    elf = bl.elf_sha256_of_bin(app)
    if not elf or elf != row.get("app_elf_sha256") or elf != row.get("image_hash"):
        raise RowRefused("app descriptor ELF sha256 %s; row app_elf_sha256 %s, image_hash %s"
                         % (elf, row.get("app_elf_sha256"), row.get("image_hash")))
    pn = app_project_name(app)
    if pn != row.get("project_name"):
        raise RowRefused("app descriptor project_name %r, row %r" % (pn, row.get("project_name")))
    if not pn or "NOT_FOR_VEHICLE" not in pn:
        raise RowRefused("app descriptor project_name %r has no NOT_FOR_VEHICLE" % pn)
    return row, folder, out


def flash_row(a, rec):
    """--row: the ESP-IDF port's build from manifest.jsonl, every file at its offset."""
    try:
        row, folder, files = jsonl_row(a)
    except (RowRefused, OSError) as e:
        rec.update(verdict="REFUSED", why=str(e))
        print("REFUSED: %s (nothing written)" % e)
        return
    rec["jammer"] = {k: row.get(k) for k in (
        "folder", "variant", "project_name", "image_hash", "app_sha256", "code_hash", "shared_commit",
        "shared_tag", "version", "pinnable", "builder")}
    rec["jammer"]["dev_builder"] = a.dev_builder
    rec["jammer"]["files"] = [{"offset": hex(o), "name": os.path.basename(p)} for o, p in files]
    args = ["write_flash"]
    for off, p in files:
        args += [hex(off), p]
    rc, tail = esptool(a.port, *args)
    rec.update({"rc": rc, "esptool_tail": tail, "verdict": "FLASHED" if rc == 0 else "FAIL"})
    print("%s %s (%s): write_flash rc %d" % (row["variant"], row["folder"], row["project_name"], rc))
    print("\n".join(tail))


def sub_flash_jammer(a, rec):
    d, _ = _checked_dump(a)
    rec["dump_record"] = a.dump_record
    rec["dump_sha256"] = d["dump_sha256"]
    if a.row is not None:
        return flash_row(a, rec)
    with open(os.path.join(JAMMER, "builds", "manifest.json"), encoding="ascii") as fh:
        rows = [r for r in json.load(fh) if r["app_md5"] == a.build]
    if len(rows) != 1:
        raise SystemExit("need exactly one manifest row for md5 %s, got %d"
                         % (a.build, len(rows)))
    row = rows[0]
    bdir = os.path.join(JAMMER, row["dir"])
    app = os.path.join(bdir, row["files"]["app"])
    if md5_file(app) != row["app_md5"]:
        raise SystemExit("app md5 does not match its manifest row")
    elf = bl.elf_sha256_of_bin(app)
    if elf != row["elf_sha256"]:
        raise SystemExit("app ELF sha256 %s != manifest %s" % (elf, row["elf_sha256"]))
    rec["jammer"] = {"progname": row["progname"], "app_md5": row["app_md5"],
                     "elf_sha256": elf}
    args = ["write_flash"]
    for k in ("bootloader", "partitions", "boot_app0", "app"):
        args += [row["flash_offsets"][k], os.path.join(bdir, row["files"][k])]
    rc, tail = esptool(a.port, *args)
    rec.update({"rc": rc, "esptool_tail": tail,
                "verdict": "FLASHED" if rc == 0 else "FAIL"})
    print("write_flash rc %d" % rc)
    print("\n".join(tail))


def sub_restore(a, rec):
    d, p = _checked_dump(a)
    rec["dump_record"] = a.dump_record
    rec["dump_sha256"] = d["dump_sha256"]
    rc, tail = esptool(a.port, "write_flash", "0x0", p)
    rec["write"] = {"rc": rc, "esptool_tail": tail}
    print("write_flash rc %d" % rc)
    if rc != 0:
        rec["verdict"] = "FAIL"
        return
    rc2, tail2 = esptool(a.port, "verify_flash", "0x0", p)
    rec["verify"] = {"rc": rc2, "esptool_tail": tail2}
    print("verify_flash rc %d" % rc2)
    print("\n".join(tail2))
    rec["verdict"] = "VERIFIED" if rc2 == 0 else "FAIL"


EXPECT_SUBS = ("dump", "check-dump")


def expect_elf_why(v):
    """None when v is an ELF sha256 prefix dump and check-dump may match against
    (8 to 64 hex digits, either case), else why not (gen-inhibit reviewer, 2026-11-12)."""
    if not v:
        return "--expect-elf is required: the ELF sha256 prefix read off the wire (0x7F7) or pinned"
    if not 8 <= len(v) <= 64 or any(c not in "0123456789abcdef" for c in v.lower()):
        return "--expect-elf %r is not 8 to 64 hex digits" % v
    return None


def sub_check_row(a, rec):
    """Board-free: flash-jammer --row's checks on a manifest.jsonl row, nothing written. The
    dry run before the board is touched, as check-dump is for a dump (2026-11-13)."""
    try:
        row, _folder, files = jsonl_row(a)
    except (RowRefused, OSError) as e:
        rec.update(verdict="REFUSED", why=str(e))
        print("REFUSED: %s" % e)
        return
    rec["jammer"] = {k: row.get(k) for k in ("folder", "variant", "project_name", "image_hash", "pinnable",
                                             "builder")}
    rec["jammer"]["dev_builder"] = a.dev_builder
    rec["jammer"]["files"] = [{"offset": hex(o), "name": os.path.basename(p)} for o, p in files]
    rec["verdict"] = "PASS"
    print("%s %s (%s, image %s, pinnable %s): every check PASS, nothing written"
          % (row["variant"], row["folder"], row["project_name"], row["image_hash"][:16], row.get("pinnable")))


def build_args_why(a):
    """None when flash-jammer's build arguments name one build, else why not: exactly one of
    --build and --row; --row needs --variant; --variant and --dev-builder only with --row.
    check-row takes --row and --variant, --dev-builder for a dev row, and no --build. Other
    subcommands take none of them (2026-11-13)."""
    given = [n for n, v in (("--build", a.build), ("--row", a.row), ("--variant", a.variant),
                            ("--dev-builder", a.dev_builder)) if v is not None]
    if a.sub == "check-row":
        if a.build is not None:
            return "check-row takes no --build: it checks a manifest.jsonl row (--row)"
        if a.row is None or not a.variant:
            return "check-row needs --row <16 hex> and --variant"
        return None
    if a.sub != "flash-jammer":
        return "%s takes no %s" % (a.sub, ", ".join(given)) if given else None
    if (a.build is None) == (a.row is None):
        return "flash-jammer needs exactly one of --build <md5> and --row <16 hex>"
    if a.row is not None and not a.variant:
        return "--row needs --variant: the variant meant (jammer, stop_a_only, stop_b_only)"
    if a.build is not None and (a.variant is not None or a.dev_builder is not None):
        return "--variant and --dev-builder go only with --row"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=("dump", "flash-jammer", "restore", "check-dump", "check-row"))
    ap.add_argument("--expect-elf", default=None,
                    help="dump, check-dump (required): the app ELF sha256 prefix, 8 to 64 hex "
                         "digits, that a slot of the dump must carry; no default since 2026-11-12")
    ap.add_argument("--dump-record")
    ap.add_argument("--build", help="flash-jammer: the PlatformIO jammer's app md5 (its x2_jammer/builds/ dir name)")
    ap.add_argument("--row", help="flash-jammer: the ESP-IDF port's build, its x2_test_images/jammer/builds/ "
                                  "folder name (16 hex digits)")
    ap.add_argument("--variant", help="flash-jammer --row (required): the variant meant; the row's must match")
    ap.add_argument("--dev-builder", help="flash-jammer --row: for a dev build (pinnable false), its builder "
                                          "as the row names it (x2_test_images SPEC T7)")
    a = ap.parse_args()
    a.port = None
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "sub": a.sub,
           "esptool_argv": None, "esptool_version": None, "port": None, "serial_device": None,
           "build_lookup": BUILD_LOOKUP}
    try:
        why = build_args_why(a)
        if why:
            rec.update(verdict="REFUSED", why=why)
            print("REFUSED: %s (nothing run)" % why)
            return 1
        if a.sub in EXPECT_SUBS:
            rec["expect_elf"] = a.expect_elf
            why = expect_elf_why(a.expect_elf)
            if why:
                rec.update(verdict="REFUSED", why=why)
                print("REFUSED: %s (nothing run)" % why)
                return 1
            a.expect_elf = a.expect_elf.lower()
        if a.sub in ESPTOOL_SUBS:
            _ET.clear()
            try:
                _ET.update(GE.checked())
                rec["esptool_argv"], rec["esptool_version"] = _ET["argv"], _ET["version"]
                dev = GS.resolve("x2")
            except (GE.Refused, GS.Refused) as e:
                rec.update(verdict="REFUSED", why=str(e))
                print("REFUSED: %s (nothing run)" % e)
                return 1
            a.port = rec["port"] = dev["port"]
            rec["serial_device"] = dev
        {"dump": sub_dump, "flash-jammer": sub_flash_jammer,
         "restore": sub_restore, "check-dump": sub_check_dump, "check-row": sub_check_row}[a.sub](a, rec)
    finally:
        write_rec(a.sub, rec)
    return 0 if rec.get("verdict") in ("PASS", "FLASHED", "VERIFIED") else 1   # check-row's PASS too


if __name__ == "__main__":
    sys.exit(main())
