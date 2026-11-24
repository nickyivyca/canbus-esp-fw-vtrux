#!/usr/bin/env python3
"""Can-fail of probe_test.c against the product: the implementor's spec 7 trip 7
mutants of the shipped code, each run through the tester's probe cases.

    python3.13 -B product_mutants.py [--only T3,T4] [--keep]     (Linux, madhouse-debian)

The mutants are not written here. They are the T-series entries of the implementor's
../gen_inhibit_shim/mutate.py (its MUT table: name -> (file under the project folder,
[(old, new), ...])), read from that file's text with ast, never imported, so this
file holds no product code and no reading of it. Each T mutant breaks one part of
spec 7 trip 7's decision or spec 11's probe on the same path (that file says which).

For each mutant: a copy, in this run's own folder in the worktree's scratch/, of the
project folder's parts build.sh compiles (firmware/main, test/gen_inhibit_shim's
sources and mock/, test/diag_host, this folder); the edits applied to the copy (an
edit that does not match its count is an error, never a catch); probe_test built
there by build.sh as run_spec_shim.py builds it, with the shared checkout and the
pinned shared/diag of this worktree (WICANDIR, DIAGDIR); every case run. A mutant is
CAUGHT when a case prints a RESULT line that is not PASS. A build failure, a crash
or no RESULT line is not a catch. The unmutated copy is run first and must give only
PASS lines, or the run stops: a control that fails would catch every mutant.

A survivor is reported as it is, with the cases that ran: it means these cases do
not exercise what that mutant breaks, not that the product is wrong. Exit 0 only
when the control passes and every mutant is caught. Gen-inhibit tester, 2026-11-23.
"""
import ast
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_env  # noqa: E402

PROJECT = os.path.normpath(os.path.join(HERE, "..", ".."))
IMPL_MUTATE = os.path.join(PROJECT, "test", "gen_inhibit_shim", "mutate.py")


def t_mutants(path=IMPL_MUTATE, prefix="T"):
    """{name: (file, [(old, new), ...])} for the MUT entries whose name starts with
    prefix, read from the file's text. An entry that is not a plain literal is skipped
    and named in the second return value."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    table = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "MUT" for t in node.targets):
            table = node.value
    if not isinstance(table, ast.Dict):
        raise RuntimeError("no MUT table in %s" % path)
    out, skipped = {}, []
    for k, v in zip(table.keys, table.values):
        name = ast.literal_eval(k)
        if not name.startswith(prefix):
            continue
        try:
            f, edits = ast.literal_eval(v)
        except ValueError:
            skipped.append(name)
            continue
        out[name] = (f, [tuple(e) for e in edits])
    return out, skipped


def make_copy(dst):
    """The project folder's parts build.sh compiles, at the same relative paths."""
    shutil.copytree(os.path.join(PROJECT, "firmware", "main"), os.path.join(dst, "firmware", "main"))
    shutil.copytree(os.path.join(PROJECT, "test", "diag_host"), os.path.join(dst, "test", "diag_host"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(HERE, os.path.join(dst, "test", "gen_inhibit_spec_shim"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shim = os.path.join(PROJECT, "test", "gen_inhibit_shim")
    d = os.path.join(dst, "test", "gen_inhibit_shim")
    shutil.copytree(os.path.join(shim, "mock"), os.path.join(d, "mock"))
    for n in os.listdir(shim):
        if n.endswith((".c", ".h", ".py")):
            shutil.copy2(os.path.join(shim, n), d)


def apply(dst, f, edits):
    """Apply edits to dst/f; returns an error string or None. Each old must occur
    exactly once."""
    p = os.path.join(dst, f)
    src = open(p, encoding="utf-8", newline="").read()
    for old, new in edits:
        if src.count(old) != 1:
            return "edit matched %d times in %s: %r" % (src.count(old), f, old[:60])
        src = src.replace(old, new)
    open(p, "w", encoding="utf-8", newline="").write(src)
    return None


def run_cases(dst, env):
    """Build probe_test in the copy and run every case: (RESULT lines, build log)."""
    spec = os.path.join(dst, "test", "gen_inhibit_spec_shim")
    exe = os.path.join(spec, "probe_test_bin")
    b = subprocess.run("NN_TEST=probe_test.c bash build.sh %s %s" % (exe, host_env.PROBE_BUILD_ARGS),
                       shell=True, cwd=spec, capture_output=True, text=True, env=env)
    if b.returncode or not os.path.exists(exe):
        return [], (b.stdout + b.stderr)[-400:]
    cases = subprocess.run([exe], capture_output=True, text=True, env=env).stdout.split()
    res = []
    for c in cases:
        try:
            r = subprocess.run([exe, c], capture_output=True, text=True, timeout=300, env=env)
            res += [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
        except subprocess.TimeoutExpired:
            pass
    return res, ""


def main(argv):
    if os.name != "posix":
        print("ERROR    Linux only (madhouse-debian); nothing run")
        return 2
    only = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))
    muts, skipped = t_mutants()
    if only:
        muts = {k: v for k, v in muts.items() if k.split("_")[0] in only or k in only}
    ident = host_env.host_identity(True)
    print(host_env.describe(ident))
    if host_env.missing(ident):
        print("ERROR    not recorded: %s" % ", ".join(host_env.missing(ident)))
        return 1
    if skipped:
        print("note     not plain literals, not run: %s" % ", ".join(skipped))
    py = sys.executable
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHON=py)
    for var, cmd in (("WICANDIR", [py, "-B", os.path.join(PROJECT, "test", "gen_inhibit_shim", "wican_ref.py")]),
                     ("DIAGDIR", [py, "-B", os.path.join(PROJECT, "test", "diag_host", "diag_host.py"), "dir"])):
        if not env.get(var):
            env[var] = subprocess.run(cmd, capture_output=True, text=True, env=env).stdout.strip()
        if not env[var]:
            print("ERROR    no %s" % var)
            return 1
    base = host_env.scratch_dir("product-mutants-spec_shim")
    print("work folder: %s" % base)
    rc = 0
    try:
        ctl = os.path.join(base, "control")
        make_copy(ctl)
        res, log = run_cases(ctl, env)
        bad = [l for l in res if l.split()[3] != "PASS"]
        print("%s control: %d RESULT lines, %d not PASS%s" % ("ok      " if res and not bad else "ERROR   ", len(res),
                                                              len(bad), ("; " + log) if log else ""))
        if not res or bad:
            for l in bad[:5]:
                print("         " + l)
            return 1
        caught = errors = 0
        for name in sorted(muts):
            f, edits = muts[name]
            d = os.path.join(base, name)
            make_copy(d)
            err = apply(d, f, edits)
            if err:
                print("ERROR    %s: %s" % (name, err))
                errors += 1
                continue
            res, log = run_cases(d, env)
            hits = [l for l in res if l.split()[3] != "PASS"]
            caught += bool(hits)
            print("%s %s  (%s%s)" % ("CAUGHT  " if hits else "SURVIVED", name,
                                     "%d of %d lines not PASS" % (len(hits), len(res)) if res
                                     else "NO RESULT: build or crash; " + log,
                                     ("; " + hits[0][:150]) if hits else ""))
            sys.stdout.flush()
            if "--keep" not in argv:
                host_env.remove(d)
        print("\n%d of %d caught by a case, %d errors" % (caught, len(muts), errors))
        rc = 0 if caught == len(muts) and not errors else 1
    finally:
        if "--keep" not in argv:
            host_env.remove(base)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
