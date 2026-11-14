"""Harness test for x2_flash_custody.py: partitions, boot_slot, app_hashes.

Synthetic flash images, no board and no esptool. Covers the checks that decide
whether a dump may be trusted to put 7f197bfd back: the partition table is
read from the image itself, otadata picks the boot slot only when its CRC is
valid, an app slot with no app descriptor yields None, and no slice file is
left behind.

The dump record's path (2026-10-23): sub_dump records it relative to the data
folder and _checked_dump / check-dump read it back from there, through a fake
esptool and a fake data folder in the worktree's scratch/; a recorded path that is
absolute or has a ".." part is refused by both readers, naming the record.
test_x2_flash_custody_mutants.py runs the mutants of that.

The port (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12): main() runs against a
made-up bench of serial devices (_fake_serial.py) and GI_X2_DEVICE, so the X2's by-id link
is resolved through gi_serial and the core's serial_dev and handed to esptool; the record
keeps the port and serial_device. GI_X2_DEVICE unset, a tty name or IDs matching no device
give a REFUSED record with nothing read; check-dump needs no port. Needs the pinned core's
shared/bench on PYTHONPATH (2026.11.11.6 or later), and Linux.

--expect-elf (gen-inhibit reviewer, 2026-11-12): no default any more (it was 7f197bfd).
main() dump and check-dump, each without it, empty, with 7 digits, a non-hex digit or 65
digits: a REFUSED record keeping what was given, no esptool call, no port, no dump;
restore needs none (its check above runs without one); an upper-case one matches; one
in neither slot FAILs, naming it.
(7f197bfd stays as the synthetic images' made-up hash.)

flash-jammer --row and check-row (gen-inhibit reviewer's jammer-idf merge reply, 2026-11-13):
row_cases() builds a fake x2_test_images/jammer/ as archive.py lays it out (manifest.jsonl,
builds/<app sha256[:16]>/ with stand-in app images carrying the descriptor's magic,
project_name and ELF sha256) and flashes through a fake esptool: the good rows write every
file in offset order and the record keeps the row; each check of the row refuses with its
reason and nothing written; --build still flashes the PlatformIO row. esptool_main_cases()
adds --row through main(), the argument refusals and check-row.

verify-row (gen-inhibit reviewer, 2026-11-13: T10's identity with committed harness):
verify_row_cases(), through main() on _fake_esptool.py with a whole-flash image; its can-fail
is another row's build on the flash, which must FAIL.

Run: python3.13 gen-inhibit/tools/bench/test_x2_flash_custody.py
"""
import os
import shutil
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# CUSTODY_DIR: a folder holding a mutant x2_flash_custody.py, imported in place of
# this folder's (test_x2_flash_custody_mutants.py); gi_paths still comes from here.
if os.environ.get("CUSTODY_DIR"):
    sys.path.insert(0, os.environ["CUSTODY_DIR"])

import x2_flash_custody as C                                    # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = gi_paths.scratch("x2_flash_custody")   # the worktree's scratch/ (AGENTS.md)
FAKE_DATA = os.path.join(WORK, "data")      # stands in for gi_paths.data_dir() in the record cases
APP_DESC_AT = 32
ELF_SHA_AT = APP_DESC_AT + 4 + 4 + 8 + 32 + 32 + 16 + 16 + 32
OTADATA, OTA0, OTA1 = 0xD000, 0x10000, 0x20000


def pt_entry(typ, sub, off, size, label):
    return (b"\xaa\x50" + struct.pack("<BBII", typ, sub, off, size)
            + label.encode().ljust(16, b"\0") + b"\0" * 4)


def image(elf0, elf1, seq=None, bad_crc=False):
    img = bytearray(b"\xff" * 0x30000)
    pt = (pt_entry(1, 2, 0x9000, 0x4000, "nvs") +
          pt_entry(1, 0, OTADATA, 0x2000, "otadata") +
          pt_entry(0, 0x10, OTA0, 0x10000, "app0") +
          pt_entry(0, 0x11, OTA1, 0x10000, "app1"))
    img[0x8000:0x8000 + len(pt)] = pt
    for base, elf in ((OTA0, elf0), (OTA1, elf1)):
        if elf is None:
            continue
        struct.pack_into("<I", img, base + APP_DESC_AT, C.bl.APP_DESC_MAGIC)
        img[base + ELF_SHA_AT:base + ELF_SHA_AT + 32] = bytes.fromhex(elf)
    if seq is not None:
        e = bytearray(b"\xff" * 32)
        struct.pack_into("<I", e, 0, seq)
        crc = zlib.crc32(bytes(e[0:4]), 0xFFFFFFFF)
        struct.pack_into("<I", e, 28, crc ^ (1 if bad_crc else 0))
        img[OTADATA:OTADATA + 32] = e
    return bytes(img)


GOOD = "7f197bfd" + "11" * 28
OTHER = "f28781c9" + "22" * 28
NEITHER = "0badc0de" + "33" * 28    # in no slot of any image here
EXP = ["--expect-elf", GOOD[:8]]    # required since 2026-11-12 (gen-inhibit reviewer)

CASES = [
    # label, image, want boot slot, want hashes by label
    ("seq 2 boots app1, which carries 7f197bfd", image(OTHER, GOOD, seq=2),
     "app1", {"app0": OTHER, "app1": GOOD}),
    ("seq 1 boots app0", image(GOOD, OTHER, seq=1), "app0",
     {"app0": GOOD, "app1": OTHER}),
    ("bad otadata CRC -> boot slot unknown", image(OTHER, GOOD, seq=2, bad_crc=True),
     None, {"app0": OTHER, "app1": GOOD}),
    ("empty otadata -> boot slot unknown", image(GOOD, None), None,
     {"app0": GOOD, "app1": None}),
    ("no slot carries it", image(OTHER, None, seq=1), "app0",
     {"app0": OTHER, "app1": None}),
]


class fake_data:
    """gi_paths.data_dir() and C.BACKUPS pointed at FAKE_DATA for the block, restored after."""
    def __enter__(self):
        self.saved = gi_paths.data_dir, C.BACKUPS
        gi_paths.data_dir = lambda *parts: os.path.join(FAKE_DATA, *parts)
        C.BACKUPS = os.path.join(FAKE_DATA, "board-backups")
        os.makedirs(C.BACKUPS, exist_ok=True)

    def __exit__(self, *exc):
        gi_paths.data_dir, C.BACKUPS = self.saved
        gi_paths.remove_tree(FAKE_DATA)


def check_dump_cases():
    """sub_check_dump on synthetic dumps: PASS only when the file still matches
    its record AND a slot carries the expected ELF."""
    import argparse
    import contextlib
    import io
    import json
    bad = []
    with fake_data():
        bad += _check_dump_cases(argparse, contextlib, io, json)
    return bad


def _check_dump_cases(argparse, contextlib, io, json):
    bad = []
    dump = os.path.join(C.BACKUPS, "synthetic_dump.bin")
    record = os.path.join(WORK, "synthetic_dump_record.json")

    def run(img, rec_sha=None):
        with open(dump, "wb") as fh:
            fh.write(img)
        sha = C.sha256_file(dump)
        with open(record, "w", encoding="ascii") as fh:
            json.dump({"dump": os.path.relpath(dump, gi_paths.data_dir()), "dump_size": len(img),
                       "dump_sha256": rec_sha or sha, "verdict": "PASS"}, fh)
        rec = {}
        with contextlib.redirect_stdout(io.StringIO()):
            C.sub_check_dump(argparse.Namespace(dump_record=record,
                                                expect_elf="7f197bfd"), rec)
        return rec.get("verdict")

    for label, img, rec_sha, want in (
            ("check-dump: matching dump carrying it -> PASS",
             image(OTHER, GOOD, seq=2), None, "PASS"),
            ("check-dump: dump changed since its record -> FAIL",
             image(OTHER, GOOD, seq=2), "00" * 32, "FAIL"),
            ("check-dump: no slot carries it -> FAIL",
             image(OTHER, None, seq=1), None, "FAIL")):
        got = run(img, rec_sha)
        ok = got == want and os.listdir(C.BACKUPS) == [os.path.basename(dump)]
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, got))
        if not ok:
            bad.append(label)
    return bad


