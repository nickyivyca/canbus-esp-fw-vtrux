"""Spec 9's core dump, read off over USB: check coredump-readback (spec 9,
the X2 settings, core dumps; the charge-interposer reviewer's follow-on to
tracker I step 2, 2026-11-18).

Spec 9 puts core dumps in flash, in the coredump partition, "so a crash on
the truck or the bench leaves a dump to read off later, on demand over
USB", with CONFIG_ESP_COREDUMP_CHECK_BOOT and
CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE not set, "so a new crash replaces the
last dump". The check has three parts, each PASS, FAIL or VOID:
  read     after a fixture's crash and its restart, ESP-IDF's esp-coredump
           reads the dump off the X2 over USB against the fixture's ELF
           (exit 0, which needs the dump's app SHA256 to match the ELF's),
           the crashed task is the fixture's, and the current thread's
           backtrace names the fixture's crashing function and not the
           other fixture's (judge_read);
  keep     the product bridge written over the fixture without touching the
           coredump partition, then given --resets chip resets 0x15
           (bench_boot_reset.py with the banner, which must PASS): the
           partition's bytes, read raw with esptool before and after the
           resets, are the same, the bytes before decode against the
           fixture's ELF, and the dump read off over USB after the resets
           passes as in "read" (judge_keep);
  replace  a second, different fixture crashes over that dump: read off
           over USB against its own ELF it passes as in "read" (its
           function named, the first's not), and the partition's bytes,
           read raw once the product is back on, differ from the first
           dump's (judge_replace).
The check PASSES only if all three do: any FAIL fails it, otherwise any
VOID voids it.

THE FIXTURES (build). The bridge of a named product commit with one
deliberate crash added to firmware/main/main.cpp, a block before setup()
and a call as setup()'s first statement, committed in a throwaway worktree
by this script (never by hand, never on a branch, never pushed):
  crash-a       on any boot but a panic restart, setup() starts a task that
                waits 5 s and calls intp_fixture_crash_abort(), which calls
                abort();
  crash-b       the same, calling intp_fixture_crash_trap(), which runs
                __builtin_trap() (an illegal instruction, not an abort);
  crash-b-keep  crash-b with CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE=y, the
                can-fail's fixture.
A panic restart (esp_reset_reason() ESP_RST_PANIC) starts no crash task, so
a fixture crashes once per other reset and then runs as the bridge. Every
esptool connection resets the board, so after one on a fixture the script
reads the port for --settle-s before the next, and the dump is not being
rewritten while it is read. The product bridge of the same commit is built
too, with no edits ("product").

    build    --commit C --out DIR [--jobs N]
             In a shell exported for the interposer's ESP-IDF, on Linux in
             build.slice, from a clean worktree (its HEAD is the harness
             commit). A detached worktree interposer-tester-crashfix beside
             this one, at C. For each image in turn (product, crash-a,
             crash-b, crash-b-keep): checked out at C, the fixture's edits
             made and committed and its diff against C checked to be
             exactly those edits, the build folder removed, vtrux_tools.py
             build bridge; the map must name the image's crash function
             and not the other (product: neither), and config/sdkconfig.json
             must have ESP_COREDUMP_ENABLE_TO_FLASH true, _CHECK_BOOT false
             and _FLASH_NO_OVERWRITE false (crash-b-keep: true). Its files
             (flasher_args.json and the files it flashes, the ELF, the map,
             sdkconfig.json) go to DIR/image-<name>/. The worktree is
             removed, pass or fail.
    run      --images DIR --port P --out DIR [--resets N] [--settle-s S]
             The check (steps in run_check()); the X2 is left on the product
             bridge with its coredump partition erased.
    canfail  --images DIR --port P --out DIR [--resets N] [--settle-s S]
             The bench can-fail (steps in run_canfail()): crash-a's dump is
             read (must PASS), then crash-b-keep crashes over it and is
             read against its own ELF ("replace" must FAIL: the dump is
             still crash-a's, so the read is refused on the app SHA256 and
             the bytes are unchanged), then the partition is erased with
             esptool between the raw read before the product's resets and
             the one after (standing in for a boot path that clears it:
             "keep" must FAIL, and the read off over USB with no dump,
             "read", must FAIL). SHOWN only if all three FAIL with the
             preconditions met. Exit 0 only then. The X2 is left as by run.
The bench parts run on Linux only in bench.slice, in a shell exported for
ESP-IDF (esp-coredump runs from IDF_PYTHON_ENV_PATH, with ESP-IDF's
xtensa-esp32s3-elf-gdb and ROM ELFs); the bench lock is the caller's to
hold (bench_lock.py status). esptool is VTRUX_ESPTOOL_ARGV or
--esptool-argv, as for bench_boot_reset_fixture.py, whose image and esptool
helpers this uses.

Each part writes DIR/record.txt (the commits, each step's exit, each
image's and read's sha256, each judgement) and DIR/logs/, and the bench
parts the raw partition reads (*.bin), the saved cores (*.core) and the
serial logs (*.serial.log).

    python3.13 -B bench_coredump_readback.py --self-test
    python3.13 -B bench_coredump_readback.py --can-fail

Tester, 2026-11-18.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (bench_boot_reset_fixture, bench_usb_host) first, then
# charge-interposer/ (vtrux_tools, serial_open)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import bench_boot_reset_fixture as brf              # noqa: E402

MAIN_REL = "charge-interposer/firmware/main/main.cpp"
SDK_REL = "charge-interposer/firmware/sdkconfig"
SETUP = "void setup() {\n"
CALL = "  intp_fixture_crash_start();\n"
NOOV_OLD = "# CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE is not set"
NOOV_NEW = "CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE=y"
FUNCS = {"abort": "intp_fixture_crash_abort", "trap": "intp_fixture_crash_trap"}
BODY = {"abort": "  abort();", "trap": "  __builtin_trap();"}
TASK = "fixture_crash"
CRASH_DELAY_MS = 5000
# name -> (crash kind or None, FLASH_NO_OVERWRITE on), in build order
IMAGES = [("product", None, False), ("crash-a", "abort", False),
          ("crash-b", "trap", False), ("crash-b-keep", "trap", True)]
VARIANT = "bridge"
BANNER = brf.BANNER
WORKTREE = "interposer-tester-crashfix"
CHIP = "esp32s3"
GDB = "xtensa-esp32s3-elf-gdb"
STACK_HEAD = "CURRENT THREAD STACK"
THREADS_HEAD = "THREADS INFO"
DUMP_END = "ESP32 CORE DUMP END"
FRAME = re.compile(r"^#(\d+)\s+(?:0x[0-9a-fA-F]+\s+in\s+)?([A-Za-z_][\w:]*)\s*\(", re.M)
CRASHED = re.compile(r"Crashed task handle: 0x[0-9a-fA-F]+, name: '([^']*)'")
PANIC = ("Guru Meditation Error", "panic'ed", "Backtrace:", "abort() was called")
ANY_RESET = re.compile(r"rst:0x[0-9a-fA-F]+")
NO_LINK = ("Failed to connect", "could not open port", "No serial data received",
           "Could not open", "Errno 16", "Device or resource busy")


# ---- the fixtures (pure) -------------------------------------------------------
def crash_lines(kind):
    """The block the fixture puts before setup(), as lines."""
    return ["// FIXTURE (bench_coredump_readback.py, never on a branch): one crash",
            "#include <stdlib.h>",
            "#include \"esp_system.h\"",
            "#include \"freertos/FreeRTOS.h\"",
            "#include \"freertos/task.h\"",
            "extern \"C\" __attribute__((noinline)) void %s(void) {" % FUNCS[kind],
            BODY[kind],
            "}",
            "static void intp_fixture_crash_task(void *) {",
            "  vTaskDelay(pdMS_TO_TICKS(%d));" % CRASH_DELAY_MS,
            "  %s();" % FUNCS[kind],
            "  vTaskDelete(NULL);",
            "}",
            "static void intp_fixture_crash_start(void) {",
            "  if (esp_reset_reason() != ESP_RST_PANIC) {",
            "    xTaskCreatePinnedToCore(intp_fixture_crash_task, \"%s\", 4096, NULL, 1, "
            "NULL, 1);" % TASK,
            "  }",
            "}"]


def fixture_texts(main, sdk, kind, keep):
    """(main.cpp, sdkconfig) with the fixture's edits. ValueError unless each
    edited text is there exactly once."""
    edits = [(MAIN_REL, main, SETUP)] + ([(SDK_REL, sdk, NOOV_OLD)] if keep else [])
    for name, text, old in edits:
        n = text.count(old)
        if n != 1:
            raise ValueError("%r occurs %d times in %s, not once" % (old, n, name))
    main = main.replace(SETUP, "\n".join(crash_lines(kind)) + "\n" + SETUP + CALL)
    return main, (sdk.replace(NOOV_OLD, NOOV_NEW) if keep else sdk)


def fixture_diff_problems(diff, kind, keep):
    """[] when `git diff --unified=0` of the product commit against the
    fixture is exactly the fixture's edits; otherwise what is wrong."""
    out = []
    files = sorted(re.findall(r"^diff --git a/(\S+) b/(\S+)$", diff, re.M))
    want_files = sorted([(MAIN_REL, MAIN_REL)] + ([(SDK_REL, SDK_REL)] if keep else []))
    if files != want_files:
        out.append("the diff names %s, not %s" % (files, want_files))
    added = sorted(ln[1:] for ln in diff.splitlines()
                   if ln.startswith("+") and not ln.startswith("+++ "))
    removed = sorted(ln[1:] for ln in diff.splitlines()
                     if ln.startswith("-") and not ln.startswith("--- "))
    want_added = sorted(crash_lines(kind) + [CALL.rstrip("\n")] + ([NOOV_NEW] if keep else []))
    if added != want_added:
        out.append("the added lines are not the fixture's (%d of %d)"
                   % (len(added), len(want_added)))
    if removed != ([NOOV_OLD] if keep else []):
        out.append("the removed lines are %r" % removed[:4])
    if "new file mode" in diff or "deleted file mode" in diff or \
            re.search(r"^old mode|^new mode", diff, re.M):
        out.append("the diff changes a file's mode or adds or removes one")
    return out


