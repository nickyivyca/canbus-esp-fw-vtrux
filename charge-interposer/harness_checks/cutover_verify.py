"""Verify the charge interposer's git cutover, file by file (tester, 2026-10-08).

The implementor copies projects/vtrux/tools/interposer/ into the repo
canbus-esp-fw-vtrux as charge-interposer/ (commit 1), less __pycache__, .pio
and firmware/datasheets. This compares a FRESH CLONE of that commit -- not
the implementor's working tree, so .gitattributes, ignore rules and LFS all
have their say -- against the SeaDrive folder:

  - every SeaDrive file (less the exclusions) at the same relative path in
    the clone;
  - no file in the clone that SeaDrive does not have, except .gitignore /
    .gitattributes;
  - the move manifest, when given: its `seadrive` column against SeaDrive,
    its `committed` column against the clone, and its list of files whose
    two columns differ against the files found to differ.

Line endings (reviewer ruling, 2026-10-08): the repo stores text with LF
(`* text=auto`), so a SeaDrive file with CRLF endings arrives in the clone
with different bytes, and that normalisation is kept. A file whose SeaDrive
bytes, CRLF -> LF, reproduce the clone's bytes exactly is reported as
EOL-ONLY -- its own category, never folded into "identical" and never into
DIFFERS. The normalisation runs one way only: an LF SeaDrive file that
arrives as CRLF is DIFFERS.

Exit 0 = byte-identical throughout; 2 = every difference is EOL-ONLY;
1 = anything else. A comparison that matched zero files would pass by finding
no differences, so the population is reported and a floor is enforced.

Usage (REVERSE_IT_DIR set; moved into git with the bench suite's
scripts, port step 0, 2026-10-26):
  py -3.14 charge-interposer/harness_checks/cutover_verify.py \\
      <clone>/charge-interposer [--manifest <git_cutover_manifest.txt>]
"""

import argparse
import hashlib
import os
import re
import sys

# reverse-it's copy, through paths.py (was relative to the reverse-it root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
SEADRIVE = os.path.join(paths.reverse_it_dir(), "projects", "vtrux", "tools",
                        "interposer")
EXCLUDE_DIRS = {"__pycache__", ".pio"}
EXCLUDE_PATHS = {"firmware/datasheets"}
CLONE_ONLY_OK = {".gitignore", ".gitattributes"}
MIN_FILES = 50
CRLF = b"\r\n"
LF = b"\n"


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read(path):
    with open(path, "rb") as f:
        return f.read()


def walk(root, skip_git=False):
    """-> {relpath: sha256 of the bytes}."""
    out = {}
    for d, dirs, files in os.walk(root):
        rel_d = os.path.relpath(d, root).replace("\\", "/")
        rel_d = "" if rel_d == "." else rel_d
        keep = []
        for x in dirs:
            rel = (rel_d + "/" + x).lstrip("/")
            if x in EXCLUDE_DIRS or rel in EXCLUDE_PATHS:
                continue
            if skip_git and x == ".git":
                continue
            keep.append(x)
        dirs[:] = keep
        for fn in files:
            rel = (rel_d + "/" + fn).lstrip("/")
            out[rel] = sha_bytes(read(os.path.join(d, fn)))
    return out


def read_manifest(path):
    """-> {relpath: [sha256, ...]}: every 64-hex hash on a row, in order
    (seadrive then committed)."""
    rows = {}
    pat = re.compile(r"\b[0-9a-f]{64}\b")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            hashes = pat.findall(line)
            if not hashes:
                continue
            rest = pat.sub(" ", line).split()
            if not rest:
                continue
            p = rest[-1].replace(os.sep, "/").replace("\\", "/")
            for pre in ("charge-interposer/",
                        "projects/vtrux/tools/interposer/"):
                if p.startswith(pre):
                    p = p[len(pre):]
            rows[p] = hashes
    return rows


def compare(clone_dir, manifest=None):
    """-> (problems, eol_only, src, dst)."""
    src = walk(SEADRIVE)
    dst = walk(clone_dir, skip_git=True)
    problems = []
    if len(src) < MIN_FILES:
        problems.append("only %d SeaDrive files found under %s -- wrong "
                        "folder?" % (len(src), SEADRIVE))
    both = set(src) & set(dst)
    eol_only = []
    differ = []
    for p in sorted(both):
        if src[p] == dst[p]:
            continue
        b = read(os.path.join(SEADRIVE, p))
        if CRLF in b and sha_bytes(b.replace(CRLF, LF)) == dst[p]:
            eol_only.append(p)
        else:
            differ.append(p)
    for p in sorted(set(src) - set(dst)):
        problems.append("MISSING in clone: %s" % p)
    for p in sorted(set(dst) - set(src)):
        if os.path.basename(p) not in CLONE_ONLY_OK:
            problems.append("EXTRA in clone: %s" % p)
    for p in differ:
        problems.append("DIFFERS: %s  seadrive %s  clone %s"
                        % (p, src[p][:16], dst[p][:16]))

    if manifest:
        man = read_manifest(manifest)
        if len(man) < MIN_FILES:
            problems.append("manifest parsed to %d rows -- format not "
                            "understood, so it was not checked" % len(man))
        else:
            for p in sorted(set(src) - set(man)):
                problems.append("NOT IN MANIFEST: %s" % p)
            for p in sorted(set(man) - set(src)):
                problems.append("MANIFEST ONLY: %s" % p)
            for p in sorted(set(man) & set(src)):
                cols = man[p]
                if cols[0] != src[p]:
                    problems.append("MANIFEST seadrive column wrong: %s" % p)
                if p in dst and cols[-1] != dst[p]:
                    problems.append("MANIFEST committed column wrong: %s" % p)
            listed = sorted(p for p in man if man[p][0] != man[p][-1])
            if listed != eol_only:
                problems.append("manifest's changed-bytes rows %s are not the "
                                "EOL-ONLY files found %s" % (listed, eol_only))
    return problems, eol_only, src, dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clone_dir", help="<fresh clone>/charge-interposer")
    ap.add_argument("--manifest")
    a = ap.parse_args()
    problems, eol_only, src, dst = compare(a.clone_dir, a.manifest)
    n_same = len(set(src) & set(dst)) - len(eol_only) - sum(
        1 for p in problems if p.startswith("DIFFERS"))
    print("seadrive files (less exclusions): %d" % len(src))
    print("clone files: %d  (byte-identical: %d, EOL-ONLY: %d)"
          % (len(dst), n_same, len(eol_only)))
    for p in eol_only:
        print("  EOL-ONLY (SeaDrive CRLF -> LF gives the clone's bytes): %s"
              % p)
    if a.manifest:
        print("manifest checked: %s" % a.manifest)
    for p in problems:
        print("  " + p)
    if problems:
        print("RESULT: %d problem(s)" % len(problems))
        return 1
    if eol_only:
        print("RESULT: AGREE EXCEPT LINE ENDINGS (%d EOL-ONLY files)"
              % len(eol_only))
        return 2
    print("RESULT: AGREE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
