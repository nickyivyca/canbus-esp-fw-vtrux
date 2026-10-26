"""Does a golden-trace change move ONLY the diag schema?

Written 2026-10-07 by the gen-inhibit tester to accept or reject the 70 golden
traces the implementor regenerated in wican-fw-vtrux 6771edf (schema 5 -> 6,
the bus_on meaning change, spec 10/11). The goldens became the tester's that
day, so they are accepted only on a check, not on the implementor's word.

DECODED, BY NAME, SINCE 2026-10-24 (port plan toolkit item 10; the reviewer's
build message and reading Q3 of that day). It used to compare the two diffs'
lines byte by byte outside a hand-written table of where the schema byte sits.
Now each golden is decoded by gi_diag, every TX line of a diag id on the schema
its own latest schema page states (the DBC from git, by schema byte; a schema no
DBC states is refused), and two traces are the same when their decoded lines
are (gi_diag.normalise_trace): every signal by its current name (RenamedFrom
followed), raw values, without diag_schema_ver and diag_schema_ver_build. A
signal on one side only is a difference. So "only the schema moved" means:
  1. every changed golden, old and new, normalises to the same lines;
  2. every schema page in the OLD trace states --old-ver and every one in the
     NEW trace --new-ver;
  3. no golden is added or removed.

--same-value OLD=NEW (port step 0c, 2026-10-25): in the OLD trace only, the
signal OLD is compared as NEW, its value unchanged. 0x7F3 B0-B3 were
diag_git_hash through schema 7 and are diag_build_id from schema 8; the DBC has
no RenamedFrom between them because the meaning changed, not the bytes, so
without the option every 0x7F3 would differ. With it, a golden whose 0x7F3 kept
B0-B3 (0 in every golden: the scenarios never set the build id) passes, and one
whose value moved is still a difference. Only the pairs named are mapped.

Phase 2 reports every 0x7F1 in the NEW goldens whose bus_on (diag_flags bit1,
by name) is clear: under schema 6 that bit means "controller running", so a
scenario that models a stopped or bus-off controller would show it clear.
Reported, not judged.

The self-test feeds synthetic traces through the same checker, on a schema set
of the real schema 7 and a schema 8 made from it here, and requires the good
bump to pass and each bad one to be rejected, so the check can fail.

Usage (repo root; --repo is the repo root, --path is relative to the project folder):
  py -3.14 gen-inhibit/tools/bench/golden_schema_bump_check.py OLD NEW [--repo PATH]
      [--same-value OLD_SIGNAL=NEW_SIGNAL ...]
  py -3.14 gen-inhibit/tools/bench/golden_schema_bump_check.py --dirs OLD_DIR NEW_DIR
      (a re-bless done on the remote, not in git yet: the two golden folders)
"""
import argparse
import os
import re
import subprocess
import sys

import gi_paths  # noqa: E402  repo_root (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), check=True,
                          capture_output=True, text=True).stdout


def schema_bytes(text, schemas):
    """The schema byte of every schema page in a trace, in order."""
    out = []
    for ln in text.splitlines():
        m = gi_diag.TRACE_RE.match(ln)
        if m and int(m.group("id"), 16) in gi_diag.SCHEMA_PAGES:
            data = bytes.fromhex(m.group("data"))
            byte = gi_diag.SCHEMA_PAGES[int(m.group("id"), 16)][1]
            out.append(data[byte] if len(data) > byte else None)
    return out


BODY_RE = re.compile(r"^(?P<head>.* id=[0-9A-F]{3} dlc=\d+ \{)(?P<body>[^}]*)(?P<tail>\}.*)$")


def same_value(lines, same):
    """Normalised lines with each signal named in `same` ({old name: new name}) renamed,
    the body re-sorted as normalise_trace sorts it. Values are left alone."""
    if not same:
        return lines
    out = []
    for ln in lines:
        m = BODY_RE.match(ln)
        if not m or not m.group("body"):
            out.append(ln)
            continue
        sigs = sorted((same.get(k, k), v) for k, v in (kv.split("=", 1) for kv in m.group("body").split(" ")))
        out.append(m.group("head") + " ".join("%s=%s" % kv for kv in sigs) + m.group("tail"))
    return out


def pair_problems(old, new, old_ver, new_ver, schemas, same=None):
    """[] if `old` -> `new` moves only the schema, else the reasons. `same`: {old signal:
    new signal}, compared by value under the new name (--same-value)."""
    probs = []
    try:
        _eq, a, b = gi_diag.compare_traces(old, new, schemas)
    except gi_diag.DiagError as e:
        return ["refused: %s" % e]
    a = same_value(a, same)
    eq = a == b
    if not eq:
        first = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        probs.append("decoded lines differ (old %d, new %d lines), first at line %d: %r -> %r"
                     % (len(a), len(b), first + 1, a[first] if first < len(a) else None,
                        b[first] if first < len(b) else None))
    so, sn = schema_bytes(old, schemas), schema_bytes(new, schemas)
    if any(v != old_ver for v in so):
        probs.append("old trace states schema %s, not only %d" % (sorted(set(so), key=str), old_ver))
    if any(v != new_ver for v in sn):
        probs.append("new trace states schema %s, not only %d" % (sorted(set(sn), key=str), new_ver))
    return probs


