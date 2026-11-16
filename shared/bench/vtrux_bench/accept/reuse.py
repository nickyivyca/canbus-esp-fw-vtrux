"""A required suite's earlier run (ACCEPT-SPEC A17; user, 2026-11-16, in the
charge-interposer reviewer's session).

When a suite run's suite requires another (Suite.requires: the bench suite
requires a whole emulated run on the same product digest), the list may name
an earlier acceptance record's run of the required suite instead of running
it again, as a [[reuse]]:

    [[reuse]]  id, tree, suite (the adapter's), record (the earlier
               acceptance record's folder in the runs folder), run (that
               record's [[suite_run]] id)

The list is checked first (validate()): a [[reuse]] is of a suite that one
of the list's suite runs requires (the adapter's SUITE_REQUIRES), and no
suite run of the list runs that suite too. The runner checks each at the
start, after the trees are made and before any premise, build, lock or run
(check()), and refuses the whole acceptance (exit 2, the record left open)
unless all of these hold:
  - the earlier record is closed: its README.md and SHA256SUMS are there,
    and README.md and the run's record.json match SHA256SUMS;
  - that run was of the whole suite (its record is not a subset run and
    has no NOT-RUN result that is not a declared skip: the chain runner's
    requires_whole), and finished (its record.json has its end);
  - its comparison held in its own closed record: the earlier list's
    [[compare]]s with that run as a side, at least one, each "held" in the
    earlier README;
  - its product digest (its record.json's) equals this tree's;
  - the harness the required suite runs is unchanged: the digest over the
    files this tree's adapter names as that suite's harness
    (adapter.harness_files(suite, tree), A15), read from git objects at
    each tree's commits (a shared/<component>/ path at that tree's commit
    of the component), is the same in the earlier run's tree and in this
    one; a file missing from either tree makes them differ.

The cited run then stands as this acceptance's run of that suite, read where
it is: the suite runs that require it are told its folder (the chain
runner's settings.requires_run), comparisons naming the [[reuse]]'s id read
its record there, and nothing is copied into, or written in, either record.
The record names the earlier record, the run and both pairs of digests
(readme_lines()).

The digest over a list of files: sha256 over the paths sorted, each as the
path, NUL, the file's bytes (or the word MISSING, for the reason, though a
missing file refuses before the digest counts), NUL.

RULES: each is switched off by a mutant in tests/test_accept_reuse.py,
which must then fail.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib

RULES = {k: True for k in (
    "required_by", "alone", "closed", "sums", "finished", "whole", "compare_named",
    "compare_held", "product", "harness", "missing_differs", "shared_at_tree",
    "in_place", "named")}

TREE_LINE = re.compile(r"^- tree (\S+): project ([0-9a-f]{40}) \(tree [0-9a-f]+\), "
                       r"product digest (\S+)$")
SHARED_LINE = re.compile(r"^  - shared (\S+): ([0-9a-f]{40}) \(tree [0-9a-f]+\)$")
LIST_LINE = re.compile(r"^- list: `([^`]+)` at ([0-9a-f]{40}), sha256 [0-9a-f]+$")
COMPARE_LINE = re.compile(r"^- compare (\S+) \([^)]*\): (held|DID NOT HOLD)$")
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class ReuseError(Exception):
    pass


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def parse_readme(text):
    """What a closed record's README.md (close.readme) says that A17 needs:
    {"list": (path, commit) or None, "trees": {name: {"project", "product_digest",
    "shared" {component: commit}}}, "compares": {id: held}}."""
    out = {"list": None, "trees": {}, "compares": {}}
    tree = None
    for ln in text.splitlines():
        m = LIST_LINE.match(ln)
        if m and out["list"] is None:
            out["list"] = (m.group(1), m.group(2))
            continue
        m = TREE_LINE.match(ln)
        if m:
            tree = {"project": m.group(2), "product_digest": m.group(3), "shared": {}}
            out["trees"][m.group(1)] = tree
            continue
        m = SHARED_LINE.match(ln)
        if m and tree is not None:
            tree["shared"][m.group(1)] = m.group(2)
            continue
        if not ln.startswith("  "):
            tree = None
        m = COMPARE_LINE.match(ln)
        if m:
            out["compares"][m.group(1)] = m.group(2) == "held"
    return out


def read_sums(folder):
    """{relative path: sha256} from a record's SHA256SUMS."""
    sums = {}
    with open(os.path.join(folder, "SHA256SUMS"), encoding="utf-8") as f:
        for ln in f.read().splitlines():
            h, _sep, rel = ln.partition("  ")
            sums[rel] = h
    return sums


def file_at(repo, tree, rel):
    """The bytes of a repo-relative path in a tree given by its commits
    ({"project": commit, "shared": {component: commit}}), from git objects:
    a shared/<component>/ path at the tree's commit of that component, any
    other at the project's. None if the tree has no such file."""
    parts = rel.split("/")
    commit = tree["project"]
    if RULES["shared_at_tree"] and len(parts) > 2 and parts[0] == "shared":
        commit = tree["shared"].get(parts[1])
        if commit is None:
            return None
    r = _git(repo, "cat-file", "blob", "%s:%s" % (commit, rel))
    return r.stdout if r.returncode == 0 else None


