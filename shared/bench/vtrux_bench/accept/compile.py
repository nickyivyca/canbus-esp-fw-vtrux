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

The environment (A7): a check starts from machine_env(), the variables a
machine needs (the operating system's own, and the adapter's MACHINE_VARS,
the ones AGENTS.md and local-paths.md name), never the operator's shell
(the chain runner's Check.base_env); the list's or the catalogue's env is
laid over it.

The chain run's record goes inside the acceptance record, in chain/.

RULES: each is switched off by a mutant in tests/test_accept_compile.py,
which must then fail.
"""

import os

from ..run.engine import Engine
from ..run.model import Check, Group, Suite, exit_code_judge

RULES = {k: True for k in ("base_env", "list_env", "machine_vars", "after",
                           "shared_path", "cwd_tree", "chain_in_record")}

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


def group_name(item_id):
    """A list id as a chain group name (no "@")."""
    return item_id.replace("@", "__")


def resolve_path(tree, rel):
    """rel (a repo-relative path) in a made tree: in its checkout of a
    shared component for shared/<component>/..., else in its project."""
    parts = rel.split("/")
    root = tree["project_dir"]
    if RULES["shared_path"] and len(parts) > 2 and parts[0] == "shared" \
            and parts[1] in tree["shared_dirs"]:
        root = tree["shared_dirs"][parts[1]]
    return os.path.join(root, *parts)


def compile_suite(alist, trees, adapter, settings, base_env, name=None):
    """One chain Suite holding every check of the list."""
    groups = []
    gnames = {group_name(c["id"]) for c in alist.checks}
    for c in alist.checks:
        tree = trees[c["tree"]]
        argv = (list(adapter.shell_argv(c["shell"], settings))
                + [resolve_path(tree, c["run"][0])] + list(c["run"][1:]))
        judge = adapter.judge(c["judge"]) if c.get("judge") else exit_code_judge
        after = [group_name(a) for a in c["after"] if group_name(a) in gnames]
        chk = Check(id="run", argv=argv,
                    cwd=tree["project_dir"] if RULES["cwd_tree"] else None,
                    env=dict(c["env"]) if RULES["list_env"] else {},
                    timeout_s=float(c["timeout_s"]), cls=c["cls"], judge=judge,
                    base_env=dict(base_env), note=c["id"])
        groups.append(Group(name=group_name(c["id"]), checks=[chk], host=c["host"],
                            after=after if RULES["after"] else [],
                            after_must_pass=False, note=c["id"]))
    return Suite(name=name or "accept-%s" % alist.step["id"], role=adapter.ROLE,
                 groups=groups)


def chain_root(rec, settings):
    return os.path.join(rec.path, "chain") if RULES["chain_in_record"] \
        else settings.get("runs_root")


def run_suite(suite, settings, rec, **kw):
    """Run the suite with the chain runner; its record, with "_dir" its
    folder."""
    eng = Engine(suite, settings, runs_root=chain_root(rec, settings), out=rec.say, **kw)
    r = eng.run()
    r["_dir"] = eng.run_dir
    return r
