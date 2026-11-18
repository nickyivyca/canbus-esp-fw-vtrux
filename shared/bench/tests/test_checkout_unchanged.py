"""shared/bench's tests leave the checkout they run from as they found it, as
shared/tools R23 judges a pinned checkout (user, 2026-11-17, R23 at
shared/tools/pin-check-git-spec 7c9170cb): every tracked file of
shared/bench has the same bytes and the same mode as git records it
(regular, executable or symlink; not the other permission bits) after every
other test file in tests/ runs, none is missing, and no other file is there
unless the committed .gitignore lists it. A machine's own excludes (a global
core.excludesFile, .git/info/exclude, a git template) do not count. No
hardware.

    python -B shared/bench/tests/test_checkout_unchanged.py            every test
    python -B shared/bench/tests/test_checkout_unchanged.py --can-fail  the control
        first (every test unchanged, which must pass), then each rule of this
        file's switched off in turn; every one must make its test fail
    python -B shared/bench/tests/test_checkout_unchanged.py test_x.py...  the
        whole-folder test over those test files only

Why. First (the charge-interposer reviewer, 2026-11-16, on the
charge-interposer tester's finding): test_run_core's bench-lock checks ran
`python -c "from vtrux_bench import bench_lock"` with the settings' python,
which had no -B, so vtrux_bench/__pycache__/ was written into the checkout
the tests ran from, and a project's pin, a digest over every file on disk,
then refused it. Every Python a test starts has run with -B since. From
R23's git rule (2026-11-17) a pin is checked against git instead: shared/main's
.gitignore lists __pycache__/ and *.pyc, so bytecode is no longer a change,
and this file judges by the same rule (the charge-interposer reviewer,
2026-11-17). The tests keep their -B, which costs nothing.

The whole run takes about 21 minutes on madhouse-debian (2026-11-17;
test_scratch_helper's --can-fail alone about 5).

How. The tracked files of shared/bench (`git ls-files -s`, as they are on
disk, each executable or not as git records it) are copied into this run's
own folder under the worktree's scratch/ (tests/_scratch.py), with the
repository's committed root .gitignore (`git show HEAD:.gitignore`) at the
copy's root, and the copy made a git repository of its own with no template
(`git init --template=`), so that only the committed ignore rules apply:
the copy's own tracked .gitignore files and that root one. Each test file
runs there twice, plain and with --can-fail (projects run both), each with
-B as its docstring says, with PYTHONDONTWRITEBYTECODE and
PYTHONPYCACHEPREFIX taken out of its environment, so that whatever it starts
without -B writes where it would from a pinned checkout. After each file the
copy is judged: a tracked file changed (its bytes), its mode changed, or
missing, or a file there that `git check-ignore --no-index` with
core.excludesFile set to the null device does not ignore, fails the test,
naming each and the test file after which it appeared, and the copy is made
again before the next file. A file the committed ignore rules list is shown
as ignored, not counted. A test file's own exit code is printed, not judged:
its own run judges it. A checkout with no tracked files under shared/bench,
or where git cannot be run, is refused.

The plant tests check the checker: test files planted in the copy that
(a) start `python -c "import vtrux_bench.decode"` without -B, writing
vtrux_bench/__pycache__/: not a change when the ignore rules list it, shown
as ignored; (b) the same with -B: nothing; (c) write a file no rule ignores:
caught, named; (d) change a tracked file's bytes, or flip its executable bit:
caught; make it read-only (chmod a-w): not a change; (e) the stray file of
(c) listed only in a machine's global excludes file: still caught. Under
--can-fail PYTHONDONTWRITEBYTECODE=1 is set in this process for the control
and every mutant, so the environment rule has something to take out.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402

PY = sys.executable
ME = os.path.basename(os.path.abspath(__file__))
SCRATCH = _scratch.run_dir("checkout_unchanged")
N = [0]
TEST_TIMEOUT_S = 3600
NO_BYTECODE_ENV = ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")
# shared/main's .gitignore lines for bytecode (R23, 2026-11-17), for the
# plant tests, which must not depend on the checkout's own root .gitignore
BYTECODE_IGNORE = "__pycache__/\n*.pyc\n"

# What the checker relies on; --can-fail switches each off in turn.
RULES = {
    "env_cleared": True,       # the bytecode variables are taken out of each test's environment
    "untracked_counted": True,  # a file no committed rule ignores is a change
    "ignored_not_counted": True,  # a file the committed rules ignore is not
    "bytes_compared": True,    # a tracked file's bytes
    "git_mode": True,          # its mode as git records it, executable or not ...
    "git_mode_only": True,     # ... and not the other permission bits
    "no_machine_excludes": True,  # core.excludesFile set to the null device
    "fresh_copy": True,        # the copy is made again after a file that changed it
}


class Refused(Exception):
    pass


def git(*args, cwd=BENCH, inp=None):
    r = subprocess.run(["git", "-C", cwd] + list(args), input=inp, capture_output=True)
    return r


def tracked():
    """shared/bench's tracked files, relative to it, with "/": {rel: executable}."""
    r = git("ls-files", "-s", "-z", ".")
    if r.returncode != 0:
        raise Refused("git ls-files failed in %s: %s" % (BENCH, r.stderr.decode(errors="replace")))
    out = {}
    for entry in r.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        out[path] = meta.split()[0] == "100755"
    if not out:
        raise Refused("no tracked files under %s" % BENCH)
    return out


