#!/usr/bin/env python3
"""Test of the "no OTA fault injection in the image" row in build_output.py, and of
elf_symbols_beside(), the nm reader it shares with the restart-test symbol row.

Until port step 1a2 the row looked for the fault branch's log string "FAULT INJECT:
suppressing report of" in the image. At release log levels (shared/tools R28) the string
is not compiled in even with OTA_HEALTH_FAULT_INJECT on, so the row passed on a fault
build at none (gen-inhibit reviewer, 2026-10-30, ruling 4). It now asks whether the ELF
beside the image defines the hook's own function, build_output.FAULT_HOOK_SYM (shared/wican
W7). The real builds' fail-on/pass-off is shown on the tester's R28 builds; this test covers
the row's logic with synthetic images (no format string in any of them) and a fake nm that
lists the symbols written in the fake ELF beside each:
  - the hook defined: FAIL, though the image has no string (the case the old row passed);
  - other symbols only, or a longer name that starts with the hook's: ok;
  - no ELF, no nm, nm exiting non-zero (whatever it listed), nm listing nothing: NO REF,
    never a pass;
  - the restart-test row, through the same reader: a restart symbol FAILS, no ELF and an
    empty listing are NO REF.

    py -3.14 -B gen-inhibit/test/build_checks/test_fault_hook_row.py [--mutants]

--mutants runs the unmutated copy of build_output.py first, as the control, then each
mutant; each must fail a check here (a crash is not a kill). Gen-inhibit tester, 2026-10-30.
"""
import importlib.util
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
WORKTREE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCRATCH = os.path.join(WORKTREE, "scratch", "build-checks", "test_fault_hook_row")
ROW = "no OTA fault injection in the image"
RT_ROW = "no restart test symbols in the ELF"
HOOK = "ota_health_fault_inject_suppress"
FAILS = []

FAKE_NM_PY = r'''import sys
words = open(sys.argv[-1], encoding="ascii").read().split()
code = 0
if words[:1] == ["EXIT"]:
    code, words = int(words[1]), words[2:]
for s in words:
    print("00000000 T %s" % s)
sys.exit(code)
'''


def make_fake_nm(folder):
    """A fake nm in `folder`: it prints "00000000 T <name>" for each word of the ELF file it is
    given; a file starting "EXIT <n>" makes it print the words after and exit <n>. Returns the
    command to run."""
    os.makedirs(folder, exist_ok=True)
    py = os.path.join(folder, "fake_nm.py")
    with open(py, "w", encoding="ascii") as fh:
        fh.write(FAKE_NM_PY)
    if os.name == "nt":
        exe = os.path.join(folder, "fake_nm.bat")
        with open(exe, "w", encoding="ascii", newline="\r\n") as fh:
            fh.write('@"%s" -B "%s" %%*\n' % (sys.executable, py))
    else:
        exe = os.path.join(folder, "fake_nm")
        with open(exe, "w", encoding="ascii") as fh:
            fh.write("#!/bin/sh\nexec \"%s\" -B \"%s\" \"$@\"\n" % (sys.executable, py))
        os.chmod(exe, 0o755)
    return exe


def use_nm(exe):
    """Make build_output's elf_symbols_beside() find `exe` (None: no nm at all)."""
    import elf_checks
    elf_checks.find_nm = lambda: exe


def image(project="wican-fw_obd_man"):
    desc = bytearray(256)
    struct.pack_into("<I", desc, 0, 0xABCD5432)
    desc[0x10:0x10 + 7] = b"a71b90a"
    desc[0x30:0x30 + len(project)] = project.encode("ascii")
    return bytes(0x20) + bytes(desc) + b"\x00body\xff" * 64


def write_pair(folder, elf_words):
    """An image (no fault string) and, unless elf_words is None, a fake ELF beside it."""
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    img = os.path.join(folder, "wican-fw_obd_test.bin")
    with open(img, "wb") as fh:
        fh.write(image())
    if elf_words is not None:
        with open(os.path.join(folder, "wican-fw_obd_test.elf"), "w", encoding="ascii") as fh:
            fh.write("\n".join(elf_words) + "\n")
    return img


