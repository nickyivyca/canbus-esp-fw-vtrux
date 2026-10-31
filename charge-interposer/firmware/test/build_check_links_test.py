"""Tests the HARNESS: build_check.check_build_links (spec 9, Build check,
C2b at b7dc12e3fa5c6852, user 2026-10-13).

The spec: the witness build differs from the bench build only by the witness
switch, and the bench build from the flight build only by its serial-output
settings. The check compares PlatformIO's RESOLVED settings, so this tests it
two ways:

1. On the real resolved config, mutated in memory: one extra flag on each
   side of each link must fail, and so must a dropped flag, an extra unflag,
   and any other setting that reaches the image (build_src_filter, lib_deps,
   board, platform, ...). A monitor setting must not, as it never reaches
   the image.
2. End to end: copies of platformio.ini with one extra flag in [env:
   esp32-can-x2], [env:esp32-can-x2-witness] or [env:truck], resolved by
   `platformio project config` itself. The unmodified copy must pass. Each
   mutant first proves it applied, so a text edit that matched nothing
   cannot pass as a clean run.

Run:  py -3.14 charge-interposer/firmware/test/build_check_links_test.py
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# charge-interposer/, for scratch_dir; last, so it shadows nothing
sys.path.append(os.path.dirname(os.path.dirname(HERE)))
import scratch_dir                                  # noqa: E402

import build_check as BC

failures = 0
EXTRA = "-DCANFAIL_EXTRA=1"
W, B, T = BC.WITNESS_ENV, BC.BENCH_ENV, BC.TRUCK_ENV


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def links(cfg):
    return BC.check_build_links(cfg=cfg)


def mutated(cfg, env, opt, fn):
    c = copy.deepcopy(cfg)
    c[env][opt] = fn(c[env].get(opt))
    return c


def add(flag):
    return lambda v: list(v or []) + [flag]


real, err = BC.resolved_config()
check(err is None and real and all(e in real for e in (W, B, T)),
      "the real config resolves with all three environments (%s)" % err)
if err or not real:
    sys.exit(1)

# -- 1. in memory ------------------------------------------------------
p = links(real)
check(p == [], "the real platformio.ini passes (%s)" % p)

cases = (
    ("extra flag in the witness env", W, "build_flags", add(EXTRA),
     "build_flags adds"),
    ("extra flag in the bench env, against the witness", B, "build_flags",
     add(EXTRA), "esp32-can-x2-witness] build_flags drops"),
    ("extra flag in the truck env", T, "build_flags", add(EXTRA),
     "truck] build_flags adds"),
    ("extra flag in the bench env, against the truck", B, "build_flags",
     add(EXTRA), "truck] build_flags drops"),
    ("the witness switch missing", W, "build_flags",
     lambda v: [f for f in v if f != "-DINTP_ORDER_WITNESS=1"],
     "allows only ['-DINTP_ORDER_WITNESS=1']"),
    ("INTP_NO_SERIAL missing from the truck", T, "build_flags",
     lambda v: [f for f in v if f != "-DINTP_NO_SERIAL=1"],
     "truck] build_flags adds"),
    ("extra unflag in the truck env", T, "build_unflags", add("-Wextra"),
     "truck] build_unflags adds"),
    ("an unflag in the witness env", W, "build_unflags", add("-Wextra"),
     "witness] build_unflags adds"),
    ("the truck's unflag missing", T, "build_unflags", lambda v: [],
     "truck] build_unflags adds nothing"),
    ("a different build_src_filter in the truck", T, "build_src_filter",
     lambda v: [v[0] + " -<machine.cpp>"], "build_src_filter"),
    ("a library in the witness env", W, "lib_deps", add("some/lib"),
     "lib_deps"),
    ("a different board in the truck", T, "board",
     lambda v: "esp32-s3-devkitm-1", "board is"),
    ("a different platform in the witness env", W, "platform",
     lambda v: "espressif32", "platform is"),
    ("a different flash mode in the truck", T, "board_build.flash_mode",
     lambda v: "dio", "board_build.flash_mode"),
    ("an extra script in the truck", T, "extra_scripts",
     add("post:extra.py"), "extra_scripts"),
)
for what, env, opt, fn, expect in cases:
    p = links(mutated(real, env, opt, fn))
    check(any(expect in q for q in p),
          "%s: fails, naming it (%s)" % (what, p[:1] or "no problem"))

p = links(mutated(real, T, "monitor_speed", lambda v: 921600))
check(p == [], "a monitor setting is not compared, as it never reaches "
      "the image (%s)" % p)
c = copy.deepcopy(real)
del c[W]
p = links(c)
check(any("not in the resolved config" in q for q in p),
      "a missing witness env is reported, not skipped (%s)" % p[:1])


# -- 2. end to end through `platformio project config` ----------------
def ini_variant(edit):
    """-> (problems, resolved) for a copy of platformio.ini after edit()."""
    with open(os.path.join(BC.FW, "platformio.ini"), encoding="utf-8") as f:
        text = f.read()
    new = edit(text)
    root = scratch_dir.make("build-check-links-test")
    try:
        with open(os.path.join(root, "platformio.ini"), "w",
                  encoding="utf-8") as f:
            f.write(new)
        cfg, err = BC.resolved_config(root)
        if err:
            return [err], None
        return BC.check_build_links(cfg=cfg), cfg
    finally:
        scratch_dir.remove(root)


def after_line(marker, line):
    def edit(text):
        assert text.count(marker) == 1, marker
        return text.replace(marker, marker + "\n" + line, 1)
    return edit


p, cfg = ini_variant(lambda t: t)
check(p == [], "end to end: an unmodified copy passes (%s)" % p)

ini_cases = (
    ("[env:esp32-can-x2-witness]", W,
     after_line("    -DINTP_ORDER_WITNESS=1", "    " + EXTRA),
     ["witness] build_flags adds"]),
    ("[env:truck]", T,
     after_line("    -DINTP_NO_SERIAL=1", "    " + EXTRA),
     ["truck] build_flags adds"]),
    ("[env:esp32-can-x2]", B,
     after_line("[env:esp32-can-x2]\nextends = common",
                "build_flags = ${common.build_flags} " + EXTRA),
     ["witness] build_flags drops", "truck] build_flags drops"]),
)
for name, env, edit, expects in ini_cases:
    p, cfg = ini_variant(edit)
    applied = bool(cfg) and EXTRA in BC._flag_tokens(cfg[env].get(
        "build_flags"))
    check(applied, "end to end: the extra flag reached %s's resolved "
          "flags (the mutation applied)" % name)
    check(applied and all(any(e in q for q in p) for e in expects),
          "end to end: one extra flag in %s fails every link it is on (%s)"
          % (name, p))

print()
print("build_check_links_test: %s" % (
    "all checks OK" if not failures else "%d failure(s)" % failures))
sys.exit(1 if failures else 0)
