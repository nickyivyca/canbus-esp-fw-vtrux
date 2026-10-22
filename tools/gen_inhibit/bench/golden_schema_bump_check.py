"""Does a golden-trace diff change ONLY the diag_schema_ver byte?

Written 2026-10-07 by the gen-inhibit tester to accept or reject the 70 golden
traces the implementor regenerated in wican-fw-vtrux 6771edf (schema 5 -> 6,
the bus_on meaning change, spec 10/11). The goldens became the tester's that
day, so they are accepted only on a check, not on the implementor's word.

What "only the schema byte" means, from the DBC (vtrux-wican-diag.dbc):
  0x7F1 diag_schema_ver        start bit 0  -> payload byte 0
  0x7F3 diag_schema_ver_build  start bit 48 -> payload byte 6
Every removed line must pair with the added line at the same position, both
lines must be identical outside those bytes, and those bytes must go OLD->NEW.

Phase 2 also reports every 0x7F1 in the NEW goldens whose bus_on bit (flags
byte 3, bit1) is clear: under schema 6 that bit means "controller running", so
a scenario that models a stopped or bus-off controller would show it clear.
Reported, not judged.

The self-test at the end feeds a mutated diff through the same checker and
requires it to be rejected, so the check can fail.

Usage (project root):
  py -3.14 tools/gen_inhibit/bench/golden_schema_bump_check.py OLD NEW [--repo PATH]
"""
import argparse
import os
import re
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

SCHEMA_BYTE = {"7F1": 0, "7F3": 6}
LINE_RE = re.compile(r"^(?P<pre>.* id=(?P<id>[0-9A-F]{3}) dlc=\d+ data=)"
                     r"(?P<data>[0-9A-F]*)(?P<post>.*)$")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), check=True,
                          capture_output=True, text=True).stdout


def pair_problem(old, new, old_ver, new_ver):
    """None if `old`->`new` changes only the schema byte, else a reason."""
    mo, mn = LINE_RE.match(old), LINE_RE.match(new)
    if not mo or not mn:
        return "not a frame line"
    if mo.group("pre") != mn.group("pre") or mo.group("post") != mn.group("post"):
        return "text outside the payload differs"
    fid = mo.group("id")
    if fid not in SCHEMA_BYTE:
        return "id %s carries no schema byte" % fid
    bo = bytes.fromhex(mo.group("data"))
    bn = bytes.fromhex(mn.group("data"))
    if len(bo) != len(bn):
        return "payload length differs"
    k = SCHEMA_BYTE[fid]
    for i, (a, b) in enumerate(zip(bo, bn)):
        if i == k:
            if (a, b) != (old_ver, new_ver):
                return "schema byte %02X->%02X" % (a, b)
        elif a != b:
            return "byte %d differs (%02X->%02X)" % (i, a, b)
    return None


def check_diff(diff_text, old_ver, new_ver):
    """Returns (pairs_ok, problems) over a `git diff -U0` text."""
    problems, ok = [], 0
    fname, minus, plus = None, [], []

    def flush():
        nonlocal ok
        if len(minus) != len(plus):
            problems.append((fname, "%d removed vs %d added in a hunk"
                             % (len(minus), len(plus))))
        for a, b in zip(minus, plus):
            why = pair_problem(a, b, old_ver, new_ver)
            if why:
                problems.append((fname, "%s: %s" % (why, a.strip())))
            else:
                ok += 1
        minus.clear()
        plus.clear()

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush()
            fname = line.split(" b/")[-1]
        elif line.startswith("@@"):
            flush()
        elif line.startswith("---") or line.startswith("+++"):
            continue
        elif line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    flush()
    return ok, problems


