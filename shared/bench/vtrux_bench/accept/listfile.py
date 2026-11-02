"""The acceptance list: read from git at a named commit, checked against the
project's adapter, and expanded (ACCEPT-SPEC A2, A3, A4 exact commits, A8).

A list is a TOML file, <project>/acceptance/<step>.toml, committed on the
project's branch and owned by its reviewer (A2). It is named on the command
line as <commit>:<path> and read with `git show` there, never from a working
tree; the result records the commit in full and the sha256 of the list's
bytes.

    [step]          id, project, ordered_by, ordered_on, spec (list), verification
    [trees.<name>]  project = <commit>; shared = {<component> = <commit or tag>}
                    (overrides the project's pin for that tree); host
    [[build]]       id, tree, target (the adapter's), options (the target's)
    [[check]]       id, tree or trees (one check per tree, id@tree), run (a list:
                    a committed file in the tree, then its arguments), shell,
                    host, cls, env, timeout_s, expect, judge, after, note
    [[set]]         id, tree, select (selectors over the adapter's catalogue:
                    "tag:<t>", "id:<glob>", "all"), host, after; one check per
                    entry picked, id <set>.<entry>
    [[suite_run]]   id, tree, suite (the adapter's), groups, checks, whole,
                    rerun_flagged, images_from (a build), after
    [baseline]      <check or suite run id> = <run or record name>
    [[compare]]     id, comparer (the adapter's), a, b (item ids, or
                    "baseline:<id>"), expect, tolerance, differences, mask
    [[canfail]]     id, target, control (checks), mutants = {from = <committed
                    file in the target's tree>, ids, mode}, expect
    [[premise]]     id, kind, tree, commit, value, board, expect, items
    [[hand]]        id, text, boards, after

A key the runner does not know is refused, at every level, so a list carries
no patch and no script text (A3). A command is a committed file in its tree:
a path under shared/<component>/ is looked for at the tree's commit of that
component (the list's override, else the project's pin from the adapter),
any other path at the tree's project commit. Each tree is at an exact
commit: a commit (7 to 40 hex digits), or for a shared component a tag; a
branch name is refused, since a branch moves (A4).

Which harness tests a step runs is the list's selector over the adapter's
committed catalogue, and only that (A8): the runner adds and drops none.

The adapter (A15) gives: PROJECT, TARGETS {name: {"options": [...]}}, SHELLS,
default_shell(path), JUDGES, COMPARERS, SUITES {name: "<path>:<attr>"},
catalogue(repo, commit) -> [entry] and shared_pins(repo, commit) ->
{component: commit or tag}.

RULES: each is switched off by a mutant in tests/test_accept_list.py, which
must then fail.
"""

import datetime
import fnmatch
import hashlib
import os
import re
import subprocess
import tomllib

from ..run.model import ID_RE, Verdict

RULES = {k: True for k in (
    "full_commit", "list_sha256", "committed_only", "unknown_keys", "required",
    "committed_files", "shared_at_tree", "exact_commit", "refs", "adapter_names",
    "project", "selector_only", "empty_selector", "unique_ids")}

HEX = re.compile(r"^[0-9a-fA-F]{7,40}$")

STR, LIST, DICT, NUM, BOOL, DATE = "a string", "a list of strings", \
    "a table of strings", "a number", "true or false", "a date"