def root_gitignore():
    """The repository's committed root .gitignore at HEAD, or "" if none."""
    r = git("show", "HEAD:.gitignore")
    return r.stdout.decode("utf-8") if r.returncode == 0 else ""


def make_copy(dst, mutate=None, plant=None, gitignore=None):
    """The tracked files into dst/shared/bench (executable as git records
    them), the committed root .gitignore (or gitignore) at dst, dst made a
    git repository with no template; a mutant's edit (rel, old, new) applied,
    which must find old exactly once; a planted test file (name, text)
    written. Returns the copy's shared/bench."""
    bench = os.path.join(dst, "shared", "bench")
    if os.path.exists(dst):
        _scratch.remove_tree(dst)
    for rel, exe in tracked().items():
        out = os.path.join(bench, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(BENCH, *rel.split("/")), out)
        if exe:
            os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(os.path.join(dst, ".gitignore"), "w", encoding="utf-8", newline="") as f:
        f.write(root_gitignore() if gitignore is None else gitignore)
    r = git("init", "-q", "--template=", cwd=dst)
    if r.returncode != 0:
        raise Refused("git init failed in %s: %s" % (dst, r.stderr.decode(errors="replace")))
    if mutate:
        rel, old, new = mutate
        p = os.path.join(bench, *rel.split("/"))
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()
        if text.count(old) != 1:
            raise Refused("mutant edit found %d times in %s: %r" % (text.count(old), rel, old))
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new))
    if plant:
        name, text = plant
        with open(os.path.join(bench, "tests", name), "w", encoding="utf-8") as f:
            f.write(text)
    return bench


