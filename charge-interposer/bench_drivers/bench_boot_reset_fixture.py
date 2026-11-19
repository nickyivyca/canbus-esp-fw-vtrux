"""The bench can-fail of bench_boot_reset.py, boot-reset-0x15 (spec 9, the X2
settings, core dumps; tracker I step 2, the charge-interposer reviewer's
choice of fixture cf2, 2026-11-18).

The fixture is a bridge built from a named product commit with spec 9's
core startup stage partition table read taken out and the core dump's boot
check put on, so the secondary stage's core dump setup reads flash again:
  firmware/main/CMakeLists.txt  "set(boot boot_partitions.c)" -> "set(boot)",
                                and the two-line target_link_libraries(...
                                "-u intp_boot_partitions_include") removed
  firmware/sdkconfig            "# CONFIG_ESP_COREDUMP_CHECK_BOOT is not set"
                                -> "CONFIG_ESP_COREDUMP_CHECK_BOOT=y"
On the X2 it must FAIL boot-reset-0x15 on a panic report. Measured by hand
before this script (tracker I step 2, 2026-11-18): this fixture over
231261b0 panicked on 50 of 50 chip resets; the same with the boot check
left off (cf1) on 0 of 50, so the boot check is part of the fixture.

    build    --commit C --out DIR [--jobs N]
             In a shell exported for the interposer's ESP-IDF, on Linux in
             build.slice, from a clean worktree (its HEAD is the harness
             commit). A detached worktree interposer-tester-bootfix beside
             this one, at C; the fixture's edits made and committed there by
             this script (never by hand, never on a branch, never pushed),
             and its diff against C checked to be exactly those edits before
             anything builds; vtrux_tools.py build bridge; then the build's
             map must name no intp_read_partition_table and its
             config/sdkconfig.json must have ESP_COREDUMP_ENABLE_TO_FLASH and
             ESP_COREDUMP_CHECK_BOOT true. The image's files (flasher_args.json
             and the files it flashes, the ELF, the map, sdkconfig.json) are
             copied to DIR/image/, and the worktree removed, pass or fail.
    flash    --image DIR --port P --out DIR [--baud B]
             esptool erase_region over the image's coredump partition (from
             its partition table), then write_flash of flasher_args.json's
             files, each "Hash of data verified". For any image with that
             layout (the product's bridge, witness, truck builds too).
    canfail  --image DIR --port P --out DIR [--resets N] [--window-s S]
             flash, then bench_boot_reset.py with the bridge's banner as its
             app line; the can-fail is SHOWN only if that run FAILS with at
             least one reset failing on a panic report. Exit 0 only then.
The bench parts (flash, canfail) run on Linux only in bench.slice; the bench
lock is the caller's to hold (bench_lock.py status), and they leave the X2
on the image they flashed. esptool is VTRUX_ESPTOOL_ARGV (a JSON list: the
settings' esptool.argv, as bench_suite.py passes it) or --esptool-argv.

Each part writes DIR/record.txt (the commits, the fixture's sha and diff,
each step's exit, the files' sha256, each check) and DIR/logs/.

    python3.13 -B bench_boot_reset_fixture.py --self-test
    python3.13 -B bench_boot_reset_fixture.py --can-fail

Tester, 2026-11-18.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))

CMAKE_REL = "charge-interposer/firmware/main/CMakeLists.txt"
SDK_REL = "charge-interposer/firmware/sdkconfig"
BOOT_OLD = "set(boot boot_partitions.c)"
BOOT_NEW = "set(boot)"
LINK_OLD = ('target_link_libraries(${COMPONENT_LIB} INTERFACE\n'
            '    "-u intp_boot_partitions_include")\n')
CB_OLD = "# CONFIG_ESP_COREDUMP_CHECK_BOOT is not set"
CB_NEW = "CONFIG_ESP_COREDUMP_CHECK_BOOT=y"
READ_SYMBOL = "intp_read_partition_table"
SDK_TRUE = ("ESP_COREDUMP_ENABLE_TO_FLASH", "ESP_COREDUMP_CHECK_BOOT")
VARIANT = "bridge"
BANNER = "Vtrux charge interposer"
WORKTREE = "interposer-tester-bootfix"
# ESP-IDF's partition table: 32-byte entries, magic 0xAA50 little-endian
PT_MAGIC = b"\xaa\x50"
PT_DATA, PT_COREDUMP = 0x01, 0x03


# ---- the fixture (pure) ------------------------------------------------------
def fixture_texts(cmake, sdk):
    """(cmake, sdkconfig) with the fixture's edits. ValueError unless each
    edited text is there exactly once."""
    for name, text, old in ((CMAKE_REL, cmake, BOOT_OLD), (CMAKE_REL, cmake, LINK_OLD),
                            (SDK_REL, sdk, CB_OLD)):
        n = text.count(old)
        if n != 1:
            raise ValueError("%r occurs %d times in %s, not once" % (old, n, name))
    return (cmake.replace(BOOT_OLD, BOOT_NEW).replace(LINK_OLD, ""),
            sdk.replace(CB_OLD, CB_NEW))


def fixture_diff_problems(diff):
    """[] when `git diff --unified=0` of the product commit against the
    fixture is exactly the fixture's edits; otherwise what is wrong."""
    out = []
    files = re.findall(r"^diff --git a/(\S+) b/(\S+)$", diff, re.M)
    if sorted(files) != sorted([(CMAKE_REL, CMAKE_REL), (SDK_REL, SDK_REL)]):
        out.append("the diff names %s, not %s and %s alone" % (files, CMAKE_REL, SDK_REL))
    body = sorted(ln for ln in diff.splitlines()
                  if ln[:1] in "+-" and not ln.startswith(("+++ ", "--- ")))
    want = sorted(["-" + BOOT_OLD, "+" + BOOT_NEW, "-" + CB_OLD, "+" + CB_NEW]
                  + ["-" + ln for ln in LINK_OLD.splitlines()])
    if body != want:
        out.append("the changed lines are %r, not the fixture's edits" % body[:8])
    if "new file mode" in diff or "deleted file mode" in diff or \
            re.search(r"^old mode|^new mode", diff, re.M):
        out.append("the diff changes a file's mode or adds or removes one")
    return out


