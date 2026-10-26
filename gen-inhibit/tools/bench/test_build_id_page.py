#!/usr/bin/env python3
"""Tests that lowid_flood_skip_bench.build_id() names the running image from /gen_inhibit
and never asks for /check_status (reviewer, 2026-10-20: it answers with the device's
config, credentials included). Until 2026-10-25 build_id() read git_version and fw_version
from /check_status, and the suite's B1, B4, B9, B10, B12 and B20 called it (gen-inhibit
reviewer, 2026-10-25: move it as 4db69fe moved e4 and the TX-abort bench).

The device is faked under lowid_flood_skip_bench.dev_get. Its /check_status answers with a
made-up value, so a request for it shows in the paths asked for and in what comes back.

  build_id   a good page names its elf_sha256 and nothing else; a page that does not
             answer, does not parse, is not an object, has no elf_sha256, or whose
             elf_sha256 is not 64 lower-case hex names nothing; only /gen_inhibit asked
  callers    restore_unarmed_check.page_state() keeps the page's elf_sha256 and asks only
             /gen_inhibit, and its judge takes 8+ leading hex digits of it; reset_matrix.build_matches() (--expect-elf): unset matches,
             8+ leading hex digits or the whole hash match, 7 digits or another image do
             not, nor does a build with no elf_sha256
  source     no string constant outside a docstring names /check_status in build_id()'s
             module or in any of its callers (a control finds one in a probe file); every
             bench file that calls build_id() is among those scanned

    py -3.14 -B tools/gen_inhibit/bench/test_build_id_page.py
Mutants: test_build_id_page_mutants.py. BUILD_ID_DIR runs another copy of the files (the
mutants use it). Gen-inhibit tester, 2026-10-25.
"""
import ast
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("BUILD_ID_DIR") or HERE
sys.path.insert(0, SRC)
sys.path.insert(1, HERE)
import gi_paths                       # noqa: E402
import lowid_flood_skip_bench as LF   # noqa: E402
import reset_matrix as RM             # noqa: E402
import restore_unarmed_check as RU    # noqa: E402

# build_id()'s module and every file that called it on 2026-10-25 (restore_unarmed_check now
# reads the page itself).
CALLERS = ("lowid_flood_skip_bench.py", "page_only_baseline.py", "reset_matrix.py", "restart_error_frames.py",
           "restore_unarmed_check.py", "interlock_bench.py", "live_jam_arm.py", "autoarm_boot_to_live.py",
           "skip_method_bench.py", "slcan_refusal_probe.py", "trip6_rearm_check.py", "wrong_bitrate_errors.py",
           "reset_rate_arms.py")
FAILS = []
MADE_UP = "made-up-value-not-a-credential"
SHA = "3639f8b2" + "c0" * 28


