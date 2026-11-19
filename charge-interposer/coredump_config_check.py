"""Build check of spec 9's core-dump settings in every variant (spec 9,
"Settings the X2 board leaves to this project", core dumps; user,
2026-11-18). Implementor's check of its own build; not evidence.

    python3.13 -B charge-interposer/coredump_config_check.py
    python3.13 -B charge-interposer/coredump_config_check.py --can-fail

From this repo's root, with the Python command local-paths.md names, after
a dev build of every variant (`vtrux_tools.py build <variant>`, shared/tools
R31). It needs no ESP-IDF environment.

Checked, each failure named:

- the committed charge-interposer/firmware/sdkconfig has
  CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y, and CONFIG_ESP_COREDUMP_CHECK_BOOT
  and CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE not set;
- for every variant firmware/vtrux_build.json lists, the build folder
  firmware/build/<variant>/ exists and was configured as that variant
  (CMakeCache.txt's INTP_VARIANT), and the configuration it compiled with,
  its generated config/sdkconfig.json, has the same three: ENABLE_TO_FLASH
  true, CHECK_BOOT and FLASH_NO_OVERWRITE false or absent (Kconfig writes
  an option whose dependencies are unmet as absent);
- the same variant's link map, interposer_<variant>.map, has main's
  boot_partitions.c in ESP-IDF's init-function array at priority 150
  (input section .esp_system_init_fn.150 from libmain.a's
  boot_partitions.c.obj), so the core-stage partition-table read is in the
  image and not dropped by the linker.

Only the app's build folder is read: the bootloader's configuration has no
core-dump options.

Exit 0: all pass. 1: a check failed. 2: the check could not run (the
project's files unreadable).

--can-fail runs the check on copies of the real files, each mutant a
premise-checked edit breaking one rule (CHECK_BOOT on in the committed
sdkconfig, or in one variant's generated configuration, for every variant;
FLASH_NO_OVERWRITE on; core dumps not to flash; a build folder missing or
configured as another variant; the init function missing from a link map),
and on unchanged copies as the control. It passes when the control passes
and every mutant fails. Copies are hard links where a file is unchanged,
in <worktree>/scratch/charge-interposer-coredump-check/<pid>/, removed when
the run ends, pass or fail (AGENTS.md "Removing scratch"); a folder that
cannot be removed is printed with the reason.
"""

import atexit
import json
import os
import re
import shutil
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.join(HERE, "firmware")
SCRATCH = os.path.join(os.path.dirname(HERE), "scratch",
                       "charge-interposer-coredump-check")
RUN_DIR = os.path.join(SCRATCH, str(os.getpid()))

UNSET = "unset"
SPEC = "spec 9, 'Settings the X2 board leaves to this project', core dumps"
# (option, wanted): True, or UNSET (not set, false or absent).
SETTINGS = [
    ("CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH", True),
    ("CONFIG_ESP_COREDUMP_CHECK_BOOT", UNSET),
    ("CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE", UNSET),
]
ABSENT = "absent"
_SET = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.*)$")
_NOT_SET = re.compile(r"^# (CONFIG_[A-Z0-9_]+) is not set$")
_VARIANT = re.compile(r"^INTP_VARIANT:[A-Z]+=(.*)$", re.M)
# GNU ld's map: the input section, then its address, size and object, on
# the same line or the next.
_INIT_FN = re.compile(r"^ \.esp_system_init_fn\.150\s+0x[0-9a-f]+\s+0x[0-9a-f]+"
                      r"\s+\S*libmain\.a\(boot_partitions\.c\.obj\)$", re.M)


class CannotCheck(Exception):
    pass


