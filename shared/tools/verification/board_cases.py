#!/usr/bin/env python3
"""R21 board-data check (vtrux_checks board) on real release builds, shown passing and failing.

    python -B board_cases.py --root <short folder in scratch/> --out <run folder> --board <board folder>
                             [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B board_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture (a
clone of gen-inhibit with fixture commits, under --root), minimal project and plumbing it
reuses. --board is a board component's folder, shared/boards/wican_v3 at the commit under test,
holding board.json and the reference table. The board is the project's one shared/boards/*
component in dependencies.lock, read from managed_components/ (SPEC R21, shared/main 2f72a20).
No board component is built yet, so after each release build the case plants one: the folder
copied to managed_components/vtrux__wican_v3/ (a case may change its board.json or add a file),
and a git entry for it, path shared/boards/wican_v3, added to the project's dependencies.lock.
The release build sees a clean tree; the planted folder is removed and the lock restored before
the next build and at the end.

What each case expects is worked out here, not by the check's reader: the table by comparing
build/partition_table/partition-table.bin with the reference file byte for byte; the slot by
this file's own reading of the reference table (the smallest app entry); the flash size by
reading the high four bits of byte 3 of the app .bin and of build/bootloader/bootloader.bin,
against this file's own table of ESP-IDF's image format (FLASH_FIELD); the build's target
from build/config/sdkconfig.h. The tool prefix the check prints must be the build's own
monitor_toolprefix (project_description.json) when the targets agree, else the compiler
ESP-IDF's toolchain-<board target>.cmake names, less "gcc". A case whose trigger does not show
there is VOID.

    release_pass       the fixture and the WiCAN's board data (4MB): passes
    other_target       the same build, the board's target esp32s3: fails on the target
    small_slot         the same build, the reference a copy of the stock table with ota_0 one
                       byte smaller than the image (MD5 entry redone): fails on the slot, and
                       on the table, which then differs
    missing_reference  the same build, the reference path naming no file: cannot read
    no_board           the same build, no shared/boards/* entry in the lock: cannot read
    two_boards         the same build, a second shared/boards/* git entry: cannot read
    local_board        the same build, the board's entry a local folder (override_path):
                       cannot read, since it is not downloaded into managed_components/
    table_differs      a release build of a commit making storage 296K in the partition CSV:
                       fails on the table
    flash_larger       a release build of a commit setting the committed sdkconfig's flash size
                       to 8MB: fails on the flash size (both headers), and on nothing else
    s3_pass            the minimal project set to esp32s3, its own partition table committed as
                       the reference, its own flash size as the board's: passes, the prefix
                       xtensa-esp32s3-elf-; and the R17 scope check passes on it (--release)
    s3_board_c3        the same build, the board's target esp32c3: fails on the target
    s3_elf_reader      not R21: R20's ELF reader (test_switches.py's test_as_nm) on the esp32s3
                       build's ELF, against that toolchain's nm (the build's monitor_toolprefix)

A smaller flash size than the board's is not a case: the partition table no longer fits, so the
build itself fails.

Before each case it checks the bench lock, the "No builds" line and running simulators, as
release_cases.py does.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402

TOOLS = RC.TOOLS
CSV_OLD = "storage,  data, spiffs, ,        300K,"
CSV_NEW = "storage,  data, spiffs, ,        296K,"
_TARGET = re.compile(r'^#define CONFIG_IDF_TARGET "([^"]+)"$', re.M)
_PREFIX_LINE = re.compile(r"^  tool prefix +(\S+)", re.M)
_COMPILER = re.compile(r"^\s*set\(\s*CMAKE_C_COMPILER\s+(\S+)gcc\s*\)", re.M)
_FLASHSIZE = re.compile(r'^#define CONFIG_ESPTOOLPY_FLASHSIZE "([^"]+)"$', re.M)
KINDS = (("table", "not byte-identical"), ("slot", "-byte slot by "), ("flash", "header gives flash size "),
         ("target", "CONFIG_IDF_TARGET is "))
# ESP-IDF's image format: the flash size field (byte 3, high four bits) of an image header
FLASH_FIELD = {"1MB": 0, "2MB": 1, "4MB": 2, "8MB": 3, "16MB": 4, "32MB": 5, "64MB": 6, "128MB": 7}
SDK_OLD = ("CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y", "# CONFIG_ESPTOOLPY_FLASHSIZE_8MB is not set",
           'CONFIG_ESPTOOLPY_FLASHSIZE="4MB"')
SDK_NEW = ("# CONFIG_ESPTOOLPY_FLASHSIZE_4MB is not set", "CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y",
           'CONFIG_ESPTOOLPY_FLASHSIZE="8MB"')


# ---------- the tester's own readings (pure; --self-test runs them on fakes) ----------

def app_sizes(table):
    """Sizes of the app entries (type 0) of a partition-table .bin, read entry by entry."""
    out = []
    for i in range(0, len(table) - 31, 32):
        if table[i:i + 2] != b"\xaa\x50":
            break
        if table[i + 2] == 0:
            out.append(struct.unpack_from("<I", table, i + 8)[0])
    return out


def shrink_ota0(table, size):
    """The table with the entry labelled ota_0 given size, and its MD5 entry redone."""
    b = bytearray(table)
    n = 0
    for i in range(0, len(b) - 31, 32):
        if b[i:i + 2] == b"\xaa\x50" and b[i + 12:i + 28].rstrip(b"\x00") == b"ota_0":
            struct.pack_into("<I", b, i + 8, size)
            n += 1
        elif b[i:i + 2] == b"\xeb\xeb":
            b[i + 16:i + 32] = hashlib.md5(bytes(b[:i])).digest()
    if n != 1:
        raise RuntimeError("%d ota_0 entries" % n)
    return bytes(b)


def flash_field(image):
    """The flash size field of an image's header: the high four bits of byte 3."""
    return image[3] >> 4 if len(image) > 3 and image[0] == 0xE9 else None


