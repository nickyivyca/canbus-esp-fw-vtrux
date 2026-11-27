#!/usr/bin/env python3
"""Mutants of baseline_b_desk.sh; test_baseline_b_desk.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 -B \
        gen-inhibit/tools/bench/test_baseline_b_desk_mutants.py

Run from the reverse-it root. The mutants run in a shared clone of this repo at HEAD,
made in the worktree's scratch/, with this folder copied over the clone's copy of it
(gen-inhibit/tools/bench/ since port step 0d); each writes the script there with one edit and is tested on
the one step the edit touches (DESK_SCRIPT, --steps). The script runs its tools from its
own folder ($A), and quiesce_analysis and passive_analysis decode through gi_diag, which
reads the DBC's git history from the repo root above it: a copy of the script alone in a bare
folder failed every step's index check with no edit at all. The unmutated copy runs
first, on every step a mutant uses, as the control: if it fails a check, no kill counts. About 10 minutes, most of it the B24 mutant. Gen-inhibit
tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

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
    ("B11raw: the records word-split", "B11raw",
     '$PY -B $A/b11_rawpost_judge.py "$@" > "$D/B11raw_window_judge.txt"',
     '$PY -B $A/b11_rawpost_judge.py $* > "$D/B11raw_window_judge.txt"'),
    ("B11raw: the window judgement over the capture judge's file", "B11raw",
     '> "$D/B11raw_window_judge.txt" 2>&1; echo "judge exit $?"', '> "$D/B11raw_rawpost_judge.txt" 2>&1; echo "judge exit $?"'),
    ("B5: three livetrain outputs will do", "B5",
     '[ $# -eq 4 ] && [ -e "$4" ] || { echo "B5: $# livetrain outputs, want 4"; exit 2; }', 'true'),
    ("B5: a missing record skipped, not refused before writing", "B5",
     '[ -n "$r" ] && [ -f "$recs/$r" ] || { echo "B5: no record for $(basename "$f") (${r:-no wrote line})"; exit 2; }',
     '[ -n "$r" ] && [ -f "$recs/$r" ] || continue'),
    ("B5: the re-judgement written under the run's own output name", "B5",
     'o="$D/$(basename "${1%.txt}")_rejudged.txt"', 'o="$D/$(basename "$1")"'),
    ("B21client: two probe outputs of a body will do", "B21client",
     '[ $# -eq 1 ] && [ -e "$1" ] || { echo "B21client: $# $body probe outputs, want 1"; exit 2; }',
     '[ -e "$1" ] || { echo "B21client: $# $body probe outputs, want 1"; exit 2; }'),
    ("B21client: a missing record skipped, not refused before writing", "B21client",
     '[ -n "$r" ] && [ -f "$recs/$r" ] || { echo "B21client: no record for $(basename "$1") (${r:-no wrote line})"; exit 2; }',
     '[ -n "$r" ] && [ -f "$recs/$r" ] || continue'),
    ("B21client: the two bodies' judgements under one name", "B21client",
     '> "$D/B21_client_$1_${C}.txt" 2>&1; rc=$?', '> "$D/B21_client_${C}.txt" 2>&1; rc=$?'),
    # F4 (2026-11-27)
    ("F4: the provenance files read as records", "F4",
     '"${1:?F4 dir}"/F4_e4_respond_entry_${C}_*_arm[0-9].json', '"${1:?F4 dir}"/F4_e4_respond_entry_${C}_*.json'),
    ("F4: exit 0 with a record counted out", "F4", '[ "$rc" -eq 0 ] || exit 1 ;;', 'true ;;'),
    ("F4: no input is analysed anyway", "F4",
     '_*_arm[0-9].json; need "$@"', '_*_arm[0-9].json'),
    ("B24: B3's arms left out", "B24",
     'set -- "$app"/B2_e4_presence_*_${C}_x2app.json "$b3"/B3_e4_golive_*_${C}_*.json; need "$@"',
     'set -- "$app"/B2_e4_presence_*_${C}_x2app.json; need "$@"'),
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = gi_paths.scratch("mutants-baseline_b_desk")
    src = open(SRC, encoding="utf-8", newline="").read()
    killed = errors = 0
    d = clone(base)

    def run(steps, text):
        script = os.path.join(d, "baseline_b_desk.sh")
        open(script, "w", encoding="utf-8", newline="").write(text)
        env = dict(os.environ, DESK_SCRIPT=script)
        r = subprocess.run([sys.executable, "-B", TEST, "--steps", steps], capture_output=True, text=True, env=env)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        return r, fails, "Traceback" in r.stderr

    # The unmutated copy first, on every step a mutant uses: if it fails a check, every
    # mutant would be killed by it.
    steps = ",".join(sorted({m[1] for m in MUTANTS}))
    r, fails, crashed = run(steps, src)
    control_ok = r.returncode == 0 and not fails and not crashed
    print("%s control: the unmutated copy [%s]  (exit %d, %d FAIL%s)%s" % (
        "PASS    " if control_ok else "FAILED  ", steps, r.returncode, len(fails), ", CRASHED" if crashed else "",
        "".join("\n           " + f[:130] for f in fails[:3])))
    sys.stdout.flush()
    for n, (name, step, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        r, fails, crashed = run(step, src.replace(old, new))
        dead = bool(fails)
        killed += dead
        print("%s m%d %s [%s]  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, step, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


def clone(base):
    """base/repo: a shared clone of this worktree at HEAD, with this folder copied over
    its copy of this folder, so the script's tools sit beside it ($A) and gi_diag,
    which quiesce_analysis and passive_analysis decode through, finds the DBC's git
    history from there. Returns that folder."""
    wt = gi_paths.worktree_of(HERE)
    head = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    repo = os.path.join(base, "repo")
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", wt, repo], check=True)
    subprocess.run(["git", "-C", repo, "-c", "advice.detachedHead=false", "checkout", "-q", head], check=True)
    d = os.path.join(repo, os.path.relpath(HERE, wt))
    shutil.rmtree(d)
    shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__"))
    # the pinned canre (canre_pin), which step B5's x2_jam_cases loads through
    # lowid_flood_skip_bench: the clone's scratch/ has no install of its own, so this
    # worktree's is copied in, and canre_pin.use() checks the copy in full (2026-11-19)
    import canre_pin
    src = canre_pin.folder(start=HERE)
    if os.path.isdir(src):
        shutil.copytree(src, canre_pin.folder(start=d), ignore=shutil.ignore_patterns("__pycache__"))
    return d


if __name__ == "__main__":
    sys.exit(main())
