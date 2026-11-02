"""The acceptance runner's trees, made from git objects (ACCEPT-SPEC A4).

Each tree the list names gets its own folder from the shared scratch helper
(vtrux_bench/scratch.py), held in the run's Made so it is removed with the
run (A14). In it, where the adapter's tree_paths(name, tree) puts them:

    the project      a clone of the repo (git clone --shared --no-checkout,
                     so no object is copied), checked out detached at the
                     tree's commit
    each component   another such clone, checked out at the commit the list
                     gives for that tree, else the project's pin

A clone reads git objects only: an uncommitted edit in the repo never
reaches a tree. Each checkout is refused (TreeError) unless its HEAD is
exactly its commit and `git status` shows no change, untracked files
included; the runner never edits it. The adapter points the project tree at
its shared checkouts (A15).

Each tree's record: per checkout the commit, the tree id and the sha256 of
every tracked file as checked out; and the product digest over the
adapter's product_files (vtrux_bench.run.engine.product_digest).

A tree on a remote host (A4: one the settings name with its ssh and
workroot) is made locally first, as above, for its record; then each of its
checkouts goes to the host as a git bundle of its commit (git objects only),
is cloned there into <workroot>/accept-<record id>/<prefix><name>/, checked
out detached at the commit and checked there the same way (HEAD, status),
and the folder is held in the run's Made as a remote one (A14). The tree's
paths are then the remote ones; "local_dir" keeps the local clone.

RULES: each is switched off by a mutant in tests/test_accept_trees.py,
which must then fail.
"""

import hashlib
import os
import shutil
import subprocess

from .. import scratch as S
from ..run.engine import product_digest

RULES = {k: True for k in (
    "exact_checkout", "shared_checkout", "from_objects", "head_check", "clean_check",
    "untracked_check", "record_tree_id", "record_files", "record_digest", "layout",
    "made_tracked", "remote_exact", "remote_check", "remote_made", "setup_runs",
    "setup_recorded", "setup_committed", "setup_recheck", "setup_stops")}


class TreeError(Exception):
    pass


def _git(args, cwd=None):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise TreeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def clone_at(repo, commit, dest):
    """A clone of repo at dest, checked out detached at commit, made from
    git objects only. Line endings as the repo's .gitattributes say,
    whatever this machine's core.autocrlf."""
    _git(["clone", "--quiet", "--shared", "--no-checkout", "-c", "core.autocrlf=false",
          os.path.abspath(repo), dest])
    _git(["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", commit],
         cwd=dest)


def check_tree(path, commit):
    """Refuse a checkout whose HEAD is not exactly commit, or that has any
    change, untracked files included."""
    head = _git(["rev-parse", "HEAD"], cwd=path).strip()
    if RULES["head_check"] and head != commit:
        raise TreeError("%s: HEAD is %s, not %s (A4)" % (path, head[:12], commit[:12]))
    st = _git(["status", "--porcelain", "--untracked-files=%s"
               % ("all" if RULES["untracked_check"] else "no")], cwd=path).strip()
    if RULES["clean_check"] and st:
        raise TreeError("%s has changes, so it is not its commit %s (A4):\n%s"
                        % (path, commit[:12], st))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def checkout_record(repo, path, commit):
    """{commit, tree_id, files: {repo-relative path: sha256}}."""
    tree_id = _git(["rev-parse", commit + "^{tree}"], cwd=repo).strip()
    rels = [r for r in _git(["ls-files", "-z"], cwd=path).split("\0") if r]
    if RULES["record_files"]:
        files = {r: _sha256(os.path.join(path, r)) for r in rels}
    else:
        files = {r: "" for r in rels}
    return {"commit": commit, "tree_id": tree_id if RULES["record_tree_id"] else commit,
            "files": files}


def _overlay_working_copy(repo, dest):
    """Only the from_objects mutant: the repo's working copy over a tree."""
    for d, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in files:
            src = os.path.join(d, n)
            dst = os.path.join(dest, os.path.relpath(src, repo))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)


def make_trees(repo, alist, adapter, parent, made, say=print, remote=None):
    """Make every tree of the list under parent. {name: {"dir", "project_dir",
    "shared_dirs" {component: dir}, "record"}}; a tree on a remote host also
    "remote", "host", "local_dir" (see make_tree). remote: {"settings": the
    run's settings (their hosts), "record_id": the acceptance record's id},
    needed for remote trees."""
    return {name: make_tree(repo, name, t, adapter, parent, made, say, remote=remote)
            for name, t in sorted(alist.trees.items())}