def expected(built, reference, image_size, build_target, board_target, fields=(), board_flash=None):
    """The failure kinds the check must name; fields: the headers' flash size fields."""
    want = []
    if built != reference:
        want.append("table")
    sizes = app_sizes(reference)
    if sizes and image_size > min(sizes):
        want.append("slot")
    if any(f != FLASH_FIELD.get(board_flash) for f in fields):
        want.append("flash")
    if build_target != board_target:
        want.append("target")
    return want


def named(out):
    lines = [l for l in out.splitlines() if l.startswith("    ")]
    return [k for k, mark in KINDS if any(mark in l for l in lines)]


def judge(rc, out, want, prefix):
    """(verdict, detail): want the failure kinds; prefix the tool prefix the check must print."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    if "cannot read" in out:
        return "FAIL", "the check could not read the build: %s" % out.strip().splitlines()[-1][:200]
    if rc != (1 if want else 0):
        return "FAIL", "the check exited %d, wanted %d" % (rc, 1 if want else 0)
    if ("PASS R21" in out) != (rc == 0):
        return "FAIL", "exit %d but %s PASS R21" % (rc, "prints" if rc else "does not print")
    got = named(out)
    if got != want:
        return "FAIL", "the check names %s, wanted %s" % (got, want)
    p = _PREFIX_LINE.findall(out)
    if p != [prefix]:
        return "FAIL", "the check prints tool prefix %s, wanted %s" % (p, prefix)
    return "PASS", "exit %d%s; tool prefix %s" % (rc, (", names " + ", ".join(want)) if want else "", prefix)


def judge_elf_reader(rc, out):
    out = out.replace("\r\n", "\n")      # unittest's output through a Windows pipe
    if rc != 0 or not re.search(r"^test_as_nm .*\.\.\. ok$", out, re.M):
        return "FAIL", "exit %d; wanted test_as_nm run and ok: %s" % (rc, out.strip().splitlines()[-1:] if out.strip() else "")
    return "PASS", "test_as_nm ran and passed"


def judge_unreadable(rc, out, mark):
    if rc != 1 or "FAIL R21: cannot read" not in out:
        return "FAIL", "exit %d; wanted 1 with 'cannot read'" % rc
    if mark not in out:
        return "FAIL", "exit 1, but the output does not say %r" % mark
    return "PASS", "exit 1, cannot read: %r" % mark


def self_test():
    ok = True

    def tab(sizes, extra=b""):
        b = b"".join(b"\xaa\x50" + bytes([0 if s else 1, 0x10]) + struct.pack("<II", 0x10000, s or 0x4000)
                     + (b"ota_0" if i == 0 else b"x").ljust(16, b"\x00") + b"\x00" * 4 for i, s in enumerate(sizes))
        b += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(b).digest() + extra
        return b + b"\xff" * (0xC00 - len(b))
    t = tab([100, 120, None])
    checks = (("app sizes", app_sizes(t), [100, 120]),
              ("app sizes stop at the end", app_sizes(tab([100]) + b"\xaa\x50" + b"\x00" * 30), [100]),
              ("ota_0 shrunk", app_sizes(shrink_ota0(t, 50)), [50, 120]),
              ("MD5 redone", shrink_ota0(t, 50)[3 * 32 + 16:3 * 32 + 32] == hashlib.md5(shrink_ota0(t, 50)[:96]).digest(), True),
              ("nothing", expected(t, t, 100, "c3", "c3"), []),
              ("at the slot", expected(t, t, 100, "c3", "c3"), []),
              ("over the slot", expected(t, t, 101, "c3", "c3"), ["slot"]),
              ("table differs", expected(t[:-1] + b"\x00", t, 1, "c3", "c3"), ["table"]),
              ("target", expected(t, t, 1, "c3", "s3"), ["target"]),
              ("all three", expected(shrink_ota0(t, 5), t, 101, "c3", "s3"), ["table", "slot", "target"]),
              ("flash size field", flash_field(bytes([0xE9, 3, 2, 0x2F])), 2),
              ("flash size field, not an image", flash_field(bytes([0xE8, 3, 2, 0x2F])), None),
              ("flash size as the board's", expected(t, t, 1, "c3", "c3", (2, 2), "4MB"), []),
              ("app header larger", expected(t, t, 1, "c3", "c3", (3, 2), "4MB"), ["flash"]),
              ("bootloader header larger", expected(t, t, 1, "c3", "c3", (2, 3), "4MB"), ["flash"]),
              ("no header read", expected(t, t, 1, "c3", "c3", (None, 2), "4MB"), ["flash"]),
              ("all four", expected(shrink_ota0(t, 5), t, 101, "c3", "s3", (3, 3), "4MB"),
               ["table", "slot", "flash", "target"]))
    for label, got, want in checks:
        print("  %s %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    try:
        shrink_ota0(tab([None]).replace(b"ota_0", b"otaX_"), 5)
        print("  FAIL shrink_ota0 without ota_0 accepted")
        ok = False
    except RuntimeError:
        print("  ok   shrink_ota0 without ota_0 refused")
    out_t = ("R21 board: ...\n  tool prefix      a-elf- (x)\nFAIL R21: 1 failure(s)\n"
             "    the build's partition table is not byte-identical to the board's reference\n")
    out_p = "R21 board: ...\n  tool prefix      a-elf- (x)\nPASS R21\n"
    out_f = ("R21 board: ...\n  tool prefix      a-elf- (x)\nFAIL R21: 1 failure(s)\n"
             "    the app image's header gives flash size 8MB, the board's is 4MB\n")
    for label, args, want in (("passes as wanted", (0, out_p, [], "a-elf-"), "PASS"),
                              ("fails as wanted", (1, out_t, ["table"], "a-elf-"), "PASS"),
                              ("wrong prefix", (0, out_p, [], "b-elf-"), "FAIL"),
                              ("no prefix line", (0, "PASS R21\n", [], "a-elf-"), "FAIL"),
                              ("passes when it should fail", (0, out_p, ["slot"], "a-elf-"), "FAIL"),
                              ("fails when it should pass", (1, out_t, [], "a-elf-"), "FAIL"),
                              ("names another kind", (1, out_t, ["slot"], "a-elf-"), "FAIL"),
                              ("names one too many", (1, out_t + "    the image is 9 bytes, over the 8-byte slot by 1\n",
                                                      ["table"], "a-elf-"), "FAIL"),
                              ("flash named", (1, out_f, ["flash"], "a-elf-"), "PASS"),
                              ("flash not named", (1, out_t, ["table", "flash"], "a-elf-"), "FAIL"),
                              ("flash named, not wanted", (1, out_t + out_f.split("\n", 3)[3], ["table"], "a-elf-"), "FAIL"),
                              ("exit 0 without PASS", (0, "  tool prefix      a-elf- (x)\n", [], "a-elf-"), "FAIL"),
                              ("exit 1 with PASS", (1, out_t + "PASS R21\n", ["table"], "a-elf-"), "FAIL"),
                              ("cannot read", (1, "FAIL R21: cannot read the build or its board data: x\n", ["table"], "a-elf-"), "FAIL"),
                              ("cannot read, with a failure line", (1, "FAIL R21: cannot read the build: x\n" + out_t, ["table"],
                                                                    "a-elf-"), "FAIL"),
                              ("exit 1, nothing named", (1, "  tool prefix      a-elf- (x)\nFAIL R21: 0 failure(s)\n", [],
                                                         "a-elf-"), "FAIL"),
                              ("crashes", (2, "Traceback", []), "ERROR")):
        v = judge(*args)[0] if len(args) == 4 else judge(*args, "a-elf-")[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    for label, args, want in (("unreadable, said", (1, "FAIL R21: cannot read ...: not built yet", "not built yet"), "PASS"),
                              ("unreadable, not said", (1, "FAIL R21: cannot read ...: x", "not built yet"), "FAIL"),
                              ("passes instead", (0, "PASS R21", "not built yet"), "FAIL")):
        v = judge_unreadable(*args)[0]
        print("  %s judge_unreadable(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    for label, args, want in (("ran and passed", (0, "test_as_nm (x.Elf.test_as_nm) ... ok\n\nOK\n"), "PASS"),
                              ("ran and passed, CRLF", (0, "test_as_nm (x) ... ok\r\n\r\nOK\r\n"), "PASS"),
                              ("skipped", (0, "test_as_nm (x) ... skipped 'no real ELF and nm'\n\nOK (skipped=1)\n"), "FAIL"),
                              ("ok, but exit 1", (1, "test_as_nm (x) ... ok\n\nOK\n"), "FAIL"),
                              ("failed", (1, "test_as_nm (x) ... FAIL\n\nFAILED (failures=1)\n"), "FAIL"),
                              ("ran nothing", (0, "Ran 0 tests\n\nOK\n"), "FAIL")):
        v = judge_elf_reader(*args)[0]
        print("  %s judge_elf_reader(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    # the planted lock entry, read by the lock reader the checks use: the old entries kept
    from vtrux_build import lockfile
    from vtrux_checks import boarddata
    old = ("dependencies:\r\n  espressif/mdns:\r\n    component_hash: ab\r\n    source:\r\n"
           "      registry_url: https://example.invalid/\r\n      type: service\r\n    version: 1.0.0\r\n"
           "  idf:\r\n    source:\r\n      type: idf\r\n    version: 5.4.1\r\ndirect_dependencies:\r\n- idf\r\n")
    for label, entries, want in (("one git board", {BOARD: GIT_SOURCE}, [BOARD]),
                                 ("none", {}, []),
                                 ("local", {BOARD: {"type": "local", "path": "../s/shared/boards/wican_v3"}}, [BOARD])):
        got = lockfile.parse(lock_with(old, entries))
        boards = sorted(n for n, e in got.items() if boarddata.is_board(e))
        good = boards == want and got.get("espressif/mdns", {}).get("version") == "1.0.0" and "idf" in got \
            and all(got[n]["source"] == entries[n] for n in entries)
        print("  %s lock_with(), %s: boards %s, %d entries" % ("ok  " if good else "FAIL", label, boards, len(got)))
        ok &= good
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def check(proj, log, what="board", *extra):
    e = RC.idf_env(PYTHONPATH=TOOLS)
    return RC.run([sys.executable, "-B", "-m", "vtrux_checks", what, "--build", os.path.join(proj, "build")] + list(extra),
                  cwd=proj, env=e, log=log)


def read(p, mode="rb"):
    with open(p, mode) as f:
        return f.read()


BOARD = "vtrux/wican_v3"
GIT_SOURCE = {"type": "git", "git": "https://example.invalid/vtrux.git", "path": "shared/boards/wican_v3"}


def lock_with(text, entries):
    """dependencies.lock's text with entries {name: source} added first under dependencies:, as
    the Component Manager writes a downloaded component's entry."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = lines.index("dependencies:")
    add = []
    for name, src in entries.items():
        add += ["  %s:" % name, "    component_hash: %s" % ("0" * 64), "    source:"]
        add += ["      %s: %s" % kv for kv in sorted(src.items())] + ["    version: 2026.10.27"]
    return "\n".join(lines[:i + 1] + add + lines[i + 1:])


