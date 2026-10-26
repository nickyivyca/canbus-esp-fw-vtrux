"""Custody of the X2's flash around the jammer: dump, flash jammer, restore.

Gen-inhibit tester, 2026-10-08. The interposer's image on the X2 cannot be
rebuilt (its spec 8.2: two clean builds give different ELFs) and no copy of
the 7f197bfd binary survives, so the board is returned by writing back a
full read-back of its flash, never from a rebuild or from .pio.

  dump           esptool read-flash of the whole chip, TWICE; the two sha256
                 must match. Then the dump's own partition table and otadata
                 are parsed, every app partition is sliced out (next to the
                 dump, deleted at once) and its ELF sha256 read with
                 build_lookup.elf_sha256_of_bin. PASS needs a slot carrying
                 --expect-elf (default 7f197bfd).
  flash-jammer   refuses unless a PASS dump record is given and the dump file
                 still has the recorded sha256; then writes the jammer's
                 bootloader, partition table, otadata and app from one
                 builds/<md5>/ row of x2_jammer/builds/manifest.json, after
                 checking the app's md5 and ELF sha256 against that row.
  restore        write-flash 0x0 <dump>, then verify-flash 0x0 <dump>.
  check-dump     no board: re-checks a saved dump against its record (size and
                 sha256), then repeats dump's analysis on it. The dry run that
                 proves the ELF-hash path works before the board is touched.

BUILD_LOOKUP COMES FROM THE INTERPOSER'S CLONE (2026-10-08). The SeaDrive copy
in projects/vtrux/tools/interposer/ is retired; $INTERPOSER_CODE must point at
a clone's charge-interposer/ (tools-reference.md, "Settings the interposer
harness reads"). Unset or wrong is an error, never a fallback. Two names are
used from it: elf_sha256_of_bin and APP_DESC_MAGIC
(firmware/esp-development.md, "Cross-project dependency").

THE DUMP IS SENSITIVE. It holds the whole flash, NVS included. Nothing here
prints, hexdumps or quotes its contents: only its path, size and sha256 are
recorded. Records go to runs/x2_flash_custody_<sub>_<stamp>.json.

After `dump` and after `restore`, identify the board from the wire with
interposer-firmware/bench_identify_board.py; this script does not, because
esptool and the CAN witness are separate steps and each is recorded.

  py -3.14 tools/gen_inhibit/bench/x2_flash_custody.py dump
  py -3.14 ... flash-jammer --dump-record runs/x2_flash_custody_dump_<stamp>.json --build <md5>
  py -3.14 ... restore --dump-record runs/x2_flash_custody_dump_<stamp>.json
"""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import zlib
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BACKUPS = gi_paths.data_dir("board-backups")
JAMMER = gi_paths.data_dir("x2_jammer")
ESPTOOL = os.path.join(os.path.expanduser("~"), ".platformio", "packages",
                       "tool-esptoolpy", "esptool.py")


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
    cmd = [sys.executable, ESPTOOL, "--chip", "esp32s3", "--port", port,
           "--baud", "921600"] + list(args)
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
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "x2_flash_custody_%s_%s.json" % (sub, rec["when"]))
    with open(path, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
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
        rc, tail = esptool(a.port, "read-flash", "0", "ALL", p)
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
            rec["why"] = "read-flash failed"
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


def sub_flash_jammer(a, rec):
    d, _ = _checked_dump(a)
    rec["dump_record"] = a.dump_record
    rec["dump_sha256"] = d["dump_sha256"]
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
    args = ["write-flash"]
    for k in ("bootloader", "partitions", "boot_app0", "app"):
        args += [row["flash_offsets"][k], os.path.join(bdir, row["files"][k])]
    rc, tail = esptool(a.port, *args)
    rec.update({"rc": rc, "esptool_tail": tail,
                "verdict": "FLASHED" if rc == 0 else "FAIL"})
    print("write-flash rc %d" % rc)
    print("\n".join(tail))


def sub_restore(a, rec):
    d, p = _checked_dump(a)
    rec["dump_record"] = a.dump_record
    rec["dump_sha256"] = d["dump_sha256"]
    rc, tail = esptool(a.port, "write-flash", "0x0", p)
    rec["write"] = {"rc": rc, "esptool_tail": tail}
    print("write-flash rc %d" % rc)
    if rc != 0:
        rec["verdict"] = "FAIL"
        return
    rc2, tail2 = esptool(a.port, "verify-flash", "0x0", p)
    rec["verify"] = {"rc": rc2, "esptool_tail": tail2}
    print("verify-flash rc %d" % rc2)
    print("\n".join(tail2))
    rec["verdict"] = "VERIFIED" if rc2 == 0 else "FAIL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=("dump", "flash-jammer", "restore", "check-dump"))
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--expect-elf", default="7f197bfd")
    ap.add_argument("--dump-record")
    ap.add_argument("--build", help="jammer app md5 (its builds/ dir name)")
    a = ap.parse_args()
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "sub": a.sub,
           "esptool": ESPTOOL, "port": a.port, "build_lookup": BUILD_LOOKUP}
    try:
        {"dump": sub_dump, "flash-jammer": sub_flash_jammer,
         "restore": sub_restore, "check-dump": sub_check_dump}[a.sub](a, rec)
    finally:
        write_rec(a.sub, rec)
    return 0 if rec.get("verdict") in ("PASS", "FLASHED", "VERIFIED") else 1


if __name__ == "__main__":
    sys.exit(main())