def read_text(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise CannotCheck("%s %s cannot be read (%s)" % (what, path, e))


def committed_options(text):
    """{CONFIG_NAME: True | False | the raw value} from sdkconfig text:
    'X=y' is True, '# X is not set' is False."""
    out = {}
    for line in text.splitlines():
        m = _SET.match(line)
        if m:
            out[m.group(1)] = True if m.group(2) == "y" else m.group(2)
            continue
        m = _NOT_SET.match(line)
        if m:
            out[m.group(1)] = False
    return out


def meets(want, got):
    if want == UNSET:
        return got is False or got == ABSENT
    return got is True


def show(v):
    return v if v == ABSENT else json.dumps(v)


def judge(where, options, key=lambda name: name):
    """[failure] for each SETTINGS option not at its value in options."""
    bad = []
    for name, want in SETTINGS:
        got = options.get(key(name), ABSENT)
        if not meets(want, got):
            bad.append("%s: %s is %s, %s wants %s" % (
                where, name, show(got), SPEC,
                "not set" if want == UNSET else "set (y)"))
    return bad


def variants(firmware):
    try:
        data = json.loads(read_text(os.path.join(firmware, "vtrux_build.json"),
                                    "the variant list"))
        names = list(data["variants"])
    except (ValueError, KeyError, TypeError) as e:
        raise CannotCheck("vtrux_build.json has no variants (%s: %s)"
                          % (type(e).__name__, e))
    if not names:
        raise CannotCheck("vtrux_build.json lists no variants")
    return names


def check_variant(firmware, v):
    """([failure], [line seen]) for one variant's build folder."""
    bd = os.path.join(firmware, "build", v)
    where = "build/%s" % v
    if not os.path.isdir(bd):
        return ["%s: no build folder; build it with vtrux_tools.py build %s"
                % (where, v)], []
    bad, seen = [], []
    try:
        cache = read_text(os.path.join(bd, "CMakeCache.txt"), where)
        m = _VARIANT.search(cache)
        if not m or m.group(1).strip() != v:
            bad.append("%s: configured as INTP_VARIANT %s, not %s" % (
                where, m.group(1).strip() if m else "(none)", v))
    except CannotCheck as e:
        bad.append(str(e))
    p = os.path.join(bd, "config", "sdkconfig.json")
    try:
        opts = json.loads(read_text(p, where))
        if not isinstance(opts, dict):
            raise ValueError("not an object")
    except (CannotCheck, ValueError) as e:
        bad.append("%s: config/sdkconfig.json: %s" % (where, e))
    else:
        bad += judge(where + " config/sdkconfig.json", opts,
                     key=lambda name: name[len("CONFIG_"):])
        seen.append("%s config/sdkconfig.json: %s" % (where, ", ".join(
            "%s %s" % (n[len("CONFIG_ESP_COREDUMP_"):],
                       show(opts.get(n[len("CONFIG_"):], ABSENT)))
            for n, _ in SETTINGS)))
    mp = os.path.join(bd, "interposer_%s.map" % v)
    try:
        if _INIT_FN.search(read_text(mp, where)):
            seen.append("%s interposer_%s.map: boot_partitions.c in "
                        ".esp_system_init_fn.150" % (where, v))
        else:
            bad.append("%s interposer_%s.map: no .esp_system_init_fn.150 from "
                       "libmain.a(boot_partitions.c.obj): the core-stage "
                       "partition-table read is not in the image (%s)"
                       % (where, v, SPEC))
    except CannotCheck as e:
        bad.append(str(e))
    return bad, seen


def check(firmware):
    """(exit code, report lines)."""
    try:
        names = variants(firmware)
        committed = committed_options(read_text(
            os.path.join(firmware, "sdkconfig"), "the committed sdkconfig"))
    except CannotCheck as e:
        return 2, ["NOT CHECKED: %s" % e]
    bad = judge("sdkconfig (committed)", committed)
    lines = ["sdkconfig (committed): %s" % ", ".join(
        "%s %s" % (n[len("CONFIG_ESP_COREDUMP_"):], show(committed.get(n, ABSENT)))
        for n, _ in SETTINGS)]
    for v in names:
        b, seen = check_variant(firmware, v)
        bad += b
        lines += seen
    if bad:
        lines += ["FAIL: %d" % len(bad)] + ["    " + b for b in bad]
        return 1, lines
    lines.append("PASS: %d variant(s) (%s), %s" % (len(names), ", ".join(names),
                                                  SPEC))
    return 0, lines


# ---------- --can-fail ----------

def _long(path):
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def _clear_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def remove_tree(path):
    """Remove path, read-only files included, retrying after short waits.
    Returns "" or why it is still there."""
    why = ""
    for wait in (0, 0.1, 0.4):
        time.sleep(wait)
        if not os.path.exists(_long(path)):
            return ""
        try:
            shutil.rmtree(_long(path), onerror=_clear_readonly)
        except OSError as e:
            why = "%s: %s" % (type(e).__name__, e)
    return "" if not os.path.exists(_long(path)) else (why or "still there")


def _cleanup():
    for d in (RUN_DIR, SCRATCH):
        if d == SCRATCH and os.path.isdir(d) and os.listdir(d):
            continue  # another run's folder is in it
        why = remove_tree(d)
        if why:
            print("LEFTOVER %s (%s)" % (d, why))


def _files(firmware, names):
    """The files the check reads, relative to firmware/."""
    out = ["sdkconfig", "vtrux_build.json"]
    for v in names:
        out += ["build/%s/CMakeCache.txt" % v,
                "build/%s/config/sdkconfig.json" % v,
                "build/%s/interposer_%s.map" % (v, v)]
    return out


def mutants(names):
    """(name, file relative to firmware/ or None, old text, new text). A
    None new text removes the build folder named by the file's folder."""
    out = [
        ("committed: CHECK_BOOT set", "sdkconfig",
         "# CONFIG_ESP_COREDUMP_CHECK_BOOT is not set\n",
         "CONFIG_ESP_COREDUMP_CHECK_BOOT=y\n"),
        ("committed: FLASH_NO_OVERWRITE set", "sdkconfig",
         "# CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE is not set\n",
         "CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE=y\n"),
        ("committed: core dumps not to flash", "sdkconfig",
         "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y\n",
         "# CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH is not set\n"),
    ]
    for v in names:
        out.append(("%s: CHECK_BOOT on" % v,
                    "build/%s/config/sdkconfig.json" % v,
                    '"ESP_COREDUMP_CHECK_BOOT": false,',
                    '"ESP_COREDUMP_CHECK_BOOT": true,'))
    v = names[0]
    out += [
        ("%s: FLASH_NO_OVERWRITE on" % v, "build/%s/config/sdkconfig.json" % v,
         '"ESP_COREDUMP_FLASH_NO_OVERWRITE": false,',
         '"ESP_COREDUMP_FLASH_NO_OVERWRITE": true,'),
        ("%s: core dumps not to flash" % v, "build/%s/config/sdkconfig.json" % v,
         '"ESP_COREDUMP_ENABLE_TO_FLASH": true,',
         '"ESP_COREDUMP_ENABLE_TO_FLASH": false,'),
        ("%s: configured as another variant" % names[-1],
         "build/%s/CMakeCache.txt" % names[-1],
         "INTP_VARIANT:STRING=%s\n" % names[-1],
         "INTP_VARIANT:STRING=%s\n" % names[0]),
        ("%s: no build folder" % names[-1],
         "build/%s/CMakeCache.txt" % names[-1], "", None),
        ("%s: core-stage read not linked" % v,
         "build/%s/interposer_%s.map" % (v, v),
         " .esp_system_init_fn.150", " .esp_system_init_fn.151"),
    ]
    return out


def can_fail():
    atexit.register(_cleanup)
    try:
        names = variants(FIRMWARE)
    except CannotCheck as e:
        print("can-fail: NOT RUN: %s" % e)
        return 2
    files = _files(FIRMWARE, names)
    for rel in files:
        if not os.path.isfile(os.path.join(FIRMWARE, rel)):
            print("can-fail: NOT RUN: %s is missing; build every variant first"
                  % rel)
            return 2
    results, bad = [], False
    for i, (name, rel, old, new) in enumerate(
            [("control", None, "", "")] + mutants(names)):
        d = os.path.join(RUN_DIR, "m%02d" % i)
        text = None
        if rel is not None and new is not None:
            with open(os.path.join(FIRMWARE, rel), encoding="utf-8",
                      newline="") as f:
                text = f.read()
            if text.count(old) != 1:
                results.append((name, "NOT MADE: the text is in %s %d times, "
                                      "not once" % (rel, text.count(old))))
                bad = True
                continue
        for r in files:
            if new is None and r.startswith(os.path.dirname(rel) + "/"):
                continue
            dst = os.path.join(d, r)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if r == rel:
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write(text.replace(old, new))
                continue
            try:
                os.link(os.path.join(FIRMWARE, r), dst)
            except OSError:
                shutil.copyfile(os.path.join(FIRMWARE, r), dst)
        code, lines = check(d)
        if name == "control":
            ok = code == 0
            results.append((name, "passes" if ok else
                            "FAILS: " + " | ".join(lines[-4:])))
        else:
            ok = code == 1
            first = next((l.strip() for l in lines if l.startswith("    ")),
                         lines[-1] if lines else "")
            results.append((name, ("caught (%s)" % first) if ok else
                            "MISSED: exit %d" % code))
        bad = bad or not ok
        why = remove_tree(d)
        if why:
            print("LEFTOVER %s (%s)" % (d, why))
    for name, what in results:
        print("%-40s %s" % (name, what))
    n = len(results) - 1
    print("can-fail: %s" % ("FAIL" if bad else
                            "PASS, the control passes and all %d mutants fail"
                            % n))
    return 1 if bad else 0


def main(argv):
    if argv == ["--can-fail"]:
        return can_fail()
    if argv:
        print(__doc__)
        return 2
    code, lines = check(FIRMWARE)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