def board_dir(proj):
    return os.path.join(proj, "managed_components", BOARD.replace("/", "__", 1))


def unplant(proj):
    """The planted board removed and dependencies.lock as committed (when git tracks one)."""
    RC.rmtree(board_dir(proj))
    if RC.run(["git", "-C", proj, "ls-files", "--error-unmatch", "dependencies.lock"])[0] == 0:
        RC.git(proj, "checkout", "-q", "--", "dependencies.lock")


def plant(ctx, proj, edit=None, extra=None, entries=None):
    """--board copied to managed_components/vtrux__wican_v3/, its board.json updated with edit,
    extra {file: bytes} written beside it, and the lock given entries (default: the board's git
    entry). The board's data as planted, the reference table's path absolute."""
    unplant(proj)
    d = board_dir(proj)
    shutil.copytree(ctx.board, d)
    data = json.loads(read(os.path.join(d, "board.json"), "r"))
    data.update(edit or {})
    with open(os.path.join(d, "board.json"), "w", newline="\n") as f:
        json.dump(data, f, indent=2)
    for name, b in (extra or {}).items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(b)
    lp = os.path.join(proj, "dependencies.lock")
    text = lock_with(read(lp, "r"), entries if entries is not None else {BOARD: GIT_SOURCE})
    with open(lp, "w", newline="\n") as f:
        f.write(text)
    return dict(data, partition_table=os.path.join(d, data["partition_table"]))