def self_test():
    good = ("diff --git a/x b/x\n@@ -1 +1 @@\n"
            "-0 TX DIAG id=7F1 dlc=8 data=0501030600FF0000 ok=1\n"
            "+0 TX DIAG id=7F1 dlc=8 data=0601030600FF0000 ok=1\n")
    bad_flag = good.replace("+0 TX DIAG id=7F1 dlc=8 data=0601030600",
                            "+0 TX DIAG id=7F1 dlc=8 data=0601030400")
    bad_ver = good.replace("data=0601", "data=0701")
    bad_count = good + "+5 TX DIAG id=7F1 dlc=8 data=0601030600FF0000 ok=1\n"
    r = [check_diff(good, 5, 6)[1] == [],
         check_diff(bad_flag, 5, 6)[1] != [],
         check_diff(bad_ver, 5, 6)[1] != [],
         check_diff(bad_count, 5, 6)[1] != []]
    return all(r), r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old", nargs="?")
    ap.add_argument("new", nargs="?")
    # A re-bless done on the remote is not in git yet: phase 1 alone, over
    # `git diff --no-index -U0 <old golden dir> <new golden dir>` saved to FILE.
    ap.add_argument("--diff", metavar="FILE")
    ap.add_argument("--repo", default=gi_paths.repo_root())
    ap.add_argument("--path", default="test/gen_inhibit_host/golden")
    ap.add_argument("--old-ver", type=int, default=5)
    ap.add_argument("--new-ver", type=int, default=6)
    a = ap.parse_args()

    st_ok, st = self_test()
    print("self-test (good accepted; flag change, wrong version, unpaired "
          "line rejected): %s %s" % ("PASS" if st_ok else "FAIL", st))
    if not st_ok:
        return 2

    if a.diff:
        with open(a.diff, encoding="ascii") as fh:
            diff = fh.read()
        ok, problems = check_diff(diff, a.old_ver, a.new_ver)
        files = [ln.split(" b/")[-1] for ln in diff.splitlines() if ln.startswith("diff --git")]
        print("\nphase 1: %s, %d golden files changed, %d line pairs checked"
              % (a.diff, len(files), ok + len(problems)))
        per = {}
        for f, why in problems:
            per.setdefault(f, []).append(why)
        for f in sorted(per):
            print("  PROBLEM %s: %d, first: %s" % (f, len(per[f]), per[f][0]))
        print("  => %s" % ("ONLY the schema byte changed, %d->%d, in every pair"
                           % (a.old_ver, a.new_ver) if not problems and ok
                           else "REJECT: %d problem(s) in %d file(s)" % (len(problems), len(per))))
        return 1 if problems or not ok else 0

    files = git(a.repo, "diff", "--name-only", a.old, a.new, "--", a.path).split()
    allfiles = git(a.repo, "diff", "--name-only", a.old, a.new).split()
    outside = [f for f in allfiles if f.startswith("test/") and
               not f.startswith(a.path)]
    diff = git(a.repo, "diff", "-U0", a.old, a.new, "--", a.path)
    ok, problems = check_diff(diff, a.old_ver, a.new_ver)
    print("\nphase 1: %s..%s, %d golden files changed, %d line pairs checked"
          % (a.old, a.new, len(files), ok + len(problems)))
    print("  test/ files changed outside %s: %s" % (a.path, outside or "none"))
    for f, why in problems[:40]:
        print("  PROBLEM %s: %s" % (f, why))
    print("  => %s" % ("ONLY the schema byte changed, %d->%d, in every pair"
                       % (a.old_ver, a.new_ver) if not problems and ok
                       else "REJECT: %d problem(s)" % len(problems)))

    print("\nphase 2: 0x7F1 frames in the NEW goldens with bus_on (bit1) clear")
    names = git(a.repo, "ls-tree", "-r", "--name-only", a.new, a.path).split()
    n7f1, clear = 0, {}
    for nm in names:
        if not nm.endswith(".trace"):
            continue
        for ln in git(a.repo, "show", "%s:%s" % (a.new, nm)).splitlines():
            m = LINE_RE.match(ln)
            if m and m.group("id") == "7F1":
                n7f1 += 1
                if not bytes.fromhex(m.group("data"))[3] & 0x02:
                    clear[nm] = clear.get(nm, 0) + 1
    print("  %d 0x7F1 frames in %d traces; bit1 clear in %d of them, in %d "
          "traces" % (n7f1, len(names), sum(clear.values()), len(clear)))
    for nm, c in sorted(clear.items()):
        print("    %s: %d" % (nm, c))
    return 1 if problems or not ok else 0


if __name__ == "__main__":
    sys.exit(main())
