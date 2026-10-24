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

Run: py -3.14 tools/gen_inhibit/bench/test_x2_flash_custody.py
"""
import os
import shutil
import struct
import sys
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
        shutil.rmtree(FAKE_DATA, ignore_errors=True)


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
        if args[0] == "read-flash":
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
            with contextlib.redirect_stdout(io.StringIO()):
                C.sub_dump(argparse.Namespace(port="FAKE", expect_elf="7f197bfd"), rec)
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
            writes = [c for c in calls if c[0] == "write-flash"]
            check("restore writes back that dump, VERIFIED",
                  got[0] == "returned" and r2.get("verdict") == "VERIFIED" and len(writes) == 1
                  and os.path.samefile(writes[0][-1], dump), r2.get("verdict"))
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
        bad += import_refusals()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