def check_sets(old_files, new_files, old_ver, new_ver, schemas, same=None):
    """old_files, new_files: {name: text}. -> (pairs checked, [(name, reason)])."""
    problems = []
    for n in sorted(set(old_files) ^ set(new_files)):
        problems.append((n, "only in the %s set" % ("old" if n in old_files else "new")))
    n_ok = 0
    for n in sorted(set(old_files) & set(new_files)):
        if old_files[n] == new_files[n]:
            continue
        p = pair_problems(old_files[n], new_files[n], old_ver, new_ver, schemas, same)
        problems.extend((n, why) for why in p)
        n_ok += 1
    return n_ok, problems


def self_test_schemas(repo=None):
    """The real schema 7 from git, and a schema 8 made from it here that changes only
    DiagSchemaVersion (its hash recomputed)."""
    real = gi_diag.SchemaSet.from_git(repo)
    e7 = real.get(7)
    D = gi_diag.core()
    t8 = e7.text.replace('BA_ "DiagSchemaVersion" 7;', 'BA_ "DiagSchemaVersion" 8;')
    h8 = D.layout_hash(D.load(text=t8))
    t8 = t8.replace('BA_ "DiagSchemaHash" "%s";' % e7.hash, 'BA_ "DiagSchemaHash" "%s";' % h8)
    return gi_diag.SchemaSet._build({7: (e7.commit, e7.hash, e7.text), 8: ("self-test", h8, t8)},
                                    "self-test", 8)


def self_test(repo=None):
    s = self_test_schemas(repo)
    old = ("0 TX DIAG id=7F1 dlc=8 data=0701030600FF0000 ok=1\n"
           "400000 TX DIAG id=7F2 dlc=8 data=0500000000000000 ok=1\n"
           "800000 TX DIAG id=7F3 dlc=8 data=0000000001000700 ok=1\n")
    good = old.replace("data=0701", "data=0801").replace("data=0000000001000700", "data=0000000001000800")
    bad_flag = good.replace("data=0801030600", "data=0801030400")
    bad_count = good.replace("data=0500", "data=0600")
    bad_ver = good.replace("data=0801", "data=0701")
    extra = good + "900000 TX DIAG id=7F2 dlc=8 data=0500000000000000 ok=1\n"
    unknown = good.replace("data=0801", "data=0901")
    r = [check_sets({"a.trace": old}, {"a.trace": good}, 7, 8, s)[1] == [],
         check_sets({"a.trace": old}, {"a.trace": bad_flag}, 7, 8, s)[1] != [],
         check_sets({"a.trace": old}, {"a.trace": bad_count}, 7, 8, s)[1] != [],
         check_sets({"a.trace": old}, {"a.trace": bad_ver}, 7, 8, s)[1] != [],
         # the old trace already on the new schema in one page
         check_sets({"a.trace": old.replace("data=0000000001000700", "data=0000000001000800")},
                    {"a.trace": good}, 7, 8, s)[1] != [],
         check_sets({"a.trace": old}, {"a.trace": extra}, 7, 8, s)[1] != [],
         any("refused" in w for _n, w in check_sets({"a.trace": old}, {"a.trace": unknown}, 7, 8, s)[1]),
         check_sets({"a.trace": old}, {"a.trace": old, "b.trace": old}, 7, 8, s)[1] != [],
         # phase 2: bus_on (diag_flags bit1) clear is counted, set is not
         bus_on_clear({"a.trace": good, "b.trace": good.replace("data=0801030600", "data=0801030400")}, s)
         == ({"b.trace": 1}, 2)]
    # --same-value, on the real schemas 7 and 8 from git (port step 0c): 0x7F3 B0-B3 are
    # diag_git_hash on 7 and diag_build_id on 8, no RenamedFrom between them.
    real = gi_diag.SchemaSet.from_git(repo)
    pair = {"diag_git_hash": "diag_build_id"}
    r += [check_sets({"a.trace": old}, {"a.trace": good}, 7, 8, real, pair)[1] == [],
          check_sets({"a.trace": old}, {"a.trace": good}, 7, 8, real)[1] != [],
          check_sets({"a.trace": old}, {"a.trace": good.replace("data=0000000001000800", "data=0100000001000800")},
                     7, 8, real, pair)[1] != [],
          check_sets({"a.trace": old}, {"a.trace": good}, 7, 8, real, {"diag_fw_ver16": "diag_build_id"})[1] != []]
    return all(r), r