def state(bench):
    """{"files": {rel: (sha256, executable, full mode)} of every file under
    bench}."""
    out = {}
    for root, _dirs, files in os.walk(bench):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, bench).replace(os.sep, "/")
            with open(p, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
            m = os.lstat(p).st_mode
            out[rel] = (h, bool(m & stat.S_IXUSR), stat.S_IMODE(m))
    return out


def ignored(bench, rels):
    """The rels (under bench) the copy's committed ignore rules list."""
    if not rels:
        return set()
    dst = os.path.dirname(os.path.dirname(bench))
    args = ["check-ignore", "--no-index", "-z", "--stdin"]
    if RULES["no_machine_excludes"]:
        args = ["-c", "core.excludesFile=" + os.devnull] + args
    r = git(*args, cwd=dst, inp="".join("shared/bench/%s\0" % p for p in rels).encode("utf-8"))
    if r.returncode not in (0, 1):
        raise Refused("git check-ignore failed in %s: %s" % (dst, r.stderr.decode(errors="replace")))
    return {p[len("shared/bench/"):] for p in r.stdout.decode("utf-8").split("\0") if p}


def judge(bench, before, after, listed):
    """(changes, ignored): the changes to the copy, as R23 counts them, and
    the files there that the committed rules ignore."""
    out = []
    for p in sorted(listed):
        if p not in before:
            continue                # gone already, and named then
        if p not in after:
            out.append("removed %s" % p)
            continue
        b, a = before[p], after[p]
        if RULES["bytes_compared"] and a[0] != b[0]:
            out.append("changed %s" % p)
        if RULES["git_mode"] and a[1] != b[1]:
            out.append("mode %s (%s)" % (p, "now executable" if a[1] else "no longer executable"))
        if not RULES["git_mode_only"] and a[2] != b[2]:
            out.append("permissions %s (%o -> %o)" % (p, b[2], a[2]))
    # added since the copy as this test file found it (with fresh_copy off,
    # a file an earlier one added is not named again)
    extra = sorted(set(after) - set(before) - set(listed))
    ign = ignored(bench, sorted(set(after) - set(listed)))
    if not RULES["ignored_not_counted"]:
        ign = set()
    if RULES["untracked_counted"]:
        out += ["added %s" % p for p in extra if p not in ign]
    return out, sorted(ign)


def child_env():
    env = dict(os.environ)
    if RULES["env_cleared"]:
        for k in NO_BYTECODE_ENV:
            env.pop(k, None)
    return env


def run_tests(names, mutate=None, plant=None, modes=((),), gitignore=None, after_hook=None):
    """Each named test file run in a fresh copy, once per mode (its extra
    arguments): [(name and mode, exit code, [changes], [ignored files])].
    after_hook(bench), if given, runs after each test file, before the
    judging (the plant tests' chmods)."""
    N[0] += 1
    dst = os.path.join(SCRATCH, "c%d" % N[0])
    bench = make_copy(dst, mutate, plant, gitignore)
    s0 = state(bench)
    listed = set(s0)
    s = s0
    out = []
    try:
        for n, args in [(n, a) for n in names for a in modes]:
            label = " ".join([n] + list(args))
            r = subprocess.run([PY, "-B", os.path.join("tests", n)] + list(args), cwd=bench,
                               env=child_env(), capture_output=True, text=True,
                               timeout=TEST_TIMEOUT_S)
            if after_hook:
                after_hook(bench)
            s1 = state(bench)
            ch, ign = judge(bench, s, s1, listed)
            out.append((label, r.returncode, ch, ign))
            if ch and RULES["fresh_copy"]:
                bench = make_copy(dst, mutate, plant, gitignore)
                s = state(bench)
            else:
                s = s1
    finally:
        _scratch.remove_tree(dst)
    return out


def all_tests():
    return sorted(n for n in os.listdir(HERE)
                  if n.startswith("test_") and n.endswith(".py") and n != ME)


def report(results):
    bad = []
    for n, rc, ch, ign in results:
        print("      %-44s exit %-3s %s%s" % (
            n, rc, "; ".join(ch) if ch else "unchanged",
            " (ignored: %s)" % ", ".join(ign) if ign else ""), flush=True)
        if ch:
            bad.append("%s: %s" % (n, "; ".join(ch)))
    return bad


BOTH_MODES = ((), ("--can-fail",))


def test_tests_leave_checkout(names=None, mutate=None, modes=BOTH_MODES):
    """Every other test file in tests/, run from a fresh copy plain and with
    --can-fail (projects run both), leaves the copy unchanged as R23 counts
    it."""
    bad = report(run_tests(names or all_tests(), mutate=mutate, modes=modes))
    if bad:
        raise AssertionError("the checkout changed: " + " | ".join(bad))


PLANT = '''import os, subprocess, sys
bench = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
subprocess.run([sys.executable%s, "-c", "import vtrux_bench.decode"], cwd=bench, check=True)
'''
STRAY = '''import os
bench = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
open(os.path.join(bench, "vtrux_bench", "stray.txt"), "w").close()
'''
NOOP = "pass\n"


def test_plant_ignored():
    """A test file that starts Python without -B writes
    vtrux_bench/__pycache__/: the committed rules list it (__pycache__/,
    *.pyc), so it is shown as ignored and is not a change."""
    (n, rc, ch, ign), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", PLANT % ""),
                                  gitignore=BYTECODE_IGNORE)
    assert rc == 0, "the plant failed (exit %s)" % rc
    assert not ch, "ignored bytecode counted as a change: %r" % ch
    assert any(p.startswith("vtrux_bench/__pycache__/") for p in ign), \
        "no bytecode written, or not shown as ignored: %r" % ign


def test_plant_clean():
    """The same plant with -B leaves the copy as it was: nothing changed,
    nothing ignored."""
    (n, rc, ch, ign), = run_tests(["test_zz_plant.py"],
                                  plant=("test_zz_plant.py", PLANT % ', "-B"'),
                                  gitignore=BYTECODE_IGNORE)
    assert rc == 0 and not ch and not ign, "exit %s, %r, %r" % (rc, ch, ign)


def test_plant_caught():
    """A test file that writes a file no committed rule ignores is caught,
    naming it."""
    (n, rc, ch, ign), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", STRAY),
                                  gitignore=BYTECODE_IGNORE)
    assert rc == 0, "the plant failed (exit %s)" % rc
    assert ch == ["added vtrux_bench/stray.txt"], ch


