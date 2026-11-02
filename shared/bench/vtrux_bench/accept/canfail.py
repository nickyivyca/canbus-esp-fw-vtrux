"""The acceptance runner's can-fails (ACCEPT-SPEC A10): a check the record
rests on, shown able to fail by committed mutant text, as AGENTS.md asks
(user, 2026-11-01), made true by construction.

A [[canfail]] names a target check and its mutants, one of:
  from = <a committed TOML file in the target's tree>, ids = [...]
      each [[mutant]] in it: id, file (repo-relative, in that tree), old,
      new. The runner makes a fresh clone of the target's tree for each
      mutant (trees.make_tree, "mutant-..."), never touching the target's
      own, and replaces old by new there. It refuses (CanfailError) unless
      old matches exactly once, and an id the file does not hold -- all
      before anything runs.
  mode = <argument>
      the harness's own can-fail mode: the target's command with the
      argument, which must pass (expect "mode-passes").
Each mutant run is one group of the compiled suite (mutant_group), after the
target, "cf-<can-fail>-<mutant>". What must change (expect):
  fails          every mutant leaves the target not PASS
  verdict-flips  the control (the target unmutated, if none is named)
                 PASSes and every mutant FAILs
  new-fail-line  every mutant's log has a line with "FAIL" that the
                 control's log does not (tree paths masked)
  mode-passes    the mode's run passes

RULES: each is switched off by a mutant in tests/test_accept_canfail.py,
which must then fail.
"""

import os
import tomllib

from ..run.model import Check, Group
from .compile import group_name, local_path, resolve_path, shell_argv, tree_env
from .judge import item
from .trees import make_tree

RULES = {k: True for k in ("fresh_tree", "exact_once", "known_ids", "fails_all",
                           "flip_control", "new_line", "mode_arg", "tree_env",
                           "remote_refused")}


class CanfailError(Exception):
    pass


def prepare(repo, alist, trees, adapter, parent, made, say=print):
    """The mutant entries of every [[canfail]], each with its own tree,
    mutated; CanfailError before anything runs if one cannot be."""
    by_check = {c["id"]: c for c in alist.checks}
    out = []
    for cf in alist.canfails:
        tgt = by_check[cf["target"]]
        t = trees[tgt["tree"]]
        m = cf["mutants"]
        if "mode" in m:
            out.append({"canfail": cf["id"], "mutant": "mode", "mode": m["mode"],
                        "target": tgt, "tree": t, "target_tree": t})
            continue
        if t.get("remote") and RULES["remote_refused"]:
            raise CanfailError("canfail %s: its target's tree is on the remote host %s, and "
                               "mutants from committed text on remote trees are not built "
                               "yet (a harness's own mode is)" % (cf["id"], t["host"]))
        with open(local_path(t, m["from"]), "rb") as f:
            muts = {x["id"]: x for x in tomllib.load(f).get("mutant", [])}
        ids = list(m.get("ids") or sorted(muts))
        unknown = [i for i in ids if i not in muts]
        if unknown and RULES["known_ids"]:
            raise CanfailError("canfail %s: %s holds no mutant %s" % (
                cf["id"], m["from"], ", ".join(unknown)))
        for i in ids:
            mu = muts.get(i)
            if mu is None:
                continue
            if RULES["fresh_tree"]:
                mt = make_tree(repo, "%s-%s" % (cf["id"], i), alist.trees[tgt["tree"]],
                               adapter, parent, made, say, prefix="mutant-")
            else:
                mt = t
            path = resolve_path(mt, mu["file"])
            with open(path, encoding="utf-8", newline="") as f:
                text = f.read()
            n = text.count(mu["old"])
            if n != 1 and RULES["exact_once"]:
                raise CanfailError("canfail %s: mutant %s: its text matches %d times in "
                                   "%s, not exactly once" % (cf["id"], i, n, mu["file"]))
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(mu["old"], mu["new"], 1))
            say("canfail %s: mutant %s applied to %s in its own tree" % (cf["id"], i, mu["file"]))
            out.append({"canfail": cf["id"], "mutant": i, "target": tgt, "tree": mt,
                        "target_tree": t, "file": mu["file"]})
    return out


def mutant_group(e, adapter, settings, base_env):
    """The chain group that runs the target's command on one mutant."""
    tgt = e["target"]
    argv = (shell_argv(adapter, tgt["shell"], settings, e["tree"])
            + [resolve_path(e["tree"], tgt["run"][0])] + list(tgt["run"][1:]))
    if "mode" in e and RULES["mode_arg"]:
        argv.append(e["mode"])
    env = dict(tgt["env"])
    # the mutant's own tree names itself, never the target's
    env.update(tree_env(adapter, e["tree"] if RULES["tree_env"] else e["target_tree"]))
    chk = Check(id="run", argv=argv, cwd=e["tree"]["project_dir"], env=env,
                timeout_s=float(tgt["timeout_s"]), cls=tgt["cls"], base_env=dict(base_env),
                note="canfail %s, mutant %s" % (e["canfail"], e["mutant"]))
    return Group(name=group_name("cf-%s-%s" % (e["canfail"], e["mutant"])), checks=[chk],
                 host=tgt["host"], after=[group_name(tgt["id"])], after_must_pass=False)


def _verdict(chain, group):
    vs = [r["verdict"] for r in chain["results"] if r["group"] == group]
    return vs[0] if vs else "absent"


def _fail_lines(chain, group, roots):
    p = os.path.join(chain["_dir"], group, "run", "check.log")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return set()
    for r in roots:
        text = text.replace(r, "<tree>").replace(r.replace("\\", "/"), "<tree>")
    return {ln.strip() for ln in text.splitlines() if "FAIL" in ln and not ln.startswith("$ ")}


def items(alist, entries, chain):
    """One verdict item per [[canfail]] (judge.item): got "as stated", or
    "not as stated" with each reason in its detail."""
    roots = sorted({e["tree"]["dir"] for e in entries}, key=len, reverse=True)
    out = []
    for cf in alist.canfails:
        es = [e for e in entries if e["canfail"] == cf["id"]]
        if not RULES["fails_all"]:
            es = es[:1]
        ctl = group_name(cf.get("control") or cf["target"])
        problems = []
        for e in es:
            g = group_name("cf-%s-%s" % (e["canfail"], e["mutant"]))
            v = _verdict(chain, g)
            if cf["expect"] == "mode-passes":
                if v != "PASS":
                    problems.append("mode %s: %s" % (e["mode"], v))
            elif cf["expect"] == "fails":
                if v == "PASS":
                    problems.append("mutant %s: STILL PASSES" % e["mutant"])
            elif cf["expect"] == "verdict-flips":
                cv = _verdict(chain, ctl)
                if cv != "PASS" and RULES["flip_control"]:
                    problems.append("control %s is %s, not PASS" % (cf.get("control")
                                                                   or cf["target"], cv))
                if v != "FAIL":
                    problems.append("mutant %s: %s, not FAIL" % (e["mutant"], v))
            else:                                                # new-fail-line
                if v == "PASS":
                    problems.append("mutant %s: STILL PASSES" % e["mutant"])
                elif RULES["new_line"] and not (_fail_lines(chain, g, roots)
                                                - _fail_lines(chain, ctl, roots)):
                    problems.append("mutant %s: %s, but no new FAIL line" % (e["mutant"], v))
        problems = sorted(set(problems), key=problems.index)
        out.append(item("canfail", cf["id"], "as stated",
                        "as stated" if not problems else "not as stated",
                        "; ".join(problems)))
    return out