def record_path_cases():
    """sub_dump -> record -> _checked_dump, sub_check_dump and sub_restore, on a fake
    esptool: the recorded path is relative to the data folder and resolves to the dump
    it names; an absolute or ".." path is refused by both readers, naming the record."""
    import argparse
    import contextlib
    import io
    import json
    bad = []
    img = image(OTHER, GOOD, seq=2)
    calls = []

    def fake_esptool(port, *args):
        calls.append(args)
        if args[0] == "read_flash":
            with open(args[-1], "wb") as fh:
                fh.write(img)
        return 0, []

    def check(label, ok, got):
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, got))
        if not ok:
            bad.append(label)

    def outcome(fn, *args):
        """("refused", message) for a SystemExit naming the refusal, else what happened."""
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r = fn(*args)
        except SystemExit as e:
            return ("refused" if "refusing dump record" in str(e) else "exit", str(e))
        except Exception as e:  # a crash is reported, never passed
            return ("crash", "%s: %s" % (type(e).__name__, e))
        return ("returned", r)

    saved = C.esptool
    C.esptool = fake_esptool
    try:
        with fake_data():
            rec = {"when": "TEST"}
            got = outcome(C.sub_dump, argparse.Namespace(port="FAKE", expect_elf="7f197bfd"), rec)
            check("dump: two reads, each by read_flash 0 ALL",
                  got[0] == "returned" and [c[:3] for c in calls] == [("read_flash", "0", "ALL")] * 2,
                  (got[0], calls))
            dump = os.path.join(C.BACKUPS, "canx2_s3_full_TEST_a.bin")
            check("dump: PASS, one read kept", rec.get("verdict") == "PASS"
                  and os.listdir(C.BACKUPS) == [os.path.basename(dump)], rec.get("verdict"))
            check("dump: path recorded relative to the data folder",
                  rec.get("dump") == "board-backups/canx2_s3_full_TEST_a.bin", rec.get("dump"))
            record = os.path.join(WORK, "dump_record.json")

            def write_record(path_field):
                with open(record, "w", encoding="ascii") as fh:
                    json.dump(dict(rec, dump=path_field), fh)

            write_record(rec.get("dump"))
            ns = argparse.Namespace(dump_record=record, expect_elf="7f197bfd", port="FAKE")
            got = outcome(C._checked_dump, ns)
            check("_checked_dump resolves the recorded path to the dump",
                  got[0] == "returned" and os.path.exists(got[1][1])
                  and os.path.samefile(got[1][1], dump), got[0])
            del calls[:]
            r2 = {}
            got = outcome(C.sub_restore, ns, r2)
            writes = [c for c in calls if c[0] == "write_flash"]
            verifies = [c for c in calls if c[0] == "verify_flash"]
            check("restore: write_flash of that dump, then verify_flash, VERIFIED",
                  got[0] == "returned" and r2.get("verdict") == "VERIFIED" and len(writes) == 1
                  and os.path.samefile(writes[0][-1], dump) and len(verifies) == 1
                  and os.path.samefile(verifies[0][-1], dump), (r2.get("verdict"), calls))
            r3 = {}
            got = outcome(C.sub_check_dump, ns, r3)
            check("check-dump on the same record: PASS", r3.get("verdict") == "PASS", r3.get("verdict"))
            # what a24de99 wrote: the path relative to this folder, the repo's bench/
            here_rel = os.path.relpath(dump, HERE).replace("\\", "/")
            for label, field in (("absolute", os.path.abspath(dump)),
                                 ("'..' part", "board-backups/../board-backups/" + os.path.basename(dump)),
                                 ("relative to bench/ (a24de99)", here_rel)):
                write_record(field)
                for reader, fn, args in (("_checked_dump", C._checked_dump, (ns,)),
                                         ("check-dump", C.sub_check_dump, (ns, {}))):
                    got = outcome(fn, *args)
                    check("%s refuses a path %s" % (reader, label),
                          got[0] == "refused" and record in got[1], got[0])
    finally:
        C.esptool = saved
    return bad


X2IMG_FAKE = os.path.join(WORK, "x2img", "jammer")   # stands in for gi_paths.x2_test_images("jammer")
JAMMER_PN = "vtrux_jammer_x2_NOT_FOR_VEHICLE"
STOP_A_PN = "jammer_stop_a_NOT_FOR_VEHICLE"
BUILDER = "gen-inhibit tester"


def app_bin(pn, elf):
    """A stand-in app image: the descriptor magic, project_name and ELF sha256 where an
    ESP-IDF app has them (esp_app_desc_t at 32)."""
    app = bytearray(b"\xe9" + b"\0" * 0x3FF)
    struct.pack_into("<I", app, APP_DESC_AT, C.bl.APP_DESC_MAGIC)
    pnb = pn.encode()
    app[C.PROJECT_NAME_AT:C.PROJECT_NAME_AT + len(pnb)] = pnb
    app[ELF_SHA_AT:ELF_SHA_AT + 32] = bytes.fromhex(elf)
    return bytes(app)


def x2img_fixture(rows=None, variant="jammer", pn=JAMMER_PN, pinnable=True, elf=GOOD, edit=None, tamper=None,
                  app_pn=None, pt=b"\xaa\x50" * 96):
    """A fresh X2IMG_FAKE holding one build as archive.py lays it out (manifest.jsonl, one row;
    builds/<app sha256[:16]>/). edit(row) changes the row before it is written; tamper(folder)
    changes the files after; app_pn puts another project_name in the app than the row says;
    rows, if given, are extra rows written before it. Returns the row as written."""
    import hashlib
    import json
    gi_paths.remove_tree(os.path.dirname(X2IMG_FAKE))
    app = app_bin(app_pn or pn, elf)
    app_sha = hashlib.sha256(app).hexdigest()
    folder = os.path.join(X2IMG_FAKE, "builds", app_sha[:16])
    os.makedirs(folder)
    files = []
    for off, name, data in (("0x0", "bootloader.bin", b"\xe9boot" * 50),
                            ("0x8000", "partition-table.bin", pt),
                            ("0xe000", "ota_data_initial.bin", b"\xff" * 256),
                            ("0x10000", pn + ".bin", app)):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
        files.append({"offset": off, "name": name, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "md5": hashlib.md5(data).hexdigest()})
    row = {"schema": 1, "image": "jammer", "variant": variant, "project_name": pn,
           "folder": "builds/" + app_sha[:16], "app_offset": "0x10000", "app_sha256": app_sha,
           "app_elf_sha256": elf, "image_hash": elf, "code_hash": "c0de" * 16,
           "shared_commit": "5ade" * 10, "shared_tag": "2026.11.13" if pinnable else None,
           "version": "2026.11.13", "pinnable": pinnable, "builder": BUILDER, "files": files}
    if edit:
        edit(row)
    row["files"] = row["files"][::-1]   # out of offset order, so the write's order is the tool's sort
    with open(os.path.join(X2IMG_FAKE, "manifest.jsonl"), "w", encoding="utf-8") as fh:
        for r in (rows or []) + [row]:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    if tamper:
        tamper(folder)
    return row


JAMMER_PT = (pt_entry(1, 2, 0x9000, 0x5000, "nvs") + pt_entry(1, 0, 0xE000, 0x2000, "otadata")
             + pt_entry(0, 0x10, 0x10000, 0x330000, "app0") + pt_entry(0, 0x11, 0x340000, 0x330000, "app1")
             + pt_entry(1, 0x82, 0x670000, 0x180000, "spiffs") + b"\xff" * 32)   # the jammer's layout


