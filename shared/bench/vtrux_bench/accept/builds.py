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
from .trees import make_tree

RULES = {k: True for k in ("own_tree", "options", "image_sha", "record_file",
                           "build_cls", "chain_in_record", "fail_recorded",
                           "masked_record")}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def build_suite(repo, alist, adapter, settings, made, parent, base_env, say, trees=None):
    """(the chain Suite of the builds, {build id: what run_builds needs})."""
    groups, ctx = [], {}
    for b in alist.builds:
        if RULES["own_tree"]:
            t = make_tree(repo, b["id"], alist.trees[b["tree"]], adapter, parent, made,
                          say, prefix="build-")
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


def run_builds(repo, alist, adapter, settings, rec, made, parent, base_env, trees=None):
    """Run every build; {build id: its record}, each also written to
    builds/<id>.json in the acceptance record."""
    if not alist.builds:
        return {}
    suite, ctx = build_suite(repo, alist, adapter, settings, made, parent, base_env,
                             rec.say, trees)
    root = chain_root(rec, settings) if RULES["chain_in_record"] else settings.get("runs_root")
    eng = Engine(suite, settings, runs_root=root, out=rec.say,
                 record_settings=masked(settings, adapter) if RULES["masked_record"] else None)
    r = eng.run()
    verdicts = {}
    for res in r["results"]:
        verdicts.setdefault(res["group"], []).append(res["verdict"])
    out = {}
    for b in alist.builds:
        c = ctx[b["id"]]
        vs = verdicts.get("build-%s" % b["id"], ["NOT-RUN"])
        verdict = "PASS" if vs and all(v == "PASS" for v in vs) else \
            next(v for v in vs if v != "PASS")
        br = {"id": b["id"], "tree": b["tree"], "target": b["target"],
              "options": c["opts"], "tree_dir": c["tree"]["dir"],
              "tree_record": c["tree"]["record"], "verdict": verdict,
              "chain_run": eng.run_id}
        if verdict == "PASS" or not RULES["fail_recorded"]:
            o = c["target"]["outputs"](c["out"], c["opts"])
            br["images"] = {n: {"path": p, "sha256": _sha256(p) if RULES["image_sha"] else ""}
                            for n, p in sorted(o["images"].items())}
            br["elf"] = {"path": o["elf"], "sha256": _sha256(o["elf"])}
            br["variant"] = o.get("variant")
            br["toolchain"] = o.get("toolchain")
        if RULES["record_file"]:
            d = os.path.join(rec.path, "builds")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "%s.json" % b["id"]), "x", encoding="utf-8",
                      newline="\n") as f:
                json.dump(br, f, indent=1, sort_keys=True)
        rec.say("build %s: %s%s" % (b["id"], verdict, "".join(
            ", %s %s" % (n, i["sha256"][:12]) for n, i in sorted((br.get("images") or {}).items()))))
        out[b["id"]] = br
    return out
