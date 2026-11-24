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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IDENTITY_CMD = ('printf "host=%s\\n" "$(uname -n)"; '
                'printf "compiler=%s\\n" "$(gcc --version 2>/dev/null | head -1)"')
# probe_test.c's extra build.sh arguments (2026-11-23): its judge, and the harness's
# ft_deliver_at_count() (../gen_inhibit_shim/deliver_at_count.c, not edited) with the
# three wraps that file names. One string, so run_spec_shim.py, mutate.py and
# product_mutants.py build it the same way.
PROBE_BUILD_ARGS = ("probe_judge.c ../gen_inhibit_shim/deliver_at_count.c "
                    "-Wl,--wrap=twai_port_status,--wrap=can_msgs_to_rx,--wrap=gen_inhibit_get_stats_json")


def _gi_paths():
    d = HERE
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("%s is in no git worktree" % HERE)
        d = os.path.dirname(d)
    bench = os.path.join(d, "gen-inhibit", "tools", "bench")
    if bench not in sys.path:
        sys.path.append(bench)
    import gi_paths
    return gi_paths


def remove(path):
    """gi_paths.remove_tree(path): a folder that can't go is printed with the reason."""
    return _gi_paths().remove_tree(path)


def scratch_dir(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and
    folders"), never the system temp folder: gen-inhibit/tools/bench/gi_paths.scratch()'s,
    removed with its owner file when the run ends, pass or fail (AGENTS.md "Removing
    scratch"). gi_paths is the one in the worktree found by walking up to its .git
    (harness-defects row, 2026-10-24)."""
    return _gi_paths().scratch(name, start=HERE)


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