def ota_blob(entries=(), size=0x2000):
    """An otadata partition: entries [(index, seq, state, bad_crc)], the rest erased."""
    b = bytearray(b"\xff" * size)
    for k, seq, state, bad in entries:
        e = bytearray(b"\xff" * 32)
        struct.pack_into("<I", e, 0, seq)
        struct.pack_into("<I", e, 24, state)
        struct.pack_into("<I", e, 28, zlib.crc32(bytes(e[0:4]), 0xFFFFFFFF) ^ (1 if bad else 0))
        b[k * 0x1000:k * 0x1000 + 32] = e
    return bytes(b)


def otadata_cases():
    """read-otadata (gen-inhibit reviewer, 2026-11-13): otadata_select picks the app as the
    bootloader does, and sub_read_otadata reads only the otadata partition the row's table
    names, PASSes only for the row's app offset, and records both entries."""
    import argparse
    import contextlib
    import io
    bad = []

    def check(label, ok, got):
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, "" if ok else got))
        if not ok:
            bad.append(label)

    parts = C.partitions(b"\xff" * C.PT_AT + JAMMER_PT)
    U = 0xFFFFFFFF
    for label, blob, want, state in (
            ("blank: no factory -> the first OTA slot", ota_blob(), "app0", None),
            ("seq 1 -> app0", ota_blob([(0, 1, U, False)]), "app0", U),
            ("seq 2 -> app1", ota_blob([(0, 2, U, False)]), "app1", U),
            ("seq 1 and seq 2 -> the higher, app1", ota_blob([(0, 1, 2, False), (1, 2, 2, False)]), "app1", 2),
            ("seq 3 with a bad crc is skipped -> seq 1, app0", ota_blob([(0, 1, 2, False), (1, 3, 2, True)]), "app0", 2),
            ("seq 4 in entry 1 -> app1", ota_blob([(1, 4, 0, False)]), "app1", 0),
            ("seq 0xFFFFFFFF is erased, not valid -> app0", ota_blob([(1, U, 2, False)]), "app0", None)):
        s = C.otadata_select(blob, parts)
        check("select: %s" % label, (s["selected"] or {}).get("label") == want and s["selecting_state"] == state, s)
    fparts = parts + [(0, 0x00, 0x700000, 0x10000, "factory")]
    s = C.otadata_select(ota_blob(), fparts)
    check("select: blank with a factory app -> factory", (s["selected"] or {}).get("label") == "factory", s)
    e = C.otadata_entries(ota_blob([(0, 7, 3, False), (1, 9, 2, True)]))
    check("entries: seq, state name, crc_ok and valid per entry",
          e[0]["ota_seq"] == 7 and e[0]["state_name"] == "INVALID" and e[0]["valid"] and e[0]["crc_ok"]
          and e[1]["ota_seq"] == 9 and not e[1]["crc_ok"] and not e[1]["valid"], e)

    calls = []
    plan = {}

    def fake_esptool(port, *args):
        calls.append(args)
        if plan.get("rc", 0) == 0:
            with open(args[-1], "wb") as fh:
                fh.write(plan["blob"])
        return plan.get("rc", 0), ["Read %d bytes" % len(plan.get("blob", b""))]

    saved = C.esptool, C.X2IMG, C.RUNS
    C.esptool, C.X2IMG, C.RUNS = fake_esptool, X2IMG_FAKE, os.path.join(WORK, "otadata_runs")
    try:
        row = x2img_fixture(pt=JAMMER_PT)
        rid = row["folder"].split("/")[1]

        def read(blob, rc=0):
            del calls[:]
            plan.clear()
            plan.update(blob=blob, rc=rc)
            rec = {"when": "20261113T000000"}
            ns = argparse.Namespace(port="FAKE", row=rid, variant="jammer", dev_builder=None, build=None)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    C.sub_read_otadata(ns, rec)
            except Exception as ex:   # a crash is reported, never passed
                rec["crash"] = "%s: %s" % (type(ex).__name__, ex)
            return rec

        rec = read(ota_blob())
        check("read: erased otadata -> PASS, equals the initial file, app0",
              rec.get("verdict") == "PASS" and rec["otadata"]["equals_initial"] is True
              and rec["selected"]["label"] == "app0", rec)
        check("read: only the otadata partition, at the table's offset and size",
              len(calls) == 1 and calls[0][:3] == ("read_flash", "0xe000", "0x2000"), calls)
        check("read: the bytes are kept beside the record, hashed",
              os.path.basename(calls[0][3]) == rec["otadata"]["file"] and len(rec["otadata"]["sha256"]) == 64, rec)
        rec = read(ota_blob([(0, 1, U, False)]))
        check("read: seq 1 (differs from initial) -> PASS, both entries recorded",
              rec.get("verdict") == "PASS" and rec["otadata"]["equals_initial"] is False
              and rec["entries"][0]["valid"] and not rec["entries"][1]["valid"], rec)
        rec = read(ota_blob([(0, 2, U, False)]))
        check("read: seq 2 selects app1 -> OTHER-SLOT", rec.get("verdict") == "OTHER-SLOT", rec)
        rec = read(ota_blob([(0, 1, 3, False)]))
        check("read: app0 by an INVALID entry -> OTHER-SLOT", rec.get("verdict") == "OTHER-SLOT", rec)
        rec = read(ota_blob([(0, 1, 4, False)]))
        check("read: app0 by an ABORTED entry -> OTHER-SLOT", rec.get("verdict") == "OTHER-SLOT", rec)
        rec = read(ota_blob(), rc=2)
        check("read: esptool failed -> ERROR", rec.get("verdict") == "ERROR", rec)
        rec = read(ota_blob(size=0x1000))
        check("read: a short read -> ERROR", rec.get("verdict") == "ERROR", rec)
        x2img_fixture()   # the old, unparseable table
        rec = read(ota_blob())
        check("read: a row table with no otadata -> REFUSED, nothing read",
              rec.get("verdict") == "REFUSED" and calls == [], (rec, calls))
    finally:
        C.esptool, C.X2IMG, C.RUNS = saved
    for label, kw, want in (("no --row", {"row": None, "variant": "jammer"}, "needs --row"),
                            ("--build", {"row": "a" * 16, "variant": "jammer", "build": "b" * 32}, "no --build"),
                            ("good", {"row": "a" * 16, "variant": "jammer"}, None)):
        ns = argparse.Namespace(sub="read-otadata", build=None, row=None, variant=None, dev_builder=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        why = C.build_args_why(ns)
        check("args: read-otadata %s" % label, (why is None) if want is None else (why and want in why), why)
    check("read-otadata runs esptool (its tool checks and port)", "read-otadata" in C.ESPTOOL_SUBS, C.ESPTOOL_SUBS)
    return bad


def row_cases():
    """flash-jammer --row (2026-11-13; x2_test_images SPEC T6, T7, T9; the variant judged
    from the image): sub_flash_jammer on a fake esptool and a fake archive. A good row writes
    every file at its offset in offset order and records the row; each check refuses with
    its reason and esptool never writes; --build still flashes the PlatformIO row."""
    import argparse
    import contextlib
    import hashlib
    import io
    import json
    bad = []
    calls = []

    def fake_esptool(port, *args):
        calls.append(args)
        return 0, ["Hash of data verified."]

    def check(label, ok, got):
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, "" if ok else got))
        if not ok:
            bad.append(label)

    saved = C.esptool, C.X2IMG, C.JAMMER
    C.esptool, C.X2IMG = fake_esptool, X2IMG_FAKE
    try:
        with fake_data():
            dump = os.path.join(C.BACKUPS, "synthetic_dump.bin")
            with open(dump, "wb") as fh:
                fh.write(image(OTHER, GOOD, seq=2))
            record = os.path.join(WORK, "row_dump_record.json")

            def write_dump_record(verdict="PASS"):
                with open(record, "w", encoding="ascii") as fh:
                    json.dump({"dump": os.path.relpath(dump, gi_paths.data_dir()), "dump_size": os.path.getsize(dump),
                               "dump_sha256": C.sha256_file(dump), "verdict": verdict}, fh)

            write_dump_record()

            def flash(row_id, variant="jammer", dev_builder=None, build=None, no_dump=False):
                del calls[:]
                rec = {}
                ns = argparse.Namespace(dump_record=None if no_dump else record, port="FAKE", build=build,
                                        row=row_id, variant=variant, dev_builder=dev_builder, no_dump=no_dump)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        C.sub_flash_jammer(ns, rec)
                except SystemExit as e:
                    rec.setdefault("exit", str(e))
                except Exception as e:   # a crash is reported, never passed
                    rec["crash"] = "%s: %s" % (type(e).__name__, e)
                return rec

            def rid(row):
                return row["folder"].split("/")[1]

            row = x2img_fixture()
            rec = flash(rid(row))
            folder = os.path.join(X2IMG_FAKE, "builds", rid(row))
            want = [("write_flash", "0x0", os.path.join(folder, "bootloader.bin"), "0x8000",
                     os.path.join(folder, "partition-table.bin"), "0xe000",
                     os.path.join(folder, "ota_data_initial.bin"), "0x10000", os.path.join(folder, JAMMER_PN + ".bin"))]
            check("--row: a pinnable row, FLASHED, every file at its offset in order",
                  rec.get("verdict") == "FLASHED" and calls == want, (rec, calls))
            j = rec.get("jammer") or {}
            check("--row: the record keeps the row (folder, variant, name, hashes, tag, pinnable, builder)",
                  j.get("folder") == row["folder"] and j.get("variant") == "jammer" and j.get("project_name") == JAMMER_PN
                  and j.get("image_hash") == GOOD and j.get("app_sha256") == row["app_sha256"]
                  and j.get("code_hash") == row["code_hash"] and j.get("shared_commit") == row["shared_commit"]
                  and j.get("shared_tag") == "2026.11.13" and j.get("pinnable") is True and j.get("builder") == BUILDER
                  and j.get("dev_builder") is None and [f["offset"] for f in j.get("files", [])]
                  == ["0x0", "0x8000", "0xe000", "0x10000"] and rec.get("dump_sha256"), j)
            # the stop_a_only image (the reviewer's can-fail, 2026-11-12)
            row = x2img_fixture(variant="stop_a_only", pn=STOP_A_PN)
            rec = flash(rid(row), variant="stop_a_only")
            check("--row: a stop_a_only row offered as stop_a_only, FLASHED",
                  rec.get("verdict") == "FLASHED" and len(calls) == 1, rec)

            def refused(label, row, want_why, **kw):
                rec = flash(rid(row) if isinstance(row, dict) else row, **kw)
                check(label, rec.get("verdict") == "REFUSED" and want_why in rec.get("why", "") and not calls
                      and "crash" not in rec and "exit" not in rec,
                      (rec.get("verdict"), rec.get("why"), rec.get("crash"), calls))

            refused("--row: a stop_a_only row offered as jammer: REFUSED",
                    x2img_fixture(variant="stop_a_only", pn=STOP_A_PN), "offered as 'jammer'")
            # dev builds (T7): only with --dev-builder equal to the row's builder
            refused("--row: a dev row without --dev-builder: REFUSED", x2img_fixture(pinnable=False), "T7")
            refused("--row: a dev row with another --dev-builder: REFUSED", x2img_fixture(pinnable=False), "T7",
                    dev_builder="can-tools")
            refused("--row: a row without pinnable is a dev row: REFUSED",
                    x2img_fixture(edit=lambda r: r.pop("pinnable")), "T7")
            row = x2img_fixture(pinnable=False)
            rec = flash(rid(row), dev_builder=BUILDER)
            check("--row: a dev row with its builder as --dev-builder, FLASHED, kept in the record",
                  rec.get("verdict") == "FLASHED" and len(calls) == 1
                  and (rec.get("jammer") or {}).get("dev_builder") == BUILDER
                  and (rec.get("jammer") or {}).get("pinnable") is False, rec)
            # the variant is judged from the image (gen-inhibit reviewer, 2026-11-12)
            refused("--row: the app's project_name is not the row's: REFUSED",
                    x2img_fixture(app_pn=STOP_A_PN), "project_name")
            refused("--row: no NOT_FOR_VEHICLE in the name, app and row agreeing: REFUSED",
                    x2img_fixture(pn="vtrux_jammer_x2"), "NOT_FOR_VEHICLE")
            refused("--row: the app's ELF sha256 is not the row's: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(app_elf_sha256=OTHER, image_hash=OTHER)), "ELF sha256")
            refused("--row: app_elf_sha256 alone differs from the app's ELF sha256: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(app_elf_sha256=OTHER)), "ELF sha256")
            refused("--row: image_hash differs from the app's ELF sha256: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(image_hash=OTHER)), "image_hash")

            def flip(name):
                def t(folder):
                    p = os.path.join(folder, name)
                    data = bytearray(open(p, "rb").read())
                    data[5] ^= 1
                    open(p, "wb").write(bytes(data))
                return t
            refused("--row: a file changed since its row (sha256): REFUSED",
                    x2img_fixture(tamper=flip("partition-table.bin")), "partition-table.bin: sha256")
            refused("--row: a file of another size: REFUSED",
                    x2img_fixture(tamper=lambda f: open(os.path.join(f, "bootloader.bin"), "ab").write(b"x")),
                    "bootloader.bin: size")
            refused("--row: a file's md5 not the row's: REFUSED",
                    x2img_fixture(edit=lambda r: r["files"][2].update(md5="0" * 32)), "ota_data_initial.bin: md5")
            refused("--row: a file missing from the folder: REFUSED",
                    x2img_fixture(tamper=lambda f: os.remove(os.path.join(f, "ota_data_initial.bin"))), "missing")
            refused("--row: a file name with a path part: REFUSED",
                    x2img_fixture(edit=lambda r: r["files"][2].update(name="../ota_data_initial.bin")), "bare name")
            refused("--row: no file at 0x8000: REFUSED",
                    x2img_fixture(edit=lambda r: r["files"].pop(1)), "no file at 0x8000")
            refused("--row: no file at the app offset: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(app_offset="0x20000")), "no file at 0x20000")
            refused("--row: two files at one offset: REFUSED",
                    x2img_fixture(edit=lambda r: r["files"][2].update(offset="0x8000")), "two files at one offset")
            refused("--row: the file at app_offset is not the app: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(app_offset="0xe000")), "not the row's app")
            refused("--row: app_sha256 not starting with the folder name: REFUSED",
                    x2img_fixture(edit=lambda r: r.update(app_sha256="f" * 64)), "does not start with")
            refused("--row: image not jammer: REFUSED", x2img_fixture(edit=lambda r: r.update(image="slcan_spi")),
                    "image 'slcan_spi'")
            refused("--row: schema 2: REFUSED", x2img_fixture(edit=lambda r: r.update(schema=2)), "schema 2")
            row = x2img_fixture()
            refused("--row: 15 hex digits: REFUSED", rid(row)[:15], "not 16 lower-case hex")
            refused("--row: upper case: REFUSED", rid(row).upper(), "not 16 lower-case hex")
            refused("--row: no row with that folder: REFUSED", "0123456789abcdef", "got 0")
            row = x2img_fixture()
            row = x2img_fixture(rows=[row])
            refused("--row: two rows with that folder: REFUSED", row, "got 2")
            refused("--row: a line of the manifest not JSON (after a non-row line): REFUSED", x2img_fixture(
                rows=["not a dict"], tamper=lambda f: open(os.path.join(X2IMG_FAKE, "manifest.jsonl"), "a")
                .write("{torn\n")), "is not JSON")
            gi_paths.remove_tree(os.path.dirname(X2IMG_FAKE))
            refused("--row: no manifest.jsonl: REFUSED", "0123456789abcdef", "manifest.jsonl")
            row = x2img_fixture()
            write_dump_record("FAIL")
            rec = flash(rid(row))
            check("--row: a dump record not PASS refuses before any row is read",
                  "not PASS" in rec.get("exit", "") and not calls and "jammer" not in rec, rec)
            # --no-dump (shared/bench SPEC section 8, 2026-11-13): no dump read, none recorded
            rec = flash(rid(row), no_dump=True)
            check("--row --no-dump: FLASHED with no dump read, no_dump true, dump_record None",
                  rec.get("verdict") == "FLASHED" and len(calls) == 1 and rec.get("no_dump") is True
                  and rec.get("dump_record") is None and rec.get("dump_sha256") is None, rec)
            write_dump_record()
            rec = flash(rid(row))
            check("--row with --dump-record: no_dump false, the dump's sha256 recorded",
                  rec.get("verdict") == "FLASHED" and rec.get("no_dump") is False
                  and rec.get("dump_sha256") == C.sha256_file(dump), rec)
            # --build, the PlatformIO jammer, still flashes its manifest.json row
            C.JAMMER = os.path.join(WORK, "x2_jammer")
            app = app_bin("vtrux_jammer_x2_NOT_FOR_VEHICLE_bb20e4a37b64", OTHER)
            md5 = hashlib.md5(app).hexdigest()
            bdir = os.path.join(C.JAMMER, "builds", md5)
            os.makedirs(bdir)
            names = {"app": "j.bin", "bootloader": "bootloader.bin", "partitions": "partitions.bin",
                     "boot_app0": "boot_app0.bin"}
            for k, n in names.items():
                with open(os.path.join(bdir, n), "wb") as fh:
                    fh.write(app if k == "app" else k.encode() * 8)
            with open(os.path.join(C.JAMMER, "builds", "manifest.json"), "w", encoding="ascii") as fh:
                json.dump([{"progname": "j", "app_md5": md5, "elf_sha256": OTHER, "dir": "builds/" + md5,
                            "files": names, "flash_offsets": {"bootloader": "0x0", "partitions": "0x8000",
                                                              "boot_app0": "0xe000", "app": "0x10000"}}], fh)
            rec = flash(None, variant=None, build=md5)
            check("--build: the PlatformIO row by md5, FLASHED, its four files",
                  rec.get("verdict") == "FLASHED" and len(calls) == 1
                  and list(calls[0][1::2]) == ["0x0", "0x8000", "0xe000", "0x10000"]
                  and (rec.get("jammer") or {}).get("app_md5") == md5, (rec, calls))
    finally:
        C.esptool, C.X2IMG, C.JAMMER = saved
        gi_paths.remove_tree(os.path.dirname(X2IMG_FAKE))
        gi_paths.remove_tree(os.path.join(WORK, "x2_jammer"))
    return bad


def esptool_main_cases():
    """main() with its own esptool() against the stand-in esptool package (_fake_esptool.py)
    named by GI_ESPTOOL_ARGV (gen-inhibit reviewer, 2026-11-11; gi_esptool.py): the version
    is read before the board is touched, the underscore command names, the argv and the
    version in each record; another version, or no GI_ESPTOOL_ARGV, refuses with a REFUSED
    record and nothing read; check-dump runs no esptool."""
    import contextlib
    import io
    import json
    import _fake_esptool as FE
    import _fake_serial as FS
    bad = []
    fake = os.path.join(WORK, "fake")
    env_fake = FE.make(fake)
    # the made-up serial devices (DEVICES-SPEC S1, 2026-11-12): the X2's port is its by-id link
    serial_env, ports = FS.fake_bench(os.path.join(WORK, "serial"))
    port = ports["x2"]
    flash = os.path.join(fake, "flash.bin")
    with open(flash, "wb") as fh:
        fh.write(image(OTHER, GOOD, seq=2))
    log = os.path.join(fake, "esptool.log")
    good = [sys.executable, "-m", "esptool"]

    def check(label, ok, got):
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, "" if ok else got))
        if not ok:
            bad.append(label)

    last = {}

    def run(args, version="4.13.0", argv=good, device="good", extra=None):
        """(exit, record, cli lines): main() with sys.argv set, in-process. device: "good"
        (the X2's IDs), None (GI_X2_DEVICE unset) or the variable's text. extra: more
        FAKE_ESPTOOL_* settings for this call. last["files"]: every file the call left in its
        runs folder, the record's name included."""
        keys = ("PYTHONPATH", "FAKE_ESPTOOL_FLASH", "FAKE_ESPTOOL_LOG", "FAKE_ESPTOOL_VERSION", "GI_ESPTOOL_ARGV",
                "FAKE_ESPTOOL_CHIP_FLASH", "FAKE_ESPTOOL_FAIL", "FAKE_ESPTOOL_RC") + tuple(serial_env)
        saved_env = {k: os.environ.get(k) for k in keys}
        saved_argv, saved_runs = sys.argv, C.RUNS
        if os.path.exists(log):
            os.remove(log)
        runs = os.path.join(WORK, "runs")
        gi_paths.remove_tree(runs)
        os.environ.update(env_fake, FAKE_ESPTOOL_FLASH=flash, FAKE_ESPTOOL_LOG=log, FAKE_ESPTOOL_VERSION=version)
        for k in ("FAKE_ESPTOOL_CHIP_FLASH", "FAKE_ESPTOOL_FAIL", "FAKE_ESPTOOL_RC"):
            os.environ.pop(k, None)
        os.environ.update(extra or {})
        os.environ.pop("GI_ESPTOOL_ARGV", None)
        if argv is not None:
            os.environ["GI_ESPTOOL_ARGV"] = json.dumps(argv)
        os.environ.update(serial_env)
        if device is None:
            os.environ.pop("GI_X2_DEVICE")
        elif device != "good":
            os.environ["GI_X2_DEVICE"] = device
        sys.argv, C.RUNS = ["x2_flash_custody.py"] + args, runs
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = C.main()
        except BaseException as e:      # a crash is reported, never passed
            rc = "crash %s: %s" % (type(e).__name__, e)
        finally:
            sys.argv, C.RUNS = saved_argv, saved_runs
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        last["files"] = sorted(os.listdir(runs)) if os.path.isdir(runs) else []
        recs = [n for n in last["files"] if n.startswith("x2_flash_custody_") and n.endswith(".json")]
        rec = None
        if len(recs) == 1:
            with open(os.path.join(runs, recs[0]), encoding="ascii") as fh:
                rec = json.load(fh)
            os.replace(os.path.join(runs, recs[0]), os.path.join(WORK, "last_" + recs[0]))
        cli = [ln[4:] for ln in open(log).read().splitlines() if ln.startswith("cli ")] \
            if os.path.exists(log) else []
        return rc, rec, cli

    with fake_data():
        rc, rec, cli = run(["dump"] + EXP)
        reads = [c for c in cli if "read_flash" in c]
        check("main dump: the version first, then two read_flash 0 ALL, PASS", rc == 0 and rec
              and rec.get("verdict") == "PASS" and cli[:1] == ["version"] and len(reads) == 2
              and all(c.startswith("--chip esp32s3 --port %s --baud 921600 read_flash 0 ALL " % port) for c in reads),
              (rc, rec and rec.get("verdict"), cli))
        check("main dump: the port is the X2's by-id link from GI_X2_DEVICE, kept with what matched (S3)",
              rec and rec.get("port") == port and (rec.get("serial_device") or {}).get("port") == port
              and (rec.get("serial_device") or {}).get("serial") == FS.X2_SERIAL,
              rec and {k: rec.get(k) for k in ("port", "serial_device")})
        check("main dump: the record holds esptool's argv and version",
              rec and rec.get("esptool_argv") == good and rec.get("esptool_version") == "4.13.0",
              rec and {k: rec.get(k) for k in ("esptool_argv", "esptool_version")})
        record = os.path.join(WORK, "main_dump_record.json")
        if rec:
            with open(record, "w", encoding="ascii") as fh:
                json.dump(rec, fh)
        kept = sorted(os.listdir(C.BACKUPS))
        rc, rec2, cli = run(["restore", "--dump-record", record])
        check("main restore: write_flash 0x0, then verify_flash 0x0, VERIFIED, with the version",
              rc == 0 and rec2 and rec2.get("verdict") == "VERIFIED" and rec2.get("esptool_version") == "4.13.0"
              and [c.split()[6:8] for c in cli[1:]] == [["write_flash", "0x0"], ["verify_flash", "0x0"]],
              (rc, rec2 and rec2.get("verdict"), cli))
        # flash-jammer --row through main (2026-11-13): esptool and the port as for the others
        saved_x2img, C.X2IMG = C.X2IMG, X2IMG_FAKE
        try:
            row = x2img_fixture()
            rid = row["folder"].split("/")[1]
            rc, rec9, cli = run(["flash-jammer", "--dump-record", record, "--row", rid, "--variant", "jammer"])
            check("main flash-jammer --row: the version, then one write_flash of the row, FLASHED",
                  rc == 0 and rec9 and rec9.get("verdict") == "FLASHED" and cli[:1] == ["version"] and len(cli) == 2
                  and cli[1].startswith("--chip esp32s3 --port %s --baud 921600 write_flash 0x0 " % port)
                  and cli[1].split()[7::2] == ["0x0", "0x8000", "0xe000", "0x10000"]
                  and rec9.get("esptool_version") == "4.13.0" and rec9.get("port") == port,
                  (rc, rec9 and rec9.get("verdict"), cli))
            for label, args, want in (
                    ("neither --build nor --row", [], "exactly one of"),
                    ("both --build and --row", ["--build", "a89ece8c", "--row", rid, "--variant", "jammer"],
                     "exactly one of"),
                    ("--row without --variant", ["--row", rid], "needs --variant"),
                    ("--build with --variant", ["--build", "a89ece8c", "--variant", "jammer"], "only with --row"),
                    ("--build with --dev-builder", ["--build", "a89ece8c", "--dev-builder", BUILDER], "only with --row")):
                rc, rec3, cli = run(["flash-jammer", "--dump-record", record] + args)
                check("main flash-jammer, %s: REFUSED before esptool and port" % label,
                      rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                      and not cli and rec3.get("port") is None, (rc, rec3, cli))
            rc, rec3, cli = run(["flash-jammer", "--no-dump", "--row", rid, "--variant", "jammer"])
            check("main flash-jammer --no-dump --row: FLASHED, no dump, the port and esptool as for the others",
                  rc == 0 and rec3 and rec3.get("verdict") == "FLASHED" and rec3.get("no_dump") is True
                  and rec3.get("dump_record") is None and len(cli) == 2 and rec3.get("port") == port,
                  (rc, rec3, cli))
            for label, args, want in (
                    ("both --dump-record and --no-dump", ["flash-jammer", "--dump-record", record, "--no-dump",
                                                          "--row", rid, "--variant", "jammer"], "exactly one of --dump"),
                    ("neither --dump-record nor --no-dump", ["flash-jammer", "--row", rid, "--variant", "jammer"],
                     "exactly one of --dump"),
                    ("restore with --no-dump", ["restore", "--dump-record", record, "--no-dump"], "takes no --no-dump")):
                rc, rec3, cli = run(args)
                check("main %s: REFUSED before esptool and port" % label,
                      rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                      and not cli and rec3.get("port") is None, (rc, rec3, cli))
            rc, rec3, cli = run(["check-row", "--row", rid, "--variant", "jammer"], argv=None, device=None)
            check("main check-row: PASS, no esptool and no port needed, the row in the record",
                  rc == 0 and rec3 and rec3.get("verdict") == "PASS" and not cli and rec3.get("port") is None
                  and rec3.get("esptool_version") is None and (rec3.get("jammer") or {}).get("folder") == row["folder"],
                  (rc, rec3, cli))
            rc, rec3, cli = run(["check-row", "--row", rid, "--variant", "stop_a_only"], argv=None, device=None)
            check("main check-row, the jammer row offered as stop_a_only: REFUSED, exit 1",
                  rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and "offered as 'stop_a_only'"
                  in rec3.get("why", "") and not cli, (rc, rec3, cli))
            for label, args, want in (("--build", ["--build", "a89ece8c"], "takes no --build"),
                                      ("no --variant", ["--row", rid], "needs --row")):
                rc, rec3, cli = run(["check-row"] + args)
                check("main check-row, %s: REFUSED" % label,
                      rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                      and not cli, (rc, rec3, cli))
            rc, rec3, cli = run(["dump", "--row", rid] + EXP)
            check("main dump with --row: REFUSED before esptool and port",
                  rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and "takes no --row" in rec3.get("why", "")
                  and not cli and sorted(os.listdir(C.BACKUPS)) == kept, (rc, rec3, cli))
            verify_row_cases(run, last, check, port)   # its checks go to this function's bad
        finally:
            C.X2IMG = saved_x2img
        for label, kw, want in (("esptool 5.0.2", {"version": "5.0.2"}, "5.0.2"),
                                ("GI_ESPTOOL_ARGV unset", {"argv": None}, "GI_ESPTOOL_ARGV"),
                                ("an argv of another form", {"argv": [sys.executable, "esptool.py"]}, "must be")):
            rc, rec3, cli = run(["dump"] + EXP, **kw)
            check("main dump, %s: REFUSED record, nothing read, no dump" % label,
                  rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                  and rec3.get("esptool_version") is None and not [c for c in cli if c != "version"]
                  and sorted(os.listdir(C.BACKUPS)) == kept, (rc, rec3, cli))
        for label, dev, want in (("GI_X2_DEVICE unset", None, "GI_X2_DEVICE is not set"),
                                 ("GI_X2_DEVICE a tty name", "/dev/ttyACM1", "not JSON"),
                                 ("GI_X2_DEVICE matching no device", json.dumps(FS.device("NOSUCHSERIAL")),
                                  "no device matches")):
            rc, rec3, cli = run(["dump"] + EXP, device=dev)
            check("main dump, %s: REFUSED record, no port, nothing read, no dump" % label,
                  rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                  and rec3.get("port") is None and not [c for c in cli if c != "version"]
                  and sorted(os.listdir(C.BACKUPS)) == kept, (rc, rec3, cli))
        # --expect-elf has no default (gen-inhibit reviewer, 2026-11-12): dump and check-dump
        # refuse without a usable one before esptool or the port is touched
        for label, args, want in (("no --expect-elf", [], "is required"),
                                  ("the old default's 7 digits", ["--expect-elf", GOOD[:7]], "not 8 to 64"),
                                  ("an empty --expect-elf", ["--expect-elf", ""], "is required"),
                                  ("a non-hex --expect-elf", ["--expect-elf", GOOD[:7] + "z"], "not 8 to 64"),
                                  ("65 digits", ["--expect-elf", GOOD + "1"], "not 8 to 64")):
            for sub in (["dump"], ["check-dump", "--dump-record", record]):
                rc, rec3, cli = run(sub + args)
                check("main %s, %s: REFUSED before esptool and port" % (sub[0], label),
                      rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                      and rec3.get("expect_elf") == (args[1] if args else None)
                      and not cli and rec3.get("esptool_version") is None and rec3.get("port") is None
                      and sorted(os.listdir(C.BACKUPS)) == kept, (rc, rec3, cli))
        rc, rec4, cli = run(["check-dump", "--dump-record", record] + EXP, argv=None)
        check("main check-dump: no esptool, none needed; argv and version None in the record",
              rc == 0 and rec4 and rec4.get("verdict") == "PASS" and not cli
              and rec4.get("esptool_argv") is None and rec4.get("esptool_version") is None, (rc, rec4, cli))
        rc, rec5, cli = run(["check-dump", "--dump-record", record] + EXP, device=None)
        check("main check-dump: no port, none needed; port and serial_device None in the record",
              rc == 0 and rec5 and rec5.get("verdict") == "PASS" and not cli
              and rec5.get("port") is None and rec5.get("serial_device") is None, (rc, rec5, cli))
        check("main dump and check-dump: the record keeps expect_elf",
              rec and rec.get("expect_elf") == EXP[1] and rec5 and rec5.get("expect_elf") == EXP[1],
              (rec and rec.get("expect_elf"), rec5 and rec5.get("expect_elf")))
        rc, rec6, cli = run(["check-dump", "--dump-record", record, "--expect-elf", GOOD[:12].upper()])
        check("main check-dump: an upper-case --expect-elf is matched in lower case, PASS",
              rc == 0 and rec6 and rec6.get("verdict") == "PASS" and rec6.get("slots_carrying_expected") == ["app1"],
              (rc, rec6 and {k: rec6.get(k) for k in ("verdict", "slots_carrying_expected")}))
        rc, rec7, cli = run(["check-dump", "--dump-record", record, "--expect-elf", NEITHER[:8]])
        check("main check-dump: an image in neither slot FAILs, naming it",
              rc == 1 and rec7 and rec7.get("verdict") == "FAIL" and NEITHER[:8] in rec7.get("why", ""),
              (rc, rec7 and {k: rec7.get(k) for k in ("verdict", "why")}))
        # dump names its files by the second and refuses to overwrite: wait for the first
        # dump's second to pass (in a fast run both fall in one second)
        while rec and time.strftime("%Y%m%dT%H%M%S") <= rec["when"]:
            time.sleep(0.05)
        rc, rec8, cli = run(["dump", "--expect-elf", NEITHER[:8]])
        check("main dump: an image in neither slot FAILs after the two reads",
              rc == 1 and rec8 and rec8.get("verdict") == "FAIL" and rec8.get("reads_match") is True
              and NEITHER[:8] in rec8.get("why", ""), (rc, rec8 and {k: rec8.get(k) for k in ("verdict", "why")}))
    return bad


# esptool 4.13.0's verify_flash output on the bench X2, 2026-11-13 18:50 (stop_b row), the
# folder shortened and the connect lines left out: the parser is checked on the real lines,
# not only on the stand-in's
REAL_VERIFY = """Configuring flash size...
Verifying 0x39a0 (14752) bytes @ 0x00000000 in flash against /x/builds/185f22b8726ecc32/bootloader.bin...
-- verify OK (digest matched)
Verifying 0xc00 (3072) bytes @ 0x00008000 in flash against /x/builds/185f22b8726ecc32/partition-table.bin...
-- verify OK (digest matched)
Verifying 0x2000 (8192) bytes @ 0x0000e000 in flash against /x/builds/185f22b8726ecc32/ota_data_initial.bin...
-- verify FAILED (digest mismatch)
Verifying 0x3ad20 (240928) bytes @ 0x00010000 in flash against /x/builds/185f22b8726ecc32/jammer_stop_b_NOT_FOR_VEHICLE.bin...
-- verify OK (digest matched)

A fatal error occurred: Verify failed.""".splitlines()


def verify_row_cases(run, last, check, port):
    """verify-row (gen-inhibit reviewer, 2026-11-13: x2_test_images SPEC T10's identity with
    committed harness): through main() on the stand-in esptool with a whole-flash image
    (FAKE_ESPTOOL_CHIP_FLASH). The row's own files on the flash, otadata marked VALID by the
    app -> PASS: verify_flash, read-otadata, run, in that order. The can-fail: another row's
    build on the flash -> FAIL; so for a changed bootloader, and for otadata selecting the
    other slot. esptool unable to look, its exit not matching its lines, and run failing ->
    ERROR. Refusals before any board command. check() records each failure in the caller's
    list."""
    got = C.verify_results(REAL_VERIFY)
    check("verify-row: the real 4.13.0 lines parse, otadata FAILED, the rest OK",
          got == {0x0: "OK", 0x8000: "OK", 0xE000: "FAILED", 0x10000: "OK"}, got)
    got = C.verify_results(REAL_VERIFY[:2] + ["-- verify OK"])
    check("verify-row: a result line not the pinned version's gives None",
          got == {0x0: None}, got)

    def chip_from(row, ota=None, flip_at=None):
        """A whole-flash image holding row's files at their offsets, otadata replaced by ota,
        one byte flipped at flip_at."""
        img = bytearray(b"\xff" * 0x20000)
        folder = os.path.join(X2IMG_FAKE, row["folder"])
        for f in row["files"]:
            data = open(os.path.join(folder, f["name"]), "rb").read()
            o = int(f["offset"], 16)
            img[o:o + len(data)] = data
        if ota is not None:
            img[0xE000:0xE000 + len(ota)] = ota
        if flip_at is not None:
            img[flip_at] ^= 1
        p = os.path.join(WORK, "chip_%d.bin" % len(os.listdir(WORK)))
        with open(p, "wb") as fh:
            fh.write(bytes(img))
        return p

    def cmds(cli):
        return [c.split()[6] if len(c.split()) > 6 else c for c in cli]

    row_b = x2img_fixture(variant="stop_a_only", pn=STOP_A_PN, pt=JAMMER_PT)
    chip_b = chip_from(row_b, ota_blob([(0, 1, 2, False)]))     # another row's build on the flash
    row = x2img_fixture(pt=JAMMER_PT)
    rid = row["folder"].split("/")[1]
    args = ["verify-row", "--row", rid, "--variant", "jammer"]
    valid = ota_blob([(0, 1, 2, False)])                       # seq 1, VALID: the app's mark

    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid)})
    v = (rec or {}).get("verify") or {}
    check("verify-row: the row on the flash, otadata VALID -> PASS, exit 0",
          rc == 0 and rec and rec.get("verdict") == "PASS", (rc, rec and rec.get("why")))
    check("verify-row: version, verify_flash, read_flash of otadata, run, in that order",
          cmds(cli) == ["version", "verify_flash", "read_flash", "run"]
          and cli[1].split()[7::2] == ["0x0", "0x8000", "0xe000", "0x10000"]
          and cli[2].split()[7:9] == ["0xe000", "0x2000"] and cli[1].split()[3] == port, cli)
    check("verify-row: otadata differs and is not judged; every other file OK and judged",
          [(f.get("offset"), f.get("result"), f.get("judged")) for f in v.get("files", [])]
          == [("0x0", "OK", True), ("0x8000", "OK", True), ("0xe000", "FAILED", False), ("0x10000", "OK", True)]
          and v.get("verdict") == "PASS", v)
    check("verify-row: the whole verify output kept beside the record, otadata read and kept",
          v.get("log") in last["files"] and any(n.startswith("x2_otadata_%s_" % rid) for n in last["files"])
          and ((rec or {}).get("otadata_read") or {}).get("verdict") == "PASS"
          and ((rec or {}).get("run") or {}).get("rc") == 0, (last["files"], rec))
    check("verify-row: the record keeps the row, the port and esptool's version",
          rec and (rec.get("row") or {}).get("folder") == row["folder"]
          and (rec.get("row") or {}).get("code_hash") == row["code_hash"] and rec.get("port") == port
          and rec.get("esptool_version") == "4.13.0", rec)
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, ota_blob())})
    check("verify-row: otadata still the initial file (erased) -> PASS",
          rc == 0 and rec and rec.get("verdict") == "PASS", (rc, rec and rec.get("why")))
    # the can-fail (reviewer, 2026-11-13): a wrong row's files must FAIL
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_b})
    check("verify-row can-fail: another row's build on the flash -> FAIL, exit 1, the app named",
          rc == 1 and rec and rec.get("verdict") == "FAIL" and JAMMER_PN + ".bin" in rec.get("why", "")
          and ((rec.get("verify") or {}).get("verdict")) == "FAIL", (rc, rec and rec.get("why")))
    check("verify-row can-fail: run still last after a FAIL (the stub left by verify_flash)",
          cmds(cli) == ["version", "verify_flash", "read_flash", "run"], cli)
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid, flip_at=0x10)})
    check("verify-row: a changed bootloader -> FAIL, naming it",
          rc == 1 and rec and rec.get("verdict") == "FAIL" and "bootloader.bin" in rec.get("why", ""),
          (rc, rec and rec.get("why")))
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, ota_blob([(0, 2, 2, False)]))})
    check("verify-row: files match but otadata selects app1 -> FAIL (OTHER-SLOT)",
          rc == 1 and rec and rec.get("verdict") == "FAIL"
          and ((rec.get("otadata_read") or {}).get("verdict")) == "OTHER-SLOT", (rc, rec and rec.get("why")))
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid),
                                    "FAKE_ESPTOOL_FAIL": "verify_flash"})
    check("verify-row: verify_flash could not look (no result lines) -> ERROR, run still last",
          rc == 1 and rec and rec.get("verdict") == "ERROR" and "no verify result" in rec.get("why", "")
          and cmds(cli)[-1] == "run", (rc, rec and rec.get("why"), cli))
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, ota_blob()),
                                    "FAKE_ESPTOOL_RC": "verify_flash=2"})
    check("verify-row: every line OK but esptool exits 2 -> ERROR",
          rc == 1 and rec and rec.get("verdict") == "ERROR" and "does not match its lines" in rec.get("why", ""),
          (rc, rec and rec.get("why")))
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid),
                                    "FAKE_ESPTOOL_FAIL": "read_flash"})
    check("verify-row: files match but the otadata read failed -> ERROR, run still last",
          rc == 1 and rec and rec.get("verdict") == "ERROR"
          and ((rec.get("otadata_read") or {}).get("verdict")) == "ERROR" and cmds(cli)[-1] == "run",
          (rc, rec and rec.get("why"), cli))
    rc, rec, cli = run(args, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid), "FAKE_ESPTOOL_FAIL": "run"})
    check("verify-row: identity matched but run failed -> ERROR naming the stub",
          rc == 1 and rec and rec.get("verdict") == "ERROR" and "stub" in rec.get("why", ""),
          (rc, rec and rec.get("why")))
    for label, a2, kw, want in (
            ("the row offered as stop_a_only", ["verify-row", "--row", rid, "--variant", "stop_a_only"], {},
             "offered as 'stop_a_only'"),
            ("no --variant", ["verify-row", "--row", rid], {}, "needs --row"),
            ("--build", ["verify-row", "--build", "a89ece8c"], {}, "takes no --build"),
            ("GI_ESPTOOL_ARGV unset", args, {"argv": None}, "GI_ESPTOOL_ARGV")):
        rc, rec, cli = run(a2, extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_from(row, valid)}, **kw)
        check("verify-row, %s: REFUSED, no board command" % label,
              rc == 1 and rec and rec.get("verdict") == "REFUSED" and want in rec.get("why", "")
              and not [c for c in cli if c != "version"], (rc, rec, cli))
    dev = x2img_fixture(pinnable=False, pt=JAMMER_PT)
    did = dev["folder"].split("/")[1]
    chip_d = chip_from(dev, valid)
    rc, rec, cli = run(["verify-row", "--row", did, "--variant", "jammer"], extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_d})
    check("verify-row, a dev row without --dev-builder: REFUSED (T7), no board command",
          rc == 1 and rec and rec.get("verdict") == "REFUSED" and "T7" in rec.get("why", "")
          and not [c for c in cli if c != "version"], (rc, rec, cli))
    rc, rec, cli = run(["verify-row", "--row", did, "--variant", "jammer", "--dev-builder", BUILDER],
                       extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_d})
    check("verify-row, a dev row with its builder: PASS, dev_builder kept",
          rc == 0 and rec and rec.get("verdict") == "PASS" and (rec.get("row") or {}).get("dev_builder") == BUILDER,
          (rc, rec and rec.get("why")))
    old = x2img_fixture()   # a table the tool cannot read an otadata partition from
    oid = old["folder"].split("/")[1]
    rc, rec, cli = run(["verify-row", "--row", oid, "--variant", "jammer"],
                       extra={"FAKE_ESPTOOL_CHIP_FLASH": chip_d})
    check("verify-row, a row table with no otadata: REFUSED, no board command",
          rc == 1 and rec and rec.get("verdict") == "REFUSED" and "otadata partitions" in rec.get("why", "")
          and not [c for c in cli if c != "version"], (rc, rec, cli))


