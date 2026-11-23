#!/usr/bin/env python3
"""Tests build_output.py's "Bluetooth off" row (gen-inhibit spec 12.3 item 3, setting 7,
and 9.4: CONFIG_BT_ENABLED unset). Gen-inhibit tester, 2026-11-23, stage (b) batch 1.

It also checks that the release profile in firmware/vtrux_checks.json gives
CONFIG_BT_ENABLED as "unset", and that the committed firmware/sdkconfig has it unset.

Cases on check_config(), with configs as build_output reads them (sdkconfig.json's
types, and the sdkconfig text's), plus a real sdkconfig text parsed by
load_sdkconfig's fallback:
  - absent, or false (sdkconfig.json's unset): PASS
  - true (=y): FAIL, the row naming the value
  - "n" (a hand-edited value Kconfig would not write): FAIL
  - an untrusted config: NO REF, never a pass
Mutants (--mutants): copies of build_output.py with the row inverted, the row
dropped, or one that passes every value. Each must make a case fail.

    python3.13 -B test_bt_row.py [--mutants]
Writes only under the worktree's scratch/build-checks/test_bt_row/, removed at the end.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
WORKTREE = os.path.dirname(PROJECT)
FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def load(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_output_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bt_rows(mod, cfg, trusted=True):
    mod.rows.clear()
    mod.check_config(cfg, trusted, "planted: another build's config")
    return [r for r in mod.rows if r[0] == "Bluetooth off"]


def cases(target):
    mod = load(target)
    base = {"BOOTLOADER_APP_ROLLBACK_ENABLE": True}
    for label, extra, want in [("absent", {}, True), ("false (sdkconfig.json unset)", {"BT_ENABLED": False}, True),
                               ("y", {"BT_ENABLED": True}, False), ("n", {"BT_ENABLED": "n"}, False)]:
        cfg = dict(base, **extra)
        rs = bt_rows(mod, cfg)
        check("%s: one Bluetooth row" % label, len(rs) == 1, rs)
        if len(rs) == 1:
            check("%s: %s" % (label, "PASS" if want else "FAIL"), rs[0][1] is want and not rs[0][3], rs[0])
            if not want:
                check("%s: the row names the value" % label, "ON" in rs[0][2], rs[0][2])
    rs = bt_rows(mod, dict(base, BT_ENABLED=True), trusted=False)
    check("untrusted: NO REF, not a pass", len(rs) == 1 and rs[0][1] is None and rs[0][3], rs)
    # The sdkconfig text form, through load_sdkconfig's fallback (no build dir with sdkconfig.json).
    work = os.path.join(WORKTREE, "scratch", "build-checks", "test_bt_row", str(os.getpid()))
    os.makedirs(work, exist_ok=True)
    try:
        for label, line, want in [("text unset", "# CONFIG_BT_ENABLED is not set", True),
                                  ("text y", "CONFIG_BT_ENABLED=y", False)]:
            with open(os.path.join(work, "sdkconfig"), "w", newline="\n") as f:
                f.write("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\n%s\n" % line)
            saved = mod.REPO
            mod.REPO = work
            try:
                cfg, _ = mod.load_sdkconfig(os.path.join(work, "no-build"))
            finally:
                mod.REPO = saved
            rs = bt_rows(mod, cfg)
            check("%s: %s" % (label, "PASS" if want else "FAIL"), len(rs) == 1 and rs[0][1] is want, rs)
    finally:
        shutil.rmtree(work)


def repo_facts():
    with open(os.path.join(PROJECT, "firmware", "vtrux_checks.json"), encoding="utf-8") as f:
        prof = json.load(f)["release_profile"]
    e = prof.get("CONFIG_BT_ENABLED", {})
    check("release profile: CONFIG_BT_ENABLED is \"unset\"", e.get("value") == "unset", e)
    check("release profile: cites 12.3 item 3 and 9.4", "12.3 item 3" in e.get("spec", "") and "9.4" in e.get("spec", ""))
    with open(os.path.join(PROJECT, "firmware", "sdkconfig"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    check("committed sdkconfig: CONFIG_BT_ENABLED unset",
          "# CONFIG_BT_ENABLED is not set" in lines and not any(l.startswith("CONFIG_BT_ENABLED=") for l in lines))


MUTANTS = [
    ("inverted", 'crow("Bluetooth off", bt is None or bt is False,', 'crow("Bluetooth off", not (bt is None or bt is False),'),
    ("passes all", 'crow("Bluetooth off", bt is None or bt is False,', 'crow("Bluetooth off", True,'),
    ("takes n as off", 'crow("Bluetooth off", bt is None or bt is False,', 'crow("Bluetooth off", bt in (None, False, "n"),'),
    ("row dropped", 'crow("Bluetooth off",', 'crow("Bluetooth (dropped)",'),
]


def mutants():
    src = os.path.join(HERE, "build_output.py")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    work = os.path.join(WORKTREE, "scratch", "build-checks", "test_bt_row", "m%d" % os.getpid())
    os.makedirs(work, exist_ok=True)
    killed = 0
    try:
        ctl = subprocess.run([sys.executable, "-B", __file__, "--target", src], capture_output=True, text=True)
        check("control: the unmutated row passes", ctl.returncode == 0, ctl.stdout[-300:])
        for name, old, new in MUTANTS:
            if text.count(old) != 1:
                check("mutant %s: text found once" % name, False, text.count(old))
                continue
            p = os.path.join(work, "build_output_%s.py" % name.replace(" ", "_"))
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text.replace(old, new))
            r = subprocess.run([sys.executable, "-B", __file__, "--target", p], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            # killed by a check, not by a crash: a traceback is an ERROR, never a kill
            dead = r.returncode == 1 and "\nFAIL  " in "\n" + r.stdout and "Traceback" not in r.stderr
            if "Traceback" in r.stderr:
                check("mutant %s: ran without a crash" % name, False, r.stderr[-200:])
            killed += dead
            check("mutant %s: killed" % name, dead, r.stdout[-200:])
    finally:
        shutil.rmtree(work)
    print("%d of %d mutants killed" % (killed, len(MUTANTS)))


def main():
    args = sys.argv[1:]
    if "--target" in args:
        target = args[args.index("--target") + 1]
        sys.path.insert(0, HERE)
        cases(target)
    elif "--mutants" in args:
        mutants()
    else:
        sys.path.insert(0, HERE)
        cases(os.path.join(HERE, "build_output.py"))
        repo_facts()
    print("%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
