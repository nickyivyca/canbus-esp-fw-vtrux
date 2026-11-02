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
  4. the premises (A11): the items of one that fails are skipped, and NOT
     EXERCISED
  5. the builds (A5), then the run's settings (A6)
  6. the can-fail mutants (A10), refused before anything runs if one cannot
     be applied
  7. the checks, suite runs and mutant runs as one chain run (A7)
  8. every item judged (A10, A12), the cleanup and the close (A13, A14)
Every folder the run makes is held in one Made: removed before the close,
and by the exit handler and the finally on any other way out. A run that
stops before its close prints why and leaves the record open; it is never
closed with a guess.

Exit: 0 ACCEPTANCE PASS, 1 ACCEPTANCE FAIL, 2 refused or stopped.
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
                           "cleanup_finally")}


def runner_identity():
    """The commit of the shared/bench this runner is, and whether its tree
    has changes there."""
    def git(*a):
        r = subprocess.run(["git", "-C", BENCH] + list(a), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--", ".")) if git("rev-parse", "HEAD")
            else None}


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
    vs = [r["verdict"] for r in chain["results"] if r["group"] == group]
    bad = [v for v in vs if v != "PASS"]
    return bad[0] if bad else (vs[0] if vs else "absent")


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


def run(repo, spec, adapter_rel, settings_path, earlier=None, out=None):
    """The whole acceptance. (exit code, record or None)."""
    say0 = out or (lambda s: print(s, flush=True))
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
        trees = T.make_trees(repo, alist, adapter, parent, made, say=rec.say)
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
        builds = B.run_builds(repo, alist, adapter, machine, rec, made, parent, base_env,
                              trees=trees)
        spath, s = ST.write_settings(settings_path, adapter, builds, trees, rec, made, parent)
        entries = CF.prepare(repo, alist, trees, adapter, parent, made, rec.say)
        suite = C.compile_suite(alist, trees, adapter, s, base_env, paths={
            "settings": spath, "masked": os.path.join(rec.path, "settings.masked.json"),
            "record_id": rec.id}, mutants=entries)
        for g in suite.groups if RULES["premise_skip"] else []:
            if g.note in blocked or any(e["canfail"] in blocked and g.name.startswith(
                    C.group_name("cf-%s-" % e["canfail"])) for e in entries):
                for c in g.checks:
                    c.skip = "a premise did not hold (A11)"
        chain = C.run_suite(suite, s, rec, adapter=adapter)
        nested = {}
        for r_ in alist.suite_runs:
            p = os.path.join(chain["_dir"], C.group_name(r_["id"]), "run", "nested.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    nested[r_["id"]] = json.load(f)
        items = [J.item("build", b["id"], "PASS", br["verdict"])
                 for b in alist.builds for br in [builds[b["id"]]]]
        items += [J.item("check", c["id"], c["expect"],
                         _group_verdict(chain, C.group_name(c["id"]))) for c in alist.checks]
        items += [J.item("suite_run", r_["id"], "PASS",
                         _group_verdict(chain, C.group_name(r_["id"])),
                         "the first, whole run decides") for r_ in alist.suite_runs]
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
        summary = {"step": alist.step,
                   "list": {"commit": alist.commit, "path": alist.path, "sha256": alist.sha256},
                   "runner": runner_identity(),
                   "trees": {n: t["record"] for n, t in trees.items()},
                   "builds": builds, "compares": compares, "premises": prem,
                   "reruns": {k: n["flagged"] for k, n in nested.items()},
                   "verdict": v, "earlier": earlier}
        res = CL.close(rec, summary, made, adapter)
        closed = True
        return (0 if v["pass"] else 1), {"record": rec.path, "verdict": v, "close": res}
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
    a = ap.parse_args(argv)
    try:
        rc, _info = run(a.repo, a.list, a.adapter, a.settings, a.earlier)
    except (AcceptError, L.ListError, R.RecordError, T.TreeError, CF.CanfailError,
            ST.SettingsError, CL.CloseError, S.ScratchError) as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    return rc
