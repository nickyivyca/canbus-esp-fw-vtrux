#!/usr/bin/env python3
"""Mutants of baseline_b_desk.sh; test_baseline_b_desk.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 -B \
        tools/gen_inhibit/bench/test_baseline_b_desk_mutants.py

Run from the reverse-it root. Each mutant is a copy of the script with one edit, in the
machine's temp folder, tested on the one step the edit touches (DESK_SCRIPT, --steps).
About 10 minutes, most of it the B24 mutant. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "baseline_b_desk.sh")
TEST = os.path.join(HERE, "test_baseline_b_desk.py")

MUTANTS = [   # (name, step, old, new)
    ("need: an unexpanded glob counts as input", "B11",
     '[ $# -gt 0 ] && [ -e "$1" ] ||', '[ $# -gt 0 ] ||'),
    ("B3: no input is analysed anyway", "B3",
     '"${1:?B3 dir}"/B3_e4_golive_*_${C}_*.json; need "$@"', '"${1:?B3 dir}"/B3_e4_golive_*_${C}_*.json'),
    ("B2: five arms will do", "B2",
     '[ $# -eq 6 ] || { echo "B2: $# arm records, want 6"; exit 2; }', 'true'),
    ("B25: two rekey-long records will do", "B25",
     '[ $# -eq 1 ] || { echo "B25: $# rekey-long records, want 1"; exit 2; }', 'true'),
    ("B19present: a missing file is skipped", "B19present",
     '[ -f "$f" ] || { echo "B19present: no $f"; exit 2; }', '[ -f "$f" ] || continue'),
    ("B13: a missing held arm is not looked for", "B13",
     'for j in "$@"; do [ -f "$j" ] || { echo "B13: no $j"; exit 2; }; done', 'true'),
    ("B13: B2's RESPOND arms by glob again", "B13",
     '"$app"/B2_e4_presence_respond_${C}_x2app.json "$held"/B2_e4_presence_respond_${C}_x2held.json',
     '"$app"/B2_e4_presence_respond_${C}_*.json "$held"/B2_e4_presence_respond_${C}_*.json'),
    ("B11raw: arguments as one unquoted string", "B11raw",
     'args=(); for n in "$@"; do args+=("$n" "${n%.json}.pcapng"); done',
     'args=(); for n in "$@"; do args+=("$n ${n%.json}.pcapng"); done; args=(${args[*]})'),
    ("B24: B3's arms left out", "B24",
     'set -- "$app"/B2_e4_presence_*_${C}_x2app.json "$b3"/B3_e4_golive_*_${C}_*.json; need "$@"',
     'set -- "$app"/B2_e4_presence_*_${C}_x2app.json; need "$@"'),
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "baseline_b_desk")
    src = open(SRC, encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, step, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        script = os.path.join(d, "baseline_b_desk.sh")
        open(script, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        env = dict(os.environ, DESK_SCRIPT=script)
        r = subprocess.run([sys.executable, "-B", TEST, "--steps", step], capture_output=True, text=True, env=env)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s [%s]  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, step, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