def build_problems(map_text, sdk_json, kind, keep):
    """[] when the build is the image: its crash function in the map and the
    other not (product: neither), and the core dump settings as given."""
    out = []
    for k, fn in sorted(FUNCS.items()):
        if (k == kind) != (fn in map_text):
            out.append("the map %s %s" % ("does not name" if k == kind else "names", fn))
    want = {"ESP_COREDUMP_ENABLE_TO_FLASH": True, "ESP_COREDUMP_CHECK_BOOT": False,
            "ESP_COREDUMP_FLASH_NO_OVERWRITE": keep}
    for k in sorted(want):
        if sdk_json.get(k) is not want[k]:
            out.append("sdkconfig.json %s is %r, not %r" % (k, sdk_json.get(k), want[k]))
    return out


# ---- judging (pure) -------------------------------------------------------------
def judge_crash(lines, banner):
    """(ok, why) for one fixture's crash, from the serial lines after a
    RESET SENT mark: the reset's rst:0x15, then a panic report, then a ROM
    reset line (the restart), then the banner, and no panic after it."""
    i = next((n for n, ln in enumerate(lines) if "rst:0x15" in ln), None)
    if i is None:
        return False, "no rst:0x15 line read: the reset was not seen"
    p = next((n for n in range(i + 1, len(lines)) if any(t in lines[n] for t in PANIC)), None)
    if p is None:
        return False, "no panic report after the reset"
    r = next((n for n in range(p + 1, len(lines)) if ANY_RESET.search(lines[n])), None)
    if r is None:
        return False, "no ROM reset line after the panic: it did not restart"
    b = next((n for n in range(r + 1, len(lines)) if banner in lines[n]), None)
    if b is None:
        return False, "the restart did not reach the application (no %r)" % banner
    again = [ln for ln in lines[b + 1:] if any(t in ln for t in PANIC)]
    if again:
        return False, "it panicked again after the panic restart: %s" % again[0][:100]
    return True, "crashed (%s), restarted (%s), %r read" % (
        lines[p].strip()[:90], ANY_RESET.search(lines[r]).group(0), banner)


def stack_section(text):
    a = text.find(STACK_HEAD)
    b = text.find(THREADS_HEAD, a + 1) if a >= 0 else -1
    return None if a < 0 or b < 0 else text[a:b]


def judge_read(rc, text, want, other):
    """(verdict, why) for one esp-coredump info_corefile read."""
    if rc is None or any(t in text for t in NO_LINK):
        return "VOID", "esp-coredump did not reach the board (exit %s)" % rc
    if rc != 0:
        tail = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "FAIL", "esp-coredump exited %s: %s" % (rc, tail[-1][:160] if tail else "(none)")
    if DUMP_END not in text:
        return "FAIL", "the report has no %r: not a whole dump" % DUMP_END
    m = CRASHED.search(text)
    if not m or m.group(1) != TASK:
        return "FAIL", "the crashed task is %r, not %r" % (m.group(1) if m else None, TASK)
    st = stack_section(text)
    if st is None:
        return "FAIL", "no current thread stack in the report"
    frames = [f for _n, f in FRAME.findall(st)]
    if want not in frames:
        return "FAIL", "the backtrace does not name %s: %s" % (want, frames[:12])
    if other in frames:
        return "FAIL", "the backtrace names %s as well" % other
    return "PASS", "crashed task %r; the backtrace names %s (frame #%d of %d)" % (
        TASK, want, frames.index(want), len(frames))


