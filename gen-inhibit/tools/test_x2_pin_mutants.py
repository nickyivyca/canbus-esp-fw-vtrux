#!/usr/bin/env python3
"""Mutants of x2_pin.py; test_x2_pin.py must fail on each BY A CHECK. A crash is not a kill,
and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/test_x2_pin_mutants.py

Each mutant is x2_pin.py with one edit, written with test_x2_pin.py and x2_pin.json into this
run's own folder in the worktree's scratch/ (removed at the end). The unmutated copy runs first,
as the control: if it fails a check, no kill counts. Gen-inhibit tester, 2026-11-10.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402
SRC, TEST, PIN = "x2_pin.py", "test_x2_pin.py", "x2_pin.json"

MUTANTS = [
    ("an unknown top-level key is accepted", "if not isinstance(pin, dict) or set(pin) != TOP_KEYS:",
     "if not isinstance(pin, dict) or not TOP_KEYS <= set(pin):"),
    ("an unknown image key is accepted", "if not isinstance(im, dict) or set(im) != IMAGE_KEYS:",
     "if not isinstance(im, dict) or not IMAGE_KEYS <= set(im):"),
    ("an unknown file key is accepted", "if not isinstance(fe, dict) or not FILE_NEED <= set(fe) <= FILE_KEYS:",
     "if not isinstance(fe, dict) or not FILE_NEED <= set(fe):"),
    ("a name may hold a path", 'NAME = re.compile(r"^[A-Za-z0-9_.-]+$")', 'NAME = re.compile(r"^.+$")'),
    ("hex in upper case is accepted", 'HEX64 = re.compile(r"^[0-9a-f]{64}$")', 'HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")'),
    ("a decimal offset is accepted", 'OFFSET = re.compile(r"^0x[0-9a-f]+$")', 'OFFSET = re.compile(r"^(0x)?[0-9a-f]+$")'),
    ("a size of true is accepted", 'if not isinstance(fe["size"], int) or isinstance(fe["size"], bool) or fe["size"] <= 0:',
     'if not isinstance(fe["size"], int) or fe["size"] <= 0:'),
    ("a short md5 is accepted", 'HEX32 = re.compile(r"^[0-9a-f]{32}$")', 'HEX32 = re.compile(r"^[0-9a-f]{30,32}$")'),
    ("app false is accepted", 'if "app" in fe and fe["app"] is not True:', 'if False:'),
    ("two files at one offset are accepted", 'if fe["offset"] in offs or fe["name"] in names:',
     'if fe["name"] in names:'),
    ("two files of one name are accepted", 'if fe["offset"] in offs or fe["name"] in names:',
     'if fe["offset"] in offs:'),
    ("any number of app files", "        if apps != 1:\n", "        if apps > 2:\n"),
    ("an 8-hex identity need not be the ELF's digits",
     'if re.match(r"^[0-9a-f]{8}$", im["identity"]) and not im["app_elf_sha256"].startswith(im["identity"]):',
     'if False:'),
    ("no source is accepted", 'if not isinstance(pin["source"], dict) or not pin["source"]:',
     'if not isinstance(pin["source"], dict):'),
    ("load: the size is not compared", 'if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):',
     'if (s, m) != (fe["sha256"], fe["md5"]):'),
    ("load: the sha256 is not compared", 'if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):',
     'if (size, m) != (fe["size"], fe["md5"]):'),
    ("load: the app's ELF is not compared", "                if elf != im[\"app_elf_sha256\"]:\n",
     "                if False:\n"),
    ("load: files from the pin dir itself, not <pin>/<image>/", '    return os.path.join(pin_dir, pin["pin"], iname)',
     '    return pin_dir'),
    ("install: a source file of the right name is taken whatever its bytes",
     "if os.path.isfile(c) and digests(c) == want:", "if os.path.isfile(c):"),
    ("install: a file in place with other bytes is overwritten",
     '                if not os.path.isfile(dest) or digests(dest) != want:\n'
     '                    raise Mismatch("%s is in place with other bytes: refusing to overwrite it" % dest)\n'
     '                plan.append((iname, fe["name"], None, dest))\n                continue\n',
     '                pass\n'),
    ("install: a .partial file is left behind", "        os.replace(tmp, dest)\n",
     "        shutil.copyfile(tmp, dest)\n"),
    ("the command line: a malformed pin exits 1", '        print("REFUSED: %s" % e)\n        return 2',
     '        print("REFUSED: %s" % e)\n        return 1'),
]


def main():
    base = gi_paths.scratch("mutants-x2_pin", start=__file__)
    d = os.path.join(base, "tools")
    os.makedirs(d)
    shutil.copy(os.path.join(HERE, TEST), d)
    shutil.copy(os.path.join(HERE, PIN), d)
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0

    def run(text):
        with open(os.path.join(d, SRC), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        return r, fails, (", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "")

    try:
        r, fails, crash = run(src)
        control_ok = r.returncode == 0 and not fails and not crash
        print("%s control: the unmutated copy  (exit %d, %d FAIL%s)" % (
            "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails), crash))
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            r, fails, crash = run(src.replace(old, new))
            dead = bool(fails)
            killed += dead
            print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
                "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails), crash,
                ("; " + fails[0][:100]) if fails else ""))
            sys.stdout.flush()
    finally:
        removed = gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s%s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS",
             "" if removed else "; the scratch folder was NOT removed (above)"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok and removed else 1


if __name__ == "__main__":
    sys.exit(main())
