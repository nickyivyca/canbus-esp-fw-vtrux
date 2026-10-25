#!/usr/bin/env python3
"""R21 board-data check (vtrux_checks board) on real release builds, shown passing and failing.

    python -B board_cases.py --root <short folder in scratch/> --out <run folder> --data <vtrux_checks.json>
                             [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B board_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture (a
clone of gen-inhibit with fixture commits, under --root), minimal project and plumbing it
reuses. --data is gen-inhibit's vtrux_checks.json, holding the WiCAN's board data; it is
copied into the project root after the build (a case may change its "board" object), so the
release build sees a clean tree.

What each case expects is worked out here, not by the check's reader: the table by comparing
build/partition_table/partition-table.bin with the reference file byte for byte; the slot by
this file's own reading of the reference table (the smallest app entry); the build's target
from build/config/sdkconfig.h. The tool prefix the check prints must be the build's own
monitor_toolprefix (project_description.json) when the targets agree, else the compiler
ESP-IDF's toolchain-<board target>.cmake names, less "gcc". A case whose trigger does not show
there is VOID.

    release_pass       the fixture and the WiCAN's board data: passes
    other_target       the same build, the board's target esp32s3: fails on the target
    small_slot         the same build, the reference a copy of the stock table with ota_0 one
                       byte smaller than the image (MD5 entry redone): fails on the slot, and
                       on the table, which then differs
    missing_reference  the same build, the reference path naming no file: cannot read
    named_board        the same build, "board" naming a board by name: cannot read (the
                       named form is not built yet)
    table_differs      a release build of a commit making storage 296K in the partition CSV:
                       fails on the table
    s3_pass            the minimal project set to esp32s3, its own partition table committed as
                       the reference: passes, the prefix xtensa-esp32s3-elf-; and the R17 scope
                       check passes on it (--release)
    s3_board_c3        the same build, the board's target esp32c3: fails on the target

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
STOCK = "test/build_checks/reference/stock-v4.13-partition-table.bin"
CSV_OLD = "storage,  data, spiffs, ,        300K,"
CSV_NEW = "storage,  data, spiffs, ,        296K,"
_TARGET = re.compile(r'^#define CONFIG_IDF_TARGET "([^"]+)"$', re.M)
_PREFIX_LINE = re.compile(r"^  tool prefix +(\S+)", re.M)
_COMPILER = re.compile(r"^\s*set\(\s*CMAKE_C_COMPILER\s+(\S+)gcc\s*\)", re.M)
KINDS = (("table", "not byte-identical"), ("slot", "-byte slot by "), ("target", "CONFIG_IDF_TARGET is "))


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


def expected(built, reference, image_size, build_target, board_target):
    """The failure kinds the check must name."""
    want = []
    if built != reference:
        want.append("table")
    sizes = app_sizes(reference)
    if sizes and image_size > min(sizes):
        want.append("slot")
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
              ("all three", expected(shrink_ota0(t, 5), t, 101, "c3", "s3"), ["table", "slot", "target"]))
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
    for label, args, want in (("passes as wanted", (0, out_p, [], "a-elf-"), "PASS"),
                              ("fails as wanted", (1, out_t, ["table"], "a-elf-"), "PASS"),
                              ("wrong prefix", (0, out_p, [], "b-elf-"), "FAIL"),
                              ("no prefix line", (0, "PASS R21\n", [], "a-elf-"), "FAIL"),
                              ("passes when it should fail", (0, out_p, ["slot"], "a-elf-"), "FAIL"),
                              ("fails when it should pass", (1, out_t, [], "a-elf-"), "FAIL"),
                              ("names another kind", (1, out_t, ["slot"], "a-elf-"), "FAIL"),
                              ("names one too many", (1, out_t + "    the image is 9 bytes, over the 8-byte slot by 1\n",
                                                      ["table"], "a-elf-"), "FAIL"),
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


def write_data(ctx, proj, board):
    """vtrux_checks.json in the project root: --data's, with "board" replaced when board is given."""
    d = json.loads(read(ctx.data, "r"))
    if board is not None:
        d["board"] = board
    with open(os.path.join(proj, "vtrux_checks.json"), "w", newline="\n") as f:
        json.dump(d, f, indent=2)
    return d["board"]


def facts(proj, board):
    """(want, prefix, note) from the build and the board, by the tester's own readings."""
    b = os.path.join(proj, "build")
    desc = json.loads(read(os.path.join(b, "project_description.json"), "r"))
    built = read(os.path.join(b, "partition_table", "partition-table.bin"))
    ref_p = os.path.join(proj, board["partition_table"])
    ref = read(ref_p) if os.path.exists(ref_p) else b""
    image = os.path.getsize(os.path.join(b, desc["app_bin"]))
    m = _TARGET.findall(read(os.path.join(b, "config", "sdkconfig.h"), "r"))
    target = m[0] if len(m) == 1 else None
    if target == board["target"]:
        prefix = desc.get("monitor_toolprefix")
    else:
        tc = os.path.join(os.environ["IDF_PATH"], "tools", "cmake", "toolchain-%s.cmake" % board["target"])
        c = _COMPILER.findall(read(tc, "r")) if os.path.exists(tc) else []
        prefix = c[0] if len(c) == 1 else None
    sizes = app_sizes(ref)
    note = "table %s; image %d, slot %s; build target %s, board %s; prefix %s" % (
        "same" if built == ref else "differs", image, min(sizes) if sizes else None, target, board["target"], prefix)
    return expected(built, ref, image, target, board["target"]), prefix, note, (built, ref, image, sizes, target)


