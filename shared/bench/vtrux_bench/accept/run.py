"""One step's acceptance, end to end (ACCEPT-SPEC A1), and its command line.

    python -m vtrux_bench.accept run --repo <git repo> --list <commit>:<path>
        --adapter <repo-relative path of the project's adapter>
        --settings <the machine's settings file> [--earlier <record id>]

In order:
  1. the list is read from git (A2) for its step id, and the record folder
     is made in the machine settings' runs_root (A13), before anything else;
     every console line from here is in it
  2. the adapter is loaded from a clean clone at the list's commit, so it is
     committed harness run from a commit the record names (A15)
  3. the list is checked (A2, A3, A8); the trees are made (A4)
  4. the premises (A11): the items of one that fails, builds included, are
     not run, and are NOT EXERCISED
  5. the builds (A5), then the run's settings (A6)
  6. the can-fail mutants (A10), refused before anything runs if one cannot
     be applied
  7. the checks, suite runs and mutant runs as one chain run (A7)
  8. every item judged (A10, A12), the cleanup and the close (A13, A14)
Every folder the run makes is held in one Made: removed before the close,
and by the exit handler and the finally on any other way out. A run that
stops before its close prints why and leaves the record open; it is never
closed with a guess.

The runner's own tree must be clean at a commit (A10, AGENTS.md: evidence
is committed harness run from a named commit; gen-inhibit's reviewer,
2026-11-01), ideally a checkout of a tag, ../shared-<tag>: one with
uncommitted changes, or in no git checkout, is refused before any record
is made. --dev runs anyway, for work on the runner itself: its record's
verdict reads "DEV RUN, not evidence", never ACCEPTANCE PASS, and so does
its skeleton.

Exit: 0 ACCEPTANCE PASS, 1 ACCEPTANCE FAIL, 2 refused or stopped, 3 a DEV
RUN (never evidence).
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tomllib

from .. import scratch as S
from . import builds as B
from . import canfail as CF
from . import close as CL
from . import compile as C
from . import judge as J
from . import listfile as L
from . import record as R
from . import settings as ST
from . import trees as T

BENCH = C.BENCH

# Each is switched off by a mutant in tests/test_accept_run.py, which must
# then fail.
RULES = {k: True for k in ("record_first", "adapter_from_commit", "premise_skip",
                           "premise_skip_builds", "cleanup_finally", "refuse_dirty",
                           "dev_never_pass", "identity_untracked", "check_verdict",
                           "not_passed_listed", "nested_not_passed")}

DEV_LINE = "DEV RUN, not evidence"


def runner_identity(path=None):
    """The commit of the runner's own tree (default this shared/bench), and
    whether that folder has changes: a modified or an untracked file. No git
    there gives no commit."""
    path = path or BENCH

    def git(*a):
        r = subprocess.run(["git", "-C", path] + list(a), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    if head is None:
        return {"commit": None, "dirty": None}
    st = git("status", "--porcelain", "--untracked-files=%s"
             % ("all" if RULES["identity_untracked"] else "no"), "--", ".")
    return {"commit": head, "dirty": bool(st)}


def runner_refusal(ident, dev):
    """Why this runner may not give evidence, or None (A10, AGENTS.md: a
    check an entry rests on is committed harness run from a named commit;
    gen-inhibit's reviewer, 2026-11-01). --dev runs anyway, as a DEV RUN."""
    if dev or not RULES["refuse_dirty"]:
        return None
    if ident["commit"] is None:
        return ("the runner is in no git checkout, so no commit names it: run it from a "
                "clean checkout of a tag (../shared-<tag>), or pass --dev for a run that "
                "is not evidence")
    if ident["dirty"]:
        return ("the runner's own tree (%s) has uncommitted changes at %s: evidence is "
                "committed harness run from a named commit (A10, AGENTS.md); run it from "
                "a clean checkout of a tag (../shared-<tag>), or pass --dev for a run "
                "that is not evidence" % (BENCH, ident["commit"][:12]))
    return None


class AcceptError(Exception):
    pass


def load_adapter(path):
    if not os.path.isfile(path):
        raise AcceptError("no adapter %s at the list's commit (A15: the adapter is "
                          "committed beside the project's suite)" % path)
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("_accept_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _group_verdict(chain, group):
    """(verdict, detail) of a check or suite run: all its results count,
    whatever their order (judge.check_verdict, A12)."""
    if RULES["check_verdict"]:
        return J.check_verdict(chain["results"], group)
    vs = [r["verdict"] for r in chain["results"] if r["group"] == group]
    bad = [v for v in vs if v != "PASS"]
    return (bad[0] if bad else (vs[0] if vs else "absent")), ""


def _suite_not_passed(chain, group, n):
    """A suite run's every result that is not a plain PASS: the outer
    chain's own for its group (the nested run's process), then, as
    nested/<id>, every one in the nested record of its first, whole run,
    where the suite's known fails sit, as KNOWN-FAIL (the charge-interposer
    reviewer, 2026-11-02). With no nested record, or one that cannot be
    read, a line says so: the list is then not the whole of it."""
    out = J.not_passed(chain["results"], group)
    if not RULES["nested_not_passed"]:
        return out
    if not n:
        return out + [("nested record", "MISSING: the suite run left no nested.json")]
    try:
        with open(os.path.join(n["first"]["dir"], "record.json"), encoding="utf-8") as f:
            results = json.load(f)["results"]
    except (OSError, KeyError, ValueError) as e:
        return out + [("nested record", "UNREADABLE: %r" % (e,))]
    return out + [("nested/" + rid, v) for rid, v in J.not_passed(results)]


def _side(name, alist, chain, runs_root, nested):
    if name.startswith("baseline:"):
        d = os.path.join(runs_root, alist.baseline[name[len("baseline:"):]])
        with open(os.path.join(d, "record.json"), encoding="utf-8") as f:
            return {"kind": "run", "dir": d, "record": json.load(f)}
    if name in nested:
        d = nested[name]["first"]["dir"]
        with open(os.path.join(d, "record.json"), encoding="utf-8") as f:
            return {"kind": "run", "dir": d, "record": json.load(f)}
    g = C.group_name(name)
    return {"kind": "check", "log": os.path.join(chain["_dir"], g, "run", "check.log"),
            "record": {"results": [r for r in chain["results"] if r["group"] == g]}}


def run(repo, spec, adapter_rel, settings_path, earlier=None, out=None, dev=False):
    """The whole acceptance. (exit code, record or None)."""
    say0 = out or (lambda s: print(s, flush=True))
    ident = runner_identity()
    why = runner_refusal(ident, dev)
    if why:
        raise AcceptError(why)
    with open(settings_path, encoding="utf-8") as f:
        machine = json.load(f)
    commit, path, raw = L.read_list(repo, spec)
    step_id = tomllib.loads(raw.decode("utf-8")).get("step", {}).get("id")
    rec = R.start(machine["runs_root"], step_id, out=say0)
    made = S.Made()
    closed = False
    try:
        parent = made.add(S.make(os.path.join(S.scratch_root(repo), "vtrux-accept"), rec.id))
        harness = os.path.join(parent, "harness")
        T.clone_at(repo, commit, harness)
        T.check_tree(harness, commit)
        adapter = load_adapter(os.path.join(harness if RULES["adapter_from_commit"] else repo,
                                            *adapter_rel.split("/")))
        rec.say("adapter: %s at %s" % (adapter_rel, commit[:12]))
        alist = L.load(repo, spec, adapter)
        trees = T.make_trees(repo, alist, adapter, parent, made, say=rec.say,
                             remote={"settings": machine, "record_id": rec.id})
        prem = J.premises(alist.premises, trees, repo, adapter, parent, made)
        blocked = set()
        all_ids = ([c["id"] for c in alist.checks] + [r["id"] for r in alist.suite_runs]
                   + [b["id"] for b in alist.builds] + [c["id"] for c in alist.compares]
                   + [c["id"] for c in alist.canfails])
        for p in prem:
            rec.say("premise %s: %s -- %s" % (p["id"], "held" if p["held"] else "DID NOT HOLD",
                                             p["why"]))
            if not p["held"]:
                blocked |= set(p["items"] or all_ids)
        base_env = C.machine_env(adapter)
        skip = {b["id"]: "a premise did not hold (A11)" for b in alist.builds
                if b["id"] in blocked} if RULES["premise_skip_builds"] else {}
        builds = B.run_builds(repo, alist, adapter, machine, rec, made, parent, base_env,
                              trees=trees, skip=skip)
        spath, s = ST.write_settings(settings_path, adapter, builds, trees, rec, made, parent)
        entries = CF.prepare(repo, alist, trees, adapter, parent, made, rec.say)
        per_run = {r_["id"]: ST.write_tree_settings(s, adapter, r_["id"], trees[r_["tree"]],
                                                    rec, made, parent)
                   for r_ in alist.suite_runs}
        suite = C.compile_suite(alist, trees, adapter, s, base_env, paths={
            "settings": spath, "masked": os.path.join(rec.path, "settings.masked.json"),
            "record_id": rec.id, "suite_runs": per_run}, mutants=entries)
        for g in suite.groups if RULES["premise_skip"] else []:
            if g.note in blocked or any(e["canfail"] in blocked and g.name.startswith(
                    C.group_name("cf-%s-" % e["canfail"])) for e in entries):
                for c in g.checks:
                    c.skip = "a premise did not hold (A11)"
        chain = C.run_suite(suite, s, rec, adapter=adapter, made=made)
        nested = {}
        for r_ in alist.suite_runs:
            p = os.path.join(chain["_dir"], C.group_name(r_["id"]), "run", "nested.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    nested[r_["id"]] = json.load(f)
        items = [J.item("build", b["id"], "PASS", br["verdict"])
                 for b in alist.builds for br in [builds[b["id"]]]]
        items += [J.item("check", c["id"], c["expect"], *_group_verdict(
            chain, C.group_name(c["id"]))) for c in alist.checks]
        items += [J.item("suite_run", r_["id"], "PASS",
                         _group_verdict(chain, C.group_name(r_["id"]))[0],
                         "the first, whole run decides") for r_ in alist.suite_runs]
        # every result of each that is not a plain PASS, for the record's
        # full list (judge.not_passed; the item's detail stops at six)
        for i in items:
            if i["kind"] in ("check", "suite_run") and RULES["not_passed_listed"]:
                i["not_passed"] = J.not_passed(chain["results"], C.group_name(i["id"]))
            if i["kind"] == "suite_run" and RULES["not_passed_listed"]:
                i["not_passed"] = _suite_not_passed(chain, C.group_name(i["id"]),
                                                    nested.get(i["id"]))
        compares = []
        for c in alist.compares:
            try:
                res = J.run_compare(c, _side(c["a"], alist, chain, machine["runs_root"], nested),
                                    _side(c["b"], alist, chain, machine["runs_root"], nested),
                                    adapter)
            except (OSError, KeyError, ValueError) as e:
                res = {"id": c["id"], "expect": c["expect"], "held": False,
                       "counted": ["could not compare: %r" % (e,)], "all": []}
            compares.append(res)
            items.append(J.item("compare", c["id"], "held", "held" if res["held"] else "differs",
                                "; ".join(res["counted"][:3])))
        items += CF.items(alist, entries, chain)
        v = J.verdict(items, prem)
        if dev and RULES["dev_never_pass"]:
            v["pass"] = False
            v["lines"][0] = "%s (the runner: %s%s; the items alone would give %s)" % (
                DEV_LINE, (ident["commit"] or "no commit")[:12],
                ", with changes" if ident["dirty"] else "",
                "a pass" if v["lines"][0].endswith("PASS") else "a fail")
        summary = {"step": alist.step,
                   "list": {"commit": alist.commit, "path": alist.path, "sha256": alist.sha256},
                   "runner": ident, "dev": dev,
                   "trees": {n: t["record"] for n, t in trees.items()},
                   "builds": builds, "compares": compares, "premises": prem,
                   "reruns": {k: n["flagged"] for k, n in nested.items()},
                   "verdict": v, "earlier": earlier}
        res = CL.close(rec, summary, made, adapter)
        closed = True
        rc = 3 if dev else (0 if v["pass"] else 1)
        return rc, {"record": rec.path, "verdict": v, "close": res}
    except Exception as e:
        if not closed:
            rec.say("STOPPED before the close: %s: %s" % (type(e).__name__, e))
            rec.say("the record %s is left open (A14: never closed with a guess)" % rec.id)
            if not RULES["record_first"]:
                # the mutant: as if the record were made only at the close
                rec.close()
                S.remove(rec.path, quiet=True)
        raise
    finally:
        left = made.remove_all(out=say0) if RULES["cleanup_finally"] else []
        if not closed:
            for p, why in left:
                say0("LEFTOVER (not in the record, which stopped before its close): %s -- %s"
                     % (p, why))
        rec.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux_bench.accept")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--repo", required=True)
    p.add_argument("--list", required=True)
    p.add_argument("--adapter", required=True)
    p.add_argument("--settings", required=True)
    p.add_argument("--earlier")
    p.add_argument("--dev", action="store_true",
                   help="run from a runner tree with changes: the record's verdict "
                   "reads DEV RUN, not evidence, never ACCEPTANCE PASS (exit 3)")
    a = ap.parse_args(argv)
    try:
        rc, _info = run(a.repo, a.list, a.adapter, a.settings, a.earlier, dev=a.dev)
    except (AcceptError, L.ListError, R.RecordError, T.TreeError, CF.CanfailError,
            ST.SettingsError, CL.CloseError, S.ScratchError) as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    return rc