def test_plant_tracked():
    """A tracked file whose bytes change, or whose executable bit flips, is
    caught; one made read-only (chmod a-w) is not a change, since git
    records no other permission bits."""
    def hook(edit):
        def h(bench):
            p = os.path.join(bench, "vtrux_bench", "decode.py")
            if edit == "bytes":
                with open(p, "a", encoding="utf-8") as f:
                    f.write("# changed\n")
            elif edit == "exec":
                os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            elif edit == "read-only":
                os.chmod(p, os.stat(p).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        return h
    got = {}
    for edit in ("bytes", "exec", "read-only"):
        (n, rc, ch, ign), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", NOOP),
                                      gitignore=BYTECODE_IGNORE, after_hook=hook(edit))
        got[edit] = ch
    assert got["bytes"] == ["changed vtrux_bench/decode.py"], got
    assert got["exec"] == ["mode vtrux_bench/decode.py (now executable)"], got
    assert got["read-only"] == [], got


def test_machine_excludes():
    """A machine's own excludes do not count: with a global git config whose
    core.excludesFile lists the stray file, the stray file is still caught."""
    N[0] += 1
    d = os.path.join(SCRATCH, "g%d" % N[0])
    os.makedirs(d)
    ex = os.path.join(d, "machine-excludes")
    with open(ex, "w", encoding="utf-8") as f:
        f.write("stray.txt\n")
    cfg = os.path.join(d, "gitconfig")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[core]\n\texcludesFile = %s\n" % ex.replace("\\", "/"))
    old = os.environ.get("GIT_CONFIG_GLOBAL")
    os.environ["GIT_CONFIG_GLOBAL"] = cfg
    try:
        (n, rc, ch, ign), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", STRAY),
                                      gitignore=BYTECODE_IGNORE)
    finally:
        if old is None:
            os.environ.pop("GIT_CONFIG_GLOBAL", None)
        else:
            os.environ["GIT_CONFIG_GLOBAL"] = old
    assert ch == ["added vtrux_bench/stray.txt"], (ch, ign)


def test_plants_fresh():
    """Two files that write the stray file in one run: each is named, since
    the copy is made again after the first (with fresh_copy off, the second
    finds it already there and adds nothing)."""
    res = run_tests(["test_zz_plant.py", "test_zz_plant.py"],
                    plant=("test_zz_plant.py", STRAY), gitignore=BYTECODE_IGNORE)
    assert [bool(ch) for _, _, ch, _ in res] == [True, True], res


TESTS = [test_plant_ignored, test_plant_clean, test_plant_caught, test_plant_tracked,
         test_machine_excludes, test_plants_fresh, test_tests_leave_checkout]


MUTANTS = [
    ("env_cleared", test_plant_ignored),
    ("untracked_counted", test_plant_caught),
    ("ignored_not_counted", test_plant_ignored),
    ("bytes_compared", test_plant_tracked),
    ("git_mode", test_plant_tracked),
    ("git_mode_only", test_plant_tracked),
    ("no_machine_excludes", test_machine_excludes),
    ("fresh_copy", test_plants_fresh),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    try:
        tracked()
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    if names:
        o = outcome_of(lambda: test_tests_leave_checkout(names))
        print("%s  test_tests_leave_checkout %s" % ("ok  " if o is None else "FAIL", o or ""))
        return 1 if o else 0
    if can_fail:
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    bad = []
    for t in TESTS:
        s = time.monotonic()
        o = outcome_of(t)
        print("%s  %-26s %.0fs %s" % ("ok  " if o is None else "FAIL", t.__name__,
                                     time.monotonic() - s, o or ""), flush=True)
        if o:
            bad.append(t.__name__)
    if not can_fail:
        print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
        return 1 if bad else 0
    if bad:
        print("\nREFUSED: the control fails (%s); no mutant is counted" % ", ".join(bad))
        return 1
    missing = sorted(set(RULES) - {k for k, _t in MUTANTS})
    if missing:
        print("FAIL  rules with no mutant: %s" % ", ".join(missing))
        return 1
    caught = 0
    for name, t in MUTANTS:
        RULES[name] = False
        try:
            o = outcome_of(t)
        finally:
            RULES[name] = True
        caught += o is not None
        print("%s  %-22s off: %-26s %s" % ("ok  " if o else "FAIL", name, t.__name__,
                                           ("fails (%s)" % o[:120]) if o else
                                           "STILL PASSES -- nothing checks it"), flush=True)
    print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
