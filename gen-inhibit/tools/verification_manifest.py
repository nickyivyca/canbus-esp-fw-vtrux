#!/usr/bin/env python3
"""The hash manifest of gen-inhibit/VERIFICATION.md: one row per SeaDrive file or folder the
document cites, with its sha256, so each citation names fixed content (port plan goal 20 item 2;
the user accepted a manifest beside the document as that reference, 2026-11-07, through the
gen-inhibit reviewer).

    py -3.14 -B gen-inhibit/tools/verification_manifest.py write [--doc PATH] [--manifest PATH]
    py -3.14 -B gen-inhibit/tools/verification_manifest.py check [--doc PATH] [--manifest PATH]

`write` makes gen-inhibit/VERIFICATION-manifest.txt from the document and the files under
$REVERSE_IT_DIR; `check` makes it again in memory and compares, exit 0 when the file in the repo
is current, 1 when not (each differing row printed), 2 when nothing could be checked.

What counts as a citation: a backticked string that starts with `runs/` (the document's rule: run
files are under $REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/) or with
`$REVERSE_IT_DIR/`. A bracketed segment starting with `_` is the document's optional part
(`..._hook{on,off}[_crash]_...`), read as {,_crash}, not a glob class; shell braces expand
({a,b}, {1..6}), then a `*` or `?` globs; a cite
holding `<`, `>` or `...` is a pattern, listed without a hash. Trailing `.,;:)` is dropped. A
path that does not exist, with no glob in it, is read as a run's stem, as the document often
cites one: the files beside it named <stem>.* or <stem>_* (bench_census_<stamp> is its .json and
its _by_source.txt). Each resolved path is one row, at the line it is first cited:

    <sha256>  file     <path under $REVERSE_IT_DIR>  L<line>
    <sha256>  tree     <folder under $REVERSE_IT_DIR>/  L<line>
    -         root     runs/ or runs/chain/: folders that grow, named but never hashed
    -         living   a document that changes as work goes on (LIVING: bench-holder.md, the
                       spec, the tracker, the working log, a harness README), named but never
                       hashed: it is cited for a rule or a description, not as what a verdict
                       rests on (gen-inhibit reviewer, 2026-11-07); a batch citing another one
                       adds it to LIVING
    -         absent   one expansion of a cite whose other expansions exist ({1..6} where
                       only some arms ran)
    <sha256>  moved    <path cited> -> <path now>  L<line>: a cited file that has moved, the
                       document saying so in a dated correction or note beside it (MOVED maps
                       the cited path to the new one); the hash is the file's at its new place
    -         missing  a cite none of whose expansions exists
    -         pattern  <cite as written>

A file's sha256 is of its bytes. A folder's is gi_paths.tree_digest's: the sha256 of its files'
sorted "relpath sha256" lines joined by newlines. The header names the document's sha256, so a
changed document makes the manifest stale. Gen-inhibit tester, 2026-11-07.
"""
import argparse
import glob
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_REL = "projects/vtrux/notes/artifacts/gen-inhibit/"
ROOTS = {RUNS_REL + "runs/", RUNS_REL + "runs/chain/"}     # folders that grow: named, never hashed
NOTES = "projects/vtrux/notes/"
LIVING = {"firmware/bench-holder.md", "firmware/bench-log.jsonl", NOTES + "gen-inhibit-wican-spec.md",
          NOTES + "gen-inhibit-review-2026-09-22.md", NOTES + "gen-inhibit-wican-firmware.md",
          RUNS_REL + "README.md", RUNS_REL + "x2_jammer/README.md",
          RUNS_REL + "step1d_test_plan.md"}
# Cited files that have moved, each with the correction or note beside its citation that says so.
MOVED = {
    # The X2 hold's "TX pin while held" passage, in both its copies: the correction of
    # 2026-11-07 beside each.
    RUNS_REL + "runs/x2_hold_hold_20261010T165839.json":
        RUNS_REL + "runs/baseline_c65c45e/bench/x2/x2_hold_hold_20261010T165839.json",
    # The live-train duplicate's run files: the note of 2026-10-24 beside them ("have since
    # moved into runs/baseline_c65c45e/bench/").
    RUNS_REL + "runs/x2_jam_livetrain_20261012T185212.json":
        RUNS_REL + "runs/baseline_c65c45e/bench/x2_jam_livetrain_20261012T185212.json",
}
KINDS = ("file", "tree", "root", "living", "moved", "absent", "missing", "pattern")
CITE = re.compile(r"`([^`\s]+)`")
OPTIONAL = re.compile(r"\[(_[^\]\[]+)\]")
TRAIL = ".,;:)"