def facts(proj, board):
    """(want, prefix, note) from the build and the board, by the tester's own readings."""
    b = os.path.join(proj, "build")
    desc = json.loads(read(os.path.join(b, "project_description.json"), "r"))
    built = read(os.path.join(b, "partition_table", "partition-table.bin"))
    ref_p = board["partition_table"]
    ref = read(ref_p) if os.path.exists(ref_p) else b""
    image = os.path.getsize(os.path.join(b, desc["app_bin"]))
    sdk = read(os.path.join(b, "config", "sdkconfig.h"), "r")
    m = _TARGET.findall(sdk)
    target = m[0] if len(m) == 1 else None
    fs = _FLASHSIZE.findall(sdk)
    sdk_flash = fs[0] if len(fs) == 1 else None
    fields = tuple(flash_field(read(os.path.join(b, x))[:4]) for x in (desc["app_bin"], "bootloader/bootloader.bin"))
    if target == board["target"]:
        prefix = desc.get("monitor_toolprefix")
    else:
        tc = os.path.join(os.environ["IDF_PATH"], "tools", "cmake", "toolchain-%s.cmake" % board["target"])
        c = _COMPILER.findall(read(tc, "r")) if os.path.exists(tc) else []
        prefix = c[0] if len(c) == 1 else None
    sizes = app_sizes(ref)
    note = "table %s; image %d, slot %s; flash fields app/bootloader %s, sdkconfig %s, board %s; build target %s, board %s; prefix %s" % (
        "same" if built == ref else "differs", image, min(sizes) if sizes else None, "/".join(map(str, fields)),
        sdk_flash, board["flash_size"], target, board["target"], prefix)
    return (expected(built, ref, image, target, board["target"], fields, board["flash_size"]), prefix, note,
            (built, ref, image, sizes, target, fields, sdk_flash))