def record_name_cases():
    """write_rec: three records of one subcommand in one second get three files, _2 and _3
    added, the first left as written (2026-11-13: the second check-row of a second crashed)."""
    import contextlib
    import io
    import json
    bad = []
    runs = os.path.join(WORK, "runs_names")
    saved = C.RUNS
    C.RUNS = runs
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            paths = [C.write_rec("check-row", {"when": "20261113T000000", "k": k}) for k in range(3)]
        names = sorted(os.listdir(runs))
        want = ["x2_flash_custody_check-row_20261113T000000%s.json" % s for s in ("", "_2", "_3")]
        firsts = [json.load(open(os.path.join(runs, n), encoding="ascii"))["k"] for n in want] \
            if names == want else None
        ok = names == want and firsts == [0, 1, 2] and [os.path.basename(p) for p in paths] == want
    except Exception as e:   # a crash is reported, never passed
        ok, names = False, "%s: %s" % (type(e).__name__, e)
    finally:
        C.RUNS = saved
        gi_paths.remove_tree(runs)
    print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", "three records in one second: three files, none overwritten",
                               "" if ok else names))
    if not ok:
        bad.append("record names")
    return bad


def import_refusals():
    """build_lookup comes only from $INTERPOSER_CODE: unset or wrong refuses."""
    import subprocess
    bad = []
    script = os.path.join(HERE, "x2_flash_custody.py")
    for label, val, want in (("INTERPOSER_CODE unset refuses", None, "not set"),
                             ("INTERPOSER_CODE without build_lookup refuses", WORK,
                              "no build_lookup.py")):
        env = {k: v for k, v in os.environ.items() if k != "INTERPOSER_CODE"}
        if val is not None:
            env["INTERPOSER_CODE"] = val
        r = subprocess.run([sys.executable, script, "check-dump"], env=env,
                           capture_output=True, text=True, timeout=60)
        ok = r.returncode != 0 and want in r.stderr
        print("  %-4s %-48s rc %d" % ("ok" if ok else "FAIL", label, r.returncode))
        if not ok:
            bad.append(label)
    retired = os.path.normcase(os.path.abspath(gi_paths.project("projects", "vtrux", "tools")))
    ok = (os.path.normcase(C.BUILD_LOOKUP) == os.path.normcase(os.path.abspath(os.path.join(
        os.environ["INTERPOSER_CODE"], "build_lookup.py"))) and
        not os.path.normcase(C.BUILD_LOOKUP).startswith(retired))
    print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", "loaded from INTERPOSER_CODE",
                               C.BUILD_LOOKUP))
    if not ok:
        bad.append("loaded from the retired SeaDrive copy")
    return bad


