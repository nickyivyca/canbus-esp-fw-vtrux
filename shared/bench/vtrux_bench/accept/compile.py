"""The list's checks compiled into one chain suite, and run (ACCEPT-SPEC A7).

What runs, runs as an ordinary chain suite (A1): the bench lock, lanes, the
wall-clock rules, verdicts and known fails are the chain runner's. Each
check of the list (one per tree for a check on several, one per catalogue
entry a set picks) is one group of one chain check, "<group>/run", in the
list's order; its `after` becomes the group's (ordering only: every check
runs and is judged, A12). The command is the adapter's shell for it, then
the check's file in its tree -- a shared/<component>/ path in that tree's
checkout of the component, any other in the project's -- then its
arguments; it runs from the tree's project checkout.

A [[suite_run]] is one more group, after the checks: a chain check running
vtrux_bench.accept.nested (this runner's own shared/bench on its path) on the
project's suite in its tree, a nested run with its own record in the
acceptance record's runs/ (A7; nested.py). Each can-fail mutant run is one
more group, after its target (A10; canfail.py).

The environment (A7): a check starts from machine_env(), the variables a
machine needs (the operating system's own, and the adapter's MACHINE_VARS,
the ones AGENTS.md and local-paths.md name), never the operator's shell
(the chain runner's Check.base_env); the list's or the catalogue's env is
laid over it, and over that the adapter's tree_env(tree), which names the
check's own tree to the code run in it (A15; the interposer's
INTERPOSER_CODE). A suite run and a mutant run get theirs the same way.

A check on a remote tree runs on that host (its group's host is the
tree's), from the remote checkout, with the remote paths and the adapter's
command for its shell there (shell_argv(shell, settings, host=...)); the
chain runner's own work folder on that host is held in the run's Made. A
suite run on a remote tree is refused: not built yet.

The chain run's record goes inside the acceptance record, in chain/, and
holds the masked settings, not the ones the run used (A6; the chain
runner's record_settings).

RULES: each is switched off by a mutant in tests/test_accept_compile.py,
which must then fail.
"""

import os

from ..run.engine import Engine
from ..run.model import Check, Group, Suite, exit_code_judge

RULES = {k: True for k in ("base_env", "list_env", "machine_vars", "after",
                           "shared_path", "cwd_tree", "chain_in_record",
                           "masked_record", "tree_env", "remote_paths", "remote_shell",
                           "chain_work_removed", "remote_suite_refused")}

# what the operating system needs to start a program, nothing of a person's
OS_VARS_NT = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PATH",
              "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
              "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
              "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
              "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "USERNAME",
              "COMPUTERNAME")
OS_VARS_POSIX = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL")


def machine_env(adapter, environ=None):
    """The environment a check starts from: the operating system's
    variables and the adapter's MACHINE_VARS, taken from environ (default
    this process's), and nothing else."""
    environ = os.environ if environ is None else environ
    if not RULES["base_env"]:
        return dict(environ)
    names = list(OS_VARS_NT if os.name == "nt" else OS_VARS_POSIX)
    if RULES["machine_vars"]:
        names += list(getattr(adapter, "MACHINE_VARS", ()))
    if os.name == "nt":
        up = {k.upper(): v for k, v in environ.items()}
        return {k: up[k.upper()] for k in names if k.upper() in up}
    return {k: environ[k] for k in names if k in environ}


def tree_env(adapter, tree):
    """What names the tree to the code run in it: the adapter's
    tree_env(tree), laid over the list's env (A15)."""
    fn = getattr(adapter, "tree_env", None)
    return dict(fn(tree)) if fn is not None and RULES["tree_env"] else {}


def group_name(item_id):
    """A list id as a chain group name (no "@")."""
    return item_id.replace("@", "__")


def resolve_path(tree, rel):
    """rel (a repo-relative path) in a made tree: in its checkout of a
    shared component for shared/<component>/..., else in its project; on a
    remote tree, the remote path (posix)."""
    import posixpath
    parts = rel.split("/")
    root = tree["project_dir"]
    if RULES["shared_path"] and len(parts) > 2 and parts[0] == "shared" \
            and parts[1] in tree["shared_dirs"]:
        root = tree["shared_dirs"][parts[1]]
    if tree.get("remote") and RULES["remote_paths"]:
        return posixpath.join(root, *parts)
    return os.path.join(root, *parts)


def local_path(tree, rel):
    """rel in the tree's local clone, remote tree or not (for reading a
    committed file, a mutant file, say)."""
    t = dict(tree, project_dir=tree.get("local_dir", tree["project_dir"]), remote=False)
    return resolve_path(t, rel)


def shell_argv(adapter, shell, settings, tree):
    """The adapter's command for a shell, on the tree's host."""
    if tree.get("remote") and RULES["remote_shell"]:
        return list(adapter.shell_argv(shell, settings, host=tree["host"]))
    return list(adapter.shell_argv(shell, settings))