def judge_case(ctx, name, proj, log, edit, trigger, extra=None):
    """edit: board.json's changes for this case (None: --board's own); trigger(want, raw) must hold,
    else VOID."""
    board = plant(ctx, proj, edit, extra)
    want, prefix, note, raw = facts(proj, board)
    with open(log, "a") as f:
        f.write("%s: %s; expected %s\n" % (name, note, want))
    if not trigger(want, raw) or not prefix:
        RC.report(name, "VOID", "the trigger does not show: %s; expected %s" % (note, want), ctx.log)
        return
    rc, out = check(proj, log)
    RC.report(name, *judge(rc, out, want, prefix), ctx.log)


def fixture_built(ctx, name, log, change=None):
    """A release build of the fixture; change: (file, old lines, new lines, message) committed first."""
    unplant(ctx.p1)
    RC.reset(ctx.p1, "fixture")
    if change:
        rel, old, new, msg = change
        RC.git(ctx.p1, "checkout", "-q", "-B", "r21-" + name, "origin/fixture")
        p = os.path.join(ctx.p1, rel)
        lines = read(p, "r").replace("\r\n", "\n").split("\n")
        for o in old:
            if lines.count(o) != 1:
                raise RuntimeError("%s: %d %r lines" % (rel, lines.count(o), o))
        swap = dict(zip(old, new))
        with open(p, "w", newline="") as f:
            f.write("\n".join(swap.get(x, x) for x in lines))
        RC.git(ctx.p1, "commit", "-q", "-am", "board case %s: %s" % (name, msg))
        with open(log, "a") as fx:
            RC.normalise(ctx, ctx.p1, name, fx)
            fx.write("%s: %s\n" % (name, RC.git(ctx.p1, "rev-parse", "HEAD")))
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return False
    return True


