#!/usr/bin/env python3
"""Test ota_rollback_rehearsal's image phase: image_facts and judge_images (2026-10-25).

Port step 0c took the commit out of project_name (wican-fw_obd_man, wican-fw_obd_man_flt).
The row "same commit in both names" read it from there, so both names gave "man" and the row
passed on any two images. The row is now "same commit in both versions": the app
descriptors' version fields (shared/tools R4), equal and not empty. Fake images, a valid
descriptor each (image_elf_sha.read_desc), in this worktree's scratch/, with a made-up
GI_CHAIN_SETTINGS (the module reads the WiCAN's identity when imported):
  - the good pair (same version, _flt only on the faulty, the fault string and the fault
    hook only in it): every row passes, before step 0c's names and after;
  - two versions, with post-0c names: the commit row fails, and only it;
  - no version in either: the commit row fails (empty is not a match);
  - a file with no descriptor: no version, the commit row fails.
Since port step 1a2 (gen-inhibit reviewer, 2026-10-30, ruling 4) the images are also judged
by the fault hook, ota_health_fault_inject_suppress (shared/wican W7), in the ELF beside each,
read through build_output.elf_symbols_beside() with test_fault_hook_row.py's fake nm (it
lists the words of the fake ELF):
  - the good ELF defines the hook and the good image has no string (a fault build at release
    log levels, which the old "good has NO fault string" row passed): only the good hook row
    fails;
  - the faulty ELF without the hook: only the faulty hook row fails;
  - no ELF beside the good, or beside the faulty: that image's hook row fails (fail closed).

--mutants edits a copy of ota_rollback_rehearsal.py once per mutant, and this test must fail on
each BY A CHECK: a crash is not a kill, and an edit that does not apply is an error. The
unmutated copy runs first, as the control: if it fails, no mutant is judged and the run exits 1.

    py -3.14 -B gen-inhibit/tools/bench/test_ota_rehearsal_images.py [--mutants]

Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_CHECKS = os.path.normpath(os.path.join(HERE, "..", "..", "test", "build_checks"))
HOOK = "ota_health_fault_inject_suppress"
SCRATCH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "scratch", "ota-rehearsal-images"))
TARGET = "ota_rollback_rehearsal.py"
ROW = "same commit in both versions"
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def fake_image(path, version, name, fault=False, desc=True, hook=None, elf=True):
    """hook: whether the fake ELF beside the image defines the fault hook (default: as fault)."""
    d = bytearray(0x400)
    d[0] = 0xE9
    if desc:
        d[0x20:0x24] = (0xABCD5432).to_bytes(4, "little")
        d[0x30:0x30 + len(version)] = version.encode()
        d[0x50:0x50 + len(name)] = name.encode()
    else:
        d[0x200:0x200 + len(name)] = name.encode()      # a name, but no descriptor
    if fault:
        d[0x300:0x30C] = b"FAULT INJECT"
    open(path, "wb").write(bytes(d))
    elf_path = os.path.splitext(path)[0] + ".elf"
    if os.path.exists(elf_path):
        os.remove(elf_path)
    if elf:
        words = ["app_main", "ota_health_report"] + ([HOOK] if (fault if hook is None else hook) else [])
        open(elf_path, "w", encoding="ascii").write(" ".join(words))
    return path


def checks(R, t):
    def pair(g, f):
        rows = dict(R.judge_images(R.image_facts(g), R.image_facts(f)))
        return rows

    p = lambda n: os.path.join(t, n)
    for label, gn, fn in (("names before step 0c", "wican-fw_obd_abc1234_man", "wican-fw_obd_abc1234_man_flt"),
                          ("names from step 0c", "wican-fw_obd_man", "wican-fw_obd_man_flt")):
        rows = pair(fake_image(p("g.bin"), "abc1234", gn), fake_image(p("f.bin"), "abc1234", fn, fault=True))
        check("the good pair, %s: every row passes" % label, rows and all(rows.values()), rows)
        check("the good pair, %s: the commit row is there" % label, ROW in rows, sorted(rows))
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man"),
                fake_image(p("f.bin"), "def5678", "wican-fw_obd_man_flt", fault=True))
    check("two versions, post-0c names: the commit row fails", rows.get(ROW) is False, rows)
    check("two versions: only the commit row fails", [k for k, v in rows.items() if not v] == [ROW], rows)
    rows = pair(fake_image(p("g.bin"), "", "wican-fw_obd_man"), fake_image(p("f.bin"), "", "wican-fw_obd_man_flt",
                                                                          fault=True))
    check("no version in either: the commit row fails", rows.get(ROW) is False, rows)
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man", desc=False),
                fake_image(p("f.bin"), "abc1234", "wican-fw_obd_man_flt", fault=True, desc=False))
    check("no descriptor: no version read", R.image_facts(p("g.bin"))["version"] is None, R.image_facts(p("g.bin")))
    check("no descriptor: the commit row fails", rows.get(ROW) is False, rows)

    good_row, faulty_row = "good ELF does NOT define the fault hook", "faulty ELF defines the fault hook"
    check("the good string row is gone", "good has NO fault string" not in rows, sorted(rows))
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man", hook=True),
                fake_image(p("f.bin"), "abc1234", "wican-fw_obd_man_flt", fault=True))
    check("the good ELF defines the hook (no string): only the good hook row fails",
          [k for k, v in rows.items() if not v] == [good_row], rows)
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man"),
                fake_image(p("f.bin"), "abc1234", "wican-fw_obd_man_flt", fault=True, hook=False))
    check("the faulty ELF without the hook: only the faulty hook row fails",
          [k for k, v in rows.items() if not v] == [faulty_row], rows)
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man", elf=False),
                fake_image(p("f.bin"), "abc1234", "wican-fw_obd_man_flt", fault=True))
    check("no ELF beside the good: only the good hook row fails",
          [k for k, v in rows.items() if not v] == [good_row], rows)
    rows = pair(fake_image(p("g.bin"), "abc1234", "wican-fw_obd_man"),
                fake_image(p("f.bin"), "abc1234", "wican-fw_obd_man_flt", fault=True, elf=False))
    check("no ELF beside the faulty: only the faulty hook row fails",
          [k for k, v in rows.items() if not v] == [faulty_row], rows)


def fake_settings():
    """The module reads the WiCAN's identity from GI_CHAIN_SETTINGS when imported; a made-up
    one here, so the test never reads the real (local) file."""
    path = os.path.join(SCRATCH, "fake_chain_settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"wican": {"base_mac": "00:00:5e:00:53:10", "device_id": "fake-device"}}')
    os.environ["GI_CHAIN_SETTINGS"] = path


def fake_nm():
    """build_output.elf_symbols_beside() finds test_fault_hook_row.py's fake nm, never a real one."""
    sys.path.insert(0, BUILD_CHECKS)
    import test_fault_hook_row as TFH
    import elf_checks
    exe = TFH.make_fake_nm(os.path.join(SCRATCH, "nm"))
    elf_checks.find_nm = lambda: exe