def harness_digest(repo, tree, files):
    """(digest, {path: sha256 or None}) over files in a tree (file_at)."""
    h = hashlib.sha256()
    per = {}
    for rel in sorted(set(files)):
        b = file_at(repo, tree, rel)
        per[rel] = hashlib.sha256(b).hexdigest() if b is not None else None
        h.update(rel.encode("utf-8") + b"\0" + (b if b is not None else b"MISSING") + b"\0")
    return h.hexdigest(), per


def tree_commits(tree_record):
    """A made tree's commits, from its record (trees.py), for file_at."""
    return {"project": tree_record["project"]["commit"],
            "shared": {c: sc["commit"] for c, sc in tree_record["shared"].items()}}


def validate(alist, adapter):
    """The list's [[reuse]]s, before any tree is made: each is of a suite
    that a suite run of the list requires (adapter.SUITE_REQUIRES: {suite:
    the suite it requires}, A15), and no suite run of the list runs that
    suite too (the requirement would have two runs to read). ReuseError
    naming every problem."""
    errs = []
    req = dict(getattr(adapter, "SUITE_REQUIRES", None) or {})
    run_suites = {r.get("suite") for r in alist.suite_runs}
    for u in alist.reuses:
        if RULES["required_by"] and u["suite"] not in {req.get(s) for s in run_suites}:
            errs.append("reuse %s: no suite run of the list is of a suite that requires "
                        "suite %s (A17: an earlier run stands only for a required suite; "
                        "the adapter's SUITE_REQUIRES: %s)" % (u["id"], u["suite"], req))
        if RULES["alone"] and u["suite"] in run_suites:
            errs.append("reuse %s: a suite run of the list runs suite %s too"
                        % (u["id"], u["suite"]))
    if len({u["suite"] for u in alist.reuses}) != len(alist.reuses):
        errs.append("two [[reuse]]s name one suite")
    if errs:
        raise ReuseError("list %s at %s refused:\n  %s" % (alist.path, alist.commit[:12],
                                                          "\n  ".join(errs)))