def need(ctx, name, flag, first):
    if not getattr(ctx, flag, False):
        RC.report(name, "VOID", "needs %s in the same batch" % first, ctx.log)
        return False
    return True


# ---------- cases ----------

def c_release_pass(ctx, log):
    if fixture_built(ctx, "release_pass", log):
        ctx.pass_built = True
        judge_case(ctx, "release_pass", ctx.p1, log, None, lambda w, r: w == [] and r[3])


def c_other_target(ctx, log):
    if need(ctx, "other_target", "pass_built", "release_pass"):
        judge_case(ctx, "other_target", ctx.p1, log, {"target": "esp32s3"},
                   lambda w, r: w == ["target"])


def c_small_slot(ctx, log):
    if not need(ctx, "small_slot", "pass_built", "release_pass"):
        return
    b = os.path.join(ctx.p1, "build")
    desc = json.loads(read(os.path.join(b, "project_description.json"), "r"))
    image = os.path.getsize(os.path.join(b, desc["app_bin"]))
    ref = json.loads(read(os.path.join(ctx.board, "board.json"), "r"))["partition_table"]
    small = shrink_ota0(read(os.path.join(ctx.board, ref)), image - 1)
    judge_case(ctx, "small_slot", ctx.p1, log, {"partition_table": "r21_small_slot.bin"},
               lambda w, r: w == ["table", "slot"] and min(r[3]) == image - 1, {"r21_small_slot.bin": small})