def build_problems(map_text, sdk_json):
    """[] when the build is the fixture: the map names no READ_SYMBOL and
    sdkconfig.json has every SDK_TRUE key true."""
    out = []
    if READ_SYMBOL in map_text:
        out.append("the map names %s: the core stage read is still linked" % READ_SYMBOL)
    for k in SDK_TRUE:
        if sdk_json.get(k) is not True:
            out.append("sdkconfig.json %s is %r, not true" % (k, sdk_json.get(k)))
    return out


# ---- the image (pure) ----------------------------------------------------------
def coredump_region(table):
    """(offset, size) of the coredump partition in a partition table's
    bytes. ValueError if there is none, or more than one."""
    found = []
    for i in range(0, len(table) - 31, 32):
        e = table[i:i + 32]
        if e[:2] != PT_MAGIC:
            break
        typ, sub, off, size = struct.unpack("<BBII", e[2:12])
        if typ == PT_DATA and sub == PT_COREDUMP:
            found.append((off, size))
    if len(found) != 1:
        raise ValueError("%d coredump partitions in the table, not one" % len(found))
    return found[0]


def image_files(flasher_args):
    """The files an image's flasher_args.json flashes, in offset order."""
    ff = flasher_args["flash_files"]
    return [ff[k] for k in sorted(ff, key=lambda a: int(a, 16))]


def esptool_steps(base, port, baud, flasher_args, region):
    """[(name, argv)] for the flash: erase the coredump region, then write
    every flash file at its offset with the image's flash settings."""
    x = flasher_args.get("extra_esptool_args", {})
    head = base + ["--chip", x.get("chip", "esp32s3"), "-p", port, "-b", str(baud),
                   "--before", x.get("before", "default_reset"),
                   "--after", x.get("after", "hard_reset")]
    fs = flasher_args["flash_settings"]
    ff = flasher_args["flash_files"]
    write = head + ["write_flash", "--flash_mode", fs["flash_mode"], "--flash_freq",
                    fs["flash_freq"], "--flash_size", fs["flash_size"]]
    for k in sorted(ff, key=lambda a: int(a, 16)):
        write += [k, ff[k]]
    return [("erase-coredump", head + ["erase_region", "0x%x" % region[0], "0x%x" % region[1]]),
            ("write-flash", write)]