class Refused(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tree_digest(path):
    lines = []
    for dp, dns, fns in os.walk(path):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            lines.append("%s %s" % (os.path.relpath(p, path).replace("\\", "/"), sha256_file(p)))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def expand_braces(s):
    """Shell brace expansion: {a,b,c} and {n..m}, nested and repeated, left to right."""
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                body, head, tail = s[start + 1:i], s[:start], s[i + 1:]
                m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", body)
                if m:
                    a, b = int(m.group(1)), int(m.group(2))
                    step = 1 if b >= a else -1
                    alts = [str(x) for x in range(a, b + step, step)]
                else:
                    alts, d, cur = [], 0, ""
                    for c in body:
                        if c == "," and d == 0:
                            alts.append(cur)
                            cur = ""
                            continue
                        d += c == "{"
                        d -= c == "}"
                        cur += c
                    alts.append(cur)
                    if len(alts) == 1:              # "{x}" is literal in the shell
                        return [head + "{" + r for r in expand_braces(body + "}" + tail)]
                return [x for alt in alts for x in expand_braces(head + alt + tail)]
    return [s]


def citations(doc_text):
    """[(cite, line)] for every citation, in document order."""
    out = []
    for n, line in enumerate(doc_text.split("\n"), 1):
        for m in CITE.finditer(line):
            t = m.group(1).rstrip(TRAIL)
            if t.startswith("runs/") or t.startswith("$REVERSE_IT_DIR/"):
                out.append((t, n))
    return out


def _resolve(rel, ri):
    """The paths (under ri, folders ending in /) one expanded citation names; [] for none."""
    full = os.path.join(ri, *rel.rstrip("/").split("/"))
    if any(c in rel for c in "*?["):
        hits = sorted(glob.glob(full))
    elif os.path.exists(full):
        hits = [full]
    else:
        d, base = os.path.split(full)
        hits = sorted(os.path.join(d, f) for f in (os.listdir(d) if os.path.isdir(d) else [])
                      if f.startswith(base + ".") or f.startswith(base + "_")) if base else []
        hits = [h for h in hits if os.path.isfile(h)]
    return [os.path.relpath(h, ri).replace("\\", "/") + ("/" if os.path.isdir(h) else "") for h in hits]


def rows(doc_text, ri):
    """The manifest's rows, sorted by path, one per resolved path (or pattern)."""
    seen = {}
    for cite, n in citations(doc_text):
        if "<" in cite or ">" in cite or "..." in cite:
            seen.setdefault(("pattern", cite), n)
            continue
        rel0 = cite[len("$REVERSE_IT_DIR/"):] if cite.startswith("$REVERSE_IT_DIR/") else RUNS_REL + cite
        rel0 = OPTIONAL.sub(lambda m: "{," + m.group(1) + "}", rel0)
        expanded = [(rel, _resolve(rel, ri)) for rel in expand_braces(rel0)]
        any_found = any(hits for _rel, hits in expanded)
        for rel, hits in expanded:
            if not hits and rel in MOVED and os.path.isfile(os.path.join(ri, *MOVED[rel].split("/"))):
                seen.setdefault(("moved", rel), n)
            elif not hits:
                seen.setdefault(("absent" if any_found else "missing", rel), n)
            for h in hits:
                kind = "root" if h in ROOTS else "living" if h in LIVING else "path"
                seen.setdefault((kind, h), n)
    out = []
    for (kind, path), n in sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if kind == "path":
            full = os.path.join(ri, *path.rstrip("/").split("/"))
            if path.endswith("/"):
                out.append("%s  tree     %s  L%d" % (tree_digest(full), path, n))
            else:
                out.append("%s  file     %s  L%d" % (sha256_file(full), path, n))
        elif kind == "moved":
            full = os.path.join(ri, *MOVED[path].split("/"))
            out.append("%s  moved    %s -> %s  L%d" % (sha256_file(full), path, MOVED[path], n))
        else:
            out.append("-  %-7s  %s  L%d" % (kind, path, n))
    return out


def manifest(doc_raw, ri, doc_name="gen-inhibit/VERIFICATION.md"):
    text = doc_raw.decode("utf-8")
    body = rows(text, ri)
    head = ["# The hash manifest of %s: every SeaDrive file or folder it cites, by path under"
            % doc_name,
            "# $REVERSE_IT_DIR. Written by gen-inhibit/tools/verification_manifest.py; do not edit.",
            "# document sha256 %s" % hashlib.sha256(doc_raw).hexdigest(),
            "# %d rows: %s" % (len(body), ", ".join("%d %s" % (sum(1 for r in body if r.split()[1] == k), k)
                                                     for k in KINDS))]
    return "\n".join(head + body) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="VERIFICATION.md's hash manifest.")
    ap.add_argument("mode", choices=("write", "check"))
    ap.add_argument("--doc", default=os.path.join(os.path.dirname(HERE), "VERIFICATION.md"))
    ap.add_argument("--manifest", default=os.path.join(os.path.dirname(HERE), "VERIFICATION-manifest.txt"))
    a = ap.parse_args(argv)
    try:
        ri = os.environ.get("REVERSE_IT_DIR", "").strip()
        if not ri or not os.path.isdir(ri):
            raise Refused("REVERSE_IT_DIR is not set to a folder")
        if not os.path.isfile(a.doc):
            raise Refused("the document is not a file: %s" % a.doc)
        with open(a.doc, "rb") as f:
            doc = f.read()
        want = manifest(doc, ri)
    except (Refused, UnicodeDecodeError) as e:
        print("REFUSED: %s" % e)
        return 2
    if a.mode == "write":
        with open(a.manifest, "w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        print("wrote %s: %s" % (a.manifest, want.split("\n")[3][2:]))
        return 0
    if not os.path.isfile(a.manifest):
        print("REFUSED: the manifest is not a file: %s" % a.manifest)
        return 2
    with open(a.manifest, encoding="utf-8", newline="") as f:
        have = f.read()
    if have == want:
        print("OK: the manifest is current (%s)" % want.split("\n")[3][2:])
        return 0
    hs, ws = have.split("\n"), want.split("\n")
    for line in [l for l in hs if l not in ws][:40]:
        print("STALE    %s" % line)
    for line in [l for l in ws if l not in hs][:40]:
        print("NOW      %s" % line)
    print("NOT CURRENT: the manifest differs from the document and the files")
    return 1


if __name__ == "__main__":
    sys.exit(main())