def c_missing_reference(ctx, log):
    if not need(ctx, "missing_reference", "pass_built", "release_pass"):
        return
    plant(ctx, ctx.p1, {"partition_table": "r21-none.bin"})
    rc, out = check(ctx.p1, log)
    RC.report("missing_reference", *judge_unreadable(rc, out, "reference partition table cannot be read"), ctx.log)


def lock_case(ctx, log, name, entries, mark):
    """The faithful board planted, the lock given entries instead of its one git entry."""
    if not need(ctx, name, "pass_built", "release_pass"):
        return
    plant(ctx, ctx.p1, entries=entries)
    with open(log, "a") as f:
        f.write("%s: lock entries %s\n" % (name, json.dumps(entries, sort_keys=True)))
    rc, out = check(ctx.p1, log)
    RC.report(name, *judge_unreadable(rc, out, mark), ctx.log)


def c_no_board(ctx, log):
    lock_case(ctx, log, "no_board", {}, "0 shared/boards/* components")


def c_two_boards(ctx, log):
    lock_case(ctx, log, "two_boards", {BOARD: GIT_SOURCE, "vtrux/x2": dict(GIT_SOURCE, path="shared/boards/x2")},
              "2 shared/boards/* components")


def c_local_board(ctx, log):
    lock_case(ctx, log, "local_board", {BOARD: {"type": "local", "path": "../shared-x/shared/boards/wican_v3"}},
              "comes from a local folder")


def c_table_differs(ctx, log):
    if fixture_built(ctx, "table_differs", log, ("wican_partitions_table.csv", (CSV_OLD,), (CSV_NEW,), "storage 296K")):
        judge_case(ctx, "table_differs", ctx.p1, log, None, lambda w, r: w == ["table"])


def c_flash_larger(ctx, log):
    if fixture_built(ctx, "flash_larger", log, ("sdkconfig", SDK_OLD, SDK_NEW, "flash size 8MB")):
        judge_case(ctx, "flash_larger", ctx.p1, log, None,
                   lambda w, r: w == ["flash"] and r[5] == (FLASH_FIELD["8MB"],) * 2 and r[6] == "8MB")


def s3_mini(ctx, log):
    """The minimal project on branch r21-s3: set to esp32s3, reproducible build on, its own default
    partition table committed as ref/pt-s3.bin, tracked files as the reconfigure writes them."""
    if not os.path.exists(os.path.join(ctx.mini, ".git")):
        RC.make_mini(ctx)
    RC.reset(ctx.mini, "main", "main")
    RC.git(ctx.mini, "checkout", "-q", "-f", "-B", "r21-s3", "main")
    RC.rmtree(os.path.join(ctx.mini, "build"))
    for args in (("set-target", "esp32s3"), ("partition-table",)):
        rc, _ = RC.idf(ctx, ctx.mini, *args, log=log)
        if rc:
            raise RuntimeError("mini esp32s3: idf.py %s failed" % " ".join(args))
    RC.set_repro(ctx.mini)
    os.makedirs(os.path.join(ctx.mini, "ref"), exist_ok=True)
    shutil.copyfile(os.path.join(ctx.mini, "build", "partition_table", "partition-table.bin"),
                    os.path.join(ctx.mini, "ref", "pt-s3.bin"))
    RC.git(ctx.mini, "add", "-A")
    RC.git(ctx.mini, "commit", "-q", "-m", "r21 case: esp32s3, its partition table as the reference")
    with open(log, "a") as fx:
        RC.normalise(ctx, ctx.mini, "r21-s3", fx)
        fx.write("r21-s3: %s\n" % RC.git(ctx.mini, "rev-parse", "HEAD"))