def remote_check_tree(ssh_host, rdir, commit):
    """check_tree() on a remote host, over ssh."""
    import shlex
    from ..run import engine as E
    q = shlex.quote(rdir)
    r = subprocess.run(list(E.SSH) + [ssh_host, "git -C %s rev-parse HEAD && git -C %s status "
                                      "--porcelain --untracked-files=all" % (q, q)],
                       capture_output=True, text=True, timeout=600)
    lines = r.stdout.strip().splitlines()
    if r.returncode or not lines:
        raise TreeError("%s:%s cannot be read: %s" % (ssh_host, rdir, r.stderr.strip()[-200:]))
    if RULES["remote_check"] and (lines[0].strip() != commit or lines[1:]):
        raise TreeError("%s:%s is not its commit %s with no change (A4): HEAD %s%s" % (
            ssh_host, rdir, commit[:12], lines[0][:12],
            "".join("\n" + ln for ln in lines[1:20])))


def _ssh(ssh_host, line, data=None):
    from ..run import engine as E
    r = subprocess.run(list(E.SSH) + [ssh_host, line], input=data, capture_output=True,
                       timeout=1800)
    if r.returncode:
        raise TreeError("on %s: %s failed: %s" % (ssh_host, line[:120],
                                                  r.stderr.decode("utf-8", "replace")[-300:]))


def _to_remote(name, t, local, paths, remote, made, say, prefix):
    """The tree on its host: each checkout sent as a git bundle of its
    commit, cloned there into <workroot>/accept-<record id>/<prefix><name>/,
    checked out detached at the commit and checked there (A4)."""
    import shlex
    from ..run import engine as E
    host = t["host"]
    cfg = ((remote or {}).get("settings") or {}).get("hosts", {}).get(host)
    if not cfg or not cfg.get("ssh") or not cfg.get("workroot"):
        raise TreeError("tree %s is on host %s, which the settings do not name with its "
                        "ssh and workroot" % (name, host))
    base = "%s/accept-%s/%s%s" % (cfg["workroot"].rstrip("/"), remote["record_id"], prefix, name)
    if RULES["remote_made"]:
        made.add_remote(E.SSH, cfg["ssh"], base, cfg["workroot"].rstrip("/"))
    checkouts = [("project", local["project_dir"], t["project"])] + [
        (c, local["shared_dirs"][c], v) for c, v in sorted(t["shared"].items())]
    out = {}
    for key, ldir, commit in checkouts:
        rdir = "%s/%s" % (base, paths[key])
        bundle = os.path.join(local["dir"], "%s.bundle" % key)
        _git(["bundle", "create", "-q", bundle, "HEAD"], cwd=ldir)
        with open(bundle, "rb") as f:
            data = f.read()
        q, qb = shlex.quote(rdir), shlex.quote(rdir + ".bundle")
        _ssh(cfg["ssh"], "mkdir -p %s && cat > %s" % (shlex.quote(base), qb), data)
        line = "git -c core.autocrlf=false clone -q --no-checkout %s %s" % (qb, q)
        if RULES["remote_exact"]:
            line += (" && git -C %s -c advice.detachedHead=false checkout -q --detach %s"
                     % (q, commit))
        _ssh(cfg["ssh"], line + " && rm -f %s" % qb)
        remote_check_tree(cfg["ssh"], rdir, commit)
        out[key] = rdir
    say("tree %s: on %s at %s" % (name, host, base))
    rec = dict(local["record"], remote={"host": host, "dir": base})
    return {"dir": local["dir"], "local_dir": local["project_dir"], "remote": True,
            "local_shared_dirs": local["shared_dirs"],
            "host": host, "remote_base": base, "project_dir": out["project"],
            "shared_dirs": {c: out[c] for c in t["shared"]}, "record": rec}


def make_tree(repo, name, t, adapter, parent, made, say=print, prefix="tree-", remote=None):
    """One tree, t as the list gives it (listfile: "project", "shared",
    "host"), in a folder <prefix><name>-... of its own under parent; on a
    remote host, also there (_to_remote), its paths the remote ones. Then
    the adapter's tree_setup in it (setup_tree)."""
    local = _make_local(repo, name, t, adapter, parent, made, say, prefix)
    if t.get("host", "local") == "local":
        tree = local
    else:
        paths = adapter.tree_paths(name, t) if RULES["layout"] else dict(
            {"project": "p"}, **{c: "s-" + c for c in t["shared"]})
        tree = _to_remote(name, t, local, paths, remote, made, say, prefix)
    setup = setup_tree(name, tree, adapter, remote, say)
    if setup is not None:
        tree["record"]["setup"] = setup
    return tree