SCHEMA = {
    "step": {"id": (STR, True), "project": (STR, True), "ordered_by": (STR, True),
             "ordered_on": (DATE, True), "spec": (LIST, True),
             "verification": (STR, True)},
    "tree": {"project": (STR, True), "shared": (DICT, False), "host": (STR, False)},
    "build": {"id": (STR, True), "tree": (STR, True), "target": (STR, True),
              "options": ("a table", False), "note": (STR, False)},
    "check": {"id": (STR, True), "tree": (STR, False), "trees": (LIST, False),
              "run": (LIST, True), "shell": (STR, False), "host": (STR, False),
              "cls": (STR, False), "env": (DICT, False), "timeout_s": (NUM, False),
              "expect": (STR, False), "judge": (STR, False), "after": (LIST, False),
              "note": (STR, False)},
    "set": {"id": (STR, True), "tree": (STR, True), "select": (LIST, True),
            "host": (STR, False), "after": (LIST, False)},
    "suite_run": {"id": (STR, True), "tree": (STR, True), "suite": (STR, True),
                  "groups": (LIST, False), "checks": (LIST, False),
                  "whole": (BOOL, False), "rerun_flagged": (BOOL, False),
                  "images_from": (STR, False), "after": (LIST, False)},
    "compare": {"id": (STR, True), "comparer": (STR, True), "a": (STR, True),
                "b": (STR, True), "expect": (STR, True),
                "tolerance": ("a table", False), "differences": (LIST, False),
                "mask": (LIST, False)},
    "canfail": {"id": (STR, True), "target": (STR, True), "control": (STR, False),
                "mutants": ("a table", True), "expect": (STR, True)},
    "mutants": {"from": (STR, True), "ids": (LIST, False), "mode": (STR, False)},
    "premise": {"id": (STR, True), "kind": (STR, True), "tree": (STR, False),
                "commit": (STR, False), "value": (STR, False), "board": (STR, False),
                "expect": (STR, False), "items": (LIST, False)},
    "hand": {"id": (STR, True), "text": (STR, True), "boards": (LIST, False),
             "after": (LIST, False)},
}
# top-level: key -> (kind, the schema of its entries)
TOP = {"step": ("table", "step"), "trees": ("tables", "tree"),
       "build": ("array", "build"), "check": ("array", "check"),
       "set": ("array", "set"), "suite_run": ("array", "suite_run"),
       "baseline": ("strings", None), "compare": ("array", "compare"),
       "canfail": ("array", "canfail"), "premise": ("array", "premise"),
       "hand": ("array", "hand")}
CLS = ("light", "build", "wallclock")
COMPARE_EXPECT = ("identical", "same-verdicts", "within", "listed")
CANFAIL_EXPECT = ("new-fail-line", "verdict-flips", "fails")
PREMISES = ("product_digest_equal", "product_digest_same_as", "board_read")


class ListError(Exception):
    pass