def main():
    bad = []
    os.makedirs(WORK, exist_ok=True)
    try:
        for label, img, want_slot, want_hashes in CASES:
            parts = C.partitions(img)
            slot, why = C.boot_slot(img, parts)
            hashes = C.app_hashes(img, parts, WORK)
            left = os.listdir(WORK)
            carriers = [k for k, v in hashes.items() if v and v.startswith("7f197bfd")]
            ok = (slot == want_slot and hashes == want_hashes and not left
                  and len(parts) == 4)
            print("  %-4s %-48s slot %s (%s), carriers %s"
                  % ("ok" if ok else "FAIL", label, slot, why, carriers))
            if not ok:
                bad.append(label)
                print("       hashes %s, left %s" % (hashes, left))
        # a table that does not start with the magic yields no partitions
        blank = b"\xff" * 0x30000
        ok = C.partitions(blank) == []
        print("  %-4s %-48s" % ("ok" if ok else "FAIL", "no partition table -> []"))
        if not ok:
            bad.append("blank")
        bad += check_dump_cases()
        bad += record_path_cases()
        bad += row_cases()
        bad += otadata_cases()
        bad += record_name_cases()
        bad += esptool_main_cases()
        bad += import_refusals()
    finally:
        gi_paths.remove_tree(WORK)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