def judge_keep(resets, before_ok, before, after, read):
    """(verdict, why): resets is bench_boot_reset's verdict, before_ok
    whether the bytes before decode against the first fixture's ELF,
    before/after the raw bytes (None: not read), read the read after."""
    if before is None or after is None:
        return "VOID", "a raw read of the partition failed"
    if not before_ok:
        return "VOID", "the partition held no dump of the fixture before the resets"
    if resets != "PASS":
        return resets if resets in ("FAIL", "VOID") else "VOID", \
            "bench_boot_reset is %s, not PASS" % resets
    if before != after:
        return "FAIL", "the partition's bytes changed over the resets"
    if read[0] != "PASS":
        return read[0], "the read after the resets: %s" % read[1]
    return "PASS", "the partition's bytes unchanged over the resets; %s" % read[1]


def judge_replace(read, first, now):
    """(verdict, why): read is the second fixture's read against its own
    ELF, first/now the raw bytes of the first dump and after the second
    crash (None: not read)."""
    if read[0] != "PASS":
        return read[0], "the read against the second fixture's ELF: %s" % read[1]
    if first is None or now is None:
        return "VOID", "a raw read of the partition failed"
    if first == now:
        return "FAIL", "the partition's bytes are still the first dump's"
    return "PASS", "the partition's bytes changed; %s" % read[1]


def overall(verdicts):
    if not verdicts or "FAIL" in verdicts:
        return "FAIL" if verdicts else "VOID"
    return "VOID" if "VOID" in verdicts else "PASS"


def canfail_shown(pre, read, keep, replace):
    """(shown, why): pre is [(what, ok)], the others (verdict, why)."""
    bad = [w for w, ok in pre if not ok]
    if bad:
        return False, "precondition not met: %s" % "; ".join(bad)
    notf = [n for n, v in (("read", read), ("keep", keep), ("replace", replace))
            if v[0] != "FAIL"]
    if notf:
        return False, "not FAIL: %s" % ", ".join("%s %s" % (n, dict(
            read=read, keep=keep, replace=replace)[n][0]) for n in notf)
    return True, "read, keep and replace each FAILED"


def is_erased(b):
    return b is not None and b.count(b"\xff") == len(b)


# ---- argv (pure) -------------------------------------------------------------------
def coredump_read_argv(cd, port, baud, elf, save):
    return cd + ["--chip", CHIP, "-p", port, "-b", str(baud), "info_corefile",
                 "--save-core", save, elf]


def coredump_decode_argv(cd, raw, elf):
    return cd + ["--chip", CHIP, "info_corefile", "--core", raw, "--core-format", "raw", elf]


def raw_read_argv(esp, port, baud, region, path):
    return esp + ["--chip", CHIP, "-p", port, "-b", str(baud), "--before", "default_reset",
                  "--after", "hard_reset", "read_flash", "0x%x" % region[0],
                  "0x%x" % region[1], path]


# ---- running -------------------------------------------------------------------------
def build(a):
    brf.need_slice("build.slice")
    code = os.path.dirname(HERE)
    import vtrux_tools
    vtrux_tools.check_idf_env()
    rc, top = brf.git("rev-parse", "--show-toplevel", cwd=code)
    rc2, harness = brf.git("rev-parse", "HEAD", cwd=top)
    rc3, dirty = brf.git("status", "--porcelain", cwd=top)
    if rc or rc2 or rc3 or dirty:
        raise SystemExit("bench_coredump_readback: this worktree is not a clean git "
                         "worktree; the harness must be committed:\n%s" % dirty)
    rc, commit = brf.git("rev-parse", "--verify", a.commit + "^{commit}", cwd=top)
    if rc:
        raise SystemExit("bench_coredump_readback: no commit %r" % a.commit)
    wt = os.path.join(os.path.dirname(top), WORKTREE)
    if os.path.exists(wt):
        raise SystemExit("bench_coredump_readback: %s is already there" % wt)
    run = brf.Run(a.out)
    run.note("bench_coredump_readback build %s" % time.strftime("%Y-%m-%d %H:%M:%S %z"))
    run.note("product commit %s" % commit)
    run.note("harness commit %s (this worktree, clean)" % harness)
    bad = []
    try:
        rc, out = brf.git("worktree", "add", "--detach", wt, commit, cwd=top)
        if rc:
            raise RuntimeError("git worktree add %s: %s" % (wt, out))
        for name, kind, keep in IMAGES:
            run.note("== %s" % name)
            rc, out = brf.git("checkout", "-q", "--force", "--detach", commit, cwd=wt)
            if rc:
                raise RuntimeError("git checkout %s: %s" % (commit, out))
            if kind:
                paths = [os.path.join(wt, *r.split("/")) for r in (MAIN_REL, SDK_REL)]
                texts = []
                for p in paths:
                    with open(p, encoding="utf-8", newline="") as f:
                        texts.append(f.read())
                for p, t in zip(paths, fixture_texts(texts[0], texts[1], kind, keep)):
                    with open(p, "w", encoding="utf-8", newline="") as f:
                        f.write(t)
                rc, out = brf.git("commit", "-q", "-a", "-m",
                                  "fixture %s (bench_coredump_readback, never on a branch)"
                                  % name, cwd=wt)
                if rc:
                    raise RuntimeError("the fixture commit: %s" % out)
                _rc, fixture = brf.git("rev-parse", "HEAD", cwd=wt)
                _rc, diff = brf.git("diff", "--unified=0", "--no-color", "--no-ext-diff",
                                    commit, fixture, cwd=wt)
                run.note("fixture %s (derived: %s + the fixture's edits)" % (fixture,
                                                                           commit[:12]))
                run.note("fixture diff:\n" + "\n".join("    " + ln for ln in diff.splitlines()))
                probs = fixture_diff_problems(diff, kind, keep)
                if probs:
                    raise RuntimeError("%s's diff is not its edits: %s" % (name, probs))
            bd = os.path.join(wt, "charge-interposer", "firmware", "build", VARIANT)
            if os.path.exists(bd):
                shutil.rmtree(bd)
            rc, _t = run.sh("build-" + name, [sys.executable, "-B", os.path.join(
                wt, "charge-interposer", "vtrux_tools.py"), "build", VARIANT]
                + (["--jobs", str(a.jobs)] if a.jobs else []), cwd=wt)
            run.note("build %s exit %s%s" % (name, rc, "" if rc == 0 else "  FAIL"))
            if rc != 0:
                raise RuntimeError("the build failed (logs/build-%s.log)" % name)
            elf = "interposer_%s.elf" % VARIANT
            mp = "interposer_%s.map" % VARIANT
            with open(os.path.join(bd, mp), encoding="utf-8", errors="replace") as f:
                map_text = f.read()
            with open(os.path.join(bd, "config", "sdkconfig.json"), encoding="utf-8") as f:
                sdk = json.load(f)
            probs = build_problems(map_text, sdk, kind, keep)
            run.note("%s build checks: %s" % (name, "; ".join(probs) if probs else "pass"))
            if probs:
                bad.append("%s build checks" % name)
            with open(os.path.join(bd, "flasher_args.json"), encoding="utf-8") as f:
                fa = json.load(f)
            img = os.path.join(a.out, "image-" + name)
            for rel in ["flasher_args.json", elf, mp, "config/sdkconfig.json"] + \
                    brf.image_files(fa):
                dst = os.path.join(img, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(bd, *rel.split("/")), dst)
                run.note("image-%s %-40s sha256 %s" % (name, rel, brf.sha256(dst)))
    except Exception as e:                           # noqa: BLE001
        run.note("STOPPED: %s: %s" % (type(e).__name__, e))
        bad.append("stopped")
    finally:
        if os.path.exists(wt):
            rc, out = brf.git("worktree", "remove", "--force", wt, cwd=top)
            brf.git("worktree", "prune", cwd=top)
            if rc or os.path.exists(wt):
                run.note("LEFTOVER worktree %s: %s" % (wt, out))
                bad.append("leftover worktree")
        run.note("SUMMARY: %s" % ("PASS" if not bad else "FAIL: " + ", ".join(bad)))
        run.write()
    return 0 if not bad else 1