class AcceptList:
    """A checked list. checks are expanded: one per tree for a check on
    several (id@tree), one per catalogue entry a set picks (<set>.<entry>)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def q(s):
    return '"%s"' % (s,)


def _git(repo, *args, binary=False):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=not binary)


def _type_ok(v, want):
    if want == STR:
        return isinstance(v, str)
    if want == LIST:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if want == DICT:
        return isinstance(v, dict) and all(isinstance(x, str) for x in v.values())
    if want == NUM:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if want == BOOL:
        return isinstance(v, bool)
    if want == DATE:
        return isinstance(v, (str, datetime.date))
    return isinstance(v, dict)


def _fields(where, d, schema, errs):
    """Check d against schema: unknown keys, required keys, types."""
    if not isinstance(d, dict):
        errs.append("%s: must be a table" % where)
        return {}
    sch = SCHEMA[schema]
    for k, v in d.items():
        if k not in sch:
            if RULES["unknown_keys"]:
                errs.append("%s: unknown key %s (a list carries no patch or "
                            "script text, A3)" % (where, q(k)))
            continue
        if not _type_ok(v, sch[k][0]):
            errs.append("%s: %s must be %s" % (where, k, sch[k][0]))
    if RULES["required"]:
        for k, (_t, req) in sch.items():
            if req and k not in d:
                errs.append("%s: %s is required" % (where, k))
    return d


def read_list(repo, spec):
    """(full commit, path, bytes) of the list named <commit>:<path>, read
    with git show there (A2)."""
    rev, sep, path = spec.partition(":")
    if len(rev) == 1 and sep and os.name == "nt":          # C:\... is no <commit>:
        rev, sep, path = "", "", spec
    if not RULES["committed_only"]:
        p = path if sep else spec
        p = p if os.path.isabs(p) else os.path.join(repo, p)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return rev or "working-tree", os.path.relpath(p, repo).replace("\\", "/"), f.read()
    if not sep or not rev or not path:
        raise ListError("the list must be named <commit>:<path> and committed "
                        "there (A2): %s" % spec)
    r = _git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    if r.returncode:
        raise ListError("no commit %s in %s" % (q(rev), repo))
    full = r.stdout.strip()
    path = path.replace("\\", "/")
    b = _git(repo, "show", "%s:%s" % (full, path), binary=True)
    if b.returncode:
        raise ListError("the list %s is not committed at %s (A2): it is read "
                        "from git, never from a working tree" % (q(path), full[:12]))
    return (full if RULES["full_commit"] else rev), path, b.stdout


def resolve(repo, rev, tags_ok):
    """The full commit of rev, which must be exact: a commit's hex, or with
    tags_ok a tag. None if it is not."""
    if not isinstance(rev, str) or not rev:
        return None
    if RULES["exact_commit"]:
        if HEX.match(rev):
            ref = rev
        elif tags_ok and _git(repo, "show-ref", "--verify", "--quiet",
                              "refs/tags/" + rev).returncode == 0:
            ref = "refs/tags/" + rev
        else:
            return None
    else:
        ref = rev
    r = _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    if r.returncode:
        return None
    full = r.stdout.strip()
    if RULES["exact_commit"] and HEX.match(rev) and not full.startswith(rev.lower()):
        return None
    return full


def _committed(repo, commit, path):
    r = _git(repo, "cat-file", "-t", "%s:%s" % (commit, path))
    return r.returncode == 0 and r.stdout.strip() == "blob"


def _commit_for(tree, path):
    """The commit a path is looked for at in a tree: a shared component's
    for shared/<component>/..., else the project's."""
    parts = path.split("/")
    if RULES["shared_at_tree"] and len(parts) > 2 and parts[0] == "shared":
        return tree["shared"].get(parts[1]), "shared component %s" % q(parts[1])
    return tree["project"], "the project"