def judge_case(ctx, name, proj, log, board, trigger):
    """board: the "board" object to write (None: --data's own); trigger(want, raw) must hold, else VOID."""
    board = write_data(ctx, proj, board)
    want, prefix, note, raw = facts(proj, board)
    with open(log, "a") as f:
        f.write("%s: %s; expected %s\n" % (name, note, want))
    if not trigger(want, raw) or not prefix:
        RC.report(name, "VOID", "the trigger does not show: %s; expected %s" % (note, want), ctx.log)
        return
    rc, out = check(proj, log)
    RC.report(name, *judge(rc, out, want, prefix), ctx.log)


def fixture_built(ctx, name, log, csv=False):
    RC.reset(ctx.p1, "fixture")
    if csv:
        RC.git(ctx.p1, "checkout", "-q", "-B", "r21-" + name, "origin/fixture")
        p = os.path.join(ctx.p1, "wican_partitions_table.csv")
        s = read(p, "r")
        lines = s.replace("\r\n", "\n").split("\n")
        if lines.count(CSV_OLD) != 1:
            raise RuntimeError("wican_partitions_table.csv: %d %r lines" % (lines.count(CSV_OLD), CSV_OLD))
        with open(p, "w", newline="") as f:
            f.write("\n".join(CSV_NEW if x == CSV_OLD else x for x in lines))
        RC.git(ctx.p1, "commit", "-q", "-am", "board case %s: storage 296K" % name)
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
        judge_case(ctx, "other_target", ctx.p1, log, {"target": "esp32s3", "partition_table": STOCK},
                   lambda w, r: w == ["target"])


def c_small_slot(ctx, log):
    if not need(ctx, "small_slot", "pass_built", "release_pass"):
        return
    b = os.path.join(ctx.p1, "build")
    desc = json.loads(read(os.path.join(b, "project_description.json"), "r"))
    image = os.path.getsize(os.path.join(b, desc["app_bin"]))
    rel = "build/r21_small_slot.bin"        # build/ is ignored, so the tree stays clean
    with open(os.path.join(ctx.p1, rel), "wb") as f:
        f.write(shrink_ota0(read(os.path.join(ctx.p1, STOCK)), image - 1))
    judge_case(ctx, "small_slot", ctx.p1, log, {"target": "esp32c3", "partition_table": rel},
               lambda w, r: w == ["table", "slot"] and min(r[3]) == image - 1)


def c_missing_reference(ctx, log):
    if not need(ctx, "missing_reference", "pass_built", "release_pass"):
        return
    write_data(ctx, ctx.p1, {"target": "esp32c3", "partition_table": "test/build_checks/reference/r21-none.bin"})
    rc, out = check(ctx.p1, log)
    RC.report("missing_reference", *judge_unreadable(rc, out, "reference partition table cannot be read"), ctx.log)


def c_named_board(ctx, log):
    if not need(ctx, "named_board", "pass_built", "release_pass"):
        return
    write_data(ctx, ctx.p1, "wican")
    rc, out = check(ctx.p1, log)
    RC.report("named_board", *judge_unreadable(rc, out, "not built yet"), ctx.log)


def c_table_differs(ctx, log):
    if fixture_built(ctx, "table_differs", log, csv=True):
        judge_case(ctx, "table_differs", ctx.p1, log, None, lambda w, r: w == ["table"])


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
    RC.rmtree(os.path.join(ctx.mini, "build"))
    rc, _ = RC.release(ctx, ctx.mini, log, jobs=ctx.jobs)
    if rc:
        RC.report("s3_pass", "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return
    ctx.s3_built = True
    judge_case(ctx, "s3_pass", ctx.mini, log, {"target": "esp32s3", "partition_table": "ref/pt-s3.bin"},
               lambda w, r: w == [] and r[4] == "esp32s3")
    rc, out = check(ctx.mini, log, "scope", "--release")
    RC.report("s3_pass (R17 scope)", "PASS" if rc == 0 and "PASS R17" in out else "FAIL",
              "scope --release on the esp32s3 build: exit %d" % rc, ctx.log)


def c_s3_board_c3(ctx, log):
    if need(ctx, "s3_board_c3", "s3_built", "s3_pass"):
        judge_case(ctx, "s3_board_c3", ctx.mini, log, {"target": "esp32c3", "partition_table": "ref/pt-s3.bin"},
                   lambda w, r: w == ["target"])


CASES = [("release_pass", c_release_pass), ("other_target", c_other_target), ("small_slot", c_small_slot),
         ("missing_reference", c_missing_reference), ("named_board", c_named_board),
         ("table_differs", c_table_differs), ("s3_pass", c_s3_pass), ("s3_board_c3", c_s3_board_c3)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--data", help="gen-inhibit's vtrux_checks.json, with the WiCAN's board data")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out and a.data) or "IDF_PATH" not in os.environ:
        ap.error("--root, --out and --data, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.data = os.path.abspath(a.data)
    ctx.log = os.path.join(ctx.out, "board_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\ndata %s sha256 %s\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain") or "clean",
            ctx.data, RC.sha256_file(ctx.data), names))
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
    RC.reset(ctx.p1, "fixture")
    for p in (ctx.p1, ctx.mini):
        q = os.path.join(p, "vtrux_checks.json")
        if os.path.exists(q) and RC.git(p, "status", "--porcelain", "--", "vtrux_checks.json").startswith("??"):
            os.remove(q)
    with open(os.path.join(ctx.out, "board_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
