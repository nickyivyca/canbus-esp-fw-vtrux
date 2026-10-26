#!/usr/bin/env python3
"""Mutants of test_only_inert.py's probe_json observable and its page-only build read
(GI_INSTRUMENT_ECC, 2026-10-20); test_test_only_inert.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error. Run with GI_FW_REPO
naming the firmware folder (gen-inhibit/firmware/ since port step 0d) of a tree whose
build_switches.py lists GI_INSTRUMENT_ECC.

    GI_FW_REPO=<tree>/gen-inhibit/firmware py -3.14 gen-inhibit/tools/bench/test_test_only_inert_probejson_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder. The dispatch
in main() (the probe_json branch) is not reached without the bench; the inert run on
the _ecc image (present) and the product image (absent) is its check.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "test_only_inert.py"
TEST = "test_test_only_inert.py"

MUTANTS = [
    ("any probe name counts", 'return any(v == observe.split(":", 1)[1] for v in got.values())',
     'return any(bool(v) for v in got.values())'),
    ("no answer is not undetermined", '        if not got or any(v is None for v in got.values()):\n',
     '        if not got:\n'),
    ("a non-JSON answer reads as the instrument", '    except ValueError:\n        return ""\n',
     '    except ValueError:\n        return "gi_ecc"\n'),
    ("no answer reads as absent", '    if st is None:\n        return None\n    try:', '    if st is None:\n        return ""\n    try:'),
    ("a JSON list reads as the instrument", 'return d.get("probe", "") if isinstance(d, dict) else ""',
     'return d.get("probe", "") if isinstance(d, dict) else "gi_ecc"'),
    ("POST paths are probed too", '        if method != "GET":\n            continue\n', '\n'),
    ("the probe value is not recorded", '        out["probe_json"][path] = probe_answer(st, body)\n',
     '        out["probe_json"][path] = "gi_ecc"\n'),
    ("alive reads the build through build_id", '"build": p.get("elf_sha256"), "mode": p.get("mode")}',
     '"build": (LF.build_id() or {}).get("git_version"), "mode": p.get("mode")}'),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-inert_probejson_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
