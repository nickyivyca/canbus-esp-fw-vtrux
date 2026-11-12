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

Run: py -3.14 gen-inhibit/tools/bench/test_x2_flash_custody.py
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
    bad = []
    fake = os.path.join(WORK, "fake")
    env_fake = FE.make(fake)
    flash = os.path.join(fake, "flash.bin")
    with open(flash, "wb") as fh:
        fh.write(image(OTHER, GOOD, seq=2))
    log = os.path.join(fake, "esptool.log")
    good = [sys.executable, "-m", "esptool"]

    def check(label, ok, got):
        print("  %-4s %-48s %s" % ("ok" if ok else "FAIL", label, "" if ok else got))
        if not ok:
            bad.append(label)

    def run(args, version="4.13.0", argv=good):
        """(exit, record, cli lines): main() with sys.argv set, in-process."""
        keys = ("PYTHONPATH", "FAKE_ESPTOOL_FLASH", "FAKE_ESPTOOL_LOG", "FAKE_ESPTOOL_VERSION", "GI_ESPTOOL_ARGV")
        saved_env = {k: os.environ.get(k) for k in keys}
        saved_argv, saved_runs = sys.argv, C.RUNS
        if os.path.exists(log):
            os.remove(log)
        runs = os.path.join(WORK, "runs")
        gi_paths.remove_tree(runs)
        os.environ.update(env_fake, FAKE_ESPTOOL_FLASH=flash, FAKE_ESPTOOL_LOG=log, FAKE_ESPTOOL_VERSION=version)
        os.environ.pop("GI_ESPTOOL_ARGV", None)
        if argv is not None:
            os.environ["GI_ESPTOOL_ARGV"] = json.dumps(argv)
        sys.argv, C.RUNS = ["x2_flash_custody.py"] + args + ["--port", "FAKE"], runs
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
        recs = sorted(os.listdir(runs)) if os.path.isdir(runs) else []
        rec = None
        if len(recs) == 1:
            with open(os.path.join(runs, recs[0]), encoding="ascii") as fh:
                rec = json.load(fh)
            os.replace(os.path.join(runs, recs[0]), os.path.join(WORK, "last_" + recs[0]))
        cli = [ln[4:] for ln in open(log).read().splitlines() if ln.startswith("cli ")] \
            if os.path.exists(log) else []
        return rc, rec, cli

    with fake_data():
        rc, rec, cli = run(["dump"])
        reads = [c for c in cli if "read_flash" in c]
        check("main dump: the version first, then two read_flash 0 ALL, PASS", rc == 0 and rec
              and rec.get("verdict") == "PASS" and cli[:1] == ["version"] and len(reads) == 2
              and all(c.startswith("--chip esp32s3 --port FAKE --baud 921600 read_flash 0 ALL ") for c in reads),
              (rc, rec and rec.get("verdict"), cli))
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
        for label, kw, want in (("esptool 5.0.2", {"version": "5.0.2"}, "5.0.2"),
                                ("GI_ESPTOOL_ARGV unset", {"argv": None}, "GI_ESPTOOL_ARGV"),
                                ("an argv of another form", {"argv": [sys.executable, "esptool.py"]}, "must be")):
            rc, rec3, cli = run(["dump"], **kw)
            check("main dump, %s: REFUSED record, nothing read, no dump" % label,
                  rc == 1 and rec3 and rec3.get("verdict") == "REFUSED" and want in rec3.get("why", "")
                  and rec3.get("esptool_version") is None and not [c for c in cli if c != "version"]
                  and sorted(os.listdir(C.BACKUPS)) == kept, (rc, rec3, cli))
        rc, rec4, cli = run(["check-dump", "--dump-record", record], argv=None)
        check("main check-dump: no esptool, none needed; argv and version None in the record",
              rc == 0 and rec4 and rec4.get("verdict") == "PASS" and not cli
              and rec4.get("esptool_argv") is None and rec4.get("esptool_version") is None, (rc, rec4, cli))
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
        bad += esptool_main_cases()
        bad += import_refusals()
    finally:
        gi_paths.remove_tree(WORK)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
