"""One step's acceptance, end to end (ACCEPT-SPEC A1), and its command line.

    python -m vtrux_bench.accept run --repo <git repo> --list <commit>:<path>
        --adapter <repo-relative path of the project's adapter>
        --settings <the machine's settings file> [--earlier <record id>]
        [--from-hold PID]

--from-hold PID (shared/bench SPEC section 6; user, 2026-11-16): a session
that won the bench with bench_lock wait holds it by hand under PID. The
acceptance takes the lock through the chain runner (A1), in each suite run's
nested run, so PID is passed to every suite run (compile.py), and a nested
run that needs the bench starts from the hand take (nested.py) instead of
a take the session's hold would refuse. So that the bench is never free
between two suite runs that need it (the gen-inhibit reviewer, 2026-11-16),
every suite run that needs the bench but the last is also given
--keep-lock PID: it ends by handing the lock back to PID, and the next one
starts from that hold; the last releases the bench. Which suite runs need
it is asked of each before the chain (compile.needs_bench: its nested
command with --needs-bench), skipping those that will not start (no
images, a premise that did not hold); one that cannot be told counts as
needing it, so the runs before it keep the hold. A suite run that then does
not take over the hold, or does not hand it back, leaves the bench held
under PID at the end, never free, and the later suite runs that need the
bench are NOT-RUN, naming it (hold.py; the charge-interposer reviewer,
2026-11-17); the console says which runs were given --keep-lock.

Without --from-hold, with the settings' bench_lock_log (ACCEPT-SPEC A18;
user, 2026-11-17), the hold is the runner's own: the same chain of
--from-hold and --keep-lock, under the acceptance runner's own PID, and
the hold is taken at the first suite run that needs the bench, by waiting
in the bench log's queue as bench_lock wait does, with no timeout; nothing
before it holds the lock. If the run ends while that hold is still its
own, it is released before the close. hold.py has the rules; the record's
bench_hold.json names the request, the take, every hand-over and release.

In order:
  1. the list is read from git (A2) for its step id, and the record folder
     is made in the machine settings' runs_root (A13), before anything else;
     every console line from here is in it
  2. the adapter is loaded from a clean clone at the list's commit, so it is
     committed harness run from a commit the record names (A15)
  3. the list is checked (A2, A3, A8); the trees are made (A4); each
     [[reuse]], an earlier record's run of a required suite, is checked
     (A17, reuse.py) and refuses the run if it does not hold
  4. the premises (A11): the items of one that fails, builds included, are
     not run, and are NOT EXERCISED
  5. the builds (A5), then the run's settings (A6)
  6. the can-fail mutants (A10), refused before anything runs if one cannot
     be applied
  7. the checks, suite runs and mutant runs as one chain run (A7)
  8. every item judged (A10, A12; a suite run from its nested record, with
     its declared skips, A12a), the cleanup and the close (A13, A14)
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
from . import hold as HO
from . import judge as J
from . import listfile as L
from . import record as R
from . import reuse as RU
from . import settings as ST
from . import trees as T

BENCH = C.BENCH

# Each is switched off by a mutant in tests/test_accept_run.py, which must
# then fail.
RULES = {k: True for k in ("record_first", "adapter_from_commit", "premise_skip",
                           "premise_skip_builds", "cleanup_finally", "refuse_dirty",
                           "dev_never_pass", "identity_untracked", "check_verdict",
                           "not_passed_listed", "nested_not_passed", "nested_wrong_shape",
                           "images_not_started", "from_hold_passed", "keep_lock_chain",
                           "keep_not_last", "keep_bench_only", "hold_queued",
                           "from_hold_no_queue", "hold_recorded", "suite_from_nested",
                           "skips_unreadable", "nested_malformed")}

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


def _read_nested(chain, suite_runs):
    """({suite run id: its nested.json}, {suite run id: why it cannot be
    read}) from the chain's folder. A malformed nested.json (not JSON, or
    not an object) is one that cannot be read, named with its path, not a
    crash that stops the run (the gen-inhibit tester's read of A12a,
    2026-11-20); a missing one is in neither."""
    nested, bad = {}, {}
    for r_ in suite_runs:
        p = os.path.join(chain["_dir"], C.group_name(r_["id"]), "run", "nested.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                got = json.load(f)
            if RULES["nested_malformed"] and not isinstance(got, dict):
                raise ValueError("not a JSON object")
            nested[r_["id"]] = got
        except (OSError, ValueError) as e:
            if not RULES["nested_malformed"]:
                raise
            bad[r_["id"]] = "%s: %r" % (p, e)
    return nested, bad


def _suite_item(chain, r_, n, unreadable=None):
    """A suite run's item, judged from its nested record (ACCEPT-SPEC A12a,
    judge.suite_run_verdict): the first, whole run's results and its
    unmatched_known_fails, its list's declared skips and the catalogue's
    (nested.json's catalogue_skips). With no nested record, or one that
    cannot be read, the outer chain's verdict for its group, as before
    A12a, unless the list declares skips: then a nested.json or a first
    run's record.json that cannot be read (unreadable: why nested.json
    could not be) makes the item FAIL "cannot be read", naming the file
    (the gen-inhibit tester's read, 2026-11-20). The item lists each
    declared skip with its reason ("declared_skips")."""
    group = C.group_name(r_["id"])
    declared = r_.get("declared_skips") or []
    strict = bool(declared) and RULES["skips_unreadable"]
    v = d = None
    if RULES["suite_from_nested"]:
        if not n and unreadable and strict:
            v, d = "FAIL", "cannot be read: %s" % unreadable
        elif n:
            path = None
            try:
                path = os.path.join(n["first"]["dir"], "record.json")
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                if not isinstance(rec.get("results"), list):
                    raise TypeError("its results are not a list")
                v, d, _p = J.suite_run_verdict(
                    rec["results"], dict(n.get("catalogue_skips") or {}), declared,
                    list(rec.get("unmatched_known_fails") or []))
            except (OSError, KeyError, ValueError, TypeError, AttributeError) as e:
                v = None
                if strict:
                    v, d = "FAIL", "cannot be read: %s: %r" % (
                        path or "the first run's record.json named in nested.json", e)
    if v is None:
        v, d = _group_verdict(chain, group)
    it = J.item("suite_run", r_["id"], "PASS", v,
                "; ".join(x for x in (d, "the first, whole run decides") if x))
    it["declared_skips"] = [(s["id"], s["reason"]) for s in declared]
    return it


def _suite_not_passed(chain, group, n, unreadable=None):
    """A suite run's every result that is not a plain PASS: the outer
    chain's own for its group (the nested run's process), then, as
    nested/<id>, every one in the nested record of its first, whole run,
    where the suite's known fails sit, as KNOWN-FAIL (the charge-interposer
    reviewer, 2026-11-02). With no nested record, or one that cannot be
    read, a line says so: the list is then not the whole of it. A nested.json
    or record.json of the wrong shape (a null where an object or a list
    belongs) is one that cannot be read, not a crash that stops the run
    (gen-inhibit's note, through the charge-interposer reviewer,
    2026-11-02)."""
    out = J.not_passed(chain["results"], group)
    if not RULES["nested_not_passed"]:
        return out
    if not n and unreadable:
        return out + [("nested record", "UNREADABLE: %s" % unreadable)]
    if not n:
        return out + [("nested record", "MISSING: the suite run left no nested.json")]
    unreadable = (OSError, KeyError, ValueError) + (
        (TypeError,) if RULES["nested_wrong_shape"] else ())
    try:
        with open(os.path.join(n["first"]["dir"], "record.json"), encoding="utf-8") as f:
            nested = J.not_passed(json.load(f)["results"])
    except unreadable as e:
        return out + [("nested record", "UNREADABLE: %r" % (e,))]
    return out + [("nested/" + rid, v) for rid, v in nested]


def _side(name, alist, chain, runs_root, nested, reused=()):
    for u in reused:
        if u["id"] == name:                 # A17: the earlier run, where it is
            with open(os.path.join(u["dir"], "record.json"), encoding="utf-8") as f:
                return {"kind": "run", "dir": u["dir"], "record": json.load(f)}
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


def _keep_lock(alist, trees, adapter, settings, base_env, paths, not_started, pid, say,
               found=None):
    """{suite run id: pid} for every suite run that needs the bench but the
    last, in the list's order (the order the chain runs them in), so the
    hold passes from one to the next; suite runs that will not start are
    left out. One whose need cannot be told counts as needing the bench.
    found, a dict, gets "bench" (the suite runs that need it, in order:
    the bench parts, A18) and "role" (the first one's suite role told)."""
    bench, roles = [], []
    for r in alist.suite_runs:
        if r["id"] in not_started:
            continue
        if RULES["keep_bench_only"]:
            nb, why, role = C.probe(r, trees[r["tree"]], adapter, settings, base_env, paths)
            if nb and role:
                roles.append(role)
        else:
            nb, why = True, "not asked"
        if nb is None:
            say("suite run %s: could not tell whether it needs the bench (%s): counted "
                "as needing it, so the suite runs before it keep the hold" % (r["id"], why))
        else:
            say("suite run %s: %s the bench (%s)" % (r["id"], "needs" if nb
                                                      else "does not need", why))
        if nb is not False:
            bench.append(r["id"])
    given = bench[:-1] if RULES["keep_not_last"] else bench
    for i in bench:
        say("suite run %s: --from-hold %d%s" % (i, pid, " --keep-lock %d (the hold passes "
                                                "to the next)" % pid if i in given else
                                                " (the last that needs the bench: it "
                                                "releases)"))
    if found is not None:
        found.update(bench=bench, role=roles[0] if roles else None)
    return {i: pid for i in given}


def run(repo, spec, adapter_rel, settings_path, earlier=None, out=None, dev=False,
        from_hold=None):
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
    hold = None
    try:
        parent = made.add(S.make(os.path.join(S.scratch_root(repo), "vtrux-accept"), rec.id))
        harness = os.path.join(parent, "harness")
        T.clone_at(repo, commit, harness)
        T.check_tree(harness, commit)
        adapter = load_adapter(os.path.join(harness if RULES["adapter_from_commit"] else repo,
                                            *adapter_rel.split("/")))
        rec.say("adapter: %s at %s" % (adapter_rel, commit[:12]))
        alist = L.load(repo, spec, adapter)
        RU.validate(alist, adapter)
        trees = T.make_trees(repo, alist, adapter, parent, made, say=rec.say,
                             remote={"settings": machine, "record_id": rec.id})
        # A17: each earlier run a [[reuse]] names, checked before any
        # premise, build, lock or run; a refusal stops the whole acceptance
        reused = [RU.check(repo, u, trees, adapter, machine["runs_root"],
                           lambda rp, sp: L.read_list(rp, sp)[2], settings=machine)
                  for u in alist.reuses]
        for u in reused:
            rec.say("reuse %s: suite %s from record %s, run %s, read in place (A17): "
                    "product digest %s in both; harness digest %s in both, over %d files%s; "
                    "its comparison held: %s" % (
                        u["id"], u["suite"], u["record"], u["run"], u["product"]["this"],
                        u["harness"]["this"], u["harness"]["files"],
                        "".join(" and %s %r" % kv for kv in sorted(
                            u["harness"]["identity"].items())),
                        ", ".join(u["compares"])))
        prem =J.premises(alist.premises, trees, repo, adapter, parent, made)
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
        if reused:
            # the suite runs that require a reused suite read its run where
            # it is (the chain runner's settings.requires_run), in their own
            # settings, written below from these
            s["requires_run"] = RU.requires_run(reused, rec.path)
        entries =CF.prepare(repo, alist, trees, adapter, parent, made, rec.say)
        # a suite run flashes the images of the builds it names (A9); one
        # whose builds made none is not started, and the rest runs
        per_run, no_images = {}, {}
        for r_ in alist.suite_runs:
            images, why = ST.suite_images(r_, builds, adapter)
            if why:
                no_images[r_["id"]] = why
                rec.say("suite run %s: %s" % (r_["id"], why))
            per_run[r_["id"]] = ST.write_tree_settings(s, adapter, r_["id"], trees[r_["tree"]],
                                                       rec, made, parent, images=images)
        # A18: the hold is the session's (--from-hold) or, under the bench
        # log, the runner's own, taken at the first bench part (hold.py)
        lock_log = s.get("bench_lock_log")
        own = bool(lock_log) and RULES["hold_queued"] and (
            from_hold is None or not RULES["from_hold_no_queue"])
        hold_pid = os.getpid() if own else from_hold
        paths = {"settings": spath, "masked": os.path.join(rec.path, "settings.masked.json"),
                 "record_id": rec.id, "suite_runs": per_run,
                 "from_hold": hold_pid if RULES["from_hold_passed"] else None}
        found = {}
        if paths["from_hold"] is not None and RULES["keep_lock_chain"]:
            paths["keep_lock"] = _keep_lock(alist, trees, adapter, s, base_env, paths,
                                            set(no_images) | blocked, hold_pid, rec.say,
                                            found)
        if own and from_hold is None and not found.get("bench"):
            paths["from_hold"], paths["keep_lock"] = None, {}    # no bench part: no hold
        if lock_log and found.get("bench") and paths["from_hold"] is not None:
            hold = HO.Hold(lock_log, found.get("role") or adapter.ROLE, found["bench"],
                           from_hold=None if own else from_hold, say=rec.say,
                           record_id=rec.id)
            hold.install()
        suite = C.compile_suite(alist, trees, adapter, s, base_env, paths=paths,
                                mutants=entries)
        for g in suite.groups if RULES["images_not_started"] else []:
            if g.note in no_images:
                for c in g.checks:
                    c.skip = no_images[g.note]
        for g in suite.groups if RULES["premise_skip"] else []:
            if g.note in blocked or any(e["canfail"] in blocked and g.name.startswith(
                    C.group_name("cf-%s-" % e["canfail"])) for e in entries):
                for c in g.checks:
                    c.skip = "a premise did not hold (A11)"
        chain = C.run_suite(suite, s, rec, adapter=adapter, made=made, hold=hold)
        held = None
        if hold is not None:
            held = hold.finish()            # A18: a leftover own hold, before the close
            if RULES["hold_recorded"]:
                with open(os.path.join(rec.path, "bench_hold.json"), "x", encoding="utf-8",
                          newline="\n") as f:
                    json.dump(held, f, indent=1, sort_keys=True)
                    f.write("\n")
        nested, nested_bad = _read_nested(chain, alist.suite_runs)
        items = [J.item("build", b["id"], "PASS", br["verdict"])
                 for b in alist.builds for br in [builds[b["id"]]]]
        items += [J.item("check", c["id"], c["expect"], *_group_verdict(
            chain, C.group_name(c["id"]))) for c in alist.checks]
        items += [_suite_item(chain, r_, nested.get(r_["id"]), nested_bad.get(r_["id"]))
                  for r_ in alist.suite_runs]
        for u in reused:
            i = J.item("reuse", u["id"], "accepted", "accepted",
                       "record %s, run %s (A17)" % (u["record"], u["run"]))
            i["not_passed"] = [("earlier/" + rid, v) for rid, v in J.not_passed(u["results"])]
            items.append(i)
        # every result of each that is not a plain PASS, for the record's
        # full list (judge.not_passed; the item's detail stops at six)
        for i in items:
            if i["kind"] in ("check", "suite_run") and RULES["not_passed_listed"]:
                i["not_passed"] = J.not_passed(chain["results"], C.group_name(i["id"]))
            if i["kind"] == "suite_run" and RULES["not_passed_listed"]:
                i["not_passed"] = _suite_not_passed(chain, C.group_name(i["id"]),
                                                    nested.get(i["id"]),
                                                    nested_bad.get(i["id"]))
        compares = []
        for c in alist.compares:
            try:
                res = J.run_compare(c, _side(c["a"], alist, chain, machine["runs_root"], nested,
                                             reused),
                                    _side(c["b"], alist, chain, machine["runs_root"], nested,
                                          reused),
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
                   "verdict": v, "earlier": earlier,
                   "hold": held if RULES["hold_recorded"] else None,
                   "reused": [{k: u[k] for k in u if k != "results"} for u in reused]}
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
        if hold is not None:
            try:
                hold.finish()               # A18: on any way out, before the cleanup
            finally:
                hold.uninstall()
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
    p.add_argument("--from-hold", type=int, metavar="PID",
                   help="the role holds the bench log's lock by hand under PID (won "
                   "with bench_lock wait): each suite run that needs the bench "
                   "starts from that hold (the chain runner's --from-hold)")
    a = ap.parse_args(argv)
    try:
        rc, _info = run(a.repo, a.list, a.adapter, a.settings, a.earlier, dev=a.dev,
                        from_hold=a.from_hold)
    except (AcceptError, L.ListError, R.RecordError, T.TreeError, CF.CanfailError,
            ST.SettingsError, CL.CloseError, S.ScratchError, RU.ReuseError) as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    return rc