def bus_on_clear(files, schemas):
    """{name: count} of 0x7F1 pages whose diag_flags bit1 (bus_on) is clear, and the total."""
    clear, n = {}, 0
    for nm, text in sorted(files.items()):
        pages, _dec = gi_diag.decode_pages(
            [(int(m.group("t")), int(m.group("id"), 16), m.group("data"))
             for m in (gi_diag.TRACE_RE.match(ln) for ln in text.splitlines()) if m],
            schemas, decode_choices=False)
        for _t, i, p in pages:
            if i == 0x7F1 and p:
                n += 1
                if not int(p["diag_flags"]) & 0x02:
                    clear[nm] = clear.get(nm, 0) + 1
    return clear, n


def read_dir(d):
    return {fn: open(os.path.join(d, fn), encoding="ascii").read()
            for fn in sorted(os.listdir(d)) if fn.endswith(".trace")}


def read_rev(repo, rev, path):
    """The goldens at rev. path is relative to the project folder, so a rev from before
    port step 0d reads it at the repo root and a later one under gen-inhibit/."""
    path = gi_paths.path_at(rev, path, repo)
    names = [n for n in git(repo, "ls-tree", "-r", "--name-only", rev, path).split() if n.endswith(".trace")]
    return {os.path.basename(n): git(repo, "show", "%s:%s" % (rev, n)) for n in names}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old", nargs="?")
    ap.add_argument("new", nargs="?")
    ap.add_argument("--dirs", nargs=2, metavar=("OLD_DIR", "NEW_DIR"))
    ap.add_argument("--repo", default=gi_paths.repo_root())
    ap.add_argument("--path", default="test/gen_inhibit_host/golden", help="relative to the project folder")
    ap.add_argument("--old-ver", type=int, default=5)
    ap.add_argument("--new-ver", type=int, default=6)
    ap.add_argument("--same-value", action="append", default=[], metavar="OLD=NEW",
                    help="in the old trace, compare signal OLD as NEW, its value unchanged (repeatable)")
    a = ap.parse_args()
    same = dict(kv.split("=", 1) for kv in a.same_value)

    st_ok, st = self_test(a.repo)
    print("self-test (good bump accepted; flag change, counter change, wrong new version, old already new, extra line, unknown "
          "schema, added golden rejected; bus_on clear counted; --same-value: the real 7->8 bump passes with the pair, not without "
          "it, nor with a moved value or another pair): %s %s" % ("PASS" if st_ok else "FAIL", st))
    if not st_ok:
        return 2

    schemas = gi_diag.SchemaSet.from_git(a.repo)
    print("schemas: %s (%s)" % (sorted(schemas.entries), schemas.source))
    if a.dirs:
        old, new = read_dir(a.dirs[0]), read_dir(a.dirs[1])
        where = "%s -> %s" % tuple(a.dirs)
    else:
        old, new = read_rev(a.repo, a.old, a.path), read_rev(a.repo, a.new, a.path)
        where = "%s..%s" % (a.old, a.new)
        # project-relative names; a file only moved into gen-inhibit/ (port step 0d) is not a change
        allfiles = []
        for ln in git(a.repo, "diff", "--name-status", "-M", a.old, a.new).splitlines():
            st, *names = ln.split("\t")
            if st != "R100":
                f = names[-1]
                allfiles.append(f[len(gi_paths.PROJECT_DIR) + 1:] if f.startswith(gi_paths.PROJECT_DIR + "/") else f)
        outside = [f for f in allfiles if f.startswith("test/") and not f.startswith(a.path)]
        print("  test/ files changed outside %s: %s" % (a.path, outside or "none"))
    if same:
        print("same value: %s" % ", ".join("%s (old) = %s (new)" % kv for kv in sorted(same.items())))
    n, problems = check_sets(old, new, a.old_ver, a.new_ver, schemas, same)
    changed = sum(1 for k in set(old) & set(new) if old[k] != new[k])
    print("\nphase 1: %s, %d golden files changed, %d compared decoded" % (where, changed, n))
    per = {}
    for f, why in problems:
        per.setdefault(f, []).append(why)
    for f in sorted(per)[:40]:
        print("  PROBLEM %s: %d, first: %s" % (f, len(per[f]), per[f][0]))
    print("  => %s" % ("ONLY the schema moved, %d->%d, in every changed golden" % (a.old_ver, a.new_ver)
                       if not problems and n else "REJECT: %d problem(s) in %d file(s)" % (len(problems), len(per))))

    print("\nphase 2: 0x7F1 frames in the NEW goldens with bus_on (diag_flags bit1) clear")
    clear, n7f1 = bus_on_clear(new, schemas)
    print("  %d 0x7F1 frames in %d traces; bit1 clear in %d of them, in %d traces"
          % (n7f1, len(new), sum(clear.values()), len(clear)))
    for nm, c in sorted(clear.items()):
        print("    %s: %d" % (nm, c))
    return 1 if problems or not n else 0


if __name__ == "__main__":
    sys.exit(main())