def coredump_base():
    py = os.environ.get("IDF_PYTHON_ENV_PATH")
    if not py or not os.environ.get("IDF_PATH"):
        raise SystemExit("bench_coredump_readback: REFUSED: not in a shell exported for "
                         "ESP-IDF (no IDF_PATH or IDF_PYTHON_ENV_PATH)")
    if not shutil.which(GDB):
        raise SystemExit("bench_coredump_readback: REFUSED: no %s on PATH" % GDB)
    return [os.path.join(py, "bin", "python"), "-m", "esp_coredump"]


class Bench:
    """One bench part's steps, each logged and noted in the record."""

    def __init__(self, a, run):
        self.a, self.run = a, run
        self.esp = brf.esptool_base(a)
        self.cd = coredump_base()
        self.img = {n: os.path.abspath(os.path.join(a.images, "image-" + n))
                    for n, _k, _keep in IMAGES}
        self.fa = {}
        for n, d in self.img.items():
            with open(os.path.join(d, "flasher_args.json"), encoding="utf-8") as f:
                self.fa[n] = json.load(f)
        pt = [v for k, v in self.fa["product"]["flash_files"].items() if int(k, 16) == 0x8000]
        with open(os.path.join(self.img["product"], *pt[0].split("/")), "rb") as f:
            self.region = brf.coredump_region(f.read())
        self.fixture_on = False

    def elf(self, n):
        return os.path.join(self.img[n], "interposer_%s.elf" % VARIANT)

    def record_images(self):
        for n, _k, _keep in IMAGES:
            for rel in brf.image_files(self.fa[n]) + ["interposer_%s.elf" % VARIANT]:
                self.run.note("image-%s %-40s sha256 %s" % (
                    n, rel, brf.sha256(os.path.join(self.img[n], *rel.split("/")))))
        self.run.note("coredump partition 0x%x, 0x%x bytes" % self.region)

    def flash(self, n, erase):
        """True if every file of image n was written and verified (after the
        coredump region's erase, if erase)."""
        steps = brf.esptool_steps(self.esp, self.a.port, self.a.baud, self.fa[n], self.region)
        if not erase:
            steps = steps[1:]
        for name, argv in steps:
            step = "%s-%s" % (name, n)
            rc, text = self.run.sh(step, argv, cwd=self.img[n], timeout=600)
            verified = text.count("Hash of data verified")
            want = len(self.fa[n]["flash_files"]) if name == "write-flash" else 0
            good = rc == 0 and (name != "write-flash" or verified == want)
            self.run.note("%s exit %s%s%s" % (step, rc, ", %d of %d verified" % (verified, want)
                                              if name == "write-flash" else "",
                                              "" if good else "  FAIL (logs/%s.log)" % step))
            if not good:
                return False
        self.fixture_on = n != "product"
        if self.fixture_on:
            self.settle("after-" + n)
        else:
            time.sleep(3)
        return True

    def serial(self, name, reset, seconds):
        import bench_usb_host
        log = os.path.join(self.a.out, name + ".serial.log")
        hp = bench_usb_host.HostPort(self.a.port, log, lines_low=True)
        try:
            time.sleep(0.5)
            if reset:
                hp.hard_reset()
            time.sleep(seconds)
        finally:
            hp.close()
        with open(log, encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\n").split("\t", 1)[-1] for ln in f]
        if reset:
            marks = [n for n, ln in enumerate(lines) if ln.startswith("# RESET SENT")]
            lines = lines[marks[-1] + 1:] if marks else []
        return [ln for ln in lines if not ln.startswith("# ")]

    def settle(self, name):
        """Read the port --settle-s after an esptool connection to a fixture
        (it crashes once more, then runs): not judged, logged."""
        lines = self.serial("settle-" + name, False, self.a.settle_s)
        n = sum(1 for ln in lines if "Guru Meditation Error" in ln or "abort() was called" in ln)
        self.run.note("settle %s: %d s read, %d panic line(s), %d line(s)" % (
            name, self.a.settle_s, n, len(lines)))

    def crash(self, n):
        lines = self.serial("crash-" + n, True, self.a.crash_window_s)
        ok, why = judge_crash(lines, BANNER)
        self.run.note("crash %s: %s: %s" % (n, "seen" if ok else "NOT SEEN", why))
        saved = any("Core dump has been saved" in ln for ln in lines)
        self.run.note("  'Core dump has been saved' %s (ESP-IDF's logs are off: spec 8.3)"
                      % ("read" if saved else "not read"))
        return ok

    def read(self, name, n, want, other):
        save = os.path.join(self.a.out, name + ".core")
        rc, text = self.run.sh(name, coredump_read_argv(self.cd, self.a.port, self.a.baud,
                                                        self.elf(n), save), timeout=600)
        v = judge_read(rc, text, want, other)
        self.run.note("%s (esp-coredump over USB against image-%s's ELF) exit %s: %s: %s%s" % (
            name, n, rc, v[0], v[1], "; saved core sha256 %s" % brf.sha256(save)
            if os.path.exists(save) else ""))
        if self.fixture_on:
            self.settle(name)
        return v

    def raw(self, name):
        path = os.path.join(self.a.out, name + ".bin")
        rc, _t = self.run.sh(name, raw_read_argv(self.esp, self.a.port, self.a.baud,
                                                 self.region, path), timeout=300)
        data = None
        if rc == 0 and os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            if len(data) != self.region[1]:
                data = None
        self.run.note("%s (raw partition read) exit %s: %s" % (
            name, rc, "sha256 %s%s" % (brf.sha256(path), ", erased" if is_erased(data) else "")
            if data is not None else "NOT READ"))
        if self.fixture_on:
            self.settle(name)
        return data

    def decode(self, name, n, want, other):
        path = os.path.join(self.a.out, name + ".bin")
        rc, text = self.run.sh(name + "-decode", coredump_decode_argv(self.cd, path,
                                                                      self.elf(n)), timeout=300)
        v = judge_read(rc, text, want, other)
        self.run.note("%s decoded offline against image-%s's ELF: %s: %s" % (name, n, v[0], v[1]))
        return v[0] == "PASS"

    def erase(self, name):
        st = brf.esptool_steps(self.esp, self.a.port, self.a.baud, self.fa["product"],
                               self.region)[0]
        rc, _t = self.run.sh(name, st[1], timeout=300)
        self.run.note("%s (esptool erase_region 0x%x 0x%x) exit %s" % ((name,) + self.region
                                                                       + (rc,)))
        return rc == 0

    def resets(self, name):
        out = os.path.join(self.a.out, name + ".txt")
        rc, text = self.run.sh(name, [
            sys.executable, "-B", os.path.join(HERE, "bench_boot_reset.py"), "--port",
            self.a.port, "--resets", str(self.a.resets), "--window-s", "5", "--app-line",
            BANNER, "--out", out], timeout=self.a.resets * 30 + 120)
        final = [ln for ln in text.splitlines() if " boot-reset-0x15" in ln]
        v = final[-1].split(" ", 1)[0] if final else "VOID"
        v = "PASS" if rc == 0 and v == "PASS" else (v if v in ("FAIL", "VOID") else "VOID")
        tot = [ln for ln in text.splitlines() if ln.startswith("resets judged")]
        self.run.note("%s (bench_boot_reset, %d resets) exit %s: %s; %s" % (
            name, self.a.resets, rc, v, tot[-1] if tot else "(no totals)"))
        return v

    def restore(self):
        ok = self.flash("product", True)
        self.run.note("restore: the product bridge flashed, coredump partition erased: %s"
                      % ("done" if ok else "FAILED"))


def run_check(b):
    """The check: crash-a's dump read; kept over the product's resets;
    replaced by crash-b's."""
    A, B = FUNCS["abort"], FUNCS["trap"]
    note = b.run.note
    v = {}
    try:
        if not (b.flash("crash-a", True) and b.crash("crash-a")):
            note("STOPPED: crash-a was not flashed or its crash not seen")
            return {"read": ("VOID", "crash-a's crash not seen")}
        v["read"] = b.read("read-crash-a", "crash-a", A, B)
        if not b.flash("product", False):
            v["keep"] = ("VOID", "the product write failed")
            return v
        before = b.raw("keep-before")
        before_ok = before is not None and b.decode("keep-before", "crash-a", A, B)
        rs = b.resets("keep-resets")
        after = b.raw("keep-after")
        rd = b.read("read-keep", "crash-a", A, B)
        v["keep"] = judge_keep(rs, before_ok, before, after, rd)
        if not (b.flash("crash-b", False) and b.crash("crash-b")):
            v["replace"] = ("VOID", "crash-b was not written or its crash not seen")
            return v
        rb = b.read("read-crash-b", "crash-b", B, A)
        if not b.flash("product", False):
            v["replace"] = ("VOID", "the product write failed")
            return v
        now = b.raw("replace")
        if now is not None:
            b.decode("replace", "crash-b", B, A)
        v["replace"] = judge_replace(rb, after, now)
        return v
    finally:
        b.restore()


def run_canfail(b):
    """The can-fail: crash-b-keep over crash-a's dump (replace must FAIL),
    then the partition erased across the product's resets (keep and read
    must FAIL)."""
    A, B = FUNCS["abort"], FUNCS["trap"]
    note = b.run.note
    pre = []
    out = {"read": ("VOID", "not reached"), "keep": ("VOID", "not reached"),
           "replace": ("VOID", "not reached")}
    try:
        ok = b.flash("crash-a", True) and b.crash("crash-a")
        pre.append(("crash-a's crash seen", ok))
        if not ok:
            return pre, out
        pre.append(("crash-a's dump read", b.read("cf-read-crash-a", "crash-a", A, B)[0]
                    == "PASS"))
        ok = b.flash("product", False)
        first = b.raw("cf-first") if ok else None
        pre.append(("the first raw read", first is not None and not is_erased(first)))
        ok = b.flash("crash-b-keep", False) and b.crash("crash-b-keep")
        pre.append(("crash-b-keep's crash seen", ok))
        if not ok:
            return pre, out
        rb = b.read("cf-read-crash-b-keep", "crash-b-keep", B, A)
        ok = b.flash("product", False)
        now = b.raw("cf-replace") if ok else None
        out["replace"] = judge_replace(rb, first, now)
        note("cf replace: %s: %s (bytes the first dump's: %s)" % (
            out["replace"] + ("yes" if first is not None and first == now else "no",)))
        before_ok = now is not None and b.decode("cf-replace", "crash-a", A, B)
        pre.append(("crash-a's dump there before the resets", before_ok))
        ok = b.erase("cf-erase")
        pre.append(("the erase", ok))
        rs = b.resets("cf-keep-resets")
        pre.append(("bench_boot_reset PASS", rs == "PASS"))
        after = b.raw("cf-keep-after")
        out["read"] = b.read("cf-read-erased", "crash-a", A, B)
        out["keep"] = judge_keep(rs, before_ok, now, after, out["read"])
        note("cf read (no dump): %s: %s" % out["read"])
        note("cf keep: %s: %s" % out["keep"])
        return pre, out
    finally:
        b.restore()


def bench(a, canfail):
    brf.need_slice("bench.slice")
    run = brf.Run(a.out)
    run.note("bench_coredump_readback %s %s" % ("canfail" if canfail else "run",
                                                time.strftime("%Y-%m-%d %H:%M:%S %z")))
    _rc, harness = brf.git("rev-parse", "HEAD", cwd=HERE)
    _rc, dirty = brf.git("status", "--porcelain", "--", ".", cwd=HERE)
    run.note("harness commit %s%s" % (harness, " (bench_drivers/ DIRTY)" if dirty else ""))
    run.note("port %s, images %s" % (a.port, os.path.abspath(a.images)))
    try:
        b = Bench(a, run)
        b.record_images()
        if canfail:
            pre, out = run_canfail(b)
            for w, ok in pre:
                run.note("precondition %s: %s" % (w, "met" if ok else "NOT MET"))
            shown, why = canfail_shown(pre, out["read"], out["keep"], out["replace"])
            run.note("SUMMARY: CAN-FAIL %s: %s" % ("SHOWN" if shown else "NOT SHOWN", why))
            return 0 if shown else 1
        v = run_check(b)
        for part in ("read", "keep", "replace"):
            pv = v.get(part, ("VOID", "not reached"))
            run.note("%s coredump-readback.%s: %s" % (pv[0], part, pv[1]))
        verdict = overall([v.get(p, ("VOID",))[0] for p in ("read", "keep", "replace")])
        run.note("SUMMARY: %s coredump-readback" % verdict)
        return 0 if verdict == "PASS" else 1
    finally:
        run.write()


# ---- self-test ------------------------------------------------------------------------
MAIN = ("#include <Arduino.h>\n\nstatic void x() {\n}\n\n" + SETUP
        + "  // logging\n  Serial.begin(115200);\n}\n\nvoid loop() {\n}\n")
SDK = ("CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y\n# CONFIG_ESP_COREDUMP_CHECK_BOOT is not set\n"
       + NOOV_OLD + "\nCONFIG_X=y\n")


def _diff(kind, keep):
    d = ("diff --git a/%s b/%s\nindex 1..2 100644\n--- a/%s\n+++ b/%s\n@@ -5,0 +6,18 @@\n"
         % ((MAIN_REL,) * 4) + "".join("+%s\n" % ln for ln in crash_lines(kind))
         + "@@ -6,0 +25 @@\n+%s" % CALL)
    if keep:
        d += ("diff --git a/%s b/%s\nindex 3..4 100644\n--- a/%s\n+++ b/%s\n@@ -3 +3 @@\n"
              "-%s\n+%s\n") % ((SDK_REL,) * 4 + (NOOV_OLD, NOOV_NEW))
    return d


SDK_OK = {"ESP_COREDUMP_ENABLE_TO_FLASH": True, "ESP_COREDUMP_CHECK_BOOT": False,
          "ESP_COREDUMP_FLASH_NO_OVERWRITE": False}
CRASH_OK = ["ESP-ROM:esp32s3-20210327", "rst:0x15 (USB_UART_CHIP_RESET),boot:0x8 (SPI_FAST_FLASH_BOOT)",
            "Vtrux charge interposer bridge", "", "abort() was called at PC 0x4200a1b2 on core 1",
            "", "Backtrace: 0x40375a1e:0x3fcf2a40 0x4037c1d5:0x3fcf2a60", "",
            "ELF file SHA256: 6564bc7d", "Rebooting...",
            "ESP-ROM:esp32s3-20210327", "rst:0xc (RTC_SW_CPU_RST),boot:0x8 (SPI_FAST_FLASH_BOOT)",
            "Vtrux charge interposer bridge", "spec 2.3: vehicle port in normal mode"]
REPORT_A = """INFO: Invoking command: ...
===============================================================
==================== ESP32 CORE DUMP START ====================

Crashed task handle: 0x3fcf2c58, name: 'fixture_crash', GDB name: 'process 1070541912'
Crashed task is not in the interrupt context
Panic reason: abort() was called at PC 0x4200a1b2 on core 1

================== CURRENT THREAD REGISTERS ===================
exccause       0x3f (Reserved)
pc             0x40379d4a          0x40379d4a <panic_abort+22>

==================== CURRENT THREAD STACK =====================
#0  0x40379d4a in panic_abort (details=0x3fcf2a8f "abort() was called at PC 0x4200a1b2 on core 1") at /x/panic.c:477
#1  0x40379d38 in esp_system_abort (details=0x3fcf2a8f "abort()") at /x/port/esp_system_chip.c:87
#2  0x4037f6a1 in abort () at /x/abort.c:38
#3  0x4200a1b2 in intp_fixture_crash_abort () at /x/main/main.cpp:772
#4  0x4200a1c3 in intp_fixture_crash_task () at /x/main/main.cpp:776
#5  0x4037c1d5 in vPortTaskWrapper (pxCode=0x4200a1b8 <intp_fixture_crash_task(void*)>, pvParameters=0x0) at /x/port.c:139

======================== THREADS INFO =========================
  Id   Target Id          Frame
* 1    process 1070541912 0x40379d4a in panic_abort ()
  2    process 1070546080 0x4037c4f2 in esp_cpu_wait_for_intr () at /x/cpu.c:64
  3    process 1070544216 0x4037c4f2 in vTaskDelay () at y.c:1

       TCB             NAME PRIO C/B  STACK USED/FREE
---------- ---------------- -------- ----------------
0x3fcf2c58    fixture_crash        1             x/y

==================== THREAD 3 (TCB: 0x3fcf1234, name: 'loopTask') =====================
#0  0x4200b000 in intp_fixture_crash_trap () at /x/y.c:1
#1  0x4037c1d5 in vPortTaskWrapper (pxCode=0x0, pvParameters=0x0) at /x/port.c:139


======================= ALL MEMORY REGIONS ========================
Name   Address   Size   Attrs

===================== ESP32 CORE DUMP END =====================
===============================================================
Done!
"""
REPORT_B = REPORT_A.replace(
    REPORT_A[REPORT_A.index("#0  0x40379d4a"):REPORT_A.index("#4  0x4200a1c3")],
    "#0  intp_fixture_crash_trap () at /x/main/main.cpp:772\n").replace(
    "#0  0x4200b000 in intp_fixture_crash_trap ()", "#0  0x4200b000 in intp_fixture_crash_abort ()")


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

    A, B = FUNCS["abort"], FUNCS["trap"]
    m, s = fixture_texts(MAIN, SDK, "abort", False)
    check(SETUP + CALL in m and m.count(SETUP) == 1, "crash-a calls the crash start first in setup()")
    check("\n".join(crash_lines("abort")) + "\n" + SETUP in m, "crash-a's block is just before setup()")
    check(m.replace("\n".join(crash_lines("abort")) + "\n", "").replace(CALL, "") == MAIN,
          "nothing else in main.cpp changes")
    check(s == SDK, "crash-a leaves sdkconfig alone")
    check("abort();" in m and "__builtin_trap" not in m and A in m and B not in m,
          "crash-a aborts in its function")
    m2, s2 = fixture_texts(MAIN, SDK, "trap", True)
    check("__builtin_trap();" in m2 and "abort();" not in m2 and B in m2 and A not in m2,
          "crash-b traps in its function")
    check(NOOV_NEW in s2 and s2.replace(NOOV_NEW, NOOV_OLD) == SDK,
          "crash-b-keep puts FLASH_NO_OVERWRITE on, nothing else in sdkconfig")
    check(fixture_texts(MAIN, SDK, "trap", False)[1] == SDK, "crash-b leaves sdkconfig alone")
    check("ESP_RST_PANIC" in m and "!= ESP_RST_PANIC" in m, "no crash after a panic restart")
    check("pdMS_TO_TICKS(5000)" in m, "the crash 5 s after setup() starts")
    check(raises(fixture_texts, MAIN.replace(SETUP, "void setup(void) {\n"), SDK, "abort", False),
          "no setup() line is refused")
    check(raises(fixture_texts, MAIN + SETUP, SDK, "abort", False), "a doubled setup() is refused")
    check(raises(fixture_texts, MAIN, SDK.replace(NOOV_OLD, NOOV_NEW), "trap", True),
          "FLASH_NO_OVERWRITE already on is refused for crash-b-keep")
    check(raises(fixture_texts, MAIN, SDK + NOOV_OLD + "\n", "trap", True),
          "a doubled FLASH_NO_OVERWRITE line is refused")
    for kind, keep in (("abort", False), ("trap", False), ("trap", True)):
        check(fixture_diff_problems(_diff(kind, keep), kind, keep) == [],
              "%s keep=%s: its own diff passes" % (kind, keep))
    check(fixture_diff_problems(_diff("abort", False), "trap", False) != [],
          "crash-a's diff is refused as crash-b's")
    check(fixture_diff_problems(_diff("trap", False), "trap", True) != [],
          "crash-b's diff is refused as crash-b-keep's")
    check(fixture_diff_problems(_diff("trap", True), "trap", False) != [],
          "crash-b-keep's diff is refused as crash-b's")
    check(fixture_diff_problems(_diff("abort", False) + "+extra\n", "abort", False) != [],
          "an added line is refused")
    check(fixture_diff_problems(_diff("abort", False).replace("@@ -6,0 +25 @@\n",
                                                             "@@ -6 +25 @@\n-  // logging\n"),
                                "abort", False) != [], "a removed line is refused")
    check(fixture_diff_problems(_diff("abort", False).replace(MAIN_REL, "charge-interposer/x.cpp"),
                                "abort", False) != [], "another file is refused")
    check(fixture_diff_problems(_diff("abort", False).replace(
        "index 1..2 100644", "old mode 100644\nnew mode 100755"), "abort", False) != [],
        "a mode change is refused")
    check(fixture_diff_problems("", "abort", False) != [], "an empty diff is refused")
    mp_a, mp_b = " .text.%s 0x42\n" % A, " .text.%s 0x42\n" % B
    check(build_problems(mp_a, SDK_OK, "abort", False) == [], "a crash-a build passes")
    check(build_problems(mp_b, SDK_OK, "trap", False) == [], "a crash-b build passes")
    check(build_problems(mp_b, dict(SDK_OK, ESP_COREDUMP_FLASH_NO_OVERWRITE=True), "trap", True)
          == [], "a crash-b-keep build passes")
    check(build_problems("", SDK_OK, None, False) == [], "a product build passes")
    check(build_problems(mp_a, SDK_OK, None, False) != [], "a product naming a crash fails")
    check(build_problems(mp_b, SDK_OK, "abort", False) != [], "crash-a without its function fails")
    check(build_problems(mp_a + mp_b, SDK_OK, "abort", False) != [],
          "crash-a naming crash-b's function fails")
    check(build_problems(mp_b, SDK_OK, "trap", True) != [],
          "crash-b-keep with FLASH_NO_OVERWRITE false fails")
    check(build_problems(mp_b, dict(SDK_OK, ESP_COREDUMP_FLASH_NO_OVERWRITE=True), "trap", False)
          != [], "crash-b with FLASH_NO_OVERWRITE true fails")
    check(build_problems("", dict(SDK_OK, ESP_COREDUMP_CHECK_BOOT=True), None, False) != [],
          "CHECK_BOOT true fails")
    check(build_problems("", {"ESP_COREDUMP_CHECK_BOOT": False,
                              "ESP_COREDUMP_FLASH_NO_OVERWRITE": False}, None, False) != [],
          "no ENABLE_TO_FLASH fails")
    check(judge_crash(CRASH_OK, BANNER)[0], "a crash, restart and banner is seen")
    check(not judge_crash(CRASH_OK[2:], BANNER)[0], "no rst:0x15: not seen")
    check(not judge_crash([ln for ln in CRASH_OK if "abort()" not in ln and "Backtrace" not in ln],
                          BANNER)[0], "no panic report: not seen")
    check(not judge_crash(CRASH_OK[:10], BANNER)[0], "no restart after the panic: not seen")
    check(not judge_crash(CRASH_OK[:12], BANNER)[0], "the restart without the banner: not seen")
    check(not judge_crash(CRASH_OK + ["Guru Meditation Error: Core  1 panic'ed"], BANNER)[0],
          "a second panic after the restart: not seen")
    check(judge_crash(CRASH_OK[:2] + CRASH_OK[3:], BANNER)[0],
          "the banner before the crash is not needed")
    check(judge_read(0, REPORT_A, A, B)[0] == "PASS", "crash-a's report names its function")
    check(judge_read(0, REPORT_B, B, A)[0] == "PASS", "crash-b's report, frame #0 without an address")
    check(judge_read(0, REPORT_A, B, A)[0] == "FAIL", "crash-a's report read as crash-b's fails")
    check(judge_read(0, REPORT_B, A, B)[0] == "FAIL", "crash-b's report read as crash-a's fails")
    check(judge_read(0, REPORT_A.replace("#4  0x4200a1c3 in intp_fixture_crash_task",
                                         "#4  0x4200a1c3 in %s" % B), A, B)[0] == "FAIL",
          "a backtrace naming both fails")
    check(judge_read(0, REPORT_A.replace("name: 'fixture_crash'", "name: 'loopTask'"), A, B)[0]
          == "FAIL", "another crashed task fails")
    check(judge_read(0, REPORT_A.replace(DUMP_END, "x"), A, B)[0] == "FAIL",
          "a report with no end fails")
    check(judge_read(0, REPORT_A.replace(STACK_HEAD, "x"), A, B)[0] == "FAIL",
          "a report with no current thread stack fails")
    check(judge_read(1, "Invalid application image for coredump: coredump SHA256(1) != app "
                     "SHA256(2).\n", A, B)[0] == "FAIL", "the app SHA256 refused: FAIL")
    check(judge_read(1, "Incorrect size of core dump image\n", A, B)[0] == "FAIL",
          "no dump in the partition: FAIL")
    check(judge_read(0, "", A, B)[0] == "FAIL", "exit 0 with no report: FAIL")
    check(judge_read(1, REPORT_A, A, B)[0] == "FAIL", "a whole report with a non-zero exit: FAIL")
    check(judge_read(0, REPORT_A.replace(" in intp_fixture_crash_abort ()", " in other_fn ()"),
                     A, B)[0] == "FAIL", "a backtrace naming neither function: FAIL")
    check(judge_read(2, "A fatal error occurred: Failed to connect to ESP32-S3\n", A, B)[0]
          == "VOID", "no connection: VOID")
    check(judge_read(None, "TimeoutExpired", A, B)[0] == "VOID", "a timeout: VOID")
    check(judge_read(0, REPORT_A, A, B)[1].endswith("(frame #3 of 6)"),
          "the frame is found in the current thread's stack only")
    P, F, V = ("PASS", "ok"), ("FAIL", "x"), ("VOID", "y")
    d1, d2 = b"\x01" * 8, b"\x02" * 8
    check(judge_keep("PASS", True, d1, d1, P)[0] == "PASS", "keep: same bytes and a read: PASS")
    check(judge_keep("PASS", True, d1, d2, P)[0] == "FAIL", "keep: the bytes changed: FAIL")
    check(judge_keep("PASS", True, d1, d1, F)[0] == "FAIL", "keep: the read FAILED: FAIL")
    check(judge_keep("PASS", True, d1, d1, V)[0] == "VOID", "keep: the read VOID: VOID")
    check(judge_keep("FAIL", True, d1, d1, P)[0] == "FAIL", "keep: the resets FAILED: FAIL")
    check(judge_keep("VOID", True, d1, d1, P)[0] == "VOID", "keep: the resets VOID: VOID")
    check(judge_keep("PASS", False, d1, d1, P)[0] == "VOID", "keep: no dump before: VOID")
    check(judge_keep("PASS", True, None, d1, P)[0] == "VOID", "keep: no raw read: VOID")
    check(judge_replace(P, d1, d2)[0] == "PASS", "replace: read and the bytes changed: PASS")
    check(judge_replace(P, d1, d1)[0] == "FAIL", "replace: the bytes unchanged: FAIL")
    check(judge_replace(F, d1, d2)[0] == "FAIL", "replace: the read FAILED: FAIL")
    check(judge_replace(V, d1, d2)[0] == "VOID", "replace: the read VOID: VOID")
    check(judge_replace(P, None, d2)[0] == "VOID", "replace: no raw read: VOID")
    check(overall(["PASS"] * 3) == "PASS" and overall(["PASS", "FAIL", "VOID"]) == "FAIL"
          and overall(["PASS", "VOID", "PASS"]) == "VOID" and overall([]) == "VOID",
          "the check's verdict from its parts")
    pre = [("a", True), ("b", True)]
    check(canfail_shown(pre, F, F, F)[0], "can-fail: all three FAIL: shown")
    check(not canfail_shown(pre, F, F, P)[0], "can-fail: replace PASS: not shown")
    check(not canfail_shown(pre, F, V, F)[0], "can-fail: keep VOID: not shown")
    check(not canfail_shown(pre, P, F, F)[0], "can-fail: read PASS: not shown")
    check(not canfail_shown(pre + [("c", False)], F, F, F)[0],
          "can-fail: a precondition not met: not shown")
    check(is_erased(b"\xff" * 16) and not is_erased(b"\xff" * 15 + b"\0") and not is_erased(None),
          "an erased partition")
    r = coredump_read_argv(["cd"], "/dev/x", 460800, "a.elf", "s.core")
    check(r == ["cd", "--chip", "esp32s3", "-p", "/dev/x", "-b", "460800", "info_corefile",
                "--save-core", "s.core", "a.elf"], "the read over USB: the port and the ELF")
    check(coredump_decode_argv(["cd"], "d.bin", "a.elf")[-5:] ==
          ["--core", "d.bin", "--core-format", "raw", "a.elf"], "the offline decode of a raw read")
    w = raw_read_argv(["esptool"], "/dev/x", 460800, (0x7F0000, 0x10000), "o.bin")
    check(w[w.index("read_flash"):] == ["read_flash", "0x7f0000", "0x10000", "o.bin"]
          and "/dev/x" in w, "the raw read covers the coredump partition, on the port")
    print("\nbench_coredump_readback self-test: %s" % ("all checks OK" if not fails
                                                     else "%d FAILED" % len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("the block not put before setup()",
     "    main = main.replace(SETUP, \"\\n\".join(crash_lines(kind)) + \"\\n\" + SETUP + CALL)",
     "    main = main.replace(SETUP, SETUP + CALL)"),
    ("the call not put in setup()", "\"\\n\" + SETUP + CALL)", "\"\\n\" + SETUP)"),
    ("FLASH_NO_OVERWRITE left off", "    return main, (sdk.replace(NOOV_OLD, NOOV_NEW) if keep else sdk)",
     "    return main, sdk"),
    ("the panic restart guard dropped",
     "            \"  if (esp_reset_reason() != ESP_RST_PANIC) {\",",
     "            \"  if (1) {\","),
    ("a missing or doubled edit text taken", "        if n != 1:\n            raise ValueError",
     "        if n > 1:\n            raise ValueError"),
    ("the diff's files not checked", "    if files != want_files:", "    if not files:"),
    ("the added lines not checked", "    if added != want_added:", "    if not added:"),
    ("the removed lines not checked", "    if removed != ([NOOV_OLD] if keep else []):",
     "    if False:"),
    ("the map's other function not checked", "        if (k == kind) != (fn in map_text):",
     "        if k == kind and fn not in map_text:"),
    ("FLASH_NO_OVERWRITE not checked", "            \"ESP_COREDUMP_FLASH_NO_OVERWRITE\": keep}",
     "            }"),
    ("the crash's reset not needed", "        return False, \"no rst:0x15 line read",
     "        return True, \"no rst:0x15 line read"),
    ("no panic needed", "        return False, \"no panic report after the reset\"",
     "        return True, \"no panic report after the reset\""),
    ("no restart needed", "        return False, \"no ROM reset line after the panic",
     "        return True, \"no ROM reset line after the panic"),
    ("no banner needed", "        return False, \"the restart did not reach the application",
     "        return True, \"the restart did not reach the application"),
    ("a second panic taken", "    if again:\n        return False", "    if False:\n        return False"),
    ("a failed connection judged FAIL", "    if rc is None or any(t in text for t in NO_LINK):",
     "    if rc is None:"),
    ("a non-zero exit taken", "    if rc != 0:\n        tail", "    if False:\n        tail"),
    ("a report with no end taken", "    if DUMP_END not in text:", "    if False:"),
    ("the crashed task not checked", "    if not m or m.group(1) != TASK:", "    if False:"),
    ("the backtrace not checked", "    if want not in frames:", "    if not frames:"),
    ("the other function taken", "    if other in frames:", "    if False:"),
    ("the frames taken from the whole report", "    frames = [f for _n, f in FRAME.findall(st)]",
     "    frames = [f for _n, f in FRAME.findall(text)]"),
    ("keep: the bytes not compared", "    if before != after:\n        return \"FAIL\"",
     "    if False:\n        return \"FAIL\""),
    ("keep: the resets not judged", "    if resets != \"PASS\":", "    if False:"),
    ("keep: the read not judged", "    if read[0] != \"PASS\":\n        return read[0], \"the read after",
     "    if False:\n        return read[0], \"the read after"),
    ("keep: no dump before taken", "    if not before_ok:", "    if False:"),
    ("replace: the read not judged", "    if read[0] != \"PASS\":\n        return read[0], \"the read against",
     "    if False:\n        return read[0], \"the read against"),
    ("replace: the bytes not compared", "    if first == now:", "    if False:"),
    ("a VOID part passes the check", "    return \"VOID\" if \"VOID\" in verdicts else \"PASS\"",
     "    return \"PASS\""),
    ("can-fail: a part not FAILED taken", "            if v[0] != \"FAIL\"]",
     "            if v[0] == \"PASS\"]"),
    ("can-fail: preconditions not checked", "    if bad:\n        return False, \"precondition",
     "    if False:\n        return False, \"precondition"),
    ("the raw read off the coredump region", "\"read_flash\", \"0x%x\" % region[0],",
     "\"read_flash\", \"0x%x\" % region[1],"),
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
    for name in ("run", "canfail"):
        p = sub.add_parser(name)
        p.add_argument("--images", required=True)
        p.add_argument("--port", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--baud", type=int, default=460800)
        p.add_argument("--esptool-argv")
        p.add_argument("--resets", type=int, default=20 if name == "run" else 5)
        p.add_argument("--settle-s", type=float, default=20.0)
        p.add_argument("--crash-window-s", type=float, default=25.0)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return build(a)
    if a.resets < 1:
        ap.error("--resets must be at least 1")
    return bench(a, a.cmd == "canfail")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