def check(repo, entry, trees, adapter, runs_root, list_reader):
    """The [[reuse]] entry checked (see the module's docstring). Its record:
    {"id", "suite", "suite_name", "record", "run", "dir", "compares",
    "product" {earlier, this}, "harness" {earlier, this, files}}; ReuseError
    naming the reason otherwise. list_reader(repo, "<commit>:<path>") gives
    the earlier list's bytes (listfile.read_list's third value)."""
    rid, run_id = entry["record"], entry["run"]
    if not (NAME.match(rid) and NAME.match(run_id)):
        raise ReuseError("reuse %s: record %r and run %r must be plain names"
                         % (entry["id"], rid, run_id))
    where = "reuse %s (record %s, run %s)" % (entry["id"], rid, run_id)
    folder = os.path.join(runs_root, rid)
    rdir = os.path.join(folder, "runs", "%s-%s" % (rid, run_id))
    readme_p = os.path.join(folder, "README.md")
    rec_p = os.path.join(rdir, "record.json")
    if RULES["closed"] and not (os.path.isfile(readme_p)
                                and os.path.isfile(os.path.join(folder, "SHA256SUMS"))):
        raise ReuseError("%s: the earlier record is not a closed acceptance record "
                         "(no README.md or SHA256SUMS in %s)" % (where, folder))
    if not os.path.isfile(rec_p):
        raise ReuseError("%s: the run did not finish: no record.json in %s" % (where, rdir))
    if RULES["sums"]:
        sums = read_sums(folder)
        for rel, p in (("README.md", readme_p),
                       ("runs/%s-%s/record.json" % (rid, run_id), rec_p)):
            if sums.get(rel) != _sha256(p):
                raise ReuseError("%s: %s does not match the record's SHA256SUMS%s: the "
                                 "closed record has changed" % (
                                     where, rel, "" if rel in sums else " (not listed)"))
    with open(readme_p, encoding="utf-8") as f:
        readme = parse_readme(f.read())
    with open(rec_p, encoding="utf-8") as f:
        r = json.load(f)
    if readme["list"] is None:
        raise ReuseError("%s: the earlier README names no list" % where)
    lpath, lcommit = readme["list"]
    try:
        earlier = tomllib.loads(list_reader(repo, "%s:%s" % (lcommit, lpath)).decode("utf-8"))
    except Exception as e:
        raise ReuseError("%s: the earlier list %s at %s cannot be read: %s"
                         % (where, lpath, lcommit[:12], e))
    sr = next((x for x in earlier.get("suite_run") or [] if x.get("id") == run_id), None)
    if sr is None:
        raise ReuseError("%s: the earlier list has no [[suite_run]] %s" % (where, run_id))
    if sr.get("suite") != entry["suite"]:
        raise ReuseError("%s: the earlier run is of suite %s, not %s"
                         % (where, sr.get("suite"), entry["suite"]))
    # whole and finished
    if RULES["finished"] and not r.get("ended"):
        raise ReuseError("%s: the run did not finish: its record.json has no end" % where)
    if RULES["whole"]:
        sel = r.get("selection") or {}
        if r.get("subset") or sel.get("groups") or sel.get("checks"):
            raise ReuseError("%s: the run was a subset run (subset %s, %d case(s)): the "
                             "whole suite must have run" % (
                                 where, r.get("subset"), len(r.get("subset_cases") or [])))
        nr = [x["id"] for x in r.get("results") or [] if x["verdict"] == "NOT-RUN"
              and not str(x.get("detail", "")).startswith("skipped: ")]
        if nr:
            raise ReuseError("%s: the run has %d check(s) NOT-RUN that are not declared "
                             "skips (%s): the whole suite must have run"
                             % (where, len(nr), ", ".join(nr[:3])))
    # its comparison held, in its own closed record
    named = [c.get("id") for c in earlier.get("compare") or []
             if run_id in (c.get("a"), c.get("b"))]
    if RULES["compare_named"] and not named:
        raise ReuseError("%s: no comparison in the earlier list has the run as a side, "
                         "so none held for it" % where)
    if RULES["compare_held"]:
        bad = [c for c in named if not readme["compares"].get(c)]
        if bad:
            raise ReuseError("%s: its comparison did not hold in the earlier record: %s"
                             % (where, ", ".join(
                                 "%s %s" % (c, "DID NOT HOLD" if c in readme["compares"]
                                            else "(not in its README)") for c in bad)))
    # the product
    this_tree = trees[entry["tree"]]
    p_this = this_tree["record"]["product_digest"]
    p_earlier = (r.get("code") or {}).get("product_digest")
    if RULES["product"] and (not p_earlier or p_earlier != p_this):
        raise ReuseError("%s: the product changed: its product digest %s, this tree's %s"
                         % (where, p_earlier, p_this))
    # the harness the required suite runs, this tree's list read in both trees
    et = readme["trees"].get(sr.get("tree"))
    if et is None:
        raise ReuseError("%s: the earlier README names no tree %s" % (where, sr.get("tree")))
    files = list(adapter.harness_files(entry["suite"], this_tree))
    if not files:
        raise ReuseError("%s: the adapter names no harness files for suite %s (A15)"
                         % (where, entry["suite"]))
    h_this, per_this = harness_digest(repo, tree_commits(this_tree["record"]), files)
    h_earlier, per_earlier = harness_digest(repo, et, files)
    if RULES["missing_differs"]:
        gone = sorted(p for p in files if per_this[p] is None or per_earlier[p] is None)
        if gone:
            raise ReuseError("%s: the harness differs: %d file(s) missing from a tree: %s"
                             % (where, len(gone), ", ".join(
                                 "%s (missing %s)" % (p, "from this tree" if per_this[p] is None
                                                      else "from the earlier tree")
                                 for p in gone[:5])))
    if RULES["harness"] and h_this != h_earlier:
        changed = sorted(p for p in files if per_this[p] != per_earlier[p])
        raise ReuseError("%s: the harness of suite %s changed: digest %s in the earlier "
                         "tree, %s in this one; %d file(s) differ: %s"
                         % (where, entry["suite"], h_earlier, h_this, len(changed),
                            ", ".join(changed[:5])))
    return {"id": entry["id"], "suite": entry["suite"], "suite_name": r.get("suite"),
            "record": rid, "run": run_id, "dir": rdir, "compares": named,
            "product": {"earlier": p_earlier, "this": p_this},
            "harness": {"earlier": h_earlier, "this": h_this, "files": len(set(files))},
            "results": r.get("results") or []}


def requires_run(reused, rec_path):
    """settings.requires_run for the suite runs: {Suite.name: the cited run's
    folder}, read where it is. (With rule in_place off, the mutant: a copy in
    this record's runs/.)"""
    out = {}
    for u in reused:
        d = u["dir"]
        if not RULES["in_place"]:
            d = os.path.join(rec_path, "runs", os.path.basename(u["dir"]))
            shutil.copytree(u["dir"], d)
        out[u["suite_name"]] = d
    return out


def readme_lines(reused):
    """The record's lines for each [[reuse]]: the earlier record, the run
    and both pairs of digests (A17)."""
    out = []
    for u in reused or []:
        if not RULES["named"]:
            out.append("- reused suite run %s" % u["id"])
            continue
        out += ["- reused suite run %s (suite %s): acceptance record %s, its run %s, "
                "read in place (A17); its comparison held there: %s" % (
                    u["id"], u["suite"], u["record"], u["run"], ", ".join(u["compares"])),
                "  - product digest: earlier %s, this tree %s" % (
                    u["product"]["earlier"], u["product"]["this"]),
                "  - harness digest (%d files): earlier %s, this tree %s" % (
                    u["harness"]["files"], u["harness"]["earlier"], u["harness"]["this"])]
    return out
