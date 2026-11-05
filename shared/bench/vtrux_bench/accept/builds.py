"""The acceptance runner's builds (ACCEPT-SPEC A5).

Each [[build]] of the list runs in a clean tree of its own (trees.make_tree,
a "build-<id>" folder, never a check's tree), through the adapter's target:
TARGETS[name] gives the committed file it runs ("run"), its shell, its
arguments (args(options, out)) and what it made (outputs(out, options):
images, elf, variant, toolchain). Its output folder is beside the tree's
checkouts, so they stay clean.

The builds run as one chain run of "build" checks, so the chain runner's
build throttle applies (AGENTS.md "Builds during wall-clock tests"); the
"No builds" line in bench-holder.md stays the tester's to set by hand. The
chain record is inside the acceptance record (chain/), and holds the masked
settings (A6).

Each build's record, builds/<id>.json in the acceptance record: the build,
its tree's record (commits, tree ids, file sha256s, product digest), its
verdict, and for a build that passed its images with their sha256, the
ELF's sha256, the variant and the toolchain. A failed build has no images.
A build's tree gets the adapter's tree_setup like any other tree, with the
run's settings.

A build a failed premise blocks is not run at all -- no tree, no check -- and
is recorded NOT-RUN with why (A11; the charge-interposer reviewer,
2026-11-01: a build is the expensive part).

RULES: each is switched off by a mutant in tests/test_accept_compile.py,
which must then fail.
"""

import hashlib
import json
import os

from ..run.engine import Engine
from ..run.model import Check, Group, Suite
from .compile import chain_root, resolve_path
from .settings import masked
from .trees import TreeError, make_tree

RULES = {k: True for k in ("own_tree", "options", "image_sha", "record_file",
                           "build_cls", "chain_in_record", "fail_recorded",
                           "masked_record", "remote_refused", "setup_settings")}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _record(rec, br):
    """builds/<id>.json in the acceptance record."""
    if RULES["record_file"]:
        d = os.path.join(rec.path, "builds")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "%s.json" % br["id"]), "x", encoding="utf-8",
                  newline="\n") as f:
            json.dump(br, f, indent=1, sort_keys=True)


def build_suite(repo, alist, adapter, settings, made, parent, base_env, say, trees=None,
                skip=None):
    """(the chain Suite of the builds, {build id: what run_builds needs});
    the builds in skip get neither a tree nor a check."""
    groups, ctx = [], {}
    for b in alist.builds:
        if b["id"] in (skip or {}):
            continue
        host = alist.trees[b["tree"]].get("host", "local")
        if host != "local" and RULES["remote_refused"]:
            raise TreeError("build %s: its tree %s is on the remote host %s, and builds on "
                            "remote trees are not built yet" % (b["id"], b["tree"], host))
        if RULES["own_tree"]:
            # the run's settings, for the adapter's tree_setup, which a
            # build's tree gets like any other (the charge-interposer
            # tester, 2026-11-04: without them every build was refused)
            t = make_tree(repo, b["id"], alist.trees[b["tree"]], adapter, parent, made,
                          say, prefix="build-",
                          remote={"settings": settings} if RULES["setup_settings"] else None)
        else:
            t = trees[b["tree"]]
        target = adapter.TARGETS[b["target"]]
        opts = dict(b.get("options") or {}) if RULES["options"] else {}
        out = os.path.join(t["dir"], "out")
        argv = (list(adapter.shell_argv(target["shell"], settings))
                + [resolve_path(t, target["run"])] + list(target["args"](opts, out)))
        chk = Check(id="build", argv=argv, cwd=t["project_dir"],
                    cls="build" if RULES["build_cls"] else "light",
                    timeout_s=float(target.get("timeout_s", 3 * 3600)),
                    base_env=dict(base_env), note="build %s" % b["id"])
        groups.append(Group(name="build-%s" % b["id"], checks=[chk], note=b["id"]))
        ctx[b["id"]] = {"tree": t, "out": out, "opts": opts, "target": target}
    return Suite(name="accept-builds-%s" % alist.step["id"], role=adapter.ROLE,
                 groups=groups), ctx


def run_builds(repo, alist, adapter, settings, rec, made, parent, base_env, trees=None,
               skip=None):
    """Run every build; {build id: its record}, each also written to
    builds/<id>.json in the acceptance record. skip: {build id: why} for
    builds a failed premise blocks (A11): not run, recorded NOT-RUN with
    why."""
    if not alist.builds:
        return {}
    skip = dict(skip or {})
    suite, ctx = build_suite(repo, alist, adapter, settings, made, parent, base_env,
                             rec.say, trees, skip)
    verdicts, run_id = {}, None
    if suite.groups:
        root = chain_root(rec, settings) if RULES["chain_in_record"] else settings.get("runs_root")
        eng = Engine(suite, settings, runs_root=root, out=rec.say,
                     record_settings=masked(settings, adapter) if RULES["masked_record"] else None)
        r = eng.run()
        run_id = eng.run_id
        for res in r["results"]:
            verdicts.setdefault(res["group"], []).append(res["verdict"])
    out = {}
    for b in alist.builds:
        if b["id"] in skip:
            br = {"id": b["id"], "tree": b["tree"], "target": b["target"],
                  "options": dict(b.get("options") or {}), "verdict": "NOT-RUN",
                  "skipped": skip[b["id"]]}
            _record(rec, br)
            rec.say("build %s: NOT-RUN, %s" % (b["id"], skip[b["id"]]))
            out[b["id"]] = br
            continue
        c = ctx[b["id"]]
        vs = verdicts.get("build-%s" % b["id"], ["NOT-RUN"])
        verdict = "PASS" if vs and all(v == "PASS" for v in vs) else \
            next(v for v in vs if v != "PASS")
        br = {"id": b["id"], "tree": b["tree"], "target": b["target"],
              "options": c["opts"], "tree_dir": c["tree"]["dir"],
              "tree_record": c["tree"]["record"], "verdict": verdict,
              "chain_run": run_id}
        if verdict == "PASS" or not RULES["fail_recorded"]:
            o = c["target"]["outputs"](c["out"], c["opts"])
            br["images"] = {n: {"path": p, "sha256": _sha256(p) if RULES["image_sha"] else ""}
                            for n, p in sorted(o["images"].items())}
            br["elf"] = {"path": o["elf"], "sha256": _sha256(o["elf"])}
            br["variant"] = o.get("variant")
            br["toolchain"] = o.get("toolchain")
        _record(rec, br)
        rec.say("build %s: %s%s" % (b["id"], verdict, "".join(
            ", %s %s" % (n, i["sha256"][:12]) for n, i in sorted((br.get("images") or {}).items()))))
        out[b["id"]] = br
    return out