def load(folder):
    fake_settings()
    fake_nm()
    sys.path.insert(0, folder)
    sys.path.insert(1, HERE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("orr_under_test", os.path.join(folder, TARGET))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path.remove(folder)
    return m


MUTANTS = [
    ("the commit row reads project_name again", 'bool(good.get("version")) and good.get("version") == faulty.get("version")))',
     'good.get("project_name", "").split("_")[2:3] == faulty.get("project_name", "").split("_")[2:3]))'),
    ("an empty version matches", 'bool(good.get("version")) and good.get("version") == faulty.get("version")))',
     'good.get("version") == faulty.get("version")))'),
    ("the version not read", '        version, elf = desc["version"], desc["elf_sha256"]', '        version, elf = "x", desc["elf_sha256"]'),
    ("the commit row always passes", 'rows.append(("same commit in both versions",\n                 bool(',
     'rows.append(("same commit in both versions",\n                 True or bool('),
    # port step 1a2 (2026-10-30): the hook rows
    ("the good row reads the string again", 'good.get("has_fault_symbol") is False))',
     'not good["has_fault_string"]))'),
    ("no ELF passes the good row", 'good.get("has_fault_symbol") is False))', 'not good.get("has_fault_symbol")))'),
    ("the faulty hook row always passes", 'faulty.get("has_fault_symbol") is True))', 'True))'),
    ("image_facts never reads the hook", "    hook, hook_why = fault_hook(path)", "    hook, hook_why = False, None"),
    ("fault_hook reads a prefix", "build_output.FAULT_HOOK_SYM in defined)", 'any(s.startswith("ota_") for s in defined))'),
]


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    if "--in-dir" in sys.argv[1:]:
        folder = sys.argv[sys.argv.index("--in-dir") + 1]
        with tempfile.TemporaryDirectory(dir=SCRATCH) as t:
            checks(load(folder), t)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    if "--mutants" not in sys.argv[1:]:
        with tempfile.TemporaryDirectory(dir=SCRATCH) as t:
            checks(load(HERE), t)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    for i, (label, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: the edit matches %d times" % (i, label, src.count(old)))
            errors += 1
            continue
        with tempfile.TemporaryDirectory(prefix="m%d-" % i, dir=SCRATCH) as d:
            open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
                src if old is None else src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--in-dir", d],
                               capture_output=True, text=True, timeout=300)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", label, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s%s" % ("KILLED  " if dead else ("CRASHED " if crashed else "SURVIVED"), i, label,
                               "  (%s)" % fails[0][6:90] if fails else ""))
    print("%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
