#!/usr/bin/env python3
"""Tests for gi_core_pin.py: gen-inhibit's shared/bench from the checkout it pins (port
step 4 item 1, the gen-inhibit reviewer's batch of 2026-11-25). No hardware.

    python3.13 -B gen-inhibit/tools/bench/test_gi_core_pin.py

What it checks:
  - the real pin: firmware/shared_pins.json's "shared/bench" entry passes against
    ../shared-<tag>/, and its digest is the one `entry` computes there;
  - on a made-up project (a git repository in scratch/ with only a pin file) beside a
    shared clone of this repository detached at the pinned tag: the control passes, and
    each planted fault is refused: every malformed pin, a missing checkout (the refusal
    names the commands that create it read-only), HEAD at another commit, a tag the
    repository lacks, a wrong digest, a changed tracked file, a stray file and a staged
    one; bytecode, which the committed .gitignore lists, passes;
  - the checker is shared/tools' code from git at the pin's commit, not the disk's: a
    disk pin.py whose git_digest agrees with any digest does not pass a wrong digest;
  - use_bench(): the pinned folder goes first on sys.path, ahead of a PYTHONPATH copy;
    a vtrux_bench imported earlier from elsewhere is refused;
  - the command line: `module` runs a core module with no PYTHONPATH, and refuses
    anything but vtrux_bench;
  - the suite and the tools take the core from the pin: with no PYTHONPATH,
    gi_serial.serial_dev(), gi_wifi.core() and kvaser_bus's lock read get the pinned
    folder's vtrux_bench, and the suite refuses a vtrux_bench imported from elsewhere.

GI_CORE_PIN_UNDER_TEST names another gi_core_pin.py to test (the mutant runner's copy).
Prints "ok ..." or "FAIL ..." per check, then PASS or FAIL; exit 0 only on PASS.
Gen-inhibit tester, 2026-11-25.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

UNDER = os.path.abspath(os.environ.get("GI_CORE_PIN_UNDER_TEST") or os.path.join(HERE, "gi_core_pin.py"))
_spec = importlib.util.spec_from_file_location("gi_core_pin", UNDER)
CP = importlib.util.module_from_spec(_spec)
sys.modules["gi_core_pin"] = CP
_spec.loader.exec_module(CP)

fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[:300]))


def fresh():
    """Forget passed checks and loaded checkers, so the next check reads everything again."""
    CP._DONE.clear()
    for k in [k for k in sys.modules if k.startswith("gi_core_pin_vtrux_build_")]:
        del sys.modules[k]


def refused(fn, *a):
    """str of the Refused fn raised, or None if it returned."""
    fresh()
    try:
        fn(*a)
    except CP.Refused as e:
        return str(e)
    return None


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), check=True, capture_output=True, text=True, **kw).stdout


def no_pp():
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def py(code, cwd, env):
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=cwd, env=env, capture_output=True, text=True)


def main():
    wt = gi_paths.worktree_of(HERE)
    with open(os.path.join(wt, CP.PIN_REL), encoding="utf-8") as f:
        real = json.load(f)[CP.COMPONENT]
    tag = real["tag"]

    # 1. The real pin
    fresh()
    try:
        c = CP.check()
    except CP.Refused as e:
        c = {"error": str(e)}
    want = os.path.join(os.path.dirname(wt), "shared-" + tag, "shared", "bench")
    check("the real pin passes: shared/bench %s at %s" % (tag, real["commit"][:10]),
          c.get("folder") and os.path.realpath(c["folder"]) == os.path.realpath(want), c)
    fresh()
    e = CP.entry(tag)
    check("entry %s gives the pin's commit and digest" % tag,
          e == {"tag": tag, "commit": real["commit"], "digest": real["digest"]}, e)

    # 2. A made-up project beside a shared clone at the tag
    base = gi_paths.scratch("test_gi_core_pin")
    proj = os.path.join(base, "proj")
    start = os.path.join(proj, "gen-inhibit", "tools", "bench")
    os.makedirs(start)
    git("init", "-q", proj)
    co = os.path.join(base, "shared-" + tag)
    git("clone", "-q", "--shared", "--no-checkout", wt, co)
    git("-C", co, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", tag)
    pinf = os.path.join(proj, CP.PIN_REL)
    os.makedirs(os.path.dirname(pinf))

    def write_pin(entry, raw=None):
        with open(pinf, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps({"shared/tools": {"tag": "x"}, CP.COMPONENT: entry}))

    write_pin(real)
    CP.START = start
    r = refused(CP.check)
    check("made-up project: the control passes", r is None, r)

    # each refused for its own reason, before any checkout is looked for
    bad = [("no entry", None, '{"shared/tools": {}}', "has no 'shared/bench' entry"),
           ("not JSON", None, "{", "not valid JSON"),
           ("no tag", dict(real, tag=""), None, "names no tag"),
           ("a commit id as the tag", dict(real, tag=real["commit"][:12]), None, "which is a commit id"),
           ("a tag with a slash", dict(real, tag="a/b"), None, "cannot be a checkout"),
           ("a short commit", dict(real, commit=real["commit"][:12]), None, "40 lower-case hex"),
           ("an upper-case digest", dict(real, digest=real["digest"].upper()), None, "64 lower-case hex")]
    for what, entry, raw, why in bad:
        write_pin(entry, raw)
        r = refused(CP.check)
        check("pin refused: %s (%s)" % (what, why), r is not None and why in r, r)
    os.remove(pinf)
    r = refused(CP.check)
    check("pin refused: no pin file", r is not None and "no pin file" in r, r)

    other = "2026.11.23.3"
    write_pin(dict(real, tag=other))
    r = refused(CP.check)
    check("a missing checkout is refused, naming the commands that create it read-only",
          r is not None and "worktree add --detach ../shared-%s %s" % (other, other) in r and "chmod -R a-w" in r, r)

    write_pin(real)
    git("-C", co, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", other)
    r = refused(CP.check)
    check("HEAD at another commit is refused", r is not None and "is at" in r, r)
    git("-C", co, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", tag)

    os.symlink(co, os.path.join(base, "shared-nosuchtag"))
    write_pin(dict(real, tag="nosuchtag"))
    r = refused(CP.check)
    check("a tag the repository lacks is refused", r is not None and "no tag of that name" in r, r)
    os.remove(os.path.join(base, "shared-nosuchtag"))

    wrong = "0" * 64
    write_pin(dict(real, digest=wrong))
    r = refused(CP.check)
    check("a wrong digest is refused", r is not None and "has the digest" in r, r)

    write_pin(real)
    init = os.path.join(co, "shared", "bench", "vtrux_bench", "__init__.py")
    with open(init, "ab") as f:
        f.write(b"\n# planted\n")
    r = refused(CP.check)
    check("a changed tracked file is refused", r is not None and "differ on disk" in r, r)
    git("-C", co, "checkout", "--", "shared/bench/vtrux_bench/__init__.py")
    r = refused(CP.check)
    check("restored: passes again", r is None, r)

    stray = os.path.join(co, "shared", "bench", "stray.py")
    open(stray, "w").close()
    r = refused(CP.check)
    check("a stray file is refused", r is not None and "stray.py" in r, r)
    os.remove(stray)

    pyc = os.path.join(co, "shared", "bench", "vtrux_bench", "__pycache__")
    os.makedirs(pyc)
    open(os.path.join(pyc, "x.cpython-313.pyc"), "wb").close()
    r = refused(CP.check)
    check("bytecode, which the committed .gitignore lists, passes", r is None, r)
    shutil.rmtree(pyc)

    staged = os.path.join(co, "shared", "bench", "staged.txt")
    open(staged, "w").close()
    git("-C", co, "add", "-f", "shared/bench/staged.txt")
    r = refused(CP.check)
    check("a staged file is refused", r is not None and "staged.txt" in r, r)
    git("-C", co, "rm", "-q", "--cached", "shared/bench/staged.txt")
    os.remove(staged)
    r = refused(CP.check)
    check("unstaged and removed: passes again", r is None, r)

    # 3. The checker comes from git at the pin's commit, not from the disk
    disk_pin = os.path.join(co, "shared", "tools", "vtrux_build", "pin.py")
    with open(disk_pin, "a", encoding="utf-8") as f:
        f.write("\n\ndef git_digest(*a, **k):\n    return %r\n" % wrong)
    write_pin(dict(real, digest=wrong))
    r = refused(CP.check)
    check("a disk pin.py that agrees with any digest does not pass a wrong one (the checker is git's)",
          r is not None and "has the digest" in r, r)
    git("-C", co, "checkout", "--", "shared/tools/vtrux_build/pin.py")
    CP.START = HERE
    fresh()

    # 4. use_bench(): first on sys.path; a foreign vtrux_bench is refused
    foreign = os.path.join(base, "foreign")
    os.makedirs(os.path.join(foreign, "vtrux_bench"))
    open(os.path.join(foreign, "vtrux_bench", "__init__.py"), "w").close()
    env = dict(no_pp(), PYTHONPATH=foreign)
    cwd = os.path.dirname(UNDER)
    r = py("import gi_core_pin as C; f = C.use_bench(); import vtrux_bench, os; "
           "print(os.path.dirname(os.path.dirname(vtrux_bench.__file__)) == f)", cwd, env)
    check("use_bench puts the pinned folder ahead of a PYTHONPATH copy",
          r.returncode == 0 and r.stdout.strip() == "True", r.stdout + r.stderr)
    r = py("import vtrux_bench, gi_core_pin as C; C.use_bench()", cwd, env)
    check("use_bench refuses a vtrux_bench imported from elsewhere",
          r.returncode != 0 and "already imported from" in r.stderr, r.stdout + r.stderr[-300:])

    # 5. The command line
    r = subprocess.run([sys.executable, "-B", UNDER, "module", "vtrux_bench.bench_lock", "--help"],
                       env=no_pp(), capture_output=True, text=True)
    check("module vtrux_bench.bench_lock --help runs with no PYTHONPATH",
          r.returncode == 0 and "usage:" in r.stdout and "status" in r.stdout, r.stdout[-200:] + r.stderr[-200:])
    r = subprocess.run([sys.executable, "-B", UNDER, "module", "json.tool"], env=no_pp(),
                       capture_output=True, text=True, input="{}")
    check("module refuses a module that is not vtrux_bench's",
          r.returncode == 2 and "only vtrux_bench modules" in r.stderr, r.stdout + r.stderr)
    r = subprocess.run([sys.executable, "-B", UNDER, "check"], env=no_pp(), capture_output=True, text=True)
    check("check prints the pin, exit 0", r.returncode == 0 and real["digest"] in r.stdout, r.stdout + r.stderr)

    # 6. The suite and the tools take the core from the pin
    tools_env = no_pp()
    probe = ("import os, types\n"
             "pinned = os.path.realpath(%r)\n"
             "def under(m): return os.path.realpath(m.__file__).startswith(pinned + os.sep)\n"
             "import gi_serial, gi_wifi, kvaser_bus\n"
             "print('serial', under(gi_serial.serial_dev()))\n"
             "print('wifi', all(under(m) for m in gi_wifi.core()))\n"
             "kvaser_bus._gi_paths = lambda: types.SimpleNamespace(bench_log=lambda: %r)\n"
             "try:\n    kvaser_bus._lock_holder()\nexcept kvaser_bus.BenchNotHeld as e:\n"
             "    print('kvaser', 'bench log could not be read' in str(e))\n"
             % (want, os.path.join(base, "no-such-log.jsonl")))
    r = py(probe, HERE, tools_env)
    got = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l)
    for tool in ("serial", "wifi", "kvaser"):
        check("with no PYTHONPATH, %s takes the pinned core" % tool, got.get(tool) == "True",
              r.stdout + r.stderr[-300:])
    suite = os.path.join(os.path.dirname(HERE), "suite_gen_inhibit.py")
    r = py("import vtrux_bench, importlib.util\n"
           "s = importlib.util.spec_from_file_location('suite_gen_inhibit', %r)\n"
           "m = importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n" % suite,
           HERE, dict(no_pp(), PYTHONPATH=foreign))
    check("the suite refuses a vtrux_bench imported from elsewhere",
          r.returncode != 0 and "already imported from" in r.stderr, r.stdout + r.stderr[-300:])

    gi_paths.remove_tree(base)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
