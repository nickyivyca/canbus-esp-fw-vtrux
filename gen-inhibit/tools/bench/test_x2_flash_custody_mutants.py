#!/usr/bin/env python3
"""Mutants of x2_flash_custody.py's dump-record path (2026-10-23); test_x2_flash_custody.py
must fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply is
an error.

    python3.13 -B gen-inhibit/tools/bench/test_x2_flash_custody_mutants.py

Since 2026-11-12 also the --expect-elf mutants: the old default back, the length, upper
bound and hex checks dropped, a refusal going on, check-dump unchecked, no lower-casing,
and the record without expect_elf.

Since 2026-11-13 also flash-jammer --row and check-row (each check of a manifest.jsonl row
dropped or loosened, a refusal going on, the argument rules) and write_rec's names for two
records in one second.

Since 2026-11-13 (later) also read-otadata: the crc, an erased seq, the highest seq, the slot
index, the factory fallback, the selecting state and ABORTED, the offset compared, the read's
extent, size and rc, and the esptool checks.

Since 2026-11-13 (later still) also verify-row: the esptool checks and its row arguments, a
refused row going on, otadata judged by verify_flash, the app not judged, a FAILED or another
version's line read as OK, a file with no result line or esptool's exit not matching its lines
let through, the whole output not kept, otadata not read, its verdict ignored or not required
to PASS, no run, run's exit ignored, run before the otadata read, a FAIL reading ERROR, and the
otadata table check dropped.

Needs REVERSE_IT_DIR and INTERPOSER_CODE, as the test does. Each mutant is a copy of
x2_flash_custody.py with one edit, in the worktree's scratch/, imported by the test
through CUSTODY_DIR. A few seconds each. Gen-inhibit tester, 2026-10-23.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_flash_custody.py"
TEST = "test_x2_flash_custody.py"
MUTANTS = [
    ("dump: path recorded relative to bench/ again (a24de99)",
     'os.path.relpath(p, gi_paths.data_dir()).replace(', 'os.path.relpath(p, HERE).replace('),
    ("dump: path recorded absolute",
     'os.path.relpath(p, gi_paths.data_dir()).replace(', 'os.path.abspath(p).replace('),
    ("readers: the refusal dropped",
     '    if os.path.isabs(rel) or rel.startswith(("/", "\\\\")) or ".." in parts:\n',
     '    if False:\n'),
    ("check-dump: resolves the path itself, without the refusal",
     '    path = dump_path(a.dump_record, dr["dump"])\n', '    path = gi_paths.data_dir(dr["dump"])\n'),
    ("_checked_dump: resolves the path itself, without the refusal",
     '    p = dump_path(a.dump_record, d["dump"])\n', '    p = gi_paths.data_dir(d["dump"])\n'),
    # esptool (gi_esptool.py; gen-inhibit reviewer, 2026-11-11)
    ("dump: the dashed read-flash", '"read_flash", "0", "ALL", p)', '"read-flash", "0", "ALL", p)'),
    ("restore: the dashed write-flash", '"write_flash", "0x0", p)', '"write-flash", "0x0", p)'),
    ("restore: the dashed verify-flash", '"verify_flash", "0x0", p)', '"verify-flash", "0x0", p)'),
    ("the version is not checked", "                _ET.update(GE.checked())\n",
     '                _ET.update({"argv": GE.from_env(), "version": None})\n'),
    ("the record has no esptool argv or version",
     '                rec["esptool_argv"], rec["esptool_version"] = _ET["argv"], _ET["version"]\n',
     "                pass\n"),
    ("a refusal goes on to the board",
     '                print("REFUSED: %s (nothing run)" % e)\n                return 1\n',
     '                print("REFUSED: %s (nothing run)" % e)\n'),
    # the port by the X2's USB IDs (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12)
    ("the port is a tty name, not resolved", '                dev = GS.resolve("x2")\n',
     '                dev = {"port": "/dev/ttyACM0"}\n'),
    ("the record has no serial_device", '            rec["serial_device"] = dev\n', "            pass\n"),
    ("a port refusal goes on to the board", "            except (GE.Refused, GS.Refused) as e:\n",
     "            except GE.Refused as e:\n"),
    # --expect-elf has no default (gen-inhibit reviewer, 2026-11-12)
    ("the old default back", '    ap.add_argument("--expect-elf", default=None,\n',
     '    ap.add_argument("--expect-elf", default="7f197bfd",\n'),
    ("no length check", "    if not 8 <= len(v) <= 64 or any(", "    if any("),
    ("no upper bound", "    if not 8 <= len(v) <= 64 or any(", "    if not 8 <= len(v) or any("),
    ("no hex check", ' or any(c not in "0123456789abcdef" for c in v.lower()):', ":"),
    ("an expect_elf refusal goes on", '                print("REFUSED: %s (nothing run)" % why)\n                return 1\n',
     '                print("REFUSED: %s (nothing run)" % why)\n'),
    ("check-dump not checked", 'EXPECT_SUBS = ("dump", "check-dump")', 'EXPECT_SUBS = ("dump",)'),
    ("matched as given, not in lower case", "            a.expect_elf = a.expect_elf.lower()\n",
     "            pass\n"),
    ("the record has no expect_elf", '            rec["expect_elf"] = a.expect_elf\n', "            pass\n"),
    # flash-jammer --row and check-row: manifest.jsonl rows of the ESP-IDF port (2026-11-13)
    ("--row: 15 or 16 digits", 'ROW_RE = re.compile(r"[0-9a-f]{16}")', 'ROW_RE = re.compile(r"[0-9a-f]{15,16}")'),
    ("--row: upper case taken", 'ROW_RE = re.compile(r"[0-9a-f]{16}")', 'ROW_RE = re.compile(r"[0-9a-fA-F]{16}")'),
    ("--row: a line not JSON skipped", '                raise RowRefused("%s line %d is not JSON" % (manifest, n))\n',
     "                continue\n"),
    ("--row: the first of several rows taken", "    if len(rows) != 1:\n        raise RowRefused(",
     "    if not rows:\n        raise RowRefused("),
    ("--row: schema and image not checked", '    if row.get("schema") != 1 or row.get("image") != "jammer":\n',
     "    if False:\n"),
    ("--row: the variant not checked", '    if row.get("variant") != a.variant:\n', "    if False:\n"),
    ("--row: dev rows not checked", '    if row.get("pinnable") is not True and a.dev_builder != row.get("builder"):\n',
     "    if False:\n"),
    ("--row: any --dev-builder will do", ' and a.dev_builder != row.get("builder"):\n', " and a.dev_builder is None:\n"),
    ("--row: a row without pinnable taken as pinnable", 'if row.get("pinnable") is not True and',
     'if row.get("pinnable") is False and'),
    ("--row: app_sha256 not matched to the folder", "    if not app_sha.startswith(a.row):\n", "    if False:\n"),
    ("--row: two files at one offset taken", "    if len(set(offsets)) != len(offsets):\n", "    if False:\n"),
    ("--row: no offsets required", "    missing = [hex(o) for o in ROW_OFFSETS + (app_off,) if o not in offsets]\n",
     "    missing = []\n"),
    ("--row: written in the row's order", "    for off, f in sorted(files, key=lambda x: x[0]):\n",
     "    for off, f in files:\n"),
    ("--row: a name with a path part taken",
     '        if not name or name != os.path.basename(name) or name in (".", "..") or "\\\\" in name:\n',
     "        if not name:\n"),
    ("--row: a missing file not named", "        if not os.path.isfile(p):\n", "        if False:\n"),
    ("--row: size not checked", 'zip(("size", "sha256", "md5"), got, want)', 'zip(("sha256", "md5"), got[1:], want[1:])'),
    ("--row: sha256 not checked", 'zip(("size", "sha256", "md5"), got, want)',
     'zip(("size", "md5"), got[::2], want[::2])'),
    ("--row: md5 not checked", 'zip(("size", "sha256", "md5"), got, want)', 'zip(("size", "sha256"), got, want)'),
    ("--row: the file at app_offset not matched to app_sha256", "    if sha256_file(app) != app_sha:\n",
     "    if False:\n"),
    ("--row: app_elf_sha256 not checked", ' or elf != row.get("app_elf_sha256") or', " or"),
    ("--row: image_hash not checked", ' or elf != row.get("image_hash"):\n', ":\n"),
    ("--row: project_name not judged from the image", '    if pn != row.get("project_name"):\n', "    if False:\n"),
    ("--row: NOT_FOR_VEHICLE not required", '    if not pn or "NOT_FOR_VEHICLE" not in pn:\n', "    if not pn:\n"),
    ("--row: a refusal goes on to the write",
     '        print("REFUSED: %s (nothing written)" % e)\n        return\n',
     '        print("REFUSED: %s (nothing written)" % e)\n'),
    ("--row: the record has no dev_builder",
     '    rec["jammer"]["dev_builder"] = a.dev_builder\n    rec["jammer"]["files"] = [{"offset": hex(o), "name": '
     'os.path.basename(p)} for o, p in files]\n    args',
     '    rec["jammer"]["files"] = [{"offset": hex(o), "name": os.path.basename(p)} for o, p in files]\n    args'),
    ("flash-jammer: --build and --row together", "    if (a.build is None) == (a.row is None):\n",
     "    if a.build is None and a.row is None:\n"),
    ("flash-jammer: --row without --variant", "    if a.row is not None and not a.variant:\n", "    if False:\n"),
    ("flash-jammer: --build with --variant or --dev-builder",
     "    if a.build is not None and (a.variant is not None or a.dev_builder is not None):\n", "    if False:\n"),
    ("other subcommands take the build arguments",
     '        return "%s takes no %s" % (a.sub, ", ".join(given)) if given else None\n', "        return None\n"),
    ("check-row: --build taken", "        if a.build is not None:\n", "        if False:\n"),
    ("records: a second record of one second crashes again",
     "        except FileExistsError:\n            n += 1\n", "        except FileExistsError:\n            raise\n"),
    ("records: a second record of one second overwrites the first",
     '            with open(path, "x", encoding="ascii", errors="replace") as fh:\n',
     '            with open(base + ".json", "w", encoding="ascii", errors="replace") as fh:\n'),
    # --no-dump (shared/bench SPEC section 8; gen-inhibit reviewer, 2026-11-13)
    ("--no-dump: the dump checked anyway", "    if a.no_dump:\n        # the holder", "    if False:\n        # the holder"),
    ("--no-dump: not recorded", '    rec["no_dump"] = bool(a.no_dump)\n', "    pass\n"),
    ("flash-jammer: --dump-record and --no-dump together",
     "    if bool(a.dump_record) == bool(a.no_dump):\n", "    if not a.dump_record and not a.no_dump:\n"),
    ("flash-jammer: neither --dump-record nor --no-dump",
     "    if bool(a.dump_record) == bool(a.no_dump):\n", "    if a.dump_record and a.no_dump:\n"),
    ("other subcommands take --no-dump",
     '        return "%s takes no --no-dump" % a.sub if a.no_dump else None\n', "        return None\n"),
    ("check-row: a refused row reads PASS",
     '        rec.update(verdict="REFUSED", why=str(e))\n        print("REFUSED: %s" % e)\n',
     '        rec.update(verdict="PASS", why=str(e))\n        print("REFUSED: %s" % e)\n'),
    # read-otadata (gen-inhibit reviewer, 2026-11-13)
    ("otadata: the crc not checked", '"valid": seq != 0xFFFFFFFF and crc_ok,', '"valid": seq != 0xFFFFFFFF,'),
    ("otadata: an erased seq taken", '"valid": seq != 0xFFFFFFFF and crc_ok,', '"valid": crc_ok,'),
    ("otadata: the lowest seq selects", "        top = max(valid, key=", "        top = min(valid, key="),
    ("otadata: slot index off by one", '        p = slots[(top["ota_seq"] - 1) % len(slots)]\n',
     '        p = slots[top["ota_seq"] % len(slots)]\n'),
    ("otadata: the factory app ignored", "    elif factory:\n", "    elif False:\n"),
    ("otadata: the selecting state not judged", ' and sel["selecting_state"] not in (3, 4)\n', "\n"),
    ("otadata: ABORTED not rejected", 'sel["selecting_state"] not in (3, 4)', 'sel["selecting_state"] not in (3,)'),
    ("otadata: the offset not compared", '    ok = sel["selected_offset"] == app_off and',
     '    ok = sel["selected_offset"] is not None and'),
    ("otadata: the whole flash read", '"read_flash", "0x%x" % off, "0x%x" % size, out)', '"read_flash", "0", "ALL", out)'),
    ("otadata: the read's size not checked",
     "    if rc != 0 or not os.path.exists(out) or os.path.getsize(out) != size:\n",
     "    if rc != 0 or not os.path.exists(out):\n"),
    ("otadata: esptool's rc not checked",
     "    if rc != 0 or not os.path.exists(out) or os.path.getsize(out) != size:\n",
     "    if not os.path.exists(out) or os.path.getsize(out) != size:\n"),
    ("otadata: read-otadata without the esptool checks",
     'ESPTOOL_SUBS = ("dump", "flash-jammer", "restore", "read-otadata", "verify-row")',
     'ESPTOOL_SUBS = ("dump", "flash-jammer", "restore", "verify-row")'),
    # verify-row (gen-inhibit reviewer, 2026-11-13: T10's identity with committed harness)
    ("verify-row: without the esptool checks",
     'ESPTOOL_SUBS = ("dump", "flash-jammer", "restore", "read-otadata", "verify-row")',
     'ESPTOOL_SUBS = ("dump", "flash-jammer", "restore", "read-otadata")'),
    ("verify-row: its row arguments not taken", '    if a.sub in ("check-row", "read-otadata", "verify-row"):\n',
     '    if a.sub in ("check-row", "read-otadata"):\n'),
    ("verify-row: a refused row goes on to the board",
     '        print("REFUSED: %s (nothing run)" % e)\n        return\n    with open(dict(files)[PT_AT]',
     '        print("REFUSED: %s (nothing run)" % e)\n    with open(dict(files)[PT_AT]'),
    ("verify-row: otadata judged too", "    judged = [(o, p) for o, p in files if o != ota_off]\n",
     "    judged = list(files)\n"),
    ("verify-row: the app not judged", "    judged = [(o, p) for o, p in files if o != ota_off]\n",
     '    judged = [(o, p) for o, p in files if o not in (ota_off, int(row["app_offset"], 16))]\n'),
    ("verify-row: a FAILED line read as OK", '"OK" if nxt == GE.VERIFY_OK else "FAILED" if nxt == VERIFY_FAILED else None',
     '"OK" if nxt in (GE.VERIFY_OK, VERIFY_FAILED) else None'),
    ("verify-row: any result line read as OK", '"OK" if nxt == GE.VERIFY_OK else',
     '"OK" if nxt.startswith("-- verify OK") else'),
    ("verify-row: a file with no result line not refused", "    if None in results:\n", "    if False:\n"),
    ("verify-row: esptool's exit not matched to its lines",
     "    elif (rc == 0) != all(r == \"OK\" for r in results):\n", "    elif False:\n"),
    ("verify-row: the whole output not kept", "    rc, _tail = esptool(a.port, *args, log=log)\n",
     "    rc, _tail = esptool(a.port, *args)\n"),
    ("verify-row: otadata not read", "    sub_read_otadata(a, orec)\n", '    orec["verdict"] = "PASS"\n'),
    ("verify-row: otadata's verdict ignored", '    if vv == "FAIL" or ov == "OTHER-SLOT":\n',
     '    if vv == "FAIL":\n'),
    ("verify-row: otadata not required to PASS", '    elif vv != "PASS" or ov != "PASS":\n',
     '    elif vv != "PASS":\n'),
    ("verify-row: no run", '    rrc, rtail = esptool(a.port, "run")\n', '    rrc, rtail = 0, []\n'),
    ("verify-row: run's exit ignored", "    elif rrc != 0:\n", "    elif False:\n"),
    ("verify-row: run before the otadata read",
     '    orec = {"when": rec["when"]}\n    sub_read_otadata(a, orec)\n    rec["otadata_read"] = orec\n'
     '    rrc, rtail = esptool(a.port, "run")\n',
     '    rrc, rtail = esptool(a.port, "run")\n    orec = {"when": rec["when"]}\n    sub_read_otadata(a, orec)\n'
     '    rec["otadata_read"] = orec\n'),
    ("verify-row: a FAIL reads ERROR", '        rec["verdict"] = "FAIL"\n        rec["why"] = "the X2 does not hold',
     '        rec["verdict"] = "ERROR"\n        rec["why"] = "the X2 does not hold'),
    ("verify-row: the otadata table check dropped", "    if len(ota) != 1:\n        rec.update(verdict=\"REFUSED\", "
     "why=\"the row's partition table has %d otadata partitions, not 1\" % len(ota))\n        print(\"REFUSED: %s "
     "(nothing run)\"", "    if False:\n        rec.update(verdict=\"REFUSED\", "
     "why=\"the row's partition table has %d otadata partitions, not 1\" % len(ota))\n        print(\"REFUSED: %s "
     "(nothing run)\""),
]


def main():
    base = gi_paths.scratch("mutants-custody_path")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, CUSTODY_DIR=d), timeout=600)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        if old is None:
            ok = r.returncode == 0 and not fails and "Traceback" not in r.stderr
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ("; " + fails[0][:90]) if fails else ""))
            if not ok:
                if r.stderr.strip():
                    print("         stderr: %s" % r.stderr.strip().splitlines()[-1][:160])
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        # killed only by a check line; a traceback with no FAIL line is a crash
        dead = bool(fails) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fails), ("; " + fails[0][:90]) if fails else ""))
        if not dead and r.stderr.strip():
            print("         stderr: %s" % r.stderr.strip().splitlines()[-1][:160])
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