def judge_canfail(rc, text):
    """(shown, why) from bench_boot_reset.py's exit code and report: shown
    only if the run FAILED and at least one reset failed on a panic report."""
    panics = len(re.findall(r"^FAIL reset \d+: panic report", text, re.M))
    final = [ln for ln in text.splitlines() if ln.endswith(" boot-reset-0x15") or
             " boot-reset-0x15: " in ln]
    verdict = final[-1].split(" ", 1)[0] if final else None
    if rc == 0:
        return False, "bench_boot_reset exited 0 (verdict %s)" % verdict
    if verdict != "FAIL":
        return False, "boot-reset-0x15 is %s, not FAIL" % verdict
    if not panics:
        return False, "boot-reset-0x15 FAILED, but on no panic report"
    return True, "boot-reset-0x15 FAILED, %d reset(s) on a panic report" % panics


def slice_problem(want, cgroup_text, system):
    """None if this process may run here: off Linux always; on Linux only
    in the named slice."""
    if not system.startswith("linux"):
        return None
    if "/%s/" % want in cgroup_text:
        return None
    return ("not in %s; run it as: systemd-run --user --scope --slice=%s -q -- <command>"
            % (want, want))


# ---- running ----------------------------------------------------------------------
class Run:
    def __init__(self, out):
        if os.path.exists(out):
            raise SystemExit("bench_boot_reset_fixture: %s exists" % out)
        self.out = out
        self.logs = os.path.join(out, "logs")
        os.makedirs(self.logs)
        self.lines = []

    def note(self, text):
        print(text, flush=True)
        self.lines.append(text)

    def sh(self, name, argv, cwd=None, env=None, timeout=4 * 3600):
        t0 = time.time()
        try:
            r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                               errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
            rc, text = r.returncode, r.stdout + r.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, text = None, "%s: %s" % (type(e).__name__, e)
        with open(os.path.join(self.logs, name + ".log"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write("$ %s\n(cwd %s)\n\n%s\n[exit %s, %.0f s]\n"
                    % (" ".join(argv), cwd, text, rc, time.time() - t0))
        return rc, text

    def write(self):
        with open(os.path.join(self.out, "record.txt"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write("\n".join(self.lines) + "\n")


def git(*args, cwd=None):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip() + (("\n" + r.stderr.strip()) if r.returncode else "")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def need_slice(want):
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as f:
            cg = f.read()
    except OSError:
        cg = ""
    why = slice_problem(want, cg, sys.platform)
    if why:
        raise SystemExit("bench_boot_reset_fixture: REFUSED: %s" % why)


def build(a):
    need_slice("build.slice")
    code = os.path.dirname(HERE)
    sys.path.insert(0, code)
    import vtrux_tools
    vtrux_tools.check_idf_env()
    rc, top = git("rev-parse", "--show-toplevel", cwd=code)
    rc2, harness = git("rev-parse", "HEAD", cwd=top)
    rc3, dirty = git("status", "--porcelain", cwd=top)
    if rc or rc2 or rc3 or dirty:
        raise SystemExit("bench_boot_reset_fixture: this worktree is not a clean git "
                         "worktree; the harness must be committed:\n%s" % dirty)
    rc, commit = git("rev-parse", "--verify", a.commit + "^{commit}", cwd=top)
    if rc:
        raise SystemExit("bench_boot_reset_fixture: no commit %r" % a.commit)
    wt = os.path.join(os.path.dirname(top), WORKTREE)
    if os.path.exists(wt):
        raise SystemExit("bench_boot_reset_fixture: %s is already there" % wt)
    run = Run(a.out)
    run.note("bench_boot_reset_fixture build %s" % time.strftime("%Y-%m-%d %H:%M:%S %z"))
    run.note("product commit %s" % commit)
    run.note("harness commit %s (this worktree, clean)" % harness)
    bad = []
    try:
        rc, out = git("worktree", "add", "--detach", wt, commit, cwd=top)
        if rc:
            raise RuntimeError("git worktree add %s: %s" % (wt, out))
        paths = [os.path.join(wt, *r.split("/")) for r in (CMAKE_REL, SDK_REL)]
        texts = []
        for p in paths:
            with open(p, encoding="utf-8", newline="") as f:
                texts.append(f.read())
        for p, t in zip(paths, fixture_texts(*texts)):
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(t)
        rc, out = git("commit", "-q", "-a", "-m",
                      "fixture (bench_boot_reset_fixture, never on a branch): the core "
                      "startup stage partition table read removed, CHECK_BOOT on", cwd=wt)
        if rc:
            raise RuntimeError("the fixture commit: %s" % out)
        _rc, fixture = git("rev-parse", "HEAD", cwd=wt)
        _rc, diff = git("diff", "--unified=0", "--no-color", "--no-ext-diff", commit,
                        fixture, cwd=wt)
        run.note("fixture %s (derived: %s + the fixture's edits)" % (fixture, commit[:12]))
        run.note("fixture diff:\n" + "\n".join("    " + ln for ln in diff.splitlines()))
        probs = fixture_diff_problems(diff)
        if probs:
            raise RuntimeError("the fixture's diff is not its edits: %s" % probs)
        rc, _t = run.sh("build-" + VARIANT, [sys.executable, "-B", os.path.join(
            wt, "charge-interposer", "vtrux_tools.py"), "build", VARIANT]
            + (["--jobs", str(a.jobs)] if a.jobs else []), cwd=wt)
        run.note("build %s exit %s%s" % (VARIANT, rc, "" if rc == 0 else "  FAIL"))
        if rc != 0:
            raise RuntimeError("the build failed (logs/build-%s.log)" % VARIANT)
        bd = os.path.join(wt, "charge-interposer", "firmware", "build", VARIANT)
        elf = "interposer_%s.elf" % VARIANT
        mp = "interposer_%s.map" % VARIANT
        with open(os.path.join(bd, mp), encoding="utf-8", errors="replace") as f:
            map_text = f.read()
        with open(os.path.join(bd, "config", "sdkconfig.json"), encoding="utf-8") as f:
            sdk = json.load(f)
        probs = build_problems(map_text, sdk)
        run.note("fixture build checks: %s" % ("; ".join(probs) if probs else
                                               "map names no %s; %s true" % (
                                                   READ_SYMBOL, ", ".join(SDK_TRUE))))
        if probs:
            bad.append("build checks")
        with open(os.path.join(bd, "flasher_args.json"), encoding="utf-8") as f:
            fa = json.load(f)
        img = os.path.join(a.out, "image")
        for rel in ["flasher_args.json", elf, mp, "config/sdkconfig.json"] + image_files(fa):
            dst = os.path.join(img, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(bd, *rel.split("/")), dst)
            run.note("image %-40s sha256 %s" % (rel, sha256(dst)))
    except Exception as e:                           # noqa: BLE001
        run.note("STOPPED: %s: %s" % (type(e).__name__, e))
        bad.append("stopped")
    finally:
        if os.path.exists(wt):
            rc, out = git("worktree", "remove", "--force", wt, cwd=top)
            git("worktree", "prune", cwd=top)
            if rc or os.path.exists(wt):
                run.note("LEFTOVER worktree %s: %s" % (wt, out))
                bad.append("leftover worktree")
        run.note("SUMMARY: %s" % ("PASS" if not bad else "FAIL: " + ", ".join(bad)))
        run.write()
    return 0 if not bad else 1


def esptool_base(a):
    raw = a.esptool_argv or os.environ.get("VTRUX_ESPTOOL_ARGV")
    if not raw:
        raise SystemExit("bench_boot_reset_fixture: REFUSED: no esptool: set "
                         "VTRUX_ESPTOOL_ARGV or --esptool-argv (a JSON list)")
    base = json.loads(raw)
    if not (isinstance(base, list) and base and all(isinstance(s, str) for s in base)):
        raise SystemExit("bench_boot_reset_fixture: REFUSED: the esptool argv is not a "
                         "JSON list of strings: %r" % raw)
    return base


def flash(run, a):
    """True if the image's coredump region was erased and every file
    written and verified."""
    img = os.path.abspath(a.image)
    with open(os.path.join(img, "flasher_args.json"), encoding="utf-8") as f:
        fa = json.load(f)
    pt = [v for k, v in fa["flash_files"].items() if int(k, 16) == 0x8000]
    if len(pt) != 1:
        raise SystemExit("bench_boot_reset_fixture: no partition table at 0x8000 in %s" % img)
    with open(os.path.join(img, *pt[0].split("/")), "rb") as f:
        region = coredump_region(f.read())
    run.note("image %s" % img)
    for rel in image_files(fa):
        run.note("  %-40s sha256 %s" % (rel, sha256(os.path.join(img, *rel.split("/")))))
    for e in [n for n in os.listdir(img) if n.endswith(".elf")]:
        run.note("  %-40s sha256 %s" % (e, sha256(os.path.join(img, e))))
    run.note("coredump partition 0x%x, 0x%x bytes" % region)
    ok = True
    for name, argv in esptool_steps(esptool_base(a), a.port, a.baud, fa, region):
        rc, text = run.sh(name, argv, cwd=img, timeout=600)
        verified = text.count("Hash of data verified")
        want = len(fa["flash_files"]) if name == "write-flash" else 0
        good = rc == 0 and (name != "write-flash" or verified == want)
        run.note("%s exit %s%s%s" % (name, rc, ", %d of %d verified" % (verified, want)
                                     if name == "write-flash" else "",
                                     "" if good else "  FAIL (logs/%s.log)" % name))
        ok = ok and good
        if not good:
            break
    return ok


def bench(a, canfail):
    need_slice("bench.slice")
    run = Run(a.out)
    run.note("bench_boot_reset_fixture %s %s" % ("canfail" if canfail else "flash",
                                                 time.strftime("%Y-%m-%d %H:%M:%S %z")))
    _rc, harness = git("rev-parse", "HEAD", cwd=HERE)
    _rc, dirty = git("status", "--porcelain", "--", ".", cwd=HERE)
    run.note("harness commit %s%s" % (harness, " (bench_drivers/ DIRTY)" if dirty else ""))
    run.note("port %s" % a.port)
    ok = False
    try:
        if not flash(run, a):
            run.note("SUMMARY: FAIL: the flash")
            return 1
        if not canfail:
            ok = True
            run.note("SUMMARY: PASS: flashed")
            return 0
        time.sleep(3)
        rc, text = run.sh("boot-reset", [
            sys.executable, "-B", os.path.join(HERE, "bench_boot_reset.py"), "--port", a.port,
            "--resets", str(a.resets), "--window-s", str(a.window_s), "--app-line", BANNER,
            "--out", os.path.join(a.out, "boot_reset.txt")], timeout=a.resets * 30 + 120)
        ok, why = judge_canfail(rc, text)
        tail = [ln for ln in text.splitlines() if ln.startswith("resets judged")]
        run.note("bench_boot_reset exit %s: %s" % (rc, tail[-1] if tail else "(no totals)"))
        run.note("SUMMARY: CAN-FAIL %s: %s" % ("SHOWN" if ok else "NOT SHOWN", why))
        return 0 if ok else 1
    finally:
        run.write()


# ---- self-test ------------------------------------------------------------------------
CMAKE = ("# boot_partitions.c (spec 9)\nset(ports a.cpp)\n" + BOOT_OLD + "\n"
         "if(x)\n    set(srcs main.cpp ${ports} ${boot})\nendif()\n"
         "idf_component_register(SRCS ${srcs}\n    INCLUDE_DIRS \".\")\n" + LINK_OLD)
SDK = "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y\n" + CB_OLD + "\nCONFIG_X=y\n"
DIFF = ("diff --git a/%s b/%s\nindex 1..2 100644\n--- a/%s\n+++ b/%s\n@@ -20 +20 @@\n"
        "-%s\n+%s\n@@ -33,2 +32,0 @@\n-%s\n-%s\n"
        "diff --git a/%s b/%s\nindex 3..4 100644\n--- a/%s\n+++ b/%s\n@@ -1692 +1692 @@\n"
        "-%s\n+%s\n") % ((CMAKE_REL,) * 4 + (BOOT_OLD, BOOT_NEW) + tuple(LINK_OLD.splitlines())
                         + (SDK_REL,) * 4 + (CB_OLD, CB_NEW))


def _entry(typ, sub, off, size, label):
    return PT_MAGIC + struct.pack("<BBII", typ, sub, off, size) + \
        label.encode().ljust(16, b"\0") + b"\0" * 4


TABLE = (_entry(1, 2, 0x9000, 0x5000, "nvs") + _entry(1, 0, 0xE000, 0x2000, "otadata")
         + _entry(0, 0x10, 0x10000, 0x330000, "app0")
         + _entry(1, 3, 0x7F0000, 0x10000, "coredump")
         + b"\xeb\xeb" + b"\xff" * 14 + b"\0" * 16 + b"\xff" * 32)
FA = {"flash_settings": {"flash_mode": "dio", "flash_size": "8MB", "flash_freq": "80m"},
      "flash_files": {"0x0": "bootloader/bootloader.bin", "0x10000": "interposer_bridge.bin",
                      "0x8000": "partition_table/partition-table.bin",
                      "0xe000": "ota_data_initial.bin"},
      "extra_esptool_args": {"after": "hard_reset", "before": "default_reset",
                             "stub": True, "chip": "esp32s3"}}
REPORT_FAIL = ("# bench_boot_reset x\nFAIL reset 1: panic report: Guru Meditation Error: "
               "Core  1 panic'ed (Cache disabled but cached memory region accessed).\n"
               "FAIL reset 2: panic report: Guru Meditation Error: Core  1 panic'ed (x)\n"
               "resets judged 2 of 2 sent: 0 PASS, 2 FAIL, 0 VOID\n"
               "FAIL boot-reset-0x15: a boot panicked or restarted\n")
REPORT_PASS = ("# bench_boot_reset x\nPASS reset 1: rst:0x15 read\nPASS reset 2: rst:0x15 read\n"
               "resets judged 2 of 2 sent: 2 PASS, 0 FAIL, 0 VOID\n"
               "PASS boot-reset-0x15: every reset seen, none panicked\n")
REPORT_VOID = ("# bench_boot_reset x\nVOID reset 1: no rst:0x15 line read\n"
               "resets judged 1 of 1 sent: 0 PASS, 0 FAIL, 1 VOID\n"
               "VOID boot-reset-0x15: not every reset was seen\n")
REPORT_APP = ("# bench_boot_reset x\nFAIL reset 1: 'Vtrux charge interposer' not read after "
              "rst:0x15\nresets judged 1 of 1 sent: 0 PASS, 1 FAIL, 0 VOID\n"
              "FAIL boot-reset-0x15: a boot panicked or restarted\n")


def self_test():
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    def raises(f, *args):
        try:
            f(*args)
        except ValueError:
            return True
        return False

    c, s = fixture_texts(CMAKE, SDK)
    check(BOOT_NEW + "\n" in c and BOOT_OLD not in c, "the fixture empties set(boot)")
    check("-u intp_boot_partitions_include" not in c and "target_link_libraries" not in c,
          "the fixture removes the -u target_link_libraries lines")
    check(c.replace(BOOT_NEW, BOOT_OLD) + LINK_OLD == CMAKE,
          "nothing else in CMakeLists.txt changes")
    check(CB_NEW in s and CB_OLD not in s and s.replace(CB_NEW, CB_OLD) == SDK,
          "the fixture puts CHECK_BOOT on, nothing else in sdkconfig")
    check(raises(fixture_texts, CMAKE.replace(BOOT_OLD, "set(boot x.c)"), SDK),
          "no set(boot boot_partitions.c) line is refused")
    check(raises(fixture_texts, CMAKE + BOOT_OLD + "\n", SDK), "a doubled set(boot) is refused")
    check(raises(fixture_texts, CMAKE.replace(LINK_OLD, ""), SDK), "no -u lines is refused")
    check(raises(fixture_texts, CMAKE, SDK.replace(CB_OLD, CB_NEW)),
          "CHECK_BOOT already on is refused")
    check(raises(fixture_texts, CMAKE, SDK + CB_OLD + "\n"), "a doubled CHECK_BOOT is refused")
    check(fixture_diff_problems(DIFF) == [], "the fixture's own diff passes")
    check(fixture_diff_problems(DIFF.replace("+" + CB_NEW, "+CONFIG_X=n")) != [],
          "another sdkconfig value is refused")
    check(fixture_diff_problems(DIFF.replace("+" + BOOT_NEW, "+set(boot x.c)")) != [],
          "another set(boot) value is refused")
    check(fixture_diff_problems(DIFF + "+extra line\n") != [], "an added line is refused")
    one = DIFF.split("diff --git a/" + SDK_REL)[0]
    check(fixture_diff_problems(one) != [], "the CMake edit alone (CHECK_BOOT left off) is refused")
    check(fixture_diff_problems(DIFF.replace(SDK_REL, "charge-interposer/firmware/x")) != [],
          "another file is refused")
    check(fixture_diff_problems(DIFF.replace("index 3..4 100644",
                                             "old mode 100644\nnew mode 100755")) != [],
          "a mode change is refused")
    check(fixture_diff_problems("") != [], "an empty diff is refused")
    sdk_ok = {"ESP_COREDUMP_ENABLE_TO_FLASH": True, "ESP_COREDUMP_CHECK_BOOT": True}
    check(build_problems("x .text y\n", sdk_ok) == [], "a fixture build passes its checks")
    check(build_problems(" .text.%s 0x42 \n" % READ_SYMBOL, sdk_ok) != [],
          "a map naming the core stage read fails")
    check(build_problems("", dict(sdk_ok, ESP_COREDUMP_CHECK_BOOT=False)) != [],
          "CHECK_BOOT false fails")
    check(build_problems("", {"ESP_COREDUMP_CHECK_BOOT": True}) != [],
          "no ENABLE_TO_FLASH fails")
    check(coredump_region(TABLE) == (0x7F0000, 0x10000), "the coredump partition is found")
    check(raises(coredump_region, TABLE[:96] + TABLE[128:]), "no coredump partition is refused")
    check(raises(coredump_region, TABLE[:128] + TABLE[96:]), "two coredump partitions are refused")
    check(raises(coredump_region, b"\xff" * 64), "an empty table is refused")
    st = esptool_steps(["esptool"], "/dev/x", 460800, FA, (0x7F0000, 0x10000))
    check([n for n, _ in st] == ["erase-coredump", "write-flash"], "erase, then write")
    check(st[0][1][-3:] == ["erase_region", "0x7f0000", "0x10000"] and "/dev/x" in st[0][1],
          "the erase covers the coredump partition, on the port")
    w = st[1][1]
    check(w[w.index("write_flash"):] == ["write_flash", "--flash_mode", "dio", "--flash_freq",
                                         "80m", "--flash_size", "8MB",
                                         "0x0", "bootloader/bootloader.bin",
                                         "0x8000", "partition_table/partition-table.bin",
                                         "0xe000", "ota_data_initial.bin",
                                         "0x10000", "interposer_bridge.bin"],
          "every flash file written at its offset with the image's settings")
    check(w[:3] == ["esptool", "--chip", "esp32s3"] and "/dev/x" in w, "the chip and port")
    check(image_files(FA) == ["bootloader/bootloader.bin", "partition_table/partition-table.bin",
                              "ota_data_initial.bin", "interposer_bridge.bin"],
          "the image's files in offset order")
    check(judge_canfail(1, REPORT_FAIL)[0], "a FAIL on panic reports shows the can-fail")
    check(not judge_canfail(0, REPORT_PASS)[0], "a PASS does not")
    check(not judge_canfail(1, REPORT_VOID)[0], "a VOID does not")
    check(not judge_canfail(1, REPORT_APP)[0], "a FAIL on the app line alone does not")
    check(not judge_canfail(0, REPORT_FAIL)[0], "exit 0 does not, whatever the text")
    check(not judge_canfail(1, "")[0], "no report does not")
    check(not judge_canfail(1, REPORT_FAIL.replace("FAIL boot-reset-0x15", "VOID boot-reset-0x15"))[0],
          "panic lines under a VOID verdict do not")
    check(not judge_canfail(1, REPORT_FAIL.rsplit("FAIL boot-reset-0x15", 1)[0])[0],
          "panic lines with no verdict line do not")
    check(slice_problem("bench.slice", "0::/user.slice/x/bench.slice/run-1.scope\n", "linux")
          is None, "in bench.slice on Linux: allowed")
    check(slice_problem("bench.slice", "0::/user.slice/x/app.slice/run-1.scope\n", "linux")
          is not None, "in app.slice on Linux: refused")
    check(slice_problem("build.slice", "0::/user.slice/x/bench.slice/run-1.scope\n", "linux")
          is not None, "a build in bench.slice: refused")
    check(slice_problem("bench.slice", "", "win32") is None, "off Linux: allowed")
    print("\nbench_boot_reset_fixture self-test: %s" % ("all checks OK" if not fails
                                                      else "%d FAILED" % len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("set(boot) left", "    return (cmake.replace(BOOT_OLD, BOOT_NEW).replace(LINK_OLD, \"\"),",
     "    return (cmake.replace(LINK_OLD, \"\"),"),
    ("the -u lines left", ".replace(LINK_OLD, \"\"),\n", ",\n"),
    ("CHECK_BOOT left off", "            sdk.replace(CB_OLD, CB_NEW))", "            sdk)"),
    ("a missing or doubled edit text taken", "        if n != 1:\n            raise ValueError",
     "        if n > 1:\n            raise ValueError"),
    ("the diff's files not checked",
     "    if sorted(files) != sorted([(CMAKE_REL, CMAKE_REL), (SDK_REL, SDK_REL)]):",
     "    if not files:"),
    ("the diff's lines not checked", "    if body != want:", "    if not body:"),
    ("mode changes taken", "re.search(r\"^old mode|^new mode\", diff, re.M)", "False"),
    ("the map not read", "    if READ_SYMBOL in map_text:", "    if False:"),
    ("sdkconfig.json not read", "        if sdk_json.get(k) is not True:",
     "        if sdk_json.get(k) is False:"),
    ("the first data partition taken", "        if typ == PT_DATA and sub == PT_COREDUMP:",
     "        if typ == PT_DATA:"),
    ("several coredump partitions taken", "    if len(found) != 1:", "    if not found:"),
    ("the erase off the coredump region",
     "\"erase_region\", \"0x%x\" % region[0], \"0x%x\" % region[1]",
     "\"erase_region\", \"0x%x\" % region[1], \"0x%x\" % region[1]"),
    ("the files out of offset order in the write",
     "    for k in sorted(ff, key=lambda a: int(a, 16)):\n        write",
     "    for k in sorted(ff):\n        write"),
    ("the image's flash settings dropped", "    write = head + [\"write_flash\", \"--flash_mode\", "
     "fs[\"flash_mode\"], \"--flash_freq\",\n                    fs[\"flash_freq\"], "
     "\"--flash_size\", fs[\"flash_size\"]]", "    write = head + [\"write_flash\"]"),
    ("exit 0 judged", "    if rc == 0:\n        return False", "    if False:\n        return False"),
    ("a VOID shows the can-fail", "    if verdict != \"FAIL\":", "    if verdict == \"PASS\":"),
    ("a FAIL on no panic shows it", "    if not panics:\n        return False",
     "    if False:\n        return False"),
    ("the slice not checked", "    if \"/%s/\" % want in cgroup_text:", "    if True:"),
    ("the slice checked off Linux too", "    if not system.startswith(\"linux\"):\n        return None",
     "    if False:\n        return None"),
]


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--can-fail"]:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--commit", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--jobs", type=int)
    for name in ("flash", "canfail"):
        p = sub.add_parser(name)
        p.add_argument("--image", required=True)
        p.add_argument("--port", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--baud", type=int, default=460800)
        p.add_argument("--esptool-argv")
        if name == "canfail":
            p.add_argument("--resets", type=int, default=50)
            p.add_argument("--window-s", type=float, default=5.0)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return build(a)
    if a.cmd == "canfail" and a.resets < 1:
        ap.error("--resets must be at least 1")
    return bench(a, a.cmd == "canfail")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