def load(repo, spec, adapter):
    """The checked, expanded list; ListError naming every problem."""
    commit, path, raw = read_list(repo, spec)
    sha = hashlib.sha256(raw if RULES["list_sha256"] else path.encode()).hexdigest()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ListError("list %s is not TOML: %s" % (q(path), e))
    errs = []
    for k, v in data.items():
        if k not in TOP:
            if RULES["unknown_keys"]:
                errs.append("unknown top-level key %s (a list carries no patch or "
                            "script text, A3)" % q(k))
            continue
        kind = TOP[k][0]
        if kind == "array" and not (isinstance(v, list) and all(isinstance(x, dict) for x in v)):
            errs.append("%s must be an array of tables ([[%s]])" % (k, k))
        elif kind in ("table", "tables", "strings") and not isinstance(v, dict):
            errs.append("%s must be a table" % k)
    for k in ("step", "trees"):
        if k not in data and RULES["required"]:
            errs.append("[%s] is required" % k)
    if errs:
        raise ListError("list %s at %s refused:\n  %s" % (path, commit[:12], "\n  ".join(errs)))

    step = dict(_fields("[step]", data.get("step", {}), "step", errs))
    if isinstance(step.get("ordered_on"), datetime.date):
        step["ordered_on"] = step["ordered_on"].isoformat()
    if RULES["project"] and step.get("project") not in (None, adapter.PROJECT):
        errs.append("[step]: project %s is not this adapter's, %s"
                    % (q(step.get("project")), q(adapter.PROJECT)))

    # trees, each at exact commits (A4)
    trees = {}
    for name, t in (data.get("trees") or {}).items():
        where = "[trees.%s]" % name
        t = _fields(where, t, "tree", errs)
        out = {"host": t.get("host", "local"), "shared": {}}
        proj = resolve(repo, t.get("project"), tags_ok=False)
        if "project" in t and proj is None:
            errs.append("%s: project %s is not an exact commit in the repo (a "
                        "commit's hex; a branch moves)" % (where, q(t.get("project"))))
        out["project"] = proj
        pins = {}
        if proj:
            try:
                pins = dict(adapter.shared_pins(repo, proj) or {})
            except Exception as e:
                errs.append("%s: the adapter could not read the shared pins at "
                            "%s: %r" % (where, proj[:12], e))
        pins.update(t.get("shared") or {})
        for comp, rev in sorted(pins.items()):
            full = resolve(repo, rev, tags_ok=True)
            if full is None:
                errs.append("%s: shared component %s at %s is not an exact commit "
                            "or tag (a branch moves)" % (where, q(comp), q(rev)))
            out["shared"][comp] = full
        trees[name] = out

    def tree_ref(where, name):
        if name not in trees:
            if RULES["refs"]:
                errs.append("%s: tree %s is not in [trees]" % (where, q(name)))
            return None
        return trees[name]

    def committed_file(where, tree, p):
        if not RULES["committed_files"] or tree is None or tree.get("project") is None:
            return
        c, what = _commit_for(tree, p)
        if c is None:
            errs.append("%s: %s has no commit for %s" % (where, what, q(p)))
        elif not _committed(repo, c, p):
            errs.append("%s: %s is not a committed file at %s (%s) (A3: a command "
                        "is a committed file in its tree)" % (where, q(p), c[:12], what))

    def adapter_name(where, kind, name, names):
        if RULES["adapter_names"] and name not in names:
            errs.append("%s: %s %s is not the adapter's (%s)"
                        % (where, kind, q(name), ", ".join(sorted(names))))

    builds = []
    for b in data.get("build") or []:
        where = "build %s" % q(b.get("id"))
        b = dict(_fields(where, b, "build", errs))
        tree_ref(where, b.get("tree"))
        tgt = b.get("target")
        adapter_name(where, "build target", tgt, adapter.TARGETS)
        for o in (b.get("options") or {}):
            if tgt in adapter.TARGETS:
                adapter_name(where, "option of %s" % tgt, o, adapter.TARGETS[tgt]["options"])
        builds.append(b)

    checks = []

    def add_check(where, c, tree_name):
        tree = tree_ref(where, tree_name)
        run = c.get("run")
        if run is not None and not (isinstance(run, list) and run
                                    and all(isinstance(x, str) for x in run)):
            errs.append("%s: run must be a list: a committed file, then its "
                        "arguments" % where)
            run = None
        if run:
            committed_file(where, tree, run[0])
        shell = c.get("shell") or (adapter.default_shell(run[0]) if run else None)
        if run and shell is None:
            errs.append("%s: names no shell, and the adapter has none for %s"
                        % (where, q(run[0])))
        elif shell is not None:
            adapter_name(where, "shell", shell, adapter.SHELLS)
        if "judge" in c:
            adapter_name(where, "judge", c["judge"], adapter.JUDGES)
        if c.get("cls", "light") not in CLS:
            errs.append("%s: cls %s is not one of %s" % (where, q(c.get("cls")), ", ".join(CLS)))
        if c.get("expect", "PASS") not in Verdict.JUDGE:
            errs.append("%s: expect %s is not a verdict" % (where, q(c.get("expect"))))
        out = dict(c)
        out.update(tree=tree_name, run=run, shell=shell, cls=c.get("cls", "light"),
                   env=dict(c.get("env") or {}), timeout_s=c.get("timeout_s", 3600),
                   expect=c.get("expect", "PASS"), judge=c.get("judge"),
                   after=list(c.get("after") or []), host=c.get("host", (tree or {}).get("host", "local")))
        out.pop("trees", None)
        checks.append(out)

    for c in data.get("check") or []:
        where = "check %s" % q(c.get("id"))
        c = _fields(where, c, "check", errs)
        if ("tree" in c) == ("trees" in c):
            errs.append("%s: give tree or trees, one of them" % where)
            continue
        if "run" not in c:
            continue
        if "trees" in c:
            for tn in c["trees"]:
                add_check("check %s" % q("%s@%s" % (c.get("id"), tn)),
                          dict(c, id="%s@%s" % (c.get("id"), tn)), tn)
        else:
            add_check(where, c, c["tree"])

    sets = []
    for s in data.get("set") or []:
        where = "set %s" % q(s.get("id"))
        s = _fields(where, s, "set", errs)
        tree = tree_ref(where, s.get("tree"))
        sets.append(dict(s))
        if tree is None or tree.get("project") is None or "select" not in s:
            continue
        entries = adapter.catalogue(repo, tree["project"])
        picked = []
        for sel in s["select"]:
            kind, _c, arg = sel.partition(":")
            if sel == "all":
                hits = list(entries)
            elif kind == "tag" and arg:
                hits = [e for e in entries if arg in (e.get("tags") or [])]
            elif kind == "id" and arg:
                hits = [e for e in entries if fnmatch.fnmatchcase(e["id"], arg)]
            else:
                errs.append("%s: selector %s is not tag:<t>, id:<glob> or all" % (where, q(sel)))
                continue
            if not hits and RULES["empty_selector"]:
                errs.append("%s: selector %s picks nothing from the catalogue" % (where, q(sel)))
            picked += [e for e in hits if e not in picked]
        if not RULES["selector_only"]:
            picked = list(entries)
        for e in sorted(picked, key=lambda e: e["id"]):
            cid = "%s.%s" % (s["id"], e["id"])
            add_check("check %s (catalogue entry %s)" % (q(cid), q(e["id"])), dict(
                id=cid, run=[e["path"]] + list(e.get("args") or []), shell=e.get("shell"),
                env=dict(e.get("env") or {}), timeout_s=e.get("timeout_s", 3600),
                cls=e.get("cls", "light"), host=s.get("host", tree.get("host", "local")),
                after=list(s.get("after") or []), tags=list(e.get("tags") or []),
                covers=list(e.get("covers") or []), from_set=s["id"]), s["tree"])

    suite_runs = []
    for r_ in data.get("suite_run") or []:
        where = "suite_run %s" % q(r_.get("id"))
        r_ = dict(_fields(where, r_, "suite_run", errs))
        tree = tree_ref(where, r_.get("tree"))
        if "suite" in r_:
            adapter_name(where, "suite", r_["suite"], adapter.SUITES)
            if r_["suite"] in adapter.SUITES:
                committed_file(where, tree, adapter.SUITES[r_["suite"]].rsplit(":", 1)[0])
        r_.setdefault("whole", True)
        r_.setdefault("rerun_flagged", False)
        suite_runs.append(r_)

    compares = [dict(_fields("compare %s" % q(c.get("id")), c, "compare", errs))
                for c in data.get("compare") or []]
    canfails = [dict(_fields("canfail %s" % q(c.get("id")), c, "canfail", errs))
                for c in data.get("canfail") or []]
    premises = [dict(_fields("premise %s" % q(p.get("id")), p, "premise", errs))
                for p in data.get("premise") or []]
    hands = [dict(_fields("hand %s" % q(h.get("id")), h, "hand", errs))
             for h in data.get("hand") or []]
    baseline = dict(data.get("baseline") or {})

    # ids: unique across the list, safe in paths
    items = ([("build", b.get("id")) for b in builds] + [("check", c.get("id")) for c in checks]
             + [("set", s.get("id")) for s in sets] + [("suite_run", r.get("id")) for r in suite_runs]
             + [("compare", c.get("id")) for c in compares] + [("canfail", c.get("id")) for c in canfails]
             + [("premise", p.get("id")) for p in premises] + [("hand", h.get("id")) for h in hands])
    seen = {}
    for kind, i in items:
        if not isinstance(i, str):
            continue
        if RULES["unique_ids"]:
            if not all(ID_RE.match(part) for part in i.split("@")):
                errs.append("%s %s: an id is made of [A-Za-z0-9_.-] (and @ between a "
                            "check and its tree)" % (kind, q(i)))
            if i in seen:
                errs.append("%s %s: the id is used twice (also a %s)" % (kind, q(i), seen[i]))
        seen.setdefault(i, kind)
    check_ids = {c.get("id") for c in checks}
    ids = set(seen)

    def ref(where, what, i, among):
        if RULES["refs"] and i not in among:
            errs.append("%s: %s %s is not defined in the list" % (where, what, q(i)))

    for kind, lst in (("check", checks), ("suite_run", suite_runs), ("hand", hands)):
        for it in lst:
            for a in it.get("after") or []:
                ref("%s %s" % (kind, q(it.get("id"))), "after", a, ids)
    for s in sets:
        for a in s.get("after") or []:
            ref("set %s" % q(s.get("id")), "after", a, ids)
    for r_ in suite_runs:
        if "images_from" in r_:
            ref("suite_run %s" % q(r_.get("id")), "images_from", r_["images_from"],
                {b.get("id") for b in builds})
    for k, v in baseline.items():
        ref("[baseline]", "item", k, check_ids | {r.get("id") for r in suite_runs})
        if not isinstance(v, str):
            errs.append("[baseline]: %s must name a run or record (a string)" % k)
    for c in compares:
        where = "compare %s" % q(c.get("id"))
        if "comparer" in c:
            adapter_name(where, "comparer", c["comparer"], adapter.COMPARERS)
        if c.get("expect") not in COMPARE_EXPECT:
            errs.append("%s: expect %s is not one of %s" % (where, q(c.get("expect")),
                                                            ", ".join(COMPARE_EXPECT)))
        for side in ("a", "b"):
            v = c.get(side)
            if isinstance(v, str) and v.startswith("baseline:"):
                ref(where, side, v[len("baseline:"):], set(baseline))
            elif v is not None:
                ref(where, side, v, ids)
    by_check = {c.get("id"): c for c in checks}
    for c in canfails:
        where = "canfail %s" % q(c.get("id"))
        ref(where, "target", c.get("target"), check_ids)
        if "control" in c:
            ref(where, "control", c["control"], check_ids)
        if c.get("expect") not in CANFAIL_EXPECT:
            errs.append("%s: expect %s is not one of %s" % (where, q(c.get("expect")),
                                                            ", ".join(CANFAIL_EXPECT)))
        m = c.get("mutants")
        if isinstance(m, dict):
            _fields(where + " mutants", m, "mutants", errs)
            tgt = by_check.get(c.get("target"))
            if "from" in m and tgt is not None:
                committed_file(where, trees.get(tgt["tree"]), m["from"])
    for p in premises:
        where = "premise %s" % q(p.get("id"))
        if p.get("kind") not in PREMISES:
            errs.append("%s: kind %s is not one of %s" % (where, q(p.get("kind")),
                                                          ", ".join(PREMISES)))
        if "tree" in p:
            tree_ref(where, p["tree"])
        if "commit" in p and resolve(repo, p["commit"], tags_ok=False) is None:
            errs.append("%s: commit %s is not an exact commit" % (where, q(p["commit"])))
        for i in p.get("items") or []:
            ref(where, "item", i, ids)

    if errs:
        raise ListError("list %s at %s refused:\n  %s" % (path, commit[:12], "\n  ".join(errs)))
    return AcceptList(commit=commit, path=path, sha256=sha, spec=spec, step=step,
                      trees=trees, builds=builds, checks=checks, sets=sets,
                      suite_runs=suite_runs, baseline=baseline, compares=compares,
                      canfails=canfails, premises=premises, hands=hands)
