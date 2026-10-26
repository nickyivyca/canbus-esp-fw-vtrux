"""Where run_spec_shim.py and mutate.py build, and what they record about it.

The build runs either in WSL (from Windows) or in the local bash (--local, on a
Linux host with gcc). Every run records the host and the compiler its build saw,
asked of that same shell: `uname -n` and the first line of `gcc --version`. Only a
run built on madhouse-debian with its gcc 14.2 counts for VERIFICATION.md; a WSL
build on NICKY-XPS is a self-check (user, 2026-10-24, through the reviewer).
Gen-inhibit tester, 2026-10-24.
"""
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
IDENTITY_CMD = ('printf "host=%s\\n" "$(uname -n)"; '
                'printf "compiler=%s\\n" "$(gcc --version 2>/dev/null | head -1)"')


def scratch_dir(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and
    folders"), never the system temp folder: gen-inhibit/tools/bench/gi_paths.scratch()'s
    rule, written out here. The worktree is found by walking up to its .git
    (harness-defects row, 2026-10-24)."""
    d = HERE
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("%s is in no git worktree" % HERE)
        d = os.path.dirname(d)
    d = os.path.join(d, "scratch", "gen-inhibit-tests", name, "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d


def wsl_path(p):
    p = os.path.abspath(p).replace("\\", "/")
    return "/mnt/" + p[0].lower() + p[2:] if re.match(r"^[A-Za-z]:/", p) else p


def build_path(p, local):
    """p as the build shell sees it."""
    return os.path.abspath(p) if local else wsl_path(p)


def sh(cmd, local, timeout=900):
    """Run cmd in the build shell: (exit code, stdout, stderr)."""
    argv = ["bash", "-c", cmd] if local else ["wsl", "-e", "bash", "-c", cmd]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def host_identity(local, run=sh):
    """The build shell's host and compiler: {"host", "compiler", "build_env"}; a value
    the shell did not give is "" (see missing())."""
    _rc, out, _err = run(IDENTITY_CMD, local)
    got = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {"host": got.get("host", "").strip(), "compiler": got.get("compiler", "").strip(),
            "build_env": "local" if local else "wsl"}


def missing(ident):
    """The identity fields that are empty: a run with any is not a record."""
    return [k for k in ("host", "compiler") if not ident.get(k)]


def describe(ident):
    """One line for a run's summary or log."""
    how = ("built in WSL: a self-check only, not for VERIFICATION.md" if ident.get("build_env") == "wsl"
           else "built in the local shell")
    return "host %s; %s; %s" % (ident.get("host") or "NOT RECORDED", ident.get("compiler") or "compiler NOT RECORDED",
                                how)
