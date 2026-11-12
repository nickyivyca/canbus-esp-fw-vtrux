#!/usr/bin/env python3
"""Harness test for gi_esptool.py: esptool's argv from GI_ESPTOOL_ARGV in its one form,
the pinned version read from `<argv> version`, and the pinned version's verify line.

    python3.13 -B gen-inhibit/tools/bench/test_gi_esptool.py [--can-fail]

The version is read from a stand-in esptool package (_fake_esptool.py) run as
`<python> -m esptool`, so no real esptool runs and no port is opened. With --can-fail the
unmutated module runs first, as the control (if a check fails, no mutant is judged and the
run exits 1); then each mutant, a copy of gi_esptool.py with one edit in this run's
scratch folder, must fail a check. A check that raises is a crash, not a kill, and an edit
that does not apply is an error. Gen-inhibit tester, 2026-11-11 (the gen-inhibit
reviewer's esptool ruling of that day).
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders
import _fake_esptool as FE  # noqa: E402

SRC = os.path.join(HERE, "gi_esptool.py")
PY = sys.executable
GOOD = [PY, "-m", "esptool"]

MUTANTS = [
    ("the version is not compared with the pin", "    if v != PINNED:\n", "    if v is None:\n"),
    ("any [python, -m, ...] is taken", 'argv[1:] == ["-m", "esptool"]', 'argv[1] == "-m"'),
    ("an empty python is taken", '    if not (isinstance(argv, list) and argv[1:] == ["-m", "esptool"] and isinstance(argv[0], str)\n'
     '            and argv[0].strip()):', '    if not (isinstance(argv, list) and argv[1:] == ["-m", "esptool"]):'),
    ("an unset GI_ESPTOOL_ARGV falls back to a default",
     'raw = (os.environ if environ is None else environ).get(ENV, "")',
     'raw = (os.environ if environ is None else environ).get(ENV, "") or \'["python3", "-m", "esptool"]\''),
    ("the version is read from the first line",
     'if lines and re.fullmatch(r"\\d+\\.\\d+\\.\\d+", lines[-1]):\n        return lines[-1]',
     'if lines and re.fullmatch(r"\\d+\\.\\d+\\.\\d+", lines[0]):\n        return lines[0]'),
    ("a line holding a version anywhere is taken", 're.fullmatch(r"\\d+\\.\\d+\\.\\d+"', 're.search(r"\\d+\\.\\d+\\.\\d+"'),
    ("esptool's exit is not looked at", "(read_version(p.stdout) if p.returncode == 0 else None)",
     "read_version(p.stdout)"),
    ("esptool 5's verify line", '{"4.13.0": "-- verify OK (digest matched)"}',
     '{"4.13.0": "Verification successful (digest matched)"}'),
]


# every harness file that runs esptool: no dashed command name in a string literal
SCAN_FILES = ("x2_hold.py", "x2_hold_inputs.py", "x2_flash_custody.py", "x2_pins_noreset_check.py",
              os.path.join("..", "suite_gen_inhibit.py"))
DASHED = re.compile(r"""["'](chip-id|read-mem|write-mem|read-flash|write-flash|verify-flash|erase-flash)["']""")


def dashed_in(text):
    return DASHED.findall(text)


def scan():
    """{file: [dashed names]} for every harness file in SCAN_FILES that has one."""
    out = {}
    for f in SCAN_FILES:
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            got = dashed_in(fh.read())
        if got:
            out[f] = got
    return out


def load(path):
    spec = importlib.util.spec_from_file_location("gi_esptool_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def refused(GE, fn, *args):
    """The Refused message, or None if fn returned or raised anything else (an error is not a
    refusal: a caller catching Refused would crash on it)."""
    try:
        fn(*args)
    except GE.Refused as e:
        return str(e) or "refused"
    except Exception:
        return None
    return None


def checks(GE, fake_env):
    """[(label, fn)]; each fn returns (ok, got)."""
    def with_fake(version, fn):
        saved = {k: os.environ.get(k) for k in ("PYTHONPATH", "FAKE_ESPTOOL_VERSION")}
        os.environ.update(fake_env, FAKE_ESPTOOL_VERSION=version)
        try:
            return fn()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    v5_out = ("Warning: Deprecated: esptool.py is deprecated\n"
              "Warning: Deprecated: Command 'version' ...\nesptool v5.0.2\n5.0.2\n")
    bad_forms = [[PY, "esptool.py"], [PY, "-m", "esptool", "--chip"], ["", "-m", "esptool"],
                 [PY, "-m", "esptool.py"], " ".join(GOOD), None, [PY, "-m"], [], [None, "-m", "esptool"]]
    c = [
        ("the pin is 4.13.0, and its verify line is 4.13.0's",
         lambda: (GE.PINNED == "4.13.0" and GE.VERIFY_OK == "-- verify OK (digest matched)",
                  (GE.PINNED, GE.VERIFY_OK))),
        ("form: [<python>, -m, esptool] is taken as it is",
         lambda: (GE.check_form(list(GOOD)) == GOOD, GOOD)),
        ("form: every other form refuses (a script, more args, an empty python, -m esptool.py, "
         "a string, None, too short, empty, a python that is not a string)",
         lambda: (all(refused(GE, GE.check_form, f) for f in bad_forms),
                  [f for f in bad_forms if not refused(GE, GE.check_form, f)])),
        ("env: GI_ESPTOOL_ARGV unset or blank refuses, naming it",
         lambda: (all(GE.ENV in (refused(GE, GE.from_env, e) or "") for e in ({}, {GE.ENV: " "})),
                  [refused(GE, GE.from_env, e) for e in ({}, {GE.ENV: " "})])),
        ("env: not JSON refuses; JSON of another form refuses",
         lambda: (bool(refused(GE, GE.from_env, {GE.ENV: "python -m esptool"})) and
                  bool(refused(GE, GE.from_env, {GE.ENV: json.dumps([PY, "esptool.py"])})), None)),
        ("env: the JSON argv is read", lambda: (GE.from_env({GE.ENV: json.dumps(GOOD)}) == GOOD, None)),
        ("read_version: 4.13.0's output (banner, then the version)",
         lambda: (GE.read_version("esptool.py v4.13.0\n4.13.0\n") == "4.13.0", None)),
        ("read_version: v5's warning lines first",
         lambda: (GE.read_version(v5_out) == "5.0.2", GE.read_version(v5_out))),
        ("read_version: the banner alone, a version with more on its line, or nothing is no version",
         lambda: ([GE.read_version(t) for t in ("esptool.py v4.13.0\n", "4.13.0 extra\n", "")]
                  == [None, None, None],
                  [GE.read_version(t) for t in ("esptool.py v4.13.0\n", "4.13.0 extra\n", "")])),
        ("version: read from `<python> -m esptool version` (the stand-in, 4.13.0)",
         lambda: with_fake("4.13.0", lambda: (GE.version(GOOD)[0] == "4.13.0", GE.version(GOOD)))),
        ("version: an esptool that exits non-zero has no version, whatever it printed",
         lambda: (GE.version([PY, "-c", "print('4.13.0'); raise SystemExit(1)"])[0] is None, None)),
        ("check_version: the pinned version passes",
         lambda: with_fake("4.13.0", lambda: (GE.check_version(GOOD) == "4.13.0", None))),
        ("check_version: 5.0.2 refuses, naming it",
         lambda: with_fake("5.0.2", lambda: ("5.0.2" in (refused(GE, GE.check_version, GOOD) or ""),
                                             refused(GE, GE.check_version, GOOD)))),
        ("check_version: an esptool that will not run refuses",
         lambda: (bool(refused(GE, GE.check_version, [PY, "-c", "raise SystemExit(3)"])), None)),
        ("checked: argv and version for the record",
         lambda: with_fake("4.13.0", lambda: (GE.checked({GE.ENV: json.dumps(GOOD)}) ==
                                              {"argv": GOOD, "version": "4.13.0"}, None))),
        ("checked: another version, or no GI_ESPTOOL_ARGV, refuses",
         lambda: with_fake("4.12.0", lambda: (bool(refused(GE, GE.checked, {GE.ENV: json.dumps(GOOD)}))
                                              and bool(refused(GE, GE.checked, {})), None))),
        ("scan: a dashed command name in a string is found, an underscore one is not",
         lambda: (dashed_in('args = ["write-flash"]; x = \'read-mem\'') == ["write-flash", "read-mem"]
                  and dashed_in('args = ["write_flash", "read_mem", "chip_id"]') == [], None)),
        ("scan: no harness file that runs esptool has a dashed command name (%s)" % ", ".join(SCAN_FILES),
         lambda: (scan() == {}, scan())),
    ]
    return c


def run_checks(GE, fake_env, show):
    """(failed labels, crashed labels)."""
    failed, crashed = [], []
    for label, fn in checks(GE, fake_env):
        try:
            ok, got = fn()
        except Exception as e:      # a crash is reported, never passed
            ok, got = None, "%s: %s" % (type(e).__name__, e)
        if show:
            print("  %-5s %s%s" % ({True: "ok", False: "FAIL", None: "CRASH"}[ok], label,
                                   "" if ok else "  (%s)" % (got,)))
        if ok is False:
            failed.append(label)
        elif ok is None:
            crashed.append(label)
    return failed, crashed


def main(argv):
    can_fail = "--can-fail" in argv
    work = gi_paths.scratch("gi_esptool")
    fake_env = FE.make(os.path.join(work, "fake"))
    if not can_fail:
        failed, crashed = run_checks(load(SRC), fake_env, True)
        bad = failed + crashed
        print("\n%s" % ("PASS" if not bad else "FAIL: %d" % len(bad)))
        return 1 if bad else 0
    print("-- control: the unmutated module")
    failed, crashed = run_checks(load(SRC), fake_env, True)
    if failed or crashed:
        print("\nREFUSED: the control fails, so no mutant is judged")
        return 1
    print("-- mutants")
    src = open(SRC, encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(work, "m%d" % n)
        os.makedirs(d)
        path = os.path.join(d, "gi_esptool.py")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new))
        try:
            failed, crashed = run_checks(load(path), fake_env, False)
        except Exception as e:
            failed, crashed = [], ["import: %s: %s" % (type(e).__name__, e)]
        dead = bool(failed)
        killed += dead
        print("%s m%d %s  (%d FAIL, %d CRASH%s)" % ("KILLED  " if dead else "SURVIVED", n, name, len(failed),
                                                   len(crashed), ("; " + failed[0][:70]) if failed else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