def c_s3_pass(ctx, log):
    s3_mini(ctx, log)
    unplant(ctx.mini)
    RC.rmtree(os.path.join(ctx.mini, "build"))
    rc, _ = RC.release(ctx, ctx.mini, log, jobs=ctx.jobs)
    if rc:
        RC.report("s3_pass", "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return
    ctx.s3_built = True
    fs = _FLASHSIZE.findall(read(os.path.join(ctx.mini, "build", "config", "sdkconfig.h"), "r"))
    ctx.s3_board = {"target": "esp32s3", "partition_table": "pt-s3.bin", "flash_size": fs[0] if len(fs) == 1 else None}
    ctx.s3_ref = {"pt-s3.bin": read(os.path.join(ctx.mini, "ref", "pt-s3.bin"))}
    judge_case(ctx, "s3_pass", ctx.mini, log, ctx.s3_board, lambda w, r: w == [] and r[4] == "esp32s3", ctx.s3_ref)
    unplant(ctx.mini)           # R17 judges the build as built; the planted board was not built
    rc, out = check(ctx.mini, log, "scope", "--release")
    RC.report("s3_pass (R17 scope)", "PASS" if rc == 0 and "PASS R17" in out else "FAIL",
              "scope --release on the esp32s3 build: exit %d" % rc, ctx.log)


def c_s3_board_c3(ctx, log):
    if need(ctx, "s3_board_c3", "s3_built", "s3_pass"):
        judge_case(ctx, "s3_board_c3", ctx.mini, log, dict(ctx.s3_board, target="esp32c3"),
                   lambda w, r: w == ["target"], ctx.s3_ref)


def c_s3_elf_reader(ctx, log):
    if not need(ctx, "s3_elf_reader", "s3_built", "s3_pass"):
        return
    b = os.path.join(ctx.mini, "build")
    desc = json.loads(read(os.path.join(b, "project_description.json"), "r"))
    elf, nm = os.path.join(b, desc["app_elf"]), desc["monitor_toolprefix"] + "nm"
    with open(log, "a") as f:
        f.write("s3_elf_reader: VTRUX_TEST_ELF %s, VTRUX_TEST_NM %s\n" % (elf, nm))
    e = RC.idf_env(PYTHONPATH=TOOLS, VTRUX_TEST_ELF=elf, VTRUX_TEST_NM=nm)
    rc, out = RC.run([sys.executable, "-B", "-m", "unittest", "-v", "vtrux_checks.tests.test_switches.Elf.test_as_nm"],
                     cwd=TOOLS, env=e, log=log)
    RC.report("s3_elf_reader", *judge_elf_reader(rc, out), ctx.log)


CASES = [("release_pass", c_release_pass), ("other_target", c_other_target), ("small_slot", c_small_slot),
         ("missing_reference", c_missing_reference), ("no_board", c_no_board), ("two_boards", c_two_boards),
         ("local_board", c_local_board),
         ("table_differs", c_table_differs), ("flash_larger", c_flash_larger), ("s3_pass", c_s3_pass),
         ("s3_board_c3", c_s3_board_c3), ("s3_elf_reader", c_s3_elf_reader)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--board", help="the board component's folder: shared/boards/wican_v3 at the commit under test")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out and a.board) or "IDF_PATH" not in os.environ:
        ap.error("--root, --out and --board, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.board = os.path.abspath(a.board)
    ctx.log = os.path.join(ctx.out, "board_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\nboard %s board.json sha256 %s\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain") or "clean",
            ctx.board, RC.sha256_file(os.path.join(ctx.board, "board.json")), names))
    if not os.path.exists(ctx.src):
        ap.error("no fixture at %s: make it with release_cases.py first" % ctx.src)
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "board_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    for p in (ctx.p1, ctx.mini):
        if os.path.exists(os.path.join(p, ".git")):
            unplant(p)
    RC.reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.out, "board_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