def _committed_in(tree, rel):
    """rel is a committed file of the tree: in its local clone of the
    component for shared/<component>/..., else of the project."""
    parts = rel.split("/")
    if not rel or rel.startswith("/") or ".." in parts or len(parts) < 2:
        return False
    clone = tree.get("local_dir", tree["project_dir"])
    shared = tree.get("local_shared_dirs", tree["shared_dirs"])
    if parts[0] == "shared" and len(parts) > 2 and parts[1] in shared:
        clone = shared[parts[1]]
    r = subprocess.run(["git", "-C", clone, "cat-file", "-t", "HEAD:" + rel],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "blob"


def setup_tree(name, tree, adapter, ctx, say=print):
    """The adapter's tree_setup(tree): commands that make a fresh tree
    usable (the interposer: install its pinned canre and can-tools into the
    tree's own scratch/). Each must be a committed file in the tree (A3); it
    runs in the tree, on its host, with the tree's env; a failure is a
    TreeError, stopping the run before anything is judged; the tree is
    checked again after, so setup changed nothing git tracks (A4). None if
    the adapter has no setup; else {"commands": [{argv, rc, console}],
    "installed": the adapter's tree_installed(tree, commands)} for the
    tree's record (the charge-interposer reviewer, 2026-11-02)."""
    fn = getattr(adapter, "tree_setup", None)
    if fn is None or not RULES["setup_runs"]:
        return None
    import shlex
    from ..run import engine as E
    from ..run import procs
    from .compile import machine_env, resolve_path, shell_argv, tree_env
    settings = (ctx or {}).get("settings")
    if settings is None:
        raise TreeError("tree %s: its setup needs the run's settings" % name)
    results = []
    for argv in fn(tree):
        rel = argv[0] if argv else ""
        if RULES["setup_committed"] and not _committed_in(tree, rel):
            raise TreeError("tree %s: setup command %s is not a committed file in the "
                            "tree (A3: the adapter names committed files only)" % (name, rel))
        shell = adapter.default_shell(rel)
        cmd = shell_argv(adapter, shell, settings, tree) + [resolve_path(tree, rel)] + list(argv[1:])
        env = tree_env(adapter, tree)
        if tree.get("remote"):
            ssh_host = settings["hosts"][tree["host"]]["ssh"]
            line = procs.remote_command(cmd, tree["project_dir"], env, 1800, False)
            r = subprocess.run(list(E.SSH) + [ssh_host, line], capture_output=True, text=True,
                               timeout=1900, errors="replace")
        else:
            r = subprocess.run(cmd, cwd=tree["project_dir"], env=dict(machine_env(adapter), **env),
                               capture_output=True, text=True, timeout=1800, errors="replace")
        out = (r.stdout + r.stderr)[-4000:]
        results.append({"argv": list(argv), "rc": r.returncode, "console": out})
        say("tree %s: setup %s: rc %d" % (name, " ".join(shlex.quote(a) for a in argv),
                                          r.returncode))
        if r.returncode != 0 and RULES["setup_stops"]:
            raise TreeError("tree %s: setup %s exited %d: %s" % (
                name, " ".join(argv), r.returncode, out.strip()[-300:]))
    if RULES["setup_recheck"]:
        rec = tree["record"]
        checkouts = [("project", rec["project"]["commit"])] + [
            (c, v["commit"]) for c, v in sorted(rec["shared"].items())]
        try:
            for key, commit in checkouts:
                d = tree["project_dir"] if key == "project" else tree["shared_dirs"][key]
                if tree.get("remote"):
                    remote_check_tree(settings["hosts"][tree["host"]]["ssh"], d, commit)
                else:
                    check_tree(d, commit)
        except TreeError as e:
            raise TreeError("tree %s: after its setup: %s" % (name, e))
    installed = adapter.tree_installed(tree, results) \
        if getattr(adapter, "tree_installed", None) else {}
    return {"commands": results if RULES["setup_recorded"] else [], "installed": installed}


def _make_local(repo, name, t, adapter, parent, made, say, prefix):
    d = S.make(parent, prefix + name)
    if RULES["made_tracked"]:
        made.add(d)
    if RULES["layout"]:
        paths = adapter.tree_paths(name, t)
    else:
        paths = dict({"project": "p"}, **{c: "s-" + c for c in t["shared"]})
    pd = os.path.join(d, paths["project"])
    clone_at(repo, t["project"] if RULES["exact_checkout"] else "HEAD", pd)
    check_tree(pd, t["project"])
    if not RULES["from_objects"]:
        _overlay_working_copy(repo, pd)
    rec = {"project": checkout_record(repo, pd, t["project"]), "shared": {}}
    shared_dirs = {}
    for comp, c in sorted(t["shared"].items()):
        sd = os.path.join(d, paths[comp])
        clone_at(repo, c if RULES["shared_checkout"] else t["project"], sd)
        check_tree(sd, c)
        rec["shared"][comp] = checkout_record(repo, sd, c)
        shared_dirs[comp] = sd
    files = adapter.product_files(pd) if RULES["record_digest"] else list(rec["project"]["files"])
    rec["product_digest"] = product_digest(pd, files)
    rec["product_files"] = len(set(files))
    say("tree %s: project %s%s, product digest %s" % (
        name, t["project"][:12], "".join(", %s %s" % (c, v[:12]) for c, v in
                                         sorted(t["shared"].items())),
        rec["product_digest"][:12]))
    return {"dir": d, "project_dir": pd, "shared_dirs": shared_dirs, "record": rec}