BENCH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def suite_run_check(r, tree, adapter, settings, base_env, paths):
    """The chain check that runs a [[suite_run]] as a nested run
    (nested.py), with this runner's own shared/bench on its path."""
    if tree.get("remote") and RULES["remote_suite_refused"]:
        raise ValueError("suite run %s: its tree is on the remote host %s, and suite runs "
                         "on remote trees are not built yet (a suite ships its own work "
                         "to its hosts)" % (r["id"], tree["host"]))
    file, attr = adapter.SUITES[r["suite"]].rsplit(":", 1)
    sp, mp = (paths.get("suite_runs") or {}).get(r["id"]) or (paths["settings"],
                                                              paths["masked"])
    argv = (list(adapter.shell_argv("python", settings))
            + ["-m", "vtrux_bench.accept.nested",
               "--suite", "%s:%s" % (resolve_path(tree, file), attr),
               "--settings", sp, "--record-settings", mp,
               "--run-id", "%s-%s" % (paths["record_id"], r["id"]), "--out", "{outdir}"])
    if r.get("rerun_flagged"):
        argv.append("--rerun-flagged")
    for g in r.get("groups") or []:
        argv += ["--group", g]
    for c in r.get("checks") or []:
        argv += ["--check", c]
    return Check(id="run", argv=argv, cwd=tree["project_dir"],
                 env=dict(tree_env(adapter, tree), PYTHONPATH=BENCH), timeout_s=48 * 3600.0,
                 base_env=dict(base_env), note="suite run %s" % r["id"])


def compile_suite(alist, trees, adapter, settings, base_env, name=None, paths=None,
                  mutants=None):
    """One chain Suite holding every check, suite run and can-fail mutant
    run of the list. paths (needed for suite runs): {"settings": the run's
    settings file, "masked": its masked copy, "record_id": the acceptance
    record's id, "suite_runs": {suite run id: (its own settings, their
    masked copy)} from settings.write_tree_settings}. mutants:
    canfail.prepare()'s entries."""
    groups = []
    gnames = {group_name(c["id"]) for c in alist.checks} | \
        {group_name(r["id"]) for r in alist.suite_runs}
    for c in alist.checks:
        tree = trees[c["tree"]]
        argv = (shell_argv(adapter, c["shell"], settings, tree)
                + [resolve_path(tree, c["run"][0])] + list(c["run"][1:]))
        judge = adapter.judge(c["judge"]) if c.get("judge") else exit_code_judge
        after = [group_name(a) for a in c["after"] if group_name(a) in gnames]
        env = dict(c["env"]) if RULES["list_env"] else {}
        env.update(tree_env(adapter, tree))
        chk = Check(id="run", argv=argv,
                    cwd=tree["project_dir"] if RULES["cwd_tree"] else None,
                    env=env,
                    timeout_s=float(c["timeout_s"]), cls=c["cls"], judge=judge,
                    base_env=dict(base_env), note=c["id"])
        groups.append(Group(name=group_name(c["id"]), checks=[chk], host=c["host"],
                            after=after if RULES["after"] else [],
                            after_must_pass=False, note=c["id"]))
    for r in alist.suite_runs:
        after = [group_name(a) for a in r.get("after") or [] if group_name(a) in gnames]
        groups.append(Group(name=group_name(r["id"]), checks=[suite_run_check(
            r, trees[r["tree"]], adapter, settings, base_env, paths or {})],
            after=after if RULES["after"] else [], after_must_pass=False, note=r["id"]))
    if mutants:
        from .canfail import mutant_group
        groups += [mutant_group(e, adapter, settings, base_env) for e in mutants]
    return Suite(name=name or "accept-%s" % alist.step["id"], role=adapter.ROLE,
                 groups=groups)


def chain_root(rec, settings):
    return os.path.join(rec.path, "chain") if RULES["chain_in_record"] \
        else settings.get("runs_root")


def run_suite(suite, settings, rec, adapter=None, made=None, **kw):
    """Run the suite with the chain runner; its record, with "_dir" its
    folder. The chain record holds the masked settings (A6). The chain
    runner's work folder on each remote host it used goes into made (A14:
    the chain runner itself leaves it)."""
    from .settings import masked
    from ..run import engine as E
    eng = Engine(suite, settings, runs_root=chain_root(rec, settings), out=rec.say,
                 record_settings=masked(settings, adapter) if RULES["masked_record"] else None,
                 **kw)
    try:
        r = eng.run()
    finally:
        if made is not None and RULES["chain_work_removed"] and getattr(eng, "run_id", None):
            for host in sorted({g.host for g in suite.groups if g.host != "local"}):
                cfg = (settings.get("hosts") or {}).get(host) or {}
                if cfg.get("ssh") and cfg.get("workroot"):
                    made.add_remote(E.SSH, cfg["ssh"], "%s/%s" % (
                        cfg["workroot"].rstrip("/"), eng.run_id))
    r["_dir"] = eng.run_dir
    return r