def check(label, cond, detail=""):
    print("%s  %s  %s" % ("ok  " if cond else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not cond:
        FAILS.append(label)


class Device:
    """A WiCAN behind dev_get. /gen_inhibit answers `page` (a dict or list, a raw string,
    or None for no answer); /check_status answers with the made-up value."""

    def __init__(self, page):
        self.page, self.paths = page, []

    def dev_get(self, path, timeout=8.0):
        self.paths.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return json.dumps({"git_version": MADE_UP, "fw_version": MADE_UP, "sta_pass": MADE_UP})
        if path.startswith("/gen_inhibit"):
            if self.page is None:
                raise OSError("timed out")
            return self.page if isinstance(self.page, str) else json.dumps(self.page)
        raise OSError("HTTP 404")


def build_id():
    for page, want, label in [
            ({"elf_sha256": SHA, "mode": 0}, {"elf_sha256": SHA}, "a good page names its elf_sha256, nothing else"),
            (None, None, "a page that does not answer names nothing"),
            ("{cut", None, "a page that does not parse names nothing"),
            ([SHA], None, "a page that is not an object names nothing"),
            ({"mode": 0}, None, "a page with no elf_sha256 names nothing"),
            ({"elf_sha256": SHA[:40], "mode": 0}, None, "an elf_sha256 shorter than 64 names nothing"),
            ({"elf_sha256": SHA.upper(), "mode": 0}, None, "an upper-case elf_sha256 is not the page's form"),
            ({"elf_sha256": "g" * 64, "mode": 0}, None, "64 characters that are not hex name nothing")]:
        dev = Device(page)
        LF.dev_get = dev.dev_get
        try:
            got = LF.build_id()
        except Exception as e:      # noqa: BLE001  a raise is this case's FAIL, not the part's
            got = "raised %s: %s" % (type(e).__name__, e)
        check("build_id: %s" % label, got == want, got)
        check("build_id: only /gen_inhibit asked (%s)" % label, dev.paths == ["/gen_inhibit"], dev.paths)
        check("build_id: the made-up value nowhere (%s)" % label, MADE_UP not in json.dumps(got))


def callers():
    dev = Device({"elf_sha256": SHA, "mode": 0, "drv": None, "uptime_ms": 5, "reset_reason": "SW"})
    LF.dev_get = dev.dev_get
    st = RU.page_state()
    check("restore_unarmed_check.page_state: the page's elf_sha256", (st or {}).get("elf_sha256") == SHA, st)
    check("restore_unarmed_check.page_state: only /gen_inhibit asked", set(dev.paths) == {"/gen_inhibit"}, dev.paths)
    check("restore_unarmed_check.page_state: no git_version kept", "git_version" not in (st or {}), st)
    sent = [(k * 0.01, 0x051, bytes([8, 0, 0x80, 0xFF, 0x7F, k & 0x0F])) for k in range(100)]
    page = {"mode": 0, "drv": None, "elf_sha256": SHA}
    for expect, want, label in [(SHA[:8], "PASS", "8 leading hex digits -> PASS"),
                                (SHA[:7], "FAIL", "7 digits -> FAIL (too short to name an image)"),
                                ("a5a0888c", "FAIL", "another image -> FAIL")]:
        got = RU.judge(sent, list(sent), page, page, expect)[0]
        check("restore_unarmed_check.judge: %s" % label, got == want, got)
    b = {"elf_sha256": SHA}
    for build, expect, want, label in [
            (b, None, True, "unset matches"),
            (b, SHA[:8], True, "8 leading hex digits match"),
            (b, SHA, True, "the whole hash matches"),
            (b, SHA[:8].upper(), True, "upper-case expectation matches"),
            (b, SHA[:7], False, "7 digits are too short to name an image"),
            (b, "a5a0888c", False, "another image does not match"),
            (b, SHA[2:10], False, "digits from inside the hash do not match"),
            (None, SHA[:8], False, "no build read does not match"),
            ({"git_version": SHA[:8]}, SHA[:8], False, "a git_version does not stand for the elf_sha256")]:
        got = RM.build_matches(build, expect)
        check("reset_matrix.build_matches: %s" % label, got is want, got)


def named_in_code(path):
    """String constants naming /check_status, docstrings left out (comments are not in the AST)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "check_status" in n.value
            and id(n) not in docs]


def source():
    for name in CALLERS:
        path = os.path.join(SRC, name)
        if not os.path.exists(path):
            check("source: %s is there to scan" % name, False, path)
            continue
        lines = named_in_code(path)
        check("source: %s names /check_status in no code string" % name, not lines, lines)
    users = sorted(n for n in os.listdir(HERE) if n.endswith(".py") and not n.startswith("test_")
                   and "lowid_flood_skip_bench" in open(os.path.join(HERE, n), encoding="utf-8").read()
                   and "build_id()" in open(os.path.join(HERE, n), encoding="utf-8").read())
    check("source: every bench file that calls build_id() is scanned (the list is complete)",
          users and not set(users) - set(CALLERS), sorted(set(users) - set(CALLERS)))
    probe = os.path.join(gi_paths.scratch("build_id_page"), "probe.py")
    open(probe, "w", encoding="utf-8").write('"""/check_status in a docstring"""\nx = dev_get("/check_status")\n')
    check("source: the check finds a request in code (control)", named_in_code(probe) == [2], named_in_code(probe))
    shutil.rmtree(os.path.dirname(probe), ignore_errors=True)


def main():
    real = LF.dev_get
    for part in (build_id, callers, source):
        try:
            part()
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
        finally:
            LF.dev_get = real
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