def verdict(B, rowname):
    hits = [r for r in B.rows if r[0] == rowname]
    if len(hits) != 1:
        return "rows: %r" % [r[0] for r in B.rows]
    _n, ok, _d, ref_missing = hits[0]
    return "NO REF" if ok is None or ref_missing else ("ok" if ok else "FAIL")


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def checks(B, nm):
    pairs = os.path.join(SCRATCH, "pair")
    cases = [
        ("the hook defined, no string in the image", [HOOK, "app_main"], nm, "FAIL"),
        ("other symbols only", ["app_main", "ota_health_report"], nm, "ok"),
        ("a longer name starting with the hook's", [HOOK + "_x", "app_main"], nm, "ok"),
        ("no ELF beside the image", None, nm, "NO REF"),
        ("no nm found", [HOOK], None, "NO REF"),
        ("nm exits 1 after listing symbols", ["EXIT", "1", "app_main"], nm, "NO REF"),
        ("nm lists nothing", [], nm, "NO REF"),
    ]
    for label, words, exe, want in cases:
        img = write_pair(pairs, words)
        use_nm(exe)
        del B.rows[:]
        B.check_not_rollback_rehearsal(img)
        got = verdict(B, ROW)
        check("fault row, %s: %s" % (label, want), got == want, got)
    for label, words, want in (("a restart symbol defined", ["gi_restart_test_register", "app_main"], "FAIL"),
                               ("no restart symbol", ["app_main"], "ok"),
                               ("no ELF beside the image", None, "NO REF"),
                               ("nm lists nothing", [], "NO REF")):
        img = write_pair(pairs, words)
        use_nm(nm)
        del B.rows[:]
        B.check_no_restart_test(img)
        got = verdict(B, RT_ROW)
        check("restart row, %s: %s" % (label, want), got == want, got)
    check("the hook's name is the shared/wican W7 function", B.FAULT_HOOK_SYM == HOOK, B.FAULT_HOOK_SYM)


def load(target):
    spec = importlib.util.spec_from_file_location("build_output_under_test", target)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MUTANTS = [
    ("the hook not looked for", "        hit = FAULT_HOOK_SYM in defined\n", "        hit = False\n"),
    ("a name starting with the hook's counts", "        hit = FAULT_HOOK_SYM in defined\n",
     "        hit = any(s.startswith(FAULT_HOOK_SYM) for s in defined)\n"),
    ("the old format-string row is back", "        hit = FAULT_HOOK_SYM in defined\n",
     '        hit = b"FAULT INJECT: suppressing report of" in open(path, "rb").read()\n'),
    ("a shorter hook name", 'FAULT_HOOK_SYM = "ota_health_fault_inject_suppress"',
     'FAULT_HOOK_SYM = "ota_health_fault_inject"'),
    ("no reference passes the fault row", "        row(name, None, why, ref_missing=True)\n",
     "        row(name, True, why)\n"),
    ("an empty nm listing is a reference", "    if not defined:\n        return None,", "    if False:\n        return None,"),
    ("nm's exit status ignored", "    if r.returncode != 0:\n        return None,", "    if False:\n        return None,"),
    ("no ELF read as no symbols", '        return None, "no ELF beside the image, so the symbol check has no reference"',
     "        return set([\"_start\"]), None"),
    ("no reference passes the restart row", '        row("no restart test symbols in the ELF", None, why, ref_missing=True)',
     '        row("no restart test symbols in the ELF", True, why)'),
]


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    nm = make_fake_nm(os.path.join(SCRATCH, "nm"))
    if "--target" in sys.argv[1:]:
        checks(load(sys.argv[sys.argv.index("--target") + 1]), nm)
        print("%s: %d failure(s)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
        return 1 if FAILS else 0
    if "--mutants" not in sys.argv[1:]:
        try:
            checks(load(os.path.join(HERE, "build_output.py")), nm)
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)
        print("%s: %d failure(s)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
        return 1 if FAILS else 0
    src = open(os.path.join(HERE, "build_output.py"), encoding="utf-8", newline="").read()
    base = os.path.join(WORKTREE, "scratch", "build-checks", "fault_hook_row_mutants")
    shutil.rmtree(base, ignore_errors=True)
    killed = errors = 0
    for n, (label, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, label, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        t = os.path.join(d, "build_output.py")
        open(t, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--target", t],
                           capture_output=True, text=True, timeout=300)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", label, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s%s" % ("KILLED  " if dead else ("CRASHED " if crashed else "SURVIVED"), n, label,
                               "  (%s)" % fails[0][6:100] if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
